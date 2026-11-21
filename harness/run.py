"""Command-line entry point for the harness.

Usage:
    python -m harness.run run --agent renewal-desk --account "Acme Corp"
    python -m harness.run run --text "draft a renewal plan for Acme Corp"
    python -m harness.run run --agent briefer --account "Acme Corp" --stub
    python -m harness.run run --agent renewal-desk --account "Acme Corp" --no-memory
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
    python -m harness.run dashboard [--port 8000]

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
"""

from __future__ import annotations

import argparse
import json
import sys

from connectors.champify.tools import champify_tools
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
    Router,
    TokenBudget,
    ToolRegistry,
    __version__,
    get_llm,
)


def build_registry(config: Config) -> ToolRegistry:
    """Register the connector tools (Salesforce + Slack + Gong + HubSpot
    + Gmail + Zoom + Zendesk + Linear + Champify).

    The CRM the agents gather from is Salesforce unless HubSpot is
    configured and Salesforce is not; then it is HubSpot. When both are
    configured, Salesforce wins. A registry built without a config
    defaults to Salesforce.
    """
    crm = (
        "hubspot"
        if config.hubspot_configured() and not config.salesforce_configured()
        else "salesforce"
    )
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
    for tool in champify_tools(config):
        registry.register(tool)
    return registry


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve the dashboard on localhost (reads the live approval queue)."""
    from harness.dashboard import main as dashboard_main

    return dashboard_main(["--port", str(args.port)])


def cmd_run(args: argparse.Namespace) -> int:
    """Route a job, run the agent, submit the draft to the queue."""
    config = Config.from_env()
    if args.stub:
        config.stub_mode = True
    registry = build_registry(config)
    llm = get_llm(config)
    router = Router(
        agents=AGENTS,
        llm=llm if config.llm_configured() else None,
        router_model=config.effective_router_model,
    )
    job: dict = {"text": args.text or ""}
    if args.agent:
        job["agent"] = args.agent
    if args.account:
        job["account"] = args.account
        if not job["text"]:
            job["text"] = args.account

    route = router.route(job)
    if route.error:
        print("Routing failed: %s" % route.error, file=sys.stderr)
        return 1
    agent = AGENTS[route.agent]
    print("Routed to %s (%s, %s)." % (agent.display_name, route.agent, route.reason))
    # Memory travels with the job: the memory file for injection, and
    # the opt-out flag. Every agent loads the account's memory from
    # it (bounded by the budget); --no-memory runs cold.
    job["memory_file"] = config.memory_file
    if args.no_memory:
        job["no_memory"] = True
    if route.agent in ("value-finder", "qbr-builder"):
        # Value-finder and qbr-builder read prior drafts from the
        # approval queue (value-finder synthesizes them, qbr-builder
        # composes from them); the queue path travels with the job.
        job["approvals_file"] = config.approvals_file
    if route.agent == "value-finder":
        # Value-finder quantifies time saved from explicit inputs only:
        # the loaded hourly rate comes from --hourly-rate or
        # HARNESS_LOADED_HOURLY_RATE, the hours from --hours-saved.
        # Neither is ever assumed.
        if args.hourly_rate is not None:
            job["hourly_rate"] = args.hourly_rate
            job["hourly_rate_source"] = "CLI --hourly-rate"
        elif config.loaded_hourly_rate is not None:
            job["hourly_rate"] = config.loaded_hourly_rate
            job["hourly_rate_source"] = "HARNESS_LOADED_HOURLY_RATE"
        if args.hours_saved is not None:
            job["hours_saved"] = args.hours_saved
    budget = TokenBudget(config.token_budget_per_job)
    try:
        draft = agent.run(job, registry, llm, budget)
    except NotImplementedError as exc:
        print("Agent error: %s" % exc, file=sys.stderr)
        return 1
    queue = ApprovalQueue(config.approvals_file)
    item = queue.submit(
        agent=agent.name,
        job=job,
        draft=draft.text,
        sources=draft.sources,
        links=draft.links,
        proof=draft.proof,
    )
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
    print("Review it with: python -m harness.run approvals show %s" % item.id)
    return 0


def cmd_agents(_args: argparse.Namespace) -> int:
    """List the nine agents and whether each is implemented in v1."""
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
    the draft's account."""
    config = Config.from_env()
    queue = ApprovalQueue(
        config.approvals_file, memory=AccountMemory(config.memory_file)
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
        print("--- draft ---")
        print(item.draft)
        print("--- end draft ---")
        return 0
    if args.action == "approve":
        item = queue.approve(args.item_id, note=args.note)
        print("Approved: %s" % item.id)
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
    run_p.add_argument("--no-memory", action="store_true", help="Run cold: do not load the account's memory as context.")
    run_p.set_defaults(func=cmd_run)

    agents_p = sub.add_parser("agents", help="List the nine agents.")
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

    dash_p = sub.add_parser("dashboard", help="Serve the dashboard on localhost.")
    dash_p.add_argument("--port", type=int, default=8000)
    dash_p.set_defaults(func=cmd_dashboard)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "approvals" and args.action in ("show", "approve", "reject", "edit") and not args.item_id:
        parser.error("approvals %s needs an item id." % args.action)
    if args.command == "memory" and args.action in ("show", "clear") and not args.account:
        parser.error("memory %s needs an account." % args.action)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
