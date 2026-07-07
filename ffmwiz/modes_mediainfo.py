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


__all__ = [
    'append_info_kv',
    'append_media_info_advanced_sections',
    'append_media_info_table',
    'append_nested_info',
    'ask_media_info_options',
    'build_media_info_report_lines',
    'create_media_info_report',
    'media_info_color_for_key',
    'print_media_info_result',
    'run_media_info_mode',
    'MediaInfoOptions',
    'MediaInfoReportResult',
]
