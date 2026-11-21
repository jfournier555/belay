"""Belay dashboard: the local web UI the CSM operates from.

Serves one page on localhost that reads live harness state: the approval
queue file, the registered agents, the connector configuration, and the
token budget. Every number on the page comes from that state; nothing is
invented. When the queue is empty the page says so.

Run it with:

    python -m harness.dashboard [--port 8000]

Then open http://127.0.0.1:8000 in a browser. Local-only, no auth: it
binds to loopback and reads the same approvals file the CLI uses
(HARNESS_APPROVALS_FILE, default approvals.json in the working dir).

Approve and reject buttons call the same ApprovalQueue methods the CLI
uses. Nothing writes back to Salesforce until a human approves it.
Write-back shipped in v1.3.0: the updater agent drafts changes, and
only an approved write draft executes, through this same queue.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from . import AGENTS, __version__
from .agents import _extract_account
from .approvals import ApprovalQueue
from .config import Config
from .escalations import EscalationStore
from .memory import AccountMemory
from .summarizer import estimate_tokens

_TEMPLATE_PATH = Path(__file__).with_name("dashboard.html")


def queue_stats(queue: ApprovalQueue) -> dict:
    """Top stat strip, all derived from the queue file.

    - accounts_watched: distinct accounts across every queued draft.
    - at_risk_flagged: pending renewal-desk drafts. A renewal plan is
      the at-risk work; the count is the drafts waiting, not a claim
      about the accounts themselves.
    - drafts_ready: pending drafts waiting for a human decision.
    - advocates_found: advocate-finder drafts in the queue. Each one
      names the advocates it found.
    """
    items = queue.list_all()
    accounts = {
        _extract_account(item.job)
        for item in items
        if _extract_account(item.job)
    }
    pending = [item for item in items if item.status == "pending"]
    return {
        "accounts_watched": len(accounts),
        "at_risk_flagged": sum(
            1 for item in pending if item.agent == "renewal-desk"
        ),
        "drafts_ready": len(pending),
        "advocates_found": sum(
            1 for item in items if item.agent == "advocate-finder"
        ),
    }


def source_statuses(config: Config) -> list[dict]:
    """Left rail: the sources, with live status from the config.

    A source is live only when its credentials are present and stub mode
    is off. Otherwise it is stub: realistic sample data, no API call.
    """
    return [
        {
            "name": "Salesforce",
            "status": "live" if config.salesforce_configured() else "stub",
            "detail": (
                "Connected."
                if config.salesforce_configured()
                else "No credentials: stub data."
            ),
        },
        {
            "name": "Slack",
            "status": "live" if config.slack_configured() else "stub",
            "detail": (
                "Connected."
                if config.slack_configured()
                else "No credentials: stub data."
            ),
        },
        {
            "name": "Gong",
            "status": "live" if config.gong_configured() else "stub",
            "detail": (
                "Connected."
                if config.gong_configured()
                else "No credentials: stub data."
            ),
        },
        {
            "name": "HubSpot",
            "status": "live" if config.hubspot_configured() else "stub",
            "detail": (
                "Connected."
                if config.hubspot_configured()
                else "No credentials: stub data."
            ),
        },
        {
            "name": "Gmail",
            "status": "live" if config.gmail_configured() else "stub",
            "detail": (
                "Connected."
                if config.gmail_configured()
                else "No credentials: stub data."
            ),
        },
        {
            "name": "Zoom",
            "status": "live" if config.zoom_configured() else "stub",
            "detail": (
                "Connected."
                if config.zoom_configured()
                else "No credentials: stub data."
            ),
        },
        {
            "name": "Zendesk",
            "status": "live" if config.zendesk_configured() else "stub",
            "detail": (
                "Connected."
                if config.zendesk_configured()
                else "No credentials: stub data."
            ),
        },
        {
            "name": "Linear",
            "status": "live" if config.linear_configured() else "stub",
            "detail": (
                "Connected."
                if config.linear_configured()
                else "No credentials: stub data."
            ),
        },
        {
            "name": "Champify",
            "status": "live" if config.champify_configured() else "stub",
            "detail": (
                "Connected."
                if config.champify_configured()
                else "No credentials: stub data."
            ),
        },
    ]


def agent_states(queue: ApprovalQueue) -> list[dict]:
    """Right rail: the 12 agents.

    Last-run state comes from the queue: the newest draft each agent
    produced, and when. An agent with no drafts has never run.
    """
    items = queue.list_all()
    states = []
    for name in sorted(AGENTS):
        agent = AGENTS[name]
        mine = [item for item in items if item.agent == name]
        last = max(mine, key=lambda i: i.created_at) if mine else None
        states.append(
            {
                "name": name,
                "display_name": agent.display_name,
                "description": agent.description,
                "status": "implemented",
                "drafts": len(mine),
                "last_run": last.created_at if last else None,
                "last_status": last.status if last else None,
            }
        )
    return states


def event_log(queue: ApprovalQueue, limit: int = 50) -> list[dict]:
    """Bottom rail: what happened, newest first.

    One event per draft queued and one per decision taken, taken from
    the queue file itself. No separate log file exists yet.
    """
    events = []
    for item in queue.list_all():
        events.append(
            {
                "at": item.created_at,
                "kind": "queued",
                "text": "%s drafted for %s"
                % (
                    item.agent,
                    _extract_account(item.job) or "unknown account",
                ),
                "item_id": item.id,
            }
        )
        if item.decided_at:
            events.append(
                {
                    "at": item.decided_at,
                    "kind": item.status,
                    "text": "%s %s" % (item.agent, item.status),
                    "item_id": item.id,
                }
            )
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]


def token_budget_state(queue: ApprovalQueue, config: Config) -> dict:
    """Bottom rail: the token budget.

    The per-job budget comes from the config (default 8000). The token
    estimate is computed live from the pending drafts' text; the queue
    file does not store token counts, so nothing is claimed that was
    not measured here.
    """
    pending = queue.list_pending()
    return {
        "budget_per_job": config.token_budget_per_job,
        "pending_drafts": len(pending),
        "estimated_tokens_pending": sum(
            estimate_tokens(item.draft) for item in pending
        ),
    }


def queue_view(queue: ApprovalQueue) -> list[dict]:
    """The approval queue, hero of the page: pending drafts first."""
    items = queue.list_all()
    pending = [i for i in items if i.status == "pending"]
    decided = [i for i in items if i.status != "pending"]
    ordered = sorted(pending, key=lambda i: i.created_at) + sorted(
        decided, key=lambda i: i.created_at, reverse=True
    )
    view = []
    for item in ordered:
        view.append(
            {
                "id": item.id,
                "agent": item.agent,
                "account": _extract_account(item.job),
                "job": item.job,
                "draft": item.draft,
                "sources": item.sources,
                "links": item.links or [],
                "proof": item.proof or [],
                "status": item.status,
                "created_at": item.created_at,
                "decided_at": item.decided_at,
                "note": item.note,
                "writes": item.writes or [],
                "execution": item.execution or [],
                "executed_at": item.executed_at,
            }
        )
    return view


def build_state(queue: ApprovalQueue, config: Config) -> dict:
    """Everything the page renders, in one dict."""
    return {
        "version": __version__,
        "stats": queue_stats(queue),
        "sources": source_statuses(config),
        "agents": agent_states(queue),
        "queue": queue_view(queue),
        "events": event_log(queue),
        "tokens": token_budget_state(queue, config),
        "stub_mode": config.stub_mode,
    }


def _load_template() -> str:
    with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def create_app(
    config: Config | None = None,
    queue: ApprovalQueue | None = None,
) -> FastAPI:
    """Build the dashboard app. Defaults read the live config and queue."""
    cfg = config or Config.from_env()
    app = FastAPI(title="Belay dashboard", version=__version__)

    def get_queue() -> ApprovalQueue:
        # Re-read the file on every request so drafts queued by the CLI
        # show up without restarting the server. The memory store is
        # attached so dashboard decisions are recorded as memory too,
        # and the escalation store so approving an escalation-manager
        # draft moves its records to escalated here as well.
        return queue or ApprovalQueue(
            cfg.approvals_file,
            memory=AccountMemory(cfg.memory_file),
            escalations=EscalationStore(cfg.escalations_file),
        )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _load_template()

    @app.get("/api/state")
    def state() -> JSONResponse:
        return JSONResponse(build_state(get_queue(), cfg))

    @app.post("/api/approvals/{item_id}/approve")
    def approve(item_id: str) -> JSONResponse:
        q = get_queue()
        try:
            item = q.approve(item_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown approval item")
        if item.writes:
            # Approval-gated execution (v1.3.0): the dashboard approves
            # through the same executor as the CLI, so a write draft
            # approved here applies exactly as one approved in the CLI
            # would. Lazy import: run.py imports the dashboard lazily,
            # so the dashboard must not import it at module load.
            from harness.run import build_registry
            from harness.writeback import execute_writes

            execute_writes(q, build_registry(cfg), item)
        return JSONResponse(item.to_dict())

    @app.post("/api/approvals/{item_id}/reject")
    def reject(item_id: str) -> JSONResponse:
        try:
            item = get_queue().reject(item_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown approval item")
        return JSONResponse(item.to_dict())

    return app


def main(argv: list[str] | None = None) -> int:
    """Serve the dashboard on localhost. `python -m harness.dashboard`."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(
        prog="belay-dashboard",
        description="Serve the Belay dashboard on localhost.",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    config = Config.from_env()
    print(
        "Belay dashboard v%s on http://%s:%d"
        % (__version__, args.host, args.port)
    )
    print("Reading queue: %s" % os.path.abspath(config.approvals_file))
    uvicorn.run(
        create_app(config),
        host=args.host,
        port=args.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
