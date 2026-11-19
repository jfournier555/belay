"""The specialist agents: the eight post-sales jobs the harness covers.

The eight jobs are the post-sales jobs that matter: scout, briefer,
onboarding guide, expansion spotter, QBR builder, advocate finder,
renewal desk, voice-of-customer.

V1 implements five end to end:
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

The other three are registered as planned: they have a name, description, and
keywords so the router knows about them, but implemented=False, so the
router never sends them work. A job that explicitly names one gets a clear
"planned, not implemented yet" result. Build them in the same shape as the
five below: gather() pulls context from the tool registry, then the LLM
turns that context into a draft.

Every agent follows the same contract:
1. gather() calls tools through the registry (summary-first results only).
2. The prompt is built from the gathered context, never from raw records.
3. The LLM drafts; the draft goes to the approval queue, never out directly.
4. The Draft records which tools were called, so a human can check sources.
5. Before the draft reaches the queue, the agent self-checks it against
   its proof checklist (what "done" looks like). A failed item is flagged
   in the draft metadata for the human reviewer, never a block: the draft
   still goes to the queue either way. Human approval remains the final
   gate.
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
    tokens_estimate: int = 0
    proof: list[dict] = field(default_factory=list)

    @property
    def proof_passed(self) -> bool:
        """True when every proof item passed (or there is no checklist)."""
        return all(item.get("passed", True) for item in self.proof)


@dataclass
class ProofItem:
    """One item on an agent's proof checklist: what "done" looks like.

    Each item names the evidence it needs. `keywords` must appear in the
    draft text (case-insensitive); `sources` must be among the tools the
    agent successfully called. An item with neither is a manual check the
    human reviewer confirms by reading the draft.
    """

    label: str
    keywords: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

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

    def _finish(self, draft: Draft) -> Draft:
        """Run the proof checklist and attach the results to the draft.
        The draft goes to the approval queue either way; a failed item is
        a flag for the human reviewer, not a block."""
        draft.proof = check_proof(self.proof_checklist, draft)
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
        "say so instead of guessing."
    )
    lines.append("")
    lines.append("Context:")
    for key, value in context.items():
        lines.append("- %s: %s" % (key, value))
    return "\n".join(lines)


def _gather(
    registry: ToolRegistry, calls: list[tuple[str, dict]], budget: TokenBudget
) -> tuple[dict, list[str]]:
    """Call tools in order, respecting the token budget. Returns
    (context dict, list of tool names successfully called). A tool that
    would blow the budget is skipped, and the skip is recorded in context.
    """
    context: dict = {}
    sources: list[str] = []
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
    return context, sources


def _render_result(result: dict) -> str:
    """Render a summary-form tool result as compact readable text."""
    parts = []
    for key, value in result.items():
        parts.append("%s: %s" % (key, value))
    return "; ".join(parts)


class RenewalDeskAgent(Agent):
    """Drafts a renewal plan for an account.

    Gathers: account summary, open renewal opportunity, open support cases,
    and recent Slack signals. Drafts: account health, risks, next steps, and
    a message to the customer.
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
        ProofItem("Covers account health", keywords=["health"]),
        ProofItem("Lists next steps", keywords=["step"]),
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources = _gather(registry, calls, budget)
        prompt = _build_prompt(
            self.name,
            context,
            "Draft a renewal plan for account %r. Cover: account health, "
            "the open renewal opportunity, open support cases, any risk "
            "signals from Slack, and the next three steps with owners." % account,
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
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            )
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
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources = _gather(registry, calls, budget)
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
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources = _gather(registry, calls, budget)
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
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources = _gather(registry, calls, budget)
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
    ]

    def run(self, job, registry, llm, budget):
        account = _extract_account(job)
        calls = [
            ("salesforce_get_account", {"account": account}),
            ("salesforce_list_opportunities", {"account": account}),
            ("salesforce_list_cases", {"account": account}),
            ("slack_search_messages", {"query": account, "limit": 5}),
        ]
        context, sources = _gather(registry, calls, budget)
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
                tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
            )
        )


class PlannedAgent(Agent):
    """A registered agent that is not implemented yet.

    Has a name, description, and keywords so the router knows about it,
    but implemented=False, so it never receives work. Build it by
    subclassing Agent and setting implemented=True, in the shape of
    RenewalDeskAgent above.
    """

    implemented = False

    def __init__(self, name, display_name, description, keywords):
        self.name = name
        self.display_name = display_name
        self.description = description
        self.keywords = keywords

    def run(self, job, registry, llm, budget):
        raise NotImplementedError(
            "Agent %r is planned for a later release." % self.name
        )


def build_agents() -> dict[str, Agent]:
    """All eight agents, keyed by name. Five implemented, three planned."""
    agents: dict[str, Agent] = {
        "renewal-desk": RenewalDeskAgent(),
        "briefer": BrieferAgent(),
        "expansion-spotter": ExpansionSpotterAgent(),
        "onboarding-guide": OnboardingGuideAgent(),
        "scout": ScoutAgent(),
        "qbr-builder": PlannedAgent(
            "qbr-builder",
            "QBR Builder",
            "Drafts a quarterly business review from account data.",
            ["qbr", "business review", "quarterly", "ebr", "executive review"],
        ),
        "advocate-finder": PlannedAgent(
            "advocate-finder",
            "Advocate Finder",
            "Finds happy customers who could advocate, reference, or review.",
            ["advocate", "reference", "testimonial", "case study", "champion"],
        ),
        "voice-of-customer": PlannedAgent(
            "voice-of-customer",
            "Voice of Customer",
            "Summarizes what customers are saying across Slack, cases, and calls.",
            ["voice of customer", "voc", "feedback", "sentiment", "complaints"],
        ),
    }
    return agents


AGENTS: dict[str, Agent] = build_agents()
