"""Tests for the churn-post-mortem agent.

The churn-post-mortem agent documents why a churned account left and
hands the record to sales for a future re-approach, not a CS win-back.
The churn reason, the re-approach timing, and the re-approach contact
are harness-computed, so the reason kinds, the timing windows, the
contact, and the block are tested directly on synthetic inputs as
well as end to end with stub tools and the stub LLM, on the CSV sample
demo, and on a churned account in a custom CSV.

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
    _churn_post_mortem_block,
    _churn_reasons,
    _closed_lost_renewal_opp,
    _extract_new_company,
    _format_commitment_line,
    _reapproach_contact,
    _reapproach_timing,
    _reapproach_triggers,
    _what_was_lost,
)
from harness.config import Config
from harness.escalations import EscalationRecord
from harness.llm import StubLLM
from harness.router import Router

TODAY = date(2026, 11, 20)  # a Friday; pinned so due dates are stable

# A closed-lost renewal opp: the anchor that establishes the churn
# itself, so contributing signals can be listed as reasons.
_ANCHOR_OPPS = {
    "opportunities": [
        {
            "name": "Gone Co - Renewal FY27",
            "type": "Renewal",
            "stage": "Closed Lost",
            "amount": 60000,
            "close_date": "2026-11-15",
        }
    ]
}


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


def _sample_csv_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "connectors",
        "csv",
        "sample_accounts.csv",
    )


def _churned_csv_path() -> str:
    """A one-account CSV for a churned account: the renewal closed
    lost, the champion left, and the account owner is on record."""
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".csv", mode="w", encoding="utf-8"
    )
    tmp.write(
        "name,domain,arr,currency,renewal_date,health,owner,industry,"
        "employees,contacts,open_cases,last_touch,notes,opp_name,"
        "opp_stage,opp_amount,opp_close_date,opp_type,cases\n"
        "Gone Co,gone.example,60000,USD,2026-11-15,red,Jane CSM,"
        "Software,120,Dana Cole <dana@gone.example>,0,2026-11-10,"
        "\"Champion left in October; renewal closed lost.\","
        "Gone Co - Renewal FY27,Closed Lost,60000,2026-11-15,Renewal,\n"
    )
    tmp.close()
    return tmp.name


def _clean_csv_path() -> str:
    """A one-account CSV with no churn evidence: green health, a
    far-out renewal, no open cases, no contacts named as champion."""
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


class TestChurnPostMortemRegistered(unittest.TestCase):
    def test_twenty_two_agents_registered(self):
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
        result = router.route({"text": "anything", "agent": "churn-post-mortem"})
        self.assertEqual(result.agent, "churn-post-mortem")
        self.assertIsNone(result.error)
        result = router.route(
            {"text": "write a churn post-mortem for Acme Corp"}
        )
        self.assertEqual(result.agent, "churn-post-mortem")
        result = router.route(
            {"text": "churn handoff to SDR for Acme Corp"}
        )
        self.assertEqual(result.agent, "churn-post-mortem")
        result = router.route(
            {"text": "document why Acme Corp churned"}
        )
        self.assertEqual(result.agent, "churn-post-mortem")
        result = router.route({"text": "sdr handoff for Acme Corp"})
        self.assertEqual(result.agent, "churn-post-mortem")

    def test_no_collision_with_renewal_risk_or_handoff(self):
        # Renewal-desk owns bare "churn" and "renewal"; risk-watcher
        # owns "risk"; handoff owns bare "handoff". None of those
        # phrases may be stolen by the post-mortem keywords.
        router = Router(agents=AGENTS)
        result = router.route(
            {"text": "draft a renewal plan, churn risk"}
        )
        self.assertEqual(result.agent, "renewal-desk")
        # Bare "churn risk" ties renewal-desk ("churn") with
        # risk-watcher ("risk") and falls through to the default: the
        # tie predates this agent, which scores 0 on the phrase. The
        # invariant is that churn-post-mortem does not steal it.
        result = router.route({"text": "churn risk for Acme Corp"})
        self.assertNotEqual(result.agent, "churn-post-mortem")
        result = router.route({"text": "early warning for Acme Corp"})
        self.assertEqual(result.agent, "risk-watcher")
        result = router.route(
            {"text": "handoff for the inherited account"}
        )
        self.assertEqual(result.agent, "handoff")


class TestClosedLostRenewalOpp(unittest.TestCase):
    def test_closed_lost_renewal_found(self):
        opps = {
            "opportunities": [
                {
                    "name": "Gone Co - Renewal FY27",
                    "type": "Renewal",
                    "stage": "Closed Lost",
                    "amount": 60000,
                    "close_date": "2026-11-15",
                },
                {
                    "name": "Gone Co - Expansion",
                    "type": "Expansion",
                    "stage": "Qualification",
                    "amount": 20000,
                    "close_date": "2027-03-01",
                },
            ]
        }
        opp = _closed_lost_renewal_opp(opps)
        self.assertIsNotNone(opp)
        self.assertEqual(opp["name"], "Gone Co - Renewal FY27")

    def test_open_renewal_is_not_closed_lost(self):
        opps = {
            "opportunities": [
                {
                    "name": "Acme Corp - Renewal FY27",
                    "type": "Renewal",
                    "stage": "Negotiation",
                    "amount": 120000,
                    "close_date": "2027-02-15",
                }
            ]
        }
        self.assertIsNone(_closed_lost_renewal_opp(opps))

    def test_no_opportunities_is_none(self):
        self.assertIsNone(_closed_lost_renewal_opp({}))
        self.assertIsNone(_closed_lost_renewal_opp(None))


class TestChurnReasons(unittest.TestCase):
    def _reasons(self, **overrides):
        kwargs = dict(
            job={},
            account_record={},
            opportunities={},
            signals=[],
            records=[],
            missed=[],
            champion_signals=[],
            adoption_list=[],
        )
        kwargs.update(overrides)
        return _churn_reasons(**kwargs)

    def test_no_evidence_is_no_reason(self):
        self.assertEqual(self._reasons(), [])

    def test_signal_without_an_anchor_is_not_a_reason(self):
        # A high-severity support ticket on a live account is not a
        # churn: without a closed-lost opp, a named reason, or a
        # churned account record, no reason is claimed at all.
        reasons = self._reasons(
            signals=[
                {
                    "severity": "high",
                    "kind": "support-delivery",
                    "text": "Support/delivery risk: 'SSO login fails' is open.",
                }
            ]
        )
        self.assertEqual(reasons, [])

    def test_named_reason_from_the_job(self):
        reasons = self._reasons(
            job={"churn_reason": "Switched to a competitor after the outage."}
        )
        self.assertEqual(len(reasons), 1)
        self.assertEqual(reasons[0]["kind"], "named")
        self.assertIn("Switched to a competitor", reasons[0]["summary"])
        self.assertIn("Named in the job", reasons[0]["evidence"])

    def test_closed_lost_renewal_is_a_reason(self):
        opps = {
            "opportunities": [
                {
                    "name": "Gone Co - Renewal FY27",
                    "type": "Renewal",
                    "stage": "Closed Lost",
                    "amount": 60000,
                    "close_date": "2026-11-15",
                }
            ]
        }
        reasons = self._reasons(opportunities=opps)
        kinds = [r["kind"] for r in reasons]
        self.assertIn("closed-lost-renewal", kinds)
        reason = reasons[kinds.index("closed-lost-renewal")]
        self.assertIn("$60,000", reason["summary"])
        self.assertIn("2026-11-15", reason["summary"])

    def test_champion_moved_on_is_a_reason(self):
        reasons = self._reasons(
            opportunities=_ANCHOR_OPPS,
            champion_signals=[
                {
                    "severity": "high",
                    "kind": "champion-moved-on",
                    "text": "Champion moved on: Dana Cole (Gmail, 'moved on'): 'Dana has moved on'",
                }
            ],
        )
        kinds = [r["kind"] for r in reasons]
        self.assertIn("champion-departure", kinds)

    def test_champion_bounce_is_a_reason(self):
        reasons = self._reasons(
            opportunities=_ANCHOR_OPPS,
            champion_signals=[
                {
                    "severity": "high",
                    "kind": "champion-bounce",
                    "text": "Champion email bouncing: Dana Cole",
                }
            ],
        )
        kinds = [r["kind"] for r in reasons]
        self.assertIn("champion-departure", kinds)

    def test_quiet_or_promoted_champion_is_not_a_reason(self):
        reasons = self._reasons(
            opportunities=_ANCHOR_OPPS,
            champion_signals=[
                {
                    "severity": "medium",
                    "kind": "champion-quiet",
                    "text": "Champion gone quiet: Dana Cole",
                },
                {
                    "severity": "medium",
                    "kind": "champion-promoted",
                    "text": "Champion promoted, not lost: Dana Cole",
                },
            ],
        )
        kinds = [r["kind"] for r in reasons]
        self.assertNotIn("champion-departure", kinds)

    def test_high_support_signal_is_a_reason(self):
        reasons = self._reasons(
            opportunities=_ANCHOR_OPPS,
            signals=[
                {
                    "severity": "high",
                    "kind": "support-delivery",
                    "text": "Support/delivery risk: 'SSO login fails' is open.",
                }
            ],
        )
        kinds = [r["kind"] for r in reasons]
        self.assertIn("support-failure", kinds)

    def test_medium_support_signal_is_not_a_reason(self):
        reasons = self._reasons(
            opportunities=_ANCHOR_OPPS,
            signals=[
                {
                    "severity": "medium",
                    "kind": "support-delivery",
                    "text": "Support/delivery risk: 'Export timeout' is open.",
                }
            ],
        )
        kinds = [r["kind"] for r in reasons]
        self.assertNotIn("support-failure", kinds)

    def test_missed_commitments_are_a_reason(self):
        reasons = self._reasons(
            opportunities=_ANCHOR_OPPS,
            missed=[
                {
                    "text": "Ship the SSO fix",
                    "owner": "Sam Reyes",
                    "due": date(2026, 11, 1),
                    "status": "new",
                }
            ],
        )
        kinds = [r["kind"] for r in reasons]
        self.assertIn("missed-commitments", kinds)
        reason = reasons[kinds.index("missed-commitments")]
        self.assertIn("Ship the SSO fix", reason["summary"])

    def test_open_escalation_is_a_reason(self):
        record = EscalationRecord(
            id="esc-1",
            account="Gone Co",
            signal_key="support-delivery:sso",
            signal_kind="support-delivery",
            severity="high",
            summary="SSO login fails for new hires",
            status="open",
        )
        reasons = self._reasons(
            opportunities=_ANCHOR_OPPS, records=[record]
        )
        kinds = [r["kind"] for r in reasons]
        self.assertIn("escalation", kinds)

    def test_resolved_escalation_is_not_a_reason(self):
        record = EscalationRecord(
            id="esc-1",
            account="Gone Co",
            signal_key="support-delivery:sso",
            signal_kind="support-delivery",
            severity="high",
            summary="SSO login fails for new hires",
            status="resolved",
        )
        reasons = self._reasons(
            opportunities=_ANCHOR_OPPS, records=[record]
        )
        kinds = [r["kind"] for r in reasons]
        self.assertNotIn("escalation", kinds)

    def test_adoption_gaps_are_a_reason(self):
        reasons = self._reasons(
            opportunities=_ANCHOR_OPPS,
            adoption_list=[
                {
                    "kind": "seats",
                    "item": "80 unused seats",
                    "evidence": "40 of 120 seats active (last-login data)",
                    "source": "job usage",
                    "value_at_stake": 40000,
                    "next_step": "Right-size or reassign.",
                }
            ],
        )
        kinds = [r["kind"] for r in reasons]
        self.assertIn("adoption", kinds)

    def test_churned_account_record_is_an_anchor(self):
        reasons = self._reasons(
            account_record={"status": "churned"},
            signals=[
                {
                    "severity": "high",
                    "kind": "support-delivery",
                    "text": "Support/delivery risk: 'SSO login fails' is open.",
                }
            ],
        )
        kinds = [r["kind"] for r in reasons]
        self.assertIn("support-failure", kinds)

    def test_multiple_reasons_all_named(self):
        opps = {
            "opportunities": [
                {
                    "name": "Gone Co - Renewal FY27",
                    "type": "Renewal",
                    "stage": "Closed Lost",
                    "amount": 60000,
                    "close_date": "2026-11-15",
                }
            ]
        }
        reasons = self._reasons(
            job={"churn_reason": "Switched to a competitor."},
            opportunities=opps,
        )
        kinds = [r["kind"] for r in reasons]
        self.assertEqual(kinds, ["named", "closed-lost-renewal"])


class TestReapproachTiming(unittest.TestCase):
    def test_named_date_wins(self):
        timing = _reapproach_timing(
            {"reapproach_date": "2027-03-01"}, None, TODAY
        )
        self.assertEqual(timing["kind"], "named")
        self.assertIn("2027-03-01", timing["text"])

    def test_replacement_renewal_date(self):
        timing = _reapproach_timing(
            {"replacement_renewal_date": "2027-06-01"}, None, TODAY
        )
        self.assertEqual(timing["kind"], "replacement-renewal")
        self.assertIn("2027-06-01", timing["text"])

    def test_cooling_off_from_closed_lost_date(self):
        lost_opp = {
            "name": "Gone Co - Renewal FY27",
            "type": "Renewal",
            "stage": "Closed Lost",
            "amount": 60000,
            "close_date": "2026-11-15",
        }
        timing = _reapproach_timing({}, lost_opp, TODAY)
        self.assertEqual(timing["kind"], "cooling-off")
        # 2026-11-15 + 90 days = 2027-02-13, labeled proposed.
        self.assertIn("2027-02-13", timing["text"])
        self.assertIn("Proposed", timing["text"])

    def test_custom_cooling_off_window(self):
        lost_opp = {
            "name": "Gone Co - Renewal FY27",
            "type": "Renewal",
            "stage": "Closed Lost",
            "amount": 60000,
            "close_date": "2026-11-15",
        }
        timing = _reapproach_timing(
            {"cooling_off_days": 30}, lost_opp, TODAY
        )
        self.assertEqual(timing["kind"], "cooling-off")
        self.assertIn("2026-12-15", timing["text"])

    def test_bad_dates_are_ignored(self):
        timing = _reapproach_timing(
            {"reapproach_date": "not a date"}, None, TODAY
        )
        self.assertEqual(timing["kind"], "none")

    def test_no_window_is_a_gap(self):
        timing = _reapproach_timing({}, None, TODAY)
        self.assertEqual(timing["kind"], "none")
        self.assertIn("No re-approach window is established", timing["evidence"])


class TestReapproachTriggers(unittest.TestCase):
    def test_new_company_is_a_trigger(self):
        triggers = _reapproach_triggers({}, "Initech")
        self.assertEqual(len(triggers), 1)
        self.assertIn("Initech", triggers[0])

    def test_named_trigger(self):
        triggers = _reapproach_triggers(
            {"trigger": "New VP of Ops starts in January."}, None
        )
        self.assertEqual(len(triggers), 1)
        self.assertIn("New VP of Ops", triggers[0])

    def test_no_triggers(self):
        self.assertEqual(_reapproach_triggers({}, None), [])

    def test_new_company_from_prior_draft(self):
        company = _extract_new_company(
            {}, "Champion moved on. new company: Initech Corp."
        )
        self.assertEqual(company, "Initech Corp")

    def test_new_company_from_the_job(self):
        company = _extract_new_company(
            {"champion_new_company": "Initech"}, ""
        )
        self.assertEqual(company, "Initech")

    def test_prose_mention_is_not_a_company(self):
        company = _extract_new_company(
            {}, "Dana mentioned Initech in passing on the call."
        )
        self.assertIsNone(company)


class TestReapproachContact(unittest.TestCase):
    def test_account_owner_is_the_contact(self):
        contact = _reapproach_contact(
            {}, {"owner": "Jane CSM"}, [], "", None, None
        )
        self.assertEqual(contact["name"], "Jane CSM")
        self.assertEqual(contact["role"], "account owner")

    def test_champion_at_new_company_beats_owner(self):
        contact = _reapproach_contact(
            {},
            {"owner": "Jane CSM"},
            [],
            "",
            "Dana Cole",
            "Initech",
        )
        self.assertEqual(contact["name"], "Dana Cole")
        self.assertIn("Initech", contact["role"])

    def test_champion_without_a_named_company_is_not_the_contact(self):
        contact = _reapproach_contact(
            {}, {"owner": "Jane CSM"}, [], "", "Dana Cole", None
        )
        self.assertEqual(contact["name"], "Jane CSM")

    def test_successor_beats_owner(self):
        people = [
            {"key": "sam reyes", "side": "customer", "emails": []},
        ]
        contact = _reapproach_contact(
            {},
            {"owner": "Jane CSM"},
            people,
            "successor: Sam Reyes",
            None,
            None,
        )
        self.assertEqual(contact["name"], "Sam Reyes")
        self.assertEqual(contact["role"], "successor")

    def test_no_contact_is_none(self):
        contact = _reapproach_contact({}, {}, [], "", None, None)
        self.assertIsNone(contact)


class TestWhatWasLost(unittest.TestCase):
    def test_arr_lost_against_closed_lost_opp(self):
        lost_opp = {
            "name": "Gone Co - Renewal FY27",
            "type": "Renewal",
            "stage": "Closed Lost",
            "amount": 60000,
            "close_date": "2026-11-15",
        }
        lost = _what_was_lost(
            {"arr": 60000}, lost_opp, {"opportunities": []}, {}, True
        )
        self.assertIn("ARR lost: $60,000", lost[0])

    def test_arr_not_claimed_lost_without_closed_lost_opp(self):
        lost = _what_was_lost(
            {"arr": 120000}, None, {"opportunities": []}, {}, True
        )
        self.assertIn("ARR on record: $120,000", lost[0])
        self.assertNotIn("ARR lost", " ".join(lost))

    def test_no_loss_claimed_for_a_clean_account(self):
        lost = _what_was_lost(
            {"arr": 120000}, None, {"opportunities": []}, {}, False
        )
        self.assertIn("No loss claimed", lost[0])
        self.assertIn("$120,000", lost[0])

    def test_open_expansion_not_listed_for_a_clean_account(self):
        lost = _what_was_lost(
            {"arr": 120000},
            None,
            {
                "opportunities": [
                    {
                        "name": "Acme Corp - Expansion (50 seats)",
                        "type": "Expansion",
                        "stage": "Qualification",
                        "amount": 45000,
                        "close_date": "2027-04-01",
                    }
                ]
            },
            {},
            False,
        )
        self.assertFalse(
            any("unlikely to close" in line for line in lost)
        )
        lost = _what_was_lost(
            {"arr": 60000},
            {
                "name": "Gone Co - Renewal FY27",
                "type": "Renewal",
                "stage": "Closed Lost",
                "amount": 60000,
                "close_date": "2026-11-15",
            },
            {
                "opportunities": [
                    {
                        "name": "Gone Co - Expansion (20 seats)",
                        "type": "Expansion",
                        "stage": "Qualification",
                        "amount": 20000,
                        "close_date": "2027-03-01",
                    }
                ]
            },
            {},
            True,
        )
        self.assertTrue(
            any("unlikely to close" in line for line in lost)
        )
        self.assertTrue(any("$20,000" in line for line in lost))


class TestChurnPostMortemBlock(unittest.TestCase):
    def _block(self, **overrides):
        kwargs = dict(
            reasons=[
                {
                    "kind": "closed-lost-renewal",
                    "summary": "Renewal Gone Co - Renewal FY27 closed lost "
                    "($60,000, close date 2026-11-15)",
                    "evidence": "Closed-lost renewal opportunity Gone Co - "
                    "Renewal FY27 in the gathered records.",
                }
            ],
            timeline=[
                "Last touch: 2026-11-10.",
                "Renewal closed lost: 2026-11-15 (Gone Co - Renewal FY27).",
            ],
            lost=["ARR lost: $60,000 (renewal Gone Co - Renewal FY27 closed lost)."],
            timing={
                "kind": "cooling-off",
                "text": "Proposed: re-approach 2027-02-13 (90 days after "
                "the renewal closed lost on 2026-11-15). Proposed, not a "
                "commitment: confirm the window with the account owner "
                "before the SDR acts on it.",
                "evidence": "Cooling-off window counted from the "
                "closed-lost date 2026-11-15.",
            },
            triggers=[
                "Departed champion now at Initech (named in the records): "
                "the move is a re-approach trigger."
            ],
            contact={
                "name": "Dana Cole",
                "role": "departed champion, now at Initech",
                "evidence": "Departed champion's new company named in "
                "the records.",
            },
            gaps=[],
        )
        kwargs.update(overrides)
        return _churn_post_mortem_block(**kwargs)

    def test_block_renders_all_sections(self):
        block = self._block()
        for section in (
            "Churn reason:",
            "Timeline:",
            "What was lost:",
            "Re-approach plan:",
            "Do not:",
            "Gaps:",
        ):
            self.assertIn(section, block)
        self.assertIn("closed-lost-renewal", block)
        self.assertIn("ARR lost: $60,000", block)
        self.assertIn("Proposed: re-approach 2027-02-13", block)
        self.assertIn("Dana Cole", block)
        self.assertIn("Initech", block)
        self.assertIn("Gaps:\n- None.", block)

    def test_do_not_list_is_present(self):
        block = self._block()
        self.assertIn("Do not re-litigate the churn", block)
        self.assertIn("Do not lead with a discount", block)
        self.assertIn(
            "Do not contact the departed champion at their old address.",
            block,
        )

    def test_block_with_no_reason_names_the_gap(self):
        block = self._block(
            reasons=[],
            timing={
                "kind": "none",
                "text": "",
                "evidence": "No re-approach window is established.",
            },
            triggers=[],
            contact=None,
            gaps=["No churn reason is on file."],
        )
        self.assertIn("No churn reason is on file", block)
        self.assertIn("no re-approach window is established", block)
        self.assertIn("no re-approach contact on file", block)
        self.assertIn("Triggers: none on file", block)
        self.assertIn(
            "unknown until the churn reason is established", block
        )

    def test_block_with_no_timeline_names_the_gap(self):
        block = self._block(timeline=[])
        self.assertIn("No dated events on record", block)


class TestChurnPostMortemEndToEnd(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_stub_run_on_acme_is_a_gap_named_draft(self):
        # Acme Corp in stub mode is green with an open renewal: no
        # churn evidence, so the draft names the gaps and claims no
        # loss, never a fabricated post-mortem.
        agent = AGENTS["churn-post-mortem"]
        draft = agent.run(
            {"account": "Acme Corp"}, self.registry, self.llm, self.budget
        )
        self.assertEqual(draft.agent, "churn-post-mortem")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("salesforce_list_opportunities", draft.sources)
        self.assertIn("Acme Corp", draft.text)
        self.assertIn("Churn reason:", draft.text)
        self.assertIn("No churn reason is on file", draft.text)
        self.assertIn("No loss claimed", draft.text)
        self.assertIn("Do not re-litigate the churn", draft.text)
        self.assertTrue(draft.links)
        self.assertTrue(draft.proof_passed)

    def test_job_named_reason_in_the_run(self):
        agent = AGENTS["churn-post-mortem"]
        draft = agent.run(
            {
                "account": "Acme Corp",
                "churn_reason": "Switched to a competitor after the outage.",
            },
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("[named] Switched to a competitor", draft.text)
        self.assertIn("Named in the job", draft.text)
        self.assertIn("ARR on record: $120,000", draft.text)
        self.assertTrue(draft.proof_passed)

    def test_csv_demo_run_does_not_crash(self):
        registry = _csv_registry(_sample_csv_path())
        agent = AGENTS["churn-post-mortem"]
        draft = agent.run(
            {"account": "Acme Corp"}, registry, self.llm, self.budget
        )
        self.assertEqual(draft.agent, "churn-post-mortem")
        self.assertIn("Churn reason:", draft.text)
        self.assertIn("No churn reason is on file", draft.text)
        self.assertIn("No loss claimed", draft.text)
        self.assertTrue(draft.proof_passed)

    def test_churned_account_csv_run(self):
        csv_path = _churned_csv_path()
        try:
            registry = _csv_registry(csv_path)
            agent = AGENTS["churn-post-mortem"]
            draft = agent.run(
                {
                    "account": "Gone Co",
                    "champion": "Dana Cole",
                    "champion_new_company": "Initech",
                },
                registry,
                self.llm,
                self.budget,
            )
            self.assertEqual(draft.agent, "churn-post-mortem")
            self.assertIn("[closed-lost-renewal]", draft.text)
            self.assertIn("ARR lost: $60,000", draft.text)
            self.assertIn("Proposed: re-approach 2027-02-13", draft.text)
            self.assertIn("Dana Cole", draft.text)
            self.assertIn("Initech", draft.text)
            self.assertIn("Last touch: 2026-11-10.", draft.text)
            self.assertIn(
                "Renewal closed lost: 2026-11-15 (Gone Co - Renewal FY27).",
                draft.text,
            )
            self.assertIn("Do not lead with a discount", draft.text)
            self.assertTrue(draft.proof_passed)
        finally:
            os.unlink(csv_path)

    def test_clean_account_names_the_gaps(self):
        csv_path = _clean_csv_path()
        try:
            registry = _csv_registry(csv_path)
            agent = AGENTS["churn-post-mortem"]
            draft = agent.run(
                {"account": "Quiet Co"}, registry, self.llm, self.budget
            )
            self.assertEqual(draft.agent, "churn-post-mortem")
            self.assertIn("No churn reason is on file", draft.text)
            self.assertIn("No loss claimed", draft.text)
            self.assertIn("no re-approach window is established", draft.text)
            self.assertTrue(draft.proof_passed)
        finally:
            os.unlink(csv_path)

    def test_missed_commitments_feed_the_reason(self):
        path = _temp_queue_path()
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
        _seed(
            path,
            [("call-recap-and-commitments", "Acme Corp", line, [])],
        )
        agent = AGENTS["churn-post-mortem"]
        draft = agent.run(
            {
                "account": "Acme Corp",
                "approvals_file": path,
                "churn_reason": "Renewal lost after a string of missed commitments.",
            },
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("[missed-commitments]", draft.text)
        self.assertIn("Ship the SSO fix", draft.text)
        self.assertIn("Composed from prior drafts:", draft.text)
        self.assertTrue(draft.proof_passed)

    def test_composed_from_prior_drafts(self):
        path = _temp_queue_path()
        _seed(
            path,
            [
                (
                    "risk-watcher",
                    "Acme Corp",
                    "Risk brief: high-severity SSO failure open.",
                    [],
                ),
                (
                    "champion-change",
                    "Acme Corp",
                    "Champion change: Dana Cole moved on. new company: Initech.",
                    [],
                ),
            ],
        )
        agent = AGENTS["churn-post-mortem"]
        draft = agent.run(
            {
                "account": "Acme Corp",
                "approvals_file": path,
                "champion": "Dana Cole",
            },
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn("Composed from prior drafts:", draft.text)
        self.assertIn("risk-watcher", draft.text)
        self.assertIn("champion-change", draft.text)
        self.assertIn("Initech", draft.text)
        self.assertTrue(draft.proof_passed)


if __name__ == "__main__":
    unittest.main()
