"""The `pave` CLI.

The tests that matter are the not-yet ones. CLAUDE.md requires an unimplemented
verb to fail loudly rather than silently succeed, and a scaffolder that exits 0
having built nothing is the exact failure that rule exists to prevent — you
find out when the thing it was supposed to build is not there.
"""

from __future__ import annotations

import pytest
from agentpave_pave.cli import NOT_YET, main


def test_dry_run_plans_without_touching_aws(capsys):
    """`pave eval --dry-run` runs in the hermetic gate: no credentials, no
    boto3 session, no network (standing rule 4)."""
    assert main(["eval", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "eval plan" in out
    assert "golden cases:  31" in out


def test_pave_new_is_no_longer_a_stub(tmp_path, capsys):
    """M04 implemented it, and this test used to assert the opposite.

    The reversal is the point. `NOT_YET` is data, so a verb graduating has to
    be a visible edit in both places rather than a message nobody re-read.
    """
    code = main(["new", "catalog-agent", "--into", str(tmp_path)])
    assert code == 0
    assert "arrives in" not in capsys.readouterr().err
    assert (tmp_path / "catalog-agent" / "agentpave_catalog_agent" / "agent.py").exists()


def test_pave_new_refuses_a_bad_name_without_writing(tmp_path, capsys):
    code = main(["new", "Catalog_Agent", "--into", str(tmp_path)])
    assert code == 1
    assert "kebab-case" in capsys.readouterr().err
    assert not list(tmp_path.iterdir()), "a rejected name left files behind"


def test_no_verb_is_still_pending():
    """`shadow-eval` graduated in M06 and was the last one.

    Asserted explicitly rather than left to a parametrised loop over `NOT_YET`.
    A `@parametrize` over an empty mapping collects zero tests and reports
    green — the not-yet rule's own coverage would have disappeared silently on
    the day it finally had nothing to guard.
    """
    assert NOT_YET == {}


def test_the_not_yet_mechanism_still_works_for_the_next_verb(capsys):
    """The machinery outlives its last entry.

    CLAUDE.md requires the next unimplemented verb to fail loudly, so the path
    is exercised directly here instead of being deleted along with the final
    entry and rediscovered the hard way in M07.
    """
    from agentpave_pave.cli import _not_yet

    NOT_YET["teleport"] = "M99"
    try:
        assert _not_yet("teleport") == 1
        err = capsys.readouterr().err
        assert "arrives in M99" in err
        assert "docs/ROADMAP.md" in err
    finally:
        del NOT_YET["teleport"]


def test_output_survives_a_cp1252_console():
    """The plan, scorecard, and diff all carry non-ASCII.

    `capsys` captures text before it is encoded for a terminal, so no other
    test in this file can see this failure — `pave eval --dry-run` died
    mid-print on a stock Windows console while the suite stayed green. This
    test encodes the real output through cp1252 the way a console would.
    """
    import io

    from agentpave_evalsvc.dataset import load_dataset
    from agentpave_evalsvc.harness import plan

    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    with pytest.raises(UnicodeEncodeError):
        console.write(plan(load_dataset()))
        console.flush()

    # …which is exactly why the CLI pins UTF-8 on its streams before printing.
    utf8 = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="replace")
    utf8.write(plan(load_dataset()))
    utf8.flush()


def test_force_utf8_output_is_idempotent_and_safe_on_odd_streams():
    """Streams without `.reconfigure` (a captured buffer, a pipe wrapper) must
    not crash the CLI before it has printed anything."""
    from agentpave_pave.cli import _force_utf8_output

    _force_utf8_output()
    _force_utf8_output()


def test_an_unknown_verb_is_rejected():
    with pytest.raises(SystemExit) as exc:
        main(["teleport"])
    assert exc.value.code != 0


def test_no_verb_is_rejected():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0


@pytest.mark.parametrize("flag", ["--adversarial", "--data", "--dry"])
def test_a_flag_given_by_prefix_is_rejected(flag):
    """argparse accepts unambiguous prefixes by default; this CLI does not.

    Every scaffolded service writes `pave eval ...` into its `gate.yml`, and an
    abbreviation that resolves today becomes ambiguous the day a second flag
    shares its prefix — breaking in someone else's CI, long after the commit
    that caused it. It also made a mutation survive: a `gate.yml` naming
    `--adversarial`, a flag that does not exist, parsed cleanly.

    `allow_abbrev=False` has to be set on the *subparsers*, which do not
    inherit it from the root. Setting it only at the root passes review and
    changes nothing.
    """
    with pytest.raises(SystemExit) as exc:
        main(["eval", flag])
    assert exc.value.code != 0


def test_every_verb_the_roadmap_names_is_implemented():
    """The reverse of the old drift test, and the reason it existed.

    A CLI that advertises a verb as pending while implementing it is the
    not-yet rule failing backwards, so the check now runs the other way: every
    verb the roadmap names must parse and none may be in `NOT_YET`.
    """
    from pathlib import Path

    roadmap = (Path(__file__).resolve().parents[2] / "docs" / "ROADMAP.md").read_text(
        encoding="utf-8"
    )
    assert "pave shadow-eval" in roadmap
    assert "shadow-eval" not in NOT_YET


# ── shadow-eval's guards (hermetic: these return before touching AWS) ──────


def test_shadow_eval_refuses_prompt_only_with_no_prompt(capsys):
    """Both arms would be the same run.

    The report would then print a confident "no case changed outcome" about a
    comparison that never happened — a reassuring sentence produced by a
    misuse, which is worse than an error.
    """
    assert main(["shadow-eval", "--prompt-only"]) == 1
    assert "nothing varies" in capsys.readouterr().err


def test_shadow_eval_refuses_an_unreadable_prompt(capsys, tmp_path):
    assert main(["shadow-eval", "--prompt", str(tmp_path / "absent.txt")]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_shadow_eval_refuses_an_empty_prompt(capsys, tmp_path):
    # An empty file would substitute an empty system prompt and quietly grade a
    # candidate nobody meant to test.
    empty = tmp_path / "prompt.txt"
    empty.write_text("   \n", encoding="utf-8")

    assert main(["shadow-eval", "--prompt", str(empty)]) == 1
    assert "is empty" in capsys.readouterr().err


# ── selfheal ──────────────────────────────────────────────────────────────


def _junit(tmp_path, *failures: str):
    cases = "".join(f'<testcase name="{n}"><failure message="x"/></testcase>' for n in failures)
    path = tmp_path / "report.xml"
    path.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>", encoding="utf-8")
    return str(path)


def test_selfheal_exits_zero_only_on_drift(tmp_path, capsys):
    report = _junit(tmp_path, "test_advertised_input_properties_match_the_contract")

    code = main(["selfheal", "--report", report, "--changed", "platform/registry/tools.yaml"])

    assert code == 0
    assert "schema_drift" in capsys.readouterr().out


def test_selfheal_exits_non_zero_on_a_real_defect(tmp_path, capsys):
    report = _junit(tmp_path, "test_response_validates_against_the_declared_output_schema")

    code = main(["selfheal", "--report", report, "--changed", "platform/registry/tools.yaml"])

    assert code == 1
    assert "propose nothing" in capsys.readouterr().out


def test_selfheal_exits_non_zero_when_it_cannot_read_the_report(tmp_path, capsys):
    """A crash must never read as permission.

    Exit 0 means "an AI may propose a repair". Every other outcome — including
    a missing file — has to be non-zero, or a broken invocation becomes consent.
    """
    code = main(["selfheal", "--report", str(tmp_path / "absent.xml")])

    assert code == 1
    assert "no report" in capsys.readouterr().err
