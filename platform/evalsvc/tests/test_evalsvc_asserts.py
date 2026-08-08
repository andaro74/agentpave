"""The deterministic asserts.

These are the half of the gate that runs without a model, so they are also the
half that has to be exactly right: a wrong assert here fails a good answer or
passes a bad one on every single run.
"""

from __future__ import annotations

import json

import pytest
from agentpave_evalsvc.asserts import (
    SUMMARY_WORD_CAP,
    check_budget,
    check_contains,
    check_enrichment_schema,
    run_deterministic,
)
from agentpave_evalsvc.models import Budget, GoldenCase


def _case(**overrides) -> GoldenCase:
    base = {
        "case_id": "a-case",
        "capability": "airing",
        "grading": "judged",
        "prompt": "q",
        "fixture": "f.json",
        "budget": {"latency_ms": 1000, "cost_usd": 0.01},
    }
    return GoldenCase.model_validate({**base, **overrides})


def _record(**overrides) -> str:
    base = {
        "title": "Severance",
        "genres": ["Drama"],
        "runtime": 49,
        "status": "Running",
        "network": None,
        "summary": "A short summary.",
    }
    return json.dumps({**base, **overrides})


# ── substring expectations ────────────────────────────────────────────────


def test_must_contain_is_case_insensitive():
    """The dataset asserts facts, not capitalisation."""
    case = _case(must_contain=("Apple TV",))
    assert check_contains("it streams on apple tv", case) == []


def test_must_contain_reports_the_missing_needle():
    case = _case(must_contain=("Apple TV",))
    (failure,) = check_contains("it airs on AMC", case)
    assert "Apple TV" in failure


def test_must_not_contain_catches_hallucination_bait():
    case = _case(must_not_contain=("season 3",))
    (failure,) = check_contains("it is airing Season 3 now", case)
    assert "season 3" in failure


def test_every_failure_is_reported_not_just_the_first():
    """One case reports every way it failed; a scorecard hiding the second
    failure sends the reader to fix the wrong thing."""
    case = _case(must_contain=("Apple TV", "Running"), must_not_contain=("AMC",))
    assert len(check_contains("it airs on AMC", case)) == 3


# ── budgets ───────────────────────────────────────────────────────────────


def test_budgets_pass_at_the_boundary():
    """The ceiling is inclusive; a run exactly at budget has not exceeded it."""
    assert check_budget(1000, 0.01, Budget(latency_ms=1000, cost_usd=0.01)) == []


def test_latency_over_budget_fails():
    (failure,) = check_budget(1001, 0.001, Budget(latency_ms=1000, cost_usd=0.01))
    assert "latency budget" in failure


def test_cost_over_budget_fails():
    (failure,) = check_budget(10, 0.02, Budget(latency_ms=1000, cost_usd=0.01))
    assert "cost budget" in failure


# ── enrichment schema ─────────────────────────────────────────────────────


def test_valid_record_passes():
    assert check_enrichment_schema(_record()) == []


def test_fenced_json_is_tolerated():
    """The fence is a wrapper, not a schema defect — failing on it would
    report a problem that is not there."""
    assert check_enrichment_schema(f"```json\n{_record()}\n```") == []


def test_prose_instead_of_json_fails():
    (failure,) = check_enrichment_schema("Severance streams on Apple TV.")
    assert "not valid JSON" in failure


def test_missing_field_is_named():
    record = json.loads(_record())
    del record["status"]
    (failure,) = check_enrichment_schema(json.dumps(record))
    assert "'status'" in failure


def test_genres_as_a_string_fails():
    """A comma-joined string satisfies the substring checks while breaking
    every consumer — exactly the failure a schema assert exists to catch."""
    (failure,) = check_enrichment_schema(_record(genres="Drama, Mystery"))
    assert "'genres' must be a list" in failure


def test_null_network_is_allowed():
    """TVMaze records null for shows carrying a webChannel. Forcing a string
    here would push the model to invent one."""
    assert check_enrichment_schema(_record(network=None)) == []


def test_summary_over_the_word_cap_fails():
    (failure,) = check_enrichment_schema(_record(summary="word " * (SUMMARY_WORD_CAP + 1)))
    assert f"cap is {SUMMARY_WORD_CAP}" in failure


def test_summary_exactly_at_the_cap_passes():
    assert check_enrichment_schema(_record(summary="word " * SUMMARY_WORD_CAP)) == []


def test_json_array_instead_of_object_fails():
    (failure,) = check_enrichment_schema("[1, 2, 3]")
    assert "expected a JSON object" in failure


# ── composition ───────────────────────────────────────────────────────────


def test_schema_is_checked_only_for_enrichment():
    """A summarize answer is prose; running it through the JSON parser would
    fail every well-formed answer."""
    case = _case(capability="summarize")
    assert run_deterministic(case, "Severance streams on Apple TV.", 10, 0.001) == []


def test_enrichment_case_runs_the_schema_check():
    case = _case(capability="enrichment", grading="deterministic")
    failures = run_deterministic(case, "not json at all", 10, 0.001)
    assert any("not valid JSON" in f for f in failures)


@pytest.mark.parametrize("capability", ["airing", "summarize", "running"])
def test_budget_applies_to_every_capability(capability):
    case = _case(capability=capability)
    failures = run_deterministic(case, "fine", 99_999, 0.001)
    assert any("latency budget" in f for f in failures)
