"""Gmail MCP server.

Exposes the Gmail tools from tools.py as MCP tools, so any
MCP-capable client can call them (the harness in phase 2, or anything
else that speaks MCP).

Run it:
    python -m connectors.gmail.server

It speaks MCP over stdio. Configure credentials with the same env vars as
the harness (GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN).
Without them it serves the same stub data as the in-process tools.

Requires: pip install -r requirements.txt  (mcp>=1.0,<2, httpx)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.config import Config

from .tools import get_thread, search_messages

mcp = FastMCP("gmail")
_config = Config.from_env()


@mcp.tool()
def gmail_search_messages(query: str, limit: int = 5) -> dict:
    """Search Gmail for an account or topic. Returns short excerpts
    (author, subject, date, first ~200 chars), not full message bodies."""
    return search_messages(_config, query, limit=limit)


@mcp.tool()
def gmail_get_thread(thread_id: str) -> dict:
    """Summarize a Gmail thread: participants, message count, key
    excerpts."""
    return get_thread(_config, thread_id)


if __name__ == "__main__":
    mcp.run()
