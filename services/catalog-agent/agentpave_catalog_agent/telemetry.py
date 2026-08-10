"""OTEL spans for catalog-agent, using GenAI semantic conventions.

ADR-003's migration checklist asks for GenAI semconv "end to end", which means
the attribute names have to be the conventional ones rather than ones that
merely read well. An `llm.model` attribute is not `gen_ai.request.model`, and a
dashboard built on the convention will silently show nothing.

No collector and no ADOT layer (ADR-019). Spans are written to stdout, which
Lambda captures into CloudWatch Logs, and `make walkthrough` reads them back
(ADR-024). That is a plain export path with no infrastructure, and — the part
that matters — it is one the deployed gate can *verify*.

The verification is not decoration. ADR-019's first implementation degraded to
a no-op whenever the OTEL packages were absent, and they were absent: they
shipped as an optional extra that nothing vendored. So no span was ever emitted
in the deployed function, and the walkthrough's `traced` act went green anyway,
reading Lambda's own X-Ray segments — which exist for every invocation,
including one that crashed at import. Telemetry that silently is not there is
worse than telemetry that is missing loudly, because a gate will vouch for it.

Everything still degrades to a no-op if the OTEL packages are absent, because a
service that cannot start without a tracer is a service whose observability
outranks its function. What changed is that the packages are now vendored into
the asset, and the deployed gate fails when the spans do not arrive.
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


# Process-level, not request-level. The rule this template enforces everywhere
# else — nothing in-process survives a request — is about *state*: a cached
# answer or a warm client changes what the next request sees. A tracer provider
# is infrastructure, and it can only be registered once per process; calling
# `set_tracer_provider` again logs a warning and keeps the first one, so
# re-registering per request would be noise that achieves nothing.
_provider_installed = False


def _install_provider() -> bool:
    """Register a real tracer provider, once. Returns whether one is active.

    Without this, `trace.get_tracer()` returns a proxy backed by the API's
    default no-op provider: every call succeeds, every span is discarded, and
    nothing anywhere reports a problem. Installing the SDK is what turns the
    calls below from decoration into telemetry.

    `SimpleSpanProcessor`, not `BatchSpanProcessor`. Batching exports on a
    background thread, and a Lambda container is frozen the moment the handler
    returns — the batch would be flushed on some later invocation or, more
    often, never. Simple exports on span end, inline, before the answer goes
    back. That is a real cost on the request path and it is the price of a
    span that actually leaves the function.
    """
    global _provider_installed
    if _provider_installed:
        return True
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
    except ImportError:
        return False

    # One line per span, not the exporter's default pretty-print.
    #
    # CloudWatch turns each line of stdout into its own log event, so an
    # indented span arrives as thirty unrelated events: the span name in one,
    # `gen_ai.system` in another, and nothing tying them together. Anything
    # reading them back — the walkthrough's `traced` act, or any query M05
    # writes — would have to reassemble the record before it could ask a
    # question about it. Compact JSON keeps one span in one event.
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    exporter = ConsoleSpanExporter(formatter=lambda span: span.to_json(indent=None) + "\n")
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _provider_installed = True
    return True


def _tracer() -> Any | None:
    if os.environ.get("AGENTPAVE_DISABLE_OTEL") == "1":
        return None
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    if not _install_provider():
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
