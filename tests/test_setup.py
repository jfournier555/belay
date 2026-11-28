"""Tests for the first-run wizard (v1.12.0): demo data, setup wizard,
LLM provider choice, and the Ollama install offer.

Everything runs against temp store files and a temp .env, so the real
approvals.json and .env are never touched. Ollama reachability is
mocked: whether Ollama happens to be running on the test machine must
never change a test result. The install offer is tested with mocked
commands: no real install ever runs in tests.
"""

import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from harness import ApprovalQueue, Config
from harness.config import OLLAMA_BASE_URL, OLLAMA_DEFAULT_MODEL
from harness.dashboard import build_state, create_app
from harness.setup import (
    PROVIDERS,
    _demo_config,
    _ollama_install_commands,
    _ollama_install_offer,
    _ollama_readiness,
    _wizard_llm_step,
    clear_demo,
    credentials_present,
    detect_provider,
    is_first_run,
    llm_available,
    print_setup_hint,
    provider_display_name,
    read_env_file,
    run_setup_wizard,
    seed_demo,
    verify_llm,
    write_env_values,
)


def _temp_config(tmpdir):
    """A Config whose file-backed stores live in a temp dir."""
    return Config(
        approvals_file=os.path.join(tmpdir, "approvals.json"),
        memory_file=os.path.join(tmpdir, "memory.json"),
        schedules_file=os.path.join(tmpdir, "schedules.json"),
        escalations_file=os.path.join(tmpdir, "escalations.json"),
        mcp_servers_file=os.path.join(tmpdir, "mcp-servers.json"),
    )


class _IO:
    """Fake input/getpass/out. One shared answer list, consumed in
    call order (the wizard calls input and getpass in a fixed order).
    Prompts are captured in out_lines; answers are not, so a secret
    only shows up in the output if the code prints it."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.out_lines: list[str] = []

    def input(self, prompt=""):
        self.out_lines.append(prompt)
        if not self.answers:
            raise AssertionError("ran out of fake inputs at %r" % prompt)
        return self.answers.pop(0)

    def getpass(self, prompt=""):
        self.out_lines.append(prompt)
        if not self.answers:
            raise AssertionError("ran out of fake getpass at %r" % prompt)
        return self.answers.pop(0)

    def out(self, text):
        self.out_lines.append(text)

    def text(self):
        return "\n".join(self.out_lines)


class _FakeResult:
    def __init__(self, returncode=0):
        self.returncode = returncode


class _FakeRun:
    """Fake subprocess.run: records commands, never executes one."""

    def __init__(self, returncode=0):
        self.commands: list = []
        self.kwargs: list = []
        self.returncode = returncode

    def __call__(self, cmd, **kwargs):
        self.commands.append(cmd)
        self.kwargs.append(kwargs)
        return _FakeResult(self.returncode)


class _FakePopen:
    """Fake subprocess.Popen: records detached starts."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return mock.Mock()


class _FakeLLM:
    """A stand-in for a real Ollama response: fixed text, no network."""

    def complete(self, prompt, system=None, model=None):
        return "Real draft from Ollama for this account."


def _reachability_after(n):
    """A side-effect function for ollama_reachable: False for the
    first n calls, True after. Models a server that comes up."""
    state = {"calls": 0}

    def _check(_url=None):
        state["calls"] += 1
        return state["calls"] > n

    return _check


class TestEnvWrites(unittest.TestCase):
    def test_preserves_comments_and_existing_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, ".env")
            with open(path, "w", encoding="utf-8") as f:
                f.write("# keep this comment\nFOO=bar\nBAZ=qux\n")
            written = write_env_values(
                path, {"FOO": "new", "ADDED": "yes"}
            )
            self.assertEqual(sorted(written), ["ADDED", "FOO"])
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("# keep this comment", text)
            self.assertIn("FOO=new", text)
            self.assertIn("BAZ=qux", text)
            self.assertIn("ADDED=yes", text)

    def test_quotes_values_with_spaces(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, ".env")
            write_env_values(path, {"KEY": "a b c"})
            _lines, values = read_env_file(path)
            self.assertEqual(values["KEY"], "a b c")

    def test_creates_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, ".env")
            write_env_values(path, {"KEY": "val"})
            self.assertTrue(os.path.exists(path))


class TestFirstRunAndHint(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.config = _temp_config(self._tmpdir.name)
        self._llm_reachable = mock.patch(
            "harness.llm.ollama_reachable", return_value=False
        )
        self._llm_reachable.start()
        self.addCleanup(self._llm_reachable.stop)
        self._setup_reachable = mock.patch(
            "harness.setup.ollama_reachable", return_value=False
        )
        self.setup_reachable = self._setup_reachable.start()
        self.addCleanup(self._setup_reachable.stop)

    def test_first_run_when_empty(self):
        self.assertTrue(is_first_run(self.config))

    def test_not_first_run_when_credential_set(self):
        self.config.slack_bot_token = "xoxb-test"
        self.assertTrue(credentials_present(self.config))
        self.assertFalse(is_first_run(self.config))

    def test_not_first_run_when_store_has_records(self):
        ApprovalQueue(self.config.approvals_file).submit(
            agent="briefer",
            job={"account": "Acme Corp"},
            draft="hi",
            sources=[],
        )
        self.assertFalse(is_first_run(self.config))

    def test_hint_prints_when_nothing_configured(self):
        io = _IO([])
        self.assertTrue(
            print_setup_hint(self.config, out=io.out, interactive=True)
        )
        self.assertIn("setup", io.text())
        self.assertIn("Ollama", io.text())

    def test_hint_silent_with_api_key(self):
        self.config.llm_api_key = "sk-test"
        io = _IO([])
        self.assertFalse(
            print_setup_hint(self.config, out=io.out, interactive=True)
        )
        self.assertEqual(io.text(), "")

    def test_hint_silent_when_ollama_reachable(self):
        self.setup_reachable.return_value = True
        io = _IO([])
        self.assertFalse(
            print_setup_hint(self.config, out=io.out, interactive=True)
        )

    def test_hint_silent_when_not_interactive(self):
        io = _IO([])
        self.assertFalse(
            print_setup_hint(self.config, out=io.out, interactive=False)
        )

    def test_llm_available(self):
        self.assertFalse(llm_available(self.config))
        self.config.llm_api_key = "sk-test"
        self.assertTrue(llm_available(self.config))
        self.config.llm_api_key = None
        self.setup_reachable.return_value = True
        self.assertTrue(llm_available(self.config))


class TestDemoSeed(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.config = _temp_config(self._tmpdir.name)
        self._llm_reachable = mock.patch(
            "harness.llm.ollama_reachable", return_value=False
        )
        self._llm_reachable.start()
        self.addCleanup(self._llm_reachable.stop)
        self._setup_reachable = mock.patch(
            "harness.setup.ollama_reachable", return_value=False
        )
        self.setup_reachable = self._setup_reachable.start()
        self.addCleanup(self._setup_reachable.stop)
        self._setup_models = mock.patch(
            "harness.setup.ollama_models", return_value=["llama3.1:8b"]
        )
        self._setup_models.start()
        self.addCleanup(self._setup_models.stop)
        self._cli = mock.patch(
            "harness.setup.ollama_cli_present", return_value=False
        )
        self.cli_present = self._cli.start()
        self.addCleanup(self._cli.stop)

    def _seed(self, **kwargs):
        io = _IO(kwargs.pop("answers", []))
        kwargs.setdefault("confirm", True)
        report = seed_demo(self.config, input_fn=io.input, out=io.out, **kwargs)
        return report, io

    def test_seed_stub_labels_drafts(self):
        report, io = self._seed()
        self.assertEqual(report["status"], "seeded")
        self.assertFalse(report["real_drafts"])
        self.assertEqual(report["counts"]["approvals"], 4)
        self.assertEqual(report["counts"]["schedules"], 1)
        self.assertGreaterEqual(report["counts"]["memory"], 1)
        self.assertGreaterEqual(report["counts"]["escalations"], 1)
        queue = ApprovalQueue(self.config.approvals_file)
        items = queue.list_all()
        self.assertEqual(len(items), 4)
        statuses = sorted(item.status for item in items)
        self.assertEqual(
            statuses, ["approved", "edited", "pending", "pending"]
        )
        for item in items:
            self.assertIn("STUB DRAFT", item.draft)
        self.assertIn("stub-labeled", io.text())
        marker_path = os.path.join(self._tmpdir.name, "demo-seed.json")
        self.assertTrue(os.path.exists(marker_path))

    def test_seed_with_ollama_uses_real_drafts(self):
        self.setup_reachable.return_value = True
        with mock.patch(
            "harness.run.get_llm", return_value=_FakeLLM()
        ):
            report, io = self._seed()
        self.assertEqual(report["status"], "seeded")
        self.assertTrue(report["real_drafts"])
        queue = ApprovalQueue(self.config.approvals_file)
        for item in queue.list_all():
            self.assertNotIn("STUB DRAFT", item.draft)
            self.assertIn("Real draft from Ollama", item.draft)
        self.assertIn("real responses from Ollama", io.text())

    def test_seed_ollama_up_model_missing_seeds_stub(self):
        # The server answers but the model is not pulled: the demo
        # must still seed, stub-labeled, instead of crashing on the
        # 404 the chat call would return. (v1.13.1 regression: the
        # seed announced stub drafts, then ran the agents through
        # the real Ollama client anyway.)
        self.setup_reachable.return_value = True
        with (
            mock.patch(
                "harness.setup.ollama_models", return_value=["qwen2.5:0.5b"]
            ),
            mock.patch("harness.llm.ollama_reachable", return_value=True),
            mock.patch(
                "harness.llm.ollama_models", return_value=["qwen2.5:0.5b"]
            ),
        ):
            report, io = self._seed()
        self.assertEqual(report["status"], "seeded")
        self.assertFalse(report["real_drafts"])
        queue = ApprovalQueue(self.config.approvals_file)
        items = queue.list_all()
        self.assertEqual(len(items), 4)
        for item in items:
            self.assertIn("STUB DRAFT", item.draft)
        self.assertIn("stub-labeled", io.text())

    def test_refuse_over_real_data_default_no(self):
        ApprovalQueue(self.config.approvals_file).submit(
            agent="briefer",
            job={"account": "Real Corp"},
            draft="real draft",
            sources=[],
        )
        io = _IO([""])
        report = seed_demo(
            self.config, confirm=None, input_fn=io.input, out=io.out
        )
        self.assertEqual(report["status"], "refused")
        self.assertEqual(
            len(ApprovalQueue(self.config.approvals_file).list_all()), 1
        )
        self.assertIn("Re-run with --yes", io.text())

    def test_refuse_over_real_data_confirm_yes(self):
        ApprovalQueue(self.config.approvals_file).submit(
            agent="briefer",
            job={"account": "Real Corp"},
            draft="real draft",
            sources=[],
        )
        report, _io = self._seed()
        self.assertEqual(report["status"], "seeded")
        self.assertEqual(
            len(ApprovalQueue(self.config.approvals_file).list_all()), 5
        )

    def test_already_seeded(self):
        self._seed()
        report, _io = self._seed()
        self.assertEqual(report["status"], "already-seeded")

    def test_clear_demo_removes_only_demo(self):
        self._seed()
        queue = ApprovalQueue(self.config.approvals_file)
        queue.submit(
            agent="briefer",
            job={"account": "Real Corp"},
            draft="real draft",
            sources=[],
        )
        io = _IO([])
        report = clear_demo(self.config, out=io.out)
        self.assertEqual(report["status"], "cleared")
        self.assertEqual(report["counts"]["approvals"], 4)
        remaining = ApprovalQueue(self.config.approvals_file).list_all()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].job.get("account"), "Real Corp")
        self.assertIn("Real records untouched", io.text())
        report = clear_demo(self.config, out=io.out)
        self.assertEqual(report["status"], "nothing-to-clear")

    def test_demo_config_clears_connectors_keeps_llm(self):
        config = _temp_config(self._tmpdir.name)
        config.salesforce_access_token = "tok"
        config.slack_bot_token = "xoxb"
        config.llm_api_key = "sk-test"
        config.stub_mode = True
        demo = _demo_config(config)
        self.assertIsNone(demo.salesforce_access_token)
        self.assertIsNone(demo.slack_bot_token)
        self.assertEqual(demo.llm_api_key, "sk-test")
        # An explicit stub mode is left alone: deliberate stub stays stub.
        self.assertTrue(demo.stub_mode)


class TestWizardLlm(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.config = _temp_config(self._tmpdir.name)
        self._setup_reachable = mock.patch(
            "harness.setup.ollama_reachable", return_value=True
        )
        self.setup_reachable = self._setup_reachable.start()
        self.addCleanup(self._setup_reachable.stop)
        self._setup_models = mock.patch(
            "harness.setup.ollama_models", return_value=["llama3.1:8b"]
        )
        self.setup_models = self._setup_models.start()
        self.addCleanup(self._setup_models.stop)
        self._cli = mock.patch(
            "harness.setup.ollama_cli_present", return_value=False
        )
        self.cli_present = self._cli.start()
        self.addCleanup(self._cli.stop)

    def _step(self, answers, **kwargs):
        io = _IO(answers)
        kwargs.setdefault("run_fn", _FakeRun())
        kwargs.setdefault("sleep_fn", lambda _s: None)
        updates = _wizard_llm_step(
            self.config, io.input, io.out, io.getpass, **kwargs
        )
        return updates, io

    def test_default_enter_picks_ollama(self):
        updates, io = self._step([""])
        self.assertEqual(
            updates,
            {
                "HARNESS_LLM_BASE_URL": OLLAMA_BASE_URL,
                "HARNESS_LLM_MODEL": OLLAMA_DEFAULT_MODEL,
            },
        )
        self.assertIn("llama3.1:8b is pulled", io.text())

    def test_model_pull_does_not_consume_wizard_input(self):
        # The v1.15.0 first-run bug: after the Ollama pull finished,
        # the wizard looked hung, with typed input echoing raw. The
        # pull subprocess inherited the wizard's stdin and ate
        # keystrokes typed during the pull. It runs with stdin
        # DEVNULL now, so the next prompt reads the wizard's input.
        with mock.patch(
            "harness.setup.ollama_models",
            side_effect=[[], ["llama3.1:8b"]],
        ), mock.patch(
            "harness.setup.ollama_cli_present", return_value=True
        ):
            run_fn = _FakeRun()
            updates, io = self._step(["1", "y"], run_fn=run_fn)
        self.assertEqual(
            run_fn.commands, [["ollama", "pull", "llama3.1:8b"]]
        )
        self.assertEqual(run_fn.kwargs[0].get("stdin"), subprocess.DEVNULL)
        self.assertIn("llama3.1:8b pulled.", io.text())
        self.assertEqual(
            updates["HARNESS_LLM_BASE_URL"], OLLAMA_BASE_URL
        )

    def test_pick_hosted_provider_stores_key_and_model(self):
        updates, io = self._step(["2", "sk-test-key-123", ""])
        self.assertEqual(
            updates,
            {
                "HARNESS_LLM_BASE_URL": "https://api.openai.com/v1",
                "HARNESS_LLM_API_KEY": "sk-test-key-123",
                "HARNESS_LLM_MODEL": "gpt-4o-mini",
            },
        )
        self.assertNotIn("sk-test-key-123", io.text())

    def test_pick_custom_endpoint(self):
        updates, io = self._step(
            ["8", "https://llm.example.invalid/v1", "custom-key-456", "my-model"]
        )
        self.assertEqual(
            updates,
            {
                "HARNESS_LLM_BASE_URL": "https://llm.example.invalid/v1",
                "HARNESS_LLM_API_KEY": "custom-key-456",
                "HARNESS_LLM_MODEL": "my-model",
            },
        )
        self.assertNotIn("custom-key-456", io.text())

    def test_ollama_unreachable_install_declined_then_pick_hosted(self):
        self.setup_reachable.return_value = False
        run_fn = _FakeRun()
        updates, io = self._step(
            ["1", "n", "n", "2", "sk-test-key-123", ""],
            run_fn=run_fn,
            which_fn=lambda _name: None,
            system_fn=lambda: "Linux",
        )
        self.assertEqual(
            updates["HARNESS_LLM_BASE_URL"], "https://api.openai.com/v1"
        )
        self.assertEqual(run_fn.commands, [])
        self.assertIn(
            "About to run: curl -fsSL https://ollama.com/install.sh | sh",
            io.text(),
        )
        self.assertIn("Skipping the Ollama install", io.text())
        self.assertNotIn("sk-test-key-123", io.text())

    def test_ollama_unreachable_install_failure(self):
        self.setup_reachable.side_effect = _reachability_after(99)
        run_fn = _FakeRun(returncode=1)
        updates, io = self._step(
            ["1", "y", "n", "2", "sk-test-key-123", ""],
            run_fn=run_fn,
            which_fn=lambda _name: None,
            system_fn=lambda: "Linux",
        )
        self.assertEqual(
            updates["HARNESS_LLM_BASE_URL"], "https://api.openai.com/v1"
        )
        self.assertEqual(
            run_fn.commands,
            ["curl -fsSL https://ollama.com/install.sh | sh"],
        )
        self.assertIn("The install failed at:", io.text())

    def test_ollama_unreachable_install_success(self):
        self.setup_reachable.side_effect = _reachability_after(2)
        run_fn = _FakeRun()
        updates, io = self._step(
            ["1", "y"],
            run_fn=run_fn,
            which_fn=lambda _name: None,
            system_fn=lambda: "Linux",
        )
        self.assertEqual(
            updates,
            {
                "HARNESS_LLM_BASE_URL": OLLAMA_BASE_URL,
                "HARNESS_LLM_MODEL": OLLAMA_DEFAULT_MODEL,
            },
        )
        self.assertEqual(
            run_fn.commands,
            ["curl -fsSL https://ollama.com/install.sh | sh"],
        )
        self.assertIn("Ollama is up.", io.text())

    def test_start_server_offer(self):
        self.setup_reachable.side_effect = _reachability_after(2)
        popen_fn = _FakePopen()
        updates, io = self._step(
            ["1", ""],
            popen_fn=popen_fn,
            which_fn=lambda name: "/usr/local/bin/ollama"
            if name == "ollama"
            else None,
        )
        self.assertEqual(
            updates["HARNESS_LLM_BASE_URL"], OLLAMA_BASE_URL
        )
        self.assertEqual(len(popen_fn.calls), 1)
        self.assertEqual(popen_fn.calls[0][0], ["ollama", "serve"])
        self.assertIn("About to run: ollama serve", io.text())

    def test_start_server_declined(self):
        self.setup_reachable.return_value = False
        popen_fn = _FakePopen()
        updates, io = self._step(
            ["1", "n", "n", "2", "sk-test-key-123", ""],
            popen_fn=popen_fn,
            which_fn=lambda name: "/usr/local/bin/ollama"
            if name == "ollama"
            else None,
        )
        self.assertEqual(
            updates["HARNESS_LLM_BASE_URL"], "https://api.openai.com/v1"
        )
        self.assertEqual(popen_fn.calls, [])

    def test_pull_offer_when_model_missing(self):
        self.setup_models.side_effect = [[], ["llama3.1:8b"]]
        self.cli_present.return_value = True
        run_fn = _FakeRun()
        updates, io = self._step(["1", "y"], run_fn=run_fn)
        self.assertEqual(
            updates["HARNESS_LLM_MODEL"], OLLAMA_DEFAULT_MODEL
        )
        self.assertEqual(
            run_fn.commands, [["ollama", "pull", "llama3.1:8b"]]
        )
        self.assertIn("llama3.1:8b pulled.", io.text())

    def test_detect_provider(self):
        self.assertEqual(detect_provider(self.config), "ollama")
        self.config.llm_base_url = "https://api.openai.com/v1"
        self.assertEqual(detect_provider(self.config), "openai")
        self.config.llm_base_url = "https://openrouter.ai/api/v1"
        self.assertEqual(detect_provider(self.config), "openrouter")
        self.config.llm_base_url = "https://llm.example.invalid/v1"
        self.assertEqual(detect_provider(self.config), "custom")
        self.config.llm_base_url = "http://192.168.1.5:11434/v1"
        self.assertEqual(detect_provider(self.config), "ollama")

    def test_provider_display_name(self):
        # Header-short: the Ollama label's parenthetical and the
        # Custom label's " endpoint" both drop; other labels pass
        # through untouched, unknown keys read capitalized.
        self.assertEqual(provider_display_name("ollama"), "Ollama")
        self.assertEqual(provider_display_name("openai"), "OpenAI")
        self.assertEqual(provider_display_name("openrouter"), "OpenRouter")
        self.assertEqual(provider_display_name("groq"), "Groq")
        self.assertEqual(provider_display_name("custom"), "Custom")
        self.assertEqual(provider_display_name("something-new"), "Something-new")

    def test_verify_llm(self):
        self.assertTrue(verify_llm(self.config))
        self.setup_reachable.return_value = False
        self.assertFalse(verify_llm(self.config))
        self.config.llm_base_url = "https://api.openai.com/v1"
        self.assertFalse(verify_llm(self.config))
        self.config.llm_api_key = "sk-test"
        with mock.patch(
            "harness.setup._http_get",
            return_value=mock.Mock(status_code=200),
        ):
            self.assertTrue(verify_llm(self.config))


class TestOllamaInstallCommands(unittest.TestCase):
    def test_linux(self):
        self.assertEqual(
            _ollama_install_commands("Linux", lambda _n: None),
            ["curl -fsSL https://ollama.com/install.sh | sh"],
        )

    def test_macos_with_brew(self):
        self.assertEqual(
            _ollama_install_commands(
                "Darwin", lambda name: "/opt/homebrew/bin/brew"
            ),
            ["brew install --cask ollama"],
        )

    def test_macos_without_brew(self):
        commands = _ollama_install_commands("Darwin", lambda _n: None)
        self.assertEqual(len(commands), 3)
        self.assertIn("Ollama-darwin.zip", commands[0])
        self.assertIn("ollama.com/download", commands[0])
        self.assertIn("/Applications", commands[1])
        self.assertEqual(commands[2], "open /Applications/Ollama.app")

    def test_windows_with_winget(self):
        self.assertEqual(
            _ollama_install_commands(
                "Windows", lambda name: "C:\\winget.exe"
            ),
            ["winget install Ollama.Ollama"],
        )

    def test_windows_without_winget(self):
        commands = _ollama_install_commands("Windows", lambda _n: None)
        self.assertEqual(len(commands), 2)
        self.assertIn("OllamaSetup.exe", commands[0])
        self.assertIn("ollama.com/download", commands[0])
        self.assertIn("OllamaSetup.exe", commands[1])

    def test_unknown_system_has_no_automated_path(self):
        self.assertIsNone(
            _ollama_install_commands("FreeBSD", lambda _n: None)
        )


class TestOllamaInstallOffer(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.config = _temp_config(self._tmpdir.name)
        self._setup_reachable = mock.patch(
            "harness.setup.ollama_reachable", return_value=False
        )
        self.setup_reachable = self._setup_reachable.start()
        self.addCleanup(self._setup_reachable.stop)

    def _offer(self, answers, **kwargs):
        io = _IO(answers)
        kwargs.setdefault("run_fn", _FakeRun())
        kwargs.setdefault("popen_fn", _FakePopen())
        kwargs.setdefault("sleep_fn", lambda _s: None)
        kwargs.setdefault("which_fn", lambda _name: None)
        kwargs.setdefault("system_fn", lambda: "Linux")
        result = _ollama_install_offer(
            self.config, io.input, io.out, **kwargs
        )
        return result, io

    def test_already_reachable_returns_true_without_offer(self):
        self.setup_reachable.return_value = True
        run_fn = _FakeRun()
        result, io = self._offer([], run_fn=run_fn)
        self.assertTrue(result)
        self.assertEqual(run_fn.commands, [])
        self.assertEqual(io.text(), "")

    def test_decline_never_runs_install(self):
        run_fn = _FakeRun()
        result, io = self._offer(["n"], run_fn=run_fn)
        self.assertFalse(result)
        self.assertEqual(run_fn.commands, [])
        self.assertIn("Skipping the Ollama install", io.text())

    def test_install_failure_is_plain_and_nonblocking(self):
        run_fn = _FakeRun(returncode=1)
        result, io = self._offer(["y"], run_fn=run_fn)
        self.assertFalse(result)
        self.assertIn("The install failed at:", io.text())
        self.assertIn("ollama.com/download", io.text())

    def test_install_success_reaches_ollama(self):
        self.setup_reachable.side_effect = _reachability_after(1)
        run_fn = _FakeRun()
        result, io = self._offer(["y"], run_fn=run_fn)
        self.assertTrue(result)
        self.assertEqual(
            run_fn.commands,
            ["curl -fsSL https://ollama.com/install.sh | sh"],
        )
        self.assertIn("Ollama is up.", io.text())

    def test_install_success_then_start_server(self):
        self.setup_reachable.side_effect = _reachability_after(6)
        run_fn = _FakeRun()
        popen_fn = _FakePopen()
        result, io = self._offer(
            ["y"], run_fn=run_fn, popen_fn=popen_fn
        )
        self.assertTrue(result)
        self.assertEqual(len(popen_fn.calls), 1)
        self.assertEqual(popen_fn.calls[0][0], ["ollama", "serve"])

    def test_unknown_system_says_so_plainly(self):
        result, io = self._offer([], system_fn=lambda: "FreeBSD")
        self.assertFalse(result)
        self.assertIn("no automated install path", io.text())


if __name__ == "__main__":
    unittest.main()

class TestWizard(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.config = _temp_config(self._tmpdir.name)
        self.env_path = os.path.join(self._tmpdir.name, ".env")
        # run_setup_wizard loads .env into the process environment:
        # snapshot it and restore it, so no test leaks env vars.
        self._saved_env = dict(os.environ)

        def _restore_env():
            os.environ.clear()
            os.environ.update(self._saved_env)

        self.addCleanup(_restore_env)
        self._llm_reachable = mock.patch(
            "harness.llm.ollama_reachable", return_value=False
        )
        self._llm_reachable.start()
        self.addCleanup(self._llm_reachable.stop)
        self._setup_reachable = mock.patch(
            "harness.setup.ollama_reachable", return_value=True
        )
        self.setup_reachable = self._setup_reachable.start()
        self.addCleanup(self._setup_reachable.stop)
        self._setup_models = mock.patch(
            "harness.setup.ollama_models", return_value=["llama3.1:8b"]
        )
        self._setup_models.start()
        self.addCleanup(self._setup_models.stop)
        # Connector verifications never touch the network in tests.
        self._http_get = mock.patch(
            "harness.setup._http_get",
            side_effect=Exception("no network in tests"),
        )
        self._http_get.start()
        self.addCleanup(self._http_get.stop)
        self._http_post = mock.patch(
            "harness.setup._http_post",
            side_effect=Exception("no network in tests"),
        )
        self._http_post.start()
        self.addCleanup(self._http_post.stop)

    def _blank_connector_answers(self):
        # One blank answer per connector field (20 fields, nine connectors).
        return [""] * 20

    def test_wizard_writes_ollama_to_env(self):
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("# keep this comment\nSOME_OTHER_KEY=keepme\n")
        io = _IO([""] + self._blank_connector_answers())
        report = run_setup_wizard(
            self.config,
            input_fn=io.input,
            out=io.out,
            getpass_fn=io.getpass,
            env_path=self.env_path,
            run_demo=False,
        )
        self.assertEqual(report["llm_provider"], "ollama")
        self.assertIn("LLM", report["verified"])
        self.assertIn("LLM", report["configured"])
        _lines, values = read_env_file(self.env_path)
        self.assertEqual(
            values["HARNESS_LLM_BASE_URL"], OLLAMA_BASE_URL
        )
        self.assertEqual(values["HARNESS_LLM_MODEL"], OLLAMA_DEFAULT_MODEL)
        self.assertEqual(values["SOME_OTHER_KEY"], "keepme")
        with open(self.env_path, encoding="utf-8") as f:
            self.assertIn("# keep this comment", f.read())

    def test_wizard_never_prints_secret(self):
        answers = [""] + self._blank_connector_answers()
        # The Slack token is the 4th prompt in call order: LLM choice,
        # Salesforce URL, Salesforce token, Slack token.
        answers[3] = "xoxb-secret-token-123"
        io = _IO(answers)
        run_setup_wizard(
            self.config,
            input_fn=io.input,
            out=io.out,
            getpass_fn=io.getpass,
            env_path=self.env_path,
            run_demo=False,
        )
        self.assertNotIn("xoxb-secret-token-123", io.text())
        _lines, values = read_env_file(self.env_path)
        self.assertEqual(values["SLACK_BOT_TOKEN"], "xoxb-secret-token-123")

    def test_wizard_demo_run_offer(self):
        io = _IO([""] + self._blank_connector_answers() + ["y"])
        report = run_setup_wizard(
            self.config,
            input_fn=io.input,
            out=io.out,
            getpass_fn=io.getpass,
            env_path=self.env_path,
            run_demo=True,
        )
        self.assertIn("Demo draft queued:", io.text())
        self.assertEqual(
            len(ApprovalQueue(self.config.approvals_file).list_all()), 1
        )


class TestDashboardFirstRun(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.config = _temp_config(self._tmpdir.name)
        self._llm_reachable = mock.patch(
            "harness.llm.ollama_reachable", return_value=False
        )
        self._llm_reachable.start()
        self.addCleanup(self._llm_reachable.stop)
        self._setup_reachable = mock.patch(
            "harness.setup.ollama_reachable", return_value=False
        )
        self._setup_reachable.start()
        self.addCleanup(self._setup_reachable.stop)
        self._setup_models = mock.patch(
            "harness.setup.ollama_models", return_value=["llama3.1:8b"]
        )
        self._setup_models.start()
        self.addCleanup(self._setup_models.stop)

    def test_build_state_first_run_true_on_empty(self):
        queue = ApprovalQueue(self.config.approvals_file)
        state = build_state(queue, self.config)
        self.assertTrue(state["first_run"])

    def test_build_state_first_run_false_after_seed(self):
        io = _IO([])
        seed_demo(
            self.config, confirm=True, input_fn=io.input, out=io.out
        )
        queue = ApprovalQueue(self.config.approvals_file)
        state = build_state(queue, self.config)
        self.assertFalse(state["first_run"])

    def test_seed_endpoint(self):
        client = TestClient(create_app(self.config))
        response = client.post("/api/demo/seed")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "seeded")
        response = client.post("/api/demo/seed")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "already-seeded")
        state = client.get("/api/state").json()
        self.assertFalse(state["first_run"])

    def test_seed_endpoint_refuses_over_real_data(self):
        ApprovalQueue(self.config.approvals_file).submit(
            agent="briefer",
            job={"account": "Real Corp"},
            draft="real draft",
            sources=[],
        )
        client = TestClient(create_app(self.config))
        response = client.post("/api/demo/seed")
        self.assertEqual(response.status_code, 409)
