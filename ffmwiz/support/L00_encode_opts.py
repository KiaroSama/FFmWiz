"""FFmWiz helpers (dependency level 0) — concerns: encode_opts(15).

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


def normalize_nvenc_multipass_mode(value: Any) -> str:
    mode = str(value or "disabled").strip().lower()
    return mode if mode in NVENC_MULTIPASS_MODES else "disabled"


def nvenc_multipass_default_mode(answers: dict[str, Any], quality_oriented: bool = True) -> str:
    if not quality_oriented:
        return "disabled"
    # Default to qres (quarter-resolution first pass): a good quality/speed balance
    # and noticeably faster than fullres, which most users do not need by default.
    return "qres"


def append_container_options(cmd: list[str], output_ext: str) -> None:
    if output_ext.lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])


def append_clear_stream_stat_metadata(cmd: list[str], stream_spec: str) -> None:
    for key in STREAM_STAT_METADATA_TAGS:
        cmd.extend([f"-metadata:s:{stream_spec}", f"{key}="])


def append_additional_source_video_codec_options(cmd: list[str], count: int) -> None:
    for relative_index in range(1, count + 1):
        cmd.extend([f"-c:v:{relative_index}", "copy"])


def pix_fmt_descriptor(pix_fmt: Any) -> dict[str, Any]:
    """Describe a pixel format: bit depth, chroma label, and colour model.
    Unknown formats are reported as such and never raise."""
    fmt = str(pix_fmt or "").strip().lower()
    desc: dict[str, Any] = {"pix_fmt": fmt or "unknown", "bit_depth": None,
                            "chroma": "unknown", "kind": "unknown"}
    if not fmt or fmt in {"unknown", "none"}:
        return desc

    # Bit depth: explicit suffix (10le/12le/16le) or pNNN family, else 8.
    depth = 8
    m = re.search(r"(\d{1,2})(?:le|be)?$", fmt)
    if m and fmt not in {"nv12", "nv21", "nv16", "nv24", "nv42"}:
        token = int(m.group(1))
        if token in (9, 10, 12, 14, 16):
            depth = token
    if fmt in {"p010", "p010le", "p010be", "p210", "p210le", "p410", "p410le"}:
        depth = 10
    if fmt in {"p016", "p016le", "p216", "p416"}:
        depth = 16

    if fmt.startswith(("rgb", "bgr", "gbr", "argb", "abgr", "rgba", "bgra",
                       "0rgb", "0bgr", "rgb0", "bgr0")):
        desc.update(bit_depth=depth, chroma="RGB", kind="rgb")
        return desc
    if fmt.startswith(("gray", "ya")) or fmt in {"monow", "monob"}:
        desc.update(bit_depth=depth, chroma="gray", kind="gray")
        return desc
    if fmt in {"nv12", "nv21", "p010", "p010le", "p010be", "p016", "p016le"}:
        desc.update(bit_depth=depth, chroma="4:2:0", kind="yuv")
        return desc
    if fmt in {"nv16", "p210", "p210le", "yuyv422", "uyvy422", "yvyu422"}:
        desc.update(bit_depth=depth, chroma="4:2:2", kind="yuv")
        return desc
    if fmt in {"nv24", "nv42", "p410", "p410le", "p416"}:
        desc.update(bit_depth=depth, chroma="4:4:4", kind="yuv")
        return desc
    for token, chroma in (("444", "4:4:4"), ("440", "4:4:0"), ("422", "4:2:2"),
                          ("411", "4:1:1"), ("410", "4:1:0"), ("420", "4:2:0")):
        if token in fmt:
            desc.update(bit_depth=depth, chroma=chroma, kind="yuv")
            return desc
    desc.update(bit_depth=depth)
    return desc


def video_bit_depth(stream: dict[str, Any]) -> int | None:
    for key in ("bits_per_raw_sample", "bits_per_sample", "bits_per_coded_sample"):
        value = stream.get(key)
        if value not in (None, "", "0", 0):
            try:
                depth = int(value)
                if depth > 0:
                    return depth
            except (TypeError, ValueError):
                pass

    pixel_format = str(stream.get("pix_fmt") or "").lower()
    if not pixel_format:
        return None
    match = re.search(r"(?:p0?|yuv|gray|gbrp)(10|12|14|16)(?:le|be)?", pixel_format)
    if match:
        return int(match.group(1))
    if pixel_format in {"yuv420p", "yuv422p", "yuv444p", "nv12", "rgb24", "bgr24", "rgba", "bgra"}:
        return 8
    if pixel_format in {"rgba64le", "rgba64be", "rgb48le", "rgb48be"}:
        return 16
    return None


def append_info_line(lines: list[tuple[str, str]], text: str = "", color: str = Color.WHITE) -> None:
    lines.append((text, color))


def nvenc_encoder_available(video_encoders: list[str] | tuple[str, ...] | set[str] | None) -> bool:
    encoders = {str(name).lower() for name in (video_encoders or [])}
    return any(name.endswith("_nvenc") for name in encoders)


def encoder_supports_two_pass(video_encoder: Any) -> bool:
    """True if the CPU encoder supports FFmpeg's -pass 1/2 two-pass rate
    control (verified set; see TWO_PASS_CPU_ENCODERS)."""
    return str(video_encoder or "").strip().lower() in TWO_PASS_CPU_ENCODERS


def resolve_video_encoder(answers: dict[str, Any]) -> tuple[str, str | None, str | None]:
    requested = answers.get("video_codec", DEFAULT_VIDEO_CODEC).strip()
    lowered = requested.lower()
    if lowered == "copy":
        return "copy", None, None

    info = VIDEO_CODEC_ALIASES.get(lowered)
    if info:
        if answers.get("use_gpu") and info.get("gpu"):
            return info["gpu"], info.get("tag"), info.get("profile")
        return info["cpu"], info.get("tag"), info.get("profile")

    return requested, None, None


def append_cuda_decode_args_for_input(cmd: list[str], answers: dict[str, Any]) -> None:
    cmd.extend(["-hwaccel", "cuda", "-hwaccel_device", str(GPU_DEVICE_INDEX)])


def cpu_two_pass_enabled_for_command(answers: dict[str, Any], cmd: list[str]) -> bool:
    if not answers.get("cpu_two_pass"):
        return False
    text = " ".join(str(part) for part in cmd)
    # The chosen video encoder appears as "-c:v <enc>" or "-c:v:0 <enc>".
    for enc in TWO_PASS_CPU_ENCODERS:
        if f"-c:v {enc}" in text or f"-c:v:0 {enc}" in text:
            return True
    return False


def cpu_two_pass_video_output_args(output_args: list[str]) -> list[str]:
    prefixes_with_values = (
        "-filter:v",
        "-vf",
        "-r:v",
        "-fps_mode:v",
        "-c:v",
        "-preset",
        "-profile:v",
        "-b:v",
        "-maxrate:v",
        "-bufsize:v",
        "-color_range:v",
        "-pix_fmt",
        "-x264-params",
        "-x265-params",
        "-svtav1-params",
        "-aom-params",
    )
    result: list[str] = []
    idx = 0
    while idx < len(output_args):
        opt = output_args[idx]
        if any(opt == prefix or opt.startswith(prefix + ":") for prefix in prefixes_with_values):
            if idx + 1 >= len(output_args):
                raise ValueError(f"Missing value for two-pass video option: {opt}")
            if opt != "-c" and output_args[idx + 1] != "copy":
                result.extend([opt, output_args[idx + 1]])
            idx += 2
            continue
        idx += 1
    text = " ".join(result)
    if "-c:v" not in text:
        raise ValueError("CPU two-pass command could not find the final video encoder options.")
    return result


def cleanup_cpu_two_pass_logs(passlog: Path) -> None:
    parent = passlog.parent
    prefix = passlog.name
    try:
        for path in parent.glob(prefix + "*"):
            try:
                path.unlink()
            except OSError:
                pass
    except OSError:
        pass


__all__ = [
    'normalize_nvenc_multipass_mode',
    'nvenc_multipass_default_mode',
    'append_container_options',
    'append_clear_stream_stat_metadata',
    'append_additional_source_video_codec_options',
    'pix_fmt_descriptor',
    'video_bit_depth',
    'append_info_line',
    'nvenc_encoder_available',
    'encoder_supports_two_pass',
    'resolve_video_encoder',
    'append_cuda_decode_args_for_input',
    'cpu_two_pass_enabled_for_command',
    'cpu_two_pass_video_output_args',
    'cleanup_cpu_two_pass_logs',
]
