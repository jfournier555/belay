"""The specialist agents: the twenty-two post-sales jobs the harness covers.

The twenty-two jobs are the post-sales jobs that matter: scout, briefer,
onboarding guide, expansion spotter, QBR builder, advocate finder,
renewal desk, voice-of-customer, value-finder, salesforce-updater,
risk-watcher, escalation-manager, competitive-defense,
stakeholder-mapper, case-triage, champion-change, handoff,
adoption-gaps, call-recap-and-commitments, trust-repair, ebr-prep, and
churn-post-mortem.

V1 implements twenty-two end to end:
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
  most, who praises it in Slack, and who would make a good reference.
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
- risk-watcher: watches for churn signals on an account (ticket spikes,
  stalled renewals, sponsor disengagement) and drafts a risk brief:
  signals ranked by severity, evidence with deep links, overall
  severity, next step. Severity is harness-computed, never model prose.
  With no signals the draft is a clean bill, stated as such.
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

- case-triage: triages the incoming cases, tickets, and issues on an
  account (Salesforce cases, Zendesk tickets, Linear issues, and Gmail
  threads that read like support requests) and drafts a triage brief:
  items grouped by subject across systems, ranked by priority,
  each with a suggested owner and a draft first response or internal
  summary. Priority is harness-computed from the record's own
  severity, age, and blocking impact, never model prose. Items that
  are stale, duplicated across systems, or already covered by another
  agent's draft are flagged as such rather than re-triaged. With no
  open items the draft is a clean bill, stated as such.

- champion-change: catches a champion departure and rebuilds the
  relationship map. Detection is harness-computed: a departure is
  claimed only when an excerpt names the champion and says they left,
  were promoted, or their mail bounces; silence is a medium signal,
  never a departure. The draft names the departed champion with the
  evidence, the relationship-map impact, ranked successor candidates
  (a title is claimed only with a source link), and the rebuild plan
  from the Champion Departure playbook's 7/14/30 spine. With no
  champion on record the draft names the gap, never a departure.
- handoff: handles a sales-to-CS transition or an inherited account.
  Gathers the account record, health signals, open cases and
  escalations, pending drafts, renewal date and stage, open
  opportunities, recent memory, and the champion and stakeholder
  picture, then drafts the handoff brief: the account's current
  state, open items ranked by urgency, risks flagged with evidence,
  what is already in flight, and the first-week actions for the new
  CSM. A missing previous owner or missing history is a named gap,
  never an error. With no prior history the draft is an empty-state
  brief, stated as such.
- adoption-gaps: finds features and teams bought but never used:
  unused seats, unused features, unonboarded teams, ranked by value
  at stake (harness-computed: unused seats x ARR per seat when the
  inputs support it, otherwise unknown and stated as such). A gap
  without evidence is dropped, never drafted; where usage data is
  missing the draft names what to collect instead of guessing. With
  no gaps the draft says so, stated as such.

- trust-repair: recovers trust after an outage, a bad escalation, or a
  missed promise. The hit is harness-computed: claimed only when the job
  names an incident, an escalation record is open, a high-severity
  support failure is detected, a prior commitment is past due, or
  escalation language appears in the records. The draft names the
  trust-hit summary with evidence, the impact on relationships and
  renewals, and the recovery plan: acknowledge and own it, fix it, the
  follow-up cadence, and the proof the customer will see. Each step
  names an owner and a date where the evidence supports one; where it
  does not, the step says so instead of inventing one. With no incident
  named anywhere the draft names the gap, never a recovery for a failure
  that did not happen.
- ebr-prep: prepares an executive business review for a specific
  executive meeting, distinct from qbr-builder's quarterly cadence
  deck: it leads with outcomes and value delivered, not activity, and
  ends with an ask (a renewal, an expansion, or a decision the
  executives must make). The executive summary, the value figures,
  the risks and their mitigations, the ask, the executive audience,
  and the last review's goals are harness-computed; every number
  carries a source, and a missing number is named as a gap. Where the
  last review's goals are not on record the draft says so instead of
  inventing them.
- churn-post-mortem: documents why a churned account left and hands
  the record to sales for a future re-approach, not a CS win-back.
  The churn reason, the re-approach timing, and the re-approach
  contact are harness-computed: a reason is claimed only when
  evidence names it, the window comes from a named date, the
  replacement contract's renewal, or a cooling-off window counted
  from the closed-lost date (labeled proposed), and the contact is a
  successor, the departed champion at a named new company, or the
  account owner. With no evidence the draft names the gap instead of
  inventing a reason, a window, or a contact.

All twenty-two are implemented. Every job in the harness has a working agent.

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




def _linear_base(result: dict) -> str:
    """The base URL for Linear deep links from a tool result."""
    if result.get("_stub"):
        return STUB_LINEAR_HOST
    return LINEAR_APP_HOST




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
        "gcal_list_events",
        "gcal_get_event",
    ):
        # Calendar links are the event URL the API returned (live)
        # or the same path on the .invalid host (stub). The URL is
        # passed through, never constructed, the same way Slack
        # permalinks are.
        if tool_name == "gcal_list_events":
            events = result.get("events", [])
        else:
            events = [result] if result.get("id") else []
        for event in events:
            if event.get("html_link"):
                links.append(
                    {
                        "label": "Calendar: %s"
                        % event.get("summary", event.get("id", "")),
                        "url": event["html_link"],
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
            ("gcal_list_events", {"days": 7, "limit": 5, "account": account}),
        ]
        context, sources, links = _gather(registry, calls, budget, job=job)
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a pre-meeting brief for account %r. Cover: who the "
            "account is, what has changed recently, open items, upcoming "
            "meetings from the calendar, and three "
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
            ("gcal_list_events", {"days": 7, "limit": 5, "account": account}),
        ]
        context, sources, links = _gather(registry, calls, budget, job=job)
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        prompt = _build_prompt(
            self.name,
            context,
            "Scout account %r for new information. Cover: new "
            "stakeholders and org changes, hiring signals, news, upcoming "
            "meetings from the calendar, and any "
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

    Gathers: account summary, open support cases, and recent Slack
    signals. Drafts: who uses the product most, who praises it in
    Slack, who would make a good reference, and any champion already
    known from the account data. For each champion it assesses
    influence from observable signals (who listens to them, defended
    spend, budget control), never assumed, and flags whether a known
    champion has gone quiet, naming the successor and what they care
    about if one is known.
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
            "data, how much influence each champion carries (who "
            "listens to them, have they defended spend, do they "
            "control budget), and whether a known champion has gone "
            "quiet, naming the successor and what they care about if "
            "one is known."
            % account,
        )
        system = (
            "You are the advocate finder for a customer success team. "
            "You find champions and advocates. Name the people, cite the "
            "evidence, and do not invent facts that are not in the "
            "context. Assess influence from evidence, not assumption: "
            "name what shows each champion can move a decision."
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
# renewal-desk health and risks, advocate-finder champions,
# voice-of-customer themes. Same store, same rule: the
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
    as a signal. Open support and delivery
    records group by subject at the worst severity in the group. A renewal
    opportunity past its close date is high; one closing within 30 days
    is medium. Escalation language in messages or transcripts is medium.
    Returns a list of {"severity", "kind", "text"} dicts, high first.
    `today` is injectable so tests can pin the date.
    """
    if today is None:
        from datetime import date as _date

        today = _date.today()
    signals: list[dict] = []

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


def _risk_signals_block(signals: list[dict]) -> str:
    """The harness-computed risk block appended to the draft: overall
    severity and the severity-ranked signals. No signals means a clean
    bill, stated as such, never an invented risk."""
    if not signals:
        return (
            "Risk signals (harness-computed): No churn signals found "
            "in the gathered records. Overall severity: none. No churn "
            "signal in the account record, opportunities, cases, "
            "tickets, issues, messages, or transcripts."
        )
    lines = [
        "Risk signals (harness-computed, severity-ranked):",
        "Overall severity: %s." % signals[0]["severity"],
    ]
    for signal in signals:
        lines.append("- [%s] %s" % (signal["severity"], signal["text"]))
    return "\n".join(lines)


class RiskWatcherAgent(Agent):
    """Watches for churn signals on an account and drafts a risk brief.

    Gathers: account summary, open opportunities, open support cases,
    recent email threads,
    Gong and Zoom transcripts, Zendesk tickets, Linear issues, and
    recent Slack signals. Drafts: a risk brief with the signals ranked
    by severity, the evidence for each with deep links, an overall
    severity, and a recommended next step.

    Composition (v1.1.0): when prior drafts from renewal-desk,
    advocate-finder, and voice-of-customer exist for the account, the
    brief is built from those drafts instead of re-gathering the same
    context; only the fresh risk signals (account record, Zendesk
    tickets, Salesforce cases) are gathered directly. When no prior
    drafts exist, the agent gathers
    directly, its previous behavior.

    Severity is harness-computed, never model prose: an open
    high-priority record is high, a renewal past its close date is
    high. With no signals at all the draft is a clean bill, stated
    as such.
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
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a risk brief for account %r. Cover: the churn "
            "signals in the context ranked by severity (open support "
            "and delivery records, stalled or closing renewals, "
            "escalation language in messages or transcripts), the "
            "evidence for each signal, the overall severity, and a "
            "recommended next step. Do not invent a signal that is "
            "not in the context." % account,
        )
        system = (
            "You are the risk watcher for a customer success team. "
            "You watch for churn before it becomes a renewal problem. "
            "Rank signals by severity, cite the evidence, and do not "
            "invent facts that are not in the context."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + _risk_signals_block(signals)
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
    if not parts:
        return (
            "No open renewal opportunity or open item in the gathered "
            "records; the vulnerability is unknown, not absent."
        )
    return "; ".join(parts) + "."


def _competitive_proof_points(results: dict) -> str:
    """Proof points to use in the defense, from the gathered records
    only: the renewal at stake and the expansion interest. Nothing
    here is a claim about the rival; it is what we have to defend
    with."""
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


# The agents champion-change composes from (v0.18.0): advocate-finder
# first (its drafts name champions), stakeholder-mapper (its map names
# roles and relationships), risk-watcher (its brief flags departure
# risk).
_CHAMPION_CHANGE_SOURCE_AGENTS = (
    "advocate-finder",
    "stakeholder-mapper",
    "risk-watcher",
)


# Phrases in a message or transcript excerpt that say a named person
# has left the account. Substring match on the excerpt, like the
# escalation words: "joined" alone is not a signal (people join
# calls), so every phrase names a move, not an attendance.
_CHAMPION_DEPARTURE_PHRASES = (
    "has left",
    "left the company",
    "left us",
    "left the account",
    "moved to",
    "now at",
    "no longer with",
    "departed",
    "resigned",
    "moved on to",
    "took a role at",
    "started at",
)
# Phrases that say the champion was promoted, not lost: the
# playbook's best version of the problem. They still work there,
# they still believe, and they can introduce the successor.
_CHAMPION_PROMOTED_PHRASES = (
    "promoted to",
    "new role",
    "moved into",
    "stepping into",
    "taking over",
)
# Phrases in a mail message that say it bounced.
_CHAMPION_BOUNCE_PHRASES = (
    "undeliverable",
    "delivery failed",
    "mailbox unavailable",
    "address not found",
    "delivery status notification",
    "returned mail",
    "could not be delivered",
)


def _extract_champion(
    job: dict, composed_text: str, memory_text: str
) -> tuple[str | None, str | None]:
    """The champion this run is about, and where the name came from.

    The job names one first (job["champion"], or "champion <Name>"
    in the job text). Otherwise a prior draft names one: the
    stakeholder-map block writes "- Name: champion", and an
    advocate-finder draft writes "champion: Name". Memory is checked
    the same way, last. Only the colon form is parsed from prose, so
    a sentence like "the champion is solid" never yields a name.
    Returns (name, source) or (None, None): no champion on record
    is a gap, not an error.
    """
    named = str(job.get("champion", "") or "").strip()
    if named:
        return named, "job"
    job_text = str(job.get("text", "") or "")
    match = re.search(
        r"[Cc]hampion:\s+([A-Z][a-z]+(?: [A-Z][a-z]+)+)", job_text
    )
    if match:
        return match.group(1), "job"
    for text, source in (
        (composed_text or "", "prior draft"),
        (memory_text or "", "memory"),
    ):
        found = re.search(
            r"(?:^|\s)-\s+([A-Z][a-z]+(?: [A-Z][a-z]+)+): champion",
            text,
        )
        if found:
            return found.group(1), source
        found = re.search(
            r"[Cc]hampion:\s+([A-Z][a-z]+(?: [A-Z][a-z]+)+)", text
        )
        if found:
            return found.group(1), source
    return None, None


def _extract_successor(
    job: dict, composed_text: str, people: list[dict]
) -> str | None:
    """A successor named for the account: job["successor"] first,
    then a prior draft's "successor: Name". The name must be a person
    on the account (in the merged people list); a name no record
    supports is not a successor, it is a guess."""
    named = str(job.get("successor", "") or "").strip()
    if named:
        return named
    match = re.search(
        r"[Ss]uccessor:\s+([A-Z][a-z]+(?: [A-Z][a-z]+)+)",
        composed_text or "",
    )
    if not match:
        return None
    keys = {person["key"] for person in people}
    if _person_key(match.group(1)) in keys:
        return match.group(1)
    return None


def _champion_appearances(champion_key: str, results: dict) -> list[str]:
    """Where the champion appears in the gathered records: one entry
    per tool that names them, in an author, speaker, participant, or
    excerpt field. Empty means the champion is quiet in every record
    gathered this run."""
    seen: list[str] = []
    checks = (
        ("gmail_search_messages", "matches", ("author", "excerpt", "subject")),
        ("gong_list_calls", "calls", ("participants",)),
        ("gong_search_transcripts", "matches", ("speaker", "excerpt")),
        ("zoom_list_recordings", "recordings", ("host",)),
        ("zoom_search_transcripts", "matches", ("speaker", "excerpt")),
        ("slack_search_messages", "matches", ("author", "excerpt")),
    )
    for tool, items_key, fields in checks:
        result = results.get(tool) or {}
        for item in result.get(items_key, []) or []:
            hit = False
            for field in fields:
                value = item.get(field, "")
                if isinstance(value, list):
                    if any(
                        champion_key in _person_key(part) for part in value
                    ):
                        hit = True
                elif champion_key and champion_key in _person_key(value):
                    hit = True
            if hit:
                seen.append(tool)
                break
    return seen


def _champion_excerpts(
    champion_key: str, champion_email: str, results: dict
) -> list[dict]:
    """Every excerpt that names the champion, with its source tool.
    The excerpt is quoted as evidence; nothing is claimed about a
    person no excerpt names."""
    found: list[dict] = []
    for tool, label in (
        ("gmail_search_messages", "Gmail"),
        ("gong_search_transcripts", "Gong transcript"),
        ("zoom_search_transcripts", "Zoom transcript"),
        ("slack_search_messages", "Slack"),
    ):
        result = results.get(tool) or {}
        for match in result.get("matches", []) or []:
            excerpt = str(match.get("excerpt", "") or "")
            subject = str(match.get("subject", "") or "")
            author = str(match.get("author", "") or "")
            speaker = str(match.get("speaker", "") or "")
            hay = " ".join((excerpt, subject, author, speaker))
            if champion_key and champion_key in _person_key(hay):
                found.append(
                    {"tool": tool, "label": label, "excerpt": excerpt}
                )
                continue
            email = (champion_email or "").lower()
            if email and email in hay.lower():
                found.append(
                    {"tool": tool, "label": label, "excerpt": excerpt}
                )
    return found


def _champion_departure_signals(
    champion: dict, results: dict, known_before: bool
) -> list[dict]:
    """Detect a champion departure from the gathered records.

    Deterministic, harness-computed: the model never decides whether
    someone left. A departure is only claimed when an excerpt names
    the champion and says so (moved on, promoted, or their mail
    bounces). Silence is a medium signal, never a departure: a quiet
    champion may be on leave, not gone. Returns severity-ranked
    {"severity", "kind", "key", "text"} dicts; an empty list means
    no departure evidence in this run's records.
    """
    signals: list[dict] = []
    name = champion["name"]
    key = _person_key(name)
    for item in _champion_excerpts(key, champion.get("email", ""), results):
        lowered = item["excerpt"].lower()
        phrase = next(
            (p for p in _CHAMPION_DEPARTURE_PHRASES if p in lowered),
            None,
        )
        if phrase:
            signals.append(
                {
                    "severity": "high",
                    "kind": "champion-moved-on",
                    "key": "champion-moved-on:" + key,
                    "text": (
                        "Champion moved on: %s (%s, %r): %r"
                        % (
                            name,
                            item["label"],
                            phrase,
                            item["excerpt"][:120],
                        )
                    ),
                }
            )
            continue
        phrase = next(
            (p for p in _CHAMPION_PROMOTED_PHRASES if p in lowered),
            None,
        )
        if phrase:
            signals.append(
                {
                    "severity": "medium",
                    "kind": "champion-promoted",
                    "key": "champion-promoted:" + key,
                    "text": (
                        "Champion promoted, not lost: %s (%s, %r): %r"
                        % (
                            name,
                            item["label"],
                            phrase,
                            item["excerpt"][:120],
                        )
                    ),
                }
            )
    gmail = results.get("gmail_search_messages") or {}
    for match in gmail.get("matches", []) or []:
        excerpt = str(match.get("excerpt", "") or "")
        subject = str(match.get("subject", "") or "")
        hay = (excerpt + " " + subject).lower()
        if not any(p in hay for p in _CHAMPION_BOUNCE_PHRASES):
            continue
        email = (champion.get("email", "") or "").lower()
        if key in _person_key(hay) or (email and email in hay):
            signals.append(
                {
                    "severity": "high",
                    "kind": "champion-bounce",
                    "key": "champion-bounce:" + key,
                    "text": (
                        "Champion email bouncing: %s (Gmail delivery "
                        "failure): %r" % (name, excerpt[:120])
                    ),
                }
            )
            break
    if not _champion_appearances(key, results):
        signals.append(
            {
                "severity": "medium",
                "kind": "champion-quiet",
                "key": "champion-quiet:" + key,
                "text": (
                    "Champion gone quiet: %s appears in none of the "
                    "gathered records (Gmail, Gong calls and "
                    "transcripts, Zoom recordings and transcripts, "
                    "Slack). %s"
                    % (
                        name,
                        "Prior drafts confirm they were active before."
                        if known_before
                        else "No prior record confirms they were "
                        "active before, so quiet is unconfirmed "
                        "against history.",
                    )
                ),
            }
        )
    seen: set[str] = set()
    unique: list[dict] = []
    for signal in signals:
        if signal["key"] not in seen:
            seen.add(signal["key"])
            unique.append(signal)
    unique.sort(key=lambda s: _SEVERITY_RANK[s["severity"]])
    return unique


def _champion_people(results: dict) -> list[dict]:
    """The people on the account for the rebuild: the stakeholder
    map's merged people, plus the contacts on the account record
    (CSV contacts, Salesforce contacts), which the map's gather does
    not read. A contact joins the map with the account record as its
    source and evidence; nobody is invented."""
    people = _stakeholder_people(results)
    by_key = {person["key"]: person for person in people}
    account = results.get("salesforce_get_account") or {}
    for contact in account.get("contacts", []) or []:
        if not isinstance(contact, dict):
            continue
        raw = str(contact.get("name", "") or "").strip()
        key = _person_key(raw)
        if not key:
            continue
        person = by_key.get(key)
        if person is None:
            person = {
                "key": key,
                "name": raw,
                "title": "",
                "side": "customer",
                "sources": set(),
                "evidence": [],
                "champion_status": None,
                "champion_since": None,
                "moved_to": None,
                "promoted": None,
                "is_owner": False,
                "handle_only": _is_handle(raw),
                "emails": set(),
            }
            people.append(person)
            by_key[key] = person
        email = str(contact.get("email", "") or "").strip()
        if email:
            person["emails"].add(email)
        person["sources"].add("salesforce_get_account")
        person["evidence"].append("Contact on the account record")
    return sorted(people, key=lambda p: p["name"].lower())


def _title_in_result(person_key: str, value) -> str | None:
    """A title for the person in one tool result: a dict whose name
    matches and carries a non-empty title. Recursive, into lists and
    nested dicts; the account record, contacts, and participants all
    live at those depths."""
    if isinstance(value, dict):
        name = value.get("name")
        if name and _person_key(name) == person_key:
            title = str(value.get("title", "") or "").strip()
            if title:
                return title
        for item in value.values():
            found = _title_in_result(person_key, item)
            if found:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _title_in_result(person_key, item)
            if found:
                return found
    return None


def _title_with_link(
    person_key: str, results: dict
) -> tuple[str | None, str | None]:
    """A person's title from a gathered record, and the tool that
    named it, but only when that tool's result also produced a deep
    link. A title without a source link is dropped, not drafted:
    the caller lists the person as title unknown. No connector
    returns titles today, so this usually returns nothing; the rule
    is the point, and the tests prove both branches with fake
    records."""
    for tool, result in results.items():
        title = _title_in_result(person_key, result)
        if not title:
            continue
        if extract_links(tool, result):
            return title, tool
    return None, None


def _successor_candidates(
    people: list[dict],
    relationships: list[dict],
    champion_key: str,
    results: dict,
) -> list[dict]:
    """Rank the successor candidates: customer stakeholders besides
    the champion, scored on observable signals. +2 appears with the
    champion in a call or thread (they already know each other); +1
    a senior title with a source link; +1 a budget title with a
    source link; +1 present across three or more sources. A title
    without a link earns nothing and is listed as unknown. Ranked by
    score, then name; the ranking is harness-computed, never model
    prose."""
    connected: set[str] = set()
    for rel in relationships:
        if rel["a"] == champion_key:
            connected.add(rel["b"])
        elif rel["b"] == champion_key:
            connected.add(rel["a"])
    candidates: list[dict] = []
    for person in people:
        if person["side"] != "customer":
            continue
        if person["key"] == champion_key:
            continue
        score = 0
        if person["key"] in connected:
            score += 2
        title, title_source = _title_with_link(person["key"], results)
        title_words = (title or "").lower().split()
        title_text = " ".join(title_words)
        if any(word in title_words for word in _SENIOR_TITLE_WORDS) or (
            "vice president" in title_text
        ):
            score += 1
        if title and any(
            word in title.lower() for word in _BUDGET_TITLE_WORDS
        ):
            score += 1
        if len(person["sources"]) >= 3:
            score += 1
        candidates.append(
            {
                "key": person["key"],
                "name": person["name"],
                "title": title,
                "title_source": title_source,
                "score": score,
                "evidence": list(person["evidence"]),
                "sources": sorted(person["sources"]),
            }
        )
    candidates.sort(key=lambda c: (-c["score"], c["name"].lower()))
    return candidates


def _champion_impact(
    champion_key: str, people: list[dict], relationships: list[dict]
) -> dict:
    """What the departure does to the relationship map: who the
    champion connected to, whether a second relationship survives,
    whether an economic buyer remains. All from the map's own
    evidence."""
    by_key = {person["key"]: person for person in people}
    others = [
        person
        for person in people
        if person["side"] == "customer" and person["key"] != champion_key
    ]
    connections: list[dict] = []
    for rel in relationships:
        other_key = None
        if rel["a"] == champion_key:
            other_key = rel["b"]
        elif rel["b"] == champion_key:
            other_key = rel["a"]
        if other_key is None:
            continue
        other = by_key.get(other_key)
        connections.append(
            {
                "name": other["name"] if other else other_key,
                "calls": rel["calls"],
                "threads": rel["threads"],
            }
        )
    return {
        "connections": connections,
        "other_customers": len(others),
        "second_relationship": len(others) > 0,
        "buyer_remains": any(_is_budget_holder(p) for p in others),
    }


def _champion_change_block(
    account: str,
    champion: dict | None,
    champion_source: str | None,
    signals: list[dict],
    impact: dict | None,
    candidates: list[dict],
    criteria: dict,
    renewal_days: int | None,
    gaps: list[str],
) -> str:
    """The harness-written champion-change brief appended to the
    draft: the departure finding with its evidence, the
    relationship-map impact, the ranked rebuild plan, and the
    Champion Departure playbook's 7/14/30 spine. No departure means
    a clean bill, stated as such; no champion on record means the
    gap is named, never papered over."""
    lines = ["Champion change (harness-computed):", ""]
    if champion is None:
        lines.append(
            "Champion: none on record for %s. No champion is named "
            "in the job, in any prior draft, or in the account "
            "record. The rebuild starts with finding one." % account
        )
    else:
        lines.append(
            "Champion: %s (source: %s)."
            % (champion["name"], champion_source or "unknown")
        )
    severity = signals[0]["severity"] if signals else "none"
    lines.append("Overall severity: %s." % severity)
    lines.append("")
    lines.append("Departure:")
    if champion is None:
        lines.append(
            "- No departure to check: no champion on record."
        )
    elif not signals:
        lines.append(
            "- No departure detected in the gathered records for "
            "%s. The champion is still in place as far as the "
            "records show." % champion["name"]
        )
    else:
        for signal in signals:
            lines.append(
                "- [%s] %s" % (signal["severity"], signal["text"])
            )
    lines.append("")
    lines.append("Relationship-map impact:")
    if impact is None:
        lines.append(
            "- No champion, so no departure impact to measure."
        )
    else:
        if impact["connections"]:
            for conn in impact["connections"]:
                parts = []
                if conn["calls"]:
                    parts.append(
                        "%d call%s"
                        % (
                            conn["calls"],
                            "s" if conn["calls"] != 1 else "",
                        )
                    )
                if conn["threads"]:
                    parts.append(
                        "%d email thread%s"
                        % (
                            conn["threads"],
                            "s" if conn["threads"] != 1 else "",
                        )
                    )
                lines.append(
                    "- %s connected to %s (%s)."
                    % (
                        champion["name"],
                        conn["name"],
                        " and in ".join(parts),
                    )
                )
        else:
            lines.append(
                "- No recorded connection between %s and any other "
                "stakeholder: no shared call or thread in the "
                "gathered records." % champion["name"]
            )
        lines.append(
            "- Second relationship survives: %s (%d other customer "
            "stakeholder(s) on the map)."
            % (
                "yes" if impact["second_relationship"] else "no",
                impact["other_customers"],
            )
        )
        lines.append(
            "- Economic buyer remains: %s."
            % ("yes" if impact["buyer_remains"] else "no")
        )
    lines.append("")
    if champion is None:
        lines.append("Potential champions (ranked):")
    else:
        lines.append("Rebuild plan (ranked successors):")
    shown = candidates[:3]
    if not shown:
        lines.append(
            "- No successor candidate in the gathered records."
        )
    for candidate in shown:
        title = candidate["title"]
        if title:
            title_text = "%s (source: %s)" % (
                title,
                candidate["title_source"],
            )
        else:
            title_text = "title unknown"
        lines.append(
            "- %s (score %d): %s. Evidence: %s."
            % (
                candidate["name"],
                candidate["score"],
                title_text,
                "; ".join(candidate["evidence"])
                or "no evidence recorded",
            )
        )
    lines.append("")
    if champion is None:
        verdict = "No champion on record: find one first."
    elif not signals:
        verdict = (
            "No departure detected: the champion is still in place."
        )
    else:
        top = candidates[0]["score"] if candidates else 0
        second = impact["second_relationship"] if impact else False
        if (
            not candidates
            and not second
            and renewal_days is not None
            and renewal_days <= 30
        ):
            verdict = "Too late to rebuild, protect the exit."
        elif top < 2 or not second:
            verdict = "Rebuildable, but thin."
        else:
            verdict = "Rebuildable, move now."
    lines.append("Rebuild verdict: %s" % verdict)
    lines.append("")
    lines.append("Champion Departure playbook checks:")
    lines.append(
        "- Second relationship in the account: %s."
        % criteria["second"]
    )
    lines.append(
        "- Successor known: %s."
        % (criteria["successor"] or "no")
    )
    lines.append(
        "- Successor's priorities known: %s."
        % criteria["successor_priorities"]
    )
    lines.append(
        "- Relationships span more than one department: %s."
        % criteria["multi_dept"]
    )
    lines.append(
        "- Value documented: %s." % criteria["value_doc"]
    )
    lines.append(
        "- Renewal date and decision process known: %s."
        % criteria["renewal_known"]
    )
    lines.append("")
    lines.append("The 7/14/30 spine:")
    lines.append(
        "- Days 1-7: map the account and reach the successor. "
        "List every person the champion touched, find the "
        "successor, and get one meeting. Say it once, clearly: "
        "the champion left, you are here, and the value is real."
    )
    lines.append(
        "- Days 8-14: re-prove the value in the successor's terms. "
        "Bring the documented results, in their numbers, and ask "
        "what they need to see to believe it."
    )
    lines.append(
        "- Days 15-30: the new champion's first win, then the "
        "renewal conversation. Get the successor one visible win "
        "with the product, then talk about the renewal. Never "
        "before."
    )
    if gaps:
        lines.append("")
        lines.append("Gaps:")
        for gap in gaps:
            lines.append("- %s" % gap)
    return "\n".join(lines)


class ChampionChangeAgent(Agent):
    """Catches a champion departure and rebuilds the relationship map.

    Gathers: account summary and contacts, recent email threads,
    Gong calls and transcripts, Zoom recordings and transcripts, and
    recent Slack signals. Drafts: the departed champion with the
    evidence, the relationship-map impact, ranked successor
    candidates, and the rebuild plan from the Champion Departure
    playbook's 7/14/30 spine.

    Composition (v1.1.0): when prior drafts from advocate-finder,
    stakeholder-mapper, and risk-watcher exist for the account, the
    brief is built from those drafts instead of re-gathering the same
    context; only the fresh departure signals are gathered directly.
    When no prior drafts exist, the agent gathers directly.

    Detection is harness-computed, never model prose: a departure is
    claimed only when an excerpt names the champion and says they
    left, were promoted, or their mail bounces; silence is a medium
    signal, never a departure. A successor's title is claimed only
    when a gathered record names it with a source link; a title
    without one is dropped. With no champion on record the draft
    names the gap, never a departure.
    """

    name = "champion-change"
    display_name = "Champion Change"
    description = (
        "Catches when a key contact leaves and rebuilds the "
        "relationship map: the departed champion, the evidence, "
        "who could succeed them, and the rebuild plan."
    )
    keywords = [
        "champion change",
        "champion-change",
        "champion left",
        "champion departed",
        "champion moved",
        "champion gone",
        "champion has left",
        "left the account",
        "successor",
        "rebuild the relationship",
        "relationship rebuild",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Names the champion or the gap",
            keywords=["champion:"],
        ),
        ProofItem(
            "States the departure finding",
            keywords=["departure:"],
        ),
        ProofItem(
            "Names successor candidates or the gap",
            keywords=["successor"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _CHAMPION_CHANGE_SOURCE_AGENTS
        )
        if composed_text:
            # Composed: build from the prior drafts. Only the fresh
            # departure signals are gathered directly; the source
            # drafts carry the transcripts and the broader account
            # context, and their deep links carry through to the
            # composed draft.
            calls = [
                ("salesforce_get_account", {"account": account}),
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
        people = _champion_people(results)
        relationships = _stakeholder_relationships(results)
        champion_name, champion_source = _extract_champion(
            job, composed_text, memory_text
        )
        gaps: list[str] = []
        champion: dict | None = None
        champion_key = ""
        if champion_name:
            champion_key = _person_key(champion_name)
            named = next(
                (p for p in people if p["key"] == champion_key), None
            )
            if named is not None and named["side"] == "internal":
                gaps.append(
                    "The named champion %s is on our side (account "
                    "owner or our team), not a customer champion."
                    % champion_name
                )
            else:
                email = ""
                if named is not None and named["emails"]:
                    email = sorted(named["emails"])[0]
                champion = {"name": champion_name, "email": email}
        known_before = champion_source in ("prior draft", "memory")
        signals = (
            _champion_departure_signals(champion, results, known_before)
            if champion is not None
            else []
        )
        impact = (
            _champion_impact(champion_key, people, relationships)
            if champion is not None
            else None
        )
        candidates = _successor_candidates(
            people, relationships, champion_key, results
        )
        account_record = results.get("salesforce_get_account") or {}
        renewal = str(account_record.get("renewal_date", "") or "")
        renewal_days: int | None = None
        try:
            from datetime import date as _date

            year, month, day = (int(part) for part in renewal.split("-"))
            renewal_days = (_date(year, month, day) - _date.today()).days
        except (ValueError, TypeError):
            renewal_days = None
        criteria = {
            "second": (
                "yes"
                if impact is not None and impact["second_relationship"]
                else "no"
            ),
            "successor": _extract_successor(
                job, composed_text, people
            ),
            "successor_priorities": "unknown",
            "multi_dept": "unknown",
            "value_doc": (
                "yes"
                if _load_prior_items(job, account, ("value-finder",))
                else "unknown"
            ),
            "renewal_known": "yes" if renewal else "no",
        }
        if champion is not None and not signals:
            gaps.append(
                "No departure evidence for %s in this run's records: "
                "no excerpt says they left, were promoted, or "
                "bounced." % champion["name"]
            )
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a champion-change brief for account %r. Cover: "
            "the champion and whether they have left, the evidence "
            "for the departure, the impact on the relationship map, "
            "who could succeed them, and the rebuild plan. Use only "
            "the context below. Never invent a departure, a person, "
            "or a title the context does not support; say unknown "
            "where the records do not say." % account,
        )
        system = (
            "You are the champion-change agent for a customer "
            "success team. You catch a champion leaving before the "
            "renewal feels it, and you rebuild the relationship map "
            "around a successor. Name the people, cite the evidence, "
            "and do not invent facts that are not in the context. A "
            "departure the records do not show is not a departure."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + _champion_change_block(
            account,
            champion,
            champion_source,
            signals,
            impact,
            candidates,
            criteria,
            renewal_days,
            gaps,
        )
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


# The agents handoff composes from (v0.18.0): briefer first (its
# snapshot is the account state), renewal-desk (the renewal picture),
# risk-watcher (the flagged risks), stakeholder-mapper (the
# relationship map), champion-change (the champion picture).
_HANDOFF_SOURCE_AGENTS = (
    "briefer",
    "renewal-desk",
    "risk-watcher",
    "stakeholder-mapper",
    "champion-change",
)


def _extract_previous_owner(
    job: dict, composed_text: str, memory_text: str
) -> tuple[str | None, str | None]:
    """The previous owner this handoff is from, and where the name came
    from.

    The job names one first (job["previous_owner"], job["from_owner"],
    or job["previous_csm"]). Otherwise a prior draft or a memory record
    names one in colon form ("previous owner: X", "former owner: X",
    "previous csm: X", "handed off by: X"). Only the colon form is
    parsed from prose, so a sentence like "the previous owner was
    great" never yields a name. Returns (name, source) or (None,
    None): no previous owner on record is a gap, not an error.
    """
    for key in ("previous_owner", "from_owner", "previous_csm"):
        name = str(job.get(key, "") or "").strip()
        if name:
            return name, "job"
    text = str(job.get("text", "") or "")
    for label in (
        "previous owner",
        "former owner",
        "previous csm",
        "handed off by",
    ):
        first = "[%s%s]" % (label[0].upper(), label[0].lower())
        pattern = re.compile(
            first
            + re.escape(label[1:])
            + r"\s*:\s*([A-Z][A-Za-z'’.-]+(?: [A-Z][A-Za-z'’.-]+){0,2})"
        )
        for source_text, source in (
            (text, "job"),
            (composed_text, "prior draft"),
            (memory_text, "memory"),
        ):
            match = pattern.search(source_text)
            if match:
                return match.group(1).strip(), source
    return None, None


def _pending_items(job: dict, account: str) -> list:
    """Drafts still pending in the approval queue for this account.
    Empty when the job names no approvals file or the queue cannot be
    read: a missing queue reads as nothing in flight, not an error."""
    path = job.get("approvals_file")
    if not path:
        return []
    try:
        queue = ApprovalQueue(path)
    except Exception:
        return []
    return [
        item
        for item in queue.list_pending()
        if _extract_account(item.job) == account
    ]


def _open_escalations(job: dict, account: str) -> list:
    """Open or escalated escalation records for this account. Empty
    when the job names no escalations file or the store cannot be
    read: a missing store reads as no open escalations, not an error."""
    path = job.get("escalations_file")
    try:
        store = EscalationStore(path) if path else EscalationStore(None)
        records = store.for_account(account)
    except Exception:
        return []
    return [r for r in records if r.status in ("open", "escalated")]


def _account_schedules(job: dict, account: str) -> list:
    """Scheduled jobs for this account. Empty when the job names no
    schedules file or the store cannot be read: a missing store reads
    as nothing scheduled, not an error."""
    path = job.get("schedules_file")
    if not path:
        return []
    try:
        from .scheduler import ScheduleStore

        store = ScheduleStore(path)
        return [
            schedule
            for schedule in store.list()
            if schedule.account == account
        ]
    except Exception:
        return []


def _handoff_open_items(
    signals: list[dict], escalations: list, pending: list
) -> list[dict]:
    """The open items for the handoff, ranked by urgency.

    Risk signals first (their harness-computed severity), then open
    escalations (escalated is high, open is medium), then drafts still
    pending a decision in the queue (low: they need a human, they are
    not account risk). Sorted by severity, then by label so the order
    is deterministic. A signal and an escalation about the same
    subject stay separate items: one is the risk, the other is the
    escalation already raised about it.
    """
    items: list[dict] = []
    for signal in signals:
        items.append(
            {
                "urgency": signal.get("severity", "low"),
                "label": signal.get("text", ""),
                "source": "risk signals",
            }
        )
    for record in escalations:
        items.append(
            {
                "urgency": "high" if record.status == "escalated" else "medium",
                "label": "Escalation %s (%s): %s"
                % (record.id, record.signal_key, record.summary),
                "source": "escalation store",
            }
        )
    for item in pending:
        items.append(
            {
                "urgency": "low",
                "label": "%s draft awaiting approval (submitted %s)"
                % (item.agent, item.created_at),
                "source": "approval queue",
            }
        )
    items.sort(
        key=lambda i: (_SEVERITY_RANK.get(i["urgency"], 2), i["label"])
    )
    return items


def _handoff_first_week(
    account_record: dict,
    renewal_days: int | None,
    open_items: list[dict],
    escalations: list,
    pending: list,
    champion: dict | None,
    champion_source: str | None,
    previous_owner: str | None,
    had_history: bool,
) -> list[str]:
    """The first-week actions for the new CSM, harness-written from
    the computed state. Each action names its source. A missing
    previous owner or missing history produces an action to gather
    it, not a blank week."""
    actions: list[str] = []
    renewal = str(account_record.get("renewal_date", "") or "")
    if renewal_days is not None and renewal_days <= 30:
        actions.append(
            "Confirm the renewal is still alive, who owns the close, "
            "and the real close date (source: renewal opportunity, "
            "closes %s)." % renewal
        )
    high = [i for i in open_items if i["urgency"] == "high"]
    if high:
        actions.append(
            "Own the %d high-urgency open item(s) first: %s "
            "(source: %s)."
            % (
                len(high),
                high[0]["label"],
                high[0]["source"],
            )
        )
    if escalations:
        actions.append(
            "Read the %d open escalation(s) and confirm the owner "
            "and next step for each (source: escalation store)."
            % len(escalations)
        )
    if pending:
        actions.append(
            "Decide the %d draft(s) awaiting approval in the queue "
            "(source: approval queue)." % len(pending)
        )
    if champion is not None:
        actions.append(
            "Meet %s in week one and confirm they still back the "
            "renewal (source: %s)."
            % (champion["name"], champion_source or "unknown")
        )
    else:
        actions.append(
            "Find the champion: no champion is on record for this "
            "account (source: account record and prior drafts)."
        )
    if previous_owner is None or not had_history:
        actions.append(
            "Introduce yourself to the account: no previous owner or "
            "history is on record, so gather the context the records "
            "do not hold (source: none on file)."
        )
    owner = str(account_record.get("owner", "") or "").strip()
    if owner:
        actions.append(
            "Book the intro call with the account owner %s "
            "(source: account record)." % owner
        )
    return actions


def _handoff_block(
    account: str,
    account_record: dict,
    renewal_days: int | None,
    signals: list[dict],
    open_items: list[dict],
    champion: dict | None,
    champion_source: str | None,
    people: list[dict],
    pending: list,
    escalations: list,
    schedules: list,
    previous_owner: str | None,
    previous_owner_source: str | None,
    actions: list[str],
    gaps: list[str],
) -> str:
    """The harness-written handoff brief appended to the draft: the
    account's current state, open items ranked by urgency, risks
    flagged with evidence, the champion and stakeholder picture, what
    is already in flight, and the first-week actions. Nothing is
    invented: every claim carries a source, and a missing item is
    named as a gap."""
    lines = ["Handoff brief (harness-computed):", ""]
    lines.append("Account state:")
    health = str(account_record.get("health", "") or "").strip()
    arr = account_record.get("arr")
    owner = str(account_record.get("owner", "") or "").strip()
    renewal = str(account_record.get("renewal_date", "") or "").strip()
    state_parts = []
    if health:
        state_parts.append("health %s" % health)
    if arr is not None:
        state_parts.append("ARR %s" % arr)
    if owner:
        state_parts.append("owner %s" % owner)
    if renewal:
        if renewal_days is not None:
            state_parts.append(
                "renewal %s (%d days)" % (renewal, renewal_days)
            )
        else:
            state_parts.append("renewal %s" % renewal)
    if state_parts:
        lines.append(
            "- %s: %s (source: account record)."
            % (account, ", ".join(state_parts))
        )
    else:
        lines.append(
            "- %s: no account record on file (source: none)."
            % account
        )
    lines.append("")
    lines.append("Open items (ranked by urgency):")
    if not open_items:
        lines.append("- No open items in the gathered records.")
    for item in open_items:
        lines.append(
            "- [%s] %s (source: %s)."
            % (item["urgency"], item["label"], item["source"])
        )
    lines.append("")
    lines.append("Risks:")
    flagged = [s for s in signals if s.get("severity") in ("high", "medium")]
    if not flagged:
        lines.append("- No high or medium risk signals in this run.")
    for signal in flagged:
        lines.append(
            "- [%s] %s" % (signal.get("severity"), signal.get("text"))
        )
    lines.append("")
    lines.append("Champion and stakeholders:")
    if champion is None:
        lines.append(
            "- Champion: none on record for %s. The new CSM starts "
            "by finding one." % account
        )
    else:
        lines.append(
            "- Champion: %s (source: %s)."
            % (champion["name"], champion_source or "unknown")
        )
    customers = [p for p in people if p.get("side") == "customer"]
    if customers:
        lines.append(
            "- Stakeholders on record: %s."
            % ", ".join(p["name"] for p in customers)
        )
    else:
        lines.append("- No stakeholders on record in the gathered data.")
    lines.append("")
    lines.append("Already in flight:")
    if pending:
        lines.append("- Pending drafts in the approval queue:")
        for item in pending:
            lines.append(
                "  - %s draft, submitted %s (source: approval queue)."
                % (item.agent, item.created_at)
            )
    else:
        lines.append("- No drafts pending in the approval queue.")
    if schedules:
        lines.append("- Scheduled jobs for this account:")
        for schedule in schedules:
            lines.append("  - %s (source: schedule store)." % schedule.describe())
    else:
        lines.append("- No scheduled jobs for this account.")
    if escalations:
        lines.append("- Open escalations:")
        for record in escalations:
            lines.append(
                "  - %s (%s, %s) (source: escalation store)."
                % (record.id, record.signal_key, record.status)
            )
    else:
        lines.append("- No open escalations.")
    lines.append("")
    lines.append("Previous owner:")
    if previous_owner is None:
        lines.append(
            "- No previous owner named in the job, in any prior draft, "
            "or in memory."
        )
    else:
        lines.append(
            "- %s (source: %s)."
            % (previous_owner, previous_owner_source or "unknown")
        )
    lines.append("")
    lines.append("First week:")
    for index, action in enumerate(actions, start=1):
        lines.append("%d. %s" % (index, action))
    if gaps:
        lines.append("")
        lines.append("Gaps:")
        for gap in gaps:
            lines.append("- %s" % gap)
    return "\n".join(lines)


class HandoffAgent(Agent):
    """Handles a sales-to-CS transition or an inherited account.

    Gathers: account record, open opportunities, open cases, recent
    email threads, Gong and Zoom transcripts, Zendesk tickets, Linear
    issues, and recent Slack signals. Drafts: the handoff brief — the
    account's current state, open items ranked by urgency, risks
    flagged with evidence, the champion and stakeholder picture, what
    is already in flight, and the first-week actions for the new CSM.

    Composition (v1.1.0): when prior drafts from briefer,
    renewal-desk, risk-watcher, stakeholder-mapper, and
    champion-change exist for the account, the brief is built from
    those drafts instead of re-gathering the same context; only the
    fresh account record, opportunities, cases, and tickets are
    gathered directly. When no prior drafts exist, the agent gathers
    directly.

    Open items, risks, and first-week actions are harness-computed,
    never model prose: urgency comes from the risk signals, the
    escalation store, and the approval queue, and every claim carries
    its source. A missing previous owner or missing history is a
    named gap, never an error, and an inherited account with no prior
    history gets an empty-state brief, stated as such.
    """

    name = "handoff"
    display_name = "Handoff"
    description = (
        "Drafts the handoff brief for an inherited account: current "
        "state, open items ranked by urgency, risks, what is already "
        "in flight, and the first-week actions for the new CSM."
    )
    keywords = [
        "handoff",
        "hand off",
        "handover",
        "inherited account",
        "inherit",
        "take over the account",
        "taking over",
        "new csm",
        "sales to cs",
        "transition the account",
        "account transition",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "States the account state",
            keywords=["account state:"],
        ),
        ProofItem(
            "Ranks open items",
            keywords=["open items (ranked by urgency):"],
        ),
        ProofItem(
            "Names the champion picture",
            keywords=["champion:"],
        ),
        ProofItem(
            "Names what is already in flight",
            keywords=["already in flight:"],
        ),
        ProofItem(
            "Lists first-week actions",
            keywords=["first week:"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _HANDOFF_SOURCE_AGENTS
        )
        if composed_text:
            # Composed: build from the prior drafts. Only the fresh
            # account record, opportunities, cases, and tickets are
            # gathered directly; the source drafts carry the
            # transcripts, Slack, and broader account context, and
            # their deep links carry through to the composed draft.
            calls = [
                ("salesforce_get_account", {"account": account}),
                ("salesforce_list_opportunities", {"account": account}),
                ("salesforce_list_cases", {"account": account}),
                ("zendesk_list_tickets", {"organization": account}),
                ("gmail_search_messages", {"query": account, "limit": 5}),
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
        account_record = results.get("salesforce_get_account") or {}
        signals = _risk_signals(results, account)
        pending = _pending_items(job, account)
        escalations = _open_escalations(job, account)
        schedules = _account_schedules(job, account)
        open_items = _handoff_open_items(signals, escalations, pending)
        people = _champion_people(results)
        champion_name, champion_source = _extract_champion(
            job, composed_text, memory_text
        )
        gaps: list[str] = []
        champion: dict | None = None
        if champion_name:
            named = next(
                (
                    p
                    for p in people
                    if p["key"] == _person_key(champion_name)
                ),
                None,
            )
            if named is not None and named["side"] == "internal":
                gaps.append(
                    "The named champion %s is on our side (account "
                    "owner or our team), not a customer champion."
                    % champion_name
                )
            else:
                email = ""
                if named is not None and named["emails"]:
                    email = sorted(named["emails"])[0]
                champion = {"name": champion_name, "email": email}
        previous_owner, previous_owner_source = _extract_previous_owner(
            job, composed_text, memory_text
        )
        had_history = bool(composed_text) or bool(memory_text)
        if previous_owner is None:
            gaps.append(
                "No previous owner named in the job, in any prior "
                "draft, or in memory: the new CSM starts without a "
                "named handover contact."
            )
        if not had_history:
            gaps.append(
                "No prior history for %s: no prior drafts and no "
                "memory records. The brief is built from the gathered "
                "records only." % account
            )
        if champion is None:
            gaps.append(
                "No champion on record for %s: the new CSM starts by "
                "finding one." % account
            )
        renewal = str(account_record.get("renewal_date", "") or "")
        renewal_days: int | None = None
        try:
            from datetime import date as _date

            year, month, day = (int(part) for part in renewal.split("-"))
            renewal_days = (_date(year, month, day) - _date.today()).days
        except (ValueError, TypeError):
            renewal_days = None
        if not renewal:
            gaps.append(
                "No renewal date on the account record: the renewal "
                "picture is unknown."
            )
        actions = _handoff_first_week(
            account_record,
            renewal_days,
            open_items,
            escalations,
            pending,
            champion,
            champion_source,
            previous_owner,
            had_history,
        )
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a handoff brief for account %r for the CSM "
            "inheriting it. Cover: the account's current state, the "
            "open items ranked by urgency, the risks with their "
            "evidence, the champion and stakeholder picture, what is "
            "already in flight, and the first-week actions. Use only "
            "the context below. Never invent a previous owner, a "
            "champion, a risk, or a date the context does not "
            "support; say unknown where the records do not say."
            % account,
        )
        system = (
            "You are the handoff agent for a customer success team. "
            "You hand an account to its new CSM with everything they "
            "need in week one: the state, the open items, the risks, "
            "and what is already moving. Name the people, cite the "
            "evidence, and do not invent facts that are not in the "
            "context. A gap the records do not fill is a gap, not a "
            "guess."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + _handoff_block(
            account,
            account_record,
            renewal_days,
            signals,
            open_items,
            champion,
            champion_source,
            people,
            pending,
            escalations,
            schedules,
            previous_owner,
            previous_owner_source,
            actions,
            gaps,
        )
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


# The two agents whose drafts adoption-gaps composes from:
# expansion-spotter (it already names usage gaps as expansion
# openings) and value-finder (it quantifies unrealized gaps).
_ADOPTION_GAPS_SOURCE_AGENTS = ("expansion-spotter", "value-finder")


def _parse_usage_number(value) -> int | None:
    """Parse a usage number leniently: ints, floats, and strings with
    commas or a dollar sign. Anything else is None, never a crash."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        try:
            return int(float(cleaned))
        except ValueError:
            return None
    return None


def _adoption_usage(job: dict, results: dict) -> tuple[dict, list[str]]:
    """Normalize the usage data for an account.

    The job's `usage` block is the primary source (the CSM pastes it
    from a last-login or usage export): seats purchased and active,
    entitled vs used features, teams with their onboarded state. The
    account record supplies the observables: employees, named
    contacts, last touch. Anything not supplied is a named gap in the
    returned `missing` list, never a guess and never a crash: a
    malformed usage block is a gap, not an error.
    """
    usage: dict = {
        "seats_purchased": None,
        "seats_active": None,
        "features": [],
        "teams": [],
    }
    missing: list[str] = []
    raw = job.get("usage")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        missing.append(
            "Usage data: the job's usage block is not a mapping, so "
            "no usage figures could be read from it."
        )
        raw = {}
    usage["seats_purchased"] = _parse_usage_number(raw.get("seats_purchased"))
    usage["seats_active"] = _parse_usage_number(raw.get("seats_active"))
    features = raw.get("features") or []
    if isinstance(features, list):
        for entry in features:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "") or "").strip()
            if not name:
                continue
            usage["features"].append(
                {
                    "name": name,
                    "entitled": bool(entry.get("entitled", True)),
                    "used": bool(entry.get("used", False)),
                    "value_at_stake": _parse_usage_number(
                        entry.get("value_at_stake")
                    ),
                }
            )
    teams = raw.get("teams") or []
    if isinstance(teams, list):
        for entry in teams:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "") or "").strip()
            if not name:
                continue
            usage["teams"].append(
                {
                    "name": name,
                    "onboarded": bool(entry.get("onboarded", False)),
                    "headcount": _parse_usage_number(entry.get("headcount")),
                }
            )
    if usage["seats_purchased"] is None:
        missing.append(
            "Seat counts: no seats purchased on record. Supply seats "
            "purchased and seats active in the job usage block."
        )
    elif usage["seats_active"] is None:
        missing.append(
            "Seats active: seats purchased is known but no active-seat "
            "count. Supply last-login or active-user data."
        )
    if not usage["features"]:
        missing.append(
            "Feature usage: no feature list on record. Supply entitled "
            "vs used features in the job usage block."
        )
    if not usage["teams"]:
        missing.append(
            "Team onboarding: no team list on record. Supply the teams "
            "and their onboarded state in the job usage block."
        )
    return usage, missing


def _adoption_gaps(usage: dict, account_record: dict) -> list[dict]:
    """Compute the adoption gaps from normalized usage and the account
    record. Every gap carries its evidence and its source; a gap
    without evidence is dropped, never drafted. Value at stake is a
    dollar figure only when the inputs support one, otherwise None
    (unknown, stated as such in the block)."""
    gaps: list[dict] = []
    arr = _parse_usage_number(account_record.get("arr"))
    employees = _parse_usage_number(account_record.get("employees"))
    contacts = account_record.get("contacts") or []
    contact_count = len(
        [
            contact
            for contact in contacts
            if isinstance(contact, dict)
            and str(contact.get("name", "") or "").strip()
        ]
    )
    seats_purchased = usage.get("seats_purchased")
    seats_active = usage.get("seats_active")
    if (
        seats_purchased is not None
        and seats_active is not None
        and seats_active < seats_purchased
    ):
        unused = seats_purchased - seats_active
        per_seat = None
        if arr is not None and seats_purchased:
            per_seat = arr / seats_purchased
        value = round(unused * per_seat) if per_seat is not None else None
        gaps.append(
            {
                "kind": "seats",
                "item": "%d unused seats" % unused,
                "evidence": "%d of %d seats active (last-login data)"
                % (seats_active, seats_purchased),
                "source": "job usage",
                "value_at_stake": value,
                "next_step": (
                    "Right-size or reassign: confirm with the champion "
                    "which seats are still needed before renewal."
                ),
            }
        )
    for feature in usage.get("features", []):
        if feature.get("entitled") and not feature.get("used"):
            gaps.append(
                {
                    "kind": "features",
                    "item": feature["name"],
                    "evidence": "Entitled but unused (feature usage)",
                    "source": "job usage",
                    "value_at_stake": feature.get("value_at_stake"),
                    "next_step": (
                        "Enablement: schedule a working session on %s "
                        "with the owning team." % feature["name"]
                    ),
                }
            )
    for team in usage.get("teams", []):
        if not team.get("onboarded"):
            headcount = team.get("headcount")
            value = None
            if headcount is not None and arr is not None and employees:
                value = round(headcount * arr / employees)
            evidence = "Not onboarded"
            if headcount is not None:
                evidence += "; %d people" % headcount
            gaps.append(
                {
                    "kind": "teams",
                    "item": team["name"],
                    "evidence": evidence,
                    "source": "job usage",
                    "value_at_stake": value,
                    "next_step": (
                        "Onboard %s: champion intro to the team lead, "
                        "then a first-value session." % team["name"]
                    ),
                }
            )
    if employees and contact_count and contact_count <= max(2, employees // 50):
        gaps.append(
            {
                "kind": "teams",
                "item": "Org coverage",
                "evidence": (
                    "%d named contacts for %d employees: most of the "
                    "org is not in the system" % (contact_count, employees)
                ),
                "source": "salesforce_get_account",
                "value_at_stake": None,
                "next_step": (
                    "Map the org: confirm with the champion which teams "
                    "use the product and who leads them."
                ),
            }
        )
    return gaps


_ADOPTION_GAP_KIND_ORDER = {"seats": 0, "features": 1, "teams": 2}


def _rank_adoption_gaps(gaps: list[dict]) -> list[dict]:
    """Rank gaps by value at stake (known dollars first, highest
    first), then kind order (seats, features, teams), then item name.
    Harness-computed, never model prose. Unknown value sorts after
    known value within its kind."""
    return sorted(
        gaps,
        key=lambda gap: (
            0 if gap.get("value_at_stake") is not None else 1,
            -(gap.get("value_at_stake") or 0),
            _ADOPTION_GAP_KIND_ORDER.get(gap.get("kind", ""), 99),
            str(gap.get("item", "")).lower(),
        ),
    )


def _adoption_usage_summary(
    job: dict, usage: dict, account_record: dict
) -> str:
    """One line stating what usage data was available for this run."""
    parts: list[str] = []
    if job.get("usage"):
        bits = []
        if usage.get("seats_purchased") is not None:
            if usage.get("seats_active") is not None:
                bits.append(
                    "%d of %d seats active"
                    % (usage["seats_active"], usage["seats_purchased"])
                )
            else:
                bits.append(
                    "%d seats purchased, active count unknown"
                    % usage["seats_purchased"]
                )
        if usage.get("features"):
            bits.append("%d features" % len(usage["features"]))
        if usage.get("teams"):
            bits.append("%d teams" % len(usage["teams"]))
        parts.append(
            "Job usage: " + ("; ".join(bits) if bits else "supplied but empty")
        )
    else:
        parts.append("No job usage supplied.")
    record_bits = []
    employees = _parse_usage_number(account_record.get("employees"))
    if employees is not None:
        record_bits.append("%d employees" % employees)
    contacts = account_record.get("contacts") or []
    contact_count = len(
        [
            contact
            for contact in contacts
            if isinstance(contact, dict)
            and str(contact.get("name", "") or "").strip()
        ]
    )
    if contact_count:
        record_bits.append("%d named contacts" % contact_count)
    last_touch = str(account_record.get("last_touch", "") or "").strip()
    if last_touch:
        record_bits.append("last touch %s" % last_touch)
    if record_bits:
        parts.append("Account record: " + ", ".join(record_bits) + ".")
    return " ".join(parts)


def _adoption_gaps_block(
    gaps: list[dict], missing: list[str], usage_summary: str
) -> str:
    """The harness-written adoption block appended to the draft: the
    ranked gaps with evidence, value at stake, and next step; what
    usage data was available and what is missing; and the next steps.
    A gap without evidence never appears here."""
    lines = ["adoption gaps (ranked by value at stake):"]
    if gaps:
        for index, gap in enumerate(gaps, 1):
            value = gap.get("value_at_stake")
            if value is not None:
                value_text = "$%s at stake" % format(value, ",")
            else:
                value_text = "value at stake unknown"
            lines.append(
                "%d. [%s] %s — %s. Evidence: %s (source: %s). "
                "Next step: %s"
                % (
                    index,
                    gap["kind"],
                    gap["item"],
                    value_text,
                    gap["evidence"],
                    gap["source"],
                    gap["next_step"],
                )
            )
    else:
        lines.append("No adoption gaps found in the gathered data.")
    lines.append("")
    lines.append("usage data:")
    lines.append(usage_summary)
    if missing:
        lines.append("Missing usage data — collect before the next run:")
        for item in missing:
            lines.append("- " + item)
    lines.append("")
    lines.append("next steps:")
    if gaps:
        for gap in gaps:
            lines.append("- " + gap["next_step"])
    else:
        lines.append(
            "- Collect the missing usage data first: seat counts, "
            "last-login data, feature usage, and the team list."
        )
    return "\n".join(lines)


class AdoptionGapsAgent(Agent):
    """Finds features and teams that were bought but never used.

    Gathers: account record, open opportunities, open cases, recent
    email threads, and recent Slack signals. Reads prior drafts from
    expansion-spotter and value-finder out of the approval queue and
    builds on them. Drafts: the adoption gaps ranked by value at
    stake (unused seats, unused features, unonboarded teams), each
    with its evidence and a suggested next step, plus what usage data
    was available and what is missing.

    Ranking and value at stake are harness-computed, never model
    prose: unused seats x ARR per seat when the inputs support it,
    otherwise unknown and stated as such. A gap without evidence is
    dropped, never drafted. Where usage data is missing entirely the
    draft says so and names what to collect instead of guessing. The
    primary usage source is the job's `usage` block (the CSM pastes
    it from a last-login or usage export); the account record
    supplies the observables (employees, named contacts, last touch).
    """

    name = "adoption-gaps"
    display_name = "Adoption Gaps"
    description = (
        "Finds features and teams bought but never used: unused seats, "
        "unused features, unonboarded teams, ranked by value at stake."
    )
    keywords = [
        "adoption gaps",
        "adoption gap",
        "unused seats",
        "unused features",
        "never used",
        "not using",
        "low adoption",
        "seat usage",
        "feature usage",
        "adoption",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Lists adoption gaps ranked by value at stake",
            keywords=["adoption gaps (ranked by value at stake):"],
        ),
        ProofItem(
            "Names the usage data available and missing",
            keywords=["usage data:"],
        ),
        ProofItem(
            "Lists next steps",
            keywords=["next steps:"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _ADOPTION_GAPS_SOURCE_AGENTS
        )
        if composed_text:
            # Composed: build from the prior drafts. Only the fresh
            # account record, opportunities, cases, and Slack signals
            # are gathered directly; the source drafts carry the usage
            # gaps they already named.
            calls = [
                ("salesforce_get_account", {"account": account}),
                ("salesforce_list_opportunities", {"account": account}),
                ("salesforce_list_cases", {"account": account}),
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
                ("gmail_search_messages", {"query": account, "limit": 5}),
                ("slack_search_messages", {"query": account, "limit": 5}),
            ]
            context, sources, links, results = _gather_with_results(
                registry, calls, budget, job=job
            )
            composed = False
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        account_record = results.get("salesforce_get_account") or {}
        usage, missing = _adoption_usage(job, results)
        gaps = _adoption_gaps(usage, account_record)
        ranked = _rank_adoption_gaps(gaps)
        usage_summary = _adoption_usage_summary(job, usage, account_record)
        prompt = _build_prompt(
            self.name,
            context,
            "Draft an adoption gaps brief for account %r. Cover: the "
            "features and teams bought but never used, what the usage "
            "data shows, and the next steps to drive adoption before "
            "renewal. Use only the context below. Never invent a seat "
            "count, a feature, a team, or a dollar figure the context "
            "does not support; say unknown where the records do not say."
            % account,
        )
        system = (
            "You are the adoption gaps agent for a customer success "
            "team. You find what was bought but never used and say "
            "what to do about it before renewal. Name the gap, cite "
            "the evidence, and do not invent usage the context does "
            "not show. A gap the records do not fill is a gap, not a "
            "guess."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + _adoption_gaps_block(ranked, missing, usage_summary)
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


# The one agent whose drafts call-recap-and-commitments composes from:
# its own prior recaps. Carried-over commitments are read out of the
# latest prior recap draft, never re-invented.
_CALL_RECAP_SOURCE_AGENTS = ("call-recap-and-commitments",)


_COMPLETION_PHRASES = (
    "done",
    "shipped",
    "fixed",
    "completed",
    "resolved",
    "delivered",
    "closed",
    "taken care of",
    "is live",
    "went out",
    "already sent",
    "already done",
)

_COMMITMENT_STOPWORDS = {
    "with", "from", "that", "this", "will", "when", "after", "before",
    "about", "into", "over", "under", "their", "them", "they", "have",
    "been", "were", "what", "your", "ours", "should", "would", "could",
    "there", "here", "then", "than", "also",
}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_WILL_PATTERN = re.compile(
    r"\b(?:I will|I'll|we will|we'll|I'm going to|we're going to)\b",
    re.IGNORECASE,
)

_FIRST_PERSON_PATTERN = re.compile(
    r"\b(?:I will|I'll|I'm going to)\b", re.IGNORECASE
)

_COMMITMENT_LINE_RE = re.compile(
    r'^(?:-|\d+\.)\s+"(?P<text>[^"]+)" — owner: (?P<owner>[^;]+); '
    r"due: (?P<due>[^;]+); status: (?P<status>[^;]+); "
    r"source: (?P<source>.+?)\.?$"
)


def _parse_iso_date(value) -> "date | None":
    """Parse an ISO date or datetime string into a date. None, never a
    crash."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        from datetime import datetime as _datetime

        return _datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _parse_due_date(text: str, call_date=None):
    """A due date from commitment wording, or None ("no date given").

    Understands ISO dates, "by <Month> <day>", "next week", "by Friday"
    / "on Friday", "tomorrow", and "end of week". The reference date is
    the call date (or today when the call date is unknown). A month/day
    already past rolls to next year. Anything else is None, never a
    guess.
    """
    from datetime import date as _date
    from datetime import timedelta as _timedelta

    if not text:
        return None
    lowered = " " + text.lower() + " "
    reference = call_date or _date.today()
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        try:
            return _date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    month_day = re.search(
        r"\bby\s+([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b", lowered
    )
    if month_day:
        month = _MONTHS.get(month_day.group(1))
        if month:
            try:
                due = _date(reference.year, month, int(month_day.group(2)))
            except ValueError:
                return None
            if due < reference:
                try:
                    due = _date(
                        reference.year + 1, month, int(month_day.group(2))
                    )
                except ValueError:
                    return None
            return due
    if "next week" in lowered:
        return reference + _timedelta(days=7)
    if "tomorrow" in lowered:
        return reference + _timedelta(days=1)
    if "end of week" in lowered or "eow" in lowered:
        return reference + _timedelta(days=(4 - reference.weekday()) % 7)
    if re.search(r"\b(?:by|on)\s+friday\b", lowered):
        days_ahead = (4 - reference.weekday()) % 7 or 7
        return reference + _timedelta(days=days_ahead)
    return None


def _extract_owner(action_text: str) -> str:
    """The actor in "Name to <verb> ..." (Zoom action items, Gong key
    points). Empty when no actor is named: the commitment is ownerless,
    never assigned by guess."""
    match = re.match(
        r"^\s*([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\s+to\s+",
        action_text or "",
    )
    return match.group(1) if match else ""


def _commitment_from_action_item(item, source: str, call_date=None) -> dict | None:
    """One commitment from a Zoom action item (or a Gong key point
    phrased as an action). The item text is the wording; the leading
    actor is the owner; the due date is parsed from the wording."""
    text = " ".join(str(item or "").split()).strip()
    if not text:
        return None
    return {
        "text": text,
        "owner": _extract_owner(text),
        "due": _parse_due_date(text, call_date),
        "source": source,
        "status": "new",
    }


def _extract_commitments_from_text(
    text, speaker: str, source: str, call_date=None
) -> list[dict]:
    """Commitment candidates from transcript prose: sentences with a
    first-person promise ("I will ...", "we will ..."). Anything else
    is discussion, not a commitment. A first-person promise belongs to
    the speaker; a "we" promise names no owner and is ownerless."""
    found: list[dict] = []
    if not text:
        return found
    for raw in re.split(r"(?<=[.!?])\s+", str(text).strip()):
        sentence = " ".join(raw.split()).strip()
        if not sentence or not _WILL_PATTERN.search(sentence):
            continue
        first_person = bool(_FIRST_PERSON_PATTERN.search(sentence))
        owner = (speaker or "").strip() if first_person else ""
        found.append(
            {
                "text": sentence,
                "owner": owner,
                "due": _parse_due_date(sentence, call_date),
                "source": source,
                "status": "new",
            }
        )
    return found


def _norm_commitment_text(text: str) -> str:
    """Normalized commitment text for matching: lowercase, punctuation
    stripped, whitespace collapsed."""
    lowered = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    return " ".join(lowered.split())


def _parse_commitment_line(line: str) -> dict | None:
    """Parse one commitment line from a prior call-recap draft (or a
    memory excerpt of one) back into a commitment. A truncated or
    malformed line is None, never a crash."""
    match = _COMMITMENT_LINE_RE.match(line.strip())
    if not match:
        return None
    due_text = match.group("due").strip()
    due = None
    if due_text != "no date given":
        due = _parse_iso_date(due_text)
    owner = match.group("owner").strip()
    if owner == "no owner named":
        owner = ""
    return {
        "text": match.group("text").strip(),
        "owner": owner,
        "due": due,
        "source": match.group("source").strip(),
        "status": match.group("status").strip(),
    }


def _prior_commitments(job: dict, account: str) -> list[dict]:
    """Prior commitments for this account: parsed out of the prior
    call-recap drafts in the approval queue, then out of memory records
    of this agent's drafts. Newest first, deduped by normalized text.
    A queue or memory file the job does not name reads as no prior
    commitments, never an error."""
    prior: list[dict] = []
    seen: set[str] = set()

    def _absorb(text: str) -> None:
        for line in str(text or "").splitlines():
            parsed = _parse_commitment_line(line)
            if not parsed:
                continue
            key = _norm_commitment_text(parsed["text"])
            if not key or key in seen:
                continue
            seen.add(key)
            prior.append(parsed)

    for item in _load_prior_items(job, account, _CALL_RECAP_SOURCE_AGENTS):
        _absorb(item.draft)
    path = job.get("memory_file")
    if path:
        try:
            memory = AccountMemory(path)
        except Exception:
            memory = None
        if memory is not None:
            for record in memory.records_for(account):
                if record.agent != "call-recap-and-commitments":
                    continue
                _absorb(record.excerpt)
                if record.correction:
                    _absorb(record.correction)
    return prior


def _distinctive_words(text: str) -> set[str]:
    """The content words of a commitment: longer than three letters,
    minus stopwords. Used to tell whether new material talks about the
    same commitment."""
    words = set()
    for word in re.findall(r"[a-z]+", (text or "").lower()):
        if len(word) > 3 and word not in _COMMITMENT_STOPWORDS:
            words.add(word)
    return words


def _commitment_kept(text: str, material: str) -> str | None:
    """Whether the new call material shows a prior commitment was kept.
    Returns the evidence sentence, or None. A sentence counts when it
    carries at least two of the commitment's distinctive words and a
    completion phrase ("done", "shipped", "fixed", ...)."""
    words = _distinctive_words(text)
    if len(words) < 2:
        return None
    for raw in re.split(r"(?<=[.!?])\s+", str(material or "")):
        sentence = " ".join(raw.split()).strip()
        if not sentence:
            continue
        lowered = sentence.lower()
        if not any(phrase in lowered for phrase in _COMPLETION_PHRASES):
            continue
        hits = sum(1 for word in words if word in lowered)
        if hits >= 2:
            return sentence
    return None


def _status_commitments(new, prior, material, today=None):
    """Merge this call's commitments with the prior ones.

    A new commitment matching a prior one (same wording, normalized)
    is carried over, keeping the earlier due date when the new wording
    names none. A prior commitment with no match is kept (closed since
    last recap, with the evidence sentence) when the new material shows
    it done, and carried over still open otherwise. Any open
    commitment whose due date has passed is overdue. Returns
    (open_commitments, closed_commitments). Harness-computed, never
    model prose.
    """
    from datetime import date as _date

    today = today or _date.today()
    open_commitments: list[dict] = []
    closed: list[dict] = []
    matched_prior: set[str] = set()
    for commitment in new:
        key = _norm_commitment_text(commitment["text"])
        prior_match = None
        for candidate in prior:
            if _norm_commitment_text(candidate["text"]) == key:
                prior_match = candidate
                break
        entry = dict(commitment)
        if prior_match is not None:
            matched_prior.add(key)
            entry["status"] = "carried over"
            if entry.get("due") is None:
                entry["due"] = prior_match.get("due")
        else:
            entry["status"] = "new"
        if entry.get("due") is not None and entry["due"] < today:
            entry["status"] = "overdue"
        open_commitments.append(entry)
    for candidate in prior:
        key = _norm_commitment_text(candidate["text"])
        if key in matched_prior:
            continue
        evidence = _commitment_kept(candidate["text"], material)
        if evidence:
            closed.append({**candidate, "kept_evidence": evidence})
            continue
        entry = dict(candidate)
        entry["status"] = "carried over"
        if entry.get("due") is not None and entry["due"] < today:
            entry["status"] = "overdue"
        open_commitments.append(entry)
    order = {"overdue": 0, "carried over": 1, "new": 2}
    open_commitments.sort(
        key=lambda c: (
            order.get(c["status"], 3),
            c.get("due") or _date.max,
            _norm_commitment_text(c["text"]),
        )
    )
    return open_commitments, closed


def _latest_call(results: dict, job: dict) -> dict:
    """The call this recap is about: the job's call_id / recording_id
    when named, else the latest Gong call or Zoom recording by start
    date. All fields blank when no call or recording was found: a
    named gap, never a guess."""
    call: dict = {
        "title": "",
        "date": None,
        "started": "",
        "duration_seconds": None,
        "participants": [],
        "gong_call_id": str(job.get("call_id", "") or "").strip(),
        "zoom_recording_id": str(job.get("recording_id", "") or "").strip(),
    }
    candidates: list[dict] = []
    gong_calls = (results.get("gong_list_calls") or {}).get("calls") or []
    for entry in gong_calls:
        if not isinstance(entry, dict):
            continue
        candidates.append(
            {
                "title": str(entry.get("title", "") or "").strip(),
                "date": _parse_iso_date(entry.get("started")),
                "started": str(entry.get("started", "") or ""),
                "duration_seconds": entry.get("duration_seconds"),
                "participants": entry.get("participants") or [],
                "gong_call_id": str(entry.get("id", "") or ""),
                "zoom_recording_id": "",
            }
        )
    zoom_recs = (results.get("zoom_list_recordings") or {}).get("recordings") or []
    for entry in zoom_recs:
        if not isinstance(entry, dict):
            continue
        candidates.append(
            {
                "title": str(entry.get("topic", "") or "").strip(),
                "date": _parse_iso_date(entry.get("start_time")),
                "started": str(entry.get("start_time", "") or ""),
                "duration_seconds": entry.get("duration"),
                "participants": [str(entry.get("host", "") or "")]
                if entry.get("host")
                else [],
                "gong_call_id": "",
                "zoom_recording_id": str(entry.get("id", "") or ""),
            }
        )
    if call["gong_call_id"] or call["zoom_recording_id"]:
        selected = None
        for candidate in candidates:
            if call["gong_call_id"] and candidate["gong_call_id"] == call["gong_call_id"]:
                selected = candidate
                break
            if call["zoom_recording_id"] and candidate["zoom_recording_id"] == call["zoom_recording_id"]:
                selected = candidate
                break
        if selected is None:
            # Named id not in the lists: keep the ids, no title or date.
            return call
        # The same call usually appears in both systems: carry both ids.
        for candidate in candidates:
            if (
                candidate["date"] == selected["date"]
                and candidate["title"] == selected["title"]
            ):
                selected["gong_call_id"] = (
                    selected["gong_call_id"] or candidate["gong_call_id"]
                )
                selected["zoom_recording_id"] = (
                    selected["zoom_recording_id"] or candidate["zoom_recording_id"]
                )
        return selected
    dated = [c for c in candidates if c["date"] is not None]
    if not dated:
        return call
    dated.sort(key=lambda c: c["date"], reverse=True)
    latest = dated[0]
    # The same call usually appears in both systems: carry both ids.
    for candidate in candidates:
        if (
            candidate["date"] == latest["date"]
            and candidate["title"] == latest["title"]
        ):
            latest["gong_call_id"] = (
                latest["gong_call_id"] or candidate["gong_call_id"]
            )
            latest["zoom_recording_id"] = (
                latest["zoom_recording_id"] or candidate["zoom_recording_id"]
            )
    return latest


def _extract_commitments(results: dict, call: dict, call_date=None) -> list[dict]:
    """This call's commitments, harness-computed from the structured
    material only: Zoom action items for the selected recording, Gong
    key points phrased as an action, and transcript excerpts from the
    selected call with a first-person promise. A candidate without a
    source excerpt is dropped, never drafted."""
    commitments: list[dict] = []
    seen: set[str] = set()

    def _add(entry):
        if entry is None:
            return
        key = _norm_commitment_text(entry["text"])
        if not key or key in seen:
            return
        seen.add(key)
        commitments.append(entry)

    zoom_summary = results.get("zoom_get_recording_summary") or {}
    zoom_title = zoom_summary.get("title") or call.get("title") or "call"
    for item in zoom_summary.get("action_items") or []:
        _add(
            _commitment_from_action_item(
                item,
                'Zoom recording "%s" (action item)' % zoom_title,
                call_date,
            )
        )
    gong_summary = results.get("gong_get_call_summary") or {}
    gong_title = gong_summary.get("title") or call.get("title") or "call"
    for point in gong_summary.get("key_points") or []:
        text = " ".join(str(point or "").split()).strip()
        if not text or not _extract_owner(text):
            continue
        _add(
            _commitment_from_action_item(
                text,
                'Gong call "%s" (key point)' % gong_title,
                call_date,
            )
        )
    gong_id = call.get("gong_call_id") or ""
    zoom_id = call.get("zoom_recording_id") or ""
    for match in (results.get("gong_search_transcripts") or {}).get("matches") or []:
        if gong_id and match.get("call_id") and match.get("call_id") != gong_id:
            continue
        source = 'Gong call "%s" @ %s (%s)' % (
            match.get("title", "call"),
            match.get("timestamp", ""),
            match.get("speaker", "unknown"),
        )
        for entry in _extract_commitments_from_text(
            match.get("excerpt", ""), match.get("speaker", ""), source, call_date
        ):
            _add(entry)
    for match in (results.get("zoom_search_transcripts") or {}).get("matches") or []:
        if zoom_id and match.get("recording_id") and match.get("recording_id") != zoom_id:
            continue
        source = 'Zoom recording "%s" @ %s (%s)' % (
            match.get("title", "call"),
            match.get("timestamp", ""),
            match.get("speaker", "unknown"),
        )
        for entry in _extract_commitments_from_text(
            match.get("excerpt", ""), match.get("speaker", ""), source, call_date
        ):
            _add(entry)
    return commitments


def _format_commitment_line(commitment: dict, index: int) -> str:
    """One commitment as a single parseable line: wording, owner, due
    date, status, source. The same format _parse_commitment_line reads
    back out of a prior draft."""
    owner = commitment.get("owner") or "no owner named"
    due = commitment.get("due")
    due_text = due.isoformat() if due is not None else "no date given"
    return (
        '%d. "%s" — owner: %s; due: %s; status: %s; source: %s.'
        % (
            index,
            commitment["text"],
            owner,
            due_text,
            commitment.get("status", "new"),
            commitment.get("source", "call material"),
        )
    )


def _call_recap_block(call, commitments, closed, gaps) -> str:
    """The harness-written recap block appended to the draft: the call,
    the commitments table (wording, owner, due date, status, source),
    what closed since the last recap, the ownerless commitments, and
    the gaps. A commitment without a source excerpt never appears
    here; an ownerless commitment is flagged, never assigned."""
    lines = ["call recap (harness):"]
    title = call.get("title") or "none found"
    date = call.get("date")
    date_text = date.isoformat() if date is not None else "date unknown"
    duration = call.get("duration_seconds")
    if duration:
        duration_text = "%d min" % (int(duration) // 60)
    else:
        duration_text = "duration unknown"
    participants = [p for p in (call.get("participants") or []) if p]
    lines.append(
        "call: %s — %s, %s, participants: %s."
        % (
            title,
            date_text,
            duration_text,
            ", ".join(participants) if participants else "unknown",
        )
    )
    lines.append("commitments:")
    if commitments:
        for index, commitment in enumerate(commitments, 1):
            lines.append(_format_commitment_line(commitment, index))
    else:
        lines.append("No commitments found in this call's material.")
    lines.append("closed since last recap:")
    if closed:
        for entry in closed:
            lines.append(
                '- "%s" — kept. Evidence: "%s"'
                % (entry["text"], entry.get("kept_evidence", ""))
            )
    else:
        lines.append("none.")
    ownerless = [c for c in commitments if not c.get("owner")]
    lines.append("ownerless commitments:")
    if ownerless:
        for entry in ownerless:
            lines.append(
                '- "%s" — no owner named; needs an owner before the next call.'
                % entry["text"]
            )
    else:
        lines.append("none.")
    lines.append("gaps:")
    if gaps:
        for gap in gaps:
            lines.append("- " + gap)
    else:
        lines.append("none.")
    return "\n".join(lines)


class CallRecapAndCommitmentsAgent(Agent):
    """Tracks what was promised on calls and who owns it.

    Gathers: the latest Gong call and Zoom recording for the account,
    their generated summaries, transcript excerpts, the follow-up
    email thread, and open cases and tickets raised on the call. Reads
    prior call-recap drafts and memory for this account, so an old
    commitment is checked against the new material instead of
    re-listed blind. Drafts: a short recap (discussed, decided, left
    open) plus a harness-written commitments table: each commitment
    with its wording, owner, due date, and status (new, carried over,
    overdue).

    Commitments are harness-computed, never model prose: Zoom action
    items, Gong key points phrased as an action, and transcript
    sentences with a first-person promise. A commitment without a
    source excerpt is dropped, never drafted. An ownerless commitment
    is flagged, never assigned by guess. A prior commitment the new
    material shows as done is closed since the last recap, with the
    evidence sentence; one still open is carried over. A due date in
    the past is overdue. The job may name a call_id (Gong) or a
    recording_id (Zoom) to recap that call instead of the latest one.
    """

    name = "call-recap-and-commitments"
    display_name = "Call Recap"
    description = (
        "Tracks what was promised on calls and who owns it: a short "
        "recap plus a commitments table with owner, due date, and "
        "status (new, carried over, overdue)."
    )
    keywords = [
        "call recap",
        "call-recap",
        "call summary",
        "recap the call",
        "call commitments",
        "commitments",
        "commitment",
        "what was promised",
        "promised on the call",
        "action items",
        "call notes",
        "transcript",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Names the call",
            keywords=["call:"],
        ),
        ProofItem(
            "Lists commitments with owner, due date, and status",
            keywords=["commitments:"],
        ),
        ProofItem(
            "Names closed commitments since the last recap",
            keywords=["closed since last recap:"],
        ),
        ProofItem(
            "Flags ownerless commitments",
            keywords=["ownerless commitments:"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        from datetime import date as _date

        account = _extract_account(job)
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _CALL_RECAP_SOURCE_AGENTS
        )
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("gong_list_calls", {"account": account, "days": 30}),
            ("zoom_list_recordings", {"account": account, "days": 30}),
            ("gong_search_transcripts", {"query": account, "limit": 5}),
            ("zoom_search_transcripts", {"query": account, "limit": 5}),
            ("gmail_search_messages", {"query": account, "limit": 5}),
            ("salesforce_list_cases", {"account": account}),
            ("zendesk_list_tickets", {"organization": account}),
        ]
        context, sources, links, results = _gather_with_results(
            registry, calls, budget, job=job
        )
        call = _latest_call(results, job)
        # Phase 2: the generated summary for the selected call. The
        # summary carries the action items the commitments come from.
        phase2 = []
        gong_id = call.get("gong_call_id") or ""
        zoom_id = call.get("zoom_recording_id") or ""
        if gong_id:
            phase2.append(("gong_get_call_summary", {"call_id": gong_id}))
        if zoom_id:
            phase2.append(
                ("zoom_get_recording_summary", {"recording_id": zoom_id})
            )
        if phase2:
            context2, sources2, links2, results2 = _gather_with_results(
                registry, phase2, budget
            )
            context.update(context2)
            sources.extend(s for s in sources2 if s not in sources)
            seen_urls = {link.get("url") for link in links}
            for link in links2:
                if link.get("url") not in seen_urls:
                    seen_urls.add(link.get("url"))
                    links.append(link)
            results.update(results2)
        if not call.get("title"):
            for key in ("gong_get_call_summary", "zoom_get_recording_summary"):
                title = (results.get(key) or {}).get("title")
                if title:
                    call["title"] = title
                    break
        if composed_text:
            context["prior_agent_drafts"] = composed_text
            seen_urls = {link.get("url") for link in links}
            for link in composed_links:
                if link.get("url") not in seen_urls:
                    seen_urls.add(link.get("url"))
                    links.append(link)
            composed = True
        else:
            composed = False
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        call_date = call.get("date")
        today = _date.today()
        material = " ".join(str(value) for value in context.values())
        prior = _prior_commitments(job, account)
        new_commitments = _extract_commitments(results, call, call_date)
        open_commitments, closed = _status_commitments(
            new_commitments, prior, material, today
        )
        gaps: list[str] = []
        if not call.get("title"):
            gaps.append(
                "No calls or recordings found for this account in the "
                "last 30 days: supply a call_id or recording_id in the "
                "job, or check the Gong/Zoom connection."
            )
        elif not (
            results.get("gong_get_call_summary")
            or results.get("zoom_get_recording_summary")
        ):
            gaps.append(
                "No generated summary available for the selected call: "
                "the recap is built from transcript excerpts and the "
                "follow-up thread only."
            )
        if not (results.get("gong_search_transcripts") or {}).get(
            "matches"
        ) and not (results.get("zoom_search_transcripts") or {}).get(
            "matches"
        ):
            gaps.append("No transcript excerpts matched this account.")
        if not open_commitments and not closed:
            gaps.append("No commitments found in this call's material.")
        if not (results.get("gmail_search_messages") or {}).get("matches"):
            gaps.append("No follow-up email thread found for this account.")
        block = _call_recap_block(call, open_commitments, closed, gaps)
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a call recap for account %r (call: %s). Cover: what "
            "was discussed, what was decided, and what was left open. "
            "Use only the context below. Never invent a commitment, an "
            "owner, or a due date the context does not support; say "
            "unknown where the records do not say. The harness appends "
            "the commitments table."
            % (account, call.get("title") or "no call found"),
        )
        system = (
            "You are the call recap agent for a customer success team. "
            "You recap the call and track what was promised, so nothing "
            "dies in a transcript. Name what was discussed, decided, "
            "and left open, and do not invent a promise, an owner, or "
            "a date the context does not show."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + block
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


# The four agents whose drafts trust-repair composes from:
# escalation-manager first (its plan is the recovery's starting point),
# plus risk-watcher, call-recap-and-commitments, and case-triage.
_TRUST_REPAIR_SOURCE_AGENTS = (
    "escalation-manager",
    "risk-watcher",
    "call-recap-and-commitments",
    "case-triage",
)


def _classify_incident_text(text: str) -> str:
    """Classify a named incident by its wording. Outage language first,
    then escalation, then a missed promise. Anything else is unknown:
    the incident is still named, only its kind is not classified."""
    lowered = str(text or "").lower()
    if any(
        word in lowered
        for word in ("outage", "downtime", "service down", "unavailable", "offline")
    ) or re.search(r"\bdown\b", lowered):
        return "outage"
    if "escalat" in lowered:
        return "bad-escalation"
    if "promise" in lowered or "missed" in lowered or "never delivered" in lowered:
        return "missed-promise"
    return "unknown"


def _missed_prior_commitments(job: dict, account: str, today=None) -> list[dict]:
    """Prior commitments for this account whose due date has passed and
    that no later material shows as done. A commitment with no due date
    cannot be missed; one already closed is not missed. Newest first."""
    if today is None:
        from datetime import date as _date

        today = _date.today()
    missed: list[dict] = []
    for item in _prior_commitments(job, account):
        due = item.get("due")
        if due is None:
            continue
        status = str(item.get("status", "") or "").lower()
        if "closed" in status or "done" in status:
            continue
        if due < today:
            missed.append(item)
    return missed


def _trust_hit(
    job: dict, signals: list[dict], records: list, missed: list[dict]
) -> dict:
    """Work out what hit the account's trust, harness-computed.

    Order: an incident the job names, then an open escalation record,
    then a high-severity support failure, then a missed prior
    commitment, then escalation language in messages or transcripts. A
    hit with no named incident is kind "unknown" with the gap stated,
    never a guessed incident. A stalled renewal is a risk, not a trust
    hit, so it never claims the hit on its own."""
    incident = str(job.get("incident", "") or "").strip()
    if incident:
        kind = str(job.get("incident_type", "") or "").strip().lower()
        if kind not in (
            "outage",
            "bad-escalation",
            "missed-promise",
            "support-failure",
        ):
            kind = _classify_incident_text(incident)
        return {
            "kind": kind,
            "summary": incident,
            "evidence": "Named in the job: %r" % incident,
            "severity": "high",
        }
    open_records = [
        record
        for record in records
        if str(getattr(record, "status", "")) in ("open", "escalated")
    ]
    if open_records:
        record = open_records[0]
        kind = str(getattr(record, "signal_kind", "") or "")
        if kind not in (
            "outage",
            "bad-escalation",
            "missed-promise",
            "support-failure",
        ):
            kind = "bad-escalation"
        return {
            "kind": kind,
            "summary": str(
                getattr(record, "summary", "") or record.signal_key
            ),
            "evidence": "Escalation record %s (%s, %s): %s"
            % (
                record.id,
                record.signal_key,
                record.status,
                getattr(record, "summary", "") or "no summary recorded",
            ),
            "severity": str(getattr(record, "severity", "high") or "high"),
        }
    high_support = [
        signal
        for signal in signals
        if signal.get("severity") == "high"
        and signal.get("kind") == "support-delivery"
    ]
    if high_support:
        signal = high_support[0]
        return {
            "kind": "support-failure",
            "summary": signal["text"],
            "evidence": "Risk signal (%s): %s" % (signal["kind"], signal["text"]),
            "severity": "high",
        }
    if missed:
        item = missed[0]
        due = item.get("due")
        return {
            "kind": "missed-promise",
            "summary": item["text"],
            "evidence": "Prior commitment, due %s, still open: %r (owner: %s)"
            % (
                due.isoformat() if due else "no date given",
                item["text"],
                item.get("owner") or "no owner named",
            ),
            "severity": "medium",
        }
    language = [
        signal for signal in signals if signal.get("kind") == "escalation-language"
    ]
    if language:
        signal = language[0]
        return {
            "kind": "bad-escalation",
            "summary": signal["text"],
            "evidence": "Risk signal (%s): %s" % (signal["kind"], signal["text"]),
            "severity": "medium",
        }
    return {"kind": "unknown", "summary": "", "evidence": "", "severity": "none"}


def _trust_impact(
    results: dict, account: str, signals: list[dict], records: list
) -> list[str]:
    """The impact assessment, harness-computed: renewal at risk, the
    champion exposed, the owner, open escalations, and the open
    high-severity support failure. Every line names its evidence."""
    impact: list[str] = []
    for signal in signals:
        if signal.get("kind") == "renewal-stalled":
            impact.append("Renewal at risk: %s" % signal["text"])
        elif signal.get("kind") == "renewal-closing":
            impact.append("Renewal closing soon: %s" % signal["text"])
    open_records = [
        record
        for record in records
        if str(getattr(record, "status", "")) in ("open", "escalated")
    ]
    if open_records:
        impact.append(
            "%d open escalation(s) for this account: %s."
            % (
                len(open_records),
                ", ".join(
                    "%s (%s)" % (record.id, record.signal_key)
                    for record in open_records
                ),
            )
        )
    account_record = results.get("salesforce_get_account") or {}
    contacts = account_record.get("contacts") or []
    champion = next(
        (
            contact
            for contact in contacts
            if str(contact.get("role", "")).lower() == "champion"
        ),
        None,
    )
    if champion:
        impact.append(
            "Champion %s is the relationship most exposed: the recovery "
            "lands or fails with them."
            % champion.get("name", "?")
        )
    else:
        impact.append(
            "No champion named in the account record: the recovery has "
            "no customer sponsor to anchor on."
        )
    owner = account_record.get("owner")
    if owner:
        impact.append("Account owner %s owns the recovery on our side." % owner)
    else:
        impact.append("No account owner named in the account record.")
    high_support = [
        signal
        for signal in signals
        if signal.get("severity") == "high"
        and signal.get("kind") == "support-delivery"
    ]
    if high_support:
        impact.append("Open high-severity support failure: %s" % high_support[0]["text"])
    return impact


def _recovery_steps(hit: dict, results: dict, records: list) -> list[dict]:
    """The recovery plan, harness-written. Each step names an owner and
    a date where the evidence supports one; where it does not, the step
    says so instead of inventing one. Follow-up dates are proposed
    (labeled as such); a fix date is stated only when a record states
    one, and no record in the gather carries one, so the fix step
    always asks for one before the next update."""
    account_record = results.get("salesforce_get_account") or {}
    owner = account_record.get("owner") or "the account owner"
    fix_owner = owner
    for key in ("salesforce_list_cases",):
        for case in (results.get(key) or {}).get("cases", []) or []:
            if str(case.get("status", "")).lower() in ("closed", "solved"):
                continue
            if case.get("owner"):
                fix_owner = case["owner"]
                break
    hit_text = hit["summary"] or "the trust hit"
    return [
        {
            "title": "Acknowledge and own it",
            "detail": (
                "Tell the customer, in the CSM's words: we own this. %s "
                "%s is driving the recovery and will update you within "
                "24 hours." % (hit_text, owner)
            ),
            "owner": owner,
            "date": "within 24 hours (proposed)",
        },
        {
            "title": "Fix it",
            "detail": (
                "Resolve the underlying failure: %s. The fix is owned by "
                "%s." % (hit_text, fix_owner)
            ),
            "owner": fix_owner,
            "date": None,
        },
        {
            "title": "Follow-up cadence",
            "detail": (
                "Update the customer within 24 hours of this draft, then "
                "every 7 days until the fix is confirmed, then a 30-day "
                "check-in after resolution."
            ),
            "owner": owner,
            "date": "24 hours, then every 7 days (proposed)",
        },
        {
            "title": "Proof the customer will see",
            "detail": (
                "The case closed and confirmed by the customer on a "
                "verification call, with the fix named in the closure "
                "note. Until the case closes, the proof is not yet "
                "available: say so in the update, do not claim it."
            ),
            "owner": owner,
            "date": None,
        },
    ]


def _trust_repair_block(
    hit: dict,
    impact: list[str],
    steps: list[dict],
    missed: list[dict],
    gaps: list[str],
) -> str:
    """The harness-written trust repair block appended to the draft: the
    trust-hit summary with evidence, the impact assessment, the
    recovery plan with owner and date per step, the missed commitments,
    and the gaps. Harness-written so the plan is in the draft even when
    the model is the stub."""
    lines = ["Trust hit:"]
    if hit["kind"] == "unknown":
        lines.append(
            "- No trust hit named in the job, the escalation store, the "
            "risk signals, or prior commitments. Name the incident "
            "(outage, bad escalation, or missed promise) to build a "
            "recovery plan."
        )
    else:
        lines.append("- [%s] %s" % (hit["kind"], hit["summary"]))
        lines.append("  Evidence: %s" % hit["evidence"])
    lines.append("Severity: %s." % hit["severity"])
    lines.append("")
    lines.append("Impact:")
    for line in impact:
        lines.append("- " + line)
    lines.append("")
    lines.append("Recovery plan:")
    for i, step in enumerate(steps, 1):
        lines.append("%d. %s" % (i, step["title"]))
        lines.append("   %s" % step["detail"])
        lines.append(
            "   Owner: %s."
            % (
                step["owner"]
                or "not named in the records — name an owner before sending this"
            )
        )
        lines.append(
            "   Date: %s."
            % (
                step["date"]
                or "not stated in the records — get a date before the next update"
            )
        )
    lines.append("")
    lines.append("Missed commitments:")
    if missed:
        for item in missed:
            due = item.get("due")
            lines.append(
                "- %r (owner: %s, due: %s)"
                % (
                    item["text"],
                    item.get("owner") or "no owner named",
                    due.isoformat() if due else "no date given",
                )
            )
    else:
        lines.append("- None found in prior call-recap drafts.")
    lines.append("")
    lines.append("Gaps:")
    if gaps:
        for gap in gaps:
            lines.append("- " + gap)
    else:
        lines.append("- None.")
    return "\n".join(lines)


class TrustRepairAgent(Agent):
    """Recovers trust after an outage, a bad escalation, or a missed promise.

    Gathers: the account record, open opportunities, open cases and
    tickets, Linear issues, Gmail and Slack messages, Gong and Zoom
    transcripts, the escalation store for this account, and prior
    call-recap drafts (for missed commitments). Composes from prior
    escalation-manager, risk-watcher, call-recap-and-commitments, and
    case-triage drafts when they exist; gathers directly otherwise.
    Drafts: the trust-hit summary with evidence, the impact on
    relationships and renewals, and a recovery plan: acknowledge and
    own it, fix it, the follow-up cadence, and the proof the customer
    will see.

    The hit, the impact, and the plan are harness-computed, never model
    prose: the hit is claimed only when the job names an incident, an
    escalation record is open, a high-severity support failure is
    detected, a prior commitment is past due, or escalation language
    appears in the records. A stalled renewal is a risk, not a trust
    hit, so it never claims the hit on its own. Each recovery step names
    an owner and a date where the evidence supports one; where it does
    not, the step says so instead of inventing one. With no incident
    named anywhere the draft names the gap, never a recovery for a
    failure that did not happen.
    """

    name = "trust-repair"
    display_name = "Trust Repair"
    description = (
        "Recovers trust after an outage, a bad escalation, or a missed "
        "promise: the trust-hit summary with evidence, the impact on "
        "relationships and renewals, and a recovery plan with owner, "
        "dates, and the proof the customer will see."
    )
    keywords = [
        "trust repair",
        "trust-repair",
        "recover trust",
        "rebuild trust",
        "trust hit",
        "outage",
        "service outage",
        "missed promise",
        "broken promise",
        "bad escalation",
        "make it right",
        "recovery plan",
        "win back trust",
        "service failure",
    ]
    proof_checklist = [
        ProofItem(
            "Names the trust hit",
            keywords=["trust hit:"],
        ),
        ProofItem(
            "Assesses the impact",
            keywords=["impact:"],
        ),
        ProofItem(
            "Gives the recovery plan",
            keywords=["recovery plan:"],
        ),
        ProofItem(
            "Names the follow-up cadence",
            keywords=["follow-up cadence"],
        ),
        ProofItem(
            "Names the proof the customer will see",
            keywords=["proof the customer will see"],
        ),
        ProofItem(
            "Cites the account record",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        from datetime import date as _date

        account = _extract_account(job)
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _TRUST_REPAIR_SOURCE_AGENTS
        )
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("zendesk_list_tickets", {"organization": account}),
            ("linear_list_issues", {"account": account}),
            ("gmail_search_messages", {"query": account, "limit": 5}),
            ("gong_search_transcripts", {"query": account, "limit": 5}),
            ("zoom_search_transcripts", {"query": account, "limit": 5}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources, links, results = _gather_with_results(
            registry, calls, budget, job=job
        )
        store = _escalation_store(job)
        records = store.for_account(account)
        signals = _risk_signals(results, account)
        today = _date.today()
        missed = _missed_prior_commitments(job, account, today)
        hit = _trust_hit(job, signals, records, missed)
        impact = _trust_impact(results, account, signals, records)
        steps = _recovery_steps(hit, results, records)
        gaps: list[str] = []
        if hit["kind"] == "unknown":
            gaps.append(
                "No trust hit named: supply the incident in the job "
                "(outage, bad escalation, or missed promise), or check "
                "the escalation store and the risk signals."
            )
        account_record = results.get("salesforce_get_account") or {}
        if not account_record.get("owner"):
            gaps.append("No account owner named in the account record.")
        contacts = account_record.get("contacts") or []
        if not any(
            str(contact.get("role", "")).lower() == "champion"
            for contact in contacts
        ):
            gaps.append(
                "No champion named in the account record: the recovery "
                "has no customer sponsor to anchor on."
            )
        open_items = (
            (results.get("salesforce_list_cases") or {}).get("cases", [])
            or (results.get("zendesk_list_tickets") or {}).get("tickets", [])
            or (results.get("linear_list_issues") or {}).get("issues", [])
        )
        if not open_items and not records:
            gaps.append(
                "No open cases, tickets, or issues found and no "
                "escalation records: the fix has nothing to attach to yet."
            )
        if composed_text:
            context["prior_agent_drafts"] = composed_text
            seen_urls = {link.get("url") for link in links}
            for link in composed_links:
                if link.get("url") not in seen_urls:
                    seen_urls.add(link.get("url"))
                    links.append(link)
            composed = True
        else:
            composed = False
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        block = _trust_repair_block(hit, impact, steps, missed, gaps)
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a trust recovery plan for account %r (trust hit: %s). "
            "Cover: what happened, the impact on relationships and "
            "renewals, and the recovery. Use only the context below. "
            "Never invent an incident, an owner, or a date the context "
            "does not support; say unknown where the records do not say. "
            "The harness appends the recovery plan."
            % (account, hit["summary"] or "none named"),
        )
        system = (
            "You are the trust repair agent for a customer success team. "
            "You rebuild trust after a failure: name what happened, what "
            "it cost, and how it gets fixed, and do not invent an "
            "incident, an owner, or a date the context does not show."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + block
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


# The nine agents whose drafts ebr-prep composes from: the prior
# review and brief, the value and expansion picture, the risks and
# recovery, the adoption gaps, the relationship map, and the open
# commitments.
_EBR_PREP_SOURCE_AGENTS = (
    "qbr-builder",
    "briefer",
    "value-finder",
    "expansion-spotter",
    "risk-watcher",
    "adoption-gaps",
    "trust-repair",
    "stakeholder-mapper",
    "call-recap-and-commitments",
)

# Words that mark a contact or a named person as an executive, for
# the EBR audience. Matched case-insensitively against a title or a
# role; a name with no title is not an executive.
_EXEC_TITLE_TOKENS = (
    "chief",
    "vp",
    "vice president",
    "director",
    "head of",
    "president",
    "cfo",
    "ceo",
    "cto",
    "coo",
    "ciso",
    "cmo",
    "general manager",
    "executive",
)


def _ebr_ask(job, account_record, opportunities, signals, today=None):
    """The ask for the EBR, harness-computed.

    Order: an ask the job names, then a renewal closing within 90
    days, then an open expansion opportunity, then a high-severity
    risk signal needing a decision. None of those is a missing ask
    hidden as a small one: kind "none" with the gap stated.
    """
    named = str(job.get("ask", "") or "").strip()
    if named:
        return {
            "kind": "named",
            "text": named,
            "evidence": "Named in the job.",
        }
    if today is None:
        from datetime import date as _date

        today = _date.today()
    opps = (opportunities or {}).get("opportunities", []) or []
    renewal_date = (account_record or {}).get("renewal_date")
    if renewal_date:
        due = _parse_iso_date(str(renewal_date))
        if due is not None and 0 <= (due - today).days <= 90:
            amount = (account_record or {}).get("arr")
            renewal_opp = next(
                (
                    opp
                    for opp in opps
                    if "renewal" in str(opp.get("type", "")).lower()
                ),
                None,
            )
            if renewal_opp is not None and isinstance(
                renewal_opp.get("amount"), (int, float)
            ):
                amount = renewal_opp["amount"]
            amount_text = (
                format_money(amount)
                if isinstance(amount, (int, float))
                else "amount not on record"
            )
            return {
                "kind": "renewal",
                "text": (
                    "Ask the executives to confirm the renewal on %s "
                    "(%s) and name the signer."
                    % (due.isoformat(), amount_text)
                ),
                "evidence": "Renewal date %s in the account record."
                % due.isoformat(),
            }
    expansion = [
        opp
        for opp in opps
        if "expansion" in str(opp.get("type", "")).lower()
        and str(opp.get("stage", "")).lower()
        not in ("closed won", "closed lost")
    ]
    if expansion:
        opp = expansion[0]
        amount = opp.get("amount")
        amount_text = (
            format_money(amount)
            if isinstance(amount, (int, float))
            else "amount not on record"
        )
        return {
            "kind": "expansion",
            "text": (
                "Ask the executives to approve the %s expansion (%s) "
                "and name the sponsor."
                % (opp.get("name", "open"), amount_text)
            ),
            "evidence": "Open expansion opportunity %s in the gathered records."
            % opp.get("name", "unnamed"),
        }
    high = [s for s in (signals or []) if s.get("severity") == "high"]
    if high:
        return {
            "kind": "decision",
            "text": "Ask the executives to decide on: %s"
            % high[0].get("text", "the open high-severity signal"),
            "evidence": "High-severity risk signal in the gathered records.",
        }
    return {
        "kind": "none",
        "text": "",
        "evidence": (
            "No ask named: no renewal within 90 days, no open "
            "expansion opportunity, and no high-severity signal "
            "needing a decision. Name the ask in the job."
        ),
    }


def _ebr_audience(job, account_record, account):
    """The executive audience for the EBR: who is in the room.

    The job names attendees first, then the account record contacts
    with executive titles, then lines in the latest prior
    stakeholder-mapper draft that name an executive. Returns
    (people, gaps): people are {"name", "title", "evidence"} dicts,
    deduped by name; a room with nobody named is a gap, not a guess.
    """
    people: list[dict] = []
    seen: set[str] = set()

    def _add(name, title, evidence):
        key = str(name or "").strip().lower()
        if not key or key in seen:
            return
        seen.add(key)
        people.append(
            {
                "name": str(name).strip(),
                "title": str(title or "").strip(),
                "evidence": evidence,
            }
        )

    audience = job.get("audience")
    if isinstance(audience, str) and audience.strip():
        for part in audience.split(","):
            _add(part.strip(), "", "Named in the job.")
    elif isinstance(audience, (list, tuple)):
        for part in audience:
            if isinstance(part, dict):
                _add(
                    part.get("name"),
                    part.get("title") or part.get("role"),
                    "Named in the job.",
                )
            else:
                _add(str(part), "", "Named in the job.")
    for contact in (account_record or {}).get("contacts", []) or []:
        if not isinstance(contact, dict):
            continue
        title = str(contact.get("title") or contact.get("role") or "")
        if title and any(tok in title.lower() for tok in _EXEC_TITLE_TOKENS):
            _add(contact.get("name"), title, "Account record contact.")
    for item in _load_prior_items(job, account, ("stakeholder-mapper",)):
        for line in str(item.draft or "").splitlines():
            lowered = line.lower()
            if not any(tok in lowered for tok in _EXEC_TITLE_TOKENS):
                continue
            text = " ".join(line.split()).lstrip("- ").strip()
            name = text.split(":", 1)[0].strip()
            title = ""
            if ":" in text:
                title = text.split(":", 1)[1].strip()
                if len(title) > 80:
                    title = title[:80] + " ..."
            _add(name, title, "Named in the stakeholder-mapper draft.")
        if people:
            break
    gaps: list[str] = []
    if not people:
        gaps.append(
            "No executive audience named: supply the attendees in "
            "the job (audience), or name them in the account record."
        )
    return people, gaps


def _ebr_last_review_goals(job, account):
    """Goals set at the last review, quoted from the latest prior QBR
    draft. Lines naming next quarter or a goal, up to three. Empty
    when no prior QBR draft names any: the draft says so instead of
    inventing the last review's goals."""
    items = _load_prior_items(job, account, ("qbr-builder",))
    goals: list[str] = []
    for item in items:
        for line in str(item.draft or "").splitlines():
            lowered = line.lower()
            if "next quarter" in lowered or "goal" in lowered:
                text = " ".join(line.split())
                if text and text not in goals:
                    goals.append(text)
        if goals:
            break
    return goals[:3]


def _ebr_mitigation(signal):
    """The mitigation for one risk signal, harness-written per kind."""
    kind = signal.get("kind", "")
    if kind == "support-delivery":
        return (
            "Get a named owner and a fix date, and a status update "
            "the CSM can send the customer."
        )
    if kind in ("renewal-stalled", "renewal-closing"):
        return (
            "Confirm whether the renewal is still alive, who owns "
            "the close, and the real close date."
        )
    if kind == "escalation-language":
        return (
            "Confirm who owns the escalation language and what the "
            "next step is."
        )
    return "Confirm who owns this signal and what the next step is."


def _ebr_adoption_expansion(composed_text, opportunities):
    """Adoption gaps and expansion opportunities for the review.

    The first lines of the composed adoption-gaps and
    expansion-spotter drafts, plus every open expansion
    opportunity in the gathered records. Empty when neither
    source names any: the draft says so instead of inventing one.
    """
    lines: list[str] = []
    for line in str(composed_text or "").splitlines():
        if line.startswith("adoption-gaps (") or line.startswith(
            "expansion-spotter ("
        ):
            text = line.split("): ", 1)[-1].strip()
            if text:
                lines.append(text[:200])
    for opp in (opportunities or {}).get("opportunities", []) or []:
        if "expansion" not in str(opp.get("type", "")).lower():
            continue
        if str(opp.get("stage", "")).lower() in (
            "closed won",
            "closed lost",
        ):
            continue
        amount = opp.get("amount")
        lines.append(
            "Open expansion: %s (%s)."
            % (
                opp.get("name", "unnamed"),
                format_money(amount)
                if isinstance(amount, (int, float))
                else "amount not on record",
            )
        )
    return lines


def _ebr_prep_block(
    summary,
    figures,
    signals,
    ask,
    audience,
    commitments,
    missed,
    goals,
    adoption_expansion,
    gaps,
):
    """The harness-written EBR block appended to the draft: the
    executive summary, value delivered with numbers, risks and
    mitigations, adoption and expansion, the ask, the executive
    audience, open commitments, last review's goals, and the gaps.
    Every number here is harness-computed; a missing number is a
    named gap, never an invented figure."""
    lines = ["executive business review (harness):"]
    lines.append("executive summary: %s" % summary)
    lines.append("value delivered:")
    lines.extend("  " + line for line in figures.splitlines())
    if signals:
        lines.append("risks and mitigations:")
        for signal in signals:
            lines.append(
                "  - [%s] %s Mitigation: %s"
                % (
                    signal.get("severity", "?"),
                    signal.get("text", ""),
                    _ebr_mitigation(signal),
                )
            )
    else:
        lines.append(
            "risks and mitigations: no signals, a clean bill for this review."
        )
    if adoption_expansion:
        lines.append("adoption and expansion:")
        for item in adoption_expansion:
            lines.append("  - %s" % item)
    else:
        lines.append("adoption and expansion: none on record.")
    lines.append("the ask: %s" % (ask["text"] or ask["evidence"]))
    if audience:
        lines.append("executive audience:")
        for person in audience:
            title = " (%s)" % person["title"] if person["title"] else ""
            lines.append(
                "  - %s%s — %s" % (person["name"], title, person["evidence"])
            )
    else:
        lines.append("executive audience: none named.")
    missed_texts = {
        _norm_commitment_text(c.get("text", "")) for c in (missed or [])
    }
    open_list = [
        c
        for c in (commitments or [])
        if "closed" not in str(c.get("status", "") or "").lower()
        and "done" not in str(c.get("status", "") or "").lower()
        and _norm_commitment_text(c.get("text", "")) not in missed_texts
    ]
    if open_list or missed:
        lines.append("open commitments:")
        for i, commitment in enumerate(open_list, start=1):
            lines.append("  " + _format_commitment_line(commitment, i))
        for commitment in missed:
            due = commitment.get("due")
            due_text = due.isoformat() if due is not None else "no date given"
            lines.append(
                '  OVERDUE: "%s" — owner: %s; due: %s; source: %s.'
                % (
                    commitment.get("text", ""),
                    commitment.get("owner") or "no owner named",
                    due_text,
                    commitment.get("source", "call material"),
                )
            )
    else:
        lines.append("open commitments: none on record.")
    if goals:
        lines.append("last review goals:")
        for goal in goals:
            lines.append("  - %s" % goal)
    else:
        lines.append(
            "last review goals: none on record, the goals set at the "
            "last review are not on file."
        )
    if gaps:
        lines.append("gaps:")
        for gap in gaps:
            lines.append("  - %s" % gap)
    return "\n".join(lines)


class EBRPrepAgent(Agent):
    """Prepares an executive business review for an account.

    Distinct from qbr-builder: a QBR is the quarterly cadence deck,
    an EBR is prepared for a specific executive meeting, leads with
    outcomes and value (not activity), and ends with an ask: a
    renewal, an expansion, or a decision the executives must make.
    Gathers: the account record, open opportunities, open cases and
    tickets, Linear issues, Gmail and Slack messages, Gong and Zoom
    transcripts. Composes from prior qbr-builder, briefer,
    value-finder, expansion-spotter, risk-watcher, adoption-gaps,
    trust-repair, stakeholder-mapper, and call-recap-and-commitments
    drafts when they exist; gathers directly otherwise.

    The executive summary, the value figures, the risks and their
    mitigations, the ask, the executive audience, and the last
    review's goals are harness-computed, never model prose. Every
    number carries a source, and a missing number is named as a gap:
    where the last review's goals are not on record the draft says
    so instead of inventing them.
    """

    name = "ebr-prep"
    display_name = "EBR Prep"
    description = (
        "Prepares an executive business review: outcomes and value "
        "delivered, risks and mitigations, adoption and expansion, "
        "and the ask the executives must decide."
    )
    keywords = [
        "ebr",
        "ebr-prep",
        "ebr prep",
        "executive business review",
        "prepare an ebr",
        "prepare the ebr",
        "ebr for",
        "business review",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Gives the executive summary",
            keywords=["executive summary:"],
        ),
        ProofItem(
            "Covers value delivered with numbers",
            keywords=["value delivered:"],
        ),
        ProofItem(
            "Covers risks and mitigations",
            keywords=["risks and mitigations:"],
        ),
        ProofItem(
            "Ends with the ask",
            keywords=["the ask:"],
        ),
        ProofItem(
            "Names the executive audience",
            keywords=["executive audience:"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        from datetime import date as _date

        account = _extract_account(job)
        today = _date.today()
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _EBR_PREP_SOURCE_AGENTS
        )
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("zendesk_list_tickets", {"organization": account}),
            ("linear_list_issues", {}),
            ("gmail_search_messages", {"query": account, "limit": 5}),
            ("gong_search_transcripts", {"query": account, "limit": 5}),
            ("zoom_search_transcripts", {"query": account, "limit": 5}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources, links, results = _gather_with_results(
            registry, calls, budget, job=job
        )
        account_record = results.get("salesforce_get_account") or {}
        opportunities = results.get("salesforce_list_opportunities")
        signals = _risk_signals(results, account, today)
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
            opportunities, hours_saved, hourly_rate, rate_source
        )
        ask = _ebr_ask(
            job, account_record, opportunities, signals, today
        )
        audience, audience_gaps = _ebr_audience(
            job, account_record, account
        )
        goals = _ebr_last_review_goals(job, account)
        commitments = _prior_commitments(job, account)
        missed = _missed_prior_commitments(job, account, today)
        adoption_expansion = _ebr_adoption_expansion(
            composed_text, opportunities
        )
        open_commitments = [
            c
            for c in commitments
            if "closed" not in str(c.get("status", "") or "").lower()
            and "done" not in str(c.get("status", "") or "").lower()
        ]
        gaps: list[str] = []
        if ask["kind"] == "none":
            gaps.append(ask["evidence"])
        gaps.extend(audience_gaps)
        if not goals:
            gaps.append(
                "No prior QBR draft on record: the goals set at the "
                "last review are not on file."
            )
        if not adoption_expansion:
            gaps.append(
                "No adoption gaps or expansion opportunities on record."
            )
        if not commitments and not missed:
            gaps.append("No prior commitments on record for this account.")
        arr = account_record.get("arr")
        summary = (
            "%s: %s ARR, health %s, renewal %s, %d open risk signal(s), "
            "%d open commitment(s)."
            % (
                account,
                format_money(arr)
                if isinstance(arr, (int, float))
                else "ARR not on record",
                account_record.get("health") or "unknown",
                account_record.get("renewal_date") or "no renewal date",
                len(signals),
                len(open_commitments),
            )
        )
        if composed_text:
            context["prior_agent_drafts"] = composed_text
            seen_urls = {link.get("url") for link in links}
            for link in composed_links:
                if link.get("url") not in seen_urls:
                    seen_urls.add(link.get("url"))
                    links.append(link)
            composed = True
        else:
            composed = False
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        block = _ebr_prep_block(
            summary,
            figures,
            signals,
            ask,
            audience,
            commitments,
            missed,
            goals,
            adoption_expansion,
            gaps,
        )
        prompt = _build_prompt(
            self.name,
            context,
            "Draft an executive business review for account %r for a "
            "specific executive meeting (not a quarterly cadence). "
            "Lead with outcomes and value delivered, not activity. "
            "Cover: the executive summary, value delivered with "
            "numbers and sources, risks and mitigations, adoption and "
            "expansion opportunities, and the ask. Use only the "
            "context below. Never invent a number, an attendee, or a "
            "goal the context does not support; say unknown where the "
            "records do not say. The harness appends the review block."
            % account,
        )
        system = (
            "You are the EBR prep agent for a customer success team. "
            "You draft executive business reviews: outcomes and value, "
            "not activity, and every number must come from the context. "
            "Do not invent a number, an attendee, or a goal the context "
            "does not show."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + block
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


# The eight agents whose drafts churn-post-mortem composes from: the
# risk and recovery picture, the champion story, the open commitments
# and adoption gaps, the renewal plan, and the brief.
_CHURN_POST_MORTEM_SOURCE_AGENTS = (
    "risk-watcher",
    "escalation-manager",
    "champion-change",
    "trust-repair",
    "call-recap-and-commitments",
    "adoption-gaps",
    "renewal-desk",
    "briefer",
)

# What would have to change before a re-approach could work, by
# churn-reason kind. Templated next steps, not model prose: each line
# names the fix the reason itself points at.
_CHURN_FIX_BY_KIND = {
    "closed-lost-renewal": (
        "The renewal itself would have to be re-openable: a new budget "
        "cycle, a new sponsor, or a scope the customer actually wants."
    ),
    "champion-departure": (
        "A new sponsor would have to be engaged: the departed champion "
        "is gone, so the re-approach needs a named person inside the "
        "account, not the old relationship."
    ),
    "support-failure": (
        "The support failure would have to be fixed in a way the "
        "customer can see: a shipped fix, not a promise of one."
    ),
    "missed-commitments": (
        "The missed commitments would have to be closed first: a "
        "re-approach that repeats an unkept promise is a second miss."
    ),
    "escalation": (
        "The escalation would have to be resolved and the resolution "
        "shown to the customer: an open escalation is not a door to "
        "knock on."
    ),
    "adoption": (
        "The adoption gap would have to close: seats or teams bought "
        "but never used are the reason the renewal did not survive."
    ),
    "named": (
        "The named reason would have to change: re-approach only when "
        "the thing that drove the churn is no longer true."
    ),
}

# The do-not list for the SDR. Fixed rules of the handoff, not model
# prose: this is a future re-approach, not a relitigation and not a
# discount-led save attempt.
_CHURN_DO_NOT = (
    "Do not re-litigate the churn with the customer.",
    "Do not lead with a discount.",
    "Do not contact the departed champion at their old address.",
)


def _closed_lost_renewal_opp(opportunities) -> dict | None:
    """The renewal opportunity that closed lost, if one is on record.
    None when the renewal is still open or no renewal opp exists: a
    churned account with no closed-lost opp is a gap, not a guess."""
    opps = (opportunities or {}).get("opportunities", []) or []
    for opp in opps:
        if str(opp.get("type", "")).lower() != "renewal":
            continue
        if str(opp.get("stage", "")).lower() == "closed lost":
            return opp
    return None


def _account_churned(account_record: dict) -> bool:
    """Whether the account record itself flags the account churned: a
    status of churned, cancelled, or lost, or health churned. A live
    Salesforce record may carry one; the stub and CSV records do not."""
    status = str(account_record.get("status", "") or "").lower()
    if status in ("churned", "cancelled", "canceled", "lost"):
        return True
    return str(account_record.get("health", "") or "").lower() == "churned"


def _churn_reasons(
    job: dict,
    account_record: dict,
    opportunities,
    signals: list[dict],
    records: list,
    missed: list[dict],
    champion_signals: list[dict],
    adoption_list: list[dict],
) -> list[dict]:
    """Why the account churned, harness-computed from evidence only.

    The churn itself must be established first: a closed-lost renewal
    opp, a reason the job names, or an account record flagged churned.
    A high-severity support ticket or a quiet champion on a live
    account is not a churn, so without an anchor no reason is claimed
    at all: the draft says the reason is not on file. With the churn
    anchored, each reason is {"kind", "summary", "evidence"}: a reason
    the job names, the closed-lost renewal opp, a champion who moved
    on or whose mail bounces, a high-severity support failure, missed
    prior commitments, open escalation records, or adoption gaps. A
    quiet champion is not a departure and a promoted champion is not
    a loss, so neither counts here."""
    named = str(job.get("churn_reason", "") or "").strip()
    lost_opp = _closed_lost_renewal_opp(opportunities)
    if not named and lost_opp is None and not _account_churned(
        account_record
    ):
        return []
    reasons: list[dict] = []
    if named:
        reasons.append(
            {
                "kind": "named",
                "summary": named,
                "evidence": "Named in the job: %r" % named,
            }
        )
    if lost_opp is not None:
        amount = lost_opp.get("amount")
        reasons.append(
            {
                "kind": "closed-lost-renewal",
                "summary": (
                    "Renewal %s closed lost (%s, close date %s)"
                    % (
                        lost_opp.get("name", "unnamed"),
                        format_money(amount)
                        if isinstance(amount, (int, float))
                        else "amount not on record",
                        lost_opp.get("close_date") or "no close date",
                    )
                ),
                "evidence": (
                    "Closed-lost renewal opportunity %s in the gathered "
                    "records." % lost_opp.get("name", "unnamed")
                ),
            }
        )
    for signal in champion_signals or []:
        if signal.get("kind") in ("champion-moved-on", "champion-bounce"):
            reasons.append(
                {
                    "kind": "champion-departure",
                    "summary": signal.get("text", ""),
                    "evidence": (
                        "Champion departure signal in the gathered records."
                    ),
                }
            )
    for signal in signals or []:
        if (
            signal.get("severity") == "high"
            and signal.get("kind") == "support-delivery"
        ):
            reasons.append(
                {
                    "kind": "support-failure",
                    "summary": signal.get("text", ""),
                    "evidence": (
                        "High-severity support/delivery signal in the "
                        "gathered records."
                    ),
                }
            )
    if missed:
        reasons.append(
            {
                "kind": "missed-commitments",
                "summary": (
                    "%d prior commitment(s) past due and not closed, "
                    "first: %r" % (len(missed), missed[0].get("text", ""))
                ),
                "evidence": (
                    "Missed prior commitments read out of prior "
                    "call-recap drafts."
                ),
            }
        )
    open_records = [
        record
        for record in records
        if str(getattr(record, "status", "")) in ("open", "escalated")
    ]
    if open_records:
        first = open_records[0]
        reasons.append(
            {
                "kind": "escalation",
                "summary": (
                    "%d escalation record(s) still open, first: %s"
                    % (
                        len(open_records),
                        str(
                            getattr(first, "summary", "")
                            or first.signal_key
                        ),
                    )
                ),
                "evidence": "Escalation record %s (%s) in the escalation "
                "store." % (first.id, first.status),
            }
        )
    if adoption_list:
        reasons.append(
            {
                "kind": "adoption",
                "summary": (
                    "%d adoption gap(s) on record, first: %s"
                    % (len(adoption_list), adoption_list[0].get("item", ""))
                ),
                "evidence": (
                    "Adoption gaps computed from the usage data and the "
                    "account record."
                ),
            }
        )
    return reasons


def _extract_new_company(job: dict, composed_text: str) -> str | None:
    """The company a departed champion moved to, named only when a
    record says so: the job names it, or a prior draft writes "new
    company: <Name>" in the colon form. A sentence that merely
    mentions a company never yields one: the re-approach contact is
    never a guess."""
    named = str(job.get("champion_new_company", "") or "").strip()
    if named:
        return named
    match = re.search(
        r"[Nn]ew company:\s+([A-Z][A-Za-z0-9]+(?: [A-Z][A-Za-z0-9]+)*)",
        composed_text or "",
    )
    if match:
        return match.group(1).strip()
    return None


def _reapproach_triggers(job: dict, new_company: str | None) -> list[str]:
    """The events a re-approach waits for: the departed champion
    landing somewhere new (only when a record names the company), or
    a trigger the job names. Empty when nothing is on file: the plan
    says so instead of inventing a trigger."""
    triggers: list[str] = []
    if new_company:
        triggers.append(
            "Departed champion now at %s (named in the records): the "
            "move is a re-approach trigger." % new_company
        )
    trigger = str(job.get("trigger", "") or "").strip()
    if trigger:
        triggers.append("Named in the job: %s." % trigger)
    return triggers


def _reapproach_timing(job: dict, lost_opp, today=None) -> dict:
    """When to try again, harness-computed, never invented.

    Order: a date the job names, then the replacement contract's
    renewal date when the job names one, then a cooling-off window
    counted from the closed-lost date (90 days unless the job names
    a different window, labeled proposed). None of those is a window:
    kind "none" with the gap stated.
    """
    if today is None:
        from datetime import date as _date

        today = _date.today()
    named = str(job.get("reapproach_date", "") or "").strip()
    if named:
        parsed = _parse_iso_date(named)
        if parsed is not None:
            return {
                "kind": "named",
                "text": "Re-approach on %s." % parsed.isoformat(),
                "evidence": "Date named in the job.",
            }
    replacement = str(
        job.get("replacement_renewal_date", "") or ""
    ).strip()
    if replacement:
        parsed = _parse_iso_date(replacement)
        if parsed is not None:
            return {
                "kind": "replacement-renewal",
                "text": (
                    "Re-approach when the replacement contract renews "
                    "(%s)." % parsed.isoformat()
                ),
                "evidence": "Replacement renewal date named in the job.",
            }
    if lost_opp is not None:
        close = _parse_iso_date(str(lost_opp.get("close_date", "") or ""))
        if close is not None:
            days = job.get("cooling_off_days")
            try:
                days = int(days) if days is not None else 90
            except (TypeError, ValueError):
                days = 90
            if days <= 0:
                days = 90
            from datetime import timedelta as _timedelta

            when = close + _timedelta(days=days)
            return {
                "kind": "cooling-off",
                "text": (
                    "Proposed: re-approach %s (%d days after the "
                    "renewal closed lost on %s). Proposed, not a "
                    "commitment: confirm the window with the account "
                    "owner before the SDR acts on it."
                    % (when.isoformat(), days, close.isoformat())
                ),
                "evidence": (
                    "Cooling-off window counted from the closed-lost "
                    "date %s." % close.isoformat()
                ),
            }
    return {
        "kind": "none",
        "text": "",
        "evidence": (
            "No re-approach window is established: no replacement "
            "renewal date, no closed-lost date to count a cooling-off "
            "window from, and no date named in the job. Name a date, a "
            "replacement renewal, or a trigger to set one."
        ),
    }


def _reapproach_contact(
    job: dict,
    account_record: dict,
    people: list[dict],
    composed_text: str,
    champion_name: str | None,
    new_company: str | None,
) -> dict | None:
    """Who the SDR talks to, named only from evidence: a successor on
    the account, the departed champion at their new company (only
    when a record names the company), or the account owner. None
    means no contact on file: a gap, never a guess."""
    successor = _extract_successor(job, composed_text, people)
    if successor:
        return {
            "name": successor,
            "role": "successor",
            "evidence": (
                "Successor named in the job or a prior draft and on "
                "the account."
            ),
        }
    if champion_name and new_company:
        return {
            "name": champion_name,
            "role": "departed champion, now at %s" % new_company,
            "evidence": (
                "Departed champion's new company named in the records."
            ),
        }
    owner = str(account_record.get("owner", "") or "").strip()
    if owner:
        return {
            "name": owner,
            "role": "account owner",
            "evidence": "Account owner in the account record.",
        }
    return None


def _what_was_lost(
    account_record: dict,
    lost_opp,
    opportunities,
    usage: dict,
    churn_established: bool,
) -> list[str]:
    """What the churn cost, from the records only. ARR is claimed as
    lost only against a closed-lost renewal opp; with no closed-lost
    opp the ARR on record is stated without claiming it lost, and
    with no churn established at all no loss is claimed. Seats come
    from the job's usage block; open expansion opportunities are
    listed as pipeline now unlikely to close."""
    lost: list[str] = []
    arr = account_record.get("arr")
    if lost_opp is not None:
        amount = lost_opp.get("amount")
        if isinstance(amount, (int, float)):
            lost.append(
                "ARR lost: %s (renewal %s closed lost)."
                % (format_money(amount), lost_opp.get("name", "unnamed"))
            )
        elif isinstance(arr, (int, float)):
            lost.append(
                "ARR lost: %s (account ARR; the closed-lost renewal "
                "opp carries no amount)." % format_money(arr)
            )
        else:
            lost.append(
                "ARR lost: amount not on record (renewal %s closed "
                "lost)." % lost_opp.get("name", "unnamed")
            )
    elif churn_established:
        if isinstance(arr, (int, float)):
            lost.append(
                "ARR on record: %s (no closed-lost renewal opp to "
                "confirm the loss against)." % format_money(arr)
            )
        else:
            lost.append("ARR on record: not stated.")
    else:
        if isinstance(arr, (int, float)):
            lost.append(
                "No loss claimed: no churn is established in the "
                "records, so the ARR on record (%s) is not a loss. The "
                "renewal is still open." % format_money(arr)
            )
        else:
            lost.append(
                "No loss claimed: no churn is established in the records."
            )
    seats = usage.get("seats_purchased")
    if churn_established and isinstance(seats, (int, float)) and seats > 0:
        active = usage.get("seats_active")
        if isinstance(active, (int, float)):
            lost.append(
                "Seats: %d purchased, %d active at churn."
                % (seats, active)
            )
        else:
            lost.append("Seats: %d purchased." % seats)
    if not churn_established:
        # A live account's open expansion pipeline is not lost: only
        # list it once the churn is established.
        return lost
    opps = (opportunities or {}).get("opportunities", []) or []
    for opp in opps:
        if "expansion" not in str(opp.get("type", "")).lower():
            continue
        if str(opp.get("stage", "")).lower() in (
            "closed won",
            "closed lost",
        ):
            continue
        amount = opp.get("amount")
        lost.append(
            "Expansion pipeline now unlikely to close: %s (%s)."
            % (
                opp.get("name", "unnamed"),
                format_money(amount)
                if isinstance(amount, (int, float))
                else "amount not on record",
            )
        )
    return lost


def _churn_timeline(
    account_record: dict, records: list, lost_opp
) -> list[str]:
    """The dated events: last touch, the renewal close date, and the
    escalation history. A timeline with nothing dated on it says so."""
    events: list[str] = []
    last_touch = str(account_record.get("last_touch", "") or "").strip()
    if last_touch:
        events.append("Last touch: %s." % last_touch)
    if lost_opp is not None and lost_opp.get("close_date"):
        events.append(
            "Renewal closed lost: %s (%s)."
            % (lost_opp.get("close_date"), lost_opp.get("name", "unnamed"))
        )
    for record in records or []:
        created = str(getattr(record, "created_at", "") or "")
        events.append(
            "Escalation %s: %s (%s, %s)."
            % (
                record.id,
                str(getattr(record, "summary", "") or record.signal_key),
                record.status,
                created[:10] if created else "no date",
            )
        )
    return events


def _churn_post_mortem_block(
    reasons: list[dict],
    timeline: list[str],
    lost: list[str],
    timing: dict,
    triggers: list[str],
    contact: dict | None,
    gaps: list[str],
) -> str:
    """The harness-written churn post-mortem block appended to the
    draft: what happened, the timeline, what was lost, the
    re-approach plan, the do-not list, and the gaps. Harness-written
    so the handoff is in the draft even when the model is the stub."""
    lines = ["Churn reason:"]
    if reasons:
        for reason in reasons:
            lines.append("- [%s] %s" % (reason["kind"], reason["summary"]))
            lines.append("  Evidence: %s" % reason["evidence"])
    else:
        lines.append(
            "- No churn reason is on file: no closed-lost renewal, no "
            "champion departure, no high-severity support failure, no "
            "missed commitments, no open escalations, and no adoption "
            "gaps in the gathered records. Name the reason in the job "
            "(churn_reason) or point the agent at the records that "
            "show it."
        )
    lines.append("")
    lines.append("Timeline:")
    if timeline:
        for event in timeline:
            lines.append("- " + event)
    else:
        lines.append(
            "- No dated events on record: no last touch, no renewal "
            "close date, and no escalation history."
        )
    lines.append("")
    lines.append("What was lost:")
    for line in lost:
        lines.append("- " + line)
    lines.append("")
    lines.append("Re-approach plan:")
    if timing["kind"] == "none":
        lines.append("- When: no re-approach window is established.")
        lines.append("  %s" % timing["evidence"])
    else:
        lines.append("- When: %s" % timing["text"])
        lines.append("  Evidence: %s" % timing["evidence"])
    if contact is not None:
        lines.append("- Who: %s (%s)." % (contact["name"], contact["role"]))
        lines.append("  Evidence: %s" % contact["evidence"])
    else:
        lines.append(
            "- Who: no re-approach contact on file. Name a successor, "
            "the departed champion's new company, or the account owner."
        )
    if triggers:
        lines.append("- Triggers (re-approach when one of these happens):")
        for trigger in triggers:
            lines.append("  - " + trigger)
    else:
        lines.append(
            "- Triggers: none on file. Name a trigger event (a champion "
            "landing somewhere new, a leadership change, the pain that "
            "drove the churn being fixed) to set one."
        )
    if reasons:
        lines.append("- What would have to change:")
        for reason in reasons:
            fix = _CHURN_FIX_BY_KIND.get(reason["kind"])
            if fix:
                lines.append("  - [%s] %s" % (reason["kind"], fix))
    else:
        lines.append(
            "- What would have to change: unknown until the churn "
            "reason is established."
        )
    lines.append("")
    lines.append("Do not:")
    for rule in _CHURN_DO_NOT:
        lines.append("- " + rule)
    lines.append("")
    lines.append("Gaps:")
    if gaps:
        for gap in gaps:
            lines.append("- " + gap)
    else:
        lines.append("- None.")
    return "\n".join(lines)


class ChurnPostMortemAgent(Agent):
    """Documents why a churned account left and hands the record to
    sales for a future re-approach.

    Not a CS win-back: the draft is never a "please come back" save
    attempt. It is a sales handoff: what happened, what was lost,
    and when and how to try again later. Gathers: the account record,
    open opportunities, open cases and tickets, Linear issues, Gmail
    and Slack messages, Gong and Zoom transcripts, and the escalation
    store for this account. Composes from prior risk-watcher,
    escalation-manager, champion-change, trust-repair,
    call-recap-and-commitments, adoption-gaps, renewal-desk, and
    briefer drafts when they exist; gathers directly otherwise.

    The churn reason, the re-approach timing, and the re-approach
    contact are harness-computed, never model prose: a reason is
    claimed only when evidence names it (a closed-lost renewal, a
    champion departure, a high-severity support failure, missed prior
    commitments, open escalations, or adoption gaps), and only once
    the churn itself is established (a closed-lost renewal opp, a
    reason the job names, or an account record flagged churned), so a
    support ticket on a live account never becomes a post-mortem.
    With no evidence the draft says the reason is not on file and
    names what would establish it. The re-approach window comes from
    a date the job names, the replacement contract's renewal, or a
    cooling-off window counted from the closed-lost date (labeled
    proposed); with nothing on file the draft says no window is
    established. The contact is a successor, the departed champion at
    a named new company, or the account owner: no contact on file is
    a gap, never a guess.
    """

    name = "churn-post-mortem"
    display_name = "Churn Post-Mortem"
    description = (
        "Documents why a churned account left and hands the record to "
        "sales for a future re-approach: the churn reason with "
        "evidence, what was lost, and the re-approach plan (when, who, "
        "what would have to change) with a do-not list for the SDR."
    )
    # No bare "churn": renewal-desk owns it, and a bare "churn" job
    # still routes there. The multi-word keywords outscore it where
    # the user means a post-mortem, and "churned" never matches
    # renewal-desk's whole-word "churn".
    keywords = [
        "churn post-mortem",
        "churn postmortem",
        "post-mortem",
        "postmortem",
        "churn handoff",
        "sdr handoff",
        "handoff to sdr",
        "churned account",
        "churned",
        "sdr",
    ]
    proof_checklist = [
        ProofItem(
            "Names the churn reason",
            keywords=["churn reason:"],
        ),
        ProofItem(
            "Gives the timeline",
            keywords=["timeline:"],
        ),
        ProofItem(
            "States what was lost",
            keywords=["what was lost:"],
        ),
        ProofItem(
            "Gives the re-approach plan",
            keywords=["re-approach plan:"],
        ),
        ProofItem(
            "Lists what the SDR must not do",
            keywords=["do not:"],
        ),
        ProofItem(
            "Cites the account record",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        from datetime import date as _date

        account = _extract_account(job)
        today = _date.today()
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _CHURN_POST_MORTEM_SOURCE_AGENTS
        )
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("zendesk_list_tickets", {"organization": account}),
            ("linear_list_issues", {"account": account}),
            ("gmail_search_messages", {"query": account, "limit": 5}),
            ("gong_search_transcripts", {"query": account, "limit": 5}),
            ("zoom_search_transcripts", {"query": account, "limit": 5}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources, links, results = _gather_with_results(
            registry, calls, budget, job=job
        )
        account_record = results.get("salesforce_get_account") or {}
        opportunities = results.get("salesforce_list_opportunities")
        signals = _risk_signals(results, account, today)
        store = _escalation_store(job)
        records = store.for_account(account)
        missed = _missed_prior_commitments(job, account, today)
        memory_text = _memory_context(job, account, budget)
        people = _champion_people(results)
        gaps: list[str] = []
        champion_name, champion_source = _extract_champion(
            job, composed_text, memory_text
        )
        champion: dict | None = None
        if champion_name:
            champion_key = _person_key(champion_name)
            named = next(
                (p for p in people if p["key"] == champion_key), None
            )
            if named is not None and named["side"] == "internal":
                gaps.append(
                    "The named champion %s is on our side (account "
                    "owner or our team), not a customer champion."
                    % champion_name
                )
            else:
                email = ""
                if named is not None and named["emails"]:
                    email = sorted(named["emails"])[0]
                champion = {"name": champion_name, "email": email}
        known_before = champion_source in ("prior draft", "memory")
        champion_signals = (
            _champion_departure_signals(champion, results, known_before)
            if champion is not None
            else []
        )
        usage, _usage_missing = _adoption_usage(job, results)
        adoption_list = _adoption_gaps(usage, account_record)
        lost_opp = _closed_lost_renewal_opp(opportunities)
        reasons = _churn_reasons(
            job,
            account_record,
            opportunities,
            signals,
            records,
            missed,
            champion_signals,
            adoption_list,
        )
        new_company = _extract_new_company(job, composed_text)
        timing = _reapproach_timing(job, lost_opp, today)
        triggers = _reapproach_triggers(job, new_company)
        contact = _reapproach_contact(
            job,
            account_record,
            people,
            composed_text,
            champion_name,
            new_company,
        )
        churn_established = bool(reasons) or _account_churned(
            account_record
        )
        lost = _what_was_lost(
            account_record, lost_opp, opportunities, usage, churn_established
        )
        timeline = _churn_timeline(account_record, records, lost_opp)
        if not reasons:
            gaps.append(
                "No churn reason is on file: name it in the job "
                "(churn_reason), or point the agent at the closed-lost "
                "renewal, the departure, the support failure, or the "
                "missed commitments that show it."
            )
        if timing["kind"] == "none":
            gaps.append(timing["evidence"])
        if contact is None:
            gaps.append(
                "No re-approach contact on file: name a successor, the "
                "departed champion's new company, or the account owner."
            )
        if not timeline:
            gaps.append(
                "No dated events on record: no last touch, no renewal "
                "close date, and no escalation history."
            )
        if champion_name is None:
            gaps.append(
                "No champion named in the job, in any prior draft, or "
                "in memory: the departure story cannot be checked."
            )
        if composed_text:
            context["prior_agent_drafts"] = composed_text
            seen_urls = {link.get("url") for link in links}
            for link in composed_links:
                if link.get("url") not in seen_urls:
                    seen_urls.add(link.get("url"))
                    links.append(link)
            composed = True
        else:
            composed = False
        if memory_text:
            context["memory"] = memory_text
        block = _churn_post_mortem_block(
            reasons,
            timeline,
            lost,
            timing,
            triggers,
            contact,
            gaps,
        )
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a churn post-mortem and SDR handoff for account %r. "
            "This is a sales handoff, not a win-back: never write a "
            "'please come back' save attempt. Cover: what happened, "
            "what was lost, and the re-approach plan. Use only the "
            "context below. Never invent a churn reason, a date, or a "
            "contact the context does not support; say unknown where "
            "the records do not say. The harness appends the "
            "post-mortem block." % account,
        )
        system = (
            "You are the churn post-mortem agent for a customer success "
            "team. You document why an account left and hand the record "
            "to sales for a future re-approach. You never write a "
            "win-back: no 'please come back', no save attempt. Do not "
            "invent a reason, a date, or a contact the context does "
            "not show."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + block
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
    """Who to loop in, from the gathered records: the account owner.
    Only people the records name; nobody is invented."""
    people: list[str] = []
    account = results.get("salesforce_get_account") or {}
    owner = account.get("owner")
    if owner:
        people.append("%s (account owner, Salesforce)" % owner)
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
    addressee = "team"
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
            "- No account owner named in the gathered records; find "
            "the sponsor before sending this."
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
    recent email threads,
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


# The three agents whose drafts case-triage composes from (v1.10.0):
# risk-watcher (its brief already covers the high-severity records),
# escalation-manager (its plans already own the escalated signals),
# voice-of-customer (its themes already cover the pain points).
# The queue is the only channel between agents.
_TRIAGE_SOURCE_AGENTS = (
    "risk-watcher",
    "escalation-manager",
    "voice-of-customer",
)

# Priority rank: lower sorts first, so P1 items head the brief.
_TRIAGE_PRIORITY_RANK = {"P1": 0, "P2": 1, "P3": 2}

# An item is stale when it has sat open this long with nothing actively
# being worked on it.
_TRIAGE_STALE_DAYS = 14

# Words in a Gmail excerpt that make the thread a support request, not
# a sales or evaluation thread. An evaluation ask ("send a comparison")
# is competitive-defense's job, not triage's.
_TRIAGE_SUPPORT_WORDS = (
    "fails",
    "failing",
    "timeout",
    "error",
    "broken",
    "not working",
    "blocking",
    "still waiting",
    "fix",
)

# Words in a record's own text that mark it as blocking the customer:
# a blocked renewal or rollout is one priority higher than the record's
# own severity says. The words come from the record itself, never the
# model.
_TRIAGE_BLOCKING_WORDS = ("blocking", "blocks", "block the")

# Words too generic to join a Gmail thread to a case group: the account
# name and the filler around it. "Re:" never survives _norm_subject
# anyway (it is punctuation), but it is listed so the intent is plain.
_TRIAGE_STOPWORDS = {
    "the",
    "for",
    "and",
    "with",
    "from",
    "before",
    "acme",
    "corp",
}


def _triage_priority(priority, age_days, text="") -> str:
    """Priority of one open record, harness-computed.

    High or urgent is P1. A medium record aged seven days or more is
    P1 too: it has waited long enough. Medium is P2, low aged fourteen
    days or more is P2, everything else is P3. A record whose own text
    says it is blocking the customer (a renewal, a rollout) moves one
    level up. Zendesk calls its middle priority "normal"; it counts as
    medium here, the same as _record_severity."""
    priority = str(priority or "").lower()
    try:
        age = int(age_days) if age_days is not None else 0
    except (TypeError, ValueError):
        age = 0
    if priority in ("urgent", "high"):
        level = "P1"
    elif priority in ("medium", "normal") and age >= 7:
        level = "P1"
    elif priority in ("medium", "normal"):
        level = "P2"
    elif priority == "low" and age >= _TRIAGE_STALE_DAYS:
        level = "P2"
    else:
        level = "P3"
    lowered = str(text or "").lower()
    if level != "P1" and any(
        word in lowered for word in _TRIAGE_BLOCKING_WORDS
    ):
        level = "P1" if level == "P2" else "P2"
    return level


def _triage_distinctive_words(subject: str) -> set:
    """The words that identify a subject: length three or more, minus
    the stopwords. Two subjects that share two of these are the same
    issue in different words, which is how a Gmail thread joins the
    case group it is about."""
    words = set()
    for word in re.split(r"\W+", str(subject or "").lower()):
        if len(word) >= 3 and word not in _TRIAGE_STOPWORDS:
            words.add(word)
    return words


def _triage_entries(
    results: dict, account: str, prior_items: list | None = None
) -> list[dict]:
    """The open items for an account, grouped and priority-ranked.

    Deterministic, harness-computed: the model never decides what
    counts as an item or how urgent it is. Salesforce cases, Zendesk
    tickets, and Linear issues group by normalized subject (the v1.6.0
    pattern): the same SSO failure in three systems is one entry, not
    three. A Gmail thread that reads like a support request joins the
    group it shares two distinctive words with, or becomes its own
    entry when it matches none. Closed, solved, done, and canceled
    records are not items: triage is about what is still open.

    Each entry carries its priority (worst of its records, oldest
    first within a priority), the systems it appears in, a suggested
    owner (the Linear assignee when one exists, else the account
    owner, else unassigned), and its flags: duplicated across
    systems, stale, or already covered by a prior agent's draft (the
    full draft text is searched for the subject, so a flag means the
    other agent really wrote about this issue). Entries sort P1
    first, then by age, oldest first.
    """
    collected: list[tuple[dict, str, str, str, str]] = []
    cases = results.get("salesforce_list_cases") or {}
    for case in cases.get("cases", []) or []:
        if str(case.get("status", "")).lower() in ("closed", "solved"):
            continue
        collected.append(
            (
                case,
                "Salesforce case %s"
                % case.get("number", case.get("id", "?")),
                str(case.get("subject", "")),
                "Salesforce",
                str(case.get("description", "") or ""),
            )
        )
    tickets = results.get("zendesk_list_tickets") or {}
    for ticket in tickets.get("tickets", []) or []:
        if str(ticket.get("status", "")).lower() not in ("open", "pending"):
            continue
        collected.append(
            (
                ticket,
                "Zendesk ticket %s" % ticket.get("id", "?"),
                str(ticket.get("subject", "")),
                "Zendesk",
                str(ticket.get("description", "") or ""),
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
        collected.append(
            (
                issue,
                "Linear issue %s"
                % issue.get("identifier", issue.get("id", "?")),
                str(issue.get("title", "")),
                "Linear",
                str(issue.get("description", "") or ""),
            )
        )
    groups: dict[str, dict] = {}
    for record, label, subject, system, text in collected:
        key = _norm_subject(subject)
        if not key:
            continue
        group = groups.setdefault(
            key,
            {
                "subject": subject,
                "parts": [],
                "records": [],
                "words": _triage_distinctive_words(subject),
            },
        )
        group["records"].append(record)
        group["parts"].append(
            "%s (%s, %s day(s) open)"
            % (
                label,
                str(record.get("priority", "?")).lower(),
                record.get("age_days", "?"),
            )
        )
    # Gmail threads that read like support requests: join the group
    # they are about, or stand alone when they match none. An
    # evaluation ask is not a support request and is skipped here.
    messages = results.get("gmail_search_messages") or {}
    for match in messages.get("matches", []) or []:
        subject = str(match.get("subject", "") or "")
        excerpt = str(match.get("excerpt", "") or "")
        lowered = (subject + " " + excerpt).lower()
        if not any(word in lowered for word in _TRIAGE_SUPPORT_WORDS):
            continue
        words = _triage_distinctive_words(subject)
        best_key = None
        best_overlap = 0
        for key, group in groups.items():
            overlap = len(words & group["words"])
            if overlap > best_overlap:
                best_overlap = overlap
                best_key = key
        if best_key is not None and best_overlap >= 2:
            group = groups[best_key]
            thread_id = str(match.get("thread_id", match.get("id", "?")))
            if thread_id not in group.setdefault("gmail_threads", set()):
                group["gmail_threads"].add(thread_id)
                group["records"].append(match)
                group["parts"].append(
                    "Gmail thread %s (support request)" % thread_id
                )
        else:
            key = "gmail:" + str(match.get("thread_id", match.get("id", "?")))
            groups[key] = {
                "subject": subject,
                "parts": [
                    "Gmail thread %s (support request)"
                    % match.get("thread_id", match.get("id", "?"))
                ],
                "records": [match],
                "words": words,
                "gmail_threads": {
                    str(match.get("thread_id", match.get("id", "?")))
                },
            }
    account_record = results.get("salesforce_get_account") or {}
    account_owner = str(account_record.get("owner", "") or "")
    prior_texts = []
    for item in prior_items or []:
        prior_texts.append((item.agent, str(item.draft or "").lower()))
    entries: list[dict] = []
    for key, group in groups.items():
        records = group["records"]
        priority = "P3"
        max_age = 0
        being_worked = False
        has_linear = False
        owner = ""
        for record in records:
            text = " ".join(
                str(record.get(field, "") or "")
                for field in ("subject", "title", "description", "excerpt")
            )
            level = _triage_priority(
                record.get("priority"), record.get("age_days"), text
            )
            if _TRIAGE_PRIORITY_RANK[level] < _TRIAGE_PRIORITY_RANK[priority]:
                priority = level
            try:
                age = int(record.get("age_days") or 0)
            except (TypeError, ValueError):
                age = 0
            max_age = max(max_age, age)
            if str(record.get("status", "")).lower() in (
                "working",
                "in progress",
            ):
                being_worked = True
            if record.get("identifier"):
                # A Linear issue (identifier ENG-###) is in this group,
                # assigned or not: the engineering issue already exists,
                # so the next step is to pick it up, not create it.
                has_linear = True
            if record.get("assignee"):
                if not owner:
                    owner = str(record["assignee"])
        if not owner:
            owner = account_owner or "unassigned — needs an owner"
        systems = sorted(
            {
                part.split(" ")[0]
                for part in group["parts"]
            }
        )
        covered_by = None
        subject_key = _norm_subject(group["subject"])
        if subject_key:
            for agent_name, draft_text in prior_texts:
                if subject_key in draft_text:
                    covered_by = agent_name
                    break
        entries.append(
            {
                "key": key,
                "subject": group["subject"],
                "priority": priority,
                "max_age": max_age,
                "parts": group["parts"],
                "systems": systems,
                "owner": owner,
                "being_worked": being_worked,
                "has_linear": has_linear,
                "duplicated": len(systems) >= 2,
                "stale": max_age >= _TRIAGE_STALE_DAYS and not being_worked,
                "covered_by": covered_by,
            }
        )
    entries.sort(
        key=lambda e: (_TRIAGE_PRIORITY_RANK[e["priority"]], -e["max_age"])
    )
    return entries


def _triage_first_response(entry: dict) -> str:
    """The draft first response for one entry, harness-written.

    Being worked on: say so and promise the next update when there is
    progress to report. Queued: say who has it and promise the plan
    once work starts. No dates are promised: the records carry none."""
    if entry["being_worked"]:
        return (
            "Thanks for raising this. %r is being worked on now by %s "
            "— we'll share the next update as soon as there's progress "
            "to report." % (entry["subject"], entry["owner"])
        )
    return (
        "Thanks for raising this. %r is queued with %s — we'll confirm "
        "the plan and share the next update once work starts."
        % (entry["subject"], entry["owner"])
    )


def _triage_internal_summary(entry: dict) -> str:
    """The draft internal summary for one entry, harness-written: what
    it is, how long it has been open, who owns it, and the next step."""
    if entry["being_worked"]:
        next_step = "ship the fix"
    elif entry["has_linear"]:
        next_step = "pick up and scope the engineering issue"
    else:
        next_step = "create the engineering issue and assign an owner"
    return (
        "%s: %r — open %d day(s) across %s. Owner: %s. Next: %s."
        % (
            entry["priority"],
            entry["subject"],
            entry["max_age"],
            ", ".join(entry["systems"]),
            entry["owner"],
            next_step,
        )
    )


def _triage_block(entries: list[dict], account: str) -> str:
    """The harness-computed triage block appended to the draft:
    overall priority, the priority-ranked entries, and for each the
    evidence, the suggested owner, the flags, and the draft first
    response and internal summary. An entry already covered by
    another agent's draft is flagged, not re-triaged: no new first
    response is drafted for it. No entries means a clean bill,
    stated as such, never an invented item."""
    if not entries:
        return (
            "Triage (harness-computed): No open cases, tickets, or "
            "issues for %s. Overall priority: none. No suggested owner "
            "is needed and no first response to draft: there is nothing "
            "open to own or answer." % account
        )
    lines = [
        "Triage for %s — priority-ranked (%d open item(s), grouped "
        "from the gathered records):" % (account, len(entries)),
        "Overall priority: %s." % entries[0]["priority"],
    ]
    for entry in entries:
        line = (
            "%s — %r (open %d day(s), %s). Suggested owner: %s."
            % (
                entry["priority"],
                entry["subject"],
                entry["max_age"],
                "; ".join(entry["parts"]),
                entry["owner"],
            )
        )
        flags = []
        if entry["duplicated"]:
            flags.append(
                "tracked in %d systems — consolidate"
                % len(entry["systems"])
            )
        if entry["stale"]:
            flags.append(
                "stale: open %d day(s) with no progress" % entry["max_age"]
            )
        if entry["covered_by"]:
            flags.append(
                "already covered in the %s draft — no new first "
                "response drafted" % entry["covered_by"]
            )
        if flags:
            line += " Flags: %s." % "; ".join(flags)
        if entry["covered_by"]:
            line += " First response: none drafted (already covered)."
        else:
            line += " First response: %s" % _triage_first_response(entry)
        line += " Internal summary: %s" % _triage_internal_summary(entry)
        lines.append("- " + line)
    return "\n".join(lines)


class CaseTriageAgent(Agent):
    """Triages the incoming cases, tickets, and issues on an account
    and drafts a triage brief.

    Gathers: account summary, open Salesforce cases, Zendesk tickets,
    Linear issues, and recent Gmail threads. Drafts: a triage brief
    with the open items grouped by subject across systems, ranked by
    priority, each with a suggested owner and a draft first response
    or internal summary.

    Composition (v1.1.0): when prior drafts from risk-watcher,
    escalation-manager, and voice-of-customer exist for the account,
    the brief is built from those drafts as well as the fresh gather:
    an item the other agents already covered is flagged, not
    re-triaged, and the draft carries a harness-written provenance
    line naming the agents it was composed from. The items themselves
    are always gathered fresh: they are the job, not the context.
    When no prior drafts exist, the agent gathers directly, its
    previous behavior.

    Priority is harness-computed, never model prose: high or urgent
    is P1, a medium record aged seven days or more is P1, medium is
    P2, low aged fourteen days or more is P2, and a record whose own
    text says it is blocking the customer moves one level up. With no
    open items at all the draft is a clean bill, stated as such.
    """

    name = "case-triage"
    display_name = "Case Triage"
    description = (
        "Triages incoming cases, tickets, and issues: priority, owner, "
        "first response. Drafts a triage brief."
    )
    keywords = [
        "case-triage",
        "triage",
        "ticket triage",
        "triage cases",
    ]
    proof_checklist = [
        ProofItem(
            "Names the account",
            sources=["salesforce_get_account"],
        ),
        ProofItem(
            "States the overall priority",
            keywords=["overall priority"],
        ),
        ProofItem(
            "Gives a suggested owner per item",
            keywords=["suggested owner"],
        ),
        ProofItem(
            "Drafts a first response or internal summary",
            keywords=["first response"],
        ),
        ProofItem(
            "Draft carries source links",
            requires_links=True,
        ),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        composed_text, composed_links, composed_agents = _compose_sources(
            job, account, _TRIAGE_SOURCE_AGENTS
        )
        # The items are the job, so they are always gathered fresh,
        # composed or not. What composition adds is the coverage check
        # (an item another agent already covered is flagged, not
        # re-triaged) and the provenance line.
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("zendesk_list_tickets", {"organization": account}),
            ("linear_list_issues", {}),
            ("gmail_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources, links, results = _gather_with_results(
            registry, calls, budget, job=job
        )
        composed = bool(composed_text)
        if composed:
            context["prior_agent_drafts"] = composed_text
            seen_urls = {link.get("url") for link in links}
            for link in composed_links:
                if link.get("url") not in seen_urls:
                    seen_urls.add(link.get("url"))
                    links.append(link)
        memory_text = _memory_context(job, account, budget)
        if memory_text:
            context["memory"] = memory_text
        prior_items = _load_prior_items(job, account, _TRIAGE_SOURCE_AGENTS)
        entries = _triage_entries(results, account, prior_items)
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a triage brief for account %r. Cover: the open "
            "cases, tickets, and issues in the context ranked by "
            "priority (P1 first), the evidence for each item with its "
            "source, a suggested owner per item, and a draft first "
            "response or internal summary per item. Flag items that "
            "are stale, duplicated across systems, or already covered "
            "by another agent's draft. Do not invent an item that is "
            "not in the context." % account,
        )
        system = (
            "You are the case triage agent for a customer success "
            "team. You triage what is already open before anything "
            "new is promised. Rank items by priority, name the owner, "
            "and draft the first response. Never invent a case, a "
            "ticket, or an issue that is not in the context."
        )
        text = llm.complete(prompt, system=system)
        text = text + "\n\n" + _triage_block(entries, account)
        checklist = list(self.proof_checklist)
        if not entries:
            # A clean bill has no items to own or answer, so the
            # per-item checklist items do not apply; the clean bill
            # itself is the check.
            checklist = [
                item
                for item in checklist
                if item.label
                not in (
                    "Gives a suggested owner per item",
                    "Drafts a first response or internal summary",
                )
            ]
            checklist += [
                ProofItem(
                    "States the clean bill",
                    keywords=["no open cases"],
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
    """All twenty-two agents, keyed by name. All implemented."""
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
        "case-triage": CaseTriageAgent(),
        "champion-change": ChampionChangeAgent(),
        "handoff": HandoffAgent(),
        "adoption-gaps": AdoptionGapsAgent(),
        "call-recap-and-commitments": CallRecapAndCommitmentsAgent(),
        "trust-repair": TrustRepairAgent(),
        "ebr-prep": EBRPrepAgent(),
        "churn-post-mortem": ChurnPostMortemAgent(),
    }
    return agents


AGENTS: dict[str, Agent] = build_agents()
