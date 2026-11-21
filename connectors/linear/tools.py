"""Linear connector tools.

Tools exposed (both in-process and as MCP tools via server.py):
- linear_list_issues: open issues for a team (title, status, priority,
  age, assignee). Summary only.
- linear_search_issues: search issues by keyword. Returns short
  excerpts, capped at ~200 chars and 5 matches.
- linear_get_issue: one issue's summary (title, status, priority,
  assignee, description excerpt capped the same way, never the full
  comment thread).

Two modes:
- Live: uses the Linear GraphQL API with an API key
  (LINEAR_API_KEY). The API calls are real code, written against the
  documented Linear GraphQL API, but they have NOT been tested against
  a live workspace (no credentials were available while building
  v0.8.7). Treat the live path as "written, not verified" until
  someone runs it with a real key. Auth is the raw API key in the
  Authorization header.
- Stub: when no key is present, or HARNESS_STUB=1, returns realistic
  sample data for a fictional team. The returned dicts carry
  "_stub": true. The stub is what the demo and the tests use.

Summary-first: list returns issue metadata only; search returns short
excerpts (title, first 200 chars of the description), capped at 5
matches; a single issue returns the summary plus a description
excerpt, never the comment thread. Issue identifiers are always
included, so the harness can build deep links to the issues.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from harness.config import Config
from harness.registry import Tool

_LINEAR_API = "https://api.linear.app/graphql"

# ---------------------------------------------------------------------------
# Stub data: a fictional team, for demo and tests. Clearly marked.
# Issue ids are fake UUIDs; identifiers are the ENG-### keys Linear
# shows. The issues are the same Acme story the other connectors
# reference: SSO login failures, the export timeout, the 50-seat
# rollout.
# ---------------------------------------------------------------------------

_STUB_TEAM = "ENG"

_STUB_ISSUES = {
    "_stub": True,
    "team": _STUB_TEAM,
    "issues": [
        {
            "id": "01J0000000000000000000001",
            "identifier": "ENG-142",
            "title": "SSO login fails for new hires",
            "status": "In Progress",
            "priority": "high",
            "assignee": "sam-eng",
            "created": "2026-11-08T10:00:00Z",
            "age_days": 6,
            "team": _STUB_TEAM,
            "description": (
                "SSO login fails for new hires again. Still waiting on "
                "the fix from support."
            ),
        },
        {
            "id": "01J0000000000000000000002",
            "identifier": "ENG-147",
            "title": "Export timeout on large reports",
            "status": "Todo",
            "priority": "medium",
            "assignee": "priya-eng",
            "created": "2026-11-12T09:30:00Z",
            "age_days": 2,
            "team": _STUB_TEAM,
            "description": (
                "Export times out on large reports. Blocks the 50-seat "
                "rollout."
            ),
        },
        {
            "id": "01J0000000000000000000003",
            "identifier": "ENG-151",
            "title": "Reporting dashboard slow for 50-seat rollout",
            "status": "Backlog",
            "priority": "low",
            "assignee": None,
            "created": "2026-11-13T14:00:00Z",
            "age_days": 1,
            "team": _STUB_TEAM,
            "description": (
                "Dashboard loads slowly now that fifty seats are live."
            ),
        },
    ],
}

_EXCERPT_MAX_CHARS = 200
_MAX_ITEMS = 5

# Linear priority is a number: 0 none, 1 urgent, 2 high, 3 medium,
# 4 low. The harness works in words, not numbers.
_PRIORITY_LABELS = {
    0: "none",
    1: "urgent",
    2: "high",
    3: "medium",
    4: "low",
}

# State types that count as open: completed and canceled are done, and
# the harness only acts on issues still being worked.
_OPEN_STATE_TYPES = {"backlog", "unstarted", "started"}

_ISSUE_FIELDS = (
    "id identifier title state { name type } priority "
    "assignee { name } createdAt team { key } description"
)


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


def _linear_graphql(config: Config, query: str, variables: dict) -> dict:
    """POST one GraphQL operation to the Linear API. Live path only.

    Auth is the raw API key in the Authorization header.
    """
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            _LINEAR_API,
            json={"query": query, "variables": variables},
            headers={
                "Authorization": config.linear_api_key,
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    errors = data.get("errors")
    if errors:
        raise RuntimeError(
            "Linear GraphQL error: %s" % (errors[0].get("message"),)
        )
    return data.get("data", {})


def _issue_summary(issue: dict) -> dict:
    """The fields an agent acts on, from one Linear issue object.

    Accepts both shapes: the live GraphQL node (nested state, team,
    and assignee objects; numeric priority; createdAt) and the flat
    stub dict (status, team, and assignee strings; priority label;
    created and precomputed age_days)."""
    state = issue.get("state")
    status = (
        state.get("name") if isinstance(state, dict) else issue.get("status")
    )
    team = issue.get("team")
    team_key = team.get("key") if isinstance(team, dict) else team
    assignee = issue.get("assignee")
    if isinstance(assignee, dict):
        assignee = assignee.get("name")
    priority = issue.get("priority")
    if isinstance(priority, int):
        priority = _PRIORITY_LABELS.get(priority, "none")
    created = issue.get("createdAt") or issue.get("created")
    age_days = issue.get("age_days")
    if age_days is None:
        age_days = _age_days(created)
    return {
        "id": issue.get("id"),
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "status": status,
        "priority": priority,
        "assignee": assignee,
        "created": created,
        "age_days": age_days,
        "team": team_key,
    }


def _issue_match(issue: dict) -> dict:
    """A search match: the summary plus a short description excerpt."""
    summary = _issue_summary(issue)
    summary["excerpt"] = str(issue.get("description", ""))[
        :_EXCERPT_MAX_CHARS
    ]
    return summary


def list_issues(
    config: Config, team: str | None = None, limit: int = 5
) -> dict:
    """Open issues, optionally for one team (key, e.g. "ENG").

    Without a team, lists open issues workspace-wide. Summary only.
    Completed and canceled issues are excluded: the harness only acts
    on issues still being worked."""
    limit = min(max(limit, 1), 20)
    if not config.linear_configured():
        result = dict(_STUB_ISSUES)
        result["requested"] = team
        issues = result["issues"]
        if team:
            issues = [i for i in issues if i["team"] == team]
        result["issues"] = list(issues)[:limit]
        return result
    filters: dict = {"state": {"type": {"in": sorted(_OPEN_STATE_TYPES)}}}
    if team:
        filters["team"] = {"key": {"eq": team}}
    data = _linear_graphql(
        config,
        "query ($first: Int, $filter: IssueFilter) {"
        " issues(first: $first, filter: $filter, orderBy: updatedAt) {"
        " nodes { %s } } }" % _ISSUE_FIELDS,
        {"first": limit, "filter": filters},
    )
    issues = [
        _issue_summary(node)
        for node in data.get("issues", {}).get("nodes", [])
        if str((node.get("state") or {}).get("type", "")).lower()
        in _OPEN_STATE_TYPES
    ][:limit]
    return {"team": team, "issues": issues}


def search_issues(
    config: Config, query: str, limit: int = 5
) -> dict:
    """Search issues by keyword. Returns short excerpts (title, first
    200 chars of the description), capped at 5 matches. Never a full
    comment thread."""
    limit = min(max(limit, 1), 20)
    if not config.linear_configured():
        needle = query.strip().lower()
        matches = [
            _issue_match(t)
            for t in _STUB_ISSUES["issues"]
            if needle in t["title"].lower()
            or needle in t["description"].lower()
        ][:limit]
        return {
            "_stub": True,
            "query": query,
            "team": _STUB_TEAM,
            "matches": matches,
        }
    data = _linear_graphql(
        config,
        "query ($query: String!, $first: Int) {"
        " issueSearch(query: $query, first: $first) {"
        " nodes { %s } } }" % _ISSUE_FIELDS,
        {"query": query, "first": limit},
    )
    matches = [
        _issue_match(node)
        for node in data.get("issueSearch", {}).get("nodes", [])
    ][:limit]
    return {"query": query, "matches": matches}


def get_issue(config: Config, issue_id: str) -> dict:
    """One issue's summary: title, status, priority, assignee, and a
    description excerpt capped at ~200 chars. Never the full comment
    thread. Takes the issue id (UUID) or identifier (e.g. ENG-142)."""
    if not config.linear_configured():
        issue = next(
            (
                t
                for t in _STUB_ISSUES["issues"]
                if t["id"] == issue_id or t["identifier"] == issue_id
            ),
            None,
        )
        if issue is None:
            return {"error": "No stub issue with id %r." % issue_id}
        result = dict(issue)
        result["_stub"] = True
        result["description"] = issue["description"][:_EXCERPT_MAX_CHARS]
        return result
    data = _linear_graphql(
        config,
        "query ($id: String!) {"
        " issues(first: 1, filter: { or: ["
        " { id: { eq: $id } }, { identifier: { eq: $id } } ] }) {"
        " nodes { %s } } }" % _ISSUE_FIELDS,
        {"id": issue_id},
    )
    nodes = data.get("issues", {}).get("nodes", [])
    if not nodes:
        return {"error": "No issue matched %r." % issue_id}
    node = nodes[0]
    result = _issue_summary(node)
    result["description"] = str(node.get("description", ""))[
        :_EXCERPT_MAX_CHARS
    ]
    return result


def linear_tools(config: Config) -> list[Tool]:
    """The three Linear tools, bound to this config, for the registry."""
    return [
        Tool(
            name="linear_list_issues",
            description=(
                "List open Linear issues, optionally for one team "
                "(team key, e.g. ENG). Summary only, completed and "
                "canceled excluded. Takes optional team and limit."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "team": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": [],
            },
            handler=lambda team=None, limit=5: list_issues(
                config, team=team, limit=limit
            ),
        ),
        Tool(
            name="linear_search_issues",
            description=(
                "Search Linear issues by keyword. Returns short "
                "excerpts, not full issues or comment threads. Takes "
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
            handler=lambda query, limit=5: search_issues(
                config, query, limit=limit
            ),
        ),
        Tool(
            name="linear_get_issue",
            description=(
                "Get one Linear issue's summary: title, status, "
                "priority, assignee, description excerpt. Never the "
                "full comment thread. Takes issue_id (UUID or "
                "identifier, e.g. ENG-142)."
            ),
            parameters={
                "type": "object",
                "properties": {"issue_id": {"type": "string"}},
                "required": ["issue_id"],
            },
            handler=lambda issue_id: get_issue(config, issue_id),
        ),
    ]
