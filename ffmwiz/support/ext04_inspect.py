"""Reading a source and reporting on it: probe, scan, describe, write.

Split out of `ext04b` as its own responsibility. Nothing here builds or runs an
encode. These read a file (or a folder of them), and the tool environment they
would run in, then say what they found -- to the console, to the log, or to a
report file on disk.

Re-exported by `ext04b`, so every consumer of `from ffmwiz.support.ext04b
import *` still sees the full set. Imports only lower tiers; it never reaches
back up into `ext04b`.
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


def write_metadata_report(input_path: Path, report_type: str, answers: dict[str, Any]) -> Path:
    if report_type == "human":
        probe = probe_media_json(input_path, answers["ffprobe"])
        lines = [f"Metadata report for: {input_path}", "", "Streams"]
        lines.extend("  " + _strip_ansi(metadata_stream_line(probe, stream)) for stream in probe.get("streams") or [])
        lines.extend(["", "Chapters"])
        lines.extend("  " + line for line in metadata_chapter_lines(probe))
        output_path = metadata_report_output_path(input_path, "_metadata_report", ".txt")
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return output_path
    args = metadata_json_report_command(answers["ffprobe"], input_path, report_type)
    log_info("Metadata report ffprobe command: " + command_to_powershell(args))
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        log_error("Metadata report ffprobe failed:\n" + (result.stderr or result.stdout or ""))
        raise RuntimeError("Metadata report failed. See log file.")
    suffix = {"full": "_metadata_report", "tags": "_metadata_tags", "color": "_metadata_color", "disposition": "_metadata_disposition", "chapters": "_metadata_chapters"}.get(report_type, "_metadata_report")
    output_path = metadata_report_output_path(input_path, suffix, ".json")
    output_path.write_text(result.stdout, encoding="utf-8")
    return output_path


def _capability_cache_view(ffmpeg: str, ffprobe: str) -> None:
    path = capability_cache_path()
    print()
    print("  " + field_text("cache file", str(path), Color.AQUA))
    cache = load_capability_cache()
    envs = cache.get("environments", {})
    if not envs:
        appio.note("No cached capability results yet.")
        return
    # Show the current environment identity for both CPU and NVENC bindings.
    for enc_label, sample_encoder in (("CPU encoders", "libx265"), ("NVENC encoders", "hevc_nvenc")):
        _identity, key = services.capability_environment_key(ffmpeg, ffprobe, sample_encoder)
        print("  " + field_text("environment (%s)" % enc_label, key[:12], Color.DIM))
    for env_key, env in envs.items():
        print(paint("  Environment %s" % env_key[:12], Color.BOLD + Color.LIME))
        ident = env.get("ffmpeg_identity", {})
        print("    " + field_text("ffmpeg", ident.get("version", "?"), Color.WHITE))
        hw = env.get("hardware_identity", {})
        if hw.get("gpu") not in (None, "n/a"):
            print("    " + field_text("gpu/driver", f"{hw.get('gpu')} / {hw.get('driver')}", Color.WHITE))
        caps = env.get("capabilities", {}).get(CAPABILITY_GROUP, {})
        for combo, entry in caps.items():
            fr = entry.get("expected_final_range")
            shown = "unspecified" if fr in {"unknown", "", None} else fr
            print("    " + field_text(
                combo,
                f"status={entry.get('status')}; expected_final_range={shown}; "
                f"verified={entry.get('verified_at_utc')}",
                Color.AQUA,
            ))


def copy_cut_source_duration(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
    chapters: list[dict[str, Any]],
) -> float:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    for start, end in keep_ranges:
        duration = max(duration, float(start or 0.0), float(end or 0.0))
    for chapter in chapters:
        end = copy_cut_chapter_seconds(chapter, "end")
        if end is not None:
            duration = max(duration, end)
    return max(0.0, duration)


def probe_additional_track_file(ffprobe: str, path: Path, ffmpeg: str | None = None) -> dict[str, Any]:
    probe = services.ffprobe_json(ffprobe, path)
    streams = probe.get("streams") or []
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    if not audio_streams and not subtitle_streams:
        raise ValueError("Additional files must contain at least one audio or subtitle stream.")
    audio_volume_stats: dict[int, dict[str, str]] = {}
    if audio_streams and ffmpeg:
        audio_volume_stats = services.get_audio_volume_stats({
            "ffmpeg": ffmpeg,
            "input_path": path,
            "audio_streams": audio_streams,
        })
    return {
        "path": path,
        "format": probe.get("format", {}),
        "audio_streams": audio_streams,
        "audio_volume_stats": audio_volume_stats,
        "subtitle_streams": subtitle_streams,
        "video_streams": video_streams,
        "chapters": probe.get("chapters") or [],
    }


def extract_scan_files(answers: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    """Return extract entries for a file or folder path. A folder is scanned
    (non-recursively) for media files. Each entry: {path, format, candidates,
    by_index}. Files that fail to probe or have no extractable streams are
    skipped."""
    ffprobe = answers["ffprobe"]
    if path.is_dir():
        paths = sorted(
            (p for p in path.iterdir()
             if is_folder_media_candidate(p) and not looks_like_generated_output_file(p)),
            key=lambda p: p.name.lower(),
        )
    else:
        paths = [path]
    entries: list[dict[str, Any]] = []
    for p in paths:
        try:
            probe = services.ffprobe_json(ffprobe, p)
        except Exception:
            log_warn(f"Extract Stream skipped unreadable file: {p}")
            continue
        streams = probe.get("streams") or []
        candidates = [s for s in streams if str(s.get("codec_type") or "").lower() in {"video", "audio", "subtitle"}]
        if not candidates:
            continue
        by_index = {stream_global_index(s): s for s in candidates if stream_global_index(s) is not None}
        entries.append({
            "path": p,
            "format": probe.get("format", {}),
            "candidates": candidates,
            "by_index": by_index,
        })
    return entries


def extract_describe_stream(stream: dict[str, Any], fmt: dict[str, Any]) -> str:
    """Compact per-stream description for the extract listing (no packet probe)."""
    codec_type = str(stream.get("codec_type") or "unknown")
    codec = str(stream.get("codec_name") or "unknown")
    idx = stream_global_index(stream)
    color = {"video": Color.MAGENTA, "audio": Color.BLUE, "subtitle": Color.LIGHT_YELLOW}.get(codec_type, Color.WHITE)
    parts = [
        field_text("stream index", idx if idx is not None else "unknown", Color.LIGHT_BLUE),
        field_text("type", codec_type, color),
        field_text("codec", codec, Color.CYAN),
        field_text("duration", format_duration(services.stream_duration_seconds(stream, fmt)), Color.MAGENTA),
    ]
    if codec_type == "video":
        parts.append(field_text("size", f"{stream.get('width', '?')}x{stream.get('height', '?')}", Color.LIME))
    elif codec_type == "audio":
        parts.append(field_text("channels", stream.get("channels", "?"), Color.GREEN))
        parts.append(field_text("sample_rate", stream.get("sample_rate", "?"), Color.MAGENTA))
        lang = display_language((stream.get("tags") or {}).get("language"))
        if lang:
            parts.append(field_text("lang", lang, Color.AQUA))
    elif codec_type == "subtitle":
        lang = display_language((stream.get("tags") or {}).get("language"))
        parts.append(field_text("language", lang or "unknown", Color.AQUA))
    return " | ".join(parts)


def print_prerequisite_summary(ffmpeg: str | None, ffprobe: str | None) -> None:
    """Prerequisites (FFmpeg/FFprobe and the optional PySide6 GUI runtime) are
    validated behind the scenes by check_tools()/ensure_pyside6_installed(),
    which prompt to install anything missing. This summary is intentionally
    silent: it only records the resolved tools to the log, with no console
    output, so startup stays clean."""
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    log_info(
        f"Prerequisites OK: Python={py_ver}; ffmpeg={ffmpeg or 'missing'}; "
        f"ffprobe={ffprobe or 'missing'}; pyside6={'installed' if _pyside6_available() else 'absent'}",
        component="Startup",
    )


__all__ = [
    'write_metadata_report',
    '_capability_cache_view',
    'copy_cut_source_duration',
    'probe_additional_track_file',
    'extract_scan_files',
    'extract_describe_stream',
    'print_prerequisite_summary',
]
