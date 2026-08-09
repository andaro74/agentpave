# ADR-017: The SEXUAL filter guards the output side, not the catalogue coming in

**Status:** Accepted
**Date:** 2026-08-09
**Milestone:** M03

## Context

The first fully-diagnosable `make eval` failed four golden cases this way:

```
not evaluated: gateway returned status 403 ... blocked_by: ['contentPolicy:SEXUAL']
```

Every one of them reads `schedule__country-us__date-2026-08-07.json` — 320KB of
one day's United States television listings, recorded from TVMaze's public API.
A day of American television contains adult programming, so its titles and
descriptions trip a `SEXUAL` filter set to `HIGH` on the input side.

The platform could not answer "what is on tonight" using the catalogue it
exists to serve. Not a degraded answer: a 403.

This is the same principle the guardrail policy already states, one filter
higher up, about not blocking `NAME` and `ADDRESS`:

> A guardrail that blocks the product is not a guardrail, it is an outage.

The decision is uncomfortable because the shape of it — a safety control
weakened in response to a failing test — is exactly the move that should
attract suspicion. What makes it defensible here is not that the test failed;
it is that the blocked content is a public programme guide, and that the
relaxation is confined to one side of the call.

## Decision

**The `SEXUAL` content filter runs at `LOW` on the input side and stays at
`HIGH` on the output side.**

The asymmetry is the decision, not an implementation detail. Inbound content
on this platform is catalogue data: a listings feed the operator chose to
ingest. Outbound content is what a model wrote. Those are different trust
levels and they get different thresholds — we trust the catalogue as *data*,
and never trust the model as an *author*.

`LOW` rather than `NONE` because the input span also carries the user's
question, which is not a programme guide and has no such claim on being
believed. Removing the filter entirely would relax the user path to fix the
tool-output path.

**Widening this to `NONE`, or lowering the output side, is forbidden without a
superseding ADR.**

Constraint carried forward, to be checked at M04 review:

- [ ] When tool output reaches the model through the real agent rather than a
      recorded fixture, the input side still admits the catalogue and the
      output side still blocks generated sexual content — the split has to
      survive the component that made it necessary

## Consequences

**Easier.** Four golden cases become gradeable, and the platform can answer
questions about its own catalogue. More importantly the eval suite stops
reporting a guardrail false positive as a quality failure: those four cases
were `not evaluated`, which the scorecard already distinguished from "graded
badly" — that distinction is the only reason this was diagnosable at all.

**Worse.** Inbound screening for sexual content is now weak. A user prompt
carrying explicit material is far more likely to reach a model than it was
yesterday, and the only thing standing between that and a response is the
output filter plus the model's own training. For a TV catalogue serving
recorded fixtures that is a proportionate trade; for a platform accepting
arbitrary public input it would not be, and this ADR should be revisited the
moment the second is true.

There is also a subtler cost. The guardrail is now tuned to the dataset it is
evaluated against, and that is a loop worth naming: every future eval failure
attributable to a filter will arrive with a ready-made argument for relaxing
that filter. The asymmetry rule is what keeps this from being a general
licence — a change that cannot be confined to the input side does not get made
this way.

**Forecloses** treating "no content-filter blocks in a run" as evidence the
content filters work. Three of the six remaining filters have now never been
observed firing on real traffic, and `SEXUAL` has only been observed firing
wrongly.

## References

- ARCHITECTURE.md §3 (guardrails applied centrally)
- `docs/VALIDATION.md` — M03 deployed row, where the four 403s are recorded
- ADR-005 — the guardrail authored as YAML, rendered by `cdk synth`
- ADR-011 — the PII filters, where the same "do not block the product"
  reasoning kept `NAME` and `ADDRESS` out of the policy
- ADR-014 — the other control changed in response to this suite, in the
  opposite direction
- `platform/gateway/agentpave_gateway/guardrail_policy.yaml`
