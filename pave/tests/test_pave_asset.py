"""What the scaffolded service imports, versus what its asset will contain.

This file exists because of one deployed run. M04's first `make walkthrough`
returned a bare `502 Internal Server Error` from three acts, and the cause was
that `requests` — declared in the rendered `pyproject.toml`, imported at module
scope in two rendered modules — was vendored by nothing. `make build` built the
gateway and MCP assets; `deploy-dev` never set `AGENTPAVE_SERVICE_ASSET`; the
CDK app fell back to plain source; the function died at import with no line of
our code run.

Nothing in `make check` could see it. The render gate runs the scaffolded tests
in a virtualenv where `requests` is installed, and the scaffolded tests
monkeypatch the network anyway. The gap was never "is the code right" — it was
"will the code's imports resolve where it actually runs", and that is a
different question that needs its own test.

The join being tested is three-way: what the rendered package imports, what the
rendered `pyproject.toml` declares, and what the Makefile vendors. Any two of
them agreeing is not enough.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest
from agentpave_pave.cli import REPO_ROOT, TEMPLATE_ROOT
from agentpave_pave.scaffold import render, validate

TEMPLATE = TEMPLATE_ROOT / "agent-tools"

# What the Python 3.12 Lambda runtime provides without vendoring. Short by
# design: AWS documents boto3 and botocore and nothing else, and treating
# anything further as "probably there" is how a service ships an import it
# cannot satisfy. urllib3 arrives inside botocore and is deliberately not
# listed — importing it directly would be relying on a transitive dependency
# AWS is free to drop.
RUNTIME_PROVIDED = frozenset({"boto3", "botocore"})

# Import name → distribution name, where they differ.
DISTRIBUTION = {"opentelemetry": "opentelemetry-api"}


@pytest.fixture
def service(tmp_path: Path) -> Path:
    render(validate("catalog-agent", "internal"), template_root=TEMPLATE, output_root=tmp_path)
    return tmp_path / "catalog-agent"


def _third_party_imports(package_dir: Path) -> set[str]:
    """Top-level third-party modules imported anywhere in the rendered package.

    Function-local imports count. `telemetry.py` imports OpenTelemetry inside
    functions precisely so the service starts without it — which makes the
    failure quieter, not less real: a missing package there produces a service
    that answers correctly and silently emits no telemetry at all.
    """
    found: set[str] = set()
    for path in sorted(package_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])

    return {
        name for name in found if name not in sys.stdlib_module_names and not name.startswith("_")
    }


def _declared(service_dir: Path) -> set[str]:
    data = tomllib.loads((service_dir / "pyproject.toml").read_text(encoding="utf-8"))
    return {re.split(r"[<>=!\[]", dep)[0].strip() for dep in data["project"]["dependencies"]}


def _vendored() -> set[str]:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(r"^SERVICE_DEPS\s*:?=\s*(.+)$", makefile, re.MULTILINE)
    assert match, "the Makefile no longer declares SERVICE_DEPS"
    return set(match.group(1).split())


def test_every_import_the_service_makes_is_declared(service: Path):
    """Declared, or provided by the runtime. Nothing else is safe to import."""
    imported = _third_party_imports(service / "agentpave_catalog_agent")
    declared = _declared(service)

    undeclared = {
        name
        for name in imported
        if name not in RUNTIME_PROVIDED and DISTRIBUTION.get(name, name) not in declared
    }
    assert not undeclared, (
        f"the rendered service imports {sorted(undeclared)}, which its pyproject does not "
        "declare — it would die at import in Lambda"
    )


def test_every_declared_dependency_is_vendored_or_provided(service: Path):
    """The join that actually broke.

    `requests` was declared here and vendored nowhere. Everything looked
    consistent from inside the template, and the function died at import.
    """
    declared = _declared(service)
    vendored = _vendored()

    missing = {name for name in declared if name not in RUNTIME_PROVIDED and name not in vendored}
    assert not missing, (
        f"the rendered service declares {sorted(missing)}, which the Makefile's SERVICE_DEPS "
        "does not vendor — the asset would ship without it and the function would 502 at "
        "import, with a bare 'Internal Server Error' and no line of our code run"
    )


def test_boto3_is_never_vendored():
    """The other direction. The runtime provides boto3 and vendoring a second
    copy is dead weight in the asset and a version that can silently diverge
    from the one AWS patches."""
    assert not (_vendored() & RUNTIME_PROVIDED)


def test_opentelemetry_is_a_hard_dependency_not_an_extra(service: Path):
    """ADR-024, pinned where it can regress.

    It shipped as an optional extra, nothing vendored it, `_tracer()` returned
    None in the deployed function, and no span was ever emitted — while the
    walkthrough's `traced` act reported the service as traced, because it was
    reading Lambda's own X-Ray segments. Telemetry that is silently absent is
    worse than telemetry that is missing loudly, because a gate will vouch for
    it.
    """
    data = tomllib.loads((service / "pyproject.toml").read_text(encoding="utf-8"))
    declared = _declared(service)

    assert "opentelemetry-sdk" in declared
    assert "opentelemetry-api" in declared
    assert "telemetry" not in data.get("project", {}).get("optional-dependencies", {})


def test_the_service_asset_is_built_and_wired_into_deploy():
    """A build target nothing calls, or an asset variable nothing sets, leaves
    the CDK app falling back to plain source. That fallback synthesises, passes
    every assertion, deploys without complaint, and then 502s."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert re.search(r"^build:.*build-service", makefile, re.MULTILINE), (
        "`make build` does not build the service asset"
    )
    deploy = re.search(r"^deploy-dev:.*?(?=\n\.PHONY)", makefile, re.MULTILINE | re.DOTALL)
    assert deploy and "AGENTPAVE_SERVICE_ASSET" in deploy.group(0), (
        "`make deploy-dev` does not point the service stack at the built asset"
    )
