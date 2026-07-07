"""FFmWiz helpers (dependency level 1) — concerns: filters(8).

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


def atempo_filter_chain(speed: float) -> str:
    """Build an atempo chain with each stage kept in FFmpeg's safe 0.5..2.0
    range. This avoids the artifacts/skipped-sample behavior of very large
    single atempo values."""
    remaining = clamp_speed_factor(speed)
    stages: list[float] = []
    while remaining > 2.0 + 1e-9:
        stages.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        stages.append(0.5)
        remaining /= 0.5
    stages.append(remaining)
    return ",".join(f"atempo={ffmpeg_float(stage)}" for stage in stages)


def build_video_speed_filter(speed: float, reverse: bool) -> str:
    speed = clamp_speed_factor(speed)
    filters: list[str] = []
    if reverse:
        filters.append("reverse")
    filters.append(f"setpts=(PTS-STARTPTS)/{ffmpeg_float(speed)}")
    return ",".join(filters)


def loudnorm_analysis_filter(target_i: float) -> str:
    """Pass-1 measurement filter: same target, JSON output, no media encode."""
    return (
        "loudnorm="
        f"I={loudnorm_number(target_i)}:"
        f"TP={loudnorm_number(LOUDNORM_TARGET_TP)}:"
        f"LRA={loudnorm_number(LOUDNORM_TARGET_LRA)}:"
        "print_format=json"
    )


def source_sar(answers: dict[str, Any]) -> float:
    """Return the source video sample aspect ratio as a float (>0). Default 1.0."""
    stream = answers.get("video_streams", [{}])[0] if answers.get("video_streams") else {}
    sar_str = stream.get("sample_aspect_ratio")
    return parse_sar_value(sar_str)


def crop_margins_validation_message(
    answers: dict[str, Any],
    top: int,
    left: int,
    right: int,
    bottom: int,
) -> str | None:
    source_w, source_h = first_video_size(answers)
    if min(top, left, right, bottom) < 0:
        return "Invalid crop margins: crop values must be zero or positive."
    if left + right >= source_w:
        return (
            f"Invalid crop margins: left + right ({left + right} px) must be "
            f"smaller than source width ({source_w} px)."
        )
    if top + bottom >= source_h:
        return (
            f"Invalid crop margins: top + bottom ({top + bottom} px) must be "
            f"smaller than source height ({source_h} px)."
        )
    return None


def closest_edge_scale_dimensions(
    crop_w: int,
    crop_h: int,
    target_w: int,
    target_h: int,
) -> tuple[int, int, str]:
    source_landscape = crop_w >= crop_h
    target_landscape = target_w >= target_h
    if source_landscape != target_landscape:
        target_w, target_h = target_h, target_w
    width_distance = abs(crop_w - target_w)
    height_distance = abs(crop_h - target_h)
    if width_distance <= height_distance:
        final_width = even_dimension(target_w)
        final_height = even_dimension(final_width * crop_h / max(1, crop_w))
        return final_width, final_height, "width"
    final_height = even_dimension(target_h)
    final_width = even_dimension(final_height * crop_w / max(1, crop_h))
    return final_width, final_height, "height"


def video_filters_required(answers: dict[str, Any]) -> bool:
    return bool(
        answers.get("crop_enabled")
        or answers.get("fps") is not None
        or answers.get("resolution", "n") != "n"
        or answers.get("cut_keep_ranges")
        or answers.get("separator_points")
        or video_speed_transform_enabled(answers)
    )


def hardsub_subtitle_filter(answers: dict[str, Any]) -> str:
    source = answers.get("hardsub_subtitle_source")
    parts: list[str]
    if source == "internal":
        input_path: Path = answers["input_path"]
        subtitle_index = int(answers.get("hardsub_subtitle_index", 0))
        parts = [f"filename={hardsub_filter_quote_path(input_path)}", f"si={subtitle_index}"]
    else:
        subtitle_path: Path = answers["hardsub_subtitle_path"]
        parts = [f"filename={hardsub_filter_quote_path(subtitle_path)}"]
    fontsdir = answers.get("hardsub_fontsdir")
    if fontsdir:
        parts.append(f"fontsdir={hardsub_filter_quote_path(Path(fontsdir))}")
    return "subtitles=" + ":".join(parts)


__all__ = [
    'atempo_filter_chain',
    'build_video_speed_filter',
    'loudnorm_analysis_filter',
    'source_sar',
    'crop_margins_validation_message',
    'closest_edge_scale_dimensions',
    'video_filters_required',
    'hardsub_subtitle_filter',
]
