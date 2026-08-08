"""Drivers — the seam that lets one contract suite run two ways.

The suite must assert identical things against fixtures in `make check` and
against the deployed Lambda in `make conformance`. The two transports do not
report failure the same way: an in-process `call_tool` raises `ToolError`,
while a real MCP client returns a result with `is_error` set. Normalising that
here is what makes "the same suite against the deployed target" literally true
instead of a claim about two suites that resemble each other.

The deployed driver appears only when `AGENTPAVE_MCP_URL` is set, so the
hermetic gate never reaches for the network.
"""

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import pytest
from agentpave_mcp_tvmaze.client import TVMazeClient
from agentpave_mcp_tvmaze.server import build_server

DEPLOYED_URL_ENV = "AGENTPAVE_MCP_URL"


@dataclass(frozen=True)
class Outcome:
    """One tool call's result, in a shape both transports can produce."""

    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None


class InProcessDriver:
    """Dispatches through MCPServer directly, over recorded fixtures."""

    name = "in-process"

    def __init__(self, principal: str = "catalog-agent") -> None:
        self.client = TVMazeClient()
        self._server = build_server(client=self.client, principal=lambda: principal)

    def list_tools(self) -> dict[str, dict[str, Any]]:
        tools = asyncio.run(self._server.list_tools())
        return {tool.name: tool.input_schema for tool in tools}

    def describe(self) -> dict[str, str]:
        tools = asyncio.run(self._server.list_tools())
        return {tool.name: tool.description or "" for tool in tools}

    def call(self, tool: str, arguments: dict[str, Any]) -> Outcome:
        try:
            result = asyncio.run(self._server.call_tool(tool, arguments))
        except Exception as exc:  # noqa: BLE001 — the transport's error channel
            return Outcome(ok=False, error=str(exc))

        if getattr(result, "is_error", False):
            return Outcome(ok=False, error=_text_of(result))
        return Outcome(ok=True, data=json.loads(_text_of(result)))


class DeployedDriver:
    """Drives a real MCP client over streamable HTTP against the Lambda."""

    name = "deployed"

    def __init__(self, url: str) -> None:
        self.url = url
        self.client = None  # the deployed server owns its own TVMaze client

    async def _session(self, work: Any) -> Any:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with (
            streamable_http_client(self.url) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await work(session)

    def list_tools(self) -> dict[str, dict[str, Any]]:
        result = asyncio.run(self._session(lambda s: s.list_tools()))
        return {tool.name: tool.inputSchema for tool in result.tools}

    def describe(self) -> dict[str, str]:
        result = asyncio.run(self._session(lambda s: s.list_tools()))
        return {tool.name: tool.description or "" for tool in result.tools}

    def call(self, tool: str, arguments: dict[str, Any]) -> Outcome:
        try:
            result = asyncio.run(self._session(lambda s: s.call_tool(tool, arguments)))
        except Exception as exc:  # noqa: BLE001
            return Outcome(ok=False, error=str(exc))

        if getattr(result, "isError", False):
            return Outcome(ok=False, error=_text_of(result))
        return Outcome(ok=True, data=json.loads(_text_of(result)))


def _text_of(result: Any) -> str:
    blocks = getattr(result, "content", []) or []
    return "".join(getattr(block, "text", "") for block in blocks)


def _available_drivers() -> list[str]:
    drivers = ["in-process"]
    if os.environ.get(DEPLOYED_URL_ENV):
        drivers.append("deployed")
    return drivers


@pytest.fixture(params=_available_drivers())
def driver(request: pytest.FixtureRequest) -> InProcessDriver | DeployedDriver:
    if request.param == "deployed":
        return DeployedDriver(os.environ[DEPLOYED_URL_ENV])
    return InProcessDriver()


@pytest.fixture
def rogue_driver() -> InProcessDriver:
    """An identity no policy grants anything to.

    In-process only: assuming a different identity against the deployed
    endpoint would mean forging an IAM principal, which is the one thing the
    deployment is supposed to make impossible. ROADMAP puts wrong-identity deny
    in the hermetic gate for the same reason.
    """
    return InProcessDriver(principal="rogue-agent")
