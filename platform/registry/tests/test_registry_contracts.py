"""Registry validation — the lint that stops an unusable contract reaching the gate."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from agentpave_registry.registry import Registry, ToolContract, load_registry
from pydantic import ValidationError


def _valid_tool(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "search_show",
        "owner": "platform-team",
        "tool_version": "1.0.0",
        "description": "Search the catalogue by show name and return the matches.",
        "consequence": "read",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        "output_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["shows"],
            "properties": {"shows": {"type": "array"}},
        },
    } | overrides


# ── the committed registry ────────────────────────────────────────────────


def test_committed_registry_is_valid() -> None:
    assert load_registry().names


def test_committed_registry_declares_the_catalogue_tools() -> None:
    # ROADMAP M02: search show, episodes, schedule.
    assert load_registry().names == {"search_show", "get_episodes", "get_schedule"}


def test_every_committed_tool_is_side_effect_free() -> None:
    # ARCHITECTURE §3 pins the sample tool's consequence class at `read`. A
    # write-classed tool arriving here is a design decision that needs an ADR,
    # so it fails the gate rather than shipping on the strength of a review.
    consequences = {tool.name: tool.consequence for tool in load_registry().tools}
    offenders = {n: c for n, c in consequences.items() if c != "read"}
    assert not offenders, f"non-read tools need an ADR before they ship: {offenders}"


def test_every_committed_tool_has_an_owner() -> None:
    assert all(tool.owner for tool in load_registry().tools)


def test_tool_lookup_raises_for_an_unknown_name() -> None:
    with pytest.raises(KeyError, match="no tool named"):
        load_registry().tool("delete_everything")


# ── schema lint ───────────────────────────────────────────────────────────


def test_schema_without_additional_properties_false_is_rejected() -> None:
    # Otherwise an unknown field passes validation and the contract cannot
    # detect a caller sending something the tool silently ignores.
    tool = _valid_tool()
    del tool["input_schema"]["additionalProperties"]
    with pytest.raises(ValidationError, match="additionalProperties"):
        ToolContract.model_validate(tool)


def test_schema_requiring_an_undefined_field_is_rejected() -> None:
    # Unsatisfiable: every call fails validation for a reason nobody will guess.
    tool = _valid_tool()
    tool["input_schema"]["required"] = ["query", "not_a_field"]
    with pytest.raises(ValidationError, match="requires fields it does not define"):
        ToolContract.model_validate(tool)


def test_schema_without_a_required_list_is_rejected() -> None:
    tool = _valid_tool()
    del tool["output_schema"]["required"]
    with pytest.raises(ValidationError, match="required list"):
        ToolContract.model_validate(tool)


def test_non_object_schema_is_rejected() -> None:
    tool = _valid_tool()
    tool["input_schema"] = {"type": "string"}
    with pytest.raises(ValidationError, match="must be a JSON Schema object"):
        ToolContract.model_validate(tool)


def test_schema_without_properties_is_rejected() -> None:
    tool = _valid_tool()
    tool["input_schema"]["properties"] = {}
    with pytest.raises(ValidationError, match="must declare properties"):
        ToolContract.model_validate(tool)


def test_output_schema_is_linted_too() -> None:
    # Both directions of the contract matter; only linting inputs would leave
    # responses unconstrained.
    tool = _valid_tool()
    tool["output_schema"]["additionalProperties"] = True
    with pytest.raises(ValidationError, match="output_schema"):
        ToolContract.model_validate(tool)


# ── metadata lint ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "1.0.0-rc1", "latest"])
def test_non_semver_version_is_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        ToolContract.model_validate(_valid_tool(tool_version=bad))


@pytest.mark.parametrize("bad", ["SearchShow", "search-show", "1search", "search show"])
def test_non_snake_case_tool_name_is_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        ToolContract.model_validate(_valid_tool(name=bad))


def test_missing_owner_is_rejected() -> None:
    # An unowned tool has nobody to page when its contract breaks.
    with pytest.raises(ValidationError):
        ToolContract.model_validate(_valid_tool(owner=""))


def test_unknown_consequence_class_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolContract.model_validate(_valid_tool(consequence="mostly_harmless"))


@pytest.mark.parametrize("bad", ["Search show.", "Searches for shows", ""])
def test_placeholder_description_is_rejected(bad: str) -> None:
    # The description is what an agent reads to decide whether to call the
    # tool. A bare restatement of the name degrades tool selection silently,
    # and the length floor is the part of that which can actually be enforced.
    with pytest.raises(ValidationError, match="at least 40 characters"):
        ToolContract.model_validate(_valid_tool(description=bad))


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolContract.model_validate(_valid_tool(consequenc="read"))


# ── registry-level rules ──────────────────────────────────────────────────


def test_duplicate_tool_names_are_rejected() -> None:
    registry = {"version": 1, "tools": [_valid_tool(), _valid_tool()]}
    with pytest.raises(ValidationError, match="duplicate tool names"):
        Registry.model_validate(registry)


def test_empty_registry_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Registry.model_validate({"version": 1, "tools": []})


def test_load_registry_accepts_an_explicit_path(tmp_path: Path) -> None:
    path = tmp_path / "tools.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "tools": [copy.deepcopy(_valid_tool())]}),
        encoding="utf-8",
    )
    assert load_registry(path).names == {"search_show"}
