# VALIDATION — review log

Human review record, one row per milestone gate plus ad-hoc reviews. This file
is the answer to "who actually ran the deployed gate, and what did they find?"
CI runs the hermetic gates; a human runs everything below.

| Date | Milestone | Gate | What was run | Findings | Resolution |
|------|-----------|------|--------------|----------|------------|
| 2026-08-07 | M00 | hermetic | `make help`, `make check` on clean clone | — | — |

## M04 AgentCore-migration checklist (per ADR-003)

To be checked at M04 review; each item keeps the Lambda→AgentCore path a
packaging change rather than a redesign.

- [ ] Agent role holds zero `bedrock:*` permissions (also asserted in synth)
- [ ] All tool access via MCP; no direct service calls from the agent
- [ ] No in-process state survives a request
- [ ] OTEL spans use GenAI semantic conventions end to end

## Ad-hoc reviews

*(none yet)*
