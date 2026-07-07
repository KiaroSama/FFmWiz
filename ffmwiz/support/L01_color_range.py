"""FFmWiz helpers (dependency level 1) — concerns: color_range(4).

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


def folder_items_with_unknown_color_range(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the folder items whose source video color range is unknown."""
    unknown: list[dict[str, Any]] = []
    for item in answers.get("_folder_items") or []:
        streams = (item.get("answers") or {}).get("video_streams") or []
        if not streams:
            continue
        if normalize_color_range(streams[0].get("color_range")) not in {"tv", "pc"}:
            unknown.append(item)
    return unknown


def source_color_range_known(answers: dict[str, Any]) -> bool:
    """True when the source video stream reports a valid, known color range."""
    stream = source_video_stream(answers) or {}
    return normalize_color_range(stream.get("color_range")) in {"tv", "pc"}


def _color_range_unresolved_message(
    answers: dict[str, Any], detected: str, workflow: str | None
) -> str:
    """Build a diagnostic message for an unresolved color-range decision.
    Identifies the source path, selected video stream, detected range, the
    workflow/builder that requested resolution, and the missing choice."""
    stream = source_video_stream(answers) or {}
    source_path = str(answers.get("input_path") or "unknown")
    stream_index = stream.get("index", "?")
    codec = stream.get("codec_name", "unknown")
    pix_fmt = stream.get("pix_fmt", "unknown")
    return (
        "Color range could not be resolved before FFmpeg execution. "
        f"workflow/builder={workflow or 'unknown'}; "
        f"source path={source_path}; "
        f"selected video stream=index {stream_index} (codec={codec}, pix_fmt={pix_fmt}); "
        f"detected range={detected or 'unknown'}; "
        "missing resolved choice=color_range_choice (expected one of tv/pc/unspecified "
        "from the wizard, batch policy, or per-file policy). "
        "Compatibility fallback is opt-in only for explicitly identified legacy/direct "
        "API callers."
    )


def build_copy_cut_range_command(
    ffmpeg: str,
    overwrite: str,
    input_path: Path,
    start: float,
    end: float,
    output_path: Path,
    chapter_plan: dict[str, Any] | None = None,
) -> list[str]:
    duration = max(0.0, float(end) - float(start))
    cmd = [
        ffmpeg, overwrite, "-hide_banner",
        "-ss", seconds_to_ffmpeg_time(start),
        "-i", str(input_path),
    ]
    if chapter_plan and chapter_plan.get("mode") == "metadata":
        cmd.extend(["-i", str(chapter_plan["metadata_path"])])
    cmd.extend(["-t", seconds_to_ffmpeg_time(duration)])
    cmd.extend(["-map", "0", "-map_metadata", "0"])
    cmd.extend(copy_cut_chapter_map_args(chapter_plan, 1))
    cmd.extend(["-c", "copy", "-avoid_negative_ts", "make_zero", str(output_path)])
    return cmd


__all__ = [
    'folder_items_with_unknown_color_range',
    'source_color_range_known',
    '_color_range_unresolved_message',
    'build_copy_cut_range_command',
]
