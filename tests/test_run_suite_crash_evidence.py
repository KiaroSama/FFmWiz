"""A06: the verdict must be complete, and the evidence must survive a crash.

Three ways the runner still reported something that was not true. Each runs the
REAL runner as a fresh subprocess and asserts the exit code AND the JSON:

* a non-daemon thread a test created raises AFTER the test returns. The thread
  hook was restored the moment the module's tests finished, so the late
  exception reached the default hook, got printed, and the run exited 0 with
  `verdict: ok`. A joined thread was already caught; an unjoined one was not.
* `--require powershell` was satisfied by any file of that name on PATH. The
  real-execution probe added for FFmpeg was never applied here, so a shim that
  exits 2 proved "PowerShell is available" and every PowerShell test that then
  skipped was excused.
* a worker that dies abruptly took every COMPLETED module's record with it:
  results were only collected after `list(pool.map(...))` returned, so the one
  artifact a red CI job is read from came back `modules: []`.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k run_suite_crash_evidence
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import FFmWiz

PROJECT_ROOT = Path(FFmWiz.__file__).resolve().parent
TESTS_DIR = PROJECT_ROOT / "tests"
RUNNER = TESTS_DIR / "run_suite.py"

# A thread the test creates, does NOT join, and which raises after the test
# method has already returned green.
LATE_THREAD = '''
import threading
import time
import unittest


def explode():
    time.sleep(0.2)
    raise RuntimeError("the late thread failed")


class Probe(unittest.TestCase):
    def test_starts_a_thread_and_does_not_wait(self):
        threading.Thread(target=explode, name="late-worker").start()
        self.assertTrue(True)
'''

# A module that passes, to sit alongside the one that kills its worker.
PASSES = '''
import unittest


class Probe(unittest.TestCase):
    def test_passes(self):
        self.assertTrue(True)
'''

# os._exit skips every cleanup path, which is exactly how a worker dies for
# real: a segfault in a C extension, or an OOM kill.
KILLS_ITS_WORKER = '''
import os
import unittest


class Probe(unittest.TestCase):
    def test_takes_the_worker_down_with_it(self):
        os._exit(3)
'''


class TheVerdictAccountsForEverything(unittest.TestCase):
    def setUp(self) -> None:
        self.written: list[Path] = []
        self.tag = uuid.uuid4().hex[:8]
        self.addCleanup(self.remove_probes)
        self.json_path = Path(tempfile.mkdtemp(prefix="ffmwiz_a06_")) / "results.json"
        self.addCleanup(lambda: self.json_path.parent.exists()
                        and __import__("shutil").rmtree(self.json_path.parent,
                                                        ignore_errors=True))

    def remove_probes(self) -> None:
        for path in self.written:
            path.unlink(missing_ok=True)
            for cached in (TESTS_DIR / "__pycache__").glob(path.stem + ".*"):
                cached.unlink(missing_ok=True)

    def probe(self, source: str) -> str:
        # Tagged per TEST, not per module: `-k probe_` would also select the
        # probes a neighbouring test happens to have on disk at that moment.
        name = f"test_probe_{self.tag}_{uuid.uuid4().hex[:6]}"
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

    def record(self) -> dict:
        self.assertTrue(self.json_path.exists(),
                        "the runner did not write its results file at all")
        return json.loads(self.json_path.read_text(encoding="utf-8"))

    # --- a late thread exception ------------------------------------------

    def test_a_late_thread_exception_fails_the_run_serially(self):
        name = self.probe(LATE_THREAD)
        result = self.run_runner("-j", "1", "--json", str(self.json_path), "-k", name[5:])
        self.assertEqual(result.returncode, 1,
                         f"a thread failed after its test and the run passed\n{result.stdout}")
        self.assertEqual("failed", self.record()["verdict"])

    def test_a_late_thread_exception_fails_the_run_in_parallel(self):
        name = self.probe(LATE_THREAD)
        result = self.run_runner("-j", "2", "--json", str(self.json_path), "-k", name[5:])
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_the_late_failure_names_its_module(self):
        name = self.probe(LATE_THREAD)
        self.run_runner("-j", "1", "--json", str(self.json_path), "-k", name[5:])
        blob = json.dumps(self.record())
        self.assertIn(name, blob)
        self.assertIn("the late thread failed", blob,
                      "the exception text was lost, so the report cannot be acted on")

    # --- a required native shell that does not work -----------------------

    @unittest.skipIf(os.name != "nt", "the .cmd shim needs a Windows shell")
    def test_an_unusable_powershell_does_not_satisfy_require(self):
        shim_dir = Path(tempfile.mkdtemp(prefix="ffmwiz_shim_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(shim_dir, ignore_errors=True))
        # First on PATH, named exactly what the probe looks for, and broken.
        (shim_dir / "powershell.cmd").write_text("@echo off\r\nexit /b 2\r\n", encoding="utf-8")
        (shim_dir / "pwsh.cmd").write_text("@echo off\r\nexit /b 2\r\n", encoding="utf-8")
        name = self.probe(PASSES)
        environment = dict(os.environ, PYTHONIOENCODING="utf-8",
                           PATH=f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                           PATHEXT=".CMD;.EXE;.BAT")
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--require", "powershell",
             "--json", str(self.json_path), "-k", name[5:]],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(PROJECT_ROOT), env=environment, timeout=300)
        self.assertEqual(result.returncode, 1,
                         "a PowerShell that cannot run satisfied --require powershell\n"
                         f"{result.stdout}\n{result.stderr}")
        self.assertIn("powershell", (result.stdout + result.stderr).lower())

    # --- a worker that dies mid-run ---------------------------------------

    def test_a_dead_worker_does_not_erase_the_completed_modules(self):
        good = [self.probe(PASSES) for _ in range(3)]
        self.probe(KILLS_ITS_WORKER)
        result = self.run_runner("-j", "2", "--json", str(self.json_path), "-k", f"probe_{self.tag}")
        self.assertEqual(result.returncode, 1, "a worker died and the run passed")
        record = self.record()
        modules = [item["module"] for item in record["modules"]]
        self.assertTrue(modules, "every completed module's record was thrown away")
        survived = [name for name in good if name in modules]
        self.assertTrue(survived,
                        f"none of the modules that finished were kept: {modules}")

    def test_a_dead_worker_is_reported_as_its_own_failure(self):
        self.probe(PASSES)
        self.probe(KILLS_ITS_WORKER)
        self.run_runner("-j", "2", "--json", str(self.json_path), "-k", f"probe_{self.tag}")
        blob = json.dumps(self.record()).lower()
        self.assertTrue("worker" in blob or "crash" in blob,
                        "the report does not say a worker died")

    # --- what must keep working -------------------------------------------

    def test_an_ordinary_parallel_run_still_passes(self):
        for _ in range(3):
            self.probe(PASSES)
        result = self.run_runner("-j", "2", "--json", str(self.json_path), "-k", f"probe_{self.tag}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = self.record()
        self.assertEqual("ok", record["verdict"])
        self.assertEqual(3, len(record["modules"]))


if __name__ == "__main__":
    unittest.main()
