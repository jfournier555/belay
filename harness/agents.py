"""The specialist agents: the ten post-sales jobs the harness covers.

The ten jobs are the post-sales jobs that matter: scout, briefer,
onboarding guide, expansion spotter, QBR builder, advocate finder,
renewal desk, voice-of-customer, value-finder, and salesforce-updater.

V1 implements ten end to end:
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

All ten are implemented. Every job in the harness has a working agent.

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
from .llm import LLMClient
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


def _gather_with_results(
    registry: ToolRegistry, calls: list[tuple[str, dict]], budget: TokenBudget
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
    for tool_name, kwargs in calls:
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
    registry: ToolRegistry, calls: list[tuple[str, dict]], budget: TokenBudget
) -> tuple[dict, list[str], list[dict]]:
    """Call tools in order, respecting the token budget. Returns
    (context dict, list of tool names successfully called, list of
    source links built from the tool results). A tool that would blow
    the budget is skipped, and the skip is recorded in context. Links
    are collected only from calls that succeeded within budget, and
    deduplicated by URL.
    """
    context, sources, links, _results = _gather_with_results(
        registry, calls, budget
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
        context, sources, links = _gather(registry, calls, budget)
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
        context, sources, links = _gather(registry, calls, budget)
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
        context, sources, links = _gather(registry, calls, budget)
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
        context, sources, links = _gather(registry, calls, budget)
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
        context, sources, links = _gather(registry, calls, budget)
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
        context, sources, links = _gather(registry, calls, budget)
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
        context, sources, links = _gather(registry, calls, budget)
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
        context, sources, links = _gather(registry, calls, budget)
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
            registry, calls, budget
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
            registry, calls, budget
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


def build_agents() -> dict[str, Agent]:
    """All ten agents, keyed by name. All implemented."""
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
    }
    return agents


AGENTS: dict[str, Agent] = build_agents()
