"""Baseline diffing and calibration — the two pieces that decide whether a
run's numbers can be believed.

The diff tests concentrate on the appeared/disappeared distinction, because
that is where a quality regression and a dataset defect are easiest to confuse.
"""

from __future__ import annotations

from agentpave_evalsvc.baseline import diff, is_recordable, render
from agentpave_evalsvc.calibration import calibrate, meets_floor
from agentpave_evalsvc.calibration import render as render_calibration
from agentpave_evalsvc.judge import JudgeError
from agentpave_evalsvc.models import (
    UNRECORDED_MODEL,
    Baseline,
    CalibrationSample,
    CaseResult,
    JudgeVerdict,
    ProbeResult,
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


def _baseline(
    by_capability: dict[str, float],
    pass_rate: float,
    cost: float = 0.01,
    model_serve: str = "serve",
    model_judge: str = "judge",
) -> Baseline:
    # Defaults match `_card`, so a test that says nothing about models is
    # testing a same-models comparison — which is what every diff test below
    # means, and what they would silently stop meaning otherwise.
    return Baseline(
        run_id="run-1",
        created_at="2026-08-07T00:00:00+00:00",
        pass_rate=pass_rate,
        total_cost_usd=cost,
        by_capability=by_capability,
        model_serve=model_serve,
        model_judge=model_judge,
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
    # The models travel with the numbers. The gateway stack publishes both ids
    # because "a score change and a model change look identical after the
    # fact"; the scorecard recorded them and the baseline used to drop them, so
    # the store contradicted its own stated reason for existing.
    assert baseline.model_serve == "serve"
    assert baseline.model_judge == "judge"


def test_a_changed_model_is_reported_and_is_not_itself_a_regression():
    """Swapping a model is a fact about the comparison, not a verdict on it.

    Treating it as a regression would block every deliberate model upgrade;
    ignoring it lets a pass-rate drop be read as a quality regression when a
    different model answered. Both are wrong, so it is reported separately.
    """
    card = _card([("a", "airing", True)])
    result = diff(card, _baseline({"airing": 1.0}, 1.0, model_serve="an-older-haiku"))
    assert result.model_changes == (("serving", "an-older-haiku", "serve"),)
    assert not result.regressed


def test_render_warns_that_a_diff_across_a_model_swap_compares_two_systems():
    card = _card([("a", "airing", True), ("b", "airing", False)])
    rendered = render(diff(card, _baseline({"airing": 1.0}, 1.0, model_judge="an-older-sonnet")))
    assert "judge model changed" in rendered
    assert "two different systems" in rendered
    # The regression verdict still stands on the numbers; the warning tells a
    # reader how much the numbers are worth, it does not suppress them.
    assert "REGRESSED" in rendered


def test_a_baseline_recorded_before_models_were_stored_still_loads():
    """Every row written before M05 lacks these attributes.

    A fix that made the existing history unreadable would be unshippable, and
    the failure would arrive as a crash in the gate rather than as a missing
    field. `unrecorded` is the honest value for a row that never knew.
    """
    legacy = Baseline(
        run_id="run-0",
        created_at="2026-08-07T00:00:00+00:00",
        pass_rate=1.0,
        total_cost_usd=0.01,
        by_capability={"airing": 1.0},
    )
    assert legacy.model_serve == UNRECORDED_MODEL
    result = diff(_card([("a", "airing", True)]), legacy)
    assert ("serving", UNRECORDED_MODEL, "serve") in result.model_changes


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


# ── what may become a baseline ────────────────────────────────────────────


def test_only_a_passing_run_may_become_the_baseline():
    """A baseline is the standard to beat, so a failing run must not set it.

    M03's teeth demonstration broke the service deliberately, scored 19/30,
    and `--save-baseline` wrote it down. The next run then "improved" on a
    regression nobody caused, and the score history carries a dip that never
    happened.
    """
    failing = _card([("a", "airing", True), ("b", "airing", False)])
    passing = _card([("a", "airing", True), ("b", "airing", True)])

    assert not is_recordable(failing)
    assert is_recordable(passing)


def test_a_failed_probe_also_blocks_the_baseline():
    """`Scorecard.passed` covers probes as well as cases, so a guardrail
    failure keeps a run out of the history exactly like a quality failure."""
    card = Scorecard(
        run_id="run-3",
        created_at="2026-08-09T00:00:00+00:00",
        model_serve="serve",
        model_judge="judge",
        cases=(
            CaseResult(case_id="a", capability="airing", passed=True, latency_ms=1, cost_usd=0.0),
        ),
        probes=(ProbeResult(probe_id="p", outcome="model_complied", passed=False, detail="d"),),
    )
    assert not is_recordable(card)
