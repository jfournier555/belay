"""Command-line entry point for the harness.

Usage:
    python -m harness.run run --agent renewal-desk --account "Acme Corp"
    python -m harness.run run --text "draft a renewal plan for Acme Corp"
    python -m harness.run run --agent briefer --account "Acme Corp" --stub
    python -m harness.run agents
    python -m harness.run tools
    python -m harness.run approvals list
    python -m harness.run approvals show <id>
    python -m harness.run approvals approve <id> [--note "..."]
    python -m harness.run approvals reject <id> [--note "..."]

The `run` command routes the job, runs the agent, and submits the draft to
the approval queue. Nothing is sent anywhere. A human approves it with
`approvals approve` before it leaves the harness.

--stub forces stub mode for all connectors and the LLM, so the full loop
runs with no credentials and no network calls.
"""

from __future__ import annotations

import argparse
import json
import sys

from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools

from harness import (
    AGENTS,
    ApprovalQueue,
    Config,
    Router,
    TokenBudget,
    ToolRegistry,
    get_llm,
)


def build_registry(config: Config) -> ToolRegistry:
    """Register the v1 connector tools (Salesforce + Slack)."""
    registry = ToolRegistry()
    for tool in salesforce_tools(config):
        registry.register(tool)
    for tool in slack_tools(config):
        registry.register(tool)
    return registry


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
        proof=draft.proof,
    )
    print("Draft submitted to the approval queue: %s" % item.id)
    print("Sources: %s" % (", ".join(draft.sources) or "none"))
    if draft.proof:
        passed = sum(1 for p in draft.proof if p.get("passed"))
        print("Proof checklist: %d/%d passed" % (passed, len(draft.proof)))
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
    """List the eight agents and whether each is implemented in v1."""
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
    """Manage the approval queue."""
    config = Config.from_env()
    queue = ApprovalQueue(config.approvals_file)
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
        if item.proof:
            passed = sum(1 for p in item.proof if p.get("passed"))
            print("Proof checklist: %d/%d passed" % (passed, len(item.proof)))
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
    print("Unknown approvals action: %s" % args.action, file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cs-harness",
        description="CS agent harness: router + agents + approval queue.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Route a job, run an agent, queue the draft.")
    run_p.add_argument("--agent", help="Agent name, e.g. renewal-desk, briefer.")
    run_p.add_argument("--account", help="Account name or id.")
    run_p.add_argument("--text", help="Free-text job description.")
    run_p.add_argument("--stub", action="store_true", help="Force stub mode (no creds, no network).")
    run_p.add_argument("--show", action="store_true", help="Print the draft after queueing it.")
    run_p.set_defaults(func=cmd_run)

    agents_p = sub.add_parser("agents", help="List the eight agents.")
    agents_p.set_defaults(func=cmd_agents)

    tools_p = sub.add_parser("tools", help="List registered connector tools.")
    tools_p.add_argument("--stub", action="store_true", help="Force stub mode.")
    tools_p.set_defaults(func=cmd_tools)

    appr_p = sub.add_parser("approvals", help="Manage the approval queue.")
    appr_p.add_argument("action", choices=["list", "show", "approve", "reject"])
    appr_p.add_argument("item_id", nargs="?", help="Approval item id.")
    appr_p.add_argument("--note", help="Note to attach to the decision.")
    appr_p.set_defaults(func=cmd_approvals)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "approvals" and args.action in ("show", "approve", "reject") and not args.item_id:
        parser.error("approvals %s needs an item id." % args.action)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
