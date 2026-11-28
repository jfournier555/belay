"""Belay dashboard: the local web UI the CSM operates from.

Serves pages on localhost that read live harness state: the approval
queue file, the registered agents, the connector configuration, and
the token budget, plus every v1.x surface finished in v1.11.0:
memory, schedules, escalations, write-back, and external MCP
servers. Every number on the page comes from that state; nothing is
invented. When a store is empty the page says so, and a missing or
corrupt store file degrades to an empty view, never a crash.

Run it with:

    python -m harness.dashboard [--port 8000]

Then open http://127.0.0.1:8000 in a browser. Local-only, no auth: it
binds to loopback and reads the same files the CLI uses
(HARNESS_APPROVALS_FILE, HARNESS_MEMORY_FILE, HARNESS_SCHEDULES_FILE,
HARNESS_ESCALATIONS_FILE, HARNESS_MCP_SERVERS_FILE, defaults in the
working dir).

Approve, edit, and reject buttons call the same ApprovalQueue methods
the CLI uses. Nothing writes back to Salesforce until a human
approves it. Write-back shipped in v1.3.0: the updater agent drafts
changes, and only an approved write draft executes, through this
same queue.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import AGENTS, __version__
from .agents import _extract_account
from .approvals import ApprovalQueue
from .config import Config
from .escalations import EscalationStore
from .launcher import bundled_icon_path
from .mcp_client import MCPClient, MCPServerStore
from .memory import AccountMemory
from .prompt import run_prompt
from .scheduler import (
    ScheduleStore,
    _parse_iso,
    is_due,
    next_run_after,
    validate_schedule,
)
from .setup import is_first_run, seed_demo
from .summarizer import estimate_tokens
from .sweep import SweepStore, run_sweep

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
            "name": "Google Calendar",
            "status": "live" if config.gcal_configured() else "stub",
            "detail": (
                "Connected."
                if config.gcal_configured()
                else "No credentials: stub data."
            ),
        },
    ]


def agent_states(queue: ApprovalQueue) -> list[dict]:
    """Right rail: the 15 agents.

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


def _queue_item_dict(item) -> dict:
    """One queue item, as the page renders it."""
    return {
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


def queue_view(queue: ApprovalQueue) -> list[dict]:
    """The approval queue, hero of the page: pending drafts first."""
    items = queue.list_all()
    pending = [i for i in items if i.status == "pending"]
    decided = [i for i in items if i.status != "pending"]
    ordered = sorted(pending, key=lambda i: i.created_at) + sorted(
        decided, key=lambda i: i.created_at, reverse=True
    )
    return [_queue_item_dict(item) for item in ordered]


def sweep_view(store: SweepStore) -> dict:
    """The stored risk sweep: the last ranked book-wide result, or
    the empty state when no sweep has run yet. A missing or corrupt
    sweep file reads as empty, never a crash."""
    return store.load()


def build_state(
    queue: ApprovalQueue,
    config: Config,
    memory: AccountMemory | None = None,
    schedules: ScheduleStore | None = None,
    escalations: EscalationStore | None = None,
    mcp_servers: MCPServerStore | None = None,
    sweep: SweepStore | None = None,
) -> dict:
    """Everything the page renders, in one dict.

    Stores not passed in are built from the config paths, so a plain
    build_state(queue, config) still covers every view. A missing or
    corrupt store file reads as empty in that store, never a crash:
    each store's loader already degrades that way.
    """
    memory = memory or AccountMemory(config.memory_file)
    schedules = schedules or ScheduleStore(config.schedules_file)
    escalations = escalations or EscalationStore(config.escalations_file)
    mcp_servers = mcp_servers or MCPServerStore(config.mcp_servers_file)
    sweep = sweep or SweepStore(config.sweep_file)
    return {
        "version": __version__,
        "stats": queue_stats(queue),
        "overview": overview_stats(queue, memory, schedules, escalations),
        "sweep": sweep_view(sweep),
        "sources": source_statuses(config),
        "agents": agent_states(queue),
        "queue": queue_view(queue),
        "events": event_log(queue),
        "tokens": token_budget_state(queue, config),
        "memory": memory_view(memory),
        "schedules": schedules_view(schedules),
        "escalations": escalations_view(escalations),
        "writeback": writeback_view(queue),
        "mcp_servers": mcp_servers_view(mcp_servers),
        "stub_mode": config.stub_mode,
        "first_run": is_first_run(
            config, queue, memory, schedules, escalations
        ),
    }


def overview_stats(
    queue: ApprovalQueue,
    memory: AccountMemory,
    schedules: ScheduleStore,
    escalations: EscalationStore,
) -> dict:
    """Second stat strip: the harness beyond the queue.

    - open_escalations: escalation records awaiting a human decision
      (open) or in flight (escalated); resolved records are done.
    - due_schedules: enabled schedules whose next run time has
      arrived.
    - memory_accounts: accounts with at least one memory record.
    - write_drafts_pending: pending drafts that carry a write plan.
    """
    records = escalations.list_all()
    due = sum(1 for s in schedules.list() if is_due(s))
    return {
        "open_escalations": sum(
            1 for r in records if r.status in ("open", "escalated")
        ),
        "due_schedules": due,
        "memory_accounts": len(memory.accounts()),
        "write_drafts_pending": sum(
            1
            for item in queue.list_pending()
            if item.writes
        ),
    }


def memory_view(memory: AccountMemory) -> list[dict]:
    """Memory, per account: records newest first, with the correction
    on edited drafts. One account's records are never mixed with
    another's; the store enforces that, this view just renders it."""
    view = []
    for account, count in memory.accounts():
        view.append(
            {
                "account": account,
                "count": count,
                "records": [
                    {
                        "id": record.id,
                        "agent": record.agent,
                        "outcome": record.outcome,
                        "timestamp": record.timestamp,
                        "excerpt": record.excerpt,
                        "correction": record.correction,
                        "note": record.note,
                    }
                    for record in memory.records_for(account)
                ],
            }
        )
    return view


def schedules_view(store: ScheduleStore) -> list[dict]:
    """Schedules, oldest first, with the next run time computed live.
    A schedule whose stored fields are broken is skipped by the
    store on load, so nothing here can raise on bad data."""
    now = datetime.now(timezone.utc)
    view = []
    for schedule in store.list():
        try:
            reference = (
                _parse_iso(schedule.last_run)
                or _parse_iso(schedule.created_at)
                or now
            )
            next_run = next_run_after(schedule, reference).isoformat(
                timespec="seconds"
            )
        except (ValueError, TypeError):
            next_run = None
        view.append(
            {
                "id": schedule.id,
                "agent": schedule.agent,
                "account": schedule.account,
                "cadence": schedule.cadence,
                "time": schedule.time,
                "timezone": schedule.timezone,
                "weekday": schedule.weekday,
                "text": schedule.text,
                "enabled": schedule.enabled,
                "stub": schedule.stub,
                "created_at": schedule.created_at,
                "last_run": schedule.last_run,
                "last_status": schedule.last_status,
                "last_error": schedule.last_error,
                "next_run": next_run,
                "due": is_due(schedule, now),
            }
        )
    return view


def escalations_view(store: EscalationStore) -> list[dict]:
    """Escalation records, oldest first, exactly as stored."""
    return [record.to_dict() for record in store.list_all()]


def writeback_view(queue: ApprovalQueue) -> list[dict]:
    """Write drafts: queue items that carry a write plan, with their
    execution status. A write draft that was never approved has no
    execution; an approved one shows what applied, op by op."""
    view = []
    for item in queue.list_all():
        if not item.writes:
            continue
        view.append(
            {
                "id": item.id,
                "agent": item.agent,
                "account": _extract_account(item.job),
                "job": item.job,
                "draft": item.draft,
                "status": item.status,
                "created_at": item.created_at,
                "decided_at": item.decided_at,
                "writes": item.writes or [],
                "execution": item.execution or [],
                "executed_at": item.executed_at,
            }
        )
    return view


def mcp_servers_view(store: MCPServerStore) -> list[dict]:
    """Configured external MCP servers. Reachability is not probed
    here: connecting to a server can take seconds, and the state
    endpoint must stay fast. The probe endpoint below connects on
    demand, per server."""
    view = []
    for server in store.list():
        if server.transport == "sse":
            target = server.url
        else:
            target = (server.command + " " + " ".join(server.args)).strip()
        view.append(
            {
                "name": server.name,
                "transport": server.transport,
                "target": target,
                "read_only": server.read_only,
                "created_at": server.created_at,
            }
        )
    return view


def _load_template() -> str:
    with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def create_app(
    config: Config | None = None,
    queue: ApprovalQueue | None = None,
    memory: AccountMemory | None = None,
    schedules: ScheduleStore | None = None,
    escalations: EscalationStore | None = None,
    mcp_servers: MCPServerStore | None = None,
) -> FastAPI:
    """Build the dashboard app. Defaults read the live config and stores.

    Stores passed in are used as-is (tests inject temp-backed ones);
    the rest are built from the config paths and re-read from disk on
    every request, so changes made from the CLI show up without a
    restart."""
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

    def get_memory() -> AccountMemory:
        return memory or AccountMemory(cfg.memory_file)

    def get_schedules() -> ScheduleStore:
        return schedules or ScheduleStore(cfg.schedules_file)

    def get_escalations() -> EscalationStore:
        return escalations or EscalationStore(cfg.escalations_file)

    def get_mcp_servers() -> MCPServerStore:
        return mcp_servers or MCPServerStore(cfg.mcp_servers_file)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _load_template()

    @app.get("/belay-icon.png")
    def icon() -> FileResponse:
        # The app icon: favicon and header image. Served from the
        # bundled asset, so it works from source and from the
        # packaged app.
        path = bundled_icon_path()
        if not path.exists():
            raise HTTPException(status_code=404, detail="Icon not found")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/state")
    def state() -> JSONResponse:
        return JSONResponse(
            build_state(
                get_queue(),
                cfg,
                memory=get_memory(),
                schedules=get_schedules(),
                escalations=get_escalations(),
                mcp_servers=get_mcp_servers(),
            )
        )

    @app.get("/api/setup")
    def get_setup() -> JSONResponse:
        # Settings view (v1.16.0): the LLM choice and connector
        # credentials, with secrets masked. The config is re-read
        # from .env on every call, so a save shows up immediately.
        from harness.setup import _reload_config, setup_state

        return JSONResponse(setup_state(_reload_config(cfg)))

    @app.post("/api/setup/llm")
    def set_setup_llm(body: dict) -> JSONResponse:
        # Apply an LLM choice from Settings: validate, write .env,
        # verify where a check exists. A bad provider or a missing
        # key is a 400, never a silent no-op.
        from harness.setup import _reload_config, apply_llm_choice

        try:
            result = apply_llm_choice(
                _reload_config(cfg),
                str(body.get("provider", "") or ""),
                api_key=body.get("api_key"),
                base_url=body.get("base_url"),
                model=body.get("model"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return JSONResponse(result)

    @app.post("/api/setup/connectors")
    def set_setup_connectors(body: dict) -> JSONResponse:
        # Apply connector credentials from Settings: only known
        # fields are accepted, blanks leave keys unchanged, and
        # secrets go to .env, never back in the response.
        from harness.setup import _reload_config, apply_connector_values

        values = body.get("values") or {}
        if not isinstance(values, dict):
            raise HTTPException(
                status_code=400, detail="values must be an object"
            )
        try:
            result = apply_connector_values(_reload_config(cfg), values)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return JSONResponse(result)

    @app.post("/api/prompt")
    def run_prompt_endpoint(body: dict) -> JSONResponse:
        # Prompt window (v1.15.0, result in place since v1.16.0):
        # free text in, and the result back in the same card. A draft
        # runs through the same execution path as a CLI run, so the
        # draft carries its proof checklist and the approval gate is
        # unchanged. A cross-account question is answered from the
        # queue and memory data, with no new draft. A prompt that
        # names no account comes back as a needs-account reply that
        # asks which account, with a working example: asking is not
        # refusing, and no draft is ever queued for the wrong account.
        # The page never jumps tabs on its own: the result carries a
        # link to the Queue item, and the user follows it.
        text = str(body.get("text", "") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Prompt needs some text")
        try:
            result = run_prompt(
                cfg,
                text,
                queue=get_queue(),
                memory=get_memory(),
                schedules=get_schedules(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except NotImplementedError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        if result["kind"] == "draft":
            return JSONResponse(
                {
                    "kind": "draft",
                    "item": _queue_item_dict(result["item"]),
                    "agent": result["agent"],
                    "display_name": result["display_name"],
                    "route": result["route"],
                    "account": result["account"],
                }
            )
        if result["kind"] == "answer":
            return JSONResponse(
                {
                    "kind": "answer",
                    "answer": result["answer"],
                    "accounts": result["accounts"],
                    "item_ids": result["item_ids"],
                }
            )
        return JSONResponse(
            {
                "kind": "needs_account",
                "message": result["message"],
                "known_accounts": result.get("known_accounts", []),
                "candidates": result.get("candidates", []),
                "example": result["example"],
            }
        )

    @app.post("/api/sweep")
    def run_sweep_endpoint() -> JSONResponse:
        # Risk sweep (v0.18.2): run risk-watcher's signal logic
        # across the whole book, rank it, and store it for the
        # Overview page. Runs on demand, not on every state call:
        # the state endpoint only reads the stored result, so the
        # 30-second poll stays fast. The sweep itself takes as long
        # as the book takes to gather (nine calls per account); a
        # book of ten stub accounts is seconds, a live book longer.
        # The result is stored before it is returned, so a refresh
        # right after shows the same sweep.
        from harness.run import build_registry

        try:
            result = run_sweep(build_registry(cfg), cfg)
        except Exception as exc:  # noqa: BLE001 - reported, never a stack trace
            raise HTTPException(status_code=500, detail=str(exc))
        SweepStore(cfg.sweep_file).save(result)
        return JSONResponse(result)

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

    @app.post("/api/approvals/{item_id}/edit")
    def edit(item_id: str, body: dict) -> JSONResponse:
        # The edit is recorded as memory with the correction itself,
        # through the same ApprovalQueue.edit the CLI uses, so the next
        # run for the account learns what the human changed. Status
        # becomes edited: it still needs a final approve before use.
        new_draft = str(body.get("draft", "") or "").strip()
        if not new_draft:
            raise HTTPException(
                status_code=400, detail="Edit needs a non-empty draft"
            )
        try:
            item = get_queue().edit(item_id, new_draft)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown approval item")
        return JSONResponse(item.to_dict())

    @app.post("/api/memory/{account}/clear")
    def clear_memory(account: str) -> JSONResponse:
        removed = get_memory().clear(account)
        return JSONResponse({"account": account, "removed": removed})

    @app.post("/api/schedules")
    def add_schedule(body: dict) -> JSONResponse:
        try:
            schedule = get_schedules().add(
                body, valid_agents=set(AGENTS)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return JSONResponse(schedule.to_dict())

    @app.delete("/api/schedules/{schedule_id}")
    def remove_schedule(schedule_id: str) -> JSONResponse:
        if not get_schedules().remove(schedule_id):
            raise HTTPException(status_code=404, detail="Unknown schedule")
        return JSONResponse({"removed": schedule_id})

    @app.post("/api/escalations/{record_id}/resolve")
    def resolve_escalation(
        record_id: str, body: dict | None = None
    ) -> JSONResponse:
        store = get_escalations()
        if store.get(record_id) is None:
            raise HTTPException(status_code=404, detail="Unknown escalation")
        note = (body or {}).get("note") or None
        if not store.resolve(record_id, note=note):
            raise HTTPException(
                status_code=400,
                detail="Escalation %s is already resolved" % record_id,
            )
        return JSONResponse(store.get(record_id).to_dict())

    @app.post("/api/demo/seed")
    def seed_demo_endpoint() -> JSONResponse:
        # Fill the stores with demo data from the dashboard banner
        # (v1.12.0): same seed the CLI runs, never over real data.
        # A refusal is a 409: the stores already hold real records.
        # Non-interactive: no model pull offer, stub fallback when
        # Ollama is not reachable, said plainly in the report.
        report = seed_demo(cfg, confirm=False, interactive=False)
        if report["status"] == "refused":
            raise HTTPException(
                status_code=409,
                detail="Stores already hold real data: %s"
                % ", ".join(
                    "%s: %d" % kv
                    for kv in sorted(report.get("real", {}).items())
                ),
            )
        return JSONResponse(report)

    @app.post("/api/mcp-servers")
    def add_mcp_server(body: dict) -> JSONResponse:
        try:
            server = get_mcp_servers().add(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return JSONResponse(server.to_dict())

    @app.delete("/api/mcp-servers/{name}")
    def remove_mcp_server(name: str) -> JSONResponse:
        if not get_mcp_servers().remove(name):
            raise HTTPException(status_code=404, detail="Unknown MCP server")
        return JSONResponse({"removed": name})

    @app.get("/api/mcp-servers/{name}/tools")
    def probe_mcp_server(name: str) -> JSONResponse:
        # Connect on demand, in a worker thread with a bound, so a
        # dead server cannot hang the dashboard. A server that fails
        # to connect is reported, never fatal.
        server = get_mcp_servers().get(name)
        if server is None:
            raise HTTPException(status_code=404, detail="Unknown MCP server")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                MCPClient(server).list_tools_sync
            )
            try:
                tools = future.result(timeout=15)
            except TimeoutError:
                return JSONResponse(
                    {
                        "name": name,
                        "status": "unreachable",
                        "error": "probe timed out after 15s",
                    }
                )
            except Exception as exc:  # noqa: BLE001 - reported, never fatal
                return JSONResponse(
                    {
                        "name": name,
                        "status": "unreachable",
                        "error": str(exc),
                    }
                )
        return JSONResponse(
            {"name": name, "status": "ok", "tools": tools}
        )

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
