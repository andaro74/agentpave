# ADR-026: The adversarial suite keeps no tool-authorization probe, and the walkthrough is not a substitute

**Status:** Accepted (supersedes ADR-015's M04 commitment)
**Date:** 2026-08-11
**Milestone:** M05

## Context

ADR-015 removed `injection-tool-escalation` from `cases/adversarial.yaml`
because it asserted a Cedar denial from a request path with no Cedar in it, and
committed the probe to return **in M04** "with the walkthrough that gives it an
agent to escalate against".

M04 closed without it. The commitment was recorded in an ADR, a file comment,
and nowhere that could fail — no test, no gate, no checklist item. M05's
curation pass found it by reading the comment.

The reason it did not return is that M04 did not actually remove the obstacle.
The adversarial harness still calls the **gateway** directly with a
`feature_id`; Cedar still runs in the MCP server behind the agent. M04 built
the agent and the walkthrough, but no route from this suite through it. Adding
the probe back today would recreate precisely the unrunnable probe ADR-015
exists to forbid — a probe naming a control its own request cannot reach.

What M04 did build is `make walkthrough`'s `guarded` act, which drives the real
agent path and asserts the platform refused rather than the model declining. It
is genuine coverage of the agent's request path. It is not coverage of tool
authorization: it exercises the gateway's guardrail on an injected question,
and never asks the agent for a tool it does not hold.

## Decision

**The adversarial suite ships no tool-authorization probe, and `make eval
--adversarial-only` therefore tests none. The walkthrough is not recorded as
covering this gap.**

Claiming otherwise is the failure this platform keeps finding in itself — M02's
conformance driver reporting passes against a wall, M04's `traced` act vouching
for telemetry that did not exist. A gap described as covered is worse than a
gap described as open, because only one of them gets fixed.

**Closing it properly requires an adversarial driver that sends through the
agent rather than the gateway**, so that a probe asking for an ungranted tool
reaches Cedar and is denied by identity. That is a new caller in
`adversarial.py` and a probe shape that names an agent endpoint — real work,
not a case edit.

**That work is unscheduled, and this ADR does not assign it to a milestone.**
The first draft sent it to M06, which was wrong twice over: M06 is self-healing
and shadow-eval, so the driver does not belong to it on scope; and M06 is
marked *(stretch)* with a roadmap entry that contemplates shipping without it,
so a gap parked there has no committed home at all.

**A commitment in an ADR is forbidden without something that fails when the
milestone closes.** This is the second-order lesson and the more useful one:
ADR-015's promise was unfalsifiable by construction — a file comment and an ADR
paragraph, neither of which can go red. Any ADR deferring work must name the
test, gate, or checklist row that turns red if the milestone closes without it.

So this one names its own: the gap is recorded as an open item in
`docs/VALIDATION.md`, and **M07's close must either resolve it or restate it in
the README's known-limits section**. M07 is committed rather than stretch, and
its gates already require `docs/VALIDATION.md` to be reviewed and the known
limits to be published — so the row is read by a gate that runs, not by a
reader who might.

## Consequences

The honest state is now written where the number is read: the adversarial
score is nine probes of guardrail, classification and screening, plus one of
encoding, and none of authorization. Anyone reading `9/10` knows what it does
not include.

The cost is that M05's gate ships with a known blind spot, and M05 is the
milestone whose entire point is that the gate bites. A pull request that
widened a Cedar policy — the single most dangerous change a service can make in
this platform — would pass every level of the ladder. That is a real hole in a
milestone named "the gate bites", and it is being accepted rather than closed
because the driver is a new caller and probe shape rather than a case edit, and
because the alternative was a probe that cannot pass.

Leaving it unscheduled is itself a cost, and the honest one to name: work with
no milestone is work that competes with whatever is in front of it. The
VALIDATION row and M07's known-limits gate are what stop that from meaning
"never", and they are weaker than a milestone would be.

It also foregoes the cheap fix. Re-adding the probe would make the suite look
complete for one line of YAML, and several people would have to discover
independently that the green was meaningless.

## References

- ADR-015 — the removal and the M04 commitment this supersedes
- ADR-008 — Cedar in-process, and how identity is derived
- ARCHITECTURE.md invariant 5 — adversarial passes mean the platform stopped it
- ROADMAP M05 — the gate bites; M07 — where the known limits are published
- `docs/VALIDATION.md` — M04's still-open items
