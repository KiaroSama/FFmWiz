"""FFmWiz helpers (dependency level 0) — concerns: probe(2).

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


def parse_rational(text: Any) -> float | None:
    """Parse 'N:M', 'N/M', or a float to a positive finite float. Return None for
    unknown/empty/invalid/zero/negative/non-finite values (so callers can detect
    them). Examples accepted: 1:1, 9:16, 16:15, 64:45, 30000/1001, decimals.
    Rejected: 0:1, zero/negative denominators, malformed values, NaN, infinity."""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw or raw.lower() in {"n/a", "unknown", "none"}:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)", raw)
    if match:
        num = float(match.group(1))
        den = float(match.group(2))
        if num > 0 and den > 0 and math.isfinite(num) and math.isfinite(den):
            return num / den
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if (val > 0 and math.isfinite(val)) else None


def rational_to_float(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            num_i = float(num)
            den_i = float(den)
            return None if den_i == 0 else num_i / den_i
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


__all__ = [
    'parse_rational',
    'rational_to_float',
]
