"""Tests for the user-configurable per-job token limit.

The limit is HARNESS_TOKEN_BUDGET (default 8000), editable in
dashboard Settings next to the LLM choice. Local Ollama inference
is free, so when the configured LLM is Ollama the budget is not
enforced: gathering is unlimited. A hosted provider gets the
configured limit per job. No telemetry, no cross-job cap: this is
only the existing per-job gathered-context budget, made
configurable with the Ollama exemption.
"""

import os
import tempfile
import unittest
from datetime import date
from unittest import mock

from fastapi.testclient import TestClient

from harness import ApprovalQueue, Config, ToolRegistry
from harness.agents import _gather
from harness.config import OLLAMA_BASE_URL
from harness.dashboard import create_app, token_budget_state
from harness.registry import Tool
from harness.setup import (
    apply_token_budget,
    parse_token_budget,
    read_env_file,
    setup_state,
)
from harness.summarizer import TokenBudget
from harness.sweep import run_sweep

TODAY = date(2026, 11, 28)
OPENAI_BASE_URL = "https://api.openai.com/v1"

_CSV_HEADER = (
    "name,domain,arr,currency,renewal_date,health,owner,industry,"
    "employees,contacts,open_cases,last_touch,notes,opp_name,opp_stage,"
    "opp_amount,opp_close_date,opp_type,cases\n"
)


def _temp_config(tmp, **overrides):
    """A Config whose file-backed stores live in a temp dir."""
    kwargs = dict(
        approvals_file=os.path.join(tmp, "approvals.json"),
        memory_file=os.path.join(tmp, "memory.json"),
        schedules_file=os.path.join(tmp, "schedules.json"),
        escalations_file=os.path.join(tmp, "escalations.json"),
        mcp_servers_file=os.path.join(tmp, "mcp-servers.json"),
        env_path=os.path.join(tmp, ".env"),
        stub_mode=True,
    )
    kwargs.update(overrides)
    return Config(**kwargs)


class _EnvCleanCase(unittest.TestCase):
    """Snapshot and restore the process environment: the apply
    helpers load .env into os.environ, so a saved value would leak
    into the next test without a full restore."""

    _KEYS = (
        "HARNESS_TOKEN_BUDGET",
        "HARNESS_LLM_BASE_URL",
        "HARNESS_LLM_API_KEY",
        "HARNESS_LLM_MODEL",
    )

    def setUp(self):
        self._saved_env = dict(os.environ)
        for key in self._KEYS:
            os.environ.pop(key, None)

        def _restore_env():
            os.environ.clear()
            os.environ.update(self._saved_env)

        self.addCleanup(_restore_env)


class TestEffectiveTokenBudget(_EnvCleanCase):
    def test_default_limit_is_8000(self):
        self.assertEqual(Config().token_budget_per_job, 8000)

    def test_default_llm_is_ollama_so_effective_is_unlimited(self):
        self.assertIsNone(Config().effective_token_budget)

    def test_hosted_llm_gets_the_configured_limit(self):
        config = Config(
            llm_base_url=OPENAI_BASE_URL, token_budget_per_job=4000
        )
        self.assertEqual(config.effective_token_budget, 4000)

    def test_custom_ollama_host_is_unlimited(self):
        config = Config(
            llm_base_url="http://192.168.1.5:11434/v1",
            token_budget_per_job=4000,
        )
        self.assertIsNone(config.effective_token_budget)

    def test_env_var_sets_the_limit(self):
        with mock.patch.dict(
            os.environ,
            {
                "HARNESS_TOKEN_BUDGET": "4000",
                "HARNESS_LLM_BASE_URL": OPENAI_BASE_URL,
            },
        ):
            with mock.patch("harness.config.load_dotenv"):
                config = Config.from_env()
        self.assertEqual(config.token_budget_per_job, 4000)
        self.assertEqual(config.effective_token_budget, 4000)

    def test_env_var_with_ollama_is_still_unlimited(self):
        with mock.patch.dict(os.environ, {"HARNESS_TOKEN_BUDGET": "4000"}):
            with mock.patch("harness.config.load_dotenv"):
                config = Config.from_env()
        self.assertEqual(config.token_budget_per_job, 4000)
        self.assertIsNone(config.effective_token_budget)


class TestUnlimitedTokenBudget(unittest.TestCase):
    def test_unlimited_spend_never_refuses(self):
        budget = TokenBudget(None)
        self.assertTrue(budget.spend("x" * 40000))  # 10,000 tokens
        self.assertTrue(budget.spend("x" * 40000))
        self.assertEqual(budget.spent, 20000)
        self.assertIsNone(budget.remaining())

    def test_unlimited_charge_still_accounts(self):
        budget = TokenBudget(None)
        budget.charge("a" * 40)  # 10 tokens
        self.assertEqual(budget.spent, 10)
        self.assertIsNone(budget.remaining())


class TestGatherEnforcement(unittest.TestCase):
    def setUp(self):
        registry = ToolRegistry()
        registry.register(
            Tool(
                name="big_tool",
                description="Big.",
                handler=lambda **kw: {"notes": "x" * 4000},
            )
        )
        registry.register(
            Tool(
                name="small_tool",
                description="Small.",
                handler=lambda **kw: {"notes": "ok"},
            )
        )
        self.registry = registry

    def test_hosted_limit_skips_the_over_budget_tool(self):
        config = Config(
            llm_base_url=OPENAI_BASE_URL, token_budget_per_job=100
        )
        context, sources, _links = _gather(
            self.registry,
            [("big_tool", {}), ("small_tool", {})],
            TokenBudget(config.effective_token_budget),
        )
        self.assertNotIn("big_tool", sources)
        self.assertIn(
            "Skipped: would exceed the token budget.", context["big_tool"]
        )
        self.assertIn("small_tool", sources)

    def test_ollama_gathers_the_same_tool(self):
        config = Config(
            llm_base_url=OLLAMA_BASE_URL, token_budget_per_job=100
        )
        context, sources, _links = _gather(
            self.registry,
            [("big_tool", {}), ("small_tool", {})],
            TokenBudget(config.effective_token_budget),
        )
        self.assertIn("big_tool", sources)
        self.assertIn("small_tool", sources)


class TestApplyTokenBudget(_EnvCleanCase):
    def test_save_persists_to_env_and_takes_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _temp_config(tmp)
            result = apply_token_budget(config, 4000)
            self.assertEqual(result["token_budget_per_job"], 4000)
            # The default LLM is Ollama, so the limit is saved but not
            # enforced: the effective budget is unlimited.
            self.assertIsNone(result["effective_token_budget"])
            self.assertTrue(result["unlimited"])
            _lines, values = read_env_file(config.env_path)
            self.assertEqual(values["HARNESS_TOKEN_BUDGET"], "4000")

    def test_save_with_hosted_llm_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _temp_config(tmp)
            # The hosted choice has to be saved first: the reload
            # inside apply_token_budget reads the LLM from .env, so
            # a constructor-only base URL would revert to Ollama.
            from harness.setup import apply_llm_choice

            apply_llm_choice(config, "openai", api_key="sk-test")
            result = apply_token_budget(config, 4000)
        self.assertEqual(result["effective_token_budget"], 4000)
        self.assertFalse(result["unlimited"])

    def test_string_value_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _temp_config(tmp)
            result = apply_token_budget(config, "4000")
        self.assertEqual(result["token_budget_per_job"], 4000)

    def test_invalid_values_raise_and_write_nothing(self):
        for bad in ("abc", "0", "-5", "", "4.5", None):
            with self.subTest(bad=bad):
                with tempfile.TemporaryDirectory() as tmp:
                    config = _temp_config(tmp)
                    with self.assertRaises(ValueError):
                        apply_token_budget(config, bad)
                    self.assertFalse(os.path.exists(config.env_path))

    def test_parse_accepts_plain_integers_only(self):
        self.assertEqual(parse_token_budget("8000"), 8000)
        self.assertEqual(parse_token_budget(8000), 8000)
        with self.assertRaises(ValueError):
            parse_token_budget("eight thousand")


class TestTokenBudgetState(unittest.TestCase):
    def test_ollama_state_is_unlimited(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _temp_config(tmp)
            queue = ApprovalQueue(config.approvals_file)
            state = token_budget_state(queue, config)
        self.assertEqual(state["budget_per_job"], 8000)
        self.assertIsNone(state["effective_token_budget"])
        self.assertTrue(state["unlimited"])

    def test_hosted_state_shows_the_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _temp_config(
                tmp, llm_base_url=OPENAI_BASE_URL, token_budget_per_job=4000
            )
            queue = ApprovalQueue(config.approvals_file)
            state = token_budget_state(queue, config)
        self.assertEqual(state["budget_per_job"], 4000)
        self.assertEqual(state["effective_token_budget"], 4000)
        self.assertFalse(state["unlimited"])


class TestTokenBudgetEndpoints(_EnvCleanCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = _temp_config(self.tmp.name)
        self.client = TestClient(create_app(self.config))

    def test_post_llm_with_token_budget_saves_both(self):
        res = self.client.post(
            "/api/setup/llm",
            json={
                "provider": "ollama",
                "model": "llama3.1:8b",
                "token_budget": 4000,
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["token_budget_per_job"], 4000)
        self.assertTrue(res.json()["unlimited"])
        state = self.client.get("/api/setup").json()
        self.assertEqual(state["llm"]["token_budget_per_job"], 4000)
        self.assertTrue(state["llm"]["token_budget_unlimited"])

    def test_post_llm_with_hosted_provider_applies_the_limit(self):
        res = self.client.post(
            "/api/setup/llm",
            json={
                "provider": "openai",
                "api_key": "sk-test",
                "model": "gpt-4o-mini",
                "token_budget": 4000,
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["effective_token_budget"], 4000)
        self.assertFalse(res.json()["unlimited"])
        state = self.client.get("/api/setup").json()
        self.assertFalse(state["llm"]["token_budget_unlimited"])

    def test_bad_token_budget_is_400_and_saves_nothing(self):
        res = self.client.post(
            "/api/setup/llm",
            json={
                "provider": "openai",
                "api_key": "sk-test",
                "token_budget": "abc",
            },
        )
        self.assertEqual(res.status_code, 400)
        # Validation runs before anything is written: the LLM choice
        # is unchanged too, not just the limit.
        state = self.client.get("/api/setup").json()
        self.assertEqual(state["llm"]["provider"], "ollama")
        self.assertFalse(os.path.exists(self.config.env_path))

    def test_llm_save_without_token_budget_leaves_limit_unchanged(self):
        res = self.client.post(
            "/api/setup/llm",
            json={"provider": "ollama", "model": "llama3.1:8b"},
        )
        self.assertEqual(res.status_code, 200)
        state = self.client.get("/api/setup").json()
        self.assertEqual(state["llm"]["token_budget_per_job"], 8000)

    def test_settings_page_has_the_token_limit_input(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn('id="llm-token-budget"', res.text)


class TestSweepRespectsTheEffectiveBudget(_EnvCleanCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv_path = os.path.join(self.tmp.name, "accounts.csv")
        row = [
            "Beta Corp",
            "beta.example",
            "80000",
            "USD",
            "2027-03-01",
            "yellow",
            "Bob",
            "Software",
            "50",
            "",
            "1",
            "2026-11-20",
            "",
            "",
            "",
            "",
            "",
            "",
            "SSO login fails|Open|High|6",
        ]
        with open(self.csv_path, "w", encoding="utf-8", newline="") as f:
            f.write(_CSV_HEADER)
            f.write(",".join(row) + "\n")

    def _config(self, llm_base_url, budget):
        return _temp_config(
            self.tmp.name,
            csv_accounts_file=self.csv_path,
            llm_base_url=llm_base_url,
            token_budget_per_job=budget,
        )

    def _registry(self, config):
        from connectors.csv.tools import csv_crm_tools

        registry = ToolRegistry()
        for tool in csv_crm_tools(config):
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
                    handler=lambda **kw: {
                        "matches": [],
                        "tickets": [],
                        "issues": [],
                    },
                )
            )
        return registry

    def test_hosted_tiny_budget_finds_no_signals(self):
        config = self._config(OPENAI_BASE_URL, 1)
        result = run_sweep(self._registry(config), config, today=TODAY)
        self.assertEqual(result["accounts"], 1)
        self.assertEqual(result["results"][0]["signal_count"], 0)
        self.assertEqual(result["results"][0]["severity"], "none")

    def test_ollama_tiny_budget_finds_the_signals(self):
        config = self._config(OLLAMA_BASE_URL, 1)
        result = run_sweep(self._registry(config), config, today=TODAY)
        self.assertEqual(result["accounts"], 1)
        self.assertEqual(result["results"][0]["signal_count"], 1)
        self.assertEqual(result["results"][0]["severity"], "high")


class TestSetupStateIncludesTokenBudget(unittest.TestCase):
    def test_state_carries_the_limit_and_the_exemption(self):
        state = setup_state(Config())
        self.assertEqual(state["llm"]["token_budget_per_job"], 8000)
        self.assertTrue(state["llm"]["token_budget_unlimited"])
        hosted = Config(
            llm_base_url=OPENAI_BASE_URL, token_budget_per_job=4000
        )
        state = setup_state(hosted)
        self.assertEqual(state["llm"]["token_budget_per_job"], 4000)
        self.assertFalse(state["llm"]["token_budget_unlimited"])


if __name__ == "__main__":
    unittest.main()
