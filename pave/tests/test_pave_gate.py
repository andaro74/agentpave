"""The gate runner, and the two shipped ladders.

Every test drives `run` with a stub runner. Nothing here executes a level —
the hermetic gate cannot run `make check` inside itself, and a test that
shelled out would be measuring the machine rather than the ladder.

The cluster that matters most is fail-closed. A gate that reports green having
run nothing is the failure this milestone is built to prevent, and it has three
plausible shapes: a ladder that would not load, a level that could not launch,
and a `fail_closed: false` nobody reviewed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from agentpave_pave.gate import (
    GateError,
    GateLevel,
    blocked,
    load,
    report,
    run,
    select,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_GATE = REPO_ROOT / "gate.yml"
SERVICE_GATE = REPO_ROOT / "services" / "catalog-agent" / "gate.yml"

VALID = {
    "service": "a-service",
    "classification": "internal",
    "fail_closed": True,
    "levels": [
        {
            "id": "L0",
            "name": "unit",
            "blocking": True,
            "needs_aws": False,
            "command": "pytest -q",
            "why": "because",
        },
        {
            "id": "L2",
            "name": "eval",
            "blocking": True,
            "needs_aws": True,
            "command": "pave eval --diff",
            "why": "because",
        },
    ],
}


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "gate.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _level(**overrides) -> GateLevel:
    base = {
        "id": "L0",
        "name": "unit",
        "blocking": True,
        "needs_aws": False,
        "command": "pytest -q",
        "why": "because",
    }
    return GateLevel.model_validate({**base, **overrides})


# ── fail closed ───────────────────────────────────────────────────────────


def test_a_ladder_that_will_not_load_raises_rather_than_running_nothing():
    """Zero levels and a green verdict is the worst output this could produce."""
    with pytest.raises(GateError):
        load(Path("no-such-gate.yml"))


def test_fail_closed_false_is_refused_at_load(tmp_path: Path):
    """The one key in the file that could disable every other line of it.

    Refused rather than honoured, because a gate that does not block is a
    report — and the difference has to be visible in a diff, not discovered
    when something ships.
    """
    with pytest.raises(GateError, match="fail_closed"):
        load(_write(tmp_path, {**VALID, "fail_closed": False}))


def test_a_level_that_cannot_launch_fails_and_is_named_as_a_broken_gate():
    """M02's false pass in miniature: a command that never ran is the most
    tempting thing to treat as "not applicable"."""

    def runner(_command: str) -> int:
        raise FileNotFoundError("make: not found")

    results = run([_level()], runner)
    assert not results[0].passed
    assert "FileNotFoundError" in (results[0].launch_error or "")
    assert blocked(results)
    assert "could not run" in report(load(PLATFORM_GATE), results)


def test_an_unknown_key_is_rejected_rather_than_ignored(tmp_path: Path):
    """`continue_on_error` is the key someone will reach for first, and a
    loader that ignored unknown keys would accept it silently."""
    broken = {**VALID, "levels": [{**VALID["levels"][0], "continue_on_error": True}]}
    with pytest.raises(GateError):
        load(_write(tmp_path, broken))


# ── running the ladder ────────────────────────────────────────────────────


def test_every_level_runs_even_after_one_blocks():
    """One red level says the gate blocked; the whole ladder says how much is
    broken. Stopping early costs a CI run to find out the rest."""
    seen: list[str] = []

    def runner(command: str) -> int:
        seen.append(command)
        return 1

    results = run([_level(id="L0"), _level(id="L2", command="pave eval")], runner)
    assert seen == ["pytest -q", "pave eval"]
    assert blocked(results)


def test_a_non_blocking_failure_does_not_block():
    results = run([_level(blocking=False)], lambda _c: 1)
    assert not results[0].passed
    assert not blocked(results)


def test_levels_are_selected_by_whether_they_need_an_account(tmp_path: Path):
    """The split the workflow runs on: hermetic levels on every push, deployed
    levels only when something they can grade has changed."""
    config = load(_write(tmp_path, VALID))
    assert [level.id for level in select(config, needs_aws=False)] == ["L0"]
    assert [level.id for level in select(config, needs_aws=True)] == ["L2"]
    assert len(select(config)) == 2


def test_selecting_nothing_reports_that_it_ran_nothing():
    """An empty selection is a legitimate state — a hermetic-only run of an
    all-AWS ladder — and it must not render as a pass with no evidence."""
    rendered = report(load(PLATFORM_GATE), [])
    assert "no levels selected" in rendered


# ── the two ladders this repo ships ───────────────────────────────────────


@pytest.mark.parametrize("path", [PLATFORM_GATE, SERVICE_GATE])
def test_the_shipped_ladders_load(path: Path):
    config = load(path)
    assert config.fail_closed
    assert config.levels


@pytest.mark.parametrize("path", [PLATFORM_GATE, SERVICE_GATE])
def test_every_level_is_blocking(path: Path):
    """Neither ladder currently has a non-blocking rung. The runner supports
    one, because a future level may earn its way in before it earns the right
    to block — but a level that quietly stopped blocking should be a visible
    edit, and this is what makes it one."""
    assert all(level.blocking for level in load(path).levels)


@pytest.mark.parametrize("path", [PLATFORM_GATE, SERVICE_GATE])
def test_every_level_says_why_it_exists(path: Path):
    """`why` is read by whoever inherits the gate. A rung nobody can justify is
    a rung that gets deleted the first time it is inconvenient."""
    for level in load(path).levels:
        assert len(level.why) > 40, f"{level.id} does not explain itself"


def test_the_platform_ladder_grades_the_judged_dataset():
    """Not the service's five deterministic cases.

    A suite of substring asserts cannot detect a change in answer quality, and
    M05's claim is a gate that blocks one. `--diff` is what turns a pass count
    into an answer to "did this change make it worse".
    """
    commands = " ".join(level.command for level in load(PLATFORM_GATE).levels)
    assert "pave eval --diff" in commands
    assert "--dataset" not in commands


def test_the_service_ladder_grades_its_own_dataset():
    """The template's promise: a scaffolded service ships a gate that runs
    against the seed dataset it also ships, or that dataset is decoration."""
    commands = " ".join(level.command for level in load(SERVICE_GATE).levels)
    assert "--dataset services/catalog-agent/eval" in commands
