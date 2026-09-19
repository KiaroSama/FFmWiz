"""One test module, in its own interpreter, from first import to last thread.

The parent runner spawns this file once per module:

    python tests/run_suite_child.py <module-name> <result-path> [<seconds>]

Why a whole process per module, in serial runs as much as parallel ones:

* a module that calls `os._exit` kills only itself. It used to kill the RUNNER
  in serial mode, losing every earlier module's record and the results file.
* the module's work can be bounded. A stuck non-daemon thread blocks interpreter
  shutdown forever, and nothing inside that interpreter can break the deadlock;
  a parent with a ceiling can.
* the exception hooks can stay installed for the whole life of the process, so a
  thread started BY a thread -- which no snapshot of "threads this module
  started" ever contains -- is still attributed to the module that started it.

The record is written from an `atexit` handler, which CPython runs after
`threading._shutdown()` has joined the non-daemon threads: anything they raise
on the way out is in the file.

Not a test file -- named so `unittest discover` (test*.py) does not load it.
"""
from __future__ import annotations

import atexit
import gc
import json
import os
import sys
import threading
import time
import traceback
import unittest
import warnings
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent

# How long a module's own threads get to finish after its last test returned.
# A ceiling, not an expected duration: a test that needs longer should join its
# thread itself, which is what makes the wait deterministic.
MODULE_THREAD_SETTLE_S = 10.0

# What the child exits with when its own watchdog has to end it. The parent
# normally kills a module at the ceiling first; this is what stops a stuck
# module becoming an immortal orphan when the parent itself is cancelled.
SELF_TIMEOUT_EXIT = 9
SELF_TIMEOUT_GRACE_S = 15.0


def _settle_module_threads(before: set, module_name: str) -> list[tuple[str, str]]:
    """Wait for the threads THIS module started; report the ones that outlive it.

    Returns the same (label, text) shape the unraisable/thread hooks produce, so
    a leaked worker lands in the module's errors like any other defect instead
    of disappearing into the next module's run.

    It is deliberately NOT the whole safety net: the set is snapshotted once, so
    a thread started by one of these threads is not in it. That descendant is
    covered by the process-level hooks below, which outlive this function.
    """
    new_threads = [thread for thread in threading.enumerate()
                   if thread not in before and thread is not threading.current_thread()]
    if not new_threads:
        return []
    deadline = time.monotonic() + MODULE_THREAD_SETTLE_S
    for thread in new_threads:
        if thread.daemon:
            continue          # a daemon thread is declared not-owned-to-completion
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            thread.join(remaining)
        except RuntimeError:  # pragma: no cover - joining self or a dead thread
            continue
    leaked = [thread for thread in new_threads if thread.is_alive()]
    return [(f"{module_name} (leaked thread {thread.name})",
             f"still running {MODULE_THREAD_SETTLE_S:.0f}s after the module's last test "
             f"returned; daemon={thread.daemon}. A test owns what it starts: join it, "
             f"or give it a bounded stop.")
            for thread in leaked]


def run_module(name: str) -> dict:
    """Run one test module in this process and return a picklable summary."""
    module_name = name
    # Unclosed subprocess pipes are a real, previously shipped bug class.
    warnings.simplefilter("error", ResourceWarning)
    sys.path.insert(0, str(TESTS_DIR))
    sys.path.insert(0, str(PROJECT_ROOT))

    # An exception raised inside __del__ -- which is where an unclosed file or
    # pipe turns the ResourceWarning above into one -- cannot propagate. Python
    # hands it to sys.unraisablehook, which PRINTS it and moves on, so a real
    # leak produced "1 test, 0 errors" and a green suite. Collect them here
    # instead, as module errors.
    unraisable: list[tuple[str, str]] = []
    previous_hook = sys.unraisablehook
    previous_thread_hook = threading.excepthook

    def collect_unraisable(entry) -> None:
        # Never raise out of this hook, and never keep a reference to the
        # object being finalized: only formatted text crosses back.
        try:
            where = getattr(entry, "err_msg", None) or "Exception ignored in"
            text = "".join(traceback.format_exception(
                entry.exc_type, entry.exc_value, entry.exc_traceback))
            unraisable.append((f"{name} (unraisable)", f"{where}\n{text}"))
        except Exception:       # noqa: BLE001 - a broken hook hides everything
            unraisable.append((f"{name} (unraisable)", "unraisable exception (details unavailable)"))

    def collect_thread_exception(entry) -> None:
        # An exception that escapes a thread's run() is printed by the default
        # hook and changes nothing else: a test that joins that thread still
        # passed, and the runner still reported OK (R08). Same contract as the
        # unraisable hook -- never raise, keep only text.
        try:
            thread_name = getattr(entry.thread, "name", "thread")
            text = "".join(traceback.format_exception(
                entry.exc_type, entry.exc_value, entry.exc_traceback))
            unraisable.append((f"{module_name} (thread {thread_name})", text))
        except Exception:       # noqa: BLE001 - a broken hook hides everything
            unraisable.append((f"{module_name} (thread)", "thread exception (details unavailable)"))

    sys.unraisablehook = collect_unraisable
    threading.excepthook = collect_thread_exception
    started = time.monotonic()
    before = set(threading.enumerate())
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName(name)
        stream = __import__("io").StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
        # A module is not finished while the threads its tests started are
        # still running. The hooks used to be restored the moment `run()`
        # returned, so a thread that raised a fraction of a second later met
        # the DEFAULT hook, which prints and returns -- module green, run green
        # (A06). Only threads that appeared during this module are waited for;
        # joining anything else would mean waiting on the pool's own workers.
        unraisable.extend(_settle_module_threads(before, module_name))
        # Force pending finalizers to run while the hook is still installed,
        # so a leak that CPython had not collected yet is still attributed to
        # the module that caused it.
        gc.collect()
    finally:
        # Restored in `finally` and in this order, so a failure inside the run
        # cannot leave the interpreter with this module's hooks installed.
        sys.unraisablehook = previous_hook
        threading.excepthook = previous_thread_hook

    return {
        "module": name,
        "status": "ok",
        "seconds": time.monotonic() - started,
        "run": result.testsRun,
        "unraisable": list(unraisable),
        # Tracebacks are already formatted strings; the TestCase objects are not
        # reliably picklable, so only the text crosses the process boundary.
        "failures": [(str(test), text) for test, text in result.failures],
        "errors": [(str(test), text) for test, text in result.errors] + list(unraisable),
        "skipped": [(str(test), why) for test, why in result.skipped],
        # An @unittest.expectedFailure test that PASSES lands here and in
        # nothing else -- not in `failures`, not in `errors`. The markers in
        # this suite are self-removing: each says "delete me once the defect
        # is fixed", and the signal to delete it is the run going red.
        # Without this list the verdict below could not see them, so a fixed
        # defect kept its marker and the suite still said OK -- which is how
        # the join builder gap stayed marked after it was closed.
        "unexpected": [str(test) for test in result.unexpectedSuccesses],
        "ok": result.wasSuccessful(),
    }


def _empty_record(name: str) -> dict:
    return {"module": name, "status": "ok", "seconds": 0.0, "run": 0,
            "unraisable": [], "failures": [], "errors": [], "skipped": [],
            "unexpected": [], "ok": True}


def _write(path: Path, record: dict) -> None:
    """Publish the record atomically, so the parent never reads half of it."""
    try:
        scratch = path.with_name(path.name + ".partial")
        scratch.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(scratch, path)
    except OSError as exc:                # pragma: no cover - the parent then reports a crash
        print(f"could not write the module record: {exc}", file=sys.stderr)


def _start_watchdog(seconds: float, path: Path, record: dict) -> None:
    """End this process if nothing else does.

    The parent kills a module at its ceiling, which is the normal bound. This
    is the one that still holds when the PARENT is the process that died: a
    stuck non-daemon thread would otherwise keep this interpreter alive with
    nobody left to reap it.
    """
    def expire() -> None:
        if not threading.Event().wait(seconds):
            record["status"] = "timeout"
            record["ok"] = False
            record["errors"] = list(record["errors"]) + [
                (f"{record['module']} (timeout)",
                 f"the module was still running {seconds:.0f}s after it started "
                 f"and ended itself. Something it owns never finished.")]
            _write(path, record)
            os._exit(SELF_TIMEOUT_EXIT)

    threading.Thread(target=expire, name="module-watchdog", daemon=True).start()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: run_suite_child.py <module-name> <result-path> [<seconds>]",
              file=sys.stderr)
        return 2
    name, result_path = argv[0], Path(argv[1])
    if len(argv) > 3 and argv[3] == "--gated":
        if sys.stdin.buffer.read(1) != b"G":
            return 2
        sys.stdin.close()
    seconds = float(argv[2]) if len(argv) > 2 else 0.0

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    record = _empty_record(name)
    if seconds > 0:
        _start_watchdog(seconds + SELF_TIMEOUT_GRACE_S, result_path, record)

    # Installed for the whole life of this process and never restored. A thread
    # started by a thread is not in any snapshot, and the exception it raises
    # arrives after `run_module` has put its own hooks back -- the default hook
    # printed it and the run passed (A06). These catch what is left, and the
    # atexit handler below runs AFTER the non-daemon threads are joined, so what
    # they raise on the way out is still in the record.
    late: list[tuple[str, str]] = []

    def collect_late_unraisable(entry) -> None:
        try:
            text = "".join(traceback.format_exception(
                entry.exc_type, entry.exc_value, entry.exc_traceback))
            late.append((f"{name} (unraisable after the module)", text))
        except Exception:       # noqa: BLE001
            late.append((f"{name} (unraisable after the module)", "details unavailable"))

    def collect_late_thread(entry) -> None:
        try:
            thread_name = getattr(entry.thread, "name", "thread")
            text = "".join(traceback.format_exception(
                entry.exc_type, entry.exc_value, entry.exc_traceback))
            late.append((f"{name} (thread {thread_name} after the module)", text))
        except Exception:       # noqa: BLE001
            late.append((f"{name} (thread after the module)", "details unavailable"))

    sys.unraisablehook = collect_late_unraisable
    threading.excepthook = collect_late_thread

    infrastructure_threads = set(threading.enumerate())

    def publish() -> None:
        # Non-daemon threads are already joined by CPython here. A daemon
        # spawned by one of them may never have appeared in the earlier
        # snapshot: account for it before publishing the final record.
        for thread in threading.enumerate():
            if thread not in infrastructure_threads and thread.is_alive():
                late.append((f"{name} (leaked thread {thread.name})",
                             "module-owned thread survived interpreter shutdown; "
                             f"daemon={thread.daemon}"))
        if late:
            record["errors"] = list(record["errors"]) + late
            record["unraisable"] = list(record["unraisable"]) + late
            record["ok"] = False
        _write(result_path, record)

    atexit.register(publish)

    started = time.monotonic()
    try:
        record.update(run_module(name))
    except BaseException:       # noqa: BLE001 - an import error is the module's failure
        record["status"] = "crash"
        record["ok"] = False
        record["seconds"] = time.monotonic() - started
        record["errors"] = [(f"{name} (did not load)", traceback.format_exc())]
    # The hooks run_module restored are its own, not these: put the process-level
    # ones back so what happens between here and interpreter exit is still ours.
    sys.unraisablehook = collect_late_unraisable
    threading.excepthook = collect_late_thread
    return 0 if record.get("ok", False) and not late else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
