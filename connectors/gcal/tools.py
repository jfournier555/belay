"""Google Calendar connector tools (v1.16.0, the 10th connector).

Tools exposed (both in-process and as MCP tools via server.py):
- gcal_list_events: list upcoming events (summary, start, end,
  attendees, link). Summary only: no event body, no attachments.
- gcal_get_event: one event by id, same summary fields.

Read-only, by design and by scope: the connector only ever reads.
There is no create, update, or delete tool, and the OAuth scope is
the readonly one
(https://www.googleapis.com/auth/calendar.readonly).

Two modes:
- Live: uses the Calendar API (v3) with OAuth 2.0. The connector
  exchanges a refresh token for an access token (POST
  oauth2.googleapis.com/token), then calls
  www.googleapis.com/calendar/v3 with singleEvents and orderBy
  startTime. Needs GCAL_CLIENT_ID, GCAL_CLIENT_SECRET, and
  GCAL_REFRESH_TOKEN. The API calls are real code, written against
  the documented Calendar API (GET /calendar/v3/calendars/primary/
  events, /events/{id}), but they have NOT been tested against a
  live calendar (no credentials were available while building
  v1.16.0). Treat the live path as "written, not verified" until
  someone runs it with real credentials.
- Stub: when credentials are missing, or HARNESS_STUB=1, returns
  realistic sample data for a fictional account. The returned dicts
  carry "_stub": true. The stub is what the demo and the tests use.

Account matching: an event belongs to an account when the account
name (or its first word) appears in the summary, description, or
location, or when an attendee's email domain contains the
account's first word (admin@acme.example belongs to Acme Corp).
The briefer and scout pass the account they are working on, so
their drafts are grounded in that account's upcoming meetings, the
same way Gmail grounds them in that account's mail. The match is a
heuristic, said so in the result: an event with no match carries
"account": None instead of a guessed one.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx

from harness.config import Config
from harness.registry import Tool

_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

# ---------------------------------------------------------------------------
# Stub data: a fictional account, for demo and tests. Clearly marked.
# Event ids are short strings; the stub events are the Acme renewal
# prep and QBR planning meetings the other connectors already
# reference.
# ---------------------------------------------------------------------------

_STUB_EVENTS = [
    {
        "id": "evt-acme-renewal-prep",
        "summary": "Acme Corp renewal prep — SSO fix review",
        "start": "2026-11-24T14:00:00-08:00",
        "end": "2026-11-24T14:30:00-08:00",
        "attendees": [
            "Acme Admin <admin@acme.example>",
            "Jane CSM <jane@cs-team.example>",
        ],
        "description": (
            "Walk through the SSO fix before the Feb 15 renewal "
            "signature. Blocker for fifty more seats."
        ),
        "location": "",
        "html_link": (
            "https://calendar.google.invalid/calendar/u/0/r/eventedit/"
            "evt-acme-renewal-prep"
        ),
    },
    {
        "id": "evt-acme-qbr-plan",
        "summary": "Acme Corp QBR planning",
        "start": "2026-11-26T10:00:00-08:00",
        "end": "2026-11-26T11:00:00-08:00",
        "attendees": [
            "Acme Admin <admin@acme.example>",
            "Mike SE <mike@cs-team.example>",
        ],
        "description": "Plan the QBR: expansion, SSO fix, renewal date.",
        "location": "",
        "html_link": (
            "https://calendar.google.invalid/calendar/u/0/r/eventedit/"
            "evt-acme-qbr-plan"
        ),
    },
    {
        "id": "evt-team-standup",
        "summary": "CS team standup",
        "start": "2026-11-24T09:00:00-08:00",
        "end": "2026-11-24T09:30:00-08:00",
        "attendees": ["Jane CSM <jane@cs-team.example>"],
        "description": "Weekly standup, no customer on the invite.",
        "location": "",
        "html_link": (
            "https://calendar.google.invalid/calendar/u/0/r/eventedit/"
            "evt-team-standup"
        ),
    },
]

_MAX_DAYS = 30
_MAX_ITEMS = 20


def _first_word(account: str) -> str:
    """The account's first word, lowercased: "acme" for Acme Corp."""
    words = (account or "").strip().split()
    return words[0].lower() if words else ""


def _event_text(event: dict) -> str:
    """Everything matchable in an event, lowercased."""
    parts = [
        str(event.get("summary", "")),
        str(event.get("description", "")),
        str(event.get("location", "")),
    ]
    for attendee in event.get("attendees", []) or []:
        parts.append(str(attendee))
    return " ".join(parts).lower()


def account_for_event(event: dict, known_accounts: list[str]) -> str | None:
    """Which known account an event belongs to, or None.

    A match is the account name (or its first word) in the event
    text, or an attendee email domain containing the account's
    first word. No match means None, never a guess.
    """
    text = _event_text(event)
    for account in known_accounts:
        name = account.lower()
        slug = _first_word(account)
        if name and name in text:
            return account
        if slug and slug in text:
            return account
        for attendee in event.get("attendees", []) or []:
            match = re.search(r"@([\w.-]+)", str(attendee))
            if match and slug and slug in match.group(1).lower():
                return account
    return None


def match_events_to_account(
    events: list[dict], account: str
) -> list[dict]:
    """The events in `events` that belong to `account`, each tagged
    with its account. Events that match nothing are dropped: a
    brief for one account never cites another account's meeting."""
    matched = []
    for event in events:
        if account_for_event(event, [account]) == account:
            tagged = dict(event)
            tagged["account"] = account
            matched.append(tagged)
    return matched


def _gcal_access_token(config: Config) -> str:
    """Exchange the refresh token for an access token. Live path only."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            _OAUTH_TOKEN_URL,
            data={
                "client_id": config.gcal_client_id,
                "client_secret": config.gcal_client_secret,
                "refresh_token": config.gcal_refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
    return resp.json()["access_token"]


def _event_from_api(item: dict) -> dict:
    """A Calendar API event reduced to the summary fields."""
    start = item.get("start", {}) or {}
    end = item.get("end", {}) or {}
    attendees = [
        attendee.get("email", "")
        for attendee in item.get("attendees", []) or []
        if attendee.get("email")
    ]
    return {
        "id": item.get("id"),
        "summary": item.get("summary", ""),
        "start": start.get("dateTime") or start.get("date", ""),
        "end": end.get("dateTime") or end.get("date", ""),
        "attendees": attendees,
        "description": str(item.get("description", ""))[:500],
        "location": item.get("location", ""),
        "html_link": item.get("htmlLink", ""),
    }


def list_events(
    config: Config,
    days: int = 7,
    limit: int = 10,
    account: str | None = None,
) -> dict:
    """List upcoming events, soonest first.

    `account` filters to that account's events (matched by name or
    attendee domain); without it, every upcoming event is returned.
    Summary fields only: no body, no attachments, never a full
    description beyond a short excerpt.
    """
    days = min(max(days, 1), _MAX_DAYS)
    limit = min(max(limit, 1), _MAX_ITEMS)
    if not config.gcal_configured():
        events = [dict(event) for event in _STUB_EVENTS]
        events.sort(key=lambda event: event.get("start", ""))
        if account:
            events = match_events_to_account(events, account)
        return {
            "_stub": True,
            "days": days,
            "account": account,
            "events": events[:limit],
        }
    now = datetime.now(timezone.utc)
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            _CALENDAR_API + "/calendars/primary/events",
            params={
                "timeMin": now.isoformat(),
                "timeMax": (now + timedelta(days=days)).isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": limit,
            },
            headers={
                "Authorization": "Bearer " + _gcal_access_token(config)
            },
        )
        resp.raise_for_status()
        data = resp.json()
    events = [_event_from_api(item) for item in data.get("items", [])]
    if account:
        events = match_events_to_account(events, account)
    return {"days": days, "account": account, "events": events}


def get_event(config: Config, event_id: str) -> dict:
    """One event by id, same summary fields as the list."""
    if not config.gcal_configured():
        for event in _STUB_EVENTS:
            if event["id"] == event_id:
                return dict(event, _stub=True)
        return {"error": "No stub event with id %r." % event_id}
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            _CALENDAR_API + "/calendars/primary/events/%s" % event_id,
            headers={
                "Authorization": "Bearer " + _gcal_access_token(config)
            },
        )
        resp.raise_for_status()
        return _event_from_api(resp.json())


def gcal_tools(config: Config) -> list[Tool]:
    """The two Google Calendar tools, bound to this config, for the
    registry. Read-only: there is no create, update, or delete tool."""
    return [
        Tool(
            name="gcal_list_events",
            description=(
                "List upcoming Google Calendar events: summary, start, "
                "end, attendees, link. Read-only. Takes days (default "
                "7) and optional limit and account (filters to that "
                "account's events by name or attendee domain)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "days": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "account": {"type": "string"},
                },
            },
            handler=lambda days=7, limit=10, account=None: list_events(
                config, days=days, limit=limit, account=account
            ),
        ),
        Tool(
            name="gcal_get_event",
            description=(
                "Get one Google Calendar event by id: summary, start, "
                "end, attendees, link. Read-only. Takes event_id."
            ),
            parameters={
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
            },
            handler=lambda event_id: get_event(config, event_id),
        ),
    ]
