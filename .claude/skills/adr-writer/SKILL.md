---
name: adr-writer
description: Write or update Architecture Decision Records for this repo. Use whenever a design decision is made, a scope is cut, the plan in ARCHITECTURE.md or ROADMAP.md is deviated from, or a component is swapped. Also use when the user says "write an ADR", "record this decision", or closes a milestone with undocumented deviations.
---

# ADR Writer

## File naming and numbering

`docs/adr/ADR-NNN-short-slug.md`. NNN is zero-padded, strictly sequential —
check the directory for the highest existing number first. The slug is 2–5
lowercase hyphenated words naming the decision, not the problem
(`lambda-over-agentcore-runtime`, not `agent-hosting-question`).

## Template

```markdown
# ADR-NNN: <Decision stated as a decision, not a topic>

**Status:** Accepted | Superseded by ADR-MMM | Proposed
**Date:** YYYY-MM-DD
**Milestone:** M0x

## Context

Why this decision had to be made now. Name the forces honestly, including
time budget and cost. If a sibling project (agentic-pii-erasure, ShowRunner)
already learned this lesson, cite its ADR — precedent is context.

## Decision

What was decided, in concrete enforceable terms. Prefer "X is forbidden
without a superseding ADR" over "we should avoid X". If the decision
constrains a future milestone, state the constraint as a checklist item.

## Consequences

What this makes easier, what it makes worse, and what it forecloses. At least
one consequence must be a cost — an ADR with no downside is marketing.

## References

Links to ARCHITECTURE.md sections, ROADMAP milestones, sibling-project ADRs,
and external docs that constrained the decision.
```

## Tone rules

- Write decisions, not narratives. Past-tense certainty: "runs as", "is
  forbidden", "moves to" — never "we might" or "it could be nice".
- One decision per ADR. If the Context describes two decisions, split it.
- Honest downsides are mandatory. The Consequences section must contain at
  least one real cost of the decision.
- Scope cuts are decisions: an ADR titled "X deferred" must say what the demo
  loses without X and what would trigger revisiting.
- Keep it under a page. If it needs more, the extra belongs in
  ARCHITECTURE.md with the ADR linking to it.
- Never renumber or rewrite an accepted ADR; supersede it.
