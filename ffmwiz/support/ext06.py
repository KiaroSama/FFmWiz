"""FFmWiz extracted helper tier ext6 (post-services layer).

Imports core, support, and top-level ffmwiz modules; acyclic.
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
import queue
import threading
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
from ffmwiz import appio  # noqa: F401
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz import runtime  # noqa: F401
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.support.ext04 import *  # noqa: F401,F403
from ffmwiz.support.ext05 import *  # noqa: F401,F403


def format_total_bitrate_kbps(fmt: dict[str, Any] | None) -> int | None:
    direct = bitrate_kbps(None, fmt)
    if direct:
        return direct
    duration = services.stream_duration_seconds({}, fmt)
    size = format_size_bytes_from_metadata(fmt)
    if duration and size:
        return max(1, round(size * 8 / duration / 1000))
    return None


def stream_size_bytes(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None = None,
    packet_sizes: dict[int, int] | None = None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> tuple[int | None, bool]:
    stream_index = stream.get("index")
    if packet_sizes and stream_index in packet_sizes:
        return packet_sizes[stream_index], False

    exact = stream_tag_size_bytes(stream)
    if stream_size_plausible(exact, fmt) and stream_statistics_tags_trustworthy(stream, fmt, sibling_streams):
        return exact, False

    duration = services.stream_duration_seconds(stream, fmt)
    rate = bitrate_kbps(stream, fmt, sibling_streams)
    if os.environ.get("FFMWIZ_ALLOW_ESTIMATED_STREAM_SIZES") and duration and rate:
        return round(rate * 1000 * duration / 8), True
    return None, True


def prepare_copy_cut_chapter_plan(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
    temp_dir: Path | None = None,
) -> dict[str, Any]:
    plan = analyze_copy_cut_chapter_plan(answers, keep_ranges)
    if plan.get("mode") == "metadata":
        if temp_dir is None:
            raise ValueError("A temporary directory is required to rebuild Copy Cut chapters.")
        plan = dict(plan)
        plan["metadata_path"] = write_copy_cut_chapter_metadata(plan, temp_dir)
    return plan


def step_extract_stream_input(answers: dict[str, Any]) -> None:
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter a media file OR a folder to extract from",
                "a file extracts its streams; a folder extracts from every media file inside",
                back="back=b, quit=exit",
            )
        )
        lowered = value.strip().lower()
        if lowered in {"b", "back"}:
            raise Back()
        if not value.strip():
            appio.error("Enter a file or folder path.")
            continue
        path = terminal_path(value)
        if not path.exists():
            appio.error("Path not found. Enter an existing file or folder path.")
            continue
        entries = extract_scan_files(answers, path)
        if not entries:
            appio.error("No extractable video/audio/subtitle streams were found here.")
            continue
        answers["_extract_files"] = entries
        answers["_extract_is_folder"] = path.is_dir()
        answers["input_path"] = path
        appio.note(
            f"Found {len(entries)} media file(s) with extractable streams."
            if path.is_dir() else f"Loaded: {path.name}"
        )
        print_extract_files_listing(entries)
        log_info(f"Extract input resolved: path={path}; is_folder={path.is_dir()}; files={len(entries)}")
        return


__all__ = [
    'format_total_bitrate_kbps',
    'stream_size_bytes',
    'prepare_copy_cut_chapter_plan',
    'step_extract_stream_input',
]
