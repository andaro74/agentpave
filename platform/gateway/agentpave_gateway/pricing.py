"""Token prices and the cost arithmetic built on them.

Prices are authored in pricing.yaml and validated here, for the same reason the
guardrail policy is (ADR-005): a price table is configuration that changes on
the vendor's schedule, not ours.

Everything is Decimal. Money in binary floating point accumulates error that is
invisible in a unit test and embarrassing in a bill, and DynamoDB rejects
floats outright.
"""

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_PRICING_PATH = Path(__file__).parent / "pricing.yaml"

TOKENS_PER_MTOK = Decimal(1_000_000)

# Eight decimal places. A single Haiku call costs on the order of $0.00002, so
# rounding to cents would record every request as free.
COST_PRECISION = Decimal("0.00000001")


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_per_mtok: Decimal = Field(gt=0)
    output_per_mtok: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def _output_costs_at_least_input(self) -> Self:
        # True of every Claude model, and a cheap guard against a transposed
        # pair — which would under-report cost on exactly the token class
        # that dominates agent spend.
        if self.output_per_mtok < self.input_per_mtok:
            raise ValueError("output_per_mtok is below input_per_mtok — the pair looks transposed")
        return self


class PriceTable(BaseModel):
    """The authored price table, validated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    # Recorded on every metering row so a later correction is auditable rather
    # than retroactive.
    basis: str = Field(min_length=1)
    models: dict[str, ModelPrice] = Field(min_length=1)

    def cost_usd(self, model_id: str, input_tokens: int, output_tokens: int) -> Decimal:
        """Cost of one call, rounded to COST_PRECISION.

        An unpriced model raises rather than metering zero. Silently recording
        a real call as free is worse than failing: the row looks legitimate,
        and the gap only surfaces when the bill does.
        """
        price = self.models.get(model_id)
        if price is None:
            raise KeyError(
                f"no price for model {model_id!r} — add it to pricing.yaml before serving it"
            )
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts cannot be negative")

        total = (
            Decimal(input_tokens) * price.input_per_mtok
            + Decimal(output_tokens) * price.output_per_mtok
        ) / TOKENS_PER_MTOK
        return total.quantize(COST_PRECISION, rounding=ROUND_HALF_UP)


def load_price_table(path: Path | None = None) -> PriceTable:
    """Parse and validate the authored price table."""
    source = path or DEFAULT_PRICING_PATH
    return PriceTable.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")))
