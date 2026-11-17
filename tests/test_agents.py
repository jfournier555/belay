"""Tests for the agents: renewal desk and briefer end to end.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed. They prove the agent gathers context from
the registry, drafts, and records its sources. The planned agents are
also checked: they exist, are marked not implemented, and refuse to run.
"""

import unittest

from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from harness import AGENTS, TokenBudget, ToolRegistry
from harness.config import Config
from harness.llm import StubLLM
from harness.router import Router


def _stub_registry() -> ToolRegistry:
    """Registry wired to the stub connector tools (no creds needed)."""
    config = Config.from_env()
    config.stub_mode = True
    registry = ToolRegistry()
    for tool in salesforce_tools(config):
        registry.register(tool)
    for tool in slack_tools(config):
        registry.register(tool)
    return registry


class TestAgents(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_eight_agents_registered(self):
        self.assertEqual(len(AGENTS), 8)
        implemented = [n for n, a in AGENTS.items() if a.implemented]
        self.assertEqual(sorted(implemented), ["briefer", "renewal-desk"])

    def test_renewal_desk_runs_and_cites_sources(self):
        agent = AGENTS["renewal-desk"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "renewal-desk")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("salesforce_list_opportunities", draft.sources)
        self.assertIn("salesforce_list_cases", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        # The stub draft shows the gathered context, including the stub
        # account's renewal date, so the pipeline is demonstrably working.
        self.assertIn("Acme Corp", draft.text)
        self.assertIn("2027-02-15", draft.text)

    def test_briefer_runs_and_cites_sources(self):
        agent = AGENTS["briefer"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "briefer")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        self.assertIn("Acme Corp", draft.text)

    def test_planned_agent_refuses_to_run(self):
        agent = AGENTS["qbr-builder"]
        self.assertFalse(agent.implemented)
        with self.assertRaises(NotImplementedError):
            agent.run({}, self.registry, self.llm, self.budget)

    def test_extract_account_prefers_explicit(self):
        from harness.agents import _extract_account

        self.assertEqual(
            _extract_account({"account": "Acme Corp", "text": "other"}),
            "Acme Corp",
        )
        self.assertEqual(
            _extract_account({"text": "draft a renewal plan for Acme Corp"}),
            "Acme Corp",
        )
        self.assertEqual(_extract_account({"text": "Acme Corp"}), "Acme Corp")

    def test_full_loop_router_to_queue(self):
        # Router -> agent -> draft -> approval queue, the whole v1 loop.
        from harness import ApprovalQueue
        import os
        import tempfile

        router = Router(agents=AGENTS, llm=None)
        route = router.route({"text": "draft a renewal plan for Acme Corp"})
        self.assertEqual(route.agent, "renewal-desk")
        agent = AGENTS[route.agent]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            TokenBudget(8000),
        )
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        os.unlink(tmp.name)
        try:
            queue = ApprovalQueue(tmp.name)
            item = queue.submit(
                agent=agent.name,
                job={"account": "Acme Corp"},
                draft=draft.text,
                sources=draft.sources,
            )
            self.assertEqual(item.status, "pending")
            self.assertEqual(len(queue.list_pending()), 1)
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
