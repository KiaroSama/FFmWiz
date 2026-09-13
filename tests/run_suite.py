"""Parallel test runner for the FFmWiz suite -- stdlib only, no test deps.

The suite is dominated by real FFmpeg encodes: measured on a 16-core machine it
spends ~13 s on CPU and the rest waiting on ffmpeg child processes and disk.
That is exactly the shape parallelism helps, and `unittest` has no parallel
runner, so this composes `unittest` with `concurrent.futures`.

Each worker process runs ONE test module to completion, pulling the next module
off the queue as it frees up (chunksize=1), so a single slow module cannot
stall a whole bucket the way a static split would. Module-level isolation is
what keeps it safe: several suites monkeypatch module globals such as
`appio.note`, which is only sound while one module owns its interpreter.

Not a test file -- named `run_suite.py` so `unittest discover` (test*.py) does
not try to load the runner as a suite.

    python tests/run_suite.py            # auto workers
    python tests/run_suite.py -j 1       # serial, same reporting
    python tests/run_suite.py -k join    # only modules matching a substring
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import gc
import json
import threading
import platform
import shutil
import time
import traceback
import unittest
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent

# Leave headroom for the OS, this parent process and the ffmpeg children each
# worker spawns; saturating every core makes the encodes contend and get slower.
DEFAULT_WORKERS = max(2, min(8, (os.cpu_count() or 4) - 2))

# A skip is legitimate only when the machine genuinely cannot provide the thing
# (no NVIDIA GPU, no symlink privilege). A skip for a capability the job INSTALLS
# means the suite silently shrank -- the false green the guard exists to stop.
#
# Named capabilities rather than one fuzzy alternation: the old pattern was
# `ffmpeg|ffprobe|numpy|powershell`, which did not mention PySide6, QtQml or
# QtQuick. The CI job that installs PySide6 to run the QML behaviour suites
# therefore reported OK with those very classes skipped -- a false green for the
# exact dependency the guard was added to enforce (B17).
CAPABILITY_PATTERNS: dict[str, re.Pattern[str]] = {
    "ffmpeg": re.compile(r"ffmpeg|ffprobe", re.I),
    "numpy": re.compile(r"numpy", re.I),
    "powershell": re.compile(r"powershell|pwsh", re.I),
    # A word-boundary `qt` too: with the broad `headless` exemption gone (R08), a skip that
    # says "Qt cannot start headless" has to land on a capability rather than
    # fall through unattributed. For this project Qt IS PySide6.
    "pyside6": re.compile(r"pyside6|qtqml|qtquick|qt quick|qml|\bqt\b", re.I),
    "wheel": re.compile(r"setuptools|wheel", re.I),
}

# Genuinely environmental: no amount of installing fixes them on a given runner.
# Matched FIRST, so "no usable NVIDIA CUDA/hevc_nvenc hardware" is never blamed
# on the ffmpeg the job did install.
#
# `no usable` is NOT in this list, and must not be put back. It used to be, and
# it matched first -- so "No usable FFmpeg binary" and "No usable PySide6
# installation" were both filed as environmental and sailed past `--require
# ffmpeg` / `--require pyside6`. A required dependency does not become optional
# because of the wording of the skip that announced its absence.
#
# `headless` and `display` are gone for the same reason (R08). They were added
# for "no display" skips, but the GUI job runs under QT_QPA_PLATFORM=offscreen,
# where a missing display is not a legitimate excuse -- so the only thing those
# two words could do was let a Qt capability failure through by wording.
# Everything left here names hardware or an OS privilege that installing
# cannot provide.
ENVIRONMENT_SKIP_RE = re.compile(
    r"nvenc|nvidia|cuda|symlink|privilege|hardware", re.I)


def classify_skip(reason: str) -> str:
    """Which capability a skip blames, or "" when it is environmental."""
    if ENVIRONMENT_SKIP_RE.search(reason):
        return ""
    for name, pattern in CAPABILITY_PATTERNS.items():
        if pattern.search(reason):
            return name
    return ""


def probe_capability(name: str) -> str:
    """"" when the capability is really present, else why it is not.

    A preflight, so `--require X` fails on the missing dependency itself rather
    than on the wording of whatever skip happened to mention it -- and fails
    even when the affected suites skipped for some other reason, or were not
    selected at all.
    """
    import importlib.util
    import shutil

    if name == "ffmpeg":
        for tool in ("ffmpeg", "ffprobe"):
            found = shutil.which(tool)
            if not found:
                return f"{tool} is not on PATH"
            why = _probe_executable(found)
            if why:
                return f"{tool} at {found} {why}"
        return ""
    if name == "powershell":
        found = shutil.which("powershell") or shutil.which("pwsh")
        if not found:
            return "neither powershell nor pwsh is on PATH"
        return ""
    modules = {"numpy": ("numpy",), "pyside6": ("PySide6", "PySide6.QtQml", "PySide6.QtQuick"),
               "wheel": ("wheel", "setuptools")}
    for module in modules.get(name, ()):  # pragma: no branch - table-driven
        if importlib.util.find_spec(module) is None:
            return f"{module} is not importable"
        why = _probe_import(module)
        if why:
            return why
    return ""


def _probe_executable(path: str) -> str:
    """"" when the binary actually RUNS, else why it does not.

    `shutil.which` only proves a file with that name is on PATH. A wrapper that
    exits 2, or a binary missing a shared library, satisfied `--require` and the
    suite then skipped every test that needed it (R08).
    """
    import subprocess
    try:
        result = subprocess.run([path, "-version"], capture_output=True, timeout=60)
    except OSError as exc:
        return f"could not be executed: {exc}"
    except subprocess.SubprocessError as exc:
        return f"did not answer -version: {exc}"
    if result.returncode != 0:
        return f"exited {result.returncode} for -version"
    return ""


def _probe_import(module: str) -> str:
    """"" when the module actually IMPORTS, else why it does not.

    `find_spec` only proves a file is in the right place. A package that raises
    at import time -- a broken Qt install is the usual one -- has a spec and no
    working module, and that satisfied `--require pyside6` (R08). The import
    runs in a FRESH interpreter so a half-initialised module cannot poison this
    process, and it is bounded.
    """
    import subprocess
    try:
        result = subprocess.run([sys.executable, "-c", f"import {module}"],
                                capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{module} could not be probed: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        return f"{module} fails at import: {detail[-1] if detail else 'unknown error'}"
    return ""


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
            name = getattr(entry.thread, "name", "thread")
            text = "".join(traceback.format_exception(
                entry.exc_type, entry.exc_value, entry.exc_traceback))
            unraisable.append((f"{module_name} (thread {name})", text))
        except Exception:       # noqa: BLE001 - a broken hook hides everything
            unraisable.append((f"{module_name} (thread)", "thread exception (details unavailable)"))

    sys.unraisablehook = collect_unraisable
    threading.excepthook = collect_thread_exception
    started = time.monotonic()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName(name)
        stream = __import__("io").StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
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


def main(argv: list[str] | None = None) -> int:
    """Run the suite, then always write the machine-readable results.

    A red CI job is exactly the run whose log is hardest to read, so the result
    file is written on every exit path -- including the ones that refuse to run
    anything at all.
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
                          time.monotonic() - started, arguments)
    return verdict


def _run(argv, collected: list[dict], parsed: dict) -> int:
    # Failure text can contain any character the app emits (the editors use
    # glyphs like the transport arrows). A cp1252 console would raise
    # UnicodeEncodeError while PRINTING the failure and hide it entirely.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-j", "--jobs", type=int, default=DEFAULT_WORKERS,
                        help=f"worker processes (default {DEFAULT_WORKERS})")
    parser.add_argument("-k", "--filter", action="append", default=None,
                        help="only modules whose name contains this substring; "
                             "repeat to select several")
    parser.add_argument("--strict-skips", action="store_true",
                        help="fail when a suite skipped for any installable capability")
    parser.add_argument("--json", metavar="PATH", default=None,
                        help="write a machine-readable result file (modules, "
                             "counts, failures, skip reasons, environment)")
    parser.add_argument("--require", action="append", default=None,
                        metavar="CAPABILITY",
                        help="fail when a suite skipped for THIS capability "
                             "(repeat or comma-separate: "
                             "ffmpeg, numpy, powershell, pyside6, wheel). "
                             "Use it in a job that installs the dependency.")
    args = parser.parse_args(argv)
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

    started = time.monotonic()
    results: list[dict] = []
    if args.jobs <= 1:
        results = [run_module(name) for name in modules]
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            # chunksize=1 -> dynamic balancing; a worker takes the next module
            # the moment it is free instead of owning a fixed share up front.
            results = list(pool.map(run_module, modules, chunksize=1))
    elapsed = time.monotonic() - started
    collected.extend(results)

    total = sum(item["run"] for item in results)
    # A module that matched the selection but ran nothing is a silent hole: a
    # renamed class, a bad -k, an import guard that swallowed everything. It
    # used to be accepted by the parent verdict as zero tests, zero failures.
    empty = [item["module"] for item in results
             if item["run"] == 0 and not item["skipped"]]
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

    if total == 0:
        print("the selection ran 0 tests")
        return 1

    if failures or errors or unexpected:
        print(f"FAILED (failures={len(failures)}, errors={len(errors)}, "
              f"unexpected successes={len(unexpected)})")
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


def environment_report() -> dict:
    """Versions and tool paths a failed CI run needs and a log line loses."""
    report = {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
    }
    for tool in ("ffmpeg", "ffprobe", "powershell", "pwsh"):
        found = shutil.which(tool)
        report[tool] = found or ""
        if found and tool in ("ffmpeg", "ffprobe"):
            try:
                import subprocess
                first = subprocess.run([found, "-version"], capture_output=True,
                                       text=True, timeout=60).stdout.splitlines()
                report[tool + "_version"] = first[0] if first else ""
            except Exception as exc:      # noqa: BLE001 - reporting must not fail the run
                report[tool + "_version"] = f"unavailable: {exc}"
    for module in ("numpy", "PySide6", "setuptools", "wheel"):
        try:
            report[module] = __import__(module).__version__
        except Exception:                 # noqa: BLE001
            report[module] = ""
    return report


def write_results(path: str, results: list[dict], verdict: int, elapsed: float,
                  arguments) -> None:
    """One JSON file per run, so a red CI job is readable without the log."""
    payload = {
        "verdict": "ok" if verdict == 0 else "failed",
        "exit_code": verdict,
        "seconds": round(elapsed, 3),
        "workers": arguments.jobs,
        "selection": arguments.filter or [],
        "required": arguments.require or [],
        "environment": environment_report(),
        "modules": [
            {
                "module": item["module"],
                "seconds": round(item["seconds"], 3),
                "tests": item["run"],
                "failures": [name for name, _text in item["failures"]],
                "errors": [name for name, _text in item["errors"]],
                "unexpected": item["unexpected"],
                "skipped": [{"test": name, "reason": why,
                             "capability": classify_skip(why)}
                            for name, why in item["skipped"]],
            }
            for item in results
        ],
    }
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    except OSError as exc:
        print(f"could not write {path}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
