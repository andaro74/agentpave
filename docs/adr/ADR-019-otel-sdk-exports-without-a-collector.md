# ADR-019: Scaffolded services export OTEL spans from the function, with no collector and no ADOT layer

**Status:** Accepted
**Date:** 2026-08-10
**Milestone:** M04

## Context

ADR-003's migration checklist asks for OTEL GenAI semantic conventions end to
end. The conventional way to do that on Lambda is the AWS Distro for
OpenTelemetry layer: an out-of-process collector that batches spans and exports
them after the response is returned, so the invocation does not pay for the
flush.

It also adds a Lambda layer to every scaffolded service's stack, an ARN that
varies by region and architecture, a second configuration surface, and a
component `make check` cannot exercise — the layer only exists in a deployed
function, so a template that depends on it is a template whose telemetry is
first tested by a human running `make walkthrough`.

The traffic here is one span per invocation on a demo that idles at zero. The
batching a collector buys is a solution to a volume problem this platform does
not have (ADR-001), and a layer that idles is another thing to keep from
billing (ADR-002).

## Decision

**Scaffolded services use the OTEL SDK in-process and export directly from the
function. No ADOT layer, no sidecar, no collector.**

Three consequences of that choice are pinned in the template rather than left
to whoever deploys:

- **The attribute names are the conventional ones**, written out as constants.
  `llm.model` is not `gen_ai.request.model`, and a dashboard built on the
  convention silently shows nothing when they differ — a failure with no error
  and no red test.
- **W3C `traceparent` is propagated by hand** across all three hops, because
  there is no auto-instrumentation doing it. Without it the agent, the gateway
  and the MCP server produce three traces of one request.
- **Everything degrades to a no-op when the OTEL packages are absent.** OTEL is
  an optional dependency; a service that cannot start without a tracer is a
  service whose observability outranks its function.

X-Ray tracing is enabled on the function (`Tracing.ACTIVE`) so the spans have
somewhere to land without additional infrastructure.

## Consequences

**Easier.** The telemetry is entirely inside the template, so the render gate
lints it, the scaffolded tests exercise it, and `make check` covers it — none
of which is true of a layer. There is no region-specific ARN in any stack and
nothing to keep current when AWS publishes a new layer version.

**Worse, and this is the cost.** The export flush happens on the invocation
path, so every traced request pays for it in user-visible latency, and a slow
or unreachable endpoint slows the request rather than a background process.
Cold starts carry the SDK's import cost. At real volume this is the wrong
answer and the ADR should be superseded rather than stretched — the trigger is
sustained traffic, or a latency budget the flush starts to threaten.

**Forecloses nothing.** The exporter is configuration. Adding the ADOT layer
later changes the stack and an environment variable; the semconv attribute
names, which are the part worth getting right, are unaffected.

## References

- ARCHITECTURE.md §5 (telemetry), ADR-003's migration checklist
- ADR-001 — tiny scale, production shape
- ADR-002 — nothing bills while idle
- `templates/agent-tools/{{package}}/telemetry.py.j2`
- `platform/infra/agentpave_infra/stacks/service_stack.py` — `Tracing.ACTIVE`
  and the X-Ray actions on the permissions boundary
