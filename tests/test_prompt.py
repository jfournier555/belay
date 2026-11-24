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

    def test_unknown_account_asks_which_account_not_a_wrong_draft(self):
        # v1.16.0: a prompt with no resolvable account is asked, not
        # refused: a 200 needs-account reply with a working example,
        # and no draft queued.
        before = len(self._queue().list_pending())
        res = self.client.post(
            "/api/prompt", json={"text": "draft a renewal plan"}
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["kind"], "needs_account")
        self.assertIn("account", body["message"].lower())
        self.assertIn("draft a renewal brief for", body["example"])
        self.assertEqual(len(self._queue().list_pending()), before)

    def test_ambiguous_account_asks_for_one_with_candidates(self):
        _seed_queue(self.config.approvals_file, ["Acme", "Apex"])
        res = self.client.post(
            "/api/prompt",
            json={"text": "draft a renewal plan for Acme and Apex"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["kind"], "needs_account")
        self.assertEqual(body["candidates"], ["Acme", "Apex"])
        self.assertIn("Acme", body["message"])

    def test_empty_prompt_is_400(self):
        res = self.client.post("/api/prompt", json={"text": "   "})
        self.assertEqual(res.status_code, 400)

    def test_prompt_result_kind_is_draft(self):
        res = self.client.post(
            "/api/prompt",
            json={"text": "draft a renewal plan for Acme Corp"},
        )
        self.assertEqual(res.json()["kind"], "draft")

    def test_icon_route_serves_the_app_icon(self):
        res = self.client.get("/belay-icon.png")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.headers["content-type"].startswith("image/png"))
        self.assertEqual(res.content[:8], b"\x89PNG\r\n\x1a\n")

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


class TestCrossAccountAnswers(unittest.TestCase):
    """v1.16.0: cross-account questions are answered from the queue
    and memory data, not refused for lacking an account name."""

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
        self.queue = _seed_queue(self.config.approvals_file, ["Acme Corp"])
        self.client = TestClient(create_app(self.config))

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_risk(self, account, count=1):
        for _ in range(count):
            self.queue.submit(
                agent="risk-watcher",
                job={"account": account},
                draft="Risk signals for %s: champion departure." % account,
                sources=["salesforce_list_cases"],
                proof=[],
            )

    def test_most_risk_answered_from_risk_watcher_drafts(self):
        self._seed_risk("Acme Corp", count=2)
        self._seed_risk("Globex Inc", count=1)
        before = len(self.queue.list_pending())
        res = self.client.post(
            "/api/prompt", json={"text": "What account has most risk"}
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["kind"], "answer")
        self.assertIn("Acme Corp", body["answer"])
        self.assertEqual(body["accounts"][0], "Acme Corp")
        self.assertIn("Globex Inc", body["accounts"])
        self.assertEqual(len(body["item_ids"]), 3)
        # No new draft was queued for the answer.
        self.assertEqual(len(self.queue.list_pending()), before)

    def test_risk_question_with_no_risk_data_says_so(self):
        res = self.client.post(
            "/api/prompt", json={"text": "Which account is most at risk?"}
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["kind"], "answer")
        self.assertIn("No accounts are flagged at-risk", body["answer"])
        self.assertEqual(body["accounts"], [])

    def test_plain_question_about_unknown_account_asks(self):
        # "What does Initech LLC do?" names no known account: ask
        # which account, with an example, instead of refusing.
        before = len(self.queue.list_pending())
        res = self.client.post(
            "/api/prompt", json={"text": "What does Initech LLC do?"}
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["kind"], "needs_account")
        self.assertIn("Acme Corp", body["known_accounts"])
        self.assertIn("draft a renewal brief for", body["example"])
        self.assertEqual(len(self.queue.list_pending()), before)

    def test_which_accounts_lists_known_accounts(self):
        res = self.client.post(
            "/api/prompt", json={"text": "Which accounts do you know?"}
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["kind"], "answer")
        self.assertIn("Acme Corp", body["accounts"])


if __name__ == "__main__":
    unittest.main()
