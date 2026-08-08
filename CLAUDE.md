# CLAUDE.md — AgentPave

> The paved road provides. The quality gate decides.

Miniature agentic AI developer platform on AWS with QA baked in.
**Before starting any milestone, read `docs/ARCHITECTURE.md` (the spec) and
`docs/ROADMAP.md` (the build order).** Work on ONE milestone at a time; do not
start the next milestone's scope without being asked. A milestone closes only
when both of its gates (hermetic + deployed) pass and its ADRs are written.

## Stack & conventions

- Python 3.12, managed with `uv`. Lint/format: `ruff`. Tests: `pytest`.
- Infrastructure: AWS CDK (Python), one stack per component
  (`gateway`, `registry`, `evalsvc`, `service-<name>`), never one mega-stack.
- Region: us-west-2. AWS profile: `agentpave`. Models via Amazon Bedrock only
  (Haiku for serving, Sonnet for judging). Change these only via `.env`.
- Monorepo layout is defined in ARCHITECTURE.md §4. Do not invent new
  top-level directories.
- Type hints everywhere; pydantic for all data models crossing a boundary.
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `infra:`). Small,
  granular commits — the public commit history is part of this project's story.
  Milestone close is marked with an `M0x` git tag.

## Command vocabulary (Makefile is the interface)

- `make check` — hermetic gate: lint, unit + contract tests on fixtures,
  policy tests, `cdk synth` with IAM assertions. **No AWS account needed.**
- `make deploy-dev` / `make destroy-dev` — create / tear down real infrastructure.
- `make smoke-gateway` · `make conformance` · `make eval` ·
  `make eval-adversarial` · `make walkthrough` — deployed gates (need AWS).
- `pave new <name> --template agent-tools` · `pave eval [--diff]` ·
  `pave shadow-eval` — the platform CLI.
- A verb that is not implemented yet must fail loudly with
  `"arrives in M0x — see docs/ROADMAP.md"`, never silently succeed.

## Standing rules

1. **Every new module ships tests in the same commit.** No test, no merge.
2. **Every deviation from ARCHITECTURE.md becomes an ADR** in `docs/adr/`
   (`ADR-NNN-slug.md`), written the same day with the `adr-writer` skill.
   Scope cuts are design decisions, not omissions.
3. **No secrets in code or fixtures. No real-PII-looking strings in fixtures.**
   There are no API keys in this project (TVMaze is keyless; Bedrock is IAM) —
   if you think you need a secret, stop and ask.
4. **Fixtures over live calls in tests.** `make check` stays hermetic and fast;
   anything needing AWS lives behind an explicit deployed-gate verb.
5. **Fail closed.** Quality gates, guardrail checks, and policy checks block on
   error — never skip-on-failure.
6. **Nothing bills while idle** (ADR-002). Lambda, DynamoDB on-demand, S3 —
   no provisioned floors. A component that violates this gets swapped, and the
   swap gets an ADR.
7. **Plan before code.** For each milestone, present a plan and wait for
   approval before writing files.
8. **Don't gold-plate.** Tiny scale, production shape. If a component works and
   is shaped correctly, stop; polish is M07's job.

## Definition of done (per milestone)

Hermetic gate green locally → deployed gate demonstrated by a human →
ADRs written for any deviations → `docs/VALIDATION.md` row added →
committed → `M0x` tag pushed.
