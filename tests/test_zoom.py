"""Tests for the Zoom connector (v0.8.5).

Pins: the stub tools (recording lists, AI summaries, transcript
search), the VTT parser the live transcript search depends on, the
live-shaped link extraction (recording play URLs passed through, never
written by the model), registry wiring, voice-of-customer gathering
from Zoom, and the dashboard source status.
"""

import asyncio
import unittest

from connectors.zoom.server import mcp as zoom_mcp
from connectors.zoom.tools import (
    _parse_vtt,
    _split_speaker,
    get_recording_summary,
    list_recordings,
    search_transcripts,
    zoom_tools,
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


class TestZoomStubTools(unittest.TestCase):
    def test_list_recordings_returns_recordings_with_ids(self):
        result = list_recordings(_stub_config(), "Acme Corp")
        self.assertTrue(result["_stub"])
        self.assertEqual(len(result["recordings"]), 3)
        for rec in result["recordings"]:
            self.assertTrue(rec["id"])
            self.assertTrue(rec["topic"])
            self.assertTrue(rec["start_time"])
            self.assertTrue(rec["play_url"])

    def test_get_recording_summary_returns_ai_summary(self):
        summary = get_recording_summary(_stub_config(), "88123456789")
        self.assertTrue(summary["_stub"])
        self.assertEqual(summary["recording_id"], "88123456789")
        self.assertTrue(summary["summary"])
        self.assertTrue(summary["topics"])
        self.assertTrue(summary["action_items"])
        self.assertLessEqual(len(summary["summary"]), 2000)

    def test_get_recording_summary_unknown_id_is_an_error(self):
        result = get_recording_summary(_stub_config(), "00000000000")
        self.assertIn("error", result)

    def test_search_transcripts_returns_short_excerpts(self):
        result = search_transcripts(_stub_config(), "Acme Corp")
        self.assertTrue(result["_stub"])
        self.assertEqual(len(result["matches"]), 3)
        for match in result["matches"]:
            self.assertTrue(match["recording_id"])
            self.assertTrue(match["timestamp"])
            self.assertTrue(match["play_url"])
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
        recordings = list_recordings(_stub_config(), "Acme Corp")
        self.assertNotIn("transcript", recordings)
        summary = get_recording_summary(_stub_config(), "88123456789")
        self.assertNotIn("transcript", summary)
        self.assertLess(len(summary["summary"]), 2000)


class TestZoomVttParsing(unittest.TestCase):
    def test_parse_vtt_returns_timestamped_cues(self):
        vtt = (
            "WEBVTT\n"
            "\n"
            "00:00:05.000 --> 00:00:10.000\n"
            "Jane CSM: Hello everyone, welcome to the QBR prep.\n"
            "\n"
            "00:12:04.000 --> 00:12:10.000\n"
            "acme-ops-lead: The reporting rollout is live for fifty seats.\n"
            "\n"
            "01:05:30.000 --> 01:05:35.000\n"
            "acme-admin: SSO login fails for new hires again.\n"
        )
        cues = _parse_vtt(vtt)
        self.assertEqual(len(cues), 3)
        # Hours fold into minutes: 01:05:30 becomes 65:30.
        self.assertEqual(cues[0], ("00:05", "Jane CSM: Hello everyone, welcome to the QBR prep."))
        self.assertEqual(cues[1][0], "12:04")
        self.assertEqual(cues[2][0], "65:30")

    def test_parse_vtt_skips_headers_and_sequence_numbers(self):
        vtt = (
            "WEBVTT\n"
            "Kind: captions\n"
            "Language: en\n"
            "\n"
            "1\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "First cue.\n"
            "\n"
            "2\n"
            "00:00:05.000 --> 00:00:08.000\n"
            "Second cue.\n"
        )
        cues = _parse_vtt(vtt)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0][1], "First cue.")
        self.assertEqual(cues[1][1], "Second cue.")

    def test_split_speaker(self):
        self.assertEqual(
            _split_speaker("Jane CSM: Hello there."),
            ("Jane CSM", "Hello there."),
        )
        self.assertEqual(
            _split_speaker("No colon here"),
            ("unknown", "No colon here"),
        )


class TestZoomLinks(unittest.TestCase):
    def test_list_recordings_links_live_shaped(self):
        links = extract_links(
            "zoom_list_recordings",
            {
                "account": "Globex Inc",
                "recordings": [
                    {
                        "id": "88123456789",
                        "topic": "Globex - Discovery",
                        "start_time": "2026-11-13T15:00:00Z",
                        "duration": 1800,
                        "host": "jane-csm",
                        "play_url": "https://zoom.us/rec/play/abc123",
                    },
                    {"topic": "No play URL here"},
                ],
            },
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["label"], "Zoom recording: Globex - Discovery")
        self.assertEqual(links[0]["url"], "https://zoom.us/rec/play/abc123")

    def test_summary_and_search_links(self):
        summary_links = extract_links(
            "zoom_get_recording_summary",
            {
                "recording_id": "88123456789",
                "title": "Globex - Discovery",
                "summary": "Short.",
                "play_url": "https://zoom.us/rec/play/abc123",
            },
        )
        self.assertEqual(len(summary_links), 1)
        self.assertEqual(
            summary_links[0]["url"], "https://zoom.us/rec/play/abc123"
        )
        search_links = extract_links(
            "zoom_search_transcripts",
            {
                "query": "SSO",
                "matches": [
                    {
                        "recording_id": "88123456790",
                        "title": "Globex - Support",
                        "timestamp": "08:31",
                        "speaker": "admin",
                        "excerpt": "SSO fails.",
                        "play_url": "https://zoom.us/rec/play/def456",
                    }
                ],
            },
        )
        self.assertEqual(len(search_links), 1)
        self.assertEqual(
            search_links[0]["label"], "Zoom recording: Globex - Support @ 08:31"
        )
        self.assertEqual(
            search_links[0]["url"], "https://zoom.us/rec/play/def456"
        )

    def test_stub_links_are_well_formed_on_a_fake_host(self):
        config = _stub_config()
        links = []
        links += extract_links(
            "zoom_list_recordings", list_recordings(config, "Acme Corp")
        )
        links += extract_links(
            "zoom_get_recording_summary",
            get_recording_summary(config, "88123456789"),
        )
        links += extract_links(
            "zoom_search_transcripts",
            search_transcripts(config, "Acme Corp"),
        )
        # 3 recordings + 1 summary + 3 transcript matches
        # (extract_links returns one link per match; _gather dedups by
        # URL later).
        self.assertEqual(len(links), 7)
        for link in links:
            self.assertTrue(
                link["url"].startswith("https://zoom.invalid/rec/play/"),
                link["url"],
            )
            self.assertTrue(link["label"])

    def test_no_link_without_play_url(self):
        self.assertEqual(
            extract_links(
                "zoom_list_recordings",
                {"account": "Globex Inc", "recordings": [{"topic": "No URL"}]},
            ),
            [],
        )
        self.assertEqual(
            extract_links("zoom_get_recording_summary", {"title": "No URL"}),
            [],
        )


class TestZoomRegistryAndServer(unittest.TestCase):
    def test_zoom_tools_registered(self):
        config = _stub_config()
        config.stub_mode = True
        registry = build_registry(config)
        names = [t.name for t in registry.list_tools()]
        self.assertIn("zoom_list_recordings", names)
        self.assertIn("zoom_get_recording_summary", names)
        self.assertIn("zoom_search_transcripts", names)
        result = registry.call("zoom_list_recordings", account="Acme Corp")
        self.assertEqual(len(result["recordings"]), 3)

    def test_zoom_tools_factory(self):
        tools = zoom_tools(_stub_config())
        self.assertEqual(len(tools), 3)
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "zoom_get_recording_summary",
                "zoom_list_recordings",
                "zoom_search_transcripts",
            ],
        )

    def test_mcp_server_exposes_the_three_tools(self):
        tools = asyncio.run(zoom_mcp.list_tools())
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "zoom_get_recording_summary",
                "zoom_list_recordings",
                "zoom_search_transcripts",
            ],
        )


class TestVoiceOfCustomerGathersZoom(unittest.TestCase):
    def test_voc_gathers_transcripts_and_links_recordings(self):
        registry = _stub_registry()
        agent = AGENTS["voice-of-customer"]
        draft = agent.run(
            {"account": "Acme Corp"},
            registry,
            StubLLM(),
            TokenBudget(8000),
        )
        self.assertIn("zoom_search_transcripts", draft.sources)
        self.assertIn("gong_search_transcripts", draft.sources)
        self.assertIn("salesforce_list_cases", draft.sources)
        self.assertIn("slack_search_messages", draft.sources)
        labels = [p["label"] for p in draft.proof]
        self.assertIn("Cites Zoom recording transcripts", labels)
        zoom_item = [
            p
            for p in draft.proof
            if p["label"] == "Cites Zoom recording transcripts"
        ][0]
        self.assertTrue(zoom_item["passed"])
        self.assertEqual(len(draft.proof), 8)
        zoom_links = [
            link for link in draft.links if "zoom.invalid" in link["url"]
        ]
        # Two distinct stub recordings surface in the transcript matches.
        self.assertEqual(len(zoom_links), 2)
        for link in zoom_links:
            self.assertTrue(
                link["url"].startswith("https://zoom.invalid/rec/play/")
            )

    def test_gather_never_shows_the_model_a_url(self):
        registry = _stub_registry()
        context, _sources, _links = _gather(
            registry,
            [("zoom_search_transcripts", {"query": "Acme Corp", "limit": 5})],
            TokenBudget(8000),
        )
        for rendered in context.values():
            self.assertNotIn("http", rendered)


class TestZoomDashboardStatus(unittest.TestCase):
    def test_no_credentials_means_stub(self):
        statuses = {s["name"]: s["status"] for s in source_statuses(Config())}
        self.assertEqual(statuses["Zoom"], "stub")

    def test_oauth_triple_means_live(self):
        config = Config(
            zoom_account_id="acc",
            zoom_client_id="cid",
            zoom_client_secret="sec",
        )
        statuses = {s["name"]: s["status"] for s in source_statuses(config)}
        self.assertEqual(statuses["Zoom"], "live")

    def test_stub_mode_overrides_credentials(self):
        config = Config(
            zoom_account_id="acc",
            zoom_client_id="cid",
            zoom_client_secret="sec",
            stub_mode=True,
        )
        statuses = {s["name"]: s["status"] for s in source_statuses(config)}
        self.assertEqual(statuses["Zoom"], "stub")


class TestZoomConfig(unittest.TestCase):
    def test_zoom_configured(self):
        self.assertFalse(Config().zoom_configured())
        self.assertTrue(
            Config(
                zoom_account_id="acc",
                zoom_client_id="cid",
                zoom_client_secret="sec",
            ).zoom_configured()
        )
        self.assertFalse(
            Config(
                zoom_account_id="acc",
                zoom_client_id="cid",
                stub_mode=True,
            ).zoom_configured()
        )
        self.assertFalse(
            Config(
                zoom_account_id="acc",
                zoom_client_id="cid",
            ).zoom_configured()
        )


if __name__ == "__main__":
    unittest.main()
