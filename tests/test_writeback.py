"""Tests for write-back (v1.3.0): the first write tools plus
approval-gated execution.

The gate is the point, so the tests pin it: a write tool never runs
except through execute_writes on an approved draft; rejected drafts
never execute; an approved draft executes exactly once per op; a
failed op can be retried without re-running the ops that already
applied. Stub writes simulate and change nothing.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from connectors.salesforce.tools import (
    log_activity,
    move_stage,
    salesforce_tools,
    update_fields,
)
from harness import AGENTS, ApprovalQueue, Config, TokenBudget, ToolRegistry
from harness.llm import StubLLM
from harness.registry import Tool
from harness.run import main
from harness.writeback import (
    build_write_plan,
    execute_writes,
    format_preview,
    format_write_plan,
    parse_change_spec,
)

from tests.test_agents import _stub_registry


def _stub_config() -> Config:
    config = Config.from_env()
    config.stub_mode = True
    return config


def _gathered() -> dict:
    """The stub Salesforce results the updater gathers, as raw results."""
    config = _stub_config()
    tools = {t.name: t for t in salesforce_tools(config)}
    return {
        "salesforce_get_account": tools["salesforce_get_account"].run(
            account="Acme Corp"
        ),
        "salesforce_list_opportunities": tools[
            "salesforce_list_opportunities"
        ].run(account="Acme Corp"),
        "salesforce_list_cases": tools["salesforce_list_cases"].run(
            account="Acme Corp"
        ),
    }


class TestParseChangeSpec(unittest.TestCase):
    def test_update_single_field(self):
        spec = parse_change_spec(
            "update Opportunity 006STUB000000001 StageName=Closed Won"
        )
        self.assertEqual(
            spec,
            {
                "kind": "update",
                "object": "Opportunity",
                "record_id": "006STUB000000001",
                "fields": {"StageName": "Closed Won"},
            },
        )

    def test_update_multiple_fields(self):
        spec = parse_change_spec(
            "update Opportunity 006STUB000000001 StageName=Closed Won, Amount=130000"
        )
        self.assertEqual(
            spec["fields"], {"StageName": "Closed Won", "Amount": "130000"}
        )

    def test_stage(self):
        spec = parse_change_spec("stage 006STUB000000001 Closed Won")
        self.assertEqual(
            spec,
            {"kind": "stage", "record_id": "006STUB000000001", "stage": "Closed Won"},
        )

    def test_log_with_description(self):
        spec = parse_change_spec(
            "log 001STUB000000001 Call with Acme Corp | Discussed renewal"
        )
        self.assertEqual(
            spec,
            {
                "kind": "log",
                "record_id": "001STUB000000001",
                "subject": "Call with Acme Corp",
                "description": "Discussed renewal",
            },
        )

    def test_log_without_description(self):
        spec = parse_change_spec("log 001STUB000000001 Call with Acme Corp")
        self.assertEqual(spec["description"], None)

    def test_bad_specs_raise(self):
        for bad in (
            "",
            "frobnicate Opportunity 1 StageName=X",
            "update Opportunity 006STUB000000001",
            "update Opportunity 006STUB000000001 StageName",
            "stage 006STUB000000001",
            "log 001STUB000000001",
        ):
            with self.assertRaises(ValueError, msg=bad):
                parse_change_spec(bad)


class TestBuildWritePlan(unittest.TestCase):
    def test_preview_shows_old_and_new(self):
        ops, preview = build_write_plan(
            [parse_change_spec("stage 006STUB000000001 Closed Won")],
            _gathered(),
        )
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["tool"], "salesforce_move_stage")
        self.assertEqual(ops[0]["stage"], "Closed Won")
        self.assertEqual(ops[0]["previous_stage"], "Negotiation")
        self.assertIn("Negotiation -> Closed Won", preview[0])

    def test_update_preview_uses_gathered_record(self):
        ops, preview = build_write_plan(
            [
                parse_change_spec(
                    "update Opportunity 006STUB000000002 Amount=50000"
                )
            ],
            _gathered(),
        )
        self.assertEqual(ops[0]["tool"], "salesforce_update_fields")
        self.assertIn("45000 -> 50000", preview[0])

    def test_log_preview_has_no_old_value(self):
        ops, preview = build_write_plan(
            [parse_change_spec("log 001STUB000000001 Call with Acme")],
            _gathered(),
        )
        self.assertEqual(ops[0]["tool"], "salesforce_log_activity")
        self.assertIn('Log activity on 001STUB000000001: "Call with Acme"', preview[0])

    def test_unknown_record_is_labeled_not_guessed(self):
        ops, preview = build_write_plan(
            [parse_change_spec("stage 006NOPE Closed Won")],
            _gathered(),
        )
        self.assertIn("not in gathered context", preview[0])
        self.assertEqual(ops[0]["previous_stage"], "not in gathered context")

    def test_preview_block_warns_nothing_applied(self):
        _ops, preview = build_write_plan(
            [parse_change_spec("stage 006STUB000000001 Closed Won")],
            _gathered(),
        )
        block = format_preview(preview)
        self.assertIn("DRY-RUN PREVIEW", block)
        self.assertIn("nothing has been applied", block)

    def test_format_write_plan_lists_ops(self):
        ops, _preview = build_write_plan(
            [
                parse_change_spec("stage 006STUB000000001 Closed Won"),
                parse_change_spec("log 001STUB000000001 Call with Acme"),
            ],
            _gathered(),
        )
        text = format_write_plan(ops)
        self.assertIn("Write plan (2 change(s)):", text)
        self.assertIn("stage -> Closed Won", text)


class TestWriteToolsStub(unittest.TestCase):
    def test_update_fields_simulates_and_changes_nothing(self):
        result = update_fields(
            _stub_config(),
            "Opportunity",
            "006STUB000000001",
            {"StageName": "Closed Won"},
        )
        self.assertTrue(result["_stub"])
        self.assertTrue(result["simulated"])
        self.assertFalse(result["applied"])
        self.assertEqual(
            result["would_change"], {"StageName": "Closed Won"}
        )

    def test_log_activity_simulates_and_creates_nothing(self):
        result = log_activity(
            _stub_config(), "001STUB000000001", "Call with Acme"
        )
        self.assertTrue(result["simulated"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["would_create"]["subject"], "Call with Acme")

    def test_move_stage_simulates(self):
        result = move_stage(
            _stub_config(), "006STUB000000001", "Closed Won"
        )
        self.assertTrue(result["simulated"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["would_change"], {"StageName": "Closed Won"})

    def test_write_tools_are_flagged_write(self):
        tools = {t.name: t for t in salesforce_tools(_stub_config())}
        for name in (
            "salesforce_update_fields",
            "salesforce_log_activity",
            "salesforce_move_stage",
        ):
            self.assertTrue(tools[name].write, name)
        for name in (
            "salesforce_get_account",
            "salesforce_list_opportunities",
            "salesforce_list_cases",
        ):
            self.assertFalse(tools[name].write, name)


class TestApprovalGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "approvals.json")
        self.queue = ApprovalQueue(self.path)
        self.registry = _stub_registry()
        self.calls: list[str] = []
        inner = self.registry.get("salesforce_move_stage")
        original_handler = inner.handler

        def counting(**kwargs):
            self.calls.append("salesforce_move_stage")
            return original_handler(**kwargs)

        inner.handler = counting

    def tearDown(self):
        self.tmp.cleanup()

    def _write_item(self):
        ops, _preview = build_write_plan(
            [parse_change_spec("stage 006STUB000000001 Closed Won")],
            _gathered(),
        )
        return self.queue.submit(
            agent="salesforce-updater",
            job={"account": "Acme Corp"},
            draft="Move the renewal to Closed Won.",
            writes=ops,
        )

    def test_pending_never_executes(self):
        item = self._write_item()
        with self.assertRaises(ValueError):
            execute_writes(self.queue, self.registry, item)
        self.assertEqual(self.calls, [])

    def test_rejected_never_executes(self):
        item = self._write_item()
        self.queue.reject(item.id)
        with self.assertRaises(ValueError):
            execute_writes(self.queue, self.registry, item)
        self.assertEqual(self.calls, [])

    def test_approved_executes_and_records(self):
        item = self._write_item()
        self.queue.approve(item.id)
        results = execute_writes(self.queue, self.registry, item)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["success"])
        self.assertTrue(results[0]["result"]["simulated"])
        self.assertEqual(len(self.calls), 1)
        stored = self.queue.get(item.id)
        self.assertEqual(len(stored.execution), 1)
        self.assertIsNotNone(stored.executed_at)

    def test_approved_executes_exactly_once(self):
        item = self._write_item()
        self.queue.approve(item.id)
        execute_writes(self.queue, self.registry, item)
        second = execute_writes(self.queue, self.registry, item)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(second), 1)
        self.assertTrue(second[0]["success"])
        self.assertEqual(len(self.queue.get(item.id).execution), 1)

    def test_failed_op_retries_without_rerunning_applied_ops(self):
        # First op fails, second succeeds; a retry runs only the failed one.
        failing = ToolRegistry()
        attempts: list[str] = []

        def flaky(**kwargs):
            attempts.append(kwargs.get("record_id", ""))
            if len(attempts) == 1:
                raise RuntimeError("org is down")
            return {"applied": True, "simulated": True}

        failing.register(
            Tool(
                name="salesforce_move_stage",
                description="flaky",
                handler=flaky,
                write=True,
            )
        )
        ops, _preview = build_write_plan(
            [
                parse_change_spec("stage 006STUB000000001 Closed Won"),
                parse_change_spec("stage 006STUB000000002 Proposal"),
            ],
            _gathered(),
        )
        item = self.queue.submit(
            agent="salesforce-updater",
            job={"account": "Acme Corp"},
            draft="Two stage moves.",
            writes=ops,
        )
        self.queue.approve(item.id)
        first = execute_writes(self.queue, failing, item)
        self.assertFalse(first[0]["success"])
        self.assertTrue(first[1]["success"])
        second = execute_writes(self.queue, failing, item)
        self.assertTrue(second[0]["success"])
        self.assertTrue(second[1]["success"])
        self.assertEqual(len(attempts), 3)  # op 0 twice, op 1 once

    def test_non_write_tool_in_plan_is_refused(self):
        item = self.queue.submit(
            agent="salesforce-updater",
            job={"account": "Acme Corp"},
            draft="Bad plan.",
            writes=[
                {
                    "tool": "salesforce_get_account",
                    "label": "read disguised as a write",
                }
            ],
        )
        self.queue.approve(item.id)
        results = execute_writes(self.queue, self.registry, item)
        self.assertFalse(results[0]["success"])
        self.assertIn("not a write tool", results[0]["error"])

    def test_empty_plan_executes_nothing(self):
        item = self.queue.submit(
            agent="briefer", job={"account": "Acme Corp"}, draft="Read only."
        )
        self.queue.approve(item.id)
        self.assertEqual(execute_writes(self.queue, self.registry, item), [])


class TestUpdaterAgent(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_updater_drafts_preview_and_plan(self):
        agent = AGENTS["salesforce-updater"]
        draft = agent.run(
            {
                "account": "Acme Corp",
                "changes": [
                    parse_change_spec("stage 006STUB000000001 Closed Won"),
                    parse_change_spec(
                        "log 001STUB000000001 Call with Acme Corp"
                    ),
                ],
            },
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("DRY-RUN PREVIEW", draft.text)
        self.assertIn("Negotiation -> Closed Won", draft.text)
        self.assertEqual(len(draft.writes), 2)
        self.assertEqual(
            [w["tool"] for w in draft.writes],
            ["salesforce_move_stage", "salesforce_log_activity"],
        )
        self.assertTrue(draft.proof_passed)
        # The updater only reads while gathering: no write tool in sources.
        for source in draft.sources:
            self.assertFalse(source.startswith("salesforce_update"))
            self.assertNotIn("log_activity", source)
            self.assertNotIn("move_stage", source)

    def test_updater_with_no_changes_has_no_plan(self):
        agent = AGENTS["salesforce-updater"]
        draft = agent.run(
            {"account": "Acme Corp", "changes": []},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.writes, [])
        self.assertIn("No changes were requested", draft.text)
        self.assertFalse(draft.proof_passed)

    def test_updater_with_bad_spec_drafts_an_explanation(self):
        agent = AGENTS["salesforce-updater"]
        draft = agent.run(
            {"account": "Acme Corp", "changes": ["frobnicate the thing"]},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.writes, [])
        self.assertIn("not understood", draft.text)

    def test_updater_is_registered(self):
        self.assertEqual(len(AGENTS), 14)
        self.assertIn("salesforce-updater", AGENTS)


class TestWritebackCliLoop(unittest.TestCase):
    """The full loop through the CLI: draft, approve, applied, recorded."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "approvals.json")
        self.old_env = dict(os.environ)
        os.environ["HARNESS_APPROVALS_FILE"] = self.path

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        self.tmp.cleanup()

    def _run_cli(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def _queue(self):
        return ApprovalQueue(self.path)

    def test_approve_applies_and_records(self):
        code, out = self._run_cli(
            [
                "run",
                "--agent",
                "salesforce-updater",
                "--account",
                "Acme Corp",
                "--stub",
                "--change",
                "stage 006STUB000000001 Closed Won",
                "--show",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN PREVIEW", out)
        item = self._queue().list_pending()[0]
        code, out = self._run_cli(["approvals", "approve", item.id])
        self.assertEqual(code, 0)
        self.assertIn("Applied op 0", out)
        stored = self._queue().get(item.id)
        self.assertEqual(stored.status, "approved")
        self.assertEqual(len(stored.execution), 1)
        self.assertTrue(stored.execution[0]["success"])
        self.assertTrue(stored.execution[0]["result"]["simulated"])
        # Approving again does not re-execute.
        self._run_cli(["approvals", "approve", item.id])
        self.assertEqual(len(self._queue().get(item.id).execution), 1)

    def test_rejected_draft_never_executes(self):
        self._run_cli(
            [
                "run",
                "--agent",
                "salesforce-updater",
                "--account",
                "Acme Corp",
                "--stub",
                "--change",
                "stage 006STUB000000001 Closed Won",
            ]
        )
        item = self._queue().list_pending()[0]
        code, _out = self._run_cli(["approvals", "reject", item.id])
        self.assertEqual(code, 0)
        stored = self._queue().get(item.id)
        self.assertEqual(stored.status, "rejected")
        self.assertEqual(stored.execution, [])

    def test_show_prints_the_write_plan(self):
        self._run_cli(
            [
                "run",
                "--agent",
                "salesforce-updater",
                "--account",
                "Acme Corp",
                "--stub",
                "--change",
                "stage 006STUB000000001 Closed Won",
            ]
        )
        item = self._queue().list_pending()[0]
        code, out = self._run_cli(["approvals", "show", item.id])
        self.assertEqual(code, 0)
        self.assertIn("Write plan (1 change(s))", out)
        self.assertIn("stage -> Closed Won", out)

    def test_bad_change_spec_fails_fast(self):
        code, _out = self._run_cli(
            [
                "run",
                "--agent",
                "salesforce-updater",
                "--account",
                "Acme Corp",
                "--stub",
                "--change",
                "frobnicate the thing",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(self._queue().list_pending(), [])


class TestDashboardApproveExecutes(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from harness.dashboard import create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "approvals.json")
        self.queue = ApprovalQueue(self.path)
        ops, _preview = build_write_plan(
            [parse_change_spec("stage 006STUB000000001 Closed Won")],
            _gathered(),
        )
        self.item = self.queue.submit(
            agent="salesforce-updater",
            job={"account": "Acme Corp"},
            draft="Move the renewal to Closed Won.",
            writes=ops,
        )
        config = Config.from_env()
        config.stub_mode = True
        config.approvals_file = self.path
        self.client = TestClient(
            create_app(config=config, queue=self.queue)
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_dashboard_approve_applies_the_plan(self):
        res = self.client.post(
            "/api/approvals/%s/approve" % self.item.id
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(len(body["execution"]), 1)
        self.assertTrue(body["execution"][0]["success"])
        self.assertTrue(body["execution"][0]["result"]["simulated"])

    def test_dashboard_reject_never_applies(self):
        res = self.client.post(
            "/api/approvals/%s/reject" % self.item.id
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "rejected")
        self.assertEqual(body["execution"], [])


if __name__ == "__main__":
    unittest.main()
