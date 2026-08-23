"""FFmWiz helpers (dependency level 0) — concerns: audio(27).

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


def stream_sample_rate(stream: dict[str, Any]) -> int | None:
    """Parse an audio stream's sample rate (Hz) to int, or None when unknown."""
    value = stream.get("sample_rate")
    try:
        return int(value) if value is not None and str(value).strip().isdigit() else None
    except (TypeError, ValueError):
        return None


def resolve_audio_sample_rate(answers: dict[str, Any]) -> int | None:
    """Chosen OUTPUT audio sample rate in Hz, or None to keep the source rate.

    An explicit answers['audio_sample_rate'] (int Hz) wins; otherwise fall back
    to the module default AUDIO_SAMPLE_RATE (None = keep source)."""
    value = answers.get("audio_sample_rate")
    if value in (None, "", "n", "keep"):
        return int(AUDIO_SAMPLE_RATE) if AUDIO_SAMPLE_RATE else None
    try:
        rate = int(value)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


def resolve_audio_channels(answers: dict[str, Any]) -> int | None:
    """Chosen OUTPUT channel count, or None to keep the source layout.

    AUDIO_CHANNELS used to default to 2 and was applied unconditionally on every
    encode path, so a 5.1 or 7.1 source came back stereo without a word -- a
    lossless FLAC cut silently destroyed four channels. The policy now mirrors
    resolve_audio_sample_rate: an explicit request wins, otherwise keep what the
    source has.

    A layout wider than MAX_PRESERVED_AUDIO_CHANNELS is not preserved: past that
    point the odds of the target encoder/container refusing the layout outweigh
    the benefit, so it falls back to the configured default.
    """
    value = answers.get("audio_channels")
    if value not in (None, "", "n", "keep"):
        try:
            count = int(value)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            return count

    stream = _selected_channel_source_stream(answers)
    channels = stream_channel_count(stream) if stream else None
    if channels and 1 <= channels <= MAX_PRESERVED_AUDIO_CHANNELS:
        return channels
    return int(AUDIO_CHANNELS) if AUDIO_CHANNELS else None


def stream_channel_count(stream: dict[str, Any]) -> int | None:
    """Parse an audio stream's channel count to int, or None when unknown."""
    try:
        value = int((stream or {}).get("channels"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _selected_channel_source_stream(answers: dict[str, Any]) -> dict[str, Any] | None:
    """The audio stream whose layout the output should follow.

    Kept dependency-free on purpose: this module is level 0 and must not import
    the stream helpers that live beside it.
    """
    streams = answers.get("audio_streams") or []
    if not streams:
        return None
    selected = answers.get("audio_tracks")
    if isinstance(selected, list):
        for index in selected:
            if isinstance(index, int) and 0 <= index < len(streams):
                return streams[index]
    return streams[0]


def loudnorm_transform_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("loudnorm_enabled"))


def loudnorm_number(value: Any) -> str:
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def parse_loudnorm_target(value: str) -> float:
    try:
        target = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("Enter a numeric LUFS value.")
    if target < LOUDNORM_MIN_TARGET_I or target > LOUDNORM_MAX_TARGET_I:
        raise ValueError(
            f"Target I must be between {LOUDNORM_MIN_TARGET_I:g} and {LOUDNORM_MAX_TARGET_I:g} LUFS."
        )
    return target


def audio_cut_transform_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("audio_cut_keep_ranges"))


def encode_audio_reverse_enabled(answers: dict[str, Any]) -> bool:
    if answers.get("audio_speed_enabled"):
        return bool(answers.get("reverse_audio"))
    if answers.get("audio_speed_from_video"):
        return bool(answers.get("reverse_video"))
    return False


def default_audio_output_ext(input_path: Path) -> str:
    ext = input_path.suffix.lstrip(".").lower()
    if ext in {"mp3", "m4a", "aac", "opus", "ogg", "wav", "flac"}:
        return ext
    return "m4a"


def audio_tool_encode_options(output_ext: str, bitrate_kbps: int = DEFAULT_SPEED_AUDIO_BITRATE_KBPS,
                              sample_rate: int | None = None,
                              channels: int | None = -1) -> list[str]:
    """Encoder options for the Audio Cut / Speed / Reverse tools.

    `channels` defaults to the sentinel -1 meaning "use the AUDIO_CHANNELS
    module default"; pass an explicit count to preserve the source layout, or
    None to let FFmpeg keep whatever the source has.
    """
    ext = str(output_ext or "").lower().lstrip(".")
    if ext == "mp3":
        options = ["-c:a", "libmp3lame", "-b:a", f"{int(bitrate_kbps)}k"]
    elif ext in {"opus", "ogg"}:
        options = ["-c:a", "libopus", "-b:a", f"{int(bitrate_kbps)}k"]
    elif ext == "wav":
        options = ["-c:a", "pcm_s16le"]
    elif ext == "flac":
        options = ["-c:a", "flac"]
    else:
        options = ["-c:a", DEFAULT_AUDIO_CODEC, "-b:a", f"{int(bitrate_kbps)}k"]
    if channels == -1:
        channels = AUDIO_CHANNELS
    if channels:
        options.extend(["-ac", str(int(channels))])
    if sample_rate:
        options.extend(["-ar", str(int(sample_rate))])
    return options


def parse_volumedetect_output(text: str) -> dict[str, str]:
    stats: dict[str, str] = {}
    for key, value in VOLUMEDETECT_RE.findall(text or ""):
        stats[key] = f"{value} dB"
    return stats


def parse_loudnorm_measurement_output(text: str) -> dict[str, float] | None:
    for match in reversed(list(re.finditer(r"\{[\s\S]*?\}", text or ""))):
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
        if not all(key in payload for key in required):
            continue
        try:
            values = {key: float(payload[key]) for key in required}
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in values.values()):
            return values
    return None


def join_summary_volume_extremes(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Lowest mean volume and highest max volume (with file names) from
    pre-computed analysis, or None when no volume values are available.

    'lowest mean volume' = most negative mean (quietest average).
    'highest max volume' = closest to / above 0 dB (loudest peak)."""
    means = [(row["mean_volume_db"], row["name"]) for row in rows if row.get("mean_volume_db") is not None]
    maxes = [(row["max_volume_db"], row["name"]) for row in rows if row.get("max_volume_db") is not None]
    if not means and not maxes:
        return None
    lowest_mean = min(means, key=lambda pair: pair[0]) if means else None
    highest_max = max(maxes, key=lambda pair: pair[0]) if maxes else None
    return {"lowest_mean": lowest_mean, "highest_max": highest_max}


def audio_volume_field(stats: dict[int, dict[str, str]], index: int, key: str) -> str:
    value = (stats.get(index) or {}).get(key)
    return value or "unknown"


def compatible_channel_layout(left: str, right: str) -> bool:
    if not left or not right or left == "unknown" or right == "unknown":
        return True
    return left == right


def audio_hash_window_starts(duration: float | None, sample_seconds: float) -> list[float]:
    if not duration or duration <= sample_seconds * 2:
        return [0.0]
    starts = {
        max(0.0, duration * 0.10),
        max(0.0, (duration - sample_seconds) * 0.50),
        max(0.0, duration - sample_seconds - max(1.0, duration * 0.05)),
    }
    return sorted(starts)


def media_info_audio_coding_type(codec: str) -> str:
    normalized = str(codec or "").lower()
    if normalized in {"flac", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "truehd"}:
        return "lossless"
    if normalized:
        return "lossy or compressed"
    return "unknown"


def folder_audio_issue_summary(filename: str, report: dict[str, Any]) -> str | None:
    parts: list[str] = []
    empty_tracks = sorted(report.get("empty_tracks", set()))
    near_empty_tracks = sorted(report.get("near_empty_tracks", set()))
    confirmed_pairs = report.get("confirmed_pairs", [])
    possible_pairs = report.get("possible_pairs", [])
    if empty_tracks:
        parts.append(f"empty audio tracks {empty_tracks}")
    if near_empty_tracks:
        parts.append(f"near-empty audio tracks {near_empty_tracks}")
    if confirmed_pairs:
        parts.append(f"confirmed duplicate audio pairs {confirmed_pairs}")
    if possible_pairs and not confirmed_pairs:
        parts.append(f"possible duplicate audio pairs {possible_pairs}")
    if not parts:
        return None
    return f"{filename}: " + "; ".join(parts)


def output_is_audio_only(answers: dict[str, Any]) -> bool:
    ext = answers.get("output_ext", "").lower()
    if ext in AUDIO_ONLY_EXTS:
        return True
    if not answers.get("video_streams"):
        return True
    return False


def wizard_audio_join_applicable(answers: dict[str, Any]) -> bool:
    """True for a pure audio input in the interactive wizard, so the user can
    join more audio files (the video join path requires video inputs)."""
    return (
        bool(answers.get("input_path"))
        and bool(answers.get("audio_streams"))
        and not answers.get("video_streams")
    )


def default_audio_codec_for_ext(ext: str) -> str:
    return AUDIO_CODEC_DEFAULTS_BY_FORMAT.get(ext.lower(), DEFAULT_AUDIO_CODEC)


def normalize_audio_codec(codec: Any, default: str | None = None) -> str:
    text = str(codec or default or DEFAULT_AUDIO_CODEC).strip()
    lowered = text.lower()
    if lowered == "n":
        return "copy"
    return AUDIO_CODEC_ALIASES.get(lowered, lowered)


def selected_audio_streams(answers: dict[str, Any]) -> list[int]:
    selected = answers.get("audio_tracks", [])
    count = len(answers.get("audio_streams", []))
    if selected == "all":
        return list(range(count))
    return selected


def _audio_report_signature(answers: dict[str, Any]) -> str:
    """Signature of the report-affecting inputs: primary file + audio count and
    every joined input file + audio count. Changing the selected files/tracks
    changes this signature and forces a fresh report."""
    parts = [str(answers.get("input_path")), str(len(answers.get("audio_streams") or []))]
    for item in answers.get("join_input_items") or []:
        parts.append(str(item.get("path")))
        parts.append(str(len(item.get("audio_streams") or [])))
    return "|".join(parts)


def ensure_audio_input(answers: dict[str, Any]) -> None:
    if not answers.get("audio_streams"):
        raise ValueError("This mode needs an audio stream.")


def lossless_audio_copy_ext_choices(codec_name: str, input_suffix: str = "") -> list[str]:
    """Container extensions that can hold the given audio codec via stream copy
    (no re-encode). The first entry is the preferred default. Unknown codecs
    fall back to universal containers."""
    choices = LOSSLESS_AUDIO_COPY_EXT_CHOICES.get(str(codec_name or "").lower())
    if choices:
        return list(choices)
    return ["mka", "mov"]


def format_audio_ranges_for_summary(ranges: list[tuple[float, float]], label: str) -> str:
    if not ranges:
        return f"{label}: (none)"
    lines = [f"{label}:"]
    for idx, (start, end) in enumerate(ranges, start=1):
        lines.append(f"  {idx}. {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}")
    return "\n".join(lines)


def add_files_supported_audio_codecs_for_container(ext: str) -> set[str] | None:
    normalized = ext.lower().lstrip(".")
    if normalized in {"mkv", "mk3d", "mka"}:
        return None
    if normalized == "webm":
        return {"opus", "vorbis"}
    if normalized in MP4_LIKE_EXTS:
        return {"aac", "mp3", "mp4a", "ac3", "eac3", "alac", "flac", "opus"}
    if normalized == "avi":
        return {"aac", "ac3", "eac3", "mp2", "mp3", "pcm_s16le", "pcm_s24le", "pcm_u8"}
    if normalized in {"ts", "m2ts", "mts"}:
        return {"aac", "ac3", "eac3", "mp2", "mp3", "dts", "truehd"}
    if normalized == "mov":
        return {"aac", "mp3", "mp4a", "ac3", "eac3", "alac", "flac", "opus", "pcm_s16le", "pcm_s24le"}
    return None


__all__ = [
    'stream_sample_rate',
    'resolve_audio_sample_rate',
    'resolve_audio_channels',
    'stream_channel_count',
    'loudnorm_transform_enabled',
    'loudnorm_number',
    'parse_loudnorm_target',
    'audio_cut_transform_enabled',
    'encode_audio_reverse_enabled',
    'default_audio_output_ext',
    'audio_tool_encode_options',
    'parse_volumedetect_output',
    'parse_loudnorm_measurement_output',
    'join_summary_volume_extremes',
    'audio_volume_field',
    'compatible_channel_layout',
    'audio_hash_window_starts',
    'media_info_audio_coding_type',
    'folder_audio_issue_summary',
    'output_is_audio_only',
    'wizard_audio_join_applicable',
    'default_audio_codec_for_ext',
    'normalize_audio_codec',
    'selected_audio_streams',
    '_audio_report_signature',
    'ensure_audio_input',
    'lossless_audio_copy_ext_choices',
    'format_audio_ranges_for_summary',
    'add_files_supported_audio_codecs_for_container',
]
