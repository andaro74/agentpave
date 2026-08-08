"""Lambda entrypoint — the deployed MCP surface.

`stateless_http=True` because Lambda has no session affinity: each invocation
may land on a different container, so a server holding session state would work
until it didn't. MCP's stateless mode exists for exactly this shape.

The arrangement below — app per invocation, lifespan on, path published by the
stack — is ADR-009. Every part of it is load-bearing, and the hermetic gate
could see none of it until `test_mcp_http_surface.py` existed.

Identity: the Function URL requires SigV4, so only an authorized AWS principal
reaches this code at all — that is the authentication. The *agent* identity is
then the fixed service identity configured on the function, not something the
caller asserts, because a caller-supplied identity over HTTP would be a header
anyone could set. Per-caller identity propagation arrives with M04's agent,
when a second identity first exists; see ADR-008.
"""

from typing import Any

from mangum import Mangum
from mcp.server.transport_security import TransportSecuritySettings

from .server import build_server

# The MCP endpoint's path within the ASGI app. The Function URL serves this app
# at its root, so the URL callers need is the function URL plus this — which is
# what the stack publishes as `McpUrl`.
STREAMABLE_HTTP_PATH = "/mcp"

# MCP's Host-header check defends a *locally bound* server against a browser
# being tricked into reaching it by DNS rebinding. That attack cannot apply
# here: the Function URL is `AWS_IAM`, so a request has to carry a valid SigV4
# signature, and a browser has no credentials to sign with. Nor could the check
# be satisfied honestly — the hostname is assigned at deploy time, and having
# the function read its own URL from its own environment is a circular
# dependency in CloudFormation. Turning it off skips Host and Origin only;
# Content-Type on POST is still enforced, and `test_mcp_http_surface.py` pins
# both halves of that so the trade stays visible. See ADR-010 — which also
# makes the stack's AWS_IAM assertion load-bearing.
_TRANSPORT_SECURITY = TransportSecuritySettings(enable_dns_rebinding_protection=False)

# Built at import time so the cost lands on cold start rather than on the first
# request, and so a misconfiguration fails the container rather than the call.
# This is the expensive half — registry parse, Cedar policy compile, tool
# registration — and none of it is per-request state.
_server = build_server()


def build_app() -> Any:
    """A fresh ASGI app, and with it a fresh streamable-HTTP session manager.

    Deliberately *not* a module-level singleton. `StreamableHTTPSessionManager`
    refuses to `run()` twice on one instance, and Mangum runs the ASGI lifespan
    on every invocation — so a shared app would serve the cold start and then
    raise on every warm one. That is a bug that only appears on the second
    request to a container, which is to say: not in a quick manual check.

    Only the wrapper is rebuilt; `_server` and everything costly in it is
    reused.
    """
    return _server.streamable_http_app(
        streamable_http_path=STREAMABLE_HTTP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=_TRANSPORT_SECURITY,
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entrypoint.

    `lifespan="auto"`, not "off": the session manager starts its anyio task
    group in lifespan startup, and without it every request dies on "Task group
    is not initialized". Mangum runs that lifespan on the same event loop as the
    request, which is what makes it safe here — and stateless mode means there
    is no cross-request state lost when it is torn down again.
    """
    return Mangum(build_app(), lifespan="auto")(event, context)
