# ADR-020: The scaffolder derives a service's tool allow-list from Cedar rather than writing one

**Status:** Accepted
**Date:** 2026-08-10
**Milestone:** M04

## Context

A scaffolded service needs to know which tools it may call. The obvious
implementation is a list in the template — three tool names, written once,
rendered into every service.

A hand-written list is a comment. It can claim access the policy does not
grant, in which case the service fails at runtime with a Cedar denial nobody
connects to a template written weeks earlier; or it can omit access the policy
does grant, in which case a capability quietly does not exist. Neither shows up
in a test, because the list and the policy are two files with nothing joining
them.

The platform already has the answer in a form it can be asked for: Cedar runs
in-process (ADR-008), the registry names every tool, and the authorizer decides
by identity. The scaffolder knows the identity — it is the service name it was
just given.

## Decision

**`pave new` asks the Cedar authorizer which tools the new identity is
permitted to invoke, and renders the answer. A hand-written tool allow-list in
a template or a scaffolded service is forbidden.**

The rendered list is explicitly **not** the control, and the template says so:
Cedar in the MCP server is the control, and it denies an ungranted call
whatever the rendered file says. The copy exists so that an ungranted call
fails in-process with a readable message instead of surfacing as a denial
someone has to find in CloudWatch. Editing it grants nothing.

An identity nobody has written a `permit` for renders an **empty** tuple and a
comment saying why. That is the correct answer, not an error: Cedar is
default-deny, so a new agent has no tools until someone writes the policy. The
paved road teaches the governance model at scaffold time rather than letting
someone discover it as a 403 later.

Constraints carried forward:

- [ ] Gaining a tool means adding a Cedar `permit` and re-rendering, never
      editing the rendered file
- [ ] The rendered list must never exceed what policy permits; a test asserts
      the subset relation in that direction, because a service claiming a tool
      it cannot call fails at runtime while one claiming a tool it *can* is
      merely redundant

## Consequences

**Easier.** The rendered service and the policy cannot disagree at birth. The
governance story a new service tells is the one its policy actually supports,
and a reviewer reading `tools.py` is reading Cedar's answer rather than an
author's recollection of it.

**Worse, and this is the cost.** `pave` now depends on `agentpave-registry`, so
the scaffolder cannot run without the platform's policy code present — a
template that was pure text rendering now needs a working authorizer to
produce a file. It also means the allow-list is a snapshot: a `permit` added
after scaffolding does not reach the service until someone re-renders, and
nothing currently detects that drift. The rendered file is stale-able in a way
a hand-written one is not, because a hand-written one was never claiming to be
derived.

**Forecloses nothing.** The derivation is one function over the authorizer. A
future service that needs a live check calls Cedar at runtime, which is what
the MCP server already does.

## References

- ADR-008 — Cedar in-process, and how identity is derived
- ADR-003's migration checklist — all tool access through MCP
- `pave/agentpave_pave/scaffold.py` — `granted_tools`
- `templates/agent-tools/{{package}}/tools.py.j2`
