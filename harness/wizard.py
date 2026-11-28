"""First-run Ollama setup from the dashboard (v0.18.1).

The GUI setup wizard: Ollama install, model pull, and server start
as background jobs the dashboard polls, so a non-technical user
never opens a terminal. The install commands are the CLI wizard's
own (`_ollama_install_commands` in setup.py): official distribution
only, shown before they run. A job runs in a thread and reports
plain-language progress lines; the dashboard polls its status. All
long operations are jobs: an install takes minutes, a model pull
takes minutes, and the state endpoint must stay fast.

One active job at a time: a second start while one runs returns the
running job instead of starting a second install on top of it.
"""

from __future__ import annotations

import itertools
import platform
import shutil
import subprocess
import threading
import time
import uuid
from types import SimpleNamespace

from .config import OLLAMA_BASE_URL, OLLAMA_DEFAULT_MODEL, Config
from .llm import _model_pulled, is_ollama_base_url, ollama_models, ollama_reachable
from .setup import (
    _ollama_install_commands,
    _start_ollama_server,
    _wait_for_ollama,
)

# A pull of the default model is 4.9 GB; an install is a package
# download plus setup. Either can take many minutes on a slow link,
# so a single command gets a generous bound instead of failing the
# job for being slow. The job thread is a daemon: it never blocks
# the dashboard from stopping.
_COMMAND_TIMEOUT = 1800

# After an install, the server needs time to come up: the installer
# starts it on Windows and macOS, the service starts on Linux.
# Thirty one-second probes before giving up and saying so plainly.
_START_WAIT_ATTEMPTS = 30


def _default_run(cmd: str):
    """Run one install/pull command through the shell. Shell is
    required: the commands include a pipe (the Linux install script)
    and Windows %TEMP% expansion. Output is captured, never printed:
    the job keeps the last few lines for the status view."""
    return subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=_COMMAND_TIMEOUT,
    )


class OllamaJob:
    """One background Ollama job: an install or a model pull.

    Status moves pending -> running -> done | failed. `lines` is the
    plain-language progress log the dashboard shows while polling.
    A job that fails names the command and its exit code, and keeps
    the command's last output lines, so the failure is diagnosable
    instead of a bare error."""

    def __init__(
        self,
        job_id: str,
        kind: str,
        commands: list[str],
        run_fn=None,
        on_success=None,
    ):
        self.id = job_id
        self.kind = kind
        self.commands = list(commands)
        self.status = "pending"
        self.lines: list[str] = []
        self.error: str | None = None
        self.returncode: int | None = None
        self._run_fn = run_fn or _default_run
        self._on_success = on_success
        self._thread: threading.Thread | None = None

    def start(self) -> "OllamaJob":
        thread = threading.Thread(target=self._run, daemon=True)
        self._thread = thread
        thread.start()
        return self

    def _run(self) -> None:
        self.status = "running"
        for cmd in self.commands:
            self.lines.append("Running: %s" % cmd)
            try:
                result = self._run_fn(cmd)
            except Exception as exc:  # noqa: BLE001 - reported, never a stack trace
                self.status = "failed"
                self.error = "Could not run %s: %s" % (cmd, exc)
                return
            self.returncode = getattr(result, "returncode", None)
            output = (
                getattr(result, "stdout", "") or ""
            ) + (
                getattr(result, "stderr", "") or ""
            )
            tail = [line for line in output.splitlines() if line.strip()]
            self.lines.extend(tail[-5:])
            if self.returncode != 0:
                self.status = "failed"
                self.error = "%s failed (exit %s)." % (cmd, self.returncode)
                return
            self.lines.append("Finished: %s" % cmd)
        if self._on_success is not None:
            # The success check (is Ollama up? is the model pulled?)
            # can fail the job after the commands ran: a clean
            # install that never answers is not a success.
            ok, message = self._on_success()
            self.lines.append(message)
            if not ok:
                self.status = "failed"
                self.error = message
                return
        self.status = "done"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "commands": self.commands,
            "lines": self.lines,
            "error": self.error,
            "returncode": self.returncode,
        }


class OllamaSetup:
    """Ollama install, pull, and serve for the dashboard wizard.

    All the checks are the CLI wizard's own: `ollama_reachable`
    for the server, `ollama_models` for the pulled model,
    `_ollama_install_commands` for the install. The dashboard only
    adds the async wrapper: the wizard cannot wait minutes on an
    HTTP request, so installs and pulls are jobs and the frontend
    polls. Fakes (run_fn, popen_fn, reachable_fn, models_fn,
    sleep_fn, system_fn, which_fn) are injectable for tests: no
    network, no installer, no real Ollama needed."""

    def __init__(
        self,
        config: Config,
        run_fn=None,
        popen_fn=None,
        reachable_fn=None,
        models_fn=None,
        sleep_fn=None,
        system_fn=None,
        which_fn=None,
    ):
        self.config = config
        self._run_fn = run_fn or _default_run
        self._popen_fn = popen_fn or subprocess.Popen
        self._reachable_fn = reachable_fn or ollama_reachable
        self._models_fn = models_fn or ollama_models
        self._sleep_fn = sleep_fn or time.sleep
        self._system_fn = system_fn or platform.system
        self._which_fn = which_fn or shutil.which
        self._jobs: dict[str, OllamaJob] = {}
        self._lock = threading.Lock()

    def _probe_url(self) -> str:
        base = self.config.llm_base_url.rstrip("/")
        return base if is_ollama_base_url(base) else OLLAMA_BASE_URL

    def _api_base(self, probe_url: str) -> str:
        return (
            probe_url[: -len("/v1")]
            if probe_url.endswith("/v1")
            else probe_url
        )

    def _install_commands(self) -> list[str] | None:
        return _ollama_install_commands(self._system_fn(), self._which_fn)

    def _active_job(self) -> OllamaJob | None:
        with self._lock:
            for job in self._jobs.values():
                if job.status in ("pending", "running"):
                    return job
        return None

    def _register(self, job: OllamaJob) -> OllamaJob:
        with self._lock:
            self._jobs[job.id] = job
        return job

    def status(self) -> dict:
        """What the wizard's first step renders: is the server up,
        is the CLI there, is the model pulled, and what install the
        wizard can offer. Nothing here installs or starts anything:
        it only reports, so the dashboard can poll it freely."""
        probe_url = self._probe_url()
        reachable = self._reachable_fn(probe_url)
        models = (
            self._models_fn(self._api_base(probe_url)) if reachable else None
        )
        commands = self._install_commands()
        return {
            "reachable": reachable,
            "cli_present": bool(self._which_fn("ollama")),
            # The model is pulled only when the server answered and
            # said so: unreachable means unknown, not pulled, and the
            # wizard must not show a green check it cannot verify.
            "model_pulled": bool(reachable)
            and _model_pulled(models, self.config.llm_model),
            "model": self.config.llm_model,
            "install_available": commands is not None,
            "install_commands": commands or [],
            "system": self._system_fn(),
        }

    def start_install(self) -> OllamaJob:
        """Start the Ollama install as a background job. One active
        job at a time: a second start returns the running job.
        Raises ValueError when Ollama is already reachable or there
        is no automated install path for this system: the wizard
        says so instead of starting a job that cannot succeed."""
        active = self._active_job()
        if active is not None:
            return active
        if self._reachable_fn(self._probe_url()):
            raise ValueError("Ollama is already reachable.")
        commands = self._install_commands()
        if commands is None:
            raise ValueError(
                "Ollama is not installed, and there is no automated "
                "install path for this system (%s). Get it at "
                "ollama.com/download." % self._system_fn()
            )
        job = OllamaJob(
            job_id=uuid.uuid4().hex[:12],
            kind="install",
            commands=commands,
            run_fn=self._run_fn,
            on_success=self._verify_after_install,
        )
        return self._register(job).start()

    def _verify_after_install(self) -> tuple[bool, str]:
        """After the install commands ran: is Ollama answering? The
        installer starts the server on Windows and macOS; on Linux
        the service starts. If it is not up yet, start `ollama
        serve` and wait again. A clean install that never answers
        is a failed job, said plainly, with the manual command."""
        probe_url = self._probe_url()
        if _wait_for_ollama(
            probe_url,
            attempts=_START_WAIT_ATTEMPTS,
            sleep_fn=self._sleep_fn,
            reachable_fn=self._reachable_fn,
        ):
            return True, "Ollama is up."
        if self._which_fn("ollama"):
            _start_ollama_server(self._popen_fn)
            if _wait_for_ollama(
                probe_url,
                attempts=_START_WAIT_ATTEMPTS,
                sleep_fn=self._sleep_fn,
                reachable_fn=self._reachable_fn,
            ):
                return True, "Ollama is up."
        return (
            False,
            "Ollama installed but did not start. "
            "Start it by hand with: ollama serve",
        )

    def start_pull(self, model: str | None = None) -> OllamaJob:
        """Pull the model as a background job: a 4.9 GB download for
        the default model, too long for an HTTP request. Raises
        ValueError when Ollama is not reachable: pulling needs the
        server up first."""
        active = self._active_job()
        if active is not None:
            return active
        model = (model or "").strip() or self.config.llm_model
        if not self._reachable_fn(self._probe_url()):
            raise ValueError(
                "Ollama is not reachable. Install or start it first."
            )
        job = OllamaJob(
            job_id=uuid.uuid4().hex[:12],
            kind="pull",
            commands=["ollama pull %s" % model],
            run_fn=self._run_fn,
            on_success=lambda: self._verify_model_pulled(model),
        )
        return self._register(job).start()

    def _verify_model_pulled(self, model: str) -> tuple[bool, str]:
        models = self._models_fn(self._api_base(self._probe_url()))
        if _model_pulled(models, model):
            return True, "%s pulled." % model
        return (
            False,
            "The pull did not complete; %s is still missing." % model,
        )

    def start_server(self) -> dict:
        """Start `ollama serve` detached and return at once: the
        wizard polls status until the server answers. Raises
        ValueError when the server is already up or the CLI is not
        installed: there is nothing to start."""
        if self._reachable_fn(self._probe_url()):
            raise ValueError("Ollama is already running.")
        if not self._which_fn("ollama"):
            raise ValueError(
                "Ollama is not installed. Install it first."
            )
        _start_ollama_server(self._popen_fn)
        return {"started": True}

    def get_job(self, job_id: str) -> OllamaJob | None:
        with self._lock:
            return self._jobs.get(job_id)


def default_model() -> str:
    """The model the wizard offers to pull after an install."""
    return OLLAMA_DEFAULT_MODEL
