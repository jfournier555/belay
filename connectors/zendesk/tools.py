"""Zendesk connector tools.

Tools exposed (both in-process and as MCP tools via server.py):
- zendesk_list_tickets: open tickets for an organization (subject,
  status, priority, age). Summary only.
- zendesk_search_tickets: search tickets by keyword. Returns short
  excerpts, capped at ~200 chars and 5 matches.
- zendesk_get_ticket: one ticket's summary (subject, status, priority,
  requester, key comment excerpts capped the same way, never the full
  comment thread).

Two modes:
- Live: uses the Zendesk API with token auth (subdomain, email, API
  token). The API calls are real code, written against the documented
  Zendesk API, but they have NOT been tested against a live instance
  (no credentials were available while building v0.8.6). Treat the
  live path as "written, not verified" until someone runs it with real
  credentials. Auth is HTTP Basic with username "{email}/token" and
  password "{api_token}".
- Stub: when credentials are missing, or HARNESS_STUB=1, returns
  realistic sample data for a fictional organization. The returned
  dicts carry "_stub": true. The stub is what the demo and the tests
  use.

Summary-first: list returns ticket metadata only; search returns short
excerpts (subject, first 200 chars of the description), capped at 5
matches; a single ticket returns the summary plus up to three comment
excerpts, never the full comment thread. Ticket ids are always
included, so the harness can build deep links to the tickets.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from harness.config import Config
from harness.registry import Tool

_ZENDESK_API = "https://{subdomain}.zendesk.com/api/v2"

# ---------------------------------------------------------------------------
# Stub data: a fictional organization, for demo and tests. Clearly marked.
# Ticket ids are small ints, the shape Zendesk uses. The tickets are the
# same Acme story the other connectors reference: SSO login failures,
# the export timeout, the 50-seat rollout.
# ---------------------------------------------------------------------------

_STUB_SUBDOMAIN = "acme-stub"

_STUB_TICKETS = {
    "_stub": True,
    "organization": "Acme Corp",
    "subdomain": _STUB_SUBDOMAIN,
    "tickets": [
        {
            "id": 101,
            "subject": "SSO login fails for new hires",
            "status": "open",
            "priority": "high",
            "requester": "acme-admin",
            "created": "2026-11-08T10:00:00Z",
            "age_days": 6,
            "description": (
                "SSO login fails for new hires again. Still waiting on "
                "the fix from support."
            ),
        },
        {
            "id": 102,
            "subject": "Export timeout on large reports",
            "status": "pending",
            "priority": "normal",
            "requester": "acme-ops-lead",
            "created": "2026-11-12T09:30:00Z",
            "age_days": 2,
            "description": (
                "Export times out on large reports. Blocks the 50-seat "
                "rollout."
            ),
        },
        {
            "id": 103,
            "subject": "Reporting dashboard slow for 50-seat rollout",
            "status": "open",
            "priority": "normal",
            "requester": "acme-ops-lead",
            "created": "2026-11-13T14:00:00Z",
            "age_days": 1,
            "description": (
                "Dashboard loads slowly now that fifty seats are live."
            ),
        },
    ],
}

_STUB_COMMENTS = {
    101: [
        {
            "author": "acme-admin",
            "created": "2026-11-08T10:05:00Z",
            "excerpt": (
                "SSO login fails for new hires again. We are still "
                "waiting on the fix from support."
            ),
        },
        {
            "author": "support-agent",
            "created": "2026-11-09T08:00:00Z",
            "excerpt": (
                "Fix ships next week. We will update this ticket when "
                "it lands."
            ),
        },
    ],
    102: [
        {
            "author": "acme-ops-lead",
            "created": "2026-11-12T09:35:00Z",
            "excerpt": (
                "Export times out on large reports. This blocks the "
                "50-seat rollout."
            ),
        },
    ],
    103: [
        {
            "author": "acme-ops-lead",
            "created": "2026-11-13T14:05:00Z",
            "excerpt": (
                "Dashboard is slow with fifty seats live. Not blocking, "
                "but worth a look."
            ),
        },
    ],
}

_EXCERPT_MAX_CHARS = 200
_MAX_ITEMS = 5
_MAX_COMMENTS = 3

# Ticket statuses that count as open: solved and closed are done, and
# the harness only acts on tickets still being worked.
_OPEN_STATUSES = {"new", "open", "pending", "hold"}


def _age_days(created_at: str | None) -> int | None:
    """Days from `created_at` (ISO 8601) to now, or None when unparsable."""
    if not created_at:
        return None
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - created).days, 0)


def _zendesk_get(config: Config, path: str, params: dict | None = None) -> dict:
    """GET a Zendesk API path with token auth. Live path only.

    Auth is HTTP Basic: username "{email}/token", password the API token.
    """
    url = _ZENDESK_API.format(subdomain=config.zendesk_subdomain) + path
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            url,
            params=params or {},
            auth=(config.zendesk_email + "/token", config.zendesk_api_token),
        )
        resp.raise_for_status()
        return resp.json()


def _find_organization_id(config: Config, organization: str) -> int | None:
    """The Zendesk organization id for `organization` (name), or None.

    Live path only. Zendesk has no exact-name lookup, so this searches
    organizations by name and takes the first match.
    """
    data = _zendesk_get(
        config,
        "/search.json",
        {"query": 'type:organization "%s"' % organization},
    )
    results = data.get("results", [])
    if not results:
        return None
    return results[0].get("id")


def _ticket_summary(ticket: dict) -> dict:
    """The fields an agent acts on, from one Zendesk ticket object."""
    return {
        "id": ticket.get("id"),
        "subject": ticket.get("subject"),
        "status": ticket.get("status"),
        "priority": ticket.get("priority"),
        "requester": ticket.get("requester_id"),
        "created": ticket.get("created_at"),
        "age_days": _age_days(ticket.get("created_at")),
    }


def list_tickets(
    config: Config, organization: str, limit: int = 5
) -> dict:
    """Open tickets for `organization` (name): subject, status, priority,
    age. Summary only. Solved and closed tickets are excluded: the
    harness only acts on tickets still being worked."""
    limit = min(max(limit, 1), 20)
    if not config.zendesk_configured():
        result = dict(_STUB_TICKETS)
        result["requested"] = organization
        result["tickets"] = list(result["tickets"])[:limit]
        return result
    org_id = _find_organization_id(config, organization)
    if org_id is None:
        return {"error": "No organization matched %r." % organization}
    data = _zendesk_get(
        config,
        "/organizations/%s/tickets.json" % org_id,
        {"sort_by": "created_at", "sort_order": "desc"},
    )
    tickets = [
        _ticket_summary(t)
        for t in data.get("tickets", [])
        if str(t.get("status", "")).lower() in _OPEN_STATUSES
    ][:limit]
    return {
        "organization": organization,
        "subdomain": config.zendesk_subdomain,
        "tickets": tickets,
    }


def search_tickets(
    config: Config, query: str, limit: int = 5
) -> dict:
    """Search tickets by keyword. Returns short excerpts (subject, first
    200 chars of the description), capped at 5 matches. Never a full
    comment thread."""
    limit = min(max(limit, 1), 20)
    if not config.zendesk_configured():
        needle = query.strip().lower()
        matches = [
            {
                "id": t["id"],
                "subject": t["subject"],
                "status": t["status"],
                "priority": t["priority"],
                "excerpt": t["description"][:_EXCERPT_MAX_CHARS],
                "created": t["created"],
            }
            for t in _STUB_TICKETS["tickets"]
            if needle in t["subject"].lower()
            or needle in t["description"].lower()
        ][:limit]
        return {
            "_stub": True,
            "query": query,
            "subdomain": _STUB_SUBDOMAIN,
            "matches": matches,
        }
    data = _zendesk_get(
        config, "/search.json", {"query": "type:ticket %s" % query}
    )
    matches = []
    for t in data.get("results", [])[:limit]:
        matches.append(
            {
                "id": t.get("id"),
                "subject": t.get("subject"),
                "status": t.get("status"),
                "priority": t.get("priority"),
                "excerpt": str(t.get("description", ""))[
                    :_EXCERPT_MAX_CHARS
                ],
                "created": t.get("created_at"),
            }
        )
    return {
        "query": query,
        "subdomain": config.zendesk_subdomain,
        "matches": matches,
    }


def get_ticket(config: Config, ticket_id: int) -> dict:
    """One ticket's summary: subject, status, priority, requester, and
    up to three comment excerpts, capped at ~200 chars each. Never the
    full comment thread."""
    if not config.zendesk_configured():
        ticket = next(
            (t for t in _STUB_TICKETS["tickets"] if t["id"] == ticket_id),
            None,
        )
        if ticket is None:
            return {"error": "No stub ticket with id %r." % ticket_id}
        return {
            "_stub": True,
            "id": ticket["id"],
            "subject": ticket["subject"],
            "status": ticket["status"],
            "priority": ticket["priority"],
            "requester": ticket["requester"],
            "created": ticket["created"],
            "subdomain": _STUB_SUBDOMAIN,
            "comments": list(_STUB_COMMENTS.get(ticket_id, [])),
        }
    data = _zendesk_get(config, "/tickets/%s.json" % ticket_id)
    ticket = data.get("ticket", {})
    comments_data = _zendesk_get(
        config, "/tickets/%s/comments.json" % ticket_id
    )
    comments = [
        {
            "author": c.get("author_id"),
            "created": c.get("created_at"),
            "excerpt": str(c.get("body", ""))[:_EXCERPT_MAX_CHARS],
        }
        for c in comments_data.get("comments", [])[:_MAX_COMMENTS]
    ]
    return {
        "id": ticket.get("id"),
        "subject": ticket.get("subject"),
        "status": ticket.get("status"),
        "priority": ticket.get("priority"),
        "requester": ticket.get("requester_id"),
        "created": ticket.get("created_at"),
        "subdomain": config.zendesk_subdomain,
        "comments": comments,
    }


def zendesk_tools(config: Config) -> list[Tool]:
    """The three Zendesk tools, bound to this config, for the registry."""
    return [
        Tool(
            name="zendesk_list_tickets",
            description=(
                "List open Zendesk tickets for an organization: subject, "
                "status, priority, age. Summary only, solved and closed "
                "excluded. Takes organization (name) and optional limit."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "organization": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["organization"],
            },
            handler=lambda organization, limit=5: list_tickets(
                config, organization, limit=limit
            ),
        ),
        Tool(
            name="zendesk_search_tickets",
            description=(
                "Search Zendesk tickets by keyword. Returns short "
                "excerpts, not full tickets or comment threads. Takes "
                "query and optional limit."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
            handler=lambda query, limit=5: search_tickets(
                config, query, limit=limit
            ),
        ),
        Tool(
            name="zendesk_get_ticket",
            description=(
                "Get one Zendesk ticket's summary: subject, status, "
                "priority, requester, key comment excerpts. Never the "
                "full comment thread. Takes ticket_id."
            ),
            parameters={
                "type": "object",
                "properties": {"ticket_id": {"type": "integer"}},
                "required": ["ticket_id"],
            },
            handler=lambda ticket_id: get_ticket(config, ticket_id),
        ),
    ]
