"""Zoom MCP server.

Exposes the Zoom tools from tools.py as MCP tools, so any
MCP-capable client can call them (the harness in phase 2, or anything
else that speaks MCP).

Run it:
    python -m connectors.zoom.server

It speaks MCP over stdio. Configure credentials with the same env vars as
the harness (ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET).
Without them it serves the same stub data as the in-process tools.

Requires: pip install -r requirements.txt  (mcp>=1.0,<2, httpx)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.config import Config

from .tools import get_recording_summary, list_recordings, search_transcripts

mcp = FastMCP("zoom")
_config = Config.from_env()


@mcp.tool()
def zoom_list_recordings(account: str, days: int = 30) -> dict:
    """List recent Zoom recordings for an account: date, duration, host,
    title. Summary only, no transcript."""
    return list_recordings(_config, account, days=days)


@mcp.tool()
def zoom_get_recording_summary(recording_id: str) -> dict:
    """Get Zoom's AI-generated summary for one recording: summary, topics,
    action items. Not the raw transcript."""
    return get_recording_summary(_config, recording_id)


@mcp.tool()
def zoom_search_transcripts(query: str, limit: int = 5) -> dict:
    """Search Zoom recording transcripts for a keyword. Returns short
    excerpts with timestamps, not full transcripts."""
    return search_transcripts(_config, query, limit=limit)


if __name__ == "__main__":
    mcp.run()
