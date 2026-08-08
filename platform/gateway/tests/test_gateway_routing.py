"""Routing resolves to a model, or refuses and says why."""

import pytest
from agentpave_gateway.routing import RoutingTable

FAST = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
CAPABLE = "us.anthropic.claude-sonnet-4-6"


@pytest.fixture
def table() -> RoutingTable:
    return RoutingTable(model_fast=FAST, model_capable=CAPABLE)


@pytest.mark.parametrize("classification", ["public", "internal"])
def test_default_features_use_the_fast_model(table: RoutingTable, classification: str) -> None:
    decision = table.route("summarize", classification)  # type: ignore[arg-type]
    assert decision.model_id == FAST
    assert decision.allowed is True


def test_enrichment_is_routed_to_the_capable_model(table: RoutingTable) -> None:
    assert table.route("enrichment", "internal").model_id == CAPABLE


def test_unknown_feature_defaults_to_the_fast_model(table: RoutingTable) -> None:
    # Defaulting open on feature names is deliberate; defaulting to the *cheap*
    # model is the safe direction. A new service must never be able to reach
    # the expensive model by inventing a feature id.
    decision = table.route("some-feature-invented-next-quarter", "internal")
    assert decision.model_id == FAST


@pytest.mark.parametrize("feature", ["summarize", "enrichment", "unknown-feature"])
def test_sensitive_is_refused_for_every_feature(table: RoutingTable, feature: str) -> None:
    decision = table.route(feature, "sensitive")
    assert decision.model_id is None
    assert decision.allowed is False


def test_sensitive_refusal_explains_itself(table: RoutingTable) -> None:
    # A refusal with no reason is indistinguishable from a bug at 3am.
    reason = table.route("summarize", "sensitive").reason
    assert "sensitive" in reason
    assert "ADR-001" in reason


def test_sensitive_outranks_the_capable_feature_rule(table: RoutingTable) -> None:
    # Order matters: if the feature rule were checked first, enrichment would
    # route sensitive data to a model.
    assert table.route("enrichment", "sensitive").model_id is None


@pytest.mark.parametrize(
    ("fast", "capable"),
    [("", CAPABLE), (FAST, "")],
)
def test_missing_model_id_fails_at_construction(fast: str, capable: str) -> None:
    # An unset environment variable should be a startup error, not a confusing
    # Bedrock 400 on some user's request.
    with pytest.raises(ValueError, match="non-empty Bedrock model id"):
        RoutingTable(model_fast=fast, model_capable=capable)
