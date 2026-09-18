# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from .colors import warn
from .constants import APP_VERSION, OPERATION_TIMEOUT_ENV_VAR

LOGGER = logging.getLogger("MuxCls")


LOG_FILE: Path | None = None


# Set MUXCLS_DEBUG=1 to record command lines and captured output for every
# command. The default INFO level keeps a run of a few hundred files readable.
DEBUG_ENV_VAR = "MUXCLS_DEBUG"


# Captured stdout/stderr is only useful up to a point; a full FFmpeg dump can be
# thousands of lines and buries the entries that matter.
MAX_CAPTURED_OUTPUT_CHARS = 2000


def debug_enabled() -> bool:
    return os.environ.get(DEBUG_ENV_VAR, "").strip().lower() not in {"", "0", "false", "no"}


def command_to_text(args: Sequence[object]) -> str:
    return subprocess.list2cmdline([str(arg) for arg in args])


def truncate_output(text: str, limit: int = MAX_CAPTURED_OUTPUT_CHARS) -> str:
    stripped = (text or "").strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit] + f"... [{len(stripped) - limit} more characters]"


def log_command_output(label: str, returncode: int, stdout: str, stderr: str) -> None:
    """Record captured output only when it can explain something: a failure, or
    an explicit debug run. Successful commands stay a single line."""
    failed = returncode != 0
    if not failed and not debug_enabled():
        return

    level = logging.ERROR if failed else logging.DEBUG
    if stdout.strip():
        LOGGER.log(level, "%s stdout: %s", label, truncate_output(stdout))
    if stderr.strip():
        LOGGER.log(level, "%s stderr: %s", label, truncate_output(stderr))


def setup_logging() -> Path | None:
    global LOG_FILE

    try:
        # This module lives in ffmwiz/muxcleanup, so the project root (where the
        # Logs folder belongs) is two directories up from the package.
        log_root = Path(__file__).resolve().parents[2] / "Logs"
        log_root.mkdir(parents=True, exist_ok=True)
        started = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        LOG_FILE = log_root / f"muxcls_{started}_UTC.log"

        level = logging.DEBUG if debug_enabled() else logging.INFO
        LOGGER.setLevel(level)
        LOGGER.handlers.clear()
        LOGGER.propagate = False

        formatter = logging.Formatter(
            "[%(asctime)s UTC] [%(levelname)s] [%(module)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        formatter.converter = time.gmtime
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(formatter)
        LOGGER.addHandler(handler)

        LOGGER.info(
            "MuxCls %s started | python=%s | os=%s | log=%s",
            APP_VERSION,
            sys.version.split()[0],
            platform.platform(),
            LOG_FILE,
        )
        # The environment a run happened in is not reconstructable afterwards,
        # and it is what most "it behaved differently on my machine" reports turn
        # out to be about.
        LOGGER.info("Command line: %s", command_to_text(sys.argv))
        LOGGER.info("Working directory: %s", Path.cwd())
        LOGGER.info(
            "Console: tty=%s encoding=%s size=%sx%s | filesystem encoding=%s",
            getattr(sys.stdout, "isatty", lambda: False)(),
            getattr(sys.stdout, "encoding", "?"),
            shutil.get_terminal_size((80, 24)).columns,
            shutil.get_terminal_size((80, 24)).lines,
            sys.getfilesystemencoding(),
        )
        LOGGER.info(
            "Settings: %s=%s | %s=%s",
            DEBUG_ENV_VAR,
            os.environ.get(DEBUG_ENV_VAR, "(unset)"),
            OPERATION_TIMEOUT_ENV_VAR,
            os.environ.get(OPERATION_TIMEOUT_ENV_VAR, "(unset, default 3h)"),
        )
        return LOG_FILE
    except OSError as exc:
        print(warn(f"Logging disabled: {exc}"))
        LOG_FILE = None
        return None
