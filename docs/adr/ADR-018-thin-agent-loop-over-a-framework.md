# ADR-018: The agent loop is written by hand, and no agent framework is adopted

**Status:** Accepted
**Date:** 2026-08-10
**Milestone:** M04

## Context

M04 needs a thin agent loop in `templates/agent-tools`. The obvious candidates
are Strands Agents (AWS's own), LangGraph, and the Bedrock Agents service.

Each of them wants to own the parts this platform's invariants have already
claimed. A framework's model call is the framework's model call — it builds the
request, chooses where the system prompt goes, and decides what is sent as
guarded content. ARCHITECTURE.md invariant 1 says every model call goes through
the AgentPave gateway and no service holds Bedrock permissions; ADR-013 says the
split between the guarded span and the unguarded `system` field is the single
control that stops a poisoned tool response, and that it fails silently when it
is wrong. Handing that split to a library means the platform's most important
control is set by someone else's default and asserted by nobody.

The same applies to tools. ADR-008 puts Cedar in the MCP server and derives
identity from the caller; a framework's tool registry would sit in front of it
with its own idea of what the agent may call.

What the loop actually has to do is small: call one tool, put its output in the
guarded span, call the gateway, return the answer. The frameworks are not too
heavy for the task — they are aimed at a different task, one where the loop is
the interesting part. Here the loop is the boring part and the boundaries are
the interesting part.

## Decision

**The scaffolded agent loop is hand-written in `agent.py`: one tool, one turn,
no memory. Adopting an agent framework in any AgentPave service is forbidden
without a superseding ADR.**

Three properties the file holds, each of which a framework would own instead:

- The model is reached only via `gateway.complete`. Nothing in a scaffolded
  service constructs a Bedrock request.
- Tool output travels in `prompt`, never in `system` (ADR-013). Instructions
  live in a module-level constant that does not vary with input, which is the
  only thing that makes them safe outside the guarded span.
- No in-process state survives a request, so a warm container behaves like a
  cold one — an ADR-003 migration-checklist item, and M02's warm-container
  failure prevented from recurring in scaffolded code.

Constraints carried forward:

- [ ] A second tool is added by changing `_grounding` and nothing else; if that
      stops being true, the loop has outgrown this ADR and the successor decides
      between growing the loop and adopting a framework.
- [ ] Multi-turn or stateful agents are out of scope for this platform
      (ADR-001). The first genuine need for one triggers this ADR's review.

## Consequences

**Easier.** The whole request path is readable in one file, and every boundary
the platform asserts at synth is visible in the code that crosses it. The
template has no agent-framework dependency to pin, patch, or explain, and
`make check` stays hermetic without mocking one.

**Worse, and this is the cost.** Everything a framework would have provided is
now absent rather than deferred: no planner, no retry policy, no tool-choice
loop, no streaming, no conversation state, no parallel tool calls. A service
that needs any of them writes it, and each one written by hand is a place this
platform can have a bug that a maintained library would not. The loop is also
genuinely naive — it calls `search_show` for every question, regardless of what
was asked, because with one tool there is nothing to choose between.

**Forecloses little.** The loop sits behind `gateway.complete` and `call_tool`,
both of which a framework would also have to use to satisfy invariant 1. A
future adoption replaces `answer()` and keeps the boundaries.

## References

- ARCHITECTURE.md invariant 1, §2 (the capped capabilities)
- ADR-003 — Lambda over AgentCore Runtime, and its migration checklist
- ADR-008 — Cedar in-process, and how identity is derived
- ADR-013 — the guarded span is data, not instructions
- `docs/ROADMAP.md` M04 — "implement the thin agent loop"
