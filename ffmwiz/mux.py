"""FFmWiz mux cluster (extracted from FFmWiz.py, method الف)."""
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
from ffmwiz.modes import *  # noqa: F401,F403
from ffmwiz import modes  # noqa: F401
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


MUX_LANGUAGE_COLORS = (
    Color.GREEN,
    Color.CYAN,
    Color.MAGENTA,
    Color.YELLOW,
    Color.BLUE,
    Color.ORANGE,
    Color.MUX_GOLD,
    Color.LIME,
    Color.MUX_MINT,
    Color.MUX_EMERALD,
    Color.MUX_TEAL,
    Color.MUX_AQUA,
    Color.MUX_SKY,
    Color.MUX_AZURE,
    Color.MUX_INDIGO,
    Color.MUX_VIOLET,
    Color.MUX_PURPLE,
    Color.MUX_LAVENDER,
    Color.PINK,
    Color.MUX_ROSE,
)


@dataclass
class MuxStreamInfo:
    index: int
    codec_type: str
    codec_name: str = ""
    language: str = "unknown"
    title: str = ""
    channels: int | None = None
    sample_rate: str = ""
    channel_layout: str = ""
    disposition_default: int = 0
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    duration: float | None = None
    bitrate_kbps: int | None = None
    size_bytes: int | None = None
    size_estimated: bool = False
    bit_depth: int | None = None
    color_range: str = ""
    max_volume: str = ""
    mean_volume: str = ""

    @classmethod
    def from_ffprobe(
        cls,
        raw: dict[str, Any],
        fmt: dict[str, Any] | None = None,
        packet_sizes: dict[int, int] | None = None,
        volume_stats: dict[str, str] | None = None,
        sibling_streams: list[dict[str, Any]] | None = None,
    ) -> "MuxStreamInfo":
        tags = raw.get("tags") or {}
        disposition = raw.get("disposition") or {}
        size, estimated = stream_size_bytes(raw, fmt, packet_sizes, sibling_streams)
        return cls(
            index=int(raw.get("index", -1)),
            codec_type=str(raw.get("codec_type", "")),
            codec_name=str(raw.get("codec_name", "")),
            language=display_language(tags.get("language")),
            title=str(tags.get("title") or ""),
            channels=raw.get("channels"),
            sample_rate=stream_metadata_value(raw, "sample_rate", ""),
            channel_layout=stream_metadata_value(raw, "channel_layout", ""),
            disposition_default=int(disposition.get("default", 0) or 0),
            width=raw.get("width"),
            height=raw.get("height"),
            fps=rational_to_float(raw.get("avg_frame_rate")),
            duration=services.stream_duration_seconds(raw, fmt),
            bitrate_kbps=stream_bitrate_kbps(raw, fmt, packet_sizes, sibling_streams),
            size_bytes=size,
            size_estimated=estimated,
            bit_depth=video_bit_depth(raw),
            color_range=str(raw.get("color_range") or "unknown"),
            max_volume=(volume_stats or {}).get("max_volume", ""),
            mean_volume=(volume_stats or {}).get("mean_volume", ""),
        )


@dataclass
class MuxMediaFile:
    path: Path
    streams: list[MuxStreamInfo]
    format: dict[str, Any]
    chapter_count: int = 0

    @property
    def video_streams(self) -> list[MuxStreamInfo]:
        return [stream for stream in self.streams if stream.codec_type == "video"]

    @property
    def audio_streams(self) -> list[MuxStreamInfo]:
        return [stream for stream in self.streams if stream.codec_type == "audio"]

    @property
    def subtitle_streams(self) -> list[MuxStreamInfo]:
        return [stream for stream in self.streams if stream.codec_type == "subtitle"]

    @property
    def attachment_streams(self) -> list[MuxStreamInfo]:
        return [stream for stream in self.streams if stream.codec_type == "attachment"]


@dataclass
class MuxStreamMetadataEdit:
    codec_type: str
    match_indexes: list[int] = field(default_factory=list)
    match_languages: list[str] = field(default_factory=list)
    language: str = ""
    title: str = ""


@dataclass
class MuxCleanupRules:
    audio_mode: str
    audio_languages: list[str]
    audio_titles: list[str]
    audio_indexes: list[int]
    subtitle_mode: str
    subtitle_languages: list[str]
    subtitle_titles: list[str]
    subtitle_indexes: list[int]
    keep_attachments: bool
    keep_metadata: bool
    keep_chapters: bool
    overwrite: bool
    copy_non_video_files: bool = True
    selection_style: str = "advanced"
    metadata_edits: list[MuxStreamMetadataEdit] = field(default_factory=list)


def mux_probe_file(ffprobe: str, path: Path, ffmpeg: str | None = None) -> MuxMediaFile | None:
    try:
        payload = services.ffprobe_json(ffprobe, path)
    except FFprobeError:
        log_debug(f"Stream Cleanup Remux skipped unreadable file: {path}")
        return None
    except Exception:
        log_exception(f"Stream Cleanup Remux probe failed: {path}")
        return None
    fmt = payload.get("format", {})
    raw_streams = payload.get("streams", [])
    packet_sizes: dict[int, int] = {}
    probe_answers = {
        "input_path": path,
        "format": fmt,
        "video_streams": [stream for stream in raw_streams if stream.get("codec_type") == "video"],
        "audio_streams": [stream for stream in raw_streams if stream.get("codec_type") == "audio"],
    }
    if packet_size_probe_needed(probe_answers):
        started_at = time.perf_counter()
        packet_sizes = services.probe_packet_sizes(ffprobe, path)
        log_debug(
            f"Stream Cleanup packet-size probe: {path}; "
            f"streams={len(packet_sizes)}; elapsed={time.perf_counter() - started_at:.3f}s"
        )
    audio_streams = [stream for stream in raw_streams if stream.get("codec_type") == "audio"]
    audio_volume_stats: dict[int, dict[str, str]] = {}
    if audio_streams and ffmpeg:
        audio_volume_stats = services.get_audio_volume_stats({
            "ffmpeg": ffmpeg,
            "input_path": path,
            "audio_streams": audio_streams,
        })
    audio_relative_index = 0
    streams: list[MuxStreamInfo] = []
    for stream in raw_streams:
        stats = None
        if stream.get("codec_type") == "audio":
            stats = audio_volume_stats.get(audio_relative_index)
            audio_relative_index += 1
        streams.append(MuxStreamInfo.from_ffprobe(stream, fmt, packet_sizes, stats, raw_streams))
    media = MuxMediaFile(path=path, streams=streams, format=fmt, chapter_count=len(payload.get("chapters") or []))
    log_debug(
        f"Stream Cleanup Remux probe OK: {path}; "
        f"video={len(media.video_streams)} audio={len(media.audio_streams)} "
        f"subtitle={len(media.subtitle_streams)} attachments={len(media.attachment_streams)}"
    )
    return media


def mux_scan_files(ffprobe: str, files: list[Path], ffmpeg: str | None = None) -> list[MuxMediaFile]:
    media_files: list[MuxMediaFile] = []
    log_info(f"Stream Cleanup scan started: files={len(files)}")
    for index, path in enumerate(files, start=1):
        print(
            f"{paint('[' + str(index) + '/' + str(len(files)) + ']', Color.MUX_GOLD)} "
            f"{paint('Scanning:', Color.MUX_SCAN_HEADER)} {paint(path.name, Color.WHITE)}"
        )
        media = mux_probe_file(ffprobe, path, ffmpeg)
        if media is not None:
            media_files.append(media)
        else:
            appio.note(f"Skipped unreadable file: {path.name}. See log file: {_log_file_text()}")
    log_info(f"Stream Cleanup scan complete: ok={len(media_files)}/{len(files)}")
    return media_files


def mux_format_stream(stream: MuxStreamInfo, fmt: dict[str, Any] | None = None, chapter_count: int = 0) -> str:
    type_color = {
        "audio": Color.BOLD + Color.MUX_AZURE,
        "subtitle": Color.BOLD + Color.MUX_VIOLET,
        "video": Color.MAGENTA,
        "attachment": Color.PINK,
    }.get(stream.codec_type, Color.WHITE)
    parts = [
        mux_pair_text("index", stream.index, Color.BOLD + Color.MUX_GOLD),
        mux_pair_text("type", stream.codec_type, type_color),
        mux_pair_text("lang", display_language(stream.language), mux_language_color(stream.language)),
        mux_pair_text("title", stream.title or "-", Color.MUX_SKY),
        mux_pair_text("codec", stream.codec_name or "-", Color.MUX_MINT),
    ]
    if stream.codec_type == "video":
        if stream.width and stream.height:
            parts.append(mux_pair_text("size", f"{stream.width}x{stream.height}", Color.LIME))
        if stream.fps:
            parts.append(mux_pair_text("fps", format(stream.fps, ".3g"), Color.MAGENTA))
        parts.append(mux_pair_text("bit depth", f"{stream.bit_depth}-bit" if stream.bit_depth else "unknown", Color.PINK))
        parts.append(mux_pair_text("Color range", display_color_range(stream.color_range), Color.COLOR_RANGE_VALUE))
        duration = stream.duration if stream.duration is not None else services.stream_duration_seconds({}, fmt)
        parts.append(mux_pair_text("duration", format_duration(duration), Color.MAGENTA))
        parts.append(mux_pair_text("bitrate", describe_bitrate(stream.bitrate_kbps), Color.YELLOW))
        estimate_label = " approx" if stream.size_estimated and stream.size_bytes else ""
        parts.append(mux_pair_text("video-only size", format_bytes(stream.size_bytes) + estimate_label, Color.GREEN))
        chapters_value = "yes" if chapter_count else "no"
        chapters_color = Color.CHAPTERS_YES if chapter_count else Color.CHAPTERS_NO
        parts.append(mux_pair_text("chapters", chapters_value, chapters_color))
    if stream.codec_type == "audio":
        if stream.channels is not None:
            parts.append(mux_pair_text("channels", stream.channels, Color.ORANGE))
        if stream.channel_layout:
            parts.append(mux_pair_text("layout", stream.channel_layout, Color.WHITE))
        if stream.sample_rate:
            parts.append(mux_pair_text("sample_rate", stream.sample_rate, Color.MAGENTA))
        duration = stream.duration if stream.duration is not None else services.stream_duration_seconds({}, fmt)
        parts.append(mux_pair_text("duration", format_duration(duration), Color.MAGENTA))
        parts.append(mux_pair_text("bitrate", describe_bitrate(stream.bitrate_kbps), Color.YELLOW))
        stats = {0: {"mean_volume": stream.mean_volume or "unknown", "max_volume": stream.max_volume or "unknown"}}
        parts.append(mux_pair_text("mean / max volume", audio_mean_max_volume_field(stats, 0), Color.MEAN_VOLUME))
        estimate_label = " approx" if stream.size_estimated and stream.size_bytes else ""
        parts.append(mux_pair_text("track size", format_bytes(stream.size_bytes) + estimate_label, Color.MUX_SILVER))
    elif stream.codec_type == "subtitle":
        duration = stream.duration if stream.duration is not None else services.stream_duration_seconds({}, fmt)
        parts.append(mux_pair_text("duration", format_duration(duration), Color.MAGENTA))
    default_value = "yes" if stream.disposition_default else "no"
    parts.append(mux_pair_text("default", default_value, Color.BOLD + Color.GREEN if stream.disposition_default else Color.GRAY))
    return " | ".join(parts)


def mux_print_scan_report(media_files: list[MuxMediaFile], input_root: Path) -> None:
    mux_print_header("Stream Cleanup Scan Report", Color.MUX_SCAN_HEADER)
    for index, media in enumerate(media_files, start=1):
        print()
        if index > 1:
            print(mux_separator_line(Color.MUX_SEPARATOR))
        print(
            f"{paint('File:', Color.MUX_FILE_LINE)} "
            f"{paint(str(mux_display_path(input_root, media.path)), Color.MUX_FILE_LINE)} | "
            f"{mux_pair_text('duration', format_duration(services.stream_duration_seconds({}, media.format)), Color.MAGENTA)} | "
            f"{mux_pair_text('total bitrate', describe_total_bitrate(media.format), Color.YELLOW)}"
        )
        if media.video_streams:
            print(paint("  Video:", Color.BOLD + Color.MAGENTA))
            for stream in media.video_streams:
                print("    " + mux_format_stream(stream, media.format, media.chapter_count))
        else:
            print(paint("  Video: none", Color.GRAY))
        if media.audio_streams:
            print(paint("  Audio:", Color.BOLD + Color.MUX_AZURE))
            for stream in media.audio_streams:
                print("    " + mux_format_stream(stream, media.format))
        else:
            print(paint("  Audio: none", Color.GRAY))
        if media.subtitle_streams:
            print(paint("  Subtitles:", Color.BOLD + Color.MUX_VIOLET))
            for stream in media.subtitle_streams:
                print("    " + mux_format_stream(stream, media.format))
        else:
            print(paint("  Subtitles: none", Color.GRAY))
        if media.attachment_streams:
            print(paint(f"  Attachments: {len(media.attachment_streams)}", Color.PINK))


def mux_unique_stream_values(media_files: list[MuxMediaFile], codec_type: str, field: str) -> list[str]:
    values: list[str] = []
    for media in media_files:
        streams = media.audio_streams if codec_type == "audio" else media.subtitle_streams
        for stream in streams:
            value = getattr(stream, field, "")
            if value and value not in values:
                values.append(value)
    return sorted(values, key=str.lower)


def mux_stream_indexes(media_files: list[MuxMediaFile], codec_type: str) -> list[int]:
    indexes = {
        stream.index
        for media in media_files
        for stream in (media.audio_streams if codec_type == "audio" else media.subtitle_streams)
    }
    return sorted(indexes)


def mux_streams_for_type(media_files: list[MuxMediaFile], codec_type: str) -> list[MuxStreamInfo]:
    return [
        stream
        for media in media_files
        for stream in media.streams
        if stream.codec_type == codec_type
    ]


def mux_selected_audio_streams(media: MuxMediaFile, rules: MuxCleanupRules) -> list[MuxStreamInfo]:
    if rules.audio_mode == "1":
        wanted = {mux_normalize_language(value) for value in rules.audio_languages}
        return [stream for stream in media.audio_streams if mux_normalize_language(stream.language) in wanted]
    if rules.audio_mode == "2":
        return [stream for stream in media.audio_streams if mux_text_matches_any(stream.title, rules.audio_titles)]
    if rules.audio_mode == "3":
        return [stream for stream in media.audio_streams if stream.index in rules.audio_indexes]
    if rules.audio_mode == "4":
        return media.audio_streams
    return []


def mux_selected_subtitle_streams(media: MuxMediaFile, rules: MuxCleanupRules) -> list[MuxStreamInfo]:
    if rules.subtitle_mode == "2":
        wanted = {mux_normalize_language(value) for value in rules.subtitle_languages}
        return [stream for stream in media.subtitle_streams if mux_normalize_language(stream.language) in wanted]
    if rules.subtitle_mode == "3":
        return [stream for stream in media.subtitle_streams if mux_text_matches_any(stream.title, rules.subtitle_titles)]
    if rules.subtitle_mode == "4":
        return [stream for stream in media.subtitle_streams if stream.index in rules.subtitle_indexes]
    if rules.subtitle_mode == "5":
        return media.subtitle_streams
    return []


def mux_kept_streams_for_metadata(
    media_files: list[MuxMediaFile],
    codec_type: str,
    rules: MuxCleanupRules | None,
) -> list[MuxStreamInfo]:
    if rules is None:
        return mux_streams_for_type(media_files, codec_type)
    kept: list[MuxStreamInfo] = []
    for media in media_files:
        if codec_type == "audio":
            kept.extend(mux_selected_audio_streams(media, rules))
        elif codec_type == "subtitle":
            kept.extend(mux_selected_subtitle_streams(media, rules))
    return kept


def mux_kept_languages_for_metadata(
    media_files: list[MuxMediaFile],
    codec_type: str,
    rules: MuxCleanupRules | None,
) -> list[str]:
    return sorted({mux_normalize_language(stream.language) for stream in mux_kept_streams_for_metadata(media_files, codec_type, rules)})


def mux_kept_indexes_for_metadata(
    media_files: list[MuxMediaFile],
    codec_type: str,
    rules: MuxCleanupRules | None,
) -> list[int]:
    return sorted({stream.index for stream in mux_kept_streams_for_metadata(media_files, codec_type, rules)})


def mux_metadata_edit_match_text(edit: MuxStreamMetadataEdit) -> str:
    if edit.match_indexes:
        return "indexes=" + ",".join(str(index) for index in edit.match_indexes)
    if edit.match_languages:
        return "languages=" + ",".join(display_language(value) for value in edit.match_languages)
    return "all"


def mux_metadata_edit_change_text(edit: MuxStreamMetadataEdit) -> str:
    changes: list[str] = []
    if edit.language:
        changes.append(f"language={display_language(edit.language)}")
    if edit.title:
        changes.append(f"title={edit.title}")
    return ", ".join(changes) if changes else "no changes"


def mux_format_metadata_edit(edit: MuxStreamMetadataEdit) -> str:
    return f"{edit.codec_type} {mux_metadata_edit_match_text(edit)} -> {mux_metadata_edit_change_text(edit)}"


def mux_format_metadata_edits(edits: list[MuxStreamMetadataEdit]) -> str:
    return "; ".join(mux_format_metadata_edit(edit) for edit in edits) if edits else "none"


def mux_language_color(language: str) -> str:
    normalized = mux_normalize_language(language)
    if normalized == "unknown":
        return Color.MUX_UNKNOWN_LANGUAGE
    return MUX_LANGUAGE_COLORS[sum(ord(ch) for ch in normalized) % len(MUX_LANGUAGE_COLORS)]


def mux_print_unique_summary(media_files: list[MuxMediaFile]) -> None:
    mux_print_header("Unique Stream Summary", Color.MUX_SUMMARY_HEADER, "-")
    for codec_type, color_code in (("audio", Color.MUX_AUDIO), ("subtitle", Color.MUX_SUBTITLE)):
        print(paint(codec_type.capitalize() + " streams found:", Color.BOLD + color_code))
        summary: dict[tuple[str, str, str], int] = {}
        for media in media_files:
            streams = media.audio_streams if codec_type == "audio" else media.subtitle_streams
            for stream in streams:
                key = (mux_normalize_language(stream.language), stream.title or "-", stream.codec_name or "-")
                summary[key] = summary.get(key, 0) + 1
        if not summary:
            print(paint("  none", Color.GRAY))
            continue
        for (language, title, codec), count in sorted(summary.items()):
            print(
                f"  {mux_pair_text('count', count, Color.BOLD + Color.MUX_GOLD)} | "
                f"{mux_pair_text('lang', display_language(language), mux_language_color(language))} | "
                f"{mux_pair_text('title', title, Color.MUX_SKY)} | "
                f"{mux_pair_text('codec', codec, Color.MUX_MINT)}"
            )
        print()


__all__ = [
    'mux_selected_audio_streams',
    'mux_selected_subtitle_streams',
    'mux_format_metadata_edit',
    'mux_format_metadata_edits',
    'mux_format_stream',
    'mux_kept_indexes_for_metadata',
    'mux_kept_languages_for_metadata',
    'mux_kept_streams_for_metadata',
    'mux_language_color',
    'mux_metadata_edit_change_text',
    'mux_metadata_edit_match_text',
    'mux_print_scan_report',
    'mux_print_unique_summary',
    'mux_probe_file',
    'mux_scan_files',
    'mux_stream_indexes',
    'mux_streams_for_type',
    'mux_unique_stream_values',
    'MUX_LANGUAGE_COLORS',
    'MuxCleanupRules',
    'MuxMediaFile',
    'MuxStreamInfo',
    'MuxStreamMetadataEdit',
]
