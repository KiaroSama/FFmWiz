"""Probe data described for the reader: the media-info text builders.

Split out of `L02` as its own responsibility. Every function here takes a raw
ffprobe stream (or format) dict and returns what the Media Info report, the
stream listings and the HDR/Dolby check put in front of the user. No answers
dict, no command building, no filesystem.

Re-exported by `L02`, so every consumer of `from ffmwiz.support.L02 import *`
still sees the full set. Imports only lower tiers; it never reaches back up
into `L02`.
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
from ffmwiz.core.artifacts import *  # noqa: F401,F403
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


def media_info_seconds_text(value: Any) -> str | None:
    seconds = _as_float_value(value)
    if seconds is None:
        text = str(value).strip()
        colon_seconds = parse_colon_duration_seconds(text)
        if colon_seconds is not None:
            precision = "hh:mm:ss.fraction" if "." in text else "hh:mm:ss"
            return f"{text} ({precision}; {_trim_float(colon_seconds)} s)"
        return None
    return f"{format_duration(seconds)} ({_trim_float(seconds)} s)"


def media_info_bitrate_text(value: Any) -> str | None:
    bit_rate = _as_int_value(value)
    if bit_rate is None:
        return None
    return f"{bit_rate} bit/s ({describe_bitrate(max(1, round(bit_rate / 1000)))})"


def media_info_bytes_text(value: Any) -> str | None:
    size = _as_int_value(value)
    if size is None:
        return None
    return f"{size} B ({format_bytes(size)})"


def info_stream_header(stream: dict[str, Any], relative_index: int, chapter_count: int = 0) -> str:
    codec_type = stream.get("codec_type", "unknown")
    codec_name = stream.get("codec_name", "unknown")
    global_index = stream.get("index", "?")
    title = stream_tag_value(stream, "title", "")
    language = display_language(stream_tag_value(stream, "language", ""))
    suffix = []
    if codec_type == "video":
        suffix.append(f"bit_depth={describe_video_bit_depth(stream)}")
        suffix.append(f"Color range={display_color_range(stream.get('color_range'))}")
        suffix.append(f"chapters={'yes' if chapter_count else 'no'}")
    if language:
        suffix.append(f"language={language}")
    if title:
        suffix.append(f"title={title}")
    suffix_text = " | " + " | ".join(suffix) if suffix else ""
    return f"Stream {relative_index} / #{global_index}: {codec_type} | codec={codec_name}{suffix_text}"


def embedded_attachment_display_line(stream: dict[str, Any], relative_index: int) -> str:
    filename = stream_tag_value(stream, "filename", "")
    mimetype = stream_tag_value(stream, "mimetype", "")
    title = stream_tag_value(stream, "title", "")
    pieces = [
        f"{relative_index}: stream #{stream.get('index', '?')}",
        f"codec={stream.get('codec_name', 'unknown')}",
        f"kind={media_info_attachment_kind(stream)}",
    ]
    if filename:
        pieces.append(f"filename={filename}")
    if mimetype:
        pieces.append(f"mimetype={mimetype}")
    if title:
        pieces.append(f"title={title}")
    return " | ".join(pieces)


def video_hdr_dolby_info(stream: dict[str, Any]) -> dict[str, Any]:
    side_data = stream.get("side_data_list") or []
    side_text = json.dumps(side_data, ensure_ascii=False).lower()
    tags_text = json.dumps(stream.get("tags") or {}, ensure_ascii=False).lower()
    color_transfer = str(stream.get("color_transfer") or "").lower()
    color_primaries = str(stream.get("color_primaries") or "").lower()
    color_space = str(stream.get("color_space") or "").lower()
    hdr = (
        color_transfer in {"smpte2084", "arib-std-b67"}
        or color_primaries == "bt2020"
        or color_space.startswith("bt2020")
        or "mastering display metadata" in side_text
        or "content light level metadata" in side_text
    )
    dolby = (
        "dovi" in side_text
        or "dolby vision" in side_text
        or "dv_profile" in side_text
        or "dovi" in tags_text
        or "dolby vision" in tags_text
    )
    return {
        "hdr": hdr,
        "dolby": dolby,
        "color_transfer": stream.get("color_transfer", "unknown"),
        "color_primaries": stream.get("color_primaries", "unknown"),
        "color_space": stream.get("color_space", "unknown"),
        "color_range": stream.get("color_range", "unknown"),
        "bit_depth": describe_video_bit_depth(stream),
    }


__all__ = [
    'media_info_seconds_text',
    'media_info_bitrate_text',
    'media_info_bytes_text',
    'info_stream_header',
    'embedded_attachment_display_line',
    'video_hdr_dolby_info',
]
