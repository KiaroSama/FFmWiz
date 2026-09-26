"""A06: report publication is mandatory, atomic and owns only its temporary file."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
import run_suite_report as report

PASS = "import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n"


class AtomicPublication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffmwiz_publish_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "results.json"
        self.args = argparse.Namespace(jobs=1, filter=[], require=[])

    def publish(self):
        with contextlib.redirect_stderr(io.StringIO()):
            return report.write_results(str(self.target), [], 1, 0.0, self.args, {})

    def test_failure_is_an_explicit_false_outcome(self):
        self.target.mkdir()
        self.assertIs(self.publish(), False)
        self.assertEqual([], list(self.root.glob("*.partial")))

    def test_success_is_an_explicit_true_outcome(self):
        self.assertIs(self.publish(), True)
        self.assertEqual("failed", json.loads(self.target.read_text())["verdict"])

    def test_predictable_partial_file_is_never_modified(self):
        sentinel = self.root / "results.json.partial"
        sentinel.write_bytes(b"not owned by this publisher")
        self.publish()
        self.assertEqual(b"not owned by this publisher", sentinel.read_bytes())

    def test_a_preexisting_partial_alias_cannot_change_its_source(self):
        source = self.root / "protected.txt"
        source.write_bytes(b"protected source")
        try:
            os.link(source, self.root / "results.json.partial")
        except OSError as exc:
            self.skipTest(f"hardlink privilege unavailable: {exc}")
        self.publish()
        self.assertEqual(b"protected source", source.read_bytes())

    def test_failed_replace_preserves_previous_report_and_removes_owned_temporary(self):
        self.target.write_text('{"previous": true}', encoding="utf-8")
        with mock.patch.object(report.os, "replace", side_effect=PermissionError("locked target")):
            self.assertIs(self.publish(), False)
        self.assertEqual({"previous": True}, json.loads(self.target.read_text()))
        self.assertEqual([], list(self.root.glob("*.partial")))

    def test_failed_flush_never_publishes_a_partial_document(self):
        self.target.write_text('{"previous": true}', encoding="utf-8")
        with mock.patch.object(report.os, "fsync", side_effect=OSError("disk full")):
            self.assertIs(self.publish(), False)
        self.assertEqual({"previous": True}, json.loads(self.target.read_text()))
        self.assertEqual([], list(self.root.glob("*.partial")))

    def test_concurrent_publishers_get_different_exclusive_temporary_files(self):
        real_replace = os.replace
        names = []
        commit_lock = threading.Lock()
        def replace(source, target):
            # Stage concurrently but serialize replacement: this checks temp
            # ownership, not platform-specific simultaneous rename contention.
            with commit_lock:
                names.append(str(source))
                real_replace(source, target)
        with mock.patch.object(report.os, "replace", side_effect=replace):
            with ThreadPoolExecutor(max_workers=4) as pool:
                outcomes = list(pool.map(lambda _: report.write_results(
                    str(self.target), [], 1, 0.0, self.args, {}), range(16)))
        self.assertTrue(all(outcomes))
        self.assertEqual(16, len(set(names)))
        self.assertEqual("failed", json.loads(self.target.read_text())["verdict"])
        self.assertEqual([], list(self.root.glob("*.partial")))


class RunnerPublication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffmwiz_report_entry_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tests").mkdir()
        for source in TESTS.glob("run_suite*.py"):
            shutil.copy2(source, self.root / "tests" / source.name)
        (self.root / "tests/test_ok.py").write_text(PASS, encoding="utf-8")

    def run_script(self, code):
        return subprocess.run([sys.executable, "-c", code], cwd=self.root,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=45)

    def test_unwritable_json_fails_in_both_worker_modes(self):
        (self.root / "results.json").mkdir()
        for jobs in (1, 2):
            with self.subTest(jobs=jobs):
                result = self.run_script(
                    "import sys; sys.path.insert(0,'tests'); import run_suite; "
                    f"raise SystemExit(run_suite.main(['-j','{jobs}','--json','results.json']))")
                self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                self.assertFalse((self.root / "results.json").is_file())
                self.assertIn("could not write", result.stderr)

    def test_every_publication_stage_can_fail_without_a_false_green(self):
        for jobs in (1, 2):
            for failed_call in (1, 2, 3):
                with self.subTest(jobs=jobs, publication=failed_call):
                    target = self.root / "results.json"
                    target.unlink(missing_ok=True)
                    script = f'''
import sys
sys.path.insert(0, 'tests')
import run_suite
original = run_suite.write_results
calls = 0
def publish(*args, **kwargs):
    global calls
    calls += 1
    if calls == {failed_call}:
        return False
    return original(*args, **kwargs)
run_suite.write_results = publish
raise SystemExit(run_suite.main(['-j','{jobs}','--json','results.json']))
'''
                    result = self.run_script(script)
                    self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                    if target.exists():
                        data = json.loads(target.read_text())
                        self.assertEqual("failed", data["verdict"])
                        self.assertEqual(0 if failed_call == 1 else 1,
                                         sum(m["tests"] for m in data["modules"]))

    def test_normal_cli_success_and_no_json_mode_are_preserved(self):
        for arguments in (["-j", "1", "--json", "results.json"], ["-j", "2"]):
            with self.subTest(arguments=arguments):
                result = self.run_script(
                    "import sys; sys.path.insert(0,'tests'); import run_suite; "
                    f"raise SystemExit(run_suite.main({arguments!r}))")
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        data = json.loads((self.root / "results.json").read_text())
        self.assertEqual("ok", data["verdict"])


if __name__ == "__main__":
    unittest.main()
