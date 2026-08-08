"""Fixture replay, and the method recording the consequence class depends on."""

import json
from pathlib import Path

import pytest
from agentpave_mcp_tvmaze.client import (
    CatalogNotFound,
    FixtureMissing,
    TVMazeClient,
    fixture_name,
)

SEVERANCE_ID = 44933


@pytest.fixture
def client() -> TVMazeClient:
    return TVMazeClient()


def test_default_mode_is_fixtures(client: TVMazeClient) -> None:
    # `make check` must not depend on a rate-limited third party. Defaulting to
    # live would make the hermetic gate quietly non-hermetic.
    assert client.mode == "fixtures"


def test_recorded_response_is_replayed(client: TVMazeClient) -> None:
    results = client.get("search/shows", {"q": "severance"})
    assert results[0]["show"]["name"] == "Severance"


def test_empty_result_is_data_not_an_error(client: TVMazeClient) -> None:
    # "Nothing matched" is a legitimate answer; raising would push callers into
    # treating an ordinary outcome as a failure.
    assert client.get("search/shows", {"q": "zzzz-no-such-show"}) == []


def test_recorded_404_raises_not_found(client: TVMazeClient) -> None:
    # The not-found path is part of the contract, so it has to be reproducible
    # without the network.
    with pytest.raises(CatalogNotFound):
        client.get("shows/99999999/episodes")


def test_missing_fixture_fails_loudly(client: TVMazeClient) -> None:
    # Never fall back to the network: that would make the gate pass on one
    # machine and fail on another, and put TVMaze in its critical path.
    with pytest.raises(FixtureMissing, match="record it with"):
        client.get("search/shows", {"q": "a-query-nobody-recorded"})


def test_every_request_records_its_method(client: TVMazeClient) -> None:
    # This is what lets the contract suite check `consequence: read` instead of
    # trusting the label.
    client.get("search/shows", {"q": "severance"})
    client.get(f"shows/{SEVERANCE_ID}/episodes")
    assert client.methods_used == ("GET", "GET")


def test_methods_are_recorded_even_when_the_call_fails(client: TVMazeClient) -> None:
    # A tool that tried to POST and got a 404 still tried to POST. Recording
    # only successful calls would let a side effect hide behind an error.
    with pytest.raises(CatalogNotFound):
        client.get("shows/99999999/episodes")
    assert client.methods_used == ("GET",)


def test_methods_used_is_immutable(client: TVMazeClient) -> None:
    client.get("search/shows", {"q": "severance"})
    assert isinstance(client.methods_used, tuple)


# ── fixture naming ────────────────────────────────────────────────────────


def test_fixture_name_is_stable_across_param_order() -> None:
    # Otherwise the same request maps to two files depending on dict order,
    # and a recorded fixture silently stops being found.
    assert fixture_name("schedule", {"country": "US", "date": "2026-08-07"}) == fixture_name(
        "schedule", {"date": "2026-08-07", "country": "US"}
    )


def test_fixture_name_is_readable() -> None:
    # A reviewer should be able to tell which call a fixture belongs to
    # without opening it.
    assert fixture_name("search/shows", {"q": "severance"}) == "search_shows__q-severance.json"


def test_fixture_name_is_filesystem_safe() -> None:
    name = fixture_name("search/shows", {"q": "who/what?*<>|"})
    assert not set(name) & set('/\\?*<>|:"')


def test_client_accepts_an_explicit_fixture_dir(tmp_path: Path) -> None:
    (tmp_path / fixture_name("shows/1/episodes", None)).write_text(
        json.dumps({"status": 200, "body": [{"id": 1}]}), encoding="utf-8"
    )
    client = TVMazeClient(fixture_dir=tmp_path)
    assert client.get("shows/1/episodes") == [{"id": 1}]
