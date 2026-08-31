"""MCP server definition: the agent-facing tool surface.

Design notes:
- Tools are synchronous; the MCP SDK offloads them to worker threads.
- Clients are created lazily so `import` (and tools like search_jobs) never
  require credentials.
- Return types are pydantic models from models.py → structured tool output.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from .api.client import LinkedInClient
from .auth.oauth import OAuthFlow, TokenStore
from .config import Settings, setup_instructions
from .errors import LinkedInError
from .jobs.filters import build_search_params
from .jobs.guest_client import GuestJobsClient
from .models import (
    AuthStatus,
    CommentResult,
    CreatePostInput,
    JobDetail,
    JobSearchResults,
    LoginStarted,
    PostResult,
    Profile,
    SearchJobsInput,
)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _required_string(value: object, what: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise LinkedInError(f"LinkedIn returned an invalid {what}.")
    return result


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


mcp = MCPServer(
    name="linkedin",
    instructions=(
        "LinkedIn tools for agents, in two groups:\n"
        "1) Posting/engagement (create_post, comment_on_post, like_post, …) uses the "
        "official LinkedIn API on the user's behalf — requires one-time OAuth: check "
        "auth_status; if not authenticated, call login and have the user open the URL. "
        "LinkedIn caps member posting at 150 requests/day and rejects duplicate posts.\n"
        "2) Job search (search_jobs, get_job) uses LinkedIn's public guest endpoints — "
        "no login needed, the user's account is never at risk. On rate-limit errors, "
        "wait a minute before retrying and keep limits small."
    ),
)

_singletons: dict[str, object] = {}


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Container health endpoint; it deliberately exposes no credentials or user data."""
    return JSONResponse({"status": "ok"})


def _store() -> TokenStore:
    if "store" not in _singletons:
        _singletons["store"] = TokenStore()
    return cast(TokenStore, _singletons["store"])


def _api() -> LinkedInClient:
    if "api" not in _singletons:
        _singletons["api"] = LinkedInClient(store=_store())
    return cast(LinkedInClient, _singletons["api"])


def _jobs() -> GuestJobsClient:
    if "jobs" not in _singletons:
        _singletons["jobs"] = GuestJobsClient()
    return cast(GuestJobsClient, _singletons["jobs"])


def build_auth_status() -> AuthStatus:
    settings = Settings.from_env()
    store = _store()
    data = store.load() or {}
    profile = _mapping(data.get("profile"))
    token_valid = store.access_token() is not None
    if not settings.configured:
        # Saved tokens keep posting working even without app credentials in the
        # env — creds are only needed to run a (re-)login.
        detail = (
            "Authenticated with saved tokens; posting works. But app credentials "
            "(LINKEDIN_CLIENT_ID/SECRET) are missing from this environment, so "
            "re-login won't be possible when the token expires."
            if token_valid
            else (
                "LinkedIn app credentials are missing, so posting tools are unavailable. Job search works without them."
            )
        )
        return AuthStatus(
            configured=False,
            authenticated=token_valid,
            detail=detail,
            scopes=_optional_string(data.get("scope")),
            expires_at=store.expires_at_iso(),
            profile_name=_optional_string(profile.get("name")),
            setup_instructions=None if token_valid else setup_instructions(settings),
        )
    if token_valid:
        detail = "Authenticated — posting tools are ready."
    elif data.get("access_token"):
        detail = "Access token expired (LinkedIn tokens last ~60 days). Run `login` again."
    else:
        detail = "Credentials configured but nobody has logged in yet. Run the `login` tool."
    return AuthStatus(
        configured=True,
        authenticated=token_valid,
        detail=detail,
        scopes=_optional_string(data.get("scope")),
        expires_at=store.expires_at_iso(),
        profile_name=_optional_string(profile.get("name")),
    )


# ------------------------------------------------------------------------- auth


@mcp.tool()
def auth_status() -> AuthStatus:
    """Check LinkedIn authentication state. Call this before posting tools; if it
    reports not configured/authenticated it includes exactly what to do next.
    Job-search tools never need authentication."""
    return build_auth_status()


@mcp.tool()
def login() -> LoginStarted:
    """Start the LinkedIn OAuth login. Returns an authorization URL — show it to the
    user and ask them to open it in a browser (it may also open automatically); the
    local callback completes the flow. Afterwards, call auth_status to confirm."""
    store = _store()
    if store.access_token():
        profile = _mapping((store.load() or {}).get("profile"))
        who = _optional_string(profile.get("name")) or "an account"
        return LoginStarted(
            authorization_url="",
            message=f"Already authenticated as {who}. Call `logout` first to switch accounts.",
        )
    flow = _singletons.get("flow")
    if isinstance(flow, OAuthFlow) and not flow._done.is_set():
        return LoginStarted(
            authorization_url=flow.authorization_url(),
            message="A login is already in progress — ask the user to open this URL, then call auth_status.",
        )
    flow = OAuthFlow(Settings.from_env(), store)
    url = flow.start(open_browser=Settings.from_env().open_browser)
    # Cache only after start() succeeds: a flow that never bound its listener must
    # not be reused by the in-progress guard above (its URL would be a dead end).
    _singletons["flow"] = flow
    return LoginStarted(
        authorization_url=url,
        message=(
            "Browser login started (a window may have opened automatically). Ask the "
            "user to open the URL and approve access, then call auth_status to confirm."
        ),
    )


@mcp.tool()
def logout() -> str:
    """Delete the stored LinkedIn tokens for this machine."""
    _store().clear()
    return "Logged out — stored LinkedIn tokens deleted."


@mcp.tool()
def get_my_profile() -> Profile:
    """Get the authenticated user's LinkedIn identity (name, email, person URN).
    Requires login."""
    info = _api().userinfo()
    return Profile(
        person_urn="urn:li:person:" + _required_string(info.get("sub"), "profile subject"),
        name=_optional_string(info.get("name")),
        given_name=_optional_string(info.get("given_name")),
        family_name=_optional_string(info.get("family_name")),
        email=_optional_string(info.get("email")),
        locale=str(info.get("locale")) if info.get("locale") is not None else None,
        picture=_optional_string(info.get("picture")),
    )


# ---------------------------------------------------------------------- posting


@mcp.tool()
def create_post(
    post: CreatePostInput,
) -> PostResult:
    """Publish a LinkedIn post as the authenticated user. IMPORTANT: posts are
    public professional content — confirm the final text with the user before
    calling this. Hashtags (#likeThis) work in `text`. Attach at most one of:
    `link` (shares a URL; `link_title`/`link_description` improve its preview
    card) or `image_path` (local file to upload). LinkedIn rejects exact
    duplicates of recent posts and caps posting at 150/day."""
    article = None
    if post.link:
        article = {"url": post.link, "title": post.link_title, "description": post.link_description}
    result = _api().create_post(
        text=post.text,
        visibility=post.visibility,
        article=article,
        image_path=post.image_path,
    )
    return PostResult(**result)


@mcp.tool()
def delete_post(post: str) -> str:
    """Delete one of the user's own posts. `post` is a post URN or a linkedin.com
    post URL."""
    urn = _api().delete_post(post)
    return f"Deleted {urn}."


@mcp.tool()
def comment_on_post(post: str, text: str) -> CommentResult:
    """Comment on a LinkedIn post as the authenticated user. `post` is a post URN
    (urn:li:share/ugcPost/activity:…) or a linkedin.com post URL. Confirm wording
    with the user first — comments are public."""
    result = _api().comment(post, text)
    return CommentResult(
        comment_urn=result["comment_urn"],
        target_urn=_required_string(result["target_urn"], "comment target"),
        message=_required_string(result["message"], "comment message"),
    )


@mcp.tool()
def like_post(post: str) -> str:
    """Like a LinkedIn post as the authenticated user. `post` is a post URN or a
    linkedin.com post URL."""
    urn = _api().like(post)
    return f"Liked {urn}."


# ------------------------------------------------------------------------- jobs


@mcp.tool()
def search_jobs(
    query: SearchJobsInput,
) -> JobSearchResults:
    """Search LinkedIn job postings (no login required; the user's account is never
    involved). `location` is free text like "Berlin", "India", or "United States";
    combine it with workplace="remote" for remote roles. `limit` max is 50 — keep
    it modest to avoid IP rate-limiting; on a rate-limit error, wait a minute
    before retrying. Use get_job with a result's job_id for the full description."""
    limit = max(1, min(int(query.limit), 50))
    params = build_search_params(query)
    jobs = _jobs().search(params, limit=limit)
    note = None
    if not jobs:
        note = "No results — try broader keywords, a different location, or fewer filters."
    return JobSearchResults(count=len(jobs), jobs=jobs, note=note)


@mcp.tool()
def get_job(job: str) -> JobDetail:
    """Fetch full details for one job posting: description, seniority, employment
    type, salary when listed, applicant count, and the external apply URL if the
    posting is not Easy Apply. `job` is a job_id from search_jobs, a
    linkedin.com/jobs/view/... URL, or a jobPosting URN."""
    return _jobs().job(job)
