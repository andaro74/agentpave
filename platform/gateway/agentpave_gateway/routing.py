"""Model selection by feature and data classification.

Two rules, in this order:

1. `sensitive` is refused, whatever the feature. This demo has no enclave to
   route it to, and inventing one would be worse than refusing (ADR-001). The
   refusal is the product behaviour, not a gap in it.
2. Everything else gets the fast model, except the features listed in
   `CAPABLE_FEATURES`, which get the capable one.

Rule 2 defaults *open* on an unknown feature, which is worth being explicit
about because standing rule 5 says fail closed. Fail-closed applies to safety
decisions; feature naming is not one. A gateway that 400s every feature it has
not been told about would mean editing and redeploying the platform every time
a service adds a capability — the opposite of a paved road. The default is
also the safe direction: unknown features get the cheapest, smallest model,
never the expensive one. Classification, which *is* a safety decision, is
checked first and fails closed.
"""

from .models import DataClassification, RoutingDecision

# Enrichment returns a structured metadata record whose fields are asserted
# deterministically by the eval suite; the smaller model's schema adherence is
# not reliable enough to grade. ARCHITECTURE.md §3: "Haiku default, Sonnet
# routed for enrichment".
#
# `judge` joined it in M03. It has to be listed explicitly, because rule 2
# below defaults *open* to the fast model: an unlisted `judge` feature would
# have graded every answer with a model no more capable than the one that
# produced it, and the suite would have gone green while measuring nothing.
# Nothing about that failure is visible in a scorecard, which is why it is a
# routing entry with a test rather than a convention.
CAPABLE_FEATURES = frozenset({"enrichment", "judge"})

_SENSITIVE_REFUSAL = (
    "data classification 'sensitive' is refused by design: this platform has no "
    "enclave to route it to (ADR-001)"
)


class RoutingTable:
    """Resolves (feature, classification) to a model, or to a refusal."""

    def __init__(self, *, model_fast: str, model_capable: str) -> None:
        # Misconfiguration fails at construction, not at the first request. An
        # empty model id would otherwise reach Bedrock as a confusing 400 on a
        # user's request rather than as a startup error in the logs.
        if not model_fast:
            raise ValueError("model_fast must be a non-empty Bedrock model id")
        if not model_capable:
            raise ValueError("model_capable must be a non-empty Bedrock model id")

        self.model_fast = model_fast
        self.model_capable = model_capable

    def route(self, feature_id: str, classification: DataClassification) -> RoutingDecision:
        if classification == "sensitive":
            return RoutingDecision(model_id=None, reason=_SENSITIVE_REFUSAL)

        if feature_id in CAPABLE_FEATURES:
            return RoutingDecision(
                model_id=self.model_capable,
                reason=f"feature '{feature_id}' is routed to the capable model",
            )

        return RoutingDecision(
            model_id=self.model_fast,
            reason=f"feature '{feature_id}' uses the default serving model",
        )
