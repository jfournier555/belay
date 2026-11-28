"""Tests for the competitive-defense agent (v1.8.0).

The competitive-defense agent drafts a defense brief: threats ranked
by severity, evidence with its source, the vulnerability, the wedge to
hold the account, and the proof points to use. Threat severity is
harness-computed, never model prose, so the signal detection is tested
directly on synthetic tool results as well as end to end with stub
tools and the stub LLM.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed.
"""

import json
import os
import tempfile
import unittest

from connectors.gmail.tools import gmail_tools
from connectors.gong.tools import gong_tools
from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from connectors.zendesk.tools import zendesk_tools
from connectors.zoom.tools import zoom_tools
from harness import AGENTS, ApprovalQueue, TokenBudget, ToolRegistry
from harness.agents import (
    _COMPETITIVE_SOURCE_AGENTS,
    _competitive_signals,
    _rival_name,
)
from harness.config import Config
from harness.llm import StubLLM
from harness.registry import Tool
from harness.router import Router
from harness.run import execute_job


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
    return registry


def _empty_registry() -> ToolRegistry:
    """Registry whose tools return empty results: no account signals at
    all. The agent must draft a clean bill, not invent a threat."""
    registry = ToolRegistry()

    def account(**kwargs):
        return {
            "_stub": True,
            "id": "001EMPTY",
            "name": "Empty Corp",
            "account": "Empty Corp",
        }

    handlers = {
        "salesforce_get_account": account,
        "salesforce_list_opportunities": lambda **kw: {"opportunities": []},
        "salesforce_list_cases": lambda **kw: {"cases": []},
        "gmail_search_messages": lambda **kw: {"matches": []},
        "gong_search_transcripts": lambda **kw: {"matches": []},
        "zoom_search_transcripts": lambda **kw: {"matches": []},
        "zendesk_list_tickets": lambda **kw: {"tickets": []},
        "slack_search_messages": lambda **kw: {"matches": []},
    }
    for name, handler in handlers.items():
        registry.register(
            Tool(name=name, description="Empty stub for %s." % name, handler=handler)
        )
    return registry


def _temp_queue_path() -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    os.unlink(tmp.name)
    return tmp.name


def _seed(path: str, drafts: list) -> ApprovalQueue:
    """Submit drafts to the queue file; each draft is
    (agent, account, text, links). created_at values are set
    increasing in submission order so 'latest' is unambiguous."""
    queue = ApprovalQueue(path)
    for agent, account, text, links in drafts:
        queue.submit(
            agent=agent,
            job={"account": account},
            draft=text,
            sources=["salesforce_get_account"],
            links=links,
        )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for i, entry in enumerate(data):
        entry["created_at"] = "2026-11-21T10:00:%02d+00:00" % i
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return ApprovalQueue(path)


class TestCompetitiveDefenseRegistered(unittest.TestCase):
    def test_seventeen_agents_registered(self):
        self.assertEqual(len(AGENTS), 17)
        agent = AGENTS["competitive-defense"]
        self.assertTrue(agent.implemented)
        self.assertEqual(agent.display_name, "Competitive Defense")


class TestCompetitiveDefenseRuns(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_fallback_run_finds_the_threats(self):
        agent = AGENTS["competitive-defense"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "competitive-defense")
        for tool in (
            "salesforce_get_account",
            "salesforce_list_opportunities",
            "salesforce_list_cases",
            "gmail_search_messages",
            "gong_search_transcripts",
            "zoom_search_transcripts",
            "zendesk_list_tickets",
            "slack_search_messages",
        ):
            self.assertIn(tool, draft.sources)
        # The stub data has two real threats: the admin wants a look at
        # migrating to RivalWorks (Gmail, displacement, high) and the
        # ops lead was asked to evaluate RivalWorks side by side (Gong,
        # evaluation, medium). The brief names the rival, ranks the
        # brief high, and carries the wedge and the proof points.
        self.assertIn("RivalWorks", draft.text)
        self.assertIn("Overall threat level: high", draft.text)
        self.assertIn("Evidence", draft.text)
        self.assertIn("Vulnerability", draft.text)
        self.assertIn("Wedge", draft.text)
        self.assertIn("Proof points", draft.text)
        self.assertTrue(draft.links)
        self.assertTrue(draft.proof_passed)

    def test_displacement_ranks_before_evaluation(self):
        agent = AGENTS["competitive-defense"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        gmail_pos = draft.text.find("displacement signal in Gmail")
        gong_pos = draft.text.find("evaluation signal in Gong")
        self.assertNotEqual(gmail_pos, -1)
        self.assertNotEqual(gong_pos, -1)
        self.assertLess(gmail_pos, gong_pos)

    def test_no_signal_case_is_a_clean_bill(self):
        agent = AGENTS["competitive-defense"]
        draft = agent.run(
            {"account": "Empty Corp"},
            _empty_registry(),
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "competitive-defense")
        self.assertIn("Overall threat level: none", draft.text)
        self.assertIn("No competitor mentions", draft.text)
        self.assertNotIn("RivalWorks", draft.text)
        self.assertTrue(draft.proof_passed)

    def test_composes_from_prior_drafts(self):
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "voice-of-customer",
                        "Acme Corp",
                        "MARKER voice-of-customer draft for Acme Corp.",
                        [
                            {
                                "label": "Account: Acme Corp",
                                "url": "https://stub.salesforce.invalid/lightning/r/Account/001STUB/view",
                            }
                        ],
                    ),
                    (
                        "risk-watcher",
                        "Acme Corp",
                        "MARKER risk-watcher draft for Acme Corp.",
                        [
                            {
                                "label": "Slack: Acme Corp signal",
                                "url": "https://stub.slack.invalid/archives/C123/p123",
                            }
                        ],
                    ),
                ],
            )
            agent = AGENTS["competitive-defense"]
            draft = agent.run(
                {"account": "Acme Corp", "approvals_file": path},
                self.registry,
                self.llm,
                self.budget,
            )
            self.assertEqual(draft.agent, "competitive-defense")
            self.assertIn(
                "Composed from prior drafts: voice-of-customer, risk-watcher.",
                draft.text,
            )
            urls = [link.get("url") for link in draft.links]
            self.assertIn(
                "https://stub.salesforce.invalid/lightning/r/Account/001STUB/view",
                urls,
            )
            self.assertIn(
                "https://stub.slack.invalid/archives/C123/p123", urls
            )
            # Fresh signals still fire in the composed path: the Gmail
            # displacement and the Gong evaluation are gathered directly,
            # not from the old drafts.
            self.assertIn("RivalWorks", draft.text)
            self.assertIn("gmail_search_messages", draft.sources)
            self.assertIn("gong_search_transcripts", draft.sources)
            # Cases come from the prior drafts here, not from a fresh
            # gather.
            self.assertNotIn("salesforce_list_cases", draft.sources)
            self.assertTrue(draft.proof_passed)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_source_agents_are_the_expected_three(self):
        self.assertEqual(
            _COMPETITIVE_SOURCE_AGENTS,
            ("voice-of-customer", "expansion-spotter", "risk-watcher"),
        )

    def test_execute_job_hands_the_queue_to_the_agent(self):
        # Regression: execute_job only hands approvals_file to a
        # hardcoded list of composing agents. Competitive-defense
        # composes, so it must be on that list, or the CLI run always
        # falls back to direct gathering even with prior drafts queued.
        path = _temp_queue_path()
        mem_path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "voice-of-customer",
                        "Acme Corp",
                        "MARKER voice-of-customer draft for Acme Corp.",
                        [],
                    ),
                ],
            )
            old_approvals = os.environ.get("HARNESS_APPROVALS_FILE")
            old_memory = os.environ.get("HARNESS_MEMORY_FILE")
            os.environ["HARNESS_APPROVALS_FILE"] = path
            os.environ["HARNESS_MEMORY_FILE"] = mem_path
            try:
                config = Config.from_env()
                config.stub_mode = True
                item, draft, agent, route = execute_job(
                    config,
                    {
                        "agent": "competitive-defense",
                        "account": "Acme Corp",
                        "text": "Acme Corp",
                    },
                )
            finally:
                if old_approvals is None:
                    os.environ.pop("HARNESS_APPROVALS_FILE", None)
                else:
                    os.environ["HARNESS_APPROVALS_FILE"] = old_approvals
                if old_memory is None:
                    os.environ.pop("HARNESS_MEMORY_FILE", None)
                else:
                    os.environ["HARNESS_MEMORY_FILE"] = old_memory
            self.assertEqual(agent.name, "competitive-defense")
            self.assertIn(
                "Composed from prior drafts: voice-of-customer.",
                draft.text,
            )
            self.assertNotIn("salesforce_list_cases", draft.sources)
            self.assertIn("RivalWorks", draft.text)
        finally:
            for p in (path, mem_path):
                if os.path.exists(p):
                    os.unlink(p)


class TestCompetitiveSignals(unittest.TestCase):
    def _results(self, matches, tickets=None, cases=None):
        results = {
            "gmail_search_messages": {"matches": matches},
            "gong_search_transcripts": {"matches": []},
            "zoom_search_transcripts": {"matches": []},
            "slack_search_messages": {"matches": []},
        }
        if tickets is not None:
            results["zendesk_list_tickets"] = {"tickets": tickets}
        if cases is not None:
            results["salesforce_list_cases"] = {"cases": cases}
        return results

    def test_displacement_is_high_and_names_the_rival(self):
        results = self._results(
            [
                {
                    "excerpt": (
                        "Before we sign, leadership wants a look at "
                        "migrating to RivalWorks."
                    )
                }
            ]
        )
        signals = _competitive_signals(results, "Acme Corp")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["severity"], "high")
        self.assertEqual(signals[0]["kind"], "displacement")
        self.assertEqual(signals[0]["rival"], "RivalWorks")
        self.assertIn("RivalWorks", signals[0]["text"])

    def test_evaluation_is_medium(self):
        results = self._results(
            [
                {
                    "excerpt": (
                        "Leadership asked us to evaluate RivalWorks side "
                        "by side before we sign."
                    )
                }
            ]
        )
        signals = _competitive_signals(results, "Acme Corp")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["severity"], "medium")
        self.assertEqual(signals[0]["kind"], "evaluation")
        self.assertEqual(signals[0]["rival"], "RivalWorks")

    def test_champion_doubt_is_medium(self):
        results = self._results(
            [
                {
                    "excerpt": (
                        "The champion is reconsidering whether this is "
                        "the right fit for the team."
                    )
                }
            ]
        )
        signals = _competitive_signals(results, "Acme Corp")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["severity"], "medium")
        self.assertEqual(signals[0]["kind"], "champion-doubt")
        self.assertEqual(signals[0]["rival"], "an unnamed rival")

    def test_our_own_demo_is_not_a_signal(self):
        # Bare "demo" is our own product demo, not a rival's. The
        # stub's own-demo excerpts must not fire.
        results = self._results(
            [
                {"excerpt": "Demoed the new reporting to Acme's ops lead."},
                {"excerpt": "SSO login fails for new hires again."},
                {"excerpt": "The reporting rollout is live for fifty seats."},
            ]
        )
        self.assertEqual(_competitive_signals(results, "Acme Corp"), [])

    def test_phrase_without_a_rival_name_still_fires_unnamed(self):
        results = self._results(
            [{"excerpt": "We are evaluating the renewal before we sign."}]
        )
        signals = _competitive_signals(results, "Acme Corp")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["rival"], "an unnamed rival")
        self.assertIn("evaluating the renewal", signals[0]["excerpt"])

    def test_open_ticket_with_comparison_language_fires(self):
        results = self._results(
            [],
            tickets=[
                {
                    "id": 201,
                    "subject": "Comparison with RivalWorks on reporting exports",
                    "status": "open",
                    "priority": "normal",
                    "description": "The team wants a comparison of the two.",
                }
            ],
        )
        signals = _competitive_signals(results, "Acme Corp")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["source"], "Zendesk")
        self.assertEqual(signals[0]["rival"], "RivalWorks")

    def test_closed_ticket_is_history_not_a_threat(self):
        results = self._results(
            [],
            tickets=[
                {
                    "id": 202,
                    "subject": "Evaluating RivalWorks last year",
                    "status": "solved",
                    "priority": "normal",
                    "description": "We evaluated RivalWorks and stayed.",
                }
            ],
        )
        self.assertEqual(_competitive_signals(results, "Acme Corp"), [])

    def test_signals_rank_high_first(self):
        results = {
            "gmail_search_messages": {
                "matches": [
                    {
                        "excerpt": (
                            "We are evaluating RivalWorks side by side."
                        )
                    }
                ]
            },
            "gong_search_transcripts": {
                "matches": [
                    {
                        "excerpt": (
                            "Leadership wants a look at migrating to "
                            "RivalWorks."
                        )
                    }
                ]
            },
            "zoom_search_transcripts": {"matches": []},
            "slack_search_messages": {"matches": []},
        }
        signals = _competitive_signals(results, "Acme Corp")
        self.assertEqual(
            [s["severity"] for s in signals], ["high", "medium"]
        )
        self.assertEqual(signals[0]["kind"], "displacement")

    def test_one_signal_per_source_at_most(self):
        results = self._results(
            [
                {"excerpt": "We are evaluating RivalWorks side by side."},
                {"excerpt": "Also comparing against OtherCorp this week."},
            ]
        )
        signals = _competitive_signals(results, "Acme Corp")
        self.assertEqual(len(signals), 1)

    def test_rival_name_extraction(self):
        self.assertEqual(
            _rival_name("Leadership asked us to evaluate RivalWorks."),
            "RivalWorks",
        )
        self.assertEqual(
            _rival_name("We are migrating to OtherCorp next quarter."),
            "OtherCorp",
        )
        self.assertEqual(
            _rival_name("A comparison with RivalWorks is due Friday."),
            "RivalWorks",
        )
        self.assertEqual(
            _rival_name("We are evaluating the renewal."),
            "an unnamed rival",
        )
        self.assertEqual(_rival_name("Nothing to see here."), "an unnamed rival")


class TestCompetitiveDefenseRouting(unittest.TestCase):
    def test_keyword_competitor_routes(self):
        router = Router()
        result = router.route(
            {"text": "we are evaluating a competitor for this account"}
        )
        self.assertEqual(result.agent, "competitive-defense")

    def test_keyword_displacement_routes(self):
        router = Router()
        result = router.route(
            {"text": "displacement risk on this account, rival demo next week"}
        )
        self.assertEqual(result.agent, "competitive-defense")

    def test_explicit_agent_routes(self):
        router = Router()
        result = router.route({"text": "anything", "agent": "competitive-defense"})
        self.assertEqual(result.agent, "competitive-defense")
        self.assertIsNone(result.error)

    def test_renewal_text_still_routes_to_renewal_desk(self):
        # "churn risk" in a renewal job must not steal the route: the
        # renewal keywords outscore the risk keyword here, and the
        # competitor keywords are absent.
        router = Router()
        result = router.route({"text": "draft a renewal plan, churn risk"})
        self.assertEqual(result.agent, "renewal-desk")

    def test_risk_text_still_routes_to_risk_watcher(self):
        router = Router()
        result = router.route({"text": "check the risk on this account"})
        self.assertEqual(result.agent, "risk-watcher")


if __name__ == "__main__":
    unittest.main()
