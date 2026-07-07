"""FFmWiz second-layer helpers (appio-dependent), tier 0.

Extracted from FFmWiz.py after the appio qualification; imports core,
existing support modules, and appio. Acyclic (imports only lower tiers).
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
from ffmwiz.appio import *  # noqa: F401,F403
from ffmwiz import appio  # qualified primitives


def option_list(items: list[str]) -> str:
    return paint(",".join(items), Color.LIGHT_BLUE)


def example_text(text: str) -> str:
    return paint(text, Color.LIGHT_BLUE)


def keep_value_text(text: str) -> str:
    return paint(text, Color.KEEP_VALUE)


def suggestion_text(text: str) -> str:
    return paint(text, Color.SUGGESTION)


def colored_audio_track_hint() -> str:
    return (
        f"{paint('n/all=all', Color.AUDIO_ALL)}; "
        f"{paint('d=drop confirmed duplicates', Color.AUDIO_DROP_DUP)}; "
        f"{paint('e=drop empty/near-empty', Color.AUDIO_DROP_EMPTY)}; "
        f"{paint('de=both', Color.AUDIO_DROP_BOTH)}; "
        f"{paint('track number 0 is the first audio track', Color.AUDIO_TRACK_NOTE)}"
    )


def colored_hardsub_quality_options() -> str:
    return (
        f"{paint('1=near-lossless (closest to source)', Color.LIME)}; "
        f"{paint('2=high quality (smaller)', Color.CYAN)}; "
        f"{paint('3=balanced (more compression)', Color.ORANGE)}; "
        f"{paint('4=custom CRF/CQ', Color.PINK)}"
    )


def colored_hardsub_audio_options() -> str:
    return (
        f"{paint('1=copy all audio tracks', Color.LIME)}; "
        f"{paint('2=choose audio tracks to copy', Color.CYAN)}; "
        f"{paint('3=no audio', Color.RED)}"
    )


def field_text(name: str, value: Any, value_color: str = Color.WHITE) -> str:
    return f"{paint(name + ':', Color.GRAY)} {paint(str(value), value_color)}"


def append_final_split_filters(
    filters: list[str],
    video_label: str,
    audio_labels: list[str],
    split_points: Any,
    final_duration: float,
    prefix: str,
    fps: float = 0.0,
) -> tuple[list[str], list[list[str]], list[tuple[float, float]]]:
    intervals = separator_ranges(split_points, final_duration)
    if len(intervals) <= 1:
        return [video_label], [[label for label in audio_labels]], intervals
    # Snap each interior split boundary to the OUTPUT frame grid so every part
    # starts/ends exactly on a frame after any fps change (no fractional first
    # frame). The clip's own start (0) and end (final_duration) are left as-is.
    if fps and float(fps) > 0:
        frame = 1.0 / float(fps)
        bounds = (
            [intervals[0][0]]
            + [round(round(iv[1] / frame) * frame, 6) for iv in intervals[:-1]]
            + [intervals[-1][1]]
        )
        snapped = [(s, e) for s, e in zip(bounds, bounds[1:]) if e > s + 1e-6]
        if snapped and snapped != intervals:
            log_info(f"Split boundaries snapped to {float(fps):g} fps frame grid: {intervals} -> {snapped}")
            intervals = snapped
    part_count = len(intervals)
    video_sources = [f"{prefix}vpart{idx}_src" for idx in range(part_count)]
    filters.append(f"[{video_label}]split={part_count}{''.join(f'[{label}]' for label in video_sources)}")
    log_info(f"Filter graph decision: final Split enabled; video split={part_count}; intervals={intervals}")
    video_outputs: list[str] = []
    for idx, (start, end) in enumerate(intervals):
        out_label = f"{prefix}vout{idx}"
        video_outputs.append(out_label)
        filters.append(
            f"[{video_sources[idx]}]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[{out_label}]"
        )
    audio_outputs_by_part: list[list[str]] = [[] for _ in intervals]
    for audio_pos, audio_label in enumerate(audio_labels):
        audio_sources = [f"{prefix}apart{idx}_{audio_pos}_src" for idx in range(part_count)]
        filters.append(f"[{audio_label}]asplit={part_count}{''.join(f'[{label}]' for label in audio_sources)}")
        log_info(f"Filter graph decision: final Split enabled; audio label [{audio_label}] asplit={part_count}.")
        for idx, (start, end) in enumerate(intervals):
            out_label = f"{prefix}aout{idx}_{audio_pos}"
            audio_outputs_by_part[idx].append(out_label)
            filters.append(
                f"[{audio_sources[idx]}]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[{out_label}]"
            )
    return video_outputs, audio_outputs_by_part, intervals


def set_nvenc_multipass_skip_reason(answers: dict[str, Any], reason: str) -> None:
    answers["nvenc_multipass_skip_reason"] = reason
    log_info(f"NVENC multipass skipped: {reason}")


def step_folder_batch_color_range(answers: dict[str, Any]) -> None:
    """Batch policy for files with an unknown color range. Asked once; the
    choice is stored and reused for every file (and every Split Part / two-pass
    pass) of this batch."""
    unknown_items = folder_items_with_unknown_color_range(answers)
    if not unknown_items:
        return
    previous = str(answers.get("_batch_color_range_policy") or "").strip().lower()
    default_choice = {"tv": "1", "unspecified": "2", "pc": "3", "each": "4"}.get(previous, "1")
    print()
    appio.note(f"One or more input videos have an unknown color range ({len(unknown_items)} file(s)):")
    mapping = {"1": "tv", "2": "unspecified", "3": "pc", "4": "each"}
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Select batch color-range policy for unknown files",
                "1=TV/Limited; 2=Do not force a range; 3=PC/Full; 4=Ask per file",
                default_choice,
            )
        )
        lowered = value.strip().lower()
        if is_back_value(value):
            raise Back()
        if not lowered:
            lowered = default_choice
        policy = mapping.get(lowered)
        if not policy:
            appio.error("Enter 1, 2, 3, or 4. Use 0 to go back.")
            continue
        answers["_batch_color_range_policy"] = policy
        if policy == "each":
            per_file: dict[str, str] = {}
            sub_mapping = {"1": "tv", "2": "unspecified", "3": "pc"}
            for item in unknown_items:
                print()
                appio.note(f"Source color range is unknown: {item['path'].name}")
                while True:
                    sub = appio.ask_raw(
                        appio.question_prompt(
                            answers,
                            "Select color range for this file",
                            "1=Assume TV/Limited; 2=Do not force a range; 3=Assume PC/Full",
                            "1",
                        )
                    ).strip().lower()
                    if is_back_value(sub):
                        raise Back()
                    if not sub:
                        sub = "1"
                    file_choice = sub_mapping.get(sub)
                    if not file_choice:
                        appio.error("Enter 1, 2, or 3. Use 0 to go back.")
                        continue
                    per_file[str(item["path"])] = file_choice
                    break
            answers["_batch_color_range_per_file"] = per_file
        else:
            answers.pop("_batch_color_range_per_file", None)
        log_info(
            "Batch color-range policy resolved: policy=%s; unknown_files=%d"
            % (policy, len(unknown_items))
        )
        appio.note(
            "Batch color range policy: "
            + {"tv": "Assume TV/Limited", "unspecified": "Do not force a range in FFmWiz",
               "pc": "Assume PC/Full", "each": "Ask per file"}[policy]
            + " (applies to unknown-range files only; detected ranges are kept)."
        )
        return


def apply_folder_batch_color_range(job: dict[str, Any], settings_answers: dict[str, Any], item: dict[str, Any]) -> None:
    """Resolve a folder job's color-range choice from the batch policy. Known
    per-file ranges are never overwritten by the batch assumption."""
    if not output_has_video(job):
        return
    if source_color_range_known(job):
        # Detected range is authoritative; do not let the batch override it.
        job.pop("color_range_choice", None)
        return
    policy = str(settings_answers.get("_batch_color_range_policy") or "").strip().lower()
    if not policy:
        return
    job["_color_range_from_batch"] = True
    if policy == "each":
        per_file = settings_answers.get("_batch_color_range_per_file") or {}
        choice = per_file.get(str(item.get("path")))
        if choice:
            job["color_range_choice"] = choice
            job["_color_range_from_batch"] = False  # per-file is a direct user choice
    elif policy in {"tv", "pc", "unspecified"}:
        job["color_range_choice"] = policy
    resolved, source = resolve_color_range(job)
    log_info(
        "Folder file color range: file=%s; detected=unknown; resolved=%s; "
        "resolution_source=%s; output_metadata=%s; pixel_value_range_conversion=no"
        % (item.get("path"), resolved or "unspecified", source, resolved or "omitted")
    )


def append_audio_encode_options(cmd: list[str], answers: dict[str, Any], has_audio: bool) -> None:
    if not has_audio:
        cmd.append("-an")
        return
    audio_codec = normalize_audio_codec(
        answers.get("audio_codec"),
        default_audio_codec_for_ext(answers.get("output_ext", "")),
    )
    answers["audio_codec"] = audio_codec
    if audio_codec == "copy":
        appio.note("Audio copy cannot be used after Split/filter processing. AAC was selected for audio.")
        audio_codec = DEFAULT_AUDIO_CODEC
        answers["audio_codec"] = audio_codec
    cmd.extend(["-c:a", audio_codec])
    audio_bitrate = answers.get("audio_bitrate_kbps")
    if audio_bitrate and audio_codec_uses_bitrate(str(audio_codec)):
        cmd.extend(["-b:a", f"{audio_bitrate}k"])
    if AUDIO_CHANNELS:
        cmd.extend(["-ac", str(AUDIO_CHANNELS)])
    sample_rate = resolve_audio_sample_rate(answers)
    if sample_rate:
        cmd.extend(["-ar", str(sample_rate)])


def append_source_metadata_chapter_options(cmd: list[str], answers: dict[str, Any]) -> None:
    if not output_has_video(answers):
        return
    cmd.extend(["-map_metadata", "0" if source_metadata_keep_enabled(answers) else "-1"])

    # Chapter handling: if user disabled chapters, always drop.
    if not source_chapters_keep_enabled(answers):
        cmd.extend(["-map_chapters", "-1"])
        return

    # If the timeline is not modified, preserve source chapters directly.
    if not timeline_is_modified(answers):
        cmd.extend(["-map_chapters", "0"])
        return

    # If a chapter metadata input was injected, use its index.
    chapter_input_index = answers.get("_chapter_metadata_input_index")
    if chapter_input_index is not None:
        cmd.extend(["-map_chapters", str(chapter_input_index)])
        log_info(f"Chapters: remapped to processed timeline (metadata input {chapter_input_index})")
        return

    # Timeline is modified but no metadata was prepared – disable chapters.
    cmd.extend(["-map_chapters", "-1"])
    log_info("Chapters: disabled because timeline changed and remapping was unavailable")


def append_clear_reencoded_stream_stat_metadata(
    cmd: list[str],
    answers: dict[str, Any],
    *,
    video_output_count: int = 0,
    audio_output_count: int = 0,
    subtitle_output_count: int = 0,
) -> None:
    if not source_metadata_keep_enabled(answers):
        return
    cleared: list[str] = []
    if int(video_output_count or 0) > 0:
        append_clear_stream_stat_metadata(cmd, "v")
        cleared.append("v:*")
    if int(audio_output_count or 0) > 0:
        append_clear_stream_stat_metadata(cmd, "a")
        cleared.append("a:*")
    if int(subtitle_output_count or 0) > 0:
        append_clear_stream_stat_metadata(cmd, "s")
        cleared.append("s:*")
    if cleared:
        log_info(
            "Cleared copied stream statistics metadata for processed output streams: "
            + ", ".join(cleared)
        )


def cpu_encoder_for_high_bit_depth(answers: dict[str, Any], video_encoder: str) -> tuple[str, str | None, str | None]:
    requested = str(answers.get("video_codec") or DEFAULT_VIDEO_CODEC).lower()
    info = VIDEO_CODEC_ALIASES.get(requested)
    if info and info.get("cpu"):
        cpu_encoder = str(info["cpu"])
        tag = info.get("tag")
        profile = info.get("profile")
    elif "hevc" in str(video_encoder) or "h265" in requested:
        cpu_encoder, tag, profile = "libx265", "hvc1", NVENC_HEVC_PROFILE
    elif "av1" in str(video_encoder) or requested == "av1":
        cpu_encoder, tag, profile = "libsvtav1", None, None
    else:
        cpu_encoder, tag, profile = "libx265", "hvc1", NVENC_HEVC_PROFILE
    if cpu_encoder == "libx264" and output_video_bit_depth(answers) > 10:
        appio.note("H.264/NVENC cannot safely preserve source bit depth above 10-bit here. H.265 CPU encoding was selected to preserve high bit depth.")
        cpu_encoder, tag, profile = "libx265", "hvc1", NVENC_HEVC_PROFILE
        answers["video_codec"] = "H265"
    return cpu_encoder, tag, profile


def append_embedded_attachment_maps(cmd: list[str], answers: dict[str, Any]) -> bool:
    if not answers.get("keep_embedded_attachments"):
        return False
    streams = embedded_attachment_streams(answers)
    if not streams:
        return False
    if not output_supports_embedded_attachments(answers):
        log_info(
            "Embedded attachments were requested but not mapped because the output "
            f"container does not support MKV attachment streams reliably: {answers.get('output_ext')}"
        )
        return False
    cmd.extend(["-map", "0:t?"])
    log_info(f"Embedded attachment streams mapped for copy: count={len(streams)}")
    return True


def append_source_data_maps(cmd: list[str], answers: dict[str, Any]) -> bool:
    streams = source_data_streams(answers)
    if not streams:
        return False
    if not source_data_keep_enabled(answers):
        return False
    for index, _stream in enumerate(streams):
        cmd.extend(["-map", f"0:d:{index}"])
    log_info(f"Source data streams mapped for copy: count={len(streams)}")
    return True


def can_map_additional_source_video_streams(answers: dict[str, Any]) -> bool:
    if not additional_source_video_streams(answers) or not source_extra_video_keep_enabled(answers):
        return False
    if answers.get("separator_points") or video_speed_transform_enabled(answers):
        log_info("Additional source video streams were not mapped because final timeline Split/speed processing is active.")
        return False
    if answers.get("cut_keep_ranges"):
        log_info("Additional source video streams were not mapped because frame-accurate cuts are active.")
        return False
    return True


def build_loudnorm_filter(answers: dict[str, Any]) -> str:
    target_i = float(answers.get("loudnorm_target_i", LOUDNORM_DEFAULT_TARGET_I))
    measured = answers.get("loudnorm_measured") if isinstance(answers.get("loudnorm_measured"), dict) else {}
    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if measured and all(measured.get(key) not in {None, ""} for key in required):
        # Two-pass: inject the Pass-1 measured values and request linear
        # normalization. FFmpeg's loudnorm applies a single fixed gain in
        # linear mode and AUTOMATICALLY falls back to dynamic internally if that
        # gain would exceed the true-peak ceiling, so linear=true is safe.
        measured_i = float(measured["input_i"])
        measured_tp = float(measured["input_tp"])
        required_gain = target_i - measured_i
        predicted_tp = measured_tp + required_gain
        target_tp = float(LOUDNORM_TARGET_TP)
        log_info(
            "LoudNorm mode: Two-pass (measured); linear=true; "
            f"target_i={target_i:g}; measured_I={measured_i:g}; measured_TP={measured_tp:g}; "
            f"gain={required_gain:+.2f} dB; predicted_TP={predicted_tp:.2f} dBTP; "
            f"target_TP={target_tp:g} dBTP "
            f"(FFmpeg falls back to dynamic internally if the linear gain would exceed the TP ceiling)."
        )
        return (
            "loudnorm="
            f"I={loudnorm_number(target_i)}:"
            f"TP={loudnorm_number(LOUDNORM_TARGET_TP)}:"
            f"LRA={loudnorm_number(LOUDNORM_TARGET_LRA)}:"
            f"measured_I={loudnorm_number(measured['input_i'])}:"
            f"measured_TP={loudnorm_number(measured['input_tp'])}:"
            f"measured_LRA={loudnorm_number(measured['input_lra'])}:"
            f"measured_thresh={loudnorm_number(measured['input_thresh'])}:"
            f"offset={loudnorm_number(measured['target_offset'])}:"
            f"linear=true:print_format=summary"
        )
    log_info(f"Using single-pass loudnorm filter because measured values are unavailable: target_i={target_i:g}")
    return (
        "loudnorm="
        f"I={loudnorm_number(target_i)}:"
        f"TP={loudnorm_number(LOUDNORM_TARGET_TP)}:"
        f"LRA={loudnorm_number(LOUDNORM_TARGET_LRA)}:"
        "print_format=summary"
    )


def _apply_tk_window_icon(root: Any) -> None:
    """Set a project-local icon on Tk roots for window chrome/taskbar."""
    png_path = asset_path(ICON_DIR_NAME, "ffmwiz_app.png")
    ico_path = asset_path(ICON_DIR_NAME, "ffmwiz_app.ico")
    try:
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FFmWiz.GUI")
                log_debug("Tk Windows AppUserModelID set: FFmWiz.GUI")
            except Exception as exc:
                log_debug(f"Could not set Tk AppUserModelID: {exc}")
        if ico_path.exists() and os.name == "nt":
            try:
                root.iconbitmap(default=str(ico_path))
                log_debug(f"Tk window iconbitmap set: {ico_path}")
            except Exception as exc:
                log_debug(f"Tk iconbitmap failed for {ico_path}: {exc}")
        if png_path.exists():
            import tkinter as tk
            icon = tk.PhotoImage(file=str(png_path), master=root)
            root.iconphoto(True, icon)
            root._ffmwiz_icon_ref = icon
            log_debug(f"Tk window iconphoto set: {png_path}")
        elif not ico_path.exists():
            log_debug("No Tk app icon asset found (ffmwiz_app.png/.ico).")
    except Exception as exc:
        log_debug(f"Could not set Tk window icon: {exc}")


def _progress_colorize(text: str, color: str, enabled: bool) -> str:
    return paint(text, color) if enabled else text


def startup_line(label: str, message: str, label_color: str, message_color: str = Color.WHITE) -> None:
    print(f"{paint(label + ':', label_color)} {paint(message, message_color)}")


def ensure_color_range_resolved(answers: dict[str, Any], workflow: str | None = None) -> None:
    """Production-entry guard: before generating a re-encode command in a UI
    workflow, confirm the color range is explicitly resolved (detected, user/
    batch assumption, or unspecified) rather than a silent compatibility
    fallback. No-op for stream-copy or audio-only outputs."""
    if not output_has_video(answers):
        return
    if str(resolve_video_encoder(answers)[0]).lower() == "copy":
        return
    resolved, source = resolve_color_range(
        answers, allow_compatibility_fallback=False, workflow=workflow
    )
    log_info(
        "Color range entry check: workflow=%s; resolved=%s; resolution_source=%s"
        % (workflow or "unknown", resolved or "unspecified", source)
    )


def load_capability_cache() -> dict[str, Any]:
    """Load the capability cache, rebuilding safely on corruption or schema drift."""
    path = capability_cache_path()
    empty = {"schema_version": CAPABILITY_CACHE_SCHEMA_VERSION, "environments": {}}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("environments"), dict):
            raise ValueError("unexpected cache structure")
        if data.get("schema_version") != CAPABILITY_CACHE_SCHEMA_VERSION:
            log_info("FFmpeg capability cache schema changed; starting a fresh cache.")
            return empty
        return data
    except Exception as exc:
        log_warn("FFmpeg capability cache is unreadable (%s); rebuilding. File: %s" % (exc, path))
        try:
            shutil.move(str(path), str(path.with_suffix(".corrupt")))
        except Exception:
            pass
        return empty


def save_capability_cache(data: dict[str, Any]) -> bool:
    """Atomically write the capability cache (temp file + flush + replace)."""
    path = capability_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return True
    except Exception as exc:
        log_warn("Could not write FFmpeg capability cache: %s" % exc)
        return False


def log_crop_normalization_summary(answers: dict[str, Any]) -> None:
    """Log the crop normalization decision once before command generation."""
    try:
        lines = crop_normalization_summary_lines(answers)
    except ValueError:
        # Validation errors are surfaced later by the command builders.
        return
    for line in lines:
        log_info(f"Crop normalization | {line}")


def set_crop_margins_if_valid(
    answers: dict[str, Any],
    top: int,
    left: int,
    right: int,
    bottom: int,
) -> bool:
    message = crop_margins_validation_message(answers, top, left, right, bottom)
    if message:
        appio.error(message)
        log_warn(message)
        return False
    answers["crop_enabled"] = any((top, left, right, bottom))
    answers["crop_top"] = top
    answers["crop_left"] = left
    answers["crop_right"] = right
    answers["crop_bottom"] = bottom
    return True


def set_single_crop_margin_if_valid(answers: dict[str, Any], key: str, value: int) -> bool:
    old_marker = object()
    old_value = answers.get(key, old_marker)
    answers[key] = value
    top = int(answers.get("crop_top", 0) or 0)
    left = int(answers.get("crop_left", 0) or 0)
    right = int(answers.get("crop_right", 0) or 0)
    bottom = int(answers.get("crop_bottom", 0) or 0)
    message = crop_margins_validation_message(answers, top, left, right, bottom)
    if not message:
        return True
    if old_value is old_marker:
        answers.pop(key, None)
    else:
        answers[key] = old_value
    appio.error(message)
    log_warn(message)
    return False


def calculate_scale_dimensions(answers: dict[str, Any], resolution: Any) -> tuple[tuple[int, int] | None, str]:
    if resolution is None or resolution == "n":
        return None, ""

    crop_w, crop_h = cropped_source_size(answers)
    answers["crop_box_dimensions"] = (crop_w, crop_h)
    answers["cropped_aspect_ratio"] = crop_w / max(1, crop_h)

    # Use display dimensions (accounting for SAR) for AR-preserving modes.
    # Output pixels are square (setsar=1), so scaled dimensions must reflect
    # the display aspect ratio, not the coded pixel grid.
    sar = source_sar(answers)
    disp_w, disp_h = cropped_display_size(answers)

    warning_parts: list[str] = []
    axis = ""
    if isinstance(resolution, dict):
        mode = resolution.get("mode")
        if mode == "preset":
            # Preset mode: compute AR-preserving dimensions that fit within
            # the preset box. The scale+pad filter handles final canvas.
            width, height, axis = closest_edge_scale_dimensions(
                disp_w,
                disp_h,
                int(resolution.get("width", disp_w) or disp_w),
                int(resolution.get("height", disp_h) or disp_h),
            )
        elif mode == "box":
            # Box mode: target the exact requested canvas dimensions.
            # The scale filter uses force_original_aspect_ratio=decrease to
            # fit the content, then pad fills the canvas. This ensures the
            # output is exactly the requested size without distortion.
            width = even_dimension(int(resolution.get("width", disp_w) or disp_w))
            height = even_dimension(int(resolution.get("height", disp_h) or disp_h))
            axis = "box"
        elif mode == "height":
            height = even_dimension(resolution.get("height", disp_h))
            width = even_dimension(height * disp_w / max(1, disp_h))
            axis = "height"
        elif mode == "width":
            width = even_dimension(resolution.get("width", disp_w))
            height = even_dimension(width * disp_h / max(1, disp_w))
            axis = "width"
        elif mode == "exact_stretch":
            requested_w = int(resolution.get("width", crop_w) or crop_w)
            requested_h = int(resolution.get("height", crop_h) or crop_h)
            width = even_dimension(requested_w)
            height = even_dimension(requested_h)
            axis = "stretch"
            if (width, height) != (requested_w, requested_h):
                warning_parts.append(
                    f"exact stretch resolution adjusted to codec-safe even dimensions: {width}x{height}"
                )
            crop_ar = disp_w / max(1, disp_h)
            out_ar = width / max(1, height)
            if abs(crop_ar - out_ar) / max(crop_ar, 1e-9) > 0.01:
                warning_parts.append(
                    "exact stretch output differs from the cropped aspect ratio and will stretch the image"
                )
        else:
            raise ValueError(f"Unknown resolution mode: {mode!r}")
    elif isinstance(resolution, tuple) and len(resolution) == 2:
        # Backward compatibility for older in-memory callers.
        width = even_dimension(resolution[0])
        height = even_dimension(resolution[1])
        axis = "stretch"
    else:
        raise ValueError(f"Invalid resolution value: {resolution!r}")

    answers["resolution_scale_axis"] = axis
    answers["final_resolution"] = (width, height)
    log_info(
        "Resolution calculation: "
        f"source={first_video_size(answers)}; SAR={sar:.4f}; "
        f"crop_margins={format_crop_margins(answers)}; "
        f"cropped_coded={crop_w}x{crop_h}; cropped_display={disp_w}x{disp_h}; "
        f"mode={resolution}; axis={axis}; "
        f"final={width}x{height}; exact_stretch={'yes' if axis == 'stretch' else 'no'}"
    )
    return (width, height), "; ".join(warning_parts)


def yn_prompt(title: str, default: bool) -> str:
    """Build a colored yes/no sub-prompt that matches the standard wizard style
    (bold title, HINT_YELLOW '(y/n)', green default, back/quit hint), for
    standalone confirmations that do not go through question_prompt."""
    default_text = "y" if default else "n"
    return (
        f"\n{paint(title, Color.BOLD)} "
        f"({paint('y/n', Color.HINT_YELLOW)}) "
        f"{paint('[' + default_text + ']', Color.GREEN)} "
        f"{back_text('back=0, quit=exit')}: "
    )


def selection_menu_line(number: int, label: str) -> str:
    """A numbered menu option line consistent with the rest of the app: a
    sky-blue 'N.' key followed by a bold label."""
    return f"  {paint(str(number) + '.', Color.LIGHT_BLUE)} {paint(label, Color.BOLD)}"


def fail(message: str) -> None:
    appio.error(f"\nERROR: {message}")
    sys.exit(1)


def _run_dependency_install(cmd: list[str], label: str) -> bool:
    print()
    appio.note("Running: " + " ".join(cmd))
    log_info(f"Dependency install command for {label}: {cmd}")
    try:
        result = subprocess.run(cmd, check=False)
    except Exception as exc:
        log_exception(f"Could not start dependency installer for {label}")
        appio.error(f"Could not start {label} installer. See log file: {_log_file_text()}")
        return False
    if result.returncode != 0:
        log_error(f"Dependency installer for {label} exited with code {result.returncode}")
        return False
    return True


def log_ffprobe_diagnostics(
    input_path: Path,
    ffprobe: str,
    args: list[str],
    return_code: int | str | None,
    stdout_text: str,
    stderr_text: str,
    decoded_using: str,
    exc: BaseException | None = None,
) -> None:
    replacement_char = chr(0xFFFD)
    log_error("ffprobe diagnostic block begin")
    log_error(f"  input path: {input_path}")
    log_error(f"  normalized path: {_safe_resolved_path(input_path)}")
    log_error(f"  path exists: {_path_exists_text(input_path)}")
    log_error(f"  ffprobe executable path: {ffprobe}")
    log_error(f"  ffprobe command arguments: {json.dumps(args, ensure_ascii=False)}")
    log_error(f"  return code: {return_code}")
    log_error(f"  decoded using: {decoded_using}")
    log_error(f"  stdout length: {len(stdout_text)}")
    log_error(f"  stderr length: {len(stderr_text)}")
    log_error(f"  stdout replacement characters: {'yes' if replacement_char in stdout_text else 'no'}")
    log_error(f"  stderr replacement characters: {'yes' if replacement_char in stderr_text else 'no'}")
    log_error("  stdout preview:\n" + _text_preview(stdout_text))
    log_error("  stderr preview:\n" + _text_preview(stderr_text))
    if exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log_error("  exception traceback:\n" + tb.rstrip())
    log_error("ffprobe diagnostic block end")


def ffprobe_text_overview(ffprobe: str, input_path: Path) -> str:
    args = [ffprobe, "-hide_banner", str(input_path)]
    log_debug(f"Media Info ffprobe text command: {json.dumps(args, ensure_ascii=False)}")
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout_text, _ = decode_subprocess_bytes(result.stdout, "utf-8")
        stderr_text, _ = decode_subprocess_bytes(result.stderr, "utf-8")
        overview = (stderr_text.strip() or stdout_text.strip() or "(no ffprobe text overview)")
        log_debug(
            f"Media Info ffprobe text overview returncode={result.returncode}; "
            f"length={len(overview)}"
        )
        return overview
    except Exception:
        log_exception(f"Media Info ffprobe text overview failed for {input_path}")
        return "(ffprobe text overview failed; see log file)"


def format_integrated_loudness_line(stats: dict[str, float]) -> str:
    """Build the 'Integrated loudness' summary line. The label is emphasized
    (bold + a distinct emerald green) while the measured value keeps its
    original MEAN_VOLUME color, so the actionable value stands out without
    recoloring the number itself."""
    label = paint("Integrated loudness:", Color.BOLD + Color.MUX_EMERALD)
    value = paint(f"{stats['input_i']:.1f} LUFS", Color.MEAN_VOLUME)
    return "  " + label + " " + value


def audio_hash_segment(
    ffmpeg: str,
    input_path: Path,
    stream_index: int,
    start: float,
    sample_seconds: float,
) -> str | None:
    args = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(input_path),
        "-map",
        f"0:{stream_index}",
        "-t",
        f"{sample_seconds:.3f}",
        "-vn",
        "-sn",
        "-dn",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        "-f",
        "hash",
        "-hash",
        "md5",
        "-",
    ]
    result = subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        log_warn(f"Could not hash audio stream #{stream_index}: {result.stderr.strip()}")
        return None
    match = re.search(r"MD5=([0-9a-fA-F]+)", result.stdout)
    return match.group(1).lower() if match else result.stdout.strip().lower() or None


def audio_hash_full(ffmpeg: str, input_path: Path, stream_index: int) -> str | None:
    args = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(input_path),
        "-map",
        f"0:{stream_index}",
        "-vn",
        "-sn",
        "-dn",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        "-f",
        "hash",
        "-hash",
        "md5",
        "-",
    ]
    result = subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        log_warn(f"Could not full-hash audio stream #{stream_index}: {result.stderr.strip()}")
        return None
    match = re.search(r"MD5=([0-9a-fA-F]+)", result.stdout)
    return match.group(1).lower() if match else result.stdout.strip().lower() or None


def duplicate_labels(index: int, report: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    empty_tracks: set[int] = report.get("empty_tracks", set())
    near_empty_tracks: set[int] = report.get("near_empty_tracks", set())
    if index in empty_tracks:
        labels.append(paint("EMPTY", Color.RED))
    elif index in near_empty_tracks:
        labels.append(paint("NEAR-EMPTY", Color.NEAR_EMPTY))

    confirmed = [pair for pair in report.get("confirmed_pairs", []) if index in pair]
    possible = [pair for pair in report.get("possible_pairs", []) if index in pair and pair not in report.get("confirmed_pairs", [])]
    if confirmed:
        peers = sorted({other for pair in confirmed for other in pair if other != index})
        labels.append(paint(f"CONFIRMED duplicate of {','.join(map(str, peers))}", Color.RED))
    if possible:
        peers = sorted({other for pair in possible for other in pair if other != index})
        labels.append(paint(f"POSSIBLE duplicate of {','.join(map(str, peers))}", Color.YELLOW))
    return labels


def run_media_info_text_command(args: list[str], label: str) -> tuple[int, str, str]:
    log_debug(f"{label} command: {json.dumps(args, ensure_ascii=False)}")
    result = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    stdout_text, stdout_encoding = decode_subprocess_bytes(result.stdout, "utf-8")
    stderr_text, stderr_encoding = decode_subprocess_bytes(result.stderr, "utf-8")
    log_debug(
        f"{label} returncode={result.returncode}; "
        f"stdout_encoding={stdout_encoding}; stderr_encoding={stderr_encoding}; "
        f"stdout_len={len(stdout_text)}; stderr_len={len(stderr_text)}"
    )
    if stderr_text.strip():
        log_debug(f"{label} stderr:\n{stderr_text.rstrip()}")
    return result.returncode, stdout_text, stderr_text


def render_info_report(lines: list[tuple[str, str]], color: bool = True) -> str:
    rendered: list[str] = []
    for text, color_code in lines:
        rendered.append(paint(text, color_code) if color and text else text)
    return "\n".join(rendered)


def ffmpeg_filter_available(ffmpeg: str, filter_name: str) -> bool:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        text, _ = decode_subprocess_bytes(result.stdout, "utf-8")
        return filter_name in text
    except Exception:
        log_exception(f"Could not inspect FFmpeg filters for {filter_name}")
        return False


def media_info_next_prompt(
    answers: dict[str, Any],
    title: str,
    details: str | None = None,
    default: str | None = None,
    back: str = "back=0, quit=exit",
) -> str:
    current = int(answers.get("_question_number", 0) or 0)
    if current < 1:
        current = 1
    answers["_question_number"] = current
    prompt = appio.question_prompt(answers, title, details, default, back)
    answers["_question_number"] = current + 1
    return prompt


__all__ = [
    'option_list',
    'example_text',
    'keep_value_text',
    'suggestion_text',
    'colored_audio_track_hint',
    'colored_hardsub_quality_options',
    'colored_hardsub_audio_options',
    'field_text',
    'append_final_split_filters',
    'set_nvenc_multipass_skip_reason',
    'step_folder_batch_color_range',
    'apply_folder_batch_color_range',
    'append_audio_encode_options',
    'append_source_metadata_chapter_options',
    'append_clear_reencoded_stream_stat_metadata',
    'cpu_encoder_for_high_bit_depth',
    'append_embedded_attachment_maps',
    'append_source_data_maps',
    'can_map_additional_source_video_streams',
    'build_loudnorm_filter',
    '_apply_tk_window_icon',
    '_progress_colorize',
    'startup_line',
    'ensure_color_range_resolved',
    'load_capability_cache',
    'save_capability_cache',
    'log_crop_normalization_summary',
    'set_crop_margins_if_valid',
    'set_single_crop_margin_if_valid',
    'calculate_scale_dimensions',
    'yn_prompt',
    'selection_menu_line',
    'fail',
    '_run_dependency_install',
    'log_ffprobe_diagnostics',
    'ffprobe_text_overview',
    'format_integrated_loudness_line',
    'audio_hash_segment',
    'audio_hash_full',
    'duplicate_labels',
    'run_media_info_text_command',
    'render_info_report',
    'ffmpeg_filter_available',
    'media_info_next_prompt',
]


# ext00b holds the second half of this tier (split for file size). Re-export its
# names here so every consumer of `from ffmwiz.support.ext00 import *` keeps
# seeing the full tier. ext00b imports ext00 at its top; this import runs after
# ext00's own defs and __all__, so the cycle resolves cleanly.
from ffmwiz.support import ext00b as _ext00b  # noqa: E402
from ffmwiz.support.ext00b import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_ext00b.__all__)
