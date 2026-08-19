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
    assert query["redirect_uri"] == "http://localhost:8765/callback"
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
        {"error": "user_cancelled_authorize", "error_description": "The user cancelled"}
    )
    assert status == 200
    assert "cancelled" in html
    with pytest.raises(Exception, match="cancelled"):
        flow.wait(timeout=1)


def test_callback_explains_missing_product(tmp_path):
    flow = OAuthFlow(make_settings(), TokenStore(path=tmp_path / "t.json"))
    status, html = flow._handle_callback(
        {
            "error": "unauthorized_scope_error",
            "error_description": "Scope &quot;openid&quot; is not authorized for your application",
        }
    )
    assert status == 200
    assert flow._done.is_set()
    assert 'Scope "openid"' in flow._error  # un-escaped for humans
    assert "Sign In with LinkedIn using OpenID Connect" in flow._error
    assert "Products tab" in html
