"""Gong connector tools.

Tools exposed (both in-process and as MCP tools via server.py):
- gong_list_calls: calls for an account (date, participants, duration,
  title). Summary only: no transcript in the result.
- gong_get_call_summary: the call summary Gong already generates
  (brief, key points, topics, call outcome), not the raw transcript.
- gong_search_transcripts: search call transcripts for a keyword.
  Returns short excerpts with timestamps, never a full transcript.

Two modes:
- Live: uses the Gong REST API (v2) with a Bearer access token.
  Needs GONG_ACCESS_TOKEN; GONG_API_BASE_URL defaults to
  https://api.gong.io/v2 (regional instances take the form
  https://{region}.api.gong.io/v2). The API calls are real code, written
  against the documented Gong API (POST /v2/calls/extensive for calls
  and summaries, POST /v2/calls/transcript for transcripts), but they
  have NOT been tested against a live workspace (no token was available
  while building v0.8.2). Treat the live path as "written, not verified"
  until someone runs it with a real token. Gong also supports Basic auth
  with an access key + secret; this connector uses the Bearer form.
- Stub: when no token is present, or HARNESS_STUB=1, returns realistic
  sample data for a fictional account. The returned dicts carry
  "_stub": true. The stub is what the demo and the tests use.

Summary-first: list_calls returns call metadata only; get_call_summary
returns Gong's own generated summary, already compressed; search returns
short excerpts (speaker, timestamp, first 200 chars), capped at 5
matches, never full transcripts. A raw transcript can be 10k+ tokens;
it must never reach a prompt. Call ids are always included, so the
harness can build deep links to the calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from harness.config import Config
from harness.registry import Tool

# ---------------------------------------------------------------------------
# Stub data: a fictional account, for demo and tests. Clearly marked.
# Call ids are 20-digit numeric strings, the shape Gong uses.
# ---------------------------------------------------------------------------

_STUB_CALLS = {
    "_stub": True,
    "account": "Acme Corp",
    "calls": [
        {
            "id": "7782342274025937895",
            "title": "Acme Corp - QBR prep",
            "started": "2026-11-13T15:00:00Z",
            "duration_seconds": 1800,
            "participants": ["jane-csm", "acme-admin"],
        },
        {
            "id": "7782342274025937896",
            "title": "Acme Corp - Renewal check-in",
            "started": "2026-11-10T14:00:00Z",
            "duration_seconds": 1500,
            "participants": ["jane-csm", "mike-se", "acme-ops-lead"],
        },
        {
            "id": "7782342274025937897",
            "title": "Acme Corp - SSO support follow-up",
            "started": "2026-11-06T16:30:00Z",
            "duration_seconds": 900,
            "participants": ["jane-csm", "acme-admin"],
        },
    ],
}

_STUB_SUMMARIES = {
    "7782342274025937895": {
        "_stub": True,
        "call_id": "7782342274025937895",
        "title": "Acme Corp - QBR prep",
        "brief": (
            "Acme's ops lead walked through the reporting rollout: 50 seats "
            "live, export timeout still open, renewal Feb 15. The champion "
            "is solid but there is no second relationship yet."
        ),
        "key_points": [
            "Reporting rollout live on 50 seats.",
            "Export timeout still open; fix ships next week.",
            "Renewal Feb 15; champion solid, no second relationship.",
        ],
        "topics": ["reporting rollout", "export timeout", "renewal"],
        "call_outcome": "Follow-up scheduled; renewal on track.",
    },
    "7782342274025937896": {
        "_stub": True,
        "call_id": "7782342274025937896",
        "title": "Acme Corp - Renewal check-in",
        "brief": (
            "Renewal check-in with the Acme admin. SSO login failures for "
            "new hires came up again; support is working the fix. The admin "
            "asked about the 50-seat expansion once the export timeout is "
            "fixed."
        ),
        "key_points": [
            "SSO login failures for new hires, fix in progress.",
            "50-seat expansion interest, gated on the export fix.",
        ],
        "topics": ["SSO login", "expansion", "renewal"],
        "call_outcome": "Expansion discussion after the export fix.",
    },
}

_STUB_TRANSCRIPT_MATCHES = {
    "_stub": True,
    "query": "Acme Corp",
    "matches": [
        {
            "call_id": "7782342274025937895",
            "title": "Acme Corp - QBR prep",
            "timestamp": "12:04",
            "speaker": "acme-ops-lead",
            "excerpt": (
                "The reporting rollout is live for fifty seats now. The "
                "export timeout is the one thing still blocking us."
            ),
        },
        {
            "call_id": "7782342274025937896",
            "title": "Acme Corp - Renewal check-in",
            "timestamp": "08:31",
            "speaker": "acme-admin",
            "excerpt": (
                "SSO login fails for new hires again. We are still waiting "
                "on the fix from support."
            ),
        },
        {
            "call_id": "7782342274025937896",
            "title": "Acme Corp - Renewal check-in",
            "timestamp": "21:47",
            "speaker": "acme-admin",
            "excerpt": (
                "If the export timeout is fixed we want fifty more seats. "
                "Talk to us after the fix ships."
            ),
        },
    ],
}

_EXCERPT_MAX_CHARS = 200
_MAX_ITEMS = 5


def _iso(dt: datetime) -> str:
    """Format a datetime as ISO-8601 with Z, the shape Gong expects."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _recent_window(days: int) -> tuple[str, str]:
    """(fromDateTime, toDateTime) for the last `days` days, as ISO strings."""
    now = datetime.now(timezone.utc)
    return _iso(now - timedelta(days=days)), _iso(now)


def _mmss(start_seconds: float) -> str:
    """Seconds into a call as MM:SS, the timestamp shown with excerpts."""
    total = int(start_seconds)
    return "%02d:%02d" % (total // 60, total % 60)


def _gong_post(config: Config, path: str, payload: dict) -> dict:
    """POST to the Gong REST API. Live path only."""
    headers = {
        "Authorization": "Bearer " + config.gong_access_token,
        "Content-Type": "application/json",
    }
    url = config.gong_api_base_url.rstrip("/") + path
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _match_account(account: str, title: str, people: list[str]) -> bool:
    """True when a call looks like it belongs to `account`: the name
    appears in the title or in a participant name or email."""
    needle = account.strip().lower()
    if not needle:
        return True
    if needle in title.lower():
        return True
    return any(needle in person.lower() for person in people)


def list_calls(config: Config, account: str, days: int = 30) -> dict:
    """Calls for `account` (name): date, participants, duration, title.

    Summary only: no transcript in the result. The live path filters
    client-side, because Gong has no account-name filter: it keeps calls
    whose title or participants mention the account.
    """
    if not config.gong_configured():
        result = dict(_STUB_CALLS)
        result["requested"] = account
        return result
    from_dt, to_dt = _recent_window(days)
    data = _gong_post(
        config,
        "/calls/extensive",
        {
            "filter": {"fromDateTime": from_dt, "toDateTime": to_dt},
            "contentSelector": {"exposedFields": {"parties": True}},
        },
    )
    calls = []
    for call in data.get("calls", []):
        meta = call.get("metaData", {})
        people = [
            str(p.get("name") or p.get("emailAddress") or "")
            for p in call.get("parties", [])
        ]
        if not _match_account(account, str(meta.get("title", "")), people):
            continue
        calls.append(
            {
                "id": meta.get("id"),
                "title": meta.get("title"),
                "started": meta.get("started") or meta.get("scheduled"),
                "duration_seconds": meta.get("duration"),
                "participants": [p for p in people if p],
            }
        )
        if len(calls) >= _MAX_ITEMS:
            break
    return {"account": account, "calls": calls}


def get_call_summary(config: Config, call_id: str) -> dict:
    """Gong's generated summary for one call: brief, key points, topics,
    call outcome. Not the raw transcript."""
    if not config.gong_configured():
        summary = _STUB_SUMMARIES.get(call_id)
        if summary is None:
            return {"error": "No stub call with id %r." % call_id}
        return dict(summary)
    data = _gong_post(
        config,
        "/calls/extensive",
        {
            "filter": {"callIds": [call_id]},
            "contentSelector": {
                "exposedFields": {
                    "parties": True,
                    "content": {
                        "brief": True,
                        "keyPoints": True,
                        "topics": True,
                        "callOutcome": True,
                    },
                }
            },
        },
    )
    calls = data.get("calls", [])
    if not calls:
        return {"error": "No call with id %r." % call_id}
    call = calls[0]
    meta = call.get("metaData", {})
    content = call.get("content", {})
    return {
        "call_id": meta.get("id", call_id),
        "title": meta.get("title"),
        "brief": content.get("brief"),
        "key_points": content.get("keyPoints", []),
        "topics": content.get("topics", []),
        "call_outcome": content.get("callOutcome"),
    }


def search_transcripts(
    config: Config, query: str, limit: int = 5
) -> dict:
    """Search call transcripts for `query`. Returns short excerpts with
    timestamps (speaker, MM:SS into the call), capped at ~200 chars and
    5 matches. Never a full transcript: the live path fetches the recent
    calls, pulls their transcripts, and scans the sentences client-side.
    """
    limit = min(max(limit, 1), 20)
    if not config.gong_configured():
        needle = query.strip().lower()
        matches = [
            m
            for m in _STUB_TRANSCRIPT_MATCHES["matches"]
            if needle in m["excerpt"].lower()
            or needle in m["title"].lower()
        ]
        result = dict(_STUB_TRANSCRIPT_MATCHES)
        result["query"] = query
        result["matches"] = matches[:limit]
        return result
    from_dt, to_dt = _recent_window(30)
    listed = _gong_post(
        config,
        "/calls/extensive",
        {
            "filter": {"fromDateTime": from_dt, "toDateTime": to_dt},
            "contentSelector": {"exposedFields": {"parties": True}},
        },
    )
    titles = {}
    call_ids = []
    for call in listed.get("calls", [])[:_MAX_ITEMS]:
        call_id = call.get("metaData", {}).get("id")
        if call_id:
            call_ids.append(call_id)
            titles[call_id] = call.get("metaData", {}).get("title", call_id)
    if not call_ids:
        return {"query": query, "matches": []}
    data = _gong_post(
        config,
        "/calls/transcript",
        {
            "filter": {
                "fromDateTime": from_dt,
                "toDateTime": to_dt,
                "callIds": call_ids,
            }
        },
    )
    needle = query.strip().lower()
    matches = []
    for entry in data.get("callTranscripts", []):
        call_id = entry.get("callId")
        for monologue in entry.get("transcript", []):
            speaker = str(monologue.get("speakerId", "unknown"))
            for sentence in monologue.get("sentences", []):
                text = str(sentence.get("text", ""))
                if needle not in text.lower():
                    continue
                matches.append(
                    {
                        "call_id": call_id,
                        "title": titles.get(call_id, call_id),
                        "timestamp": _mmss(float(sentence.get("start") or 0)),
                        "speaker": speaker,
                        "excerpt": text[:_EXCERPT_MAX_CHARS],
                    }
                )
                if len(matches) >= limit:
                    return {"query": query, "matches": matches}
    return {"query": query, "matches": matches}


def gong_tools(config: Config) -> list[Tool]:
    """The three Gong tools, bound to this config, for the registry."""
    return [
        Tool(
            name="gong_list_calls",
            description=(
                "List recent Gong calls for an account: date, participants, "
                "duration, title. Summary only, no transcript. "
                "Takes account (name) and optional days."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "account": {"type": "string"},
                    "days": {"type": "integer"},
                },
                "required": ["account"],
            },
            handler=lambda account, days=30: list_calls(
                config, account, days=days
            ),
        ),
        Tool(
            name="gong_get_call_summary",
            description=(
                "Get Gong's generated summary for one call: brief, key "
                "points, topics, call outcome. Not the raw transcript. "
                "Takes call_id."
            ),
            parameters={
                "type": "object",
                "properties": {"call_id": {"type": "string"}},
                "required": ["call_id"],
            },
            handler=lambda call_id: get_call_summary(config, call_id),
        ),
        Tool(
            name="gong_search_transcripts",
            description=(
                "Search Gong call transcripts for a keyword. Returns short "
                "excerpts with timestamps, not full transcripts. "
                "Takes query and optional limit."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
            handler=lambda query, limit=5: search_transcripts(
                config, query, limit=limit
            ),
        ),
    ]
