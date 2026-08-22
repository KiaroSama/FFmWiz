"""FFmWiz metadata cluster (extracted from FFmWiz.py, method الف)."""
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
from ffmwiz.trackmanager import *  # noqa: F401,F403
from ffmwiz import trackmanager  # noqa: F401


def metadata_menu_selection(default: str = "1", back: str = "0=back, quit=exit") -> str:
    value = appio.ask_raw(f"{paint('Selection', Color.BOLD)} {paint('[' + default + ']', Color.GREEN)} {back_text(back)}: ")
    return default if value == "" else value


def metadata_refresh_probe(answers: dict[str, Any]) -> dict[str, Any]:
    probe = probe_media_json(answers["metadata_input_path"], answers.get("ffprobe"))
    answers["metadata_probe"] = probe
    return probe


def metadata_show_input_overview(answers: dict[str, Any]) -> None:
    probe = metadata_refresh_probe(answers)
    input_path = answers["metadata_input_path"]
    print()
    print(paint("Metadata Editor input:", Color.BOLD + Color.LIGHT_BLUE))
    print("  " + field_text("Path", input_path, Color.WHITE))
    fmt = probe.get("format") if isinstance(probe.get("format"), dict) else {}
    print("  " + field_text("Container", fmt.get("format_name") or "unknown", Color.CYAN))
    print("  " + field_text("Duration", format_duration(services.stream_duration_seconds({}, fmt)), Color.MAGENTA))
    print("  " + field_text("Chapters", len(probe.get("chapters") or []), Color.YELLOW))
    list_streams_for_selection(probe)


def metadata_set_current_input(answers: dict[str, Any], output_path: Path) -> None:
    if output_path.exists():
        answers["metadata_input_path"] = output_path
        metadata_refresh_probe(answers)
        appio.note(f"Metadata Editor current input is now: {output_path}")


def metadata_prompt_input(base_answers: dict[str, Any]) -> dict[str, Any]:
    answers = dict(base_answers)
    answers["_metadata_question_number"] = 0
    while True:
        value = appio.ask_required(
            metadata_prompt(answers, "Enter input media file path", r"drag and drop a file here or paste a path; example: D:\Videos\input.mkv"),
            allow_n=False,
        )
        input_path = terminal_path(value)
        if not input_path.exists() or not input_path.is_file():
            appio.error("Input file was not found.")
            continue
        answers["metadata_input_path"] = input_path
        metadata_show_input_overview(answers)
        return answers


def run_stream_metadata_editor(answers: dict[str, Any]) -> None:
    while True:
        print()
        print(paint("Stream Metadata Editor", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Edit stream title", default=True))
        print(metadata_menu_item(2, "Edit stream language"))
        print(metadata_menu_item(3, "Remove stream title"))
        print(metadata_menu_item(4, "Remove stream language"))
        print(metadata_menu_item(5, "Custom stream metadata key/value"))
        print(metadata_menu_item(6, "Remove custom stream metadata key"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            return
        if choice not in {"1", "2", "3", "4", "5", "6"}:
            appio.error("Enter a menu number from 1 to 6.")
            continue
        try:
            probe = metadata_refresh_probe(answers)
            stream = select_stream(probe, answers)
            spec = metadata_stream_spec(probe, stream)
            suffix = "_metadata_stream"
            if choice == "1":
                value = metadata_value_prompt(answers, "Enter new stream title")
                metadata_arg = f"title={value}"
                suffix = "_metadata_stream_title"
            elif choice == "2":
                appio.note("Language examples: Japanese=jpn, English=eng, Persian=per or fas, Arabic=ara, Korean=kor, Chinese=chi or zho.")
                value = metadata_value_prompt(answers, "Enter ISO 639-2 language code")
                metadata_arg = f"language={value}"
                suffix = "_metadata_language"
            elif choice == "3":
                metadata_arg = "title="
                suffix = "_metadata_title_removed"
            elif choice == "4":
                metadata_arg = "language="
                suffix = "_metadata_language_removed"
            elif choice == "5":
                key = metadata_value_prompt(answers, "Enter metadata key")
                value = metadata_value_prompt(answers, "Enter metadata value")
                metadata_arg = f"{key}={value}"
                suffix = "_metadata_custom"
            else:
                key = metadata_value_prompt(answers, "Enter metadata key to remove")
                metadata_arg = f"{key}="
                suffix = "_metadata_custom_removed"
            output_path = metadata_output_path(answers["metadata_input_path"], suffix)
            cmd = metadata_stream_copy_command(answers["ffmpeg"], answers["metadata_input_path"], output_path)
            cmd.extend([f"-metadata:s:{spec}", metadata_arg, str(output_path)])
            if runner.confirm_and_run_ffmpeg(answers, cmd, "Stream Metadata Editor", output_path):
                metadata_set_current_input(answers, output_path)
        except Back:
            continue


def run_stream_disposition_editor(answers: dict[str, Any]) -> None:
    while True:
        print()
        print(paint("Stream Disposition Editor", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Set audio stream as default", default=True))
        print(metadata_menu_item(2, "Remove default flag from audio stream"))
        print(metadata_menu_item(3, "Set subtitle stream as default"))
        print(metadata_menu_item(4, "Remove default flag from subtitle stream"))
        print(metadata_menu_item(5, "Set subtitle stream as forced"))
        print(metadata_menu_item(6, "Remove forced flag from subtitle stream"))
        print(metadata_menu_item(7, "Set commentary flag"))
        print(metadata_menu_item(8, "Remove commentary flag"))
        print(metadata_menu_item(9, "Set original flag"))
        print(metadata_menu_item(10, "Remove original flag"))
        print(metadata_menu_item(11, "Clear all disposition flags from selected stream"))
        print(metadata_menu_item(12, "Custom disposition flag editor"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            return
        if choice not in {str(i) for i in range(1, 13)}:
            appio.error("Enter a menu number from 1 to 12.")
            continue
        try:
            if choice == "12":
                available = metadata_dispositions_text(answers["ffmpeg"])
                if available:
                    print()
                    print(paint("Available FFmpeg dispositions:", Color.BOLD + Color.LIGHT_BLUE))
                    print(available)
            probe = metadata_refresh_probe(answers)
            allowed = None
            flag = ""
            remove_flag = False
            clear_type_default = False
            if choice in {"1", "2"}:
                allowed = {"audio"}
                flag = "default"
                remove_flag = choice == "2"
            elif choice in {"3", "4", "5", "6"}:
                allowed = {"subtitle"}
                flag = "default" if choice in {"3", "4"} else "forced"
                remove_flag = choice in {"4", "6"}
            elif choice in {"7", "8"}:
                flag = "commentary"
                remove_flag = choice == "8"
            elif choice in {"9", "10"}:
                flag = "original"
                remove_flag = choice == "10"
            elif choice == "11":
                flag = "0"
            else:
                flag = metadata_value_prompt(answers, "Enter disposition flag name")
                remove_flag = appio.ask_yes_no(metadata_prompt(answers, "Remove this flag instead of setting it?", "y/n", "n"), False)
            stream = select_stream(probe, answers, allowed)
            spec = metadata_stream_spec(probe, stream)
            codec_type = metadata_stream_type(stream)
            if flag == "default" and not remove_flag:
                appio.note("Setting default on one stream may not automatically remove default from other streams.")
                clear_type_default = appio.ask_yes_no(metadata_prompt(answers, "Make this stream the only default stream of its type?", "y/n", "n"), False)
            disposition_value = "0" if flag == "0" else (f"-{flag}" if remove_flag else flag)
            output_path = metadata_output_path(answers["metadata_input_path"], "_metadata_disposition")
            cmd = metadata_stream_copy_command(answers["ffmpeg"], answers["metadata_input_path"], output_path)
            if clear_type_default:
                prefix = {"audio": "a", "subtitle": "s", "video": "v"}.get(codec_type)
                if prefix:
                    cmd.extend([f"-disposition:{prefix}", "0"])
            cmd.extend([f"-disposition:{spec}", disposition_value, str(output_path)])
            if runner.confirm_and_run_ffmpeg(answers, cmd, "Stream Disposition Editor", output_path):
                metadata_set_current_input(answers, output_path)
        except Back:
            continue


def run_chapter_metadata_editor(answers: dict[str, Any]) -> None:
    while True:
        print()
        print(paint("Chapter Metadata Editor", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Show chapters", default=True))
        print(metadata_menu_item(2, "Export metadata and chapters to ffmetadata file"))
        print(metadata_menu_item(3, "Import metadata and chapters from ffmetadata file"))
        print(metadata_menu_item(4, "Remove all chapters"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            return
        try:
            input_path = answers["metadata_input_path"]
            if choice == "1":
                for line in metadata_chapter_lines(metadata_refresh_probe(answers)):
                    print("  " + line)
            elif choice == "2":
                output_path = metadata_report_output_path(input_path, "_ffmetadata", ".txt")
                cmd = [answers["ffmpeg"], "-hide_banner", "-y", "-i", str(input_path), "-f", "ffmetadata", str(output_path)]
                runner.confirm_and_run_ffmpeg(answers, cmd, "Chapter Metadata Export", output_path)
            elif choice == "3":
                appio.note("Importing ffmetadata may replace metadata according to the file content.")
                metadata_file = terminal_path(appio.ask_required(metadata_prompt(answers, "Enter ffmetadata file path")))
                if not metadata_file.exists() or not metadata_file.is_file():
                    appio.error("Metadata file was not found.")
                    continue
                output_path = metadata_output_path(input_path, "_chapters_imported")
                cmd = [answers["ffmpeg"], "-hide_banner", "-y", "-i", str(input_path), "-i", str(metadata_file), "-map", "0", "-map_metadata", "1", "-map_chapters", "1", "-c", "copy", str(output_path)]
                if runner.confirm_and_run_ffmpeg(answers, cmd, "Chapter Metadata Import", output_path):
                    metadata_set_current_input(answers, output_path)
            elif choice == "4":
                output_path = metadata_output_path(input_path, "_chapters_removed")
                cmd = [answers["ffmpeg"], "-hide_banner", "-y", "-i", str(input_path), "-map", "0", "-map_chapters", "-1", "-c", "copy", str(output_path)]
                if runner.confirm_and_run_ffmpeg(answers, cmd, "Chapter Removal", output_path):
                    metadata_set_current_input(answers, output_path)
            else:
                appio.error("Enter a menu number from 1 to 4.")
        except Back:
            continue


def run_cover_picture_editor(answers: dict[str, Any]) -> None:
    while True:
        print()
        print(paint("Cover / Attached Picture Editor", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Add cover image", default=True))
        print(metadata_menu_item(2, "Replace existing cover image"))
        print(metadata_menu_item(3, "Remove attached pictures"))
        print(metadata_menu_item(4, "Show attached picture streams"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            return
        try:
            input_path = answers["metadata_input_path"]
            probe = metadata_refresh_probe(answers)
            attached = metadata_attached_picture_streams(probe)
            if choice == "4":
                if not attached:
                    appio.note("No attached picture streams were found.")
                for stream in attached:
                    print("  " + metadata_stream_line(probe, stream))
                continue
            if choice in {"1", "2"}:
                cover = terminal_path(appio.ask_required(metadata_prompt(answers, "Enter cover image path", "jpg, jpeg, or png")))
                container_ext = input_path.suffix.lstrip(".").lower()
                # One validation for image type, missing file AND container. The
                # old code applied the MP4 attached_pic recipe to every
                # container: Matroska needs an -attach, Opus/Ogg need a base64
                # METADATA_BLOCK_PICTURE tag, and .mov accepts the command but
                # writes no picture at all.
                reason = cover_art_rejection_reason(container_ext, cover)
                if reason:
                    appio.error(reason)
                    continue
                output_path = metadata_output_path(input_path, "_cover_replaced" if choice == "2" else "_cover_added")
                cmd = [answers["ffmpeg"], "-hide_banner", "-y", "-i", str(input_path)]
                cmd.extend(cover_art_input_args(container_ext, cover))
                cmd.append("-map")
                cmd.append("0")
                if choice == "2":
                    for stream in attached:
                        cmd.extend(["-map", f"-0:v:{metadata_stream_relative_index(probe, stream)}"])
                real_video_streams = len([
                    s for s in probe.get("streams") or []
                    if metadata_stream_type(s) == "video"
                ]) - len(attached)
                cmd.extend(["-map_metadata", "0", "-c", "copy"])
                cmd.extend(cover_art_output_args(
                    container_ext, cover,
                    cover_input_index=1,
                    mapped_video_streams=max(0, real_video_streams),
                    existing_attachments=len(metadata_attachment_streams(probe)),
                ))
                cmd.append(str(output_path))
                appio.note(
                    f"Cover art for .{container_ext} is written as "
                    f"{COVER_ART_METHOD_DESCRIPTIONS[cover_art_method(container_ext)]}; "
                    "the media streams are copied unchanged."
                )
                if runner.confirm_and_run_ffmpeg(answers, cmd, "Cover / Attached Picture Editor", output_path):
                    metadata_set_current_input(answers, output_path)
            elif choice == "3":
                if not attached:
                    appio.note("No attached picture streams were found.")
                    continue
                output_path = metadata_output_path(input_path, "_cover_removed")
                cmd = [answers["ffmpeg"], "-hide_banner", "-y", "-i", str(input_path), "-map", "0"]
                for stream in attached:
                    cmd.extend(["-map", f"-0:v:{metadata_stream_relative_index(probe, stream)}"])
                cmd.extend(["-c", "copy", str(output_path)])
                if runner.confirm_and_run_ffmpeg(answers, cmd, "Attached Picture Removal", output_path):
                    metadata_set_current_input(answers, output_path)
            else:
                appio.error("Enter a menu number from 1 to 4.")
        except Back:
            continue


def run_video_bitstream_metadata_tools(answers: dict[str, Any]) -> None:
    while True:
        print()
        print(paint("Video Bitstream Metadata Tools", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Inspect declared video color metadata", default=True))
        print(metadata_menu_item(2, "Estimate actual color range from pixel values"))
        print(metadata_menu_item(3, "Set H.264 video_full_range_flag"))
        print(metadata_menu_item(4, "Set HEVC video_full_range_flag"))
        print(metadata_menu_item(5, "Set H.264 color primaries / transfer / matrix"))
        print(metadata_menu_item(6, "Set HEVC color primaries / transfer / matrix"))
        print(metadata_menu_item(7, "Set H.264 sample aspect ratio"))
        print(metadata_menu_item(8, "Set HEVC sample aspect ratio"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            return
        if choice not in {str(i) for i in range(1, 9)}:
            appio.error("Enter a menu number from 1 to 8.")
            continue
        try:
            input_path = answers["metadata_input_path"]
            probe = metadata_refresh_probe(answers)
            video_streams = [s for s in probe.get("streams") or [] if metadata_stream_type(s) == "video"]
            if choice == "1":
                for stream in video_streams:
                    print("  " + metadata_stream_line(probe, stream))
                continue
            if len(video_streams) == 1:
                stream = video_streams[0]
                list_streams_for_selection(probe, video_streams)
                stream_index = metadata_stream_index(stream)
                appio.note(f"Using the only video stream: {stream_index}.")
                log_info(
                    f"Metadata Editor auto-selected only video stream: index={stream_index}; "
                    f"spec={metadata_stream_spec(probe, stream)}"
                )
            else:
                stream = select_stream(probe, answers, {"video"})
            if choice == "2":
                mode = appio.ask_raw(metadata_prompt(answers, "Choose sampling density", "1=fast every 120th frame; 2=balanced every 30th frame; 3=detailed every 5th frame", "2"))
                if is_back_value(mode):
                    raise Back()
                sampling = {"1": "fast", "2": "balanced", "3": "detailed", "": "balanced"}.get(mode, "balanced")
                if gpu_available_for_answers(answers):
                    use_cuda_decode = appio.ask_yes_no(
                        metadata_prompt(
                            answers,
                            "Use CUDA/NVDEC GPU decode for this analysis?",
                            "signalstats analysis still runs on CPU; GPU decode only reduces decode load when supported",
                            "y",
                        ),
                        True,
                    )
                else:
                    use_cuda_decode = False
                    appio.note("No usable NVIDIA/NVENC GPU was detected. CUDA decode question skipped.")
                source_bit_depth = video_bit_depth(stream) or 8
                result = services.estimate_color_range(
                    input_path,
                    int(metadata_stream_index(stream) or 0),
                    sampling,
                    answers["ffmpeg"],
                    use_cuda_decode=use_cuda_decode,
                    bit_depth=source_bit_depth,
                    duration_seconds=services.stream_duration_seconds(stream, probe.get("format")),
                )
                print()
                print(paint("Color range estimate", Color.BOLD + Color.LIGHT_BLUE))
                print("  " + field_text("Declared color_range", stream.get("color_range") or "unknown", Color.COLOR_RANGE_VALUE))
                result_depth = result.get("bit_depth") or source_bit_depth
                print("  " + field_text("Luma bit depth", f"{result_depth}-bit", Color.PINK))
                print("  " + field_text("Absolute YMIN / YMAX", f"{result['ymin']} / {result['ymax']}", Color.YELLOW))
                # The decision uses the robust per-frame median (8-bit equivalent),
                # which ignores rare fade/flash frames; show it so the numbers match
                # the conclusion against the limited (16..235) / full (0..255) points.
                if result.get("ymin8") is not None and result.get("ymax8") is not None:
                    print("  " + field_text(
                        "Typical 8-bit YMIN / YMAX (median)",
                        f"{result['ymin8']:.1f} / {result['ymax8']:.1f}",
                        Color.YELLOW,
                    ))
                print("  " + field_text("Observed YLOW average", result["ylow"], Color.CYAN))
                print("  " + field_text("Observed YHIGH average", result["yhigh"], Color.CYAN))
                print("  " + field_text("Sampled frames", result["frames"], Color.GREEN))
                print("  " + field_text("Conclusion", result["conclusion"], Color.MAGENTA))
                appio.note("This is an approximation based on decoded pixel statistics. It is not a 100% reliable proof of the original intended color range. The declared color_range metadata is usually the most reliable indicator.")
                continue
            required_codec = {"3": {"h264", "avc1"}, "5": {"h264", "avc1"}, "7": {"h264", "avc1"}, "4": {"hevc", "h265"}, "6": {"hevc", "h265"}, "8": {"hevc", "h265"}}[choice]
            codec = str(stream.get("codec_name") or "").lower()
            if codec not in required_codec:
                appio.error("This operation is only available for the matching H.264 or HEVC codec.")
                continue
            bsf = metadata_bsf_name(codec)
            if not bsf:
                appio.error("This video codec is not supported by this bitstream metadata tool.")
                continue
            appio.note("This changes metadata/signaling only. It does not truly convert the video pixels. Wrong values can cause washed-out image or crushed blacks.")
            if not appio.ask_yes_no(metadata_prompt(answers, "Continue with this advanced bitstream metadata change?", "y/n", "n"), False):
                continue
            if choice in {"3", "4"}:
                value = appio.ask_raw(metadata_prompt(
                    answers,
                    "Choose video_full_range_flag",
                    "0=limited/TV; 1=full/PC; use b to go back",
                    back="back=b, quit=exit",
                ))
                if value.lower().strip() in {"b", "back"}:
                    raise Back()
                if value not in {"0", "1"}:
                    appio.error("Enter 0 or 1.")
                    continue
                suffix = "_colorflag_full" if value == "1" else "_colorflag_limited"
                filter_arg = f"{bsf}=video_full_range_flag={value}"
            elif choice in {"5", "6"}:
                appio.note("Common values: BT.709 = 1/1/1; BT.2020 SDR/PQ commonly uses primaries=9, transfer=14 or 16, matrix=9; SMPTE 170M/SD = 6/6/6.")
                primaries = metadata_value_prompt(answers, "Enter colour_primaries numeric value")
                transfer = metadata_value_prompt(answers, "Enter transfer_characteristics numeric value")
                matrix = metadata_value_prompt(answers, "Enter matrix_coefficients numeric value")
                suffix = "_color_metadata"
                filter_arg = f"{bsf}=colour_primaries={primaries}:transfer_characteristics={transfer}:matrix_coefficients={matrix}"
            else:
                sar = metadata_value_prompt(answers, "Enter sample aspect ratio")
                suffix = "_sample_aspect_ratio"
                filter_arg = f"{bsf}=sample_aspect_ratio={sar}"
            output_path = metadata_output_path(input_path, suffix)
            spec = metadata_stream_spec(probe, stream)
            cmd = [answers["ffmpeg"], "-hide_banner", "-y", "-i", str(input_path), "-map", "0", "-c", "copy", f"-bsf:{spec}", filter_arg, str(output_path)]
            if runner.confirm_and_run_ffmpeg(answers, cmd, "Video Bitstream Metadata Tool", output_path):
                metadata_set_current_input(answers, output_path)
        except Back:
            continue


def run_metadata_editor_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        answers = metadata_prompt_input(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None
    while True:
        print()
        print(paint("Metadata Editor", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Stream Metadata Editor", default=True))
        print(metadata_menu_item(2, "Stream Disposition Editor"))
        print(metadata_menu_item(3, "Chapter Metadata Editor"))
        print(metadata_menu_item(4, "Cover / Attached Picture Editor"))
        print(metadata_menu_item(5, "Video Bitstream Metadata Tools"))
        choice = metadata_menu_selection("1")
        if is_back_value(choice):
            appio.note("Returning to main menu.")
            return None
        try:
            if choice == "1":
                run_stream_metadata_editor(answers)
            elif choice == "2":
                run_stream_disposition_editor(answers)
            elif choice == "3":
                run_chapter_metadata_editor(answers)
            elif choice == "4":
                run_cover_picture_editor(answers)
            elif choice == "5":
                run_video_bitstream_metadata_tools(answers)
            else:
                appio.error("Enter a menu number from 1 to 5.")
        except ExitWizard:
            raise
        except Exception as exc:
            log_exception("Metadata Editor operation failed")
            appio.error(str(exc))


__all__ = [
    'metadata_menu_selection',
    'metadata_prompt_input',
    'metadata_refresh_probe',
    'metadata_set_current_input',
    'metadata_show_input_overview',
    'run_chapter_metadata_editor',
    'run_cover_picture_editor',
    'run_metadata_editor_mode',
    'run_stream_disposition_editor',
    'run_stream_metadata_editor',
    'run_video_bitstream_metadata_tools',
]
