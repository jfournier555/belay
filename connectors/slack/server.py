"""Slack MCP server.

Exposes the Slack tools from tools.py as MCP tools, so any MCP-capable LLM
can call them (Claude Desktop, the harness in phase 2, anything else that
speaks MCP).

Run it:
    python -m connectors.slack.server

It speaks MCP over stdio. Configure credentials with the same env vars as
the harness (SLACK_BOT_TOKEN). Without a token it serves the same stub data
as the in-process tools.

Requires: pip install -r requirements.txt  (mcp>=1.0,<2, httpx)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.config import Config

from .tools import get_thread, list_channels, search_messages

mcp = FastMCP("slack")
_config = Config.from_env()


@mcp.tool()
def slack_search_messages(
    query: str, channel: str | None = None, limit: int = 5
) -> dict:
    """Search Slack messages for an account or topic. Returns short excerpts
    (author, channel, timestamp), not full threads."""
    return search_messages(_config, query, channel=channel, limit=limit)


@mcp.tool()
def slack_list_channels() -> dict:
    """List Slack channels the bot can read: id, name, member count."""
    return list_channels(_config)


@mcp.tool()
def slack_get_thread(channel: str, thread_ts: str) -> dict:
    """Summarize a Slack thread: participants, message count, key excerpts.
    Takes channel (id or name) and thread_ts."""
    return get_thread(_config, channel, thread_ts)


if __name__ == "__main__":
    mcp.run()
