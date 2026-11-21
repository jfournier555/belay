"""Tests for the deep-link proof fix (v0.8.1).

The harness builds source links from the record ids the tools returned;
the model never writes a URL. These tests pin: the Salesforce Lightning
URL builder (live-shaped and stub), the Slack permalink carry-through
(live-shaped and stub), _gather collecting links, Draft.links
round-tripping through the approval queue, dashboard state carrying
links and no aggregate score, and CLI output showing links and no
aggregate proof score.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from connectors.salesforce.tools import (
    get_account,
    list_cases,
    list_opportunities,
)
from connectors.slack.tools import search_messages
from harness import AGENTS, ApprovalQueue, Config, TokenBudget, ToolRegistry
from harness.agents import Draft, ProofItem, _gather, extract_links
from harness.config import Config as _Config
from harness.llm import StubLLM
from harness.run import main as run_main

from tests.test_agents import _stub_registry


class TestSalesforceLinks(unittest.TestCase):
    def test_account_link_live_shaped(self):
        result = {
            "name": "Globex Inc",
            "id": "001ABC000000001",
            "instance_url": "https://globex.my.salesforce.com",
            "arr": 50000,
        }
        links = extract_links("salesforce_get_account", result)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["label"], "Account: Globex Inc")
        self.assertEqual(
            links[0]["url"],
            "https://globex.my.salesforce.com/lightning/r/Account/001ABC000000001/view",
        )

    def test_opportunity_and_case_links_live_shaped(self):
        base = "https://globex.my.salesforce.com"
        opps = extract_links(
            "salesforce_list_opportunities",
            {
                "account": "Globex Inc",
                "instance_url": base,
                "opportunities": [
                    {"name": "Globex - Renewal FY27", "id": "006ABC000000001"},
                    {"name": "No id here"},
                ],
            },
        )
        self.assertEqual(len(opps), 1)
        self.assertEqual(
            opps[0]["url"],
            base + "/lightning/r/Opportunity/006ABC000000001/view",
        )
        cases = extract_links(
            "salesforce_list_cases",
            {
                "account": "Globex Inc",
                "instance_url": base,
                "cases": [
                    {
                        "number": "00009999",
                        "id": "500ABC000000001",
                        "subject": "Login fails",
                    }
                ],
            },
        )
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["label"], "Case 00009999: Login fails")
        self.assertEqual(
            cases[0]["url"],
            base + "/lightning/r/Case/500ABC000000001/view",
        )

    def test_no_link_without_id_or_instance_url(self):
        # A record without an id yields no link instead of a guessed one.
        self.assertEqual(
            extract_links(
                "salesforce_get_account",
                {"name": "Globex Inc", "instance_url": "https://x.my.salesforce.com"},
            ),
            [],
        )
        # A live-shaped result with no instance URL yields no link: the
        # harness never falls back to a fake host for live data.
        self.assertEqual(
            extract_links(
                "salesforce_get_account",
                {"name": "Globex Inc", "id": "001ABC000000001"},
            ),
            [],
        )

    def test_stub_connector_results_carry_ids_and_instance_url(self):
        config = _Config()
        account = get_account(config, "Acme Corp")
        self.assertEqual(account["id"], "001STUB000000001")
        self.assertEqual(
            account["instance_url"], "https://stub.salesforce.invalid"
        )
        opps = list_opportunities(config, "Acme Corp")
        self.assertTrue(all("id" in o for o in opps["opportunities"]))
        cases = list_cases(config, "Acme Corp")
        self.assertTrue(all("id" in c for c in cases["cases"]))

    def test_stub_links_are_well_formed_on_a_fake_host(self):
        config = _Config()
        links = []
        links += extract_links(
            "salesforce_get_account", get_account(config, "Acme Corp")
        )
        links += extract_links(
            "salesforce_list_opportunities",
            list_opportunities(config, "Acme Corp"),
        )
        links += extract_links(
            "salesforce_list_cases", list_cases(config, "Acme Corp")
        )
        self.assertEqual(len(links), 5)
        for link in links:
            self.assertTrue(
                link["url"].startswith("https://stub.salesforce.invalid/lightning/r/"),
                link["url"],
            )
            self.assertTrue(link["label"])


class TestSlackLinks(unittest.TestCase):
    def test_permalink_carried_through_live_shaped(self):
        links = extract_links(
            "slack_search_messages",
            {
                "query": "Acme Corp",
                "matches": [
                    {
                        "author": "jane-csm",
                        "channel": "#acme-corp",
                        "timestamp": "2026-11-14T15:02:00Z",
                        "permalink": "https://acme.slack.com/archives/C001/p1763125320000100",
                        "excerpt": "SSO again.",
                    },
                    {
                        "author": "nobody",
                        "channel": "#random",
                        "timestamp": "2026-11-14T15:03:00Z",
                        "excerpt": "No permalink, no link.",
                    },
                ],
            },
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0]["url"],
            "https://acme.slack.com/archives/C001/p1763125320000100",
        )
        self.assertEqual(links[0]["label"], "Slack #acme-corp, jane-csm")

    def test_stub_messages_carry_permalinks(self):
        config = _Config()
        result = search_messages(config, "Acme Corp")
        self.assertTrue(all("permalink" in m for m in result["matches"]))
        links = extract_links("slack_search_messages", result)
        self.assertEqual(len(links), 3)
        for link in links:
            self.assertTrue(
                link["url"].startswith("https://stub.slack.invalid/archives/"),
                link["url"],
            )


class TestGatherLinks(unittest.TestCase):
    def test_gather_collects_links_alongside_sources(self):
        registry = _stub_registry()
        calls = [
            ("salesforce_get_account", {"account": "Acme Corp"}),
            ("salesforce_list_opportunities", {"account": "Acme Corp"}),
            ("salesforce_list_cases", {"account": "Acme Corp"}),
            ("slack_search_messages", {"query": "Acme Corp", "limit": 5}),
        ]
        context, sources, links = _gather(registry, calls, TokenBudget(8000))
        self.assertEqual(len(sources), 4)
        # 1 account + 2 opportunities + 2 cases + 3 Slack messages.
        self.assertEqual(len(links), 8)
        urls = [link["url"] for link in links]
        self.assertEqual(len(set(urls)), len(urls))
        # The model never sees a URL: the rendered context has none.
        for rendered in context.values():
            self.assertNotIn("http", rendered)

    def test_agent_run_carries_links_and_links_item_passes(self):
        agent = AGENTS["renewal-desk"]
        draft = agent.run(
            {"account": "Acme Corp"},
            _stub_registry(),
            StubLLM(),
            TokenBudget(8000),
        )
        self.assertEqual(len(draft.links), 10)
        labels = [p["label"] for p in draft.proof]
        self.assertIn("Draft carries source links", labels)
        links_item = [
            p for p in draft.proof if p["label"] == "Draft carries source links"
        ][0]
        self.assertTrue(links_item["passed"])

    def test_links_item_fails_when_no_links_gathered(self):
        item = ProofItem("Draft carries source links", requires_links=True)
        result = item.check(
            Draft(text="A draft.", agent="test", job={}, sources=[], links=[])
        )
        self.assertFalse(result["passed"])
        self.assertIn("no source links", result["detail"])


class TestLinksRoundTrip(unittest.TestCase):
    def test_links_survive_submit_and_reload(self):
        links = [
            {
                "label": "Account: Acme Corp",
                "url": "https://stub.salesforce.invalid/lightning/r/Account/001STUB000000001/view",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "approvals.json")
            queue = ApprovalQueue(path)
            item = queue.submit(
                agent="renewal-desk",
                job={"account": "Acme Corp"},
                draft="A draft.",
                sources=["salesforce_get_account"],
                links=links,
            )
            reloaded = ApprovalQueue(path).get(item.id)
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.links, links)

    def test_old_queue_files_without_links_load_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "approvals.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    [
                        {
                            "id": "appr-old0001",
                            "agent": "briefer",
                            "job": {"account": "Acme Corp"},
                            "draft": "An old draft.",
                            "sources": [],
                            "proof": [],
                            "status": "pending",
                        }
                    ],
                    f,
                )
            queue = ApprovalQueue(path)
            item = queue.get("appr-old0001")
            self.assertIsNotNone(item)
            self.assertEqual(item.links, [])


class TestDashboardLinks(unittest.TestCase):
    def test_state_carries_links_and_no_aggregate_score(self):
        from harness.dashboard import build_state, queue_view

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "approvals.json")
            queue = ApprovalQueue(path)
            queue.submit(
                agent="renewal-desk",
                job={"account": "Acme Corp"},
                draft="A draft.",
                sources=["salesforce_get_account"],
                links=[
                    {
                        "label": "Account: Acme Corp",
                        "url": "https://stub.salesforce.invalid/lightning/r/Account/001STUB000000001/view",
                    }
                ],
                proof=[{"label": "Draft carries source links", "passed": True, "detail": "ok"}],
            )
            view = queue_view(queue)
            self.assertEqual(len(view[0]["links"]), 1)
            self.assertNotIn("proof_passed", view[0])
            self.assertNotIn("proof_total", view[0])
            state = build_state(queue, Config())
            self.assertEqual(len(state["queue"][0]["links"]), 1)


class TestCliLinks(unittest.TestCase):
    def test_cli_shows_links_and_no_aggregate_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "approvals.json")
            env = {"HARNESS_APPROVALS_FILE": path}
            buf = io.StringIO()
            with mock.patch.dict(os.environ, env), redirect_stdout(buf):
                code = run_main(
                    [
                        "run",
                        "--agent",
                        "renewal-desk",
                        "--account",
                        "Acme Corp",
                        "--stub",
                        "--show",
                    ]
                )
            self.assertEqual(code, 0)
            out = buf.getvalue()
            self.assertIn("Source links:", out)
            self.assertIn(
                "https://stub.salesforce.invalid/lightning/r/Account/001STUB000000001/view",
                out,
            )
            self.assertNotIn("Proof checklist:", out)
            self.assertNotRegex(out, r"\d+/\d+ passed")


if __name__ == "__main__":
    unittest.main()
