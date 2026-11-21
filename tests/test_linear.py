"""Tests for the Linear connector (v0.8.7).

Pins: the stub tools (open issue lists, issue search, issue
summaries), the live-shaped link extraction (Linear issue URLs built
from team + identifier, never written by the model), registry
wiring, renewal-desk gathering from Linear, and the dashboard
source status.
"""

import asyncio
import unittest

from connectors.linear.server import mcp as linear_mcp
from connectors.linear.tools import (
    get_issue,
    linear_tools,
    list_issues,
    search_issues,
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


class TestLinearStubTools(unittest.TestCase):
    def test_list_issues_returns_open_issues_with_identifiers(self):
        result = list_issues(_stub_config())
        self.assertTrue(result["_stub"])
        self.assertEqual(result["team"], "ENG")
        self.assertEqual(len(result["issues"]), 3)
        for issue in result["issues"]:
            self.assertTrue(issue["id"])
            self.assertTrue(issue["identifier"].startswith("ENG-"))
            self.assertTrue(issue["title"])
            self.assertTrue(issue["status"])
            self.assertTrue(issue["priority"])
            self.assertEqual(issue["team"], "ENG")
            self.assertTrue(issue["age_days"] >= 0)

    def test_list_issues_caps_at_limit(self):
        result = list_issues(_stub_config(), limit=2)
        self.assertEqual(len(result["issues"]), 2)

    def test_list_issues_filters_by_team(self):
        self.assertEqual(
            len(list_issues(_stub_config(), team="ENG")["issues"]), 3
        )
        self.assertEqual(
            len(list_issues(_stub_config(), team="XXX")["issues"]), 0
        )

    def test_search_issues_returns_short_excerpts(self):
        result = search_issues(_stub_config(), "SSO")
        self.assertTrue(result["_stub"])
        self.assertEqual(result["query"], "SSO")
        self.assertEqual(len(result["matches"]), 1)
        match = result["matches"][0]
        self.assertEqual(match["identifier"], "ENG-142")
        self.assertLessEqual(len(match["excerpt"]), 200)

    def test_search_issues_caps_matches(self):
        result = search_issues(_stub_config(), "e", limit=2)
        self.assertEqual(len(result["matches"]), 2)

    def test_search_issues_no_match_is_empty(self):
        result = search_issues(_stub_config(), "zzzznope")
        self.assertEqual(result["matches"], [])

    def test_get_issue_returns_summary_and_excerpt(self):
        issue = get_issue(_stub_config(), "01J0000000000000000000001")
        self.assertTrue(issue["_stub"])
        self.assertEqual(issue["identifier"], "ENG-142")
        self.assertEqual(issue["title"], "SSO login fails for new hires")
        self.assertEqual(issue["status"], "In Progress")
        self.assertEqual(issue["priority"], "high")
        self.assertEqual(issue["assignee"], "sam-eng")
        self.assertLessEqual(len(issue["description"]), 200)

    def test_get_issue_accepts_identifier(self):
        issue = get_issue(_stub_config(), "ENG-147")
        self.assertEqual(issue["identifier"], "ENG-147")
        self.assertEqual(issue["title"], "Export timeout on large reports")

    def test_get_issue_unknown_id_is_an_error(self):
        result = get_issue(_stub_config(), "ENG-999")
        self.assertIn("error", result)

    def test_no_full_comment_thread_anywhere(self):
        issue = get_issue(_stub_config(), "ENG-142")
        self.assertNotIn("comments", issue)
        self.assertLessEqual(len(issue["description"]), 200)
        for match in search_issues(_stub_config(), "SSO")["matches"]:
            self.assertNotIn("comments", match)
            self.assertLessEqual(len(match["excerpt"]), 200)


class TestLinearLinks(unittest.TestCase):
    def test_list_issues_links_live_shaped(self):
        links = extract_links(
            "linear_list_issues",
            {
                "team": "ENG",
                "issues": [
                    {
                        "id": "01J0000000000000000000009",
                        "identifier": "ENG-200",
                        "title": "Globex - SSO broken",
                        "status": "In Progress",
                        "team": "ENG",
                    },
                    {"title": "No identifier here", "team": "ENG"},
                ],
            },
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0]["label"], "Linear ENG-200: Globex - SSO broken"
        )
        self.assertEqual(
            links[0]["url"], "https://linear.app/ENG/issue/ENG-200"
        )

    def test_search_and_get_issue_links(self):
        search_links = extract_links(
            "linear_search_issues",
            {
                "query": "SSO",
                "team": "ENG",
                "matches": [
                    {
                        "id": "01J0000000000000000000009",
                        "identifier": "ENG-201",
                        "title": "Globex - Export timeout",
                        "team": "ENG",
                    }
                ],
            },
        )
        self.assertEqual(len(search_links), 1)
        self.assertEqual(
            search_links[0]["url"], "https://linear.app/ENG/issue/ENG-201"
        )
        issue_links = extract_links(
            "linear_get_issue",
            {
                "id": "01J0000000000000000000009",
                "identifier": "ENG-202",
                "title": "Globex - Billing question",
                "team": "ENG",
            },
        )
        self.assertEqual(len(issue_links), 1)
        self.assertEqual(
            issue_links[0]["url"], "https://linear.app/ENG/issue/ENG-202"
        )

    def test_stub_links_are_well_formed_on_a_fake_host(self):
        links = extract_links("linear_list_issues", list_issues(_stub_config()))
        self.assertEqual(len(links), 3)
        for link in links:
            self.assertTrue(
                link["url"].startswith("https://linear.invalid/ENG/issue/")
            )

    def test_no_link_without_identifier_or_team(self):
        self.assertEqual(
            extract_links(
                "linear_list_issues",
                {"team": "ENG", "issues": [{"title": "No identifier"}]},
            ),
            [],
        )
        self.assertEqual(
            extract_links(
                "linear_list_issues",
                {
                    "issues": [
                        {"identifier": "ENG-203", "title": "No team"}
                    ]
                },
            ),
            [],
        )


class TestLinearRegistryAndServer(unittest.TestCase):
    def test_linear_tools_registered(self):
        config = _stub_config()
        config.stub_mode = True
        registry = build_registry(config)
        names = [t.name for t in registry.list_tools()]
        self.assertIn("linear_list_issues", names)
        self.assertIn("linear_search_issues", names)
        self.assertIn("linear_get_issue", names)
        result = registry.call("linear_list_issues")
        self.assertEqual(len(result["issues"]), 3)

    def test_linear_tools_factory(self):
        tools = linear_tools(_stub_config())
        self.assertEqual(len(tools), 3)
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "linear_get_issue",
                "linear_list_issues",
                "linear_search_issues",
            ],
        )

    def test_mcp_server_exposes_the_three_tools(self):
        tools = asyncio.run(linear_mcp.list_tools())
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "linear_get_issue",
                "linear_list_issues",
                "linear_search_issues",
            ],
        )

    def test_dashboard_shows_linear_stub_without_credentials(self):
        statuses = {
            s["name"]: s["status"]
            for s in source_statuses(_stub_config())
        }
        self.assertEqual(statuses["Linear"], "stub")

    def test_dashboard_shows_linear_live_with_key(self):
        config = _stub_config()
        config.linear_api_key = "lin_api_testkey"
        statuses = {
            s["name"]: s["status"] for s in source_statuses(config)
        }
        self.assertEqual(statuses["Linear"], "live")


class TestRenewalDeskGathersLinear(unittest.TestCase):
    def test_renewal_desk_gathers_issues_and_links_them(self):
        registry = _stub_registry()
        agent = AGENTS["renewal-desk"]
        draft = agent.run(
            {"account": "Acme Corp"},
            registry,
            StubLLM(),
            TokenBudget(8000),
        )
        self.assertIn("linear_list_issues", draft.sources)
        labels = [p["label"] for p in draft.proof]
        self.assertIn("Cites open engineering issues", labels)
        linear_item = [
            p
            for p in draft.proof
            if p["label"] == "Cites open engineering issues"
        ][0]
        self.assertTrue(linear_item["passed"])
        self.assertEqual(len(draft.proof), 7)
        linear_links = [
            link for link in draft.links if "linear.invalid" in link["url"]
        ]
        self.assertEqual(len(linear_links), 3)
        for link in linear_links:
            self.assertTrue(
                link["url"].startswith("https://linear.invalid/ENG/issue/")
            )

    def test_gather_never_shows_the_model_a_url(self):
        registry = _stub_registry()
        context, _sources, _links = _gather(
            registry,
            [("linear_list_issues", {})],
            TokenBudget(8000),
        )
        rendered = context["linear_list_issues"]
        self.assertNotIn("http", rendered)
        self.assertIn("ENG-142", rendered)
