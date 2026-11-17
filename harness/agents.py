"""The specialist agents: the "8" in "1 router + 8 agents".

The eight jobs come from Kevin Lau's "Claude Customer Success Engine" post
(Nov 9, 2026). Justin's verdict that day: the 8 jobs are the right 8 jobs.
They are, in Lau's terms: scout, briefer, onboarding guide, expansion
spotter, QBR builder, advocate finder, renewal desk, voice-of-customer.

V1 implements two end to end:
- renewal-desk: the highest-stakes job. Gathers account health, open
  renewal opportunity, open cases, and recent Slack signals, then drafts a
  renewal plan. Ties directly to the Renewal Scorecard on jfournier.net.
- briefer: the most common daily job. Gathers an account snapshot and recent
  activity, then drafts a pre-meeting brief.

The other six are registered as planned: they have a name, description, and
keywords so the router knows about them, but implemented=False, so the
router never sends them work. A job that explicitly names one gets a clear
"planned, not implemented yet" result. Build them in the same shape as the
two below: gather() pulls context from the tool registry, then the LLM
turns that context into a draft.

Every agent follows the same contract:
1. gather() calls tools through the registry (summary-first results only).
2. The prompt is built from the gathered context, never from raw records.
3. The LLM drafts; the draft goes to the approval queue, never out directly.
4. The Draft records which tools were called, so a human can check sources.
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


class Agent:
    """One specialist agent."""

    name: str = ""
    display_name: str = ""
    description: str = ""
    keywords: list[str] = []
    implemented: bool = True

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
        return Draft(
            text=text,
            agent=self.name,
            job=job,
            sources=sources,
            tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
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
        return Draft(
            text=text,
            agent=self.name,
            job=job,
            sources=sources,
            tokens_estimate=estimate_tokens(prompt) + estimate_tokens(text),
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
    """All eight agents, keyed by name. Two implemented, six planned."""
    agents: dict[str, Agent] = {
        "renewal-desk": RenewalDeskAgent(),
        "briefer": BrieferAgent(),
        "scout": PlannedAgent(
            "scout",
            "Scout",
            "Finds accounts showing expansion or risk signals.",
            ["scout", "signal", "signals", "watch", "monitor", "flag"],
        ),
        "onboarding-guide": PlannedAgent(
            "onboarding-guide",
            "Onboarding Guide",
            "Drafts onboarding plans and first-value checklists.",
            ["onboard", "onboarding", "first value", "kickoff", "go-live", "go live"],
        ),
        "expansion-spotter": PlannedAgent(
            "expansion-spotter",
            "Expansion Spotter",
            "Finds expansion openings: usage gaps, new teams, new use cases.",
            ["expansion", "expand", "upsell", "growth", "land and expand"],
        ),
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
