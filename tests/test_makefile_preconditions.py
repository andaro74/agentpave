"""`make deploy-dev` refuses to start without a terminal, and refuses early.

M07's deployed gate asks for a walkthrough from a clean deploy, and that is the
one deploy this project had never made. Every earlier one *updated* stacks that
already existed with IAM that had not changed, so `--require-approval
broadening` found nothing broadening and never prompted — the verb ran headless
for six milestones and looked like it always would. On an empty account every
statement is new, so cdk asks, finds no TTY, and stops.

Both tests execute make rather than reading the Makefile as text. A regex over
the file would assert that the guard is *written*, which is the shape of check
this project has now found eleven times and which passes just as happily when
the guard is written and unreachable.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    shutil.which("make") is None,
    reason="make is not on PATH — but `make check` is how this suite runs, so this "
    "skip is unreachable in the gate and exists for a bare `pytest` invocation",
)


def _make(*args: str) -> subprocess.CompletedProcess[str]:
    """Run make with stdin closed, which is what makes the TTY check fire."""
    return subprocess.run(
        ["make", *args],
        cwd=REPO,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_require_tty_refuses_when_stdin_is_not_a_terminal() -> None:
    """The guard runs and fails. Not that it exists — that it bites."""
    result = _make("require-tty")

    assert result.returncode != 0, (
        "require-tty succeeded with stdin closed, so `make deploy-dev` will run "
        "headless up to the point cdk stops it — which is the failure this "
        "target exists to move earlier"
    )
    assert "needs a terminal" in result.stdout


def test_the_refusal_names_the_cause_not_just_the_symptom() -> None:
    """A precondition that says only "no terminal" leaves the reader to discover
    *why* a deploy would want one — which took a teardown and a failed deploy to
    learn the first time. `make synth`'s precondition prints the install
    command for the same reason."""
    result = _make("require-tty")

    assert "IAM" in result.stdout
    assert "interactive shell" in result.stdout


def test_deploy_dev_checks_for_a_terminal_before_it_builds_anything() -> None:
    """Ordering is the whole point, and it is make's to resolve, not the text's.

    `deploy-dev: require-tty build` — the guard is a prerequisite rather than
    the recipe's first line so that it fires before three asset builds and a
    30-second synth. Asserting that with a regex on the prerequisite list would
    pin the spelling; asking make to print its plan pins the behaviour.
    """
    plan = _make("-n", "deploy-dev")
    assert plan.returncode == 0, plan.stderr

    # `make -n` echoes `@#` recipe comments alongside the commands, and this
    # Makefile comments heavily — the require-tty recipe's own prose quotes the
    # string `cdk deploy`, which matched at line 0 and made the guard look like
    # it ran after the deploy. Commands are the plan; prose is not.
    lines = [line for line in plan.stdout.splitlines() if not line.lstrip().startswith("#")]

    def first_index(needle: str) -> int:
        for i, line in enumerate(lines):
            if needle in line:
                return i
        pytest.fail(f"{needle!r} is not in `make -n deploy-dev` at all")

    tty_check = first_index("needs a terminal")
    first_build = first_index("mkdir -p build/")
    deploy = first_index("cdk deploy")

    assert tty_check < first_build, (
        "deploy-dev builds assets before checking for a terminal — the check has "
        "stopped being a prerequisite and become part of the recipe"
    )
    assert tty_check < deploy
