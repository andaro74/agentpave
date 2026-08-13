"""One structured line per request, for the dashboard to read.

The gateway wrote nothing to stdout until M05. Everything it knew — which
service called, what it cost, whether a guardrail fired and which filter — went
into the metering table and nowhere else, and a CloudWatch dashboard cannot
read DynamoDB. There was, literally, nothing to chart.

**Logs rather than metrics, and that is an ADR-002 decision** (ADR-030). A
custom CloudWatch metric bills about $0.30 a month each, forever, whether or
not anything runs; six of them is a standing charge on an idle platform, which
is the one thing this project says it will not have. A JSON line costs storage
measured in kilobytes, and the dashboard's Logs Insights widgets are billed per
query when somebody looks.

The line is emitted for **every** outcome, including refusals. A dashboard that
only saw successful completions would show a guardrail intervention rate of
zero on the day everything was blocked.

Nothing here can raise. Telemetry that can fail a request is worse than no
telemetry: the gateway's job is to answer or to refuse, and neither should turn
into a 500 because a log line could not be written.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any

from .models import GatewayCompletion, GatewayRefusal, GatewayRequest

# The marker every line carries. Logs Insights filters on it rather than on the
# absence of other fields, because Lambda's own START/END/REPORT lines and any
# stray print share this log group — a query that matched "has a service_id"
# would silently start including anything else that ever grows one.
EVENT = "agentpave.gateway.request"


def request_line(
    request: GatewayRequest,
    outcome: GatewayCompletion | GatewayRefusal,
    *,
    request_id: str,
    model_id: str | None = None,
) -> dict[str, Any]:
    """The record, as a dict. Pure, so the shape is testable without a runtime.

    Deliberately flat. Logs Insights addresses nested fields with dotted paths
    and they work, but every widget query then carries the nesting, and a
    dashboard is edited by whoever is on call rather than by whoever wrote the
    schema.
    """
    line: dict[str, Any] = {
        "event": EVENT,
        "request_id": request_id,
        "service_id": request.service_id,
        "feature_id": request.feature_id,
        "classification": request.classification,
    }

    if isinstance(outcome, GatewayRefusal):
        line.update(
            {
                "outcome": "refused",
                "stage": outcome.stage,
                # Filter *types*, never matched text — the same rule the
                # refusal payload follows. A blocked string echoed into a log
                # group undoes the filter that stopped it.
                "blocked_by": list(outcome.blocked_by),
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
            }
        )
        return line

    line.update(
        {
            "outcome": "served",
            "model_id": outcome.model_id,
            "input_tokens": outcome.usage.input_tokens,
            "output_tokens": outcome.usage.output_tokens,
            "cost_usd": outcome.usage.cost_usd,
        }
    )
    if model_id:
        line["model_id"] = model_id
    return line


def emit(line: dict[str, Any]) -> None:
    """Write one line, and never let writing it break a request.

    `default=str` because a cost may arrive as a `Decimal` and a bare
    `json.dumps` raises on it — the exact shape of failure this function exists
    to swallow, and the reason the swallow is not merely defensive.

    One line, not pretty-printed: CloudWatch makes each line its own event, and
    an indented object becomes thirty events that no query can reassemble.
    ADR-024 learned that with spans.
    """
    # Suppressed, not handled. There is nothing useful to do with a telemetry
    # failure inside a request: the caller wanted an answer or a refusal, and
    # turning either into a 500 because a log line did not serialise is a worse
    # outcome than losing the line.
    with contextlib.suppress(Exception):
        print(json.dumps(line, default=str), file=sys.stdout, flush=True)
