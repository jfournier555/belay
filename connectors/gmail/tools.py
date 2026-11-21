"""Gmail connector tools.

Tools exposed (both in-process and as MCP tools via server.py):
- gmail_search_messages: search messages for an account or topic.
  Returns short excerpts (author, subject, date, first ~200 chars),
  max 5 matches, never a full message body.
- gmail_get_thread: thread summary (participants, message count, key
  excerpts), capped the same way.

Two modes:
- Live: uses the Gmail API (v1) with OAuth 2.0. Gmail does not accept
  a simple API key for reading mail: the connector exchanges a refresh
  token for an access token (POST oauth2.googleapis.com/token), then
  calls gmail.googleapis.com with format=metadata, which returns
  headers and Google's own snippet, never a body. Needs
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN. The
  API calls are real code, written against the documented Gmail API
  (GET /gmail/v1/users/me/messages, /messages/{id}, /threads/{id}),
  but they have NOT been tested against a live mailbox (no credentials
  were available while building v0.8.4). Treat the live path as
  "written, not verified" until someone runs it with real credentials.
- Stub: when credentials are missing, or HARNESS_STUB=1, returns
  realistic sample data for a fictional account. The returned dicts
  carry "_stub": true. The stub is what the demo and the tests use.

Summary-first: search returns Google's snippet (truncated to ~200
chars) plus headers only; a thread summary returns participants,
message count, and three short excerpts. A full message body is never
fetched, so it can never reach a prompt. Thread ids are always
included, so the harness can build deep links to the threads.
"""

from __future__ import annotations

import httpx

from harness.config import Config
from harness.registry import Tool

_GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

# ---------------------------------------------------------------------------
# Stub data: a fictional account, for demo and tests. Clearly marked.
# Gmail ids are hex strings; the stub threads are the Acme renewal and
# SSO threads the other connectors already reference.
# ---------------------------------------------------------------------------

_STUB_MESSAGES = {
    "_stub": True,
    "query": "Acme Corp",
    "matches": [
        {
            "id": "18d01a2b3c4d5e60",
            "thread_id": "18c8f5a2b3c4d5e6",
            "author": "Acme Admin <admin@acme.example>",
            "subject": "Re: Acme Corp renewal Feb 15 - SSO still blocking new hires",
            "date": "2026-11-14T15:02:00Z",
            "excerpt": (
                "SSO login fails for new hires again. We are still "
                "waiting on the fix from support before we sign the "
                "renewal."
            ),
        },
        {
            "id": "18d01a2b3c4d5e61",
            "thread_id": "18c8f5a2b3c4d5e6",
            "author": "Jane CSM <jane@cs-team.example>",
            "subject": "Re: Acme Corp renewal Feb 15 - SSO still blocking new hires",
            "date": "2026-11-13T10:41:00Z",
            "excerpt": (
                "Support says the fix ships next week. Once it lands, "
                "the admin wants to talk about fifty more seats."
            ),
        },
        {
            "id": "18d01a2b3c4d5e62",
            "thread_id": "18c8f5a2b3c4d5e7",
            "author": "Mike SE <mike@cs-team.example>",
            "subject": "Export timeout - Acme",
            "date": "2026-11-12T09:18:00Z",
            "excerpt": (
                "Demoed the new reporting to Acme Corp's ops lead. The "
                "export timeout is the one thing still blocking the "
                "expansion."
            ),
        },
    ],
}

_STUB_THREADS = {
    "18c8f5a2b3c4d5e6": {
        "_stub": True,
        "thread_id": "18c8f5a2b3c4d5e6",
        "subject": "Re: Acme Corp renewal Feb 15 - SSO still blocking new hires",
        "participants": [
            "Acme Admin <admin@acme.example>",
            "Jane CSM <jane@cs-team.example>",
        ],
        "message_count": 2,
        "excerpts": [
            "Acme Admin: SSO login fails for new hires again. We are "
            "still waiting on the fix from support before we sign the "
            "renewal.",
            "Jane CSM: Support says the fix ships next week. Once it "
            "lands, the admin wants to talk about fifty more seats.",
        ],
    },
    "18c8f5a2b3c4d5e7": {
        "_stub": True,
        "thread_id": "18c8f5a2b3c4d5e7",
        "subject": "Export timeout - Acme",
        "participants": ["Mike SE <mike@cs-team.example>"],
        "message_count": 1,
        "excerpts": [
            "Mike SE: Demoed the new reporting to Acme Corp's ops lead. The "
            "export timeout is the one thing still blocking the "
            "expansion.",
        ],
    },
}

_EXCERPT_MAX_CHARS = 200
_MAX_ITEMS = 5


def _gmail_access_token(config: Config) -> str:
    """Exchange the refresh token for an access token. Live path only."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            _OAUTH_TOKEN_URL,
            data={
                "client_id": config.gmail_client_id,
                "client_secret": config.gmail_client_secret,
                "refresh_token": config.gmail_refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
    return resp.json()["access_token"]


def _gmail_get(config: Config, path: str, params: dict | None = None) -> dict:
    """GET from the Gmail API. Live path only."""
    headers = {
        "Authorization": "Bearer " + _gmail_access_token(config),
    }
    url = _GMAIL_API + path
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, params=params or {}, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _header(message: dict, name: str) -> str:
    """One header value from a format=metadata message, or empty."""
    for header in (
        message.get("payload", {}).get("headers", [])
    ):
        if header.get("name", "").lower() == name.lower():
            return str(header.get("value", ""))
    return ""


def _match_from_metadata(message: dict) -> dict:
    """A format=metadata message reduced to the summary fields."""
    return {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "author": _header(message, "From"),
        "subject": _header(message, "Subject"),
        "date": _header(message, "Date"),
        "excerpt": str(message.get("snippet", ""))[:_EXCERPT_MAX_CHARS],
    }


def search_messages(
    config: Config, query: str, limit: int = 5
) -> dict:
    """Search messages for `query`. Returns short excerpts only.

    The live path lists message ids, then fetches each with
    format=metadata: headers plus Google's snippet, never a body.
    """
    limit = min(max(limit, 1), 20)
    if not config.gmail_configured():
        needle = query.strip().lower()
        matches = [
            m
            for m in _STUB_MESSAGES["matches"]
            if needle in m["subject"].lower()
            or needle in m["excerpt"].lower()
            or needle in m["author"].lower()
        ]
        result = dict(_STUB_MESSAGES)
        result["query"] = query
        result["matches"] = matches[:limit]
        return result
    data = _gmail_get(
        config,
        "/users/me/messages",
        params={"q": query, "maxResults": limit},
    )
    matches = []
    for entry in data.get("messages", [])[:limit]:
        message = _gmail_get(
            config,
            "/users/me/messages/%s" % entry.get("id"),
            params={
                "format": "metadata",
                "metadataHeaders": ["From", "Subject", "Date"],
            },
        )
        matches.append(_match_from_metadata(message))
    return {"query": query, "matches": matches}


def get_thread(config: Config, thread_id: str) -> dict:
    """Summarize a thread: participants, message count, key excerpts."""
    if not config.gmail_configured():
        thread = _STUB_THREADS.get(thread_id)
        if thread is None:
            return {"error": "No stub thread with id %r." % thread_id}
        return dict(thread)
    data = _gmail_get(
        config,
        "/users/me/threads/%s" % thread_id,
        params={"format": "metadata"},
    )
    messages = data.get("messages", [])
    participants: list[str] = []
    excerpts: list[str] = []
    subject = ""
    for message in messages:
        author = _header(message, "From")
        if author and author not in participants:
            participants.append(author)
        if not subject:
            subject = _header(message, "Subject")
        if len(excerpts) < 3:
            excerpt = str(message.get("snippet", ""))[:_EXCERPT_MAX_CHARS]
            if excerpt:
                excerpts.append("%s: %s" % (author or "unknown", excerpt))
    return {
        "thread_id": thread_id,
        "subject": subject,
        "participants": participants,
        "message_count": len(messages),
        "excerpts": excerpts,
    }


def gmail_tools(config: Config) -> list[Tool]:
    """The two Gmail tools, bound to this config, for the registry."""
    return [
        Tool(
            name="gmail_search_messages",
            description=(
                "Search Gmail for an account or topic. Returns short "
                "excerpts (author, subject, date, first ~200 chars), "
                "not full message bodies. Takes query and optional limit."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
            handler=lambda query, limit=5: search_messages(
                config, query, limit=limit
            ),
        ),
        Tool(
            name="gmail_get_thread",
            description=(
                "Summarize a Gmail thread: participants, message count, "
                "key excerpts. Takes thread_id."
            ),
            parameters={
                "type": "object",
                "properties": {"thread_id": {"type": "string"}},
                "required": ["thread_id"],
            },
            handler=lambda thread_id: get_thread(config, thread_id),
        ),
    ]
