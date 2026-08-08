"""Cedar authorization, evaluated in-process.

ARCHITECTURE.md §3 cuts Amazon Verified Permissions, not Cedar — this is the
real Cedar engine (`cedarpy`), running the same policy language a full-scale
platform would deploy to AVP. The cut is where evaluation happens, not what
evaluates.

The entity graph is derived from the registry rather than hand-written, so a
tool cannot exist in `tools.yaml` and be invisible to policy, or be granted by
policy without a declared contract. Cedar is default-deny, so an agent nobody
wrote a rule for is denied without any rule saying so.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cedarpy

from .registry import Registry, load_registry

POLICY_DIR = Path(__file__).parent / "policies"
DEFAULT_POLICY_PATH = POLICY_DIR / "catalog.cedar"
DEFAULT_SCHEMA_PATH = POLICY_DIR / "schema.cedarschema.json"

# The group every catalogue tool belongs to. Named here rather than in each
# tool's contract: group membership is a policy concern, and putting it in
# tools.yaml would let a tool grant itself access by editing its own entry.
CATALOG_GROUP = "catalog"

INVOKE_ACTION = 'Action::"invoke"'


@dataclass(frozen=True)
class Decision:
    """One authorization decision, with enough context to log it usefully."""

    allowed: bool
    agent_id: str
    tool_name: str
    reason: str

    def __str__(self) -> str:
        verdict = "allow" if self.allowed else "DENY"
        return f"{verdict} {self.agent_id} -> {self.tool_name}: {self.reason}"


def entities_from_registry(registry: Registry, *, agent_ids: list[str]) -> list[dict[str, Any]]:
    """Build the Cedar entity graph from declared contracts.

    Agents are materialised only so the graph is complete for validation.
    An agent absent from this list is not thereby denied — Cedar's default-deny
    does that — so callers never need to pre-register an identity to have it
    correctly refused.
    """
    entities: list[dict[str, Any]] = [
        {"uid": {"type": "ToolGroup", "id": CATALOG_GROUP}, "attrs": {}, "parents": []}
    ]

    for tool in registry.tools:
        entities.append(
            {
                "uid": {"type": "Tool", "id": tool.name},
                "attrs": {"consequence": tool.consequence},
                "parents": [{"type": "ToolGroup", "id": CATALOG_GROUP}],
            }
        )

    for agent_id in agent_ids:
        entities.append({"uid": {"type": "Agent", "id": agent_id}, "attrs": {}, "parents": []})

    return entities


class Authorizer:
    """Decides whether an agent may invoke a tool."""

    def __init__(
        self,
        *,
        registry: Registry | None = None,
        policy_path: Path | None = None,
        schema_path: Path | None = None,
        known_agents: list[str] | None = None,
    ) -> None:
        self.registry = registry or load_registry()
        self._policies = (policy_path or DEFAULT_POLICY_PATH).read_text(encoding="utf-8")
        self._schema = json.loads((schema_path or DEFAULT_SCHEMA_PATH).read_text(encoding="utf-8"))
        self._entities = entities_from_registry(
            self.registry, agent_ids=known_agents or ["catalog-agent"]
        )

    def validate(self) -> None:
        """Check the policies against the Cedar schema. Raises on any error.

        This is the hermetic gate's policy lint. A policy that references a
        misspelled attribute is not a syntax error to Cedar — it simply never
        matches, so the rule silently stops applying. Validation is what turns
        that into a failure.
        """
        result = cedarpy.validate_policies(self._policies, self._schema)
        if not result.validation_passed:
            raise ValueError(f"Cedar policy validation failed: {result.errors}")

    def decide(self, agent_id: str, tool_name: str) -> Decision:
        """Authorize one invocation.

        An unknown tool is denied rather than raising: from policy's point of
        view "this tool does not exist" and "you may not call it" are the same
        answer, and raising would leak registry contents to an unauthorized
        caller through the difference in error shape.
        """
        request = {
            "principal": {"type": "Agent", "id": agent_id},
            "action": INVOKE_ACTION,
            "resource": {"type": "Tool", "id": tool_name},
            "context": {},
        }

        if tool_name not in self.registry.names:
            return Decision(False, agent_id, tool_name, "no such tool in the registry")

        result = cedarpy.is_authorized(request, self._policies, self._entities, self._schema)
        allowed = result.decision == cedarpy.Decision.Allow

        reasons = getattr(result.diagnostics, "reasons", None) or []
        reason = (
            f"permitted by policy {', '.join(reasons)}"
            if allowed and reasons
            else "allowed"
            if allowed
            else "no policy permits this agent to invoke this tool"
        )
        return Decision(allowed, agent_id, tool_name, reason)
