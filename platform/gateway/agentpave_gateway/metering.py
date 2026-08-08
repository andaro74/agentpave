"""The metering ledger: one row per request, whatever the outcome.

Refusals are metered too, at zero tokens. ROADMAP calls this "token metering",
and metering only served calls would be cheaper — but M05's dashboard has to
count guardrail interventions and classification refusals, and a ledger that
only records successes cannot answer "how often did the platform say no?"
without a second data source that could disagree with this one.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from .models import DataClassification, Usage
from .pricing import PriceTable

# `served` reached a model. `refused` never did — the routing table declined it.
# `blocked` reached Bedrock and the guardrail intervened. The three are kept
# apart because they mean different things about the caller, and collapsing
# them would make the M05 intervention count unreadable.
MeteringOutcome = Literal["served", "refused", "blocked"]


class MeteringWriter:
    """Writes one append-only row per request to DynamoDB."""

    def __init__(
        self,
        table: Any,
        *,
        price_table: PriceTable,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._table = table
        self._prices = price_table
        # Injected so tests assert on an exact sort key instead of sleeping.
        self._clock = clock or (lambda: datetime.now(UTC))

    def write(
        self,
        *,
        request_id: str,
        service_id: str,
        feature_id: str,
        classification: DataClassification,
        outcome: MeteringOutcome,
        model_id: str | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> Usage:
        """Record the request and return the usage it cost.

        Raises on a DynamoDB failure rather than swallowing it. A dropped row
        is unmetered spend that nothing will ever reconcile, so the request
        fails closed (standing rule 5) — the platform would rather refuse a
        call than serve one it cannot account for.
        """
        cost = (
            self._prices.cost_usd(model_id, input_tokens, output_tokens)
            if model_id is not None
            else Decimal(0)
        )
        timestamp = self._clock().isoformat()

        item: dict[str, Any] = {
            # service_id#feature_id — "tokens/cost per service/feature" is the
            # M05 dashboard's unit, so it is the partition key.
            "pk": f"{service_id}#{feature_id}",
            "sk": f"{timestamp}#{request_id}",
            "request_id": request_id,
            "service_id": service_id,
            "feature_id": feature_id,
            "classification": classification,
            "outcome": outcome,
            "timestamp": timestamp,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
            # Which price table produced cost_usd. Without it, correcting a
            # rate silently restates history (ADR-006).
            "price_basis": self._prices.basis,
        }
        if model_id is not None:
            item["model_id"] = model_id

        self._table.put_item(Item=item)

        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=float(cost),
        )
