# ADR-028: The deployed levels run on changed paths only

**Status:** Accepted
**Date:** 2026-08-11
**Milestone:** M05

## Context

L2 and L5 need a deployed stack and cost about $0.47 a run, measured across
seven consecutive runs that varied by less than 0.1%. Running them on every
push to every pull request means paying that for a typo in a docstring, and
paying it again on the next push five minutes later.

Running them never is the other failure, and the worse one: a gate that does
not run is a gate that reports nothing, and M05's entire claim is that the gate
bites.

## Decision

**The hermetic level runs on every push to every pull request, unconditionally
and with no path filter.** It costs nothing and it is the level that catches a
syntax error before the expensive ones start.

**The deployed levels run only when a changed path could plausibly move a
score**, currently `platform/`, `pave/`, `gate.yml`, and `.github/workflows/`.
The filter is computed by `git diff` in the workflow rather than by a
third-party filter action: the decision of when to spend money is one this
repository should be able to read without leaving it.

**The filter is a deliberate coverage cut and is named as one.** When it
skips, the workflow logs that it skipped and why. A gate that quietly declines
to run reads identically to a gate that ran and passed, which is the M02
false-pass defect wearing a workflow's clothes.

**`.github/workflows/` is inside the filter on purpose.** A change to the gate
itself must be graded by the gate, or the one pull request that can disable
this file is the one it does not examine.

## Consequences

A pull request that changes only `docs/`, `templates/`, `services/`, or the
README does not get an eval, and that is the intended saving — those are the
majority of changes in a project whose commit history is part of the artifact.

The cost is a real hole, in the direction path filters always leak. A change
under `services/` **can** move a score: the scaffolded service's prompts are
rendered from `templates/`, and a template edit that changed how a service
asks its question would ship ungraded. It is out of the filter because the
platform's dataset does not grade the scaffolded service — the gate that would
catch it is the service's own ladder, which this repository runs only in the
render gate. Anyone widening the template's reach into serving behaviour has to
widen this filter with it, and nothing enforces that.

More generally: a path filter is a list of guesses about what matters, and it
is wrong the moment someone adds a directory. The failure is silent by
construction — the eval simply does not run, the checks go green, and nothing
says a level was skipped for a reason that stopped being true. The log line is
the only mitigation, and a log line is weaker than a test.

The nightly run is what stops this being unbounded: everything the filter skips
during the day is graded within twenty-four hours, against the same baseline,
on `main`. That converts a missed regression from "never detected" into
"detected late", which is a different and much smaller problem.

## References

- ARCHITECTURE.md §4 — `.github/workflows/`
- ROADMAP M05 — the gate bites; the nightly eval schedule
- ADR-027 — the CI role, and why the nightly cannot re-baseline
- ADR-026 — the coverage this ladder already does not have
- `docs/VALIDATION.md` — the seven-run cost measurement this filter is priced
  against
