# ADR-027: CI is blocked by the bar and cannot move it

**Status:** Accepted
**Date:** 2026-08-11
**Milestone:** M05

## Context

M05 puts the quality gate in CI, and two of its four levels need AWS. That
requires giving a workflow credentials, and the shape of those credentials
decides two separate things: how they can leak, and what a run can do once it
has them.

The leak question has a settled answer. An access key in a repository secret
exists whether or not a workflow is running, cannot be scoped to a branch, and
outlives every rotation policy nobody remembers. GitHub's OIDC provider issues a
token per run instead, and there is nothing persistent to steal.

The second question is the one this platform has to answer for itself. The gate
compares a run against a recorded baseline, and `--save-baseline` writes that
baseline. If the CI role can write it, then a run that scored badly can record
itself as the new standard, and the next run "improves" on a regression while
the diff reports no problem.

That is not hypothetical. `is_recordable` exists because M03's teeth
demonstration deliberately broke the service, scored 19/30, and `--save-baseline`
wrote it down as the bar; the row is still in the table at 0.633. The guard
inside the process now refuses a failing run — but it is a Python function in the
same process as the code being graded, and M05 is the milestone that puts that
process on an automatic trigger nobody watches.

## Decision

**The CI role holds no `dynamodb:PutItem`, `UpdateItem`, or `DeleteItem`. It can
read the baseline and be blocked by it; it cannot move it.** Setting the bar
stays a deliberate human act — `make seed-baseline`, run from a laptop by
someone who decided the current scores deserve to be the standard.

`is_recordable` remains, and this does not replace it. One is a policy in the
process; the other is a permission the process cannot exceed. A defect in the
first is contained by the second, which is the only reason to have both.

**The CI role also cannot deploy** — no `cloudformation:CreateStack` or
`UpdateStack`, no `iam:*`. A gate that can change the infrastructure it grades
is a gate that can make itself pass. It reads stack outputs to find the
deployment, which is all `pave eval` needs.

**The identity lives in its own stack, `AgentPave-Ci`.** CLAUDE.md enumerates
one stack per component — gateway, registry, evalsvc, service-`<name>` — and CI
identity is none of them. Folding it into the eval stack would tie the
credential CI depends on to the deploy lifecycle of a component it has no
relationship with, and `make destroy-dev` would take CI's ability to
authenticate with it. The stack is not stage-suffixed: an OIDC provider is an
account-level singleton, so a second stage would fail on the duplicate rather
than get its own.

**Trust is scoped to two subjects**, `repo:<owner>/<name>:pull_request` and
`repo:<owner>/<name>:ref:refs/heads/main`, not `repo:<owner>/<name>:*`. The
wildcard also matches `ref:refs/tags/*`, so anyone able to push a tag could
assume the role.

## Consequences

The bar cannot drift downward on its own. A degrading platform produces red
builds until a person looks at them and decides, rather than a baseline that
follows the decline down and reports no regression at any step.

The cost is a manual step in a milestone about automation, and it is a real one.
Seeding requires a laptop, AWS credentials, and about $0.47 — so the bar will be
re-seeded less often than it could be, and will sometimes be staler than the
platform. A stale bar makes the diff less informative: improvements stop
showing up as improvements once the baseline is behind. That is the trade, and
the direction of the risk is deliberate — a bar that is too old blocks
pull requests that should pass, which someone notices, while a bar that follows
the platform down blocks nothing and nobody notices.

It also means the nightly run cannot re-baseline, so nightly is a report rather
than a ratchet. Whether it should open an issue on a regression is left to the
workflow and not decided here.

Scoping trust to two subjects will break the first time a workflow runs on
something else — a tag build, a release environment, a merge queue. The failure
is a clear `AssumeRoleWithWebIdentity` denial rather than a silent one, and
widening it is a deliberate edit to this stack.

## References

- ARCHITECTURE.md §3 (eval service, baseline store) and §4 (`platform/infra`
  holds all stacks; `.github/workflows/`)
- ADR-002 — nothing bills while idle; the OIDC provider is native
  CloudFormation rather than a custom resource, so no Lambda lingers to manage it
- ADR-025 — an IAM-authed function URL needs both invoke actions; the CI role
  carries both for the same reason the scaffolded service does
- ROADMAP M05 — the gate bites
- `docs/VALIDATION.md` — M03's teeth row, and the 0.633 baseline it left behind
