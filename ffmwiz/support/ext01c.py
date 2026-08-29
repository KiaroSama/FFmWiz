"""FFmWiz ext01 overflow (ext01c) — split for file size.

Back-imports ext01 and is re-exported by it, so every consumer of
`from ffmwiz.support.ext01 import *` still sees the full set. Monkeypatch-safe.
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
# The facade back-import was deleted: it carried no name this module does
# not already get from the lower tiers above, and it made the facade's
# `__all__` depend on which side was imported first.


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
    'ask_media_info_input_path',
    'mux_print_header',
    'mux_print_setting',
    'mux_ask_csv_int_required',
    'mux_ask_language_codes_required',
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
