import os
import time
from urllib.parse import parse_qs, urlparse

import pytest

from linkedin_mcp.auth import oauth
from linkedin_mcp.auth.oauth import OAuthFlow, TokenStore
from linkedin_mcp.config import Settings
from linkedin_mcp.errors import NotAuthenticatedError


def make_settings(**overrides) -> Settings:
    values = dict(
        client_id="cid",
        client_secret="secret",
        redirect_port=8765,
        api_version="202606",
        posts_backend="auto",
    )
    values.update(overrides)
    return Settings(**values)


def test_token_store_roundtrip(tmp_path):
    store = TokenStore(path=tmp_path / "tokens.json")
    store.save({"access_token": "tok", "expires_at": time.time() + 1000})
    assert store.access_token() == "tok"
    assert (tmp_path / "tokens.json").stat().st_mode & 0o777 == 0o600


def test_token_store_expired(tmp_path):
    store = TokenStore(path=tmp_path / "tokens.json")
    store.save({"access_token": "tok", "expires_at": time.time() - 1})
    assert store.access_token() is None


def test_unconfigured_flow_raises_with_instructions(tmp_path):
    with pytest.raises(NotAuthenticatedError, match="developers/apps"):
        OAuthFlow(make_settings(client_id=None), TokenStore(path=tmp_path / "t.json"))


def test_authorization_url_contents(tmp_path):
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


def test_callback_rejects_state_mismatch(tmp_path):
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    status, _html = flow._handle_callback({"code": "c", "state": "WRONG"})
    assert status == 400
    assert not flow._done.is_set()


def test_callback_success_saves_tokens(tmp_path, monkeypatch):
    store = TokenStore(path=tmp_path / "t.json")
    flow = OAuthFlow(make_settings(), store)
    monkeypatch.setattr(
        oauth,
        "exchange_code",
        lambda settings, code: {"access_token": "fresh", "expires_at": time.time() + 100},
    )
    status, html = flow._handle_callback({"code": "c", "state": flow._state})
    assert status == 200
    assert "connected" in html.lower()
    assert flow._done.is_set()
    assert store.access_token() == "fresh"


def test_callback_reports_linkedin_error(tmp_path):
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


def test_callback_explains_missing_product(tmp_path):
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
    assert 'Scope "openid"' in flow._error  # un-escaped for humans
    assert "Sign In with LinkedIn using OpenID Connect" in flow._error
    assert "Products tab" in html


# --------------------------------------------------------------- security regressions


def test_callback_ignores_error_without_valid_state(tmp_path):
    """An unauthenticated GET must not be able to abort a pending login.

    Any local process — and any web page the user has open, via a bare
    <img src="http://127.0.0.1:8765/callback?error=x"> — can reach this
    listener. Handling `error` before checking `state` let such a request kill
    the flow, which also released the port for an authorization-code
    interceptor.
    """
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    status, _html = flow._handle_callback(
        {"error": "access_denied", "error_description": "drive-by abort"}
    )
    assert status == 400
    assert not flow._done.is_set()  # the login survives
    assert flow._error is None


def test_callback_without_state_cannot_reflect_html(tmp_path):
    """The stateless drive-by path must not echo attacker markup at all."""
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    payload = "<script>alert(document.domain)</script>"
    _status, html = flow._handle_callback({"error": "x", "error_description": payload})
    assert payload not in html
    assert "alert(" not in html


def test_error_page_escapes_attacker_markup(tmp_path):
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


def test_state_mismatch_still_does_not_abort(tmp_path):
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    status, _html = flow._handle_callback({"code": "c", "state": "WRONG"})
    assert status == 400
    assert not flow._done.is_set()


def test_token_file_is_never_world_readable_even_mid_write(tmp_path, monkeypatch):
    """The 0600 must hold from creation, not be applied afterwards.

    write_text()+chmod() published the whole access token at 0644 first; a local
    watcher could win that race. Snapshot the mode from inside the write.
    """
    path = tmp_path / "tokens.json"
    store = TokenStore(path=path)
    observed: list[int] = []
    real_write = os.write

    def spy(fd, data):
        observed.append(os.fstat(fd).st_mode & 0o777)
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", spy)
    store.save({"access_token": "tok", "expires_at": time.time() + 1000})

    assert observed == [0o600]  # already private while the bytes land
    assert path.stat().st_mode & 0o777 == 0o600


def test_token_save_refuses_to_write_through_a_symlink(tmp_path):
    """A pre-planted symlink must not redirect the token write (CWE-59)."""
    victim = tmp_path / "victim.txt"
    victim.write_text("do not clobber")
    link = tmp_path / "tokens.json"
    link.symlink_to(victim)

    with pytest.raises(OSError):
        TokenStore(path=link).save({"access_token": "tok"})
    assert victim.read_text() == "do not clobber"


def test_existing_loose_token_file_is_tightened_on_read(tmp_path):
    """Upgrading from an older version must not leave a 0644 token behind."""
    path = tmp_path / "tokens.json"
    path.write_text('{"access_token": "legacy"}')
    path.chmod(0o644)

    assert TokenStore(path=path).load() == {"access_token": "legacy"}
    assert path.stat().st_mode & 0o777 == 0o600


def test_data_dir_is_private(tmp_path, monkeypatch):
    from linkedin_mcp.config import data_dir

    target = tmp_path / "loose"
    target.mkdir(mode=0o755)
    monkeypatch.setenv("LINKEDIN_MCP_DIR", str(target))

    assert data_dir().stat().st_mode & 0o077 == 0  # no group/other access


@pytest.mark.parametrize("bad_state", ["caf\u00e9", "\U0001f600", "", None])
def test_non_ascii_state_is_rejected_not_crashed(tmp_path, bad_state):
    """secrets.compare_digest() raises TypeError on a non-ASCII str.

    Any drive-by request can supply one, and an exception escaping the handler
    means no HTTP status and a traceback per request. Compare as bytes.
    """
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    status, _html = flow._handle_callback({"state": bad_state, "error": "x"})
    assert status == 400
    assert not flow._done.is_set()
