"""Synth assertions for the dashboard.

A dashboard is the one component whose failures are invisible by construction:
a wrong log group name, a query naming a field that does not exist, a panel
filtered on a marker nothing writes — every one of them renders as an empty
widget, which looks exactly like a quiet week. Nothing about a deployed
dashboard tells you it is lying.

So these tests do the looking. The load-bearing one is the drift test at the
bottom: it synthesises the producer stacks and the dashboard together and
asserts the groups the dashboard *queries* are the groups the other stacks
*create* (ADR-031). That test is the entire justification for naming the groups
by hand instead of importing them across stacks.
"""

import json
import re
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from agentpave_infra.log_groups import eval_log_group, gateway_log_group
from agentpave_infra.stacks.dashboard_stack import (
    DEFECTS_LEAKED_LAST_REVIEWED,
    DashboardStack,
)
from agentpave_infra.stacks.eval_stack import EvalStack
from agentpave_infra.stacks.gateway_stack import GatewayStack
from aws_cdk.assertions import Template

REPO_ROOT = Path(__file__).resolve().parents[3]
GATEWAY_ASSET = REPO_ROOT / "platform" / "gateway"
STAGE = "test"

# The markers the two writers emit. Imported as literals rather than from the
# emitters, deliberately: if `telemetry.py` renames its event, this test must
# fail rather than silently follow the rename and leave the deployed dashboard
# filtering on a marker nothing writes any more.
GATEWAY_EVENT = "agentpave.gateway.request"
EVAL_EVENT = "agentpave.eval.scorecard"


@pytest.fixture(scope="module")
def stack() -> DashboardStack:
    app = cdk.App()
    return DashboardStack(
        app,
        "AgentPave-Dashboard-test",
        stage=STAGE,
        gateway_log_group=gateway_log_group(STAGE),
        eval_log_group=eval_log_group(STAGE),
    )


@pytest.fixture(scope="module")
def template(stack: DashboardStack) -> Template:
    return Template.from_stack(stack)


# Each widget carries `"region": "<Ref AWS::Region>"`, so the rendered body is an
# `Fn::Join` whose tokens sit *inside* string literals. Substituting the token's
# JSON would produce a quote inside a quoted value and break the parse, so it is
# replaced by a placeholder — no assertion here is about the region.
TOKEN_PLACEHOLDER = "<token>"


def _body(template: Template) -> dict[str, Any]:
    """The dashboard's rendered JSON body, as CloudWatch will read it."""
    (dashboard,) = template.find_resources("AWS::CloudWatch::Dashboard").values()
    body = dashboard["Properties"]["DashboardBody"]
    if isinstance(body, str):
        return json.loads(body)
    parts = body["Fn::Join"][1]
    return json.loads("".join(p if isinstance(p, str) else TOKEN_PLACEHOLDER for p in parts))


def _widgets(template: Template) -> list[dict[str, Any]]:
    return _body(template)["widgets"]


def _queries(template: Template) -> list[str]:
    return [
        w["properties"]["query"] for w in _widgets(template) if "query" in w.get("properties", {})
    ]


def _queried_groups(template: Template) -> set[str]:
    """The log groups the widgets actually read.

    CDK renders a `LogQueryWidget`'s groups as a `SOURCE '<name>'` clause at the
    head of the query rather than as a separate field, so this reads them back out
    of the query — which is the string CloudWatch runs, and therefore the only
    place a wrong group would show up.
    """
    return {name for query in _queries(template) for name in re.findall(r"SOURCE '([^']+)'", query)}


def _markdown(template: Template) -> str:
    return "\n".join(
        w["properties"]["markdown"]
        for w in _widgets(template)
        if "markdown" in w.get("properties", {})
    )


# ── nothing bills while idle ──────────────────────────────────────────────


def test_there_is_exactly_one_dashboard(template: Template) -> None:
    """CloudWatch gives three dashboards per account free and charges $3 a month
    for the fourth. One is a budget, not a preference (ADR-002)."""
    template.resource_count_is("AWS::CloudWatch::Dashboard", 1)


def test_the_dashboard_publishes_no_custom_metrics(template: Template) -> None:
    """The decision in ADR-030, asserted rather than trusted to the docstring.

    A metric filter is the tempting way to build these panels — and it is a
    custom metric, at roughly $0.30 a month each forever, whether or not
    anything runs. Six of them is a standing charge on an idle platform.
    """
    assert template.find_resources("AWS::Logs::MetricFilter") == {}
    assert template.find_resources("AWS::CloudWatch::Alarm") == {}
    assert "PutMetricData" not in json.dumps(template.to_json())


def test_the_dashboard_creates_no_role_and_no_function(template: Template) -> None:
    """Widgets are queries evaluated when a human opens the page. Anything here
    that needed an execution role would be something running on its own."""
    assert template.find_resources("AWS::IAM::Role") == {}
    assert template.find_resources("AWS::Lambda::Function") == {}


# ── the panels ROADMAP M05 asks for ───────────────────────────────────────


def test_all_four_panels_are_present(template: Template) -> None:
    """ROADMAP M05 names them: eval trend, tokens/cost per service, guardrail
    interventions, defect-leakage counter. Counted, so a panel cannot be dropped
    in a refactor and noticed a milestone later."""
    titles = " ".join(w["properties"].get("title", "") for w in _widgets(template)).lower()
    assert "eval trend" in titles
    assert "tokens and cost" in titles
    assert "guardrail interventions" in titles
    # The leakage counter is text, not a query — see its own test below.
    assert "defect leakage" in _markdown(template).lower()


def test_every_query_filters_on_an_event_marker(template: Template) -> None:
    """Both groups also carry Lambda's START/END/REPORT lines and anything
    anyone ever printed. A query without the marker widens the day something
    else in the group grows a field it happens to name."""
    assert _queries(template), "the dashboard has no Logs Insights widgets at all"
    for query in _queries(template):
        assert GATEWAY_EVENT in query or EVAL_EVENT in query, query


def test_the_refusal_panel_reads_blocked_by_as_an_array(template: Template) -> None:
    """`blocked_by` is a JSON array in the real row:

        "blocked_by": ["contentPolicy:PROMPT_ATTACK"]

    Logs Insights addresses its first element as `blocked_by.0`. A query naming
    the bare field returns a column of blanks — which is what M05's first draft
    did, because it was written against the schema someone intended rather than
    against the rows in `docs/VALIDATION.md`.
    """
    (refusals,) = [q for q in _queries(template) if "refused" in q]
    assert "blocked_by.0" in refusals
    # The bare field must not appear on its own anywhere in the query.
    assert "blocked_by " not in refusals.replace("blocked_by.0", "")


def test_the_refusal_panel_separates_the_stage_from_the_filter(template: Template) -> None:
    """ "Refused at the classification gate" and "refused by a content filter"
    are different events. A count that merged them would report a guardrail
    intervention rate including refusals no guardrail was involved in."""
    (refusals,) = [q for q in _queries(template) if "refused" in q]
    assert "by stage" in refusals


def test_the_spend_panel_groups_by_service(template: Template) -> None:
    """ROADMAP asks for tokens and cost *per service*. A total would answer a
    question nobody has: the useful one is which caller is the money."""
    (spend,) = [q for q in _queries(template) if "input_tokens" in q]
    assert "by service_id" in spend
    assert "sum(cost_usd)" in spend


def test_the_trend_panel_charts_the_pass_rate_over_time(template: Template) -> None:
    """A trend needs a time bucket. Without `bin(...)` this renders as a single
    aggregate number in a line chart with one point."""
    (trend,) = [q for q in _queries(template) if EVAL_EVENT in q]
    assert "pass_rate" in trend
    assert "bin(" in trend


# ── the honesty of the hand-cranked panel ─────────────────────────────────


def test_the_leakage_panel_admits_it_is_maintained_by_hand(template: Template) -> None:
    """The panel ADR-032 requires to say so on its face.

    ARCHITECTURE.md §7 Q2 asks what the honest automated trigger for this counter
    would be, and the answer is that there is not one — this platform has no
    production. A number rendered like the three measured panels beside it would
    borrow their provenance, and a hand-cranked number that looks measured is
    worse than an empty panel.
    """
    markdown = _markdown(template).lower()
    assert "by hand" in markdown
    assert "no production" in markdown
    # The review date is on the page, because a counter at zero is only
    # meaningful next to when a person last looked.
    assert DEFECTS_LEAKED_LAST_REVIEWED in _markdown(template)


def test_the_heading_names_the_groups_it_reads(template: Template) -> None:
    """A reader who finds a panel empty cannot tell "nothing ran" from "wrong
    group" out of a chart. The page says which groups it is reading."""
    markdown = _markdown(template)
    assert gateway_log_group(STAGE) in markdown
    assert eval_log_group(STAGE) in markdown
    # And the names on the page are the names in the queries, not a stale copy
    # written by hand into the heading.
    assert _queried_groups(template) == {gateway_log_group(STAGE), eval_log_group(STAGE)}


# ── the drift test ────────────────────────────────────────────────────────


def test_the_dashboard_queries_the_groups_the_other_stacks_create() -> None:
    """The test that makes ADR-031 safe.

    Naming log groups by hand buys a dashboard that is correct at synth time and
    costs the possibility of a typo. That trade is only worth taking if the typo
    fails here — so this synthesises the gateway, the eval service and the
    dashboard from the same stage and asserts the names match on both sides.

    Without it, renaming a producer's group leaves a dashboard that deploys
    clean, queries a group that does not exist, and reports a platform where
    nothing ever happens.
    """
    app = cdk.App()
    stage = "drift"
    gateway = GatewayStack(
        app,
        f"AgentPave-Gateway-{stage}",
        asset_path=str(GATEWAY_ASSET),
        model_serve="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        model_judge="us.anthropic.claude-sonnet-4-6",
        log_group_name=gateway_log_group(stage),
    )
    evalsvc = EvalStack(app, f"AgentPave-Eval-{stage}", log_group_name=eval_log_group(stage))
    dashboard = DashboardStack(
        app,
        f"AgentPave-Dashboard-{stage}",
        stage=stage,
        gateway_log_group=gateway_log_group(stage),
        eval_log_group=eval_log_group(stage),
    )

    created = {
        group["Properties"]["LogGroupName"]
        for stack in (gateway, evalsvc)
        for group in Template.from_stack(stack).find_resources("AWS::Logs::LogGroup").values()
    }

    queried = _queried_groups(Template.from_stack(dashboard))

    assert queried, "the dashboard queries no log groups at all"
    assert queried <= created, f"the dashboard queries groups nobody creates: {queried - created}"
    # Both writers are charted. A dashboard reading only the gateway would have
    # no eval trend, which is the panel M05 exists to deliver.
    assert queried == {gateway_log_group(stage), eval_log_group(stage)}
