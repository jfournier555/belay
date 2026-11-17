"""Configuration for the CS agent harness.

All configuration comes from environment variables. No secrets are stored in
code. If a credential is missing, the affected connector falls back to stub
mode (realistic sample data, no API call) instead of failing, so the harness
can be demoed and tested without any accounts.

Environment variables:
    HARNESS_LLM_BASE_URL   Base URL for an OpenAI-compatible chat API.
                           Default: https://api.openai.com/v1
    HARNESS_LLM_API_KEY    API key for the LLM. If unset, the stub LLM is used.
    HARNESS_LLM_MODEL      Model for agent drafts. Default: gpt-4o-mini.
    HARNESS_ROUTER_MODEL   Model for the router (small/cheap). Default: same
                           as HARNESS_LLM_MODEL.
    HARNESS_STUB           Set to "1" to force stub mode for all connectors,
                           even if credentials are present.
    SALESFORCE_INSTANCE_URL  e.g. https://mycompany.my.salesforce.com
    SALESFORCE_ACCESS_TOKEN  OAuth access token for the Salesforce REST API.
    SLACK_BOT_TOKEN          Slack bot token (xoxb-...).
    HARNESS_APPROVALS_FILE   Path to the approval-queue JSON file.
                             Default: approvals.json (in the working dir).
    HARNESS_TOKEN_BUDGET     Max estimated tokens per job. Default: 8000.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    """Harness configuration, loaded from the environment."""

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    router_model: str | None = None
    stub_mode: bool = False
    salesforce_instance_url: str | None = None
    salesforce_access_token: str | None = None
    slack_bot_token: str | None = None
    approvals_file: str = "approvals.json"
    token_budget_per_job: int = 8000

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from environment variables."""
        return cls(
            llm_base_url=os.environ.get(
                "HARNESS_LLM_BASE_URL", "https://api.openai.com/v1"
            ),
            llm_api_key=os.environ.get("HARNESS_LLM_API_KEY") or None,
            llm_model=os.environ.get("HARNESS_LLM_MODEL", "gpt-4o-mini"),
            router_model=os.environ.get("HARNESS_ROUTER_MODEL") or None,
            stub_mode=os.environ.get("HARNESS_STUB", "") == "1",
            salesforce_instance_url=os.environ.get("SALESFORCE_INSTANCE_URL")
            or None,
            salesforce_access_token=os.environ.get("SALESFORCE_ACCESS_TOKEN")
            or None,
            slack_bot_token=os.environ.get("SLACK_BOT_TOKEN") or None,
            approvals_file=os.environ.get(
                "HARNESS_APPROVALS_FILE", "approvals.json"
            ),
            token_budget_per_job=int(
                os.environ.get("HARNESS_TOKEN_BUDGET", "8000")
            ),
        )

    @property
    def effective_router_model(self) -> str:
        """The model the router uses. Defaults to the agent model."""
        return self.router_model or self.llm_model

    def llm_configured(self) -> bool:
        """True when a real LLM can be called (an API key is present)."""
        return bool(self.llm_api_key)

    def salesforce_configured(self) -> bool:
        """True when Salesforce credentials are present and stub mode is off."""
        return bool(
            self.salesforce_instance_url
            and self.salesforce_access_token
            and not self.stub_mode
        )

    def slack_configured(self) -> bool:
        """True when a Slack token is present and stub mode is off."""
        return bool(self.slack_bot_token and not self.stub_mode)
