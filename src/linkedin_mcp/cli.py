"""Command-line entry point.

`linkedin-safe-mcp`         → run the MCP server over Streamable HTTP
`linkedin-safe-mcp auth`    → one-time browser OAuth flow
`linkedin-safe-mcp status`  → print auth status as JSON
`linkedin-safe-mcp logout`  → delete stored tokens
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import cast

from mcp.server.transport_security import TransportSecuritySettings

from . import __version__
from .config import DEFAULT_MCP_PORT


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="linkedin-safe-mcp",
        description="MCP server for LinkedIn: official-API posting and account-safe job search.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    default_host = os.environ.get("LINKEDIN_MCP_HOST", "127.0.0.1")
    default_port = int(os.environ.get("LINKEDIN_MCP_PORT", DEFAULT_MCP_PORT))
    parser.set_defaults(host=default_host, port=default_port)
    serve_parser = sub.add_parser("serve", help="Run the Streamable HTTP MCP server (default)")
    serve_parser.add_argument("--host", default=default_host)
    serve_parser.add_argument("--port", type=int, default=default_port)
    auth_parser = sub.add_parser("auth", help="Run the one-time LinkedIn OAuth flow")
    auth_parser.add_argument("--no-browser", action="store_true", help="Print the URL instead of opening a browser")
    sub.add_parser("status", help="Show authentication status as JSON")
    sub.add_parser("logout", help="Delete stored LinkedIn tokens")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    command = cast(str | None, args.command) or "serve"
    if command == "serve":
        from .server import mcp

        allowed_hosts = [
            value.strip()
            for value in os.environ.get("LINKEDIN_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*").split(",")
            if value.strip()
        ]
        allowed_origins = [
            value.strip()
            for value in os.environ.get(
                "LINKEDIN_MCP_ALLOWED_ORIGINS",
                "http://127.0.0.1:*,http://localhost:*",
            ).split(",")
            if value.strip()
        ]
        mcp.run(
            transport="streamable-http",
            host=cast(str, args.host),
            port=cast(int, args.port),
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=allowed_hosts,
                allowed_origins=allowed_origins,
            ),
        )
    elif command == "auth":
        _run_auth(open_browser=not cast(bool, args.no_browser))
    elif command == "status":
        from .server import build_auth_status

        sys.stdout.write(json.dumps(build_auth_status().model_dump(exclude_none=True), indent=2) + "\n")
    elif command == "logout":
        from .auth.oauth import TokenStore

        TokenStore().clear()
        sys.stdout.write("Stored LinkedIn tokens deleted.\n")


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
        sys.stdout.write("Waiting for LinkedIn authorization in the browser…\n")
        sys.stdout.write(f"If no window opened, visit:\n\n  {url}\n\n")
        flow.wait(timeout=300)
        profile = LinkedInClient(settings, store).userinfo(refresh=True)
        name = profile.get("name") or "LinkedIn user"
        sys.stdout.write(f"✓ Connected as {name}. Tokens saved to {store.path}\n")
    except LinkedInError as exc:
        sys.stderr.write(f"✗ {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
