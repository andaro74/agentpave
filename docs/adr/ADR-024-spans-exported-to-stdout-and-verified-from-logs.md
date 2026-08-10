# ADR-024: Spans are exported to stdout and the deployed gate reads them back from CloudWatch

**Status:** Accepted (supersedes ADR-019)
**Date:** 2026-08-10
**Milestone:** M04

## Context

ADR-019 decided that scaffolded services would use the OTEL SDK in-process and
export directly from the Lambda, with no collector and no ADOT layer. The
decision stands. Its implementation did not survive first contact with a
deployed run.

M04's first `make walkthrough` reported the service as **traced** on a run
where the function crashed at import and answered nothing. Three failures
compounded into one green tick:

1. **Nothing was ever exported.** OTEL shipped as an optional dependency of the
   template and nothing vendored it into the asset, so `_tracer()` hit
   `ImportError` and returned `None`. The graceful degradation ADR-019 chose —
   a service that starts without a tracer rather than refusing to run — is what
   made the absence silent.
2. **Nothing would have been exported anyway.** `trace.get_tracer()` with no
   `TracerProvider` registered returns a proxy backed by the API's default
   no-op provider. Every call succeeds and every span is discarded. Installing
   the SDK package is not the same as installing a provider, and ADR-019's
   implementation did the first and not the second.
3. **The gate could not tell.** The `traced` act read X-Ray trace summaries,
   and `Tracing.ACTIVE` makes Lambda emit a segment for *every* invocation
   including a failed init. It was reading the runtime's instrumentation and
   reporting it as the platform's.

Telemetry that is silently absent is worse than telemetry that is missing
loudly, because a gate will vouch for it. This is M02's false-pass defect —
transport failure folded into a passing result — rebuilt inside the milestone
that was supposed to have learned it.

## Decision

**OTEL is a hard runtime dependency of every scaffolded service, vendored into
the Lambda asset. A real `TracerProvider` is installed on first use, with a
`SimpleSpanProcessor` writing compact one-line JSON to stdout. `make
walkthrough` proves telemetry by reading those records out of the service's
CloudWatch log group, never from X-Ray.**

Four details are load-bearing and each has a test:

- **`SimpleSpanProcessor`, not `BatchSpanProcessor`.** A Lambda container is
  frozen the moment the handler returns, so a batch is flushed on some later
  invocation or, far more often, never.
- **One line per span.** CloudWatch turns each line of stdout into its own log
  event, so the exporter's default pretty-print scatters one span across thirty
  events with nothing tying them together.
- **The act checks the exact semconv strings.** `llm.system` is not
  `gen_ai.system`, and a dashboard built on the convention shows nothing when
  they differ — with no error anywhere.
- **A declared dependency that nothing vendors fails `make check`.**
  `test_pave_asset.py` joins three lists that previously agreed pairwise: what
  the rendered package imports, what its `pyproject.toml` declares, and what
  the Makefile's `SERVICE_DEPS` vendors.

Graceful degradation is unchanged: with the packages absent the tracer is still
a no-op and the service still answers. What changed is that the packages are
present, and the deployed gate fails when the spans are not.

## Consequences

**Easier.** The checklist item "OTEL spans use GenAI semantic conventions end
to end" is now a claim something checks rather than a claim someone made. Spans
land in the same log group as everything else the service writes, so
correlating a span with an error needs no second console. M05's dashboard can
query them with CloudWatch Logs Insights and no new infrastructure.

**Worse, and this is the cost.** Every traced request now pays a synchronous
JSON serialisation and a write on the response path — the cost ADR-019 named in
the abstract, now actually being paid, because previously nothing was exported
at all. The asset grows by the OTEL SDK and its transitive dependencies, which
lengthens cold starts. Span records are also verbose, and CloudWatch bills by
ingested volume: at this demo's traffic that is negligible, and at real traffic
it is the first thing that would make this decision wrong.

Structured logs are also not traces. There is no service map, no latency
waterfall, and no automatic correlation across the three hops beyond the
`traceparent` this platform propagates by hand. A real deployment wants a
collector; this one wants a verifiable export path and no idle infrastructure
(ADR-002).

**Forecloses nothing.** The exporter is one line in `_install_provider`.
Pointing it at an OTLP endpoint or adding the ADOT layer changes that line and
the stack, and leaves the attribute names — the part worth getting right —
untouched.

## References

- ADR-019 — superseded; its decision to avoid a collector stands, its
  implementation did not export
- ADR-002 — nothing bills while idle, which is why there is still no collector
- ADR-009 — the warm-container failure this project keeps re-learning; the
  frozen-container reasoning behind `SimpleSpanProcessor` is the same shape
- `docs/VALIDATION.md` — the M04 deployed row recording the false pass
- `platform/infra/agentpave_infra/walkthrough.py` — `judge_traced`
