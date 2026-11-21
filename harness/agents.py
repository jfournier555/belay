"""The specialist agents: the fourteen post-sales jobs the harness covers.

The fourteen jobs are the post-sales jobs that matter: scout, briefer,
onboarding guide, expansion spotter, QBR builder, advocate finder,
renewal desk, voice-of-customer, value-finder, salesforce-updater,
risk-watcher, escalation-manager, competitive-defense, and
stakeholder-mapper.

V1 implements fourteen end to end:
- renewal-desk: the highest-stakes job. Gathers account health, open
  renewal opportunity, open cases, and recent Slack signals, then drafts a
  renewal plan. Ties directly to the Renewal Scorecard on jfournier.net.
- briefer: the most common daily job. Gathers an account snapshot and recent
  activity, then drafts a pre-meeting brief.
- expansion-spotter: finds expansion openings from usage gaps, new teams,
  and opportunities already in flight.
- onboarding-guide: drafts an onboarding plan and a first-value checklist.
  The renewal gets decided in the first 60 days; first value is one
  workflow working. Ties to the Onboarding Playbook on jfournier.net.
- scout: finds new information about an account: new stakeholders, org
  changes, hiring signals, news, and new opportunities or risks.
- qbr-builder: drafts a quarterly business review: outcomes delivered,
  adoption and value realized, open risks, and the plan for next quarter.
  Turns the account picture into the QBR deck narrative.
- advocate-finder: finds champions and advocates: who uses the product
  most, who praises it in Slack, who would make a good reference, and
  which champions changed jobs.
- voice-of-customer: collects and synthesizes customer feedback: feature
  requests, pain points, sentiment from cases, Slack, Zendesk tickets,
  and Gong call transcripts, and the top themes to take back to the
  product team.
- value-finder: finds realized and unrealized outcomes in an account,
  quantified in money or time (=money): time saved, revenue protected
  or expanded, gaps not yet captured. Every dollar figure is labeled
  measured or modeled with its basis, and the figures are computed by
  the harness, never by the model.
- salesforce-updater: drafts Salesforce changes for an account (field
  changes, activity logs, stage moves) from the account context other
  agents gathered. The draft carries a dry-run preview (old -> new for
  every change) and the write plan; nothing executes until a human
  approves the draft in the queue.
- risk-watcher: watches for churn signals on an account (champion
  departure, ticket spikes, stalled renewals, sponsor disengagement)
  and drafts a risk brief: signals ranked by severity, evidence with
  deep links, overall severity, next step. Severity is harness-computed,
  never model prose. When a champion departure fires, the draft asks
  the three questions from the Champion Departure Playbook: did the
  mandate change, who is the successor, what are the successor's
  priorities and do they hold real influence. With no signals the draft
  is a clean bill, stated as such.
- escalation-manager: owns escalations end to end. When a high-severity
  signal fires (from a risk-watcher brief or from its own gather), it
  drafts the escalation plan: the situation with evidence and deep
  links, who to loop in and why, the ask, the exec update and the
  customer comms as drafts, and what happens if it does not land. The
  plan and the ask are harness-written, never model prose. One record
  per signal per account in the escalation store (open, escalated,
  resolved): a second run for the same signal drafts a status note,
  not a second plan; approving a plan moves its record to escalated;
  a resolved record stays resolved. Resolving is a human decision:
  the `escalations resolve` command, or approving a closure draft the
  agent wrote when the signal was no longer detected.
- competitive-defense: watches for displacement risk on an account
  (competitor mentions, evaluation signals, champion doubt) and drafts
  the defense brief: threats ranked by severity, evidence with its
  source, where the account is vulnerable, the wedge to hold the
  account, and the proof points to use. Threat severity is
  harness-computed, never model prose. A rival is named only when the
  gathered records name one, and every rival named carries its quoted
  evidence. With no signals the draft is a clean bill, stated as such.
- stakeholder-mapper: builds the relationship map for an account
  (users, champions, economic buyers, blockers, and how they connect).
  The map records influence, not just titles: who listens to whom, who
  has defended spend, who controls budget. Influence is scored on
  observable signals, never assumed: an active champion and a senior
  title score high, presence across several sources scores, a handle
  with no name or title scores unknown. Every person, role, and
  relationship comes from a gathered record and carries its source; a
  role the records do not support stays unknown, and the coverage gaps
  (no economic buyer, an empty champion seat, a successor never named)
  are named, never papered over.

All fourteen are implemented. Every job in the harness has a working agent.

Every agent follows the same contract:
1. gather() calls tools through the registry (summary-first results only).
2. The prompt is built from the gathered context, never from raw records.
3. The LLM drafts; the draft goes to the approval queue, never out directly.
4. The Draft records which tools were called and the deep links to the
   records they returned, so a human can click through to the source
   instead of taking the draft's word for it.
5. Before the draft reaches the queue, the agent self-checks it against
   its proof checklist (what "done" looks like). The checklist is a
   completeness gate, not a score: each item is a fact the draft must
   contain or a source it must have gathered, and one item checks the
   draft carries source links at all. A failed item is flagged in the
   draft metadata for the human reviewer, never a block: the draft
   still goes to the queue either way. Human approval remains the final
   gate. No aggregate score is ever shown: a score would let the
   reviewer trust the number instead of checking the sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .approvals import ApprovalQueue
from .escalations import EscalationStore
from .llm import LLMClient
from .mcp_client import build_external_calls
from .memory import AccountMemory
from .registry import ToolRegistry
from .summarizer import TokenBudget, estimate_tokens
from .writeback import build_write_plan, format_preview, parse_change_spec


@dataclass
class Draft:
    """What an agent produced. Always goes to the approval queue next.

    `writes` is the write plan a write draft carries (v1.3.0): the ops
    the executor runs if, and only if, a human approves the draft.
    Read-only drafts carry none."""

    text: str
    agent: str
    job: dict
    sources: list[str] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    tokens_estimate: int = 0
    proof: list[dict] = field(default_factory=list)
    writes: list[dict] = field(default_factory=list)

    @property
    def proof_passed(self) -> bool:
        """True when every proof item passed (or there is no checklist).

        Internal helper for tests only. Nothing user-facing shows an
        aggregate: the checklist is a completeness gate, and the
        reviewer reads the per-item results and the source links.
        """
        return all(item.get("passed", True) for item in self.proof)


@dataclass
class ProofItem:
    """One item on an agent's proof checklist: what "done" looks like.

    Each item names the evidence it needs. `keywords` must appear in the
    draft text (case-insensitive); `any_keywords` passes when at least
    one of them appears (for a label that takes one of two forms, such
    as measured or modeled); `sources` must be among the tools the
    agent successfully called; `requires_links` means the draft must
    carry at least one source link. An item with none of these is a
    manual check the human reviewer confirms by reading the draft.
    """

    label: str
    keywords: list[str] = field(default_factory=list)
    any_keywords: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    requires_links: bool = False

    def check(self, draft: Draft) -> dict:
        """Check one item against a draft. Returns a result dict."""
        missing: list[str] = []
        text = draft.text.lower()
        for keyword in self.keywords:
            if keyword.lower() not in text:
                missing.append("draft does not mention %r" % keyword)
        if self.any_keywords and not any(
            keyword.lower() in text for keyword in self.any_keywords
        ):
            missing.append(
                "draft mentions none of %r" % (self.any_keywords,)
            )
        for source in self.sources:
            if source not in draft.sources:
                missing.append("source %r was not gathered" % source)
        if self.requires_links and not draft.links:
            missing.append("no source links were gathered")
        return {
            "label": self.label,
            "passed": not missing,
            "detail": "; ".join(missing) if missing else "ok",
        }


def check_proof(checklist: list[ProofItem], draft: Draft) -> list[dict]:
    """Run an agent's proof checklist against a draft. Returns one result
    per item. A failure is a flag for the human reviewer, never a block:
    the draft still goes to the approval queue either way."""
    return [item.check(draft) for item in checklist]


class Agent:
    """One specialist agent."""

    name: str = ""
    display_name: str = ""
    description: str = ""
    keywords: list[str] = []
    implemented: bool = True
    proof_checklist: list[ProofItem] = []

    def _finish(
        self, draft: Draft, checklist: list[ProofItem] | None = None
    ) -> Draft:
        """Run the proof checklist and attach the results to the draft.
        The draft goes to the approval queue either way; a failed item is
        a flag for the human reviewer, not a block. `checklist` overrides
        the agent's default checklist: renewal-desk uses it for the
        HubSpot path, where the same items name HubSpot tools."""
        draft.proof = check_proof(checklist or self.proof_checklist, draft)
        return draft

    def run(
        self,
        job: dict,
        registry: ToolRegistry,
        llm: LLMClient,
        budget: TokenBudget,
    ) -> Draft:
        """Run the agent for a job. Subclasses implement this."""
        raise NotImplementedError("Agent %r is not implemented." % self.name)


def _extract_account(job: dict) -> str:
    """Work out which account the job is about.

    Prefers job["account"] when set. Otherwise, if the free-text job says
    "... for <Name>", takes the words after the last "for" (so "draft a
    renewal plan for Acme Corp" gives "Acme Corp", not the whole sentence).
    Falls back to the raw text.
    """
    account = str(job.get("account", "") or "").strip()
    if account:
        return account
    text = str(job.get("text", "") or "").strip()
    lower = text.lower()
    idx = lower.rfind(" for ")
    if idx != -1:
        candidate = text[idx + 5:].strip().rstrip(".,!?")
        if candidate:
            return candidate
    return text


def _build_prompt(agent_name: str, context: dict, task: str) -> str:
    """Assemble the agent prompt: task first, then the gathered context.
    The context is already summary-form, so the prompt stays small. The
    context is the last section, so a reader (or the stub LLM) can find
    where it ends."""
    lines = ["Task: " + task, ""]
    lines.append(
        "Write a concise draft for a customer success manager. "
        "Use only the context below. If a fact is not in the context, "
        "say so instead of guessing. Reference each claim to the named "
        "source it came from: the account record, an opportunity, a "
        "case, or a Slack message. Never write a URL in the draft; the "
        "harness attaches the source links itself, so the human "
        "reviewer can click through to the record."
    )
    lines.append("")
    lines.append("Context:")
    for key, value in context.items():
        lines.append("- %s: %s" % (key, value))
    return "\n".join(lines)


# Stub hosts are on .invalid, a TLD that never resolves, so a stub link
# is well-formed but unmistakably fake. The harness only builds a
# Salesforce link when the tool result carries an instance URL (live)
# or is stub data (this host); a result with neither yields no link.
STUB_SALESFORCE_HOST = "https://stub.salesforce.invalid"


# Gong call links are built from the call id: the app URL is fixed,
# https://app.gong.io/call?id=<callId>. Stub data links to the same
# path on .invalid, so a stub link is well-formed but unmistakably fake.
STUB_GONG_HOST = "https://app.gong.invalid"
GONG_APP_HOST = "https://app.gong.io"


# HubSpot record links are built from the portal id and the record id:
# https://app.hubspot.com/contacts/<portalId>/company/<companyId> (and
# /deal/<dealId>, /ticket/<ticketId>). Stub data links to the same path
# on .invalid with portal id 0, so a stub link is well-formed but
# unmistakably fake.
STUB_HUBSPOT_HOST = "https://app.hubspot.invalid"
HUBSPOT_APP_HOST = "https://app.hubspot.com"


# Gmail thread links are built from the thread id:
# https://mail.google.com/mail/u/0/#inbox/<threadId>. Stub data links
# to the same path on .invalid, so a stub link is well-formed but
# unmistakably fake.
STUB_GMAIL_HOST = "https://mail.google.invalid"
GMAIL_APP_HOST = "https://mail.google.com"


# Zendesk ticket links are built from the subdomain and the ticket id:
# https://{subdomain}.zendesk.com/agent/tickets/{id}. Stub data links
# to the same path on .invalid, so a stub link is well-formed but
# unmistakably fake.
STUB_ZENDESK_HOST = "https://acme-stub.zendesk.invalid"


# Linear issue links are built from the team key and the identifier:
# https://linear.app/{team}/issue/{identifier}. Stub data links to
# the same path on .invalid, so a stub link is well-formed but
# unmistakably fake.
STUB_LINEAR_HOST = "https://linear.invalid"
LINEAR_APP_HOST = "https://linear.app"


# Champify champion links are built from the champion id:
# https://app.champify.io/champions/{id}. Stub data links to the same
# path on .invalid, so a stub link is well-formed but unmistakably fake.
# The app URL shape is assumed: Champify publishes no public API
# reference, so it has not been confirmed against documentation.
STUB_CHAMPIFY_HOST = "https://app.champify.invalid"
CHAMPIFY_APP_HOST = "https://app.champify.io"


def _linear_base(result: dict) -> str:
    """The base URL for Linear deep links from a tool result."""
    if result.get("_stub"):
        return STUB_LINEAR_HOST
    return LINEAR_APP_HOST


def _champify_base(result: dict) -> str:
    """The base URL for Champify deep links from a tool result."""
    if result.get("_stub"):
        return STUB_CHAMPIFY_HOST
    return CHAMPIFY_APP_HOST


def _gmail_base(result: dict) -> str:
    """The base URL for Gmail deep links from a tool result."""
    if result.get("_stub"):
        return STUB_GMAIL_HOST
    return GMAIL_APP_HOST


def _hubspot_base(result: dict) -> str | None:
    """The base URL for HubSpot deep links from a tool result."""
    if result.get("_stub"):
        return STUB_HUBSPOT_HOST
    if result.get("portal_id"):
        return HUBSPOT_APP_HOST
    return None


def _gong_base(result: dict) -> str:
    """The base URL for Gong deep links from a tool result."""
    if result.get("_stub"):
        return STUB_GONG_HOST
    return GONG_APP_HOST


def _zendesk_base(result: dict) -> str | None:
    """The base URL for Zendesk deep links from a tool result."""
    if result.get("_stub"):
        return STUB_ZENDESK_HOST
    if result.get("subdomain"):
        return "https://%s.zendesk.com" % result["subdomain"]
    return None


def _salesforce_base(result: dict) -> str | None:
    """The base URL for Salesforce deep links from a tool result."""
    if result.get("instance_url"):
        return str(result["instance_url"]).rstrip("/")
    if result.get("_stub"):
        return STUB_SALESFORCE_HOST
    return None


def extract_links(tool_name: str, result: dict) -> list[dict]:
    """Build deterministic deep links from one tool result.

    The harness builds these from the record ids the tools returned;
    the model never writes a URL, so a link can never be hallucinated.
    A record without an id, or a Salesforce result with no instance
    URL, yields no link instead of a guessed one. Each link is a dict
    with a human-readable `label` and a `url`.
    """
    links: list[dict] = []
    if tool_name == "salesforce_get_account":
        base = _salesforce_base(result)
        record_id = result.get("id")
        if base and record_id:
            links.append(
                {
                    "label": "Account: %s" % result.get("name", record_id),
                    "url": "%s/lightning/r/Account/%s/view"
                    % (base, record_id),
                }
            )
    elif tool_name == "salesforce_list_opportunities":
        base = _salesforce_base(result)
        if base:
            for opp in result.get("opportunities", []):
                if opp.get("id"):
                    links.append(
                        {
                            "label": "Opportunity: %s"
                            % opp.get("name", opp["id"]),
                            "url": "%s/lightning/r/Opportunity/%s/view"
                            % (base, opp["id"]),
                        }
                    )
    elif tool_name == "salesforce_list_cases":
        base = _salesforce_base(result)
        if base:
            for case in result.get("cases", []):
                if case.get("id"):
                    links.append(
                        {
                            "label": "Case %s: %s"
                            % (
                                case.get("number", case["id"]),
                                case.get("subject", ""),
                            ),
                            "url": "%s/lightning/r/Case/%s/view"
                            % (base, case["id"]),
                        }
                    )
    elif tool_name == "hubspot_get_company":
        base = _hubspot_base(result)
        portal = result.get("portal_id")
        record_id = result.get("id")
        if base and portal is not None and record_id:
            links.append(
                {
                    "label": "Company: %s" % result.get("name", record_id),
                    "url": "%s/contacts/%s/company/%s"
                    % (base, portal, record_id),
                }
            )
    elif tool_name == "hubspot_list_deals":
        base = _hubspot_base(result)
        portal = result.get("portal_id")
        if base and portal is not None:
            for deal in result.get("deals", []):
                if deal.get("id"):
                    links.append(
                        {
                            "label": "Deal: %s" % deal.get("name", deal["id"]),
                            "url": "%s/contacts/%s/deal/%s"
                            % (base, portal, deal["id"]),
                        }
                    )
    elif tool_name == "hubspot_list_tickets":
        base = _hubspot_base(result)
        portal = result.get("portal_id")
        if base and portal is not None:
            for ticket in result.get("tickets", []):
                if ticket.get("id"):
                    links.append(
                        {
                            "label": "Ticket: %s"
                            % ticket.get("subject", ticket["id"]),
                            "url": "%s/contacts/%s/ticket/%s"
                            % (base, portal, ticket["id"]),
                        }
                    )
    elif tool_name == "slack_search_messages":
        for match in result.get("matches", []):
            if match.get("permalink"):
                links.append(
                    {
                        "label": "Slack #%s, %s"
                        % (
                            match.get("channel", "unknown").lstrip("#"),
                            match.get("author", "unknown"),
                        ),
                        "url": match["permalink"],
                    }
                )
    elif tool_name in (
        "gong_list_calls",
        "gong_get_call_summary",
        "gong_search_transcripts",
    ):
        base = _gong_base(result)
        if tool_name == "gong_list_calls":
            for call in result.get("calls", []):
                if call.get("id"):
                    links.append(
                        {
                            "label": "Gong call: %s"
                            % call.get("title", call["id"]),
                            "url": "%s/call?id=%s" % (base, call["id"]),
                        }
                    )
        elif tool_name == "gong_get_call_summary":
            call_id = result.get("call_id")
            if call_id:
                links.append(
                    {
                        "label": "Gong call: %s"
                        % result.get("title", call_id),
                        "url": "%s/call?id=%s" % (base, call_id),
                    }
                )
        else:
            for match in result.get("matches", []):
                if match.get("call_id"):
                    links.append(
                        {
                            "label": "Gong call: %s @ %s"
                            % (
                                match.get("title", match["call_id"]),
                                match.get("timestamp", ""),
                            ),
                            "url": "%s/call?id=%s"
                            % (base, match["call_id"]),
                        }
                    )
    elif tool_name == "gmail_search_messages":
        base = _gmail_base(result)
        for match in result.get("matches", []):
            if match.get("thread_id"):
                links.append(
                    {
                        "label": "Email: %s (%s)"
                        % (
                            match.get("subject", match["thread_id"]),
                            match.get("author", "unknown"),
                        ),
                        "url": "%s/mail/u/0/#inbox/%s"
                        % (base, match["thread_id"]),
                    }
                )
    elif tool_name == "gmail_get_thread":
        base = _gmail_base(result)
        thread_id = result.get("thread_id")
        if thread_id:
            links.append(
                {
                    "label": "Email thread: %s"
                    % result.get("subject", thread_id),
                    "url": "%s/mail/u/0/#inbox/%s" % (base, thread_id),
                }
            )
    elif tool_name in (
        "zoom_list_recordings",
        "zoom_get_recording_summary",
        "zoom_search_transcripts",
    ):
        # Zoom links are the recording play URL the API returned (live)
        # or the same path on the .invalid host (stub). The URL is
        # passed through, never constructed, the same way Slack
        # permalinks are.
        if tool_name == "zoom_list_recordings":
            for rec in result.get("recordings", []):
                if rec.get("play_url"):
                    links.append(
                        {
                            "label": "Zoom recording: %s"
                            % rec.get("topic", rec.get("id", "")),
                            "url": rec["play_url"],
                        }
                    )
        elif tool_name == "zoom_get_recording_summary":
            if result.get("play_url"):
                links.append(
                    {
                        "label": "Zoom recording: %s"
                        % result.get(
                            "title", result.get("recording_id", "")
                        ),
                        "url": result["play_url"],
                    }
                )
        else:
            for match in result.get("matches", []):
                if match.get("play_url"):
                    links.append(
                        {
                            "label": "Zoom recording: %s @ %s"
                            % (
                                match.get(
                                    "title", match.get("recording_id", "")
                                ),
                                match.get("timestamp", ""),
                            ),
                            "url": match["play_url"],
                        }
                    )
    elif tool_name in (
        "zendesk_list_tickets",
        "zendesk_search_tickets",
        "zendesk_get_ticket",
    ):
        base = _zendesk_base(result)
        if base:
            if tool_name == "zendesk_list_tickets":
                tickets = result.get("tickets", [])
            elif tool_name == "zendesk_search_tickets":
                tickets = result.get("matches", [])
            else:
                tickets = [result] if result.get("id") else []
            for ticket in tickets:
                if ticket.get("id"):
                    links.append(
                        {
                            "label": "Zendesk ticket %s: %s"
                            % (
                                ticket["id"],
                                ticket.get("subject", ""),
                            ),
                            "url": "%s/agent/tickets/%s"
                            % (base, ticket["id"]),
                        }
                    )
    elif tool_name in (
        "linear_list_issues",
        "linear_search_issues",
        "linear_get_issue",
    ):
        base = _linear_base(result)
        if tool_name == "linear_list_issues":
            issues = result.get("issues", [])
        elif tool_name == "linear_search_issues":
            issues = result.get("matches", [])
        else:
            issues = [result] if result.get("identifier") else []
        for issue in issues:
            team = issue.get("team") or result.get("team")
            identifier = issue.get("identifier")
            if team and identifier:
                links.append(
                    {
                        "label": "Linear %s: %s"
                        % (identifier, issue.get("title", "")),
                        "url": "%s/%s/issue/%s" % (base, team, identifier),
                    }
                )
    elif tool_name in (
        "champify_list_champions",
        "champify_list_job_changes",
        "champify_get_champion",
    ):
        base = _champify_base(result)
        if tool_name == "champify_list_champions":
            champions = result.get("champions", [])
            for champion in champions:
                if champion.get("id"):
                    links.append(
                        {
                            "label": "Champify champion: %s"
                            % champion.get("name", champion["id"]),
                            "url": "%s/champions/%s"
                            % (base, champion["id"]),
                        }
                    )
        elif tool_name == "champify_list_job_changes":
            for change in result.get("job_changes", []):
                champion_id = change.get("champion_id") or change.get("id")
                if champion_id:
                    links.append(
                        {
                            "label": "Champify job change: %s (%s -> %s)"
                            % (
                                change.get("name", champion_id),
                                change.get("previous_company", ""),
                                change.get("new_company", ""),
                            ),
                            "url": "%s/champions/%s"
                            % (base, champion_id),
                        }
                    )
        else:
            if result.get("id"):
                links.append(
                    {
                        "label": "Champify champion: %s"
                        % result.get("name", result["id"]),
                        "url": "%s/champions/%s" % (base, result["id"]),
                    }
                )
    return links


def external_calls_for_job(
    job: dict, registry: ToolRegistry
) -> list[tuple[str, dict]]:
    """The external tool calls for a job, selected by description.

    Thin wrapper over the MCP client's build_external_calls: the job
    text is scored against each external tool's name and description,
    and the matching tools are called with arguments filled from the
    job. No external servers configured costs nothing (an empty list).
    Write-flagged external tools are never returned: gathering can
    never run one, only the approval-gated executor can."""
    if job is None:
        return []
    return build_external_calls(registry, job)


def _gather_with_results(
    registry: ToolRegistry,
    calls: list[tuple[str, dict]],
    budget: TokenBudget,
    job: dict | None = None,
) -> tuple[dict, list[str], list[dict], dict]:
    """Call tools in order, respecting the token budget. Returns
    (context dict, list of tool names successfully called, list of
    source links built from the tool results, raw tool results by name).
    A tool that would blow the budget is skipped, and the skip is
    recorded in context. Links are collected only from calls that
    succeeded within budget, and deduplicated by URL. The raw results
    are for the agent to compute figures from the records (amounts,
    stages) instead of parsing the rendered context; they never reach
    the prompt.
    """
    context: dict = {}
    sources: list[str] = []
    links: list[dict] = []
    results: dict = {}
    seen_urls: set[str] = set()
    all_calls = list(calls)
    if job is not None:
        # External tools the job asks for (v1.5.0): selected by
        # description, appended after the agent's own calls, so the
        # agent's built-in sources always gather first and the budget
        # check still applies to every external result.
        all_calls.extend(external_calls_for_job(job, registry))
    for tool_name, kwargs in all_calls:
        try:
            result = registry.call(tool_name, **kwargs)
        except Exception as exc:  # tool missing, API down, bad args
            context[tool_name] = "Tool call failed: %s" % exc
            continue
        rendered = _render_result(result)
        if not budget.spend(rendered):
            context[tool_name] = "Skipped: would exceed the token budget."
            continue
        context[tool_name] = rendered
        sources.append(tool_name)
        results[tool_name] = result
        for link in extract_links(tool_name, result):
            if link["url"] not in seen_urls:
                seen_urls.add(link["url"])
                links.append(link)
    return context, sources, links, results


def _gather(
    registry: ToolRegistry,
    calls: list[tuple[str, dict]],
    budget: TokenBudget,
    job: dict | None = None,
) -> tuple[dict, list[str], list[dict]]:
    """Call tools in order, respecting the token budget. Returns
    (context dict, list of tool names successfully called, list of
    source links built from the tool results). A tool that would blow
    the budget is skipped, and the skip is recorded in context. Links
    are collected only from calls that succeeded within budget, and
    deduplicated by URL. `job` carries the job through so external
    tools the job asks for are gathered too (v1.5.0).
    """
    context, sources, links, _results = _gather_with_results(
        registry, calls, budget, job=job
    )
    return context, sources, links


# Keys the model must never see: the harness builds source links from
# these, and a URL in the prompt is a URL the model can copy into the
# draft. They are excluded from the rendered context, not from the
# tool result itself, so the link extractor still sees them.
_URL_KEYS = {"instance_url", "permalink", "play_url"}


def _sanitize_for_prompt(value):
    """Drop URL-bearing keys from a tool result before rendering.

    The strip is recursive: Slack results carry permalinks inside the
    nested matches list, and rendering a list with str() would leak
    them. The harness keeps the URLs for its own link extractor; the
    model never sees them.
    """
    if isinstance(value, dict):
        return {
            key: _sanitize_for_prompt(item)
            for key, item in value.items()
            if key not in _URL_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_for_prompt(item) for item in value]
    return value


def _render_result(result: dict) -> str:
    """Render a summary-form tool result as compact readable text."""
    parts = []
    for key, value in _sanitize_for_prompt(result).items():
        parts.append("%s: %s" % (key, value))
    return "; ".join(parts)


class RenewalDeskAgent(Agent):
    """Drafts a renewal plan for an account.

    Gathers: account health, open renewal opportunity, open support cases,
    recent Slack signals, recent email threads, and open engineering
    issues. Drafts: account health, risks, next steps, and a message to
    the customer. The CRM it gathers from is the registry's `crm`:
    Salesforce by default, HubSpot when HubSpot is configured and
    Salesforce is not. The proof checklist follows the CRM.
    """

    name = "renewal-desk"
    display_name = "Renewal Desk"
    description = (
        "Drafts a renewal plan for an account: health, risks, next steps."
    )
    keywords = [
        "renewal",
        "renew",
        "churn",
        "expire",
        "contract",
        "save",
        "at-risk",
        "at risk",
    ]
    proof_checklist = [
        ProofItem(
            "Names the open renewal opportunity",
            sources=["salesforce_list_opportunities"],
        ),
        ProofItem(
            "Names open support cases",
            sources=["salesforce_list_cases"],
        ),
        ProofItem(
            "Cites email threads",
            sources=["gmail_search_messages"],
        ),
        ProofItem(
            "Cites open engineering issues",
            sources=["linear_list_issues"],
        ),
        ProofItem("Covers account health", keywords=["health"]),
        ProofItem("Lists next steps", keywords=["step"]),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]
    hubspot_proof_checklist = [
        ProofItem(
            "Names the open renewal deal",
            sources=["hubspot_list_deals"],
        ),
        ProofItem(
            "Names open support tickets",
            sources=["hubspot_list_tickets"],
        ),
        ProofItem(
            "Cites email threads",
            sources=["gmail_search_messages"],
        ),
        ProofItem(
            "Cites open engineering issues",
            sources=["linear_list_issues"],
        ),
        ProofItem("Covers account health", keywords=["health"]),
        ProofItem("Lists next steps", keywords=["step"]),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        if getattr(registry, "crm", "salesforce") == "hubspot":
            calls = [
                ("hubspot_get_company", {"company": account}),
                ("hubspot_list_deals", {"company": account}),
                ("hubspot_list_tickets", {"company": account}),
            ]
            checklist = self.hubspot_proof_checklist
        else:
            calls = [
                ("salesforce_get_account", {"account": account}),
                ("salesforce_list_opportunities", {"account": account}),
                ("salesforce_list_cases", {"account": account}),
            ]
            checklist = self.proof_checklist
        calls.append(("slack_search_messages", {"query": account, "limit": 5}))
        calls.append(("gmail_search_messages", {"query": account, "limit": 5}))
        calls.append(("linear_list_issues", {}))
        context, sources, links = _gather(registry, calls, budget, job=job)
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a renewal plan for account %r. Cover: account health, "
            "the open renewal opportunity, open support cases, open "
            "engineering issues, any risk signals from Slack and email, "
            "and the next three steps with owners." % account,
        )
        system = (
            "You are the renewal desk for a customer success team. "
            "You draft renewal plans. Be direct, name the risks, and do "
            "not invent numbers that are not in the context."
        )
        text = llm.complete(prompt, system=system)
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            ),
            checklist=checklist,
        )


class BrieferAgent(Agent):
    """Drafts a pre-meeting brief for an account.

    Gathers: account snapshot and recent Slack activity. Drafts: who the
    account is, what has changed since the last touch, open items, and
    suggested questions to ask in the meeting.
    """

    name = "briefer"
    display_name = "Briefer"
    description = (
        "Drafts a pre-meeting brief: account snapshot, recent activity, "
        "questions to ask."
    )
    keywords = [
        "brief",
        "briefing",
        "meeting",
        "prep",
        "prepare",
        "qbr prep",
        "call prep",
        "who is",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem("Notes what changed", keywords=["changed"]),
        ProofItem("Suggests questions to ask", keywords=["question"]),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources, links = _gather(registry, calls, budget, job=job)
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a pre-meeting brief for account %r. Cover: who the "
            "account is, what has changed recently, open items, and three "
            "sharp questions to ask in the meeting." % account,
        )
        system = (
            "You are a briefing agent for a customer success team. "
            "You prepare pre-meeting briefs. Be concise, lead with what "
            "changed, and do not invent facts that are not in the context."
        )
        text = llm.complete(prompt, system=system)
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            )
        )


class ExpansionSpotterAgent(Agent):
    """Drafts an expansion plan for an account.

    Gathers: account summary, open opportunities (including any
    expansion opportunity already in flight), open support cases, and
    recent Slack signals. Drafts: where the expansion opening is (usage
    gaps, new teams, new use cases), the evidence for it, and the next
    three steps with owners.
    """

    name = "expansion-spotter"
    display_name = "Expansion Spotter"
    description = (
        "Drafts an expansion plan: openings, evidence, next steps."
    )
    keywords = [
        "expansion",
        "expand",
        "upsell",
        "growth",
        "land and expand",
    ]
    proof_checklist = [
        ProofItem(
            "Names the expansion opening",
            keywords=["expansion"],
        ),
        ProofItem(
            "Cites evidence from account data",
            sources=[
                "salesforce_get_account",
                "salesforce_list_opportunities",
            ],
        ),
        ProofItem("Lists next steps", keywords=["step"]),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources, links = _gather(registry, calls, budget, job=job)
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        prompt = _build_prompt(
            self.name,
            context,
            "Draft an expansion plan for account %r. Cover: where the "
            "expansion opening is (usage gaps, new teams, new use cases), "
            "the evidence for it from the account data and Slack signals, "
            "any open expansion opportunity already in flight, and the "
            "next three steps with owners." % account,
        )
        system = (
            "You are the expansion spotter for a customer success team. "
            "You draft expansion plans. Name the opening, cite the "
            "evidence, and do not invent numbers that are not in the "
            "context."
        )
        text = llm.complete(prompt, system=system)
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            )
        )


class OnboardingGuideAgent(Agent):
    """Drafts an onboarding plan and a first-value checklist.

    Gathers: account summary, open opportunities, open support cases, and
    recent Slack signals. Drafts: the first value workflow (one workflow
    working in the first 60 days), a first value checklist, open items and
    risk signals, and 30-day and 60-day milestones with owners. The renewal
    gets decided in the first 60 days, so the plan is built around getting
    to first value fast. Ties to the Onboarding Playbook on jfournier.net.
    """

    name = "onboarding-guide"
    display_name = "Onboarding Guide"
    description = (
        "Drafts onboarding plans and first-value checklists."
    )
    keywords = [
        "onboard",
        "onboarding",
        "first value",
        "kickoff",
        "go-live",
        "go live",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Names the first value workflow",
            keywords=["first value"],
        ),
        ProofItem(
            "Includes a first value checklist",
            keywords=["checklist"],
        ),
        ProofItem(
            "Names open support cases",
            sources=["salesforce_list_cases"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources, links = _gather(registry, calls, budget, job=job)
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        prompt = _build_prompt(
            self.name,
            context,
            "Draft an onboarding plan for account %r. Cover: the first "
            "value workflow (one workflow working in the first 60 days), "
            "a first value checklist, open support cases and any risk "
            "signals from Slack, and 30-day and 60-day milestones with "
            "owners." % account,
        )
        system = (
            "You are the onboarding guide for a customer success team. "
            "You draft onboarding plans. Name the first value workflow, "
            "keep it to one workflow working, and do not invent facts "
            "that are not in the context."
        )
        text = llm.complete(prompt, system=system)
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            )
        )


class ScoutAgent(Agent):
    """Finds new information about an account.

    Gathers: account summary, open opportunities, open support cases, and
    recent Slack signals. Drafts: new stakeholders and org changes, hiring
    signals, news, and any new opportunities or risks in the account data
    and Slack signals.
    """

    name = "scout"
    display_name = "Scout"
    description = (
        "Finds new information: stakeholders, org changes, hiring signals."
    )
    keywords = [
        "scout",
        "signal",
        "signals",
        "watch",
        "monitor",
        "flag",
        "stakeholder",
        "hiring",
        "news",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Covers new stakeholders",
            keywords=["stakeholder"],
        ),
        ProofItem(
            "Covers hiring signals",
            keywords=["hiring"],
        ),
        ProofItem(
            "Names new opportunities",
            sources=["salesforce_list_opportunities"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources, links = _gather(registry, calls, budget, job=job)
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        prompt = _build_prompt(
            self.name,
            context,
            "Scout account %r for new information. Cover: new "
            "stakeholders and org changes, hiring signals, news, and any "
            "new opportunities or risks in the account data and Slack "
            "signals." % account,
        )
        system = (
            "You are the scout for a customer success team. "
            "You find new information about accounts. Name what changed, "
            "cite the signal, and do not invent facts that are not in "
            "the context."
        )
        text = llm.complete(prompt, system=system)
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            )
        )


class QBRBuilderAgent(Agent):
    """Drafts a quarterly business review for an account.

    Composition (v1.1.0): when prior drafts from renewal-desk,
    expansion-spotter, and voice-of-customer exist for the account in
    the approval queue, the QBR is built from those drafts instead of
    re-gathering the same context. Only the account record is gathered
    directly; the source drafts carry the opportunities, cases, and
    Slack signals, and their deep links carry through to the composed
    draft. When no prior drafts exist, the agent gathers directly, its
    previous behavior. Composition is an optimization, never a hard
    dependency. Drafts: outcomes delivered this quarter, adoption and
    value realized, open risks and support cases, and the plan for next
    quarter. This is the agent that turns the account picture into the
    QBR deck narrative: what was delivered, what it is worth, what is
    at risk, and what happens next.
    """

    name = "qbr-builder"
    display_name = "QBR Builder"
    description = (
        "Drafts a quarterly business review: outcomes, adoption, risks, "
        "next quarter plan."
    )
    keywords = [
        "qbr",
        "business review",
        "quarterly",
        "ebr",
        "executive review",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Covers outcomes delivered",
            keywords=["outcome"],
        ),
        ProofItem(
            "Covers adoption and value realized",
            keywords=["adoption"],
        ),
        ProofItem(
            "Covers the plan for next quarter",
            keywords=["next quarter"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _QBR_SOURCE_AGENTS
        )
        if composed_text:
            # Composed: build from the prior drafts. Only the account
            # record is gathered directly; the source drafts carry the
            # rest. The draft carries a harness-written provenance
            # line naming the agents it was composed from, so the
            # composition proof item checks a fact, not model prose.
            context, sources, links = _gather(
                registry,
                [("salesforce_get_account", {"account": account})],
                budget,
                job=job,
            )
            context["prior_agent_drafts"] = composed_text
            seen_urls = {link.get("url") for link in links}
            for link in composed_links:
                if link.get("url") not in seen_urls:
                    seen_urls.add(link.get("url"))
                    links.append(link)
            memory_text = _memory_context(job, account, budget)
            if memory_text:
                context["memory"] = memory_text
            prompt = _build_prompt(
                self.name,
                context,
                "Draft a quarterly business review for account %r. "
                "Build it from the prior agent drafts in the context: "
                "renewal-desk health and risks, expansion-spotter "
                "gaps, voice-of-customer themes. Cover: outcomes "
                "delivered this quarter, adoption and value realized, "
                "open risks and support cases, and the plan for next "
                "quarter." % account,
            )
            system = (
                "You are the QBR builder for a customer success team. "
                "You draft quarterly business reviews. Lead with outcomes, "
                "name the risks, and do not invent numbers that are not in "
                "the context."
            )
            text = llm.complete(prompt, system=system)
            text += (
                "\n\nComposed from prior drafts: %s."
                % ", ".join(composed_agents)
            )
            checklist = self.proof_checklist + [
                ProofItem(
                    "Composed from prior agent drafts",
                    keywords=["composed from prior"],
                )
            ]
            return self._finish(
                Draft(
                    text=text,
                    agent=self.name,
                    job=job,
                    sources=sources,
                    links=links,
                    tokens_estimate=estimate_tokens(prompt)
                    + estimate_tokens(text),
                ),
                checklist=checklist,
            )
        # Fallback: no prior drafts for this account, gather directly
        # as before.
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources, links = _gather(registry, calls, budget, job=job)
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a quarterly business review for account %r. Cover: "
            "outcomes delivered this quarter, adoption and value realized, "
            "open risks and support cases, and the plan for next quarter." % account,
        )
        system = (
            "You are the QBR builder for a customer success team. "
            "You draft quarterly business reviews. Lead with outcomes, "
            "name the risks, and do not invent numbers that are not in "
            "the context."
        )
        text = llm.complete(prompt, system=system)
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            )
        )


class AdvocateFinderAgent(Agent):
    """Finds champions and advocates in an account.

    Gathers: account summary, open support cases, recent Slack
    signals, and recent champion job changes. Drafts: who uses the
    product most, who praises it in Slack, who would make a good
    reference, and any champion already known from the account data.
    For each champion it assesses influence from observable signals
    (who listens to them, defended spend, budget control), never
    assumed, and flags whether a known champion has gone quiet or
    left, naming the successor and what they care about if one is
    known. A champion who changed jobs is the departure signal: the
    job-change record says when it happened and where they went.
    """

    name = "advocate-finder"
    display_name = "Advocate Finder"
    description = (
        "Finds champions and advocates: who uses it most, who praises it, "
        "who would reference, and how much influence each carries."
    )
    keywords = [
        "advocate",
        "reference",
        "testimonial",
        "case study",
        "champion",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Names who praises the product in Slack",
            sources=["slack_search_messages"],
        ),
        ProofItem(
            "Cites recent champion job changes",
            sources=["champify_list_job_changes"],
        ),
        ProofItem(
            "Names a potential reference",
            keywords=["reference"],
        ),
        ProofItem(
            "Names a champion",
            keywords=["champion"],
        ),
        ProofItem(
            "Names each champion's influence",
            keywords=["influence"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
            ("champify_list_job_changes", {"account": account, "days": 90}),
        ]
        context, sources, links = _gather(registry, calls, budget, job=job)
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        prompt = _build_prompt(
            self.name,
            context,
            "Find advocates in account %r. Cover: who uses the product "
            "most, who praises it in Slack, who would make a good "
            "reference, any champion already known from the account "
            "data, recent champion job changes (who moved, from where "
            "to where, and whether a known champion left), how much "
            "influence each champion carries (who listens to them, "
            "have they defended spend, do they control budget), "
            "and whether a known champion has gone quiet or left, naming "
            "the successor and what they care about if one is known."
            % account,
        )
        system = (
            "You are the advocate finder for a customer success team. "
            "You find champions and advocates. Name the people, cite the "
            "evidence, and do not invent facts that are not in the "
            "context. Assess influence from evidence, not assumption: "
            "name what shows each champion can move a decision. A "
            "champion who changed jobs is a departure signal: name the "
            "move, when it happened, and what it means for the account."
        )
        text = llm.complete(prompt, system=system)
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            )
        )


class VoiceOfCustomerAgent(Agent):
    """Collects and synthesizes customer feedback for an account.

    Gathers: open support cases, Zendesk tickets, recent Slack signals,
    Gong call transcripts, and Zoom recording transcripts. Drafts:
    feature requests, pain points, sentiment from cases, tickets, Slack,
    and calls, and the top themes to take back to the product team.
    """

    name = "voice-of-customer"
    display_name = "Voice of Customer"
    description = (
        "Summarizes customer feedback: requests, pain points, sentiment, "
        "themes."
    )
    keywords = [
        "voice of customer",
        "voc",
        "feedback",
        "sentiment",
        "complaints",
    ]
    proof_checklist = [
        ProofItem(
            "Names feature requests",
            keywords=["feature"],
        ),
        ProofItem(
            "Names pain points",
            keywords=["pain"],
        ),
        ProofItem(
            "Covers sentiment",
            keywords=["sentiment"],
        ),
        ProofItem(
            "Cites support cases",
            sources=["salesforce_list_cases"],
        ),
        ProofItem(
            "Cites Slack messages",
            sources=["slack_search_messages"],
        ),
        ProofItem(
            "Cites Zendesk tickets",
            sources=["zendesk_list_tickets"],
        ),
        ProofItem(
            "Cites Gong call transcripts",
            sources=["gong_search_transcripts"],
        ),
        ProofItem(
            "Cites Zoom recording transcripts",
            sources=["zoom_search_transcripts"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_list_cases", {"account": account}),
            ("zendesk_list_tickets", {"organization": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
            ("gong_search_transcripts", {"query": account, "limit": 5}),
            ("zoom_search_transcripts", {"query": account, "limit": 5}),
        ]
        context, sources, links = _gather(registry, calls, budget, job=job)
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        prompt = _build_prompt(
            self.name,
            context,
            "Summarize the voice of the customer for account %r. Cover: "
            "feature requests, pain points, sentiment from support cases, "
            "Zendesk tickets, Slack, Gong call transcripts, and Zoom "
            "recording transcripts, and the top themes to take back to "
            "the product team." % account,
        )
        system = (
            "You are the voice of the customer for a customer success "
            "team. You collect and synthesize customer feedback. Name the "
            "themes, cite the evidence, and do not invent facts that are "
            "not in the context."
        )
        text = llm.complete(prompt, system=system)
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            )
        )


def format_money(amount: float) -> str:
    """Format a dollar amount: $1,500, or $1,500.50 when it has cents."""
    if float(amount) == int(amount):
        return "$%s" % format(int(amount), ",")
    return "$%s" % format(amount, ",.2f")


def quantify_time_savings(hours: float, hourly_rate: float) -> float:
    """Hours saved x loaded hourly rate = dollars. Pure arithmetic; the
    caller decides whether the inputs have a basis."""
    return float(hours) * float(hourly_rate)


def _value_figures(
    opportunities: dict | None,
    hours_saved: float | None,
    hourly_rate: float | None,
    rate_source: str,
) -> str:
    """Build the quantified figures block for a value report.

    Every dollar figure is computed here from the gathered records or
    from explicit job inputs, never by the model, and every line labels
    the figure measured or modeled with its basis. A figure with no
    basis is stated as absent, never invented.

    - Realized, measured: closed-won opportunity amounts.
    - Realized, modeled: hours saved x loaded hourly rate.
    - Unrealized, measured: open opportunity amounts (expansion not yet
      captured, renewal not yet secured).
    - Unrealized, modeled: none stated; the job carries hours already
      saved, not hours still to save, so there is no basis for one.
    `opportunities` is the raw result dict from the opportunities tool,
    or None when that call did not run. `hours_saved` is the hours the
    CSM supplied with the job, or None. `hourly_rate` is the loaded
    rate from the job or the environment, or None; `rate_source` names
    where it came from.
    """
    if hours_saved is not None and hours_saved <= 0:
        hours_saved = None
    if hourly_rate is not None and hourly_rate <= 0:
        hourly_rate = None
    lines = ["Quantified figures (harness-computed, not model prose):"]
    if opportunities is None:
        realized_measured = 0.0
        unrealized_measured = 0.0
        lines.append(
            "- Realized (measured): not gathered, the opportunities "
            "call did not run, so no measured figure is stated."
        )
        lines.append(
            "- Unrealized (measured): not gathered, the opportunities "
            "call did not run, so no measured figure is stated."
        )
    else:
        opps = opportunities.get("opportunities", []) or []
        priced = [
            opp
            for opp in opps
            if isinstance(opp.get("amount"), (int, float))
        ]
        realized = [
            opp
            for opp in priced
            if str(opp.get("stage", "")).lower() == "closed won"
        ]
        open_opps = [opp for opp in priced if opp not in realized]
        realized_measured = sum(opp["amount"] for opp in realized)
        unrealized_measured = sum(opp["amount"] for opp in open_opps)
        if realized:
            lines.append(
                "- Realized (measured): %s, sum of %d closed-won "
                "opportunity amount(s) from the gathered records."
                % (format_money(realized_measured), len(realized))
            )
        else:
            lines.append(
                "- Realized (measured): $0, no closed-won revenue in "
                "the gathered opportunity records."
            )
        if open_opps:
            parts = [
                "%s %s"
                % (
                    format_money(opp["amount"]),
                    opp.get("type", "opportunity"),
                )
                for opp in open_opps
            ]
            lines.append(
                "- Unrealized (measured): %s, %s, from the gathered "
                "opportunity records (open, not yet captured)."
                % (format_money(unrealized_measured), "; ".join(parts))
            )
        else:
            lines.append(
                "- Unrealized (measured): $0, no open opportunities in "
                "the gathered records."
            )
    realized_modeled = 0.0
    if hours_saved is not None and hourly_rate is not None:
        realized_modeled = quantify_time_savings(hours_saved, hourly_rate)
        lines.append(
            "- Realized (modeled): %s, %s hours saved x %s/hour loaded "
            "rate (hours supplied with the job; rate from %s)."
            % (
                format_money(realized_modeled),
                "%g" % hours_saved,
                format_money(hourly_rate),
                rate_source,
            )
        )
    elif hours_saved is not None:
        lines.append(
            "- Realized (modeled): no dollar figure, %s hours saved "
            "supplied but no loaded hourly rate was configured (set "
            "HARNESS_LOADED_HOURLY_RATE or pass --hourly-rate); time "
            "saved is reported in hours only, not dollars."
            % ("%g" % hours_saved)
        )
    elif hourly_rate is not None:
        lines.append(
            "- Realized (modeled): no dollar figure, a loaded hourly "
            "rate is configured but no hours saved were supplied with "
            "the job; pass --hours-saved to quantify time saved."
        )
    else:
        lines.append(
            "- Realized (modeled): no dollar figure, no hours saved and "
            "no loaded hourly rate supplied."
        )
    lines.append(
        "- Unrealized (modeled): none stated, no basis supplied for "
        "time still spent manually."
    )
    realized_total = realized_measured + realized_modeled
    lines.append("- Realized total: %s." % format_money(realized_total))
    lines.append(
        "- Total quantified: realized %s; unrealized %s."
        % (
            format_money(realized_total),
            format_money(unrealized_measured),
        )
    )
    return "\n".join(lines)


# The three agents whose drafts value-finder synthesizes (SPEC.md):
# renewal-desk health, expansion-spotter gaps, onboarding-guide
# first-value checklist. The approval queue is the store they already
# share; no new cross-agent mechanism is introduced.
_PRIOR_DRAFT_AGENTS = ("renewal-desk", "expansion-spotter", "onboarding-guide")

# The three agents whose drafts qbr-builder composes (SPEC.md v1.1.0):
# renewal-desk health and risks, expansion-spotter gaps,
# voice-of-customer themes. Same store, same rule: the queue is the
# only channel between agents.
_QBR_SOURCE_AGENTS = ("renewal-desk", "expansion-spotter", "voice-of-customer")

# The three agents whose drafts risk-watcher composes (v1.6.0):
# renewal-desk health and risks, advocate-finder champions and
# departures, voice-of-customer themes. Same store, same rule: the
# queue is the only channel between agents.
_RISK_SOURCE_AGENTS = ("renewal-desk", "advocate-finder", "voice-of-customer")


def _load_prior_items(job: dict, account: str, agents: tuple) -> list:
    """Prior drafts from the named agents for this account, from the
    approval queue file the job names. Newest first; ties (same
    created second) break toward the later-submitted item. Empty when
    the job names no approvals file or the queue cannot be read:
    composition is an optimization, never a hard dependency, so a
    missing queue reads as no prior drafts, not an error."""
    path = job.get("approvals_file")
    if not path:
        return []
    try:
        queue = ApprovalQueue(path)
    except Exception:
        return []
    indexed = list(enumerate(queue.list_all()))
    items = [
        item
        for _i, item in indexed
        if item.agent in agents
        and _extract_account(item.job) == account
    ]
    order = {id(item): i for i, item in indexed}
    items.sort(key=lambda item: (item.created_at, order[id(item)]), reverse=True)
    return items


def _format_prior_items(items: list) -> str:
    """Prior drafts as prompt context. Excerpts are capped at 500
    characters and URLs are stripped: the model never sees a URL."""
    parts = []
    for item in items:
        excerpt = re.sub(r"https?://\S+", "[link removed]", item.draft)
        excerpt = " ".join(excerpt.split())
        if len(excerpt) > 500:
            excerpt = excerpt[:500] + " ..."
        parts.append(
            "%s (%s): %s" % (item.agent, item.created_at, excerpt)
        )
    return "Prior agent drafts for this account:\n" + "\n".join(parts)


def _agent_list_text(agents: tuple) -> str:
    """Agent names as a readable list: 'a, b, or c'."""
    if len(agents) == 1:
        return agents[0]
    return "%s, or %s" % (", ".join(agents[:-1]), agents[-1])


def _memory_context(job: dict, account: str, budget: TokenBudget) -> str:
    """Past drafts, decisions, and corrections for this account, from
    the memory file the job names. Bounded by the token budget:
    newest records first, stops when a record no longer fits. Empty
    when the job opts out (--no-memory), names no memory file, or the
    account has no memory. Memory is an input to the draft, not a
    replacement for fresh tool results, so it is loaded after the
    gather and only spends what the budget has left."""
    if job.get("no_memory"):
        return ""
    path = job.get("memory_file")
    if not path:
        return ""
    try:
        memory = AccountMemory(path)
    except Exception:
        return ""
    return memory.render(account, budget)


def _prior_drafts(
    job: dict, account: str, agents: tuple = _PRIOR_DRAFT_AGENTS
) -> str:
    """Prior drafts from the other agents for this account, from the
    approval queue file the job names. The queue is the store those
    drafts already share; value-finder reads it, it does not invent a
    new channel. Returns a short summary for the prompt context."""
    path = job.get("approvals_file")
    if not path:
        return (
            "No prior agent drafts on file (no approvals file supplied "
            "with the job)."
        )
    try:
        queue = ApprovalQueue(path)
    except Exception:
        return "Prior agent drafts could not be read."
    items = _load_prior_items(job, account, agents)
    if not items:
        return (
            "No prior drafts from %s for this account."
            % _agent_list_text(agents)
        )
    return _format_prior_items(items[:3])


def _compose_sources(
    job: dict, account: str, agents: tuple
) -> tuple[str, list[dict], list[str]]:
    """The composition helper (v1.1.0): the latest draft from each named
    agent for this account, from the approval queue. Returns (summary
    text, merged deep links, agent names used). Empty (\"\", [], []) when
    no prior drafts exist: the caller falls back to gathering directly.
    Links are the source drafts' own harness-built deep links,
    deduplicated by URL; the excerpts are stripped the same way
    _prior_drafts strips them, so the model never sees a URL."""
    items = _load_prior_items(job, account, agents)
    if not items:
        return "", [], []
    latest: dict[str, object] = {}
    for item in items:  # newest first, so the first per agent wins
        if item.agent not in latest:
            latest[item.agent] = item
    chosen = [latest[name] for name in agents if name in latest]
    links: list[dict] = []
    seen: set[str] = set()
    for item in chosen:
        for link in item.links:
            url = link.get("url")
            if url and url not in seen:
                seen.add(url)
                links.append(link)
    return (
        _format_prior_items(chosen),
        links,
        [item.agent for item in chosen],
    )


class ValueFinderAgent(Agent):
    """Finds realized and unrealized outcomes in an account, quantified
    in money or time (=money).

    Gathers: account summary, open opportunities, open support cases,
    and recent Slack signals. Reads prior drafts from renewal-desk,
    expansion-spotter, and onboarding-guide out of the approval queue
    and synthesizes them. Drafts: a report with realized outcomes with
    evidence, unrealized gaps with the fix, and a dollar total,
    structured for an executive business review.

    Quantification is harness-computed, not model prose: closed-won
    opportunity amounts are measured, hours saved x the loaded hourly
    rate is modeled, and every dollar figure is labeled with its basis.
    The loaded hourly rate comes from the job (--hourly-rate) or the
    HARNESS_LOADED_HOURLY_RATE environment variable; it is never
    assumed. A figure with no basis does not appear.
    """

    name = "value-finder"
    display_name = "Value Finder"
    description = (
        "Finds realized and unrealized outcomes in money or time: time "
        "saved, revenue protected, gaps not yet captured."
    )
    keywords = [
        "value",
        "value report",
        "value-finder",
        "value finder",
        "realized value",
        "unrealized value",
        "roi",
        "return on investment",
        "time saved",
        "money saved",
        "cost saved",
        "savings",
    ]
    proof_checklist = [
        ProofItem(
            "Covers realized outcomes",
            keywords=["realized"],
        ),
        ProofItem(
            "Covers unrealized gaps",
            keywords=["unrealized"],
        ),
        ProofItem(
            "Cites the account record",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Cites opportunities",
            sources=["salesforce_list_opportunities"],
        ),
        ProofItem(
            "Cites support cases",
            sources=["salesforce_list_cases"],
        ),
        ProofItem(
            "Cites Slack signals",
            sources=["slack_search_messages"],
        ),
        ProofItem(
            "Dollar figures are labeled measured or modeled",
            any_keywords=["measured", "modeled"],
        ),
        ProofItem(
            "Gives a dollar total",
            keywords=["total"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources, links, results = _gather_with_results(
            registry, calls, budget, job=job
        )
        context["prior_agent_drafts"] = _prior_drafts(job, account)
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        hours_saved = job.get("hours_saved")
        if hours_saved is not None:
            try:
                hours_saved = float(hours_saved)
            except (TypeError, ValueError):
                hours_saved = None
        if hours_saved is not None and hours_saved <= 0:
            hours_saved = None
        hourly_rate = job.get("hourly_rate")
        if hourly_rate is not None:
            try:
                hourly_rate = float(hourly_rate)
            except (TypeError, ValueError):
                hourly_rate = None
        if hourly_rate is not None and hourly_rate <= 0:
            hourly_rate = None
        rate_source = job.get("hourly_rate_source") or "the job"
        figures = _value_figures(
            results.get("salesforce_list_opportunities"),
            hours_saved,
            hourly_rate,
            rate_source,
        )
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a value report for account %r, structured for an "
            "executive business review. Cover: realized outcomes with "
            "the evidence for each (time saved, errors caught, revenue "
            "protected or expanded), unrealized gaps with the fix for "
            "each (usage gaps, workflows still manual, expansion not "
            "yet captured, value at risk), and a dollar total. Label "
            "every dollar figure measured or modeled and cite its "
            "basis; a figure with no basis does not appear. Do not "
            "invent numbers that are not in the context." % account,
        )
        system = (
            "You are the value finder for a customer success team. "
            "You measure outcomes, not usage: time saved, revenue "
            "protected, gaps not yet captured. Every dollar figure "
            "must be labeled measured or modeled with its basis, and "
            "a figure with no basis must not appear. Do not invent "
            "numbers that are not in the context."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + figures
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            )
        )


class SalesforceUpdaterAgent(Agent):
    """Drafts Salesforce changes for an account, approval-gated.

    Gathers: account summary, open opportunities, open support cases.
    Reads prior drafts from renewal-desk, expansion-spotter, and
    voice-of-customer out of the approval queue for context on what
    needs updating. Drafts: field changes, activity logs, and stage
    moves, from the explicit change specs in the job (--change on the
    CLI). The draft carries a harness-computed dry-run preview (old ->
    new for every change) and the machine-readable write plan.

    The agent never calls a write tool: it only reads. The write plan
    executes only when a human approves the draft in the queue, through
    the executor in harness/writeback.py. A spec the harness cannot
    parse, or no specs at all, drafts an explanation with no write
    plan; the proof checklist flags the missing preview for the human
    reviewer.
    """

    name = "salesforce-updater"
    display_name = "Salesforce Updater"
    description = (
        "Drafts Salesforce changes for approval: field changes, activity "
        "logs, stage moves. Nothing applies until a human approves."
    )
    keywords = [
        "salesforce-updater",
        "salesforce update",
        "update salesforce",
        "update opportunity",
        "update the account",
        "field change",
        "log activity",
        "activity log",
        "move stage",
        "stage move",
        "updater",
    ]
    proof_checklist = [
        ProofItem(
            "Draft carries the dry-run preview",
            keywords=["dry-run preview"],
        ),
        ProofItem(
            "Preview shows old and new values",
            keywords=["->"],
        ),
        ProofItem(
            "Cites the account record",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Cites opportunities",
            sources=["salesforce_list_opportunities"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
        ]
        context, sources, links, results = _gather_with_results(
            registry, calls, budget, job=job
        )
        context["prior_agent_drafts"] = _prior_drafts(
            job,
            account,
            ("renewal-desk", "expansion-spotter", "voice-of-customer"),
        )
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        raw_changes = job.get("changes") or []
        specs: list[dict] = []
        bad_spec: str | None = None
        for raw in raw_changes:
            if isinstance(raw, dict):
                specs.append(raw)
                continue
            try:
                specs.append(parse_change_spec(str(raw)))
            except ValueError as exc:
                bad_spec = str(exc)
                break
        if bad_spec is not None:
            text = (
                "Could not draft the Salesforce updates for account %r: "
                "the change spec was not understood (%s). No write plan "
                "is attached to this draft; re-run with a valid --change "
                "spec." % (account, bad_spec)
            )
            return self._finish(
                Draft(
                    text=text,
                    agent=self.name,
                    job=job,
                    sources=sources,
                    links=links,
                    tokens_estimate=estimate_tokens(text),
                )
            )
        ops, preview = build_write_plan(specs, results)
        if preview:
            requested = "; ".join(preview)
        else:
            requested = "none: no changes were requested"
        prompt = _build_prompt(
            self.name,
            context,
            "Draft the Salesforce updates for account %r. Requested "
            "changes: %s. For each change, say what record it touches, "
            "what changes, and why, using only the context and the prior "
            "agent drafts. Do not invent a change that was not requested "
            "and do not invent a current value that is not in the "
            "context." % (account, requested),
        )
        system = (
            "You are the Salesforce updater for a customer success team. "
            "You draft record changes for a human to approve; you never "
            "apply anything yourself. Describe each requested change and "
            "its reason, using only the context. Do not invent changes "
            "or current values that are not in the context."
        )
        text = llm.complete(prompt, system=system)
        if preview:
            text = text + "\n\n" + format_preview(preview)
        else:
            text = text + (
                "\n\nNo changes were requested, so there is no write plan "
                "in this draft."
            )
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
                writes=ops,
            )
        )


_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# Escalation language in a message or transcript excerpt: the customer
# talking about leaving, not just about a bug. A bug with a fix date is
# a support record; "cancel" or "switching to" is a churn signal.
_ESCALATION_WORDS = (
    "cancel",
    "cancelling",
    "escalat",
    "switching to",
    "leaving us",
    "churn",
    "unacceptable",
    "not renewing",
)


def _record_severity(priority, age_days) -> str:
    """Severity of one open record from its priority and age.

    High priority is high. A medium record only becomes a signal when
    it has sat open five days or more. Zendesk calls its middle
    priority "normal"; it counts as medium here. Low priority, and a
    fresh medium record, are normal operations, not churn signals, so
    they return no severity at all."""
    priority = str(priority or "").lower()
    try:
        age = int(age_days) if age_days is not None else 0
    except (TypeError, ValueError):
        age = 0
    if priority == "high":
        return "high"
    if priority in ("medium", "normal") and age >= 5:
        return "medium"
    return ""


def _norm_subject(subject) -> str:
    """Group key for one issue across systems: lowercased, punctuation
    collapsed. The same SSO failure in Salesforce, Zendesk, and Linear
    is one signal, not three."""
    return re.sub(r"\W+", " ", str(subject or "").lower()).strip()


def _risk_signals(results: dict, account: str, today=None) -> list[dict]:
    """Detect churn signals from the gathered tool results, severity-ranked.

    Deterministic, harness-computed: the model never decides what counts
    as a signal. Champion departures are high. Open support and delivery
    records group by subject at the worst severity in the group. A renewal
    opportunity past its close date is high; one closing within 30 days
    is medium. Escalation language in messages or transcripts is medium.
    Returns a list of {"severity", "kind", "text"} dicts, high first; a
    champion-departure signal also carries its "record" for the departure
    block. `today` is injectable so tests can pin the date.
    """
    if today is None:
        from datetime import date as _date

        today = _date.today()
    signals: list[dict] = []

    # Champion departures: a job change out of this account to another
    # company. An internal move (same company, new role) is a promotion,
    # not a departure, and produces no signal.
    changes = results.get("champify_list_job_changes") or {}
    departures = []
    for change in changes.get("job_changes", []) or []:
        previous = change.get("previous_company")
        new = change.get("new_company")
        if not previous or not new:
            continue
        if str(previous) != str(account):
            continue
        if str(new) == str(previous):
            continue
        departures.append(change)
        signals.append(
            {
                "severity": "high",
                "kind": "champion-departure",
                "key": "champion-departure:%s"
                % str(change.get("name", "?")).lower(),
                "text": (
                    "Champion departure: %s left %s (%s) for %s (%s) "
                    "on %s."
                    % (
                        change.get("name", "a champion"),
                        previous,
                        change.get("previous_role", "unknown role"),
                        new,
                        change.get("new_role", "unknown role"),
                        change.get("changed_at", "unknown date"),
                    )
                ),
                "record": change,
            }
        )

    # Open support and delivery records, grouped by subject. One record
    # per system at most: the group text names every system it appears
    # in, so the reviewer sees the spread without three copies of the
    # same failure.
    groups: dict[str, dict] = {}
    grouped: list[tuple[dict, str, str, str]] = []
    cases = results.get("salesforce_list_cases") or {}
    for case in cases.get("cases", []) or []:
        if str(case.get("status", "")).lower() in ("closed", "solved"):
            continue
        grouped.append(
            (
                case,
                "Salesforce case %s"
                % case.get("number", case.get("id", "?")),
                str(case.get("subject", "")),
                "Salesforce",
            )
        )
    tickets = results.get("zendesk_list_tickets") or {}
    for ticket in tickets.get("tickets", []) or []:
        if str(ticket.get("status", "")).lower() not in ("open", "pending"):
            continue
        grouped.append(
            (
                ticket,
                "Zendesk ticket %s" % ticket.get("id", "?"),
                str(ticket.get("subject", "")),
                "Zendesk",
            )
        )
    issues = results.get("linear_list_issues") or {}
    for issue in issues.get("issues", []) or []:
        if str(issue.get("status", "")).lower() in (
            "done",
            "canceled",
            "cancelled",
        ):
            continue
        grouped.append(
            (
                issue,
                "Linear issue %s" % issue.get("identifier", issue.get("id", "?")),
                str(issue.get("title", "")),
                "Linear",
            )
        )
    for record, label, subject, system in grouped:
        severity = _record_severity(
            record.get("priority"), record.get("age_days")
        )
        if not severity:
            continue
        key = _norm_subject(subject)
        if not key:
            continue
        group = groups.setdefault(
            key, {"subject": subject, "severity": "low", "parts": []}
        )
        if _SEVERITY_RANK[severity] < _SEVERITY_RANK[group["severity"]]:
            group["severity"] = severity
        group["parts"].append(
            "%s (%s, %s days open)"
            % (
                label,
                str(record.get("priority", "?")).lower(),
                record.get("age_days", "?"),
            )
        )
    for group_key, group in groups.items():
        signals.append(
            {
                "severity": group["severity"],
                "kind": "support-delivery",
                "key": "support-delivery:" + group_key,
                "text": (
                    "Support/delivery risk: %r is open in %d place(s): "
                    "%s."
                    % (
                        group["subject"],
                        len(group["parts"]),
                        "; ".join(group["parts"]),
                    )
                ),
            }
        )

    # Renewal opportunities: past close date is high, closing within 30
    # days is medium. A single snapshot cannot show a stage regression,
    # so none is claimed.
    opps = results.get("salesforce_list_opportunities") or {}
    for opp in opps.get("opportunities", []) or []:
        if str(opp.get("type", "")).lower() != "renewal":
            continue
        if str(opp.get("stage", "")).lower() in ("closed won", "closed lost"):
            continue
        close = str(opp.get("close_date", "") or "")
        try:
            from datetime import date as _date

            year, month, day = (int(part) for part in close.split("-"))
            close_date = _date(year, month, day)
        except (ValueError, TypeError):
            continue
        delta = (close_date - today).days
        if delta < 0:
            signals.append(
                {
                    "severity": "high",
                    "kind": "renewal-stalled",
                    "key": "renewal-stalled:%s"
                    % str(opp.get("name", "?")).lower(),
                    "text": (
                        "Renewal stalled: %s (%s) passed its close date "
                        "%s and is still open."
                        % (
                            opp.get("name", "renewal opportunity"),
                            format_money(opp["amount"])
                            if isinstance(opp.get("amount"), (int, float))
                            else "amount unknown",
                            close,
                        )
                    ),
                }
            )
        elif delta <= 30:
            signals.append(
                {
                    "severity": "medium",
                    "kind": "renewal-closing",
                    "key": "renewal-closing:%s"
                    % str(opp.get("name", "?")).lower(),
                    "text": (
                        "Renewal closing soon: %s (%s) closes %s, %d "
                        "day(s) out, still open."
                        % (
                            opp.get("name", "renewal opportunity"),
                            format_money(opp["amount"])
                            if isinstance(opp.get("amount"), (int, float))
                            else "amount unknown",
                            close,
                            delta,
                        )
                    ),
                }
            )

    # Escalation language in messages or transcripts: one signal naming
    # the sources that used it, with the first matched excerpt quoted.
    for tool_name, label in (
        ("gmail_search_messages", "Gmail"),
        ("gong_search_transcripts", "Gong transcripts"),
        ("zoom_search_transcripts", "Zoom transcripts"),
        ("slack_search_messages", "Slack"),
    ):
        result = results.get(tool_name) or {}
        matches = result.get("matches", []) or []
        for match in matches:
            excerpt = str(match.get("excerpt", "") or "")
            lowered = excerpt.lower()
            hit = next(
                (word for word in _ESCALATION_WORDS if word in lowered),
                None,
            )
            if hit:
                signals.append(
                    {
                        "severity": "medium",
                        "kind": "escalation-language",
                        "key": "escalation-language:%s:%s"
                        % (tool_name, hit),
                        "text": (
                            "Escalation language in %s (%r): %r"
                            % (label, hit, excerpt[:120])
                        ),
                    }
                )
                break

    signals.sort(key=lambda s: _SEVERITY_RANK[s["severity"]])
    return signals


def _departure_block(departures: list[dict], champions: dict | None) -> str:
    """The three champion-departure questions, harness-written so they
    are in the draft even when the model is the stub. One block per
    departed champion. The successor is named only when the gathered
    records name one; otherwise the block names the remaining active
    champions as candidates to confirm, or says none are named."""
    lines = [
        "Champion departure: answer these three before drafting the "
        "save plan."
    ]
    active: list[str] = []
    if isinstance(champions, dict):
        for champ in champions.get("champions", []) or []:
            if str(champ.get("status", "")).lower() == "active":
                active.append(
                    "%s (%s)"
                    % (champ.get("name", "?"), champ.get("role", "?"))
                )
    for dep in departures:
        name = dep.get("name", "the champion")
        lines.append(
            "- Did the mandate change? %s left %s (%s) for %s (%s) on "
            "%s. Find out whether they left or were pushed out because "
            "the mandate that bought the product changed. If the mandate "
            "is gone, the save plan becomes an exit plan."
            % (
                name,
                dep.get("previous_company", "?"),
                dep.get("previous_role", "unknown role"),
                dep.get("new_company", "?"),
                dep.get("new_role", "unknown role"),
                dep.get("changed_at", "unknown date"),
            )
        )
        if active:
            lines.append(
                "- Who is the successor? The gathered records name no "
                "successor for %s. The remaining active champions are "
                "%s; confirm with the account whether one of them picks "
                "up the mandate." % (name, "; ".join(active))
            )
        else:
            lines.append(
                "- Who is the successor? The gathered records name no "
                "successor for %s; find the successor before drafting "
                "the save plan." % name
            )
        lines.append(
            "- What are the successor's priorities, and do they hold "
            "real influence? Validate the successor's priorities and "
            "influence from observable signals (who listens to them, "
            "have they defended spend, do they control budget) before "
            "drafting the save plan."
        )
    return "\n".join(lines)


def _risk_signals_block(
    signals: list[dict], departures: list[dict], champions: dict | None
) -> str:
    """The harness-computed risk block appended to the draft: overall
    severity, the severity-ranked signals, and the departure questions
    when a champion left. No signals means a clean bill, stated as
    such, never an invented risk."""
    if not signals:
        return (
            "Risk signals (harness-computed): No churn signals found "
            "in the gathered records. Overall severity: none. No churn "
            "signal in the account record, opportunities, cases, "
            "tickets, issues, messages, transcripts, or champion job "
            "changes."
        )
    lines = [
        "Risk signals (harness-computed, severity-ranked):",
        "Overall severity: %s." % signals[0]["severity"],
    ]
    for signal in signals:
        lines.append("- [%s] %s" % (signal["severity"], signal["text"]))
    if departures:
        lines.append("")
        lines.append(_departure_block(departures, champions))
    return "\n".join(lines)


class RiskWatcherAgent(Agent):
    """Watches for churn signals on an account and drafts a risk brief.

    Gathers: account summary, open opportunities, open support cases,
    champion job changes and the champion list, recent email threads,
    Gong and Zoom transcripts, Zendesk tickets, Linear issues, and
    recent Slack signals. Drafts: a risk brief with the signals ranked
    by severity, the evidence for each with deep links, an overall
    severity, and a recommended next step.

    Composition (v1.1.0): when prior drafts from renewal-desk,
    advocate-finder, and voice-of-customer exist for the account, the
    brief is built from those drafts instead of re-gathering the same
    context; only the fresh risk signals (account record, champion job
    changes, champion list, Zendesk tickets, Salesforce cases) are
    gathered directly. When no prior drafts exist, the agent gathers
    directly, its previous behavior.

    Severity is harness-computed, never model prose: champion departure
    is high, an open high-priority record is high, a renewal past its
    close date is high. When a champion departure fires, the draft
    carries the three questions from the Champion Departure Playbook:
    did the mandate change, who is the successor, what are the
    successor's priorities and do they hold real influence. With no
    signals at all the draft is a clean bill, stated as such.
    """

    name = "risk-watcher"
    display_name = "Risk Watcher"
    description = (
        "Watches for churn signals: champion departure, ticket spikes, "
        "stalled renewals, sponsor disengagement. Drafts a risk brief."
    )
    keywords = [
        "risk",
        "risk-watcher",
        "early warning",
        "departure",
        "champion departure",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Cites champion job changes",
            sources=["champify_list_job_changes"],
        ),
        ProofItem(
            "States the overall severity",
            keywords=["overall severity"],
        ),
        ProofItem(
            "Gives a recommended next step",
            keywords=["next step"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _RISK_SOURCE_AGENTS
        )
        if composed_text:
            # Composed: build from the prior drafts. Only the fresh risk
            # signals are gathered directly; the source drafts carry the
            # opportunities, Slack, transcripts, Gmail, and Linear
            # context, and their deep links carry through to the
            # composed draft. The draft carries a harness-written
            # provenance line naming the agents it was composed from.
            calls = [
                ("salesforce_get_account", {"account": account}),
                ("champify_list_job_changes", {"account": account, "days": 90}),
                ("champify_list_champions", {"account": account}),
                ("zendesk_list_tickets", {"organization": account}),
                ("salesforce_list_cases", {"account": account}),
            ]
            context, sources, links, results = _gather_with_results(
                registry, calls, budget, job=job
            )
            context["prior_agent_drafts"] = composed_text
            seen_urls = {link.get("url") for link in links}
            for link in composed_links:
                if link.get("url") not in seen_urls:
                    seen_urls.add(link.get("url"))
                    links.append(link)
            composed = True
        else:
            # Fallback: no prior drafts for this account, gather directly.
            calls = [
                ("salesforce_get_account", {"account": account}),
                ("salesforce_list_opportunities", {"account": account}),
                ("salesforce_list_cases", {"account": account}),
                ("champify_list_job_changes", {"account": account, "days": 90}),
                ("champify_list_champions", {"account": account}),
                ("gmail_search_messages", {"query": account, "limit": 5}),
                ("gong_search_transcripts", {"query": account, "limit": 5}),
                ("zoom_search_transcripts", {"query": account, "limit": 5}),
                ("zendesk_list_tickets", {"organization": account}),
                ("linear_list_issues", {}),
                ("slack_search_messages", {"query": account, "limit": 5}),
            ]
            context, sources, links, results = _gather_with_results(
                registry, calls, budget, job=job
            )
            composed = False
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        signals = _risk_signals(results, account)
        departures = [
            signal["record"]
            for signal in signals
            if signal["kind"] == "champion-departure"
        ]
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a risk brief for account %r. Cover: the churn "
            "signals in the context ranked by severity (champion "
            "departures, open support and delivery records, stalled or "
            "closing renewals, escalation language in messages or "
            "transcripts), the evidence for each signal, the overall "
            "severity, and a recommended next step. When a champion "
            "departure is in the context, answer the three questions: "
            "did the mandate change, who is the successor, what are "
            "the successor's priorities and do they hold real "
            "influence. Do not invent a signal that is not in the "
            "context." % account,
        )
        system = (
            "You are the risk watcher for a customer success team. "
            "You watch for churn before it becomes a renewal problem. "
            "Rank signals by severity, cite the evidence, and do not "
            "invent facts that are not in the context. A champion who "
            "changed jobs is a departure signal: name the move, when "
            "it happened, and what it means for the account."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + _risk_signals_block(
            signals, departures, results.get("champify_list_champions")
        )
        checklist = list(self.proof_checklist)
        if departures:
            checklist += [
                ProofItem(
                    "Asks whether the mandate changed",
                    keywords=["mandate"],
                ),
                ProofItem(
                    "Names the successor question",
                    keywords=["successor"],
                ),
                ProofItem(
                    "Validates the successor's priorities",
                    keywords=["priorities"],
                ),
            ]
        if composed:
            text += (
                "\n\nComposed from prior drafts: %s."
                % ", ".join(composed_agents)
            )
            checklist += [
                ProofItem(
                    "Composed from prior agent drafts",
                    keywords=["composed from prior"],
                )
            ]
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            ),
            checklist=checklist,
        )


# Competitive-threat phrases, by severity class. Displacement language
# (the account is moving to a rival) is high; evaluation language (the
# account is comparing a rival) and champion doubt are medium. Bare
# words like "demo" or "trial" are not signals: our own demos and trials
# use those words too. The phrases here name a rival, a comparison, or
# a move, so an excerpt about our own product does not fire.
_COMPETITIVE_DISPLACEMENT_PHRASES = (
    "migrat",
    "switching to",
    "moving to",
    "moving off",
)

_COMPETITIVE_EVALUATION_PHRASES = (
    "evaluat",
    "side by side",
    "comparing",
    "comparison",
    "their demo",
    "their trial",
    "their price",
    "their pricing",
    "alternative",
    "competitor",
    "rival",
)

_CHAMPION_DOUBT_PHRASES = (
    "reconsidering",
    "second thoughts",
    "losing confidence",
    "not the right fit",
)

# Sources scanned for competitive language: message and transcript
# excerpts first, then ticket and case subjects.
_COMPETITIVE_SOURCES = (
    ("gmail_search_messages", "Gmail"),
    ("gong_search_transcripts", "Gong transcripts"),
    ("zoom_search_transcripts", "Zoom transcripts"),
    ("slack_search_messages", "Slack"),
)

# The wedge to hold the account, per threat kind. Harness-written
# playbook lines, never model prose: the wedge is the move, not a
# claim about the rival.
_COMPETITIVE_WEDGES = {
    "displacement": (
        "Migration has not started: the renewal is the decision point. "
        "Hold the account by closing the open items the rival can point "
        "at and getting the champion to defend the renewal internally "
        "before the rival's next step."
    ),
    "evaluation": (
        "The decision is still open: the renewal is unsigned and the "
        "rival's demo has not happened yet. Win on the open items "
        "before the demo, and make the champion the one who frames the "
        "comparison."
    ),
    "champion-doubt": (
        "The doubt is about outcomes, not the relationship: answer "
        "with fix dates and the expansion value, not a feature list. "
        "Give the champion something concrete to defend internally."
    ),
}

_RIVAL_NAME_RE = re.compile(
    r"(?:[Ee]valuat\w*|[Mm]igrat\w*\s+to|[Ss]witch\w*\s+to|[Mm]oving\s+to|"
    r"[Cc]ompar\w+\s+(?:us\s+)?(?:to|with|against)|"
    r"[Ll]ook\s+at\s+(?:a\s+)?(?:move\s+to\s+)?)"
    r"\s*(?:the\s+)?([A-Z][A-Za-z0-9]{1,30})"
)


def _rival_name(excerpt: str) -> str:
    """The rival named in an excerpt, or 'an unnamed rival' when the
    excerpt names none. The name comes from the customer's own words
    (evaluate X, migrating to X, comparison with X); the harness never
    guesses a rival the records do not name."""
    match = _RIVAL_NAME_RE.search(excerpt)
    if not match:
        return "an unnamed rival"
    return match.group(1)


def _competitive_phrase_hit(lowered: str) -> tuple | None:
    """First competitive phrase in lowercased text, as (phrase,
    severity, kind), displacement first, then evaluation, then doubt.
    None when no phrase matches."""
    for phrase in _COMPETITIVE_DISPLACEMENT_PHRASES:
        if phrase in lowered:
            return (phrase, "high", "displacement")
    for phrase in _COMPETITIVE_EVALUATION_PHRASES:
        if phrase in lowered:
            return (phrase, "medium", "evaluation")
    for phrase in _CHAMPION_DOUBT_PHRASES:
        if phrase in lowered:
            return (phrase, "medium", "champion-doubt")
    return None


def _competitive_signals(results: dict, account: str) -> list[dict]:
    """Detect competitive threats from the gathered tool results.

    Deterministic, harness-computed: the model never decides what counts
    as a threat. Scans message and transcript excerpts plus open ticket
    and case subjects for displacement language (high), evaluation
    language (medium), and champion doubt (medium). One signal per
    source at most: the first phrase hit in that source wins, and the
    excerpt is quoted as the evidence, so every rival named carries its
    source. A phrase hit with no rival name in the excerpt still fires
    (the excerpt is the evidence, and the human judges it); the signal
    says the rival is unnamed. Returns a list of {severity, kind, key,
    rival, source, excerpt, text} dicts, high first.
    """
    signals: list[dict] = []

    def _signal(severity, kind, hit, label, excerpt):
        rival = _rival_name(excerpt)
        signals.append(
            {
                "severity": severity,
                "kind": kind,
                "key": "competitive:%s:%s" % (label.lower(), rival.lower()),
                "rival": rival,
                "source": label,
                "excerpt": excerpt[:200],
                "text": (
                    "%s %s signal in %s (%r): %r"
                    % (
                        rival,
                        kind.replace("-", " "),
                        label,
                        hit,
                        excerpt[:120],
                    )
                ),
            }
        )

    for tool_name, label in _COMPETITIVE_SOURCES:
        result = results.get(tool_name) or {}
        for match in result.get("matches", []) or []:
            excerpt = str(match.get("excerpt", "") or "")
            hit = _competitive_phrase_hit(excerpt.lower())
            if hit is None:
                continue
            phrase, severity, kind = hit
            _signal(severity, kind, phrase, label, excerpt)
            break

    # Open tickets and cases: subject and description carry comparison
    # or migration language. Closed records are history, not a live
    # threat, so they are skipped the same way risk-watcher skips them.
    for tool_name, label, records, text_fields in (
        (
            "zendesk_list_tickets",
            "Zendesk",
            (results.get("zendesk_list_tickets") or {}).get("tickets", [])
            or [],
            ("subject", "description"),
        ),
        (
            "salesforce_list_cases",
            "Salesforce",
            (results.get("salesforce_list_cases") or {}).get("cases", [])
            or [],
            ("subject",),
        ),
    ):
        for record in records:
            if str(record.get("status", "")).lower() in (
                "closed",
                "solved",
                "done",
                "canceled",
                "cancelled",
            ):
                continue
            text = " ".join(
                str(record.get(field, "") or "") for field in text_fields
            )
            hit = _competitive_phrase_hit(text.lower())
            if hit is None:
                continue
            phrase, severity, kind = hit
            _signal(severity, kind, phrase, label, text)
            break

    signals.sort(key=lambda s: _SEVERITY_RANK[s["severity"]])
    return signals


def _competitive_vulnerability(results: dict) -> str:
    """Where the account is vulnerable, from the gathered records only:
    the open renewal opportunity at risk, the open items the rival can
    point at, and how thin the champion bench is. A part with no records
    behind it is left out, never invented."""
    parts = []
    opps = results.get("salesforce_list_opportunities") or {}
    for opp in opps.get("opportunities", []) or []:
        if str(opp.get("type", "")).lower() != "renewal":
            continue
        if str(opp.get("stage", "")).lower() in ("closed won", "closed lost"):
            continue
        parts.append(
            "the renewal %s (%s, stage %s, closes %s) is unsigned while "
            "the rival is in play"
            % (
                opp.get("name", "opportunity"),
                format_money(opp["amount"])
                if isinstance(opp.get("amount"), (int, float))
                else "amount unknown",
                opp.get("stage", "unknown stage"),
                opp.get("close_date", "unknown date"),
            )
        )
        break
    open_items = []
    cases = results.get("salesforce_list_cases") or {}
    for case in cases.get("cases", []) or []:
        if str(case.get("status", "")).lower() in ("closed", "solved"):
            continue
        open_items.append(str(case.get("subject", "")))
    tickets = results.get("zendesk_list_tickets") or {}
    for ticket in tickets.get("tickets", []) or []:
        if str(ticket.get("status", "")).lower() not in ("open", "pending"):
            continue
        open_items.append(str(ticket.get("subject", "")))
    if open_items:
        parts.append(
            "open items the rival can point at: %s"
            % "; ".join(sorted(set(open_items)))
        )
    champions = results.get("champify_list_champions") or {}
    active = [
        c
        for c in champions.get("champions", []) or []
        if str(c.get("status", "")).lower() == "active"
    ]
    if len(active) == 1:
        parts.append(
            "only one active champion (%s), so the defense rests on one "
            "relationship" % active[0].get("name", "unnamed")
        )
    elif len(active) == 0 and champions.get("champions") is not None:
        parts.append("no active champion in the gathered records")
    if not parts:
        return (
            "No open renewal opportunity, open item, or champion list in "
            "the gathered records; the vulnerability is unknown, not "
            "absent."
        )
    return "; ".join(parts) + "."


def _competitive_proof_points(results: dict) -> str:
    """Proof points to use in the defense, from the gathered records
    only: the renewal at stake, the expansion interest, and the active
    champions by name and role. Nothing here is a claim about the
    rival; it is what we have to defend with."""
    points = []
    opps = results.get("salesforce_list_opportunities") or {}
    for opp in opps.get("opportunities", []) or []:
        if str(opp.get("stage", "")).lower() in ("closed won", "closed lost"):
            continue
        points.append(
            "%s: %s, closes %s, stage %s"
            % (
                opp.get("name", "opportunity"),
                format_money(opp["amount"])
                if isinstance(opp.get("amount"), (int, float))
                else "amount unknown",
                opp.get("close_date", "unknown date"),
                opp.get("stage", "unknown stage"),
            )
        )
    champions = results.get("champify_list_champions") or {}
    active = [
        c
        for c in champions.get("champions", []) or []
        if str(c.get("status", "")).lower() == "active"
    ]
    if active:
        points.append(
            "Active champions: %s"
            % ", ".join(
                "%s (%s)" % (c.get("name", "?"), c.get("role", "unknown role"))
                for c in active
            )
        )
    if not points:
        return "No proof points in the gathered records."
    return "; ".join(points) + "."


def _competitive_defense_block(signals: list[dict], results: dict) -> str:
    """The harness-computed defense brief block appended to the draft:
    overall threat level, each threat with its evidence and source, the
    vulnerability, the wedge per threat kind, and the proof points. No
    signals means a clean bill, stated as such, never an invented
    threat. Every rival named comes from a quoted excerpt."""
    if not signals:
        return (
            "Competitive threats (harness-computed): No competitor "
            "mentions, evaluation signals, or champion doubt found in "
            "the gathered records. Overall threat level: none. No rival "
            "named anywhere in messages, transcripts, tickets, or cases "
            "for this account."
        )
    lines = [
        "Competitive threats (harness-computed, severity-ranked):",
        "Overall threat level: %s." % signals[0]["severity"],
    ]
    for signal in signals:
        lines.append(
            "- [%s] %s" % (signal["severity"], signal["text"])
        )
        lines.append(
            "  Evidence: %r (%s)." % (signal["excerpt"], signal["source"])
        )
    lines.append("")
    lines.append("Vulnerability: %s" % _competitive_vulnerability(results))
    kinds = []
    for signal in signals:
        if signal["kind"] not in kinds:
            kinds.append(signal["kind"])
    for kind in kinds:
        lines.append(
            "Wedge (%s): %s" % (kind, _COMPETITIVE_WEDGES[kind])
        )
    lines.append("Proof points: %s" % _competitive_proof_points(results))
    return "\n".join(lines)


# The three agents whose drafts competitive-defense composes from
# (v1.8.0): voice-of-customer first (its themes carry the rival
# mentions), plus expansion-spotter and risk-watcher.
_COMPETITIVE_SOURCE_AGENTS = (
    "voice-of-customer",
    "expansion-spotter",
    "risk-watcher",
)


class CompetitiveDefenseAgent(Agent):
    """Watches for displacement risk on an account and drafts the
    defense brief.

    Gathers: account summary, open opportunities, open support cases,
    champion list, recent email threads, Gong and Zoom transcripts,
    Zendesk tickets, and recent Slack signals. Drafts: a defense brief
    with the threats ranked by severity, the evidence for each with its
    source, where the account is vulnerable, the wedge to hold the
    account, and the proof points to use.

    Composition (v1.1.0): when prior drafts from voice-of-customer,
    expansion-spotter, and risk-watcher exist for the account, the brief
    is built from those drafts instead of re-gathering the same context;
    only the fresh competitive signals (account record, opportunities,
    champion list, Gmail, Gong, Zoom, Zendesk, Slack) are gathered
    directly, and Salesforce cases are left to the source drafts. When
    no prior drafts exist, the agent gathers directly, its previous
    behavior.

    Threat severity is harness-computed, never model prose: displacement
    language is high, evaluation language and champion doubt are medium.
    A rival is named only when the gathered records name one, and every
    rival named carries its quoted evidence. With no signals at all the
    draft is a clean bill, stated as such.
    """

    name = "competitive-defense"
    display_name = "Competitive Defense"
    description = (
        "Watches for displacement risk: competitor mentions, evaluation "
        "signals, champion doubt. Drafts a defense brief."
    )
    keywords = [
        "competitor",
        "competitive",
        "competitive-defense",
        "displacement",
        "rival",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Cites evidence for each threat",
            keywords=["evidence"],
        ),
        ProofItem(
            "States the overall threat level",
            keywords=["overall threat level"],
        ),
        ProofItem(
            "Gives the wedge to hold the account",
            keywords=["wedge"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _COMPETITIVE_SOURCE_AGENTS
        )
        if composed_text:
            # Composed: build from the prior drafts. Only the fresh
            # competitive signals are gathered directly; the source
            # drafts carry the cases and the broader account context,
            # and their deep links carry through to the composed draft.
            # The draft carries a harness-written provenance line
            # naming the agents it was composed from.
            calls = [
                ("salesforce_get_account", {"account": account}),
                ("salesforce_list_opportunities", {"account": account}),
                ("champify_list_champions", {"account": account}),
                ("gmail_search_messages", {"query": account, "limit": 5}),
                ("gong_search_transcripts", {"query": account, "limit": 5}),
                ("zoom_search_transcripts", {"query": account, "limit": 5}),
                ("zendesk_list_tickets", {"organization": account}),
                ("slack_search_messages", {"query": account, "limit": 5}),
            ]
            context, sources, links, results = _gather_with_results(
                registry, calls, budget, job=job
            )
            context["prior_agent_drafts"] = composed_text
            seen_urls = {link.get("url") for link in links}
            for link in composed_links:
                if link.get("url") not in seen_urls:
                    seen_urls.add(link.get("url"))
                    links.append(link)
            composed = True
        else:
            # Fallback: no prior drafts for this account, gather directly.
            calls = [
                ("salesforce_get_account", {"account": account}),
                ("salesforce_list_opportunities", {"account": account}),
                ("salesforce_list_cases", {"account": account}),
                ("champify_list_champions", {"account": account}),
                ("gmail_search_messages", {"query": account, "limit": 5}),
                ("gong_search_transcripts", {"query": account, "limit": 5}),
                ("zoom_search_transcripts", {"query": account, "limit": 5}),
                ("zendesk_list_tickets", {"organization": account}),
                ("slack_search_messages", {"query": account, "limit": 5}),
            ]
            context, sources, links, results = _gather_with_results(
                registry, calls, budget, job=job
            )
            composed = False
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        signals = _competitive_signals(results, account)
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a defense brief for account %r. Cover: the "
            "competitive threats in the context ranked by severity "
            "(displacement language, evaluation signals, champion "
            "doubt), the evidence for each threat with its source, "
            "where the account is vulnerable, the wedge to hold the "
            "account, and the proof points to use. Name a rival only "
            "when the context names one, and quote the evidence for "
            "every rival named. Do not invent a threat that is not in "
            "the context." % account,
        )
        system = (
            "You are the competitive-defense agent for a customer "
            "success team. You watch for displacement risk before it "
            "becomes a lost renewal. Rank threats by severity, cite "
            "the evidence, and never name a rival the records do not "
            "name. A customer evaluating another product is a threat "
            "to answer, not a done deal: the brief is the defense play."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + _competitive_defense_block(signals, results)
        checklist = list(self.proof_checklist)
        if composed:
            text += (
                "\n\nComposed from prior drafts: %s."
                % ", ".join(composed_agents)
            )
            checklist += [
                ProofItem(
                    "Composed from prior agent drafts",
                    keywords=["composed from prior"],
                )
            ]
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            ),
            checklist=checklist,
        )


# The three agents whose drafts stakeholder-mapper composes from
# (v1.9.0): advocate-finder first (its champions carry roles and
# influence), plus risk-watcher and voice-of-customer.
_STAKEHOLDER_SOURCE_AGENTS = (
    "advocate-finder",
    "risk-watcher",
    "voice-of-customer",
)


# Words in a recorded job title that signal seniority (influence) or
# budget authority (economic buyer). A title alone never makes
# someone a champion; only a champion record does that.
_SENIOR_TITLE_WORDS = ("vp", "director", "head", "chief", "president")
_BUDGET_TITLE_WORDS = (
    "cfo",
    "chief financial",
    "vp finance",
    "vice president finance",
    "procurement",
)
# Words in a display name that mark it as a function label, not a
# person's name: "Acme Admin" is a role, not a name.
_GENERIC_NAME_WORDS = ("admin", "support", "team", "ops", "owner", "user", "lead")


def _person_key(raw: str) -> str:
    """Normalize a name or handle for merging: the display name from
    Gmail ("Jane CSM <jane@cs-team.example>"), the handle from Gong
    ("jane-csm"), and the Salesforce owner ("Jane CSM") are the same
    person. The email part, case, and punctuation are dropped."""
    name = str(raw or "").split("<")[0].strip()
    cleaned = "".join(ch if ch.isalnum() else " " for ch in name.lower())
    return " ".join(cleaned.split())


def _is_handle(raw: str) -> bool:
    """True when the name is only a handle: lowercase, hyphenated,
    no space, like "acme-ops-lead". A display name with a space is a
    name, even when it is a role label like "Acme Admin"."""
    name = str(raw or "").split("<")[0].strip()
    return " " not in name and "-" in name


def _is_generic_label(name: str) -> bool:
    """True when the display name is a function label, not a person's
    name: any word in it is a generic role word."""
    words = _person_key(name).split()
    return any(word in _GENERIC_NAME_WORDS for word in words)


def _stakeholder_role(person: dict) -> str:
    """Role from record evidence only. A champion record makes a
    champion; a moved champion is a former champion; the account
    owner is the account owner. A budget title (CFO, finance,
    procurement) makes an economic buyer. Anything else is unknown,
    never guessed from a first name or a handle."""
    if person["champion_status"] == "active":
        return "champion"
    if person["champion_status"] == "moved":
        return "former champion"
    title = (person.get("title") or "").lower()
    if any(word in title for word in _BUDGET_TITLE_WORDS):
        return "economic buyer"
    if person["is_owner"]:
        return "account owner"
    return "unknown"


def _stakeholder_influence(person: dict) -> str:
    """Influence scored on observable signals, never assumed: an
    active champion (+2), a senior title (+1), presence across three
    or more sources (+1). High at 3, medium at 1. A person known only
    by a handle, or who left the account, scores unknown: there is
    nothing to score. Influence is a customer-side measure; our own
    team is listed without one."""
    if person["side"] == "internal":
        return "n/a"
    if person["champion_status"] == "moved":
        return "unknown"
    if person["handle_only"]:
        return "unknown"
    score = 0
    if person["champion_status"] == "active":
        score += 2
    title_words = (person.get("title") or "").lower().split()
    title = " ".join(title_words)
    if any(word in title_words for word in _SENIOR_TITLE_WORDS) or (
        "vice president" in title
    ):
        score += 1
    if len(person["sources"]) >= 3:
        score += 1
    if score >= 3:
        return "high"
    if score >= 1:
        return "medium"
    return "low"


def _stakeholder_people(results: dict) -> list[dict]:
    """The people on the account, merged across the gathered records.
    One entry per person, keyed by normalized name, so "Jane CSM"
    (Salesforce owner, Gmail author) and "jane-csm" (Gong, Zoom,
    Slack) are one person, not three. Only people the records name;
    nobody is invented. Side (customer vs our team) is decided from
    the records: the account owner is ours, an email from the owner's
    domain is ours, and a handle ending in -csm or -se is ours."""
    people: dict[str, dict] = {}

    def person_for(raw: str) -> dict | None:
        key = _person_key(raw)
        if not key:
            return None
        person = people.get(key)
        if person is None:
            display = str(raw).split("<")[0].strip()
            person = {
                "key": key,
                "name": display,
                "title": "",
                "side": "customer",
                "sources": set(),
                "evidence": [],
                "champion_status": None,
                "champion_since": None,
                "moved_to": None,
                "promoted": None,
                "is_owner": False,
                "handle_only": _is_handle(display),
                "emails": set(),
            }
            people[key] = person
        return person

    def prefer_display(person: dict, raw: str) -> None:
        """Keep the most name-like display form: a display name with
        a space beats a hyphenated handle."""
        display = str(raw).split("<")[0].strip()
        if " " in display and _is_handle(person["name"]):
            person["name"] = display
            person["handle_only"] = False

    account = results.get("salesforce_get_account") or {}
    owner = str(account.get("owner") or "").strip()
    owner_key = _person_key(owner)
    if owner:
        person = person_for(owner)
        person["side"] = "internal"
        person["is_owner"] = True
        person["sources"].add("salesforce_get_account")
        person["evidence"].append("Account owner (Salesforce)")

    champions = results.get("champify_list_champions") or {}
    for champ in champions.get("champions", []) or []:
        name = str(champ.get("name") or "").strip()
        if not name:
            continue
        person = person_for(name)
        person["title"] = str(champ.get("role") or "")
        person["champion_status"] = (
            str(champ.get("status") or "").lower() or None
        )
        person["champion_since"] = champ.get("champion_since")
        person["sources"].add("champify_list_champions")
        person["evidence"].append(
            "Champion record (%s, %s)"
            % (
                person["title"] or "unknown title",
                person["champion_status"] or "unknown status",
            )
        )

    changes = results.get("champify_list_job_changes") or {}
    for change in changes.get("job_changes", []) or []:
        name = str(change.get("name") or "").strip()
        if not name:
            continue
        person = person_for(name)
        person["sources"].add("champify_list_job_changes")
        prev_company = change.get("previous_company")
        new_company = change.get("new_company")
        changed_at = change.get("changed_at") or "date unknown"
        if new_company and prev_company and new_company != prev_company:
            person["moved_to"] = {
                "company": new_company,
                "role": change.get("new_role"),
                "changed_at": change.get("changed_at"),
            }
            person["evidence"].append(
                "Moved %s -> %s (%s)" % (prev_company, new_company, changed_at)
            )
        elif new_company and new_company == prev_company:
            person["promoted"] = {
                "from": change.get("previous_role"),
                "to": change.get("new_role"),
                "changed_at": change.get("changed_at"),
            }
            person["evidence"].append(
                "Promoted %s -> %s (%s)"
                % (
                    change.get("previous_role") or "unknown role",
                    change.get("new_role") or "unknown role",
                    changed_at,
                )
            )

    def count_appearances(
        items: list, key_fn, source: str, label: str, one: str, many: str
    ) -> None:
        counts: dict[str, int] = {}
        for item in items:
            raw = key_fn(item)
            if not raw:
                continue
            person = person_for(raw)
            if person is None:
                continue
            prefer_display(person, raw)
            if "<" in str(raw) and ">" in str(raw):
                person["emails"].add(
                    str(raw).split("<", 1)[1].split(">", 1)[0].strip()
                )
            person["sources"].add(source)
            counts[person["key"]] = counts.get(person["key"], 0) + 1
        for key, count in counts.items():
            people[key]["evidence"].append(
                "%s (%d %s)" % (label, count, one if count == 1 else many)
            )

    gmail = results.get("gmail_search_messages") or {}
    count_appearances(
        gmail.get("matches", []) or [],
        lambda m: m.get("author", ""),
        "gmail_search_messages",
        "Email author",
        "message",
        "messages",
    )
    calls = results.get("gong_list_calls") or {}
    call_people: list[str] = []
    for call in calls.get("calls", []) or []:
        call_people.extend(call.get("participants", []) or [])
    count_appearances(
        call_people,
        lambda p: p,
        "gong_list_calls",
        "Call participant",
        "call",
        "calls",
    )
    gong_transcripts = results.get("gong_search_transcripts") or {}
    count_appearances(
        gong_transcripts.get("matches", []) or [],
        lambda m: m.get("speaker", ""),
        "gong_search_transcripts",
        "Gong transcript speaker",
        "excerpt",
        "excerpts",
    )
    recordings = results.get("zoom_list_recordings") or {}
    count_appearances(
        recordings.get("recordings", []) or [],
        lambda r: r.get("host", ""),
        "zoom_list_recordings",
        "Recording host",
        "recording",
        "recordings",
    )
    zoom_transcripts = results.get("zoom_search_transcripts") or {}
    count_appearances(
        zoom_transcripts.get("matches", []) or [],
        lambda m: m.get("speaker", ""),
        "zoom_search_transcripts",
        "Zoom transcript speaker",
        "excerpt",
        "excerpts",
    )
    slack = results.get("slack_search_messages") or {}
    count_appearances(
        slack.get("matches", []) or [],
        lambda m: m.get("author", ""),
        "slack_search_messages",
        "Slack author",
        "message",
        "messages",
    )

    # Side: our team. The owner is ours. An email from the owner's
    # domain is ours (the stub's cs-team.example; live, whatever
    # domain the owner's address is on). A handle ending in -csm or
    # -se is ours: the harness's own team suffixes.
    owner_domains = set()
    if owner_key in people:
        for email in people[owner_key]["emails"]:
            if "@" in email:
                owner_domains.add(email.split("@", 1)[1].lower())
    for person in people.values():
        if person["is_owner"]:
            person["side"] = "internal"
            continue
        if any(
            "@" in email and email.split("@", 1)[1].lower() in owner_domains
            for email in person["emails"]
        ):
            person["side"] = "internal"
            continue
        if person["name"].lower().endswith(("-csm", "-se")):
            person["side"] = "internal"
    return sorted(people.values(), key=lambda p: p["name"].lower())


def _stakeholder_relationships(results: dict) -> list[dict]:
    """Pairs of people who appear together in the gathered records:
    the same Gong call, or the same Gmail thread. Only co-appearance
    the records show; nothing is inferred from a shared account
    alone."""
    pairs: dict[tuple[str, str], dict] = {}

    def note(first: str, second: str, kind: str) -> None:
        a, b = sorted((_person_key(first), _person_key(second)))
        if not a or not b or a == b:
            return
        pair = pairs.setdefault((a, b), {"calls": 0, "threads": 0})
        pair[kind] += 1

    calls = results.get("gong_list_calls") or {}
    for call in calls.get("calls", []) or []:
        participants = call.get("participants", []) or []
        for i in range(len(participants)):
            for j in range(i + 1, len(participants)):
                note(participants[i], participants[j], "calls")
    gmail = results.get("gmail_search_messages") or {}
    threads: dict[str, set] = {}
    for match in gmail.get("matches", []) or []:
        author = str(match.get("author") or "").strip()
        if not author:
            continue
        threads.setdefault(str(match.get("thread_id") or ""), set()).add(author)
    for authors in threads.values():
        ordered = sorted(authors)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                note(ordered[i], ordered[j], "threads")
    return [
        {"a": a, "b": b, "calls": pair["calls"], "threads": pair["threads"]}
        for (a, b), pair in sorted(pairs.items())
    ]


def _is_budget_holder(person: dict) -> bool:
    """True when the person controls budget: the role says so, or
    the recorded title does. A CFO who is also an active champion
    is a champion in the map, but the buyer gap must still see her
    as the budget holder."""
    if _stakeholder_role(person) == "economic buyer":
        return True
    title = (person.get("title") or "").lower()
    return any(word in title for word in _BUDGET_TITLE_WORDS)


def _stakeholder_gaps(
    people: list[dict], relationships: list[dict]
) -> list[str]:
    """Coverage gaps from the map's own evidence: no economic buyer,
    a departed champion with no successor named, a stakeholder known
    only by a display name or handle, and no visible relationship
    between two customer stakeholders. A gap the evidence does not
    support is not listed."""
    gaps: list[str] = []
    customers = [p for p in people if p["side"] == "customer"]
    if not any(_is_budget_holder(p) for p in customers):
        gaps.append(
            "No economic buyer identified in the gathered records; "
            "nobody in the map controls budget."
        )
    for person in customers:
        if person["champion_status"] != "moved":
            continue
        moved = person.get("moved_to") or {}
        gaps.append(
            "%s (%s, former champion) left the account%s; no successor "
            "is named in any record."
            % (
                person["name"],
                person["title"] or "unknown title",
                " on %s" % moved["changed_at"]
                if moved.get("changed_at")
                else "",
            )
        )
    label_only = [
        p
        for p in customers
        if p["handle_only"] or _is_generic_label(p["name"])
    ]
    if label_only:
        gaps.append(
            "%s known only by a display name or handle; real names and "
            "roles are unknown."
            % (
                " and ".join(sorted(p["name"] for p in label_only))
                if len(label_only) > 1
                else label_only[0]["name"] + " is"
            )
        )
    by_key = {p["key"]: p for p in people}
    customer_pairs = [
        r
        for r in relationships
        if by_key.get(r["a"], {}).get("side") == "customer"
        and by_key.get(r["b"], {}).get("side") == "customer"
    ]
    if customers and not customer_pairs:
        gaps.append(
            "No two customer stakeholders appear together in any call "
            "or thread in the gathered records; how they connect to "
            "each other is unknown."
        )
    if not customers:
        gaps.append(
            "No customer stakeholders found in the gathered records; "
            "the whole map is a gap."
        )
    return gaps


def _stakeholder_map_block(
    people: list[dict], relationships: list[dict], gaps: list[str]
) -> str:
    """The harness-computed stakeholder map appended to the draft:
    each person with role and influence and the evidence behind both,
    the relationships the records show, and the coverage gaps. No
    people means the map is empty and the gaps say so, never an
    invented stakeholder."""
    if not people:
        return (
            "Stakeholder map (harness-computed): No stakeholders found "
            "in the gathered records for this account. No people, no "
            "roles, no influence to score. Coverage gaps: the whole map "
            "is a gap: no champion, no economic buyer, no end user "
            "identified."
        )
    lines = ["Stakeholder map (harness-computed):", ""]
    customers = [p for p in people if p["side"] == "customer"]
    internal = [p for p in people if p["side"] == "internal"]
    by_key = {p["key"]: p for p in people}
    lines.append("Customer stakeholders:")
    for person in customers:
        role = _stakeholder_role(person)
        influence = _stakeholder_influence(person)
        detail = "- %s: %s" % (person["name"], role)
        if person["title"] and role in (
            "champion",
            "former champion",
            "economic buyer",
        ):
            detail += " (%s)" % person["title"]
        detail += ", influence %s." % influence
        moved = person.get("moved_to") or {}
        if moved.get("company"):
            detail += " Left the account %s, now %s at %s." % (
                moved.get("changed_at") or "date unknown",
                moved.get("role") or "unknown role",
                moved["company"],
            )
        promoted = person.get("promoted") or {}
        if promoted.get("to"):
            detail += " Promoted %s -> %s." % (
                promoted.get("from") or "unknown role",
                promoted["to"],
            )
        detail += " Evidence: %s." % "; ".join(person["evidence"])
        lines.append(detail)
    if internal:
        lines.append("")
        lines.append("Our side:")
        for person in internal:
            role = _stakeholder_role(person)
            detail = "- %s: %s (internal)." % (person["name"], role)
            detail += " Evidence: %s." % "; ".join(person["evidence"])
            lines.append(detail)
    lines.append("")
    lines.append("Relationships:")
    shown = [
        r
        for r in relationships
        if not (
            by_key.get(r["a"], {}).get("side") == "internal"
            and by_key.get(r["b"], {}).get("side") == "internal"
        )
    ]
    if not shown:
        lines.append(
            "- No two people appear together in any call or thread in "
            "the gathered records."
        )
    for rel in shown:
        a = by_key.get(rel["a"], {}).get("name", rel["a"])
        b = by_key.get(rel["b"], {}).get("name", rel["b"])
        parts = []
        if rel["calls"]:
            parts.append(
                "%d call%s" % (rel["calls"], "s" if rel["calls"] != 1 else "")
            )
        if rel["threads"]:
            parts.append(
                "%d email thread%s"
                % (rel["threads"], "s" if rel["threads"] != 1 else "")
            )
        lines.append("- %s and %s: together on %s." % (a, b, " and in ".join(parts)))
    lines.append("")
    lines.append("Coverage gaps:")
    for gap in gaps:
        lines.append("- %s" % gap)
    return "\n".join(lines)


class StakeholderMapperAgent(Agent):
    """Builds the relationship map for an account.

    Gathers: account summary, champion list and job changes, recent
    email threads, Gong calls and transcripts, Zoom recordings and
    transcripts, and recent Slack signals. Drafts: a stakeholder map
    with each person, their role and influence, how they connect, and
    the coverage gaps.

    Composition (v1.1.0): when prior drafts from advocate-finder,
    risk-watcher, and voice-of-customer exist for the account, the map
    is built from those drafts instead of re-gathering the same
    context; only the fresh person signals (account record, champion
    list and job changes, Gmail, Gong calls, Zoom recordings, Slack)
    are gathered directly, and the transcripts are left to the source
    drafts. When no prior drafts exist, the agent gathers directly,
    its previous behavior.

    Roles and influence are harness-computed, never model prose: a
    champion record makes a champion, a budget title makes an
    economic buyer, anything else is unknown; influence is scored on
    observable signals (active champion, senior title, presence
    across sources) and unknown where there is nothing to score. A
    person, role, or relationship the records do not support never
    appears. With no people at all the map is empty, stated as such.
    """

    name = "stakeholder-mapper"
    display_name = "Stakeholder Mapper"
    description = (
        "Builds the relationship map: users, champions, economic "
        "buyers, blockers, and how they connect. Records influence, "
        "not just titles."
    )
    keywords = [
        "stakeholder",
        "stakeholders",
        "stakeholder-mapper",
        "stakeholder mapper",
        "relationship map",
        "org chart",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Lists each stakeholder with role and influence",
            keywords=["stakeholder map"],
        ),
        ProofItem(
            "Names the coverage gaps",
            keywords=["coverage gaps"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _STAKEHOLDER_SOURCE_AGENTS
        )
        if composed_text:
            # Composed: build from the prior drafts. Only the fresh
            # person signals are gathered directly; the source drafts
            # carry the transcripts and the broader account context,
            # and their deep links carry through to the composed draft.
            # The draft carries a harness-written provenance line
            # naming the agents it was composed from.
            calls = [
                ("salesforce_get_account", {"account": account}),
                ("champify_list_champions", {"account": account}),
                ("champify_list_job_changes", {"account": account}),
                ("gmail_search_messages", {"query": account, "limit": 5}),
                ("gong_list_calls", {"account": account, "days": 30}),
                ("zoom_list_recordings", {"account": account, "days": 30}),
                ("slack_search_messages", {"query": account, "limit": 5}),
            ]
            context, sources, links, results = _gather_with_results(
                registry, calls, budget, job=job
            )
            context["prior_agent_drafts"] = composed_text
            seen_urls = {link.get("url") for link in links}
            for link in composed_links:
                if link.get("url") not in seen_urls:
                    seen_urls.add(link.get("url"))
                    links.append(link)
            composed = True
        else:
            # Fallback: no prior drafts for this account, gather directly.
            calls = [
                ("salesforce_get_account", {"account": account}),
                ("champify_list_champions", {"account": account}),
                ("champify_list_job_changes", {"account": account}),
                ("gmail_search_messages", {"query": account, "limit": 5}),
                ("gong_list_calls", {"account": account, "days": 30}),
                ("gong_search_transcripts", {"query": account, "limit": 5}),
                ("zoom_list_recordings", {"account": account, "days": 30}),
                ("zoom_search_transcripts", {"query": account, "limit": 5}),
                ("slack_search_messages", {"query": account, "limit": 5}),
            ]
            context, sources, links, results = _gather_with_results(
                registry, calls, budget, job=job
            )
            composed = False
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        people = _stakeholder_people(results)
        relationships = _stakeholder_relationships(results)
        gaps = _stakeholder_gaps(people, relationships)
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a stakeholder map for account %r. Cover: each "
            "person with their role and influence, how they connect to "
            "each other, and the coverage gaps. Use only the context "
            "below. Never invent a person, a role, or a relationship "
            "the context does not support; say unknown where the "
            "records do not say." % account,
        )
        system = (
            "You are the stakeholder-mapper agent for a customer "
            "success team. You map the people on an account before "
            "anyone acts on it: who the champions are, who controls "
            "budget, who is missing. A role the records do not support "
            "is unknown, never guessed; a relationship nobody recorded "
            "does not exist in the map."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + _stakeholder_map_block(people, relationships, gaps)
        checklist = list(self.proof_checklist)
        if composed:
            text += (
                "\n\nComposed from prior drafts: %s."
                % ", ".join(composed_agents)
            )
            checklist += [
                ProofItem(
                    "Composed from prior agent drafts",
                    keywords=["composed from prior"],
                )
            ]
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            ),
            checklist=checklist,
        )


# The three agents whose drafts escalation-manager composes from
# (v1.7.0): risk-watcher first (its brief is the escalation's
# trigger), plus renewal-desk and voice-of-customer.
_ESCALATION_SOURCE_AGENTS = ("risk-watcher", "renewal-desk", "voice-of-customer")


def _escalation_store(job: dict) -> EscalationStore:
    """The escalation store for a job. The file the job names, or an
    in-memory store when it names none: a direct agent call in a test
    never touches the working directory."""
    path = job.get("escalations_file")
    return EscalationStore(path) if path else EscalationStore(None)


def _pending_escalation_record_ids(job: dict, account: str) -> set[str]:
    """Record ids on escalation-manager drafts still pending in the
    queue for this account. A plan awaiting a human decision is a
    duplicate in waiting: the next run must not draft another plan
    for the same signal while it sits undecided."""
    path = job.get("approvals_file")
    if not path:
        return set()
    try:
        queue = ApprovalQueue(path)
    except Exception:
        return set()
    ids: set[str] = set()
    for item in queue.list_pending():
        if item.agent != "escalation-manager":
            continue
        if _extract_account(item.job) != account:
            continue
        for record_id in item.job.get("escalation_records", []) or []:
            ids.add(str(record_id))
    return ids


def _escalation_ask(signal: dict) -> str:
    """The ask for one high-severity signal, harness-written per kind."""
    kind = signal.get("kind", "")
    if kind == "champion-departure":
        return (
            "Confirm with the account whether the mandate that bought "
            "the product changed, and who picks it up now."
        )
    if kind == "support-delivery":
        return (
            "Get a named owner and a fix date for the issue, and a "
            "status update the CSM can send the customer."
        )
    if kind == "renewal-stalled":
        return (
            "Confirm whether the renewal is still alive, who owns the "
            "close, and what the real close date is."
        )
    return "Confirm who owns this signal and what the next step is."


def _escalation_fallback(signal: dict) -> str:
    """What happens if the ask does not land, harness-written per kind."""
    kind = signal.get("kind", "")
    if kind == "champion-departure":
        return (
            "If the mandate is gone, the save plan becomes an exit "
            "plan: protect the exit, keep the relationship, stop "
            "spending save effort on a dead deal."
        )
    if kind == "support-delivery":
        return (
            "If no owner or fix date lands, take it to the account's "
            "executive sponsor with the case history attached."
        )
    if kind == "renewal-stalled":
        return (
            "If the renewal is dead, close it out honestly and move "
            "the effort to the next renewal."
        )
    return "If nothing lands, re-raise it with the account's sponsor."


def _people_to_loop_in(results: dict) -> list[str]:
    """Who to loop in, from the gathered records: the account owner
    and the active champions, with their roles. Only people the
    records name; nobody is invented."""
    people: list[str] = []
    account = results.get("salesforce_get_account") or {}
    owner = account.get("owner")
    if owner:
        people.append("%s (account owner, Salesforce)" % owner)
    champions = results.get("champify_list_champions") or {}
    for champ in champions.get("champions", []) or []:
        if str(champ.get("status", "")).lower() == "active":
            people.append(
                "%s (%s, active champion)"
                % (champ.get("name", "?"), champ.get("role", "?"))
            )
    return people


def _escalation_plan_block(
    signals: list[dict],
    records: list,
    account: str,
    results: dict,
) -> str:
    """The harness-written escalation plan: situation with evidence,
    people to loop in, the ask, the exec update and customer comms as
    drafts, and the fallback. Harness-written so the plan is in the
    draft even when the model is the stub."""
    people = _people_to_loop_in(results)
    champions = results.get("champify_list_champions") or {}
    active = [
        champ.get("name", "?")
        for champ in champions.get("champions", []) or []
        if str(champ.get("status", "")).lower() == "active"
    ]
    addressee = active[0] if active else "team"
    owner = (results.get("salesforce_get_account") or {}).get("owner") or "the account owner"
    kinds = ", ".join(sorted({signal["kind"] for signal in signals}))
    lines = [
        "Escalation plan (harness-computed):",
        "Overall severity: high.",
        "Escalation records: %s."
        % (
            ", ".join(
                "%s (%s)" % (record.id, record.signal_key)
                for record in records
            )
            if records
            else "none recorded"
        ),
        "",
        "Situation:",
    ]
    for signal in signals:
        lines.append("- [%s] %s" % (signal["severity"], signal["text"]))
    lines.append("")
    lines.append("People to loop in:")
    if people:
        for person in people:
            lines.append("- " + person)
    else:
        lines.append(
            "- No active champions or account owner named in the "
            "gathered records; find the sponsor before sending this."
        )
    lines.append("")
    lines.append("The ask:")
    for signal in signals:
        lines.append("- %s" % _escalation_ask(signal))
    lines.append("")
    first = signals[0]
    lines.append("Exec update (draft):")
    lines.append(
        "Escalation for %s: %d high-severity signal(s) (%s). %s The "
        "ask is: %s. If it does not land: %s."
        % (
            account,
            len(signals),
            kinds,
            first["text"],
            _escalation_ask(first).rstrip("."),
            _escalation_fallback(first).rstrip("."),
        )
    )
    lines.append("")
    lines.append("Customer comms (draft):")
    if first["kind"] == "champion-departure":
        dep = first.get("record") or {}
        lines.append(
            "Hi %s, we heard %s moved on to %s as %s — "
            "congratulations to them. We want to make sure the work we "
            "do together keeps its footing: can we find 20 minutes to "
            "confirm what matters most to you now, and who owns it on "
            "your side?"
            % (
                addressee,
                dep.get("name", "your champion"),
                dep.get("new_company", "?"),
                dep.get("new_role", "?"),
            )
        )
    else:
        lines.append(
            "Hi %s, flagging that we are escalating this on our side: "
            "%s %s is driving it from our side, and we will update "
            "you as soon as we have a named owner and a fix date. If "
            "you have anything to add — impact, timing, who else "
            "feels it — send it over."
            % (addressee, first["text"], owner)
        )
    lines.append("")
    lines.append("If this does not land:")
    for signal in signals:
        lines.append("- %s" % _escalation_fallback(signal))
    return "\n".join(lines)


def _escalation_status_block(account: str, records: list) -> str:
    """The status note a run drafts when nothing new needs
    escalating but the account has escalation records. Names each
    record and its state; it is a report, not a plan."""
    lines = [
        "Escalation status for %s:" % account,
        "Overall severity: high.",
    ]
    for record in records:
        line = "- %s: %s is %s" % (
            record.id,
            record.signal_key,
            record.status,
        )
        if record.status == "open" and record.draft_id:
            line += ", awaiting a decision on draft %s" % record.draft_id
        if record.status == "escalated":
            line += " (plan approved, in flight)"
        if record.status == "resolved":
            line += " (resolved; a resolved escalation stays resolved)"
        lines.append(line)
    lines.append(
        "No new escalation is drafted: every high-severity signal for "
        "this account already has a record."
    )
    return "\n".join(lines)


def _escalation_closure_block(records: list, account: str) -> str:
    """The closure confirmation for escalated records whose signal is
    no longer detected. Approving this draft marks the records
    resolved: resolving is a human decision recorded through the
    queue outcome, never the agent's own call."""
    lines = [
        "Escalation closure: the signal behind these escalations was "
        "not detected in this run's fresh gather for %s:" % account,
        "Overall severity: high.",
    ]
    for record in records:
        lines.append(
            "- %s: %s (escalated since %s)"
            % (record.id, record.signal_key, record.created_at)
        )
    lines.append(
        "Approve this draft to mark them resolved. Reject it to keep "
        "them escalated."
    )
    return "\n".join(lines)


class EscalationManagerAgent(Agent):
    """Owns escalations end to end.

    Gathers: account summary, open opportunities, open support cases,
    champion job changes and the champion list, recent email threads,
    Gong and Zoom transcripts, Zendesk tickets, Linear issues, and
    recent Slack signals — the same escalation-relevant signals
    risk-watcher watches. Drafts: an escalation plan with the
    situation and evidence, the people to loop in, the ask, the exec
    update and the customer comms as drafts, and the fallback if it
    does not land.

    Composition (v1.1.0): when prior drafts from risk-watcher,
    renewal-desk, and voice-of-customer exist for the account, the
    plan is built from those drafts instead of re-gathering the same
    context; only the fresh escalation signals are gathered directly.
    When no prior drafts exist, the agent gathers directly, its
    previous behavior.

    State (v1.7.0): one record per signal per account in the
    escalation store (open, escalated, resolved). A second run for
    the same signal drafts a status note, not a second plan;
    approving a plan moves its record to escalated; a resolved
    record stays resolved even if the signal fires again. Severity,
    the ask, and the fallback are harness-computed, never model
    prose. With no high-severity signals the draft says no escalation
    is needed, stated as such.
    """

    name = "escalation-manager"
    display_name = "Escalation Manager"
    description = (
        "Owns escalations: drafts the escalation plan (situation, "
        "people, ask, exec update, customer comms, fallback) and "
        "tracks each escalation open, escalated, resolved."
    )
    keywords = [
        "escalation",
        "escalation-manager",
        "escalate",
        "escalation plan",
        "exec update",
        "customer comms",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "States the overall severity",
            keywords=["overall severity"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        store = _escalation_store(job)
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _ESCALATION_SOURCE_AGENTS
        )
        if composed_text:
            # Composed: build from the prior drafts. Only the fresh
            # escalation signals are gathered directly; the source
            # drafts carry the opportunities, Slack, transcripts,
            # Gmail, and Linear context, and their deep links carry
            # through to the composed draft.
            calls = [
                ("salesforce_get_account", {"account": account}),
                ("salesforce_list_opportunities", {"account": account}),
                ("salesforce_list_cases", {"account": account}),
                ("champify_list_job_changes", {"account": account, "days": 90}),
                ("champify_list_champions", {"account": account}),
                ("zendesk_list_tickets", {"organization": account}),
            ]
            context, sources, links, results = _gather_with_results(
                registry, calls, budget, job=job
            )
            context["prior_agent_drafts"] = composed_text
            seen_urls = {link.get("url") for link in links}
            for link in composed_links:
                if link.get("url") not in seen_urls:
                    seen_urls.add(link.get("url"))
                    links.append(link)
            composed = True
        else:
            # Fallback: no prior drafts for this account, gather directly.
            calls = [
                ("salesforce_get_account", {"account": account}),
                ("salesforce_list_opportunities", {"account": account}),
                ("salesforce_list_cases", {"account": account}),
                ("champify_list_job_changes", {"account": account, "days": 90}),
                ("champify_list_champions", {"account": account}),
                ("gmail_search_messages", {"query": account, "limit": 5}),
                ("gong_search_transcripts", {"query": account, "limit": 5}),
                ("zoom_search_transcripts", {"query": account, "limit": 5}),
                ("zendesk_list_tickets", {"organization": account}),
                ("linear_list_issues", {}),
                ("slack_search_messages", {"query": account, "limit": 5}),
            ]
            context, sources, links, results = _gather_with_results(
                registry, calls, budget, job=job
            )
            composed = False
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        signals = _risk_signals(results, account)
        high = [s for s in signals if s["severity"] == "high"]
        current_keys = {s.get("key") for s in signals if s.get("key")}
        pending = _pending_escalation_record_ids(job, account)

        # Classify each high signal against the store: new (draft a
        # plan), or already tracked (report its state, draft nothing
        # new for it). A resolved record stays resolved even when the
        # signal fires again. An open record with a plan still pending
        # in the queue is a duplicate in waiting. An open record with
        # no pending draft means a previous plan was decided against,
        # so the risk is still open and a fresh plan may be drafted;
        # the rejection is in memory, so the next draft knows.
        new_signals: list[dict] = []
        new_records: list = []
        existing_lines: list[str] = []
        for signal in high:
            key = signal.get("key") or ""
            record = store.record_for(account, key) if key else None
            if record is not None and record.status == "resolved":
                existing_lines.append(
                    "- %s (%s) is resolved; a resolved escalation "
                    "stays resolved, so no new plan is drafted."
                    % (record.id, record.signal_key)
                )
            elif record is not None and record.status == "escalated":
                existing_lines.append(
                    "- %s (%s) is already escalated (plan approved, "
                    "in flight); no new plan is drafted."
                    % (record.id, record.signal_key)
                )
            elif record is not None and record.id in pending:
                existing_lines.append(
                    "- %s (%s) already has a plan awaiting a decision "
                    "in the queue; no duplicate is drafted."
                    % (record.id, record.signal_key)
                )
            else:
                if record is None and key:
                    record = store.create(
                        account,
                        key,
                        signal["kind"],
                        signal["severity"],
                        signal["text"],
                    )
                new_signals.append(signal)
                if record is not None:
                    new_records.append(record)

        # Closures: escalated records whose signal is gone. Only on a
        # direct gather: in the composed path some tools are not
        # re-gathered, so a missing signal there proves nothing about
        # the risk. The agent drafts the closure; the human approves
        # it, and the approval resolves the record.
        closure_records: list = []
        if not composed:
            for record in store.for_account(account):
                if (
                    record.status == "escalated"
                    and record.signal_key not in current_keys
                ):
                    closure_records.append(record)

        # `prompt` is defined by the plan and clean branches; the
        # status branch drafts without a model call, so it starts
        # empty and the token estimate at the end stays valid.
        prompt = ""
        if new_signals or closure_records:
            if new_records:
                job["escalation_records"] = [r.id for r in new_records]
            if closure_records:
                job["escalation_resolve"] = [r.id for r in closure_records]
            if new_signals:
                task = (
                    "Draft an escalation plan for account %r. Cover: "
                    "the situation and the evidence for each "
                    "high-severity signal, who to loop in and why, the "
                    "ask, an exec update and customer comms as drafts, "
                    "and what happens if it does not land. Do not "
                    "invent a signal that is not in the context."
                    % account
                )
            else:
                task = (
                    "Account %r has escalated records whose signals "
                    "were not detected in this run's fresh gather. "
                    "Draft a short closure confirmation naming them "
                    "and asking the human to approve it to mark them "
                    "resolved. Do not invent a signal that is not in "
                    "the context." % account
                )
            prompt = _build_prompt(self.name, context, task)
            system = (
                "You are the escalation manager for a customer success "
                "team. You own escalations end to end: the plan, the "
                "people, the ask, and the fallback. Cite the evidence "
                "and do not invent facts that are not in the context."
            )
            text = llm.complete(prompt, system=system)
            if new_signals:
                text = text + "\n\n" + _escalation_plan_block(
                    new_signals, new_records, account, results
                )
            if closure_records:
                text = text + "\n\n" + _escalation_closure_block(
                    closure_records, account
                )
            if existing_lines:
                text = text + "\n\nAlready tracked for this account:\n" + "\n".join(
                    existing_lines
                )
            checklist = list(self.proof_checklist)
            if new_signals:
                checklist += [
                    ProofItem(
                        "Gives the escalation plan",
                        keywords=["escalation plan"],
                    ),
                    ProofItem(
                        "Names who to loop in",
                        keywords=["people to loop in"],
                    ),
                    ProofItem(
                        "States the ask",
                        keywords=["the ask"],
                    ),
                    ProofItem(
                        "Drafts the exec update",
                        keywords=["exec update"],
                    ),
                    ProofItem(
                        "Drafts the customer comms",
                        keywords=["customer comms"],
                    ),
                    ProofItem(
                        "States the fallback",
                        keywords=["if this does not land"],
                    ),
                ]
            if closure_records:
                checklist += [
                    ProofItem(
                        "Names the escalations to close",
                        keywords=["escalation closure"],
                    ),
                    ProofItem(
                        "Says approval resolves them",
                        keywords=["mark them resolved"],
                    ),
                ]
        elif store.for_account(account):
            # Nothing new to draft, but the account has escalations:
            # report their state instead of drafting a second plan.
            text = _escalation_status_block(
                account, store.for_account(account)
            )
            checklist = list(self.proof_checklist)
            checklist += [
                ProofItem(
                    "Reports the escalation status",
                    keywords=["escalation status"],
                ),
                ProofItem(
                    "Says no new escalation is drafted",
                    keywords=["no new escalation is drafted"],
                ),
            ]
        else:
            # No high-severity signals and no escalations on file.
            prompt = _build_prompt(
                self.name,
                context,
                "Account %r has no high-severity signals in the "
                "gathered context. Draft a short note saying no "
                "escalation is needed and why. Do not invent a signal "
                "that is not in the context." % account,
            )
            system = (
                "You are the escalation manager for a customer success "
                "team. You escalate only on evidence: no high-severity "
                "signal means no escalation."
            )
            text = llm.complete(prompt, system=system)
            overall = signals[0]["severity"] if signals else "none"
            text = text + (
                "\n\nNo escalation needed for %s: no high-severity "
                "signals in the gathered records. Overall severity: %s."
                % (account, overall)
            )
            checklist = list(self.proof_checklist)
            checklist += [
                ProofItem(
                    "Says no escalation is needed",
                    keywords=["no escalation needed"],
                ),
            ]
        if composed:
            text += (
                "\n\nComposed from prior drafts: %s."
                % ", ".join(composed_agents)
            )
            checklist += [
                ProofItem(
                    "Composed from prior agent drafts",
                    keywords=["composed from prior"],
                )
            ]
        return self._finish(
            Draft(
                text=text,
                agent=self.name,
                job=job,
                sources=sources,
                links=links,
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            ),
            checklist=checklist,
        )


def build_agents() -> dict[str, Agent]:
    """All fourteen agents, keyed by name. All implemented."""
    agents: dict[str, Agent] = {
        "renewal-desk": RenewalDeskAgent(),
        "briefer": BrieferAgent(),
        "expansion-spotter": ExpansionSpotterAgent(),
        "onboarding-guide": OnboardingGuideAgent(),
        "scout": ScoutAgent(),
        "qbr-builder": QBRBuilderAgent(),
        "advocate-finder": AdvocateFinderAgent(),
        "voice-of-customer": VoiceOfCustomerAgent(),
        "value-finder": ValueFinderAgent(),
        "salesforce-updater": SalesforceUpdaterAgent(),
        "risk-watcher": RiskWatcherAgent(),
        "escalation-manager": EscalationManagerAgent(),
        "competitive-defense": CompetitiveDefenseAgent(),
        "stakeholder-mapper": StakeholderMapperAgent(),
    }
    return agents


AGENTS: dict[str, Agent] = build_agents()
