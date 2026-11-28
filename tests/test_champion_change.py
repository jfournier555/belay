"""Tests for the champion-change agent (v0.18.0).

The champion-change agent catches a champion departure and rebuilds
the relationship map: the departed champion with the evidence, the
relationship-map impact, ranked successor candidates, and the rebuild
plan from the Champion Departure playbook's 7/14/30 spine.

Detection is harness-computed, never model prose, so the departure
signals, the successor ranking, and the title rule are tested
directly on synthetic tool results as well as end to end with stub
tools and the stub LLM.

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
    _champion_change_block,
    _champion_departure_signals,
    _champion_people,
    _extract_champion,
    _extract_successor,
    _successor_candidates,
    _title_with_link,
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


class TestChampionChangeRegistered(unittest.TestCase):
    def test_seventeen_agents_registered(self):
        self.assertEqual(len(AGENTS), 17)
        agent = AGENTS["champion-change"]
        self.assertTrue(agent.implemented)
        self.assertEqual(agent.name, "champion-change")
        self.assertEqual(agent.display_name, "Champion Change")
        self.assertIn("successor", agent.keywords)
        self.assertIn("champion left", agent.keywords)


class TestExtractChampion(unittest.TestCase):
    def test_job_names_the_champion(self):
        name, source = _extract_champion(
            {"account": "Acme Corp", "champion": "Dana Cole"}, "", ""
        )
        self.assertEqual((name, source), ("Dana Cole", "job"))

    def test_job_text_names_the_champion(self):
        name, source = _extract_champion(
            {"text": "check on champion: Dana Cole for Acme Corp"},
            "",
            "",
        )
        self.assertEqual((name, source), ("Dana Cole", "job"))

    def test_prior_draft_map_block_names_the_champion(self):
        composed = (
            "Prior agent drafts for this account:\n"
            "stakeholder-mapper (2026-11-21T10:00:00+00:00): "
            "Stakeholder map (harness-computed): Customer "
            "stakeholders: - Dana Cole: champion (VP CS), influence "
            "high. Evidence: Call participant (2 calls)."
        )
        name, source = _extract_champion({}, composed, "")
        self.assertEqual((name, source), ("Dana Cole", "prior draft"))

    def test_prior_draft_colon_form_names_the_champion(self):
        composed = (
            "advocate-finder (2026-11-21T10:00:00+00:00): Champion: "
            "Dana Cole is the strongest advocate in the account."
        )
        name, source = _extract_champion({}, composed, "")
        self.assertEqual((name, source), ("Dana Cole", "prior draft"))

    def test_prose_about_a_champion_names_nobody(self):
        # "the champion is solid" is a sentence, not a name.
        composed = (
            "The champion is solid but there is no second "
            "relationship yet."
        )
        name, source = _extract_champion({}, composed, "")
        self.assertEqual((name, source), (None, None))

    def test_no_champion_anywhere_is_a_gap(self):
        name, source = _extract_champion({"account": "Acme Corp"}, "", "")
        self.assertEqual((name, source), (None, None))


class TestDepartureSignals(unittest.TestCase):
    def setUp(self):
        self.champion = {"name": "Dana Cole", "email": "dana@acme.example"}

    def test_moved_on_excerpt_is_high(self):
        results = {
            "gmail_search_messages": {
                "matches": [
                    {
                        "author": "Acme Admin <admin@acme.example>",
                        "subject": "Dana Cole",
                        "excerpt": (
                            "Dana Cole has left the company, she moved "
                            "to RivalWorks last week."
                        ),
                    }
                ]
            }
        }
        signals = _champion_departure_signals(
            self.champion, results, known_before=True
        )
        kinds = {s["kind"]: s for s in signals}
        self.assertIn("champion-moved-on", kinds)
        self.assertEqual(kinds["champion-moved-on"]["severity"], "high")
        self.assertIn("RivalWorks", kinds["champion-moved-on"]["text"])
        # Moved on, not merely quiet: the excerpt names her.
        self.assertNotIn("champion-quiet", kinds)

    def test_promoted_is_medium_not_a_loss(self):
        results = {
            "gong_search_transcripts": {
                "matches": [
                    {
                        "speaker": "acme-admin",
                        "excerpt": (
                            "Dana Cole was promoted to VP Customer "
                            "Success last month, she still runs the "
                            "account."
                        ),
                    }
                ]
            }
        }
        signals = _champion_departure_signals(
            self.champion, results, known_before=True
        )
        kinds = {s["kind"]: s for s in signals}
        self.assertIn("champion-promoted", kinds)
        self.assertEqual(kinds["champion-promoted"]["severity"], "medium")
        self.assertNotIn("champion-moved-on", kinds)

    def test_bounce_is_high(self):
        results = {
            "gmail_search_messages": {
                "matches": [
                    {
                        "author": "Mail Delivery Subsystem",
                        "subject": "Delivery Status Notification",
                        "excerpt": (
                            "Delivery Status Notification: message to "
                            "dana@acme.example undeliverable, mailbox "
                            "unavailable."
                        ),
                    }
                ]
            }
        }
        signals = _champion_departure_signals(
            self.champion, results, known_before=True
        )
        kinds = {s["kind"]: s for s in signals}
        self.assertIn("champion-bounce", kinds)
        self.assertEqual(kinds["champion-bounce"]["severity"], "high")

    def test_bounce_without_the_champion_is_not_a_signal(self):
        results = {
            "gmail_search_messages": {
                "matches": [
                    {
                        "author": "Mail Delivery Subsystem",
                        "subject": "Delivery Status Notification",
                        "excerpt": (
                            "Message to someone-else@example.com "
                            "undeliverable, mailbox unavailable."
                        ),
                    }
                ]
            }
        }
        signals = _champion_departure_signals(
            self.champion, results, known_before=True
        )
        # The bounce names nobody relevant, and Dana appears nowhere:
        # only the quiet signal fires.
        self.assertEqual(
            [s["kind"] for s in signals], ["champion-quiet"]
        )

    def test_gone_quiet_is_medium(self):
        signals = _champion_departure_signals(
            self.champion, {}, known_before=True
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["kind"], "champion-quiet")
        self.assertEqual(signals[0]["severity"], "medium")
        self.assertIn("Prior drafts confirm", signals[0]["text"])

    def test_gone_quiet_without_baseline_says_so(self):
        signals = _champion_departure_signals(
            self.champion, {}, known_before=False
        )
        self.assertEqual(len(signals), 1)
        self.assertIn("unconfirmed against history", signals[0]["text"])

    def test_active_champion_is_no_signal(self):
        results = {
            "slack_search_messages": {
                "matches": [
                    {
                        "author": "Dana Cole",
                        "excerpt": (
                            "Dana Cole asked about the renewal date "
                            "in the channel."
                        ),
                    }
                ]
            }
        }
        signals = _champion_departure_signals(
            self.champion, results, known_before=True
        )
        self.assertEqual(signals, [])

    def test_signals_rank_high_first(self):
        results = {
            "gmail_search_messages": {
                "matches": [
                    {
                        "author": "Acme Admin <admin@acme.example>",
                        "subject": "Dana Cole",
                        "excerpt": (
                            "Dana Cole has left the company."
                        ),
                    }
                ]
            },
            "slack_search_messages": {"matches": []},
        }
        signals = _champion_departure_signals(
            self.champion, results, known_before=True
        )
        self.assertEqual(signals[0]["severity"], "high")
        self.assertEqual(signals[0]["kind"], "champion-moved-on")


class TestSuccessorRanking(unittest.TestCase):
    def _people(self):
        # Two customer stakeholders besides the champion, plus the
        # champion herself. Sam shares calls with Dana; Priya does not.
        return [
            {
                "key": "dana cole",
                "name": "Dana Cole",
                "title": "",
                "side": "customer",
                "sources": {"gmail_search_messages"},
                "evidence": ["Email author (2 messages)"],
                "champion_status": "active",
                "champion_since": None,
                "moved_to": None,
                "promoted": None,
                "is_owner": False,
                "handle_only": False,
                "emails": {"dana@acme.example"},
            },
            {
                "key": "sam reyes",
                "name": "Sam Reyes",
                "title": "",
                "side": "customer",
                "sources": {
                    "gmail_search_messages",
                    "gong_list_calls",
                    "slack_search_messages",
                },
                "evidence": [
                    "Email author (1 message)",
                    "Call participant (2 calls)",
                    "Slack author (1 message)",
                ],
                "champion_status": None,
                "champion_since": None,
                "moved_to": None,
                "promoted": None,
                "is_owner": False,
                "handle_only": False,
                "emails": {"sam@acme.example"},
            },
            {
                "key": "priya nair",
                "name": "Priya Nair",
                "title": "",
                "side": "customer",
                "sources": {"gmail_search_messages"},
                "evidence": ["Email author (1 message)"],
                "champion_status": None,
                "champion_since": None,
                "moved_to": None,
                "promoted": None,
                "is_owner": False,
                "handle_only": False,
                "emails": {"priya@beaconhealth.example"},
            },
            {
                "key": "jane csm",
                "name": "Jane CSM",
                "title": "",
                "side": "internal",
                "sources": {"salesforce_get_account"},
                "evidence": ["Account owner (Salesforce)"],
                "champion_status": None,
                "champion_since": None,
                "moved_to": None,
                "promoted": None,
                "is_owner": True,
                "handle_only": False,
                "emails": {"jane@cs-team.example"},
            },
        ]

    def test_connected_candidate_ranks_first(self):
        people = self._people()
        relationships = [
            {"a": "dana cole", "b": "sam reyes", "calls": 2, "threads": 1}
        ]
        ranked = _successor_candidates(
            people, relationships, "dana cole", {}
        )
        names = [c["name"] for c in ranked]
        self.assertEqual(names, ["Sam Reyes", "Priya Nair"])
        # Sam: +2 connected, +1 three-plus sources. Priya: nothing.
        self.assertEqual(ranked[0]["score"], 3)
        self.assertEqual(ranked[1]["score"], 0)
        # The champion herself and our side are never candidates.
        self.assertNotIn("Dana Cole", names)
        self.assertNotIn("Jane CSM", names)

    def test_title_with_a_link_is_kept_and_scores(self):
        people = self._people()
        results = {
            "salesforce_get_account": {
                "name": "Acme Corp",
                "id": "001STUB000000001",
                "instance_url": "https://stub.salesforce.invalid",
                "contacts": [
                    {
                        "name": "Sam Reyes",
                        "title": "VP Customer Success",
                    }
                ],
            }
        }
        title, source = _title_with_link("sam reyes", results)
        self.assertEqual(title, "VP Customer Success")
        self.assertEqual(source, "salesforce_get_account")
        ranked = _successor_candidates(
            people, [], "dana cole", results
        )
        sam = [c for c in ranked if c["name"] == "Sam Reyes"][0]
        # +1 senior title, +1 three-plus sources. No connection here.
        self.assertEqual(sam["score"], 2)
        self.assertEqual(sam["title"], "VP Customer Success")

    def test_title_without_a_link_is_dropped(self):
        people = self._people()
        results = {
            "salesforce_get_account": {
                "name": "Acme Corp",
                "id": "001STUB000000001",
                "contacts": [
                    {
                        "name": "Sam Reyes",
                        "title": "VP Customer Success",
                    }
                ],
            }
        }
        title, source = _title_with_link("sam reyes", results)
        self.assertIsNone(title)
        self.assertIsNone(source)
        ranked = _successor_candidates(
            people, [], "dana cole", results
        )
        sam = [c for c in ranked if c["name"] == "Sam Reyes"][0]
        self.assertIsNone(sam["title"])
        # No title points: +1 three-plus sources only.
        self.assertEqual(sam["score"], 1)

    def test_budget_title_with_a_link_scores(self):
        people = self._people()
        results = {
            "salesforce_get_account": {
                "name": "Acme Corp",
                "id": "001STUB000000001",
                "instance_url": "https://stub.salesforce.invalid",
                "contacts": [
                    {"name": "Priya Nair", "title": "CFO"}
                ],
            }
        }
        ranked = _successor_candidates(
            people, [], "dana cole", results
        )
        priya = [c for c in ranked if c["name"] == "Priya Nair"][0]
        self.assertEqual(priya["title"], "CFO")
        # +1 budget title. No senior word, one source.
        self.assertEqual(priya["score"], 1)

    def test_contacts_join_the_people_map(self):
        results = {
            "salesforce_get_account": {
                "name": "Acme Corp",
                "id": "001STUB000000001",
                "instance_url": "https://stub.salesforce.invalid",
                "owner": "Jane CSM",
                "contacts": [
                    {"name": "Dana Cole", "email": "dana@acme.example"},
                    {"name": "Sam Reyes", "email": "sam@acme.example"},
                ],
            },
            "gmail_search_messages": {"matches": []},
            "gong_list_calls": {"calls": []},
            "gong_search_transcripts": {"matches": []},
            "zoom_list_recordings": {"recordings": []},
            "zoom_search_transcripts": {"matches": []},
            "slack_search_messages": {"matches": []},
        }
        people = _champion_people(results)
        names = [p["name"] for p in people]
        self.assertIn("Dana Cole", names)
        self.assertIn("Sam Reyes", names)
        dana = [p for p in people if p["name"] == "Dana Cole"][0]
        self.assertEqual(dana["side"], "customer")
        self.assertIn("dana@acme.example", dana["emails"])
        self.assertIn("Contact on the account record", dana["evidence"])


class TestChampionChangeBlock(unittest.TestCase):
    def test_no_champion_names_the_gap(self):
        block = _champion_change_block(
            "Acme Corp",
            None,
            None,
            [],
            None,
            [],
            {
                "second": "no",
                "successor": None,
                "successor_priorities": "unknown",
                "multi_dept": "unknown",
                "value_doc": "unknown",
                "renewal_known": "yes",
            },
            90,
            [],
        )
        self.assertIn("Champion: none on record", block)
        self.assertIn("Overall severity: none.", block)
        self.assertIn("No departure to check", block)
        self.assertIn("No successor candidate", block)
        self.assertIn("No champion on record: find one first.", block)

    def test_departure_block_names_evidence_and_plan(self):
        block = _champion_change_block(
            "Acme Corp",
            {"name": "Dana Cole", "email": "dana@acme.example"},
            "job",
            [
                {
                    "severity": "high",
                    "kind": "champion-moved-on",
                    "key": "champion-moved-on:dana cole",
                    "text": "Champion moved on: Dana Cole (Gmail, "
                    "'has left'): 'Dana Cole has left the company.'",
                }
            ],
            {
                "connections": [
                    {"name": "Sam Reyes", "calls": 2, "threads": 1}
                ],
                "other_customers": 1,
                "second_relationship": True,
                "buyer_remains": False,
            },
            [
                {
                    "key": "sam reyes",
                    "name": "Sam Reyes",
                    "title": None,
                    "title_source": None,
                    "score": 3,
                    "evidence": ["Call participant (2 calls)"],
                    "sources": ["gong_list_calls"],
                }
            ],
            {
                "second": "yes",
                "successor": "Sam Reyes",
                "successor_priorities": "unknown",
                "multi_dept": "unknown",
                "value_doc": "unknown",
                "renewal_known": "yes",
            },
            90,
            [],
        )
        self.assertIn("Champion: Dana Cole (source: job).", block)
        self.assertIn("Overall severity: high.", block)
        self.assertIn("Champion moved on: Dana Cole", block)
        self.assertIn("Sam Reyes (score 3): title unknown.", block)
        self.assertIn("Rebuild verdict: Rebuildable, move now.", block)
        self.assertIn("Days 1-7:", block)
        self.assertIn("Days 8-14:", block)
        self.assertIn("Days 15-30:", block)
        self.assertIn("Successor known: Sam Reyes.", block)

    def test_too_late_verdict(self):
        block = _champion_change_block(
            "Acme Corp",
            {"name": "Dana Cole", "email": ""},
            "job",
            [
                {
                    "severity": "high",
                    "kind": "champion-moved-on",
                    "key": "champion-moved-on:dana cole",
                    "text": "Champion moved on: Dana Cole.",
                }
            ],
            {
                "connections": [],
                "other_customers": 0,
                "second_relationship": False,
                "buyer_remains": False,
            },
            [],
            {
                "second": "no",
                "successor": None,
                "successor_priorities": "unknown",
                "multi_dept": "unknown",
                "value_doc": "unknown",
                "renewal_known": "yes",
            },
            12,
            [],
        )
        self.assertIn(
            "Rebuild verdict: Too late to rebuild, protect the exit.",
            block,
        )


class TestChampionChangeEndToEnd(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_runs_with_named_champion_and_proves(self):
        agent = AGENTS["champion-change"]
        draft = agent.run(
            {"account": "Acme Corp", "champion": "Dana Cole"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "champion-change")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("Champion: Dana Cole (source: job).", draft.text)
        self.assertIn("Departure:", draft.text)
        self.assertIn("gone quiet", draft.text)
        self.assertIn("successor", draft.text.lower())
        self.assertTrue(draft.links)
        self.assertTrue(draft.proof_passed)

    def test_no_champion_is_a_gap_not_a_crash(self):
        agent = AGENTS["champion-change"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "champion-change")
        self.assertIn("Champion: none on record", draft.text)
        self.assertIn("No departure to check", draft.text)
        self.assertTrue(draft.proof_passed)

    def test_named_owner_is_not_a_customer_champion(self):
        agent = AGENTS["champion-change"]
        draft = agent.run(
            {"account": "Acme Corp", "champion": "Jane CSM"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("Champion: none on record", draft.text)
        self.assertIn("on our side", draft.text)

    def test_csv_contacts_join_the_map(self):
        registry = _csv_registry()
        agent = AGENTS["champion-change"]
        draft = agent.run(
            {"account": "Acme Corp", "champion": "Dana Cole"},
            registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "champion-change")
        self.assertIn("Champion: Dana Cole (source: job).", draft.text)
        self.assertIn("gone quiet", draft.text)
        # Sam Reyes is on the account record: a successor candidate.
        self.assertIn("Sam Reyes", draft.text)

    def test_composed_path_finds_the_champion_in_prior_drafts(self):
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "stakeholder-mapper",
                        "Acme Corp",
                        "Stakeholder map (harness-computed): Customer "
                        "stakeholders: - Dana Cole: champion (VP CS), "
                        "influence high. Evidence: Call participant "
                        "(2 calls).",
                        [],
                    ),
                    (
                        "advocate-finder",
                        "Acme Corp",
                        "Champion: Dana Cole is the strongest "
                        "advocate in the account.",
                        [],
                    ),
                ],
            )
            agent = AGENTS["champion-change"]
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
            self.assertIn("gone quiet", draft.text)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_router_routes_explicitly_and_by_keyword(self):
        router = Router(agents=AGENTS, llm=None)
        result = router.route(
            {"text": "anything", "agent": "champion-change"}
        )
        self.assertEqual(result.agent, "champion-change")
        self.assertEqual(result.confidence, "high")
        self.assertIsNone(result.error)
        result = router.route(
            {"text": "find the successor for Acme Corp"}
        )
        self.assertEqual(result.agent, "champion-change")
        result = router.route(
            {"text": "the champion left the account, what now"}
        )
        self.assertEqual(result.agent, "champion-change")


class TestExtractSuccessor(unittest.TestCase):
    def test_job_names_the_successor(self):
        people = [{"key": "sam reyes", "name": "Sam Reyes"}]
        self.assertEqual(
            _extract_successor(
                {"successor": "Sam Reyes"}, "", people
            ),
            "Sam Reyes",
        )

    def test_prior_draft_names_the_successor(self):
        people = [{"key": "sam reyes", "name": "Sam Reyes"}]
        self.assertEqual(
            _extract_successor(
                {},
                "Successor: Sam Reyes is the most likely next "
                "champion.",
                people,
            ),
            "Sam Reyes",
        )

    def test_a_name_no_record_supports_is_not_a_successor(self):
        people = [{"key": "sam reyes", "name": "Sam Reyes"}]
        self.assertIsNone(
            _extract_successor(
                {}, "Successor: Nobody Real is the next champion.", people
            )
        )


if __name__ == "__main__":
    unittest.main()
