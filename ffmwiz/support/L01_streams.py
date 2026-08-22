"""FFmWiz helpers (dependency level 1) — concerns: streams(8).

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


def stream_tag_size_bytes(stream: dict[str, Any]) -> int | None:
    return tag_int(stream, ["NUMBER_OF_BYTES", "NUMBER_OF_BYTES-ENG"])


def stream_size_plausible(size_bytes: int | None, fmt: dict[str, Any] | None) -> bool:
    if size_bytes is None or size_bytes <= 0:
        return False
    total_size = format_size_bytes_from_metadata(fmt)
    if total_size and size_bytes > int(total_size * 1.02):
        return False
    return True


def stream_title(stream: dict[str, Any], relative_index: int) -> str:
    codec = stream.get("codec_name", "unknown")
    channels = stream.get("channels")
    lang = display_language(stream.get("tags", {}).get("language"))
    title = stream.get("tags", {}).get("title")
    global_index = stream.get("index", "?")
    parts = [f"{relative_index}: stream #{global_index}", f"codec={codec}"]
    if channels:
        parts.append(f"channels={channels}")
    if lang:
        parts.append(f"lang={lang}")
    if title:
        parts.append(f"title={title}")
    return " | ".join(parts)


def add_files_stream_copy_compatibility_errors(input_path: Path, extra_items: list[dict[str, Any]]) -> list[str]:
    ext = (input_path.suffix or ".mkv").lower().lstrip(".")
    audio_allowed = add_files_supported_audio_codecs_for_container(ext)
    subtitle_allowed = add_files_supported_subtitle_codecs_for_container(ext)
    errors: list[str] = []
    for item in extra_items:
        item_path = Path(item["path"]).name
        for stream in item.get("audio_streams") or []:
            codec = str(stream.get("codec_name") or "unknown").lower()
            if audio_allowed is not None and codec not in audio_allowed:
                errors.append(
                    f"{item_path}: audio codec {codec} cannot be safely stream-copied into .{ext}."
                )
        for stream in item.get("subtitle_streams") or []:
            codec = str(stream.get("codec_name") or "unknown").lower()
            if subtitle_allowed is not None and codec not in subtitle_allowed:
                errors.append(
                    f"{item_path}: subtitle codec {codec} cannot be safely stream-copied into .{ext}."
                )
    return errors


def extract_stream_container_options(stream: dict[str, Any]) -> tuple[list[str], str]:
    """Return (ordered copy-compatible container extensions, default) for the
    given stream. All options keep the stream with -c copy (no re-encode). The
    first option is the recommended default (m4a for common MP4-family audio)."""
    codec_type = str(stream.get("codec_type") or "").lower()
    codec = str(stream.get("codec_name") or "").lower()
    native = extract_stream_default_extension(stream).lstrip(".")
    if codec_type == "audio":
        base = EXTRACT_COPY_CONTAINERS_AUDIO.get(codec, [native])
        universal = "mka"
    elif codec_type == "video":
        base = EXTRACT_COPY_CONTAINERS_VIDEO.get(codec, ["mkv", "mp4"])
        universal = "mkv"
    elif codec_type == "subtitle":
        base = EXTRACT_COPY_CONTAINERS_SUBTITLE.get(codec, [native])
        universal = "mkv"
    else:
        base = [native or "bin"]
        universal = "mkv"
    options: list[str] = []
    for ext in [*base, universal]:
        ext = (ext or "").lstrip(".").lower()
        if ext and ext not in options:
            options.append(ext)
    return options, options[0]


def extract_stream_candidates(answers: dict[str, Any]) -> list[dict[str, Any]]:
    streams = answers.get("probe", {}).get("streams") or []
    candidates = [
        stream for stream in streams
        if stream.get("codec_type") in {"video", "audio", "subtitle"} and stream_global_index(stream) is not None
    ]
    return sorted(candidates, key=lambda stream: int(stream.get("index", 0)))


def build_extract_stream_command(ffmpeg: str, input_path: Path, stream: dict[str, Any], output_path: Path) -> list[str]:
    stream_index = stream_global_index(stream)
    if stream_index is None:
        raise ValueError("Selected stream has no ffprobe stream index.")
    codec_type = str(stream.get("codec_type") or "").lower()
    codec_args, _mode = extract_stream_codec_args(stream)
    cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(input_path), "-map", f"0:{stream_index}"]
    if codec_type == "video":
        cmd.extend(["-an", "-sn", "-dn"])
    elif codec_type == "audio":
        cmd.extend(["-vn", "-sn", "-dn"])
    elif codec_type == "subtitle":
        cmd.extend(["-vn", "-an", "-dn"])
    cmd.extend(codec_args)
    cmd.append(str(output_path))
    return cmd


def normalized_sar_text(value: Any) -> str:
    """Comparable SAR text. "", "0:1", "1:1" and None all mean square pixels, so
    they must normalise to the same string or files that simply omit the field
    would be treated as incompatible."""
    text = str(value or "").strip().lower()
    if text in {"", "0:1", "1:1", "n/a", "0/1", "1/1"}:
        return "1:1"
    return text.replace("/", ":")


def join_stream_signature(item: dict[str, Any]) -> list[tuple[Any, ...]]:
    signature: list[tuple[Any, ...]] = []
    for stream in item.get("streams") or []:
        codec_type = stream.get("codec_type")
        if codec_type not in {"video", "audio", "subtitle"}:
            continue
        if codec_type == "video":
            # SAR, profile, level and field order belong in the signature: the
            # concat demuxer labels the WHOLE output with input 0's values, so
            # two 320x240 clips with SAR 1:1 and SAR 2:1 were declared
            # copy-compatible and the second one played squashed for its entire
            # half of the runtime. An unset value normalises to "" so files that
            # simply do not declare a SAR still compare equal.
            signature.append(
                (
                    "video",
                    str(stream.get("codec_name") or "").lower(),
                    int(stream.get("width") or 0),
                    int(stream.get("height") or 0),
                    round(rational_to_float(stream.get("avg_frame_rate")) or rational_to_float(stream.get("r_frame_rate")) or 0.0, 3),
                    str(stream.get("pix_fmt") or "").lower(),
                    normalized_sar_text(stream.get("sample_aspect_ratio")),
                    str(stream.get("profile") or "").lower(),
                    str(stream.get("level") if stream.get("level") is not None else ""),
                    str(stream.get("field_order") or "").lower(),
                )
            )
        elif codec_type == "audio":
            signature.append(
                (
                    "audio",
                    str(stream.get("codec_name") or "").lower(),
                    int(stream.get("sample_rate") or 0),
                    int(stream.get("channels") or 0),
                    str(stream.get("channel_layout") or "").lower(),
                )
            )
        else:
            signature.append(("subtitle", str(stream.get("codec_name") or "").lower()))
    return signature


__all__ = [
    'stream_tag_size_bytes',
    'stream_size_plausible',
    'stream_title',
    'add_files_stream_copy_compatibility_errors',
    'extract_stream_container_options',
    'extract_stream_candidates',
    'build_extract_stream_command',
    'join_stream_signature',
    'normalized_sar_text',
]
