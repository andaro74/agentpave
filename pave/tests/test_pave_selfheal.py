"""The self-heal trigger classifier.

Every test here is really one question asked from a different angle: **can this
thing be talked into proposing a repair for a real bug?** The classifier's
value is entirely in what it refuses, so most of this file is refusals.
"""

from __future__ import annotations

import pytest
from agentpave_pave.selfheal import (
    SCHEMA_DRIFT_TESTS,
    Classification,
    ReportError,
    classify,
    failures_from_junit,
    normalise,
    render,
    touches_schema,
)

DRIFT = "test_advertised_input_properties_match_the_contract"
REQUIRED = "test_advertised_required_fields_match_the_contract"
OUTPUT_SCHEMA = "test_response_validates_against_the_declared_output_schema"
REGISTRY = "platform/registry/tools.yaml"
SERVER = "platform/mcp-tvmaze/agentpave_mcp_tvmaze/tools.py"


# ── the one case that may be proposed ─────────────────────────────────────


def test_drift_with_a_schema_change_is_proposable() -> None:
    result = classify([DRIFT, REQUIRED], [REGISTRY])

    assert result.verdict == "schema_drift"
    assert result.proposable is True


@pytest.mark.parametrize("path", [REGISTRY, SERVER])
def test_either_schema_surface_explains_drift(path: str) -> None:
    # A schema moves in two places: where it is declared and where it is served.
    assert classify([DRIFT], [path]).proposable is True


def test_parametrised_failures_are_recognised() -> None:
    """`test_foo[get_episodes]` is `test_foo`.

    Comparing raw ids against the drift set would classify every parametrised
    failure as a real defect. It would still fail closed — but for the wrong
    reason, with a message naming a test that is in the drift set as though it
    were not.
    """
    result = classify([f"{DRIFT}[get_episodes]", f"{DRIFT}[search_show]"], [REGISTRY])

    assert result.verdict == "schema_drift"


def test_a_fully_qualified_node_id_is_recognised() -> None:
    node_id = f"platform/mcp-tvmaze/tests/test_mcp_contract.py::{DRIFT}[search_show]"

    assert classify([node_id], [REGISTRY]).verdict == "schema_drift"


# ── everything it refuses ─────────────────────────────────────────────────


def test_an_output_schema_violation_is_a_real_defect() -> None:
    """The served data broke the contract. The test is right.

    This is the failure whose auto-repair would be worst: editing it ships a
    tool returning shapes nobody agreed to, and deletes the only thing that
    noticed.
    """
    result = classify([OUTPUT_SCHEMA], [REGISTRY])

    assert result.verdict == "real_defect"
    assert result.proposable is False


def test_one_unrecognised_failure_poisons_a_drift_shaped_run() -> None:
    # Drift does not come mixed with other breakage. A run containing both is a
    # run where the drift is not the interesting part.
    result = classify([DRIFT, REQUIRED, OUTPUT_SCHEMA], [REGISTRY])

    assert result.verdict == "real_defect"
    assert OUTPUT_SCHEMA in result.deciding_tests


def test_drift_with_no_schema_change_is_unclassified() -> None:
    # Drift has a cause. Drift-shaped failures with no schema edit behind them
    # are a symptom of something else.
    result = classify([DRIFT], ["README.md"])

    assert result.verdict == "unclassified"
    assert result.proposable is False


def test_no_failures_is_not_an_invitation() -> None:
    result = classify([], [REGISTRY])

    assert result.verdict == "unclassified"
    assert result.proposable is False


def test_no_changed_paths_at_all_is_refused() -> None:
    assert classify([DRIFT], []).proposable is False


@pytest.mark.parametrize(
    "test_name",
    [
        "test_server_offers_exactly_the_registered_tools",
        "test_wrong_identity_is_denied",
        "test_denial_is_logged",
        "test_read_tools_issue_only_get",
        "test_missing_required_argument_is_rejected",
        "test_contract_check_catches_schema_drift",
    ],
)
def test_governance_and_behaviour_tests_are_never_drift(test_name: str) -> None:
    """These describe promises the platform makes, not shapes that drift.

    Named individually rather than asserted as a set difference: a set
    difference would pass just as happily on the day someone widened
    `SCHEMA_DRIFT_TESTS` to include one of them.
    """
    assert classify([test_name], [REGISTRY]).verdict == "real_defect"


def test_an_unknown_test_name_is_a_real_defect() -> None:
    # A test this classifier has never heard of is not a test it may edit.
    assert classify(["test_something_added_next_quarter"], [REGISTRY]).verdict == "real_defect"


def test_the_drift_set_stays_small_and_deliberate() -> None:
    """A guard on scope creep, not on correctness.

    Every name added here widens what an AI may rewrite unreviewed-by-default.
    If this assertion fails, the question is not "update the number" — it is
    whether the new entry is a shape that drifts or a promise that broke.
    """
    assert len(SCHEMA_DRIFT_TESTS) == 3
    assert all(name.startswith("test_advertised_") for name in SCHEMA_DRIFT_TESTS)


# ── path handling ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "platform/registry/tools.yaml",
        "./platform/registry/tools.yaml",
        "platform\\registry\\tools.yaml",
    ],
)
def test_schema_paths_are_recognised_in_every_shape_git_emits(path: str) -> None:
    # `git diff --name-only` on Windows and in CI do not agree on separators,
    # and a classifier that missed the match would refuse a legitimate repair
    # on a platform detail.
    assert touches_schema([path]) != ()


def test_a_near_miss_path_does_not_count() -> None:
    assert touches_schema(["platform/registry-notes/README.md"]) == ()
    assert touches_schema(["docs/platform/registry/tools.yaml"]) == ()


def test_normalise_strips_parametrisation_and_the_node_path() -> None:
    assert normalise("tests/test_x.py::test_foo[a-b]") == "test_foo"
    assert normalise("test_foo") == "test_foo"


# ── reading a junit report ────────────────────────────────────────────────


def _write(tmp_path, body: str):
    path = tmp_path / "report.xml"
    path.write_text(body, encoding="utf-8")
    return path


def test_failures_and_errors_are_both_collected(tmp_path) -> None:
    """A test that could not run has not passed.

    Treating an `error` as absent is the M02 false-pass shape: transport
    failure folded into a passing result.
    """
    report = _write(
        tmp_path,
        """<testsuites><testsuite>
          <testcase name="test_passed"/>
          <testcase name="test_failed"><failure message="x"/></testcase>
          <testcase name="test_errored"><error message="y"/></testcase>
        </testsuite></testsuites>""",
    )

    assert set(failures_from_junit(report)) == {"test_failed", "test_errored"}


def test_a_green_report_yields_no_failures(tmp_path) -> None:
    report = _write(
        tmp_path,
        '<testsuites><testsuite><testcase name="test_passed"/></testsuite></testsuites>',
    )

    assert failures_from_junit(report) == ()


def test_a_missing_report_raises_rather_than_returning_empty(tmp_path) -> None:
    """An unreadable report is not evidence of anything.

    Returning `()` would flow into `classify` as "nothing failed" — a verdict
    that reads as reassurance and was produced by a missing file.
    """
    with pytest.raises(ReportError, match="no report"):
        failures_from_junit(tmp_path / "absent.xml")


def test_a_malformed_report_raises(tmp_path) -> None:
    with pytest.raises(ReportError, match="not readable XML"):
        failures_from_junit(_write(tmp_path, "<testsuites><testsuite>"))


# ── the rendering ─────────────────────────────────────────────────────────


def test_a_refusal_says_what_happens_next() -> None:
    rendered = render(classify([OUTPUT_SCHEMA], [REGISTRY]))

    assert "propose nothing" in rendered
    assert OUTPUT_SCHEMA in rendered


def test_a_proposable_verdict_still_names_the_human() -> None:
    rendered = render(classify([DRIFT], [REGISTRY]))

    assert "human review" in rendered


def test_proposable_is_true_for_exactly_one_verdict() -> None:
    assert Classification(verdict="schema_drift", reason="r").proposable is True
    assert Classification(verdict="real_defect", reason="r").proposable is False
    assert Classification(verdict="unclassified", reason="r").proposable is False
