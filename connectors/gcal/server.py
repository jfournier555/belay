"""Google Calendar MCP server.

Exposes the Google Calendar tools from tools.py as MCP tools, so any
MCP-capable client can call them (the harness in phase 2, or anything
else that speaks MCP).

Run it:
    python -m connectors.gcal.server

It speaks MCP over stdio. Configure credentials with the same env vars as
the harness (GCAL_CLIENT_ID, GCAL_CLIENT_SECRET, GCAL_REFRESH_TOKEN).
Without them it serves the same stub data as the in-process tools.

Read-only: the server exposes only the list and get tools. There is
no create, update, or delete tool.

Requires: pip install -r requirements.txt  (mcp>=1.0,<2, httpx)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.config import Config

from .tools import get_event, list_events

mcp = FastMCP("gcal")
_config = Config.from_env()


@mcp.tool()
def gcal_list_events(days: int = 7, limit: int = 10, account: str | None = None) -> dict:
    """List upcoming Google Calendar events: summary, start, end,
    attendees, link. Read-only. `account` filters to that account's
    events by name or attendee domain."""
    return list_events(_config, days=days, limit=limit, account=account)


@mcp.tool()
def gcal_get_event(event_id: str) -> dict:
    """Get one Google Calendar event by id: summary, start, end,
    attendees, link. Read-only."""
    return get_event(_config, event_id)


if __name__ == "__main__":
    mcp.run()
