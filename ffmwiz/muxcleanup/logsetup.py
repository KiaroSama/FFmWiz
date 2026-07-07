# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

import logging
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from .constants import APP_VERSION
from .colors import warn

LOGGER = logging.getLogger("MuxCls")


LOG_FILE: Optional[Path] = None


def command_to_text(args: Sequence[object]) -> str:
    return subprocess.list2cmdline([str(arg) for arg in args])


def setup_logging() -> Optional[Path]:
    global LOG_FILE

    try:
        # This module lives in ffmwiz/muxcleanup, so the project root (where the
        # Logs folder belongs) is two directories up from the package.
        log_root = Path(__file__).resolve().parents[2] / "Logs"
        log_root.mkdir(parents=True, exist_ok=True)
        LOG_FILE = log_root / f"muxcls_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"

        LOGGER.setLevel(logging.DEBUG)
        LOGGER.handlers.clear()
        LOGGER.propagate = False

        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        LOGGER.addHandler(handler)
        LOGGER.info("MuxCls started")
        LOGGER.info("MuxCls version: %s", APP_VERSION)
        LOGGER.info("Python: %s", sys.version.replace("\n", " "))
        LOGGER.info("OS: %s", platform.platform())
        LOGGER.info("Log file: %s", LOG_FILE)
        LOGGER.info("Command line: %s", command_to_text(sys.argv))
        return LOG_FILE
    except OSError as exc:
        print(warn(f"Logging disabled: {exc}"))
        LOG_FILE = None
        return None
