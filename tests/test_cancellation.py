"""Regression: cancelling an encode must be fast, graceful and complete (D10).

Three separate defects, all in the same teardown path:

  * `except Exception` does NOT catch KeyboardInterrupt, so Ctrl+C fell through
    to a bare `finally` that called reap_subprocess with its default 60 s wait.
    The wizard looked frozen for a full minute after the user had cancelled.
  * `process.terminate()` on Windows is TerminateProcess -- an instant kill, so
    FFmpeg never ran av_write_trailer and the partial output had no index.
  * `process.kill()` reaps ffmpeg.exe alone; anything it spawned kept the output
    file locked and the pipes open.

The stop ladder is now graceful-first: signal -> bounded wait -> terminate ->
bounded wait -> kill the whole tree, and `cancelled=True` skips the initial wait
entirely.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import FFmWiz

from ffmwiz import runtime

# A child that ignores everything short of a real kill.
SPIN = [sys.executable, "-c", "import time\nwhile True: time.sleep(0.05)"]


def _spawn():
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(SPIN, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, **kwargs)


class ReapIsBounded(unittest.TestCase):
    def test_a_wedged_child_is_stopped_within_the_timeout(self):
        process = _spawn()
        started = time.perf_counter()
        runtime.reap_subprocess(process, wait_timeout=1.0, label="spin")
        elapsed = time.perf_counter() - started
        self.assertIsNotNone(process.poll(), "the child survived reaping")
        # 1 s wait + at most three 8 s ladder rungs; generous but bounded.
        self.assertLess(elapsed, 30.0, f"reaping took {elapsed:.1f}s")

    def test_cancelled_skips_the_wait_entirely(self):
        # The whole point: a user who pressed Ctrl+C must not wait out a timeout
        # that exists for a DIFFERENT situation.
        process = _spawn()
        started = time.perf_counter()
        runtime.reap_subprocess(process, wait_timeout=60.0, label="spin",
                                cancelled=True)
        elapsed = time.perf_counter() - started
        self.assertIsNotNone(process.poll())
        self.assertLess(elapsed, 30.0,
                        f"cancelled reap waited {elapsed:.1f}s -- it must not "
                        "sit in the 60 s wait")

    def test_both_pipes_are_closed(self):
        process = _spawn()
        runtime.reap_subprocess(process, wait_timeout=1.0, label="spin")
        for name in ("stdout", "stderr"):
            pipe = getattr(process, name)
            with self.subTest(name):
                self.assertTrue(pipe is None or pipe.closed)

    def test_reaping_an_already_dead_child_is_harmless(self):
        process = _spawn()
        process.kill()
        process.wait(timeout=10)
        runtime.reap_subprocess(process, wait_timeout=1.0, label="spin")

    def test_none_is_accepted(self):
        runtime.reap_subprocess(None)


class StopLadderIsGracefulFirst(unittest.TestCase):
    """A cancelled encode should still leave a playable file where possible."""

    def test_a_signal_is_attempted_before_terminate(self):
        calls = []
        real_signal = runtime._signal_graceful_stop

        def spy(process, label, own_group):
            calls.append("signal")
            return real_signal(process, label, own_group)

        runtime._signal_graceful_stop = spy
        try:
            process = _spawn()
            runtime.reap_subprocess(process, label="spin", cancelled=True,
                                    own_process_group=True)
        finally:
            runtime._signal_graceful_stop = real_signal
        self.assertEqual(["signal"], calls,
                         "terminate must not be the first thing a cancel does")

    def test_the_tree_killer_is_used_as_the_last_resort(self):
        source = (FFmWiz.Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "runtime.py").read_text(encoding="utf-8")
        ladder = source.split("def reap_subprocess")[1].split("\ndef ")[0]
        signal_at = ladder.index("_signal_graceful_stop")
        terminate_at = ladder.index("process.terminate()")
        kill_at = ladder.index("_kill_process_tree")
        self.assertLess(signal_at, terminate_at, "signal must precede terminate")
        self.assertLess(terminate_at, kill_at, "terminate must precede the tree kill")

    @unittest.skipUnless(sys.platform == "win32", "Windows process-group behaviour")
    def test_the_child_is_started_in_its_own_process_group(self):
        # CTRL_BREAK_EVENT only reaches a child in its own group -- and without
        # the flag it would also hit FFmWiz's own console.
        # Scope this to the Popen CALL: the name also appears in a docstring, so
        # a whole-file substring search passes even when the flag is gone.
        source = (FFmWiz.Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "runtime.py").read_text(encoding="utf-8")
        body = source.split("def run_ffmpeg_with_progress")[1]
        popen_call = body.split("subprocess.Popen(")[1].split(")\n")[0]
        self.assertIn("creationflags", popen_call,
                      "the ffmpeg child must be started in its own process group")
        self.assertIn("CREATE_NEW_PROCESS_GROUP", popen_call)

    @unittest.skipUnless(sys.platform == "win32", "taskkill is Windows-only")
    def test_the_tree_kill_uses_taskkill_with_the_tree_flag(self):
        source = (FFmWiz.Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "runtime.py").read_text(encoding="utf-8")
        killer = source.split("def _kill_process_tree")[1].split("\ndef ")[0]
        self.assertIn("taskkill", killer)
        self.assertIn('"/T"', killer, "without /T only ffmpeg.exe itself dies")


class SignalIsScopedToItsOwnGroup(unittest.TestCase):
    """CTRL_BREAK_EVENT goes to a whole process GROUP.

    Sending it to a child that shares our console group takes down FFmWiz
    itself. That is not theoretical: services.py reaps ffprobe/loudnorm children
    created WITHOUT the flag, and the first version of this fix signalled them
    unconditionally -- the test suite died mid-run with exit 130 (SIGINT).
    """

    @unittest.skipUnless(sys.platform == "win32", "Windows signal scoping")
    def test_no_signal_is_sent_to_a_shared_group_child(self):
        process = subprocess.Popen(SPIN, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            self.assertFalse(
                runtime._signal_graceful_stop(process, "shared", False),
                "signalling a child in OUR console group would kill FFmWiz too")
        finally:
            runtime.reap_subprocess(process, wait_timeout=1.0, label="shared")

    def test_the_default_is_the_safe_one(self):
        import inspect
        default = inspect.signature(
            runtime.reap_subprocess).parameters["own_process_group"].default
        self.assertIs(False, default,
                      "a caller that does not opt in must never be signalled")

    def test_only_the_progress_runner_opts_in(self):
        source = (FFmWiz.Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "runtime.py").read_text(encoding="utf-8")
        self.assertEqual(1, source.count("own_process_group=("),
                         "exactly one caller creates its own group and may opt in")


class KeyboardInterruptIsHandled(unittest.TestCase):
    def test_the_render_loop_catches_it_explicitly(self):
        # `except Exception` does not catch KeyboardInterrupt; without its own
        # clause the cancel silently took the slow path.
        source = (FFmWiz.Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "runtime.py").read_text(encoding="utf-8")
        body = source.split("def run_ffmpeg_with_progress")[1]
        self.assertIn("except KeyboardInterrupt:", body)

    def test_it_reaps_as_cancelled_and_re_raises(self):
        source = (FFmWiz.Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "runtime.py").read_text(encoding="utf-8")
        body = source.split("def run_ffmpeg_with_progress")[1]
        clause = body.split("except KeyboardInterrupt:")[1].split("except Exception:")[0]
        self.assertIn("cancelled = True", clause)
        self.assertIn("raise", clause,
                      "the interrupt must still reach the top-level handler")
        self.assertIn("cancelled=cancelled", body,
                      "the finally must pass the flag through to reap_subprocess")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "ffmpeg/ffprobe not on PATH")
@unittest.skipUnless(sys.platform == "win32", "CTRL_BREAK_EVENT is Windows-specific")
class CancelledEncodeStaysPlayable(unittest.TestCase):
    """The whole reason the ladder is graceful-first.

    Measured against a real 60 s libx264 encode stopped after 3 s:
        terminate()       -> 0 KB, ffprobe "Invalid data"  (everything lost)
        CTRL_BREAK_EVENT  -> 128 KB, 22.80 s, 684 frames   (usable)
    """

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_cancelplay_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_a_graceful_stop_leaves_a_file_ffprobe_can_read(self):
        out = self._tmp / "partial.mp4"
        process = subprocess.Popen(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=60",
             "-c:v", "libx264", "-preset", "veryslow", "-crf", "20", str(out)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        time.sleep(3.0)
        if process.poll() is not None:
            runtime.reap_subprocess(process, wait_timeout=1.0, label="probe")
            self.skipTest("the encode finished before the cancel -- inconclusive")

        # This is the path a Ctrl+C now takes.
        runtime.reap_subprocess(process, label="probe", cancelled=True,
                                own_process_group=True)

        self.assertIsNotNone(process.poll(), "FFmpeg survived the cancel")
        self.assertTrue(out.exists() and out.stat().st_size > 0,
                        "a cancelled encode wrote nothing at all")
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(out)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0,
                         "the partial output has no trailer and cannot be read: "
                         + (result.stderr or "").strip()[-200:])
        duration = float(json.loads(result.stdout)["format"]["duration"])
        self.assertGreater(duration, 0.5,
                           "the trailer was written but the file holds no video")


if __name__ == "__main__":
    unittest.main()
