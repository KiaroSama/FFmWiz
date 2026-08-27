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
from typing import Any, Callable, NamedTuple
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
from ffmwiz.support.L01_subtitles import *  # noqa: F401,F403
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
    if answers.get("join_input_items"):
        # Every part here is rebuilt with the SINGLE-input builder, which reads
        # only answers["input_path"]. Producing specs for a join would silently
        # encode input 0 alone, so refuse and let the caller use the join
        # command's own multi-output split graph.
        log_info("Split job specs skipped: this is a join; the join command owns its own split graph.")
        return []
    if not split_parts_share_the_source_clock(answers):
        # Same shape of guard, second reason: the ranges below are SOURCE
        # seconds, and a cut/speed/reverse means the split points are not. Let
        # the caller run the built multi-output graph instead (F02).
        log_info("Split job specs skipped: the timeline is transformed, so the split points "
                 "are processed-clock seconds and the built multi-output graph owns them.")
        return []
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


def run_bounded_audio_reverse_encode(answers: dict[str, Any], cmd: list[str], *,
                                     total_duration: float | None,
                                     label: str) -> tuple[int, float]:
    """Reverse an ENCODE job's audio without disturbing anything else about it.

    `run_bounded_audio_reverse` was written for the standalone AUDIO TOOLS,
    whose source is a track plus an optional cover image. Wiring the main
    executor into it applied those semantics to jobs that are nothing like it,
    and three separate kinds of silent data loss followed. All three returned
    exit code 0:

        D01  a video + audio job came back audio-only
             OUTPUT_STREAM_TYPES ['audio']
        D02  a two-input Join produced only the first input
             EXPECTED_DURATION 4.0   ACTUAL_DURATION 2.0   ACTUAL_STREAMS ['audio']
        D03  a Split wrote one unsplit audio-only Part01 and no Part02
             EXISTS [true, false]

    The shape that keeps the workflow is the one the video side already uses:
    reverse in its own stage, then let the ORIGINAL job run over the result.

      1. JOIN forward, when there is a join, so the reversal sees the whole
         timeline rather than input 1 (D02).
      2. REVERSE the selected audio of that source into a lossless scratch, in
         bounded chunks. `bounded_audio_reverse_to_file` is the same reversal
         the standalone tools run; there is no second implementation of it.
      3. MUX that audio back onto the source's own picture and every other
         stream it carries, through the SAME stream policy the video reverse
         mux uses (D01).
      4. REBUILD the original job on that file with the reversal already spent.
         Cuts, speed, subtitles, chapters and Split all run exactly as they
         would have, because nothing else about the job was touched (D03).
    """
    started_at = time.perf_counter()
    indices = audio_reverse_indices(answers)
    if not audio_reverse_needs_staging(answers, indices):
        return run_ffmpeg_with_progress(
            cmd, total_duration=total_duration, label=label)

    output_path = Path(answers["output_path"])
    # Captured BEFORE any stage rewrites them: these are the paths the summary
    # showed and the user confirmed.
    planned_output_paths = list(answers.get("split_output_paths") or [])
    workspace = artifact_lease(answers).register(
        Path(tempfile.mkdtemp(prefix="ffmwiz_audio_reverse_")))
    source_answers = answers

    if answers.get("join_input_items"):
        items = join_items_from_answers(answers)
        if not items:
            return 1, time.perf_counter() - started_at
        joined = workspace / f"joined_forward.{INTERMEDIATE_CONTAINER_EXT}"
        # Owns the geometry and nothing else, exactly as the video pipeline's
        # forward stage does: the reversal and every later edit belong to the
        # stages after it.
        forward = intermediate_profile(
            stage_answers(answers, owns=GEOMETRY_TRANSFORMATIONS))
        forward["output_path"] = joined
        appio.note("Audio reverse across a join: joining first, so the whole "
                   "joined timeline is reversed rather than the first input.")
        code, _elapsed = run_ffmpeg_with_progress(
            wizard.build_join_encode_command(forward, items, joined),
            total_duration=sum(join_item_picture_span(item) for item in items) or None,
            label="Joining before audio reverse")
        if code != 0 or not joined.exists():
            return (code or 1), time.perf_counter() - started_at
        source_answers = _single_input_answers(answers, joined)
        indices = audio_reverse_indices(source_answers)

    reversed_audio = workspace / f"audio_reversed.{INTERMEDIATE_CONTAINER_EXT}"
    code, _elapsed = bounded_audio_reverse_to_file(
        source_answers, reversed_audio, audio_indices=indices, label=label)
    if code != 0 or not reversed_audio.exists():
        return (code or 1), time.perf_counter() - started_at

    rebased = workspace / f"reversed_audio_source.{INTERMEDIATE_CONTAINER_EXT}"
    maps, dispositions, warnings = reverse_mux_stream_policy(source_answers)
    for warning in warnings:
        appio.note(warning)
    mux = [str(answers["ffmpeg"]), "-y" if OVERWRITE_OUTPUT else "-n", "-hide_banner",
           "-i", str(source_answers["input_path"]), "-i", str(reversed_audio),
           *maps, *dispositions, "-c", "copy",
           "-avoid_negative_ts", "make_zero", str(rebased)]
    log_info("Audio reverse remux: " + command_to_powershell(mux))
    code, _elapsed = run_ffmpeg_with_progress(
        mux, total_duration=total_duration, label="Restoring the picture")
    if code != 0 or not rebased.exists():
        return (code or 1), time.perf_counter() - started_at

    final = _single_input_answers(answers, rebased)
    final["reverse_audio"] = False
    final["audio_cut_keep_ranges"] = []
    final["audio_keep_ranges"] = []
    final["output_path"] = output_path
    # Keep the names the summary already showed. By this point `input_path` is
    # the pipeline's own scratch, so a Split would derive its parts from THAT
    # and write `reversed_audio_source_Part01.mkv` -- measured, and the same
    # class of defect the staged video Split had.
    resolved_stem = str(answers.get("output_name_stem") or "").strip()
    if not resolved_stem and planned_output_paths:
        resolved_stem = re.sub(r"_Part\d+$", "", Path(planned_output_paths[0]).stem)
    if not resolved_stem:
        resolved_stem = Path(answers["input_path"]).stem
    final["output_name_stem"] = resolved_stem
    final["cmd"] = build_ffmpeg_command(final)
    answers["split_output_paths"] = final.get("split_output_paths") or []
    answers["split_part_intervals"] = final.get("split_part_intervals") or []
    code, _elapsed = execute_encode_plan(
        final, final["cmd"], total_duration=total_duration,
        label=label)
    answers["output_path"] = final.get("output_path", output_path)
    return code, time.perf_counter() - started_at


def execute_encode_plan(answers: dict[str, Any], cmd: list[str], *,
                        total_duration: float | None, label: str,
                        **progress_kwargs: Any) -> tuple[int, float]:
    """The single place that chooses between a bounded plan and one-shot execution.

    The project promises that reverse is segmented so a long clip does not have
    to be buffered whole, but each caller decided for itself and Folder Encode
    simply never asked -- it called run_ffmpeg_with_progress directly, so a
    reversed folder job ran the full-buffer `reverse` filter with the UI still
    claiming the safe behaviour (R09).

    Routing every caller through here means the promise and the execution can
    only disagree in one place. A join deliberately does NOT segment: the
    segment builder understands a single input, and reversing input 1 alone is
    far worse than buffering (R01). `reverse_video_needs_segmented_main_encode`
    owns that decision.
    """
    # Last line of defence: the join and audio-only builders never go through
    # build_ffmpeg_command, so this is the only point every executor shares.
    # Idempotent -- it returns immediately when the flag is unset or supported.
    two_pass_off = normalize_cpu_two_pass_selection(answers)
    if two_pass_off:
        appio.note(f"CPU two-pass was turned off for this job: {two_pass_off}.")
        log_info(f"CPU two-pass disabled before execution: {two_pass_off}")
    if reverse_video_needs_segmented_main_encode(answers) and not answers.get("separator_points"):
        answers["cmd"] = cmd
        return run_segmented_reverse_main_encode(answers)
    if (answers.get("reverse_video")
            and (answers.get("join_input_items") or answers.get("separator_points"))):
        # A join or a Split means the one-pass plan would hand `reverse` a whole
        # timeline: 336 GiB of decoded frames for an hour of joined 1080p30, or
        # 56 GiB for ten split minutes of it. Neither can finish, so both go
        # through the staged pipeline instead of being warned about.
        answers["cmd"] = cmd
        return run_bounded_reverse_pipeline(answers)
    if answers.get("reverse_audio") and not answers.get("reverse_video"):
        # `areverse` buffers its WHOLE input, so an audio reverse is unbounded
        # for exactly the same reason a video one is -- and only the two
        # standalone audio tools had a bounded plan. The main encode, a join
        # reversing audio alone and every Folder Encode item reached the
        # one-shot command directly (D13). The executor runs that same command
        # unchanged when the job already fits the budget, so a short clip costs
        # nothing extra.
        answers["cmd"] = cmd
        # NOT `run_bounded_audio_reverse`: that one is the standalone AUDIO
        # TOOLS' plan, and applying its semantics to an encode job dropped the
        # picture, the later Join inputs and the Split plan (D01-D03).
        return run_bounded_audio_reverse_encode(
            answers, cmd, total_duration=total_duration, label=label)
    # The two-pass check lives here too, so no executor can quietly skip it.
    # The main dispatcher used to duplicate this whole selection and reach the
    # runner directly, which is how a retained cpu_two_pass was downgraded to
    # one pass on some paths and executed unsupported on others (F10/F11).
    if cpu_two_pass_enabled_for_command(answers, cmd):
        return run_cpu_two_pass_ffmpeg(
            cmd, answers, total_duration=total_duration,
            progress_output_paths=progress_kwargs.get("progress_output_paths"))
    return run_ffmpeg_with_progress(cmd, total_duration=total_duration,
                                    label=label, **progress_kwargs)


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
    'build_separator_job_specs',
    'effective_source_dar',
    'ensure_ffmpeg_reference_file',
    'generate_ffmpeg_reference_text',
    'invalidate_capability_entry',
    'load_answers_from_config',
    'run_crop_only_prompt',
    'run_metadata_report_inspect',
    'execute_encode_plan',
    'run_bounded_audio_reverse_encode',
    'run_separator_main_encode',
    'FFMPEG_REFERENCE_SECTIONS',
]


# The staged reverse pipeline lives in its own module now; re-exported here
# so every existing `from ffmwiz.encoding import *` keeps working.
from ffmwiz import reverse_pipeline  # noqa: E402
from ffmwiz.reverse_pipeline import *  # noqa: E402,F401,F403
__all__ += reverse_pipeline.__all__
