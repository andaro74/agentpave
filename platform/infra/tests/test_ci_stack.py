"""IAM assertions for the CI identity.

The role a workflow assumes is the one credential in this platform that a
stranger can reach — anyone who can open a pull request can cause it to be
assumed. So what it cannot do matters more than what it can, and both are
asserted here rather than trusted to a review of the stack file.
"""

from typing import Any

import aws_cdk as cdk
import pytest
from agentpave_infra.stacks.ci_stack import GITHUB_AUDIENCE, GITHUB_ISSUER, CiStack
from aws_cdk.assertions import Match, Template

REPOSITORY = "andaro74/agentpave"
OWNER_ID = 3157440
REPOSITORY_ID = 1327317546
# The subject GitHub actually issues, read out of CloudTrail after the first
# CI run was denied against a policy naming the documented plain form.
QUALIFIED = f"andaro74@{OWNER_ID}/agentpave@{REPOSITORY_ID}"


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    return Template.from_stack(
        CiStack(
            app,
            "AgentPave-Ci-test",
            repository=REPOSITORY,
            owner_id=OWNER_ID,
            repository_id=REPOSITORY_ID,
        )
    )


def _statements(template: Template) -> list[dict[str, Any]]:
    return [
        statement
        for policy in template.find_resources("AWS::IAM::Policy").values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
    ]


def _actions(statement: dict[str, Any]) -> list[str]:
    action = statement["Action"]
    return [action] if isinstance(action, str) else list(action)


# ── what it cannot do ─────────────────────────────────────────────────────


def test_the_ci_role_cannot_record_a_baseline(template: Template) -> None:
    """It can read the bar and be blocked by it; it cannot move it.

    `is_recordable` already refuses to bank a failing run inside the process.
    This is the same property in IAM, where a bug in the process cannot reach
    it — a CI run has no way to write itself in as the new standard, so setting
    the bar stays a deliberate act by a person running `make seed-baseline`.
    """
    for statement in _statements(template):
        for action in _actions(statement):
            assert not action.startswith("dynamodb:Put"), f"CI role holds {action}"
            assert not action.startswith("dynamodb:Update"), f"CI role holds {action}"
            assert not action.startswith("dynamodb:Delete"), f"CI role holds {action}"


def test_the_ci_role_holds_no_bedrock_permission(template: Template) -> None:
    """Invariant 1 applies to CI too. The gate reaches models the same way
    every service does — through the gateway, metered and guarded."""
    assert "bedrock:" not in str(template.to_json()).lower()


def test_the_ci_role_cannot_deploy(template: Template) -> None:
    """It reads stack outputs to find the deployment; it does not change it.

    A gate that could deploy is a gate that could make itself pass.
    """
    for statement in _statements(template):
        for action in _actions(statement):
            assert action != "cloudformation:CreateStack"
            assert action != "cloudformation:UpdateStack"
            assert not action.startswith("iam:")


# ── who may assume it ─────────────────────────────────────────────────────


def _subjects(template: Template) -> list[str]:
    roles = template.find_resources("AWS::IAM::Role")
    policy = next(iter(roles.values()))["Properties"]["AssumeRolePolicyDocument"]
    return policy["Statement"][0]["Condition"]["StringLike"][f"{GITHUB_ISSUER}:sub"]


def test_the_subjects_carry_githubs_immutable_ids(template: Template) -> None:
    """The claim GitHub issues, not the one the documentation shows.

    The first CI run was denied `sts:AssumeRoleWithWebIdentity` against a
    policy naming `repo:andaro74/agentpave:ref:refs/heads/main`. CloudTrail
    recorded what was actually presented:

        repo:andaro74@3157440/agentpave@1327317546:ref:refs/heads/main

    A login and a repository name can be released and re-registered by someone
    else; the ids cannot. Trusting only the id form is the stricter reading and
    the one that works.
    """
    for sub in _subjects(template):
        assert sub.startswith(f"repo:{QUALIFIED}:"), sub
        assert str(OWNER_ID) in sub
        assert str(REPOSITORY_ID) in sub


def test_the_subject_list_carries_no_wildcard(template: Template) -> None:
    """A `:*` suffix would also match `ref:refs/tags/*`, so anyone able to push
    a tag could assume the role. The two subjects are named in full."""
    subjects = _subjects(template)
    assert not any("*" in sub for sub in subjects)
    assert set(subjects) == {
        f"repo:{QUALIFIED}:pull_request",
        f"repo:{QUALIFIED}:ref:refs/heads/main",
    }


def test_the_audience_is_pinned(template: Template) -> None:
    """Without an `aud` condition the role trusts any token GitHub issues, for
    any repository on the platform."""
    roles = template.find_resources("AWS::IAM::Role")
    policy = next(iter(roles.values()))["Properties"]["AssumeRolePolicyDocument"]
    condition = policy["Statement"][0]["Condition"]
    assert condition["StringEquals"][f"{GITHUB_ISSUER}:aud"] == GITHUB_AUDIENCE


# ── what it can do ────────────────────────────────────────────────────────


def test_the_role_can_call_an_iam_authed_function_url(template: Template) -> None:
    """Both actions. An IAM-authed function URL has required
    `lambda:InvokeFunction` as well as `lambda:InvokeFunctionUrl` since October
    2025, and holding only the URL action produces a 403 at the endpoint with
    nothing logged on the far side — ADR-025, learned the expensive way in M04
    and not worth rediscovering as a red CI run."""
    granted = {action for statement in _statements(template) for action in _actions(statement)}
    assert "lambda:InvokeFunctionUrl" in granted
    assert "lambda:InvokeFunction" in granted


def test_direct_invocation_stays_shut(template: Template) -> None:
    for statement in _statements(template):
        if _actions(statement) == ["lambda:InvokeFunction"]:
            assert statement["Condition"] == {"Bool": {"lambda:InvokedViaFunctionUrl": "true"}}


def test_the_role_arn_is_published_for_the_repository_variable(template: Template) -> None:
    """It has to be pasted into GitHub by hand, so it is an output rather than
    something an operator digs out of the console."""
    template.has_output("CiRoleArn", Match.any_value())


def test_the_oidc_provider_is_native_cloudformation(template: Template) -> None:
    """Not the CDK construct, which is backed by a custom resource — that would
    leave a Lambda and its role in the account forever to manage something
    CloudFormation supports directly (ADR-002: nothing bills while idle)."""
    template.resource_count_is("AWS::IAM::OIDCProvider", 1)
    template.resource_count_is("AWS::Lambda::Function", 0)
