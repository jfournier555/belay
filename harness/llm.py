"""LLM client interface for the harness.

The harness is model-agnostic. Any LLM that speaks the OpenAI-compatible
chat-completions API works: OpenAI, Ollama, vLLM, or any other endpoint, by
pointing HARNESS_LLM_BASE_URL at it. The router is meant to use a small,
cheap model; the agent drafts use whatever HARNESS_LLM_MODEL is set to.

Two implementations ship in v1:
- OpenAICompatibleLLM: real HTTP client (httpx) against /v1/chat/completions.
  Needs HARNESS_LLM_API_KEY to run.
- StubLLM: deterministic, no network, no key. It does NOT reason. It takes
  the prompt the agent built, extracts the gathered context, and returns a
  clearly-marked stub draft so the harness can be demoed and tested end to
  end without an LLM. Never mistake stub output for real AI output.

Use get_llm(config) to pick: real client when a key is present, stub otherwise.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from .config import Config


class LLMClient(Protocol):
    """Minimal interface an LLM must satisfy for the harness."""

    def complete(
        self, prompt: str, system: str | None = None, model: str | None = None
    ) -> str:
        """Return a completion for the prompt. `system` is an optional
        system message. `model` overrides the client's default model."""
        ...


class StubLLM:
    """Deterministic stand-in for a real LLM.

    Does not reason. It returns a draft built from the context section of
    the prompt, clearly marked as a stub, so the pipeline (router -> agent
    -> tools -> draft -> approval queue) can be exercised without an API key.
    """

    STUB_HEADER = "[STUB DRAFT -- no LLM configured. Set HARNESS_LLM_API_KEY for a real draft.]"

    def complete(
        self, prompt: str, system: str | None = None, model: str | None = None
    ) -> str:
        # Pull the task and the context block out of the prompt so the stub
        # draft shows what the agent was asked to do and what it gathered.
        task = _extract_between(prompt, "Task:", "Context:")
        context = _extract_after(prompt, "Context:")
        lines = [self.STUB_HEADER, ""]
        if task:
            lines.append("Task: " + task.strip())
            lines.append("")
        if context:
            lines.append("Context gathered from the tools:")
            lines.append(context.strip())
            lines.append("")
        lines.append(
            "A real LLM would turn the context above into prose here. "
            "Wire up HARNESS_LLM_API_KEY to replace this stub."
        )
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
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
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
        with httpx.Client(timeout=self.timeout) as client:
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


def get_llm(config: Config) -> LLMClient:
    """Return the real LLM client when a key is configured, else the stub."""
    if config.llm_configured():
        return OpenAICompatibleLLM(
            api_key=config.llm_api_key,  # type: ignore[arg-type]
            base_url=config.llm_base_url,
            model=config.llm_model,
        )
    return StubLLM()
