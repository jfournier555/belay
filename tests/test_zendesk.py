"""Tests for the Zendesk connector (v0.8.6).

Pins: the stub tools (open ticket lists, ticket search, ticket
summaries), the live-shaped link extraction (agent ticket URLs built
from subdomain + ticket id, never written by the model), registry
wiring, voice-of-customer gathering from Zendesk, and the dashboard
source status.
"""

import asyncio
import unittest

from connectors.zendesk.server import mcp as zendesk_mcp
from connectors.zendesk.tools import (
    get_ticket,
    list_tickets,
    search_tickets,
    zendesk_tools,
)
from harness import AGENTS, Config, TokenBudget
from harness.agents import _gather, extract_links
from harness.config import Config as _Config
from harness.dashboard import source_statuses
from harness.llm import StubLLM
from harness.run import build_registry

from tests.test_agents import _stub_registry


def _stub_config() -> _Config:
    """A config with no credentials: stub mode for every connector."""
    return _Config()


class TestZendeskStubTools(unittest.TestCase):
    def test_list_tickets_returns_open_tickets_with_ids(self):
        result = list_tickets(_stub_config(), "Acme Corp")
        self.assertTrue(result["_stub"])
        self.assertEqual(result["organization"], "Acme Corp")
        self.assertEqual(result["subdomain"], "acme-stub")
        self.assertEqual(len(result["tickets"]), 3)
        for ticket in result["tickets"]:
            self.assertTrue(ticket["id"])
            self.assertTrue(ticket["subject"])
            self.assertTrue(ticket["status"])
            self.assertTrue(ticket["priority"])
            self.assertTrue(ticket["age_days"] >= 0)

    def test_list_tickets_caps_at_limit(self):
        result = list_tickets(_stub_config(), "Acme Corp", limit=2)
        self.assertEqual(len(result["tickets"]), 2)

    def test_get_ticket_returns_summary_and_comment_excerpts(self):
        ticket = get_ticket(_stub_config(), 101)
        self.assertTrue(ticket["_stub"])
        self.assertEqual(ticket["id"], 101)
        self.assertEqual(ticket["subject"], "SSO login fails for new hires")
        self.assertEqual(ticket["status"], "open")
        self.assertEqual(ticket["priority"], "high")
        self.assertTrue(ticket["comments"])
        self.assertLessEqual(len(ticket["comments"]), 3)
        for comment in ticket["comments"]:
            self.assertTrue(comment["author"])
            self.assertLessEqual(len(comment["excerpt"]), 200)

    def test_get_ticket_unknown_id_is_an_error(self):
        result = get_ticket(_stub_config(), 999999)
        self.assertIn("error", result)

    def test_search_tickets_returns_short_excerpts(self):
        result = search_tickets(_stub_config(), "SSO")
        self.assertTrue(result["_stub"])
        self.assertEqual(len(result["matches"]), 1)
        match = result["matches"][0]
        self.assertEqual(match["id"], 101)
        self.assertTrue(match["subject"])
        self.assertLessEqual(len(match["excerpt"]), 200)

    def test_search_tickets_caps_matches(self):
        # "seat" matches the export ticket (description) and the
        # reporting ticket (subject), so the cap is what limits it.
        result = search_tickets(_stub_config(), "seat", limit=1)
        self.assertEqual(len(result["matches"]), 1)

    def test_search_tickets_no_match_is_empty(self):
        result = search_tickets(_stub_config(), "nonexistent keyword xyz")
        self.assertEqual(result["matches"], [])

    def test_no_full_comment_thread_anywhere(self):
        # The stub never returns a full thread: at most three short
        # excerpts per ticket, so nothing here can blow up a prompt.
        ticket = get_ticket(_stub_config(), 101)
        self.assertLessEqual(len(ticket["comments"]), 3)
        for comment in ticket["comments"]:
            self.assertLessEqual(len(comment["excerpt"]), 200)
        searched = search_tickets(_stub_config(), "SSO")
        for match in searched["matches"]:
            self.assertNotIn("comments", match)


class TestZendeskLinks(unittest.TestCase):
    def test_list_tickets_links_live_shaped(self):
        links = extract_links(
            "zendesk_list_tickets",
            {
                "organization": "Globex Inc",
                "subdomain": "globex",
                "tickets": [
                    {
                        "id": 4242,
                        "subject": "Globex - SSO broken",
                        "status": "open",
                        "priority": "high",
                    },
                    {"subject": "No id here"},
                ],
            },
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0]["label"], "Zendesk ticket 4242: Globex - SSO broken"
        )
        self.assertEqual(
            links[0]["url"],
            "https://globex.zendesk.com/agent/tickets/4242",
        )

    def test_search_and_get_ticket_links(self):
        search_links = extract_links(
            "zendesk_search_tickets",
            {
                "query": "SSO",
                "subdomain": "globex",
                "matches": [
                    {
                        "id": 4243,
                        "subject": "Globex - Export timeout",
                        "status": "pending",
                    }
                ],
            },
        )
        self.assertEqual(len(search_links), 1)
        self.assertEqual(
            search_links[0]["url"],
            "https://globex.zendesk.com/agent/tickets/4243",
        )
        ticket_links = extract_links(
            "zendesk_get_ticket",
            {
                "id": 4244,
                "subject": "Globex - Billing question",
                "status": "open",
                "subdomain": "globex",
                "comments": [],
            },
        )
        self.assertEqual(len(ticket_links), 1)
        self.assertEqual(
            ticket_links[0]["url"],
            "https://globex.zendesk.com/agent/tickets/4244",
        )

    def test_stub_links_are_well_formed_on_a_fake_host(self):
        config = _stub_config()
        links = []
        links += extract_links(
            "zendesk_list_tickets", list_tickets(config, "Acme Corp")
        )
        links += extract_links(
            "zendesk_search_tickets", search_tickets(config, "SSO")
        )
        links += extract_links(
            "zendesk_get_ticket", get_ticket(config, 101)
        )
        # 3 list tickets + 1 search match + 1 single ticket.
        self.assertEqual(len(links), 5)
        for link in links:
            self.assertTrue(
                link["url"].startswith(
                    "https://acme-stub.zendesk.invalid/agent/tickets/"
                ),
                link["url"],
            )
            self.assertTrue(link["label"])

    def test_no_link_without_id_or_subdomain(self):
        self.assertEqual(
            extract_links(
                "zendesk_list_tickets",
                {"organization": "Globex Inc", "tickets": []},
            ),
            [],
        )
        self.assertEqual(
            extract_links(
                "zendesk_list_tickets",
                {
                    "organization": "Globex Inc",
                    "tickets": [{"subject": "No id, no subdomain"}],
                },
            ),
            [],
        )
        self.assertEqual(
            extract_links(
                "zendesk_get_ticket",
                {"subject": "No id, no subdomain"},
            ),
            [],
        )


class TestZendeskRegistryAndServer(unittest.TestCase):
    def test_zendesk_tools_registered(self):
        config = _stub_config()
        config.stub_mode = True
        registry = build_registry(config)
        names = [t.name for t in registry.list_tools()]
        self.assertIn("zendesk_list_tickets", names)
        self.assertIn("zendesk_search_tickets", names)
        self.assertIn("zendesk_get_ticket", names)
        result = registry.call(
            "zendesk_list_tickets", organization="Acme Corp"
        )
        self.assertEqual(len(result["tickets"]), 3)

    def test_zendesk_tools_factory(self):
        tools = zendesk_tools(_stub_config())
        self.assertEqual(len(tools), 3)
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "zendesk_get_ticket",
                "zendesk_list_tickets",
                "zendesk_search_tickets",
            ],
        )

    def test_mcp_server_exposes_the_three_tools(self):
        tools = asyncio.run(zendesk_mcp.list_tools())
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "zendesk_get_ticket",
                "zendesk_list_tickets",
                "zendesk_search_tickets",
            ],
        )


class TestVoiceOfCustomerGathersZendesk(unittest.TestCase):
    def test_voc_gathers_tickets_and_links_them(self):
        registry = _stub_registry()
        agent = AGENTS["voice-of-customer"]
        draft = agent.run(
            {"account": "Acme Corp"},
            registry,
            StubLLM(),
            TokenBudget(8000),
        )
        self.assertIn("zendesk_list_tickets", draft.sources)
        self.assertIn("salesforce_list_cases", draft.sources)
        labels = [p["label"] for p in draft.proof]
        self.assertIn("Cites Zendesk tickets", labels)
        zendesk_item = [
            p
            for p in draft.proof
            if p["label"] == "Cites Zendesk tickets"
        ][0]
        self.assertTrue(zendesk_item["passed"])
        self.assertEqual(len(draft.proof), 9)
        zendesk_links = [
            link
            for link in draft.links
            if "zendesk.invalid" in link["url"]
        ]
        self.assertEqual(len(zendesk_links), 3)
        for link in zendesk_links:
            self.assertTrue(
                link["url"].startswith(
                    "https://acme-stub.zendesk.invalid/agent/tickets/"
                )
            )

    def test_gather_never_shows_the_model_a_url(self):
        registry = _stub_registry()
        context, _sources, _links = _gather(
            registry,
            [("zendesk_list_tickets", {"organization": "Acme Corp"})],
            TokenBudget(8000),
        )
        for rendered in context.values():
            self.assertNotIn("http", rendered)


class TestZendeskDashboardStatus(unittest.TestCase):
    def test_no_credentials_means_stub(self):
        statuses = {s["name"]: s["status"] for s in source_statuses(Config())}
        self.assertEqual(statuses["Zendesk"], "stub")

    def test_token_triple_means_live(self):
        config = Config(
            zendesk_subdomain="acme",
            zendesk_email="csm@acme.com",
            zendesk_api_token="token",
        )
        statuses = {s["name"]: s["status"] for s in source_statuses(config)}
        self.assertEqual(statuses["Zendesk"], "live")

    def test_stub_mode_overrides_credentials(self):
        config = Config(
            zendesk_subdomain="acme",
            zendesk_email="csm@acme.com",
            zendesk_api_token="token",
            stub_mode=True,
        )
        statuses = {s["name"]: s["status"] for s in source_statuses(config)}
        self.assertEqual(statuses["Zendesk"], "stub")


class TestZendeskConfig(unittest.TestCase):
    def test_zendesk_configured(self):
        self.assertFalse(Config().zendesk_configured())
        self.assertTrue(
            Config(
                zendesk_subdomain="acme",
                zendesk_email="csm@acme.com",
                zendesk_api_token="token",
            ).zendesk_configured()
        )
        self.assertFalse(
            Config(
                zendesk_subdomain="acme",
                zendesk_email="csm@acme.com",
                stub_mode=True,
            ).zendesk_configured()
        )
        self.assertFalse(
            Config(
                zendesk_subdomain="acme",
                zendesk_email="csm@acme.com",
            ).zendesk_configured()
        )


if __name__ == "__main__":
    unittest.main()
