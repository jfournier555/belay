"""LLM client interface for the harness.

The harness is model-agnostic. Any LLM that speaks the OpenAI-compatible
chat-completions API works: Ollama, OpenAI, or any other endpoint, by
pointing HARNESS_LLM_BASE_URL at it. The router is meant to use a small,
cheap model; the agent drafts use whatever HARNESS_LLM_MODEL is set to.

The default LLM is Ollama, self-hosted and local, so a new user gets
real responses out of the box with no API key. Resolution order in
get_llm (v1.12.0):
- --stub (or HARNESS_STUB=1) forces the stub, always.
- An explicitly configured hosted provider (a non-Ollama base URL
  plus an API key, e.g. picked in the setup wizard) wins: the choice
  the user made is not overridden by a locally running Ollama.
- Otherwise the default chain: Ollama if reachable at
  http://localhost:11434 (OpenAI-compatible /v1 endpoint) -> a
  configured API key provider -> the stub. An API key with no
  explicit base URL is treated as an OpenAI key (the pre-1.12.0
  default). The stub is the labeled fallback, never the silent
  default: stub drafts say so in their header.

Two implementations ship in v1:
- OpenAICompatibleLLM: real HTTP client (httpx) against /v1/chat/completions.
  Needs HARNESS_LLM_API_KEY for a hosted provider; Ollama runs with
  a dummy key (Ollama ignores it).
- StubLLM: deterministic, no network, no key. It does NOT reason. It takes
  the prompt the agent built, extracts the gathered context, and returns a
  clearly-marked stub draft so the harness can be demoed and tested end to
  end without an LLM. Never mistake stub output for real AI output.

Use get_llm(config) to pick: Ollama first, then a key provider, then stub.
"""

from __future__ import annotations

import os
from typing import Protocol
from urllib.parse import urlparse

import httpx

from harness.paths import cli_invocation

from .config import (
    OLLAMA_API_BASE,
    OLLAMA_BASE_URL,
    OLLAMA_DEFAULT_MODEL,
    OPENAI_BASE_URL,
    OPENAI_DEFAULT_MODEL,
    Config,
)

# Marker separating a stub draft's summary from the full gathered
# context. The dashboard splits the draft here: the summary stays in
# the card, the context goes behind an expandable debug view. A real
# LLM draft never contains it.
STUB_CONTEXT_MARKER = "--- Full context (debug) ---"

# A stub summary line is one tool per line, truncated: long enough to
# name what was gathered and its key facts, short enough to read.
_STUB_LINE_LIMIT = 220
# One scalar fact inside a summary line is cut here, so a long
# subject or excerpt cannot push the line past its limit on its own.
_STUB_FACT_LIMIT = 80

# Ollama ignores the key on its OpenAI-compatible endpoint; any
# non-empty value works. "ollama" says what it is.
OLLAMA_DUMMY_KEY = "ollama"

# Reachability probes run on every plain `run` (get_llm resolves the
# default chain), so the timeout stays short: a refused connection to
# localhost is instant, a hung listener costs at most this.
REACHABILITY_TIMEOUT = 2.0

# A completion against local Ollama gets a long timeout: the first
# call loads the model into memory, and a slow machine can take
# minutes before the first token, plus a long draft at a few tokens
# a second. (This VM's 0.5b model runs ~4.5 t/s; a renewal draft is
# ~1,300 tokens, so one call can take five minutes here.) Hosted
# providers keep the 60s default: a hung API call should fail fast,
# not hang the run.
OLLAMA_TIMEOUT = 600.0


def _env_proxy() -> str | None:
    """The egress proxy from the environment, if one is set. Read
    straight from the environment, not through httpx's own parsing:
    a NO_PROXY carrying bracketed IPv6 entries ([::1]) makes httpx
    0.28.1 raise InvalidURL before any request is made, so any
    client that lets httpx read the proxy env vars breaks."""
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )


def http_client(timeout: float, url: str) -> httpx.Client:
    """An httpx client for `url` that works in sandboxed and plain
    environments alike. Loopback targets (Ollama, any local server)
    are never proxied. External targets use the environment's egress
    proxy when one is set, direct otherwise. trust_env is always
    off: httpx's own NO_PROXY parsing is the thing that breaks."""
    host = urlparse(url).hostname or ""
    if host in ("localhost", "127.0.0.1", "::1"):
        return httpx.Client(timeout=timeout, trust_env=False)
    proxy = _env_proxy()
    if proxy is None:
        return httpx.Client(timeout=timeout, trust_env=False)
    return httpx.Client(timeout=timeout, trust_env=False, proxy=proxy)


class LLMClient(Protocol):
    """Minimal interface an LLM must satisfy for the harness."""

    def complete(
        self, prompt: str, system: str | None = None, model: str | None = None
    ) -> str:
        """Return a completion for the prompt. `system` is an optional
        system message. `model` overrides the client's default model."""
        ...


def _truncate(text: str, limit: int) -> str:
    """Cut `text` to `limit` chars at a word boundary, with an
    ellipsis when anything was cut. Short text passes through."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut + "…"


def _split_depth_zero(text: str, sep: str) -> list[str]:
    """Split `text` on `sep` where the separator sits at bracket
    depth 0 and outside quotes. A separator inside a dict, a list,
    or a quoted string (a Slack excerpt, a subject line) does not
    split: the rendered tool results nest those everywhere."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote is not None:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(text):
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch in "[{(":
            depth += 1
            buf.append(ch)
        elif ch in "]})":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif depth == 0 and text.startswith(sep, i):
            parts.append("".join(buf))
            buf = []
            i += len(sep)
            continue
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [part.strip() for part in parts if part.strip()]


def _unquote(raw: str) -> str:
    """A repr'd scalar as plain text: a quoted string unquotes, a
    number or bare word passes through."""
    import ast

    raw = raw.strip()
    if len(raw) >= 2 and raw[0] in ("'", '"'):
        try:
            value = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return raw
        return str(value) if not isinstance(value, str) else value
    return raw


def _dict_scalars(text: str, limit: int = 2) -> list[tuple[str, str]]:
    """The first `limit` scalar key: value pairs from a dict literal,
    headline fields first. A case reads as (subject, status), an
    opportunity as (name, stage), not as whichever fields happen
    to come first in the record. Nested values are skipped, never
    rendered: the point is the headline facts, not the record. An
    unparseable dict yields nothing instead of raising."""
    import ast

    # Headline order: what a human scans for first in a record.
    _HEADLINE = (
        "name",
        "subject",
        "title",
        "excerpt",
        "author",
        "channel",
        "stage",
        "status",
        "priority",
        "amount",
        "id",
    )
    inner = text.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    found: list[tuple[str, str]] = []
    for part in _split_depth_zero(inner, ", "):
        if ": " not in part:
            continue
        key_raw, value_raw = part.split(": ", 1)
        value_raw = value_raw.strip()
        if value_raw.startswith(("[", "{")):
            continue
        try:
            key = ast.literal_eval(key_raw.strip())
            value = ast.literal_eval(value_raw)
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, (dict, list)):
            continue
        found.append((str(key), str(value)))

    def _rank(pair: tuple[str, str]) -> int:
        try:
            return _HEADLINE.index(pair[0])
        except ValueError:
            return len(_HEADLINE)

    found.sort(key=_rank)
    return found[:limit]


def _summarize_tool_value(value: str) -> str:
    """One-line summary of one tool's rendered result: scalar facts
    kept, record lists replaced by a count plus the first record's
    headline fields, no raw dicts. A list of records reads as
    `opportunities: 2 records (name: 'Acme Corp - Renewal FY27',
    stage: 'Negotiation')`, not as the records themselves."""
    kept: list[str] = []
    for segment in _split_depth_zero(value, "; "):
        if ": " not in segment:
            kept.append(_truncate(segment, _STUB_FACT_LIMIT))
            continue
        key, rest = segment.split(": ", 1)
        key = key.strip()
        rest = rest.strip()
        if not rest or rest in ("[]", "{}", "None"):
            kept.append("%s: none" % key)
            continue
        if rest.startswith("["):
            inner = rest[1:-1].strip() if rest.endswith("]") else rest[1:]
            items = _split_depth_zero(inner, ", ") if inner else []
            if not items:
                kept.append("%s: none" % key)
                continue
            head = ""
            first = items[0].strip()
            if first.startswith("{"):
                scalars = _dict_scalars(first)
                if scalars:
                    head = " (" + ", ".join(
                        "%s: %s" % (name, _truncate(val, 40))
                        for name, val in scalars
                    ) + ")"
            kept.append(
                "%s: %d record%s%s"
                % (key, len(items), "" if len(items) == 1 else "s", head)
            )
        elif rest.startswith("{"):
            scalars = _dict_scalars(rest)
            if scalars:
                kept.append(
                    "%s: %s"
                    % (
                        key,
                        ", ".join(
                            "%s: %s" % (name, _truncate(val, 40))
                            for name, val in scalars
                        ),
                    )
                )
            else:
                kept.append("%s: set" % key)
        else:
            kept.append(
                "%s: %s" % (key, _truncate(_unquote(rest), _STUB_FACT_LIMIT))
            )
    return "; ".join(kept)


def _summarize_context(context: str) -> list[str]:
    """The gathered context as one summary line per tool. Lines that
    are not a tool result (a continuation of a multi-line value)
    are skipped: the summary covers the tool, and the full context
    is preserved behind the debug marker."""
    lines = []
    for line in context.splitlines():
        line = line.strip()
        if not line.startswith("- ") or ": " not in line[2:]:
            continue
        tool, value = line[2:].split(": ", 1)
        summary = _summarize_tool_value(value.strip())
        lines.append(
            "- %s: %s" % (tool.strip(), _truncate(summary, _STUB_LINE_LIMIT))
        )
    return lines


class StubLLM:
    """Deterministic stand-in for a real LLM.

    Does not reason. It returns a draft built from the context section of
    the prompt, clearly marked as a stub, so the pipeline (router -> agent
    -> tools -> draft -> approval queue) can be exercised without an API key.
    """

    # The setup command is the invocation the user actually has:
    # `belay setup` in the packaged app, `python -m harness.run
    # setup` from source. Computed at import: sys.frozen is set
    # before any module loads in a packaged app.
    STUB_HEADER = (
        "[STUB DRAFT -- no LLM available: Ollama is not reachable at "
        "http://localhost:11434 and no API key is configured. Run "
        "`%s setup` to enable real responses.]" % cli_invocation()
    )

    def complete(
        self, prompt: str, system: str | None = None, model: str | None = None
    ) -> str:
        # Pull the task and the context block out of the prompt. The
        # draft body is a compact summary, one line per tool: the
        # full gathered context used to be the body, and a human
        # reads that wall of record dicts as word salad. The full
        # context is still in the draft, behind the debug marker,
        # where the dashboard shows it as an expandable view.
        task = _extract_between(prompt, "Task:", "Context:")
        context = _extract_after(prompt, "Context:")
        lines = [self.STUB_HEADER, ""]
        if task:
            lines.append("Task: " + task.strip())
            lines.append("")
        if context:
            lines.append("Gathered from the tools:")
            lines.extend(_summarize_context(context))
            lines.append("")
        lines.append(
            "A real LLM would turn the summary above into prose here. "
            "Start Ollama (or wire up an API key) to replace this stub."
        )
        if context:
            lines.append("")
            lines.append(STUB_CONTEXT_MARKER)
            lines.append(context.strip())
        return "\n".join(lines)


def _extract_between(prompt: str, start_marker: str, end_marker: str) -> str:
    """Text between start_marker and end_marker, or after start_marker to
    the end when end_marker is absent."""
    start = prompt.find(start_marker)
    if start == -1:
        return ""
    rest = prompt[start + len(start_marker):]
    end = rest.find(end_marker)
    if end != -1:
        rest = rest[:end]
    # Drop the instruction line that sits between the task and the context.
    kept = [
        line
        for line in rest.splitlines()
        if line.strip()
        and not line.strip().startswith("Write a concise draft")
    ]
    return "\n".join(kept).strip()


def _extract_after(prompt: str, marker: str) -> str:
    """Text after `marker` to the end of the prompt."""
    idx = prompt.find(marker)
    if idx == -1:
        return ""
    return prompt[idx + len(marker):].strip()


class OpenAICompatibleLLM:
    """Real LLM client for any OpenAI-compatible chat-completions endpoint.

    Works with OpenAI, Ollama, vLLM, and any other server that accepts
    POST {base_url}/chat/completions with an OpenAI-shaped body.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_DEFAULT_MODEL,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def complete(
        self, prompt: str, system: str | None = None, model: str | None = None
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {
            "model": model or self.model,
            "messages": messages,
            "temperature": 0.3,
        }
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        }
        with http_client(self.timeout, self.base_url) as client:
            resp = client.post(
                self.base_url + "/chat/completions",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "Unexpected chat-completions response shape: %r" % (data,)
            ) from exc


def is_ollama_base_url(base_url: str) -> bool:
    """True when the base URL points at an Ollama server: the
    OpenAI-compatible /v1 endpoint on Ollama's default port, 11434.
    A custom Ollama host (http://192.168.1.5:11434/v1) counts."""
    try:
        return urlparse(base_url).port == 11434
    except ValueError:
        return False


def ollama_reachable(
    base_url: str = OLLAMA_BASE_URL, timeout: float = REACHABILITY_TIMEOUT
) -> bool:
    """True when the Ollama server behind `base_url` answers its
    OpenAI-compatible models list. A 200 proves the server answers.
    It does not prove the configured model is pulled: get_llm checks
    that separately before picking Ollama. Any failure (not running,
    refused, timeout, non-200) is False: the caller falls back down
    the chain instead of failing."""
    try:
        with http_client(timeout, base_url) as client:
            resp = client.get(base_url.rstrip("/") + "/models")
        return 200 <= resp.status_code < 300
    except Exception:
        return False


def ollama_models(
    api_base: str = OLLAMA_API_BASE, timeout: float = REACHABILITY_TIMEOUT
) -> list[str] | None:
    """Names of the models Ollama has pulled, via its native /api/tags
    endpoint. None when the server is unreachable or the response
    does not parse: the caller treats that as unknown, not empty."""
    try:
        with http_client(timeout, api_base) as client:
            resp = client.get(api_base.rstrip("/") + "/api/tags")
        if not 200 <= resp.status_code < 300:
            return None
        models = resp.json().get("models") or []
        return [m.get("name", "") for m in models if isinstance(m, dict)]
    except Exception:
        return None


def _model_pulled(models: list[str] | None, model: str) -> bool:
    """True when `model` is among Ollama's pulled models.

    None (unknown: the tags list did not answer) is treated as
    pulled: the server just answered, so the caller keeps the old
    behavior rather than second-guessing it. An exact match wins;
    a model named without a tag also matches the pulled `:latest`
    (the same rule the setup wizard's readiness check uses).
    """
    if models is None:
        return True
    return any(name == model or name == model + ":latest" for name in models)


def get_llm(config: Config) -> LLMClient:
    """Resolve the LLM for this run (v1.12.0 default chain).

    - stub mode (HARNESS_STUB=1 or --stub) forces the stub.
    - An explicitly configured hosted provider (a non-Ollama base
      URL plus an API key) wins over a locally running Ollama: a
      choice the user made in the setup wizard is not overridden.
    - Otherwise Ollama if reachable at its base URL and the
      configured model is pulled, then a configured API key provider
      (an API key with no explicit base URL is treated as an OpenAI
      key), then the stub."""
    if config.stub_mode:
        return StubLLM()
    base = config.llm_base_url.rstrip("/")
    if not is_ollama_base_url(base) and config.llm_api_key:
        return OpenAICompatibleLLM(
            api_key=config.llm_api_key,
            base_url=base,
            model=config.llm_model,
        )
    probe_url = base if is_ollama_base_url(base) else OLLAMA_BASE_URL
    if ollama_reachable(probe_url):
        # Reachable is not usable: the server answers, but a missing
        # model makes every chat call 404. Check the pulled models
        # first; without the model, fall through to a key provider
        # or the stub instead of crashing the run.
        api_base = probe_url[: -len("/v1")] if probe_url.endswith("/v1") else probe_url
        if _model_pulled(ollama_models(api_base), config.llm_model):
            return OpenAICompatibleLLM(
                api_key=OLLAMA_DUMMY_KEY,
                base_url=probe_url,
                model=config.llm_model,
                timeout=OLLAMA_TIMEOUT,
            )
    if config.llm_api_key:
        # Ollama is down and a key is configured, but the base URL
        # is Ollama's: the key has no provider of its own, so it is
        # treated as an OpenAI key (the pre-1.12.0 default).
        return OpenAICompatibleLLM(
            api_key=config.llm_api_key,
            base_url=OPENAI_BASE_URL,
            model=config.llm_model
            if config.llm_model != OLLAMA_DEFAULT_MODEL
            else OPENAI_DEFAULT_MODEL,
        )
    return StubLLM()
