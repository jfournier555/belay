"""Tests for the trust-repair agent (v0.19.0).

The trust-repair agent recovers trust after an outage, a bad
escalation, or a missed promise: the trust-hit summary with evidence,
the impact on relationships and renewals, and a recovery plan with
owner, dates, and the proof the customer will see. The hit, the
impact, and the plan are harness-computed, so detection, impact, and
the plan are tested directly on synthetic inputs as well as end to
end with stub tools and the stub LLM.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed.
"""

import json
import os
import tempfile
import unittest
from datetime import date

from connectors.csv.tools import csv_crm_tools
from connectors.gmail.tools import gmail_tools
from connectors.gong.tools import gong_tools
from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from connectors.zendesk.tools import zendesk_tools
from connectors.zoom.tools import zoom_tools
from harness import AGENTS, ApprovalQueue, TokenBudget, ToolRegistry
from harness.agents import (
    _classify_incident_text,
    _format_commitment_line,
    _missed_prior_commitments,
    _recovery_steps,
    _trust_hit,
    _trust_impact,
    _trust_repair_block,
)
from harness.config import Config
from harness.escalations import EscalationRecord
from harness.llm import StubLLM
from harness.router import Router

TODAY = date(2026, 11, 20)  # a Friday; pinned so due dates are stable


def _stub_registry() -> ToolRegistry:
    """Registry wired to the stub connector tools (no creds needed)."""
    config = Config.from_env()
    config.stub_mode = True
    registry = ToolRegistry()
    for tool in salesforce_tools(config):
        registry.register(tool)
    for tool in gong_tools(config):
        registry.register(tool)
    for tool in zoom_tools(config):
        registry.register(tool)
    for tool in gmail_tools(config):
        registry.register(tool)
    for tool in zendesk_tools(config):
        registry.register(tool)
    for tool in slack_tools(config):
        registry.register(tool)
    return registry


def _csv_registry(csv_path: str) -> ToolRegistry:
    """Registry wired to a CSV accounts file for the CRM tools, plus
    stub tools for calls, messages, and tickets."""
    config = Config.from_env()
    config.stub_mode = True
    config.csv_accounts_file = csv_path
    registry = ToolRegistry()
    for tool in csv_crm_tools(config):
        registry.register(tool)
    for tool in gong_tools(config):
        registry.register(tool)
    for tool in zoom_tools(config):
        registry.register(tool)
    for tool in gmail_tools(config):
        registry.register(tool)
    for tool in zendesk_tools(config):
        registry.register(tool)
    for tool in slack_tools(config):
        registry.register(tool)
    return registry


def _clean_csv_path() -> str:
    """A one-account CSV with no incidents: green health, a far-out
    renewal, no open cases, no contacts named as champion."""
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".csv", mode="w", encoding="utf-8"
    )
    tmp.write(
        "name,domain,arr,currency,renewal_date,health,owner,industry,"
        "employees,contacts,open_cases,last_touch,notes,opp_name,"
        "opp_stage,opp_amount,opp_close_date,opp_type,cases\n"
        "Quiet Co,quiet.example,40000,USD,2027-06-01,green,Jane CSM,"
        "Software,50,,0,2026-11-18,\"Steady account, no open issues.\","
        "Quiet Co - Renewal FY27,Negotiation,40000,2027-06-01,Renewal,\n"
    )
    tmp.close()
    return tmp.name


def _csv_only_registry(csv_path: str) -> ToolRegistry:
    """Registry with only the CSV accounts connector: no ticket, case,
    message, or transcript tools at all, so a clean account gathers no
    incident from any other system. Used for the no-incident gap path."""
    config = Config.from_env()
    config.stub_mode = True
    config.csv_accounts_file = csv_path
    registry = ToolRegistry()
    for tool in csv_crm_tools(config):
        registry.register(tool)
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


class TestTrustRepairRegistered(unittest.TestCase):
    def test_twenty_agents_registered(self):
        self.assertEqual(len(AGENTS), 22)
        implemented = [n for n, a in AGENTS.items() if a.implemented]
        self.assertEqual(
            sorted(implemented),
            [
                "adoption-gaps",
                "advocate-finder",
                "briefer",
                "call-recap-and-commitments",
                "case-triage",
                "champion-change",
                "churn-post-mortem",
                "competitive-defense",
                "ebr-prep",
                "escalation-manager",
                "expansion-spotter",
                "handoff",
                "onboarding-guide",
                "qbr-builder",
                "renewal-desk",
                "risk-watcher",
                "salesforce-updater",
                "scout",
                "stakeholder-mapper",
                "trust-repair",
                "value-finder",
                "voice-of-customer",
            ],
        )

    def test_router_routes_explicitly_and_by_keyword(self):
        router = Router(agents=AGENTS)
        result = router.route({"text": "anything", "agent": "trust-repair"})
        self.assertEqual(result.agent, "trust-repair")
        self.assertIsNone(result.error)
        result = router.route(
            {"text": "draft a trust recovery plan for Acme Corp"}
        )
        self.assertEqual(result.agent, "trust-repair")
        result = router.route(
            {"text": "the service outage hurt Acme Corp, help us recover"}
        )
        self.assertEqual(result.agent, "trust-repair")


class TestClassifyIncidentText(unittest.TestCase):
    def test_outage_words(self):
        self.assertEqual(_classify_incident_text("API outage on Tuesday"), "outage")
        self.assertEqual(
            _classify_incident_text("the service was down for hours"), "outage"
        )

    def test_escalation_words(self):
        self.assertEqual(
            _classify_incident_text("a bad escalation with the support team"),
            "bad-escalation",
        )

    def test_missed_promise_words(self):
        self.assertEqual(
            _classify_incident_text("we missed the promise to ship by Friday"),
            "missed-promise",
        )

    def test_unclassified_is_unknown_not_a_guess(self):
        self.assertEqual(_classify_incident_text("something went wrong"), "unknown")


class TestTrustHit(unittest.TestCase):
    def test_job_incident_with_explicit_type(self):
        hit = _trust_hit(
            {"incident": "API outage on Tuesday", "incident_type": "outage"},
            [],
            [],
            [],
        )
        self.assertEqual(hit["kind"], "outage")
        self.assertEqual(hit["summary"], "API outage on Tuesday")
        self.assertEqual(hit["severity"], "high")
        self.assertIn("Named in the job", hit["evidence"])

    def test_job_incident_classified_from_wording(self):
        hit = _trust_hit({"incident": "the service was down for hours"}, [], [], [])
        self.assertEqual(hit["kind"], "outage")

    def test_open_escalation_record_is_the_hit(self):
        record = EscalationRecord(
            id="esc-1",
            account="Acme Corp",
            signal_key="support-delivery:sso",
            signal_kind="support-delivery",
            severity="high",
            summary="SSO login fails for new hires",
            status="open",
        )
        hit = _trust_hit({}, [], [record], [])
        self.assertEqual(hit["kind"], "bad-escalation")
        self.assertIn("esc-1", hit["evidence"])
        self.assertEqual(hit["severity"], "high")

    def test_resolved_record_is_not_a_hit(self):
        record = EscalationRecord(
            id="esc-1",
            account="Acme Corp",
            signal_key="support-delivery:sso",
            signal_kind="support-delivery",
            severity="high",
            summary="SSO login fails for new hires",
            status="resolved",
        )
        hit = _trust_hit({}, [], [record], [])
        self.assertEqual(hit["kind"], "unknown")

    def test_high_support_signal_is_a_support_failure(self):
        hit = _trust_hit(
            {},
            [
                {
                    "severity": "high",
                    "kind": "support-delivery",
                    "text": "Support/delivery risk: 'SSO login fails' is open.",
                }
            ],
            [],
            [],
        )
        self.assertEqual(hit["kind"], "support-failure")
        self.assertEqual(hit["severity"], "high")

    def test_stalled_renewal_alone_is_not_a_trust_hit(self):
        hit = _trust_hit(
            {},
            [
                {
                    "severity": "high",
                    "kind": "renewal-stalled",
                    "text": "Renewal stalled: Acme Corp - Renewal FY27 passed its close date.",
                }
            ],
            [],
            [],
        )
        self.assertEqual(hit["kind"], "unknown")

    def test_missed_commitment_is_a_missed_promise(self):
        missed = [
            {
                "text": "Ship the SSO fix",
                "owner": "Sam Reyes",
                "due": date(2026, 11, 1),
                "source": "call material",
                "status": "new",
            }
        ]
        hit = _trust_hit({}, [], [], missed)
        self.assertEqual(hit["kind"], "missed-promise")
        self.assertEqual(hit["severity"], "medium")
        self.assertIn("Ship the SSO fix", hit["evidence"])

    def test_escalation_language_is_a_bad_escalation(self):
        hit = _trust_hit(
            {},
            [
                {
                    "severity": "medium",
                    "kind": "escalation-language",
                    "text": "Escalation language in Gmail ('unacceptable'): 'this is unacceptable'",
                }
            ],
            [],
            [],
        )
        self.assertEqual(hit["kind"], "bad-escalation")

    def test_nothing_anywhere_is_unknown(self):
        hit = _trust_hit({}, [], [], [])
        self.assertEqual(hit["kind"], "unknown")
        self.assertEqual(hit["severity"], "none")


class TestMissedPriorCommitments(unittest.TestCase):
    def _job_with_recap(self, line: str) -> dict:
        path = _temp_queue_path()
        _seed(
            path,
            [("call-recap-and-commitments", "Acme Corp", line, [])],
        )
        return {"account": "Acme Corp", "approvals_file": path}

    def test_past_due_commitment_is_missed(self):
        line = _format_commitment_line(
            {
                "text": "Ship the SSO fix",
                "owner": "Sam Reyes",
                "due": date(2026, 11, 1),
                "status": "new",
                "source": "call material",
            },
            1,
        )
        missed = _missed_prior_commitments(self._job_with_recap(line), "Acme Corp", TODAY)
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0]["text"], "Ship the SSO fix")

    def test_future_commitment_is_not_missed(self):
        line = _format_commitment_line(
            {
                "text": "Ship the SSO fix",
                "owner": "Sam Reyes",
                "due": date(2026, 12, 1),
                "status": "new",
                "source": "call material",
            },
            1,
        )
        missed = _missed_prior_commitments(self._job_with_recap(line), "Acme Corp", TODAY)
        self.assertEqual(missed, [])

    def test_commitment_without_a_date_cannot_be_missed(self):
        line = _format_commitment_line(
            {
                "text": "Ship the SSO fix",
                "owner": "Sam Reyes",
                "due": None,
                "status": "new",
                "source": "call material",
            },
            1,
        )
        missed = _missed_prior_commitments(self._job_with_recap(line), "Acme Corp", TODAY)
        self.assertEqual(missed, [])

    def test_closed_commitment_is_not_missed(self):
        line = _format_commitment_line(
            {
                "text": "Ship the SSO fix",
                "owner": "Sam Reyes",
                "due": date(2026, 11, 1),
                "status": "closed since last recap",
                "source": "call material",
            },
            1,
        )
        missed = _missed_prior_commitments(self._job_with_recap(line), "Acme Corp", TODAY)
        self.assertEqual(missed, [])


class TestTrustImpact(unittest.TestCase):
    def test_renewal_at_risk_and_champion_exposed(self):
        results = {
            "salesforce_get_account": {
                "owner": "Jane CSM",
                "contacts": [
                    {"name": "Dana Cole", "role": "champion"},
                    {"name": "Sam Reyes", "role": "user"},
                ],
            }
        }
        signals = [
            {
                "severity": "high",
                "kind": "renewal-stalled",
                "text": "Renewal stalled: Acme Corp - Renewal FY27 passed its close date.",
            }
        ]
        impact = _trust_impact(results, "Acme Corp", signals, [])
        self.assertTrue(any("Renewal at risk" in line for line in impact))
        self.assertTrue(any("Dana Cole" in line for line in impact))
        self.assertTrue(any("Jane CSM" in line for line in impact))

    def test_no_champion_is_named_as_a_gap(self):
        results = {"salesforce_get_account": {"owner": "Jane CSM", "contacts": []}}
        impact = _trust_impact(results, "Acme Corp", [], [])
        self.assertTrue(any("No champion named" in line for line in impact))

    def test_no_owner_is_named(self):
        results = {"salesforce_get_account": {"contacts": []}}
        impact = _trust_impact(results, "Acme Corp", [], [])
        self.assertTrue(any("No account owner named" in line for line in impact))

    def test_open_escalations_counted(self):
        record = EscalationRecord(
            id="esc-1",
            account="Acme Corp",
            signal_key="support-delivery:sso",
            signal_kind="support-delivery",
            severity="high",
            summary="SSO login fails for new hires",
            status="open",
        )
        impact = _trust_impact(
            {"salesforce_get_account": {"owner": "Jane CSM"}}, "Acme Corp", [], [record]
        )
        self.assertTrue(any("1 open escalation" in line for line in impact))


class TestRecoverySteps(unittest.TestCase):
    def test_fix_step_has_no_invented_date(self):
        hit = {
            "kind": "support-failure",
            "summary": "SSO login fails for new hires",
            "evidence": "Risk signal",
            "severity": "high",
        }
        steps = _recovery_steps(
            hit, {"salesforce_get_account": {"owner": "Jane CSM"}}, []
        )
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[0]["title"], "Acknowledge and own it")
        self.assertEqual(steps[0]["owner"], "Jane CSM")
        self.assertEqual(steps[1]["title"], "Fix it")
        self.assertIsNone(steps[1]["date"])
        self.assertEqual(steps[2]["title"], "Follow-up cadence")
        self.assertEqual(steps[3]["title"], "Proof the customer will see")

    def test_block_renders_all_sections(self):
        hit = {
            "kind": "outage",
            "summary": "API outage on Tuesday",
            "evidence": "Named in the job",
            "severity": "high",
        }
        steps = _recovery_steps(
            hit, {"salesforce_get_account": {"owner": "Jane CSM"}}, []
        )
        block = _trust_repair_block(
            hit, ["Renewal at risk: stalled."], steps, [], []
        )
        for section in (
            "Trust hit:",
            "Impact:",
            "Recovery plan:",
            "Missed commitments:",
            "Gaps:",
        ):
            self.assertIn(section, block)
        self.assertIn("not stated in the records", block)
        self.assertIn("API outage on Tuesday", block)

    def test_block_with_no_hit_names_the_gap(self):
        hit = {"kind": "unknown", "summary": "", "evidence": "", "severity": "none"}
        block = _trust_repair_block(hit, [], [], [], ["No trust hit named."])
        self.assertIn("No trust hit named", block)
        self.assertIn("Severity: none.", block)


class TestTrustRepairEndToEnd(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_stub_run_finds_the_support_failure_and_proves(self):
        agent = AGENTS["trust-repair"]
        draft = agent.run(
            {"account": "Acme Corp"}, self.registry, self.llm, self.budget
        )
        self.assertEqual(draft.agent, "trust-repair")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("salesforce_list_cases", draft.sources)
        self.assertIn("Acme Corp", draft.text)
        self.assertIn("Trust hit:", draft.text)
        self.assertIn("support-failure", draft.text)
        self.assertIn("Impact:", draft.text)
        self.assertIn("Recovery plan:", draft.text)
        self.assertIn("Follow-up cadence", draft.text)
        self.assertIn("Proof the customer will see", draft.text)
        self.assertTrue(draft.links)
        self.assertTrue(draft.proof_passed)

    def test_job_incident_overrides_the_gather(self):
        agent = AGENTS["trust-repair"]
        draft = agent.run(
            {
                "account": "Acme Corp",
                "incident": "API outage on Tuesday",
                "incident_type": "outage",
            },
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("outage", draft.text)
        self.assertIn("API outage on Tuesday", draft.text)

    def test_csv_demo_run_does_not_crash(self):
        registry = _csv_registry(
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "connectors",
                "csv",
                "sample_accounts.csv",
            )
        )
        agent = AGENTS["trust-repair"]
        draft = agent.run(
            {"account": "Acme Corp"}, registry, self.llm, self.budget
        )
        self.assertEqual(draft.agent, "trust-repair")
        self.assertIn("Trust hit:", draft.text)
        self.assertIn("Recovery plan:", draft.text)

    def test_clean_account_names_the_gap(self):
        csv_path = _clean_csv_path()
        try:
            # CSV tools only: the Zendesk stub returns Acme's tickets for
            # any account, so a registry with the ticket tools would
            # invent an incident for this clean account.
            registry = _csv_only_registry(csv_path)
            agent = AGENTS["trust-repair"]
            draft = agent.run(
                {"account": "Quiet Co"}, registry, self.llm, self.budget
            )
            self.assertEqual(draft.agent, "trust-repair")
            self.assertIn("No trust hit named", draft.text)
            self.assertIn("Recovery plan:", draft.text)
        finally:
            os.unlink(csv_path)

    def test_composed_from_prior_drafts(self):
        path = _temp_queue_path()
        _seed(
            path,
            [
                (
                    "escalation-manager",
                    "Acme Corp",
                    "Escalation plan (harness-computed): SSO login fails.",
                    [],
                ),
                (
                    "risk-watcher",
                    "Acme Corp",
                    "Risk signals (harness-computed): high support failure.",
                    [],
                ),
            ],
        )
        agent = AGENTS["trust-repair"]
        draft = agent.run(
            {"account": "Acme Corp", "approvals_file": path},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("Composed from prior drafts:", draft.text)
        self.assertIn("escalation-manager", draft.text)
        self.assertIn("risk-watcher", draft.text)


if __name__ == "__main__":
    unittest.main()
