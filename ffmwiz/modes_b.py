"""FFmWiz modes overflow (modes_b) — split for file size.

Back-imports modes and is re-exported by it, so every consumer of
`from ffmwiz.modes import *` still sees the full set. Monkeypatch-safe.
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
from ffmwiz.wizard import *  # noqa: F401,F403
from ffmwiz import wizard  # noqa: F401
from ffmwiz.modes import *  # noqa: E402,F401,F403  (back-import)
from ffmwiz import modes  # noqa: E402,F401  (qualified self-ref for patched names)


def run_extract_stream_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    steps = [
        Step("input_path", lambda a: True, step_extract_stream_input),
        Step("extract_stream_index", lambda a: True, step_extract_stream_index),
        Step("extract_format", lambda a: True, step_extract_stream_format),
        Step("start_now", lambda a: True, step_extract_stream_start_now),
    ]
    try:
        run_mode_steps(answers, steps)
    except Back:
        appio.note("Returning to main menu.")
        return None
    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The plan above is ready to run manually.")
        return None
    jobs = answers["_extract_jobs"]
    ffmpeg = answers["ffmpeg"]
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    total_rc = 0
    ok = 0
    started_at = time.perf_counter()
    # Group by source file: every stream extracted from a container costs a FULL
    # read of it, because audio and subtitle packets are interleaved throughout.
    # Running one FFmpeg per stream therefore re-read the whole file N times --
    # measured on a real 722 MB MKV, two streams took 5.80 s as separate runs
    # against 4.57 s as one run with two outputs.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for job in jobs:
        grouped.setdefault(str(job["path"]), []).append(job)
    done = 0
    for source, source_jobs in grouped.items():
        pairs: list[tuple[dict[str, Any], Path]] = []
        for job in source_jobs:
            out = Path(job["output_path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            pairs.append((job["stream"], out))
            done += 1
            idx = stream_global_index(job["stream"])
            appio.note(f"[{done}/{len(jobs)}] {Path(source).name} #{idx} -> {out.name}")
            log_info(f"Extract Stream job {done}/{len(jobs)}: input={source}; index={idx}; output={out}")
        cmd = build_extract_streams_command(ffmpeg, Path(source), pairs)
        if len(pairs) > 1:
            log_info(f"Extract Stream: {len(pairs)} streams from {source} in one pass (one read).")
        longest = max(
            (services.stream_duration_seconds(job["stream"], job.get("fmt")) or 0.0)
            for job in source_jobs
        )
        rc, _elapsed = run_ffmpeg_with_progress(
            cmd,
            total_duration=(longest if longest > 0 else None),
            label="Extract Stream",
        )
        if rc == 0:
            ok += len(pairs)
        else:
            total_rc = rc
            appio.error(f"Extraction failed (exit {rc}) for {Path(source).name}.")
    elapsed = time.perf_counter() - started_at
    appio.note(f"Extract Stream done: {ok}/{len(jobs)} stream(s) extracted.")
    log_info(f"Extract Stream finished: ok={ok}/{len(jobs)}; elapsed={elapsed:.2f}s")
    return total_rc, elapsed


def ask_hardsub_source_video(answers: dict[str, Any]) -> None:
    while True:
        video_example = example_text(r"E:\Input\video.mkv")
        value = appio.ask_required(
            appio.question_prompt(
                answers,
                "Enter source video file path",
                f"drag and drop a video file here or paste a path; example: {video_example}",
            )
        )
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
            log_exception(f"ffprobe metadata load failed for Hard Sub source video: {input_path}")
            appio.error(f"ffprobe could not read the file. See log file: {_log_file_text()}")
            continue
        if not answers.get("video_streams"):
            appio.error("The input must contain a video stream.")
            continue
        answers["hardsub_hdr_info"] = video_hdr_dolby_info(answers["video_streams"][0])
        trackmanager.print_source_info(answers)
        return


def run_hardsub_encode_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_hardsub_encode_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_hardsub_encode_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    steps = [
        Step("input_path", lambda a: True, ask_hardsub_source_video),
        Step("output_location", lambda a: True, step_hardsub_output_location),
        Step("output_format", lambda a: True, step_hardsub_output_format),
        Step("hardsub_subtitle", lambda a: True, step_hardsub_subtitle_source),
        Step("hardsub_fontsdir", lambda a: True, step_hardsub_fontsdir),
        Step("video_codec", lambda a: True, step_hardsub_video_codec),
        Step("use_gpu", lambda a: True, step_hardsub_use_gpu),
        Step(
            "nvenc_multipass",
            lambda a: nvenc_multipass_prompt_applicable(a),
            lambda a: ask_nvenc_multipass_if_applicable(a, workflow_name="HardSub", quality_oriented=True),
        ),
        Step("hardsub_quality", lambda a: True, step_hardsub_quality),
        Step("hardsub_hdr", lambda a: True, step_hardsub_hdr_handling),
        Step("hardsub_audio", lambda a: True, step_hardsub_audio_mode),
        Step("hardsub_audio_container", lambda a: True, step_hardsub_audio_container_policy),
        Step("color_range", color_range_prompt_applicable, step_color_range),
        Step("start_now", lambda a: True, step_hardsub_start_now),
    ]

    idx = 0
    while idx < len(steps):
        try:
            answers["_question_number"] = idx + 1
            steps[idx].run(answers)
            idx += 1
        except Back:
            if idx == 0:
                raise
            idx -= 1
            while idx > 0 and step_is_auto_back_skip(steps[idx], answers):
                idx -= 1

    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The command above is ready to run manually.")
        return None
    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    log_info(
        f"Hard Sub Encode starting: input={answers['input_path']}; "
        f"output={answers.get('output_path')}; subtitle_source={answers.get('hardsub_subtitle_source')}"
    )
    return run_ffmpeg_with_progress(
        answers["cmd"],
        total_duration=(duration if duration > 0 else None),
        label="Hard Sub Encode",
    )


__all__ = [
    'run_extract_stream_mode',
    'ask_hardsub_source_video',
    'run_hardsub_encode_mode',
    '_run_hardsub_encode_mode_impl',
]
