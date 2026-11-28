"""Tests for the CLI entry point (v1.0.0): version, help, and commands."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from harness import __version__
from harness.run import main


class TestCli(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_version_flag_prints_package_version(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as caught:
                main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn(__version__, buf.getvalue())

    def test_help_lists_all_commands(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as caught:
                main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        for command in ("run", "agents", "tools", "approvals", "dashboard", "memory", "launch"):
            self.assertIn(command, buf.getvalue())

    def test_bare_run_launches_the_dashboard(self):
        # v1.16.0, decided: a bare `belay` with no subcommand launches
        # the dashboard instead of printing a usage error. The GUI is
        # the default; the CLI is the power-user path.
        from unittest import mock

        with mock.patch("harness.run.cmd_launch", return_value=0) as launch:
            code = main([])
        self.assertEqual(code, 0)
        launch.assert_called_once()
        self.assertEqual(launch.call_args[0][0].port, 8000)

    def test_launch_starts_server_and_blocks_on_it(self):
        from unittest import mock

        thread = mock.Mock()
        with mock.patch(
            "harness.launcher.launch_dashboard",
            return_value={
                "url": "http://127.0.0.1:8000",
                "up": True,
                "thread": thread,
            },
        ) as launch:
            code, out = self._run(["launch"])
        self.assertEqual(code, 0)
        launch.assert_called_once()
        self.assertEqual(launch.call_args.kwargs["port"], 8000)
        thread.join.assert_called_once()
        self.assertIn("Belay dashboard", out)

    def test_agents_lists_nineteen_implemented(self):
        code, out = self._run(["agents"])
        self.assertEqual(code, 0)
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 19)
        self.assertTrue(all("implemented" in line for line in lines))

    def test_tools_lists_all_connector_tools_in_stub(self):
        code, out = self._run(["tools", "--stub"])
        self.assertEqual(code, 0)
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 32)
        for prefix in (
            "salesforce_",
            "slack_",
            "gong_",
            "hubspot_",
            "gmail_",
            "zoom_",
            "zendesk_",
            "linear_",
            "gcal_",
            "csv_",
        ):
            self.assertTrue(
                any(line.startswith(prefix) for line in lines),
                "no tool from %s" % prefix,
            )

    def test_unknown_agent_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HARNESS_APPROVALS_FILE"] = os.path.join(
                tmp, "approvals.json"
            )
            try:
                code, _ = self._run(
                    [
                        "run",
                        "--agent",
                        "nope",
                        "--account",
                        "Acme Corp",
                        "--stub",
                    ]
                )
            finally:
                del os.environ["HARNESS_APPROVALS_FILE"]
        self.assertEqual(code, 1)

    def test_run_help_lists_value_finder_flags(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as caught:
                main(["run", "--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--hourly-rate", buf.getvalue())
        self.assertIn("--hours-saved", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
