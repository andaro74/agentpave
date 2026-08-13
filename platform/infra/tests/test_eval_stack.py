"""Synth assertions for the eval stack.

Two properties, both of which are invariants rather than preferences:

* **No Bedrock permission anywhere in this stack.** The eval service reaches
  models only through the gateway (ARCHITECTURE.md invariant 1). This is the
  negative assertion — the one that stays true by being checked, since nothing
  about a passing eval run would reveal that evalsvc had grown its own
  `bedrock:InvokeModel` and started grading off-gateway.
* **On-demand billing.** Nothing bills while idle (ADR-002). A provisioned
  floor on a table that is written a few times a day would violate that, and
  it is exactly the kind of change that looks harmless in a diff.
"""

import json

import aws_cdk as cdk
import pytest
from agentpave_infra.log_groups import EVAL_LOG_STREAM, eval_log_group
from agentpave_infra.stacks.eval_stack import EvalStack
from aws_cdk.assertions import Template

LOG_GROUP = eval_log_group("test")


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    return Template.from_stack(EvalStack(app, "AgentPave-Eval-test", log_group_name=LOG_GROUP))


def test_baseline_table_is_on_demand(template: Template):
    """A provisioned floor would bill while idle (ADR-002)."""
    template.has_resource_properties("AWS::DynamoDB::Table", {"BillingMode": "PAY_PER_REQUEST"})


def test_baseline_table_has_no_provisioned_throughput(template: Template):
    """PAY_PER_REQUEST and a throughput block are mutually exclusive; asserting
    the absence catches a half-finished edit that sets both."""
    (table,) = template.find_resources("AWS::DynamoDB::Table").values()
    assert "ProvisionedThroughput" not in table["Properties"]


def test_table_is_keyed_for_a_single_reverse_query(template: Template):
    """`latest_baseline` reads the newest row with one reverse query. That is
    only possible if the sort key is time-ordered."""
    (table,) = template.find_resources("AWS::DynamoDB::Table").values()
    assert table["Properties"]["KeySchema"] == [
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ]


def test_the_eval_stack_holds_no_bedrock_permission(template: Template):
    """The load-bearing negative assertion.

    If evalsvc ever grows its own Bedrock access, judging stops going through
    the gateway — no guardrail, no metering, no central routing — and every
    scorecard afterwards is measuring something the platform does not govern.
    Nothing in a green eval run would show it.
    """
    rendered = json.dumps(template.to_json())
    assert "bedrock:" not in rendered


def test_the_eval_stack_creates_no_roles_or_functions(template: Template):
    """The stack is a table and a log group. A Lambda here would be the nightly
    runner arriving early, with a role nobody asked for (ADR-012) — and the log
    group deliberately does not come with one: it is written to by the CI role,
    which lives in `CiStack` where its grants can be asserted together."""
    assert template.find_resources("AWS::IAM::Role") == {}
    assert template.find_resources("AWS::Lambda::Function") == {}


def test_the_table_name_is_published(template: Template):
    """`pave eval --diff` resolves the table from this output; without it the
    diff would need the name hard-coded or guessed."""
    assert "BaselineTableName" in template.to_json()["Outputs"]


# ── the scorecard log group ───────────────────────────────────────────────


def test_the_scorecard_group_carries_the_name_it_was_given(template: Template):
    """The dashboard names this group in a query at synth time (ADR-031)."""
    template.has_resource_properties("AWS::Logs::LogGroup", {"LogGroupName": LOG_GROUP})


def test_the_scorecard_group_outlives_a_week(template: Template):
    """The trend panel's only history.

    The Lambda groups keep a week, which is plenty for debugging a request. This
    group is the sole source of the eval trend, and a fortnight of nightlies is
    the shortest window in which the word "trend" means anything — a seven-day
    retention would silently cap the chart at seven points.
    """
    (group,) = template.find_resources("AWS::Logs::LogGroup").values()
    assert group["Properties"]["RetentionInDays"] >= 30


def test_the_stream_exists_so_ci_needs_no_createlogstream(template: Template):
    """The narrowest write in the platform depends on this resource.

    `CiStack` grants `logs:PutLogEvents` on exactly this stream and deliberately
    withholds `logs:CreateLogStream`. If the stream stopped being created here,
    the grant would be correct and useless — every eval run would report a failed
    telemetry write, and the dashboard would stay flat while the gate stayed
    green (ADR-027, ADR-030).
    """
    template.has_resource_properties("AWS::Logs::LogStream", {"LogStreamName": EVAL_LOG_STREAM})


def test_the_group_and_stream_names_are_published(template: Template):
    """`pave eval` resolves both from stack outputs rather than from environment
    variables, so a run writes to whatever was last deployed."""
    outputs = template.to_json()["Outputs"]
    assert "ScorecardLogGroupName" in outputs
    assert "ScorecardLogStreamName" in outputs
