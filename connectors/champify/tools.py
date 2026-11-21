"""Champify connector tools.

Tools exposed (both in-process and as MCP tools via server.py):
- champify_list_champions: champions tracked for an account (name,
  role, company, status). Summary only.
- champify_list_job_changes: recent job changes among an account's
  champions (who moved, from where to where, when). Summary only.
- champify_get_champion: one champion's detail (current role and
  company, job history, contact status). Summary only.

Two modes:
- Live: uses the Champify API with an API key (CHAMPIFY_API_KEY).
  Auth is the raw key as a Bearer token. The API calls are real code,
  but they have NOT been tested against a live account (no key was
  available while building v0.8.8). Treat the live path as "written,
  not verified" until someone runs it with a real key. One honest
  caveat, stronger than the other connectors: Champify publishes no
  public API reference, so the endpoint shapes below
  (GET /v1/champions, GET /v1/champions/{id}, GET /v1/job-changes)
  are assumed from the product's own data model (champions with job
  histories), not from documentation. They must be corrected against
  the real API the first time a key is available. The parser is
  deliberately tolerant (it accepts alternate field names) so a small
  shape difference does not break the tool.
- Stub: when no key is present, or HARNESS_STUB=1, returns realistic
  sample data for a fictional account. The returned dicts carry
  "_stub": true. The stub is what the demo and the tests use.

Summary-first: list calls return champion metadata only; a single
champion returns a short job history, never a full profile or contact
record. Champion ids are always included, so the harness can build
deep links to the champions.
"""

from __future__ import annotations

import httpx

from harness.config import Config
from harness.registry import Tool

_CHAMPIFY_API = "https://api.champify.io/v1"

# ---------------------------------------------------------------------------
# Stub data: a fictional account, for demo and tests. Clearly marked.
# Champion ids are short "chp_" strings, the shape the stub uses.
# ---------------------------------------------------------------------------

_STUB_CHAMPIONS = {
    "_stub": True,
    "account": "Acme Corp",
    "champions": [
        {
            "id": "chp_7f3a91",
            "name": "Dana Reyes",
            "role": "VP Operations",
            "company": "Acme Corp",
            "status": "active",
            "champion_since": "2025-06-01",
        },
        {
            "id": "chp_7f3a92",
            "name": "Marcus Webb",
            "role": "Director of IT",
            "company": "Acme Corp",
            "status": "active",
            "champion_since": "2025-09-15",
        },
        {
            "id": "chp_7f3a93",
            "name": "Priya Nair",
            "role": "Head of Support",
            "company": "Acme Corp",
            "status": "moved",
            "champion_since": "2025-03-10",
        },
        {
            "id": "chp_7f3a94",
            "name": "Tom Okafor",
            "role": "Senior Engineer",
            "company": "Acme Corp",
            "status": "moved",
            "champion_since": "2024-11-20",
        },
    ],
}

_STUB_JOB_CHANGES = {
    "_stub": True,
    "account": "Acme Corp",
    "job_changes": [
        {
            "champion_id": "chp_7f3a93",
            "name": "Priya Nair",
            "previous_company": "Acme Corp",
            "previous_role": "Head of Support",
            "new_company": "Globex Inc",
            "new_role": "VP Customer Experience",
            "changed_at": "2026-11-02",
        },
        {
            "champion_id": "chp_7f3a91",
            "name": "Dana Reyes",
            "previous_company": "Acme Corp",
            "previous_role": "Operations Manager",
            "new_company": "Acme Corp",
            "new_role": "VP Operations",
            "changed_at": "2026-10-15",
        },
        {
            "champion_id": "chp_7f3a94",
            "name": "Tom Okafor",
            "previous_company": "Acme Corp",
            "previous_role": "Senior Engineer",
            "new_company": "Initech",
            "new_role": "Director of Operations",
            "changed_at": "2026-09-20",
        },
    ],
}

_STUB_CHAMPION_DETAIL = {
    "chp_7f3a91": {
        "_stub": True,
        "id": "chp_7f3a91",
        "name": "Dana Reyes",
        "role": "VP Operations",
        "company": "Acme Corp",
        "status": "active",
        "contact_status": "reachable",
        "job_history": [
            {
                "company": "Acme Corp",
                "role": "Operations Manager",
                "started": "2024-02-01",
                "ended": "2026-10-14",
            },
            {
                "company": "Acme Corp",
                "role": "VP Operations",
                "started": "2026-10-15",
                "ended": None,
            },
        ],
    },
    "chp_7f3a92": {
        "_stub": True,
        "id": "chp_7f3a92",
        "name": "Marcus Webb",
        "role": "Director of IT",
        "company": "Acme Corp",
        "status": "active",
        "contact_status": "reachable",
        "job_history": [
            {
                "company": "Acme Corp",
                "role": "Director of IT",
                "started": "2025-09-15",
                "ended": None,
            },
        ],
    },
    "chp_7f3a93": {
        "_stub": True,
        "id": "chp_7f3a93",
        "name": "Priya Nair",
        "role": "VP Customer Experience",
        "company": "Globex Inc",
        "status": "moved",
        "contact_status": "reachable",
        "job_history": [
            {
                "company": "Acme Corp",
                "role": "Head of Support",
                "started": "2025-03-10",
                "ended": "2026-11-01",
            },
            {
                "company": "Globex Inc",
                "role": "VP Customer Experience",
                "started": "2026-11-02",
                "ended": None,
            },
        ],
    },
    "chp_7f3a94": {
        "_stub": True,
        "id": "chp_7f3a94",
        "name": "Tom Okafor",
        "role": "Director of Operations",
        "company": "Initech",
        "status": "moved",
        "contact_status": "reachable",
        "job_history": [
            {
                "company": "Acme Corp",
                "role": "Senior Engineer",
                "started": "2024-11-20",
                "ended": "2026-09-19",
            },
            {
                "company": "Initech",
                "role": "Director of Operations",
                "started": "2026-09-20",
                "ended": None,
            },
        ],
    },
}

_MAX_ITEMS = 5


def _champify_get(config: Config, path: str, params: dict | None = None) -> dict:
    """GET from the Champify API. Live path only."""
    headers = {
        "Authorization": "Bearer " + config.champify_api_key,
        "Content-Type": "application/json",
    }
    url = config.champify_api_base_url.rstrip("/") + path
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, params=params or {}, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _norm_champion(rec: dict) -> dict:
    """One champion record, tolerating alternate field names.

    The endpoint shapes are assumed (see the module docstring), so the
    parser accepts the obvious variants instead of failing on one.
    """
    return {
        "id": rec.get("id") or rec.get("champion_id"),
        "name": rec.get("name"),
        "role": rec.get("role") or rec.get("title"),
        "company": rec.get("company") or rec.get("company_name"),
        "status": rec.get("status"),
        "champion_since": rec.get("champion_since")
        or rec.get("tracked_since"),
    }


def _norm_change(rec: dict) -> dict:
    """One job-change record, tolerating alternate field names."""
    return {
        "champion_id": rec.get("champion_id") or rec.get("id"),
        "name": rec.get("name") or rec.get("champion_name"),
        "previous_company": rec.get("previous_company")
        or rec.get("from_company"),
        "previous_role": rec.get("previous_role") or rec.get("from_role"),
        "new_company": rec.get("new_company") or rec.get("to_company"),
        "new_role": rec.get("new_role") or rec.get("to_role"),
        "changed_at": rec.get("changed_at") or rec.get("date"),
    }


def _match_account(account: str, company: str | None) -> bool:
    """True when a record looks like it belongs to `account`."""
    needle = account.strip().lower()
    if not needle:
        return True
    return needle in (company or "").lower()


def list_champions(config: Config, account: str) -> dict:
    """Champions tracked for `account` (name): name, role, company,
    status. Summary only, no full profile or contact record."""
    if not config.champify_configured():
        result = dict(_STUB_CHAMPIONS)
        result["requested"] = account
        return result
    data = _champify_get(
        config, "/champions", params={"company": account}
    )
    records = data.get("champions", []) if isinstance(data, dict) else data
    champions = [
        _norm_champion(rec)
        for rec in records
        if _match_account(account, rec.get("company") or rec.get("company_name"))
    ]
    return {"account": account, "champions": champions[:_MAX_ITEMS]}


def list_job_changes(
    config: Config, account: str, days: int = 90
) -> dict:
    """Recent job changes among `account`'s champions: who moved, from
    where to where, when. Summary only. A champion who left the account
    is the departure signal; a champion who moved to a new company is
    also a new pipeline signal, not just a loss."""
    if not config.champify_configured():
        result = dict(_STUB_JOB_CHANGES)
        result["requested"] = account
        return result
    data = _champify_get(
        config,
        "/job-changes",
        params={"company": account, "days": days},
    )
    records = (
        data.get("job_changes", []) if isinstance(data, dict) else data
    )
    changes = [
        _norm_change(rec)
        for rec in records
        if _match_account(
            account,
            rec.get("previous_company") or rec.get("from_company"),
        )
        or _match_account(
            account, rec.get("new_company") or rec.get("to_company")
        )
    ]
    return {"account": account, "job_changes": changes[:_MAX_ITEMS]}


def get_champion(config: Config, champion_id: str) -> dict:
    """One champion's detail: current role and company, job history,
    contact status. Short job history only, never a full profile."""
    if not config.champify_configured():
        detail = _STUB_CHAMPION_DETAIL.get(champion_id)
        if detail is None:
            return {"error": "No stub champion with id %r." % champion_id}
        return dict(detail)
    data = _champify_get(config, "/champions/%s" % champion_id)
    rec = data.get("champion", data) if isinstance(data, dict) else {}
    detail = _norm_champion(rec if isinstance(rec, dict) else {})
    history = []
    for entry in (rec.get("job_history") or []) if isinstance(rec, dict) else []:
        history.append(
            {
                "company": entry.get("company") or entry.get("company_name"),
                "role": entry.get("role") or entry.get("title"),
                "started": entry.get("started") or entry.get("start_date"),
                "ended": entry.get("ended") or entry.get("end_date"),
            }
        )
    detail["contact_status"] = (
        rec.get("contact_status") if isinstance(rec, dict) else None
    )
    detail["job_history"] = history
    return detail


def champify_tools(config: Config) -> list[Tool]:
    """The three Champify tools, bound to this config, for the registry."""
    return [
        Tool(
            name="champify_list_champions",
            description=(
                "List champions tracked for an account: name, role, "
                "company, status. Summary only. Takes account (name)."
            ),
            parameters={
                "type": "object",
                "properties": {"account": {"type": "string"}},
                "required": ["account"],
            },
            handler=lambda account: list_champions(config, account),
        ),
        Tool(
            name="champify_list_job_changes",
            description=(
                "List recent job changes among an account's champions: "
                "who moved, from where to where, when. Takes account "
                "(name) and optional days."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "account": {"type": "string"},
                    "days": {"type": "integer"},
                },
                "required": ["account"],
            },
            handler=lambda account, days=90: list_job_changes(
                config, account, days=days
            ),
        ),
        Tool(
            name="champify_get_champion",
            description=(
                "Get one champion's detail: current role and company, "
                "job history, contact status. Takes champion_id."
            ),
            parameters={
                "type": "object",
                "properties": {"champion_id": {"type": "string"}},
                "required": ["champion_id"],
            },
            handler=lambda champion_id: get_champion(config, champion_id),
        ),
    ]
