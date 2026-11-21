"""Linear MCP server.

Exposes the Linear tools from tools.py as MCP tools, so any
MCP-capable client can call them (the harness in phase 2, or anything
else that speaks MCP).

Run it:
    python -m connectors.linear.server

It speaks MCP over stdio. Configure credentials with the same env var as
the harness (LINEAR_API_KEY). Without it it serves the same stub data
as the in-process tools.

Requires: pip install -r requirements.txt  (mcp>=1.0,<2, httpx)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.config import Config

from .tools import get_issue, list_issues, search_issues

mcp = FastMCP("linear")
_config = Config.from_env()


@mcp.tool()
def linear_list_issues(team: str | None = None, limit: int = 5) -> dict:
    """List open Linear issues, optionally for one team (team key, e.g.
    ENG). Summary only, completed and canceled excluded."""
    return list_issues(_config, team=team, limit=limit)


@mcp.tool()
def linear_search_issues(query: str, limit: int = 5) -> dict:
    """Search Linear issues by keyword. Returns short excerpts, not
    full issues or comment threads."""
    return search_issues(_config, query, limit=limit)


@mcp.tool()
def linear_get_issue(issue_id: str) -> dict:
    """Get one Linear issue's summary: title, status, priority,
    assignee, description excerpt. Never the full comment thread."""
    return get_issue(_config, issue_id)


if __name__ == "__main__":
    mcp.run()
