"""Schema and loader for the tool registry.

Same author-then-lint shape as the guardrail policy (ADR-005): the YAML is the
contract, and the rules below are what stop an unusable contract from reaching
the gate. The rules are deliberately about *checkability* — a schema that
cannot be validated against, or a `required` field that does not exist, is a
contract in name only.
"""

from collections import Counter
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_REGISTRY_PATH = Path(__file__).parent / "tools.yaml"

# What calling the tool does to the world. `read` is a claim the contract suite
# checks by recording HTTP methods; the other two exist so a tool that needs
# them has somewhere honest to say so rather than mislabelling itself `read`.
Consequence = Literal["read", "write", "admin"]

SEMVER = r"^\d+\.\d+\.\d+$"
TOOL_NAME = r"^[a-z][a-z0-9_]*$"


def _lint_json_schema(schema: dict[str, Any], *, label: str) -> None:
    """Reject schemas that cannot serve as a contract.

    Not a full JSON Schema validation — that is jsonschema's job at call time.
    These are the three ways a schema silently stops constraining anything.
    """
    if schema.get("type") != "object":
        raise ValueError(f"{label} must be a JSON Schema object")

    # Without this, an unknown field passes validation and the contract cannot
    # detect a caller sending something the tool will ignore.
    if schema.get("additionalProperties") is not False:
        raise ValueError(f"{label} must set additionalProperties: false")

    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise ValueError(f"{label} must declare properties")

    required = schema.get("required")
    if not isinstance(required, list):
        raise ValueError(f"{label} must declare a required list (use [] if nothing is required)")

    # A required field that isn't in properties can never be satisfied, which
    # makes every call fail validation for a reason nobody will guess.
    missing = [name for name in required if name not in properties]
    if missing:
        raise ValueError(f"{label} requires fields it does not define: {sorted(missing)}")


class ToolContract(BaseModel):
    """One tool's declared contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=TOOL_NAME, max_length=64)
    owner: str = Field(min_length=1, max_length=64)
    # Not `version` — pydantic-adjacent tooling and MCP both use that name for
    # other things, and a collision here would be quietly confusing.
    tool_version: str = Field(pattern=SEMVER)
    # The description is what an agent reads to decide whether to call this
    # tool, so a placeholder degrades tool selection silently. The length floor
    # is the enforceable part: it is long enough that a bare restatement of the
    # name cannot satisfy it, and short enough not to invite padding.
    description: str = Field(min_length=40)
    consequence: Consequence
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]

    @model_validator(mode="after")
    def _schemas_are_usable_contracts(self) -> Self:
        _lint_json_schema(self.input_schema, label=f"{self.name}.input_schema")
        _lint_json_schema(self.output_schema, label=f"{self.name}.output_schema")
        return self


class Registry(BaseModel):
    """The whole registry, validated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    tools: list[ToolContract] = Field(min_length=1)

    @model_validator(mode="after")
    def _tool_names_are_unique(self) -> Self:
        duplicates = [n for n, count in Counter(t.name for t in self.tools).items() if count > 1]
        if duplicates:
            raise ValueError(f"duplicate tool names: {sorted(duplicates)}")
        return self

    @property
    def names(self) -> set[str]:
        return {tool.name for tool in self.tools}

    def tool(self, name: str) -> ToolContract:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise KeyError(f"no tool named {name!r} in the registry")


def load_registry(path: Path | None = None) -> Registry:
    """Parse and validate the registry. Raises on anything malformed."""
    source = path or DEFAULT_REGISTRY_PATH
    return Registry.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")))
