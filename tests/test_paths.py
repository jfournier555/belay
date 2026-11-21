"""Tests for harness.paths (v1.14.0): the per-OS user data directory,
frozen detection, the invocation string, and the Config defaults that
follow them. From source with no override, everything is exactly as
before: bare filenames in the working directory."""
import os
import sys
import tempfile
import unittest
from unittest import mock

from harness import Config
from harness.paths import (
    cli_invocation,
    data_dir,
    default_env_path,
    default_store_path,
    ensure_data_dir,
    is_frozen,
    uses_data_dir,
)
from harness.setup import print_setup_hint


class TestFrozenDetection(unittest.TestCase):
    def test_not_frozen_from_source(self):
        self.assertFalse(is_frozen())
        self.assertFalse(uses_data_dir())
        self.assertEqual(cli_invocation(), "python -m harness.run")

    def test_frozen_when_sys_frozen_set(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertTrue(is_frozen())
            self.assertTrue(uses_data_dir())
            self.assertEqual(
                cli_invocation(), os.path.basename(sys.executable)
            )

    def test_belay_data_dir_alone_uses_data_dir(self):
        with mock.patch.dict(os.environ, {"BELAY_DATA_DIR": "/tmp/x"}):
            self.assertFalse(is_frozen())
            self.assertTrue(uses_data_dir())


class TestDataDir(unittest.TestCase):
    def _env(self, **overrides):
        """A clean environment for data_dir: only the keys given."""
        clean = {"HOME": "/home/tester"}
        clean.update(overrides)
        return mock.patch.dict(os.environ, clean, clear=True)

    def test_linux_default(self):
        with self._env(), mock.patch.object(
            sys, "platform", "linux"
        ), mock.patch("os.name", "posix"):
            self.assertEqual(
                data_dir(), os.path.join("/home/tester", ".local", "share", "belay")
            )

    def test_linux_xdg(self):
        with self._env(XDG_DATA_HOME="/home/tester/xdg"), mock.patch.object(
            sys, "platform", "linux"
        ), mock.patch("os.name", "posix"):
            self.assertEqual(data_dir(), "/home/tester/xdg/belay")

    def test_macos(self):
        with self._env(), mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(
                data_dir(),
                "/home/tester/Library/Application Support/Belay",
            )

    def test_windows_appdata(self):
        with self._env(APPDATA="C:\\Users\\tester\\AppData\\Roaming"), mock.patch(
            "os.name", "nt"
        ):
            self.assertEqual(
                data_dir(),
                "C:\\Users\\tester\\AppData\\Roaming\\Belay",
            )

    def test_windows_no_appdata(self):
        with self._env(), mock.patch("os.name", "nt"):
            self.assertEqual(
                data_dir(),
                "/home/tester\\AppData\\Roaming\\Belay",
            )

    def test_override_wins(self):
        with self._env(BELAY_DATA_DIR="~/custom-belay"):
            self.assertEqual(data_dir(), "/home/tester/custom-belay")

    def test_ensure_data_dir_creates(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "nested", "belay")
            with mock.patch.dict(os.environ, {"BELAY_DATA_DIR": target}):
                made = ensure_data_dir()
                self.assertEqual(str(made), target)
                self.assertTrue(os.path.isdir(target))


class TestDefaultPaths(unittest.TestCase):
    def test_source_defaults_are_bare_filenames(self):
        self.assertEqual(default_store_path("approvals.json"), "approvals.json")
        self.assertEqual(default_env_path(), ".env")

    def test_frozen_defaults_are_under_data_dir(self):
        with mock.patch.object(
            sys, "frozen", True, create=True
        ), mock.patch.dict(os.environ, {"BELAY_DATA_DIR": "/tmp/belay-data"}):
            self.assertEqual(
                default_store_path("approvals.json"),
                "/tmp/belay-data/approvals.json",
            )
            self.assertEqual(default_env_path(), "/tmp/belay-data/.env")


class TestConfigDataDir(unittest.TestCase):
    def test_from_env_points_at_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = os.path.join(tmp, ".env")
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("HARNESS_LLM_MODEL=test-model-x\n")
            clean = {
                "BELAY_DATA_DIR": tmp,
                "HOME": "/home/tester",
                "PATH": os.environ.get("PATH", ""),
            }
            for key in (
                "HARNESS_APPROVALS_FILE",
                "HARNESS_MEMORY_FILE",
                "HARNESS_SCHEDULES_FILE",
                "HARNESS_MCP_SERVERS_FILE",
                "HARNESS_ESCALATIONS_FILE",
                "HARNESS_LLM_MODEL",
                "HARNESS_LLM_BASE_URL",
                "HARNESS_LLM_API_KEY",
            ):
                clean.pop(key, None)
            with mock.patch.dict(os.environ, clean, clear=True):
                config = Config.from_env()
            self.assertEqual(
                config.approvals_file, os.path.join(tmp, "approvals.json")
            )
            self.assertEqual(
                config.memory_file, os.path.join(tmp, "memory.json")
            )
            self.assertEqual(
                config.schedules_file, os.path.join(tmp, "schedules.json")
            )
            self.assertEqual(
                config.escalations_file, os.path.join(tmp, "escalations.json")
            )
            self.assertEqual(config.env_path, env_path)
            # The data-dir .env loads: the model came from it.
            self.assertEqual(config.llm_model, "test-model-x")
            self.assertTrue(os.path.isdir(tmp))

    def test_from_env_source_defaults_unchanged(self):
        clean = {"HOME": "/home/tester", "PATH": os.environ.get("PATH", "")}
        for key in (
            "BELAY_DATA_DIR",
            "HARNESS_APPROVALS_FILE",
            "HARNESS_MEMORY_FILE",
            "HARNESS_SCHEDULES_FILE",
            "HARNESS_MCP_SERVERS_FILE",
            "HARNESS_ESCALATIONS_FILE",
        ):
            clean.pop(key, None)
        with mock.patch.dict(os.environ, clean, clear=True):
            config = Config.from_env()
        self.assertEqual(config.approvals_file, "approvals.json")
        self.assertEqual(config.memory_file, "memory.json")
        self.assertEqual(config.env_path, ".env")


class TestHintInvocation(unittest.TestCase):
    def test_hint_names_belay_setup_when_frozen(self):
        lines: list[str] = []
        config = Config()
        with mock.patch.object(
            sys, "frozen", True, create=True
        ), mock.patch("harness.setup.ollama_reachable", return_value=False):
            printed = print_setup_hint(config, out=lines.append, interactive=True)
        self.assertTrue(printed)
        text = "\n".join(lines)
        self.assertIn("`%s setup`" % os.path.basename(sys.executable), text)
        self.assertNotIn("python -m harness.run", text)


if __name__ == "__main__":
    unittest.main()
