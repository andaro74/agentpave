# ADR-001: Scope and non-goals — tiny scale, production shape

**Status:** Accepted
**Date:** 2026-08-07
**Milestone:** M00

## Context

AgentPave is a one-week, one-person build whose purpose is to demonstrate a
thesis — that quality engineering for agentic AI belongs in platform
infrastructure, inherited by every service at birth — not to ship a product.
A week is enough to build every component *shaped* correctly; it is nowhere
near enough to build any component *sized* for production. Without an explicit
scope contract, the default failure mode is predictable: gold-plating the
first components and abandoning the last ones.

## Decision

Every component is the smallest implementation that preserves its production
shape, defined as: real AWS services (no mocks in the deployed path), real IAM
boundaries, fail-closed gates, tests in the same commit, and honest telemetry.

In scope: one LLM gateway (Lambda), one MCP tool (TVMaze), one golden-path
template, one scaffolded sample service, a ~30-case golden dataset with a
calibrated LLM-as-judge, a ~10-probe adversarial suite, one CI quality gate,
one CloudWatch dashboard, and (stretch, M06) headless-Claude self-healing.

Non-goals, permanently for this repo: multi-account topology; AgentCore
Runtime (ADR-003); Amazon Verified Permissions (Cedar runs in-process);
long-term agent memory; more than one template or tool; canary traffic
infrastructure (`pave shadow-eval` stands in); any UI beyond CloudWatch;
identity federation; production hardening of the TVMaze integration.

Scope cuts made *during* the week are not covered by this ADR — each gets its
own, the day it happens.

## Consequences

- The demo narrative (three acts) is achievable in seven milestone days.
- Anyone extrapolating this repo to production must read the non-goals list;
  the README's known-limits section (M07) will repeat it.
- "Smallest that preserves shape" is a judgment call — when in doubt, the
  tiebreaker is: does the cut change what the demo *proves*? If no, cut.

## References

- `docs/ARCHITECTURE.md` §3 (invariants, scope cuts)
- agentic-pii-erasure — the sibling project whose conventions this repo follows
