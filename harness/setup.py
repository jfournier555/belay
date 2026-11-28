"""First-run wizard: demo data or guided setup.

The first thing a new user hits after clone and install. It asks exactly
one question with two answers:

    1) Fill with Demo data?
    2) Run Setup Wizard?

Demo data seeds the file-backed stores (approvals, memory, schedules,
escalations) with stub content, so the dashboard shows a lived-in
harness instead of empty views. The demo also installs the sample
accounts CSV (connectors/csv/sample_accounts.csv, ten fictional
accounts) next to the stores, and the demo agents gather account
records from it, so the demo proves the machine runs end to end on
file data without live accounts. The demo drafts use the default LLM
chain: Ollama when it is reachable (real responses), the stub when
it is not, labeled as stub in the output and in the drafts. Setup
walks the LLM provider choice plus the ten connectors, writes them
to .env, and verifies each one where a live check exists. When
Ollama is missing, the wizard offers to install it (official
distribution only), explicit yes, showing the exact command first.

Boundaries, all deliberate:
- The wizard never prints a secret value anywhere: not in prompts,
  not in logs, not in the summary. Secrets are read with getpass
  (no echo) and only ever written to .env.
- Demo data is marked. A marker file (demo-seed.json, next to the
  approvals file) lists every seeded record id. Seeding refuses to
  run over real data without explicit confirmation (default no), and
  clearing demo data removes only the marked records, never real ones.
- Nothing here auto-runs. `setup` is the entry point; a bare `run`
  with nothing configured prints a one-line hint to run setup and
  then runs anyway. The wizard never blocks a run.
- .env is loaded by Config.from_env (python-dotenv, no override of
  real environment variables), so values the wizard writes take
  effect on the next run without the user exporting anything.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time

try:
    # Arrow-key editing in input(): without readline, the packaged
    # binary echoes arrow keys raw (^[[A) instead of moving the
    # cursor. Importing it here also makes the packager bundle it.
    # Absent on Windows and in minimal builds: the wizard still
    # works, keys are just typed, not edited.
    import readline  # noqa: F401
except ImportError:
    pass
from dataclasses import replace
from datetime import datetime, timezone

from harness.agents import AGENTS
from harness.approvals import ApprovalQueue
from harness.config import (
    OLLAMA_BASE_URL,
    OLLAMA_DEFAULT_MODEL,
    OPENAI_BASE_URL,
    OPENAI_DEFAULT_MODEL,
    Config,
)
from harness.escalations import EscalationStore
from harness.llm import (
    http_client,
    is_ollama_base_url,
    ollama_models,
    ollama_reachable,
)
from harness.memory import AccountMemory
from harness.paths import cli_invocation
from harness.scheduler import ScheduleStore

DEMO_ACCOUNT = "Acme Corp"

# LLM providers the OpenAI-compatible client can actually talk to,
# in the order the wizard lists them. Ollama first: it is the
# default, local, no key. Each entry carries its default base URL
# and default model in code, not hardcoded in prompts. Custom
# covers anything else: the wizard asks for base URL, key, model.
PROVIDERS: dict[str, dict[str, str | bool | None]] = {
    "ollama": {
        "label": "Ollama (local, no key)",
        "base_url": OLLAMA_BASE_URL,
        "model": OLLAMA_DEFAULT_MODEL,
        "needs_key": False,
    },
    "openai": {
        "label": "OpenAI",
        "base_url": OPENAI_BASE_URL,
        "model": OPENAI_DEFAULT_MODEL,
        "needs_key": True,
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "needs_key": True,
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.1-8b-instant",
        "needs_key": True,
    },
    "together": {
        "label": "Together",
        "base_url": "https://api.together.xyz/v1",
        "model": "meta-llama/Llama-3.1-8B-Instruct-Turbo",
        "needs_key": True,
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "needs_key": True,
    },
    "mistral": {
        "label": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-small-latest",
        "needs_key": True,
    },
    "custom": {
        "label": "Custom endpoint",
        "base_url": None,
        "model": None,
        "needs_key": True,
    },
}
PROVIDER_ORDER = [
    "ollama",
    "openai",
    "openrouter",
    "groq",
    "together",
    "deepseek",
    "mistral",
    "custom",
]

# (section, env key, prompt label, secret?, default shown)
# A default is shown so the user knows what the harness falls back to;
# a blank answer leaves the key unset and the default applies in code.
FIELD_SPECS: list[tuple[str, str, str, bool, str | None]] = [
    ("Salesforce", "SALESFORCE_INSTANCE_URL", "Salesforce instance URL", False, None),
    ("Salesforce", "SALESFORCE_ACCESS_TOKEN", "Salesforce access token", True, None),
    ("Slack", "SLACK_BOT_TOKEN", "Slack bot token", True, None),
    ("Gong", "GONG_ACCESS_TOKEN", "Gong access token", True, None),
    ("Gong", "GONG_API_BASE_URL", "Gong API base URL", False, "https://api.gong.io/v2"),
    ("HubSpot", "HUBSPOT_ACCESS_TOKEN", "HubSpot access token", True, None),
    ("HubSpot", "HUBSPOT_PORTAL_ID", "HubSpot portal id (optional)", False, None),
    ("Gmail", "GMAIL_CLIENT_ID", "Gmail client id", False, None),
    ("Gmail", "GMAIL_CLIENT_SECRET", "Gmail client secret", True, None),
    ("Gmail", "GMAIL_REFRESH_TOKEN", "Gmail refresh token", True, None),
    ("Zoom", "ZOOM_ACCOUNT_ID", "Zoom account id", False, None),
    ("Zoom", "ZOOM_CLIENT_ID", "Zoom client id", False, None),
    ("Zoom", "ZOOM_CLIENT_SECRET", "Zoom client secret", True, None),
    ("Zendesk", "ZENDESK_SUBDOMAIN", "Zendesk subdomain", False, None),
    ("Zendesk", "ZENDESK_EMAIL", "Zendesk API user email", False, None),
    ("Zendesk", "ZENDESK_API_TOKEN", "Zendesk API token", True, None),
    ("Linear", "LINEAR_API_KEY", "Linear API key", True, None),
    ("Google Calendar", "GCAL_CLIENT_ID", "Google Calendar client id", False, None),
    ("Google Calendar", "GCAL_CLIENT_SECRET", "Google Calendar client secret", True, None),
    ("Google Calendar", "GCAL_REFRESH_TOKEN", "Google Calendar refresh token", True, None),
]

# Section -> Config method that says whether it is live. The LLM
# section is not here: it is a provider choice, not a credential
# check (Ollama needs no key), and the wizard handles it specially.
SECTION_CONFIGURED = {
    "Salesforce": "salesforce_configured",
    "Slack": "slack_configured",
    "Gong": "gong_configured",
    "HubSpot": "hubspot_configured",
    "Gmail": "gmail_configured",
    "Zoom": "zoom_configured",
    "Zendesk": "zendesk_configured",
    "Linear": "linear_configured",
    "Google Calendar": "gcal_configured",
}

# Raw credential fields: any one set means something is configured,
# even if the full triple for that connector is not complete yet.
_CREDENTIAL_FIELDS = (
    "llm_api_key",
    "salesforce_access_token",
    "slack_bot_token",
    "gong_access_token",
    "hubspot_access_token",
    "gmail_refresh_token",
    "zoom_client_secret",
    "zendesk_api_token",
    "linear_api_key",
    "gcal_refresh_token",
)


def credentials_present(config: Config) -> bool:
    """True when any credential is set (raw fields, not the
    configured() methods: a half-filled connector still counts as
    something configured, and stub mode must not hide it)."""
    return any(bool(getattr(config, field, None)) for field in _CREDENTIAL_FIELDS)


def stores_empty(config: Config) -> bool:
    """True when every file-backed store holds nothing. A missing or
    corrupt file reads as empty, same as the stores themselves."""
    if ApprovalQueue(config.approvals_file).list_all():
        return False
    if AccountMemory(config.memory_file).accounts():
        return False
    if ScheduleStore(config.schedules_file).list():
        return False
    if EscalationStore(config.escalations_file).list_all():
        return False
    return True


def is_first_run(
    config: Config,
    queue: ApprovalQueue | None = None,
    memory: AccountMemory | None = None,
    schedules: ScheduleStore | None = None,
    escalations: EscalationStore | None = None,
) -> bool:
    """First run: no credentials configured anywhere and every store
    empty. Stores may be passed in (the dashboard already built
    them); otherwise they are built from the config paths."""
    if credentials_present(config):
        return False
    if queue is not None and queue.list_all():
        return False
    if memory is not None and memory.accounts():
        return False
    if schedules is not None and schedules.list():
        return False
    if escalations is not None and escalations.list_all():
        return False
    if queue is None and memory is None and schedules is None and escalations is None:
        return stores_empty(config)
    return True


def llm_available(config: Config) -> bool:
    """True when a run will get real responses: Ollama is reachable
    or an API key is configured. A false here means stub drafts."""
    return bool(config.llm_api_key) or ollama_reachable(
        config.llm_base_url
        if is_ollama_base_url(config.llm_base_url)
        else OLLAMA_BASE_URL
    )


def print_setup_hint(config: Config, out=None, interactive: bool = True) -> bool:
    """One line pointing at setup, printed only for an interactive run
    with nothing configured: no LLM available (no Ollama, no API key)
    and no connectors. Never blocks anything; returns True when the
    hint was printed."""
    if not interactive or credentials_present(config) or llm_available(config):
        return False
    (out or print)(
        "Tip: nothing is configured yet (no Ollama, no API key). Run "
        "`%s setup` for demo data or the setup wizard." % cli_invocation()
    )
    return True


# ---------------------------------------------------------------------------
# .env file handling: preserve everything, replace in place, append new.
# ---------------------------------------------------------------------------


def read_env_file(path: str) -> tuple[list[str], dict[str, str]]:
    """Read a .env file. Returns (lines, values): lines verbatim
    (comments and blanks kept), values mapping KEY -> value for KEY=
    lines. A missing file reads as empty."""
    lines: list[str] = []
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return lines, values
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        key = key.strip()
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return lines, values


def _quote_env_value(value: str) -> str:
    """Quote a .env value only when it needs it (spaces, #, quotes).
    Single quotes, with an embedded single quote escaped the
    shell way, so python-dotenv reads it back unchanged."""
    if not value or any(c in value for c in " \t#\"'") or value != value.strip():
        return "'" + value.replace("'", "'\"'\"'") + "'"
    return value


def write_env_values(path: str, updates: dict[str, str]) -> list[str]:
    """Write keys into a .env file, preserving everything else:
    existing keys are replaced in place, missing keys are appended,
    comments and blank lines are untouched. Returns the keys written.
    The file is created when it does not exist."""
    lines, _values = read_env_file(path)
    written: list[str] = []
    remaining = dict(updates)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.partition("=")[0].strip()
        if key in remaining:
            lines[index] = "%s=%s" % (key, _quote_env_value(remaining.pop(key)))
            written.append(key)
    for key, value in remaining.items():
        lines.append("%s=%s" % (key, _quote_env_value(value)))
        written.append(key)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return written


# ---------------------------------------------------------------------------
# Live verification: one cheap call per connector, status only.
# ---------------------------------------------------------------------------


def _http_get(url: str, headers: dict | None = None, timeout: float = 10.0):
    # http_client handles the proxy: loopback never proxied, external
    # targets use the environment's egress proxy when one is set.
    with http_client(timeout, url) as client:
        return client.get(url, headers=headers or {})


def _http_post(
    url: str,
    headers: dict | None = None,
    json_body: dict | None = None,
    data: dict | None = None,
    auth=None,
    timeout: float = 10.0,
):
    with http_client(timeout, url) as client:
        return client.post(
            url, headers=headers or {}, json=json_body, data=data, auth=auth
        )


def _ok(response) -> bool:
    """A 2xx status is verified. Anything else, or any exception
    from the caller, is not verified: the harness falls back to stub
    data for that source until it works."""
    return 200 <= response.status_code < 300


def verify_llm(config: Config) -> bool:
    """The chosen LLM answers. Ollama: the server answers its models
    list (no key). A hosted provider: the endpoint answers a models
    list with the key. No key and Ollama down is not verified: the
    run falls back to stub drafts."""
    base = config.llm_base_url.rstrip("/")
    if is_ollama_base_url(base):
        return ollama_reachable(base)
    if not config.llm_api_key:
        return False
    try:
        response = _http_get(
            base + "/models",
            headers={"Authorization": "Bearer " + config.llm_api_key},
        )
        return _ok(response)
    except Exception:
        return False


def verify_salesforce(config: Config) -> bool:
    """The instance answers the API version list with the token."""
    if not config.salesforce_instance_url or not config.salesforce_access_token:
        return False
    try:
        response = _http_get(
            config.salesforce_instance_url.rstrip("/") + "/services/data/",
            headers={"Authorization": "Bearer " + config.salesforce_access_token},
        )
        return _ok(response)
    except Exception:
        return False


def verify_slack(config: Config) -> bool:
    """auth.test answers ok with the bot token."""
    if not config.slack_bot_token:
        return False
    try:
        response = _http_get(
            "https://slack.com/api/auth.test",
            headers={"Authorization": "Bearer " + config.slack_bot_token},
        )
        return _ok(response) and bool(response.json().get("ok"))
    except Exception:
        return False


def verify_gong(config: Config) -> bool:
    """A one-day calls/extensive window answers with the token. The
    same endpoint the connector gathers from, so a pass here means
    the gather path works."""
    if not config.gong_access_token:
        return False
    try:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        body = {
            "filter": {
                "fromDateTime": (now - timedelta(days=1)).isoformat(),
                "toDateTime": now.isoformat(),
            }
        }
        response = _http_post(
            config.gong_api_base_url.rstrip("/") + "/v2/calls/extensive",
            headers={"Authorization": "Bearer " + config.gong_access_token},
            json_body=body,
        )
        return _ok(response)
    except Exception:
        return False


def verify_hubspot(config: Config) -> bool:
    """One company reads with the private app token."""
    if not config.hubspot_access_token:
        return False
    try:
        response = _http_get(
            "https://api.hubapi.com/crm/v3/objects/companies?limit=1",
            headers={"Authorization": "Bearer " + config.hubspot_access_token},
        )
        return _ok(response)
    except Exception:
        return False


def verify_gmail(config: Config) -> bool:
    """The refresh token exchanges for an access token. No mailbox
    is read: the exchange alone proves the OAuth triple works."""
    if not (
        config.gmail_client_id
        and config.gmail_client_secret
        and config.gmail_refresh_token
    ):
        return False
    try:
        response = _http_post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": config.gmail_client_id,
                "client_secret": config.gmail_client_secret,
                "refresh_token": config.gmail_refresh_token,
                "grant_type": "refresh_token",
            },
        )
        return _ok(response)
    except Exception:
        return False


def verify_zoom(config: Config) -> bool:
    """The server-to-server triple exchanges for an access token.
    No recordings are read: the exchange alone proves it works."""
    if not (
        config.zoom_account_id
        and config.zoom_client_id
        and config.zoom_client_secret
    ):
        return False
    try:
        response = _http_post(
            "https://zoom.us/oauth/token?grant_type=account_credentials",
            auth=(config.zoom_client_id, config.zoom_client_secret),
            data={"account_id": config.zoom_account_id},
        )
        return _ok(response)
    except Exception:
        return False


def verify_zendesk(config: Config) -> bool:
    """The current user reads with the token triple."""
    if not (
        config.zendesk_subdomain
        and config.zendesk_email
        and config.zendesk_api_token
    ):
        return False
    try:
        response = _http_get(
            "https://%s.zendesk.com/api/v2/users/me.json" % config.zendesk_subdomain,
            auth=("%s/token" % config.zendesk_email, config.zendesk_api_token),
        )
        return _ok(response)
    except Exception:
        return False


def verify_linear(config: Config) -> bool:
    """The viewer query answers with the API key."""
    if not config.linear_api_key:
        return False
    try:
        response = _http_post(
            "https://api.linear.app/graphql",
            headers={"Authorization": config.linear_api_key},
            json_body={"query": "{ viewer { id } }"},
        )
        return _ok(response)
    except Exception:
        return False


def verify_gcal(config: Config) -> bool:
    """The refresh token exchanges for an access token. No calendar
    is read: the exchange alone proves the OAuth triple works."""
    if not (
        config.gcal_client_id
        and config.gcal_client_secret
        and config.gcal_refresh_token
    ):
        return False
    try:
        response = _http_post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": config.gcal_client_id,
                "client_secret": config.gcal_client_secret,
                "refresh_token": config.gcal_refresh_token,
                "grant_type": "refresh_token",
            },
        )
        return _ok(response)
    except Exception:
        return False


VERIFY_CHECKS = {
    "LLM": verify_llm,
    "Salesforce": verify_salesforce,
    "Slack": verify_slack,
    "Gong": verify_gong,
    "HubSpot": verify_hubspot,
    "Gmail": verify_gmail,
    "Zoom": verify_zoom,
    "Zendesk": verify_zendesk,
    "Linear": verify_linear,
    "Google Calendar": verify_gcal,
}


# ---------------------------------------------------------------------------
# LLM provider choice: Ollama default, hosted providers, custom endpoint.
# ---------------------------------------------------------------------------


def detect_provider(config: Config) -> str:
    """Which provider the config points at, by base URL. A base URL
    that matches no known provider is Custom; the Ollama default
    (and any :11434 base URL) is Ollama."""
    base = config.llm_base_url.rstrip("/")
    if is_ollama_base_url(base):
        return "ollama"
    for key in PROVIDER_ORDER:
        provider_base = PROVIDERS[key]["base_url"]
        if provider_base and base == str(provider_base).rstrip("/"):
            return key
    return "custom"


def ollama_cli_present() -> bool:
    """True when the `ollama` CLI is on PATH (needed to pull a model)."""
    return shutil.which("ollama") is not None


def _ollama_readiness(
    config: Config,
    input_fn,
    out,
    model: str | None = None,
    interactive: bool = True,
    run_fn=None,
) -> tuple[bool, bool]:
    """The Ollama check the wizard and the demo path share: is the
    server reachable, and is the model pulled? When the server is up
    but the model is missing and the CLI is there and this is
    interactive, it offers to pull it. Prints plain-language status
    either way. Returns (reachable, model_present). Never blocks: a
    missing model or a declined pull just means stub drafts until
    the model is pulled or the server starts."""
    run_fn = run_fn or subprocess.run
    model = model or config.llm_model
    base = config.llm_base_url.rstrip("/")
    probe_url = base if is_ollama_base_url(base) else OLLAMA_BASE_URL
    if not ollama_reachable(probe_url):
        out("Ollama is not reachable at %s." % probe_url)
        return False, False
    api_base = probe_url[: -len("/v1")] if probe_url.endswith("/v1") else probe_url
    models = ollama_models(api_base)
    present = models is not None and any(
        name == model or name == model + ":latest" for name in models
    )
    if present:
        out("Ollama is up and %s is pulled." % model)
        return True, True
    out("Ollama is up, but %s is not pulled yet." % model)
    if ollama_cli_present() and interactive:
        if _ask_yes_no("Pull %s now?" % model, input_fn, default=True):
            # stdin is DEVNULL, not the wizard's terminal: a long
            # pull (4.9 GB for the default model) must not consume
            # keystrokes the user types while it runs, or the next
            # prompt reads them as its answer and the wizard looks
            # hung. The blank line after it redraws the prompt
            # cleanly: the pull's progress output leaves the cursor
            # mid-line.
            result = run_fn(
                ["ollama", "pull", model], stdin=subprocess.DEVNULL
            )
            out("")
            if result.returncode == 0:
                models = ollama_models(api_base)
                present = models is not None and any(
                    name == model or name == model + ":latest"
                    for name in models
                )
                if present:
                    out("%s pulled." % model)
                    return True, True
            out("The pull did not complete; the model is still missing.")
    else:
        out("Pull it with: ollama pull %s" % model)
    return True, False


def _ollama_install_commands(system: str, which_fn) -> list[str] | None:
    """The install commands for this OS, official distribution only:
    ollama.com, Homebrew, winget. Nothing third-party, no other
    download source. None when there is no automated path for this
    system: the wizard says so and falls back instead of guessing."""
    if system == "Linux":
        return ["curl -fsSL https://ollama.com/install.sh | sh"]
    if system == "Darwin":
        if which_fn("brew"):
            return ["brew install --cask ollama"]
        tmp = os.path.join(tempfile.gettempdir(), "Ollama-darwin.zip")
        return [
            "curl -fsSL -o %s https://ollama.com/download/Ollama-darwin.zip"
            % tmp,
            "unzip -o %s -d /Applications" % tmp,
            "open /Applications/Ollama.app",
        ]
    if system == "Windows":
        if which_fn("winget"):
            return ["winget install Ollama.Ollama"]
        return [
            "curl -fsSL -o %TEMP%\\OllamaSetup.exe "
            "https://ollama.com/download/OllamaSetup.exe",
            "%TEMP%\\OllamaSetup.exe",
        ]
    return None


def _start_ollama_server(popen_fn) -> None:
    """Start `ollama serve` detached, so the wizard can continue
    while the server runs. stdout/stderr go nowhere: the server
    logs to its own log, not the wizard."""
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    popen_fn(["ollama", "serve"], **kwargs)


def _wait_for_ollama(
    probe_url: str, attempts: int = 5, sleep_fn=None, reachable_fn=None
) -> bool:
    """Poll until the Ollama server answers, or the attempts run
    out. The server takes a few seconds to come up after a start
    or an install. reachable_fn is injectable for the dashboard
    wizard, which polls with a fake in tests."""
    sleep_fn = sleep_fn or time.sleep
    reachable_fn = reachable_fn or ollama_reachable
    for _ in range(attempts):
        if reachable_fn(probe_url):
            return True
        sleep_fn(1.0)
    return False


def _ollama_install_offer(
    config: Config,
    input_fn,
    out,
    run_fn=None,
    popen_fn=None,
    system_fn=None,
    which_fn=None,
    sleep_fn=None,
) -> bool:
    """Ollama is missing (server not reachable). Offer to get it
    running: start the server when the CLI is there, install it
    when it is not. Explicit yes only, never silent: the exact
    command is shown before it runs. After an install, the server
    is started if it is not running, reachability is verified, and
    the caller then offers the model pull. Returns True when
    Ollama is reachable afterwards. A decline or a failed install
    prints plainly and returns False: the wizard falls back to an
    API key or stub, never blocks."""
    run_fn = run_fn or subprocess.run
    popen_fn = popen_fn or subprocess.Popen
    system_fn = system_fn or platform.system
    which_fn = which_fn or shutil.which
    sleep_fn = sleep_fn or time.sleep
    base = config.llm_base_url.rstrip("/")
    probe_url = base if is_ollama_base_url(base) else OLLAMA_BASE_URL
    if ollama_reachable(probe_url):
        return True
    if which_fn("ollama"):
        out("Ollama is installed but the server is not running.")
        out("About to run: ollama serve")
        if not _ask_yes_no(
            "Start the Ollama server now?", input_fn, default=True
        ):
            out(
                "Not starting Ollama; drafts will be stub-labeled "
                "until it runs."
            )
            return False
        _start_ollama_server(popen_fn)
        if _wait_for_ollama(probe_url, sleep_fn=sleep_fn):
            out("Ollama is up.")
            return True
        out("Ollama did not start. Start it by hand with: ollama serve")
        return False
    system = system_fn()
    commands = _ollama_install_commands(system, which_fn)
    if commands is None:
        out(
            "Ollama is not installed, and there is no automated "
            "install path for this system (%s). Get it at "
            "ollama.com/download." % system
        )
        return False
    out(
        "Ollama is not installed. The wizard can install it for you "
        "(official distribution only)."
    )
    for cmd in commands:
        out("About to run: %s" % cmd)
    if not _ask_yes_no("Install Ollama now?", input_fn, default=False):
        out(
            "Skipping the Ollama install. Pick a hosted provider, or "
            "continue with stub drafts."
        )
        return False
    for cmd in commands:
        # Same stdin rule as the model pull: an install script must
        # not eat the wizard's input while it runs.
        result = run_fn(cmd, shell=True, stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            out("The install failed at: %s" % cmd)
            out(
                "Install Ollama by hand from ollama.com/download, or "
                "pick a hosted provider, or continue with stub drafts."
            )
            return False
    # Installed. The Linux script and the macOS/Windows installers
    # start the server where they can; start it by hand when they
    # did not.
    if _wait_for_ollama(probe_url, sleep_fn=sleep_fn):
        out("Ollama is up.")
        return True
    _start_ollama_server(popen_fn)
    if _wait_for_ollama(probe_url, sleep_fn=sleep_fn):
        out("Ollama is up.")
        return True
    out(
        "Ollama installed, but the server is not running yet. Start "
        "it with: ollama serve"
    )
    return False


def _wizard_llm_step(
    config: Config,
    input_fn,
    out,
    getpass_fn,
    run_fn=None,
    popen_fn=None,
    system_fn=None,
    which_fn=None,
    sleep_fn=None,
) -> dict[str, str]:
    """The LLM step of the setup wizard: a provider list, Ollama the
    default. Per provider it prompts for what that provider needs
    and nothing more: Ollama gets a reachability check and a model
    pull offer; a hosted provider gets its API key and model (with
    that provider's sensible default); custom gets base URL, key,
    and model. The choice, base URL, model, and key are returned as
    .env updates. Never prints a secret value. Never blocks: an
    unreachable Ollama offers another provider or keeps Ollama
    anyway (the run falls back to stub until it starts)."""
    current = detect_provider(config)
    while True:
        out("== LLM: %s" % PROVIDERS[current]["label"])
        for index, key in enumerate(PROVIDER_ORDER, start=1):
            mark = " (default)" if key == "ollama" else ""
            out("  %d) %s%s" % (index, PROVIDERS[key]["label"], mark))
        answer = input_fn(
            "Choose 1-%d [%d]: "
            % (len(PROVIDER_ORDER), PROVIDER_ORDER.index(current) + 1)
        ).strip()
        if not answer:
            choice = current
        elif answer.isdigit() and 1 <= int(answer) <= len(PROVIDER_ORDER):
            choice = PROVIDER_ORDER[int(answer) - 1]
        else:
            out("Please answer 1-%d." % len(PROVIDER_ORDER))
            continue
        provider = PROVIDERS[choice]
        if choice == "ollama":
            # Staying on Ollama keeps the current model; switching
            # to Ollama resets it to the Ollama default (a hosted
            # model name would never pull here).
            model = (
                config.llm_model
                if current == "ollama"
                else OLLAMA_DEFAULT_MODEL
            )
            reachable, _present = _ollama_readiness(
                config, input_fn, out, model=model, run_fn=run_fn
            )
            if not reachable:
                # Offer to get Ollama running (start it, or install
                # it); a decline or a failure never blocks the
                # wizard. An install that lands re-runs the readiness
                # check, so the model pull offer still happens.
                if _ollama_install_offer(
                    config,
                    input_fn,
                    out,
                    run_fn=run_fn,
                    popen_fn=popen_fn,
                    system_fn=system_fn,
                    which_fn=which_fn,
                    sleep_fn=sleep_fn,
                ):
                    _ollama_readiness(
                        config, input_fn, out, model=model, run_fn=run_fn
                    )
                elif not _ask_yes_no(
                    "Keep Ollama as the provider anyway?",
                    input_fn,
                    default=False,
                ):
                    continue
            return {
                "HARNESS_LLM_BASE_URL": OLLAMA_BASE_URL,
                "HARNESS_LLM_MODEL": model,
            }
        if choice == "custom":
            base_url = input_fn("  Custom base URL: ").strip()
            if not base_url:
                if current == "custom":
                    base_url = config.llm_base_url
                else:
                    out("No base URL entered; skipping the LLM step.")
                    return {}
            key = getpass_fn("  Custom API key: ").strip()
            if not key:
                if current == "custom" and config.llm_api_key:
                    key = config.llm_api_key
                else:
                    out("No key entered; skipping the LLM step.")
                    return {}
            model = input_fn(
                "  Model [%s]: " % config.llm_model
            ).strip() or config.llm_model
            return {
                "HARNESS_LLM_BASE_URL": base_url,
                "HARNESS_LLM_API_KEY": key,
                "HARNESS_LLM_MODEL": model,
            }
        # A hosted provider: base URL and default model come from the
        # table; only the key and the model are prompted. Staying on
        # the same provider keeps the current model; switching resets
        # it to that provider's default.
        default_model = (
            config.llm_model
            if current == choice
            else str(provider["model"])
        )
        key = getpass_fn("  %s API key: " % provider["label"]).strip()
        if not key:
            if current == choice and config.llm_api_key:
                key = config.llm_api_key
            else:
                out("No key entered; skipping the LLM step.")
                return {}
        model = (
            input_fn("  Model [%s]: " % default_model).strip() or default_model
        )
        updates = {
            "HARNESS_LLM_BASE_URL": str(provider["base_url"]),
            "HARNESS_LLM_API_KEY": key,
            "HARNESS_LLM_MODEL": model,
        }
        return updates


# Connector credential fields: cleared for demo runs, so the demo
# agents gather stub data even when the user has real credentials
# configured. The LLM fields are left alone: demo drafts use the
# default chain (Ollama when reachable, stub-labeled when not).
_DEMO_CLEAR_FIELDS = (
    "salesforce_instance_url",
    "salesforce_access_token",
    "slack_bot_token",
    "gong_access_token",
    "hubspot_access_token",
    "gmail_client_id",
    "gmail_client_secret",
    "gmail_refresh_token",
    "zoom_account_id",
    "zoom_client_id",
    "zoom_client_secret",
    "zendesk_subdomain",
    "zendesk_email",
    "zendesk_api_token",
    "linear_api_key",
    "gcal_client_id",
    "gcal_client_secret",
    "gcal_refresh_token",
)


def _demo_config(config: Config) -> Config:
    """A config for demo runs: connectors stubbed, LLM on the
    default chain. Never touches the user's real accounts; the
    drafts are real when Ollama is reachable, stub-labeled when
    it is not. An explicit stub mode (HARNESS_STUB=1, --stub) is
    left alone: a deliberate stub request stays stub."""
    return replace(config, **{field: None for field in _DEMO_CLEAR_FIELDS})


# ---------------------------------------------------------------------------
# Dashboard setup (v1.16.0): the same wizard, as a web flow.
#
# The dashboard Settings view is the default first-run path: LLM
# choice and connector setup without a terminal. These helpers are
# the shared core the dashboard endpoints and the CLI wizard both
# use: state (with secrets masked, never in full), and apply
# (validated, written to .env, verified where a check exists).
# Secrets are never printed and never returned in full.
# ---------------------------------------------------------------------------

# Env key -> Config attribute, for reading a field's current value.
_ENV_TO_ATTR = {
    "SALESFORCE_INSTANCE_URL": "salesforce_instance_url",
    "SALESFORCE_ACCESS_TOKEN": "salesforce_access_token",
    "SLACK_BOT_TOKEN": "slack_bot_token",
    "GONG_ACCESS_TOKEN": "gong_access_token",
    "GONG_API_BASE_URL": "gong_api_base_url",
    "HUBSPOT_ACCESS_TOKEN": "hubspot_access_token",
    "HUBSPOT_PORTAL_ID": "hubspot_portal_id",
    "GMAIL_CLIENT_ID": "gmail_client_id",
    "GMAIL_CLIENT_SECRET": "gmail_client_secret",
    "GMAIL_REFRESH_TOKEN": "gmail_refresh_token",
    "ZOOM_ACCOUNT_ID": "zoom_account_id",
    "ZOOM_CLIENT_ID": "zoom_client_id",
    "ZOOM_CLIENT_SECRET": "zoom_client_secret",
    "ZENDESK_SUBDOMAIN": "zendesk_subdomain",
    "ZENDESK_EMAIL": "zendesk_email",
    "ZENDESK_API_TOKEN": "zendesk_api_token",
    "LINEAR_API_KEY": "linear_api_key",
    "GCAL_CLIENT_ID": "gcal_client_id",
    "GCAL_CLIENT_SECRET": "gcal_client_secret",
    "GCAL_REFRESH_TOKEN": "gcal_refresh_token",
}


def mask_secret(value: str) -> str:
    """Mask a secret for display: eight stars plus the last four
    characters, so the user can tell which key it is without ever
    seeing the full value. An empty value stays empty (not set)."""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * 8 + value[-4:]


def setup_state(config: Config) -> dict:
    """What the dashboard Settings view renders: the LLM provider,
    model, and whether a key is set (masked), and per connector the
    fields with masked secret values and the configured state. No
    secret is ever returned in full."""
    connectors = []
    for section in dict.fromkeys(spec[0] for spec in FIELD_SPECS):
        method = SECTION_CONFIGURED[section]
        fields = []
        for _sec, key, label, secret, _default in FIELD_SPECS:
            if _sec != section:
                continue
            raw = str(getattr(config, _ENV_TO_ATTR[key], None) or "")
            fields.append(
                {
                    "key": key,
                    "label": label,
                    "secret": secret,
                    "set": bool(raw),
                    "value": mask_secret(raw) if secret else raw,
                }
            )
        connectors.append(
            {
                "name": section,
                "configured": bool(getattr(config, method)()),
                "fields": fields,
            }
        )
    return {
        "llm": {
            "provider": detect_provider(config),
            "base_url": config.llm_base_url,
            "model": config.llm_model,
            "api_key_set": bool(config.llm_api_key),
            "api_key_masked": mask_secret(config.llm_api_key or ""),
            "token_budget_per_job": config.token_budget_per_job,
            "token_budget_unlimited": config.effective_token_budget is None,
        },
        "providers": [
            {
                "key": key,
                "label": str(PROVIDERS[key]["label"]),
                "needs_key": bool(PROVIDERS[key]["needs_key"]),
            }
            for key in PROVIDER_ORDER
        ],
        "connectors": connectors,
    }


def _reload_config(config: Config) -> Config:
    """Reload the config from .env after a write, keeping the
    caller's store paths: .env never carries store paths, so the
    reloaded config would otherwise lose a temp dir's paths."""
    from dotenv import load_dotenv

    load_dotenv(config.env_path)
    reloaded = Config.from_env()
    return replace(
        reloaded,
        approvals_file=config.approvals_file,
        memory_file=config.memory_file,
        schedules_file=config.schedules_file,
        escalations_file=config.escalations_file,
        mcp_servers_file=config.mcp_servers_file,
        env_path=config.env_path,
    )


def apply_llm_choice(
    config: Config,
    provider: str,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> dict:
    """Apply an LLM choice from the dashboard: validate the provider,
    write the base URL, model, and key to .env, reload, and verify
    where a check exists. Returns {"provider", "base_url", "model",
    "verified"}. Raises ValueError on an unknown provider or a
    missing value the provider needs."""
    provider = (provider or "").strip()
    if provider not in PROVIDERS:
        raise ValueError("Unknown LLM provider: %r." % provider)
    if provider == "ollama":
        updates = {
            "HARNESS_LLM_BASE_URL": OLLAMA_BASE_URL,
            "HARNESS_LLM_MODEL": (model or "").strip() or OLLAMA_DEFAULT_MODEL,
        }
    elif provider == "custom":
        base = (base_url or "").strip()
        if not base:
            raise ValueError("A custom endpoint needs a base URL.")
        updates = {
            "HARNESS_LLM_BASE_URL": base,
            "HARNESS_LLM_MODEL": (model or "").strip() or config.llm_model,
        }
        if (api_key or "").strip():
            updates["HARNESS_LLM_API_KEY"] = api_key.strip()
    else:
        key = (api_key or "").strip()
        if not key:
            raise ValueError(
                "%s needs an API key." % PROVIDERS[provider]["label"]
            )
        updates = {
            "HARNESS_LLM_BASE_URL": str(PROVIDERS[provider]["base_url"]),
            "HARNESS_LLM_API_KEY": key,
            "HARNESS_LLM_MODEL": (model or "").strip()
            or str(PROVIDERS[provider]["model"]),
        }
    write_env_values(config.env_path, updates)
    new_config = _reload_config(config)
    return {
        "provider": provider,
        "base_url": new_config.llm_base_url,
        "model": new_config.llm_model,
        "verified": verify_llm(new_config),
    }


def parse_token_budget(value) -> int:
    """Validate a per-job token limit from the dashboard: a whole
    number of at least 1. Raises ValueError on anything else, so a
    bad value is a 400 with nothing written."""
    text = str(value).strip() if value is not None else ""
    try:
        limit = int(text)
    except (TypeError, ValueError):
        raise ValueError("Token limit must be a whole number.")
    if limit < 1:
        raise ValueError("Token limit must be at least 1.")
    return limit


def apply_token_budget(config: Config, value) -> dict:
    """Save the per-job token limit from the dashboard: validate,
    write HARNESS_TOKEN_BUDGET to .env, reload. The value is the
    hosted limit; local Ollama never counts tokens, so the effective
    budget is unlimited while Ollama is the LLM. Returns the saved
    limit and the effective state. Raises ValueError on a bad value."""
    limit = parse_token_budget(value)
    write_env_values(config.env_path, {"HARNESS_TOKEN_BUDGET": str(limit)})
    new_config = _reload_config(config)
    return {
        "token_budget_per_job": new_config.token_budget_per_job,
        "effective_token_budget": new_config.effective_token_budget,
        "unlimited": new_config.effective_token_budget is None,
    }


def apply_connector_values(config: Config, values: dict) -> dict:
    """Write connector fields from the dashboard. Only FIELD_SPECS
    keys are accepted: an unknown key raises ValueError. A blank
    value leaves the key unchanged (a secret is never cleared by
    submitting an empty field). Returns {"written": [...],
    "connectors": [configured section names]}. Secrets are written
    to .env only, never returned."""
    allowed = {key for _sec, key, _label, _secret, _d in FIELD_SPECS}
    updates = {}
    for key, value in (values or {}).items():
        if key not in allowed:
            raise ValueError("Unknown connector field: %r." % key)
        text = str(value or "").strip()
        if text:
            updates[key] = text
    written = write_env_values(config.env_path, updates) if updates else []
    new_config = _reload_config(config)
    configured = [
        section
        for section, method in SECTION_CONFIGURED.items()
        if getattr(new_config, method)()
    ]
    return {"written": written, "connectors": configured}


# ---------------------------------------------------------------------------
# The two choices.
# ---------------------------------------------------------------------------


def prompt_setup_choice(input_fn=input, out=None) -> str:
    """Ask the one question with two answers. Loops on bad input.
    Returns "demo" or "wizard"."""
    out = out or print
    out("First run. Two ways in:")
    out("  1) Fill with Demo data")
    out("  2) Run Setup Wizard")
    while True:
        answer = input_fn("Choose 1 or 2: ").strip()
        if answer in ("1", "demo"):
            return "demo"
        if answer in ("2", "wizard", "setup"):
            return "wizard"
        out("Please answer 1 or 2.")


def demo_marker_path(config: Config) -> str:
    """The marker file lives next to the approvals file."""
    return os.path.join(
        os.path.dirname(os.path.abspath(config.approvals_file)),
        "demo-seed.json",
    )


def _read_marker(config: Config) -> dict:
    path = demo_marker_path(config)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_marker(config: Config, marker: dict) -> None:
    path = demo_marker_path(config)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(marker, f, indent=2)
    os.replace(tmp, path)


def _real_records(config: Config, marker: dict) -> dict:
    """Records in the stores that are not demo-seeded: anything the
    user (or another run) put there. Counts per store."""
    demo_approvals = set(marker.get("approvals", []) or [])
    demo_memory = {
        record_id
        for ids in (marker.get("memory", {}) or {}).values()
        for record_id in ids
    }
    demo_schedules = set(marker.get("schedules", []) or [])
    demo_escalations = set(marker.get("escalations", []) or [])
    queue = ApprovalQueue(config.approvals_file)
    memory = AccountMemory(config.memory_file)
    schedules = ScheduleStore(config.schedules_file)
    escalations = EscalationStore(config.escalations_file)
    real = {
        "approvals": sum(
            1 for item in queue.list_all() if item.id not in demo_approvals
        ),
        "memory": sum(
            1
            for account, _count in memory.accounts()
            for record in memory.records_for(account)
            if record.id not in demo_memory
        ),
        "schedules": sum(
            1 for s in schedules.list() if s.id not in demo_schedules
        ),
        "escalations": sum(
            1 for r in escalations.list_all() if r.id not in demo_escalations
        ),
    }
    return {key: count for key, count in real.items() if count}


def _install_sample_csv(config: Config) -> str | None:
    """Install the sample accounts CSV next to the stores, for the
    demo and for first use. Copies connectors/csv/sample_accounts.csv
    to sample_accounts.csv beside the approvals file, only when it is
    not already there, so a user's own file is never clobbered.
    Returns the installed path, or None when the sample is not
    shipped with this install (the packaged app without the data
    file): the demo then runs as before, on stub data."""
    from connectors.csv.tools import SAMPLE_CSV_PATH

    dest = os.path.join(
        os.path.dirname(os.path.abspath(config.approvals_file)),
        "sample_accounts.csv",
    )
    if os.path.exists(dest):
        return dest
    if not os.path.exists(SAMPLE_CSV_PATH):
        return None
    try:
        shutil.copyfile(SAMPLE_CSV_PATH, dest)
    except OSError:
        return None
    return dest


def seed_demo(
    config: Config,
    confirm: bool | None = None,
    input_fn=input,
    out=None,
    interactive: bool = True,
) -> dict:
    """Fill the stores with demo data, so the dashboard shows a
    lived-in harness instead of empty views.

    Runs four agents (renewal-desk, briefer, risk-watcher,
    escalation-manager) against the stub account Acme Corp through
    the real execution path, with the connector credentials cleared
    so they gather stub data even when the user has real accounts
    configured. The drafts use the default LLM chain: Ollama when
    it is reachable (real responses), the stub when it is not, and
    the stub fallback is said plainly in the output and labeled in
    the drafts themselves. The Ollama check (reachable, model
    pulled, pull offer) is the same one the setup wizard runs.
    Then the queue is driven the way a human would: the briefer
    draft is approved, the renewal-desk draft is edited with a
    correction, the other two stay pending. The edit and the
    approval are recorded as memory. A daily briefer schedule
    (stub mode) and the escalation-manager's open escalation
    records complete the picture. Every draft is demo data with
    .invalid links, clearly demo.

    Refuses over real data: when the stores hold records the demo
    marker does not list, seeding stops unless `confirm` is True.
    With `confirm` None (interactive), it asks, defaulting to no.
    Returns a report dict with status seeded | refused |
    already-seeded and the counts.
    """
    from harness.run import execute_job

    out = out or print
    marker = _read_marker(config)
    real = _real_records(config, marker)
    if real:
        if confirm is None:
            out(
                "The stores already hold real data (%s). Seed demo data "
                "anyway? [y/N] "
                % ", ".join("%s: %d" % kv for kv in sorted(real.items()))
            )
            try:
                answer = input_fn("").strip().lower()
            except EOFError:
                answer = ""
            confirm = answer in ("y", "yes")
        if not confirm:
            out(
                "Not seeding: the stores already hold real data (%s). "
                "Re-run with --yes to seed anyway."
                % ", ".join("%s: %d" % kv for kv in sorted(real.items()))
            )
            return {"status": "refused", "real": real}
    if marker and not real:
        # Everything on file is demo-seeded already.
        queue = ApprovalQueue(config.approvals_file)
        seeded_ids = set(marker.get("approvals", []) or [])
        if seeded_ids and all(
            queue.get(item_id) is not None for item_id in seeded_ids
        ):
            return {"status": "already-seeded", "counts": _marker_counts(marker)}
    _reachable, model_present = _ollama_readiness(
        config, input_fn, out, interactive=interactive
    )
    real_drafts = _reachable and model_present
    if not real_drafts:
        out(
            "Demo drafts will be stub-labeled: Ollama is not reachable "
            "or the model is not pulled. Run `%s setup` to enable real "
            "responses." % cli_invocation()
        )
    demo_config = _demo_config(config)
    sample_csv = _install_sample_csv(config)
    if sample_csv is not None:
        # The demo agents gather account records from the sample
        # file, not the stub: the demo proves the machine runs end
        # to end on file data. A real CSV the user configured is
        # never used here; the demo always uses the sample, so it
        # cannot touch real accounts.
        demo_config = replace(demo_config, csv_accounts_file=sample_csv)

    def run_agent(agent: str):
        item, _draft, _agent, _route = execute_job(
            demo_config,
            {"agent": agent, "account": DEMO_ACCOUNT, "text": DEMO_ACCOUNT},
        )
        return item

    renewal = run_agent("renewal-desk")
    briefer = run_agent("briefer")
    risk = run_agent("risk-watcher")
    escalation = run_agent("escalation-manager")
    # The queue loads once at construction, so it is built after the
    # runs: a queue built before them would not see the new drafts,
    # and saving from it would clobber them.
    queue = ApprovalQueue(
        config.approvals_file,
        memory=AccountMemory(config.memory_file),
        escalations=EscalationStore(config.escalations_file),
    )
    queue.approve(briefer.id, note="Demo approval.")
    edited_text = (
        "Demo edit: lead with the renewal date, not the health score. "
        "Acme Corp renews Feb 15 ($120,000), and the SSO fix ships next "
        "week, which unblocks the signature.\n\n" + renewal.draft
    )
    queue.edit(renewal.id, edited_text)
    schedules = ScheduleStore(config.schedules_file)
    schedule = schedules.add(
        {
            "agent": "briefer",
            "account": DEMO_ACCOUNT,
            "cadence": "daily",
            "time": "07:00",
            "timezone": "America/Los_Angeles",
            "stub": True,
        },
        valid_agents=set(AGENTS),
    )
    escalations = EscalationStore(config.escalations_file)
    memory = AccountMemory(config.memory_file)
    memory_ids: dict[str, list[str]] = {}
    for record in memory.records_for(DEMO_ACCOUNT):
        if record.draft_id in (renewal.id, briefer.id):
            memory_ids.setdefault(DEMO_ACCOUNT, []).append(record.id)
    new_marker = {
        "seeded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "approvals": [renewal.id, briefer.id, risk.id, escalation.id],
        "memory": memory_ids,
        "schedules": [schedule.id],
        "escalations": [r.id for r in escalations.for_account(DEMO_ACCOUNT)],
    }
    merged = {
        "seeded_at": new_marker["seeded_at"],
        "approvals": sorted(set(marker.get("approvals", []) or []) | set(new_marker["approvals"])),
        "memory": {
            account: sorted(
                set(marker.get("memory", {}).get(account, []) or [])
                | set(ids)
            )
            for account, ids in {
                **(marker.get("memory", {}) or {}),
                **new_marker["memory"],
            }.items()
        },
        "schedules": sorted(set(marker.get("schedules", []) or []) | set(new_marker["schedules"])),
        "escalations": sorted(set(marker.get("escalations", []) or []) | set(new_marker["escalations"])),
    }
    _write_marker(config, merged)
    counts = {
        "approvals": 4,
        "memory": sum(len(ids) for ids in memory_ids.values()),
        "schedules": 1,
        "escalations": len(new_marker["escalations"]),
    }
    out(
        "Demo data seeded for %s: %d drafts (1 approved, 1 edited, "
        "2 pending), %d memory record(s), 1 daily schedule, %d open "
        "escalation(s). Drafts are %s."
        % (
            DEMO_ACCOUNT,
            counts["approvals"],
            counts["memory"],
            counts["escalations"],
            "real responses from Ollama"
            if real_drafts
            else "stub-labeled (Ollama not reachable)",
        )
    )
    if sample_csv is not None:
        out(
            "Sample accounts installed: %s (10 fictional accounts, "
            "gathered by the demo drafts). Keep using them with "
            "HARNESS_CSV_ACCOUNTS_FILE=%s, or point it at your own CSV."
            % (sample_csv, sample_csv)
        )
    else:
        out(
            "Sample accounts not installed: the sample CSV is not "
            "shipped with this install. The demo drafts used stub data."
        )
    return {
        "status": "seeded",
        "counts": counts,
        "real_drafts": real_drafts,
        "sample_csv": sample_csv,
    }


def _marker_counts(marker: dict) -> dict:
    return {
        "approvals": len(marker.get("approvals", []) or []),
        "memory": sum(len(ids) for ids in (marker.get("memory", {}) or {}).values()),
        "schedules": len(marker.get("schedules", []) or []),
        "escalations": len(marker.get("escalations", []) or []),
    }


def clear_demo(config: Config, out=None) -> dict:
    """Remove exactly the demo-seeded records, nothing else. The
    marker file lists them; records the user added stay put. Returns
    a report dict with status cleared | nothing-to-clear and the
    counts removed."""
    out = out or print
    marker = _read_marker(config)
    if not marker:
        out("No demo data on file.")
        return {"status": "nothing-to-clear", "counts": {}}
    queue = ApprovalQueue(config.approvals_file)
    memory = AccountMemory(config.memory_file)
    schedules = ScheduleStore(config.schedules_file)
    escalations = EscalationStore(config.escalations_file)
    removed = {"approvals": 0, "memory": 0, "schedules": 0, "escalations": 0}
    for item_id in marker.get("approvals", []) or []:
        if queue.remove(item_id):
            removed["approvals"] += 1
    for account, ids in (marker.get("memory", {}) or {}).items():
        for record_id in ids:
            if memory.remove_record(account, record_id):
                removed["memory"] += 1
    for schedule_id in marker.get("schedules", []) or []:
        if schedules.remove(schedule_id):
            removed["schedules"] += 1
    for record_id in marker.get("escalations", []) or []:
        if escalations.remove(record_id):
            removed["escalations"] += 1
    try:
        os.unlink(demo_marker_path(config))
    except OSError:
        pass
    out(
        "Demo data cleared: %d draft(s), %d memory record(s), %d "
        "schedule(s), %d escalation(s) removed. Real records untouched."
        % (
            removed["approvals"],
            removed["memory"],
            removed["schedules"],
            removed["escalations"],
        )
    )
    return {"status": "cleared", "counts": removed}


# ---------------------------------------------------------------------------
# The setup wizard.
# ---------------------------------------------------------------------------


def _ask_yes_no(prompt: str, input_fn, default: bool = False) -> bool:
    """A yes/no question with an explicit default. Blank takes the
    default; anything starting with y is yes, anything starting
    with n is no; anything else asks again."""
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        answer = input_fn(prompt + suffix).strip().lower()
        if not answer:
            return default
        if answer.startswith("y"):
            return True
        if answer.startswith("n"):
            return False


def run_setup_wizard(
    config: Config | None = None,
    input_fn=input,
    out=None,
    getpass_fn=None,
    env_path: str = ".env",
    run_demo: bool = True,
) -> dict:
    """Walk the LLM provider choice plus the nine connectors, writing
    to .env.

    The LLM step first: a provider list with Ollama the default
    (local, no key), then hosted providers, then a custom endpoint.
    Per connector it shows configured or missing, then per field it
    shows already set (and asks whether to replace, default no) or
    prompts for a value (blank skips). Secrets are read with getpass
    and never printed. Existing .env entries are preserved: only keys
    the user provides or replaces are written, everything else in
    the file is untouched. After writing, the config is reloaded and
    each newly configured source is verified live where a check
    exists; a source that cannot be verified reports stub fallback
    honestly. Ends by offering a demo run (stub data, real LLM when
    Ollama is reachable).

    Returns a report: configured, verified, unverified, skipped,
    llm_provider. `config` defaults to Config.from_env() (which
    loads .env)."""
    from dotenv import load_dotenv

    from harness.run import execute_job

    out = out or print
    getpass_fn = getpass_fn or getpass.getpass
    load_dotenv(env_path)
    config = config or Config.from_env()
    _lines, existing = read_env_file(env_path)
    updates: dict[str, str] = {}
    configured: list[str] = []
    skipped: list[str] = []
    llm_updates = _wizard_llm_step(config, input_fn, out, getpass_fn)
    updates.update(llm_updates)
    for section in dict.fromkeys(spec[0] for spec in FIELD_SPECS):
        method = SECTION_CONFIGURED[section]
        state = "configured" if getattr(config, method)() else "missing"
        out("== %s: %s" % (section, state))
        for _sec, key, label, secret, default in FIELD_SPECS:
            if _sec != section:
                continue
            current = os.environ.get(key) or existing.get(key) or ""
            if current:
                out("  %s: already set" % label)
                if not _ask_yes_no("  Replace it?", input_fn, default=False):
                    continue
            prompt = "  %s" % label
            if default:
                prompt += " (default: %s)" % default
            prompt += ": "
            if secret:
                value = getpass_fn(prompt).strip()
            else:
                value = input_fn(prompt).strip()
            if not value:
                skipped.append(key)
                continue
            updates[key] = value
    if updates:
        written = write_env_values(env_path, updates)
        out("Wrote %d key(s) to %s." % (len(written), env_path))
        load_dotenv(env_path)
        # The reload drops non-default store paths the caller passed
        # in (a temp dir in tests); .env never carries store paths,
        # so the caller's paths are the ones to keep.
        reloaded = Config.from_env()
        config = replace(
            reloaded,
            approvals_file=config.approvals_file,
            memory_file=config.memory_file,
            schedules_file=config.schedules_file,
            escalations_file=config.escalations_file,
            mcp_servers_file=config.mcp_servers_file,
        )
    else:
        out("Nothing to write: no values provided.")
    verified: list[str] = []
    unverified: list[str] = []
    # The LLM is always verified: Ollama by reachability, a hosted
    # provider by its models list. A fail is honest stub fallback,
    # never a block.
    if verify_llm(config):
        configured.append("LLM")
        verified.append("LLM")
        out("  LLM: verified live.")
    elif config.llm_api_key:
        configured.append("LLM")
        unverified.append("LLM")
        out("  LLM: configured, could not verify (stub data until it works).")
    else:
        unverified.append("LLM")
        out(
            "  LLM: not verified (Ollama is not reachable and no API "
            "key is set); drafts will be stub-labeled until it runs."
        )
    for section in dict.fromkeys(spec[0] for spec in FIELD_SPECS):
        method = SECTION_CONFIGURED[section]
        if not getattr(config, method)():
            continue
        configured.append(section)
        check = VERIFY_CHECKS.get(section)
        if check is None:
            unverified.append(section)
            out("  %s: configured, no live check (stub fallback until a run works)." % section)
            continue
        if check(config):
            verified.append(section)
            out("  %s: verified live." % section)
        else:
            unverified.append(section)
            detail = "stub data until it works"
            out("  %s: configured, could not verify (%s)." % (section, detail))
    if run_demo and _ask_yes_no(
        "Run a demo job now?", input_fn, default=True
    ):
        demo_config = _demo_config(config)
        item, _draft, agent, _route = execute_job(
            demo_config,
            {"agent": "renewal-desk", "account": DEMO_ACCOUNT, "text": DEMO_ACCOUNT},
        )
        out(
            "Demo draft queued: %s (%s). Review it with: %s approvals "
            "show %s" % (item.id, agent.display_name, cli_invocation(), item.id)
        )
    return {
        "configured": configured,
        "verified": verified,
        "unverified": unverified,
        "skipped": skipped,
        "llm_provider": detect_provider(config),
    }
