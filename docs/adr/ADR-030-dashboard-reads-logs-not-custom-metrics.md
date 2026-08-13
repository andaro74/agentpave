# ADR-030: The dashboard is built from log queries, never from custom metrics

**Status:** Accepted
**Date:** 2026-08-12
**Milestone:** M05

## Context

ROADMAP M05 asks for four panels: eval trend, tokens and cost per service,
guardrail interventions, and a defect-leakage counter. Until M05 the gateway
wrote nothing to stdout — everything it knew went into the metering table, and a
CloudWatch dashboard cannot read DynamoDB. There was literally nothing to chart.

Two ways to fix that. Publish custom metrics (`PutMetricData`, or EMF, or a
metric filter over the logs), or write one structured JSON line per event and
query it with Logs Insights.

Custom metrics bill about $0.30 per metric per month, forever, whether or not
anything runs. The panels need at least six series — input tokens, output
tokens, cost, refusals, eval pass rate, eval cost — which is roughly $1.80 a
month of standing charge on a platform that is idle almost all of the time.
ADR-002 forbids exactly that, and it is inherited from agentic-pii-erasure
ADR-021 for the same reason: a demo that quietly bills while nobody is looking
is a demo whose cost section cannot be honest.

Logs Insights inverts the billing. Storage for a JSON line is measured in
kilobytes, and a query is charged per gigabyte scanned **when somebody runs it**
— which happens when a human opens the dashboard.

## Decision

Every panel on the AgentPave dashboard is a Logs Insights query over a
structured log line. `PutMetricData`, EMF, `AWS::Logs::MetricFilter` and
CloudWatch alarms are **forbidden in the dashboard stack** without a superseding
ADR, and `platform/infra/tests/test_dashboard_stack.py` asserts their absence.

Concretely:

- The gateway emits one line per request, on **every** outcome including
  refusals. A dashboard that only saw completions would show a guardrail
  intervention rate of zero on the day everything was blocked.
- `pave eval` emits one line per graded run, passing or failing. A trend that
  dropped its failures would chart a platform that never regressed.
- There is **one** dashboard. CloudWatch gives three per account free and
  charges $3 a month for the fourth; the count is asserted at synth.
- Every query filters on its event marker. Both groups also carry Lambda's
  `START`/`END`/`REPORT` lines and anything anyone ever printed.
- Lines carry filter *types* and summary numbers only — never matched text,
  prompts, or model answers. A blocked string echoed into a log group undoes the
  filter that stopped it.

## Consequences

**Nothing bills while idle, and the dashboard costs approximately nothing to
own.** ADR-002 survives an observability milestone, which is the milestone most
likely to break it.

**The cost: no alarms are possible.** An alarm needs a metric, and there are no
metrics — so nothing in this platform can page anybody. Every panel is a thing a
human notices by looking. For a demo with no users that is the right trade; for
anything with a pager rotation it is not, and the migration is to EMF from the
same log lines, which is why the lines are flat and already carry the fields a
metric would need.

**History is bounded by retention, not by metric storage.** CloudWatch keeps
metrics for 15 months. These lines live as long as their group's retention — one
week for the gateway, three months for eval scorecards — so the eval trend
cannot reach further back than three months, ever. Extending it means paying for
retention, not for a metric.

**Queries recompute on every page load.** A wide time window is slow and scans
more data, and a dashboard left open on auto-refresh does bill. The default
window is therefore 7 days, and 14 for the trend.

## References

- ARCHITECTURE.md §3 (observability: dashboard-as-code) and §7 Q2
- ROADMAP.md M05
- ADR-002 (nothing bills while idle), inherited from agentic-pii-erasure ADR-021
- ADR-012 (the eval runs from CI, not a Lambda) — why a scorecard line has to be
  written on purpose rather than arriving as a side effect
- ADR-031 (the groups those queries name), ADR-032 (the one panel that is not a
  query)
- `platform/gateway/agentpave_gateway/telemetry.py`,
  `platform/evalsvc/agentpave_evalsvc/telemetry.py`
