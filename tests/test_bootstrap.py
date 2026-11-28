"""Tests for bootstrap.sh / bootstrap.ps1 (v1.13.0): the one-command install.

The shell script is written so its steps are functions and main() only
runs when the script is executed, never when sourced. These tests
source it and call the functions directly, with fake python3 and curl
binaries on PATH where the real ones would be slow or unavailable.
"""
import os
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOTSTRAP_SH = os.path.join(REPO, "bootstrap.sh")
BOOTSTRAP_PS1 = os.path.join(REPO, "bootstrap.ps1")

FAKE_PYTHON3 = """#!/usr/bin/python3
\"\"\"Fake python3 for bootstrap tests: version from FAKE_PY_VERSION.\"\"\"
import os
import sys

version = os.environ.get("FAKE_PY_VERSION", "3.12.0")
if len(sys.argv) > 2 and sys.argv[1] == "-c":
    code = sys.argv[2]
    if "version_info[:2]" in code:
        print(".".join(version.split(".")[:2]))
    elif "sys.exit" in code:
        major, minor = (int(x) for x in version.split(".")[:2])
        sys.exit(0 if (major, minor) >= (3, 11) else 1)
"""

FAKE_CURL = """#!/bin/sh
# Fake curl for bootstrap tests: succeeds iff FAKE_CURL_OK=1.
if [ "$FAKE_CURL_OK" = "1" ]; then
  exit 0
fi
exit 1
"""


def run_bash(script, env=None, cwd=None, stdin=None):
    """Run a bash snippet with the given env, capturing output."""
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        env=merged,
        cwd=cwd,
        stdin=stdin,
        timeout=60,
    )


def make_bin(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class BootstrapShTest(unittest.TestCase):
    def test_find_python_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = make_bin(tmpdir, "python3", FAKE_PYTHON3)
            result = run_bash(
                'source "%s"; find_python' % BOOTSTRAP_SH,
                env={"PATH": tmpdir, "FAKE_PY_VERSION": "3.12.0"},
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), fake)

    def test_find_python_too_old(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            make_bin(tmpdir, "python3", FAKE_PYTHON3)
            result = run_bash(
                'source "%s"; find_python' % BOOTSTRAP_SH,
                env={"PATH": tmpdir, "FAKE_PY_VERSION": "3.9.5"},
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("Python 3.11 or newer is required", result.stderr)
            self.assertIn("3.9", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_find_python_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_bash(
                'source "%s"; find_python' % BOOTSTRAP_SH,
                env={"PATH": tmpdir},
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("python3 was not found", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_ensure_env_creates_then_preserves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, ".env.example"), "w", encoding="utf-8") as f:
                f.write("HARNESS_LLM_MODEL=llama3.1:8b\n")
            first = run_bash(
                'source "%s"; ensure_env' % BOOTSTRAP_SH, cwd=tmpdir
            )
            self.assertEqual(first.returncode, 0)
            self.assertIn("Created .env", first.stdout)
            env_path = os.path.join(tmpdir, ".env")
            self.assertTrue(os.path.exists(env_path))
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("SENTINEL=keep-me\n")
            second = run_bash(
                'source "%s"; ensure_env' % BOOTSTRAP_SH, cwd=tmpdir
            )
            self.assertEqual(second.returncode, 0)
            self.assertIn("left alone", second.stdout)
            with open(env_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "SENTINEL=keep-me\n")

    def test_ensure_env_missing_example(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_bash(
                'source "%s"; ensure_env' % BOOTSTRAP_SH, cwd=tmpdir
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn(".env.example is missing", result.stderr)

    def test_ensure_venv_reuses_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.mkdir(os.path.join(tmpdir, ".venv"))
            result = run_bash(
                'source "%s"; ensure_venv' % BOOTSTRAP_SH, cwd=tmpdir
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("reusing it", result.stdout)
            self.assertFalse(os.path.exists(os.path.join(tmpdir, ".venv", "bin")))

    def test_check_ollama_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            make_bin(tmpdir, "curl", FAKE_CURL)
            result = run_bash(
                'source "%s"; check_ollama' % BOOTSTRAP_SH,
                env={"PATH": tmpdir, "FAKE_CURL_OK": "1"},
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("Ollama found", result.stdout)

    def test_check_ollama_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            make_bin(tmpdir, "curl", FAKE_CURL)
            result = run_bash(
                'source "%s"; check_ollama' % BOOTSTRAP_SH,
                env={"PATH": tmpdir, "FAKE_CURL_OK": "0"},
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("Ollama not found", result.stdout)
            self.assertIn("setup wizard will offer", result.stdout)

    def test_launch_wizard_without_terminal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_bash(
                'source "%s"; launch_wizard' % BOOTSTRAP_SH,
                cwd=tmpdir,
                stdin=subprocess.DEVNULL,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("python -m harness.run setup", result.stdout)

    def test_no_activation_step(self):
        with open(BOOTSTRAP_SH, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("activate", text)
        self.assertNotIn("source .venv", text)
        self.assertTrue(os.access(BOOTSTRAP_SH, os.X_OK))


class BootstrapPs1Test(unittest.TestCase):
    def test_one_windows_install_script(self):
        # bootstrap.ps1 (v1.13.0, from-source, never run on Windows)
        # was replaced by packaging/install.ps1 (binary download):
        # one Windows install script, not two competing ones.
        ps1 = os.path.join(REPO, "packaging", "install.ps1")
        self.assertFalse(os.path.exists(BOOTSTRAP_PS1))
        self.assertTrue(os.path.exists(ps1))
        with open(ps1, encoding="utf-8") as f:
            text = f.read()
        for needle in (
            # The URL is built from $Repo, so the literal host+repo
            # never appears; both parts are checked instead.
            "jfournier555/belay",
            "github.com/$Repo",
            "belay.exe",
            "Programs\\Belay",
            "Belay.lnk",
            "launch",
        ):
            self.assertIn(needle, text)
        self.assertEqual(text.count("{"), text.count("}"))
        self.assertEqual(text.count("("), text.count(")"))


if __name__ == "__main__":
    unittest.main()
