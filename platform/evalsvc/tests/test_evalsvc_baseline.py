"""Baseline diffing and calibration — the two pieces that decide whether a
run's numbers can be believed.

The diff tests concentrate on the appeared/disappeared distinction, because
that is where a quality regression and a dataset defect are easiest to confuse.
"""

from __future__ import annotations

from agentpave_evalsvc.baseline import diff, render
from agentpave_evalsvc.calibration import calibrate, meets_floor
from agentpave_evalsvc.calibration import render as render_calibration
from agentpave_evalsvc.judge import JudgeError
from agentpave_evalsvc.models import (
    Baseline,
    CalibrationSample,
    CaseResult,
    JudgeVerdict,
    Scorecard,
)


def _card(results: list[tuple[str, str, bool]], cost: float = 0.01) -> Scorecard:
    return Scorecard(
        run_id="run-2",
        created_at="2026-08-08T00:00:00+00:00",
        model_serve="serve",
        model_judge="judge",
        cases=tuple(
            CaseResult(
                case_id=case_id,
                capability=capability,
                passed=passed,
                latency_ms=10,
                cost_usd=cost / max(len(results), 1),
            )
            for case_id, capability, passed in results
        ),
    )


def _baseline(by_capability: dict[str, float], pass_rate: float, cost: float = 0.01) -> Baseline:
    return Baseline(
        run_id="run-1",
        created_at="2026-08-07T00:00:00+00:00",
        pass_rate=pass_rate,
        total_cost_usd=cost,
        by_capability=by_capability,
    )


# ── the diff ──────────────────────────────────────────────────────────────


def test_identical_scores_are_not_a_regression():
    card = _card([("a", "airing", True), ("b", "airing", False)])
    result = diff(card, _baseline({"airing": 0.5}, 0.5))
    assert result.pass_rate_delta == 0.0
    assert not result.regressed


def test_a_drop_in_pass_rate_is_a_regression():
    card = _card([("a", "airing", False), ("b", "airing", False)])
    result = diff(card, _baseline({"airing": 1.0}, 1.0))
    assert result.pass_rate_delta == -1.0
    assert result.regressed


def test_an_improvement_is_not_a_regression():
    card = _card([("a", "airing", True), ("b", "airing", True)])
    result = diff(card, _baseline({"airing": 0.5}, 0.5))
    assert result.pass_rate_delta == 0.5
    assert not result.regressed


def test_one_capability_going_backwards_regresses_a_flat_overall_score():
    """Overall pass rate can hold steady while a capability collapses and
    another improves. The per-capability check is what catches that."""
    card = _card([("a", "airing", False), ("b", "summarize", True)])
    result = diff(card, _baseline({"airing": 1.0, "summarize": 0.0}, 0.5))
    assert result.pass_rate_delta == 0.0
    assert result.regressed


def test_a_disappeared_capability_is_a_regression_not_a_zero():
    """Cases that did not run are a dataset defect, and reporting that as
    "no change" would be M02's false pass in a new costume."""
    card = _card([("a", "airing", True)])
    result = diff(card, _baseline({"airing": 1.0, "enrichment": 1.0}, 1.0))
    assert result.disappeared == ("enrichment",)
    assert "enrichment" not in result.by_capability_delta
    assert result.regressed


def test_a_new_capability_appears_without_a_false_delta():
    """Comparing a new capability against an absent baseline as -1.0 would
    describe a regression that did not happen."""
    card = _card([("a", "airing", True), ("b", "enrichment", True)])
    result = diff(card, _baseline({"airing": 1.0}, 1.0))
    assert result.appeared == ("enrichment",)
    assert not result.regressed


def test_render_names_a_disappeared_capability_loudly():
    card = _card([("a", "airing", True)])
    rendered = render(diff(card, _baseline({"airing": 1.0, "running": 1.0}, 1.0)))
    assert "DISAPPEARED" in rendered
    assert "REGRESSED" in rendered


def test_baseline_round_trips_from_a_scorecard():
    card = _card([("a", "airing", True), ("b", "enrichment", False)])
    baseline = Baseline.from_scorecard(card)
    assert baseline.pass_rate == 0.5
    assert baseline.by_capability == {"airing": 1.0, "enrichment": 0.0}


# ── calibration ───────────────────────────────────────────────────────────


def _sample(case_id: str, human_pass: bool) -> CalibrationSample:
    return CalibrationSample(
        case_id=case_id, answer="an answer", human_pass=human_pass, note="a note"
    )


def _verdict(passing: bool) -> JudgeVerdict:
    score = 5 if passing else 1
    return JudgeVerdict(groundedness=score, completeness=score, tone=score, rationale="because")


def test_a_judge_agreeing_with_every_label_scores_one():
    samples = (_sample("a", True), _sample("b", False))
    report = calibrate(samples, lambda s: _verdict(s.human_pass))
    assert report.agreement_rate == 1.0
    assert meets_floor(report)


def test_disagreements_are_named():
    samples = (_sample("a", True), _sample("b", False))
    report = calibrate(samples, lambda s: _verdict(True))
    assert report.agreements == 1
    assert report.disagreements == (("b", True, False),)


def test_a_judge_below_the_floor_is_not_fit_to_grade():
    """A run whose judge failed calibration fails as a whole rather than
    reporting scores it cannot stand behind."""
    samples = tuple(_sample(f"c{i}", True) for i in range(10))
    report = calibrate(samples, lambda s: _verdict(s.case_id in ("c0", "c1", "c2", "c3", "c4")))
    assert report.agreement_rate == 0.5
    assert not meets_floor(report)


def test_an_unreadable_verdict_does_not_abort_calibration():
    """Found by deliberately starving the source during the M03 teeth check.

    The judge's own reply degraded — it dropped the `tone` axis — and the
    `JudgeError` escaped `calibrate`, killing `make eval` with a traceback
    before a single golden case was graded. The operator got a stack trace
    instead of a report.
    """
    samples = (
        CalibrationSample(case_id="a", answer="x", human_pass=True, note="n"),
        CalibrationSample(case_id="b", answer="y", human_pass=True, note="n"),
    )

    def score(sample):
        if sample.case_id == "a":
            raise JudgeError("judge reply does not match the verdict schema:\nmissing tone")
        return JudgeVerdict(groundedness=5, completeness=5, tone=5, rationale="fine")

    report = calibrate(samples, score)

    assert report.samples == 2
    assert report.agreements == 1
    assert [case_id for case_id, _ in report.unparseable] == ["a"]
    # Counted as non-agreement, not skipped: a judge that cannot produce a
    # verdict has not agreed with anyone, and skipping would raise the rate.
    assert report.agreement_rate == 0.5
    assert "unreadable verdict on a" in render_calibration(report)


def test_enough_unreadable_verdicts_close_the_gate():
    # The fail-closed half. Garbage from the judge must sink the run rather
    # than shrink the denominator until the survivors look like agreement.
    samples = tuple(
        CalibrationSample(case_id=f"c{i}", answer="x", human_pass=True, note="n") for i in range(5)
    )

    def score(sample):
        raise JudgeError("unreadable")

    report = calibrate(samples, score)
    assert report.agreement_rate == 0.0
    assert not meets_floor(report)


def test_an_empty_calibration_set_never_meets_the_floor():
    """Zero samples is not 100% agreement; it is no evidence at all."""
    assert not meets_floor(calibrate((), lambda s: _verdict(True)))
