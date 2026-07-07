"""FFmWiz helpers (dependency level 0) — concerns: streams(19).

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


def streams_for_statistics_from_answers(answers: dict[str, Any]) -> list[dict[str, Any]]:
    probe = answers.get("probe") if isinstance(answers.get("probe"), dict) else {}
    probe_streams = probe.get("streams") if isinstance(probe, dict) else None
    if isinstance(probe_streams, list) and probe_streams:
        return [stream for stream in probe_streams if isinstance(stream, dict)]
    streams: list[dict[str, Any]] = []
    for key in ("video_streams", "audio_streams", "subtitle_streams", "attachment_streams", "data_streams"):
        streams.extend(stream for stream in (answers.get(key) or []) if isinstance(stream, dict))
    return streams


def source_video_stream(answers: dict[str, Any]) -> dict[str, Any] | None:
    streams = answers.get("video_streams") or []
    return streams[0] if streams else None


def additional_source_video_streams(answers: dict[str, Any]) -> list[dict[str, Any]]:
    streams = answers.get("video_streams") or []
    return list(streams[1:]) if len(streams) > 1 else []


def embedded_attachment_streams(answers: dict[str, Any]) -> list[dict[str, Any]]:
    return list(answers.get("attachment_streams") or [])


def source_data_streams(answers: dict[str, Any]) -> list[dict[str, Any]]:
    return list(answers.get("data_streams") or [])


def _all_stream_indexes_selected(selected: Any, count: int) -> bool:
    if count <= 0:
        return True
    if selected == "all":
        return True
    if isinstance(selected, list):
        try:
            return sorted(int(item) for item in selected) == list(range(count))
        except (TypeError, ValueError):
            return False
    return False


def _explicit_all_streams_selected(selected: Any, count: int) -> bool:
    if count <= 0:
        return True
    return selected == "all"


def stream_statistics_siblings(stream: dict[str, Any], sibling_streams: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    streams = [item for item in (sibling_streams or []) if isinstance(item, dict)]
    stream_index = stream.get("index")
    if stream_index is not None and all(item.get("index") != stream_index for item in streams):
        streams.append(stream)
    elif stream_index is None and not streams:
        streams.append(stream)
    return streams


def stream_tag_value(stream: dict[str, Any], key: str, default: str = "unknown") -> str:
    value = stream.get("tags", {}).get(key)
    if value is None or value == "":
        return default
    return str(value)


def media_info_stream_name(stream: dict[str, Any]) -> str:
    index = stream.get("index", "?")
    codec_type = stream.get("codec_type", "unknown")
    codec = stream.get("codec_name", "unknown")
    return f"stream #{index} {codec_type} {codec}"


def media_info_stream_tags(stream: dict[str, Any]) -> dict[str, Any]:
    tags = stream.get("tags")
    return tags if isinstance(tags, dict) else {}


def write_media_info_stream_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "stream_index", "type", "codec", "language", "title", "duration",
        "bitrate", "size_bytes", "size_mb", "percent_of_file", "estimated",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def media_info_main_video_stream(payload: dict[str, Any]) -> dict[str, Any] | None:
    for stream in payload.get("streams") or []:
        if stream.get("codec_type") == "video":
            return stream
    return None


def validate_stream_selection_for_folder_job(
    selected: list[int] | str,
    count: int,
    stream_kind: str,
) -> None:
    if selected == "all":
        return
    bad = [index for index in selected if index < 0 or index >= count]
    if bad:
        raise ValueError(
            f"Selected {stream_kind} stream(s) {bad} do not exist in this file. "
            f"Available range: 0 to {max(0, count - 1)}."
        )


def selected_subtitle_streams(answers: dict[str, Any]) -> list[int]:
    selected = answers.get("subtitle_tracks", [])
    count = len(answers.get("subtitle_streams", []))
    if selected == "all":
        return list(range(count))
    return selected


def stream_global_index(stream: dict[str, Any]) -> int | None:
    try:
        return int(stream.get("index"))
    except (TypeError, ValueError):
        return None


def extract_stream_default_extension(stream: dict[str, Any]) -> str:
    codec_type = str(stream.get("codec_type") or "").lower()
    codec = str(stream.get("codec_name") or "").lower()
    if codec_type == "audio":
        return EXTRACT_AUDIO_EXTENSIONS.get(codec, ".mka")
    if codec_type == "subtitle":
        return EXTRACT_SUBTITLE_EXTENSIONS.get(codec, ".srt")
    if codec_type == "video":
        return ".mkv"
    return ".bin"


def extract_stream_codec_args(stream: dict[str, Any]) -> tuple[list[str], str]:
    codec_type = str(stream.get("codec_type") or "").lower()
    codec = str(stream.get("codec_name") or "").lower()
    if codec_type == "subtitle" and codec in {"mov_text", "text"}:
        return ["-c:s", "srt"], "text subtitle converted to SRT for extraction"
    return ["-c", "copy"], "stream copy"


def parse_stream_index_spec(value: str) -> list[int]:
    """Parse a stream selection into a sorted, de-duplicated list of ffprobe
    stream indexes. Supports a single index (4), a list (1,2,3), a range (1-5),
    and any mix (1-5,6,8-10)."""
    text = str(value).strip()
    if not text:
        raise ValueError("Enter at least one stream index.")
    result: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            bits = [b.strip() for b in part.split("-")]
            if len(bits) != 2 or not bits[0].isdigit() or not bits[1].isdigit():
                raise ValueError(f"Invalid range: {part!r}. Use like 1-5.")
            lo, hi = int(bits[0]), int(bits[1])
            if hi < lo:
                lo, hi = hi, lo
            result.update(range(lo, hi + 1))
        elif part.isdigit():
            result.add(int(part))
        else:
            raise ValueError(
                f"Invalid stream index: {part!r}. Use numbers, commas, and ranges (e.g. 1-5,6,8-10)."
            )
    if not result:
        raise ValueError("Enter at least one stream index.")
    return sorted(result)


__all__ = [
    'streams_for_statistics_from_answers',
    'source_video_stream',
    'additional_source_video_streams',
    'embedded_attachment_streams',
    'source_data_streams',
    '_all_stream_indexes_selected',
    '_explicit_all_streams_selected',
    'stream_statistics_siblings',
    'stream_tag_value',
    'media_info_stream_name',
    'media_info_stream_tags',
    'write_media_info_stream_summary_csv',
    'media_info_main_video_stream',
    'validate_stream_selection_for_folder_job',
    'selected_subtitle_streams',
    'stream_global_index',
    'extract_stream_default_extension',
    'extract_stream_codec_args',
    'parse_stream_index_spec',
]
