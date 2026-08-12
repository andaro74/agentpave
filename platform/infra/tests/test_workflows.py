"""The workflows, asserted structurally.

ROADMAP M05 asks for `actionlint` in the hermetic gate. It runs in CI rather
than in `make check`, because `make check` must pass on a clean clone and
actionlint is a Go binary nobody has by default — the reasoning and the trade
are in ADR-028. These assertions are the half that runs everywhere, and they
cover what actionlint would not anyway: actionlint checks that the YAML is
valid Actions syntax, not that the gate is wired to fail closed.

The properties here are the ones whose absence is silent. A workflow missing
`id-token: write` fails loudly on the first run; a workflow that posts no
comment when the gate blocks, or that lets a fork read the CI role, does not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
GATE = WORKFLOWS / "gate.yml"
NIGHTLY = WORKFLOWS / "nightly-eval.yml"


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gate() -> dict[str, Any]:
    return _load(GATE)


@pytest.fixture(scope="module")
def nightly() -> dict[str, Any]:
    return _load(NIGHTLY)


def _jobs(workflow: dict[str, Any]) -> dict[str, Any]:
    return workflow["jobs"]


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return job.get("steps", [])


# ── both workflows exist and are the ones the spec names ──────────────────


def test_the_spec_named_workflows_exist():
    """ARCHITECTURE.md §4 names `gate.yml` and `nightly-eval.yml`. A workflow
    under a different name is a workflow the spec does not describe."""
    assert GATE.exists()
    assert NIGHTLY.exists()


# ── the gate ──────────────────────────────────────────────────────────────


def test_the_hermetic_levels_run_on_every_pull_request(gate: dict[str, Any]) -> None:
    """No path filter on the hermetic job. It costs nothing and it is the only
    level that runs when someone edits a docstring."""
    hermetic = _jobs(gate)["hermetic"]
    assert "if" not in hermetic, "the hermetic levels must not be conditional"
    assert any("pave gate --hermetic" in str(step.get("run", "")) for step in _steps(hermetic))


def test_the_deployed_levels_run_the_ladder_rather_than_their_own_commands(
    gate: dict[str, Any],
) -> None:
    """The whole point of the ladder being data. A workflow that inlined
    `pave eval --diff` would be a second definition of the gate, free to drift
    from `gate.yml` with nothing to notice."""
    deployed = _jobs(gate)["deployed"]
    assert any("pave gate --aws" in str(step.get("run", "")) for step in _steps(deployed))


def test_the_deployed_job_can_mint_an_oidc_token(gate: dict[str, Any]) -> None:
    """Without `id-token: write` the role cannot be assumed at all. This one
    fails loudly on the first run — asserted anyway, because it is the line
    someone deletes while tightening permissions."""
    assert _jobs(gate)["deployed"]["permissions"]["id-token"] == "write"


def test_no_workflow_carries_a_long_lived_credential(
    gate: dict[str, Any], nightly: dict[str, Any]
) -> None:
    """ADR-027: OIDC, per run, scoped to this repository. An access key in a
    repository secret exists whether or not a workflow is running."""
    for workflow in (gate, nightly):
        rendered = yaml.safe_dump(workflow)
        assert "AWS_ACCESS_KEY_ID" not in rendered
        assert "AWS_SECRET_ACCESS_KEY" not in rendered
        assert "secrets.AWS" not in rendered


def test_the_scorecard_is_posted_even_when_the_gate_blocks(gate: dict[str, Any]) -> None:
    """The failure case is the one that needs the comment.

    A step that only ran on success would leave a blocked pull request with a
    red check and no explanation — the gate at its least useful exactly when
    someone needs it most.
    """
    deployed = _jobs(gate)["deployed"]
    post = next(step for step in _steps(deployed) if "github-script" in str(step.get("uses", "")))
    assert "always()" in post["if"]


def test_the_comment_step_can_write_to_the_pull_request(gate: dict[str, Any]) -> None:
    assert _jobs(gate)["deployed"]["permissions"]["pull-requests"] == "write"


def test_the_deployed_levels_wait_for_the_hermetic_ones(gate: dict[str, Any]) -> None:
    """Spending $0.47 to discover a syntax error is a bill for something the
    free level already knew."""
    assert "hermetic" in _jobs(gate)["deployed"]["needs"]


def test_the_gate_does_not_continue_on_error(gate: dict[str, Any]) -> None:
    """CLAUDE.md standing rule 5, in the one place it could be undone with a
    single key. `gate.yml`'s own comment says adding it would undo the file;
    the same is true here."""
    for job in _jobs(gate).values():
        assert "continue-on-error" not in job
        for step in _steps(job):
            assert "continue-on-error" not in step


# ── the nightly ───────────────────────────────────────────────────────────


def test_the_nightly_is_scheduled_and_runnable_by_hand(nightly: dict[str, Any]) -> None:
    # `True` because YAML parses a bare `on:` key as the boolean, which is a
    # well-known Actions footgun and the reason this is read by key rather than
    # by name.
    triggers = nightly[True] if True in nightly else nightly["on"]
    assert triggers["schedule"]
    assert "workflow_dispatch" in triggers


def test_the_nightly_cannot_move_the_bar(nightly: dict[str, Any]) -> None:
    """A nightly that re-baselined every night would erase the drift it exists
    to detect. Enforced in IAM by ADR-027; asserted here because the tempting
    fix for a noisy nightly is to add `--save-baseline` to it."""
    rendered = yaml.safe_dump(nightly)
    assert "--save-baseline" not in rendered
    assert "seed-baseline" not in rendered
