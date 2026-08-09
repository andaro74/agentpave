"""The platform SDK: how catalog-agent reaches a model.

Every model call on AgentPave goes through the gateway (ARCHITECTURE.md
invariant 1). This service holds no `bedrock:*` permission of its own, which is
asserted in `cdk synth` rather than trusted, so there is no code path here that
could reach Bedrock even if someone wanted one.

**The `system` / `prompt` split is the important thing in this file.**

`prompt` is the untrusted span. The gateway wraps it in `guardContent`, so
Bedrock's `PROMPT_ATTACK` filter inspects it. Tool output goes here — all of
it, always. A poisoned upstream response is exactly what that filter is for.

`system` is not inspected. It carries instructions this service wrote, that
live in code and never vary with input. Putting tool output or a user's
question there routes it around the platform's main injection defence, and
nothing in a passing test run would reveal it: you would get a 200 and a
plausible answer (ADR-013).

The gateway caps `system` at 4096 characters. That is not the contract, it is
a backstop — it stops a whole tool response fitting through and does nothing
about a single injected sentence.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

SERVICE_ID = "catalog-agent"

# The data classification this service was scaffolded with. The gateway routes
# on it, and refuses `sensitive` by design (ADR-001).
CLASSIFICATION = "internal"

GATEWAY_URL_ENV = "AGENTPAVE_GATEWAY_URL"


class GatewayError(RuntimeError):
    """The gateway could not be reached, or answered with something unusable."""


class GatewayRefusal(RuntimeError):
    """The platform declined the request. Not an error — a working control.

    Carries the stage and, when the gateway reports them, the filters that
    fired. A refusal whose message stops at "blocked" costs a redeploy to
    diagnose; M03 spent one finding that out.
    """

    def __init__(self, stage: str, reason: str, blocked_by: tuple[str, ...] = ()) -> None:
        self.stage = stage
        self.reason = reason
        self.blocked_by = blocked_by
        named = f" [{', '.join(blocked_by)}]" if blocked_by else ""
        super().__init__(f"refused at {stage}: {reason}{named}")


def _signed_post(url: str, payload: str, timeout: int) -> tuple[int, dict[str, Any]]:
    session = boto3.Session()
    request = AWSRequest(
        method="POST",
        url=url,
        data=payload,
        headers={"content-type": "application/json"},
    )
    # The gateway's Function URL is AWS_IAM. An unsigned request gets a 403 on
    # everything, and a caller that reads 403 as "the platform said no" reports
    # a working control where there is a wall (ADR-010).
    SigV4Auth(session.get_credentials(), "lambda", session.region_name).add_auth(request)
    response = requests.post(url, data=payload, headers=dict(request.headers), timeout=timeout)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"raw": response.text[:500]}


def complete(
    *,
    feature_id: str,
    prompt: str,
    system: str | None = None,
    max_tokens: int = 1024,
    temperature: float | None = None,
    timeout: int = 60,
) -> str:
    """Ask the gateway for a completion, or raise.

    `prompt` carries data — tool output, the user's question. `system` carries
    this service's own instructions. See the module docstring; getting this
    backwards is silent in one direction and loud in the other.
    """
    url = os.environ.get(GATEWAY_URL_ENV)
    if not url:
        raise GatewayError(
            f"{GATEWAY_URL_ENV} is not set — this service cannot reach a model "
            "without the gateway, and has no Bedrock permissions of its own"
        )

    body: dict[str, Any] = {
        "service_id": SERVICE_ID,
        "feature_id": feature_id,
        "prompt": prompt,
        "classification": CLASSIFICATION,
        "max_tokens": max_tokens,
    }
    if system:
        body["system"] = system
    if temperature is not None:
        body["temperature"] = temperature

    status, payload = _signed_post(url, json.dumps(body), timeout)

    if payload.get("refused") is True:
        raise GatewayRefusal(
            stage=str(payload.get("stage", "unknown")),
            reason=str(payload.get("reason", ""))[:300],
            blocked_by=tuple(str(f) for f in (payload.get("blocked_by") or ())),
        )
    if status != 200:
        raise GatewayError(f"gateway returned status {status}: {str(payload)[:300]}")

    completion = payload.get("completion")
    if not isinstance(completion, str) or not completion.strip():
        raise GatewayError(f"gateway returned no completion: {str(payload)[:300]}")
    return completion
