"""FFmWiz helpers (dependency level 0) — concerns: color_range(3).

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


def normalize_color_range(value: Any) -> str:
    """Map any ffprobe/metadata color-range value to 'tv', 'pc', or '' (unspecified)."""
    text = str(value or "").strip().lower()
    return COLOR_RANGE_ALIASES.get(text, "")


def display_color_range(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "unknown":
        return "unknown"
    normalized = text.lower()
    if normalized == "tv":
        return "TV"
    if normalized == "pc":
        return "PC"
    return text.upper() if len(text) <= 3 else text


def copy_cut_removed_ranges(
    keep_ranges: list[tuple[float, float]],
    duration: float,
) -> list[tuple[float, float]]:
    keep = normalize_cut_ranges(keep_ranges, duration)
    removed: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in keep:
        if start > cursor + 1e-6:
            removed.append((cursor, start))
        cursor = max(cursor, end)
    if duration > 0 and cursor < duration - 1e-6:
        removed.append((cursor, duration))
    return removed


__all__ = [
    'normalize_color_range',
    'display_color_range',
    'copy_cut_removed_ranges',
]
