# ADR-039: The clean-stack teardown spares `AgentPave-Ci`

**Status:** Accepted
**Date:** 2026-08-14
**Milestone:** M07

## Context

ROADMAP M07's deployed gate asks for a full walkthrough "from `make deploy-dev` on
a clean stack" and for evidence that "`make destroy-dev` leaves nothing billing".
Running it means tearing the account down first, and `make destroy-dev` is
`cdk destroy --all` — which includes `AgentPave-Ci`.

That stack is not like the other five. It holds the GitHub OIDC provider and the
role both workflows assume, and the role's name is **CloudFormation-generated**:
`AgentPave-Ci-CiRole5A6E8228-sfjQ0U4K5fSE`. Destroying and recreating it produces
a different suffix, so the GitHub repository variable `AWS_CI_ROLE_ARN` — read by
`gate.yml` and `nightly-eval.yml` — would point at a role that no longer exists.
`main` is protected by ADR-034's required `gate verdict` check, so on a public
repository every pull request would wait on a check that cannot run until a human
noticed and pasted a new ARN in.

This is ADR-034's shape a second time: a reference living outside this repository
that nothing inside it can pin. Nothing in the Makefile, and no ADR before this
one, mentioned it. The five stage stacks carry the opposite property — they hold
every piece of state the demo actually asserts about, and all of it is
`removal_policy=DESTROY` by ADR-002's reasoning, so destroying them is the point.

## Decision

The M07 clean-stack teardown destroys **the five stage stacks only** —
`AgentPave-Gateway-dev`, `AgentPave-Mcp-dev`, `AgentPave-Eval-dev`,
`AgentPave-Dashboard-dev`, `AgentPave-Service-CatalogAgent-dev`. `AgentPave-Ci`
stands.

The clean-stack claim this project makes is about **the platform the paved road
deploys**, not about account-level identity plumbing. `AgentPave-Ci` holds no
per-stage state, bills nothing while idle, and is a precondition for CI rather
than a product of it.

`make destroy-dev` is **not** changed to exclude the CI stack. A verb that quietly
skips a stack is a verb whose name has stopped being true, and the next reader
would have to discover the exception from its recipe. The exception is a property
of this one teardown, taken deliberately and recorded here.

Any future full `cdk destroy --all` **must** be followed by reading the new role
ARN and updating the `AWS_CI_ROLE_ARN` repository variable before the next push to
`main`. That is a checklist item for whoever runs it, not something this
repository can enforce.

## Consequences

**The deployed gate's teardown half is exercised in part, not in full.** Five of
six stacks were destroyed. The review row says so rather than implying `make
destroy-dev` ran end to end, and the "nothing billing" evidence is correspondingly
a claim about the five — which is where all of the billing was.

**CI keeps working across the teardown**, so the graded levels and the required
check survive a window in which the platform they grade does not exist. ADR-028's
path filter is what makes that safe: a change touching no gradeable path skips
L2/L5 rather than failing them against absent stacks.

**The cost: a real failure mode is documented and not fixed.** `make destroy-dev`
still silently orphans `AWS_CI_ROLE_ARN` for anyone who runs it as written, and
this ADR is the only place that says so. A fix exists — give the role an explicit
`role_name` so it is stable across recreation — and is deliberately not taken in
M07, because a named IAM role is a new global uniqueness constraint on an account
that already carries five unrelated projects, and changing it now would replace
the role in the middle of the milestone that depends on it. Revisit if the CI
stack is ever recreated for any other reason.

**Nothing hermetic can catch a recurrence.** The pairing lives half in a
CloudFormation-generated name and half in a GitHub variable, and `make check` can
see neither — the same un-assertable half ADR-034 settled for and ADR-026 settled
for before it. The mitigation is that the trap is now written down where the next
teardown will be planned.

## References

- ROADMAP.md M07 deployed gate (clean-stack deploy; `destroy-dev` leaves nothing billing)
- ADR-002 (nothing bills while idle) — why the stage stacks destroy cleanly
- ADR-028 (deployed levels run on changed paths only) — what keeps CI green while the platform is gone
- ADR-034 (a verdict job is the required check) — the precedent for a reference this repo cannot pin
- ADR-026 (adversarial suite tests no tool authorization) — the precedent for a gap recorded rather than closed
- `Makefile` (`destroy-dev`), `.github/workflows/gate.yml`, `.github/workflows/nightly-eval.yml`
