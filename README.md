# linkedin-safe-mcp

An [MCP](https://modelcontextprotocol.io) server for account-safe LinkedIn
automation: official-API posting and engagement, plus logged-out public job
search. Job search needs no LinkedIn credentials; posting uses a LinkedIn OAuth
app and a one-time browser login.

This is a maintained fork of
[AmmYoo7/linkedin-safe-mcp](https://github.com/AmmYoo7/linkedin-safe-mcp).

## Features

- Publish posts with text, links, or validated local images.
- Comment on, like, and delete the account's own posts.
- Search public job listings with location, workplace, date, experience, and
  employment filters, then fetch full job details.
- Expose a stateless Streamable HTTP MCP endpoint at `/mcp`.

## Quick start with Docker

Requirements: Docker with Compose. The standalone Compose file creates its
default project network, stores application state in the named
`linkedin-safe-mcp-data` volume, and publishes both listeners on host loopback:

- MCP: `http://127.0.0.1:8000/mcp`
- OAuth callback: `http://127.0.0.1:8765/callback`

Job search works without a `.env` file. For posting, copy the example and add
your LinkedIn app credentials:

```bash
cp .env.example .env
$EDITOR .env
docker compose up -d --build
```

Point a local MCP client at `http://127.0.0.1:8000/mcp`. The default Host and
Origin allowlists accept `127.0.0.1` and `localhost`; keep them restricted to
the exact local or trusted origins you use. If either default host port is busy,
set `LINKEDIN_MCP_HOST_PORT` or `LINKEDIN_REDIRECT_HOST_PORT` in `.env` before
starting.

Useful operations:

```bash
docker compose logs -f
docker compose restart
docker compose down
```

Do not add `-v` to `docker compose down` unless you intentionally want to erase
the named volume and its saved OAuth tokens and application state. That data is
not recoverable from the container.

If you put a reverse proxy in front of the loopback listeners, terminate TLS
there and set `LINKEDIN_MCP_ALLOWED_HOSTS` and
`LINKEDIN_MCP_ALLOWED_ORIGINS` to the exact public host and browser origin.
Do not expose the application listener directly to an untrusted network.

## Enable posting

1. Create an app at <https://www.linkedin.com/developers/apps> and add the
   **Share on LinkedIn** and **Sign In with LinkedIn using OpenID Connect**
   products.
2. Add `http://127.0.0.1:8765/callback` as an authorized redirect URL.
3. Put the app's client ID and secret in `.env`, then recreate the service:

   ```bash
   docker compose up -d --force-recreate
   ```

4. Ask your MCP client to call `login`, open the returned URL, and then call
   `auth_status` to confirm the connection.

The callback uses the IPv4 loopback literal intentionally. When the browser is
on another machine, forward the callback port with a secure SSH tunnel or
equivalent trusted local tunnel. Tokens are stored privately in the `/data`
volume and are never written into the image.

## Tools

The current public surface contains 10 tools:

| Tool | Auth | Purpose |
|---|---|---|
| `auth_status` | – | Report configuration and authentication state |
| `login` / `logout` | – | Start OAuth or delete saved tokens |
| `get_my_profile` | ✓ | Read the connected account profile |
| `create_post` | ✓ | Publish text, link, or image posts |
| `delete_post` | ✓ | Delete one of the account's posts |
| `comment_on_post` | ✓ | Comment on a post |
| `like_post` | ✓ | Like a post |
| `search_jobs` | – | Search public job listings |
| `get_job` | – | Fetch a complete job listing |

Posting is subject to LinkedIn API permissions and rate limits. Public job
endpoints may be rate-limited per IP; keep result limits modest and retry later
when asked.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | empty | LinkedIn app credentials for posting and login |
| `LINKEDIN_MCP_DIR` | `/data` in Compose | Token and state directory |
| `LINKEDIN_MCP_HOST` / `LINKEDIN_MCP_PORT` | `0.0.0.0` / `8000` in Compose | Container HTTP bind |
| `LINKEDIN_MCP_ALLOWED_HOSTS` | loopback hosts | HTTP Host allowlist |
| `LINKEDIN_MCP_ALLOWED_ORIGINS` | loopback origins | Browser Origin allowlist |
| `LINKEDIN_REDIRECT_PORT` | `8765` | OAuth callback port inside the container |
| `LINKEDIN_REDIRECT_BIND_HOST` | `0.0.0.0` in Compose | Callback listener bind |
| `LINKEDIN_REDIRECT_URI` | loopback callback | Optional exact OAuth redirect override |
| `LINKEDIN_OPEN_BROWSER` | `false` in Compose | Open a browser from the service process |
| `LINKEDIN_API_VERSION` | `202606` | Version header for LinkedIn REST calls |
| `LINKEDIN_POSTS_BACKEND` | `auto` | `rest`, `ugc`, or automatic selection |
| `LINKEDIN_MCP_USER_AGENT` | Chrome-like UA | User agent for public job requests |
| `LINKEDIN_MCP_IMAGE_DIR` | unset | Optional image-upload directory |

## Development

Local development requires Python 3.14 and
[`uv`](https://docs.astral.sh/uv/). Use the locked environment and hardlink mode:

```bash
UV_LINK_MODE=hardlink uv sync --locked
just check
just unit
just docker-build
just runtime-smoke
```

The runtime smoke starts an isolated Compose project with free loopback host
ports, waits for health, and sends real JSON-RPC `initialize` and `tools/list`
requests to the published `/mcp` endpoint. It checks the complete 10-tool
surface and cleans up its temporary containers and volume.

See [`SECURITY.md`](SECURITY.md) for vulnerability reporting. This project is
licensed under the [MIT License](LICENSE).
