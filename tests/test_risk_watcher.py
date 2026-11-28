"""Tests for the risk-watcher agent (v1.6.0).

The risk-watcher drafts a risk brief: churn signals ranked by severity,
evidence with deep links, overall severity, next step. Severity is
harness-computed, never model prose, so the signal detection is tested
directly on synthetic tool results as well as end to end with stub tools
and the stub LLM.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed.
"""

import json
import os
import tempfile
import unittest
from datetime import date

from connectors.gmail.tools import gmail_tools
from connectors.gong.tools import gong_tools
from connectors.linear.tools import linear_tools
from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from connectors.zendesk.tools import zendesk_tools
from connectors.zoom.tools import zoom_tools
from harness import AGENTS, ApprovalQueue, TokenBudget, ToolRegistry
from harness.agents import (
    _RISK_SOURCE_AGENTS,
    _risk_signals,
    _risk_signals_block,
)
from harness.config import Config
from harness.llm import StubLLM
from harness.registry import Tool
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


def _empty_registry() -> ToolRegistry:
    """Registry whose tools return empty results: no account signals at
    all. The agent must draft a clean bill, not invent a risk."""
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
        "linear_list_issues": lambda **kw: {"issues": []},
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


class TestRiskWatcherRegistered(unittest.TestCase):
    def test_twenty_agents_registered(self):
        self.assertEqual(len(AGENTS), 21)
        agent = AGENTS["risk-watcher"]
        self.assertTrue(agent.implemented)
        self.assertEqual(agent.display_name, "Risk Watcher")


class TestRiskWatcherRuns(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_fallback_run_gathers_sources(self):
        agent = AGENTS["risk-watcher"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "risk-watcher")
        for tool in (
            "salesforce_get_account",
            "salesforce_list_opportunities",
            "salesforce_list_cases",
            "gmail_search_messages",
            "gong_search_transcripts",
            "zoom_search_transcripts",
            "zendesk_list_tickets",
            "linear_list_issues",
            "slack_search_messages",
        ):
            self.assertIn(tool, draft.sources)
        # The stub data has a real high signal: the SSO failure open
        # in three places. No champion data is gathered any more, so
        # no departure is named.
        self.assertIn("SSO login fails for new hires", draft.text)
        self.assertIn("Overall severity: high", draft.text)
        self.assertNotIn("Priya Nair", draft.text)
        self.assertTrue(draft.links)
        self.assertTrue(draft.proof_passed)

    def test_sso_grouped_into_one_signal(self):
        # The same SSO failure is an open Salesforce case, an open
        # Zendesk ticket, and an open Linear issue. The brief lists it
        # once, naming all three places, not three times.
        agent = AGENTS["risk-watcher"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn(
            "Support/delivery risk: 'SSO login fails for new hires' is "
            "open in 3 place(s)",
            draft.text,
        )

    def test_no_signal_case_is_a_clean_bill(self):
        agent = AGENTS["risk-watcher"]
        draft = agent.run(
            {"account": "Empty Corp"},
            _empty_registry(),
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "risk-watcher")
        self.assertIn("Overall severity: none", draft.text)
        self.assertIn("No churn signals found", draft.text)
        self.assertNotIn("Champion departure", draft.text)
        self.assertTrue(draft.proof_passed)


class TestRiskSignals(unittest.TestCase):
    def test_records_group_by_subject_at_worst_severity(self):
        results = {
            "salesforce_list_cases": {
                "cases": [
                    {
                        "number": "00001234",
                        "subject": "SSO login fails for new hires",
                        "status": "Working",
                        "priority": "High",
                        "age_days": 6,
                    }
                ]
            },
            "zendesk_list_tickets": {
                "tickets": [
                    {
                        "id": 101,
                        "subject": "SSO login fails for new hires",
                        "status": "open",
                        "priority": "high",
                        "age_days": 6,
                    }
                ]
            },
            "linear_list_issues": {
                "issues": [
                    {
                        "identifier": "ENG-142",
                        "title": "SSO login fails for new hires",
                        "status": "In Progress",
                        "priority": "high",
                        "age_days": 6,
                    },
                    {
                        "identifier": "ENG-151",
                        "title": "Reporting dashboard slow",
                        "status": "Backlog",
                        "priority": "low",
                        "age_days": 1,
                    },
                ]
            },
        }
        signals = _risk_signals(results, "Acme Corp")
        grouped = [s for s in signals if s["kind"] == "support-delivery"]
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["severity"], "high")
        self.assertIn("3 place(s)", grouped[0]["text"])
        # The low-priority dashboard issue is normal operations, not a
        # signal, so it appears nowhere.
        self.assertNotIn("dashboard", "\n".join(s["text"] for s in signals))

    def test_stale_medium_record_is_a_signal_fresh_one_is_not(self):
        results = {
            "zendesk_list_tickets": {
                "tickets": [
                    {
                        "id": 201,
                        "subject": "Old medium ticket",
                        "status": "open",
                        "priority": "normal",
                        "age_days": 9,
                    },
                    {
                        "id": 202,
                        "subject": "Fresh medium ticket",
                        "status": "open",
                        "priority": "normal",
                        "age_days": 1,
                    },
                ]
            }
        }
        signals = _risk_signals(results, "Acme Corp")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["severity"], "medium")
        self.assertIn("Old medium ticket", signals[0]["text"])

    def test_renewal_dates(self):
        today = date(2026, 11, 21)
        results = {
            "salesforce_list_opportunities": {
                "opportunities": [
                    {
                        "name": "Acme Corp - Renewal FY27",
                        "stage": "Negotiation",
                        "amount": 120000,
                        "close_date": "2026-10-01",
                        "type": "Renewal",
                    },
                    {
                        "name": "Acme Corp - Renewal FY28",
                        "stage": "Negotiation",
                        "amount": 90000,
                        "close_date": "2026-12-10",
                        "type": "Renewal",
                    },
                    {
                        "name": "Acme Corp - Expansion",
                        "stage": "Qualification",
                        "amount": 45000,
                        "close_date": "2027-04-01",
                        "type": "Expansion",
                    },
                ]
            }
        }
        signals = _risk_signals(results, "Acme Corp", today=today)
        kinds = {s["kind"]: s["severity"] for s in signals}
        self.assertEqual(kinds.get("renewal-stalled"), "high")
        self.assertEqual(kinds.get("renewal-closing"), "medium")
        # The expansion opportunity is not a renewal signal.
        self.assertNotIn("Expansion", "\n".join(s["text"] for s in signals))

    def test_escalation_language_is_a_signal(self):
        results = {
            "gmail_search_messages": {
                "matches": [
                    {
                        "subject": "Renewal",
                        "excerpt": (
                            "If this is not fixed we will cancel at "
                            "renewal."
                        ),
                    }
                ]
            }
        }
        signals = _risk_signals(results, "Acme Corp")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["kind"], "escalation-language")
        self.assertEqual(signals[0]["severity"], "medium")

    def test_signals_rank_high_first(self):
        results = {
            "zendesk_list_tickets": {
                "tickets": [
                    {
                        "id": 201,
                        "subject": "Old medium ticket",
                        "status": "open",
                        "priority": "normal",
                        "age_days": 9,
                    }
                ]
            },
            "salesforce_list_opportunities": {
                "opportunities": [
                    {
                        "name": "Acme Corp - Renewal FY27",
                        "stage": "Negotiation",
                        "amount": 120000,
                        "close_date": "2026-10-01",
                        "type": "Renewal",
                    }
                ]
            },
        }
        signals = _risk_signals(results, "Acme Corp", today=date(2026, 11, 21))
        self.assertEqual(
            [s["severity"] for s in signals], ["high", "medium"]
        )

    def test_clean_bill_block(self):
        block = _risk_signals_block([])
        self.assertIn("Overall severity: none", block)
        self.assertIn("No churn signals found", block)


class TestRiskWatcherComposition(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_composes_from_prior_drafts(self):
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "renewal-desk",
                        "Acme Corp",
                        "MARKER renewal draft for Acme Corp.",
                        [
                            {
                                "label": "Account: Acme Corp",
                                "url": "https://stub.salesforce.invalid/lightning/r/Account/001STUB/view",
                            }
                        ],
                    ),
                    (
                        "advocate-finder",
                        "Acme Corp",
                        "MARKER advocate draft for Acme Corp.",
                        [
                            {
                                "label": "Slack: Acme Corp praise",
                                "url": "https://stub.slack.invalid/archives/C123/p123",
                            }
                        ],
                    ),
                ],
            )
            agent = AGENTS["risk-watcher"]
            draft = agent.run(
                {"account": "Acme Corp", "approvals_file": path},
                self.registry,
                self.llm,
                self.budget,
            )
            self.assertEqual(draft.agent, "risk-watcher")
            self.assertIn(
                "Composed from prior drafts: renewal-desk, advocate-finder.",
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
            # Fresh signals still fire in the composed path: the SSO
            # failure is gathered directly, not from the old drafts.
            self.assertIn("SSO login fails for new hires", draft.text)
            # Opportunities come from the prior drafts here, not from a
            # fresh gather.
            self.assertNotIn(
                "salesforce_list_opportunities", draft.sources
            )
            self.assertTrue(draft.proof_passed)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_source_agents_are_the_expected_three(self):
        self.assertEqual(
            _RISK_SOURCE_AGENTS,
            ("renewal-desk", "advocate-finder", "voice-of-customer"),
        )


class TestRiskWatcherRouting(unittest.TestCase):
    def test_keyword_risk_routes(self):
        router = Router()
        result = router.route(
            {"text": "check the risk on this account", "account": "Acme Corp"}
        )
        self.assertEqual(result.agent, "risk-watcher")

    def test_explicit_agent_routes(self):
        router = Router()
        result = router.route({"text": "anything", "agent": "risk-watcher"})
        self.assertEqual(result.agent, "risk-watcher")
        self.assertIsNone(result.error)

    def test_renewal_text_still_routes_to_renewal_desk(self):
        # "churn risk" in a renewal job must not steal the route: the
        # renewal keywords outscore the risk keyword here.
        router = Router()
        result = router.route({"text": "draft a renewal plan, churn risk"})
        self.assertEqual(result.agent, "renewal-desk")


if __name__ == "__main__":
    unittest.main()
