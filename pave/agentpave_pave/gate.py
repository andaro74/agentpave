"""Running a quality ladder declared in a `gate.yml`.

Both `gate.yml` files in this repo open by insisting they are data, not
workflows. This is what makes that true: one runner reads a ladder and executes
it, so the platform's gate and a scaffolded service's gate cannot drift onto
different machinery, and the ladder's shape can be asserted by a test that
never parses GitHub Actions YAML.

The split that matters is `needs_aws`. Levels that need an account are selected
separately from the ones that do not, because the workflow runs them in
different jobs under different conditions — the hermetic levels on every push,
the deployed ones only when something they can actually grade has changed
(ADR-028). A runner that could not make that distinction would force the choice
of paying for an eval on a README edit or not running the gate at all.

Fail closed, in the specific sense CLAUDE.md standing rule 5 means: a level that
*errors* is a failed level, never a skipped one. A command that cannot be
launched at all — a missing binary, a bad path — is the most tempting thing in
the world to treat as "not applicable", and it is the exact shape of M02's
false pass.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class GateError(RuntimeError):
    """A ladder that cannot be read. Never a silently empty ladder.

    An unreadable `gate.yml` that produced zero levels would report a green
    gate having run nothing, which is worse than any failure it could report.
    """


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GateLevel(_Strict):
    """One rung. `why` is required and read by humans, not by code."""

    id: str = Field(min_length=1, pattern=r"^L\d+$")
    name: str = Field(min_length=1)
    blocking: bool
    needs_aws: bool
    command: str = Field(min_length=1)
    why: str = Field(min_length=1)


class GateConfig(_Strict):
    """A whole ladder, as `gate.yml` declares it."""

    service: str = Field(min_length=1)
    classification: Literal["internal", "sensitive"]
    fail_closed: bool
    levels: tuple[GateLevel, ...] = Field(min_length=1)


@dataclass(frozen=True)
class LevelResult:
    level: GateLevel
    exit_code: int
    # Set when the command could not be launched at all, as opposed to running
    # and failing. Both block; only one of them means "fix your gate".
    launch_error: str | None = None

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and self.launch_error is None


# What actually runs a level. Injected so the whole ladder is testable without
# executing anything — the hermetic gate cannot run `make check` inside itself.
Runner = Callable[[str], int]


def load(path: Path) -> GateConfig:
    """Read and validate a ladder."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GateError(f"no gate at {path}") from exc
    except yaml.YAMLError as exc:
        raise GateError(f"gate at {path} is not readable YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise GateError(f"gate at {path} is not a mapping")

    try:
        config = GateConfig.model_validate(raw)
    except ValidationError as exc:
        raise GateError(f"gate at {path} is not usable: {exc}") from exc

    if not config.fail_closed:
        # The one value in the file that could quietly disable everything else.
        raise GateError(
            f"gate at {path} sets fail_closed: false — a gate that does not block "
            "is a report, and this runner will not pretend otherwise"
        )
    return config


def select(config: GateConfig, *, needs_aws: bool | None = None) -> tuple[GateLevel, ...]:
    """The levels to run. `None` means all of them, in declared order."""
    if needs_aws is None:
        return config.levels
    return tuple(level for level in config.levels if level.needs_aws is needs_aws)


def run_level(level: GateLevel, runner: Runner) -> LevelResult:
    """One level, with a launch failure kept distinct from a test failure."""
    try:
        return LevelResult(level=level, exit_code=runner(level.command))
    except Exception as exc:  # noqa: BLE001 — a gate that cannot run has failed
        return LevelResult(level=level, exit_code=1, launch_error=f"{type(exc).__name__}: {exc}")


def run(levels: Sequence[GateLevel], runner: Runner) -> list[LevelResult]:
    """Every level, in order, and no early exit.

    A blocking failure does not stop the ladder. One red level tells you the
    gate blocked; the whole ladder tells you how much else is broken, and
    finding that out costs one more CI run otherwise. The verdict is computed
    after, by `blocked`.
    """
    return [run_level(level, runner) for level in levels]


def blocked(results: Sequence[LevelResult]) -> bool:
    """Whether the gate blocks. Only `blocking` levels can."""
    return any(not result.passed and result.level.blocking for result in results)


def report(config: GateConfig, results: Sequence[LevelResult]) -> str:
    """The ladder, as the workflow log shows it."""
    lines = [f"quality gate — {config.service} ({config.classification})"]
    for result in results:
        mark = "✅" if result.passed else "❌"
        suffix = "" if result.level.blocking else " (non-blocking)"
        lines.append(f"  {mark} {result.level.id} {result.level.name}{suffix}")
        if result.launch_error:
            # Named separately because it means the gate is broken, not the
            # code — and the fix is in a different file.
            lines.append(f"       could not run: {result.launch_error}")
        elif not result.passed:
            lines.append(f"       `{result.level.command}` exited {result.exit_code}")

    if not results:
        lines.append("  no levels selected")
    lines.append("")
    lines.append("❌ gate blocked" if blocked(results) else "✅ gate passed")
    return "\n".join(lines)


def shell_runner_in(directory: Path) -> Runner:
    """Run levels for real, from the directory the ladder sits in.

    The working directory is the gate file's own, not wherever the process was
    launched. A `gate.yml` describes the ladder for the thing it sits beside,
    so `pytest tests -q` in a service's gate means *that service's* tests —
    which is what a scaffolded service in its own repository would mean by it
    too, and the only reading under which the file travels.

    Found by running the shipped ladders instead of trusting them: the
    service's L0 exited 4 from the repo root, having collected nothing. It had
    been correct-looking data since M04 with nothing to execute it.

    `shell=True` deliberately: the commands are written to be read by a person
    and include shell forms like `make check`. A ladder someone can edit is
    already a ladder that can run anything, and splitting on whitespace would
    only break legitimate commands while pretending otherwise.
    """

    def runner(command: str) -> int:
        return subprocess.call(command, shell=True, cwd=directory)  # noqa: S602

    return runner


def describe(command: str) -> str:
    """A command rendered for a log line, quoted so it can be pasted."""
    return " ".join(shlex.quote(part) for part in shlex.split(command))
