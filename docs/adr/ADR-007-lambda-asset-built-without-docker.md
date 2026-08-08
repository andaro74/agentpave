# ADR-007: The Lambda asset is built by cross-platform wheel download, not by Docker bundling

**Status:** Accepted
**Date:** 2026-08-07
**Milestone:** M01

## Context

The gateway Lambda needs pydantic and PyYAML at runtime. CDK's idiomatic answer
is `aws-lambda-python-alpha`'s `PythonFunction`, which pip-installs the
dependencies inside a Docker container during **synthesis**.

That is fatal here. Standing rule 4 says `make check` is hermetic and fast, and
`make check` runs `cdk synth`. Docker bundling would put a running Docker daemon
on the critical path of the hermetic gate — on every contributor's laptop and in
every CI job — to produce a CloudFormation template whose IAM assertions do not
depend on the asset's contents at all. The gate would become slow, and it would
fail for a reason unrelated to anything it is supposed to be testing.

The dependencies themselves are the reason a plain `Code.from_asset` on the
source tree is not enough: `pydantic-core` is a compiled extension, so the asset
must carry a wheel built for the Lambda runtime's platform — `aarch64` Linux,
CPython 3.12 — which is not the platform anyone here develops on.

## Decision

The asset is built by an explicit `make build-gateway` step that resolves
platform-specific wheels without executing them:

```
uv pip install --target build/gateway \
    --python-platform aarch64-manylinux2014 --python-version 3.12 \
    --only-binary=:all: pydantic pyyaml
```

`--only-binary=:all:` is load-bearing rather than an optimisation: without it, a
missing wheel would silently fall back to a source build against the *host*
toolchain and produce an x86-64 Windows artefact that fails at cold start with an
import error. Failing the build is the correct outcome.

boto3 is deliberately not vendored — the Lambda runtime provides it. It stays a
declared dependency of `agentpave-gateway` because the unit tests import it.

The CDK app reads `AGENTPAVE_GATEWAY_ASSET` and defaults to the plain source
tree. `cdk synth` therefore needs no build step, and `make deploy-dev` depends
on `build-gateway` and points the variable at the built directory. Adding a
Docker-bundling construct to any stack is forbidden without a superseding ADR.

Host-built `__pycache__` directories are deleted after the copy: they are the
wrong platform, and leaving them makes the asset hash differ between machines
for no change in behaviour.

## Consequences

- `make check` stays hermetic and needs no Docker, which is the whole point.
- **Synth and deploy build different assets.** The template `make check`
  produces is not byte-identical to the one `make deploy-dev` produces — the
  asset hash differs. The IAM assertions are unaffected (they read the policy
  document, not the asset), but anyone diffing synthesised templates across the
  two paths will see a spurious change, and a future test that asserted on the
  asset hash would be wrong in a confusing way.
- Nothing verifies that the vendored wheels actually import on the target
  platform until the first cold start. `make check` cannot catch a bad wheel;
  `make smoke-gateway` is what catches it, and the failure will look like a
  Lambda import error rather than anything about wheels.
- The dependency list lives in the Makefile rather than being derived from
  `platform/gateway/pyproject.toml`. Adding a runtime dependency to the gateway
  means editing two places, and forgetting the second one produces a cold-start
  import error, not a test failure. Deriving it automatically is the obvious
  improvement and is deliberately not done in M01 — it is M07 polish, and the
  duplication is two lines with one consumer.
- The approach is bounded by the 250 MB unzipped Lambda limit. The current asset
  is ~10 MB, so there is no pressure now; a component that outgrows it needs a
  layer or a container image, and that gets its own ADR.

## References

- `docs/ROADMAP.md` M01 hermetic gate (`cdk synth`, no AWS account)
- `CLAUDE.md` standing rule 4 (fixtures over live calls; `make check` stays fast)
- [ADR-003](ADR-003-lambda-over-agentcore-runtime.md) — Lambda as the runtime this packages for
- [uv: installing for a different platform](https://docs.astral.sh/uv/pip/compatibility/)
