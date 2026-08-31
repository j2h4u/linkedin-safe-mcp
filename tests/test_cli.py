from __future__ import annotations

import json
from typing import cast

import pytest
from mcp.server.transport_security import TransportSecuritySettings

from linkedin_mcp import cli, server
from linkedin_mcp.auth import oauth


def test_main_serve_uses_streamable_http_and_allowlists(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LINKEDIN_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("LINKEDIN_MCP_PORT", "9123")
    monkeypatch.setenv("LINKEDIN_MCP_ALLOWED_HOSTS", "example.test:9123,127.0.0.1:*")
    monkeypatch.setenv("LINKEDIN_MCP_ALLOWED_ORIGINS", "https://example.test,http://127.0.0.1:*")
    calls: list[dict[str, object]] = []

    def fake_run(*args: object, **kwargs: object) -> None:
        assert args == ()
        calls.append(kwargs)

    monkeypatch.setattr(server.mcp, "run", fake_run)
    cli.main([])
    cli.main(["serve", "--host", "127.0.0.1", "--port", "8123"])

    assert len(calls) == 2
    assert calls[0]["transport"] == "streamable-http"
    assert calls[0]["host"] == "0.0.0.0"
    assert calls[0]["port"] == 9123
    assert calls[0]["streamable_http_path"] == "/mcp"
    assert calls[0]["stateless_http"] is True
    assert calls[0]["json_response"] is True
    security = cast(TransportSecuritySettings, calls[0]["transport_security"])
    assert security.allowed_hosts == ["example.test:9123", "127.0.0.1:*"]
    assert security.allowed_origins == ["https://example.test", "http://127.0.0.1:*"]
    assert calls[1]["host"] == "127.0.0.1"
    assert calls[1]["port"] == 8123


def test_main_auth_status_and_logout_commands(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    auth_calls: list[bool] = []

    def fake_auth(*, open_browser: bool) -> None:
        auth_calls.append(open_browser)

    monkeypatch.setattr(cli, "_run_auth", fake_auth)
    cli.main(["auth", "--no-browser"])
    cli.main(["auth"])
    assert auth_calls == [False, True]

    class FakeStatus:
        def model_dump(self, *, exclude_none: bool) -> dict[str, object]:
            assert exclude_none is True
            return {"configured": False, "authenticated": False}

    def fake_status() -> FakeStatus:
        return FakeStatus()

    monkeypatch.setattr(server, "build_auth_status", fake_status)
    cli.main(["status"])
    assert json.loads(capsys.readouterr().out) == {"configured": False, "authenticated": False}

    cleared: list[bool] = []

    def fake_clear(_store: oauth.TokenStore) -> None:
        cleared.append(True)

    monkeypatch.setattr(oauth.TokenStore, "clear", fake_clear)
    cli.main(["logout"])
    assert cleared == [True]
    assert "deleted" in capsys.readouterr().out
