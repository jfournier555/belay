"""Tests for the handoff agent (v0.18.0).

The handoff agent handles a sales-to-CS transition or an inherited
account: it gathers the account record, health signals, open cases
and escalations, pending drafts, renewal date and stage, open
opportunities, recent memory, and the champion and stakeholder
picture, then drafts the handoff brief — the account's current state,
open items ranked by urgency, risks flagged with evidence, what is
already in flight, and the first-week actions for the new CSM.

Open items, risks, and first-week actions are harness-computed, never
model prose, so the ranking, the gap handling, and the empty-history
case are tested directly on synthetic inputs as well as end to end
with stub tools and the stub LLM.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed.
"""

import json
import os
import tempfile
import unittest

from connectors.csv.tools import csv_crm_tools
from connectors.gmail.tools import gmail_tools
from connectors.gong.tools import gong_tools
from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from connectors.zoom.tools import zoom_tools
from harness import AGENTS, ApprovalQueue, TokenBudget, ToolRegistry
from harness.agents import (
    _extract_previous_owner,
    _handoff_block,
    _handoff_first_week,
    _handoff_open_items,
)
from harness.config import Config
from harness.escalations import EscalationStore
from harness.llm import StubLLM
from harness.registry import Tool
from harness.router import Router
from harness.scheduler import ScheduleStore


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
    return registry


def _csv_registry() -> ToolRegistry:
    """Registry wired to the CSV accounts connector (sample demo) for
    the CRM tools, plus stub tools for messages, calls, and Slack."""
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
    for tool in gong_tools(config):
        registry.register(tool)
    for tool in gmail_tools(config):
        registry.register(tool)
    for tool in zoom_tools(config):
        registry.register(tool)
    return registry


def _fake_registry(handlers: dict) -> ToolRegistry:
    """Registry whose tools return exactly the given results."""
    registry = ToolRegistry()
    for name, handler in handlers.items():
        registry.register(
            Tool(
                name=name,
                description="Fake stub for %s." % name,
                handler=handler,
            )
        )
    return registry


def _temp_path(suffix: str = ".json") -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
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


class TestHandoffRegistered(unittest.TestCase):
    def test_twenty_agents_registered(self):
        self.assertEqual(len(AGENTS), 20)
        agent = AGENTS["handoff"]
        self.assertTrue(agent.implemented)
        self.assertEqual(agent.name, "handoff")
        self.assertEqual(agent.display_name, "Handoff")
        self.assertIn("handoff", agent.keywords)
        self.assertIn("inherited account", agent.keywords)


class TestExtractPreviousOwner(unittest.TestCase):
    def test_job_names_the_previous_owner(self):
        name, source = _extract_previous_owner(
            {"account": "Acme Corp", "previous_owner": "Sam Reed"},
            "",
            "",
        )
        self.assertEqual((name, source), ("Sam Reed", "job"))

    def test_job_from_owner_names_the_previous_owner(self):
        name, source = _extract_previous_owner(
            {"account": "Acme Corp", "from_owner": "Sam Reed"},
            "",
            "",
        )
        self.assertEqual((name, source), ("Sam Reed", "job"))

    def test_prior_draft_colon_form_names_the_previous_owner(self):
        composed = (
            "Prior agent drafts for this account:\n"
            "briefer (2026-11-21T10:00:00+00:00): Previous owner: "
            "Sam Reed handed the account over in October."
        )
        name, source = _extract_previous_owner({}, composed, "")
        self.assertEqual((name, source), ("Sam Reed", "prior draft"))

    def test_prose_about_a_previous_owner_names_nobody(self):
        # "the previous owner was great" is a sentence, not a name.
        composed = (
            "The previous owner was great but the account moved "
            "teams anyway."
        )
        name, source = _extract_previous_owner({}, composed, "")
        self.assertEqual((name, source), (None, None))

    def test_no_previous_owner_anywhere_is_a_gap(self):
        name, source = _extract_previous_owner(
            {"account": "Acme Corp"}, "", ""
        )
        self.assertEqual((name, source), (None, None))


class TestHandoffOpenItems(unittest.TestCase):
    def test_high_signals_rank_first(self):
        signals = [
            {"severity": "medium", "text": "Renewal closes in 20 days."},
            {"severity": "high", "text": "SSO login fails for new hires."},
            {"severity": "low", "text": "Minor usage dip."},
        ]
        items = _handoff_open_items(signals, [], [])
        self.assertEqual(
            [i["urgency"] for i in items], ["high", "medium", "low"]
        )
        self.assertEqual(
            items[0]["label"], "SSO login fails for new hires."
        )
        self.assertEqual(items[0]["source"], "risk signals")

    def test_escalations_and_pending_drafts_join_the_ranking(self):
        store = EscalationStore(None)
        record = store.create(
            "Acme Corp",
            "sso-outage",
            "support-delivery",
            "high",
            "SSO outage, new hires blocked.",
        )
        path = _temp_path()
        try:
            queue = _seed(
                path,
                [
                    (
                        "renewal-desk",
                        "Acme Corp",
                        "Renewal plan draft.",
                        [],
                    ),
                ],
            )
            pending = queue.list_pending()
            items = _handoff_open_items([], [record], pending)
            self.assertEqual(len(items), 2)
            # An open escalation is medium; a pending draft is low.
            self.assertEqual(items[0]["urgency"], "medium")
            self.assertIn("Escalation", items[0]["label"])
            self.assertEqual(items[1]["urgency"], "low")
            self.assertIn("renewal-desk", items[1]["label"])
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_escalated_record_is_high(self):
        store = EscalationStore(None)
        record = store.create(
            "Acme Corp",
            "sso-outage",
            "support-delivery",
            "high",
            "SSO outage, new hires blocked.",
        )
        record.status = "escalated"
        items = _handoff_open_items([], [record], [])
        self.assertEqual(items[0]["urgency"], "high")

    def test_no_signals_no_items(self):
        self.assertEqual(_handoff_open_items([], [], []), [])


class TestHandoffFirstWeek(unittest.TestCase):
    def setUp(self):
        self.record = {
            "health": "green",
            "arr": 120000,
            "owner": "Jane CSM",
            "renewal_date": "2027-02-15",
        }

    def test_closing_renewal_is_first(self):
        actions = _handoff_first_week(
            self.record,
            12,
            [],
            [],
            [],
            None,
            None,
            "Sam Reed",
            True,
        )
        self.assertIn("Confirm the renewal is still alive", actions[0])
        self.assertIn("2027-02-15", actions[0])

    def test_no_champion_means_find_one(self):
        actions = _handoff_first_week(
            self.record,
            80,
            [],
            [],
            [],
            None,
            None,
            "Sam Reed",
            True,
        )
        self.assertTrue(
            any("Find the champion" in a for a in actions)
        )

    def test_named_champion_means_meet_them(self):
        actions = _handoff_first_week(
            self.record,
            80,
            [],
            [],
            [],
            {"name": "Dana Cole", "email": "dana@acme.example"},
            "prior draft",
            "Sam Reed",
            True,
        )
        self.assertTrue(
            any(
                "Meet Dana Cole in week one" in a for a in actions
            )
        )

    def test_no_history_means_introduce_yourself(self):
        actions = _handoff_first_week(
            self.record,
            80,
            [],
            [],
            [],
            None,
            None,
            None,
            False,
        )
        self.assertTrue(
            any("Introduce yourself to the account" in a for a in actions)
        )

    def test_high_items_are_owned_first(self):
        actions = _handoff_first_week(
            self.record,
            80,
            [
                {
                    "urgency": "high",
                    "label": "SSO login fails for new hires.",
                    "source": "risk signals",
                }
            ],
            [],
            [],
            None,
            None,
            "Sam Reed",
            True,
        )
        self.assertTrue(
            any("Own the 1 high-urgency open item(s)" in a for a in actions)
        )


class TestHandoffBlock(unittest.TestCase):
    def setUp(self):
        self.record = {
            "health": "green",
            "arr": 120000,
            "owner": "Jane CSM",
            "renewal_date": "2027-02-15",
        }
        self.signals = [
            {
                "severity": "high",
                "kind": "support-delivery",
                "text": "SSO login fails for new hires.",
            }
        ]
        self.items = [
            {
                "urgency": "high",
                "label": "SSO login fails for new hires.",
                "source": "risk signals",
            }
        ]
        self.people = [
            {"name": "Dana Cole", "side": "customer"},
            {"name": "Sam Reyes", "side": "customer"},
        ]
        self.actions = [
            "Own the 1 high-urgency open item(s) first: SSO login "
            "fails for new hires. (source: risk signals).",
            "Meet Dana Cole in week one and confirm they still back "
            "the renewal (source: prior draft).",
        ]

    def _block(self, **overrides):
        kwargs = {
            "account": "Acme Corp",
            "account_record": self.record,
            "renewal_days": 80,
            "signals": self.signals,
            "open_items": self.items,
            "champion": {"name": "Dana Cole", "email": "dana@acme.example"},
            "champion_source": "prior draft",
            "people": self.people,
            "pending": [],
            "escalations": [],
            "schedules": [],
            "previous_owner": "Sam Reed",
            "previous_owner_source": "job",
            "actions": self.actions,
            "gaps": [],
        }
        kwargs.update(overrides)
        return _handoff_block(**kwargs)

    def test_full_block_names_every_section(self):
        block = self._block()
        self.assertIn("Account state:", block)
        self.assertIn("health green", block)
        self.assertIn("ARR 120000", block)
        self.assertIn("owner Jane CSM", block)
        self.assertIn("renewal 2027-02-15 (80 days)", block)
        self.assertIn("Open items (ranked by urgency):", block)
        self.assertIn("[high] SSO login fails for new hires.", block)
        self.assertIn("Risks:", block)
        self.assertIn("Champion: Dana Cole (source: prior draft).", block)
        self.assertIn("Stakeholders on record: Dana Cole, Sam Reyes.", block)
        self.assertIn("Already in flight:", block)
        self.assertIn("No drafts pending in the approval queue.", block)
        self.assertIn("Previous owner:", block)
        self.assertIn("Sam Reed (source: job).", block)
        self.assertIn("First week:", block)
        self.assertIn("1. Own the 1 high-urgency open item(s)", block)

    def test_empty_state_names_the_gaps(self):
        block = self._block(
            signals=[],
            open_items=[],
            champion=None,
            champion_source=None,
            people=[],
            previous_owner=None,
            previous_owner_source=None,
            actions=[
                "Find the champion: no champion is on record for "
                "this account (source: account record and prior "
                "drafts).",
                "Introduce yourself to the account: no previous "
                "owner or history is on record, so gather the "
                "context the records do not hold (source: none on "
                "file).",
            ],
            gaps=[
                "No previous owner named in the job, in any prior "
                "draft, or in memory: the new CSM starts without a "
                "named handover contact.",
                "No prior history for Acme Corp: no prior drafts "
                "and no memory records.",
            ],
        )
        self.assertIn("No open items in the gathered records.", block)
        self.assertIn("No high or medium risk signals in this run.", block)
        self.assertIn("Champion: none on record for Acme Corp.", block)
        self.assertIn("No previous owner named in the job", block)
        self.assertIn("Gaps:", block)


class TestHandoffEndToEnd(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_runs_with_previous_owner_and_champion_and_proves(self):
        agent = AGENTS["handoff"]
        draft = agent.run(
            {
                "account": "Acme Corp",
                "previous_owner": "Sam Reed",
                "champion": "Dana Cole",
            },
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "handoff")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("Account state:", draft.text)
        self.assertIn("health green", draft.text)
        self.assertIn("Champion: Dana Cole (source: job).", draft.text)
        self.assertIn("Sam Reed (source: job).", draft.text)
        self.assertIn("Open items (ranked by urgency):", draft.text)
        self.assertIn(
            "[high] Support/delivery risk: 'SSO login fails for new "
            "hires' is open in 1 place(s)",
            draft.text,
        )
        self.assertIn("Already in flight:", draft.text)
        self.assertIn("First week:", draft.text)
        self.assertTrue(draft.links)
        self.assertTrue(draft.proof_passed)

    def test_no_history_is_an_empty_state_not_a_crash(self):
        agent = AGENTS["handoff"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "handoff")
        self.assertIn("Champion: none on record for Acme Corp.", draft.text)
        self.assertIn("No previous owner named in the job", draft.text)
        self.assertIn("No prior history for Acme Corp", draft.text)
        self.assertIn("Introduce yourself to the account", draft.text)
        self.assertTrue(draft.proof_passed)

    def test_csv_sample_demo_brief(self):
        registry = _csv_registry()
        agent = AGENTS["handoff"]
        draft = agent.run(
            {"account": "Acme Corp"},
            registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "handoff")
        self.assertIn("health green", draft.text)
        self.assertIn("owner Jane CSM", draft.text)
        # The CSV contacts join the stakeholder picture.
        self.assertIn("Dana Cole", draft.text)
        self.assertIn("Sam Reyes", draft.text)
        self.assertIn(
            "[high] Support/delivery risk: 'SSO login fails for new "
            "hires' is open in 1 place(s)",
            draft.text,
        )
        self.assertTrue(draft.proof_passed)

    def test_composed_path_builds_from_prior_drafts(self):
        path = _temp_path()
        try:
            _seed(
                path,
                [
                    (
                        "briefer",
                        "Acme Corp",
                        "Pre-meeting brief: Acme Corp is green, "
                        "renewal 2027-02-15. Champion: Dana Cole is "
                        "the strongest advocate in the account.",
                        [],
                    ),
                    (
                        "risk-watcher",
                        "Acme Corp",
                        "Risk brief: SSO login fails for new hires "
                        "is a high signal.",
                        [],
                    ),
                ],
            )
            agent = AGENTS["handoff"]
            draft = agent.run(
                {
                    "account": "Acme Corp",
                    "approvals_file": path,
                },
                self.registry,
                self.llm,
                self.budget,
            )
            self.assertIn(
                "Champion: Dana Cole (source: prior draft).", draft.text
            )
            self.assertIn("Composed from prior drafts", draft.text)
            self.assertIn("briefer, risk-watcher", draft.text)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_in_flight_items_are_named(self):
        approvals_path = _temp_path()
        escalations_path = _temp_path()
        schedules_path = _temp_path()
        try:
            _seed(
                approvals_path,
                [
                    (
                        "renewal-desk",
                        "Acme Corp",
                        "Renewal plan draft awaiting a decision.",
                        [],
                    ),
                ],
            )
            store = EscalationStore(escalations_path)
            store.create(
                "Acme Corp",
                "sso-outage",
                "support-delivery",
                "high",
                "SSO outage, new hires blocked.",
            )
            schedules = ScheduleStore(schedules_path)
            schedules.add(
                {
                    "agent": "briefer",
                    "account": "Acme Corp",
                    "cadence": "daily",
                    "time": "07:00",
                    "timezone": "America/Los_Angeles",
                },
                valid_agents=set(AGENTS),
            )
            agent = AGENTS["handoff"]
            draft = agent.run(
                {
                    "account": "Acme Corp",
                    "approvals_file": approvals_path,
                    "escalations_file": escalations_path,
                    "schedules_file": schedules_path,
                },
                self.registry,
                self.llm,
                self.budget,
            )
            self.assertIn("renewal-desk draft, submitted", draft.text)
            self.assertIn("sso-outage", draft.text)
            self.assertIn("briefer", draft.text)
            self.assertIn("Decide the 1 draft(s) awaiting approval", draft.text)
            self.assertIn("Read the 1 open escalation(s)", draft.text)
        finally:
            for path in (
                approvals_path,
                escalations_path,
                schedules_path,
            ):
                if os.path.exists(path):
                    os.unlink(path)

    def test_router_routes_explicitly_and_by_keyword(self):
        router = Router(agents=AGENTS, llm=None)
        result = router.route({"text": "anything", "agent": "handoff"})
        self.assertEqual(result.agent, "handoff")
        self.assertEqual(result.confidence, "high")
        self.assertIsNone(result.error)
        result = router.route(
            {"text": "hand off the Acme Corp account to the new CSM"}
        )
        self.assertEqual(result.agent, "handoff")
        result = router.route(
            {"text": "draft the inherited account handoff for Acme Corp"}
        )
        self.assertEqual(result.agent, "handoff")


if __name__ == "__main__":
    unittest.main()
