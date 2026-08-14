# AgentPave — project page copy

Copy for **floresinnovations.com/projects**, drafted in the repository so it
travels with the numbers it quotes. Every figure here is sourced from
[`README.md`](../README.md) and [`VALIDATION.md`](VALIDATION.md); nothing is
rounded in a friendlier direction. Adapt the headings to the site's template —
the sections are ordered as a page, not as a spec.

---

## Hero

**AgentPave**

> The paved road provides. The quality gate decides.

A miniature agentic AI developer platform on AWS with QA baked into the
infrastructure — one command scaffolds a governed agent that arrives with evals,
guardrails, tracing, and a failing-closed CI quality gate already attached.

Built in public over seven days with Claude Code, one milestone at a time.

**39 ADRs · 741 hermetic tests · six stacks · $20.45 of Bedrock, total.**

`[ View on GitHub ]` → https://github.com/andaro74/agentpave

---

## The problem, in two sentences

Most agentic AI demos prove that *an agent* can work. Very few prove that an
*organisation* of agents can work — that a second, third and tenth team could
ship governed, evaluated, observable agents without rebuilding the same
machinery every time.

That is a platform problem, and AgentPave's thesis is that such a platform's
defining property is **quality engineering baked into the infrastructure**.

---

## Three acts

**Act 1 — The paved road.** `pave new catalog-agent --template agent-tools` →
scaffold → deploy → a traced, metered, guarded answer. Five deployed facts, not
mocks: scaffolded into its own stack, answered grounded via an MCP tool, guarded
by an injection blocked at `contentPolicy:PROMPT_ATTACK` *by the platform rather
than the model's manners*, metered at $0.000629, traced with GenAI
semantic-convention attributes.

**Act 2 — The gate bites.** A prompt change is blocked by the eval gate, with the
score diff posted as a pull-request comment. The red pull request stays open in
history. The first attempt at breaking it — *"be concise"* — scored 31/31 and was
**correctly let through**; a gate that reddened there would be measuring prose
length. The second — *"at most eight words"* — blocked at 29/31, −6.5%, and the
drop was targeted rather than diffuse.

**Act 3 — Self-healing, human-triggered.** A schema change breaks a contract
test; a classifier decides it is drift rather than a real defect, and a **human**
runs Claude Code against that verdict. The model call is made by a person, not by
a GitHub Action — running it headless would need a CI identity holding
`bedrock:InvokeModel`, permanently weakening the platform's first invariant to
buy one demo act. The trade was declined and written up.

*(Each act has a GIF in the repository README — reuse them here.)*

---

## What it actually cost

| | |
|---|---|
| Total spend, seven days, everything | **$20.45** |
| Cost of the answers the product actually served | **$0.0031** |
| Share of spend that is LLM-as-judge | **69%** |
| Idle cost | **$0** — no provisioned floors |
| Judge agreement with hand-labelled answers | **90%** against a 0.8 floor |

**The QA machinery cost roughly 6,500× the product it was grading, and the judge
is 69% of that.** At this scale that is the correct trade — the thesis is that
the gate is the expensive part and worth it. At any real scale it is the first
thing you would optimise.

---

## The finding worth publishing

Every defect this project found in seven days was found by one of three things:
**deploying it, changing something of a shape the system had never seen, or a
human reading the output.** Not one was found by adding another test of the kind
already there.

> **The recurring defect was never broken code. It was checks that could not
> fail.**

Eleven times across six milestones the gate was green while something underneath
it was measuring nothing. A trace check that read the runtime's own
instrumentation and vouched for telemetry that was being discarded. A dashboard
query correct in every clause a test could name, rendering an empty column. A
type comparison returning `None` on both sides. An architecture diagram verified
twice against a PNG export, which would have published as correctly-shaped boxes
containing **no text at all** — found forty minutes after the section naming that
exact pattern was written, on the artifact illustrating it.

That last one is the honest state of the art: not that the pattern was solved,
but that it is frequent enough to catch someone actively looking for it.

---

## What would make this production

Tiny scale, production *shape* — a deliberate scope, published with its gaps
named rather than implied. **Nothing here can page anybody** (log queries, not
custom metrics, because metrics bill while idle). **One account, one stage.**
**The canary is a stand-in, and a saturated one** — the incumbent scores 31/31,
so improvement is arithmetically unreachable. **No trajectory evals**, justified
only while the agent's trajectory is a constant. **The defect-leakage counter is
incremented by hand**, and the panel says so on its face.

Each gap is an ADR, written the day the trade was made.

---

## Stack

Python 3.12 · AWS CDK · Lambda · DynamoDB on-demand · Amazon Bedrock (Haiku
serves, Sonnet judges) · Bedrock Guardrails · MCP · Cedar · OpenTelemetry ·
CloudWatch Logs Insights · GitHub Actions with OIDC · built with Claude Code.

---

## Suggested meta

- **Title:** AgentPave — an agentic AI platform with the quality gate built in
- **Description:** A miniature agent platform on AWS where evals, guardrails,
  tracing and a failing-closed CI gate are infrastructure, not add-ons. Built in
  public in seven days for $20.45.
- **Social image:** `docs/images/agentpave-github-social-preview@2x.png`
