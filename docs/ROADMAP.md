# ROADMAP — AgentPave build order

> **The paved road provides. The quality gate decides.**

Docs-first, built milestone by milestone with Claude Code, following the same
convention as [agentic-pii-erasure](https://github.com/andaro74/agentic-pii-erasure):
every milestone has two gates — a **hermetic** gate that runs in `make check`
with no AWS account, and a **deployed** gate a human runs after `make deploy-dev`.
A milestone is not done until both gates pass and its deviations are recorded as
ADRs in `docs/adr/`.

Target cadence: one milestone per day, seven days. Slips are recorded, not hidden.

---

## M00 — Plan on the record

Repo public from the first commit. `docs/ARCHITECTURE.md`, this roadmap,
`CLAUDE.md`, `.claude/skills/adr-writer`, AWS prep (Bedrock model access,
budget alarm, `cdk bootstrap`).

- **Hermetic gate:** repo clones clean; `make help` lists every verb this
  roadmap references (unimplemented verbs fail loudly with "arrives in M0x").
- **Deployed gate:** none — nothing deploys in M00.
- **ADRs opened:** ADR-001 scope & non-goals (tiny scale, production shape);
  ADR-002 nothing-bills-while-idle invariant (inherited from
  agentic-pii-erasure ADR-021); ADR-003 Lambda over AgentCore Runtime for the
  one-week build, with the migration path recorded.

## M01 — LLM Gateway

Gateway Lambda: Bedrock invoke behind one internal API; Guardrails applied
centrally; routing table (model by task type + data classification, with
`sensitive` refused by design in this demo); token metering to DynamoDB.

- **Hermetic gate:** `make check` — unit tests for routing table and metering
  writer (mocked); guardrail policy file lints; `cdk synth` passes IAM
  assertions (gateway role: `bedrock:InvokeModel` and its own table, nothing else).
- **Deployed gate:** `make smoke-gateway` — a curl returns a guarded, metered
  completion; the metering row exists; a must-block prompt is blocked.

## M02 — MCP tool + registry

`tvmaze-catalog` MCP server (search show, episodes, schedule) over recorded
fixtures + live mode. `registry/tools.yaml`: owner, semver, JSON schemas,
consequence class (`read`). Cedar policy (in-process) binding agent identity →
allowed tools. Same server registered in Claude Code's own config (dogfooding).

- **Hermetic gate:** contract suite green on fixtures — schema conformance,
  error shapes, side-effect-free verification, wrong-identity **deny** asserted;
  a deliberately broken schema turns the suite red (committed as a test of the
  tests, then reverted).
- **Deployed gate:** `make conformance` — the same suite against the deployed
  Lambda target.

## M03 — Eval service

Golden dataset (~30 cases, Claude-drafted / human-curated, curation rate
recorded) over TVMaze fixtures. Deterministic asserts: JSON schema for
enrichment mode, latency budget, cost budget. LLM-as-judge on Bedrock (Sonnet
judges, Haiku serves): groundedness vs. fixture data, completeness, tone —
judge calibrated on 10 hand-labeled cases, agreement published. Adversarial
mini-suite (~10 probes, incl. injection embedded in a tool-response fixture).
Baselines + score diff.

- **Hermetic gate:** dataset schema validates; deterministic asserts run on
  fixtures; judge prompts lint; `pave eval --dry-run` produces a plan.
- **Deployed gate:** `make eval` produces a scorecard and `pave eval --diff`
  a baseline comparison; `make eval-adversarial` passes on *"guardrail blocked,
  or policy denied and logged"* — never on *"the model resisted."*

## M04 — Golden path + first customer

`pave new <name> --template agent-tools --classification internal` renders the
template: gateway SDK pre-wired, OTEL, seed dataset, judge config, `gate.yml`,
per-agent IAM role with permission boundary, tool bindings, budget alarm.
Scaffold `services/catalog-agent`, implement the thin agent loop, deploy.

- **Hermetic gate:** scaffolded output passes its own `make check` from a clean
  render (template tests render + assert, snapshot-style); scaffolder unit tests.
- **Deployed gate:** `make walkthrough` — Act 1 end to end: scaffold → deploy →
  a traced, metered, guarded answer about a TV show. Recordable.

## M05 — The gate bites

`gate.yml` in CI: L0 unit → L1 contract → L2 evals → L5 adversarial,
fail-closed, score-diff table posted as a PR comment. CloudWatch
dashboard-as-code: eval trend, tokens/cost per service, guardrail
interventions, defect-leakage counter. Nightly eval schedule.

- **Hermetic gate:** workflow lints (`actionlint`); the PR-comment renderer has
  unit tests with a golden output file.
- **Deployed gate:** the demo PR — a "be more concise" prompt change — is
  **blocked** by the eval gate with the score-diff comment visible. The red PR
  stays in history deliberately. Act 2 recordable.

## M06 — Self-healing (stretch) + shadow eval

`selfheal.yml`: on a contract-test failure with a schema-diff signature, run
Claude Code headless with the failing test + the diff → open a PR labeled
`ai-proposed` with the repaired test and its reasoning; human review required —
propose/dispose applied to test maintenance. `pave shadow-eval`: candidate vs.
incumbent model/prompt on the golden set (the canary stand-in).

- **Hermetic gate:** selfheal trigger classifier unit-tested (real defect vs.
  schema drift); shadow-eval comparator unit-tested.
- **Deployed gate:** a staged schema change produces an `ai-proposed` PR that a
  human approves; Act 3 recordable. **If this milestone slips:** ship without
  it and record the design as an ADR marked *next* — honesty over vaporware.

> **Outcome (2026-08-12): the slip clause was used, in half.** `selfheal.yml`
> is not written. Running Claude Code headless in CI needs an identity holding
> `bedrock:InvokeModel`, and no such identity exists that does not weaken
> ARCHITECTURE invariant 1 — the trade was declined and the design recorded as
> *next* (ADR-035). What shipped is the classifier, `pave selfheal`, which is
> the distinction the milestone was actually about; a human runs Claude Code
> against its verdict, so Act 3 is recordable but human-triggered. `pave
> shadow-eval` shipped in full, varying the candidate model through the
> gateway's routing table rather than by letting a caller name a model
> (ADR-036). Both hermetic gates are met.

## M07 — Docs + publish

README (epigraph, problem, the three acts as GIFs, architecture, honest cost
section, known limits — same skeleton as agentic-pii-erasure). `docs/diagrams/`
rendered via `make diagrams`. `docs/VALIDATION.md` review log finalized.
10–15 ADRs complete. Lessons & Failures section with at least three real
failures from the week. Project page at floresinnovations.com/projects.

- **Hermetic gate:** `make check` green from a clean clone; README quick-start
  verified against a fresh checkout; no broken doc links.
- **Deployed gate:** full `make walkthrough` from `make deploy-dev` on a clean
  stack; `make destroy-dev` leaves nothing billing.

---

## Standing rules for every milestone

1. Both gates pass before the milestone closes; the deployed gate is run by a
   human, never assumed from CI.
2. Every deviation from ARCHITECTURE.md becomes an `ADR-NNN-slug.md` the same day.
3. `make check` stays fast and hermetic — anything needing AWS lives behind an
   explicit verb (`conformance`, `eval`, `integration`, `walkthrough`).
4. Nothing bills while idle (ADR-002). A component that violates this gets
   swapped, and the swap gets an ADR.
5. The commit history is part of the artifact: small conventional commits,
   milestone close marked with a `M0x` tag.
