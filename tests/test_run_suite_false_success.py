"""F10: the runner must not report success it cannot justify.

Three ways a green run meant nothing:
(a) an unclosed real file raised a ResourceWarning through `sys.unraisablehook`
    -- which PRINTS and continues -- so the module reported one passing test and
    zero errors;
(b) a selected module that ran zero tests was accepted by the parent verdict;
(c) `No usable FFmpeg binary` and `No usable PySide6 installation` matched the
    broad `no usable` environment pattern FIRST, so `--require` let a required
    dependency go missing.

Every case drives the REAL runner as a subprocess, serial and parallel, and
checks the process exit code -- a warning printed on stdout is not a failure.
Classification-level coverage lives in test_run_suite_skip_policy.py.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k run_suite_false_success
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import FFmWiz
import run_suite

PROJECT_ROOT = Path(FFmWiz.__file__).resolve().parent
TESTS_DIR = PROJECT_ROOT / "tests"
RUNNER = TESTS_DIR / "run_suite.py"

LEAKING_FILE = '''import tempfile, unittest


class Probe(unittest.TestCase):
    def test_leaks_a_real_file(self):
        path = tempfile.mkstemp(suffix=".probe")[1]
        open(path, "w")
        self.assertTrue(True)
'''

LEAKING_PIPE = '''import subprocess, sys, unittest


class Probe(unittest.TestCase):
    def test_leaks_a_pipe(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.PIPE)
        proc.wait()
        self.assertTrue(True)
'''

FAILING = '''import unittest


class Probe(unittest.TestCase):
    def test_fails(self):
        self.assertEqual(1, 2)
'''

UNEXPECTED_SUCCESS = '''import unittest


class Probe(unittest.TestCase):
    @unittest.expectedFailure
    def test_passes_after_all(self):
        self.assertTrue(True)
'''

NO_TESTS = '''class NotATestCase:
    pass
'''

SKIPS_FOR_FFMPEG = '''import unittest


class Probe(unittest.TestCase):
    def test_needs_ffmpeg(self):
        raise unittest.SkipTest("No usable FFmpeg binary")
'''

SKIPS_FOR_QT = '''import unittest


class Probe(unittest.TestCase):
    def test_needs_qt(self):
        raise unittest.SkipTest("No usable PySide6 installation")
'''

SKIPS_FOR_HARDWARE = '''import unittest


class Probe(unittest.TestCase):
    def test_needs_a_gpu(self):
        raise unittest.SkipTest("no usable NVIDIA CUDA hardware on this runner")
'''

PASSING = '''import unittest


class Probe(unittest.TestCase):
    def test_passes(self):
        self.assertTrue(True)
'''


class TheRunnerReportsWhatActuallyHappened(unittest.TestCase):
    """Every case runs the real CLI; only its exit code counts as a verdict."""

    def setUp(self) -> None:
        self.written: list[Path] = []
        self.addCleanup(self.remove_probes)

    def remove_probes(self) -> None:
        for path in self.written:
            path.unlink(missing_ok=True)
            for cached in (TESTS_DIR / "__pycache__").glob(path.stem + ".*"):
                cached.unlink(missing_ok=True)

    def probe(self, source: str) -> str:
        """Write a throwaway test module the runner will discover by name."""
        name = f"test_probe_{uuid.uuid4().hex[:10]}"
        path = TESTS_DIR / f"{name}.py"
        path.write_text(source, encoding="utf-8")
        self.written.append(path)
        return name

    def run_runner(self, *arguments: str) -> subprocess.CompletedProcess:
        environment = dict(os.environ, PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, str(RUNNER), *arguments],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=str(PROJECT_ROOT),
                              env=environment, timeout=300)

    def assert_fails(self, result: subprocess.CompletedProcess, expected: str) -> None:
        self.assertEqual(result.returncode, 1,
                         f"the runner reported success\n{result.stdout}\n{result.stderr}")
        self.assertIn(expected, result.stdout + result.stderr)

    # --- (a) unraisable exceptions -----------------------------------------
    def test_a_leaked_file_fails_the_run_serially(self):
        name = self.probe(LEAKING_FILE)
        self.assert_fails(self.run_runner("-j", "1", "-k", name), "unraisable")

    def test_a_leaked_file_fails_the_run_in_parallel(self):
        name = self.probe(LEAKING_FILE)
        other = self.probe(PASSING)
        self.assert_fails(self.run_runner("-j", "2", "-k", name, "-k", other), "unraisable")

    def test_a_leaked_pipe_fails_the_run(self):
        name = self.probe(LEAKING_PIPE)
        self.assert_fails(self.run_runner("-j", "1", "-k", name), "unraisable")

    def test_the_leak_is_attributed_to_its_module(self):
        name = self.probe(LEAKING_FILE)
        result = self.run_runner("-j", "1", "-k", name)
        self.assertIn(name, result.stdout)

    # --- ordinary verdicts still work --------------------------------------
    def test_a_failing_assertion_still_fails(self):
        name = self.probe(FAILING)
        self.assert_fails(self.run_runner("-j", "1", "-k", name), "FAILED")

    def test_an_unexpected_success_still_fails(self):
        name = self.probe(UNEXPECTED_SUCCESS)
        self.assert_fails(self.run_runner("-j", "1", "-k", name), "UNEXPECTED SUCCESS")

    def test_a_healthy_module_still_passes(self):
        name = self.probe(PASSING)
        result = self.run_runner("-j", "1", "-k", name)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # --- (b) empty selections ----------------------------------------------
    def test_a_selected_module_with_no_tests_fails(self):
        name = self.probe(NO_TESTS)
        self.assert_fails(self.run_runner("-j", "1", "-k", name), "ran no tests")

    def test_an_empty_selection_fails(self):
        self.assert_fails(self.run_runner("-j", "1", "-k", "no_such_module_anywhere"),
                          "no test modules matched")

    def test_an_intentionally_skipped_module_is_not_penalised(self):
        name = self.probe(SKIPS_FOR_HARDWARE)
        result = self.run_runner("-j", "1", "-k", name)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # --- (c) skip attribution ----------------------------------------------
    def test_a_missing_ffmpeg_skip_is_not_environmental(self):
        name = self.probe(SKIPS_FOR_FFMPEG)
        self.assert_fails(self.run_runner("-j", "1", "-k", name, "--require", "ffmpeg"),
                          "suite shrank")

    def test_a_missing_qt_skip_is_not_environmental(self):
        name = self.probe(SKIPS_FOR_QT)
        self.assert_fails(self.run_runner("-j", "1", "-k", name, "--require", "pyside6"),
                          "suite shrank")

    def test_a_genuine_hardware_skip_is_still_environmental(self):
        name = self.probe(SKIPS_FOR_HARDWARE)
        result = self.run_runner("-j", "1", "-k", name, "--require", "ffmpeg")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_classifier_no_longer_exempts_the_phrase_no_usable(self):
        self.assertEqual(run_suite.classify_skip("No usable FFmpeg binary"), "ffmpeg")
        self.assertEqual(run_suite.classify_skip("No usable PySide6 installation"), "pyside6")
        self.assertEqual(run_suite.classify_skip("no usable NVIDIA CUDA hardware"), "")

    # --- preflight ----------------------------------------------------------
    def test_a_required_capability_present_on_this_machine_probes_clean(self):
        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            self.skipTest("ffmpeg is not available to probe")
        self.assertEqual(run_suite.probe_capability("ffmpeg"), "")

    def test_an_absent_required_capability_fails_before_any_test_runs(self):
        """A PATH without ffmpeg makes it genuinely missing; the run must not start.

        The interpreter's own directories stay on PATH -- Windows loads
        python3xx.dll from there -- so this removes the tool, not the runtime.
        """
        system_root = os.environ.get("SystemRoot") or "C:" + os.sep + "Windows"
        keep = [str(Path(sys.executable).parent), sys.prefix, sys.base_prefix,
                str(Path(sys.base_prefix) / "Scripts"),
                system_root, str(Path(system_root) / "System32")]
        minimal = os.pathsep.join(dict.fromkeys(keep))
        if shutil.which("ffmpeg", path=minimal) or shutil.which("ffprobe", path=minimal):
            self.skipTest("ffmpeg lives beside the interpreter, so it cannot be hidden")
        name = self.probe(PASSING)
        environment = dict(os.environ, PYTHONIOENCODING="utf-8", PATH=minimal)
        result = subprocess.run(
            [sys.executable, str(RUNNER), "-j", "1", "-k", name, "--require", "ffmpeg"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(PROJECT_ROOT), env=environment, timeout=300)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("a capability this job requires is not installed", result.stdout)
        self.assertNotIn("Ran 1 tests", result.stdout,
                         "the preflight ran the suite before refusing")

    def test_an_unknown_capability_name_is_refused(self):
        name = self.probe(PASSING)
        result = self.run_runner("-j", "1", "-k", name, "--require", "unknown_capability")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown --require capability", result.stdout)

    def test_every_known_capability_probes_without_raising(self):
        for capability in run_suite.CAPABILITY_PATTERNS:
            with self.subTest(capability=capability):
                self.assertIsInstance(run_suite.probe_capability(capability), str)


if __name__ == "__main__":
    unittest.main()
