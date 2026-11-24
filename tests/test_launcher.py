"""Tests for double-click launch and the desktop launcher (v1.16.0).

The launcher is what a non-technical user double-clicks: it starts
the dashboard server and opens it in the browser, with no terminal
visible. These tests pin the per-OS launcher files (Linux .desktop,
Windows shortcut script, macOS .app bundle), the icon install, and
the launch path (server thread + browser open), all without touching
the real home directory or opening a real browser.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness.launcher import (
    bundled_icon_path,
    create_launcher,
    installed_icon_path,
    launch_dashboard,
    linux_desktop_entry,
    macos_info_plist,
    macos_launcher_script,
    windows_shortcut_script,
)


class TestIcon(unittest.TestCase):
    def test_bundled_icon_is_a_png(self):
        path = bundled_icon_path()
        self.assertTrue(path.exists())
        with open(path, "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")

    def test_installed_icon_copies_to_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"BELAY_DATA_DIR": tmp}):
                target = installed_icon_path()
            self.assertIsNotNone(target)
            self.assertTrue(os.path.exists(target))
            self.assertEqual(
                os.path.getsize(target),
                os.path.getsize(bundled_icon_path()),
            )


class TestLinuxLauncher(unittest.TestCase):
    def test_desktop_entry_runs_launch_without_terminal(self):
        entry = linux_desktop_entry(
            '"belay" launch', "/home/user/.local/share/belay/belay-icon.png"
        )
        self.assertIn("Exec=\"belay\" launch", entry)
        self.assertIn("Terminal=false", entry)
        self.assertIn("Icon=/home/user/.local/share/belay/belay-icon.png", entry)
        self.assertIn("Type=Application", entry)

    def test_create_launcher_writes_desktop_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            with mock.patch.dict(os.environ, {"BELAY_DATA_DIR": tmp}):
                report = create_launcher(system="Linux", home=home)
            self.assertTrue(report["created"])
            target = Path(report["path"])
            self.assertTrue(target.exists())
            self.assertEqual(
                target.parent,
                home / ".local" / "share" / "applications",
            )
            text = target.read_text(encoding="utf-8")
            self.assertIn("launch", text)
            self.assertIn("Terminal=false", text)
            self.assertIn("belay-icon.png", text)


class TestMacOsLauncher(unittest.TestCase):
    def test_create_launcher_builds_app_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            with mock.patch.dict(os.environ, {"BELAY_DATA_DIR": tmp}):
                report = create_launcher(
                    system="Darwin", home=home, version="1.16.0"
                )
            self.assertTrue(report["created"])
            app = Path(report["path"])
            script = app / "Contents" / "MacOS" / "Belay"
            self.assertTrue(script.exists())
            self.assertTrue(os.access(script, os.X_OK))
            self.assertIn("launch", script.read_text(encoding="utf-8"))
            plist = (app / "Contents" / "Info.plist").read_text(
                encoding="utf-8"
            )
            self.assertIn("1.16.0", plist)
            self.assertIn("net.jfournier.belay", plist)
            self.assertTrue(
                (app / "Contents" / "Resources" / "belay-icon.png").exists()
            )

    def test_plist_names_the_bundle(self):
        plist = macos_info_plist("1.16.0")
        self.assertIn("CFBundleExecutable", plist)
        self.assertIn("Belay", plist)


class TestWindowsLauncher(unittest.TestCase):
    def test_shortcut_script_points_at_belay_launch(self):
        script = windows_shortcut_script(
            "C:\\Program Files\\Belay\\belay.exe",
            "launch",
            "C:\\Users\\x\\Start Menu\\Belay.lnk",
            "C:\\Users\\x\\Belay\\belay-icon.png",
        )
        self.assertIn("CreateShortcut", script)
        self.assertIn("belay.exe", script)
        self.assertIn("launch", script)
        self.assertIn("belay-icon.png", script)

    def test_create_launcher_runs_powershell_per_shortcut(self):
        calls = []

        class _Result:
            returncode = 0

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _Result()

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            with mock.patch.dict(
                os.environ,
                {"BELAY_DATA_DIR": tmp, "APPDATA": str(home / "AppData")},
            ):
                report = create_launcher(
                    system="Windows", home=home, run_fn=fake_run
                )
            self.assertTrue(report["created"])
            self.assertTrue(
                any("powershell" in cmd[0] for cmd in calls)
            )
            self.assertTrue(
                any("Belay.lnk" in cmd[-1] for cmd in calls)
            )


class TestCreateLauncherUnknownSystem(unittest.TestCase):
    def test_unknown_system_reports_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = create_launcher(system="Haiku", home=Path(tmp))
        self.assertFalse(report["created"])
        self.assertIsNone(report["path"])
        self.assertIn("Haiku", report["error"])


class TestLaunchDashboard(unittest.TestCase):
    def test_launch_opens_browser_at_dashboard_url(self):
        opened = []
        with mock.patch(
            "harness.launcher._wait_for_port", return_value=True
        ), mock.patch(
            "uvicorn.run", return_value=None
        ), mock.patch(
            "webbrowser.open", side_effect=lambda url: opened.append(url)
        ):
            result = launch_dashboard(port=8123, open_browser=True)
        self.assertTrue(result["up"])
        self.assertEqual(result["url"], "http://127.0.0.1:8123")
        self.assertEqual(opened, ["http://127.0.0.1:8123"])

    def test_launch_without_browser_open_does_not_open(self):
        opened = []
        with mock.patch(
            "harness.launcher._wait_for_port", return_value=True
        ), mock.patch(
            "uvicorn.run", return_value=None
        ), mock.patch(
            "webbrowser.open", side_effect=lambda url: opened.append(url)
        ):
            result = launch_dashboard(port=8123, open_browser=False)
        self.assertTrue(result["up"])
        self.assertEqual(opened, [])


if __name__ == "__main__":
    unittest.main()
