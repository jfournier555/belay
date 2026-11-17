"""Connector interface and pattern for the harness.

A connector is a source of truth the agents can read: Salesforce, Slack,
Gong, and so on. Every connector follows the same shape, so adding a new
one is a copy of an existing one, not a new design:

    connectors/<name>/
        tools.py   Pure tool functions. Each takes plain arguments and
                   returns a dict. No MCP code here. This is the one
                   implementation, shared by both interfaces below.
        server.py  FastMCP wrapper. Exposes the same functions as MCP
                   tools, so any MCP-capable LLM (Claude Desktop, the
                   harness in phase 2, anything else) can call them.

Two interfaces, one implementation:
- In-process: the harness imports the tool functions and registers them
  in the ToolRegistry (see harness/run.py). This is the v1 path. It runs
  with no subprocesses and no network beyond the vendor API itself.
- MCP server: `python -m connectors.<name>.server` starts a FastMCP
  server on stdio. Any MCP client can connect. Calling these servers from
  the harness via an MCP client (instead of in-process) is a phase-2 item.

Credentials: each connector reads its own env vars and reports whether it
is configured. When credentials are missing, or when HARNESS_STUB=1, the
tool functions return realistic sample data instead of calling the vendor
API. The sample data is clearly marked in the code and in the returned
dicts (a "_stub": true key). Never mistake stub data for real account data.
"""

from __future__ import annotations

from typing import Protocol


class Connector(Protocol):
    """What a connector must provide. (Documented interface; the v1
    connectors are plain modules, not classes, because that is simpler.)"""

    name: str
    description: str

    def is_configured(self) -> bool:
        """True when the vendor credentials are present."""
        ...

    def tools(self):
        """Return the list of harness Tool objects this connector provides."""
        ...
