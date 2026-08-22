"""Pure time-format and cut-range / separator math helpers for FFmWiz.

Leaf module extracted verbatim from FFmWiz.py. Depends only on the standard
library and module constants; no dependency on the command/wizard/UI layers.
"""
from __future__ import annotations

import re
import math  # noqa: F401  (kept for parity with source scope)
from typing import Any  # noqa: F401


def format_elapsed(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_progress_duration(seconds: float | None) -> str:
    if seconds is None:
        return "calculating"
    return format_elapsed(seconds)


def format_progress_clock(seconds: float | None) -> str:
    total = max(0, int(round(seconds or 0)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_progress_elapsed_dot(seconds: float | None) -> str:
    total = max(0, int(round(seconds or 0)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}.{secs:02d}"


HMSF_PATTERN = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*:\s*(\d+)\s*:\s*(\d+)\s*$")


def parse_hmsf_time(value: str, fps: float) -> float:
    """Parse a h:m:s:frame string into seconds.

    Small frame overflow is normalized: if frame >= rounded fps, the extra time
    rolls over into seconds. Validation:
      hours   >= 0
      minutes 0-59
      seconds 0-59
      frame   >= 0 and below 10 seconds worth of frames
    """
    if value is None or not str(value).strip():
        raise ValueError("Time is empty. Expected h:m:s:frame, e.g. 00:01:30:12")
    match = HMSF_PATTERN.match(str(value))
    if not match:
        raise ValueError(f"Invalid time format. Expected h:m:s:frame, got {value!r}")
    hours, minutes, seconds, frame = (int(group) for group in match.groups())
    if minutes >= 60:
        raise ValueError(f"Minutes must be 0-59, got {minutes}")
    if seconds >= 60:
        raise ValueError(f"Seconds must be 0-59, got {seconds}")
    if fps <= 0:
        fps = 25.0
    fps_int = max(1, round(fps))
    frame_limit = fps_int * 10
    if frame >= frame_limit:
        raise ValueError(
            f"Frame value is too large for {fps_int} fps: got {frame}, "
            f"maximum accepted overflow is {frame_limit - 1}"
        )
    total = hours * 3600.0 + minutes * 60.0 + seconds + frame / fps
    return total


def seconds_to_hmsf(seconds: float, fps: float) -> str:
    """Format seconds as h:m:s:frame using the supplied FPS."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    if fps <= 0:
        fps = 25.0
    fps_int = max(1, round(fps))
    # Split whole seconds and frames from the SAME clock. The old code counted
    # total frames at the TRUE rate (23.976) and then divided by the ROUNDED one
    # (24), losing (fps_int - fps) / fps_int per second -- 3.58 s per hour at
    # 23.976/29.97/59.94. That also made this the non-inverse of
    # parse_hmsf_time, so a value read off the editor and typed back landed
    # several seconds away. Wall-clock h:m:s is now exact at every rate and
    # `whole_seconds + frame / fps` reproduces the input.
    seconds = float(seconds)
    whole_seconds = int(seconds)
    frame = int(round((seconds - whole_seconds) * fps))
    if frame >= fps_int:
        frame = 0
        whole_seconds += 1
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frame:02d}"


def seconds_to_ffmpeg_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm suitable for FFmpeg -ss / -to."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    total_ms = int(round(float(seconds) * 1000))
    hours, remainder = divmod(total_ms, 3600 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    secs = remainder / 1000.0
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def normalize_cut_ranges(
    ranges: list[tuple[float, float]],
    duration: float,
) -> list[tuple[float, float]]:
    """Clean a list of (start, end) ranges: clamp to [0, duration], drop empty
    or inverted ones, sort, and merge overlapping/touching intervals."""
    duration = max(0.0, float(duration or 0.0))
    cleaned: list[tuple[float, float]] = []
    for start, end in ranges or []:
        try:
            start_value = max(0.0, float(start))
            end_value = float(end)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            start_value = min(start_value, duration)
            end_value = min(end_value, duration)
        if end_value <= start_value:
            continue
        cleaned.append((start_value, end_value))
    cleaned.sort()
    merged: list[tuple[float, float]] = []
    for start, end in cleaned:
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def invert_cut_ranges_to_keep_ranges(
    remove_ranges: list[tuple[float, float]],
    duration: float,
) -> list[tuple[float, float]]:
    """Convert a list of remove ranges into the equivalent keep ranges for the
    file duration. Useful when the user picks 'Remove ...' modes."""
    duration = max(0.0, float(duration or 0.0))
    remove = normalize_cut_ranges(remove_ranges, duration)
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in remove:
        if start > cursor:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        keep.append((cursor, duration))
    return keep


def total_keep_duration(keep_ranges: list[tuple[float, float]]) -> float:
    return sum(max(0.0, end - start) for start, end in keep_ranges)


def normalize_separator_points(points: Any, duration: float) -> list[float]:
    duration = max(0.0, float(duration or 0.0))
    cleaned: list[float] = []
    if duration <= 0:
        return cleaned
    for value in points or []:
        try:
            point = float(value)
        except (TypeError, ValueError):
            continue
        if 1e-6 < point < duration - 1e-6:
            cleaned.append(point)
    return sorted(set(round(point, 6) for point in cleaned))


def separator_ranges(points: Any, duration: float) -> list[tuple[float, float]]:
    duration = max(0.0, float(duration or 0.0))
    if duration <= 0:
        return []
    normalized = normalize_separator_points(points, duration)
    boundaries = [0.0, *normalized, duration]
    ranges: list[tuple[float, float]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        if end > start + 1e-6:
            ranges.append((start, end))
    return ranges


def intersect_keep_ranges_with_segment(
    keep_ranges: list[tuple[float, float]],
    segment: tuple[float, float],
    duration: float,
) -> list[tuple[float, float]]:
    segment_start, segment_end = segment
    source_keeps = normalize_cut_ranges(keep_ranges, duration)
    if not source_keeps:
        source_keeps = [(segment_start, segment_end)]
    intersections: list[tuple[float, float]] = []
    for start, end in source_keeps:
        clipped_start = max(float(start), segment_start)
        clipped_end = min(float(end), segment_end)
        if clipped_end > clipped_start + 1e-6:
            intersections.append((clipped_start, clipped_end))
    return normalize_cut_ranges(intersections, duration)


__all__ = [
    'format_elapsed',
    'format_progress_duration',
    'format_progress_clock',
    'format_progress_elapsed_dot',
    'parse_hmsf_time',
    'seconds_to_hmsf',
    'seconds_to_ffmpeg_time',
    'normalize_cut_ranges',
    'invert_cut_ranges_to_keep_ranges',
    'total_keep_duration',
    'normalize_separator_points',
    'separator_ranges',
    'intersect_keep_ranges_with_segment',
]
