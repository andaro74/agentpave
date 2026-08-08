"""Refresh the recorded TVMaze fixtures.

A developer tool, never part of a gate — it needs the network, and the whole
point of the fixtures is that the gate does not. Run it when TVMaze's shape
changes or a new call is added:

    uv run python -m agentpave_mcp_tvmaze.record

Fixtures record the *status* alongside the body so the not-found path is
replayable. A 404 is part of the contract — the error-shape assertions in the
contract suite depend on being able to reproduce one without the network.
"""

import json
from pathlib import Path
from typing import Any

from .client import DEFAULT_FIXTURE_DIR, CatalogNotFound, TVMazeClient, fixture_name

# TVMaze's id for Severance, taken from the recorded search rather than
# guessed. The episodes fixture has to be for the id that search actually
# returns, or a search-then-episodes flow asks for a fixture that isn't there.
SEVERANCE_ID = 44933

# The recorded calls. `expect_missing` marks the ones whose value is that they
# fail — without them the suite can only test the happy path.
RECORDINGS: list[dict[str, Any]] = [
    {"path": "search/shows", "params": {"q": "severance"}},
    {"path": "search/shows", "params": {"q": "zzzz-no-such-show"}},
    {"path": f"shows/{SEVERANCE_ID}/episodes", "params": None},
    {"path": "shows/99999999/episodes", "params": None, "expect_missing": True},
    {"path": "schedule", "params": {"country": "US", "date": "2026-08-07"}},
]


def record_all(fixture_dir: Path | None = None) -> list[Path]:
    target = fixture_dir or DEFAULT_FIXTURE_DIR
    target.mkdir(parents=True, exist_ok=True)
    client = TVMazeClient(mode="live")

    written: list[Path] = []
    for recording in RECORDINGS:
        path, params = recording["path"], recording["params"]
        destination = target / fixture_name(path, params)

        try:
            payload = {"status": 200, "body": client.get(path, params)}
        except CatalogNotFound:
            if not recording.get("expect_missing"):
                raise
            payload = {"status": 404, "body": None}

        destination.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        written.append(destination)
        print(f"recorded {destination.name}")

    return written


if __name__ == "__main__":
    record_all()
