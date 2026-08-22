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
]



# ext00b holds the second half of this tier (split for file size). Re-export its
# names here so every consumer of `from ffmwiz.support.ext00 import *` keeps
# seeing the full tier. ext00b imports ext00 at its top; this import runs after
# ext00's own defs and __all__, so the cycle resolves cleanly.
from ffmwiz.support import ext00b as _ext00b  # noqa: E402
# When ext00b is imported FIRST (`import ffmwiz.support.ext00b`) it re-enters
# here while it is still only partially initialised, so it has no __all__ yet
# and the star-import below would raise AttributeError. Importing the parent
# first is the normal path and is unaffected; this guard just makes the direct
# import work too, and ext00b re-exports the tier itself in that case.
_ext00b_names = list(getattr(_ext00b, "__all__", []))
if _ext00b_names:
    from ffmwiz.support.ext00b import *  # noqa: E402,F401,F403
    __all__ = list(__all__) + _ext00b_names


# ext00c holds an overflow slice of this module (split for file size).
from ffmwiz.support import ext00c as _ext00c  # noqa: E402
# Guard the direct-import case: importing this overflow module FIRST
# re-enters the parent while the child has no __all__ yet.
_ext00c_names = list(getattr(_ext00c, "__all__", []))
if _ext00c_names:
    from ffmwiz.support.ext00c import *  # noqa: E402,F401,F403
    __all__ = list(__all__) + _ext00c_names
