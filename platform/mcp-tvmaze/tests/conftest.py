"""Drivers — the seam that lets one contract suite run two ways.

The suite must assert identical things against fixtures in `make check` and
against the deployed Lambda in `make conformance`. The two transports do not
report failure the same way: an in-process `call_tool` raises `ToolError`,
while a real MCP client returns a result with `is_error` set. Normalising that
here is what makes "the same suite against the deployed target" literally true
instead of a claim about two suites that resemble each other.

What must *not* be normalised is the difference between "the tool said no" and
"the endpoint was never reached" — see `TransportError`.

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
from mcp.shared.exceptions import MCPError

DEPLOYED_URL_ENV = "AGENTPAVE_MCP_URL"

_SIGN = object()


class TransportError(RuntimeError):
    """The endpoint was not reached, or did not answer in MCP.

    Deliberately never converted into an `Outcome`. Folding transport failure
    into "the call returned an error" is what let the first conformance run
    report passes against an endpoint that answered 403 to everything: each
    test asserting a call *fails* was satisfied by the transport failing, and
    only the happy-path tests went red. A gate that green-lights an unreachable
    service is worse than no gate, so this leaves the suite by raising.
    """


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
            # No transport here, so every exception really is the tool's answer
            # — the ambiguity `TransportError` exists to prevent cannot arise.
            result = asyncio.run(self._server.call_tool(tool, arguments))
        except Exception as exc:  # noqa: BLE001 — the tool's error channel
            return Outcome(ok=False, error=str(exc))

        if result.is_error:
            return Outcome(ok=False, error=_text_of(result))
        return Outcome(ok=True, data=json.loads(_text_of(result)))


def sigv4_auth() -> Any:
    """Sign requests as the caller's AWS identity.

    The Function URL is `AWS_IAM` (an unauthenticated MCP endpoint is not worth
    the convenience), so an unsigned client gets 403 on every request — which
    is exactly what the first conformance run did. `make smoke-gateway` signs
    the same way for the same reason.
    """
    import boto3
    import httpx2
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    session = boto3.Session()
    signer = SigV4Auth(session.get_credentials(), "lambda", session.region_name)

    class _SigV4(httpx2.Auth):
        # botocore signs the payload hash, so the body has to be in hand before
        # the signature is computed.
        requires_request_body = True

        def auth_flow(self, request: Any) -> Any:
            signed = AWSRequest(
                method=request.method,
                url=str(request.url),
                data=request.content,
                headers={"content-type": request.headers.get("content-type", "")},
            )
            signer.add_auth(signed)
            for header in ("Authorization", "X-Amz-Date", "X-Amz-Security-Token"):
                if header in signed.headers:
                    request.headers[header] = signed.headers[header]
            yield request

    return _SigV4()


class DeployedDriver:
    """Drives a real MCP client over streamable HTTP against the Lambda."""

    name = "deployed"

    def __init__(self, url: str, auth: Any = _SIGN) -> None:
        self.url = url
        self.client = None  # the deployed server owns its own TVMaze client
        # `auth=None` means genuinely unsigned, which is a thing a test wants
        # to ask for — hence a sentinel rather than None as the default.
        self._auth = sigv4_auth() if auth is _SIGN else auth

    async def _session(self, work: Any) -> Any:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        async with (
            create_mcp_http_client(auth=self._auth) as http_client,
            # A 2-tuple in MCP 2.0; older releases yielded a third element.
            streamable_http_client(self.url, http_client=http_client) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await work(session)

    def _run(self, work: Any) -> Any:
        try:
            return asyncio.run(self._session(work))
        except Exception as exc:
            raise TransportError(f"{self.url}: {exc}") from exc

    def list_tools(self) -> dict[str, dict[str, Any]]:
        result = self._run(lambda s: s.list_tools())
        return {tool.name: tool.input_schema for tool in result.tools}

    def describe(self) -> dict[str, str]:
        result = self._run(lambda s: s.list_tools())
        return {tool.name: tool.description or "" for tool in result.tools}

    def call(self, tool: str, arguments: dict[str, Any]) -> Outcome:
        try:
            result = asyncio.run(self._session(lambda s: s.call_tool(tool, arguments)))
        except MCPError as exc:
            # The server answered, and the answer was "no". That is a result.
            return Outcome(ok=False, error=str(exc))
        except Exception as exc:
            # Anything else means we never got an answer at all.
            raise TransportError(f"{self.url}: {exc}") from exc

        # `result.is_error`, not `getattr(result, "isError", False)`: camelCase
        # is the wire alias, the attribute is snake_case, and the getattr
        # default quietly turned every error result into a success — which the
        # suite then tried to JSON-decode from an empty string. A missing
        # attribute should be a crash, not a False.
        if result.is_error:
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
