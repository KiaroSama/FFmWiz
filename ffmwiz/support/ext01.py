"""FFmWiz second-layer helpers (appio-dependent), tier 1.

Extracted from FFmWiz.py after the appio qualification; imports core,
existing support modules, and appio. Acyclic (imports only lower tiers).
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
from ffmwiz.support.L03 import *  # noqa: F401,F403
from ffmwiz.support.L04 import *  # noqa: F401,F403
from ffmwiz.support.L05 import *  # noqa: F401,F403
from ffmwiz.support.L06 import *  # noqa: F401,F403
from ffmwiz.support.L07 import *  # noqa: F401,F403
from ffmwiz.appio import *  # noqa: F401,F403
from ffmwiz import appio  # qualified primitives
from ffmwiz.support.ext00 import *  # noqa: F401,F403


def split_part_output_paths(
    output_path: Path,
    part_count: int,
    input_paths: list[Path] | None = None,
) -> list[Path]:
    input_paths = input_paths or []
    stem = sanitize_output_stem(output_path.stem)
    suffix = output_path.suffix or ".mp4"
    paths: list[Path] = []
    for index in range(1, int(part_count) + 1):
        candidate = output_path.with_name(f"{stem}_Part{index:02d}{suffix}")
        candidate = resolve_output_collision_against_inputs(candidate, input_paths, "_Final")
        candidate = unique_numbered_path(candidate)
        paths.append(candidate)
    return paths


def enforce_bit_depth_compatible_video_encoder(
    answers: dict[str, Any],
    video_encoder: str,
    tag: str | None,
    profile: str | None,
) -> tuple[str, str | None, str | None]:
    if high_bit_depth_requires_cpu_encoder(answers, video_encoder):
        source_depth = source_video_bit_depth(answers)
        target_depth = output_video_bit_depth(answers)
        cpu_encoder, cpu_tag, cpu_profile = cpu_encoder_for_high_bit_depth(answers, video_encoder)
        limit = "8-bit" if str(video_encoder) == H264_NVENC_ENCODER else "10-bit"
        appio.note(
            f"Source video is {source_depth}-bit. {video_encoder} output is limited to {limit}, "
            f"so {cpu_encoder} was selected to preserve {target_depth}-bit output."
        )
        return cpu_encoder, cpu_tag if cpu_tag is not None else tag, cpu_profile if cpu_profile is not None else profile
    return video_encoder, tag, profile


def append_additional_source_video_maps(cmd: list[str], answers: dict[str, Any]) -> int:
    if not can_map_additional_source_video_streams(answers):
        return 0
    count = 0
    for relative_index, _stream in enumerate(additional_source_video_streams(answers), start=1):
        cmd.extend(["-map", f"0:v:{relative_index}"])
        count += 1
    if count:
        log_info(f"Additional source video streams mapped for copy: count={count}")
    return count


def build_encode_audio_processing_filter(answers: dict[str, Any]) -> str:
    filters: list[str] = list(audio_speed_reverse_filter_parts(answers))
    if loudnorm_transform_enabled(answers):
        filters.append(build_loudnorm_filter(answers))
        # Explicitly resample after LoudNorm to guarantee a stable output rate.
        filters.append(f"aresample={_loudnorm_output_sample_rate(answers)}")
    filters.append("asetpts=PTS-STARTPTS")
    # The sound's half of the fade, on the same rule and in the same place as
    # the picture's: last, so "one second" is one second of the output. Without
    # it a faded picture would go to black over full-volume audio.
    fade_in, fade_out = requested_fade_seconds(answers)
    if fade_in or fade_out:
        # Deferred: the timeline map is built a tier above this one.
        from ffmwiz.wizard_build_b import encode_timeline_map
        try:
            output_seconds = encode_timeline_map(answers).output_duration
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            # A job whose duration is genuinely unknown. `fade_filter_parts`
            # drops the fade-out and says so. A bare `except Exception` here
            # swallowed the NameError from this function not being importable
            # at this tier, and silently dropped every fade-out instead.
            output_seconds = 0.0
        filters.extend(fade_filter_parts("a", output_seconds, fade_in, fade_out))
    chain = ",".join(filters)
    if loudnorm_transform_enabled(answers):
        log_info(f"LoudNorm audio filter segment inserted: {chain}")
        log_info(f"LoudNorm applied tracks: {answers.get('audio_tracks')}")
    return chain


def resolve_scale_dimensions(answers: dict[str, Any], resolution: Any) -> tuple[int, int] | None:
    dimensions, warning_text = calculate_scale_dimensions(answers, resolution)
    if dimensions is None:
        answers.pop("final_resolution", None)
        answers.pop("crop_box_dimensions", None)
        answers.pop("cropped_aspect_ratio", None)
        answers.pop("resolution_scale_axis", None)
        return None

    crop_w, crop_h = cropped_source_size(answers)
    answers["crop_box_dimensions"] = (crop_w, crop_h)
    answers["cropped_aspect_ratio"] = crop_w / max(1, crop_h)
    width, height = dimensions
    answers["final_resolution"] = (width, height)
    if warning_text and answers.get("_resolution_warning_emitted") != warning_text:
        appio.note("Resolution warning: " + warning_text + ".")
        answers["_resolution_warning_emitted"] = warning_text
    return width, height


def ensure_ffmpeg_tools_installed(interactive: bool = True) -> tuple[str | None, str | None]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe
    missing = []
    if not ffmpeg:
        missing.append("ffmpeg")
    if not ffprobe:
        missing.append("ffprobe")
    log_warn("Missing FFmpeg tools: " + ", ".join(missing))
    if not interactive:
        return ffmpeg, ffprobe

    print()
    appio.note("Missing prerequisite: " + ", ".join(missing))
    appio.note("FFmWiz needs the full FFmpeg package, including ffprobe.")

    installers: list[tuple[str, list[str]]] = []
    winget = shutil.which("winget")
    if winget:
        installers.append(("winget", [winget, "install", "--id", "Gyan.FFmpeg", "-e", "--source", "winget"]))
    choco = shutil.which("choco")
    if choco:
        installers.append(("Chocolatey", [choco, "install", "ffmpeg", "-y"]))

    if not installers:
        appio.note("No supported package manager was found. Install FFmpeg manually and reopen FFmWiz.")
        return ffmpeg, ffprobe

    label, cmd = installers[0]
    if not _confirm_install(
        f"Install FFmpeg now using {label}? [Y/n] ",
        "FFMWIZ_AUTO_INSTALL_FFMPEG",
    ):
        return ffmpeg, ffprobe

    if _run_dependency_install(cmd, "FFmpeg"):
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg and ffprobe:
            appio.note("FFmpeg tools are now available.")
            return ffmpeg, ffprobe
        appio.note("Install finished, but ffmpeg/ffprobe are still not visible in this terminal. Reopen the terminal if PATH was updated.")

    if len(installers) > 1:
        fallback_label, fallback_cmd = installers[1]
        if _confirm_install(
            f"Try installing FFmpeg using {fallback_label} instead? [Y/n] ",
            "FFMWIZ_AUTO_INSTALL_FFMPEG",
        ) and _run_dependency_install(fallback_cmd, "FFmpeg"):
            ffmpeg = shutil.which("ffmpeg")
            ffprobe = shutil.which("ffprobe")
    return ffmpeg, ffprobe


def ffprobe_full_json(ffprobe: str, input_path: Path) -> dict[str, Any]:
    args = [
        ffprobe,
        "-hide_banner",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-show_programs",
        "-show_private_data",
        "-print_format",
        "json",
        str(input_path),
    ]
    stdout_text = ""
    stderr_text = ""
    decoded_using = "not decoded"
    log_debug(f"Media Info ffprobe JSON command: {json.dumps(args, ensure_ascii=False)}")
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout_text, stdout_encoding = decode_subprocess_bytes(result.stdout, "utf-8-sig")
        stderr_text, stderr_encoding = decode_subprocess_bytes(result.stderr, "utf-8")
        decoded_using = f"stdout={stdout_encoding}; stderr={stderr_encoding}"
        if result.returncode != 0:
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe could not read the file. See log file: {_log_file_text()}")
        if not stdout_text.strip():
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe returned no JSON output. See log file: {_log_file_text()}")
        try:
            payload = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using, exc,
            )
            raise FFprobeError(f"ffprobe returned invalid JSON. See log file: {_log_file_text()}") from exc
        if not isinstance(payload, dict):
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe returned unexpected JSON. See log file: {_log_file_text()}")
        log_debug(
            f"Media Info ffprobe JSON decoded for {input_path}; "
            f"stdout length={len(stdout_text)} stderr length={len(stderr_text)}"
        )
        return payload
    except FFprobeError:
        raise
    except Exception as exc:
        log_ffprobe_diagnostics(
            input_path, ffprobe, args, "not available",
            stdout_text, stderr_text, decoded_using, exc,
        )
        raise FFprobeError(f"ffprobe could not read the file. See log file: {_log_file_text()}") from exc


def audio_hash(ffmpeg: str, input_path: Path, stream_index: int, duration: float | None = None) -> str | None:
    sample_seconds = max(1.0, DUPLICATE_AUDIO_HASH_SECONDS)
    starts = audio_hash_window_starts(duration, sample_seconds)
    parts: list[str] = []
    started_at = time.perf_counter()
    for start in starts:
        segment_hash = audio_hash_segment(ffmpeg, input_path, stream_index, start, sample_seconds)
        if not segment_hash:
            return None
        parts.append(segment_hash)
    log_debug(
        f"Quick audio hash stream #{stream_index}: windows={len(starts)} "
        f"seconds={sample_seconds:g} elapsed={time.perf_counter() - started_at:.3f}s"
    )
    return "|".join(parts)


def analyze_packet_bitrate(ffprobe: str, input_path: Path, csv_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    args = [
        ffprobe,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_packets",
        "-show_entries", "packet=pts_time,dts_time,duration_time,size,flags",
        "-of", "csv=p=0",
        str(input_path),
    ]
    returncode, stdout_text, _stderr_text = run_media_info_text_command(args, "Media Info per-second bitrate analysis")
    if returncode != 0:
        return None, "ffprobe packet analysis failed"
    buckets: dict[int, int] = {}
    for row in csv.reader(stdout_text.splitlines()):
        if not row:
            continue
        timestamp: float | None = None
        for item in row[:2]:
            try:
                timestamp = float(item)
                break
            except (TypeError, ValueError):
                continue
        size: int | None = None
        for item in row:
            try:
                number = int(float(item))
            except (TypeError, ValueError):
                continue
            if number > 1:
                size = number
        if timestamp is None or size is None:
            continue
        second = max(0, int(math.floor(timestamp)))
        buckets[second] = buckets.get(second, 0) + size
    if not buckets:
        return None, "packet timestamps or sizes were not available"
    rows = [
        {"second": second, "kbps": bytes_value * 8.0 / 1000.0, "bytes": bytes_value}
        for second, bytes_value in sorted(buckets.items())
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["second", "kbps", "bytes"])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "second": row["second"],
                "kbps": f"{row['kbps']:.3f}",
                "bytes": row["bytes"],
            })
    kbps_values = [float(row["kbps"]) for row in rows]
    summary = {
        "csv_path": csv_path,
        "rows": rows,
        "average_kbps": sum(kbps_values) / len(kbps_values),
        "minimum_kbps": min(kbps_values),
        "maximum_kbps": max(kbps_values),
        "p05_kbps": media_info_percentile(kbps_values, 5),
        "median_kbps": media_info_percentile(kbps_values, 50),
        "p95_kbps": media_info_percentile(kbps_values, 95),
        "seconds": len(rows),
    }
    return summary, None


def analyze_frame_types_and_gop(ffprobe: str, input_path: Path, csv_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    args = [
        ffprobe,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time,pkt_pts_time,pict_type,key_frame,pkt_size",
        "-of", "csv=p=0",
        str(input_path),
    ]
    returncode, stdout_text, _stderr_text = run_media_info_text_command(args, "Media Info frame type analysis")
    if returncode != 0:
        return None, "ffprobe frame analysis failed"
    frame_rows: list[dict[str, Any]] = []
    type_counts: dict[str, int] = {"I": 0, "P": 0, "B": 0}
    keyframe_indices: list[int] = []
    keyframe_times: list[float] = []
    for row in csv.reader(stdout_text.splitlines()):
        if not row:
            continue
        pict_type = next((item.strip().upper() for item in row if item.strip().upper() in {"I", "P", "B"}), "unknown")
        key_frame = "1" if any(item.strip() == "1" for item in row[:3]) else "0"
        time_value = next((media_info_csv_time(item) for item in row if media_info_csv_time(item) is not None), None)
        pkt_size = None
        for item in reversed(row):
            try:
                number = int(float(item))
            except (TypeError, ValueError):
                continue
            if number > 1:
                pkt_size = number
                break
        frame_index = len(frame_rows)
        if pict_type in type_counts:
            type_counts[pict_type] += 1
        if key_frame == "1":
            keyframe_indices.append(frame_index)
            if time_value is not None:
                keyframe_times.append(time_value)
        frame_rows.append({
            "time": f"{time_value:.6f}" if time_value is not None else "",
            "pict_type": pict_type,
            "key_frame": key_frame,
            "pkt_size": pkt_size if pkt_size is not None else "",
        })
    if not frame_rows:
        return None, "no frame rows were available"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["time", "pict_type", "key_frame", "pkt_size"])
        writer.writeheader()
        writer.writerows(frame_rows)
    total = len(frame_rows)
    gop_lengths = [
        keyframe_indices[index] - keyframe_indices[index - 1]
        for index in range(1, len(keyframe_indices))
    ]
    keyframe_intervals = [
        keyframe_times[index] - keyframe_times[index - 1]
        for index in range(1, len(keyframe_times))
        if keyframe_times[index] >= keyframe_times[index - 1]
    ]
    summary = {
        "csv_path": csv_path,
        "total_frames": total,
        "i_frames": type_counts.get("I", 0),
        "p_frames": type_counts.get("P", 0),
        "b_frames": type_counts.get("B", 0),
        "keyframes": len(keyframe_indices),
        "average_gop_frames": (sum(gop_lengths) / len(gop_lengths)) if gop_lengths else None,
        "minimum_gop_frames": min(gop_lengths) if gop_lengths else None,
        "maximum_gop_frames": max(gop_lengths) if gop_lengths else None,
        "first_keyframe_time": keyframe_times[0] if keyframe_times else None,
        "last_keyframe_time": keyframe_times[-1] if keyframe_times else None,
        "average_keyframe_interval": (sum(keyframe_intervals) / len(keyframe_intervals)) if keyframe_intervals else None,
        "minimum_keyframe_interval": min(keyframe_intervals) if keyframe_intervals else None,
        "maximum_keyframe_interval": max(keyframe_intervals) if keyframe_intervals else None,
    }
    return summary, None


def write_media_info_report(
    input_path: Path,
    lines: list[tuple[str, str]],
    payload: dict[str, Any],
    text_overview: str,
    info_path: Path,
) -> list[Path]:
    plain_report = render_info_report(lines, color=False)
    report_payload = media_info_payload_for_report(payload)
    raw_json = json.dumps(report_payload, ensure_ascii=False, indent=2)
    content = (
        plain_report
        + "\n\nRaw ffprobe JSON\n"
        + "-" * 48
        + "\n"
        + raw_json
        + "\n\nRaw ffprobe text overview\n"
        + "-" * 48
        + "\n"
        + text_overview.strip()
        + "\n"
    )
    info_path.write_text(content, encoding="utf-8")
    html_path = info_path.with_suffix(".html")
    html_report = render_info_report_html(lines, input_path, plain_report, raw_json, text_overview)
    html_path.write_text(html_report, encoding="utf-8")
    raw_json_path = media_info_sidecar_path(info_path, "raw_ffprobe", ".json")
    raw_json_path.write_text(raw_json, encoding="utf-8")
    log_info(f"Media Info report written: {info_path}")
    log_info(f"Media Info HTML report written: {html_path}")
    log_info(f"Media Info raw ffprobe JSON written: {raw_json_path}")
    log_debug(f"Media Info report size: {len(content)} characters for {input_path}")
    return [html_path, raw_json_path]


__all__ = [
    'split_part_output_paths',
    'enforce_bit_depth_compatible_video_encoder',
    'append_additional_source_video_maps',
    'build_encode_audio_processing_filter',
    'resolve_scale_dimensions',
    'ensure_ffmpeg_tools_installed',
    'ffprobe_full_json',
    'audio_hash',
    'analyze_packet_bitrate',
    'analyze_frame_types_and_gop',
    'write_media_info_report',
]



# ext01b holds the second half of this tier (split for file size). Re-export its
# names so consumers of `from ffmwiz.support.ext01 import *` see the full tier.
from ffmwiz.support import ext01b as _ext01b  # noqa: E402
# Guard the direct-import case: importing this overflow module FIRST
# re-enters the parent while the child has no __all__ yet.
from ffmwiz.support.ext01b import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_ext01b.__all__)


# ext01c holds an overflow slice of this module (split for file size).
from ffmwiz.support import ext01c as _ext01c  # noqa: E402
# Guard the direct-import case: importing this overflow module FIRST
# re-enters the parent while the child has no __all__ yet.
from ffmwiz.support.ext01c import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_ext01c.__all__)
