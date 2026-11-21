"""MCP client (v1.5.0): the harness uses MCP servers it did not build.

Until v1.5.0 the harness only called its own connector tools in-process;
the MCP servers in connectors/ were standalone, for other clients to
call. This module turns that around: a harness-side MCP client that
connects to MCP servers the user already runs, over stdio or SSE, lists
their tools, and registers them in the tool registry alongside the
built-in connectors. That is the multiplier: instead of building
connectors one by one, Belay can use any MCP server the user already
has connected.

Design, and the safety rules that come with it:

- Servers are configured in one file-backed JSON store
  (mcp-servers.json, HARNESS_MCP_SERVERS_FILE to move it), same
  pattern as schedules.json and memory.json: human-editable, a
  missing or corrupt file reads as no servers, an invalid entry is
  skipped. A server that fails to connect is reported and skipped,
  never fatal to the harness.
- External tools are namespaced by server (`gmail.gmail_search_messages`),
  so a tool name can never collide with a built-in tool or with the
  same tool from another server.
- Tool selection is by description: the job text is scored against
  each external tool's name and description with the same whole-word
  matching the router uses for agents. An external tool is only called
  when the job asks for it in words the tool understands.
- Adapters: where an external tool's shape does not fit the harness,
  an explicit adapter wraps it summary-first. Adapters are registered
  by bare tool name; the module ships one worked example (the Gmail
  search adapter). Without an adapter, the default summarizer caps
  the result the same way it caps built-in tools.
- Safety: external tools follow the same rules as built-in ones. A
  tool is treated as a write tool unless its MCP annotations declare
  readOnlyHint=true, so an unknown tool defaults to the safe side:
  it is flagged write=True, it is never selected for gathering, and
  it only ever runs through the approval-gated write-back path
  (harness/writeback.py). A server can also assert read-only in its
  config (`read_only: true`): the user states every tool on that
  server only reads, which is how the harness's own servers (whose
  tools carry no annotations) become usable for gathering. The
  assertion is the user's word, not a check: asserting read-only on
  a server that writes lets its tools run during gathering without
  approval, so assert it only for servers you know are read-only
  (the Gmail connector, for example, is read-only by OAuth scope).
  External results go through the same summary-first rendering and
  the same URL stripping as built-in results: the model never sees
  a URL.
- Connections are per call: each tool call opens a fresh session to
  the server, calls the tool, and closes it. Slower than a persistent
  session, but no session lifecycle to manage and no stale state.
  A persistent session is a later optimization, not a v1.5.0 gap.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client

from .registry import Tool, ToolRegistry
from .summarizer import summarize_tool_result

TRANSPORTS = ("stdio", "sse")

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# Words in a job that do not select a tool on their own: the account
# name and the filler around it. Selection scores the tool's name and
# description against the job's content words.
_STOPWORDS = {
    "the", "and", "for", "with", "from", "about", "that", "this",
    "draft", "write", "make", "plan", "corp", "inc", "llc", "ltd",
}

# A parameter whose name contains one of these hints is filled from the
# job when the tool is selected: the account for account-like params,
# the job text for query-like params.
_ACCOUNT_HINTS = ("account", "company", "customer", "org")
_QUERY_HINTS = ("query", "search", "topic", "text", "subject", "keyword")

_CONNECT_TIMEOUT = 30.0
_CALL_TIMEOUT = 60.0


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Server config: one external MCP server.
# ---------------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    """One external MCP server the harness may use.

    stdio: `command` plus `args` (a command line the user runs, e.g.
    `python -m connectors.gmail.server`), optional `env` additions and
    `cwd`. sse: `url` plus optional `headers`. `name` is the namespace
    its tools are registered under, so it must be one word of letters,
    digits, dashes, and underscores. `read_only` is the user's
    assertion that every tool on the server only reads (see the
    module docstring): it makes annotation-less read tools usable
    for gathering, and it must only be set for servers known to be
    read-only.
    """

    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    read_only: bool = False
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MCPServerConfig":
        return cls(
            name=data["name"],
            transport=data.get("transport", "stdio"),
            command=data.get("command", ""),
            args=list(data.get("args", [])),
            env=dict(data.get("env", {})),
            cwd=data.get("cwd"),
            url=data.get("url", ""),
            headers=dict(data.get("headers", {})),
            read_only=bool(data.get("read_only", False)),
            created_at=data.get("created_at", _now_iso()),
        )

    def describe(self) -> str:
        """One line for `mcp-servers list`."""
        if self.transport == "sse":
            target = self.url
        else:
            target = (self.command + " " + " ".join(self.args)).strip()
        suffix = " [read-only]" if self.read_only else ""
        return "%-20s %-6s %s%s" % (self.name, self.transport, target, suffix)


def validate_server_config(data: dict) -> dict:
    """Check a server config's fields before it is stored. Returns a
    cleaned copy. Raises ValueError with a plain message on bad input.
    Unknown keys are dropped: the file stays forward-compatible, and
    only known fields are ever stored."""
    name = str(data.get("name", "") or "").strip()
    if not name:
        raise ValueError("server needs --name")
    if not _NAME_RE.match(name):
        raise ValueError(
            "server name %r must be letters, digits, dashes, and "
            "underscores, starting with a letter or digit." % name
        )
    transport = str(data.get("transport", "") or "").strip().lower()
    if transport not in TRANSPORTS:
        raise ValueError(
            "transport must be stdio or sse, got %r" % data.get("transport")
        )
    command = str(data.get("command", "") or "").strip()
    args = data.get("args") or []
    if isinstance(args, str):
        args = shlex.split(args)
    args = [str(a) for a in args]
    env = data.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError("--env must be KEY=VALUE pairs")
    env = {str(k): str(v) for k, v in env.items()}
    cwd = data.get("cwd")
    cwd = str(cwd).strip() if cwd else None
    url = str(data.get("url", "") or "").strip()
    headers = data.get("headers") or {}
    if not isinstance(headers, dict):
        raise ValueError("--header must be KEY: VALUE pairs")
    headers = {str(k): str(v) for k, v in headers.items()}
    read_only = data.get("read_only", False)
    if not isinstance(read_only, bool):
        raise ValueError("--read-only must be true or false")
    if transport == "stdio":
        if not command:
            raise ValueError("stdio servers need --command")
        if url:
            raise ValueError("stdio servers do not take --url")
    else:
        if not url:
            raise ValueError("sse servers need --url")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("sse url must start with http:// or https://, got %r" % url)
        if command:
            raise ValueError("sse servers do not take --command")
    return {
        "name": name,
        "transport": transport,
        "command": command,
        "args": args,
        "env": env,
        "cwd": cwd,
        "url": url,
        "headers": headers,
        "read_only": read_only,
    }


class MCPServerStore:
    """File-backed store of external MCP servers, one JSON file.

    Same pattern as the schedule store and memory: a missing or
    corrupt file reads as no servers, an invalid entry is skipped,
    saves are atomic (tmp file + replace). The file is human-editable:
    one object per server, all fields plain values."""

    def __init__(self, path: str = "mcp-servers.json") -> None:
        self.path = path
        self._servers: dict[str, MCPServerConfig] = {}
        self._load()

    def _load(self) -> None:
        self._servers = {}
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        for entry in data if isinstance(data, list) else []:
            try:
                server = MCPServerConfig.from_dict(entry)
                validate_server_config(entry)
            except (KeyError, TypeError, ValueError):
                continue
            self._servers[server.name] = server

    def reload(self) -> None:
        """Re-read the file from disk."""
        self._load()

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                [s.to_dict() for s in self._servers.values()],
                f,
                indent=2,
            )
        os.replace(tmp, self.path)

    def list(self) -> list[MCPServerConfig]:
        """All servers, oldest first."""
        return sorted(
            self._servers.values(), key=lambda s: s.created_at
        )

    def get(self, name: str) -> MCPServerConfig | None:
        """One server by name, or None."""
        return self._servers.get(name)

    def add(self, data: dict) -> MCPServerConfig:
        """Validate and store a new server. A name already in the
        store is an error: remove it first, then add the replacement,
        so a typo never silently overwrites a working server."""
        cleaned = validate_server_config(data)
        if cleaned["name"] in self._servers:
            raise ValueError(
                "server %r is already configured; remove it first to "
                "replace it." % cleaned["name"]
            )
        server = MCPServerConfig(**cleaned)
        self._servers[server.name] = server
        self._save()
        return server

    def remove(self, name: str) -> bool:
        """Delete a server. True when one was removed."""
        if name not in self._servers:
            return False
        del self._servers[name]
        self._save()
        return True


# ---------------------------------------------------------------------------
# Client: speak MCP to one server, over stdio or SSE.
# ---------------------------------------------------------------------------


def _run_coro(coro):
    """Run a coroutine from sync code.

    The harness is sync throughout, so asyncio.run() is the normal
    path. When a loop is already running (a dashboard request calling
    a tool), the coroutine runs on a fresh loop in a helper thread
    instead, since asyncio.run() refuses a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    result: dict = {}

    def _target() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            result["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _result_to_dict(result) -> dict:
    """Turn an MCP CallToolResult into a plain dict for the registry.

    A server that returns structured content gives us the dict
    directly. Otherwise the text blocks are joined: a single JSON
    object parses to itself, anything else becomes {"text": ...}.
    An error result becomes {"error": ...}, so the harness records
    the failure instead of treating it as data."""
    if getattr(result, "isError", False):
        texts = [
            block.text
            for block in (result.content or [])
            if hasattr(block, "text")
        ]
        return {"error": " ".join(texts) or "MCP tool returned an error."}
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    texts = [
        block.text
        for block in (result.content or [])
        if hasattr(block, "text")
    ]
    joined = "\n".join(texts).strip()
    if not joined:
        return {}
    try:
        parsed = json.loads(joined)
    except (json.JSONDecodeError, ValueError):
        return {"text": joined}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def _is_read_only(tool) -> bool:
    """True when the tool's MCP annotations declare it read-only.

    The rule is conservative: a tool is a write tool unless its
    annotations say readOnlyHint=true. An annotation-less tool is
    assumed to write, so it defaults to the safe side.
    """
    annotations = getattr(tool, "annotations", None)
    if annotations is None:
        return False
    return bool(getattr(annotations, "readOnlyHint", False))


def _with_server_stderr(exc: Exception, errlog) -> Exception:
    """Attach the tail of a stdio server's stderr to a failure, so a
    server that dies on startup shows why. A server that logged
    nothing adds nothing to the message. `errlog` is a real temp file
    (Popen needs a fileno for stderr), read back from the start."""
    try:
        errlog.seek(0)
        text = errlog.read()
    except (OSError, ValueError):
        text = ""
    tail = [
        line.strip() for line in text.strip().splitlines() if line.strip()
    ][-3:]
    if not tail:
        return exc
    return RuntimeError("%s (server stderr: %s)" % (exc, " | ".join(tail)))


class MCPClient:
    """Speaks MCP to one configured server.

    One client object per server config; sessions are per call, not
    persistent. `list_tools` and `call_tool` are async; the `_sync`
    wrappers run them from sync code. Every call is bounded by a
    timeout, so a server that starts but never answers does not hang
    the harness.
    """

    def __init__(self, server: MCPServerConfig) -> None:
        self.server = server

    def _session(self, errlog=None):
        """The transport context manager for this server.

        A stdio server's stderr is captured, not inherited: a healthy
        FastMCP server logs every request to stderr, and inheriting it
        would print a line per call on the harness's stderr. The
        captured tail is attached to the error when a call fails, so
        a server that dies on startup still shows why."""
        if self.server.transport == "sse":
            return sse_client(
                self.server.url,
                headers=self.server.headers or None,
                timeout=5,
            )
        params = StdioServerParameters(
            command=self.server.command,
            args=list(self.server.args),
            env=dict(self.server.env) if self.server.env else None,
            cwd=self.server.cwd,
        )
        return stdio_client(
            params,
            errlog=errlog
            if errlog is not None
            else tempfile.TemporaryFile(mode="w+", encoding="utf-8"),
        )

    async def list_tools(self) -> list[dict]:
        """List the server's tools: name, description, parameter
        schema, and whether it is read-only. Raises on connect or
        protocol failure; the caller decides what a failure means."""
        errlog = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        try:
            async with self._session(errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(
                        session.initialize(), timeout=_CONNECT_TIMEOUT
                    )
                    result = await asyncio.wait_for(
                        session.list_tools(), timeout=_CONNECT_TIMEOUT
                    )
                    tools = []
                    for tool in result.tools:
                        tools.append(
                            {
                                "name": tool.name,
                                "description": tool.description or "",
                                "inputSchema": dict(tool.inputSchema or {}),
                                "read_only": _is_read_only(tool),
                            }
                        )
                    return tools
        except Exception as exc:
            raise _with_server_stderr(exc, errlog)
        finally:
            errlog.close()

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Call one tool on the server and return its result as a
        dict. Raises on connect, protocol, or transport failure."""
        errlog = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        try:
            async with self._session(errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(
                        session.initialize(), timeout=_CONNECT_TIMEOUT
                    )
                    result = await asyncio.wait_for(
                        session.call_tool(name, arguments),
                        timeout=_CALL_TIMEOUT,
                    )
                    return _result_to_dict(result)
        except Exception as exc:
            raise _with_server_stderr(exc, errlog)
        finally:
            errlog.close()

    def list_tools_sync(self) -> list[dict]:
        """Sync wrapper for list_tools."""
        return _run_coro(self.list_tools())

    def call_tool_sync(self, name: str, arguments: dict) -> dict:
        """Sync wrapper for call_tool."""
        return _run_coro(self.call_tool(name, arguments))


# ---------------------------------------------------------------------------
# Adapters: reshape an external tool's result summary-first.
# ---------------------------------------------------------------------------


def gmail_search_messages_adapter(result: dict) -> dict:
    """Worked adapter example: the Gmail search tool, as served by
    the harness's own Gmail MCP server.

    The server already returns excerpts, so the adapter's job is
    enforcement, not repair: keep only the summary keys, cap matches
    at 5, truncate every excerpt to 200 chars, and drop any body-like
    key a server might add. The harness never sees a full message
    body through this tool, adapter or not."""
    matches = result.get("matches") or []
    capped = []
    for match in matches[:5]:
        if not isinstance(match, dict):
            continue
        capped.append(
            {
                key: match.get(key)
                for key in (
                    "id",
                    "thread_id",
                    "author",
                    "subject",
                    "date",
                    "excerpt",
                )
                if key in match
            }
        )
        if capped[-1].get("excerpt"):
            capped[-1]["excerpt"] = str(capped[-1]["excerpt"])[:200]
    out: dict = {"query": result.get("query"), "matches": capped}
    if result.get("_stub"):
        out["_stub"] = True
    return out


# Adapters by bare tool name, applied to that tool on any server.
# Register more with register_adapter().
ADAPTERS: dict[str, Callable[[dict], dict]] = {
    "gmail_search_messages": gmail_search_messages_adapter,
}


def register_adapter(name: str, adapter: Callable[[dict], dict]) -> None:
    """Register an adapter for an external tool, by bare tool name.
    The adapter runs as the tool's summarize function: it receives
    the tool's result dict and returns the summary form the harness
    sees. Re-registering a name replaces the adapter."""
    ADAPTERS[name] = adapter


# ---------------------------------------------------------------------------
# Registry wiring: external servers become namespaced tools.
# ---------------------------------------------------------------------------


def is_external_tool(name: str) -> bool:
    """True for a namespaced external tool (`server.tool`). Built-in
    tool names never contain a dot."""
    return "." in name


def _external_tool(
    server: MCPServerConfig, info: dict, client: MCPClient
) -> Tool:
    """One external tool as a registry Tool, namespaced by server.

    The handler opens a fresh session per call. A write-flagged tool
    is registered like any other, but it is never selected for
    gathering (see build_external_calls): the only caller that runs
    it is the approval-gated executor. A tool counts as read-only
    when its MCP annotations say so, or when the server asserts
    read-only in its config. An adapter, when one is registered for
    the bare tool name, runs as the summarize function; otherwise
    the registry's default summarizer caps the result."""
    bare = info["name"]
    namespaced = "%s.%s" % (server.name, bare)
    parameters = info.get("inputSchema") or {}
    adapter = ADAPTERS.get(bare)

    def _handler(**kwargs):
        return client.call_tool_sync(bare, dict(kwargs))

    return Tool(
        name=namespaced,
        description=info.get("description") or "",
        parameters=parameters,
        handler=_handler,
        summarize=adapter,
        write=not (info.get("read_only", False) or server.read_only),
    )


def load_external_tools(
    registry: ToolRegistry, store: MCPServerStore
) -> dict:
    """Connect to every configured server and register its tools in
    the registry, namespaced by server.

    Returns a report: {"servers": [names connected], "tools":
    [namespaced tool names], "failures": [{"server": name, "error":
    str}]}. A server that fails to connect is in `failures` and
    skipped; it never raises and never stops the other servers.
    An empty store is a no-op: no servers, no tools, no failures."""
    report: dict = {"servers": [], "tools": [], "failures": []}
    for server in store.list():
        client = MCPClient(server)
        try:
            tools = client.list_tools_sync()
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            report["failures"].append(
                {"server": server.name, "error": str(exc)}
            )
            continue
        report["servers"].append(server.name)
        for info in tools:
            tool = _external_tool(server, info, client)
            registry.register(tool)
            report["tools"].append(tool.name)
    return report


# ---------------------------------------------------------------------------
# Tool selection by description: the job picks its own tools.
# ---------------------------------------------------------------------------


def _content_words(text: str) -> list[str]:
    """The job text as content words: lowercased, whole words, three
    letters or more, stopwords dropped."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in words if len(w) >= 3 and w not in _STOPWORDS]


def _score_description(words: list[str], name: str, description: str) -> int:
    """Whole-word hits of the job's words in the tool's name and
    description, same matching the router uses for agents: a word
    counts once per occurrence. The name counts too, so a tool whose
    description is terse but whose name says what it does is still
    findable."""
    haystack = (name.replace("_", " ").replace(".", " ") + " " + description).lower()
    score = 0
    for word in words:
        score += len(re.findall(r"\b" + re.escape(word) + r"\b", haystack))
    return score


def select_external_tools(
    registry: ToolRegistry, text: str, limit: int = 3
) -> list[Tool]:
    """The external tools the job asks for, by description.

    Scores every namespaced tool in the registry against the job
    text; a tool with no hits is never selected. Read tools only:
    a write-flagged tool is never returned here, so gathering can
    never run one. Returns the best matches, highest score first,
    capped at `limit`."""
    words = _content_words(text)
    if not words:
        return []
    scored = []
    for tool in registry.list_tools():
        if not is_external_tool(tool.name):
            continue
        if tool.write:
            continue
        score = _score_description(words, tool.name, tool.description)
        if score >= 1:
            scored.append((score, tool))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [tool for _score, tool in scored[:limit]]


def _fill_arguments(tool: Tool, job: dict) -> dict | None:
    """Build the arguments for a selected external tool from the job.

    A required string parameter whose name hints at the account takes
    the job's account; one that hints at a search takes the account
    too, falling back to the job's text when no account is set (the
    account is what the agent searches for, the text is the
    instruction). A tool with a required parameter no hint covers is
    skipped (None): the harness does not guess arguments. Optional
    parameters are left unset."""
    schema = tool.parameters or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    account = str(job.get("account", "") or "").strip()
    text = str(job.get("text", "") or "").strip()
    args: dict = {}
    for name in required:
        spec = properties.get(name) or {}
        if spec.get("type") not in (None, "string"):
            return None
        lowered = name.lower()
        if any(hint in lowered for hint in _ACCOUNT_HINTS):
            if not account:
                return None
            args[name] = account
        elif any(hint in lowered for hint in _QUERY_HINTS):
            args[name] = account or text
            if not args[name]:
                return None
        else:
            return None
    return args


def build_external_calls(
    registry: ToolRegistry, job: dict, limit: int = 3
) -> list[tuple[str, dict]]:
    """The external tool calls for a job: select by description,
    fill arguments from the job, skip what cannot be filled.

    The job text is the job's text plus its account, so a job that
    only names the account still matches tools that mention the
    account. Returns [(namespaced tool name, kwargs)] in selection
    order. An empty registry of external tools is an empty list:
    no servers configured costs nothing."""
    text = str(job.get("text", "") or "")
    if job.get("account"):
        text = text + " " + str(job["account"])
    calls: list[tuple[str, dict]] = []
    for tool in select_external_tools(registry, text, limit=limit):
        args = _fill_arguments(tool, job)
        if args is None:
            continue
        calls.append((tool.name, args))
    return calls
