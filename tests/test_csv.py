"""Tests for the CSV accounts connector: parsing, loading, the
registry alias, the sample accounts file, and the demo path.

No live network and no credentials are needed: the connector reads a
local file, and the demo test runs with the stub LLM. Gaps, not
errors: a missing file, a bad row, or a bad field is a named gap in
the returned dict, never a crash.
"""

import csv
import os
import tempfile
import unittest
from unittest import mock

from connectors.csv.tools import (
    SAMPLE_CSV_PATH,
    csv_crm_tools,
    csv_tools,
    get_account,
    list_accounts,
    list_cases,
    list_opportunities,
    load_accounts,
)
from harness import AGENTS, TokenBudget
from harness.config import Config
from harness.llm import StubLLM
from harness.run import build_registry
from harness.setup import seed_demo

COLUMNS = [
    "name",
    "domain",
    "arr",
    "currency",
    "renewal_date",
    "health",
    "owner",
    "industry",
    "employees",
    "contacts",
    "open_cases",
    "last_touch",
    "notes",
    "opp_name",
    "opp_stage",
    "opp_amount",
    "opp_close_date",
    "opp_type",
    "cases",
]


def _write_csv(path, rows):
    """Write rows (dicts keyed by COLUMNS) to a CSV file."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in COLUMNS})


def _acme_row(**overrides):
    """The Acme Corp row, matching the stub account and the sample
    file's first account."""
    row = {
        "name": "Acme Corp",
        "domain": "acme.example",
        "arr": "120000",
        "currency": "USD",
        "renewal_date": "2027-02-15",
        "health": "green",
        "owner": "Jane CSM",
        "industry": "Software",
        "employees": "450",
        "contacts": "Dana Cole <dana@acme.example>; Sam Reyes <sam@acme.example>",
        "open_cases": "2",
        "last_touch": "2026-11-10",
        "notes": "SSO fix ships next week.",
        "opp_name": "Acme Corp - Renewal FY27",
        "opp_stage": "Negotiation",
        "opp_amount": "120000",
        "opp_close_date": "2027-02-15",
        "opp_type": "Renewal",
        "cases": "SSO login fails for new hires|Working|High|6; Export timeout|New|Medium|2",
    }
    row.update(overrides)
    return row


class _TempCsv(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.path = os.path.join(self._tmpdir.name, "accounts.csv")

    def _config(self, path=None, **kwargs):
        return Config(csv_accounts_file=path if path is not None else self.path, **kwargs)


class TestCsvParsing(_TempCsv):
    def test_valid_row_parses_all_fields(self):
        _write_csv(self.path, [_acme_row()])
        loaded = load_accounts(self._config())
        self.assertEqual(loaded["gaps"], [])
        self.assertEqual(len(loaded["accounts"]), 1)
        account = loaded["accounts"][0]
        self.assertEqual(account["name"], "Acme Corp")
        self.assertEqual(account["id"], "csv:acme-corp")
        self.assertEqual(account["domain"], "acme.example")
        self.assertEqual(account["arr"], 120000)
        self.assertEqual(account["currency"], "USD")
        self.assertEqual(account["renewal_date"], "2027-02-15")
        self.assertEqual(account["health"], "green")
        self.assertEqual(account["owner"], "Jane CSM")
        self.assertEqual(account["industry"], "Software")
        self.assertEqual(account["employees"], 450)
        self.assertEqual(
            account["contacts"],
            [
                {"name": "Dana Cole", "email": "dana@acme.example"},
                {"name": "Sam Reyes", "email": "sam@acme.example"},
            ],
        )
        self.assertEqual(account["open_cases"], 2)
        self.assertEqual(account["last_touch"], "2026-11-10")
        self.assertEqual(account["notes"], "SSO fix ships next week.")
        self.assertEqual(account["source"], "csv")

    def test_only_name_is_required(self):
        _write_csv(self.path, [_acme_row(name="Bare Corp", domain="", arr="", renewal_date="", health="", owner="", industry="", employees="", contacts="", open_cases="", last_touch="", notes="", opp_name="", cases="")])
        loaded = load_accounts(self._config())
        self.assertEqual(loaded["gaps"], [])
        self.assertEqual(len(loaded["accounts"]), 1)
        account = loaded["accounts"][0]
        self.assertEqual(account["name"], "Bare Corp")
        self.assertEqual(account["currency"], "USD")
        self.assertEqual(account["contacts"], [])
        self.assertIsNone(account["open_cases"])

    def test_missing_name_row_is_skipped_as_a_gap(self):
        _write_csv(self.path, [_acme_row(), _acme_row(name="")])
        loaded = load_accounts(self._config())
        self.assertEqual(len(loaded["accounts"]), 1)
        self.assertEqual(len(loaded["gaps"]), 1)
        self.assertIn("Row 3", loaded["gaps"][0])
        self.assertIn("no account name", loaded["gaps"][0])

    def test_bad_number_is_a_gap_and_the_field_stays_empty(self):
        _write_csv(self.path, [_acme_row(arr="not-a-number", employees="many")])
        loaded = load_accounts(self._config())
        self.assertEqual(len(loaded["accounts"]), 1)
        account = loaded["accounts"][0]
        self.assertIsNone(account["arr"])
        self.assertIsNone(account["employees"])
        # One gap per row: the row's bad fields are joined in it.
        self.assertEqual(len(loaded["gaps"]), 1)
        self.assertIn("arr", loaded["gaps"][0])
        self.assertIn("employees", loaded["gaps"][0])

    def test_lenient_numbers_parse(self):
        _write_csv(self.path, [_acme_row(arr="$120,000", employees=" 450 ")])
        loaded = load_accounts(self._config())
        self.assertEqual(loaded["gaps"], [])
        account = loaded["accounts"][0]
        self.assertEqual(account["arr"], 120000)
        self.assertEqual(account["employees"], 450)

    def test_bad_date_is_a_gap_and_the_field_stays_empty(self):
        _write_csv(self.path, [_acme_row(renewal_date="Feb 15 2027")])
        loaded = load_accounts(self._config())
        self.assertEqual(len(loaded["accounts"]), 1)
        self.assertEqual(loaded["accounts"][0]["renewal_date"], "")
        self.assertEqual(len(loaded["gaps"]), 1)
        self.assertIn("renewal_date", loaded["gaps"][0])

    def test_unknown_health_is_a_gap_and_left_empty(self):
        _write_csv(self.path, [_acme_row(health="blue")])
        loaded = load_accounts(self._config())
        self.assertEqual(len(loaded["accounts"]), 1)
        self.assertEqual(loaded["accounts"][0]["health"], "")
        self.assertEqual(len(loaded["gaps"]), 1)
        self.assertIn("health", loaded["gaps"][0])

    def test_duplicate_name_is_a_gap_and_the_first_is_kept(self):
        _write_csv(self.path, [_acme_row(), _acme_row(arr="999999")])
        loaded = load_accounts(self._config())
        self.assertEqual(len(loaded["accounts"]), 1)
        self.assertEqual(loaded["accounts"][0]["arr"], 120000)
        self.assertEqual(len(loaded["gaps"]), 1)
        self.assertIn("duplicate", loaded["gaps"][0])

    def test_short_contacts_and_cases_still_load_leniently(self):
        # A contact without an email keeps its name and no email;
        # a case with only a subject still loads, the rest empty.
        # Neither is a gap: the row loads, and nothing is guessed.
        _write_csv(
            self.path,
            [_acme_row(contacts="No Email Here", cases="not a case")],
        )
        loaded = load_accounts(self._config())
        self.assertEqual(loaded["gaps"], [])
        self.assertEqual(len(loaded["accounts"]), 1)
        account = loaded["accounts"][0]
        self.assertEqual(
            account["contacts"], [{"name": "No Email Here", "email": None}]
        )
        self.assertEqual(len(account["cases"]), 1)
        self.assertEqual(account["cases"][0]["subject"], "not a case")
        self.assertEqual(account["cases"][0]["status"], "")
        self.assertIsNone(account["cases"][0]["age_days"])

    def test_header_only_file_loads_zero_accounts(self):
        _write_csv(self.path, [])
        loaded = load_accounts(self._config())
        self.assertEqual(loaded["accounts"], [])
        self.assertEqual(loaded["gaps"], [])

    def test_empty_file_is_a_named_gap(self):
        with open(self.path, "w", encoding="utf-8"):
            pass
        loaded = load_accounts(self._config())
        self.assertEqual(loaded["accounts"], [])
        self.assertEqual(len(loaded["gaps"]), 1)
        self.assertIn("empty", loaded["gaps"][0])


class TestCsvLoading(_TempCsv):
    def test_missing_file_is_a_named_gap_not_an_error(self):
        config = self._config(path=os.path.join(self._tmpdir.name, "nope.csv"))
        loaded = load_accounts(config)
        self.assertEqual(loaded["accounts"], [])
        self.assertEqual(len(loaded["gaps"]), 1)
        self.assertIn("not found", loaded["gaps"][0])
        result = get_account(config, "Acme Corp")
        self.assertEqual(result["gaps"], loaded["gaps"])
        self.assertNotIn("error", result)

    def test_no_file_configured_is_a_named_gap(self):
        config = Config()
        loaded = load_accounts(config)
        self.assertEqual(loaded["accounts"], [])
        self.assertEqual(len(loaded["gaps"]), 1)
        self.assertIn("HARNESS_CSV_ACCOUNTS_FILE", loaded["gaps"][0])

    def test_get_account_found_exact_case_insensitive_and_domain(self):
        _write_csv(self.path, [_acme_row()])
        config = self._config()
        self.assertEqual(get_account(config, "Acme Corp")["arr"], 120000)
        self.assertEqual(get_account(config, "acme corp")["arr"], 120000)
        self.assertEqual(get_account(config, "acme.example")["arr"], 120000)

    def test_get_account_substring_match(self):
        _write_csv(self.path, [_acme_row()])
        self.assertEqual(get_account(self._config(), "Acme")["name"], "Acme Corp")

    def test_get_account_unknown_is_an_error_not_a_gap(self):
        _write_csv(self.path, [_acme_row()])
        result = get_account(self._config(), "No Such Corp")
        self.assertIn("error", result)
        self.assertIn("No account matched", result["error"])

    def test_list_accounts_soonest_renewal_first_no_date_last(self):
        _write_csv(
            self.path,
            [
                _acme_row(name="No Date Co", renewal_date=""),
                _acme_row(name="Later Co", renewal_date="2027-06-01"),
                _acme_row(name="Sooner Co", renewal_date="2027-01-05"),
            ],
        )
        listed = list_accounts(self._config())
        self.assertEqual(
            [a["name"] for a in listed["accounts"]],
            ["Sooner Co", "Later Co", "No Date Co"],
        )

    def test_opportunities_present_and_absent(self):
        _write_csv(self.path, [_acme_row(), _acme_row(name="No Opp Co", opp_name="")])
        config = self._config()
        opps = list_opportunities(config, "Acme Corp")
        self.assertEqual(len(opps["opportunities"]), 1)
        self.assertEqual(opps["opportunities"][0]["stage"], "Negotiation")
        self.assertEqual(opps["opportunities"][0]["amount"], 120000)
        self.assertEqual(opps["opportunities"][0]["type"], "Renewal")
        self.assertEqual(opps["opportunities"][0]["close_date"], "2027-02-15")
        empty = list_opportunities(config, "No Opp Co")
        self.assertEqual(empty["opportunities"], [])
        self.assertEqual(len(empty["gaps"]), 1)
        self.assertIn("No opportunities", empty["gaps"][0])

    def test_cases_present_and_absent(self):
        _write_csv(self.path, [_acme_row(), _acme_row(name="No Cases Co", cases="")])
        config = self._config()
        cases = list_cases(config, "Acme Corp")
        self.assertEqual(len(cases["cases"]), 2)
        self.assertEqual(cases["cases"][0]["subject"], "SSO login fails for new hires")
        self.assertEqual(cases["cases"][0]["status"], "Working")
        self.assertEqual(cases["cases"][0]["priority"], "High")
        self.assertEqual(cases["cases"][0]["age_days"], 6)
        empty = list_cases(config, "No Cases Co")
        self.assertEqual(empty["cases"], [])
        self.assertEqual(len(empty["gaps"]), 1)
        self.assertIn("No cases", empty["gaps"][0])

    def test_csv_tools_are_four_read_only_tools(self):
        tools = csv_tools(self._config())
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "csv_get_account",
                "csv_list_accounts",
                "csv_list_cases",
                "csv_list_opportunities",
            ],
        )
        self.assertTrue(all(not t.write for t in tools))

    def test_csv_crm_tools_use_salesforce_names(self):
        tools = csv_crm_tools(self._config())
        self.assertEqual(
            sorted(t.name for t in tools),
            [
                "salesforce_get_account",
                "salesforce_list_cases",
                "salesforce_list_opportunities",
            ],
        )
        self.assertTrue(all(not t.write for t in tools))


class TestCsvRegistry(_TempCsv):
    def _registry(self, **kwargs):
        return build_registry(self._config(**kwargs))

    def test_crm_is_csv_when_csv_configured_and_no_crm(self):
        _write_csv(self.path, [_acme_row()])
        registry = self._registry()
        self.assertEqual(registry.crm, "csv")
        self.assertIn("csv_list_accounts", [t.name for t in registry.list_tools()])
        self.assertIn("csv_get_account", [t.name for t in registry.list_tools()])
        result = registry.call(
            "salesforce_get_account", account="Acme Corp"
        )
        self.assertEqual(result["source"], "csv")
        self.assertEqual(result["arr"], 120000)
        self.assertEqual(result["renewal_date"], "2027-02-15")
        self.assertNotIn("link", result)

    def test_crm_stays_salesforce_when_salesforce_configured(self):
        _write_csv(self.path, [_acme_row()])
        config = self._config(salesforce_instance_url="https://example.my.salesforce.com", salesforce_access_token="x")
        registry = build_registry(config)
        self.assertEqual(registry.crm, "salesforce")
        # The CSV tools are still registered under their own names.
        self.assertIn("csv_list_accounts", [t.name for t in registry.list_tools()])

    def test_crm_stays_salesforce_in_stub_mode(self):
        _write_csv(self.path, [_acme_row()])
        config = self._config()
        config.stub_mode = True
        registry = build_registry(config)
        self.assertEqual(registry.crm, "salesforce")

    def test_crm_is_salesforce_when_no_csv_configured(self):
        registry = build_registry(Config())
        self.assertEqual(registry.crm, "salesforce")
        self.assertIn("csv_list_accounts", [t.name for t in registry.list_tools()])
        result = registry.call("csv_get_account", account="Acme Corp")
        self.assertIn("HARNESS_CSV_ACCOUNTS_FILE", result["gaps"][0])

    def test_alias_serves_csv_opportunities_and_cases(self):
        _write_csv(self.path, [_acme_row()])
        registry = self._registry()
        opps = registry.call(
            "salesforce_list_opportunities", account="Acme Corp"
        )
        self.assertEqual(opps["opportunities"][0]["name"], "Acme Corp - Renewal FY27")
        cases = registry.call("salesforce_list_cases", account="Acme Corp")
        self.assertEqual(len(cases["cases"]), 2)


class TestCsvSample(unittest.TestCase):
    def test_sample_file_exists_and_has_ten_fictional_accounts(self):
        self.assertTrue(os.path.exists(SAMPLE_CSV_PATH))
        config = Config(csv_accounts_file=SAMPLE_CSV_PATH)
        loaded = load_accounts(config)
        self.assertEqual(loaded["gaps"], [])
        self.assertEqual(len(loaded["accounts"]), 10)
        names = [a["name"] for a in loaded["accounts"]]
        self.assertEqual(
            names,
            [
                "Acme Corp",
                "Beacon Health",
                "Cascade Logistics",
                "Driftwood Outdoors",
                "Ember Financial",
                "Foxglove Media",
                "Granite Construction",
                "Harbor Dental",
                "Ironpeak Manufacturing",
                "Juniper Legal",
            ],
        )
        self.assertTrue(all(".example" in (a.get("domain") or "") for a in loaded["accounts"]))

    def test_sample_first_account_matches_the_stub(self):
        config = Config(csv_accounts_file=SAMPLE_CSV_PATH)
        acme = get_account(config, "Acme Corp")
        self.assertEqual(acme["arr"], 120000)
        self.assertEqual(acme["renewal_date"], "2027-02-15")
        self.assertEqual(acme["health"], "green")
        self.assertEqual(acme["owner"], "Jane CSM")
        self.assertEqual(acme["industry"], "Software")
        self.assertEqual(acme["employees"], 450)
        self.assertEqual(acme["open_cases"], 2)
        self.assertEqual(acme["last_touch"], "2026-11-10")
        self.assertEqual(
            acme["contacts"],
            [
                {"name": "Dana Cole", "email": "dana@acme.example"},
                {"name": "Sam Reyes", "email": "sam@acme.example"},
            ],
        )

    def test_sample_health_spread_covers_all_three_states(self):
        config = Config(csv_accounts_file=SAMPLE_CSV_PATH)
        loaded = load_accounts(config)
        healths = [a["health"] for a in loaded["accounts"]]
        self.assertEqual(healths.count("green"), 5)
        self.assertEqual(healths.count("yellow"), 3)
        self.assertEqual(healths.count("red"), 2)

    def test_sample_every_account_has_a_renewal_opportunity(self):
        config = Config(csv_accounts_file=SAMPLE_CSV_PATH)
        loaded = load_accounts(config)
        for account in loaded["accounts"]:
            opps = list_opportunities(config, account["name"])
            self.assertEqual(len(opps["opportunities"]), 1, account["name"])


class _IO:
    """Captures seed_demo output; answers prompts from a list."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.lines = []

    def input(self, prompt=""):
        return self.answers.pop(0) if self.answers else "y"

    def out(self, text=""):
        self.lines.append(text)

    def text(self):
        return "\n".join(self.lines)


class TestCsvDemo(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.config = Config(
            approvals_file=os.path.join(self._tmpdir.name, "approvals.json"),
            memory_file=os.path.join(self._tmpdir.name, "memory.json"),
            schedules_file=os.path.join(self._tmpdir.name, "schedules.json"),
            escalations_file=os.path.join(self._tmpdir.name, "escalations.json"),
            mcp_servers_file=os.path.join(self._tmpdir.name, "mcp-servers.json"),
        )
        self._llm = mock.patch(
            "harness.setup.ollama_reachable", return_value=False
        )
        self._llm.start()
        self.addCleanup(self._llm.stop)
        self._models = mock.patch(
            "harness.setup.ollama_models", return_value=["llama3.1:8b"]
        )
        self._models.start()
        self.addCleanup(self._models.stop)
        self._cli = mock.patch(
            "harness.setup.ollama_cli_present", return_value=False
        )
        self._cli.start()
        self.addCleanup(self._cli.stop)

    def _seed(self):
        io = _IO([])
        report = seed_demo(
            self.config, confirm=True, input_fn=io.input, out=io.out
        )
        return report, io

    def test_seed_demo_installs_the_sample_csv(self):
        report, io = self._seed()
        self.assertEqual(report["status"], "seeded")
        sample_csv = report["sample_csv"]
        self.assertIsNotNone(sample_csv)
        self.assertTrue(os.path.exists(sample_csv))
        self.assertEqual(
            os.path.dirname(os.path.abspath(sample_csv)),
            os.path.dirname(os.path.abspath(self.config.approvals_file)),
        )
        self.assertEqual(os.path.basename(sample_csv), "sample_accounts.csv")
        loaded = load_accounts(Config(csv_accounts_file=sample_csv))
        self.assertEqual(len(loaded["accounts"]), 10)
        self.assertIn("Sample accounts installed", io.text())
        self.assertIn("HARNESS_CSV_ACCOUNTS_FILE", io.text())

    def test_seed_demo_never_clobbers_an_existing_sample_csv(self):
        dest = os.path.join(self._tmpdir.name, "sample_accounts.csv")
        # A user's own file, with the demo account in it so the demo
        # agents still find Acme Corp; the point is the content is
        # theirs, not the sample's.
        content = (
            "name,domain,arr,currency,renewal_date,health,owner,industry,"
            "employees,contacts,open_cases,last_touch,notes,opp_name,"
            "opp_stage,opp_amount,opp_close_date,opp_type,cases\n"
            "Acme Corp,myown.example,999,USD,2027-02-15,green,Jane CSM,"
            "Software,10,,,,,,,,,,\n"
        )
        with open(dest, "w", encoding="utf-8") as f:
            f.write(content)
        report, _ = self._seed()
        self.assertEqual(report["sample_csv"], dest)
        with open(dest, encoding="utf-8") as f:
            self.assertEqual(f.read(), content)

    def test_demo_agents_gather_from_the_csv_file(self):
        report, _ = self._seed()
        config = Config(csv_accounts_file=report["sample_csv"])
        registry = build_registry(config)
        self.assertEqual(registry.crm, "csv")
        result = registry.call("salesforce_get_account", account="Cascade Logistics")
        self.assertEqual(result["source"], "csv")
        self.assertEqual(result["health"], "yellow")
        self.assertEqual(result["arr"], 210000)
        agent = AGENTS["renewal-desk"]
        draft = agent.run(
            {"account": "Cascade Logistics"},
            registry,
            StubLLM(),
            TokenBudget(8000),
        )
        self.assertEqual(draft.agent, "renewal-desk")
        self.assertIn("salesforce_get_account", draft.sources)
        self.assertIn("salesforce_list_opportunities", draft.sources)
        self.assertIn("Cascade Logistics", draft.text)
        self.assertIn("2027-01-20", draft.text)


if __name__ == "__main__":
    unittest.main()
