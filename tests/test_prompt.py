"""Tests for the dashboard prompt window (v1.15.0).

The prompt bar takes free text, resolves the account from it, routes
through the existing router, and queues the draft exactly like a CLI
run: same proof checklist, same approval gate. These tests pin the
account resolution (known match, longest wins, ambiguous tie, the
"for" fallback, unknown) and the endpoint (happy path, routing,
unknown/ambiguous/empty prompts). They run against temp store files
in stub mode, so no credentials and no network calls.
"""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from harness import ApprovalQueue, Config
from harness.dashboard import create_app
from harness.prompt import (
    AmbiguousAccountError,
    UnknownAccountError,
    known_accounts,
    resolve_account,
)
from harness.memory import AccountMemory
from harness.scheduler import ScheduleStore


def _seed_queue(path, accounts):
    queue = ApprovalQueue(path)
    for account in accounts:
        queue.submit(
            agent="briefer",
            job={"account": account},
            draft="Brief for %s." % account,
            sources=["salesforce_get_account"],
            proof=[],
        )
    return queue


class TestResolveAccount(unittest.TestCase):
    def test_known_account_named_in_text(self):
        self.assertEqual(
            resolve_account(
                "draft a renewal plan for Acme Corp", {"Acme Corp"}
            ),
            "Acme Corp",
        )

    def test_match_is_case_insensitive(self):
        self.assertEqual(
            resolve_account("DRAFT A RENEWAL PLAN FOR acme corp", {"Acme Corp"}),
            "Acme Corp",
        )

    def test_longest_match_wins(self):
        self.assertEqual(
            resolve_account(
                "draft a renewal plan for Acme Corp",
                {"Acme", "Acme Corp"},
            ),
            "Acme Corp",
        )

    def test_word_boundaries_keep_common_words_out(self):
        # "Hope" the account does not match "hopefully" in the text.
        with self.assertRaises(UnknownAccountError):
            resolve_account(
                "hopefully the renewal lands soon", {"Hope"}
            )

    def test_ambiguous_tie_raises_with_candidates(self):
        with self.assertRaises(AmbiguousAccountError) as caught:
            resolve_account(
                "draft a renewal plan for Acme and Apex",
                {"Acme", "Apex"},
            )
        self.assertEqual(caught.exception.candidates, ["Acme", "Apex"])

    def test_for_fallback_for_a_new_account(self):
        self.assertEqual(
            resolve_account("draft a renewal plan for Initech LLC", set()),
            "Initech LLC",
        )

    def test_no_known_match_and_no_for_is_unknown(self):
        with self.assertRaises(UnknownAccountError):
            resolve_account("draft a renewal plan", {"Acme Corp"})

    def test_empty_text_is_unknown(self):
        with self.assertRaises(UnknownAccountError):
            resolve_account("", {"Acme Corp"})


class TestKnownAccounts(unittest.TestCase):
    def test_gathers_memory_queue_and_schedules(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = os.path.join(tmp, "approvals.json")
            queue = _seed_queue(queue_path, ["Acme Corp"])
            globex_item = queue.submit(
                agent="briefer",
                job={"account": "Globex Inc"},
                draft="Brief for Globex Inc.",
                sources=["salesforce_get_account"],
                proof=[],
            )
            memory = AccountMemory(os.path.join(tmp, "memory.json"))
            memory.record(globex_item, "approved")
            schedules = ScheduleStore(os.path.join(tmp, "schedules.json"))
            schedules.add(
                {
                    "agent": "briefer",
                    "account": "Initech LLC",
                    "cadence": "daily",
                    "time": "07:00",
                    "timezone": "America/Los_Angeles",
                },
                valid_agents={"briefer"},
            )
            self.assertEqual(
                known_accounts(queue, memory, schedules),
                {"Acme Corp", "Globex Inc", "Initech LLC"},
            )


class TestPromptEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(
            approvals_file=os.path.join(self.tmp.name, "approvals.json"),
            memory_file=os.path.join(self.tmp.name, "memory.json"),
            schedules_file=os.path.join(self.tmp.name, "schedules.json"),
            escalations_file=os.path.join(self.tmp.name, "escalations.json"),
            mcp_servers_file=os.path.join(self.tmp.name, "mcp-servers.json"),
            stub_mode=True,
        )
        # Acme Corp is a known account (queue), so the prompt can name
        # it without a "for".
        _seed_queue(self.config.approvals_file, ["Acme Corp"])
        self.client = TestClient(create_app(self.config))

    def tearDown(self):
        self.tmp.cleanup()

    def _queue(self):
        return ApprovalQueue(self.config.approvals_file)

    def test_index_renders_the_prompt_bar(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn('id="prompt-form"', res.text)
        self.assertIn("Ask Belay", res.text)

    def test_prompt_routes_and_queues_like_a_cli_run(self):
        res = self.client.post(
            "/api/prompt",
            json={"text": "draft a renewal plan for Acme Corp"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["agent"], "renewal-desk")
        self.assertEqual(body["account"], "Acme Corp")
        item = body["item"]
        self.assertEqual(item["status"], "pending")
        # Same proof checklist a CLI run would carry.
        self.assertTrue(item["proof"])
        self.assertTrue(item["draft"])
        pending = self._queue().list_pending()
        self.assertEqual(len(pending), 2)
        self.assertEqual(pending[-1].id, item["id"])
        self.assertEqual(pending[-1].agent, "renewal-desk")

    def test_prompt_uses_a_known_account_without_for(self):
        res = self.client.post(
            "/api/prompt",
            json={"text": "draft a renewal plan, Acme Corp is at risk"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["account"], "Acme Corp")

    def test_prompt_falls_back_to_the_for_rule(self):
        res = self.client.post(
            "/api/prompt",
            json={"text": "draft a renewal plan for Initech LLC"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["account"], "Initech LLC")
        self.assertEqual(res.json()["agent"], "renewal-desk")

    def test_no_keyword_match_defaults_to_briefer(self):
        res = self.client.post(
            "/api/prompt",
            json={"text": "tell me about Acme Corp"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["agent"], "briefer")
        self.assertEqual(res.json()["route"]["confidence"], "low")

    def test_unknown_account_is_400_not_a_wrong_draft(self):
        before = len(self._queue().list_pending())
        res = self.client.post(
            "/api/prompt", json={"text": "draft a renewal plan"}
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("account", res.json()["detail"].lower())
        self.assertEqual(len(self._queue().list_pending()), before)

    def test_ambiguous_account_is_400_with_candidates(self):
        _seed_queue(self.config.approvals_file, ["Acme", "Apex"])
        res = self.client.post(
            "/api/prompt",
            json={"text": "draft a renewal plan for Acme and Apex"},
        )
        self.assertEqual(res.status_code, 400)
        detail = res.json()["detail"]
        self.assertIn("Acme", detail)
        self.assertIn("Apex", detail)

    def test_empty_prompt_is_400(self):
        res = self.client.post("/api/prompt", json={"text": "   "})
        self.assertEqual(res.status_code, 400)

    def test_queued_prompt_draft_shows_in_state(self):
        self.client.post(
            "/api/prompt",
            json={"text": "draft a renewal plan for Acme Corp"},
        )
        state = self.client.get("/api/state").json()
        self.assertEqual(state["stats"]["drafts_ready"], 2)
        agents = [i for i in state["queue"] if i["agent"] == "renewal-desk"]
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["account"], "Acme Corp")


if __name__ == "__main__":
    unittest.main()
