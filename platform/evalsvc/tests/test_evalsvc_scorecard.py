"""Scorecard rendering.

Rendering gets tests because the scorecard is the deployed gate's entire
output: if a failure's reason does not reach the page, the gate has told a
human that something broke and left them to go find out where.
"""

from __future__ import annotations

from agentpave_evalsvc.models import CaseResult, ProbeResult, Scorecard
from agentpave_evalsvc.scorecard import render, summary_line


def _card(cases: tuple[CaseResult, ...], probes: tuple[ProbeResult, ...] = ()) -> Scorecard:
    return Scorecard(
        run_id="run-1",
        created_at="2026-08-08T00:00:00+00:00",
        model_serve="serve-model",
        model_judge="judge-model",
        cases=cases,
        probes=probes,
    )


def _case(case_id: str, passed: bool, **overrides) -> CaseResult:
    base = {
        "case_id": case_id,
        "capability": "airing",
        "passed": passed,
        "latency_ms": 10,
        "cost_usd": 0.001,
    }
    return CaseResult.model_validate({**base, **overrides})


def test_both_models_are_recorded():
    """A scorecard whose model pair is unknown is not comparable to the next
    one — a score change and a model change look identical after the fact."""
    rendered = render(_card((_case("a", True),)))
    assert "serve-model" in rendered
    assert "judge-model" in rendered


def test_a_failure_prints_its_reason_in_full():
    rendered = render(
        _card((_case("a", False, assert_failures=("must_contain: 'Apple TV' absent",)),))
    )
    assert "FAIL a" in rendered
    assert "'Apple TV' absent" in rendered


def test_an_unevaluated_case_is_distinguished_from_a_low_score():
    """ "Could not be graded" and "graded badly" are different kinds of bad
    news, and only one of them means the endpoint is broken."""
    rendered = render(_card((_case("a", False, error="gateway returned status 403"),)))
    assert "not evaluated" in rendered
    assert "403" in rendered


def test_a_clean_run_says_so():
    assert "no failing cases" in render(_card((_case("a", True),)))


def test_pass_rate_and_capability_breakdown():
    card = _card(
        (
            _case("a", True),
            _case("b", False),
            _case("c", True, capability="enrichment"),
        )
    )
    rendered = render(card)
    assert "2/3 cases passed (67%)" in rendered
    assert "airing: 50%" in rendered
    assert "enrichment: 100%" in rendered


def test_summary_line_fails_when_a_probe_failed():
    """A run with every case green but a probe red has not passed. The gate
    reads `passed`, and it must not be swayed by the case count."""
    card = _card(
        (_case("a", True),),
        (ProbeResult(probe_id="p", outcome="model_complied", passed=False, detail="no"),),
    )
    assert not card.passed
    assert summary_line(card).startswith("[FAIL]")


def test_summary_line_passes_only_when_everything_did():
    card = _card(
        (_case("a", True),),
        (ProbeResult(probe_id="p", outcome="guardrail_blocked", passed=True, detail="ok"),),
    )
    assert summary_line(card).startswith("[PASS]")


def test_an_empty_run_does_not_report_a_pass_rate_of_one():
    """Zero cases is not a perfect score; a suite that stopped loading its
    dataset must not render as green."""
    assert _card(()).pass_rate == 0.0
