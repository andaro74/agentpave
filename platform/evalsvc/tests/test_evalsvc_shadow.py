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
from agentpave_evalsvc.models import CaseResult, Scorecard
from agentpave_evalsvc.shadow import candidate_caller, compare, render
from agentpave_gateway.routing import SHADOW_CANDIDATE_FEATURE

HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET = "us.anthropic.claude-sonnet-4-6"


def _case(case_id: str, *, capability: str = "airing", passed: bool = True, cost: float = 0.01):
    return CaseResult(
        case_id=case_id,
        capability=capability,  # type: ignore[arg-type]
        passed=passed,
        latency_ms=100,
        cost_usd=cost,
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


def test_a_prompt_change_is_declared_in_the_report() -> None:
    incumbent = _card("run-a", [_case("a")])
    candidate = _card("run-b", [_case("a")])

    rendered = render(compare(incumbent, candidate, prompt_changed=True))

    assert "different serving prompt" in rendered
