# ADR-004: Components are uv workspace members with distinct import names, not submodules of a `platform` package

**Status:** Accepted
**Date:** 2026-08-07
**Milestone:** M01

## Context

ARCHITECTURE.md §4 fixes the repo layout: components live at
`platform/gateway/`, `platform/registry/`, `platform/evalsvc/`,
`platform/infra/`. The obvious reading — make `platform/` a Python package and
import `platform.gateway.routing` — is unusable. `platform` is a Python
standard-library module, imported transitively by `attrs`, and therefore by
`jsii`, and therefore by all of `aws-cdk-lib`. Any arrangement that puts a
`platform` of ours on the import path is a loaded gun pointed at every CDK
test in the repo.

This is not hypothetical. The first attempt set pytest's
`--import-mode=importlib` specifically to avoid `sys.path` mutation. That mode
derives module names from each file's path relative to rootdir, so
`platform/infra/tests/test_gateway_stack.py` became
`platform.infra.tests.test_gateway_stack`, and the synthesised `platform`
parent package displaced the standard library module in `sys.modules`.
Collection died at `module 'platform' has no attribute 'python_implementation'`.
The mitigation caused the failure it was chosen to prevent.

Renaming the directory was available and rejected: the layout is the spec, and
a milestone that quietly edits ARCHITECTURE.md to dodge a packaging problem
sets a worse precedent than the problem.

## Decision

`platform/` is a directory, never a package. It contains no `__init__.py`, and
adding one is forbidden without a superseding ADR.

Each component under `platform/` is a uv workspace member with its own
`pyproject.toml`, declaring a distribution name of `agentpave-<component>` and
an import package of `agentpave_<component>` — `platform/gateway/` ships
`agentpave_gateway`, `platform/infra/` ships `agentpave_infra`. Members are
listed explicitly in the root `[tool.uv.workspace]` as their milestones land;
globbing is forbidden, so an unfinished component never enters the build by
accident.

pytest runs in its default `prepend` import mode. `--import-mode=importlib` is
forbidden repo-wide for the reason above. Prepend mode names test modules by
basename, which requires those basenames to be unique across the monorepo, so
test files carry their component as a prefix: `test_gateway_stack.py`,
`test_gateway_models.py`, and in later milestones `test_registry_*`,
`test_evalsvc_*`. `platform/gateway/tests/test_gateway_models.py` asserts that
`import platform` still resolves to the standard library, so a regression fails
the hermetic gate rather than surfacing as an unrelated CDK import error.

## Consequences

- The layout in ARCHITECTURE.md §4 survives intact; the fix lives one level
  below it, in packaging.
- Import paths no longer mirror directory paths: the reader of
  `platform/gateway/` must learn that its module is `agentpave_gateway`. This
  is a real and permanent cost, paid on every file in the repo, to buy a
  directory name.
- Each new component costs a `pyproject.toml` and a root-workspace edit — about
  fifteen lines of ceremony per milestone that a single flat package would not
  charge.
- Test basenames are globally constrained. M04 renders a service template with
  its own tests; that template must adopt the same prefixing rule or the
  scaffolded output will collide with the platform's own suite on its first
  `make check`. Recorded here as an M04 checklist item.
- Workspace members are installed editable by `uv sync`, so tests import the
  same code the Lambda asset ships — no `sys.path` shims in `conftest.py`.

## References

- `docs/ARCHITECTURE.md` §4 (repo layout — the constraint this preserves)
- `docs/ROADMAP.md` M01 hermetic gate (`cdk synth` with IAM assertions — the
  suite that the stdlib collision took down)
- [uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/)
- [pytest import modes](https://docs.pytest.org/en/stable/explanation/pythonpath.html)
