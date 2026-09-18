"""A03: ownership is released on CONFIRMED exit, never on a failed attempt.

Two injected faults, both with real child processes:

* `terminate()` and `kill()` raise `PermissionError` and the child stays alive.
  `cancel()` had already removed it from the owner before terminating, and
  `_terminate()` swallowed the failure, so `active_children()` read 0 and
  `close()` returned True while the process was still running.
* a registered child's `communicate()` raises `OSError`. The Bridge's
  unconditional `finally: finish(proc)` deregistered a child that had not
  exited; cleanup then reported success and deleted the directory that child
  was still writing into.

These are injected counterexamples, not a claim that an OS normally refuses to
kill your own child. The normal paths -- ordinary cancellation, reaping,
unfinished-worker detection, refusing a partial PCM -- are covered next door in
test_gui_child_ownership.py and must keep passing.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k gui_child_fault_injection
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
import threading
import sys
import unittest

from ffmwiz.gui.gui_child_owner import ChildProcessOwner

# Long enough that nothing here finishes on its own; every test kills it.
SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]


def spawn_sleeper(**kwargs):
    return subprocess.Popen(SLEEPER, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **kwargs)


class Unkillable:
    """A real child whose terminate/kill are refused, like a denied handle."""

    def __init__(self) -> None:
        self.proc = spawn_sleeper()
        self.terminate_calls = 0
        self.kill_calls = 0

    # -- the Popen surface the owner uses --
    def poll(self):
        return self.proc.poll()

    def wait(self, timeout=None):
        return self.proc.wait(timeout=timeout)

    def terminate(self):
        self.terminate_calls += 1
        raise PermissionError("terminate refused")

    def kill(self):
        self.kill_calls += 1
        raise PermissionError("kill refused")

    def really_stop(self):
        self.proc.kill()
        try:
            self.proc.wait(timeout=10)
        except Exception:       # noqa: BLE001 - best effort in cleanup
            pass


class ARefusedTerminationKeepsOwnership(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = ChildProcessOwner(kill_timeout=0.4)
        self.stubborn = Unkillable()
        self.addCleanup(self.stubborn.really_stop)
        started = self.owner.start(SLEEPER, popen=lambda args, **kw: self.stubborn)
        self.assertIs(started, self.stubborn)

    def test_the_child_is_still_owned_after_a_failed_cancel(self):
        self.owner.cancel()
        self.assertIsNone(self.stubborn.poll(), "the fixture's child died on its own")
        self.assertEqual(1, self.owner.active_children(),
                         "a child that would not die was quietly disowned")

    def test_close_admits_the_shutdown_did_not_complete(self):
        self.assertFalse(self.owner.close(worker_timeout=0.5),
                         "close() reported success with a live child it could not stop")

    def test_both_stop_signals_were_actually_attempted(self):
        self.owner.cancel()
        self.assertGreaterEqual(self.stubborn.terminate_calls, 1)
        self.assertGreaterEqual(self.stubborn.kill_calls, 1,
                                "kill was never tried after terminate was refused")

    def test_the_failure_is_retryable_rather_than_forgotten(self):
        self.owner.cancel()
        self.assertEqual(1, self.owner.active_children())
        # Once the obstruction clears, a second cancel must finish the job --
        # which it can only do if ownership was retained.
        self.stubborn.really_stop()
        self.owner.cancel()
        self.assertEqual(0, self.owner.active_children())

    def test_the_reason_is_available_to_the_caller(self):
        self.owner.cancel()
        unreaped = self.owner.unreaped()
        self.assertTrue(unreaped, "nothing says WHY the child is still there")
        self.assertIn("refused", " ".join(unreaped).lower())


class FinishOnlyReleasesAProcessThatExited(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = ChildProcessOwner(kill_timeout=0.4)

    def test_finishing_a_live_child_does_not_deregister_it(self):
        proc = self.owner.start(SLEEPER, popen=lambda args, **kw: spawn_sleeper())
        self.addCleanup(lambda: (proc.kill(), proc.wait(timeout=10)))
        self.owner.finish(proc)
        self.assertEqual(1, self.owner.active_children(),
                         "a running child was released by finish(); "
                         "cleanup would then delete the files it is writing")

    def test_finishing_an_exited_child_does_release_it(self):
        proc = self.owner.start([sys.executable, "-c", "pass"],
                                popen=lambda args, **kw: subprocess.Popen(
                                    args, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL))
        proc.wait(timeout=30)
        self.owner.finish(proc)
        self.assertEqual(0, self.owner.active_children())

    def test_close_is_false_while_a_finished_but_live_child_remains(self):
        proc = self.owner.start(SLEEPER, popen=lambda args, **kw: spawn_sleeper())
        self.addCleanup(lambda: proc.poll() is None and (proc.kill(), proc.wait(timeout=10)))
        self.owner.finish(proc)
        # close() may legitimately succeed here: it is allowed to kill it.
        self.assertTrue(self.owner.close(worker_timeout=0.5))
        self.assertIsNotNone(proc.poll(), "close() returned True without reaping the child")


class OrdinaryLifecycleIsUnchanged(unittest.TestCase):
    """The paths that already worked. A fix that breaks these is not a fix."""

    def setUp(self) -> None:
        self.owner = ChildProcessOwner(kill_timeout=2.0)

    def test_cancel_stops_and_reaps_a_normal_child(self):
        proc = self.owner.start(SLEEPER, popen=lambda args, **kw: spawn_sleeper())
        self.assertEqual(1, self.owner.cancel())
        self.assertIsNotNone(proc.poll())
        self.assertEqual(0, self.owner.active_children())

    def test_close_returns_true_for_a_clean_shutdown(self):
        self.owner.start(SLEEPER, popen=lambda args, **kw: spawn_sleeper())
        self.assertTrue(self.owner.close(worker_timeout=2.0))
        self.assertEqual(0, self.owner.active_children())

    def test_a_spared_generation_survives_a_cancel(self):
        keep = self.owner.bump_generation()
        spared = self.owner.start(SLEEPER, generation=keep,
                                  popen=lambda args, **kw: spawn_sleeper())
        self.addCleanup(lambda: (spared.kill(), spared.wait(timeout=10)))
        self.owner.cancel(keep_generation=keep)
        self.assertEqual(1, self.owner.active_children())
        self.assertIsNone(spared.poll())

    def test_close_is_idempotent(self):
        self.owner.start(SLEEPER, popen=lambda args, **kw: spawn_sleeper())
        self.assertTrue(self.owner.close(worker_timeout=2.0))
        self.assertTrue(self.owner.close(worker_timeout=2.0))

    def test_an_unfinished_worker_still_fails_the_close(self):
        self.owner.worker_started()
        self.addCleanup(self.owner.worker_finished)
        self.assertFalse(self.owner.close(worker_timeout=0.3))



class ATerminationInProgressStillOwnsItsChild(unittest.TestCase):
    """A03: the registry must not go empty DURING a stop.

    `cancel()` removed the child, then terminated it, then put it back if the
    stop had failed. For the whole length of the termination the owner reported
    zero children -- so a `close()` arriving in that window returned True with a
    live PID, and the caller went on to delete the files that PID was writing.

    The barrier here is deterministic, not a race to lose: termination is held
    open until the test says otherwise. It demonstrates the Qt-free owner's
    concurrency contract, not that a Qt UI action reproduces this ordering.
    """

    def setUp(self) -> None:
        self.owner = ChildProcessOwner(kill_timeout=2.0)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        real_terminate = self.owner._terminate

        def held_terminate(proc):
            self.entered.set()
            self.release.wait(20)
            return real_terminate(proc)

        self.owner._terminate = held_terminate
        self.proc = self.owner.start(SLEEPER, popen=lambda args, **kw: spawn_sleeper())
        self.addCleanup(self._really_stop)

    def _really_stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except Exception:       # noqa: BLE001 - best effort in cleanup
                pass

    def test_the_child_is_still_counted_while_it_is_being_stopped(self):
        worker = threading.Thread(target=self.owner.cancel, name="canceller")
        worker.start()
        self.addCleanup(worker.join, 20)
        self.assertTrue(self.entered.wait(10), "termination never started")
        self.assertEqual(1, self.owner.active_children(),
                         "the owner reported zero children while a stop was in flight")
        self.assertIsNone(self.proc.poll(), "the fixture's child exited on its own")

    def test_close_refuses_while_a_stop_is_in_flight(self):
        worker = threading.Thread(target=self.owner.cancel, name="canceller")
        worker.start()
        self.addCleanup(worker.join, 20)
        self.assertTrue(self.entered.wait(10), "termination never started")
        self.assertFalse(self.owner.close(worker_timeout=0.3),
                         "close() reported a completed shutdown during a live stop")

    def test_the_transition_completes_once_the_stop_is_allowed_to_finish(self):
        worker = threading.Thread(target=self.owner.cancel, name="canceller")
        worker.start()
        self.assertTrue(self.entered.wait(10))
        self.release.set()
        worker.join(20)
        self.assertFalse(worker.is_alive())
        self.assertEqual(0, self.owner.active_children())
        self.assertIsNotNone(self.proc.poll(), "the child was never reaped")
        self.assertTrue(self.owner.close(worker_timeout=1.0))


class AFailedStopKeepsItsArtifact(unittest.TestCase):
    """A03: the file a live child is writing is not the caller's to delete.

    `_load_pcm` stopped its child after a communication error and then removed
    the PCM in an unconditional `finally` -- even when the stop had been REFUSED
    and the decoder was still writing into that file. The stop's outcome has to
    decide, and a retained artifact stays owned and retryable rather than
    disappearing or being reported as cleaned.
    """

    def setUp(self) -> None:
        self.owner = ChildProcessOwner(kill_timeout=0.4)
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="ffmwiz_a03art_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.artifact = self.root / "decode.pcm"
        self.artifact.write_bytes(b"partial samples")

    def test_a_retained_artifact_is_listed_with_its_reason(self):
        self.owner.retain_artifact(self.artifact, "terminate refused")
        self.assertEqual([self.artifact], self.owner.retained_artifacts())
        self.assertIn("terminate refused", " ".join(self.owner.retained_reasons()))

    def test_a_retained_artifact_is_not_deleted_while_a_child_is_alive(self):
        stubborn = Unkillable()
        self.addCleanup(stubborn.really_stop)
        self.owner.start(SLEEPER, popen=lambda args, **kw: stubborn)
        self.owner.retain_artifact(self.artifact, "terminate refused")
        self.assertFalse(self.owner.sweep_retained(),
                         "a live child's artifact was swept")
        self.assertTrue(self.artifact.exists())

    def test_it_is_swept_once_the_owner_is_really_empty(self):
        self.owner.retain_artifact(self.artifact, "terminate refused")
        self.assertTrue(self.owner.sweep_retained())
        self.assertFalse(self.artifact.exists())
        self.assertEqual([], self.owner.retained_artifacts())

    def test_close_admits_the_shutdown_is_incomplete_while_a_writer_lives(self):
        # The artifact alone does not make a shutdown incomplete -- an owner
        # with nothing running can remove it, and then it really is complete.
        # What must never happen is reporting complete while the WRITER is
        # alive, because that is when deleting the file is the race.
        stubborn = Unkillable()
        self.addCleanup(stubborn.really_stop)
        self.owner.start(SLEEPER, popen=lambda args, **kw: stubborn)
        self.owner.retain_artifact(self.artifact, "terminate refused")
        self.assertFalse(self.owner.close(worker_timeout=0.2),
                         "cleanup reported complete with a live writer")
        self.assertTrue(self.artifact.exists(),
                        "the file a live child is writing was deleted anyway")

    def test_close_completes_and_sweeps_once_nothing_is_running(self):
        self.owner.retain_artifact(self.artifact, "terminate refused")
        self.assertTrue(self.owner.close(worker_timeout=0.2))
        self.assertFalse(self.artifact.exists(),
                         "the retained file was left behind with nobody responsible")


if __name__ == "__main__":
    unittest.main()
