"""Router: pick the right specialist agent for a job, cheaply.

The router is the single entry point for the harness: one router, fourteen
specialist agents. Its job is to be cheap: it should cost almost nothing
to decide who does the work, so the tokens go to the work itself.

Routing order, cheapest first:
1. Explicit: the job names an agent. Use it. (No model call at all.)
2. Keyword: score each implemented agent's keywords against the job text.
   A clear winner routes immediately. (No model call.)
3. Small-model LLM: only when the keywords are ambiguous or match nothing.
   The prompt is tiny (agent list + job text), and it runs on the router
   model, which is meant to be the smallest/cheapest model configured.
4. Default: when there is no LLM and no keyword match, route to the
   briefer, the safest generalist, and say so.

The router only routes to implemented agents. Planned agents (see agents.py)
are never a routing target; a job that explicitly names one gets a clear
"not implemented yet" result instead of a silent misroute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .agents import AGENTS, Agent
from .llm import LLMClient


@dataclass
class RouteResult:
    """What the router decided, and why."""

    agent: str | None
    confidence: str  # high | medium | low
    reason: str
    error: str | None = None


def _keyword_score(text: str, keywords: list[str]) -> int:
    """Count keyword hits in the job text. A multi-word keyword counts once
    per occurrence; matching is case-insensitive, whole-word."""
    lowered = text.lower()
    score = 0
    for kw in keywords:
        pattern = r"\b" + re.escape(kw.lower()) + r"\b"
        score += len(re.findall(pattern, lowered))
    return score


class Router:
    """Routes jobs to the right specialist agent."""

    def __init__(
        self,
        agents: dict[str, Agent] | None = None,
        llm: LLMClient | None = None,
        router_model: str | None = None,
        default_agent: str = "briefer",
    ) -> None:
        self.agents = agents if agents is not None else AGENTS
        self.llm = llm
        self.router_model = router_model
        self.default_agent = default_agent

    def _implemented(self) -> dict[str, Agent]:
        return {n: a for n, a in self.agents.items() if a.implemented}

    def route(self, job: dict) -> RouteResult:
        """Pick an agent for `job`.

        `job` is a dict with at least a "text" key (free-text description),
        and optionally an "agent" key naming the agent explicitly.
        """
        implemented = self._implemented()
        text = str(job.get("text", "") or "")
        if job.get("account"):
            text = text + " " + str(job["account"])

        # 1. Explicit agent.
        requested = job.get("agent")
        if requested:
            if requested in implemented:
                return RouteResult(
                    agent=requested,
                    confidence="high",
                    reason="Job named agent %r explicitly." % requested,
                )
            if requested in self.agents:
                return RouteResult(
                    agent=None,
                    confidence="high",
                    reason="Agent %r is planned, not implemented yet." % requested,
                    error="Agent %r is planned for a later release." % requested,
                )
            return RouteResult(
                agent=None,
                confidence="high",
                reason="Unknown agent %r." % requested,
                error="Unknown agent: %r" % requested,
            )

        # 2. Keyword scoring over implemented agents.
        scores = {
            name: _keyword_score(text, agent.keywords)
            for name, agent in implemented.items()
        }
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        if ranked and ranked[0][1] >= 1:
            # A clear winner needs at least 1 hit and a strict lead over
            # the runner-up. A tie means the keywords are ambiguous, so
            # fall through to the LLM (or the default).
            if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
                return RouteResult(
                    agent=ranked[0][0],
                    confidence="medium",
                    reason="Keyword match: %r scored %d."
                    % (ranked[0][0], ranked[0][1]),
                )

        # 3. Small-model LLM, only when keywords are ambiguous.
        if self.llm is not None:
            picked = self._route_with_llm(text, implemented)
            if picked:
                return RouteResult(
                    agent=picked,
                    confidence="medium",
                    reason="Router model picked %r." % picked,
                )

        # 4. Default: the briefer, the safest generalist.
        if self.default_agent in implemented:
            return RouteResult(
                agent=self.default_agent,
                confidence="low",
                reason="No clear match; defaulted to %r." % self.default_agent,
            )
        return RouteResult(
            agent=None,
            confidence="low",
            reason="No agent matched and no default is available.",
            error="No agent matched this job.",
        )

    def _route_with_llm(
        self, text: str, implemented: dict[str, Agent]
    ) -> str | None:
        """Ask the router model to pick an agent. Returns the agent name,
        or None when the model is unavailable or its answer is unusable."""
        if self.llm is None:
            return None
        agent_list = "\n".join(
            "- %s: %s" % (name, agent.description)
            for name, agent in implemented.items()
        )
        prompt = (
            "You are a router. Pick exactly one agent for this job. "
            "Reply with only the agent name, nothing else.\n\n"
            "Agents:\n" + agent_list + "\n\nJob: " + text
        )
        try:
            answer = self.llm.complete(
                prompt,
                system="You route customer-success jobs to specialist agents.",
                model=self.router_model,
            ).strip().lower()
        except Exception:
            return None
        for name in implemented:
            if name.lower() in answer:
                return name
        return None
