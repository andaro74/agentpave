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
    assert "golden cases:  30" in out


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


def test_pave_shadow_eval_fails_loudly(capsys):
    code = main(["shadow-eval"])
    assert code == 1
    assert "arrives in M06" in capsys.readouterr().err


@pytest.mark.parametrize("verb", sorted(NOT_YET))
def test_every_not_yet_verb_exits_non_zero(verb):
    """Whatever else changes, none of these may exit 0."""
    argv = [verb, "a-name"] if verb == "new" else [verb]
    assert main(argv) != 0


@pytest.mark.parametrize("verb", sorted(NOT_YET))
def test_every_not_yet_verb_points_at_the_roadmap(verb, capsys):
    argv = [verb, "a-name"] if verb == "new" else [verb]
    main(argv)
    assert "docs/ROADMAP.md" in capsys.readouterr().err


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


def test_not_yet_milestones_match_the_roadmap():
    """The message and the roadmap must not drift apart.

    `pave shadow-eval` is M06's canary stand-in. `pave new` graduated in M04
    and must no longer be advertised as pending — a CLI that says a verb is
    missing while implementing it is the not-yet rule failing in reverse.
    """
    from pathlib import Path

    roadmap = (Path(__file__).resolve().parents[2] / "docs" / "ROADMAP.md").read_text(
        encoding="utf-8"
    )
    assert "## M06" in roadmap
    assert "pave shadow-eval" in roadmap
    assert NOT_YET == {"shadow-eval": "M06"}
