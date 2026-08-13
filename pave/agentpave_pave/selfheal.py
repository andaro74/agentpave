"""Deciding whether a red contract suite is safe for an AI to propose a fix for.

ROADMAP M06 asks for a trigger classifier that separates a **real defect** from
**schema drift**, and that distinction is the whole design. Everything else in
self-healing is plumbing: run an agent, open a pull request, label it. This is
the part that decides whether the agent should be run at all.

The asymmetry is the reason it exists. Schema drift is a test that has fallen
behind a deliberate change — the tool's declared shape moved, the assertion
still describes the old one, and editing the assertion to match is the correct
repair. A real defect is the opposite: the test is right and the code is wrong,
and "repairing" the test would delete the only thing that noticed. On a
platform whose thesis is that quality gates catch regressions, an agent that
quietly rewrites a failing assertion is not a feature. It is the single worst
thing this repository could ship.

So the classifier is built to refuse. Three rules, in order, and every path
that is not positively recognised as drift returns something `proposable`
rejects:

1. Nothing failed → nothing to repair.
2. Anything failed that is not in `SCHEMA_DRIFT_TESTS` → a real defect. One
   unrecognised failure is enough; drift does not come mixed with other
   breakage, and a run that contains both is a run where the drift is not the
   interesting part.
3. Only drift-shaped failures, but nothing in the change touched a schema →
   unexplained. Drift has a cause, and a drift-shaped failure with no schema
   change is a symptom of something this classifier does not understand.

Only a change that is drift-shaped *and* explained by an edit to the schema
surface is proposable. That is standing rule 5 applied to an author rather than
a gate: **on error, on ambiguity, and on anything unrecognised, propose
nothing.**

The `--propose` half of M06 — running Claude Code headless in CI against this
verdict — is deferred, and the reasons are in ADR-035. This module is complete
without it: it runs on a laptop, it is unit-tested with no AWS and no model,
and it is the input the deferred workflow would have consumed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Verdict = Literal["schema_drift", "real_defect", "unclassified"]

# The only failures a test edit may legitimately repair.
#
# All three compare what the MCP server *advertises* against what the registry
# *declares*. When a tool's signature changes on purpose, these are the
# assertions that fall behind, and bringing them forward is a mechanical edit
# whose correctness a reviewer can see at a glance.
SCHEMA_DRIFT_TESTS = frozenset(
    {
        "test_advertised_input_properties_match_the_contract",
        "test_advertised_required_fields_match_the_contract",
        "test_advertised_description_is_the_registered_one",
    }
)

# Deliberately absent, with reasons — because the tempting additions are the
# dangerous ones and an empty comment here would invite them back:
#
# * `test_response_validates_against_the_declared_output_schema` — the served
#   data violates the contract. The test is right; editing it would ship a tool
#   returning shapes nobody agreed to.
# * `test_server_offers_exactly_the_registered_tools` — a tool appeared or
#   vanished. An undeclared tool is an ungoverned tool, which is a governance
#   failure and not a stale assertion.
# * every error-shape, policy, and side-effect-freedom test — these describe
#   behaviour the platform promises, not shapes that drift.

# Paths whose change can legitimately produce drift. A schema moves in exactly
# two places: the registry that declares it, and the server whose signatures
# MCP derives it from.
SCHEMA_SURFACE = (
    "platform/registry/",
    "platform/mcp-tvmaze/agentpave_mcp_tvmaze/",
)

# `test_foo[get_episodes]` and `test_foo` are the same test. Parametrisation is
# not a distinction the classifier cares about, and comparing raw ids against
# the drift set would silently classify every parametrised failure as a real
# defect — failing closed, but for the wrong reason and with a misleading
# message.
_PARAM = re.compile(r"\[.*\]$")


@dataclass(frozen=True)
class Classification:
    """A verdict and the sentence a human reads to check it."""

    verdict: Verdict
    reason: str
    # The failures that decided it, so the reason can be audited rather than
    # believed.
    deciding_tests: tuple[str, ...] = ()

    @property
    def proposable(self) -> bool:
        """Whether an AI may be pointed at this failure.

        One verdict of three. The default answer is no.
        """
        return self.verdict == "schema_drift"


def normalise(test_id: str) -> str:
    """A bare test name, from whatever form the report carried."""
    return _PARAM.sub("", test_id.rsplit("::", 1)[-1].strip())


def touches_schema(changed_paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """The changed paths that could explain schema drift."""
    normalised = [p.replace("\\", "/").lstrip("./") for p in changed_paths]
    return tuple(p for p in normalised if p.startswith(SCHEMA_SURFACE))


def classify(
    failed_tests: tuple[str, ...] | list[str],
    changed_paths: tuple[str, ...] | list[str],
) -> Classification:
    """Decide whether a red suite is drift an AI may propose a repair for."""
    failures = tuple(dict.fromkeys(normalise(t) for t in failed_tests if t.strip()))

    if not failures:
        return Classification(
            verdict="unclassified",
            reason="no test failed — there is nothing to repair",
        )

    unrecognised = tuple(t for t in failures if t not in SCHEMA_DRIFT_TESTS)
    if unrecognised:
        return Classification(
            verdict="real_defect",
            reason=(
                f"{len(unrecognised)} failure(s) are not schema drift: "
                f"{', '.join(unrecognised)}. A test that is right about broken code "
                "must not be edited to agree with it"
            ),
            deciding_tests=unrecognised,
        )

    explaining = touches_schema(changed_paths)
    if not explaining:
        return Classification(
            verdict="unclassified",
            reason=(
                "the failures look like schema drift, but nothing under "
                f"{' or '.join(SCHEMA_SURFACE)} changed — drift with no cause is a "
                "symptom of something this classifier does not understand"
            ),
            deciding_tests=failures,
        )

    return Classification(
        verdict="schema_drift",
        reason=(
            f"only advertised-vs-declared assertions failed, and {len(explaining)} "
            f"schema path(s) changed ({', '.join(explaining)})"
        ),
        deciding_tests=failures,
    )


class ReportError(RuntimeError):
    """A report that cannot be read.

    Raised rather than returned as a verdict: an unreadable report is not
    evidence of drift, and a caller that mistook it for one would propose a
    repair on the strength of a parse failure.
    """


def failures_from_junit(path: Path) -> tuple[str, ...]:
    """Failing and erroring test names from a JUnit XML report.

    JUnit rather than a JSON report because `pytest --junitxml` is built in and
    the parser is in the standard library. `make check` must pass from a clean
    clone (ADR-029), and a classifier that needed a plugin installed to read its
    own input would put a dependency in the path of the hermetic gate.
    """
    try:
        root = ET.parse(path).getroot()  # noqa: S314 — CI-local report, not untrusted input
    except FileNotFoundError as exc:
        raise ReportError(f"no report at {path}") from exc
    except ET.ParseError as exc:
        raise ReportError(f"report at {path} is not readable XML: {exc}") from exc

    names: list[str] = []
    for case in root.iter("testcase"):
        # `failure` is an assertion that fired; `error` is a test that could not
        # run. Both are failures here — a test that could not run has not passed,
        # and treating it as absent is the M02 false-pass shape.
        if case.find("failure") is not None or case.find("error") is not None:
            names.append(case.get("name", ""))
    return tuple(n for n in names if n)


def render(result: Classification) -> str:
    """The verdict, as `pave selfheal` prints it."""
    mark = {"schema_drift": "🔧", "real_defect": "🛑", "unclassified": "❓"}[result.verdict]
    lines = [f"{mark} {result.verdict}", f"   {result.reason}"]
    if result.deciding_tests:
        lines.append("")
        lines.extend(f"     {name}" for name in result.deciding_tests)
    lines.append("")
    lines.append(
        "→ an AI may be asked to propose a repair, under human review"
        if result.proposable
        else "→ propose nothing; a human should look at this"
    )
    return "\n".join(lines)
