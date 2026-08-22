"""FFmWiz helpers (dependency level 3) — concerns: misc(7), encode_opts(6), filters(2).

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


def ffmpeg_progress_duration_for_answers(answers: dict[str, Any], source_duration: float) -> float:
    final_duration = final_processed_duration_for_splits(answers, source_duration)
    split_points = normalize_separator_points(answers.get("separator_points"), final_duration)
    if split_points:
        # FFmpeg's -progress out_time is per active output in multi-output Split
        # commands. FFmWiz reconstructs aggregate Split progress from part
        # durations plus the active output timestamp, so the progress duration
        # must be the full processed program duration rather than only the last
        # part.
        return final_duration
    return final_duration


def cpu_pixel_format_for_output(answers: dict[str, Any]) -> str:
    """Planar software pixel format for CPU/libx26x encoding."""
    return "yuv420p10le" if output_video_bit_depth(answers) > 8 else CPU_FORMAT


def cuda_pixel_format_for_output(answers: dict[str, Any]) -> str:
    """Hardware-surface pixel format for CUDA filter graphs feeding NVENC."""
    return "p010le" if output_video_bit_depth(answers) > 8 else CUDA_FORMAT


def nvenc_software_pixel_format_for_output(answers: dict[str, Any]) -> str:
    """Software-frame pixel format for a CPU filter graph feeding hevc_nvenc.

    NVENC accepts software yuv420p (8-bit) and p010le (10-bit) input frames.
    yuv420p10le is NOT a native NVENC input format, so a CPU filter graph that
    feeds NVENC for 10-bit output must terminate in p010le, not yuv420p10le."""
    return "p010le" if output_video_bit_depth(answers) > 8 else "yuv420p"


def hevc_profile_for_output(answers: dict[str, Any], default_profile: str | None = None) -> str:
    """HEVC profile for the resolved output bit depth: main10 for 10-bit,
    otherwise the requested/default profile (main)."""
    if output_video_bit_depth(answers) > 8:
        return "main10"
    return default_profile or NVENC_HEVC_PROFILE


def bit_depth_precision_note(answers: dict[str, Any]) -> str | None:
    """A human-readable note when source and output bit depths differ.

    - source > output: precision is reduced (e.g. 12-bit source to 10-bit out).
    - source < output: output is encoded at a higher bit depth, but no new real
      precision is created (8-bit source to 10-bit encode).
    Returns None when source depth is unknown or equals the output depth."""
    src = source_video_bit_depth(answers)
    out = output_video_bit_depth(answers)
    if not src:
        return None
    if src > out:
        return (
            f"source is {src}-bit; output is limited to {out}-bit. "
            f"Precision will be reduced from {src}-bit to {out}-bit."
        )
    if src < out:
        return (
            f"source is {src}-bit; output will be encoded as {out}-bit, "
            f"but source precision remains {src}-bit."
        )
    return None


def filter_graph_path_label(answers: dict[str, Any], video_encoder: str | None = None) -> str:
    """Describe the filter-graph/frame path for the command summary."""
    if video_encoder is None:
        video_encoder, _tag, _profile = resolve_video_encoder(answers)
    if str(video_encoder).lower() == "copy":
        return "stream copy (no filter graph)"
    if str(video_encoder).endswith("_nvenc"):
        if can_use_cuda_fast_path(answers, video_encoder):
            return "CUDA/GPU filter graph feeding NVENC"
        return "CPU filter graph feeding NVENC"
    return "CPU/libx26x software filter graph"


def high_bit_depth_requires_cpu_encoder(answers: dict[str, Any], video_encoder: str) -> bool:
    # Bit-depth support is per-ENCODER, not per-family. hevc_nvenc and av1_nvenc
    # do Main10, but h264_nvenc has NO 10-bit mode at all: feeding it p010le
    # fails at encoder init with "Provided device doesn't support required NVENC
    # features", so anything above 8-bit must leave the H.264 NVENC path.
    encoder = str(video_encoder)
    depth = output_video_bit_depth(answers)
    if encoder == H264_NVENC_ENCODER:
        return depth > 8
    return depth > 10 and encoder.endswith("_nvenc")


def audio_speed_reverse_filter_parts(answers: dict[str, Any]) -> list[str]:
    """Reverse/atempo filter parts shared by the final encode and the two-pass
    loudnorm measurement graph (everything before the loudnorm insertion)."""
    parts: list[str] = []
    if encode_audio_reverse_enabled(answers):
        parts.append("areverse")
    speed = encode_audio_speed_factor(answers)
    if abs(speed - 1.0) > 1e-6:
        parts.append(atempo_filter_chain(speed))
    return parts


def audio_transform_enabled(answers: dict[str, Any]) -> bool:
    return audio_speed_transform_enabled(answers) or audio_cut_transform_enabled(answers) or loudnorm_transform_enabled(answers)


def color_range_output_args(
    answers: dict[str, Any],
    spec: str = ":v:0",
    *,
    allow_compatibility_fallback: bool = False,
    workflow: str | None = None,
) -> list[str]:
    """Return the FFmpeg output color-range option (or [] when unspecified).

    This only writes output metadata; it never performs a pixel-value range
    conversion on its own. The defensive default (allow_compatibility_fallback=
    False) raises ColorRangeUnresolvedError for an unresolved unknown-range
    source so production command builders never emit a silent fallback."""
    resolved, _source = resolve_color_range(
        answers,
        allow_compatibility_fallback=allow_compatibility_fallback,
        workflow=workflow,
    )
    if resolved in {"tv", "pc"}:
        return [f"-color_range{spec}", resolved]
    return []


def stream_statistics_tags_trustworthy(
    stream: dict[str, Any],
    fmt: dict[str, Any] | None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> bool:
    size_bytes = stream_tag_size_bytes(stream)
    if size_bytes is not None and not stream_size_plausible(size_bytes, fmt):
        return False
    if stream_statistics_tags_conflict_with_container(stream, fmt, sibling_streams):
        return False
    return True


def media_info_value_with_units(key: str, value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)

    key_text = str(key or "value")
    normalized = key_text.lower().replace("-", "_").replace(" ", "_")

    if normalized == "color_range":
        return display_color_range(value)

    if normalized in {"duration", "start_time", "end_time"} or (
        (normalized.startswith("duration_") or normalized.endswith("_duration"))
        and normalized != "duration_ts"
    ):
        formatted = media_info_seconds_text(value)
        if formatted:
            return formatted

    if normalized in {"time_base", "codec_time_base"}:
        formatted = media_info_time_base_text(value)
        if formatted:
            return formatted

    if normalized in {"avg_frame_rate", "r_frame_rate", "frame_rate"}:
        formatted = media_info_fps_text(value)
        if formatted:
            return formatted

    if (
        normalized in {"bit_rate", "max_bit_rate"}
        or normalized.startswith("bps")
        or normalized.endswith("_bps")
    ):
        formatted = media_info_bitrate_text(value)
        if formatted:
            return formatted

    if (
        normalized == "size"
        or "number_of_bytes" in normalized
        or normalized.endswith("_size")
        or normalized == "extradata_size"
    ):
        formatted = media_info_bytes_text(value)
        if formatted:
            return formatted

    if normalized in {"width", "height", "coded_width", "coded_height", "displaymatrix_rotation"}:
        number = _as_int_value(value)
        if number is not None:
            unit = "degree" if normalized == "displaymatrix_rotation" else "px"
            return f"{number} {unit}"

    if normalized in {"sample_rate"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} Hz"

    if normalized in {"bits_per_raw_sample", "bits_per_sample", "bits_per_coded_sample"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'bit')}"

    if normalized in {"channels"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'channel')}"

    if normalized in {"sample_aspect_ratio", "display_aspect_ratio"}:
        return f"{value} ratio"

    if normalized in {"duration_ts", "start_pts", "pts", "dts"} or normalized.endswith("_ts"):
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'tick')}"

    if normalized in {"start", "end"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'tick')}"

    if normalized in {"nb_frames", "nb_read_frames"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'frame')}"

    if "number_of_frames" in normalized:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'frame')}"

    if normalized in {"nb_read_packets"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'packet')}"

    if normalized in {"nb_streams", "stream_count"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'stream')}"

    if normalized in {"nb_programs", "program_count"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'program')}"

    if normalized in {"nb_chapters", "chapter_count"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'chapter')}"

    if normalized in {"initial_padding", "trailing_padding", "skip_samples", "frame_size"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'sample')}"

    if normalized in {"has_b_frames", "refs"}:
        number = _as_int_value(value)
        if number is not None:
            return f"{number} {_plural_unit(number, 'frame')}"

    if normalized == "probe_score":
        number = _as_int_value(value)
        if number is not None:
            return f"{number}/100"

    return str(value)


def apply_output_location_value(answers: dict[str, Any], value: str) -> None:
    input_path: Path = answers["input_path"]
    answers.pop("output_name_stem", None)
    answers["output_used_default"] = False
    if not value:
        # The default output folder is the input folder. The previous default
        # was a fixed E:\output path which broke most workflows.
        answers["output_location"] = input_path.parent
        answers["output_used_default"] = True
        return

    output_value = terminal_path(value)
    if not output_value.drive and not output_value.root and output_value.parent == Path(".") and not output_value.suffix:
        answers["output_location"] = input_path.parent
        answers["output_name_stem"] = output_value.name
    else:
        answers["output_location"] = output_value


def hardsub_output_10bit(answers: dict[str, Any]) -> bool:
    stream = (answers.get("video_streams") or [{}])[0]
    hdr_info = answers.get("hardsub_hdr_info") or video_hdr_dolby_info(stream)
    return (video_bit_depth(stream) or 8) > 8 or bool(hdr_info.get("hdr") or hdr_info.get("dolby"))


def join_frame_rates_differ(items: list[dict[str, Any]]) -> bool:
    """True when at least two joined video inputs have distinct frame rates.

    Rounded to 3 decimals to match join_stream_signature so the copy-compat
    logic and this detection agree (e.g. 29.970 vs 30.000 are different)."""
    distinct = {round(rate, 3) for rate in join_video_frame_rates(items)}
    return len(distinct) >= 2


def join_highest_frame_rate(items: list[dict[str, Any]]) -> float | None:
    """Highest source frame rate among joined video inputs (unify default)."""
    rates = join_video_frame_rates(items)
    return max(rates) if rates else None


def join_copy_compatible_except_fps(items: list[dict[str, Any]]) -> bool:
    """True when the joined inputs match on everything except frame rate (same
    container, codec, resolution, pixel format, and audio layout). In that case
    the concat demuxer + stream copy can join them into a VFR output."""
    if len(items) < 2:
        return False
    first_ext = items[0]["path"].suffix.lower()
    first_sig = join_signature_without_fps(items[0])
    for item in items[1:]:
        if item["path"].suffix.lower() != first_ext:
            return False
        if join_signature_without_fps(item) != first_sig:
            return False
    return True


__all__ = [
    'ffmpeg_progress_duration_for_answers',
    'cpu_pixel_format_for_output',
    'cuda_pixel_format_for_output',
    'nvenc_software_pixel_format_for_output',
    'hevc_profile_for_output',
    'bit_depth_precision_note',
    'filter_graph_path_label',
    'high_bit_depth_requires_cpu_encoder',
    'audio_speed_reverse_filter_parts',
    'audio_transform_enabled',
    'color_range_output_args',
    'stream_statistics_tags_trustworthy',
    'media_info_value_with_units',
    'apply_output_location_value',
    'hardsub_output_10bit',
    'join_frame_rates_differ',
    'join_highest_frame_rate',
    'join_copy_compatible_except_fps',
]
