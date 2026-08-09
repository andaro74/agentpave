# ADR-016: The eval suite pins temperature to 0; serving keeps Bedrock's default

**Status:** Accepted
**Date:** 2026-08-08
**Milestone:** M03

## Context

`make eval` was run twice in succession against the deployed stack, with
identical code and an identical dataset. The score diff reported:

```
▲ pass rate +3.3%
    running: +16.7%
  no regression
```

Nothing changed between those runs. One case flipped from fail to pass because
the model sampled differently, and `--diff` printed `no regression` over the
top of it with complete confidence.

The gateway sent `inferenceConfig={"maxTokens": n}` and nothing else, so every
call ran at Bedrock's default temperature of 1.0 — the serving answer *and* the
judge's verdict, each rolling their own dice. A case near a threshold passes or
fails by luck, and the judge can score the same answer differently on two
readings.

This matters more for `--diff` than for any single scorecard. Its entire
purpose is telling a quality regression from noise, and it had just
demonstrated a noise floor of roughly one case — about 3% — while asserting
there was no regression. A real 3% regression would look exactly like what we
saw. The feature was not merely imprecise; it was actively misleading, which
is the failure mode this project keeps finding and this milestone exists to
guard against.

## Decision

**Every call the eval service makes — serving, judging, and adversarial
probing — pins `temperature` to `0.0` via `EVAL_TEMPERATURE`. The gateway
carries `temperature` as an optional field and omits it entirely when unset,
so serving traffic keeps Bedrock's default and requests that predate this
parameter are byte-identical.**

Pinning only part of the suite would be worse than pinning none of it: the
score would still flap, through whichever path was left open, while looking
deterministic. A test asserts all three call sites send it.

`0.0` is a value, not an absence. The invoker checks `temperature is None`
rather than truthiness, and a test names that specifically — `if temperature:`
would silently drop the one value the eval suite ever sends, leaving the code
reading as pinned and the runs still sampling.

**This buys reproducibility, not determinism.** Bedrock makes no guarantee
that temperature 0 is bit-identical across invocations, and model versions
change underneath a stable model id. The claim here is that run-to-run
variance stops dominating the signal, not that two runs are provably equal.

Constraint carried forward, to be checked at M05 review:

- [ ] The nightly eval reports whether consecutive runs of unchanged code
      produce identical scorecards. A residual flap is a fact about the
      platform worth charting, not an embarrassment to hide

## Consequences

**Easier.** A score change now means something changed. `--diff` becomes
usable for what it was built for, the baseline table accumulates comparable
rows rather than samples, and — immediately — the 18 currently-failing golden
cases can be fixed and the fix verified, which was not possible while any
change moved the number by luck.

**Worse.** Temperature 0 is not how the platform serves users, so the eval
now measures a mode the product does not run in. A case that passes
deterministically at 0 may fail for a real user at 1.0, and this suite will
never see it. That is a genuine narrowing of what the gate covers, traded for
a signal that can be reasoned about at all. Greedy decoding also has its own
failure modes — repetition, and a tendency to be more confidently wrong —
which the golden set will now systematically sample and users will not.

**Forecloses** measuring output variance as a quality signal. "How often does
this case pass out of ten runs?" is a better question than "did it pass?",
and pinning temperature makes it unanswerable without a deliberate second
mode. If flakiness ever becomes the thing worth measuring, this decision has
to be revisited rather than worked around.

**Revisit when** the platform serves real traffic and the gap between "passes
at 0" and "passes for a user" starts costing something — at which point the
answer is probably a small repeated-sampling suite beside the golden set, not
unpinning this one.

## References

- ARCHITECTURE.md §3 (the eval service and its score diff)
- `docs/VALIDATION.md` — M03 deployed row, where the +3.3% on unchanged code
  is recorded
- ADR-012 — the baseline table these runs write to, and M05's trend chart that
  will read it
- ADR-013 — the previous gateway request-shape change, for the same
  omit-unless-asked pattern
- `platform/evalsvc/agentpave_evalsvc/harness.py` — `EVAL_TEMPERATURE`
