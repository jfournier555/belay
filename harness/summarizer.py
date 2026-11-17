"""Summary-first tool results: the low-token-use mechanism.

The rule: tools return summaries, never raw transcripts. A Salesforce account
record, a Slack thread, or a Gong call transcript is far more tokens than the
agent needs. The harness keeps the fields the agent acts on and drops the
rest, then enforces a per-job token budget so one bad tool call cannot blow
up the context window.

Token counting here is a rough heuristic (characters / 4), not a real
tokenizer. It is good enough to enforce a budget; it is not a billing
measurement. If exact counts matter, plug in tiktoken and replace
estimate_tokens.
"""

from __future__ import annotations

CHARS_PER_TOKEN = 4
DEFAULT_MAX_CHARS = 2000
DEFAULT_MAX_LIST_ITEMS = 5


def estimate_tokens(text: str) -> int:
    """Rough token estimate for a string. Heuristic: 1 token per 4 chars."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, noting what was cut. No-op when short."""
    if len(text) <= max_chars:
        return text
    cut = len(text) - max_chars
    return text[:max_chars] + "\n[... %d chars cut ...]" % cut


def summarize_value(value, max_chars: int = DEFAULT_MAX_CHARS):
    """Shrink one value to a summary-friendly form.

    - Short strings pass through; long strings are truncated.
    - Lists are capped at DEFAULT_MAX_LIST_ITEMS, with a count of the rest.
    - Dicts are recursed, one level deep, with long values truncated.
    - Numbers, booleans, and None pass through unchanged.
    """
    if isinstance(value, str):
        return truncate_text(value, max_chars)
    if isinstance(value, list):
        items = [summarize_value(v, max_chars) for v in value[:DEFAULT_MAX_LIST_ITEMS]]
        if len(value) > DEFAULT_MAX_LIST_ITEMS:
            items.append("[... %d more items ...]" % (len(value) - DEFAULT_MAX_LIST_ITEMS))
        return items
    if isinstance(value, dict):
        return {
            str(k): summarize_value(v, max_chars) for k, v in value.items()
        }
    return value


def summarize_tool_result(
    raw: dict, max_chars: int = DEFAULT_MAX_CHARS
) -> dict:
    """Turn a raw tool result into its summary form.

    Keeps every top-level key (so the agent still sees the shape of the
    data) but shrinks long strings and caps long lists. The result is what
    gets passed to the LLM, never the raw record.
    """
    if not isinstance(raw, dict):
        return {"value": summarize_value(raw, max_chars)}
    return {str(k): summarize_value(v, max_chars) for k, v in raw.items()}


class TokenBudget:
    """Per-job token budget. Tracks estimated spend and refuses to exceed it.

    Agents check the budget before adding tool results to a prompt. When the
    budget is exhausted, the agent stops gathering and works with what it
    has, rather than failing the job.
    """

    def __init__(self, max_tokens: int) -> None:
        self.max_tokens = max_tokens
        self.spent = 0

    def remaining(self) -> int:
        """Estimated tokens left in the budget (never below 0)."""
        return max(0, self.max_tokens - self.spent)

    def spend(self, text: str) -> bool:
        """Charge `text` against the budget. Returns True when it fits and
        was charged, False when it would exceed the budget (not charged)."""
        cost = estimate_tokens(text)
        if self.spent + cost > self.max_tokens:
            return False
        self.spent += cost
        return True

    def charge(self, text: str) -> int:
        """Charge `text` and return the cost, even if it exceeds the budget.
        Use for accounting after the fact, not for gating."""
        cost = estimate_tokens(text)
        self.spent += cost
        return cost
