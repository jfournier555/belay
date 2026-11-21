"""Slack connector tools.

Tools exposed (both in-process and as MCP tools via server.py):
- slack_search_messages: search messages for an account/topic.
- slack_list_channels: list channels the bot can read.
- slack_get_thread: summarize a thread (participants, count, key excerpts).

Two modes:
- Live: uses the Slack Web API with a bot token (SLACK_BOT_TOKEN). The API
  calls are real code, written against the documented Slack Web API, but
  they have NOT been tested against a live workspace (no token was
  available while building v1). Treat the live path as "written, not
  verified" until someone runs it with a real token.
- Stub: when no token is present, or HARNESS_STUB=1, returns realistic
  sample data for a fictional workspace. The returned dicts carry
  "_stub": true. The stub is what the demo and the tests use.

Summary-first: search returns short excerpts (author, channel, timestamp,
first 200 chars), never full threads. A thread summary returns the
participant list, message count, and the three most relevant excerpts, not
the whole conversation. The agent reads the signal, not the transcript.
"""

from __future__ import annotations

import httpx

from harness.config import Config
from harness.registry import Tool

_SLACK_API = "https://slack.com/api"

# ---------------------------------------------------------------------------
# Stub data: a fictional workspace, for demo and tests. Clearly marked.
# ---------------------------------------------------------------------------

_STUB_MESSAGES = {
    "_stub": True,
    "query": "Acme Corp",
    "matches": [
        {
            "author": "jane-csm",
            "channel": "#acme-corp",
            "timestamp": "2026-11-14T15:02:00Z",
            "permalink": "https://stub.slack.invalid/archives/acme-corp/p1763125320000100",
            "excerpt": "Acme's admin asked about SSO for new hires again, still waiting on the fix from support.",
        },
        {
            "author": "mike-se",
            "channel": "#acme-corp",
            "timestamp": "2026-11-12T10:41:00Z",
            "permalink": "https://stub.slack.invalid/archives/acme-corp/p1762953660000200",
            "excerpt": "Demoed the new reporting to Acme's ops lead. She wants 50 more seats if the export timeout is fixed.",
        },
        {
            "author": "jane-csm",
            "channel": "#csm-team",
            "timestamp": "2026-11-10T09:18:00Z",
            "permalink": "https://stub.slack.invalid/archives/csm-team/p1762766280000300",
            "excerpt": "Acme renewal is Feb 15. Champion is solid but I have no second relationship there yet.",
        },
    ],
}

_STUB_CHANNELS = {
    "_stub": True,
    "channels": [
        {"id": "C001", "name": "acme-corp", "members": 12},
        {"id": "C002", "name": "csm-team", "members": 8},
        {"id": "C003", "name": "support-escalations", "members": 21},
    ],
}

_STUB_THREAD = {
    "_stub": True,
    "channel": "acme-corp",
    "thread_ts": "1763125200.000100",
    "participants": ["jane-csm", "mike-se", "acme-admin"],
    "message_count": 7,
    "excerpts": [
        "jane-csm: Acme's admin asked about SSO for new hires again.",
        "mike-se: Support says the fix ships next week.",
        "acme-admin: Thanks, that unblocks our onboarding.",
    ],
}


def _slack_post(config: Config, method: str, payload: dict) -> dict:
    """POST to the Slack Web API. Live path only."""
    headers = {
        "Authorization": "Bearer " + config.slack_bot_token,
        "Content-Type": "application/json; charset=utf-8",
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            _SLACK_API + "/" + method, json=payload, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(
            "Slack API error on %s: %s" % (method, data.get("error"))
        )
    return data


def search_messages(
    config: Config, query: str, channel: str | None = None, limit: int = 5
) -> dict:
    """Search Slack messages for `query`. Returns short excerpts only."""
    if not config.slack_configured():
        result = dict(_STUB_MESSAGES)
        result["query"] = query
        return result
    payload: dict = {"query": query, "count": min(max(limit, 1), 20)}
    data = _slack_post(config, "search.messages", payload)
    matches = []
    for m in data.get("messages", {}).get("matches", [])[:limit]:
        text = m.get("text", "")
        matches.append(
            {
                "author": m.get("username", "unknown"),
                "channel": m.get("channel", {}).get("name", "unknown"),
                "timestamp": m.get("ts", ""),
                "permalink": m.get("permalink"),
                "excerpt": text[:200],
            }
        )
    return {"query": query, "matches": matches}


def list_channels(config: Config) -> dict:
    """List channels the bot can read."""
    if not config.slack_configured():
        return dict(_STUB_CHANNELS)
    data = _slack_post(config, "conversations.list", {"limit": 100})
    channels = [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "members": c.get("num_members"),
        }
        for c in data.get("channels", [])
    ]
    return {"channels": channels}


def get_thread(config: Config, channel: str, thread_ts: str) -> dict:
    """Summarize a Slack thread: participants, count, key excerpts."""
    if not config.slack_configured():
        result = dict(_STUB_THREAD)
        result["channel"] = channel
        result["thread_ts"] = thread_ts
        return result
    data = _slack_post(
        config,
        "conversations.replies",
        {"channel": channel, "ts": thread_ts, "limit": 20},
    )
    messages = data.get("messages", [])
    participants: list[str] = []
    excerpts: list[str] = []
    for m in messages:
        user = m.get("user", "unknown")
        if user not in participants:
            participants.append(user)
        if len(excerpts) < 3:
            excerpts.append("%s: %s" % (user, m.get("text", "")[:200]))
    return {
        "channel": channel,
        "thread_ts": thread_ts,
        "participants": participants,
        "message_count": len(messages),
        "excerpts": excerpts,
    }


def slack_tools(config: Config) -> list[Tool]:
    """The three Slack tools, bound to this config, for the registry."""
    return [
        Tool(
            name="slack_search_messages",
            description=(
                "Search Slack messages for an account or topic. Returns "
                "short excerpts (author, channel, timestamp), not full "
                "threads. Takes query, optional channel, optional limit."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "channel": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
            handler=lambda query, channel=None, limit=5: search_messages(
                config, query, channel=channel, limit=limit
            ),
        ),
        Tool(
            name="slack_list_channels",
            description=(
                "List Slack channels the bot can read: id, name, member count."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda: list_channels(config),
        ),
        Tool(
            name="slack_get_thread",
            description=(
                "Summarize a Slack thread: participants, message count, key "
                "excerpts. Takes channel (id or name) and thread_ts."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "thread_ts": {"type": "string"},
                },
                "required": ["channel", "thread_ts"],
            },
            handler=lambda channel, thread_ts: get_thread(
                config, channel, thread_ts
            ),
        ),
    ]
