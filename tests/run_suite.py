"""Parallel test runner for the FFmWiz suite -- stdlib only, no test deps.

The suite is dominated by real FFmpeg encodes: measured on a 16-core machine it
spends ~13 s on CPU and the rest waiting on ffmpeg child processes and disk.
That is exactly the shape parallelism helps, and `unittest` has no parallel
runner, so this supervises one child interpreter per test module.

ONE shape for serial and parallel runs alike: every module is executed by
`run_suite_child.py` in its own process, bounded by a wall ceiling, and its
record is written to the results file the moment it arrives. `-j` only changes
how many of those children run at once. That is what makes a module's own
failures survivable -- an `os._exit` used to take the serial runner down with
it, and a stuck non-daemon thread used to hang the run with nothing written.

Module-level isolation is also what keeps the suite safe: several modules
monkeypatch module globals such as `appio.note`, which is only sound while one
module owns its interpreter.

Not a test file -- named `run_suite.py` so `unittest discover` (test*.py) does
not try to load the runner as a suite.

    python tests/run_suite.py            # auto workers
    python tests/run_suite.py -j 1       # serial, same reporting
    python tests/run_suite.py -k join    # only modules matching a substring
"""
from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from run_suite_child import MODULE_THREAD_SETTLE_S, run_module  # noqa: E402,F401
from run_suite_report import (CAPABILITY_PATTERNS,  # noqa: E402
                              ENVIRONMENT_SKIP_RE, MAX_DETAIL_CHARS,
                              classify_skip, environment_report,
                              probe_capability, probe_executable, probe_import,
                              trimmed, write_results)

# The self-tests reach for the previous private spellings; they name the same
# functions, which now belong to the reporting module.
_probe_executable = probe_executable
_probe_import = probe_import
_trimmed = trimmed

CHILD = TESTS_DIR / "run_suite_child.py"

# Leave headroom for the OS, this parent process and the ffmpeg children each
# worker spawns; saturating every core makes the encodes contend and get slower.
DEFAULT_WORKERS = max(2, min(8, (os.cpu_count() or 4) - 2))

# How long one module gets, from its first import to its last thread. A ceiling,
# not an expected duration: the slowest module in this suite runs in ~8 s. It
# exists because nothing INSIDE a hung interpreter can end it -- a stuck
# non-daemon thread blocks shutdown forever and the run never reports at all.
MODULE_TIMEOUT_S = 600.0

# Enough of the child's console output to act on, never enough to bury the file.
MAX_LOG_TAIL = 4000


def discover_modules(patterns: str | list[str] | None = None) -> list[str]:
    """Top-level test module names, ordered slowest-first when known.

    Slowest-first matters: dispatching the long modules while every worker is
    still idle keeps the tail short. Without it the 7 s packaging module can be
    picked up last and become the critical path on its own.

    `patterns` may be one substring or several; several means "any of these",
    which is how CI selects the handful of suites that need PySide6.
    """
    if patterns is None:
        wanted: list[str] = []
    elif isinstance(patterns, str):
        wanted = [patterns]
    else:
        wanted = [p for p in patterns if p]
    names = sorted(
        path.stem for path in TESTS_DIR.glob("test*.py")
        if not wanted or any(pattern in path.stem for pattern in wanted)
    )
    # Rough cost order measured with --durations; unknown modules sort after.
    slow_first = ["test_packaging", "test_hdr_dolby_detection", "test_import_topology",
                  "test_subprocess_lifecycle", "test_command_container_policy",
                  "test_mux_cleanup", "test_join_audio_topology", "test_command_option_order"]
    rank = {name: index for index, name in enumerate(slow_first)}
    return sorted(names, key=lambda name: (rank.get(name, len(slow_first)), name))


# Keep the old import surface while the lifecycle boundary has one owner.
from run_suite_process import (module_problem as _module_problem,
                               supervise_module as _supervise_module)


def supervise_module(name, scratch, timeout, live=None, *, cancel=None):
    return _supervise_module(name, scratch, timeout, live, cancel=cancel, child=CHILD)


def main(argv: list[str] | None = None) -> int:
    """Run the suite, then always write the machine-readable results.

    A red CI job is exactly the run whose log is hardest to read, so the result
    file is written on every exit path -- including the ones that refuse to run
    anything at all, and every time one more module finishes.
    """
    started = time.monotonic()
    collected: list[dict] = []
    parsed: dict = {}
    verdict = 1
    try:
        verdict = _run(argv, collected, parsed)
    finally:
        arguments = parsed.get("args")
        if arguments is not None and getattr(arguments, "json", None):
            if not write_results(arguments.json, collected, verdict,
                                 time.monotonic() - started, arguments,
                                 parsed.get("environment")):
                verdict = 1
                print("FAILED: the requested JSON result was not published", file=sys.stderr)
    return verdict


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-j", "--jobs", type=int, default=DEFAULT_WORKERS,
                        help=f"module processes at once (default {DEFAULT_WORKERS})")
    parser.add_argument("-k", "--filter", action="append", default=None,
                        help="only modules whose name contains this substring; "
                             "repeat to select several")
    parser.add_argument("--strict-skips", action="store_true",
                        help="fail when a suite skipped for any installable capability")
    parser.add_argument("--json", metavar="PATH", default=None,
                        help="write a machine-readable result file (modules, "
                             "counts, failures, skip reasons, environment)")
    parser.add_argument("--module-timeout", type=float, default=MODULE_TIMEOUT_S,
                        metavar="SECONDS",
                        help=f"ceiling for one module, first import to last "
                             f"thread (default {MODULE_TIMEOUT_S:.0f})")
    parser.add_argument("--require", action="append", default=None,
                        metavar="CAPABILITY",
                        help="fail when a suite skipped for THIS capability "
                             "(repeat or comma-separate: "
                             "ffmpeg, numpy, powershell, pyside6, wheel). "
                             "Use it in a job that installs the dependency.")
    return parser


def _run(argv, collected: list[dict], parsed: dict) -> int:
    # Failure text can contain any character the app emits (the editors use
    # glyphs like the transport arrows). A cp1252 console would raise
    # UnicodeEncodeError while PRINTING the failure and hide it entirely.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = _parser().parse_args(argv)
    parsed["args"] = args
    if args.jobs < 1 or not math.isfinite(args.module_timeout) or args.module_timeout <= 0:
        print("jobs and module timeout must be positive; timeout must be finite", file=sys.stderr)
        return 1

    required = {name.strip().lower()
                for value in (args.require or [])
                for name in value.split(",") if name.strip()}
    unknown = required - set(CAPABILITY_PATTERNS)
    if unknown:
        print("unknown --require capability: " + ", ".join(sorted(unknown)))
        print("known: " + ", ".join(sorted(CAPABILITY_PATTERNS)))
        return 1

    # Preflight: prove the required capability is actually here BEFORE running,
    # instead of inferring its presence from the absence of a matching skip.
    absent = [f"{name}: {why}" for name in sorted(required) if (why := probe_capability(name))]
    if absent:
        print("a capability this job requires is not installed:")
        for item in absent:
            print("  " + item)
        return 1

    modules = discover_modules(args.filter)
    if not modules:
        print("no test modules matched", file=sys.stderr)
        return 1

    # Probed once, here, so the incremental writes below cost nothing and a
    # hung tool cannot stall every one of them.
    parsed["environment"] = environment_report()
    # Fail before launching tests if the requested evidence cannot be written.
    # This also replaces a stale green report with an explicit non-final one.
    if args.json and not write_results(args.json, [], 1, 0.0, args,
                                       parsed["environment"]):
        parsed["report_failed"] = True
        return 1

    started = time.monotonic()
    results: list[dict] = collected
    # Preserve the documented PID lookup, but never delete a pre-existing
    # directory: a recycled PID or planted path is not proof of ownership.
    scratch = Path(tempfile.gettempdir()) / f"ffmwiz_suite_{os.getpid()}"
    scratch.mkdir(parents=True, exist_ok=False)
    try:
        _execute(modules, args, results, parsed, scratch, started)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    elapsed = time.monotonic() - started
    verdict = _report(results, args, required, elapsed)
    if parsed.get("report_failed"):
        print("FAILED: at least one required JSON publication failed", file=sys.stderr)
        return 1
    return verdict


def _execute(modules: list[str], args, results: list[dict], parsed: dict,
             scratch: Path, started: float) -> None:
    """Run every module and persist each record the moment it arrives.

    `results` IS the list `main` writes from its `finally`, not a copy taken at
    the end: a run that is cancelled, killed or hangs still leaves the record of
    everything that had finished (A06).
    """
    cancelled = threading.Event()
    timeout = float(args.module_timeout)

    def keep(record: dict) -> None:
        results.append(record)
        if args.json:
            if not write_results(args.json, results, 1, time.monotonic() - started,
                                 args, parsed.get("environment")):
                # Keep completed records and continue safely; publication
                # failure is sticky even if a later retry happens to succeed.
                parsed["report_failed"] = True

    pool = ThreadPoolExecutor(max_workers=args.jobs)
    pending, recorded = {}, set()
    interrupted = None
    try:
        for name in modules:
            pending[pool.submit(supervise_module, name, scratch, timeout,
                                cancel=cancelled)] = name
        for future in as_completed(pending):
            name = pending[future]
            try:
                record = future.result()
            except Exception as exc:  # noqa: BLE001 - supervisory failure is not a pass
                record = _module_problem(name, "crash", 0.0, repr(exc))
            keep(record)
            recorded.add(name)
    except BaseException as exc:
        interrupted = exc
        # Signal BEFORE executor shutdown waits. Supervisors own the teardown;
        # queued tasks never open the gate after cancellation was requested.
        cancelled.set()
        for future in pending:
            future.cancel()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        for future, name in pending.items():
            if name in recorded:
                continue
            if future.cancelled():
                record = _module_problem(name, "not-run", 0.0, "cancelled before launch")
            else:
                try:
                    record = future.result()
                except BaseException as exc:
                    record = _module_problem(name, "crash", 0.0, repr(exc))
            keep(record)
            recorded.add(name)
        for name in modules:
            if name not in recorded:
                keep(_module_problem(name, "not-run", 0.0, "not submitted before interruption"))
    if interrupted is not None:
        raise interrupted


def _report(results: list[dict], args, required: set[str], elapsed: float) -> int:
    total = sum(item["run"] for item in results)
    # A module that matched the selection but ran nothing is a silent hole: a
    # renamed class, a bad -k, an import guard that swallowed everything. It
    # used to be accepted by the parent verdict as zero tests, zero failures.
    # A timeout or a crash is NOT that hole -- it has its own record and its own
    # message, and reporting it as "ran no tests" would hide what happened.
    empty = [item["module"] for item in results
             if item.get("status", "ok") == "ok" and item["run"] == 0
             and not item["skipped"] and not item["errors"] and not item["failures"]]
    failures = [entry for item in results for entry in item["failures"]]
    errors = [entry for item in results for entry in item["errors"]]
    skipped = [entry for item in results for entry in item["skipped"]]
    unexpected = [entry for item in results for entry in item["unexpected"]]

    for test, text in failures + errors:
        print(f"\n{'=' * 70}\nFAIL: {test}\n{'-' * 70}\n{text}")
    for test, why in skipped:
        print(f"skipped: {test}: {why}")

    slowest = sorted(results, key=lambda item: -item["seconds"])[:5]
    print("\nslowest modules: " + ", ".join(
        f"{item['module']} {item['seconds']:.1f}s" for item in slowest))
    print(f"\nRan {total} tests in {elapsed:.2f}s across {args.jobs} worker(s)")

    unfinished = [item for item in results if item.get("status", "ok") != "ok"]
    for item in unfinished:
        print(f"{item['status'].upper()}: {item['module']}")

    for test in unexpected:
        print("=" * 70)
        print(f"UNEXPECTED SUCCESS: {test}")
        print("-" * 70)
        print("Marked @unittest.expectedFailure, and it passed. The defect it")
        print("documents is fixed: delete the marker and the comment above it,")
        print("so the test guards the fix from here on.")

    if empty:
        print("selected modules ran no tests at all: " + ", ".join(sorted(empty)))
        print("a matched module with zero tests is a hole in the suite, not a pass.")
        return 1

    if failures or errors or unexpected or unfinished:
        print(f"FAILED (failures={len(failures)}, errors={len(errors)}, "
              f"unexpected successes={len(unexpected)})")
        return 1

    if total == 0:
        print("the selection ran 0 tests")
        return 1

    if args.strict_skips or required:
        missing = []
        for test, why in skipped:
            capability = classify_skip(why)
            if not capability:
                continue
            if capability in required or (args.strict_skips and not required):
                missing.append(f"[{capability}] {test}: {why}")
        if missing:
            print("suite shrank - a capability this job provides was reported missing:")
            for item in missing:
                print("  " + item)
            return 1

    print("OK" + (f" (skipped={len(skipped)})" if skipped else ""))
    return 0


if __name__ == "__main__":
    import signal

    def _request_shutdown(_signum, _frame):
        raise KeyboardInterrupt("runner termination requested")

    signal.signal(signal.SIGTERM, _request_shutdown)
    raise SystemExit(main())
