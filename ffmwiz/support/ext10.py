"""FFmWiz extracted helper tier ext10 (post-services layer).

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
from ffmwiz.support.ext04 import *  # noqa: F401,F403
from ffmwiz.support.ext05 import *  # noqa: F401,F403
from ffmwiz.support.ext06 import *  # noqa: F401,F403
from ffmwiz.support.ext07 import *  # noqa: F401,F403
from ffmwiz.support.ext08 import *  # noqa: F401,F403
from ffmwiz.support.ext09 import *  # noqa: F401,F403


def print_join_input_summary(answers: dict[str, Any]) -> None:
    """Compact, color-coded Join input summary printed before the output-format
    step. 'highest' and 'lowest' use distinct colors, file names use a distinct
    color, and each label is visually separated. Also shows the total raw
    (pre-cut) duration, approximate frame count, and audio volume extremes."""
    rows = join_input_media_stats(answers)
    if len(rows) < 2:
        return
    # Populate audio loudness (mean/max) so the summary can show volume
    # extremes, then rebuild rows so the freshly scanned values are included.
    ensure_join_volume_stats(answers)
    rows = join_input_media_stats(answers)
    # Keep the plain-text version for the log (and tests).
    plain = format_join_input_summary_lines(answers)

    print()
    print(paint("Join input summary", Color.BOLD + Color.JOIN_SUMMARY))
    print("  " + paint("Files selected:", Color.JOIN_LABEL) + " " + paint(str(len(rows)), Color.JOIN_COUNT))

    def print_metric(label: str, key: str, unit: str, render) -> None:
        result = _join_summary_minmax(rows, key)
        if result is None:
            print("  " + paint(f"{label}:", Color.JOIN_LABEL) + " " + paint("unavailable", Color.DIM))
            return
        (hi_value, hi_name), (lo_value, lo_name), unknown = result
        line = (
            "  " + paint(f"{label}:", Color.JOIN_LABEL) + " "
            + paint("highest", Color.JOIN_HIGH) + " "
            + paint(f"{render(hi_value)}{unit}", Color.JOIN_HIGH)
            + " " + paint(f"({hi_name})", Color.JOIN_FILE) + ", "
            + paint("lowest", Color.JOIN_LOW) + " "
            + paint(f"{render(lo_value)}{unit}", Color.JOIN_LOW)
            + " " + paint(f"({lo_name})", Color.JOIN_FILE)
        )
        if unknown:
            line += "; " + paint(f"{unknown} unknown", Color.DIM)
        print(line)

    print_metric("Video bitrate", "video_kbps", " kbps", lambda v: f"{int(v):,}")
    print_metric("Audio bitrate", "audio_kbps", " kbps", lambda v: f"{int(v):,}")
    print_metric("FPS", "fps", "", lambda v: f"{v:.3f}")

    duration_info = join_summary_total_duration(answers, rows)
    print("  " + paint("Total raw duration:", Color.JOIN_LABEL) + " "
          + paint(join_summary_duration_text(duration_info), Color.JOIN_DURATION))

    volume = join_summary_volume_extremes(rows)
    if volume is None:
        print("  " + paint("Mean volume:", Color.JOIN_LABEL) + " " + paint("unavailable", Color.DIM))
        print("  " + paint("Max volume:", Color.JOIN_LABEL) + " " + paint("unavailable", Color.DIM))
    else:
        if volume["lowest_mean"]:
            value, name = volume["lowest_mean"]
            print("  " + paint("Mean volume:", Color.JOIN_LABEL) + " "
                  + paint("lowest", Color.JOIN_VOL_LOW) + " " + paint(f"{value:.1f} dB", Color.JOIN_VOL_LOW)
                  + " " + paint(f"({name})", Color.JOIN_FILE))
        else:
            print("  " + paint("Mean volume:", Color.JOIN_LABEL) + " " + paint("unavailable", Color.DIM))
        if volume["highest_max"]:
            value, name = volume["highest_max"]
            print("  " + paint("Max volume:", Color.JOIN_LABEL) + " "
                  + paint("highest", Color.JOIN_VOL_HIGH) + " " + paint(f"{value:.1f} dB", Color.JOIN_VOL_HIGH)
                  + " " + paint(f"({name})", Color.JOIN_FILE))
        else:
            print("  " + paint("Max volume:", Color.JOIN_LABEL) + " " + paint("unavailable", Color.DIM))

    log_info("Join input summary: " + " | ".join(plain[1:]))


def auto_select_audio_tracks(answers: dict[str, Any], mode: str) -> list[int]:
    count = len(answers.get("audio_streams", []))
    selected = set(range(count))
    report = detect_duplicate_audio(answers)
    lowered = mode.lower()

    if "d" in lowered:
        selected -= duplicate_tracks_to_drop(report)
    if "e" in lowered:
        selected -= set(report.get("empty_tracks", set()))
        selected -= set(report.get("near_empty_tracks", set()))

    if not selected and count:
        fallback = [idx for idx in range(count) if idx not in set(report.get("empty_tracks", set()))]
        selected.add(fallback[0] if fallback else 0)
        appio.note("Audio auto-selection would remove every track, so the safest remaining track was kept.")

    return sorted(selected)


def print_folder_media_summary(items: list[dict[str, Any]], base_answers: dict[str, Any]) -> None:
    print()
    print(paint("Folder media files", Color.BOLD + Color.LIGHT_BLUE))
    print(paint("-" * 48, Color.GRAY))
    ensure_folder_exact_packet_sizes(items, base_answers)
    audio_warnings: list[str] = []
    for index, item in enumerate(items, start=1):
        detail_answers = dict(base_answers)
        copy_media_metadata(detail_answers, item["answers"])
        detail_answers["detect_duplicate_audio"] = base_answers.get("detect_duplicate_audio", True)
        detail_answers["_quiet_packet_size_probe"] = True
        input_path: Path = detail_answers["input_path"]
        display_name = folder_item_display_name(item)
        fmt = detail_answers.get("format", {})
        packet_sizes = services.get_packet_sizes(detail_answers)
        duration = format_duration(services.stream_duration_seconds({}, fmt))
        print(
            f"  {paint(str(index) + '.', Color.LIGHT_BLUE)} "
            f"{paint(display_name, Color.WHITE)} | "
            f"{field_text('duration', duration, Color.MAGENTA)} | "
            f"{field_text('total bitrate', describe_total_bitrate(fmt), Color.YELLOW)}"
        )

        for video_idx, stream in enumerate(detail_answers.get("video_streams", [])):
            rate = stream_bitrate_kbps(stream, fmt, packet_sizes)
            size, estimated = stream_size_bytes(stream, fmt, packet_sizes)
            stream_duration = format_duration(services.stream_duration_seconds(stream, fmt))
            fps = rational_to_float(stream.get("avg_frame_rate"))
            width = stream.get("width", "?")
            height = stream.get("height", "?")
            estimate_label = " approx" if estimated and size else ""
            chapters_value, chapters_color = chapter_presence(detail_answers)
            print(
                f"     {paint('video ' + str(video_idx), Color.MAGENTA)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('size', str(width) + 'x' + str(height), Color.LIME)} | "
                f"{field_text('fps', format(fps, '.3g') if fps else 'unknown', Color.MAGENTA)} | "
                f"{field_text('bit depth', describe_video_bit_depth(stream), Color.PINK)} | "
                f"{field_text('Color range', display_color_range(stream.get('color_range')), Color.COLOR_RANGE_VALUE)} | "
                f"{field_text('duration', stream_duration, Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('video-only size', format_bytes(size) + estimate_label, Color.GREEN)} | "
                f"{field_text('total bitrate', describe_total_bitrate(fmt), Color.AQUA)} | "
                f"{field_text('chapters', chapters_value, chapters_color)}"
            )

        for audio_idx, stream in enumerate(detail_answers.get("audio_streams", [])):
            volume_stats = services.get_audio_volume_stats(detail_answers)
            item["answers"]["audio_volume_stats"] = volume_stats
            rate = stream_bitrate_kbps(stream, fmt, packet_sizes)
            size, estimated = stream_size_bytes(stream, fmt, packet_sizes)
            estimate_label = " approx" if estimated and size else ""
            print(
                f"     {paint('audio ' + str(audio_idx), Color.BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('sample_rate', stream.get('sample_rate', 'unknown'), Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('mean / max volume', audio_mean_max_volume_field(volume_stats, audio_idx), Color.MEAN_VOLUME)} | "
                f"{field_text('track size', format_bytes(size) + estimate_label, Color.LIME)}"
            )

        if detail_answers.get("audio_streams") and detail_answers.get("detect_duplicate_audio", True):
            report = detect_duplicate_audio(detail_answers)
            warning = folder_audio_issue_summary(display_name, report)
            if warning:
                audio_warnings.append(warning)
        if index < len(items):
            print()
    if audio_warnings:
        print()
        print(paint("Audio track warnings", Color.BOLD + Color.ORANGE))
        for warning in audio_warnings:
            print("  " + paint(warning, Color.ORANGE))
    print()


def step_audio_bitrate(answers: dict[str, Any]) -> None:
    first_selected = selected_audio_streams(answers)[0]
    packet_sizes = services.get_packet_sizes(answers)
    source = stream_bitrate_kbps(answers["audio_streams"][first_selected], answers.get("format"), packet_sizes)
    source_limit, source_limit_label = detected_audio_bitrate_limit(answers)
    # Join mode: the default keep-value should reflect the HIGHEST source audio
    # bitrate among all joined inputs (not just the first input), so the joined
    # program is not down-rated to the first clip's bitrate.
    answers.pop("_join_audio_bitrate_source_name", None)
    if answers.get("join_input_items"):
        join_max, join_source_name = join_max_source_audio_bitrate(answers, first_selected)
        if join_max:
            source = join_max
            answers["_join_audio_bitrate_source_name"] = join_source_name
            log_info(
                f"Join audio bitrate default uses highest source audio bitrate among joined inputs: "
                f"{join_max} kbps from {join_source_name}"
            )
        else:
            log_info("Join audio bitrate default: all joined source audio bitrates unknown; using safe default.")
    # The source bitrate is computed with real effort above -- including the
    # highest across joined inputs -- and used to be discarded unless it was
    # BELOW 128, so the prompt offered 128 for a 320 kbps track and pressing
    # Enter down-rated it by 60% (USER-5-3). Default to the source when it is
    # known; the clamp matters because a lossless source estimates in the four
    # figures, which is meaningless as a target for a lossy encoder.
    # confirm_numeric_target_not_above_source below still guards a user value
    # above the source, so defaulting to the source cannot inflate the file.
    # No floor here on purpose: raising the default ABOVE a very low source
    # would both inflate the file and trip confirm_numeric_target_not_above_source
    # below, leaving the prompt stuck asking to confirm its own default.
    if source:
        default_audio_bitrate = min(int(source), AUDIO_TOOL_MAX_BITRATE_KBPS)
    else:
        default_audio_bitrate = DEFAULT_AUDIO_BITRATE_KBPS
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter audio bitrate in kbps",
                f"examples: {example_text('64,96,128,160,192,256,320')}; "
                f"{keep_value_text('n=keep current value' + (' around ' + str(source) + 'k' if source else ''))}",
                str(default_audio_bitrate),
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = str(default_audio_bitrate)
        if value.lower() == "n":
            answers["audio_bitrate_kbps"] = source
            answers["audio_bitrate_keep"] = True
            return
        if not re.fullmatch(r"\d+", value):
            appio.error("Enter an integer only.")
            continue
        number = int(value)
        if number == 0:
            raise Back()
        if not confirm_numeric_target_not_above_source(
            answers,
            "audio bitrate",
            number,
            source_limit,
            source_limit_label,
            "kbps",
            "higher audio bitrate can increase file size without adding real source quality",
        ):
            continue
        answers["audio_bitrate_kbps"] = number
        answers["audio_bitrate_keep"] = False
        services.print_encode_size_estimate(answers, number, "audio")
        return


__all__ = [
    'print_join_input_summary',
    'auto_select_audio_tracks',
    'print_folder_media_summary',
    'step_audio_bitrate',
]
