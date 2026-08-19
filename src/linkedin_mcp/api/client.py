"""Authenticated client for LinkedIn's official REST APIs.

Two posting backends exist:
- "rest": the modern versioned Posts API (POST /rest/posts + LinkedIn-Version header)
- "ugc":  the classic UGC API documented for self-serve "Share on LinkedIn" apps

Which backend a given self-serve app may call has shifted over the years, so
backend "auto" tries one, falls back to the other on authorization-shaped errors
(403/426), and remembers the winner in state.json. Article (link) posts prefer
the ugc backend first because it scrapes the URL's preview metadata; the rest
backend requires the caller to supply title/description.
"""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx

from ..auth.oauth import TokenStore, maybe_refresh
from ..config import Settings, data_dir
from ..errors import LinkedInAPIError, NotAuthenticatedError
from .ltf import escape_little_text
from .urns import encode_urn, extract_post_urn, post_url

logger = logging.getLogger(__name__)

API_BASE = "https://api.linkedin.com"
_FALLBACK_STATUSES = {403, 426}


# --------------------------------------------------------------- payload builders
# Pure functions so tests can pin the exact JSON LinkedIn receives.


def build_rest_post_payload(
    author: str,
    text: str,
    visibility: str,
    article: dict | None = None,
    image_urn: str | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "author": author,
        "commentary": escape_little_text(text),
        "visibility": visibility,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if article:
        content: dict[str, Any] = {
            "source": article["url"],
            # title is required by the rest backend; fall back to the URL itself
            "title": article.get("title") or article["url"],
        }
        if article.get("description"):
            content["description"] = article["description"]
        payload["content"] = {"article": content}
    elif image_urn:
        payload["content"] = {"media": {"id": image_urn}}
    return payload


def build_ugc_post_payload(
    author: str,
    text: str,
    visibility: str,
    article: dict | None = None,
    image_asset: str | None = None,
) -> dict:
    share_content: dict[str, Any] = {
        "shareCommentary": {"text": text},
        "shareMediaCategory": "NONE",
    }
    if article:
        media: dict[str, Any] = {"status": "READY", "originalUrl": article["url"]}
        if article.get("title"):
            media["title"] = {"text": article["title"]}
        if article.get("description"):
            media["description"] = {"text": article["description"]}
        share_content["shareMediaCategory"] = "ARTICLE"
        share_content["media"] = [media]
    elif image_asset:
        share_content["shareMediaCategory"] = "IMAGE"
        share_content["media"] = [{"status": "READY", "media": image_asset}]
    return {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": share_content},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": visibility},
    }


# ------------------------------------------------------------------------ client


class LinkedInClient:
    def __init__(
        self,
        settings: Settings | None = None,
        store: TokenStore | None = None,
        http: httpx.Client | None = None,
    ):
        self.settings = settings or Settings.from_env()
        self.store = store or TokenStore()
        self._http = http or httpx.Client(timeout=30.0)

    # ------------------------------------------------------------- auth plumbing

    def _token(self) -> str:
        token = self.store.access_token()
        if not token:
            token = maybe_refresh(self.settings, self.store)
        if not token:
            raise NotAuthenticatedError(
                "Not authenticated with LinkedIn (no valid access token). Use the "
                "`login` tool, or run `linkedin-safe-mcp auth` in a terminal. "
                "LinkedIn tokens expire after ~60 days and must then be re-created."
            )
        return token

    def _headers(self, versioned: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
        if versioned:
            headers["LinkedIn-Version"] = self.settings.api_version
        return headers

    def _request(
        self, method: str, path: str, *, versioned: bool = False, **kwargs
    ) -> httpx.Response:
        resp = self._http.request(
            method, API_BASE + path, headers=self._headers(versioned), **kwargs
        )
        if resp.status_code >= 400:
            self._raise_api_error(resp)
        return resp

    def _raise_api_error(self, resp: httpx.Response) -> None:
        try:
            body = resp.json()
            message = body.get("message") or json.dumps(body)[:300]
            code = body.get("serviceErrorCode")
        except Exception:
            message, code = resp.text[:300], None
        status = resp.status_code
        if status == 401:
            raise NotAuthenticatedError(
                f"LinkedIn rejected the access token (401: {message}). "
                "Re-run the `login` tool to get a fresh token."
            )
        hints = {
            403: (
                " Hint: the LinkedIn app may be missing a product/scope — posting "
                "needs 'Share on LinkedIn' (w_member_social) plus 'Sign In with "
                "LinkedIn using OpenID Connect'."
            ),
            422: " Hint: LinkedIn rejects exact duplicates of a recent post; vary the text.",
            429: (
                " Hint: rate limited. Member posting is capped at 150 requests/day "
                "(UTC); try again later."
            ),
        }
        raise LinkedInAPIError(status, message + hints.get(status, ""), code)

    # ------------------------------------------------------------------ identity

    def userinfo(self, refresh: bool = False) -> dict:
        if not refresh:
            cached = (self.store.load() or {}).get("profile")
            if cached:
                return cached
        data = self._request("GET", "/v2/userinfo").json()
        self.store.update(profile=data)
        return data

    def person_urn(self) -> str:
        return "urn:li:person:" + self.userinfo()["sub"]

    # ------------------------------------------------------- backend persistence

    @property
    def _state_path(self) -> Path:
        return data_dir() / "state.json"

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _remember_backend(self, backend: str) -> None:
        state = self._load_state()
        if state.get("posts_backend") != backend:
            state["posts_backend"] = backend
            self._state_path.write_text(json.dumps(state, indent=2))

    def _backend_order(self, prefer_ugc: bool) -> list[str]:
        chosen = self.settings.posts_backend
        if chosen in ("rest", "ugc"):
            return [chosen]
        remembered = self._load_state().get("posts_backend")
        default = ["ugc", "rest"] if prefer_ugc else ["rest", "ugc"]
        if remembered in ("rest", "ugc"):
            other = "ugc" if remembered == "rest" else "rest"
            return [remembered, other]
        return default

    # ------------------------------------------------------------------- posting

    def create_post(
        self,
        text: str,
        visibility: str = "PUBLIC",
        article: dict | None = None,
        image_path: str | None = None,
    ) -> dict:
        if article and image_path:
            raise ValueError("A post can attach a link or an image, not both.")
        author = self.person_urn()
        last_error: LinkedInAPIError | None = None
        # ugc first for link posts: it auto-scrapes the URL preview
        for backend in self._backend_order(prefer_ugc=article is not None):
            try:
                if backend == "rest":
                    urn = self._create_post_rest(author, text, visibility, article, image_path)
                else:
                    urn = self._create_post_ugc(author, text, visibility, article, image_path)
            except LinkedInAPIError as exc:
                if exc.status in _FALLBACK_STATUSES:
                    logger.info("posts backend %s refused (%s); trying fallback", backend, exc)
                    last_error = exc
                    continue
                raise
            self._remember_backend(backend)
            return {
                "post_urn": urn,
                "url": post_url(urn),
                "backend": backend,
                "visibility": visibility,
            }
        raise last_error  # both backends refused

    def _create_post_rest(
        self,
        author: str,
        text: str,
        visibility: str,
        article: dict | None,
        image_path: str | None,
    ) -> str:
        image_urn = self._upload_image_rest(author, image_path) if image_path else None
        payload = build_rest_post_payload(author, text, visibility, article, image_urn)
        resp = self._request("POST", "/rest/posts", versioned=True, json=payload)
        return self._created_urn(resp)

    def _create_post_ugc(
        self,
        author: str,
        text: str,
        visibility: str,
        article: dict | None,
        image_path: str | None,
    ) -> str:
        image_asset = self._upload_image_ugc(author, image_path) if image_path else None
        payload = build_ugc_post_payload(author, text, visibility, article, image_asset)
        resp = self._request("POST", "/v2/ugcPosts", json=payload)
        return self._created_urn(resp)

    @staticmethod
    def _created_urn(resp: httpx.Response) -> str:
        urn = resp.headers.get("x-restli-id")
        if not urn:
            try:
                urn = resp.json().get("id", "")
            except Exception:
                urn = ""
        if not urn:
            raise LinkedInAPIError(resp.status_code, "Post created but no ID returned")
        return urn

    # -------------------------------------------------------------- image upload

    def _read_image(self, image_path: str) -> bytes:
        path = Path(image_path).expanduser()
        if not path.is_file():
            raise ValueError(f"Image file not found: {path}")
        return path.read_bytes()

    def _upload_binary(self, upload_url: str, data: bytes) -> None:
        resp = self._http.put(
            upload_url,
            content=data,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/octet-stream",
            },
        )
        if resp.status_code >= 400:
            raise LinkedInAPIError(resp.status_code, f"Image upload failed: {resp.text[:200]}")

    def _upload_image_rest(self, author: str, image_path: str) -> str:
        data = self._read_image(image_path)
        init = self._request(
            "POST",
            "/rest/images?action=initializeUpload",
            versioned=True,
            json={"initializeUploadRequest": {"owner": author}},
        ).json()["value"]
        self._upload_binary(init["uploadUrl"], data)
        return init["image"]

    def _upload_image_ugc(self, author: str, image_path: str) -> str:
        data = self._read_image(image_path)
        register = self._request(
            "POST",
            "/v2/assets?action=registerUpload",
            json={
                "registerUploadRequest": {
                    "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                    "owner": author,
                    "serviceRelationships": [
                        {
                            "relationshipType": "OWNER",
                            "identifier": "urn:li:userGeneratedContent",
                        }
                    ],
                }
            },
        ).json()["value"]
        upload_url = register["uploadMechanism"][
            "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
        ]["uploadUrl"]
        self._upload_binary(upload_url, data)
        return register["asset"]

    # ---------------------------------------------------------- delete / social

    def delete_post(self, target: str) -> str:
        urn = extract_post_urn(target)
        self._rest_then_v2(
            "DELETE", f"/rest/posts/{encode_urn(urn)}", f"/v2/ugcPosts/{encode_urn(urn)}"
        )
        return urn

    def comment(self, target: str, text: str) -> dict:
        urn = extract_post_urn(target)
        payload = {
            "actor": self.person_urn(),
            "object": urn,
            "message": {"text": text},
        }
        resp = self._rest_then_v2(
            "POST",
            f"/rest/socialActions/{encode_urn(urn)}/comments",
            f"/v2/socialActions/{encode_urn(urn)}/comments",
            json=payload,
        )
        comment_urn = None
        with suppress(Exception):
            comment_urn = resp.json().get("commentUrn")
        return {"comment_urn": comment_urn, "target_urn": urn, "message": text}

    def like(self, target: str) -> str:
        urn = extract_post_urn(target)
        payload = {"actor": self.person_urn(), "object": urn}
        self._rest_then_v2(
            "POST",
            f"/rest/socialActions/{encode_urn(urn)}/likes",
            f"/v2/socialActions/{encode_urn(urn)}/likes",
            json=payload,
        )
        return urn

    def _rest_then_v2(self, method: str, rest_path: str, v2_path: str, **kwargs) -> httpx.Response:
        try:
            return self._request(method, rest_path, versioned=True, **kwargs)
        except LinkedInAPIError as exc:
            if exc.status not in _FALLBACK_STATUSES:
                raise
            logger.info("%s %s refused (%s); retrying via v2", method, rest_path, exc.status)
            return self._request(method, v2_path, **kwargs)
