"""FFmWiz extracted helper tier ext4 (post-services layer).

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
from ffmwiz.support.L01_subtitles import TimelineMap  # noqa: F401
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


def remap_chapters_for_encode(
    answers: dict[str, Any],
    speed_factor: float = 1.0,
    part_interval: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Remap source chapters to the processed output timeline.

    Returns a plan dict compatible with `copy_cut_chapter_map_args`:
      mode="copy"     – use -map_chapters 0 (no timeline change, chapters valid)
      mode="drop"     – use -map_chapters -1 (no chapters survive)
      mode="metadata" – use a generated FFmetadata file
      mode="disable"  – use -map_chapters -1 (remapping unavailable)

    Parameters
    ----------
    answers : dict
        The standard wizard answers dict containing probe data.
    speed_factor : float
        The video speed multiplier (>1 = faster, <1 = slower).
    part_interval : tuple[float, float] | None
        If the output is split into parts, the (start, end) interval of this
        part in the *processed* timeline (after cuts + speed). Chapters are
        clipped to this interval and timestamps offset to start at zero.
    """
    probe = answers.get("probe") if isinstance(answers.get("probe"), dict) else {}
    chapters = (probe or {}).get("chapters") or []
    chapters = [ch for ch in chapters if isinstance(ch, dict)]
    if not chapters:
        log_info("Chapters: source contains no chapters")
        return {"mode": "copy", "chapters": [], "overlap_count": 0}

    if not timeline_is_modified(answers):
        log_info("Chapters: preserved from source")
        return {"mode": "copy", "chapters": [], "overlap_count": 0}

    # Determine keep ranges; default to full source duration.
    source_duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    for ch in chapters:
        end = copy_cut_chapter_seconds(ch, "end")
        if end is not None:
            source_duration = max(source_duration, end)
    source_duration = max(0.0, source_duration)

    # One shared transform for cuts + reverse + speed. This loop used to carry
    # its own copy of that arithmetic, and reverse was missing from it entirely
    # -- every chapter stayed at its original time in a reversed clip (R05).
    # TimelineMap is the same object the subtitle cues are retimed with, so the
    # two clocks cannot drift apart again.
    timeline = TimelineMap(
        keep_ranges=answers.get("cut_keep_ranges"),
        source_duration=source_duration,
        speed=speed_factor,
        reverse=bool(answers.get("reverse_video")),
    )
    remapped: list[dict[str, Any]] = []
    for ch in chapters:
        start = copy_cut_chapter_seconds(ch, "start")
        end = copy_cut_chapter_seconds(ch, "end")
        if start is None or end is None or end <= start:
            continue
        pieces = timeline.map_interval(start, end)
        if not pieces:
            continue
        # A chapter is one contiguous label: when a cut removes a hole inside
        # it, keep the span it still covers instead of splitting it in two.
        new_start, new_end = pieces[0][0], pieces[-1][1]
        if new_end <= new_start + 1e-6:
            continue
        remapped.append({
            "start": max(0.0, new_start),
            "end": max(0.0, new_end),
            "metadata": dict(ch.get("tags") or {}),
        })
    remapped.sort(key=lambda item: item["start"])

    # Clip to part interval if splitting.
    if part_interval is not None:
        part_start, part_end = part_interval
        part_chapters: list[dict[str, Any]] = []
        for ch in remapped:
            clip_start = max(ch["start"], part_start)
            clip_end = min(ch["end"], part_end)
            if clip_end > clip_start + 1e-6:
                part_chapters.append({
                    "start": clip_start - part_start,
                    "end": clip_end - part_start,
                    "metadata": dict(ch.get("metadata") or {}),
                })
        remapped = part_chapters

    if not remapped:
        log_info("Chapters: disabled because timeline changed and all chapters were removed")
        return {"mode": "drop", "chapters": [], "overlap_count": 0}

    log_info(f"Chapters: remapped to processed timeline ({len(remapped)} chapter(s) retained)")
    return {"mode": "metadata", "chapters": remapped, "overlap_count": 0}


def source_extra_policy_applicable(answers: dict[str, Any]) -> bool:
    return output_has_video(answers) and bool(services.source_extra_preservation_features(answers))


def preview_console_colors() -> None:
    """Print a small ANSI color preview for users editing FFmWiz colors."""
    print(paint("FFmWiz color preview", Color.BOLD + Color.LIGHT_BLUE))
    print()
    print("Base colors:")
    for name in (
        "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE",
        "GRAY", "ORANGE", "LIGHT_BLUE", "LIGHT_YELLOW", "HINT_YELLOW",
        "AQUA", "PINK", "LIME", "KEEP_VALUE", "RES_NUMBERS",
        "RES_TARGET", "RES_EXACT", "AUDIO_ALL", "AUDIO_DROP_DUP",
        "AUDIO_DROP_EMPTY", "AUDIO_DROP_BOTH", "AUDIO_TRACK_NOTE",
        "FINAL_COMMAND_LABEL", "FINAL_COMMAND_TEXT", "SUGGESTION",
        "BACK_PROMPT", "EXIT_PROMPT", "NEAR_EMPTY", "ZERO_INLINE", "PROGRESS_PERCENT",
        "PROGRESS_TIME", "PROGRESS_FPS", "PROGRESS_Q", "PROGRESS_SPEED",
        "PROGRESS_SIZE", "PROGRESS_BITRATE", "PROGRESS_ELAPSED",
        "PROGRESS_ETA_LABEL", "PROGRESS_ETA_VALUE",
    ):
        code = getattr(Color, name)
        print(f"  {name:13s} {paint('Sample text', code)}  {repr(code)}")
    print()
    print("Prompt keep-current sample:")
    print("  " + keep_value_text("n=current resolution"))
    print("  " + keep_value_text("n=current FPS around 30"))
    print("  " + keep_value_text("n=keep current value around 359k"))
    print()
    sample_state = {
        "out_time_ms": str(int((19 * 60 + 1) * 1_000_000)),
        "fps": "63.70",
        "stream_0_0_q": "9.0",
        "speed": "15.9x",
        "total_size": str(int(18.8 * 1024 * 1024)),
        "bitrate": "137.8kbits/s",
        "progress": "continue",
    }
    started_at = time.perf_counter() - 62.0
    print("Progress full:")
    print("  " + _render_progress_line(sample_state, 79 * 60 + 5, started_at))
    print("Progress 100-column preview:")
    print("  " + _render_progress_line(sample_state, 79 * 60 + 5, started_at, max_width=100))


def ensure_join_volume_stats(answers: dict[str, Any]) -> None:
    """Populate audio volume (mean/max) for the Join input set so the summary
    can show loudness extremes. Runs volumedetect on audio track 0 of each
    input that has audio but no cached value, in parallel, and caches the
    result on the primary answers and on each join item so it is computed at
    most once per session. Failures are tolerated (left as unknown)."""
    items = list(answers.get("join_input_items") or [])
    if not items:
        return
    ffmpeg = str(answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg")

    # Build the list of (target_dict, input_path) needing a scan. The primary
    # input is stored on answers; extra inputs on their own item dicts.
    pending: list[tuple[dict[str, Any], Path]] = []
    if answers.get("audio_streams") and not answers.get("audio_volume_stats") and answers.get("input_path"):
        pending.append((answers, Path(answers["input_path"])))
    for item in items:
        if item.get("audio_streams") and not item.get("audio_volume_stats") and item.get("path"):
            pending.append((item, Path(item["path"])))
    if not pending:
        return

    appio.note(f"Scanning audio loudness of {len(pending)} input file(s) for the summary...")

    def _scan(target_and_path: tuple[dict[str, Any], Path]) -> tuple[dict[str, Any], dict[str, str]]:
        target, path = target_and_path
        try:
            stats = services.probe_audio_volume_stats(ffmpeg, path, 0)
        except Exception:
            log_exception(f"Join summary volume scan failed for {path}")
            stats = {}
        return target, stats

    workers = max(1, min(4, len(pending)))
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for target, stats in executor.map(_scan, pending):
                if stats:
                    target["audio_volume_stats"] = {0: stats}
    except Exception:
        log_exception("Join summary parallel volume scan failed; volume extremes may be unavailable.")


def stream_bitrate_plausible(kbps: int | None, stream: dict[str, Any] | None,
                             fmt: dict[str, Any] | None) -> bool:
    if kbps is None or kbps <= 0:
        return False
    duration = services.stream_duration_seconds(stream or {}, fmt)
    total_size = format_size_bytes_from_metadata(fmt)
    if duration and total_size:
        implied_size = kbps * 1000 * duration / 8
        if implied_size > total_size * 1.02:
            return False
    return True


def media_info_report_path(input_path: Path) -> Path:
    reports_dir = services.default_media_reports_dir()
    reports_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_output_stem(input_path.name)
    return unique_numbered_path(reports_dir / f"{safe_name}_info.txt")


def optional_reference_metrics(
    ffprobe: str,
    ffmpeg: str | None,
    input_path: Path,
    reference_path: Path | None,
    payload: dict[str, Any],
    info_path: Path,
    skipped: list[str],
) -> list[Path]:
    if not ffmpeg or reference_path is None:
        return []
    try:
        reference_payload = ffprobe_full_json(ffprobe, reference_path)
        main_video = media_info_main_video_stream(payload) or {}
        ref_video = media_info_main_video_stream(reference_payload) or {}
        warnings: list[str] = []
        if services.stream_duration_seconds({}, payload.get("format") or {}) != services.stream_duration_seconds({}, reference_payload.get("format") or {}):
            warnings.append("duration differs")
        if (main_video.get("width"), main_video.get("height")) != (ref_video.get("width"), ref_video.get("height")):
            warnings.append("resolution differs")
        if media_info_video_fps(main_video) != media_info_video_fps(ref_video):
            warnings.append("frame rate differs")
        if warnings:
            skipped.append("Reference metric warning: " + ", ".join(warnings) + ". Metrics only make sense when files are aligned.")
    except Exception as exc:
        skipped.append(f"Reference metric warning: could not inspect reference file metadata ({exc}).")
    generated: list[Path] = []
    metric_specs = [
        ("psnr", ["[0:v][1:v]psnr=stats_file={path}"], media_info_sidecar_path(info_path, "psnr", ".log")),
        ("ssim", ["[0:v][1:v]ssim=stats_file={path}"], media_info_sidecar_path(info_path, "ssim", ".log")),
    ]
    if ffmpeg_filter_available(ffmpeg, "libvmaf"):
        metric_specs.append(("vmaf", ["[0:v][1:v]libvmaf=log_fmt=json:log_path={path}"], media_info_sidecar_path(info_path, "vmaf", ".json")))
    else:
        skipped.append("VMAF skipped: this FFmpeg build does not report libvmaf support.")
    for label, filter_templates, output_path in metric_specs:
        lavfi = filter_templates[0].format(path=str(output_path).replace("\\", "/").replace(":", "\\:"))
        args = [
            ffmpeg,
            "-hide_banner",
            "-i", str(input_path),
            "-i", str(reference_path),
            "-lavfi", lavfi,
            "-f", "null",
            "-",
        ]
        log_info(f"Media Info {label.upper()} command: {json.dumps(args, ensure_ascii=False)}")
        print(f"Running {label.upper()} reference metric...")
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        stdout_text, _ = decode_subprocess_bytes(result.stdout, "utf-8")
        stderr_text, _ = decode_subprocess_bytes(result.stderr, "utf-8")
        metric_output = media_info_sidecar_path(info_path, f"{label}_ffmpeg_output", ".log")
        metric_output.write_text((stdout_text + "\n" + stderr_text).strip() + "\n", encoding="utf-8")
        generated.append(metric_output)
        if output_path.exists():
            generated.append(output_path)
        if result.returncode != 0:
            skipped.append(f"{label.upper()} failed; FFmpeg output saved to {metric_output}.")
    return generated


def optional_extract_screenshots(
    ffmpeg: str | None,
    input_path: Path,
    payload: dict[str, Any],
    info_path: Path,
    skipped: list[str],
) -> Path | None:
    if not ffmpeg:
        skipped.append("Screenshot extraction skipped: ffmpeg path is unavailable.")
        return None
    duration = services.stream_duration_seconds({}, payload.get("format") or {})
    if not duration or duration <= 0:
        skipped.append("Screenshot extraction skipped: duration is unknown.")
        return None
    screenshot_dir = info_path.with_name(f"{info_path.stem}_samples")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    for percent in (10, 25, 50, 75, 90):
        timestamp = duration * percent / 100.0
        if timestamp <= 0 or timestamp >= duration:
            continue
        output_png = screenshot_dir / f"sample_{percent:02d}pct.png"
        args = [
            ffmpeg,
            "-hide_banner",
            "-ss", f"{timestamp:.3f}",
            "-i", str(input_path),
            "-frames:v", "1",
            "-q:v", "1",
            str(output_png),
        ]
        log_info(f"Media Info screenshot command: {json.dumps(args, ensure_ascii=False)}")
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode == 0 and output_png.exists():
            created += 1
        else:
            log_warn(f"Screenshot extraction failed at {percent}% for {input_path}")
    if created <= 0:
        skipped.append("Screenshot extraction produced no files.")
    return screenshot_dir if created else None


def media_info_folder_candidates(folder_path: Path) -> list[Path]:
    reports_dir = services.default_media_reports_dir()
    files = [
        path for path in folder_path.rglob("*")
        if path.is_file() and not path_is_inside(path, reports_dir)
    ]
    return sorted(files, key=lambda path: str(path.relative_to(folder_path)).lower())


def ensure_folder_exact_packet_sizes(items: list[dict[str, Any]], base_answers: dict[str, Any]) -> None:
    work: list[dict[str, Any]] = []
    for item in items:
        item_answers = dict(base_answers)
        copy_media_metadata(item_answers, item["answers"])
        if "packet_sizes" not in item["answers"] and packet_size_probe_needed(item_answers):
            work.append(item)
    if not work:
        return

    def probe_item(item: dict[str, Any]) -> tuple[dict[str, Any], dict[int, int]]:
        item_answers = dict(base_answers)
        copy_media_metadata(item_answers, item["answers"])
        started_at = time.perf_counter()
        sizes = services.probe_packet_sizes(item_answers["ffprobe"], item_answers["input_path"])
        log_debug(
            f"Folder exact packet-size probe completed for {item_answers['input_path']} "
            f"in {time.perf_counter() - started_at:.3f}s"
        )
        return item, sizes

    max_workers = min(FOLDER_PROBE_WORKERS, len(work))
    if max_workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for item, sizes in executor.map(probe_item, work):
                item["answers"]["packet_sizes"] = sizes
    else:
        for item in work:
            _, sizes = probe_item(item)
            item["answers"]["packet_sizes"] = sizes


def load_input_metadata(answers: dict[str, Any], input_path: Path) -> None:
    probe = services.ffprobe_json(answers["ffprobe"], input_path)
    streams = probe.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
    attachment_streams = [stream for stream in streams if stream.get("codec_type") == "attachment"]
    data_streams = [stream for stream in streams if stream.get("codec_type") == "data"]
    attachment_streams = [stream for stream in streams if stream.get("codec_type") == "attachment"]
    data_streams = [stream for stream in streams if stream.get("codec_type") == "data"]
    if not video_streams and not audio_streams:
        raise ValueError("This file has no detectable video or audio streams.")

    answers["input_path"] = input_path
    answers["probe"] = probe
    answers["format"] = probe.get("format", {})
    answers["video_streams"] = video_streams
    answers["audio_streams"] = audio_streams
    answers["subtitle_streams"] = subtitle_streams
    answers["attachment_streams"] = attachment_streams
    answers["data_streams"] = data_streams
    answers.pop("packet_sizes", None)
    answers.pop("audio_duplicate_report", None)


def join_add_folder_items(
    answers: dict[str, Any],
    folder: Path,
    items: list[dict[str, Any]],
) -> int:
    """Probe every video file in `folder` (sorted by name) and append the usable
    ones to `items`, skipping the main input, already-added files, and generated
    FFmWiz outputs. Returns the number of files actually added."""
    input_path = answers.get("input_path")
    candidates = join_video_files_in_folder(folder)
    if not candidates:
        appio.error("No video files were found in that folder.")
        return 0
    added = 0
    for path in candidates:
        if input_path and paths_same(path, input_path):
            continue
        if any(paths_same(path, item["path"]) for item in items):
            continue
        if looks_like_generated_output_file(path):
            appio.note(f"Skipped generated output file: {path.name}")
            continue
        try:
            items.append(services.join_load_media_item(answers, path))
            added += 1
            log_info(f"Join folder added: {path}")
        except Exception as exc:  # noqa: BLE001
            log_exception(f"Join folder probe skipped: {path}")
            appio.note(f"Skipped (not a usable video): {path.name} ({exc})")
    if added:
        appio.note(f"Added {added} video(s) from folder: {folder}")
    else:
        appio.error("No usable new videos were added from that folder.")
    return added


def step_audio_sample_rate(answers: dict[str, Any]) -> None:
    if answers.get("join_input_items"):
        default_rate = join_source_audio_sample_rate(answers)
        appio.note("Join resamples every input to one common sample rate (uniform output).")
    else:
        default_rate = source_audio_sample_rate(answers)
    ask_audio_sample_rate(answers, default_rate)


def step_source_extra_policy(answers: dict[str, Any]) -> None:
    features = services.source_extra_preservation_features(answers)
    if not features:
        answers["keep_source_metadata"] = True
        answers["keep_source_chapters"] = True
        answers["keep_source_subtitles"] = True
        answers["keep_source_data_streams"] = True
        answers["keep_source_extra_video_streams"] = True
        answers["keep_embedded_attachments"] = False
        return
    print()
    print(paint("Detected source metadata / extra streams:", Color.BOLD + Color.LIGHT_BLUE))
    for feature in features:
        print("  " + paint(feature, Color.WHITE))
    # Join mode defaults to NOT keeping source extras: copying metadata,
    # chapters, data streams, and attachments from one selected input is
    # misleading for a joined program and risks MP4 muxing issues. Non-Join
    # workflows keep the existing default of 'y'.
    join_mode = bool(answers.get("join_input_items"))
    default_keep = not join_mode
    default_text = "y" if default_keep else "n"
    keep = appio.ask_yes_no(
        appio.question_prompt(
            answers,
            "Keep source metadata, chapters, extra video/subtitle/data streams, and embedded fonts/attachments?",
            "y/n; n removes metadata, chapters, extra source video streams, source subtitles, data streams, and embedded font/attachment streams",
            default_text,
        ),
        default_keep,
    )
    answers["keep_source_metadata"] = keep
    answers["keep_source_chapters"] = keep
    answers["keep_source_subtitles"] = keep
    answers["keep_source_data_streams"] = keep
    answers["keep_source_extra_video_streams"] = keep
    if not keep:
        answers["subtitle_tracks"] = []
        answers["keep_embedded_attachments"] = False
        log_info(
            "User choice: keep_source_extras=False; "
            "metadata=no; chapters=no; extra_video_streams=no; subtitles=no; data_streams=no; embedded_attachments=no"
        )
        return

    attachment_count = len(embedded_attachment_streams(answers))
    keep_attachments = False
    if attachment_count:
        if output_supports_embedded_attachments(answers):
            keep_attachments = True
        else:
            appio.note(
                "Embedded font/attachment streams can only be kept reliably in MKV output here. "
                f"Current output format is {answers.get('output_ext')}."
            )
            change_to_mkv = appio.ask_yes_no(
                appio.question_prompt(
                    answers,
                    "Change output format to MKV so embedded font/attachment streams can be kept?",
                    "y/n; otherwise metadata/chapters/subtitles are kept but embedded attachments are dropped",
                    "n",
                ),
                False,
            )
            if change_to_mkv:
                answers["output_ext"] = "mkv"
                keep_attachments = True
    answers["keep_embedded_attachments"] = keep_attachments
    log_info(
        "User choice: keep_source_extras=True; "
        f"metadata=yes; chapters=yes; extra_video_streams=yes; subtitles=yes; data_streams=yes; "
        f"embedded_attachments={keep_attachments}; output_ext={answers.get('output_ext')}"
    )


__all__ = [
    'remap_chapters_for_encode',
    'source_extra_policy_applicable',
    'preview_console_colors',
    'ensure_join_volume_stats',
    'stream_bitrate_plausible',
    'media_info_report_path',
    'optional_reference_metrics',
    'optional_extract_screenshots',
    'media_info_folder_candidates',
    'ensure_folder_exact_packet_sizes',
    'load_input_metadata',
    'join_add_folder_items',
    'step_audio_sample_rate',
    'step_source_extra_policy',
]


# ext04b holds the second half of this tier (split for file size). Re-export its
# names so consumers of `from ffmwiz.support.ext04 import *` see the full tier.
from ffmwiz.support import ext04b as _ext04b  # noqa: E402
# Guard the direct-import case: importing this overflow module FIRST
# re-enters the parent while the child has no __all__ yet.
from ffmwiz.support.ext04b import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_ext04b.__all__)
