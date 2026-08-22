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


def step_input_path(answers: dict[str, Any]) -> None:
    while True:
        input_example = example_text('"E:\\Input\\video.mkv"')
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter input file path",
                f"drag and drop a file here or paste a path; example: {input_example}",
                back="back=0, quit=exit, f=join all videos in folder",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            appio.error("This value cannot be empty. Enter a file path, or 'f' to join a folder.")
            continue
        # Folder join: the 'f' keyword prompts for a folder; a directory path is
        # also accepted directly. Every video in it is joined in name order.
        folder: Path | None = None
        if value.lower() in {"f", "folder"}:
            folder = ask_join_folder_path(answers)
            if folder is None:
                continue
        else:
            candidate = terminal_path(value)
            if candidate.is_dir():
                folder = candidate
        if folder is not None:
            if _load_input_folder_join(answers, folder):
                return
            continue
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
            log_exception(f"ffprobe metadata load failed for input path: {input_path}")
            appio.error(f"ffprobe could not read the file. See log file: {_log_file_text()}")
            continue
        return


def step_output_location(answers: dict[str, Any]) -> None:
    folder_example = example_text(r"E:\output")
    name_example = example_text('"File name"')
    value = appio.ask_raw(
        appio.question_prompt(
            answers,
            "Enter output path, output folder, or bare output name",
            f"Enter=same folder as input; examples: {folder_example} or {name_example}",
        )
    )
    if is_back_value(value):
        raise Back()
    apply_output_location_value(answers, value)
    join_items = list(answers.get("join_input_items") or [])
    if join_items:
        original_title = answers.get("_source_info_title")
        answers["_source_info_title"] = f"Source file info (1/{len(join_items) + 1})"
        trackmanager.print_source_info(answers)
        if original_title is None:
            answers.pop("_source_info_title", None)
        else:
            answers["_source_info_title"] = original_title
        for idx, item in enumerate(join_items, start=2):
            joined_answers = join_item_answers(answers, item)
            joined_answers["_source_info_title"] = f"Source file info ({idx}/{len(join_items) + 1})"
            trackmanager.print_source_info(joined_answers)
    else:
        trackmanager.print_source_info(answers)


def ask_join_add_another(prompt: str, allow_folder: bool = True) -> bool | str:
    """Join-flow variant of the add-another question. Returns True (yes),
    False (no / Enter), or the string 'folder'. Raises Back on 0/back tokens."""
    while True:
        value = appio.ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        lowered = value.lower()
        if not value or lowered in {"n", "no"}:
            log_info(f"User choice: join_add_another=no; raw={value!r}")
            return False
        if lowered in {"y", "yes"}:
            log_info("User choice: join_add_another=yes")
            return True
        if allow_folder and lowered in {"f", "folder"}:
            log_info("User choice: join_add_another=folder")
            return "folder"
        appio.error("Enter y, n, or 'f' (join all videos in a folder)." if allow_folder else "Enter y or n.")


def step_join_additional_inputs_for_encode(answers: dict[str, Any]) -> None:
    audio_join = wizard_audio_join_applicable(answers) and not output_has_video(answers)
    if (not output_has_video(answers) and not audio_join) or not answers.get("input_path"):
        answers.pop("join_input_items", None)
        answers["_join_question_extra"] = 0
        answers.pop("_join_base_question", None)
        answers.pop("_join_last_question", None)
        return
    existing_items = list(answers.get("join_input_items") or [])
    # Media-aware wording + options (audio joins do not offer folder scanning).
    media_word = "audio" if audio_join else "video"
    join_back = JOIN_ADD_ANOTHER_BACK_AUDIO if audio_join else JOIN_ADD_ANOTHER_BACK
    allow_folder = not audio_join
    existing_extra = int(answers.get("_join_question_extra", 0) or 0)
    current_question = int(answers.get("_question_number", 0) or 0)
    resuming_existing_join = bool(existing_items and existing_extra and current_question > existing_extra)
    base_question = int(answers.get("_join_base_question") or (current_question - existing_extra if resuming_existing_join else current_question))
    items: list[dict[str, Any]] = existing_items if resuming_existing_join else []
    if resuming_existing_join:
        resume_question = max(current_question, int(answers.get("_join_last_question") or current_question))
        sub_question_base = resume_question
        first_title = f"Add another {media_word} file?"
    else:
        # Folder given at the input prompt pre-loads the join set; keep those
        # items instead of discarding them.
        preloaded_from_folder = bool(answers.pop("_join_preloaded_from_folder", False))
        if preloaded_from_folder:
            items = existing_items
        else:
            answers.pop("join_input_items", None)
            items = []
        answers["_join_question_extra"] = 0
        answers["_join_base_question"] = base_question
        sub_question_base = base_question
        first_title = f"Add another {media_word} file?" if items else f"Add another {media_word} file to join with this input?"

    # Initial add-another question (now also accepts 'folder').
    answers["_question_number"] = sub_question_base
    decision = wizard.ask_join_add_another(
        appio.question_prompt(answers, first_title, "y/n", "n", back=join_back), allow_folder=allow_folder
    )
    if decision is False:
        if resuming_existing_join:
            answers["join_input_items"] = items
            answers["_join_question_extra"] = existing_extra
        else:
            # Preserve folder-preloaded inputs (and bookkeeping for back-nav).
            answers["join_input_items"] = items
            if items:
                answers["_join_question_extra"] = max(1, existing_extra)
                answers["_join_last_question"] = sub_question_base
            else:
                answers.pop("_join_base_question", None)
                answers.pop("_join_last_question", None)
        return
    sub_question = sub_question_base + 1
    need_file = decision is True
    if decision == "folder":
        answers["_question_number"] = sub_question
        folder = ask_join_folder_path(answers)
        if folder is not None:
            join_add_folder_items(answers, folder, items)
        sub_question += 1
        need_file = False

    while True:
        if need_file:
            answers["_question_number"] = sub_question
            value = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    f"Enter additional {media_word} file path",
                    f"drag and drop a {media_word} file here or paste a path; b=re-enter previous file",
                    back="back=0, quit=exit",
                )
            )
            if is_back_value(value):
                # '0' goes back to the previous wizard step.
                raise Back()
            if value.lower() in {"b", "back"}:
                # 'b' steps back to the PREVIOUS join file (re-enter it) rather
                # than leaving the join question entirely. With no previous
                # additional file, fall back to the previous wizard step.
                if items:
                    removed = items.pop()
                    appio.note(f"Removed previous join file: {Path(removed['path']).name}. Re-enter it.")
                    continue
                raise Back()
            if not value:
                appio.error("This value cannot be empty. Enter a file path, or 'b' to go back.")
                continue
            path = terminal_path(value)
            if not path.exists() or not path.is_file():
                appio.error("File not found. Enter the full file path again.")
                continue
            if paths_same(path, answers["input_path"]) or any(paths_same(path, item["path"]) for item in items):
                appio.error("This file is already selected for joining. Enter a different file.")
                continue
            if looks_like_generated_output_file(path):
                appio.error("This looks like a previously generated FFmWiz output file. It was not added as a join input.")
                continue
            try:
                items.append(services.join_load_media_item(answers, path, allow_audio_only=audio_join))
            except Exception as exc:
                log_exception(f"Join input probe failed: {path}")
                appio.error(str(exc))
                continue
            sub_question += 1

        answers["_question_number"] = sub_question
        decision = wizard.ask_join_add_another(
            appio.question_prompt(answers, f"Add another {media_word} file?", "y/n", "n", back=join_back), allow_folder=allow_folder
        )
        if decision is False:
            break
        sub_question += 1
        if decision == "folder":
            answers["_question_number"] = sub_question
            folder = ask_join_folder_path(answers)
            if folder is not None:
                join_add_folder_items(answers, folder, items)
            sub_question += 1
            need_file = False
            continue
        need_file = True
    answers["_join_last_question"] = sub_question
    answers["_join_question_extra"] = max(0, sub_question - base_question)
    answers["join_input_items"] = items
    appio.note(f"Added {len(items)} additional {media_word} input(s) for joining.")
    if items:
        print_join_order_list([answers["input_path"], *[it["path"] for it in items]])


def step_output_format(answers: dict[str, Any], allowed: list[str] | None = None) -> None:
    """Ask for the output container.

    `allowed` restricts BOTH the displayed list and what is accepted. A mode
    whose builder can only produce certain containers must pass it -- Video
    Speed/Reverse offered .mp3 and .webm from the generic list while its
    builder hardcoded H.264 + AAC and always mapped a video stream, so the
    command it printed could never mux.
    """
    # Join mode: show the compact source summary (min/max video bitrate, audio
    # bitrate, fps, file count) right before the format prompt.
    if answers.get("join_input_items"):
        print_join_input_summary(answers)
    input_ext = answers["input_path"].suffix.lstrip(".") or "mp4"
    if answers.get("video_streams"):
        # Default to mp4 for video, except keep mkv when the input is mkv.
        default_ext = "mkv" if input_ext.lower() == "mkv" else "mp4"
    else:
        default_ext = "mp3"
    allowed_formats = [str(item).lower().lstrip(".") for item in (allowed or [])]
    common_formats = allowed_formats or (COMMON_VIDEO_FORMATS + COMMON_AUDIO_FORMATS)
    if allowed_formats and default_ext not in allowed_formats:
        default_ext = allowed_formats[0]
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter final output format",
                f"common: {option_list(common_formats)}; {keep_value_text(f'n=Use input format ({input_ext})')}",
                default_ext,
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = default_ext
        try:
            answers["output_format_keep_input"] = value.lower().strip() == "n"
            ext = normalize_format(value, input_ext)
        except ValueError as exc:
            appio.error(str(exc))
            continue
        # Reject unknown output formats (likely typos) with a suggestion, and
        # ask for a different format instead of building a command FFmpeg fails.
        if allowed_formats and ext not in allowed_formats:
            appio.error(
                f"This mode cannot produce .{ext}. Choose one of: "
                + ", ".join(allowed_formats)
            )
            continue
        if not answers["output_format_keep_input"] and ext not in KNOWN_OUTPUT_FORMATS:
            import difflib
            close = difflib.get_close_matches(ext, sorted(KNOWN_OUTPUT_FORMATS), n=1)
            hint = f" Did you mean '{close[0]}'?" if close else ""
            appio.error(f"'{ext}' is not a supported output format.{hint} Enter a different format.")
            continue
        answers["output_ext"] = ext
        return


def step_video_codec(answers: dict[str, Any]) -> None:
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter video codec",
                f"common: {option_list(COMMON_VIDEO_CODECS)}; {keep_value_text('n=copy current video stream without re-encoding')}",
                DEFAULT_VIDEO_CODEC,
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = DEFAULT_VIDEO_CODEC
        if value.lower() == "n":
            value = "copy"
        lowered = value.lower()
        if lowered == "copy" or lowered in VIDEO_CODEC_ALIASES:
            answers["video_codec"] = value
            return
        available = {str(item).lower() for item in answers.get("video_encoders") or []}
        if not available or lowered in available:
            answers["video_codec"] = value
            return
        appio.error(
            f"Unknown video encoder '{value}'. Enter one of the common aliases "
            "or an encoder reported by your FFmpeg build."
        )


def step_use_gpu(answers: dict[str, Any]) -> None:
    if not gpu_available_for_answers(answers):
        answers["use_gpu"] = False
        appio.note("No usable NVIDIA/NVENC GPU was detected. GPU question skipped; CPU mode selected.")
        log_info("User choice: use_gpu=False; reason=GPU unavailable")
        return
    answers["use_gpu"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Use GPU/NVIDIA for decode/filter/encode?", "y/n", "y"),
        True,
    )


def step_cpu_two_pass(answers: dict[str, Any]) -> None:
    answers["cpu_two_pass"] = appio.ask_yes_no(
        appio.question_prompt(
            answers,
            "Use two-pass CPU video encoding for closer target bitrate?",
            "y/n; slower, but usually closer to the requested bitrate",
            "y",
        ),
        True,
    )


def step_unified_video_editor_for_encode(answers: dict[str, Any]) -> None:
    if answers.get("_disable_graphical_editors"):
        answers["_unified_video_editor_used"] = False
        return
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Open unified graphical video editor?",
                f"y/n; {colored_unified_editor_hint()}",
                "y",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "y"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["_unified_video_editor_used"] = False
            answers["_unified_video_editor_declined"] = True
            answers["_disable_followup_video_gui_prompts"] = True
            # In the config wizard (Mode 2), declining the editor must NOT discard
            # crop/speed/reverse that came from config.env; those were already
            # applied and should still take effect.
            if not answers.get("_config_mode"):
                answers["crop_enabled"] = False
                answers["crop_values_inline"] = False
                for key in ("crop_top", "crop_left", "crop_right", "crop_bottom"):
                    answers.pop(key, None)
                answers["video_speed_enabled"] = False
                answers["reverse_video"] = False
                answers["audio_speed_from_video"] = False
                answers["cut_keep_ranges"] = []
                answers.pop("separator_points", None)
                answers.pop("_unified_separator_points", None)
            return
        if lowered in {"y", "yes"}:
            appio.note("Loading Unified Graphical Video Editor...")
            sys.stdout.flush()
            result = guibridge.open_unified_video_gui(answers)
            if result is None:
                appio.note("Unified graphical video editor was canceled. Returning to the unified editor question.")
                continue
            top, left, right, bottom = result["margins"]
            if not set_crop_margins_if_valid(answers, top, left, right, bottom):
                continue
            answers["_unified_video_editor_used"] = True
            answers["_unified_video_editor_declined"] = False
            answers["_disable_followup_video_gui_prompts"] = False
            answers["_unified_cut_keep_ranges"] = result.get("keep_ranges") or []
            answers["_unified_separator_points"] = result.get("separator_points") or []
            if answers["_unified_separator_points"]:
                answers["separator_points"] = list(answers["_unified_separator_points"])
            else:
                answers.pop("separator_points", None)
            answers["_unified_video_speed"] = result["speed"]
            answers["_unified_reverse_video"] = result["reverse"]
            answers["_unified_include_audio"] = result["include_audio"]
            speed = clamp_speed_factor(result.get("speed", DEFAULT_SPEED_FACTOR))
            reverse = bool(result.get("reverse"))
            answers["video_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
            answers["video_speed_factor"] = speed
            answers["reverse_video"] = reverse
            answers["audio_speed_from_video"] = bool(result.get("include_audio"))
            unified_duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
            unified_duration += sum(float(item.get("duration") or 0.0) for item in answers.get("join_input_items") or [])
            keep_ranges = normalize_cut_ranges(result.get("keep_ranges") or [], unified_duration)
            answers["cut_keep_ranges"] = keep_ranges
            if keep_ranges:
                print(paint(format_cut_ranges_for_summary(keep_ranges, services.get_video_fps(answers), "Cuts (keep ranges)"), Color.LIME))
            _unified_seps = answers.get("_unified_separator_points") or []
            if _unified_seps:
                print(paint(format_split_points_for_summary(_unified_seps, services.get_video_fps(answers), "Split points"), Color.LIME))
            print(paint("Graphical edits captured.", Color.LIME))
            if answers["video_speed_enabled"]:
                print(
                    paint(
                        f"Applied unified video speed: {speed * 100:.0f}% ({speed:g}x); "
                        f"reverse video: {'yes' if reverse else 'no'}; "
                        f"sync audio: {'yes' if answers['audio_speed_from_video'] else 'no'}",
                        Color.LIME,
                    )
                )
            return
        appio.error("Enter y or n.")


def step_crop_enabled(answers: dict[str, Any]) -> None:
    if answers.get("_unified_video_editor_used"):
        margins = (
            int(answers.get("crop_top", 0) or 0),
            int(answers.get("crop_left", 0) or 0),
            int(answers.get("crop_right", 0) or 0),
            int(answers.get("crop_bottom", 0) or 0),
        )
        answers["crop_enabled"] = any(margins)
        answers["crop_values_inline"] = bool(any(margins))
        if any(margins):
            print(paint(f"Applied unified crop: {format_crop_margins(answers)}", Color.LIME))
        return
    while True:
        crop_hint = (
            "y/n, or inline top,left,right,bottom like "
            f"{example_text('100,300,200,550')}; {paint('zero is allowed inside inline crop', Color.ZERO_INLINE)}"
        )
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Apply crop?",
                crop_hint,
                "n",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["crop_enabled"] = False
            answers["crop_values_inline"] = False
            for key in ("crop_top", "crop_left", "crop_right", "crop_bottom"):
                answers.pop(key, None)
            return
        if lowered in {"y", "yes"}:
            answers["crop_enabled"] = True
            answers["crop_values_inline"] = False
            return
        if lowered in {"g", "gui", "preview"}:
            appio.error("The standalone Crop GUI is archived. Use the Unified Video Editor or enter crop margins inline.")
            continue

        pieces = [piece.strip() for piece in value.split(",")]
        if len(pieces) != 4 or any(not re.fullmatch(r"\d+", piece) for piece in pieces):
            appio.error("Enter y, n, or four integer crop margins: top,left,right,bottom")
            continue
        top, left, right, bottom = [int(piece) for piece in pieces]
        if not set_crop_margins_if_valid(answers, top, left, right, bottom):
            continue
        answers["crop_values_inline"] = True
        return


def step_video_bitrate(answers: dict[str, Any]) -> None:
    packet_sizes = services.get_packet_sizes(answers)
    source = stream_bitrate_kbps(answers["video_streams"][0], answers.get("format"), packet_sizes)
    source_limit, source_limit_label = detected_video_bitrate_limit(answers)

    # First ask: bitrate mode or constant quality (RF/CQ).
    mode_prompt = appio.question_prompt(
        answers,
        "Video quality mode",
        f"{paint('bitrate', Color.OPT_KEY_CYAN)}{paint('=target average kbps', Color.HINT_YELLOW)}; "
        f"{paint('CRF', Color.OPT_KEY_CYAN)}{paint('=constant quality (RF/CQ)', Color.HINT_YELLOW)}",
        "bitrate",
    )
    while True:
        mode_value = appio.ask_raw(mode_prompt).strip().lower()
        if is_back_value(mode_value):
            raise Back()
        if not mode_value or mode_value in {"bitrate", "b", "1"}:
            mode_value = "bitrate"
            break
        if mode_value in {"rf", "crf", "cq", "2", "quality"}:
            mode_value = "rf"
            break
        appio.error("Enter 'bitrate' or 'CRF'.")

    if mode_value == "rf":
        _step_video_constant_quality(answers)
        return

    # Bitrate mode: same as before.
    suggested = source or DEFAULT_OUTPUT_VIDEO_BITRATE_KBPS
    prompt = appio.question_prompt(
        answers,
        "Enter average video bitrate in kbps",
        f"examples: {example_text('400,800,1500')}; "
        f"{keep_value_text('n=keep current value' + (' around ' + str(source) + 'k' if source else ''))}; "
        f"{suggestion_text(f'suggestion: {suggested}')}",
        str(suggested),
    )
    while True:
        value = appio.ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        if not value:
            value = str(suggested)
        if value.lower() == "n":
            answers["video_bitrate_kbps"] = source
            answers["video_bitrate_keep"] = True
            return
        if not re.fullmatch(r"\d+", value):
            appio.error("Enter an integer only.")
            continue
        number = int(value)
        if number == 0:
            raise Back()
        if not confirm_numeric_target_not_above_source(
            answers,
            "video bitrate",
            number,
            source_limit,
            source_limit_label,
            "kbps",
            "higher video bitrate can increase file size without adding real source detail",
        ):
            continue
        answers["video_bitrate_kbps"] = number
        answers["video_bitrate_keep"] = False
        answers.pop("video_crf", None)
        services.print_encode_size_estimate(answers, number, "video")
        return


def step_resolution(answers: dict[str, Any]) -> None:
    while True:
        prompt = appio.question_prompt(
            answers,
            "Enter output resolution",
            f"presets/plain numbers preserve aspect ratio using closest-edge scaling: {option_list(list(RESOLUTION_PRESETS))}, "
            f"{paint('numbers without p like 480', Color.RES_NUMBERS)}, "
            f"{paint('force width like w720', Color.RES_TARGET)}, "
            f"{paint('force height like h480', Color.RES_TARGET)}, "
            f"{paint('target box like', Color.RES_EXACT)} {example_text('1280x720')}, "
            f"{paint('exact stretch like', Color.RES_EXACT)} {example_text('stretch:1280x720')}; "
            +
            keep_value_text("n=current resolution"),
            "n",
        )
        value = appio.ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        try:
            parsed = parse_resolution(value)
        except ValueError as exc:
            appio.error(str(exc))
            continue
        if not confirm_resolution_not_above_source(answers, parsed):
            continue
        answers["resolution"] = parsed
        return


def step_fps(answers: dict[str, Any]) -> None:
    # Join with different source frame rates: ask the unify/VFR policy first, and
    # (when unifying) ask the target fps here so the fps question comes after the
    # unify question. When declined the join stays VFR and no fps is asked.
    if answers.get("join_input_items") and output_has_video(answers):
        join_items = join_ordered_items_for_answers(answers)
        if len(join_items) >= 2 and join_frame_rates_differ(join_items):
            ask_join_frame_rate_policy(answers, join_items)
            return
        answers.setdefault("join_vfr", False)
    fps = rational_to_float(answers["video_streams"][0].get("avg_frame_rate"))
    source_limit, source_limit_label = detected_fps_limit(answers)
    keep_fps_text = "n=current FPS" + (f" around {format(fps, '.3g')}" if fps else "")
    prompt = appio.question_prompt(
        answers,
        "Enter frames per second",
        f"examples: {example_text('4,5,24,30,60')}; {keep_value_text(keep_fps_text)}",
        "n",
    )
    while True:
        value = appio.ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        if value.lower() == "n":
            answers["fps"] = None
            return
        if not re.fullmatch(r"\d+", value):
            appio.error("Enter an integer only.")
            continue
        number = int(value)
        if number == 0:
            raise Back()
        if not confirm_numeric_target_not_above_source(
            answers,
            "frames per second",
            number,
            source_limit,
            source_limit_label,
            "fps",
            "higher FPS duplicates or interpolates timing work without adding real captured frames",
        ):
            continue
        answers["fps"] = number
        return


__all__ = [
    'ask_join_add_another',
    'step_cpu_two_pass',
    'step_crop_enabled',
    'step_fps',
    'step_input_path',
    'step_join_additional_inputs_for_encode',
    'step_output_format',
    'step_output_location',
    'step_resolution',
    'step_unified_video_editor_for_encode',
    'step_use_gpu',
    'step_video_bitrate',
    'step_video_codec',
]
