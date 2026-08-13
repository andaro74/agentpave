"""Routing resolves to a model, or refuses and says why."""

import pytest
from agentpave_gateway.routing import SHADOW_CANDIDATE_FEATURE, RoutingTable

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


# ── the shadow candidate (M06) ────────────────────────────────────────────


def test_the_shadow_candidate_routes_to_the_capable_model(table: RoutingTable) -> None:
    """`pave shadow-eval`'s candidate arm reaches a different model than the
    incumbent arm — which is the only reason a shadow run tells you anything."""
    assert table.route(SHADOW_CANDIDATE_FEATURE, "internal").model_id == CAPABLE


def test_the_shadow_candidate_differs_from_the_default_serving_model(
    table: RoutingTable,
) -> None:
    """The comparison must not be a model against itself.

    If the candidate feature ever fell out of `CAPABLE_FEATURES`, rule 2 would
    default it *open* to the fast model and every shadow run would compare the
    incumbent to itself — reporting a flat, reassuring "no change" while
    measuring nothing. That failure has no symptom in the report, so it is
    asserted here instead of trusted.
    """
    incumbent = table.route("summarize", "internal").model_id
    candidate = table.route(SHADOW_CANDIDATE_FEATURE, "internal").model_id
    assert candidate != incumbent


def test_the_shadow_candidate_is_still_refused_for_sensitive(table: RoutingTable) -> None:
    # A shadow run is still a caller. Naming a feature does not buy an
    # exemption from the classification rule.
    assert table.route(SHADOW_CANDIDATE_FEATURE, "sensitive").model_id is None


@pytest.mark.parametrize(
    ("fast", "capable"),
    [("", CAPABLE), (FAST, "")],
)
def test_missing_model_id_fails_at_construction(fast: str, capable: str) -> None:
    # An unset environment variable should be a startup error, not a confusing
    # Bedrock 400 on some user's request.
    with pytest.raises(ValueError, match="non-empty Bedrock model id"):
        RoutingTable(model_fast=fast, model_capable=capable)
