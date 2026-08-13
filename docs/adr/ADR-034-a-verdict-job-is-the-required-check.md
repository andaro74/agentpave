# ADR-034: A verdict job is the required check, so a skipped eval stays a visible skip

**Status:** Accepted
**Date:** 2026-08-12
**Milestone:** M05

## Context

ROADMAP M05's deployed gate says the demo pull request is "**blocked** by the eval
gate", and ARCHITECTURE.md's Act 2 says "merge blocked". Neither was true. `main`
carried no branch protection, and `gh pr view 1` reported PR #1 as `MERGEABLE`
with the eval level red. The gate *reported*, the comment posted, and a human
declined to merge. That is a weaker claim than the one this milestone leads with,
and it was found by a human opening the pull request rather than by any test.

Turning protection on is not a one-line fix, because the obvious check to require
cannot be required. `deployed` is conditional on ADR-028's path filter — it runs
only when a gradeable path changed — and GitHub treats a **required check that
never runs as permanently pending**. Requiring it would deadlock every
documentation-only pull request, including the ones that close this milestone.

The tempting alternative is to make `deployed` always run and exit green when
nothing gradeable changed. That is worse than the deadlock. It produces a green
`L2 eval + L5 adversarial` having evaluated nothing, and a required check is read
as a verdict. This repository has shipped that exact shape four times: M02 folded
transport failures into passing results, M04's `traced` act vouched for Lambda's
own segments, ADR-015's probe could never have passed, and the first PII assertion
read the same YAML on both sides. A fifth, in the milestone whose thesis is that
gates fail closed, is not a trade worth making.

## Decision

A `verdict` job named **`gate verdict`** is the single required status check on
`main`. It `needs` every other job in `gate.yml`, runs `if: always()`, and states
its truth table explicitly:

- `hermetic` must be `success`. Not "not failure" — `cancelled` must block, and the
  workflow's concurrency group cancels superseded runs routinely.
- `changed` must be `success`.
- If `graded == 'true'`, `deployed` must be `success`.
- If `graded == 'false'`, `deployed` must be **`skipped`** and nothing else. Any
  other result means the filter and the run disagree about what happened, and a
  verdict is the wrong place to guess.

`deployed` keeps its `if`, so a skipped eval renders in the pull request's check
list as *skipped* rather than as a green tick. Making any level report success
without having run is **forbidden** without a superseding ADR.

Every branch of that table is asserted in
`platform/infra/tests/test_workflows.py`, including that the script tests for
`success` rather than against `failure`. Two mutations were run: accepting
`!= "failure"` and dropping a job from `needs` both turn the gate red.

## Consequences

**The milestone's headline claim becomes literally true.** A regression the eval
level catches now blocks the merge button, not merely the reader's conscience.

**Documentation pull requests stay mergeable** without the eval ever running, and
the check list says so on its face.

**The cost: the required check's name lives outside this repository.** GitHub
stores `gate verdict` as a string in a branch protection rule, so renaming the job
silently un-protects `main` — the rule waits forever for a check that no longer
exists and every pull request goes green. Nothing in `make check` can see the
GitHub side of that pair. A test pins the name here with the consequence in its
failure message, which is the same partial mitigation ADR-026 settled for: it
catches the rename, not the drift.

**One more job, and one more thing to understand.** Reading `gate.yml` now means
reading a truth table as well as a ladder, and the honest reason the table exists
is a GitHub implementation detail rather than anything about quality engineering.

**Enabling the rule is a manual step.** The protection rule is applied with a
`gh api` call by a person, is not in version control, and would not survive
recreating the repository. It is recorded in `docs/VALIDATION.md` rather than
asserted.

## References

- ARCHITECTURE.md §3 invariant 2 (quality gates fail closed), Act 2 in §1
- ROADMAP.md M05 deployed gate ("the demo PR … is **blocked**")
- ADR-028 (deployed levels run on changed paths only) — the filter this works around
- ADR-026 (a known gap read by a gate rather than by a reader) — the precedent for
  the un-assertable half
- ADR-029 (actionlint runs in CI, not in `make check`) — why these are structural
  YAML assertions rather than a lint
- `.github/workflows/gate.yml`, `platform/infra/tests/test_workflows.py`
