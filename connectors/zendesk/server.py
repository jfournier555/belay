"""Zendesk MCP server.

Exposes the Zendesk tools from tools.py as MCP tools, so any
MCP-capable client can call them (the harness in phase 2, or anything
else that speaks MCP).

Run it:
    python -m connectors.zendesk.server

It speaks MCP over stdio. Configure credentials with the same env vars as
the harness (ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN).
Without them it serves the same stub data as the in-process tools.

Requires: pip install -r requirements.txt  (mcp>=1.0,<2, httpx)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.config import Config

from .tools import get_ticket, list_tickets, search_tickets

mcp = FastMCP("zendesk")
_config = Config.from_env()


@mcp.tool()
def zendesk_list_tickets(organization: str, limit: int = 5) -> dict:
    """List open Zendesk tickets for an organization: subject, status,
    priority, age. Summary only, solved and closed excluded."""
    return list_tickets(_config, organization, limit=limit)


@mcp.tool()
def zendesk_search_tickets(query: str, limit: int = 5) -> dict:
    """Search Zendesk tickets by keyword. Returns short excerpts, not
    full tickets or comment threads."""
    return search_tickets(_config, query, limit=limit)


@mcp.tool()
def zendesk_get_ticket(ticket_id: int) -> dict:
    """Get one Zendesk ticket's summary: subject, status, priority,
    requester, key comment excerpts. Never the full comment thread."""
    return get_ticket(_config, ticket_id)


if __name__ == "__main__":
    mcp.run()
