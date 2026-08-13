# ADR-037: The contract suite compares only top-level types, so union-typed parameters go unchecked

**Status:** Accepted
**Date:** 2026-08-13
**Milestone:** M06

## Context

ARCHITECTURE.md §2 says the registry is a contract and the MCP server must
advertise exactly what it declares — "drift in either direction fails the
hermetic gate". `platform/registry/agentpave_registry/tools.yaml` repeats the
claim in its own header. The assertion behind both statements is
`test_advertised_input_properties_match_the_contract`, which compares the two
schemas through one helper:

```python
def _types_of(schema):
    return {name: prop.get("type") for name, prop in (schema.get("properties") or {}).items()}
```

The helper is deliberately lossy. MCP derives its schemas from Python
signatures and decorates them with `title` and `default`; the registry is
hand-authored and carries `pattern`, `minimum`, and `description`. Comparing
the two verbatim would fail on presentation, so the helper narrows to the one
field that carries meaning.

M06's Act 3 staged a deliberate schema change — `get_schedule` gained
`limit: int | None = None` — to exercise `pave selfheal`. MCP advertises that
parameter as a union:

```json
"limit": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": null}
```

A union has no top-level `type`. `prop.get("type")` returns `None`, and once
the registry declares `limit` with the same `anyOf` shape, `None == None` and
the assertion passes. **It would pass just as green if the registry declared
`limit` a string, an array, or an object.** For every parameter of this shape
the check is vacuous, and nothing in the output distinguishes a vacuous pass
from a real one.

The gap was found by using the suite, not by reading it. The three drift tests
were reviewed twice in M06 — once to choose which failures `selfheal` may
propose repairs for, once when the classifier was rehearsed against real pytest
output — and neither pass noticed, because every parameter the platform had
until Act 3 was a plain `str` or `int`.

Fixing it is not a one-line change, which is the reason this ADR exists rather
than a commit. A strict comparison fails immediately on the registry's own
`minimum: 1` and `description` against MCP's `title` and `default` — the noise
the helper was written to absorb. A correct fix compares *union structure*
while still normalising presentation, which is a design change to the contract
suite with its own tests and its own decisions about what counts as cosmetic.
M06 is a stretch milestone already built at half scope (ADR-035), and M07 is
documentation and publication.

## Decision

The gap is recorded and left unfixed in M06.

`_types_of` is **not** repaired under a `schema_drift` verdict. The classifier
licenses edits to the declared contract and nothing else, and an AI widening a
comparison so that its own change passes is the exact failure
`pave selfheal` exists to prevent (ADR-035, `SCHEMA_DRIFT_TESTS`). This holds
even when the helper is genuinely weak: the licence is the point, not the
diagnosis.

Three constraints follow, and they are checklist items for whoever picks this
up:

1. **M07's known-limits section must carry it.** The README may not claim the
   registry is an enforced contract without stating that union-typed
   parameters are exempt from the type check. `docs/VALIDATION.md` carries the
   finding forward until then.
2. **A repair must add a test that fails against today's helper**, declaring a
   union-typed parameter as the wrong union and asserting the suite goes red.
   A fix verified only by the suite continuing to pass has demonstrated
   nothing — the suite passes now.
3. **No new union-typed tool parameter may be added until (1) is written**,
   because each one silently enlarges the unchecked surface.

## Consequences

The registry's contract claim is weaker than ARCHITECTURE.md states, and this
ADR is now the honest version of that claim. Anyone reading §2 and expecting
the hermetic gate to catch a mis-declared optional parameter is wrong, and
`get_schedule`'s `limit` is the live example: it is declared correctly today by
authorship, not by enforcement.

The cost is carried in the artifact. The one union-typed parameter the platform
has was added in M06 and shipped under a check that does not check it. That is
a real hole in a repository whose thesis is that quality gates catch what
review misses, and deferring it means the hole is documented rather than
closed.

Against that: the gap was found the only way it could have been — by making a
change of a shape the platform had never made before — and the discovery
happened inside the self-healing exercise, where the AI's refusal to touch the
helper is what left the finding visible for a human. A classifier permissive
enough to "fix" `_types_of` would have closed the test and the finding in one
commit, and nobody would have known the assertion had ever been empty. The
deferral keeps that story intact and costs one unchecked parameter.

## References

- ARCHITECTURE.md §2 (the registry as contract), §3 (scope cuts)
- ROADMAP M06 (self-healing + shadow eval), M07 (docs + known limits)
- ADR-035 — self-healing in CI deferred; `SCHEMA_DRIFT_TESTS` and why the
  classifier refuses by default
- ADR-036 — the shadow candidate is a routed feature
- `platform/mcp-tvmaze/tests/test_mcp_contract.py` — `_types_of`
- Pull request #2, the `ai-proposed` repair that surfaced this
