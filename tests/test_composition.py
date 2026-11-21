"""Tests for composition (v1.1.0).

Agents build on each other's outputs: qbr-builder composes its draft
from renewal-desk, expansion-spotter, and voice-of-customer drafts in
the approval queue instead of re-gathering the same context. The queue
is the only channel between agents; no new store is introduced.

Composition is an optimization, never a hard dependency: with no prior
drafts for the account, qbr-builder gathers directly, exactly as
before. A composed draft carries the deep links from its source
drafts, and the proof checklist gains a composition item checked
against a harness-written provenance line.

These run the full loop with stub tools and the stub LLM, so no network
and no credentials are needed.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from connectors.salesforce.tools import salesforce_tools
from connectors.slack.tools import slack_tools
from harness import AGENTS, ApprovalQueue, TokenBudget, ToolRegistry
from harness.agents import (
    _QBR_SOURCE_AGENTS,
    _compose_sources,
    _prior_drafts,
)
from harness.config import Config
from harness.llm import StubLLM
from harness.run import main


def _stub_registry() -> ToolRegistry:
    """Registry wired to the stub connector tools (no creds needed)."""
    config = Config.from_env()
    config.stub_mode = True
    registry = ToolRegistry()
    for tool in salesforce_tools(config):
        registry.register(tool)
    for tool in slack_tools(config):
        registry.register(tool)
    return registry


def _temp_queue_path() -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    os.unlink(tmp.name)
    return tmp.name


def _seed(tmp_name: str, drafts: list) -> ApprovalQueue:
    """Submit drafts to the queue file; each draft is
    (agent, account, text, links). created_at values are set
    increasing in submission order so 'latest' is unambiguous."""
    queue = ApprovalQueue(tmp_name)
    for agent, account, text, links in drafts:
        queue.submit(
            agent=agent,
            job={"account": account},
            draft=text,
            sources=["salesforce_get_account"],
            links=links,
        )
    with open(tmp_name, "r", encoding="utf-8") as f:
        data = json.load(f)
    for i, entry in enumerate(data):
        entry["created_at"] = "2026-11-21T10:00:%02d+00:00" % i
    with open(tmp_name, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return ApprovalQueue(tmp_name)


def _link(url: str, label: str) -> dict:
    return {"label": label, "url": url}


class TestComposeSources(unittest.TestCase):
    def test_latest_one_per_agent(self):
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "renewal-desk",
                        "Acme Corp",
                        "MARKER OLD renewal draft.",
                        [],
                    ),
                    (
                        "renewal-desk",
                        "Acme Corp",
                        "MARKER NEW renewal draft.",
                        [],
                    ),
                    (
                        "expansion-spotter",
                        "Acme Corp",
                        "MARKER expansion draft.",
                        [],
                    ),
                    (
                        "voice-of-customer",
                        "Acme Corp",
                        "MARKER voc draft.",
                        [],
                    ),
                    (
                        "renewal-desk",
                        "Globex Inc",
                        "MARKER globex draft, wrong account.",
                        [],
                    ),
                ],
            )
            text, _links, agents = _compose_sources(
                {"approvals_file": path}, "Acme Corp", _QBR_SOURCE_AGENTS
            )
            self.assertIn("MARKER NEW renewal draft.", text)
            self.assertIn("MARKER expansion draft.", text)
            self.assertIn("MARKER voc draft.", text)
            self.assertNotIn("MARKER OLD", text)
            self.assertNotIn("globex", text)
            self.assertEqual(
                agents,
                ["renewal-desk", "expansion-spotter", "voice-of-customer"],
            )
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_empty_queue_returns_empty(self):
        path = _temp_queue_path()
        try:
            text, links, agents = _compose_sources(
                {"approvals_file": path}, "Acme Corp", _QBR_SOURCE_AGENTS
            )
            self.assertEqual((text, links, agents), ("", [], []))
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_no_approvals_file_returns_empty(self):
        text, links, agents = _compose_sources(
            {}, "Acme Corp", _QBR_SOURCE_AGENTS
        )
        self.assertEqual((text, links, agents), ("", [], []))

    def test_links_merged_and_deduped(self):
        path = _temp_queue_path()
        try:
            shared = _link("https://stub.salesforce.invalid/006A", "Opp A")
            _seed(
                path,
                [
                    (
                        "renewal-desk",
                        "Acme Corp",
                        "Renewal draft.",
                        [
                            shared,
                            _link(
                                "https://stub.salesforce.invalid/001A",
                                "Case A",
                            ),
                        ],
                    ),
                    (
                        "expansion-spotter",
                        "Acme Corp",
                        "Expansion draft.",
                        [
                            shared,
                            _link(
                                "https://stub.salesforce.invalid/006B",
                                "Opp B",
                            ),
                        ],
                    ),
                ],
            )
            _text, links, _agents = _compose_sources(
                {"approvals_file": path}, "Acme Corp", _QBR_SOURCE_AGENTS
            )
            urls = [link["url"] for link in links]
            self.assertEqual(len(urls), len(set(urls)))
            self.assertEqual(
                urls,
                [
                    "https://stub.salesforce.invalid/006A",
                    "https://stub.salesforce.invalid/001A",
                    "https://stub.salesforce.invalid/006B",
                ],
            )
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_urls_stripped_from_excerpts(self):
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "renewal-desk",
                        "Acme Corp",
                        "See https://example.com/secret for detail.",
                        [],
                    ),
                ],
            )
            text, _links, _agents = _compose_sources(
                {"approvals_file": path}, "Acme Corp", _QBR_SOURCE_AGENTS
            )
            self.assertIn("[link removed]", text)
            self.assertNotIn("https://example.com/secret", text)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_prior_drafts_refactor_keeps_value_finder_behavior(self):
        # _prior_drafts still takes the latest three across its agent
        # set and still names them when none exist.
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "renewal-desk",
                        "Acme Corp",
                        "Renewal health is green.",
                        [],
                    ),
                ],
            )
            self.assertIn(
                "Renewal health is green.",
                _prior_drafts({"approvals_file": path}, "Acme Corp"),
            )
            self.assertIn(
                "renewal-desk, expansion-spotter, or onboarding-guide",
                _prior_drafts({"approvals_file": path}, "Globex Inc"),
            )
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestQBRComposition(unittest.TestCase):
    def setUp(self):
        self.registry = _stub_registry()
        self.llm = StubLLM()
        self.budget = TokenBudget(8000)

    def _seed_three(self, path: str) -> None:
        _seed(
            path,
            [
                (
                    "renewal-desk",
                    "Acme Corp",
                    "MARKER renewal health green, renewal Feb 15.",
                    [
                        _link(
                            "https://stub.salesforce.invalid/001A",
                            "Case A",
                        )
                    ],
                ),
                (
                    "expansion-spotter",
                    "Acme Corp",
                    "MARKER expansion gap: unused seats.",
                    [
                        _link(
                            "https://stub.salesforce.invalid/006B",
                            "Opp B",
                        )
                    ],
                ),
                (
                    "voice-of-customer",
                    "Acme Corp",
                    "MARKER customers want faster exports.",
                    [
                        _link(
                            "https://slack.stub.invalid/msg-1",
                            "Slack message",
                        )
                    ],
                ),
            ],
        )

    def test_composed_uses_prior_drafts_not_tools(self):
        path = _temp_queue_path()
        try:
            self._seed_three(path)
            agent = AGENTS["qbr-builder"]
            draft = agent.run(
                {"account": "Acme Corp", "approvals_file": path},
                self.registry,
                self.llm,
                self.budget,
            )
            # Only the account record is gathered directly; the rest
            # comes from the prior drafts.
            self.assertEqual(draft.sources, ["salesforce_get_account"])
            self.assertIn("MARKER renewal health green", draft.text)
            self.assertIn("MARKER expansion gap", draft.text)
            self.assertIn("MARKER customers want faster exports", draft.text)
            # The source drafts' deep links carry through, plus the
            # account link from the direct gather.
            urls = [link["url"] for link in draft.links]
            self.assertIn("https://stub.salesforce.invalid/001A", urls)
            self.assertIn("https://stub.salesforce.invalid/006B", urls)
            self.assertIn("https://slack.stub.invalid/msg-1", urls)
            self.assertEqual(len(urls), 4)
            self.assertIn(
                "Composed from prior drafts: renewal-desk, "
                "expansion-spotter, voice-of-customer.",
                draft.text,
            )
            # The proof checklist passes end to end, with the extra
            # composition item.
            self.assertTrue(draft.proof_passed)
            self.assertEqual(len(draft.proof), 6)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_composed_partial_sources(self):
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "renewal-desk",
                        "Acme Corp",
                        "MARKER renewal health green.",
                        [],
                    ),
                    (
                        "voice-of-customer",
                        "Acme Corp",
                        "MARKER customers want faster exports.",
                        [],
                    ),
                ],
            )
            agent = AGENTS["qbr-builder"]
            draft = agent.run(
                {"account": "Acme Corp", "approvals_file": path},
                self.registry,
                self.llm,
                self.budget,
            )
            self.assertEqual(draft.sources, ["salesforce_get_account"])
            self.assertIn(
                "Composed from prior drafts: renewal-desk, "
                "voice-of-customer.",
                draft.text,
            )
            self.assertTrue(draft.proof_passed)
            self.assertEqual(len(draft.proof), 6)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_fallback_empty_queue(self):
        path = _temp_queue_path()
        try:
            agent = AGENTS["qbr-builder"]
            draft = agent.run(
                {"account": "Acme Corp", "approvals_file": path},
                self.registry,
                self.llm,
                self.budget,
            )
            # No prior drafts: gathers directly, exactly as before.
            self.assertEqual(
                draft.sources,
                [
                    "salesforce_get_account",
                    "salesforce_list_opportunities",
                    "salesforce_list_cases",
                    "slack_search_messages",
                ],
            )
            self.assertNotIn("Composed from prior", draft.text)
            self.assertTrue(draft.proof_passed)
            self.assertEqual(len(draft.proof), 5)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_fallback_wrong_account_only(self):
        path = _temp_queue_path()
        try:
            _seed(
                path,
                [
                    (
                        "renewal-desk",
                        "Globex Inc",
                        "MARKER globex draft, wrong account.",
                        [],
                    ),
                    (
                        "voice-of-customer",
                        "Globex Inc",
                        "MARKER globex feedback.",
                        [],
                    ),
                ],
            )
            agent = AGENTS["qbr-builder"]
            draft = agent.run(
                {"account": "Acme Corp", "approvals_file": path},
                self.registry,
                self.llm,
                self.budget,
            )
            self.assertNotIn("MARKER globex", draft.text)
            self.assertNotIn("Composed from prior", draft.text)
            self.assertEqual(
                draft.sources,
                [
                    "salesforce_get_account",
                    "salesforce_list_opportunities",
                    "salesforce_list_cases",
                    "slack_search_messages",
                ],
            )
            self.assertTrue(draft.proof_passed)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_fallback_no_approvals_file(self):
        agent = AGENTS["qbr-builder"]
        draft = agent.run(
            {"account": "Acme Corp"},
            self.registry,
            self.llm,
            self.budget,
        )
        self.assertNotIn("Composed from prior", draft.text)
        self.assertEqual(len(draft.proof), 5)
        self.assertTrue(draft.proof_passed)

    def test_cli_composition_end_to_end(self):
        path = _temp_queue_path()
        try:
            self._seed_three(path)
            with mock.patch.dict(
                os.environ, {"HARNESS_APPROVALS_FILE": path}
            ):
                code = main(
                    [
                        "run",
                        "--agent",
                        "qbr-builder",
                        "--account",
                        "Acme Corp",
                        "--stub",
                    ]
                )
            self.assertEqual(code, 0)
            queue = ApprovalQueue(path)
            items = [
                item
                for item in queue.list_all()
                if item.agent == "qbr-builder"
            ]
            self.assertEqual(len(items), 1)
            self.assertIn("MARKER renewal health green", items[0].draft)
            self.assertIn(
                "Composed from prior drafts: renewal-desk, "
                "expansion-spotter, voice-of-customer.",
                items[0].draft,
            )
            self.assertEqual(items[0].sources, ["salesforce_get_account"])
        finally:
            if os.path.exists(path):
                os.unlink(path)


if __name__ == "__main__":
    unittest.main()
