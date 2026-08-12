"""The pull-request comment, pinned to a golden file.

ROADMAP M05's hermetic gate asks for exactly this, and the reason is that the
comment is a *communication* artifact. A renderer tested with `assert "FAIL" in
rendered` can degrade into something unreadable while every assertion stays
green — the words can all be present and the thing still be useless to the
person it exists for.

So the whole output is compared, byte for byte, against a file a human read and
approved. When the comment changes, the diff is the review.
"""

from __future__ import annotations

from pathlib import Path

from agentpave_evalsvc.judge import JUDGE_FAILURE_PREFIX, judge_failure
from agentpave_evalsvc.models import (
    Baseline,
    CaseResult,
    JudgeVerdict,
    ProbeResult,
    Scorecard,
)
from agentpave_evalsvc.pr_comment import MARKER, render

GOLDEN = Path(__file__).parent / "golden"

BASELINE = Baseline(
    run_id="eval-baseline-1",
    created_at="2026-08-11T00:00:00+00:00",
    pass_rate=1.0,
    total_cost_usd=0.4719,
    by_capability={"airing": 1.0, "summarize": 1.0, "enrichment": 1.0},
    model_serve="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    model_judge="us.anthropic.claude-sonnet-4-6",
)


def _case(
    case_id: str,
    capability: str,
    passed: bool,
    *,
    failures: tuple[str, ...] = (),
    verdict: JudgeVerdict | None = None,
    error: str | None = None,
) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        capability=capability,
        passed=passed,
        assert_failures=failures,
        verdict=verdict,
        latency_ms=1200,
        cost_usd=0.11693,
        error=error,
    )


def _card(cases: tuple[CaseResult, ...], probes: tuple[ProbeResult, ...] = ()) -> Scorecard:
    return Scorecard(
        run_id="eval-run-2",
        created_at="2026-08-11T14:00:00+00:00",
        model_serve="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        model_judge="us.anthropic.claude-sonnet-4-6",
        cases=cases,
        probes=probes,
    )


BLOCKED_CARD = _card(
    cases=(
        _case("airing-severance-channel", "airing", True),
        _case(
            "airing-schedule-abc-overnight",
            "airing",
            False,
            # Both, because `run_case` records both: the verdict object *and*
            # an assert-failure string restating it. The first version of this
            # fixture carried only the verdict — a shape the harness never
            # produces — so the golden file was approved against a comment that
            # could not occur, and the duplicate rationale reached a real pull
            # request before anything noticed.
            failures=(
                "judge: groundedness=5 completeness=5 tone=3 — includes unnecessary "
                "technical detail (airstamp in UTC) that adds little value",
            ),
            verdict=JudgeVerdict(
                groundedness=5,
                completeness=5,
                tone=3,
                rationale=(
                    "includes unnecessary technical detail (airstamp in UTC) that adds little value"
                ),
            ),
        ),
        _case("summarize-severance-premise", "summarize", True),
        _case(
            "enrichment-severance-null-runtime",
            "enrichment",
            False,
            failures=("must_not_contain: '49' present in the answer",),
        ),
    ),
    probes=(
        ProbeResult(
            probe_id="injection-system-prompt-extraction",
            outcome="guardrail_blocked",
            passed=True,
            detail="guardrail intervened",
        ),
    ),
)


# ── the golden file ───────────────────────────────────────────────────────


def test_a_blocked_gate_renders_the_approved_comment():
    """The whole artifact, byte for byte.

    To change the comment deliberately, update the golden file in the same
    commit and let the diff be the review — which is the point of pinning a
    document nobody would otherwise re-read.
    """
    expected = (GOLDEN / "pr_comment_blocked.md").read_text(encoding="utf-8")
    assert render(BLOCKED_CARD, BASELINE) == expected.rstrip("\n")


# ── the properties the golden file cannot pin on its own ──────────────────


def test_the_marker_leads_so_the_workflow_can_update_in_place():
    """Without it the gate posts a new comment per push and buries the record.

    First line, not merely present: the workflow finds its own comment by
    prefix, and a marker that drifts into the middle is a marker that matches
    nothing on the run where it moved.
    """
    assert render(BLOCKED_CARD, BASELINE).startswith(MARKER)


def test_the_judges_own_words_reach_the_reader():
    """The rationale is the most persuasive thing the platform produces, and it
    was already recorded — the terminal renderer just never printed it."""
    assert "airstamp in UTC" in render(BLOCKED_CARD, BASELINE)


def test_the_judges_rationale_appears_exactly_once():
    """`run_case` records a failing verdict twice — as an assert-failure string
    and as the verdict object — and this renderer printed both.

    The first pull request the gate ever blocked carried the rationale in full,
    twice, in consecutive lines. Caught by reading the comment on a real pull
    request, which is the only place it had ever been rendered from a scorecard
    the harness actually produced.
    """
    rendered = render(BLOCKED_CARD, BASELINE)
    assert rendered.count("airstamp in UTC") == 1


def test_the_prefix_this_renderer_filters_is_the_one_the_writer_writes():
    """Asserted against the writer, not re-typed from it.

    The first version of this test rebuilt the string with its own f-string,
    which would have gone on passing after the writer changed — a pin that
    holds nothing. `judge_failure` is now the one place the shape lives, and
    both the harness and this filter read from it.
    """
    written = judge_failure(JudgeVerdict(groundedness=1, completeness=1, tone=1, rationale="no"))
    assert written.startswith(JUDGE_FAILURE_PREFIX)


def test_a_first_run_says_there_is_no_baseline_rather_than_diffing_against_nothing():
    """A fresh stack has no history. "No comparison" and "no change" are
    different facts, and rendering the first as the second would tell a reviewer
    their change moved nothing when nothing was measured."""
    rendered = render(BLOCKED_CARD, None)
    assert "No baseline recorded yet" in rendered
    assert "Δ" not in rendered


def test_a_model_swap_replaces_the_footer_with_a_warning():
    """A pass-rate delta measured across a model change is two variables in one
    number, and this is the line that stops it being read as one."""
    swapped = BASELINE.model_copy(update={"model_serve": "an-older-haiku"})
    rendered = render(BLOCKED_CARD, swapped)
    assert "⚠️ **Models changed**" in rendered
    assert "compare two systems" in rendered


def test_a_capability_that_did_not_run_is_louder_than_a_bad_score():
    """A vanished capability usually means the dataset failed to load, and its
    quietest possible symptom is a row that is simply absent."""
    card = _card(cases=(_case("airing-severance-channel", "airing", True),))
    rendered = render(card, BASELINE)
    assert "did not run" in rendered
    assert "cases missing" in rendered


def test_a_failed_probe_is_reported_as_the_platform_not_stopping_it():
    """Invariant 5, in the words a reviewer needs: the probe fails because no
    control fired, not because the model was rude about it."""
    card = _card(
        cases=(_case("airing-severance-channel", "airing", True),),
        probes=(
            ProbeResult(
                probe_id="injection-role-override",
                outcome="model_complied",
                passed=False,
                detail="the model declined politely; no platform control fired",
            ),
        ),
    )
    rendered = render(card, BASELINE)
    assert "Adversarial probes not stopped by the platform" in rendered
    assert "model_complied" in rendered


def test_a_clean_run_still_reports_the_adversarial_count():
    """The security claim belongs on every comment, not only on failures."""
    card = _card(
        cases=(_case("airing-severance-channel", "airing", True),),
        probes=(
            ProbeResult(
                probe_id="injection-role-override",
                outcome="guardrail_blocked",
                passed=True,
                detail="guardrail intervened",
            ),
        ),
    )
    rendered = render(card, BASELINE)
    assert rendered.startswith(f"{MARKER}\n## ✅ Quality gate")
    assert "1/1 probes blocked or denied" in rendered
