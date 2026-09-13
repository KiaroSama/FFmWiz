"""The run's evidence must survive an artifact upload that never happened.

CI uploads `results.json`, and that upload failed for real on run 34762807032
with "Artifact storage quota has been hit". `continue-on-error` kept the green
suite from reporting red, and left the job with a verdict and nothing behind it.
`tools/ci_result_summary.py` is the answer: the same facts, written to the job
log and the step summary, where a storage quota has no reach.

These tests hold it to the two things that matter -- the evidence is really in
the text, and the reporting step can never fail a job whose tests passed.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k ci_result_summary
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import FFmWiz

ROOT = Path(FFmWiz.__file__).resolve().parent
SCRIPT = ROOT / "tools" / "ci_result_summary.py"


def _module():
    """Load the script by path: `tools` is a directory of scripts, not a package."""
    spec = importlib.util.spec_from_file_location("ci_result_summary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(**overrides) -> dict:
    payload = {
        "verdict": "failed",
        "exit_code": 1,
        "seconds": 225.9,
        "workers": 8,
        "selection": [],
        "required": ["ffmpeg", "numpy"],
        "environment": {
            "python": "3.13.13",
            "implementation": "CPython",
            "platform": "Windows-11-10.0.26200-SP0",
            "ffmpeg": r"C:\tools\ffmpeg.exe",
            "ffmpeg_version": "ffmpeg version 6.1.1-essentials_build",
            "ffprobe_version": "ffprobe version 6.1.1-essentials_build",
            "numpy": "2.3.4",
            "PySide6": "",
        },
        "modules": [
            {"module": "test_alpha", "seconds": 12.5, "tests": 40,
             "failures": ["test_alpha.Case.test_one"], "errors": [],
             "unexpected": [], "skipped": []},
            {"module": "test_beta", "seconds": 100.0, "tests": 60,
             "failures": [], "errors": ["test_beta.Case.test_two"],
             "unexpected": [],
             "skipped": [
                 {"test": "test_beta.Case.test_three", "reason": "No usable PySide6",
                  "capability": "pyside6"},
                 {"test": "test_beta.Case.test_four", "reason": "No usable PySide6",
                  "capability": "pyside6"},
                 {"test": "test_beta.Case.test_five", "reason": "no NVENC hardware",
                  "capability": "hardware"},
             ]},
        ],
    }
    payload.update(overrides)
    return payload


class TheEvidenceSurvivesTheQuota(unittest.TestCase):
    """What the artifact would have carried is in the text instead."""

    def setUp(self) -> None:
        self.summary = _module().render(_record(), "py3.13 / ffmpeg 6.1.1")

    def test_the_label_and_verdict_lead(self):
        self.assertIn("FAIL", self.summary.splitlines()[0])
        self.assertIn("py3.13 / ffmpeg 6.1.1", self.summary.splitlines()[0])

    def test_every_failing_test_is_named(self):
        self.assertIn("test_alpha.Case.test_one", self.summary)
        self.assertIn("test_beta.Case.test_two", self.summary)

    def test_the_counts_are_the_totals_not_one_module(self):
        self.assertIn("**100**", self.summary)          # 40 + 60 tests

    def test_skips_are_grouped_by_capability(self):
        self.assertIn("pyside6: 2", self.summary)
        self.assertIn("hardware: 1", self.summary)

    def test_the_required_capabilities_are_stated(self):
        # Without these, a reader cannot tell a legitimate skip from a shrunken
        # suite -- which is the distinction the whole --require mechanism makes.
        self.assertIn("ffmpeg", self.summary)
        self.assertIn("numpy", self.summary)

    def test_a_passing_run_says_so(self):
        text = _module().render(_record(verdict="ok", exit_code=0), "")
        self.assertIn("PASS", text.splitlines()[0])

    def test_a_record_that_carries_its_traceback_still_names_the_test(self):
        # run_suite.py now writes {"test": ..., "detail": ...} so a failure
        # record carries the exception text (A06). The bare-string form above
        # is what an older artifact holds, and both must render.
        modern = _record()
        modern["modules"][0]["failures"] = [
            {"test": "test_alpha.Case.test_one", "detail": "RuntimeError: boom"}]
        text = _module().render(modern, "")
        self.assertIn("test_alpha.Case.test_one", text)
        self.assertNotIn("{'test'", text, "the raw dict leaked into the summary")


class TheSummaryNamesWhichTreeItDescribes(unittest.TestCase):
    """A summary without SHA and tool versions describes SOME run, not this one.

    That is the exact reason the lost artifact mattered, so the replacement has
    to carry what the artifact carried.
    """

    def render(self, upload: str = "", **environment) -> str:
        module = _module()
        previous = dict(os.environ)
        os.environ.update(environment)
        try:
            return module.render(_record(), "py3.13 / ffmpeg 6.1.1", upload)
        finally:
            os.environ.clear()
            os.environ.update(previous)

    def test_the_commit_sha_and_run_are_named(self):
        text = self.render(GITHUB_SHA="a92d1d1d9b48ccf619b0a5c07916b20208d8d054",
                           GITHUB_RUN_ID="34765106471", GITHUB_REF_NAME="main")
        self.assertIn("a92d1d1d9b48ccf619b0a5c07916b20208d8d054", text)
        self.assertIn("34765106471", text)
        self.assertIn("main", text)

    def test_the_tool_versions_are_named(self):
        text = self.render()
        self.assertIn("3.13.13", text)
        self.assertIn("6.1.1-essentials_build", text)
        self.assertIn("2.3.4", text)

    def test_an_absent_tool_is_not_listed_as_empty(self):
        # PySide6 is "" on the legs that deliberately do not install it.
        text = self.render()
        self.assertNotIn("PySide6: ``", text)

    def test_a_refused_upload_is_stated_not_implied(self):
        text = self.render(upload="failure")
        self.assertIn("NOT retained", text)
        self.assertIn("this summary is the record", text)

    def test_a_successful_upload_is_stated_too(self):
        self.assertIn("uploaded", self.render(upload="success"))

    def test_nothing_is_claimed_when_the_outcome_is_unknown(self):
        # Locally, and any time the step id is not wired: say nothing rather
        # than guess. Claiming an artifact that does not exist is the failure
        # mode this line exists to prevent.
        text = self.render()
        self.assertNotIn("result artifact", text)


class TheReporterNeverDecides(unittest.TestCase):
    """A summary step that can fail a job is a second verdict. There is one."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_summary_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.summary_file = self.root / "step-summary.md"

    def run_script(self, *arguments: str) -> subprocess.CompletedProcess:
        environment = dict(os.environ, GITHUB_STEP_SUMMARY=str(self.summary_file),
                           PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, str(SCRIPT), *arguments],
                              capture_output=True, text=True, timeout=120,
                              env=environment)

    def test_a_failed_run_still_exits_zero(self):
        record = self.root / "results.json"
        record.write_text(json.dumps(_record()), encoding="utf-8")
        result = self.run_script(str(record), "a label")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("test_alpha.Case.test_one", result.stdout)

    def test_a_missing_record_is_reported_not_swallowed(self):
        result = self.run_script(str(self.root / "absent.json"), "a label")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No readable test record", result.stdout)

    def test_a_corrupt_record_is_reported_not_swallowed(self):
        record = self.root / "broken.json"
        record.write_text("{not json", encoding="utf-8")
        result = self.run_script(str(record))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No readable test record", result.stdout)

    def test_the_step_summary_is_appended_never_overwritten(self):
        # Several steps write to this file in one job; clobbering it would
        # delete another step's evidence to preserve this one's.
        self.summary_file.write_text("### an earlier step\n", encoding="utf-8")
        record = self.root / "results.json"
        record.write_text(json.dumps(_record(verdict="ok", exit_code=0)), encoding="utf-8")
        self.assertEqual(self.run_script(str(record), "leg").returncode, 0)
        written = self.summary_file.read_text(encoding="utf-8")
        self.assertIn("an earlier step", written)
        self.assertIn("PASS", written)

    def test_it_works_with_no_step_summary_variable_at_all(self):
        # Locally, and in any runner that does not set it: print and move on.
        record = self.root / "results.json"
        record.write_text(json.dumps(_record()), encoding="utf-8")
        environment = {key: value for key, value in os.environ.items()
                       if key != "GITHUB_STEP_SUMMARY"}
        environment["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run([sys.executable, str(SCRIPT), str(record)],
                                capture_output=True, text=True, timeout=120,
                                env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FAIL", result.stdout)


class TheWorkflowActuallyRunsIt(unittest.TestCase):
    """A reporter no job calls is decoration."""

    def setUp(self) -> None:
        self.workflow = (ROOT / ".github" / "workflows" / "python-smoke.yml").read_text(
            encoding="utf-8")

    def test_every_job_that_uploads_results_also_summarises_them(self):
        uploads = self.workflow.count("actions/upload-artifact")
        summaries = self.workflow.count("tools/ci_result_summary.py")
        self.assertEqual(uploads, summaries,
                         "a job uploads results.json but does not restate them in the log")

    def test_the_summary_step_always_runs(self):
        # `if: always()` matters most exactly when the suite failed.
        for block in self.workflow.split("- name: ")[1:]:
            if "tools/ci_result_summary.py" in block:
                self.assertIn("if: always()", block)

    def test_every_summary_is_told_the_upload_outcome(self):
        # Without it the summary cannot say whether an artifact exists, and the
        # requirement is precisely never to imply one that the quota refused.
        calls = [block for block in self.workflow.split("- name: ")[1:]
                 if "tools/ci_result_summary.py" in block]
        self.assertTrue(calls)
        for block in calls:
            self.assertIn("steps.upload.outcome", block)
        self.assertEqual(self.workflow.count("id: upload"), len(calls),
                         "a summary reads steps.upload.outcome from a step with no id")


if __name__ == "__main__":
    unittest.main()
