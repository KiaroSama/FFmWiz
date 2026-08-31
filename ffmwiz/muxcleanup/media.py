# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional, Sequence, Tuple

from .constants import (
    FFPROBE_BIN,
    OPERATION_TIMEOUT_ENV_VAR,
    OPERATION_TIMEOUT_SECONDS,
    PARTIAL_MARKER,
    PROBE_TIMEOUT_SECONDS,
    PROCESS_KILL_GRACE_SECONDS,
    PROGRESS_POLL_SECONDS,
    TIMEOUT_RETURNCODE,
    VIDEO_EXTENSIONS,
)
from .colors import err, info
from .logsetup import LOGGER, command_to_text, log_command_output
from .models import MediaFile, StreamInfo, parse_duration_seconds
from .textutil import ProgressPrinter


class ScanResult(NamedTuple):
    """Everything a scan found: the files that probed cleanly and the ones that
    did not. Failures are returned rather than dropped so the caller decides."""

    files: List[MediaFile]
    failures: List[Path]


def operation_timeout_seconds() -> Optional[float]:
    """One timeout policy for every remux and copy. Returns None only when the
    user explicitly disables the bound."""
    raw = os.environ.get(OPERATION_TIMEOUT_ENV_VAR, "").strip()
    if not raw:
        return float(OPERATION_TIMEOUT_SECONDS)
    try:
        value = float(raw)
    except ValueError:
        LOGGER.warning("Ignoring invalid %s=%r", OPERATION_TIMEOUT_ENV_VAR, raw)
        return float(OPERATION_TIMEOUT_SECONDS)
    return None if value <= 0 else value


def read_new_output(handle, offset: int) -> Tuple[str, int]:
    """Read only what the child appended since `offset`.

    FFmpeg's -progress output grows for the whole run - an hour of remuxing is
    megabytes - so each poll reads the new tail rather than the whole file.
    The returned offset is the handle's own cookie, which is what text-mode
    seek() expects.
    """
    try:
        handle.seek(offset)
        return handle.read(), handle.tell()
    except (OSError, ValueError):
        return "", offset


def terminate_process(proc: subprocess.Popen, grace_seconds: float = PROCESS_KILL_GRACE_SECONDS) -> None:
    """Stop a child process we own: ask it to exit, then kill it if it will not.
    Safe to call on a process that has already finished."""
    if proc.poll() is not None:
        return

    try:
        proc.terminate()
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        LOGGER.warning("Process %s ignored terminate; killing it", proc.pid)
    except OSError as exc:
        LOGGER.warning("Could not terminate process %s: %s", proc.pid, exc)
        return

    try:
        proc.kill()
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        LOGGER.error("Process %s is still alive after kill", proc.pid)
    except OSError as exc:
        LOGGER.warning("Could not kill process %s: %s", proc.pid, exc)


def run_command(
    args: Sequence[str],
    timeout: Optional[float] = PROBE_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Run a short command to completion. A timeout is a controlled failure, not
    a hang: subprocess.run kills the child before raising."""
    LOGGER.debug("Command: %s", command_to_text(args))
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        LOGGER.error("Command timed out after %ss: %s", timeout, command_to_text(args))
        return subprocess.CompletedProcess(
            list(args),
            TIMEOUT_RETURNCODE,
            exc.stdout or "",
            f"Command timeout after {timeout} seconds",
        )

    log_command_output(Path(args[0]).name, proc.returncode, proc.stdout, proc.stderr)
    return proc


FFMPEG_TIME_KEY = "out_time_us="
FFMPEG_SIZE_KEY = "total_size="
ROBOCOPY_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


def read_ffmpeg_percent(text: str, duration_seconds: Optional[float]) -> Optional[float]:
    """Position reported by `-progress pipe:1`, as a percentage of the duration.

    FFmpeg appends key=value blocks, so the last out_time_us wins.
    """
    if not duration_seconds or duration_seconds <= 0:
        return None
    index = text.rfind(FFMPEG_TIME_KEY)
    if index < 0:
        return None
    raw = text[index + len(FFMPEG_TIME_KEY):].split("\n", 1)[0].strip()
    try:
        seconds = int(raw) / 1_000_000
    except ValueError:
        return None
    return max(0.0, min(100.0, seconds / duration_seconds * 100.0))


def read_ffmpeg_bytes(text: str) -> Optional[int]:
    """Bytes written so far, from the same `-progress` blocks.

    Measured on a real HEVC/Opus release: a stream copy can report
    `out_time_us=N/A` in every block while `total_size=` counts up normally.
    When that happens this is the only figure that moves, so the bar would
    otherwise sit at 0% for the whole file and then jump to 100%.
    """
    index = text.rfind(FFMPEG_SIZE_KEY)
    if index < 0:
        return None
    raw = text[index + len(FFMPEG_SIZE_KEY):].split("\n", 1)[0].strip()
    try:
        return int(raw)
    except ValueError:
        return None


def read_robocopy_percent(text: str) -> Optional[float]:
    """Robocopy's own percentage. It rewrites the figure with carriage returns,
    so the last match in what has been written so far is the current one."""
    matches = ROBOCOPY_PERCENT.findall(text)
    if not matches:
        return None
    try:
        return max(0.0, min(100.0, float(matches[-1])))
    except ValueError:
        return None


# The code this reports when the child could not be started at all, or its
# output could not be captured. It was 1 -- and robocopy's convention, which
# `output.robocopy_success` implements, is that 0-7 all mean SUCCESS. So a
# robocopy that never launched came back as rc=1 and both call sites in
# copying.py read it as a clean run: one logged "Extra files copied ... rc=1"
# having copied nothing, the other skipped its "robocopy failed" raise and
# died later on the misleading "robocopy did not create the output file".
#
# 127 is the shell convention for "command not found" and, more to the point,
# it is outside robocopy's success range. Every consumer here treats any
# non-zero code as failure (`!= 0` in logsetup and processing), so raising the
# value costs them nothing.
LAUNCH_FAILED_RETURNCODE = 127


def run_with_progress(
    args: Sequence[str],
    total_started_at: Optional[float] = None,
    timeout: Optional[float] = None,
    on_output: Optional[Callable[[str], None]] = None,
) -> subprocess.CompletedProcess:
    """Run a long command while showing its own elapsed timer.

    The child is always reaped: on timeout, on Ctrl+C and on any other error it
    is terminated (then killed) before this function returns or re-raises, so a
    result is never reported while the process is still writing output.
    """
    LOGGER.debug("Command: %s", command_to_text(args))
    progress = ProgressPrinter(total_started_at)
    label = Path(args[0]).name

    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout_file, \
                tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_file:
            try:
                proc = subprocess.Popen(
                    args,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    stdin=subprocess.DEVNULL,
                    text=True,
                )
            except OSError as exc:
                LOGGER.error("Could not start %s: %s", label, exc)
                return subprocess.CompletedProcess(
                    list(args), LAUNCH_FAILED_RETURNCODE, "", str(exc))

            timed_out = False
            capture_offset = 0
            try:
                while proc.poll() is None:
                    progress.tick()
                    if on_output is not None:
                        # The child writes to a temp file rather than a pipe, so
                        # reading it here cannot deadlock on a full pipe buffer
                        # the way reading its stdout directly would.
                        chunk, capture_offset = read_new_output(stdout_file, capture_offset)
                        if chunk:
                            on_output(chunk)
                    if timeout is not None and time.perf_counter() - progress.started_at > timeout:
                        timed_out = True
                        LOGGER.error("%s exceeded its %ss timeout; stopping it", label, timeout)
                        break
                    time.sleep(PROGRESS_POLL_SECONDS)
                else:
                    progress.tick(force=True)
            finally:
                terminate_process(proc)
                progress.close()

            returncode = proc.wait()
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
    except OSError as exc:
        LOGGER.exception("Failed to capture output of %s", label)
        return subprocess.CompletedProcess(
            list(args), LAUNCH_FAILED_RETURNCODE, "", str(exc))

    if timed_out:
        return subprocess.CompletedProcess(
            list(args),
            TIMEOUT_RETURNCODE,
            stdout,
            f"{stderr}\nCommand timeout after {timeout} seconds".strip(),
        )

    # When a caller consumes stdout as telemetry (FFmpeg's -progress stream,
    # robocopy's percentage) it is thousands of key=value lines, and logging it
    # on failure would bury the stderr message that actually says what broke.
    log_command_output(label, returncode, "" if on_output else stdout, stderr)
    return subprocess.CompletedProcess(list(args), returncode, stdout, stderr)


def tool_version(binary: str) -> str:
    """First line of `<tool> -version`, or why it could not be read.

    Which build of FFmpeg ran is the first question any report about a bad
    output raises, and it is not something the user can reconstruct later.

    UTF-8 explicitly, like `run_command` above. `text=True` alone decodes with
    the OS code page, and a build whose `configuration:` line names a non-ASCII
    path emits bytes that page has no character for. Measured on cp1252 with a
    Cyrillic prefix: the decode raised inside subprocess's own reader thread, so
    `except (OSError, SubprocessError)` never saw it -- a UnicodeDecodeError
    traceback went to the console and the version came back "unknown".
    """
    try:
        proc = subprocess.run(
            [binary, "-version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=PROBE_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable ({exc})"
    first = (proc.stdout or proc.stderr or "").strip().splitlines()
    return first[0] if first else "unknown"


def require_tool(binary: str) -> bool:
    found = shutil.which(binary)
    if found:
        LOGGER.info("Tool: %s -> %s | %s", binary, found, tool_version(binary))
    else:
        LOGGER.error("Required tool not found in PATH: %s", binary)
    return found is not None


def find_video_files(input_path: Path) -> List[Path]:
    files: List[Path] = []

    if input_path.is_file():
        if input_path.suffix.lower() in VIDEO_EXTENSIONS:
            return [input_path]
        return []

    for path in input_path.rglob("*"):
        # A leftover partial file (only possible after a hard kill) is not input.
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS and PARTIAL_MARKER not in path.name:
            files.append(path)

    return sorted(files, key=lambda p: str(p).lower())


def find_non_video_extensions(input_path: Path) -> List[str]:
    extensions = set()

    if input_path.is_file():
        suffix = input_path.suffix.lower() or "[no extension]"
        if suffix not in VIDEO_EXTENSIONS:
            return [suffix]
        return []

    for path in input_path.rglob("*"):
        if path.is_file():
            suffix = path.suffix.lower() or "[no extension]"
            if suffix not in VIDEO_EXTENSIONS:
                extensions.add(suffix)

    return sorted(extensions)


def probe_file(path: Path) -> Optional[MediaFile]:
    args = [
        FFPROBE_BIN,
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,channels,duration,bit_rate:stream_tags:stream_disposition=default:format=duration",
        "-of",
        "json",
        str(path),
    ]

    # Probing is a metadata read; if it has not answered in two minutes the file
    # or the drive is the problem, and waiting longer will not help.
    proc = run_command(args, timeout=PROBE_TIMEOUT_SECONDS)

    if proc.returncode != 0:
        LOGGER.error("Probe failed for %s", path)
        print(err(f"[PROBE FAILED] {path}"))
        if proc.stderr.strip():
            print(proc.stderr.strip())
        return None

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        LOGGER.error("Probe returned invalid JSON for %s", path)
        print(err(f"[PROBE FAILED] Invalid JSON from ffprobe: {path}"))
        return None

    raw_streams = data.get("streams") or []
    if not isinstance(raw_streams, list):
        LOGGER.error("Probe returned invalid streams data for %s: %r", path, raw_streams)
        print(err(f"[PROBE FAILED] Invalid streams data from ffprobe: {path}"))
        return None

    streams = [StreamInfo.from_ffprobe(s) for s in raw_streams if isinstance(s, dict)]
    container = data.get("format") or {}
    duration = parse_duration_seconds(container.get("duration")) if isinstance(container, dict) else None
    LOGGER.debug(
        "Probe OK: %s | video=%s audio=%s subtitle=%s attachment=%s",
        path,
        sum(1 for s in streams if s.codec_type == "video"),
        sum(1 for s in streams if s.codec_type == "audio"),
        sum(1 for s in streams if s.codec_type == "subtitle"),
        sum(1 for s in streams if s.codec_type == "attachment"),
    )
    return MediaFile(path=path, streams=streams, duration_seconds=duration)


def scan_files(files: List[Path]) -> ScanResult:
    scanned: List[MediaFile] = []
    failures: List[Path] = []
    LOGGER.info("Scanning %s file(s)", len(files))

    for i, file_path in enumerate(files, start=1):
        print(info(f"[{i}/{len(files)}] Scanning: {file_path.name}"))
        media = probe_file(file_path)
        if media:
            scanned.append(media)
        else:
            failures.append(file_path)

    LOGGER.info("Scan complete: probed=%s failed=%s of %s", len(scanned), len(failures), len(files))
    for path in failures:
        LOGGER.error("Probe failed: %s", path)
    return ScanResult(scanned, failures)
