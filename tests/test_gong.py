"""Tests for the Gong connector (v0.8.2).

Pins: the stub tools (call lists, generated summaries, transcript
search), the live-shaped link extraction (Gong call URLs built from
call ids, never from the model), registry wiring, voice-of-customer
gathering from Gong, and the dashboard source status moving from
planned to live/stub.
"""

import asyncio
import unittest

from connectors.gong.server import mcp as gong_mcp
from connectors.gong.tools import (
    get_call_summary,
    gong_tools,
    list_calls,
    search_transcripts,
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


class TestGongStubTools(unittest.TestCase):
    def test_list_calls_returns_calls_with_ids(self):
        result = list_calls(_stub_config(), "Acme Corp")
        self.assertTrue(result["_stub"])
        self.assertEqual(len(result["calls"]), 3)
        for call in result["calls"]:
            self.assertTrue(call["id"])
            self.assertTrue(call["title"])
            self.assertTrue(call["started"])
            self.assertTrue(call["participants"])

    def test_get_call_summary_returns_generated_summary(self):
        summary = get_call_summary(_stub_config(), "7782342274025937895")
        self.assertTrue(summary["_stub"])
        self.assertEqual(summary["call_id"], "7782342274025937895")
        self.assertTrue(summary["brief"])
        self.assertTrue(summary["key_points"])
        self.assertTrue(summary["topics"])
        self.assertTrue(summary["call_outcome"])

    def test_get_call_summary_unknown_id_is_an_error(self):
        result = get_call_summary(_stub_config(), "0000000000000000000")
        self.assertIn("error", result)

    def test_search_transcripts_returns_short_excerpts(self):
        result = search_transcripts(_stub_config(), "Acme Corp")
        self.assertTrue(result["_stub"])
        self.assertEqual(len(result["matches"]), 3)
        for match in result["matches"]:
            self.assertTrue(match["call_id"])
            self.assertTrue(match["timestamp"])
            self.assertLessEqual(len(match["excerpt"]), 200)

    def test_search_transcripts_caps_matches(self):
        result = search_transcripts(_stub_config(), "Acme Corp", limit=2)
        self.assertEqual(len(result["matches"]), 2)

    def test_search_transcripts_no_match_is_empty(self):
        result = search_transcripts(_stub_config(), "nonexistent keyword xyz")
        self.assertEqual(result["matches"], [])

    def test_no_full_transcript_anywhere(self):
        # The stub never returns a raw transcript: summaries and excerpts
        # only, so nothing here can blow up a prompt.
        calls = list_calls(_stub_config(), "Acme Corp")
        self.assertNotIn("transcript", calls)
        summary = get_call_summary(_stub_config(), "7782342274025937895")
        self.assertNotIn("transcript", summary)
        self.assertLess(len(summary["brief"]), 2000)


class TestGongLinks(unittest.TestCase):
    def test_list_calls_links_live_shaped(self):
        links = extract_links(
            "gong_list_calls",
            {
                "account": "Globex Inc",
                "calls": [
                    {
                        "id": "7782342274025937895",
                        "title": "Globex - Discovery",
                        "started": "2026-11-13T15:00:00Z",
                        "duration_seconds": 1800,
                        "participants": ["jane-csm"],
                    },
                    {"title": "No id here"},
                ],
            },
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["label"], "Gong call: Globex - Discovery")
        self.assertEqual(
            links[0]["url"],
            "https://app.gong.io/call?id=7782342274025937895",
        )

    def test_summary_and_search_links(self):
        summary_links = extract_links(
            "gong_get_call_summary",
            {
                "call_id": "7782342274025937895",
                "title": "Globex - Discovery",
                "brief": "Short.",
            },
        )
        self.assertEqual(len(summary_links), 1)
        self.assertEqual(
            summary_links[0]["url"],
            "https://app.gong.io/call?id=7782342274025937895",
        )
        search_links = extract_links(
            "gong_search_transcripts",
            {
                "query": "SSO",
                "matches": [
                    {
                        "call_id": "7782342274025937896",
                        "title": "Globex - Support",
                        "timestamp": "08:31",
                        "speaker": "admin",
                        "excerpt": "SSO fails.",
                    }
                ],
            },
        )
        self.assertEqual(len(search_links), 1)
        self.assertEqual(
            search_links[0]["label"], "Gong call: Globex - Support @ 08:31"
        )
        self.assertEqual(
            search_links[0]["url"],
            "https://app.gong.io/call?id=7782342274025937896",
        )

    def test_stub_links_are_well_formed_on_a_fake_host(self):
        config = _stub_config()
        links = []
        links += extract_links("gong_list_calls", list_calls(config, "Acme Corp"))
        links += extract_links(
            "gong_get_call_summary",
            get_call_summary(config, "7782342274025937895"),
        )
        links += extract_links(
            "gong_search_transcripts",
            search_transcripts(config, "Acme Corp"),
        )
        # 3 calls + 1 summary + 3 transcript matches (extract_links
        # returns one link per match; _gather dedups by URL later).
        self.assertEqual(len(links), 7)
        for link in links:
            self.assertTrue(
                link["url"].startswith("https://app.gong.invalid/call?id="),
                link["url"],
            )
            self.assertTrue(link["label"])

    def test_no_link_without_call_id(self):
        self.assertEqual(
            extract_links(
                "gong_list_calls",
                {"account": "Globex Inc", "calls": [{"title": "No id"}]},
            ),
            [],
        )
        self.assertEqual(
            extract_links("gong_get_call_summary", {"title": "No id"}),
            [],
        )


class TestGongRegistryAndServer(unittest.TestCase):
    def test_gong_tools_registered(self):
        config = _stub_config()
        config.stub_mode = True
        registry = build_registry(config)
        names = [t.name for t in registry.list_tools()]
        self.assertIn("gong_list_calls", names)
        self.assertIn("gong_get_call_summary", names)
        self.assertIn("gong_search_transcripts", names)
        result = registry.call("gong_list_calls", account="Acme Corp")
        self.assertEqual(len(result["calls"]), 3)

    def test_gong_tools_factory(self):
        tools = gong_tools(_stub_config())
        self.assertEqual(len(tools), 3)
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "gong_get_call_summary",
                "gong_list_calls",
                "gong_search_transcripts",
            ],
        )

    def test_mcp_server_exposes_the_three_tools(self):
        tools = asyncio.run(gong_mcp.list_tools())
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "gong_get_call_summary",
                "gong_list_calls",
                "gong_search_transcripts",
            ],
        )


class TestVoiceOfCustomerGathersGong(unittest.TestCase):
    def test_voc_gathers_transcripts_and_links_calls(self):
        registry = _stub_registry()
        agent = AGENTS["voice-of-customer"]
        draft = agent.run(
            {"account": "Acme Corp"},
            registry,
            StubLLM(),
            TokenBudget(8000),
        )
        self.assertIn("gong_search_transcripts", draft.sources)
        self.assertIn("salesforce_list_cases", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        labels = [p["label"] for p in draft.proof]
        self.assertIn("Cites Gong call transcripts", labels)
        gong_item = [
            p for p in draft.proof if p["label"] == "Cites Gong call transcripts"
        ][0]
        self.assertTrue(gong_item["passed"])
        self.assertEqual(len(draft.proof), 8)
        gong_links = [
            link for link in draft.links if "gong" in link["url"]
        ]
        # Two distinct stub calls surface in the transcript matches.
        self.assertEqual(len(gong_links), 2)
        for link in gong_links:
            self.assertTrue(
                link["url"].startswith("https://app.gong.invalid/call?id=")
            )

    def test_gather_never_shows_the_model_a_url(self):
        registry = _stub_registry()
        context, _sources, _links = _gather(
            registry,
            [("gong_search_transcripts", {"query": "Acme Corp", "limit": 5})],
            TokenBudget(8000),
        )
        for rendered in context.values():
            self.assertNotIn("http", rendered)


class TestGongDashboardStatus(unittest.TestCase):
    def test_no_credentials_means_stub(self):
        statuses = {s["name"]: s["status"] for s in source_statuses(Config())}
        self.assertEqual(statuses["Gong"], "stub")

    def test_token_means_live(self):
        config = Config(gong_access_token="gong-token")
        statuses = {s["name"]: s["status"] for s in source_statuses(config)}
        self.assertEqual(statuses["Gong"], "live")

    def test_stub_mode_overrides_token(self):
        config = Config(gong_access_token="gong-token", stub_mode=True)
        statuses = {s["name"]: s["status"] for s in source_statuses(config)}
        self.assertEqual(statuses["Gong"], "stub")


class TestGongConfig(unittest.TestCase):
    def test_gong_configured(self):
        self.assertFalse(Config().gong_configured())
        self.assertTrue(Config(gong_access_token="tok").gong_configured())
        self.assertFalse(
            Config(gong_access_token="tok", stub_mode=True).gong_configured()
        )
        self.assertEqual(
            Config().gong_api_base_url, "https://api.gong.io/v2"
        )


if __name__ == "__main__":
    unittest.main()
