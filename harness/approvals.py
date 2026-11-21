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
    """One draft waiting for (or having received) a human decision.

    `writes` is the machine-readable write plan a write draft carries
    (v1.3.0): the ops the executor runs if the draft is approved.
    Editing the draft text does NOT change the write plan; to change
    what will execute, re-run the agent that drafted it. `execution`
    is the recorded result of running the plan (one result per op, in
    plan order), and `executed_at` is when the plan last ran. A draft
    with no writes has both empty: read-only drafts execute nothing.
    """

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
    writes: list[dict] = field(default_factory=list)
    execution: list[dict] = field(default_factory=list)
    executed_at: str | None = None

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
            writes=data.get("writes", []),
            execution=data.get("execution", []),
            executed_at=data.get("executed_at"),
        )


class ApprovalQueue:
    """File-backed queue of drafts awaiting human review.

    Takes an optional AccountMemory store: when one is attached,
    every decision (approve, reject, edit) is recorded as memory for
    that draft's account, so the next run for the account learns from
    it. Memory never breaks the queue: a failed record is swallowed
    here, and a queue built without a store behaves exactly as
    before.

    Takes an optional EscalationStore (v1.7.0): when one is attached,
    approving an escalation-manager draft moves its escalation
    records (open -> escalated) and resolves the records its closure
    section names. A failed transition is swallowed the same way:
    the queue decision stands either way.
    """

    def __init__(self, path: str = "approvals.json", memory=None, escalations=None) -> None:
        self.path = path
        self.memory = memory
        self.escalations = escalations
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
        writes: list[dict] | None = None,
    ) -> ApprovalItem:
        """Put a new draft in the queue as pending. Returns the item.

        `writes` is the draft's write plan (v1.3.0): the ops the
        executor runs if, and only if, a human approves the draft."""
        item = ApprovalItem(
            id="appr-" + uuid.uuid4().hex[:8],
            agent=agent,
            job=job,
            draft=draft,
            sources=list(sources or []),
            links=list(links or []),
            proof=list(proof or []),
            writes=list(writes or []),
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

    def _escalation_outcome(self, item: ApprovalItem) -> None:
        """Move escalation records on an approved escalation-manager
        draft (v1.7.0): its records go open -> escalated, and the
        records its closure section names go to resolved. A failed
        transition never breaks the queue decision."""
        if self.escalations is None or item.agent != "escalation-manager":
            return
        try:
            for record_id in item.job.get("escalation_records", []) or []:
                self.escalations.mark_escalated(str(record_id))
            for record_id in item.job.get("escalation_resolve", []) or []:
                self.escalations.resolve(str(record_id))
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
        if status == "approved":
            self._escalation_outcome(item)
        return item

    def record_execution(
        self, item_id: str, execution: list[dict], executed_at: str
    ) -> ApprovalItem:
        """Record the result of running a draft's write plan (v1.3.0).
        Used by the executor in harness/writeback.py after it runs the
        plan; it only records, it never decides or executes."""
        item = self.get(item_id)
        if item is None:
            raise KeyError("Unknown approval item: %r" % item_id)
        item.execution = list(execution)
        item.executed_at = executed_at
        self._save()
        return item

    def approve(self, item_id: str, note: str | None = None) -> ApprovalItem:
        """Mark a draft approved. Only approved drafts leave the harness.

        Approving a write draft (v1.3.0) does not itself execute the
        write plan: the caller (CLI or dashboard) runs the executor
        after this returns. The plan executes only from this approved
        state, and only once per op."""
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

    def remove(self, item_id: str) -> bool:
        """Delete one item from the queue. True when one was removed.
        Used by the demo-data clear (v1.12.0): only the seeded ids
        are removed, never anything else."""
        if item_id not in self._items:
            return False
        del self._items[item_id]
        self._save()
        return True
