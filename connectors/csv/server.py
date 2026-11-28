"""CSV accounts MCP server.

Exposes the CSV accounts tools from tools.py as MCP tools, so any
MCP-capable client can call them (the harness in phase 2, or anything
else that speaks MCP).

Run it:
    python -m connectors.csv.server

It speaks MCP over stdio. Configure the file with the same env var as
the harness (HARNESS_CSV_ACCOUNTS_FILE). Without it the tools report
the named gap, the same as the in-process tools.

Read-only: the server exposes only the list and get tools. There is
no create, update, or delete tool, and nothing here ever writes to
the file.

Requires: pip install -r requirements.txt  (mcp>=1.0,<2, httpx)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.config import Config

from .tools import get_account, list_accounts, list_cases, list_opportunities

mcp = FastMCP("csv")
_config = Config.from_env()


@mcp.tool()
def csv_list_accounts() -> dict:
    """List every account in the CSV accounts file: name, domain, ARR,
    renewal date, health, owner. Soonest renewal first. Read-only."""
    return list_accounts(_config)


@mcp.tool()
def csv_get_account(account: str) -> dict:
    """Get one account record from the CSV accounts file by name (or
    domain): ARR, renewal date, health, owner, contacts, notes.
    Read-only."""
    return get_account(_config, account)


@mcp.tool()
def csv_list_opportunities(account: str) -> dict:
    """List the account's renewal opportunity from the CSV accounts
    file: stage, amount, close date, type. Read-only."""
    return list_opportunities(_config, account)


@mcp.tool()
def csv_list_cases(account: str) -> dict:
    """List the account's support cases from the CSV accounts file:
    subject, status, priority, age. Read-only."""
    return list_cases(_config, account)


if __name__ == "__main__":
    mcp.run()
