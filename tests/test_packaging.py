"""Tests for the packaged installer (v1.14.0): the PyInstaller spec
compiles and names the right entry point and data file, and the
release workflow parses and has the three-OS matrix plus the release
job. The YAML check skips when PyYAML is not installed: the workflow
is validated directly in the v1.14.0 build, and the suite must stay
green on a fresh clone without dev-only tools."""
import os
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
            ["Belay-macos.zip", "belay-linux", "belay.exe"],
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
        self.assertIn("Belay-macos.zip", files)
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


if __name__ == "__main__":
    unittest.main()
