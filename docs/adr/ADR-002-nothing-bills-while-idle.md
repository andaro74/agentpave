# ADR-002: Nothing bills while idle

**Status:** Accepted
**Date:** 2026-08-07
**Milestone:** M00

## Context

This constraint was learned the expensive way in the sibling project:
agentic-pii-erasure originally used OpenSearch Serverless for its derived
index, whose OCU floor billed for as long as the collection *existed* and
dominated the bill by an order of magnitude over everything else combined
(its ADR-021 records the swap to S3 Vectors, made purely on cost). For a
public reference repo that strangers are invited to deploy, a component that
punishes deployment is a structural problem: CI cost should scale with work
done, not with how long a stack has existed.

AgentPave is even more exposed to this failure mode than the PII project,
because a *platform* accumulates always-on-shaped components by nature —
gateways, dashboards, schedulers, baselines.

## Decision

Every component must bill only for work performed. Concretely: Lambda for all
compute (including the gateway and the MCP server); DynamoDB in on-demand
mode for metering, baselines, and eval results; S3 for datasets and fixtures;
EventBridge Scheduler for the nightly eval; CloudWatch within its per-use
pricing. Explicitly forbidden without a superseding ADR: provisioned-capacity
DynamoDB, OpenSearch Serverless (OCU floor), always-on ECS services, NAT
gateways, provisioned Aurora, and anything attached to a VPC that implies an
hourly charge.

An idle deployed stack must cost cents per month. `make destroy-dev` remains
available and documented, but the design goal is that forgetting it is an
annoyance, not an incident.

## Consequences

- The gateway is a Lambda Function URL/API Gateway construct, not an ALB-fronted
  service — cold starts are accepted and measured rather than engineered away.
- If a future milestone genuinely needs a floor-billing component, the swap-in
  requires its own ADR stating the monthly idle cost in dollars.
- The M07 README gets an honest cost section, same as the sibling repo.

## References

- agentic-pii-erasure ADR-021 (S3 Vectors for cost) — the precedent
- `docs/ROADMAP.md` standing rule 4
