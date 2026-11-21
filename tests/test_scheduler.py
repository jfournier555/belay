"""Tests for scheduling (v1.4.0): store, due selection, run-once,
daemon lock, and the draft-only safety rule."""

import io
import json
import os
import tempfile
import threading
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone

from harness import __version__
from harness.run import main
from harness.scheduler import (
    Schedule,
    ScheduleStore,
    SchedulerAlreadyRunning,
    is_due,
    next_run_after,
    run_daemon,
    run_due,
    scheduler_lock,
    validate_schedule,
)

UTC = timezone.utc


def _dt(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


class _EnvCase(unittest.TestCase):
    """Base: temp dir + env vars pointed at it, restored after."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = {}
        for name, filename in (
            ("HARNESS_SCHEDULES_FILE", "schedules.json"),
            ("HARNESS_APPROVALS_FILE", "approvals.json"),
            ("HARNESS_MEMORY_FILE", "memory.json"),
        ):
            self._saved[name] = os.environ.get(name)
            os.environ[name] = os.path.join(self._tmp.name, filename)
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _run_cli(self, argv):
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _write_schedules(self, entries):
        """Write schedule dicts straight to the schedules file.

        The CLI stamps created_at with now, and a fresh schedule is
        never due until its next occurrence, so due-path tests need
        chosen created_at values: two days ago means due, now means
        not yet due."""
        path = os.environ["HARNESS_SCHEDULES_FILE"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f)

    def _entry(self, agent, account, created_days_ago=2, **overrides):
        created = (
            datetime.now(timezone.utc) - timedelta(days=created_days_ago)
        ).isoformat(timespec="seconds")
        entry = {
            "id": "sched-" + uuid.uuid4().hex[:8],
            "agent": agent,
            "account": account,
            "cadence": "daily",
            "time": "00:00",
            "timezone": "UTC",
            "created_at": created,
            "stub": True,
        }
        entry.update(overrides)
        return entry


class TestValidation(unittest.TestCase):
    def test_valid_daily(self):
        cleaned = validate_schedule(
            {
                "agent": "briefer",
                "account": "Acme Corp",
                "cadence": "daily",
                "time": "07:00",
                "timezone": "America/Los_Angeles",
            },
            valid_agents={"briefer"},
        )
        self.assertEqual(cleaned["cadence"], "daily")
        self.assertEqual(cleaned["time"], "07:00")
        self.assertIsNone(cleaned["weekday"])

    def test_unknown_agent_rejected(self):
        with self.assertRaises(ValueError):
            validate_schedule(
                {
                    "agent": "nope",
                    "account": "Acme Corp",
                    "cadence": "daily",
                    "time": "07:00",
                },
                valid_agents={"briefer"},
            )

    def test_bad_cadence_time_timezone(self):
        base = {
            "agent": "briefer",
            "account": "Acme Corp",
            "cadence": "daily",
            "time": "07:00",
        }
        for bad in (
            {"cadence": "hourly"},
            {"time": "7am"},
            {"time": "25:00"},
            {"time": "07:60"},
            {"timezone": "Not/AZone"},
        ):
            with self.assertRaises(ValueError, msg=str(bad)):
                validate_schedule({**base, **bad})

    def test_weekly_needs_weekday(self):
        with self.assertRaises(ValueError):
            validate_schedule(
                {
                    "agent": "briefer",
                    "account": "Acme Corp",
                    "cadence": "weekly",
                    "time": "07:00",
                }
            )
        cleaned = validate_schedule(
            {
                "agent": "briefer",
                "account": "Acme Corp",
                "cadence": "weekly",
                "time": "07:00",
                "weekday": "Friday",
            }
        )
        self.assertEqual(cleaned["weekday"], 4)
        cleaned = validate_schedule(
            {
                "agent": "briefer",
                "account": "Acme Corp",
                "cadence": "weekly",
                "time": "07:00",
                "weekday": 0,
            }
        )
        self.assertEqual(cleaned["weekday"], 0)
        with self.assertRaises(ValueError):
            validate_schedule(
                {
                    "agent": "briefer",
                    "account": "Acme Corp",
                    "cadence": "weekly",
                    "time": "07:00",
                    "weekday": "Funday",
                }
            )

    def test_bad_rate_and_hours(self):
        base = {
            "agent": "value-finder",
            "account": "Acme Corp",
            "cadence": "daily",
            "time": "07:00",
        }
        with self.assertRaises(ValueError):
            validate_schedule({**base, "hourly_rate": 0})
        with self.assertRaises(ValueError):
            validate_schedule({**base, "hourly_rate": "abc"})
        with self.assertRaises(ValueError):
            validate_schedule({**base, "hours_saved": -1})


class TestStore(_EnvCase):
    def test_add_list_remove_round_trip(self):
        store = ScheduleStore(os.environ["HARNESS_SCHEDULES_FILE"])
        self.assertEqual(store.list(), [])
        item = store.add(
            {
                "agent": "briefer",
                "account": "Acme Corp",
                "cadence": "daily",
                "time": "07:00",
                "timezone": "America/Los_Angeles",
            },
            valid_agents={"briefer"},
        )
        self.assertTrue(item.id.startswith("sched-"))
        self.assertEqual(len(store.list()), 1)
        self.assertIsNotNone(store.get(item.id))
        # A fresh store reads the file back.
        reloaded = ScheduleStore(os.environ["HARNESS_SCHEDULES_FILE"])
        self.assertEqual(len(reloaded.list()), 1)
        self.assertEqual(reloaded.get(item.id).account, "Acme Corp")
        self.assertTrue(store.remove(item.id))
        self.assertFalse(store.remove(item.id))
        self.assertEqual(store.list(), [])

    def test_corrupt_file_reads_as_empty(self):
        path = os.environ["HARNESS_SCHEDULES_FILE"]
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(ScheduleStore(path).list(), [])

    def test_invalid_entry_skipped_not_fatal(self):
        path = os.environ["HARNESS_SCHEDULES_FILE"]
        good = {
            "id": "sched-good0001",
            "agent": "briefer",
            "account": "Acme Corp",
            "cadence": "daily",
            "time": "07:00",
            "timezone": "UTC",
            "created_at": "2026-11-21T00:00:00+00:00",
        }
        bad = dict(good, id="sched-bad00001", cadence="hourly")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([good, bad], f)
        store = ScheduleStore(path)
        self.assertEqual([s.id for s in store.list()], ["sched-good0001"])


class TestDueSelection(unittest.TestCase):
    def _schedule(self, **overrides):
        data = {
            "id": "sched-test0001",
            "agent": "briefer",
            "account": "Acme Corp",
            "cadence": "daily",
            "time": "07:00",
            "timezone": "America/Los_Angeles",
            "created_at": "2026-11-21T00:00:00+00:00",
        }
        data.update(overrides)
        return Schedule.from_dict(data)

    def test_daily_next_run_wall_clock(self):
        # Nov 21 2026 is PST (UTC-8): 07:00 local is 15:00 UTC.
        schedule = self._schedule()
        self.assertEqual(
            next_run_after(schedule, _dt(2026, 11, 21, 0, 0)),
            _dt(2026, 11, 21, 15, 0),
        )
        # After today's run time, the next run is tomorrow.
        self.assertEqual(
            next_run_after(schedule, _dt(2026, 11, 21, 16, 0)),
            _dt(2026, 11, 22, 15, 0),
        )

    def test_daily_wall_clock_across_dst(self):
        # Jul 2026 is PDT (UTC-7): 07:00 local is 14:00 UTC. The time
        # is wall-clock in the timezone, not a fixed UTC offset.
        schedule = self._schedule(created_at="2026-07-01T00:00:00+00:00")
        self.assertEqual(
            next_run_after(schedule, _dt(2026, 7, 1, 0, 0)),
            _dt(2026, 7, 1, 14, 0),
        )

    def test_weekly_next_run(self):
        # Nov 21 2026 is a Saturday; next Monday is Nov 23.
        schedule = self._schedule(
            cadence="weekly", weekday=0, created_at="2026-11-21T00:00:00+00:00"
        )
        self.assertEqual(
            next_run_after(schedule, _dt(2026, 11, 21, 0, 0)),
            _dt(2026, 11, 23, 15, 0),
        )
        # Same weekday, after the run time: next week.
        self.assertEqual(
            next_run_after(schedule, _dt(2026, 11, 23, 16, 0)),
            _dt(2026, 11, 30, 15, 0),
        )

    def test_is_due_before_and_at_run_time(self):
        schedule = self._schedule()
        self.assertFalse(is_due(schedule, _dt(2026, 11, 21, 14, 59)))
        self.assertTrue(is_due(schedule, _dt(2026, 11, 21, 15, 0)))
        self.assertTrue(is_due(schedule, _dt(2026, 11, 21, 16, 0)))

    def test_disabled_never_due(self):
        schedule = self._schedule(enabled=False)
        self.assertFalse(is_due(schedule, _dt(2026, 11, 21, 16, 0)))

    def test_last_run_moves_reference_forward(self):
        schedule = self._schedule(last_run="2026-11-21T15:00:00+00:00")
        # Ran at today's 07:00 local; not due again until tomorrow.
        self.assertFalse(is_due(schedule, _dt(2026, 11, 21, 16, 0)))
        self.assertTrue(is_due(schedule, _dt(2026, 11, 22, 15, 0)))


class TestRunDue(_EnvCase):
    def _store_with(self, **overrides):
        # Written straight to the file (not via add): the test's fixed
        # "now" needs a chosen created_at, and add() stamps real now.
        entry = self._entry("briefer", "Acme Corp", created_days_ago=2, **overrides)
        self._write_schedules([entry])
        store = ScheduleStore(os.environ["HARNESS_SCHEDULES_FILE"])
        return store, store.get(entry["id"])

    def test_due_job_runs_and_records_ok(self):
        store, schedule = self._store_with()
        calls = []

        def executor(sched, config):
            calls.append(sched.id)
            return "appr-12345678"

        results = run_due(store, None, executor, now=_dt(2026, 11, 21, 8, 0))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "ok")
        self.assertEqual(results[0].item_id, "appr-12345678")
        self.assertEqual(calls, [schedule.id])
        self.assertEqual(store.get(schedule.id).last_status, "ok")
        self.assertEqual(
            store.get(schedule.id).last_run, "2026-11-21T08:00:00+00:00"
        )

    def test_not_yet_due_skipped_with_reason(self):
        store, _ = self._store_with(last_run="2026-11-21T05:00:00+00:00")
        results = run_due(
            store, None, lambda s, c: "appr-x", now=_dt(2026, 11, 21, 6, 0)
        )
        self.assertEqual(results[0].status, "skipped")
        self.assertIn("not yet due", results[0].reason)

    def test_failed_job_does_not_block_next(self):
        first = self._entry("briefer", "Acme Corp", created_days_ago=2)
        second = self._entry("briefer", "Globex Inc", created_days_ago=2)
        self._write_schedules([first, second])
        store = ScheduleStore(os.environ["HARNESS_SCHEDULES_FILE"])
        first_id = first["id"]

        def executor(sched, config):
            if sched.id == first_id:
                raise RuntimeError("boom")
            return "appr-ok00001"

        results = run_due(store, None, executor, now=_dt(2026, 11, 21, 8, 0))
        self.assertEqual(
            [r.status for r in results], ["failed", "ok"]
        )
        self.assertEqual(results[0].error, "boom")
        self.assertEqual(store.get(first_id).last_status, "failed")
        self.assertEqual(store.get(first_id).last_error, "boom")
        self.assertEqual(store.get(second["id"]).last_status, "ok")

    def test_missed_run_fires_once_not_per_missed_cadence(self):
        store, schedule = self._store_with()
        calls = []
        results = run_due(
            store,
            None,
            lambda s, c: calls.append(s.id) or "appr-x",
            now=_dt(2026, 11, 25, 8, 0),  # four days late
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(results[0].status, "ok")
        # And it is not due again immediately after.
        results = run_due(
            store,
            None,
            lambda s, c: calls.append(s.id) or "appr-x",
            now=_dt(2026, 11, 25, 8, 1),
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(results[0].status, "skipped")


class TestLock(unittest.TestCase):
    def test_second_lock_holder_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "scheduler.lock")
            with scheduler_lock(path):
                with self.assertRaises(SchedulerAlreadyRunning):
                    with scheduler_lock(path):
                        pass
            # Released: a new holder succeeds.
            with scheduler_lock(path):
                pass


class TestDaemon(_EnvCase):
    def test_stop_preset_runs_nothing(self):
        store = ScheduleStore(os.environ["HARNESS_SCHEDULES_FILE"])
        store.add(
            {
                "agent": "briefer",
                "account": "Acme Corp",
                "cadence": "daily",
                "time": "07:00",
                "timezone": "UTC",
                "created_at": "2026-11-21T00:00:00+00:00",
            }
        )
        calls = []
        stop = threading.Event()
        stop.set()
        code = run_daemon(
            store, None, lambda s, c: calls.append(1) or "appr-x", stop=stop
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])

    def test_daemon_runs_due_then_stops(self):
        self._write_schedules(
            [self._entry("briefer", "Acme Corp", created_days_ago=2)]
        )
        store = ScheduleStore(os.environ["HARNESS_SCHEDULES_FILE"])
        calls = []
        stop = threading.Event()
        result = {}

        def _run():
            result["code"] = run_daemon(
                store,
                None,
                lambda s, c: calls.append(s.id) or "appr-x",
                interval=3600,
                stop=stop,
            )

        thread = threading.Thread(target=_run)
        thread.start()
        # The daemon runs due jobs on its first pass, then waits.
        import time as _time

        _time.sleep(0.5)
        stop.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["code"], 0)
        self.assertEqual(len(calls), 1)

    def test_daemon_refused_when_lock_held(self):
        store = ScheduleStore(os.environ["HARNESS_SCHEDULES_FILE"])
        lock_path = os.environ["HARNESS_SCHEDULES_FILE"] + ".lock"
        with scheduler_lock(lock_path):
            code = run_daemon(
                store, None, lambda s, c: "appr-x", stop=threading.Event()
            )
        self.assertEqual(code, 2)


class TestSafetyAndCli(_EnvCase):
    def test_schedules_cli_add_list_remove(self):
        code, out, _ = self._run_cli(
            [
                "schedules",
                "add",
                "--agent",
                "briefer",
                "--account",
                "Acme Corp",
                "--cadence",
                "daily",
                "--time",
                "07:00",
                "--timezone",
                "America/Los_Angeles",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("Scheduled:", out)
        code, out, _ = self._run_cli(["schedules", "list"])
        self.assertEqual(code, 0)
        self.assertIn("briefer", out)
        self.assertIn("Acme Corp", out)
        self.assertIn("daily 07:00 America/Los_Angeles", out)
        schedule_id = out.split()[0]
        code, out, _ = self._run_cli(["schedules", "remove", schedule_id])
        self.assertEqual(code, 0)
        self.assertIn("Removed", out)
        code, out, _ = self._run_cli(["schedules", "list"])
        self.assertIn("No schedules", out)

    def test_schedules_add_rejects_unknown_agent(self):
        code, _, err = self._run_cli(
            [
                "schedules",
                "add",
                "--agent",
                "nope",
                "--account",
                "Acme Corp",
                "--cadence",
                "daily",
                "--time",
                "07:00",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("unknown agent", err)

    def test_scheduler_once_runs_due_and_skips_not_due(self):
        self._write_schedules(
            [
                self._entry("briefer", "Acme Corp", created_days_ago=2),
                self._entry("briefer", "Globex Inc", created_days_ago=0),
            ]
        )
        code, out, err = self._run_cli(["scheduler", "--once"])
        self.assertEqual(code, 0)
        self.assertIn("Ran briefer for Acme Corp", out)
        self.assertIn("Skipped briefer for Globex Inc", out)

    def test_scheduled_run_drafts_only(self):
        """A scheduled run lands a pending draft in the queue and
        executes nothing: the v1.3.0 approval gate still applies."""
        from harness import ApprovalQueue

        self._write_schedules(
            [
                self._entry(
                    "salesforce-updater",
                    "Acme Corp",
                    created_days_ago=2,
                    changes=["stage 006STUB000000001 Closed Won"],
                )
            ]
        )
        code, out, _ = self._run_cli(["scheduler", "--once"])
        self.assertEqual(code, 0)
        self.assertIn("Ran salesforce-updater for Acme Corp", out)
        queue = ApprovalQueue(os.environ["HARNESS_APPROVALS_FILE"])
        pending = queue.list_pending()
        self.assertEqual(len(pending), 1)
        item = pending[0]
        self.assertEqual(item.agent, "salesforce-updater")
        # A write plan is drafted, but nothing executed: no result,
        # no timestamp, still pending.
        self.assertTrue(item.writes)
        self.assertEqual(item.execution, [])
        self.assertIsNone(item.executed_at)
        self.assertEqual(item.status, "pending")

    def test_scheduled_run_with_bad_change_fails_not_fatal(self):
        self._write_schedules(
            [
                self._entry(
                    "salesforce-updater",
                    "Acme Corp",
                    created_days_ago=2,
                    changes=["nonsense change spec"],
                ),
                self._entry("briefer", "Globex Inc", created_days_ago=2),
            ]
        )
        code, out, err = self._run_cli(["scheduler", "--once"])
        self.assertEqual(code, 1)
        self.assertIn("FAILED salesforce-updater for Acme Corp", err)
        self.assertIn("Ran briefer for Globex Inc", out)

    def test_help_lists_scheduler_commands(self):
        out = io.StringIO()
        with redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("schedules", out.getvalue())
        self.assertIn("scheduler", out.getvalue())

    def test_version_still_1_8_0(self):
        self.assertEqual(__version__, "1.8.0")


if __name__ == "__main__":
    unittest.main()
