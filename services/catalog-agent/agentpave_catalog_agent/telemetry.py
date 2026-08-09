"""OTEL spans for catalog-agent, using GenAI semantic conventions.

ADR-003's migration checklist asks for GenAI semconv "end to end", which means
the attribute names have to be the conventional ones rather than ones that
merely read well. An `llm.model` attribute is not `gen_ai.request.model`, and a
dashboard built on the convention will silently show nothing.

No collector and no ADOT layer (ADR-019): the SDK exports straight from the
Lambda. That keeps the dependency inside this template, keeps `make check`
hermetic, and keeps cold starts honest — at the cost of an export flush on the
invocation path, and of trace correlation that only works because the context
is propagated explicitly below.

Everything degrades to a no-op if the OTEL packages are absent, because a
service that cannot start without a tracer is a service whose observability
outranks its function.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

SERVICE_NAME = "catalog-agent"

# The conventional attribute names. Written out rather than composed, so a
# rename in the spec is a visible diff here instead of a dashboard that quietly
# stops matching.
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

# W3C trace context, so the agent, the gateway and the MCP server land in one
# trace instead of three. Propagated by hand because there is no collector and
# no auto-instrumentation doing it for us.
TRACEPARENT_HEADER = "traceparent"


class _NoopSpan:
    """What callers get when OTEL is not installed or not configured."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D102
        return None

    def record_answer(self, text: str) -> None:
        return None

    def record_failure(self, reason: str) -> None:
        return None


class _Span:
    """A thin wrapper, so the agent never touches the OTEL API directly."""

    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, key: str, value: Any) -> None:
        self._span.set_attribute(key, value)

    def record_answer(self, text: str) -> None:
        # Length, not content. An answer can carry anything the catalogue
        # carries, and traces are read by more people than logs are.
        self._span.set_attribute("agentpave.answer.chars", len(text))

    def record_failure(self, reason: str) -> None:
        self._span.set_attribute("agentpave.failed", True)
        self._span.set_attribute("agentpave.failure.reason", reason[:200])


def _tracer() -> Any | None:
    if os.environ.get("AGENTPAVE_DISABLE_OTEL") == "1":
        return None
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    return trace.get_tracer(SERVICE_NAME)


@contextmanager
def agent_span(*, feature_id: str):
    """One span per answered question."""
    tracer = _tracer()
    if tracer is None:
        yield _NoopSpan()
        return

    with tracer.start_as_current_span(f"{SERVICE_NAME}.answer") as raw:
        raw.set_attribute(GEN_AI_SYSTEM, "aws.bedrock")
        raw.set_attribute(GEN_AI_OPERATION, "chat")
        raw.set_attribute("agentpave.feature_id", feature_id)
        raw.set_attribute("agentpave.service_id", SERVICE_NAME)
        yield _Span(raw)


def trace_headers() -> dict[str, str]:
    """W3C headers for an outbound call, or an empty dict.

    The gateway and the MCP server are separate Lambdas. Without this the
    platform produces three unrelated traces per question and the "end to end"
    in the checklist item is not true.
    """
    tracer = _tracer()
    if tracer is None:
        return {}
    try:
        from opentelemetry.propagate import inject
    except ImportError:
        return {}
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier
