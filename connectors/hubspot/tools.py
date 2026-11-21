"""HubSpot connector tools.

Tools exposed (both in-process and as MCP tools via server.py):
- hubspot_get_company: company summary (name, domain, revenue, industry,
  size, owner).
- hubspot_list_deals: open deals for the company (name, stage, amount,
  close date, type).
- hubspot_list_tickets: open support tickets for the company (subject,
  status, priority, age).

Two modes:
- Live: uses the HubSpot CRM API (v3) with a private app access token.
  Needs HUBSPOT_ACCESS_TOKEN. The API calls are real code, written
  against the documented HubSpot CRM API (POST /crm/v3/objects/companies
  /search, /deals/search, /tickets/search; GET /account-info/v3/details
  for the portal id), but they have NOT been tested against a live portal
  (no token was available while building v0.8.3). Treat the live path as
  "written, not verified" until someone runs it against a real portal.
- Stub: when no token is present, or HARNESS_STUB=1, returns realistic
  sample data for a fictional company. The returned dicts carry
  "_stub": true. The stub is what the demo and the tests use.

Summary-first: each tool returns only the fields an agent acts on (name,
revenue, stage, amount, status, priority, age). The full HubSpot record
is never returned, so the agent never sees a wall of fields it does not
need. Record ids and the portal id are always included, so the harness
can build deep links to the records.

HubSpot vocabulary, for the record: deals have a `dealtype` of
"newbusiness" or "existingbusiness" and a `dealstage` from the default
pipeline (mapped to readable labels below); tickets have an
`hs_ticket_status` of "1" New, "2" Waiting on contact, "3" Waiting on
us, "4" Closed in the default pipeline (mapped below). Ticket search
filters out status "4"; a portal with a custom ticket pipeline may need
that filter adjusted. Deal search filters `hs_is_closed` false.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from harness.config import Config
from harness.registry import Tool

_HUBSPOT_API = "https://api.hubapi.com"

# ---------------------------------------------------------------------------
# Stub data: a fictional company, for demo and tests. Clearly marked.
# HubSpot ids are numeric strings; the stub portal id is 0.
# ---------------------------------------------------------------------------

_STUB_COMPANY = {
    "_stub": True,
    "name": "Acme Corp",
    "id": "10000000001",
    "portal_id": "0",
    "domain": "acme.example",
    "revenue": 120000,
    "currency": "USD",
    "industry": "Software",
    "employees": 450,
    "owner": "Jane CSM",
}

_STUB_DEALS = {
    "_stub": True,
    "company": "Acme Corp",
    "portal_id": "0",
    "deals": [
        {
            "name": "Acme Corp - Renewal FY27",
            "id": "20000000001",
            "stage": "Decision maker bought in",
            "amount": 120000,
            "close_date": "2027-02-15",
            "type": "existingbusiness",
        },
        {
            "name": "Acme Corp - Expansion (50 seats)",
            "id": "20000000002",
            "stage": "Qualified to buy",
            "amount": 45000,
            "close_date": "2027-04-01",
            "type": "newbusiness",
        },
    ],
}

_STUB_TICKETS = {
    "_stub": True,
    "company": "Acme Corp",
    "portal_id": "0",
    "tickets": [
        {
            "id": "30000000001",
            "subject": "SSO login fails for new hires",
            "status": "Waiting on us",
            "priority": "High",
            "age_days": 6,
        },
        {
            "id": "30000000002",
            "subject": "Export timeout on large reports",
            "status": "New",
            "priority": "Medium",
            "age_days": 2,
        },
    ],
}

# Default-pipeline deal stages, mapped to readable labels. Unknown stages
# pass through as-is, so a custom pipeline still renders.
_DEAL_STAGE_LABELS = {
    "appointmentscheduled": "Appointment scheduled",
    "qualifiedtobuy": "Qualified to buy",
    "presentationscheduled": "Presentation scheduled",
    "decisionmakerboughtin": "Decision maker bought in",
    "contractssent": "Contract sent",
    "closedwon": "Closed won",
    "closedlost": "Closed lost",
}

# Default-pipeline ticket statuses ("1" New through "4" Closed). Unknown
# statuses pass through as-is.
_TICKET_STATUS_LABELS = {
    "1": "New",
    "2": "Waiting on contact",
    "3": "Waiting on us",
    "4": "Closed",
}

_MAX_ITEMS = 5


def _to_number(value):
    """A HubSpot numeric property (usually a string) as int/float, or None."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _age_days(created: str | None) -> int | None:
    """Days since an ISO timestamp, or None when it cannot be parsed."""
    if not created:
        return None
    try:
        moment = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc) - moment).days)


def _hubspot_request(config: Config, method: str, path: str, payload: dict | None = None) -> dict:
    """One call to the HubSpot API. Live path only."""
    headers = {
        "Authorization": "Bearer " + config.hubspot_access_token,
        "Content-Type": "application/json",
    }
    url = _HUBSPOT_API + path
    with httpx.Client(timeout=30.0) as client:
        if method == "POST":
            resp = client.post(url, json=payload or {}, headers=headers)
        else:
            resp = client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _portal_id(config: Config) -> str | None:
    """The portal (hub) id, for deep links. Env var first, then the
    account-info API. None when it cannot be determined: the harness
    builds no HubSpot links instead of guessing one."""
    if config.hubspot_portal_id:
        return config.hubspot_portal_id
    try:
        data = _hubspot_request(config, "GET", "/account-info/v3/details")
    except Exception:
        return None
    portal = data.get("portalId")
    return str(portal) if portal is not None else None


def get_company(config: Config, company: str) -> dict:
    """Company summary for `company` (name or id)."""
    if not config.hubspot_configured():
        result = dict(_STUB_COMPANY)
        result["requested"] = company
        return result
    data = _hubspot_request(
        config,
        "POST",
        "/crm/v3/objects/companies/search",
        {
            "query": company,
            "properties": [
                "name",
                "domain",
                "annualrevenue",
                "industry",
                "numberofemployees",
                "hubspot_owner_id",
            ],
            "limit": 1,
        },
    )
    results = data.get("results", [])
    if not results:
        return {"error": "No company matched %r." % company}
    rec = results[0]
    props = rec.get("properties", {})
    return {
        "name": props.get("name"),
        "id": rec.get("id"),
        "portal_id": _portal_id(config),
        "domain": props.get("domain"),
        "revenue": _to_number(props.get("annualrevenue")),
        "industry": props.get("industry"),
        "employees": _to_number(props.get("numberofemployees")),
        "owner": props.get("hubspot_owner_id"),
    }


def list_deals(config: Config, company: str) -> dict:
    """Open deals for `company` (name or id)."""
    if not config.hubspot_configured():
        result = dict(_STUB_DEALS)
        result["requested"] = company
        return result
    company_result = get_company(config, company)
    if "error" in company_result:
        return company_result
    company_id = company_result.get("id")
    data = _hubspot_request(
        config,
        "POST",
        "/crm/v3/objects/deals/search",
        {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "associations.company",
                            "operator": "EQ",
                            "value": company_id,
                        },
                        {
                            "propertyName": "hs_is_closed",
                            "operator": "EQ",
                            "value": "false",
                        },
                    ]
                }
            ],
            "properties": [
                "dealname",
                "dealstage",
                "amount",
                "closedate",
                "dealtype",
            ],
            "sorts": [{"propertyName": "closedate", "direction": "ASCENDING"}],
            "limit": _MAX_ITEMS,
        },
    )
    deals = [
        {
            "name": r.get("properties", {}).get("dealname"),
            "id": r.get("id"),
            "stage": _DEAL_STAGE_LABELS.get(
                r.get("properties", {}).get("dealstage", ""),
                r.get("properties", {}).get("dealstage"),
            ),
            "amount": _to_number(r.get("properties", {}).get("amount")),
            "close_date": r.get("properties", {}).get("closedate"),
            "type": r.get("properties", {}).get("dealtype"),
        }
        for r in data.get("results", [])
    ]
    return {
        "company": company,
        "portal_id": company_result.get("portal_id"),
        "deals": deals,
    }


def list_tickets(config: Config, company: str) -> dict:
    """Open support tickets for `company` (name or id)."""
    if not config.hubspot_configured():
        result = dict(_STUB_TICKETS)
        result["requested"] = company
        return result
    company_result = get_company(config, company)
    if "error" in company_result:
        return company_result
    company_id = company_result.get("id")
    data = _hubspot_request(
        config,
        "POST",
        "/crm/v3/objects/tickets/search",
        {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "associations.company",
                            "operator": "EQ",
                            "value": company_id,
                        },
                        {
                            # "4" is Closed in the default ticket pipeline;
                            # a portal with a custom pipeline may need this
                            # filter adjusted.
                            "propertyName": "hs_ticket_status",
                            "operator": "NEQ",
                            "value": "4",
                        },
                    ]
                }
            ],
            "properties": [
                "subject",
                "hs_ticket_priority",
                "hs_ticket_status",
                "createdate",
            ],
            "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
            "limit": _MAX_ITEMS,
        },
    )
    tickets = [
        {
            "id": r.get("id"),
            "subject": r.get("properties", {}).get("subject"),
            "status": _TICKET_STATUS_LABELS.get(
                str(r.get("properties", {}).get("hs_ticket_status", "")),
                r.get("properties", {}).get("hs_ticket_status"),
            ),
            "priority": str(
                r.get("properties", {}).get("hs_ticket_priority", "")
            ).title(),
            "age_days": _age_days(r.get("properties", {}).get("createdate")),
        }
        for r in data.get("results", [])
    ]
    return {
        "company": company,
        "portal_id": company_result.get("portal_id"),
        "tickets": tickets,
    }


def hubspot_tools(config: Config) -> list[Tool]:
    """The three HubSpot tools, bound to this config, for the registry."""
    return [
        Tool(
            name="hubspot_get_company",
            description=(
                "Get a company summary from HubSpot: revenue, industry, "
                "size, owner. Takes company (name or id)."
            ),
            parameters={
                "type": "object",
                "properties": {"company": {"type": "string"}},
                "required": ["company"],
            },
            handler=lambda company: get_company(config, company),
        ),
        Tool(
            name="hubspot_list_deals",
            description=(
                "List open HubSpot deals for a company: stage, amount, "
                "close date, type. Takes company (name or id)."
            ),
            parameters={
                "type": "object",
                "properties": {"company": {"type": "string"}},
                "required": ["company"],
            },
            handler=lambda company: list_deals(config, company),
        ),
        Tool(
            name="hubspot_list_tickets",
            description=(
                "List open HubSpot support tickets for a company: subject, "
                "status, priority, age. Takes company (name or id)."
            ),
            parameters={
                "type": "object",
                "properties": {"company": {"type": "string"}},
                "required": ["company"],
            },
            handler=lambda company: list_tickets(config, company),
        ),
    ]
