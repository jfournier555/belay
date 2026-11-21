"""Tests for the MCP client (v1.5.0): server store, client, adapters,
registry wiring, description-based selection, write-flag safety."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agents import _gather, external_calls_for_job
from harness.config import Config
from harness.mcp_client import (
    ADAPTERS,
    MCPClient,
    MCPServerConfig,
    MCPServerStore,
    _is_read_only,
    _result_to_dict,
    build_external_calls,
    gmail_search_messages_adapter,
    is_external_tool,
    load_external_tools,
    register_adapter,
    select_external_tools,
    validate_server_config,
)
from harness.registry import Tool, ToolRegistry
from harness.run import build_registry
from harness.summarizer import TokenBudget

REPO_ROOT = Path(__file__).resolve().parent.parent

_FAKE_SSE_SERVER = '''
import sys
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("fake-notes")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def notes_search(query: str) -> dict:
    """Search notes for a query. Returns short note excerpts."""
    return {
        "query": query,
        "matches": [{"title": "Renewal notes", "excerpt": "Acme renews in Feb."}],
    }


@mcp.tool()
def notes_append(text: str) -> dict:
    """Append a note. Writes to the notes store."""
    return {"appended": text}


if __name__ == "__main__":
    try:
        mcp.settings.port = int(sys.argv[1])
    except Exception:
        pass
    mcp.run(transport="sse")
'''


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _gmail_server_config(name="gmail", read_only=False):
    return {
        "name": name,
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "connectors.gmail.server"],
        "cwd": str(REPO_ROOT),
        "read_only": read_only,
    }


class _Annotation:
    def __init__(self, read_only_hint=None):
        self.readOnlyHint = read_only_hint


class _MCPTool:
    def __init__(self, annotations=None):
        self.annotations = annotations


class _TextBlock:
    def __init__(self, text):
        self.text = text


class _CallResult:
    def __init__(self, content=None, structured=None, is_error=False):
        self.content = content or []
        self.structuredContent = structured
        self.isError = is_error


class TestServerStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "mcp-servers.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_list_remove_round_trip(self):
        store = MCPServerStore(self.path)
        self.assertEqual(store.list(), [])
        server = store.add(_gmail_server_config())
        self.assertEqual(server.name, "gmail")
        self.assertEqual([s.name for s in store.list()], ["gmail"])
        reloaded = MCPServerStore(self.path)
        self.assertEqual([s.name for s in reloaded.list()], ["gmail"])
        self.assertEqual(reloaded.get("gmail").command, sys.executable)
        self.assertTrue(store.remove("gmail"))
        self.assertEqual(store.list(), [])
        self.assertFalse(store.remove("gmail"))

    def test_duplicate_name_rejected(self):
        store = MCPServerStore(self.path)
        store.add(_gmail_server_config())
        with self.assertRaises(ValueError):
            store.add(_gmail_server_config())

    def test_missing_file_is_empty(self):
        self.assertEqual(MCPServerStore(self.path).list(), [])

    def test_corrupt_file_is_empty(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(MCPServerStore(self.path).list(), [])

    def test_invalid_entry_skipped(self):
        with open(self.path, "w") as f:
            json.dump(
                [
                    {"name": "bad", "transport": "carrier-pigeon"},
                    _gmail_server_config(),
                ],
                f,
            )
        self.assertEqual(
            [s.name for s in MCPServerStore(self.path).list()], ["gmail"]
        )

    def test_validate_stdio_needs_command(self):
        with self.assertRaises(ValueError):
            validate_server_config({"name": "x", "transport": "stdio"})

    def test_validate_sse_needs_http_url(self):
        with self.assertRaises(ValueError):
            validate_server_config(
                {"name": "x", "transport": "sse", "url": "ftp://example.com"}
            )
        with self.assertRaises(ValueError):
            validate_server_config({"name": "x", "transport": "sse"})

    def test_validate_bad_name(self):
        with self.assertRaises(ValueError):
            validate_server_config(
                {"name": "has space", "transport": "stdio", "command": "x"}
            )

    def test_validate_sse_and_stdio_conflict(self):
        with self.assertRaises(ValueError):
            validate_server_config(
                {
                    "name": "x",
                    "transport": "stdio",
                    "command": "x",
                    "url": "https://example.com/mcp",
                }
            )

    def test_validate_drops_unknown_keys(self):
        cleaned = validate_server_config(
            {
                "name": "x",
                "transport": "sse",
                "url": "https://example.com/mcp",
                "surprise": True,
            }
        )
        self.assertNotIn("surprise", cleaned)
        self.assertTrue(cleaned["read_only"] is False)


class TestReadOnlyDetection(unittest.TestCase):
    def test_annotated_read_only(self):
        self.assertTrue(_is_read_only(_MCPTool(_Annotation(True))))

    def test_annotated_writable(self):
        self.assertFalse(_is_read_only(_MCPTool(_Annotation(False))))

    def test_no_annotations_is_not_read_only(self):
        # The conservative rule: an annotation-less tool is assumed
        # to write, so it defaults to the safe side.
        self.assertFalse(_is_read_only(_MCPTool(None)))
        self.assertFalse(_is_read_only(_MCPTool()))


class TestResultConversion(unittest.TestCase):
    def test_structured_content_wins(self):
        result = _result_to_dict(
            _CallResult(structured={"a": 1}, content=[_TextBlock("{}")])
        )
        self.assertEqual(result, {"a": 1})

    def test_json_text_parses(self):
        result = _result_to_dict(
            _CallResult(content=[_TextBlock('{"query": "x", "n": 2}')])
        )
        self.assertEqual(result, {"query": "x", "n": 2})

    def test_plain_text_becomes_text(self):
        result = _result_to_dict(_CallResult(content=[_TextBlock("hello")]))
        self.assertEqual(result, {"text": "hello"})

    def test_error_becomes_error_dict(self):
        result = _result_to_dict(
            _CallResult(
                content=[_TextBlock("boom")], is_error=True
            )
        )
        self.assertEqual(result, {"error": "boom"})

    def test_empty_content_is_empty_dict(self):
        self.assertEqual(_result_to_dict(_CallResult()), {})


class TestAdapters(unittest.TestCase):
    def test_gmail_adapter_trims_and_caps(self):
        raw = {
            "query": "Acme",
            "matches": [
                {
                    "id": "1",
                    "thread_id": "t1",
                    "author": "a@example",
                    "subject": "s",
                    "date": "2026-01-01",
                    "excerpt": "x" * 500,
                    "body": "full body that must never pass through",
                }
                for _ in range(8)
            ],
            "extra_key": "dropped",
        }
        out = gmail_search_messages_adapter(raw)
        self.assertEqual(len(out["matches"]), 5)
        self.assertEqual(len(out["matches"][0]["excerpt"]), 200)
        self.assertNotIn("body", out["matches"][0])
        self.assertNotIn("extra_key", out)

    def test_gmail_adapter_keeps_stub_flag(self):
        out = gmail_search_messages_adapter(
            {"query": "x", "matches": [], "_stub": True}
        )
        self.assertTrue(out["_stub"])

    def test_register_adapter_replaces(self):
        register_adapter("demo_tool", lambda r: {"adapted": True})
        self.assertIn("demo_tool", ADAPTERS)
        try:
            self.assertEqual(ADAPTERS["demo_tool"]({}), {"adapted": True})
        finally:
            del ADAPTERS["demo_tool"]


class TestStdioClient(unittest.TestCase):
    def test_list_and_call_the_harness_gmail_server(self):
        client = MCPClient(
            MCPServerConfig.from_dict(_gmail_server_config())
        )
        tools = client.list_tools_sync()
        names = sorted(t["name"] for t in tools)
        self.assertEqual(
            names, ["gmail_get_thread", "gmail_search_messages"]
        )
        # The harness's own server carries no annotations, so the
        # conservative rule flags its tools as writes.
        self.assertTrue(all(not t["read_only"] for t in tools))
        result = client.call_tool_sync(
            "gmail_search_messages", {"query": "Acme"}
        )
        self.assertTrue(result.get("_stub"))
        self.assertEqual(len(result["matches"]), 4)

    def test_bad_command_raises(self):
        client = MCPClient(
            MCPServerConfig(
                name="ghost", transport="stdio", command="/nonexistent/nope"
            )
        )
        with self.assertRaises(Exception):
            client.list_tools_sync()

    def test_sse_fake_server_annotations_and_call(self):
        port = _free_port()
        script = os.path.join(
            tempfile.mkdtemp(), "fake_sse_server.py"
        )
        with open(script, "w") as f:
            f.write(_FAKE_SSE_SERVER)
        proc = subprocess.Popen(
            [sys.executable, script, str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    time.sleep(0.2)
            else:
                self.fail("fake SSE server did not start")
            client = MCPClient(
                MCPServerConfig(
                    name="notes",
                    transport="sse",
                    url="http://127.0.0.1:%d/sse" % port,
                )
            )
            # httpx reads proxy env vars, and this environment's
            # NO_PROXY list contains bracketed IPv6 entries httpx
            # cannot parse, so the SSE connect would fail on proxy
            # handling before reaching the server. Clear the proxy
            # vars for the local connect; a real failure to reach a
            # server is still reported by the client as unreachable.
            proxy_vars = [
                k for k in os.environ if k.lower().endswith("proxy")
            ]
            saved = {k: os.environ.pop(k) for k in proxy_vars}
            try:
                tools = client.list_tools_sync()
            finally:
                os.environ.update(saved)
            by_name = {t["name"]: t for t in tools}
            self.assertTrue(by_name["notes_search"]["read_only"])
            self.assertFalse(by_name["notes_append"]["read_only"])
            saved = {k: os.environ.pop(k) for k in proxy_vars}
            try:
                result = client.call_tool_sync(
                    "notes_search", {"query": "renewal"}
                )
            finally:
                os.environ.update(saved)
            self.assertEqual(result["query"], "renewal")
            self.assertEqual(len(result["matches"]), 1)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


class TestRegistryWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "mcp-servers.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_registers_namespaced_tools(self):
        store = MCPServerStore(self.path)
        store.add(_gmail_server_config(read_only=True))
        registry = ToolRegistry()
        report = load_external_tools(registry, store)
        self.assertEqual(report["servers"], ["gmail"])
        self.assertEqual(report["failures"], [])
        self.assertIn("gmail.gmail_search_messages", report["tools"])
        tool = registry.get("gmail.gmail_search_messages")
        # The server asserts read-only, so the tool is a read tool.
        self.assertFalse(tool.write)
        result = registry.call("gmail.gmail_search_messages", query="Acme")
        self.assertTrue(result.get("_stub"))

    def test_load_without_assertion_flags_writes(self):
        store = MCPServerStore(self.path)
        store.add(_gmail_server_config(read_only=False))
        registry = ToolRegistry()
        load_external_tools(registry, store)
        self.assertTrue(registry.get("gmail.gmail_search_messages").write)

    def test_failing_server_skipped_not_fatal(self):
        store = MCPServerStore(self.path)
        store.add(
            {
                "name": "ghost",
                "transport": "stdio",
                "command": "/nonexistent/nope",
            }
        )
        store.add(_gmail_server_config(read_only=True))
        registry = ToolRegistry()
        report = load_external_tools(registry, store)
        self.assertEqual(len(report["failures"]), 1)
        self.assertEqual(report["failures"][0]["server"], "ghost")
        self.assertEqual(report["servers"], ["gmail"])
        self.assertIn("gmail.gmail_search_messages", report["tools"])

    def test_empty_store_is_a_noop(self):
        registry = ToolRegistry()
        report = load_external_tools(registry, MCPServerStore(self.path))
        self.assertEqual(report, {"servers": [], "tools": [], "failures": []})

    def test_same_tool_two_servers_no_collision(self):
        store = MCPServerStore(self.path)
        store.add(_gmail_server_config(name="gmail-a", read_only=True))
        store.add(_gmail_server_config(name="gmail-b", read_only=True))
        registry = ToolRegistry()
        report = load_external_tools(registry, store)
        self.assertIn("gmail-a.gmail_search_messages", report["tools"])
        self.assertIn("gmail-b.gmail_search_messages", report["tools"])
        self.assertEqual(
            len([t for t in report["tools"] if "gmail_search_messages" in t]),
            2,
        )

    def test_is_external_tool(self):
        self.assertTrue(is_external_tool("gmail.gmail_search_messages"))
        self.assertFalse(is_external_tool("gmail_search_messages"))

    def test_build_registry_loads_external_tools(self):
        store = MCPServerStore(self.path)
        store.add(_gmail_server_config(read_only=True))
        config = Config(mcp_servers_file=self.path)
        registry = build_registry(config)
        self.assertIn(
            "gmail.gmail_search_messages",
            [t.name for t in registry.list_tools()],
        )
        # Built-ins still register alongside.
        self.assertIn(
            "salesforce_get_account",
            [t.name for t in registry.list_tools()],
        )


class TestSelection(unittest.TestCase):
    def _registry_with_external(self):
        registry = ToolRegistry()
        registry.register(
            Tool(
                name="gmail.gmail_search_messages",
                description=(
                    "Search Gmail for an account or topic. Returns short "
                    "excerpts, not full message bodies."
                ),
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                handler=lambda query: {"query": query, "matches": []},
            )
        )
        registry.register(
            Tool(
                name="notes.notes_append",
                description="Append a note. Writes to the notes store.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                handler=lambda text: {"appended": text},
                write=True,
            )
        )
        registry.register(
            Tool(
                name="salesforce_get_account",
                description="Get a Salesforce account record.",
                handler=lambda account: {"name": account},
            )
        )
        return registry

    def test_selects_by_description(self):
        registry = self._registry_with_external()
        picked = select_external_tools(
            registry, "search Gmail messages about the renewal"
        )
        self.assertEqual(
            [t.name for t in picked], ["gmail.gmail_search_messages"]
        )

    def test_no_match_returns_nothing(self):
        registry = self._registry_with_external()
        self.assertEqual(
            select_external_tools(registry, "draft a qbr deck"), []
        )

    def test_write_tools_never_selected(self):
        registry = self._registry_with_external()
        picked = select_external_tools(registry, "append a note")
        self.assertEqual(picked, [])

    def test_builtin_tools_never_selected(self):
        registry = self._registry_with_external()
        picked = select_external_tools(registry, "get Salesforce account")
        # Only external tools are ever returned; the built-in is
        # never in the list, whatever the job text says.
        self.assertNotIn(
            "salesforce_get_account", [t.name for t in picked]
        )
        self.assertTrue(all(is_external_tool(t.name) for t in picked))

    def test_build_calls_fills_query_from_account(self):
        registry = self._registry_with_external()
        calls = build_external_calls(
            registry,
            {"account": "Acme Corp", "text": "search Gmail messages"},
        )
        self.assertEqual(
            calls,
            [("gmail.gmail_search_messages", {"query": "Acme Corp"})],
        )

    def test_build_calls_skips_unfillable_params(self):
        registry = ToolRegistry()
        registry.register(
            Tool(
                name="vault.vault_open",
                description="Open the vault.",
                parameters={
                    "type": "object",
                    "properties": {"combination": {"type": "string"}},
                    "required": ["combination"],
                },
                handler=lambda combination: {"open": True},
            )
        )
        calls = build_external_calls(
            registry, {"account": "Acme Corp", "text": "open the vault"}
        )
        self.assertEqual(calls, [])

    def test_external_calls_for_job_uses_account_text(self):
        registry = self._registry_with_external()
        calls = external_calls_for_job(
            {"account": "Acme Corp", "text": "search Gmail messages"},
            registry,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "gmail.gmail_search_messages")


class TestGatherWithExternal(unittest.TestCase):
    def test_external_result_lands_in_context(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            path = os.path.join(tmp.name, "mcp-servers.json")
            store = MCPServerStore(path)
            store.add(_gmail_server_config(read_only=True))
            registry = ToolRegistry()
            load_external_tools(registry, store)
            registry.register(
                Tool(
                    name="salesforce_get_account",
                    description="Get an account.",
                    handler=lambda account: {"name": account},
                )
            )
            job = {
                "account": "Acme Corp",
                "text": "search Gmail messages about the renewal",
            }
            context, sources, links = _gather(
                registry,
                [("salesforce_get_account", {"account": "Acme Corp"})],
                TokenBudget(8000),
                job=job,
            )
            self.assertIn("gmail.gmail_search_messages", sources)
            self.assertIn("gmail.gmail_search_messages", context)
            self.assertIn("Acme", context["gmail.gmail_search_messages"])
        finally:
            tmp.cleanup()


class TestMcpServersCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "mcp-servers.json")
        self.env = mock.patch.dict(
            os.environ,
            {
                "HARNESS_MCP_SERVERS_FILE": self.path,
                "HARNESS_APPROVALS_FILE": os.path.join(
                    self.tmp.name, "approvals.json"
                ),
                "HARNESS_MEMORY_FILE": os.path.join(
                    self.tmp.name, "memory.json"
                ),
                "HARNESS_SCHEDULES_FILE": os.path.join(
                    self.tmp.name, "schedules.json"
                ),
            },
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _run(self, argv):
        from harness.run import main

        return main(argv)

    def test_add_list_remove(self):
        from io import StringIO

        with mock.patch("sys.stdout", new=StringIO()) as out:
            self.assertEqual(
                self._run(
                    [
                        "mcp-servers",
                        "add",
                        "--name",
                        "gmail",
                        "--stdio",
                        sys.executable + " -m connectors.gmail.server",
                        "--cwd",
                        str(REPO_ROOT),
                        "--read-only",
                    ]
                ),
                0,
            )
            self.assertIn("gmail", out.getvalue())
        with mock.patch("sys.stdout", new=StringIO()) as out:
            self.assertEqual(self._run(["mcp-servers", "list"]), 0)
            self.assertIn("gmail", out.getvalue())
            self.assertIn(
                "ok (2 tool(s): gmail_search_messages, gmail_get_thread)",
                out.getvalue(),
            )
        with mock.patch("sys.stdout", new=StringIO()):
            self.assertEqual(
                self._run(["mcp-servers", "remove", "gmail"]), 0
            )
        with mock.patch("sys.stdout", new=StringIO()) as out:
            self.assertEqual(self._run(["mcp-servers", "list"]), 0)
            self.assertIn("No MCP servers", out.getvalue())

    def test_add_duplicate_fails(self):
        from io import StringIO

        with mock.patch("sys.stdout", new=StringIO()):
            self._run(
                [
                    "mcp-servers",
                    "add",
                    "--name",
                    "gmail",
                    "--stdio",
                    sys.executable + " -m connectors.gmail.server",
                ]
            )
        with mock.patch("sys.stdout", new=StringIO()):
            with mock.patch("sys.stderr", new=StringIO()):
                self.assertEqual(
                    self._run(
                        [
                            "mcp-servers",
                            "add",
                            "--name",
                            "gmail",
                            "--stdio",
                            sys.executable + " -m connectors.gmail.server",
                        ]
                    ),
                    2,
                )

    def test_list_reports_unreachable_server(self):
        from io import StringIO

        with mock.patch("sys.stdout", new=StringIO()):
            self._run(
                [
                    "mcp-servers",
                    "add",
                    "--name",
                    "ghost",
                    "--stdio",
                    "/nonexistent/nope",
                ]
            )
        with mock.patch("sys.stdout", new=StringIO()) as out:
            self.assertEqual(self._run(["mcp-servers", "list"]), 0)
            self.assertIn("ghost", out.getvalue())
            self.assertIn("unreachable", out.getvalue())

    def test_remove_unknown_fails(self):
        from io import StringIO

        with mock.patch("sys.stdout", new=StringIO()):
            with mock.patch("sys.stderr", new=StringIO()):
                self.assertEqual(
                    self._run(["mcp-servers", "remove", "nope"]), 1
                )


if __name__ == "__main__":
    unittest.main()
