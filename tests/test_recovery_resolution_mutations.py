"""Bounded negative controls in disposable source copies; never edit live code."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "engine/recovery_resolution.py"
TEST = "tests/test_recovery_resolution.py"

# Each mutation disables one protection and selects its behavioral regression.
CASES = [
    ("writer_guard", None,
     "if not self.writer_enabled:", "if False:",
     "test_disabled_writer_and_append_only_controls"),
    ("identity_revalidation", "current",
     "if retained is not None:", "if False:",
     "test_current_revalidates_original_identity_instead_of_returning_stale_resolution"),
    ("conflict_hold", "append",
     "if conflict or existing_hold:", "if False:",
     "test_duplicate_returns_original_admission_and_later_conflict"),
    ("freshness_guard", "append",
     "if not 0 <= age <= 86400:", "if False:",
     "test_freshness_is_evaluated_after_writer_lock_acquisition"),
    ("export_snapshot", "export_attempt",
     'conn.execute("BEGIN")', "pass",
     "test_export_has_one_snapshot_during_concurrent_append"),
    ("provenance_retention", "append",
     '"observation": data, "expected_revision": expected_revision,',
     '"observation": {k: v for k, v in data.items() if k != "observer_ref"}, "expected_revision": expected_revision,',
     "test_unknown_to_applied_survives_restart_with_provenance_and_v1_untouched"),
    ("event_writer", "append",
     'conn.execute("INSERT INTO recovery_synthetic_events VALUES(?,?,?,?,?,?,?)", (\n'
     '                    observation.attempt_id, revision, observation.event_id, digest, kind, retained, _json(receipt)))',
     "pass",
     "test_unknown_to_applied_survives_restart_with_provenance_and_v1_untouched"),
]


def mutate(source, method, before, after):
    start = source.index(f"    def {method}(") if method else 0
    end = source.find("\n    def ", start + 1) if method else -1
    if end == -1:
        end = len(source)
    section = source[start:end]
    assert section.count(before) == 1, "mutation target changed; re-review the control"
    changed = source[:start] + section.replace(before, after, 1) + source[end:]
    compile(changed, SOURCE, "exec")
    return changed


def run_case(directory, test_name, report_name):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(directory)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    report = directory / report_name
    entry = (
        "from pathlib import Path; import sys; import engine.recovery_resolution as m; "
        "assert Path(m.__file__).resolve() == (Path.cwd() / 'engine/recovery_resolution.py').resolve(); "
        "import pytest; raise SystemExit(pytest.main(sys.argv[1:]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", entry, "-q", f"{TEST}::{test_name}", f"--junitxml={report}"],
        cwd=directory, env=env, capture_output=True, text=True, timeout=30,
    )
    assert report.is_file(), result.stdout + result.stderr
    root = ET.parse(report).getroot()
    cases = root.findall(".//testcase")
    assert cases, "control selected no tests"
    assert not root.findall(".//error"), result.stdout + result.stderr
    assert not root.findall(".//skipped"), "skipped cases are not negative-control evidence"
    assert all(case.attrib["name"].split("[", 1)[0] == test_name for case in cases)
    return result, cases


@pytest.mark.parametrize("name,method,before,after,target", CASES, ids=[case[0] for case in CASES])
def test_removed_protection_is_detected(tmp_path, name, method, before, after, target):
    for relative in [SOURCE, TEST, "engine/__init__.py", "engine/revocation_closeout.py"]:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    original = (tmp_path / SOURCE).read_text()
    baseline, baseline_cases = run_case(tmp_path, target, "baseline.xml")
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    (tmp_path / SOURCE).write_text(mutate(original, method, before, after))
    mutated, mutated_cases = run_case(tmp_path, target, "mutated.xml")
    # Exit 1 plus selected testcase failures is required; import/collection errors,
    # timeouts, empty selection or successful mutants cannot satisfy this gate.
    assert mutated.returncode == 1, mutated.stdout + mutated.stderr
    assert len(mutated_cases) == len(baseline_cases)
    assert any(case.find("failure") is not None for case in mutated_cases)
    assert (ROOT / SOURCE).read_text() == original, "live source must remain untouched"
