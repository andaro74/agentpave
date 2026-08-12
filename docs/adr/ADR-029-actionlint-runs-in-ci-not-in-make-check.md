# ADR-029: actionlint runs in CI, and `make check` asserts the workflows structurally

**Status:** Accepted
**Date:** 2026-08-11
**Milestone:** M05

## Context

ROADMAP M05 names the hermetic gate as "workflow lints (`actionlint`)".
`actionlint` is a Go binary. `make check` is required to pass on a clean clone
with `uv` and nothing else (CLAUDE.md standing rule: the hermetic gate needs no
AWS account, and by extension no toolchain the repo does not declare).

Adding `actionlint` to `make check` therefore has three shapes, and two of them
are worse than not having it:

- **Require it.** `make check` fails on every machine without a Go binary
  nobody was told to install, including the first clone of a reader following
  the README.
- **Skip when absent.** A gate that skips is a gate that reports green having
  checked nothing — the failure mode this project has now found three times
  (M02's conformance driver, M04's `traced` act, M04's unwired service).
- **Install it.** `make check` downloads a binary, which puts the network on
  the critical path of the hermetic gate.

## Decision

**`actionlint` runs in the `hermetic` job of `.github/workflows/gate.yml`,
where installing a binary is what a runner is for.** It lints every workflow on
every pull request, and it blocks.

**`make check` asserts the workflows structurally instead**, in
`platform/infra/tests/test_workflows.py`, by parsing the YAML and checking the
properties whose absence is silent: that the deployed job can mint an OIDC
token, that the scorecard is posted even when the gate blocks, that no workflow
carries a long-lived credential, that the deployed levels run the ladder rather
than inlining their own commands, and that nothing sets `continue-on-error`.

These are not the same check and neither substitutes for the other.
`actionlint` verifies the file is valid Actions syntax; it has no opinion about
whether the gate fails closed. The structural tests verify the gate's
behaviour; they would happily pass on a file GitHub refuses to parse. Both run
on every pull request; one of them also runs on a laptop.

## Consequences

`make check` stays true to its claim — clean clone, `uv`, no network beyond
localhost, no undeclared toolchain — and it gains the workflow assertions that
matter most for this milestone.

The cost is that a workflow syntax error is caught one step later than the
ROADMAP intended: on the pull request rather than before the push. For a file
edited a handful of times per milestone that is a small delay, and the feedback
is still automatic and blocking. But it does mean `make check` green is no
longer sufficient to know the workflows are well-formed, and someone editing
YAML on a plane will find out when they land.

It also means the hermetic job downloads `actionlint` on every run, which is a
network dependency inside CI and a supply-chain surface: the install script is
fetched from the project's `main` branch rather than pinned to a release. That
is worth tightening the first time it matters, and is recorded here rather than
left as an unexamined convenience.

## References

- ROADMAP M05 — "Hermetic gate: workflow lints (`actionlint`)"
- CLAUDE.md standing rules 3 and 4 — `make check` stays hermetic and fast
- ADR-028 — the path filter, whose skip-logic these tests also cover
- `platform/infra/tests/test_workflows.py`
