"""The M04 deployed gate's verdict logic, graded hermetically.

`make walkthrough` decides whether M04 closes. Its judgements are code, and
code that decides a milestone has to be tested like any other — a gate that
passes for the wrong reason is worse than a gate that fails, because nobody
looks again.

Every function under test is pure over dictionaries. Nothing here touches AWS.
"""

from __future__ import annotations

from agentpave_infra.walkthrough import (
    GUARDED_QUESTION,
    QUESTION,
    SERVICE_ID,
    judge_answered,
    judge_guarded,
    judge_metered,
    judge_scaffolded,
    judge_traced,
    report,
)

SERVED_ROW = {
    "service_id": SERVICE_ID,
    "outcome": "served",
    "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "cost_usd": "0.000123",
    "price_basis": "list-2026-08",
    "input_tokens": 480,
    "output_tokens": 40,
}
BLOCKED_ROW = {"service_id": SERVICE_ID, "outcome": "blocked", "cost_usd": "0"}


# ── scaffolded ────────────────────────────────────────────────────────────


def test_a_service_without_its_own_stack_outputs_fails():
    result = judge_scaffolded({})
    assert not result.passed
    assert "ServiceUrl" in result.detail


def test_a_service_with_a_url_but_no_identity_fails():
    """A function with no role of its own has no identity for Cedar to
    authorize or for the permissions boundary to constrain."""
    assert not judge_scaffolded({"ServiceUrl": "https://x.lambda-url.invalid/"}).passed


def test_a_deployed_service_stack_passes():
    assert judge_scaffolded(
        {"ServiceUrl": "https://x.lambda-url.invalid/", "ServiceRoleArn": "arn:aws:iam::1:role/r"}
    ).passed


# ── answered ──────────────────────────────────────────────────────────────


def test_a_grounded_answer_passes():
    result = judge_answered(200, {"answer": "Severance airs on Apple TV+.", "tool": "search_show"})
    assert result.passed


def test_an_answer_that_never_called_the_tool_fails():
    """An answer with no tool call is an answer from training data, whatever it
    says. The act is about the path, not the sentence."""
    assert not judge_answered(200, {"answer": "Severance airs on Apple TV+."}).passed


def test_an_answer_that_invents_a_network_fails():
    """The half that matters more. Missing the fact is a bad answer; supplying
    a different one from memory is the failure this platform exists to expose,
    and it looks identical in a scorecard that only checks for the right
    string — this answer contains "Apple TV" too."""
    result = judge_answered(
        200,
        {
            "answer": "Severance airs on Apple TV in the US and on HBO elsewhere.",
            "tool": "search_show",
        },
    )
    assert not result.passed
    assert "HBO" in result.detail


def test_an_empty_answer_fails():
    assert not judge_answered(200, {"answer": "", "tool": "search_show"}).passed


def test_a_non_200_fails():
    assert not judge_answered(502, {"error": "tool unreachable"}).passed


# ── guarded ───────────────────────────────────────────────────────────────


def test_a_platform_refusal_passes():
    result = judge_guarded(
        403,
        {
            "refused": True,
            "stage": "guardrail",
            "reason": "blocked by the AgentPave gateway guardrail",
            "blocked_by": ["contentPolicy:PROMPT_ATTACK"],
        },
    )
    assert result.passed


def test_the_screen_is_also_a_platform_control():
    """ADR-014's input screen fires before any model is reached, which is what
    invariant 5 asks for — not the model declining to cooperate."""
    assert judge_guarded(
        403, {"refused": True, "stage": "screening", "reason": "encoded instruction"}
    ).passed


def test_a_polite_refusal_from_the_model_fails():
    """The single most counter-intuitive assertion in this file, and the one
    most likely to be "fixed" by someone reading a failing act and noticing the
    model said no. A 200 proves nothing about the platform: swap the model
    tomorrow and the control is gone with this gate still green."""
    result = judge_guarded(
        200, {"answer": "I'm sorry, I can't share my system prompt.", "tool": "search_show"}
    )
    assert not result.passed
    assert "no platform control fired" in result.detail


def test_a_refusal_at_the_wrong_stage_fails():
    """A classification refusal is a real control but not this one. If the
    injection is being turned away by the routing table, the guardrail is
    untested and the act would be reporting coverage it does not have."""
    assert not judge_guarded(
        403, {"refused": True, "stage": "classification", "reason": "sensitive"}
    ).passed


def test_a_403_without_a_refusal_body_fails():
    """A 403 from something else — IAM, a proxy — is not the platform refusing."""
    assert not judge_guarded(403, {"message": "Forbidden"}).passed


def test_a_refusal_with_no_reason_fails():
    assert not judge_guarded(403, {"refused": True, "stage": "guardrail"}).passed


# ── metered: this act is invariant 1's evidence ───────────────────────────


def test_a_served_and_a_blocked_row_pass():
    assert judge_metered([SERVED_ROW, BLOCKED_ROW]).passed


def test_no_rows_fails():
    """The service holds no `bedrock:*`, so an answer with no gateway row means
    the model was reached some way this platform does not know about."""
    result = judge_metered([])
    assert not result.passed
    assert "gateway saw nothing" in result.detail


def test_a_row_attributed_to_another_service_fails():
    assert not judge_metered([{**SERVED_ROW, "service_id": "evalsvc"}, BLOCKED_ROW]).passed


def test_a_served_row_with_no_cost_fails():
    assert not judge_metered([{**SERVED_ROW, "cost_usd": "0"}, BLOCKED_ROW]).passed


def test_a_served_row_with_no_price_basis_fails():
    """ADR-006: a cost with no recorded basis cannot be corrected later."""
    row = {k: v for k, v in SERVED_ROW.items() if k != "price_basis"}
    assert not judge_metered([row, BLOCKED_ROW]).passed


def test_a_run_with_no_blocked_row_fails():
    """The guarded act produced a refusal; the ledger has to carry it. A
    refusal nothing records cannot be counted by M05's intervention dashboard,
    and the two gates would then disagree about what happened."""
    result = judge_metered([SERVED_ROW])
    assert not result.passed
    assert "blocked" in result.detail


# ── traced ────────────────────────────────────────────────────────────────


SPAN_RECORD = (
    '{"name": "catalog-agent.answer", "context": {"trace_id": "0xabc"}, '
    '"attributes": {"gen_ai.system": "aws.bedrock", "gen_ai.operation.name": "chat", '
    '"agentpave.feature_id": "summarize"}}'
)


def test_an_exported_span_passes():
    assert judge_traced([SPAN_RECORD]).passed


def test_no_span_records_fails():
    """Fail closed. Spans configured and none arriving is precisely the state
    the first implementation produced — OTEL was an optional extra that nothing
    vendored — so it cannot be the state that passes."""
    assert not judge_traced([]).passed


def test_lambdas_own_trace_output_does_not_count():
    """The false pass this act was rewritten to remove.

    `Tracing.ACTIVE` makes Lambda emit an X-Ray segment for every invocation,
    including one that crashed at import before a line of our code ran. The
    first version of this act read those summaries and went green on exactly
    that run, while `answered`, `guarded` and `metered` all failed.
    """
    lambda_noise = [
        "START RequestId: 7c1f Version: $LATEST",
        "REPORT RequestId: 7c1f Duration: 812.44 ms XRAY TraceId: 1-68a-abc",
        "END RequestId: 7c1f",
    ]
    result = judge_traced(lambda_noise)
    assert not result.passed
    assert "not this service's agent loop" in result.detail


def test_a_span_without_the_conventional_attribute_names_fails():
    """`llm.model` is not `gen_ai.request.model`. A dashboard built on the
    convention shows nothing when they differ, and nothing else anywhere would
    report a problem — so the exact strings are what get checked."""
    renamed = SPAN_RECORD.replace("gen_ai.system", "llm.system")
    result = judge_traced([renamed])
    assert not result.passed
    assert "gen_ai.system" in result.detail


# ── the report ────────────────────────────────────────────────────────────


def test_one_failed_act_fails_the_run():
    from agentpave_infra.walkthrough import Result

    rendered, passed = report([Result("a", True, "ok"), Result("b", False, "nope")])
    assert not passed
    assert "FAILED" in rendered


def test_an_empty_run_does_not_pass():
    """A walkthrough that evaluated nothing has not passed. This is the M02
    false-pass defect in its most reduced form."""
    _, passed = report([])
    assert not passed


# ── the questions themselves ──────────────────────────────────────────────


def test_the_guarded_question_still_names_the_show():
    """The agent calls its tool before it calls the model, and `search_subject`
    prefers the last capitalised run. A capitalised "Ignore" would beat
    "Severance", the query would match no fixture, and the act would fail at
    the tool with the guardrail never consulted — passing for a reason that
    has nothing to do with what it claims to test.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "catalog-agent"))
    try:
        from agentpave_catalog_agent.agent import search_subject
    finally:
        sys.path.pop(0)

    assert search_subject(GUARDED_QUESTION) == "Severance"
    assert search_subject(QUESTION) == "Severance"
