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

import subprocess
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


if __name__ == "__main__":
    unittest.main()
