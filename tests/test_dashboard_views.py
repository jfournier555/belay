"""Tests for the finished dashboard (v1.11.0): one interface for the
whole harness.

The v0.8.0 dashboard covered the approval queue only. The finished
dashboard adds a view for every v1.x surface: memory, schedules,
escalations, write-back, and external MCP servers, plus an overview
that ties them together. These tests pin the new state builders and
the new endpoints, and that a missing or corrupt store file degrades
to an empty view, never a crash. They run against temp store files so
the real harness files are never touched.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from harness import Config
from harness.approvals import ApprovalQueue
from harness.dashboard import (
    build_state,
    create_app,
    mcp_servers_view,
    memory_view,
    schedules_view,
    writeback_view,
)
from harness.escalations import EscalationStore
from harness.mcp_client import MCPServerStore
from harness.memory import AccountMemory
from harness.scheduler import ScheduleStore

REPO_ROOT = Path(__file__).resolve().parent.parent


def _gmail_server_config(name="gmail"):
    return {
        "name": name,
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "connectors.gmail.server"],
        "cwd": str(REPO_ROOT),
        "read_only": True,
    }


def _fake_item(agent, draft, account):
    return SimpleNamespace(
        agent=agent,
        id="appr-fake",
        draft=draft,
        job={"account": account},
        note=None,
    )


class DashboardViewsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = {
            "approvals": os.path.join(self.tmp.name, "approvals.json"),
            "memory": os.path.join(self.tmp.name, "memory.json"),
            "schedules": os.path.join(self.tmp.name, "schedules.json"),
            "escalations": os.path.join(self.tmp.name, "escalations.json"),
            "mcp_servers": os.path.join(self.tmp.name, "mcp-servers.json"),
        }
        self.config = Config.from_env()
        self.config.approvals_file = self.paths["approvals"]
        self.config.memory_file = self.paths["memory"]
        self.config.schedules_file = self.paths["schedules"]
        self.config.escalations_file = self.paths["escalations"]
        self.config.mcp_servers_file = self.paths["mcp_servers"]

        self.memory = AccountMemory(self.paths["memory"])
        self.escalations = EscalationStore(self.paths["escalations"])
        # Attach the stores the way create_app does for a fresh queue,
        # so dashboard decisions are recorded as memory and move
        # escalation records, exactly like the CLI path.
        self.queue = ApprovalQueue(
            self.paths["approvals"],
            memory=self.memory,
            escalations=self.escalations,
        )
        self.queue.submit(
            agent="renewal-desk",
            job={"account": "Acme Corp"},
            draft="Renewal plan for Acme Corp.",
            sources=["salesforce_get_account"],
            proof=[{"label": "Names the renewal", "passed": True}],
        )
        self.write_item = self.queue.submit(
            agent="salesforce-updater",
            job={"account": "Acme Corp"},
            draft="Move the renewal to Closed Won.",
            writes=[
                {
                    "tool": "salesforce_move_stage",
                    "record_id": "006STUB000000001",
                    "stage": "Closed Won",
                    "label": "Opportunity 006STUB000000001 stage -> Closed Won",
                }
            ],
        )

        self.memory.record(
            _fake_item("renewal-desk", "Renewal plan v1.", "Acme Corp"),
            "approved",
        )
        self.memory.record(
            _fake_item(
                "renewal-desk",
                "Renewal plan v2, human version.",
                "Acme Corp",
            ),
            "edited",
            old_draft="Renewal plan v1.",
        )

        self.schedules = ScheduleStore(self.paths["schedules"])
        self.due_schedule = self.schedules.add(
            {
                "agent": "briefer",
                "account": "Acme Corp",
                "cadence": "daily",
                "time": "07:00",
                "timezone": "America/Los_Angeles",
            }
        )
        # Backdate creation by two days, so the daily job is due.
        self.due_schedule.created_at = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat(timespec="seconds")
        self.schedules._save()
        self.later_schedule = self.schedules.add(
            {
                "agent": "renewal-desk",
                "account": "Globex Inc",
                "cadence": "daily",
                "time": "23:59",
                "timezone": "UTC",
            }
        )

        self.open_esc = self.escalations.create(
            "Acme Corp",
            "champion-departure-priya",
            "champion-departure",
            "high",
            "Priya Nair left Acme Corp.",
        )
        self.resolved_esc = self.escalations.create(
            "Globex Inc",
            "sso-failure",
            "support-risk",
            "high",
            "SSO login fails for new hires.",
        )
        self.escalations.resolve(self.resolved_esc.id, note="Fixed.")

        self.mcp_servers = MCPServerStore(self.paths["mcp_servers"])
        self.mcp_servers.add(_gmail_server_config())
        self.mcp_servers.add(
            {
                "name": "ghost",
                "transport": "stdio",
                "command": "/nonexistent/nope",
            }
        )

        self.client = TestClient(
            create_app(
                config=self.config,
                queue=self.queue,
                memory=self.memory,
                schedules=self.schedules,
                escalations=self.escalations,
                mcp_servers=self.mcp_servers,
            )
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _state(self):
        res = self.client.get("/api/state")
        self.assertEqual(res.status_code, 200)
        return res.json()

    def test_state_covers_every_new_view(self):
        state = self._state()
        for key in (
            "overview",
            "memory",
            "schedules",
            "escalations",
            "writeback",
            "mcp_servers",
        ):
            self.assertIn(key, state)
        self.assertEqual(len(state["memory"]), 1)
        self.assertEqual(len(state["schedules"]), 2)
        self.assertEqual(len(state["escalations"]), 2)
        self.assertEqual(len(state["writeback"]), 1)
        self.assertEqual(len(state["mcp_servers"]), 2)

    def test_overview_counts_come_from_the_stores(self):
        state = self._state()
        overview = state["overview"]
        self.assertEqual(overview["open_escalations"], 1)
        self.assertEqual(overview["due_schedules"], 1)
        self.assertEqual(overview["memory_accounts"], 1)
        self.assertEqual(overview["write_drafts_pending"], 1)

    def test_memory_view_is_per_account_newest_first(self):
        view = memory_view(self.memory)
        self.assertEqual(len(view), 1)
        self.assertEqual(view[0]["account"], "Acme Corp")
        self.assertEqual(view[0]["count"], 2)
        records = view[0]["records"]
        self.assertEqual(records[0]["outcome"], "edited")
        self.assertIn("Renewal plan v2", records[0]["correction"])
        self.assertEqual(records[1]["outcome"], "approved")

    def test_memory_clear_endpoint(self):
        res = self.client.post("/api/memory/Acme%20Corp/clear")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["removed"], 2)
        self.assertEqual(self._state()["memory"], [])
        res = self.client.post("/api/memory/Acme%20Corp/clear")
        self.assertEqual(res.json()["removed"], 0)

    def test_edit_endpoint_records_memory(self):
        item_id = self.queue.list_pending()[0].id
        res = self.client.post(
            "/api/approvals/%s/edit" % item_id,
            json={"draft": "Renewal plan, human version. Lead with the date."},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "edited")
        self.assertIn("Lead with the date", body["draft"])
        records = self.memory.records_for("Acme Corp")
        self.assertEqual(records[0].outcome, "edited")
        self.assertIn("Lead with the date", records[0].correction)

    def test_edit_endpoint_rejects_bad_input(self):
        item_id = self.queue.list_pending()[0].id
        res = self.client.post(
            "/api/approvals/%s/edit" % item_id, json={"draft": "   "}
        )
        self.assertEqual(res.status_code, 400)
        res = self.client.post(
            "/api/approvals/appr-nope/edit", json={"draft": "x"}
        )
        self.assertEqual(res.status_code, 404)

    def test_schedules_view_computes_next_run_and_due(self):
        view = schedules_view(self.schedules)
        due = [s for s in view if s["id"] == self.due_schedule.id][0]
        self.assertTrue(due["due"])
        self.assertIsNotNone(due["next_run"])
        later = [s for s in view if s["id"] == self.later_schedule.id][0]
        self.assertFalse(later["due"])

    def test_schedules_add_and_remove_endpoints(self):
        res = self.client.post(
            "/api/schedules",
            json={
                "agent": "briefer",
                "account": "Initech",
                "cadence": "weekly",
                "time": "09:00",
                "timezone": "UTC",
                "weekday": "mon",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["id"].startswith("sched-"))
        self.assertEqual(len(self._state()["schedules"]), 3)
        res = self.client.post(
            "/api/schedules",
            json={
                "agent": "not-an-agent",
                "account": "Initech",
                "cadence": "daily",
                "time": "09:00",
                "timezone": "UTC",
            },
        )
        self.assertEqual(res.status_code, 400)
        res = self.client.delete(
            "/api/schedules/%s" % self.later_schedule.id
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(self._state()["schedules"]), 2)
        res = self.client.delete("/api/schedules/sched-nope")
        self.assertEqual(res.status_code, 404)

    def test_escalations_resolve_endpoint(self):
        res = self.client.post(
            "/api/escalations/%s/resolve" % self.open_esc.id,
            json={"note": "Successor confirmed."},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "resolved")
        self.assertEqual(res.json()["note"], "Successor confirmed.")
        res = self.client.post(
            "/api/escalations/%s/resolve" % self.open_esc.id,
            json={},
        )
        self.assertEqual(res.status_code, 400)
        res = self.client.post(
            "/api/escalations/esc-nope/resolve", json={}
        )
        self.assertEqual(res.status_code, 404)

    def test_writeback_view_shows_plan_and_execution(self):
        view = writeback_view(self.queue)
        self.assertEqual(len(view), 1)
        self.assertEqual(view[0]["id"], self.write_item.id)
        self.assertEqual(view[0]["status"], "pending")
        self.assertEqual(
            view[0]["writes"][0]["label"],
            "Opportunity 006STUB000000001 stage -> Closed Won",
        )
        self.assertEqual(view[0]["execution"], [])
        self.queue.record_execution(
            self.write_item.id,
            [
                {
                    "op_index": 0,
                    "tool": "salesforce_move_stage",
                    "success": True,
                    "result": {"simulated": True},
                }
            ],
            "2026-11-21T12:00:00+00:00",
        )
        view = writeback_view(self.queue)
        self.assertTrue(view[0]["execution"][0]["success"])
        self.assertEqual(
            view[0]["executed_at"], "2026-11-21T12:00:00+00:00"
        )

    def test_mcp_servers_view_lists_configured_servers(self):
        view = mcp_servers_view(self.mcp_servers)
        by_name = {s["name"]: s for s in view}
        self.assertEqual(by_name["gmail"]["transport"], "stdio")
        self.assertIn("connectors.gmail.server", by_name["gmail"]["target"])
        self.assertTrue(by_name["gmail"]["read_only"])
        self.assertEqual(by_name["ghost"]["target"], "/nonexistent/nope")

    def test_mcp_servers_add_and_remove_endpoints(self):
        res = self.client.post(
            "/api/mcp-servers",
            json={
                "name": "docs",
                "transport": "sse",
                "url": "https://example.com/mcp",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["name"], "docs")
        res = self.client.post(
            "/api/mcp-servers",
            json={"name": "bad one!", "transport": "stdio", "command": "x"},
        )
        self.assertEqual(res.status_code, 400)
        res = self.client.post(
            "/api/mcp-servers",
            json={
                "name": "gmail",
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "connectors.gmail.server"],
            },
        )
        self.assertEqual(res.status_code, 400)
        res = self.client.delete("/api/mcp-servers/ghost")
        self.assertEqual(res.status_code, 200)
        res = self.client.delete("/api/mcp-servers/ghost")
        self.assertEqual(res.status_code, 404)

    def test_mcp_probe_unreachable_server(self):
        res = self.client.get("/api/mcp-servers/ghost/tools")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "unreachable")
        self.assertIn("No such file", body["error"])

    def test_mcp_probe_lists_the_gmail_servers_tools(self):
        res = self.client.get("/api/mcp-servers/gmail/tools")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "ok")
        names = sorted(t["name"] for t in body["tools"])
        self.assertEqual(
            names, ["gmail_get_thread", "gmail_search_messages"]
        )

    def test_mcp_probe_unknown_server_is_404(self):
        res = self.client.get("/api/mcp-servers/nope/tools")
        self.assertEqual(res.status_code, 404)

    def test_index_has_all_eight_views(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        for label in (
            "Overview",
            "Memory",
            "Schedules",
            "Escalations",
            "Write-back",
            "MCP servers",
            "Agents",
            "Approval queue",
        ):
            self.assertIn(label, res.text)


class TestCorruptStoresDegradeToEmpty(unittest.TestCase):
    def test_garbage_in_every_store_file_is_an_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config.from_env()
            paths = {}
            for key, name in (
                ("approvals", "approvals.json"),
                ("memory", "memory.json"),
                ("schedules", "schedules.json"),
                ("escalations", "escalations.json"),
                ("mcp_servers", "mcp-servers.json"),
            ):
                path = os.path.join(tmp, name)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("{not json at all")
                paths[key] = path
            config.approvals_file = paths["approvals"]
            config.memory_file = paths["memory"]
            config.schedules_file = paths["schedules"]
            config.escalations_file = paths["escalations"]
            config.mcp_servers_file = paths["mcp_servers"]
            client = TestClient(create_app(config=config))
            res = client.get("/api/state")
            self.assertEqual(res.status_code, 200)
            state = res.json()
            self.assertEqual(state["queue"], [])
            self.assertEqual(state["memory"], [])
            self.assertEqual(state["schedules"], [])
            self.assertEqual(state["escalations"], [])
            self.assertEqual(state["writeback"], [])
            self.assertEqual(state["mcp_servers"], [])
            self.assertEqual(
                state["overview"],
                {
                    "open_escalations": 0,
                    "due_schedules": 0,
                    "memory_accounts": 0,
                    "write_drafts_pending": 0,
                },
            )


class TestBuildStateDefaults(unittest.TestCase):
    def test_build_state_without_stores_covers_new_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config.from_env()
            config.approvals_file = os.path.join(tmp, "approvals.json")
            config.memory_file = os.path.join(tmp, "memory.json")
            config.schedules_file = os.path.join(tmp, "schedules.json")
            config.escalations_file = os.path.join(tmp, "escalations.json")
            config.mcp_servers_file = os.path.join(tmp, "mcp-servers.json")
            queue = ApprovalQueue(config.approvals_file)
            state = build_state(queue, config)
            for key in (
                "overview",
                "memory",
                "schedules",
                "escalations",
                "writeback",
                "mcp_servers",
            ):
                self.assertIn(key, state)
                self.assertEqual(state[key], [] if key != "overview" else {
                    "open_escalations": 0,
                    "due_schedules": 0,
                    "memory_accounts": 0,
                    "write_drafts_pending": 0,
                })


if __name__ == "__main__":
    unittest.main()
