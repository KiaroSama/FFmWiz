"""FFmWiz modes cluster (extracted from FFmWiz.py, method الف)."""
from __future__ import annotations
from dataclasses import dataclass, field

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
import logging
import atexit
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
from ffmwiz.support.ext04 import *  # noqa: F401,F403
from ffmwiz.support.ext05 import *  # noqa: F401,F403
from ffmwiz.support.ext06 import *  # noqa: F401,F403
from ffmwiz.support.ext07 import *  # noqa: F401,F403
from ffmwiz.support.ext08 import *  # noqa: F401,F403
from ffmwiz.support.ext09 import *  # noqa: F401,F403
from ffmwiz.support.ext10 import *  # noqa: F401,F403
from ffmwiz.support.ext11 import *  # noqa: F401,F403
from ffmwiz.support.ext12 import *  # noqa: F401,F403
from ffmwiz.appio import *  # noqa: F401,F403
from ffmwiz import appio  # noqa: F401
from ffmwiz.guibridge import *  # noqa: F401,F403
from ffmwiz import guibridge  # noqa: F401
from ffmwiz.metadata import *  # noqa: F401,F403
from ffmwiz import metadata  # noqa: F401
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz import runner  # noqa: F401
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz import runtime  # noqa: F401
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.trackmanager import *  # noqa: F401,F403
from ffmwiz import trackmanager  # noqa: F401
from ffmwiz.wizard import *  # noqa: F401,F403
from ffmwiz import wizard  # noqa: F401


MEDIA_INFO_VALUE_COLORS = [
    Color.CYAN,
    Color.LIME,
    Color.MAGENTA,
    Color.YELLOW,
    Color.AQUA,
    Color.PINK,
    Color.LIGHT_BLUE,
    Color.ORANGE,
]


def media_info_color_for_key(key: str) -> str:
    normalized = str(key or "").lower().replace("-", "_").replace(" ", "_")
    if normalized == "color_range":
        return Color.COLOR_RANGE_VALUE
    total = sum(ord(ch) for ch in key)
    return MEDIA_INFO_VALUE_COLORS[total % len(MEDIA_INFO_VALUE_COLORS)]


@dataclass
class MediaInfoOptions:
    deep_analysis: bool = True
    extract_screenshots: bool = False
    reference_path: Path | None = None


@dataclass
class MediaInfoReportResult:
    lines: list[tuple[str, str]]
    info_path: Path
    sidecar_paths: list[Path] = field(default_factory=list)
    screenshot_dir: Path | None = None
    skipped_sections: list[str] = field(default_factory=list)


def append_info_kv(lines: list[tuple[str, str]], key: str, value: Any, indent: int = 0, color: str | None = None) -> None:
    prefix = "  " * indent
    value_text = media_info_value_with_units(key, value)
    append_info_line(lines, f"{prefix}{media_info_display_key(key)}: {value_text}", color or media_info_color_for_key(key))


def append_nested_info(
    lines: list[tuple[str, str]],
    value: Any,
    indent: int = 0,
    key_name: str | None = None,
) -> None:
    if isinstance(value, dict):
        items = list(value.items())
        if key_name is not None:
            append_info_line(lines, f"{'  ' * indent}{key_name}:", Color.BOLD + media_info_color_for_key(key_name))
            indent += 1
        if not items:
            append_info_line(lines, f"{'  ' * indent}(empty)", Color.GRAY)
            return
        for key, child in items:
            append_nested_info(lines, child, indent, str(key))
        return
    if isinstance(value, list):
        if key_name is not None:
            append_info_line(lines, f"{'  ' * indent}{key_name}:", Color.BOLD + media_info_color_for_key(key_name))
            indent += 1
        if not value:
            append_info_line(lines, f"{'  ' * indent}(empty)", Color.GRAY)
            return
        for index, child in enumerate(value):
            append_nested_info(lines, child, indent, f"[{index}]")
        return
    append_info_kv(lines, key_name or "value", value, indent)


def append_media_info_table(lines: list[tuple[str, str]], rows: list[dict[str, Any]], indent: int = 1) -> None:
    if not rows:
        append_info_line(lines, "  " * indent + "(none)", Color.GRAY)
        return
    for row in rows:
        text = " | ".join(f"{key}: {value}" for key, value in row.items())
        append_info_line(lines, "  " * indent + text, media_info_color_for_key(str(row.get("type") or row.get("codec") or "row")))


def append_media_info_advanced_sections(
    lines: list[tuple[str, str]],
    input_path: Path,
    payload: dict[str, Any],
    analysis: dict[str, Any] | None,
    audio_volume_stats: dict[int, dict[str, str]] | None,
) -> None:
    analysis = analysis or {}
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    stream_rows = analysis.get("stream_size_rows") or media_info_stream_size_rows(input_path, payload, analysis.get("packet_sizes"))

    append_info_section(lines, "Technical Diagnosis", Color.PINK)
    for observation in build_media_info_technical_diagnosis(input_path, payload, stream_rows):
        append_info_line(lines, "  " + observation, Color.WHITE)

    append_info_section(lines, "Encoder Metadata / Encoding Settings", Color.AQUA)
    append_info_line(
        lines,
        "  Encoder metadata may be unavailable if it was stripped or never written.",
        Color.YELLOW,
    )
    append_info_line(
        lines,
        "  CRF, preset, tune, AQ, keyint, and other encoder settings cannot be reliably detected unless stored in metadata or bitstream information.",
        Color.YELLOW,
    )
    for stream in streams:
        if stream.get("codec_type") not in {"video", "audio"}:
            continue
        append_info_line(lines, "  " + media_info_stream_name(stream), Color.BOLD + Color.CYAN)
        size, estimated = stream_size_bytes(stream, fmt, analysis.get("packet_sizes"), streams)
        bitrate = stream_bitrate_kbps(stream, fmt, analysis.get("packet_sizes"), streams)
        append_info_kv(lines, "computed bitrate", describe_bitrate(bitrate), 2, Color.YELLOW)
        append_info_kv(lines, "computed stream size", f"{format_bytes(size)}{' estimated' if estimated else ''}", 2, Color.LIME)
        for key in ("codec_name", "codec_long_name", "profile", "level", "pix_fmt", "color_range", "color_space", "color_transfer", "color_primaries"):
            if stream.get(key) not in {None, ""}:
                append_info_kv(lines, key, stream.get(key), 2)
        tags = media_info_stream_tags(stream)
        encoder_tags = {key: value for key, value in tags.items() if "encod" in str(key).lower() or str(key).upper() in {"BPS", "NUMBER_OF_BYTES"}}
        if encoder_tags:
            append_nested_info(lines, encoder_tags, 2, "encoder-related tags")
        for side_index, side_data in enumerate(stream.get("side_data_list") or []):
            append_nested_info(lines, side_data, 2, f"side data {side_index}")

    append_info_section(lines, "Stream Size Analysis", Color.LIME)
    append_media_info_table(lines, stream_rows)

    append_info_section(lines, "Bits Per Pixel Per Frame", Color.ORANGE)
    bpppf_rows = analysis.get("bpppf_rows") or calculate_bpppf_rows(payload, analysis.get("packet_sizes"))
    append_media_info_table(lines, bpppf_rows)
    append_info_line(lines, "  Very low bpppf may indicate heavy compression.", Color.GRAY)
    append_info_line(lines, "  Very high bpppf may indicate large file size, near-source encode, inefficient encode, or overkill bitrate.", Color.GRAY)
    append_info_line(lines, "  This metric is only a rough technical indicator, not a final visual quality score.", Color.GRAY)

    append_info_section(lines, "Per-Second Bitrate Analysis", Color.CYAN)
    packet_summary = analysis.get("packet_bitrate")
    if packet_summary:
        for key in ("average_kbps", "minimum_kbps", "maximum_kbps", "p05_kbps", "median_kbps", "p95_kbps"):
            value = packet_summary.get(key)
            append_info_kv(lines, key, f"{value:.3f} kbps" if isinstance(value, (int, float)) else "unknown", 1)
        append_info_kv(lines, "analyzed seconds", packet_summary.get("seconds", "unknown"), 1)
        append_info_kv(lines, "CSV", packet_summary.get("csv_path", "unknown"), 1)
    else:
        append_info_line(lines, "  Skipped: " + str(analysis.get("packet_bitrate_skip") or "packet timestamps were unavailable"), Color.YELLOW)

    append_info_section(lines, "Frame Type / I-P-B Analysis", Color.MAGENTA)
    frame_summary = analysis.get("frame_analysis")
    if frame_summary:
        total = int(frame_summary.get("total_frames") or 0)
        for label, key in (("I frames", "i_frames"), ("P frames", "p_frames"), ("B frames", "b_frames")):
            count = int(frame_summary.get(key) or 0)
            percent = (count / total * 100.0) if total else 0.0
            append_info_kv(lines, label, f"{count} ({percent:.2f}%)", 1)
        append_info_kv(lines, "total analyzed frames", total, 1)
        append_info_kv(lines, "keyframes", frame_summary.get("keyframes", "unknown"), 1)
        append_info_kv(lines, "average GOP length", frame_summary.get("average_gop_frames") or "unknown", 1)
        append_info_kv(lines, "minimum GOP length", frame_summary.get("minimum_gop_frames") or "unknown", 1)
        append_info_kv(lines, "maximum GOP length", frame_summary.get("maximum_gop_frames") or "unknown", 1)
        append_info_kv(lines, "CSV", frame_summary.get("csv_path", "unknown"), 1)
        append_info_line(lines, "  Very long GOP can improve compression but may reduce seeking accuracy.", Color.GRAY)
        append_info_line(lines, "  More B-frames usually improves compression efficiency.", Color.GRAY)
        append_info_line(lines, "  Frame type distribution is technical information and does not directly prove visual quality.", Color.GRAY)
    else:
        append_info_line(lines, "  Skipped: " + str(analysis.get("frame_analysis_skip") or "frame data was unavailable"), Color.YELLOW)

    append_info_section(lines, "GOP / Keyframe Summary", Color.YELLOW)
    if frame_summary and int(frame_summary.get("keyframes") or 0) >= 2:
        for key in ("first_keyframe_time", "last_keyframe_time", "average_keyframe_interval", "minimum_keyframe_interval", "maximum_keyframe_interval", "average_gop_frames"):
            value = frame_summary.get(key)
            if isinstance(value, (int, float)):
                text = f"{value:.3f} s" if "time" in key or "interval" in key else f"{value:.2f} frames"
            else:
                text = "unknown"
            append_info_kv(lines, key, text, 1)
    else:
        append_info_line(lines, "  GOP statistics are limited because too few keyframes were available.", Color.YELLOW)

    append_info_section(lines, "Color Metadata Extended", Color.LIGHT_BLUE)
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        append_info_line(lines, "  " + media_info_stream_name(stream), Color.BOLD + Color.LIGHT_BLUE)
        for key in ("color_range", "color_space", "color_transfer", "color_primaries", "chroma_location", "pix_fmt", "bits_per_raw_sample", "field_order", "sample_aspect_ratio", "display_aspect_ratio"):
            append_info_kv(lines, key, stream.get(key, "unknown"), 2)
        if stream.get("color_range") in {None, "", "unknown"}:
            append_info_line(lines, "    Color range is not declared in metadata. This does not always mean the actual range is unknown; it means it was not signaled clearly in the file metadata.", Color.YELLOW)

    append_info_section(lines, "Audio Technical Detail", Color.BLUE)
    audio_relative = 0
    for stream in streams:
        if stream.get("codec_type") != "audio":
            continue
        append_info_line(lines, "  " + media_info_stream_name(stream), Color.BOLD + Color.BLUE)
        for key in ("codec_name", "profile", "sample_fmt", "sample_rate", "channels", "channel_layout", "bits_per_raw_sample", "bit_rate"):
            if key == "bit_rate" and stream.get(key) in {None, "", "N/A", "unknown"}:
                continue
            append_info_kv(lines, key, stream.get(key, "unknown"), 2)
        size, estimated = stream_size_bytes(stream, fmt, analysis.get("packet_sizes"), streams)
        bitrate = stream_bitrate_kbps(stream, fmt, analysis.get("packet_sizes"), streams)
        append_info_kv(lines, "computed bitrate", describe_bitrate(bitrate), 2, Color.YELLOW)
        append_info_kv(lines, "size", f"{format_bytes(size)}{' estimated' if estimated else ''}", 2)
        append_info_kv(lines, "language", display_language(media_info_stream_tags(stream).get("language")), 2)
        append_info_kv(lines, "default", media_info_disposition(stream, "default"), 2)
        append_info_kv(lines, "original", media_info_disposition(stream, "original"), 2)
        stats = audio_volume_stats or {}
        append_info_kv(lines, "mean / max volume", audio_mean_max_volume_field(stats, audio_relative), 2, Color.MEAN_VOLUME)
        audio_relative += 1

    append_info_section(lines, "Subtitle and Attachment Detail", Color.ORANGE)
    for stream in streams:
        if stream.get("codec_type") == "subtitle":
            append_info_line(lines, "  " + media_info_stream_name(stream), Color.BOLD + Color.ORANGE)
            append_info_kv(lines, "language", display_language(media_info_stream_tags(stream).get("language")), 2)
            append_info_kv(lines, "title", media_info_stream_tags(stream).get("title") or "unknown", 2)
            append_info_kv(lines, "default", media_info_disposition(stream, "default"), 2)
            append_info_kv(lines, "forced", media_info_disposition(stream, "forced"), 2)
            append_info_kv(lines, "hearing impaired", media_info_disposition(stream, "hearing_impaired"), 2)
            append_info_kv(lines, "subtitle kind", media_info_subtitle_kind(str(stream.get("codec_name") or "")), 2)
        elif stream.get("codec_type") == "attachment":
            tags = media_info_stream_tags(stream)
            append_info_line(lines, "  " + media_info_stream_name(stream), Color.BOLD + Color.PINK)
            append_info_kv(lines, "filename", tags.get("filename") or "unknown", 2)
            append_info_kv(lines, "mimetype", tags.get("mimetype") or tags.get("MIME_TYPE") or "unknown", 2)
            size, estimated = stream_size_bytes(stream, fmt, analysis.get("packet_sizes"), streams)
            append_info_kv(lines, "size", f"{format_bytes(size)}{' estimated' if estimated else ''}", 2)
            append_info_kv(lines, "attachment kind", media_info_attachment_kind(stream), 2)


def build_media_info_report_lines(
    input_path: Path,
    payload: dict[str, Any],
    text_overview: str,
    info_path: Path,
    audio_volume_stats: dict[int, dict[str, str]] | None = None,
    analysis: dict[str, Any] | None = None,
) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    chapters = payload.get("chapters") or []
    programs = payload.get("programs") or []

    append_info_section(lines, "Media Info Report", Color.LIGHT_BLUE)
    append_info_kv(lines, "Generated", datetime.datetime.now().isoformat(timespec="seconds"), 1, Color.WHITE)
    append_info_kv(lines, "Input path", input_path, 1, Color.WHITE)
    append_info_kv(lines, "Normalized path", _safe_resolved_path(input_path), 1, Color.CYAN)
    append_info_kv(lines, "Path exists", "yes" if input_path.exists() else "no", 1, Color.GREEN if input_path.exists() else Color.RED)
    append_info_kv(lines, "File size", format_bytes(input_path.stat().st_size if input_path.exists() else None), 1, Color.LIME)
    append_info_kv(lines, "Duration", format_duration(services.stream_duration_seconds({}, fmt)), 1, Color.MAGENTA)
    append_info_kv(lines, "Total bitrate", describe_total_bitrate(fmt), 1, Color.YELLOW)
    append_info_kv(lines, "Report file", info_path, 1, Color.AQUA)

    append_media_info_advanced_sections(lines, input_path, payload, analysis, audio_volume_stats)

    append_info_section(lines, "Container / Format", Color.CYAN)
    append_nested_info(lines, fmt, 1)

    append_info_section(lines, "Streams", Color.MAGENTA)
    append_info_kv(lines, "Stream count", len(streams), 1, Color.LIGHT_BLUE)
    type_counts: dict[str, int] = {}
    for stream in streams:
        stream_type = str(stream.get("codec_type", "unknown"))
        type_counts[stream_type] = type_counts.get(stream_type, 0) + 1
    if type_counts:
        append_info_kv(lines, "Stream types", ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items())), 1, Color.LIME)
    audio_relative_index = 0
    for relative_index, stream in enumerate(streams):
        stream_type = str(stream.get("codec_type", "unknown"))
        color = {
            "video": Color.MAGENTA,
            "audio": Color.BLUE,
            "subtitle": Color.ORANGE,
            "attachment": Color.PINK,
            "data": Color.YELLOW,
        }.get(stream_type, Color.WHITE)
        append_info_line(lines)
        append_info_line(lines, "  " + info_stream_header(stream, relative_index, len(chapters)), Color.BOLD + color)
        append_info_line(lines, "  " + "-" * 46, Color.GRAY)
        if stream_type == "audio":
            stats = audio_volume_stats or {}
            append_info_kv(lines, "mean / max volume", audio_mean_max_volume_field(stats, audio_relative_index), 2, Color.MEAN_VOLUME)
            audio_relative_index += 1
        append_nested_info(lines, stream, 2)

    append_info_section(lines, "Chapters", Color.ORANGE)
    append_info_kv(lines, "Chapter count", len(chapters), 1, Color.LIGHT_BLUE)
    append_nested_info(lines, chapters, 1)

    append_info_section(lines, "Programs", Color.YELLOW)
    append_info_kv(lines, "Program count", len(programs), 1, Color.LIGHT_BLUE)
    append_nested_info(lines, programs, 1)

    append_info_section(lines, "FFprobe Text Overview", Color.LIME)
    for line in text_overview.splitlines() or ["(empty)"]:
        append_info_line(lines, "  " + line, Color.WHITE)
    return lines


def create_media_info_report(
    ffprobe: str,
    input_path: Path,
    ffmpeg: str | None = None,
    options: MediaInfoOptions | None = None,
) -> MediaInfoReportResult:
    started_at = time.perf_counter()
    options = options or MediaInfoOptions()
    info_path = media_info_report_path(input_path)
    log_info(
        "Media Info report options: "
        f"input={input_path}; deep_analysis={options.deep_analysis}; "
        f"extract_screenshots={options.extract_screenshots}; "
        f"reference_path={options.reference_path if options.reference_path else 'none'}"
    )
    payload = ffprobe_full_json(ffprobe, input_path)
    text_overview = ffprobe_text_overview(ffprobe, input_path)
    sidecars: list[Path] = []
    skipped: list[str] = []
    analysis = build_media_info_analysis(ffprobe, input_path, payload, info_path, options.deep_analysis, sidecars, skipped)
    audio_streams = [stream for stream in (payload.get("streams") or []) if stream.get("codec_type") == "audio"]
    audio_volume_stats: dict[int, dict[str, str]] = {}
    if audio_streams and ffmpeg:
        volume_answers = {
            "ffmpeg": ffmpeg,
            "input_path": input_path,
            "audio_streams": audio_streams,
        }
        audio_volume_stats = services.get_audio_volume_stats(volume_answers)
    metric_sidecars = optional_reference_metrics(ffprobe, ffmpeg, input_path, options.reference_path, payload, info_path, skipped)
    sidecars.extend(metric_sidecars)
    screenshot_dir = optional_extract_screenshots(ffmpeg, input_path, payload, info_path, skipped) if options.extract_screenshots else None
    lines = build_media_info_report_lines(input_path, payload, text_overview, info_path, audio_volume_stats, analysis)
    append_info_section(lines, "Sidecar Files", Color.AQUA)
    display_sidecars = [*sidecars, info_path.with_suffix(".html"), media_info_sidecar_path(info_path, "raw_ffprobe", ".json")]
    for path in display_sidecars:
        append_info_line(lines, "  " + str(path), Color.WHITE)
    if screenshot_dir:
        append_info_line(lines, "  screenshots: " + str(screenshot_dir), Color.WHITE)
    if skipped:
        append_info_section(lines, "Skipped Sections", Color.YELLOW)
        for reason in skipped:
            append_info_line(lines, "  " + reason, Color.YELLOW)
    written_sidecars = write_media_info_report(input_path, lines, payload, text_overview, info_path)
    sidecars.extend(written_sidecars)
    if screenshot_dir:
        log_info(f"Media Info screenshot folder generated: {screenshot_dir}")
    if skipped:
        for reason in skipped:
            log_info(f"Media Info skipped section: {reason}")
    log_info(
        "Media Info generated outputs: "
        f"txt={info_path}; sidecars={[str(path) for path in sidecars]}; "
        f"screenshot_dir={screenshot_dir if screenshot_dir else 'none'}"
    )
    log_info(
        f"Media Info report completed for {input_path} -> {info_path} "
        f"in {time.perf_counter() - started_at:.3f}s"
    )
    return MediaInfoReportResult(lines, info_path, sidecars, screenshot_dir, skipped)


def ask_media_info_options(answers: dict[str, Any], input_path: Path) -> MediaInfoOptions:
    deep_analysis = appio.ask_yes_no(
        media_info_next_prompt(
            answers,
            "Run deep packet/frame analysis?",
            "can be slower on large files; generates bitrate/frame CSV sidecars",
            "n",
        ),
        False,
    )
    reference_path: Path | None = None
    if input_path.is_file() and appio.ask_yes_no(
        media_info_next_prompt(
            answers,
            "Compare this file with a reference/source file for PSNR/SSIM/VMAF?",
            "quality metrics only make sense when both files are aligned and represent the same content",
            "n",
        ),
        False,
    ):
        while True:
            value = appio.ask_required(
                media_info_next_prompt(
                    answers,
                    "Enter reference/source file path",
                    "drag and drop a file or paste a path; example: " + example_text(r"D:\Videos\source.mkv"),
                )
            )
            candidate = terminal_path(value)
            if not candidate.exists() or not candidate.is_file():
                appio.error("Reference file not found. Enter an existing file path.")
                continue
            reference_path = candidate
            break
    screenshot_prompt = "Extract sample screenshots?" if input_path.is_file() else "Extract sample screenshots for each file?"
    extract_screenshots = appio.ask_yes_no(
        media_info_next_prompt(
            answers,
            screenshot_prompt,
            "saves PNG samples at 10%,25%,50%,75%,90% without modifying the video",
            "n",
        ),
        False,
    )
    return MediaInfoOptions(
        deep_analysis=deep_analysis,
        extract_screenshots=extract_screenshots,
        reference_path=reference_path,
    )


def print_media_info_result(result: MediaInfoReportResult, print_lines: bool) -> None:
    if print_lines:
        print()
        print(render_info_report(result.lines, color=True))
    appio.note(f"Media info TXT report written to: {result.info_path}")
    for path in result.sidecar_paths:
        suffix = path.suffix.lower().lstrip(".").upper()
        appio.note(f"Media info {suffix} sidecar written to: {path}")
    if result.screenshot_dir:
        appio.note(f"Media info screenshots written to: {result.screenshot_dir}")
    for reason in result.skipped_sections:
        appio.note(f"Media info skipped section: {reason}")


def run_media_info_mode(base_answers: dict[str, Any]) -> None:
    try:
        answers = dict(base_answers)
        answers["_question_number"] = 1
        input_path = ask_media_info_input_path(answers)
        options = ask_media_info_options(answers, input_path)
        log_info(f"Media Info mode input: {input_path}")
        if input_path.is_file():
            result = create_media_info_report(answers["ffprobe"], input_path, answers.get("ffmpeg"), options)
            print_media_info_result(result, print_lines=False)
            return

        candidates = media_info_folder_candidates(input_path)
        if not candidates:
            appio.error("No files were found in this folder.")
            return
        print()
        print(paint("Media Info folder scan", Color.BOLD + Color.LIGHT_BLUE))
        print("  " + field_text("Folder", input_path, Color.WHITE))
        print("  " + field_text("Files found", len(candidates), Color.LIME))
        print("  " + field_text("Output folder", services.default_media_reports_dir(), Color.AQUA))
        log_info(f"Media Info folder scan: folder={input_path}; files={len(candidates)}")

        written = 0
        skipped = 0
        sidecar_count = 0
        for index, path in enumerate(candidates, start=1):
            try:
                result = create_media_info_report(answers["ffprobe"], path, answers.get("ffmpeg"), options)
            except FFprobeError:
                skipped += 1
                log_debug(f"Media Info skipped unsupported/unreadable file: {path}")
                continue
            except Exception:
                skipped += 1
                log_exception(f"Media Info failed for file: {path}")
                continue
            written += 1
            sidecar_count += len(result.sidecar_paths) + (1 if result.screenshot_dir else 0)
            print(
                f"  {paint(str(index) + '.', Color.LIGHT_BLUE)} "
                f"{paint('wrote', Color.LIME)} {paint(path.name, Color.WHITE)} "
                f"{paint('->', Color.GRAY)} {paint(str(result.info_path), Color.AQUA)}"
            )
            for reason in result.skipped_sections:
                appio.note(f"  skipped section for {path.name}: {reason}")

        print()
        if written:
            appio.note(f"Media info reports written to: {services.default_media_reports_dir()}")
            appio.note(f"Media info sidecar files/folders generated: {sidecar_count}")
        if skipped:
            appio.note(f"Skipped {skipped} unsupported or unreadable file(s). See log file: {_log_file_text()}")
        if not written:
            appio.error("No ffprobe-readable files were found in this folder.")
    except Back:
        appio.note("Returning to main menu.")


def run_mode_steps(answers: dict[str, Any], steps: list[Step]) -> None:
    def visible_question_number(current: int) -> int:
        count = 0
        for pos in range(current + 1):
            if steps[pos].applicable(answers):
                count += 1
        return int(answers.get("_question_offset", 0) or 0) + count

    idx = 0
    while idx < len(steps):
        if not steps[idx].applicable(answers):
            idx += 1
            continue
        try:
            answers["_question_number"] = visible_question_number(idx)
            steps[idx].run(answers)
            idx += 1
        except Back:
            if idx == 0:
                raise
            idx -= 1
            while idx > 0 and (not steps[idx].applicable(answers) or step_is_auto_back_skip(steps[idx], answers)):
                idx -= 1


def run_capability_cache_menu(base_answers: dict[str, Any]) -> None:
    """Diagnostics sub-menu for the FFmpeg capability cache. View / re-probe /
    clear, using the existing 0=Back convention. Never affects user settings,
    logs, or secrets."""
    ffmpeg = base_answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"
    ffprobe = base_answers.get("ffprobe") or shutil.which("ffprobe") or "ffprobe"
    while True:
        print()
        print(paint("FFmpeg capability cache:", Color.BOLD + Color.LIGHT_BLUE))
        print(f"  {paint('1.', Color.LIGHT_BLUE)} View cached capability results")
        print(f"  {paint('2.', Color.LIGHT_BLUE)} Re-probe current encoder/container capabilities")
        print(f"  {paint('3.', Color.LIGHT_BLUE)} Clear capability cache")
        print(f"  {paint('0.', Color.LIGHT_BLUE)} Back")
        value = appio.ask_raw(f"{paint('Selection', Color.BOLD)} {paint('[0]', Color.GREEN)} "
                        f"{back_text('quit=exit')}: ").strip().lower()
        if value in {"", "0", "b", "back"}:
            return
        if value == "1":
            _capability_cache_view(ffmpeg, ffprobe)
        elif value == "2":
            _capability_cache_reprobe(ffmpeg, ffprobe)
        elif value == "3":
            _capability_cache_clear()
        else:
            appio.error("Enter 0, 1, 2, or 3.")


def _capability_cache_reprobe(ffmpeg: str, ffprobe: str) -> None:
    print()
    encoders = ["libx264", "libx265", "h264_nvenc", "hevc_nvenc"]
    containers = ["mp4", "mkv"]
    cache = load_capability_cache()
    for encoder in encoders:
        identity, env_key = services.capability_environment_key(ffmpeg, ffprobe, encoder)
        for ext in containers:
            appio.note("Re-probing %s + %s ..." % (encoder, ext))
            probe = services.probe_color_range_capability(ffmpeg, ffprobe, encoder, ext)
            cap_key = "%s|%s" % (encoder, container_family(ext))
            _store_capability_entry(cache, env_key, identity, cap_key, probe)
            _CAPABILITY_SESSION_MEMO.pop("%s::%s" % (env_key, cap_key), None)
            fr = probe.get("expected_final_range")
            shown = "unspecified" if fr in {"unknown", "", None} else fr
            print("    " + field_text(cap_key, "%s -> %s" % (probe["status"], shown), Color.AQUA))
    save_capability_cache(cache)
    appio.note("Re-probe complete; results cached for the current FFmpeg environment.")


def _capability_cache_clear() -> None:
    # Delete only the exact FFmWiz-owned capability-cache artifacts that the
    # application itself writes: the primary ffmpeg_capabilities.json and the
    # single corrupt-backup the recovery code creates via path.with_suffix(
    # ".corrupt") -> ffmpeg_capabilities.corrupt. Never remove the enclosing
    # .cache directory, ownership markers, or unrelated files.
    path = capability_cache_path()
    owned_files = [path, path.with_suffix(".corrupt")]
    if not any(p.exists() for p in owned_files):
        appio.note("Capability cache is already empty.")
        return
    if not appio.ask_yes_no(yn_prompt("Clear the FFmpeg capability cache?", False), False):
        appio.note("Capability cache not cleared.")
        return
    removed: list[str] = []
    failed: list[str] = []
    for p in owned_files:
        if not p.exists():
            continue  # missing file is a no-op
        try:
            p.unlink()
            removed.append(p.name)
        except OSError as exc:
            failed.append(p.name)
            appio.error("Could not delete capability cache file %s: %s" % (p.name, exc))
    _CAPABILITY_SESSION_MEMO.clear()
    if failed:
        appio.note("Capability cache only partially cleared. Removed: %s. Failed: %s. "
             "The .cache directory, ownership markers, and unrelated files are untouched."
             % (", ".join(removed) or "none", ", ".join(failed)))
    else:
        appio.note("Capability cache cleared (%s). The .cache directory, user settings, logs, "
             "and unrelated files are untouched." % (", ".join(removed) or "nothing"))


def ask_cut_method(answers: dict[str, Any]) -> int:
    """Ask the user how to define cut ranges.

    Returns:
        1 -> enter cut times manually with h:m:s:frame
    """
    print()
    print(paint("Cut video only with copy:", Color.BOLD + Color.LIGHT_BLUE))
    print(f"  {paint('1.', Color.LIGHT_BLUE)} Manual cut using h:m:s:frame {paint('[default]', Color.GREEN)}")
    print()
    while True:
        value = appio.ask_raw(
            f"{paint('Selection', Color.BOLD)} {paint('[1]', Color.GREEN)} "
            f"{back_text('0=back, quit=exit')}: "
        )
        if not value:
            return 1
        if is_back_value(value):
            raise Back()
        if value == "1":
            return int(value)
        if value.lower() in {"g", "gui", "graphical"}:
            appio.error("The standalone Cut GUI is archived. Use manual cut in this mode.")
            continue
        appio.error("Enter 1.")


def print_cut_summary(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
    remove_ranges: list[tuple[float, float]],
    fps: float,
    duration: float,
    mode_label: str,
    output_path: Path,
) -> None:
    print()
    print(paint("Cut summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("Mode", mode_label, Color.CYAN))
    print("  " + field_text("Input", answers["input_path"], Color.WHITE))
    print("  " + field_text("Output", output_path, Color.LIME))
    print("  " + field_text("Detected FPS", f"{fps:.3f}", Color.MAGENTA))
    print("  " + field_text("Source duration", format_duration(duration), Color.MAGENTA))
    print("  " + field_text("Kept duration", format_duration(total_keep_duration(keep_ranges)), Color.GREEN))
    if remove_ranges:
        print(paint(format_cut_ranges_for_summary(remove_ranges, fps, "Removed ranges"), Color.ORANGE))
    print(paint(format_cut_ranges_for_summary(keep_ranges, fps, "Kept ranges"), Color.LIGHT_BLUE))


def ask_continue_default_yes(answers: dict[str, Any]) -> bool:
    """Ask the user 'Continue? [Y/n]'. Default Yes; pressing Enter continues.

    Returns True to continue, False to cancel. Raises Back when the user enters
    '0' so callers can decide how to handle the previous-step navigation.
    Callers MUST wrap this in try/except Back when they want to return to a
    higher-level menu instead of crashing.
    """
    while True:
        value = appio.ask_raw(
            f"{paint('Continue?', Color.BOLD)} {paint('[Y/n]', Color.GREEN)} "
            f"{back_text('0=back, quit=exit')}: "
        )
        if is_back_value(value):
            raise Back()
        if not value:
            return True
        lowered = value.lower()
        if lowered in {"y", "yes"}:
            return True
        if lowered in {"n", "no"}:
            return False
        appio.error("Enter y or n. Default on Enter: Y (continue).")


def run_copy_cut_mode(
    base_answers: dict[str, Any],
) -> tuple[int, float] | None:
    """Top-level driver for main-menu option 3.

    Only Back from the first Copy Cut question bubbles up to the main menu.
    Later questions handle Back locally so 0 moves one step backward.
    """
    try:
        return _run_copy_cut_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_copy_cut_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    answers["_question_offset"] = 1
    answers.pop("cut_keep_ranges", None)

    keep_ranges: list[tuple[float, float]] = []
    output_path: Path | None = None
    fps = 25.0
    duration = 0.0
    stage = 0
    while True:
        if stage == 0:
            try:
                answers["_question_number"] = 1
                wizard.step_input_path(answers)
            except Back:
                raise
            if not answers.get("video_streams"):
                appio.error("Copy cut mode requires a video stream.")
                continue
            fps = services.get_video_fps(answers)
            duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
            stage = 1
            continue

        if stage == 1:
            try:
                answers["_question_number"] = 2
                wizard.step_output_location(answers)
            except Back:
                stage = 0
                continue
            input_path: Path = answers["input_path"]
            answers["output_ext"] = input_path.suffix.lstrip(".") or "mkv"
            stage = 2
            continue

        if stage == 2:
            appio.note(COPY_CUT_WARNING)
            answers["_question_number"] = 3
            try:
                method = ask_cut_method(answers)
            except Back:
                stage = 1
                continue
            if method == 1:
                try:
                    keep_ranges = services.collect_cut_ranges_terminal(answers, fps, duration)
                except Back:
                    appio.note("Returning to the cut-method menu.")
                    continue
            else:
                appio.error("The standalone Cut GUI is archived. Use manual cut in this mode.")
                continue
            if not keep_ranges:
                appio.note("No keep ranges were produced. Returning to the cut-method menu.")
                continue
            stage = 3
            continue

        answers["output_collision_suffix"] = "_cut"
        output_path = services.build_output_path(answers)
        answers["output_path"] = output_path

        remove_ranges = invert_cut_ranges_to_keep_ranges(keep_ranges, duration) if duration > 0 else []
        print_cut_summary(answers, keep_ranges, remove_ranges, fps, duration, "Stream copy", output_path)

        try:
            proceed = ask_continue_default_yes(answers)
        except Back:
            stage = 2
            continue
        if not proceed:
            appio.note("Operation canceled by user.")
            return None
        break

    started_at = time.perf_counter()
    if output_path is None:
        raise RuntimeError("Copy Cut output path was not resolved.")
    return_code = run_copy_cut(answers, keep_ranges, output_path)
    elapsed = time.perf_counter() - started_at
    return return_code, elapsed


def run_folder_settings_wizard(answers: dict[str, Any]) -> None:
    steps = [
        Step("output_format", lambda a: True, wizard.step_output_format),
        Step("video_codec", output_has_video, wizard.step_video_codec),
        Step("use_gpu", output_has_video, wizard.step_use_gpu),
        Step("unified_video_editor", output_has_video, wizard.step_unified_video_editor_for_encode),
        Step("crop_enabled", output_has_video, wizard.step_crop_enabled),
        Step("crop_top", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_top),
        Step("crop_left", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_left),
        Step("crop_right", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_right),
        Step("crop_bottom", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_bottom),
        Step("video_bitrate", video_reencode_options_applicable, wizard.step_video_bitrate),
        Step("nvenc_multipass", nvenc_multipass_prompt_applicable, step_nvenc_multipass),
        Step("resolution", video_reencode_options_applicable, wizard.step_resolution),
        Step("fps", video_reencode_options_applicable, wizard.step_fps),
        Step("video_speed_reverse", output_has_video, wizard.step_video_speed_reverse_for_encode),
        Step("audio_tracks", lambda a: bool(a.get("audio_streams")), step_audio_tracks),
        Step("loudnorm", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_loudnorm),
        Step("audio_cut", audio_only_transform_prompt_applicable, step_audio_cut_for_encode),
        Step("audio_speed_reverse", audio_only_transform_prompt_applicable, step_audio_speed_reverse_for_encode),
        Step("audio_codec", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_audio_codec),
        Step("audio_bitrate", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy" and audio_codec_uses_bitrate(str(a.get("audio_codec") or default_audio_codec_for_ext(a.get("output_ext", "")))), step_audio_bitrate),
        Step("audio_sample_rate", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy", step_audio_sample_rate),
        Step("source_extras", source_extra_policy_applicable, step_source_extra_policy),
        Step("subtitle_tracks", lambda a: output_has_video(a) and source_subtitles_keep_enabled(a) and bool(a.get("subtitle_streams")), step_subtitle_tracks),
        Step("color_range", folder_batch_color_range_applicable, step_folder_batch_color_range),
        Step("start_now", lambda a: True, step_start_folder_now),
    ]

    def next_index(start: int) -> int:
        idx = start
        while idx < len(steps) and not steps[idx].applicable(answers):
            idx += 1
        return idx

    def prev_index(start: int) -> int:
        idx = start
        while idx > 0 and (
            not steps[idx].applicable(answers)
            or is_auto_unified_crop_step(idx)
            or step_is_auto_back_skip(steps[idx], answers)
        ):
            idx -= 1
        return max(0, idx)

    def is_auto_unified_crop_step(pos: int) -> bool:
        return bool(
            answers.get("_unified_video_editor_used")
            and steps[pos].name in {"crop_enabled", "crop_top", "crop_left", "crop_right", "crop_bottom"}
        )

    def question_number(current: int) -> int:
        count = 0
        for pos in range(current + 1):
            if steps[pos].applicable(answers) and not is_auto_unified_crop_step(pos):
                count += 1
        return answers.get("_question_offset", 0) + count

    idx = next_index(0)
    while idx < len(steps):
        try:
            answers["_question_number"] = question_number(idx)
            steps[idx].run(answers)
            apply_unified_video_editor_answers(answers)
            idx = next_index(idx + 1)
        except Back:
            if idx == 0:
                raise
            idx = prev_index(idx - 1)


def run_folder_encode_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_folder_encode_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_folder_encode_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_disable_graphical_editors"] = True
    answers["_folder_encode_mode"] = True
    answers["_quiet_packet_size_probe"] = True
    answers["_question_number"] = 1
    answers["_question_offset"] = 0

    run_folder_input_output_steps(answers)

    folder_path: Path = answers["folder_input_path"]
    output_folder: Path = answers["folder_output_location"]
    items = scan_folder_media_files(base_answers, folder_path, exclude_folder=output_folder)
    if not items:
        appio.error("No supported audio or video files were found in this folder.")
        return None
    print_folder_media_summary(items, base_answers)
    answers["_folder_items"] = items
    appio.note(f"Folder Encode found {len(items)} media file(s). Files will be encoded one at a time.")

    representative = choose_folder_representative(items)
    copy_media_metadata(answers, representative["answers"])
    answers["_folder_representative_path"] = representative["path"]
    answers["_question_offset"] = 2
    while True:
        try:
            run_folder_settings_wizard(answers)
            break
        except Back:
            run_folder_output_step_with_back(answers)
            folder_path = answers["folder_input_path"]
            output_folder = answers["folder_output_location"]
            items = scan_folder_media_files(base_answers, folder_path, exclude_folder=output_folder)
            if not items:
                appio.error("No supported audio or video files were found in this folder.")
                return None
            print_folder_media_summary(items, base_answers)
            answers["_folder_items"] = items
            appio.note(f"Folder Encode found {len(items)} media file(s). Files will be encoded one at a time.")
            representative = choose_folder_representative(items)
            copy_media_metadata(answers, representative["answers"])
            answers["_folder_representative_path"] = representative["path"]

    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The example command above is ready to adapt manually.")
        return None

    output_folder: Path = answers["folder_output_location"]
    try:
        output_folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        appio.error(f"Could not create output folder: {output_folder}. {exc}")
        return 1, 0.0

    started_at = time.perf_counter()
    failures = 0
    completed = 0
    total = len(items)
    for index, item in enumerate(items, start=1):
        input_path: Path = item["path"]
        print()
        print(paint(f"Folder Encode [{index}/{total}]: {input_path.name}", Color.BOLD + Color.LIGHT_BLUE))
        try:
            job_answers = prepare_folder_job_answers(answers, item)
            ensure_color_range_resolved(job_answers, workflow="Folder Encode")
            log_and_warn_pixel_format(job_answers)
            cmd = build_ffmpeg_command(job_answers)
        except Exception as exc:
            failures += 1
            log_exception(f"Folder Encode could not prepare file: {input_path}")
            appio.error(f"Skipped {input_path.name}: {exc}")
            continue

        total_duration = services.stream_duration_seconds({}, job_answers.get("format")) or 0.0
        log_info(
            f"Folder Encode starting {index}/{total}: input={input_path}; "
            f"output={job_answers.get('output_path')}; duration={total_duration or 'unknown'}"
        )
        print(paint("Starting FFmpeg...", Color.GREEN))
        return_code, _elapsed = run_ffmpeg_with_progress(
            cmd,
            total_duration=(total_duration if total_duration > 0 else None),
            label=f"Folder Encode {index}/{total}",
        )
        if return_code == 0:
            completed += 1
            appio.note(f"Finished {input_path.name}")
        else:
            failures += 1
            appio.error(f"Failed {input_path.name} with exit code {return_code}.")

    elapsed = time.perf_counter() - started_at
    print()
    if failures:
        appio.error(f"Folder Encode completed with {completed} success(es) and {failures} failure(s).")
        return 1, elapsed
    appio.note(f"Folder Encode completed successfully: {completed} file(s).")
    return 0, elapsed


def ask_add_files_source_video(answers: dict[str, Any]) -> None:
    while True:
        video_example = example_text('"E:\\Input\\video.mkv"')
        value = appio.ask_required(
            appio.question_prompt(
                answers,
                "Enter source video file path",
                f"drag and drop a video file here or paste a path; example: {video_example}",
            )
        )
        input_path = terminal_path(value)
        if not input_path.exists() or not input_path.is_file():
            appio.error("File not found. Enter the full file path again.")
            continue
        try:
            load_input_metadata(answers, input_path)
        except FFprobeError as exc:
            appio.error(str(exc))
            continue
        except Exception:
            log_exception(f"ffprobe metadata load failed for Add files source video: {input_path}")
            appio.error(f"ffprobe could not read the file. See log file: {_log_file_text()}")
            continue
        if not answers.get("video_streams"):
            appio.error("The first input must contain a video stream.")
            continue
        trackmanager.print_source_info(answers)
        return


def run_add_files_to_video_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_add_files_to_video_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_add_files_to_video_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    ask_add_files_source_video(answers)
    extra_items: list[dict[str, Any]] = []
    while True:
        try:
            extra_items = ask_additional_track_files(answers, extra_items)
        except Back:
            answers["_question_number"] = 1
            ask_add_files_source_video(answers)
            extra_items = []
            continue

        compatibility_errors = add_files_stream_copy_compatibility_errors(answers["input_path"], extra_items)
        if compatibility_errors:
            appio.error("Cannot add these streams without changing container or re-encoding:")
            for message in compatibility_errors:
                appio.error(f"  {message}")
            appio.error("Add files mode keeps the original container and uses stream copy only. No output was created.")
            return None

        output_path = choose_add_files_output_path(answers["input_path"], extra_items)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = build_add_files_to_video_command(
            answers["ffmpeg"],
            answers["input_path"],
            extra_items,
            output_path,
            source_audio_count=len(answers.get("audio_streams") or []),
            source_subtitle_count=len(answers.get("subtitle_streams") or []),
        )
        print_add_files_summary(answers, extra_items, output_path, cmd)
        answers["_question_number"] = len(extra_items) + 3
        try:
            start_now = appio.ask_yes_no(
                appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
                True,
            )
        except Back:
            continue
        if not start_now:
            appio.note("FFmpeg was not started. The command above is ready to run manually.")
            return None
        break

    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    log_info(
        f"Add files to video starting: input={answers['input_path']}; "
        f"output={output_path}; extra_files={len(extra_items)}"
    )
    return run_ffmpeg_with_progress(
        cmd,
        total_duration=(duration if duration > 0 else None),
        label="Add files to video",
    )


def run_extract_stream_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    steps = [
        Step("input_path", lambda a: True, step_extract_stream_input),
        Step("extract_stream_index", lambda a: True, step_extract_stream_index),
        Step("extract_format", lambda a: True, step_extract_stream_format),
        Step("start_now", lambda a: True, step_extract_stream_start_now),
    ]
    try:
        run_mode_steps(answers, steps)
    except Back:
        appio.note("Returning to main menu.")
        return None
    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The plan above is ready to run manually.")
        return None
    jobs = answers["_extract_jobs"]
    ffmpeg = answers["ffmpeg"]
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    total_rc = 0
    ok = 0
    started_at = time.perf_counter()
    for i, job in enumerate(jobs, start=1):
        out = Path(job["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = build_extract_stream_command(ffmpeg, Path(job["path"]), job["stream"], out)
        idx = stream_global_index(job["stream"])
        appio.note(f"[{i}/{len(jobs)}] {Path(job['path']).name} #{idx} -> {out.name}")
        log_info(f"Extract Stream job {i}/{len(jobs)}: input={job['path']}; index={idx}; output={out}")
        duration = services.stream_duration_seconds(job["stream"], job.get("fmt"))
        rc, _elapsed = run_ffmpeg_with_progress(
            cmd,
            total_duration=(duration if duration and duration > 0 else None),
            label="Extract Stream",
        )
        if rc == 0:
            ok += 1
        else:
            total_rc = rc
            appio.error(f"Extraction failed (exit {rc}) for {Path(job['path']).name} #{idx}.")
    elapsed = time.perf_counter() - started_at
    appio.note(f"Extract Stream done: {ok}/{len(jobs)} stream(s) extracted.")
    log_info(f"Extract Stream finished: ok={ok}/{len(jobs)}; elapsed={elapsed:.2f}s")
    return total_rc, elapsed


def ask_hardsub_source_video(answers: dict[str, Any]) -> None:
    while True:
        video_example = example_text(r"E:\Input\video.mkv")
        value = appio.ask_required(
            appio.question_prompt(
                answers,
                "Enter source video file path",
                f"drag and drop a video file here or paste a path; example: {video_example}",
            )
        )
        input_path = terminal_path(value)
        if not input_path.exists() or not input_path.is_file():
            appio.error("File not found. Enter the full file path again.")
            continue
        try:
            load_input_metadata(answers, input_path)
        except FFprobeError as exc:
            appio.error(str(exc))
            continue
        except Exception:
            log_exception(f"ffprobe metadata load failed for Hard Sub source video: {input_path}")
            appio.error(f"ffprobe could not read the file. See log file: {_log_file_text()}")
            continue
        if not answers.get("video_streams"):
            appio.error("The input must contain a video stream.")
            continue
        answers["hardsub_hdr_info"] = video_hdr_dolby_info(answers["video_streams"][0])
        trackmanager.print_source_info(answers)
        return


def run_hardsub_encode_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_hardsub_encode_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_hardsub_encode_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    steps = [
        Step("input_path", lambda a: True, ask_hardsub_source_video),
        Step("output_location", lambda a: True, step_hardsub_output_location),
        Step("output_format", lambda a: True, step_hardsub_output_format),
        Step("hardsub_subtitle", lambda a: True, step_hardsub_subtitle_source),
        Step("hardsub_fontsdir", lambda a: True, step_hardsub_fontsdir),
        Step("video_codec", lambda a: True, step_hardsub_video_codec),
        Step("use_gpu", lambda a: True, step_hardsub_use_gpu),
        Step(
            "nvenc_multipass",
            lambda a: nvenc_multipass_prompt_applicable(a),
            lambda a: ask_nvenc_multipass_if_applicable(a, workflow_name="HardSub", quality_oriented=True),
        ),
        Step("hardsub_quality", lambda a: True, step_hardsub_quality),
        Step("hardsub_hdr", lambda a: True, step_hardsub_hdr_handling),
        Step("hardsub_audio", lambda a: True, step_hardsub_audio_mode),
        Step("hardsub_audio_container", lambda a: True, step_hardsub_audio_container_policy),
        Step("color_range", color_range_prompt_applicable, step_color_range),
        Step("start_now", lambda a: True, step_hardsub_start_now),
    ]

    idx = 0
    while idx < len(steps):
        try:
            answers["_question_number"] = idx + 1
            steps[idx].run(answers)
            idx += 1
        except Back:
            if idx == 0:
                raise
            idx -= 1
            while idx > 0 and step_is_auto_back_skip(steps[idx], answers):
                idx -= 1

    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    log_info(
        f"Hard Sub Encode starting: input={answers['input_path']}; "
        f"output={answers.get('output_path')}; subtitle_source={answers.get('hardsub_subtitle_source')}"
    )
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(duration if duration > 0 else None),
        label="Hard Sub Encode",
    )


def run_video_speed_reverse_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_video_speed_reverse_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_video_speed_reverse_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    steps = [
        Step("input_path", lambda a: True, wizard.step_input_path),
        Step("speed_reverse", lambda a: True, step_video_speed_reverse_options),
        Step("output_location", lambda a: not a.get("_speed_reverse_noop"), wizard.step_output_location),
        Step("output_format", lambda a: not a.get("_speed_reverse_noop"), wizard.step_output_format),
        Step("start_now", lambda a: not a.get("_speed_reverse_noop"), step_video_speed_start_now),
    ]
    while True:
        try:
            run_mode_steps(answers, steps)
            break
        except ValueError as exc:
            appio.error(str(exc))
            return None
    ensure_video_input(answers)
    if answers.get("_speed_reverse_noop"):
        appio.note("Video speed/reverse was not enabled. Returning to the first question.")
        return None
    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    if answers.get("reverse_video"):
        return run_segmented_reverse_video_speed(answers)
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(duration / max(0.001, float(answers.get("speed_factor", 1.0))) if duration > 0 else None),
        label="Video Speed / Reverse",
    )


def run_audio_cut_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_audio_cut_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_audio_cut_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    steps = [
        Step("input_path", lambda a: True, wizard.step_input_path),
        Step("audio_track", lambda a: True, step_audio_track_for_tool),
        Step("output_location", lambda a: True, wizard.step_output_location),
        Step("audio_cut_gui", lambda a: True, step_audio_cut_editor),
        Step("start_now", lambda a: not a.get("_audio_cut_noop"), step_audio_cut_start_now),
    ]
    try:
        run_mode_steps(answers, steps)
    except ValueError as exc:
        appio.error(str(exc))
        return None
    ensure_audio_input(answers)
    if answers.get("_audio_cut_noop"):
        appio.note("Audio cut was canceled. Returning to the first question.")
        return None
    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    total_duration = total_keep_duration(answers.get("audio_keep_ranges") or [])
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(total_duration if total_duration > 0 else None),
        label="Audio Cut",
    )


def run_audio_speed_reverse_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_audio_speed_reverse_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_audio_speed_reverse_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    steps = [
        Step("input_path", lambda a: True, wizard.step_input_path),
        Step("audio_track", lambda a: True, step_audio_track_for_tool),
        Step("speed_reverse", lambda a: True, step_audio_speed_reverse_options),
        Step("output_location", lambda a: not a.get("_speed_reverse_noop"), wizard.step_output_location),
        Step("start_now", lambda a: not a.get("_speed_reverse_noop"), step_audio_speed_start_now),
    ]
    try:
        run_mode_steps(answers, steps)
    except ValueError as exc:
        appio.error(str(exc))
        return None
    ensure_audio_input(answers)
    if answers.get("_speed_reverse_noop"):
        appio.note("Audio speed/reverse was not enabled. Returning to the first question.")
        return None
    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(duration / max(0.001, float(answers.get("speed_factor", 1.0))) if duration > 0 else None),
        label="Audio Speed / Reverse",
    )


def run_audio_transform_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_audio_transform_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_audio_transform_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    steps = [
        Step("input_path", lambda a: True, wizard.step_input_path),
        Step("audio_track", lambda a: True, step_audio_track_for_tool),
        Step("output_location", lambda a: True, wizard.step_output_location),
        Step("audio_transform_gui", lambda a: True, step_audio_transform_editor),
        Step("start_now", lambda a: not a.get("_audio_transform_noop"), step_audio_transform_start_now),
    ]
    try:
        run_mode_steps(answers, steps)
    except ValueError as exc:
        appio.error(str(exc))
        return None
    ensure_audio_input(answers)
    if answers.get("_audio_transform_noop"):
        appio.note("Audio transform was not enabled. Returning to the first question.")
        return None
    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    keep_duration = total_keep_duration(answers.get("audio_cut_keep_ranges") or [])
    if keep_duration <= 0:
        keep_duration = duration
    speed = max(0.001, float(answers.get("audio_speed_factor", DEFAULT_SPEED_FACTOR) or DEFAULT_SPEED_FACTOR))
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(keep_duration / speed if keep_duration > 0 else None),
        label="Audio Cut / Speed / Reverse",
    )


def build_join_near_quality_command(answers: dict[str, Any], items: list[dict[str, Any]], output_path: Path) -> list[str]:
    output_path = resolve_output_collision_against_inputs(
        output_path,
        [Path(item["path"]) for item in items if item.get("path")],
        answers.get("output_collision_suffix", "_Final"),
    )
    answers["output_path"] = output_path
    first_video = items[0]["video_streams"][0]
    format_answers = dict(answers)
    format_answers["video_streams"] = [first_video]
    output_pix_fmt = cpu_pixel_format_for_output(format_answers)
    nvenc_pix_fmt = "p010le" if output_video_bit_depth(format_answers) > 8 else "yuv420p"
    target_depth = output_video_bit_depth(format_answers)
    target_w = int(first_video.get("width") or 1280)
    target_h = int(first_video.get("height") or 720)
    target_fps = rational_to_float(first_video.get("avg_frame_rate")) or rational_to_float(first_video.get("r_frame_rate")) or 30.0
    available_video_encoders = {str(name).lower() for name in answers.get("video_encoders") or []}
    use_nvenc_encode = "h264_nvenc" in available_video_encoders and target_depth <= 10
    use_cuda_decode_complex = bool(answers.get("use_gpu") and use_nvenc_encode)
    cmd: list[str] = [answers["ffmpeg"], "-hide_banner", "-y" if OVERWRITE_OUTPUT else "-n"]
    for item in items:
        if use_cuda_decode_complex:
            append_cuda_decode_args_for_input(cmd, answers)
        cmd.extend(["-i", str(item["path"])])
    filters: list[str] = []
    inputs: list[str] = []
    any_audio = any(item.get("audio_streams") for item in items)
    # VFR join re-encode: omit the per-input fps= filter (which would force CFR)
    # and let the output keep variable timing via -fps_mode vfr.
    vfr_join = bool(answers.get("join_vfr"))
    join_rate = join_target_sample_rate(answers)
    prep = join_audio_prep_filter(join_rate)
    for idx, item in enumerate(items):
        fps_prefix = "" if vfr_join else f"fps={target_fps:g},"
        filters.append(
            f"[{idx}:v:0]{fps_prefix}"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:reset_sar=1,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,"
            f"format={output_pix_fmt},setpts=PTS-STARTPTS[v{idx}]"
        )
        inputs.append(f"[v{idx}]")
        if any_audio and item.get("audio_streams"):
            filters.append(f"[{idx}:a:0]{prep}[a{idx}]")
            inputs.append(f"[a{idx}]")
        elif any_audio:
            duration = max(0.001, float(item.get("duration") or 0.001))
            filters.append(f"anullsrc=channel_layout=stereo:sample_rate={join_rate}:d={duration:.6f}[a{idx}]")
            inputs.append(f"[a{idx}]")
    filters.append(f"{''.join(inputs)}concat=n={len(items)}:v=1:a={1 if any_audio else 0}[v]{'[a]' if any_audio else ''}")
    cmd.extend(["-filter_complex", ";".join(filters), "-map", "[v]"])
    if any_audio:
        cmd.extend(["-map", "[a]"])
    else:
        cmd.append("-an")
    if use_nvenc_encode:
        log_info("Join Videos near-quality encode selected h264_nvenc because NVENC is available.")
        cmd.extend([
            "-c:v", "h264_nvenc",
            "-preset", NVENC_PRESET,
            "-tune", NVENC_TUNE,
            "-rc", "constqp",
        ])
        append_nvenc_multipass_args(cmd, answers, "h264_nvenc")
        cmd.extend([
            "-qp", "18",
            "-pix_fmt", nvenc_pix_fmt,
        ])
    elif target_depth > 10:
        cmd.extend(["-c:v", "libx265", "-preset", "slow", "-crf", "18", "-pix_fmt", output_pix_fmt])
        cmd.extend(["-profile:v", hevc_profile_for_output(format_answers, "main")])
    else:
        cmd.extend(["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", output_pix_fmt])
    if any_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ac", "2"])
    if vfr_join:
        # Preserve variable timing across segments instead of resampling to CFR.
        cmd.extend(["-fps_mode", "vfr"])
    if output_path.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(str(output_path))
    return cmd


def run_join_videos_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    items: list[dict[str, Any]] = []
    try:
        need_file = True
        while True:
            if need_file:
                answers["_question_number"] = len(items) + 1
                label = "Enter first media file path (audio or video)" if not items else "Enter another media file path"
                hint = "drag and drop an audio or video file here or paste a path"
                if items:
                    hint += "; b=re-enter previous file"
                value = appio.ask_raw(
                    appio.question_prompt(answers, label, hint, back="back=0, quit=exit")
                )
                if is_back_value(value):
                    raise Back()
                if value.lower() in {"b", "back"}:
                    if items:
                        removed = items.pop()
                        appio.note(f"Removed previous file: {Path(removed['path']).name}. Re-enter it.")
                        continue
                    raise Back()
                if not value:
                    appio.error("This value cannot be empty. Enter a file path.")
                    continue
                path = terminal_path(value)
                if not path.exists() or not path.is_file():
                    appio.error("File not found. Enter the full file path again.")
                    continue
                if any(paths_same(path, it["path"]) for it in items):
                    appio.error("This file is already selected. Enter a different file.")
                    continue
                try:
                    item = services.join_load_media_item(answers, path, allow_audio_only=True)
                except Exception as exc:
                    log_exception(f"Join media probe failed: {path}")
                    appio.error(str(exc))
                    continue
                items.append(item)
            if len(items) >= 2:
                answers["_question_number"] = len(items) + 1
                more = wizard.ask_join_add_another(
                    appio.question_prompt(answers, "Add another media file?", "y/n", "n", back=JOIN_ADD_ANOTHER_BACK)
                )
                if more is False:
                    break
                if more == "folder":
                    answers["_question_number"] = len(items) + 1
                    folder = ask_join_folder_path(answers)
                    if folder is not None:
                        join_add_folder_items(answers, folder, items)
                    need_file = False
                    continue
                need_file = True
                continue
            need_file = True

        print_join_order_list([it["path"] for it in items])
        output_answers = dict(answers)
        output_answers["input_path"] = items[0]["path"]
        output_answers["probe"] = items[0]["probe"]
        output_answers["format"] = items[0]["format"]
        output_answers["video_streams"] = items[0]["video_streams"]
        output_answers["audio_streams"] = items[0]["audio_streams"]
        output_answers["subtitle_streams"] = [stream for stream in items[0].get("streams", []) if stream.get("codec_type") == "subtitle"]
        output_answers["attachment_streams"] = [stream for stream in items[0].get("streams", []) if stream.get("codec_type") == "attachment"]
        output_answers["data_streams"] = [stream for stream in items[0].get("streams", []) if stream.get("codec_type") == "data"]
        output_answers["join_input_items"] = items[1:]
        wizard.step_output_location(output_answers)
        answers.update({key: output_answers[key] for key in ("output_location", "output_name_stem", "output_used_default") if key in output_answers})
    except Back:
        appio.note("Returning to main menu.")
        return None

    output_path = join_default_output_path(answers, items[0]["path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Auto-detect whether this is an audio-only join or a video join.
    has_video_items = [it for it in items if it.get("video_streams")]
    audio_only_items = [it for it in items if not it.get("video_streams")]
    if has_video_items and audio_only_items:
        appio.error(
            "Cannot mix audio-only and video inputs in one join. "
            f"Audio-only: {', '.join(Path(it['path']).name for it in audio_only_items)}. "
            "Select all video files, or all audio files."
        )
        return None
    audio_only_join = not has_video_items
    if audio_only_join:
        appio.note("Detected audio-only inputs: performing an audio join.")

    # Variable frame rate policy: when joining videos with different frame rates,
    # ask whether to unify them (then ask the target fps) or keep them variable.
    if not audio_only_join:
        try:
            ask_join_frame_rate_policy(answers, items)
        except Back:
            appio.note("Returning to main menu.")
            return None

    copy_compatible, reasons = join_copy_compatibility(items)
    # A VFR join whose inputs match on everything except frame rate can be joined
    # with the concat demuxer (stream copy), which preserves each segment's own
    # frame rate and yields a genuine variable-frame-rate file with no re-encode.
    if (
        not copy_compatible
        and answers.get("join_vfr")
        and not audio_only_join
        and join_copy_compatible_except_fps(items)
    ):
        copy_compatible = True
        reasons = []
        appio.note("VFR join: using stream copy (concat) to preserve each file's frame rate.")
    print_join_summary(items, copy_compatible, reasons)
    if copy_compatible:
        cmd = build_join_copy_command(answers, items, output_path)
    elif audio_only_join:
        appio.note("These audio files cannot be joined with stream copy. Re-encoding to AAC is required.")
        # Default the audio bitrate to the highest known source among inputs.
        candidates: list[int] = []
        for item in items:
            astreams = item.get("audio_streams") or []
            if astreams:
                value = stream_bitrate_kbps(astreams[0], item.get("format"), services.get_packet_sizes(join_item_answers(answers, item)))
                if value:
                    candidates.append(int(value))
        answers["audio_bitrate_kbps"] = max(candidates) if candidates else DEFAULT_AUDIO_BITRATE_KBPS
        cmd = build_join_audio_encode_command(answers, items, output_path)
    else:
        appio.note("These files cannot be safely joined with stream copy. Re-encoding is required.")
        use_near = appio.ask_yes_no(
            appio.question_prompt(
                answers,
                "Encode with closest possible quality to the inputs?",
                "y/n",
                "y",
            ),
            True,
        )
        if not use_near:
            appio.note("Join was canceled before encoding.")
            return None
        first_video = items[0]["video_streams"][0]
        format_answers = dict(answers)
        format_answers["video_streams"] = [first_video]
        target_depth = output_video_bit_depth(format_answers)
        available_video_encoders = {str(name).lower() for name in answers.get("video_encoders") or []}
        if "h264_nvenc" in available_video_encoders and target_depth <= 10:
            ask_nvenc_multipass_if_applicable(
                answers,
                video_encoder="h264_nvenc",
                workflow_name="Join Videos near-quality",
                quality_oriented=True,
            )
        else:
            set_nvenc_multipass_skip_reason(answers, "CPU encoder selected")
        cmd = build_join_near_quality_command(answers, items, output_path)
    output_path = Path(answers.get("output_path") or output_path)
    answers["output_path"] = output_path
    log_info(f"Join command: {command_to_powershell(cmd)}")
    print()
    print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    start_now = appio.ask_yes_no(appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"), True)
    if not start_now:
        appio.note("FFmpeg was not started. The command above is ready to run manually.")
        cleanup_join_concat_list(answers)
        return None
    total_duration = sum(float(item.get("duration") or 0.0) for item in items)
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    try:
        return run_ffmpeg_with_progress(cmd, total_duration=(total_duration if total_duration > 0 else None), label="Join")
    finally:
        cleanup_join_concat_list(answers)


__all__ = [
    'append_info_kv',
    'append_media_info_advanced_sections',
    'append_media_info_table',
    'append_nested_info',
    'ask_add_files_source_video',
    'ask_continue_default_yes',
    'ask_cut_method',
    'ask_hardsub_source_video',
    'ask_media_info_options',
    'build_join_near_quality_command',
    'build_media_info_report_lines',
    'create_media_info_report',
    'media_info_color_for_key',
    'print_cut_summary',
    'print_media_info_result',
    'run_add_files_to_video_mode',
    'run_audio_cut_mode',
    'run_audio_speed_reverse_mode',
    'run_audio_transform_mode',
    'run_capability_cache_menu',
    'run_copy_cut_mode',
    'run_extract_stream_mode',
    'run_folder_encode_mode',
    'run_folder_settings_wizard',
    'run_hardsub_encode_mode',
    'run_join_videos_mode',
    'run_media_info_mode',
    'run_mode_steps',
    'run_video_speed_reverse_mode',
    '_capability_cache_clear',
    '_capability_cache_reprobe',
    '_run_add_files_to_video_mode_impl',
    '_run_audio_cut_mode_impl',
    '_run_audio_speed_reverse_mode_impl',
    '_run_audio_transform_mode_impl',
    '_run_copy_cut_mode_impl',
    '_run_folder_encode_mode_impl',
    '_run_hardsub_encode_mode_impl',
    '_run_video_speed_reverse_mode_impl',
    'MEDIA_INFO_VALUE_COLORS',
    'MediaInfoOptions',
    'MediaInfoReportResult',
]
