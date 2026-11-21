"""Tests for the value-finder agent (v0.9.0).

Value-finder finds realized and unrealized outcomes in an account,
quantified in money or time. The quantification is harness-computed,
never model prose: closed-won opportunity amounts are measured, hours
saved x the loaded hourly rate is modeled, and every dollar figure is
labeled with its basis. A figure with no basis does not appear.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed.
"""

import os
import tempfile
import unittest
from unittest import mock

from connectors.champify.tools import champify_tools
from connectors.gmail.tools import gmail_tools
from connectors.gong.tools import gong_tools
from connectors.linear.tools import linear_tools
from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from connectors.zendesk.tools import zendesk_tools
from connectors.zoom.tools import zoom_tools
from harness import AGENTS, ApprovalQueue, TokenBudget, ToolRegistry
from harness.agents import (
    ProofItem,
    _value_figures,
    format_money,
    quantify_time_savings,
)
from harness.config import Config
from harness.llm import StubLLM
from harness.router import Router
from harness.run import build_parser


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
    for tool in champify_tools(config):
        registry.register(tool)
    return registry


def _stub_opportunities() -> dict:
    """The real stub opportunities result, straight from the tool."""
    config = Config.from_env()
    config.stub_mode = True
    tools = {tool.name: tool for tool in salesforce_tools(config)}
    return tools["salesforce_list_opportunities"].run(account="Acme Corp")


class TestQuantification(unittest.TestCase):
    def test_time_savings_is_hours_times_rate(self):
        self.assertEqual(quantify_time_savings(10, 150), 1500)
        self.assertEqual(quantify_time_savings(2.5, 100), 250)
        self.assertEqual(quantify_time_savings(0, 150), 0)

    def test_format_money(self):
        self.assertEqual(format_money(0), "$0")
        self.assertEqual(format_money(1500), "$1,500")
        self.assertEqual(format_money(165000), "$165,000")
        self.assertEqual(format_money(1500.5), "$1,500.50")

    def test_figures_from_stub_opportunities(self):
        # Stub: $120,000 renewal in flight (Negotiation) + $45,000
        # expansion (Qualification), nothing closed-won.
        block = _value_figures(
            _stub_opportunities(), None, None, "the job"
        )
        self.assertIn("Realized (measured): $0", block)
        self.assertIn("Unrealized (measured): $165,000", block)
        self.assertIn("$45,000 Expansion", block)
        self.assertIn("$120,000 Renewal", block)
        self.assertIn("Realized total: $0.", block)
        self.assertIn(
            "Total quantified: realized $0; unrealized $165,000.", block
        )

    def test_figures_with_hours_and_rate(self):
        block = _value_figures(
            _stub_opportunities(), 10, 150, "CLI --hourly-rate"
        )
        self.assertIn("Realized (modeled): $1,500", block)
        self.assertIn("10 hours saved x $150/hour", block)
        self.assertIn("rate from CLI --hourly-rate", block)
        self.assertIn("Realized total: $1,500.", block)
        self.assertIn(
            "Total quantified: realized $1,500; unrealized $165,000.",
            block,
        )

    def test_figures_without_rate_report_hours_only(self):
        # Hours supplied, no rate: no dollar figure is invented for
        # the time saved, and the block says so.
        block = _value_figures(
            _stub_opportunities(), 10, None, "the job"
        )
        self.assertIn("no dollar figure", block)
        self.assertIn("no loaded hourly rate", block)
        self.assertNotIn("$1,500", block)
        self.assertIn("Realized total: $0.", block)

    def test_figures_without_hours_or_rate(self):
        block = _value_figures(
            _stub_opportunities(), None, None, "the job"
        )
        self.assertIn("no hours saved and no loaded hourly rate", block)
        self.assertIn("Realized total: $0.", block)

    def test_figures_when_opportunities_not_gathered(self):
        block = _value_figures(None, None, None, "the job")
        self.assertIn("not gathered", block)
        self.assertIn("Realized total: $0.", block)
        self.assertIn(
            "Total quantified: realized $0; unrealized $0.", block
        )

    def test_closed_won_counts_as_realized_measured(self):
        opps = {
            "opportunities": [
                {
                    "name": "Won deal",
                    "id": "006WON",
                    "stage": "Closed Won",
                    "amount": 80000,
                    "type": "Expansion",
                },
                {
                    "name": "Open deal",
                    "id": "006OPEN",
                    "stage": "Negotiation",
                    "amount": 20000,
                    "type": "Expansion",
                },
            ]
        }
        block = _value_figures(opps, None, None, "the job")
        self.assertIn("Realized (measured): $80,000", block)
        self.assertIn("Unrealized (measured): $20,000", block)

    def test_nonpositive_inputs_are_treated_as_absent(self):
        block = _value_figures(
            _stub_opportunities(), 0, -50, "the job"
        )
        self.assertIn("no hours saved and no loaded hourly rate", block)
        self.assertIn("Realized total: $0.", block)


class TestValueFinderAgent(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_runs_and_cites_sources(self):
        agent = AGENTS["value-finder"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "value-finder")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("salesforce_list_opportunities", draft.sources)
        self.assertIn("salesforce_list_cases", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        # The stub draft shows the gathered context and the task: the
        # account, realized and unrealized outcomes, and the dollar
        # total from the harness-computed figures block.
        self.assertIn("Acme Corp", draft.text)
        self.assertIn("realized", draft.text.lower())
        self.assertIn("unrealized", draft.text.lower())
        self.assertIn("$165,000", draft.text)
        self.assertIn("measured", draft.text)
        self.assertIn("modeled", draft.text)
        # Deep links: account + 2 opportunities + 2 cases + 3 Slack.
        self.assertEqual(len(draft.links), 8)
        # The proof checklist passes end to end in stub mode.
        self.assertTrue(draft.proof_passed)
        self.assertEqual(len(draft.proof), 9)

    def test_runs_with_hours_and_rate(self):
        agent = AGENTS["value-finder"]
        draft = agent.run(
            {
                "account": "Acme Corp",
                "hours_saved": 10,
                "hourly_rate": 150,
                "hourly_rate_source": "CLI --hourly-rate",
            },
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("$1,500", draft.text)
        self.assertIn("10 hours saved x $150/hour", draft.text)
        self.assertTrue(draft.proof_passed)

    def test_runs_without_rate_invents_no_dollar_figure(self):
        agent = AGENTS["value-finder"]
        draft = agent.run(
            {"account": "Acme Corp", "hours_saved": 10},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("no loaded hourly rate", draft.text)
        self.assertNotIn("$1,500", draft.text)
        self.assertTrue(draft.proof_passed)

    def test_prior_drafts_are_synthesized_from_the_queue(self):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        os.unlink(tmp.name)
        try:
            queue = ApprovalQueue(tmp.name)
            queue.submit(
                agent="renewal-desk",
                job={"account": "Acme Corp"},
                draft="Renewal plan: health is green, renewal Feb 15.",
                sources=["salesforce_get_account"],
            )
            queue.submit(
                agent="expansion-spotter",
                job={"account": "Globex Inc"},
                draft="Expansion opening at Globex, not Acme.",
                sources=["salesforce_list_opportunities"],
            )
            agent = AGENTS["value-finder"]
            draft = agent.run(
                {"account": "Acme Corp", "approvals_file": tmp.name},
                self.registry,
                self.llm,
                self.budget,
            )
            # The Acme renewal-desk draft is in the context; the
            # Globex expansion draft is not.
            self.assertIn("health is green", draft.text)
            self.assertNotIn("not Acme", draft.text)
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def test_runs_without_an_approvals_file(self):
        agent = AGENTS["value-finder"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("No prior agent drafts on file", draft.text)
        self.assertTrue(draft.proof_passed)

    def test_does_not_synthesize_its_own_prior_drafts(self):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        os.unlink(tmp.name)
        try:
            queue = ApprovalQueue(tmp.name)
            queue.submit(
                agent="value-finder",
                job={"account": "Acme Corp"},
                draft="MARKER own prior value report, must not repeat.",
                sources=["salesforce_list_opportunities"],
            )
            agent = AGENTS["value-finder"]
            draft = agent.run(
                {"account": "Acme Corp", "approvals_file": tmp.name},
                self.registry,
                self.llm,
                self.budget,
            )
            self.assertNotIn("MARKER", draft.text)
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def test_value_keyword_routes(self):
        router = Router(agents=AGENTS, llm=None)
        route = router.route(
            {"text": "draft a value report for Acme Corp"}
        )
        self.assertEqual(route.agent, "value-finder")

    def test_routing_does_not_steal_qbr_or_onboarding(self):
        router = Router(agents=AGENTS, llm=None)
        self.assertEqual(
            router.route({"text": "draft a qbr for Acme Corp"}).agent,
            "qbr-builder",
        )
        self.assertEqual(
            router.route(
                {
                    "text": (
                        "draft an onboarding plan with a first value "
                        "checklist for Acme Corp"
                    )
                }
            ).agent,
            "onboarding-guide",
        )


class TestProofAnyKeywords(unittest.TestCase):
    def test_passes_when_one_keyword_present(self):
        item = ProofItem(
            "Dollar figures are labeled measured or modeled",
            any_keywords=["measured", "modeled"],
        )
        from harness.agents import Draft

        draft = Draft(
            text="Total: $1,500 (modeled: 10 hours x $150/hour).",
            agent="test-agent",
            job={},
        )
        self.assertTrue(item.check(draft)["passed"])

    def test_fails_when_neither_keyword_present(self):
        item = ProofItem(
            "Dollar figures are labeled measured or modeled",
            any_keywords=["measured", "modeled"],
        )
        from harness.agents import Draft

        draft = Draft(
            text="Total: $1,500.",
            agent="test-agent",
            job={},
        )
        result = item.check(draft)
        self.assertFalse(result["passed"])
        self.assertIn("measured", result["detail"])


class TestRateConfig(unittest.TestCase):
    def test_loaded_rate_from_env(self):
        with mock.patch.dict(
            os.environ, {"HARNESS_LOADED_HOURLY_RATE": "150"}
        ):
            self.assertEqual(Config.from_env().loaded_hourly_rate, 150.0)

    def test_invalid_rate_is_none(self):
        with mock.patch.dict(
            os.environ, {"HARNESS_LOADED_HOURLY_RATE": "abc"}
        ):
            self.assertIsNone(Config.from_env().loaded_hourly_rate)

    def test_zero_and_negative_rates_are_none(self):
        with mock.patch.dict(
            os.environ, {"HARNESS_LOADED_HOURLY_RATE": "0"}
        ):
            self.assertIsNone(Config.from_env().loaded_hourly_rate)
        with mock.patch.dict(
            os.environ, {"HARNESS_LOADED_HOURLY_RATE": "-20"}
        ):
            self.assertIsNone(Config.from_env().loaded_hourly_rate)

    def test_cli_flags_exist(self):
        args = build_parser().parse_args(
            [
                "run",
                "--agent",
                "value-finder",
                "--account",
                "Acme Corp",
                "--stub",
                "--hourly-rate",
                "150",
                "--hours-saved",
                "10",
            ]
        )
        self.assertEqual(args.hourly_rate, 150)
        self.assertEqual(args.hours_saved, 10)


if __name__ == "__main__":
    unittest.main()
