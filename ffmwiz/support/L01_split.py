"""FFmWiz helpers (dependency level 1) — concerns: split(1).

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


def parse_split_times_line(text: str, duration: float) -> list[float]:
    """Parse a comma-separated list of split timestamps into sorted, unique
    points strictly inside (0, duration). Bare numbers (no ':') use the file's
    largest time unit. Points at/after the duration or <=0 are dropped."""
    bare_unit = largest_time_unit(duration)
    points: list[float] = []
    for token in str(text or "").split(","):
        token = token.strip()
        if not token:
            continue
        points.append(parse_split_timestamp(token, bare_unit))
    cleaned = sorted({round(p, 6) for p in points if p > 0.0 and (not duration or p < duration - 1e-6)})
    return cleaned


__all__ = [
    'parse_split_times_line',
]
