"""The specialist agents: the eight post-sales jobs the harness covers.

The eight jobs are the post-sales jobs that matter: scout, briefer,
onboarding guide, expansion spotter, QBR builder, advocate finder,
renewal desk, voice-of-customer.

V1 implements eight end to end:
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
  most, who praises it in Slack, who would make a good reference.
- voice-of-customer: collects and synthesizes customer feedback: feature
  requests, pain points, sentiment from cases, Slack, Zendesk tickets,
  and Gong call transcripts, and the top themes to take back to the
  product team.

All eight are implemented. Every job in the harness has a working agent.

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

from dataclasses import dataclass, field

from .llm import LLMClient
from .registry import ToolRegistry
from .summarizer import TokenBudget, estimate_tokens


@dataclass
class Draft:
    """What an agent produced. Always goes to the approval queue next."""

    text: str
    agent: str
    job: dict
    sources: list[str] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    tokens_estimate: int = 0
    proof: list[dict] = field(default_factory=list)

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
    draft text (case-insensitive); `sources` must be among the tools the
    agent successfully called; `requires_links` means the draft must
    carry at least one source link. An item with none of these is a
    manual check the human reviewer confirms by reading the draft.
    """

    label: str
    keywords: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    requires_links: bool = False

    def check(self, draft: Draft) -> dict:
        """Check one item against a draft. Returns a result dict."""
        missing: list[str] = []
        text = draft.text.lower()
        for keyword in self.keywords:
            if keyword.lower() not in text:
                missing.append("draft does not mention %r" % keyword)
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
    return links


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
    context: dict = {}
    sources: list[str] = []
    links: list[dict] = []
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
        for link in extract_links(tool_name, result):
            if link["url"] not in seen_urls:
                seen_urls.add(link["url"])
                links.append(link)
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
    recent Slack signals, and recent email threads. Drafts: account health,
    risks, next steps, and a message to the customer. The CRM it gathers
    from is the registry's `crm`: Salesforce by default, HubSpot when
    HubSpot is configured and Salesforce is not. The proof checklist
    follows the CRM.
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
        context, sources, links = _gather(registry, calls, budget)
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a renewal plan for account %r. Cover: account health, "
            "the open renewal opportunity, open support cases, any risk "
            "signals from Slack and email, and the next three steps with "
            "owners." % account,
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

    Gathers: account summary, open opportunities, open support cases, and
    recent Slack signals. Drafts: outcomes delivered this quarter, adoption
    and value realized, open risks and support cases, and the plan for next
    quarter. This is the agent that turns the account picture into the QBR
    deck narrative: what was delivered, what it is worth, what is at risk,
    and what happens next.
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
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources, links = _gather(registry, calls, budget)
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
    signals. Drafts: who uses the product most, who praises it in Slack,
    who would make a good reference, and any champion already known from
    the account data. For each champion it assesses influence from
    observable signals (who listens to them, defended spend, budget
    control), never assumed, and flags whether a known champion has gone
    quiet or left, naming the successor and what they care about if one
    is known.
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
        context, sources, links = _gather(registry, calls, budget)
        prompt = _build_prompt(
            self.name,
            context,
            "Find advocates in account %r. Cover: who uses the product "
            "most, who praises it in Slack, who would make a good "
            "reference, any champion already known from the account "
            "data, how much influence each champion carries (who listens "
            "to them, have they defended spend, do they control budget), "
            "and whether a known champion has gone quiet or left, naming "
            "the successor and what they care about if one is known."
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
        context, sources, links = _gather(registry, calls, budget)
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


def build_agents() -> dict[str, Agent]:
    """All eight agents, keyed by name. All implemented."""
    agents: dict[str, Agent] = {
        "renewal-desk": RenewalDeskAgent(),
        "briefer": BrieferAgent(),
        "expansion-spotter": ExpansionSpotterAgent(),
        "onboarding-guide": OnboardingGuideAgent(),
        "scout": ScoutAgent(),
        "qbr-builder": QBRBuilderAgent(),
        "advocate-finder": AdvocateFinderAgent(),
        "voice-of-customer": VoiceOfCustomerAgent(),
    }
    return agents


AGENTS: dict[str, Agent] = build_agents()
