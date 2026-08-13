# ADR-038: The eval trend charts the day's worst run beside its best

**Status:** Accepted
**Date:** 2026-08-13
**Milestone:** M07

## Context

The eval trend widget aggregates with `max(pass_rate)` over `bin(1d)`, and that
was a considered choice, not an oversight. Its docstring argues it: several runs
land on one day — a pull request's gate, a re-run after the fix, the nightly —
and `avg` would chart a day's worst moment into the trend forever, when the
question the panel answers is "does the suite still pass".

The reasoning holds for `avg`. It does not hold for what `max` does to a
*regression*, and M07's evidence capture is where that surfaced.

On 2026-08-13 the gate blocked a pull request at 29/31. `pave eval` wrote the
failing scorecard line exactly as ADR-030 requires, and the panel rendered two
points, 100 → 93.5. M05's review log recorded that as the first time the trend
had ever shown a regression rather than a dot. Later the same UTC day, PR #2's
two gate runs and the nightly all passed, and the 08-13 bucket became
`max(0.935, 1, 1, 1) = 100`. **The panel is now a flat line at 100 across both
days, and the only regression this project has ever recorded is not on it.**

The fix-and-re-run cycle guarantees this. A regression that is *repaired* is
followed by a passing run within hours and almost always inside the same UTC
day, so `max` by day does not merely de-emphasise regressions — it deletes
precisely the ones that were handled well, and keeps only the ones nobody got
round to fixing before midnight.

This contradicts ADR-030 in its own words. That ADR requires `pave eval` to emit
a line "per graded run, passing or failing", because "a trend that dropped its
failures would chart a platform that never regressed". The write side preserves
the failures on purpose; the read side then discards them. The data is intact in
the log group — only the query loses it.

The docstring's fallback — that the failures are "visible in the gate that
blocked and in the PR comment that explained why" — is true, and is an argument
for not needing a dashboard at all. The panel exists because ROADMAP M05 asks
for an eval trend. A trend that cannot render the event it exists to render is
this repository's recurring defect in chart form.

## Decision

The eval trend charts **two series over the same daily bin**:

    stats max(pass_rate) * 100 as best_pct,
          min(pass_rate) * 100 as worst_pct by bin(1d)

`best_pct` keeps exactly what the original argument wanted — "does the suite
still pass" — and `worst_pct` makes a regression permanently visible on the day
it happened, whether or not it was repaired before midnight.

A single-series aggregate over the eval trend is **forbidden without a
superseding ADR**, and `platform/infra/tests/test_dashboard_stack.py` asserts
that the trend query names both aggregates. Binning stays at `1d` and stays
UTC-anchored; Logs Insights cannot bin in local time, and the nightly is
scheduled in UTC.

`avg` remains rejected for the reason the original docstring gives.

## Consequences

**A repaired regression stays on the chart.** A reader can see that the suite
went red on 08-13 and that it also went green on 08-13, which is the shape of a
platform whose gate works — not the shape of one that never regressed. That
distinction is the entire claim this project makes about itself.

**The cost: a flake now marks the day permanently.** `worst_pct` cannot tell a
real regression from `airing-schedule-abc-overnight`'s unexplained tone failure,
so one flaky run leaves a dip that looks identical to a genuine defect and stays
there for the retention window. That is the accepted price of not hiding
regressions, and it is the correct direction for a quality panel to be wrong in:
a false dip prompts someone to read the run history, a hidden one prompts
nothing.

**Two lines are harder to read than one**, and on a saturated suite `best_pct`
is a flat 100 that carries no information most days. The panel is legible only
because the incumbent passes almost always — on a noisier suite the two series
would need separate widgets.

**It does not fix the underlying blindness, only this instance of it.** Any
Logs Insights aggregate discards something, and nothing in the dashboard tests
can tell that a query returns a *true* number that answers the wrong question.
This ADR fixes one panel; the class stays open.

## References

- ARCHITECTURE.md §3 (observability), §7 Q3
- ROADMAP.md M05 (dashboard-as-code: eval trend)
- ADR-030 (logs never metrics) — the write-side rule this decision restores at
  the read side; ADR-031 (the groups the query names), ADR-032 (the one panel
  that is not a query)
- ADR-016 (pinned temperature) — why a moved point is a signal rather than noise
- `platform/infra/agentpave_infra/stacks/dashboard_stack.py` (`_eval_trend`)
- docs/VALIDATION.md, M05 deployed row of 2026-08-13, whose "the trend now
  renders two points, 100 → 93.5" was true when written and is no longer
