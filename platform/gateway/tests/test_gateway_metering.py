"""The metering ledger records every request, including the refused ones."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from agentpave_gateway.metering import MeteringWriter
from agentpave_gateway.pricing import load_price_table

HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
FIXED_TIME = datetime(2026, 8, 7, 12, 30, 0, tzinfo=UTC)


class FakeTable:
    """Stands in for a boto3 Table resource."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def put_item(self, *, Item: dict[str, Any]) -> None:  # noqa: N803 — boto3's casing
        self.items.append(Item)


class BrokenTable:
    def put_item(self, *, Item: dict[str, Any]) -> None:  # noqa: N803
        raise RuntimeError("ProvisionedThroughputExceededException")


@pytest.fixture
def table() -> FakeTable:
    return FakeTable()


@pytest.fixture
def writer(table: FakeTable) -> MeteringWriter:
    return MeteringWriter(table, price_table=load_price_table(), clock=lambda: FIXED_TIME)


def _write_served(writer: MeteringWriter, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "request_id": "req-1",
        "service_id": "catalog-agent",
        "feature_id": "summarize",
        "classification": "internal",
        "outcome": "served",
        "model_id": HAIKU,
        "input_tokens": 120,
        "output_tokens": 80,
    }
    return writer.write(**(kwargs | overrides))


def test_served_request_writes_one_row(writer: MeteringWriter, table: FakeTable) -> None:
    _write_served(writer)
    assert len(table.items) == 1


def test_keys_partition_by_service_and_feature(writer: MeteringWriter, table: FakeTable) -> None:
    # "tokens/cost per service/feature" is the M05 dashboard's unit.
    _write_served(writer)
    assert table.items[0]["pk"] == "catalog-agent#summarize"
    assert table.items[0]["sk"] == f"{FIXED_TIME.isoformat()}#req-1"


def test_cost_is_stored_as_decimal(writer: MeteringWriter, table: FakeTable) -> None:
    # DynamoDB rejects floats outright.
    _write_served(writer)
    cost = table.items[0]["cost_usd"]
    assert isinstance(cost, Decimal)
    assert cost == Decimal("0.00052000")  # 120 in @ $1/MTok + 80 out @ $5/MTok


def test_row_records_the_price_basis(writer: MeteringWriter, table: FakeTable) -> None:
    # Without it, correcting a rate silently restates every historical row.
    _write_served(writer)
    assert table.items[0]["price_basis"] == load_price_table().basis


def test_returned_usage_matches_the_row(writer: MeteringWriter, table: FakeTable) -> None:
    usage = _write_served(writer)
    assert usage.input_tokens == 120
    assert usage.output_tokens == 80
    assert usage.cost_usd == pytest.approx(float(table.items[0]["cost_usd"]))


def test_refusal_is_metered_at_zero(writer: MeteringWriter, table: FakeTable) -> None:
    # A ledger that only records successes cannot answer "how often did the
    # platform say no?" — which is exactly what M05 needs to chart.
    usage = writer.write(
        request_id="req-2",
        service_id="catalog-agent",
        feature_id="enrichment",
        classification="sensitive",
        outcome="refused",
        model_id=None,
    )
    row = table.items[0]
    assert row["outcome"] == "refused"
    assert row["cost_usd"] == Decimal(0)
    assert "model_id" not in row
    assert usage.cost_usd == 0.0


def test_blocked_request_is_metered_with_real_tokens(
    writer: MeteringWriter, table: FakeTable
) -> None:
    # Bedrock bills for a call its guardrail stopped. Recording it as free
    # would put the ledger at odds with the invoice.
    _write_served(writer, outcome="blocked", request_id="req-3")
    row = table.items[0]
    assert row["outcome"] == "blocked"
    assert row["cost_usd"] > 0


def test_outcomes_stay_distinguishable(writer: MeteringWriter, table: FakeTable) -> None:
    _write_served(writer, outcome="served", request_id="a")
    _write_served(writer, outcome="blocked", request_id="b")
    writer.write(
        request_id="c",
        service_id="catalog-agent",
        feature_id="summarize",
        classification="sensitive",
        outcome="refused",
        model_id=None,
    )
    assert [item["outcome"] for item in table.items] == ["served", "blocked", "refused"]


def test_classification_is_recorded(writer: MeteringWriter, table: FakeTable) -> None:
    _write_served(writer, classification="public")
    assert table.items[0]["classification"] == "public"


def test_dynamodb_failure_propagates(table: FakeTable) -> None:
    # Fail closed: a dropped row is unmetered spend nothing will reconcile.
    writer = MeteringWriter(BrokenTable(), price_table=load_price_table())
    with pytest.raises(RuntimeError, match="ProvisionedThroughput"):
        _write_served(writer)


def test_unpriced_model_fails_before_writing(table: FakeTable) -> None:
    writer = MeteringWriter(table, price_table=load_price_table())
    with pytest.raises(KeyError):
        _write_served(writer, model_id="us.anthropic.unpriced-model")
    assert table.items == [], "a row was written for a call whose cost is unknown"
