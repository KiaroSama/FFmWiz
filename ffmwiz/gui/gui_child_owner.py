"""One owner for every child process a GUI background worker starts.

Both editors render preview proxies and decode waveforms on background
threads, and each of those holds an FFmpeg child. Registering that child
*after* `Popen` returns leaves a window with no owner: a cancel or a window
close that lands inside it sees no child, reports everything clean, deletes
the temp directory -- and the child it never saw keeps running, then registers
itself into an owner that has already shut down. A paused-spawn probe
reproduced exactly that: cleanup returned, the proxy directory was gone, the
real child was still alive.

The window is closed by starting the child UNDER the lock that records it. A
canceller therefore either arrives before the spawn, and the spawn is refused,
or after the registration, and finds the child. There is no third case.

Qt-free and importable without PySide6, so the lifecycle can be tested with
real child processes and no display.
"""
from __future__ import annotations

import subprocess
import threading


class ChildProcessOwner:
    """Tracks child processes and background workers for one editor window.

    `generation` supports superseded work: a reverse-preview render started for
    an older seek must not spawn at all once a newer one exists, and must not
    publish its result if it was already running.
    """

    def __init__(self, kill_timeout: float = 5.0) -> None:
        self._lock = threading.RLock()
        # id(proc) -> (proc, generation). Keyed by identity because Popen is
        # not hashable-by-value and two runs can share an exit code.
        self._children: dict[int, tuple[subprocess.Popen, int]] = {}
        # id(proc) -> why it could not be reaped. Ownership is kept for these,
        # so a later attempt can finish the job and a caller can say what is
        # still holding the files (A03).
        self._unreaped: dict[int, str] = {}
        self._closed = False
        self._generation = 0
        self._workers = 0
        self._idle = threading.Event()
        self._idle.set()
        self._kill_timeout = max(0.1, float(kill_timeout))

    # --- state -------------------------------------------------------------
    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def active_children(self) -> int:
        with self._lock:
            return len(self._children)

    def active_workers(self) -> int:
        with self._lock:
            return self._workers

    def current_generation(self) -> int:
        with self._lock:
            return self._generation

    def bump_generation(self, generation: int | None = None) -> int:
        """Make `generation` (or the next number) the live one."""
        with self._lock:
            self._generation = int(generation) if generation is not None else self._generation + 1
            return self._generation

    def is_current(self, generation: int) -> bool:
        with self._lock:
            return not self._closed and int(generation) >= self._generation

    # --- workers -----------------------------------------------------------
    def worker_started(self) -> None:
        with self._lock:
            self._workers += 1
            self._idle.clear()

    def worker_finished(self) -> None:
        with self._lock:
            self._workers = max(0, self._workers - 1)
            if not self._workers:
                self._idle.set()

    def wait_idle(self, timeout: float) -> bool:
        """True when every registered worker has returned."""
        return self._idle.wait(max(0.0, float(timeout)))

    # --- children ----------------------------------------------------------
    def start(self, args, generation: int | None = None, popen=subprocess.Popen, **kwargs):
        """Spawn and register a child atomically; None when it was refused.

        Refused after `close()`, and refused for a generation another render
        has already superseded -- a superseded child is never created at all,
        which is cheaper and far safer than creating one and racing to kill it.
        """
        with self._lock:
            if self._closed:
                return None
            if generation is not None and int(generation) < self._generation:
                return None
            proc = popen(args, **kwargs)
            self._children[id(proc)] = (proc, self._generation if generation is None else int(generation))
            return proc

    def unreaped(self) -> list[str]:
        """Why each still-owned child could not be stopped. Empty when clean."""
        with self._lock:
            return list(self._unreaped.values())

    def finish(self, proc) -> bool:
        """Release a child that has EXITED. False when it is still running.

        The caller used to run this from an unconditional `finally`, so a
        `communicate()` that raised released a child that was still alive --
        and cleanup then deleted the directory it was writing into (A03).
        Releasing ownership is a statement that the process is gone, so it is
        made only when `poll()` says so.
        """
        if proc is None:
            return True
        try:
            exited = proc.poll() is not None
        except Exception:      # noqa: BLE001 - a handle we can no longer query
            exited = False
        if not exited:
            return False
        with self._lock:
            self._children.pop(id(proc), None)
            self._unreaped.pop(id(proc), None)
        return True

    def stop(self, proc) -> bool:
        """Terminate and reap ONE owned child. False when it survived.

        The single-child form of `cancel`, for a worker whose communication
        failed: the child must be stopped before anyone may touch what it was
        writing.
        """
        if proc is None:
            return True
        with self._lock:
            entry = self._children.pop(id(proc), None)
        why = self._terminate(proc)
        if why:
            with self._lock:
                if entry is not None:
                    self._children[id(proc)] = entry
                self._unreaped[id(proc)] = why
            return False
        with self._lock:
            self._unreaped.pop(id(proc), None)
        return True

    def cancel(self, keep_generation: int | None = None) -> int:
        """Stop owned children; optionally spare the given generation.

        Returns how many were actually STOPPED, not how many were attempted.
        A child whose terminate and kill were both refused stays owned and
        stays counted: releasing it on a failed attempt is what let
        `active_children()` read 0 while the process was still running (A03).
        """
        with self._lock:
            doomed = [entry for entry in self._children.values()
                      if keep_generation is None or entry[1] != int(keep_generation)]
            for proc, _generation in doomed:
                self._children.pop(id(proc), None)
        stopped = 0
        for proc, generation in doomed:
            why = self._terminate(proc)
            with self._lock:
                if why:
                    self._children[id(proc)] = (proc, generation)
                    self._unreaped[id(proc)] = why
                else:
                    stopped += 1
                    self._unreaped.pop(id(proc), None)
        return stopped

    def close(self, worker_timeout: float = 8.0) -> bool:
        """Shut down, and say honestly whether the shutdown completed.

        Returns True only when every worker returned inside `worker_timeout`.
        A False result means somebody still owns resources -- the caller must
        NOT then delete the files those workers are writing into, which is
        exactly what happened while this returned None and the caller had
        nothing to check (R04).

        The order matters: killing first is what unblocks a worker sitting in
        `communicate()`, and waiting for the workers is what makes it safe for
        the caller to clean up. Idempotent.
        """
        with self._lock:
            self._closed = True
        self.cancel()
        idle = self.wait_idle(worker_timeout)
        self.cancel()      # a worker may have raced one last child in
        with self._lock:
            # A surviving child counts too. Workers idle while a process they
            # started is still writing is exactly the state in which deleting
            # its directory is a race (A03).
            return bool(idle) and self._workers == 0 and not self._children

    def _terminate(self, proc) -> str:
        """Bounded terminate -> kill -> reap. "" on confirmed exit, else why not.

        An outcome, not a best effort. Swallowing every failure here is how a
        live child came to be reported as cleaned up: the caller had nothing to
        check and no reason to keep looking (A03).
        """
        reasons: list[str] = []
        try:
            if proc.poll() is not None:
                return ""
            proc.terminate()
        except Exception as exc:   # noqa: BLE001 - already gone, or refused
            reasons.append(f"terminate refused: {exc}")
        try:
            proc.wait(timeout=self._kill_timeout)
            return ""
        except Exception:          # noqa: BLE001 - still alive after terminate
            pass
        try:
            proc.kill()
        except Exception as exc:   # noqa: BLE001
            reasons.append(f"kill refused: {exc}")
        try:
            proc.wait(timeout=self._kill_timeout)
        except Exception as exc:   # noqa: BLE001
            reasons.append(f"still running after kill: {exc}")
        try:
            if proc.poll() is not None:
                return ""
        except Exception as exc:   # noqa: BLE001
            reasons.append(f"exit status unavailable: {exc}")
        return "; ".join(reasons) or "did not exit within the kill timeout"


__all__ = ["ChildProcessOwner"]
