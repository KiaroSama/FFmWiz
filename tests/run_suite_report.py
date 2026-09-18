"""What the runner reports: skip classification, capability probes, results file.

Split out of `run_suite.py` when supervision moved in there and the file reached
its size ceiling. The parent runner re-exports every public name here, so
`run_suite.classify_skip` and friends keep working for the self-tests and for
anything else that imports them.

Not a test file -- named so `unittest discover` (test*.py) does not load it.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

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

# A traceback is the useful part of a failure record; a 200 KB one is not, and
# the file has to stay readable. Keep the head and the tail, which is where the
# cause and the assertion live.
MAX_DETAIL_CHARS = 4000


def classify_skip(reason: str) -> str:
    """Which capability a skip blames, or "" when it is environmental."""
    if ENVIRONMENT_SKIP_RE.search(reason):
        return ""
    for name, pattern in CAPABILITY_PATTERNS.items():
        if pattern.search(reason):
            return name
    return ""


def trimmed(text: str) -> str:
    text = str(text or "")
    if len(text) <= MAX_DETAIL_CHARS:
        return text
    half = MAX_DETAIL_CHARS // 2
    return f"{text[:half]}\n... [{len(text) - MAX_DETAIL_CHARS} characters omitted] ...\n{text[-half:]}"


def probe_capability(name: str) -> str:
    """"" when the capability is really present, else why it is not.

    A preflight, so `--require X` fails on the missing dependency itself rather
    than on the wording of whatever skip happened to mention it -- and fails
    even when the affected suites skipped for some other reason, or were not
    selected at all.
    """
    import importlib.util

    if name == "ffmpeg":
        for tool in ("ffmpeg", "ffprobe"):
            found = shutil.which(tool)
            if not found:
                return f"{tool} is not on PATH"
            why = probe_executable(found)
            if why:
                return f"{tool} at {found} {why}"
        return ""
    if name == "powershell":
        # Probed by RUNNING it, exactly like ffmpeg above. `which` only proves a
        # file of that name exists: a wrapper that exits 2 satisfied --require
        # powershell, and every PowerShell test that then skipped was excused by
        # a capability the job never actually had (A06).
        reasons = []
        for tool in ("powershell", "pwsh"):
            found = shutil.which(tool)
            if not found:
                continue
            why = probe_executable(found, ["-NoLogo", "-NoProfile", "-Command", "exit 0"])
            if not why:
                return ""
            reasons.append(f"{tool} at {found} {why}")
        if reasons:
            return "; ".join(reasons)
        return "neither powershell nor pwsh is on PATH"
    modules = {"numpy": ("numpy",), "pyside6": ("PySide6", "PySide6.QtQml", "PySide6.QtQuick"),
               "wheel": ("wheel", "setuptools")}
    for module in modules.get(name, ()):  # pragma: no branch - table-driven
        if importlib.util.find_spec(module) is None:
            return f"{module} is not importable"
        why = probe_import(module)
        if why:
            return why
    return ""


def probe_executable(path: str, arguments: list[str] | None = None) -> str:
    """"" when the binary actually RUNS, else why it does not.

    `shutil.which` only proves a file with that name is on PATH. A wrapper that
    exits 2, or a binary missing a shared library, satisfied `--require` and the
    suite then skipped every test that needed it (R08, and again for PowerShell
    in A06). `arguments` is the bounded sentinel command for this tool; FFmpeg
    answers `-version`, a shell needs its own.
    """
    sentinel = list(arguments) if arguments else ["-version"]
    label = " ".join(sentinel)
    try:
        result = subprocess.run([path, *sentinel], capture_output=True, timeout=60)
    except OSError as exc:
        return f"could not be executed: {exc}"
    except subprocess.SubprocessError as exc:
        return f"did not answer `{label}`: {exc}"
    if result.returncode != 0:
        return f"exited {result.returncode} for `{label}`"
    return ""


def probe_import(module: str) -> str:
    """"" when the module actually IMPORTS, else why it does not.

    `find_spec` only proves a file is in the right place. A package that raises
    at import time -- a broken Qt install is the usual one -- has a spec and no
    working module, and that satisfied `--require pyside6` (R08). The import
    runs in a FRESH interpreter so a half-initialised module cannot poison this
    process, and it is bounded.
    """
    try:
        result = subprocess.run([sys.executable, "-c", f"import {module}"],
                                capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{module} could not be probed: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        return f"{module} fails at import: {detail[-1] if detail else 'unknown error'}"
    return ""


def _installed_version(distribution: str) -> str:
    """A package's version WITHOUT importing it.

    The report used to `__import__` PySide6 to read `__version__`, which starts
    a Qt runtime inside the runner's own process purely to write a line in a
    JSON file -- and a broken Qt install then took the report down with the
    thing it was meant to describe (A06). The metadata is on disk.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version(distribution)
    except PackageNotFoundError:
        return ""
    except Exception:                     # noqa: BLE001 - reporting must not fail the run
        return ""


def environment_report() -> dict:
    """Versions and tool paths a failed CI run needs and a log line loses.

    Every external call here is bounded: a hung `ffmpeg -version` used to be
    able to stall the one artifact a red job is read from.
    """
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
                first = subprocess.run([found, "-version"], capture_output=True,
                                       text=True, timeout=60).stdout.splitlines()
                report[tool + "_version"] = first[0] if first else ""
            except Exception as exc:      # noqa: BLE001 - reporting must not fail the run
                report[tool + "_version"] = f"unavailable: {exc}"
    for distribution in ("numpy", "PySide6", "setuptools", "wheel"):
        report[distribution] = _installed_version(distribution)
    return report


def write_results(path: str, results: list[dict], verdict: int, elapsed: float,
                  arguments, environment: dict | None = None) -> None:
    """One JSON file per run, so a red CI job is readable without the log.

    Written after EVERY module, not only at the end: a run that is killed, hangs
    or is cancelled still leaves the record of everything that had finished.
    `environment` is passed in so the incremental writes do not re-probe the
    tools on every module.
    """
    payload = {
        "verdict": "ok" if verdict == 0 else "failed",
        "exit_code": verdict,
        "seconds": round(elapsed, 3),
        "workers": arguments.jobs,
        "selection": arguments.filter or [],
        "required": arguments.require or [],
        "environment": environment if environment is not None else environment_report(),
        "modules": [
            {
                "module": item["module"],
                # ok / timeout / crash / not-run. A module killed at its ceiling
                # and one that never started are not each other, and neither of
                # them is a pass (A06).
                "status": item.get("status", "ok"),
                "seconds": round(item["seconds"], 3),
                "tests": item["run"],
                # Name AND text. A name alone says a thread failed but not what
                # it raised, so the record a red CI job is read from could not
                # be acted on without the log it was meant to replace (A06).
                "failures": [{"test": name, "detail": trimmed(text)}
                             for name, text in item["failures"]],
                "errors": [{"test": name, "detail": trimmed(text)}
                           for name, text in item["errors"]],
                "unexpected": item["unexpected"],
                "skipped": [{"test": name, "reason": why,
                             "capability": classify_skip(why)}
                            for name, why in item["skipped"]],
            }
            for item in results
        ],
    }
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and moved into place, so a reader never
        # sees half a file and a crash mid-write cannot destroy the last one.
        scratch = target.with_name(target.name + ".partial")
        scratch.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                           encoding="utf-8")
        os.replace(scratch, target)
    except OSError as exc:
        print(f"could not write {path}: {exc}", file=sys.stderr)
