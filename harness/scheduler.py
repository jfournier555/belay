"""Scheduling: the harness runs on its own.

A watcher that only runs when typed is not watching. A schedule names an
agent, an account, and a cadence (daily or weekly, wall-clock time in a
named timezone). The scheduler runs due jobs by the same path a manual
`run` takes, so a scheduled run drafts into the approval queue exactly
like a typed one: it never writes back without human approval (the
v1.3.0 gate still applies), it respects the per-job token budget and
the memory controls, and a failed run is logged and does not block the
next job.

Two modes, one due check:
- `scheduler --once`: run every due job now, then exit. This is the
  mode a system cron calls (e.g. every 5 minutes). Exit 0 when every
  due job ran, 1 when any due job failed, so cron can report it.
- `scheduler` (daemon): hold a lock so two daemons cannot run at
  once, wake when the next job is due, run it, sleep again. Reloads
  the schedules file every cycle, so edits take effect without a
  restart. Stops cleanly on SIGINT/SIGTERM.

The schedule store is one JSON file (default schedules.json in the
working dir, HARNESS_SCHEDULES_FILE to move it), same file-backed
pattern as the approval queue and memory. It is human-editable: one
object per schedule, all fields plain values.

Boundaries, all deliberate:
- A schedule never executes a write. Scheduled runs draft only; the
  approval gate is the only way a write happens, same as manual runs.
- A missed run fires once, not once per missed cadence: after a run
  (success or failure) the schedule's last-run time moves to now, so
  a daemon that was down for a week does not queue a week of drafts.
- A corrupt or unreadable schedules file reads as no schedules, and
  an invalid entry is skipped, never fatal. A failed scheduled run
  never stops the daemon or the next job.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CADENCES = ("daily", "weekly")
WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class SchedulerAlreadyRunning(Exception):
    """A scheduler daemon (or another run-once) already holds the lock."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_time(raw: str) -> tuple[int, int]:
    """Parse an HH:MM 24-hour time. Raises ValueError on bad input."""
    parts = str(raw or "").strip().split(":")
    if len(parts) != 2:
        raise ValueError("time must be HH:MM, got %r" % raw)
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        raise ValueError("time must be HH:MM, got %r" % raw)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("time must be HH:MM, got %r" % raw)
    return hour, minute


def _parse_timezone(raw: str) -> ZoneInfo:
    """Resolve an IANA timezone name. Raises ValueError on unknown zones."""
    name = str(raw or "").strip()
    if not name:
        raise ValueError("timezone must be an IANA name, e.g. America/Los_Angeles")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise ValueError("unknown timezone %r" % name)


def _parse_weekday(raw) -> int:
    """Parse a weekday as a name (mon, Monday) or number (0=Mon..6=Sun)."""
    if isinstance(raw, int) and not isinstance(raw, bool):
        if 0 <= raw <= 6:
            return raw
        raise ValueError("weekday must be 0-6 (Mon-Sun), got %r" % raw)
    name = str(raw or "").strip().lower()
    if name in WEEKDAYS:
        return WEEKDAYS[name]
    if name.isdigit() and 0 <= int(name) <= 6:
        return int(name)
    raise ValueError("weekday must be a day name or 0-6 (Mon-Sun), got %r" % raw)


def _parse_iso(raw: str | None) -> datetime | None:
    """Parse an ISO timestamp, or None when missing/unparseable."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class Schedule:
    """One scheduled job: an agent, an account, and when to run it."""

    id: str
    agent: str
    account: str
    cadence: str  # daily | weekly
    time: str  # HH:MM, wall-clock in `timezone`
    timezone: str = "UTC"
    weekday: int | None = None  # 0=Mon..6=Sun, required for weekly
    text: str = ""
    hourly_rate: float | None = None
    hours_saved: float | None = None
    changes: list[str] = field(default_factory=list)
    no_memory: bool = False
    stub: bool = False
    enabled: bool = True
    created_at: str = field(default_factory=_now_iso)
    last_run: str | None = None
    last_status: str | None = None  # ok | failed
    last_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Schedule":
        return cls(
            id=data["id"],
            agent=data["agent"],
            account=data["account"],
            cadence=data["cadence"],
            time=data["time"],
            timezone=data.get("timezone", "UTC"),
            weekday=data.get("weekday"),
            text=data.get("text", ""),
            hourly_rate=data.get("hourly_rate"),
            hours_saved=data.get("hours_saved"),
            changes=list(data.get("changes", [])),
            no_memory=bool(data.get("no_memory", False)),
            stub=bool(data.get("stub", False)),
            enabled=bool(data.get("enabled", True)),
            created_at=data.get("created_at", _now_iso()),
            last_run=data.get("last_run"),
            last_status=data.get("last_status"),
            last_error=data.get("last_error"),
        )

    def describe(self) -> str:
        """One line for `schedules list`."""
        when = "%s %s %s" % (self.cadence, self.time, self.timezone)
        if self.cadence == "weekly" and self.weekday is not None:
            when = "weekly %s %s %s" % (
                WEEKDAY_NAMES[self.weekday],
                self.time,
                self.timezone,
            )
        state = "enabled" if self.enabled else "disabled"
        last = ""
        if self.last_run:
            last = "  last run %s (%s)" % (self.last_run, self.last_status or "?")
            if self.last_error:
                last += ": %s" % self.last_error
        return "%s  %-20s %-30s %-32s %s%s" % (
            self.id,
            self.agent,
            self.account,
            when,
            state,
            last,
        )


def validate_schedule(data: dict, valid_agents=None) -> dict:
    """Check a schedule's fields before it is stored. Returns a
    cleaned copy. Raises ValueError with a plain message on bad input.
    `valid_agents` is a set of agent names; when given, the agent must
    be one of them."""
    agent = str(data.get("agent", "") or "").strip()
    if not agent:
        raise ValueError("schedule needs --agent")
    if valid_agents is not None and agent not in valid_agents:
        raise ValueError("unknown agent %r" % agent)
    account = str(data.get("account", "") or "").strip()
    if not account:
        raise ValueError("schedule needs --account")
    cadence = str(data.get("cadence", "") or "").strip().lower()
    if cadence not in CADENCES:
        raise ValueError("cadence must be daily or weekly, got %r" % data.get("cadence"))
    hour, minute = _parse_time(data.get("time", ""))
    timezone_name = str(data.get("timezone", "UTC") or "UTC").strip()
    _parse_timezone(timezone_name)
    weekday = None
    if cadence == "weekly":
        if data.get("weekday") is None:
            raise ValueError("weekly schedules need --weekday")
        weekday = _parse_weekday(data.get("weekday"))
    elif data.get("weekday") is not None:
        weekday = _parse_weekday(data.get("weekday"))
    hourly_rate = data.get("hourly_rate")
    if hourly_rate is not None:
        try:
            hourly_rate = float(hourly_rate)
        except (TypeError, ValueError):
            raise ValueError("--hourly-rate must be a number")
        if hourly_rate <= 0:
            raise ValueError("--hourly-rate must be positive")
    hours_saved = data.get("hours_saved")
    if hours_saved is not None:
        try:
            hours_saved = float(hours_saved)
        except (TypeError, ValueError):
            raise ValueError("--hours-saved must be a number")
        if hours_saved < 0:
            raise ValueError("--hours-saved must not be negative")
    changes = data.get("changes") or []
    if isinstance(changes, str):
        changes = [changes]
    return {
        "agent": agent,
        "account": account,
        "cadence": cadence,
        "time": "%02d:%02d" % (hour, minute),
        "timezone": timezone_name,
        "weekday": weekday,
        "text": str(data.get("text", "") or ""),
        "hourly_rate": hourly_rate,
        "hours_saved": hours_saved,
        "changes": [str(c) for c in changes],
        "no_memory": bool(data.get("no_memory", False)),
        "stub": bool(data.get("stub", False)),
        "enabled": bool(data.get("enabled", True)),
    }


def next_run_after(schedule: Schedule, after: datetime) -> datetime:
    """First scheduled time strictly after `after` (aware UTC).

    The time is wall-clock in the schedule's timezone: a daily 07:00
    America/Los_Angeles job fires at 07:00 local time even across a
    daylight-saving change."""
    tz = _parse_timezone(schedule.timezone)
    hour, minute = _parse_time(schedule.time)
    local_after = after.astimezone(tz)
    candidate = local_after.replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if schedule.cadence == "daily":
        if candidate <= local_after:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)
    days_ahead = (schedule.weekday - local_after.weekday()) % 7
    candidate = candidate + timedelta(days=days_ahead)
    if candidate <= local_after:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def is_due(schedule: Schedule, now: datetime | None = None) -> bool:
    """True when the schedule's next run time has arrived. Disabled
    schedules are never due. The reference point is the last run (or
    creation when it never ran): a job is due once its next scheduled
    time after that point has passed."""
    if not schedule.enabled:
        return False
    now = now or datetime.now(timezone.utc)
    reference = _parse_iso(schedule.last_run) or _parse_iso(
        schedule.created_at
    ) or now
    return now >= next_run_after(schedule, reference)


class ScheduleStore:
    """File-backed schedule store, one JSON file.

    Same pattern as the approval queue and memory: a missing or
    corrupt file reads as no schedules, an invalid entry is skipped,
    saves are atomic (tmp file + replace)."""

    def __init__(self, path: str = "schedules.json") -> None:
        self.path = path
        self._schedules: dict[str, Schedule] = {}
        self._load()

    def _load(self) -> None:
        self._schedules = {}
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        for entry in data if isinstance(data, list) else []:
            try:
                schedule = Schedule.from_dict(entry)
                validate_schedule(entry)
            except (KeyError, TypeError, ValueError):
                continue
            self._schedules[schedule.id] = schedule

    def reload(self) -> None:
        """Re-read the file from disk, so edits take effect without a
        restart. The daemon calls this every cycle."""
        self._load()

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                [s.to_dict() for s in self._schedules.values()],
                f,
                indent=2,
            )
        os.replace(tmp, self.path)

    def list(self) -> list[Schedule]:
        """All schedules, oldest first."""
        return sorted(
            self._schedules.values(), key=lambda s: s.created_at
        )

    def get(self, schedule_id: str) -> Schedule | None:
        """One schedule by id, or None."""
        return self._schedules.get(schedule_id)

    def add(self, data: dict, valid_agents=None) -> Schedule:
        """Validate and store a new schedule. Returns it."""
        cleaned = validate_schedule(data, valid_agents=valid_agents)
        schedule = Schedule(
            id="sched-" + uuid.uuid4().hex[:8],
            **cleaned,
        )
        self._schedules[schedule.id] = schedule
        self._save()
        return schedule

    def remove(self, schedule_id: str) -> bool:
        """Delete a schedule. True when one was removed."""
        if schedule_id not in self._schedules:
            return False
        del self._schedules[schedule_id]
        self._save()
        return True

    def record_run(
        self,
        schedule: Schedule,
        status: str,
        error: str | None = None,
        at: datetime | None = None,
    ) -> None:
        """Mark a run attempt (success or failure) on the schedule.
        The last-run time moves to now either way: a missed run fires
        once, and a failed run retries at the next cadence, not in a
        tight loop. The error is kept on the record for `schedules
        list` to show."""
        schedule.last_run = (at or datetime.now(timezone.utc)).isoformat(
            timespec="seconds"
        )
        schedule.last_status = status
        schedule.last_error = error
        self._save()


@dataclass
class RunResult:
    """What happened to one schedule in one due pass."""

    schedule_id: str
    agent: str
    account: str
    status: str  # ok | failed | skipped
    reason: str = ""
    item_id: str | None = None
    error: str | None = None


def run_due(
    store: ScheduleStore,
    config,
    executor,
    now: datetime | None = None,
) -> list[RunResult]:
    """Run every due schedule once, in store order.

    `executor(schedule, config)` runs one job and returns the queued
    approval item id. A failure in one job is recorded on the schedule
    and returned as a failed result; it never blocks the next job.
    Schedules that are disabled or not yet due come back as skipped,
    so the caller can report what was (and was not) run."""
    now = now or datetime.now(timezone.utc)
    results: list[RunResult] = []
    for schedule in store.list():
        if not schedule.enabled:
            results.append(
                RunResult(
                    schedule_id=schedule.id,
                    agent=schedule.agent,
                    account=schedule.account,
                    status="skipped",
                    reason="disabled",
                )
            )
            continue
        if not is_due(schedule, now):
            results.append(
                RunResult(
                    schedule_id=schedule.id,
                    agent=schedule.agent,
                    account=schedule.account,
                    status="skipped",
                    reason="not yet due (next %s)"
                    % next_run_after(
                        schedule,
                        _parse_iso(schedule.last_run)
                        or _parse_iso(schedule.created_at)
                        or now,
                    ).isoformat(timespec="seconds"),
                )
            )
            continue
        try:
            item_id = executor(schedule, config)
        except Exception as exc:  # one job's failure never blocks the next
            store.record_run(schedule, "failed", error=str(exc), at=now)
            results.append(
                RunResult(
                    schedule_id=schedule.id,
                    agent=schedule.agent,
                    account=schedule.account,
                    status="failed",
                    error=str(exc),
                )
            )
            continue
        store.record_run(schedule, "ok", at=now)
        results.append(
            RunResult(
                schedule_id=schedule.id,
                agent=schedule.agent,
                account=schedule.account,
                status="ok",
                item_id=item_id,
            )
        )
    return results


@contextmanager
def scheduler_lock(lock_path: str):
    """Hold an exclusive lock for the scheduler's lifetime.

    Raises SchedulerAlreadyRunning when another daemon (or run-once)
    already holds it. Uses flock where available, with an exclusive
    create as the fallback."""
    try:
        import fcntl

        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise SchedulerAlreadyRunning(
                "another scheduler already holds %s" % lock_path
            )
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
    except ImportError:
        try:
            fd = os.open(
                lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError:
            raise SchedulerAlreadyRunning(
                "another scheduler already holds %s" % lock_path
            )
        try:
            yield
        finally:
            os.close(fd)
            try:
                os.unlink(lock_path)
            except OSError:
                pass


def run_daemon(
    store: ScheduleStore,
    config,
    executor,
    *,
    interval: int = 60,
    stop: threading.Event | None = None,
) -> int:
    """Run due jobs on schedule until stopped.

    Holds the single-instance lock for the whole run. Each cycle
    reloads the schedules file, runs due jobs, then sleeps until the
    next job is due or `interval` seconds, whichever is sooner, so a
    schedule edit is picked up within a minute. Returns 0 on a clean
    stop, 2 when another scheduler already holds the lock. `stop` is
    set by the CLI's signal handlers."""
    stop = stop or threading.Event()
    lock_path = store.path + ".lock"
    try:
        with scheduler_lock(lock_path):
            while not stop.is_set():
                store.reload()
                run_due(store, config, executor)
                now = datetime.now(timezone.utc)
                wake_in = float(interval)
                for schedule in store.list():
                    if not schedule.enabled:
                        continue
                    reference = _parse_iso(schedule.last_run) or _parse_iso(
                        schedule.created_at
                    ) or now
                    seconds = (
                        next_run_after(schedule, reference) - now
                    ).total_seconds()
                    if seconds < wake_in:
                        wake_in = max(1.0, seconds)
                stop.wait(timeout=wake_in)
    except SchedulerAlreadyRunning as exc:
        print("Scheduler: %s" % exc)
        return 2
    return 0


def install_stop_handlers(stop: threading.Event) -> None:
    """SIGINT/SIGTERM set the stop event, so the daemon exits cleanly.
    A second signal re-raises, so a stuck daemon can still be killed."""
    original_int = signal.getsignal(signal.SIGINT)
    original_term = signal.getsignal(signal.SIGTERM)

    def _handler(signum, frame):
        stop.set()
        signal.signal(signal.SIGINT, original_int)
        signal.signal(signal.SIGTERM, original_term)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
