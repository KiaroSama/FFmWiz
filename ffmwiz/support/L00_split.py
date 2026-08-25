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
from typing import Any, Callable, NamedTuple
from urllib.parse import unquote, urlparse

from ffmwiz.core.constants import *  # noqa: F401,F403
from ffmwiz.support.L00_probe import *  # noqa: F401,F403
from ffmwiz.core.colors import *  # noqa: F401,F403
from ffmwiz.core.exceptions import *  # noqa: F401,F403
from ffmwiz.core.timeline import *  # noqa: F401,F403


# FFmpeg time arguments are emitted as `f"{value:.6f}"` (wizard_build.py -> -t,
# ext04b.py -> -ss/-t), which ROUNDS. A 28-frame window at 60 fps is 0.4666666 s
# and prints as `-t 0.466667`, two microseconds past frame 28, so FFmpeg reads a
# 29th frame into the reverse buffer and the plan is one frame over its own cap.
# Planning on the same grid, downwards, keeps the emitted window inside the plan.
COMMAND_TIME_DECIMALS = 6
_COMMAND_TIME_GRID = 10.0 ** COMMAND_TIME_DECIMALS


def _floor_to_command_grid(seconds: float) -> float:
    return math.floor(seconds * _COMMAND_TIME_GRID) / _COMMAND_TIME_GRID


class ReverseFilterInput(NamedTuple):
    """Geometry, rate and format at the INPUT of the `reverse` filter."""

    width: int
    height: int
    fps: float
    pix_fmt: Any


class ReverseSegmentPlan(NamedTuple):
    """One bounded reverse segment, with the whole calculation attached.

    `hard_capped` is the honest half: it is False when the plan had to assume
    something it could not read, or when a single frame already exceeds the
    allowance, because a plan built on an assumption is best-effort and must
    not be advertised as a cap (D12).
    """

    width: int
    height: int
    fps: float
    pix_fmt: Any
    bytes_per_pixel: float
    bytes_per_frame: float
    frames: int
    seconds: float
    overhead_bytes: int
    cap_bytes: int
    peak_bytes: float
    hard_capped: bool
    assumptions: tuple[str, ...]

    @property
    def window_text(self) -> str:
        """The window as a caller should PRINT it.

        `f"{seconds:.0f}s"` renders every legitimate subsecond budget as `0s`,
        which is how a 233 ms window came to be announced as if it were nothing
        (D09). Milliseconds plus the frame count say what was actually planned.
        """
        return format_reverse_segment_window(self.seconds, self.fps, self.frames)

    def describe(self) -> str:
        """The full calculation, for the log the caller owns."""
        return (
            f"{self.width}x{self.height} {self.pix_fmt} at {self.fps:.6g} fps -> "
            f"{self.bytes_per_pixel:g} B/px, "
            f"{self.bytes_per_frame / 1024 ** 2:.2f} MiB/frame (incl. "
            f"{(REVERSE_FRAME_SAFETY - 1) * 100:.0f}% safety), "
            f"{self.frames} frame(s) = {self.window_text}; peak "
            f"{self.peak_bytes / 1024 ** 3:.3f} GiB = "
            f"{self.overhead_bytes / 1024 ** 3:.3f} GiB overhead + frames, cap "
            f"{self.cap_bytes / 1024 ** 3:.3f} GiB"
            + (f"; ASSUMED: {'; '.join(self.assumptions)}" if self.assumptions else "")
        )


def format_reverse_segment_window(seconds: float, fps: float = 0.0,
                                  frames: int = 0) -> str:
    """Render a segment window so a subsecond value is still readable.

    `f"{seconds:.0f}s"` prints every legitimate subsecond budget as `0s` (D09).
    """
    seconds = max(0.0, float(seconds or 0.0))
    if not frames and fps and fps > 0:
        frames = max(1, int(round(seconds * fps)))
    text = f"{seconds * 1000:.0f} ms" if seconds < 1.0 else f"{seconds:.3f} s"
    return f"{text} ({frames} frame{'' if frames == 1 else 's'})" if frames else text


def reverse_filter_input_descriptor(
    source_width: Any,
    source_height: Any,
    source_fps: Any,
    source_pix_fmt: Any = None,
    *,
    crop_size: tuple[Any, Any] | None = None,
    scale_size: tuple[Any, Any] | None = None,
    output_fps: Any = None,
    graph_pix_fmt: Any = None,
) -> ReverseFilterInput:
    """Resolve what the `reverse` filter actually buffers.

    `reverse` holds POST-filter frames, and the CPU graph is
    crop -> fps -> scale/pad -> speed/reverse -> format, so the last geometry
    before `reverse` wins, not the probe. Sizing from the source handed a
    1080p30 clip upscaled to 8K a 15 s window whose real peak is over 24 GiB
    against a 2 GiB cap (D11).

    `scale_size` is the padded TARGET canvas: the aspect-preserving chain is
    `scale=W:H:force_original_aspect_ratio=decrease` followed by `pad=W:H`, so
    the frame entering `reverse` is the full canvas even when the picture
    inside it is letterboxed.

    `graph_pix_fmt` is the format the graph converts to. It matters because the
    trailing `format=` filter sits DOWNSTREAM of `reverse`, and `reverse` passes
    formats through, so FFmpeg negotiates that format back up the chain -- the
    frames in the buffer carry the encoder's format, not the source's.
    """
    def _size(pair):
        if not pair:
            return None
        try:
            width, height = int(pair[0] or 0), int(pair[1] or 0)
        except (TypeError, ValueError, IndexError):
            return None
        return (width, height) if width > 0 and height > 0 else None

    def _rate(value):
        try:
            rate = float(value or 0.0)
        except (TypeError, ValueError):
            return None
        return rate if rate > 0 and math.isfinite(rate) else None

    width, height = (_size(scale_size) or _size(crop_size)
                     or _size((source_width, source_height)) or (0, 0))
    fps = _rate(output_fps) or _rate(source_fps) or 0.0
    return ReverseFilterInput(width, height, fps, graph_pix_fmt or source_pix_fmt)


def reverse_segment_plan(
    width: Any,
    height: Any,
    fps: Any,
    pix_fmt: Any = None,
    *,
    cap_bytes: int | None = None,
    overhead_bytes: int | None = None,
    frame_safety: float | None = None,
    max_seconds: float | None = None,
    best_effort: bool = False,
) -> ReverseSegmentPlan:
    """Plan one reverse segment that fits the peak budget, or refuse.

    Pure, and it lives here rather than in the executor so every reverse entry
    point shares it: the standalone Video Speed / Reverse mode had its own call
    with no budget at all and announced a flat 60 s window, which is 20.9 GiB of
    decoded frames at 4K30 (B06).

    The budget is a PEAK -- reserved process overhead plus frames sized from the
    real pixel format -- and the result is an integer FRAME count, so nothing
    can round it back up past the cap.

    Anything the plan needs but cannot read is a planning error, not a warning:
    the old code assumed 1080p60 10-bit for unknown geometry and carried on,
    which leaves an unknown 8K source completely unbounded (D12). `best_effort`
    is the explicit override, and it returns `hard_capped=False` so the caller
    cannot keep advertising a cap it no longer has.

    An invalid cap always raises. A cap of zero, a negative cap, or a cap at or
    below the reserved overhead is a configuration mistake rather than an
    unknown, and there is no honest segment to return for it.
    """
    cap_bytes = int(REVERSE_PEAK_BUDGET_BYTES if cap_bytes is None else cap_bytes)
    overhead_bytes = int(REVERSE_FIXED_OVERHEAD_BYTES if overhead_bytes is None
                         else overhead_bytes)
    frame_safety = float(REVERSE_FRAME_SAFETY if frame_safety is None else frame_safety)
    max_seconds = float(REVERSE_SEGMENT_SECONDS if max_seconds is None else max_seconds)
    if cap_bytes <= 0:
        raise ReverseBudgetError(
            f"Reverse memory cap must be positive; got {cap_bytes} bytes. "
            "Set FFMWIZ_REVERSE_PEAK_BUDGET_MB to a usable value.")
    if overhead_bytes < 0:
        raise ReverseBudgetError(
            f"Reverse fixed overhead must not be negative; got {overhead_bytes} bytes.")
    allowance = cap_bytes - overhead_bytes
    if allowance <= 0:
        raise ReverseBudgetError(
            f"Reverse memory cap {cap_bytes / 1024 ** 2:.0f} MiB leaves nothing for "
            f"frames: the decoder/encoder working set alone reserves "
            f"{overhead_bytes / 1024 ** 2:.0f} MiB. Raise "
            "FFMWIZ_REVERSE_PEAK_BUDGET_MB above that.")

    assumptions: list[str] = []

    def _unknown(what: str, message: str, fallback):
        if not best_effort:
            raise ReverseBudgetError(message)
        assumptions.append(what)
        return fallback

    try:
        width, height = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        width = height = 0
    if width <= 0 or height <= 0:
        # 4K, not the old 1080p: if we are guessing at all, guess at something
        # demanding enough that the guess is unlikely to be exceeded.
        width, height = _unknown(
            "frame geometry (assumed 3840x2160)",
            "Reverse memory cap needs the frame geometry and the probe did not "
            "report it. Probe the input before planning a reverse, or accept a "
            "best-effort segment that is no longer hard-capped.",
            (3840, 2160))
    try:
        fps = float(fps or 0.0)
    except (TypeError, ValueError):
        fps = 0.0
    if not (fps > 0 and math.isfinite(fps)):
        fps = _unknown(
            "frame rate (assumed 60 fps)",
            "Reverse memory cap needs the frame rate and the probe did not "
            "report it. Probe the input before planning a reverse, or accept a "
            "best-effort segment that is no longer hard-capped.",
            60.0)

    bytes_per_pixel = pixel_format_bytes_per_pixel(pix_fmt)
    if bytes_per_pixel is None:
        name = str(pix_fmt or "").strip().lower() or "<missing>"
        hardware = name in HARDWARE_PIXEL_FORMATS
        bytes_per_pixel = _unknown(
            f"pixel format {name} (assumed {UNKNOWN_PIXEL_FORMAT_BYTES:g} B/px)",
            (f"Reverse memory cap cannot size frames in {name!r}: it is "
             + ("a hardware surface, whose frames are not a byte block this "
                "budget can measure. Decode to a software format first"
                if hardware else
                "not a pixel format this FFmpeg build reports. Probe the input")
             + ", or accept a best-effort segment that is no longer hard-capped."),
            UNKNOWN_PIXEL_FORMAT_BYTES)

    bytes_per_frame = width * height * bytes_per_pixel * frame_safety
    frames = int(allowance // bytes_per_frame) if bytes_per_frame > 0 else 0
    hard_capped = not assumptions
    if frames < 1:
        # `max(1, ...)` used to hide this: a frame that does not fit was still
        # returned, so the cap was broken by the only chunk the plan can make.
        if not best_effort:
            raise ReverseBudgetError(
                f"One decoded {width}x{height} {str(pix_fmt or 'unknown')} frame is "
                f"{bytes_per_frame / 1024 ** 2:.0f} MiB and the reverse budget only "
                f"allows {allowance / 1024 ** 2:.0f} MiB "
                f"({cap_bytes / 1024 ** 2:.0f} MiB cap less "
                f"{overhead_bytes / 1024 ** 2:.0f} MiB reserved overhead). Raise "
                "FFMWIZ_REVERSE_PEAK_BUDGET_MB or reverse a smaller frame.")
        assumptions.append("a single frame exceeds the allowance")
        hard_capped = False
        frames = 1
    frames = min(frames, max(1, int(max_seconds * fps)))
    return ReverseSegmentPlan(
        width=width, height=height, fps=fps, pix_fmt=pix_fmt,
        bytes_per_pixel=bytes_per_pixel, bytes_per_frame=bytes_per_frame,
        frames=frames, seconds=_floor_to_command_grid(frames / fps),
        overhead_bytes=overhead_bytes, cap_bytes=cap_bytes,
        peak_bytes=overhead_bytes + bytes_per_frame * frames,
        hard_capped=hard_capped, assumptions=tuple(assumptions))


def reverse_segment_seconds_for(width: int, height: int, fps: float,
                                pix_fmt: Any = None, **kwargs: Any) -> float:
    """Seconds of video one reverse segment may hold within the peak budget.

    Thin float view of `reverse_segment_plan` for callers that only want the
    window; it raises the same `ReverseBudgetError` and takes the same keyword
    options. Callers that need the frame count, the peak, or the
    hard-capped/best-effort distinction ask for the plan instead.
    """
    return reverse_segment_plan(width, height, fps, pix_fmt, **kwargs).seconds


def split_ranges_for_reverse_segments(
    ranges: list[tuple[float, float]],
    duration: float,
    segment_seconds: float = REVERSE_SEGMENT_SECONDS,
    fps: float | None = None,
) -> list[tuple[float, float]]:
    """Tile the kept ranges into chunks no longer than `segment_seconds`.

    There is no one-second floor. The floor that used to sit here threw away
    every subsecond budget the calculator produced, so the executor always ran
    at least a second of frames: 2.10 GiB at 4K60 10-bit, 3.70 GiB at 8K60
    8-bit, 6.90 GiB at 8K60 10-bit and 102.8 GiB at 16K120 12-bit 4:4:4 against
    a 2 GiB cap (D09). The smallest legal chunk is ONE FRAME.

    Pass `fps` to cut on the frame grid: the chunk becomes a whole number of
    frames, so a boundary cannot land mid-frame and pull an extra frame into
    the buffer.
    """
    duration = max(0.0, float(duration or 0.0))
    try:
        step = float(segment_seconds or 0.0)
    except (TypeError, ValueError):
        step = 0.0
    if step <= 0:
        step = float(REVERSE_SEGMENT_SECONDS)
    try:
        rate = float(fps or 0.0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate > 0 and math.isfinite(rate):
        # 1e-3 of a frame of tolerance: the incoming budget has already been
        # floored to the command grid, so 28 frames arrives as 27.99996 and a
        # bare int() would silently drop a frame from every chunk.
        step = max(1, math.floor(step * rate + 1e-3)) / rate
    step = _floor_to_command_grid(step)
    # A step of zero would not terminate the tiling loop, and below the command
    # grid it prints as `-t 0.000000`. With a known rate the frame count already
    # bounds the chunk, so only the rate-less path needs the coarser floor --
    # raising it there too would hand back more than one frame above 1000 fps.
    step = max(step, 1e-6 if rate > 0 else 1e-3)
    source_ranges = normalize_cut_ranges(ranges, duration) if ranges else []
    if not source_ranges and duration > 0:
        source_ranges = [(0.0, duration)]
    chunks: list[tuple[float, float]] = []
    for start, end in source_ranges:
        cursor = max(0.0, start)
        end = min(duration, end) if duration > 0 else end
        while cursor < end - 1e-6:
            next_end = min(end, cursor + step)
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
    'COMMAND_TIME_DECIMALS',
    'ReverseFilterInput',
    'ReverseSegmentPlan',
    'format_reverse_segment_window',
    'reverse_filter_input_descriptor',
    'reverse_segment_plan',
    'reverse_segment_seconds_for',
    'split_ranges_for_reverse_segments',
    '_split_progress_seconds',
    'parse_split_timestamp',
]
