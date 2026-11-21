"""Tests for the Gmail connector (v0.8.4).

Pins: the stub tools (message search, thread summary), the live-shaped
link extraction (Gmail thread URLs built from thread ids, never from
the model), registry wiring, renewal-desk gathering from Gmail, and
the dashboard source status.
"""

import asyncio
import unittest

from connectors.gmail.server import mcp as gmail_mcp
from connectors.gmail.tools import (
    get_thread,
    gmail_tools,
    search_messages,
)
from harness import AGENTS, Config, TokenBudget, ToolRegistry
from harness.agents import _gather, extract_links
from harness.config import Config as _Config
from harness.dashboard import source_statuses
from harness.llm import StubLLM
from harness.run import build_registry

from tests.test_agents import _stub_registry


def _stub_config() -> _Config:
    """A config with no credentials: stub mode for every connector."""
    return _Config()


class TestGmailStubTools(unittest.TestCase):
    def test_search_returns_matches_with_ids(self):
        result = search_messages(_stub_config(), "Acme Corp")
        self.assertTrue(result["_stub"])
        self.assertEqual(len(result["matches"]), 4)
        for match in result["matches"]:
            self.assertTrue(match["id"])
            self.assertTrue(match["thread_id"])
            self.assertTrue(match["author"])
            self.assertTrue(match["subject"])
            self.assertTrue(match["date"])
            self.assertTrue(match["excerpt"])

    def test_search_excerpts_are_short(self):
        result = search_messages(_stub_config(), "Acme Corp")
        for match in result["matches"]:
            self.assertLessEqual(len(match["excerpt"]), 200)

    def test_search_caps_matches(self):
        result = search_messages(_stub_config(), "Acme Corp", limit=2)
        self.assertEqual(len(result["matches"]), 2)

    def test_search_no_match_is_empty(self):
        result = search_messages(_stub_config(), "nonexistent keyword xyz")
        self.assertEqual(result["matches"], [])

    def test_no_full_body_anywhere(self):
        # The stub never returns a message body: snippets and headers
        # only, so nothing here can blow up a prompt.
        result = search_messages(_stub_config(), "Acme Corp")
        self.assertNotIn("body", result)
        for match in result["matches"]:
            self.assertNotIn("body", match)
        thread = get_thread(_stub_config(), "18c8f5a2b3c4d5e6")
        self.assertNotIn("body", thread)
        self.assertEqual(thread["message_count"], 2)
        self.assertEqual(len(thread["participants"]), 2)

    def test_get_thread_returns_summary(self):
        thread = get_thread(_stub_config(), "18c8f5a2b3c4d5e6")
        self.assertTrue(thread["_stub"])
        self.assertEqual(thread["thread_id"], "18c8f5a2b3c4d5e6")
        self.assertTrue(thread["subject"])
        self.assertEqual(len(thread["excerpts"]), 2)

    def test_get_thread_unknown_id_is_an_error(self):
        result = get_thread(_stub_config(), "0000000000000000")
        self.assertIn("error", result)


class TestGmailLinks(unittest.TestCase):
    def test_search_links_live_shaped(self):
        links = extract_links(
            "gmail_search_messages",
            {
                "query": "Globex",
                "matches": [
                    {
                        "id": "18d01a2b3c4d5e60",
                        "thread_id": "18c8f5a2b3c4d5e6",
                        "author": "Globex Admin <admin@globex.example>",
                        "subject": "Re: Globex renewal",
                        "date": "2026-11-14T15:02:00Z",
                        "excerpt": "Renewal question.",
                    },
                    {"subject": "No thread id here"},
                ],
            },
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0]["label"],
            "Email: Re: Globex renewal (Globex Admin <admin@globex.example>)",
        )
        self.assertEqual(
            links[0]["url"],
            "https://mail.google.com/mail/u/0/#inbox/18c8f5a2b3c4d5e6",
        )

    def test_thread_link(self):
        links = extract_links(
            "gmail_get_thread",
            {
                "thread_id": "18c8f5a2b3c4d5e6",
                "subject": "Re: Globex renewal",
                "participants": ["admin@globex.example"],
                "message_count": 2,
                "excerpts": ["admin: Renewal question."],
            },
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["label"], "Email thread: Re: Globex renewal")
        self.assertEqual(
            links[0]["url"],
            "https://mail.google.com/mail/u/0/#inbox/18c8f5a2b3c4d5e6",
        )

    def test_stub_links_are_well_formed_on_a_fake_host(self):
        config = _stub_config()
        links = []
        links += extract_links(
            "gmail_search_messages", search_messages(config, "Acme Corp")
        )
        links += extract_links(
            "gmail_get_thread", get_thread(config, "18c8f5a2b3c4d5e6")
        )
        # 4 search matches + 1 thread link; _gather dedups by URL later.
        self.assertEqual(len(links), 5)
        for link in links:
            self.assertTrue(
                link["url"].startswith(
                    "https://mail.google.invalid/mail/u/0/#inbox/"
                ),
                link["url"],
            )
            self.assertTrue(link["label"])

    def test_no_link_without_thread_id(self):
        self.assertEqual(
            extract_links(
                "gmail_search_messages",
                {
                    "query": "Globex",
                    "matches": [{"subject": "No thread id"}],
                },
            ),
            [],
        )
        self.assertEqual(
            extract_links("gmail_get_thread", {"subject": "No thread id"}),
            [],
        )


class TestGmailRegistryAndServer(unittest.TestCase):
    def test_gmail_tools_registered(self):
        config = _stub_config()
        config.stub_mode = True
        registry = build_registry(config)
        names = [t.name for t in registry.list_tools()]
        self.assertIn("gmail_search_messages", names)
        self.assertIn("gmail_get_thread", names)
        result = registry.call("gmail_search_messages", query="Acme Corp")
        self.assertEqual(len(result["matches"]), 4)

    def test_gmail_tools_factory(self):
        tools = gmail_tools(_stub_config())
        self.assertEqual(len(tools), 2)
        self.assertEqual(
            sorted(t.name for t in tools),
            ["gmail_get_thread", "gmail_search_messages"],
        )

    def test_mcp_server_exposes_the_two_tools(self):
        tools = asyncio.run(gmail_mcp.list_tools())
        self.assertEqual(
            sorted(t.name for t in tools),
            ["gmail_get_thread", "gmail_search_messages"],
        )


class TestRenewalDeskGathersGmail(unittest.TestCase):
    def test_renewal_desk_gathers_email_and_links_threads(self):
        registry = _stub_registry()
        agent = AGENTS["renewal-desk"]
        draft = agent.run(
            {"account": "Acme Corp"},
            registry,
            StubLLM(),
            TokenBudget(8000),
        )
        self.assertIn("gmail_search_messages", draft.sources)
        self.assertIn("salesforce_list_opportunities", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        labels = [p["label"] for p in draft.proof]
        self.assertIn("Cites email threads", labels)
        gmail_item = [
            p for p in draft.proof if p["label"] == "Cites email threads"
        ][0]
        self.assertTrue(gmail_item["passed"])
        self.assertEqual(len(draft.proof), 7)
        gmail_links = [
            link for link in draft.links if "mail.google" in link["url"]
        ]
        # Four stub matches across three threads dedup to three links.
        self.assertEqual(len(gmail_links), 3)
        for link in gmail_links:
            self.assertTrue(
                link["url"].startswith(
                    "https://mail.google.invalid/mail/u/0/#inbox/"
                )
            )

    def test_gather_never_shows_the_model_a_url(self):
        registry = _stub_registry()
        context, _sources, _links = _gather(
            registry,
            [("gmail_search_messages", {"query": "Acme Corp", "limit": 5})],
            TokenBudget(8000),
        )
        for rendered in context.values():
            self.assertNotIn("http", rendered)


class TestGmailDashboardStatus(unittest.TestCase):
    def test_no_credentials_means_stub(self):
        statuses = {s["name"]: s["status"] for s in source_statuses(Config())}
        self.assertEqual(statuses["Gmail"], "stub")

    def test_oauth_triple_means_live(self):
        config = Config(
            gmail_client_id="client-id",
            gmail_client_secret="client-secret",
            gmail_refresh_token="refresh-token",
        )
        statuses = {s["name"]: s["status"] for s in source_statuses(config)}
        self.assertEqual(statuses["Gmail"], "live")

    def test_partial_oauth_means_stub(self):
        # Any missing piece of the triple is stub: a half-configured
        # OAuth flow must not look live.
        config = Config(
            gmail_client_id="client-id",
            gmail_refresh_token="refresh-token",
        )
        statuses = {s["name"]: s["status"] for s in source_statuses(config)}
        self.assertEqual(statuses["Gmail"], "stub")

    def test_stub_mode_overrides_credentials(self):
        config = Config(
            gmail_client_id="client-id",
            gmail_client_secret="client-secret",
            gmail_refresh_token="refresh-token",
            stub_mode=True,
        )
        statuses = {s["name"]: s["status"] for s in source_statuses(config)}
        self.assertEqual(statuses["Gmail"], "stub")


class TestGmailConfig(unittest.TestCase):
    def test_gmail_configured(self):
        self.assertFalse(Config().gmail_configured())
        self.assertFalse(Config(gmail_client_id="id").gmail_configured())
        self.assertFalse(
            Config(
                gmail_client_id="id", gmail_client_secret="secret"
            ).gmail_configured()
        )
        self.assertTrue(
            Config(
                gmail_client_id="id",
                gmail_client_secret="secret",
                gmail_refresh_token="token",
            ).gmail_configured()
        )
        self.assertFalse(
            Config(
                gmail_client_id="id",
                gmail_client_secret="secret",
                gmail_refresh_token="token",
                stub_mode=True,
            ).gmail_configured()
        )


if __name__ == "__main__":
    unittest.main()
