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
import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..auth.oauth import TokenStore, maybe_refresh
from ..config import Settings, data_dir, ensure_private, image_root, write_private
from ..errors import LinkedInAPIError, LinkedInError, NotAuthenticatedError
from .ltf import escape_little_text
from .urns import encode_urn, extract_post_urn, post_url

logger = logging.getLogger(__name__)

API_BASE = "https://api.linkedin.com"
_FALLBACK_STATUSES = {403, 426}

# --------------------------------------------------------------- upload guards

# `image_path` is an agent-supplied tool argument, and this same server feeds
# untrusted scraped job text into that agent's context. Without these checks the
# image attachment is an arbitrary-file-read-to-public-post exfiltration channel:
# "attach ~/.ssh/id_rsa" is a valid call. An extension allowlist alone is
# bypassed by renaming, and a magic-byte prefix alone is bypassed by appending the
# secret after a real header — so the content must match at BOTH ends.
_IMAGE_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
}
# Each format's mandatory final bytes: PNG's IEND chunk, JPEG's EOI marker, GIF's
# trailer. Requiring the file to END here is what stops `header || secret`.
_IMAGE_TRAILER: dict[str, bytes] = {
    ".png": b"IEND\xaeB`\x82",
    ".jpg": b"\xff\xd9",
    ".jpeg": b"\xff\xd9",
    ".gif": b";",
}
_IMAGE_MAX_BYTES = 10 * 1024 * 1024  # LinkedIn rejects larger member uploads anyway

# Hosts allowed to receive the Bearer token during a media upload. The upload URL
# arrives inside a LinkedIn API response, so it is only as trustworthy as that
# response — pin it rather than PUT credentials wherever we are told to.
_UPLOAD_HOSTS = ("linkedin.com", "licdn.com")


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
            data = json.loads(self._state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        ensure_private(self._state_path)  # tighten a file written by an older version
        return data

    def _remember_backend(self, backend: str) -> None:
        state = self._load_state()
        if state.get("posts_backend") != backend:
            state["posts_backend"] = backend
            write_private(self._state_path, json.dumps(state, indent=2))

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
        """Read a local image for upload, refusing anything that isn't really an image.

        Deliberately strict — see the _IMAGE_MAGIC comment above for why this is a
        security boundary and not mere input tidiness.
        """
        try:
            # resolve() collapses symlinks and traversal so the confinement
            # check below cannot be walked around with a link or '..'.
            path = Path(image_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Image file not found: {image_path}") from exc

        suffix = path.suffix.lower()
        if suffix not in _IMAGE_MAGIC:
            raise ValueError(
                f"Refusing to attach {path.name!r}: only "
                f"{', '.join(sorted(_IMAGE_MAGIC))} files can be posted."
            )

        root = image_root()
        if root is not None and not path.is_relative_to(root):
            raise ValueError(
                f"Refusing to attach {path}: LINKEDIN_MCP_IMAGE_DIR confines uploads to {root}."
            )

        # O_NONBLOCK matters: without it, os.open() on a FIFO with no writer blocks
        # forever and wedges this thread — an agent-reachable hang. It is a no-op for
        # regular files, which are all we go on to accept. O_NOFOLLOW is
        # belt-and-braces against a symlink swapped in after resolve() (TOCTOU).
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(
                    f"Refusing to attach {path}: not a regular file "
                    "(a directory, device or pipe cannot be a post image)."
                )
            if info.st_nlink > 1:
                # A hardlink cannot be resolved away, so it is the one way to
                # smuggle an outside file into LINKEDIN_MCP_IMAGE_DIR.
                raise ValueError(
                    f"Refusing to attach {path.name!r}: it is a hardlink "
                    f"({info.st_nlink} names point at these bytes). Copy it first."
                )
            chunks: list[bytes] = []
            remaining = _IMAGE_MAX_BYTES + 1  # one extra byte reveals an oversized file
            while remaining > 0:
                chunk = os.read(fd, min(remaining, 1 << 20))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(fd)

        data = b"".join(chunks)
        if len(data) > _IMAGE_MAX_BYTES:
            raise ValueError(
                f"Refusing to attach {path.name!r}: larger than "
                f"{_IMAGE_MAX_BYTES // (1024 * 1024)} MB."
            )
        kind = suffix.lstrip(".")
        if not data.startswith(_IMAGE_MAGIC[suffix]):
            raise ValueError(
                f"Refusing to attach {path.name!r}: the contents are not a real "
                f"{kind} image, whatever the file name says."
            )
        if not data.endswith(_IMAGE_TRAILER[suffix]):
            raise ValueError(
                f"Refusing to attach {path.name!r}: it starts like a {kind} image but "
                f"does not end like one — truncated, or something is appended after "
                f"the image data."
            )
        return data

    @staticmethod
    def _checked_upload_url(upload_url: str) -> str:
        try:
            parsed = urlparse(upload_url)
            host = (parsed.hostname or "").lower()
            # No userinfo: "https://www.linkedin.com@evil.test/" reads as LinkedIn to a
            # human and resolves to evil.test. LinkedIn never returns credentials in an
            # upload URL, so refusing the whole class costs nothing. A trailing-dot
            # host is likewise never legitimate here, so it is not normalised away.
            ok = (
                parsed.scheme == "https"
                and parsed.username is None
                and parsed.password is None
                and any(host == h or host.endswith("." + h) for h in _UPLOAD_HOSTS)
            )
        except ValueError:  # malformed enough that urlparse itself gives up
            ok = False
        if not ok:
            raise LinkedInError(
                f"Refusing to upload to {upload_url[:80]!r}: LinkedIn returned an "
                "upload target that is not an HTTPS LinkedIn host. Nothing was sent."
            )
        return upload_url

    def _upload_binary(self, upload_url: str, data: bytes) -> None:
        resp = self._http.put(
            self._checked_upload_url(upload_url),
            content=data,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/octet-stream",
            },
        )
        # >=300, not >=400: redirects are not followed (that would carry the Bearer
        # token off-host), so a 3xx means the bytes never landed.
        if resp.status_code >= 300:
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
