"""Tests for the stakeholder-mapper agent (v1.9.0).

The stakeholder-mapper agent builds the relationship map for an
account: each person with their role and influence, how they connect,
and the coverage gaps. Roles and influence are harness-computed, never
model prose, so the person extraction and scoring are tested directly
on synthetic tool results as well as end to end with stub tools and
the stub LLM.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed.
"""

import json
import os
import tempfile
import unittest

from connectors.champify.tools import champify_tools
from connectors.gmail.tools import gmail_tools
from connectors.gong.tools import gong_tools
from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from connectors.zoom.tools import zoom_tools
from harness import AGENTS, ApprovalQueue, TokenBudget, ToolRegistry
from harness.agents import (
    _STAKEHOLDER_SOURCE_AGENTS,
    _is_budget_holder,
    _stakeholder_gaps,
    _stakeholder_influence,
    _stakeholder_people,
    _stakeholder_relationships,
    _stakeholder_role,
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
    for tool in champify_tools(config):
        registry.register(tool)
    return registry


def _empty_registry() -> ToolRegistry:
    """Registry whose tools return empty results: no people at all.
    The agent must draft an empty map, not invent a stakeholder."""
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
        "champify_list_champions": lambda **kw: {"champions": []},
        "champify_list_job_changes": lambda **kw: {"job_changes": []},
        "gmail_search_messages": lambda **kw: {"matches": []},
        "gong_list_calls": lambda **kw: {"calls": []},
        "gong_search_transcripts": lambda **kw: {"matches": []},
        "zoom_list_recordings": lambda **kw: {"recordings": []},
        "zoom_search_transcripts": lambda **kw: {"matches": []},
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


class TestStakeholderMapperRegistered(unittest.TestCase):
    def test_fifteen_agents_registered(self):
        self.assertEqual(len(AGENTS), 15)
        agent = AGENTS["stakeholder-mapper"]
        self.assertTrue(agent.implemented)
        self.assertEqual(agent.display_name, "Stakeholder Mapper")


class TestStakeholderMapperRuns(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_fallback_run_maps_the_account(self):
        agent = AGENTS["stakeholder-mapper"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "stakeholder-mapper")
        for tool in (
            "salesforce_get_account",
            "champify_list_champions",
            "champify_list_job_changes",
            "gmail_search_messages",
            "gong_list_calls",
            "gong_search_transcripts",
            "zoom_list_recordings",
            "zoom_search_transcripts",
            "slack_search_messages",
        ):
            self.assertIn(tool, draft.sources)
        # The stub account maps to eight people: four champions
        # (two active, two moved), the account owner and the SE on
        # our side, and two customer contacts known by display name
        # or handle.
        for name in (
            "Dana Reyes",
            "Marcus Webb",
            "Priya Nair",
            "Tom Okafor",
            "Jane CSM",
            "Mike SE",
            "Acme Admin",
            "acme-ops-lead",
        ):
            self.assertIn(name, draft.text)
        self.assertIn("Stakeholder map", draft.text)
        self.assertIn("Coverage gaps", draft.text)
        self.assertIn("Relationships", draft.text)
        # Roles and influence are harness-computed: the active
        # champions are high, the moved champions are former with
        # unknown influence, and the handle-only contact is unknown.
        self.assertIn("Dana Reyes: champion (VP Operations), influence high", draft.text)
        self.assertIn("Marcus Webb: champion (Director of IT), influence high", draft.text)
        self.assertIn("Priya Nair: former champion (Head of Support), influence unknown", draft.text)
        self.assertIn("acme-ops-lead: unknown, influence unknown", draft.text)
        # The gaps: no economic buyer, two departed champions with
        # no successor named, two people known only by display name
        # or handle, and no customer-customer relationship visible.
        self.assertIn("No economic buyer identified", draft.text)
        self.assertIn("Priya Nair (Head of Support, former champion) left the account on 2026-11-02", draft.text)
        self.assertIn("Tom Okafor (Senior Engineer, former champion) left the account on 2026-09-20", draft.text)
        self.assertIn("Acme Admin and acme-ops-lead known only by a display name or handle", draft.text)
        self.assertIn("No two customer stakeholders appear together", draft.text)
        self.assertTrue(draft.links)
        self.assertTrue(draft.proof_passed)

    def test_no_people_case_is_an_empty_map(self):
        agent = AGENTS["stakeholder-mapper"]
        draft = agent.run(
            {"account": "Empty Corp"},
            _empty_registry(),
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "stakeholder-mapper")
        self.assertIn("No stakeholders found in the gathered records", draft.text)
        self.assertIn("Coverage gaps", draft.text)
        self.assertNotIn("Dana Reyes", draft.text)
        self.assertTrue(draft.proof_passed)

    def test_composes_from_prior_drafts(self):
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "advocate-finder",
                        "Acme Corp",
                        "MARKER advocate-finder draft for Acme Corp.",
                        [
                            {
                                "label": "Champion: Dana Reyes",
                                "url": "https://app.champify.invalid/champions/chp_7f3a91",
                            }
                        ],
                    ),
                    (
                        "risk-watcher",
                        "Acme Corp",
                        "MARKER risk-watcher draft for Acme Corp.",
                        [
                            {
                                "label": "Account: Acme Corp",
                                "url": "https://stub.salesforce.invalid/lightning/r/Account/001STUB/view",
                            }
                        ],
                    ),
                ],
            )
            agent = AGENTS["stakeholder-mapper"]
            draft = agent.run(
                {"account": "Acme Corp", "approvals_file": path},
                self.registry,
                self.llm,
                self.budget,
            )
            self.assertEqual(draft.agent, "stakeholder-mapper")
            self.assertIn(
                "Composed from prior drafts: advocate-finder, risk-watcher.",
                draft.text,
            )
            urls = [link.get("url") for link in draft.links]
            self.assertIn(
                "https://app.champify.invalid/champions/chp_7f3a91", urls
            )
            self.assertIn(
                "https://stub.salesforce.invalid/lightning/r/Account/001STUB/view",
                urls,
            )
            # Fresh person signals still fire in the composed path:
            # the champions and the Gmail authors are gathered
            # directly, not from the old drafts.
            self.assertIn("Dana Reyes", draft.text)
            self.assertIn("Acme Admin", draft.text)
            self.assertIn("champify_list_champions", draft.sources)
            self.assertIn("gmail_search_messages", draft.sources)
            # Transcripts come from the prior drafts here, not from a
            # fresh gather.
            self.assertNotIn("gong_search_transcripts", draft.sources)
            self.assertNotIn("zoom_search_transcripts", draft.sources)
            self.assertTrue(draft.proof_passed)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_source_agents_are_the_expected_three(self):
        self.assertEqual(
            _STAKEHOLDER_SOURCE_AGENTS,
            ("advocate-finder", "risk-watcher", "voice-of-customer"),
        )

    def test_execute_job_hands_the_queue_to_the_agent(self):
        # Regression (v1.8.0): execute_job only hands approvals_file
        # to a hardcoded list of composing agents. Stakeholder-mapper
        # composes, so it must be on that list, or the CLI run always
        # falls back to direct gathering even with prior drafts queued.
        path = _temp_queue_path()
        mem_path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "advocate-finder",
                        "Acme Corp",
                        "MARKER advocate-finder draft for Acme Corp.",
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
                        "agent": "stakeholder-mapper",
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
            self.assertEqual(agent.name, "stakeholder-mapper")
            self.assertIn(
                "Composed from prior drafts: advocate-finder.",
                draft.text,
            )
            self.assertNotIn("gong_search_transcripts", draft.sources)
            self.assertIn("Dana Reyes", draft.text)
        finally:
            for p in (path, mem_path):
                if os.path.exists(p):
                    os.unlink(p)

    def test_router_keyword_routes_to_stakeholder_mapper(self):
        router = Router(agents=AGENTS, llm=None)
        result = router.route({"text": "map the stakeholders on this account"})
        self.assertEqual(result.agent, "stakeholder-mapper")
        # The renewal keyword still wins its own job.
        result = router.route({"text": "draft a renewal plan, churn risk"})
        self.assertEqual(result.agent, "renewal-desk")


class TestStakeholderPeople(unittest.TestCase):
    def _results(self, champions=None, changes=None, account=None):
        results = {
            "salesforce_get_account": account
            or {"owner": "Jane CSM", "name": "Acme Corp"},
            "champify_list_champions": {"champions": champions or []},
            "champify_list_job_changes": {"job_changes": changes or []},
            "gmail_search_messages": {"matches": []},
            "gong_list_calls": {"calls": []},
            "gong_search_transcripts": {"matches": []},
            "zoom_list_recordings": {"recordings": []},
            "zoom_search_transcripts": {"matches": []},
            "slack_search_messages": {"matches": []},
        }
        return results

    def test_same_person_merges_across_name_forms(self):
        results = self._results()
        results["gmail_search_messages"] = {
            "matches": [
                {"author": "Jane CSM <jane@cs-team.example>", "thread_id": "t1"},
                {"author": "Mike SE <mike@cs-team.example>", "thread_id": "t2"},
            ]
        }
        results["gong_list_calls"] = {
            "calls": [
                {"participants": ["jane-csm", "mike-se", "acme-admin"]},
            ]
        }
        people = _stakeholder_people(results)
        by_key = {p["key"]: p for p in people}
        # Jane and Mike appear once each, not once per name form.
        self.assertEqual(len(people), 3)
        self.assertEqual(by_key["jane csm"]["name"], "Jane CSM")
        self.assertEqual(by_key["jane csm"]["side"], "internal")
        self.assertEqual(by_key["mike se"]["name"], "Mike SE")
        self.assertEqual(by_key["mike se"]["side"], "internal")
        self.assertEqual(by_key["acme admin"]["side"], "customer")

    def test_active_champion_is_high_influence(self):
        results = self._results(
            champions=[
                {
                    "name": "Dana Reyes",
                    "role": "VP Operations",
                    "status": "active",
                    "champion_since": "2025-06-01",
                }
            ]
        )
        people = _stakeholder_people(results)
        dana = [p for p in people if p["name"] == "Dana Reyes"][0]
        self.assertEqual(_stakeholder_role(dana), "champion")
        self.assertEqual(_stakeholder_influence(dana), "high")

    def test_moved_champion_is_former_with_unknown_influence(self):
        results = self._results(
            champions=[
                {
                    "name": "Priya Nair",
                    "role": "Head of Support",
                    "status": "moved",
                    "champion_since": "2025-03-10",
                }
            ],
            changes=[
                {
                    "name": "Priya Nair",
                    "previous_company": "Acme Corp",
                    "previous_role": "Head of Support",
                    "new_company": "Globex Inc",
                    "new_role": "VP Customer Experience",
                    "changed_at": "2026-11-02",
                }
            ],
        )
        people = _stakeholder_people(results)
        priya = [p for p in people if p["name"] == "Priya Nair"][0]
        self.assertEqual(_stakeholder_role(priya), "former champion")
        self.assertEqual(_stakeholder_influence(priya), "unknown")
        self.assertEqual(priya["moved_to"]["company"], "Globex Inc")

    def test_budget_title_is_the_economic_buyer(self):
        # A budget title on a non-champion makes an economic buyer.
        # No current tool carries titles for non-champions, so the
        # role helper is tested directly on a synthetic person.
        person = {
            "name": "Cora Finch",
            "title": "CFO",
            "champion_status": None,
            "is_owner": False,
            "side": "customer",
            "sources": {"gmail_search_messages"},
            "handle_only": False,
        }
        self.assertEqual(_stakeholder_role(person), "economic buyer")
        self.assertTrue(_is_budget_holder(person))
        # And a champion record wins the role, but the buyer gap
        # still sees her as the budget holder.
        person["champion_status"] = "active"
        self.assertEqual(_stakeholder_role(person), "champion")
        self.assertTrue(_is_budget_holder(person))

    def test_gmail_only_person_has_unknown_role(self):
        # Gmail carries no title, so a person known only from email
        # has an unknown role even when the name sounds senior.
        results = self._results()
        results["gmail_search_messages"] = {
            "matches": [
                {
                    "author": "Cora Finch <cora@acme.example>",
                    "thread_id": "t1",
                }
            ]
        }
        people = _stakeholder_people(results)
        cora = [p for p in people if p["name"] == "Cora Finch"][0]
        self.assertEqual(_stakeholder_role(cora), "unknown")

    def test_handle_only_is_unknown_influence(self):
        results = self._results()
        results["gong_list_calls"] = {
            "calls": [{"participants": ["jane-csm", "acme-ops-lead"]}]
        }
        people = _stakeholder_people(results)
        ops = [p for p in people if p["key"] == "acme ops lead"][0]
        self.assertTrue(ops["handle_only"])
        self.assertEqual(_stakeholder_role(ops), "unknown")
        self.assertEqual(_stakeholder_influence(ops), "unknown")

    def test_three_source_presence_scores_medium(self):
        results = self._results()
        results["gmail_search_messages"] = {
            "matches": [{"author": "Acme Admin <admin@acme.example>", "thread_id": "t1"}]
        }
        results["gong_list_calls"] = {
            "calls": [{"participants": ["jane-csm", "acme-admin"]}]
        }
        results["slack_search_messages"] = {
            "matches": [{"author": "acme-admin", "channel": "#acme-corp"}]
        }
        people = _stakeholder_people(results)
        admin = [p for p in people if p["key"] == "acme admin"][0]
        self.assertEqual(len(admin["sources"]), 3)
        self.assertEqual(_stakeholder_influence(admin), "medium")

    def test_relationships_come_from_shared_calls_and_threads(self):
        results = self._results()
        results["gmail_search_messages"] = {
            "matches": [
                {"author": "Acme Admin <admin@acme.example>", "thread_id": "t1"},
                {"author": "Jane CSM <jane@cs-team.example>", "thread_id": "t1"},
                {"author": "Acme Admin <admin@acme.example>", "thread_id": "t2"},
                {"author": "Jane CSM <jane@cs-team.example>", "thread_id": "t2"},
            ]
        }
        results["gong_list_calls"] = {
            "calls": [
                {"participants": ["jane-csm", "acme-admin"]},
                {"participants": ["jane-csm", "mike-se", "acme-ops-lead"]},
            ]
        }
        rels = _stakeholder_relationships(results)
        by_pair = {(r["a"], r["b"]): r for r in rels}
        admin_jane = by_pair[("acme admin", "jane csm")]
        self.assertEqual(admin_jane["calls"], 1)
        self.assertEqual(admin_jane["threads"], 2)
        ops_mike = by_pair[("acme ops lead", "mike se")]
        self.assertEqual(ops_mike["calls"], 1)
        self.assertEqual(ops_mike["threads"], 0)
        # Sharing an account alone is not a relationship: the admin
        # and the ops lead never share a call or a thread.
        self.assertNotIn(("acme admin", "acme ops lead"), by_pair)

    def test_gaps_name_what_is_missing(self):
        results = self._results(
            champions=[
                {
                    "name": "Priya Nair",
                    "role": "Head of Support",
                    "status": "moved",
                    "champion_since": "2025-03-10",
                }
            ],
            changes=[
                {
                    "name": "Priya Nair",
                    "previous_company": "Acme Corp",
                    "previous_role": "Head of Support",
                    "new_company": "Globex Inc",
                    "new_role": "VP Customer Experience",
                    "changed_at": "2026-11-02",
                }
            ],
        )
        people = _stakeholder_people(results)
        rels = _stakeholder_relationships(results)
        gaps = _stakeholder_gaps(people, rels)
        self.assertTrue(any("No economic buyer" in g for g in gaps))
        self.assertTrue(
            any("Priya Nair" in g and "no successor" in g for g in gaps)
        )

    def test_single_champion_map_still_has_buyer_gap(self):
        # Dana is the only customer person: the buyer gap fires (no
        # record names a budget holder) and so does the
        # no-customer-relationship gap (one person cannot connect to
        # anyone), while no departed-champion gap fires.
        results = self._results()
        results["champify_list_champions"] = {
            "champions": [
                {
                    "name": "Dana Reyes",
                    "role": "VP Operations",
                    "status": "active",
                    "champion_since": "2025-06-01",
                }
            ]
        }
        people = _stakeholder_people(results)
        rels = _stakeholder_relationships(results)
        gaps = _stakeholder_gaps(people, rels)
        self.assertTrue(any("No economic buyer" in g for g in gaps))
        self.assertTrue(
            any("No two customer stakeholders" in g for g in gaps)
        )
        self.assertFalse(any("no successor" in g for g in gaps))


if __name__ == "__main__":
    unittest.main()
