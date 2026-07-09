"""FFmWiz encoding cluster (extracted from FFmWiz.py, method الف)."""
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
from ffmwiz.metadata import *  # noqa: F401,F403
from ffmwiz import metadata  # noqa: F401
from ffmwiz.modes import *  # noqa: F401,F403
from ffmwiz import modes  # noqa: F401
from ffmwiz.mux import *  # noqa: F401,F403
from ffmwiz import mux  # noqa: F401
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


def invalidate_capability_entry(answers: dict[str, Any]) -> None:
    """Mark the cached entry for the current encoder/container stale (remove it).
    Used when a real output inspection contradicts the cached expectation."""
    encoder = str(resolve_video_encoder(answers)[0]).lower()
    family = container_family(answers.get("output_ext"))
    ffmpeg = answers.get("ffmpeg") or "ffmpeg"
    ffprobe = answers.get("ffprobe") or "ffprobe"
    _identity, env_key = services.capability_environment_key(ffmpeg, ffprobe, encoder)
    cap_key = "%s|%s" % (encoder, family)
    _CAPABILITY_SESSION_MEMO.pop("%s::%s" % (env_key, cap_key), None)
    cache = load_capability_cache()
    caps = (cache["environments"].get(env_key, {})
            .get("capabilities", {}).get(CAPABILITY_GROUP, {}))
    if cap_key in caps:
        del caps[cap_key]
        save_capability_cache(cache)
        log_warn("Capability cache entry %s invalidated (output inspection mismatch)." % cap_key)


def effective_source_dar(answers: dict[str, Any]) -> float | None:
    """Return the effective source display aspect ratio, or None if unknown."""
    return sar_dar_info(answers).get("dar")


FFMPEG_REFERENCE_SECTIONS: list[tuple[str, list[str]]] = [
    ("FFmpeg version + build configuration", ["-hide_banner", "-version"]),
    ("Supported formats (container names)", ["-hide_banner", "-formats"]),
    ("Output muxers", ["-hide_banner", "-muxers"]),
    ("Input demuxers", ["-hide_banner", "-demuxers"]),
    ("Codecs (all)", ["-hide_banner", "-codecs"]),
    ("Encoders", ["-hide_banner", "-encoders"]),
    ("Decoders", ["-hide_banner", "-decoders"]),
    ("Bitstream filters", ["-hide_banner", "-bsfs"]),
    ("Filters", ["-hide_banner", "-filters"]),
    ("Protocols", ["-hide_banner", "-protocols"]),
    ("Hardware acceleration methods", ["-hide_banner", "-hwaccels"]),
    ("Pixel formats", ["-hide_banner", "-pix_fmts"]),
    ("Sample (audio) formats", ["-hide_banner", "-sample_fmts"]),
    ("Channel layouts", ["-hide_banner", "-layouts"]),
    ("Colors / color spaces", ["-hide_banner", "-colors"]),
]


def generate_ffmpeg_reference_text(ffmpeg_path: str) -> str:
    """Run ffmpeg's various -list commands and return a single combined
    plain-text reference. Each section captures the live output from the
    installed ffmpeg.exe so the reference is always accurate for THIS build."""
    header = [
        "============================================================",
        " FFmWiz - FFmpeg capability reference",
        "============================================================",
        " This file was auto-generated from your installed ffmpeg.exe.",
        f" ffmpeg binary: {ffmpeg_path}",
        " Delete this file (or pass --refresh-ffmpeg-reference to the",
        " launcher) to regenerate it.",
        "",
        " Notes:",
        "   - FFmpeg support is build-specific. Codecs, encoders,",
        "     muxers, filters, protocols, and hwaccels listed here",
        "     reflect what THIS ffmpeg binary supports - nothing more.",
        "   - To inspect any single list interactively, run e.g.:",
        "       ffmpeg -hide_banner -encoders",
        "       ffmpeg -hide_banner -decoders",
        "       ffmpeg -hide_banner -hwaccels",
        "       ffmpeg -h encoder=hevc_nvenc",
        "       ffmpeg -h muxer=mp4",
        "============================================================",
        "",
    ]
    parts: list[str] = ["\n".join(header)]
    for title, args in FFMPEG_REFERENCE_SECTIONS:
        parts.append("\n" + "-" * 60)
        parts.append(f" {title}")
        parts.append(" Command: ffmpeg " + " ".join(args))
        parts.append("-" * 60 + "\n")
        try:
            result = subprocess.run(
                [ffmpeg_path, *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            output = result.stdout.strip() or "(no output)"
        except Exception as exc:
            output = f"(failed to run: {exc})"
        parts.append(output)
        parts.append("")
    return "\n".join(parts)


def ensure_ffmpeg_reference_file(path: Path, ffmpeg_path: str, force: bool = False) -> None:
    """Create the FFmpeg capability reference text file next to the script if
    it does not already exist. Pass force=True to regenerate."""
    if path.exists() and not force:
        return
    try:
        content = generate_ffmpeg_reference_text(ffmpeg_path)
        path.write_text(content, encoding="utf-8")
        appio.note(f"Generated FFmpeg capability reference: {path}")
    except OSError as exc:
        appio.note(f"Could not write FFmpeg reference next to the script: {exc}")


def build_cpu_fallback_from_cuda_filter(answers: dict[str, Any]) -> str | None:
    cpu_filter = build_cpu_video_filter(answers)
    if not cpu_filter:
        return None
    cuda_format = cuda_pixel_format_for_output(answers)
    return f"hwdownload,format={cuda_format},{cpu_filter},format={cuda_format},hwupload_cuda"


def build_separator_job_specs(answers: dict[str, Any]) -> list[dict[str, Any]]:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    segments = separator_ranges(answers.get("separator_points"), duration)
    if len(segments) <= 1:
        return []
    source_keep_ranges = normalize_cut_ranges(list(answers.get("cut_keep_ranges") or []), duration)
    # Reuse the exact per-part output paths already computed by
    # build_ffmpeg_command (and shown in the summary), so the per-part encode
    # writes the same files. Align them to the KEPT parts in order.
    prebuilt_paths = [Path(p) for p in (answers.get("split_output_paths") or [])]
    specs: list[dict[str, Any]] = []
    kept = 0
    for index, segment in enumerate(segments, start=1):
        keep_ranges = intersect_keep_ranges_with_segment(source_keep_ranges, segment, duration)
        if not keep_ranges:
            log_info(f"Split part {index} skipped because cuts remove the whole part: {segment}")
            continue
        if kept < len(prebuilt_paths):
            output_path = prebuilt_paths[kept]
        else:
            output_path = separator_output_path(answers, index)
        kept += 1
        job_answers = dict(answers)
        job_answers["output_location"] = output_path.parent
        job_answers["output_name_stem"] = output_path.stem
        job_answers["output_ext"] = output_path.suffix.lstrip(".") or str(answers.get("output_ext") or "mp4")
        job_answers["output_collision_suffix"] = ""
        job_answers["cut_keep_ranges"] = keep_ranges
        job_answers.pop("separator_points", None)
        job_answers.pop("separator_jobs", None)
        job_answers.pop("cmd", None)
        job_answers.pop("output_path", None)
        cmd = build_ffmpeg_command(job_answers)
        specs.append(
            {
                "index": index,
                "segment": segment,
                "keep_ranges": keep_ranges,
                "output_path": job_answers["output_path"],
                "cmd": cmd,
                "answers": job_answers,
            }
        )
    return specs


def run_separator_main_encode(answers: dict[str, Any]) -> tuple[int, float]:
    specs = build_separator_job_specs(answers)
    if not specs:
        return run_ffmpeg_with_progress(
            answers["cmd"],
            total_duration=None,
            label="FFmpeg encode",
        )
    appio.note(f"Split mode will write {len(specs)} output file(s) one at a time.")
    started_at = time.perf_counter()
    failures = 0
    completed = 0
    for position, spec in enumerate(specs, start=1):
        job_answers = spec["answers"]
        start, end = spec["segment"]
        print()
        print(paint(f"Split part [{position}/{len(specs)}]: {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}", Color.BOLD + Color.LIGHT_BLUE))
        if reverse_video_needs_segmented_main_encode(job_answers):
            rc, _ = run_segmented_reverse_main_encode(job_answers)
        else:
            part_source_seconds = total_keep_duration(job_answers.get("cut_keep_ranges") or [])
            part_speed = encode_video_speed_factor(job_answers) if video_speed_transform_enabled(job_answers) else 1.0
            part_output_seconds = part_source_seconds / max(0.01, float(part_speed or 1.0))
            rc, _ = run_ffmpeg_with_progress(
                spec["cmd"],
                total_duration=max(0.001, part_output_seconds),
                label=f"Split part {position}/{len(specs)}",
            )
        if rc == 0:
            completed += 1
            appio.note(f"Finished {Path(spec['output_path']).name}")
        else:
            failures += 1
            appio.error(f"Failed Split part {position} with exit code {rc}.")
    elapsed = time.perf_counter() - started_at
    if failures:
        appio.error(f"Split mode completed with {completed} success(es) and {failures} failure(s).")
        return 1, elapsed
    appio.note(f"Split mode completed successfully: {completed} file(s).")
    return 0, elapsed


def build_main_encode_reverse_segment_command(
    answers: dict[str, Any],
    start: float,
    end: float,
    output_path: Path,
) -> list[str]:
    segment_answers = dict(answers)
    segment_answers["cut_keep_ranges"] = [(start, end)]
    segment_answers["output_location"] = output_path.parent
    segment_answers["output_name_stem"] = output_path.stem
    segment_answers["output_ext"] = output_path.suffix.lstrip(".") or str(answers.get("output_ext") or "mp4")
    segment_answers["output_collision_suffix"] = ""
    segment_answers.pop("output_path", None)
    return build_ffmpeg_command(segment_answers)


def run_segmented_reverse_main_encode(answers: dict[str, Any]) -> tuple[int, float]:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    if duration <= 0:
        return run_ffmpeg_with_progress(
            answers["cmd"],
            total_duration=None,
            label="FFmpeg encode",
        )
    original_keep_ranges = normalize_cut_ranges(list(answers.get("cut_keep_ranges") or []), duration)
    chunks = split_ranges_for_reverse_segments(original_keep_ranges, duration)
    if not chunks:
        return 1, 0.0
    speed = encode_video_speed_factor(answers)
    output_path = Path(answers["output_path"])
    appio.note(
        f"Reverse encode uses {len(chunks)} segment(s) of up to {int(REVERSE_SEGMENT_SECONDS)}s "
        "to avoid buffering the full video in RAM."
    )
    started_at = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ffmwiz_reverse_encode_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        segment_ext = output_path.suffix.lstrip(".") or str(answers.get("output_ext") or "mp4")
        segment_paths: list[Path] = []
        for idx, (start, end) in enumerate(chunks, start=1):
            segment_path = tmpdir / f"reverse_encode_seg_{idx:04d}.{segment_ext}"
            segment_paths.append(segment_path)
            cmd = build_main_encode_reverse_segment_command(answers, start, end, segment_path)
            log_info(f"Reverse encode segment {idx}/{len(chunks)} command: {command_to_powershell(cmd)}")
            appio.note(f"Reverse encode segment {idx}/{len(chunks)}: {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}")
            rc, _ = run_ffmpeg_with_progress(
                cmd,
                total_duration=max(0.001, (end - start) / speed),
                label=f"Reverse encode segment {idx}/{len(chunks)}",
            )
            if rc != 0:
                return rc, time.perf_counter() - started_at
        concat_list = tmpdir / "concat.txt"
        write_concat_list(list(reversed(segment_paths)), concat_list)
        concat_cmd = build_concat_copy_command(answers["ffmpeg"], concat_list, output_path)
        log_info("Reverse encode concat command: " + command_to_powershell(concat_cmd))
        appio.note("Concatenating reversed encoded segments...")
        rc, _ = run_ffmpeg_with_progress(
            concat_cmd,
            total_duration=(total_keep_duration(chunks) / speed if chunks else None),
            label="Reverse encode concat",
        )
        return rc, time.perf_counter() - started_at


def load_answers_from_config(answers: dict[str, Any], path: Path, skip_crop: bool = False) -> None:
    ensure_config_file(path)
    try:
        config = parse_env_config(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValueError(f"Could not read config file: {path}. {exc}") from exc

    input_value = config_value(config, "input_path")
    if not input_value:
        raise ValueError(f"input_path is required in config file: {path}")
    input_path = terminal_path(input_value)
    if not input_path.exists() or not input_path.is_file():
        raise ValueError(f"input_path does not exist: {input_path}")

    load_input_metadata(answers, input_path)
    trackmanager.print_source_info(answers)
    apply_config_settings_after_input(answers, config, skip_crop=skip_crop, force_video_options=skip_crop)


def run_metadata_report_inspect(answers: dict[str, Any]) -> None:
    while True:
        print()
        print(paint("Metadata Report / Inspect", Color.BOLD + Color.LIGHT_BLUE))
        print(metadata_menu_item(1, "Full ffprobe JSON report", default=True))
        print(metadata_menu_item(2, "Human-readable stream report"))
        print(metadata_menu_item(3, "Tags-only report"))
        print(metadata_menu_item(4, "Color metadata report"))
        print(metadata_menu_item(5, "Disposition report"))
        print(metadata_menu_item(6, "Chapter report"))
        choice = metadata.metadata_menu_selection("1")
        if is_back_value(choice):
            return
        report_type = {"1": "full", "2": "human", "3": "tags", "4": "color", "5": "disposition", "6": "chapters"}.get(choice)
        if not report_type:
            appio.error("Enter a menu number from 1 to 6.")
            continue
        try:
            output_path = write_metadata_report(answers["metadata_input_path"], report_type, answers)
            appio.note(f"Metadata report written: {output_path}")
            log_info(f"Metadata report written: type={report_type}; path={output_path}")
        except Exception as exc:
            log_exception("Metadata report failed")
            appio.error(str(exc))


def run_crop_only_prompt(answers: dict[str, Any]) -> None:
    if not output_has_video(answers):
        appio.note("This output has no video stream, so crop selection was skipped.")
        return

    steps = [
        Step("crop_enabled", output_has_video, wizard.step_crop_enabled),
        Step("crop_top", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_top),
        Step("crop_left", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_left),
        Step("crop_right", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_right),
        Step("crop_bottom", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_bottom),
    ]

    def next_index(start: int) -> int:
        idx = start
        while idx < len(steps) and not steps[idx].applicable(answers):
            idx += 1
        return idx

    def prev_index(start: int) -> int:
        idx = start
        while idx > 0 and (not steps[idx].applicable(answers) or step_is_auto_back_skip(steps[idx], answers)):
            idx -= 1
        return max(0, idx)

    idx = next_index(0)
    while idx < len(steps):
        try:
            answers["_question_number"] = answers.get("_question_offset", 0) + idx + 1
            answers["_last_question_number"] = answers["_question_number"]
            steps[idx].run(answers)
            idx = next_index(idx + 1)
        except Back:
            if idx == 0:
                raise
            idx = prev_index(idx - 1)


__all__ = [
    'build_cpu_fallback_from_cuda_filter',
    'build_main_encode_reverse_segment_command',
    'build_separator_job_specs',
    'effective_source_dar',
    'ensure_ffmpeg_reference_file',
    'generate_ffmpeg_reference_text',
    'invalidate_capability_entry',
    'load_answers_from_config',
    'run_crop_only_prompt',
    'run_metadata_report_inspect',
    'run_segmented_reverse_main_encode',
    'run_separator_main_encode',
    'FFMPEG_REFERENCE_SECTIONS',
]
