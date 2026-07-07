"""FFmWiz trackmanager cluster (extracted from FFmWiz.py, method الف)."""
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
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz import runner  # noqa: F401
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz import runtime  # noqa: F401
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401


_COMMON_RATIOS: list[tuple[float, str]] = [
    (1.0, "1:1"), (4 / 3, "4:3"), (16 / 9, "16:9"), (9 / 16, "9:16"),
    (21 / 9, "21:9"), (3 / 2, "3:2"), (2 / 3, "2:3"), (5 / 4, "5:4"),
    (16 / 15, "16:15"), (64 / 45, "64:45"), (32 / 27, "32:27"),
    (8 / 9, "8:9"), (40 / 33, "40:33"), (2.0, "2:1"), (0.5, "1:2"),
    (2.39, "239:100"),
]


def ratio_to_pair(value: float | None, max_den: int = SAR_DAR_MAX_DENOMINATOR) -> tuple[int, int] | None:
    """Approximate a positive finite float as a reduced integer (num, den) pair.
    Snaps to a clean common ratio within tolerance; otherwise uses a bounded
    rational approximation reduced by GCD. Returns None for invalid input."""
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    for ratio, label in _COMMON_RATIOS:
        if abs(value - ratio) <= SAR_DAR_TOLERANCE:
            w, h = label.split(":")
            return int(w), int(h)
    frac = Fraction(value).limit_denominator(max_den)
    if frac.numerator <= 0 or frac.denominator <= 0:
        return None
    return frac.numerator, frac.denominator


def ratio_text(value: float | None) -> str:
    """Render a positive ratio float as a compact 'W:H' rational, or 'unknown'."""
    pair = ratio_to_pair(value)
    if not pair:
        return "unknown"
    return f"{pair[0]}:{pair[1]}"


def sar_dar_info(answers: dict[str, Any]) -> dict[str, Any]:
    """Resolve SAR/DAR geometry for the source video stream with explicit
    provenance. Reads only RAW ffprobe metadata (sample_aspect_ratio /
    display_aspect_ratio) from the stream, then delegates to the pure
    resolve_video_geometry() so calculated/fallback values can never be fed
    back in as detected metadata. Does not mutate the stream."""
    stream = source_video_stream(answers) or {}
    try:
        coded_w = int(stream.get("width") or 0)
        coded_h = int(stream.get("height") or 0)
    except (TypeError, ValueError):
        coded_w = coded_h = 0
    raw_ffprobe_sar = parse_rational(stream.get("sample_aspect_ratio"))
    raw_ffprobe_dar = parse_rational(stream.get("display_aspect_ratio"))

    geo = resolve_video_geometry(coded_w, coded_h, raw_ffprobe_sar, raw_ffprobe_dar)

    # Public result: the pure geometry plus backwards-compatible aliases used by
    # display, summary, command generation, and existing tests.
    info: dict[str, Any] = dict(geo)
    info["coded_w"] = geo["coded_width"]
    info["coded_h"] = geo["coded_height"]
    info["detected_sar"] = geo["raw_ffprobe_sar"]
    info["detected_dar"] = geo["raw_ffprobe_dar"]
    info["probe_dar"] = geo["raw_ffprobe_dar"]
    info["sar"] = geo["resolved_sar"]
    info["dar"] = geo["resolved_dar"]
    info["sar_text"] = ratio_text(geo["resolved_sar"])
    info["dar_text"] = ratio_text(geo["resolved_dar"])
    info["detected_sar_text"] = ratio_text(geo["raw_ffprobe_sar"])
    info["detected_dar_text"] = ratio_text(geo["raw_ffprobe_dar"])
    return info


def print_loudnorm_stats(stats: dict[str, float]) -> None:
    print()
    print(paint("Current audio loudnorm measurement:", Color.BOLD + Color.LIGHT_BLUE))
    # Integrated loudness is the main actionable value: its label is emphasized
    # (bold + emerald green) while the number keeps its original color.
    print(format_integrated_loudness_line(stats))
    print("  " + field_text("True peak", f"{stats['input_tp']:.1f} dBTP", Color.CYAN))
    print("  " + field_text("Loudness range", f"{stats['input_lra']:.1f} LU", Color.MAGENTA))
    print("  " + field_text("Threshold", f"{stats['input_thresh']:.1f} LUFS", Color.YELLOW))
    print("  " + field_text("Target offset", f"{stats['target_offset']:.1f} LU", Color.ORANGE))


def print_source_info(answers: dict[str, Any]) -> None:
    input_path: Path = answers["input_path"]
    fmt = answers.get("format", {})
    video_streams = answers.get("video_streams", [])
    audio_streams = answers.get("audio_streams", [])
    subtitle_streams = answers.get("subtitle_streams", [])
    file_size = input_path.stat().st_size if input_path.exists() else None
    packet_sizes = services.get_packet_sizes(answers)
    title = str(answers.get("_source_info_title") or "Source file info")
    sibling_streams = streams_for_statistics_from_answers(answers)

    print()
    print(paint(title, Color.BOLD + Color.LIGHT_BLUE))
    print(paint("-" * 48, Color.GRAY))
    print(field_text("Path", input_path, Color.WHITE))
    print(field_text("Container", fmt.get("format_name", "unknown"), Color.CYAN))
    print(field_text("Duration", format_duration(services.stream_duration_seconds({}, fmt)), Color.MAGENTA))
    print(field_text("File size", format_bytes(file_size), Color.LIME))
    print(field_text("Total bitrate", describe_total_bitrate(fmt), Color.YELLOW))

    if video_streams:
        print(paint("\nVideo streams", Color.BOLD + Color.MAGENTA))
        chapters_value, chapters_color = chapter_presence(answers)
        for idx, stream in enumerate(video_streams):
            rate = stream_bitrate_kbps(stream, fmt, packet_sizes, sibling_streams)
            size, estimated = stream_size_bytes(stream, fmt, packet_sizes, sibling_streams)
            duration = format_duration(services.stream_duration_seconds(stream, fmt))
            fps = rational_to_float(stream.get("avg_frame_rate"))
            width = stream.get("width", "?")
            height = stream.get("height", "?")
            estimate_label = " approx" if estimated and size else ""
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('size', str(width) + 'x' + str(height), Color.LIME)} | "
                f"{field_text('fps', format(fps, '.3g') if fps else 'unknown', Color.MAGENTA)} | "
                f"{field_text('bit depth', describe_video_bit_depth(stream), Color.PINK)} | "
                f"{field_text('duration', duration, Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('video-only size', format_bytes(size) + estimate_label, Color.GREEN)} | "
                f"{field_text('total bitrate', describe_total_bitrate(fmt), Color.AQUA)} | "
                f"{field_text('chapters', chapters_value, chapters_color)} | "
                f"{field_text('Color range', display_color_range(stream.get('color_range')), Color.COLOR_RANGE_VALUE)}"
            )
            if idx == 0:
                info = sar_dar_info(answers)
                coded = (
                    f"{info['coded_w']}x{info['coded_h']}"
                    if info.get("coded_w") and info.get("coded_h") else "unknown"
                )
                eff = f"{info['effective_dar_decimal']:.6f}" if info.get("effective_dar_decimal") else "unknown"
                sar_display = f"{info['sar_text']} ({info['sar_source']})"
                dar_display = f"{info['dar_text']} ({info['dar_source']})"
                print(
                    "     "
                    f"{field_text('coded resolution', coded, Color.LIME)} | "
                    f"{field_text('SAR', sar_display, Color.AQUA)} | "
                    f"{field_text('DAR', dar_display, Color.AQUA)} | "
                    f"{field_text('effective DAR', eff, Color.AQUA)} | "
                    f"{field_text('pixel shape', info['pixel_shape'], Color.PINK)}"
                )
                if info.get("warning"):
                    print("     " + field_text("geometry warning", info["warning"], Color.YELLOW))
                log_info(
                    "Source SAR/DAR: coded=%s; SAR=%s; DAR=%s; effective_DAR=%s; "
                    "pixel_shape=%s; DAR_source=%s"
                    % (coded, info['sar_text'], info['dar_text'], eff,
                       info['pixel_shape'], info['dar_source'])
                )
                if info.get("discrepancy"):
                    calc_dar, probe_dar = info["discrepancy"]
                    log_warn(
                        "SAR/DAR discrepancy: calculated DAR %.6f vs ffprobe DAR %.6f; "
                        "using calculated (coded resolution + SAR) as source of truth."
                        % (calc_dar, probe_dar)
                    )
        print(paint("\nAudio streams", Color.BOLD + Color.BLUE))
        volume_stats = services.get_audio_volume_stats(answers)
        report = detect_duplicate_audio(answers) if answers.get("detect_duplicate_audio", True) else None
        for idx, stream in enumerate(audio_streams):
            rate = stream_bitrate_kbps(stream, fmt, packet_sizes, sibling_streams)
            size, estimated = stream_size_bytes(stream, fmt, packet_sizes, sibling_streams)
            estimate_label = " approx" if estimated and size else ""
            labels = duplicate_labels(idx, report) if report else []
            label_text = f" | {' | '.join(labels)}" if labels else ""
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('channels', stream.get('channels', 'unknown'), Color.GREEN)} | "
                f"{field_text('sample_rate', stream.get('sample_rate', 'unknown'), Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('mean / max volume', audio_mean_max_volume_field(volume_stats, idx), Color.MEAN_VOLUME)} | "
                f"{field_text('track size', format_bytes(size) + estimate_label, Color.LIME)}{label_text}"
            )
        if report:
            print_audio_duplicate_report(answers, report)

    if subtitle_streams:
        print(paint("\nSubtitle streams", Color.BOLD + Color.WHITE))
        for idx, stream in enumerate(subtitle_streams):
            print(f"  {paint(str(idx), Color.LIGHT_BLUE)}: {paint(stream_title(stream, idx), Color.WHITE)}")
    print()


def step_loudnorm(answers: dict[str, Any]) -> None:
    answers.pop("loudnorm_enabled", None)
    answers.pop("loudnorm_target_i", None)
    answers.pop("loudnorm_measured", None)
    answers.pop("loudnorm_mode", None)
    selected = selected_audio_streams(answers) if answers.get("audio_streams") else []
    if not selected:
        return
    join_items = list(answers.get("join_input_items") or [])
    is_join = bool(join_items)

    # Explicit mode menu replaces the old yes/no + vague "measure?" prompt.
    if is_join:
        title = "Audio loudness normalization for joined output"
        opt2 = "Single-pass loudnorm after joining all audio"
        opt3 = "Two-pass loudnorm after joining all audio"
    else:
        title = "Audio loudness normalization"
        opt2 = "Single-pass loudnorm on final output audio"
        opt3 = "Two-pass loudnorm on final output audio"
    while True:
        print()
        print(paint(title + ":", Color.BOLD + Color.LIGHT_BLUE))
        print(selection_menu_line(1, "Off"))
        print(selection_menu_line(2, opt2))
        print(selection_menu_line(3, opt3))
        choice = appio.ask_raw(appio.question_prompt(answers, "Select an option", None, "1")).strip()
        if is_back_value(choice):
            raise Back()
        if not choice:
            choice = "1"
        if choice in {"1", "2", "3"}:
            break
        appio.error("Enter 1, 2, or 3.")
    if choice == "1":
        answers["loudnorm_enabled"] = False
        answers["loudnorm_mode"] = "off"
        log_info("User choice: loudnorm mode=off")
        return
    two_pass = choice == "3"

    # LoudNorm requires re-encoding audio; it cannot run on a stream copy.
    audio_codec = normalize_audio_codec(
        answers.get("audio_codec"),
        default_audio_codec_for_ext(answers.get("output_ext", "")),
    )
    answers["audio_codec"] = audio_codec
    if audio_codec == "copy":
        use_aac = appio.ask_yes_no(
            appio.question_prompt(answers, "LoudNorm requires audio re-encoding. Use AAC?", "y/n", "y"),
            True,
        )
        if not use_aac:
            appio.note("LoudNorm disabled because audio remains stream-copy.")
            answers["loudnorm_enabled"] = False
            answers["loudnorm_mode"] = "off"
            log_info("LoudNorm disabled: user kept audio stream-copy.")
            return
        answers["audio_codec"] = DEFAULT_AUDIO_CODEC
        answers.setdefault("audio_bitrate_kbps", DEFAULT_AUDIO_BITRATE_KBPS)

    measured: dict[str, float] | None = None
    # ALWAYS ask before measuring (default yes), for both single and two-pass.
    # Declining a two-pass measurement falls back to single-pass.
    measure_now = appio.ask_yes_no(
        yn_prompt("Measure current audio loudness first (to help choose a target)?", True),
        True,
    )
    if two_pass and not measure_now:
        appio.note("Two-pass loudnorm needs a measurement; using single-pass instead.")
        two_pass = False
    if measure_now:
        ffmpeg = str(answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg")
        audio_index = selected[0]
        while True:
            if is_join:
                ordered_items = join_ordered_items_for_answers(answers)
                total_duration = sum(float(it.get("duration") or 0.0) for it in ordered_items)
                appio.note(
                    f"Measuring loudness of the complete joined audio "
                    f"({len(ordered_items)} inputs) on track {audio_index}..."
                )
                measured = services.probe_join_loudnorm_measurement(
                    answers, ordered_items, audio_index,
                    target_i=LOUDNORM_DEFAULT_TARGET_I,
                    total_duration=(total_duration if total_duration > 0 else None),
                )
            else:
                appio.note(f"Measuring current loudness on audio track {audio_index}...")
                total_duration = services.stream_duration_seconds({}, answers.get("format"))
                measured = services.probe_loudnorm_measurement(
                    ffmpeg, Path(answers["input_path"]), audio_index,
                    target_i=LOUDNORM_DEFAULT_TARGET_I, total_duration=total_duration,
                )
            if measured is not None:
                print_loudnorm_stats(measured)
                break
            appio.error("LoudNorm measurement failed or its JSON output could not be parsed.")
            if not two_pass:
                # Single-pass does not need the measurement; continue to target.
                appio.note("Could not measure the current loudness; continuing to the target prompt.")
                measured = None
                break
            # Two-pass: never silently inject fake measured values.
            action = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    "LoudNorm measurement failed. Choose action",
                    "r=retry; c=continue with single-pass; m=cancel loudnorm",
                    "r",
                )
            ).strip().lower()
            if is_back_value(action):
                raise Back()
            if action == "m":
                answers["loudnorm_enabled"] = False
                answers["loudnorm_mode"] = "off"
                log_info("LoudNorm canceled after measurement failure.")
                return
            if action == "c":
                measured = None
                two_pass = False
                appio.note("Continuing with single-pass loudnorm (no measured values).")
                break
            if action != "r":
                appio.error("Enter r, c, or m.")

    # Target Integrated Loudness prompt (asked AFTER measurement for two-pass).
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter target Integrated Loudness I in LUFS",
                "examples: -16 general video, -18 safer/lower, -14 louder",
                loudnorm_number(LOUDNORM_DEFAULT_TARGET_I),
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = loudnorm_number(LOUDNORM_DEFAULT_TARGET_I)
        try:
            target_i = parse_loudnorm_target(value)
            break
        except ValueError as exc:
            appio.error(str(exc))

    answers["loudnorm_enabled"] = True
    answers["loudnorm_target_i"] = target_i
    if two_pass and measured is not None:
        answers["loudnorm_measured"] = measured
        answers["loudnorm_mode"] = "two_pass"
    else:
        answers.pop("loudnorm_measured", None)
        answers["loudnorm_mode"] = "single"
    log_info(
        f"User choice: loudnorm mode={answers['loudnorm_mode']}; target_i={target_i}; "
        f"join={'yes' if is_join else 'no'}"
    )


def print_track_list(answers: dict[str, Any]) -> None:
    streams = (answers.get("probe") or {}).get("streams") or []
    if not streams:
        # Fallback: rebuild from the categorized lists (absolute index may be absent).
        streams = (
            list(answers.get("video_streams") or [])
            + list(answers.get("audio_streams") or [])
            + list(answers.get("subtitle_streams") or [])
            + list(answers.get("data_streams") or [])
        )
    print()
    print(paint("Streams in this file:", Color.BOLD + Color.BLUE))
    for stream in streams:
        index = stream.get("index", "?")
        codec_type = stream.get("codec_type", "?")
        codec = stream.get("codec_name", "?")
        lang = (stream.get("tags") or {}).get("language", "")
        title = (stream.get("tags") or {}).get("title", "")
        extra = " | ".join(part for part in [f"lang={lang}" if lang else "", f"title={title}" if title else ""] if part)
        type_color = {
            "video": Color.CYAN, "audio": Color.GREEN, "subtitle": Color.MAGENTA,
        }.get(codec_type, Color.WHITE)
        print(
            "  " + paint(f"#{index}", Color.LIGHT_BLUE) + " "
            + paint(f"{codec_type}", type_color) + f" ({codec})"
            + (f" | {extra}" if extra else "")
        )
    print("  " + paint("Remove by absolute index (e.g. 2) or type:index (e.g. a:1, s:0).", Color.HINT_YELLOW))


def ask_track_manager_source(answers: dict[str, Any]) -> None:
    while True:
        value = appio.ask_required(
            appio.question_prompt(
                answers,
                "Enter source media file path",
                f"drag and drop a media file here or paste a path; example: {example_text('E:/Input/video.mkv')}",
            )
        )
        if is_back_value(value):
            raise Back()
        input_path = terminal_path(value)
        if not input_path.exists() or not input_path.is_file():
            appio.error("File not found. Enter the full file path again.")
            continue
        try:
            load_input_metadata(answers, input_path)
        except FFprobeError as exc:
            appio.error(str(exc))
            continue
        except Exception:
            log_exception(f"ffprobe metadata load failed for Track Manager source: {input_path}")
            appio.error(f"ffprobe could not read the file. See log file: {_log_file_text()}")
            continue
        return


def ask_track_remove_specs(answers: dict[str, Any], stream_count: int | None) -> list[str]:
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Stream(s) to REMOVE",
                "comma-separated; index like 2 or type:index like a:1; Enter = remove nothing",
                None,
            )
        )
        if is_back_value(value):
            raise Back()
        if not value.strip():
            return []
        try:
            return parse_track_remove_specs(value, stream_count)
        except ValueError as exc:
            appio.error(str(exc))


def run_track_manager_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_track_manager_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _track_manager_ask_loudnorm(answers: dict[str, Any], *, sample_path: Path | None = None) -> None:
    """Offer the same loudness-normalization options as the interactive wizard
    (Off / Single-pass / Two-pass) for the Track Manager. Reuses step_loudnorm
    so the prompts, measurement, and target handling stay identical.

    For folder mode a representative sample file is used for the optional
    two-pass measurement; the resulting values are reused for every file, so
    single-pass is the safer choice when the folder mixes loudness levels.
    """
    if sample_path is not None:
        appio.note("Two-pass measurement (if chosen) uses the first folder file as reference; "
             "single-pass is recommended when files differ in loudness.")
        probe = services.ffprobe_json(answers["ffprobe"], sample_path)
        answers["input_path"] = sample_path
        answers["format"] = probe.get("format", {})
        answers["audio_streams"] = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    if not answers.get("audio_streams"):
        # Nothing to normalize (e.g. the source had no audio); skip silently.
        answers["loudnorm_enabled"] = False
        answers["loudnorm_mode"] = "off"
        return
    # step_loudnorm needs a selected-audio set and an output extension; apply
    # loudnorm to every audio stream of the Track Manager output.
    answers["audio_tracks"] = "all"
    answers["output_ext"] = (sample_path or Path(answers.get("input_path") or "x.mkv")).suffix.lstrip(".") or "mkv"
    step_loudnorm(answers)


def _track_manager_collect_externals(answers: dict[str, Any]) -> list[dict[str, Any]]:
    if not appio.ask_yes_no(yn_prompt("Add a track from an external file?", True), True):
        return []
    try:
        return ask_additional_track_files(answers, [])
    except Back:
        return []


def _run_track_manager_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    print()
    print(paint("Track Manager (remove / add / replace tracks, and normalize audio loudness):", Color.BOLD + Color.LIGHT_BLUE))
    print(selection_menu_line(1, "Single file"))
    print(selection_menu_line(2, "Folder (apply the same change to every media file)"))
    while True:
        scope = appio.ask_raw(appio.question_prompt(answers, "Select an option", None, "1")).strip()
        if is_back_value(scope):
            raise Back()
        if not scope:
            scope = "1"
        if scope in {"1", "2"}:
            break
        appio.error("Enter 1 or 2.")

    if scope == "2":
        return _run_track_manager_folder(answers)
    return _run_track_manager_single(answers)


def _run_track_manager_single(answers: dict[str, Any]) -> tuple[int, float] | None:
    # Stage machine so Back ('0') goes ONE step back instead of cancelling the
    # whole mode: source -> remove -> externals -> loudnorm -> confirm.
    stage = "source"
    streams: list[dict[str, Any]] = []
    stream_count = 0
    remove_specs: list[str] = []
    extra_items: list[dict[str, Any]] = []
    while True:
        if stage == "source":
            # Back from the first prompt exits the mode (to the scope menu).
            ask_track_manager_source(answers)
            print_source_info(answers)
            print_track_list(answers)
            streams = (answers.get("probe") or {}).get("streams") or []
            stream_count = len(streams)
            stage = "remove"
        elif stage == "remove":
            try:
                specs = ask_track_remove_specs(answers, stream_count or None)
            except Back:
                stage = "source"
                continue
            # Prefer explicit type:index over a bare absolute index (e.g. -0:a:0).
            remove_specs = normalize_track_remove_specs(specs, streams)
            stage = "externals"
        elif stage == "externals":
            try:
                extra_items = _track_manager_collect_externals(answers)
            except Back:
                stage = "remove"
                continue
            stage = "loudnorm"
        elif stage == "loudnorm":
            try:
                _track_manager_ask_loudnorm(answers)
            except Back:
                stage = "externals"
                continue
            stage = "metadata"
        elif stage == "metadata":
            try:
                keep_meta = appio.ask_yes_no(
                    appio.question_prompt(
                        answers,
                        "Keep metadata in the output (container tags, chapters, stream titles/languages)?",
                        "y/n; n removes all source and added-track metadata for a clean output",
                        "y",
                    ),
                    True,
                )
            except Back:
                stage = "loudnorm"
                continue
            answers["track_manager_keep_metadata"] = keep_meta
            stage = "confirm"
        else:  # confirm
            if not remove_specs and not extra_items and not loudnorm_transform_enabled(answers):
                appio.note("No track was removed or added; nothing to do.")
                return None
            input_path = Path(answers["input_path"])
            output_path = track_manager_output_path(input_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            cmd = build_track_manager_command(answers["ffmpeg"], input_path, remove_specs, extra_items, output_path, answers)
            print()
            print(paint("Track Manager summary:", Color.BOLD + Color.LIME))
            print("  " + field_text("input", input_path, Color.WHITE))
            print("  " + field_text("remove", ", ".join(remove_specs) or "(none)", Color.ORANGE))
            print("  " + field_text("add external", ", ".join(Path(it["path"]).name for it in extra_items) or "(none)", Color.GREEN))
            print("  " + field_text("loudnorm", _track_manager_loudnorm_summary(answers), Color.GREEN))
            print("  " + field_text("metadata", "kept" if answers.get("track_manager_keep_metadata", True) else "removed", Color.ORANGE))
            print("  " + field_text("output", output_path, Color.LIME))
            print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
            log_info("Final PowerShell command: " + command_to_powershell(cmd))
            print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
            try:
                start_now = appio.ask_yes_no(appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"), True)
            except Back:
                stage = "metadata"  # Back -> previous step
                continue
            if not start_now:
                appio.note("FFmpeg was not started. The command above is ready to run manually.")
                return None
            print()
            print(paint("Starting FFmpeg...", Color.GREEN))
            duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
            return run_ffmpeg_with_progress(cmd, total_duration=(duration if duration > 0 else None), label="Track Manager")


def _run_track_manager_folder(answers: dict[str, Any]) -> tuple[int, float] | None:
    while True:
        value = appio.ask_required(
            appio.question_prompt(
                answers,
                "Enter folder path (every media file inside is processed)",
                "drag and drop a folder here or paste a path",
            )
        )
        if is_back_value(value):
            raise Back()
        folder = terminal_path(value)
        if not folder.exists() or not folder.is_dir():
            appio.error("Folder not found. Enter a valid folder path.")
            continue
        break
    media_files = sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in TRACK_MANAGER_MEDIA_EXTS
         and not looks_like_generated_output_file(p)),
        key=lambda p: p.name.lower(),
    )
    if not media_files:
        appio.error("No media files were found in that folder.")
        return None
    appio.note(f"Found {len(media_files)} media file(s) in the folder.")
    # For folder scope, removal uses type:index specs so it applies per file.
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Stream(s) to REMOVE from every file",
                "comma-separated type:index (e.g. a:1, s:0); Enter = remove nothing",
                None,
            )
        )
        if is_back_value(value):
            raise Back()
        if not value.strip():
            remove_specs: list[str] = []
            break
        try:
            remove_specs = parse_track_remove_specs(value)
            if any(re.fullmatch(r"\d+", spec) for spec in remove_specs):
                appio.error("Folder mode needs type:index specs (e.g. a:1), not absolute indexes (they differ per file).")
                continue
            break
        except ValueError as exc:
            appio.error(str(exc))
    extra_items = _track_manager_collect_externals(answers)
    _track_manager_ask_loudnorm(answers, sample_path=media_files[0])
    answers["track_manager_keep_metadata"] = appio.ask_yes_no(
        appio.question_prompt(
            answers,
            "Keep metadata in every output (container tags, chapters, stream titles/languages)?",
            "y/n; n removes all source and added-track metadata for clean outputs",
            "y",
        ),
        True,
    )
    if not remove_specs and not extra_items and not loudnorm_transform_enabled(answers):
        appio.note("No track was removed or added; nothing to do.")
        return None
    last_result: tuple[int, float] | None = None
    succeeded = 0
    for media in media_files:
        output_path = track_manager_output_path(media)
        cmd = build_track_manager_command(answers["ffmpeg"], media, remove_specs, extra_items, output_path, answers)
        appio.note(f"Processing: {media.name} -> {output_path.name}")
        print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
        rc, elapsed = run_ffmpeg_with_progress(cmd, total_duration=None, label=f"Track Manager: {media.name}")
        last_result = (rc, elapsed)
        if rc == 0:
            succeeded += 1
        else:
            appio.error(f"FFmpeg failed on {media.name} (exit {rc}).")
    appio.note(f"Track Manager folder run finished: {succeeded}/{len(media_files)} succeeded.")
    return last_result


__all__ = [
    'ask_track_manager_source',
    'ask_track_remove_specs',
    'print_loudnorm_stats',
    'print_source_info',
    'print_track_list',
    'ratio_text',
    'ratio_to_pair',
    'run_track_manager_mode',
    'sar_dar_info',
    'step_loudnorm',
    '_run_track_manager_folder',
    '_run_track_manager_mode_impl',
    '_run_track_manager_single',
    '_track_manager_ask_loudnorm',
    '_track_manager_collect_externals',
    '_COMMON_RATIOS',
]
