"""The three catalogue tools, over recorded fixtures."""

import pytest
from agentpave_mcp_tvmaze import tools
from agentpave_mcp_tvmaze.client import CatalogNotFound, TVMazeClient

SEVERANCE_ID = 44933
RECORDED_DATE = "2026-08-07"


@pytest.fixture
def client() -> TVMazeClient:
    return TVMazeClient()


# ── search_show ───────────────────────────────────────────────────────────


def test_search_returns_the_show(client: TVMazeClient) -> None:
    show = tools.search_show(client, query="severance")["shows"][0]
    assert show["id"] == SEVERANCE_ID
    assert show["name"] == "Severance"


def test_streaming_show_reports_its_web_channel_as_the_network(client: TVMazeClient) -> None:
    # TVMaze files streaming services under `webChannel`, leaving `network`
    # null. Returning that null would answer "what network airs Severance?"
    # with nothing — wrong, not merely unhelpful.
    show = tools.search_show(client, query="severance")["shows"][0]
    assert show["network"] == "Apple TV"


def test_summary_is_plain_text_not_html(client: TVMazeClient) -> None:
    # Models read prose better than markup, and the schema says string.
    summary = tools.search_show(client, query="severance")["shows"][0]["summary"]
    assert summary
    assert "<" not in summary and ">" not in summary


def test_search_with_no_matches_returns_an_empty_list(client: TVMazeClient) -> None:
    assert tools.search_show(client, query="zzzz-no-such-show") == {"shows": []}


def test_search_declares_every_required_field(client: TVMazeClient) -> None:
    show = tools.search_show(client, query="severance")["shows"][0]
    assert {"id", "name", "status"} <= show.keys()


def test_status_is_never_null(client: TVMazeClient) -> None:
    # The output schema requires `status`; TVMaze can omit it, so the tool
    # substitutes rather than emitting a contract violation.
    assert tools.search_show(client, query="severance")["shows"][0]["status"]


# ── get_episodes ──────────────────────────────────────────────────────────


def test_episodes_are_returned_for_the_id_search_gives(client: TVMazeClient) -> None:
    # The search-then-episodes flow is the one an agent actually performs, so
    # the fixtures have to be linked by the id search really returns.
    show_id = tools.search_show(client, query="severance")["shows"][0]["id"]
    episodes = tools.get_episodes(client, show_id=show_id)["episodes"]
    assert episodes
    assert episodes[0]["season"] == 1


def test_episode_carries_its_season_and_number(client: TVMazeClient) -> None:
    episode = tools.get_episodes(client, show_id=SEVERANCE_ID)["episodes"][0]
    assert {"id", "name", "season", "number"} <= episode.keys()


def test_unknown_show_raises_not_found(client: TVMazeClient) -> None:
    with pytest.raises(CatalogNotFound):
        tools.get_episodes(client, show_id=99999999)


# ── get_schedule ──────────────────────────────────────────────────────────


def test_schedule_maps_show_and_episode_names(client: TVMazeClient) -> None:
    entry = tools.get_schedule(client, date=RECORDED_DATE)["entries"][0]
    assert entry["show_name"]
    assert entry["episode_name"]


def test_schedule_defaults_to_us(client: TVMazeClient) -> None:
    # The default has to match the recorded fixture, or the default path is
    # the one nothing tests.
    assert tools.get_schedule(client, date=RECORDED_DATE)["entries"]


def test_schedule_entries_declare_required_fields(client: TVMazeClient) -> None:
    for entry in tools.get_schedule(client, date=RECORDED_DATE)["entries"]:
        assert {"show_name", "episode_name"} <= entry.keys()


def test_schedule_limit_caps_the_entries(client: TVMazeClient) -> None:
    assert len(tools.get_schedule(client, date=RECORDED_DATE, limit=5)["entries"]) == 5


def test_schedule_limit_defaults_to_the_whole_day(client: TVMazeClient) -> None:
    """The default must stay uncapped.

    Two golden cases ask what a whole day's schedule looks like. A tool that
    quietly trimmed by default would answer them from a prefix and still look
    grounded — the shape of failure this repository keeps finding, where the
    assertion and the data agree because both were narrowed.
    """
    whole_day = tools.get_schedule(client, date=RECORDED_DATE)["entries"]
    assert len(whole_day) > 5


def test_schedule_limit_above_the_available_entries_is_not_an_error(
    client: TVMazeClient,
) -> None:
    whole_day = tools.get_schedule(client, date=RECORDED_DATE)["entries"]
    assert tools.get_schedule(client, date=RECORDED_DATE, limit=10_000)["entries"] == whole_day


# ── the tool set ──────────────────────────────────────────────────────────


def test_every_tool_is_reachable_by_name() -> None:
    # The server and the contract suite both dispatch through TOOLS, so this
    # mapping is the single place the tool set is defined.
    assert set(tools.TOOLS) == {"search_show", "get_episodes", "get_schedule"}


def test_reads_only_issue_get(client: TVMazeClient) -> None:
    # The registry claims consequence: read for all three. This is that claim,
    # checked.
    tools.search_show(client, query="severance")
    tools.get_episodes(client, show_id=SEVERANCE_ID)
    tools.get_schedule(client, date=RECORDED_DATE)
    assert set(client.methods_used) == {"GET"}
