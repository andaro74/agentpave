"""The HTTP surface Lambda actually serves.

The rest of the suite drives `MCPServer` in-process, which is the right seam
for contract assertions — and is also how three deployment bugs walked through
a green hermetic gate. What Lambda serves is not that server: it is an ASGI app
with a lifespan that starts the session manager's task group, a mount path, and
a security middleware, wrapped in an adapter that re-runs the lifespan on every
invocation. None of those exist in-process, so no contract test could see them.

These tests drive the real handler with real Function URL events, and the real
ASGI app over HTTP. No AWS, no network. See ADR-009 for the hosting
arrangement and ADR-010 for the transport-security trade.
"""

import json
from typing import Any

import pytest
from agentpave_mcp_tvmaze.lambda_handler import STREAMABLE_HTTP_PATH, build_app, handler
from starlette.testclient import TestClient

INITIALIZE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "http-surface-test", "version": "1"},
    },
}

HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def function_url_event(
    body: dict[str, Any],
    *,
    path: str = STREAMABLE_HTTP_PATH,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A Lambda Function URL request, in the shape AWS actually delivers it."""
    return {
        "version": "2.0",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {**HEADERS, **(headers or {})},
        "requestContext": {
            "http": {"method": "POST", "path": path, "sourceIp": "203.0.113.1"},
            "stage": "$default",
        },
        "body": json.dumps(body),
        "isBase64Encoded": False,
    }


@pytest.fixture
def client() -> Any:
    # Entering the context manager runs the ASGI lifespan — the same thing the
    # adapter must do. Without it the session manager never starts its task
    # group and every request 500s.
    with TestClient(build_app()) as running:
        yield running


# ── the handler, end to end ───────────────────────────────────────────────


def test_the_handler_completes_a_handshake() -> None:
    response = handler(function_url_event(INITIALIZE), None)

    assert response["statusCode"] == 200, response.get("body")
    assert "serverInfo" in response["body"]


def test_a_warm_container_still_works_on_the_second_invocation() -> None:
    """The bug a single manual request cannot find.

    `StreamableHTTPSessionManager` refuses to `run()` twice on one instance and
    the adapter runs the ASGI lifespan every invocation, so a module-level app
    serves the cold start and then raises on every request after it. Deployed,
    that reads as an endpoint that works when you test it and fails under any
    real traffic.
    """
    first = handler(function_url_event(INITIALIZE), None)
    second = handler(function_url_event(INITIALIZE), None)

    assert first["statusCode"] == 200, first.get("body")
    assert second["statusCode"] == 200, second.get("body")


def test_the_handler_does_not_serve_mcp_at_the_url_root() -> None:
    # Why `McpUrl` publishes the path rather than the bare function URL.
    response = handler(function_url_event(INITIALIZE, path="/"), None)

    assert response["statusCode"] == 404


# ── transport security ────────────────────────────────────────────────────


def test_an_unrecognised_host_header_is_accepted(client: Any) -> None:
    # DNS-rebinding protection is deliberately off (ADR-010): the hostname is
    # assigned at deploy time and SigV4 is the control that actually matters.
    # Pinned so re-enabling it cannot silently 421 the deployed gate again.
    response = client.post(
        STREAMABLE_HTTP_PATH,
        json=INITIALIZE,
        headers={**HEADERS, "host": "anything.example.com"},
    )

    assert response.status_code == 200, response.text


def test_a_post_without_json_content_type_is_still_rejected(client: Any) -> None:
    # The half of the middleware that survives. Disabling rebinding protection
    # skips Host and Origin only; Content-Type on POST is still enforced, and
    # asserting it is what makes that a checked claim rather than a comment.
    response = client.post(
        STREAMABLE_HTTP_PATH,
        content=b"not json",
        headers={"content-type": "text/plain", "accept": "application/json"},
    )

    assert response.status_code == 400


def test_the_lifespan_is_what_makes_any_of_it_work() -> None:
    """Asserting the failure mode, not just the success.

    A handshake test that would also pass against a half-started app proves
    nothing about the setting it exists to defend.
    """
    unstarted = TestClient(build_app())  # never entered: no lifespan startup

    with pytest.raises(RuntimeError, match="[Tt]ask group"):
        unstarted.post(STREAMABLE_HTTP_PATH, json=INITIALIZE, headers=HEADERS)
