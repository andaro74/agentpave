# ADR-022: A scaffolded service ships every seed case deterministic, and the judge off until someone calibrates it

**Status:** Accepted
**Date:** 2026-08-10
**Milestone:** M04

## Context

ROADMAP.md M04 says the template renders a seed dataset and judge config. The
straightforward reading is that a scaffolded service arrives with judged cases,
the way the platform's own dataset has them.

M03 established what a judged case costs. The judge is graded against
hand-labelled answers, `MIN_AGREEMENT` is 0.8, and a run below the floor fails
rather than downgrading — because an uncalibrated judge's verdicts are noise,
and a gate built on noise is worse than no gate, since it looks like coverage.

Calibration needs labelled **answers**, not labelled cases: the question is
whether the judge agrees with a person about a specific piece of text, and the
text a live run produces is not known in advance. A service scaffolded five
minutes ago has never run. There are no answers to label, and the only way to
ship a populated `calibration.yaml` in a template is to write samples nobody
labelled — a fabricated agreement rate that the gate would then trust to grade
real work.

The loader also required `calibration.yaml` to exist and be non-empty, so
"ship no calibration" was not expressible.

## Decision

**Every case in the scaffolded seed dataset is `grading: deterministic`, and
the template ships no `calibration.yaml`. Fabricating calibration samples for a
service that has not run is forbidden.**

To make that expressible without opening a hole, the loader changes in a pair:

- `calibration.yaml` is optional **by absence only**. A file that exists is
  still parsed strictly; someone who wrote that file meant something by it.
- A dataset containing a judged case and no calibration samples **fails the
  load**. A dataset may drop calibration only by also dropping every judged
  case.

Turning the judge on is therefore an ordered, human act, and the rendered
README states it: run the suite, label ten real answers with a reason each,
then switch cases to `judged`. The loader refuses the half-done state.

The seed grades on what needs no judge — `must_contain`, `must_not_contain`,
latency and cost budgets, and the enrichment schema.

## Consequences

**Easier.** A scaffolded service's gate means exactly what it says on day one.
Nothing in it rests on an agreement rate nobody measured, and the platform
cannot ship a number it invented. The new loader rule also strengthens the
platform's own dataset: deleting its calibration file now fails the load
instead of quietly grading with an unmeasured judge.

**Worse, and this is the cost.** The seed gate is genuinely smaller. It cannot
catch a hallucination that avoids every `must_not_contain` string, an answer
that is grounded but incomplete, or a tone regression — which is most of what
the judge exists for. A service whose owner never gets round to labelling ten
answers keeps that smaller gate forever, and nothing nags them. This ADR
chooses an honestly small gate over a larger one resting on a fabricated
number, and the coverage lost is real.

**Forecloses nothing.** The judge, the axes, the threshold and the calibration
machinery are all present and unchanged; what is absent is data only a person
can produce.

## References

- ROADMAP.md M03 — "judge calibrated on 10 hand-labeled cases, agreement
  published"; M04 — the seed dataset and judge config
- `platform/evalsvc/agentpave_evalsvc/calibration.py` — `MIN_AGREEMENT`
- `platform/evalsvc/agentpave_evalsvc/dataset.py` — the optional-by-absence
  rule and its pairing
- `docs/VALIDATION.md` — M03's published agreement rate and curation rate
- ADR-011 — the other place this project recorded a control it could not
  honestly exercise, rather than pretending to
