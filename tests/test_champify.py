"""Tests for the Champify connector (v0.8.8).

Pins: the stub tools (champion lists, job changes, champion detail),
the live-shaped link extraction (Champify champion URLs built from
champion ids, never from the model), registry wiring, advocate-finder
gathering from Champify, and the dashboard source status.
"""

import asyncio
import unittest

from connectors.champify.server import mcp as champify_mcp
from connectors.champify.tools import (
    champify_tools,
    get_champion,
    list_champions,
    list_job_changes,
)
from harness import AGENTS, Config, TokenBudget
from harness.agents import extract_links
from harness.config import Config as _Config
from harness.dashboard import source_statuses
from harness.llm import StubLLM
from harness.run import build_registry

from tests.test_agents import _stub_registry


def _stub_config() -> _Config:
    """A config with no credentials: stub mode for every connector."""
    return _Config()


class TestChampifyStubTools(unittest.TestCase):
    def test_list_champions_returns_champions_with_ids(self):
        result = list_champions(_stub_config(), "Acme Corp")
        self.assertTrue(result["_stub"])
        self.assertEqual(len(result["champions"]), 4)
        for champion in result["champions"]:
            self.assertTrue(champion["id"])
            self.assertTrue(champion["name"])
            self.assertTrue(champion["role"])
            self.assertTrue(champion["company"])
            self.assertTrue(champion["status"])

    def test_list_job_changes_returns_moves_with_both_sides(self):
        result = list_job_changes(_stub_config(), "Acme Corp")
        self.assertTrue(result["_stub"])
        self.assertEqual(len(result["job_changes"]), 3)
        for change in result["job_changes"]:
            self.assertTrue(change["champion_id"])
            self.assertTrue(change["name"])
            self.assertTrue(change["previous_company"])
            self.assertTrue(change["new_company"])
            self.assertTrue(change["changed_at"])

    def test_job_changes_include_a_departure(self):
        # Priya Nair left Acme for Globex: the departure signal
        # advocate-finder is built to flag.
        result = list_job_changes(_stub_config(), "Acme Corp")
        departed = [
            c
            for c in result["job_changes"]
            if c["previous_company"] == "Acme Corp"
            and c["new_company"] != "Acme Corp"
        ]
        self.assertEqual(len(departed), 2)
        self.assertEqual(departed[0]["name"], "Priya Nair")

    def test_get_champion_returns_detail_with_job_history(self):
        detail = get_champion(_stub_config(), "chp_7f3a93")
        self.assertTrue(detail["_stub"])
        self.assertEqual(detail["id"], "chp_7f3a93")
        self.assertEqual(detail["name"], "Priya Nair")
        self.assertEqual(detail["company"], "Globex Inc")
        self.assertEqual(detail["status"], "moved")
        self.assertEqual(len(detail["job_history"]), 2)
        self.assertEqual(
            detail["job_history"][0]["company"], "Acme Corp"
        )

    def test_get_champion_unknown_id_is_an_error(self):
        result = get_champion(_stub_config(), "chp_000000")
        self.assertIn("error", result)


class TestChampifyLinks(unittest.TestCase):
    def test_list_champions_links_live_shaped(self):
        links = extract_links(
            "champify_list_champions",
            {
                "account": "Globex Inc",
                "champions": [
                    {
                        "id": "chp_7f3a91",
                        "name": "Dana Reyes",
                        "role": "VP Operations",
                        "company": "Globex Inc",
                        "status": "active",
                    },
                    {"name": "No id here"},
                ],
            },
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["label"], "Champify champion: Dana Reyes")
        self.assertEqual(
            links[0]["url"],
            "https://app.champify.io/champions/chp_7f3a91",
        )

    def test_job_change_and_detail_links(self):
        change_links = extract_links(
            "champify_list_job_changes",
            {
                "account": "Acme Corp",
                "job_changes": [
                    {
                        "champion_id": "chp_7f3a93",
                        "name": "Priya Nair",
                        "previous_company": "Acme Corp",
                        "new_company": "Globex Inc",
                        "changed_at": "2026-11-02",
                    }
                ],
            },
        )
        self.assertEqual(len(change_links), 1)
        self.assertEqual(
            change_links[0]["label"],
            "Champify job change: Priya Nair (Acme Corp -> Globex Inc)",
        )
        self.assertEqual(
            change_links[0]["url"],
            "https://app.champify.io/champions/chp_7f3a93",
        )
        detail_links = extract_links(
            "champify_get_champion",
            {
                "id": "chp_7f3a91",
                "name": "Dana Reyes",
                "role": "VP Operations",
                "company": "Acme Corp",
                "status": "active",
            },
        )
        self.assertEqual(len(detail_links), 1)
        self.assertEqual(
            detail_links[0]["url"],
            "https://app.champify.io/champions/chp_7f3a91",
        )

    def test_stub_links_are_well_formed_on_a_fake_host(self):
        config = _stub_config()
        links = []
        links += extract_links(
            "champify_list_champions", list_champions(config, "Acme Corp")
        )
        links += extract_links(
            "champify_list_job_changes",
            list_job_changes(config, "Acme Corp"),
        )
        links += extract_links(
            "champify_get_champion", get_champion(config, "chp_7f3a91")
        )
        # 4 champions + 3 job changes + 1 detail.
        self.assertEqual(len(links), 8)
        for link in links:
            self.assertTrue(
                link["url"].startswith(
                    "https://app.champify.invalid/champions/"
                ),
                link["url"],
            )
            self.assertTrue(link["label"])

    def test_no_link_without_champion_id(self):
        self.assertEqual(
            extract_links(
                "champify_list_champions",
                {"account": "Acme Corp", "champions": [{"name": "No id"}]},
            ),
            [],
        )
        self.assertEqual(
            extract_links("champify_get_champion", {"name": "No id"}),
            [],
        )


class TestChampifyRegistryAndServer(unittest.TestCase):
    def test_champify_tools_registered(self):
        config = _stub_config()
        config.stub_mode = True
        registry = build_registry(config)
        names = [t.name for t in registry.list_tools()]
        self.assertIn("champify_list_champions", names)
        self.assertIn("champify_list_job_changes", names)
        self.assertIn("champify_get_champion", names)
        result = registry.call(
            "champify_list_job_changes", account="Acme Corp"
        )
        self.assertEqual(len(result["job_changes"]), 3)

    def test_champify_tools_factory(self):
        tools = champify_tools(_stub_config())
        self.assertEqual(len(tools), 3)
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "champify_get_champion",
                "champify_list_champions",
                "champify_list_job_changes",
            ],
        )

    def test_mcp_server_exposes_the_three_tools(self):
        tools = asyncio.run(champify_mcp.list_tools())
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "champify_get_champion",
                "champify_list_champions",
                "champify_list_job_changes",
            ],
        )


class TestChampifyAdvocateFinder(unittest.TestCase):
    def test_advocate_finder_gathers_job_changes(self):
        agent = AGENTS["advocate-finder"]
        draft = agent.run(
            {"account": "Acme Corp"},
            _stub_registry(),
            StubLLM(),
            TokenBudget(8000),
        )
        self.assertIn("champify_list_job_changes", draft.sources)
        self.assertTrue(draft.proof_passed)
        # The checklist gains the Champify item: 6 -> 7.
        self.assertEqual(len(draft.proof), 7)
        self.assertTrue(
            any(
                link["url"].startswith(
                    "https://app.champify.invalid/champions/"
                )
                for link in draft.links
            )
        )


class TestChampifyDashboardStatus(unittest.TestCase):
    def test_no_credentials_means_stub(self):
        statuses = {
            s["name"]: s["status"] for s in source_statuses(Config())
        }
        self.assertEqual(statuses["Champify"], "stub")

    def test_credentials_mean_live(self):
        config = Config(champify_api_key="chp_test_key")
        statuses = {
            s["name"]: s["status"] for s in source_statuses(config)
        }
        self.assertEqual(statuses["Champify"], "live")

    def test_stub_mode_overrides_credentials(self):
        config = Config(champify_api_key="chp_test_key", stub_mode=True)
        statuses = {
            s["name"]: s["status"] for s in source_statuses(config)
        }
        self.assertEqual(statuses["Champify"], "stub")


if __name__ == "__main__":
    unittest.main()
