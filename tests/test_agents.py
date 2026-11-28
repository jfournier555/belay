"""Tests for the agents: all twenty-two end to end, plus the proof checklist.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed. They prove the agent gathers context from
the registry, drafts, records its sources, and self-checks the draft
against its proof checklist before the draft reaches the approval queue.
"""

import unittest

from connectors.gmail.tools import gmail_tools
from connectors.gong.tools import gong_tools
from connectors.linear.tools import linear_tools
from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from connectors.zendesk.tools import zendesk_tools
from connectors.zoom.tools import zoom_tools
from harness import AGENTS, TokenBudget, ToolRegistry
from harness.agents import Draft, ProofItem, check_proof
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
    for tool in gong_tools(config):
        registry.register(tool)
    for tool in gmail_tools(config):
        registry.register(tool)
    for tool in zoom_tools(config):
        registry.register(tool)
    for tool in zendesk_tools(config):
        registry.register(tool)
    for tool in linear_tools(config):
        registry.register(tool)
    return registry


class TestAgents(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_twenty_agents_registered(self):
        self.assertEqual(len(AGENTS), 22)
        implemented = [n for n, a in AGENTS.items() if a.implemented]
        self.assertEqual(
            sorted(implemented),
            [
                "adoption-gaps",
                "advocate-finder",
                "briefer",
                "call-recap-and-commitments",
                "case-triage",
                "champion-change",
                "churn-post-mortem",
                "competitive-defense",
                "ebr-prep",
                "escalation-manager",
                "expansion-spotter",
                "handoff",
                "onboarding-guide",
                "qbr-builder",
                "renewal-desk",
                "risk-watcher",
                "salesforce-updater",
                "scout",
                "stakeholder-mapper",
                "trust-repair",
                "value-finder",
                "voice-of-customer",
            ],
        )

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
        self.assertIn("gmail_search_messages", draft.sources)
        self.assertIn("linear_list_issues", draft.sources)
        # The stub draft shows the gathered context, including the stub
        # account's renewal date, so the pipeline is demonstrably working.
        self.assertIn("Acme Corp", draft.text)
        self.assertIn("2027-02-15", draft.text)

    def test_expansion_spotter_runs_and_cites_sources(self):
        agent = AGENTS["expansion-spotter"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "expansion-spotter")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("salesforce_list_opportunities", draft.sources)
        self.assertIn("salesforce_list_cases", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        # The stub draft shows the gathered context, including the open
        # expansion opportunity already in flight, so the pipeline is
        # demonstrably working.
        self.assertIn("Acme Corp", draft.text)
        self.assertIn("Expansion (50 seats)", draft.text)

    def test_onboarding_guide_runs_and_cites_sources(self):
        agent = AGENTS["onboarding-guide"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "onboarding-guide")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("salesforce_list_opportunities", draft.sources)
        self.assertIn("salesforce_list_cases", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        # The stub draft shows the gathered context and the task: the
        # account, the first value workflow, and the 60-day horizon.
        self.assertIn("Acme Corp", draft.text)
        self.assertIn("first value", draft.text)
        self.assertIn("60 days", draft.text)
        # The proof checklist passes end to end in stub mode.
        self.assertTrue(draft.proof_passed)
        self.assertEqual(len(draft.proof), 5)

    def test_voice_of_customer_runs_and_cites_sources(self):
        agent = AGENTS["voice-of-customer"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "voice-of-customer")
        self.assertIn("salesforce_list_cases", draft.sources)
        self.assertIn("zendesk_list_tickets", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        self.assertIn("gong_search_transcripts", draft.sources)
        self.assertIn("zoom_search_transcripts", draft.sources)
        # The voice of customer reads what customers say, not the account
        # record or the pipeline.
        self.assertNotIn("salesforce_get_account", draft.sources)
        self.assertNotIn("salesforce_list_opportunities", draft.sources)
        # The stub draft shows the gathered context and the task: the
        # account, feature requests, pain points, and sentiment.
        self.assertIn("Acme Corp", draft.text)
        self.assertIn("feature", draft.text)
        self.assertIn("pain", draft.text)
        self.assertIn("sentiment", draft.text)
        # The proof checklist passes end to end in stub mode.
        self.assertTrue(draft.proof_passed)
        self.assertEqual(len(draft.proof), 9)

    def test_voc_keyword_routes(self):
        router = Router(agents=AGENTS, llm=None)
        route = router.route(
            {"text": "summarize customer feedback for Acme Corp"}
        )
        self.assertEqual(route.agent, "voice-of-customer")

    def test_advocate_finder_runs_and_cites_sources(self):
        agent = AGENTS["advocate-finder"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "advocate-finder")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("salesforce_list_cases", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        # The advocate finder does not pull opportunities: it looks for
        # people, not deals.
        self.assertNotIn("salesforce_list_opportunities", draft.sources)
        # The stub draft shows the gathered context and the task: the
        # account, a potential reference, a champion, and each champion's
        # influence.
        self.assertIn("Acme Corp", draft.text)
        self.assertIn("reference", draft.text)
        self.assertIn("champion", draft.text)
        self.assertIn("influence", draft.text)
        # The proof checklist passes end to end in stub mode.
        self.assertTrue(draft.proof_passed)
        self.assertEqual(len(draft.proof), 6)

    def test_advocate_keyword_routes(self):
        router = Router(agents=AGENTS, llm=None)
        route = router.route({"text": "find an advocate for Acme Corp"})
        self.assertEqual(route.agent, "advocate-finder")

    def test_qbr_builder_runs_and_cites_sources(self):
        agent = AGENTS["qbr-builder"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "qbr-builder")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("salesforce_list_opportunities", draft.sources)
        self.assertIn("salesforce_list_cases", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        # The stub draft shows the gathered context and the task: the
        # account, outcomes delivered, adoption, and next quarter.
        self.assertIn("Acme Corp", draft.text)
        self.assertIn("outcome", draft.text)
        self.assertIn("adoption", draft.text)
        self.assertIn("next quarter", draft.text)
        # The proof checklist passes end to end in stub mode.
        self.assertTrue(draft.proof_passed)
        self.assertEqual(len(draft.proof), 5)

    def test_qbr_keyword_routes(self):
        router = Router(agents=AGENTS, llm=None)
        route = router.route({"text": "draft a qbr for Acme Corp"})
        self.assertEqual(route.agent, "qbr-builder")

    def test_scout_runs_and_cites_sources(self):
        agent = AGENTS["scout"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "scout")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("salesforce_list_opportunities", draft.sources)
        self.assertIn("salesforce_list_cases", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        # The stub draft shows the gathered context and the task: the
        # account, new stakeholders, and hiring signals.
        self.assertIn("Acme Corp", draft.text)
        self.assertIn("stakeholder", draft.text)
        self.assertIn("hiring", draft.text)
        # The proof checklist passes end to end in stub mode.
        self.assertTrue(draft.proof_passed)
        self.assertEqual(len(draft.proof), 5)

    def test_scout_keyword_routes(self):
        router = Router(agents=AGENTS, llm=None)
        route = router.route({"text": "scout Acme Corp for new signals"})
        self.assertEqual(route.agent, "scout")

    def test_scout_proof_flags_missing_sources_when_budget_skips_tools(self):
        # Stub tool costs: account 58, opportunities 96, cases 90,
        # slack 153. A budget of 100 lets only the account call through,
        # so the scout proof fails exactly the one source item.
        agent = AGENTS["scout"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            TokenBudget(100),
        )
        self.assertEqual(draft.sources, ["salesforce_get_account"])
        self.assertFalse(draft.proof_passed)
        failed = [p for p in draft.proof if not p["passed"]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["label"], "Names new opportunities")

    def test_onboarding_keyword_routes(self):
        router = Router(agents=AGENTS, llm=None)
        route = router.route({"text": "draft an onboarding plan for Acme Corp"})
        self.assertEqual(route.agent, "onboarding-guide")

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


class TestProofChecklist(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def _draft(self, text, sources):
        return Draft(
            text=text,
            agent="test-agent",
            job={},
            sources=list(sources),
        )

    def test_proof_item_passes_when_evidence_present(self):
        item = ProofItem(
            "Names the renewal",
            keywords=["renewal"],
            sources=["salesforce_list_opportunities"],
        )
        result = item.check(
            self._draft(
                "The renewal is on 2027-02-15.",
                ["salesforce_list_opportunities"],
            )
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["label"], "Names the renewal")
        self.assertEqual(result["detail"], "ok")

    def test_proof_item_fails_with_detail_when_evidence_missing(self):
        item = ProofItem(
            "Names the renewal",
            keywords=["renewal"],
            sources=["salesforce_list_opportunities"],
        )
        result = item.check(self._draft("Nothing here.", []))
        self.assertFalse(result["passed"])
        self.assertIn("renewal", result["detail"])
        self.assertIn("salesforce_list_opportunities", result["detail"])

    def test_proof_item_keyword_match_is_case_insensitive(self):
        item = ProofItem("Covers health", keywords=["Health"])
        result = item.check(self._draft("account HEALTH looks fine.", []))
        self.assertTrue(result["passed"])

    def test_check_proof_returns_one_result_per_item(self):
        checklist = [
            ProofItem("First", keywords=["one"]),
            ProofItem("Second", keywords=["two"]),
        ]
        results = check_proof(
            checklist, self._draft("one two three", [])
        )
        self.assertEqual([r["label"] for r in results], ["First", "Second"])
        self.assertTrue(all(r["passed"] for r in results))

    def test_agent_run_attaches_passing_proof(self):
        agent = AGENTS["renewal-desk"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertTrue(draft.proof_passed)
        self.assertEqual(len(draft.proof), 7)
        self.assertTrue(all(p["passed"] for p in draft.proof))

    def test_proof_flags_missing_sources_when_budget_skips_tools(self):
        # Stub tool costs: account 58, opportunities 96, cases 90,
        # slack 153. A budget of 100 lets only the account call through,
        # so the renewal desk proof fails exactly the four source items.
        agent = AGENTS["renewal-desk"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            TokenBudget(100),
        )
        self.assertEqual(draft.sources, ["salesforce_get_account"])
        self.assertFalse(draft.proof_passed)
        failed = [p for p in draft.proof if not p["passed"]]
        self.assertEqual(len(failed), 4)
        labels = [p["label"] for p in failed]
        self.assertIn("Names the open renewal opportunity", labels)
        self.assertIn("Names open support cases", labels)
        self.assertIn("Cites email threads", labels)
        self.assertIn("Cites open engineering issues", labels)

    def test_proof_failure_does_not_block_the_draft(self):
        # A failed proof item is a flag for the human reviewer, never a
        # block: the draft still exists and still reaches the queue.
        from harness import ApprovalQueue
        import os
        import tempfile

        agent = AGENTS["renewal-desk"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            TokenBudget(100),
        )
        self.assertFalse(draft.proof_passed)
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
                proof=draft.proof,
            )
            # The proof round-trips through the file-backed queue, so
            # the reviewer sees it when they open the item.
            reloaded = ApprovalQueue(tmp.name).get(item.id)
            self.assertIsNotNone(reloaded)
            self.assertEqual(len(reloaded.proof), 7)
            self.assertFalse(
                all(p["passed"] for p in reloaded.proof)
            )
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
