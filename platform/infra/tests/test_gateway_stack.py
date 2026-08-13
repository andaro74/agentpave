"""IAM assertions for the gateway stack.

ARCHITECTURE.md invariant 1 says no service holds Bedrock permissions of its
own. That is only true if something checks, so these tests are the check. They
are deliberately exact rather than "contains": a test that asserts the role has
`bedrock:InvokeModel` would still pass if someone added `iam:PassRole` next to
it. Asserting the whole permission set means any widening turns the gate red.
"""

from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from agentpave_gateway.guardrail import load_policy
from agentpave_infra.log_groups import gateway_log_group
from agentpave_infra.stacks.gateway_stack import GatewayStack
from aws_cdk.assertions import Match, Template

REPO_ROOT = Path(__file__).resolve().parents[3]
GATEWAY_ASSET = REPO_ROOT / "platform" / "gateway"

# The complete set of permissions the gateway may hold. Widening this set is a
# design decision: change it here, in the same commit as the stack, or not
# at all.
EXPECTED_ACTIONS = {
    "bedrock:InvokeModel",
    "bedrock:ApplyGuardrail",
    "dynamodb:PutItem",
    "logs:CreateLogStream",
    "logs:PutLogEvents",
}


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    stack = GatewayStack(
        app,
        "AgentPave-Gateway-test",
        asset_path=str(GATEWAY_ASSET),
        model_serve="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        model_judge="us.anthropic.claude-sonnet-4-6",
        log_group_name=gateway_log_group("test"),
    )
    return Template.from_stack(stack)


def _statements(template: Template) -> list[dict[str, Any]]:
    """Every IAM statement the stack synthesises, from every policy."""
    statements: list[dict[str, Any]] = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        statements.extend(policy["Properties"]["PolicyDocument"]["Statement"])
    return statements


def _actions(statement: dict[str, Any]) -> list[str]:
    action = statement["Action"]
    return [action] if isinstance(action, str) else list(action)


def _resources(statement: dict[str, Any]) -> list[Any]:
    resource = statement["Resource"]
    return [resource] if isinstance(resource, str | dict) else list(resource)


def test_stack_synthesises_exactly_one_role(template: Template) -> None:
    # A second role would mean a second permission surface to reason about —
    # usually a CDK helper construct smuggling one in (log retention, custom
    # resources). One component, one role.
    template.resource_count_is("AWS::IAM::Role", 1)


def test_stack_synthesises_exactly_one_function(template: Template) -> None:
    template.resource_count_is("AWS::Lambda::Function", 1)


def test_role_holds_exactly_the_expected_actions(template: Template) -> None:
    granted = {action for stmt in _statements(template) for action in _actions(stmt)}
    assert granted == EXPECTED_ACTIONS


def test_no_statement_grants_wildcard_resource(template: Template) -> None:
    for statement in _statements(template):
        assert "*" not in _resources(statement), (
            f"statement {statement.get('Sid', '<unnamed>')} grants a wildcard resource"
        )


def test_no_statement_grants_wildcard_action(template: Template) -> None:
    for statement in _statements(template):
        for action in _actions(statement):
            assert not action.endswith(":*"), f"wildcard action {action}"
            assert action != "*", "fully wildcard action"


def test_bedrock_access_is_scoped_to_anthropic_models(template: Template) -> None:
    bedrock = [s for s in _statements(template) if "bedrock:InvokeModel" in _actions(s)]
    assert len(bedrock) == 1, "bedrock access should live in exactly one statement"

    for resource in _resources(bedrock[0]):
        rendered = str(resource)
        assert "anthropic" in rendered, f"unscoped Bedrock resource: {rendered}"


def test_role_attaches_no_managed_policies(template: Template) -> None:
    # AWSLambdaBasicExecutionRole would grant logs actions on `*`, quietly
    # undoing test_no_statement_grants_wildcard_resource.
    template.has_resource_properties(
        "AWS::IAM::Role",
        Match.not_(Match.object_like({"ManagedPolicyArns": Match.any_value()})),
    )


def test_metering_table_is_on_demand(template: Template) -> None:
    # ADR-002: nothing bills while idle. Provisioned capacity bills at rest.
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "BillingMode": "PAY_PER_REQUEST",
            "ProvisionedThroughput": Match.absent(),
        },
    )


def test_function_url_requires_iam_auth(template: Template) -> None:
    # An unauthenticated URL in front of Bedrock is an open invitation to
    # spend someone else's money.
    template.has_resource_properties("AWS::Lambda::Url", {"AuthType": "AWS_IAM"})


def test_the_url_grants_nobody_through_a_resource_policy(template: Template) -> None:
    """See the twin of this test in `test_mcp_tvmaze_stack.py`. Same-account
    callers are authorized by their own identity policy; a standing grant to
    the account root here would open nothing that was shut."""
    assert not template.find_resources("AWS::Lambda::Permission")


def test_log_group_has_bounded_retention(template: Template) -> None:
    # Never-expiring logs are a slow leak that bills while idle.
    template.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": Match.any_value()})


def test_the_log_group_carries_the_name_it_was_given(template: Template) -> None:
    """The dashboard queries this group by name at synth time (ADR-031).

    A CDK-generated name would synthesise, deploy, and leave the dashboard's
    spend and guardrail panels permanently empty — the failure mode `log_groups`
    exists to make impossible. Asserted here so the property belongs to the
    stack rather than only to the dashboard's drift test.
    """
    template.has_resource_properties(
        "AWS::Logs::LogGroup", {"LogGroupName": gateway_log_group("test")}
    )


# ── guardrail ─────────────────────────────────────────────────────────────


def test_stack_renders_exactly_one_guardrail(template: Template) -> None:
    template.resource_count_is("AWS::Bedrock::Guardrail", 1)


def test_guardrail_carries_the_authored_policy(template: Template) -> None:
    # The YAML is the source of truth; this asserts it actually reaches
    # CloudFormation rather than being decorative.
    policy = load_policy()
    template.has_resource_properties(
        "AWS::Bedrock::Guardrail",
        {
            "BlockedInputMessaging": policy.blocked_input_message,
            "BlockedOutputsMessaging": policy.blocked_output_message,
        },
    )


def test_guardrail_filters_prompt_attack(template: Template) -> None:
    # The one filter the M03 adversarial gate depends on.
    template.has_resource_properties(
        "AWS::Bedrock::Guardrail",
        {
            "ContentPolicyConfig": {
                "FiltersConfig": Match.array_with(
                    [Match.object_like({"Type": "PROMPT_ATTACK", "InputStrength": "HIGH"})]
                )
            }
        },
    )


def test_guardrail_carries_every_authored_pii_filter(template: Template) -> None:
    """The substitute coverage ADR-011 promises, and the only coverage the PII
    filters have.

    Standing rule 3 forbids the PII-shaped strings a runtime probe would need,
    so no gate in this project ever observes these filters firing.

    Read what this does and does not prove. Both sides come from the same
    YAML, so it is a **round-trip** check: it catches a render path that drops
    or renames entities on their way to CloudFormation. It does *not* catch an
    entity deleted from the authored policy — that moves both sides together
    and stays green here. `test_committed_policy_blocks_contact_and_financial_pii`
    in the gateway's own suite is what pins the required set, with the entity
    types written out by hand.

    Neither asserts the control *fires*. ADR-011 is explicit about that gap;
    this test is not evidence it is closed.
    """
    policy = load_policy()
    assert policy.pii_entities, "the authored policy declares no PII entities to assert on"
    template.has_resource_properties(
        "AWS::Bedrock::Guardrail",
        {
            "SensitiveInformationPolicyConfig": {
                "PiiEntitiesConfig": Match.array_with(
                    [
                        Match.object_like({"Type": entity.type, "Action": entity.action})
                        for entity in policy.pii_entities
                    ]
                )
            }
        },
    )


def test_guardrail_permission_is_scoped_to_this_guardrail(template: Template) -> None:
    # `bedrock:ApplyGuardrail` on `*` would let the gateway apply — or claim to
    # apply — a guardrail this stack does not own.
    statements = [s for s in _statements(template) if "bedrock:ApplyGuardrail" in _actions(s)]
    assert len(statements) == 1

    resources = _resources(statements[0])
    assert resources != ["*"]
    assert all("GatewayGuardrail" in str(r) for r in resources)


def test_function_is_told_which_guardrail_to_apply(template: Template) -> None:
    # The handler fails closed without these, so the stack must supply them.
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "AGENTPAVE_GUARDRAIL_ID": Match.any_value(),
                        "AGENTPAVE_GUARDRAIL_VERSION": Match.any_value(),
                    }
                )
            }
        },
    )
