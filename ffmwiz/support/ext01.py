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
        appio.note(
            f"Source video is {source_depth}-bit. NVENC output is limited to 10-bit here, "
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


def ask_media_info_input_path(answers: dict[str, Any]) -> Path:
    while True:
        input_example = example_text(r"D:\Videos\input.mkv")
        value = appio.ask_required(
            appio.question_prompt(
                answers,
                "Enter file or folder path for media info",
                f"drag and drop a file/folder or paste a path; example: {input_example}",
            )
        )
        path = terminal_path(value)
        if not path.exists():
            appio.error("Path not found. Enter the full file or folder path again.")
            continue
        return path


def mux_print_header(text: str, color_code: str = Color.MUX_HEADER, char: str = "=") -> None:
    print()
    print(paint(mux_center_text(text), color_code))
    print(mux_separator_line(color_code, char))


def mux_print_setting(name: str, value: Any, value_color: str = Color.MUX_SETTING_VALUE) -> None:
    print("  " + mux_setting_text(name, value, value_color))


def mux_ask_csv_int_required(answers: dict[str, Any], title: str, available: list[int]) -> list[int]:
    available_set = set(available)
    while True:
        raw = mux_ask_text(
            answers,
            title,
            f"available: {example_text(mux_format_index_list(available))}; use b to go back",
            zero_is_value=True,
        )
        indexes = mux_parse_csv_int(raw)
        if not indexes:
            appio.error("Enter at least one stream index.")
            continue
        unknown = sorted(set(indexes) - available_set)
        if unknown:
            appio.error("These indexes were not found: " + mux_format_index_list(unknown))
            continue
        return indexes


def mux_ask_language_codes_required(answers: dict[str, Any], title: str, available: list[str] | None = None) -> list[str]:
    details = "example: jpn,eng,fas"
    if available:
        details += f"; available: {example_text(','.join(available))}"
    while True:
        values = [mux_normalize_language(value) for value in mux_parse_csv_text(mux_ask_text(answers, title, details))]
        if values:
            return values
        appio.error("Enter at least one language code.")


def mux_ask_output_base(answers: dict[str, Any], input_root: Path) -> Path:
    default_text = "Enter=input parent folder"
    prompt_number = mux_assign_prompt_number(answers)
    while True:
        answers["_question_number"] = prompt_number
        value = appio.ask_raw(appio.question_prompt(answers, "Enter output folder path", default_text))
        if is_back_value(value):
            raise Back()
        if not value:
            return input_root.parent
        if value.lower() in {"y", "yes", "n", "no", "y/n", "yes/no", "n/y", "no/yes"}:
            appio.error("Please enter a folder path, or press Enter to use the input parent folder.")
            continue
        path = terminal_path(value)
        if path.exists() and not path.is_dir():
            appio.error("Output path exists but is not a folder. Enter another path.")
            continue
        if path.suffix.lower() in MUX_CLEANUP_VIDEO_EXTS:
            appio.error("Output path must be a folder, not a media file name.")
            continue
        if path.suffix and not path.exists():
            appio.note(f"This output folder name has an extension: {path.name}")
            if not mux_ask_yes_no(answers, "Use this as a folder path?", False):
                continue
        if not path.is_absolute():
            resolved = (Path.cwd() / path).resolve()
            appio.note(f"Relative output folder will resolve to: {resolved}")
            if not mux_ask_yes_no(answers, "Use this relative output folder?", False):
                continue
            return resolved
        return path


def mux_copy_video_without_remux(input_file: Path, output_file: Path) -> None:
    if shutil.which(ROBOCOPY_BIN) is None:
        raise OSError("robocopy was not found in PATH")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    before = mux_destination_snapshot([output_file])
    args = [
        ROBOCOPY_BIN,
        str(input_file.parent),
        str(output_file.parent),
        input_file.name,
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
    ]
    result = mux_run_robocopy(args)
    if result.stdout.strip():
        log_debug("robocopy stdout: " + result.stdout.strip())
    if result.stderr.strip():
        log_debug("robocopy stderr: " + result.stderr.strip())
    after = mux_destination_snapshot([output_file])
    if not mux_robocopy_success(result.returncode):
        raise OSError(f"robocopy failed with exit code {result.returncode}")
    if output_file not in after:
        raise OSError("robocopy did not create the output file")
    if before.get(output_file) == after.get(output_file):
        log_info(f"robocopy copied unchanged video but destination metadata did not change: {output_file}")


def ask_mux_cleanup_input_path(answers: dict[str, Any]) -> Path:
    while True:
        value = appio.ask_required(
            appio.question_prompt(
                answers,
                "Enter file or folder path for Stream Cleanup Remux",
                f"drag/drop a video file or folder; supported: {option_list(sorted(ext.lstrip('.') for ext in MUX_CLEANUP_VIDEO_EXTS))}",
            )
        )
        path = terminal_path(value)
        if not path.exists():
            appio.error("Path not found. Enter a valid file or folder path.")
            continue
        return path


def gpu_available_for_answers(answers: dict[str, Any]) -> bool:
    if "gpu_available" in answers:
        return bool(answers.get("gpu_available"))
    available = detect_nvidia_gpu_available(str(answers.get("ffmpeg") or "ffmpeg"), list(answers.get("video_encoders") or []))
    answers["gpu_available"] = available
    return available


def confirm_numeric_target_not_above_source(
    answers: dict[str, Any],
    setting_label: str,
    target: float | int,
    source: float | int | None,
    source_label: str,
    unit: str,
    consequence: str,
) -> bool:
    if source is None or float(target) <= float(source):
        return True
    target_text = f"{format(target, '.3g')} {unit}" if isinstance(target, float) else f"{target} {unit}"
    source_text = f"{format(source, '.3g')} {unit}" if isinstance(source, float) else f"{source} {unit}"
    return confirm_target_above_source(answers, setting_label, target_text, source_text, source_label, consequence)


def confirm_resolution_not_above_source(answers: dict[str, Any], resolution: Any) -> bool:
    dimensions, _warning_text = calculate_scale_dimensions(answers, resolution)
    if dimensions is None:
        return True
    source, source_label = detected_resolution_limit(answers)
    if source is None:
        return True
    out_w, out_h = dimensions
    source_w, source_h = source
    if out_w <= source_w and out_h <= source_h:
        return True
    warning_key = f"{out_w}x{out_h}>{source_w}x{source_h}"
    if answers.get("_resolution_upscale_note_emitted") != warning_key:
        appio.note(
            "Resolution note: output resolution "
            f"{out_w}x{out_h} is above {source_label} {source_w}x{source_h}; "
            "this upscales pixels and can increase file size without adding real detail."
        )
        log_info(
            f"Accepted practical resolution upscale: target={out_w}x{out_h}; "
            f"source={source_w}x{source_h}; source_label={source_label}"
        )
        answers["_resolution_upscale_note_emitted"] = warning_key
    return True


def step_crop_top(answers: dict[str, Any]) -> None:
    while True:
        value = appio.ask_raw(appio.question_prompt(answers, "Enter crop top px", "integer pixels; zero is allowed", back="back=b, quit=exit"))
        if value.lower().strip() in {"b", "back"}:
            raise Back()
        if not re.fullmatch(r"\d+", value or ""):
            appio.error("Enter a non-negative integer.")
            continue
        if set_single_crop_margin_if_valid(answers, "crop_top", int(value)):
            return


def step_crop_left(answers: dict[str, Any]) -> None:
    while True:
        value = appio.ask_raw(appio.question_prompt(answers, "Enter crop left px", "integer pixels; zero is allowed", back="back=b, quit=exit"))
        if value.lower().strip() in {"b", "back"}:
            raise Back()
        if not re.fullmatch(r"\d+", value or ""):
            appio.error("Enter a non-negative integer.")
            continue
        if set_single_crop_margin_if_valid(answers, "crop_left", int(value)):
            return


def step_crop_right(answers: dict[str, Any]) -> None:
    while True:
        value = appio.ask_raw(appio.question_prompt(answers, "Enter crop right px", "integer pixels; zero is allowed", back="back=b, quit=exit"))
        if value.lower().strip() in {"b", "back"}:
            raise Back()
        if not re.fullmatch(r"\d+", value or ""):
            appio.error("Enter a non-negative integer.")
            continue
        if set_single_crop_margin_if_valid(answers, "crop_right", int(value)):
            return


def step_crop_bottom(answers: dict[str, Any]) -> None:
    while True:
        value = appio.ask_raw(appio.question_prompt(answers, "Enter crop bottom px", "integer pixels; zero is allowed", back="back=b, quit=exit"))
        if value.lower().strip() in {"b", "back"}:
            raise Back()
        if not re.fullmatch(r"\d+", value or ""):
            appio.error("Enter a non-negative integer.")
            continue
        if set_single_crop_margin_if_valid(answers, "crop_bottom", int(value)):
            return


def _step_video_constant_quality(answers: dict[str, Any]) -> None:
    """Ask for a CRF/CQ value for constant-quality encoding."""
    encoder, _, _ = resolve_video_encoder(answers)
    is_nvenc = str(encoder).endswith("_nvenc")

    if is_nvenc:
        label = "CQ"
        range_text = "0-51; 0 = lossless, 19-23 = visually good, 28-35 = smaller files"
        default = "23"
    else:
        label = "CRF"
        range_text = "0-51; 0 = lossless, 18-23 = visually good, 28-35 = smaller files"
        default = "23"

    appio.note(
        f"Constant Quality ({paint(label, Color.CYAN)}) mode: the encoder targets a perceptual quality level.\n"
        f"  Range: {paint(range_text, Color.HINT_YELLOW)}\n"
        f"  Lower = higher quality + larger file. Higher = lower quality + smaller file.\n"
        f"  Decimal values accepted (e.g. {paint('22.5', Color.LIME)}). Typical range for good quality: {paint('18-28', Color.GREEN)}."
    )

    prompt = appio.question_prompt(
        answers,
        f"Enter {label} value",
        f"range {example_text('0-51')}; examples: {example_text('18, 23, 28, 22.5')}",
        default,
    )
    while True:
        value = appio.ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        if not value:
            value = default
        try:
            crf = float(value)
        except (TypeError, ValueError):
            appio.error("Enter a number (integer or decimal).")
            continue
        if crf < 0 or crf > 51:
            appio.error("Value must be between 0 and 51.")
            continue
        answers["video_crf"] = crf
        answers.pop("video_bitrate_kbps", None)
        answers.pop("video_bitrate_keep", None)
        log_info(f"User choice: video constant quality {label}={crf}")
        return


def step_audio_codec(answers: dict[str, Any]) -> None:
    default_codec = default_audio_codec_for_ext(answers.get("output_ext", ""))
    value = appio.ask_raw(
        appio.question_prompt(
            answers,
            "Enter audio codec",
            f"common: {option_list(COMMON_AUDIO_CODECS)}; {keep_value_text('n=copy current audio stream without re-encoding')}",
            "n" if answers.get("_folder_encode_mode") else default_codec,
        )
    )
    if is_back_value(value):
        raise Back()
    if not value:
        value = "n" if answers.get("_folder_encode_mode") else default_codec
    if value.lower() == "n":
        value = "copy"
    value = normalize_audio_codec(value, default_codec)
    if loudnorm_transform_enabled(answers) and value.lower() == "copy":
        use_aac = appio.ask_yes_no(
            appio.question_prompt(
                answers,
                "LoudNorm requires audio re-encoding. Use AAC?",
                "y/n",
                "y",
            ),
            True,
        )
        if use_aac:
            value = DEFAULT_AUDIO_CODEC
        else:
            appio.note("LoudNorm disabled because audio remains stream-copy.")
            answers["loudnorm_enabled"] = False
    answers["audio_codec"] = normalize_audio_codec(value, default_codec)


def step_subtitle_tracks(answers: dict[str, Any]) -> None:
    streams = answers["subtitle_streams"]
    print()
    print(paint(f"Detected {len(streams)} subtitle track(s):", Color.BOLD + Color.WHITE))
    for idx, stream in enumerate(streams):
        print("  " + paint(stream_title(stream, idx), Color.WHITE))
    answers["subtitle_tracks"] = ask_selection(
        appio.question_prompt(
            answers,
            "Which subtitle tracks should be kept?",
            f"example: {example_text('0,1')}; Enter=track 0; n/all=all; none/clear=remove all; 0 is track 0 here",
            back="back=b, quit=exit",
        ),
        max_count=len(streams),
        default=[0],
        allow_none=True,
    )


def print_separator_summary(specs: list[dict[str, Any]]) -> None:
    if not specs:
        return
    print()
    print(paint("Split output plan:", Color.BOLD + Color.LIGHT_BLUE))
    for spec in specs:
        start, end = spec["segment"]
        print(
            "  "
            + field_text(f"#{spec['index']}", f"{seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}", Color.CYAN)
            + " | "
            + field_text("output", spec["output_path"], Color.LIME)
        )


def print_transform_summary(answers: dict[str, Any], cmd: list[str], title: str) -> None:
    print()
    print(paint(f"{title} summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("input", answers["input_path"], Color.WHITE))
    print("  " + field_text("output", answers["output_path"], Color.LIME))
    if "speed_factor" in answers:
        print("  " + field_text("speed", f"{float(answers['speed_factor']) * 100:.0f}%", Color.MAGENTA))
    if "audio_speed_factor" in answers:
        print("  " + field_text("audio speed", f"{float(answers['audio_speed_factor']) * 100:.0f}%", Color.MAGENTA))
    if "reverse_video" in answers:
        print("  " + field_text("reverse video", "yes" if answers.get("reverse_video") else "no", Color.ORANGE))
        print("  " + field_text("audio", "all tracks synced" if answers.get("include_audio") else "none", Color.BLUE))
        if answers.get("reverse_video"):
            appio.note(
                "FFmWiz runs reverse video in short segments to avoid buffering the whole clip in RAM. "
                "The single command above is an equivalent simple reference command."
            )
    if "reverse_audio" in answers:
        print("  " + field_text("reverse audio", "yes" if answers.get("reverse_audio") else "no", Color.ORANGE))
        print("  " + field_text("audio track", answers.get("audio_index", 0), Color.BLUE))
    if "audio_keep_ranges" in answers:
        print("  " + field_text("audio track", answers.get("audio_index", 0), Color.BLUE))
        print(paint(format_audio_ranges_for_summary(answers["audio_keep_ranges"], "audio keep ranges"), Color.LIME))
    if "audio_cut_keep_ranges" in answers:
        print("  " + field_text("audio track", answers.get("audio_index", 0), Color.BLUE))
        print(paint(format_audio_ranges_for_summary(answers["audio_cut_keep_ranges"], "audio keep ranges"), Color.LIME))
    print()
    print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
    log_info("Final PowerShell command: " + command_to_powershell(cmd))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))


def print_ffmpeg_processing_plan(
    answers: dict[str, Any],
    cmd: list[str],
    total_duration: float | None,
    processed_duration: float | None = None,
) -> None:
    text = " ".join(str(part) for part in cmd)
    has_nvdec = "-hwaccel cuda" in text
    has_cuda_frames = "-hwaccel_output_format cuda" in text
    has_nvenc = "_nvenc" in text
    has_complex = "-filter_complex" in cmd
    if has_cuda_frames:
        path = "CUDA fast path: NVDEC/CUDA decode -> CUDA filters -> NVENC encode"
    elif has_nvdec and has_nvenc and has_complex:
        path = "Hybrid GPU path: CUDA/NVDEC decode -> CPU filter graph -> NVENC encode"
    elif has_nvenc:
        path = "NVENC encode path: CPU decode/filter -> NVENC encode"
    elif has_complex:
        path = "CPU filter graph path"
    else:
        path = "Standard FFmpeg path"
    print("  " + field_text("processing path", path, Color.LIGHT_BLUE))
    log_info(f"FFmpeg processing path: {path}")
    if has_cuda_frames:
        print("  " + field_text("decode", "CUDA frames are kept on GPU; scale_cuda is used where scaling is needed", Color.CYAN))
        log_info("Command decision: CUDA fast path enabled; hwaccel_output_format cuda is intentional.")
    elif has_nvdec and has_nvenc and has_complex:
        print("  " + field_text("decode", "CUDA/NVDEC is used before each video input", Color.CYAN))
        print("  " + field_text("filters", "CPU filter_complex: crop/fps/scale/pad/concat/trim/speed/Split/audio filters", Color.ORANGE))
        print("  " + field_text("encode", "NVENC is used for final video encoding", Color.LIME))
        log_info("Command decision: complex graph uses CPU filters; CUDA decode-only enabled; hwaccel_output_format cuda intentionally omitted.")
        log_info("Command decision: scale_cuda/pad_cuda/hwdownload/hwupload_cuda intentionally not used in complex CPU graph.")
    elif has_nvenc:
        print("  " + field_text("encode", "NVENC is used for final video encoding", Color.LIME))
        log_info("Command decision: NVENC encode path without CUDA frame filtering.")
    elif has_complex:
        print("  " + field_text("filters", "CPU filter_complex", Color.ORANGE))
        log_info("Command decision: CPU filter graph path.")
    if answers.get("join_input_items"):
        print("  " + field_text("join", f"{len(answers.get('join_input_items') or []) + 1} input videos", Color.CYAN))
    if answers.get("cut_keep_ranges"):
        print("  " + field_text("cuts", f"{len(answers.get('cut_keep_ranges') or [])} keep range(s)", Color.ORANGE))
    if answers.get("separator_points"):
        print("  " + field_text("Split", f"{len(answers.get('separator_points') or []) + 1} output part(s)", Color.LIGHT_BLUE))
        if processed_duration and processed_duration > 0:
            print("  " + field_text("processed duration", format_elapsed(processed_duration), Color.CYAN))
            print("  " + field_text("progress basis", "aggregate Split timeline from encoded frames", Color.GRAY))
    if loudnorm_transform_enabled(answers):
        print("  " + field_text("LoudNorm", f"I={answers.get('loudnorm_target_i', LOUDNORM_DEFAULT_TARGET_I):g} LUFS", Color.MEAN_VOLUME))
    if total_duration and total_duration > 0:
        print("  " + field_text("progress duration", format_elapsed(total_duration), Color.MAGENTA))
    if has_complex:
        print("  " + field_text("startup phase", "decoding input, priming filter_complex, then feeding encoder", Color.GRAY))


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
    'ask_media_info_input_path',
    'mux_print_header',
    'mux_print_setting',
    'mux_ask_csv_int_required',
    'mux_ask_language_codes_required',
    'mux_ask_output_base',
    'mux_copy_video_without_remux',
    'ask_mux_cleanup_input_path',
    'gpu_available_for_answers',
    'confirm_numeric_target_not_above_source',
    'confirm_resolution_not_above_source',
    'step_crop_top',
    'step_crop_left',
    'step_crop_right',
    'step_crop_bottom',
    '_step_video_constant_quality',
    'step_audio_codec',
    'step_subtitle_tracks',
    'print_separator_summary',
    'print_transform_summary',
    'print_ffmpeg_processing_plan',
]


# ext01b holds the second half of this tier (split for file size). Re-export its
# names so consumers of `from ffmwiz.support.ext01 import *` see the full tier.
from ffmwiz.support import ext01b as _ext01b  # noqa: E402
from ffmwiz.support.ext01b import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_ext01b.__all__)
