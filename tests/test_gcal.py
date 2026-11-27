"""Tests for the Google Calendar connector (v1.16.0, 10th connector).

Read-only by design: the connector lists upcoming events and matches
them to accounts by name or attendee domain, so the briefer and scout
can ground drafts in a meeting. There is no create, update, or delete
tool. These tests pin the stub data, the matching, the config check,
the registry (two tools, both read-only), and the briefer/scout
grounding, all without credentials or network.
"""

import os
import tempfile
import unittest
from unittest import mock

from harness import Config
from harness.agents import extract_links
from harness.run import build_registry
from harness.setup import verify_gcal
from connectors.gcal.tools import (
    account_for_event,
    gcal_tools,
    get_event,
    list_events,
    match_events_to_account,
)

_STUB_CONFIG = Config(stub_mode=True)


def _temp_config(tmp):
    return Config(
        approvals_file=os.path.join(tmp, "approvals.json"),
        memory_file=os.path.join(tmp, "memory.json"),
        schedules_file=os.path.join(tmp, "schedules.json"),
        escalations_file=os.path.join(tmp, "escalations.json"),
        mcp_servers_file=os.path.join(tmp, "mcp-servers.json"),
        stub_mode=True,
    )


class TestStubEvents(unittest.TestCase):
    def test_list_returns_stub_events_soonest_first(self):
        result = list_events(_STUB_CONFIG)
        self.assertTrue(result["_stub"])
        self.assertEqual(len(result["events"]), 3)
        starts = [event["start"] for event in result["events"]]
        self.assertEqual(starts, sorted(starts))

    def test_list_filters_to_one_account(self):
        result = list_events(_STUB_CONFIG, account="Acme Corp")
        self.assertEqual(len(result["events"]), 2)
        self.assertTrue(
            all(
                event["account"] == "Acme Corp"
                for event in result["events"]
            )
        )
        summaries = [event["summary"] for event in result["events"]]
        self.assertNotIn("CS team standup", summaries)

    def test_list_for_unknown_account_is_empty_not_an_error(self):
        result = list_events(_STUB_CONFIG, account="Umbrella Corp")
        self.assertEqual(result["events"], [])

    def test_get_event_hit_and_miss(self):
        event = get_event(_STUB_CONFIG, "evt-acme-renewal-prep")
        self.assertEqual(event["summary"], "Acme Corp renewal prep — SSO fix review")
        self.assertTrue(event["_stub"])
        self.assertIn("error", get_event(_STUB_CONFIG, "evt-nope"))

    def test_events_carry_summary_fields_only(self):
        result = list_events(_STUB_CONFIG)
        for event in result["events"]:
            self.assertIn("summary", event)
            self.assertIn("start", event)
            self.assertIn("attendees", event)
            self.assertNotIn("body", event)


class TestAccountMatching(unittest.TestCase):
    def test_name_match(self):
        event = {"summary": "Acme Corp QBR", "attendees": []}
        self.assertEqual(account_for_event(event, ["Acme Corp"]), "Acme Corp")

    def test_attendee_domain_match(self):
        event = {
            "summary": "Renewal call",
            "attendees": ["Someone <ops@acme.example>"],
        }
        self.assertEqual(account_for_event(event, ["Acme Corp"]), "Acme Corp")

    def test_no_match_is_none_never_a_guess(self):
        event = {"summary": "CS team standup", "attendees": ["jane@cs-team.example"]}
        self.assertIsNone(account_for_event(event, ["Acme Corp"]))

    def test_match_tags_events_and_drops_the_rest(self):
        events = [
            {"summary": "Acme Corp renewal prep", "attendees": []},
            {"summary": "CS team standup", "attendees": []},
        ]
        matched = match_events_to_account(events, "Acme Corp")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["account"], "Acme Corp")


class TestConfigAndVerify(unittest.TestCase):
    def test_not_configured_without_the_triple(self):
        self.assertFalse(Config().gcal_configured())
        self.assertFalse(
            Config(gcal_client_id="id", gcal_client_secret="secret").gcal_configured()
        )

    def test_configured_with_the_triple(self):
        config = Config(
            gcal_client_id="id",
            gcal_client_secret="secret",
            gcal_refresh_token="refresh",
        )
        self.assertTrue(config.gcal_configured())
        self.assertFalse(
            Config(
                gcal_client_id="id",
                gcal_client_secret="secret",
                gcal_refresh_token="refresh",
                stub_mode=True,
            ).gcal_configured()
        )

    def test_verify_exchanges_the_refresh_token(self):
        config = Config(
            gcal_client_id="id",
            gcal_client_secret="secret",
            gcal_refresh_token="refresh",
        )

        class _Resp:
            status_code = 200

        with mock.patch(
            "harness.setup._http_post", return_value=_Resp()
        ) as post:
            self.assertTrue(verify_gcal(config))
            body = post.call_args.kwargs.get("data") or post.call_args[1].get("data")
            self.assertEqual(body["grant_type"], "refresh_token")

    def test_verify_fails_without_credentials(self):
        self.assertFalse(verify_gcal(Config()))


class TestRegistry(unittest.TestCase):
    def test_two_read_only_tools_and_no_write_tool(self):
        tools = gcal_tools(_STUB_CONFIG)
        names = [tool.name for tool in tools]
        self.assertEqual(names, ["gcal_list_events", "gcal_get_event"])
        for name in names:
            self.assertNotIn("create", name)
            self.assertNotIn("update", name)
            self.assertNotIn("delete", name)

    def test_build_registry_includes_gcal_tools(self):
        registry = build_registry(_temp_config(tempfile.mkdtemp()))
        names = [tool.name for tool in registry.list_tools()]
        self.assertIn("gcal_list_events", names)
        self.assertIn("gcal_get_event", names)
        self.assertEqual(len(names), 28)

    def test_event_links_come_from_the_returned_url(self):
        result = list_events(_STUB_CONFIG, account="Acme Corp")
        links = extract_links("gcal_list_events", result)
        self.assertEqual(len(links), 2)
        self.assertTrue(all("calendar.google.invalid" in link["url"] for link in links))
        self.assertTrue(all(link["label"].startswith("Calendar:") for link in links))


class TestAgentGrounding(unittest.TestCase):
    def test_briefer_and_scout_gather_calendar_events(self):
        from harness.run import execute_job

        with tempfile.TemporaryDirectory() as tmp:
            config = _temp_config(tmp)
            for agent in ("briefer", "scout"):
                item, _draft, _agent, _route = execute_job(
                    config,
                    {"agent": agent, "account": "Acme Corp", "text": "Acme Corp"},
                )
                self.assertIn("gcal_list_events", item.sources)


if __name__ == "__main__":
    unittest.main()
