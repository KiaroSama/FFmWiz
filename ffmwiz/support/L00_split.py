"""FFmWiz helpers (dependency level 0) — concerns: split(3).

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


def split_ranges_for_reverse_segments(
    ranges: list[tuple[float, float]],
    duration: float,
    segment_seconds: float = REVERSE_SEGMENT_SECONDS,
) -> list[tuple[float, float]]:
    duration = max(0.0, float(duration or 0.0))
    segment_seconds = max(1.0, float(segment_seconds or REVERSE_SEGMENT_SECONDS))
    source_ranges = normalize_cut_ranges(ranges, duration) if ranges else []
    if not source_ranges and duration > 0:
        source_ranges = [(0.0, duration)]
    chunks: list[tuple[float, float]] = []
    for start, end in source_ranges:
        cursor = max(0.0, start)
        end = min(duration, end) if duration > 0 else end
        while cursor < end - 1e-6:
            next_end = min(end, cursor + segment_seconds)
            if next_end > cursor:
                chunks.append((cursor, next_end))
            cursor = next_end
    return chunks


def _split_progress_seconds(
    raw_current_s: float,
    frame_seconds: float,
    part_durations: list[float],
    output_sizes: list[int] | None,
    previous_output_sizes: list[int] | None,
    active_part: int,
    previous_raw_s: float | None = None,
    active_part_start_raw: float = 0.0,
) -> tuple[float, int, float]:
    durations: list[float] = []
    for value in part_durations:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            durations.append(duration)
    if not durations:
        return max(0.0, max(raw_current_s, frame_seconds)), 0, active_part_start_raw
    active = max(0, min(len(durations) - 1, int(active_part or 0)))
    original_active = active
    sizes = list(output_sizes or [])
    previous_sizes = list(previous_output_sizes or [])
    growing_part: int | None = None
    max_delta = 0
    if sizes and previous_sizes:
        deltas: list[int] = []
        for idx, size in enumerate(sizes[:len(durations)]):
            previous = previous_sizes[idx] if idx < len(previous_sizes) else 0
            deltas.append(max(0, int(size) - int(previous)))
        if deltas:
            max_delta = max(deltas)
        if max_delta > 0:
            growing_part = deltas.index(max_delta)
        # Split outputs are written sequentially. Only advance the active part
        # when the growing file shows a MEANINGFUL delta (not just a later
        # part's freshly-opened container header/moov flush) and only one part
        # at a time. This prevents the aggregate percent from jumping forward
        # (e.g. 30% -> 59%) when the next part's file is created early while the
        # current part is still being written.
        if growing_part is not None and max_delta > 65536 and growing_part <= active + 1:
            active = max(active, growing_part)
        else:
            growing_part = None
    elif previous_raw_s is not None and raw_current_s + 0.25 < previous_raw_s and active + 1 < len(durations):
        active += 1

    offset = sum(durations[:active])
    part_duration = durations[active]
    if (
        active + 1 < len(durations)
        and growing_part != active
        and frame_seconds >= offset + part_duration - 0.25
        and 0.0 < raw_current_s < part_duration - 0.25
    ):
        # Multi-output FFmpeg progress commonly freezes frame at the first
        # part's frame count while out_time restarts from zero for the next
        # output. Detect that handoff even before the next output file has
        # flushed enough bytes for file-size delta detection.
        active += 1
        offset = sum(durations[:active])
        part_duration = durations[active]
    # When the active part advances, capture this part's out_time baseline.
    # FFmpeg's multi-output -progress reports out_time in one of two ways
    # depending on build/filters: it either RESETS to zero for each output, or
    # reports the GLOBAL/continuous input position. Detect which: if raw already
    # reached this part's start offset, out_time is global -> baseline = offset;
    # otherwise it reset to part-local 0 -> baseline = 0. Subtracting the
    # baseline collapses BOTH behaviors to true part-local progress and prevents
    # the offset from being double-counted (which otherwise jumps e.g. 30% ->
    # 59% at the boundary and hits 100% while the last part is only half done).
    if active > original_active:
        active_part_start_raw = offset if raw_current_s >= offset - 0.25 else 0.0
    if active == 0:
        local_s = max(raw_current_s, min(frame_seconds, part_duration))
    else:
        local_s = raw_current_s - active_part_start_raw
        if local_s < -0.25:
            # out_time reset to part-local AFTER the baseline was captured;
            # the raw value is already this part's local time.
            local_s = raw_current_s
        if local_s <= 0.0 and frame_seconds > offset:
            local_s = frame_seconds - offset
    local_s = max(0.0, min(part_duration, local_s))
    current_s = max(0.0, min(sum(durations), offset + local_s))
    return current_s, active, active_part_start_raw


def parse_split_timestamp(token: str, bare_unit: str = "s") -> float:
    """Parse one split timestamp. Accepts seconds (e.g. 90 or 90.5),
    MM:SS, MM:SS:mmm, or HH:MM:SS:mmm. The last colon field is milliseconds
    (optional; missing => 0).

    A BARE number with no ':' is interpreted in `bare_unit` ('h', 'm', or 's'),
    which the caller derives from the file's largest time unit (e.g. for a
    31-minute file, "16" means 16 minutes)."""
    token = str(token or "").strip()
    if not token:
        raise ValueError("Empty timestamp.")
    if ":" not in token:
        number = float(token)
        if bare_unit == "h":
            return max(0.0, number * 3600.0)
        if bare_unit == "m":
            return max(0.0, number * 60.0)
        return max(0.0, number)
    parts = token.split(":")
    if any(part.strip() == "" for part in parts[:-1]):
        raise ValueError(f"Invalid timestamp: {token!r}")
    try:
        nums = [int(part) if part.strip() != "" else 0 for part in parts]
    except ValueError:
        raise ValueError(f"Invalid timestamp: {token!r}")
    if len(nums) == 2:
        minutes, seconds = nums
        return minutes * 60 + seconds
    if len(nums) == 3:
        minutes, seconds, ms = nums
        return minutes * 60 + seconds + ms / 1000.0
    if len(nums) == 4:
        hours, minutes, seconds, ms = nums
        return hours * 3600 + minutes * 60 + seconds + ms / 1000.0
    raise ValueError("Use seconds, MM:SS, MM:SS:mmm, or HH:MM:SS:mmm.")


__all__ = [
    'split_ranges_for_reverse_segments',
    '_split_progress_seconds',
    'parse_split_timestamp',
]
