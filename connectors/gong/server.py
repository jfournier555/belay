"""Gong MCP server.

Exposes the Gong tools from tools.py as MCP tools, so any
MCP-capable client can call them (the harness in phase 2, or anything
else that speaks MCP).

Run it:
    python -m connectors.gong.server

It speaks MCP over stdio. Configure credentials with the same env vars as
the harness (GONG_ACCESS_TOKEN, GONG_API_BASE_URL). Without a token it
serves the same stub data as the in-process tools.

Requires: pip install -r requirements.txt  (mcp>=1.0,<2, httpx)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.config import Config

from .tools import get_call_summary, list_calls, search_transcripts

mcp = FastMCP("gong")
_config = Config.from_env()


@mcp.tool()
def gong_list_calls(account: str, days: int = 30) -> dict:
    """List recent Gong calls for an account: date, participants, duration,
    title. Summary only, no transcript."""
    return list_calls(_config, account, days=days)


@mcp.tool()
def gong_get_call_summary(call_id: str) -> dict:
    """Get Gong's generated summary for one call: brief, key points,
    topics, call outcome. Not the raw transcript."""
    return get_call_summary(_config, call_id)


@mcp.tool()
def gong_search_transcripts(query: str, limit: int = 5) -> dict:
    """Search Gong call transcripts for a keyword. Returns short excerpts
    with timestamps, not full transcripts."""
    return search_transcripts(_config, query, limit=limit)


if __name__ == "__main__":
    mcp.run()
