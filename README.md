# linkedin-safe-mcp

An [MCP](https://modelcontextprotocol.io) server that gives AI agents (Claude Code,
Codex, Claude Desktop, Cursor, …) LinkedIn superpowers — **without putting your
LinkedIn account at risk**:

- **Post to LinkedIn** — text, links, and images via LinkedIn's **official API**
  (OAuth, ToS-compliant), plus comments and likes.
- **Search jobs** — keyword/location/remote/experience/date filters via LinkedIn's
  **public guest endpoints**. No login, no cookies: your account is never involved.
- **Run a job hunt** — a local SQLite application tracker (interested → applied →
  interviewing → offer) with notes and per-job posting snapshots, so an agent can
  manage your pipeline and write tailored cover letters even after a posting is
  taken down.

## Why this design?

LinkedIn offers **no official job-search API**, and the unofficial routes (Voyager
internal API with your `li_at` session cookie, headless browsers on your logged-in
session) violate LinkedIn's User Agreement §8.2 and routinely get accounts
restricted. This server deliberately splits the difference:

| Concern | How it's handled | Account risk |
|---|---|---|
| Posting, comments, likes | Official REST API, your own OAuth app, `w_member_social` | None — sanctioned |
| Job search & details | Guest endpoints (the logged-out jobs pages), IP-rate-limited | None — no credentials involved |
| Pipeline tracking | Local SQLite on your machine | None — never touches LinkedIn |
| Easy Apply, DMs, feed reading | **Intentionally not included** — impossible without ToS-violating access | — |

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- For posting only: a free self-serve LinkedIn developer app (5-minute setup below).
  Job search and the tracker work with zero setup.

## Install & connect to your agent

Clone/copy this directory, then register it with your MCP client. `<REPO>` below is
the absolute path to this project.

**Claude Code**

```bash
claude mcp add linkedin \
  --env LINKEDIN_CLIENT_ID=your_client_id \
  --env LINKEDIN_CLIENT_SECRET=your_client_secret \
  -- uv run --directory <REPO> linkedin-safe-mcp
```

Or in a project's `.mcp.json`:

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "uv",
      "args": ["run", "--directory", "<REPO>", "linkedin-safe-mcp"],
      "env": {
        "LINKEDIN_CLIENT_ID": "your_client_id",
        "LINKEDIN_CLIENT_SECRET": "your_client_secret"
      }
    }
  }
}
```

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.linkedin]
command = "uv"
args = ["run", "--directory", "<REPO>", "linkedin-safe-mcp"]
env = { LINKEDIN_CLIENT_ID = "your_client_id", LINKEDIN_CLIENT_SECRET = "your_client_secret" }
```

**Claude Desktop** (`claude_desktop_config.json`) — same JSON shape as `.mcp.json`
above.

The `LINKEDIN_CLIENT_*` variables are only needed for posting; omit them if you
only want job search + tracking.

## Enabling posting (one-time LinkedIn app setup)

1. Go to <https://www.linkedin.com/developers/apps> → **Create app** (requires
   associating any LinkedIn Page; you can create a trivial one).
2. On the app's **Products** tab, add **Share on LinkedIn** and **Sign In with
   LinkedIn using OpenID Connect**.
3. On the **Auth** tab, add the redirect URL `http://127.0.0.1:8765/callback`
   (it must be the IP literal, not `localhost` — see Security below).
4. Copy the **Client ID** and **Client Secret** into the env vars shown above.
5. Authenticate once — either way works:
   - In a terminal: `uv run --directory <REPO> linkedin-safe-mcp auth`
   - Or just ask your agent to post something; it will call the `login` tool and
     hand you the authorization URL.

Tokens are stored in `~/.linkedin-mcp/tokens.json`, created mode 0600 inside a 0700
directory, and last ~60 days; LinkedIn doesn't issue refresh tokens to self-serve
apps, so you re-run the login when it expires (`auth_status` tells the agent exactly
when that is).
LinkedIn doesn't issue refresh tokens to self-serve apps, so you re-run the login
when it expires (`auth_status` tells the agent exactly when that is).

## Tools

| Tool | Needs auth | What it does |
|---|---|---|
| `auth_status` | – | Reports config/auth state with exact next steps |
| `login` / `logout` | – | Browser OAuth flow / delete stored tokens |
| `get_my_profile` | ✓ | Name, email, person URN of the connected account |
| `create_post` | ✓ | Publish a post: text (+hashtags), optional link **or** local image (real PNG/JPEG/GIF, ≤10 MB); `PUBLIC` or `CONNECTIONS` |
| `delete_post` | ✓ | Delete one of your posts (URN or post URL) |
| `comment_on_post` | ✓ | Comment on a post (URN or post URL) |
| `like_post` | ✓ | Like a post (URN or post URL) |
| `search_jobs` | – | Filters: location, remote/hybrid/onsite, time posted, experience levels, job types, Easy-Apply-only, sort; up to 50 results |
| `get_job` | – | Full posting: description, seniority, type, salary if listed, applicant count, external apply URL |
| `save_job` | – | Snapshot a job into the local tracker |
| `get_saved_job` / `list_saved_jobs` | – | One job with history / pipeline overview with status counts |
| `update_job_status` | – | interested → applied → interviewing → offer / rejected / withdrawn / archived, with notes |
| `add_job_note` / `remove_saved_job` | – | Append a note / drop a job |

Things agents can do with this: *"find remote staff-engineer roles posted this week,
save the promising ones, draft tailored cover letters from the saved descriptions,
mark the ones I applied to, and post a summary of my open-source work."*

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | – | LinkedIn app credentials (posting only) |
| `LINKEDIN_MCP_DIR` | `~/.linkedin-mcp` | Where tokens + tracker DB live |
| `LINKEDIN_REDIRECT_PORT` | `8765` | OAuth callback port (must match the app's redirect URL) |
| `LINKEDIN_API_VERSION` | `202606` | `LinkedIn-Version` header for `/rest/*` calls |
| `LINKEDIN_POSTS_BACKEND` | `auto` | `rest`, `ugc`, or `auto` (try + remember what your app is allowed to use) |
| `LINKEDIN_MCP_USER_AGENT` | a Chrome UA | UA for guest job requests |
| `LINKEDIN_MCP_IMAGE_DIR` | unset | If set, `create_post` may only attach images from this directory |

## Behavior notes & limits

- **Posting**: LinkedIn caps member posting at **150 requests/day** and rejects
  exact duplicates of recent posts (422). Reserved characters in post text are
  escaped automatically for the versioned API so parentheses don't cause errors;
  hashtags are preserved.
- **Job search**: guest endpoints are rate-limited **per IP** (HTTP 429). The
  server caches results (10 min searches / 6 h job details), retries with backoff,
  and paces multi-page fetches; on a persistent 429 it returns a clear "wait a
  minute" error to the agent. Keep `limit` modest.
- **Scraping posture**: guest job search reads the same public pages a logged-out
  visitor sees, at human-ish rates, with caching to minimize load. Still, LinkedIn
  could change or gate these endpoints at any time — the parsers are pinned by
  fixture tests so breakage is detected loudly, and the tool errors stay
  agent-actionable.

## Security

The threat model assumes the agent driving this server is *not* trusted: it reads
job descriptions scraped from LinkedIn, so a hostile posting is a prompt-injection
channel straight into every tool argument. The boundaries that follow from that:

- **Image attachments are not a file-read primitive.** `create_post(image_path=…)`
  accepts only real PNG/JPEG/GIF files — verified by magic bytes *and* the format's
  mandatory trailer, so neither renaming a secret nor appending one after a valid
  header gets through — at most 10 MB, never via a symlink, hardlink, pipe or
  device. Without this, "attach `~/.ssh/id_rsa`" was a valid call that published
  the key. **Residual limit:** an attacker who can already both read a secret and
  write files could encode it inside a structurally valid image; no format check
  can prevent that. Set `LINKEDIN_MCP_IMAGE_DIR` to confine uploads to one folder
  if that matters to you.
- **The OAuth callback validates `state` before anything else.** The listener on
  `127.0.0.1` is reachable by any local process and by any web page the user has
  open, so an unauthenticated request must not be able to abort a pending login
  (which would also free the port for an authorization-code interceptor). Error
  text is HTML-escaped at the sink.
- **The redirect URL is `127.0.0.1`, never `localhost`.** Browsers may resolve the
  name to `::1`, which a different local account can bind. RFC 8252 §8.3.
- **Secrets are 0600 from creation.** Tokens, `state.json` and the tracker DB are
  created private rather than chmod-ed afterwards, closing the window where a
  local watcher could read a fresh access token; `~/.linkedin-mcp` is 0700.
- **Upload targets are pinned.** The Bearer token is only ever PUT to an HTTPS
  `linkedin.com`/`licdn.com` host, whatever URL the API response asks for.

These are covered by regression tests (`tests/test_client_security.py`, the
security sections of `tests/test_oauth.py` and `tests/test_tracker.py`) — each one
is a working exfiltration or hijack attempt that must fail closed.

Found something? Open an issue, or email the address on the GitHub profile for
anything sensitive.

## Development

```bash
uv sync            # install deps (Python ≥3.11)
uv run pytest      # 102 tests: parsers vs live fixtures, payloads, OAuth, tracker,
                   # security regressions, plus an end-to-end stdio smoke test
                   # that spawns the real server
uv run ruff check src tests && uv run ruff format --check src tests
```

Layout: `src/linkedin_mcp/` — `server.py` (tool surface) · `api/` (official REST:
posts, social actions, uploads, dual rest/ugc backend) · `auth/` (OAuth + token
store) · `jobs/` (guest client, HTML parsers, filter mappings) · `tracker/`
(SQLite store) · `cli.py` (`serve` | `auth` | `status` | `logout`).

## Roadmap

- Publish to PyPI (`uvx linkedin-safe-mcp` one-liner)
- Reaction types beyond like; multi-image posts; poll posts
- Optional third-party job-data providers behind the same tool schema
- `streamable-http` transport for remote/hosted use
- (Considered, opt-in only, off by default) a cookie-based Voyager provider for
  personalized features — with loud warnings, since it violates LinkedIn's ToS

## License

MIT
