"""Lambda entrypoint: parse, route, invoke, meter, respond.

The ordering is the policy. Classification is checked before anything reaches
Bedrock, so a `sensitive` request costs nothing and touches no model. Metering
happens on every path, including the ones that refuse — see metering.py.

The gateway is deliberately dependency-injected: `Gateway` holds no AWS
knowledge, and `handler` is the only function that reads the environment or
constructs a boto3 client. That split is what lets the whole request pipeline
be tested on fixtures with no account (standing rule 4).
"""

import json
import os
import uuid
from typing import Any

from pydantic import ValidationError

from .invoker import BedrockInvoker
from .metering import MeteringWriter
from .models import GatewayCompletion, GatewayRefusal, GatewayRequest
from .pricing import load_price_table
from .routing import RoutingTable

_BLOCKED_REASON = "blocked by the AgentPave gateway guardrail"


class Gateway:
    """The request pipeline, with every dependency handed to it."""

    def __init__(
        self,
        *,
        routing: RoutingTable,
        invoker: BedrockInvoker,
        metering: MeteringWriter,
    ) -> None:
        self._routing = routing
        self._invoker = invoker
        self._metering = metering

    def handle(
        self, request: GatewayRequest, *, request_id: str
    ) -> GatewayCompletion | GatewayRefusal:
        decision = self._routing.route(request.feature_id, request.classification)

        if decision.model_id is None:
            # Refused before any model call. Metered at zero tokens so the
            # refusal is still countable in M05.
            self._metering.write(
                request_id=request_id,
                service_id=request.service_id,
                feature_id=request.feature_id,
                classification=request.classification,
                outcome="refused",
                model_id=None,
            )
            return GatewayRefusal(stage="classification", reason=decision.reason)

        result = self._invoker.invoke(
            model_id=decision.model_id,
            prompt=request.prompt,
            max_tokens=request.max_tokens,
            system=request.system,
        )

        # Blocked calls are metered with their real token counts: Bedrock bills
        # for a request its guardrail stopped, so pretending it was free would
        # make the ledger disagree with the invoice.
        usage = self._metering.write(
            request_id=request_id,
            service_id=request.service_id,
            feature_id=request.feature_id,
            classification=request.classification,
            outcome="blocked" if result.blocked else "served",
            model_id=decision.model_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

        if result.blocked:
            return GatewayRefusal(
                stage="guardrail",
                reason=_BLOCKED_REASON,
                blocked_by=result.blocked_by,
            )

        return GatewayCompletion(
            completion=result.completion,
            model_id=decision.model_id,
            usage=usage,
        )


def build_gateway(env: dict[str, str], bedrock_client: Any, metering_table: Any) -> Gateway:
    """Assemble a Gateway from configuration, failing loudly on anything missing."""
    price_table = load_price_table()
    return Gateway(
        routing=RoutingTable(
            model_fast=env.get("AGENTPAVE_MODEL_SERVE", ""),
            # The capable tier and the judge share a model. The env var keeps
            # the name it has in .env rather than gaining a second alias.
            model_capable=env.get("AGENTPAVE_MODEL_JUDGE", ""),
        ),
        invoker=BedrockInvoker(
            bedrock_client,
            guardrail_id=env.get("AGENTPAVE_GUARDRAIL_ID", ""),
            guardrail_version=env.get("AGENTPAVE_GUARDRAIL_VERSION", ""),
        ),
        metering=MeteringWriter(metering_table, price_table=price_table),
    )


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda Function URL entrypoint."""
    import boto3  # imported here so unit tests never need the SDK configured

    try:
        request = GatewayRequest.model_validate_json(event.get("body") or "")
    except ValidationError as exc:
        return _response(400, {"error": "invalid request", "detail": exc.errors(include_url=False)})

    gateway = build_gateway(
        dict(os.environ),
        boto3.client("bedrock-runtime"),
        boto3.resource("dynamodb").Table(os.environ["AGENTPAVE_METERING_TABLE"]),
    )

    request_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    outcome = gateway.handle(request, request_id=request_id)

    # A refusal is a 403, not a 500: the platform worked exactly as designed.
    status = 403 if isinstance(outcome, GatewayRefusal) else 200
    return _response(status, outcome.model_dump())
