"""Tests for the tool registry: registration, calling, summary-first."""

import unittest

from harness.registry import Tool, ToolRegistry


def _echo_handler(**kwargs):
    # Returns a deliberately bloated raw result, so the test can prove
    # the registry shrinks it before returning.
    return {
        "short": "ok",
        "long": "x" * 5000,
        "items": list(range(20)),
    }


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register(
            Tool(
                name="echo",
                description="Echoes back a bloated result.",
                parameters={},
                handler=_echo_handler,
            )
        )

    def test_get_known_tool(self):
        tool = self.registry.get("echo")
        self.assertEqual(tool.name, "echo")

    def test_get_unknown_tool_raises(self):
        with self.assertRaises(KeyError):
            self.registry.get("nope")

    def test_list_tools_sorted(self):
        self.registry.register(Tool(name="aaa", description="first"))
        names = [t.name for t in self.registry.list_tools()]
        self.assertEqual(names, ["aaa", "echo"])

    def test_call_returns_summary_not_raw(self):
        result = self.registry.call("echo")
        # The 5000-char string is truncated, and the 20-item list is capped.
        self.assertLess(len(result["long"]), 5000)
        self.assertIn("cut", result["long"])
        self.assertLessEqual(len(result["items"]), 6)
        self.assertEqual(result["short"], "ok")

    def test_call_unknown_tool_raises(self):
        with self.assertRaises(KeyError):
            self.registry.call("nope")

    def test_custom_summary_function_is_used(self):
        self.registry.register(
            Tool(
                name="custom",
                description="Uses a custom summarizer.",
                handler=lambda: {"a": 1, "b": 2},
                summarize=lambda raw: {"only": raw["a"]},
            )
        )
        self.assertEqual(self.registry.call("custom"), {"only": 1})


if __name__ == "__main__":
    unittest.main()
