"""Tool registry: the single place agents get their tools.

A tool is a named function with a description, a JSON-schema parameter
spec, a handler, and an optional summary function. The registry is what
makes the harness model-agnostic and connector-agnostic: an agent calls a
tool by name, and does not care whether the implementation behind it is a
local function, a Salesforce MCP server, or a Slack MCP server.

Summary-first: registry.call() runs the handler, then shrinks the raw
result through the summarizer before returning it. The agent only ever sees
the summary. That is the low-token-use contract, enforced here, not left to
each agent to remember.

MCP note (v1 scope): the connectors in connectors/ are real MCP servers
(FastMCP) exposing the same tool functions. In v1 the harness calls the
tool functions in-process, through this registry, so the demo runs without
spawning subprocesses. Calling the MCP servers over stdio/SSE from the
harness, via an MCP client, is a phase-2 item: the tool functions are
already written once and shared between the in-process registry and the
MCP server wrapper, so the cutover is a client, not a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .summarizer import summarize_tool_result


@dataclass
class Tool:
    """One callable tool an agent can use."""

    name: str
    description: str
    parameters: dict = field(default_factory=dict)
    handler: Callable[..., dict] | None = None
    summarize: Callable[[dict], dict] | None = None

    def run(self, **kwargs) -> dict:
        """Invoke the handler and return the summary form of its result."""
        if self.handler is None:
            raise RuntimeError("Tool %r has no handler." % self.name)
        raw = self.handler(**kwargs)
        if not isinstance(raw, dict):
            raw = {"value": raw}
        if self.summarize is not None:
            return self.summarize(raw)
        return summarize_tool_result(raw)


class ToolRegistry:
    """Holds the tools agents may call, by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool. Re-registering the same name replaces it."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """Return the tool with this name, or raise KeyError."""
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError("Unknown tool: %r" % name) from None

    def list_tools(self) -> list[Tool]:
        """All registered tools, sorted by name."""
        return [self._tools[k] for k in sorted(self._tools)]

    def describe(self) -> list[dict]:
        """Name + description for every tool, for prompts and debugging."""
        return [
            {"name": t.name, "description": t.description}
            for t in self.list_tools()
        ]

    def call(self, name: str, **kwargs) -> dict:
        """Call a tool by name and return its summary form."""
        return self.get(name).run(**kwargs)
