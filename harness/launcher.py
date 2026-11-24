"""Double-click launch and desktop launcher (v1.16.0).

A CSM never opens a terminal. The packaged app therefore ships a
double-click path to the dashboard, and the setup wizard creates it
automatically at the end of setup, so the user never hand-writes a
launcher file:

- Linux: a .desktop file in ~/.local/share/applications/ (the bare
  binary does not run on double-click in KDE/Dolphin: it shows a
  "Choose Application" dialog instead, so the launcher is the fix,
  not the binary itself).
- Windows: a Start Menu shortcut (and a desktop shortcut) pointing
  at the binary.
- macOS: a Belay.app bundle in ~/Applications.

Every launcher runs `belay launch`: start the dashboard server and
open it in the browser, with no terminal visible. `belay launch`
is also a CLI command, and a bare `belay` with no subcommand launches
the dashboard too (the GUI-first default, decided in v1.16.0).

The launcher is best effort, never fatal: a failed write is
reported and setup continues. Creating it never needs network, and
nothing here prints a secret.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from harness.paths import data_dir, ensure_data_dir, is_frozen

ICON_NAME = "belay-icon.png"
APP_NAME = "Belay"


def bundled_icon_path() -> Path:
    """The icon as shipped in the repo (and in the PyInstaller bundle)."""
    return Path(__file__).with_name("assets") / ICON_NAME


def installed_icon_path() -> str | None:
    """Copy the icon into the Belay data dir and return its path.

    The data dir is writable in the packaged app; the bundle itself
    is read-only on macOS once installed, so the launcher points at
    this copy, not at the bundle. Returns None when there is no icon
    to copy: the launcher is created without an icon instead of
    failing.
    """
    source = bundled_icon_path()
    if not source.exists():
        return None
    try:
        ensure_data_dir()
        target = Path(data_dir()) / ICON_NAME
        if not target.exists() or target.stat().st_size != source.stat().st_size:
            shutil.copyfile(source, target)
        return str(target)
    except OSError:
        return None


def launch_command_parts() -> list[str]:
    """How to run `belay launch`: the binary when packaged, the
    module when run from source."""
    if is_frozen():
        return [sys.executable]
    return [sys.executable, "-m", "harness.run"]


def repo_root() -> Path:
    """The checkout root, when run from source."""
    return Path(__file__).resolve().parent.parent


def linux_desktop_entry(exec_line: str, icon_path: str | None) -> str:
    """The .desktop file text. Terminal=false: no terminal visible."""
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=Belay",
        "Comment=Post-sales AI harness",
        "Exec=%s" % exec_line,
        "Terminal=false",
        "Categories=Office;",
        "StartupWMClass=Belay",
    ]
    if not is_frozen():
        lines.append("Path=%s" % repo_root())
    if icon_path:
        lines.append("Icon=%s" % icon_path)
    return "\n".join(lines) + "\n"


def windows_shortcut_script(
    target: str, arguments: str, shortcut_path: str, icon_path: str | None
) -> str:
    """PowerShell that creates one .lnk shortcut via WScript.Shell.

    Returned as text (not run here) so the caller can run it and the
    tests can assert on it without touching Windows.
    """
    lines = [
        "$shell = New-Object -ComObject WScript.Shell",
        "$link = $shell.CreateShortcut('%s')" % shortcut_path.replace("'", "''"),
        "$link.TargetPath = '%s'" % target.replace("'", "''"),
        "$link.Arguments = '%s'" % arguments.replace("'", "''"),
    ]
    if not is_frozen():
        lines.append(
            "$link.WorkingDirectory = '%s'"
            % str(repo_root()).replace("'", "''")
        )
    if icon_path:
        lines.append(
            "$link.IconLocation = '%s'" % icon_path.replace("'", "''")
        )
    lines.append("$link.Save()")
    return "\n".join(lines)


def macos_launcher_script() -> str:
    """The Belay script inside the .app bundle: run the dashboard
    launch with no terminal."""
    parts = launch_command_parts()
    if is_frozen():
        return '#!/bin/sh\nexec "%s" launch\n' % parts[0]
    return '#!/bin/sh\ncd "%s"\nexec "%s" -m harness.run launch\n' % (
        repo_root(),
        parts[0],
    )


def macos_info_plist(version: str) -> str:
    """Info.plist for the auto-created Belay.app."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>Belay</string>
  <key>CFBundleIdentifier</key>
  <string>net.jfournier.belay</string>
  <key>CFBundleName</key>
  <string>Belay</string>
  <key>CFBundleShortVersionString</key>
  <string>%s</string>
  <key>CFBundleVersion</key>
  <string>%s</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
</dict>
</plist>
""" % (version, version)


def _create_linux_launcher(home: Path, icon_path: str | None) -> str:
    """Write the .desktop file. Returns its path."""
    applications = home / ".local" / "share" / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    parts = launch_command_parts()
    exec_line = " ".join('"%s"' % p for p in parts) + " launch"
    target = applications / "belay.desktop"
    target.write_text(
        linux_desktop_entry(exec_line, icon_path), encoding="utf-8"
    )
    return str(target)


def _create_windows_launcher(home: Path, icon_path: str | None, run_fn=None) -> list[str]:
    """Create Start Menu and desktop shortcuts. Returns the paths
    created. Runs PowerShell once per shortcut; a failure raises,
    and the caller reports it instead of failing setup."""
    run_fn = run_fn or subprocess.run
    parts = launch_command_parts()
    target = parts[0]
    arguments = "launch" if is_frozen() else "-m harness.run launch"
    appdata = os.environ.get("APPDATA") or str(
        home / "AppData" / "Roaming"
    )
    desktop = os.environ.get("USERPROFILE") and os.path.join(
        os.environ["USERPROFILE"], "Desktop"
    )
    destinations = [
        os.path.join(
            appdata,
            "Microsoft",
            "Windows",
            "Start Menu",
            "Programs",
            "Belay.lnk",
        )
    ]
    if desktop:
        destinations.append(os.path.join(desktop, "Belay.lnk"))
    created: list[str] = []
    for destination in destinations:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        script = windows_shortcut_script(
            target, arguments, destination, icon_path
        )
        result = run_fn(
            ["powershell", "-NoProfile", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            created.append(destination)
    return created


def _create_macos_launcher(home: Path, version: str) -> str:
    """Create ~/Applications/Belay.app. Returns its path."""
    app = home / "Applications" / "Belay.app"
    macos_dir = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    launcher = macos_dir / "Belay"
    launcher.write_text(macos_launcher_script(), encoding="utf-8")
    launcher.chmod(0o755)
    (app / "Contents" / "Info.plist").write_text(
        macos_info_plist(version), encoding="utf-8"
    )
    source = bundled_icon_path()
    if source.exists():
        shutil.copyfile(source, resources / ICON_NAME)
    return str(app)


def create_launcher(
    system: str | None = None,
    home: Path | None = None,
    version: str = "1.16.0",
    run_fn=None,
) -> dict:
    """Create the desktop launcher for this OS. Best effort: never
    raises. Returns {"created": bool, "path": str|None, "platform":
    str, "error": str|None}. `system` and `home` are injectable so
    tests can build each platform's launcher on Linux."""
    current = system or platform.system()
    home = home or Path.home()
    icon_path = installed_icon_path()
    try:
        if current == "Linux":
            path = _create_linux_launcher(home, icon_path)
            return {"created": True, "path": path, "platform": current, "error": None}
        if current == "Windows":
            created = _create_windows_launcher(home, icon_path, run_fn=run_fn)
            return {
                "created": bool(created),
                "path": created[0] if created else None,
                "platform": current,
                "error": None if created else "no shortcut was created",
            }
        if current == "Darwin":
            path = _create_macos_launcher(home, version)
            return {"created": True, "path": path, "platform": current, "error": None}
        return {
            "created": False,
            "path": None,
            "platform": current,
            "error": "no launcher for this system (%s)" % current,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "created": False,
            "path": None,
            "platform": current,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# `belay launch`: start the dashboard server and open it in the browser.
# ---------------------------------------------------------------------------


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    """Poll until the dashboard answers a TCP connect, or give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def launch_dashboard(
    port: int = 8000,
    host: str = "127.0.0.1",
    open_browser: bool = True,
    config=None,
) -> dict:
    """Serve the dashboard in a background thread and open it in the
    browser. Returns {"url": ..., "up": bool}. The server thread is
    a daemon: the caller blocks until it is stopped (Ctrl-C in a
    terminal, or closing the launcher). Separated from the CLI so
    tests can call it with a stubbed server."""
    import uvicorn

    from harness.dashboard import create_app

    cfg = config
    if cfg is None:
        from harness.config import Config

        cfg = Config.from_env()
    app = create_app(cfg)
    thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": host, "port": port, "log_level": "warning"},
        daemon=True,
    )
    thread.start()
    up = _wait_for_port(host, port)
    url = "http://%s:%d" % (host, port)
    if up and open_browser:
        webbrowser.open(url)
    return {"url": url, "up": up, "thread": thread}
