"""Data models crossing the gateway boundary.

Every model here is strict: unknown fields are rejected rather than ignored.
A caller that sends a field the gateway does not understand has a bug, and a
gateway that silently drops it hides that bug — fail closed (standing rule 5).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Three tiers for this demo. `sensitive` exists so the platform can demonstrate
# refusing it by design; the full-scale answer is a dedicated enclave, which is
# out of scope (ADR-001).
DataClassification = Literal["public", "internal", "sensitive"]

# Where in the pipeline a request died. Kept distinct so the M05 dashboard can
# count classification refusals and guardrail interventions separately — they
# mean different things about the caller.
RefusalStage = Literal["classification", "routing", "guardrail"]

# How long a caller's system instructions may be. Sized to fit a real system
# prompt with room to spare — the eval judge's is about a thousand characters —
# and to be far too small for the tool output a caller might be tempted to move
# here after a guardrail block. See `GatewayRequest.system`.
SYSTEM_MAX_CHARS = 4096


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GatewayRequest(_Strict):
    """One inbound completion request from a service."""

    service_id: str = Field(min_length=1, max_length=64)
    feature_id: str = Field(min_length=1, max_length=64)
    # The untrusted span. Everything here is offered to the guardrail as
    # material that might be an attack: tool output, a user's question, an
    # answer under evaluation.
    prompt: str = Field(min_length=1)
    # The caller's own instructions, which do NOT pass through the guardrail's
    # prompt-attack filter. A service putting tool output or user text here
    # would route it around the platform's main injection defence, so the
    # contract is narrow and stated in ADR-013: code-resident text only.
    #
    # The cap is what little of that contract can be enforced mechanically. It
    # bounds the damage rather than preventing it — a 12k fixture will not fit,
    # a one-line injection still would — and it fails loudly at the boundary
    # instead of succeeding quietly at a model.
    system: str | None = Field(default=None, max_length=SYSTEM_MAX_CHARS)
    classification: DataClassification = "internal"
    max_tokens: int = Field(default=512, ge=1, le=4096)


class Usage(_Strict):
    """Token counts and the cost they imply, as reported by Bedrock."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)


class GatewayCompletion(_Strict):
    """A successful, guarded, metered completion."""

    refused: Literal[False] = False
    completion: str
    model_id: str
    usage: Usage


class GatewayRefusal(_Strict):
    """A request the gateway declined to send to a model, and why.

    A refusal is a normal, expected outcome — not an error. It carries the
    stage that produced it so callers (and the eval suite) can tell "this was
    blocked" from "this failed".
    """

    refused: Literal[True] = True
    stage: RefusalStage
    reason: str
    # Which guardrail filters intervened, as `policy:type` labels — empty for
    # every other stage. Filter *types* only: the matched text is never carried
    # here, because a refusal payload travels into CI logs and echoing blocked
    # content back out would undo the filter that stopped it.
    blocked_by: tuple[str, ...] = ()


class RoutingDecision(_Strict):
    """The routing table's verdict for one (feature, classification) pair.

    `model_id` is None when the pair is refused by design; `reason` always
    explains the choice, so a refusal is never silent.
    """

    model_id: str | None
    reason: str

    @property
    def allowed(self) -> bool:
        return self.model_id is not None
