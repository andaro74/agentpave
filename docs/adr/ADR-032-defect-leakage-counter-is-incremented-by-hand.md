# ADR-032: The defect-leakage counter is incremented by hand, and says so on its face

**Status:** Accepted
**Date:** 2026-08-12
**Milestone:** M05

## Context

ROADMAP M05 asks the dashboard for a defect-leakage counter, and ARCHITECTURE.md
§7 keeps the awkward part open as **Q2**: the counter needs a "prod-detected"
increment path, and what would the honest automated trigger be?

There is no honest automated trigger, because there is no production. AgentPave
has one account, one stage, no users, and nothing downstream of the gate that
could discover a defect the gate missed. "Defect leaked to production" is not a
measurable event in this platform.

Three ways to handle that, and the middle one is the trap:

1. **Omit the panel.** Honest, and drops a metric that is the whole point of a
   quality-engineering platform — leakage is the number that says whether the
   gate works.
2. **Synthesise a trigger.** Count gate failures on `main`, or nightly failures,
   and call them leakage. This is false in a specific and damaging way: a gate
   that fails on `main` is a defect **caught**. Charting it as leakage would make
   the platform's own working controls look like escapes, and the number would
   move for reasons unrelated to what it claims to measure.
3. **Maintain it by hand and admit it.**

## Decision

The counter is `DEFECTS_LEAKED` in
`platform/infra/agentpave_infra/stacks/dashboard_stack.py`, incremented by a
person in a reviewed commit, alongside `DEFECTS_LEAKED_LAST_REVIEWED`.

The panel is a text widget, not a query, and it **must state on its face** that
the number is maintained by hand and that nothing detects leakage automatically
because there is no production. `test_dashboard_stack.py` asserts that admission
is present. A hand-cranked number rendered like the three measured panels beside
it would borrow their provenance, and a hand-cranked number that looks measured
is worse than an empty panel.

Automating this counter is **forbidden** until there is a deployment with users
and a real detection path. Deriving it from gate failures, nightly failures, or
any signal produced by the gate itself is forbidden outright — those measure
catching, not leaking.

M07's close must restate this in the README's known limits rather than let a
dashboard panel imply it was solved.

## Consequences

**Q2 is answered rather than left open**, and answered in the direction the rest
of the project argues for: a measurement whose provenance is stated is worth more
than a measurement that looks automatic.

**The cost: the number will go stale, and a stale zero reads exactly like a
measured zero** to anybody skimming the page. The review date is the only defence
and nothing enforces its freshness — no test can, because "a human looked
recently" is not a property of the repository. This is the same failure shape
ADR-026's row in `docs/VALIDATION.md` exists to catch, and it is not fully closed
here.

**It is not chartable.** The three query panels have a time axis; this one is a
single number, so a leak followed by a fix leaves no trace on the dashboard. The
git history of the constant is the only record of movement.

**Revisit when** the platform has a real deployment with users, or when M06's
self-healing loop produces a defect class that reaches something downstream of
the gate — either would give the counter a trigger that is true.

## References

- ARCHITECTURE.md §7 Q2 (the question this answers), §3 (dashboard-as-code)
- ROADMAP.md M05 (the panel it asks for), M07 (known limits must restate this)
- ADR-030 (why the other three panels are queries)
- ADR-026 and the `docs/VALIDATION.md` row on it — the precedent for a known gap
  being read by a gate rather than by a reader who might
