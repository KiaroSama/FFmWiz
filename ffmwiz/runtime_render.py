"""FFmWiz progress-line rendering helpers, split from runtime.py for file size.

Pure string/layout builders for the in-place FFmpeg progress line. Not
monkeypatched; back-imports runtime and is re-exported by it.
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
import logging
import atexit
import concurrent.futures
import queue
import threading
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
from ffmwiz.support.L01_audio import *  # noqa: F401,F403
from ffmwiz.support.L01_color_range import *  # noqa: F401,F403
from ffmwiz.support.L01_encode_opts import *  # noqa: F401,F403
from ffmwiz.support.L01_filters import *  # noqa: F401,F403
from ffmwiz.support.L01_metadata import *  # noqa: F401,F403
from ffmwiz.support.L01_misc import *  # noqa: F401,F403
from ffmwiz.support.L01_naming import *  # noqa: F401,F403
from ffmwiz.support.L01_paths import *  # noqa: F401,F403
from ffmwiz.support.L01_split import *  # noqa: F401,F403
from ffmwiz.support.L01_streams import *  # noqa: F401,F403
from ffmwiz.support.L01_text import *  # noqa: F401,F403
from ffmwiz.support.L02 import *  # noqa: F401,F403
from ffmwiz.support.L03 import *  # noqa: F401,F403
from ffmwiz.support.L04 import *  # noqa: F401,F403
from ffmwiz.support.L05 import *  # noqa: F401,F403
from ffmwiz.support.L06 import *  # noqa: F401,F403
from ffmwiz.support.L07 import *  # noqa: F401,F403
from ffmwiz.support.ext00 import *  # noqa: F401,F403
from ffmwiz.support.ext01 import *  # noqa: F401,F403
from ffmwiz.support.ext02 import *  # noqa: F401,F403
from ffmwiz.support.ext03 import *  # noqa: F401,F403
from ffmwiz.appio import *  # noqa: F401,F403
# The facade back-import was deleted: every name this module uses comes
# from the LOWER tiers above, which the facade only re-exported. Importing
# it here bought nothing and made this module unimportable on its own,
# because the facade ends with `__all__ += <this module>.__all__` and
# reached that line while this module was still on its first statements.


# The progress-line colour table. It lives with the renderer that reads it;
# runtime re-exports it (see its __all__) for `from ffmwiz.runtime import *`.
PROGRESS_COLORS: dict[str, str] = {
    "percent": Color.PROGRESS_PERCENT,
    "time": Color.PROGRESS_TIME,
    "total": Color.GRAY,
    "fps": Color.PROGRESS_FPS,
    "q": Color.PROGRESS_Q,
    "speed": Color.PROGRESS_SPEED,
    "size": Color.PROGRESS_SIZE,
    "bitrate": Color.PROGRESS_BITRATE,
    "elapsed": Color.PROGRESS_ELAPSED,
    "eta_label": Color.PROGRESS_ETA_LABEL,
    "eta_value": Color.PROGRESS_ETA_VALUE,
    "separator": Color.DIM,
}


def _progress_terminal_width() -> int:
    try:
        # No 60-column floor: over-reporting the width is what makes the
        # single-row clamp wrap on a genuinely narrow terminal, and a wrapped
        # line defeats the carriage-return redraw. A missing/zero size is
        # already covered by the (100, 20) fallback.
        return max(20, shutil.get_terminal_size((100, 20)).columns)
    except Exception:
        return 100


def _join_progress_segments(
    segments: list[tuple[str, str]],
    colorize: bool,
    separator: str = "  •  ",
) -> str:
    sep = _progress_colorize(separator, PROGRESS_COLORS["separator"], colorize)
    return sep.join(text if not color else _progress_colorize(text, color, colorize) for text, color in segments)


def _render_progress_line(state: dict[str, str], total_duration: float | None,
                          started_at: float, max_width: int | None = None) -> str:
    """Format a single FFmpeg progress status line."""
    current_s = _progress_seconds_from_state(state)
    if state.get("progress") == "end" and total_duration and total_duration > 0:
        current_s = max(current_s, float(total_duration))

    def q_value() -> str:
        for key in ("stream_0_0_q", "q"):
            value = state.get(key)
            if value:
                return value
        for key, value in state.items():
            if key.endswith("_q") and value:
                return value
        return "N/A"

    def visible_value(value: Any) -> str:
        return str(value or "").strip()

    def has_real_value(value: Any) -> bool:
        text = visible_value(value)
        return bool(text) and text.upper() not in {"N/A", "NA", "NONE", "NULL", "-", "-1", "-1.0"}

    def output_size() -> str:
        override = state.get("_ffmwiz_size_text")
        if override:
            return override
        value = state.get("total_size", "")
        if value.isdigit():
            return (
                _human_size(max(0, int(value)))
                .replace("KiB", "KB")
                .replace("MiB", "MB")
                .replace("GiB", "GB")
                .replace("TiB", "TB")
            )
        return "N/A"

    def bitrate_value() -> str:
        value = state.get("bitrate", "N/A") or "N/A"
        if state.get("_ffmwiz_bitrate_text") and (
            state.get("_ffmwiz_prefer_elapsed_speed") or state.get("_ffmwiz_size_source") == "file"
        ):
            return str(state["_ffmwiz_bitrate_text"])
        if value == "N/A" and state.get("_ffmwiz_bitrate_text"):
            return str(state["_ffmwiz_bitrate_text"])
        return value

    def fps_value() -> str:
        value = state.get("fps", "N/A") or "N/A"
        return "N/A" if value in {"0", "0.0", "0.00"} else value

    def speed_value() -> str:
        override = state.get("_ffmwiz_speed_text")
        if override:
            return override
        return state.get("speed", "N/A") or "N/A"

    elapsed = max(0.0, time.perf_counter() - started_at)
    # Refresh the ETA only when the media position actually advances (a real
    # FFmpeg progress tick). Between ticks the loop re-renders every ~0.25s to
    # keep the line live; recomputing the ETA from the ever-growing wall-clock
    # 'elapsed' on those heartbeats made it drift/refresh faster than the
    # percent/time/size fields. Caching it against current_s keeps every field
    # updating in lock-step.
    eta_anchor = f"{current_s:.3f}"
    if state.get("_ffmwiz_eta_anchor") == eta_anchor and "_ffmwiz_eta_seconds" in state:
        cached_eta = state.get("_ffmwiz_eta_seconds")
        eta_s = None if cached_eta in (None, "", "none") else float(cached_eta)
    else:
        if total_duration and total_duration > 0 and current_s >= total_duration * 0.995:
            eta_s = 0.0
        elif total_duration and current_s > 0.5 and elapsed > 0.5:
            # Use a smoothed rate (the overall average) instead of FFmpeg's jumpy
            # per-tick speed, so the ETA is steady and reliable.
            speed_ratio = _smoothed_eta_rate(state, current_s, elapsed) or (current_s / elapsed)
            eta_s = max(0.0, (total_duration - current_s) / speed_ratio) if speed_ratio and speed_ratio > 0.01 else None
        else:
            eta_s = None
        state["_ffmwiz_eta_anchor"] = eta_anchor
        state["_ffmwiz_eta_seconds"] = "none" if eta_s is None else f"{eta_s:.3f}"

    colorize = USE_COLOR
    if total_duration and total_duration > 0:
        pct = min(100.0, 100.0 * current_s / total_duration)
        pct_text = f"{pct:.1f}%"
    else:
        pct_text = "progress"

    current_text = format_progress_clock(current_s)
    total_text = format_progress_clock(total_duration) if total_duration and total_duration > 0 else "unknown"
    elapsed_text = format_progress_elapsed_dot(elapsed)
    eta_text = format_progress_duration(eta_s) if eta_s is not None else "calculating"
    time_total_text = (
        f"{_progress_colorize(f'time {current_text}', PROGRESS_COLORS['time'], colorize)} "
        f"{_progress_colorize(f'/ {total_text}', PROGRESS_COLORS['total'], colorize)}"
    )
    eta_segment = (
        f"{_progress_colorize('ETA', PROGRESS_COLORS['eta_label'], colorize)} "
        f"{_progress_colorize(eta_text, PROGRESS_COLORS['eta_value'], colorize)}"
    )

    # Each segment carries a DROP RANK. The line is clamped to one terminal row,
    # and the old code did that by chopping the tail with "..." -- which ate the
    # ETA first, because ETA is last. Now the optional fields are dropped by
    # rank (highest first) until the line fits, so percent, time/total, elapsed
    # and ETA (rank 0) always survive on a narrow terminal.
    KEEP = 0
    verbose_segments: list[tuple[str, str, int]] = [
        (pct_text, PROGRESS_COLORS["percent"], KEEP),
    ]
    # Show active split part indicator if available.
    part_label = state.get("_ffmwiz_split_part_label")
    if part_label:
        verbose_segments.append((part_label, Color.LIGHT_BLUE, KEEP))
    verbose_segments.append((time_total_text, "", KEEP))
    fps_text = fps_value()
    if has_real_value(fps_text):
        verbose_segments.append((f"fps {fps_text}", PROGRESS_COLORS["fps"], 5))
    q_text = q_value()
    if has_real_value(q_text):
        verbose_segments.append((f"q {q_text}", PROGRESS_COLORS["q"], 6))
    speed_text = speed_value()
    if has_real_value(speed_text):
        verbose_segments.append((f"speed {speed_text}", PROGRESS_COLORS["speed"], 2))
    size_text = output_size()
    if has_real_value(size_text):
        verbose_segments.append((f"size {size_text}", PROGRESS_COLORS["size"], 3))
    bitrate_text = bitrate_value()
    if has_real_value(bitrate_text):
        verbose_segments.append((f"bitrate {bitrate_text}", PROGRESS_COLORS["bitrate"], 4))
    verbose_segments.extend([
        # Elapsed is the last optional to go: on a very narrow terminal the
        # remaining time matters more than the time already spent.
        (f"elapsed {elapsed_text}", PROGRESS_COLORS["elapsed"], 1),
        (eta_segment, "", KEEP),
    ])

    def _render(segments: list[tuple[str, str, int]]) -> str:
        return _join_progress_segments([(text, color) for text, color, _ in segments], colorize)

    rendered = _render(verbose_segments)
    if max_width is None:
        # No budget requested: return the complete line. Only the display path
        # (runtime._write_progress_line) knows the real terminal width, so only
        # it asks for a budget.
        return rendered
    while _visible_len(rendered) > max_width:
        droppable = [rank for _, _, rank in verbose_segments if rank != KEEP]
        if not droppable:
            break
        worst = max(droppable)
        verbose_segments = [seg for seg in verbose_segments if seg[2] != worst]
        rendered = _render(verbose_segments)
    return rendered


__all__ = [
    'PROGRESS_COLORS',
    '_progress_terminal_width',
    '_join_progress_segments',
    '_render_progress_line',
]
