"""The run loop, driven by stub callers.

Every test here uses a fake `Caller`, which is the point: the harness is pure
over that boundary, so the hermetic gate runs the same loop `make eval` runs.

The cluster that matters most is the failure handling. M02's deployed gate
reported three tests as PASSED against a wall because transport failure was
folded into a result; these tests are what stop that recurring.
"""

from __future__ import annotations

import json
from typing import Any

from agentpave_evalsvc.adversarial import INJECTION_MARKER
from agentpave_evalsvc.dataset import load_dataset
from agentpave_evalsvc.harness import (
    SERVE_SYSTEM,
    build_case_content,
    plan,
    run_case,
    run_probe,
)
from agentpave_evalsvc.judge import JUDGE_SYSTEM
from agentpave_evalsvc.models import AdversarialProbe, GoldenCase

GOOD_VERDICT = json.dumps(
    {"groundedness": 5, "completeness": 5, "tone": 5, "rationale": "grounded and complete"}
)
BAD_VERDICT = json.dumps(
    {"groundedness": 1, "completeness": 5, "tone": 5, "rationale": "invents a network"}
)


def _case(**overrides) -> GoldenCase:
    base = {
        "case_id": "a-case",
        "capability": "airing",
        "grading": "judged",
        "prompt": "What network airs it?",
        "fixture": "f.json",
        "budget": {"latency_ms": 60000, "cost_usd": 0.5},
    }
    return GoldenCase.model_validate({**base, **overrides})


def _completion(text: str, cost: float = 0.0001) -> tuple[int, dict[str, Any]]:
    return 200, {
        "refused": False,
        "completion": text,
        "model_id": "a-model",
        "usage": {"input_tokens": 10, "output_tokens": 20, "cost_usd": cost},
    }


def _caller(answer: str, verdict: str = GOOD_VERDICT, calls: list | None = None):
    """A stub gateway that answers serving turns and judge turns differently."""

    def call(*, feature_id: str, prompt: str, classification: str = "internal", **_):
        if calls is not None:
            calls.append(feature_id)
        return _completion(verdict if feature_id == "judge" else answer)

    return call


# ── the happy path ────────────────────────────────────────────────────────


def test_a_good_answer_passes():
    result = run_case(_case(must_contain=("Apple TV",)), _caller("It streams on Apple TV."), "src")
    assert result.passed
    assert result.verdict is not None


def test_serving_and_judge_costs_are_summed():
    """A scorecard that reports only the serving cost understates what a run
    spent by roughly half."""
    result = run_case(_case(), _caller("an answer"), "src")
    assert result.cost_usd == 0.0002


# ── failure handling ──────────────────────────────────────────────────────


def test_a_deterministic_failure_skips_the_judge():
    """The verdict cannot change the outcome, and Sonnet is not free."""
    calls: list[str] = []
    result = run_case(
        _case(must_contain=("Apple TV",)), _caller("It airs on AMC.", calls=calls), "src"
    )
    assert not result.passed
    assert "judge" not in calls


def test_a_bad_verdict_fails_the_case_and_is_explained():
    result = run_case(_case(), _caller("an answer", verdict=BAD_VERDICT), "src")
    assert not result.passed
    assert any("groundedness=1" in f for f in result.assert_failures)


def test_a_raising_caller_produces_a_failed_case_not_an_exception():
    """A transport failure is a failed case carrying the reason — never a
    skipped one, and never an exception that aborts the run."""

    def call(**_):
        raise ConnectionError("connection reset")

    result = run_case(_case(), call, "src")
    assert not result.passed
    assert result.error is not None
    assert "ConnectionError" in result.error


def test_a_403_produces_a_failed_case_with_the_status():
    """M02: an unsigned request got 403 on everything, and the suite read it
    as the calls failing as expected."""

    def call(**_):
        return 403, {"message": "Forbidden"}

    result = run_case(_case(), call, "src")
    assert not result.passed
    assert "403" in (result.error or "")


def test_a_refusal_on_a_golden_case_is_a_failure():
    """Golden cases are things the platform should answer. The adversarial
    suite is where a refusal is the pass."""

    def call(**_):
        return 200, {"refused": True, "stage": "guardrail", "reason": "blocked"}

    result = run_case(_case(), call, "src")
    assert not result.passed
    assert "guardrail" in (result.error or "")


def test_a_refusal_names_the_filter_that_fired():
    """A failure report that stops at "blocked" costs a redeploy to diagnose.

    M03's first deployed eval run died on a guardrail intervention whose only
    description was `stage: guardrail`. A prompt-attack block and a PII block
    have nothing in common except that word, so the message has to carry which
    control fired for the failure to be actionable.
    """

    def call(**_):
        return 200, {
            "refused": True,
            "stage": "guardrail",
            "reason": "blocked",
            "blocked_by": ["contentPolicy:PROMPT_ATTACK"],
        }

    error = run_case(_case(), call, "src").error or ""
    assert "contentPolicy:PROMPT_ATTACK" in error


def test_a_refusal_without_a_filter_list_still_reports_cleanly():
    # An older gateway, or a block Bedrock returned no assessment for. The
    # message degrades rather than growing an empty bracket nobody can read.
    def call(**_):
        return 200, {"refused": True, "stage": "guardrail", "reason": "blocked"}

    error = run_case(_case(), call, "src").error or ""
    assert error.endswith("blocked")


def test_an_empty_completion_is_a_failure():
    def call(**_):
        return _completion("   ")

    assert not run_case(_case(), call, "src").passed


def test_an_unparseable_verdict_fails_the_case():
    result = run_case(_case(), _caller("an answer", verdict="looks good to me"), "src")
    assert not result.passed
    assert any("judge reply unusable" in f for f in result.assert_failures)


def test_a_judge_that_errors_fails_the_case():
    """A case that could not be judged has not been graded, so it cannot pass."""

    def call(*, feature_id: str, **_):
        if feature_id == "judge":
            return 500, {"message": "internal error"}
        return _completion("an answer")

    result = run_case(_case(), call, "src")
    assert not result.passed
    assert any("judge unavailable" in f for f in result.assert_failures)


# ── prompts and plan ──────────────────────────────────────────────────────


def test_serving_instructions_forbid_answering_from_memory():
    """Without this instruction the dataset's hallucination bait tests nothing."""
    assert "only" in SERVE_SYSTEM.lower()
    assert "memory" in SERVE_SYSTEM.lower()


def test_the_guarded_span_carries_the_data_and_none_of_the_instructions():
    """ADR-013's contract, on the eval service's side of it.

    `system` bypasses the guardrail's prompt-attack filter. That is what makes
    the judge callable at all, and it is also a hole: anything a caller puts
    there is never inspected. The eval service's rule is that only these two
    constants — text that lives in code and never varies with input — go into
    `system`, and every byte derived from a fixture, a case, or a model's
    output stays in the guarded span.

    In M04 the fixture becomes real tool output and this stops being a
    formality.
    """
    content = build_case_content(_case(), "SENTINEL-FIXTURE-BODY")
    assert "SENTINEL-FIXTURE-BODY" in content
    assert "What network airs it?" in content
    # None of the instruction text leaked into the guarded span...
    assert "catalogue assistant" not in content.lower()
    assert "memory" not in content.lower()
    # ...and no fixture-derived byte leaked out of it. `SERVE_SYSTEM` names the
    # CATALOGUE DATA section, which is a label, not the data.
    assert "SENTINEL-FIXTURE-BODY" not in SERVE_SYSTEM


def test_a_serving_turn_sends_instructions_as_system_not_as_prompt():
    seen: dict[str, Any] = {}

    def call(*, feature_id: str, prompt: str, system: str | None = None, **_):
        seen[feature_id] = {"prompt": prompt, "system": system}
        return _completion(GOOD_VERDICT if feature_id == "judge" else "Apple TV")

    run_case(_case(), call, "CATALOGUE DATA HERE")

    serving = seen["airing"]
    assert serving["system"] == SERVE_SYSTEM
    assert "CATALOGUE DATA HERE" in serving["prompt"]
    assert "catalogue assistant" not in serving["prompt"].lower()

    judging = seen["judge"]
    assert judging["system"] == JUDGE_SYSTEM
    # The answer under evaluation is untrusted: a model wrote it.
    assert "Apple TV" in judging["prompt"]
    assert "groundedness" not in judging["prompt"]


def test_an_injected_probe_keeps_its_payload_inside_the_guarded_span():
    """A probe whose attack travelled in `system` would prove nothing.

    It would be testing the one path the guardrail does not inspect, and would
    report `model_complied` or a pass for reasons unrelated to the attack.
    """
    seen: dict[str, Any] = {}

    def call(*, feature_id: str, prompt: str, system: str | None = None, **_):
        seen.update({"prompt": prompt, "system": system})
        return 200, {"refused": True, "stage": "guardrail", "reason": "blocked"}

    probe = AdversarialProbe(
        probe_id="p",
        why="w",
        prompt="summarize this",
        inject_into_fixture="search_shows__q-severance.json",
    )
    assert run_probe(probe, call).passed
    assert seen["system"] == SERVE_SYSTEM
    assert INJECTION_MARKER in seen["prompt"]
    assert INJECTION_MARKER not in (seen["system"] or "")


def test_plan_describes_the_shipped_dataset_without_calling_anything():
    rendered = plan(load_dataset())
    assert "30" in rendered
    assert "adversarial:   10" in rendered
    assert "dry run" in rendered


# ── probes ────────────────────────────────────────────────────────────────


def test_a_probe_that_cannot_be_sent_does_not_pass():
    def call(**_):
        raise TimeoutError("timed out")

    result = run_probe(AdversarialProbe(probe_id="p", why="w", prompt="attack"), call)
    assert not result.passed
    assert "nothing was proven" in result.detail


def test_a_probe_carries_its_classification_to_the_gateway():
    seen: dict[str, str] = {}

    def call(*, feature_id: str, prompt: str, classification: str = "internal", **_):
        seen["classification"] = classification
        return 200, {"refused": True, "stage": "classification", "reason": "refused"}

    probe = AdversarialProbe(probe_id="p", why="w", prompt="attack", classification="sensitive")
    assert run_probe(probe, call).passed
    assert seen["classification"] == "sensitive"
