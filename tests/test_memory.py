"""Tests for memory (v1.2.0).

Past drafts, decisions, and corrections per account, injected as
context next run. Capture happens in the approval queue: approve,
reject, and edit each record the outcome for the draft's account,
and an edit stores the correction itself (a diff), not just a flag.
Injection loads that account's memory newest-first, bounded by the
per-job token budget. Memory never crosses accounts.

These run the full loop with stub tools and the stub LLM, so no
network and no credentials are needed.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from harness import AGENTS, AccountMemory, ApprovalQueue, TokenBudget
from harness.agents import _extract_account
from harness.config import Config
from harness.llm import StubLLM
from harness.memory import _account_from_job
from harness.run import build_registry, main


def _temp_path() -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    os.unlink(tmp.name)
    return tmp.name


def _seed_queue(queue_path: str, mem_path: str, drafts: list) -> ApprovalQueue:
    """Submit drafts to a queue with a memory store attached. Each
    draft is (agent, account, text). Returns the queue."""
    memory = AccountMemory(mem_path)
    queue = ApprovalQueue(queue_path, memory=memory)
    for agent, account, text in drafts:
        queue.submit(agent=agent, job={"account": account}, draft=text)
    return queue


class TestMemoryStore(unittest.TestCase):
    def test_approve_records_outcome(self):
        qpath, mpath = _temp_path(), _temp_path()
        try:
            queue = _seed_queue(
                qpath,
                mpath,
                [("renewal-desk", "Acme Corp", "Renewal draft text.")],
            )
            item = queue.list_all()[0]
            queue.approve(item.id)
            records = AccountMemory(mpath).records_for("Acme Corp")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].outcome, "approved")
            self.assertEqual(records[0].agent, "renewal-desk")
            self.assertEqual(records[0].draft_id, item.id)
            self.assertEqual(records[0].account, "Acme Corp")
            self.assertIn("Renewal draft text.", records[0].excerpt)
        finally:
            for path in (qpath, mpath):
                if os.path.exists(path):
                    os.unlink(path)

    def test_edit_stores_the_correction(self):
        qpath, mpath = _temp_path(), _temp_path()
        try:
            queue = _seed_queue(
                qpath,
                mpath,
                [
                    (
                        "renewal-desk",
                        "Acme Corp",
                        "Line one.\nLine two.\n",
                    )
                ],
            )
            item = queue.list_all()[0]
            queue.edit(item.id, "Line one.\nLine two changed.\n")
            records = AccountMemory(mpath).records_for("Acme Corp")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].outcome, "edited")
            # The correction itself: what changed, not just a flag.
            self.assertIn("-Line two.", records[0].correction)
            self.assertIn("+Line two changed.", records[0].correction)
            self.assertIn("Line two changed.", records[0].render())
        finally:
            for path in (qpath, mpath):
                if os.path.exists(path):
                    os.unlink(path)

    def test_reject_recorded(self):
        qpath, mpath = _temp_path(), _temp_path()
        try:
            queue = _seed_queue(
                qpath,
                mpath,
                [("briefer", "Acme Corp", "Brief draft.")],
            )
            item = queue.list_all()[0]
            queue.reject(item.id)
            records = AccountMemory(mpath).records_for("Acme Corp")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].outcome, "rejected")
        finally:
            for path in (qpath, mpath):
                if os.path.exists(path):
                    os.unlink(path)

    def test_records_newest_first(self):
        qpath, mpath = _temp_path(), _temp_path()
        try:
            queue = _seed_queue(
                qpath,
                mpath,
                [
                    ("briefer", "Acme Corp", "First draft."),
                    ("briefer", "Acme Corp", "Second draft."),
                    ("briefer", "Acme Corp", "Third draft."),
                ],
            )
            for item in queue.list_all():
                queue.approve(item.id)
            with open(mpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            for i, entry in enumerate(data["Acme Corp"]):
                entry["timestamp"] = "2026-11-21T10:00:%02d+00:00" % i
            with open(mpath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            records = AccountMemory(mpath).records_for("Acme Corp")
            self.assertEqual(
                [r.excerpt for r in records],
                ["Third draft.", "Second draft.", "First draft."],
            )
        finally:
            for path in (qpath, mpath):
                if os.path.exists(path):
                    os.unlink(path)

    def test_account_isolation(self):
        qpath, mpath = _temp_path(), _temp_path()
        try:
            queue = _seed_queue(
                qpath,
                mpath,
                [("briefer", "Acme Corp", "Acme draft.")],
            )
            queue.approve(queue.list_all()[0].id)
            self.assertEqual(
                len(AccountMemory(mpath).records_for("Globex Inc")), 0
            )
            self.assertEqual(
                len(AccountMemory(mpath).records_for("acme corp")), 0
            )
        finally:
            for path in (qpath, mpath):
                if os.path.exists(path):
                    os.unlink(path)

    def test_clear_only_that_account(self):
        qpath, mpath = _temp_path(), _temp_path()
        try:
            queue = _seed_queue(
                qpath,
                mpath,
                [
                    ("briefer", "Acme Corp", "Acme draft."),
                    ("briefer", "Globex Inc", "Globex draft."),
                ],
            )
            for item in queue.list_all():
                queue.approve(item.id)
            memory = AccountMemory(mpath)
            self.assertEqual(memory.clear("Acme Corp"), 1)
            self.assertEqual(memory.records_for("Acme Corp"), [])
            self.assertEqual(len(memory.records_for("Globex Inc")), 1)
            self.assertEqual(memory.clear("Acme Corp"), 0)
        finally:
            for path in (qpath, mpath):
                if os.path.exists(path):
                    os.unlink(path)

    def test_cap_fifty_per_account(self):
        mpath = _temp_path()
        try:
            memory = AccountMemory(mpath)
            queue = ApprovalQueue(_temp_path(), memory=memory)
            for i in range(55):
                item = queue.submit(
                    agent="briefer",
                    job={"account": "Acme Corp"},
                    draft="Draft %d." % i,
                )
                queue.approve(item.id)
            records = memory.records_for("Acme Corp")
            self.assertEqual(len(records), 50)
            excerpts = [r.excerpt for r in records]
            self.assertNotIn("Draft 0.", excerpts)
            self.assertIn("Draft 54.", excerpts)
        finally:
            if os.path.exists(mpath):
                os.unlink(mpath)

    def test_corrupt_and_missing_files_tolerated(self):
        mpath = _temp_path()
        try:
            with open(mpath, "w", encoding="utf-8") as f:
                f.write("{not json")
            memory = AccountMemory(mpath)
            self.assertEqual(memory.records_for("Acme Corp"), [])
            self.assertEqual(memory.render("Acme Corp", TokenBudget(8000)), "")
            queue = ApprovalQueue(_temp_path(), memory=memory)
            item = queue.submit(
                agent="briefer",
                job={"account": "Acme Corp"},
                draft="Draft.",
            )
            queue.approve(item.id)
            self.assertEqual(
                len(AccountMemory(mpath).records_for("Acme Corp")), 1
            )
        finally:
            if os.path.exists(mpath):
                os.unlink(mpath)

    def test_queue_without_memory_records_nothing(self):
        qpath, mpath = _temp_path(), _temp_path()
        try:
            queue = ApprovalQueue(qpath)
            item = queue.submit(
                agent="briefer",
                job={"account": "Acme Corp"},
                draft="Draft.",
            )
            queue.approve(item.id)
            queue.edit(item.id, "Edited.")
            queue.reject(item.id)
            self.assertFalse(os.path.exists(mpath))
        finally:
            for path in (qpath, mpath):
                if os.path.exists(path):
                    os.unlink(path)


class TestMemoryRenderAndBudget(unittest.TestCase):
    def _seeded(self, mpath: str) -> None:
        queue = _seed_queue(
            _temp_path(),
            mpath,
            [
                ("renewal-desk", "Acme Corp", "First draft."),
                ("renewal-desk", "Acme Corp", "Second draft."),
            ],
        )
        for item in queue.list_all():
            queue.approve(item.id)

    def test_render_newest_first_within_budget(self):
        mpath = _temp_path()
        try:
            self._seeded(mpath)
            text = AccountMemory(mpath).render(
                "Acme Corp", TokenBudget(8000)
            )
            self.assertIn("Second draft.", text)
            self.assertIn("First draft.", text)
            self.assertLess(text.index("Second draft."), text.index("First draft."))
        finally:
            if os.path.exists(mpath):
                os.unlink(mpath)

    def test_tiny_budget_renders_nothing(self):
        mpath = _temp_path()
        try:
            self._seeded(mpath)
            self.assertEqual(
                AccountMemory(mpath).render("Acme Corp", TokenBudget(1)),
                "",
            )
        finally:
            if os.path.exists(mpath):
                os.unlink(mpath)

    def test_budget_stops_at_first_record_that_does_not_fit(self):
        mpath = _temp_path()
        try:
            self._seeded(mpath)
            # Two short records; a budget that fits one but not both.
            text = AccountMemory(mpath).render(
                "Acme Corp", TokenBudget(30)
            )
            self.assertIn("Second draft.", text)
            self.assertNotIn("First draft.", text)
        finally:
            if os.path.exists(mpath):
                os.unlink(mpath)

    def test_account_extractor_agrees_with_agents(self):
        jobs = [
            {"account": "Acme Corp"},
            {"text": "draft a renewal plan for Acme Corp"},
            {"text": "brief for Globex Inc"},
            {"text": "hello"},
            {"account": "  ", "text": "draft a plan for Initech"},
        ]
        for job in jobs:
            self.assertEqual(
                _account_from_job(job), _extract_account(job), job
            )


class TestMemoryInjection(unittest.TestCase):
    def setUp(self):
        config = Config.from_env()
        config.stub_mode = True
        self.registry = build_registry(config)
        self.llm = StubLLM()

    def _seed_edit(self, mem_path: str, account: str) -> None:
        queue = _seed_queue(
            _temp_path(),
            mem_path,
            [("renewal-desk", account, "Renewal draft v1.\n")],
        )
        item = queue.list_all()[0]
        queue.edit(
            item.id,
            "Renewal draft v2.\nMARKER CORRECTION lead with the renewal date.\n",
        )

    def _run(self, account: str, mem_path: str, no_memory: bool = False):
        job = {"account": account, "memory_file": mem_path}
        if no_memory:
            job["no_memory"] = True
        return AGENTS["renewal-desk"].run(
            job, self.registry, self.llm, TokenBudget(8000)
        )

    def test_correction_in_next_draft(self):
        mpath = _temp_path()
        try:
            self._seed_edit(mpath, "Acme Corp")
            draft = self._run("Acme Corp", mpath)
            self.assertIn("MARKER CORRECTION", draft.text)
            self.assertIn("lead with the renewal date", draft.text)
        finally:
            if os.path.exists(mpath):
                os.unlink(mpath)

    def test_no_memory_flag_runs_cold(self):
        mpath = _temp_path()
        try:
            self._seed_edit(mpath, "Acme Corp")
            draft = self._run("Acme Corp", mpath, no_memory=True)
            self.assertNotIn("MARKER CORRECTION", draft.text)
        finally:
            if os.path.exists(mpath):
                os.unlink(mpath)

    def test_other_account_sees_nothing(self):
        mpath = _temp_path()
        try:
            self._seed_edit(mpath, "Acme Corp")
            draft = self._run("Globex Inc", mpath)
            self.assertNotIn("MARKER CORRECTION", draft.text)
        finally:
            if os.path.exists(mpath):
                os.unlink(mpath)

    def test_no_memory_file_runs_cold(self):
        mpath = _temp_path()
        try:
            draft = AGENTS["renewal-desk"].run(
                {"account": "Acme Corp"},
                self.registry,
                self.llm,
                TokenBudget(8000),
            )
            self.assertNotIn("MARKER CORRECTION", draft.text)
            self.assertTrue(draft.proof_passed)
        finally:
            if os.path.exists(mpath):
                os.unlink(mpath)


class TestMemoryCli(unittest.TestCase):
    def _run(self, argv, env):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False):
            with redirect_stdout(buf):
                code = main(argv)
        return code, buf.getvalue()

    def test_edit_show_clear_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "HARNESS_APPROVALS_FILE": os.path.join(tmp, "a.json"),
                "HARNESS_MEMORY_FILE": os.path.join(tmp, "m.json"),
            }
            code, _ = self._run(
                [
                    "run",
                    "--agent",
                    "renewal-desk",
                    "--account",
                    "Acme Corp",
                    "--stub",
                ],
                env,
            )
            self.assertEqual(code, 0)
            queue = ApprovalQueue(env["HARNESS_APPROVALS_FILE"])
            item = queue.list_all()[0]
            code, out = self._run(
                [
                    "approvals",
                    "edit",
                    item.id,
                    "--draft",
                    "Edited draft MARKER.",
                ],
                env,
            )
            self.assertEqual(code, 0)
            self.assertIn("Edited:", out)
            code, out = self._run(
                ["memory", "show", "Acme Corp"], env
            )
            self.assertEqual(code, 0)
            self.assertIn("edited", out)
            self.assertIn("MARKER", out)
            code, out = self._run(["memory", "accounts"], env)
            self.assertEqual(code, 0)
            self.assertIn("Acme Corp", out)
            code, out = self._run(
                ["memory", "clear", "Acme Corp"], env
            )
            self.assertEqual(code, 0)
            self.assertIn("Forgot 1 record", out)
            code, out = self._run(
                ["memory", "show", "Acme Corp"], env
            )
            self.assertIn("No memory for Acme Corp.", out)

    def test_memory_show_unknown_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "HARNESS_MEMORY_FILE": os.path.join(tmp, "m.json"),
            }
            code, out = self._run(
                ["memory", "show", "Nobody Inc"], env
            )
            self.assertEqual(code, 0)
            self.assertIn("No memory for Nobody Inc.", out)

    def test_run_no_memory_flag(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as caught:
                main(["run", "--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--no-memory", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
