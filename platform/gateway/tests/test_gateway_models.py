"""Boundary models reject what they do not understand."""

import pytest
from agentpave_gateway.models import (
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
