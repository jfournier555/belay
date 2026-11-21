"""Zoom connector tools.

Tools exposed (both in-process and as MCP tools via server.py):
- zoom_list_recordings: recordings for an account (date, participants,
  duration, title). Summary only: no transcript in the result.
- zoom_get_recording_summary: the recording's AI-generated summary
  (topics, action items, next steps), not the raw transcript.
- zoom_search_transcripts: search recording transcripts for a keyword.
  Returns short excerpts with timestamps, never a full transcript.

Two modes:
- Live: uses the Zoom API with server-to-server OAuth (account id,
  client id, client secret). The connector exchanges them for an access
  token (POST zoom.us/oauth/token, grant_type=account_credentials),
  then calls api.zoom.us: GET /v2/users/me/recordings for lists,
  GET /v2/meetings/{meetingId}/recordings for summaries and transcript
  files. The API calls are real code, written against the documented
  Zoom API, but they have NOT been tested against a live account (no
  credentials were available while building v0.8.5). Treat the live
  path as "written, not verified" until someone runs it with real
  credentials. Server-to-server OAuth needs the recording:read:admin
  scope on the Zoom app.
- Stub: when no credentials are present, or HARNESS_STUB=1, returns
  realistic sample data for a fictional account. The returned dicts
  carry "_stub": true. The stub is what the demo and the tests use.

Summary-first: list_recordings returns recording metadata only;
get_recording_summary returns Zoom's own AI-generated summary, already
compressed and capped at 2000 chars; search returns short excerpts
(speaker, timestamp, first 200 chars), capped at 5 matches, never full
transcripts. A raw transcript can be 10k+ tokens; it must never reach
a prompt. Recording ids and play URLs are always included, so the
harness can build deep links to the recordings.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from harness.config import Config
from harness.registry import Tool

_ZOOM_OAUTH_URL = "https://zoom.us/oauth/token"
_ZOOM_API = "https://api.zoom.us/v2"

# ---------------------------------------------------------------------------
# Stub data: a fictional account, for demo and tests. Clearly marked.
# Recording ids are 11-digit numeric strings, the shape Zoom uses.
# The recordings are the same Acme story the other connectors reference:
# QBR prep, renewal check-in, SSO follow-up.
# ---------------------------------------------------------------------------

_STUB_RECORDINGS = {
    "_stub": True,
    "account": "Acme Corp",
    "recordings": [
        {
            "id": "88123456789",
            "topic": "Acme Corp - QBR prep",
            "start_time": "2026-11-13T15:00:00Z",
            "duration": 1800,
            "host": "jane-csm",
            "play_url": "https://zoom.invalid/rec/play/88123456789",
        },
        {
            "id": "88123456790",
            "topic": "Acme Corp - Renewal check-in",
            "start_time": "2026-11-10T14:00:00Z",
            "duration": 1500,
            "host": "jane-csm",
            "play_url": "https://zoom.invalid/rec/play/88123456790",
        },
        {
            "id": "88123456791",
            "topic": "Acme Corp - SSO support follow-up",
            "start_time": "2026-11-06T16:30:00Z",
            "duration": 900,
            "host": "jane-csm",
            "play_url": "https://zoom.invalid/rec/play/88123456791",
        },
    ],
}

_STUB_SUMMARIES = {
    "88123456789": {
        "_stub": True,
        "recording_id": "88123456789",
        "title": "Acme Corp - QBR prep",
        "summary": (
            "Acme's ops lead walked through the reporting rollout: 50 seats "
            "live, export timeout still open, renewal Feb 15. The champion "
            "is solid but there is no second relationship yet."
        ),
        "topics": ["reporting rollout", "export timeout", "renewal"],
        "action_items": [
            "Support to confirm the export fix ship date.",
            "Jane to book the second-relationship intro with ops.",
        ],
        "play_url": "https://zoom.invalid/rec/play/88123456789",
    },
    "88123456790": {
        "_stub": True,
        "recording_id": "88123456790",
        "title": "Acme Corp - Renewal check-in",
        "summary": (
            "Renewal check-in with the Acme admin. SSO login failures for "
            "new hires came up again; support is working the fix. The admin "
            "asked about the 50-seat expansion once the export timeout is "
            "fixed."
        ),
        "topics": ["SSO login", "expansion", "renewal"],
        "action_items": [
            "Support to update the admin when the SSO fix ships.",
            "Jane to revisit the 50-seat expansion after the export fix.",
        ],
        "play_url": "https://zoom.invalid/rec/play/88123456790",
    },
}

_STUB_TRANSCRIPT_MATCHES = {
    "_stub": True,
    "query": "Acme Corp",
    "matches": [
        {
            "recording_id": "88123456789",
            "title": "Acme Corp - QBR prep",
            "timestamp": "12:04",
            "speaker": "acme-ops-lead",
            "excerpt": (
                "The reporting rollout is live for fifty seats now. The "
                "export timeout is the one thing still blocking us."
            ),
            "play_url": "https://zoom.invalid/rec/play/88123456789",
        },
        {
            "recording_id": "88123456790",
            "title": "Acme Corp - Renewal check-in",
            "timestamp": "08:31",
            "speaker": "acme-admin",
            "excerpt": (
                "SSO login fails for new hires again. We are still waiting "
                "on the fix from support."
            ),
            "play_url": "https://zoom.invalid/rec/play/88123456790",
        },
        {
            "recording_id": "88123456790",
            "title": "Acme Corp - Renewal check-in",
            "timestamp": "21:47",
            "speaker": "acme-admin",
            "excerpt": (
                "If the export timeout is fixed we want fifty more seats. "
                "Talk to us after the fix ships."
            ),
            "play_url": "https://zoom.invalid/rec/play/88123456790",
        },
    ],
}

_EXCERPT_MAX_CHARS = 200
_SUMMARY_MAX_CHARS = 2000
_MAX_ITEMS = 5

# Access-token cache: one exchange per hour at most, instead of one per
# tool call. Module-level on purpose: the tools are stateless functions.
_TOKEN_CACHE: dict = {"token": None, "expires_at": 0.0}


def _zoom_access_token(config: Config) -> str:
    """Exchange the server-to-server OAuth triple for an access token.

    Live path only. The token is cached until shortly before expiry.
    """
    now = time.time()
    if _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expires_at"] - 60:
        return _TOKEN_CACHE["token"]
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            _ZOOM_OAUTH_URL,
            params={
                "grant_type": "account_credentials",
                "account_id": config.zoom_account_id,
            },
            auth=(config.zoom_client_id, config.zoom_client_secret),
        )
        resp.raise_for_status()
        data = resp.json()
    _TOKEN_CACHE["token"] = data["access_token"]
    _TOKEN_CACHE["expires_at"] = now + int(data.get("expires_in", 3600))
    return data["access_token"]


def _zoom_get(config: Config, path: str, params: dict | None = None) -> dict:
    """GET a Zoom API path with a Bearer token. Live path only."""
    headers = {"Authorization": "Bearer " + _zoom_access_token(config)}
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            _ZOOM_API + path, params=params or {}, headers=headers
        )
        resp.raise_for_status()
        return resp.json()


def _zoom_download(config: Config, url: str) -> str:
    """Download a file (transcript VTT, AI summary) with a Bearer token.

    Live path only. Zoom file URLs can redirect to storage, so redirects
    are followed.
    """
    headers = {"Authorization": "Bearer " + _zoom_access_token(config)}
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text


def _recent_window(days: int) -> tuple[str, str]:
    """(from, to) dates for the last `days` days, as YYYY-MM-DD."""
    now = datetime.now(timezone.utc)
    return (
        (now - timedelta(days=days)).strftime("%Y-%m-%d"),
        now.strftime("%Y-%m-%d"),
    )


def _mp4_play_url(files: list[dict]) -> str | None:
    """The play URL of the MP4 recording file, if the API returned one."""
    for f in files:
        if f.get("file_type") == "MP4" and f.get("play_url"):
            return f["play_url"]
    for f in files:
        if f.get("play_url"):
            return f["play_url"]
    return None


def _match_account(account: str, topic: str, host: str) -> bool:
    """True when a recording looks like it belongs to `account`: the name
    appears in the topic or in the host name or email."""
    needle = account.strip().lower()
    if not needle:
        return True
    if needle in topic.lower():
        return True
    return needle in host.lower()


_CUE_START = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[.,]\d+\s*-->")


def _parse_vtt(text: str) -> list[tuple[str, str]]:
    """Parse a Zoom transcript VTT file into (MM:SS, cue text) pairs.

    Cue sequence numbers and the WEBVTT header are skipped. Hours are
    folded into minutes, so 01:05:30 becomes 65:30.
    """
    cues: list[tuple[str, str]] = []
    current_start: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_start, current_lines
        if current_start is not None and current_lines:
            cues.append((current_start, " ".join(current_lines).strip()))
        current_start = None
        current_lines = []

    for raw in text.splitlines():
        line = raw.strip()
        match = _CUE_START.match(line)
        if match:
            flush()
            hours, minutes, seconds = (
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )
            current_start = "%02d:%02d" % (hours * 60 + minutes, seconds)
        elif line and line != "WEBVTT" and not line.startswith(("Kind:", "Language:", "NOTE")):
            if current_start is not None:
                current_lines.append(line)
        elif not line:
            flush()
    flush()
    return cues


def _split_speaker(text: str) -> tuple[str, str]:
    """Split "Name: words" into (speaker, words). Zoom VTT cues carry
    the speaker name inline. Falls back to unknown when there is none."""
    if ": " in text:
        name, rest = text.split(": ", 1)
        if 0 < len(name) <= 40:
            return name.strip(), rest.strip()
    return "unknown", text


def list_recordings(config: Config, account: str, days: int = 30) -> dict:
    """Recordings for `account` (name): date, duration, host, title.

    Summary only: no transcript in the result. The live path filters
    client-side, because Zoom has no account-name filter: it keeps
    recordings whose topic or host mentions the account.
    """
    if not config.zoom_configured():
        result = dict(_STUB_RECORDINGS)
        result["requested"] = account
        return result
    from_date, to_date = _recent_window(days)
    data = _zoom_get(
        config,
        "/users/me/recordings",
        {"from": from_date, "to": to_date, "page_size": 30},
    )
    recordings = []
    for meeting in data.get("meetings", []):
        topic = str(meeting.get("topic", ""))
        host = str(
            meeting.get("host_email", "") or meeting.get("host_id", "")
        )
        if not _match_account(account, topic, host):
            continue
        recordings.append(
            {
                "id": str(meeting.get("id")),
                "topic": topic,
                "start_time": meeting.get("start_time"),
                "duration": meeting.get("duration"),
                "host": host,
                "play_url": _mp4_play_url(meeting.get("recording_files", [])),
            }
        )
        if len(recordings) >= _MAX_ITEMS:
            break
    return {"account": account, "recordings": recordings}


def get_recording_summary(config: Config, recording_id: str) -> dict:
    """Zoom's AI-generated summary for one recording: summary, topics,
    action items. Not the raw transcript. Needs AI Companion summaries
    enabled on the Zoom account; without one the call errors.
    """
    if not config.zoom_configured():
        summary = _STUB_SUMMARIES.get(recording_id)
        if summary is None:
            return {"error": "No stub recording with id %r." % recording_id}
        return dict(summary)
    data = _zoom_get(config, "/meetings/%s/recordings" % recording_id)
    files = data.get("recording_files", [])
    play_url = _mp4_play_url(files)
    summary_file = next(
        (f for f in files if f.get("file_type") == "SUMMARY"), None
    )
    if summary_file is None or not summary_file.get("download_url"):
        return {
            "error": "No AI summary for recording %r." % recording_id
        }
    text = _zoom_download(config, summary_file["download_url"])
    return {
        "recording_id": recording_id,
        "title": data.get("topic"),
        "summary": text[:_SUMMARY_MAX_CHARS],
        "play_url": play_url,
    }


def search_transcripts(
    config: Config, query: str, limit: int = 5
) -> dict:
    """Search recording transcripts for `query`. Returns short excerpts
    with timestamps (speaker, MM:SS into the recording), capped at ~200
    chars and 5 matches. Never a full transcript: the live path lists
    recent recordings, downloads their transcript files, and scans the
    cues client-side.
    """
    limit = min(max(limit, 1), 20)
    if not config.zoom_configured():
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
    from_date, to_date = _recent_window(30)
    listed = _zoom_get(
        config,
        "/users/me/recordings",
        {"from": from_date, "to": to_date, "page_size": 30},
    )
    meetings = listed.get("meetings", [])[:_MAX_ITEMS]
    needle = query.strip().lower()
    matches = []
    for meeting in meetings:
        recording_id = str(meeting.get("id"))
        detail = _zoom_get(
            config, "/meetings/%s/recordings" % recording_id
        )
        files = detail.get("recording_files", [])
        transcript_file = next(
            (f for f in files if f.get("file_type") == "TRANSCRIPT"), None
        )
        if transcript_file is None or not transcript_file.get("download_url"):
            continue
        vtt = _zoom_download(config, transcript_file["download_url"])
        play_url = _mp4_play_url(files)
        for timestamp, cue_text in _parse_vtt(vtt):
            if needle not in cue_text.lower():
                continue
            speaker, excerpt = _split_speaker(cue_text)
            matches.append(
                {
                    "recording_id": recording_id,
                    "title": detail.get("topic", recording_id),
                    "timestamp": timestamp,
                    "speaker": speaker,
                    "excerpt": excerpt[:_EXCERPT_MAX_CHARS],
                    "play_url": play_url,
                }
            )
            if len(matches) >= limit:
                return {"query": query, "matches": matches}
    return {"query": query, "matches": matches}


def zoom_tools(config: Config) -> list[Tool]:
    """The three Zoom tools, bound to this config, for the registry."""
    return [
        Tool(
            name="zoom_list_recordings",
            description=(
                "List recent Zoom recordings for an account: date, "
                "duration, host, title. Summary only, no transcript. "
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
            handler=lambda account, days=30: list_recordings(
                config, account, days=days
            ),
        ),
        Tool(
            name="zoom_get_recording_summary",
            description=(
                "Get Zoom's AI-generated summary for one recording: "
                "summary, topics, action items. Not the raw transcript. "
                "Takes recording_id."
            ),
            parameters={
                "type": "object",
                "properties": {"recording_id": {"type": "string"}},
                "required": ["recording_id"],
            },
            handler=lambda recording_id: get_recording_summary(
                config, recording_id
            ),
        ),
        Tool(
            name="zoom_search_transcripts",
            description=(
                "Search Zoom recording transcripts for a keyword. Returns "
                "short excerpts with timestamps, not full transcripts. "
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
