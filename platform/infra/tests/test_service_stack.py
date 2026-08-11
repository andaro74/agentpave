"""IAM assertions for a scaffolded service's stack.

This is the golden path's governance, checked. Everything the template says
about a service reaching models only through the gateway is a comment until
something asserts the role cannot do otherwise — and a scaffolder that renders
a confident README over a permissive role is worse than no scaffolder, because
it manufactures the belief at scale.

The Bedrock assertions here are negative on purpose. `test_role_holds_exactly_
the_expected_actions` in the gateway suite proves a *positive* set; these prove
an absence, which is the harder thing to keep true as a stack grows.
"""

from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from agentpave_infra.stacks.service_stack import (
    BOUNDARY_ACTIONS,
    INVOCATION_ALARM_THRESHOLD,
    ServiceStack,
)
from aws_cdk.assertions import Match, Template

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_ASSET = REPO_ROOT / "services" / "catalog-agent"


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    stack = ServiceStack(
        app,
        "AgentPave-Service-CatalogAgent-test",
        asset_path=str(SERVICE_ASSET),
        service_name="catalog-agent",
        package="agentpave_catalog_agent",
        gateway_url="https://gateway.example/",
        mcp_url="https://mcp.example/mcp",
    )
    return Template.from_stack(stack)


def _statements(template: Template) -> list[dict[str, Any]]:
    statements: list[dict[str, Any]] = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        statements.extend(policy["Properties"]["PolicyDocument"]["Statement"])
    for policy in template.find_resources("AWS::IAM::ManagedPolicy").values():
        statements.extend(policy["Properties"]["PolicyDocument"]["Statement"])
    return statements


def _actions(statement: dict[str, Any]) -> list[str]:
    action = statement["Action"]
    return [action] if isinstance(action, str) else list(action)


# ── the property the whole platform rests on ──────────────────────────────


def test_the_service_role_holds_no_bedrock_permission(template: Template) -> None:
    """Invariant 1. A scaffolded service that could invoke a model directly
    would route around the guardrail, the classification check and the metering
    ledger in one step — and every quality claim this platform makes assumes it
    cannot."""
    for statement in _statements(template):
        for action in _actions(statement):
            assert not action.lower().startswith("bedrock"), f"service role holds {action}"


def test_no_bedrock_action_appears_anywhere_in_the_template(template: Template) -> None:
    """Belt and braces: catches a grant arriving through a CDK helper construct
    rather than through a statement we wrote.

    Matches `bedrock:` with the colon, not the bare word. An IAM action is
    always `bedrock:Something`, while prose is not — the first version of this
    test failed on the boundary policy's own description ("no Bedrock, ever"),
    which is a false positive that would have taught someone to delete the
    assertion rather than fix a permission.
    """
    assert "bedrock:" not in str(template.to_json()).lower()


def test_the_function_is_not_told_how_to_reach_a_model(template: Template) -> None:
    """No model id, no guardrail id, no Bedrock endpoint. A service that cannot
    name a model is a service that cannot quietly acquire one."""
    functions = template.find_resources("AWS::Lambda::Function")
    for function in functions.values():
        env = str(function["Properties"].get("Environment", {})).lower()
        for forbidden in ("bedrock", "guardrail", "anthropic", "model_id"):
            assert forbidden not in env, f"service environment leaks {forbidden!r}"


# ── the permission boundary ───────────────────────────────────────────────


def test_the_role_carries_a_permission_boundary(template: Template) -> None:
    """The difference between "we did not grant Bedrock" and "this role cannot
    hold Bedrock". Without a boundary, a later edit to the inline policy is the
    only thing standing between a scaffolded service and a direct model call."""
    template.has_resource_properties(
        "AWS::IAM::Role",
        {"PermissionsBoundary": Match.any_value()},
    )


def test_the_boundary_does_not_permit_bedrock(template: Template) -> None:
    assert not any(action.lower().startswith("bedrock") for action in BOUNDARY_ACTIONS)


def test_the_boundary_is_an_allow_list_not_a_bedrock_deny(template: Template) -> None:
    """A deny-list is a promise to remember every service someone might reach
    for next. An allow-list is a promise to remember one thing."""
    for statement in _statements(template):
        assert statement.get("Effect") != "Deny", (
            "the boundary should constrain by enumerating what is allowed, "
            "not by denying what is currently feared"
        )


def test_the_role_attaches_no_managed_policies_beyond_its_boundary(template: Template) -> None:
    # AWSLambdaBasicExecutionRole would grant logs on `*`, which the boundary
    # permits — but it also normalises "attach a managed policy" as a habit,
    # and the next one may not be so harmless.
    for role in template.find_resources("AWS::IAM::Role").values():
        assert not role["Properties"].get("ManagedPolicyArns"), (
            "a scaffolded service role should carry only its permission boundary"
        )


def test_the_role_can_actually_call_an_iam_authed_function_url(template: Template) -> None:
    """Both actions, or the service cannot reach a single tool.

    Since October 2025 an IAM-authed function URL requires the caller to hold
    `lambda:InvokeFunction` as well as `lambda:InvokeFunctionUrl`. With only the
    URL action the call is refused at the endpoint — 403, no invocation logged
    on the far side, nothing to distinguish it from a bad signature. Synth was
    clean, the deploy was clean, and every other assertion in this file was
    green while the golden path was completely broken.

    Asserted against the boundary too: a grant the ceiling does not admit is
    not a grant.

    https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html
    """
    granted = {action for statement in _statements(template) for action in _actions(statement)}
    for action in ("lambda:InvokeFunctionUrl", "lambda:InvokeFunction"):
        assert action in granted, f"{action} is required to invoke an IAM-authed function URL"
        assert action in BOUNDARY_ACTIONS, f"the boundary caps the role below {action}"


def test_direct_invocation_is_shut_even_though_invoke_is_granted(template: Template) -> None:
    """`lambda:InvokeFunction` is granted for the front door only.

    Unconditioned, it would let a scaffolded service call any function in the
    account through the ordinary Invoke API — the gateway without its URL, and
    whatever else lives here. `InvokedViaFunctionUrl` is what keeps the second
    action to the permission we meant to grant.
    """
    statements = [
        statement
        for policy in template.find_resources("AWS::IAM::Policy").values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if _actions(statement) == ["lambda:InvokeFunction"]
    ]
    assert statements, "the service needs lambda:InvokeFunction to use a function URL at all"
    for statement in statements:
        assert statement.get("Condition") == {"Bool": {"lambda:InvokedViaFunctionUrl": "true"}}, (
            "unconditioned lambda:InvokeFunction is direct invocation of anything in the account"
        )


# ── the rest of the shape ─────────────────────────────────────────────────


def test_the_function_url_requires_iam_auth(template: Template) -> None:
    template.has_resource_properties("AWS::Lambda::Url", {"AuthType": "AWS_IAM"})


def test_a_runaway_loop_raises_an_alarm(template: Template) -> None:
    """ADR-002 says nothing bills while idle. This is the other half: a ceiling
    on what a retry loop can cost while busy."""
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "MetricName": "Invocations",
            "Threshold": INVOCATION_ALARM_THRESHOLD,
            "EvaluationPeriods": 1,
        },
    )


def test_log_retention_is_bounded(template: Template) -> None:
    template.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": Match.any_value()})


def test_tracing_is_active(template: Template) -> None:
    # ADR-019: spans come from the function itself, so the function has to be
    # traced for the three hops to correlate at all.
    template.has_resource_properties("AWS::Lambda::Function", {"TracingConfig": {"Mode": "Active"}})


def test_the_service_is_told_where_the_gateway_and_mcp_server_are(template: Template) -> None:
    # It has no other way to reach either, and both clients fail loudly rather
    # than silently when the variable is missing.
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "AGENTPAVE_GATEWAY_URL": Match.any_value(),
                        "AGENTPAVE_MCP_URL": Match.any_value(),
                    }
                )
            }
        },
    )


def test_stack_synthesises_exactly_one_role_and_one_function(template: Template) -> None:
    # A second role is a second permission surface, usually smuggled in by a
    # CDK helper construct. One service, one role.
    template.resource_count_is("AWS::IAM::Role", 1)
    template.resource_count_is("AWS::Lambda::Function", 1)


def test_the_stack_publishes_what_the_walkthrough_needs(template: Template) -> None:
    """`make walkthrough` resolves the service by stack output, never by guess.

    The generated function name is not derivable from the stack name, so a
    deployed gate without this output would either guess an identifier or skip
    the traced act — and a gate that cannot check has to fail, not skip
    (standing rule 5). Asserted here so removing the output turns `make check`
    red rather than surfacing as a failed act at the end of a deploy.
    """
    outputs = template.to_json().get("Outputs", {})
    assert {"ServiceUrl", "ServiceRoleArn", "ServiceFunctionName"} <= set(outputs)
