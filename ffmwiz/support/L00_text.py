"""FFmWiz helpers (dependency level 0) — concerns: text(7).

Extracted verbatim from FFmWiz.py; imports ffmwiz.core.* and lower levels.
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
import concurrent.futures
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from ffmwiz.core.constants import *  # noqa: F401,F403
from ffmwiz.core.colors import *  # noqa: F401,F403
from ffmwiz.core.exceptions import *  # noqa: F401,F403
from ffmwiz.core.timeline import *  # noqa: F401,F403


def command_to_text(args: Any) -> str:
    try:
        return subprocess.list2cmdline([str(arg) for arg in args])
    except Exception:
        try:
            return " ".join(str(arg) for arg in args)
        except Exception:
            return str(args)


def _utc_now_text() -> str:
    """Current UTC timestamp, second precision, no milliseconds."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _path_exists_text(path: Path) -> str:
    try:
        return "yes" if path.exists() else "no"
    except Exception as exc:
        return f"unknown ({exc})"


def _plain_number_text(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    return str(value).strip()


def mux_parse_csv_text(raw: str) -> list[str]:
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def options_text(items: list[str], extra: list[str] | None = None) -> str:
    values = []
    if extra:
        values.extend(extra)
    values.extend(items)
    return ",".join(dict.fromkeys(values))


def metadata_dispositions_text(ffmpeg: str) -> str:
    args = [ffmpeg, "-hide_banner", "-dispositions"]
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", check=False)
    return (result.stdout or result.stderr or "").strip()


__all__ = [
    'command_to_text',
    '_utc_now_text',
    '_path_exists_text',
    '_plain_number_text',
    'mux_parse_csv_text',
    'options_text',
    'metadata_dispositions_text',
]
