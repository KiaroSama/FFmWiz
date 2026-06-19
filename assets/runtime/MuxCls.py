#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MuxCls

What it does:
- Scans a folder recursively for video files.
- Shows audio and subtitle streams with index, language, title, codec, channels.
- Lets you choose which audio and subtitle streams to keep.
- Saves processed files into a new output folder with the same folder structure and file names.
- Uses FFmpeg stream copy only: no re-encoding, no quality loss.

Requirements:
- ffmpeg and ffprobe must be installed and available in PATH.

Recommended usage:
    python MuxCls.py
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".webm", ".mov", ".avi"}

FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"
ROBOCOPY_BIN = "robocopy"
APP_VERSION = "1.3.0"

LOGGER = logging.getLogger("MuxCls")
LOG_FILE: Optional[Path] = None
_EXECUTION_ID: str = ""
_SESSION_START_MONOTONIC: Optional[float] = None
_SHUTDOWN_LOGGED = False
_DEFAULT_LOG_COMPONENT = "MuxCls"

# Secret/credential redaction applied to every log record (conservative: a value
# is masked only when introduced by a known sensitive key, a Bearer token, or
# embedded URL credentials, so ordinary arguments such as crf=23 are untouched).
_SECRET_KEY_RE = re.compile(
    r"(?i)\b(pass(?:word|wd)?|tokens?|api[_-]?keys?|secrets?|access[_-]?tokens?|"
    r"refresh[_-]?tokens?|client[_-]?secrets?|private[_-]?keys?|authorization|"
    r"signing[_-]?secret|webhook[_-]?secret)\b(\s*[:=]\s*|\s+)([^\s,;\"']+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_URL_CRED_RE = re.compile(r"://([^:@/\s]+):([^@/\s]+)@")


def redact_secrets(text: object) -> str:
    """Mask credentials/tokens in a log string without altering ordinary text."""
    if text is None:
        return ""
    out = str(text)
    try:
        out = _BEARER_RE.sub("Bearer [REDACTED]", out)
        out = _SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", out)
        out = _URL_CRED_RE.sub(r"://\1:[REDACTED]@", out)
    except Exception:
        return out
    return out


class _LogContextFilter(logging.Filter):
    """Guarantee a [COMPONENT] field on every record and redact secrets from the
    fully-rendered message before it is written to the file."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "component", None):
            record.component = _DEFAULT_LOG_COMPONENT
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        record.msg = redact_secrets(rendered)
        record.args = None
        return True


class _UtcFormatter(logging.Formatter):
    """Formatter whose timestamps are UTC, second-precision, no milliseconds."""

    converter = time.gmtime

    def formatTime(self, record, datefmt=None):  # noqa: N802
        ct = self.converter(record.created)
        return time.strftime(datefmt or "%Y-%m-%d %H:%M:%S", ct)


# ANSI colors. No external dependency required.
# Use bright variants so text stays readable on black terminal backgrounds.
ENABLE_COLORS = True


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"
    ORANGE = "\033[38;5;208m"
    GOLD = "\033[38;5;220m"
    AMBER = "\033[38;5;214m"
    LIME = "\033[38;5;154m"
    MINT = "\033[38;5;121m"
    EMERALD = "\033[38;5;48m"
    TEAL = "\033[38;5;37m"
    AQUA = "\033[38;5;51m"
    SKY = "\033[38;5;117m"
    AZURE = "\033[38;5;75m"
    INDIGO = "\033[38;5;99m"
    VIOLET = "\033[38;5;135m"
    PURPLE = "\033[38;5;141m"
    LAVENDER = "\033[38;5;183m"
    PINK = "\033[38;5;213m"
    ROSE = "\033[38;5;204m"
    CORAL = "\033[38;5;209m"
    SALMON = "\033[38;5;210m"
    STEEL = "\033[38;5;110m"
    SILVER = "\033[38;5;250m"
    LAUNCHER_PINK = "\033[38;2;255;50;115m"
    LOG_YELLOW = "\033[38;2;255;240;74m"
    BACK_ORANGE = "\033[38;5;166m"
    QUIT_GREEN = "\033[38;5;32m"
    FILE_RED = "\033[38;2;255;20;20m"
    SCAN_HEADER = "\033[38;2;68;221;255m"
    SUMMARY_HEADER = "\033[38;2;170;255;82m"
    VERIFY_HEADER = "\033[38;2;255;115;225m"
    NOTE_BLUE = "\033[38;2;80;190;255m"
    NON_VIDEO_NOTE = "\033[38;2;255;210;95m"
    FOUND_LABEL = "\033[38;2;90;255;190m"
    FOUND_VALUE = "\033[38;2;255;235;120m"
    FOUND_DETAIL_VALUE = "\033[38;2;120;220;255m"
    EXAMPLE_COLOR = "\033[38;2;255;215;80m"
    ACTION_SEPARATOR = "\033[38;2;75;130;190m"
    CONFIRM_HEADER = "\033[38;2;255;155;60m"
    PROCESS_HEADER = "\033[38;2;80;255;205m"
    DONE_HEADER = "\033[38;2;145;255;95m"
    PROCESS_SEPARATOR = "\033[38;2;80;150;210m"
    PROCESS_DONE = "\033[38;2;90;255;135m"
    YES_NO_HINT = "\033[38;5;178m"
    UNKNOWN_LANGUAGE = "\033[38;5;244m"
    SETTING_LABEL = "\033[38;2;110;210;255m"
    SETTING_VALUE = "\033[38;2;245;245;245m"
    SETTING_INPUT_PATH = "\033[38;2;70;255;210m"
    SETTING_OUTPUT_BASE = "\033[38;2;255;105;180m"
    SETTING_OUTPUT_ROOT = "\033[38;2;190;255;70m"
    SETTING_MODE = "\033[38;2;180;145;255m"
    SETTING_AUDIO = "\033[38;2;120;255;170m"
    SETTING_SUBTITLE = "\033[38;2;255;150;220m"
    SETTING_TRUE = "\033[38;2;95;255;120m"
    SETTING_FALSE = "\033[38;2;255;95;95m"
    SUMMARY_FAILED = "\033[38;2;255;60;72m"
    SUMMARY_EXTRA_FAILED = "\033[38;2;255;95;120m"
    SUMMARY_SIZE_DIFF = "\033[38;2;0;170;125m"
    SUMMARY_ELAPSED = "\033[38;2;205;122;42m"


LANGUAGE_COLORS = (
    C.GREEN,
    C.CYAN,
    C.MAGENTA,
    C.YELLOW,
    C.BLUE,
    C.ORANGE,
    C.GOLD,
    C.LIME,
    C.MINT,
    C.EMERALD,
    C.TEAL,
    C.AQUA,
    C.SKY,
    C.AZURE,
    C.INDIGO,
    C.VIOLET,
    C.PURPLE,
    C.LAVENDER,
    C.PINK,
    C.ROSE,
)

HEADER_COLOR = C.BOLD + C.LAUNCHER_PINK
HEADER_SEPARATOR_COLOR = C.LAUNCHER_PINK
SCAN_SEPARATOR_COLOR = C.BOLD + C.AQUA
FILE_LINE_COLOR = C.BOLD + C.FILE_RED
PROMPT_LABEL_COLOR = C.BOLD + C.LAUNCHER_PINK
PROMPT_DEFAULT_COLOR = C.BOLD + C.GREEN
FOUND_LABEL_COLOR = C.BOLD + C.FOUND_LABEL
FOUND_VALUE_COLOR = C.BOLD + C.FOUND_VALUE
FOUND_DETAIL_VALUE_COLOR = C.BOLD + C.FOUND_DETAIL_VALUE
EXAMPLE_TEXT_COLOR = C.EXAMPLE_COLOR
ACTION_SEPARATOR_COLOR = C.BOLD + C.ACTION_SEPARATOR
PROCESS_SEPARATOR_COLOR = C.BOLD + C.PROCESS_SEPARATOR
PROCESS_DONE_COLOR = C.BOLD + C.PROCESS_DONE
YES_NO_HINT_COLOR = C.YES_NO_HINT
UNKNOWN_LANGUAGE_COLOR = C.UNKNOWN_LANGUAGE
SETTING_LABEL_COLOR = C.BOLD + C.SETTING_LABEL
SETTING_VALUE_COLOR = C.SETTING_VALUE
SETTING_INPUT_PATH_COLOR = C.SETTING_INPUT_PATH
SETTING_OUTPUT_BASE_COLOR = C.SETTING_OUTPUT_BASE
SETTING_OUTPUT_ROOT_COLOR = C.SETTING_OUTPUT_ROOT
SETTING_MODE_COLOR = C.BOLD + C.SETTING_MODE
SETTING_AUDIO_COLOR = C.SETTING_AUDIO
SETTING_SUBTITLE_COLOR = C.SETTING_SUBTITLE
SETTING_TRUE_COLOR = C.BOLD + C.SETTING_TRUE
SETTING_FALSE_COLOR = C.BOLD + C.SETTING_FALSE


def color(text: object, code: str) -> str:
    if not ENABLE_COLORS:
        return str(text)
    return f"{code}{text}{C.RESET}"


def ok(text: object) -> str:
    return color(text, C.GREEN)


def warn(text: object) -> str:
    return color(text, C.YELLOW)


def err(text: object) -> str:
    return color(text, C.RED)


def info(text: object) -> str:
    return color(text, C.CYAN)


def dim(text: object) -> str:
    return color(text, C.GRAY)


def enable_windows_ansi() -> None:
    if os.name != "nt":
        return

    try:
        import ctypes

        ENABLE_PROCESSED_OUTPUT = 0x0001
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32 = ctypes.windll.kernel32
        enabled = False

        for handle_id in (-11, -12):
            handle = kernel32.GetStdHandle(handle_id)
            if not handle or handle == -1:
                continue

            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                new_mode = mode.value | ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                if kernel32.SetConsoleMode(handle, new_mode):
                    enabled = True

        if not enabled:
            os.system("")
    except Exception:
        # Color is cosmetic. If VT mode cannot be enabled, continue without failing startup.
        try:
            os.system("")
        except Exception:
            pass
        return


def command_to_text(args: Sequence[object]) -> str:
    return subprocess.list2cmdline([str(arg) for arg in args])


def setup_logging() -> Optional[Path]:
    global LOG_FILE, _EXECUTION_ID, _SESSION_START_MONOTONIC

    _EXECUTION_ID = uuid.uuid4().hex[:12]
    _SESSION_START_MONOTONIC = time.monotonic()
    try:
        embedded_log = os.environ.get("FFMWIZ_LOG_FILE")
        if embedded_log:
            LOG_FILE = Path(embedded_log)
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        else:
            log_root = Path(__file__).resolve().parent / "Logs"
            log_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
            candidate = log_root / f"muxcls_{stamp}_UTC.log"
            if candidate.exists():
                candidate = log_root / f"muxcls_{stamp}_UTC_{_EXECUTION_ID}.log"
            LOG_FILE = candidate

        LOGGER.setLevel(logging.DEBUG)
        LOGGER.handlers.clear()
        LOGGER.propagate = False

        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(_UtcFormatter(
            "[%(asctime)s UTC] [%(levelname)s] [%(component)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        handler.addFilter(_LogContextFilter())
        LOGGER.addHandler(handler)
        atexit.register(shutdown_logging)
        LOGGER.info("MuxCls started")
        LOGGER.info("MuxCls version: %s", APP_VERSION)
        LOGGER.info("Execution ID: %s", _EXECUTION_ID)
        LOGGER.info("Python: %s", sys.version.replace("\n", " "))
        LOGGER.info("OS: %s (arch=%s)", platform.platform(), platform.machine())
        LOGGER.info("Process ID: %s", os.getpid())
        LOGGER.info("Log file: %s", LOG_FILE)
        LOGGER.info("Command line: %s", command_to_text(sys.argv))
        return LOG_FILE
    except OSError as exc:
        print(warn(f"Persistent file logging is unavailable: {exc}. Continuing without a log file."))
        LOG_FILE = None
        return None


def shutdown_logging(exit_code: object = None) -> None:
    """Flush and close all log handlers once, recording total session duration.
    Safe to call multiple times (registered via atexit and callable manually)."""
    global _SHUTDOWN_LOGGED
    if _SHUTDOWN_LOGGED or not LOGGER.handlers:
        return
    _SHUTDOWN_LOGGED = True
    try:
        if _SESSION_START_MONOTONIC is not None:
            elapsed = time.monotonic() - _SESSION_START_MONOTONIC
            msg = f"MuxCls session ended; total duration={elapsed:.2f}s"
            if exit_code is not None:
                msg += f"; exit_code={exit_code}"
            LOGGER.info(msg)
        for handler in list(LOGGER.handlers):
            try:
                handler.flush()
                handler.close()
            except Exception:
                pass
            LOGGER.removeHandler(handler)
    except Exception:
        pass


@dataclass
class StreamInfo:
    index: int
    codec_type: str
    codec_name: str = ""
    language: str = ""
    title: str = ""
    channels: Optional[int] = None
    disposition_default: int = 0
    size_bytes: Optional[int] = None

    @classmethod
    def from_ffprobe(cls, raw: Dict[str, Any]) -> "StreamInfo":
        tags = raw.get("tags") or {}
        disposition = raw.get("disposition") or {}
        return cls(
            index=int(raw.get("index", -1)),
            codec_type=str(raw.get("codec_type", "")),
            codec_name=str(raw.get("codec_name", "")),
            language=str(tags.get("language", "") or "und"),
            title=str(tags.get("title", "") or ""),
            channels=raw.get("channels"),
            disposition_default=int(disposition.get("default", 0) or 0),
            size_bytes=stream_size_bytes_from_ffprobe(raw, tags),
        )


@dataclass
class MediaFile:
    path: Path
    streams: List[StreamInfo]

    @property
    def video_streams(self) -> List[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "video"]

    @property
    def audio_streams(self) -> List[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "audio"]

    @property
    def subtitle_streams(self) -> List[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "subtitle"]

    @property
    def attachment_streams(self) -> List[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "attachment"]


@dataclass
class StreamMetadataEdit:
    codec_type: str
    match_indexes: List[int] = field(default_factory=list)
    match_languages: List[str] = field(default_factory=list)
    language: str = ""
    title: str = ""


@dataclass
class SelectionRules:
    audio_mode: str
    audio_languages: List[str]
    audio_titles: List[str]
    audio_indexes: List[int]

    subtitle_mode: str
    subtitle_languages: List[str]
    subtitle_titles: List[str]
    subtitle_indexes: List[int]

    keep_attachments: bool
    keep_metadata: bool
    keep_chapters: bool
    overwrite: bool
    copy_non_video_files: bool = True
    selection_style: str = "advanced"
    metadata_edits: List[StreamMetadataEdit] = field(default_factory=list)


AUDIO_BY_LANGUAGE = "1"
AUDIO_BY_TITLE = "2"
AUDIO_BY_INDEX = "3"
AUDIO_ALL = "4"
AUDIO_NONE = "5"

SUBTITLE_ALL = "1"
SUBTITLE_BY_LANGUAGE = "2"
SUBTITLE_BY_TITLE = "3"
SUBTITLE_BY_INDEX = "4"
SUBTITLE_NONE = "5"


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


def terminal_width() -> int:
    return max(20, shutil.get_terminal_size((80, 20)).columns)


def separator_line(code: str = HEADER_SEPARATOR_COLOR) -> str:
    return color("=" * terminal_width(), code)


def center_for_terminal(text: str) -> str:
    return text.center(terminal_width())


def header_color(text: str) -> str:
    normalized = text.strip().upper()
    if normalized == "SCAN REPORT: AUDIO AND SUBTITLE STREAMS":
        return C.BOLD + C.SCAN_HEADER
    if normalized == "UNIQUE STREAM SUMMARY":
        return C.BOLD + C.SUMMARY_HEADER
    if normalized == "VERIFY OUTPUT FOLDER":
        return C.BOLD + C.VERIFY_HEADER
    if normalized == "CONFIRM SETTINGS":
        return C.BOLD + C.CONFIRM_HEADER
    if normalized == "PROCESSING FILES":
        return C.BOLD + C.PROCESS_HEADER
    if normalized == "DONE":
        return C.BOLD + C.DONE_HEADER
    return HEADER_COLOR


def print_header(text: str, leading_blank: bool = True) -> None:
    if leading_blank:
        print()
    code = header_color(text)
    print(color(center_for_terminal(text), code))
    print(separator_line(code))


def print_setting(label: str, value: object) -> None:
    normalized = label.lower()

    if normalized == "metadata edits" and isinstance(value, list):
        print(f"{color(label + ':', SETTING_LABEL_COLOR)} {color(format_metadata_edits(value), SETTING_VALUE_COLOR)}")
        return

    if normalized in {"audio languages", "subtitle languages"} and isinstance(value, list):
        print(f"{color(label + ':', SETTING_LABEL_COLOR)} {format_language_list(value, SETTING_AUDIO_COLOR if normalized.startswith('audio') else SETTING_SUBTITLE_COLOR)}")
        return

    if isinstance(value, bool):
        value_color = SETTING_TRUE_COLOR if value else SETTING_FALSE_COLOR
    elif normalized == "input":
        value_color = SETTING_INPUT_PATH_COLOR
    elif normalized == "output base":
        value_color = SETTING_OUTPUT_BASE_COLOR
    elif normalized == "output root":
        value_color = SETTING_OUTPUT_ROOT_COLOR
    elif normalized.endswith("mode"):
        value_color = SETTING_MODE_COLOR
    elif normalized.startswith("audio"):
        value_color = SETTING_AUDIO_COLOR
    elif normalized.startswith("subtitle"):
        value_color = SETTING_SUBTITLE_COLOR
    elif normalized in {"overwrite", "copy non-video files"}:
        value_color = SETTING_FALSE_COLOR if not value else SETTING_TRUE_COLOR
    else:
        value_color = SETTING_VALUE_COLOR

    print(f"{color(label + ':', SETTING_LABEL_COLOR)} {color(value, value_color)}")


EXIT_TOKENS = {"q", "quit", "exit"}


class MenuExit(Exception):
    pass


class MenuBack(Exception):
    pass


def prompt_label(prompt: str) -> str:
    return prompt.strip().rstrip(":").strip()


def option_suffix(default: Optional[str], allow_back: bool, show_default: bool = True, back_token: str = "0") -> str:
    parts: List[str] = []
    if default and show_default:
        parts.append(f"[{default}]")

    nav_parts: List[str] = []
    nav_parts.append("quit=exit")
    if allow_back:
        nav_parts.append(f"back={back_token}")
    parts.append(f"{{{', '.join(nav_parts)}}}")

    return " ".join(parts)


def colored_option_suffix(default: Optional[str], allow_back: bool, show_default: bool = True, back_token: str = "0") -> str:
    parts: List[str] = []
    if default and show_default:
        parts.append(color(f"[{default}]", PROMPT_DEFAULT_COLOR))

    nav_parts = [color("quit=exit", C.QUIT_GREEN)]
    if allow_back:
        nav_parts.append(color(f"back={back_token}", C.BACK_ORANGE))
    parts.append("{" + ", ".join(nav_parts) + "}")

    return " ".join(parts)


UNKNOWN_LANGUAGE_DISPLAY = "*uknown"
UNKNOWN_LANGUAGE_INPUTS = {"", "und", "unk", "unknown", "undefined", "uknown", "*uknown"}


def normalize_language_code(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized in UNKNOWN_LANGUAGE_INPUTS:
        return "und"
    return normalized


def is_unknown_language(value: str) -> bool:
    return normalize_language_code(value) == "und"


def display_language(value: str) -> str:
    if is_unknown_language(value):
        return UNKNOWN_LANGUAGE_DISPLAY
    return value or UNKNOWN_LANGUAGE_DISPLAY


def color_language_value(value: str, fallback_color: str = FOUND_VALUE_COLOR) -> str:
    if is_unknown_language(value):
        return color(display_language(value), UNKNOWN_LANGUAGE_COLOR)
    return color(display_language(value), fallback_color)


def format_language_list(values: Iterable[str], fallback_color: str = FOUND_VALUE_COLOR) -> str:
    rendered = [color_language_value(value, fallback_color) for value in values]
    return ", ".join(rendered) if rendered else "none"


def format_language_list_from_text(text: str, fallback_color: str = FOUND_VALUE_COLOR) -> str:
    values = [value.strip() for value in text.split(",") if value.strip()]
    return format_language_list(values, fallback_color)


def color_found_text(text: str) -> str:
    marker = "Found: "
    index = text.find(marker)
    if index == -1:
        return text
    start = index
    value_start = start + len(marker)
    return (
        text[:start]
        + color(marker.rstrip(), FOUND_LABEL_COLOR)
        + " "
        + format_language_list_from_text(text[value_start:], FOUND_VALUE_COLOR)
    )


def color_found_detail_text(text: str) -> str:
    marker = " found: "
    index = text.lower().find(marker)
    if index == -1:
        return text
    label_end = index + len(marker)
    return text[:label_end] + format_language_list_from_text(text[label_end:], FOUND_DETAIL_VALUE_COLOR)


def color_example_text(text: str) -> str:
    start = text.find("(example")
    if start == -1:
        return text
    end = text.find(")", start)
    if end == -1:
        return text
    end += 1
    return text[:start] + color(text[start:end], EXAMPLE_TEXT_COLOR) + text[end:]


def format_prompt_label(prompt: str) -> str:
    return color_example_text(color_found_detail_text(color_found_text(prompt_label(prompt))))


def prompt_text(prompt: str, default: Optional[str], allow_back: bool, show_default: bool = True, back_token: str = "0") -> str:
    label = format_prompt_label(prompt)
    suffix = colored_option_suffix(default, allow_back, show_default=show_default, back_token=back_token)
    return f"{label} {suffix}{color(':', C.GRAY)} "


def read_rendered_input(rendered_prompt: str, default: Optional[str], allow_back: bool, back_token: str = "0") -> str:
    while True:
        raw = input(rendered_prompt).strip()
        lowered = raw.lower()

        if lowered in EXIT_TOKENS:
            raise MenuExit

        back_tokens = {back_token.lower()}
        if back_token.lower() == "b":
            back_tokens.add("back")
        if allow_back and lowered in back_tokens:
            raise MenuBack

        if raw == "0" and not allow_back:
            print(warn("Back is not available here."))
            continue

        if raw == "0" and back_token == "0":
            if allow_back:
                raise MenuBack
            print(warn("Back is not available here."))
            continue

        if not raw and default is not None:
            return default

        return raw


def read_menu_input(
    prompt: str,
    default: Optional[str] = None,
    allow_back: bool = True,
    show_default: bool = True,
    back_token: str = "0",
) -> str:
    return read_rendered_input(prompt_text(prompt, default, allow_back, show_default, back_token), default, allow_back, back_token)


def ask_text(prompt: str, allow_back: bool = True, back_token: str = "0") -> str:
    while True:
        raw = read_menu_input(prompt, allow_back=allow_back, back_token=back_token)
        if raw:
            return raw
        print(err("Value cannot be empty."))


def ask_path(prompt: str, must_exist: bool = False, allow_back: bool = True) -> Path:
    while True:
        raw = read_menu_input(prompt, allow_back=allow_back)
        if not raw:
            print(err("Path cannot be empty."))
            continue

        path = normalize_path_text(raw)

        if must_exist and not path.exists():
            print(err(f"Path does not exist: {path}"))
            continue

        return path


def normalize_path_text(raw: str) -> Path:
    return Path(raw.strip().strip('"').strip("'")).expanduser()


def absolute_path_for_display(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        if path.is_absolute():
            return path
        return (Path.cwd() / path).absolute()


def input_path_from_args(args: Sequence[str]) -> Optional[Path]:
    if not args:
        return None

    # The launchers pass the dropped file or folder as the first argument.
    raw = args[0].strip()
    if not raw:
        return None

    return normalize_path_text(raw)


def ask_output_base_path(input_root: Path) -> Path:
    default_base = input_root.parent
    while True:
        raw = read_menu_input("Output folder path [Enter=input parent folder]", allow_back=True)
        if not raw:
            print(info(f"Using input parent folder: {absolute_path_for_display(default_base)}"))
            return default_base

        if raw.lower() in {"y", "yes", "n", "no", "y/n", "yes/no", "n/y", "no/yes"}:
            print(warn("Please enter a folder path, or press Enter to use the input parent folder."))
            continue

        path = normalize_path_text(raw)
        if path.exists() and not path.is_dir():
            print(err(f"Output path exists but is not a folder: {path}"))
            continue

        if path.suffix.lower() in VIDEO_EXTENSIONS:
            print(err("Output path must be a folder, not a media file name."))
            continue

        if path.suffix and not path.exists():
            print(warn(f"This output folder name has an extension: {path.name}"))
            try:
                use_extension_path = ask_yes_no("Use this as a folder path?", False)
            except MenuBack:
                print(warn("Back. Returning to output folder path."))
                continue
            if not use_extension_path:
                continue

        if not path.is_absolute():
            resolved = absolute_path_for_display(path)
            print(warn(f"Relative output folder will resolve to: {resolved}"))
            try:
                use_relative_path = ask_yes_no("Use this relative output folder?", False)
            except MenuBack:
                print(warn("Back. Returning to output folder path."))
                continue
            if not use_relative_path:
                continue
            return resolved

        return path


def yes_no_choice_suffix(default: bool) -> str:
    if default:
        return f"{color('(y/n)', YES_NO_HINT_COLOR)} {color('[Y]', PROMPT_DEFAULT_COLOR)}"
    return f"{color('(y/n)', YES_NO_HINT_COLOR)} {color('[n]', PROMPT_DEFAULT_COLOR)}"


def ask_yes_no(prompt: str, default: bool = True, allow_back: bool = True) -> bool:
    while True:
        rendered_prompt = (
            f"{prompt} {yes_no_choice_suffix(default)} "
            f"{colored_option_suffix(None, allow_back, show_default=False)}{color(':', C.GRAY)} "
        )
        raw = read_rendered_input(rendered_prompt, default=None, allow_back=allow_back).lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print(warn("Please enter y or n, or press Enter for the default."))


def print_metadata_note() -> None:
    print("Metadata note:")
    print(dim("  Keeping metadata preserves supported titles, language tags, chapters, and stream labels."))


def ask_choice(prompt: str, valid: Iterable[str], default: str, allow_back: bool = True) -> str:
    valid_set = {v.lower() for v in valid}
    while True:
        raw = read_menu_input(prompt, default=default, allow_back=allow_back).lower()
        if raw in valid_set:
            return raw
        print(warn(f"Invalid choice. Valid options: {', '.join(sorted(valid_set))}"))


def numbered_option(value: str, text: str, is_default: bool) -> str:
    suffix = f" {color('(default)', PROMPT_DEFAULT_COLOR)}" if is_default else ""
    return f"{color(value + '.', C.BOLD + C.GREEN)} {color_example_text(color_found_text(text))}{suffix}"


def numbered_choice_prompt(prompt: str, allow_back: bool, colon_after_prompt: bool) -> str:
    suffix = colored_option_suffix(None, allow_back, show_default=False)
    if colon_after_prompt:
        return f"{prompt}: {suffix}{color(':', C.GRAY)} "
    return f"{prompt} {suffix}{color(':', C.GRAY)} "


def ask_numbered_menu(
    title: str,
    options: Sequence[Tuple[str, str]],
    default: str,
    prompt: str,
    allow_back: bool = True,
    leading_blank: bool = True,
    colon_after_prompt: bool = False,
    notes: Optional[Sequence[str]] = None,
) -> str:
    valid_set = {value.lower() for value, _ in options}

    if leading_blank:
        print()
    print(f"{title}:")
    for note in notes or ():
        print(format_prompt_label(note))
    for value, text in options:
        print(numbered_option(value, text, value == default))

    rendered_prompt = numbered_choice_prompt(prompt, allow_back, colon_after_prompt)
    while True:
        raw = read_rendered_input(rendered_prompt, default=default, allow_back=allow_back).lower()
        if raw in valid_set:
            return raw
        print(warn(f"Invalid choice. Valid options: {', '.join(sorted(valid_set))}"))


def parse_csv_text(raw: str) -> List[str]:
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def ask_csv_text_required(prompt: str) -> List[str]:
    while True:
        values = parse_csv_text(ask_text(prompt))
        if values:
            return values
        print(warn("Please enter at least one value."))


def ask_language_codes_required(prompt: str) -> List[str]:
    return [normalize_language_code(value) for value in ask_csv_text_required(prompt)]


def parse_csv_int(raw: str) -> List[int]:
    result: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError:
            print(warn(f"Ignored invalid number: {part}"))
    return result


def ask_csv_int_required(prompt: str, available_indexes: Optional[List[int]] = None) -> List[int]:
    while True:
        indexes = parse_csv_int(ask_text(prompt + " (use b to go back)", back_token="b"))
        if not indexes:
            print(warn("Please enter at least one stream index."))
            continue

        if available_indexes is not None:
            unknown_indexes = sorted(set(indexes) - set(available_indexes))
            if unknown_indexes:
                print(warn(f"These indexes were not found in the scan: {format_index_list(unknown_indexes)}"))
                try:
                    keep_unknown = ask_yes_no("Keep these indexes anyway?", False)
                except MenuBack:
                    print(warn("Back. Returning to stream index entry."))
                    continue
                if not keep_unknown:
                    continue

        return indexes


def format_elapsed_time(seconds: float) -> str:
    if seconds < 0:
        LOGGER.warning("Negative elapsed time received: %s", seconds)
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def parse_int_value(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_float_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_duration_seconds(value: Any) -> Optional[float]:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if ":" not in text:
        return parse_float_value(text)

    parts = text.split(":")
    try:
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + float(part)
        return seconds
    except ValueError:
        return None


def tag_value_by_prefix(tags: Dict[str, Any], prefix: str) -> Optional[Any]:
    wanted = prefix.upper()
    for key, value in tags.items():
        if str(key).upper().startswith(wanted):
            return value
    return None


def stream_size_bytes_from_ffprobe(raw: Dict[str, Any], tags: Dict[str, Any]) -> Optional[int]:
    exact_bytes = parse_int_value(tag_value_by_prefix(tags, "NUMBER_OF_BYTES"))
    if exact_bytes is not None and exact_bytes >= 0:
        return exact_bytes

    bit_rate = parse_int_value(raw.get("bit_rate"))
    if bit_rate is None:
        bit_rate = parse_int_value(tag_value_by_prefix(tags, "BPS"))

    duration = parse_duration_seconds(raw.get("duration"))
    if duration is None:
        duration = parse_duration_seconds(tag_value_by_prefix(tags, "DURATION"))

    if bit_rate is None or bit_rate <= 0 or duration is None or duration <= 0:
        return None

    return int(round((bit_rate * duration) / 8))


def format_stream_size(size_bytes: Optional[int]) -> str:
    if size_bytes is None:
        return "-"
    if size_bytes < 0:
        return "-"

    kb = size_bytes / 1024
    mb = kb / 1024
    gb = mb / 1024

    if gb >= 1:
        return f"{gb:.1f} GB"
    if mb >= 1:
        return f"{mb:.1f} MB"
    return f"{kb:.0f} KB"


def format_size_difference(size_bytes: int) -> str:
    sign = "+" if size_bytes > 0 else "-" if size_bytes < 0 else ""
    absolute = abs(size_bytes)
    kb = absolute / 1024
    mb = kb / 1024
    gb = mb / 1024

    if mb < 5:
        return f"{sign}{kb:.2f} KB"
    if gb >= 1:
        return f"{sign}{gb:.2f} GB"
    return f"{sign}{mb:.2f} MB"


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


def format_stream(stream: StreamInfo) -> str:
    title = stream.title if stream.title else "-"
    lang = stream.language if stream.language else "und"
    display_lang = display_language(lang)
    codec = stream.codec_name if stream.codec_name else "-"
    channels = str(stream.channels) if stream.channels is not None else "-"
    default = "yes" if stream.disposition_default else "no"

    index_part = color(f"index={stream.index}", C.BOLD + C.GOLD)
    lang_part = color(f"lang={display_lang}", language_color(lang))
    title_part = color(f"title={title}", C.SKY)
    codec_part = color(f"codec={codec}", C.MINT)
    default_part = color(f"default={default}", C.BOLD + C.GREEN if default == "yes" else C.GRAY)
    size_part = color(f"size={format_stream_size(stream.size_bytes)}", C.SILVER)

    if stream.codec_type == "audio":
        type_part = color("type=audio", C.BOLD + C.AZURE)
        channels_part = color(f"channels={channels}", C.ORANGE)
        return " | ".join((index_part, type_part, lang_part, title_part, codec_part, channels_part, default_part, size_part))

    if stream.codec_type == "subtitle":
        type_part = color("type=subtitle", C.BOLD + C.VIOLET)
        return " | ".join((index_part, type_part, lang_part, title_part, codec_part, default_part, size_part))

    type_part = color(f"type={stream.codec_type}", C.GRAY)
    return " | ".join((index_part, type_part, lang_part, title_part, codec_part))


def language_color(language: str) -> str:
    normalized = normalize_language_code(language)
    if normalized == "und":
        return UNKNOWN_LANGUAGE_COLOR
    return LANGUAGE_COLORS[sum(ord(ch) for ch in normalized) % len(LANGUAGE_COLORS)]


def terminal_separator() -> str:
    return separator_line(SCAN_SEPARATOR_COLOR)


def print_scan_report(media_files: List[MediaFile], root: Path) -> None:
    print_header("Scan Report: Audio And Subtitle Streams")

    for i, media in enumerate(media_files, start=1):
        rel = display_path(root, media.path)

        print()
        if i > 1:
            print(terminal_separator())
        print(color(f"File: {rel}", FILE_LINE_COLOR))

        audio = media.audio_streams
        subtitles = media.subtitle_streams

        if audio:
            print(color("  Audio:", C.BOLD + C.AZURE))
            for s in audio:
                print(f"    {format_stream(s)}")
        else:
            print(dim("  Audio: none"))

        if subtitles:
            print(color("  Subtitles:", C.BOLD + C.VIOLET))
            for s in subtitles:
                print(f"    {format_stream(s)}")
        else:
            print(dim("  Subtitles: none"))


def add_stream_summary(
    summary: Dict[Tuple[str, str, str], Tuple[int, str, str, str]],
    stream: StreamInfo,
) -> None:
    lang = normalize_language_code(stream.language)
    title = stream.title or ""
    codec = stream.codec_name or ""
    key = (lang.lower(), title.lower(), codec.lower())
    if key in summary:
        count, display_lang, display_title, display_codec = summary[key]
        summary[key] = (count + 1, display_lang, display_title, display_codec)
    else:
        summary[key] = (1, lang, title, codec)


def format_stream_summary_row(count: int, lang: str, title: str, codec: str) -> str:
    display_lang = display_language(lang)
    display_title = title or "-"
    display_codec = codec or "-"
    return " | ".join((
        color(f"count={count}", C.BOLD + C.GOLD),
        color(f"lang={display_lang}", language_color(lang)),
        color(f"title={display_title}", C.SKY),
        color(f"codec={display_codec}", C.MINT),
    ))


def add_stream_index_summary(
    summary: Dict[Tuple[int, str, str, str], Tuple[int, int, str, str, str]],
    stream: StreamInfo,
) -> None:
    index = stream.index
    lang = normalize_language_code(stream.language)
    title = stream.title or ""
    codec = stream.codec_name or ""
    key = (index, lang.lower(), title.lower(), codec.lower())
    if key in summary:
        count, display_index, display_lang, display_title, display_codec = summary[key]
        summary[key] = (count + 1, display_index, display_lang, display_title, display_codec)
    else:
        summary[key] = (1, index, lang, title, codec)


def format_stream_index_summary_row(count: int, index: int, lang: str, title: str, codec: str) -> str:
    display_lang = display_language(lang)
    display_title = title or "-"
    display_codec = codec or "-"
    return " | ".join((
        color(f"count={count}", C.BOLD + C.GOLD),
        color(f"index={index}", C.BOLD + C.ORANGE),
        color(f"lang={display_lang}", language_color(lang)),
        color(f"title={display_title}", C.SKY),
        color(f"codec={display_codec}", C.MINT),
    ))


def streams_for_type(media_files: List[MediaFile], codec_type: str) -> List[StreamInfo]:
    return [
        stream
        for media in media_files
        for stream in media.streams
        if stream.codec_type == codec_type
    ]


def print_stream_choices(label: str, streams: List[StreamInfo], include_index: bool) -> None:
    heading = f"{label} {'indexes' if include_index else 'titles'} found:"
    print(color(heading, C.BOLD + (C.AZURE if label.lower() == "audio" else C.VIOLET)))

    if not streams:
        print(dim("  none"))
        return

    if include_index:
        index_summary: Dict[Tuple[int, str, str, str], Tuple[int, int, str, str, str]] = {}
        for stream in streams:
            add_stream_index_summary(index_summary, stream)
        for key in sorted(index_summary):
            count, index, lang, title, codec = index_summary[key]
            print(f"  {format_stream_index_summary_row(count, index, lang, title, codec)}")
        return

    title_summary: Dict[Tuple[str, str, str], Tuple[int, str, str, str]] = {}
    for stream in streams:
        add_stream_summary(title_summary, stream)
    for key in sorted(title_summary):
        count, lang, title, codec = title_summary[key]
        print(f"  {format_stream_summary_row(count, lang, title, codec)}")


def matching_streams_for_selection(
    media_files: List[MediaFile],
    codec_type: str,
    mode: str,
    languages: Optional[List[str]] = None,
    titles: Optional[List[str]] = None,
    indexes: Optional[List[int]] = None,
) -> List[StreamInfo]:
    streams = streams_for_type(media_files, codec_type)
    languages = languages or []
    titles = titles or []
    indexes = indexes or []

    if codec_type == "audio":
        if mode == AUDIO_BY_LANGUAGE:
            wanted = {normalize_language_code(value) for value in languages}
            return [stream for stream in streams if normalize_language_code(stream.language) in wanted]
        if mode == AUDIO_BY_TITLE:
            return [stream for stream in streams if text_matches_any(stream.title, titles)]
        if mode == AUDIO_BY_INDEX:
            return [stream for stream in streams if stream.index in indexes]
        if mode == AUDIO_ALL:
            return streams
        return []

    if mode == SUBTITLE_BY_LANGUAGE:
        wanted = {normalize_language_code(value) for value in languages}
        return [stream for stream in streams if normalize_language_code(stream.language) in wanted]
    if mode == SUBTITLE_BY_TITLE:
        return [stream for stream in streams if text_matches_any(stream.title, titles)]
    if mode == SUBTITLE_BY_INDEX:
        return [stream for stream in streams if stream.index in indexes]
    if mode == SUBTITLE_ALL:
        return streams

    return []


def matching_streams_for_media(
    media: MediaFile,
    codec_type: str,
    mode: str,
    languages: Optional[List[str]] = None,
    titles: Optional[List[str]] = None,
    indexes: Optional[List[int]] = None,
) -> List[StreamInfo]:
    return matching_streams_for_selection([media], codec_type, mode, languages, titles, indexes)


def print_selection_preview(
    label: str,
    media_files: List[MediaFile],
    codec_type: str,
    mode: str,
    languages: Optional[List[str]] = None,
    titles: Optional[List[str]] = None,
    indexes: Optional[List[int]] = None,
) -> None:
    selected_by_file = [
        (media, matching_streams_for_media(media, codec_type, mode, languages, titles, indexes))
        for media in media_files
    ]
    streams = [stream for _, matches in selected_by_file for stream in matches]
    matched_files = sum(1 for _, matches in selected_by_file if matches)
    unmatched_files = [media for media, matches in selected_by_file if not matches]

    print(color(f"Selected {label.lower()} streams:", C.BOLD + (C.AZURE if label.lower() == "audio" else C.VIOLET)))
    print(info(f"  files matched={matched_files}/{len(media_files)} | streams selected={len(streams)} | no match={len(unmatched_files)}"))
    if not streams:
        print(warn("  none matched"))
        return

    index_summary: Dict[Tuple[int, str, str, str], Tuple[int, int, str, str, str]] = {}
    for stream in streams:
        add_stream_index_summary(index_summary, stream)
    for key in sorted(index_summary):
        count, index, lang, title, codec = index_summary[key]
        print(f"  {format_stream_index_summary_row(count, index, lang, title, codec)}")

    if unmatched_files:
        print(warn(f"  files with no selected {label.lower()}: {len(unmatched_files)}"))
        for media in unmatched_files[:8]:
            print(warn(f"    {media.path.name}"))
        if len(unmatched_files) > 8:
            print(warn(f"    ... {len(unmatched_files) - 8} more"))


def print_unique_summary(media_files: List[MediaFile]) -> None:
    audio_summary: Dict[Tuple[str, str, str], Tuple[int, str, str, str]] = {}
    subtitle_summary: Dict[Tuple[str, str, str], Tuple[int, str, str, str]] = {}

    for media in media_files:
        for s in media.audio_streams:
            add_stream_summary(audio_summary, s)

        for s in media.subtitle_streams:
            add_stream_summary(subtitle_summary, s)

    print_header("Unique Stream Summary")

    print(color("Audio streams found:", C.BOLD + C.AZURE))
    if audio_summary:
        for key in sorted(audio_summary):
            count, lang, title, codec = audio_summary[key]
            print(f"  {format_stream_summary_row(count, lang, title, codec)}")
    else:
        print(dim("  none"))

    print()
    print(color("Subtitle streams found:", C.BOLD + C.VIOLET))
    if subtitle_summary:
        for key in sorted(subtitle_summary):
            count, lang, title, codec = subtitle_summary[key]
            print(f"  {format_stream_summary_row(count, lang, title, codec)}")
    else:
        print(dim("  none"))


def format_index_list(indexes: List[int]) -> str:
    if not indexes:
        return "none"
    return ", ".join(str(index) for index in indexes)


def format_text_list(values: List[str]) -> str:
    if not values:
        return "none"
    return ", ".join(display_language(value) for value in values)


def stream_indexes_for(media_files: List[MediaFile], codec_type: str) -> List[int]:
    return sorted({
        stream.index
        for media in media_files
        for stream in media.streams
        if stream.codec_type == codec_type
    })


def stream_languages_for(media_files: List[MediaFile], codec_type: str) -> List[str]:
    return sorted({
        normalize_language_code(stream.language)
        for media in media_files
        for stream in media.streams
        if stream.codec_type == codec_type
    })


def has_unknown_language(media_files: List[MediaFile], codec_type: Optional[str] = None) -> bool:
    return any(
        (codec_type is None or stream.codec_type == codec_type)
        and is_unknown_language(stream.language)
        for media in media_files
        for stream in media.streams
    )


def ask_language_code(prompt: str) -> str:
    return normalize_language_code(ask_text(prompt))


def metadata_edit_match_text(edit: StreamMetadataEdit) -> str:
    if edit.match_indexes:
        return "indexes=" + ",".join(str(index) for index in edit.match_indexes)
    if edit.match_languages:
        return "languages=" + ", ".join(display_language(value) for value in edit.match_languages)
    return "all"


def metadata_edit_change_text(edit: StreamMetadataEdit) -> str:
    changes = []
    if edit.language:
        changes.append(f"language={display_language(edit.language)}")
    if edit.title:
        changes.append(f"title={edit.title}")
    return ", ".join(changes) if changes else "no changes"


def format_metadata_edit(edit: StreamMetadataEdit) -> str:
    return f"{edit.codec_type} {metadata_edit_match_text(edit)} -> {metadata_edit_change_text(edit)}"


def format_metadata_edits(edits: Sequence[StreamMetadataEdit]) -> str:
    if not edits:
        return "none"
    return "; ".join(format_metadata_edit(edit) for edit in edits)


def kept_streams_for_metadata(
    media_files: List[MediaFile],
    codec_type: str,
    current_rules: Optional[SelectionRules],
) -> List[StreamInfo]:
    if current_rules is None:
        return [
            stream
            for media in media_files
            for stream in media.streams
            if stream.codec_type == codec_type
        ]

    kept: List[StreamInfo] = []
    for media in media_files:
        if codec_type == "audio":
            kept.extend(selected_audio_streams(media, current_rules))
        elif codec_type == "subtitle":
            kept.extend(selected_subtitle_streams(media, current_rules))
    return kept


def kept_languages_for_metadata(
    media_files: List[MediaFile],
    codec_type: str,
    current_rules: Optional[SelectionRules],
) -> List[str]:
    return sorted({
        normalize_language_code(stream.language)
        for stream in kept_streams_for_metadata(media_files, codec_type, current_rules)
    })


def kept_indexes_for_metadata(
    media_files: List[MediaFile],
    codec_type: str,
    current_rules: Optional[SelectionRules],
) -> List[int]:
    return sorted({
        stream.index
        for stream in kept_streams_for_metadata(media_files, codec_type, current_rules)
    })


def ask_metadata_edits(
    media_files: List[MediaFile],
    initial_edits: Optional[Sequence[StreamMetadataEdit]] = None,
    current_rules: Optional[SelectionRules] = None,
) -> List[StreamMetadataEdit]:
    current_edits = list(initial_edits or [])
    audio_languages_available = kept_languages_for_metadata(media_files, "audio", current_rules)
    subtitle_languages_available = kept_languages_for_metadata(media_files, "subtitle", current_rules)
    unknown_audio_found = any(is_unknown_language(value) for value in audio_languages_available)
    unknown_subtitle_found = any(is_unknown_language(value) for value in subtitle_languages_available)
    default_action = "1" if unknown_audio_found else "2" if unknown_subtitle_found else "8"

    while True:
        print()
        print("Edit Output Metadata:")
        print(dim("  Edits apply only to output audio/subtitle streams that are kept. Input files are not changed."))
        if current_edits:
            print(info(f"  Current edits: {format_metadata_edits(current_edits)}"))

        edit_enabled = ask_yes_no("Edit output stream metadata?", bool(current_edits))
        if not edit_enabled:
            return []

        while True:
            if current_edits:
                print(info(f"Current metadata edits: {format_metadata_edits(current_edits)}"))

            try:
                action = ask_numbered_menu(
                    "Metadata edit actions",
                    (
                        ("1", "set audio language by current language"),
                        ("2", "set subtitle language by current language"),
                        ("3", "set audio language by exact stream indexes"),
                        ("4", "set subtitle language by exact stream indexes"),
                        ("5", "set audio title by exact stream indexes"),
                        ("6", "set subtitle title by exact stream indexes"),
                        ("7", "clear metadata edits"),
                        ("8", "done"),
                    ),
                    default_action if not current_edits else "8",
                    "Choose metadata edit",
                    leading_blank=True,
                )
            except MenuBack:
                print(warn("Back. Returning to metadata edit question."))
                break

            if action == "8":
                return current_edits

            if action == "7":
                current_edits = []
                print(warn("Metadata edits cleared."))
                continue

            try:
                if action in {"1", "2"}:
                    codec_type = "audio" if action == "1" else "subtitle"
                    available_languages = audio_languages_available if codec_type == "audio" else subtitle_languages_available
                    if not available_languages:
                        print(warn(f"No kept {codec_type} streams are available for metadata editing."))
                        continue
                    print(format_prompt_label(f"Current {codec_type} languages found: {format_text_list(available_languages)}"))
                    current_languages = ask_language_codes_required(
                        f"Current {codec_type} language code(s) to edit from the list above, (example: *uknown,jpn)"
                    )
                    new_language = ask_language_code(f"New {codec_type} language code, (example: jpn)")
                    current_edits.append(StreamMetadataEdit(
                        codec_type=codec_type,
                        match_languages=current_languages,
                        language=new_language,
                    ))
                    print(ok(f"Added metadata edit: {format_metadata_edit(current_edits[-1])}"))
                    continue

                codec_type = "audio" if action in {"3", "5"} else "subtitle"
                available_indexes = kept_indexes_for_metadata(media_files, codec_type, current_rules)
                if not available_indexes:
                    print(warn(f"No kept {codec_type} streams are available for metadata editing."))
                    continue
                print(format_prompt_label(f"Current {codec_type} indexes found: {format_index_list(available_indexes)}"))
                indexes = ask_csv_int_required(
                    f"{codec_type.capitalize()} stream indexes to edit from the list above, (example: 2,3)",
                    available_indexes,
                )

                if action in {"3", "4"}:
                    new_language = ask_language_code(f"New {codec_type} language code, (example: jpn)")
                    current_edits.append(StreamMetadataEdit(
                        codec_type=codec_type,
                        match_indexes=indexes,
                        language=new_language,
                    ))
                else:
                    new_title = ask_text(f"New {codec_type} title")
                    current_edits.append(StreamMetadataEdit(
                        codec_type=codec_type,
                        match_indexes=indexes,
                        title=new_title,
                    ))

                print(ok(f"Added metadata edit: {format_metadata_edit(current_edits[-1])}"))
            except MenuBack:
                print(warn("Back. Returning to metadata edit actions."))


def ask_keep_indexes(
    label: str,
    available_indexes: List[int],
    exact_mode: str,
    all_mode: str,
    none_mode: str,
) -> Tuple[str, List[int]]:
    print()
    print(color(f"{label} stream selection", C.BOLD + C.WHITE))
    print(info(f"Available {label.lower()} stream indexes: {format_index_list(available_indexes)}"))

    if not available_indexes:
        print(warn(f"No {label.lower()} streams were found; selecting none."))
        return none_mode, []

    print("Enter indexes from the scan report to Keep.")
    print("Examples: 1,2 | all | none")

    available_set = set(available_indexes)

    while True:
        raw = ask_text(f"{label} stream indexes to Keep (use b to go back)", back_token="b").lower()
        if not raw:
            print(warn("Please enter stream indexes, all, or none."))
            continue

        if raw in {"all", "a", "*"}:
            return all_mode, []

        if raw in {"none", "n", "no", "remove", "-"}:
            return none_mode, []

        indexes = parse_csv_int(raw)
        if not indexes:
            print(warn("Please enter stream indexes, all, or none."))
            continue

        unknown_indexes = sorted(set(indexes) - available_set)
        if unknown_indexes:
            print(warn(f"These indexes were not found in the scan: {format_index_list(unknown_indexes)}"))
            try:
                keep_unknown = ask_yes_no("Keep these indexes anyway?", False)
            except MenuBack:
                print(warn("Back. Returning to stream index entry."))
                continue
            if not keep_unknown:
                continue

        return exact_mode, indexes


def audio_mode_needs_detail(mode: str) -> bool:
    return mode in {AUDIO_BY_LANGUAGE, AUDIO_BY_TITLE, AUDIO_BY_INDEX}


def subtitle_mode_needs_detail(mode: str) -> bool:
    return mode in {SUBTITLE_BY_LANGUAGE, SUBTITLE_BY_TITLE, SUBTITLE_BY_INDEX}


def previous_advanced_step(
    step: int,
    audio_mode: str,
    subtitle_mode: str,
    skip_audio_selection: bool = False,
    skip_subtitle_selection: bool = False,
) -> int:
    if step == 1:
        return 0
    if step == 2:
        if skip_audio_selection:
            return -1
        return 1 if audio_mode_needs_detail(audio_mode) else 0
    if step == 3:
        return 2
    if step == 4:
        if skip_subtitle_selection:
            if skip_audio_selection:
                return -1
            if audio_mode_needs_detail(audio_mode):
                return 1
            return 0
        return 3 if subtitle_mode_needs_detail(subtitle_mode) else 2
    if step == 5:
        if skip_subtitle_selection:
            if skip_audio_selection:
                return -1
            if audio_mode_needs_detail(audio_mode):
                return 1
            return 0
        if subtitle_mode == SUBTITLE_NONE:
            return 3 if subtitle_mode_needs_detail(subtitle_mode) else 2
        return 4
    if step == 6:
        return 5
    if step == 7:
        return 6
    if step == 8:
        return 7
    if step == 9:
        return 8
    return 0


def previous_exact_step(step: int, subtitle_mode: str) -> int:
    if step == 1:
        return 0
    if step == 2:
        return 1
    if step == 3:
        return 2 if subtitle_mode != SUBTITLE_NONE else 1
    if step == 4:
        return 3
    if step == 5:
        return 4
    if step == 6:
        return 5
    if step == 7:
        return 6
    return 0


def configure_rules_advanced(
    media_files: List[MediaFile],
    initial: Optional[SelectionRules] = None,
    start_step: int = 0,
) -> SelectionRules:
    audio_language_options = stream_languages_for(media_files, "audio")
    subtitle_language_options = stream_languages_for(media_files, "subtitle")
    skip_audio_selection = len(audio_language_options) <= 1
    skip_subtitle_selection = not subtitle_language_options

    if initial is None and skip_audio_selection:
        start_step = 5 if skip_subtitle_selection else 2

    step = start_step
    audio_mode = initial.audio_mode if initial else AUDIO_BY_LANGUAGE
    audio_languages = list(initial.audio_languages) if initial else []
    audio_titles = list(initial.audio_titles) if initial else []
    audio_indexes = list(initial.audio_indexes) if initial else []
    subtitle_mode = initial.subtitle_mode if initial else SUBTITLE_ALL
    subtitle_languages = list(initial.subtitle_languages) if initial else []
    subtitle_titles = list(initial.subtitle_titles) if initial else []
    subtitle_indexes = list(initial.subtitle_indexes) if initial else []
    keep_attachments = initial.keep_attachments if initial else True
    keep_metadata = initial.keep_metadata if initial else True
    metadata_edits = list(initial.metadata_edits) if initial else []
    keep_chapters = initial.keep_chapters if initial else True
    copy_non_video_files = initial.copy_non_video_files if initial else True
    overwrite = initial.overwrite if initial else False

    if initial is None and skip_audio_selection:
        if audio_language_options:
            audio_mode = AUDIO_BY_LANGUAGE
            audio_languages = list(audio_language_options)
        else:
            audio_mode = AUDIO_NONE
    if initial is None and skip_subtitle_selection:
        subtitle_mode = SUBTITLE_NONE
        keep_attachments = False

    if initial is None and skip_audio_selection:
        print()
        print("Configure Output Rules:")
        if audio_language_options:
            print(format_prompt_label(f"Audio languages found: {format_text_list(audio_language_options)}"))
            print(info("Only one audio language found; keeping all audio."))
        else:
            print(warn("No audio streams found; selecting no audio."))
        if skip_subtitle_selection:
            print(warn("No subtitle streams found; skipping subtitle selection."))

    while True:
        try:
            if step == 0:
                print()
                print("Configure Output Rules:")
                audio_mode = ask_numbered_menu(
                    "Audio selection modes",
                    (
                        (AUDIO_BY_LANGUAGE, "keep audio by language codes."),
                        (AUDIO_BY_TITLE, "keep audio by title text, (example: Stereo,Main)"),
                        (AUDIO_BY_INDEX, "keep audio by exact stream indexes, (example: 2,3)"),
                        (AUDIO_ALL, "keep all audio"),
                        (AUDIO_NONE, "remove all audio"),
                    ),
                    AUDIO_BY_LANGUAGE,
                    "Choose audio mode",
                    leading_blank=True,
                    notes=(f"Found: {format_text_list(audio_language_options)}",),
                )
                audio_languages = []
                audio_titles = []
                audio_indexes = []
                step = 1 if audio_mode_needs_detail(audio_mode) else (5 if skip_subtitle_selection else 2)
                continue

            if step == 1:
                if audio_mode == AUDIO_BY_LANGUAGE:
                    print(format_prompt_label(f"Audio languages found: {format_text_list(audio_language_options)}"))
                    audio_languages = ask_language_codes_required("Audio language codes to Keep from the list above")
                    print_selection_preview(
                        "Audio",
                        media_files,
                        "audio",
                        audio_mode,
                        languages=audio_languages,
                    )
                elif audio_mode == AUDIO_BY_TITLE:
                    print_stream_choices("Audio", streams_for_type(media_files, "audio"), include_index=False)
                    audio_titles = ask_csv_text_required("Audio title text to Keep, (example: japanese,commentary)")
                    print_selection_preview(
                        "Audio",
                        media_files,
                        "audio",
                        audio_mode,
                        titles=audio_titles,
                    )
                elif audio_mode == AUDIO_BY_INDEX:
                    print_stream_choices("Audio", streams_for_type(media_files, "audio"), include_index=True)
                    audio_indexes = ask_csv_int_required(
                        "Audio stream indexes to Keep, (example: 2,3)",
                        stream_indexes_for(media_files, "audio"),
                    )
                    print_selection_preview(
                        "Audio",
                        media_files,
                        "audio",
                        audio_mode,
                        indexes=audio_indexes,
                    )
                step = 5 if skip_subtitle_selection else 2
                continue

            if step == 2:
                if skip_subtitle_selection:
                    subtitle_mode = SUBTITLE_NONE
                    subtitle_languages = []
                    subtitle_titles = []
                    subtitle_indexes = []
                    keep_attachments = False
                    print(warn("No subtitle streams found; skipping subtitle selection."))
                    step = 5
                    continue

                subtitle_mode = ask_numbered_menu(
                    "Subtitle selection modes",
                    (
                        (SUBTITLE_ALL, "keep all subtitles"),
                        (SUBTITLE_BY_LANGUAGE, "keep subtitles by language codes."),
                        (SUBTITLE_BY_TITLE, "keep subtitles by title text, (example: Signs,Full)"),
                        (SUBTITLE_BY_INDEX, "keep subtitles by exact stream indexes, (example: 3,4)"),
                        (SUBTITLE_NONE, "remove all subtitles"),
                    ),
                    SUBTITLE_ALL,
                    "Choose subtitle mode",
                    leading_blank=True,
                    notes=(f"Found: {format_text_list(subtitle_language_options)}",),
                )
                subtitle_languages = []
                subtitle_titles = []
                subtitle_indexes = []
                step = 3 if subtitle_mode_needs_detail(subtitle_mode) else 4
                continue

            if step == 3:
                if subtitle_mode == SUBTITLE_BY_LANGUAGE:
                    print(format_prompt_label(f"Subtitle languages found: {format_text_list(subtitle_language_options)}"))
                    subtitle_languages = ask_language_codes_required("Subtitle language codes to Keep from the list above")
                    print_selection_preview(
                        "Subtitle",
                        media_files,
                        "subtitle",
                        subtitle_mode,
                        languages=subtitle_languages,
                    )
                elif subtitle_mode == SUBTITLE_BY_TITLE:
                    print_stream_choices("Subtitle", streams_for_type(media_files, "subtitle"), include_index=False)
                    subtitle_titles = ask_csv_text_required("Subtitle title text to Keep, (example: signs,full)")
                    print_selection_preview(
                        "Subtitle",
                        media_files,
                        "subtitle",
                        subtitle_mode,
                        titles=subtitle_titles,
                    )
                elif subtitle_mode == SUBTITLE_BY_INDEX:
                    print_stream_choices("Subtitle", streams_for_type(media_files, "subtitle"), include_index=True)
                    subtitle_indexes = ask_csv_int_required(
                        "Subtitle stream indexes to Keep, (example: 3,4)",
                        stream_indexes_for(media_files, "subtitle"),
                    )
                    print_selection_preview(
                        "Subtitle",
                        media_files,
                        "subtitle",
                        subtitle_mode,
                        indexes=subtitle_indexes,
                    )
                step = 4
                continue

            if step == 4:
                print()
                if subtitle_mode == SUBTITLE_NONE:
                    keep_attachments = False
                    print(warn("Subtitle mode is remove-all, so font attachments will also be removed."))
                else:
                    keep_attachments = ask_yes_no("Keep MKV font attachments? Recommended if you keep ASS/SSA subtitles", True)
                step = 5
                continue

            if step == 5:
                print_metadata_note()
                keep_metadata = ask_yes_no("Keep input metadata?", True)
                step = 6
                continue

            if step == 6:
                metadata_context_rules = SelectionRules(
                    audio_mode=audio_mode,
                    audio_languages=audio_languages,
                    audio_titles=audio_titles,
                    audio_indexes=audio_indexes,
                    subtitle_mode=subtitle_mode,
                    subtitle_languages=subtitle_languages,
                    subtitle_titles=subtitle_titles,
                    subtitle_indexes=subtitle_indexes,
                    keep_attachments=keep_attachments,
                    keep_metadata=keep_metadata,
                    keep_chapters=keep_chapters,
                    overwrite=overwrite,
                    copy_non_video_files=copy_non_video_files,
                    selection_style="advanced",
                    metadata_edits=metadata_edits,
                )
                metadata_edits = ask_metadata_edits(media_files, metadata_edits, metadata_context_rules)
                step = 7
                continue

            if step == 7:
                keep_chapters = ask_yes_no("Keep chapters?", True)
                step = 8
                continue

            if step == 8:
                copy_non_video_files = ask_yes_no("Copy non-video files to output folder?", True)
                step = 9
                continue

            if step == 9:
                overwrite = ask_yes_no("Overwrite existing output files?", False)
                return SelectionRules(
                    audio_mode=audio_mode,
                    audio_languages=audio_languages,
                    audio_titles=audio_titles,
                    audio_indexes=audio_indexes,
                    subtitle_mode=subtitle_mode,
                    subtitle_languages=subtitle_languages,
                    subtitle_titles=subtitle_titles,
                    subtitle_indexes=subtitle_indexes,
                    keep_attachments=keep_attachments,
                    keep_metadata=keep_metadata,
                    keep_chapters=keep_chapters,
                    overwrite=overwrite,
                    copy_non_video_files=copy_non_video_files,
                    selection_style="advanced",
                    metadata_edits=metadata_edits,
                )
        except MenuBack:
            if step == 0:
                raise
            step = previous_advanced_step(
                step,
                audio_mode,
                subtitle_mode,
                skip_audio_selection=skip_audio_selection,
                skip_subtitle_selection=skip_subtitle_selection,
            )
            if step < 0:
                raise
            print(warn("Back. Returning to previous step."))


def configure_rules_exact(
    media_files: List[MediaFile],
    initial: Optional[SelectionRules] = None,
    start_step: int = 0,
) -> SelectionRules:
    step = start_step
    audio_mode = initial.audio_mode if initial else AUDIO_BY_INDEX
    audio_indexes = list(initial.audio_indexes) if initial else []
    subtitle_mode = initial.subtitle_mode if initial else SUBTITLE_BY_INDEX
    subtitle_indexes = list(initial.subtitle_indexes) if initial else []
    keep_attachments = initial.keep_attachments if initial else True
    keep_metadata = initial.keep_metadata if initial else True
    metadata_edits = list(initial.metadata_edits) if initial else []
    keep_chapters = initial.keep_chapters if initial else True
    copy_non_video_files = initial.copy_non_video_files if initial else True
    overwrite = initial.overwrite if initial else False

    while True:
        try:
            if step == 0:
                audio_mode, audio_indexes = ask_keep_indexes(
                    "Audio",
                    stream_indexes_for(media_files, "audio"),
                    exact_mode=AUDIO_BY_INDEX,
                    all_mode=AUDIO_ALL,
                    none_mode=AUDIO_NONE,
                )
                step = 1
                continue

            if step == 1:
                subtitle_mode, subtitle_indexes = ask_keep_indexes(
                    "Subtitle",
                    stream_indexes_for(media_files, "subtitle"),
                    exact_mode=SUBTITLE_BY_INDEX,
                    all_mode=SUBTITLE_ALL,
                    none_mode=SUBTITLE_NONE,
                )
                step = 2
                continue

            if step == 2:
                print()
                if subtitle_mode == SUBTITLE_NONE:
                    keep_attachments = False
                    print(warn("Subtitle selection is none, so font attachments will also be removed."))
                else:
                    keep_attachments = ask_yes_no("Keep MKV font attachments? Recommended if you keep ASS/SSA subtitles", True)
                step = 3
                continue

            if step == 3:
                print_metadata_note()
                keep_metadata = ask_yes_no("Keep input metadata?", True)
                step = 4
                continue

            if step == 4:
                metadata_context_rules = SelectionRules(
                    audio_mode=audio_mode,
                    audio_languages=[],
                    audio_titles=[],
                    audio_indexes=audio_indexes,
                    subtitle_mode=subtitle_mode,
                    subtitle_languages=[],
                    subtitle_titles=[],
                    subtitle_indexes=subtitle_indexes,
                    keep_attachments=keep_attachments,
                    keep_metadata=keep_metadata,
                    keep_chapters=keep_chapters,
                    overwrite=overwrite,
                    copy_non_video_files=copy_non_video_files,
                    selection_style="exact",
                    metadata_edits=metadata_edits,
                )
                metadata_edits = ask_metadata_edits(media_files, metadata_edits, metadata_context_rules)
                step = 5
                continue

            if step == 5:
                keep_chapters = ask_yes_no("Keep chapters?", True)
                step = 6
                continue

            if step == 6:
                copy_non_video_files = ask_yes_no("Copy non-video files to output folder?", True)
                step = 7
                continue

            if step == 7:
                overwrite = ask_yes_no("Overwrite existing output files?", False)
                return SelectionRules(
                    audio_mode=audio_mode,
                    audio_languages=[],
                    audio_titles=[],
                    audio_indexes=audio_indexes,
                    subtitle_mode=subtitle_mode,
                    subtitle_languages=[],
                    subtitle_titles=[],
                    subtitle_indexes=subtitle_indexes,
                    keep_attachments=keep_attachments,
                    keep_metadata=keep_metadata,
                    keep_chapters=keep_chapters,
                    overwrite=overwrite,
                    copy_non_video_files=copy_non_video_files,
                    selection_style="exact",
                    metadata_edits=metadata_edits,
                )
        except MenuBack:
            if step == 0:
                raise
            step = previous_exact_step(step, subtitle_mode)
            print(warn("Back. Returning to previous step."))


def configure_rules(media_files: List[MediaFile]) -> SelectionRules:
    if len(stream_languages_for(media_files, "audio")) <= 1:
        LOGGER.info("Selection style skipped: one or zero audio languages found")
        return configure_rules_advanced(media_files)

    while True:
        selection_style = ask_numbered_menu(
            "Choose Streams to Keep",
            (
                ("1", "advanced rules by language, title, or index"),
                ("2", "choose exact stream indexes from the scan report"),
            ),
            "1",
            "Choose selection style",
            leading_blank=True,
        )

        try:
            if selection_style == "1":
                LOGGER.info("Selection style: advanced")
                return configure_rules_advanced(media_files)

            LOGGER.info("Selection style: exact stream indexes")
            return configure_rules_exact(media_files)
        except MenuBack:
            LOGGER.info("Back requested inside stream selection; returning to selection style")
            print(warn("Back. Returning to selection style."))


def revisit_last_rule_step(media_files: List[MediaFile], rules: SelectionRules) -> SelectionRules:
    if rules.selection_style == "exact":
        return configure_rules_exact(media_files, initial=rules, start_step=7)
    return configure_rules_advanced(media_files, initial=rules, start_step=9)


def text_matches_any(value: str, needles: List[str]) -> bool:
    haystack = (value or "").lower()
    return any(needle in haystack for needle in needles)


def selected_audio_streams(media: MediaFile, rules: SelectionRules) -> List[StreamInfo]:
    audio = media.audio_streams

    if rules.audio_mode == AUDIO_BY_LANGUAGE:
        wanted = {normalize_language_code(value) for value in rules.audio_languages}
        return [s for s in audio if normalize_language_code(s.language) in wanted]

    if rules.audio_mode == AUDIO_BY_TITLE:
        return [s for s in audio if text_matches_any(s.title, rules.audio_titles)]

    if rules.audio_mode == AUDIO_BY_INDEX:
        return [s for s in audio if s.index in rules.audio_indexes]

    if rules.audio_mode == AUDIO_ALL:
        return audio

    if rules.audio_mode == AUDIO_NONE:
        return []

    return []


def selected_subtitle_streams(media: MediaFile, rules: SelectionRules) -> List[StreamInfo]:
    subtitles = media.subtitle_streams

    if rules.subtitle_mode == SUBTITLE_NONE:
        return []

    if rules.subtitle_mode == SUBTITLE_BY_LANGUAGE:
        wanted = {normalize_language_code(value) for value in rules.subtitle_languages}
        return [s for s in subtitles if normalize_language_code(s.language) in wanted]

    if rules.subtitle_mode == SUBTITLE_BY_TITLE:
        return [s for s in subtitles if text_matches_any(s.title, rules.subtitle_titles)]

    if rules.subtitle_mode == SUBTITLE_BY_INDEX:
        return [s for s in subtitles if s.index in rules.subtitle_indexes]

    if rules.subtitle_mode == SUBTITLE_ALL:
        return subtitles

    return []


def metadata_edit_applies(edit: StreamMetadataEdit, stream: StreamInfo) -> bool:
    if edit.codec_type != stream.codec_type:
        return False
    if edit.match_indexes and stream.index in edit.match_indexes:
        return True
    if edit.match_languages and normalize_language_code(stream.language) in {
        normalize_language_code(value) for value in edit.match_languages
    }:
        return True
    return not edit.match_indexes and not edit.match_languages


def metadata_values_for_stream(stream: StreamInfo, rules: SelectionRules) -> Tuple[str, str]:
    language = ""
    title = ""

    for edit in rules.metadata_edits:
        if not metadata_edit_applies(edit, stream):
            continue
        if edit.language:
            language = normalize_language_code(edit.language)
        if edit.title:
            title = edit.title

    return language, title


def add_stream_metadata_options(
    cmd: List[str],
    stream_spec: str,
    stream: StreamInfo,
    rules: SelectionRules,
) -> None:
    language, title = metadata_values_for_stream(stream, rules)
    if language:
        cmd += [f"-metadata:{stream_spec}", f"language={language}"]
    if title:
        cmd += [f"-metadata:{stream_spec}", f"title={title}"]


def same_stream_indexes(original: Sequence[StreamInfo], selected: Sequence[StreamInfo]) -> bool:
    return [stream.index for stream in original] == [stream.index for stream in selected]


def default_disposition_needs_update(streams: Sequence[StreamInfo]) -> bool:
    if not streams:
        return False
    if streams[0].disposition_default != 1:
        return True
    return any(stream.disposition_default != 0 for stream in streams[1:])


def metadata_edits_need_remux(streams: Sequence[StreamInfo], rules: SelectionRules) -> bool:
    for stream in streams:
        target_language, target_title = metadata_values_for_stream(stream, rules)
        if target_language and normalize_language_code(stream.language) != normalize_language_code(target_language):
            return True
        if target_title and (stream.title or "") != target_title:
            return True
    return False


def remux_needed_reasons(
    media: MediaFile,
    rules: SelectionRules,
    audio_keep: Sequence[StreamInfo],
    subtitles_keep: Sequence[StreamInfo],
) -> List[str]:
    reasons: List[str] = []

    if not same_stream_indexes(media.audio_streams, audio_keep):
        reasons.append("audio stream selection changes")
    if not same_stream_indexes(media.subtitle_streams, subtitles_keep):
        reasons.append("subtitle stream selection changes")
    if not rules.keep_attachments and media.attachment_streams:
        reasons.append("attachments are removed")
    if not rules.keep_metadata:
        reasons.append("input metadata is removed")
    if not rules.keep_chapters:
        reasons.append("chapters are removed")
    if default_disposition_needs_update(audio_keep):
        reasons.append("audio default disposition is normalized")
    if default_disposition_needs_update(subtitles_keep):
        reasons.append("subtitle default disposition is normalized")
    if metadata_edits_need_remux([*audio_keep, *subtitles_keep], rules):
        reasons.append("stream metadata is edited")

    return reasons


def build_ffmpeg_command(
    input_file: Path,
    output_file: Path,
    media: MediaFile,
    rules: SelectionRules,
) -> Tuple[List[str], List[StreamInfo], List[StreamInfo]]:
    audio_keep = selected_audio_streams(media, rules)
    subtitles_keep = selected_subtitle_streams(media, rules)

    cmd = [FFMPEG_BIN, "-hide_banner"]

    if rules.overwrite:
        cmd.append("-y")
    else:
        cmd.append("-n")

    cmd += ["-i", str(input_file)]

    # Always keep all video streams. This is safer for special files with multiple video streams,
    # such as cover art or alternate angles. Usually this is just one video stream.
    cmd += ["-map", "0:v?"]

    for s in audio_keep:
        cmd += ["-map", f"0:{s.index}"]

    for s in subtitles_keep:
        cmd += ["-map", f"0:{s.index}"]

    if rules.keep_attachments:
        cmd += ["-map", "0:t?"]

    if rules.keep_metadata:
        cmd += ["-map_metadata", "0"]
    else:
        cmd += ["-map_metadata", "-1"]

    if rules.keep_chapters:
        cmd += ["-map_chapters", "0"]
    else:
        cmd += ["-map_chapters", "-1"]

    cmd += ["-c", "copy"]

    for index in range(len(audio_keep)):
        cmd += [f"-disposition:a:{index}", "+default" if index == 0 else "-default"]

    for index in range(len(subtitles_keep)):
        cmd += [f"-disposition:s:{index}", "+default" if index == 0 else "-default"]

    for index, stream in enumerate(audio_keep):
        add_stream_metadata_options(cmd, f"s:a:{index}", stream, rules)

    for index, stream in enumerate(subtitles_keep):
        add_stream_metadata_options(cmd, f"s:s:{index}", stream, rules)

    cmd += [str(output_file)]

    return cmd, audio_keep, subtitles_keep


INVALID_FILENAME_CHARS = '<>:"/\\|?*'


def sanitize_filename_part(value: str, fallback: str = "Muxed") -> str:
    cleaned = "".join("-" if ch in INVALID_FILENAME_CHARS else ch for ch in value)
    cleaned = " ".join(cleaned.split()).strip(" .")
    if not any(ch.isalnum() for ch in cleaned):
        return fallback
    return cleaned or fallback


def language_label(value: str) -> str:
    normalized = normalize_language_code(value)
    labels = {
        "und": "UKNOWN",
        "ja": "JA",
        "jpn": "JA",
        "japanese": "JA",
        "en": "EN",
        "eng": "EN",
        "english": "EN",
        "fa": "FA",
        "fas": "FA",
        "per": "FA",
        "persian": "FA",
    }
    return labels.get(normalized, normalized.upper())


def compact_labels(values: Sequence[str], max_items: int = 3) -> str:
    unique = []
    for value in values:
        label = language_label(value)
        if label not in unique:
            unique.append(label)
    if not unique:
        return ""
    if len(unique) > max_items:
        return "+".join(unique[:max_items]) + "+..."
    return "+".join(unique)


def stream_rule_part(kind: str, mode: str, languages: List[str], titles: List[str], indexes: List[int]) -> str:
    if kind == "audio":
        if mode == AUDIO_BY_LANGUAGE and languages:
            return f"{compact_labels(languages)} Audio"
        if mode == AUDIO_BY_TITLE and titles:
            return "Selected Audio"
        if mode == AUDIO_BY_INDEX and indexes:
            return "Audio " + "+".join(str(index) for index in indexes[:4])
        if mode == AUDIO_ALL:
            return "All Audio"
        if mode == AUDIO_NONE:
            return "No Audio"
        return "No Audio Match"

    if mode == SUBTITLE_NONE:
        return "No Subs"
    if mode == SUBTITLE_BY_LANGUAGE and languages:
        return f"{compact_labels(languages)} Subs"
    if mode == SUBTITLE_BY_TITLE and titles:
        return "Selected Subs"
    if mode == SUBTITLE_BY_INDEX and indexes:
        return "Subs " + "+".join(str(index) for index in indexes[:4])
    if mode == SUBTITLE_ALL:
        return "All Subs"
    return "No Subtitle Match"


def selection_suffix(rules: SelectionRules) -> str:
    parts = [
        stream_rule_part("audio", rules.audio_mode, rules.audio_languages, rules.audio_titles, rules.audio_indexes),
        stream_rule_part("subtitle", rules.subtitle_mode, rules.subtitle_languages, rules.subtitle_titles, rules.subtitle_indexes),
    ]

    compact: List[str] = []
    for part in parts:
        if part and part not in compact:
            compact.append(part)

    suffix = " + ".join(compact) if compact else "Muxed"
    return f"[{sanitize_filename_part(suffix)}]"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    for counter in range(2, 10000):
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not find available output path for: {path}")


def unique_directory_path(path: Path) -> Path:
    if not path.exists():
        return path

    for counter in range(2, 10000):
        candidate = path.with_name(f"{path.name} ({counter})")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not find available output folder for: {path}")


def resolve_output_root(input_root: Path, output_base: Path, rules: SelectionRules) -> Path:
    if input_root.is_dir():
        folder_name = sanitize_filename_part(f"{input_root.name} {selection_suffix(rules)}")
        return unique_directory_path(output_base / folder_name)

    return output_base


def make_output_path(input_root: Path, output_root: Path, input_file: Path, rules: SelectionRules) -> Path:
    if input_root.is_file():
        suffix = selection_suffix(rules)
        filename = sanitize_filename_part(f"{input_file.stem} {suffix}") + input_file.suffix
        output_file = output_root / filename
    else:
        rel = input_file.relative_to(input_root)
        output_file = output_root / rel

    try:
        if output_file.resolve() == input_file.resolve():
            output_file = unique_path(output_file)
    except OSError:
        pass

    if output_file.exists() and not rules.overwrite:
        output_file = unique_path(output_file)

    return output_file


def display_path(input_root: Path, input_file: Path) -> Path:
    if input_root.is_file():
        return Path(input_file.name)

    try:
        return input_file.relative_to(input_root)
    except ValueError:
        return input_file


def path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def path_total_size(path: Path, exclude_paths: Optional[Sequence[Path]] = None) -> int:
    excludes = list(exclude_paths or [])
    total = 0

    if not path.exists():
        return 0

    if path.is_file():
        try:
            return path.stat().st_size
        except OSError as exc:
            LOGGER.warning("Could not read file size for %s: %s", path, exc)
            return 0

    for child in path.rglob("*"):
        if not child.is_file():
            continue
        if any(path_is_under(child, excluded) for excluded in excludes):
            continue
        try:
            total += child.stat().st_size
        except OSError as exc:
            LOGGER.warning("Could not read file size for %s: %s", child, exc)

    return total


def extra_file_sources(input_root: Path, output_root: Path) -> List[Path]:
    sources: List[Path] = []
    for source in sorted(input_root.rglob("*"), key=lambda path: str(path).lower()):
        if not source.is_file():
            continue
        if source.suffix.lower() in VIDEO_EXTENSIONS:
            continue
        if path_is_under(source, output_root):
            continue
        sources.append(source)
    return sources


def destination_snapshot(paths: Iterable[Path]) -> Dict[Path, Tuple[int, int]]:
    snapshot: Dict[Path, Tuple[int, int]] = {}
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[path] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def robocopy_success(returncode: int) -> bool:
    return 0 <= returncode <= 7


def append_file_result(
    results: List[Dict[str, str]],
    index: int,
    total: int,
    action: str,
    status: str,
    input_file: Path,
    output_file: Optional[Path],
    detail: str,
    started_at: float,
    returncode: Optional[int] = None,
) -> None:
    elapsed = format_elapsed_time(time.perf_counter() - started_at)
    result = {
        "index": f"{index}/{total}",
        "action": action,
        "status": status,
        "input": str(input_file),
        "output": str(output_file) if output_file else "-",
        "detail": detail or "-",
        "returncode": str(returncode) if returncode is not None else "-",
        "elapsed": elapsed,
    }
    results.append(result)

    message = (
        "RESULT %(index)s | status=%(status)s | action=%(action)s | "
        "input=%(input)s | output=%(output)s | detail=%(detail)s | "
        "returncode=%(returncode)s | elapsed=%(elapsed)s"
    ) % result

    if status == "FAILED":
        LOGGER.error(message)
    elif status in {"SKIPPED", "NO_AUDIO_MATCH", "WARNING"}:
        LOGGER.warning(message)
    else:
        LOGGER.info(message)


def log_file_result_summary(results: Sequence[Dict[str, str]]) -> None:
    LOGGER.info("Per-file result summary begin: count=%s", len(results))
    for result in results:
        message = (
            "SUMMARY_RESULT %(index)s | status=%(status)s | action=%(action)s | "
            "input=%(input)s | output=%(output)s | detail=%(detail)s | "
            "returncode=%(returncode)s | elapsed=%(elapsed)s"
        ) % result
        LOGGER.info(message)
    LOGGER.info("Per-file result summary end")


def copy_extra_files(input_root: Path, output_root: Path, rules: SelectionRules) -> Tuple[int, int, int]:
    if input_root.is_file():
        return 0, 0, 0

    sources = extra_file_sources(input_root, output_root)
    if not sources:
        return 0, 0, 0

    if shutil.which(ROBOCOPY_BIN) is None:
        LOGGER.error("robocopy was not found in PATH")
        print(err("robocopy was not found in PATH; non-video files were not copied."))
        return 0, 0, len(sources)

    destinations: List[Path] = []
    for source in sources:
        try:
            destinations.append(output_root / source.relative_to(input_root))
        except ValueError:
            pass

    before = destination_snapshot(destinations)

    args = [
        ROBOCOPY_BIN,
        str(input_root),
        str(output_root),
        "/E",
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
        "/XF",
    ]
    args.extend(f"*{suffix}" for suffix in sorted(VIDEO_EXTENSIONS))

    if path_is_under(output_root, input_root):
        args.extend(["/XD", str(output_root)])

    if not rules.overwrite:
        args.extend(["/XC", "/XN", "/XO"])

    LOGGER.info("Command: %s", command_to_text(args))
    proc = run_command(args)
    if proc.stdout.strip():
        LOGGER.debug("robocopy stdout: %s", proc.stdout.strip())
    if proc.stderr.strip():
        LOGGER.debug("robocopy stderr: %s", proc.stderr.strip())

    after = destination_snapshot(destinations)
    copied = 0
    skipped = 0
    failed = 0

    for destination in destinations:
        before_stat = before.get(destination)
        after_stat = after.get(destination)
        if after_stat is None:
            failed += 1
        elif before_stat is None or after_stat != before_stat:
            copied += 1
        else:
            skipped += 1

    if not robocopy_success(proc.returncode):
        failed = max(failed, 1)
        LOGGER.error("robocopy failed with exit code %s", proc.returncode)
        print(err(f"robocopy failed while copying non-video files. Exit code: {proc.returncode}"))
    else:
        LOGGER.info(
            "robocopy copied extra files: copied=%s skipped=%s failed=%s returncode=%s",
            copied,
            skipped,
            failed,
            proc.returncode,
        )

    return copied, skipped, failed


def copy_video_without_remux(input_file: Path, output_file: Path, progress_started_at: Optional[float] = None) -> int:
    if shutil.which(ROBOCOPY_BIN) is None:
        raise OSError("robocopy was not found in PATH")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    before = destination_snapshot([output_file])

    args = [
        ROBOCOPY_BIN,
        str(input_file.parent),
        str(output_file.parent),
        input_file.name,
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
        "/J",
    ]

    LOGGER.info("Command: %s", command_to_text(args))
    proc = run_copy_command(args, progress_started_at)
    if proc.stdout.strip():
        LOGGER.debug("robocopy stdout: %s", proc.stdout.strip())
    if proc.stderr.strip():
        LOGGER.debug("robocopy stderr: %s", proc.stderr.strip())

    after = destination_snapshot([output_file])
    if not robocopy_success(proc.returncode):
        raise OSError(f"robocopy failed with exit code {proc.returncode}")
    if output_file not in after:
        raise OSError("robocopy did not create the output file")
    if before.get(output_file) == after.get(output_file):
        LOGGER.info("robocopy copied unchanged video but destination metadata did not change: %s", output_file)
    return proc.returncode


def process_files(media_files: List[MediaFile], input_root: Path, output_root: Path, rules: SelectionRules) -> None:
    print_header("Processing Files")
    print()
    started_at = time.perf_counter()
    LOGGER.info("Processing started: input=%s output=%s files=%s", input_root, output_root, len(media_files))
    LOGGER.info("Rules: %s", rules)

    total = len(media_files)
    succeeded = 0
    skipped = 0
    no_audio = 0
    failed = 0
    copied_unchanged = 0
    remuxed = 0
    output_files_for_size: List[Path] = []
    file_results: List[Dict[str, str]] = []

    for i, media in enumerate(media_files, start=1):
        input_file = media.path
        rel = display_path(input_root, input_file)
        if i > 1:
            print(separator_line(PROCESS_SEPARATOR_COLOR))

        try:
            output_file = make_output_path(input_root, output_root, input_file, rules)
        except RuntimeError as exc:
            print(err(f"[{i}/{total}] FAILED: {exc}"))
            LOGGER.exception("Could not resolve output path for %s", input_file)
            failed += 1
            append_file_result(
                file_results,
                i,
                total,
                "resolve-output",
                "FAILED",
                input_file,
                None,
                str(exc),
                started_at,
            )
            continue

        audio_keep = selected_audio_streams(media, rules)
        if rules.audio_mode != AUDIO_NONE and not audio_keep and media.audio_streams:
            print(warn(f"[{i}/{total}] SKIP no matching audio selected: {rel}"))
            LOGGER.warning("No matching audio selected: %s", input_file)
            no_audio += 1
            append_file_result(
                file_results,
                i,
                total,
                "select-audio",
                "NO_AUDIO_MATCH",
                input_file,
                output_file,
                "selected audio rule matched no audio streams",
                started_at,
            )
            continue

        if output_file.exists() and not rules.overwrite:
            print(warn(f"[{i}/{total}] SKIP exists: {rel}"))
            LOGGER.warning("Skip existing output: %s", output_file)
            skipped += 1
            append_file_result(
                file_results,
                i,
                total,
                "skip-existing",
                "SKIPPED",
                input_file,
                output_file,
                "output already exists and overwrite is disabled",
                started_at,
            )
            continue

        subtitles_keep = selected_subtitle_streams(media, rules)
        reasons = remux_needed_reasons(media, rules, audio_keep, subtitles_keep)
        if not reasons:
            print(info(f"[{i}/{total}] Copying unchanged: {rel}"))
            print(dim("          no remux needed"))
            LOGGER.info("Action: copy unchanged | file=%s | reason=no remux needed", input_file)
            try:
                copy_returncode = copy_video_without_remux(input_file, output_file, started_at)
            except OSError as exc:
                failed += 1
                LOGGER.exception("Could not copy unchanged video %s -> %s", input_file, output_file)
                print(err(f"          FAILED to copy unchanged file: {exc}"))
                append_file_result(
                    file_results,
                    i,
                    total,
                    "copy-unchanged",
                    "FAILED",
                    input_file,
                    output_file,
                    str(exc),
                    started_at,
                )
                continue

            succeeded += 1
            copied_unchanged += 1
            output_files_for_size.append(output_file)
            LOGGER.info("Copy unchanged OK: %s -> %s", input_file, output_file)
            append_file_result(
                file_results,
                i,
                total,
                "copy-unchanged",
                "OK",
                input_file,
                output_file,
                "no remux needed",
                started_at,
                copy_returncode,
            )
            print(color(center_for_terminal("Done"), PROCESS_DONE_COLOR))
            continue

        cmd, audio_keep, subtitles_keep = build_ffmpeg_command(input_file, output_file, media, rules)
        LOGGER.info("Action: remux | file=%s | reasons=%s", input_file, "; ".join(reasons))
        LOGGER.info(
            "File %s/%s: %s -> %s | audio_keep=%s subtitle_keep=%s attachments=%s remux_reasons=%s",
            i,
            total,
            input_file,
            output_file,
            [s.index for s in audio_keep],
            [s.index for s in subtitles_keep],
            rules.keep_attachments,
            reasons,
        )

        if not media.video_streams:
            print(warn(f"[{i}/{total}] WARNING: no video stream found: {rel}"))
            LOGGER.warning("No video stream found: %s", input_file)

        print(info(f"[{i}/{total}] Remuxing: {rel}"))
        print(dim(f"          audio kept: {len(audio_keep)} | subtitles kept: {len(subtitles_keep)} | attachments: {'yes' if rules.keep_attachments else 'no'}"))
        LOGGER.info("Command: %s", command_to_text(cmd))

        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            failed += 1
            LOGGER.exception("Could not create output folder for %s", output_file)
            print(err(f"          FAILED to create output folder: {exc}"))
            append_file_result(
                file_results,
                i,
                total,
                "prepare-output",
                "FAILED",
                input_file,
                output_file,
                str(exc),
                started_at,
            )
            continue

        proc = run_ffmpeg_command(cmd, started_at)

        if proc.returncode == 0:
            succeeded += 1
            remuxed += 1
            output_files_for_size.append(output_file)
            LOGGER.info("Remux OK: %s", output_file)
            append_file_result(
                file_results,
                i,
                total,
                "remux",
                "OK",
                input_file,
                output_file,
                "; ".join(reasons),
                started_at,
                proc.returncode,
            )
            print(color(center_for_terminal("Done"), PROCESS_DONE_COLOR))
        else:
            failed += 1
            LOGGER.error("Remux failed: %s | returncode=%s", input_file, proc.returncode)
            print(err("          FAILED"))
            if proc.stderr.strip():
                print(proc.stderr.strip())
            append_file_result(
                file_results,
                i,
                total,
                "remux",
                "FAILED",
                input_file,
                output_file,
                f"ffmpeg return code {proc.returncode}",
                started_at,
                proc.returncode,
            )

    if rules.copy_non_video_files:
        extra_copied, extra_skipped, extra_failed = copy_extra_files(input_root, output_root, rules)
    else:
        extra_copied, extra_skipped, extra_failed = 0, 0, 0
        LOGGER.info("Non-video file copy skipped by user setting")
    elapsed = time.perf_counter() - started_at
    print()
    print(separator_line(C.BOLD + C.DONE_HEADER))
    print(color(center_for_terminal("All Done"), C.BOLD + C.DONE_HEADER))
    print(separator_line(C.BOLD + C.DONE_HEADER))

    if input_root.is_dir():
        original_total_size = path_total_size(input_root, exclude_paths=[output_root])
        output_total_size = path_total_size(output_root)
    else:
        original_total_size = path_total_size(input_root)
        output_total_size = sum(path_total_size(path) for path in output_files_for_size)

    size_delta = output_total_size - original_total_size
    formatted_size_delta = format_size_difference(size_delta)

    print(color(f"Total:   {total}", C.BOLD + C.LAVENDER))
    print(color(f"OK:      {succeeded}", C.BOLD + C.GREEN))
    print(color(f"Remuxed: {remuxed}", C.BOLD + C.AZURE))
    print(color(f"Copied unchanged: {copied_unchanged}", C.BOLD + C.MINT))
    print(color(f"Skipped: {skipped}", C.BOLD + C.AMBER))
    print(color(f"No audio match: {no_audio}", C.BOLD + C.YELLOW))
    print(color(f"Failed:  {failed}", C.BOLD + C.SUMMARY_FAILED))
    print(color(f"Extra files copied:  {extra_copied}", C.BOLD + C.AQUA))
    print(color(f"Extra files skipped: {extra_skipped}", C.BOLD + C.GOLD))
    print(color(f"Extra files failed:  {extra_failed}", C.BOLD + C.SUMMARY_EXTRA_FAILED))
    print(color(f"Output:  {output_root}", C.BOLD + C.SKY))
    print(color(f"Size difference: {formatted_size_delta}", C.BOLD + C.SUMMARY_SIZE_DIFF))
    print(color(f"Elapsed {format_elapsed_time(elapsed)}", C.BOLD + C.SUMMARY_ELAPSED))
    log_file_result_summary(file_results)
    LOGGER.info("Original size: %s bytes", original_total_size)
    LOGGER.info("Output size: %s bytes", output_total_size)
    LOGGER.info("Size difference: %s (%s bytes)", formatted_size_delta, size_delta)
    LOGGER.info(
        "Processing done: total=%s ok=%s remuxed=%s copied_unchanged=%s skipped=%s no_audio_match=%s failed=%s extra_copied=%s extra_skipped=%s extra_failed=%s original_size_bytes=%s output_size_bytes=%s size_difference=%s size_delta_bytes=%s elapsed=%s output=%s",
        total,
        succeeded,
        remuxed,
        copied_unchanged,
        skipped,
        no_audio,
        failed,
        extra_copied,
        extra_skipped,
        extra_failed,
        original_total_size,
        output_total_size,
        formatted_size_delta,
        size_delta,
        format_elapsed_time(elapsed),
        output_root,
    )


def print_ready_for_next_task(message: str = "Task complete. Ready for next task.") -> None:
    print()
    print(separator_line(ACTION_SEPARATOR_COLOR))
    print(color(center_for_terminal(message), PROCESS_DONE_COLOR))
    print(separator_line(ACTION_SEPARATOR_COLOR))
    print()


def verify_output(root: Path, rules: Optional[SelectionRules] = None) -> None:
    print_header("Verify Output Folder")

    files = find_video_files(root)
    if not files:
        print(warn("No video files found."))
        return

    media_files = scan_files(files)

    for media in media_files:
        rel = display_path(root, media.path)

        video_count = len(media.video_streams)
        audio_count = len(media.audio_streams)
        subtitle_count = len(media.subtitle_streams)
        attachment_count = len(media.attachment_streams)

        audio_expected = rules is None or rules.audio_mode != AUDIO_NONE
        status_color = C.GREEN if video_count >= 1 and (audio_count >= 1 or not audio_expected) else C.YELLOW
        audio_langs = format_language_list((s.language for s in media.audio_streams), status_color) if audio_count else "-"
        subtitle_langs = format_language_list((s.language for s in media.subtitle_streams), status_color) if subtitle_count else "-"
        print(
            color(f"{rel} | video={video_count} | audio={audio_count} [", status_color)
            + audio_langs
            + color(f"] | subs={subtitle_count} [", status_color)
            + subtitle_langs
            + color(f"] | attachments={attachment_count}", status_color)
        )


def main_menu() -> None:
    enable_windows_ansi()
    log_path = setup_logging()
    embedded = os.environ.get("FFMWIZ_EMBEDDED_MUXCLS") == "1"
    print_header("MuxCls", leading_blank=False)
    if log_path:
        print(color(f"Log file: {log_path}", C.LOG_YELLOW))
    print()
    print(color("This script uses ffmpeg -c copy. It does not re-encode video, audio, or subtitles.", C.NOTE_BLUE))
    print()

    if not require_tool(FFMPEG_BIN):
        LOGGER.error("Required tool missing: %s", FFMPEG_BIN)
        print(err("ERROR: ffmpeg was not found in PATH."))
        print("Install FFmpeg or add ffmpeg.exe to PATH, then run this script again.")
        if LOG_FILE:
            print(color(f"Log file: {LOG_FILE}", C.LOG_YELLOW))
        sys.exit(1)

    if not require_tool(FFPROBE_BIN):
        LOGGER.error("Required tool missing: %s", FFPROBE_BIN)
        print(err("ERROR: ffprobe was not found in PATH."))
        print("Install FFmpeg or add ffprobe.exe to PATH, then run this script again.")
        if LOG_FILE:
            print(color(f"Log file: {LOG_FILE}", C.LOG_YELLOW))
        sys.exit(1)

    input_from_args = input_path_from_args(sys.argv[1:])

    while True:
        input_root = input_from_args
        input_from_args = None

        if input_root is not None:
            print(info(f"Input from launcher/drag-drop: {input_root}"))
            LOGGER.info("Input from args: %s", input_root)
            if not input_root.exists():
                LOGGER.warning("Input path from args does not exist: %s", input_root)
                print(err(f"Path does not exist: {input_root}"))
                input_root = None

        if input_root is None:
            input_root = ask_path(
                "Input file or folder path (drag/drop here, then press Enter)",
                must_exist=True,
                allow_back=embedded,
            )
        LOGGER.info("Input root: %s", input_root)
        print()

        files = find_video_files(input_root)
        if not files:
            LOGGER.warning("No supported video files found under %s", input_root)
            print(warn("No supported video files found."))
            print(f"Supported extensions: {', '.join(sorted(VIDEO_EXTENSIONS))}")
            skipped_extensions = find_non_video_extensions(input_root)
            if skipped_extensions:
                shown = ", ".join(skipped_extensions[:12])
                suffix = ", ..." if len(skipped_extensions) > 12 else ""
                print(warn(f"Other file extensions found: {shown}{suffix}"))
            sys.exit(1)

        print(ok(f"Found {len(files)} video file(s)."))
        LOGGER.info("Found %s video file(s)", len(files))

        media_files = scan_files(files)
        if not media_files:
            print(err("No files could be scanned successfully."))
            sys.exit(1)

        print_scan_report(media_files, input_root)
        print_unique_summary(media_files)

        restart_input = False
        rules: Optional[SelectionRules] = None
        while True:
            if rules is None:
                try:
                    rules = configure_rules(media_files)
                except MenuBack:
                    LOGGER.info("Back requested; returning to input path")
                    print(warn("Back. Returning to input path."))
                    restart_input = True
                    break

            output_base: Optional[Path] = None
            while output_base is None:
                try:
                    output_base = ask_output_base_path(input_root)
                except MenuBack:
                    LOGGER.info("Back requested at output folder; returning to previous rule step")
                    print(warn("Back. Returning to previous step."))
                    try:
                        rules = revisit_last_rule_step(media_files, rules)
                    except MenuBack:
                        LOGGER.info("Back requested from first revisited rule step; returning to stream selection")
                        print(warn("Back. Returning to stream selection."))
                        rules = None
                        break

            if output_base is None:
                continue

            try:
                output_root = resolve_output_root(input_root, output_base, rules)
            except RuntimeError as exc:
                LOGGER.exception("Could not resolve output root")
                print(err(f"Could not create a safe output root: {exc}"))
                continue

            LOGGER.info("Configured output base: %s", output_base)
            LOGGER.info("Resolved output root: %s", output_root)

            print_header("Confirm Settings")
            print_setting("Input", input_root)
            print_setting("Output base", output_base)
            print_setting("Output root", output_root)
            print_setting("Audio mode", rules.audio_mode)
            print_setting("Audio languages", rules.audio_languages)
            print_setting("Audio titles", rules.audio_titles)
            print_setting("Audio indexes", rules.audio_indexes)
            print_setting("Subtitle mode", rules.subtitle_mode)
            print_setting("Subtitle languages", rules.subtitle_languages)
            print_setting("Subtitle titles", rules.subtitle_titles)
            print_setting("Subtitle indexes", rules.subtitle_indexes)
            print_setting("Metadata edits", rules.metadata_edits)
            print_setting("Keep attachments", rules.keep_attachments)
            print_setting("Keep metadata", rules.keep_metadata)
            print_setting("Keep chapters", rules.keep_chapters)
            print_setting("Copy non-video files", rules.copy_non_video_files)
            print_setting("Overwrite", rules.overwrite)
            LOGGER.info("Confirmed rules: %s", rules)

            try:
                start_processing = ask_yes_no("Start processing?", True)
            except MenuBack:
                LOGGER.info("Back requested at confirmation; returning to output folder")
                print(warn("Back. Returning to output folder."))
                continue

            if not start_processing:
                LOGGER.info("User cancelled before processing")
                print(warn("Cancelled."))
                return

            process_files(media_files, input_root, output_root, rules)
            print_ready_for_next_task()
            restart_input = True
            break

        if not restart_input:
            break


if __name__ == "__main__":
    try:
        main_menu()
    except MenuExit:
        LOGGER.info("User selected exit")
        print(warn("Exited."))
        sys.exit(0)
    except MenuBack:
        LOGGER.info("Back requested at the first menu")
        print(warn("Back is not available here."))
        sys.exit(0)
    except KeyboardInterrupt:
        print()
        LOGGER.info("Cancelled by keyboard interrupt")
        print(warn("Cancelled by user."))
        sys.exit(130)
    except Exception as exc:
        LOGGER.exception("Unhandled error")
        print(err(f"ERROR: {exc}"))
        if LOG_FILE:
            print(color(f"Log file: {LOG_FILE}", C.LOG_YELLOW))
        sys.exit(1)
