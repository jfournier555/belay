"""Tests for the router: explicit, keyword, LLM, and default paths."""

import unittest

from harness.agents import AGENTS
from harness.llm import StubLLM
from harness.router import Router


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.router = Router(agents=AGENTS, llm=None)

    def test_explicit_agent(self):
        result = self.router.route({"text": "anything", "agent": "renewal-desk"})
        self.assertEqual(result.agent, "renewal-desk")
        self.assertEqual(result.confidence, "high")
        self.assertIsNone(result.error)

    def test_explicit_unknown_agent(self):
        result = self.router.route({"text": "anything", "agent": "nope"})
        self.assertIsNone(result.agent)
        self.assertIsNotNone(result.error)

    def test_explicit_voice_of_customer_routes(self):
        # All nine agents are implemented now, so an explicit request
        # for the last one routes straight to it.
        result = self.router.route(
            {"text": "anything", "agent": "voice-of-customer"}
        )
        self.assertEqual(result.agent, "voice-of-customer")
        self.assertIsNone(result.error)

    def test_keyword_renewal(self):
        result = self.router.route({"text": "draft a renewal plan, churn risk"})
        self.assertEqual(result.agent, "renewal-desk")

    def test_keyword_briefer(self):
        result = self.router.route({"text": "prep a briefing for the meeting"})
        self.assertEqual(result.agent, "briefer")

    def test_keyword_expansion_routes_to_expansion_spotter(self):
        result = self.router.route({"text": "find expansion openings"})
        self.assertEqual(result.agent, "expansion-spotter")

    def test_keyword_feedback_routes_to_voc(self):
        result = self.router.route(
            {"text": "summarize customer feedback for this account"}
        )
        self.assertEqual(result.agent, "voice-of-customer")

    def test_empty_job_defaults_to_briefer(self):
        result = self.router.route({"text": ""})
        self.assertEqual(result.agent, "briefer")
        self.assertEqual(result.confidence, "low")

    def test_llm_routing_picks_renewal(self):
        # A stub LLM that always answers "renewal-desk". Proves the LLM
        # path is wired; the real model is not needed for the test.
        class FixedLLM:
            def complete(self, prompt, system=None, model=None):
                return "renewal-desk"

        router = Router(agents=AGENTS, llm=FixedLLM())
        result = router.route({"text": "something ambiguous"})
        self.assertEqual(result.agent, "renewal-desk")
        self.assertEqual(result.reason.split()[0], "Router")

    def test_llm_garbage_answer_falls_back(self):
        class GarbageLLM:
            def complete(self, prompt, system=None, model=None):
                return "banana"

        router = Router(agents=AGENTS, llm=GarbageLLM())
        result = router.route({"text": "something ambiguous"})
        self.assertEqual(result.agent, "briefer")


if __name__ == "__main__":
    unittest.main()
