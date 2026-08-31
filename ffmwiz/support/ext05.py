"""FFmWiz extracted helper tier ext5 (post-services layer).

Imports core, support, and top-level ffmwiz modules; acyclic.
"""
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
from ffmwiz.appio import *  # noqa: F401,F403
from ffmwiz import appio  # noqa: F401
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz import runtime  # noqa: F401
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.support.ext04 import *  # noqa: F401,F403


def bitrate_kbps(
    stream: dict[str, Any] | None,
    fmt: dict[str, Any] | None = None,
    sibling_streams: list[dict[str, Any]] | None = None,
) -> int | None:
    sources = (("stream", stream),) if stream is not None else (("format", fmt),)
    for source_name, source in sources:
        if not source:
            continue
        bit_rate = source.get("bit_rate")
        if bit_rate:
            try:
                value = max(1, round(int(bit_rate) / 1000))
                if source_name == "format" or stream_bitrate_plausible(value, stream, fmt):
                    return value
            except ValueError:
                pass
        tags = source.get("tags") if isinstance(source, dict) else None
        if isinstance(tags, dict):
            normalized = {str(key).upper(): value for key, value in tags.items()}
            for key in ("BPS", "BPS-ENG"):
                tagged_bps = normalized.get(key)
                if tagged_bps:
                    try:
                        value = max(1, round(int(float(tagged_bps)) / 1000))
                        if source_name == "format" or (
                            stream_bitrate_plausible(value, stream, fmt)
                            and stream is not None
                            and stream_statistics_tags_trustworthy(stream, fmt, sibling_streams)
                        ):
                            return value
                    except (TypeError, ValueError):
                        pass
    return None


def scan_folder_media_files(
    base_answers: dict[str, Any],
    folder_path: Path,
    exclude_folder: Path | None = None,
) -> list[dict[str, Any]]:
    files = [
        path for path in folder_path.rglob("*")
        if path.is_file() and not (exclude_folder and path_is_inside(path, exclude_folder))
    ]
    candidates = sorted(
        (path for path in files if is_folder_media_candidate(path) and not looks_like_generated_output_file(path)),
        key=lambda path: str(path.relative_to(folder_path)).lower(),
    )
    skipped_non_media = sum(1 for path in files if not is_folder_media_candidate(path))
    skipped_generated = sum(1 for path in files if is_folder_media_candidate(path) and looks_like_generated_output_file(path))
    log_info(
        f"Folder Encode scan: folder={folder_path}; media candidates={len(candidates)}; "
        f"non-media files ignored={skipped_non_media}; generated outputs ignored={skipped_generated}"
    )
    def probe_one(path: Path) -> dict[str, Any] | None:
        probe_answers = dict(base_answers)
        probe_answers["detect_duplicate_audio"] = False
        try:
            load_input_metadata(probe_answers, path)
        except Exception:
            log_exception(f"Folder Encode skipped file after probe failure: {path}")
            appio.note(f"Skipped unreadable media file: {path.name}. See log file: {_log_file_text()}")
            return None
        return {
            "path": path,
            "relative_path": path.relative_to(folder_path),
            "answers": {key: probe_answers.get(key) for key in FOLDER_MEDIA_METADATA_KEYS if key in probe_answers},
            "has_video": bool(probe_answers.get("video_streams")),
            "has_audio": bool(probe_answers.get("audio_streams")),
        }

    items: list[dict[str, Any]] = []
    max_workers = min(FOLDER_PROBE_WORKERS, len(candidates)) if candidates else 1
    if max_workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for item in executor.map(probe_one, candidates):
                if item is not None:
                    items.append(item)
    else:
        for path in candidates:
            item = probe_one(path)
            if item is not None:
                items.append(item)
    return items


def _load_input_folder_join(answers: dict[str, Any], folder: Path) -> bool:
    """Treat `folder` as the whole join set: the first video (by name) becomes the
    main input and the rest become join inputs. Returns True on success."""
    videos = [v for v in join_video_files_in_folder(folder) if not looks_like_generated_output_file(v)]
    if not videos:
        appio.error("No video files were found in that folder.")
        return False
    first = videos[0]
    try:
        load_input_metadata(answers, first)
    except FFprobeError as exc:
        appio.error(str(exc))
        return False
    except Exception:
        log_exception(f"ffprobe metadata load failed for folder input: {first}")
        appio.error(f"ffprobe could not read the first folder video. See log file: {_log_file_text()}")
        return False
    items: list[dict[str, Any]] = []
    for path in videos[1:]:
        try:
            items.append(services.join_load_media_item(answers, path))
        except Exception as exc:  # noqa: BLE001
            log_exception(f"Join folder probe skipped: {path}")
            appio.note(f"Skipped (not a usable video): {path.name} ({exc})")
    answers["join_input_items"] = items
    answers["_join_preloaded_from_folder"] = True
    appio.note(f"Loaded {len(items) + 1} video(s) from folder: {folder}")
    print_join_order_list([answers["input_path"], *[it["path"] for it in items]])
    return True


def build_audio_transform_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    answers["output_ext"] = resolve_audio_tool_output_ext(answers)
    suffix_parts: list[str] = []
    if answers.get("audio_cut_keep_ranges"):
        suffix_parts.append("AudioCut")
    if answers.get("audio_speed_enabled"):
        suffix_parts.append(
            speed_suffix(
                float(answers.get("audio_speed_factor", DEFAULT_SPEED_FACTOR)),
                bool(answers.get("reverse_audio")),
            ).lstrip("_")
        )
    answers["output_collision_suffix"] = "_" + "_".join(suffix_parts or ["AudioTransform"])
    output_path = services.build_output_path(answers)
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    audio_index = int(answers.get("audio_index", 0))
    filter_complex, labels = build_audio_transform_filter_complex(answers, [audio_index])
    if not labels:
        raise ValueError("No audio stream was selected for transformation.")
    cmd: list[str] = [
        ffmpeg,
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-i",
        str(input_path),
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{labels[0]}]",
        *audio_tool_picture_args(answers, answers["output_ext"]),
        "-sn",
        "-dn",
    ]
    cmd.extend(audio_tool_encode_options(
        answers["output_ext"],
        bitrate_kbps=resolve_audio_tool_bitrate_kbps(answers),
        sample_rate=resolve_audio_sample_rate(answers),
        channels=resolve_audio_tool_channels(answers),
    ))
    cmd.append(str(output_path))
    log_info(
        f"Audio transform command built: audio_index={audio_index}; "
        f"cuts={answers.get('audio_cut_keep_ranges')}; "
        f"speed={answers.get('audio_speed_factor')}; reverse={answers.get('reverse_audio')}; "
        f"output={output_path}"
    )
    return cmd


def step_video_speed_start_now(answers: dict[str, Any]) -> None:
    if answers.get("_speed_reverse_noop"):
        return
    cmd = build_video_speed_reverse_command(answers)
    answers["cmd"] = cmd
    print_transform_summary(answers, cmd, "Video Speed / Reverse")
    answers["start_now"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def step_audio_speed_start_now(answers: dict[str, Any]) -> None:
    if answers.get("_speed_reverse_noop"):
        return
    cmd = build_audio_speed_reverse_command(answers)
    answers["cmd"] = cmd
    print_transform_summary(answers, cmd, "Audio Speed / Reverse")
    answers["start_now"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def analyze_copy_cut_chapter_plan(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
) -> dict[str, Any]:
    chapters = load_copy_cut_chapters(answers)
    if not chapters:
        return {"mode": "copy", "chapters": [], "overlap_count": 0}

    duration = copy_cut_source_duration(answers, keep_ranges, chapters)
    normalized_keep = normalize_cut_ranges(keep_ranges, duration)
    removed_ranges = copy_cut_removed_ranges(normalized_keep, duration)
    if not removed_ranges:
        return {"mode": "copy", "chapters": [], "overlap_count": 0}

    overlap_count = 0
    remapped_chapters: list[dict[str, Any]] = []
    for chapter in chapters:
        start = copy_cut_chapter_seconds(chapter, "start")
        end = copy_cut_chapter_seconds(chapter, "end")
        if start is None or end is None or end <= start:
            log_debug(f"Copy Cut skipped invalid chapter while rebuilding: {chapter!r}")
            continue
        if copy_cut_chapter_overlaps_removed(start, end, removed_ranges):
            overlap_count += 1
            continue
        remapped = copy_cut_remap_chapter(start, end, normalized_keep)
        if remapped is None:
            continue
        new_start, new_end = remapped
        remapped_chapters.append({
            "start": new_start,
            "end": new_end,
            "metadata": dict(chapter.get("tags") or {}),
        })

    # NOT `if overlap_count == 0: return copy`. "No chapter was cut through" is
    # not "the clock did not move": this point is only reached once
    # `removed_ranges` is non-empty, so every chapter after a removed span sits
    # at the wrong time. `-map_chapters 0` cannot express that -- and across the
    # multi-range concat demuxer it does not even carry the chapters: measured
    # on a 12 s source with chapters A 0-2 and B 8-10 cut to [(0,4),(8,12)],
    # neither chapter touches the removed 4-8 s, and the output came back with
    # ZERO chapters. The remap below already had the right answer (A 0-2,
    # B 4-6); it was simply thrown away.
    if not remapped_chapters:
        log_info(f"Copy Cut chapter plan: dropping all chapters; overlap_count={overlap_count}")
        return {"mode": "drop", "chapters": [], "overlap_count": overlap_count}
    log_info(
        "Copy Cut chapter plan: rebuilding chapters; "
        f"kept={len(remapped_chapters)} dropped={overlap_count}"
    )
    return {"mode": "metadata", "chapters": remapped_chapters, "overlap_count": overlap_count}


def print_extract_files_listing(entries: list[dict[str, Any]]) -> None:
    """List each file and its extractable streams (per file)."""
    for entry in entries:
        print()
        print(
            paint(entry["path"].name, Color.BOLD + Color.LIGHT_BLUE)
            + paint(f"  ({len(entry['candidates'])} extractable stream(s))", Color.GRAY)
        )
        for stream in entry["candidates"]:
            print("  " + extract_describe_stream(stream, entry["format"]))


__all__ = [
    'bitrate_kbps',
    'scan_folder_media_files',
    '_load_input_folder_join',
    'build_audio_transform_command',
    'step_video_speed_start_now',
    'step_audio_speed_start_now',
    'analyze_copy_cut_chapter_plan',
    'print_extract_files_listing',
]
