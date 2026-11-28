"""Tests for the case-triage agent (v1.10.0).

The case-triage agent drafts a triage brief: the open cases, tickets,
and issues grouped by subject across systems, ranked by priority, each
with a suggested owner and a draft first response or internal summary.
Priority is harness-computed, never model prose, so the priority rules
and the grouping are tested directly on synthetic tool results as well
as end to end with stub tools and the stub LLM.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed.
"""

import json
import os
import tempfile
import unittest

from connectors.gmail.tools import gmail_tools
from connectors.linear.tools import linear_tools
from connectors.salesforce.tools import salesforce_tools
from connectors.zendesk.tools import zendesk_tools
from harness import AGENTS, ApprovalQueue, TokenBudget, ToolRegistry
from harness.agents import (
    _TRIAGE_SOURCE_AGENTS,
    _load_prior_items,
    _triage_entries,
    _triage_priority,
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
    for tool in zendesk_tools(config):
        registry.register(tool)
    for tool in linear_tools(config):
        registry.register(tool)
    for tool in gmail_tools(config):
        registry.register(tool)
    return registry


def _empty_registry() -> ToolRegistry:
    """Registry whose tools return empty results: no open items at all.
    The agent must draft a clean bill, not invent an item."""
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
        "salesforce_list_cases": lambda **kw: {"cases": []},
        "zendesk_list_tickets": lambda **kw: {"tickets": []},
        "linear_list_issues": lambda **kw: {"issues": []},
        "gmail_search_messages": lambda **kw: {"matches": []},
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


class TestCaseTriageRegistered(unittest.TestCase):
    def test_seventeen_agents_registered(self):
        self.assertEqual(len(AGENTS), 17)
        agent = AGENTS["case-triage"]
        self.assertTrue(agent.implemented)
        self.assertEqual(agent.display_name, "Case Triage")


class TestCaseTriageRuns(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_fallback_run_triages_the_stub_items(self):
        agent = AGENTS["case-triage"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "case-triage")
        for tool in (
            "salesforce_get_account",
            "salesforce_list_cases",
            "zendesk_list_tickets",
            "linear_list_issues",
            "gmail_search_messages",
        ):
            self.assertIn(tool, draft.sources)
        # The stub data has three issues: the SSO failure (high, 6
        # days, in all three systems plus Gmail), the export timeout
        # (medium, 2 days, blocking the rollout, so P1), and the slow
        # dashboard (normal/low, 1 day, P2). Each entry names its
        # owner, a first response, and an internal summary.
        self.assertIn("Overall priority: P1", draft.text)
        self.assertIn("SSO login fails for new hires", draft.text)
        self.assertIn("Export timeout on large reports", draft.text)
        self.assertIn("Reporting dashboard slow for 50-seat rollout", draft.text)
        self.assertIn("Suggested owner: sam-eng", draft.text)
        self.assertIn("Suggested owner: priya-eng", draft.text)
        self.assertIn("Suggested owner: Jane CSM", draft.text)
        self.assertIn("First response", draft.text)
        self.assertIn("Internal summary", draft.text)
        # The SSO fix is being worked, the dashboard issue is in the
        # backlog with an engineering issue already filed: the next
        # steps differ accordingly.
        self.assertIn("Next: ship the fix", draft.text)
        self.assertIn("Next: pick up and scope the engineering issue", draft.text)
        # The SSO failure is tracked in four systems (Salesforce,
        # Zendesk, Linear, Gmail); the dashboard in two.
        self.assertIn("tracked in 4 systems", draft.text)
        self.assertIn("tracked in 2 systems", draft.text)
        self.assertTrue(draft.links)
        self.assertTrue(draft.proof_passed)

    def test_priority_ranking_p1_before_p2(self):
        agent = AGENTS["case-triage"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        sso_pos = draft.text.find("SSO login fails for new hires")
        export_pos = draft.text.find("Export timeout on large reports")
        dashboard_pos = draft.text.find("Reporting dashboard slow")
        self.assertNotEqual(sso_pos, -1)
        self.assertNotEqual(export_pos, -1)
        self.assertNotEqual(dashboard_pos, -1)
        # P1 first, oldest first within P1; the P2 dashboard last.
        self.assertLess(sso_pos, export_pos)
        self.assertLess(export_pos, dashboard_pos)

    def test_blocking_bump_promotes_export_to_p1(self):
        # The export timeout is medium priority, but its own record
        # says it blocks the 50-seat rollout, so it triages P1.
        agent = AGENTS["case-triage"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("P1 — 'Export timeout on large reports'", draft.text)
        self.assertIn(
            "P2 — 'Reporting dashboard slow for 50-seat rollout'", draft.text
        )

    def test_no_open_items_is_a_clean_bill(self):
        agent = AGENTS["case-triage"]
        draft = agent.run(
            {"account": "Empty Corp"},
            _empty_registry(),
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "case-triage")
        self.assertIn("Overall priority: none", draft.text)
        self.assertIn("No open cases", draft.text)
        self.assertNotIn("SSO login fails", draft.text)
        self.assertTrue(draft.proof_passed)


class TestTriagePriorityRules(unittest.TestCase):
    def test_high_is_p1(self):
        self.assertEqual(_triage_priority("High", 1), "P1")
        self.assertEqual(_triage_priority("urgent", 0), "P1")

    def test_medium_ages_into_p1(self):
        self.assertEqual(_triage_priority("Medium", 2), "P2")
        self.assertEqual(_triage_priority("Medium", 7), "P1")
        # Zendesk calls its middle priority "normal".
        self.assertEqual(_triage_priority("normal", 2), "P2")
        self.assertEqual(_triage_priority("normal", 7), "P1")

    def test_low_ages_into_p2(self):
        self.assertEqual(_triage_priority("low", 1), "P3")
        self.assertEqual(_triage_priority("low", 14), "P2")

    def test_blocking_text_bumps_one_level(self):
        self.assertEqual(
            _triage_priority("Medium", 2, "Blocks the 50-seat rollout."),
            "P1",
        )
        self.assertEqual(
            _triage_priority("low", 1, "Still blocking new hires."),
            "P2",
        )
        # P1 stays P1: the bump never overshoots.
        self.assertEqual(
            _triage_priority("High", 1, "Blocking the renewal."),
            "P1",
        )


class TestTriageEntries(unittest.TestCase):
    def _results(self, cases=None, tickets=None, issues=None, matches=None):
        return {
            "salesforce_get_account": {"owner": "Jane CSM"},
            "salesforce_list_cases": {"cases": cases or []},
            "zendesk_list_tickets": {"tickets": tickets or []},
            "linear_list_issues": {"issues": issues or []},
            "gmail_search_messages": {"matches": matches or []},
        }

    def test_same_subject_groups_across_systems(self):
        results = self._results(
            cases=[
                {
                    "number": "00009999",
                    "subject": "Widget sync fails",
                    "status": "Working",
                    "priority": "High",
                    "age_days": 3,
                }
            ],
            tickets=[
                {
                    "id": 201,
                    "subject": "Widget sync fails",
                    "status": "open",
                    "priority": "high",
                    "age_days": 3,
                }
            ],
        )
        entries = _triage_entries(results, "Acme Corp")
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["subject"], "Widget sync fails")
        self.assertEqual(entry["priority"], "P1")
        self.assertTrue(entry["duplicated"])
        self.assertEqual(entry["systems"], ["Salesforce", "Zendesk"])
        # No Linear assignee, so the account owner owns it.
        self.assertEqual(entry["owner"], "Jane CSM")

    def test_stale_flag_needs_age_and_no_progress(self):
        results = self._results(
            cases=[
                {
                    "number": "00009998",
                    "subject": "Old report glitch",
                    "status": "New",
                    "priority": "Medium",
                    "age_days": 20,
                }
            ]
        )
        entries = _triage_entries(results, "Acme Corp")
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["stale"])
        # The same age, actively being worked, is not stale.
        results["salesforce_list_cases"]["cases"][0]["status"] = "Working"
        entries = _triage_entries(results, "Acme Corp")
        self.assertFalse(entries[0]["stale"])

    def test_gmail_support_thread_joins_its_group(self):
        results = self._results(
            cases=[
                {
                    "number": "00009997",
                    "subject": "SSO login fails for new hires",
                    "status": "Working",
                    "priority": "High",
                    "age_days": 6,
                }
            ],
            matches=[
                {
                    "id": "m1",
                    "thread_id": "t1",
                    "subject": "Re: SSO still blocking new hires",
                    "excerpt": "SSO login fails again. Still waiting on the fix.",
                },
                {
                    "id": "m2",
                    "thread_id": "t2",
                    "subject": "Evaluating RivalWorks before the renewal",
                    "excerpt": "Leadership wants a look at migrating. Send a comparison?",
                },
                {
                    # A second message in the same SSO thread: the
                    # thread is listed once, not once per message.
                    "id": "m3",
                    "thread_id": "t1",
                    "subject": "Re: SSO still blocking new hires",
                    "excerpt": "Support says the fix ships next week.",
                },
            ],
        )
        entries = _triage_entries(results, "Acme Corp")
        # The support thread joins the SSO group; the evaluation ask
        # is not a support request and is skipped, not triaged.
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0]["parts"]), 2)
        self.assertIn("Gmail thread", entries[0]["parts"][1])

    def test_unassigned_when_no_owner_anywhere(self):
        results = self._results(
            issues=[
                {
                    "identifier": "ENG-999",
                    "title": "Orphan bug",
                    "status": "Backlog",
                    "priority": "low",
                    "assignee": None,
                    "age_days": 1,
                }
            ]
        )
        results["salesforce_get_account"] = {}
        entries = _triage_entries(results, "Acme Corp")
        self.assertEqual(entries[0]["owner"], "unassigned — needs an owner")

    def test_already_covered_is_flagged_from_prior_drafts(self):
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "risk-watcher",
                        "Acme Corp",
                        "Risk signals: 'SSO login fails for new hires' is "
                        "open in 3 place(s). Overall severity: high.",
                        [],
                    ),
                ],
            )
            job = {"account": "Acme Corp", "approvals_file": path}
            prior_items = _load_prior_items(job, "Acme Corp", _TRIAGE_SOURCE_AGENTS)
            results = self._results(
                cases=[
                    {
                        "number": "00001234",
                        "subject": "SSO login fails for new hires",
                        "status": "Working",
                        "priority": "High",
                        "age_days": 6,
                    }
                ],
            )
            entries = _triage_entries(results, "Acme Corp", prior_items)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["covered_by"], "risk-watcher")
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestCaseTriageComposition(unittest.TestCase):
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
                        "risk-watcher",
                        "Acme Corp",
                        "Risk signals: 'SSO login fails for new hires' is "
                        "open in 3 place(s). Overall severity: high.",
                        [
                            {
                                "label": "Account: Acme Corp",
                                "url": "https://stub.salesforce.invalid/lightning/r/Account/001STUB/view",
                            }
                        ],
                    ),
                ],
            )
            agent = AGENTS["case-triage"]
            draft = agent.run(
                {"account": "Acme Corp", "approvals_file": path},
                self.registry,
                self.llm,
                self.budget,
            )
            self.assertEqual(draft.agent, "case-triage")
            self.assertIn(
                "Composed from prior drafts: risk-watcher.",
                draft.text,
            )
            urls = [link.get("url") for link in draft.links]
            self.assertIn(
                "https://stub.salesforce.invalid/lightning/r/Account/001STUB/view",
                urls,
            )
            # The SSO entry was already covered by the risk-watcher
            # draft, so it is flagged, not re-triaged: no new first
            # response for it. The export timeout was not covered, so
            # it still gets one.
            self.assertIn("already covered in the risk-watcher draft", draft.text)
            self.assertIn(
                "First response: none drafted (already covered).", draft.text
            )
            self.assertIn(
                "First response: Thanks for raising this. "
                "'Export timeout on large reports'",
                draft.text,
            )
            # The fresh items are still gathered directly: the items
            # are the job, not the context.
            self.assertIn("salesforce_list_cases", draft.sources)
            self.assertIn("zendesk_list_tickets", draft.sources)
            self.assertTrue(draft.proof_passed)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_source_agents_are_the_expected_three(self):
        self.assertEqual(
            _TRIAGE_SOURCE_AGENTS,
            ("risk-watcher", "escalation-manager", "voice-of-customer"),
        )

    def test_execute_job_hands_the_queue_to_the_agent(self):
        # Regression: execute_job only hands approvals_file to a
        # hardcoded list of composing agents. Case-triage composes,
        # so it must be on that list, or the CLI run always falls
        # back to direct gathering even with prior drafts queued.
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
                        "agent": "case-triage",
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
            self.assertEqual(agent.name, "case-triage")
            self.assertIn(
                "Composed from prior drafts: voice-of-customer.",
                draft.text,
            )
            self.assertEqual(item.agent, "case-triage")
        finally:
            for p in (path, mem_path):
                if os.path.exists(p):
                    os.unlink(p)


class TestCaseTriageRouting(unittest.TestCase):
    def test_keyword_triage_routes(self):
        router = Router()
        result = router.route({"text": "triage the open cases for this account"})
        self.assertEqual(result.agent, "case-triage")

    def test_explicit_agent_routes(self):
        router = Router()
        result = router.route({"text": "anything", "agent": "case-triage"})
        self.assertEqual(result.agent, "case-triage")
        self.assertIsNone(result.error)

    def test_renewal_text_still_routes_to_renewal_desk(self):
        # "churn risk" in a renewal job must not steal the route, and
        # the triage keywords are absent.
        router = Router()
        result = router.route({"text": "draft a renewal plan, churn risk"})
        self.assertEqual(result.agent, "renewal-desk")

    def test_risk_text_still_routes_to_risk_watcher(self):
        router = Router()
        result = router.route({"text": "check the risk on this account"})
        self.assertEqual(result.agent, "risk-watcher")


if __name__ == "__main__":
    unittest.main()
