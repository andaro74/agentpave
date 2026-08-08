# ADR-013: The guarded span is the caller's data; instructions travel beside it

**Status:** Accepted
**Date:** 2026-08-08
**Milestone:** M03

## Context

M03's first deployed `make eval` never graded anything. The judge's own call
to the gateway was blocked, and once the guardrail trace was turned on the
reason was unambiguous: `contentPolicy:PROMPT_ATTACK`.

Nothing was attacking the platform. Until now the gateway wrapped the entire
`prompt` in a single `guardContent` block, which tells Bedrock that all of it
is untrusted user input. The judge's prompt is a meta-prompt — grading
instructions, then the SOURCE, the QUESTION, and the ANSWER — so the filter
was asked whether text reading *"You are grading one answer… Score three
axes… Reply with a single JSON object"* looks like instructions smuggled into
user data. It does. That is precisely what the filter is for, and at `HIGH` it
did its job on our own system prompt. AWS's guidance says to tag only the user
input for exactly this reason.

The serving path has the same shape (`"You are a TV catalogue assistant…"`),
so the 30 golden cases were on the same road; calibration merely runs first.

The worst version of this was in the adversarial suite. Ten probes whose pass
condition is "the guardrail blocked it" would all have been blocked by their
framing rather than their payloads, and `make eval-adversarial` would have
reported **10/10 green while proving nothing** — M02's false-pass defect,
reappearing in the gate specifically built to catch that class of failure.

Lowering the filter strength was rejected: `PROMPT_ATTACK` at `HIGH` is the one
guardrail control this platform has actually observed working (M01's deployed
gate), and the adversarial suite depends on it.

## Decision

**`GatewayRequest.prompt` is the untrusted span and is the only thing wrapped
in `guardContent`. `GatewayRequest.system` carries the caller's own
instructions, reaches Bedrock through the Converse `system` parameter, and is
not inspected by the guardrail's prompt-attack filter.**

**Only code-resident text may travel in `system`. Any byte derived from tool
output, a fixture, a user's question, or a model's own output belongs in
`prompt`. Putting caller-supplied or model-derived data in `system` is
forbidden without a superseding ADR.**

Two mechanical guards exist, and neither is the contract:

- `system` is capped at `SYSTEM_MAX_CHARS` (4096). A 12k fixture will not fit,
  so the tempting response to any future block — move the offending text to
  `system` — fails at the boundary instead of succeeding quietly at a model.
- `test_the_guarded_span_carries_the_data_and_none_of_the_instructions` and
  `test_an_injected_probe_keeps_its_payload_inside_the_guarded_span` pin the
  eval service to the right side of the split, the second because a probe
  whose payload rode in `system` would pass by going uninspected.

Constraints carried forward, to be checked at M04 review:

- [ ] The catalog agent puts MCP tool results in `prompt`, never in `system`
- [ ] The `agent-tools` template renders instructions into `system` and tool
      output into `prompt`, and ships the test that pins it

## Consequences

**Easier.** The judge is callable at all, and the adversarial suite now
measures what it claims to: the attack sits in the guarded span, so a block is
attributable to the payload rather than to the framing around it. Legitimate
system prompts stop competing with attacks for the same filter's attention,
which should reduce false positives across every capability.

**Worse, and this is the cost.** The platform now has an unguarded input path
that did not exist yesterday. `system` reaches the model without passing the
prompt-attack filter, so a caller that puts untrusted data there disables the
platform's principal injection defence for that request — silently, with a
200 and a plausible answer. Nothing in a passing eval run would reveal it. The
character cap bounds bulk smuggling and does nothing about a one-line
injection, and no code here can distinguish instructions from data; that
judgement lives with each caller.

M04 is where this bites: the catalog agent's whole job is to put tool output
into a model call, and tool output is the least trustworthy input the platform
handles. This ADR is a standing review item there, not a solved problem.

**Forecloses** the option of treating the gateway as the single place that
guarantees every byte reaching a model was inspected. That property held
before this change and does not hold now. If it is wanted back, the way is to
move system prompts into the gateway keyed by `feature_id` so callers can only
send data — considered and rejected here because it would deploy the judge's
prompt with the gateway and separate it from `lint_prompt`, the only thing
stopping the judge from silently grading nothing.

**Revisit when** a second service needs a system prompt. Two callers with
hand-written instructions is a convention; three is a registry, and at that
point the gateway-owned option above is worth its cost.

## References

- ARCHITECTURE.md §3 (guardrails applied centrally), invariant 1, invariant 5
- `docs/VALIDATION.md` — M01 deployed row (PROMPT_ATTACK observed firing at
  `HIGH`), M03 deployed row (this block)
- ADR-005 — guardrail authored as YAML and applied to every call
- ADR-010 — SigV4 as the transport control, and M02's false-pass defect that
  the adversarial framing risk mirrors
- ADR-011 — the PII filters, still asserted rather than probed; this change
  does not affect them
- `platform/gateway/agentpave_gateway/invoker.py` — where the split is made
