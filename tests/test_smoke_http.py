"""Bounded end-to-end smoke test for the real Streamable HTTP server."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED_TOOLS = {
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
    "save_job",
    "get_saved_job",
    "list_saved_jobs",
    "update_job_status",
    "add_job_note",
    "remove_saved_job",
}
STARTUP_TIMEOUT = 10.0
REQUEST_TIMEOUT = 10.0
SHUTDOWN_TIMEOUT = 5.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        address = cast(tuple[str, int], sock.getsockname())
        port = address[1]
        if isinstance(port, int):
            return port
        raise RuntimeError("socket did not provide an integer port")


def _healthcheck(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=0.5):
            return True
    except OSError, urllib.error.URLError:
        return False


def _http_status(url: str, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT):
            return 200
    except urllib.error.HTTPError as exc:
        try:
            return exc.code
        finally:
            exc.close()


@pytest.fixture
async def running_server(tmp_path: Path) -> AsyncIterator[tuple[asyncio.subprocess.Process, str, str]]:
    port = _free_port()
    data_dir = tmp_path / "data"
    env = {key: value for key, value in os.environ.items() if not key.startswith("LINKEDIN_")}
    env["LINKEDIN_MCP_DIR"] = str(data_dir)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "linkedin_mcp.cli",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    health_url = f"http://127.0.0.1:{port}/health"
    mcp_url = f"http://127.0.0.1:{port}/mcp"
    try:
        await _wait_for_health(process, health_url)
        yield process, health_url, mcp_url
    finally:
        await _stop_process(process)


async def _wait_for_health(process: asyncio.subprocess.Process, url: str) -> None:
    deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT
    while asyncio.get_running_loop().time() < deadline:
        if process.returncode is not None:
            pytest.fail(f"MCP server exited before becoming ready (status {process.returncode})")
        if await asyncio.to_thread(_healthcheck, url):
            return
        await asyncio.sleep(0.05)
    pytest.fail(f"MCP server did not become ready within {STARTUP_TIMEOUT:g}s")


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=SHUTDOWN_TIMEOUT)
    except TimeoutError:
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=SHUTDOWN_TIMEOUT)


@pytest.mark.integration
async def test_streamable_http_initialize_and_list_tools(
    running_server: tuple[asyncio.subprocess.Process, str, str],
) -> None:
    _process, _health_url, mcp_url = running_server
    async with asyncio.timeout(REQUEST_TIMEOUT):
        async with streamable_http_client(mcp_url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()

    names = {tool.name for tool in tools.tools}
    assert names >= EXPECTED_TOOLS, f"missing: {EXPECTED_TOOLS - names}"
    assert all(tool.description for tool in tools.tools), "every tool must have a description"


async def test_invalid_host_is_rejected_by_real_http_server(
    running_server: tuple[asyncio.subprocess.Process, str, str],
) -> None:
    _process, _health_url, mcp_url = running_server
    status = await asyncio.to_thread(_http_status, mcp_url, {"Host": "attacker.example"})
    assert status == 421


async def test_invalid_origin_is_rejected_by_real_http_server(
    running_server: tuple[asyncio.subprocess.Process, str, str],
) -> None:
    _process, _health_url, mcp_url = running_server
    status = await asyncio.to_thread(_http_status, mcp_url, {"Origin": "https://attacker.example"})
    assert status == 403
