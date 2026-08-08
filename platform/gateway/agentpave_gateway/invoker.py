"""The one place in AgentPave that calls Bedrock.

Every call carries the guardrail identifier, and the invoker refuses to
construct without one (ADR-005). That refusal is the enforcement point for
"Guardrails applied centrally": there is no code path here that reaches a model
unguarded, so the property holds by construction rather than by review.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict

# Bedrock's Converse API reports an intervention with this stop reason.
GUARDRAIL_STOP_REASON = "guardrail_intervened"


class InvocationResult(BaseModel):
    """What one Converse call produced."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    completion: str
    input_tokens: int
    output_tokens: int
    # True when Bedrock's guardrail stopped the call. Tokens are still billed
    # and still metered — a blocked request is not a free request.
    blocked: bool


class BedrockInvoker:
    """Invokes Bedrock's Converse API with the platform guardrail attached."""

    def __init__(
        self,
        client: Any,
        *,
        guardrail_id: str,
        guardrail_version: str,
    ) -> None:
        # An unguarded call is worse than no call, so a missing guardrail is a
        # startup failure rather than a per-request degradation.
        if not guardrail_id:
            raise ValueError("guardrail_id is required — refusing to invoke Bedrock unguarded")
        if not guardrail_version:
            raise ValueError("guardrail_version is required — refusing to invoke Bedrock unguarded")

        self._client = client
        self._guardrail_id = guardrail_id
        self._guardrail_version = guardrail_version

    def invoke(self, *, model_id: str, prompt: str, max_tokens: int) -> InvocationResult:
        response = self._client.converse(
            modelId=model_id,
            # The prompt is wrapped in guardContent so the guarded span is
            # explicit in the request rather than implied by a default.
            messages=[{"role": "user", "content": [{"guardContent": {"text": {"text": prompt}}}]}],
            inferenceConfig={"maxTokens": max_tokens},
            guardrailConfig={
                "guardrailIdentifier": self._guardrail_id,
                "guardrailVersion": self._guardrail_version,
            },
        )

        usage = response.get("usage", {})
        return InvocationResult(
            completion=_first_text(response),
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            blocked=response.get("stopReason") == GUARDRAIL_STOP_REASON,
        )


def _first_text(response: dict[str, Any]) -> str:
    """Concatenate the text blocks of the reply, tolerating a shapeless one.

    A blocked response carries the guardrail's own message here; a malformed
    one carries nothing. Neither should raise — the caller decides what an
    empty completion means, and it has the stop reason to decide with.
    """
    blocks = response.get("output", {}).get("message", {}).get("content", [])
    return "".join(block["text"] for block in blocks if "text" in block)
