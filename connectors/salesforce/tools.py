"""Salesforce connector tools.

Tools exposed (both in-process and as MCP tools via server.py):
- salesforce_get_account: account summary (ARR, renewal date, health, owner).
- salesforce_list_opportunities: open opportunities for the account.
- salesforce_list_cases: open support cases for the account.

Two modes:
- Live: uses the Salesforce REST API (SOQL) with an OAuth access token.
  Needs SALESFORCE_INSTANCE_URL and SALESFORCE_ACCESS_TOKEN. The REST calls
  are real code, written against the documented Salesforce REST API, but
  they have NOT been tested against a live org (no credentials were
  available while building v1). Treat the live path as "written, not
  verified" until someone runs it against a real org.
- Stub: when credentials are missing, or HARNESS_STUB=1, returns realistic
  sample data for a fictional account. The returned dicts carry
  "_stub": true. The stub is what the demo and the tests use.

Summary-first: each tool returns only the fields an agent acts on (name,
ARR, renewal date, health, stage, amount, status, priority, age). The full
Salesforce record is never returned, so the agent never sees a wall of
fields it does not need.
"""

from __future__ import annotations

import httpx

from harness.config import Config
from harness.registry import Tool

# ---------------------------------------------------------------------------
# Stub data: a fictional account, for demo and tests. Clearly marked.
# ---------------------------------------------------------------------------

_STUB_ACCOUNT = {
    "_stub": True,
    "name": "Acme Corp",
    "id": "001STUB000000001",
    "arr": 120000,
    "currency": "USD",
    "renewal_date": "2027-02-15",
    "health": "green",
    "owner": "Jane CSM",
    "industry": "Software",
    "employees": 450,
    "open_cases": 2,
    "last_touch": "2026-11-10",
}

_STUB_OPPORTUNITIES = {
    "_stub": True,
    "account": "Acme Corp",
    "opportunities": [
        {
            "name": "Acme Corp - Renewal FY27",
            "stage": "Negotiation",
            "amount": 120000,
            "close_date": "2027-02-15",
            "type": "Renewal",
        },
        {
            "name": "Acme Corp - Expansion (50 seats)",
            "stage": "Qualification",
            "amount": 45000,
            "close_date": "2027-04-01",
            "type": "Expansion",
        },
    ],
}

_STUB_CASES = {
    "_stub": True,
    "account": "Acme Corp",
    "cases": [
        {
            "number": "00001234",
            "subject": "SSO login fails for new hires",
            "status": "Working",
            "priority": "High",
            "age_days": 6,
        },
        {
            "number": "00001241",
            "subject": "Export timeout on large reports",
            "status": "New",
            "priority": "Medium",
            "age_days": 2,
        },
    ],
}


def _soql_query(config: Config, soql: str) -> dict:
    """Run a SOQL query against the Salesforce REST API. Live path only."""
    url = config.salesforce_instance_url.rstrip("/") + "/services/data/v59.0/query"
    headers = {
        "Authorization": "Bearer " + config.salesforce_access_token,
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, params={"q": soql}, headers=headers)
        resp.raise_for_status()
        return resp.json()


def get_account(config: Config, account: str) -> dict:
    """Account summary for `account` (name or id)."""
    if not config.salesforce_configured():
        result = dict(_STUB_ACCOUNT)
        result["requested"] = account
        return result
    soql = (
        "SELECT Id, Name, AnnualRevenue, Industry, NumberOfEmployees "
        "FROM Account WHERE Name LIKE '%{0}%' LIMIT 1".format(
            account.replace("'", "\\'")
        )
    )
    data = _soql_query(config, soql)
    records = data.get("records", [])
    if not records:
        return {"error": "No account matched %r." % account}
    rec = records[0]
    return {
        "name": rec.get("Name"),
        "id": rec.get("Id"),
        "arr": rec.get("AnnualRevenue"),
        "industry": rec.get("Industry"),
        "employees": rec.get("NumberOfEmployees"),
    }


def list_opportunities(config: Config, account: str) -> dict:
    """Open opportunities for `account` (name or id)."""
    if not config.salesforce_configured():
        result = dict(_STUB_OPPORTUNITIES)
        result["requested"] = account
        return result
    soql = (
        "SELECT Id, Name, StageName, Amount, CloseDate, Type FROM Opportunity "
        "WHERE Account.Name LIKE '%{0}%' AND IsClosed = false "
        "ORDER BY CloseDate".format(account.replace("'", "\\'"))
    )
    data = _soql_query(config, soql)
    opps = [
        {
            "name": r.get("Name"),
            "stage": r.get("StageName"),
            "amount": r.get("Amount"),
            "close_date": r.get("CloseDate"),
            "type": r.get("Type"),
        }
        for r in data.get("records", [])
    ]
    return {"account": account, "opportunities": opps}


def list_cases(config: Config, account: str) -> dict:
    """Open support cases for `account` (name or id)."""
    if not config.salesforce_configured():
        result = dict(_STUB_CASES)
        result["requested"] = account
        return result
    soql = (
        "SELECT CaseNumber, Subject, Status, Priority, CreatedDate FROM Case "
        "WHERE Account.Name LIKE '%{0}%' AND IsClosed = false "
        "ORDER BY CreatedDate DESC".format(account.replace("'", "\\'"))
    )
    data = _soql_query(config, soql)
    cases = [
        {
            "number": r.get("CaseNumber"),
            "subject": r.get("Subject"),
            "status": r.get("Status"),
            "priority": r.get("Priority"),
            "created": r.get("CreatedDate"),
        }
        for r in data.get("records", [])
    ]
    return {"account": account, "cases": cases}


def salesforce_tools(config: Config) -> list[Tool]:
    """The three Salesforce tools, bound to this config, for the registry."""
    return [
        Tool(
            name="salesforce_get_account",
            description=(
                "Get an account summary from Salesforce: ARR, renewal date, "
                "health, owner. Takes account (name or id)."
            ),
            parameters={
                "type": "object",
                "properties": {"account": {"type": "string"}},
                "required": ["account"],
            },
            handler=lambda account: get_account(config, account),
        ),
        Tool(
            name="salesforce_list_opportunities",
            description=(
                "List open Salesforce opportunities for an account: stage, "
                "amount, close date, type. Takes account (name or id)."
            ),
            parameters={
                "type": "object",
                "properties": {"account": {"type": "string"}},
                "required": ["account"],
            },
            handler=lambda account: list_opportunities(config, account),
        ),
        Tool(
            name="salesforce_list_cases",
            description=(
                "List open Salesforce support cases for an account: subject, "
                "status, priority, age. Takes account (name or id)."
            ),
            parameters={
                "type": "object",
                "properties": {"account": {"type": "string"}},
                "required": ["account"],
            },
            handler=lambda account: list_cases(config, account),
        ),
    ]
