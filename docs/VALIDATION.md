# VALIDATION — review log

Human review record, one row per milestone gate plus ad-hoc reviews. This file
is the answer to "who actually ran the deployed gate, and what did they find?"
CI runs the hermetic gates; a human runs everything below.

| Date | Milestone | Gate | What was run | Findings | Resolution |
|------|-----------|------|--------------|----------|------------|
| 2026-08-07 | M00 | hermetic | `make help`, `make check` on clean clone | — | — |
| 2026-08-07 | M01 | hermetic | `make check` — 130 tests, ruff, `cdk synth` with IAM assertions | pytest's `--import-mode=importlib` synthesised a `platform` package that displaced the stdlib module, taking down the whole CDK import chain | Reverted to the default prepend mode; component-prefixed test basenames; regression pinned by a test (ADR-004) |
| 2026-08-07 | M01 | deployed | `make deploy-dev` then `make smoke-gateway` — 4/4 probes green on the first run | Both flagged uncertainties cleared: the `guardContent` wrapping behaves as intended, and the `PROMPT_ATTACK` filter at `HIGH` blocked the injection probe. **The guardrail's PII filters ship unprobed** — standing rule 3 forbids the PII-looking strings a probe would need | Accepted for M01. M03's adversarial suite must cover PII by another route, or record why it cannot |

## M04 AgentCore-migration checklist (per ADR-003)

To be checked at M04 review; each item keeps the Lambda→AgentCore path a
packaging change rather than a redesign.

- [ ] Agent role holds zero `bedrock:*` permissions (also asserted in synth)
- [ ] All tool access via MCP; no direct service calls from the agent
- [ ] No in-process state survives a request
- [ ] OTEL spans use GenAI semantic conventions end to end

## M04 template checklist (per ADR-004, ADR-005)

Constraints M01 created for the scaffolder. Each is a way the template could
render output that fails its own `make check` on first run.

- [ ] Rendered tests use component-prefixed basenames — pytest's prepend import
      mode requires them unique across the monorepo (ADR-004)
- [ ] Scaffolded services reach models only through the gateway, and therefore
      inherit the central guardrail rather than declaring one (ADR-005)
- [ ] Any model a template can route to has a price in `pricing.yaml`, or
      metering records it as free (ADR-006)

## Ad-hoc reviews

*(none yet)*
