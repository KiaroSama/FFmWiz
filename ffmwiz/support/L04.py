"""FFmWiz helpers (dependency level 4) — concerns: encode_opts(3), misc(2), metadata(1).

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


def cpu_graph_pixel_format_for_encoder(answers: dict[str, Any], video_encoder: str | None = None) -> str:
    """Terminal 'format=' for a CPU/software filter graph, chosen for the
    resolved encoder. This is the single decision point for the final pixel
    format produced by software filter graphs:

      - hevc/h264_nvenc : p010le (10-bit) / yuv420p (8-bit)  [software frames]
      - libx265/libx264 : yuv420p10le (10-bit) / yuv420p (8-bit)

    CUDA hardware filter graphs use cuda_pixel_format_for_output instead
    (scale_cuda=format=p010le / nv12)."""
    if video_encoder is None:
        video_encoder, _tag, _profile = resolve_video_encoder(answers)
    if str(video_encoder).endswith("_nvenc"):
        return nvenc_software_pixel_format_for_output(answers)
    return cpu_pixel_format_for_output(answers)


def target_pixel_format_for_answers(answers: dict[str, Any]) -> str:
    """The pixel format the resolved encoder will output (CPU or NVENC)."""
    video_encoder, _tag, _profile = resolve_video_encoder(answers)
    if str(video_encoder).endswith("_nvenc"):
        return cuda_pixel_format_for_output(answers)
    return cpu_pixel_format_for_output(answers)


def output_size_alignment(answers: dict[str, Any]) -> tuple[int, int]:
    """Return the (width, height) alignment the cropped frame must satisfy.

    When a resize step follows the crop, that scale (force_divisible_by=2 plus a
    pad to an even canvas) already produces encodable dimensions, so the crop
    output size needs no separate even-dimension correction and only the crop
    origin must stay chroma-aligned. Without a following resize, the cropped
    dimensions must satisfy the output pixel-format / encoder grid directly.

    The "resize requested" test mirrors calculate_scale_dimensions (which treats
    only None / "n" as no-resize) and intentionally avoids calling
    resolve_scale_dimensions, because that path computes scaled dimensions from
    cropped_source_size and would recurse back into crop normalization."""
    resolution = answers.get("resolution", "n")
    resize_follows = resolution is not None and resolution != "n"
    if resize_follows:
        return (1, 1)
    return chroma_subsampling_alignment(cpu_pixel_format_for_output(answers))


def stream_has_fast_size_metadata(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> bool:
    return stream_size_plausible(stream_tag_size_bytes(stream), fmt) and stream_statistics_tags_trustworthy(stream, fmt, sibling_streams)


def build_video_speed_reverse_segment_command(
    answers: dict[str, Any],
    start: float,
    end: float,
    output_path: Path,
) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    speed = clamp_speed_factor(answers.get("speed_factor", DEFAULT_SPEED_FACTOR))
    reverse = bool(answers.get("reverse_video"))
    include_audio = bool(answers.get("include_audio", True)) and bool(answers.get("audio_streams"))
    audio_count = len(answers.get("audio_streams") or [])
    cmd: list[str] = [
        ffmpeg,
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-ss",
        ffmpeg_float(start),
        "-t",
        ffmpeg_float(max(0.0, end - start)),
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
    ]
    cmd.extend(["-sn", "-dn", "-filter:v", build_video_speed_filter(speed, reverse)])
    cmd.extend(["-c:v", "libx264", "-preset", CPU_PRESET, "-crf", "18", "-pix_fmt", cpu_pixel_format_for_output(answers)])
    if include_audio:
        labels: list[str] = []
        parts: list[str] = []
        for index in range(audio_count):
            label = f"aspd{index}"
            labels.append(label)
            parts.append(f"[0:a:{index}]{build_audio_speed_filter(speed, reverse)}[{label}]")
        cmd.extend(["-filter_complex", ";".join(parts)])
        for label in labels:
            cmd.extend(["-map", f"[{label}]"])
        cmd.extend(["-c:a", DEFAULT_AUDIO_CODEC, "-b:a", f"{DEFAULT_SPEED_AUDIO_BITRATE_KBPS}k"])
        if AUDIO_CHANNELS:
            cmd.extend(["-ac", str(AUDIO_CHANNELS)])
    else:
        cmd.append("-an")
    if output_path.suffix.lstrip(".").lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])
    cmd.append(str(output_path))
    return cmd


def append_hardsub_color_args(cmd: list[str], answers: dict[str, Any]) -> None:
    handling = answers.get("hardsub_hdr_handling", "standard")
    stream = (answers.get("video_streams") or [{}])[0]
    if handling == "tone-map":
        cmd.extend(["-color_range", "tv", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"])
        return
    if handling != "preserve":
        # Standard handling: write the resolved output color-range metadata
        # (detected source range, or the user's unknown-range assumption). This
        # only sets metadata; it performs no pixel-value range conversion.
        cmd.extend(color_range_output_args(answers, "", workflow="build_hardsub_command"))
        return
    for ff_arg, key in (
        ("-color_range", "color_range"),
        ("-color_primaries", "color_primaries"),
        ("-color_trc", "color_transfer"),
        ("-colorspace", "color_space"),
    ):
        value = str(stream.get(key) or "").strip()
        if value and value.lower() not in {"unknown", "unspecified", "reserved"}:
            cmd.extend([ff_arg, value])


__all__ = [
    'cpu_graph_pixel_format_for_encoder',
    'target_pixel_format_for_answers',
    'output_size_alignment',
    'stream_has_fast_size_metadata',
    'build_video_speed_reverse_segment_command',
    'append_hardsub_color_args',
]
