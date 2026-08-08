# ADR-009: MCP streamable HTTP is served by a per-invocation ASGI app

**Status:** Accepted
**Date:** 2026-08-08
**Milestone:** M02

## Context

M02's plan named this the milestone's open risk: "Mangum + streamable HTTP
under a Function URL is unproven here", with the fallback being a plain
JSON-RPC handler and an ADR recording that the deployed surface is not
literally MCP. The risk was real. The obvious arrangement — build the ASGI app
at import, adapt it with `Mangum(app, lifespan="off")` — fails in three
distinct ways, none of which the hermetic gate could see:

1. The streamable-HTTP session manager starts its anyio task group in ASGI
   **lifespan startup**. With the lifespan off it never starts, and every
   request dies on `RuntimeError: Task group is not initialized`.
2. `StreamableHTTPSessionManager.run()` refuses to run twice on one instance,
   and the adapter runs the lifespan on **every** invocation. A module-level
   app therefore serves the cold start and raises on every warm request after
   it — an endpoint that works when probed by hand and fails under traffic.
3. The app serves MCP at `/mcp`, not at the ASGI root, so the function URL the
   stack published as `McpUrl` 404'd for every consumer.

All three reached the deployed gate because the hermetic gate tested
`MCPServer` in-process. The object Lambda serves is not that server: it has a
lifespan, a mount path, and middleware, and none of those exist in-process.
The gap was structural, not an oversight in any one test.

## Decision

**The deployed surface stays literally MCP.** The fallback JSON-RPC handler is
not taken.

**The ASGI app is constructed per invocation**, by `build_app()`. A
module-level app object is forbidden without a superseding ADR. The expensive
half — registry parse, Cedar policy compile, tool registration — is built once
at import as `_server` and reused; only the Starlette wrapper and its session
manager are per-request.

**The adapter runs with `lifespan="auto"`.** Mangum runs that lifespan on the
same event loop as the request, which is what makes it correct here; stateless
mode means there is no cross-request state to lose when it is torn down.

**The stack publishes the MCP endpoint, not the function URL root.** The path
is named in both the handler and the stack, and a test asserts they agree,
because an import would make the deployed asset depend on the infra package.

**This constrains every future HTTP-served component (M04 onward):**

- [ ] ships a hermetic test that drives its Lambda handler with a real event
- [ ] invokes that handler **twice**, so warm-container failures are caught
      before deployment rather than by it

## Consequences

- A Starlette app and session manager are constructed on every request. At
  this scale the cost is negligible, but it scales with request rate rather
  than with container count, which is the wrong direction. A component with
  real traffic should revisit it.
- Lifespan startup and shutdown now run per request. Any future startup work
  added to the app silently becomes per-request work; anything genuinely
  expensive must go in `_server` or it will not stay cheap.
- Stateless mode forecloses server-initiated SSE streaming and resumability.
  Tools that want to stream progress cannot, until something other than
  Lambda hosts this.
- The `make conformance` driver itself carried a stale-API bug — it unpacked a
  three-tuple from a transport that returns two — and no hermetic test could
  reach that line, because it only executes against a live endpoint. Deployed
  gates remain the only check on deployed-only code paths.

## References

- `docs/ROADMAP.md` M02 deployed gate (`make conformance`)
- [ADR-003](ADR-003-lambda-over-agentcore-runtime.md) — why this runs on Lambda
- [ADR-007](ADR-007-lambda-asset-built-without-docker.md) — how the asset is built
- [ADR-010](ADR-010-dns-rebinding-check-off-sigv4-is-the-control.md) — the
  fourth failure the same gate surfaced
- `platform/mcp-tvmaze/tests/test_mcp_http_surface.py` — the tests that now
  close this gap
