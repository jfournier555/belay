"""Tests for the book-wide risk-watcher sweep (v0.18.2).

The sweep runs risk-watcher's own signal logic across every account
in the book, ranks the results (severity, then renewal proximity),
and stores them for the Overview page. Severity is harness-computed,
never model prose, so the ranking is tested directly on synthetic
tool results as well as end to end with a temp CSV file. No live
network and no credentials are needed anywhere here.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from unittest import mock

from fastapi.testclient import TestClient

from harness import ApprovalQueue, Config, ToolRegistry
from harness.agents import _risk_signals
from harness.dashboard import build_state, create_app, sweep_view
from harness.registry import Tool
from harness.run import main
from harness.summarizer import TokenBudget
from harness.sweep import (
    SweepStore,
    empty_sweep,
    rank_results,
    run_sweep,
    sweep_account,
    sweep_accounts,
)

TODAY = date(2026, 11, 28)

_CSV_HEADER = (
    "name,domain,arr,currency,renewal_date,health,owner,industry,"
    "employees,contacts,open_cases,last_touch,notes,opp_name,opp_stage,"
    "opp_amount,opp_close_date,opp_type,cases\n"
)


def _write_csv(path: str, rows: list[str]) -> str:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(_CSV_HEADER)
        for row in rows:
            f.write(row + "\n")
    return path


def _fake_registry(per_account: dict) -> ToolRegistry:
    """A registry whose nine gather tools return per-account data.

    `per_account` maps an account name to {"account": {...},
    "opportunities": [...], "cases": [...], "tickets": [...],
    "issues": [...], "matches": [...]}. Anything missing is empty.
    """
    registry = ToolRegistry()

    def data_for(kwargs, key):
        name = (
            kwargs.get("account")
            or kwargs.get("query")
            or kwargs.get("organization")
            or ""
        )
        return per_account.get(name, {}).get(key)

    handlers = {
        "salesforce_get_account": lambda **kw: data_for(kw, "account")
        or {"name": kw.get("account", ""), "renewal_date": ""},
        "salesforce_list_opportunities": lambda **kw: {
            "opportunities": data_for(kw, "opportunities") or []
        },
        "salesforce_list_cases": lambda **kw: {
            "cases": data_for(kw, "cases") or []
        },
        "gmail_search_messages": lambda **kw: {
            "matches": data_for(kw, "matches") or []
        },
        "gong_search_transcripts": lambda **kw: {
            "matches": data_for(kw, "matches") or []
        },
        "zoom_search_transcripts": lambda **kw: {
            "matches": data_for(kw, "matches") or []
        },
        "zendesk_list_tickets": lambda **kw: {
            "tickets": data_for(kw, "tickets") or []
        },
        "linear_list_issues": lambda **kw: {
            "issues": data_for(kw, "issues") or []
        },
        "slack_search_messages": lambda **kw: {
            "matches": data_for(kw, "matches") or []
        },
    }
    for name, handler in handlers.items():
        registry.register(
            Tool(name=name, description="Fake %s." % name, handler=handler)
        )
    return registry


def _config(tmp: str, csv_path: str | None = None) -> Config:
    return Config(
        approvals_file=os.path.join(tmp, "approvals.json"),
        memory_file=os.path.join(tmp, "memory.json"),
        schedules_file=os.path.join(tmp, "schedules.json"),
        mcp_servers_file=os.path.join(tmp, "mcp-servers.json"),
        escalations_file=os.path.join(tmp, "escalations.json"),
        sweep_file=os.path.join(tmp, "sweep.json"),
        csv_accounts_file=csv_path,
    )


class TestRankResults(unittest.TestCase):
    def test_severity_order_is_high_medium_low_none(self):
        rows = [
            {"account": "None Co", "severity": "none", "days_to_renewal": 5},
            {"account": "Low Co", "severity": "low", "days_to_renewal": 5},
            {"account": "High Co", "severity": "high", "days_to_renewal": 90},
            {"account": "Med Co", "severity": "medium", "days_to_renewal": 5},
        ]
        ranked = rank_results(rows)
        self.assertEqual(
            [row["account"] for row in ranked],
            ["High Co", "Med Co", "Low Co", "None Co"],
        )

    def test_same_severity_sorts_by_renewal_proximity(self):
        rows = [
            {"account": "Later", "severity": "high", "days_to_renewal": 60},
            {"account": "No date", "severity": "high", "days_to_renewal": None},
            {"account": "Sooner", "severity": "high", "days_to_renewal": 10},
        ]
        ranked = rank_results(rows)
        self.assertEqual(
            [row["account"] for row in ranked],
            ["Sooner", "Later", "No date"],
        )


class TestSweepAccount(unittest.TestCase):
    def setUp(self):
        self.registry = _fake_registry(
            {
                "Case Corp": {
                    "account": {"name": "Case Corp", "renewal_date": "2027-03-01"},
                    "cases": [
                        {
                            "number": "0001",
                            "subject": "SSO login fails",
                            "status": "Open",
                            "priority": "High",
                            "age_days": 6,
                        }
                    ],
                },
                "Stalled Corp": {
                    "account": {"name": "Stalled Corp", "renewal_date": "2026-12-01"},
                    "opportunities": [
                        {
                            "name": "Stalled Corp - Renewal",
                            "type": "Renewal",
                            "stage": "Negotiation",
                            "amount": 50000,
                            "close_date": "2026-11-01",
                        }
                    ],
                },
                "Closing Corp": {
                    "account": {"name": "Closing Corp", "renewal_date": "2026-12-10"},
                    "opportunities": [
                        {
                            "name": "Closing Corp - Renewal",
                            "type": "Renewal",
                            "stage": "Negotiation",
                            "amount": 30000,
                            "close_date": "2026-12-10",
                        }
                    ],
                },
                "Clean Corp": {
                    "account": {"name": "Clean Corp", "renewal_date": "2027-06-01"},
                },
            }
        )

    def test_high_priority_case_is_high(self):
        row = sweep_account(
            self.registry, "Case Corp", TokenBudget(8000), today=TODAY
        )
        self.assertEqual(row["severity"], "high")
        self.assertIn("SSO login fails", row["top_signal"])
        self.assertEqual(row["renewal_date"], "2027-03-01")
        self.assertEqual(row["days_to_renewal"], 93)
        self.assertEqual(row["signal_count"], 1)

    def test_stalled_renewal_is_high(self):
        row = sweep_account(
            self.registry, "Stalled Corp", TokenBudget(8000), today=TODAY
        )
        self.assertEqual(row["severity"], "high")
        self.assertIn("stalled", row["top_signal"].lower())
        self.assertEqual(row["days_to_renewal"], 3)

    def test_closing_renewal_is_medium(self):
        row = sweep_account(
            self.registry, "Closing Corp", TokenBudget(8000), today=TODAY
        )
        self.assertEqual(row["severity"], "medium")
        self.assertIn("closing soon", row["top_signal"].lower())

    def test_clean_account_is_none_with_a_clean_bill(self):
        row = sweep_account(
            self.registry, "Clean Corp", TokenBudget(8000), today=TODAY
        )
        self.assertEqual(row["severity"], "none")
        self.assertEqual(row["top_signal"], "No churn signals found.")
        self.assertEqual(row["signal_count"], 0)

    def test_renewal_date_falls_back_to_the_opp_close_date(self):
        registry = _fake_registry(
            {
                "Opp Corp": {
                    "account": {"name": "Opp Corp", "renewal_date": ""},
                    "opportunities": [
                        {
                            "name": "Opp Corp - Renewal",
                            "type": "Renewal",
                            "stage": "Negotiation",
                            "amount": 10000,
                            "close_date": "2027-02-01",
                        }
                    ],
                }
            }
        )
        row = sweep_account(registry, "Opp Corp", TokenBudget(8000), today=TODAY)
        self.assertEqual(row["renewal_date"], "2027-02-01")
        self.assertEqual(row["days_to_renewal"], 65)


class TestRiskSignalsReused(unittest.TestCase):
    def test_sweep_uses_the_agents_signal_function(self):
        # The sweep does not invent a risk model: the same
        # _risk_signals the agent uses decides the severity.
        results = {
            "salesforce_list_cases": {
                "cases": [
                    {
                        "number": "1",
                        "subject": "Outage",
                        "status": "Open",
                        "priority": "High",
                        "age_days": 2,
                    }
                ]
            }
        }
        signals = _risk_signals(results, "Any Corp", today=TODAY)
        self.assertEqual(signals[0]["severity"], "high")


class TestRunSweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self.tmp.name, "accounts.csv")
        # 19 fields: name, domain, arr, currency, renewal_date, health,
        # owner, industry, employees, contacts, open_cases, last_touch,
        # notes, opp_name, opp_stage, opp_amount, opp_close_date,
        # opp_type, cases.
        rows = [
            [
                "Alpha Corp", "alpha.example", "120000", "USD", "2026-12-01",
                "green", "Jane", "Software", "100", "", "0", "2026-11-10",
                "", "Alpha Renewal", "Negotiation", "120000", "2026-11-01",
                "Renewal", "",
            ],
            [
                "Beta Corp", "beta.example", "80000", "USD", "2027-03-01",
                "yellow", "Bob", "Software", "50", "", "1", "2026-11-20",
                "", "", "", "", "", "",
                "SSO login fails|Open|High|6",
            ],
            [
                "Gamma Corp", "gamma.example", "40000", "USD", "2027-01-15",
                "green", "Ann", "Software", "20", "", "0", "2026-11-15",
                "", "", "", "", "", "", "",
            ],
        ]
        _write_csv(self.csv_path, [",".join(row) for row in rows])
        self.config = _config(self.tmp.name, self.csv_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _csv_registry(self) -> ToolRegistry:
        """The CSV account tools as the CRM, with empty stubs for the
        other six gather tools: the true CSV end to end, no live
        accounts, no stub-ticket noise."""
        from connectors.csv.tools import csv_crm_tools

        registry = ToolRegistry()
        for tool in csv_crm_tools(self.config):
            registry.register(tool)
        for name in (
            "gmail_search_messages",
            "gong_search_transcripts",
            "zoom_search_transcripts",
            "zendesk_list_tickets",
            "linear_list_issues",
            "slack_search_messages",
        ):
            registry.register(
                Tool(
                    name=name,
                    description="Empty stub for %s." % name,
                    handler=lambda **kw: {"matches": [], "tickets": [], "issues": []},
                )
            )
        return registry

    def test_three_accounts_ranked_worst_first(self):
        result = run_sweep(self._csv_registry(), self.config, today=TODAY)
        self.assertEqual(result["accounts"], 3)
        self.assertEqual(
            [row["account"] for row in result["results"]],
            ["Alpha Corp", "Beta Corp", "Gamma Corp"],
        )
        # Alpha: stalled renewal (high, renewal 2026-12-01).
        # Beta: high-priority case (high, renewal 2027-03-01).
        # Gamma: clean (none).
        self.assertEqual(result["results"][0]["severity"], "high")
        self.assertEqual(result["results"][1]["severity"], "high")
        self.assertEqual(result["results"][2]["severity"], "none")
        self.assertEqual(result["results"][0]["renewal_date"], "2026-12-01")
        self.assertEqual(result["results"][1]["renewal_date"], "2027-03-01")
        self.assertIsNotNone(result["ran_at"])

    def test_empty_book_is_gaps_not_an_error(self):
        config = _config(self.tmp.name, None)
        result = run_sweep(_fake_registry({}), config, today=TODAY)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["accounts"], 0)
        self.assertTrue(
            any("No accounts found" in gap for gap in result["gaps"])
        )


class TestSweepAccounts(unittest.TestCase):
    def test_csv_accounts_come_first_then_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "accounts.csv")
            _write_csv(
                csv_path,
                ["Acme Corp,acme.example,120000,USD,2027-02-15,green,Jane,Software,450,,2,2026-11-10,,,,,,,"],
            )
            config = _config(tmp, csv_path)
            from types import SimpleNamespace

            from harness.memory import AccountMemory

            memory = AccountMemory(config.memory_file)
            memory.record(
                SimpleNamespace(
                    agent="renewal-desk",
                    id="d1",
                    draft="ok",
                    job={"account": "Globex Inc"},
                    note="",
                ),
                "approved",
            )
            names, gaps, records = sweep_accounts(config, memory)
            self.assertEqual(names, ["Acme Corp", "Globex Inc"])
            self.assertEqual(records["acme corp"]["renewal_date"], "2027-02-15")

    def test_duplicate_names_dedupe_case_insensitively(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "accounts.csv")
            _write_csv(
                csv_path,
                ["Acme Corp,acme.example,120000,USD,2027-02-15,green,Jane,Software,450,,2,2026-11-10,,,,,,,"],
            )
            config = _config(tmp, csv_path)
            from types import SimpleNamespace

            from harness.memory import AccountMemory

            memory = AccountMemory(config.memory_file)
            memory.record(
                SimpleNamespace(
                    agent="renewal-desk",
                    id="d1",
                    draft="ok",
                    job={"account": "ACME CORP"},
                    note="",
                ),
                "approved",
            )
            names, _gaps, _records = sweep_accounts(config, memory)
            self.assertEqual(names, ["Acme Corp"])


class TestSweepStore(unittest.TestCase):
    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sweep.json")
            store = SweepStore(path)
            result = {
                "ran_at": "2026-11-28T00:00:00+00:00",
                "accounts": 1,
                "results": [
                    {
                        "account": "Acme Corp",
                        "severity": "high",
                        "top_signal": "Renewal stalled.",
                        "renewal_date": "2027-02-15",
                        "days_to_renewal": 79,
                        "signal_count": 1,
                        "signals": [],
                    }
                ],
                "gaps": [],
            }
            store.save(result)
            self.assertEqual(store.load(), result)

    def test_missing_file_is_the_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                SweepStore(os.path.join(tmp, "nope.json")).load(), empty_sweep()
            )

    def test_corrupt_file_is_the_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sweep.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertEqual(SweepStore(path).load(), empty_sweep())

    def test_wrong_shape_is_the_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sweep.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(["not", "a", "sweep"], f)
            self.assertEqual(SweepStore(path).load(), empty_sweep())


class TestSweepDashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self.tmp.name, "accounts.csv")
        _write_csv(
            self.csv_path,
            [
                "Acme Corp,acme.example,120000,USD,2027-02-15,green,Jane,Software,450,,2,2026-11-10,,,,,,,",
                "Beta Corp,beta.example,80000,USD,2026-12-20,yellow,Bob,Software,50,,1,2026-11-20,,,,,,,",
            ],
        )
        self.config = _config(self.tmp.name, self.csv_path)
        self.config.stub_mode = True
        self.queue = ApprovalQueue(self.config.approvals_file)
        self.client = TestClient(create_app(self.config, self.queue))

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_state_includes_the_sweep_empty_state(self):
        state = build_state(self.queue, self.config)
        self.assertIn("sweep", state)
        self.assertEqual(state["sweep"], empty_sweep())

    def test_sweep_view_is_the_stored_result(self):
        store = SweepStore(self.config.sweep_file)
        store.save(
            {
                "ran_at": "2026-11-28T00:00:00+00:00",
                "accounts": 1,
                "results": [],
                "gaps": [],
            }
        )
        self.assertEqual(sweep_view(store)["accounts"], 1)

    def test_post_sweep_runs_stores_and_shows_in_state(self):
        res = self.client.post("/api/sweep")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["accounts"], 2)
        self.assertEqual(
            [row["account"] for row in body["results"]],
            ["Beta Corp", "Acme Corp"],
        )
        state = self.client.get("/api/state").json()
        self.assertIsNotNone(state["sweep"]["ran_at"])
        self.assertEqual(len(state["sweep"]["results"]), 2)

    def test_index_renders_the_sweep_card(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Risk sweep", res.text)
        self.assertIn("Run sweep", res.text)


class TestSweepCLI(unittest.TestCase):
    def test_sweep_prints_the_ranked_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "accounts.csv")
            _write_csv(
                csv_path,
                [
                    "Acme Corp,acme.example,120000,USD,2027-02-15,green,Jane,Software,450,,2,2026-11-10,,,,,,,",
                    "Beta Corp,beta.example,80000,USD,2026-12-20,yellow,Bob,Software,50,,1,2026-11-20,,,,,,,",
                ],
            )
            env = {
                "BELAY_DATA_DIR": tmp,
                "HARNESS_CSV_ACCOUNTS_FILE": csv_path,
            }
            buf = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=False):
                with redirect_stdout(buf):
                    code = main(["sweep", "--stub"])
            self.assertEqual(code, 0)
            out = buf.getvalue()
            self.assertIn("Risk sweep: 2 account(s)", out)
            # Beta renews sooner, so it prints first.
            self.assertLess(out.index("Beta Corp"), out.index("Acme Corp"))
            self.assertTrue(os.path.exists(os.path.join(tmp, "sweep.json")))

    def test_sweep_with_no_accounts_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BELAY_DATA_DIR": tmp}
            buf = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=False):
                with redirect_stdout(buf):
                    code = main(["sweep", "--stub"])
            self.assertEqual(code, 0)
            self.assertIn("Sweep found no accounts.", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
