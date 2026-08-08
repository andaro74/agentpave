"""Rendering a scorecard for a human.

Pure string formatting over a `Scorecard`, kept apart from the harness so it
can be tested without running anything. Two rules it follows:

* Failures print their reason in full. A scorecard that says "12/30 passed"
  and makes the reader open CloudWatch to find out why has moved the work
  rather than done it.
* Passes print one line. The eye should land on what broke.
"""

from __future__ import annotations

from .models import Scorecard


def render(card: Scorecard) -> str:
    """The scorecard `make eval` prints."""
    lines = [
        f"eval scorecard — run {card.run_id} ({card.created_at})",
        f"  serving model: {card.model_serve}",
        f"  judge model:   {card.model_judge}",
        "",
        f"  {sum(1 for c in card.cases if c.passed)}/{len(card.cases)} cases passed "
        f"({card.pass_rate:.0%})",
        f"  cost: ${card.total_cost_usd:.6f}",
        "",
    ]

    for capability, rate in card.score_by_capability().items():
        lines.append(f"  {capability}: {rate:.0%}")
    lines.append("")

    failures = [c for c in card.cases if not c.passed]
    if not failures:
        lines.append("  no failing cases")
    for case in sorted(failures, key=lambda c: c.case_id):
        lines.append(f"  FAIL {case.case_id} ({case.capability})")
        if case.error:
            # An un-evaluated case is a distinct kind of bad news from a case
            # that ran and scored badly, and the scorecard says which.
            lines.append(f"       not evaluated: {case.error}")
        for failure in case.assert_failures:
            lines.append(f"       {failure}")

    return "\n".join(lines)


def summary_line(card: Scorecard) -> str:
    """One line, for a CI annotation or a commit comment."""
    verdict = "PASS" if card.passed else "FAIL"
    return (
        f"[{verdict}] {sum(1 for c in card.cases if c.passed)}/{len(card.cases)} cases, "
        f"{sum(1 for p in card.probes if p.passed)}/{len(card.probes)} probes, "
        f"${card.total_cost_usd:.6f}"
    )
