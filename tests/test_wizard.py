"""Tests for the first-run Ollama wizard (v0.18.1).

The wizard installs Ollama, pulls the model, and starts the server
from the dashboard, so a non-technical user never opens a terminal.
Installs and pulls are background jobs the dashboard polls: they
take minutes, too long for an HTTP request. Every test here uses a
faked installer, a faked reachability probe, and a no-op sleep, so
no network, no installer, and no real Ollama are needed.
"""

import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from harness import Config
from harness.approvals import ApprovalQueue
from harness.config import OLLAMA_DEFAULT_MODEL
from harness.dashboard import build_state, create_app
from harness.wizard import OllamaJob, OllamaSetup


def _ok_run(cmd):
    """A fake installer: the command succeeds at once."""
    return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")


def _fail_run(cmd):
    """A fake installer: the command fails at once."""
    return SimpleNamespace(returncode=1, stdout="", stderr="boom\n")


def _recording_run(record):
    def run(cmd):
        record.append(cmd)
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    return run


def _config(tmp):
    config = Config.from_env()
    config.approvals_file = os.path.join(tmp, "approvals.json")
    config.memory_file = os.path.join(tmp, "memory.json")
    config.schedules_file = os.path.join(tmp, "schedules.json")
    config.escalations_file = os.path.join(tmp, "escalations.json")
    config.mcp_servers_file = os.path.join(tmp, "mcp-servers.json")
    config.env_path = os.path.join(tmp, ".env")
    return config


class OllamaSetupTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = _config(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _setup(self, **overrides):
        defaults = {
            "run_fn": _ok_run,
            "popen_fn": lambda *a, **k: SimpleNamespace(),
            "reachable_fn": lambda url: False,
            "models_fn": lambda base: [],
            "sleep_fn": lambda s: None,
            "system_fn": lambda: "Windows",
            # The simulated Windows machine has winget, no Ollama:
            # the wizard offers the winget install, as on Justin's
            # Windows first-run test.
            "which_fn": lambda name: "winget" if name == "winget" else None,
        }
        defaults.update(overrides)
        return OllamaSetup(self.config, **defaults)

    def test_status_reports_unreachable_and_install_commands(self):
        setup = self._setup(
            which_fn=lambda name: "winget" if name == "winget" else None
        )
        status = setup.status()
        self.assertFalse(status["reachable"])
        self.assertFalse(status["cli_present"])
        self.assertFalse(status["model_pulled"])
        self.assertEqual(status["model"], OLLAMA_DEFAULT_MODEL)
        self.assertTrue(status["install_available"])
        self.assertEqual(
            status["install_commands"], ["winget install Ollama.Ollama"]
        )
        self.assertEqual(status["system"], "Windows")

    def test_status_reports_reachable_and_model_pulled(self):
        setup = self._setup(
            reachable_fn=lambda url: True,
            models_fn=lambda base: [OLLAMA_DEFAULT_MODEL],
        )
        status = setup.status()
        self.assertTrue(status["reachable"])
        self.assertTrue(status["model_pulled"])

    def test_status_model_missing_when_not_pulled(self):
        setup = self._setup(
            reachable_fn=lambda url: True,
            models_fn=lambda base: ["qwen2.5:0.5b"],
        )
        self.assertFalse(setup.status()["model_pulled"])

    def test_status_darwin_without_brew_uses_zip(self):
        setup = self._setup(system_fn=lambda: "Darwin")
        commands = setup.status()["install_commands"]
        self.assertEqual(len(commands), 3)
        self.assertIn("Ollama-darwin.zip", commands[0])
        self.assertIn("/Applications", commands[1])

    def test_status_no_install_path_when_unknown_system(self):
        setup = self._setup(system_fn=lambda: "Plan9")
        status = setup.status()
        self.assertFalse(status["install_available"])
        self.assertEqual(status["install_commands"], [])

    def test_start_install_refuses_when_reachable(self):
        setup = self._setup(reachable_fn=lambda url: True)
        with self.assertRaises(ValueError):
            setup.start_install()

    def test_start_install_refuses_without_install_path(self):
        setup = self._setup(system_fn=lambda: "Plan9")
        with self.assertRaises(ValueError):
            setup.start_install()

    def test_start_pull_refuses_when_unreachable(self):
        setup = self._setup()
        with self.assertRaises(ValueError):
            setup.start_pull()

    def test_serve_refuses_when_reachable(self):
        setup = self._setup(reachable_fn=lambda url: True)
        with self.assertRaises(ValueError):
            setup.start_server()

    def test_serve_refuses_when_cli_missing(self):
        setup = self._setup()
        with self.assertRaises(ValueError):
            setup.start_server()

    def test_serve_starts_server(self):
        started = []
        setup = self._setup(
            which_fn=lambda name: "ollama" if name == "ollama" else None,
            popen_fn=lambda args, **k: started.append(args),
        )
        self.assertEqual(setup.start_server(), {"started": True})
        self.assertEqual(started, [["ollama", "serve"]])


class OllamaJobTestCase(unittest.TestCase):
    def _join(self, job, timeout=5.0):
        if job._thread is not None:
            job._thread.join(timeout=timeout)
        self.assertFalse(
            job._thread.is_alive() if job._thread else False,
            "job thread did not finish",
        )

    def test_job_runs_commands_in_order_and_completes(self):
        record = []
        job = OllamaJob(
            job_id="job-1",
            kind="install",
            commands=["winget install Ollama.Ollama"],
            run_fn=_recording_run(record),
            on_success=lambda: (True, "Ollama is up."),
        )
        job.start()
        self._join(job)
        self.assertEqual(job.status, "done")
        self.assertEqual(record, ["winget install Ollama.Ollama"])
        self.assertIn("Running: winget install Ollama.Ollama", job.lines)
        self.assertIn("Ollama is up.", job.lines)
        self.assertIsNone(job.error)

    def test_job_failure_names_command_and_exit_code(self):
        job = OllamaJob(
            job_id="job-2",
            kind="install",
            commands=["winget install Ollama.Ollama"],
            run_fn=_fail_run,
        )
        job.start()
        self._join(job)
        self.assertEqual(job.status, "failed")
        self.assertIn("winget install Ollama.Ollama", job.error)
        self.assertIn("exit 1", job.error)
        self.assertEqual(job.returncode, 1)

    def test_job_stops_after_failed_command(self):
        record = []
        job = OllamaJob(
            job_id="job-3",
            kind="install",
            commands=["first-command", "second-command"],
            run_fn=lambda cmd: (
                record.append(cmd),
                SimpleNamespace(returncode=1, stdout="", stderr=""),
            )[1],
        )
        job.start()
        self._join(job)
        self.assertEqual(job.status, "failed")
        self.assertEqual(record, ["first-command"])

    def test_job_failed_success_check_fails_the_job(self):
        # The commands ran clean, but Ollama never answers: a clean
        # install that never answers is not a success.
        job = OllamaJob(
            job_id="job-4",
            kind="install",
            commands=["winget install Ollama.Ollama"],
            run_fn=_ok_run,
            on_success=lambda: (
                False,
                "Ollama did not start. Start it by hand with: ollama serve",
            ),
        )
        job.start()
        self._join(job)
        self.assertEqual(job.status, "failed")
        self.assertIn("did not start", job.error)

    def test_job_dict_has_everything_the_dashboard_polls(self):
        job = OllamaJob(
            job_id="job-5",
            kind="pull",
            commands=["ollama pull llama3.1:8b"],
            run_fn=_ok_run,
        )
        job.start()
        self._join(job)
        data = job.to_dict()
        self.assertEqual(data["id"], "job-5")
        self.assertEqual(data["kind"], "pull")
        self.assertEqual(data["status"], "done")
        self.assertEqual(data["commands"], ["ollama pull llama3.1:8b"])
        self.assertIsInstance(data["lines"], list)
        self.assertIsNone(data["error"])


class OllamaSetupJobFlowTestCase(unittest.TestCase):
    """The full install/pull flows through OllamaSetup, with fakes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = _config(self.tmp.name)
        self.reachable = {"up": False}

    def tearDown(self):
        self.tmp.cleanup()

    def _setup(self, **overrides):
        defaults = {
            "run_fn": _ok_run,
            "popen_fn": lambda *a, **k: SimpleNamespace(),
            "reachable_fn": lambda url: self.reachable["up"],
            "models_fn": lambda base: [],
            "sleep_fn": lambda s: None,
            "system_fn": lambda: "Windows",
            "which_fn": lambda name: None,
        }
        defaults.update(overrides)
        return OllamaSetup(self.config, **defaults)

    def _wait_done(self, job, timeout=5.0):
        deadline = time.time() + timeout
        while job.status in ("pending", "running") and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(job.status, "done", job.to_dict())

    def test_install_completes_when_server_answers(self):
        # The installer starts the server: reachability flips on
        # when the fake install command runs.
        def run(cmd):
            self.reachable["up"] = True
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

        setup = self._setup(run_fn=run)
        job = setup.start_install()
        self._wait_done(job)
        self.assertIn("Ollama is up.", job.lines)

    def test_install_fails_when_server_never_answers(self):
        setup = self._setup()
        job = setup.start_install()
        deadline = time.time() + 5.0
        while job.status in ("pending", "running") and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(job.status, "failed")
        self.assertIn("did not start", job.error)

    def test_second_start_returns_running_job(self):
        started = threading.Event()
        released = threading.Event()

        def run(cmd):
            # The installer starts the server: reachability flips
            # on when the install command runs.
            self.reachable["up"] = True
            started.set()
            released.wait(timeout=5)
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

        setup = self._setup(run_fn=run)
        first = setup.start_install()
        self.assertTrue(started.wait(timeout=5))
        second = setup.start_install()
        self.assertIs(second, first)
        released.set()
        self._wait_done(first)

    def test_pull_completes_when_model_appears(self):
        self.reachable["up"] = True
        pulled = {"models": []}

        def run(cmd):
            pulled["models"] = [OLLAMA_DEFAULT_MODEL]
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

        setup = self._setup(
            run_fn=run, models_fn=lambda base: pulled["models"]
        )
        job = setup.start_pull()
        self._wait_done(job)
        self.assertIn("%s pulled." % OLLAMA_DEFAULT_MODEL, job.lines)

    def test_pull_fails_when_model_still_missing(self):
        self.reachable["up"] = True
        setup = self._setup()
        job = setup.start_pull()
        deadline = time.time() + 5.0
        while job.status in ("pending", "running") and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(job.status, "failed")
        self.assertIn("still missing", job.error)


class WizardEndpointsTestCase(unittest.TestCase):
    """The dashboard wizard endpoints, with a faked installer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = _config(self.tmp.name)
        self.queue = ApprovalQueue(self.config.approvals_file)
        self.reachable = {"up": False}
        self.models = {"names": []}
        self.commands_run = []

    def tearDown(self):
        self.tmp.cleanup()

    def _client(self, **overrides):
        def run(cmd):
            self.commands_run.append(cmd)
            self.reachable["up"] = True
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

        defaults = {
            "run_fn": run,
            "popen_fn": lambda *a, **k: SimpleNamespace(),
            "reachable_fn": lambda url: self.reachable["up"],
            "models_fn": lambda base: self.models["names"],
            "sleep_fn": lambda s: None,
            "system_fn": lambda: "Windows",
            # The simulated Windows machine has winget, no Ollama:
            # the wizard offers the winget install, as on Justin's
            # Windows first-run test.
            "which_fn": lambda name: "winget" if name == "winget" else None,
        }
        defaults.update(overrides)
        wizard = OllamaSetup(self.config, **defaults)
        app = create_app(self.config, queue=self.queue, wizard=wizard)
        return TestClient(app), wizard

    def _wait_job(self, client, job_id, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            res = client.get("/api/setup/ollama/jobs/" + job_id)
            self.assertEqual(res.status_code, 200)
            if res.json()["status"] not in ("pending", "running"):
                return res.json()
            time.sleep(0.02)
        self.fail("job %s did not finish" % job_id)

    def test_ollama_status_endpoint(self):
        client, _wizard = self._client()
        res = client.get("/api/setup/ollama")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["reachable"])
        self.assertFalse(body["cli_present"])
        self.assertFalse(body["model_pulled"])
        self.assertEqual(body["model"], OLLAMA_DEFAULT_MODEL)
        self.assertTrue(body["install_available"])
        self.assertEqual(
            body["install_commands"], ["winget install Ollama.Ollama"]
        )

    def test_install_endpoint_runs_job_and_completes(self):
        client, _wizard = self._client()
        res = client.post("/api/setup/ollama/install")
        self.assertEqual(res.status_code, 200)
        job = res.json()
        self.assertEqual(job["kind"], "install")
        self.assertIn("id", job)
        done = self._wait_job(client, job["id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(
            self.commands_run, ["winget install Ollama.Ollama"]
        )
        self.assertIn("Ollama is up.", done["lines"])

    def test_install_endpoint_returns_running_job_when_active(self):
        started = threading.Event()
        released = threading.Event()

        def run(cmd):
            # The installer starts the server: reachability flips
            # on when the install command runs.
            self.reachable["up"] = True
            started.set()
            released.wait(timeout=5)
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

        client, _wizard = self._client(run_fn=run)
        first = client.post("/api/setup/ollama/install").json()
        self.assertTrue(started.wait(timeout=5))
        second = client.post("/api/setup/ollama/install").json()
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["status"], "running")
        released.set()
        done = self._wait_job(client, first["id"])
        self.assertEqual(done["status"], "done")

    def test_install_endpoint_rejects_when_reachable(self):
        self.reachable["up"] = True
        client, _wizard = self._client()
        res = client.post("/api/setup/ollama/install")
        self.assertEqual(res.status_code, 400)
        self.assertIn("already reachable", res.json()["detail"])

    def test_install_endpoint_rejects_without_install_path(self):
        client, _wizard = self._client(system_fn=lambda: "Plan9")
        res = client.post("/api/setup/ollama/install")
        self.assertEqual(res.status_code, 400)
        self.assertIn("no automated", res.json()["detail"])

    def test_pull_endpoint_needs_ollama(self):
        client, _wizard = self._client()
        res = client.post("/api/setup/ollama/pull", json={})
        self.assertEqual(res.status_code, 400)
        self.assertIn("not reachable", res.json()["detail"])

    def test_pull_endpoint_completes_when_model_appears(self):
        self.reachable["up"] = True

        def run(cmd):
            self.commands_run.append(cmd)
            self.models["names"] = [OLLAMA_DEFAULT_MODEL]
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

        client, _wizard = self._client(run_fn=run)
        res = client.post("/api/setup/ollama/pull", json={})
        self.assertEqual(res.status_code, 200)
        job = res.json()
        self.assertEqual(job["kind"], "pull")
        done = self._wait_job(client, job["id"])
        self.assertEqual(done["status"], "done")
        self.assertIn(
            "%s pulled." % OLLAMA_DEFAULT_MODEL, done["lines"]
        )

    def test_serve_endpoint_starts_server(self):
        started = []
        client, _wizard = self._client(
            which_fn=lambda name: "ollama" if name == "ollama" else None,
            popen_fn=lambda args, **k: started.append(args),
        )
        res = client.post("/api/setup/ollama/serve")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"started": True})
        self.assertEqual(started, [["ollama", "serve"]])

    def test_serve_endpoint_rejects_without_cli(self):
        client, _wizard = self._client()
        res = client.post("/api/setup/ollama/serve")
        self.assertEqual(res.status_code, 400)
        self.assertIn("not installed", res.json()["detail"])

    def test_unknown_job_is_404(self):
        client, _wizard = self._client()
        res = client.get("/api/setup/ollama/jobs/no-such-job")
        self.assertEqual(res.status_code, 404)

    def test_state_includes_setup_command(self):
        # The first-run banner and the wizard show the command the
        # user actually has, never a hardcoded one for the wrong
        # mode: from source in tests, `belay setup` packaged.
        client, _wizard = self._client()
        res = client.get("/api/state")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res.json()["setup_command"], "python -m harness.run setup"
        )

    def test_state_setup_command_is_packaged_when_frozen(self):
        client, _wizard = self._client()
        with mock.patch(
            "harness.dashboard.cli_invocation", return_value="belay.exe"
        ):
            res = client.get("/api/state")
        self.assertEqual(res.json()["setup_command"], "belay.exe setup")


if __name__ == "__main__":
    unittest.main()
