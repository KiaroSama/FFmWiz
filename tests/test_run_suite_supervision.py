"""A06: a module's whole life is supervised, in both -j 1 and -j 2.

Three failures reproduced with the previous runner, in a fresh process:

* `_settle_module_threads()` takes ONE snapshot of the threads a module
  started. A thread that starts ANOTHER thread escapes it: the descendant is
  not in the snapshot, so nothing waits for it, and when it raises after the
  module's hooks are restored the default hook prints it and the run exits 0.
* a stuck non-daemon thread outlives the settling ceiling. The module was
  recorded as failed, and then the child hung at interpreter shutdown --
  serial mode never returned, parallel mode never wrote its JSON at all. A run
  that never ends reports nothing.
* `os._exit(7)` inside a module killed the RUNNER in serial mode, taking the
  already-finished module and the results file with it.

The repair is one shape for both modes: every module runs in its own child
interpreter, bounded by a wall ceiling, and its record is persisted the moment
it arrives. A timeout, a crash and a module that never ran are three different
records, none of which may read as a pass.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k run_suite_supervision
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

import FFmWiz

PROJECT_ROOT = Path(FFmWiz.__file__).resolve().parent
TESTS_DIR = PROJECT_ROOT / "tests"
RUNNER = TESTS_DIR / "run_suite.py"

# A thread that starts ANOTHER thread. The grandchild is the one that raises,
# and it never appears in the single snapshot the settling pass takes.
DESCENDANT_THREAD = '''
import threading
import time
import unittest


def grandchild():
    time.sleep(0.4)
    raise RuntimeError("the descendant thread failed")


def child():
    time.sleep(0.2)
    threading.Thread(target=grandchild, name="grandchild").start()


class Probe(unittest.TestCase):
    def test_starts_a_thread_that_starts_a_thread(self):
        threading.Thread(target=child, name="child").start()
        self.assertTrue(True)
'''

# A non-daemon thread that never finishes: the module cannot end, and neither
# can the interpreter that ran it.
STUCK_THREAD = '''
import threading
import unittest

FOREVER = threading.Event()


class Probe(unittest.TestCase):
    def test_starts_work_it_never_stops(self):
        threading.Thread(target=FOREVER.wait, name="stuck-worker").start()
        self.assertTrue(True)
'''

# A daemon thread is declared not-owned-to-completion: it must NOT hold the
# module open or turn it into a timeout. It is still unaccounted work, so the
# runner names it.
STUCK_DAEMON = '''
import threading
import unittest

FOREVER = threading.Event()


class Probe(unittest.TestCase):
    def test_leaves_a_daemon_running(self):
        threading.Thread(target=FOREVER.wait, name="stuck-daemon", daemon=True).start()
        self.assertTrue(True)
'''

PASSES = '''
import unittest


class Probe(unittest.TestCase):
    def test_passes(self):
        self.assertTrue(True)
'''

# os._exit skips every cleanup path -- how a worker dies for real.
ABRUPT_EXIT = '''
import os
import unittest


class Probe(unittest.TestCase):
    def test_takes_its_process_down(self):
        os._exit(7)
'''


class RunnerProbeCase(unittest.TestCase):
    """Writes throwaway test modules and runs the real runner over them."""

    def setUp(self) -> None:
        self.written: list[Path] = []
        self.tag = uuid.uuid4().hex[:8]
        self.addCleanup(self.remove_probes)
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_a06sup_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.json_path = self.root / "results.json"

    def remove_probes(self) -> None:
        for path in self.written:
            path.unlink(missing_ok=True)
            for cached in (TESTS_DIR / "__pycache__").glob(path.stem + ".*"):
                cached.unlink(missing_ok=True)

    def probe(self, source: str, suffix: str = "") -> str:
        name = f"test_probe_{self.tag}_{suffix or uuid.uuid4().hex[:6]}"
        (TESTS_DIR / f"{name}.py").write_text(source, encoding="utf-8")
        self.written.append(TESTS_DIR / f"{name}.py")
        return name

    def start_runner(self, *arguments: str) -> subprocess.Popen:
        environment = dict(os.environ, PYTHONIOENCODING="utf-8")
        process = subprocess.Popen(
            [sys.executable, str(RUNNER), *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT),
            env=environment, **({"start_new_session": True} if os.name != "nt" else {}))
        # A runner this test KILLS never reaches its own cleanup, so the test
        # that killed it owns what is left. The runner names its scratch after
        # its pid precisely so this is possible.
        self.addCleanup(shutil.rmtree,
                        Path(tempfile.gettempdir()) / f"ffmwiz_suite_{process.pid}",
                        ignore_errors=True)
        return process

    def kill_tree(self, process: subprocess.Popen) -> None:
        """End a runner AND the module processes it started.

        `process.kill()` alone orphans them: they hold the runner's scratch
        directory open and outlive the test. The tree has to go while its root
        is still alive, which is what makes the parent link usable.
        """
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           capture_output=True, timeout=120)
        else:
            import signal
            # The nested runner owns separate module sessions. Ask it to stop
            # those before terminating its own isolated process group. Killing
            # the inherited group used to kill THIS test interpreter as well.
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()

    def run_runner(self, *arguments: str, bound: float = 300.0):
        process = self.start_runner(*arguments)
        try:
            output, _ = process.communicate(timeout=bound)
        except subprocess.TimeoutExpired:
            self.kill_tree(process)
            process.communicate()
            self.fail(f"the runner did not finish within {bound:.0f}s: "
                      f"a stuck module must be bounded, not waited on forever")
        return process.returncode, output

    def record(self) -> dict:
        self.assertTrue(self.json_path.exists(),
                        "the runner did not write its results file at all")
        return json.loads(self.json_path.read_text(encoding="utf-8"))

    def module_record(self, name: str) -> dict:
        for item in self.record()["modules"]:
            if item["module"] == name:
                return item
        self.fail(f"{name} has no record at all; the run reported "
                  f"{[item['module'] for item in self.record()['modules']]}")


class ADescendantThreadIsStillTheModulesWork(RunnerProbeCase):
    def assert_the_descendant_is_reported(self, jobs: str) -> None:
        name = self.probe(DESCENDANT_THREAD)
        code, output = self.run_runner("-k", name, "-j", jobs,
                                       "--json", str(self.json_path))
        self.assertEqual(1, code,
                         "a thread started by a thread raised and the run still "
                         f"passed:\n{output[-1500:]}")
        self.assertEqual("failed", self.record()["verdict"])
        detail = json.dumps(self.module_record(name))
        self.assertIn("the descendant thread failed", detail,
                      "the failure was printed somewhere but never recorded")

    def test_it_fails_the_run_serially(self):
        self.assert_the_descendant_is_reported("1")

    def test_it_fails_the_run_in_parallel(self):
        self.assert_the_descendant_is_reported("2")


class AStuckModuleIsBoundedNotWaitedOn(RunnerProbeCase):
    """The ceiling is the runner's, not the stuck thread's goodwill."""

    def assert_it_is_bounded(self, jobs: str) -> None:
        name = self.probe(STUCK_THREAD)
        started = time.monotonic()
        code, output = self.run_runner("-k", name, "-j", jobs,
                                       "--module-timeout", "6",
                                       "--json", str(self.json_path),
                                       bound=120.0)
        self.assertLess(time.monotonic() - started, 100.0,
                        "the runner took far longer than the module ceiling")
        self.assertEqual(1, code, output[-1500:])
        item = self.module_record(name)
        self.assertEqual("timeout", item.get("status"),
                         "a module killed at its ceiling must be recorded as a "
                         "timeout -- not as a pass, and not as never run")
        self.assertTrue(item["errors"], "a timeout with no diagnostics at all")

    def test_a_stuck_module_is_bounded_serially(self):
        self.assert_it_is_bounded("1")

    def test_a_stuck_module_is_bounded_in_parallel(self):
        self.assert_it_is_bounded("2")

    def test_a_stuck_daemon_is_named_not_waited_on(self):
        # A daemon thread does not block interpreter shutdown, so the module
        # must NOT become a timeout. It is still work the test left running,
        # which the runner names rather than passes over.
        name = self.probe(STUCK_DAEMON)
        started = time.monotonic()
        code, output = self.run_runner("-k", name, "-j", "1",
                                       "--module-timeout", "60",
                                       "--json", str(self.json_path), bound=120.0)
        self.assertLess(time.monotonic() - started, 55.0,
                        "waiting for a daemon thread turns every such module "
                        "into a timeout; it is declared not-owned-to-completion")
        self.assertEqual(1, code, output[-1500:])
        item = self.module_record(name)
        self.assertEqual("ok", item.get("status"),
                         "the module finished on its own -- that is not a timeout")
        self.assertEqual(1, item["tests"])
        self.assertIn("stuck-daemon", json.dumps(item["errors"]),
                      "the leaked daemon was not attributed to its module")


class AnAbruptExitKeepsTheFinishedWork(RunnerProbeCase):
    """A module that kills its interpreter loses only itself."""

    def assert_both_are_recorded(self, jobs: str) -> None:
        ok = self.probe(PASSES, suffix="a_ok")
        boom = self.probe(ABRUPT_EXIT, suffix="b_boom")
        code, output = self.run_runner("-k", self.tag, "-j", jobs,
                                       "--json", str(self.json_path))
        self.assertEqual(1, code, output[-1500:])
        self.assertEqual(1, self.module_record(ok)["tests"],
                         "the module that finished before the crash lost its record")
        crashed = self.module_record(boom)
        self.assertEqual("crash", crashed.get("status"))
        self.assertIn("7", json.dumps(crashed["errors"]),
                      "the record does not say how the process died")

    def test_a_serial_abrupt_exit_does_not_kill_the_runner(self):
        self.assert_both_are_recorded("1")

    def test_a_parallel_abrupt_exit_does_not_erase_the_completed_module(self):
        self.assert_both_are_recorded("2")


class WhatFinishedIsOnDiskBeforeTheRunEnds(RunnerProbeCase):
    """Evidence survives a cancellation, so it cannot depend on a finaliser."""

    def test_a_completed_module_is_persisted_while_the_run_continues(self):
        self.probe(PASSES, suffix="a_ok")
        self.probe(STUCK_THREAD, suffix="b_stuck")
        process = self.start_runner("-k", self.tag, "-j", "1",
                                    "--module-timeout", "20",
                                    "--json", str(self.json_path))
        try:
            deadline = time.monotonic() + 90.0
            names: list[str] = []
            while time.monotonic() < deadline:
                if self.json_path.exists():
                    try:
                        names = [item["module"]
                                 for item in json.loads(
                                     self.json_path.read_text(encoding="utf-8"))["modules"]]
                    except (ValueError, OSError):
                        names = []
                    if names:
                        break
                time.sleep(0.2)
            self.assertTrue(names,
                            "nothing was persisted until the whole run ended, so a "
                            "cancelled or hung run leaves no evidence at all")
        finally:
            self.kill_tree(process)
            process.communicate(timeout=60)
        self.assertTrue(self.json_path.exists(),
                        "the results file did not survive the cancellation")


class TheEnvironmentReportIsBounded(unittest.TestCase):
    def test_it_does_not_import_the_gui_toolkit_into_this_process(self):
        sys.path.insert(0, str(TESTS_DIR))
        try:
            import run_suite
        finally:
            sys.path.pop(0)
        before = "PySide6" in sys.modules
        started = time.monotonic()
        report = run_suite.environment_report()
        self.assertLess(time.monotonic() - started, 120.0)
        self.assertIn("python", report)
        if not before:
            self.assertNotIn("PySide6", sys.modules,
                             "reporting a version must not import the toolkit -- a "
                             "broken Qt install then takes the report down with it")


if __name__ == "__main__":
    unittest.main()
