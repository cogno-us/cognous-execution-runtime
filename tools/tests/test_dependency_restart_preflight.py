import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dependency_restart_preflight import check_graph, check_manifest


class DependencyPreflightTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"devDependencies": {"vitest": "4.0.18", "@vitest/browser-playwright": "4.0.18"}}
        entries = {name: {"specifier": version, "version": version} for name, version in self.manifest["devDependencies"].items()}
        nodes = {name + "@" + version: {} for name, version in self.manifest["devDependencies"].items()}
        self.lock = {"importers": {"ui": {"devDependencies": entries}}, "packages": copy.deepcopy(nodes), "snapshots": copy.deepcopy(nodes)}

    def test_coherent_baseline(self):
        self.assertEqual(check_manifest(self.manifest, self.lock, "ui"), [])
        self.assertEqual(check_graph(self.lock), [])

    def test_original_manifest_only_upgrade_is_rejected(self):
        self.manifest["devDependencies"]["vitest"] = "4.1.11"
        errors = check_manifest(self.manifest, self.lock, "ui")
        self.assertTrue(any("specifier mismatch" in e for e in errors))
        self.assertTrue(any("coordinated review" in e for e in errors))

    def test_editing_specifiers_cannot_hide_stale_resolution(self):
        for name in self.manifest["devDependencies"]:
            self.manifest["devDependencies"][name] = "4.1.11"
            self.lock["importers"]["ui"]["devDependencies"][name]["specifier"] = "4.1.11"
        errors = check_manifest(self.manifest, self.lock, "ui")
        self.assertEqual(sum("resolution mismatch" in e for e in errors), 2)

    def test_matching_versions_require_present_importer_snapshots(self):
        del self.lock["snapshots"]["vitest@4.0.18"]
        self.assertTrue(any("missing importer snapshot" in e for e in check_manifest(self.manifest, self.lock, "ui")))

    def test_graph_requires_package_resolution(self):
        del self.lock["packages"]["vitest@4.0.18"]
        self.assertTrue(any("no package resolution" in e for e in check_graph(self.lock)))

    def test_graph_requires_transitive_snapshot(self):
        self.lock["snapshots"]["vitest@4.0.18"]["dependencies"] = {"missing": "1.0.0"}
        self.assertTrue(any("missing snapshot" in e for e in check_graph(self.lock)))

    def test_alias_and_local_workspace_references_are_preserved(self):
        self.lock["snapshots"]["vitest@4.0.18"]["dependencies"] = {"browser-alias": "@vitest/browser-playwright@4.0.18", "workspace": "link:../local"}
        self.assertEqual(check_graph(self.lock), [])

    def test_cli_rejects_mismatch_and_malformed_input(self):
        script = Path(__file__).resolve().parents[1] / "dependency_restart_preflight.py"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "ui").mkdir()
            self.manifest["devDependencies"]["vitest"] = "4.1.11"
            (root / "ui/package.json").write_text(json.dumps(self.manifest))
            (root / "pnpm-lock.yaml").write_text(json.dumps(self.lock))
            run = subprocess.run([sys.executable, str(script), "ui", "--root", folder], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("specifier mismatch", run.stdout)
            (root / "pnpm-lock.yaml").write_text("[broken")
            run = subprocess.run([sys.executable, str(script), "ui", "--root", folder], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("Invalid preflight input", run.stderr)


if __name__ == "__main__":
    unittest.main()
