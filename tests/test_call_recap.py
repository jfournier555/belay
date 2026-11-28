"""Tests for the call-recap-and-commitments agent (v0.19.0).

The call-recap agent tracks what was promised on calls and who owns
it: a short recap plus a commitments table with owner, due date, and
status (new, carried over, overdue). Commitments are harness-computed
from Zoom action items, Gong key points phrased as an action, and
transcript sentences with a first-person promise, so extraction, due
dates, owners, and status are tested directly on synthetic inputs as
well as end to end with stub tools and the stub LLM.

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
    _commitment_from_action_item,
    _commitment_kept,
    _extract_commitments,
    _extract_commitments_from_text,
    _extract_owner,
    _format_commitment_line,
    _latest_call,
    _parse_commitment_line,
    _parse_due_date,
    _prior_commitments,
    _status_commitments,
)
from harness.config import Config
from harness.llm import StubLLM
from harness.router import Router

CALL_DATE = date(2026, 11, 13)  # a Friday; the stub QBR prep call date


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


def _csv_registry() -> ToolRegistry:
    """Registry wired to the CSV accounts connector (sample demo) for
    the CRM tools, plus stub tools for calls, messages, and tickets."""
    config = Config.from_env()
    config.stub_mode = True
    config.csv_accounts_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "connectors",
        "csv",
        "sample_accounts.csv",
    )
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


class TestCallRecapRegistered(unittest.TestCase):
    def test_nineteen_agents_registered(self):
        self.assertEqual(len(AGENTS), 19)
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
                "competitive-defense",
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
                "value-finder",
                "voice-of-customer",
            ],
        )

    def test_router_routes_explicitly_and_by_keyword(self):
        router = Router(agents=AGENTS, llm=None)
        result = router.route(
            {"text": "anything", "agent": "call-recap-and-commitments"}
        )
        self.assertEqual(result.agent, "call-recap-and-commitments")
        self.assertEqual(result.confidence, "high")
        self.assertIsNone(result.error)
        result = router.route({"text": "call recap for Acme Corp"})
        self.assertEqual(result.agent, "call-recap-and-commitments")
        result = router.route(
            {"text": "what was promised on the call for Acme Corp"}
        )
        self.assertEqual(result.agent, "call-recap-and-commitments")
        result = router.route({"text": "track the commitments for Acme Corp"})
        self.assertEqual(result.agent, "call-recap-and-commitments")


class TestParseDueDate(unittest.TestCase):
    def test_iso_date(self):
        self.assertEqual(
            _parse_due_date("Send it by 2026-12-01.", CALL_DATE),
            date(2026, 12, 1),
        )

    def test_month_day(self):
        self.assertEqual(
            _parse_due_date("Jane to send it by December 1.", CALL_DATE),
            date(2026, 12, 1),
        )
        self.assertEqual(
            _parse_due_date("Jane to send it by Dec 1st.", CALL_DATE),
            date(2026, 12, 1),
        )

    def test_month_day_in_the_past_rolls_to_next_year(self):
        self.assertEqual(
            _parse_due_date("Jane to send it by January 5.", CALL_DATE),
            date(2027, 1, 5),
        )

    def test_next_week(self):
        self.assertEqual(
            _parse_due_date("We will review it next week.", CALL_DATE),
            date(2026, 11, 20),
        )

    def test_tomorrow(self):
        self.assertEqual(
            _parse_due_date("I'll follow up tomorrow.", CALL_DATE),
            date(2026, 11, 14),
        )

    def test_by_friday_from_a_friday_is_next_friday(self):
        self.assertEqual(
            _parse_due_date("I will send it by Friday.", CALL_DATE),
            date(2026, 11, 20),
        )

    def test_end_of_week_from_a_friday_is_that_friday(self):
        self.assertEqual(
            _parse_due_date("Send it by end of week.", CALL_DATE),
            date(2026, 11, 13),
        )

    def test_no_date_is_none_not_a_guess(self):
        self.assertIsNone(_parse_due_date("Jane to book the intro.", CALL_DATE))
        self.assertIsNone(_parse_due_date("", CALL_DATE))
        self.assertIsNone(
            _parse_due_date("Jane to send it by February 30.", CALL_DATE)
        )


class TestExtractOwner(unittest.TestCase):
    def test_named_actor(self):
        self.assertEqual(
            _extract_owner("Jane to book the intro with ops."), "Jane"
        )
        self.assertEqual(
            _extract_owner("Support to confirm the fix ship date."),
            "Support",
        )

    def test_no_actor_is_empty_not_a_guess(self):
        self.assertEqual(_extract_owner("The fix ships next week."), "")
        self.assertEqual(_extract_owner("No owner here."), "")
        self.assertEqual(_extract_owner(""), "")


class TestCommitmentFromActionItem(unittest.TestCase):
    def test_action_item_keeps_wording_owner_and_due(self):
        commitment = _commitment_from_action_item(
            "Jane to book the intro by December 1.",
            'Zoom recording "Acme Corp - QBR prep" (action item)',
            CALL_DATE,
        )
        self.assertEqual(
            commitment["text"], "Jane to book the intro by December 1."
        )
        self.assertEqual(commitment["owner"], "Jane")
        self.assertEqual(commitment["due"], date(2026, 12, 1))
        self.assertEqual(commitment["status"], "new")

    def test_action_item_without_a_date_has_none(self):
        commitment = _commitment_from_action_item(
            "Support to confirm the export fix ship date.",
            'Zoom recording "Acme Corp - QBR prep" (action item)',
            CALL_DATE,
        )
        self.assertEqual(commitment["owner"], "Support")
        self.assertIsNone(commitment["due"])

    def test_empty_item_is_none(self):
        self.assertIsNone(_commitment_from_action_item("   ", "x", CALL_DATE))


class TestExtractCommitmentsFromText(unittest.TestCase):
    def test_first_person_promise_belongs_to_the_speaker(self):
        found = _extract_commitments_from_text(
            "I will send the renewal paperwork by December 1.",
            "jane-csm",
            "Gong call",
            CALL_DATE,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["owner"], "jane-csm")
        self.assertEqual(found[0]["due"], date(2026, 12, 1))

    def test_we_promise_is_ownerless(self):
        found = _extract_commitments_from_text(
            "We will review the export timeout next week.",
            "acme-admin",
            "Gong call",
            CALL_DATE,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["owner"], "")
        self.assertEqual(found[0]["due"], date(2026, 11, 20))

    def test_contraction_promise_counts(self):
        found = _extract_commitments_from_text(
            "I'll follow up tomorrow.", "jane-csm", "Gong call", CALL_DATE
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["due"], date(2026, 11, 14))

    def test_discussion_is_not_a_commitment(self):
        self.assertEqual(
            _extract_commitments_from_text(
                "The rollout is live for fifty seats now.",
                "acme-ops-lead",
                "Gong call",
                CALL_DATE,
            ),
            [],
        )
        self.assertEqual(
            _extract_commitments_from_text(
                "SSO login fails for new hires again.",
                "acme-admin",
                "Gong call",
                CALL_DATE,
            ),
            [],
        )


class TestCommitmentLineRoundTrip(unittest.TestCase):
    def test_format_then_parse(self):
        commitment = {
            "text": "Support to confirm the export fix ship date.",
            "owner": "Support",
            "due": date(2026, 12, 1),
            "source": 'Zoom recording "Acme Corp - QBR prep" (action item)',
            "status": "carried over",
        }
        line = _format_commitment_line(commitment, 1)
        parsed = _parse_commitment_line(line)
        self.assertEqual(parsed["text"], commitment["text"])
        self.assertEqual(parsed["owner"], "Support")
        self.assertEqual(parsed["due"], date(2026, 12, 1))
        self.assertEqual(parsed["status"], "carried over")

    def test_ownerless_round_trips_as_empty_owner(self):
        commitment = {
            "text": "We will review the export timeout next week.",
            "owner": "",
            "due": None,
            "source": "Gong call",
            "status": "new",
        }
        line = _format_commitment_line(commitment, 1)
        self.assertIn("owner: no owner named", line)
        self.assertIn("due: no date given", line)
        parsed = _parse_commitment_line(line)
        self.assertEqual(parsed["owner"], "")
        self.assertIsNone(parsed["due"])

    def test_truncated_line_is_none(self):
        self.assertIsNone(
            _parse_commitment_line(
                '1. "Support to confirm the export fix ship'
            )
        )
        self.assertIsNone(_parse_commitment_line("not a commitment line"))


class TestCommitmentKept(unittest.TestCase):
    def test_kept_with_evidence_sentence(self):
        evidence = _commitment_kept(
            "Support to confirm the export fix ship date.",
            "Support confirmed the export fix shipped.",
        )
        self.assertEqual(
            evidence, "Support confirmed the export fix shipped."
        )

    def test_still_open_without_a_completion_phrase(self):
        self.assertIsNone(
            _commitment_kept(
                "Support to confirm the export fix ship date.",
                "The export timeout is still open.",
            )
        )

    def test_one_shared_word_is_not_enough(self):
        self.assertIsNone(
            _commitment_kept(
                "Support to confirm the export fix ship date.",
                "The export timeout is still open; the fix is pending.",
            )
        )


class TestStatusCommitments(unittest.TestCase):
    def test_new_commitment_stays_new(self):
        open_list, closed = _status_commitments(
            [
                {
                    "text": "Jane to book the intro.",
                    "owner": "Jane",
                    "due": None,
                    "source": "x",
                    "status": "new",
                }
            ],
            [],
            "",
            date(2026, 11, 20),
        )
        self.assertEqual(len(open_list), 1)
        self.assertEqual(open_list[0]["status"], "new")
        self.assertEqual(closed, [])

    def test_matching_prior_is_carried_over_and_keeps_the_due_date(self):
        open_list, closed = _status_commitments(
            [
                {
                    "text": "Support to confirm the export fix ship date.",
                    "owner": "Support",
                    "due": None,
                    "source": "x",
                    "status": "new",
                }
            ],
            [
                {
                    "text": "Support to confirm the export fix ship date.",
                    "owner": "Support",
                    "due": date(2026, 12, 1),
                    "source": "y",
                    "status": "new",
                }
            ],
            "",
            date(2026, 11, 20),
        )
        self.assertEqual(len(open_list), 1)
        self.assertEqual(open_list[0]["status"], "carried over")
        self.assertEqual(open_list[0]["due"], date(2026, 12, 1))
        self.assertEqual(closed, [])

    def test_prior_shown_done_is_closed_with_evidence(self):
        open_list, closed = _status_commitments(
            [],
            [
                {
                    "text": "Support to confirm the export fix ship date.",
                    "owner": "Support",
                    "due": None,
                    "source": "y",
                    "status": "new",
                }
            ],
            "Support confirmed the export fix shipped.",
            date(2026, 11, 20),
        )
        self.assertEqual(open_list, [])
        self.assertEqual(len(closed), 1)
        self.assertIn("shipped", closed[0]["kept_evidence"])

    def test_prior_not_mentioned_is_carried_over(self):
        open_list, closed = _status_commitments(
            [],
            [
                {
                    "text": "Jane to send the renewal paperwork.",
                    "owner": "Jane",
                    "due": date(2026, 12, 1),
                    "source": "y",
                    "status": "new",
                }
            ],
            "The rollout is live for fifty seats now.",
            date(2026, 11, 20),
        )
        self.assertEqual(len(open_list), 1)
        self.assertEqual(open_list[0]["status"], "carried over")
        self.assertEqual(closed, [])

    def test_due_date_in_the_past_is_overdue(self):
        open_list, _closed = _status_commitments(
            [
                {
                    "text": "Jane to send the renewal paperwork.",
                    "owner": "Jane",
                    "due": date(2026, 11, 19),
                    "source": "x",
                    "status": "new",
                }
            ],
            [],
            "",
            date(2026, 11, 20),
        )
        self.assertEqual(open_list[0]["status"], "overdue")

    def test_overdue_sorts_first(self):
        open_list, _closed = _status_commitments(
            [
                {
                    "text": "Jane to book the intro.",
                    "owner": "Jane",
                    "due": None,
                    "source": "x",
                    "status": "new",
                },
                {
                    "text": "Support to confirm the fix.",
                    "owner": "Support",
                    "due": date(2026, 11, 19),
                    "source": "x",
                    "status": "new",
                },
            ],
            [],
            "",
            date(2026, 11, 20),
        )
        self.assertEqual(
            [c["status"] for c in open_list], ["overdue", "new"]
        )


class TestLatestCall(unittest.TestCase):
    def test_latest_call_merges_both_systems(self):
        results = {
            "gong_list_calls": {
                "calls": [
                    {
                        "id": "1",
                        "title": "Acme Corp - Renewal check-in",
                        "started": "2026-11-10T14:00:00Z",
                        "duration_seconds": 1500,
                        "participants": ["jane-csm"],
                    },
                    {
                        "id": "2",
                        "title": "Acme Corp - QBR prep",
                        "started": "2026-11-13T15:00:00Z",
                        "duration_seconds": 1800,
                        "participants": ["jane-csm", "acme-admin"],
                    },
                ]
            },
            "zoom_list_recordings": {
                "recordings": [
                    {
                        "id": "9",
                        "topic": "Acme Corp - QBR prep",
                        "start_time": "2026-11-13T15:00:00Z",
                        "duration": 1800,
                        "host": "jane-csm",
                        "play_url": "https://zoom.invalid/rec/play/9",
                    }
                ]
            },
        }
        call = _latest_call(results, {})
        self.assertEqual(call["title"], "Acme Corp - QBR prep")
        self.assertEqual(call["date"], date(2026, 11, 13))
        self.assertEqual(call["gong_call_id"], "2")
        self.assertEqual(call["zoom_recording_id"], "9")

    def test_job_call_id_selects_that_call(self):
        results = {
            "gong_list_calls": {
                "calls": [
                    {
                        "id": "1",
                        "title": "Acme Corp - Renewal check-in",
                        "started": "2026-11-10T14:00:00Z",
                        "duration_seconds": 1500,
                        "participants": ["jane-csm"],
                    },
                    {
                        "id": "2",
                        "title": "Acme Corp - QBR prep",
                        "started": "2026-11-13T15:00:00Z",
                        "duration_seconds": 1800,
                        "participants": ["jane-csm", "acme-admin"],
                    },
                ]
            },
            "zoom_list_recordings": {"recordings": []},
        }
        call = _latest_call(results, {"call_id": "1"})
        self.assertEqual(call["title"], "Acme Corp - Renewal check-in")
        self.assertEqual(call["gong_call_id"], "1")

    def test_no_calls_is_a_blank_call(self):
        call = _latest_call({}, {})
        self.assertEqual(call["title"], "")
        self.assertIsNone(call["date"])
        self.assertEqual(call["gong_call_id"], "")
        self.assertEqual(call["zoom_recording_id"], "")


class TestExtractCommitments(unittest.TestCase):
    def test_action_items_key_points_and_excerpts(self):
        results = {
            "zoom_get_recording_summary": {
                "recording_id": "88123456789",
                "title": "Acme Corp - QBR prep",
                "action_items": [
                    "Jane to book the intro by December 1.",
                    "Support to confirm the fix.",
                ],
            },
            "gong_get_call_summary": {
                "call_id": "7782342274025937895",
                "title": "Acme Corp - QBR prep",
                "key_points": ["Reporting rollout live on 50 seats."],
            },
            "gong_search_transcripts": {
                "matches": [
                    {
                        "call_id": "7782342274025937895",
                        "title": "Acme Corp - QBR prep",
                        "timestamp": "12:04",
                        "speaker": "acme-ops-lead",
                        "excerpt": "I will send the comparison by Friday.",
                    }
                ]
            },
            "zoom_search_transcripts": {"matches": []},
        }
        call = {
            "title": "Acme Corp - QBR prep",
            "date": CALL_DATE,
            "gong_call_id": "7782342274025937895",
            "zoom_recording_id": "88123456789",
        }
        commitments = _extract_commitments(results, call, CALL_DATE)
        self.assertEqual(len(commitments), 3)
        by_text = {c["text"]: c for c in commitments}
        self.assertEqual(
            by_text["Jane to book the intro by December 1."]["owner"],
            "Jane",
        )
        self.assertEqual(
            by_text["Jane to book the intro by December 1."]["due"],
            date(2026, 12, 1),
        )
        self.assertEqual(
            by_text["Support to confirm the fix."]["owner"], "Support"
        )
        self.assertEqual(
            by_text["I will send the comparison by Friday."]["owner"],
            "acme-ops-lead",
        )
        self.assertEqual(
            by_text["I will send the comparison by Friday."]["due"],
            date(2026, 11, 20),
        )

    def test_excerpts_from_other_calls_are_excluded(self):
        results = {
            "zoom_get_recording_summary": {},
            "gong_get_call_summary": {},
            "gong_search_transcripts": {
                "matches": [
                    {
                        "call_id": "other-call",
                        "title": "Other call",
                        "timestamp": "01:00",
                        "speaker": "someone",
                        "excerpt": "I will send the comparison by Friday.",
                    }
                ]
            },
            "zoom_search_transcripts": {"matches": []},
        }
        call = {
            "title": "Acme Corp - QBR prep",
            "date": CALL_DATE,
            "gong_call_id": "7782342274025937895",
            "zoom_recording_id": "88123456789",
        }
        self.assertEqual(_extract_commitments(results, call, CALL_DATE), [])


class TestPriorCommitments(unittest.TestCase):
    def test_prior_draft_lines_parse_back(self):
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "call-recap-and-commitments",
                        "Acme Corp",
                        "call recap (harness):\n"
                        "call: Acme Corp - Renewal check-in — 2026-11-10, "
                        "25 min, participants: jane-csm.\n"
                        "commitments:\n"
                        '1. "Support to confirm the export fix ship date." '
                        "— owner: Support; due: 2026-12-01; status: new; "
                        'source: Zoom recording "Acme Corp - Renewal '
                        'check-in" (action item).\n'
                        "closed since last recap:\n"
                        "none.\n"
                        "ownerless commitments:\n"
                        "none.\n"
                        "gaps:\n"
                        "none.\n",
                        [],
                    ),
                ],
            )
            prior = _prior_commitments(
                {"account": "Acme Corp", "approvals_file": path},
                "Acme Corp",
            )
            self.assertEqual(len(prior), 1)
            self.assertEqual(
                prior[0]["text"],
                "Support to confirm the export fix ship date.",
            )
            self.assertEqual(prior[0]["owner"], "Support")
            self.assertEqual(prior[0]["due"], date(2026, 12, 1))
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_no_queue_file_is_no_prior_commitments(self):
        self.assertEqual(
            _prior_commitments({"account": "Acme Corp"}, "Acme Corp"), []
        )


class TestCallRecapRuns(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def test_runs_with_stub_calls_and_proves(self):
        agent = AGENTS["call-recap-and-commitments"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "call-recap-and-commitments")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("gong_list_calls", draft.sources)
        self.assertIn("zoom_list_recordings", draft.sources)
        self.assertIn("gong_get_call_summary", draft.sources)
        self.assertIn("zoom_get_recording_summary", draft.sources)
        self.assertIn("call recap (harness):", draft.text)
        self.assertIn(
            "call: Acme Corp - QBR prep — 2026-11-13, 30 min",
            draft.text,
        )
        self.assertIn(
            '"Support to confirm the export fix ship date."',
            draft.text,
        )
        self.assertIn(
            '"Jane to book the second-relationship intro with ops."',
            draft.text,
        )
        self.assertIn("owner: Support", draft.text)
        self.assertIn("owner: Jane", draft.text)
        self.assertIn("status: new", draft.text)
        self.assertIn("closed since last recap:", draft.text)
        self.assertIn("ownerless commitments:", draft.text)
        self.assertIn("gaps:\nnone.", draft.text)
        self.assertTrue(all(p["passed"] for p in draft.proof))

    def test_runs_for_a_named_call(self):
        agent = AGENTS["call-recap-and-commitments"]
        draft = agent.run(
            {
                "account": "Acme Corp",
                "call_id": "7782342274025937896",
            },
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertIn(
            "call: Acme Corp - Renewal check-in — 2026-11-10, 25 min",
            draft.text,
        )
        self.assertIn(
            '"Support to update the admin when the SSO fix ships."',
            draft.text,
        )
        self.assertIn(
            '"Jane to revisit the 50-seat expansion after the export fix."',
            draft.text,
        )
        self.assertTrue(all(p["passed"] for p in draft.proof))

    def test_csv_demo_runs(self):
        agent = AGENTS["call-recap-and-commitments"]
        draft = agent.run(
            {"account": "Acme Corp"},
            _csv_registry(),
            self.llm,
            self.budget,
        )
        self.assertEqual(draft.agent, "call-recap-and-commitments")
        self.assertIn("call recap (harness):", draft.text)
        self.assertIn(
            '"Support to confirm the export fix ship date."',
            draft.text,
        )
        self.assertTrue(all(p["passed"] for p in draft.proof))

    def test_no_calls_is_a_gap_not_a_crash(self):
        config = Config.from_env()
        config.stub_mode = True
        registry = ToolRegistry()
        for tool in salesforce_tools(config):
            registry.register(tool)
        agent = AGENTS["call-recap-and-commitments"]
        draft = agent.run(
            {"account": "Acme Corp"},
            registry,
            self.llm,
            self.budget,
        )
        self.assertIn("call: none found", draft.text)
        self.assertIn("No calls or recordings found", draft.text)
        self.assertIn("No commitments found in this call's material.", draft.text)
        self.assertTrue(all(p["passed"] for p in draft.proof))

    def test_composed_path_carries_commitments_over(self):
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "call-recap-and-commitments",
                        "Acme Corp",
                        "call recap (harness):\n"
                        "call: Acme Corp - Renewal check-in — 2026-11-10, "
                        "25 min, participants: jane-csm.\n"
                        "commitments:\n"
                        '1. "Support to confirm the export fix ship date." '
                        "— owner: Support; due: 2026-12-01; status: new; "
                        'source: Zoom recording "Acme Corp - Renewal '
                        'check-in" (action item).\n'
                        '2. "Jane to send the renewal paperwork by '
                        'December 1." — owner: Jane; due: 2026-12-01; '
                        "status: new; source: Gong call "
                        '"Acme Corp - Renewal check-in" @ 08:31 '
                        "(acme-admin).\n"
                        "closed since last recap:\n"
                        "none.\n"
                        "ownerless commitments:\n"
                        "none.\n"
                        "gaps:\n"
                        "none.\n",
                        [],
                    ),
                ],
            )
            agent = AGENTS["call-recap-and-commitments"]
            draft = agent.run(
                {
                    "account": "Acme Corp",
                    "approvals_file": path,
                },
                self.registry,
                self.llm,
                self.budget,
            )
            self.assertIn(
                "Composed from prior drafts: call-recap-and-commitments.",
                draft.text,
            )
            self.assertIn("status: carried over", draft.text)
            self.assertIn("due: 2026-12-01", draft.text)
            self.assertIn(
                '"Jane to send the renewal paperwork by December 1."',
                draft.text,
            )
            self.assertIn(
                '"Jane to book the second-relationship intro with ops."',
                draft.text,
            )
            self.assertIn("status: new", draft.text)
            self.assertTrue(all(p["passed"] for p in draft.proof))
        finally:
            if os.path.exists(path):
                os.unlink(path)
