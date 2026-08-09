# catalog-agent

Scaffolded by `pave new catalog-agent --template agent-tools --classification internal`.

## What arrived with it

| Thing | Where | Why it is not optional |
|---|---|---|
| Gateway client | `agentpave_catalog_agent/gateway.py` | Invariant 1: no service holds Bedrock permissions |
| MCP tool client | `agentpave_catalog_agent/tools.py` | Registry + Cedar authorize every tool call (ADR-008) |
| Thin agent loop | `agentpave_catalog_agent/agent.py` | One tool, one turn, no in-process state (ADR-018) |
| OTEL spans | `agentpave_catalog_agent/telemetry.py` | GenAI semconv, propagated across all three hops (ADR-019) |
| Eval dataset | `eval/golden.yaml` | The gate needs something to grade |
| Quality gate | `gate.yml` | Fails closed; bites from M05 |

## The one mistake that fails silently

`prompt` is inspected by the guardrail. `system` is not.

Tool output and user questions go in `prompt`. Instructions this service wrote
go in `system`. Putting tool output in `system` routes it around the platform's
injection defence and you get a 200 and a plausible answer — nothing in a
passing test run reveals it (ADR-013).

## Local

```
make check      # from the repo root: lint, tests, synth
```

`eval/golden.yaml` seeds five cases over recorded fixtures. Add to it before
you add capabilities — a service whose dataset lags its features has a gate
that grades a subset and reports a whole.
