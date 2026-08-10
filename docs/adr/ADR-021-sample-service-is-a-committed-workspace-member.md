# ADR-021: The sample service is committed as a workspace member, and a drift test keeps it a render

**Status:** Accepted
**Date:** 2026-08-10
**Milestone:** M04

## Context

`services/catalog-agent` is scaffolder output. Two ways to treat it were open.

Leave it out of the repo and render it on demand. The template stays the single
source of truth and nothing can drift — but the tests it renders never run in
`make check`, so nothing grades the golden path. M04's whole claim is that a
scaffolded service arrives working, and a claim that runs in nobody's CI is a
claim.

Commit it as a `uv` workspace member. Then `make check` runs the tests the
service was scaffolded with, against the code it was scaffolded with, on every
change to the platform underneath it. The cost is that the repo now holds two
copies of the same thing.

The second option is the only one that grades anything, so the question is what
to do about the copy.

## Decision

**`services/catalog-agent` is committed and listed as a `uv` workspace member,
and `test_the_sample_service_is_what_the_template_renders_today` asserts it is
byte-identical to a fresh render.**

The sample is a render, not a fork. When the drift test goes red the remedy is
to re-render; hand-editing the sample is forbidden, because a fix applied to
the copy is a fix no future service receives.

The test earned its place on its first run, catching two template edits that
had already left the committed copy behind.

Constraints carried forward:

- [ ] Any template change re-renders the sample in the same commit
- [ ] The sample keeps its own tests passing under `make check`; a scaffolded
      service whose tests are skipped in the monorepo grades nothing

## Consequences

**Easier.** The golden path is graded rather than asserted. A platform change
that breaks scaffolded code — a gateway signature, a registry rename, a Cedar
policy — turns `make check` red in the same commit, instead of being discovered
by the next person to run `pave new`.

**Worse, and this is the cost, and it is sharper than it looks.** A declared
workspace member that is not on disk makes `uv` refuse to resolve the
workspace *at all*. So `pave new catalog-agent` cannot re-render its own
sample through `uv run`: deleting the directory first breaks the very command
that would recreate it. Re-rendering means invoking the interpreter directly,
bypassing uv's resolver — which is exactly the kind of step someone skips,
hand-edits around, and thereby forks the sample. The drift test is the guard
against that, not a cure for it.

The repo also carries duplicated content in review diffs: a one-line template
change shows up twice.

**Forecloses nothing.** Removing the sample later is deleting a directory and
a workspace entry. What would be lost is the grading, and this ADR is where
that trade is written down.

## References

- ADR-004 — workspace members over a top-level package, and the package-naming
  convention the sample follows
- `docs/ROADMAP.md` M04 — "scaffolded output passes its own `make check` from a
  clean render"
- `pave/tests/test_pave_scaffold.py` — the drift test and the render gate
- `pyproject.toml` — `[tool.uv.workspace] members`
