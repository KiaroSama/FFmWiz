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


__all__ = [
    'source_audio_sample_rate',
    'loudnorm_mode',
    '_loudnorm_output_sample_rate',
    'resolve_audio_tool_output_ext',
    'audio_mean_max_volume_field',
    'audio_bitrate_estimate_kbps',
    'audio_codec_uses_bitrate',
    'lossless_audio_copy_ext',
]
