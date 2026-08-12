"""The scorecard line the dashboard's eval trend is charted from.

Tested at the line level because the alternative is testing it from the chart,
and a chart cannot tell a run that scored badly from a line that was never
written. Everything below is the pure half; `emit` is wiring and runs only under
the deployed gate.
"""

from __future__ import annotations

from agentpave_evalsvc.models import CaseResult, ProbeResult, Scorecard
from agentpave_evalsvc.telemetry import (
    EVENT,
    LOCAL_ORIGIN,
    event_timestamp_ms,
    origin,
    scorecard_line,
)

CREATED_AT = "2026-08-12T09:14:03+00:00"


def _case(case_id: str, passed: bool, **overrides) -> CaseResult:
    base = {
        "case_id": case_id,
        "capability": "airing",
        "passed": passed,
        "latency_ms": 10,
        "cost_usd": 0.001,
    }
    return CaseResult.model_validate({**base, **overrides})


def _card(cases: tuple[CaseResult, ...], probes: tuple[ProbeResult, ...] = ()) -> Scorecard:
    return Scorecard(
        run_id="eval-1786459419-6aaf90",
        created_at=CREATED_AT,
        model_serve="serve-model",
        model_judge="judge-model",
        cases=cases,
        probes=probes,
    )


def _probe(probe_id: str, passed: bool) -> ProbeResult:
    return ProbeResult(
        probe_id=probe_id,
        outcome="guardrail_blocked",
        passed=passed,
        detail="contentPolicy:PROMPT_ATTACK",
    )


def _line(**kwargs) -> dict:
    return scorecard_line(_card(**kwargs), run_origin="nightly eval")


# ── the shape the queries depend on ───────────────────────────────────────


def test_the_line_carries_the_event_marker():
    """The trend query filters on it. Without the marker the widget either
    matches nothing or matches Lambda's own REPORT lines."""
    assert _line(cases=(_case("a", True),))["event"] == EVENT


def test_the_line_is_flat():
    """Logs Insights addresses nested fields with dotted paths and they work,
    but every widget query then carries the nesting — and a dashboard is edited
    by whoever is on call, not by whoever wrote the schema. Per-capability
    scores live in the baseline table, which is what `--diff` reads."""
    for key, value in _line(cases=(_case("a", True),)).items():
        assert not isinstance(value, dict | list | tuple), f"{key} is nested"


def test_the_line_reports_the_counts_and_the_rate():
    """Both, because they answer different questions. A run at 30/31 has a pass
    rate of 97% and did not pass; a trend holding only the boolean could not
    show how close it came."""
    line = _line(
        cases=(_case("a", True), _case("b", True), _case("c", False)),
        probes=(_probe("p1", True),),
    )
    assert line["cases_passed"] == 2
    assert line["cases_total"] == 3
    assert line["probes_passed"] == 1
    assert line["probes_total"] == 1
    assert line["pass_rate"] == 2 / 3
    assert line["passed"] is False


def test_a_failing_probe_fails_the_run_in_the_line_too():
    """`Scorecard.passed` requires every case *and* every probe. A line that
    reported a clean run because the cases passed would chart a green trend
    across the day a guardrail stopped working."""
    line = _line(cases=(_case("a", True),), probes=(_probe("p1", False),))
    assert line["passed"] is False
    # The pass rate is about cases only, and stays 100% — which is exactly why
    # the boolean is on the line beside it.
    assert line["pass_rate"] == 1.0


def test_both_models_are_on_the_line():
    """A point on the trend whose model pair is unknown is not comparable to the
    next point — a score change and a model change look identical after the
    fact. The baseline store learned this the same way (see `Baseline`)."""
    line = _line(cases=(_case("a", True),))
    assert line["model_serve"] == "serve-model"
    assert line["model_judge"] == "judge-model"


# ── what must never reach a log group ─────────────────────────────────────


def test_the_line_carries_no_answer_text_and_no_case_detail():
    """Summary numbers only.

    The gateway's line records filter *types* and never the matched text, on the
    principle that a blocked string echoed into a log group undoes the filter
    that stopped it. The same rule applies here for a different reason: an eval
    answer is model output about fixture data, the scorecard and the PR comment
    already hold it in full, and a log group is the wrong place for prose.
    """
    line = _line(
        cases=(
            _case(
                "airing-schedule-abc-overnight",
                False,
                assert_failures=("must_contain: 'Apple TV' absent from the answer",),
            ),
        ),
        probes=(_probe("injection-via-tool-response", True),),
    )
    rendered = str(line)
    assert "Apple TV" not in rendered
    assert "must_contain" not in rendered
    assert "PROMPT_ATTACK" not in rendered
    # Not even the case ids: the line is one row per run, not per case.
    assert "airing-schedule-abc-overnight" not in rendered


# ── origin ────────────────────────────────────────────────────────────────


def test_a_laptop_run_is_marked_local():
    """A trend that mixed a developer's half-broken debugging runs into the same
    line as the nightly would report regressions nobody caused."""
    assert origin({}) == LOCAL_ORIGIN
    # Present but not "true" — a variable someone exported by hand must not
    # promote a laptop run to a workflow.
    assert origin({"GITHUB_ACTIONS": "", "GITHUB_WORKFLOW": "gate"}) == LOCAL_ORIGIN


def test_a_workflow_run_is_named_after_its_workflow():
    """ "Was this the pull-request gate or the nightly" is the question asked of
    a suspicious point, and the workflow name is the answer."""
    assert origin({"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW": "nightly eval"}) == "nightly eval"
    assert origin({"GITHUB_ACTIONS": "true"}) == "github-actions"


# ── timestamps ────────────────────────────────────────────────────────────


def test_the_event_is_stamped_with_the_runs_own_time():
    """`bin(1d)` buckets by the event timestamp, so a run must be stamped when it
    ran rather than when it was written. The two differ by the length of an eval
    — minutes — which is enough to put a run that started at 23:58 in the wrong
    day."""
    assert event_timestamp_ms(CREATED_AT) == 1786526043000


def test_an_unparseable_timestamp_falls_back_rather_than_raising():
    """A malformed `created_at` is a bug worth a red test elsewhere, not a reason
    to drop the only datapoint the dashboard was going to get."""
    assert event_timestamp_ms("not a date") > 0
