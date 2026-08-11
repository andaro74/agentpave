"""Tool access for catalog-agent, exclusively through MCP.

ADR-003's migration checklist requires that all tool access go through MCP and
that no service makes direct calls of its own. That is not decoration: the MCP
server is where the registry lives and where Cedar authorizes by identity
(ADR-008). A service that reached TVMaze directly would bypass both, and the
registry would describe a governance story the code did not follow.

The client is built per call. Nothing is cached between invocations, because
nothing in-process may survive a request — a warm container has to behave like
a cold one.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

MCP_URL_ENV = "AGENTPAVE_MCP_URL"

# Rendered from the registry, not hand-written. `pave new` asked Cedar which
# tools it permits `catalog-agent` to invoke and wrote the answer here, so this
# list and the policy cannot disagree at birth.
#
# It is a local pre-check, not the control. Cedar in the MCP server is the
# control, and it will deny an ungranted call whatever this file says. The
# value of the copy is that an ungranted call fails in this process with a
# readable message instead of surfacing as a denial someone has to find in
# CloudWatch.
#
# Editing this list grants nothing. To gain a tool, add a Cedar `permit` in
# `platform/registry/agentpave_registry/policies/` and re-render.
ALLOWED_TOOLS = ("get_episodes", "get_schedule", "search_show")


class ToolError(RuntimeError):
    """A tool call failed. Never silently treated as an empty result.

    M02's conformance driver folded transport failure into a result and
    reported passes against an endpoint answering 403 to everything. An empty
    payload and a failed call are different things, and only one of them is
    safe to hand a model.
    """


def call_tool(name: str, arguments: dict[str, Any], *, timeout: int = 30) -> str:
    """Call one MCP tool and return its text payload, or raise."""
    if name not in ALLOWED_TOOLS:
        raise ToolError(
            f"tool {name!r} is not granted to catalog-agent; granted: {', '.join(ALLOWED_TOOLS)}"
        )

    url = os.environ.get(MCP_URL_ENV)
    if not url:
        raise ToolError(f"{MCP_URL_ENV} is not set — this service has no tools without it")

    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    session = boto3.Session()
    request = AWSRequest(
        method="POST",
        url=url,
        data=payload,
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
    )
    SigV4Auth(session.get_credentials(), "lambda", session.region_name).add_auth(request)

    try:
        response = requests.post(url, data=payload, headers=dict(request.headers), timeout=timeout)
    except requests.RequestException as exc:
        raise ToolError(f"tool {name!r} unreachable: {exc}") from exc

    if response.status_code != 200:
        # The body, not just the code. A bare "returned status 403" is the same
        # sentence whether IAM refused the signature, Cedar denied the tool, or
        # the server fell over — and it cost M04 two deploy cycles to tell those
        # apart from the outside. Truncated because a tool server having a bad
        # day can return a very long apology.
        raise ToolError(
            f"tool {name!r} returned status {response.status_code}: {response.text[:300]}"
        )

    return _extract_text(response.text, name)


def _extract_text(body: str, name: str) -> str:
    """Pull the tool result's text out of an MCP response.

    `isError` is read explicitly rather than with a defaulting `getattr`. The
    wire alias is camelCase and the attribute is snake_case; a default turned
    every error result into a success in M02, and the suite never noticed.
    """
    try:
        message = json.loads(body)
    except json.JSONDecodeError:
        # Streamable HTTP may frame the reply as SSE.
        for line in body.splitlines():
            if line.startswith("data:"):
                message = json.loads(line[5:].strip())
                break
        else:
            raise ToolError(f"tool {name!r} returned an unreadable body: {body[:200]}") from None

    if "error" in message:
        raise ToolError(f"tool {name!r} failed: {str(message['error'])[:200]}")

    result = message.get("result") or {}
    if result.get("isError") is True:
        raise ToolError(f"tool {name!r} reported an error result: {str(result)[:200]}")

    blocks = result.get("content") or []
    text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
    if not text:
        raise ToolError(f"tool {name!r} returned no content: {str(result)[:200]}")
    return text
