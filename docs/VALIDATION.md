# VALIDATION — review log

Human review record, one row per milestone gate plus ad-hoc reviews. This file
is the answer to "who actually ran the deployed gate, and what did they find?"
CI runs the hermetic gates; a human runs everything below.

| Date | Milestone | Gate | What was run | Findings | Resolution |
|------|-----------|------|--------------|----------|------------|
| 2026-08-07 | M00 | hermetic | `make help`, `make check` on clean clone | — | — |
| 2026-08-07 | M01 | hermetic | `make check` — 130 tests, ruff, `cdk synth` with IAM assertions | pytest's `--import-mode=importlib` synthesised a `platform` package that displaced the stdlib module, taking down the whole CDK import chain | Reverted to the default prepend mode; component-prefixed test basenames; regression pinned by a test (ADR-004) |
| 2026-08-07 | M01 | deployed | `make deploy-dev` then `make smoke-gateway` — 4/4 probes green on the first run | Both flagged uncertainties cleared: the `guardContent` wrapping behaves as intended, and the `PROMPT_ATTACK` filter at `HIGH` blocked the injection probe. **The guardrail's PII filters ship unprobed** — standing rule 3 forbids the PII-looking strings a probe would need | Accepted for M01. M03's adversarial suite must cover PII by another route, or record why it cannot |
| 2026-08-08 | M02 | hermetic | `make check` — 248 tests, ruff, `cdk synth` with IAM assertions incl. the negative Bedrock assertion | Green. Teeth demonstrated twice: a deliberately red commit (`d5bdce7`, 1 failure of 231, on the contract assertion) and a permanent mutation test | — |
| 2026-08-08 | M02 | deployed | `make deploy-dev` then `make conformance` — **failed on the first four runs** | Five defects, none visible hermetically. (1) The conformance driver sent unsigned requests to an `AWS_IAM` Function URL → 403 on everything. (2) The driver folded transport failure into `ok=False`, so three tests reported PASSED against a wall — every "this call must fail" test was satisfied by nothing working. (3) `lifespan="off"` left the session manager's task group unstarted → 500. (4) A module-level ASGI app would have 500'd every warm invocation. (5) MCP's `Host` check 421'd a hostname that cannot be known at build time. Plus two stale-API bugs in deployed-only code that had never executed: a 3-tuple unpack of a 2-tuple transport, and `getattr(result, "isError", False)` silently reading every error as success | Fixed; **48 passed, 1 skipped** against the deployed endpoint. Hermetic gap closed by `test_mcp_http_surface.py`, which drives the real handler with real Function URL events and invokes it twice; all three deployment fixes verified by mutation. ADR-009 (hosting) and ADR-010 (transport security) written. `test_read_tools_issue_only_get` stays skipped on the deployed driver — the HTTP client lives inside the Lambda |

| 2026-08-08 | M03 | hermetic | `make check` — 374 tests, ruff, `cdk synth` with IAM assertions incl. the eval stack's negative Bedrock assertion; `pave eval --dry-run`; dataset schema validation; judge-prompt lint | Green. Three defects found while building, each invisible to the suite meant to catch it. (1) The top-level `pave/` directory shadowed the installed `pave` package as a namespace package — ADR-004's failure in a new costume; the module is now `agentpave_pave`, matching every other component. (2) `pave eval --dry-run` died with `UnicodeEncodeError` on a stock Windows console while every test stayed green, because pytest's `capsys` captures text before it is encoded for a terminal. (3) The first PII synth assertion written for ADR-011 was tautological — both sides read the same YAML, so deleting an entity from the policy left it green; it is now paired with a hard-coded expectation in the gateway suite. Teeth demonstrated by five mutations: dropping `judge` from `CAPABLE_FEATURES`, accepting a polite model refusal as an adversarial pass, treating a disappeared capability as no change, deleting a PII entity from the authored policy, and renaming a key in the PII render path — all five turn the gate red | Fixed. The encoding fix ships with a test that encodes real output through cp1252. ADR-011 (PII asserted not probed) and ADR-012 (no Lambda in the eval stack) written. **Dataset curation rate: not yet recorded — the 30 golden cases, 10 probes and 10 calibration samples were drafted by Claude and have not been through human curation. ARCHITECTURE.md §6 requires the rate to be published, and it can only be recorded by the person who curates; this row is not complete until they do** |
| — | M03 | deployed | *(not yet run — `make deploy-dev`, then `make eval` and `make eval-adversarial`)* | — | — |

## M04 AgentCore-migration checklist (per ADR-003)

To be checked at M04 review; each item keeps the Lambda→AgentCore path a
packaging change rather than a redesign.

- [ ] Agent role holds zero `bedrock:*` permissions (also asserted in synth)
- [ ] All tool access via MCP; no direct service calls from the agent
- [ ] No in-process state survives a request
- [ ] OTEL spans use GenAI semantic conventions end to end

## M04 template checklist (per ADR-004, ADR-005, ADR-009)

Constraints M01 and M02 created for the scaffolder. Each is a way the template
could render output that fails its own `make check` on first run — or worse,
passes it and fails deployed.

- [ ] Rendered tests use component-prefixed basenames — pytest's prepend import
      mode requires them unique across the monorepo (ADR-004)
- [ ] Scaffolded services reach models only through the gateway, and therefore
      inherit the central guardrail rather than declaring one (ADR-005)
- [ ] Any model a template can route to has a price in `pricing.yaml`, or
      metering records it as free (ADR-006)
- [ ] Any HTTP-served component ships a hermetic test that drives its Lambda
      handler with a real event, and invokes it **twice** — M02's warm-container
      failure was invisible to both `make check` and a single manual probe
      (ADR-009)
- [ ] Any deployed gate that talks to an IAM-authenticated endpoint signs its
      requests, and treats transport failure as an error rather than as a
      result — otherwise "the call failed" tests pass against a dead endpoint
      (ADR-009)
- [ ] No scaffolded eval case, probe, or fixture contains a PII-shaped string
      (ADR-011, standing rule 3)
- [ ] Any feature the template can route to is listed in the gateway's
      `CAPABLE_FEATURES` or is genuinely fine on the fast model — the routing
      table defaults *open*, so an unlisted feature is silently downgraded and
      nothing in a passing run reveals it (M03: `judge` was that feature)
- [ ] Any Python package the template renders is named `agentpave_<component>`
      and never shares a name with its own directory — a directory that matches
      a module name shadows it as a namespace package (M03: `pave/`, ADR-004)
- [ ] Rendered CLI output is encoded explicitly, not left to the console's code
      page — `capsys` never sees an encoding error, so the hermetic gate cannot
      catch one (M03: `pave eval --dry-run` on cp1252)

## Ad-hoc reviews

*(none yet)*
