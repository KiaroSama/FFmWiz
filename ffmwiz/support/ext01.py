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


def metadata_stream_line(probe_json: dict[str, Any], stream: dict[str, Any]) -> str:
    codec_type = metadata_stream_type(stream)
    tags = metadata_tags(stream)
    parts = [
        field_text("stream index", metadata_stream_index(stream), Color.LIGHT_BLUE),
        field_text("type", codec_type, metadata_type_color(codec_type)),
        field_text("codec", stream.get("codec_name") or "unknown", Color.CYAN),
    ]
    if codec_type != "video":
        parts.extend([
            field_text("language", display_language(tags.get("language")), Color.GREEN),
            field_text("title", tags.get("title") or "unknown", Color.WHITE),
        ])
    parts.extend([
        field_text("disposition", metadata_disposition_summary(stream), Color.YELLOW),
        field_text("spec", metadata_stream_spec(probe_json, stream), Color.MAGENTA),
    ])
    if codec_type == "video":
        parts.extend([
            field_text("size", f"{stream.get('width', '?')}x{stream.get('height', '?')}", Color.LIME),
            field_text("pix_fmt", stream.get("pix_fmt") or "unknown", Color.ORANGE),
            field_text("color_range", stream.get("color_range") or "unknown", Color.COLOR_RANGE_VALUE),
            field_text("color_space", stream.get("color_space") or "unknown", Color.LIGHT_BLUE),
            field_text("color_transfer", stream.get("color_transfer") or "unknown", Color.PINK),
            field_text("color_primaries", stream.get("color_primaries") or "unknown", Color.AQUA),
        ])
    elif codec_type == "audio":
        parts.extend([
            field_text("sample_rate", stream.get("sample_rate") or "unknown", Color.MAGENTA),
            field_text("channels", stream.get("channels") or "unknown", Color.GREEN),
            field_text("channel_layout", stream.get("channel_layout") or "unknown", Color.AQUA),
        ])
    elif codec_type == "subtitle":
        parts.append(field_text("subtitle codec", stream.get("codec_name") or "unknown", Color.LIGHT_YELLOW))
    return " | ".join(str(part) for part in parts)


def metadata_output_path(input_path: Path, suffix: str, output_ext: str | None = None) -> Path:
    ext = output_ext or input_path.suffix or ".mkv"
    if ext and not str(ext).startswith("."):
        ext = "." + str(ext)
    candidate = input_path.with_name(f"{sanitize_output_stem(input_path.stem)}{suffix}{ext}")
    candidate = resolve_output_collision_against_inputs(candidate, [input_path], suffix or "_metadata")
    return unique_numbered_path(candidate)


def metadata_value_prompt(answers: dict[str, Any], title: str, allow_empty: bool = False) -> str:
    while True:
        value = appio.ask_raw(metadata_prompt(answers, title, back="back=b, quit=exit"))
        if value.lower().strip() in {"b", "back"}:
            raise Back()
        if value or allow_empty:
            return value
        appio.error("This value cannot be empty.")


def ask_lossless_split_ext(answers: dict[str, Any], codec_name: str) -> str:
    """Ask which output container extension to use for the lossless audio split,
    listing only extensions that can hold the source codec WITHOUT re-encoding.
    Caches the answer; raises Back on '0'."""
    choices = lossless_audio_copy_ext_choices(codec_name, Path(answers["input_path"]).suffix)
    default = str(answers.get("lossless_split_ext") or choices[0]).lower().lstrip(".")
    if default not in choices:
        choices = [default] + [c for c in choices if c != default]
    valid = {c.lower() for c in choices}
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Choose output extension for the split audio",
                f"lossless containers for {codec_name or 'this codec'} (no re-encode): {option_list(choices)}",
                default,
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = default
        ext = value.strip().lower().lstrip(".")
        if ext in valid:
            answers["lossless_split_ext"] = ext
            return ext
        appio.error(f"Enter one of: {', '.join(choices)} (these hold {codec_name or 'the source codec'} without re-encoding).")


def ask_manual_cut_layout(answers: dict[str, Any], allow_split: bool = False) -> int:
    """Ask which manual cut layout the user wants. Returns 1..4 (or 5 when
    allow_split and the user chooses lossless split)."""
    print()
    print(paint("Manual cut mode:", Color.BOLD + Color.LIGHT_BLUE))
    print(selection_menu_line(1, "Keep one range") + " " + paint("[1]", Color.GREEN))
    print(selection_menu_line(2, "Remove one range"))
    print(selection_menu_line(3, "Remove multiple ranges"))
    print(selection_menu_line(4, "Keep multiple ranges"))
    valid = {"1", "2", "3", "4"}
    if allow_split:
        print(selection_menu_line(5, "Split into separate files at given times (lossless)"))
        valid.add("5")
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
        if value in valid:
            return int(value)
        appio.error("Enter " + ", ".join(sorted(valid)) + ".")


def step_folder_input_path(answers: dict[str, Any]) -> None:
    while True:
        folder_example = example_text(r"D:\Videos\Season 01")
        value = appio.ask_required(
            appio.question_prompt(
                answers,
                "Enter input folder path",
                f"drag and drop a folder here or paste a path; example: {folder_example}",
            )
        )
        folder_path = terminal_path(value)
        if not folder_path.exists() or not folder_path.is_dir():
            appio.error("Folder not found. Enter the full folder path again.")
            continue
        answers["folder_input_path"] = folder_path
        return


def step_folder_output_location(answers: dict[str, Any]) -> None:
    input_folder: Path = answers["folder_input_path"]
    default_output = folder_default_output_path(input_folder)
    folder_example = example_text(r"E:\output")
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter output folder",
                f"Enter=create sibling folder named {default_output.name}; example: {folder_example}",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            output_folder = default_output
        else:
            output_folder = terminal_path(value)
            if not output_folder.is_absolute():
                output_folder = input_folder.parent / output_folder
        if output_folder.exists() and not output_folder.is_dir():
            appio.error("Output path exists and is not a folder. Enter a different folder.")
            continue
        answers["folder_output_location"] = output_folder
        answers["output_location"] = output_folder
        answers.pop("output_name_stem", None)
        return


def ask_metadata_for_added_stream(
    answers: dict[str, Any],
    stream_kind: str,
    stream_index: int,
    stream: dict[str, Any],
) -> dict[str, str]:
    current_language = display_language(stream_tag_value(stream, "language"))
    current_title = stream_tag_value(stream, "title")
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                f"Set {stream_kind} stream {stream_index} metadata",
                (
                    f"{example_text('language,title')} {paint('like', Color.HINT_YELLOW)} {example_text('eng,English')} ; "
                    f"{keep_value_text('Enter=keep current metadata')} ; "
                    f"{field_text('current language', current_language, Color.CYAN)} ; "
                    f"{field_text('title', current_title, Color.MAGENTA)}"
                ),
                None,
                "back=0, quit=exit",
            )
        )
        if is_back_value(value):
            raise RetryAdditionalFile()
        metadata = parse_add_track_metadata(value)
        if value and not metadata:
            appio.error("Enter metadata like eng,English or press Enter to keep current metadata.")
            continue
        return metadata


def describe_additional_track_file_colored(item: dict[str, Any]) -> str:
    parts: list[str] = []
    audio_count = len(item.get("audio_streams") or [])
    subtitle_count = len(item.get("subtitle_streams") or [])
    ignored_video_count = len(item.get("video_streams") or [])
    if audio_count:
        parts.append(field_text("audio streams", audio_count, Color.GREEN))
    if subtitle_count:
        parts.append(field_text("subtitle streams", subtitle_count, Color.MAGENTA))
    if ignored_video_count:
        parts.append(field_text("ignored cover/video streams", ignored_video_count, Color.ORANGE))
    return f" {paint('|', Color.GRAY)} ".join(parts) if parts else paint("no addable streams", Color.RED)


def build_track_manager_command(
    ffmpeg: str,
    input_path: Path,
    remove_specs: list[str],
    extra_items: list[dict[str, Any]],
    output_path: Path,
    answers: dict[str, Any] | None = None,
) -> list[str]:
    """Stream-copy command that maps all source streams except the removed ones
    and appends audio/subtitle streams from external files. Replace = remove the
    old track and add the new one in the same run.

    When loudnorm is enabled (answers['loudnorm_enabled']) the audio streams are
    re-encoded with the loudnorm filter while video/subtitles stay stream-copied.
    """
    cmd: list[str] = [ffmpeg, "-hide_banner", "-y" if OVERWRITE_OUTPUT else "-n", "-i", str(input_path)]
    for item in extra_items:
        cmd.extend(["-i", str(item["path"])])
    cmd.extend(["-map", "0"])
    for spec in remove_specs:
        cmd.extend(["-map", f"-0:{spec}"])
    for input_number, item in enumerate(extra_items, start=1):
        # Required maps (no trailing '?'): if the external file's audio/subtitle
        # stream is missing or undetectable, FFmpeg must fail loudly instead of
        # silently producing output without the replacement track. Map only the
        # FIRST stream of each kind (:a:0 / :s:0) so a multi-track external file
        # adds exactly one audio/subtitle track, matching the metadata prompt
        # which only configures stream 0.
        if item.get("audio_streams"):
            cmd.extend(["-map", f"{input_number}:a:0"])
        if item.get("subtitle_streams"):
            cmd.extend(["-map", f"{input_number}:s:0"])
    keep_metadata = True if answers is None else bool(answers.get("track_manager_keep_metadata", True))
    if keep_metadata:
        cmd.extend(["-map_metadata", "0"])
    else:
        # Drop container/global metadata, chapters, and every output stream's
        # metadata (titles, language tags) for a clean output.
        cmd.extend(["-map_metadata", "-1", "-map_chapters", "-1", "-map_metadata:s", "-1"])
    if answers is not None and loudnorm_transform_enabled(answers):
        # Copy everything, then override audio so loudnorm can re-encode it.
        # The later -c:a wins over the earlier -c copy for audio streams only.
        audio_codec = normalize_audio_codec(
            answers.get("audio_codec"),
            default_audio_codec_for_ext(output_path.suffix.lstrip(".")),
        )
        if audio_codec == "copy":
            audio_codec = DEFAULT_AUDIO_CODEC
        cmd.extend(["-c", "copy", "-c:a", audio_codec])
        bitrate = answers.get("audio_bitrate_kbps")
        if bitrate:
            cmd.extend(["-b:a", f"{int(bitrate)}k"])
        _ar = resolve_audio_sample_rate(answers)
        if _ar:
            cmd.extend(["-ar", str(_ar)])
        # -filter:a applies the loudnorm chain to every mapped audio stream.
        cmd.extend(["-filter:a", build_loudnorm_filter(answers)])
    else:
        cmd.extend(["-c", "copy"])
    cmd.append(str(output_path))
    return cmd


def choose_extract_stream_output_path(input_path: Path, stream: dict[str, Any], value: str, ext: str | None = None) -> Path:
    default_path = default_extract_stream_output_path(input_path, stream, ext)
    default_suffix = default_path.suffix
    if not value:
        candidate = default_path
    else:
        output_value = terminal_path(value)
        if not output_value.drive and not output_value.root and output_value.parent == Path("."):
            if output_value.suffix:
                candidate = input_path.parent / sanitize_output_stem(output_value.stem)
                candidate = candidate.with_suffix(output_value.suffix)
            else:
                candidate = input_path.parent / f"{sanitize_output_stem(output_value.name)}{default_suffix}"
        elif output_value.suffix:
            candidate = output_value.with_name(f"{sanitize_output_stem(output_value.stem)}{output_value.suffix}")
        else:
            candidate = output_value / default_path.name
    candidate = resolve_output_collision_against_inputs(candidate, [input_path], "_Extract")
    return unique_numbered_path(candidate)


def build_extract_jobs(
    entries: list[dict[str, Any]],
    requested: list[int],
    ext_override: str | None,
) -> tuple[list[dict[str, Any]], list[tuple[str, list[int], bool]], list[int]]:
    """Resolve the per-file extraction plan.

    Returns (jobs, per_file_summary, missing_everywhere).
      jobs: list of {path, stream, output_path, multi, fmt}
      per_file_summary: [(file_name, [extracted indexes], multi_bool)]
      missing_everywhere: requested indexes present in NO file
    A file yielding >= 2 streams gets its own "<file.name>" subfolder; a file
    yielding exactly one stream writes next to itself. When a single stream is
    extracted from a single file, ext_override (if any) is honored; otherwise
    each stream keeps its own copy-compatible default container.
    """
    jobs: list[dict[str, Any]] = []
    per_file: list[tuple[str, list[int], bool]] = []
    present_any: set[int] = set()
    single_file_single_stream = len(entries) == 1 and len([i for i in requested if i in entries[0]["by_index"]]) == 1
    for entry in entries:
        got = [i for i in requested if i in entry["by_index"]]
        present_any.update(got)
        if not got:
            continue
        matching = [entry["by_index"][i] for i in got]
        multi = len(matching) >= 2
        # A file with several extracted streams gets its own folder named exactly
        # after the file (e.g. "movie.mkv"). It lives under an "_Extracted" root
        # so the folder name never clashes with the source file that sits in the
        # same directory (a file and a folder cannot share a name).
        out_dir = (entry["path"].parent / "_Extracted" / entry["path"].name) if multi else entry["path"].parent
        for stream in matching:
            if ext_override and single_file_single_stream:
                ext = ext_override
            else:
                ext = extract_stream_container_options(stream)[1]
            idx = stream_global_index(stream)
            ctype = str(stream.get("codec_type") or "stream").lower()
            stem = f"{sanitize_output_stem(entry['path'].stem)}{EXTRACT_STREAM_OUTPUT_SUFFIX}{idx}_{ctype}"
            candidate = out_dir / f"{stem}.{ext.lstrip('.')}"
            candidate = resolve_output_collision_against_inputs(candidate, [entry["path"]], "_Extract")
            candidate = unique_numbered_path(candidate)
            jobs.append({"path": entry["path"], "stream": stream, "output_path": candidate, "multi": multi, "fmt": entry["format"]})
        per_file.append((entry["path"].name, got, multi))
    missing_everywhere = [i for i in requested if i not in present_any]
    return jobs, per_file, missing_everywhere


def print_extract_stream_summary(answers: dict[str, Any], jobs: list[dict[str, Any]]) -> None:
    files = sorted({str(job["path"]) for job in jobs})
    print()
    print(paint("Extract Stream summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("Files", len(files), Color.LIGHT_BLUE))
    print("  " + field_text("Streams to extract", len(jobs), Color.CYAN))
    for job in jobs:
        stream = job["stream"]
        idx = stream_global_index(stream)
        ctype = str(stream.get("codec_type") or "stream")
        label = f"{Path(job['path']).name} #{idx} {ctype}"
        out = Path(job["output_path"])
        # For multi-stream files show the "<file.ext>" subfolder + name; else just name.
        shown = f"{out.parent.name}/{out.name}" if job.get("multi") else out.name
        print("    " + field_text(label, shown, Color.WHITE))
    log_info("Extract Stream plan: " + json.dumps(
        [{"input": str(job["path"]), "index": stream_global_index(job["stream"]),
          "type": job["stream"].get("codec_type"), "codec": job["stream"].get("codec_name"),
          "output": str(job["output_path"])} for job in jobs],
        ensure_ascii=False,
    ))


def step_hardsub_output_location(answers: dict[str, Any]) -> None:
    folder_example = example_text(r"E:\output")
    value = appio.ask_raw(
        appio.question_prompt(
            answers,
            "Enter output path, output folder, or bare output name",
            f"Enter=same folder as input; default suffix {HARDSUB_OUTPUT_SUFFIX}; example: {folder_example}",
        )
    )
    if is_back_value(value):
        raise Back()
    apply_output_location_value(answers, value)


def step_hardsub_output_format(answers: dict[str, Any]) -> None:
    input_ext = answers["input_path"].suffix.lstrip(".") or "mkv"
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter final output format",
                f"common: {option_list(COMMON_VIDEO_FORMATS)}; {keep_value_text(f'n=Use input format ({input_ext})')}",
                "n",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        try:
            answers["output_ext"] = normalize_format(value, input_ext)
            return
        except ValueError as exc:
            appio.error(str(exc))


def step_hardsub_subtitle_source(answers: dict[str, Any]) -> None:
    subtitle_streams = answers.get("subtitle_streams") or []
    if subtitle_streams:
        print()
        print(paint("Internal subtitle streams", Color.BOLD + Color.MAGENTA))
        for index, stream in enumerate(subtitle_streams, start=1):
            print(
                f"  {paint(str(index) + '.', Color.LIGHT_BLUE)} "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('language', display_language(stream_tag_value(stream, 'language')), Color.WHITE)} | "
                f"{field_text('title', stream_tag_value(stream, 'title'), Color.WHITE)}"
            )
        while True:
            value = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    "Choose subtitle source",
                    "1=internal subtitle track; 2=external .srt/.ass/.ssa/.vtt/.webvtt file",
                    "1",
                )
            )
            if is_back_value(value):
                raise Back()
            if not value:
                value = "1"
            if value in {"1", "2"}:
                source = "internal" if value == "1" else "external"
                break
            appio.error("Enter 1 or 2.")
    else:
        appio.note("No internal subtitle streams were found; external subtitle file will be used.")
        source = "external"

    if source == "internal":
        while True:
            value = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    "Choose internal subtitle track",
                    f"1-{len(subtitle_streams)}; track number 1 is the first subtitle stream",
                    "1",
                )
            )
            if is_back_value(value):
                raise Back()
            if not value:
                value = "1"
            if re.fullmatch(r"\d+", value) and 1 <= int(value) <= len(subtitle_streams):
                selected = int(value) - 1
                codec = str(subtitle_streams[selected].get("codec_name", "")).lower()
                codec_error = hardsub_internal_subtitle_error(codec)
                if codec_error:
                    appio.error(codec_error)
                    continue
                answers["hardsub_subtitle_source"] = "internal"
                answers["hardsub_subtitle_index"] = selected
                answers["hardsub_subtitle_codec"] = codec
                return
            appio.error(f"Enter a number from 1 to {len(subtitle_streams)}.")

    while True:
        value = appio.ask_required(
            appio.question_prompt(
                answers,
                "Enter external subtitle file path",
                f"supported common files: {option_list(sorted(ext.lstrip('.') for ext in HARDSUB_SUBTITLE_EXTS))}",
            )
        )
        path = terminal_path(value)
        if not path.exists() or not path.is_file():
            appio.error("Subtitle file not found. Enter the full path again.")
            continue
        if not hardsub_external_subtitle_extension_supported(path):
            appio.error("Unsupported external subtitle extension. Use .srt, .ass, .ssa, .vtt, or .webvtt.")
            continue
        if path.suffix.lower() in {".ass", ".ssa"}:
            if ass_ssa_has_embedded_fonts(path):
                appio.note("This ASS/SSA file contains embedded fonts. fontsdir is optional.")
            else:
                appio.note("No embedded fonts were found. If this subtitle uses custom fonts that are not installed system-wide, provide a fonts directory.")
        answers["hardsub_subtitle_source"] = "external"
        answers["hardsub_subtitle_path"] = path
        return


def step_hardsub_fontsdir(answers: dict[str, Any]) -> None:
    fonts_example = example_text(r"D:\Subs\fonts")
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter subtitle fonts directory",
                f"Enter=none; optional for ASS/SSA embedded fonts and MKV font attachments; example: {fonts_example}",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            answers["hardsub_fontsdir"] = None
            return
        path = terminal_path(value)
        if not path.exists() or not path.is_dir():
            appio.error("Fonts directory not found. Enter an existing folder path or press Enter for none.")
            continue
        answers["hardsub_fontsdir"] = path
        return


def step_hardsub_video_codec(answers: dict[str, Any]) -> None:
    hdr_info = answers.get("hardsub_hdr_info") or {}
    default_codec = "H265" if hdr_info.get("hdr") or hdr_info.get("dolby") else source_video_codec_family(answers)
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter hard-sub video codec",
                f"common: {option_list(['H265', 'H264', 'AV1', 'VP9'])}; {keep_value_text(f'n=match source family ({default_codec})')}; copy is not possible for hard subtitles",
                "n",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value or value.lower() == "n":
            value = default_codec
        if value.lower() == "copy":
            appio.error("Hard subtitles require video re-encoding; copy is not valid here.")
            continue
        answers["video_codec"] = value
        return


def step_hardsub_quality(answers: dict[str, Any]) -> None:
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Choose hard-sub quality",
                colored_hardsub_quality_options(),
                "1",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "1"
        mapping = {"1": "near-lossless", "2": "high quality", "3": "balanced"}
        if value in mapping:
            answers["hardsub_quality_mode"] = mapping[value]
            return
        if value == "4":
            while True:
                custom = appio.ask_raw(
                    appio.question_prompt(
                        answers,
                        "Enter custom quality value",
                        "CRF/CQ integer 1-51; lower is higher quality",
                        "18",
                    )
                )
                if is_back_value(custom):
                    raise Back()
                if not custom:
                    custom = "18"
                if re.fullmatch(r"\d+", custom) and 1 <= int(custom) <= 51:
                    answers["hardsub_quality_mode"] = "custom"
                    answers["hardsub_quality_value"] = int(custom)
                    return
                appio.error("Enter an integer from 1 to 51.")
        else:
            appio.error("Enter 1, 2, 3, or 4.")


def step_hardsub_hdr_handling(answers: dict[str, Any]) -> None:
    hdr_info = answers.get("hardsub_hdr_info") or {}
    if not hdr_info.get("hdr") and not hdr_info.get("dolby"):
        answers["hardsub_hdr_handling"] = "standard"
        return
    print()
    print(paint("HDR / Dolby Vision detected", Color.BOLD + Color.ORANGE))
    print("  " + field_text("HDR", "yes" if hdr_info.get("hdr") else "no", Color.ORANGE))
    print("  " + field_text("Dolby Vision", "yes" if hdr_info.get("dolby") else "no", Color.ORANGE))
    print("  " + field_text("transfer", hdr_info.get("color_transfer"), Color.CYAN))
    print("  " + field_text("primaries", hdr_info.get("color_primaries"), Color.CYAN))
    print("  " + field_text("bit depth", hdr_info.get("bit_depth"), Color.PINK))
    if hdr_info.get("dolby"):
        appio.note("Dolby Vision dynamic metadata cannot be reliably preserved after hard-sub re-encoding; HDR10/static metadata can only be copied best-effort.")
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Choose HDR/Dolby handling",
                "1=preserve HDR metadata best effort; 2=tone-map to SDR; 3=standard encode without HDR-specific handling",
                "1",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "1"
        mapping = {"1": "preserve", "2": "tone-map", "3": "standard"}
        if value in mapping:
            answers["hardsub_hdr_handling"] = mapping[value]
            return
        appio.error("Enter 1, 2, or 3.")


def step_hardsub_audio_mode(answers: dict[str, Any]) -> None:
    if not answers.get("audio_streams"):
        answers["hardsub_audio_mode"] = "none"
        return
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Choose audio handling",
                colored_hardsub_audio_options(),
                "1",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "1"
        if value == "1":
            answers["hardsub_audio_mode"] = "copy-all"
            return
        if value == "3":
            answers["hardsub_audio_mode"] = "none"
            return
        if value == "2":
            answers["hardsub_audio_mode"] = "selected"
            answers["hardsub_audio_tracks"] = ask_selection(
                appio.question_prompt(
                    answers,
                    "Which audio tracks should be copied?",
                    f"example: {example_text('0,1')}; 0 is the first audio track here; back=b, quit=exit",
                    back="back=b, quit=exit",
                ),
                max_count=len(answers["audio_streams"]),
                default=[0],
                allow_none=True,
            )
            if answers["hardsub_audio_tracks"] == "all":
                answers["hardsub_audio_mode"] = "copy-all"
                answers.pop("hardsub_audio_tracks", None)
            return
        appio.error("Enter 1, 2, or 3.")


def print_startup_banner(config_path: Path, launcher_path: Path, answers: dict[str, Any] | None = None) -> None:
    _ = (config_path, launcher_path)
    startup_line("FFmpeg", "found.", Color.LIME)
    answers = answers or {}
    if answers.get("gpu_available"):
        model = answers.get("gpu_model") or "NVIDIA NVENC GPU"
        startup_line("GPU", f"detected - {model} (NVENC hardware encoding).", Color.GREEN, Color.GREEN)
    else:
        startup_line("GPU", "not detected - video will be encoded on the CPU.", Color.RED, Color.RED)


def build_join_copy_command(answers: dict[str, Any], items: list[dict[str, Any]], output_path: Path) -> list[str]:
    output_path = resolve_output_collision_against_inputs(
        output_path,
        [Path(item["path"]) for item in items if item.get("path")],
        answers.get("output_collision_suffix", "_Encode"),
    )
    answers["output_path"] = output_path
    list_path = write_join_concat_list(items, output_path)
    answers["_join_concat_list"] = list_path
    return [
        answers["ffmpeg"],
        "-hide_banner",
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-map",
        "0",
        "-c",
        "copy",
        str(output_path),
    ]


def print_join_summary(items: list[dict[str, Any]], copy_compatible: bool, reasons: list[str]) -> None:
    print()
    print(paint("Join summary:", Color.BOLD + Color.LIGHT_BLUE))
    for idx, item in enumerate(items, start=1):
        video_streams = item.get("video_streams") or []
        if video_streams:
            video = video_streams[0]
            fps = rational_to_float(video.get("avg_frame_rate")) or rational_to_float(video.get("r_frame_rate")) or 0.0
            detail = (
                f"{item['path'].name} | duration: {format_duration(item.get('duration'))} | "
                f"video: {video.get('codec_name', 'unknown')} {video.get('width', '?')}x{video.get('height', '?')} {fps:g} fps | "
                f"audio tracks: {len(item.get('audio_streams') or [])}"
            )
        else:
            audio_streams = item.get("audio_streams") or []
            codec = audio_streams[0].get("codec_name", "unknown") if audio_streams else "none"
            detail = (
                f"{item['path'].name} | duration: {format_duration(item.get('duration'))} | "
                f"audio-only: {codec} | audio tracks: {len(audio_streams)}"
            )
        print("  " + field_text(f"input {idx}", detail, Color.WHITE))
    if copy_compatible:
        print("  " + field_text("join mode", "stream copy, no re-encode", Color.GREEN))
    else:
        print("  " + field_text("join mode", "re-encode required", Color.ORANGE))
        for reason in reasons:
            print("    " + paint(reason, Color.YELLOW))


def ask_join_frame_rate_policy(answers: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    """Ask how to handle joined video inputs that have different frame rates.

    Only relevant for a video join of >=2 inputs whose frame rates differ.
    Default (yes) unifies every input to one frame rate: it then asks the target
    fps (defaulting to the highest source rate) and stores it in answers['fps'].
    Declining (no) marks the join as VFR (variable frame rate): each file keeps
    its own frame rate and the output has a variable frame rate. The unify
    question is asked first and the fps question comes after it, per design.

    Returns True when this join branch decided the fps (the caller must NOT ask
    the fps question again), False when the policy does not apply."""
    if len(items) < 2 or not join_frame_rates_differ(items):
        answers.setdefault("join_vfr", False)
        return False
    rates_text = ", ".join(f"{rate:g}" for rate in join_video_frame_rates(items))
    appio.note(f"Joined inputs have different frame rates ({rates_text} fps).")
    unify = appio.ask_yes_no(
        appio.question_prompt(answers, "Make all frame rates the same?", "y/n", "y"),
        True,
    )
    if not unify:
        answers["join_vfr"] = True
        answers["join_unify_fps"] = False
        answers["fps"] = None
        appio.note("VFR join: each file keeps its own frame rate; the output will have a variable frame rate.")
        return True
    answers["join_vfr"] = False
    answers["join_unify_fps"] = True
    highest = join_highest_frame_rate(items) or 30.0
    default_fps = max(1, int(round(highest)))
    prompt = appio.question_prompt(
        answers,
        "Enter frames per second for all joined videos",
        f"examples: {example_text('24,30,60')}; highest source is {format(highest, '.3g')}",
        str(default_fps),
    )
    while True:
        value = appio.ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        if not value:
            value = str(default_fps)
        if not re.fullmatch(r"\d+", value):
            appio.error("Enter an integer only.")
            continue
        number = int(value)
        if number == 0:
            raise Back()
        answers["fps"] = number
        return True


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
    'metadata_stream_line',
    'metadata_output_path',
    'metadata_value_prompt',
    'ask_lossless_split_ext',
    'ask_manual_cut_layout',
    'step_folder_input_path',
    'step_folder_output_location',
    'ask_metadata_for_added_stream',
    'describe_additional_track_file_colored',
    'build_track_manager_command',
    'choose_extract_stream_output_path',
    'build_extract_jobs',
    'print_extract_stream_summary',
    'step_hardsub_output_location',
    'step_hardsub_output_format',
    'step_hardsub_subtitle_source',
    'step_hardsub_fontsdir',
    'step_hardsub_video_codec',
    'step_hardsub_quality',
    'step_hardsub_hdr_handling',
    'step_hardsub_audio_mode',
    'print_startup_banner',
    'build_join_copy_command',
    'print_join_summary',
    'ask_join_frame_rate_policy',
]
