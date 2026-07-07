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

    keep_ranges = normalize_cut_ranges(list(answers.get("cut_keep_ranges") or []), source_duration)
    if not keep_ranges:
        keep_ranges = [(0.0, source_duration)]

    # Remap each chapter through retained ranges.
    remapped: list[dict[str, Any]] = []
    for ch in chapters:
        start = copy_cut_chapter_seconds(ch, "start")
        end = copy_cut_chapter_seconds(ch, "end")
        if start is None or end is None or end <= start:
            continue

        # Compute output position by accumulating kept ranges.
        output_offset = 0.0
        ch_new_start: float | None = None
        ch_new_end: float | None = None

        for keep_start, keep_end in keep_ranges:
            keep_duration = keep_end - keep_start
            # Chapter must overlap this kept range to survive.
            overlap_start = max(start, keep_start)
            overlap_end = min(end, keep_end)
            if overlap_end > overlap_start + 1e-6:
                seg_start = output_offset + (overlap_start - keep_start)
                seg_end = output_offset + (overlap_end - keep_start)
                if ch_new_start is None:
                    ch_new_start = seg_start
                ch_new_end = seg_end
            output_offset += keep_duration

        if ch_new_start is None or ch_new_end is None:
            continue

        # Apply speed factor.
        if speed_factor > 0 and abs(speed_factor - 1.0) > 1e-9:
            ch_new_start = ch_new_start / speed_factor
            ch_new_end = ch_new_end / speed_factor

        if ch_new_end <= ch_new_start + 1e-6:
            continue

        remapped.append({
            "start": ch_new_start,
            "end": ch_new_end,
            "metadata": dict(ch.get("tags") or {}),
        })

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


def build_audio_transform_filter_complex(
    answers: dict[str, Any],
    audio_indices: list[int],
) -> tuple[str, list[str]]:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    keep_ranges = normalize_cut_ranges(list(answers.get("audio_cut_keep_ranges") or []), duration)
    parts: list[str] = []
    output_labels: list[str] = []
    for pos, audio_index in enumerate(audio_indices):
        current_label = f"0:a:{audio_index}"
        if keep_ranges:
            source_labels: list[str]
            if len(keep_ranges) > 1:
                source_labels = [f"acut{pos}_src{range_idx}" for range_idx in range(len(keep_ranges))]
                parts.append(
                    f"[{current_label}]asplit={len(keep_ranges)}"
                    f"{''.join(f'[{label}]' for label in source_labels)}"
                )
                log_info(
                    f"Inserted asplit={len(keep_ranges)} for multi-range audio trim from "
                    f"[{current_label}]."
                )
            else:
                source_labels = [current_label]
            range_labels: list[str] = []
            for range_idx, (start, end) in enumerate(keep_ranges):
                label = f"acut{pos}_{range_idx}"
                range_labels.append(f"[{label}]")
                parts.append(
                    f"[{source_labels[range_idx]}]atrim=start={start:.6f}:end={end:.6f},"
                    f"asetpts=PTS-STARTPTS[{label}]"
                )
            if len(keep_ranges) > 1:
                cut_label = f"acut{pos}"
                parts.append(f"{''.join(range_labels)}concat=n={len(keep_ranges)}:v=0:a=1[{cut_label}]")
                current_label = cut_label
            else:
                current_label = f"acut{pos}_0"
        out_label = f"aout{pos}"
        if audio_speed_transform_enabled(answers) or loudnorm_transform_enabled(answers):
            parts.append(f"[{current_label}]{build_encode_audio_speed_filter(answers)}[{out_label}]")
        elif current_label.startswith("0:"):
            parts.append(f"[{current_label}]anull[{out_label}]")
        else:
            parts.append(f"[{current_label}]asetpts=PTS-STARTPTS[{out_label}]")
        output_labels.append(out_label)
    return ";".join(parts), output_labels


def build_video_speed_reverse_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    answers["output_collision_suffix"] = speed_suffix(
        float(answers.get("speed_factor", DEFAULT_SPEED_FACTOR)),
        bool(answers.get("reverse_video")),
    )
    output_path = services.build_output_path(answers)
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    speed = clamp_speed_factor(answers.get("speed_factor", DEFAULT_SPEED_FACTOR))
    reverse = bool(answers.get("reverse_video"))
    include_audio = bool(answers.get("include_audio", True)) and bool(answers.get("audio_streams"))
    audio_count = len(answers.get("audio_streams") or [])

    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n", "-i", str(input_path)]
    cmd.extend(["-map", "0:v:0"])
    cmd.extend(["-sn", "-dn"])
    cmd.extend(["-filter:v", build_video_speed_filter(speed, reverse)])
    cmd.extend(["-c:v", "libx264", "-preset", CPU_PRESET, "-crf", "18", "-pix_fmt", cpu_pixel_format_for_output(answers)])
    if include_audio:
        labels: list[str] = []
        parts: list[str] = []
        for index in range(audio_count):
            label = f"aspd{index}"
            labels.append(label)
            parts.append(f"[0:a:{index}]{build_audio_speed_filter(speed, reverse)}[{label}]")
        cmd.extend(["-filter_complex", ";".join(parts)])
        for label in labels:
            cmd.extend(["-map", f"[{label}]"])
        cmd.extend(["-c:a", DEFAULT_AUDIO_CODEC, "-b:a", f"{DEFAULT_SPEED_AUDIO_BITRATE_KBPS}k"])
        if AUDIO_CHANNELS:
            cmd.extend(["-ac", str(AUDIO_CHANNELS)])
    else:
        cmd.append("-an")
    if answers["output_ext"].lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])
    cmd.append(str(output_path))
    log_info(
        f"Video speed/reverse command built: speed={speed}; reverse={reverse}; "
        f"include_audio={include_audio}; output={output_path}"
    )
    return cmd


def run_segmented_reverse_video_speed(answers: dict[str, Any]) -> tuple[int, float]:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    if duration <= 0:
        return run_ffmpeg_with_progress(
            answers["cmd"],
            total_duration=None,
            label="Video Speed / Reverse",
        )
    output_path = Path(answers["output_path"])
    speed = clamp_speed_factor(answers.get("speed_factor", DEFAULT_SPEED_FACTOR))
    chunks = split_ranges_for_reverse_segments([], duration)
    if not chunks:
        return 1, 0.0
    appio.note(
        f"Reverse mode uses {len(chunks)} segment(s) of up to {int(REVERSE_SEGMENT_SECONDS)}s "
        "to avoid buffering the full video in RAM."
    )
    started_at = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ffmwiz_reverse_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        segment_paths: list[Path] = []
        segment_ext = output_path.suffix.lstrip(".") or "mp4"
        for idx, (start, end) in enumerate(chunks, start=1):
            segment_path = tmpdir / f"reverse_seg_{idx:04d}.{segment_ext}"
            segment_paths.append(segment_path)
            cmd = build_video_speed_reverse_segment_command(answers, start, end, segment_path)
            log_info(f"Reverse segment {idx}/{len(chunks)} command: {command_to_powershell(cmd)}")
            appio.note(f"Reverse segment {idx}/{len(chunks)}: {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}")
            rc, _ = run_ffmpeg_with_progress(
                cmd,
                total_duration=max(0.001, (end - start) / speed),
                label=f"Reverse segment {idx}/{len(chunks)}",
            )
            if rc != 0:
                return rc, time.perf_counter() - started_at
        concat_list = tmpdir / "concat.txt"
        write_concat_list(list(reversed(segment_paths)), concat_list)
        concat_cmd = build_concat_copy_command(answers["ffmpeg"], concat_list, output_path)
        log_info("Reverse concat command: " + command_to_powershell(concat_cmd))
        appio.note("Concatenating reversed segments...")
        rc, _ = run_ffmpeg_with_progress(
            concat_cmd,
            total_duration=(duration / speed if duration > 0 else None),
            label="Reverse concat",
        )
        return rc, time.perf_counter() - started_at


def build_audio_speed_reverse_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    answers["output_ext"] = resolve_audio_tool_output_ext(answers)
    answers["output_collision_suffix"] = speed_suffix(
        float(answers.get("speed_factor", DEFAULT_SPEED_FACTOR)),
        bool(answers.get("reverse_audio")),
    )
    output_path = services.build_output_path(answers)
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    audio_index = int(answers.get("audio_index", 0))
    speed = clamp_speed_factor(answers.get("speed_factor", DEFAULT_SPEED_FACTOR))
    reverse = bool(answers.get("reverse_audio"))
    cmd: list[str] = [
        ffmpeg,
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-i",
        str(input_path),
        "-map",
        f"0:a:{audio_index}",
        "-vn",
        "-sn",
        "-dn",
        "-filter:a",
        build_audio_speed_filter(speed, reverse),
    ]
    cmd.extend(audio_tool_encode_options(answers["output_ext"], sample_rate=resolve_audio_sample_rate(answers)))
    cmd.append(str(output_path))
    log_info(
        f"Audio speed/reverse command built: audio_index={audio_index}; "
        f"speed={speed}; reverse={reverse}; output={output_path}"
    )
    return cmd


def build_audio_cut_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    answers["output_ext"] = resolve_audio_tool_output_ext(answers)
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    keep_ranges = normalize_cut_ranges(list(answers.get("audio_keep_ranges") or []), duration)
    if not keep_ranges:
        raise ValueError("No valid audio keep ranges were selected.")
    answers["audio_keep_ranges"] = keep_ranges
    answers["output_collision_suffix"] = "_AudioCut"
    output_path = services.build_output_path(answers)
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    audio_index = int(answers.get("audio_index", 0))

    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n"]
    if len(keep_ranges) == 1:
        start, end = keep_ranges[0]
        if start > 0:
            cmd.extend(["-ss", f"{start:.6f}"])
        cmd.extend(["-i", str(input_path), "-t", f"{max(0.0, end - start):.6f}"])
        cmd.extend(["-map", f"0:a:{audio_index}", "-vn", "-sn", "-dn"])
    else:
        cmd.extend(["-i", str(input_path)])
        parts: list[str] = []
        labels: list[str] = []
        source_labels = [f"acut_src{idx}" for idx in range(len(keep_ranges))]
        parts.append(
            f"[0:a:{audio_index}]asplit={len(keep_ranges)}"
            f"{''.join(f'[{label}]' for label in source_labels)}"
        )
        log_info(
            f"Inserted asplit={len(keep_ranges)} for multi-range audio trim from [0:a:{audio_index}]."
        )
        for idx, (start, end) in enumerate(keep_ranges):
            label = f"a{idx}"
            labels.append(f"[{label}]")
            parts.append(
                f"[{source_labels[idx]}]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS[{label}]"
            )
        parts.append(f"{''.join(labels)}concat=n={len(keep_ranges)}:v=0:a=1[a]")
        cmd.extend(["-filter_complex", ";".join(parts), "-map", "[a]", "-vn", "-sn", "-dn"])

    cmd.extend(audio_tool_encode_options(answers["output_ext"], sample_rate=resolve_audio_sample_rate(answers)))
    cmd.append(str(output_path))
    log_info(
        f"Audio cut command built: audio_index={audio_index}; ranges={keep_ranges}; output={output_path}"
    )
    return cmd


def apply_unified_video_editor_answers(answers: dict[str, Any]) -> None:
    if not answers.get("_unified_video_editor_used"):
        return
    if "_unified_video_speed" in answers or "_unified_reverse_video" in answers:
        speed = clamp_speed_factor(answers.get("_unified_video_speed", DEFAULT_SPEED_FACTOR))
        reverse = bool(answers.get("_unified_reverse_video"))
        answers["video_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
        answers["video_speed_factor"] = speed
        answers["reverse_video"] = reverse
        answers["audio_speed_from_video"] = bool(answers.get("_unified_include_audio"))
    if "_unified_cut_keep_ranges" in answers:
        duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
        duration += sum(float(item.get("duration") or 0.0) for item in answers.get("join_input_items") or [])
        answers["cut_keep_ranges"] = normalize_cut_ranges(answers.get("_unified_cut_keep_ranges") or [], duration)


def log_final_normalized_answers(answers: dict[str, Any], cmd: list[str]) -> None:
    try:
        input_paths = [str(answers.get("input_path"))]
        input_paths.extend(str(item.get("path")) for item in answers.get("join_input_items") or [] if item.get("path"))
        source_duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
        split_points = normalize_separator_points(answers.get("separator_points"), final_processed_duration_for_splits(answers, source_duration))
        split_intervals = separator_ranges(split_points, final_processed_duration_for_splits(answers, source_duration)) if split_points else []
        text = " ".join(str(part) for part in cmd)
        summary = {
            "input_paths": input_paths,
            "output_path": str(answers.get("output_path")),
            "split_output_paths": [str(path) for path in answers.get("split_output_paths") or []],
            "video_codec": answers.get("video_codec"),
            "source_bit_depth": describe_video_bit_depth(source_video_stream(answers) or {}),
            "output_bit_depth": output_video_bit_depth(answers) if output_has_video(answers) else None,
            "output_cpu_pixel_format": cpu_pixel_format_for_output(answers) if output_has_video(answers) else None,
            "output_cuda_pixel_format": cuda_pixel_format_for_output(answers) if output_has_video(answers) else None,
            "audio_codec": answers.get("audio_codec"),
            "selected_audio_tracks": selected_audio_streams(answers) if answers.get("audio_streams") else [],
            "additional_video_streams": len(additional_source_video_streams(answers)),
            "attachment_streams": len(embedded_attachment_streams(answers)),
            "data_streams": len(source_data_streams(answers)),
            "keep_source_metadata": source_metadata_keep_enabled(answers),
            "keep_source_chapters": source_chapters_keep_enabled(answers),
            "keep_source_subtitles": source_subtitles_keep_enabled(answers),
            "keep_source_data_streams": source_data_keep_enabled(answers),
            "keep_source_extra_video_streams": source_extra_video_keep_enabled(answers),
            "keep_embedded_attachments": bool(answers.get("keep_embedded_attachments")),
            "crop_enabled": bool(answers.get("crop_enabled")),
            "crop_margins": {
                "top": answers.get("crop_top", 0),
                "left": answers.get("crop_left", 0),
                "right": answers.get("crop_right", 0),
                "bottom": answers.get("crop_bottom", 0),
            },
            "crop_box_dimensions": answers.get("crop_box_dimensions"),
            "final_resolution": answers.get("final_resolution"),
            "fps": answers.get("fps"),
            "video_speed": encode_video_speed_factor(answers) if video_speed_transform_enabled(answers) else 1.0,
            "audio_speed": encode_audio_speed_factor(answers) if audio_speed_transform_enabled(answers) else 1.0,
            "reverse_video": bool(answers.get("reverse_video")),
            "reverse_audio": encode_audio_reverse_enabled(answers),
            "split_enabled": bool(split_points),
            "split_points": split_points,
            "split_intervals": split_intervals,
            "loudnorm_enabled": loudnorm_transform_enabled(answers),
            "loudnorm_target_i": answers.get("loudnorm_target_i"),
            "loudnorm_measured": answers.get("loudnorm_measured"),
            "loudnorm_applied_tracks": selected_audio_streams(answers) if loudnorm_transform_enabled(answers) and answers.get("audio_streams") else [],
            "gpu_requested": bool(answers.get("use_gpu")),
            "cuda_fast_path": "-hwaccel_output_format cuda" in text and "scale_cuda" in text,
            "complex_cpu_graph": "-filter_complex" in cmd,
            "gpu_decode_only": "-hwaccel cuda" in text and "-hwaccel_output_format cuda" not in text,
            "nvenc_encode": "_nvenc" in text,
            "nvenc_multipass": normalize_nvenc_multipass_mode(answers.get("nvenc_multipass")),
            "nvenc_multipass_skip_reason": answers.get("nvenc_multipass_skip_reason"),
            "scale_cuda_used": "scale_cuda" in text,
            "hwdownload_used": "hwdownload" in text,
            "hwupload_cuda_used": "hwupload_cuda" in text,
        }
        log_info("Final normalized answers before execution:\n" + json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    except Exception as exc:
        log_warn(f"Could not log final normalized answers: {exc}")


def run_cpu_two_pass_ffmpeg(
    cmd: list[str],
    answers: dict[str, Any],
    *,
    total_duration: float | None,
    progress_output_paths: list[Path],
) -> tuple[int, float]:
    first, second, passlog = build_cpu_two_pass_commands(cmd, answers)
    log_info("CPU two-pass encoding enabled.")
    log_command("CPU two-pass pass 1", first)
    log_command("CPU two-pass pass 2", second)
    try:
        print(paint("Starting FFmpeg pass 1/2...", Color.GREEN))
        rc1, elapsed1 = run_ffmpeg_with_progress(
            first,
            total_duration=total_duration,
            label="FFmpeg encode pass 1/2",
            initial_detail="CPU two-pass analysis pass",
        )
        if rc1 != 0:
            return rc1, elapsed1
        print()
        print(paint("Starting FFmpeg pass 2/2...", Color.GREEN))
        rc2, elapsed2 = run_ffmpeg_with_progress(
            second,
            total_duration=total_duration,
            label="FFmpeg encode pass 2/2",
            initial_detail="CPU two-pass final encode pass",
            progress_output_paths=progress_output_paths,
        )
        return rc2, elapsed1 + elapsed2
    finally:
        cleanup_cpu_two_pass_logs(passlog)


def _apply_manual_audio_transform(answers: dict[str, Any]) -> None:
    """Manual (non-GUI) audio transform with prompt-by-prompt Back navigation:
    optional cuts/lossless-split, then speed, then reverse. Back at the first
    prompt raises Back (to return to the editor menu); Back at a later prompt
    returns to the immediately previous prompt."""
    answers.pop("_audio_transform_split_points", None)
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    keep_ranges: list[tuple[float, float]] = []
    speed = 1.0
    reverse = False
    stage = "cuts_q"
    while True:
        if stage == "cuts_q":
            # Back here propagates to the editor menu.
            want = appio.ask_yes_no(yn_prompt("Add audio cuts or split into separate files?", False), False)
            if want:
                stage = "cut_detail"
            else:
                keep_ranges = []
                stage = "speed"
        elif stage == "cut_detail":
            try:
                kr = services.collect_cut_ranges_terminal(answers, 25.0, duration, allow_split=True)
            except Back:
                stage = "cuts_q"
                continue
            split_points = answers.pop("_manual_split_points", None)
            if split_points:
                # Lossless split: produce multiple files; speed/reverse do not apply.
                answers["_audio_transform_split_points"] = split_points
                answers["audio_cut_keep_ranges"] = []
                answers["audio_speed_enabled"] = False
                answers["reverse_audio"] = False
                answers["_audio_transform_noop"] = False
                log_info(f"Manual audio split points: {split_points}")
                return
            keep_ranges = kr
            if keep_ranges:
                print(paint(format_audio_ranges_for_summary(keep_ranges, "Audio cuts (keep ranges)"), Color.LIME))
            stage = "speed"
        elif stage == "speed":
            value = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    "Enter audio speed",
                    "examples: 150% or 1.5x or 1.5 (100% = no change)",
                    "100%",
                )
            )
            if is_back_value(value):
                stage = "cuts_q"  # Back -> previous prompt
                continue
            if not value:
                value = "100%"
            try:
                speed = parse_speed_factor(value)
            except ValueError as exc:
                appio.error(str(exc))
                continue  # re-ask speed
            stage = "reverse"
        else:  # reverse
            try:
                reverse = appio.ask_yes_no(yn_prompt("Reverse audio?", False), False)
            except Back:
                stage = "speed"  # Back -> previous prompt
                continue
            break
    if not (keep_ranges or reverse or abs(speed - 1.0) > 1e-6):
        appio.note("No audio transform was selected (no cuts, speed 100%, no reverse).")
        answers["_audio_transform_noop"] = True
        return
    answers["audio_cut_keep_ranges"] = keep_ranges
    answers["audio_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
    answers["audio_speed_factor"] = speed
    answers["reverse_audio"] = reverse
    answers["_audio_transform_noop"] = False
    log_info(f"Manual audio transform: cuts={keep_ranges}; speed={speed}; reverse={reverse}")


def write_metadata_report(input_path: Path, report_type: str, answers: dict[str, Any]) -> Path:
    if report_type == "human":
        probe = probe_media_json(input_path, answers["ffprobe"])
        lines = [f"Metadata report for: {input_path}", "", "Streams"]
        lines.extend("  " + _strip_ansi(metadata_stream_line(probe, stream)) for stream in probe.get("streams") or [])
        lines.extend(["", "Chapters"])
        lines.extend("  " + line for line in metadata_chapter_lines(probe))
        output_path = metadata_report_output_path(input_path, "_metadata_report", ".txt")
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return output_path
    args = metadata_json_report_command(answers["ffprobe"], input_path, report_type)
    log_info("Metadata report ffprobe command: " + command_to_powershell(args))
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        log_error("Metadata report ffprobe failed:\n" + (result.stderr or result.stdout or ""))
        raise RuntimeError("Metadata report failed. See log file.")
    suffix = {"full": "_metadata_report", "tags": "_metadata_tags", "color": "_metadata_color", "disposition": "_metadata_disposition", "chapters": "_metadata_chapters"}.get(report_type, "_metadata_report")
    output_path = metadata_report_output_path(input_path, suffix, ".json")
    output_path.write_text(result.stdout, encoding="utf-8")
    return output_path


def _capability_cache_view(ffmpeg: str, ffprobe: str) -> None:
    path = capability_cache_path()
    print()
    print("  " + field_text("cache file", str(path), Color.AQUA))
    cache = load_capability_cache()
    envs = cache.get("environments", {})
    if not envs:
        appio.note("No cached capability results yet.")
        return
    # Show the current environment identity for both CPU and NVENC bindings.
    for enc_label, sample_encoder in (("CPU encoders", "libx265"), ("NVENC encoders", "hevc_nvenc")):
        _identity, key = services.capability_environment_key(ffmpeg, ffprobe, sample_encoder)
        print("  " + field_text("environment (%s)" % enc_label, key[:12], Color.DIM))
    for env_key, env in envs.items():
        print(paint("  Environment %s" % env_key[:12], Color.BOLD + Color.LIME))
        ident = env.get("ffmpeg_identity", {})
        print("    " + field_text("ffmpeg", ident.get("version", "?"), Color.WHITE))
        hw = env.get("hardware_identity", {})
        if hw.get("gpu") not in (None, "n/a"):
            print("    " + field_text("gpu/driver", f"{hw.get('gpu')} / {hw.get('driver')}", Color.WHITE))
        caps = env.get("capabilities", {}).get(CAPABILITY_GROUP, {})
        for combo, entry in caps.items():
            fr = entry.get("expected_final_range")
            shown = "unspecified" if fr in {"unknown", "", None} else fr
            print("    " + field_text(
                combo,
                f"status={entry.get('status')}; expected_final_range={shown}; "
                f"verified={entry.get('verified_at_utc')}",
                Color.AQUA,
            ))


def copy_cut_source_duration(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
    chapters: list[dict[str, Any]],
) -> float:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    for start, end in keep_ranges:
        duration = max(duration, float(start or 0.0), float(end or 0.0))
    for chapter in chapters:
        end = copy_cut_chapter_seconds(chapter, "end")
        if end is not None:
            duration = max(duration, end)
    return max(0.0, duration)


def probe_additional_track_file(ffprobe: str, path: Path, ffmpeg: str | None = None) -> dict[str, Any]:
    probe = services.ffprobe_json(ffprobe, path)
    streams = probe.get("streams") or []
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    if not audio_streams and not subtitle_streams:
        raise ValueError("Additional files must contain at least one audio or subtitle stream.")
    audio_volume_stats: dict[int, dict[str, str]] = {}
    if audio_streams and ffmpeg:
        audio_volume_stats = services.get_audio_volume_stats({
            "ffmpeg": ffmpeg,
            "input_path": path,
            "audio_streams": audio_streams,
        })
    return {
        "path": path,
        "format": probe.get("format", {}),
        "audio_streams": audio_streams,
        "audio_volume_stats": audio_volume_stats,
        "subtitle_streams": subtitle_streams,
        "video_streams": video_streams,
        "chapters": probe.get("chapters") or [],
    }


def extract_scan_files(answers: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    """Return extract entries for a file or folder path. A folder is scanned
    (non-recursively) for media files. Each entry: {path, format, candidates,
    by_index}. Files that fail to probe or have no extractable streams are
    skipped."""
    ffprobe = answers["ffprobe"]
    if path.is_dir():
        paths = sorted(
            (p for p in path.iterdir()
             if is_folder_media_candidate(p) and not looks_like_generated_output_file(p)),
            key=lambda p: p.name.lower(),
        )
    else:
        paths = [path]
    entries: list[dict[str, Any]] = []
    for p in paths:
        try:
            probe = services.ffprobe_json(ffprobe, p)
        except Exception:
            log_warn(f"Extract Stream skipped unreadable file: {p}")
            continue
        streams = probe.get("streams") or []
        candidates = [s for s in streams if str(s.get("codec_type") or "").lower() in {"video", "audio", "subtitle"}]
        if not candidates:
            continue
        by_index = {stream_global_index(s): s for s in candidates if stream_global_index(s) is not None}
        entries.append({
            "path": p,
            "format": probe.get("format", {}),
            "candidates": candidates,
            "by_index": by_index,
        })
    return entries


def extract_describe_stream(stream: dict[str, Any], fmt: dict[str, Any]) -> str:
    """Compact per-stream description for the extract listing (no packet probe)."""
    codec_type = str(stream.get("codec_type") or "unknown")
    codec = str(stream.get("codec_name") or "unknown")
    idx = stream_global_index(stream)
    color = {"video": Color.MAGENTA, "audio": Color.BLUE, "subtitle": Color.LIGHT_YELLOW}.get(codec_type, Color.WHITE)
    parts = [
        field_text("stream index", idx if idx is not None else "unknown", Color.LIGHT_BLUE),
        field_text("type", codec_type, color),
        field_text("codec", codec, Color.CYAN),
        field_text("duration", format_duration(services.stream_duration_seconds(stream, fmt)), Color.MAGENTA),
    ]
    if codec_type == "video":
        parts.append(field_text("size", f"{stream.get('width', '?')}x{stream.get('height', '?')}", Color.LIME))
    elif codec_type == "audio":
        parts.append(field_text("channels", stream.get("channels", "?"), Color.GREEN))
        parts.append(field_text("sample_rate", stream.get("sample_rate", "?"), Color.MAGENTA))
        lang = display_language((stream.get("tags") or {}).get("language"))
        if lang:
            parts.append(field_text("lang", lang, Color.AQUA))
    elif codec_type == "subtitle":
        lang = display_language((stream.get("tags") or {}).get("language"))
        parts.append(field_text("language", lang or "unknown", Color.AQUA))
    return " | ".join(parts)


def print_prerequisite_summary(ffmpeg: str | None, ffprobe: str | None) -> None:
    """Prerequisites (FFmpeg/FFprobe and the optional PySide6 GUI runtime) are
    validated behind the scenes by check_tools()/ensure_pyside6_installed(),
    which prompt to install anything missing. This summary is intentionally
    silent: it only records the resolved tools to the log, with no console
    output, so startup stays clean."""
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    log_info(
        f"Prerequisites OK: Python={py_ver}; ffmpeg={ffmpeg or 'missing'}; "
        f"ffprobe={ffprobe or 'missing'}; pyside6={'installed' if _pyside6_available() else 'absent'}",
        component="Startup",
    )


def build_join_audio_encode_command(answers: dict[str, Any], items: list[dict[str, Any]], output_path: Path) -> list[str]:
    """Audio-only Join re-encode: prepare each input's first audio track with
    the shared join audio preparation, concat them, and encode to AAC. Used when
    every joined input is audio-only and stream copy is not possible."""
    output_path = resolve_output_collision_against_inputs(
        output_path,
        [Path(item["path"]) for item in items if item.get("path")],
        answers.get("output_collision_suffix", "_Encode"),
    )
    answers["output_path"] = output_path
    bitrate = int(answers.get("audio_bitrate_kbps") or DEFAULT_AUDIO_BITRATE_KBPS)
    cmd: list[str] = [answers["ffmpeg"], "-hide_banner", "-y" if OVERWRITE_OUTPUT else "-n"]
    for item in items:
        cmd.extend(["-i", str(item["path"])])
    filters: list[str] = []
    inputs: list[str] = []
    prep = join_audio_prep_filter(join_target_sample_rate(answers))
    for idx, _item in enumerate(items):
        filters.append(f"[{idx}:a:0]{prep}[a{idx}]")
        inputs.append(f"[a{idx}]")
    filters.append(f"{''.join(inputs)}concat=n={len(items)}:v=0:a=1[acat]")
    # Apply the same transforms the wizard collected to the JOINED audio:
    # cut trims, then speed/reverse and loudnorm (no-ops for the menu-12 join,
    # which sets none of these).
    source_join_duration = sum(float(item.get("duration") or 0.0) for item in items)
    keep_ranges = normalize_cut_ranges(
        list(answers.get("cut_keep_ranges") or answers.get("audio_cut_keep_ranges") or []),
        source_join_duration,
    )
    label = append_join_trim_concat_filter(filters, "acat", keep_ranges, "audio", "acut")
    if audio_speed_transform_enabled(answers) or loudnorm_transform_enabled(answers):
        filters.append(f"[{label}]{build_encode_audio_speed_filter(answers)}[a]")
    else:
        filters.append(f"[{label}]asetpts=PTS-STARTPTS[a]")
    cmd.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[a]",
        "-vn", "-sn", "-dn",
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-c:a", "aac", "-b:a", f"{bitrate}k", "-ac", "2", "-ar", str(join_target_sample_rate(answers)),
    ])
    if output_path.suffix.lower() in {".mp4", ".m4a", ".mov"}:
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(str(output_path))
    return cmd


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
    'build_audio_transform_filter_complex',
    'build_video_speed_reverse_command',
    'run_segmented_reverse_video_speed',
    'build_audio_speed_reverse_command',
    'build_audio_cut_command',
    'apply_unified_video_editor_answers',
    'log_final_normalized_answers',
    'run_cpu_two_pass_ffmpeg',
    '_apply_manual_audio_transform',
    'write_metadata_report',
    '_capability_cache_view',
    'copy_cut_source_duration',
    'probe_additional_track_file',
    'extract_scan_files',
    'extract_describe_stream',
    'print_prerequisite_summary',
    'build_join_audio_encode_command',
]
