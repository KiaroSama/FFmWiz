"""FFmWiz second-layer helpers (appio-dependent), tier 2.

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
from ffmwiz.support.ext01 import *  # noqa: F401,F403


def resolved_video_encoder_for_nvenc_multipass(answers: dict[str, Any]) -> str:
    if not output_has_video(answers):
        return ""
    video_encoder, tag, profile = resolve_video_encoder(answers)
    if video_encoder == "copy" and video_filters_required(answers):
        fallback_answers = dict(answers)
        fallback_answers["video_codec"] = DEFAULT_VIDEO_CODEC
        video_encoder, tag, profile = resolve_video_encoder(fallback_answers)
    video_encoder, _tag, _profile = enforce_bit_depth_compatible_video_encoder(
        answers,
        video_encoder,
        tag,
        profile,
    )
    return video_encoder


def build_encode_audio_speed_filter(answers: dict[str, Any]) -> str:
    return build_encode_audio_processing_filter(answers)


def check_tools(interactive: bool = True) -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        ffmpeg, ffprobe = ensure_ffmpeg_tools_installed(interactive=interactive)
    if not ffmpeg:
        fail("ffmpeg is not installed or is not available in PATH. Install FFmpeg and add its bin folder to PATH.")
    if not ffprobe:
        fail("ffprobe was not found. Install the full FFmpeg package; ffprobe is normally included with it.")
    return ffmpeg, ffprobe


def ask_audio_sample_rate(answers: dict[str, Any], default_rate: int | None) -> None:
    """Prompt for the OUTPUT audio sample rate in Hz. Enter / 'n' keeps the
    current source rate (default_rate). Stores answers['audio_sample_rate']."""
    default_text = str(default_rate) if default_rate else "n"
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter audio sample rate in Hz",
                f"examples: {example_text('44100,48000,96000')}; "
                f"{keep_value_text('n=keep current rate' + (f' ({default_rate} Hz)' if default_rate else ''))}",
                default_text,
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = default_text
        if value.lower() in {"n", "keep"}:
            answers["audio_sample_rate"] = default_rate if default_rate else None
            answers["audio_sample_rate_keep"] = True
            log_info(f"User choice: audio_sample_rate=keep ({default_rate or 'source'} Hz)")
            return
        if not re.fullmatch(r"\d+", value):
            appio.error("Enter an integer sample rate in Hz (e.g. 48000), or n to keep the current rate.")
            continue
        rate = int(value)
        if rate < MIN_AUDIO_SAMPLE_RATE or rate > MAX_AUDIO_SAMPLE_RATE:
            appio.error(f"Enter a sample rate between {MIN_AUDIO_SAMPLE_RATE} and {MAX_AUDIO_SAMPLE_RATE} Hz.")
            continue
        # Warn (like video/audio bitrate, FPS and resolution) before accepting a
        # rate above the source: upsampling cannot add real audio detail and only
        # grows the file. For a join the reference is the highest source rate.
        if not confirm_numeric_target_not_above_source(
            answers,
            "audio sample rate",
            rate,
            default_rate,
            "highest source rate" if answers.get("join_input_items") else "source rate",
            "Hz",
            "upsampling cannot add real audio detail and only increases file size.",
        ):
            continue
        answers["audio_sample_rate"] = rate
        answers["audio_sample_rate_keep"] = False
        log_info(f"User choice: audio_sample_rate={rate} Hz")
        return


def build_cuda_video_filter(answers: dict[str, Any]) -> str | None:
    resolution = answers.get("resolution", "n")
    scale_dimensions = resolve_scale_dimensions(answers, resolution)
    cuda_format = cuda_pixel_format_for_output(answers)
    # scale_cuda gained reset_sar in the same 2025 commit as scale, so a pre-7.2
    # build rejects it and the whole encode fails at filter init. The CPU path
    # can fall back to a square-pixel pre-pass; inside a CUDA chain the frames
    # are already on the device, so the only safe downgrade is to omit the
    # option and leave the source SAR flag alone -- the picture is still
    # correct, it just keeps non-square pixels (D01).
    ffmpeg = answers.get("ffmpeg") or "ffmpeg"
    if filter_option_available(ffmpeg, "scale_cuda", "reset_sar"):
        sar_option = ":reset_sar=1"
    else:
        sar_option = ""
        log_info("FFmpeg scale_cuda has no reset_sar (pre-7.2 build); the GPU "
                 "scale keeps the source sample aspect ratio.")
    if scale_dimensions:
        width, height = scale_dimensions
        return (
            f"scale_cuda=w={width}:h={height}:format={cuda_format}:"
            f"interp_algo=bicubic:passthrough=0{sar_option}"
        )
    return f"scale_cuda=format={cuda_format}:passthrough=0{sar_option}"


def list_streams_for_selection(probe_json: dict[str, Any], streams: list[dict[str, Any]] | None = None) -> None:
    print()
    print(paint("Streams", Color.BOLD + Color.LIGHT_BLUE))
    for stream in (streams if streams is not None else (probe_json.get("streams") or [])):
        print("  " + metadata_stream_line(probe_json, stream))


def load_copy_cut_chapters(answers: dict[str, Any]) -> list[dict[str, Any]]:
    probe = answers.get("probe") if isinstance(answers.get("probe"), dict) else {}
    chapters = (probe or {}).get("chapters") or []
    if chapters:
        return [chapter for chapter in chapters if isinstance(chapter, dict)]

    ffprobe = answers.get("ffprobe")
    input_path = answers.get("input_path")
    if not ffprobe or not input_path:
        return []
    try:
        payload = ffprobe_full_json(str(ffprobe), Path(input_path))
    except Exception:
        log_exception(f"Copy Cut chapter probe failed for {input_path}")
        return []
    answers["probe"] = payload
    if payload.get("format"):
        answers["format"] = payload.get("format") or answers.get("format", {})
    chapters = payload.get("chapters") or []
    return [chapter for chapter in chapters if isinstance(chapter, dict)]


def run_folder_input_output_steps(answers: dict[str, Any]) -> None:
    while True:
        answers["_question_number"] = 1
        step_folder_input_path(answers)
        try:
            answers["_question_number"] = 2
            step_folder_output_location(answers)
            return
        except Back:
            continue


def run_folder_output_step_with_back(answers: dict[str, Any]) -> None:
    while True:
        try:
            answers["_question_number"] = 2
            step_folder_output_location(answers)
            return
        except Back:
            answers["_question_number"] = 1
            step_folder_input_path(answers)


def ask_additional_track_metadata(answers: dict[str, Any], item: dict[str, Any]) -> None:
    audio_metadata: list[dict[str, str]] = []
    subtitle_metadata: list[dict[str, str]] = []
    base_question_number = int(answers.get("_question_number", 2))

    for idx, stream in enumerate(item.get("audio_streams") or []):
        answers["_question_number"] = f"{base_question_number}.a{idx}"
        audio_metadata.append(ask_metadata_for_added_stream(answers, "audio", idx, stream))

    for idx, stream in enumerate(item.get("subtitle_streams") or []):
        answers["_question_number"] = f"{base_question_number}.s{idx}"
        subtitle_metadata.append(ask_metadata_for_added_stream(answers, "subtitle", idx, stream))

    answers["_question_number"] = base_question_number
    item["audio_metadata"] = audio_metadata
    item["subtitle_metadata"] = subtitle_metadata


def print_add_files_summary(
    answers: dict[str, Any],
    extra_items: list[dict[str, Any]],
    output_path: Path,
    cmd: list[str],
) -> None:
    print()
    print(paint("Add files to video summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("Input video", answers["input_path"], Color.WHITE))
    print("  " + field_text("Output", output_path, Color.LIME))
    print(paint("  Files to add:", Color.BOLD + Color.LIGHT_BLUE))
    for index, item in enumerate(extra_items, start=1):
        print(
            f"    {paint(str(index) + '.', Color.LIGHT_BLUE)} "
            f"{paint(str(item['path']), Color.WHITE)} | "
            f"{describe_additional_track_file_colored(item)}"
        )
        for audio_index, metadata in enumerate(item.get("audio_metadata") or []):
            print(
                f"       {paint('audio ' + str(audio_index), Color.BLUE)} "
                f"{paint(format_track_metadata(metadata), Color.WHITE)}"
            )
        for subtitle_index, metadata in enumerate(item.get("subtitle_metadata") or []):
            print(
                f"       {paint('subtitle ' + str(subtitle_index), Color.MAGENTA)} "
                f"{paint(format_track_metadata(metadata), Color.WHITE)}"
            )
    print()
    print(paint("Final PowerShell command:", Color.FINAL_COMMAND_LABEL))
    log_info("Final PowerShell command: " + command_to_powershell(cmd))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))


def step_extract_stream_index(answers: dict[str, Any]) -> None:
    entries = answers["_extract_files"]
    all_indices = sorted({i for e in entries for i in e["by_index"]})
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter stream index(es) to extract",
                "single 4; list 1,2,3; range 1-5; mix 1-5,6,8-10",
                back="back=b, quit=exit",
            )
        )
        lowered = value.strip().lower()
        if lowered in {"b", "back"}:
            raise Back()
        try:
            requested = parse_stream_index_spec(value)
        except ValueError as exc:
            appio.error(str(exc))
            continue
        jobs, per_file, missing = build_extract_jobs(entries, requested, None)
        if not jobs:
            appio.error(f"None of the requested stream(s) {requested} exist in any file. Available indexes: {all_indices}")
            continue
        answers["_extract_requested"] = requested
        print()
        print(paint("Extraction plan", Color.BOLD + Color.LIME))
        for name, got, multi in per_file:
            suffix = paint("  -> subfolder", Color.GRAY) if multi else ""
            print("  " + field_text(name, "stream(s) " + ",".join(str(i) for i in got), Color.LIGHT_BLUE) + suffix)
        if missing:
            appio.note("Requested stream index(es) not present in ANY file (skipped): "
                 + ",".join(str(i) for i in missing))
        skipped = [e["path"].name for e in entries if not any(i in e["by_index"] for i in requested)]
        if skipped:
            appio.note("Files with none of the requested streams (skipped): " + ", ".join(skipped))
        log_info(f"Extract spec: requested={requested}; jobs={len(jobs)}; missing={missing}")
        return


def step_extract_stream_format(answers: dict[str, Any]) -> None:
    """Ask the output container ONLY when exactly one stream from one file is
    extracted (single-line prompt, m4a default for common audio). When several
    streams are extracted, each keeps its own copy-compatible default container
    automatically (no prompt), since one container cannot fit mixed types."""
    entries = answers["_extract_files"]
    requested = answers["_extract_requested"]
    jobs, _per_file, _missing = build_extract_jobs(entries, requested, None)
    if len(jobs) == 1:
        stream = jobs[0]["stream"]
        options, default_ext = extract_stream_container_options(stream)
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter output container",
                f"no re-encode, copy-compatible: {','.join(options)}",
                default_ext,
            )
        ).strip().lower().lstrip(".")
        if is_back_value(value):
            raise Back()
        answers["_extract_ext"] = value or default_ext
        log_info(f"User choice: extract container={answers['_extract_ext']}; offered={options}")
    else:
        answers["_extract_ext"] = None
        appio.note("Each extracted stream keeps a copy-compatible container automatically (no re-encode).")


def step_extract_stream_start_now(answers: dict[str, Any]) -> None:
    entries = answers["_extract_files"]
    requested = answers["_extract_requested"]
    jobs, _per_file, _missing = build_extract_jobs(entries, requested, answers.get("_extract_ext"))
    if not jobs:
        raise ValueError("No streams matched the requested selection.")
    answers["_extract_jobs"] = jobs
    print_extract_stream_summary(answers, jobs)
    answers["start_now"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def step_hardsub_use_gpu(answers: dict[str, Any]) -> None:
    if not gpu_available_for_answers(answers):
        answers["use_gpu"] = False
        appio.note("No usable NVIDIA/NVENC GPU was detected. GPU question skipped; CPU mode selected.")
        log_info("User choice: use_gpu=False; reason=GPU unavailable")
        return
    answers["use_gpu"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Use GPU/NVIDIA encoder if available?", "y/n", "y"),
        True,
    )


__all__ = [
    'resolved_video_encoder_for_nvenc_multipass',
    'build_encode_audio_speed_filter',
    'check_tools',
    'ask_audio_sample_rate',
    'build_cuda_video_filter',
    'list_streams_for_selection',
    'load_copy_cut_chapters',
    'run_folder_input_output_steps',
    'run_folder_output_step_with_back',
    'ask_additional_track_metadata',
    'print_add_files_summary',
    'step_extract_stream_index',
    'step_extract_stream_format',
    'step_extract_stream_start_now',
    'step_hardsub_use_gpu',
]
