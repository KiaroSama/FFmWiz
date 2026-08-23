"""FFmWiz extracted helper tier ext4 (post-services layer).

Imports core, support, and top-level ffmwiz modules; acyclic.
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

from ffmwiz.support.L01_cover import cover_art_method  # noqa: F401
from ffmwiz.support.ext04 import *  # sibling helpers  # noqa: F401,F403


def build_audio_transform_filter_complex(
    answers: dict[str, Any],
    audio_indices: list[int],
) -> tuple[str, list[str]]:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    keep_ranges = normalize_cut_ranges(list(answers.get("audio_cut_keep_ranges") or []), duration)
    parts: list[str] = []
    output_labels: list[str] = []
    for pos, audio_index in enumerate(audio_indices):
        current_label = f"0:a:{audio_index}"
        if keep_ranges:
            source_labels: list[str]
            if len(keep_ranges) > 1:
                source_labels = [f"acut{pos}_src{range_idx}" for range_idx in range(len(keep_ranges))]
                parts.append(
                    f"[{current_label}]asplit={len(keep_ranges)}"
                    f"{''.join(f'[{label}]' for label in source_labels)}"
                )
                log_info(
                    f"Inserted asplit={len(keep_ranges)} for multi-range audio trim from "
                    f"[{current_label}]."
                )
            else:
                source_labels = [current_label]
            range_labels: list[str] = []
            for range_idx, (start, end) in enumerate(keep_ranges):
                label = f"acut{pos}_{range_idx}"
                range_labels.append(f"[{label}]")
                parts.append(
                    f"[{source_labels[range_idx]}]atrim=start={start:.6f}:end={end:.6f},"
                    f"asetpts=PTS-STARTPTS[{label}]"
                )
            if len(keep_ranges) > 1:
                cut_label = f"acut{pos}"
                parts.append(f"{''.join(range_labels)}concat=n={len(keep_ranges)}:v=0:a=1[{cut_label}]")
                current_label = cut_label
            else:
                current_label = f"acut{pos}_0"
        out_label = f"aout{pos}"
        if audio_speed_transform_enabled(answers) or loudnorm_transform_enabled(answers):
            parts.append(f"[{current_label}]{build_encode_audio_speed_filter(answers)}[{out_label}]")
        elif current_label.startswith("0:"):
            parts.append(f"[{current_label}]anull[{out_label}]")
        else:
            parts.append(f"[{current_label}]asetpts=PTS-STARTPTS[{out_label}]")
        output_labels.append(out_label)
    return ";".join(parts), output_labels


def build_video_speed_reverse_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    answers["output_collision_suffix"] = speed_suffix(
        float(answers.get("speed_factor", DEFAULT_SPEED_FACTOR)),
        bool(answers.get("reverse_video")),
    )
    output_path = services.build_output_path(answers)
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    speed = clamp_speed_factor(answers.get("speed_factor", DEFAULT_SPEED_FACTOR))
    reverse = bool(answers.get("reverse_video"))
    include_audio = bool(answers.get("include_audio", True)) and bool(answers.get("audio_streams"))
    audio_count = len(answers.get("audio_streams") or [])

    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n", "-i", str(input_path)]
    cmd.extend(["-map", "0:v:0"])
    cmd.extend(["-sn", "-dn"])
    cmd.extend(["-filter:v", build_video_speed_filter(speed, reverse)])
    cmd.extend(["-c:v", "libx264", "-preset", CPU_PRESET, "-crf", "18", "-pix_fmt", cpu_pixel_format_for_output(answers)])
    if include_audio:
        labels: list[str] = []
        parts: list[str] = []
        for index in range(audio_count):
            label = f"aspd{index}"
            labels.append(label)
            parts.append(f"[0:a:{index}]{build_audio_speed_filter(speed, reverse)}[{label}]")
        cmd.extend(["-filter_complex", ";".join(parts)])
        for label in labels:
            cmd.extend(["-map", f"[{label}]"])
        cmd.extend(["-c:a", DEFAULT_AUDIO_CODEC, "-b:a", f"{DEFAULT_SPEED_AUDIO_BITRATE_KBPS}k"])
        channels = resolve_audio_channels(answers)
        if channels:
            cmd.extend(["-ac", str(channels)])
    else:
        cmd.append("-an")
    if answers["output_ext"].lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])
    cmd.append(str(output_path))
    log_info(
        f"Video speed/reverse command built: speed={speed}; reverse={reverse}; "
        f"include_audio={include_audio}; output={output_path}"
    )
    return cmd


def run_segmented_reverse_video_speed(answers: dict[str, Any]) -> tuple[int, float]:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    if duration <= 0:
        return run_ffmpeg_with_progress(
            answers["cmd"],
            total_duration=None,
            label="Video Speed / Reverse",
        )
    output_path = Path(answers["output_path"])
    speed = clamp_speed_factor(answers.get("speed_factor", DEFAULT_SPEED_FACTOR))
    chunks = split_ranges_for_reverse_segments([], duration)
    if not chunks:
        return 1, 0.0
    appio.note(
        f"Reverse mode uses {len(chunks)} segment(s) of up to {int(REVERSE_SEGMENT_SECONDS)}s "
        "to avoid buffering the full video in RAM."
    )
    started_at = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ffmwiz_reverse_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        segment_paths: list[Path] = []
        segment_ext = output_path.suffix.lstrip(".") or "mp4"
        for idx, (start, end) in enumerate(chunks, start=1):
            segment_path = tmpdir / f"reverse_seg_{idx:04d}.{segment_ext}"
            segment_paths.append(segment_path)
            cmd = build_video_speed_reverse_segment_command(answers, start, end, segment_path)
            log_info(f"Reverse segment {idx}/{len(chunks)} command: {command_to_powershell(cmd)}")
            appio.note(f"Reverse segment {idx}/{len(chunks)}: {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}")
            rc, _ = run_ffmpeg_with_progress(
                cmd,
                total_duration=max(0.001, (end - start) / speed),
                label=f"Reverse segment {idx}/{len(chunks)}",
            )
            if rc != 0:
                return rc, time.perf_counter() - started_at
        concat_list = tmpdir / "concat.txt"
        write_concat_list(list(reversed(segment_paths)), concat_list)
        concat_cmd = build_concat_copy_command(answers["ffmpeg"], concat_list, output_path)
        log_info("Reverse concat command: " + command_to_powershell(concat_cmd))
        appio.note("Concatenating reversed segments...")
        rc, _ = run_ffmpeg_with_progress(
            concat_cmd,
            total_duration=(duration / speed if duration > 0 else None),
            label="Reverse concat",
        )
        return rc, time.perf_counter() - started_at


def build_audio_speed_reverse_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    answers["output_ext"] = resolve_audio_tool_output_ext(answers)
    answers["output_collision_suffix"] = speed_suffix(
        float(answers.get("speed_factor", DEFAULT_SPEED_FACTOR)),
        bool(answers.get("reverse_audio")),
    )
    output_path = services.build_output_path(answers)
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    audio_index = int(answers.get("audio_index", 0))
    speed = clamp_speed_factor(answers.get("speed_factor", DEFAULT_SPEED_FACTOR))
    reverse = bool(answers.get("reverse_audio"))
    cmd: list[str] = [
        ffmpeg,
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-i",
        str(input_path),
        "-map",
        f"0:a:{audio_index}",
        *audio_tool_picture_args(answers, answers["output_ext"]),
        "-sn",
        "-dn",
        "-filter:a",
        build_audio_speed_filter(speed, reverse),
    ]
    cmd.extend(audio_tool_encode_options(
        answers["output_ext"],
        bitrate_kbps=resolve_audio_tool_bitrate_kbps(answers),
        sample_rate=resolve_audio_sample_rate(answers),
        channels=resolve_audio_tool_channels(answers),
    ))
    cmd.append(str(output_path))
    log_info(
        f"Audio speed/reverse command built: audio_index={audio_index}; "
        f"speed={speed}; reverse={reverse}; output={output_path}"
    )
    return cmd


def audio_tool_picture_args(answers: dict[str, Any], output_ext: str) -> list[str]:
    """Args that carry the source cover art through an audio tool, or ["-vn"].

    Mapping the picture only works for containers that store a cover AS a
    stream (mp4/m4a, mp3, flac). Opus/Ogg keep it in a base64 VorbisComment and
    reject a mapped image stream outright, and wav has no mechanism at all, so
    those still drop video.
    """
    method = cover_art_method(output_ext)
    if method not in {"attached_pic", "id3", "flac_stream"}:
        return ["-vn"]
    for position, stream in enumerate(answers.get("video_streams") or []):
        if (stream.get("disposition") or {}).get("attached_pic"):
            args = ["-map", f"0:v:{position}", "-c:v", "copy"]
            if method == "id3":
                args.extend(["-id3v2_version", "3"])
            args.extend(["-disposition:v", "attached_pic"])
            return args
    return ["-vn"]


def audio_cut_stream_copy_available(answers: dict[str, Any]) -> bool:
    """True when a single-range Audio Cut can be trimmed by stream copy.

    The tool asks for no encode setting, so re-encoding a track the chosen
    container already accepts only loses quality. An explicit bitrate or sample
    rate, loudnorm, or a second keep range all mean a filter graph is required.
    """
    if len(answers.get("audio_keep_ranges") or []) != 1:
        return False
    if loudnorm_transform_enabled(answers):
        return False
    if answers.get("audio_bitrate_kbps") not in (None, "", "n", "keep"):
        return False
    if answers.get("audio_sample_rate") not in (None, "", "n", "keep"):
        return False
    streams = answers.get("audio_streams") or []
    index = int(answers.get("audio_index", 0))
    if index >= len(streams):
        return False
    output_ext = str(answers.get("output_ext") or resolve_audio_tool_output_ext(answers)).lower().lstrip(".")
    return output_ext in lossless_audio_copy_ext_choices(str(streams[index].get("codec_name") or ""))


def build_audio_cut_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    answers["output_ext"] = resolve_audio_tool_output_ext(answers)
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    keep_ranges = normalize_cut_ranges(list(answers.get("audio_keep_ranges") or []), duration)
    if not keep_ranges:
        raise ValueError("No valid audio keep ranges were selected.")
    answers["audio_keep_ranges"] = keep_ranges
    answers["output_collision_suffix"] = "_AudioCut"
    output_path = services.build_output_path(answers)
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    audio_index = int(answers.get("audio_index", 0))
    picture_args = audio_tool_picture_args(answers, answers["output_ext"])
    stream_copy = bool(answers.get("audio_cut_stream_copy")) and audio_cut_stream_copy_available(answers)

    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n"]
    if len(keep_ranges) == 1:
        start, end = keep_ranges[0]
        if start > 0:
            cmd.extend(["-ss", f"{start:.6f}"])
        cmd.extend(["-i", str(input_path), "-t", f"{max(0.0, end - start):.6f}"])
        cmd.extend(["-map", f"0:a:{audio_index}", *picture_args, "-sn", "-dn"])
    else:
        cmd.extend(["-i", str(input_path)])
        parts: list[str] = []
        labels: list[str] = []
        source_labels = [f"acut_src{idx}" for idx in range(len(keep_ranges))]
        parts.append(
            f"[0:a:{audio_index}]asplit={len(keep_ranges)}"
            f"{''.join(f'[{label}]' for label in source_labels)}"
        )
        log_info(
            f"Inserted asplit={len(keep_ranges)} for multi-range audio trim from [0:a:{audio_index}]."
        )
        for idx, (start, end) in enumerate(keep_ranges):
            label = f"a{idx}"
            labels.append(f"[{label}]")
            parts.append(
                f"[{source_labels[idx]}]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS[{label}]"
            )
        parts.append(f"{''.join(labels)}concat=n={len(keep_ranges)}:v=0:a=1[a]")
        cmd.extend(["-filter_complex", ";".join(parts), "-map", "[a]", *picture_args, "-sn", "-dn"])

    if stream_copy:
        # Input seeking snaps the cut to the nearest audio packet; that is the
        # price of not re-encoding, and it is what the user was offered.
        cmd.extend(["-c:a", "copy", "-avoid_negative_ts", "make_zero"])
    else:
        cmd.extend(audio_tool_encode_options(
            answers["output_ext"],
            bitrate_kbps=resolve_audio_tool_bitrate_kbps(answers),
            sample_rate=resolve_audio_sample_rate(answers),
            channels=resolve_audio_tool_channels(answers),
        ))
    cmd.append(str(output_path))
    log_info(
        f"Audio cut command built: audio_index={audio_index}; ranges={keep_ranges}; "
        f"stream_copy={stream_copy}; output={output_path}"
    )
    return cmd


def apply_unified_video_editor_answers(answers: dict[str, Any]) -> None:
    if not answers.get("_unified_video_editor_used"):
        return
    if "_unified_video_speed" in answers or "_unified_reverse_video" in answers:
        speed = clamp_speed_factor(answers.get("_unified_video_speed", DEFAULT_SPEED_FACTOR))
        reverse = bool(answers.get("_unified_reverse_video"))
        answers["video_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
        answers["video_speed_factor"] = speed
        answers["reverse_video"] = reverse
        answers["audio_speed_from_video"] = bool(answers.get("_unified_include_audio"))
    if "_unified_cut_keep_ranges" in answers:
        duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
        duration += sum(float(item.get("duration") or 0.0) for item in answers.get("join_input_items") or [])
        answers["cut_keep_ranges"] = normalize_cut_ranges(answers.get("_unified_cut_keep_ranges") or [], duration)


def log_final_normalized_answers(answers: dict[str, Any], cmd: list[str]) -> None:
    try:
        input_paths = [str(answers.get("input_path"))]
        input_paths.extend(str(item.get("path")) for item in answers.get("join_input_items") or [] if item.get("path"))
        source_duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
        split_points = normalize_separator_points(answers.get("separator_points"), final_processed_duration_for_splits(answers, source_duration))
        split_intervals = separator_ranges(split_points, final_processed_duration_for_splits(answers, source_duration)) if split_points else []
        text = " ".join(str(part) for part in cmd)
        summary = {
            "input_paths": input_paths,
            "output_path": str(answers.get("output_path")),
            "split_output_paths": [str(path) for path in answers.get("split_output_paths") or []],
            "video_codec": answers.get("video_codec"),
            "source_bit_depth": describe_video_bit_depth(source_video_stream(answers) or {}),
            "output_bit_depth": output_video_bit_depth(answers) if output_has_video(answers) else None,
            "output_cpu_pixel_format": cpu_pixel_format_for_output(answers) if output_has_video(answers) else None,
            "output_cuda_pixel_format": cuda_pixel_format_for_output(answers) if output_has_video(answers) else None,
            "audio_codec": answers.get("audio_codec"),
            "selected_audio_tracks": selected_audio_streams(answers) if answers.get("audio_streams") else [],
            "additional_video_streams": len(additional_source_video_streams(answers)),
            "attachment_streams": len(embedded_attachment_streams(answers)),
            "data_streams": len(source_data_streams(answers)),
            "keep_source_metadata": source_metadata_keep_enabled(answers),
            "keep_source_chapters": source_chapters_keep_enabled(answers),
            "keep_source_subtitles": source_subtitles_keep_enabled(answers),
            "keep_source_data_streams": source_data_keep_enabled(answers),
            "keep_source_extra_video_streams": source_extra_video_keep_enabled(answers),
            "keep_embedded_attachments": bool(answers.get("keep_embedded_attachments")),
            "crop_enabled": bool(answers.get("crop_enabled")),
            "crop_margins": {
                "top": answers.get("crop_top", 0),
                "left": answers.get("crop_left", 0),
                "right": answers.get("crop_right", 0),
                "bottom": answers.get("crop_bottom", 0),
            },
            "crop_box_dimensions": answers.get("crop_box_dimensions"),
            "final_resolution": answers.get("final_resolution"),
            "fps": answers.get("fps"),
            "video_speed": encode_video_speed_factor(answers) if video_speed_transform_enabled(answers) else 1.0,
            "audio_speed": encode_audio_speed_factor(answers) if audio_speed_transform_enabled(answers) else 1.0,
            "reverse_video": bool(answers.get("reverse_video")),
            "reverse_audio": encode_audio_reverse_enabled(answers),
            "split_enabled": bool(split_points),
            "split_points": split_points,
            "split_intervals": split_intervals,
            "loudnorm_enabled": loudnorm_transform_enabled(answers),
            "loudnorm_target_i": answers.get("loudnorm_target_i"),
            "loudnorm_measured": answers.get("loudnorm_measured"),
            "loudnorm_applied_tracks": selected_audio_streams(answers) if loudnorm_transform_enabled(answers) and answers.get("audio_streams") else [],
            "gpu_requested": bool(answers.get("use_gpu")),
            "cuda_fast_path": "-hwaccel_output_format cuda" in text and "scale_cuda" in text,
            "complex_cpu_graph": "-filter_complex" in cmd,
            "gpu_decode_only": "-hwaccel cuda" in text and "-hwaccel_output_format cuda" not in text,
            "nvenc_encode": "_nvenc" in text,
            "nvenc_multipass": normalize_nvenc_multipass_mode(answers.get("nvenc_multipass")),
            "nvenc_multipass_skip_reason": answers.get("nvenc_multipass_skip_reason"),
            "scale_cuda_used": "scale_cuda" in text,
            "hwdownload_used": "hwdownload" in text,
            "hwupload_cuda_used": "hwupload_cuda" in text,
        }
        log_info("Final normalized answers before execution:\n" + json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    except Exception as exc:
        log_warn(f"Could not log final normalized answers: {exc}")


def run_cpu_two_pass_ffmpeg(
    cmd: list[str],
    answers: dict[str, Any],
    *,
    total_duration: float | None,
    progress_output_paths: list[Path],
) -> tuple[int, float]:
    first, second, passlog = build_cpu_two_pass_commands(cmd, answers)
    log_info("CPU two-pass encoding enabled.")
    log_command("CPU two-pass pass 1", first)
    log_command("CPU two-pass pass 2", second)
    try:
        print(paint("Starting FFmpeg pass 1/2...", Color.GREEN))
        rc1, elapsed1 = run_ffmpeg_with_progress(
            first,
            total_duration=total_duration,
            label="FFmpeg encode pass 1/2",
            initial_detail="CPU two-pass analysis pass",
        )
        if rc1 != 0:
            return rc1, elapsed1
        print()
        print(paint("Starting FFmpeg pass 2/2...", Color.GREEN))
        rc2, elapsed2 = run_ffmpeg_with_progress(
            second,
            total_duration=total_duration,
            label="FFmpeg encode pass 2/2",
            initial_detail="CPU two-pass final encode pass",
            progress_output_paths=progress_output_paths,
        )
        return rc2, elapsed1 + elapsed2
    finally:
        cleanup_cpu_two_pass_logs(passlog)


def _apply_manual_audio_transform(answers: dict[str, Any]) -> None:
    """Manual (non-GUI) audio transform with prompt-by-prompt Back navigation:
    optional cuts/lossless-split, then speed, then reverse. Back at the first
    prompt raises Back (to return to the editor menu); Back at a later prompt
    returns to the immediately previous prompt."""
    answers.pop("_audio_transform_split_points", None)
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    keep_ranges: list[tuple[float, float]] = []
    speed = 1.0
    reverse = False
    stage = "cuts_q"
    while True:
        if stage == "cuts_q":
            # Back here propagates to the editor menu.
            want = appio.ask_yes_no(yn_prompt("Add audio cuts or split into separate files?", False), False)
            if want:
                stage = "cut_detail"
            else:
                keep_ranges = []
                stage = "speed"
        elif stage == "cut_detail":
            try:
                kr = services.collect_cut_ranges_terminal(answers, 25.0, duration, allow_split=True)
            except Back:
                stage = "cuts_q"
                continue
            split_points = answers.pop("_manual_split_points", None)
            if split_points:
                # Lossless split: produce multiple files; speed/reverse do not apply.
                answers["_audio_transform_split_points"] = split_points
                answers["audio_cut_keep_ranges"] = []
                answers["audio_speed_enabled"] = False
                answers["reverse_audio"] = False
                answers["_audio_transform_noop"] = False
                log_info(f"Manual audio split points: {split_points}")
                return
            keep_ranges = kr
            if keep_ranges:
                print(paint(format_audio_ranges_for_summary(keep_ranges, "Audio cuts (keep ranges)"), Color.LIME))
            stage = "speed"
        elif stage == "speed":
            value = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    "Enter audio speed",
                    "examples: 150% or 1.5x or 1.5 (100% = no change)",
                    "100%",
                )
            )
            if is_back_value(value):
                stage = "cuts_q"  # Back -> previous prompt
                continue
            if not value:
                value = "100%"
            try:
                speed = parse_speed_factor(value)
            except ValueError as exc:
                appio.error(str(exc))
                continue  # re-ask speed
            stage = "reverse"
        else:  # reverse
            try:
                reverse = appio.ask_yes_no(yn_prompt("Reverse audio?", False), False)
            except Back:
                stage = "speed"  # Back -> previous prompt
                continue
            break
    if not (keep_ranges or reverse or abs(speed - 1.0) > 1e-6):
        appio.note("No audio transform was selected (no cuts, speed 100%, no reverse).")
        answers["_audio_transform_noop"] = True
        return
    answers["audio_cut_keep_ranges"] = keep_ranges
    answers["audio_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
    answers["audio_speed_factor"] = speed
    answers["reverse_audio"] = reverse
    answers["_audio_transform_noop"] = False
    log_info(f"Manual audio transform: cuts={keep_ranges}; speed={speed}; reverse={reverse}")


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


def build_join_audio_encode_command(answers: dict[str, Any], items: list[dict[str, Any]], output_path: Path) -> list[str]:
    """Audio-only Join re-encode: prepare each input's first audio track with
    the shared join audio preparation, concat them, and encode to AAC. Used when
    every joined input is audio-only and stream copy is not possible."""
    output_path = resolve_output_collision_against_inputs(
        output_path,
        [Path(item["path"]) for item in items if item.get("path")],
        answers.get("output_collision_suffix", "_Encode"),
    )
    answers["output_path"] = output_path
    bitrate = int(answers.get("audio_bitrate_kbps") or DEFAULT_AUDIO_BITRATE_KBPS)
    cmd: list[str] = [answers["ffmpeg"], "-hide_banner", "-y" if OVERWRITE_OUTPUT else "-n"]
    for item in items:
        cmd.extend(["-i", str(item["path"])])
    filters: list[str] = []
    inputs: list[str] = []
    prep = join_audio_prep_filter(join_target_sample_rate(answers),
                                  join_target_channel_layout(items))
    for idx, _item in enumerate(items):
        filters.append(f"[{idx}:a:0]{prep}[a{idx}]")
        inputs.append(f"[a{idx}]")
    filters.append(f"{''.join(inputs)}concat=n={len(items)}:v=0:a=1[acat]")
    # Apply the same transforms the wizard collected to the JOINED audio:
    # cut trims, then speed/reverse and loudnorm (no-ops for the menu-12 join,
    # which sets none of these).
    source_join_duration = sum(float(item.get("duration") or 0.0) for item in items)
    keep_ranges = normalize_cut_ranges(
        list(answers.get("cut_keep_ranges") or answers.get("audio_cut_keep_ranges") or []),
        source_join_duration,
    )
    label = append_join_trim_concat_filter(filters, "acat", keep_ranges, "audio", "acut")
    if audio_speed_transform_enabled(answers) or loudnorm_transform_enabled(answers):
        filters.append(f"[{label}]{build_encode_audio_speed_filter(answers)}[a]")
    else:
        filters.append(f"[{label}]asetpts=PTS-STARTPTS[a]")
    cmd.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[a]",
        "-vn", "-sn", "-dn",
        "-map_metadata", "-1", "-map_chapters", "-1",
    ])
    # The output extension comes from input 0, so a hardcoded `-c:a aac` sent
    # AAC into .flac / .ogg / .opus and the muxer refused the header.
    join_audio_args, join_audio_note = container_audio_encode_args(
        output_path.suffix, "aac", bitrate,
        channels=resolve_audio_channels(answers), sample_rate=join_target_sample_rate(answers))
    if join_audio_note:
        appio.note(join_audio_note)
    cmd.extend(join_audio_args)
    if output_path.suffix.lower() in {".mp4", ".m4a", ".mov"}:
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(str(output_path))
    return cmd


__all__ = [
    'build_audio_transform_filter_complex',
    'audio_tool_picture_args',
    'audio_cut_stream_copy_available',
    'build_video_speed_reverse_command',
    'run_segmented_reverse_video_speed',
    'build_audio_speed_reverse_command',
    'build_audio_cut_command',
    'apply_unified_video_editor_answers',
    'log_final_normalized_answers',
    'run_cpu_two_pass_ffmpeg',
    '_apply_manual_audio_transform',
    'write_metadata_report',
    '_capability_cache_view',
    'copy_cut_source_duration',
    'probe_additional_track_file',
    'extract_scan_files',
    'extract_describe_stream',
    'print_prerequisite_summary',
    'build_join_audio_encode_command',
]
