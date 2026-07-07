"""FFmWiz helpers (dependency level 0) — concerns: filters(10).

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


def join_audio_prep_filter(rate: int) -> str:
    """Per-input audio prep for Join graphs at the given uniform sample rate."""
    return f"aresample={int(rate)}:async=1:first_pts=0,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS"


def _apply_dark_title_bar(window: Any) -> None:
    """Switch the native window frame to dark mode on Windows 10/11.

    No-op on other platforms. Safe to call multiple times. Failures are
    swallowed so unsupported Windows versions or non-DWM environments do
    not break the GUI.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        enabled = ctypes.c_int(1)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE on Windows 11 / late 10.
        # 19 = legacy attribute for early Windows 10 builds.
        for attribute in (20, 19):
            attr_result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                attribute,
                ctypes.byref(enabled),
                ctypes.sizeof(enabled),
            )
            if attr_result == 0:
                break
    except Exception:
        return


def format_crop_margins(answers: dict[str, Any]) -> str:
    return (
        f"top={answers.get('crop_top', 0)} px, "
        f"left={answers.get('crop_left', 0)} px, "
        f"right={answers.get('crop_right', 0)} px, "
        f"bottom={answers.get('crop_bottom', 0)} px"
    )


def format_resolution_summary(value: Any) -> str:
    if value is None or value == "n":
        return "source"
    if isinstance(value, dict):
        mode = value.get("mode")
        if mode == "preset":
            return f"{value.get('label')} closest-edge preset"
        if mode == "box":
            return f"{value.get('width')}x{value.get('height')} preserve-aspect box"
        if mode == "height":
            return f"{value.get('height')}p target height"
        if mode == "width":
            return f"{value.get('width')}w target width"
        if mode == "exact_stretch":
            return f"{value.get('width')}x{value.get('height')} exact stretch"
    if isinstance(value, tuple) and len(value) == 2:
        return f"{value[0]}x{value[1]}"
    return str(value)


def parse_sar_value(sar_str: str | None) -> float:
    """Parse a sample aspect ratio string (e.g. '4:3', '16/9', '1.333') to a float.
    Returns 1.0 for unknown, empty, or invalid values."""
    if not sar_str or str(sar_str).strip().lower() in {"", "n/a", "unknown", "0:0", "0/0", "0:1"}:
        return 1.0
    text = str(sar_str).strip()
    # Handle ratio notation: "N:M" or "N/M"
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)", text)
    if match:
        num = float(match.group(1))
        den = float(match.group(2))
        if den > 0:
            return num / den
        return 1.0
    # Handle plain float
    try:
        val = float(text)
        return val if val > 0 else 1.0
    except (TypeError, ValueError):
        return 1.0


def _normalize_crop_axis(
    requested_near: int,
    requested_far: int,
    source_dim: int,
    origin_align: int,
    output_align: int,
) -> tuple[int, int] | None:
    """Find the closest valid (near, far) crop pair for one axis.

    near = left/top, far = right/bottom. A valid pair satisfies:
      near >= 0, far >= 0, near + far < source_dim
      near % origin_align == 0                         (chroma-aligned origin)
      (source_dim - near - far) % output_align == 0    (encodable output size)
      source_dim - near - far >= output_align          (positive output size)

    Among valid pairs the closest one is chosen by this deterministic priority
    (each minimized in order):
      1. total absolute adjustment from the requested values
      2. preserve the requested total crop amount (keep the output size)
      3. smallest crop-box center shift
      4. remove less image content (smaller total crop)
      5. prefer decreasing the origin-side crop
    Returns None when no valid pair exists for this axis.
    """
    origin_align = max(1, int(origin_align))
    output_align = max(1, int(output_align))
    requested_near = max(0, int(requested_near))
    requested_far = max(0, int(requested_far))

    best_key: tuple[int, int, int, int, int] | None = None
    best_pair: tuple[int, int] | None = None
    window = max(8, 4 * max(origin_align, output_align) + 2)
    limit = source_dim + max(origin_align, output_align)
    while best_pair is None and window <= limit:
        near_hi = requested_near + window
        far_hi = requested_far + window
        for near in range(max(0, requested_near - window), near_hi + 1):
            if near % origin_align != 0:
                continue
            for far in range(max(0, requested_far - window), far_hi + 1):
                cropped = source_dim - near - far
                if cropped < output_align or cropped % output_align != 0:
                    continue
                key = (
                    abs(near - requested_near) + abs(far - requested_far),
                    abs((near + far) - (requested_near + requested_far)),
                    abs((near - far) - (requested_near - requested_far)),
                    near + far,
                    near,
                )
                if best_key is None or key < best_key:
                    best_key = key
                    best_pair = (near, far)
        window *= 2
    return best_pair


def parse_resolution(value: str) -> dict[str, Any] | str:
    lowered = value.lower().strip()
    if lowered == "n":
        return "n"
    if lowered in RESOLUTION_PRESETS:
        width, height = RESOLUTION_PRESETS[lowered]
        return {"mode": "preset", "label": lowered, "width": width, "height": height}
    if re.fullmatch(r"\d{3,4}", lowered) and f"{lowered}p" in RESOLUTION_PRESETS:
        label = f"{lowered}p"
        width, height = RESOLUTION_PRESETS[label]
        return {"mode": "preset", "label": label, "width": width, "height": height}
    width_match = re.fullmatch(r"(?:w|width=)(\d{2,5})", lowered) or re.fullmatch(r"(\d{2,5})w", lowered)
    if width_match:
        width = int(width_match.group(1))
        if width == 0:
            raise Back()
        return {"mode": "width", "width": width, "label": f"{width}w"}
    height_match = re.fullmatch(r"(?:h|height=)(\d{2,5})", lowered) or re.fullmatch(r"(\d{2,5})h", lowered)
    if height_match:
        height = int(height_match.group(1))
        if height == 0:
            raise Back()
        return {"mode": "height", "height": height, "label": f"{height}h"}
    stretch_match = re.fullmatch(r"(?:stretch|exact):\s*(\d{2,5})\s*[xX]\s*(\d{2,5})", lowered)
    if stretch_match:
        width = int(stretch_match.group(1))
        height = int(stretch_match.group(2))
        if width == 0 or height == 0:
            raise Back()
        return {"mode": "exact_stretch", "width": width, "height": height, "label": f"stretch:{width}x{height}"}
    match = re.fullmatch(r"(\d{2,5})\s*[xX]\s*(\d{2,5})", lowered)
    if match:
        width = int(match.group(1))
        height = int(match.group(2))
        if width == 0 or height == 0:
            raise Back()
        return {"mode": "box", "width": width, "height": height, "label": f"{width}x{height}"}
    raise ValueError("Invalid resolution. Example: 480p, 480, w720, h480, 1280x720, or stretch:1280x720")


def has_crop(answers: dict[str, Any]) -> bool:
    return bool(
        answers.get("crop_enabled")
        and any(int(answers.get(key, 0) or 0) for key in ("crop_top", "crop_bottom", "crop_left", "crop_right"))
    )


def metadata_filter_path(path: Path) -> str:
    text = str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    return "'" + text + "'"


def hardsub_filter_quote_path(path: Path) -> str:
    text = path.resolve().as_posix()
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    text = text.replace(",", "\\,")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    return "'" + text + "'"


__all__ = [
    'join_audio_prep_filter',
    '_apply_dark_title_bar',
    'format_crop_margins',
    'format_resolution_summary',
    'parse_sar_value',
    '_normalize_crop_axis',
    'parse_resolution',
    'has_crop',
    'metadata_filter_path',
    'hardsub_filter_quote_path',
]
