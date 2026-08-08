# ADR-006: Cost is metered at published list prices, with the price basis recorded on every row

**Status:** Accepted
**Date:** 2026-08-07
**Milestone:** M01

## Context

The metering writer has to turn token counts into a dollar figure, which means
the platform needs a price per token. That number is harder to obtain honestly
than it looks.

Bedrock is partner-operated and prices separately from Anthropic's first-party
API. Anthropic's own model documentation publishes list rates — $1.00/$5.00 per
million tokens for Haiku 4.5, $3.00/$15.00 for Sonnet 4.6, verified 2026-08-07 —
but AWS's Bedrock pricing page does not publish rows for these models in a form
that can be read and cited, and the account's real rate can differ from any
published figure through negotiated terms. So the exact number this platform
will actually be billed is not knowable from where this code sits.

Three bad options were available. Guess a Bedrock-specific number and present
it as fact. Omit cost and meter only tokens, which fails the ROADMAP gate
("tokens/cost per service") and pushes the arithmetic onto whoever reads the
dashboard. Or block M01 on obtaining a real invoice, which cannot happen before
anything has been deployed to generate one.

## Decision

Cost is computed from published Anthropic list prices, authored in
`platform/gateway/agentpave_gateway/pricing.yaml` and validated by the schema in
`pricing.py`, on the same author-then-lint pattern as the guardrail policy
(ADR-005).

Every metering row records a `price_basis` field naming the price table that
produced its `cost_usd` — currently `anthropic-list-2026-08-07`. Correcting a
rate means publishing a new basis, not editing history: rows written under the
old basis keep their original figures and stay comparable among themselves.
Rewriting `cost_usd` on existing rows in place is forbidden without a
superseding ADR.

Cost arithmetic is `Decimal` end to end, quantised to eight decimal places. A
single Haiku call costs on the order of $0.00002, so cent precision would record
every request in this demo as free.

A model with no entry in the price table raises rather than metering zero. The
hermetic gate asserts that every model id configured in `.env.example` has a
price, so a model swap fails `make check` at the moment the configuration
changes rather than silently metering the new model at nothing.

**This constrains M07**: the honest-cost section of the README reports figures
derived from list prices and must say so, alongside at least one real AWS bill
for the demo account. If the two disagree materially, that gap is the finding,
and it gets published rather than reconciled away.

## Consequences

- The dashboard has a cost axis from M01 rather than waiting on billing data,
  and the number is reproducible from a file anyone can read.
- **The figures are estimates and will not match the invoice.** Anyone reading
  a cost number from this platform is reading list price, not spend. That is a
  real limitation of the metering story and it is disclosed on the row itself
  rather than in a footnote someone has to find.
- Carrying `price_basis` on every row costs storage and makes queries
  group by basis rather than summing naively across a price change. That is the
  point — a naive sum across a correction would be wrong, and this makes the
  wrongness visible instead of silent.
- Refusals are metered at zero cost, so a spike in refusals shows up as request
  volume with no spend. A reader who charts cost alone will miss it; the
  `outcome` field is what makes the refusal countable.
- The price table is a second thing to keep current. It will go stale between
  vendor price changes and whoever notices, and nothing in the hermetic gate can
  detect staleness — only that the file is well-formed and covers the configured
  models.

## References

- `docs/ROADMAP.md` M01 (token metering to DynamoDB), M05 (tokens/cost per service)
- [ADR-002](ADR-002-nothing-bills-while-idle.md) — the cost invariant this measures
- [ADR-005](ADR-005-guardrail-authored-as-yaml-enforced-by-bedrock.md) — the
  author-then-lint pattern this reuses
- [Claude model pricing](https://platform.claude.com/docs/en/about-claude/models/overview)
- [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) — partner-operated, priced separately
