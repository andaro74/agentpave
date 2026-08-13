"""The shadow comparator, and the caller wrapper that feeds it.

Two properties carry this module, and both are invisible in the output when
they break — which is why they are asserted rather than reviewed:

1. The judge is identical on both arms. A shadow run that moved the judge would
   print deltas that measure the grader.
2. A regressed case sinks the verdict even when the mean improved. That is the
   whole difference between this and `baseline.diff`.
"""

from __future__ import annotations

from typing import Any

import pytest
from agentpave_evalsvc.judge import JUDGE_FEATURE
from agentpave_evalsvc.models import CaseResult, JudgeVerdict, Scorecard
from agentpave_evalsvc.shadow import candidate_caller, compare, observing_caller, render
from agentpave_gateway.routing import SHADOW_CANDIDATE_FEATURE

HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET = "us.anthropic.claude-sonnet-4-6"


_VERDICT = JudgeVerdict(groundedness=5, completeness=5, tone=5, rationale="fine")


def _case(
    case_id: str,
    *,
    capability: str = "airing",
    passed: bool = True,
    cost: float = 0.01,
    judged: bool = False,
):
    return CaseResult(
        case_id=case_id,
        capability=capability,  # type: ignore[arg-type]
        passed=passed,
        latency_ms=100,
        cost_usd=cost,
        # Defaults to unjudged so both arms of every other test carry the same
        # count and the judging note stays out of their rendered output.
        verdict=_VERDICT if judged else None,
    )


def _card(run_id: str, cases, *, model_serve: str = HAIKU, model_judge: str = SONNET) -> Scorecard:
    return Scorecard(
        run_id=run_id,
        created_at="2026-08-12T00:00:00+00:00",
        model_serve=model_serve,
        model_judge=model_judge,
        cases=tuple(cases),
    )


# ── the caller wrapper ────────────────────────────────────────────────────


class _Recorder:
    """A fake `Caller` that records what it was asked for."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        self.calls.append(kwargs)
        return 200, {"completion": "ok", "model_id": "whatever", "usage": {}}


def test_serving_calls_are_rerouted_to_the_candidate_feature() -> None:
    recorder = _Recorder()
    wrapped = candidate_caller(recorder)

    wrapped(feature_id="airing", prompt="q", system="serve")

    assert recorder.calls[0]["feature_id"] == SHADOW_CANDIDATE_FEATURE


def test_the_judge_feature_is_never_rewritten() -> None:
    """If this fails, both arms are graded by different models and every delta
    the report prints is measuring the judge instead of the candidate."""
    recorder = _Recorder()
    wrapped = candidate_caller(recorder, system="a different serving prompt")

    wrapped(feature_id=JUDGE_FEATURE, prompt="grade this", system="THE RUBRIC")

    assert recorder.calls[0]["feature_id"] == JUDGE_FEATURE
    assert recorder.calls[0]["system"] == "THE RUBRIC", "the judge's rubric was substituted"


def test_the_serving_prompt_is_substituted() -> None:
    recorder = _Recorder()
    wrapped = candidate_caller(recorder, system="be more concise")

    wrapped(feature_id="summarize", prompt="q", system="the incumbent prompt")

    assert recorder.calls[0]["system"] == "be more concise"


def test_a_prompt_only_candidate_leaves_the_feature_alone() -> None:
    # Varying the prompt without varying the model is a legitimate shadow run,
    # and the one that reproduces M05's Act 2 change.
    recorder = _Recorder()
    wrapped = candidate_caller(recorder, feature_id=None, system="be more concise")

    wrapped(feature_id="airing", prompt="q", system="incumbent")

    assert recorder.calls[0]["feature_id"] == "airing"
    assert recorder.calls[0]["system"] == "be more concise"


def test_a_model_only_candidate_leaves_the_prompt_alone() -> None:
    recorder = _Recorder()
    wrapped = candidate_caller(recorder)

    wrapped(feature_id="airing", prompt="q", system="incumbent")

    assert recorder.calls[0]["system"] == "incumbent"


def test_every_other_argument_is_passed_through() -> None:
    # A wrapper that dropped `temperature` would un-pin the eval's temperature
    # and reintroduce the resample-vs-regression ambiguity ADR-016 closed.
    recorder = _Recorder()
    wrapped = candidate_caller(recorder)

    wrapped(
        feature_id="airing",
        prompt="q",
        system="s",
        classification="internal",
        max_tokens=1024,
        temperature=0.0,
    )

    call = recorder.calls[0]
    assert call["prompt"] == "q"
    assert call["max_tokens"] == 1024
    assert call["temperature"] == 0.0
    assert call["classification"] == "internal"


# ── the comparison ────────────────────────────────────────────────────────


def test_deltas_are_computed_against_the_incumbent() -> None:
    incumbent = _card("run-a", [_case("one"), _case("two", passed=False)])
    candidate = _card("run-b", [_case("one"), _case("two")], model_serve=SONNET)

    report = compare(incumbent, candidate)

    assert report.pass_rate_delta == pytest.approx(0.5)
    assert report.incumbent_model_serve == HAIKU
    assert report.candidate_model_serve == SONNET


def test_cost_delta_is_reported_signed() -> None:
    incumbent = _card("run-a", [_case("one", cost=0.01)])
    candidate = _card("run-b", [_case("one", cost=0.04)])

    assert compare(incumbent, candidate).cost_delta_usd == pytest.approx(0.03)


def test_improvements_and_regressions_are_named_per_case() -> None:
    incumbent = _card("run-a", [_case("kept"), _case("fixed", passed=False), _case("broken")])
    candidate = _card("run-b", [_case("kept"), _case("fixed"), _case("broken", passed=False)])

    report = compare(incumbent, candidate)

    assert report.improvements == ("fixed",)
    assert report.regressions == ("broken",)


def test_a_regression_sinks_the_verdict_even_when_the_mean_improves() -> None:
    """The reason this module is not `baseline.diff`.

    Two cases fixed, one broken: the pass rate rises and every aggregate looks
    like a win. One user who used to get an answer now does not. A verdict that
    averaged that away is how a platform ships a regression while its own
    dashboard turns green.
    """
    incumbent = _card(
        "run-a",
        [_case("a", passed=False), _case("b", passed=False), _case("c"), _case("d")],
    )
    candidate = _card("run-b", [_case("a"), _case("b"), _case("c"), _case("d", passed=False)])

    report = compare(incumbent, candidate)

    assert report.pass_rate_delta > 0, "the mean did improve — that is the trap"
    assert report.regressions == ("d",)
    assert report.shippable is False


def test_a_clean_improvement_is_shippable() -> None:
    incumbent = _card("run-a", [_case("a", passed=False), _case("b")])
    candidate = _card("run-b", [_case("a"), _case("b")])

    assert compare(incumbent, candidate).shippable is True


def test_an_identical_pair_is_shippable_and_says_nothing_moved() -> None:
    incumbent = _card("run-a", [_case("a"), _case("b")])
    candidate = _card("run-b", [_case("a"), _case("b")])

    report = compare(incumbent, candidate)

    assert report.shippable is True
    assert report.regressions == ()
    assert report.improvements == ()
    assert "no case changed outcome" in render(report)


def test_a_changed_judge_makes_the_pair_incomparable() -> None:
    incumbent = _card("run-a", [_case("a")], model_judge=SONNET)
    candidate = _card("run-b", [_case("a")], model_judge="some-other-judge")

    report = compare(incumbent, candidate)

    assert report.judge_changed is True
    assert report.comparable is False
    assert report.shippable is False


def test_a_case_set_mismatch_makes_the_pair_incomparable() -> None:
    """Both arms run the same dataset in the same process, so a mismatch means
    an arm failed to run cases — and comparing pass rates across two different
    case sets is arithmetic on two different questions."""
    incumbent = _card("run-a", [_case("a"), _case("b")])
    candidate = _card("run-b", [_case("a")])

    report = compare(incumbent, candidate)

    assert report.only_incumbent == ("b",)
    assert report.comparable is False
    assert report.shippable is False


def test_capability_deltas_cover_only_shared_capabilities() -> None:
    incumbent = _card("run-a", [_case("a", capability="airing"), _case("b", capability="running")])
    candidate = _card(
        "run-b",
        [_case("a", capability="airing", passed=False), _case("b", capability="running")],
    )

    report = compare(incumbent, candidate)

    assert report.by_capability_delta["airing"] == pytest.approx(-1.0)
    assert report.by_capability_delta["running"] == pytest.approx(0.0)


# ── the rendering ─────────────────────────────────────────────────────────


def test_the_report_names_both_serving_models() -> None:
    # A shadow report whose two models are unknown is a table of numbers with
    # no subject.
    incumbent = _card("run-a", [_case("a")])
    candidate = _card("run-b", [_case("a")], model_serve=SONNET)

    rendered = render(compare(incumbent, candidate))

    assert HAIKU in rendered
    assert SONNET in rendered


def test_a_regression_is_shouted_not_buried() -> None:
    incumbent = _card("run-a", [_case("a")])
    candidate = _card("run-b", [_case("a", passed=False)])

    rendered = render(compare(incumbent, candidate))

    assert "REGRESSED" in rendered
    assert "a" in rendered
    assert "❌" in rendered


def test_an_incomparable_pair_disowns_its_own_numbers() -> None:
    """A reader who skips to the verdict must not carry the deltas away as a
    finding, so the incomparability is stated rather than implied by absence."""
    incumbent = _card("run-a", [_case("a")], model_judge=SONNET)
    candidate = _card("run-b", [_case("a")], model_judge="other")

    rendered = render(compare(incumbent, candidate))

    assert "not comparable" in rendered
    assert "✅" not in rendered


# ── the failure the first deployed run actually hit ────────────────────────


def test_two_arms_on_one_model_is_not_comparable() -> None:
    """The first deployed shadow run's exact shape.

    Both arms served by Haiku, every case tied, and the report said "no case
    regressed — safe to adopt". Not merely wrong: *reassuring*, which is what a
    comparison of a run against itself always produces.
    """
    incumbent = _card("run-a", [_case("a"), _case("b")], model_serve=HAIKU)
    candidate = _card("run-b", [_case("a"), _case("b")], model_serve=HAIKU)

    report = compare(incumbent, candidate, expect_model_change=True)

    assert report.served_identically is True
    assert report.comparable is False
    assert report.shippable is False, "a run compared against itself reported adoptable"


def test_identical_models_are_expected_when_only_the_prompt_varies() -> None:
    # A prompt-only candidate serves on the incumbent's model by design, so the
    # same check must not fire and condemn a legitimate run.
    incumbent = _card("run-a", [_case("a")], model_serve=HAIKU)
    candidate = _card("run-b", [_case("a")], model_serve=HAIKU)

    report = compare(incumbent, candidate, prompt_changed=True, expect_model_change=False)

    assert report.served_identically is False
    assert report.comparable is True


def test_the_same_model_report_names_the_cause_and_the_fix() -> None:
    """The reader is a person who just spent a dollar and got a green tick.

    Naming the model is not enough — the cause is that the routing table is
    deployed code, which is not something the output would otherwise suggest.
    """
    incumbent = _card("run-a", [_case("a")], model_serve=HAIKU)
    candidate = _card("run-b", [_case("a")], model_serve=HAIKU)

    rendered = render(compare(incumbent, candidate, expect_model_change=True))

    assert "compared the incumbent to itself" in rendered
    assert "deploy-dev" in rendered
    assert "✅" not in rendered


def test_a_genuine_model_change_passes_the_check() -> None:
    incumbent = _card("run-a", [_case("a")], model_serve=HAIKU)
    candidate = _card("run-b", [_case("a")], model_serve=SONNET)

    report = compare(incumbent, candidate, expect_model_change=True)

    assert report.served_identically is False
    assert report.shippable is True


# ── observing which model actually served ─────────────────────────────────


def test_the_served_model_is_recorded_from_the_response() -> None:
    """Configuration is not evidence.

    The header used to print a model name derived from a stack output and a
    boolean — what *should* have happened. This reads what did.
    """
    seen: set[str] = set()

    def call(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        return 200, {"completion": "ok", "model_id": HAIKU}

    observing_caller(call, seen)(feature_id="airing", prompt="q")

    assert seen == {HAIKU}


def test_the_judges_model_is_not_recorded_as_a_serving_model() -> None:
    # Folding the judge in would make both arms look like they served on two
    # models each, and the same-model check would never fire.
    seen: set[str] = set()
    wrapped = observing_caller(lambda **kw: (200, {"model_id": SONNET}), seen)

    wrapped(feature_id=JUDGE_FEATURE, prompt="grade")

    assert seen == set()


def test_a_refusal_contributes_no_served_model() -> None:
    seen: set[str] = set()
    wrapped = observing_caller(lambda **kw: (403, {"refused": True}), seen)

    wrapped(feature_id="airing", prompt="q")

    assert seen == set()


def test_the_observer_returns_the_response_untouched() -> None:
    seen: set[str] = set()
    wrapped = observing_caller(lambda **kw: (200, {"completion": "hello", "model_id": HAIKU}), seen)

    status, body = wrapped(feature_id="airing", prompt="q")

    assert status == 200
    assert body["completion"] == "hello"


def test_observation_composes_with_the_candidate_wrapper() -> None:
    """The candidate arm wraps both, and the outer observer must still see the
    original feature id so it can tell a judge call from a serving call."""
    seen: set[str] = set()
    recorder = _Recorder()
    wrapped = observing_caller(candidate_caller(recorder), seen)

    wrapped(feature_id="airing", prompt="q")
    wrapped(feature_id=JUDGE_FEATURE, prompt="grade")

    assert recorder.calls[0]["feature_id"] == SHADOW_CANDIDATE_FEATURE
    assert recorder.calls[1]["feature_id"] == JUDGE_FEATURE
    assert seen == {"whatever"}, "only the serving call contributed a model"


def test_a_prompt_change_is_declared_in_the_report() -> None:
    incumbent = _card("run-a", [_case("a")])
    candidate = _card("run-b", [_case("a")])

    rendered = render(compare(incumbent, candidate, prompt_changed=True))

    assert "different serving prompt" in rendered


# ── the cost delta is not independent of the outcomes ──────────────────────
#
# A failing case skips its judge call, so a worse arm is a cheaper arm. On the
# first comparable deployed run the six regressions saved more judging than the
# candidate's extra serving cost, and the report printed `cost -0.010189 USD`
# with nothing to say the saving *was* the failures.


def test_judge_verdicts_are_counted_per_arm() -> None:
    incumbent = _card("run-a", [_case("a", judged=True), _case("b", judged=True)])
    candidate = _card("run-b", [_case("a", judged=True), _case("b", passed=False)])

    report = compare(incumbent, candidate)

    assert report.incumbent_judged == 2
    assert report.candidate_judged == 1
    assert not report.judged_evenly


def test_uneven_judging_qualifies_the_cost_line() -> None:
    """The confound is named where the misreading happens.

    A reader who takes `cost -0.01 USD` from a report with six regressions has
    read a symptom of the failures as a benefit of the candidate.
    """
    incumbent = _card("run-a", [_case("a", judged=True, cost=0.05)])
    candidate = _card("run-b", [_case("a", passed=False, cost=0.01)])

    rendered = render(compare(incumbent, candidate))

    assert "judge verdicts: incumbent 1, candidate 0" in rendered
    assert "not charged for the same work" in rendered


def test_even_judging_says_nothing() -> None:
    """No note when there is nothing to qualify — a caveat that always prints
    is a caveat nobody reads."""
    incumbent = _card("run-a", [_case("a", judged=True)])
    candidate = _card("run-b", [_case("a", judged=True)])

    rendered = render(compare(incumbent, candidate))

    assert "judge verdicts" not in rendered


def test_the_note_is_symmetric_about_which_arm_judged_less() -> None:
    """An incumbent that skipped judge calls is the same confound reversed, and
    a report that only warned in one direction would flatter the candidate."""
    incumbent = _card("run-a", [_case("a", passed=False)])
    candidate = _card("run-b", [_case("a", judged=True)])

    rendered = render(compare(incumbent, candidate))

    assert "judge verdicts: incumbent 0, candidate 1" in rendered


def test_uneven_judging_does_not_make_the_pair_incomparable() -> None:
    """Uneven judging is caused by the pass rates, not a threat to them.

    Folding it into `comparable` would suppress the deltas — and the
    regressions that produced the imbalance are exactly what the reader came
    for. It qualifies the cost line and nothing else.
    """
    incumbent = _card("run-a", [_case("a", judged=True), _case("b", judged=True)])
    candidate = _card("run-b", [_case("a", judged=True), _case("b", passed=False)])

    report = compare(incumbent, candidate)

    assert report.comparable
    assert not report.shippable, "the regression still sinks it"
