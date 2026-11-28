"""Tests for the ebr-prep agent (v0.19.0).

The ebr-prep agent prepares an executive business review for a
specific executive meeting, distinct from qbr-builder's quarterly
cadence deck: it leads with outcomes and value delivered, not
activity, and ends with an ask (a renewal, an expansion, or a
decision the executives must make). The executive summary, the value
figures, the risks and their mitigations, the ask, the executive
audience, and the last review's goals are harness-computed, so the
ask, the audience, the goals, and the block are tested directly on
synthetic inputs as well as end to end with stub tools and the stub
LLM.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed.
"""

import json
import os
import tempfile
import unittest
from datetime import date

from connectors.csv.tools import csv_crm_tools
from connectors.gmail.tools import gmail_tools
from connectors.gong.tools import gong_tools
from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from connectors.zendesk.tools import zendesk_tools
from connectors.zoom.tools import zoom_tools
from harness import AGENTS, ApprovalQueue, TokenBudget, ToolRegistry
from harness.agents import (
    _ebr_ask,
    _ebr_audience,
    _ebr_last_review_goals,
    _ebr_mitigation,
    _ebr_prep_block,
    _format_commitment_line,
)
from harness.config import Config
from harness.llm import StubLLM
from harness.router import Router

TODAY = date(2026, 11, 20)  # a Friday; pinned so due dates are stable


def _stub_registry() -> ToolRegistry:
    """Registry wired to the stub connector tools (no creds needed)."""
    config = Config.from_env()
    config.stub_mode = True
    registry = ToolRegistry()
    for tool in salesforce_tools(config):
        registry.register(tool)
    for tool in gong_tools(config):
        registry.register(tool)
    for tool in zoom_tools(config):
        registry.register(tool)
    for tool in gmail_tools(config):
        registry.register(tool)
    for tool in zendesk_tools(config):
        registry.register(tool)
    for tool in slack_tools(config):
        registry.register(tool)
    return registry


def _csv_registry(csv_path: str) -> ToolRegistry:
    """Registry wired to a CSV accounts file for the CRM tools, plus
    stub tools for calls, messages, and tickets."""
    config = Config.from_env()
    config.stub_mode = True
    config.csv_accounts_file = csv_path
    registry = ToolRegistry()
    for tool in csv_crm_tools(config):
        registry.register(tool)
    for tool in gong_tools(config):
        registry.register(tool)
    for tool in zoom_tools(config):
        registry.register(tool)
    for tool in gmail_tools(config):
        registry.register(tool)
    for tool in zendesk_tools(config):
        registry.register(tool)
    for tool in slack_tools(config):
        registry.register(tool)
    return registry


def _clean_csv_path() -> str:
    """A one-account CSV with no incidents: green health, a far-out
    renewal, no open cases, no contacts named as champion."""
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".csv", mode="w", encoding="utf-8"
    )
    tmp.write(
        "name,domain,arr,currency,renewal_date,health,owner,industry,"
        "employees,contacts,open_cases,last_touch,notes,opp_name,"
        "opp_stage,opp_amount,opp_close_date,opp_type,cases\n"
        "Quiet Co,quiet.example,40000,USD,2027-06-01,green,Jane CSM,"
        "Software,50,,0,2026-11-18,\"Steady account, no open issues.\","
        "Quiet Co - Renewal FY27,Negotiation,40000,2027-06-01,Renewal,\n"
    )
    tmp.close()
    return tmp.name


def _csv_only_registry(csv_path: str) -> ToolRegistry:
    """Registry with only the CSV accounts connector: no ticket, case,
    message, or transcript tools at all, so a clean account gathers
    nothing from any other system. Used for the no-ask gap path."""
    config = Config.from_env()
    config.stub_mode = True
    config.csv_accounts_file = csv_path
    registry = ToolRegistry()
    for tool in csv_crm_tools(config):
        registry.register(tool)
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


class TestEBRPrepRegistered(unittest.TestCase):
    def test_twenty_agents_registered(self):
        self.assertEqual(len(AGENTS), 21)
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

    def test_router_routes_explicitly_and_by_keyword(self):
        router = Router(agents=AGENTS)
        result = router.route({"text": "anything", "agent": "ebr-prep"})
        self.assertEqual(result.agent, "ebr-prep")
        self.assertIsNone(result.error)
        result = router.route(
            {"text": "prepare an ebr for Acme Corp"}
        )
        self.assertEqual(result.agent, "ebr-prep")
        result = router.route(
            {"text": "executive business review for Acme Corp"}
        )
        self.assertEqual(result.agent, "ebr-prep")

    def test_qbr_still_routes_by_its_own_keywords(self):
        # The EBR/QBR distinction: quarterly language stays with
        # qbr-builder even though both agents share "business review".
        router = Router(agents=AGENTS)
        result = router.route(
            {"text": "quarterly business review for Acme Corp"}
        )
        self.assertEqual(result.agent, "qbr-builder")
        result = router.route({"text": "qbr for Acme Corp"})
        self.assertEqual(result.agent, "qbr-builder")


class TestEBRAsk(unittest.TestCase):
    def test_named_ask_wins(self):
        ask = _ebr_ask(
            {"ask": "Approve the renewal and the expansion."},
            {},
            {},
            [],
            TODAY,
        )
        self.assertEqual(ask["kind"], "named")
        self.assertEqual(ask["text"], "Approve the renewal and the expansion.")

    def test_renewal_within_90_days(self):
        account = {"renewal_date": "2027-02-15", "arr": 120000}
        opps = {
            "opportunities": [
                {
                    "name": "Acme Corp - Renewal FY27",
                    "type": "Renewal",
                    "stage": "Negotiation",
                    "amount": 120000,
                    "close_date": "2027-02-15",
                },
                {
                    "name": "Acme Corp - Expansion (50 seats)",
                    "type": "Expansion",
                    "stage": "Qualification",
                    "amount": 45000,
                    "close_date": "2027-04-01",
                },
            ]
        }
        ask = _ebr_ask({}, account, opps, [], TODAY)
        self.assertEqual(ask["kind"], "renewal")
        self.assertIn("2027-02-15", ask["text"])
        self.assertIn("$120,000", ask["text"])
        self.assertIn("name the signer", ask["text"])

    def test_renewal_uses_account_arr_without_a_renewal_opp(self):
        account = {"renewal_date": "2027-02-15", "arr": 120000}
        ask = _ebr_ask({}, account, {"opportunities": []}, [], TODAY)
        self.assertEqual(ask["kind"], "renewal")
        self.assertIn("$120,000", ask["text"])

    def test_expansion_when_no_renewal_near(self):
        account = {"renewal_date": "2028-01-01", "arr": 120000}
        opps = {
            "opportunities": [
                {
                    "name": "Acme Corp - Expansion (50 seats)",
                    "type": "Expansion",
                    "stage": "Qualification",
                    "amount": 45000,
                    "close_date": "2027-04-01",
                }
            ]
        }
        ask = _ebr_ask({}, account, opps, [], TODAY)
        self.assertEqual(ask["kind"], "expansion")
        self.assertIn("Expansion (50 seats)", ask["text"])
        self.assertIn("$45,000", ask["text"])
        self.assertIn("name the sponsor", ask["text"])

    def test_closed_expansion_is_not_an_ask(self):
        account = {"renewal_date": "2028-01-01", "arr": 120000}
        opps = {
            "opportunities": [
                {
                    "name": "Acme Corp - Expansion (50 seats)",
                    "type": "Expansion",
                    "stage": "Closed Won",
                    "amount": 45000,
                    "close_date": "2026-10-01",
                }
            ]
        }
        ask = _ebr_ask({}, account, opps, [], TODAY)
        self.assertEqual(ask["kind"], "none")

    def test_high_signal_is_a_decision_ask(self):
        signals = [
            {
                "severity": "high",
                "kind": "support-delivery",
                "text": "Support/delivery risk: 'SSO login fails' is open.",
            }
        ]
        ask = _ebr_ask(
            {}, {"renewal_date": "2028-01-01"}, {"opportunities": []}, signals, TODAY
        )
        self.assertEqual(ask["kind"], "decision")
        self.assertIn("SSO login fails", ask["text"])

    def test_past_due_renewal_is_not_a_renewal_ask(self):
        # A renewal already past its date is a risk, not a renewal
        # ask: it falls through to the decision ask when a high
        # signal exists, and to none when nothing does.
        account = {"renewal_date": "2026-11-01", "arr": 120000}
        ask = _ebr_ask({}, account, {"opportunities": []}, [], TODAY)
        self.assertEqual(ask["kind"], "none")

    def test_nothing_anywhere_is_none_with_the_gap(self):
        ask = _ebr_ask(
            {}, {"renewal_date": "2028-01-01"}, {"opportunities": []}, [], TODAY
        )
        self.assertEqual(ask["kind"], "none")
        self.assertIn("No ask named", ask["evidence"])


class TestEBRAudience(unittest.TestCase):
    def test_job_audience_wins(self):
        people, gaps = _ebr_audience(
            {"account": "Acme Corp", "audience": "Pat Exec, Robin VP"},
            {},
            "Acme Corp",
        )
        self.assertEqual([p["name"] for p in people], ["Pat Exec", "Robin VP"])
        self.assertEqual(gaps, [])

    def test_job_audience_as_list_of_dicts(self):
        people, gaps = _ebr_audience(
            {
                "account": "Acme Corp",
                "audience": [{"name": "Pat Exec", "title": "VP Sales"}],
            },
            {},
            "Acme Corp",
        )
        self.assertEqual(people[0]["name"], "Pat Exec")
        self.assertEqual(people[0]["title"], "VP Sales")
        self.assertEqual(gaps, [])

    def test_account_contacts_with_executive_titles(self):
        account_record = {
            "contacts": [
                {"name": "Pat Exec", "title": "VP Sales"},
                {"name": "Sam Reyes", "role": "user"},
                {"name": "Dana Cole", "role": "champion"},
            ]
        }
        people, gaps = _ebr_audience(
            {"account": "Acme Corp"}, account_record, "Acme Corp"
        )
        self.assertEqual([p["name"] for p in people], ["Pat Exec"])
        self.assertEqual(gaps, [])

    def test_stakeholder_mapper_draft_names_the_executive(self):
        path = _temp_queue_path()
        _seed(
            path,
            [
                (
                    "stakeholder-mapper",
                    "Acme Corp",
                    "- Pat Exec: economic buyer, VP Sales\n"
                    "- Dana Cole: champion",
                    [],
                )
            ],
        )
        people, gaps = _ebr_audience(
            {"account": "Acme Corp", "approvals_file": path}, {}, "Acme Corp"
        )
        self.assertEqual([p["name"] for p in people], ["Pat Exec"])
        self.assertEqual(gaps, [])

    def test_no_audience_anywhere_is_a_gap(self):
        people, gaps = _ebr_audience(
            {"account": "Acme Corp"}, {"contacts": []}, "Acme Corp"
        )
        self.assertEqual(people, [])
        self.assertEqual(len(gaps), 1)
        self.assertIn("No executive audience named", gaps[0])


class TestEBRLastReviewGoals(unittest.TestCase):
    def test_goals_quoted_from_prior_qbr(self):
        path = _temp_queue_path()
        _seed(
            path,
            [
                (
                    "qbr-builder",
                    "Acme Corp",
                    "QBR for Acme Corp.\n"
                    "Next quarter: land the expansion and fix SSO.\n"
                    "Goals: 95% adoption by March.",
                    [],
                )
            ],
        )
        goals = _ebr_last_review_goals(
            {"account": "Acme Corp", "approvals_file": path}, "Acme Corp"
        )
        self.assertEqual(len(goals), 2)
        self.assertIn("Next quarter: land the expansion", goals[0])
        self.assertIn("95% adoption", goals[1])

    def test_no_prior_qbr_is_empty(self):
        goals = _ebr_last_review_goals({"account": "Acme Corp"}, "Acme Corp")
        self.assertEqual(goals, [])


class TestEBRMitigation(unittest.TestCase):
    def test_support_delivery(self):
        text = _ebr_mitigation(
            {"kind": "support-delivery", "severity": "high", "text": "x"}
        )
        self.assertIn("named owner and a fix date", text)

    def test_renewal_stalled_and_closing(self):
        for kind in ("renewal-stalled", "renewal-closing"):
            text = _ebr_mitigation(
                {"kind": kind, "severity": "medium", "text": "x"}
            )
            self.assertIn("still alive", text)

    def test_unknown_kind_is_not_a_guess(self):
        text = _ebr_mitigation(
            {"kind": "something-else", "severity": "low", "text": "x"}
        )
        self.assertIn("Confirm who owns this signal", text)


class TestEBRPrepBlock(unittest.TestCase):
    def _block(self, **overrides):
        kwargs = dict(
            summary="Acme Corp: $120,000 ARR, health green, renewal "
            "2027-02-15, 1 open risk signal(s), 0 open commitment(s).",
            figures="Quantified figures (harness-computed, not model prose):\n"
            "- Realized (measured): $0, no closed-won revenue.",
            signals=[
                {
                    "severity": "high",
                    "kind": "support-delivery",
                    "text": "Support/delivery risk: 'SSO login fails' is open.",
                }
            ],
            ask={
                "kind": "renewal",
                "text": "Ask the executives to confirm the renewal on "
                "2027-02-15 ($120,000) and name the signer.",
                "evidence": "Renewal date 2027-02-15 in the account record.",
            },
            audience=[
                {
                    "name": "Pat Exec",
                    "title": "VP Sales",
                    "evidence": "Named in the job.",
                }
            ],
            commitments=[],
            missed=[],
            goals=["Next quarter: land the expansion and fix SSO."],
            adoption_expansion=["Open expansion: Acme Corp - Expansion (50 seats) ($45,000)."],
            gaps=[],
        )
        kwargs.update(overrides)
        return _ebr_prep_block(**kwargs)

    def test_block_renders_all_sections(self):
        block = self._block()
        for section in (
            "executive summary:",
            "value delivered:",
            "risks and mitigations:",
            "adoption and expansion:",
            "the ask:",
            "executive audience:",
            "open commitments:",
            "last review goals:",
        ):
            self.assertIn(section, block)
        self.assertIn("Pat Exec", block)
        self.assertIn("confirm the renewal", block)
        self.assertIn("Next quarter: land the expansion", block)
        self.assertNotIn("gaps:", block)

    def test_block_with_no_signals_is_a_clean_bill(self):
        block = self._block(signals=[])
        self.assertIn("no signals, a clean bill for this review", block)

    def test_block_with_no_ask_names_the_gap(self):
        block = self._block(
            ask={
                "kind": "none",
                "text": "",
                "evidence": "No ask named: no renewal within 90 days.",
            },
            gaps=["No ask named: no renewal within 90 days."],
        )
        self.assertIn("the ask: No ask named", block)
        self.assertIn("gaps:", block)

    def test_block_lists_overdue_commitments(self):
        missed = [
            {
                "text": "Ship the SSO fix",
                "owner": "Sam Reyes",
                "due": date(2026, 11, 1),
                "source": "call material",
                "status": "new",
            }
        ]
        block = self._block(missed=missed)
        self.assertIn("OVERDUE", block)
        self.assertIn("Ship the SSO fix", block)
        self.assertIn("2026-11-01", block)


class TestEBRPrepEndToEnd(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_stub_run_builds_the_review_and_proves(self):
        agent = AGENTS["ebr-prep"]
        draft = agent.run(
            {"account": "Acme Corp"}, self.registry, self.llm, self.budget
        )
        self.assertEqual(draft.agent, "ebr-prep")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("salesforce_list_opportunities", draft.sources)
        self.assertIn("Acme Corp", draft.text)
        self.assertIn("executive summary:", draft.text)
        self.assertIn("value delivered:", draft.text)
        self.assertIn("risks and mitigations:", draft.text)
        self.assertIn("the ask:", draft.text)
        self.assertIn("executive audience:", draft.text)
        self.assertIn("last review goals:", draft.text)
        self.assertTrue(draft.links)
        self.assertTrue(draft.proof_passed)

    def test_named_ask_and_audience_in_the_run(self):
        agent = AGENTS["ebr-prep"]
        draft = agent.run(
            {
                "account": "Acme Corp",
                "ask": "Approve the renewal and the expansion.",
                "audience": "Pat Exec, VP Sales",
            },
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("Approve the renewal and the expansion", draft.text)
        self.assertIn("Pat Exec", draft.text)

    def test_csv_demo_run_does_not_crash(self):
        registry = _csv_registry(
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "connectors",
                "csv",
                "sample_accounts.csv",
            )
        )
        agent = AGENTS["ebr-prep"]
        draft = agent.run(
            {"account": "Acme Corp"}, registry, self.llm, self.budget
        )
        self.assertEqual(draft.agent, "ebr-prep")
        self.assertIn("executive summary:", draft.text)
        self.assertIn("the ask:", draft.text)

    def test_clean_account_names_the_gaps(self):
        csv_path = _clean_csv_path()
        try:
            registry = _csv_only_registry(csv_path)
            agent = AGENTS["ebr-prep"]
            draft = agent.run(
                {"account": "Quiet Co"}, registry, self.llm, self.budget
            )
            self.assertEqual(draft.agent, "ebr-prep")
            self.assertIn("the ask: No ask named", draft.text)
            self.assertIn("last review goals: none on record", draft.text)
            self.assertIn("executive audience: none named", draft.text)
        finally:
            os.unlink(csv_path)

    def test_composed_from_prior_drafts(self):
        path = _temp_queue_path()
        _seed(
            path,
            [
                (
                    "qbr-builder",
                    "Acme Corp",
                    "QBR for Acme Corp. Next quarter: land the expansion.",
                    [],
                ),
                (
                    "value-finder",
                    "Acme Corp",
                    "Value report: realized $0, unrealized $165,000.",
                    [],
                ),
            ],
        )
        agent = AGENTS["ebr-prep"]
        draft = agent.run(
            {"account": "Acme Corp", "approvals_file": path},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("Composed from prior drafts:", draft.text)
        self.assertIn("qbr-builder", draft.text)
        self.assertIn("value-finder", draft.text)
        self.assertIn("Next quarter: land the expansion", draft.text)


if __name__ == "__main__":
    unittest.main()
