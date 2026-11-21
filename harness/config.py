"""Configuration for the CS agent harness.

All configuration comes from environment variables. No secrets are stored in
code. If a credential is missing, the affected connector falls back to stub
mode (realistic sample data, no API call) instead of failing, so the harness
can be demoed and tested without any accounts.

Environment variables:
    HARNESS_LLM_BASE_URL   Base URL for an OpenAI-compatible chat API.
                           Default: http://localhost:11434/v1 (Ollama,
                           local, no key). Point it at a hosted
                           provider to use that provider instead.
    HARNESS_LLM_API_KEY    API key for a hosted LLM provider. If unset
                           and Ollama is not reachable, the stub LLM
                           is used.
    HARNESS_LLM_MODEL      Model for agent drafts. Default: llama3.1:8b
                           (Ollama). Each hosted provider has its own
                           sensible default, set by the setup wizard.
    HARNESS_ROUTER_MODEL   Model for the router (small/cheap). Default: same
                           as HARNESS_LLM_MODEL.
    HARNESS_STUB           Set to "1" to force stub mode for all connectors,
                           even if credentials are present.
    SALESFORCE_INSTANCE_URL  e.g. https://mycompany.my.salesforce.com
    SALESFORCE_ACCESS_TOKEN  OAuth access token for the Salesforce REST API.
    SLACK_BOT_TOKEN          Slack bot token (xoxb-...).
    GONG_ACCESS_TOKEN        Gong API access token (Bearer auth).
    GONG_API_BASE_URL        Gong API base URL. Default: https://api.gong.io/v2
                             (regional instances take the form
                             https://{region}.api.gong.io/v2).
    HUBSPOT_ACCESS_TOKEN     HubSpot private app access token (Bearer auth).
    HUBSPOT_PORTAL_ID        HubSpot portal (hub) id, for deep links to
                             records. Optional: without it the harness still
                             gathers, but builds no HubSpot links. Falls
                             back to the account-info API when unset.
    GMAIL_CLIENT_ID          Gmail OAuth client id (Desktop app type).
    GMAIL_CLIENT_SECRET      Gmail OAuth client secret.
    GMAIL_REFRESH_TOKEN      Gmail OAuth refresh token, minted once with
                             the readonly scope
                             https://www.googleapis.com/auth/gmail.readonly.
                             The connector exchanges it for an access token
                             on every call. Read-only by scope: it can
                             never send or delete mail.
    ZOOM_ACCOUNT_ID          Zoom server-to-server OAuth account id.
    ZOOM_CLIENT_ID           Zoom server-to-server OAuth client id.
    ZOOM_CLIENT_SECRET       Zoom server-to-server OAuth client secret.
                             The connector exchanges the triple for an
                             access token (grant_type=account_credentials).
                             The Zoom app needs the recording:read:admin
                             scope.
    ZENDESK_SUBDOMAIN        Zendesk subdomain, e.g. acme for
                             acme.zendesk.com.
    ZENDESK_EMAIL            Email of the Zendesk API user.
    ZENDESK_API_TOKEN        Zendesk API token. Auth is HTTP Basic with
                             username "{email}/token" and password the
                             token.
    LINEAR_API_KEY           Linear API key (Settings > API > Personal
                             API keys). Auth is the raw key in the
                             Authorization header.
    CHAMPIFY_API_KEY         Champify API key. Auth is the raw key as a
                             Bearer token.
    CHAMPIFY_API_BASE_URL    Champify API base URL. Default:
                             https://api.champify.io/v1
    HARNESS_APPROVALS_FILE   Path to the approval-queue JSON file.
                             Default: approvals.json (in the working dir).
    HARNESS_MEMORY_FILE      Path to the per-account memory JSON file.
                             Default: memory.json (in the working dir).
                             Memory is local only; nothing in it is ever
                             sent anywhere.
    HARNESS_SCHEDULES_FILE   Path to the schedule JSON file (v1.4.0).
                             Default: schedules.json (in the working
                             dir). One object per schedule: agent,
                             account, cadence, time, timezone.
    HARNESS_MCP_SERVERS_FILE Path to the external MCP server JSON file
                             (v1.5.0). Default: mcp-servers.json (in
                             the working dir). One object per server:
                             name, transport (stdio or sse), command
                             and args, or url. A missing file means no
                             external servers.
    HARNESS_ESCALATIONS_FILE
                             Path to the escalation-state JSON file
                             (v1.7.0). Default: escalations.json (in
                             the working dir). One record per risk
                             signal per account: open, escalated,
                             resolved.
    HARNESS_TOKEN_BUDGET     Max estimated tokens per job. Default: 8000.
    HARNESS_LOADED_HOURLY_RATE
                             Loaded hourly rate in dollars, used by
                             value-finder to turn hours saved into dollars
                             (hours saved x loaded rate). Never assumed:
                             without it (and without --hourly-rate on the
                             run) time saved is reported in hours only,
                             not dollars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# The default LLM is Ollama, self-hosted and local: a new user gets
# real responses out of the box with no API key. The OpenAI defaults
# are kept for the legacy case (an API key with no explicit base URL
# is treated as an OpenAI key, the pre-1.12.0 default).
OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_API_BASE = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL = "llama3.1:8b"
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"


def _parse_rate(raw: str | None) -> float | None:
    """Parse a loaded hourly rate from the environment. A missing,
    unparseable, zero, or negative rate is None: the rate is never
    assumed, and without one time saved is reported in hours only,
    not dollars."""
    if raw is None:
        return None
    try:
        rate = float(raw)
    except ValueError:
        return None
    return rate if rate > 0 else None


@dataclass
class Config:
    """Harness configuration, loaded from the environment."""

    llm_base_url: str = OLLAMA_BASE_URL
    llm_api_key: str | None = None
    llm_model: str = OLLAMA_DEFAULT_MODEL
    router_model: str | None = None
    stub_mode: bool = False
    salesforce_instance_url: str | None = None
    salesforce_access_token: str | None = None
    slack_bot_token: str | None = None
    gong_access_token: str | None = None
    gong_api_base_url: str = "https://api.gong.io/v2"
    hubspot_access_token: str | None = None
    hubspot_portal_id: str | None = None
    gmail_client_id: str | None = None
    gmail_client_secret: str | None = None
    gmail_refresh_token: str | None = None
    zoom_account_id: str | None = None
    zoom_client_id: str | None = None
    zoom_client_secret: str | None = None
    zendesk_subdomain: str | None = None
    zendesk_email: str | None = None
    zendesk_api_token: str | None = None
    linear_api_key: str | None = None
    champify_api_key: str | None = None
    champify_api_base_url: str = "https://api.champify.io/v1"
    approvals_file: str = "approvals.json"
    memory_file: str = "memory.json"
    schedules_file: str = "schedules.json"
    mcp_servers_file: str = "mcp-servers.json"
    escalations_file: str = "escalations.json"
    token_budget_per_job: int = 8000
    loaded_hourly_rate: float | None = None

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from environment variables.

        A .env file in the working directory (or above it) is loaded
        first, so values written there take effect without exporting
        anything. Real environment variables always win: load_dotenv
        never overrides one that is already set."""
        load_dotenv()
        return cls(
            llm_base_url=os.environ.get(
                "HARNESS_LLM_BASE_URL", OLLAMA_BASE_URL
            ),
            llm_api_key=os.environ.get("HARNESS_LLM_API_KEY") or None,
            llm_model=os.environ.get("HARNESS_LLM_MODEL", OLLAMA_DEFAULT_MODEL),
            router_model=os.environ.get("HARNESS_ROUTER_MODEL") or None,
            stub_mode=os.environ.get("HARNESS_STUB", "") == "1",
            salesforce_instance_url=os.environ.get("SALESFORCE_INSTANCE_URL")
            or None,
            salesforce_access_token=os.environ.get("SALESFORCE_ACCESS_TOKEN")
            or None,
            slack_bot_token=os.environ.get("SLACK_BOT_TOKEN") or None,
            gong_access_token=os.environ.get("GONG_ACCESS_TOKEN") or None,
            gong_api_base_url=os.environ.get(
                "GONG_API_BASE_URL", "https://api.gong.io/v2"
            ),
            hubspot_access_token=os.environ.get("HUBSPOT_ACCESS_TOKEN")
            or None,
            hubspot_portal_id=os.environ.get("HUBSPOT_PORTAL_ID") or None,
            gmail_client_id=os.environ.get("GMAIL_CLIENT_ID") or None,
            gmail_client_secret=os.environ.get("GMAIL_CLIENT_SECRET")
            or None,
            gmail_refresh_token=os.environ.get("GMAIL_REFRESH_TOKEN")
            or None,
            zoom_account_id=os.environ.get("ZOOM_ACCOUNT_ID") or None,
            zoom_client_id=os.environ.get("ZOOM_CLIENT_ID") or None,
            zoom_client_secret=os.environ.get("ZOOM_CLIENT_SECRET") or None,
            zendesk_subdomain=os.environ.get("ZENDESK_SUBDOMAIN") or None,
            zendesk_email=os.environ.get("ZENDESK_EMAIL") or None,
            zendesk_api_token=os.environ.get("ZENDESK_API_TOKEN") or None,
            linear_api_key=os.environ.get("LINEAR_API_KEY") or None,
            champify_api_key=os.environ.get("CHAMPIFY_API_KEY") or None,
            champify_api_base_url=os.environ.get(
                "CHAMPIFY_API_BASE_URL", "https://api.champify.io/v1"
            ),
            approvals_file=os.environ.get(
                "HARNESS_APPROVALS_FILE", "approvals.json"
            ),
            memory_file=os.environ.get("HARNESS_MEMORY_FILE", "memory.json"),
            schedules_file=os.environ.get(
                "HARNESS_SCHEDULES_FILE", "schedules.json"
            ),
            mcp_servers_file=os.environ.get(
                "HARNESS_MCP_SERVERS_FILE", "mcp-servers.json"
            ),
            escalations_file=os.environ.get(
                "HARNESS_ESCALATIONS_FILE", "escalations.json"
            ),
            token_budget_per_job=int(
                os.environ.get("HARNESS_TOKEN_BUDGET", "8000")
            ),
            loaded_hourly_rate=_parse_rate(
                os.environ.get("HARNESS_LOADED_HOURLY_RATE")
            ),
        )

    @property
    def effective_router_model(self) -> str:
        """The model the router uses. Defaults to the agent model."""
        return self.router_model or self.llm_model

    def llm_configured(self) -> bool:
        """True when an API key for a hosted provider is present.
        Ollama needs no key, so this says nothing about Ollama: use
        get_llm in llm.py to resolve the default chain (Ollama if
        reachable, then a key provider, then the stub)."""
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

    def gong_configured(self) -> bool:
        """True when a Gong token is present and stub mode is off."""
        return bool(self.gong_access_token and not self.stub_mode)

    def hubspot_configured(self) -> bool:
        """True when a HubSpot token is present and stub mode is off."""
        return bool(self.hubspot_access_token and not self.stub_mode)

    def gmail_configured(self) -> bool:
        """True when the Gmail OAuth triple is present and stub mode is off."""
        return bool(
            self.gmail_client_id
            and self.gmail_client_secret
            and self.gmail_refresh_token
            and not self.stub_mode
        )

    def zoom_configured(self) -> bool:
        """True when the Zoom server-to-server OAuth triple is present
        and stub mode is off."""
        return bool(
            self.zoom_account_id
            and self.zoom_client_id
            and self.zoom_client_secret
            and not self.stub_mode
        )

    def zendesk_configured(self) -> bool:
        """True when the Zendesk token triple is present and stub mode
        is off."""
        return bool(
            self.zendesk_subdomain
            and self.zendesk_email
            and self.zendesk_api_token
            and not self.stub_mode
        )

    def linear_configured(self) -> bool:
        """True when a Linear API key is present and stub mode is off."""
        return bool(self.linear_api_key and not self.stub_mode)

    def champify_configured(self) -> bool:
        """True when a Champify API key is present and stub mode is off."""
        return bool(self.champify_api_key and not self.stub_mode)
