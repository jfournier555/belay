"""Tests for the dashboard setup wizard and connector config
(v1.16.0): the Settings view is the default first-run path.

Secrets go to .env and are never displayed in full: the state
endpoint masks them, the apply endpoints never return them, and a
blank field never clears a stored key. These tests pin the masking,
the state shape, the apply validation, and the three endpoints,
against temp store files and a temp .env, so no real credentials
and no network calls.
"""

import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from harness import Config
from harness.dashboard import create_app
from harness.setup import (
    apply_connector_values,
    apply_llm_choice,
    mask_secret,
    read_env_file,
    setup_state,
)

_ENV_KEYS = (
    "HARNESS_LLM_BASE_URL",
    "HARNESS_LLM_API_KEY",
    "HARNESS_LLM_MODEL",
    "SALESFORCE_INSTANCE_URL",
    "SALESFORCE_ACCESS_TOKEN",
    "SLACK_BOT_TOKEN",
    "GONG_ACCESS_TOKEN",
    "HUBSPOT_ACCESS_TOKEN",
    "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET",
    "GMAIL_REFRESH_TOKEN",
    "ZOOM_ACCOUNT_ID",
    "ZOOM_CLIENT_ID",
    "ZOOM_CLIENT_SECRET",
    "ZENDESK_SUBDOMAIN",
    "ZENDESK_EMAIL",
    "ZENDESK_API_TOKEN",
    "LINEAR_API_KEY",
    "CHAMPIFY_API_KEY",
    "GCAL_CLIENT_ID",
    "GCAL_CLIENT_SECRET",
    "GCAL_REFRESH_TOKEN",
)


def _temp_config(tmp):
    return Config(
        approvals_file=os.path.join(tmp, "approvals.json"),
        memory_file=os.path.join(tmp, "memory.json"),
        schedules_file=os.path.join(tmp, "schedules.json"),
        escalations_file=os.path.join(tmp, "escalations.json"),
        mcp_servers_file=os.path.join(tmp, "mcp-servers.json"),
        env_path=os.path.join(tmp, ".env"),
        stub_mode=True,
    )


class _EnvCleanCase(unittest.TestCase):
    """Snapshot and restore the whole process environment: the apply
    helpers load .env into os.environ, so popping only the keys the
    test knows about would leak values into the next test."""

    def setUp(self):
        self._saved_env = dict(os.environ)
        for key in list(_ENV_KEYS):
            os.environ.pop(key, None)

        def _restore_env():
            os.environ.clear()
            os.environ.update(self._saved_env)

        self.addCleanup(_restore_env)


class TestMaskSecret(_EnvCleanCase):
    def test_empty_stays_empty(self):
        self.assertEqual(mask_secret(""), "")

    def test_short_value_is_all_stars(self):
        self.assertEqual(mask_secret("abc"), "***")

    def test_long_value_shows_only_last_four(self):
        masked = mask_secret("sk-live-abcdef1234")
        self.assertEqual(masked, "********1234")
        self.assertNotIn("sk-live-abcdef", masked)

    def test_mask_never_contains_the_full_value(self):
        value = "xoxb-1234567890-secret"
        self.assertNotIn(value, mask_secret(value))


class TestSetupState(_EnvCleanCase):
    def test_state_masks_secrets_and_shows_plain_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _temp_config(tmp)
            config.slack_bot_token = "xoxb-1234567890-secret"
            config.zendesk_subdomain = "acme"
            state = setup_state(config)
        slack = next(
            c for c in state["connectors"] if c["name"] == "Slack"
        )
        token = next(
            f for f in slack["fields"] if f["key"] == "SLACK_BOT_TOKEN"
        )
        self.assertTrue(token["set"])
        self.assertEqual(token["value"], "********cret")
        self.assertNotIn("xoxb-1234567890", token["value"])
        zendesk = next(
            c for c in state["connectors"] if c["name"] == "Zendesk"
        )
        subdomain = next(
            f for f in zendesk["fields"] if f["key"] == "ZENDESK_SUBDOMAIN"
        )
        self.assertEqual(subdomain["value"], "acme")
        self.assertFalse(subdomain["secret"])

    def test_state_covers_ten_connectors_ollama_first(self):
        state = setup_state(Config())
        self.assertEqual(len(state["connectors"]), 10)
        self.assertEqual(
            [c["name"] for c in state["connectors"]],
            [
                "Salesforce",
                "Slack",
                "Gong",
                "HubSpot",
                "Gmail",
                "Zoom",
                "Zendesk",
                "Linear",
                "Champify",
                "Google Calendar",
            ],
        )
        self.assertEqual(state["providers"][0]["key"], "ollama")
        self.assertEqual(state["llm"]["provider"], "ollama")


class TestApplyLlmChoice(_EnvCleanCase):
    def test_ollama_writes_base_url_and_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _temp_config(tmp)
            result = apply_llm_choice(config, "ollama")
            self.assertEqual(result["provider"], "ollama")
            self.assertEqual(
                result["base_url"], "http://localhost:11434/v1"
            )
            self.assertEqual(result["model"], "llama3.1:8b")
            self.assertFalse(result["verified"])
            _lines, values = read_env_file(config.env_path)
            self.assertEqual(
                values["HARNESS_LLM_BASE_URL"], "http://localhost:11434/v1"
            )

    def test_unknown_provider_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                apply_llm_choice(_temp_config(tmp), "nope")

    def test_hosted_provider_needs_a_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                apply_llm_choice(_temp_config(tmp), "openai", api_key="  ")

    def test_hosted_provider_writes_key_model_and_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _temp_config(tmp)
            result = apply_llm_choice(
                config, "openai", api_key="sk-test-key-1234"
            )
            self.assertEqual(result["provider"], "openai")
            self.assertEqual(result["model"], "gpt-4o-mini")
            _lines, values = read_env_file(config.env_path)
            self.assertEqual(values["HARNESS_LLM_API_KEY"], "sk-test-key-1234")

    def test_custom_needs_a_base_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                apply_llm_choice(_temp_config(tmp), "custom", base_url=" ")


class TestApplyConnectorValues(_EnvCleanCase):
    def test_writes_keys_to_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _temp_config(tmp)
            result = apply_connector_values(
                config, {"SLACK_BOT_TOKEN": "xoxb-test-token"}
            )
            self.assertEqual(result["written"], ["SLACK_BOT_TOKEN"])
            _lines, values = read_env_file(config.env_path)
            self.assertEqual(values["SLACK_BOT_TOKEN"], "xoxb-test-token")
            # The key name is reported; the secret value is never returned.
            self.assertNotIn("xoxb-test-token", str(result))

    def test_blank_value_leaves_the_key_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _temp_config(tmp)
            apply_connector_values(
                config, {"SLACK_BOT_TOKEN": "xoxb-test-token"}
            )
            result = apply_connector_values(
                config, {"SLACK_BOT_TOKEN": "   "}
            )
            self.assertEqual(result["written"], [])
            _lines, values = read_env_file(config.env_path)
            self.assertEqual(values["SLACK_BOT_TOKEN"], "xoxb-test-token")

    def test_unknown_key_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _temp_config(tmp)
            with self.assertRaises(ValueError):
                apply_connector_values(
                    config, {"EVIL_KEY": "x", "SLACK_BOT_TOKEN": "y"}
                )
            self.assertFalse(os.path.exists(config.env_path))


class TestSetupEndpoints(_EnvCleanCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = _temp_config(self.tmp.name)
        self.client = TestClient(create_app(self.config))

    def test_get_setup_masks_and_lists_connectors(self):
        res = self.client.get("/api/setup")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["llm"]["provider"], "ollama")
        self.assertEqual(len(body["connectors"]), 10)
        gcal = next(
            c for c in body["connectors"] if c["name"] == "Google Calendar"
        )
        self.assertFalse(gcal["configured"])
        self.assertEqual(len(gcal["fields"]), 3)

    def test_post_connectors_saves_and_masks_on_readback(self):
        res = self.client.post(
            "/api/setup/connectors",
            json={"values": {"SLACK_BOT_TOKEN": "xoxb-1234567890-secret"}},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["written"], ["SLACK_BOT_TOKEN"])
        state = self.client.get("/api/setup").json()
        slack = next(
            c for c in state["connectors"] if c["name"] == "Slack"
        )
        token = next(
            f for f in slack["fields"] if f["key"] == "SLACK_BOT_TOKEN"
        )
        self.assertEqual(token["value"], "********cret")

    def test_post_connectors_rejects_unknown_key(self):
        res = self.client.post(
            "/api/setup/connectors",
            json={"values": {"EVIL_KEY": "x"}},
        )
        self.assertEqual(res.status_code, 400)

    def test_post_llm_saves_choice(self):
        res = self.client.post(
            "/api/setup/llm",
            json={"provider": "ollama", "model": "llama3.1:8b"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["provider"], "ollama")
        self.assertFalse(res.json()["verified"])

    def test_post_llm_rejects_unknown_provider(self):
        res = self.client.post(
            "/api/setup/llm", json={"provider": "nope"}
        )
        self.assertEqual(res.status_code, 400)

    def test_settings_view_is_in_the_page(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn('data-view="settings"', res.text)
        self.assertIn('id="view-settings"', res.text)
        self.assertIn('id="llm-form"', res.text)
        self.assertIn('id="connectors"', res.text)
        self.assertIn("/belay-icon.png", res.text)


if __name__ == "__main__":
    unittest.main()
