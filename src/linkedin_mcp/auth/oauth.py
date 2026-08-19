"""LinkedIn OAuth 2.0 (3-legged) flow with a localhost callback listener.

LinkedIn specifics that shaped this module:
- Access tokens live ~60 days. Refresh tokens are only issued to approved
  Marketing partners, so most users re-run the browser flow when the token
  expires (we still use a refresh token if LinkedIn ever provides one).
- The redirect URL must be registered verbatim in the LinkedIn app's Auth tab,
  which is why the port is fixed/configurable rather than randomly chosen.
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import secrets
import threading
import time
import webbrowser
from contextlib import suppress
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..config import Settings, data_dir, ensure_private, setup_instructions, write_private
from ..errors import LinkedInError, NotAuthenticatedError

AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
SCOPES = ["openid", "profile", "email", "w_member_social"]

logger = logging.getLogger(__name__)

_SUCCESS_HTML = """<!doctype html><meta charset="utf-8"><title>LinkedIn connected</title>
<body style="font-family:system-ui;display:grid;place-items:center;height:90vh">
<div style="text-align:center"><h1>&#10003; LinkedIn connected</h1>
<p>You can close this tab and return to your agent.</p></div></body>"""

_ERROR_HTML = """<!doctype html><meta charset="utf-8"><title>LinkedIn login failed</title>
<body style="font-family:system-ui;display:grid;place-items:center;height:90vh">
<div style="text-align:center"><h1>&#10007; Login failed</h1><p>{msg}</p></div></body>"""


def _error_page(msg: str) -> str:
    """Render the failure page.

    `msg` carries attacker-influenced text (LinkedIn's error_description is just
    a query parameter on a loopback URL anyone can hit), and
    _explain_authorize_error un-escapes it for readability — so it must be
    HTML-escaped here, at the sink, or it is reflected XSS.
    """
    return _ERROR_HTML.format(msg=html_mod.escape(msg, quote=True))


class TokenStore:
    """Persists the OAuth token set (plus a cached userinfo profile) as JSON, 0600."""

    def __init__(self, path: Path | None = None):
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        # Resolved lazily so LINKEDIN_MCP_DIR set at call time (e.g. in tests) wins.
        return self._path or (data_dir() / "tokens.json")

    def load(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        ensure_private(self.path)  # tighten a token file written by an older version
        return data

    def save(self, tokens: dict) -> None:
        with self._lock:
            write_private(self.path, json.dumps(tokens, indent=2))

    def update(self, **fields) -> None:
        data = self.load() or {}
        data.update(fields)
        self.save(data)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def access_token(self) -> str | None:
        data = self.load()
        if not data:
            return None
        if time.time() >= data.get("expires_at", 0):
            return None
        return data.get("access_token")

    def expires_at_iso(self) -> str | None:
        data = self.load()
        if not data or "expires_at" not in data:
            return None
        return datetime.fromtimestamp(data["expires_at"], tz=UTC).isoformat()


# Which Developer Portal product grants each scope we request — LinkedIn's
# "Scope ... is not authorized" error names the scope but not the product.
_SCOPE_PRODUCTS = {
    "openid": "Sign In with LinkedIn using OpenID Connect",
    "profile": "Sign In with LinkedIn using OpenID Connect",
    "email": "Sign In with LinkedIn using OpenID Connect",
    "w_member_social": "Share on LinkedIn",
}


def _explain_authorize_error(description: str) -> str:
    description = html_mod.unescape(description)  # LinkedIn HTML-escapes quotes
    if "not authorized" in description.lower():
        for scope, product in _SCOPE_PRODUCTS.items():
            if scope in description:
                return (
                    f"{description} — fix: at https://www.linkedin.com/developers/apps "
                    f'open this app\'s Products tab and add "{product}", then log in '
                    f"again. (Product access is usually granted instantly.)"
                )
    return description


def _token_request(form: dict) -> dict:
    resp = httpx.post(TOKEN_URL, data=form, timeout=30.0)
    if resp.status_code != 200:
        raise LinkedInError(
            f"LinkedIn token endpoint returned {resp.status_code}: {resp.text[:300]}"
        )
    payload = resp.json()
    now = time.time()
    tokens = {
        "access_token": payload["access_token"],
        # 60 s safety margin so we never present an about-to-expire token
        "expires_at": now + int(payload.get("expires_in", 0)) - 60,
        "scope": payload.get("scope"),
        "obtained_at": now,
    }
    if payload.get("refresh_token"):
        tokens["refresh_token"] = payload["refresh_token"]
        tokens["refresh_expires_at"] = now + int(payload.get("refresh_token_expires_in", 0))
    return tokens


def exchange_code(settings: Settings, code: str) -> dict:
    return _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "redirect_uri": settings.redirect_uri,
        }
    )


def maybe_refresh(settings: Settings, store: TokenStore) -> str | None:
    """Refresh the access token if a still-valid refresh token exists (partner apps
    only — most self-serve apps never get one). Returns the new access token or None."""
    data = store.load()
    if not data or not data.get("refresh_token"):
        return None
    if time.time() >= data.get("refresh_expires_at", 0):
        return None
    try:
        tokens = _token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": data["refresh_token"],
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
            }
        )
    except LinkedInError as exc:
        logger.warning("Token refresh failed: %s", exc)
        return None
    if "profile" in data:  # keep the cached userinfo across refreshes
        tokens["profile"] = data["profile"]
    store.save(tokens)
    return tokens["access_token"]


class OAuthFlow:
    """One-shot authorization-code flow.

    start() binds the localhost listener and returns the URL the user must open;
    the listener thread exchanges the code and saves tokens when LinkedIn
    redirects back. wait() blocks until that happens (CLI usage); MCP tool usage
    instead polls auth_status.
    """

    def __init__(self, settings: Settings, store: TokenStore):
        if not settings.configured:
            raise NotAuthenticatedError(
                "LinkedIn app credentials are missing (LINKEDIN_CLIENT_ID / "
                "LINKEDIN_CLIENT_SECRET).\n\n" + setup_instructions(settings)
            )
        self.settings = settings
        self.store = store
        self._state = secrets.token_urlsafe(24)
        self._done = threading.Event()
        self._error: str | None = None
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def authorization_url(self) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "state": self._state,
                "scope": " ".join(SCOPES),
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def start(self, open_browser: bool = True, timeout: float = 600.0) -> str:
        flow = self
        callback_path = urlparse(self.settings.redirect_uri).path

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # stdout belongs to the MCP protocol
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path != callback_path:
                    self.send_error(404)
                    return
                params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                status, html = flow._handle_callback(params)
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode())

        try:
            self._server = HTTPServer(("127.0.0.1", self.settings.redirect_port), Handler)
        except OSError as exc:
            # Leave the flow in a terminal state so no holder of this object can
            # ever mistake it for a login in progress.
            self._error = f"Could not bind localhost:{self.settings.redirect_port}: {exc}"
            self._done.set()
            raise LinkedInError(
                f"Could not listen on localhost:{self.settings.redirect_port} ({exc}). "
                "Another login may be in progress, or the port is taken — set "
                "LINKEDIN_REDIRECT_PORT to a free port and add the matching redirect "
                "URL to the LinkedIn app's Auth tab."
            ) from exc

        self._server.timeout = 1.0
        deadline = time.time() + timeout

        def serve():
            try:
                while not self._done.is_set() and time.time() < deadline:
                    self._server.handle_request()
            finally:
                self._server.server_close()
            if not self._done.is_set():
                self._error = "Login timed out before the browser flow completed."
                self._done.set()

        self._thread = threading.Thread(target=serve, name="linkedin-oauth", daemon=True)
        self._thread.start()

        url = self.authorization_url()
        if open_browser:
            with suppress(Exception):  # headless environment — the URL is still returned
                webbrowser.open(url)
        return url

    def _handle_callback(self, params: dict) -> tuple[int, str]:
        # The state check comes FIRST, before the error branch, and gates every
        # path that can terminate the flow. This listener is reachable by any
        # local process and by any web page the user has open (a bare <img
        # src="http://127.0.0.1:8765/callback?error=x"> is enough), so handling
        # `error` before validating `state` let an unauthenticated request kill
        # a pending login — which also released the port for whoever wanted to
        # catch the real authorization code next.
        # Compared as bytes: secrets.compare_digest() raises TypeError on a
        # non-ASCII str, and any drive-by request can supply one.
        supplied = str(params.get("state") or "").encode("utf-8", "replace")
        if not secrets.compare_digest(supplied, self._state.encode()):
            return 400, _error_page("State mismatch — possible CSRF; try again.")
        if params.get("error"):
            self._error = _explain_authorize_error(
                params.get("error_description") or params["error"]
            )
            self._done.set()
            return 200, _error_page(self._error)
        code = params.get("code")
        if not code:
            return 400, _error_page("Missing authorization code.")
        try:
            tokens = exchange_code(self.settings, code)
        except Exception as exc:
            self._error = str(exc)
            self._done.set()
            return 200, _error_page(self._error)
        self.store.save(tokens)
        self._done.set()
        return 200, _SUCCESS_HTML

    def wait(self, timeout: float = 300.0) -> None:
        if not self._done.wait(timeout):
            raise LinkedInError("Timed out waiting for the browser login to complete.")
        if self._error:
            raise LinkedInError(f"LinkedIn login failed: {self._error}")
