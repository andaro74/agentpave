# ADR-025: The scaffolded service role holds `lambda:InvokeFunction`, restricted to function-URL calls

**Status:** Accepted
**Date:** 2026-08-11
**Milestone:** M04

## Context

A scaffolded service reaches the gateway and the MCP server over IAM-authed
Lambda Function URLs, signing each request with SigV4 (ADR-010). Its role was
granted the one action that appears to describe that: `lambda:InvokeFunctionUrl`,
scoped to functions in this account, under a permission boundary listing the
same action.

Every call it made was refused. `403 Forbidden` from the URL endpoint, with no
invocation recorded on the far side — the tool server's log group stayed empty,
so the failure was indistinguishable from a bad signature and read like one for
most of a day. AWS changed the requirement in October 2025: an IAM-authed
function URL now requires the caller to hold **both** `lambda:InvokeFunctionUrl`
and `lambda:InvokeFunction`. Function URLs created after that date enforce it.

Nothing in this repository could see it. The template synthesised, all thirteen
IAM assertions on the service stack passed, the deploy raised no warning, and
`make check` was green throughout. `make smoke-gateway` and `make conformance`
could not catch it either: both sign as the developer, whose own credentials
carry broad `lambda:*`, so they exercise the endpoint but never the identity the
paved road actually runs on. Only `make walkthrough` — the one gate that asks
the scaffolded service to do its own work — was ever going to fail.

Granting the second action bluntly would hand every scaffolded service
`lambda:InvokeFunction` on every function in the account: the gateway reachable
through the ordinary Invoke API rather than its URL, and anything else that
happens to live here. That is a wider grant than the one being fixed, and this
platform's claim about a scaffolded service is that its role is small enough to
read.

## Decision

The scaffolded service role holds two statements, and the permission boundary
admits both actions:

- `lambda:InvokeFunctionUrl` on `arn:aws:lambda:<region>:<account>:function:*`,
  conditioned on `lambda:FunctionUrlAuthType` equal to `AWS_IAM`.
- `lambda:InvokeFunction` on the same scope, conditioned on
  `lambda:InvokedViaFunctionUrl` being `true`.

Unconditioned `lambda:InvokeFunction` in a scaffolded service's role — or in the
boundary — is forbidden without a superseding ADR. The condition is the whole
point of granting it: the service may knock on a front door, and may not call
anything directly.

No resource-based policy is added to the gateway or the MCP server. Every caller
is in this account, where AWS accepts an identity-based grant *or* a
resource-based one; a standing `AddPermission` for the account root would widen
the door without opening anything that was shut.

Both properties are asserted in `platform/infra/tests/test_service_stack.py`,
against the boundary as well as the inline policy — a grant the ceiling does not
admit is not a grant.

## Consequences

The golden path works: the scaffolded service reaches its tool through MCP and
its model through the gateway, which is Act 1 of `make walkthrough`.

The cost is that the paved road's role is no longer the minimal thing it claimed
to be, and the honest version is longer to explain. "This service may invoke
Lambda functions, but only through their URLs" needs a reader to know what
`InvokedViaFunctionUrl` does; "this service holds no Bedrock at all" did not.
The role now carries an action whose name says more than the grant does, and
anyone auditing it by reading action names will over-read it.

It also leaves the platform depending on an AWS condition key for a governance
property. If `lambda:InvokedViaFunctionUrl` is ever absent from a request's
context, this statement stops matching and every tool call fails closed — the
same silent-403 failure mode as the defect it fixes, in the other direction.
That is the correct direction to fail, and it is still a failure that no
hermetic test can predict.

What it forecloses: a scaffolded service cannot be given direct `InvokeFunction`
for a legitimate reason — an async fan-out, say — without superseding this ADR.
That is deliberate.

## References

- ARCHITECTURE.md invariant 1 (models are reached only through the gateway) and
  §4 (one stack per component)
- ADR-010 — SigV4 is the control on the Function URL
- ADR-008 — Cedar authorizes by identity in the MCP server
- ROADMAP M04 — deployed gate is `make walkthrough`
- AWS, *Control access to Lambda function URLs* —
  https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html
  ("Starting in October 2025, new function URLs will require both
  `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` permissions.")
