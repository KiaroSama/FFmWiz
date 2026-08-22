"""Regression tests for the subprocess lifecycle contract (D10).

Every `Popen(..., stdout=PIPE, stderr=PIPE)` in FFmWiz must end in
`runtime.reap_subprocess`, which bounds the wait, bounds the reader-thread
join, and CLOSES the pipe handles. Before this contract existed the handles
survived until garbage collection and Python reported
`ResourceWarning: unclosed file`, and an unbounded `wait()`/`join()` meant one
wedged child could hang the wizard with no way out.

These tests use a plain Python child instead of ffmpeg so they stay hermetic
and fast, and they promote ResourceWarning to an error inside the assertion
window so a regression fails loudly instead of printing a warning nobody reads.
"""
import contextlib
import io
import pathlib
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock
import warnings

import FFmWiz


def _python_progress_child(events: int = 3, exit_code: int = 0) -> list[str]:
    """A child that speaks just enough of ffmpeg's -progress protocol."""
    script = (
        "import sys,time\n"
        f"for i in range({events}):\n"
        "    sys.stdout.write('out_time_us=%d\\n' % ((i+1)*100000))\n"
        "    sys.stdout.write('progress=continue\\n')\n"
        "    sys.stdout.flush()\n"
        "sys.stdout.write('out_time_us=400000\\nprogress=end\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write('child stderr line\\n')\n"
        f"sys.exit({exit_code})\n"
    )
    return [sys.executable, "-c", script]


@contextlib.contextmanager
def _no_resource_warnings(test):
    """Fail the test if any ResourceWarning is raised inside the block."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        yield
    leaked = [w for w in caught if issubclass(w.category, ResourceWarning)]
    test.assertEqual(leaked, [], f"ResourceWarning(s) leaked: {[str(w.message) for w in leaked]}")


class SubprocessLifecycle(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def _run_progress(self, cmd):
        # run_ffmpeg_with_progress injects ffmpeg-only flags (-nostats, -progress
        # pipe:1, ...) that a plain Python child cannot parse, so the injector is
        # neutralised here. Everything under test -- the reader threads, the
        # progress loop and the reap -- is still the real code.
        buffer = io.StringIO()
        with mock.patch.object(FFmWiz.runtime, "_inject_progress_args", side_effect=lambda c: list(c)):
            with contextlib.redirect_stdout(buffer):
                return FFmWiz.run_ffmpeg_with_progress(cmd, total_duration=0.4, label="LifecycleTest")

    # ---------------- run_ffmpeg_with_progress ----------------

    def test_success_run_closes_pipes_and_leaks_no_warning(self):
        with _no_resource_warnings(self):
            rc, elapsed = self._run_progress(_python_progress_child())
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(elapsed, 0.0)

    def test_nonzero_exit_still_closes_pipes(self):
        with _no_resource_warnings(self):
            rc, _ = self._run_progress(_python_progress_child(exit_code=3))
        self.assertEqual(rc, 3)

    def test_launch_failure_returns_without_raising(self):
        with _no_resource_warnings(self):
            rc, elapsed = self._run_progress(["ffmwiz-no-such-binary-xyz"])
        self.assertEqual(rc, 1)
        self.assertEqual(elapsed, 0.0)

    # ---------------- reap_subprocess directly ----------------

    def test_reap_closes_every_pipe(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "print('hi')"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE,
            text=True,
        )
        stdout, stderr, stdin = process.stdout, process.stderr, process.stdin
        FFmWiz.reap_subprocess(process, label="unit")
        self.assertEqual(process.returncode, 0)
        for pipe in (stdout, stderr, stdin):
            self.assertTrue(pipe.closed, "reap_subprocess must close every PIPE handle")

    def test_reap_is_idempotent(self):
        process = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.PIPE)
        FFmWiz.reap_subprocess(process, label="unit")
        FFmWiz.reap_subprocess(process, label="unit")  # must not raise
        self.assertEqual(process.returncode, 0)

    def test_reap_of_none_is_a_noop(self):
        FFmWiz.reap_subprocess(None)  # must not raise

    def test_reap_kills_a_child_that_outlives_the_wait_budget(self):
        # A child that sleeps far past the budget must be terminated, and the
        # call must return promptly instead of blocking on an unbounded wait().
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        started = time.perf_counter()
        FFmWiz.reap_subprocess(process, wait_timeout=0.5, label="unit")
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 20.0, "reap_subprocess must be bounded, not open-ended")
        self.assertIsNotNone(process.returncode, "the child must actually be reaped")
        self.assertTrue(process.stdout.closed)

    # ---------------- ffprobe read budgets ----------------

    def test_ffprobe_json_passes_a_positive_timeout(self):
        """NEW-TRK6: the primary probe ran unbounded while every sibling probe
        was bounded, so one wedged ffprobe hung the wizard with no output."""
        seen = {}

        def _fake_run(args, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(args, 0, b'{"streams": []}', b"")

        with mock.patch("subprocess.run", side_effect=_fake_run):
            payload = FFmWiz.services.ffprobe_json("ffprobe", pathlib.Path("clip.mkv"))
        self.assertEqual(payload, {"streams": []})
        self.assertGreater(seen.get("timeout") or 0, 0, "ffprobe_json must bound its read")

    def test_packet_probe_returns_instead_of_hanging_on_a_wedged_child(self):
        """NEW-RT4: the reap bounded wait(), but it was only reached after the
        stdout loop hit EOF -- which a wedged ffprobe never delivers. This runs
        a REAL child that emits one line and then sleeps far past the budget."""
        child = [sys.executable, "-c",
                 "import sys,time; sys.stdout.write('0,1500\\n'); sys.stdout.flush(); time.sleep(60)"]
        real_run = subprocess.run

        def _fake_run(args, **kwargs):
            return real_run(child, **kwargs)

        buffer = io.StringIO()
        started = time.perf_counter()
        with mock.patch.object(FFmWiz.services, "_FFPROBE_PACKETS_TIMEOUT", 2.0), \
                mock.patch("subprocess.run", side_effect=_fake_run), \
                contextlib.redirect_stdout(buffer):
            sizes = FFmWiz.services.probe_packet_sizes("ffprobe", pathlib.Path("clip.mkv"))
        elapsed = time.perf_counter() - started
        self.assertEqual(sizes, {}, "a probe that never finished cannot report sizes")
        self.assertLess(elapsed, 30.0, "probe_packet_sizes must be bounded, not open-ended")

    def test_packet_probe_still_sums_a_healthy_probe(self):
        csv_out = "0,1500\n0,500\n1,64\n"
        with mock.patch("subprocess.run",
                        return_value=subprocess.CompletedProcess(["ffprobe"], 0, csv_out, "")):
            sizes = FFmWiz.services.probe_packet_sizes("ffprobe", pathlib.Path("clip.mkv"))
        self.assertEqual(sizes, {0: 2000, 1: 64})

    def test_reap_joins_reader_threads_before_closing(self):
        # The reader must never race a closed handle: reap joins threads first.
        process = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdout.write('x\\n')"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        seen = []

        def _read():
            for line in process.stdout:
                seen.append(line.strip())

        thread = threading.Thread(target=_read, daemon=True)
        thread.start()
        FFmWiz.reap_subprocess(process, (thread,), label="unit")
        self.assertFalse(thread.is_alive(), "reader thread must be joined before returning")
        self.assertEqual(seen, ["x"])


if __name__ == "__main__":
    unittest.main()
