"""Champify MCP server.

Exposes the Champify tools from tools.py as MCP tools, so any
MCP-capable client can call them (the harness in phase 2, or anything
else that speaks MCP).

Run it:
    python -m connectors.champify.server

It speaks MCP over stdio. Configure credentials with the same env vars as
the harness (CHAMPIFY_API_KEY, CHAMPIFY_API_BASE_URL). Without a key it
serves the same stub data as the in-process tools.

Requires: pip install -r requirements.txt  (mcp>=1.0,<2, httpx)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.config import Config

from .tools import get_champion, list_champions, list_job_changes

mcp = FastMCP("champify")
_config = Config.from_env()


@mcp.tool()
def champify_list_champions(account: str) -> dict:
    """List champions tracked for an account: name, role, company,
    status. Summary only."""
    return list_champions(_config, account)


@mcp.tool()
def champify_list_job_changes(account: str, days: int = 90) -> dict:
    """List recent job changes among an account's champions: who moved,
    from where to where, when."""
    return list_job_changes(_config, account, days=days)


@mcp.tool()
def champify_get_champion(champion_id: str) -> dict:
    """Get one champion's detail: current role and company, job history,
    contact status."""
    return get_champion(_config, champion_id)


if __name__ == "__main__":
    mcp.run()
