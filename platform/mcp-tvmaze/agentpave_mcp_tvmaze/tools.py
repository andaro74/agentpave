"""The three catalogue tools, as plain functions.

Plain on purpose: MCP registration, Cedar authorization, and transport all wrap
these from the outside, so the same function body serves the stdio server, the
Lambda, and the contract suite. Nothing here knows it is a tool.

Each returns a dict matching the `output_schema` declared in the registry —
that correspondence is asserted by the contract suite rather than trusted.
"""

import re
from typing import Any

from .client import TVMazeClient

_HTML_TAG = re.compile(r"<[^>]+>")


def _plain_text(html: str | None) -> str | None:
    """TVMaze summaries are HTML fragments; models read prose better."""
    if not html:
        return None
    return _HTML_TAG.sub("", html).strip() or None


def _channel_of(show: dict[str, Any]) -> str | None:
    """Who airs it.

    TVMaze splits broadcasters into `network` (over-the-air) and `webChannel`
    (streaming), and a streaming-only show has a null `network`. Returning that
    null would answer "what network airs Severance?" with nothing, which is
    wrong rather than merely unhelpful — Apple TV+ is the answer, it is just
    filed under a different key.
    """
    for key in ("network", "webChannel"):
        channel = show.get(key)
        if channel and channel.get("name"):
            return str(channel["name"])
    return None


def search_show(client: TVMazeClient, *, query: str) -> dict[str, Any]:
    results = client.get("search/shows", {"q": query})
    return {
        "shows": [
            {
                "id": show["id"],
                "name": show["name"],
                "network": _channel_of(show),
                "status": show.get("status") or "Unknown",
                "genres": show.get("genres") or [],
                "premiered": show.get("premiered"),
                "summary": _plain_text(show.get("summary")),
            }
            for show in (hit["show"] for hit in results)
        ]
    }


def get_episodes(client: TVMazeClient, *, show_id: int) -> dict[str, Any]:
    episodes = client.get(f"shows/{show_id}/episodes")
    return {
        "episodes": [
            {
                "id": episode["id"],
                "name": episode["name"],
                "season": episode["season"],
                "number": episode.get("number"),
                "airdate": episode.get("airdate") or None,
                "runtime": episode.get("runtime"),
            }
            for episode in episodes
        ]
    }


def get_schedule(
    client: TVMazeClient, *, date: str, country: str = "US", limit: int | None = None
) -> dict[str, Any]:
    """One day's listings, optionally capped.

    `limit` exists because a whole day of US listings is 438k of JSON, and the
    eval harness already carries a 40k character cap to stop it costing $0.11 a
    case (`SOURCE_CHAR_CAP`). That cap is a blunt instrument — it truncates
    mid-array, leaving JSON the model reads as text because it can no longer be
    parsed — and the note above it says the right fix is a tool that returns
    less rather than a caller that throws most of it away. This is that fix.

    Defaults to `None`, so every existing caller gets exactly what it got
    before. Trimming is the caller's decision to make, not a default this tool
    imposes on the two evals whose questions are about the whole day.
    """
    entries = client.get("schedule", {"country": country, "date": date})
    if limit is not None:
        entries = entries[:limit]
    return {
        "entries": [
            {
                "show_name": entry["show"]["name"],
                "episode_name": entry["name"],
                "network": _channel_of(entry["show"]),
                "airtime": entry.get("airtime") or None,
            }
            for entry in entries
        ]
    }


# Name -> callable, so the server and the contract suite agree on the tool set
# without either hard-coding it twice.
TOOLS = {
    "search_show": search_show,
    "get_episodes": get_episodes,
    "get_schedule": get_schedule,
}
