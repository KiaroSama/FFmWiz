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
import time
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
    "pyside6": re.compile(r"pyside6|qtqml|qtquick|qt quick|qml", re.I),
    "wheel": re.compile(r"setuptools|wheel", re.I),
}

# Genuinely environmental: no amount of installing fixes them on a given runner.
# Matched FIRST, so "no usable NVIDIA CUDA/hevc_nvenc hardware" is never blamed
# on the ffmpeg the job did install.
ENVIRONMENT_SKIP_RE = re.compile(
    r"nvenc|nvidia|cuda|symlink|privilege|hardware|no usable", re.I)


def classify_skip(reason: str) -> str:
    """Which capability a skip blames, or "" when it is environmental."""
    if ENVIRONMENT_SKIP_RE.search(reason):
        return ""
    for name, pattern in CAPABILITY_PATTERNS.items():
        if pattern.search(reason):
            return name
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
    # Unclosed subprocess pipes are a real, previously shipped bug class.
    warnings.simplefilter("error", ResourceWarning)
    sys.path.insert(0, str(TESTS_DIR))
    sys.path.insert(0, str(PROJECT_ROOT))

    started = time.monotonic()
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    stream = __import__("io").StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    return {
        "module": name,
        "seconds": time.monotonic() - started,
        "run": result.testsRun,
        # Tracebacks are already formatted strings; the TestCase objects are not
        # reliably picklable, so only the text crosses the process boundary.
        "failures": [(str(test), text) for test, text in result.failures],
        "errors": [(str(test), text) for test, text in result.errors],
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
    parser.add_argument("--require", action="append", default=None,
                        metavar="CAPABILITY",
                        help="fail when a suite skipped for THIS capability "
                             "(repeat or comma-separate: "
                             "ffmpeg, numpy, powershell, pyside6, wheel). "
                             "Use it in a job that installs the dependency.")
    args = parser.parse_args(argv)

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

    total = sum(item["run"] for item in results)
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

    if failures or errors or unexpected:
        print(f"FAILED (failures={len(failures)}, errors={len(errors)}, "
              f"unexpected successes={len(unexpected)})")
        return 1

    required = {name.strip().lower()
                for value in (args.require or [])
                for name in value.split(",") if name.strip()}
    unknown = required - set(CAPABILITY_PATTERNS)
    if unknown:
        print("unknown --require capability: " + ", ".join(sorted(unknown)))
        print("known: " + ", ".join(sorted(CAPABILITY_PATTERNS)))
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
