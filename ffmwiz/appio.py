"""FFmWiz application I/O + logging foundation.

Holds the interactive primitives (error, note, ask_*, question_prompt, paint)
and the logging subsystem, which are mutually dependent (error -> log_info ->
_LOGGER <- setup_logging -> error). Patched by tests as ffmwiz.appio.<name>.
Imports ffmwiz.core.* and ffmwiz.support.*; contains no higher-layer deps.
"""
from __future__ import annotations

import os
import sys
import re
import math
import json
import time
import shutil
import subprocess
import tempfile
import platform
import datetime
import uuid
import hashlib
import html
import csv
import logging
import atexit
import concurrent.futures
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from ffmwiz.core.constants import *  # noqa: F401,F403
from ffmwiz.core.colors import *  # noqa: F401,F403
from ffmwiz.core.exceptions import *  # noqa: F401,F403
from ffmwiz.core.timeline import *  # noqa: F401,F403
from ffmwiz.support.L00_audio import *  # noqa: F401,F403
from ffmwiz.support.L00_color_range import *  # noqa: F401,F403
from ffmwiz.support.L00_encode_opts import *  # noqa: F401,F403
from ffmwiz.support.L00_filters import *  # noqa: F401,F403
from ffmwiz.support.L00_metadata import *  # noqa: F401,F403
from ffmwiz.support.L00_misc import *  # noqa: F401,F403
from ffmwiz.support.L00_naming import *  # noqa: F401,F403
from ffmwiz.support.L00_paths import *  # noqa: F401,F403
from ffmwiz.support.L00_probe import *  # noqa: F401,F403
from ffmwiz.support.L00_split import *  # noqa: F401,F403
from ffmwiz.support.L00_streams import *  # noqa: F401,F403
from ffmwiz.support.L00_text import *  # noqa: F401,F403
from ffmwiz.support.L01_audio import *  # noqa: F401,F403
from ffmwiz.support.L01_color_range import *  # noqa: F401,F403
from ffmwiz.support.L01_encode_opts import *  # noqa: F401,F403
from ffmwiz.support.L01_filters import *  # noqa: F401,F403
from ffmwiz.support.L01_metadata import *  # noqa: F401,F403
from ffmwiz.support.L01_misc import *  # noqa: F401,F403
from ffmwiz.support.L01_naming import *  # noqa: F401,F403
from ffmwiz.support.L01_paths import *  # noqa: F401,F403
from ffmwiz.support.L01_split import *  # noqa: F401,F403
from ffmwiz.support.L01_streams import *  # noqa: F401,F403
from ffmwiz.support.L01_text import *  # noqa: F401,F403
from ffmwiz.support.L02 import *  # noqa: F401,F403
from ffmwiz.support.L03 import *  # noqa: F401,F403
from ffmwiz.support.L04 import *  # noqa: F401,F403
from ffmwiz.support.L05 import *  # noqa: F401,F403
from ffmwiz.support.L06 import *  # noqa: F401,F403
from ffmwiz.support.L07 import *  # noqa: F401,F403


USE_COLOR = os.environ.get("NO_COLOR") is None


def paint(text: str, color_code: str) -> str:
    if not USE_COLOR:
        return text
    return f"{color_code}{text}{Color.RESET}"


def error(message: str) -> None:
    print(paint(message, Color.RED))
    try:
        log_error(message)
    except NameError:
        pass


def note(message: str) -> None:
    print(paint(message, Color.NOTE_YELLOW))
    try:
        log_info(message)
    except NameError:
        pass


def back_text(text: str = "back=0, quit=exit") -> str:
    parts = []
    for part in text.split(", "):
        lowered = part.lower()
        if "back" in lowered:
            parts.append(paint(part, Color.BACK_PROMPT))
        elif "exit" in lowered:
            parts.append(paint(part, Color.EXIT_PROMPT))
        elif "folder" in lowered:
            parts.append(paint(part, Color.FOLDER_PROMPT))
        else:
            parts.append(paint(part, Color.WHITE))
    return "{" + ", ".join(parts) + "}"


_LOG_PATH: Path | None = None


_LOGGER: logging.Logger | None = None


_LOG_TO_CONSOLE_FALLBACK = False


_EXECUTION_ID: str = ""


_SESSION_START_MONOTONIC: float | None = None


_SHUTDOWN_LOGGED = False


_DEFAULT_LOG_COMPONENT = "FFmWiz"


def _logs_dir() -> Path:
    return script_dir() / LOGS_DIR_NAME


class _LogContextFilter(logging.Filter):
    """Guarantee a [COMPONENT] field on every record and redact secrets from the
    fully-rendered message before it is written to the file."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "component") or not getattr(record, "component"):
            # Almost no call site passes a component, so 96 of 115 lines in a
            # real session log all read [FFmWiz] -- the field carried no
            # information and the log could not be filtered by subsystem.
            # `_emit_log` sets stacklevel so record.module is the module that
            # actually logged, which is both free and more specific than any
            # name a call site would have bothered to pass.
            record.component = getattr(record, "module", None) or _DEFAULT_LOG_COMPONENT
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

    def formatTime(self, record, datefmt=None):  # noqa: N802 (Qt/py style)
        ct = self.converter(record.created)
        return time.strftime(datefmt or "%Y-%m-%d %H:%M:%S", ct)


def _logging_enabled_from_config() -> bool:
    value = _config_setting_for_logging("logging_enabled", True)
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    return parse_bool_config(str(value).strip(), True)


def _log_level_from_config() -> int:
    """File log level. Defaults to DEBUG so support evidence is never lost by
    surprise; set log_level=info (or FFMWIZ_LOG_LEVEL=info) for a lean log.

    More than half of a real session log is DEBUG detail, so this is the knob
    for users who want the file small rather than complete.
    """
    names = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING,
             "warn": logging.WARNING, "error": logging.ERROR, "critical": logging.CRITICAL}
    value = os.environ.get("FFMWIZ_LOG_LEVEL") or _config_setting_for_logging("log_level", "debug")
    return names.get(str(value).strip().lower(), logging.DEBUG)


def _log_retention_days_from_config() -> int:
    value = _config_setting_for_logging("log_retention_days", 0)
    try:
        return max(0, int(str(value).strip()))
    except Exception:
        return 0


def setup_logging() -> Path | None:
    """Initialize the professional file logger and return the log path.

    A new UTF-8 log file is created for every execution, named
    ffmwiz_<UTC-timestamp>_UTC.log with a collision-resistant suffix. Entries
    use the structure '[YYYY-MM-DD HH:mm:ss UTC] [LEVEL] [COMPONENT] message',
    secrets are redacted, and handlers are flushed/closed at process exit.
    Subsequent calls are no-ops and return the existing path."""
    global _LOG_PATH, _LOGGER, _LOG_TO_CONSOLE_FALLBACK, _EXECUTION_ID, _SESSION_START_MONOTONIC
    if _LOGGER is not None:
        return _LOG_PATH
    if not _logging_enabled_from_config():
        _LOGGER = None
        _LOG_PATH = None
        return None
    _EXECUTION_ID = uuid.uuid4().hex[:12]
    _SESSION_START_MONOTONIC = time.monotonic()
    try:
        logs_dir = _logs_dir()
        logs_dir.mkdir(parents=True, exist_ok=True)
        _prune_old_logs(logs_dir, _log_retention_days_from_config())
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        # Collision-resistant: never overwrite a previous execution's log.
        candidate = logs_dir / f"ffmwiz_{stamp}_UTC.log"
        if candidate.exists():
            candidate = logs_dir / f"ffmwiz_{stamp}_UTC_{_EXECUTION_ID}.log"
        _LOG_PATH = candidate
        logger = logging.getLogger("ffmwiz")
        logger.setLevel(_log_level_from_config())
        # Wipe any handlers added by previous runs in the same process.
        for h in list(logger.handlers):
            logger.removeHandler(h)
        handler = logging.FileHandler(_LOG_PATH, encoding="utf-8")
        handler.setFormatter(_UtcFormatter(
            "[%(asctime)s UTC] [%(levelname)s] [%(component)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        handler.addFilter(_LogContextFilter())
        logger.addHandler(handler)
        logger.propagate = False
        _LOGGER = logger
        _LOG_TO_CONSOLE_FALLBACK = False
        atexit.register(shutdown_logging)
        log_info("FFmWiz session started", component="Startup")
        return _LOG_PATH
    except Exception as exc:
        # Logging must never break the wizard. Fall back to a clear console
        # notice so the user knows persistent logging is unavailable.
        _LOGGER = None
        _LOG_PATH = None
        _LOG_TO_CONSOLE_FALLBACK = True
        try:
            error(f"Persistent file logging is unavailable ({exc}). Continuing without a log file.")
        except Exception:
            pass
        return None


def shutdown_logging(exit_code: Any = None) -> None:
    """Flush and close all log handlers once, recording total session duration.
    Safe to call multiple times (registered via atexit and callable manually)."""
    global _SHUTDOWN_LOGGED
    if _LOGGER is None or _SHUTDOWN_LOGGED:
        return
    _SHUTDOWN_LOGGED = True
    try:
        if _SESSION_START_MONOTONIC is not None:
            elapsed = time.monotonic() - _SESSION_START_MONOTONIC
            log_info(
                f"FFmWiz session ended; total duration={elapsed:.2f}s"
                + (f"; exit_code={exit_code}" if exit_code is not None else ""),
                component="Shutdown",
            )
        for handler in list(_LOGGER.handlers):
            try:
                handler.flush()
                handler.close()
            except Exception:
                pass
            _LOGGER.removeHandler(handler)
    except Exception:
        pass


def log_path() -> Path | None:
    return _LOG_PATH


def _emit_log(level: int, msg: str, component: str | None, exc_info: bool = False) -> None:
    if _LOGGER is None:
        return
    try:
        extra = {"component": component} if component else None
        # stacklevel=3 walks past _emit_log and its log_*() wrapper so the
        # record's module/lineno describe the code that actually logged, not
        # this file. _LogContextFilter uses that as the [COMPONENT] field.
        _LOGGER.log(level, msg, exc_info=exc_info, extra=extra, stacklevel=3)
    except Exception:
        pass


def log_info(msg: str, component: str | None = None) -> None:
    _emit_log(logging.INFO, msg, component)


def log_warn(msg: str, component: str | None = None) -> None:
    _emit_log(logging.WARNING, msg, component)


def log_error(msg: str, component: str | None = None) -> None:
    _emit_log(logging.ERROR, msg, component)


def log_critical(msg: str, component: str | None = None) -> None:
    _emit_log(logging.CRITICAL, msg, component)


def log_debug(msg: str, component: str | None = None) -> None:
    _emit_log(logging.DEBUG, msg, component)


def log_exception(msg: str, component: str | None = None) -> None:
    _emit_log(logging.ERROR, msg, component, exc_info=True)


def log_environment(extra: dict[str, Any] | None = None) -> None:
    """Log app/system/python/runtime environment so every run is traceable."""
    log_info("=" * 72, component="Startup")
    log_info(f"FFmWiz version: {APP_VERSION}", component="Startup")
    log_info(f"Execution ID: {_EXECUTION_ID or '(none)'}", component="Startup")
    log_info(f"Session start (UTC): {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC", component="Startup")
    try:
        log_info(f"OS: {platform.system()} {platform.release()} ({platform.version()}); arch={platform.machine()}", component="Startup")
    except Exception:
        pass
    log_info(f"Python: {sys.version.replace(chr(10), ' ')}", component="Startup")
    log_info(f"Executable: {sys.executable}", component="Startup")
    log_info(f"Script: {Path(__file__).resolve()}", component="Startup")
    log_info(f"CWD: {Path.cwd()}", component="Startup")
    try:
        log_info(f"Process ID: {os.getpid()}", component="Startup")
    except Exception:
        pass
    if _LOG_PATH is not None:
        log_info(f"Log file: {_LOG_PATH}", component="Startup")
    log_info(f"Command line: {command_to_text(sys.argv)}", component="Startup")
    if extra:
        for k, v in extra.items():
            log_info(f"{k}: {v}", component="Startup")


def log_command(label: str, cmd: list[str], extra_args: list[str] | None = None) -> None:
    """Record one command: a pasteable line at INFO, the exact argv at DEBUG.

    A single run used to emit the SAME command four times -- "display command"
    and "actual argv" from here, then both again from the progress wrapper with
    only `-progress pipe:1 -nostats` added. That was ~2.5 KB of near-identical
    text per invocation and it buried everything else in the log.

    The two forms that remain answer different questions: the INFO line is what
    a human re-runs in a terminal, the DEBUG argv is the exact token split when
    an argument's quoting is itself the suspect. `extra_args` records what the
    progress wrapper injected without re-dumping the whole command.
    """
    log_info(f"{label}: {command_to_text(cmd)}")
    log_debug(f"{label} argv: {json.dumps([str(part) for part in cmd], ensure_ascii=False)}")
    if extra_args:
        log_debug(f"{label} progress args: {' '.join(str(part) for part in extra_args)}")


def question_prompt(
    answers: dict[str, Any],
    title: str,
    details: str | None = None,
    default: str | None = None,
    back: str = "back=0, quit=exit",
) -> str:
    number = answers.get("_question_number", "?")
    prompt = paint(f"{number}. {title}", Color.BOLD)
    if details:
        prompt += f" ({paint(details, Color.HINT_YELLOW)})"
    if default is not None:
        prompt += f" {paint('[' + default + ']', Color.GREEN)}"
    if back:
        prompt += f" {back_text(back)}"
    return "\n" + prompt + ": "


def _log_file_text() -> str:
    path = log_path()
    return str(path) if path is not None else "(logging is disabled)"


def ask_raw(prompt: str) -> str:
    value = strip_quotes(input(prompt).strip())
    if value.lower() == "exit":
        log_info(f"User input: prompt={_strip_ansi(prompt).strip().replace(chr(10), ' ')}; action=quit")
        raise ExitWizard()
    log_info(
        "User input: "
        f"prompt={_strip_ansi(prompt).strip().replace(chr(10), ' ')}; "
        f"value={value!r}; default_used={'yes' if value == '' else 'no'}; "
        f"action={'back' if is_back_value(value, allow_text=True) else 'answer'}"
    )
    return value


def ask_required(prompt: str, allow_n: bool = False) -> str:
    while True:
        value = ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        if value:
            if value.lower() == "n" and not allow_n:
                error("n is not valid for this question. Enter a valid value.")
                continue
            return value
        error("This value cannot be empty. Try again.")


def ask_yes_no(prompt: str, default: bool) -> bool:
    default_text = "y" if default else "n"
    while True:
        value = ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        if not value:
            log_info(f"User choice: yes_no={default}; default_used=yes")
            return default
        lowered = value.lower()
        if lowered in {"y", "yes"}:
            log_info("User choice: yes_no=True; default_used=no")
            return True
        if lowered in {"n", "no"}:
            log_info("User choice: yes_no=False; default_used=no")
            return False
        error(f"Enter only y or n. Default on Enter: {default_text}")



# --- secret redaction (used by the log formatter) ---
_SECRET_KEY_RE = re.compile(
    r"(?i)\b(pass(?:word|wd)?|tokens?|api[_-]?keys?|secrets?|access[_-]?tokens?|"
    r"refresh[_-]?tokens?|client[_-]?secrets?|private[_-]?keys?|authorization|"
    r"signing[_-]?secret|webhook[_-]?secret)\b(\s*[:=]\s*|\s+)([^\s,;\"']+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_URL_CRED_RE = re.compile(r"://([^:@/\s]+):([^@/\s]+)@")


def redact_secrets(text: Any) -> str:
    """Mask credentials/tokens in a log string without altering ordinary text."""
    if text is None:
        return ""
    out = str(text)
    try:
        # Bearer tokens first so 'Authorization: Bearer <jwt>' has the token
        # removed before the key/value rule masks the rest of the field.
        out = _BEARER_RE.sub("Bearer [REDACTED]", out)
        out = _SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", out)
        out = _URL_CRED_RE.sub(r"://\1:[REDACTED]@", out)
    except Exception:
        return out
    return out


__all__ = [
    'ask_raw',
    'ask_required',
    'ask_yes_no',
    'back_text',
    'error',
    'log_command',
    'log_critical',
    'log_debug',
    'log_environment',
    'log_error',
    'log_exception',
    '_log_file_text',
    'log_info',
    'log_path',
    'log_warn',
    'note',
    'paint',
    'question_prompt',
    'setup_logging',
    'shutdown_logging',
    'USE_COLOR',
    'redact_secrets',
]
