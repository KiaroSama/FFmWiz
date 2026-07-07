# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Sequence

from .constants import FFPROBE_BIN, VIDEO_EXTENSIONS
from .colors import C, color, err, info
from .logsetup import LOGGER, command_to_text
from .models import MediaFile, StreamInfo
from .textutil import format_elapsed_time, terminal_width

def run_command(args: Sequence[str]) -> subprocess.CompletedProcess:
    LOGGER.debug("Running command: %s", command_to_text(args))
    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    LOGGER.debug("Command return code: %s", proc.returncode)
    if proc.stdout.strip():
        LOGGER.debug("Command stdout:\n%s", proc.stdout.strip())
    if proc.stderr.strip():
        LOGGER.debug("Command stderr:\n%s", proc.stderr.strip())
    return proc


def run_copy_command(args: Sequence[str], progress_started_at: Optional[float] = None) -> subprocess.CompletedProcess:
    LOGGER.debug("Running copy command: %s", command_to_text(args))

    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout_file, \
                tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_file:
            proc = subprocess.Popen(
                args,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
            )

            started_at = progress_started_at if progress_started_at is not None else time.perf_counter()
            last_elapsed_second = -1
            progress_line_started = False
            while proc.poll() is None:
                now = time.perf_counter()
                elapsed_second = int(now - started_at)
                if elapsed_second != last_elapsed_second:
                    if not progress_line_started:
                        sys.stdout.write("\n")
                        progress_line_started = True
                    sys.stdout.write("\r" + color(f"          Elapsed {format_elapsed_time(elapsed_second)}", C.BOLD + C.SUMMARY_ELAPSED))
                    sys.stdout.flush()
                    last_elapsed_second = elapsed_second
                time.sleep(0.2)

            final_elapsed_second = int(time.perf_counter() - started_at)
            if final_elapsed_second != last_elapsed_second:
                if not progress_line_started:
                    sys.stdout.write("\n")
                    progress_line_started = True
                sys.stdout.write("\r" + color(f"          Elapsed {format_elapsed_time(final_elapsed_second)}", C.BOLD + C.SUMMARY_ELAPSED))
                sys.stdout.flush()

            if progress_line_started:
                sys.stdout.write("\r" + (" " * terminal_width()) + "\r\n")
            else:
                sys.stdout.write("\r" + (" " * terminal_width()) + "\r")
            sys.stdout.flush()

            returncode = proc.wait()
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
    except OSError as exc:
        LOGGER.exception("Failed to start copy command")
        return subprocess.CompletedProcess(args, 16, "", str(exc))

    LOGGER.debug("Copy command return code: %s", returncode)
    if stdout.strip():
        LOGGER.debug("Copy command stdout:\n%s", stdout.strip())
    if stderr.strip():
        LOGGER.debug("Copy command stderr:\n%s", stderr.strip())

    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def run_ffmpeg_command(args: Sequence[str], progress_started_at: Optional[float] = None) -> subprocess.CompletedProcess:
    LOGGER.debug("Running FFmpeg command: %s", command_to_text(args))

    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout_file, \
                tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_file:
            proc = subprocess.Popen(
                args,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
            )

            started_at = progress_started_at or time.perf_counter()
            last_elapsed_second = -1
            progress_line_started = False
            while proc.poll() is None:
                now = time.perf_counter()
                elapsed_second = int(now - started_at)
                if elapsed_second != last_elapsed_second:
                    if not progress_line_started:
                        sys.stdout.write("\n")
                        progress_line_started = True
                    sys.stdout.write("\r" + color(f"          Elapsed {format_elapsed_time(elapsed_second)}", C.BOLD + C.SUMMARY_ELAPSED))
                    sys.stdout.flush()
                    last_elapsed_second = elapsed_second
                time.sleep(0.2)

            final_elapsed_second = int(time.perf_counter() - started_at)
            if final_elapsed_second != last_elapsed_second:
                if not progress_line_started:
                    sys.stdout.write("\n")
                    progress_line_started = True
                sys.stdout.write("\r" + color(f"          Elapsed {format_elapsed_time(final_elapsed_second)}", C.BOLD + C.SUMMARY_ELAPSED))
                sys.stdout.flush()

            if progress_line_started:
                sys.stdout.write("\r" + (" " * terminal_width()) + "\r\n")
            else:
                sys.stdout.write("\r" + (" " * terminal_width()) + "\r")
            sys.stdout.flush()

            returncode = proc.wait()
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
    except OSError as exc:
        LOGGER.exception("Failed to start FFmpeg")
        return subprocess.CompletedProcess(args, 1, "", str(exc))

    LOGGER.debug("FFmpeg return code: %s", returncode)
    if stdout.strip():
        LOGGER.debug("FFmpeg stdout:\n%s", stdout.strip())
    if stderr.strip():
        LOGGER.debug("FFmpeg stderr:\n%s", stderr.strip())

    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def require_tool(binary: str) -> bool:
    found = shutil.which(binary)
    LOGGER.info("Tool check: %s -> %s", binary, found or "not found")
    return found is not None


def find_video_files(input_path: Path) -> List[Path]:
    files: List[Path] = []

    if input_path.is_file():
        if input_path.suffix.lower() in VIDEO_EXTENSIONS:
            return [input_path]
        return []

    for path in input_path.rglob("*"):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
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
        "stream=index,codec_type,codec_name,channels,duration,bit_rate:stream_tags:stream_disposition=default",
        "-of",
        "json",
        str(path),
    ]

    proc = run_command(args)

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
    LOGGER.info(
        "Probe OK: %s | video=%s audio=%s subtitle=%s attachment=%s",
        path,
        sum(1 for s in streams if s.codec_type == "video"),
        sum(1 for s in streams if s.codec_type == "audio"),
        sum(1 for s in streams if s.codec_type == "subtitle"),
        sum(1 for s in streams if s.codec_type == "attachment"),
    )
    return MediaFile(path=path, streams=streams)


def scan_files(files: List[Path]) -> List[MediaFile]:
    result: List[MediaFile] = []
    LOGGER.info("Scanning %s file(s)", len(files))

    for i, file_path in enumerate(files, start=1):
        print(info(f"[{i}/{len(files)}] Scanning: {file_path.name}"))
        media = probe_file(file_path)
        if media:
            result.append(media)

    LOGGER.info("Scan complete: %s/%s file(s) probed successfully", len(result), len(files))
    return result
