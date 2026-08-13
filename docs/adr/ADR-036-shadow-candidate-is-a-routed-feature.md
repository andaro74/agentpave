# ADR-036: The shadow candidate is a routed feature, not a caller-named model

**Status:** Accepted
**Date:** 2026-08-12
**Milestone:** M06

## Context

ARCHITECTURE.md §3 lists "no canary infrastructure" as a deliberate scope cut
with a named replacement: `pave shadow-eval`, candidate versus incumbent on the
golden set. ROADMAP M06 specifies the candidate as a different **model or
prompt**.

The prompt axis is free — the harness already passes a serving system prompt per
case, and substituting it changes nothing structural. The model axis is not.
`GatewayRequest` has no model field. Routing resolves `(feature_id,
classification)` to a model inside the gateway, and that placement is invariant 1
made mechanical: a service cannot ask for a model, because asking is how a
service ends up on an unpriced, unmetered, or unguarded one.

So a shadow run that let its caller pass a model id would have routed around the
table that exists to prevent exactly that. It would also have made two very
different requests indistinguishable at the boundary: "try the candidate model"
and "try any model at all". The second is what a compromised or careless caller
sends, and the gateway would have had no way to tell them apart.

The alternative considered was adding an allow-listed model override to
`GatewayRequest`, validated against a set of permitted ids. It fails on the same
ground for a smaller gain: the allow-list is a routing table with a second,
weaker implementation on the request path, and two tables that disagree is worse
than one table that constrains.

## Decision

**The candidate is a feature id. `SHADOW_CANDIDATE_FEATURE` is declared in the
gateway's routing table and routed to the capable model; `pave shadow-eval`
rewrites serving calls to name that feature and nothing else.** No model id
crosses the gateway boundary in either direction, and `GatewayRequest` is
unchanged. Answering "what if serving ran on a different model?" is one entry in
`CAPABLE_FEATURES`, added under review like any other routing change.

**The judge is identical on both arms, enforced in code.** `candidate_caller`
passes judge calls through untouched — neither its feature nor its system prompt
is rewritten. Rewriting either would score the two arms with different graders,
and every delta the report printed would then measure the judge. That failure
has no symptom in the output, so it is a branch with a test rather than a
convention.

**A shadow run writes no baseline and emits no scorecard line.** Its candidate
arm was served by a model the platform does not serve on, and charting that in
the eval trend would put a point on the graph that no deployed configuration
produced.

**`shippable` requires that no case regressed.** Not an improved mean, not a
favourable ratio. A candidate that lifts the pass rate while breaking a case
that used to pass is reported as not shippable, which is the property that
distinguishes this from `pave eval --diff`.

## Consequences

Invariant 1 holds without amendment, and shadow-eval needed no gateway contract
change to get a model axis. The routing table stays the single authority on what
runs where, and the shadow feature is visible in the same file as every other
routing decision.

The cost is that the candidate model is not arbitrary. Comparing against a third
model means editing the routing table and redeploying the gateway, not passing a
flag — so the fast experiment this verb was meant to enable is a deploy slower
than it could be. For a platform whose model set is two, that is a fair trade;
for a platform with ten it would be an obstacle, and the honest fix at that
scale is a routing table with more entries rather than a request field.

Refusing to record a baseline means a shadow run's numbers live only in the
terminal that produced them. Whoever runs one and wants the result kept has to
paste it somewhere, and nothing enforces that they do.

The strict `shippable` rule will report "not shippable" for candidates a human
would happily adopt — a case that flips on a stylistic axis is enough to trigger
it. That is deliberate and it is a real cost in false alarms: the rule is not
that a regressing candidate cannot be adopted, only that it cannot be adopted
without someone reading the case and saying so.

`pave shadow-eval` compares on the golden set only, and open question Q3 —
at what dataset size that stops being a meaningful canary stand-in — is
unanswered by this decision. Thirty-one cases is small enough that a single case
is 3.2% of the pass rate, so the verdict is sensitive to exactly the noise
ADR-016 pinned temperature to suppress.

## References

- ARCHITECTURE.md §3 (invariant 1; the canary scope cut and its named
  replacement) and open question Q3
- ROADMAP.md M06 — "candidate vs. incumbent model/prompt on the golden set"
- ADR-016 — the eval pins temperature, so a diff can tell a regression from a
  resample
- ADR-027 — the CI role cannot move the bar; a shadow run does not either
- ADR-030 — the dashboard charts deployed runs; a shadow run is not one
- `platform/gateway/agentpave_gateway/routing.py`,
  `platform/evalsvc/agentpave_evalsvc/shadow.py`
