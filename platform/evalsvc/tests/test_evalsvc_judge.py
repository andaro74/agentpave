"""The judge: routing, parsing, threshold, and the prompt lint.

The load-bearing test in this file is `test_judge_feature_routes_to_the_capable_model`.
Without it, the judge would run on the serving model and the whole suite would
grade itself with a peer — green, and meaningless.
"""

from __future__ import annotations

import json

import pytest
from agentpave_evalsvc.judge import (
    JUDGE_FEATURE,
    PASS_THRESHOLD,
    JudgeError,
    build_judge_content,
    lint_prompt,
    parse_verdict,
    verdict_passes,
)
from agentpave_evalsvc.models import GoldenCase, JudgeVerdict
from agentpave_gateway.routing import RoutingTable


def _case() -> GoldenCase:
    return GoldenCase.model_validate(
        {
            "case_id": "a-case",
            "capability": "airing",
            "grading": "judged",
            "prompt": "What network airs it?",
            "fixture": "f.json",
            "budget": {"latency_ms": 1000, "cost_usd": 0.01},
        }
    )


def _verdict(**overrides) -> JudgeVerdict:
    base = {"groundedness": 5, "completeness": 5, "tone": 5, "rationale": "fine"}
    return JudgeVerdict.model_validate({**base, **overrides})


# ── routing ───────────────────────────────────────────────────────────────


def test_judge_feature_routes_to_the_capable_model():
    """The judge must not be graded by a peer of the thing it grades.

    The gateway's routing rule 2 defaults *open* to the fast model, so an
    unlisted `judge` feature would route to Haiku and the entire suite would
    go green while measuring nothing. Nothing in a scorecard reveals that.
    """
    table = RoutingTable(model_fast="fast-model", model_capable="capable-model")
    decision = table.route(JUDGE_FEATURE, "internal")
    assert decision.model_id == "capable-model"


def test_judge_feature_is_still_refused_for_sensitive_data():
    """Classification is checked before feature routing and fails closed."""
    table = RoutingTable(model_fast="fast-model", model_capable="capable-model")
    assert table.route(JUDGE_FEATURE, "sensitive").model_id is None


# ── prompt assembly ───────────────────────────────────────────────────────


def test_judge_prompt_carries_source_question_and_answer():
    """Groundedness is unanswerable without the source: a judge that cannot
    see it can only rate whether the answer sounds right."""
    prompt = build_judge_content(_case(), "THE SOURCE DATA", "THE ANSWER")
    assert "THE SOURCE DATA" in prompt
    assert "What network airs it?" in prompt
    assert "THE ANSWER" in prompt


def test_judge_prompt_truncates_a_huge_source():
    prompt = build_judge_content(_case(), "x" * 50_000, "answer")
    assert len(prompt) < 20_000


# ── verdict parsing ───────────────────────────────────────────────────────


def test_parses_a_bare_json_object():
    verdict = parse_verdict('{"groundedness": 4, "completeness": 5, "tone": 3, "rationale": "ok"}')
    assert verdict.groundedness == 4


def test_parses_a_fenced_object():
    reply = '```json\n{"groundedness": 4, "completeness": 4, "tone": 4, "rationale": "ok"}\n```'
    assert parse_verdict(reply).tone == 4


def test_parses_an_object_with_a_preamble_sentence():
    reply = (
        "Here is my assessment: "
        '{"groundedness": 4, "completeness": 4, "tone": 4, "rationale": "ok"}'
    )
    assert parse_verdict(reply).completeness == 4


def test_prose_reply_raises_rather_than_defaulting():
    """An unparseable judge reply is an error, never a default score — the M02
    defect where an unreadable result was read as success."""
    with pytest.raises(JudgeError, match="no JSON object"):
        parse_verdict("The answer looks pretty good to me.")


def test_out_of_range_score_raises():
    with pytest.raises(JudgeError, match="verdict schema"):
        parse_verdict('{"groundedness": 9, "completeness": 4, "tone": 4, "rationale": "ok"}')


def test_missing_axis_raises():
    with pytest.raises(JudgeError, match="verdict schema"):
        parse_verdict('{"groundedness": 4, "tone": 4, "rationale": "ok"}')


def test_malformed_json_raises():
    with pytest.raises(JudgeError, match="not valid JSON"):
        parse_verdict('{"groundedness": 4,,}')


# ── threshold ─────────────────────────────────────────────────────────────


def test_all_axes_at_threshold_passes():
    assert verdict_passes(
        _verdict(groundedness=PASS_THRESHOLD, completeness=PASS_THRESHOLD, tone=PASS_THRESHOLD)
    )


def test_one_axis_below_threshold_fails():
    assert not verdict_passes(_verdict(groundedness=PASS_THRESHOLD - 1))


def test_high_tone_cannot_rescue_low_groundedness():
    """Not a mean. Averaging would let a 5 for tone buy a pass for an answer
    that invented a fact — inverting what this gate is for."""
    assert not verdict_passes(_verdict(groundedness=1, completeness=5, tone=5))


# ── prompt lint (the hermetic gate's "judge prompts lint") ────────────────


def test_shipped_prompt_lints_clean():
    assert lint_prompt() == []


@pytest.mark.parametrize("axis", ["groundedness", "completeness", "tone"])
def test_lint_catches_a_dropped_axis(axis):
    """A prompt missing an axis keeps running and quietly stops grading it."""
    stripped = json.dumps({"note": "graded"})  # nothing resembling the real prompt
    problems = lint_prompt(stripped)
    assert any(axis in p for p in problems)


def test_lint_catches_a_prompt_that_decides_pass_fail():
    """Two thresholds would exist, and only one of them has a test."""
    problems = lint_prompt(
        "Score groundedness, completeness and tone from 1 to 5 with a rationale "
        "in JSON, then say whether it should pass."
    )
    assert any("threshold belongs in" in p for p in problems)


def test_lint_catches_a_dropped_json_contract():
    problems = lint_prompt(
        "Rate groundedness, completeness and tone from 1 to 5 and give a rationale."
    )
    assert any("JSON" in p for p in problems)
