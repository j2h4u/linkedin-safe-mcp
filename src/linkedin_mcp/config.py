"""Environment-driven configuration, shared filesystem paths, and the private-file
primitives every secret-bearing write in this package goes through."""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REDIRECT_PORT = 8765
# LinkedIn-Version header (YYYYMM) sent to /rest/* endpoints. LinkedIn sunsets
# versions roughly a year after release; override with LINKEDIN_API_VERSION.
DEFAULT_API_VERSION = "202606"

_PRIVATE_FILE = 0o600
_PRIVATE_DIR = 0o700


def data_dir() -> Path:
    """Directory for tokens, the tracker DB, and remembered state.

    Forced to 0700: everything in here (OAuth access token, the job-hunt
    database with salary/interview notes) is private to the user, and the
    default 0755 made those readable by every other local account.
    """
    override = os.environ.get("LINKEDIN_MCP_DIR")
    path = Path(override).expanduser() if override else Path.home() / ".linkedin-mcp"
    # mkdir(parents=True, mode=...) applies the mode to the leaf only — any
    # intermediate directory it invents would land at 0755. Walk down instead.
    for ancestor in reversed(path.parents):
        if not ancestor.exists():
            with suppress(OSError):
                ancestor.mkdir(mode=_PRIVATE_DIR)
    path.mkdir(exist_ok=True, mode=_PRIVATE_DIR)
    # mkdir's mode applies only when it creates the directory, so an existing
    # (or pre-planted) directory still has to be tightened explicitly.
    with suppress(OSError):
        if path.stat().st_mode & 0o077:
            path.chmod(_PRIVATE_DIR)
    return path


def image_root() -> Path | None:
    """Optional directory that `create_post(image_path=...)` is confined to.

    Unset by default. Set LINKEDIN_MCP_IMAGE_DIR to restrict uploads to one
    folder — worth doing if the agent driving this server also reads
    untrusted web content.
    """
    override = os.environ.get("LINKEDIN_MCP_IMAGE_DIR")
    if not override:
        return None
    return Path(override).expanduser().resolve()


def open_private(path: Path, *, exclusive: bool = False) -> int:
    """Open `path` for writing, 0600 from the instant it is created.

    Creating with write_text() and chmod()-ing afterwards leaves the file
    world-readable for the duration of the write — long enough for a local
    watcher to read a freshly minted access token. O_NOFOLLOW additionally
    refuses to write through a symlink someone else planted at this path.
    """
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    return os.open(path, flags, _PRIVATE_FILE)


def write_private(path: Path, text: str) -> None:
    """Write text so the bytes are never on disk world-readable."""
    fd = open_private(path)
    try:
        os.fchmod(fd, _PRIVATE_FILE)  # tighten a file that predates this code
        os.write(fd, text.encode())
    finally:
        os.close(fd)


def ensure_private(path: Path) -> None:
    """Tighten an existing regular file to 0600; ignore anything else."""
    with suppress(OSError):
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_mode & 0o177:
            path.chmod(_PRIVATE_FILE)


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
        # 127.0.0.1, not "localhost": the callback listener binds the IPv4
        # loopback, but browsers routinely resolve "localhost" to ::1 first, so
        # another local account could bind [::1]:port and receive the
        # authorization code. RFC 8252 §8.3 requires the IP literal for this.
        return f"http://127.0.0.1:{self.redirect_port}/callback"

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


SETUP_INSTRUCTIONS = """\
To enable LinkedIn posting, create a (free) LinkedIn developer app once:
1. Go to https://www.linkedin.com/developers/apps and click "Create app".
2. In the app's Products tab, add "Share on LinkedIn" and
   "Sign In with LinkedIn using OpenID Connect".
3. In the Auth tab, add this Authorized redirect URL: http://127.0.0.1:{port}/callback
   (it must be 127.0.0.1, not localhost — the two are not interchangeable here).
4. Copy the Client ID and Client Secret from the Auth tab and export them where the
   MCP server runs, e.g. in the MCP client config:
     LINKEDIN_CLIENT_ID=...   LINKEDIN_CLIENT_SECRET=...
5. Restart the MCP server, then use the `login` tool (or run
   `linkedin-safe-mcp auth` in a terminal) to connect the LinkedIn account.
Job search tools work without any of this."""


def setup_instructions(settings: Settings) -> str:
    return SETUP_INSTRUCTIONS.format(port=settings.redirect_port)
