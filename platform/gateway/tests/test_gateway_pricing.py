"""Price table validation and cost arithmetic."""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from agentpave_gateway.pricing import PriceTable, load_price_table
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET = "us.anthropic.claude-sonnet-4-6"


def _valid_table() -> dict[str, object]:
    return {
        "version": 1,
        "basis": "test-basis",
        "models": {HAIKU: {"input_per_mtok": "1.00", "output_per_mtok": "5.00"}},
    }


# ── the committed table ───────────────────────────────────────────────────


def test_committed_table_is_valid() -> None:
    assert load_price_table().basis


def test_every_model_in_env_example_has_a_price() -> None:
    # The routing table can only ever emit the models named in .env, so this
    # is the check that stops a model swap from silently metering at zero.
    # It fails on the config change, not on the first request in production.
    priced = set(load_price_table().models)
    configured = {
        line.split("=", 1)[1].strip()
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.startswith(("AGENTPAVE_MODEL_SERVE=", "AGENTPAVE_MODEL_JUDGE="))
    }

    assert configured, "no model ids found in .env.example — did the variable names change?"
    unpriced = configured - priced
    assert not unpriced, f"models configured in .env.example with no price: {unpriced}"


# ── cost arithmetic ───────────────────────────────────────────────────────


def test_cost_is_computed_per_million_tokens() -> None:
    table = load_price_table()
    # 1M input at $1.00 + 1M output at $5.00.
    assert table.cost_usd(HAIKU, 1_000_000, 1_000_000) == Decimal("6.00000000")


def test_cost_of_a_realistic_call_is_not_rounded_to_zero() -> None:
    # ~$0.00002. At cent precision this would record as free, which is the
    # whole reason COST_PRECISION is eight places.
    table = load_price_table()
    cost = table.cost_usd(HAIKU, 12, 8)
    assert cost > 0
    assert cost == Decimal("0.00005200")


def test_capable_model_costs_more_than_the_fast_one() -> None:
    table = load_price_table()
    assert table.cost_usd(SONNET, 1000, 1000) > table.cost_usd(HAIKU, 1000, 1000)


def test_zero_tokens_cost_nothing() -> None:
    assert load_price_table().cost_usd(HAIKU, 0, 0) == Decimal("0E-8")


def test_cost_returns_decimal_not_float() -> None:
    # DynamoDB rejects floats, and float money accumulates error silently.
    assert isinstance(load_price_table().cost_usd(HAIKU, 5, 5), Decimal)


def test_unpriced_model_raises_rather_than_metering_zero() -> None:
    with pytest.raises(KeyError, match="no price for model"):
        load_price_table().cost_usd("us.anthropic.some-future-model", 100, 100)


def test_negative_token_counts_are_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        load_price_table().cost_usd(HAIKU, -1, 0)


# ── lint rules ────────────────────────────────────────────────────────────


def test_transposed_price_pair_is_rejected() -> None:
    # Output is dearer than input on every Claude model, and output tokens
    # dominate agent spend — a transposition would under-report exactly where
    # it matters most.
    table = _valid_table()
    table["models"][HAIKU] = {"input_per_mtok": "5.00", "output_per_mtok": "1.00"}  # type: ignore[index]
    with pytest.raises(ValidationError, match="transposed"):
        PriceTable.model_validate(table)


def test_zero_price_is_rejected() -> None:
    table = _valid_table()
    table["models"][HAIKU] = {"input_per_mtok": "0", "output_per_mtok": "5.00"}  # type: ignore[index]
    with pytest.raises(ValidationError):
        PriceTable.model_validate(table)


def test_empty_model_table_is_rejected() -> None:
    table = _valid_table()
    table["models"] = {}
    with pytest.raises(ValidationError):
        PriceTable.model_validate(table)


def test_missing_basis_is_rejected() -> None:
    # Without a basis, a later price correction silently restates history.
    table = _valid_table()
    del table["basis"]
    with pytest.raises(ValidationError):
        PriceTable.model_validate(table)


def test_unknown_key_is_rejected() -> None:
    table = _valid_table()
    table["modles"] = {}
    with pytest.raises(ValidationError):
        PriceTable.model_validate(table)


def test_load_price_table_accepts_an_explicit_path(tmp_path: Path) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text(yaml.safe_dump(_valid_table()), encoding="utf-8")
    assert load_price_table(path).basis == "test-basis"
