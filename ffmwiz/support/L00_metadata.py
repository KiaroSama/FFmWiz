"""FFmWiz helpers (dependency level 0) — concerns: metadata(26).

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


def chapter_presence(payload_or_answers: dict[str, Any] | None) -> tuple[str, str]:
    data = payload_or_answers or {}
    chapters = data.get("chapters")
    if chapters is None and isinstance(data.get("probe"), dict):
        chapters = data["probe"].get("chapters")
    has_chapters = bool(chapters)
    return ("yes" if has_chapters else "no", Color.CHAPTERS_YES if has_chapters else Color.CHAPTERS_NO)


def source_metadata_keep_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("keep_source_metadata", True))


def source_chapters_keep_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("keep_source_chapters", True))


def source_chapter_streams(answers: dict[str, Any]) -> list[dict[str, Any]]:
    probe = answers.get("probe") if isinstance(answers.get("probe"), dict) else {}
    chapters = probe.get("chapters") if isinstance(probe, dict) else []
    return [chapter for chapter in (chapters or []) if isinstance(chapter, dict)]


def source_metadata_tags_present(answers: dict[str, Any]) -> bool:
    fmt = answers.get("format") if isinstance(answers.get("format"), dict) else {}
    if isinstance(fmt, dict) and fmt.get("tags"):
        return True
    streams: list[dict[str, Any]] = []
    for key in ("video_streams", "audio_streams", "subtitle_streams", "attachment_streams", "data_streams"):
        streams.extend(stream for stream in (answers.get(key) or []) if isinstance(stream, dict))
    return any(bool(stream.get("tags")) for stream in streams)


def format_size_bytes_from_metadata(fmt: dict[str, Any] | None) -> int | None:
    if not fmt:
        return None
    value = fmt.get("size")
    if not value:
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def int_metadata_value(stream: dict[str, Any], key: str) -> int | None:
    value = stream.get(key)
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def stream_metadata_value(stream: dict[str, Any], key: str, default: str = "unknown") -> str:
    value = stream.get(key)
    if value is None or value == "":
        return default
    return str(value)


def media_info_disposition(stream: dict[str, Any], name: str) -> str:
    disposition = stream.get("disposition")
    if not isinstance(disposition, dict):
        return "unknown"
    value = disposition.get(name)
    if value in {1, "1", True}:
        return "yes"
    if value in {0, "0", False}:
        return "no"
    return "unknown"


def copy_media_metadata(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in FOLDER_MEDIA_METADATA_KEYS:
        if key in source:
            target[key] = source[key]
        else:
            target.pop(key, None)
    target.pop("audio_duplicate_report", None)
    target.pop("final_resolution", None)
    target.pop("crop_box_dimensions", None)
    target.pop("cropped_aspect_ratio", None)


def metadata_stream_index(stream: dict[str, Any]) -> int | None:
    try:
        return int(stream.get("index"))
    except (TypeError, ValueError):
        return None


def metadata_stream_type(stream: dict[str, Any]) -> str:
    return str(stream.get("codec_type") or "unknown").lower()


def metadata_tags(stream: dict[str, Any]) -> dict[str, Any]:
    return stream.get("tags") if isinstance(stream.get("tags"), dict) else {}


def metadata_disposition_summary(stream: dict[str, Any]) -> str:
    disposition = stream.get("disposition") if isinstance(stream.get("disposition"), dict) else {}
    flags = [name for name in METADATA_DISPOSITION_FLAGS if int(disposition.get(name) or 0)]
    return ",".join(flags) if flags else "-"


def metadata_type_color(codec_type: str) -> str:
    return {
        "video": Color.MAGENTA,
        "audio": Color.BLUE,
        "subtitle": Color.LIGHT_YELLOW,
        "attachment": Color.MUX_LAVENDER,
        "data": Color.GRAY,
    }.get(codec_type, Color.WHITE)


def metadata_stream_copy_command(ffmpeg: str, input_path: Path, output_path: Path) -> list[str]:
    return [ffmpeg, "-hide_banner", "-y", "-i", str(input_path), "-map", "0", "-c", "copy"]


def metadata_chapter_lines(probe: dict[str, Any]) -> list[str]:
    chapters = probe.get("chapters") or []
    if not chapters:
        return ["No chapters were found."]
    lines = []
    for idx, chapter in enumerate(chapters, 1):
        tags = chapter.get("tags") if isinstance(chapter.get("tags"), dict) else {}
        title = tags.get("title") or "untitled"
        start = float(chapter.get("start_time") or 0.0)
        end = float(chapter.get("end_time") or 0.0)
        lines.append(f"{idx}. {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)} | {title}")
    return lines


def metadata_bsf_name(codec_name: str) -> str | None:
    codec = str(codec_name or "").lower()
    if codec in {"h264", "avc1"}:
        return "h264_metadata"
    if codec in {"hevc", "h265"}:
        return "hevc_metadata"
    return None


def metadata_json_report_command(ffprobe: str, input_path: Path, report_type: str) -> list[str]:
    if report_type == "tags":
        return [ffprobe, "-hide_banner", "-v", "error", "-show_entries", "format_tags:stream_tags:chapters", "-of", "json", str(input_path)]
    if report_type == "color":
        return [ffprobe, "-hide_banner", "-v", "error", "-select_streams", "v", "-show_entries", "stream=index,codec_name,pix_fmt,bits_per_raw_sample,color_range,color_space,color_transfer,color_primaries,width,height", "-of", "json", str(input_path)]
    if report_type == "disposition":
        return [ffprobe, "-hide_banner", "-v", "error", "-show_entries", "stream=index,codec_type,codec_name:stream_disposition:stream_tags", "-of", "json", str(input_path)]
    if report_type == "chapters":
        return [ffprobe, "-hide_banner", "-v", "error", "-show_chapters", "-of", "json", str(input_path)]
    return [ffprobe, "-hide_banner", "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json", str(input_path)]


def copy_cut_chapter_seconds(chapter: dict[str, Any], prefix: str) -> float | None:
    time_value = chapter.get(f"{prefix}_time")
    if time_value not in (None, ""):
        try:
            return float(time_value)
        except (TypeError, ValueError):
            pass
    raw_value = chapter.get(prefix)
    if raw_value in (None, ""):
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    time_base = str(chapter.get("time_base") or "").strip()
    match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", time_base)
    if not match:
        return value
    numerator = int(match.group(1))
    denominator = int(match.group(2))
    if denominator <= 0:
        return value
    return value * numerator / denominator


def copy_cut_chapter_overlaps_removed(
    chapter_start: float,
    chapter_end: float,
    removed_ranges: list[tuple[float, float]],
) -> bool:
    return any(chapter_start < removed_end and chapter_end > removed_start for removed_start, removed_end in removed_ranges)


def copy_cut_remap_chapter(
    chapter_start: float,
    chapter_end: float,
    keep_ranges: list[tuple[float, float]],
) -> tuple[float, float] | None:
    output_offset = 0.0
    for keep_start, keep_end in keep_ranges:
        if chapter_start >= keep_start - 1e-6 and chapter_end <= keep_end + 1e-6:
            new_start = output_offset + max(0.0, chapter_start - keep_start)
            new_end = output_offset + max(0.0, chapter_end - keep_start)
            if new_end > new_start:
                return new_start, new_end
            return None
        output_offset += max(0.0, keep_end - keep_start)
    return None


def ffmetadata_escape(value: Any) -> str:
    r"""Escape one FFmetadata key or value.

    FFmetadata escapes a special character by prefixing it with a backslash
    -- and a physical newline is one of them, so a two-line chapter title is
    backslash + LF, NOT the C escape "\n". Emitting the C form made a real
    FFmpeg round trip read First<LF>Second back as the literal text
    "FirstnSecond", and CRLF as "FirstrnSecond": the newline vanished and
    its letter stayed. CR is normalized to LF first, because a bare CR
    cannot be carried through this format without the reader ending the
    line early; that keeps the Windows CRLF case lossless in the only way
    the format allows.
    """
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\", "\\\\")
    for char in ("=", ";", "#", "\n"):
        text = text.replace(char, "\\" + char)
    return text


def copy_cut_chapter_map_args(plan: dict[str, Any] | None, metadata_input_index: int = 1) -> list[str]:
    mode = (plan or {}).get("mode", "copy")
    if mode == "metadata":
        return ["-map_chapters", str(metadata_input_index)]
    if mode == "drop":
        return ["-map_chapters", "-1"]
    return ["-map_chapters", "0"]


def parse_add_track_metadata(value: str) -> dict[str, str]:
    if not value or value.lower().strip() in {"n", "keep"}:
        return {}
    language, separator, title = value.partition(",")
    metadata: dict[str, str] = {}
    if language.strip():
        metadata["language"] = language.strip()
    if separator and title.strip():
        metadata["title"] = title.strip()
    return metadata


def format_track_metadata(metadata: dict[str, str]) -> str:
    if not metadata:
        return "keep existing metadata"
    parts = []
    if metadata.get("language"):
        parts.append(f"language={metadata['language']}")
    if metadata.get("title"):
        parts.append(f"title={metadata['title']}")
    return ", ".join(parts)


__all__ = [
    'chapter_presence',
    'source_metadata_keep_enabled',
    'source_chapters_keep_enabled',
    'source_chapter_streams',
    'source_metadata_tags_present',
    'format_size_bytes_from_metadata',
    'int_metadata_value',
    'stream_metadata_value',
    'media_info_disposition',
    'copy_media_metadata',
    'metadata_stream_index',
    'metadata_stream_type',
    'metadata_tags',
    'metadata_disposition_summary',
    'metadata_type_color',
    'metadata_stream_copy_command',
    'metadata_chapter_lines',
    'metadata_bsf_name',
    'metadata_json_report_command',
    'copy_cut_chapter_seconds',
    'copy_cut_chapter_overlaps_removed',
    'copy_cut_remap_chapter',
    'ffmetadata_escape',
    'copy_cut_chapter_map_args',
    'parse_add_track_metadata',
    'format_track_metadata',
]
