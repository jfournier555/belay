"""Salesforce MCP server.

Exposes the Salesforce tools from tools.py as MCP tools, so any
MCP-capable client can call them (the harness in phase 2, or anything
else that speaks MCP).

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

from .tools import (
    get_account,
    list_cases,
    list_opportunities,
    log_activity,
    move_stage,
    update_fields,
)

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


@mcp.tool()
def salesforce_update_fields(object: str, record_id: str, fields: dict) -> dict:
    """WRITE TOOL (v1.3.0, approval-gated): change fields on one
    Salesforce record. Takes object (e.g. Opportunity), record_id, and
    fields (a dict of field names to new values). In the harness this
    runs only after a human approves the draft carrying the change;
    called directly here it applies immediately, so use it only when
    that is what you mean. Without credentials it simulates the write
    and changes nothing."""
    return update_fields(_config, object, record_id, fields)


@mcp.tool()
def salesforce_log_activity(
    record_id: str, subject: str, description: str | None = None
) -> dict:
    """WRITE TOOL (v1.3.0, approval-gated): log an activity entry on a
    Salesforce record. Takes record_id and subject, optionally
    description. In the harness this runs only after a human approves
    the draft carrying the change; called directly here it applies
    immediately, so use it only when that is what you mean. Without
    credentials it simulates the log entry and creates nothing."""
    return log_activity(_config, record_id, subject, description)


@mcp.tool()
def salesforce_move_stage(record_id: str, stage: str) -> dict:
    """WRITE TOOL (v1.3.0, approval-gated): move an opportunity to a new
    stage. Takes record_id and stage. In the harness this runs only
    after a human approves the draft carrying the change; called
    directly here it applies immediately, so use it only when that is
    what you mean. Without credentials it simulates the move and
    changes nothing."""
    return move_stage(_config, record_id, stage)


if __name__ == "__main__":
    mcp.run()
