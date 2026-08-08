"""The tool contract suite.

Runs unchanged against recorded fixtures in `make check` and against the
deployed Lambda in `make conformance` — see conftest.py for the seam that makes
that possible.

The registry is the contract. These tests check that the served tool matches it
in both directions: the schemas MCP advertises agree with the ones declared,
and every response validates against the declared output schema. Neither is
generated from the other, so disagreement is real drift rather than a
tautology.
"""

import copy
from typing import Any

import pytest
from agentpave_registry.registry import Registry, load_registry
from conftest import DeployedDriver, TransportError
from jsonschema import Draft202012Validator

SEVERANCE_ID = 44933
RECORDED_DATE = "2026-08-07"

REGISTRY = load_registry()

# The calls the suite exercises, and the arguments that reach recorded
# fixtures. Kept as data so a new tool cannot be added to the registry without
# someone noticing there is nothing here to call it with.
HAPPY_PATH: dict[str, dict[str, Any]] = {
    "search_show": {"query": "severance"},
    "get_episodes": {"show_id": SEVERANCE_ID},
    "get_schedule": {"date": RECORDED_DATE},
}


def _types_of(schema: dict[str, Any]) -> dict[str, Any]:
    """Property name -> declared JSON type, normalised for comparison.

    MCP derives its schemas from Python signatures and adds `title`s; the
    registry is hand-authored. Comparing names, requiredness, and types catches
    real drift without failing on cosmetic differences that mean nothing.
    """
    return {name: prop.get("type") for name, prop in (schema.get("properties") or {}).items()}


def contract_violations(tool_name: str, data: Any, *, registry: Registry = REGISTRY) -> list[str]:
    """Every way `data` fails the tool's declared output schema.

    The single place the contract is enforced. Both the conformance assertion
    and the mutation test below call it, so the guard exercises the real check
    rather than a re-implementation that could drift from it.
    """
    validator = Draft202012Validator(registry.tool(tool_name).output_schema)
    return [error.message for error in sorted(validator.iter_errors(data), key=lambda e: e.path)]


# ── the tool set ──────────────────────────────────────────────────────────


def test_server_offers_exactly_the_registered_tools(driver: Any) -> None:
    # Drift in either direction is a failure: an undeclared tool is ungoverned,
    # and a declared tool that is not served is a contract nobody honours.
    assert set(driver.list_tools()) == REGISTRY.names


@pytest.mark.parametrize("tool_name", sorted(HAPPY_PATH))
def test_every_registered_tool_is_exercised(tool_name: str) -> None:
    # Guards the suite against itself: adding a tool to the registry without
    # adding a call here would leave it silently untested.
    assert tool_name in REGISTRY.names


def test_every_registered_tool_has_a_happy_path_call() -> None:
    assert REGISTRY.names == set(HAPPY_PATH)


# ── schema conformance ────────────────────────────────────────────────────


@pytest.mark.parametrize("tool_name", sorted(REGISTRY.names))
def test_advertised_input_properties_match_the_contract(driver: Any, tool_name: str) -> None:
    declared = REGISTRY.tool(tool_name).input_schema
    advertised = driver.list_tools()[tool_name]
    assert _types_of(advertised) == _types_of(declared)


@pytest.mark.parametrize("tool_name", sorted(REGISTRY.names))
def test_advertised_required_fields_match_the_contract(driver: Any, tool_name: str) -> None:
    declared = REGISTRY.tool(tool_name).input_schema
    advertised = driver.list_tools()[tool_name]
    assert set(advertised.get("required") or []) == set(declared.get("required") or [])


@pytest.mark.parametrize("tool_name", sorted(REGISTRY.names))
def test_advertised_description_is_the_registered_one(driver: Any, tool_name: str) -> None:
    # The description is what an agent reads to choose a tool, so it is part of
    # the contract rather than incidental prose.
    assert driver.describe()[tool_name].strip() == REGISTRY.tool(tool_name).description.strip()


@pytest.mark.parametrize("tool_name", sorted(HAPPY_PATH))
def test_response_validates_against_the_declared_output_schema(driver: Any, tool_name: str) -> None:
    # The load-bearing assertion. Breaking the declared schema turns this red,
    # which is what makes the registry a contract rather than documentation.
    outcome = driver.call(tool_name, HAPPY_PATH[tool_name])
    assert outcome.ok, outcome.error

    violations = contract_violations(tool_name, outcome.data)
    assert not violations, f"{tool_name} response violates its contract: {violations}"


# ── the suite's own teeth ─────────────────────────────────────────────────


def _with_renamed_output_property(tool_name: str, old: str, new: str) -> Registry:
    """A copy of the registry with one output property renamed.

    Models the realistic drift: someone edits the contract and not the code.
    """
    payload = copy.deepcopy(REGISTRY.model_dump())
    for tool in payload["tools"]:
        if tool["name"] == tool_name:
            properties = tool["output_schema"]["properties"]["shows"]["items"]["properties"]
            properties[new] = properties.pop(old)
    return Registry.model_validate(payload)


def test_contract_check_catches_schema_drift(driver: Any) -> None:
    """The permanent test of the tests.

    A suite that has never been observed failing is indistinguishable from one
    that cannot fail. The deliberately red commit in this repo's history proved
    that once; this proves it on every run, so the check cannot quietly go
    toothless after the story has been told.
    """
    outcome = driver.call("search_show", HAPPY_PATH["search_show"])
    assert outcome.ok, outcome.error
    assert not contract_violations("search_show", outcome.data)

    drifted = _with_renamed_output_property("search_show", "network", "channel")
    violations = contract_violations("search_show", outcome.data, registry=drifted)

    assert violations, "the contract check accepted a response its schema should reject"
    assert any("network" in message for message in violations)


def test_an_unreachable_endpoint_is_not_a_tool_error() -> None:
    """The other way this suite could go toothless.

    The first conformance run reported three deployed tests as PASSED against
    an endpoint answering 403 to everything: the driver folded transport
    failure into `ok=False`, which is precisely what those tests assert. Every
    "this call must fail" test was therefore satisfied by nothing working.

    Hermetic: port 1 on loopback refuses immediately, so this reaches no
    network and needs no credentials.
    """
    dead = DeployedDriver("http://127.0.0.1:1/mcp", auth=None)

    with pytest.raises(TransportError):
        dead.call("search_show", HAPPY_PATH["search_show"])


# ── error shapes ──────────────────────────────────────────────────────────


def test_unknown_show_fails_without_leaking_a_stack_trace(driver: Any) -> None:
    outcome = driver.call("get_episodes", {"show_id": 99999999})
    assert not outcome.ok
    assert outcome.error
    assert "Traceback" not in outcome.error


def test_missing_required_argument_is_rejected(driver: Any) -> None:
    outcome = driver.call("search_show", {})
    assert not outcome.ok


def test_wrong_argument_type_is_rejected(driver: Any) -> None:
    outcome = driver.call("get_episodes", {"show_id": "not-a-number"})
    assert not outcome.ok


def test_no_matches_is_a_result_not_an_error(driver: Any) -> None:
    # "Nothing matched" is an answer. Reporting it as a failure would push
    # every caller into treating an ordinary outcome as an exception.
    outcome = driver.call("search_show", {"query": "zzzz-no-such-show"})
    assert outcome.ok
    assert outcome.data == {"shows": []}


# ── policy ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool_name", sorted(HAPPY_PATH))
def test_wrong_identity_is_denied(rogue_driver: Any, tool_name: str) -> None:
    # ROADMAP M02 hermetic gate: wrong-identity deny asserted.
    outcome = rogue_driver.call(tool_name, HAPPY_PATH[tool_name])
    assert not outcome.ok
    assert "rogue-agent" in (outcome.error or "")


def test_denial_happens_before_the_tool_runs(rogue_driver: Any) -> None:
    # A policy consulted after the side effect is an audit log, not an
    # authorization check. These tools are side-effect-free, which is exactly
    # why the ordering should be pinned here — on the tool where getting it
    # wrong is free.
    rogue_driver.call("search_show", {"query": "severance"})
    assert rogue_driver.client.methods_used == ()


def test_denial_is_logged(rogue_driver: Any, caplog: pytest.LogCaptureFixture) -> None:
    # M02 passes on "policy denied *and logged*", never on a silent refusal.
    with caplog.at_level("WARNING", logger="agentpave.mcp.tvmaze"):
        rogue_driver.call("search_show", {"query": "severance"})
    assert any("DENY" in record.message for record in caplog.records)


# ── side-effect freedom ───────────────────────────────────────────────────


def test_read_tools_issue_only_get(driver: Any) -> None:
    """The registry's `consequence: read` claim, checked rather than trusted.

    Skipped on the deployed driver: the client that issues the HTTP calls lives
    inside the Lambda, so this process cannot observe its methods. That is a
    real limit of the deployed gate, not an oversight — recorded here so nobody
    reads a green conformance run as having proved it.
    """
    if driver.client is None:
        pytest.skip("methods are only observable in-process (see docstring)")

    for tool_name, arguments in HAPPY_PATH.items():
        assert driver.call(tool_name, arguments).ok

    assert set(driver.client.methods_used) == {"GET"}


def test_every_tool_is_declared_side_effect_free() -> None:
    assert all(REGISTRY.tool(name).consequence == "read" for name in HAPPY_PATH)
