"""The eval gate's pull-request comment.

Separate from `scorecard.py` on purpose. That renderer writes for a developer
who ran `make eval` in a terminal they already understand — indentation, arrows,
and the assumption that the reader knows what `airing` is. This one writes for
someone looking at a pull request, possibly on a phone, possibly having never
opened this repository. The two audiences want different artifacts, and one
renderer serving both would serve neither.

It is the most-read thing this platform produces. Every other output is seen by
whoever ran it; this one is seen by whoever is reviewing the change, and it is
the whole of their experience of the quality gate. So it states the verdict
before the evidence, gives absolute numbers rather than only deltas, and quotes
the judge in its own words — a rationale like *"the answer is cut off
mid-sentence"* explains a failing build in a way no pass-rate can.

Pure over its inputs, and pinned by a golden output file (ROADMAP M05's
hermetic gate). A renderer tested only by "does it contain the word FAIL" is a
renderer that can regress into unreadability while every test stays green.
"""

from __future__ import annotations

from .baseline import diff as compute_diff
from .judge import JUDGE_FAILURE_PREFIX
from .models import UNRECORDED_MODEL, Baseline, CaseResult, Scorecard

# How the workflow finds its own previous comment to update in place. An
# invisible HTML comment rather than a title match, because a title is content
# and content changes — a marker that moves is a marker that posts a duplicate
# every push and buries the history it was meant to preserve.
MARKER = "<!-- agentpave-eval-gate -->"

# Capability order. Fixed rather than sorted-by-score, so a reader comparing two
# comments is comparing rows in the same places. Sorting by severity would put
# the worst first, which reads well once and is unreadable across runs.
CAPABILITY_ORDER = ("airing", "summarize", "running", "enrichment")


def _pct(value: float) -> str:
    return f"{value:.1%}"


def _delta(value: float) -> str:
    """A delta, or an em-dash when nothing moved.

    `+0.0%` and `—` carry the same information and read very differently: a
    column of zeroes invites scanning past, a column of dashes leaves the eye
    free to land on the one row that changed.
    """
    return "—" if abs(value) < 1e-9 else f"**{value:+.1%}**" if value < 0 else f"{value:+.1%}"


def _case_line(case: CaseResult) -> list[str]:
    """One failing case, with the reason a reader can act on."""
    lines = [f"- **`{case.case_id}`** · {case.capability}"]

    if case.error:
        # Not evaluated is a different kind of bad news from evaluated badly,
        # and collapsing them is how M02 reported passes against a wall.
        lines.append(f"  - not evaluated — {case.error}")
        return lines

    for failure in case.assert_failures:
        # The harness records a failing verdict twice over: once as an assert
        # failure string, and once as the verdict object rendered below. Both
        # were printed, so the first real blocked pull request carried the
        # judge's rationale in full, twice, in consecutive lines.
        #
        # The golden test did not catch it because its fixture carried a
        # verdict with no matching assert failure — a shape `run_case` never
        # produces. A golden file is only as honest as the inputs someone
        # imagined for it.
        if failure.startswith(JUDGE_FAILURE_PREFIX):
            continue
        lines.append(f"  - {failure}")

    if case.verdict is not None:
        verdict = case.verdict
        lines.append(
            f"  - judge: groundedness {verdict.groundedness}, "
            f"completeness {verdict.completeness}, tone {verdict.tone}"
        )
        # The judge's own sentence. This is the line that makes the gate look
        # like a grader rather than a counter, and it is already recorded — the
        # terminal renderer simply never printed it.
        lines.append(f"  > {verdict.rationale}")
    return lines


def render(card: Scorecard, previous: Baseline | None = None) -> str:
    """The comment the eval gate posts on a pull request."""
    passed = sum(1 for c in card.cases if c.passed)
    total = len(card.cases)
    failures = sorted((c for c in card.cases if not c.passed), key=lambda c: c.case_id)
    failed_probes = sorted((p for p in card.probes if not p.passed), key=lambda p: p.probe_id)

    if card.passed:
        heading = f"## ✅ Quality gate — {passed}/{total} cases passed"
    else:
        broke = len(failures) + len(failed_probes)
        heading = f"## ❌ Quality gate — {broke} {'check' if broke == 1 else 'checks'} failed"

    lines = [MARKER, heading, ""]

    result = compute_diff(card, previous) if previous is not None else None

    if result is None:
        lines.append(
            f"No baseline recorded yet, so there is nothing to compare against — "
            f"this run scored **{passed}/{total}** ({_pct(card.pass_rate)})."
        )
    else:
        verb = "blocked this pull request" if not card.passed else "passed"
        lines.append(
            f"**L2 eval** {verb}. Golden set **{passed}/{total}** "
            f"({_pct(card.pass_rate)}), against baseline `{result.baseline_run_id}`."
        )
        lines.append("")
        lines.append("| | baseline | this run | Δ |")
        lines.append("|---|---|---|---|")
        lines.append(
            f"| **pass rate** | {_pct(previous.pass_rate)} | {_pct(card.pass_rate)} "
            f"| {_delta(result.pass_rate_delta)} |"
        )

        now = card.score_by_capability()
        for capability in CAPABILITY_ORDER:
            if capability in result.by_capability_delta:
                delta = _delta(result.by_capability_delta[capability])
                lines.append(
                    f"| {capability} | {_pct(previous.by_capability[capability])} "
                    f"| {_pct(now[capability])} | {delta} |"
                )
        for capability in result.appeared:
            lines.append(f"| {capability} | — | {_pct(now[capability])} | new |")
        for capability in result.disappeared:
            # Cases that did not run at all. Loudest row in the table, because
            # the likeliest cause is a dataset that failed to load and the
            # quietest possible symptom is a capability simply not appearing.
            lines.append(
                f"| **{capability}** | {_pct(previous.by_capability[capability])} "
                f"| **did not run** | **cases missing** |"
            )
        lines.append(
            f"| cost | ${previous.total_cost_usd:.4f} | ${card.total_cost_usd:.4f} "
            f"| {result.cost_delta_usd:+.4f} |"
        )

    if failures:
        lines.append("")
        lines.append("**What failed**")
        lines.append("")
        for case in failures:
            lines.extend(_case_line(case))

    if failed_probes:
        lines.append("")
        lines.append("**Adversarial probes not stopped by the platform**")
        lines.append("")
        for probe in failed_probes:
            # Invariant 5, stated where it will be read: a probe fails when the
            # model handled it politely and no platform control fired.
            lines.append(f"- **`{probe.probe_id}`** — {probe.outcome}: {probe.detail}")

    lines.append("")
    blocked = sum(1 for p in card.probes if p.passed)
    footer = [f"**Adversarial** {blocked}/{len(card.probes)} probes blocked or denied"]

    if result is not None and result.model_changes:
        # When the pair moves, this stops being a footnote. A pass-rate delta
        # measured across a model swap is two variables in one number.
        changes = "; ".join(
            f"{name} {was} → {is_now}" for name, was, is_now in result.model_changes
        )
        footer.append(f"⚠️ **Models changed** ({changes}) — the numbers above compare two systems")
    else:
        serve = "unrecorded" if card.model_serve == UNRECORDED_MODEL else card.model_serve
        footer.append(f"**Models** serving `{serve}`, judge `{card.model_judge}`")

    footer.append(f"**Run** `{card.run_id}`")
    lines.append(" · ".join(footer))
    return "\n".join(lines)
