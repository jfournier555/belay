"""Where Belay keeps its writable state (v1.14.0).

Run from source, the harness keeps its state in the working directory,
as it always has: approvals.json, memory.json, schedules.json,
escalations.json, mcp-servers.json, and .env next to the checkout.

The packaged app (PyInstaller one-file binary) cannot write there: it
must never write into the install location or next to the binary, and
on macOS the app bundle itself is read-only once installed. So when
running frozen, or when BELAY_DATA_DIR is set, the state files live
in a per-OS user data directory instead:

- Windows: %APPDATA%\\Belay
- macOS:   ~/Library/Application Support/Belay
- Linux:   $XDG_DATA_HOME/belay, else ~/.local/share/belay

BELAY_DATA_DIR overrides all of it (a temp dir in tests, a shared
location for a team install). The same module answers how the user
invokes the harness, so user-facing strings say `belay setup` in the
packaged app and `python -m harness.run setup` from source.

Paths are plain strings here, not pathlib objects, on purpose: the
Windows branch builds a backslash path with string joining, which is
what Windows produces at runtime and what the tests can assert on a
Linux build machine. Callers that need to create the directory use
ensure_data_dir, which wraps the string in a Path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Belay"


def is_frozen() -> bool:
    """True when running as a PyInstaller-packaged binary."""
    return bool(getattr(sys, "frozen", False))


def cli_invocation() -> str:
    """How the user invokes the harness: the binary name when
    packaged (`belay`, `belay.exe`), the module when run from
    source (`python -m harness.run`)."""
    if is_frozen():
        return os.path.basename(sys.executable)
    return "python -m harness.run"


def data_dir() -> str:
    """The per-OS user data directory for Belay state. BELAY_DATA_DIR
    wins over the platform default. Never the install location."""
    override = os.environ.get("BELAY_DATA_DIR")
    if override:
        return os.path.expanduser(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return base.rstrip("\\/") + "\\" + APP_NAME
        home = os.path.expanduser("~")
        return home.rstrip("\\/") + "\\AppData\\Roaming\\" + APP_NAME
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(
            home, "Library", "Application Support", APP_NAME
        )
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, "belay")
    return os.path.join(home, ".local", "share", "belay")


def uses_data_dir() -> bool:
    """True when state belongs in the data directory: packaged, or
    BELAY_DATA_DIR set. From source with no override, the working
    directory is still home, as before."""
    return is_frozen() or bool(os.environ.get("BELAY_DATA_DIR"))


def ensure_data_dir() -> str:
    """Create the data directory (parents included) and return it.
    Called once when the config loads in packaged mode, so the
    stores (which open their files directly) never fail on a
    missing parent."""
    directory = data_dir()
    Path(directory).mkdir(parents=True, exist_ok=True)
    return directory


def default_store_path(filename: str) -> str:
    """Where a store file lives by default: the data directory when
    packaged (or BELAY_DATA_DIR set), else the bare filename in the
    working directory, as before."""
    if uses_data_dir():
        return os.path.join(data_dir(), filename)
    return filename


def default_env_path() -> str:
    """Where .env lives by default: the data directory when packaged
    (or BELAY_DATA_DIR set), else `.env` in the working directory."""
    if uses_data_dir():
        return os.path.join(data_dir(), ".env")
    return ".env"
