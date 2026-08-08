"""The deployed gate's verdict logic, tested hermetically.

`make smoke-gateway` needs AWS, but the judgements it makes are ordinary code.
A gate whose assertions are untested can pass for the wrong reason — which is
precisely the failure mode the whole project is about — so the pure half is
exercised here on fixture responses.
"""

from decimal import Decimal
from typing import Any

import pytest
from agentpave_infra.smoke import (
    Result,
    build_probes,
    judge,
    judge_metering,
    report,
)

PROBES = {probe.name: probe for probe in build_probes("smoke-1")}


def _served_row(**overrides: Any) -> dict[str, Any]:
    return {
        "outcome": "served",
        "cost_usd": Decimal("0.00052"),
        "price_basis": "anthropic-list-2026-08-07",
        "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    } | overrides


def _refused_row(**overrides: Any) -> dict[str, Any]:
    return {"outcome": "refused", "cost_usd": Decimal(0)} | overrides


def _blocked_row() -> dict[str, Any]:
    return {
        "outcome": "blocked",
        "cost_usd": Decimal("0.00004"),
        "price_basis": "anthropic-list-2026-08-07",
        "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    }


def _all_rows() -> list[dict[str, Any]]:
    return [_served_row(), _refused_row(), _blocked_row()]


# ── probe construction ────────────────────────────────────────────────────


def test_probes_cover_the_roadmap_gate() -> None:
    # ROADMAP M01: a guarded metered completion, the metering row, and a
    # must-block prompt.
    assert set(PROBES) == {"served", "sensitive-refused", "guardrail-blocked"}


def test_probes_are_scoped_to_the_run() -> None:
    # A unique service_id is what lets the metering assertion query this run's
    # rows instead of guessing which row is newest.
    assert all(p.body["service_id"] == "smoke-1" for p in build_probes("smoke-1"))


def test_must_block_probe_contains_no_pii() -> None:
    # Standing rule 3. The guardrail's PII filters cannot be exercised without
    # committing a PII-looking string, so the must-block probe is an injection
    # string instead — which is also the filter M03 depends on.
    prompt = PROBES["guardrail-blocked"].body["prompt"]
    assert "@" not in prompt
    assert not any(char.isdigit() for char in prompt)


# ── served probe ──────────────────────────────────────────────────────────


def _completion(**overrides: Any) -> dict[str, Any]:
    return {
        "refused": False,
        "completion": "Severance airs on Apple TV+.",
        "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "usage": {"input_tokens": 120, "output_tokens": 80, "cost_usd": 0.00052},
    } | overrides


def test_served_probe_passes_on_a_metered_completion() -> None:
    assert judge(PROBES["served"], 200, _completion()).passed


def test_served_probe_fails_on_empty_completion() -> None:
    result = judge(PROBES["served"], 200, _completion(completion=""))
    assert not result.passed
    assert "empty" in result.detail


def test_served_probe_fails_when_usage_is_missing() -> None:
    # A 200 with no token counts means metering silently did nothing.
    result = judge(PROBES["served"], 200, _completion(usage={}))
    assert not result.passed
    assert "input tokens" in result.detail


def test_served_probe_fails_when_cost_is_zero() -> None:
    result = judge(
        PROBES["served"],
        200,
        _completion(usage={"input_tokens": 120, "output_tokens": 80, "cost_usd": 0}),
    )
    assert not result.passed
    assert "cost" in result.detail


def test_served_probe_fails_on_wrong_status() -> None:
    result = judge(PROBES["served"], 500, {"error": "boom"})
    assert not result.passed
    assert "expected HTTP 200" in result.detail


# ── refusal probes ────────────────────────────────────────────────────────


def test_sensitive_probe_passes_on_a_classification_refusal() -> None:
    body = {"refused": True, "stage": "classification", "reason": "sensitive is refused"}
    assert judge(PROBES["sensitive-refused"], 403, body).passed


def test_guardrail_probe_passes_on_a_guardrail_refusal() -> None:
    body = {"refused": True, "stage": "guardrail", "reason": "blocked by the guardrail"}
    assert judge(PROBES["guardrail-blocked"], 403, body).passed


def test_guardrail_probe_fails_when_the_block_came_from_classification() -> None:
    # The distinction M03 rests on. If a guardrail block reports as a
    # classification refusal, the platform cannot honestly say which
    # mechanism stopped the call.
    body = {"refused": True, "stage": "classification", "reason": "..."}
    result = judge(PROBES["guardrail-blocked"], 403, body)
    assert not result.passed
    assert "stage" in result.detail


def test_guardrail_probe_fails_when_the_model_merely_declined() -> None:
    # A 200 with a polite refusal in the text is "the model resisted", which
    # ARCHITECTURE.md invariant 5 says must never count as a pass.
    body = _completion(completion="I'm sorry, I can't help with that.")
    result = judge(PROBES["guardrail-blocked"], 200, body)
    assert not result.passed
    assert "expected HTTP 403" in result.detail


def test_refusal_without_a_reason_fails() -> None:
    body = {"refused": True, "stage": "classification", "reason": ""}
    result = judge(PROBES["sensitive-refused"], 403, body)
    assert not result.passed
    assert "reason" in result.detail


# ── metering ──────────────────────────────────────────────────────────────


def test_metering_passes_when_all_three_outcomes_are_recorded() -> None:
    assert judge_metering(_all_rows()).passed


def test_metering_fails_when_no_rows_were_written() -> None:
    result = judge_metering([])
    assert not result.passed
    assert "no metering rows" in result.detail


@pytest.mark.parametrize("dropped", ["served", "refused", "blocked"])
def test_metering_fails_when_an_outcome_is_missing(dropped: str) -> None:
    rows = [row for row in _all_rows() if row["outcome"] != dropped]
    result = judge_metering(rows)
    assert not result.passed
    assert dropped in result.detail


def test_metering_fails_when_the_served_row_has_no_cost() -> None:
    result = judge_metering([_served_row(cost_usd=Decimal(0)), _refused_row(), _blocked_row()])
    assert not result.passed
    assert "no cost" in result.detail


def test_metering_fails_without_a_price_basis() -> None:
    # ADR-006: without it, a later price correction restates history silently.
    rows = [_served_row(price_basis=""), _refused_row(), _blocked_row()]
    result = judge_metering(rows)
    assert not result.passed
    assert "price_basis" in result.detail


def test_metering_fails_when_a_refusal_was_charged() -> None:
    rows = [_served_row(), _refused_row(cost_usd=Decimal("0.01")), _blocked_row()]
    result = judge_metering(rows)
    assert not result.passed
    assert "charged" in result.detail


def test_metering_fails_when_the_model_is_not_recorded() -> None:
    rows = [_served_row(model_id=""), _refused_row(), _blocked_row()]
    result = judge_metering(rows)
    assert not result.passed
    assert "which model" in result.detail


# ── reporting ─────────────────────────────────────────────────────────────


def test_report_passes_only_when_every_result_passed() -> None:
    rendered, passed = report([Result("a", True, "ok"), Result("b", True, "ok")])
    assert passed
    assert "passed (deployed)" in rendered


def test_report_fails_when_any_result_failed() -> None:
    rendered, passed = report([Result("a", True, "ok"), Result("b", False, "nope")])
    assert not passed
    assert "FAILED" in rendered


def test_report_fails_on_an_empty_run() -> None:
    # Fail closed: a gate that evaluated nothing has not passed.
    _, passed = report([])
    assert not passed
