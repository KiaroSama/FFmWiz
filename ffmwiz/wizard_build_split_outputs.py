"""Writing the per-part outputs of a Split job.

Split out of ffmwiz/wizard_build.py for file size. A leaf of that module's
call graph: `build_ffmpeg_command` calls it and it calls nothing back, which
matters here because `build_ffmpeg_command` and `build_quick_output_stages` are
mutually recursive and therefore could not be separated from each other.
"""
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
from ffmwiz.support.L01_subtitles import *  # noqa: F401,F403
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
from ffmwiz import metadata  # noqa: F401
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.trackmanager import *  # noqa: F401,F403
from ffmwiz import wizard_base  # noqa: E402,F401
from ffmwiz import wizard_build_b  # noqa: E402,F401
from ffmwiz import wizard_build_filters  # noqa: E402,F401  (defines build_cpu_video_filter)
from ffmwiz import wizard_build_subtitles  # noqa: E402,F401


def append_single_input_split_outputs(
    cmd: list[str],
    answers: dict[str, Any],
    output_path: Path,
    video_encoder: str,
    tag: str | None,
    profile: str | None,
    audio_indices: list[int],
    audio_transform_active: bool,
    multi_cut: bool,
    audio_for_cut: int | None,
    retimed_subtitles: list[dict[str, Any]] | None = None,
) -> bool:
    source_duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    final_duration = final_processed_duration_for_splits(answers, source_duration)
    split_points = normalize_separator_points(answers.get("separator_points"), final_duration)
    if not split_points:
        return False
    filters: list[str] = []
    audio_labels: list[str] = []
    if multi_cut:
        filters.extend(wizard_build_b.build_cut_filter_complex(answers, list(answers.get("cut_keep_ranges") or []), audio_for_cut).split(";"))
        video_label = "v"
        if audio_for_cut is not None:
            audio_labels.append("a")
    else:
        video_filter = wizard_build_filters.build_cpu_video_filter(answers) or "null"
        filters.append(f"[0:v:0]{video_filter}[vbase]")
        video_label = "vbase"
        if audio_indices:
            if audio_transform_active:
                audio_fc, labels = build_audio_transform_filter_complex(answers, audio_indices)
                filters.extend(audio_fc.split(";"))
                audio_labels.extend(labels)
            else:
                for pos, audio_index in enumerate(audio_indices):
                    label = f"abase{pos}"
                    filters.append(f"[0:a:{audio_index}]asetpts=PTS-STARTPTS[{label}]")
                    audio_labels.append(label)
    filters.append(f"[{video_label}]setpts=PTS-STARTPTS[vfinal]")
    final_audio_labels: list[str] = []
    for pos, label in enumerate(audio_labels):
        final_label = f"afinal{pos}"
        filters.append(f"[{label}]asetpts=PTS-STARTPTS[{final_label}]")
        final_audio_labels.append(final_label)
    video_outputs, audio_outputs_by_part, split_intervals = append_final_split_filters(
        filters,
        "vfinal",
        final_audio_labels,
        split_points,
        final_duration,
        "s",
        float(answers.get("fps") or services.get_video_fps(answers) or 0.0),
    )
    output_paths = split_part_output_paths(output_path, len(video_outputs), [Path(answers["input_path"])])
    answers["split_output_paths"] = output_paths
    answers["split_part_intervals"] = split_intervals
    answers["output_path"] = output_paths[0]

    # Prepare per-part chapter remapping if timeline is modified and chapters exist.
    split_chapter_plans: list[dict[str, Any]] = []
    split_chapter_metadata_paths: list[Path] = []
    if source_chapters_keep_enabled(answers):
        speed_factor = encode_video_speed_factor(answers) if video_speed_transform_enabled(answers) else 1.0
        for part_idx, interval in enumerate(split_intervals):
            plan = remap_chapters_for_encode(answers, speed_factor=speed_factor, part_interval=interval)
            split_chapter_plans.append(plan)
            if plan.get("mode") == "metadata" and plan.get("chapters"):
                chapter_temp_dir = answers.get("_chapter_metadata_temp_dir")
                if not chapter_temp_dir:
                    # Leased, not just keyed: every Split part is rebuilt from a
                    # SHALLOW COPY of answers, so a key written here dies with
                    # the copy and the directory outlived the run (R06).
                    chapter_temp_dir = str(artifact_lease(answers).register(
                        Path(tempfile.mkdtemp(prefix="ffmwiz_split_chapters_"))))
                    answers["_chapter_metadata_temp_dir"] = chapter_temp_dir
                metadata_path = write_encode_chapter_metadata(plan, Path(chapter_temp_dir), suffix=f"_part{part_idx + 1:02d}")
                split_chapter_metadata_paths.append(metadata_path)
            else:
                split_chapter_metadata_paths.append(None)

    # Add chapter metadata inputs (input index 1, 2, ... for each part that has chapters).
    # Not a literal 1: the caller adds the retimed-subtitle inputs before this
    # runs, so a hardcoded base pointed -map_chapters at an .srt and gave every
    # part the PREVIOUS part's chapters. Count what is already there instead.
    chapter_input_base = sum(1 for arg in cmd if arg == "-i")
    metadata_input_count = 0
    part_chapter_input_indices: list[int | None] = []
    for metadata_path in split_chapter_metadata_paths:
        if metadata_path is not None:
            cmd.extend(["-i", str(metadata_path)])
            part_chapter_input_indices.append(chapter_input_base + metadata_input_count)
            metadata_input_count += 1
        else:
            part_chapter_input_indices.append(None)

    # Slice the subtitles onto each part's own clock and map them. Without this
    # the Split path emitted -sn: a cut/speed Split built and announced a
    # retimed track that no output ever carried.
    split_subtitles = wizard_build_b.build_split_subtitle_inputs(
        answers, split_intervals, retimed_subtitles)
    subtitle_input_indices: list[list[int]] = []
    next_input_index = chapter_input_base + metadata_input_count
    for part_tracks in split_subtitles:
        indices: list[int] = []
        for track in part_tracks:
            cmd.extend(["-i", str(track["path"])])
            indices.append(next_input_index)
            next_input_index += 1
        subtitle_input_indices.append(indices)

    cmd.extend(["-filter_complex", ";".join(filters)])
    for part_idx, part_output in enumerate(output_paths):
        cmd.extend(["-map", f"[{video_outputs[part_idx]}]"])
        for audio_label in audio_outputs_by_part[part_idx]:
            cmd.extend(["-map", f"[{audio_label}]"])
        part_subtitles = (split_subtitles[part_idx]
                          if part_idx < len(split_subtitles) else [])
        for subtitle_input in (subtitle_input_indices[part_idx]
                               if part_idx < len(subtitle_input_indices) else []):
            cmd.extend(["-map", f"{subtitle_input}:s:0"])
        # Data streams carry packets, so they are mapped BEFORE the
        # attachment, keeping the rule uniform across this builder. It cannot
        # be reached today -- Matroska is the only container FFmWiz maps an
        # attachment into and it refuses a data stream outright ("Only audio,
        # video, and subtitles are supported for Matroska") -- so this is
        # ordering hygiene, not a fix for an observed failure.
        data_mapped = append_source_data_maps(cmd, answers)
        attachments_mapped = append_embedded_attachment_maps(cmd, answers)

        # Per-part chapter handling for splits.
        if not output_has_video(answers):
            pass
        else:
            cmd.extend(["-map_metadata", "0" if source_metadata_keep_enabled(answers) else "-1"])
            if not source_chapters_keep_enabled(answers):
                cmd.extend(["-map_chapters", "-1"])
            elif part_chapter_input_indices and part_chapter_input_indices[part_idx] is not None:
                cmd.extend(["-map_chapters", str(part_chapter_input_indices[part_idx])])
                log_info(f"Chapters: Part {part_idx + 1} uses remapped metadata input {part_chapter_input_indices[part_idx]}")
            else:
                cmd.extend(["-map_chapters", "-1"])

        append_negative_stream_options(
            cmd, answers, True,
            list(range(len(part_subtitles))) if part_subtitles else [],
            data_mapped)
        wizard_base.append_video_encode_options(cmd, answers, video_encoder, tag, profile)
        append_audio_encode_options(cmd, answers, bool(audio_outputs_by_part[part_idx]))
        append_clear_reencoded_stream_stat_metadata(
            cmd,
            answers,
            video_output_count=1 if video_encoder != "copy" else 0,
            audio_output_count=len(audio_outputs_by_part[part_idx]) if audio_outputs_by_part[part_idx] else 0,
            subtitle_output_count=len(part_subtitles),
        )
        if part_subtitles:
            # Sliced tracks are freshly written SRT whatever the source was, so
            # ask the container about subrip -- the same check the non-split
            # path makes before mapping anything.
            subtitle_args, subtitle_problems = subtitle_codec_args_for_container(
                answers["output_ext"], ["subrip"] * len(part_subtitles))
            for problem in subtitle_problems:
                log_warn(f"Subtitle/container: {problem}")
            if subtitle_args:
                cmd.extend(subtitle_args)
            wizard_build_subtitles.append_subtitle_track_metadata(cmd, part_subtitles)
        if attachments_mapped:
            append_embedded_attachment_codec_options(cmd, answers)
        if data_mapped:
            append_source_data_codec_options(cmd, answers)
        append_container_options(cmd, answers["output_ext"])
        # Same position and same reason as the single-output path below: last,
        # immediately before this part's output, so the user's own options can
        # override what the wizard chose. This branch RETURNS before that code
        # is reached, so without this line a Split silently dropped the raw
        # options from every part it wrote.
        cmd.extend(answers.get("raw_ffmpeg_args") or [])
        cmd.append(str(part_output))
    log_info(
        "Split final output into parts: "
        + ", ".join(
            f"Part {idx + 1:02d} {seconds_to_ffmpeg_time(start)}->{seconds_to_ffmpeg_time(end)}"
            for idx, (start, end) in enumerate(split_intervals)
        )
    )
    return True


__all__ = [
    'append_single_input_split_outputs',
]
