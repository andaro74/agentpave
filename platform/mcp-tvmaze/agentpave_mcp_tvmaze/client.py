"""TVMaze access, in two modes.

`fixtures` replays recorded responses and is the default everywhere, including
the deployed Lambda. That is a deliberate choice: `make conformance` asserts on
specific shows and episode counts, and running it against live TVMaze would
make the deployed gate flaky for reasons that have nothing to do with the
platform — a show's status changes, a schedule shifts, and a green gate goes
red overnight. Live mode exists, is exercised by `record.py`, and is opt-in.

Every request records its HTTP method whether or not it leaves the process.
That is what lets the contract suite check the registry's `consequence: read`
claim instead of trusting the label.
"""

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Literal

BASE_URL = "https://api.tvmaze.com"
DEFAULT_FIXTURE_DIR = Path(__file__).parent / "fixtures"

Mode = Literal["fixtures", "live"]


class CatalogError(Exception):
    """Base for errors the tools translate into structured MCP failures."""


class CatalogNotFound(CatalogError):
    """The catalogue has nothing under that id or query."""


class FixtureMissing(CatalogError):
    """No recorded response for this request.

    Raised loudly rather than falling back to the network: a silent fallback
    would make `make check` pass on one machine and fail on another, and would
    quietly put a rate-limited third party in the hermetic gate's path.
    """


def fixture_name(path: str, params: dict[str, Any] | None) -> str:
    """A stable, readable filename for one request.

    Readable on purpose — someone reviewing a diff should be able to tell which
    call a fixture belongs to without opening it.
    """
    parts = [path.strip("/").replace("/", "_")]
    for key in sorted(params or {}):
        parts.append(f"{key}-{params[key]}")
    slug = "__".join(str(p) for p in parts).lower()
    return re.sub(r"[^a-z0-9_.-]+", "-", slug) + ".json"


class TVMazeClient:
    """Reads the TV catalogue. Never writes — see `methods_used`."""

    def __init__(
        self,
        *,
        mode: Mode = "fixtures",
        fixture_dir: Path | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.mode = mode
        self.fixture_dir = fixture_dir or DEFAULT_FIXTURE_DIR
        self.timeout = timeout
        self._methods_used: list[str] = []

    @property
    def methods_used(self) -> tuple[str, ...]:
        """Every HTTP method this client has issued, in order.

        The contract suite asserts this contains only GET for a `read` tool,
        which turns the consequence class from a label into a checked property.
        """
        return tuple(self._methods_used)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self._methods_used.append("GET")
        if self.mode == "live":
            return self._get_live(path, params)
        return self._get_fixture(path, params)

    # ── modes ─────────────────────────────────────────────────────────────

    def _get_fixture(self, path: str, params: dict[str, Any] | None) -> Any:
        source = self.fixture_dir / fixture_name(path, params)
        if not source.exists():
            raise FixtureMissing(
                f"no fixture {source.name!r} — record it with "
                f"`uv run python -m agentpave_mcp_tvmaze.record`"
            )
        payload = json.loads(source.read_text(encoding="utf-8"))
        if payload.get("status") == 404:
            raise CatalogNotFound(f"{path} not found in the catalogue")
        return payload["body"]

    def _get_live(self, path: str, params: dict[str, Any] | None) -> Any:
        url = f"{BASE_URL}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        request = urllib.request.Request(  # noqa: S310 — scheme is fixed above
            url,
            method="GET",
            headers={"user-agent": "agentpave-mcp-tvmaze"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise CatalogNotFound(f"{path} not found in the catalogue") from exc
            raise CatalogError(f"TVMaze returned HTTP {exc.code} for {path}") from exc
