"""IAM assertions for the MCP stack.

The assertion that matters here is a negative one. ADR-003 keeps the path to
AgentCore open by requiring that nothing but the gateway holds Bedrock
permissions; this is the first component where that could quietly stop being
true, so it is checked rather than reviewed.
"""

from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from agentpave_infra.stacks.mcp_tvmaze_stack import McpTvmazeStack
from aws_cdk.assertions import Match, Template

REPO_ROOT = Path(__file__).resolve().parents[3]
MCP_ASSET = REPO_ROOT / "platform" / "mcp-tvmaze"

# The complete permission set. A tool server that reads a public API needs
# nothing beyond writing its own logs.
EXPECTED_ACTIONS = {"logs:CreateLogStream", "logs:PutLogEvents"}


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    stack = McpTvmazeStack(app, "AgentPave-Mcp-test", asset_path=str(MCP_ASSET))
    return Template.from_stack(stack)


def _statements(template: Template) -> list[dict[str, Any]]:
    statements: list[dict[str, Any]] = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        statements.extend(policy["Properties"]["PolicyDocument"]["Statement"])
    return statements


def _actions(statement: dict[str, Any]) -> list[str]:
    action = statement["Action"]
    return [action] if isinstance(action, str) else list(action)


def test_role_holds_no_bedrock_permission_at_all(template: Template) -> None:
    # ADR-003 constraint (a): the gateway is the only model path. A tool server
    # that acquired bedrock:InvokeModel would break the AgentCore migration
    # story silently — nothing would fail, it would just no longer be true.
    granted = {action for stmt in _statements(template) for action in _actions(stmt)}
    bedrock = {action for action in granted if action.lower().startswith("bedrock")}
    assert not bedrock, f"the MCP server must hold no Bedrock access: {bedrock}"


def test_role_holds_exactly_the_expected_actions(template: Template) -> None:
    granted = {action for stmt in _statements(template) for action in _actions(stmt)}
    assert granted == EXPECTED_ACTIONS


def test_no_statement_grants_a_wildcard_resource(template: Template) -> None:
    for statement in _statements(template):
        resource = statement["Resource"]
        resources = [resource] if isinstance(resource, str | dict) else list(resource)
        assert "*" not in resources


def test_stack_synthesises_exactly_one_role(template: Template) -> None:
    template.resource_count_is("AWS::IAM::Role", 1)


def test_role_attaches_no_managed_policies(template: Template) -> None:
    template.has_resource_properties(
        "AWS::IAM::Role",
        Match.not_(Match.object_like({"ManagedPolicyArns": Match.any_value()})),
    )


def test_function_url_requires_iam_auth(template: Template) -> None:
    # SigV4 at the edge is the authentication; see ADR-008 for what it is and
    # is not doing about agent identity.
    template.has_resource_properties("AWS::Lambda::Url", {"AuthType": "AWS_IAM"})


def test_function_is_told_which_identity_to_authorize_as(template: Template) -> None:
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": Match.object_like({"AGENTPAVE_AGENT_ID": Match.any_value()})
            }
        },
    )


def test_deployed_server_serves_fixtures_not_live_tvmaze(template: Template) -> None:
    # `make conformance` asserts on specific shows. Pointing the deployed
    # server at the live catalogue would make the gate go red overnight for
    # reasons that have nothing to do with the platform.
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Environment": {"Variables": Match.object_like({"AGENTPAVE_TVMAZE_MODE": "fixtures"})}},
    )


def test_log_group_has_bounded_retention(template: Template) -> None:
    template.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": Match.any_value()})


def test_stack_provisions_nothing_that_bills_while_idle(template: Template) -> None:
    # ADR-002. Lambda and a log group only; no table, no queue, no floor.
    for billed_at_rest in ("AWS::DynamoDB::Table", "AWS::SQS::Queue", "AWS::RDS::DBInstance"):
        template.resource_count_is(billed_at_rest, 0)
