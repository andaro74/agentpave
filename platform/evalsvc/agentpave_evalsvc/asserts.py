"""Deterministic asserts: everything gradeable without a model.

These run on every case, judged or not. They are the cheap, exact half of the
gate — a case that fails here never reaches the judge, because paying Sonnet to
form an opinion about an answer that already broke its cost budget is waste.

Each assert returns a failure string or None. Collecting strings rather than
raising means one case reports every way it failed, not just the first; a
scorecard that says "cost budget exceeded" while also hiding a schema error
sends the reader to fix the wrong thing.

The enrichment record's shape comes from ARCHITECTURE.md §2: title, genres,
runtime, status, network, summary ≤50 words.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import Budget, GoldenCase

# ARCHITECTURE.md §2 fixes both the fields and the summary cap.
ENRICHMENT_FIELDS = ("title", "genres", "runtime", "status", "network", "summary")
SUMMARY_WORD_CAP = 50

# Models like to wrap JSON in a fence even when asked not to. Tolerating the
# fence is not laxity — the schema underneath is what the case is testing, and
# failing on the wrapper would report a schema defect that is not there.
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _strip_fence(text: str) -> str:
    match = _FENCE.match(text)
    return match.group(1) if match else text


def check_contains(answer: str, case: GoldenCase) -> list[str]:
    """Substring expectations, case-insensitively.

    Case-insensitive because the dataset asserts *facts*, not capitalisation:
    a case that fails because the model wrote "apple TV" is measuring nothing
    anyone cares about.
    """
    haystack = answer.casefold()
    failures = []
    for needle in case.must_contain:
        if needle.casefold() not in haystack:
            failures.append(f"must_contain: {needle!r} absent from the answer")
    for needle in case.must_not_contain:
        if needle.casefold() in haystack:
            failures.append(f"must_not_contain: {needle!r} present in the answer")
    return failures


def check_budget(latency_ms: int, cost_usd: float, budget: Budget) -> list[str]:
    """Latency and cost ceilings."""
    failures = []
    if latency_ms > budget.latency_ms:
        failures.append(f"latency budget: {latency_ms}ms exceeds {budget.latency_ms}ms")
    if cost_usd > budget.cost_usd:
        failures.append(f"cost budget: ${cost_usd:.6f} exceeds ${budget.cost_usd:.6f}")
    return failures


def check_enrichment_schema(answer: str) -> list[str]:
    """The structured enrichment record, checked field by field.

    Hand-rolled rather than jsonschema-driven: the record has six fields and
    one word cap, and a dependency whose error messages need translating into
    this function's vocabulary anyway is not paying for itself (rule 8).
    """
    text = _strip_fence(answer)
    try:
        record: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f"enrichment schema: answer is not valid JSON ({exc.msg})"]

    if not isinstance(record, dict):
        return [f"enrichment schema: expected a JSON object, got {type(record).__name__}"]

    failures = []
    for field in ENRICHMENT_FIELDS:
        if field not in record:
            failures.append(f"enrichment schema: missing field {field!r}")

    # `genres` is the one field whose type carries meaning for the dataset:
    # several cases assert on individual genre values, and a comma-joined
    # string would satisfy those substring checks while breaking any consumer.
    genres = record.get("genres")
    if "genres" in record and genres is not None and not isinstance(genres, list):
        failures.append(f"enrichment schema: 'genres' must be a list, got {type(genres).__name__}")

    # `network` is nullable by design — TVMaze records null for shows that
    # carry a webChannel instead, and forcing a string here would push the
    # model to invent one. That is the failure `enrichment-null-network` hunts.
    if "network" in record and not isinstance(record["network"], (str, type(None))):
        failures.append("enrichment schema: 'network' must be a string or null")

    # Null is a legitimate value for any field, not just `network`. The
    # enrichment prompt tells the model to return null for anything it cannot
    # ground in the data, which is the whole point of `enrichment-unknown-show`
    # — an empty search result must produce a well-formed record of nulls, not
    # an invented show. Requiring a string here contradicted that instruction
    # and failed the one case built to test it.
    #
    # The schema checks shape; `must_contain` checks content. Cases about real
    # shows pin their values that way, so nullability here does not weaken them.
    summary = record.get("summary")
    if isinstance(summary, str):
        words = len(summary.split())
        if words > SUMMARY_WORD_CAP:
            failures.append(
                f"enrichment schema: summary is {words} words, cap is {SUMMARY_WORD_CAP}"
            )
    elif "summary" in record and summary is not None:
        failures.append("enrichment schema: 'summary' must be a string or null")

    return failures


def run_deterministic(case: GoldenCase, answer: str, latency_ms: int, cost_usd: float) -> list[str]:
    """Every deterministic assert for one case, in one list."""
    failures = check_contains(answer, case)
    failures += check_budget(latency_ms, cost_usd, case.budget)
    if case.capability == "enrichment":
        failures += check_enrichment_schema(answer)
    return failures
