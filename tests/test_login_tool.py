"""Regression tests for the login tool's flow caching.

The original bug: login() cached the OAuthFlow in _singletons BEFORE start()
bound the callback listener. A port conflict then left a corpse flow whose
_done was never set, so every later login() returned its authorization URL
with "a login is already in progress" — a dead end (nothing listening on the
redirect port) that persisted until the server process restarted.
"""

import socket
from collections.abc import Generator
from pathlib import Path
from typing import cast

import pytest

import linkedin_mcp.auth.oauth as oauth_module
from linkedin_mcp import server
from linkedin_mcp.auth.oauth import OAuthFlow, TokenStore
from linkedin_mcp.errors import LinkedInError
from test_oauth import make_settings


@pytest.fixture(autouse=True)
def no_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth_module.webbrowser, "open", lambda *_args, **_kwargs: True)


@pytest.fixture(autouse=True)
def clean_flow_singleton() -> Generator[None]:
    server._singletons.pop("flow", None)
    yield
    flow = server._singletons.pop("flow", None)
    if isinstance(flow, OAuthFlow):
        flow._done.set()
        if flow._thread:
            flow._thread.join(timeout=3)


@pytest.fixture
def creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "cid")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "secret")


@pytest.fixture
def blocked_port() -> Generator[int]:
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    try:
        yield blocker.getsockname()[1]
    finally:
        blocker.close()


def test_bind_failure_marks_flow_terminal(tmp_path: Path, blocked_port: int):
    flow = OAuthFlow(make_settings(redirect_port=blocked_port), TokenStore(path=tmp_path / "t.json"))
    with pytest.raises(LinkedInError, match="Could not listen"):
        flow.start(open_browser=False)
    assert flow._done.is_set(), "a flow that never started must not look in-progress"
    assert flow._error


def test_login_tool_not_poisoned_by_bind_failure(creds: None, monkeypatch: pytest.MonkeyPatch):
    assert creds is None
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = cast(int, blocker.getsockname()[1])
    monkeypatch.setenv("LINKEDIN_REDIRECT_PORT", str(port))

    try:
        with pytest.raises(LinkedInError, match="Could not listen"):
            server.login()
        # Regression: the second call must retry the bind and fail loudly again —
        # not hand out the dead flow's URL as "a login already in progress".
        with pytest.raises(LinkedInError, match="Could not listen"):
            server.login()
        assert "flow" not in server._singletons
    finally:
        blocker.close()

    # Port freed → the same server process recovers without a restart.
    started = server.login()
    assert started.authorization_url.startswith("https://www.linkedin.com/oauth")
    live = server._singletons["flow"]
    assert isinstance(live, OAuthFlow)
    assert live._server is not None
    assert not live._done.is_set()


def test_login_reuses_genuinely_pending_flow(creds: None, monkeypatch: pytest.MonkeyPatch):
    assert creds is None
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = cast(int, probe.getsockname()[1])
    probe.close()
    monkeypatch.setenv("LINKEDIN_REDIRECT_PORT", str(port))

    first = server.login()
    second = server.login()
    assert second.authorization_url == first.authorization_url
    assert "already in progress" in second.message


def test_auth_status_reports_saved_tokens_without_creds():
    import time

    # conftest guarantees LINKEDIN_CLIENT_ID/SECRET are absent from the env
    TokenStore().save(
        {
            "access_token": "tok",
            "expires_at": time.time() + 1000,
            "scope": "openid profile email w_member_social",
            "profile": {"name": "Amrit"},
        }
    )
    status = server.build_auth_status()
    assert status.configured is False
    assert status.authenticated is True
    assert "posting works" in status.detail
    assert status.profile_name == "Amrit"
    assert status.setup_instructions is None
