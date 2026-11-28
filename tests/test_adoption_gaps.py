"""Tests for the adoption-gaps agent (v0.18.0).

The adoption-gaps agent finds features and teams bought but never
used: unused seats, unused features, unonboarded teams, ranked by
value at stake. Ranking and value at stake are harness-computed,
never model prose, so the gap detection, the ranking, and the block
are tested directly on synthetic inputs as well as end to end with
stub tools and the stub LLM.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed.
"""

import json
import os
import tempfile
import unittest

from connectors.csv.tools import csv_crm_tools
from connectors.gmail.tools import gmail_tools
from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from harness import AGENTS, ApprovalQueue, TokenBudget, ToolRegistry
from harness.agents import (
    _adoption_gaps,
    _adoption_gaps_block,
    _adoption_usage,
    _parse_usage_number,
    _rank_adoption_gaps,
)
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
    for tool in gmail_tools(config):
        registry.register(tool)
    return registry


def _csv_registry() -> ToolRegistry:
    """Registry wired to the CSV accounts connector (sample demo) for
    the CRM tools, plus stub tools for messages and Slack."""
    config = Config.from_env()
    config.stub_mode = True
    config.csv_accounts_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "connectors",
        "csv",
        "sample_accounts.csv",
    )
    registry = ToolRegistry()
    for tool in csv_crm_tools(config):
        registry.register(tool)
    for tool in slack_tools(config):
        registry.register(tool)
    for tool in gmail_tools(config):
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


class TestAdoptionGapsRegistered(unittest.TestCase):
    def test_twenty_agents_registered(self):
        self.assertEqual(len(AGENTS), 20)
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
        router = Router(agents=AGENTS, llm=None)
        result = router.route(
            {"text": "anything", "agent": "adoption-gaps"}
        )
        self.assertEqual(result.agent, "adoption-gaps")
        self.assertEqual(result.confidence, "high")
        self.assertIsNone(result.error)
        result = router.route(
            {"text": "find the adoption gaps for Acme Corp"}
        )
        self.assertEqual(result.agent, "adoption-gaps")
        result = router.route({"text": "unused seats at Acme Corp"})
        self.assertEqual(result.agent, "adoption-gaps")


class TestParseUsageNumber(unittest.TestCase):
    def test_numbers_pass_through(self):
        self.assertEqual(_parse_usage_number(200), 200)
        self.assertEqual(_parse_usage_number(120.9), 120)

    def test_strings_parse_leniently(self):
        self.assertEqual(_parse_usage_number("200"), 200)
        self.assertEqual(_parse_usage_number("1,200"), 1200)
        self.assertEqual(_parse_usage_number("$120000"), 120000)

    def test_garbage_is_none_not_a_crash(self):
        self.assertIsNone(_parse_usage_number(None))
        self.assertIsNone(_parse_usage_number("lots"))
        self.assertIsNone(_parse_usage_number(True))
        self.assertIsNone(_parse_usage_number([1, 2]))


class TestAdoptionUsage(unittest.TestCase):
    def test_full_job_usage_normalizes(self):
        usage, missing = _adoption_usage(
            {
                "usage": {
                    "seats_purchased": 200,
                    "seats_active": 120,
                    "features": [
                        {"name": "SSO", "entitled": True, "used": False},
                        {"name": "Reports", "entitled": True, "used": True},
                    ],
                    "teams": [
                        {"name": "Support", "onboarded": False, "headcount": 40},
                        {"name": "Sales", "onboarded": True, "headcount": 60},
                    ],
                }
            },
            {},
        )
        self.assertEqual(usage["seats_purchased"], 200)
        self.assertEqual(usage["seats_active"], 120)
        self.assertEqual(len(usage["features"]), 2)
        self.assertEqual(len(usage["teams"]), 2)
        self.assertEqual(missing, [])

    def test_no_usage_names_everything_missing(self):
        usage, missing = _adoption_usage({}, {})
        self.assertIsNone(usage["seats_purchased"])
        self.assertEqual(usage["features"], [])
        self.assertEqual(usage["teams"], [])
        self.assertEqual(len(missing), 3)
        self.assertTrue(any("Seat counts" in m for m in missing))
        self.assertTrue(any("Feature usage" in m for m in missing))
        self.assertTrue(any("Team onboarding" in m for m in missing))

    def test_seats_purchased_without_active_names_the_gap(self):
        _usage, missing = _adoption_usage(
            {"usage": {"seats_purchased": 200}}, {}
        )
        self.assertTrue(any("Seats active" in m for m in missing))
        self.assertFalse(any("Seat counts" in m for m in missing))

    def test_malformed_usage_block_is_a_gap(self):
        _usage, missing = _adoption_usage({"usage": "200 seats"}, {})
        self.assertTrue(
            any("not a mapping" in m for m in missing)
        )

    def test_feature_without_a_name_is_skipped(self):
        usage, _missing = _adoption_usage(
            {"usage": {"features": [{"entitled": True, "used": False}]}},
            {},
        )
        self.assertEqual(usage["features"], [])


class TestAdoptionGaps(unittest.TestCase):
    def test_unused_seats_valued_from_arr(self):
        usage = {
            "seats_purchased": 200,
            "seats_active": 120,
            "features": [],
            "teams": [],
        }
        account = {"arr": 120000, "employees": 450, "contacts": []}
        gaps = _adoption_gaps(usage, account)
        self.assertEqual(len(gaps), 1)
        gap = gaps[0]
        self.assertEqual(gap["kind"], "seats")
        self.assertEqual(gap["item"], "80 unused seats")
        self.assertEqual(gap["value_at_stake"], 48000)
        self.assertEqual(gap["source"], "job usage")

    def test_unused_seats_without_arr_is_unknown_value(self):
        usage = {
            "seats_purchased": 200,
            "seats_active": 120,
            "features": [],
            "teams": [],
        }
        gaps = _adoption_gaps(usage, {})
        self.assertEqual(len(gaps), 1)
        self.assertIsNone(gaps[0]["value_at_stake"])

    def test_all_seats_active_is_no_gap(self):
        usage = {
            "seats_purchased": 200,
            "seats_active": 200,
            "features": [],
            "teams": [],
        }
        self.assertEqual(_adoption_gaps(usage, {}), [])

    def test_entitled_unused_feature_is_a_gap(self):
        usage = {
            "seats_purchased": None,
            "seats_active": None,
            "features": [
                {"name": "SSO", "entitled": True, "used": False,
                 "value_at_stake": None},
                {"name": "Reports", "entitled": True, "used": True,
                 "value_at_stake": None},
                {"name": "API", "entitled": False, "used": False,
                 "value_at_stake": None},
            ],
            "teams": [],
        }
        gaps = _adoption_gaps(usage, {})
        self.assertEqual([g["item"] for g in gaps], ["SSO"])
        self.assertEqual(gaps[0]["kind"], "features")

    def test_unonboarded_team_valued_from_headcount(self):
        usage = {
            "seats_purchased": None,
            "seats_active": None,
            "features": [],
            "teams": [
                {"name": "Support", "onboarded": False, "headcount": 40},
                {"name": "Sales", "onboarded": True, "headcount": 60},
            ],
        }
        account = {"arr": 120000, "employees": 450, "contacts": []}
        gaps = _adoption_gaps(usage, account)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["item"], "Support")
        self.assertEqual(gaps[0]["value_at_stake"], 10667)

    def test_few_contacts_for_a_big_org_is_a_coverage_gap(self):
        usage = {
            "seats_purchased": None,
            "seats_active": None,
            "features": [],
            "teams": [],
        }
        account = {
            "arr": 120000,
            "employees": 450,
            "contacts": [
                {"name": "Dana Cole", "email": "dana@acme.example"},
                {"name": "Sam Reyes", "email": "sam@acme.example"},
            ],
        }
        gaps = _adoption_gaps(usage, account)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["item"], "Org coverage")
        self.assertEqual(gaps[0]["kind"], "teams")
        self.assertIn("2 named contacts for 450 employees", gaps[0]["evidence"])

    def test_no_contacts_is_no_coverage_gap(self):
        usage = {
            "seats_purchased": None,
            "seats_active": None,
            "features": [],
            "teams": [],
        }
        self.assertEqual(
            _adoption_gaps(usage, {"arr": 120000, "employees": 450}),
            [],
        )

    def test_many_contacts_is_no_coverage_gap(self):
        usage = {
            "seats_purchased": None,
            "seats_active": None,
            "features": [],
            "teams": [],
        }
        account = {
            "employees": 450,
            "contacts": [{"name": "Person %d" % i} for i in range(50)],
        }
        self.assertEqual(_adoption_gaps(usage, account), [])


class TestRankAdoptionGaps(unittest.TestCase):
    def test_value_first_then_kind_order(self):
        gaps = [
            {"kind": "teams", "item": "Support", "value_at_stake": 10667},
            {"kind": "seats", "item": "80 unused seats",
             "value_at_stake": 48000},
            {"kind": "features", "item": "SSO", "value_at_stake": None},
            {"kind": "teams", "item": "Org coverage",
             "value_at_stake": None},
        ]
        ranked = _rank_adoption_gaps(gaps)
        self.assertEqual(
            [g["item"] for g in ranked],
            ["80 unused seats", "Support", "SSO", "Org coverage"],
        )


class TestAdoptionGapsBlock(unittest.TestCase):
    def test_block_lists_gaps_with_value_and_next_step(self):
        gaps = [
            {
                "kind": "seats",
                "item": "80 unused seats",
                "evidence": "120 of 200 seats active (last-login data)",
                "source": "job usage",
                "value_at_stake": 48000,
                "next_step": "Right-size or reassign.",
            }
        ]
        block = _adoption_gaps_block(
            gaps, [], "Job usage: 120 of 200 seats active."
        )
        self.assertIn(
            "adoption gaps (ranked by value at stake):", block
        )
        self.assertIn("80 unused seats", block)
        self.assertIn("$48,000 at stake", block)
        self.assertIn("usage data:", block)
        self.assertIn("next steps:", block)
        self.assertIn("Right-size or reassign.", block)

    def test_unknown_value_says_so(self):
        gaps = [
            {
                "kind": "teams",
                "item": "Org coverage",
                "evidence": "2 named contacts for 450 employees",
                "source": "salesforce_get_account",
                "value_at_stake": None,
                "next_step": "Map the org.",
            }
        ]
        block = _adoption_gaps_block(gaps, [], "Account record: 450 employees.")
        self.assertIn("value at stake unknown", block)

    def test_empty_gaps_names_the_missing_data(self):
        block = _adoption_gaps_block(
            [],
            ["Seat counts: no seats purchased on record."],
            "No job usage supplied.",
        )
        self.assertIn("No adoption gaps found in the gathered data.", block)
        self.assertIn("Seat counts: no seats purchased on record.", block)
        self.assertIn("Collect the missing usage data first", block)


class TestAdoptionGapsRuns(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_runs_with_no_usage_and_proves(self):
        agent = AGENTS["adoption-gaps"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "adoption-gaps")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("No adoption gaps found in the gathered data.", draft.text)
        self.assertIn("usage data:", draft.text)
        self.assertIn("No job usage supplied.", draft.text)
        self.assertIn("Seat counts: no seats purchased on record.", draft.text)
        self.assertTrue(all(p["passed"] for p in draft.proof))

    def test_runs_with_job_usage_and_values_the_gaps(self):
        agent = AGENTS["adoption-gaps"]
        draft = agent.run(
            {
                "account": "Acme Corp",
                "usage": {
                    "seats_purchased": 200,
                    "seats_active": 120,
                    "features": [
                        {"name": "SSO", "entitled": True, "used": False},
                    ],
                    "teams": [
                        {"name": "Support", "onboarded": False,
                         "headcount": 40},
                    ],
                },
            },
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("80 unused seats", draft.text)
        self.assertIn("$48,000 at stake", draft.text)
        self.assertIn("SSO", draft.text)
        self.assertIn("Support", draft.text)
        self.assertTrue(all(p["passed"] for p in draft.proof))

    def test_csv_demo_runs_and_flags_org_coverage(self):
        agent = AGENTS["adoption-gaps"]
        draft = agent.run(
            {"account": "Acme Corp"},
            _csv_registry(),
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "adoption-gaps")
        self.assertIn("Org coverage", draft.text)
        self.assertIn(
            "2 named contacts for 450 employees", draft.text
        )
        self.assertIn("usage data:", draft.text)
        self.assertIn("Seat counts: no seats purchased on record.", draft.text)

    def test_composed_path_builds_from_prior_drafts(self):
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "expansion-spotter",
                        "Acme Corp",
                        "Expansion plan: usage gaps in the Support team, "
                        "which never adopted SSO.",
                        [],
                    ),
                    (
                        "value-finder",
                        "Acme Corp",
                        "Unrealized outcomes: 80 seats unused, worth "
                        "about $48,000 at the current per-seat rate.",
                        [],
                    ),
                ],
            )
            agent = AGENTS["adoption-gaps"]
            draft = agent.run(
                {
                    "account": "Acme Corp",
                    "approvals_file": path,
                },
                self.registry,
                self.llm,
                self.budget,
            )
            self.assertIn("Composed from prior drafts", draft.text)
            self.assertIn(
                "expansion-spotter, value-finder", draft.text
            )
        finally:
            if os.path.exists(path):
                os.unlink(path)
