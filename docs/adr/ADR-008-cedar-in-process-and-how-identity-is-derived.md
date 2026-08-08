# ADR-008: Cedar runs in-process via cedarpy, and the deployed server authorizes as a fixed service identity

**Status:** Accepted
**Date:** 2026-08-08
**Milestone:** M02

## Context

ARCHITECTURE.md §3 says tool access is decided by "Cedar evaluated in-process
rather than via Amazon Verified Permissions". That settles *where* evaluation
happens and leaves two things open, both of which turned out to matter.

**What evaluates.** "Cedar in-process" could mean the real Cedar engine or a
hand-rolled evaluator for the subset of the language we use. A re-implementation
would be quick and would pass its own tests, which is precisely the danger: the
policies would then be Cedar-shaped text evaluated by something that is not
Cedar, and every claim about Cedar semantics — `forbid` overriding `permit`,
default-deny, attribute evaluation order — would be a claim about our code.

**Whose identity.** Cedar answers "may *this principal* invoke this tool", so
something has to say who the principal is. Under stdio the answer is easy: the
process that launched the server declares it. Over HTTP it is not, because
anything the caller sends is something the caller chose.

## Decision

**Cedar is the real engine.** `cedarpy` 4.8.7 wraps the upstream Rust
implementation and ships wheels for both the development platform and
`aarch64` Linux, so the same engine runs in the hermetic gate and in the
Lambda under ADR-007's no-Docker asset build. Re-implementing Cedar evaluation
is forbidden without a superseding ADR.

The entity graph is derived from `tools.yaml` rather than authored separately,
so a tool cannot be declared and be invisible to policy, or be granted by
policy without a declared contract. Group membership is deliberately **not** a
field on `ToolContract`: a tool that declared its own group could grant itself
access by editing its own registry entry.

Policies are validated against a Cedar schema in the hermetic gate. A policy
that references a misspelled attribute is not a syntax error to Cedar — the
rule simply never matches, so it silently stops applying. Validation is what
turns that into a failure, and a test proves the validation rejects such a
policy rather than merely being called.

**Identity, under stdio**, comes from `AGENTPAVE_AGENT_ID`, falling back to
`anonymous` — an identity no policy grants anything to. A missing identity is
therefore denied by the ordinary policy path rather than by a special case
someone could forget to write.

**Identity, over HTTP**, is the fixed service identity configured on the
function, not anything the caller sends. The Function URL requires SigV4, so
only an authorized AWS principal reaches the code at all; that is the
authentication. Accepting an agent id from a request header would not be
authentication at all — it is a string anyone who can reach the endpoint can
choose — and shipping it would make the policy engine decorative on exactly the
surface where it matters most.

## Consequences

- Claims about Cedar semantics are claims about Cedar. The `forbid` rule that
  denies any non-`read` tool overrides the group `permit` because that is how
  Cedar works, and the test asserting it is testing the engine's behaviour
  rather than our reading of it.
- **The deployed server authorizes as one identity, so the wrong-identity deny
  is proven hermetically and not on the deployed path.** `make conformance`
  exercises schema conformance, error shapes, and the happy path against real
  infrastructure; it does not and cannot demonstrate a denial, because there is
  no second identity to deny. This is the weakest part of M02's deployed gate
  and the reason ROADMAP puts wrong-identity deny in the hermetic gate.
- Cedar adds ~5 MB of native wheel to the MCP asset, which is now ~47 MB
  against a 250 MB limit. Not a problem yet; a component that adds a second
  engine of comparable weight would need to reconsider.
- Per-caller identity is deferred, not designed away. **This constrains M04**:
  when the catalog agent lands it will call this server with its own IAM role,
  and identity must then be derived from the signed principal — mapping role
  ARN to agent id — rather than from an environment variable. Until that
  exists, the platform has one agent and a mapping table would be speculative
  generality with a security-shaped hole in the middle.
- Cedar's error messages are terse, and the schema format is unforgiving about
  entity shapes. Anyone editing the policy should expect the feedback loop to
  be "validation failed" plus a location, not an explanation.

## References

- `docs/ARCHITECTURE.md` §3 (Cedar in-process; scope cuts)
- `docs/ROADMAP.md` M02 hermetic gate (wrong-identity deny asserted)
- [ADR-003](ADR-003-lambda-over-agentcore-runtime.md) — constraint (a), enforced
  by the MCP stack's negative IAM assertion
- [ADR-007](ADR-007-lambda-asset-built-without-docker.md) — why the `aarch64`
  wheel availability was a gating question
- [Cedar policy language](https://www.cedarpolicy.com/)
