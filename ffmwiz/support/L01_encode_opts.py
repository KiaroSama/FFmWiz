"""FFmWiz helpers (dependency level 1) — concerns: encode_opts(9).

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


def nvenc_multipass_args(mode: Any) -> list[str]:
    normalized = normalize_nvenc_multipass_mode(mode)
    if normalized in {"qres", "fullres"}:
        return ["-multipass", normalized]
    return []


def source_video_bit_depth(answers: dict[str, Any]) -> int | None:
    stream = source_video_stream(answers)
    return video_bit_depth(stream) if stream else None


def append_negative_stream_options(
    cmd: list[str],
    answers: dict[str, Any],
    has_video: bool,
    subtitle_indices: list[int],
    data_mapped: bool,
) -> None:
    if not has_video:
        cmd.append("-vn")
    if answers.get("subtitle_streams") and not subtitle_indices:
        cmd.append("-sn")
    if source_data_streams(answers) and not data_mapped:
        cmd.append("-dn")


def describe_video_bit_depth(stream: dict[str, Any]) -> str:
    depth = video_bit_depth(stream)
    return f"{depth}-bit" if depth else "unknown"


def append_info_section(lines: list[tuple[str, str]], title: str, color: str = Color.LIGHT_BLUE) -> None:
    if lines and lines[-1][0] != "":
        append_info_line(lines)
    append_info_line(lines, title, Color.BOLD + color)
    append_info_line(lines, "-" * max(48, len(title)), Color.GRAY)


def list_encoders(ffmpeg: str, kind: str) -> list[str]:
    try:
        output = run_capture([ffmpeg, "-hide_banner", "-encoders"])
    except Exception:
        if kind == "video":
            return ["hevc_nvenc", "h264_nvenc", "av1_nvenc", "libx265", "libx264", "libsvtav1"]
        return ["aac", "libopus", "libmp3lame", "flac"]

    wanted = "V" if kind == "video" else "A"
    encoders: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] != wanted:
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            encoders.append(parts[1])
    return sorted(set(encoders))


def append_video_bitrate_args(
    cmd: list[str],
    answers: dict[str, Any],
    bitrate_kbps: int,
    stream_spec: str = ":v",
) -> None:
    mode = video_bitrate_mode(answers)
    # SVT-AV1 uses simple VBR targeting (-b:v); HRD-style -maxrate/-bufsize are
    # not part of its recommended rate control, so emit only the target bitrate.
    recent_encoder = ""
    for _i in range(len(cmd) - 1):
        if cmd[_i] in ("-c:v", "-c:v:0"):
            recent_encoder = str(cmd[_i + 1]).strip().lower()
    if recent_encoder == "libsvtav1":
        cmd.extend([f"-b{stream_spec}", f"{bitrate_kbps}k"])
        return
    if mode == "strict_size":
        maxrate = bitrate_kbps
        bufsize = bitrate_kbps * 2
    else:
        maxrate = bitrate_kbps * 2
        bufsize = bitrate_kbps * 4
    cmd.extend([
        f"-b{stream_spec}", f"{bitrate_kbps}k",
        f"-maxrate{stream_spec}", f"{maxrate}k",
        f"-bufsize{stream_spec}", f"{bufsize}k",
    ])


def cpu_two_pass_log_prefix(answers: dict[str, Any]) -> Path:
    existing = answers.get("_cpu_two_pass_passlogfile")
    if existing:
        return Path(str(existing))
    safe_stem = sanitize_output_stem(Path(str(answers.get("output_path") or "ffmwiz")).stem)[:48] or "ffmwiz"
    passlog = Path(tempfile.gettempdir()) / f"ffmwiz_2pass_{safe_stem}_{os.getpid()}_{int(time.time())}"
    answers["_cpu_two_pass_passlogfile"] = str(passlog)
    return passlog


def append_hardsub_quality_args(cmd: list[str], answers: dict[str, Any], video_encoder: str) -> None:
    quality = hardsub_quality_value(answers, video_encoder)
    if video_encoder.endswith("_nvenc"):
        cmd.extend(["-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq:v", str(quality), "-b:v", "0"])
    elif video_encoder in {"libx264", "libx265"}:
        cmd.extend(["-preset", "slow", "-crf", str(quality)])
    elif video_encoder in {"libaom-av1", "libvpx-vp9"}:
        cmd.extend(["-crf", str(quality), "-b:v", "0"])
    else:
        cmd.extend(["-q:v", str(max(1, min(31, quality)))])


__all__ = [
    'nvenc_multipass_args',
    'source_video_bit_depth',
    'append_negative_stream_options',
    'describe_video_bit_depth',
    'append_info_section',
    'list_encoders',
    'append_video_bitrate_args',
    'cpu_two_pass_log_prefix',
    'append_hardsub_quality_args',
]
