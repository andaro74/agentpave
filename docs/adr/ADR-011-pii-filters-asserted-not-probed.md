# ADR-011: The guardrail's PII filters are asserted at synth, never probed at runtime

**Status:** Accepted
**Date:** 2026-08-08
**Milestone:** M03

## Context

M01's deployed gate left a debt on the record: the guardrail's PII filters
shipped unprobed, because standing rule 3 forbids committing PII-looking
strings and a runtime probe needs one to fire the filter. `docs/VALIDATION.md`
deferred the question to M03's adversarial suite — "must cover PII by another
route, or record why it cannot".

M03 is where it comes due. The suite has ten probes and a real fixture-borne
injection path, so the machinery to send a PII probe exists; what does not
exist is a way to write one without putting a plausible national-ID or
card-shaped string into the repository. The strings would live in
`cases/adversarial.yaml`, in git history, in every clone, and in CI logs
whenever a probe failed and printed its input. Synthetic strings do not escape
this: a synthetic value that reliably trips a PII classifier is, by
construction, indistinguishable from a real one to every tool that scans for
leaked secrets — including the pre-commit hook ARCHITECTURE.md §6 specifies.

The alternative considered and rejected was fetching probe strings from
somewhere outside the repo at run time. That trades a committed string for an
un-auditable one, breaks the hermetic gate's no-network rule, and makes the
adversarial suite's inputs invisible to review — three costs for one benefit.

## Decision

**The adversarial suite contains no PII-shaped strings, and no probe exercises
the guardrail's PII filters at runtime. Adding one is forbidden without a
superseding ADR.**

Coverage moves from behaviour to configuration, and takes two assertions
rather than one, because the two ways this control can rot are different:

- **The policy stops requiring an entity.** `test_committed_policy_blocks_contact_and_financial_pii`
  pins `EMAIL`, `PHONE`, `CREDIT_DEBIT_CARD_NUMBER`, and
  `US_SOCIAL_SECURITY_NUMBER` with the type names written out by hand. An
  entity deleted from the YAML turns it red.
- **The render path stops delivering it.** `test_guardrail_carries_every_authored_pii_filter`
  asserts the entities reach `SensitiveInformationPolicyConfig` in the
  synthesised template at the action they were declared with. A renamed or
  dropped key turns it red.

Only the first has a hard-coded expectation; the second is a round-trip check
and would stay green if an entity were removed from the authored policy. Both
are needed and neither substitutes for the other. Both were mutation-tested
when this ADR was written.

This asserts that the control **is configured**, not that it **fires**. The
distinction is real and is not papered over: the M01 gate proved `PROMPT_ATTACK`
fires at `HIGH` against a live endpoint, and nothing equivalent exists for the
PII filters.

Constraint carried forward, to be checked at M04 review:

- [ ] The `agent-tools` template renders no PII-shaped strings into any
      scaffolded eval case, probe, or fixture (ADR-011, standing rule 3)

## Consequences

**Easier.** The repository holds no string that looks like personal data, so
the pre-commit PII hook, secret scanners, and public-repo review all stay
clean. The adversarial suite's inputs remain fully auditable in git.

**Worse — and this is the real cost.** The platform ships a guardrail control
that has never been observed working. Every assertion above reads the same
authored YAML the deployment does, so anything that makes the filters inert on
Bedrock's side — an entity type Bedrock no longer honours, a policy attached to
the wrong guardrail version, a service-side behaviour change — passes every
gate this project has. The assertions prove the config is declared and
delivered; nothing proves Bedrock acts on it. If the PII filters matter to a
real deployment, this is the gap to close first.

**Forecloses.** Any future defect-leakage metric (M05's dashboard) cannot count
PII interventions, because none will ever be recorded. The dashboard must not
present a zero there as evidence of safety.

**Revisit when** the platform gains a scratch account whose logs are not
retained and whose repository is not the one shipped — at which point a probe
can carry a synthetic string that never touches this git history. Until then
this ADR stands.

## References

- `docs/VALIDATION.md` — M01 deployed-gate row, which recorded the debt
- CLAUDE.md standing rule 3 (no real-PII-looking strings in fixtures)
- ARCHITECTURE.md §3 (Bedrock Guardrails applied centrally), §6 (pre-commit
  hook blocking PII-looking strings in fixtures)
- ADR-005 — guardrail authored as YAML, enforced by Bedrock
- `platform/infra/agentpave_infra/guardrail_render.py` — where the
  `sensitiveInformationPolicyConfig` is rendered and asserted
