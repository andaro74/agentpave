# ADR-023: The agent derives its search term with a heuristic, not with a second model call

**Status:** Accepted
**Date:** 2026-08-10
**Milestone:** M04

## Context

The scaffolded agent loop passed the user's whole question to `search_show` as
the query. `search_show` takes a query, not a sentence.

The MCP server defaults to fixtures mode, and fixture names are derived from
the query, so *"What network airs Severance?"* asks for
`search_shows__q-what-network-airs-severance-.json` — a file that does not
exist. Act 1's first real question would have raised `CatalogNotFound` before
any model was reached. Against live TVMaze it would not raise, but it would
depend on how forgiving its fuzzy match happened to be on the day of a
recording.

This was found while building `make walkthrough` and confirmed hermetically;
it had not yet reached a deployed run.

The correct general answer is tool-calling: give the model the tool schema and
let it choose the arguments. That is a second gateway round-trip per answer, on
a loop whose whole claim (ADR-018) is one tool, one turn, and it doubles the
serving cost of every case in every service's dataset. The wrong answer in the
other direction is to write the demo's questions around the defect, which hides
it behind a dataset nobody would write independently.

## Decision

**`search_subject()` derives the query from the question by heuristic: take the
capitalised runs, ignoring the sentence's first word, prefer the longest run,
and on a tie prefer the last. On no match, fall back to the question itself.**

**A second model call to choose tool arguments is out of scope for the thin
loop, and adding one requires superseding ADR-018.**

The rules are what they are for stated reasons: the first word is skipped
because English capitalises a sentence's opening regardless of what it is;
longest wins because multi-word titles exist; last wins ties because a question
names its subject at the end far more often than at the start.

The fallback matters as much as the rule. A miss degrades to the previous
behaviour — the whole question as the query — rather than to an exception. The
heuristic is allowed to be wrong; it is not allowed to break the loop.

Constraints carried forward:

- [ ] A service whose catalogue has lowercase or non-Latin titles cannot use
      this heuristic and must supersede this ADR
- [ ] If a second tool is ever added, argument selection is the decision that
      re-opens tool-calling, since a heuristic cannot choose *between* tools

## Consequences

**Easier.** One tool call, one model call, one turn — the loop keeps the shape
ADR-018 describes and its cost per answer is unchanged. The derivation is a
pure function over a string, so it is unit-tested in the hermetic gate with the
exact questions the seed dataset asks, and a regression is caught by `make
check` rather than by a deployed run.

**Worse, and this is the cost.** It is a heuristic and it is wrong for real
questions. It has no opinion about a lowercase title, it is fooled by any other
proper noun in the sentence — *"Which network broadcasts Severance, ABC or
Apple?"* derives the wrong subject — and it is English-shaped in a way nothing
in the code announces at the call site. A wrong subject does not error: it
fetches the wrong show and the model answers confidently from it, which is a
grounded-looking wrong answer, the exact failure mode this platform is built to
expose. The seed dataset's `must_contain` expectations are what would catch it,
and only for the shows they name.

**Forecloses nothing.** `search_subject` is one function behind `_grounding`.
Tool-calling replaces it without touching the gateway boundary or the guarded
span.

## References

- ADR-018 — the thin agent loop, one tool and one turn
- ADR-009 — the MCP server and its fixtures mode, whose query-derived fixture
  names are what made the defect deterministic rather than intermittent
- `templates/agent-tools/{{package}}/agent.py.j2` — `search_subject`
- `docs/ROADMAP.md` M04 — `make walkthrough`, Act 1
