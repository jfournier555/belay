"""Tests for the summarizer: token estimates, truncation, budgets."""

import unittest

from harness.summarizer import (
    TokenBudget,
    estimate_tokens,
    summarize_tool_result,
    summarize_value,
    truncate_text,
)


class TestSummarizer(unittest.TestCase):
    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("a" * 400), 100)

    def test_truncate_short_text_unchanged(self):
        self.assertEqual(truncate_text("hello", 100), "hello")

    def test_truncate_long_text_notes_the_cut(self):
        out = truncate_text("x" * 100, 10)
        self.assertTrue(out.startswith("x" * 10))
        self.assertIn("90 chars cut", out)

    def test_summarize_value_caps_lists(self):
        out = summarize_value(list(range(20)))
        self.assertEqual(len(out), 6)  # 5 items + the "more" note
        self.assertIn("15 more items", out[5])

    def test_summarize_tool_result_keeps_shape(self):
        raw = {"name": "Acme", "notes": "y" * 5000, "tags": list(range(10))}
        out = summarize_tool_result(raw)
        self.assertEqual(set(out.keys()), {"name", "notes", "tags"})
        self.assertLess(len(out["notes"]), 5000)
        self.assertEqual(len(out["tags"]), 6)

    def test_budget_allows_and_blocks(self):
        budget = TokenBudget(10)
        self.assertTrue(budget.spend("abcd"))  # 1 token
        self.assertEqual(budget.remaining(), 9)
        self.assertFalse(budget.spend("x" * 400))  # 100 tokens, over budget
        self.assertEqual(budget.remaining(), 9)  # failed spend charges nothing

    def test_budget_charge_accounts(self):
        budget = TokenBudget(10)
        budget.charge("a" * 40)  # 10 tokens
        self.assertEqual(budget.remaining(), 0)


if __name__ == "__main__":
    unittest.main()
