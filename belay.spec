# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Belay packaged app (v1.14.0).

One-file console binary, named `belay`. The entry point is
harness/run.py: the same main() the `cs-harness` console script
calls, so the binary is the full CLI (run, approvals, memory,
schedules, scheduler, setup, dashboard, and the rest).

dashboard.html ships as data next to the harness package, so the
dashboard template loads from the bundle the same way it loads from
source (Path(__file__).with_name("dashboard.html") resolves into the
extraction dir). The app icon (harness/assets/belay-icon.png) ships
the same way: the dashboard serves it as its favicon/header image,
and the setup wizard installs it to the data dir for the desktop
launcher.

No code signing here: the first packaged release is unsigned, and
signing is a follow-up that needs the Apple Developer account and a
Windows certificate. The README documents the Gatekeeper and
SmartScreen warnings.

Build it with: pyinstaller belay.spec
(after: pip install -r requirements-build.txt)
"""

block_cipher = None

a = Analysis(
    ["harness/run.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("harness/dashboard.html", "harness"),
        ("harness/assets/belay-icon.png", "harness/assets"),
        ("connectors/csv/sample_accounts.csv", "connectors/csv"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="belay",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
