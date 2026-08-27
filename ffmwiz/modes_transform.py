"""FFmWiz audio/video speed, reverse, and cut transform mode runners.

Modes 10 (Video Speed/Reverse) and 11 (Audio Cut/Speed/Reverse), split out of
modes.py. Non-monkeypatched: uses the sibling back-import pattern. modes.py
re-exports this module at its end so the flat FFmWiz.<name> API is unchanged.
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
from ffmwiz.metadata import *  # noqa: F401,F403
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.trackmanager import *  # noqa: F401,F403
from ffmwiz.wizard import *  # noqa: F401,F403
from ffmwiz import wizard
from ffmwiz.modes import *  # noqa: F401,F403  (back-import: run_mode_steps etc.)
from ffmwiz import modes  # noqa: F401


def run_video_speed_reverse_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_video_speed_reverse_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_video_speed_reverse_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    steps = [
        Step("input_path", lambda a: True, wizard.step_input_path),
        Step("speed_reverse", lambda a: True, step_video_speed_reverse_options),
        Step("output_location", lambda a: not a.get("_speed_reverse_noop"), wizard.step_output_location),
        # This mode always maps a video stream, so audio-only containers are not
        # producible; webm is excluded because the builder emits H.264 + AAC.
        Step("output_format", lambda a: not a.get("_speed_reverse_noop"),
             lambda a: wizard.step_output_format(a, allowed=VIDEO_SPEED_REVERSE_FORMATS)),
        Step("start_now", lambda a: not a.get("_speed_reverse_noop"), step_video_speed_start_now),
    ]
    while True:
        try:
            run_mode_steps(answers, steps)
            break
        except ValueError as exc:
            appio.error(str(exc))
            return None
    ensure_video_input(answers)
    if answers.get("_speed_reverse_noop"):
        appio.note("Video speed/reverse was not enabled. Returning to the first question.")
        return None
    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    if answers.get("reverse_video"):
        return run_segmented_reverse_video_speed(answers)
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(duration / max(0.001, float(answers.get("speed_factor", 1.0))) if duration > 0 else None),
        label="Video Speed / Reverse",
    )


def run_audio_cut_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_audio_cut_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_audio_cut_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    steps = [
        Step("input_path", lambda a: True, wizard.step_input_path),
        Step("audio_track", lambda a: True, step_audio_track_for_tool),
        Step("output_location", lambda a: True, wizard.step_output_location),
        Step("audio_cut_gui", lambda a: True, step_audio_cut_editor),
        Step("start_now", lambda a: not a.get("_audio_cut_noop"), step_audio_cut_start_now),
    ]
    try:
        run_mode_steps(answers, steps)
    except ValueError as exc:
        appio.error(str(exc))
        return None
    ensure_audio_input(answers)
    if answers.get("_audio_cut_noop"):
        appio.note("Audio cut was canceled. Returning to the first question.")
        return None
    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    total_duration = total_keep_duration(answers.get("audio_keep_ranges") or [])
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(total_duration if total_duration > 0 else None),
        label="Audio Cut",
    )


def run_audio_speed_reverse_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_audio_speed_reverse_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_audio_speed_reverse_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    steps = [
        Step("input_path", lambda a: True, wizard.step_input_path),
        Step("audio_track", lambda a: True, step_audio_track_for_tool),
        Step("speed_reverse", lambda a: True, step_audio_speed_reverse_options),
        Step("output_location", lambda a: not a.get("_speed_reverse_noop"), wizard.step_output_location),
        Step("start_now", lambda a: not a.get("_speed_reverse_noop"), step_audio_speed_start_now),
    ]
    try:
        run_mode_steps(answers, steps)
    except ValueError as exc:
        appio.error(str(exc))
        return None
    ensure_audio_input(answers)
    if answers.get("_speed_reverse_noop"):
        appio.note("Audio speed/reverse was not enabled. Returning to the first question.")
        return None
    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The command above is ready to run manually.")
        # The printed command is the ONE-SHOT one. For a track past the peak
        # budget that is exactly the unbounded reverse the staged plan avoids,
        # so say so rather than let it look equivalent.
        warning = audio_reverse_one_shot_warning(answers)
        if warning:
            appio.note("Note: " + warning)
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    speed = max(0.001, float(answers.get("speed_factor", 1.0)))
    if answers.get("reverse_audio"):
        # `areverse` buffers its whole input, so a long track has to be reversed
        # in bounded lossless chunks rather than in one shot (D13). The executor
        # runs the one-shot command unchanged when the job already fits the
        # budget, so a short clip costs nothing extra.
        return run_bounded_audio_reverse(
            answers, build_audio_speed_reverse_command,
            label="Audio Speed / Reverse",
            total_duration=(duration / speed if duration > 0 else None),
        )
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(duration / speed if duration > 0 else None),
        label="Audio Speed / Reverse",
    )


def run_audio_transform_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_audio_transform_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_audio_transform_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    steps = [
        Step("input_path", lambda a: True, wizard.step_input_path),
        Step("audio_track", lambda a: True, step_audio_track_for_tool),
        Step("output_location", lambda a: True, wizard.step_output_location),
        Step("audio_transform_gui", lambda a: True, step_audio_transform_editor),
        Step("start_now", lambda a: not a.get("_audio_transform_noop"), step_audio_transform_start_now),
    ]
    try:
        run_mode_steps(answers, steps)
    except ValueError as exc:
        appio.error(str(exc))
        return None
    ensure_audio_input(answers)
    if answers.get("_audio_transform_noop"):
        appio.note("Audio transform was not enabled. Returning to the first question.")
        return None
    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The command above is ready to run manually.")
        warning = audio_reverse_one_shot_warning(answers)
        if warning:
            appio.note("Note: " + warning)
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    keep_duration = total_keep_duration(answers.get("audio_cut_keep_ranges") or [])
    if keep_duration <= 0:
        keep_duration = duration
    speed = max(0.001, float(answers.get("audio_speed_factor", DEFAULT_SPEED_FACTOR) or DEFAULT_SPEED_FACTOR))
    if answers.get("reverse_audio"):
        # Same bounded plan as the standalone Audio Speed / Reverse tool: the
        # cuts are applied once on the forward pass, the reversal is chunked,
        # and speed/LoudNorm stay continuous on the joined result (D13).
        return run_bounded_audio_reverse(
            answers, build_audio_transform_command,
            label="Audio Cut / Speed / Reverse",
            total_duration=(keep_duration / speed if keep_duration > 0 else None),
        )
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(keep_duration / speed if keep_duration > 0 else None),
        label="Audio Cut / Speed / Reverse",
    )


__all__ = [
    'run_video_speed_reverse_mode',
    '_run_video_speed_reverse_mode_impl',
    'run_audio_cut_mode',
    '_run_audio_cut_mode_impl',
    'run_audio_speed_reverse_mode',
    '_run_audio_speed_reverse_mode_impl',
    'run_audio_transform_mode',
    '_run_audio_transform_mode_impl',
]
