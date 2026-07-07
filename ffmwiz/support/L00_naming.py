"""FFmWiz helpers (dependency level 0) — concerns: naming(17).

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


def container_family(ext: Any) -> str:
    """Normalize an output extension to a container family for capability keys."""
    e = str(ext or "").strip().lower().lstrip(".")
    if e in MP4_LIKE_EXTS:
        return "mp4"
    return e or "unknown"


def _format_ratio(value: float) -> str:
    """Format an aspect-ratio float as a compact 'W:H' when it matches a common
    ratio, otherwise as a 3-decimal number."""
    if value <= 0:
        return "unknown"
    common = {
        16 / 9: "16:9", 4 / 3: "4:3", 21 / 9: "21:9", 1.0: "1:1",
        3 / 2: "3:2", 5 / 4: "5:4", 9 / 16: "9:16", 2.39: "239:100",
    }
    for ratio, label in common.items():
        if abs(value - ratio) < 0.005:
            return label
    return f"{value:.3f}"


def _text_preview(text: str, limit: int = 4000) -> str:
    if not text:
        return "(empty)"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... truncated, total {len(text)} characters ..."


def _format_hms_ms(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm."""
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    size = float(value)
    if size < 1024:
        return f"{int(size)} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024 * 1024):.2f} TB"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def mux_format_index_list(indexes: list[int]) -> str:
    return ", ".join(str(index) for index in indexes) if indexes else "none"


def mux_text_matches_any(value: str, needles: list[str]) -> bool:
    haystack = (value or "").lower()
    return any(needle in haystack for needle in needles)


def mux_format_size_difference(size_bytes: int) -> str:
    sign = "+" if size_bytes > 0 else "-" if size_bytes < 0 else ""
    absolute = abs(int(size_bytes))
    kb = absolute / 1024
    mb = kb / 1024
    gb = mb / 1024
    if mb < 5:
        return f"{sign}{kb:.2f} KB"
    if gb >= 1:
        return f"{sign}{gb:.2f} GB"
    return f"{sign}{mb:.2f} MB"


def folder_item_display_name(item: dict[str, Any]) -> str:
    return str(item.get("relative_path") or item["path"].name)


def normalize_format(value: str, input_ext: str) -> str:
    lowered = value.lower().strip().lstrip(".")
    if lowered == "n":
        return input_ext.lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_+-]*", lowered):
        raise ValueError("Format must be like mp4, mkv, or mp3.")
    return lowered


def source_video_codec_name(answers: dict[str, Any]) -> str:
    streams = answers.get("video_streams") or []
    if not streams:
        return ""
    return str(streams[0].get("codec_name", "") or "").strip().lower()


def format_cut_ranges_for_summary(
    ranges: list[tuple[float, float]],
    fps: float,
    label: str,
) -> str:
    if not ranges:
        return f"{label}: (none)"
    lines = [f"{label}:"]
    for idx, (start, end) in enumerate(ranges, start=1):
        lines.append(
            f"  {idx}. {seconds_to_hmsf(start, fps)} -> {seconds_to_hmsf(end, fps)}  "
            f"({seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)})"
        )
    return "\n".join(lines)


def format_split_points_for_summary(points: list[float], fps: float, label: str = "Split points") -> str:
    pts = sorted(float(p) for p in (points or []))
    if not pts:
        return f"{label}: (none)"
    lines = [f"{label}: {len(pts)} (output split into {len(pts) + 1} parts):"]
    for idx, t in enumerate(pts, start=1):
        lines.append(f"  {idx}. {seconds_to_hmsf(t, fps)}  ({seconds_to_ffmpeg_time(t)})")
    return "\n".join(lines)


def add_files_supported_subtitle_codecs_for_container(ext: str) -> set[str] | None:
    normalized = ext.lower().lstrip(".")
    if normalized in {"mkv", "mk3d", "mka"}:
        return None
    if normalized in MP4_LIKE_EXTS:
        return {"mov_text", "tx3g"}
    if normalized == "webm":
        return {"webvtt"}
    if normalized in {"ts", "m2ts", "mts"}:
        return {"dvb_subtitle", "dvbsub", "hdmv_pgs_subtitle", "pgs"}
    return set()


def hardsub_external_subtitle_extension_supported(path: Path) -> bool:
    return path.suffix.lower() in HARDSUB_SUBTITLE_EXTS


def hardsub_input_output_exts(answers: dict[str, Any]) -> tuple[str, str]:
    input_path: Path = answers["input_path"]
    input_ext = input_path.suffix.lstrip(".").lower() or "mkv"
    output_ext = str(answers.get("output_ext") or input_ext).lower().lstrip(".")
    return input_ext, output_ext


__all__ = [
    'container_family',
    '_format_ratio',
    '_text_preview',
    '_format_hms_ms',
    'format_bytes',
    'format_duration',
    'mux_format_index_list',
    'mux_text_matches_any',
    'mux_format_size_difference',
    'folder_item_display_name',
    'normalize_format',
    'source_video_codec_name',
    'format_cut_ranges_for_summary',
    'format_split_points_for_summary',
    'add_files_supported_subtitle_codecs_for_container',
    'hardsub_external_subtitle_extension_supported',
    'hardsub_input_output_exts',
]
