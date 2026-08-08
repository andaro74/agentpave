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

# Bedrock returns the guardrail's assessment only when the trace is on. Without
# it, a block is a bare boolean: something stopped the call and nothing records
# what. M03's first deployed eval run died on a guardrail intervention that no
# amount of reading the response could explain.
GUARDRAIL_TRACE = "enabled"

# Which field names each kind of intervention, per assessment policy.
#
# The `None` entries are the point of this table. Those records identify
# themselves by `match` — the offending text itself. Copying that into a
# refusal payload, and from there into CI logs, would echo blocked content back
# out through the diagnostic built to explain the block. For a PII filter the
# matched text is the personal data the filter exists to stop, so this is
# standing rule 3 at the one place the platform could break it by accident.
# Entity *types* are safe and are what a human debugging a block actually needs.
_ASSESSED: tuple[tuple[str, str, str | None], ...] = (
    ("topicPolicy", "topics", "name"),
    ("contentPolicy", "filters", "type"),
    ("wordPolicy", "customWords", None),
    ("wordPolicy", "managedWordLists", "type"),
    ("sensitiveInformationPolicy", "piiEntities", "type"),
    ("sensitiveInformationPolicy", "regexes", "name"),
    ("contextualGroundingPolicy", "filters", "type"),
)


class InvocationResult(BaseModel):
    """What one Converse call produced."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    completion: str
    input_tokens: int
    output_tokens: int
    # True when Bedrock's guardrail stopped the call. Tokens are still billed
    # and still metered — a blocked request is not a free request.
    blocked: bool
    # Which filters intervened, as `policy:type` labels. Empty when nothing was
    # blocked, and empty rather than absent when Bedrock returns no trace — a
    # missing assessment is not evidence of a missing filter.
    blocked_by: tuple[str, ...] = ()


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

    def invoke(
        self,
        *,
        model_id: str,
        prompt: str,
        max_tokens: int,
        system: str | None = None,
    ) -> InvocationResult:
        """Send one guarded turn.

        `prompt` is the untrusted span: tool output, a user's question, an
        answer being graded. `system` is the caller's own instructions, which
        it wrote and Bedrock is told to treat as such.

        The split is not cosmetic. Everything inside `guardContent` is offered
        to `PROMPT_ATTACK` as material that might be an injection, so a caller
        that folds its instructions into `prompt` is asking the filter whether
        its own system prompt looks like an attack on itself. It does — that is
        what an instruction inside untrusted input is — and M03's first
        deployed eval run was blocked exactly this way (ADR-013).
        """
        request: dict[str, Any] = {
            "modelId": model_id,
            # Wrapped in guardContent so the guarded span is explicit in the
            # request rather than implied by a Bedrock default.
            "messages": [
                {"role": "user", "content": [{"guardContent": {"text": {"text": prompt}}}]}
            ],
            "inferenceConfig": {"maxTokens": max_tokens},
            "guardrailConfig": {
                "guardrailIdentifier": self._guardrail_id,
                "guardrailVersion": self._guardrail_version,
                "trace": GUARDRAIL_TRACE,
            },
        }
        # Omitted rather than sent empty: a caller with no instructions of its
        # own should produce a request indistinguishable from one that predates
        # this parameter.
        if system:
            request["system"] = [{"text": system}]

        response = self._client.converse(**request)

        usage = response.get("usage", {})
        return InvocationResult(
            completion=_first_text(response),
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            blocked=response.get("stopReason") == GUARDRAIL_STOP_REASON,
            blocked_by=_blocked_by(response),
        )


def _assessments(guardrail: Any) -> list[dict[str, Any]]:
    """Every assessment in the trace, input and output side alike.

    Both sides are keyed by guardrail id — one assessment on the input, a list
    of them on the output. Which side blocked is not recorded here: a caller
    debugging a block needs to know *what* fired, and the input/output
    distinction is already carried by the stage on the refusal.
    """
    if not isinstance(guardrail, dict):
        return []

    found: list[dict[str, Any]] = []
    for assessment in (guardrail.get("inputAssessment") or {}).values():
        if isinstance(assessment, dict):
            found.append(assessment)
    for entries in (guardrail.get("outputAssessments") or {}).values():
        found.extend(entry for entry in (entries or []) if isinstance(entry, dict))
    return found


def _blocked_by(response: dict[str, Any]) -> tuple[str, ...]:
    """Which filters intervened, named but never quoted.

    Tolerant of a trace that is absent, empty, or shaped differently than
    expected: this runs on the path where a request has *already* been blocked
    correctly, and raising here would turn a working guardrail into a 500.
    """
    labels: list[str] = []

    trace = response.get("trace")
    for assessment in _assessments(trace.get("guardrail") if isinstance(trace, dict) else None):
        for policy, collection, identifier in _ASSESSED:
            records = (assessment.get(policy) or {}).get(collection) or []
            for record in records:
                if not isinstance(record, dict) or record.get("action") != "BLOCKED":
                    continue
                name = record.get(identifier) if identifier else None
                labels.append(f"{policy}:{name}" if name else f"{policy}:{collection}")

    # Deduplicated, insertion-ordered: one filter firing on six PII entities is
    # one fact, and a caller reading the label list should not have to count.
    return tuple(dict.fromkeys(labels))


def _first_text(response: dict[str, Any]) -> str:
    """Concatenate the text blocks of the reply, tolerating a shapeless one.

    A blocked response carries the guardrail's own message here; a malformed
    one carries nothing. Neither should raise — the caller decides what an
    empty completion means, and it has the stop reason to decide with.
    """
    blocks = response.get("output", {}).get("message", {}).get("content", [])
    return "".join(block["text"] for block in blocks if "text" in block)
