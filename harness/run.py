"""Command-line entry point for the harness.

Usage:
    python -m harness.run run --agent renewal-desk --account "Acme Corp"
    python -m harness.run run --text "draft a renewal plan for Acme Corp"
    python -m harness.run run --agent briefer --account "Acme Corp" --stub
    python -m harness.run run --agent renewal-desk --account "Acme Corp" --no-memory
    python -m harness.run run --agent salesforce-updater --account "Acme Corp" --stub --change "stage 006STUB000000001 Closed Won"
    python -m harness.run agents
    python -m harness.run tools
    python -m harness.run approvals list
    python -m harness.run approvals show <id>
    python -m harness.run approvals approve <id> [--note "..."]
    python -m harness.run approvals reject <id> [--note "..."]
    python -m harness.run approvals edit <id> --draft "..." | --file <path>
    python -m harness.run memory show "Acme Corp"
    python -m harness.run memory clear "Acme Corp"
    python -m harness.run memory accounts
    python -m harness.run schedules list
    python -m harness.run schedules add --agent briefer --account "Acme Corp" --cadence daily --time 07:00 --timezone America/Los_Angeles
    python -m harness.run schedules remove <id>
    python -m harness.run scheduler --once
    python -m harness.run scheduler
    python -m harness.run mcp-servers list
    python -m harness.run mcp-servers add --name gmail --stdio "python -m connectors.gmail.server"
    python -m harness.run mcp-servers add --name docs --sse https://example.com/mcp
    python -m harness.run mcp-servers remove gmail
    python -m harness.run escalations list
    python -m harness.run escalations show <id>
    python -m harness.run escalations resolve <id> [--note "..."]
    python -m harness.run sweep [--stub]
    python -m harness.run setup [--demo | --wizard | --clear-demo]
    python -m harness.run dashboard [--port 8000]
    python -m harness.run launch [--port 8000]
    python -m harness.run

A bare run with no subcommand launches the dashboard (v1.16.0):
the GUI is the default first run, the CLI is the power-user path.

The `run` command routes the job, runs the agent, and submits the draft to
the approval queue. Nothing is sent anywhere. A human approves it with
`approvals approve` before it leaves the harness.

Every decision in the queue is recorded as memory for the draft's
account (memory.json, HARNESS_MEMORY_FILE to move it): approvals,
rejections, and edits. An edit stores the correction itself. The next
run for the same account loads that memory as context, newest first,
bounded by the token budget. --no-memory runs cold. `memory show` and
`memory clear` inspect and forget one account's memory; memory never
crosses accounts.

--stub forces stub mode for all connectors and the LLM, so the full loop
runs with no credentials and no network calls.

Scheduled runs (`scheduler --once`, or the `scheduler` daemon) use the
same execution path as a manual `run`: they draft into the approval
queue only, never execute a write, and respect the same token budget
and memory controls. A failed scheduled run is logged on its schedule
and does not block the next job.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import threading
from dataclasses import replace

from connectors.csv.tools import csv_crm_tools, csv_tools
from connectors.gcal.tools import gcal_tools
from connectors.gmail.tools import gmail_tools
from connectors.gong.tools import gong_tools
from connectors.hubspot.tools import hubspot_tools
from connectors.linear.tools import linear_tools
from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from connectors.zendesk.tools import zendesk_tools
from connectors.zoom.tools import zoom_tools

from harness import (
    AGENTS,
    AccountMemory,
    ApprovalQueue,
    Config,
    EscalationStore,
    Router,
    TokenBudget,
    ToolRegistry,
    __version__,
    get_llm,
)
from harness.llm import StubLLM
from harness.scheduler import (
    ScheduleStore,
    SchedulerAlreadyRunning,
    install_stop_handlers,
    run_daemon,
    run_due,
    scheduler_lock,
)
from harness.mcp_client import (
    MCPClient,
    MCPServerStore,
    load_external_tools,
)
from harness.paths import cli_invocation
from harness.setup import (
    clear_demo,
    print_setup_hint,
    prompt_setup_choice,
    run_setup_wizard,
    seed_demo,
)
from harness.writeback import (
    execute_writes,
    format_write_plan,
    parse_change_spec,
)


def build_registry(config: Config) -> ToolRegistry:
    """Register the connector tools (Salesforce + Slack + Gong + HubSpot
    + Gmail + Zoom + Zendesk + Linear + Google Calendar + CSV), then the
    external MCP servers the user has configured (v1.5.0).

    The CRM the agents gather from is Salesforce unless HubSpot is
    configured and Salesforce is not; then it is HubSpot. When neither
    is configured and a CSV accounts file is, the CSV file is the CRM
    (v0.18.1): the agents' account-record calls are served from the
    file, so the harness runs without live customer accounts. When
    both are configured, Salesforce wins. A registry built without a
    config defaults to Salesforce.

    External servers load from the server store file
    (HARNESS_MCP_SERVERS_FILE, default mcp-servers.json). A missing
    file means no external servers, so the demo and the tests pay
    nothing. A configured server that fails to connect is reported on
    stderr and skipped; it never breaks the registry build.
    """
    if config.hubspot_configured() and not config.salesforce_configured():
        crm = "hubspot"
    elif (
        config.csv_configured()
        and not config.salesforce_configured()
        and not config.hubspot_configured()
    ):
        crm = "csv"
    else:
        crm = "salesforce"
    registry = ToolRegistry(crm=crm)
    for tool in salesforce_tools(config):
        registry.register(tool)
    for tool in slack_tools(config):
        registry.register(tool)
    for tool in gong_tools(config):
        registry.register(tool)
    for tool in hubspot_tools(config):
        registry.register(tool)
    for tool in gmail_tools(config):
        registry.register(tool)
    for tool in zoom_tools(config):
        registry.register(tool)
    for tool in zendesk_tools(config):
        registry.register(tool)
    for tool in linear_tools(config):
        registry.register(tool)
    for tool in gcal_tools(config):
        registry.register(tool)
    for tool in csv_tools(config):
        registry.register(tool)
    if crm == "csv":
        # The CSV file is the CRM: the account-record tools agents
        # call (salesforce_get_account and friends) are served from
        # the file, so every agent gathers real file data with no
        # agent changes. Re-registering replaces the Salesforce
        # handlers; the results carry source=csv, so the provenance
        # is explicit in every result.
        for tool in csv_crm_tools(config):
            registry.register(tool)
    try:
        store = MCPServerStore(config.mcp_servers_file)
        report = load_external_tools(registry, store)
    except Exception as exc:  # noqa: BLE001 - a bad store never breaks runs
        print("MCP servers: could not load %s: %s" % (config.mcp_servers_file, exc), file=sys.stderr)
        return registry
    for failure in report["failures"]:
        print(
            "MCP server %r unreachable, skipped: %s"
            % (failure["server"], failure["error"]),
            file=sys.stderr,
        )
    return registry


def cmd_setup(args: argparse.Namespace) -> int:
    """First run: demo data or the setup wizard (v1.12.0).

    --demo fills the stores with demo data (refuses over real
    data unless --yes). --wizard walks the LLM provider choice and
    the ten connectors, writing .env. --clear-demo removes only the seeded
    records. With no flag and an interactive terminal, it asks the
    one question with two answers; without a terminal it says so
    and exits 2."""
    config = Config.from_env()
    if args.clear_demo:
        report = clear_demo(config)
        return 0
    if args.demo:
        report = seed_demo(
            config, confirm=bool(args.yes), interactive=sys.stdin.isatty()
        )
        if report["status"] == "seeded":
            return 0
        if report["status"] == "already-seeded":
            print(
                "Demo data is already seeded. Clear it first with: "
                "%s setup --clear-demo" % cli_invocation()
            )
            return 0
        return 1
    if args.wizard:
        run_setup_wizard(config, env_path=config.env_path)
        _create_launcher_after_wizard()
        return 0
    if not sys.stdin.isatty():
        print("First run. Two ways in:")
        print("  1) Fill with Demo data  (--demo)")
        print("  2) Run Setup Wizard     (--wizard)")
        print("Not an interactive terminal: re-run with --demo or --wizard.")
        return 2
    choice = prompt_setup_choice()
    if choice == "demo":
        report = seed_demo(config)
        if report["status"] == "seeded":
            return 0
        if report["status"] == "already-seeded":
            print(
                "Demo data is already seeded. Clear it first with: "
                "%s setup --clear-demo" % cli_invocation()
            )
            return 0
        return 1
    run_setup_wizard(config, env_path=config.env_path)
    _create_launcher_after_wizard()
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve the dashboard on localhost (reads the live approval queue)."""
    from harness.dashboard import main as dashboard_main

    return dashboard_main(["--port", str(args.port)])


def cmd_launch(args: argparse.Namespace) -> int:
    """Double-click launch (v1.16.0): start the dashboard server and
    open it in the browser, with no terminal interaction needed.
    Blocks until the server stops (Ctrl-C). A bare `belay` with no
    subcommand runs this too: the GUI is the default, the CLI is the
    power-user path."""
    from harness.launcher import launch_dashboard

    print(
        "Belay dashboard v%s on http://127.0.0.1:%d"
        % (__version__, args.port)
    )
    print("Close this window to stop Belay, or press Ctrl-C.")
    result = launch_dashboard(port=args.port, open_browser=True)
    if not result["up"]:
        print(
            "The dashboard did not start on port %d." % args.port,
            file=sys.stderr,
        )
        return 1
    try:
        result["thread"].join()
    except KeyboardInterrupt:
        print("\nBelay stopped.")
    return 0


def _create_launcher_after_wizard() -> None:
    """At the end of the CLI wizard, create the desktop launcher
    automatically, so the user never hand-writes one. Best effort:
    a failure is reported, never fatal."""
    from harness.launcher import create_launcher

    report = create_launcher()
    if report["created"]:
        print("Desktop launcher created: %s" % report["path"])
    else:
        print(
            "Desktop launcher not created: %s"
            % (report["error"] or "unknown reason")
        )


def execute_job(config: Config, job: dict):
    """Route a job, run the agent, submit the draft to the queue.

    The one execution path for a manual `run` and for scheduled runs:
    both draft into the approval queue only, nothing executes here.
    Returns (item, draft, agent, route). Raises ValueError when routing
    fails and NotImplementedError when the agent is not implemented.
    `job` must name the agent (or free text the router can route) and
    the account; memory_file and approvals_file default from the config
    when the job does not carry them. For salesforce-updater, the job's
    `changes` must already be parsed change specs (cmd_run and the
    scheduler parse them before calling here, so a bad spec fails
    before the agent runs)."""
    registry = build_registry(config)
    llm = get_llm(config)
    router = Router(
        agents=AGENTS,
        # The router uses the LLM when it is a real client (Ollama or
        # a key provider); a stub would return stub text, not a route.
        llm=llm if not isinstance(llm, StubLLM) else None,
        router_model=config.effective_router_model,
    )
    route = router.route(job)
    if route.error:
        raise ValueError(route.error)
    agent = AGENTS[route.agent]
    # Memory travels with the job: the memory file for injection, and
    # the opt-out flag. Every agent loads the account's memory from
    # it (bounded by the budget); --no-memory runs cold.
    job.setdefault("memory_file", config.memory_file)
    if route.agent in (
        "value-finder",
        "qbr-builder",
        "salesforce-updater",
        "risk-watcher",
        "escalation-manager",
        "competitive-defense",
        "stakeholder-mapper",
        "case-triage",
        "call-recap-and-commitments",
    ):
        # Value-finder and qbr-builder read prior drafts from the
        # approval queue (value-finder synthesizes them, qbr-builder
        # composes from them); salesforce-updater reads them for
        # context on what needs updating; risk-watcher,
        # escalation-manager, competitive-defense,
        # stakeholder-mapper, and case-triage compose from them;
        # call-recap-and-commitments reads its own prior recaps to
        # check whether an old commitment was kept or is still open.
        # The queue path travels with the job.
        job.setdefault("approvals_file", config.approvals_file)
    if route.agent == "escalation-manager":
        # The escalation store travels with the job too: the agent
        # reads and writes it, and the draft ids are linked to its
        # records after the draft is queued below.
        job.setdefault("escalations_file", config.escalations_file)
    if route.agent == "value-finder":
        # Value-finder quantifies time saved from explicit inputs only:
        # the loaded hourly rate comes from the job, or from
        # HARNESS_LOADED_HOURLY_RATE. It is never assumed.
        if "hourly_rate" not in job and config.loaded_hourly_rate is not None:
            job["hourly_rate"] = config.loaded_hourly_rate
            job["hourly_rate_source"] = "HARNESS_LOADED_HOURLY_RATE"
    budget = TokenBudget(config.token_budget_per_job)
    draft = agent.run(job, registry, llm, budget)
    queue = ApprovalQueue(config.approvals_file)
    item = queue.submit(
        agent=agent.name,
        job=job,
        draft=draft.text,
        sources=draft.sources,
        links=draft.links,
        proof=draft.proof,
        writes=draft.writes,
    )
    if route.agent == "escalation-manager" and job.get("escalation_records"):
        # Link the new escalation records to this draft, so approving
        # it moves them to escalated. State tracking never breaks the
        # queue path: a failed link is swallowed here.
        try:
            store = EscalationStore(
                job.get("escalations_file") or config.escalations_file
            )
            for record_id in job["escalation_records"]:
                store.set_draft(str(record_id), item.id)
        except Exception:
            pass
    return item, draft, agent, route


def cmd_run(args: argparse.Namespace) -> int:
    """Route a job, run the agent, submit the draft to the queue."""
    config = Config.from_env()
    if args.stub:
        config.stub_mode = True
    else:
        # First run, interactively: one line pointing at setup, then
        # the run goes ahead anyway. The wizard never blocks a run.
        print_setup_hint(config, interactive=sys.stdin.isatty())
    job: dict = {"text": args.text or ""}
    if args.agent:
        job["agent"] = args.agent
    if args.account:
        job["account"] = args.account
        if not job["text"]:
            job["text"] = args.account
    if args.no_memory:
        job["no_memory"] = True
    if args.change:
        # The updater drafts only the changes the job names (--change,
        # repeatable). A bad spec fails fast here, before the agent
        # runs, so a typo never becomes a draft. Other agents ignore
        # the key; the updater reads it when the job routes there.
        specs = []
        for raw in args.change:
            try:
                specs.append(parse_change_spec(raw))
            except ValueError as exc:
                print("Bad --change spec %r: %s" % (raw, exc), file=sys.stderr)
                return 2
        job["changes"] = specs
    if args.hourly_rate is not None:
        job["hourly_rate"] = args.hourly_rate
        job["hourly_rate_source"] = "CLI --hourly-rate"
    if args.hours_saved is not None:
        job["hours_saved"] = args.hours_saved
    try:
        item, draft, agent, route = execute_job(config, job)
    except ValueError as exc:
        print("Routing failed: %s" % exc, file=sys.stderr)
        return 1
    except NotImplementedError as exc:
        print("Agent error: %s" % exc, file=sys.stderr)
        return 1
    print("Routed to %s (%s, %s)." % (agent.display_name, route.agent, route.reason))
    print("Draft submitted to the approval queue: %s" % item.id)
    print("Sources: %s" % (", ".join(draft.sources) or "none"))
    if draft.links:
        print("Source links:")
        for link in draft.links:
            print("  %s: %s" % (link["label"], link["url"]))
    if draft.proof:
        for p in draft.proof:
            if not p.get("passed"):
                print("  FAIL: %s (%s)" % (p.get("label"), p.get("detail")))
    print("Estimated tokens: %d (budget %d)" % (draft.tokens_estimate, config.token_budget_per_job))
    if args.show:
        print("--- draft ---")
        print(draft.text)
        print("--- end draft ---")
    print("Review it with: %s approvals show %s" % (cli_invocation(), item.id))
    return 0


def cmd_agents(_args: argparse.Namespace) -> int:
    """List the nineteen agents and whether each is implemented in v1."""
    for name in sorted(AGENTS):
        agent = AGENTS[name]
        status = "implemented" if agent.implemented else "planned"
        print("%-20s %-12s %s" % (name, status, agent.description))
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    """List the registered connector tools."""
    config = Config.from_env()
    if args.stub:
        config.stub_mode = True
    registry = build_registry(config)
    for tool in registry.list_tools():
        print("%-32s %s" % (tool.name, tool.description))
    return 0


def cmd_approvals(args: argparse.Namespace) -> int:
    """Manage the approval queue.

    The queue is built with the memory store attached, so every
    decision here (approve, reject, edit) is recorded as memory for
    the draft's account. The escalation store is attached too, so
    approving an escalation-manager draft moves its records to
    escalated and resolves the ones its closure section names."""
    config = Config.from_env()
    queue = ApprovalQueue(
        config.approvals_file,
        memory=AccountMemory(config.memory_file),
        escalations=EscalationStore(config.escalations_file),
    )
    if args.action == "list":
        items = queue.list_pending()
        if not items:
            print("No pending drafts.")
            return 0
        for item in items:
            print("%s  %-14s %s  (created %s)" % (item.id, item.agent, item.status, item.created_at))
        return 0
    if args.action == "show":
        item = queue.get(args.item_id)
        if item is None:
            print("Unknown approval item: %s" % args.item_id, file=sys.stderr)
            return 1
        print("ID: %s" % item.id)
        print("Agent: %s" % item.agent)
        print("Status: %s" % item.status)
        print("Created: %s" % item.created_at)
        if item.decided_at:
            print("Decided: %s" % item.decided_at)
        if item.note:
            print("Note: %s" % item.note)
        print("Sources: %s" % (", ".join(item.sources) or "none"))
        if item.links:
            print("Source links:")
            for link in item.links:
                print("  %s: %s" % (link["label"], link["url"]))
        if item.proof:
            print("Completeness check:")
            for p in item.proof:
                mark = "PASS" if p.get("passed") else "FAIL"
                print("  %s: %s (%s)" % (mark, p.get("label"), p.get("detail")))
        print("Job: %s" % json.dumps(item.job))
        if item.writes:
            # A write draft shows its plan, so the human sees exactly
            # what approving will run. Editing the draft text does not
            # change this plan.
            print(format_write_plan(item.writes))
        if item.execution:
            print("Execution (%s):" % (item.executed_at or "recorded"))
            for result in item.execution:
                mark = "APPLIED" if result.get("success") else "FAILED"
                detail = result.get("error") or result.get("tool")
                print("  %s: op %s (%s)" % (mark, result.get("op_index"), detail))
        print("--- draft ---")
        print(item.draft)
        print("--- end draft ---")
        return 0
    if args.action == "approve":
        item = queue.approve(args.item_id, note=args.note)
        print("Approved: %s" % item.id)
        if item.writes:
            # Approval-gated execution (v1.3.0): only an approved write
            # draft executes, and only through the executor. The plan
            # prints first, so the human sees what is about to run.
            print(format_write_plan(item.writes))
            registry = build_registry(config)
            results = execute_writes(queue, registry, item)
            for result in results:
                if result.get("skipped"):
                    print(
                        "  Already applied op %s (%s), not re-run"
                        % (result.get("op_index"), result.get("tool"))
                    )
                elif result.get("success"):
                    print(
                        "  Applied op %s (%s)"
                        % (result.get("op_index"), result.get("tool"))
                    )
                else:
                    print(
                        "  FAILED op %s (%s): %s"
                        % (
                            result.get("op_index"),
                            result.get("tool"),
                            result.get("error"),
                        )
                    )
            if any(not r.get("success") for r in results):
                print(
                    "Some writes failed; the applied ones will not run "
                    "again. Approve again to retry the failed ones.",
                    file=sys.stderr,
                )
                return 1
        return 0
    if args.action == "reject":
        item = queue.reject(args.item_id, note=args.note)
        print("Rejected: %s" % item.id)
        return 0
    if args.action == "edit":
        if args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                new_draft = f.read()
        elif args.draft:
            new_draft = args.draft
        else:
            print(
                "approvals edit needs --draft \"...\" or --file <path>.",
                file=sys.stderr,
            )
            return 1
        item = queue.edit(args.item_id, new_draft)
        print("Edited: %s" % item.id)
        return 0
    print("Unknown approvals action: %s" % args.action, file=sys.stderr)
    return 1


def cmd_memory(args: argparse.Namespace) -> int:
    """Inspect or forget one account's memory.

    show prints the account's records, newest first, with the
    correction on edited drafts. clear forgets that account only.
    accounts lists every account with memory."""
    config = Config.from_env()
    memory = AccountMemory(config.memory_file)
    if args.action == "show":
        records = memory.records_for(args.account)
        if not records:
            print("No memory for %s." % args.account)
            return 0
        print("%d record(s) for %s:" % (len(records), args.account))
        for record in records:
            print("- " + record.render().replace("\n", "\n  "))
        return 0
    if args.action == "clear":
        removed = memory.clear(args.account)
        print("Forgot %d record(s) for %s." % (removed, args.account))
        return 0
    if args.action == "accounts":
        entries = memory.accounts()
        if not entries:
            print("No memory stored.")
            return 0
        for account, count in entries:
            print("%-30s %d record(s)" % (account, count))
        return 0
    print("Unknown memory action: %s" % args.action, file=sys.stderr)
    return 1


def job_from_schedule(schedule, config: Config) -> dict:
    """Build the job dict for one scheduled run. Same fields a manual
    `run` builds. A bad --change spec raises ValueError: the run is
    logged as failed, never fatal to the scheduler."""
    job: dict = {
        "agent": schedule.agent,
        "account": schedule.account,
        "text": schedule.text or schedule.account,
    }
    if schedule.no_memory:
        job["no_memory"] = True
    if schedule.agent == "salesforce-updater":
        job["changes"] = [parse_change_spec(raw) for raw in schedule.changes]
    if schedule.agent == "value-finder":
        if schedule.hourly_rate is not None:
            job["hourly_rate"] = schedule.hourly_rate
            job["hourly_rate_source"] = "schedule"
        if schedule.hours_saved is not None:
            job["hours_saved"] = schedule.hours_saved
    return job


def _scheduled_executor(schedule, config: Config) -> str:
    """Run one scheduled job through the same path as a manual `run`
    and return the queued draft's id. Drafts only: nothing here
    executes a write, and the approval gate still applies."""
    run_config = replace(config, stub_mode=True) if schedule.stub else config
    job = job_from_schedule(schedule, run_config)
    item, _draft, _agent, _route = execute_job(run_config, job)
    return item.id


def cmd_schedules(args: argparse.Namespace) -> int:
    """List, add, or remove scheduled jobs."""
    config = Config.from_env()
    store = ScheduleStore(config.schedules_file)
    if args.action == "list":
        schedules = store.list()
        if not schedules:
            print(
                "No schedules. Add one with: %s "
                "schedules add --agent briefer --account \"Acme Corp\" "
                "--cadence daily --time 07:00 --timezone America/Los_Angeles"
                % cli_invocation()
            )
            return 0
        for schedule in schedules:
            print(schedule.describe())
        return 0
    if args.action == "add":
        data = {
            "agent": args.agent,
            "account": args.account,
            "cadence": args.cadence,
            "time": args.time,
            "timezone": args.timezone,
            "weekday": args.weekday,
            "text": args.text,
            "hourly_rate": args.hourly_rate,
            "hours_saved": args.hours_saved,
            "changes": list(args.change or []),
            "no_memory": args.no_memory,
            "stub": args.stub,
        }
        try:
            schedule = store.add(data, valid_agents=set(AGENTS))
        except ValueError as exc:
            print("Bad schedule: %s" % exc, file=sys.stderr)
            return 2
        print("Scheduled: %s" % schedule.describe())
        return 0
    if args.action == "remove":
        if store.remove(args.schedule_id):
            print("Removed schedule %s." % args.schedule_id)
            return 0
        print("Unknown schedule: %s" % args.schedule_id, file=sys.stderr)
        return 1
    print("Unknown schedules action: %s" % args.action, file=sys.stderr)
    return 1


def cmd_scheduler(args: argparse.Namespace) -> int:
    """Run due scheduled jobs: once, or as a daemon.

    --once runs every due job now and exits: 0 when every due job
    ran, 1 when any due job failed, 2 when another scheduler already
    holds the lock. The daemon holds the lock for its whole run and
    stops cleanly on Ctrl-C (SIGINT/SIGTERM)."""
    config = Config.from_env()
    store = ScheduleStore(config.schedules_file)
    if args.once:
        try:
            with scheduler_lock(store.path + ".lock"):
                results = run_due(store, config, _scheduled_executor)
        except SchedulerAlreadyRunning as exc:
            print("Scheduler: %s" % exc, file=sys.stderr)
            return 2
        failed = 0
        for result in results:
            if result.status == "ok":
                print(
                    "Ran %s for %s (%s): draft %s"
                    % (
                        result.agent,
                        result.account,
                        result.schedule_id,
                        result.item_id,
                    )
                )
            elif result.status == "failed":
                print(
                    "FAILED %s for %s (%s): %s"
                    % (
                        result.agent,
                        result.account,
                        result.schedule_id,
                        result.error,
                    ),
                    file=sys.stderr,
                )
                failed += 1
            else:
                print(
                    "Skipped %s for %s (%s): %s"
                    % (
                        result.agent,
                        result.account,
                        result.schedule_id,
                        result.reason,
                    )
                )
        return 1 if failed else 0
    stop = threading.Event()
    install_stop_handlers(stop)
    print(
        "Scheduler running (schedules file: %s). Ctrl-C to stop."
        % config.schedules_file
    )
    return run_daemon(
        store, config, _scheduled_executor, interval=args.interval, stop=stop
    )


def cmd_mcp_servers(args: argparse.Namespace) -> int:
    """List, add, or remove external MCP servers (v1.5.0).

    list connects to each configured server and reports its tools, or
    the reason it could not be reached. A server that fails to connect
    is reported and skipped, never fatal. add stores a stdio server
    (--stdio "command args...") or an SSE server (--sse <url>); --env
    KEY=VALUE and --header "KEY: VALUE" are repeatable."""
    config = Config.from_env()
    store = MCPServerStore(config.mcp_servers_file)
    if args.action == "list":
        servers = store.list()
        if not servers:
            print(
                "No MCP servers configured. Add one with: %s "
                "mcp-servers add --name gmail --stdio "
                "\"python -m connectors.gmail.server\""
                % cli_invocation()
            )
            return 0
        for server in servers:
            try:
                tools = MCPClient(server).list_tools_sync()
                print(
                    "%s  ok (%d tool(s): %s)"
                    % (
                        server.describe(),
                        len(tools),
                        ", ".join(t["name"] for t in tools) or "none",
                    )
                )
            except Exception as exc:  # noqa: BLE001 - reported, never fatal
                print("%s  unreachable: %s" % (server.describe(), exc))
        return 0
    if args.action == "add":
        data: dict = {"name": args.name}
        if args.stdio:
            parts = shlex.split(args.stdio)
            data["transport"] = "stdio"
            data["command"] = parts[0] if parts else ""
            data["args"] = parts[1:]
        elif args.sse:
            data["transport"] = "sse"
            data["url"] = args.sse
        env: dict = {}
        for raw in args.env or []:
            if "=" not in raw:
                print("--env needs KEY=VALUE form, got %r." % raw, file=sys.stderr)
                return 2
            key, _, value = raw.partition("=")
            env[key] = value
        data["env"] = env
        headers: dict = {}
        for raw in args.header or []:
            if ":" not in raw:
                print('--header needs "KEY: VALUE" form, got %r.' % raw, file=sys.stderr)
                return 2
            key, _, value = raw.partition(":")
            headers[key.strip()] = value.strip()
        data["headers"] = headers
        data["read_only"] = bool(args.read_only)
        if args.cwd:
            data["cwd"] = args.cwd
        try:
            server = store.add(data)
        except ValueError as exc:
            print("Bad server: %s" % exc, file=sys.stderr)
            return 2
        print("MCP server added: %s" % server.describe())
        return 0
    if args.action == "remove":
        if store.remove(args.server_name):
            print("Removed MCP server %s." % args.server_name)
            return 0
        print("Unknown MCP server: %s" % args.server_name, file=sys.stderr)
        return 1
    print("Unknown mcp-servers action: %s" % args.action, file=sys.stderr)
    return 1


def cmd_escalations(args: argparse.Namespace) -> int:
    """List, show, or resolve escalation records (v1.7.0).

    list shows every record, or one account's with --account. show
    prints one record in full. resolve is the human decision that
    closes an escalation: it marks the record resolved, and the next
    run for the account will not re-escalate that signal."""
    config = Config.from_env()
    store = EscalationStore(config.escalations_file)
    if args.action == "list":
        if args.account:
            records = store.for_account(args.account)
        else:
            records = store.list_all()
        if not records:
            print("No escalations on file.")
            return 0
        for record in records:
            print(record.describe())
        return 0
    if args.action == "show":
        record = store.get(args.record_id)
        if record is None:
            print("Unknown escalation: %s" % args.record_id, file=sys.stderr)
            return 1
        print("ID: %s" % record.id)
        print("Account: %s" % record.account)
        print("Signal: %s (%s, %s)" % (record.signal_key, record.signal_kind, record.severity))
        print("Status: %s" % record.status)
        print("Created: %s" % record.created_at)
        print("Updated: %s" % record.updated_at)
        if record.draft_id:
            print("Draft: %s" % record.draft_id)
        if record.resolved_at:
            print("Resolved: %s" % record.resolved_at)
        if record.note:
            print("Note: %s" % record.note)
        print("Summary: %s" % record.summary)
        return 0
    if args.action == "resolve":
        if store.resolve(args.record_id, note=args.note):
            print("Resolved escalation %s." % args.record_id)
            return 0
        record = store.get(args.record_id)
        if record is None:
            print("Unknown escalation: %s" % args.record_id, file=sys.stderr)
        else:
            print(
                "Escalation %s is already %s."
                % (args.record_id, record.status),
                file=sys.stderr,
            )
        return 1
    print("Unknown escalations action: %s" % args.action, file=sys.stderr)
    return 1


def cmd_sweep(args: argparse.Namespace) -> int:
    """Sweep the whole book for risk, rank it, store it for the
    Overview page. Prints the ranked table: severity, account,
    renewal date, top signal. No drafts are queued and no LLM is
    called: the sweep is the harness-computed signals, ranked. A
    book with no accounts prints the gaps and exits 0; an empty
    book is a state to report, not a failure."""
    from harness.sweep import SweepStore, run_sweep

    config = Config.from_env()
    if args.stub:
        config.stub_mode = True
    registry = build_registry(config)
    result = run_sweep(registry, config)
    SweepStore(config.sweep_file).save(result)
    if not result["results"]:
        print("Sweep found no accounts.")
        for gap in result["gaps"]:
            print("  Gap: %s" % gap)
        return 0
    print(
        "Risk sweep: %d account(s), worst first (ran %s)."
        % (result["accounts"], result["ran_at"])
    )
    for row in result["results"]:
        renewal = row["renewal_date"] or "no renewal date"
        print(
            "  [%-6s] %-28s %s  %s"
            % (row["severity"], row["account"], renewal, row["top_signal"])
        )
    for gap in result["gaps"]:
        print("  Gap: %s" % gap)
    print("Saved to %s." % config.sweep_file)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cs-harness",
        description="CS agent harness: router + agents + approval queue.",
    )
    parser.add_argument(
        "--version", action="version", version="Belay %s" % __version__
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Route a job, run an agent, queue the draft.")
    run_p.add_argument("--agent", help="Agent name, e.g. renewal-desk, briefer.")
    run_p.add_argument("--account", help="Account name or id.")
    run_p.add_argument("--text", help="Free-text job description.")
    run_p.add_argument("--stub", action="store_true", help="Force stub mode (no creds, no network).")
    run_p.add_argument("--show", action="store_true", help="Print the draft after queueing it.")
    run_p.add_argument("--hourly-rate", type=float, default=None, help="Loaded hourly rate in dollars, for value-finder (overrides HARNESS_LOADED_HOURLY_RATE).")
    run_p.add_argument("--hours-saved", type=float, default=None, help="Hours saved by automation, for value-finder (supplied by the CSM; never assumed).")
    run_p.add_argument("--change", action="append", default=None, help="Change for salesforce-updater, repeatable. Forms: 'update <Object> <id> <Field>=<value>[, ...]', 'stage <id> <StageName>', 'log <id> <Subject> [| <Description>]'.")
    run_p.add_argument("--no-memory", action="store_true", help="Run cold: do not load the account's memory as context.")
    run_p.set_defaults(func=cmd_run)

    agents_p = sub.add_parser("agents", help="List the nineteen agents.")
    agents_p.set_defaults(func=cmd_agents)

    tools_p = sub.add_parser("tools", help="List registered connector tools.")
    tools_p.add_argument("--stub", action="store_true", help="Force stub mode.")
    tools_p.set_defaults(func=cmd_tools)

    appr_p = sub.add_parser("approvals", help="Manage the approval queue.")
    appr_p.add_argument("action", choices=["list", "show", "approve", "reject", "edit"])
    appr_p.add_argument("item_id", nargs="?", help="Approval item id.")
    appr_p.add_argument("--note", help="Note to attach to the decision.")
    appr_p.add_argument("--draft", help="New draft text for approvals edit.")
    appr_p.add_argument("--file", help="File holding the new draft text for approvals edit.")
    appr_p.set_defaults(func=cmd_approvals)

    mem_p = sub.add_parser("memory", help="Inspect or forget an account's memory.")
    mem_p.add_argument("action", choices=["show", "clear", "accounts"])
    mem_p.add_argument("account", nargs="?", help="Account name, for show and clear.")
    mem_p.set_defaults(func=cmd_memory)

    sched_p = sub.add_parser("schedules", help="List, add, or remove scheduled jobs.")
    sched_p.add_argument("action", choices=["list", "add", "remove"])
    sched_p.add_argument("schedule_id", nargs="?", help="Schedule id, for remove.")
    sched_p.add_argument("--agent", help="Agent name, e.g. briefer, renewal-desk.")
    sched_p.add_argument("--account", help="Account name or id.")
    sched_p.add_argument("--cadence", choices=["daily", "weekly"], help="How often to run.")
    sched_p.add_argument("--time", help="Wall-clock time HH:MM in the schedule timezone.")
    sched_p.add_argument("--timezone", default="UTC", help="IANA timezone, e.g. America/Los_Angeles. Default: UTC.")
    sched_p.add_argument("--weekday", help="Day name or 0-6 (Mon-Sun), for weekly schedules.")
    sched_p.add_argument("--text", help="Free-text job description (defaults to the account).")
    sched_p.add_argument("--hourly-rate", type=float, default=None, help="Loaded hourly rate in dollars, for value-finder.")
    sched_p.add_argument("--hours-saved", type=float, default=None, help="Hours saved by automation, for value-finder.")
    sched_p.add_argument("--change", action="append", default=None, help="Change for salesforce-updater, repeatable. Same forms as run --change.")
    sched_p.add_argument("--no-memory", action="store_true", help="Run cold: do not load the account's memory as context.")
    sched_p.add_argument("--stub", action="store_true", help="Run scheduled jobs in stub mode.")
    sched_p.set_defaults(func=cmd_schedules)

    daemon_p = sub.add_parser("scheduler", help="Run due scheduled jobs: once, or as a daemon.")
    daemon_p.add_argument("--once", action="store_true", help="Run due jobs once, then exit.")
    daemon_p.add_argument("--interval", type=int, default=60, help="Max seconds between daemon wake-ups. Default: 60.")
    daemon_p.set_defaults(func=cmd_scheduler)

    dash_p = sub.add_parser("dashboard", help="Serve the dashboard on localhost.")
    dash_p.add_argument("--port", type=int, default=8000)
    dash_p.set_defaults(func=cmd_dashboard)

    launch_p = sub.add_parser(
        "launch",
        help="Start the dashboard and open it in the browser (double-click launch).",
    )
    launch_p.add_argument("--port", type=int, default=8000)
    launch_p.set_defaults(func=cmd_launch)

    setup_p = sub.add_parser("setup", help="First run: demo data or the setup wizard.")
    setup_p.add_argument("--demo", action="store_true", help="Fill the stores with demo data.")
    setup_p.add_argument("--wizard", action="store_true", help="Walk the LLM provider choice and connectors, writing .env.")
    setup_p.add_argument("--clear-demo", action="store_true", help="Remove demo-seeded records only.")
    setup_p.add_argument("--yes", action="store_true", help="With --demo: seed even over real data.")
    setup_p.set_defaults(func=cmd_setup)

    mcp_p = sub.add_parser("mcp-servers", help="List, add, or remove external MCP servers.")
    mcp_p.add_argument("action", choices=["list", "add", "remove"])
    mcp_p.add_argument("server_name", nargs="?", help="Server name, for remove.")
    mcp_p.add_argument("--name", help="Server name, for add. One word: letters, digits, dashes, underscores.")
    mcp_p.add_argument("--stdio", help="Stdio server command line, e.g. \"python -m connectors.gmail.server\".")
    mcp_p.add_argument("--sse", help="SSE server URL, e.g. https://example.com/mcp.")
    mcp_p.add_argument("--env", action="append", default=None, help="Environment addition KEY=VALUE for a stdio server, repeatable.")
    mcp_p.add_argument("--header", action="append", default=None, help='Header "KEY: VALUE" for an SSE server, repeatable.')
    mcp_p.add_argument("--read-only", action="store_true", help="Assert every tool on this server only reads. Only set for servers known to be read-only: it lets their tools run during gathering.")
    mcp_p.add_argument("--cwd", help="Working directory for a stdio server.")
    mcp_p.set_defaults(func=cmd_mcp_servers)

    esc_p = sub.add_parser("escalations", help="List, show, or resolve escalation records.")
    esc_p.add_argument("action", choices=["list", "show", "resolve"])
    esc_p.add_argument("record_id", nargs="?", help="Escalation id, for show and resolve.")
    esc_p.add_argument("--account", help="Account name, for list.")
    esc_p.add_argument("--note", help="Note to attach when resolving.")
    esc_p.set_defaults(func=cmd_escalations)

    sweep_p = sub.add_parser("sweep", help="Sweep the whole book for risk, rank it, store it for the Overview page.")
    sweep_p.add_argument("--stub", action="store_true", help="Force stub mode (no creds, no network).")
    sweep_p.set_defaults(func=cmd_sweep)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        # Bare `belay` with no subcommand (v1.16.0, decided): launch
        # the dashboard. The GUI is the default first run; the CLI
        # stays as the power-user path. A usage error here would send
        # a non-technical user back to the terminal for help, which
        # is the exact step the double-click launch removes.
        return cmd_launch(argparse.Namespace(port=8000))
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "approvals" and args.action in ("show", "approve", "reject", "edit") and not args.item_id:
        parser.error("approvals %s needs an item id." % args.action)
    if args.command == "memory" and args.action in ("show", "clear") and not args.account:
        parser.error("memory %s needs an account." % args.action)
    if args.command == "schedules" and args.action == "add" and not (
        args.agent and args.account and args.cadence and args.time
    ):
        parser.error("schedules add needs --agent, --account, --cadence, and --time.")
    if args.command == "schedules" and args.action == "remove" and not args.schedule_id:
        parser.error("schedules remove needs a schedule id.")
    if args.command == "mcp-servers" and args.action == "add" and not (
        args.name and (args.stdio or args.sse) and not (args.stdio and args.sse)
    ):
        parser.error("mcp-servers add needs --name and exactly one of --stdio or --sse.")
    if args.command == "mcp-servers" and args.action == "remove" and not args.server_name:
        parser.error("mcp-servers remove needs a server name.")
    if args.command == "escalations" and args.action in ("show", "resolve") and not args.record_id:
        parser.error("escalations %s needs an escalation id." % args.action)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
