"""FFmWiz helpers (dependency level 1) — concerns: text(4).

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


def join_summary_duration_text(info: dict[str, Any]) -> str:
    """Plain-text 'Total raw duration' value built from join_summary_total_duration."""
    if info["known_count"] == 0:
        return "unavailable"
    text = _format_hms_ms(info["total_seconds"])
    if info["frames"] is not None:
        text += f" (~{info['frames']:,} frames {info['frame_basis']})"
    if info["unknown_count"]:
        text += f"; {info['unknown_count']} files unknown"
    return text


def media_info_fps_text(value: Any) -> str | None:
    text = str(value).strip()
    fps = rational_to_float(text)
    if fps is None:
        return None
    return f"{text} fps ({format(fps, '.5g')} fps)"


def media_info_time_base_text(value: Any) -> str | None:
    text = str(value).strip()
    seconds_per_tick = rational_to_float(text)
    if seconds_per_tick is None:
        return None
    return f"{text} s/tick ({_trim_float(seconds_per_tick)} s/tick)"


def mux_center_text(text: str) -> str:
    return text.center(mux_terminal_width())


__all__ = [
    'join_summary_duration_text',
    'media_info_fps_text',
    'media_info_time_base_text',
    'mux_center_text',
]
