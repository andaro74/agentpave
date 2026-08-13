"""The tvmaze-catalog MCP server.

Cedar decides before the tool runs. That ordering is the whole point: a policy
consulted after the side effect is an audit log, not an authorization check.
These tools are side-effect-free, so the distinction costs nothing here — which
is exactly why it should be established here, on the tool where getting it
wrong is free, rather than on the first tool where it isn't.

The advertised tool schemas are derived by MCP from the function signatures;
the registry declares them independently. Neither is generated from the other,
so the contract suite comparing them is a real drift check rather than a
tautology.
"""

import logging
import os
from collections.abc import Callable

from agentpave_registry.authz import Authorizer
from mcp.server.mcpserver import MCPServer

from . import tools
from .client import TVMazeClient

logger = logging.getLogger("agentpave.mcp.tvmaze")

SERVER_NAME = "tvmaze-catalog"

# An identity nobody granted anything to. Used when none is supplied, so the
# absence of an identity is denied by the ordinary policy path rather than by a
# special case that could be forgotten.
ANONYMOUS = "anonymous"


class ToolDenied(Exception):
    """Policy refused this invocation."""


def principal_from_env() -> str:
    return os.environ.get("AGENTPAVE_AGENT_ID") or ANONYMOUS


def client_from_env() -> TVMazeClient:
    """Fixtures unless explicitly told otherwise.

    The default is the safe one: a misconfigured deployment replays recordings
    rather than silently putting a rate-limited third party in the request
    path. Live mode has to be asked for.
    """
    mode = os.environ.get("AGENTPAVE_TVMAZE_MODE", "fixtures")
    if mode not in ("fixtures", "live"):
        raise ValueError(f"AGENTPAVE_TVMAZE_MODE must be 'fixtures' or 'live', not {mode!r}")
    return TVMazeClient(mode=mode)


def build_server(
    *,
    client: TVMazeClient | None = None,
    authorizer: Authorizer | None = None,
    principal: Callable[[], str] = principal_from_env,
) -> MCPServer:
    """Assemble the MCP server. Every dependency is injectable for the suite."""
    client = client or client_from_env()
    authorizer = authorizer or Authorizer()
    registry = authorizer.registry

    server = MCPServer(name=SERVER_NAME, version="1.0.0")

    def guard(tool_name: str) -> None:
        decision = authorizer.decide(principal(), tool_name)
        if decision.allowed:
            logger.info("%s", decision)
            return
        # M02 passes on "policy denied *and logged*", so the denial is logged
        # at warning with the reason before it is raised.
        logger.warning("%s", decision)
        raise ToolDenied(str(decision))

    @server.tool(name="search_show", description=registry.tool("search_show").description)
    def search_show(query: str) -> dict:
        guard("search_show")
        return tools.search_show(client, query=query)

    @server.tool(name="get_episodes", description=registry.tool("get_episodes").description)
    def get_episodes(show_id: int) -> dict:
        guard("get_episodes")
        return tools.get_episodes(client, show_id=show_id)

    @server.tool(name="get_schedule", description=registry.tool("get_schedule").description)
    def get_schedule(date: str, country: str = "US", limit: int | None = None) -> dict:
        guard("get_schedule")
        return tools.get_schedule(client, date=date, country=country, limit=limit)

    return server
