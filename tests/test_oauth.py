import os
import time
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlparse

import pytest

from linkedin_mcp.auth import oauth
from linkedin_mcp.auth.oauth import OAuthFlow, TokenStore
from linkedin_mcp.config import Settings
from linkedin_mcp.errors import NotAuthenticatedError


def make_settings(**overrides: str | int | bool | None) -> Settings:
    values: dict[str, str | int | bool | None] = {
        "client_id": "cid",
        "client_secret": "secret",
        "redirect_port": 8765,
        "api_version": "202606",
        "posts_backend": "auto",
        "redirect_bind_host": "127.0.0.1",
        "redirect_uri_override": None,
        "open_browser": False,
    }
    values.update(overrides)
    return Settings(
        client_id=cast(str | None, values["client_id"]),
        client_secret=cast(str | None, values["client_secret"]),
        redirect_port=cast(int, values["redirect_port"]),
        api_version=cast(str, values["api_version"]),
        posts_backend=cast(str, values["posts_backend"]),
        redirect_bind_host=cast(str, values["redirect_bind_host"]),
        redirect_uri_override=cast(str | None, values["redirect_uri_override"]),
        open_browser=cast(bool, values["open_browser"]),
    )


class FakeTokenResponse:
    def __init__(self, payload: object, status_code: int = 200, text: str = "") -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> object:
        return self.payload


def test_redirect_uri_can_be_overridden_for_http_deployments():
    settings = make_settings(redirect_uri_override="https://linkedin.example.test/callback")

    assert settings.redirect_uri == "https://linkedin.example.test/callback"


def test_token_store_roundtrip(tmp_path: Path):
    store = TokenStore(path=tmp_path / "tokens.json")
    store.save({"access_token": "tok", "expires_at": time.time() + 1000})
    assert store.access_token() == "tok"
    assert (tmp_path / "tokens.json").stat().st_mode & 0o777 == 0o600


def test_token_store_expired(tmp_path: Path):
    store = TokenStore(path=tmp_path / "tokens.json")
    store.save({"access_token": "tok", "expires_at": time.time() - 1})
    assert store.access_token() is None


def test_token_request_builds_access_and_refresh_tokens(monkeypatch: pytest.MonkeyPatch):
    def fake_post(_url: str, *, data: dict[str, str], timeout: float) -> FakeTokenResponse:
        assert data["grant_type"] == "authorization_code"
        assert timeout == 30.0
        return FakeTokenResponse(
            {
                "access_token": "fresh",
                "expires_in": 3600,
                "scope": "openid profile",
                "refresh_token": "refresh",
                "refresh_token_expires_in": 7200,
            }
        )

    monkeypatch.setattr(oauth.httpx, "post", fake_post)
    tokens = oauth._token_request({"grant_type": "authorization_code"})

    assert tokens["access_token"] == "fresh"
    assert tokens["scope"] == "openid profile"
    assert isinstance(tokens["expires_at"], float)
    assert isinstance(tokens["refresh_expires_at"], float)
    assert tokens["refresh_token"] == "refresh"


def test_token_request_rejects_http_and_payload_errors(monkeypatch: pytest.MonkeyPatch):
    def fake_post(_url: str, *, data: dict[str, str], timeout: float) -> FakeTokenResponse:
        del data, timeout
        return FakeTokenResponse({}, status_code=503, text="temporarily unavailable")

    monkeypatch.setattr(oauth.httpx, "post", fake_post)
    with pytest.raises(Exception, match="503"):
        oauth._token_request({"grant_type": "authorization_code"})

    responses = [FakeTokenResponse([]), FakeTokenResponse({}), FakeTokenResponse({"access_token": 42})]
    for response in responses:

        def fake_payload_post(
            _url: str,
            *,
            data: dict[str, str],
            timeout: float,
            response: FakeTokenResponse = response,
        ) -> FakeTokenResponse:
            del data, timeout
            return response

        monkeypatch.setattr(
            oauth.httpx,
            "post",
            fake_payload_post,
        )
        with pytest.raises(Exception, match="token endpoint"):
            oauth._token_request({"grant_type": "authorization_code"})


def test_token_request_defaults_invalid_expiry_and_omits_empty_refresh(monkeypatch: pytest.MonkeyPatch):
    def fake_post(_url: str, *, data: dict[str, str], timeout: float) -> FakeTokenResponse:
        del data, timeout
        return FakeTokenResponse(
            {
                "access_token": "fresh",
                "expires_in": "not-a-number",
                "refresh_token": "",
                "refresh_token_expires_in": "not-a-number",
            }
        )

    monkeypatch.setattr(oauth.httpx, "post", fake_post)
    tokens = oauth._token_request({"grant_type": "authorization_code"})

    assert tokens["access_token"] == "fresh"
    assert "refresh_token" not in tokens
    assert "refresh_expires_at" not in tokens


def test_unconfigured_flow_raises_with_instructions(tmp_path: Path):
    with pytest.raises(NotAuthenticatedError, match="developers/apps"):
        OAuthFlow(make_settings(client_id=None), TokenStore(path=tmp_path / "t.json"))


@pytest.mark.parametrize(
    "saved_tokens",
    [{}, {"refresh_token": "refresh"}, {"refresh_token": "refresh", "refresh_expires_at": time.time() - 1}],
)
def test_maybe_refresh_skips_missing_or_expired_refresh_tokens(tmp_path: Path, saved_tokens: dict[str, object]):
    store = TokenStore(path=tmp_path / "t.json")
    store.save(saved_tokens)

    assert oauth.maybe_refresh(make_settings(), store) is None


def test_maybe_refresh_saves_new_tokens_and_preserves_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = TokenStore(path=tmp_path / "t.json")
    store.save(
        {
            "refresh_token": "refresh",
            "refresh_expires_at": time.time() + 1000,
            "profile": {"name": "Amrit"},
        }
    )
    captured: dict[str, str] = {}

    def fake_request(form: dict[str, str]) -> dict[str, object]:
        captured.update(form)
        return {"access_token": "new", "expires_at": time.time() + 1000}

    monkeypatch.setattr(oauth, "_token_request", fake_request)
    assert oauth.maybe_refresh(make_settings(), store) == "new"
    assert captured == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh",
        "client_id": "cid",
        "client_secret": "secret",
    }
    saved = store.load()
    assert saved is not None
    assert saved["access_token"] == "new"
    assert saved["profile"] == {"name": "Amrit"}


def test_maybe_refresh_returns_none_when_linkedin_rejects_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = TokenStore(path=tmp_path / "t.json")
    store.save({"refresh_token": "refresh", "refresh_expires_at": time.time() + 1000})

    def reject(_form: dict[str, str]) -> dict[str, object]:
        raise oauth.LinkedInError("refresh rejected")

    monkeypatch.setattr(oauth, "_token_request", reject)
    assert oauth.maybe_refresh(make_settings(), store) is None


def test_authorization_url_contents(tmp_path: Path):
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    parsed = urlparse(flow.authorization_url())
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert parsed.netloc == "www.linkedin.com"
    assert query["client_id"] == "cid"
    # 127.0.0.1, never "localhost" — browsers may resolve the name to ::1, which
    # another local account can bind to intercept the authorization code.
    assert query["redirect_uri"] == "http://127.0.0.1:8765/callback"
    assert query["scope"] == "openid profile email w_member_social"
    assert len(query["state"]) > 20


def test_callback_rejects_state_mismatch(tmp_path: Path):
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    status, _html = flow._handle_callback({"code": "c", "state": "WRONG"})
    assert status == 400
    assert not flow._done.is_set()


def test_callback_success_saves_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = TokenStore(path=tmp_path / "t.json")
    flow = OAuthFlow(make_settings(), store)
    monkeypatch.setattr(
        oauth,
        "exchange_code",
        lambda _settings, _code: {"access_token": "fresh", "expires_at": time.time() + 100},
    )
    status, html = flow._handle_callback({"code": "c", "state": flow._state})
    assert status == 200
    assert "connected" in html.lower()
    assert flow._done.is_set()
    assert store.access_token() == "fresh"


def test_callback_reports_linkedin_error(tmp_path: Path):
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    status, html = flow._handle_callback(
        {
            "state": flow._state,
            "error": "user_cancelled_authorize",
            "error_description": "The user cancelled",
        }
    )
    assert status == 200
    assert "cancelled" in html
    with pytest.raises(Exception, match="cancelled"):
        flow.wait(timeout=1)


def test_callback_explains_missing_product(tmp_path: Path):
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    status, html = flow._handle_callback(
        {
            "state": flow._state,
            "error": "unauthorized_scope_error",
            "error_description": "Scope &quot;openid&quot; is not authorized for your application",
        }
    )
    assert status == 200
    assert flow._done.is_set()
    assert flow._error is not None
    assert 'Scope "openid"' in flow._error  # un-escaped for humans
    assert "Sign In with LinkedIn using OpenID Connect" in flow._error
    assert "Products tab" in html


# --------------------------------------------------------------- security regressions


def test_callback_ignores_error_without_valid_state(tmp_path: Path):
    """An unauthenticated GET must not be able to abort a pending login.

    Any local process — and any web page the user has open, via a bare
    <img src="http://127.0.0.1:8765/callback?error=x"> — can reach this
    listener. Handling `error` before checking `state` let such a request kill
    the flow, which also released the port for an authorization-code
    interceptor.
    """
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    status, _html = flow._handle_callback({"error": "access_denied", "error_description": "drive-by abort"})
    assert status == 400
    assert not flow._done.is_set()  # the login survives
    assert flow._error is None


def test_callback_without_state_cannot_reflect_html(tmp_path: Path):
    """The stateless drive-by path must not echo attacker markup at all."""
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    payload = "<script>alert(document.domain)</script>"
    _status, html = flow._handle_callback({"error": "x", "error_description": payload})
    assert payload not in html
    assert "alert(" not in html


def test_error_page_escapes_attacker_markup(tmp_path: Path):
    """Even with a valid state, error text is escaped at the sink.

    _explain_authorize_error() calls html.unescape() to make LinkedIn's messages
    readable, so an entity-encoded payload is decoded back into live markup
    before rendering — the escape has to happen on output.
    """
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    _status, html = flow._handle_callback(
        {
            "state": flow._state,
            "error": "x",
            "error_description": "&lt;script&gt;alert(1)&lt;/script&gt;<img src=x onerror=1>",
        }
    )
    assert "<script>" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;" in html  # rendered as visible text instead


def test_state_mismatch_still_does_not_abort(tmp_path: Path):
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    status, _html = flow._handle_callback({"code": "c", "state": "WRONG"})
    assert status == 400
    assert not flow._done.is_set()


def test_token_file_is_never_world_readable_even_mid_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The 0600 must hold from creation, not be applied afterwards.

    write_text()+chmod() published the whole access token at 0644 first; a local
    watcher could win that race. Snapshot the mode from inside the write.
    """
    path = tmp_path / "tokens.json"
    store = TokenStore(path=path)
    observed: list[int] = []
    real_write = os.write

    def spy(fd: int, data: bytes) -> int:
        observed.append(os.fstat(fd).st_mode & 0o777)
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", spy)
    store.save({"access_token": "tok", "expires_at": time.time() + 1000})

    assert observed == [0o600]  # already private while the bytes land
    assert path.stat().st_mode & 0o777 == 0o600


def test_token_save_refuses_to_write_through_a_symlink(tmp_path: Path):
    """A pre-planted symlink must not redirect the token write (CWE-59)."""
    victim = tmp_path / "victim.txt"
    victim.write_text("do not clobber")
    link = tmp_path / "tokens.json"
    link.symlink_to(victim)

    with pytest.raises(OSError):
        TokenStore(path=link).save({"access_token": "tok"})
    assert victim.read_text() == "do not clobber"


def test_existing_loose_token_file_is_tightened_on_read(tmp_path: Path):
    """Upgrading from an older version must not leave a 0644 token behind."""
    path = tmp_path / "tokens.json"
    path.write_text('{"access_token": "legacy"}')
    path.chmod(0o644)

    assert TokenStore(path=path).load() == {"access_token": "legacy"}
    assert path.stat().st_mode & 0o777 == 0o600


def test_data_dir_is_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from linkedin_mcp.config import data_dir

    target = tmp_path / "loose"
    target.mkdir(mode=0o755)
    monkeypatch.setenv("LINKEDIN_MCP_DIR", str(target))

    assert data_dir().stat().st_mode & 0o077 == 0  # no group/other access


@pytest.mark.parametrize("bad_state", ["caf\u00e9", "\U0001f600", "", None])
def test_non_ascii_state_is_rejected_not_crashed(tmp_path: Path, bad_state: str | None):
    """secrets.compare_digest() raises TypeError on a non-ASCII str.

    Any drive-by request can supply one, and an exception escaping the handler
    means no HTTP status and a traceback per request. Compare as bytes.
    """
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    status, _html = flow._handle_callback({"state": bad_state, "error": "x"})
    assert status == 400
    assert not flow._done.is_set()
