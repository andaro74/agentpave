"""Lambda entrypoint — the deployed MCP surface.

`stateless_http=True` because Lambda has no session affinity: each invocation
may land on a different container, so a server holding session state would work
until it didn't. MCP's stateless mode exists for exactly this shape.

Identity: the Function URL requires SigV4, so only an authorized AWS principal
reaches this code at all — that is the authentication. The *agent* identity is
then the fixed service identity configured on the function, not something the
caller asserts, because a caller-supplied identity over HTTP would be a header
anyone could set. Per-caller identity propagation arrives with M04's agent,
when a second identity first exists; see ADR-008.
"""

from mangum import Mangum

from .server import build_server

# Built at import time so the cost lands on cold start rather than on the first
# request, and so a misconfiguration fails the container rather than the call.
_app = build_server().streamable_http_app(stateless_http=True, json_response=True)

handler = Mangum(_app, lifespan="off")
