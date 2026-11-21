"""Salesforce connector tools.

Tools exposed (both in-process and as MCP tools via server.py):
- salesforce_get_account: account summary (ARR, renewal date, health, owner).
- salesforce_list_opportunities: open opportunities for the account.
- salesforce_list_cases: open support cases for the account.
- salesforce_update_fields (v1.3.0, WRITE): field changes on an
  account or opportunity. Approval-gated: never called by an agent.
- salesforce_log_activity (v1.3.0, WRITE): activity log entry on a
  record. Approval-gated: never called by an agent.
- salesforce_move_stage (v1.3.0, WRITE): opportunity stage move.
  Approval-gated: never called by an agent.

Write tools (v1.3.0): the only caller is the approval-gated executor
in harness/writeback.py, which runs them after a human approves the
draft that carries them. An agent never calls a write tool while
gathering; the updater agent drafts the change, the draft sits in the
approval queue, and only an approved draft executes. In stub mode a
write tool simulates the write and reports exactly what would change,
without changing anything: the stub result carries "simulated": true
and "applied": false, so a demo or a test can never mistake a
simulation for a real change. Live writes use the Salesforce REST API
(PATCH/POST against /services/data/v59.0/sobjects/), written against
the documented API but NOT tested against a live org.

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
    "instance_url": "https://stub.salesforce.invalid",
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
    "instance_url": "https://stub.salesforce.invalid",
    "opportunities": [
        {
            "name": "Acme Corp - Renewal FY27",
            "id": "006STUB000000001",
            "stage": "Negotiation",
            "amount": 120000,
            "close_date": "2027-02-15",
            "type": "Renewal",
        },
        {
            "name": "Acme Corp - Expansion (50 seats)",
            "id": "006STUB000000002",
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
    "instance_url": "https://stub.salesforce.invalid",
    "cases": [
        {
            "number": "00001234",
            "id": "500STUB000000001",
            "subject": "SSO login fails for new hires",
            "status": "Working",
            "priority": "High",
            "age_days": 6,
        },
        {
            "number": "00001241",
            "id": "500STUB000000002",
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
        "instance_url": config.salesforce_instance_url,
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
            "id": r.get("Id"),
            "stage": r.get("StageName"),
            "amount": r.get("Amount"),
            "close_date": r.get("CloseDate"),
            "type": r.get("Type"),
        }
        for r in data.get("records", [])
    ]
    return {
        "account": account,
        "instance_url": config.salesforce_instance_url,
        "opportunities": opps,
    }


def list_cases(config: Config, account: str) -> dict:
    """Open support cases for `account` (name or id)."""
    if not config.salesforce_configured():
        result = dict(_STUB_CASES)
        result["requested"] = account
        return result
    soql = (
        "SELECT Id, CaseNumber, Subject, Status, Priority, CreatedDate "
        "FROM Case "
        "WHERE Account.Name LIKE '%{0}%' AND IsClosed = false "
        "ORDER BY CreatedDate DESC".format(account.replace("'", "\\'"))
    )
    data = _soql_query(config, soql)
    cases = [
        {
            "number": r.get("CaseNumber"),
            "id": r.get("Id"),
            "subject": r.get("Subject"),
            "status": r.get("Status"),
            "priority": r.get("Priority"),
            "created": r.get("CreatedDate"),
        }
        for r in data.get("records", [])
    ]
    return {
        "account": account,
        "instance_url": config.salesforce_instance_url,
        "cases": cases,
    }


def _sobjects_url(config: Config, path: str) -> str:
    """Base URL for a Salesforce sobjects REST call. Live path only."""
    return config.salesforce_instance_url.rstrip("/") + "/services/data/v59.0/sobjects/" + path


def _live_headers(config: Config) -> dict:
    return {
        "Authorization": "Bearer " + config.salesforce_access_token,
        "Content-Type": "application/json",
    }


def update_fields(config: Config, object: str, record_id: str, fields: dict) -> dict:
    """Change fields on one Salesforce record (Account, Opportunity, ...).

    Write tool (v1.3.0): call only through the approval-gated executor
    in harness/writeback.py, after a human approves the draft carrying
    the change. In stub mode this simulates the write: it reports what
    would change and changes nothing.
    """
    if not isinstance(fields, dict) or not fields:
        return {"error": "update_fields needs a non-empty fields dict."}
    if not config.salesforce_configured():
        return {
            "_stub": True,
            "simulated": True,
            "applied": False,
            "object": object,
            "record_id": record_id,
            "would_change": dict(fields),
        }
    url = _sobjects_url(config, "%s/%s" % (object, record_id))
    with httpx.Client(timeout=30.0) as client:
        resp = client.patch(url, json=fields, headers=_live_headers(config))
        resp.raise_for_status()
    return {
        "applied": True,
        "object": object,
        "record_id": record_id,
        "fields": dict(fields),
    }


def log_activity(
    config: Config,
    record_id: str,
    subject: str,
    description: str | None = None,
    activity_type: str = "Task",
) -> dict:
    """Log an activity entry on a Salesforce record (a Task, WhatId =
    the record). The entry is marked Completed with today's date.

    Write tool (v1.3.0): call only through the approval-gated executor
    in harness/writeback.py. In stub mode this simulates the log entry
    and creates nothing.
    """
    if not subject:
        return {"error": "log_activity needs a subject."}
    if not config.salesforce_configured():
        return {
            "_stub": True,
            "simulated": True,
            "applied": False,
            "record_id": record_id,
            "would_create": {
                "object": "Task",
                "subject": subject,
                "description": description,
                "activity_type": activity_type,
            },
        }
    from datetime import date

    body = {
        "Subject": subject,
        "WhatId": record_id,
        "Status": "Completed",
        "ActivityDate": date.today().isoformat(),
        "Type": activity_type,
    }
    if description:
        body["Description"] = description
    url = _sobjects_url(config, "Task")
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=body, headers=_live_headers(config))
        resp.raise_for_status()
        created = resp.json()
    return {
        "applied": True,
        "record_id": created.get("id"),
        "subject": subject,
        "related_to": record_id,
    }


def move_stage(config: Config, record_id: str, stage: str) -> dict:
    """Move an opportunity to a new stage (sets StageName).

    Write tool (v1.3.0): call only through the approval-gated executor
    in harness/writeback.py. In stub mode this simulates the move and
    changes nothing.
    """
    if not stage:
        return {"error": "move_stage needs a stage name."}
    if not config.salesforce_configured():
        return {
            "_stub": True,
            "simulated": True,
            "applied": False,
            "object": "Opportunity",
            "record_id": record_id,
            "would_change": {"StageName": stage},
        }
    url = _sobjects_url(config, "Opportunity/%s" % record_id)
    with httpx.Client(timeout=30.0) as client:
        resp = client.patch(
            url, json={"StageName": stage}, headers=_live_headers(config)
        )
        resp.raise_for_status()
    return {
        "applied": True,
        "object": "Opportunity",
        "record_id": record_id,
        "stage": stage,
    }


def salesforce_tools(config: Config) -> list[Tool]:
    """The Salesforce tools, bound to this config, for the registry.

    The first three are read tools. The last three are write tools
    (write=True): the registry lists them, but no agent calls them
    while gathering; only the approval-gated executor runs them."""
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
        Tool(
            name="salesforce_update_fields",
            description=(
                "WRITE TOOL (v1.3.0, approval-gated): change fields on one "
                "Salesforce record. Takes object (e.g. Opportunity), "
                "record_id, and fields (a dict of field names to new "
                "values). Never called by an agent; only the executor runs "
                "it after a human approves the draft carrying the change."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "object": {"type": "string"},
                    "record_id": {"type": "string"},
                    "fields": {"type": "object"},
                },
                "required": ["object", "record_id", "fields"],
            },
            handler=lambda object, record_id, fields: update_fields(
                config, object, record_id, fields
            ),
            write=True,
        ),
        Tool(
            name="salesforce_log_activity",
            description=(
                "WRITE TOOL (v1.3.0, approval-gated): log an activity entry "
                "on a Salesforce record. Takes record_id, subject, and "
                "optionally description and activity_type. Never called by "
                "an agent; only the executor runs it after a human approves "
                "the draft carrying the change."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                    "activity_type": {"type": "string"},
                },
                "required": ["record_id", "subject"],
            },
            handler=lambda record_id, subject, description=None, activity_type="Task": log_activity(
                config, record_id, subject, description, activity_type
            ),
            write=True,
        ),
        Tool(
            name="salesforce_move_stage",
            description=(
                "WRITE TOOL (v1.3.0, approval-gated): move an opportunity "
                "to a new stage. Takes record_id and stage. Never called "
                "by an agent; only the executor runs it after a human "
                "approves the draft carrying the change."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                    "stage": {"type": "string"},
                },
                "required": ["record_id", "stage"],
            },
            handler=lambda record_id, stage: move_stage(
                config, record_id, stage
            ),
            write=True,
        ),
    ]
