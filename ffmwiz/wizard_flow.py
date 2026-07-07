"""FFmWiz wizard cluster (extracted from FFmWiz.py, method الف)."""
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

from ffmwiz import wizard  # facade for monkeypatch-stable cross-module calls  # noqa: F401
from ffmwiz.wizard import *  # sibling helpers  # noqa: F401,F403


def run_wizard(answers: dict[str, Any], config: dict[str, Any] | None = None) -> None:
    # config is None for Mode 1 (full wizard). In Mode 2 it is the parsed
    # config.env: every question whose config value is non-empty is auto-applied
    # and SKIPPED; empty ones are asked (just like Mode 1). The unified graphical
    # editor and the final "start now?" question are ALWAYS asked.
    config_mode = config is not None

    def cfg_has(key: str) -> bool:
        return config_mode and config_value(config, key).strip() != ""

    # step name -> config key(s). A step is skipped only when ALL its keys are
    # provided. Steps not listed (join_inputs, unified_video_editor, cuts,
    # audio_cut, start_now) are NEVER auto-skipped, so they are always asked.
    skip_map: dict[str, tuple[str, ...]] = {
        "input_path": ("input_path",),
        "output_location": ("output_path",),
        "output_format": ("output_format",),
        "video_codec": ("video_codec",),
        "use_gpu": ("use_gpu",),
        "crop_enabled": ("crop",),
        "crop_top": ("crop",), "crop_left": ("crop",), "crop_right": ("crop",), "crop_bottom": ("crop",),
        "video_bitrate": ("video_bitrate_kbps",),
        "nvenc_multipass": ("nvenc_multipass",),
        "cpu_two_pass": ("cpu_two_pass",),
        "resolution": ("resolution",),
        "fps": ("fps",),
        "video_speed_reverse": ("video_speed", "reverse_video"),
        "audio_tracks": ("audio_tracks",),
        "loudnorm": ("loudnorm",),
        "audio_speed_reverse": ("audio_speed", "reverse_audio"),
        "audio_codec": ("audio_codec",),
        "audio_bitrate": ("audio_bitrate_kbps",),
        "audio_sample_rate": ("audio_sample_rate",),
        "source_extras": ("keep_source_metadata",),
        "subtitle_tracks": ("subtitle_tracks",),
        "color_range": ("color_range",),
    }

    applied = {"done": False}

    def ensure_config_applied() -> None:
        if not config_mode or applied["done"]:
            return
        if not answers.get("input_path"):
            return
        if not (answers.get("video_streams") or answers.get("audio_streams")):
            return
        apply_config_settings_after_input(
            answers, config, skip_crop=not cfg_has("crop"), force_video_options=True
        )
        seed_unified_editor_from_config(answers, config)
        answers["_config_mode"] = True
        applied["done"] = True

    def is_config_skipped(step: "Step") -> bool:
        if not config_mode or not applied["done"]:
            return False
        keys = skip_map.get(step.name)
        if not keys:
            return False
        return all(cfg_has(k) for k in keys)

    steps = [
        wizard.Step("input_path", lambda a: True, wizard.step_input_path),
        wizard.Step("join_inputs", wizard_join_inputs_applicable, wizard.step_join_additional_inputs_for_encode),
        wizard.Step("output_location", lambda a: True, wizard.step_output_location),
        wizard.Step("output_format", lambda a: True, wizard.step_output_format),
        wizard.Step("video_codec", output_has_video, wizard.step_video_codec),
        wizard.Step("use_gpu", output_has_video, wizard.step_use_gpu),
        wizard.Step("unified_video_editor", output_has_video, wizard.step_unified_video_editor_for_encode),
        wizard.Step("crop_enabled", lambda a: output_has_video(a) and not a.get("_unified_video_editor_declined"), wizard.step_crop_enabled),
        wizard.Step("crop_top", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_top),
        wizard.Step("crop_left", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_left),
        wizard.Step("crop_right", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_right),
        wizard.Step("crop_bottom", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_bottom),
        wizard.Step("video_bitrate", video_reencode_options_applicable, wizard.step_video_bitrate),
        wizard.Step("nvenc_multipass", wizard.nvenc_multipass_prompt_applicable, wizard.step_nvenc_multipass),
        wizard.Step("cpu_two_pass", cpu_two_pass_applicable, wizard.step_cpu_two_pass),
        wizard.Step("resolution", video_reencode_options_applicable, wizard.step_resolution),
        wizard.Step("fps", video_reencode_options_applicable, wizard.step_fps),
        wizard.Step("video_speed_reverse", lambda a: output_has_video(a) and not a.get("_unified_video_editor_used") and not a.get("_unified_video_editor_declined"), wizard.step_video_speed_reverse_for_encode),
        wizard.Step("cuts", lambda a: video_reencode_options_applicable(a) and not a.get("_unified_video_editor_used") and not a.get("_unified_video_editor_declined"), wizard.step_cuts),
        wizard.Step("audio_tracks", lambda a: bool(a.get("audio_streams")), step_audio_tracks),
        wizard.Step("loudnorm", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_loudnorm),
        wizard.Step("audio_cut", audio_only_transform_prompt_applicable, wizard.step_audio_cut_for_encode),
        wizard.Step("audio_speed_reverse", audio_only_transform_prompt_applicable, wizard.step_audio_speed_reverse_for_encode),
        wizard.Step("audio_codec", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_audio_codec),
        wizard.Step("audio_bitrate", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy" and audio_codec_uses_bitrate(str(a.get("audio_codec") or default_audio_codec_for_ext(a.get("output_ext", "")))), step_audio_bitrate),
        wizard.Step("audio_sample_rate", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy", step_audio_sample_rate),
        wizard.Step("source_extras", source_extra_policy_applicable, step_source_extra_policy),
        wizard.Step("subtitle_tracks", lambda a: output_has_video(a) and source_subtitles_keep_enabled(a) and bool(a.get("subtitle_streams")), step_subtitle_tracks),
        wizard.Step("color_range", color_range_prompt_applicable, wizard.step_color_range),
        wizard.Step("start_now", lambda a: True, wizard.step_start_now),
    ]

    def runnable(pos: int) -> bool:
        return steps[pos].applicable(answers) and not is_config_skipped(steps[pos])

    def next_index(start: int) -> int:
        idx = start
        while idx < len(steps) and not runnable(idx):
            idx += 1
        return idx

    def prev_index(start: int) -> int:
        idx = start
        while idx > 0 and (
            not runnable(idx)
            or is_auto_unified_crop_step(idx)
            or wizard.step_is_auto_back_skip(steps[idx], answers)
        ):
            idx -= 1
        return max(0, idx)

    def is_auto_unified_crop_step(pos: int) -> bool:
        return bool(
            answers.get("_unified_video_editor_used")
            and steps[pos].name in {"crop_enabled", "crop_top", "crop_left", "crop_right", "crop_bottom"}
        )

    def question_number(current: int) -> int:
        count = 0
        join_pos = next((pos for pos, step in enumerate(steps) if step.name == "join_inputs"), -1)
        for pos in range(current + 1):
            if runnable(pos) and not is_auto_unified_crop_step(pos):
                count += 1
        extra = int(answers.get("_join_question_extra", 0) or 0) if join_pos >= 0 and current >= join_pos else 0
        return answers.get("_question_offset", 0) + count + extra

    # In config mode, pre-load the input file from config (so the input question
    # is skipped) and apply every provided setting before the first question.
    if config_mode and cfg_has("input_path"):
        cfg_input = terminal_path(config_value(config, "input_path"))
        if not cfg_input.exists() or not cfg_input.is_file():
            fail(f"input_path in config.env does not exist: {cfg_input}")
        load_input_metadata(answers, cfg_input)
        trackmanager.print_source_info(answers)
    ensure_config_applied()

    idx = next_index(0)
    while idx < len(steps):
        try:
            answers["_question_number"] = question_number(idx)
            steps[idx].run(answers)
            apply_unified_video_editor_answers(answers)
            ensure_config_applied()
            idx = next_index(idx + 1)
        except Back:
            if idx == 0:
                raise
            idx = prev_index(idx - 1)


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
            answers.get("video_codec"), answers.get("audio_codec"),
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
        print("  " + field_text("video codec", answers.get("video_codec", DEFAULT_VIDEO_CODEC), Color.CYAN))
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
                _pf = wizard.pixel_format_analysis(answers)
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
    if answers.get("audio_streams"):
        print("  " + field_text("audio tracks", answers.get("audio_tracks"), Color.LIGHT_BLUE))
        print("  " + field_text("audio codec", answers.get("audio_codec"), Color.CYAN))
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
        and str(answers.get("audio_codec")) != "copy"
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


def step_video_speed_reverse_options(answers: dict[str, Any]) -> None:
    ensure_video_input(answers)
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Change video speed or reverse video?",
                f"y/n, {wizard.graphical_hint('g=Show Graphical Video Speed Editor')}; "
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
            include_default = bool(answers.get("audio_streams"))
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
            f"y/n, {wizard.graphical_hint('g=Show Graphical Video Speed Editor')}; "
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
            if answers.get("audio_streams"):
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
                f"y/n, {wizard.graphical_hint('g=Show Graphical Audio Speed Editor')}; "
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
            result = wizard.open_audio_speed_gui(answers, audio_index)
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
            f"y/n, {wizard.graphical_hint('g=Show Graphical Audio Speed Editor')}; applies to selected audio tracks.{suffix}"
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
            result = wizard.open_audio_speed_gui(answers, audio_index)
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
    cmd = wizard.build_ffmpeg_command(answers)
    answers["cmd"] = cmd
    wizard.print_summary(answers, cmd)
    answers["start_now"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Start folder encode now?", "y/n", "y"),
        True,
    )


def step_hardsub_start_now(answers: dict[str, Any]) -> None:
    ensure_color_range_resolved(answers, workflow="HardSub")
    wizard.log_and_warn_pixel_format(answers)
    cmd = wizard.build_hardsub_command(answers)
    answers["cmd"] = cmd
    print()
    print(paint("Hard Sub Encode summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("input", answers["input_path"], Color.WHITE))
    if answers.get("hardsub_subtitle_source") == "internal":
        print("  " + field_text("subtitle", f"internal subtitle #{answers.get('hardsub_subtitle_index')}", Color.MAGENTA))
    else:
        print("  " + field_text("subtitle", answers.get("hardsub_subtitle_path"), Color.MAGENTA))
    print("  " + field_text("output", answers["output_path"], Color.LIME))
    print("  " + field_text("video codec", answers.get("video_codec"), Color.CYAN))
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


__all__ = [
    'graphical_hint',
    'print_summary',
    'run_wizard',
    'step_audio_speed_reverse_for_encode',
    'step_audio_speed_reverse_options',
    'step_cuts',
    'step_hardsub_start_now',
    'step_start_folder_now',
    'step_video_speed_reverse_for_encode',
    'step_video_speed_reverse_options',
]
