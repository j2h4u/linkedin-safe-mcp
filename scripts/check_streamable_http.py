"""Exercise the real MCP Streamable HTTP initialize and tools/list requests."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from http.client import HTTPResponse
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _json_object(payload: bytes, *, content_type: str) -> dict[str, object]:
    if not payload:
        return {}
    text = payload.decode("utf-8")
    if "text/event-stream" in content_type:
        data_lines = [line[6:] for line in text.splitlines() if line.startswith("data:")]
        text = "\n".join(data_lines)
    value = cast(object, json.loads(text))
    if not isinstance(value, dict):
        raise RuntimeError(f"MCP response must be a JSON object, got {type(value).__name__}")
    return cast(dict[str, object], value)


def _post(
    url: str, request_id: int | None, method: str, params: Mapping[str, object] | None = None
) -> dict[str, object]:
    message: dict[str, object] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = dict(params)
    request = Request(
        url,
        data=json.dumps(message).encode("utf-8"),
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with cast(HTTPResponse, urlopen(request, timeout=10)) as response:
            return _json_object(response.read(), content_type=response.headers.get("Content-Type", ""))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"MCP {method} returned HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"could not reach MCP endpoint {url}: {exc.reason}") from exc


def main() -> int:
    url = os.environ.get("LINKEDIN_MCP_SMOKE_URL", "http://127.0.0.1:8000/mcp")
    initialized = _post(
        url,
        1,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "linkedin-safe-mcp-runtime-smoke", "version": "1.0"},
        },
    )
    result = initialized.get("result")
    if not isinstance(result, dict) or not result.get("serverInfo"):
        raise RuntimeError(f"MCP initialize response lacks serverInfo: {initialized}")

    # Stateless HTTP accepts notifications without a session and returns no JSON body.
    _post(url, None, "notifications/initialized")
    tools_response = _post(url, 2, "tools/list", {})
    tools_result = tools_response.get("result")
    if not isinstance(tools_result, dict):
        raise RuntimeError(f"MCP tools/list response lacks result: {tools_response}")
    tools = tools_result.get("tools")
    if not isinstance(tools, list) or not tools:
        raise RuntimeError(f"MCP tools/list returned no tools: {tools_response}")

    names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
    expected = {
        "auth_status",
        "login",
        "logout",
        "get_my_profile",
        "create_post",
        "delete_post",
        "comment_on_post",
        "like_post",
        "search_jobs",
        "get_job",
    }
    if names != expected:
        unexpected = sorted(str(name) for name in names ^ expected)
        raise RuntimeError(f"MCP tools/list returned unexpected tools: {unexpected}")
    print(f"streamable HTTP smoke passed: initialize and tools/list ({len(tools)} tools)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"streamable HTTP smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
