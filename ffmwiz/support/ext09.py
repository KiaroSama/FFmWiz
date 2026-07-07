"""FFmWiz extracted helper tier ext9 (post-services layer).

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
from ffmwiz.support.ext07 import *  # noqa: F401,F403
from ffmwiz.support.ext08 import *  # noqa: F401,F403


def format_join_input_summary_lines(answers: dict[str, Any]) -> list[str]:
    """Plain-text lines for the Join input summary (no color), for tests/logs."""
    rows = join_input_media_stats(answers)
    if len(rows) < 2:
        return []
    lines = ["Join input summary", f"Files selected: {len(rows)}"]

    def metric_line(label: str, key: str, unit: str, render) -> str:
        result = _join_summary_minmax(rows, key)
        if result is None:
            return f"{label}: unavailable"
        (hi_value, hi_name), (lo_value, lo_name), unknown = result
        text = f"{label}: highest {render(hi_value)}{unit} ({hi_name}), lowest {render(lo_value)}{unit} ({lo_name})"
        if unknown:
            text += f"; {unknown} unknown"
        return text

    lines.append(metric_line("Video bitrate", "video_kbps", " kbps", lambda v: f"{int(v):,}"))
    lines.append(metric_line("Audio bitrate", "audio_kbps", " kbps", lambda v: f"{int(v):,}"))
    lines.append(metric_line("FPS", "fps", "", lambda v: f"{v:.3f}"))
    duration_info = join_summary_total_duration(answers, rows)
    lines.append(f"Total raw duration: {join_summary_duration_text(duration_info)}")
    volume = join_summary_volume_extremes(rows)
    if volume is None:
        lines.append("Mean volume: unavailable")
        lines.append("Max volume: unavailable")
    else:
        if volume["lowest_mean"]:
            lines.append(f"Mean volume: lowest {volume['lowest_mean'][0]:.1f} dB ({volume['lowest_mean'][1]})")
        else:
            lines.append("Mean volume: unavailable")
        if volume["highest_max"]:
            lines.append(f"Max volume: highest {volume['highest_max'][0]:.1f} dB ({volume['highest_max'][1]})")
        else:
            lines.append("Max volume: unavailable")
    return lines


def detect_duplicate_audio(answers: dict[str, Any]) -> dict[str, Any]:
    if "audio_duplicate_report" in answers:
        return answers["audio_duplicate_report"]

    input_path: Path = answers["input_path"]
    fmt = answers.get("format", {})
    audio_streams = answers.get("audio_streams", [])
    packet_sizes = services.get_packet_sizes(answers)
    possible_pairs: list[tuple[int, int]] = []
    confirmed_pairs: list[tuple[int, int]] = []
    hashes: dict[int, str | None] = {}
    sample_hashes: dict[int, str | None] = {}
    empty_tracks, near_empty_tracks = classify_sparse_audio_tracks(audio_streams, fmt, packet_sizes)
    ignored_tracks = empty_tracks | near_empty_tracks

    for left_pos in range(len(audio_streams)):
        for right_pos in range(left_pos + 1, len(audio_streams)):
            left = audio_streams[left_pos]
            right = audio_streams[right_pos]
            if left_pos in ignored_tracks or right_pos in ignored_tracks:
                continue
            if possible_audio_duplicate(left, right, fmt, packet_sizes):
                possible_pairs.append((left_pos, right_pos))

    hash_positions = sorted({pos for pair in possible_pairs for pos in pair})
    if hash_positions:
        def _sample_hash_position(pos: int) -> tuple[int, str | None]:
            stream = audio_streams[pos]
            duration = services.stream_duration_seconds(stream, fmt) or services.stream_duration_seconds({}, fmt)
            return pos, audio_hash(answers["ffmpeg"], input_path, int(stream["index"]), duration)

        max_workers = min(DUPLICATE_AUDIO_HASH_WORKERS, len(hash_positions))
        if max_workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                for pos, value in executor.map(_sample_hash_position, hash_positions):
                    sample_hashes[pos] = value
        else:
            for pos in hash_positions:
                pos, value = _sample_hash_position(pos)
                sample_hashes[pos] = value

    full_hash_positions = sorted({
        pos
        for left_pos, right_pos in possible_pairs
        if sample_hashes.get(left_pos) and sample_hashes.get(left_pos) == sample_hashes.get(right_pos)
        for pos in (left_pos, right_pos)
    })
    if full_hash_positions:
        def _full_hash_position(pos: int) -> tuple[int, str | None]:
            stream = audio_streams[pos]
            return pos, audio_hash_full(answers["ffmpeg"], input_path, int(stream["index"]))

        max_workers = min(DUPLICATE_AUDIO_HASH_WORKERS, len(full_hash_positions))
        if max_workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                for pos, value in executor.map(_full_hash_position, full_hash_positions):
                    hashes[pos] = value
        else:
            for pos in full_hash_positions:
                pos, value = _full_hash_position(pos)
                hashes[pos] = value

    for left_pos, right_pos in possible_pairs:
        if hashes.get(left_pos) and hashes.get(left_pos) == hashes.get(right_pos):
            confirmed_pairs.append((left_pos, right_pos))

    report = {
        "possible_pairs": possible_pairs,
        "confirmed_pairs": confirmed_pairs,
        "empty_tracks": empty_tracks,
        "near_empty_tracks": near_empty_tracks,
        "hashes": hashes,
        "sample_hashes": sample_hashes,
    }
    answers["audio_duplicate_report"] = report
    return report


def build_media_info_analysis(
    ffprobe: str,
    input_path: Path,
    payload: dict[str, Any],
    info_path: Path,
    deep_analysis: bool,
    sidecars: list[Path],
    skipped: list[str],
) -> dict[str, Any]:
    analysis: dict[str, Any] = {}
    packet_sizes: dict[int, int] = {}
    print("Calculating exact stream sizes...")
    log_info(f"Media Info exact stream-size packet scan started for {input_path}")
    packet_sizes = services.probe_packet_sizes(ffprobe, input_path)
    log_info(
        f"Media Info exact stream-size packet scan completed for {input_path}; "
        f"streams={len(packet_sizes)}"
    )
    if deep_analysis:
        log_info(f"Media Info deep analysis enabled for {input_path}")
    else:
        log_info(
            f"Media Info deep analysis disabled by user for {input_path}; "
            "exact stream sizes were still calculated, but CSV sidecars will not be generated."
        )
        skipped.append("Deep packet/frame analysis skipped by user.")
    analysis["packet_sizes"] = packet_sizes
    stream_rows = media_info_stream_size_rows(input_path, payload, packet_sizes)
    analysis["stream_size_rows"] = stream_rows
    analysis["bpppf_rows"] = calculate_bpppf_rows(payload, packet_sizes)
    if deep_analysis:
        stream_csv = media_info_sidecar_path(info_path, "stream_summary", ".csv")
        write_media_info_stream_summary_csv(stream_csv, stream_rows)
        sidecars.append(stream_csv)
        log_info(f"Media Info stream summary CSV written: {stream_csv}")
        print("Running per-second bitrate analysis...")
        packet_csv = media_info_sidecar_path(info_path, "per_second_bitrate", ".csv")
        packet_summary, packet_skip = analyze_packet_bitrate(ffprobe, input_path, packet_csv)
        if packet_summary:
            analysis["packet_bitrate"] = packet_summary
            sidecars.append(packet_csv)
            log_info(f"Media Info per-second bitrate CSV written: {packet_csv}")
        else:
            analysis["packet_bitrate_skip"] = packet_skip
            skipped.append(f"Per-second bitrate analysis skipped: {packet_skip}.")
            log_warn(f"Media Info per-second bitrate analysis skipped for {input_path}: {packet_skip}")
        print("Running frame type / GOP analysis...")
        frame_csv = media_info_sidecar_path(info_path, "frame_analysis", ".csv")
        frame_summary, frame_skip = analyze_frame_types_and_gop(ffprobe, input_path, frame_csv)
        if frame_summary:
            analysis["frame_analysis"] = frame_summary
            sidecars.append(frame_csv)
            log_info(f"Media Info frame analysis CSV written: {frame_csv}")
        else:
            analysis["frame_analysis_skip"] = frame_skip
            skipped.append(f"Frame type / GOP analysis skipped: {frame_skip}.")
            log_warn(f"Media Info frame type / GOP analysis skipped for {input_path}: {frame_skip}")
    return analysis


def join_max_source_audio_bitrate(answers: dict[str, Any], audio_index: int) -> tuple[int | None, str | None]:
    """Highest known source audio bitrate (and its file name) for the given
    track across all joined inputs. Returns (None, None) when all are unknown."""
    candidates = join_audio_bitrate_candidates(answers, audio_index)
    if not candidates:
        return None, None
    best_kbps, best_name = max(candidates, key=lambda pair: pair[0])
    return best_kbps, best_name


def ask_additional_track_files(
    answers: dict[str, Any],
    current_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    extra_items: list[dict[str, Any]] = list(current_items or [])
    while True:
        answers["_question_number"] = len(extra_items) + 2
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter audio/subtitle file to add",
                (
                    f"{paint('type done when finished', Color.LIME)}; "
                    f"{paint('audio', Color.BLUE)} or {paint('subtitle', Color.MAGENTA)} file; "
                    f"{paint('Enter after each path asks for the next file', Color.HINT_YELLOW)}"
                ),
            )
        )
        lowered = value.lower().strip()
        if is_back_value(value):
            if extra_items:
                removed = extra_items.pop()
                appio.note(f"Removed added file: {Path(removed['path']).name}")
                continue
            raise Back()
        if lowered == "done":
            if not extra_items:
                appio.error("Add at least one audio or subtitle file before typing done.")
                continue
            return extra_items
        if not value:
            appio.error("Enter an audio/subtitle file path, or type done when finished.")
            continue

        path = terminal_path(value)
        if not path.exists() or not path.is_file():
            appio.error("File not found. Enter the full file path again.")
            continue
        if paths_same(path, answers["input_path"]):
            appio.error("The additional file cannot be the same as the source video.")
            continue
        try:
            item = probe_additional_track_file(answers["ffprobe"], path, answers.get("ffmpeg"))
        except FFprobeError as exc:
            appio.error(str(exc))
            continue
        except Exception as exc:
            log_exception(f"Could not probe additional track file: {path}")
            appio.error(str(exc))
            continue
        print_additional_track_file_info(item)
        try:
            ask_additional_track_metadata(answers, item)
        except RetryAdditionalFile:
            continue
        extra_items.append(item)
        appio.note(f"Added file #{len(extra_items)}: {path.name} ({describe_additional_track_file(item)})")


def print_extract_stream_candidates(answers: dict[str, Any]) -> None:
    print()
    print(paint("Extractable streams", Color.BOLD + Color.LIGHT_BLUE))
    for stream in extract_stream_candidates(answers):
        print("  " + extract_stream_description(stream, answers))


__all__ = [
    'format_join_input_summary_lines',
    'detect_duplicate_audio',
    'build_media_info_analysis',
    'join_max_source_audio_bitrate',
    'ask_additional_track_files',
    'print_extract_stream_candidates',
]
