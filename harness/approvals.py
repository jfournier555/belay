"""Approval queue: a human reads the draft, not the whole thread.

This is the correctness mechanism, and the reason the harness is safe to
run against real accounts. No agent output is sent, posted, or acted on
directly. Every draft goes into the queue as pending. A human approves,
rejects, or edits it. Only an approved draft leaves the harness.

The queue is a JSON file (default approvals.json in the working directory).
No database is needed for v1; the file is human-readable and survives
restarts. Statuses: pending, approved, rejected, edited.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ApprovalItem:
    """One draft waiting for (or having received) a human decision."""

    id: str
    agent: str
    job: dict
    draft: str
    sources: list[str] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    proof: list[dict] = field(default_factory=list)
    status: str = "pending"  # pending | approved | rejected | edited
    created_at: str = field(default_factory=_now_iso)
    decided_at: str | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ApprovalItem":
        return cls(
            id=data["id"],
            agent=data["agent"],
            job=data.get("job", {}),
            draft=data["draft"],
            sources=data.get("sources", []),
            links=data.get("links", []),
            proof=data.get("proof", []),
            status=data.get("status", "pending"),
            created_at=data.get("created_at", _now_iso()),
            decided_at=data.get("decided_at"),
            note=data.get("note"),
        )


class ApprovalQueue:
    """File-backed queue of drafts awaiting human review.

    Takes an optional AccountMemory store: when one is attached,
    every decision (approve, reject, edit) is recorded as memory for
    that draft's account, so the next run for the account learns from
    it. Memory never breaks the queue: a failed record is swallowed
    here, and a queue built without a store behaves exactly as
    before.
    """

    def __init__(self, path: str = "approvals.json", memory=None) -> None:
        self.path = path
        self.memory = memory
        self._items: dict[str, ApprovalItem] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        for entry in data if isinstance(data, list) else []:
            try:
                item = ApprovalItem.from_dict(entry)
            except (KeyError, TypeError):
                continue
            self._items[item.id] = item

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                [item.to_dict() for item in self._items.values()],
                f,
                indent=2,
            )
        os.replace(tmp, self.path)

    def submit(
        self,
        agent: str,
        job: dict,
        draft: str,
        sources: list[str] | None = None,
        links: list[dict] | None = None,
        proof: list[dict] | None = None,
    ) -> ApprovalItem:
        """Put a new draft in the queue as pending. Returns the item."""
        item = ApprovalItem(
            id="appr-" + uuid.uuid4().hex[:8],
            agent=agent,
            job=job,
            draft=draft,
            sources=list(sources or []),
            links=list(links or []),
            proof=list(proof or []),
        )
        self._items[item.id] = item
        self._save()
        return item

    def get(self, item_id: str) -> ApprovalItem | None:
        """Return one item by id, or None."""
        return self._items.get(item_id)

    def list_pending(self) -> list[ApprovalItem]:
        """Pending items, oldest first."""
        return [
            item
            for item in sorted(
                self._items.values(), key=lambda i: i.created_at
            )
            if item.status == "pending"
        ]

    def list_all(self) -> list[ApprovalItem]:
        """All items, oldest first."""
        return sorted(self._items.values(), key=lambda i: i.created_at)

    def _remember(
        self, item: ApprovalItem, outcome: str, old_draft: str | None = None
    ) -> None:
        """Record a decision as memory for the draft's account. A
        failed record never breaks the queue."""
        if self.memory is None:
            return
        try:
            self.memory.record(item, outcome, old_draft=old_draft)
        except Exception:
            return

    def _decide(
        self, item_id: str, status: str, note: str | None
    ) -> ApprovalItem:
        item = self.get(item_id)
        if item is None:
            raise KeyError("Unknown approval item: %r" % item_id)
        item.status = status
        item.decided_at = _now_iso()
        item.note = note
        self._save()
        self._remember(item, status)
        return item

    def approve(self, item_id: str, note: str | None = None) -> ApprovalItem:
        """Mark a draft approved. Only approved drafts leave the harness."""
        return self._decide(item_id, "approved", note)

    def reject(self, item_id: str, note: str | None = None) -> ApprovalItem:
        """Mark a draft rejected. It stays in the file as a record."""
        return self._decide(item_id, "rejected", note)

    def edit(self, item_id: str, new_draft: str) -> ApprovalItem:
        """Replace a draft with a human-edited version. Status becomes
        edited, which still counts as needing a final approve before use.
        The edit is recorded as memory with the correction itself, so
        the next run for the account learns what the human changed."""
        item = self.get(item_id)
        if item is None:
            raise KeyError("Unknown approval item: %r" % item_id)
        old_draft = item.draft
        item.draft = new_draft
        item.status = "edited"
        item.decided_at = _now_iso()
        self._save()
        self._remember(item, "edited", old_draft=old_draft)
        return item
