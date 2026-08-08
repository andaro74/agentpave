# ADR-010: DNS-rebinding host validation is off; SigV4 is the access control

**Status:** Accepted
**Date:** 2026-08-08
**Milestone:** M02

## Context

MCP's transport security middleware validates the `Host` header against an
allow-list, defaulted from the server's bind address. That check exists to
protect a **locally bound** MCP server from DNS rebinding: a malicious web page
resolves a hostname to `127.0.0.1` and drives the user's local server from
their browser, which would otherwise trust anything that reached it.

Deployed behind a Lambda Function URL, the check rejected every correctly
signed request with `421 Invalid Host header`, and it cannot be satisfied
honestly:

- The allow-list supports exact hosts and `:*` port wildcards only — no domain
  wildcard that could cover `*.lambda-url.us-west-2.on.aws`.
- The hostname is assigned at deploy time, so it cannot be compiled in.
- It cannot be injected either: having the function read its own function URL
  from its own environment is a circular dependency in CloudFormation
  (function → URL → function).

## Decision

**DNS-rebinding protection is disabled explicitly**, with
`TransportSecuritySettings(enable_dns_rebinding_protection=False)`, rather than
worked around by rewriting the `Host` header in the adapter.

The reasoning is that the attack the control defends against cannot occur here.
The Function URL is `AWS_IAM`: a request must carry a valid SigV4 signature, and
a browser has no credentials to sign with. **SigV4 is the access control; the
`Host` header was never what was protecting this endpoint.**

Disabling it skips `Host` and `Origin` validation only. **Content-Type
validation on POST is retained** and asserted by test, so the surviving half of
the middleware is a checked claim rather than a comment.

**This makes an existing assertion load-bearing.** The MCP function URL must
remain `AWS_IAM`; `test_function_url_requires_iam_auth` is now the only thing
standing between this decision and an unauthenticated MCP server. Switching
that URL to `NONE` auth is forbidden without superseding this ADR.

Revisit when the hostname becomes knowable at build time — a custom domain in
front of the function, or a second deployment pass that writes the resolved
host back into the function's environment.

## Consequences

- **The cost: defence in depth is gone on this surface.** Two independent
  controls became one. If the function URL's auth type ever changed, nothing
  in the application would object — the app would happily serve any caller,
  and only a CDK test would notice.
- A reader of the code sees a security control switched off, which always
  looks worse than it is. The comment at the call site and this ADR are the
  mitigation, and they only work if they stay accurate.
- The decision is specific to Lambda Function URLs plus SigV4. Any future
  transport that fronts this server — an ALB, API Gateway with a different
  authorizer, a public endpoint — invalidates the reasoning rather than
  inheriting it, and must re-derive its own answer.
- `Origin` validation is lost alongside `Host`. Nothing depends on it today
  because no browser can reach this endpoint, which is the same argument as
  above and fails at the same moment if that ever stops being true.

## References

- `docs/ARCHITECTURE.md` §3 (tool access)
- [ADR-008](ADR-008-cedar-in-process-and-how-identity-is-derived.md) — SigV4 as
  the authentication, and what it does not say about agent identity
- [ADR-009](ADR-009-mcp-served-by-a-per-invocation-asgi-app.md) — the hosting
  arrangement this sits inside
- `platform/infra/tests/test_mcp_tvmaze_stack.py` —
  `test_function_url_requires_iam_auth`, now load-bearing
- `platform/mcp-tvmaze/tests/test_mcp_http_surface.py` — pins both halves of
  the trade
