"""Cedar decides who may invoke what."""

from pathlib import Path
from typing import Any

import pytest
from agentpave_registry.authz import Authorizer, entities_from_registry
from agentpave_registry.registry import Registry, load_registry

CATALOG_AGENT = "catalog-agent"


def _tool(name: str, consequence: str = "read") -> dict[str, Any]:
    return {
        "name": name,
        "owner": "platform-team",
        "tool_version": "1.0.0",
        "description": "A tool that exists purely so this test has something to authorize.",
        "consequence": consequence,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [],
            "properties": {"q": {"type": "string"}},
        },
        "output_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [],
            "properties": {"r": {"type": "string"}},
        },
    }


@pytest.fixture(scope="module")
def authorizer() -> Authorizer:
    return Authorizer()


# ── the committed policy ──────────────────────────────────────────────────


def test_committed_policy_validates_against_the_schema(authorizer: Authorizer) -> None:
    # A policy referencing a misspelled attribute is not a Cedar syntax error —
    # it simply never matches, so the rule silently stops applying. Validation
    # is what turns that into a failure.
    authorizer.validate()


def test_validate_rejects_a_policy_with_a_misspelled_attribute(tmp_path: Path) -> None:
    # The test that proves the check above is load-bearing. Without it,
    # `validate()` passing would be indistinguishable from `validate()` being
    # unable to fail.
    policy = tmp_path / "typo.cedar"
    policy.write_text(
        'permit (principal, action == Action::"invoke", resource)\n'
        'when { resource.consequenceTYPO != "read" };\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Cedar policy validation failed"):
        Authorizer(policy_path=policy).validate()


@pytest.mark.parametrize("tool", ["search_show", "get_episodes", "get_schedule"])
def test_catalog_agent_may_invoke_every_catalogue_tool(authorizer: Authorizer, tool: str) -> None:
    assert authorizer.decide(CATALOG_AGENT, tool).allowed


# ── denial ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", ["search_show", "get_episodes", "get_schedule"])
def test_wrong_identity_is_denied(authorizer: Authorizer, tool: str) -> None:
    # ROADMAP M02 hermetic gate: wrong-identity deny asserted.
    assert not authorizer.decide("rogue-agent", tool).allowed


def test_unknown_agent_is_denied_without_being_registered(authorizer: Authorizer) -> None:
    # Cedar is default-deny, so an identity nobody wrote a rule for is refused
    # without any rule saying so — and without needing to be pre-registered
    # in order to be correctly refused.
    assert not authorizer.decide("agent-invented-next-quarter", "search_show").allowed


def test_unknown_tool_is_denied_not_raised(authorizer: Authorizer) -> None:
    # Raising would leak registry contents to an unauthorized caller through
    # the difference in error shape.
    decision = authorizer.decide(CATALOG_AGENT, "delete_everything")
    assert not decision.allowed
    assert "no such tool" in decision.reason


def test_denial_says_why(authorizer: Authorizer) -> None:
    # A denial with no reason is indistinguishable from a bug at 3am, and M02
    # passes on "policy denied *and logged*".
    decision = authorizer.decide("rogue-agent", "search_show")
    assert decision.reason
    assert "rogue-agent" in str(decision)
    assert "DENY" in str(decision)


# ── the forbid rule ───────────────────────────────────────────────────────


def _authorizer_with(tools: list[dict[str, Any]]) -> Authorizer:
    return Authorizer(registry=Registry.model_validate({"version": 1, "tools": tools}))


@pytest.mark.parametrize("consequence", ["write", "admin"])
def test_state_changing_tool_is_forbidden_even_when_permitted(consequence: str) -> None:
    # The forbid rule overrides the group permit. This is the case that
    # matters: someone adds a write tool to the catalogue and it is granted by
    # the same rule that grants the read tools — and Cedar still refuses.
    authorizer = _authorizer_with([_tool("mutate_catalogue", consequence)])
    assert not authorizer.decide(CATALOG_AGENT, "mutate_catalogue").allowed


def test_read_tool_in_the_same_registry_is_still_allowed() -> None:
    # Guards against the forbid rule being too broad — it must deny the write
    # tool without collaterally denying its neighbours.
    authorizer = _authorizer_with([_tool("mutate_catalogue", "write"), _tool("read_catalogue")])
    assert not authorizer.decide(CATALOG_AGENT, "mutate_catalogue").allowed
    assert authorizer.decide(CATALOG_AGENT, "read_catalogue").allowed


# ── the entity graph ──────────────────────────────────────────────────────


def test_entities_are_derived_from_the_registry() -> None:
    # A tool cannot exist in tools.yaml and be invisible to policy, or be
    # granted by policy without a declared contract.
    registry = load_registry()
    entities = entities_from_registry(registry, agent_ids=[CATALOG_AGENT])

    tool_ids = {e["uid"]["id"] for e in entities if e["uid"]["type"] == "Tool"}
    assert tool_ids == registry.names


def test_every_tool_entity_carries_its_consequence_class() -> None:
    # The forbid rule reads this attribute; a tool entity without it would
    # silently fall through the rule.
    entities = entities_from_registry(load_registry(), agent_ids=[CATALOG_AGENT])
    tools = [e for e in entities if e["uid"]["type"] == "Tool"]
    assert tools
    assert all(e["attrs"].get("consequence") for e in tools)


def test_every_tool_entity_is_in_the_catalog_group() -> None:
    # Group membership is what the permit rule matches on; a tool outside the
    # group would be denied for a reason that looks like a policy bug.
    entities = entities_from_registry(load_registry(), agent_ids=[CATALOG_AGENT])
    tools = [e for e in entities if e["uid"]["type"] == "Tool"]
    assert all(e["parents"] == [{"type": "ToolGroup", "id": "catalog"}] for e in tools)


def test_group_membership_is_not_self_declared() -> None:
    # Deliberately absent from ToolContract: if a tool declared its own group,
    # a tool could grant itself access by editing its own registry entry.
    assert "group" not in load_registry().tools[0].model_fields_set
    assert not hasattr(load_registry().tools[0], "group")


# ── policy loading ────────────────────────────────────────────────────────


def test_authorizer_accepts_explicit_policy_paths(tmp_path: Path) -> None:
    # A policy file that permits nothing denies everything — the sharpest
    # check that the file on disk is really what is being evaluated.
    policy = tmp_path / "empty.cedar"
    policy.write_text("// no rules\n", encoding="utf-8")

    authorizer = Authorizer(policy_path=policy)
    assert not authorizer.decide(CATALOG_AGENT, "search_show").allowed
