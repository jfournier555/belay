"""Tests for the Belay dashboard (v0.8.0).

The dashboard reads live harness state and serves it on localhost. These
tests pin the parts with logic worth pinning: queue-derived stats, agent
last-run state, the event log, and the approve/reject endpoints. They run
against a temp queue file so the real approvals.json is never touched.
"""

import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from harness import ApprovalQueue, Config
from harness.dashboard import (
    agent_states,
    build_state,
    create_app,
    event_log,
    queue_stats,
    source_statuses,
    token_budget_state,
)


def _sample_queue(path):
    queue = ApprovalQueue(path)
    queue.submit(
        agent="renewal-desk",
        job={"account": "Acme Corp"},
        draft="Renewal plan for Acme Corp. Health is good. Next step: call.",
        sources=["salesforce_get_account"],
        links=[
            {
                "label": "Account: Acme Corp",
                "url": "https://stub.salesforce.invalid/lightning/r/Account/001STUB000000001/view",
            }
        ],
        proof=[
            {"label": "Names the open renewal opportunity", "passed": True, "detail": "ok"},
            {"label": "Covers account health", "passed": False, "detail": "draft does not mention 'health'"},
        ],
    )
    queue.submit(
        agent="advocate-finder",
        job={"account": "Globex Inc"},
        draft="Champion: Jane at Globex. Influence: controls budget.",
        sources=["slack_search_messages"],
        proof=[
            {"label": "Names a champion", "passed": True, "detail": "ok"},
        ],
    )
    queue.submit(
        agent="advocate-finder",
        job={"account": "Acme Corp"},
        draft="Champion: Bob at Acme. Influence: defends spend.",
        sources=["slack_search_messages"],
        proof=[],
    )
    return queue


class TestQueueStats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "approvals.json")
        self.queue = _sample_queue(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stats_come_from_the_queue(self):
        stats = queue_stats(self.queue)
        self.assertEqual(stats["accounts_watched"], 2)
        self.assertEqual(stats["at_risk_flagged"], 1)
        self.assertEqual(stats["drafts_ready"], 3)
        self.assertEqual(stats["advocates_found"], 2)

    def test_decided_drafts_leave_drafts_ready(self):
        pending = self.queue.list_pending()
        self.queue.approve(pending[0].id)
        stats = queue_stats(self.queue)
        self.assertEqual(stats["drafts_ready"], 2)
        self.assertEqual(stats["at_risk_flagged"], 0)

    def test_empty_queue_is_an_honest_zero(self):
        empty = ApprovalQueue(os.path.join(self.tmp.name, "empty.json"))
        stats = queue_stats(empty)
        self.assertEqual(stats, {
            "accounts_watched": 0,
            "at_risk_flagged": 0,
            "drafts_ready": 0,
            "advocates_found": 0,
        })


class TestAgentStates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "approvals.json")
        self.queue = _sample_queue(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_eighteen_agents_including_value_finder(self):
        states = agent_states(self.queue)
        self.assertEqual(len(states), 18)
        names = [s["name"] for s in states]
        self.assertIn("value-finder", names)
        self.assertIn("salesforce-updater", names)
        self.assertIn("risk-watcher", names)
        self.assertIn("escalation-manager", names)
        self.assertIn("competitive-defense", names)
        self.assertIn("champion-change", names)
        self.assertIn("handoff", names)
        finder = [s for s in states if s["name"] == "value-finder"][0]
        self.assertEqual(finder["status"], "implemented")
        self.assertEqual(finder["drafts"], 0)
        self.assertIsNone(finder["last_run"])

    def test_last_run_comes_from_the_queue(self):
        states = {s["name"]: s for s in agent_states(self.queue)}
        self.assertEqual(states["renewal-desk"]["drafts"], 1)
        self.assertIsNotNone(states["renewal-desk"]["last_run"])
        self.assertEqual(states["advocate-finder"]["drafts"], 2)
        self.assertEqual(states["briefer"]["drafts"], 0)
        self.assertIsNone(states["briefer"]["last_run"])


class TestSourceStatuses(unittest.TestCase):
    def test_no_credentials_means_stub(self):
        config = Config()
        statuses = {s["name"]: s["status"] for s in source_statuses(config)}
        self.assertEqual(statuses["Salesforce"], "stub")
        self.assertEqual(statuses["Slack"], "stub")
        self.assertEqual(statuses["Gong"], "stub")

    def test_credentials_mean_live(self):
        config = Config(
            salesforce_instance_url="https://x.my.salesforce.com",
            salesforce_access_token="token",
            slack_bot_token="xoxb-1",
            gong_access_token="gong-token",
        )
        statuses = {s["name"]: s["status"] for s in source_statuses(config)}
        self.assertEqual(statuses["Salesforce"], "live")
        self.assertEqual(statuses["Slack"], "live")
        self.assertEqual(statuses["Gong"], "live")

    def test_stub_mode_overrides_credentials(self):
        config = Config(
            salesforce_instance_url="https://x.my.salesforce.com",
            salesforce_access_token="token",
            slack_bot_token="xoxb-1",
            gong_access_token="gong-token",
            stub_mode=True,
        )
        statuses = {s["name"]: s["status"] for s in source_statuses(config)}
        self.assertEqual(statuses["Salesforce"], "stub")
        self.assertEqual(statuses["Slack"], "stub")
        self.assertEqual(statuses["Gong"], "stub")


class TestEventLogAndTokens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "approvals.json")
        self.queue = _sample_queue(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_event_log_records_queue_and_decision(self):
        pending = self.queue.list_pending()
        self.queue.approve(pending[0].id)
        events = event_log(self.queue)
        kinds = [e["kind"] for e in events]
        self.assertIn("queued", kinds)
        self.assertIn("approved", kinds)
        self.assertEqual(events[0]["at"], max(e["at"] for e in events))

    def test_token_budget_comes_from_config(self):
        config = Config(token_budget_per_job=8000)
        state = token_budget_state(self.queue, config)
        self.assertEqual(state["budget_per_job"], 8000)
        self.assertEqual(state["pending_drafts"], 3)
        self.assertGreater(state["estimated_tokens_pending"], 0)


class TestDashboardApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "approvals.json")
        self.queue = _sample_queue(self.path)
        self.client = TestClient(create_app(Config(), self.queue))

    def tearDown(self):
        self.tmp.cleanup()

    def test_index_renders(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Belay", res.text)
        self.assertIn("Everything is a draft until I say send.", res.text)
        self.assertIn("Approval queue", res.text)

    def test_state_endpoint_returns_real_numbers(self):
        res = self.client.get("/api/state")
        self.assertEqual(res.status_code, 200)
        state = res.json()
        self.assertEqual(state["stats"]["drafts_ready"], 3)
        self.assertEqual(state["stats"]["accounts_watched"], 2)
        self.assertEqual(len(state["agents"]), 18)
        self.assertEqual(len(state["queue"]), 3)
        first = state["queue"][0]
        # Links ride along in the state; no aggregate proof score does.
        self.assertEqual(len(first["links"]), 1)
        self.assertEqual(
            first["links"][0]["url"],
            "https://stub.salesforce.invalid/lightning/r/Account/001STUB000000001/view",
        )
        self.assertNotIn("proof_passed", first)
        self.assertNotIn("proof_total", first)
        self.assertEqual(len(first["proof"]), 2)

    def test_approve_moves_the_draft(self):
        item_id = self.queue.list_pending()[0].id
        res = self.client.post("/api/approvals/%s/approve" % item_id)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "approved")
        state = self.client.get("/api/state").json()
        self.assertEqual(state["stats"]["drafts_ready"], 2)

    def test_reject_moves_the_draft(self):
        item_id = self.queue.list_pending()[0].id
        res = self.client.post("/api/approvals/%s/reject" % item_id)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "rejected")

    def test_unknown_item_is_404(self):
        res = self.client.post("/api/approvals/appr-nope/approve")
        self.assertEqual(res.status_code, 404)

    def test_empty_queue_state(self):
        empty_path = os.path.join(self.tmp.name, "empty.json")
        client = TestClient(create_app(Config(), ApprovalQueue(empty_path)))
        state = client.get("/api/state").json()
        self.assertEqual(state["stats"]["drafts_ready"], 0)
        self.assertEqual(state["queue"], [])


class TestBuildState(unittest.TestCase):
    def test_build_state_covers_every_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = _sample_queue(os.path.join(tmp, "approvals.json"))
            state = build_state(queue, Config())
            for key in ("version", "stats", "sources", "agents", "queue", "events", "tokens", "stub_mode"):
                self.assertIn(key, state)


if __name__ == "__main__":
    unittest.main()
