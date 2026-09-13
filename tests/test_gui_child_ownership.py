"""F08: no editor child process or owned temp file may outlive the window.

The reverse-preview and waveform workers each hold an FFmpeg child. Both used
to register that child only after `Popen` returned, so a cancel or a window
close landing inside that gap saw nothing, reported clean, deleted the proxy
directory -- and the process it never saw kept running and registered itself
into an owner that had already shut down. The waveform worker had no tracked
process at all: its child, its worker thread and its PCM file all survived
cleanup.

Every test here runs REAL child processes. The races are forced with barriers
rather than hoped for, and the Qt cases build the real Bridge offscreen, so no
window is ever shown.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k gui_child_ownership
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz.gui.gui_child_owner import ChildProcessOwner

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = unittest.skipUnless(bool(FFMPEG), "ffmpeg is not available")

try:  # PySide6 is optional for the CLI, so the Qt cases self-skip without it.
    from PySide6.QtCore import QObject, Property, Signal, Slot   # noqa: F401
    _HAVE_QT = True
except Exception:                                                 # noqa: BLE001
    _HAVE_QT = False
requires_qt = unittest.skipUnless(_HAVE_QT, "No usable PySide6 installation for the Qt lifecycle tests")

# The child under test, not a wait: every test below KILLS this process and
# asserts it is gone, so the 30 s is only how long it would survive if the
# owner failed to reap it. Nothing here ever waits for it to finish.
SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]


def spawn_sleeper(**kwargs):
    return subprocess.Popen(SLEEPER, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **kwargs)


class TheOwnerClosesTheRegistrationGap(unittest.TestCase):
    """The owner itself, with real processes and forced races."""

    def setUp(self) -> None:
        self.owner = ChildProcessOwner(kill_timeout=5.0)
        self.spawned: list[subprocess.Popen] = []
        self.addCleanup(self.reap)

    def reap(self) -> None:
        for proc in self.spawned:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:                                     # noqa: BLE001
                pass

    def recording_popen(self, before=None):
        def popen(args, **kwargs):
            if before is not None:
                before()
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, **kwargs)
            self.spawned.append(proc)
            return proc
        return popen

    def assert_all_dead(self) -> None:
        for proc in self.spawned:
            self.assertIsNotNone(proc.poll(), f"pid {proc.pid} is still running")

    def test_a_close_during_the_spawn_still_kills_the_child(self):
        """The race the audit reproduced: cleanup must not finish first."""
        spawning = threading.Event()
        closed = threading.Event()

        def slow_spawn():
            spawning.set()
            time.sleep(0.4)        # the window the old code left unguarded

        def close_now():
            spawning.wait(5)
            self.owner.close(worker_timeout=5)
            closed.set()

        closer = threading.Thread(target=close_now, daemon=True)
        closer.start()
        proc = self.owner.start(SLEEPER, popen=self.recording_popen(slow_spawn))
        closer.join(10)
        self.assertTrue(closed.is_set(), "close() never returned")
        self.assertIsNotNone(proc)
        proc.wait(timeout=5)
        self.assert_all_dead()
        self.assertEqual(self.owner.active_children(), 0)

    def test_nothing_starts_after_close(self):
        self.owner.close(worker_timeout=1)
        self.assertIsNone(self.owner.start(SLEEPER, popen=self.recording_popen()))
        self.assertEqual(self.spawned, [], "a child was created after shutdown")

    def test_a_superseded_generation_never_spawns(self):
        self.owner.bump_generation(5)
        self.assertIsNone(self.owner.start(SLEEPER, generation=4,
                                           popen=self.recording_popen()))
        self.assertEqual(self.spawned, [])
        live = self.owner.start(SLEEPER, generation=5, popen=self.recording_popen())
        self.assertIsNotNone(live)

    def test_cancel_spares_the_current_generation_and_kills_the_stale_one(self):
        stale = self.owner.start(SLEEPER, generation=1, popen=self.recording_popen())
        self.owner.bump_generation(2)
        current = self.owner.start(SLEEPER, generation=2, popen=self.recording_popen())
        self.owner.cancel(keep_generation=2)
        stale.wait(timeout=5)
        self.assertIsNotNone(stale.poll(), "the superseded render survived")
        self.assertIsNone(current.poll(), "the live render was killed too")
        self.owner.close(worker_timeout=1)

    def test_a_killed_child_is_reaped_not_left_a_zombie(self):
        proc = self.owner.start(SLEEPER, popen=self.recording_popen())
        self.owner.cancel()
        self.assertIsNotNone(proc.poll(), "cancel() returned before the child was reaped")

    def test_close_waits_for_the_worker_before_the_caller_deletes_files(self):
        scratch = Path(tempfile.mkdtemp(prefix="ffmwiz_f08_"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        finished = threading.Event()

        def worker():
            self.owner.worker_started()
            try:
                proc = self.owner.start(SLEEPER, popen=self.recording_popen())
                if proc is not None:
                    proc.wait()
                time.sleep(0.2)
                (scratch / "written-after-kill").write_text("x", encoding="utf-8")
                finished.set()
            finally:
                self.owner.worker_finished()

        threading.Thread(target=worker, daemon=True).start()
        time.sleep(0.3)
        self.owner.close(worker_timeout=10)
        self.assertTrue(finished.is_set(),
                        "close() returned while a worker was still writing")
        self.assert_all_dead()

    def test_close_admits_when_a_worker_did_not_finish(self):
        """`close()` must not report success while somebody still owns files.

        It returned None and the caller had nothing to check, so bridge cleanup
        went on to delete a directory a live worker was still writing into (R04).
        """
        release = threading.Event()
        self.addCleanup(release.set)

        def stuck_worker():
            self.owner.worker_started()
            try:
                release.wait(30)
            finally:
                self.owner.worker_finished()

        threading.Thread(target=stuck_worker, daemon=True).start()
        self.assertTrue(self.wait_until(lambda: self.owner.active_workers() == 1))
        self.assertFalse(self.owner.close(worker_timeout=0.5),
                         "close() claimed a clean shutdown with a worker still running")
        release.set()
        self.assertTrue(self.wait_until(lambda: self.owner.active_workers() == 0))
        self.assertTrue(self.owner.close(worker_timeout=5),
                        "close() still reports failure after the worker returned")

    def wait_until(self, predicate, timeout=10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_close_is_idempotent(self):
        self.owner.start(SLEEPER, popen=self.recording_popen())
        self.owner.close(worker_timeout=2)
        self.owner.close(worker_timeout=2)
        self.assert_all_dead()

    def test_an_already_dead_child_does_not_hang_cancel(self):
        proc = self.owner.start([sys.executable, "-c", "pass"], popen=self.recording_popen())
        proc.wait(timeout=10)
        started = time.monotonic()
        self.owner.cancel()
        self.assertLess(time.monotonic() - started, 5.0)


@requires_qt
@requires_ffmpeg
class TheEditorLeavesNothingRunning(unittest.TestCase):
    """The real Bridge, built offscreen, driving real FFmpeg children."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.media_root = Path(tempfile.mkdtemp(prefix="ffmwiz_f08media_"))
        cls.source = cls.media_root / "clip.mp4"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=12",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                        str(cls.source)], check=True, timeout=180)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.media_root, ignore_errors=True)

    def setUp(self) -> None:
        super().setUp()
        # A PRIVATE temp root. The suite runs eight workers against one %TEMP%,
        # so "no ffmwiz_qml* directory appeared" is otherwise a statement about
        # sibling tests, and that false positive is exactly what the artifact
        # guard was written for.
        self.temp_root = Path(tempfile.mkdtemp(prefix="ffmwiz_f08root_"))
        self.previous_tempdir = tempfile.tempdir
        tempfile.tempdir = str(self.temp_root)
        self.addCleanup(shutil.rmtree, self.temp_root, ignore_errors=True)
        self.addCleanup(setattr, tempfile, "tempdir", self.previous_tempdir)

    def make_bridge(self, **overrides):
        from PySide6.QtCore import QObject, Property, Signal, Slot
        from ffmwiz.gui.modern.ffmwiz_gui_qml import (build_reverse_proxy_vf,
                                                      reverse_proxy_wants_audio)
        from ffmwiz.gui.modern.gui_qml_bridge import build_bridge

        self.replies: list[dict] = []
        Bridge = build_bridge(QObject, Slot, Signal, Property, self.replies.append,
                              lambda level, message: None,
                              reverse_proxy_wants_audio, build_reverse_proxy_vf)
        request = {"input_path": str(self.source), "ffmpeg": FFMPEG,
                   "duration": 12.0, "has_audio": True}
        request.update(overrides)
        bridge = Bridge(self.app, request)
        # Reverse and waveform are separate lanes now, and several tests below
        # turn on telling their children apart.
        self.started: list[subprocess.Popen] = []
        self.reverse_started: list[subprocess.Popen] = []
        self.wave_started: list[subprocess.Popen] = []
        for owner, bucket in ((bridge._reverse_children, self.reverse_started),
                              (bridge._wave_children, self.wave_started)):
            self._record(owner, bucket)
        self.addCleanup(self.assert_nothing_survived, bridge)
        return bridge

    def _record(self, owner, bucket) -> None:
        real_start = owner.start

        def recording_start(*args, **kwargs):
            proc = real_start(*args, **kwargs)
            if proc is not None:
                bucket.append(proc)
                self.started.append(proc)
            return proc

        owner.start = recording_start

    def assert_nothing_survived(self, bridge) -> None:
        bridge.cleanup_reverse()
        for proc in self.started:
            self.assertIsNotNone(proc.poll(), f"pid {proc.pid} outlived the editor")
        for owner in (bridge._reverse_children, bridge._wave_children):
            self.assertEqual(owner.active_children(), 0)
            self.assertEqual(owner.active_workers(), 0)
        self.assertFalse(os.path.isdir(bridge._rev_temp.name),
                         "the owned proxy directory survived cleanup")

    def wait_for(self, predicate, timeout=40.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_the_public_cancel_slot_stops_the_current_render(self):
        """Exactly what QML calls -- no private generation mutation.

        The previous version of this test bumped `_children` by hand before
        cancelling, which changed the precondition and hid the defect: the slot
        called `cancel(keep_generation=current)` and therefore SPARED the very
        process QML had just asked it to stop (R04).

        The render is deliberately one this machine cannot finish quickly, and
        the stop is required to be FAST. An earlier version asked for an 8 s
        chunk and then waited 20 s: the child finished on its own and the test
        passed whether or not cancelling did anything.
        """
        bridge = self.make_bridge()
        bridge.renderReverse('{"gen": 1, "ss": 0, "dur": 11, "width": 1920}')
        self.assertTrue(self.wait_for(lambda: self.reverse_started),
                        "the render never started a child")
        proc = self.reverse_started[0]
        self.assertIsNone(proc.poll(), "the render finished before it could be cancelled")
        started = time.monotonic()
        bridge.cancelReverse()        # the public slot, as QML calls it
        elapsed = time.monotonic() - started
        self.assertIsNotNone(proc.poll(), "the cancelled render is still running")
        self.assertLess(elapsed, 15.0,
                        "cancelReverse() returned only once the render ended by itself")

    def test_cancelling_reverse_does_not_stop_the_waveform(self):
        """Two lanes: a reverse cancel is not a waveform cancel."""
        bridge = self.make_bridge()
        bridge.startWaveform()
        self.assertTrue(self.wait_for(lambda: self.wave_started),
                        "the waveform decode never started"),
        bridge.cancelReverse()
        self.assertIn(self.wave_started[0].poll(), (None, 0),
                      "cancelling reverse terminated the waveform decode")

    def test_starting_a_reverse_render_does_not_kill_a_running_decode(self):
        """Superseding reverse generations must not touch the other lane."""
        bridge = self.make_bridge()
        bridge.startWaveform()
        self.assertTrue(self.wait_for(lambda: self.wave_started))
        bridge.renderReverse('{"gen": 7, "ss": 0, "dur": 8, "width": 320}')
        self.assertTrue(self.wait_for(lambda: self.reverse_started))
        # Still running, or finished cleanly -- both mean "not killed". On a
        # fast runner the decode of a short clip completes before this line, so
        # demanding `poll() is None` made the test fail for the machine's speed
        # rather than for the behaviour it names.
        self.assertIn(self.wave_started[0].poll(), (None, 0),
                      "starting a reverse render terminated the waveform child")

    def test_window_shutdown_stops_both_lanes(self):
        bridge = self.make_bridge()
        bridge.startWaveform()
        bridge.renderReverse('{"gen": 1, "ss": 0, "dur": 10, "width": 640}')
        self.assertTrue(self.wait_for(lambda: self.wave_started and self.reverse_started))
        bridge.cleanup_reverse()
        for proc in self.started:
            self.assertIsNotNone(proc.poll(), f"pid {proc.pid} survived shutdown")

    def partial_then_fail_shim(self) -> Path:
        """A stand-in decoder that writes real PCM and THEN exits nonzero.

        This is the audit's fault injection. Pointing the bridge at a missing
        binary is a different failure -- it dies at spawn and never reaches the
        nonzero-exit path -- so it cannot show whether a truncated decode gets
        promoted into the cache.
        """
        if os.name != "nt":
            self.skipTest("the .cmd shim used for fault injection is Windows-only")
        shim = self.media_root / "partial_then_fail.cmd"
        shim.write_text(
            "@echo off\r\n"
            f'"{sys.executable}" -c "import sys,array;'
            'open(sys.argv[-1],\'wb\').write(array.array(\'h\',[1000]*4000).tobytes())" %*\r\n'
            "exit /b 1\r\n",
            encoding="utf-8")
        return shim

    def test_a_partial_decode_that_exits_nonzero_never_becomes_the_cache(self):
        """A nonzero decoder exit must not publish the PCM it managed to write."""
        shim = self.partial_then_fail_shim()
        bridge = self.make_bridge(ffmpeg=str(shim))
        before_key, before_pcm = bridge._wave_key, bridge._pcm
        bridge.startWaveform()
        self.assertTrue(self.wait_for(
            lambda: bridge._wave_thread is not None and not bridge._wave_thread.is_alive(),
            timeout=60), "the decode worker never finished")
        # The shim really did write samples, so this is the promotion case.
        self.assertTrue(self.wave_started, "no decode child was started")
        self.assertEqual(self.wave_started[0].returncode, 1)
        self.assertEqual(bridge._wave_key, before_key,
                         "a failed decode was promoted into the cache key")
        self.assertIs(bridge._pcm, before_pcm,
                      "a failed decode published its partial PCM")

    def test_a_decoder_that_cannot_even_start_is_reported_not_cached(self):
        bridge = self.make_bridge(ffmpeg=str(self.media_root / "no-such-ffmpeg.exe"))
        before_key, before_pcm = bridge._wave_key, bridge._pcm
        bridge.startWaveform()
        self.assertTrue(self.wait_for(
            lambda: bridge._wave_thread is not None and not bridge._wave_thread.is_alive(),
            timeout=30))
        self.assertEqual(bridge._wave_key, before_key)
        self.assertIs(bridge._pcm, before_pcm)

    def test_a_failed_communicate_does_not_release_a_live_child(self):
        """A03: the `finally: finish(proc)` released a child that never exited.

        `communicate()` raising means nothing is known about the process. The
        worker used to deregister it anyway, so cleanup reported success and
        deleted the directory a live FFmpeg was still writing into.
        """
        bridge = self.make_bridge()
        owner = bridge._reverse_children
        real_start = owner.start

        def start_with_broken_communicate(*args, **kwargs):
            proc = real_start(*args, **kwargs)
            if proc is not None:
                # Only the communication fails; the child is real and alive.
                # The pipes are closed first, because the real communicate()
                # is what would have closed them -- leaving them open would be
                # a leak this test introduced, not one it is testing.
                def fail(*_args, **_kwargs):
                    for pipe in (proc.stdout, proc.stderr, proc.stdin):
                        if pipe is not None and not pipe.closed:
                            pipe.close()
                    raise OSError("the pipe went away")

                proc.communicate = fail
            return proc

        owner.start = start_with_broken_communicate
        self.addCleanup(setattr, owner, "start", real_start)

        bridge.renderReverse('{"gen": 1, "ss": 0, "dur": 11, "width": 1920}')
        self.assertTrue(self.wait_for(lambda: self.reverse_started, timeout=20))
        proc = self.reverse_started[0]
        # The worker has raised by now; what matters is that the child was
        # stopped and reaped rather than silently disowned.
        self.assertTrue(self.wait_for(lambda: proc.poll() is not None, timeout=20),
                        "the child was left running after its communicate failed")
        self.assertTrue(bridge.cleanup_reverse())

    def test_repeated_cleanup_is_idempotent(self):
        bridge = self.make_bridge()
        bridge.renderReverse('{"gen": 1, "ss": 0, "dur": 4, "width": 320}')
        self.wait_for(lambda: self.reverse_started, timeout=15)
        self.assertTrue(bridge.cleanup_reverse())
        self.assertTrue(bridge.cleanup_reverse())

    def test_closing_during_a_render_leaves_nothing_behind(self):
        bridge = self.make_bridge()
        bridge.renderReverse('{"gen": 1, "ss": 0, "dur": 10, "width": 640}')
        self.assertTrue(self.wait_for(lambda: self.started))
        bridge.cleanup_reverse()        # the cleanup assertions run again teardown
        self.assertIsNotNone(self.started[0].poll())

    def test_closing_during_a_waveform_decode_kills_it_and_its_pcm(self):
        bridge = self.make_bridge()
        before = set(Path(tempfile.gettempdir()).glob("ffmwiz_qmlwave_*"))
        bridge.startWaveform()
        self.assertTrue(self.wait_for(lambda: self.started),
                        "the waveform decode never registered a child")
        bridge.cleanup_reverse()
        self.assertIsNotNone(self.started[0].poll(), "the waveform child survived")
        leftover = set(Path(tempfile.gettempdir()).glob("ffmwiz_qmlwave_*")) - before
        self.assertEqual(leftover, set(), "the waveform PCM file was left behind")

    def test_rapid_toggles_leave_only_the_newest_render_alive(self):
        bridge = self.make_bridge()
        for generation in range(1, 6):
            bridge.renderReverse('{"gen": %d, "ss": %d, "dur": 6, "width": 320}'
                                 % (generation, generation))
            time.sleep(0.15)
            self.app.processEvents()
        self.assertTrue(self.wait_for(lambda: len(self.started) >= 2))
        for proc in self.started[:-1]:
            proc.wait(timeout=20)
            self.assertIsNotNone(proc.poll(), "a superseded render is still running")

    def test_a_launch_failure_is_reported_and_owns_nothing(self):
        bridge = self.make_bridge(ffmpeg=str(self.media_root / "no-such-ffmpeg.exe"))
        published: list[tuple] = []
        bridge.reverseReady.connect(lambda gen, path: published.append((gen, path)))
        bridge.renderReverse('{"gen": 1, "ss": 0, "dur": 1, "width": 160}')
        self.assertTrue(self.wait_for(lambda: published, timeout=20),
                        "a failed launch never reported back")
        self.assertEqual(published[0][1], "", "a failed launch published an output path")
        self.assertEqual(bridge._reverse_children.active_children(), 0)

    def test_a_result_is_not_published_after_shutdown(self):
        bridge = self.make_bridge()
        published: list[tuple] = []
        bridge.reverseReady.connect(lambda gen, path: published.append((gen, path)))
        bridge.renderReverse('{"gen": 1, "ss": 0, "dur": 10, "width": 640}')
        self.assertTrue(self.wait_for(lambda: self.started))
        bridge.cleanup_reverse()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.05)
        self.assertEqual(published, [], "a stale result was published after shutdown")

    def test_repeated_open_and_close_accumulates_nothing(self):
        temp_root = Path(tempfile.gettempdir())
        before = set(temp_root.glob("ffmwiz_qmlrev_*"))
        for _ in range(3):
            bridge = self.make_bridge()
            bridge.renderReverse('{"gen": 1, "ss": 0, "dur": 4, "width": 320}')
            self.wait_for(lambda: self.started, timeout=10)
            bridge.cleanup_reverse()
        self.assertEqual(set(temp_root.glob("ffmwiz_qmlrev_*")) - before, set(),
                         "editor sessions accumulated proxy directories")


@requires_qt
class TheEditorProcessCleansUpAfterItself(unittest.TestCase):
    """The whole driver, as FFmWiz actually launches it."""

    def test_a_full_run_leaves_no_owned_temp_artifacts(self):
        root = Path(tempfile.mkdtemp(prefix="ffmwiz_f08proc_"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        request = root / "request.json"
        reply = root / "reply.json"
        request.write_text('{"mode": "unified", "duration": 1.0, "input_path": "x.mkv"}',
                           encoding="utf-8")
        # The child gets a temp directory of its OWN, so what is left in it was
        # left by this editor run and by nothing else on a machine running
        # eight test workers.
        child_temp = root / "temp"
        child_temp.mkdir()
        environment = dict(os.environ,
                           FFMWIZ_QML_SELFTEST="1", QT_QPA_PLATFORM="offscreen",
                           TEMP=str(child_temp), TMP=str(child_temp),
                           TMPDIR=str(child_temp))
        result = subprocess.run(
            [sys.executable, "-m", "ffmwiz.gui.modern.ffmwiz_gui_qml",
             "--request", str(request), "--reply", str(reply)],
            capture_output=True, env=environment, timeout=180,
            cwd=str(Path(FFmWiz.__file__).resolve().parent))
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertIn('"status": "ok"', reply.read_text(encoding="utf-8"))
        self.assertEqual(list(child_temp.glob("ffmwiz_qml*")), [],
                         "the editor process left owned temp artifacts behind")


if __name__ == "__main__":
    unittest.main()
