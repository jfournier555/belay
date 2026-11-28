"""Tests for the packaged installer (v1.14.0): the PyInstaller spec
compiles and names the right entry point and data file, and the
release workflow parses and has the three-OS matrix plus the release
job. The YAML check skips when PyYAML is not installed: the workflow
is validated directly in the v1.14.0 build, and the suite must stay
green on a fresh clone without dev-only tools.

Also the 0.18.3 installers (packaging/): the Windows Inno Setup
script, the Linux .deb and AppImage builders, the AUR PKGBUILD, and
the CLI install scripts. Shell scripts are written so their steps are
functions and main() only runs when executed, never when sourced: these
tests source them and call the functions directly, with fake binaries
on PATH where the real ones would be slow or unavailable.
"""
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

try:
    import yaml
except ImportError:  # noqa: BLE001 - optional dev-only dependency
    yaml = None

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSpec(unittest.TestCase):
    def test_spec_compiles_and_names_entry_and_template(self):
        spec_path = os.path.join(REPO, "belay.spec")
        with open(spec_path, "r", encoding="utf-8") as f:
            source = f.read()
        compile(source, spec_path, "exec")
        self.assertIn("harness/run.py", source)
        self.assertIn("dashboard.html", source)
        self.assertIn('name="belay"', source)

    def test_spec_bundles_the_app_icon(self):
        spec_path = os.path.join(REPO, "belay.spec")
        with open(spec_path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("belay-icon.png", source)
        self.assertTrue(
            os.path.exists(
                os.path.join(REPO, "harness", "assets", "belay-icon.png")
            )
        )


@unittest.skipUnless(yaml is not None, "PyYAML not installed")
class TestReleaseWorkflow(unittest.TestCase):
    def _workflow(self):
        path = os.path.join(REPO, ".github", "workflows", "release.yml")
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_triggered_by_version_tags(self):
        workflow = self._workflow()
        # PyYAML reads `on:` as the boolean True (YAML 1.1).
        trigger = workflow.get("on", workflow.get(True))
        self.assertEqual(trigger["push"]["tags"], ["v*"])

    def test_matrix_covers_three_os(self):
        workflow = self._workflow()
        include = workflow["jobs"]["build"]["strategy"]["matrix"]["include"]
        self.assertEqual(
            sorted(entry["os"] for entry in include),
            ["macos-latest", "ubuntu-latest", "windows-latest"],
        )
        self.assertEqual(
            sorted(entry["artifact"] for entry in include),
            ["Belay-macos.dmg", "belay-linux", "belay.exe"],
        )

    def test_build_uses_the_spec(self):
        workflow = self._workflow()
        steps = workflow["jobs"]["build"]["steps"]
        runs = [str(step.get("run", "")) for step in steps]
        self.assertTrue(
            any("pyinstaller" in run and "belay.spec" in run for run in runs)
        )

    def test_release_job_uploads_all_three_artifacts(self):
        workflow = self._workflow()
        release = workflow["jobs"]["release"]
        self.assertEqual(release["needs"], "build")
        steps = release["steps"]
        files = ""
        for step in steps:
            files += str(step.get("with", {}).get("files", ""))
        self.assertIn("belay.exe", files)
        self.assertIn("Belay-macos.dmg", files)
        self.assertIn("belay-linux", files)

    def test_macos_app_launches_dashboard_without_terminal(self):
        # v1.16.0: double-clicking Belay.app runs `belay launch`
        # (dashboard in the browser, no terminal), not a Terminal
        # window running the interactive wizard.
        workflow = self._workflow()
        steps = workflow["jobs"]["build"]["steps"]
        runs = "\n".join(str(step.get("run", "")) for step in steps)
        self.assertIn('exec "$DIR/belay" launch', runs)
        self.assertNotIn("open -a Terminal", runs)
        self.assertIn("belay-icon.png", runs)

    def test_release_job_uploads_the_installers(self):
        # 0.18.3: the Windows installer, the Linux .deb and the
        # AppImage ride the release alongside the three binaries.
        workflow = self._workflow()
        release = workflow["jobs"]["release"]
        steps = release["steps"]
        files = ""
        for step in steps:
            files += str(step.get("with", {}).get("files", ""))
        self.assertIn("Belay-Setup-", files)
        self.assertIn("belay_", files)
        self.assertIn("_amd64.deb", files)
        self.assertIn("Belay-", files)
        self.assertIn("-x86_64.AppImage", files)

    def test_build_job_builds_the_installers(self):
        # 0.18.3: Inno Setup on Windows, dpkg-deb and appimagetool
        # on Ubuntu. The macOS DMG is unchanged.
        workflow = self._workflow()
        steps = workflow["jobs"]["build"]["steps"]
        runs = "\n".join(str(step.get("run", "")) for step in steps)
        self.assertIn("iscc", runs)
        self.assertIn("belay.iss", runs)
        self.assertIn("build-deb.sh", runs)
        self.assertIn("build-appimage.sh", runs)
        self.assertIn("appimagetool", runs)

    def test_release_job_renders_plain_language_notes(self):
        # 0.18.5: the release notes lead with a "Get Belay" section
        # rendered from the template, and the action prepends it to
        # the auto-generated changelog (per the action's docs, a
        # supplied body is pre-pended to the generated notes).
        workflow = self._workflow()
        release = workflow["jobs"]["release"]
        steps = release["steps"]
        uses = [str(step.get("uses", "")) for step in steps]
        # The release job needs the template, so it checks out.
        self.assertIn("actions/checkout@v4", uses)
        runs = "\n".join(str(step.get("run", "")) for step in steps)
        self.assertIn("packaging/release-notes.md", runs)
        self.assertIn("s/{version}/", runs)
        self.assertIn("> release-notes.md", runs)
        for step in steps:
            if "softprops/action-gh-release" in str(step.get("uses", "")):
                with_ = step.get("with", {})
                self.assertEqual(with_.get("body_path"), "release-notes.md")
                self.assertTrue(with_.get("generate_release_notes"))


PACKAGING = os.path.join(REPO, "packaging")
ISS = os.path.join(PACKAGING, "windows", "belay.iss")
ICO = os.path.join(PACKAGING, "windows", "belay.ico")
DESKTOP = os.path.join(PACKAGING, "linux", "belay.desktop")
BUILD_DEB = os.path.join(PACKAGING, "linux", "build-deb.sh")
BUILD_APPIMAGE = os.path.join(PACKAGING, "linux", "build-appimage.sh")
PKGBUILD = os.path.join(PACKAGING, "aur", "PKGBUILD")
SRCINFO = os.path.join(PACKAGING, "aur", ".SRCINFO")
INSTALL_SH = os.path.join(PACKAGING, "install.sh")
INSTALL_PS1 = os.path.join(PACKAGING, "install.ps1")
RELEASE_NOTES = os.path.join(PACKAGING, "release-notes.md")

FAKE_UNAME = """#!/bin/sh
# Fake uname for install tests: values from FAKE_UNAME_S / FAKE_UNAME_M.
if [ "$1" = "-s" ]; then
  printf '%s\\n' "${FAKE_UNAME_S:-Linux}"
elif [ "$1" = "-m" ]; then
  printf '%s\\n' "${FAKE_UNAME_M:-x86_64}"
fi
"""


def run_bash(script, env=None, cwd=None):
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
        timeout=120,
    )


def make_bin(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class IssTest(unittest.TestCase):
    def test_inno_setup_script(self):
        text = read(ISS)
        # Per-user install, Start Menu shortcut, optional PATH and
        # desktop icon, version from the workflow (/DMyAppVersion).
        for needle in (
            "AppName",
            "Belay",
            "{localappdata}\\Programs",
            "PrivilegesRequired=lowest",
            "{autoprograms}",
            "belay.exe",
            "launch",
            "belay.ico",
            "addtopath",
            "NeedsAddPath",
            "OutputBaseFilename=Belay-Setup-{#MyAppVersion}",
        ):
            self.assertIn(needle, text)
        # No hardcoded release version: the workflow passes it in.
        self.assertNotIn("0.18", text)
        # The installer never touches the data folder: no operative
        # line (comments excluded) references it or deletes on
        # uninstall.
        code = "\n".join(
            line for line in text.splitlines()
            if not line.strip().startswith(";")
        )
        self.assertNotIn("APPDATA", code)
        self.assertNotIn("UninstallDelete", text)

    def test_icon_is_png_in_ico(self):
        with open(ICO, "rb") as f:
            data = f.read()
        # ICO header, one image, embedding the PNG.
        self.assertEqual(data[:4], b"\x00\x00\x01\x00")
        self.assertIn(b"\x89PNG\r\n\x1a\n", data)


class DesktopTest(unittest.TestCase):
    def test_shared_desktop_entry(self):
        fields = {}
        for line in read(DESKTOP).splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value
        self.assertEqual(fields.get("Type"), "Application")
        self.assertEqual(fields.get("Name"), "Belay")
        self.assertEqual(fields.get("Exec"), "belay launch")
        self.assertEqual(fields.get("Icon"), "belay")
        self.assertEqual(fields.get("Terminal"), "false")


class AurTest(unittest.TestCase):
    def test_pkgbuild_and_srcinfo_agree(self):
        pkgbuild = read(PKGBUILD)
        srcinfo = read(SRCINFO)
        pkgver = re.search(r"^pkgver=(\S+)", pkgbuild, re.M).group(1)
        self.assertEqual(
            re.search(r"^\tpkgver = (\S+)", srcinfo, re.M).group(1), pkgver
        )
        # Every source in the PKGBUILD is in .SRCINFO, with a matching
        # sha256sums entry (SKIP until the release binary exists).
        sources = re.findall(r'"([^"]+)"', re.search(
            r"^source=\((.*?)\)", pkgbuild, re.S | re.M).group(1))
        srcinfo_sources = re.findall(r"^\tsource = (\S+)", srcinfo, re.M)
        self.assertEqual(len(sources), len(srcinfo_sources))
        self.assertEqual(
            len(re.findall(r"^\tsha256sums = ", srcinfo, re.M)), len(sources))
        self.assertIn("releases/download/v${pkgver}/belay-linux", pkgbuild)
        self.assertIn("belay.desktop", pkgbuild)
        self.assertIn("belay-icon.png", pkgbuild)
        self.assertIn("usr/bin/belay", pkgbuild)


class DebTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb not available")
    def test_build_deb(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = make_bin(tmpdir, "belay-linux", "#!/bin/sh\necho fake\n")
            outdir = os.path.join(tmpdir, "out")
            result = run_bash(
                '"%s" "%s" 0.18.3 "%s"' % (BUILD_DEB, fake, outdir)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            deb = os.path.join(outdir, "belay_0.18.3_amd64.deb")
            self.assertTrue(os.path.exists(deb))
            info = subprocess.run(
                ["dpkg-deb", "--info", deb],
                capture_output=True, text=True, timeout=60,
            ).stdout
            for needle in (
                "Package: belay",
                "Version: 0.18.3",
                "Architecture: amd64",
                "Maintainer: Justin Fournier",
            ):
                self.assertIn(needle, info)
            contents = subprocess.run(
                ["dpkg-deb", "--contents", deb],
                capture_output=True, text=True, timeout=60,
            ).stdout
            for needle in (
                "./usr/bin/belay",
                "./usr/share/applications/belay.desktop",
                "./usr/share/pixmaps/belay.png",
            ):
                self.assertIn(needle, contents)
            # Installed dirs are world-readable: a restrictive umask
            # must not lock normal users out of /usr/bin.
            self.assertNotIn("drwxrwx---", contents)
            self.assertNotIn("drwx------", contents)


class AppImageTest(unittest.TestCase):
    def test_assemble_appdir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = make_bin(tmpdir, "belay-linux", "#!/bin/sh\necho fake\n")
            appdir = os.path.join(tmpdir, "Belay.AppDir")
            result = run_bash(
                'source "%s"; assemble_appdir "%s" "%s"'
                % (BUILD_APPIMAGE, appdir, fake)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.access(
                os.path.join(appdir, "AppRun"), os.X_OK))
            self.assertTrue(os.access(
                os.path.join(appdir, "usr", "bin", "belay"), os.X_OK))
            self.assertTrue(os.path.exists(
                os.path.join(appdir, "belay.desktop")))
            self.assertTrue(os.path.exists(
                os.path.join(appdir, "belay.png")))
            with open(os.path.join(appdir, "AppRun"), encoding="utf-8") as f:
                self.assertIn("usr/bin/belay", f.read())


class InstallShTest(unittest.TestCase):
    def test_bash_syntax(self):
        for script in (INSTALL_SH, BUILD_DEB, BUILD_APPIMAGE):
            result = run_bash('bash -n "%s"' % script)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_asset_for(self):
        cases = (
            ("linux", "x86_64", "belay-linux"),
            ("darwin", "arm64", "Belay-macos.dmg"),
            ("darwin", "x86_64", "Belay-macos.dmg"),
        )
        for os_name, arch, expected in cases:
            result = run_bash(
                'source "%s"; asset_for %s %s' % (INSTALL_SH, os_name, arch)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), expected)
        result = run_bash(
            'source "%s"; asset_for linux arm64' % INSTALL_SH
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no Linux binary", result.stderr)

    def test_download_url(self):
        result = run_bash(
            'source "%s"; download_url latest belay-linux' % INSTALL_SH
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "https://github.com/jfournier555/belay/releases/latest/download/belay-linux",
        )
        result = run_bash(
            'source "%s"; download_url 0.18.3 Belay-macos.dmg' % INSTALL_SH
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "https://github.com/jfournier555/belay/releases/download/v0.18.3/Belay-macos.dmg",
        )

    def test_detect_os(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            make_bin(tmpdir, "uname", FAKE_UNAME)
            for fake_os, expected in (("Linux", "linux"), ("Darwin", "darwin")):
                result = run_bash(
                    'source "%s"; detect_os' % INSTALL_SH,
                    env={"PATH": tmpdir, "FAKE_UNAME_S": fake_os},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)


class InstallPs1Test(unittest.TestCase):
    def test_windows_install_script(self):
        text = read(INSTALL_PS1)
        for needle in (
            # The URL is built from $Repo, so the literal host+repo
            # never appears; both parts are checked instead.
            "jfournier555/belay",
            "github.com/$Repo",
            "releases/latest/download/belay.exe",
            "Programs\\Belay",
            "Belay.lnk",
            "launch",
            "Environment",
        ):
            self.assertIn(needle, text)
        self.assertEqual(text.count("{"), text.count("}"))
        self.assertEqual(text.count("("), text.count(")"))


class ReleaseNotesTest(unittest.TestCase):
    def _rendered(self, version="0.18.5"):
        # Render exactly the way the release workflow does: sed
        # substituting {version} from the tag.
        result = subprocess.run(
            ["sed", "s/{version}/%s/g" % version, RELEASE_NOTES],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_template_renders_with_version(self):
        # 0.18.5: the "Get Belay" section names every download in
        # plain language, with the release version in the filenames.
        rendered = self._rendered("0.18.5")
        for needle in (
            "## Get Belay",
            "**Windows** — double-click **Belay-Setup-0.18.5.exe**",
            "**Mac** — open **Belay-macos.dmg**",
            "**Linux, Ubuntu/Debian** — double-click **belay_0.18.5_amd64.deb**",
            "**Linux, any distro** — double-click **Belay-0.18.5-x86_64.AppImage**",
            "**Terminal only (advanced)** — belay.exe (Windows) or belay-linux (Linux)",
        ):
            self.assertIn(needle, rendered)
        self.assertNotIn("{version}", rendered)

    def test_template_has_version_placeholders(self):
        # The template must not hardcode a release: every versioned
        # filename carries the {version} placeholder the workflow
        # substitutes from the tag.
        text = read(RELEASE_NOTES)
        self.assertIn("{version}", text)
        self.assertNotIn("0.18", text)


if __name__ == "__main__":
    unittest.main()
