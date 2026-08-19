"""Environment-driven configuration and shared filesystem paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REDIRECT_PORT = 8765
# LinkedIn-Version header (YYYYMM) sent to /rest/* endpoints. LinkedIn sunsets
# versions roughly a year after release; override with LINKEDIN_API_VERSION.
DEFAULT_API_VERSION = "202606"


def data_dir() -> Path:
    """Directory for tokens, the tracker DB, and remembered state."""
    override = os.environ.get("LINKEDIN_MCP_DIR")
    path = Path(override).expanduser() if override else Path.home() / ".linkedin-mcp"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    client_id: str | None
    client_secret: str | None
    redirect_port: int
    api_version: str
    posts_backend: str  # "auto" | "rest" | "ugc"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            client_id=os.environ.get("LINKEDIN_CLIENT_ID") or None,
            client_secret=os.environ.get("LINKEDIN_CLIENT_SECRET") or None,
            redirect_port=int(os.environ.get("LINKEDIN_REDIRECT_PORT", DEFAULT_REDIRECT_PORT)),
            api_version=os.environ.get("LINKEDIN_API_VERSION", DEFAULT_API_VERSION),
            posts_backend=os.environ.get("LINKEDIN_POSTS_BACKEND", "auto").lower(),
        )

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.redirect_port}/callback"

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


SETUP_INSTRUCTIONS = """\
To enable LinkedIn posting, create a (free) LinkedIn developer app once:
1. Go to https://www.linkedin.com/developers/apps and click "Create app".
2. In the app's Products tab, add "Share on LinkedIn" and
   "Sign In with LinkedIn using OpenID Connect".
3. In the Auth tab, add this Authorized redirect URL: http://localhost:{port}/callback
4. Copy the Client ID and Client Secret from the Auth tab and export them where the
   MCP server runs, e.g. in the MCP client config:
     LINKEDIN_CLIENT_ID=...   LINKEDIN_CLIENT_SECRET=...
5. Restart the MCP server, then use the `login` tool (or run
   `linkedin-safe-mcp auth` in a terminal) to connect the LinkedIn account.
Job search tools work without any of this."""


def setup_instructions(settings: Settings) -> str:
    return SETUP_INSTRUCTIONS.format(port=settings.redirect_port)
