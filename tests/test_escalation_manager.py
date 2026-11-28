"""Tests for the escalation-manager agent (v1.7.0).

The escalation-manager drafts an escalation plan for high-severity
signals: the situation with evidence, the people to loop in, the ask,
the exec update and the customer comms as drafts, and the fallback.
One record per signal per account lives in the escalation store
(open, escalated, resolved): a second run for the same signal drafts
a status note, not a second plan; approving a plan moves its records
to escalated; a resolved record stays resolved even when the signal
fires again. Severity, the ask, and the fallback are harness-computed,
never model prose, so the plan structure is tested directly as well
as end to end with stub tools and the stub LLM.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed.
"""

import json
import os
import tempfile
import unittest

from connectors.gmail.tools import gmail_tools
from connectors.gong.tools import gong_tools
from connectors.linear.tools import linear_tools
from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from connectors.zendesk.tools import zendesk_tools
from connectors.zoom.tools import zoom_tools
from harness import (
    AGENTS,
    ApprovalQueue,
    EscalationStore,
    TokenBudget,
    ToolRegistry,
)
from harness.agents import (
    _ESCALATION_SOURCE_AGENTS,
    _escalation_ask,
    _escalation_fallback,
    _people_to_loop_in,
    _risk_signals,
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
    for tool in zendesk_tools(config):
        registry.register(tool)
    for tool in linear_tools(config):
        registry.register(tool)
    return registry


def _empty_registry() -> ToolRegistry:
    """Registry whose tools return empty results: no account signals at
    all. The agent must draft no escalation, not invent one."""
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
        "salesforce_list_opportunities": lambda **kw: {"opportunities": []},
        "salesforce_list_cases": lambda **kw: {"cases": []},
        "gmail_search_messages": lambda **kw: {"matches": []},
        "gong_search_transcripts": lambda **kw: {"matches": []},
        "zoom_search_transcripts": lambda **kw: {"matches": []},
        "zendesk_list_tickets": lambda **kw: {"tickets": []},
        "linear_list_issues": lambda **kw: {"issues": []},
        "slack_search_messages": lambda **kw: {"matches": []},
    }
    for name, handler in handlers.items():
        registry.register(
            Tool(name=name, description="Empty stub for %s." % name, handler=handler)
        )
    return registry


def _temp_path() -> str:
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


class TestEscalationManagerRegistered(unittest.TestCase):
    def test_sixteen_agents_registered(self):
        self.assertEqual(len(AGENTS), 16)
        agent = AGENTS["escalation-manager"]
        self.assertTrue(agent.implemented)
        self.assertEqual(agent.display_name, "Escalation Manager")


class TestEscalationManagerRuns(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)
        self.esc_path = _temp_path()
        self.queue_path = _temp_path()

    def tearDown(self):
        for path in (self.esc_path, self.queue_path):
            if os.path.exists(path):
                os.unlink(path)

    def _job(self, **extra):
        job = {"account": "Acme Corp", "escalations_file": self.esc_path}
        job.update(extra)
        return job

    def test_plan_run_drafts_the_plan(self):
        agent = AGENTS["escalation-manager"]
        job = self._job()
        draft = agent.run(job, self.registry, self.llm, self.budget)
        self.assertEqual(draft.agent, "escalation-manager")
        # The stub data fires one high signal: the SSO failure across
        # three systems. The plan names it, with the harness-written
        # structure.
        self.assertIn("Escalation plan", draft.text)
        self.assertIn("Overall severity: high", draft.text)
        self.assertIn("SSO login fails for new hires", draft.text)
        self.assertIn("People to loop in", draft.text)
        self.assertIn("Jane CSM (account owner, Salesforce)", draft.text)
        self.assertIn("The ask", draft.text)
        self.assertIn("Exec update", draft.text)
        self.assertIn("Customer comms", draft.text)
        self.assertIn("If this does not land", draft.text)
        self.assertTrue(draft.links)
        self.assertTrue(draft.proof_passed)
        # One record per high signal, all open.
        store = EscalationStore(self.esc_path)
        records = store.for_account("Acme Corp")
        self.assertEqual(len(records), 1)
        self.assertTrue(all(r.status == "open" for r in records))
        keys = {r.signal_key for r in records}
        self.assertEqual(
            keys, {"support-delivery:sso login fails for new hires"}
        )
        self.assertEqual(len(job["escalation_records"]), 1)

    def test_second_run_suppresses_the_duplicate(self):
        agent = AGENTS["escalation-manager"]
        job = self._job(approvals_file=self.queue_path)
        draft = agent.run(job, self.registry, self.llm, self.budget)
        queue = ApprovalQueue(self.queue_path)
        queue.submit(
            agent=draft.agent,
            job=job,
            draft=draft.text,
            sources=draft.sources,
            links=draft.links,
            proof=draft.proof,
        )
        # Second run, same account, same signals, first plan still
        # pending: a status note, not a second plan.
        draft2 = agent.run(
            self._job(approvals_file=self.queue_path),
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("Escalation status for Acme Corp", draft2.text)
        self.assertIn("Overall severity: high", draft2.text)
        self.assertNotIn("The ask", draft2.text)
        self.assertNotIn("Escalation plan", draft2.text)
        self.assertTrue(draft2.proof_passed)
        store = EscalationStore(self.esc_path)
        self.assertEqual(len(store.for_account("Acme Corp")), 1)

    def test_rejected_plan_can_be_redrafted(self):
        agent = AGENTS["escalation-manager"]
        job = self._job(approvals_file=self.queue_path)
        draft = agent.run(job, self.registry, self.llm, self.budget)
        queue = ApprovalQueue(self.queue_path)
        item = queue.submit(
            agent=draft.agent,
            job=job,
            draft=draft.text,
            sources=draft.sources,
            links=draft.links,
            proof=draft.proof,
        )
        queue.reject(item.id, note="Not yet, checking with the sponsor first.")
        # The human rejected the plan, not the risk: the next run may
        # draft again, and it must not open duplicate records.
        draft2 = agent.run(
            self._job(approvals_file=self.queue_path),
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("Escalation plan", draft2.text)
        self.assertIn("The ask", draft2.text)
        store = EscalationStore(self.esc_path)
        self.assertEqual(len(store.for_account("Acme Corp")), 1)

    def test_approve_moves_records_to_escalated(self):
        agent = AGENTS["escalation-manager"]
        job = self._job(approvals_file=self.queue_path)
        draft = agent.run(job, self.registry, self.llm, self.budget)
        store = EscalationStore(self.esc_path)
        queue = ApprovalQueue(
            self.queue_path, escalations=EscalationStore(self.esc_path)
        )
        item = queue.submit(
            agent=draft.agent,
            job=job,
            draft=draft.text,
            sources=draft.sources,
            links=draft.links,
            proof=draft.proof,
        )
        # execute_job links the draft id to the records after submit.
        for record_id in job["escalation_records"]:
            store.set_draft(record_id, item.id)
        queue.approve(item.id)
        records = EscalationStore(self.esc_path).for_account("Acme Corp")
        self.assertTrue(all(r.status == "escalated" for r in records))

    def test_resolved_record_stays_resolved(self):
        agent = AGENTS["escalation-manager"]
        draft = agent.run(
            self._job(), self.registry, self.llm, self.budget
        )
        self.assertIn("Escalation plan", draft.text)
        store = EscalationStore(self.esc_path)
        for record in store.for_account("Acme Corp"):
            self.assertTrue(store.resolve(record.id, note="Done."))
        # The signal still fires in the stub data, but its record
        # is resolved: no new plan is drafted.
        draft2 = agent.run(
            self._job(), self.registry, self.llm, self.budget
        )
        self.assertNotIn("The ask", draft2.text)
        self.assertIn("resolved", draft2.text)
        self.assertEqual(len(store.for_account("Acme Corp")), 1)

    def test_closure_draft_resolves_through_approval(self):
        agent = AGENTS["escalation-manager"]
        job = self._job(approvals_file=self.queue_path)
        draft = agent.run(job, self.registry, self.llm, self.budget)
        store = EscalationStore(self.esc_path)
        queue = ApprovalQueue(
            self.queue_path, escalations=EscalationStore(self.esc_path)
        )
        item = queue.submit(
            agent=draft.agent,
            job=job,
            draft=draft.text,
            sources=draft.sources,
            links=draft.links,
            proof=draft.proof,
        )
        for record_id in job["escalation_records"]:
            store.set_draft(record_id, item.id)
        queue.approve(item.id)
        # The signals are gone now (empty registry, direct gather):
        # the agent drafts a closure confirmation, and approving it
        # resolves the records. The agent never resolves on its own.
        job2 = self._job()
        draft2 = agent.run(job2, _empty_registry(), self.llm, self.budget)
        self.assertIn("Escalation closure", draft2.text)
        self.assertIn("mark them resolved", draft2.text)
        self.assertEqual(len(job2["escalation_resolve"]), 1)
        self.assertTrue(draft2.proof_passed)
        item2 = queue.submit(
            agent=draft2.agent,
            job=job2,
            draft=draft2.text,
            sources=draft2.sources,
            links=draft2.links,
            proof=draft2.proof,
        )
        queue.approve(item2.id)
        records = EscalationStore(self.esc_path).for_account("Acme Corp")
        self.assertTrue(all(r.status == "resolved" for r in records))
        self.assertTrue(all(r.resolved_at for r in records))

    def test_no_high_severity_is_no_escalation(self):
        agent = AGENTS["escalation-manager"]
        draft = agent.run(
            self._job(), _empty_registry(), self.llm, self.budget
        )
        self.assertEqual(draft.agent, "escalation-manager")
        self.assertIn("No escalation needed", draft.text)
        self.assertIn("Overall severity: none", draft.text)
        self.assertNotIn("Escalation plan", draft.text)
        self.assertTrue(draft.proof_passed)
        store = EscalationStore(self.esc_path)
        self.assertEqual(store.for_account("Empty Corp"), [])
        self.assertEqual(store.for_account("Acme Corp"), [])

    def test_composes_from_risk_watcher_draft(self):
        _seed(
            self.queue_path,
            [
                (
                    "risk-watcher",
                    "Acme Corp",
                    "MARKER risk brief for Acme Corp.",
                    [
                        {
                            "label": "Account: Acme Corp",
                            "url": "https://stub.salesforce.invalid/lightning/r/Account/001STUB/view",
                        }
                    ],
                ),
            ],
        )
        agent = AGENTS["escalation-manager"]
        draft = agent.run(
            self._job(approvals_file=self.queue_path),
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "escalation-manager")
        self.assertIn(
            "Composed from prior drafts: risk-watcher.", draft.text
        )
        urls = [link.get("url") for link in draft.links]
        self.assertIn(
            "https://stub.salesforce.invalid/lightning/r/Account/001STUB/view",
            urls,
        )
        # Fresh signals still fire in the composed path: the SSO
        # failure is gathered directly, not from the old draft.
        self.assertIn("SSO login fails for new hires", draft.text)
        self.assertIn("Escalation plan", draft.text)
        self.assertTrue(draft.proof_passed)

    def test_source_agents_are_the_expected_three(self):
        self.assertEqual(
            _ESCALATION_SOURCE_AGENTS,
            ("risk-watcher", "renewal-desk", "voice-of-customer"),
        )


class TestEscalationStore(unittest.TestCase):
    def setUp(self):
        self.path = _temp_path()

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_create_is_idempotent_per_signal(self):
        store = EscalationStore(self.path)
        first = store.create(
            "Acme Corp",
            "champion-departure:priya nair",
            "champion-departure",
            "high",
            "Champion departure: Priya Nair left.",
        )
        second = store.create(
            "Acme Corp",
            "champion-departure:priya nair",
            "champion-departure",
            "high",
            "Champion departure: Priya Nair left.",
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(store.for_account("Acme Corp")), 1)
        # Same key, different account: a separate record.
        other = store.create(
            "Globex Inc",
            "champion-departure:priya nair",
            "champion-departure",
            "high",
            "Champion departure: Priya Nair left.",
        )
        self.assertNotEqual(first.id, other.id)

    def test_mark_escalated_only_moves_open(self):
        store = EscalationStore(self.path)
        record = store.create(
            "Acme Corp", "k1", "champion-departure", "high", "s"
        )
        self.assertTrue(store.mark_escalated(record.id))
        self.assertEqual(store.get(record.id).status, "escalated")
        # Already escalated: no second move.
        self.assertFalse(store.mark_escalated(record.id))
        self.assertFalse(store.mark_escalated("esc-nope"))

    def test_resolve_sets_resolved_at_and_is_idempotent(self):
        store = EscalationStore(self.path)
        record = store.create(
            "Acme Corp", "k1", "champion-departure", "high", "s"
        )
        self.assertTrue(store.resolve(record.id, note="Fixed."))
        resolved = store.get(record.id)
        self.assertEqual(resolved.status, "resolved")
        self.assertIsNotNone(resolved.resolved_at)
        self.assertEqual(resolved.note, "Fixed.")
        self.assertFalse(store.resolve(record.id))
        self.assertFalse(store.resolve("esc-nope"))

    def test_corrupt_file_reads_as_empty(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        store = EscalationStore(self.path)
        self.assertEqual(store.list_all(), [])

    def test_in_memory_store_never_touches_disk(self):
        store = EscalationStore(None)
        record = store.create(
            "Acme Corp", "k1", "champion-departure", "high", "s"
        )
        self.assertEqual(store.get(record.id).status, "open")
        self.assertFalse(os.path.exists(self.path))


class TestSignalKeys(unittest.TestCase):
    def test_high_signals_carry_stable_keys(self):
        results = {
            "salesforce_list_cases": {
                "cases": [
                    {
                        "number": "00001234",
                        "subject": "SSO login fails for new hires",
                        "status": "Working",
                        "priority": "High",
                        "age_days": 6,
                    }
                ]
            },
            "salesforce_list_opportunities": {
                "opportunities": [
                    {
                        "name": "Acme Corp - Renewal FY27",
                        "stage": "Negotiation",
                        "amount": 120000,
                        "close_date": "2026-10-01",
                        "type": "Renewal",
                    }
                ]
            },
        }
        signals = _risk_signals(results, "Acme Corp")
        keys = {s["kind"]: s.get("key") for s in signals}
        self.assertEqual(
            keys.get("support-delivery"),
            "support-delivery:sso login fails for new hires",
        )
        self.assertEqual(
            keys.get("renewal-stalled"),
            "renewal-stalled:acme corp - renewal fy27",
        )


class TestEscalationPlanBlock(unittest.TestCase):
    def test_ask_and_fallback_differ_per_kind(self):
        kinds = ("support-delivery", "renewal-stalled")
        asks = {_escalation_ask({"kind": k}) for k in kinds}
        fallbacks = {_escalation_fallback({"kind": k}) for k in kinds}
        self.assertEqual(len(asks), 2)
        self.assertEqual(len(fallbacks), 2)
        self.assertIn(
            "named owner", _escalation_ask({"kind": "support-delivery"})
        )
        self.assertIn(
            "executive sponsor",
            _escalation_fallback({"kind": "support-delivery"}),
        )

    def test_people_to_loop_in_names_owner(self):
        results = {
            "salesforce_get_account": {"owner": "Jane CSM"},
        }
        people = _people_to_loop_in(results)
        self.assertEqual(people, ["Jane CSM (account owner, Salesforce)"])


class TestEscalationManagerRouting(unittest.TestCase):
    def test_keyword_escalate_routes(self):
        router = Router()
        result = router.route(
            {"text": "escalate the Acme account", "account": "Acme Corp"}
        )
        self.assertEqual(result.agent, "escalation-manager")

    def test_explicit_agent_routes(self):
        router = Router()
        result = router.route({"text": "anything", "agent": "escalation-manager"})
        self.assertEqual(result.agent, "escalation-manager")
        self.assertIsNone(result.error)

    def test_risk_text_still_routes_to_risk_watcher(self):
        # "check the risk" must not be stolen by the escalation
        # keywords: risk-watcher owns risk language.
        router = Router()
        result = router.route(
            {"text": "check the risk on this account", "account": "Acme Corp"}
        )
        self.assertEqual(result.agent, "risk-watcher")

    def test_renewal_text_still_routes_to_renewal_desk(self):
        router = Router()
        result = router.route({"text": "draft a renewal plan, churn risk"})
        self.assertEqual(result.agent, "renewal-desk")


if __name__ == "__main__":
    unittest.main()
