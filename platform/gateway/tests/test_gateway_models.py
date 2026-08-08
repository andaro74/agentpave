"""Boundary models reject what they do not understand."""

import pytest
from agentpave_gateway.models import (
    SYSTEM_MAX_CHARS,
    GatewayCompletion,
    GatewayRefusal,
    GatewayRequest,
    RoutingDecision,
    Usage,
)
from pydantic import ValidationError


def test_request_defaults_to_internal_classification() -> None:
    req = GatewayRequest(service_id="catalog-agent", feature_id="summarize", prompt="hi")
    assert req.classification == "internal"
    assert req.max_tokens == 512
    # No instructions unless a caller supplies them.
    assert req.system is None


def test_system_instructions_are_capped() -> None:
    """The only part of ADR-013's contract a machine can enforce.

    `system` skips the guardrail's prompt-attack filter, so the tempting fix
    for any future block is to move the offending text there. A cap makes that
    fail at the boundary for anything fixture-sized instead of succeeding
    quietly at a model.

    It bounds the damage; it does not prevent it. A short injection still fits,
    and nothing here can tell instructions from data. That is why the contract
    is an ADR and a review item, not just a number.
    """
    with pytest.raises(ValidationError, match="at most"):
        GatewayRequest(
            service_id="catalog-agent",
            feature_id="summarize",
            prompt="hi",
            system="x" * (SYSTEM_MAX_CHARS + 1),
        )


def test_a_realistic_system_prompt_fits_the_cap() -> None:
    # A cap that rejected a legitimate system prompt would push callers to fold
    # their instructions back into the guarded span — reintroducing the exact
    # block this split exists to fix. This stands in for the eval judge's
    # prompt at roughly its real length; the gateway suite does not import the
    # eval service.
    realistic = "You are grading one answer produced by a TV-catalogue assistant. " * 15
    assert len(realistic) < SYSTEM_MAX_CHARS
    GatewayRequest(service_id="evalsvc", feature_id="judge", prompt="hi", system=realistic)


def test_request_rejects_unknown_field() -> None:
    # A typo'd or renamed field must fail loudly, not be silently dropped.
    with pytest.raises(ValidationError):
        GatewayRequest(
            service_id="catalog-agent",
            feature_id="summarize",
            prompt="hi",
            temperture=0.9,  # type: ignore[call-arg]
        )


def test_request_rejects_unknown_classification() -> None:
    with pytest.raises(ValidationError):
        GatewayRequest(
            service_id="catalog-agent",
            feature_id="summarize",
            prompt="hi",
            classification="top-secret",  # type: ignore[arg-type]
        )


def test_request_rejects_empty_prompt() -> None:
    with pytest.raises(ValidationError):
        GatewayRequest(service_id="catalog-agent", feature_id="summarize", prompt="")


@pytest.mark.parametrize("max_tokens", [0, -1, 4097])
def test_request_rejects_out_of_range_max_tokens(max_tokens: int) -> None:
    with pytest.raises(ValidationError):
        GatewayRequest(
            service_id="catalog-agent",
            feature_id="summarize",
            prompt="hi",
            max_tokens=max_tokens,
        )


def test_request_is_frozen() -> None:
    req = GatewayRequest(service_id="catalog-agent", feature_id="summarize", prompt="hi")
    with pytest.raises(ValidationError):
        req.prompt = "mutated"  # type: ignore[misc]


def test_completion_and_refusal_are_discriminable_by_refused_flag() -> None:
    completion = GatewayCompletion(
        completion="Severance airs on Apple TV+.",
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        usage=Usage(input_tokens=12, output_tokens=8, cost_usd=0.000_02),
    )
    refusal = GatewayRefusal(stage="classification", reason="sensitive is refused by design")

    assert completion.refused is False
    assert refusal.refused is True


def test_usage_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1, output_tokens=0, cost_usd=0.0)


def test_routing_decision_reports_allowed() -> None:
    allowed = RoutingDecision(model_id="anthropic.claude-haiku", reason="default serving model")
    refused = RoutingDecision(model_id=None, reason="sensitive is refused by design")

    assert allowed.allowed is True
    assert refused.allowed is False


def test_platform_directory_does_not_shadow_stdlib() -> None:
    # `platform/` is a stdlib module name. The workspace layout keeps it off
    # sys.path; this test fails loudly if that ever regresses.
    import platform as stdlib_platform

    assert stdlib_platform.python_version().startswith("3.")
