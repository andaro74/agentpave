# ADR-003: Lambda over AgentCore Runtime for the agent, with the migration path recorded

**Status:** Accepted
**Date:** 2026-08-07
**Milestone:** M00

## Context

Amazon Bedrock AgentCore Runtime is the right production home for the sample
agent: session isolation, agent identity, managed memory, and native OTEL
observability are exactly the properties a real platform standardizes on —
and the sibling project ShowRunner already demonstrates an AgentCore
deployment in full. But AgentCore adds real setup and iteration weight
(runtime packaging, identity wiring, session semantics) that would consume a
meaningful fraction of a seven-day budget while proving something this repo's
thesis does not depend on. AgentPave's thesis lives in the *platform*
machinery around the agent — gateway, registry, evals, gates — not in the
agent's runtime.

There is also a demo-integrity consideration: the catalog agent should be the
*most boring possible customer* of the platform, so that everything
interesting on screen is platform behavior.

## Decision

The catalog agent runs as a plain Lambda using Strands (or LangGraph — decided
at M04 by whichever lifts faster from existing code), invoked synchronously,
stateless per request, calling models only through the platform gateway and
tools only through the MCP registry.

The migration path to AgentCore Runtime is recorded here as a design
constraint on M04, not deferred thinking: the agent must (a) hold zero direct
Bedrock permissions — the gateway is its only model path; (b) reach tools only
via MCP, so AgentCore Gateway can replace the transport without agent changes;
(c) keep no in-process state between requests, so AgentCore session/memory
semantics can be adopted rather than retrofitted; and (d) emit OTEL spans with
GenAI semantic conventions, which AgentCore's observability consumes natively.
An agent meeting those four constraints moves to AgentCore Runtime as a
packaging change, not a redesign.

## Consequences

- The one-week budget is spent on the platform, which is the point of the repo.
- The demo does not showcase AgentCore Runtime; ShowRunner already does, and
  the M07 README will say so explicitly to preempt "why not AgentCore?"
- The four migration constraints above are enforceable: (a) is a `cdk synth`
  IAM assertion from M01; (b)–(d) are M04 review-checklist items in
  `docs/VALIDATION.md`.

## References

- `docs/ARCHITECTURE.md` §3 invariant 1 (all model calls via the gateway)
- ShowRunner — AgentCore Runtime/Memory/Identity/Gateway demonstrated in full
