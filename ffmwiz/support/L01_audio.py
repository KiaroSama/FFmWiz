"""FFmWiz helpers (dependency level 1) — concerns: audio(8).

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


def source_audio_sample_rate(answers: dict[str, Any]) -> int | None:
    """Sample rate (Hz) of the first selected audio stream (or first audio
    stream), used as the default 'keep current rate' value in prompts."""
    streams = answers.get("audio_streams") or []
    if not streams:
        return None
    try:
        selected = selected_audio_streams(answers) if answers.get("audio_tracks") is not None else []
    except Exception:
        selected = []
    idx = selected[0] if selected else 0
    if 0 <= idx < len(streams):
        rate = stream_sample_rate(streams[idx])
        if rate:
            return rate
    for stream in streams:
        rate = stream_sample_rate(stream)
        if rate:
            return rate
    return None


def loudnorm_mode(answers: dict[str, Any]) -> str:
    """Resolved loudnorm mode: 'off', 'single', or 'two_pass'.

    Prefers the explicit answers['loudnorm_mode'] set by the wizard, and falls
    back to deriving it from the enabled flag plus presence of measured values
    (so older answer dicts still report correctly)."""
    explicit = str(answers.get("loudnorm_mode") or "").strip().lower()
    if explicit in {"off", "single", "two_pass"}:
        return explicit
    if not loudnorm_transform_enabled(answers):
        return "off"
    return "two_pass" if (isinstance(answers.get("loudnorm_measured"), dict) and answers.get("loudnorm_measured")) else "single"


def _loudnorm_output_sample_rate(answers: dict[str, Any] | None = None) -> int:
    """Return the sample rate to apply after LoudNorm to stabilize the output.
    Honors the chosen output sample rate when available."""
    if answers is not None:
        rate = resolve_audio_sample_rate(answers)
        if rate:
            return rate
    return int(AUDIO_SAMPLE_RATE) if AUDIO_SAMPLE_RATE else 48000


def resolve_audio_tool_output_ext(answers: dict[str, Any]) -> str:
    output_location = answers.get("output_location")
    if isinstance(output_location, Path) and output_location.suffix:
        requested = output_location.suffix.lstrip(".").lower()
        if requested in {"mp3", "m4a", "aac", "opus", "ogg", "wav", "flac"}:
            return requested
    input_path = answers.get("input_path")
    if isinstance(input_path, Path):
        return default_audio_output_ext(input_path)
    return "m4a"


def audio_mean_max_volume_field(stats: dict[int, dict[str, str]], index: int) -> str:
    mean_value = audio_volume_field(stats, index, "mean_volume")
    max_value = audio_volume_field(stats, index, "max_volume")
    if mean_value.endswith(" dB") and max_value.endswith(" dB"):
        return f"{mean_value[:-3]} / {max_value[:-3]} dB"
    return f"{mean_value} / {max_value}"


def audio_bitrate_estimate_kbps(stream: dict[str, Any]) -> int | None:
    codec = str(stream.get("codec_name", "")).lower()
    channels = int_metadata_value(stream, "channels") or 2
    sample_rate = int_metadata_value(stream, "sample_rate") or 48000
    if codec.startswith("pcm_"):
        bits = int_metadata_value(stream, "bits_per_raw_sample") or 16
        return max(1, round(sample_rate * channels * bits / 1000))
    if codec in {"flac", "alac"}:
        return max(384, channels * 384)
    if codec in {"ac3"}:
        return 192 if channels <= 2 else 448
    if codec in {"eac3"}:
        return 160 if channels <= 2 else 384
    if codec in {"opus", "libopus"}:
        return 96 if channels <= 1 else max(128, channels * 48)
    if codec in {"mp3", "mp3float", "libmp3lame"}:
        return 96 if channels <= 1 else 160
    if codec in {"aac", "aac_latm", "mp4a"}:
        return 96 if channels <= 1 else max(128, channels * 64)
    return max(96, channels * 64)


def audio_codec_uses_bitrate(codec: str) -> bool:
    lowered = normalize_audio_codec(codec)
    if lowered == "copy":
        return False
    if lowered.startswith("pcm_"):
        return False
    return lowered in BITRATE_AUDIO_CODECS


def lossless_audio_copy_ext(codec_name: str, input_suffix: str = "") -> str:
    """Preferred audio container extension for a lossless (stream-copy) split of
    the given codec."""
    return lossless_audio_copy_ext_choices(codec_name, input_suffix)[0]


def container_video_codec(output_ext: str, requested: str) -> tuple[str, str | None]:
    """(codec alias to use, note when it was changed) for this container.

    HardSub, standalone Join and the main wizard each resolved the video codec
    independently, so H.264 into .webm was emitted from two of the three paths
    and died at header-write time.
    """
    ext = str(output_ext or "").lower().lstrip(".")
    policy = VIDEO_CODECS_BY_FORMAT.get(ext)
    if not policy:
        return requested, None
    if str(requested).upper() in {str(item).upper() for item in policy["allowed"]}:
        return requested, None
    replacement = policy["fallback"]
    return replacement, (
        f".{ext} cannot store {requested} video; {replacement} was selected "
        "for container compatibility."
    )


def container_audio_codec(output_ext: str, requested: str,
                          source_codec: str | None = None) -> tuple[str, str | None]:
    """(audio encoder to use, note when it was changed) for this container.

    `copy` is only safe when the SOURCE codec is one the container accepts:
    stream-copying an AAC track into WebM is rejected by the muxer exactly like
    encoding AAC into it would be, so a `source_codec` must be supplied wherever
    copy is a real option.
    """
    ext = str(output_ext or "").lower().lstrip(".")
    allowed = AUDIO_CODECS_BY_FORMAT.get(ext)
    if not allowed:
        return requested, None
    if requested == "copy":
        if source_codec is None:
            return requested, None
        normalized = normalize_audio_codec(source_codec)
        if normalized in allowed or f"lib{normalized}" in allowed:
            return requested, None
        replacement = default_audio_codec_for_ext(ext)
        return replacement, (
            f".{ext} cannot store a copied {source_codec} track; {replacement} "
            "was selected for container compatibility."
        )
    if requested in allowed:
        return requested, None
    replacement = default_audio_codec_for_ext(ext)
    return replacement, (
        f".{ext} cannot store {requested} audio; {replacement} was selected "
        "for container compatibility."
    )


def container_audio_encode_args(output_ext: str, requested: str, bitrate_kbps: int | None,
                                channels: int | None = None,
                                sample_rate: int | None = None,
                                source_codec: str | None = None) -> tuple[list[str], str | None]:
    """Full `-c:a [...]` for a container, dropping -b:a for lossless codecs.

    `-b:a` on flac/pcm is meaningless, and the join/audio-tool builders used to
    hardcode `-c:a aac -b:a Nk` regardless of the output container.
    """
    codec, note = container_audio_codec(output_ext, requested, source_codec)
    args = ["-c:a", codec]
    if codec != "copy" and bitrate_kbps and audio_codec_uses_bitrate(codec):
        args.extend(["-b:a", f"{int(bitrate_kbps)}k"])
    if channels:
        args.extend(["-ac", str(int(channels))])
    if sample_rate:
        args.extend(["-ar", str(int(sample_rate))])
    return args, note


def _selected_audio_tool_stream(answers: dict[str, Any]) -> dict[str, Any] | None:
    streams = answers.get("audio_streams") or []
    try:
        index = int(answers.get("audio_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    if 0 <= index < len(streams):
        return streams[index]
    return streams[0] if streams else None


def resolve_audio_tool_bitrate_kbps(answers: dict[str, Any]) -> int:
    """Output bitrate (kbps) for the Audio Cut / Speed / Reverse tools.

    Those tools always re-encode. They used to call audio_tool_encode_options()
    without a bitrate, so EVERY output was pinned to
    DEFAULT_SPEED_AUDIO_BITRATE_KBPS (128) regardless of the source -- a 320 kbps
    track came back at 128. Prefer an explicit user answer, then the source
    stream's declared bitrate, then a codec/channel estimate.

    The result is clamped to AUDIO_TOOL_MAX_BITRATE_KBPS because the estimate for
    a lossless source (1411 kbps for CD PCM) is meaningless as a target for a
    lossy encoder.
    """
    explicit = answers.get("audio_bitrate_kbps")
    if explicit not in (None, "", "n", "keep"):
        try:
            value = int(explicit)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value

    stream = _selected_audio_tool_stream(answers)
    if stream:
        source = int_metadata_value(stream, "bit_rate")
        kbps = round(source / 1000) if source and source > 0 else audio_bitrate_estimate_kbps(stream)
        if kbps:
            return max(AUDIO_TOOL_MIN_BITRATE_KBPS, min(int(kbps), AUDIO_TOOL_MAX_BITRATE_KBPS))
    return DEFAULT_SPEED_AUDIO_BITRATE_KBPS


def resolve_audio_tool_channels(answers: dict[str, Any]) -> int | None:
    """Output channel count for the audio tools, or None to keep the source.

    AUDIO_CHANNELS forced every audio-tool output to stereo, so cutting a 5.1
    track silently downmixed it. Keep the source layout when the encoder can
    carry it.
    """
    stream = _selected_audio_tool_stream(answers)
    channels = int_metadata_value(stream, "channels") if stream else None
    if channels and 1 <= channels <= MAX_PRESERVED_AUDIO_CHANNELS:
        return channels
    return int(AUDIO_CHANNELS) if AUDIO_CHANNELS else None


__all__ = [
    'source_audio_sample_rate',
    'loudnorm_mode',
    '_loudnorm_output_sample_rate',
    'resolve_audio_tool_output_ext',
    'container_video_codec',
    'container_audio_codec',
    'container_audio_encode_args',
    'resolve_audio_tool_bitrate_kbps',
    'resolve_audio_tool_channels',
    'audio_mean_max_volume_field',
    'audio_bitrate_estimate_kbps',
    'audio_codec_uses_bitrate',
    'lossless_audio_copy_ext',
]
