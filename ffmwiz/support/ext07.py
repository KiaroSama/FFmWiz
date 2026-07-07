"""FFmWiz extracted helper tier ext7 (post-services layer).

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
from ffmwiz.support.ext06 import *  # noqa: F401,F403


def estimate_stream_bitrate_kbps(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None = None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> int | None:
    codec_type = stream.get("codec_type")
    if codec_type == "audio":
        return audio_bitrate_estimate_kbps(stream)
    if codec_type != "video":
        return None

    total = format_total_bitrate_kbps(fmt)
    if not total:
        return None
    siblings = sibling_streams or [stream]
    unknown_video_count = 0
    known_or_estimated_other = 0
    stream_index = stream.get("index")
    for other in siblings:
        other_type = other.get("codec_type")
        direct = bitrate_kbps(other, fmt, siblings)
        if other_type == "video":
            if direct:
                known_or_estimated_other += direct
            elif other.get("index") == stream_index:
                unknown_video_count += 1
            else:
                unknown_video_count += 1
        elif other_type == "audio":
            known_or_estimated_other += direct or audio_bitrate_estimate_kbps(other) or 0

    if unknown_video_count <= 0:
        return None
    remaining = total - known_or_estimated_other
    if remaining <= 0:
        remaining = max(1, round(total * 0.85))
    return max(1, round(remaining / unknown_video_count))


def stream_bitrate_kbps(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None = None,
    packet_sizes: dict[int, int] | None = None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> int | None:
    stream_index = stream.get("index")
    duration = services.stream_duration_seconds(stream, fmt)
    if packet_sizes and stream_index in packet_sizes and duration:
        return max(1, round(packet_sizes[stream_index] * 8 / duration / 1000))
    direct = bitrate_kbps(stream, fmt, sibling_streams)
    if direct:
        return direct
    size, estimated = stream_size_bytes(stream, fmt, packet_sizes, sibling_streams)
    if size and duration and not estimated:
        return max(1, round(size * 8 / duration / 1000))
    return None


def describe_total_bitrate(fmt: dict[str, Any] | None) -> str:
    return describe_bitrate(format_total_bitrate_kbps(fmt))


def run_copy_cut(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
    output_path: Path,
) -> int:
    """Execute stream-copy cuts using one ffmpeg call (single range) or
    segment extraction + concat demuxer (multiple ranges)."""
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    overwrite = "-y" if OVERWRITE_OUTPUT else "-n"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if len(keep_ranges) == 1:
        start, end = keep_ranges[0]
        temp_context: tempfile.TemporaryDirectory[str] | None = None
        try:
            initial_plan = analyze_copy_cut_chapter_plan(answers, keep_ranges)
            if initial_plan.get("mode") == "metadata":
                temp_context = tempfile.TemporaryDirectory(prefix="ffmwiz_copycut_chapters_")
                chapter_plan = dict(initial_plan)
                chapter_plan["metadata_path"] = write_copy_cut_chapter_metadata(chapter_plan, Path(temp_context.name))
            else:
                chapter_plan = initial_plan
            cmd = build_copy_cut_range_command(ffmpeg, overwrite, input_path, start, end, output_path, chapter_plan)
            print()
            print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
            print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
            print()
            print(paint("Starting FFmpeg...", Color.GREEN))
            rc, _ = run_ffmpeg_with_progress(cmd, total_duration=max(0.0, end - start),
                                              label="Stream-copy cut")
            return rc
        finally:
            if temp_context is not None:
                temp_context.cleanup()

    # Multiple ranges: extract segments to MKV intermediates and concat.
    with tempfile.TemporaryDirectory(prefix="ffmwiz_copycut_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        chapter_plan = prepare_copy_cut_chapter_plan(answers, keep_ranges, tmpdir)
        segment_chapter_map = "0" if chapter_plan.get("mode") == "copy" else "-1"
        segments: list[Path] = []
        seg_ext = "mkv"  # MKV is the safest concat-with-copy intermediate.
        for idx, (start, end) in enumerate(keep_ranges):
            seg_path = tmpdir / f"seg_{idx:04d}.{seg_ext}"
            duration = max(0.0, end - start)
            cmd = [
                ffmpeg, overwrite, "-hide_banner",
                "-ss", seconds_to_ffmpeg_time(start),
                "-i", str(input_path),
                "-t", seconds_to_ffmpeg_time(duration),
                "-map", "0",
                "-map_metadata", "0",
                "-map_chapters", segment_chapter_map,
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                str(seg_path),
            ]
            print()
            appio.note(f"Segment {idx + 1}/{len(keep_ranges)}: {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}")
            print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
            rc, _ = run_ffmpeg_with_progress(
                cmd, total_duration=max(0.0, end - start),
                label=f"Segment {idx + 1}/{len(keep_ranges)}",
            )
            if rc != 0:
                appio.error(f"Segment extraction failed for range #{idx + 1}.")
                return rc
            segments.append(seg_path)

        # Build the concat demuxer list file. Path entries use forward slashes
        # and single-quoted strings; ' inside a path is escaped as '\''.
        concat_list = tmpdir / "concat.txt"
        with concat_list.open("w", encoding="utf-8") as handle:
            for seg in segments:
                escaped = seg.as_posix().replace("'", "'\\''")
                handle.write(f"file '{escaped}'\n")

        cmd = build_copy_cut_concat_command(ffmpeg, overwrite, concat_list, output_path, chapter_plan)
        print()
        appio.note("Concatenating segments with -f concat -c copy...")
        print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
        total_seg_duration = sum(max(0.0, e - s) for s, e in keep_ranges)
        rc, _ = run_ffmpeg_with_progress(
            cmd, total_duration=total_seg_duration, label="Concat segments",
        )
        return rc


__all__ = [
    'estimate_stream_bitrate_kbps',
    'stream_bitrate_kbps',
    'describe_total_bitrate',
    'run_copy_cut',
]
