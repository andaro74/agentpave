# ADR-005: The guardrail is authored as YAML and enforced by Bedrock, never evaluated in the gateway

**Status:** Accepted
**Date:** 2026-08-07
**Milestone:** M01

## Context

ARCHITECTURE.md §3 says "Bedrock Guardrails applied centrally". The M01
implementation plan drafted something else: a YAML file of regex rules
evaluated in-process by the gateway Lambda. ROADMAP's M01 hermetic gate —
"guardrail policy file lints" — reads naturally against either.

The two are not equivalent, and the difference lands squarely on M03. The
adversarial gate passes only on *"guardrail blocked, or policy denied and
logged"* and explicitly never on *"the model resisted"*. That distinction
exists to stop the demo claiming a safety property it does not have. An
in-process regex denylist is a third thing — neither a guardrail nor model
reticence — and letting the adversarial suite pass on it would be the same
category of dishonesty the invariant was written to prevent.

The pull toward in-process evaluation is real: it is fully testable in
`make check`, with no account and no network. Bedrock Guardrails are not.
Choosing enforcement over hermetic testability is a deliberate trade, made
here rather than left implicit.

## Decision

`platform/gateway/agentpave_gateway/guardrail_policy.yaml` is the authored
source of truth. `cdk synth` renders it into an `AWS::Bedrock::Guardrail`, and
the gateway Lambda receives only the resulting identifier and version as
environment variables, which it passes on every `InvokeModel` call.

The gateway does not evaluate content policy in-process. Adding a regex
denylist, keyword filter, or any other request-time content check to the
gateway is forbidden without a superseding ADR.

The Lambda refuses to invoke a model when `AGENTPAVE_GUARDRAIL_ID` or
`AGENTPAVE_GUARDRAIL_VERSION` is unset. An unguarded call is worse than no
call, and `bedrock:ApplyGuardrail` is scoped to this stack's guardrail so a
misconfigured identifier fails at the API rather than silently degrading.

"Policy file lints" means the schema in `guardrail.py`, which encodes the
Bedrock constraints that would otherwise only surface as a `CreateGuardrail`
error partway through a deploy, plus two rules of our own: a filter disabled on
both sides is rejected rather than left in the file looking active, and a
policy without a `PROMPT_ATTACK` filter fails the gate outright, because the
M03 adversarial claim depends on that filter existing.

## Consequences

- The M03 adversarial gate can honestly say "guardrail blocked", because a
  guardrail is what blocked it.
- **`make check` cannot prove the guardrail blocks anything.** It proves the
  policy is well-formed and that it reaches CloudFormation. A policy that is
  valid, deployed, and ineffective would pass the hermetic gate; only
  `make smoke-gateway` and `make eval-adversarial` would catch it. This is the
  real cost of the decision and it does not have a cheap fix — the must-block
  probe in the deployed gate is the only thing standing behind the claim.
- Guardrails bill per text unit on every request, so the platform is more
  expensive per call than an in-process check. Nothing bills while idle, so
  ADR-002 holds.
- Guardrail interventions become Bedrock CloudWatch metrics rather than
  application logs, which is what M05's dashboard wants and what an in-process
  check would have made us build by hand.
- Rules Bedrock cannot express — a project-specific denylist, say — now have
  nowhere to live. That is intentional: the next such need should force a
  superseding ADR rather than quietly reintroduce a second enforcement path.
- The guardrail is referenced at `DRAFT`, so editing the YAML and redeploying
  moves live enforcement with no version pin. Acceptable for one account and
  one stage; a second stage sharing this guardrail would need numbered
  versions and a superseding ADR.
- `NAME` and `ADDRESS` are excluded from the PII block list. A catalog agent
  legitimately handles actor names and settings, and blocking them would break
  the sample use case while looking like a stricter policy — a guardrail that
  blocks the product is an outage. The exclusion is asserted in the gateway
  test suite so it cannot be "tightened" without someone reading this.

## References

- `docs/ARCHITECTURE.md` §3 (Bedrock Guardrails applied centrally) and
  invariant 5 (adversarial passes mean blocked or denied, never "resisted")
- `docs/ROADMAP.md` M01 hermetic gate, M03 deployed gate
- [ADR-001](ADR-001-scope-and-non-goals.md) — single account and stage
- [Bedrock Guardrails components](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-components.html)
