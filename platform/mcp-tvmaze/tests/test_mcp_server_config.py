"""How the server reads its environment.

Small surface, but the failure modes are the quiet kind: a mode that silently
falls back to live traffic, or an identity that defaults to something
privileged.
"""

import pytest
from agentpave_mcp_tvmaze.server import ANONYMOUS, client_from_env, principal_from_env


def test_mode_defaults_to_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    # A misconfigured deployment must replay recordings rather than silently
    # putting a rate-limited third party in the request path.
    monkeypatch.delenv("AGENTPAVE_TVMAZE_MODE", raising=False)
    assert client_from_env().mode == "fixtures"


def test_live_mode_has_to_be_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTPAVE_TVMAZE_MODE", "live")
    assert client_from_env().mode == "live"


def test_unknown_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Typo'd config should fail the container, not fall through to a default
    # that looks like it worked.
    monkeypatch.setenv("AGENTPAVE_TVMAZE_MODE", "fixture")
    with pytest.raises(ValueError, match="must be 'fixtures' or 'live'"):
        client_from_env()


def test_missing_identity_is_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    # An identity no policy grants anything to, so a missing identity is denied
    # by the ordinary policy path rather than by a special case.
    monkeypatch.delenv("AGENTPAVE_AGENT_ID", raising=False)
    assert principal_from_env() == ANONYMOUS


def test_empty_identity_is_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    # An unset variable and one set to empty must behave the same; treating ""
    # as a valid identity would authorize against a principal nobody named.
    monkeypatch.setenv("AGENTPAVE_AGENT_ID", "")
    assert principal_from_env() == ANONYMOUS


def test_anonymous_is_granted_nothing() -> None:
    from agentpave_registry.authz import Authorizer

    authorizer = Authorizer()
    assert not authorizer.decide(ANONYMOUS, "search_show").allowed
