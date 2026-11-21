"""Tests for the HubSpot connector (v0.8.3).

Pins: the stub tools (company summary, open deals, open tickets), the
live-shaped link extraction (HubSpot record URLs built from portal id
and record ids, never from the model), registry wiring and the CRM
selector (HubSpot when configured and Salesforce is not), renewal-desk
gathering from HubSpot on that path, and the dashboard source status.
"""

import asyncio
import unittest

from connectors.hubspot.server import mcp as hubspot_mcp
from connectors.hubspot.tools import (
    get_company,
    hubspot_tools,
    list_deals,
    list_tickets,
)
from harness import AGENTS, Config, TokenBudget, ToolRegistry
from harness.agents import _gather, extract_links
from harness.config import Config as _Config
from harness.dashboard import source_statuses
from harness.llm import StubLLM
from harness.run import build_registry

from connectors.slack.tools import slack_tools

from connectors.gmail.tools import gmail_tools


def _stub_config() -> _Config:
    """A config with no credentials: stub mode for every connector."""
    return _Config()


def _hubspot_registry() -> ToolRegistry:
    """Registry wired to the HubSpot + Slack stub tools, with the CRM
    selector set to HubSpot: the shape a HubSpot-only shop gets."""
    config = _stub_config()
    config.stub_mode = True
    registry = ToolRegistry(crm="hubspot")
    for tool in hubspot_tools(config):
        registry.register(tool)
    for tool in slack_tools(config):
        registry.register(tool)
    for tool in gmail_tools(config):
        registry.register(tool)
    return registry


class TestHubSpotStubTools(unittest.TestCase):
    def test_get_company_returns_summary_with_ids(self):
        result = get_company(_stub_config(), "Acme Corp")
        self.assertTrue(result["_stub"])
        self.assertEqual(result["name"], "Acme Corp")
        self.assertEqual(result["id"], "10000000001")
        self.assertEqual(result["portal_id"], "0")
        self.assertEqual(result["revenue"], 120000)
        self.assertEqual(result["industry"], "Software")
        self.assertEqual(result["employees"], 450)
        self.assertTrue(result["owner"])

    def test_list_deals_returns_open_deals_with_ids(self):
        result = list_deals(_stub_config(), "Acme Corp")
        self.assertTrue(result["_stub"])
        self.assertEqual(result["portal_id"], "0")
        self.assertEqual(len(result["deals"]), 2)
        for deal in result["deals"]:
            self.assertTrue(deal["id"])
            self.assertTrue(deal["name"])
            self.assertTrue(deal["stage"])
            self.assertTrue(deal["amount"])
            self.assertTrue(deal["close_date"])
            self.assertTrue(deal["type"])

    def test_list_tickets_returns_open_tickets_with_ids(self):
        result = list_tickets(_stub_config(), "Acme Corp")
        self.assertTrue(result["_stub"])
        self.assertEqual(result["portal_id"], "0")
        self.assertEqual(len(result["tickets"]), 2)
        for ticket in result["tickets"]:
            self.assertTrue(ticket["id"])
            self.assertTrue(ticket["subject"])
            self.assertTrue(ticket["status"])
            self.assertTrue(ticket["priority"])
            self.assertIsInstance(ticket["age_days"], int)


class TestHubSpotLinks(unittest.TestCase):
    def test_company_link_live_shaped(self):
        links = extract_links(
            "hubspot_get_company",
            {
                "name": "Globex Inc",
                "id": "10000000099",
                "portal_id": "123456789",
            },
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["label"], "Company: Globex Inc")
        self.assertEqual(
            links[0]["url"],
            "https://app.hubspot.com/contacts/123456789/company/10000000099",
        )

    def test_deal_and_ticket_links_live_shaped(self):
        deals = extract_links(
            "hubspot_list_deals",
            {
                "company": "Globex Inc",
                "portal_id": "123456789",
                "deals": [
                    {"name": "Globex - Renewal FY27", "id": "20000000099"},
                    {"name": "No id here"},
                ],
            },
        )
        self.assertEqual(len(deals), 1)
        self.assertEqual(
            deals[0]["url"],
            "https://app.hubspot.com/contacts/123456789/deal/20000000099",
        )
        tickets = extract_links(
            "hubspot_list_tickets",
            {
                "company": "Globex Inc",
                "portal_id": "123456789",
                "tickets": [
                    {"subject": "SSO login fails", "id": "30000000099"},
                ],
            },
        )
        self.assertEqual(len(tickets), 1)
        self.assertEqual(
            tickets[0]["url"],
            "https://app.hubspot.com/contacts/123456789/ticket/30000000099",
        )

    def test_stub_links_use_the_invalid_host(self):
        links = extract_links("hubspot_get_company", get_company(_stub_config(), "Acme Corp"))
        self.assertEqual(len(links), 1)
        self.assertTrue(
            links[0]["url"].startswith(
                "https://app.hubspot.invalid/contacts/0/company/"
            )
        )

    def test_no_portal_id_means_no_link(self):
        self.assertEqual(
            extract_links(
                "hubspot_get_company",
                {"name": "Globex Inc", "id": "10000000099"},
            ),
            [],
        )
        self.assertEqual(
            extract_links(
                "hubspot_list_deals",
                {
                    "company": "Globex Inc",
                    "deals": [{"name": "Globex - Renewal", "id": "20000000099"}],
                },
            ),
            [],
        )


class TestHubSpotRegistryAndServer(unittest.TestCase):
    def test_hubspot_tools_registered(self):
        config = _stub_config()
        config.stub_mode = True
        registry = build_registry(config)
        names = [t.name for t in registry.list_tools()]
        self.assertIn("hubspot_get_company", names)
        self.assertIn("hubspot_list_deals", names)
        self.assertIn("hubspot_list_tickets", names)
        result = registry.call("hubspot_get_company", company="Acme Corp")
        self.assertEqual(result["name"], "Acme Corp")

    def test_hubspot_tools_factory(self):
        tools = hubspot_tools(_stub_config())
        self.assertEqual(len(tools), 3)
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "hubspot_get_company",
                "hubspot_list_deals",
                "hubspot_list_tickets",
            ],
        )

    def test_mcp_server_exposes_the_three_tools(self):
        tools = asyncio.run(hubspot_mcp.list_tools())
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "hubspot_get_company",
                "hubspot_list_deals",
                "hubspot_list_tickets",
            ],
        )


class TestCrmSelector(unittest.TestCase):
    def test_default_registry_is_salesforce(self):
        self.assertEqual(ToolRegistry().crm, "salesforce")

    def test_hubspot_only_config_selects_hubspot(self):
        config = _Config(hubspot_access_token="pat-test-token")
        self.assertEqual(build_registry(config).crm, "hubspot")

    def test_both_configured_selects_salesforce(self):
        config = _Config(
            salesforce_instance_url="https://x.my.salesforce.com",
            salesforce_access_token="token",
            hubspot_access_token="pat-test-token",
        )
        self.assertEqual(build_registry(config).crm, "salesforce")

    def test_stub_mode_selects_salesforce(self):
        config = _Config(hubspot_access_token="pat-test-token", stub_mode=True)
        self.assertEqual(build_registry(config).crm, "salesforce")


class TestRenewalDeskGathersHubSpot(unittest.TestCase):
    def test_renewal_desk_on_the_hubspot_path(self):
        registry = _hubspot_registry()
        agent = AGENTS["renewal-desk"]
        draft = agent.run(
            {"account": "Acme Corp"},
            registry,
            StubLLM(),
            TokenBudget(8000),
        )
        self.assertIn("hubspot_get_company", draft.sources)
        self.assertIn("hubspot_list_deals", draft.sources)
        self.assertIn("hubspot_list_tickets", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        self.assertIn("gmail_search_messages", draft.sources)
        self.assertNotIn("salesforce_get_account", draft.sources)
        self.assertNotIn("salesforce_list_opportunities", draft.sources)
        self.assertNotIn("salesforce_list_cases", draft.sources)
        self.assertIn("Acme Corp", draft.text)
        labels = [p["label"] for p in draft.proof]
        self.assertIn("Names the open renewal deal", labels)
        self.assertIn("Names open support tickets", labels)
        self.assertIn("Cites email threads", labels)
        self.assertEqual(len(draft.proof), 6)
        self.assertTrue(all(p["passed"] for p in draft.proof))
        hubspot_links = [
            link for link in draft.links if "hubspot" in link["url"]
        ]
        # Company + 2 deals + 2 tickets, all on the stub host.
        self.assertEqual(len(hubspot_links), 5)
        for link in hubspot_links:
            self.assertTrue(
                link["url"].startswith("https://app.hubspot.invalid/contacts/0/")
            )

    def test_gather_never_shows_the_model_a_url(self):
        registry = _hubspot_registry()
        context, _sources, _links = _gather(
            registry,
            [("hubspot_get_company", {"company": "Acme Corp"})],
            TokenBudget(8000),
        )
        for rendered in context.values():
            self.assertNotIn("http", rendered)


class TestHubSpotDashboard(unittest.TestCase):
    def test_source_statuses_include_hubspot_stub(self):
        statuses = source_statuses(_stub_config())
        hubspot = [s for s in statuses if s["name"] == "HubSpot"][0]
        self.assertEqual(hubspot["status"], "stub")

    def test_source_statuses_include_hubspot_live(self):
        config = _Config(hubspot_access_token="pat-test-token")
        statuses = source_statuses(config)
        hubspot = [s for s in statuses if s["name"] == "HubSpot"][0]
        self.assertEqual(hubspot["status"], "live")
