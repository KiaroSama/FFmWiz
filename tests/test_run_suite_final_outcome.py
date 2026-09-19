"""A06: final process outcomes, not pre-exit JSON, decide module success."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PASS = "import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n"
FINALIZER_EXIT = PASS + """
import os
class Late:
 def __del__(self, leave=os._exit): leave(7)
keep = Late()
"""
FINALIZER_HANG = PASS + """
import time
class Late:
 def __del__(self, clock=time.monotonic):
  deadline = clock() + 120
  while clock() < deadline: pass
keep = Late()
"""
DAEMON_DESCENDANT = """
import threading, time, unittest
class T(unittest.TestCase):
 def test_ok(self):
  def outer():
   time.sleep(0.2)
   threading.Thread(target=lambda: time.sleep(120), name='late-daemon', daemon=True).start()
  threading.Thread(target=outer).start()
"""
PROCESS_LEAK = """
import os, subprocess, sys, unittest
from pathlib import Path
keepers = []
class T(unittest.TestCase):
 def test_ok(self):
  script = "import time; from pathlib import Path; end=time.monotonic()+120;\\nwhile time.monotonic()<end and not Path('stop').exists(): time.sleep(.02)"
  child = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  keepers.append(child)
  Path('pid').write_text(str(child.pid))
"""


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists():
            return stat.read_text().rsplit(")", 1)[1].split()[0] not in {"Z", "X"}
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class FinalOutcome(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffmwiz_final_outcome_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tests").mkdir()
        for source in TESTS.glob("run_suite*.py"):
            shutil.copy2(source, self.root / "tests" / source.name)
        self.addCleanup(self.stop_fixture)

    def stop_fixture(self):
        (self.root / "stop").touch()
        path = self.root / "pid"
        if path.exists():
            pid = int(path.read_text())
            deadline = time.monotonic() + 5
            while pid_alive(pid) and time.monotonic() < deadline:
                time.sleep(.02)
            if pid_alive(pid):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, timeout=10)
                else:
                    os.kill(pid, signal.SIGKILL)

    def run_case(self, source: str, jobs: int, ceiling: float = 6):
        (self.root / "tests" / "test_fixture.py").write_text(source, encoding="utf-8")
        result = subprocess.run([sys.executable, "tests/run_suite.py", "-j", str(jobs),
                                 "--module-timeout", str(ceiling), "--json", "result.json"],
                                cwd=self.root, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60)
        data = json.loads((self.root / "result.json").read_text())
        self.diagnostic = result.stdout + result.stderr + "\n" + json.dumps(data)
        return result, data

    def test_a_passing_module_still_passes(self):
        for jobs in (1, 2):
            with self.subTest(jobs=jobs):
                result, data = self.run_case(PASS, jobs)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual("ok", data["verdict"])

    def test_post_publication_exit_is_not_a_pass(self):
        for jobs in (1, 2):
            with self.subTest(jobs=jobs):
                result, data = self.run_case(FINALIZER_EXIT, jobs)
                self.assertEqual(1, result.returncode, self.diagnostic)
                self.assertEqual("failed", data["verdict"])
                self.assertEqual("crash", data["modules"][0]["status"])
                self.assertEqual(1, data["modules"][0]["tests"])
                self.assertIn("exited 7", json.dumps(data))

    def test_post_publication_timeout_is_not_a_pass(self):
        for jobs in (1, 2):
            with self.subTest(jobs=jobs):
                result, data = self.run_case(FINALIZER_HANG, jobs)
                self.assertEqual(1, result.returncode, self.diagnostic)
                self.assertEqual("timeout", data["modules"][0]["status"])
                self.assertEqual(1, data["modules"][0]["tests"])

    def test_orphaned_child_is_reported_and_stopped(self):
        for jobs in (1, 2):
            with self.subTest(jobs=jobs):
                (self.root / "stop").unlink(missing_ok=True)
                result, data = self.run_case(PROCESS_LEAK, jobs)
                self.assertEqual(1, result.returncode, self.diagnostic)
                self.assertEqual("failed", data["verdict"])
                self.assertFalse(pid_alive(int((self.root / "pid").read_text())))
                self.assertIn("live child process", json.dumps(data))

    def test_descendant_daemon_is_not_lost_between_snapshots(self):
        for jobs in (1, 2):
            with self.subTest(jobs=jobs):
                result, data = self.run_case(DAEMON_DESCENDANT, jobs)
                self.assertEqual(1, result.returncode, self.diagnostic)
                self.assertIn("late-daemon", json.dumps(data))

    @unittest.skipIf(os.name == "nt", "SIGINT delivery uses POSIX signals; Windows Job tested separately")
    def test_cancel_stops_before_executor_waits_for_the_module_timeout(self):
        source = PASS + "\nimport time\nfrom pathlib import Path\nPath('entered').touch()\ntime.sleep(120)\n"
        (self.root / "tests" / "test_fixture.py").write_text(source)
        process = subprocess.Popen([sys.executable, "tests/run_suite.py", "-j", "2",
                                    "--module-timeout", "90", "--json", "result.json"],
                                   cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        try:
            deadline = time.monotonic() + 15
            while not (self.root / "entered").exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((self.root / "entered").exists())
            process.send_signal(signal.SIGINT)
            process.communicate(timeout=12)
            self.assertNotEqual(0, process.returncode)
            data = json.loads((self.root / "result.json").read_text())
            self.assertEqual("failed", data["verdict"])
            self.assertEqual("cancelled", data["modules"][0]["status"])
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
