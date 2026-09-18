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
import json
import os
import shutil
import subprocess
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


def _module_problem(name: str, status: str, seconds: float, detail: str) -> dict:
    """A module that did not report for itself, shaped like one that did.

    Same keys as a real record so the verdict, the printed failures and the JSON
    all handle it without a special case -- and a distinct `status`, because a
    module killed at its ceiling, one whose process died and one that never
    started are three different facts and none of them is a pass.
    """
    return {
        "module": name, "status": status, "seconds": seconds, "run": 0,
        "unraisable": [], "failures": [],
        "errors": [(f"{name} ({status})", detail)],
        "skipped": [], "unexpected": [], "ok": False,
    }


def _log_tail(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-MAX_LOG_TAIL:] if len(text) > MAX_LOG_TAIL else text


def _kill_tree(process: subprocess.Popen) -> None:
    """End the module's process AND whatever it started, then prove it is gone.

    A test module spawns real ffmpeg children; killing only the interpreter
    would leave them running after the run reported. Nothing outside the tree
    this runner started is ever touched.
    """
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            process.kill()
    else:
        import signal
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()
    try:
        process.wait(timeout=60)
    except subprocess.TimeoutExpired:      # pragma: no cover - the OS refused
        pass


def supervise_module(name: str, scratch: Path, timeout: float,
                     live: dict | None = None) -> dict:
    """Run one module in its own interpreter and come back with a record.

    Always comes back: a module that hangs is killed at the ceiling, a module
    whose process dies leaves its exit code and console tail, and either way the
    caller gets something to write down.
    """
    result_path = scratch / f"{name}.json"
    log_path = scratch / f"{name}.log"
    started = time.monotonic()
    arguments = [sys.executable, str(CHILD), name, str(result_path), f"{timeout:.3f}"]
    options: dict = {}
    if os.name != "nt":
        # Its own session, so the whole tree can be signalled at once.
        options["start_new_session"] = True
    with open(log_path, "wb") as log:
        process = subprocess.Popen(arguments, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, cwd=str(PROJECT_ROOT),
                                   **options)
        if live is not None:
            live[name] = process
        try:
            code = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            code, timed_out = None, True
        finally:
            if live is not None:
                live.pop(name, None)
    seconds = time.monotonic() - started
    tail = _log_tail(log_path)

    if result_path.exists():
        try:
            record = json.loads(result_path.read_text(encoding="utf-8"))
            record["seconds"] = seconds
            return record
        except (ValueError, OSError):
            pass                            # fall through to the crash record
    if timed_out:
        return _module_problem(
            name, "timeout", seconds,
            f"the module was killed after {timeout:.0f}s. It never finished: a test "
            f"left work running that nothing joined or stopped, and no result of "
            f"its own reached disk.\nlast output:\n{tail}")
    return _module_problem(
        name, "crash", seconds,
        f"the interpreter running this module exited {code} without writing a "
        f"result. The module took its own process down -- os._exit, a segfault in "
        f"a C extension, or an OOM kill.\nlast output:\n{tail}")


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
            write_results(arguments.json, collected, verdict,
                          time.monotonic() - started, arguments,
                          parsed.get("environment"))
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

    started = time.monotonic()
    results: list[dict] = collected
    # Named for THIS process, not a random suffix. The directory is removed in
    # the `finally` below, but a runner that is killed outright never reaches
    # it -- and a random name then leaves a directory nobody can prove they own.
    # A pid names exactly one live process, so the only holder of this name is a
    # runner that is already gone, and whoever killed us can clean it by pid.
    scratch = Path(tempfile.gettempdir()) / f"ffmwiz_suite_{os.getpid()}"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        _execute(modules, args, results, parsed, scratch, started)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    elapsed = time.monotonic() - started
    return _report(results, args, required, elapsed)


def _execute(modules: list[str], args, results: list[dict], parsed: dict,
             scratch: Path, started: float) -> None:
    """Run every module and persist each record the moment it arrives.

    `results` IS the list `main` writes from its `finally`, not a copy taken at
    the end: a run that is cancelled, killed or hangs still leaves the record of
    everything that had finished (A06).
    """
    lock = threading.Lock()
    live: dict[str, subprocess.Popen] = {}
    timeout = max(1.0, float(args.module_timeout))

    def keep(record: dict) -> None:
        with lock:
            results.append(record)
            if args.json:
                write_results(args.json, results, 1, time.monotonic() - started,
                              args, parsed.get("environment"))

    workers = max(1, int(args.jobs))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(supervise_module, name, scratch, timeout, live): name
                       for name in modules}
            recorded: set[str] = set()
            try:
                for future in as_completed(pending):
                    name = pending[future]
                    recorded.add(name)
                    try:
                        keep(future.result())
                    except Exception as exc:    # noqa: BLE001 - supervising failed
                        keep(_module_problem(name, "crash", 0.0,
                                             f"the module could not be supervised: {exc!r}"))
            finally:
                # Completeness is the point: every module that was submitted
                # gets a record, so "modules" can never be shorter than the
                # selection and quietly read as "these are all that ran" (A06).
                for name in modules:
                    if name not in recorded:
                        keep(_module_problem(
                            name, "not-run", 0.0,
                            "this module was selected and never produced a result."))
    finally:
        # Nothing this runner started outlives it, including on the cancelled
        # path: a module process is owned until it is proven gone.
        for process in list(live.values()):
            if process.poll() is None:
                _kill_tree(process)


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

    if failures or errors or unexpected:
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
    raise SystemExit(main())
