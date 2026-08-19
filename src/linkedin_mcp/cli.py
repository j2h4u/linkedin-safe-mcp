"""Command-line entry point.

`linkedin-safe-mcp`         → run the MCP server on stdio (what MCP clients spawn)
`linkedin-safe-mcp auth`    → one-time browser OAuth flow
`linkedin-safe-mcp status`  → print auth status as JSON
`linkedin-safe-mcp logout`  → delete stored tokens
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="linkedin-safe-mcp",
        description="MCP server for LinkedIn: official-API posting, account-safe job "
        "search, and a local application tracker.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Run the MCP server on stdio (default)")
    auth_parser = sub.add_parser("auth", help="Run the one-time LinkedIn OAuth flow")
    auth_parser.add_argument(
        "--no-browser", action="store_true", help="Print the URL instead of opening a browser"
    )
    sub.add_parser("status", help="Show authentication status as JSON")
    sub.add_parser("logout", help="Delete stored LinkedIn tokens")
    args = parser.parse_args(argv)

    # stdout is the MCP protocol channel in serve mode — all logging goes to stderr.
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    command = args.command or "serve"
    if command == "serve":
        from .server import mcp

        mcp.run("stdio")
    elif command == "auth":
        _run_auth(open_browser=not args.no_browser)
    elif command == "status":
        from .server import build_auth_status

        print(json.dumps(build_auth_status().model_dump(exclude_none=True), indent=2))
    elif command == "logout":
        from .auth.oauth import TokenStore

        TokenStore().clear()
        print("Stored LinkedIn tokens deleted.")


def _run_auth(open_browser: bool) -> None:
    from .api.client import LinkedInClient
    from .auth.oauth import OAuthFlow, TokenStore
    from .config import Settings
    from .errors import LinkedInError

    settings = Settings.from_env()
    store = TokenStore()
    try:
        flow = OAuthFlow(settings, store)
        url = flow.start(open_browser=open_browser)
        print("Waiting for LinkedIn authorization in the browser…")
        print(f"If no window opened, visit:\n\n  {url}\n")
        flow.wait(timeout=300)
        profile = LinkedInClient(settings, store).userinfo(refresh=True)
        name = profile.get("name") or "LinkedIn user"
        print(f"✓ Connected as {name}. Tokens saved to {store.path}")
    except LinkedInError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
