"""Baseline store and score diff.

The diff logic is pure and lives above the `# ── wiring ──` line; DynamoDB
lives below it and runs only under the deployed gate. Same split as
`smoke.py`, for the same reason M02 taught the hard way: a gate whose verdict
logic is untested can pass for the wrong reason.

Storage is DynamoDB on-demand (ARCHITECTURE.md §3). On-demand and not
provisioned because nothing may bill while idle (ADR-002); the synth assertion
pins the billing mode so a later edit cannot reintroduce a floor.

Only the summary numbers a diff needs are stored. The table is not the system
of record for run detail — the printed scorecard and CloudWatch already hold
that, and a store that accumulated whole scorecards would be a log with a
worse query interface.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .models import Baseline, Scorecard, ScoreDiff

TABLE_ENV = "AGENTPAVE_BASELINE_TABLE"

# One partition holds the whole history, sorted by time. The dataset is 30
# cases run a few times a day; a partition key with more structure would be
# designing for a scale this platform explicitly does not have (rule 8).
PARTITION = "baseline"


def diff(current: Scorecard, previous: Baseline) -> ScoreDiff:
    """Compare a fresh scorecard against a recorded baseline.

    Capabilities present in one run but not the other are reported as
    `appeared` / `disappeared` rather than as a delta against zero. The
    distinction matters: a capability scoring 0.0 means its cases ran and
    failed, while a capability that vanished means its cases did not run at
    all — usually a loader problem. Reporting the second as a -1.0 delta would
    describe a quality regression that did not happen and hide a dataset
    defect that did.
    """
    now = current.score_by_capability()
    before = previous.by_capability

    shared = sorted(set(now) & set(before))
    return ScoreDiff(
        baseline_run_id=previous.run_id,
        pass_rate_delta=round(current.pass_rate - previous.pass_rate, 6),
        cost_delta_usd=round(current.total_cost_usd - previous.total_cost_usd, 6),
        by_capability_delta={cap: round(now[cap] - before[cap], 6) for cap in shared},
        appeared=tuple(sorted(set(now) - set(before))),
        disappeared=tuple(sorted(set(before) - set(now))),
    )


def render(result: ScoreDiff) -> str:
    """The diff, as `pave eval --diff` prints it."""
    arrow = "▲" if result.pass_rate_delta > 0 else "▼" if result.pass_rate_delta < 0 else "="
    lines = [
        f"score diff vs {result.baseline_run_id}",
        f"  {arrow} pass rate {result.pass_rate_delta:+.1%}",
        f"    cost {result.cost_delta_usd:+.6f} USD",
    ]
    for cap, delta in sorted(result.by_capability_delta.items()):
        lines.append(f"    {cap}: {delta:+.1%}")
    for cap in result.appeared:
        lines.append(f"    {cap}: appeared (no baseline)")
    for cap in result.disappeared:
        lines.append(f"    {cap}: DISAPPEARED — cases did not run")
    lines.append("  REGRESSED" if result.regressed else "  no regression")
    return "\n".join(lines)


# ── wiring (touches AWS; runs only under the deployed gates) ──────────────


def _table(table_name: str) -> Any:
    import boto3

    return boto3.resource("dynamodb").Table(table_name)


def put_baseline(table_name: str, baseline: Baseline) -> None:
    """Record a scorecard summary as a baseline row.

    Floats are stored as `Decimal` because DynamoDB rejects Python floats.
    Routed through `str()` rather than `Decimal(float)` so the stored value is
    the decimal the scorecard printed, not the binary float nearest to it.
    """
    _table(table_name).put_item(
        Item={
            "pk": PARTITION,
            "sk": f"{baseline.created_at}#{baseline.run_id}",
            "run_id": baseline.run_id,
            "created_at": baseline.created_at,
            "pass_rate": Decimal(str(baseline.pass_rate)),
            "total_cost_usd": Decimal(str(baseline.total_cost_usd)),
            "by_capability": {
                cap: Decimal(str(score)) for cap, score in baseline.by_capability.items()
            },
        }
    )


def latest_baseline(table_name: str) -> Baseline | None:
    """The most recently recorded baseline, or None if there is no history.

    None is a real answer — the first run on a fresh stack has nothing to
    compare against. It is distinct from a failed read, which raises: "there
    is no baseline yet" and "the table could not be queried" must not collapse
    into the same quiet no-op.
    """
    from boto3.dynamodb.conditions import Key

    response = _table(table_name).query(
        KeyConditionExpression=Key("pk").eq(PARTITION),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return None

    item = items[0]
    return Baseline(
        run_id=item["run_id"],
        created_at=item["created_at"],
        pass_rate=float(item["pass_rate"]),
        total_cost_usd=float(item["total_cost_usd"]),
        by_capability={cap: float(score) for cap, score in item["by_capability"].items()},
    )
