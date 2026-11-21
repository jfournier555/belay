"""Per-account memory: the harness remembers what the human decided.

Every run starts cold without it: a CS tool with no memory is a
drafting tool, not a harness. Memory is the feedback loop. When a
draft is approved, edited, or rejected in the queue, the outcome is
recorded for that account. An edit is the highest-value record: the
human's version is the correction the next run should learn from, so
the correction itself is stored (a diff when the change is small,
the edited text when the draft was rewritten), not just a flag.

On the next run for the same account, the records are loaded as
context for the agent, newest first, bounded by the per-job token
budget. Memory is an input to the draft, not a replacement for fresh
tool results: the tools are still gathered first, and memory only
spends what the budget has left.

Boundaries, all deliberate:
- Memory never crosses accounts. Records are keyed by the exact
  account string, and a lookup returns only that account's records.
- Memory is local only. One JSON file (default memory.json in the
  working dir, HARNESS_MEMORY_FILE to move it), same file-backed
  pattern as the approval queue. Nothing in it is ever sent anywhere.
- Memory never breaks a run. A missing or corrupt file reads as no
  memory, and a failed write is swallowed at the queue boundary.
- At most 50 records are kept per account; older ones are pruned on
  record, so the file cannot grow without bound.
"""

from __future__ import annotations

import difflib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

MAX_RECORDS_PER_ACCOUNT = 50
MAX_EXCERPT_CHARS = 300
MAX_CORRECTION_CHARS = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _account_from_job(job: dict) -> str:
    """Work out which account a queued job is about.

    Mirrors agents._extract_account (same rules) so a decision
    recorded here is found again under the same account next run:
    prefer job["account"], else the words after the last "for" in
    the free text, else the raw text. Kept here instead of imported
    because agents.py already imports the approval queue; importing
    agents from here would be a cycle.
    """
    account = str(job.get("account", "") or "").strip()
    if account:
        return account
    text = str(job.get("text", "") or "").strip()
    lower = text.lower()
    idx = lower.rfind(" for ")
    if idx != -1:
        candidate = text[idx + 5 :].strip().rstrip(".,!?")
        if candidate:
            return candidate
    return text


def _correction_diff(old_draft: str, new_draft: str) -> str:
    """What the human changed. A small change stores as a unified
    diff. A wholesale rewrite stores the edited text itself: a diff
    of a rewritten draft is mostly removed lines, and the cap would
    cut off the human's new words. Either way the correction itself
    is stored, capped, never just a flag. Empty when nothing
    changed."""
    diff = "\n".join(
        difflib.unified_diff(
            old_draft.splitlines(),
            new_draft.splitlines(),
            fromfile="draft",
            tofile="edited",
            lineterm="",
            n=1,
        )
    )
    if len(diff) <= MAX_CORRECTION_CHARS:
        return diff
    text = new_draft
    if len(text) > MAX_CORRECTION_CHARS:
        text = text[:MAX_CORRECTION_CHARS] + "\n[... correction cut ...]"
    return "Edited draft:\n" + text


@dataclass
class MemoryRecord:
    """One human decision on one draft for one account."""

    id: str
    account: str
    agent: str
    draft_id: str
    outcome: str  # approved | edited | rejected
    timestamp: str = field(default_factory=_now_iso)
    excerpt: str = ""
    correction: str = ""
    note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryRecord":
        return cls(
            id=data["id"],
            account=data["account"],
            agent=data["agent"],
            draft_id=data["draft_id"],
            outcome=data["outcome"],
            timestamp=data.get("timestamp", _now_iso()),
            excerpt=data.get("excerpt", ""),
            correction=data.get("correction", ""),
            note=data.get("note"),
        )

    def render(self) -> str:
        """One record as prompt context: outcome first, then the
        substance. Edits carry the correction, the part the next run
        should learn from; approvals and rejections carry a short
        excerpt of what was decided on."""
        head = "[%s] %s draft %s: %s." % (
            self.timestamp,
            self.agent,
            self.draft_id,
            self.outcome,
        )
        if self.outcome == "edited" and self.correction:
            return head + "\nHuman correction:\n" + self.correction
        if self.excerpt:
            return head + " " + self.excerpt
        return head


class AccountMemory:
    """File-backed memory, one JSON file, keyed by account."""

    def __init__(self, path: str = "memory.json") -> None:
        self.path = path
        self._records: dict[str, list[MemoryRecord]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(data, dict):
            return
        for account, entries in data.items():
            if not isinstance(entries, list):
                continue
            records = []
            for entry in entries:
                try:
                    records.append(MemoryRecord.from_dict(entry))
                except (KeyError, TypeError):
                    continue
            if records:
                self._records[str(account)] = records

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {
                    account: [record.to_dict() for record in records]
                    for account, records in self._records.items()
                },
                f,
                indent=2,
            )
        os.replace(tmp, self.path)

    def record(
        self, item, outcome: str, old_draft: str | None = None
    ) -> MemoryRecord | None:
        """Record a human decision on a queued draft. `item` is an
        approval-queue item (duck-typed: agent, id, draft, job, note).
        Returns the record, or None when the job names no account.
        Edits store the correction itself (a diff of the draft before
        and after the edit), not just a flag."""
        account = _account_from_job(item.job)
        if not account:
            return None
        new_draft = str(item.draft or "")
        excerpt = " ".join(new_draft.split())
        if len(excerpt) > MAX_EXCERPT_CHARS:
            excerpt = excerpt[:MAX_EXCERPT_CHARS] + " ..."
        correction = ""
        if outcome == "edited" and old_draft is not None:
            correction = _correction_diff(str(old_draft), new_draft)
        record = MemoryRecord(
            id="mem-" + uuid.uuid4().hex[:8],
            account=account,
            agent=item.agent,
            draft_id=item.id,
            outcome=outcome,
            excerpt=excerpt,
            correction=correction,
            note=item.note,
        )
        records = self._records.setdefault(account, [])
        records.append(record)
        self._records[account] = records[-MAX_RECORDS_PER_ACCOUNT:]
        self._save()
        return record

    def records_for(self, account: str) -> list[MemoryRecord]:
        """That account's records, newest first. Exact account match
        only; nothing from another account is ever returned. Ties
        (same created second) break toward the later-recorded item."""
        records = self._records.get(account, [])
        indexed = sorted(
            enumerate(records),
            key=lambda pair: (pair[1].timestamp, pair[0]),
            reverse=True,
        )
        return [record for _index, record in indexed]

    def render(self, account: str, budget) -> str:
        """The account's memory as prompt context, bounded by the
        token budget: newest records first, stops when the next
        record no longer fits. Empty when the account has no memory
        or nothing fits."""
        records = self.records_for(account)
        if not records:
            return ""
        parts = []
        for record in records:
            text = record.render()
            if not budget.spend(text):
                break
            parts.append(text)
        if not parts:
            return ""
        return (
            "Past work for this account (what the human decided last time):\n"
            + "\n".join(parts)
        )

    def clear(self, account: str) -> int:
        """Forget one account's memory. Returns how many records were
        removed. Other accounts are untouched."""
        removed = len(self._records.pop(account, []))
        if removed:
            self._save()
        return removed

    def accounts(self) -> list[tuple[str, int]]:
        """Accounts with memory, and how many records each holds."""
        return sorted(
            (
                (account, len(records))
                for account, records in self._records.items()
            ),
            key=lambda pair: pair[0],
        )
