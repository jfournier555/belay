"""Salesforce MCP server.

Exposes the Salesforce tools from tools.py as MCP tools, so any
MCP-capable LLM can call them (Claude Desktop, the harness in phase 2,
anything else that speaks MCP).

Run it:
    python -m connectors.salesforce.server

It speaks MCP over stdio. Configure credentials with the same env vars as
the harness (SALESFORCE_INSTANCE_URL, SALESFORCE_ACCESS_TOKEN). Without
credentials it serves the same stub data as the in-process tools.

Requires: pip install -r requirements.txt  (mcp>=1.0,<2, httpx)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.config import Config

from .tools import get_account, list_cases, list_opportunities

mcp = FastMCP("salesforce")
_config = Config.from_env()


@mcp.tool()
def salesforce_get_account(account: str) -> dict:
    """Get an account summary from Salesforce: ARR, renewal date, health,
    owner. Takes account (name or id)."""
    return get_account(_config, account)


@mcp.tool()
def salesforce_list_opportunities(account: str) -> dict:
    """List open Salesforce opportunities for an account: stage, amount,
    close date, type. Takes account (name or id)."""
    return list_opportunities(_config, account)


@mcp.tool()
def salesforce_list_cases(account: str) -> dict:
    """List open Salesforce support cases for an account: subject, status,
    priority, age. Takes account (name or id)."""
    return list_cases(_config, account)


if __name__ == "__main__":
    mcp.run()
