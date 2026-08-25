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


def build_main_encode_reverse_segment_command(
    answers: dict[str, Any],
    start: float,
    end: float,
    output_path: Path,
) -> list[str]:
    # Open the lease BEFORE the shallow copy. dict() shares the container only
    # if the key is already there, so without this each segment got a lease of
    # its own and every per-segment retimed-subtitle and chapter directory
    # leaked -- the R06 trap, on the one path that makes the most copies.
    artifact_lease(answers)
    segment_answers = dict(answers)
    segment_answers["cut_keep_ranges"] = [(start, end)]
    segment_answers["output_location"] = output_path.parent
    segment_answers["output_name_stem"] = output_path.stem
    segment_answers["output_ext"] = output_path.suffix.lstrip(".") or str(answers.get("output_ext") or "mp4")
    segment_answers["output_collision_suffix"] = ""
    segment_answers.pop("output_path", None)
    return build_ffmpeg_command(segment_answers)


def reverse_segment_seconds(answers: dict[str, Any]) -> float:
    """How many seconds of video one reverse segment may safely hold.

    Reads the geometry out of `answers` and defers the arithmetic to
    `reverse_segment_seconds_for`, which lives beside the chunk splitter so the
    standalone Video Speed / Reverse mode shares the same budget instead of
    calling the splitter with no size at all (B06).
    """
    stream = (answers.get("video_streams") or [{}])[0]
    try:
        fps = float(answers.get("fps") or services.get_video_fps(answers) or 0.0)
    except Exception:
        fps = 0.0
    seconds = reverse_segment_seconds_for(
        stream.get("width"), stream.get("height"), fps, stream.get("pix_fmt"))
    log_info(
        "Reverse budget: %sx%s %s at %.3f fps -> %.3f s/segment (peak cap "
        "%.2f GiB incl. %.2f GiB overhead, %.0f%% frame safety)"
        % (stream.get("width") or "?", stream.get("height") or "?",
           stream.get("pix_fmt") or "unknown", fps or 0.0, seconds,
           REVERSE_PEAK_BUDGET_BYTES / 1024 ** 3,
           REVERSE_FIXED_OVERHEAD_BYTES / 1024 ** 3,
           (REVERSE_FRAME_SAFETY - 1) * 100))
    if not (stream.get("width") and stream.get("height")):
        log_warn("Reverse budget: frame geometry unknown; the segment length "
                 "assumes 1080p60 10-bit. Probe the input for a real bound.")
    return seconds


# Every user edit the staged reverse pipeline can apply, grouped by the
# transformation that owns it. A stage that does not own a transformation must
# not carry its keys: the pipeline used to clear only the VIDEO edits, so an
# independent 2x audio speed was applied by the forward-join stage, again by
# the segmented reverse and again by the final split. Measured on two joined
# 2 s clips: 4.100 s of video against 1.111 s of audio, where one application
# owes about 2 s (B01).
STAGE_TRANSFORMATIONS: dict[str, tuple[str, ...]] = {
    "cuts": ("cut_keep_ranges",),
    "audio_cuts": ("audio_cut_keep_ranges", "audio_cut_stream_copy"),
    "video_speed": ("video_speed_enabled", "video_speed_factor"),
    "audio_speed": ("audio_speed_enabled", "audio_speed_factor",
                    "audio_speed_from_video"),
    "video_reverse": ("reverse_video",),
    "audio_reverse": ("reverse_audio",),
    "loudnorm": ("loudnorm_enabled", "loudnorm_mode", "loudnorm_measured",
                 "loudnorm_target_i"),
    "split": ("separator_points", "split_output_paths", "split_part_intervals"),
    # GEOMETRY. `build_cpu_video_filter` orders crop -> fps -> scale/pad ->
    # speed/reverse, so every one of these is a semantic transformation that a
    # stage can apply a second time. They were missing, which is why a
    # `stage_answers(..., owns=())` "neutral" stage still cropped: a real
    # 160x120 Join + Reverse + Split asking for 10 px off each side produced
    # 120x120 parts instead of 140x120, with `crop=` in joined_forward.mkv, in
    # every reverse segment AND in the final Split graph (D01). FPS and resize
    # leak the same way; a repeated scale is not free even when it is
    # dimensionally idempotent, because it re-processes an already lossy
    # intermediate and changes the frame layout the reverse budget is sized
    # from (D02).
    "crop": ("crop_enabled", "crop_top", "crop_left", "crop_right",
             "crop_bottom", "crop_box_dimensions", "cropped_aspect_ratio"),
    "fps": ("fps",),
    "resize": ("resolution", "final_resolution"),
}

# Geometry travels together: cropping in one stage and resizing in another
# would make the second stage scale a frame the first already changed.
GEOMETRY_TRANSFORMATIONS: tuple[str, ...] = ("crop", "fps", "resize")


def _requests_video_speed(answers: dict[str, Any]) -> bool:
    return (bool(answers.get("video_speed_enabled"))
            and float(answers.get("video_speed_factor") or 1.0) != 1.0)


# What makes each transformation REQUESTED. Ownership is only meaningful
# against this: a plan that owns nothing is valid for a job that asks for
# nothing, and invalid for one that asks for a crop.
_TRANSFORMATION_REQUESTED: dict[str, Any] = {
    "cuts": lambda a: bool(a.get("cut_keep_ranges")),
    "audio_cuts": lambda a: bool(a.get("audio_cut_keep_ranges")),
    "video_speed": _requests_video_speed,
    "audio_speed": lambda a: (
        (bool(a.get("audio_speed_enabled"))
         and float(a.get("audio_speed_factor") or 1.0) != 1.0)
        or (bool(a.get("audio_speed_from_video")) and _requests_video_speed(a))),
    "video_reverse": lambda a: bool(a.get("reverse_video")),
    "audio_reverse": lambda a: bool(a.get("reverse_audio")),
    "loudnorm": lambda a: (bool(a.get("loudnorm_enabled"))
                           and str(a.get("loudnorm_mode") or "off") != "off"),
    "split": lambda a: bool(a.get("separator_points")),
    "crop": lambda a: (bool(a.get("crop_enabled"))
                       and any(int(a.get(f"crop_{edge}", 0) or 0)
                               for edge in ("top", "left", "right", "bottom"))),
    "fps": lambda a: a.get("fps") is not None,
    "resize": lambda a: a.get("resolution") not in (None, "n"),
}


def requested_transformations(answers: dict[str, Any]) -> set[str]:
    """Every transformation this job actually asks for.

    Declared per transformation rather than inferred, so a new key added to
    `STAGE_TRANSFORMATIONS` without a matching predicate fails loudly here
    instead of being silently treated as never requested.
    """
    missing = set(STAGE_TRANSFORMATIONS) - set(_TRANSFORMATION_REQUESTED)
    if missing:
        raise ValueError(
            f"transformation(s) with no requested-predicate: {sorted(missing)}")
    return {name for name, asked in _TRANSFORMATION_REQUESTED.items()
            if asked(answers)}

# Falsey neutral values, so a stage that reads a key without checking for its
# absence still sees "no transformation" rather than a stale truth.
_NEUTRAL_VALUES: dict[str, Any] = {
    "video_speed_factor": 1.0,
    "audio_speed_factor": 1.0,
    "loudnorm_mode": "off",
    # "n" is what the builders read as "keep the source size"; removing the key
    # would make `answers.get("resolution", "n")` agree by accident, but a
    # stage that reads it without a default would see nothing at all.
    "resolution": "n",
}


def intermediate_profile(staged: dict[str, Any]) -> dict[str, Any]:
    """Retune a pipeline stage that writes a SCRATCH file, not the user's output.

    An intermediate is re-encoded again by a later stage, so it must not carry
    the final output's rate control. Setting `video_crf` alone did nothing: the
    encoder builder prefers a bitrate when one is present, so a job targeting
    250 kbps wrote `joined_forward.mkv` AND the reverse segments at `-b:v 250k`
    with no effective CRF -- several low-bitrate generations before the encode
    the user actually asked for, and the same again for `-b:a 32k` (B03).

    Video becomes near-lossless CRF, audio becomes lossless FLAC, and the
    container becomes Matroska so both are always legal. Pixel format, bit
    depth and colour metadata are left alone: they are the properties the
    later stage has to preserve.
    """
    scratch = dict(staged)
    for key in ("video_bitrate_kbps", "video_bitrate_mode", "cpu_two_pass",
                "nvenc_multipass", "nvenc_multipass_skip_reason",
                "audio_bitrate_kbps"):
        scratch.pop(key, None)
    scratch["video_crf"] = REVERSE_INTERMEDIATE_CRF
    scratch["crf"] = REVERSE_INTERMEDIATE_CRF
    scratch["audio_codec"] = INTERMEDIATE_AUDIO_CODEC
    scratch["output_ext"] = INTERMEDIATE_CONTAINER_EXT
    return scratch


# Dispositions worth carrying, named explicitly. Echoing back every truthy key
# ffprobe reports would eventually hand FFmpeg a flag name its `-disposition`
# parser does not accept, and a rejected command loses more than a lost flag.
_CARRIED_DISPOSITIONS = ("default", "forced", "hearing_impaired",
                         "visual_impaired", "comment", "descriptions",
                         "original", "dub")


def _disposition_value(stream: dict[str, Any]) -> str:
    flags = (stream or {}).get("disposition") or {}
    kept = [name for name in _CARRIED_DISPOSITIONS if flags.get(name)]
    return "+".join(kept) if kept else "0"


def _selected_indices(streams: list[Any], chosen: Any) -> list[int]:
    if chosen is None or chosen is True or chosen == "all":
        return list(range(len(streams)))
    try:
        return [int(index) for index in chosen if 0 <= int(index) < len(streams)]
    except TypeError:
        return list(range(len(streams)))


def reverse_mux_stream_policy(
        answers: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """Which streams the video-only reverse mux keeps, and from which input.

    Input 0 is the reversed picture, input 1 the forward audio. The whole
    policy used to be `-map 0:v -map 1:a`, and once ANY explicit map is given
    FFmpeg selects nothing else -- so every other stream type was dropped in
    the last command of the pipeline, after the earlier stages had carried it
    faithfully. A real source came out two streams short:

        SOURCE topology  ['video', 'audio', 'subtitle', 'attachment']
        OUTPUT topology  ['video', 'audio']

    The subtitle had even been retimed onto the processed timeline and
    announced to the user before being discarded (D03), and attachments and
    data streams went the same way (D04).

    The reversed picture is the authority for everything except audio: the
    per-segment encodes already applied the user's stream selection, and the
    concat copy preserves what they produced -- measured on segments carrying
    video/audio/subtitle/attachment, `-map 0 -c copy -an` yielded
    video/subtitle/attachment. So the policy is "everything input 0 still has,
    minus its audio, plus input 1's audio", stated per type rather than by
    negative mapping so it does not depend on the FFmpeg build's handling of
    `-map -0:a`.

    Dispositions come back too. The concat DEMUXER does not carry them, which
    is invisible until you look: traced through one run, the segment held
    `default=1 forced=1` and the concat copy that consumed it held
    `default=0 forced=0`, so a subtitle the source marked default and forced
    arrived marked neither. Only the ordinary encode paths were preserving
    them, which is why nothing caught it. They are restored here, against the
    SOURCE streams, at the one point where the final output order is known.

    Returns (maps, dispositions, warnings). Attachments are only claimed when
    the chosen container can actually hold one; when it cannot, the loss is
    REPORTED rather than silent, which is the difference the brief asks for.
    """
    maps = ["-map", "0:v", "-map", "1:a", "-map", "0:s?", "-map", "0:d?"]
    warnings: list[str] = []
    has_attachments = bool(answers.get("attachment_streams"))
    if output_supports_embedded_attachments(answers):
        maps += ["-map", "0:t?"]
    elif has_attachments and answers.get("keep_embedded_attachments"):
        warnings.append(
            f"{str(answers.get('output_ext') or '').upper()} cannot store embedded "
            "attachments, so the source's attachment(s) are not carried into the "
            "reversed output. Choose MKV to keep them.")

    dispositions: list[str] = []
    for kind, key, chosen in (("a", "audio_streams", answers.get("audio_tracks")),
                              ("s", "subtitle_streams", answers.get("subtitle_tracks"))):
        streams = list(answers.get(key) or [])
        for position, index in enumerate(_selected_indices(streams, chosen)):
            dispositions += [f"-disposition:{kind}:{position}",
                             _disposition_value(streams[index])]
    return maps, dispositions, warnings


def stage_answers(answers: dict[str, Any], owns: tuple[str, ...]) -> dict[str, Any]:
    """A copy of `answers` carrying ONLY the transformations this stage owns.

    Ownership is declared, never inferred from what happens to be in a copied
    dictionary. Anything the stage does not own is removed, and the effective
    map is dropped with it so a resolution made for another stage cannot leak
    in (the same map is what let a Join's libx265 fallback survive a rebuild).
    """
    unknown = set(owns) - set(STAGE_TRANSFORMATIONS)
    if unknown:
        raise ValueError(f"unknown transformation(s): {sorted(unknown)}")
    staged = dict(answers)
    staged.pop(EFFECTIVE_SETTINGS_KEY, None)
    for name, keys in STAGE_TRANSFORMATIONS.items():
        if name in owns:
            continue
        for key in keys:
            if key in _NEUTRAL_VALUES:
                staged[key] = _NEUTRAL_VALUES[key]
            else:
                staged.pop(key, None)
    return staged


def validate_stage_plan(stages: list[tuple[str, tuple[str, ...]]],
                       answers: dict[str, Any] | None = None) -> None:
    """Every transformation the job requests is owned by EXACTLY one stage.

    Raises on a duplicate: applying a speed change twice is silent in the argv
    and only visible in the finished media, which is how B01 survived.

    Pass `answers` and it also raises on a MISSING owner. Duplicate-only
    checking could not see D01 at all -- crop was owned by no stage, so there
    was nothing to be a duplicate OF, and it simply survived into all three.
    An unowned transformation is not neutral; it is applied wherever the
    filter builder happens to look.
    """
    seen: dict[str, str] = {}
    for label, owns in stages:
        for name in owns:
            if name not in STAGE_TRANSFORMATIONS:
                raise ValueError(f"unknown transformation: {name!r}")
            if name in seen:
                raise ValueError(
                    f"transformation {name!r} is owned by both {seen[name]!r} "
                    f"and {label!r}")
            seen[name] = label
    if answers is None:
        return
    unowned = sorted(requested_transformations(answers) - set(seen))
    if unowned:
        raise ValueError(
            f"transformation(s) {unowned} are requested but owned by no stage; "
            f"they would be applied in every stage that reads them")


def _single_input_answers(answers: dict[str, Any], source: Path) -> dict[str, Any]:
    """Re-point a job at one already-produced file, keeping its output settings."""
    probe = services.ffprobe_json(answers.get("ffprobe") or "ffprobe", source)
    streams = (probe or {}).get("streams") or []
    rebased = dict(answers)
    rebased.pop("join_input_items", None)
    rebased["input_path"] = source
    rebased["probe"] = probe or {}
    rebased["format"] = (probe or {}).get("format") or {}
    rebased["video_streams"] = [s for s in streams if s.get("codec_type") == "video"]
    rebased["audio_streams"] = [s for s in streams if s.get("codec_type") == "audio"]
    rebased["subtitle_streams"] = [s for s in streams if s.get("codec_type") == "subtitle"]
    return rebased


def bounded_reverse_plan(answers: dict[str, Any],
                        workspace: Path) -> list[tuple[str, list[str]]]:
    """The staged commands this job WILL run, without running any of them.

    The summary printed the ordinary one-shot command, which for a Join or
    Split reverse carries a full-timeline `reverse` filter that execution never
    uses. Running it by hand buffers the whole timeline -- exactly what the
    staged plan exists to avoid -- so the printed command described a different
    job from the one that would run (B04).

    The intermediates are DESCRIBED rather than probed, because they do not
    exist yet: their geometry comes from the source and their codec/container
    from `intermediate_profile`, which is what actually produces them. The
    regression that keeps the two honest runs the exported plan in a fresh
    process and compares its media to the automatic path.
    """
    stages: list[tuple[str, list[str]]] = []
    extension = INTERMEDIATE_CONTAINER_EXT
    split_points = list(answers.get("separator_points") or [])
    planned_output_paths = list(answers.get("split_output_paths") or [])
    stage_source = answers

    def described(source_answers: dict[str, Any], produced: Path) -> dict[str, Any]:
        """The intermediate as it will be, built from what we know we write."""
        rebased = dict(source_answers)
        rebased.pop("join_input_items", None)
        rebased["input_path"] = produced
        streams = [dict(stream) for stream in (source_answers.get("video_streams") or [])]
        for stream in streams:
            stream["codec_name"] = "h264"
        rebased["video_streams"] = streams
        rebased["audio_streams"] = [
            {**dict(stream), "codec_name": INTERMEDIATE_AUDIO_CODEC}
            for stream in (source_answers.get("audio_streams") or [])]
        rebased["subtitle_streams"] = []
        rebased["probe"] = {"streams": streams, "format": source_answers.get("format") or {}}
        rebased["format"] = dict(source_answers.get("format") or {})
        return rebased

    # The SAME ownership the executor uses. Two copies of this decision is how
    # the exported plan drifted from the job it claimed to describe; keeping
    # the split identical is the point of validating both against one schema.
    has_join = bool(answers.get("join_input_items"))
    forward_owns = GEOMETRY_TRANSFORMATIONS if has_join else ()
    reverse_owns = ("cuts", "audio_cuts", "video_speed", "audio_speed",
                    "video_reverse", "audio_reverse", "loudnorm")
    if not has_join:
        reverse_owns = reverse_owns + GEOMETRY_TRANSFORMATIONS
    validate_stage_plan([("forward join", forward_owns),
                         ("reverse", reverse_owns),
                         ("split", ("split",) if split_points else ())],
                        answers)

    if has_join:
        items = join_items_from_answers(answers)
        if not items:
            return stages
        joined = workspace / f"joined_forward.{extension}"
        forward = intermediate_profile(stage_answers(answers, owns=forward_owns))
        forward["output_path"] = joined
        stages.append(("Join the inputs forward",
                       [str(part) for part in
                        wizard.build_join_encode_command(forward, items, joined)]))
        stage_source = described(answers, joined)

    reverse_answers = stage_answers(stage_source, owns=reverse_owns)
    reverse_answers.pop("join_input_items", None)
    if split_points:
        reversed_whole = workspace / f"reversed_whole.{extension}"
        reverse_answers = intermediate_profile(reverse_answers)
        reverse_answers["output_path"] = reversed_whole
        reverse_answers["output_location"] = workspace
        reverse_answers["output_name_stem"] = reversed_whole.stem
        reverse_answers["output_collision_suffix"] = ""
    else:
        reverse_answers["output_path"] = answers["output_path"]

    duration = services.stream_duration_seconds({}, reverse_answers.get("format")) or 0.0
    keep_ranges = normalize_cut_ranges(
        list(reverse_answers.get("cut_keep_ranges") or []), duration)
    chunks = split_ranges_for_reverse_segments(
        keep_ranges, duration, reverse_segment_seconds(reverse_answers))
    segment_ext = Path(reverse_answers["output_path"]).suffix.lstrip(".") or extension
    segments: list[Path] = []
    for index, (start, end) in enumerate(chunks, start=1):
        segment = workspace / f"reverse_encode_seg_{index:04d}.{segment_ext}"
        segments.append(segment)
        stages.append((
            f"Reverse segment {index}/{len(chunks)}",
            [str(part) for part in build_main_encode_reverse_segment_command(
                reverse_answers, start, end, segment)]))

    # The concat lists are written NOW so the exported script is runnable as
    # it stands; the executor writes its own at run time from the same order.
    ffmpeg = reverse_answers.get("ffmpeg") or "ffmpeg"
    reverse_target = Path(reverse_answers["output_path"])
    audio_follows = encode_audio_reverse_enabled(reverse_answers)
    has_audio = bool(reverse_answers.get("audio_streams")) and bool(
        reverse_answers.get("audio_tracks", True))
    if segments and has_audio and not audio_follows:
        video_list = workspace / "plan_concat_video.txt"
        audio_list = workspace / "plan_concat_audio.txt"
        write_concat_list(list(reversed(segments)), video_list)
        write_concat_list(list(segments), audio_list)
        reversed_video = workspace / f"video_reversed.{segment_ext}"
        forward_audio = workspace / f"audio_forward.{segment_ext}"
        video_cmd = build_concat_copy_command(ffmpeg, video_list, reversed_video)
        video_cmd[video_cmd.index(str(reversed_video)):] = ["-an", str(reversed_video)]
        audio_cmd = build_concat_copy_command(ffmpeg, audio_list, forward_audio)
        audio_cmd[audio_cmd.index(str(forward_audio)):] = ["-vn", str(forward_audio)]
        stages.append(("Concatenate the video in reversed order",
                       [str(part) for part in video_cmd]))
        stages.append(("Concatenate the audio in source order",
                       [str(part) for part in audio_cmd]))
        # The SAME policy object the executor uses; two hand-written map lists
        # is exactly how the exported plan drifted from the real job before.
        plan_maps, plan_dispositions, _plan_warnings = reverse_mux_stream_policy(reverse_answers)
        plan_maps = plan_maps + plan_dispositions
        stages.append(("Mux the reversed picture with its own audio", [
            str(ffmpeg), "-y", "-hide_banner",
            "-i", str(reversed_video), "-i", str(forward_audio),
            *plan_maps, "-c", "copy",
            "-avoid_negative_ts", "make_zero", str(reverse_target)]))
    elif segments:
        concat_list = workspace / "plan_concat.txt"
        write_concat_list(list(reversed(segments)), concat_list)
        stages.append(("Concatenate the reversed segments",
                       [str(part) for part in build_concat_copy_command(
                           ffmpeg, concat_list, reverse_target)]))

    if split_points:
        split_answers = stage_answers(
            described(answers, Path(reverse_answers["output_path"])), owns=("split",))
        split_answers.pop("split_output_paths", None)
        split_answers.pop("split_part_intervals", None)
        split_answers["separator_points"] = split_points
        resolved_stem = str(answers.get("output_name_stem") or "").strip()
        if not resolved_stem and planned_output_paths:
            resolved_stem = re.sub(r"_Part\d+$", "", Path(planned_output_paths[0]).stem)
        if not resolved_stem:
            resolved_stem = Path(answers["input_path"]).stem
        split_answers["output_name_stem"] = resolved_stem
        stages.append(("Split the reversed result",
                       [str(part) for part in build_ffmpeg_command(split_answers)]))
    return stages


def export_bounded_reverse_plan(answers: dict[str, Any], destination: Path) -> Path | None:
    """Write the staged plan as a runnable PowerShell script.

    A multi-stage job has no single "final command", so exporting one and
    labelling it that way is what made the manual path wrong. The script stops
    on the first failure and names the scratch directory the user has to remove
    afterwards, because the generated inputs are deliberately preserved.
    """
    stem = re.sub(r"_Part\d+$", "", Path(destination).stem)
    workspace = Path(destination).parent / f"{stem}_plan"
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        stages = bounded_reverse_plan(answers, workspace)
    except Exception as exc:  # a plan we cannot describe must not break the run
        log_warn(f"Could not export the staged reverse plan: {exc}")
        return None
    if not stages:
        return None
    script = Path(destination).parent / f"{stem}.plan.ps1"
    lines = [
        "# FFmWiz staged reverse plan.",
        "# This job runs as several FFmpeg commands, in this order. The single",
        "# command shown in the summary is a readable reference only: running it",
        "# would buffer the whole timeline, which is what the staged plan avoids.",
        "$ErrorActionPreference = 'Stop'",
        "",
    ]
    for label, cmd in stages:
        lines.append(f"# {label}")
        if cmd and cmd[0].startswith("<"):
            lines.append(f"#   {cmd[0]} -- FFmWiz writes this list at run time")
        else:
            # `&` is required: command_to_powershell quotes the executable for
            # DISPLAY, and PowerShell parses a bare quoted string followed by
            # arguments as an expression, not a command.
            lines.append("& " + command_to_powershell(cmd))
            lines.append("if ($LASTEXITCODE -ne 0) { throw '"
                         + label.replace("'", "''") + " failed' }")
        lines.append("")
    lines.append(f"# Scratch files live in: {workspace}")
    lines.append("# Remove that directory once the outputs are correct.")
    script.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    log_info(f"Exported staged reverse plan with {len(stages)} stage(s) to {script}")
    return script


def run_bounded_reverse_pipeline(answers: dict[str, Any]) -> tuple[int, float]:
    """Reverse without ever handing the filter a whole timeline (F10).

    `reverse` cannot emit a frame until it has buffered every decoded frame of
    its input, so the only safe shape is to give it one bounded segment at a
    time -- never a joined program, never a to-be-split one. Three stages, each
    skipped when it does not apply:

      1. JOIN the inputs forward into a leased intermediate. The segmented
         executor cannot be aimed at a join directly: it rebuilds each segment
         with the SINGLE-input builder, which reverses input 1 alone (R01).
      2. REVERSE that single input in segments sized against the frame budget,
         applying the cuts and speed along with it.
      3. SPLIT the reversed result. Split points are chosen on the final
         processed timeline, which is exactly what stage 2 produced, so the
         parts fall where the summary said they would.

    Measured: an hour of joined 1080p30 is roughly 336 GiB of decoded frames in
    one pass, and ten minutes of single-input 1080p30 with a split is about
    56 GiB -- neither can finish. Each intermediate costs one extra encode and
    is written at a visually lossless quality, then removed with the rest of the
    job's temporary files.
    """
    started_at = time.perf_counter()
    extension = str(answers.get("output_ext") or "mkv").lstrip(".")
    workspace = artifact_lease(answers).register(
        Path(tempfile.mkdtemp(prefix="ffmwiz_reverse_pipeline_")))
    split_points = list(answers.get("separator_points") or [])
    # Captured BEFORE any stage rewrites them: these are the paths the summary
    # showed and the user confirmed.
    planned_output_paths = list(answers.get("split_output_paths") or [])
    stage_source = answers

    # Geometry is owned by the FIRST stage that writes a picture: the forward
    # join when there is one, otherwise the reverse encode. Later stages read
    # an already-cropped, already-resized intermediate, so re-applying would
    # crop the crop (D01/D02).
    has_join = bool(answers.get("join_input_items"))
    forward_owns = GEOMETRY_TRANSFORMATIONS if has_join else ()
    reverse_owns = ("cuts", "audio_cuts", "video_speed", "audio_speed",
                    "video_reverse", "audio_reverse", "loudnorm")
    if not has_join:
        reverse_owns = reverse_owns + GEOMETRY_TRANSFORMATIONS
    # Before the join, not after: a plan that cannot be executed correctly must
    # not spend a full forward encode first.
    validate_stage_plan([("forward join", forward_owns),
                         ("reverse", reverse_owns),
                         ("split", ("split",) if split_points else ())],
                        answers)

    if has_join:
        items = join_items_from_answers(answers)
        if not items:
            return 1, time.perf_counter() - started_at
        joined = workspace / f"joined_forward.{INTERMEDIATE_CONTAINER_EXT}"
        # Owns the GEOMETRY and nothing else. Clearing only the video edits
        # left an independent audio speed to be applied here AND by the reverse
        # stage AND by the split (B01); leaving geometry unowned did the same
        # to crop, fps and resize (D01/D02).
        forward = intermediate_profile(stage_answers(answers, owns=forward_owns))
        forward["output_path"] = joined
        forward_cmd = wizard.build_join_encode_command(forward, items, joined)
        appio.note("Reverse across a join: joining first, then reversing in bounded "
                   "segments so the whole joined timeline is never held in RAM.")
        log_info(f"Bounded reverse pipeline: forward join -> {joined}")
        code, _elapsed = run_ffmpeg_with_progress(
            forward_cmd,
            total_duration=sum(float(item.get("duration") or 0.0) for item in items) or None,
            label="Joining before reverse")
        if code != 0 or not joined.exists():
            return (code or 1), time.perf_counter() - started_at
        stage_source = _single_input_answers(answers, joined)

    # Owns everything except the split -- and the geometry too when no forward
    # join already applied it.
    reverse_answers = stage_answers(stage_source, owns=reverse_owns)
    reverse_answers.pop("join_input_items", None)
    if split_points:
        # Split AFTER the reverse: reversing each part separately would return
        # the parts in their original order, and reversing the whole thing at
        # once is the unbounded plan this exists to avoid.
        pass
        reversed_whole = workspace / f"reversed_whole.{INTERMEDIATE_CONTAINER_EXT}"
        reverse_answers = intermediate_profile(reverse_answers)
        reverse_answers["output_path"] = reversed_whole
        reverse_answers["output_location"] = workspace
        reverse_answers["output_name_stem"] = reversed_whole.stem
        reverse_answers["output_collision_suffix"] = ""
        appio.note("Reverse with Split: reversing the whole timeline in bounded "
                   "segments first, then cutting the parts out of the result.")
    else:
        reverse_answers["output_path"] = answers["output_path"]
    reverse_answers["cmd"] = build_ffmpeg_command(dict(reverse_answers))
    code, _elapsed = run_segmented_reverse_main_encode(reverse_answers)
    if code != 0 or not split_points:
        return code, time.perf_counter() - started_at

    reversed_whole = Path(reverse_answers["output_path"])
    if not reversed_whole.exists():
        return 1, time.perf_counter() - started_at
    # Owns the split alone. Stage 2 already applied every other edit; leaving
    # any of them here would apply it a second (or third) time.
    split_answers = stage_answers(
        _single_input_answers(answers, reversed_whole), owns=("split",))
    split_answers.pop("split_output_paths", None)
    split_answers.pop("split_part_intervals", None)
    split_answers["separator_points"] = split_points
    # Keep the part filenames the summary already showed the user. Taking the
    # stem from input_path instead promised CustomMovie_Part01.mkv and wrote
    # a_Part01.mkv, because by this point input_path is the pipeline's own
    # scratch file (B14). Prefer the user's stem, then the stem the first build
    # already resolved, and only then the source name.
    resolved_stem = str(answers.get("output_name_stem") or "").strip()
    if not resolved_stem:
        planned = [Path(part) for part in (planned_output_paths or [])]
        if planned:
            resolved_stem = re.sub(r"_Part\d+$", "", planned[0].stem)
    if not resolved_stem:
        resolved_stem = Path(answers["input_path"]).stem
    split_answers["output_name_stem"] = resolved_stem
    split_cmd = build_ffmpeg_command(split_answers)
    log_info(f"Bounded reverse pipeline: splitting {reversed_whole} into "
             f"{len(split_answers.get('split_output_paths') or [])} part(s)")
    code, _elapsed = run_ffmpeg_with_progress(
        split_cmd,
        total_duration=services.stream_duration_seconds({}, split_answers.get("format")),
        label="Splitting the reversed result")
    answers["split_output_paths"] = split_answers.get("split_output_paths")
    return code, time.perf_counter() - started_at


def run_segmented_reverse_main_encode(answers: dict[str, Any]) -> tuple[int, float]:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    if duration <= 0:
        return run_ffmpeg_with_progress(
            answers["cmd"],
            total_duration=None,
            label="FFmpeg encode",
        )
    original_keep_ranges = normalize_cut_ranges(list(answers.get("cut_keep_ranges") or []), duration)
    segment_seconds = reverse_segment_seconds(answers)
    chunks = split_ranges_for_reverse_segments(original_keep_ranges, duration,
                                               segment_seconds)
    if not chunks:
        return 1, 0.0
    speed = encode_video_speed_factor(answers)
    output_path = Path(answers["output_path"])
    appio.note(
        f"Reverse encode uses {len(chunks)} segment(s) of up to {segment_seconds:.0f}s "
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
        # Concatenating reversed(segments) reverses the order of their AUDIO
        # blocks too. That is right when the audio follows the video reverse and
        # wrong when the user declined it: measured on 440 Hz for 0-2 s and
        # 880 Hz for 2-4 s with reverse_audio=False, the output played 880 Hz at
        # 0.4 s and 440 Hz at 3.2 s -- chunk-reordered without a single
        # `areverse` in any command (B02).
        #
        # So the two timelines are concatenated separately: video from the
        # reversed order, audio from the forward one, then muxed. Both passes
        # are stream copies, so this costs no extra encode.
        audio_follows_reverse = encode_audio_reverse_enabled(answers)
        has_audio = bool(answers.get("audio_streams")) and bool(answers.get("audio_tracks", True))
        concat_list = tmpdir / "concat.txt"
        if has_audio and not audio_follows_reverse:
            video_ext = output_path.suffix.lstrip(".") or segment_ext
            reversed_video = tmpdir / f"video_reversed.{video_ext}"
            forward_audio = tmpdir / f"audio_forward.{video_ext}"
            write_concat_list(list(reversed(segment_paths)), concat_list)
            video_cmd = build_concat_copy_command(answers["ffmpeg"], concat_list, reversed_video)
            video_cmd[video_cmd.index(str(reversed_video)):] = ["-an", str(reversed_video)]
            audio_list = tmpdir / "concat_audio.txt"
            write_concat_list(list(segment_paths), audio_list)
            audio_cmd = build_concat_copy_command(answers["ffmpeg"], audio_list, forward_audio)
            audio_cmd[audio_cmd.index(str(forward_audio)):] = ["-vn", str(forward_audio)]
            for label, cmd in (("video (reversed order)", video_cmd),
                               ("audio (source order)", audio_cmd)):
                log_info(f"Reverse encode concat {label}: " + command_to_powershell(cmd))
                rc, _ = run_ffmpeg_with_progress(
                    cmd, total_duration=(total_keep_duration(chunks) / speed if chunks else None),
                    label=f"Reverse encode concat {label}")
                if rc != 0:
                    return rc, time.perf_counter() - started_at
            appio.note("Video reverse only: the audio keeps its own order and is "
                       "muxed back onto the reversed picture.")
            mux_inputs = ["-i", str(reversed_video), "-i", str(forward_audio)]
            mux_maps, mux_dispositions, mux_warnings = reverse_mux_stream_policy(answers)
            mux_maps = mux_maps + mux_dispositions
            for warning in mux_warnings:
                appio.note(warning)
                log_warn(warning)
            metadata_input = 2
        else:
            write_concat_list(list(reversed(segment_paths)), concat_list)
            mux_inputs = ["-f", "concat", "-safe", "0", "-i", str(concat_list)]
            mux_maps = ["-map", "0"]
            metadata_input = 1
        # The per-segment encodes carry chapter metadata, but this final
        # concat-copy did not restore any of it, so a reversed chaptered source
        # came out with ZERO chapters (R05). Attach the remapped chapters here,
        # where the output timeline finally exists. remap_chapters_for_encode
        # applies the reverse flip itself.
        chapter_plan = remap_chapters_for_encode(answers, speed_factor=speed)
        concat_cmd = [answers["ffmpeg"], "-y" if OVERWRITE_OUTPUT else "-n",
                      "-hide_banner", *mux_inputs]
        chapter_args: list[str] = []
        if chapter_plan.get("mode") == "metadata" and chapter_plan.get("chapters"):
            chapter_metadata = write_encode_chapter_metadata(chapter_plan, tmpdir, "_reverse")
            # Appended LAST so it is always the highest input index: the
            # video-only branch already occupies 0 and 1, and inserting it
            # earlier would renumber the audio input the maps depend on.
            concat_cmd.extend(["-i", str(chapter_metadata)])
            chapter_args = copy_cut_chapter_map_args(
                chapter_plan, metadata_input_index=metadata_input)
            log_info(f"Reverse encode: restored {len(chapter_plan['chapters'])} chapter(s) "
                     "onto the reversed timeline")
        else:
            # Be explicit rather than leaving a silent gap: a source WITH
            # chapters whose plan is not usable loses them here.
            chapter_args = copy_cut_chapter_map_args(chapter_plan)
        concat_cmd.extend([*mux_maps, "-c", "copy",
                           "-avoid_negative_ts", "make_zero", *chapter_args])
        if output_path.suffix.lstrip(".").lower() in MP4_LIKE_EXTS and MOVFLAGS:
            concat_cmd.extend(["-movflags", MOVFLAGS])
        concat_cmd.append(str(output_path))
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
    'reverse_segment_seconds',
    'run_bounded_reverse_pipeline',
    'bounded_reverse_plan',
    'export_bounded_reverse_plan',
    'intermediate_profile',
    'stage_answers',
    'validate_stage_plan',
    'reverse_mux_stream_policy',
    'requested_transformations',
    'GEOMETRY_TRANSFORMATIONS',
    'STAGE_TRANSFORMATIONS',
    'run_segmented_reverse_main_encode',
    'execute_encode_plan',
    'run_separator_main_encode',
    'FFMPEG_REFERENCE_SECTIONS',
]
