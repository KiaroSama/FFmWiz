"""FFmWiz helpers (dependency level 1) — concerns: metadata(4).

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


def write_encode_chapter_metadata(plan: dict[str, Any], temp_dir: Path, suffix: str = "") -> Path:
    """Write an FFmetadata file for remapped encode chapters."""
    metadata_path = temp_dir / f"chapters_encode{suffix}.ffmetadata"
    lines = [";FFMETADATA1", ""]
    for chapter in plan.get("chapters") or []:
        start_ms = max(0, int(round(float(chapter["start"]) * 1000)))
        end_ms = max(start_ms + 1, int(round(float(chapter["end"]) * 1000)))
        lines.extend([
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start_ms}",
            f"END={end_ms}",
        ])
        for key, value in (chapter.get("metadata") or {}).items():
            if value is None:
                continue
            lines.append(f"{ffmetadata_escape(key)}={ffmetadata_escape(value)}")
        lines.append("")
    metadata_path.write_text("\n".join(lines), encoding="utf-8")
    return metadata_path


def metadata_stream_spec(probe_json: dict[str, Any], stream: dict[str, Any]) -> str:
    stream_index = metadata_stream_index(stream)
    codec_type = metadata_stream_type(stream)
    prefix = {"video": "v", "audio": "a", "subtitle": "s", "attachment": "t", "data": "d"}.get(codec_type)
    if prefix is None:
        return str(stream_index if stream_index is not None else 0)
    relative = 0
    for candidate in probe_json.get("streams") or []:
        if metadata_stream_type(candidate) != codec_type:
            continue
        if metadata_stream_index(candidate) == stream_index:
            return f"{prefix}:{relative}"
        relative += 1
    return str(stream_index if stream_index is not None else 0)


def metadata_attached_picture_streams(probe: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        stream for stream in (probe.get("streams") or [])
        if metadata_stream_type(stream) == "video"
        and int((stream.get("disposition") or {}).get("attached_pic") or 0)
    ]


def write_copy_cut_chapter_metadata(plan: dict[str, Any], temp_dir: Path) -> Path:
    metadata_path = temp_dir / "chapters.ffmetadata"
    lines = [";FFMETADATA1", ""]
    for chapter in plan.get("chapters") or []:
        start_ms = max(0, int(round(float(chapter["start"]) * 1000)))
        end_ms = max(start_ms + 1, int(round(float(chapter["end"]) * 1000)))
        lines.extend([
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start_ms}",
            f"END={end_ms}",
        ])
        for key, value in (chapter.get("metadata") or {}).items():
            if value is None:
                continue
            lines.append(f"{ffmetadata_escape(key)}={ffmetadata_escape(value)}")
        lines.append("")
    metadata_path.write_text("\n".join(lines), encoding="utf-8")
    return metadata_path


__all__ = [
    'write_encode_chapter_metadata',
    'metadata_stream_spec',
    'metadata_attached_picture_streams',
    'write_copy_cut_chapter_metadata',
]
