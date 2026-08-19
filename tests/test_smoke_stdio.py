"""End-to-end smoke test: spawn the real server over stdio and drive it with the
MCP client, exactly as Claude Code / Codex would."""

import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

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


def _server_params(tmp_path) -> StdioServerParameters:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LINKEDIN_")}
    env["LINKEDIN_MCP_DIR"] = str(tmp_path / "data")
    return StdioServerParameters(
        command=sys.executable, args=["-m", "linkedin_mcp.cli", "serve"], env=env
    )


async def test_stdio_roundtrip(tmp_path):
    async with (
        stdio_client(_server_params(tmp_path)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names >= EXPECTED_TOOLS, f"missing: {EXPECTED_TOOLS - names}"
        described = [t for t in tools.tools if t.description]
        assert len(described) == len(tools.tools), "every tool must have a description"

        # auth_status: works with no credentials and says what to do next
        result = await session.call_tool("auth_status", {})
        assert not result.is_error
        status = result.structured_content
        assert status["configured"] is False
        assert status["authenticated"] is False
        assert "developers/apps" in (status.get("setup_instructions") or "")

        # tracker roundtrip through the full protocol stack
        result = await session.call_tool("save_job", {"job": "4449049579"})
        assert not result.is_error
        listed = await session.call_tool("list_saved_jobs", {})
        assert listed.structured_content["total"] == 1

        # posting without auth -> clean, actionable tool error (not a crash)
        result = await session.call_tool("create_post", {"text": "hi"})
        assert result.is_error
        text = "".join(c.text for c in result.content if hasattr(c, "text"))
        assert "login" in text.lower()
