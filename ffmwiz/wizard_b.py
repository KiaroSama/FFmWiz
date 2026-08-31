"""FFmWiz wizard overflow (wizard_b) — split for file size.

Back-imports wizard and is re-exported by it, so every consumer of
`from ffmwiz.wizard import *` still sees the full set. Monkeypatch-safe.
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

from ffmwiz.core.artifacts import *  # noqa: F401,F403
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
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.trackmanager import *  # noqa: F401,F403
from ffmwiz import wizard_base  # noqa: E402,F401  (defines names used below)
from ffmwiz import wizard_build  # noqa: E402,F401  (defines names used below)
from ffmwiz import wizard_build_b  # noqa: E402,F401  (defines names used below)
from ffmwiz import wizard_flow  # noqa: E402,F401  (defines names used below)
# The facade back-import was deleted: every name this module uses comes
# from the LOWER tiers above, which the facade only re-exported. Importing
# it here bought nothing and made this module unimportable on its own,
# because the facade ends with `__all__ += <this module>.__all__` and
# reached that line while this module was still on its first statements.


def open_audio_speed_gui(answers: dict[str, Any], audio_index: int) -> dict[str, Any] | None:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    request = {
        "mode": "audio_speed",
        "input_path": str(answers["input_path"]),
        "audio_index": int(audio_index),
        "duration": float(duration),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
    }
    reply = guibridge._launch_qt_gui(request)
    if reply is None:
        appio.error("Graphical audio speed editor is not available. Install PySide6 and try again.")
        return None
    if reply.get("status") == "ok":
        try:
            return {
                "speed": clamp_speed_factor(reply.get("speed", DEFAULT_SPEED_FACTOR)),
                "reverse": bool(reply.get("reverse")),
            }
        except ValueError as exc:
            appio.error(str(exc))
            return None
    if reply.get("status") == "error":
        answers["_last_gui_error"] = "audio_speed"
        appio.error("Audio speed GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
    return None


def open_audio_cut_gui(answers: dict[str, Any], audio_index: int) -> list[tuple[float, float]] | None:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    request = {
        "mode": "audio_cut",
        "input_path": str(answers["input_path"]),
        "audio_index": int(audio_index),
        "duration": float(duration),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
    }
    reply = guibridge._launch_qt_gui(request)
    if reply is None:
        appio.error("Graphical audio cut editor is not available. Install PySide6 and try again.")
        return None
    if reply.get("status") == "ok":
        ranges = reply.get("keep_ranges") or []
        normalized: list[tuple[float, float]] = []
        for entry in ranges:
            try:
                s, e = float(entry[0]), float(entry[1])
            except Exception:
                continue
            if e > s:
                normalized.append((s, e))
        return normalize_cut_ranges(normalized, duration)
    if reply.get("status") == "error":
        answers["_last_gui_error"] = "audio_cut"
        appio.error("Audio cut GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
    return None


def step_start_now(answers: dict[str, Any]) -> None:
    # A rebuild is a NEW plan revision. Back leaves the REQUESTED answers alone
    # -- which is the point -- but the RESOLVED map used to survive with them,
    # so a Join's forced libx265/aac stayed in force after the Join was removed
    # and the rebuilt copy/copy job was re-encoded to HEVC (B13). Open the plan
    # here, on the outer dict, before any builder takes its shallow copy: the
    # join builders resolve into THIS revision's map rather than one of their
    # own, so the summary still describes the command that will run.
    begin_plan(answers)
    if output_is_audio_only(answers) and not answers.get("audio_streams"):
        fail("Audio-only output was selected, but the input file has no audio stream.")

    # The shared builder, not a hand-rolled copy. This one had already
    # drifted -- it omitted input 1's `subtitle_streams` (R04) -- and it kept
    # every item's CONTAINER duration, so a silent input padded the join past
    # its own last frame (B07).
    join_items = join_items_from_answers(answers)
    answers.pop("separator_jobs", None)
    if join_items:
        output_path = services.build_output_path(answers)
        answers["output_path"] = output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        copy_compatible, reasons = join_copy_compatibility(join_items)
        # VFR join whose inputs match on everything except frame rate can use
        # the concat demuxer (stream copy) to keep each segment's own rate.
        if (
            answers.get("join_vfr")
            and not copy_compatible
            and any(item.get("video_streams") for item in join_items)
            and join_copy_compatible_except_fps(join_items)
        ):
            copy_compatible = True
            reasons = []
            appio.note("VFR join: using stream copy (concat) to preserve each file's frame rate.")
        # Input compatibility is not the same question as "the selected plan
        # stream-copies". This gate used to accept a selection only when it was
        # literally None or the string "all", so the ordinary track question --
        # which returns a LIST -- forced a one-track input answered `[0]` down
        # the re-encode path while the summary still promised no re-encode
        # (F06). join_copy_plan compares normalised index SETS instead.
        copy_plan = join_copy_plan(answers, join_items)
        can_copy = copy_compatible and copy_plan["supported"]
        print_join_summary(join_items, copy_compatible, reasons, copy_plan)
        audio_only_join = all(not item.get("video_streams") for item in join_items)
        if audio_only_join:
            # Interactive-wizard audio join: stream-copy when compatible,
            # otherwise concatenate and re-encode the joined audio.
            if copy_compatible and str(answers.get("audio_codec", "")).lower() == "copy":
                cmd = build_join_copy_command(answers, join_items, output_path)
            else:
                appio.note("Joining audio inputs (concatenate and re-encode).")
                cmd = build_join_audio_encode_command(answers, join_items, output_path)
        elif can_copy:
            cmd = build_join_copy_command(answers, join_items, output_path)
        else:
            if copy_compatible:
                appio.note("Join inputs are stream-copy compatible, but selected encode settings require re-encoding.")
            else:
                appio.note("Join inputs are not stream-copy compatible. Re-encoding is required.")
            cmd = wizard_build_b.build_join_encode_command(answers, join_items, output_path)
    elif answers.get("composite_mode") or answers.get("composite_audio_mix"):
        # Without this branch the compositing question was asked, answered and
        # then ignored: the job ran as an ordinary encode with no overlay and
        # no warning, which is worse than not offering the feature. Compositing
        # needs its own command because it maps a SECOND input into the graph,
        # which `build_ffmpeg_command` has no shape for. `mix` sets
        # `composite_audio_mix` and never `composite_mode`, so an audio-only
        # composite used to fall through to the ordinary encode and lose the
        # second track.
        output_path = services.build_output_path(answers)
        answers["output_path"] = output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = wizard_build_b.build_composite_command(answers, output_path)
    else:
        cmd = wizard_build.build_ffmpeg_command(answers)
    answers["cmd"] = cmd
    ensure_color_range_resolved(answers, workflow="Main Wizard")
    log_crop_normalization_summary(answers)
    wizard_base.log_and_warn_pixel_format(answers)
    wizard_flow.print_summary(answers, cmd)
    answers["start_now"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def _run_gui_audio_transform(answers: dict[str, Any], audio_index: int) -> None:
    """Graphical audio transform path. Sets the transform answers or marks
    _audio_transform_noop. Raises Back to return to the editor menu."""
    while True:
        result = guibridge.open_audio_transform_gui(answers, audio_index)
        if result is None:
            appio.note("Graphical audio transform editor was canceled.")
            action = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    "Reopen the editor, enter values manually, or skip?",
                    "g=graphical editor, m=manual entry, n=skip",
                    "g",
                )
            ).strip().lower()
            if is_back_value(action):
                raise Back()
            if action in {"m", "manual"}:
                _apply_manual_audio_transform(answers)
                return
            if action in {"n", "no", "skip"}:
                answers["_audio_transform_noop"] = True
                return
            continue  # 'g' or empty -> reopen
        keep_ranges = list(result.get("keep_ranges") or [])
        speed = clamp_speed_factor(result.get("speed", DEFAULT_SPEED_FACTOR))
        reverse = bool(result.get("reverse"))
        if not (keep_ranges or reverse or abs(speed - 1.0) > 1e-6):
            appio.note("No audio transform was selected in the editor.")
            action = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    "Nothing changed. Enter values manually, reopen editor, or skip?",
                    "m=manual entry, g=graphical editor, n=skip",
                    "m",
                )
            ).strip().lower()
            if is_back_value(action):
                raise Back()
            if action in {"g", "graphical"}:
                continue
            if action in {"n", "no", "skip"}:
                answers["_audio_transform_noop"] = True
                return
            _apply_manual_audio_transform(answers)
            return
        answers["audio_cut_keep_ranges"] = keep_ranges
        answers["audio_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
        answers["audio_speed_factor"] = speed
        answers["reverse_audio"] = reverse
        answers["_audio_transform_noop"] = False
        return


def _confirm_audio_transform_start(answers: dict[str, Any]) -> None:
    """Build the command, print the summary, and ask "Start FFmpeg now?".
    Raises Back (on '0') so the editor can return to the value prompts."""
    split_points = answers.get("_audio_transform_split_points")
    if split_points:
        # Ask which lossless container extension to use (copy-compatible list).
        _ai = int(answers.get("audio_index", 0))
        _astreams = answers.get("audio_streams") or []
        _acodec = str(_astreams[_ai].get("codec_name", "")) if 0 <= _ai < len(_astreams) else ""
        ask_lossless_split_ext(answers, _acodec)
        cmd, pattern = build_lossless_split_command(answers, split_points)
        answers["cmd"] = cmd
        answers["output_path"] = pattern
        print()
        print(paint("Lossless audio split (stream copy, selected audio track only):", Color.BOLD + Color.LIME))
        print("  " + field_text("input", answers["input_path"], Color.WHITE))
        _acodec_disp = _acodec or "unknown"
        print("  " + field_text("audio track", f"{_ai + 1} (codec: {_acodec_disp})", Color.AQUA))
        print("  " + field_text(
            "output container",
            f"{pattern.suffix.lstrip('.')} (holds {_acodec_disp} losslessly; tracks have a codec, not an extension)",
            Color.LIME,
        ))
        print("  " + field_text("output parts", f"{len(split_points) + 1} files -> {pattern.name}", Color.LIME))
        print(paint(format_split_points_for_summary(split_points, float(answers.get("fps") or 25.0)), Color.LIGHT_BLUE))
        appio.note("Stream-copy split: rejoining the audio parts reproduces the original audio stream.")
        print()
        print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        log_info("Final PowerShell command: " + command_to_powershell(cmd))
        print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    else:
        cmd = build_audio_transform_command(answers)
        answers["cmd"] = cmd
        print_transform_summary(answers, cmd, "Audio Cut / Speed / Reverse")
    answers["start_now"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def step_audio_transform_editor(answers: dict[str, Any]) -> None:
    audio_index = int(answers.get("audio_index", 0))
    answers.pop("_audio_transform_split_points", None)
    answers.pop("lossless_split_ext", None)
    answers["_audio_transform_finalized"] = False
    # Stage machine so Back goes one prompt back instead of jumping to the start:
    #   menu  -> (Back) previous wizard step
    #   configure (values) -> (Back) menu
    #   confirm ("Start now?") -> (Back) configure / value prompts
    stage = "menu"
    choice = "1"
    while True:
        if stage == "menu":
            print()
            print(paint("Audio transform:", Color.BOLD + Color.LIGHT_BLUE))
            print(selection_menu_line(1, "Graphical editor (waveform cuts + speed / reverse)"))
            print(selection_menu_line(2, "Enter values manually (cuts + speed + reverse)"))
            choice_value = appio.ask_raw(appio.question_prompt(answers, "Select an option", None, "1")).strip()
            if is_back_value(choice_value):
                raise Back()
            if not choice_value:
                choice_value = "1"
            if choice_value not in {"1", "2"}:
                appio.error("Enter 1 or 2.")
                continue
            choice = choice_value
            stage = "configure"
        elif stage == "configure":
            answers["_audio_transform_noop"] = False
            try:
                if choice == "2":
                    _apply_manual_audio_transform(answers)
                else:
                    _run_gui_audio_transform(answers, audio_index)
            except Back:
                stage = "menu"
                continue
            if answers.get("_audio_transform_noop"):
                return  # nothing selected; no confirmation needed
            stage = "confirm"
        else:  # confirm
            try:
                _confirm_audio_transform_start(answers)
            except Back:
                stage = "configure"
                continue
            answers["_audio_transform_finalized"] = True
            return


def step_audio_cut_for_encode(answers: dict[str, Any]) -> None:
    answers.pop("audio_cut_keep_ranges", None)
    allow_gui = not answers.get("_disable_graphical_editors")
    selected = selected_audio_streams(answers) if answers.get("audio_tracks") is not None else [0]
    audio_index = selected[0] if selected else 0
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    while True:
        hint = (
            f"y/n, {wizard_flow.graphical_hint('g=Show Graphical Audio Cut Editor')}; applies to selected audio tracks"
            if allow_gui
            else "y/n; terminal range entry applies to selected audio tracks"
        )
        value = appio.ask_raw(appio.question_prompt(answers, "Apply audio waveform cuts?", hint, "n"))
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            return
        if lowered in {"g", "gui", "graphical"}:
            if not allow_gui:
                appio.error("Graphical audio cut editor is not available in Folder Encode.")
                continue
            appio.note("Loading Graphical Audio Cut Editor...")
            sys.stdout.flush()
            ranges = open_audio_cut_gui(answers, audio_index)
            if ranges is None:
                appio.note("Graphical audio cut editor was canceled. Returning to the audio cut question.")
                continue
            keep_ranges = normalize_cut_ranges(ranges, duration)
        elif lowered in {"y", "yes"}:
            try:
                keep_ranges = services.collect_cut_ranges_terminal(answers, 25.0, duration)
            except Back:
                continue
        else:
            appio.error("Enter y, n, or g." if allow_gui else "Enter y or n.")
            continue
        if not keep_ranges:
            appio.note("No audio keep ranges were produced; audio cuts disabled.")
            return
        answers["audio_cut_keep_ranges"] = keep_ranges
        print(paint(format_audio_ranges_for_summary(keep_ranges, "Audio cuts (keep ranges)"), Color.LIME))
        return


def step_audio_transform_start_now(answers: dict[str, Any]) -> None:
    if answers.get("_audio_transform_noop"):
        return
    # The editor step now builds the command and asks "Start FFmpeg now?" itself
    # (so Back from the confirmation returns to the value prompts, not the menu).
    if answers.get("_audio_transform_finalized"):
        return
    # Fallback for any path that did not finalize in the editor.
    _confirm_audio_transform_start(answers)


__all__ = [
    'open_audio_speed_gui',
    'open_audio_cut_gui',
    'step_start_now',
    '_run_gui_audio_transform',
    '_confirm_audio_transform_start',
    'step_audio_transform_editor',
    'step_audio_cut_for_encode',
    'step_audio_transform_start_now',
]
