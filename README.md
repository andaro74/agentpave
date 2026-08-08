# AgentPave

> **The paved road provides. The quality gate decides.**

**A miniature agentic AI developer platform on AWS with QA baked in — one
command scaffolds a governed agent with evals, guardrails, tracing, and a
failing-closed CI quality gate.**

Built in public, one milestone at a time, with
[Claude Code](https://claude.com/product/claude-code) — following the same
docs-first, ADR-driven convention as
[agentic-pii-erasure](https://github.com/andaro74/agentic-pii-erasure).

## Status

🚧 **M00 of M07** — plan on the record. See
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the build order and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the spec. Every milestone
has two gates: a **hermetic** one (`make check`, no AWS account) and a
**deployed** one a human runs after `make deploy-dev`. Deviations from the
plan become ADRs in [`docs/adr/`](docs/adr/) the day they happen.

The full README — the three-act demo, architecture, honest cost section, and
known limits — arrives at M07. Until then, the docs *are* the project.

## The three acts (coming)

1. **Paved road** — `pave new catalog-agent` → deployed, traced, metered,
   guarded agent in minutes.
2. **The gate bites** — a "be more concise" prompt PR blocked by the eval gate,
   score-diff posted as a PR comment. The red PR stays in history.
3. **Self-healing** — a schema change breaks a contract test; Claude Code
   (headless, in CI) proposes the repaired test as a human-reviewed PR.

## Quick start (grows with the milestones)

```
make help     # every verb this project will have — unimplemented ones say which milestone they arrive in
make check    # the hermetic gate: lint, unit + contract tests on fixtures, policy, synth — no AWS needed
```

## License

MIT. See [LICENSE](LICENSE).
