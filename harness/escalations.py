"""Escalation state: one record per risk signal per account.

When escalation-manager drafts an escalation plan for a high-severity
signal, the signal gets a record here. The record is what stops the
same risk being escalated twice: a second run for the same account and
the same signal finds the record and drafts a status note instead of
a second plan. A resolved record stays resolved: even if the signal
fires again, the harness does not re-escalate it.

Statuses, and who moves them:
- open: the signal fired and a plan was drafted, awaiting a human
  decision in the queue. A rejected plan leaves the record open: the
  human rejected the plan, not the risk, so the next run may draft
  again (the rejection is in memory, so the next draft knows).
- escalated: the human approved an escalation plan in the queue, so
  the escalation is in flight. The queue drives this: approving an
  escalation-manager draft marks its records escalated.
- resolved: the human decided the escalation is done. Two paths, both
  human: the `escalations resolve` command, or approving a closure
  draft the agent wrote when the signal was no longer detected. The
  agent never resolves a record on its own.

Boundaries, all deliberate:
- State is local only. One JSON file (default escalations.json in the
  working dir, HARNESS_ESCALATIONS_FILE to move it), same file-backed
  pattern as the approval queue and memory. Nothing in it is ever sent
  anywhere.
- State never breaks a run. A missing or corrupt file reads as no
  escalations, and a failed write is swallowed at the queue boundary.
- One record per (account, signal key). Creating a record for a key
  that already has one returns the existing record: the same signal
  can never open two records.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

STATUSES = ("open", "escalated", "resolved")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class EscalationRecord:
    """One escalation for one risk signal on one account."""

    id: str
    account: str
    signal_key: str
    signal_kind: str
    severity: str
    summary: str
    status: str = "open"  # open | escalated | resolved
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    draft_id: str | None = None
    resolved_at: str | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EscalationRecord":
        return cls(
            id=data["id"],
            account=data["account"],
            signal_key=data["signal_key"],
            signal_kind=data.get("signal_kind", ""),
            severity=data.get("severity", "high"),
            summary=data.get("summary", ""),
            status=data.get("status", "open"),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", data.get("created_at", _now_iso())),
            draft_id=data.get("draft_id"),
            resolved_at=data.get("resolved_at"),
            note=data.get("note"),
        )

    def describe(self) -> str:
        """One line for `escalations list`: id, account, signal, state."""
        return "%s  %-14s %-22s %-10s %s" % (
            self.id,
            self.account,
            self.signal_key,
            self.status,
            self.summary,
        )


class EscalationStore:
    """File-backed escalation records, one JSON file.

    `path=None` means in-memory only: no file is read or written. The
    agent uses that when a job names no escalations file, so a direct
    agent call in a test never touches the working directory.
    """

    def __init__(self, path: str | None = "escalations.json") -> None:
        self.path = path
        self._records: dict[str, EscalationRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        for entry in data if isinstance(data, list) else []:
            try:
                record = EscalationRecord.from_dict(entry)
            except (KeyError, TypeError):
                continue
            self._records[record.id] = record

    def _save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                [record.to_dict() for record in self._records.values()],
                f,
                indent=2,
            )
        os.replace(tmp, self.path)

    def create(
        self,
        account: str,
        signal_key: str,
        signal_kind: str,
        severity: str,
        summary: str,
    ) -> EscalationRecord:
        """Open a record for a signal, or return the existing one for
        that (account, signal key). One record per signal per account:
        the same signal can never open two records."""
        existing = self.record_for(account, signal_key)
        if existing is not None:
            return existing
        record = EscalationRecord(
            id="esc-" + uuid.uuid4().hex[:8],
            account=account,
            signal_key=signal_key,
            signal_kind=signal_kind,
            severity=severity,
            summary=summary,
        )
        self._records[record.id] = record
        self._save()
        return record

    def get(self, record_id: str) -> EscalationRecord | None:
        """One record by id, or None."""
        return self._records.get(record_id)

    def record_for(
        self, account: str, signal_key: str
    ) -> EscalationRecord | None:
        """The record for one signal on one account, any status, or
        None. Exact account and key match only."""
        for record in self._records.values():
            if record.account == account and record.signal_key == signal_key:
                return record
        return None

    def for_account(self, account: str) -> list[EscalationRecord]:
        """That account's records, oldest first."""
        return sorted(
            (r for r in self._records.values() if r.account == account),
            key=lambda r: r.created_at,
        )

    def list_all(self) -> list[EscalationRecord]:
        """All records, oldest first."""
        return sorted(
            self._records.values(), key=lambda r: r.created_at
        )

    def set_draft(self, record_id: str, draft_id: str) -> bool:
        """Link a record to the queue draft that carries its plan.
        True when the record exists."""
        record = self._records.get(record_id)
        if record is None:
            return False
        record.draft_id = draft_id
        record.updated_at = _now_iso()
        self._save()
        return True

    def mark_escalated(self, record_id: str) -> bool:
        """Open -> escalated: the human approved the escalation plan.
        Only an open record moves; an escalated or resolved record
        stays as it is. True when the record exists and moved."""
        record = self._records.get(record_id)
        if record is None or record.status != "open":
            return False
        record.status = "escalated"
        record.updated_at = _now_iso()
        self._save()
        return True

    def resolve(self, record_id: str, note: str | None = None) -> bool:
        """Mark a record resolved. A human decision only: the agent
        never calls this. True when the record exists and moved; an
        already-resolved record stays resolved."""
        record = self._records.get(record_id)
        if record is None or record.status == "resolved":
            return False
        record.status = "resolved"
        record.resolved_at = _now_iso()
        record.updated_at = _now_iso()
        if note:
            record.note = note
        self._save()
        return True
