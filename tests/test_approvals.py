"""Tests for the approval queue: submit, decide, persist."""

import json
import os
import tempfile
import unittest

from harness.approvals import ApprovalQueue


class TestApprovalQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        os.unlink(self.tmp.name)  # start with no file, queue creates it
        self.queue = ApprovalQueue(self.tmp.name)

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_submit_is_pending(self):
        item = self.queue.submit(
            agent="renewal-desk",
            job={"account": "Acme Corp"},
            draft="Draft text.",
            sources=["salesforce_get_account"],
        )
        self.assertEqual(item.status, "pending")
        self.assertTrue(item.id.startswith("appr-"))
        self.assertEqual(item.sources, ["salesforce_get_account"])

    def test_list_pending_only(self):
        a = self.queue.submit("renewal-desk", {}, "one")
        b = self.queue.submit("briefer", {}, "two")
        self.queue.approve(a.id)
        pending = self.queue.list_pending()
        self.assertEqual([i.id for i in pending], [b.id])

    def test_approve_and_reject(self):
        item = self.queue.submit("briefer", {}, "draft")
        approved = self.queue.approve(item.id, note="Looks good.")
        self.assertEqual(approved.status, "approved")
        self.assertEqual(approved.note, "Looks good.")
        self.assertIsNotNone(approved.decided_at)
        other = self.queue.submit("briefer", {}, "draft 2")
        rejected = self.queue.reject(other.id, note="Wrong account.")
        self.assertEqual(rejected.status, "rejected")

    def test_edit_replaces_draft(self):
        item = self.queue.submit("briefer", {}, "original")
        edited = self.queue.edit(item.id, "human rewrite")
        self.assertEqual(edited.draft, "human rewrite")
        self.assertEqual(edited.status, "edited")

    def test_unknown_id_raises(self):
        with self.assertRaises(KeyError):
            self.queue.approve("appr-doesnotexist")

    def test_persists_across_instances(self):
        item = self.queue.submit("renewal-desk", {"account": "Acme"}, "draft")
        reloaded = ApprovalQueue(self.tmp.name)
        found = reloaded.get(item.id)
        self.assertIsNotNone(found)
        self.assertEqual(found.draft, "draft")
        self.assertEqual(found.status, "pending")

    def test_file_is_human_readable_json(self):
        self.queue.submit("briefer", {}, "draft")
        with open(self.tmp.name, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["agent"], "briefer")


if __name__ == "__main__":
    unittest.main()
