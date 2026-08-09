"""The scaffolder, and the render gate that grades its output.

`test_the_rendered_service_passes_its_own_lint_and_tests` is the M04 hermetic
gate. It is the only thing here that could have caught what it caught: the
first version of this template rendered code that failed ruff's import order,
and then code that failed its own formatter because the rendered service had no
lint config and silently inherited an 88-column default while the platform uses
100. Both passed every unit test in this file.

A scaffolder graded only by unit tests is a scaffolder that renders confidently
broken services.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agentpave_pave.cli import TEMPLATE_ROOT
from agentpave_pave.scaffold import ScaffoldError, render, validate

TEMPLATE = TEMPLATE_ROOT / "agent-tools"


# ── validation ────────────────────────────────────────────────────────────


def test_the_package_never_shares_a_name_with_its_directory():
    """ADR-004, rendered. A directory matching a module name shadows it as a
    namespace package, and `sys.path[0]` is the repo root under pytest."""
    spec = validate("catalog-agent", "internal")
    assert spec.package == "agentpave_catalog_agent"
    assert spec.package != spec.name


def test_test_basenames_are_prefixed_by_service():
    # pytest's prepend import mode names modules by basename, so two services
    # each rendering `test_agent.py` collide the moment both exist.
    assert validate("catalog-agent", "internal").test_prefix == "test_catalog_agent"


@pytest.mark.parametrize(
    "name",
    ["Catalog-Agent", "catalog_agent", "9lives", "catalog agent", "", "catalog--agent"],
)
def test_a_name_that_would_break_a_package_or_stack_is_refused(name):
    with pytest.raises(ScaffoldError, match="kebab-case"):
        validate(name, "internal")


def test_an_unknown_classification_is_refused():
    with pytest.raises(ScaffoldError, match="classification"):
        validate("catalog-agent", "top-secret")


def test_sensitive_is_accepted_here_and_refused_at_the_gateway():
    # The scaffolder does not get to decide ADR-001; it has to render a
    # service that asks correctly and is turned down at the boundary.
    assert validate("catalog-agent", "sensitive").classification == "sensitive"


# ── rendering ─────────────────────────────────────────────────────────────


def test_render_refuses_to_overwrite(tmp_path: Path):
    spec = validate("catalog-agent", "internal")
    render(spec, template_root=TEMPLATE, output_root=tmp_path)
    with pytest.raises(ScaffoldError, match="refusing to overwrite"):
        render(spec, template_root=TEMPLATE, output_root=tmp_path)


def test_a_missing_template_fails_before_writing_anything(tmp_path: Path):
    spec = validate("catalog-agent", "internal")
    with pytest.raises(ScaffoldError, match="template not found"):
        render(spec, template_root=TEMPLATE.parent / "no-such-template", output_root=tmp_path)
    assert not (tmp_path / "catalog-agent").exists()


def test_paths_carry_the_substitutions_not_just_file_contents(tmp_path: Path):
    written = render(
        validate("catalog-agent", "internal"), template_root=TEMPLATE, output_root=tmp_path
    )
    rendered = {str(p).replace("\\", "/") for p in written}

    assert "agentpave_catalog_agent/agent.py" in rendered
    assert "tests/test_catalog_agent_agent.py" in rendered
    # No template suffix and no unsubstituted placeholder survives.
    assert not any(".j2" in path or "{{" in path for path in rendered)


def test_no_unsubstituted_placeholder_survives_in_any_file(tmp_path: Path):
    render(validate("catalog-agent", "internal"), template_root=TEMPLATE, output_root=tmp_path)
    for path in (tmp_path / "catalog-agent").rglob("*"):
        if path.is_file():
            assert "{{" not in path.read_text(encoding="utf-8"), f"{path} kept a placeholder"


def test_the_rendered_service_holds_no_bedrock_reference(tmp_path: Path):
    """Invariant 1, at the template level. A scaffolded service that imported a
    Bedrock client would be asking for a permission its role must never hold —
    and the synth assertion would catch it only after someone wrote the IAM."""
    render(validate("catalog-agent", "internal"), template_root=TEMPLATE, output_root=tmp_path)
    for path in (tmp_path / "catalog-agent").rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert "bedrock-runtime" not in text
        assert 'client("bedrock' not in text


def test_the_template_renders_no_pii_shaped_string(tmp_path: Path):
    """Standing rule 3, and ADR-011's carried constraint. The template seeds a
    dataset; a PII-shaped string in it would land in every scaffolded repo."""
    import re

    render(validate("catalog-agent", "internal"), template_root=TEMPLATE, output_root=tmp_path)
    patterns = (
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # national-id shaped
        re.compile(r"\b(?:\d[ -]*?){13,16}\b"),  # card shaped
        re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),  # email shaped
    )
    for path in (tmp_path / "catalog-agent").rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            assert not pattern.search(text), f"{path.name} carries a PII-shaped string"


# ── the render gate ───────────────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
def test_the_rendered_service_passes_its_own_lint_and_tests(tmp_path: Path):
    """The M04 hermetic gate: a clean render must pass its own checks.

    Runs ruff and pytest against the *output*, not the template. Every unit
    test above passed while the template was rendering lint-dirty code; this is
    what noticed.

    `cdk synth` is deliberately not run here — it would make the hermetic gate
    depend on Node and roughly triple its runtime. The rendered stack is
    asserted in `platform/infra/tests` instead, against the same template.
    """
    render(validate("catalog-agent", "internal"), template_root=TEMPLATE, output_root=tmp_path)
    service = tmp_path / "catalog-agent"

    for command in (
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "ruff", "format", "--check", "."],
    ):
        result = subprocess.run(command, cwd=service, capture_output=True, text=True)
        assert result.returncode == 0, (
            f"scaffolded service fails `{' '.join(command[2:])}`:\n{result.stdout}\n{result.stderr}"
        )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=service,
        capture_output=True,
        text=True,
        # The parent environment is preserved and PYTHONPATH added. Replacing
        # it wholesale breaks socket initialisation on Windows (WinError
        # 10106) long before pytest gets a chance to run anything.
        env={**os.environ, "PYTHONPATH": str(service)},
    )
    assert result.returncode == 0, f"scaffolded tests fail:\n{result.stdout}\n{result.stderr}"
