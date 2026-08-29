"""FFmWiz wizard_flow overflow (wizard_flow_b) — split for file size.

Re-exported by wizard_flow, so every consumer of
`from ffmwiz.wizard_flow import *` still sees the full set. Monkeypatch-safe.
"""
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
from ffmwiz.core.artifacts import *  # noqa: F401,F403
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
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz import wizard_composite  # noqa: F401  (module, so a patch is seen)
from ffmwiz import wizard_quick  # noqa: F401  (module, so a patch is seen)
from ffmwiz import wizard_raw  # noqa: F401  (module, so a patch is seen)
from ffmwiz.trackmanager import *  # noqa: F401,F403


def step_video_speed_reverse_options(answers: dict[str, Any]) -> None:
    ensure_video_input(answers)
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Change video speed or reverse video?",
                f"y/n, {graphical_hint('g=Show Graphical Video Speed Editor')}; "
                "y=manual speed/reverse settings",
                "n",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["_speed_reverse_noop"] = True
            return
        if lowered in {"g", "gui", "graphical"}:
            result = guibridge.open_video_speed_gui(answers)
            if result is None:
                appio.note("Graphical video speed editor was canceled.")
                continue
            answers["speed_factor"] = result["speed"]
            answers["reverse_video"] = result["reverse"]
            answers["include_audio"] = result["include_audio"]
            answers["_speed_reverse_noop"] = False
            return
        if lowered in {"y", "yes"}:
            while True:
                speed_text = appio.ask_raw(
                    "Enter speed factor or percent (examples: 0.5x, 1.25x, 125%; Enter=100%; 0=back): "
                )
                if is_back_value(speed_text):
                    break
                if not speed_text:
                    speed_text = "100%"
                try:
                    answers["speed_factor"] = parse_speed_factor(speed_text)
                    break
                except ValueError as exc:
                    appio.error(str(exc))
            if "speed_factor" not in answers:
                continue
            answers["reverse_video"] = appio.ask_yes_no(yn_prompt("Reverse video too?", False), False)
            include_default = any_join_audio(answers)
            answers["include_audio"] = appio.ask_yes_no(yn_prompt("Sync all audio tracks with the video speed/reverse change?", include_default), include_default)
            answers["_speed_reverse_noop"] = False
            return
        appio.error("Enter y, n, or g.")


def step_video_speed_reverse_for_encode(answers: dict[str, Any]) -> None:
    for key in ("video_speed_enabled", "video_speed_factor", "reverse_video", "audio_speed_from_video"):
        answers.pop(key, None)
    if answers.get("_unified_video_editor_used"):
        speed = clamp_speed_factor(answers.get("_unified_video_speed", DEFAULT_SPEED_FACTOR))
        reverse = bool(answers.get("_unified_reverse_video"))
        answers["video_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
        answers["video_speed_factor"] = speed
        answers["reverse_video"] = reverse
        answers["audio_speed_from_video"] = bool(answers.get("_unified_include_audio"))
        if answers["video_speed_enabled"]:
            print(paint(f"Applied unified speed/reverse: {speed:.2f}x, reverse={'yes' if reverse else 'no'}", Color.LIME))
        return
    allow_gui = not answers.get("_disable_graphical_editors") and not answers.get("_disable_followup_video_gui_prompts")
    while True:
        hint = (
            f"y/n, {graphical_hint('g=Show Graphical Video Speed Editor')}; "
            "speed/reverse requires video re-encoding"
            if allow_gui
            else "y/n; speed/reverse requires video re-encoding"
        )
        value = appio.ask_raw(appio.question_prompt(answers, "Change video speed or reverse video?", hint, "n"))
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["video_speed_enabled"] = False
            return
        if lowered in {"g", "gui", "graphical"}:
            if not allow_gui:
                appio.error("Graphical video speed editor is not available here. Use the Unified Video Editor or manual settings.")
                continue
            appio.note("Loading Graphical Video Speed Editor...")
            sys.stdout.flush()
            result = guibridge.open_video_speed_gui(answers)
            if result is None:
                appio.note("Graphical video speed editor was canceled. Returning to the speed question.")
                continue
            answers["video_speed_enabled"] = True
            answers["video_speed_factor"] = result["speed"]
            answers["reverse_video"] = result["reverse"]
            answers["audio_speed_from_video"] = bool(result.get("include_audio"))
            return
        if lowered in {"y", "yes"}:
            while True:
                speed_text = appio.ask_raw("Enter video speed factor or percent (examples: 0.5x, 1.25x, 125%; Enter=100%; 0=back): ")
                if is_back_value(speed_text):
                    break
                if not speed_text:
                    speed_text = "100%"
                try:
                    answers["video_speed_factor"] = parse_speed_factor(speed_text)
                    break
                except ValueError as exc:
                    appio.error(str(exc))
            if "video_speed_factor" not in answers:
                continue
            answers["reverse_video"] = appio.ask_yes_no(yn_prompt("Reverse video too?", False), False)
            # any_join_audio: a silent input 1 used to skip this question for a
            # join whose later inputs are audible, so the joined audio kept its
            # original length under a re-timed video (R02).
            if any_join_audio(answers):
                answers["audio_speed_from_video"] = appio.ask_yes_no(yn_prompt("Apply the same speed/reverse to selected audio too?", True), True)
            answers["video_speed_enabled"] = True
            return
        appio.error("Enter y, n, or g." if allow_gui else "Enter y or n.")


def step_audio_speed_reverse_options(answers: dict[str, Any]) -> None:
    audio_index = int(answers.get("audio_index", 0))
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Change audio speed or reverse audio?",
                f"y/n, {graphical_hint('g=Show Graphical Audio Speed Editor')}; "
                "y=manual speed/reverse settings",
                "n",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["_speed_reverse_noop"] = True
            return
        if lowered in {"g", "gui", "graphical"}:
            result = wizard_b.open_audio_speed_gui(answers, audio_index)
            if result is None:
                appio.note("Graphical audio speed editor was canceled.")
                continue
            answers["speed_factor"] = result["speed"]
            answers["reverse_audio"] = result["reverse"]
            answers["_speed_reverse_noop"] = False
            return
        if lowered in {"y", "yes"}:
            while True:
                speed_text = appio.ask_raw(
                    "Enter speed factor or percent (examples: 0.5x, 1.25x, 125%; Enter=100%; 0=back): "
                )
                if is_back_value(speed_text):
                    break
                if not speed_text:
                    speed_text = "100%"
                try:
                    answers["speed_factor"] = parse_speed_factor(speed_text)
                    break
                except ValueError as exc:
                    appio.error(str(exc))
            if "speed_factor" not in answers:
                continue
            answers["reverse_audio"] = appio.ask_yes_no(yn_prompt("Reverse audio too?", False), False)
            answers["_speed_reverse_noop"] = False
            return
        appio.error("Enter y, n, or g.")


def step_audio_speed_reverse_for_encode(answers: dict[str, Any]) -> None:
    for key in ("audio_speed_enabled", "audio_speed_factor", "reverse_audio"):
        answers.pop(key, None)
    allow_gui = not answers.get("_disable_graphical_editors")
    selected = selected_audio_streams(answers) if answers.get("audio_tracks") is not None else [0]
    audio_index = selected[0] if selected else 0
    while True:
        suffix = " Enter=n keeps any video-linked audio speed." if answers.get("audio_speed_from_video") else ""
        hint = (
            f"y/n, {graphical_hint('g=Show Graphical Audio Speed Editor')}; applies to selected audio tracks.{suffix}"
            if allow_gui
            else f"y/n; applies to selected audio tracks.{suffix}"
        )
        value = appio.ask_raw(appio.question_prompt(answers, "Change audio speed or reverse audio?", hint, "n"))
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["audio_speed_enabled"] = False
            return
        if lowered in {"g", "gui", "graphical"}:
            if not allow_gui:
                appio.error("Graphical audio speed editor is not available in Folder Encode.")
                continue
            appio.note("Loading Graphical Audio Speed Editor...")
            sys.stdout.flush()
            result = wizard_b.open_audio_speed_gui(answers, audio_index)
            if result is None:
                appio.note("Graphical audio speed editor was canceled. Returning to the speed question.")
                continue
            answers["audio_speed_enabled"] = True
            answers["audio_speed_factor"] = result["speed"]
            answers["reverse_audio"] = result["reverse"]
            answers["audio_speed_from_video"] = False
            return
        if lowered in {"y", "yes"}:
            while True:
                speed_text = appio.ask_raw("Enter audio speed factor or percent (examples: 0.5x, 1.25x, 125%; Enter=100%; 0=back): ")
                if is_back_value(speed_text):
                    break
                if not speed_text:
                    speed_text = "100%"
                try:
                    answers["audio_speed_factor"] = parse_speed_factor(speed_text)
                    break
                except ValueError as exc:
                    appio.error(str(exc))
            if "audio_speed_factor" not in answers:
                continue
            answers["reverse_audio"] = appio.ask_yes_no(yn_prompt("Reverse audio too?", False), False)
            answers["audio_speed_enabled"] = True
            answers["audio_speed_from_video"] = False
            return
        appio.error("Enter y, n, or g." if allow_gui else "Enter y or n.")


def step_cuts(answers: dict[str, Any]) -> None:
    """Optional wizard step: ask the user whether to define cuts before
    re-encoding. Stores answers['cut_keep_ranges'] when active."""
    answers.pop("cut_keep_ranges", None)
    fps = services.get_video_fps(answers)
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    if answers.get("_unified_video_editor_used"):
        keep_ranges = normalize_cut_ranges(list(answers.get("_unified_cut_keep_ranges") or []), duration)
        if keep_ranges and not (len(keep_ranges) == 1 and keep_ranges[0][0] <= 1e-6 and keep_ranges[0][1] >= duration - 1e-6):
            answers["cut_keep_ranges"] = keep_ranges
            print(paint(
                format_cut_ranges_for_summary(keep_ranges, fps, "Cuts (keep ranges)"),
                Color.LIME,
            ))
        return
    while True:
        hint_text = "y/n; cuts are applied frame-accurate via filter_complex"
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Apply cuts before encoding?",
                hint_text,
                "n",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            return
        if lowered in {"y", "yes"}:
            try:
                keep_ranges = services.collect_cut_ranges_terminal(answers, fps, duration)
            except Back:
                continue
        elif lowered in {"g", "gui", "preview"}:
            appio.error("The standalone Cut GUI is archived. Use the Unified Video Editor or enter cuts manually.")
            continue
        else:
            appio.error("Enter y or n.")
            continue
        if not keep_ranges:
            appio.note("No keep ranges were produced; cuts disabled.")
            return
        answers["cut_keep_ranges"] = keep_ranges
        print(paint(
            format_cut_ranges_for_summary(keep_ranges, fps, "Cuts (keep ranges)"),
            Color.LIME,
        ))
        return


def step_start_folder_now(answers: dict[str, Any]) -> None:
    confirm_source_extra_stream_outcomes(answers)
    cmd = wizard_build.build_ffmpeg_command(answers)
    answers["cmd"] = cmd
    print_summary(answers, cmd)
    answers["start_now"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Start folder encode now?", "y/n", "y"),
        True,
    )


def step_hardsub_start_now(answers: dict[str, Any]) -> None:
    ensure_color_range_resolved(answers, workflow="HardSub")
    wizard_base.log_and_warn_pixel_format(answers)
    cmd = wizard_build_b.build_hardsub_command(answers)
    answers["cmd"] = cmd
    print()
    print(paint("Hard Sub Encode summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("input", answers["input_path"], Color.WHITE))
    if answers.get("hardsub_subtitle_source") == "internal":
        print("  " + field_text("subtitle", f"internal subtitle #{answers.get('hardsub_subtitle_index')}", Color.MAGENTA))
    else:
        print("  " + field_text("subtitle", answers.get("hardsub_subtitle_path"), Color.MAGENTA))
    print("  " + field_text("output", answers["output_path"], Color.LIME))
    print("  " + field_text("video codec", effective_value(answers, "video_codec"), Color.CYAN))
    if "_nvenc" in command_to_text(cmd):
        print("  " + field_text("NVENC multipass", normalize_nvenc_multipass_mode(answers.get("nvenc_multipass")), Color.YELLOW))
    print("  " + field_text("quality", answers.get("hardsub_quality_mode"), Color.YELLOW))
    print("  " + field_text("HDR/Dolby handling", answers.get("hardsub_hdr_handling"), Color.ORANGE))
    print("  " + field_text("audio", answers.get("hardsub_audio_mode"), Color.BLUE))
    print("  " + field_text("audio container policy", answers.get("hardsub_audio_container_policy", "copy-anyway"), Color.CYAN))
    print()
    print(paint("Final PowerShell command:", Color.FINAL_COMMAND_LABEL))
    log_info("Final PowerShell command: " + command_to_powershell(cmd))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    answers["start_now"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def print_summary(answers: dict[str, Any], cmd: list[str]) -> None:
    two_pass_display = cpu_two_pass_enabled_for_command(answers, cmd)
    if two_pass_display:
        pass1_cmd, pass2_cmd, _passlog = build_cpu_two_pass_commands(cmd, answers)
        log_info("Final PowerShell command (CPU two-pass pass 1/2): " + command_to_powershell(pass1_cmd))
        log_info("Final PowerShell command (CPU two-pass pass 2/2): " + command_to_powershell(pass2_cmd))
        log_command("Actual final subprocess pass 1/2", pass1_cmd)
        log_command("Actual final subprocess pass 2/2", pass2_cmd)
    else:
        log_info("Final PowerShell command: " + command_to_powershell(cmd))
        log_command("Actual final subprocess", cmd)
    log_final_normalized_answers(answers, cmd)
    log_info(
        "Selected settings: input={}; output={}; format={}; video_codec={}; audio_codec={}; crop={}; fps={}; resolution={}".format(
            answers.get("input_path"), answers.get("output_path"), answers.get("output_ext"),
            effective_value(answers, "video_codec"), effective_value(answers, "audio_codec"),
            format_crop_margins(answers) if answers.get("crop_enabled") else "no",
            answers.get("fps") or "source", format_resolution_summary(answers.get("resolution")),
        )
    )
    print()
    if two_pass_display:
        pass1_cmd, pass2_cmd, _passlog = build_cpu_two_pass_commands(cmd, answers)
        print(paint("Final PowerShell command (CPU two-pass pass 1/2):", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        print(paint(command_to_powershell(pass1_cmd), Color.FINAL_COMMAND_TEXT))
        print()
        print(paint("Final PowerShell command (CPU two-pass pass 2/2):", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        print(paint(command_to_powershell(pass2_cmd), Color.FINAL_COMMAND_TEXT))
    else:
        print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    print()
    print(paint("Selected settings summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("input", answers["input_path"], Color.WHITE))
    if answers.get("join_input_items"):
        join_items = list(answers.get("join_input_items") or [])
        print("  " + field_text("joined inputs", len(join_items) + 1, Color.LIGHT_BLUE))
        print("    " + paint(f"1. {Path(answers['input_path']).name}", Color.WHITE))
        for idx, item in enumerate(join_items, start=2):
            print("    " + paint(f"{idx}. {Path(item.get('path')).name}", Color.WHITE))
        print("  " + field_text("join settings", "video/audio settings apply by track number to every joined input", Color.YELLOW))
    print("  " + field_text("output", answers["output_path"], Color.LIME))
    if output_has_video(answers):
        print("  " + field_text("video codec",
                                effective_value(answers, "video_codec", DEFAULT_VIDEO_CODEC),
                                Color.CYAN))
        _resolved_encoder = resolve_video_encoder(answers)[0]
        print("  " + field_text("source bit depth", describe_video_bit_depth(source_video_stream(answers) or {}), Color.PINK))
        print("  " + field_text("output bit depth", f"{output_video_bit_depth(answers)}-bit", Color.PINK))
        if str(_resolved_encoder).lower() != "copy":
            print("  " + field_text("encoder", _resolved_encoder, Color.CYAN))
            if "hevc" in str(_resolved_encoder) or str(_resolved_encoder) in {"libx265"}:
                _default_profile = "main" if str(_resolved_encoder) in {"libx265"} else None
                print("  " + field_text("profile", hevc_profile_for_output(answers, _default_profile), Color.CYAN))
            print("  " + field_text(
                "pixel format",
                cuda_pixel_format_for_output(answers) if (str(_resolved_encoder).endswith("_nvenc") and can_use_cuda_fast_path(answers, _resolved_encoder))
                else cpu_graph_pixel_format_for_encoder(answers, _resolved_encoder),
                Color.ORANGE,
            ))
            print("  " + field_text("path", filter_graph_path_label(answers, _resolved_encoder), Color.AQUA))
            _precision_note = bit_depth_precision_note(answers)
            if _precision_note:
                print("  " + field_text("precision note", _precision_note, Color.NOTE_YELLOW))
        print("  " + field_text("GPU", "yes" if answers.get("use_gpu") else "no", Color.GREEN if answers.get("use_gpu") else Color.YELLOW))
        if "_nvenc" in command_to_text(cmd):
            print("  " + field_text("NVENC multipass", normalize_nvenc_multipass_mode(answers.get("nvenc_multipass")), Color.YELLOW))
        if answers.get("crop_enabled"):
            print("  " + field_text("crop", "yes, " + format_crop_margins(answers), Color.ORANGE))
            try:
                adj_left, adj_right, adj_top, adj_bottom = normalized_crop_margins(answers)
                req = (
                    int(answers.get("crop_left", 0) or 0),
                    int(answers.get("crop_right", 0) or 0),
                    int(answers.get("crop_top", 0) or 0),
                    int(answers.get("crop_bottom", 0) or 0),
                )
                if (adj_left, adj_right, adj_top, adj_bottom) != req:
                    print("  " + field_text(
                        "crop (aligned)",
                        f"top={adj_top} px, left={adj_left} px, right={adj_right} px, bottom={adj_bottom} px",
                        Color.ORANGE,
                    ))
                crop_w, crop_h = cropped_source_size(answers)
                print("  " + field_text("cropped resolution", f"{crop_w}x{crop_h}", Color.ORANGE))
            except ValueError:
                pass
            crop_box = answers.get("crop_box_dimensions")
            crop_ar = answers.get("cropped_aspect_ratio")
            if crop_box and crop_ar:
                print("  " + field_text("crop box", f"{crop_box[0]}x{crop_box[1]} (AR {crop_ar:.4f})", Color.ORANGE))
        else:
            print("  " + field_text("crop", "no", Color.GREEN))
        print("  " + field_text("video bitrate", str(answers.get("video_bitrate_kbps") or "source/default") + " kbps", Color.YELLOW))
        if answers.get("cpu_two_pass"):
            print("  " + field_text("CPU two-pass", "yes", Color.YELLOW))
        print("  " + field_text("resolution", format_resolution_summary(answers.get("resolution")), Color.MAGENTA))
        if answers.get("final_resolution"):
            final_w, final_h = answers["final_resolution"]
            print("  " + field_text("final output resolution", f"{final_w}x{final_h}", Color.LIME))
        # Color-range and SAR/DAR summary. The summary is a display surface, so
        # an unresolved unknown range (e.g. a pure stream-copy that writes no
        # color-range metadata) is reported honestly instead of raising.
        try:
            resolved_range, range_source = resolve_color_range(answers, workflow="print_summary")
        except ColorRangeUnresolvedError:
            resolved_range, range_source = "", "unresolved (no metadata written)"
        detected_range = display_color_range((source_video_stream(answers) or {}).get("color_range"))
        is_copy = str(resolve_video_encoder(answers)[0]).lower() == "copy"
        print("  " + field_text("detected source color range", detected_range, Color.COLOR_RANGE_VALUE))
        if is_copy:
            # Stream copy: bitstream range signaling is preserved from the source;
            # the re-encode menu semantics do not apply.
            print("  " + field_text("color-range policy", "stream copy (preserved from source)", Color.COLOR_RANGE_VALUE))
            print("  " + field_text(
                "output color-range metadata", f"{detected_range} (preserved from copied stream)", Color.COLOR_RANGE_VALUE))
        elif range_source == "user choice":
            # "Do not force a range in FFmWiz" (option 2). Capability is resolved
            # from the per-environment FFmpeg cache (lazy probe). Without a
            # verified result we report conservatively rather than guessing.
            cap = services.resolve_capability(answers, allow_probe=not answers.get("_no_capability_probe"))
            print("  " + field_text("requested color-range policy", "do not force", Color.COLOR_RANGE_VALUE))
            print("  " + field_text("FFmWiz explicit color-range option", "omitted", Color.COLOR_RANGE_VALUE))
            print("  " + field_text("capability source", cap["capability_source"], Color.COLOR_RANGE_VALUE))
            print("  " + field_text("capability environment fingerprint", cap["env_short"], Color.DIM))
            if cap.get("status") == "verified" and cap.get("expected_final_range") is not None:
                fr = cap["expected_final_range"]
                shown = "unspecified" if fr in {"unknown", "", None} else fr
                print("  " + field_text("expected encoder-reported final range", shown, Color.COLOR_RANGE_VALUE))
                if cap.get("verified_at_utc"):
                    print("  " + field_text("verified probe timestamp", cap["verified_at_utc"], Color.DIM))
            else:
                print("  " + field_text("expected encoder-reported final range",
                                        "unknown until verified", Color.COLOR_RANGE_VALUE))
        else:
            print("  " + field_text(
                "resolved color range",
                (resolved_range or "unspecified") + f" ({range_source})",
                Color.COLOR_RANGE_VALUE,
            ))
            print("  " + field_text(
                "output color-range metadata",
                resolved_range if resolved_range else "omitted",
                Color.COLOR_RANGE_VALUE,
            ))
        print("  " + field_text("pixel-value range conversion", "no", Color.DIM))
        _sd = sar_dar_info(answers)
        print("  " + field_text(
            "source SAR",
            f"{_sd['sar_text']} ({_sd['sar_source']})",
            Color.AQUA,
        ))
        print("  " + field_text(
            "source DAR",
            f"{_sd['dar_text']} ({_sd['dar_source']})",
            Color.AQUA,
        ))
        if _sd.get("effective_dar_decimal"):
            print("  " + field_text("effective DAR", f"{_sd['effective_dar_decimal']:.6f}", Color.AQUA))
        print("  " + field_text("pixel shape", _sd["pixel_shape"], Color.PINK))
        if _sd.get("warning"):
            print("  " + field_text("geometry warning", _sd["warning"], Color.YELLOW))
        # Pixel-format operation summary.
        try:
            if str(resolve_video_encoder(answers)[0]).lower() != "copy":
                _pf = wizard_base.pixel_format_analysis(answers)
                _src, _tgt = _pf["source"], _pf["target"]
                print("  " + field_text(
                    "source pixel format",
                    f"{_src['pix_fmt']} ({(str(_src['bit_depth']) + '-bit') if _src['bit_depth'] else 'unknown-bit'}, {_src['chroma']})",
                    Color.ORANGE,
                ))
                print("  " + field_text(
                    "target pixel format",
                    f"{_tgt['pix_fmt']} ({(str(_tgt['bit_depth']) + '-bit') if _tgt['bit_depth'] else 'unknown-bit'}, {_tgt['chroma']})",
                    Color.ORANGE,
                ))
                print("  " + field_text("pixel-format operation", _pf["operation"], Color.PINK))
                print("  " + field_text("bit-depth conversion", _pf["bit_depth_conversion"], Color.PINK))
                print("  " + field_text("chroma-subsampling conversion", _pf["chroma_conversion"], Color.PINK))
        except Exception:
            pass
        print("  " + field_text("fps", answers.get("fps") or "source", Color.MAGENTA))
        if video_speed_transform_enabled(answers):
            print("  " + field_text("video speed", f"{encode_video_speed_factor(answers) * 100:.0f}%", Color.MAGENTA))
            print("  " + field_text("reverse video", "yes" if answers.get("reverse_video") else "no", Color.ORANGE))
            if answers.get("reverse_video"):
                appio.note(
                    "FFmWiz runs reverse video in short segments to avoid buffering the whole clip in RAM. "
                    "The single command above is an equivalent simple reference command."
                )
        if answers.get("separator_points"):
            print(paint(format_split_points_for_summary(answers.get("separator_points") or [], services.get_video_fps(answers), "Split points"), Color.LIGHT_BLUE))
        if answers.get("split_output_paths"):
            print("  " + field_text("Split output parts", len(answers.get("split_output_paths") or []), Color.LIGHT_BLUE))
            split_intervals = list(answers.get("split_part_intervals") or [])
            for idx, part_path in enumerate(answers.get("split_output_paths") or [], start=1):
                interval_text = ""
                if idx - 1 < len(split_intervals):
                    start, end = split_intervals[idx - 1]
                    duration = max(0.0, end - start)
                    interval_text = f"  [{seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}, duration {format_elapsed(duration)}]"
                print("    " + field_text(f"Part {idx:02d}", str(part_path) + interval_text, Color.LIME))
    # Picture filters, quick output, compositing, volume and raw ffmpeg options
    # each change the job as much as anything printed above, and this is the
    # last thing a user reads before committing to an encode that may run for
    # an hour. Guarded on the ANSWER, not on the describer's text -- every
    # describe_* below returns the string "none" for a job that never touched
    # it, and "none" is still a non-empty, truthy string.
    if any(key in answers for key in LOOK_ANSWER_KEYS):
        print("  " + field_text("picture filters", describe_look(answers), Color.ORANGE))
    if answers.get("quick_output") or answers.get("loop_count"):
        print("  " + field_text("quick output", wizard_quick.describe_quick(answers), Color.LIME))
    if answers.get("composite_mode") or answers.get("composite_audio_mix"):
        print("  " + field_text("composite", wizard_composite.describe_composite(answers), Color.LIGHT_BLUE))
    _volume = answers.get("audio_volume")
    if _volume is not None and abs(float(_volume) - 1.0) > 1e-9:
        print("  " + field_text("volume", wizard_raw.describe_raw({"audio_volume": _volume}), Color.MEAN_VOLUME))
    if answers.get("raw_ffmpeg_args"):
        print("  " + field_text(
            "raw options",
            wizard_raw.describe_raw({"raw_ffmpeg_args": answers["raw_ffmpeg_args"]}),
            Color.YELLOW,
        ))
    if any_join_audio(answers):
        _recovered_streams, _recovered_tracks = join_audio_recovery(answers)
        print("  " + field_text(
            "audio tracks",
            f"{_recovered_tracks} (input 1 is silent; taken from the other joined inputs)"
            if _recovered_streams else answers.get("audio_tracks"),
            Color.LIGHT_BLUE))
        # The resolved codec: a container fallback records its substitution
        # in the effective map and leaves the requested key alone, so this
        # line reported "copy" while -c:a said aac (F07).
        print("  " + field_text("audio codec", effective_value(answers, "audio_codec"), Color.CYAN))
        print("  " + field_text("audio bitrate", str(answers.get("audio_bitrate_kbps") or "source/default") + " kbps", Color.YELLOW))
        _sr = resolve_audio_sample_rate(answers)
        if answers.get("join_input_items"):
            print("  " + field_text("audio sample rate", f"{join_target_sample_rate(answers)} Hz (uniform across joined inputs)", Color.AUDIO_SAMPLE_RATE))
        else:
            print("  " + field_text("audio sample rate", f"{_sr} Hz" if _sr else "keep source", Color.AUDIO_SAMPLE_RATE))
        if audio_cut_transform_enabled(answers):
            print(paint(format_audio_ranges_for_summary(answers["audio_cut_keep_ranges"], "audio cuts (keep ranges)"), Color.LIME))
        if audio_speed_transform_enabled(answers):
            print("  " + field_text("audio speed", f"{encode_audio_speed_factor(answers) * 100:.0f}%", Color.MAGENTA))
            print("  " + field_text("reverse audio", "yes" if encode_audio_reverse_enabled(answers) else "no", Color.ORANGE))
        if loudnorm_transform_enabled(answers):
            mode = loudnorm_mode(answers)
            mode_text = {"single": "Single-pass", "two_pass": "Two-pass"}.get(mode, mode)
            target_i = answers.get("loudnorm_target_i", LOUDNORM_DEFAULT_TARGET_I)
            print("  " + field_text(
                "LoudNorm",
                f"{mode_text} on final output audio  (I={target_i:g}, TP={LOUDNORM_TARGET_TP:g}, LRA={LOUDNORM_TARGET_LRA:g})",
                Color.MEAN_VOLUME,
            ))
            if mode == "two_pass":
                source_text = (
                    "final joined audio from all selected input clips"
                    if answers.get("join_input_items") else "final output audio"
                )
                print("    " + field_text("measurement source", source_text, Color.MEAN_VOLUME))
    if output_has_video(answers) and answers.get("subtitle_streams"):
        if source_subtitles_keep_enabled(answers):
            print("  " + field_text("subtitle tracks", answers.get("subtitle_tracks"), Color.WHITE))
        else:
            print("  " + field_text("subtitle tracks", "removed by metadata policy", Color.ORANGE))
    if output_has_video(answers) and services.source_extra_preservation_features(answers):
        print("  " + field_text("source metadata", "keep" if source_metadata_keep_enabled(answers) else "remove", Color.LIGHT_BLUE))
        print("  " + field_text("chapters", "keep" if source_chapters_keep_enabled(answers) else "remove", Color.LIGHT_BLUE))
    if output_has_video(answers) and additional_source_video_streams(answers):
        _extra_count = len(additional_source_video_streams(answers))
        _extra_reason = additional_source_video_drop_reason(answers)
        _extra_keep = resolve_source_extra_video_keep(answers)
        print("  " + field_text(
            "extra source video streams",
            f"keep {_extra_count}" if _extra_keep
            else f"dropped ({_extra_reason or 'removed by the metadata policy'})",
            Color.LIGHT_BLUE if _extra_keep else Color.ORANGE))
    if output_has_video(answers) and source_data_streams(answers) and source_data_keep_enabled(answers) and timeline_is_modified(answers):
        print("  " + field_text(
            "source data streams",
            f"copied unchanged ({len(source_data_streams(answers))}); timestamps still follow the source timeline",
            Color.ORANGE))
    if output_has_video(answers) and embedded_attachment_streams(answers):
        attachment_state = "yes" if embedded_attachment_keep_enabled(answers) else "no"
        print("  " + field_text("embedded attachments", attachment_state, Color.PINK))
    cut_keep_ranges = answers.get("cut_keep_ranges") or []
    if cut_keep_ranges:
        fps = services.get_video_fps(answers)
        print(paint(
            format_cut_ranges_for_summary(cut_keep_ranges, fps, "cuts (keep ranges)"),
            Color.LIME,
        ))
    _print_estimated_output_size(answers)


def _print_estimated_output_size(answers: dict[str, Any]) -> None:
    """Summary line: approximate output size from the chosen target bitrates.

    Uses the TOTAL target bitrate (video + audio). Shows ``N/A`` with a reason
    when a target bitrate is not available for the video stream (constant-quality
    CRF/CQ mode, or stream copy), since size then cannot be estimated.
    """
    total_kbps = 0.0
    na_reason = ""
    if output_has_video(answers):
        if str(resolve_video_encoder(answers)[0]).lower() == "copy":
            na_reason = "video stream copy, no target bitrate"
        elif answers.get("video_crf") is not None:
            na_reason = "constant-quality CRF/CQ mode"
        elif answers.get("video_bitrate_kbps"):
            total_kbps += float(answers["video_bitrate_kbps"])
        else:
            na_reason = "no target video bitrate"
    if (
        not na_reason
        and answers.get("audio_streams")
        and answers.get("audio_bitrate_kbps")
        and str(effective_value(answers, "audio_codec")) != "copy"
    ):
        total_kbps += float(answers["audio_bitrate_kbps"])
    if na_reason:
        print("  " + field_text("estimated output size", f"N/A ({na_reason})", Color.NOTE_YELLOW))
        return
    duration = services.estimated_encode_duration_seconds(answers)
    size = estimate_size_bytes_from_bitrate(total_kbps, duration)
    if size is None or total_kbps <= 0:
        print("  " + field_text("estimated output size", "N/A (unknown source duration)", Color.NOTE_YELLOW))
        return
    print("  " + field_text(
        "estimated output size",
        f"{format_estimated_size(size)}  (total {int(total_kbps)} kbps over {format_duration(duration)})",
        Color.LIME,
    ))
    appio.note(BITRATE_SIZE_ESTIMATE_NOTE)


def graphical_hint(text: str) -> str:
    if USE_COLOR:
        return f"{Color.AQUA}{text}{Color.RESET}{Color.HINT_YELLOW}"
    return text


__all__ = [
    'graphical_hint',
    'print_summary',
    'step_video_speed_reverse_options',
    'step_video_speed_reverse_for_encode',
    'step_audio_speed_reverse_options',
    'step_audio_speed_reverse_for_encode',
    'step_cuts',
    'step_start_folder_now',
    'step_hardsub_start_now',
]


# Siblings are addressed through their defining module's object rather than
# star-imported: a bare name binds at import time and would ignore a test that
# patches the definer. None of them imports wizard_flow -- the facade that merges
# this module's __all__ -- nor wizard, so the wizard tier stays acyclic.
from ffmwiz import wizard_base  # noqa: E402,F401
from ffmwiz import wizard_b  # noqa: E402,F401
from ffmwiz import wizard_build  # noqa: E402,F401
from ffmwiz import wizard_build_b  # noqa: E402,F401
