# ADR-035: Self-healing ships its classifier and defers its CI model call

**Status:** Accepted
**Date:** 2026-08-12
**Milestone:** M06

## Context

ROADMAP M06 runs Claude Code headless in CI: a contract-test failure with a
schema-diff signature triggers an agent that opens an `ai-proposed` pull
request with a repaired test. Act 3 of the demo narrative.

The obvious implementation needs an Anthropic API key in a repository secret.
CLAUDE.md standing rule 3 forbids it outright, and ADR-027 already rejected the
same shape for AWS: a key in a secret exists whether or not a workflow is
running, cannot be scoped to a branch, and outlives every rotation policy
nobody remembers. That is settled and was never reopened.

The candidate that keeps both commitments is `CLAUDE_CODE_USE_BEDROCK=1`.
Claude Code resolves AWS credentials from the standard chain, so the OIDC token
a workflow already receives is sufficient and nothing persistent exists to
steal. It works. It also needs `bedrock:InvokeModel` on some role, and that is
the collision: ARCHITECTURE.md invariant 1 says every model call goes through
the gateway and no service holds Bedrock permissions of its own, asserted at
synth. `test_ci_stack.py` enforces it as `"bedrock:" not in template`.

Four options were costed. An API key is out on rule 3. Bedrock on the *existing*
CI role is out because the identity that runs the quality gate would gain model
access, and because it deletes an invariant's teeth rather than scoping them.
Bedrock on a *separate* self-heal role in its own stack works and was the
recommendation — at the price of amending invariant 1. Teaching the gateway to
speak Bedrock's wire protocol so Claude Code routes through it honours the
invariant literally, and is a gateway rewrite: streaming, tool use, multi-turn
agent payloads, and Bedrock Guardrails applied to a coding agent's transcript,
where one false positive silently corrupts a repair.

The price of the separate-role option is what decided this. Today the invariant
is checkable by one assertion across every synthesised template. After it, the
true statement is "no Bedrock permission outside the gateway stack *and the
self-heal stack*" — strictly weaker, and a scope boundary a reader has to take
partly on trust. That is a permanent cost to the cleanest claim in the spec,
paid for the milestone ROADMAP itself marks as a stretch, on the highest-risk
surface in the project: a credentialed agent in CI with pull-request write and a
prompt-injection path through repository contents it is asked to read.

Meanwhile the platform's own quality story has acknowledged holes — ADR-026's
untested tool authorization, ADR-028's path filter leaking on `templates/`, the
judge's near-miss blind spot. Those are gaps in the thesis. A missing third act
is not.

## Decision

**Running a model from CI is deferred. The classifier ships.**

`pave selfheal` is implemented, unit-tested, and hermetic: it reads a JUnit
report and a change set and returns `schema_drift`, `real_defect`, or
`unclassified`, proposing only on the first. It satisfies ROADMAP M06's hermetic
gate in full and runs on a laptop.

**`selfheal.yml` is not written, and no identity in this account holds Bedrock
permission outside `AgentPave-Gateway`.** `test_ci_stack.py`'s Bedrock assertion
stands unmodified, and ARCHITECTURE.md invariant 1 is unamended. Adding a
self-heal identity is forbidden without a superseding ADR.

Act 3 is demonstrated human-triggered: a staged schema change reddens the
contract suite, `pave selfheal` classifies it as drift, a human runs Claude Code
against that verdict from a laptop, and the resulting `ai-proposed` pull request
passes the same `gate verdict` check as any other. Propose/dispose intact;
autonomy absent. The README says so on its face — a recording that implied CI
made the model call would be the vaporware this deferral exists to avoid.

**Revisiting is triggered by either of two things**, and by nothing else:

- [ ] The gateway gains a Bedrock-protocol passthrough route, at which point
      Claude Code can be pointed at it and invariant 1 needs no amendment.
- [ ] A deliberate decision that the self-heal identity is worth the amendment,
      taken with the M07 known-limits list in view rather than mid-milestone.

## Consequences

The demo ships two automated acts and one human-triggered act where
ARCHITECTURE.md §1 promises three. That is the cost, it is visible in the
README, and it is the one a reader is most likely to notice.

The interesting half survives. The real-defect-versus-schema-drift distinction
is the whole design; running an agent afterwards is plumbing. Shipping the
classifier with tests and deferring the plumbing means the deferral is
demonstrable rather than described — the ADR points at working code and names
the one missing wire, instead of describing a system nobody built.

Invariant 1 stays absolute and stays cheap to verify. Every future reviewer can
still confirm it with a single assertion over synthesised templates, which is a
property that would not have survived the amendment and could not have been
recovered afterwards.

The prompt-injection surface is never opened. A pull-request author who controls
both the repository contents Claude Code reads and the prompt it is fed would
have been reaching into a credentialed model call with pull-request write. The
mitigations were identified and would probably have held; not needing them is
better than needing them.

The cost that is easy to overlook: a classifier with no automated consumer is
never exercised by anything except its own tests. Its rules encode assumptions
about which contract tests drift — assumptions that a year of real failures
would correct and that unit tests cannot. If self-healing is never wired up, the
classifier's fitness stays a hypothesis.

## References

- ARCHITECTURE.md §1 (the three acts), §3 (invariant 1), §6 (headless Claude
  Code in CI)
- ROADMAP.md M06, including its slip clause: "ship without it and record the
  design as an ADR marked *next* — honesty over vaporware"
- ADR-001 — M06 recorded as a stretch milestone from the start
- ADR-027 — no long-lived keys; the CI role is blocked by the bar and cannot
  move it
- ADR-003 — precedent for recording a rejected option's migration path rather
  than the option
- `pave/agentpave_pave/selfheal.py` and its tests — what shipped
- `docs/VALIDATION.md` — the M06 handoff that named this collision
