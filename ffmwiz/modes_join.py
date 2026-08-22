"""FFmWiz modes cluster (extracted from FFmWiz.py, method الف)."""
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


MEDIA_INFO_VALUE_COLORS = [
    Color.CYAN,
    Color.LIME,
    Color.MAGENTA,
    Color.YELLOW,
    Color.AQUA,
    Color.PINK,
    Color.LIGHT_BLUE,
    Color.ORANGE,
]


def build_join_near_quality_command(answers: dict[str, Any], items: list[dict[str, Any]], output_path: Path) -> list[str]:
    output_path = resolve_output_collision_against_inputs(
        output_path,
        [Path(item["path"]) for item in items if item.get("path")],
        answers.get("output_collision_suffix", "_Final"),
    )
    answers["output_path"] = output_path
    first_video = items[0]["video_streams"][0]
    format_answers = dict(answers)
    format_answers["video_streams"] = [first_video]
    output_pix_fmt = cpu_pixel_format_for_output(format_answers)
    nvenc_pix_fmt = "p010le" if output_video_bit_depth(format_answers) > 8 else "yuv420p"
    target_depth = output_video_bit_depth(format_answers)
    target_w = int(first_video.get("width") or 1280)
    target_h = int(first_video.get("height") or 720)
    target_fps = rational_to_float(first_video.get("avg_frame_rate")) or rational_to_float(first_video.get("r_frame_rate")) or 30.0
    available_video_encoders = {str(name).lower() for name in answers.get("video_encoders") or []}
    use_nvenc_encode = H264_NVENC_ENCODER in available_video_encoders and target_depth <= 8
    # h264_nvenc has no 10-bit mode; a >8-bit join falls through to the
    # software encoders below rather than failing at encoder init.
    use_cuda_decode_complex = bool(answers.get("use_gpu") and use_nvenc_encode)
    cmd: list[str] = [answers["ffmpeg"], "-hide_banner", "-y" if OVERWRITE_OUTPUT else "-n"]
    for item in items:
        if use_cuda_decode_complex:
            append_cuda_decode_args_for_input(cmd, answers)
        cmd.extend(["-i", str(item["path"])])
    filters: list[str] = []
    inputs: list[str] = []
    any_audio = any(item.get("audio_streams") for item in items)
    # VFR join re-encode: omit the per-input fps= filter (which would force CFR)
    # and let the output keep variable timing via -fps_mode vfr.
    vfr_join = bool(answers.get("join_vfr"))
    join_rate = join_target_sample_rate(answers)
    prep = join_audio_prep_filter(join_rate)
    for idx, item in enumerate(items):
        fps_prefix = "" if vfr_join else f"fps={target_fps:g},"
        filters.append(
            f"[{idx}:v:0]{fps_prefix}"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:reset_sar=1,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,"
            f"format={output_pix_fmt},setpts=PTS-STARTPTS[v{idx}]"
        )
        inputs.append(f"[v{idx}]")
        if any_audio and item.get("audio_streams"):
            filters.append(f"[{idx}:a:0]{prep}[a{idx}]")
            inputs.append(f"[a{idx}]")
        elif any_audio:
            duration = max(0.001, float(item.get("duration") or 0.001))
            filters.append(f"anullsrc=channel_layout=stereo:sample_rate={join_rate}:d={duration:.6f}[a{idx}]")
            inputs.append(f"[a{idx}]")
    filters.append(f"{''.join(inputs)}concat=n={len(items)}:v=1:a={1 if any_audio else 0}[v]{'[a]' if any_audio else ''}")
    cmd.extend(["-filter_complex", ";".join(filters), "-map", "[v]"])
    if any_audio:
        cmd.extend(["-map", "[a]"])
    else:
        cmd.append("-an")
    if use_nvenc_encode:
        log_info("Join Videos near-quality encode selected h264_nvenc because NVENC is available.")
        cmd.extend([
            "-c:v", "h264_nvenc",
            "-preset", NVENC_PRESET,
            "-tune", NVENC_TUNE,
            "-rc", "constqp",
        ])
        append_nvenc_multipass_args(cmd, answers, "h264_nvenc")
        cmd.extend([
            "-qp", "18",
            "-pix_fmt", nvenc_pix_fmt,
        ])
    elif target_depth > 10:
        cmd.extend(["-c:v", "libx265", "-preset", "slow", "-crf", "18", "-pix_fmt", output_pix_fmt])
        cmd.extend(["-profile:v", hevc_profile_for_output(format_answers, "main")])
    else:
        cmd.extend(["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", output_pix_fmt])
    if any_audio:
        near_quality_audio, near_quality_note = container_audio_encode_args(
            output_path.suffix, "aac", 192, channels=2)
        if near_quality_note:
            appio.note(near_quality_note)
        cmd.extend(near_quality_audio)
    if vfr_join:
        # Preserve variable timing across segments instead of resampling to CFR.
        cmd.extend(["-fps_mode", "vfr"])
    if output_path.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(str(output_path))
    return cmd


def run_join_videos_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    items: list[dict[str, Any]] = []
    try:
        need_file = True
        while True:
            if need_file:
                answers["_question_number"] = len(items) + 1
                label = "Enter first media file path (audio or video)" if not items else "Enter another media file path"
                hint = "drag and drop an audio or video file here or paste a path"
                if items:
                    hint += "; b=re-enter previous file"
                value = appio.ask_raw(
                    appio.question_prompt(answers, label, hint, back="back=0, quit=exit")
                )
                if is_back_value(value):
                    raise Back()
                if value.lower() in {"b", "back"}:
                    if items:
                        removed = items.pop()
                        appio.note(f"Removed previous file: {Path(removed['path']).name}. Re-enter it.")
                        continue
                    raise Back()
                if not value:
                    appio.error("This value cannot be empty. Enter a file path.")
                    continue
                path = terminal_path(value)
                if not path.exists() or not path.is_file():
                    appio.error("File not found. Enter the full file path again.")
                    continue
                if any(paths_same(path, it["path"]) for it in items):
                    appio.error("This file is already selected. Enter a different file.")
                    continue
                try:
                    item = services.join_load_media_item(answers, path, allow_audio_only=True)
                except Exception as exc:
                    log_exception(f"Join media probe failed: {path}")
                    appio.error(str(exc))
                    continue
                items.append(item)
            if len(items) >= 2:
                answers["_question_number"] = len(items) + 1
                more = wizard.ask_join_add_another(
                    appio.question_prompt(answers, "Add another media file?", "y/n", "n", back=JOIN_ADD_ANOTHER_BACK)
                )
                if more is False:
                    break
                if more == "folder":
                    answers["_question_number"] = len(items) + 1
                    folder = ask_join_folder_path(answers)
                    if folder is not None:
                        join_add_folder_items(answers, folder, items)
                    need_file = False
                    continue
                need_file = True
                continue
            need_file = True

        print_join_order_list([it["path"] for it in items])
        output_answers = dict(answers)
        output_answers["input_path"] = items[0]["path"]
        output_answers["probe"] = items[0]["probe"]
        output_answers["format"] = items[0]["format"]
        output_answers["video_streams"] = items[0]["video_streams"]
        output_answers["audio_streams"] = items[0]["audio_streams"]
        output_answers["subtitle_streams"] = [stream for stream in items[0].get("streams", []) if stream.get("codec_type") == "subtitle"]
        output_answers["attachment_streams"] = [stream for stream in items[0].get("streams", []) if stream.get("codec_type") == "attachment"]
        output_answers["data_streams"] = [stream for stream in items[0].get("streams", []) if stream.get("codec_type") == "data"]
        output_answers["join_input_items"] = items[1:]
        wizard.step_output_location(output_answers)
        answers.update({key: output_answers[key] for key in ("output_location", "output_name_stem", "output_used_default") if key in output_answers})
    except Back:
        appio.note("Returning to main menu.")
        return None

    output_path = join_default_output_path(answers, items[0]["path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Auto-detect whether this is an audio-only join or a video join.
    has_video_items = [it for it in items if it.get("video_streams")]
    audio_only_items = [it for it in items if not it.get("video_streams")]
    if has_video_items and audio_only_items:
        appio.error(
            "Cannot mix audio-only and video inputs in one join. "
            f"Audio-only: {', '.join(Path(it['path']).name for it in audio_only_items)}. "
            "Select all video files, or all audio files."
        )
        return None
    audio_only_join = not has_video_items
    if audio_only_join:
        appio.note("Detected audio-only inputs: performing an audio join.")

    # Variable frame rate policy: when joining videos with different frame rates,
    # ask whether to unify them (then ask the target fps) or keep them variable.
    if not audio_only_join:
        try:
            ask_join_frame_rate_policy(answers, items)
        except Back:
            appio.note("Returning to main menu.")
            return None

    copy_compatible, reasons = join_copy_compatibility(items)
    # A VFR join whose inputs match on everything except frame rate can be joined
    # with the concat demuxer (stream copy), which preserves each segment's own
    # frame rate and yields a genuine variable-frame-rate file with no re-encode.
    if (
        not copy_compatible
        and answers.get("join_vfr")
        and not audio_only_join
        and join_copy_compatible_except_fps(items)
    ):
        copy_compatible = True
        reasons = []
        appio.note("VFR join: using stream copy (concat) to preserve each file's frame rate.")
    print_join_summary(items, copy_compatible, reasons)
    if copy_compatible:
        cmd = build_join_copy_command(answers, items, output_path)
    elif audio_only_join:
        appio.note("These audio files cannot be joined with stream copy. Re-encoding to AAC is required.")
        # Default the audio bitrate to the highest known source among inputs.
        candidates: list[int] = []
        for item in items:
            astreams = item.get("audio_streams") or []
            if astreams:
                value = stream_bitrate_kbps(astreams[0], item.get("format"), services.get_packet_sizes(join_item_answers(answers, item)))
                if value:
                    candidates.append(int(value))
        answers["audio_bitrate_kbps"] = max(candidates) if candidates else DEFAULT_AUDIO_BITRATE_KBPS
        cmd = build_join_audio_encode_command(answers, items, output_path)
    else:
        appio.note("These files cannot be safely joined with stream copy. Re-encoding is required.")
        use_near = appio.ask_yes_no(
            appio.question_prompt(
                answers,
                "Encode with closest possible quality to the inputs?",
                "y/n",
                "y",
            ),
            True,
        )
        if not use_near:
            appio.note("Join was canceled before encoding.")
            return None
        first_video = items[0]["video_streams"][0]
        format_answers = dict(answers)
        format_answers["video_streams"] = [first_video]
        target_depth = output_video_bit_depth(format_answers)
        available_video_encoders = {str(name).lower() for name in answers.get("video_encoders") or []}
        if H264_NVENC_ENCODER in available_video_encoders and target_depth <= 8:
            ask_nvenc_multipass_if_applicable(
                answers,
                video_encoder="h264_nvenc",
                workflow_name="Join Videos near-quality",
                quality_oriented=True,
            )
        else:
            set_nvenc_multipass_skip_reason(answers, "CPU encoder selected")
        cmd = build_join_near_quality_command(answers, items, output_path)
    output_path = Path(answers.get("output_path") or output_path)
    answers["output_path"] = output_path
    log_info(f"Join command: {command_to_powershell(cmd)}")
    print()
    print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    start_now = appio.ask_yes_no(appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"), True)
    if not start_now:
        appio.note("FFmpeg was not started. The command above is ready to run manually.")
        cleanup_join_concat_list(answers)
        return None
    total_duration = sum(float(item.get("duration") or 0.0) for item in items)
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    try:
        return run_ffmpeg_with_progress(cmd, total_duration=(total_duration if total_duration > 0 else None), label="Join")
    finally:
        cleanup_join_concat_list(answers)


__all__ = [
    'build_join_near_quality_command',
    'run_join_videos_mode',
]
