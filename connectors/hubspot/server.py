"""HubSpot MCP server.

Exposes the HubSpot tools from tools.py as MCP tools, so any
MCP-capable client can call them (the harness in phase 2, or anything
else that speaks MCP).

Run it:
    python -m connectors.hubspot.server

It speaks MCP over stdio. Configure credentials with the same env vars as
the harness (HUBSPOT_ACCESS_TOKEN, HUBSPOT_PORTAL_ID). Without
credentials it serves the same stub data as the in-process tools.

Requires: pip install -r requirements.txt  (mcp>=1.0,<2, httpx)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.config import Config

from .tools import get_company, list_deals, list_tickets

mcp = FastMCP("hubspot")
_config = Config.from_env()


@mcp.tool()
def hubspot_get_company(company: str) -> dict:
    """Get a company summary from HubSpot: revenue, industry, size,
    owner. Takes company (name or id)."""
    return get_company(_config, company)


@mcp.tool()
def hubspot_list_deals(company: str) -> dict:
    """List open HubSpot deals for a company: stage, amount, close date,
    type. Takes company (name or id)."""
    return list_deals(_config, company)


@mcp.tool()
def hubspot_list_tickets(company: str) -> dict:
    """List open HubSpot support tickets for a company: subject, status,
    priority, age. Takes company (name or id)."""
    return list_tickets(_config, company)


if __name__ == "__main__":
    mcp.run()
