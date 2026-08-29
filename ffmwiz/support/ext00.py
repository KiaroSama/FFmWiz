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
    if audio_codec == "copy":
        appio.note("Audio copy cannot be used after Split/filter processing. AAC was selected for audio.")
        audio_codec = DEFAULT_AUDIO_CODEC
    # `answers` here is often a shallow copy the caller made, so a write to the
    # requested key would never reach the dict the summary reads -- and the
    # requested key is the wrong place anyway, because Back has to show the
    # user their own answer. The effective map is both visible through a copy
    # and the right home for a resolution (R10/D15).
    effective_settings(answers)["audio_codec"] = audio_codec
    cmd.extend(["-c:a", audio_codec])
    audio_bitrate = answers.get("audio_bitrate_kbps")
    if audio_bitrate and audio_codec_uses_bitrate(str(audio_codec)):
        cmd.extend(["-b:a", f"{audio_bitrate}k"])
    channels = resolve_audio_channels(answers)
    if channels:
        cmd.extend(["-ac", str(channels)])
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
        effective_settings(answers)["video_codec"] = "H265"
    return cpu_encoder, tag, profile


def append_embedded_attachment_maps(cmd: list[str], answers: dict[str, Any]) -> bool:
    """Map the source's attachment streams. Call this AFTER every other map.

    Matroska will not accept a packet-bearing stream at a higher output index
    than an attachment, and `-map` order IS output-index order, so an
    attachment mapped early poisons every map that follows it -- including the
    filter-complex audio outputs, which a builder appends much later.
    Callers must therefore treat this as the last map they emit.
    """
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


def additional_source_video_drop_reason(answers: dict[str, Any]) -> str:
    """Why this command cannot carry the extra source video streams ("" = it can).

    They are stream-copied on the SOURCE timeline, so anything that rebuilds
    that timeline leaves them unsynchronized and FFmWiz removes them. Returning
    the reason instead of only logging it is what lets the question and the
    summary state the exact outcome rather than keep promising "keep" (R11).
    """
    if answers.get("join_input_items"):
        return "the join graph produces a single video stream"
    if answers.get("separator_points"):
        return "the final output is split into parts"
    if video_speed_transform_enabled(answers) or answers.get("reverse_video"):
        return "a speed/reverse change rebuilds the video timeline"
    if answers.get("cut_keep_ranges"):
        return "frame-accurate cuts rebuild the video timeline"
    return ""


def resolve_source_extra_video_keep(answers: dict[str, Any]) -> bool:
    """Whether the extra source video streams SURVIVE this command, recorded.

    The single decider, so the command, the summary and the resolved state can
    never disagree. It used to be decided in the builder and nowhere else: the
    keep flag and the summary went on promising streams the command removed.
    The requested value is left alone -- Back and reopen must still show the
    user their own answer.
    """
    if not additional_source_video_streams(answers):
        return False
    keep = bool(effective_value(
        answers, "keep_source_extra_video_streams", source_extra_video_keep_enabled(answers)))
    if keep and additional_source_video_drop_reason(answers):
        keep = False
    if not keep:
        effective_settings(answers)["keep_source_extra_video_streams"] = False
    return keep


def can_map_additional_source_video_streams(answers: dict[str, Any]) -> bool:
    if resolve_source_extra_video_keep(answers):
        return True
    reason = additional_source_video_drop_reason(answers)
    if reason and source_extra_video_keep_enabled(answers) and additional_source_video_streams(answers):
        log_info(f"Additional source video streams were not mapped because {reason}.")
    return False


def source_extra_stream_outcome_notes(answers: dict[str, Any]) -> list[str]:
    """One line per "keep source extras" promise this timeline cannot honour.

    Pure: the wizard calls it from a step predicate as well as from the prompt,
    so it must not write anything.
    """
    notes: list[str] = []
    extra = additional_source_video_streams(answers)
    if extra and source_extra_video_keep_enabled(answers):
        reason = additional_source_video_drop_reason(answers)
        if reason:
            notes.append(
                f"Extra source video streams: all {len(extra)} of them are REMOVED from the "
                f"output because {reason}."
            )
    data = source_data_streams(answers)
    if data and source_data_keep_enabled(answers) and timeline_is_modified(answers):
        notes.append(
            f"Source data streams: all {len(data)} of them are copied unchanged, so their "
            "timestamps still describe the SOURCE timeline and will not line up with the "
            "processed one."
        )
    return notes


def confirm_source_extra_stream_outcomes(answers: dict[str, Any]) -> None:
    """State what "keep source extras" really does here, then take a yes/no.

    Asked BEFORE the command is generated so the answer can still change the
    edit; `n` goes back to the previous question instead of explaining the loss
    after the fact.
    """
    notes = source_extra_stream_outcome_notes(answers)
    if not notes:
        return
    # Record it where a shallow answers copy cannot hide it, so the summary and
    # the command agree even on the build paths that never ask.
    resolve_source_extra_video_keep(answers)
    print()
    appio.note("Keep source extras: this timeline cannot preserve every stream you asked to keep.")
    for note in notes:
        print("  " + paint(note, Color.ORANGE))
    log_info("Source extra stream outcomes disclosed: " + " | ".join(notes))
    keep_going = appio.ask_yes_no(
        appio.question_prompt(
            answers,
            "Continue with that outcome?",
            "y/n; n goes back so you can drop the speed/cuts/Split instead",
            "y",
        ),
        True,
    )
    if not keep_going:
        raise Back()


def item_audio_streams(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Every audio stream of one join input, in file order.

    Mirrors `item_subtitle_streams`: some paths assemble a join item without an
    `audio_streams` key -- its audio only appears inside `streams` -- and
    reading the key alone made such an input look silent.
    """
    streams = item.get("audio_streams")
    if streams is None:
        streams = [stream for stream in (item.get("streams") or [])
                   if str(stream.get("codec_type") or "").lower() == "audio"]
    return list(streams or [])


def _format_duration_seconds(fmt: dict[str, Any] | None) -> float:
    try:
        return float((fmt or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def join_items_from_answers(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """The COMPLETE join item list, input 1 rebuilt from the top-level answers.

    `answers["join_input_items"]` holds inputs 2..N only; input 1 lives in the
    ordinary answer keys, so anything that needs the whole list has to
    reassemble it. Doing that by hand in more than one place is how input 1's
    `subtitle_streams` came to be missing from one of the copies (R04).

    Returns [] when this is not a join.
    """
    extra = list(answers.get("join_input_items") or [])
    if not extra:
        return []
    primary = {
        "path": answers["input_path"],
        "probe": answers.get("probe") or {},
        "format": answers.get("format") or {},
        "streams": (
            list(answers.get("video_streams") or [])
            + list(answers.get("audio_streams") or [])
            + list(answers.get("subtitle_streams") or [])
            + list(answers.get("attachment_streams") or [])
            + list(answers.get("data_streams") or [])
        ),
        "video_streams": answers.get("video_streams") or [],
        "audio_streams": answers.get("audio_streams") or [],
        "subtitle_streams": answers.get("subtitle_streams") or [],
        "data_streams": answers.get("data_streams") or [],
        # The container duration, which stays the answer only for an audio-only
        # join item: with no picture there is nothing else to measure.
        "duration": _format_duration_seconds(answers.get("format")),
    }
    # Every other item's `duration` is its PICTURE span, because that is what `concat`
    # actually splices and what every offset downstream measures. `format.duration`
    # is the container's, and a trailing subtitle or audio pad inflates it: a
    # 2.000 s picture in a 3.000 s MKV pushed the next input a second late (B07).
    # Copied, never written back -- the caller's stored items stay untouched.
    return [{**item, "duration": join_item_picture_span(item)}
            for item in (primary, *extra)]


def join_audio_segment_flags(answers: dict[str, Any]) -> list[bool]:
    """Audio presence per joined input, input 1 first."""
    flags = [bool(answers.get("audio_streams"))]
    flags.extend(bool(item_audio_streams(item)) for item in (answers.get("join_input_items") or []))
    return flags


def join_audio_track_count(items: list[dict[str, Any]]) -> int:
    """How many LOGICAL audio tracks the joined set offers.

    Taken across ALL inputs, not input 1. Sizing the track question from input
    1 is what made a track only a later input carries impossible to select, and
    then reported it as "NOT in the joined output" (F05).
    """
    return max((len(item_audio_streams(item)) for item in items), default=0)


def join_audio_streams_view(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """One representative stream per LOGICAL joined audio track.

    The exact analogue of `join_subtitle_streams_view`. Track i is described by
    the FIRST input that actually has an i-th audio stream, so the track,
    codec, bitrate, sample-rate and LoudNorm questions describe the stream the
    joined output really carries instead of input 1's list alone.
    """
    items = answers.get("join_input_items") or []
    if not items:
        return list(answers.get("audio_streams") or [])
    all_items = [{"audio_streams": list(answers.get("audio_streams") or [])}, *items]
    view: list[dict[str, Any]] = []
    for index in range(join_audio_track_count(all_items)):
        for item in all_items:
            streams = item_audio_streams(item)
            if index < len(streams):
                view.append(streams[index])
                break
    return view


def any_join_audio(answers: dict[str, Any]) -> bool:
    """True when ANY input carries audio.

    Every audio feature gate used to read input 1's stream list alone, so a
    silent first input hid the speed-sync, codec, sample-rate and LoudNorm
    questions for a join whose later inputs are audible -- the joined audio then
    played at 1x under a 2x video and outlived it by its whole length (R02).
    """
    return bool(join_audio_streams_view(answers))


def join_audio_selection(answers: dict[str, Any],
                         items: list[dict[str, Any]] | None = None) -> tuple[str, list[int]]:
    """The audio-track answer as an explicit STATE, not a truthiness guess.

    * `"unasked"` -- the key was never created. The track question is gated on
      the primary input having audio, so an absent key is the ONLY case in
      which a join may recover a later input's track.
    * `"none"` -- the user answered with an empty selection. That is an
      authoritative video-only request; the silent-first recovery used to
      overwrite it because an empty list and a missing key both looked falsy
      (F04). `selected_join_subtitle_tracks` already drew this line.
    * `"all"` / `"indices"` -- normalised to a LOGICAL index list, so a
      selection can be compared as a SET against the complete set instead of by
      representation (`[0]` vs `"all"`, which is what F06 got wrong).
    """
    if "audio_tracks" not in answers:
        return "unasked", []
    selected = answers.get("audio_tracks")
    count = (join_audio_track_count(items) if items is not None
             else len(join_audio_streams_view(answers)))
    if selected == "all":
        return "all", list(range(count))
    if not selected:
        return "none", []
    indices: list[int] = []
    for value in selected:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= index < count and index not in indices:
            indices.append(index)
    return "indices", sorted(indices)


def join_audio_recovery(answers: dict[str, Any]) -> tuple[list[dict[str, Any]], list[int]]:
    """The audio the joined output really carries when the question was never asked.

    Mirrors what `build_join_encode_command` does: track 0 of the first audible
    input, with silence synthesised for the inputs that lack it. Returns
    ([], []) when the user ANSWERED the track question -- an explicit answer,
    including an empty one, is authoritative (F04) -- and when input 1 has audio
    or no input does.
    """
    if "audio_tracks" in answers:
        return [], []
    if answers.get("audio_streams") or not answers.get("join_input_items"):
        return [], []
    for item in answers.get("join_input_items") or []:
        streams = item_audio_streams(item)
        if streams:
            return [streams[0]], [0]
    return [], []


def joined_audio_track_model(answers: dict[str, Any]) -> dict[str, Any]:
    """Every LOGICAL joined audio track, described BY ITS OWN CARRIERS.

    A joined audio track is not one stream. It is the i-th audio stream of each
    input in turn, spliced by `concat`, with synthesised silence for the inputs
    that do not have it. `join_audio_streams_view` lends one representative
    stream dict per track, but every cache that JUDGES a stream --
    `packet_sizes`, `audio_volume_stats`, `audio_duplicate_report` -- belongs to
    the PRIMARY file and is keyed by ABSOLUTE stream index, so a track borrowed
    from input 2 was measured against whatever input 1 happens to hold at that
    index. Measured: primary audio at absolute index 1, a German track only
    input 2 carries at absolute index 2, and input 1's absolute index 2 is a
    2-byte subtitle. The lent view was [1, 2] and
    `auto_select_audio_tracks(..., "de")` read the German track as 2 bytes
    instead of 46,964 and 1 kbps instead of 125, then dropped it as empty (B12).

    Returns {"view", "packet_sizes", "volume_stats", "report", "carriers",
    "signature"}. The view's stream dicts are COPIES renumbered to their
    LOGICAL position and carrying their own carrier's duration, so two inputs
    that both use absolute index 2 can no longer collide inside the lent caches.
    `carriers[i]` is [(input position, that input's path, its stream), ...] --
    the provenance itself, without a reference back to the answers dict the
    model is cached on.

    Program-level rules, applied over the carriers a track actually HAS -- an
    input that lacks the track contributes silence, not a carrier:

    * empty       -- every carrier is empty. One real carrier makes the track
                     real; a track whose FIRST input is silent is still audio.
    * near-empty  -- not empty, and every carrier is empty or near-empty.
    * duplicate   -- confirmed in every input that carries BOTH tracks, and at
                     least one does. Dropping a track as redundant is only safe
                     when it is redundant for the whole joined program.

    `detect_duplicate_audio=False` still classifies empty/near-empty per
    carrier -- that is what track selection needs -- but reports no pairs and
    hashes nothing.
    """
    extra = list(answers.get("join_input_items") or [])
    if not extra:
        return {"view": list(answers.get("audio_streams") or []), "packet_sizes": {},
                "volume_stats": {}, "report": {}, "carriers": {}, "signature": ""}
    signature = _audio_report_signature(answers)
    cached = answers.get("_joined_audio_track_model")
    if isinstance(cached, dict) and cached.get("signature") == signature:
        return cached

    # Lazy: duplicate/sparse detection and the probe caches all sit ABOVE this
    # tier, and `services` imports this module. Resolving them at call time also
    # keeps the monkeypatch seam the tests already use.
    from ffmwiz import services
    from ffmwiz.support import ext08, ext09

    detect = bool(answers.get("detect_duplicate_audio", True))
    # The primary carrier IS `answers`, so its probe caches are computed once
    # and stay where the rest of the wizard reads them.
    inputs: list[tuple[dict[str, Any], list[dict[str, Any]]]] = [
        (answers, list(answers.get("audio_streams") or []))]
    for item in extra:
        inputs.append((join_item_answers(answers, item), item_audio_streams(item)))

    reports: list[dict[str, Any]] = []
    for carrier, streams in inputs:
        if not streams:
            reports.append({})
        elif detect:
            reports.append(ext09.detect_duplicate_audio(carrier))
        else:
            empty, near = ext08.classify_sparse_audio_tracks(
                streams, carrier.get("format") or {}, services.get_packet_sizes(carrier))
            reports.append({"empty_tracks": empty, "near_empty_tracks": near,
                            "possible_pairs": [], "confirmed_pairs": []})

    count = max((len(streams) for _carrier, streams in inputs), default=0)
    view: list[dict[str, Any]] = []
    packet_sizes: dict[int, int] = {}
    volume_stats: dict[int, dict[str, str]] = {}
    carriers: dict[int, list[tuple[int, Any, dict[str, Any]]]] = {}
    empty_tracks: set[int] = set()
    near_empty_tracks: set[int] = set()
    for index in range(count):
        holders = [(position, carrier, streams[index])
                   for position, (carrier, streams) in enumerate(inputs)
                   if index < len(streams)]
        carriers[index] = [(position, carrier.get("input_path"), stream)
                           for position, carrier, stream in holders]
        _position, first_carrier, first_stream = holders[0]
        representative = dict(first_stream)
        representative["index"] = index
        duration = services.stream_duration_seconds(first_stream, first_carrier.get("format"))
        if duration:
            # Its OWN file's duration. Without it `stream_duration_seconds`
            # falls through to the primary's container and turns a correct byte
            # count back into a wrong bitrate.
            representative["duration"] = f"{float(duration):.6f}"
        view.append(representative)
        size = services.get_packet_sizes(first_carrier).get(first_stream.get("index"))
        if size is not None:
            packet_sizes[index] = size
        stats = (services.get_audio_volume_stats(first_carrier) or {}).get(index)
        if stats:
            volume_stats[index] = stats
        states = [(index in (reports[position].get("empty_tracks") or set()),
                   index in (reports[position].get("near_empty_tracks") or set()))
                  for position, _carrier, _stream in holders]
        if all(is_empty for is_empty, _is_near in states):
            empty_tracks.add(index)
        elif all(is_empty or is_near for is_empty, is_near in states):
            near_empty_tracks.add(index)

    confirmed: list[tuple[int, int]] = []
    possible: list[tuple[int, int]] = []
    for left in range(count):
        for right in range(left + 1, count):
            shared = [reports[position]
                      for position, (_carrier, streams) in enumerate(inputs)
                      if left < len(streams) and right < len(streams)]
            if not shared:
                continue
            confirmed_sets = [{tuple(pair) for pair in (report.get("confirmed_pairs") or [])}
                              for report in shared]
            possible_sets = [pairs | {tuple(pair) for pair in (report.get("possible_pairs") or [])}
                             for report, pairs in zip(shared, confirmed_sets)]
            if all((left, right) in pairs for pairs in confirmed_sets):
                confirmed.append((left, right))
            if all((left, right) in pairs for pairs in possible_sets):
                possible.append((left, right))

    model = {
        "view": view,
        "packet_sizes": packet_sizes,
        "volume_stats": volume_stats,
        "carriers": carriers,
        "report": {"possible_pairs": possible, "confirmed_pairs": confirmed,
                   "empty_tracks": empty_tracks, "near_empty_tracks": near_empty_tracks,
                   "hashes": {}, "sample_hashes": {}},
        "signature": signature,
    }
    answers["_joined_audio_track_model"] = model
    return model


def with_join_audio_view(step: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    """Run an audio step against the JOIN's audio instead of input 1's.

    The audio questions live behind `answers["audio_streams"]` -- input 1 alone
    -- so a join whose first input is silent lost LoudNorm and the codec/rate
    questions, and a track only a later input carries could not be reached at
    all. Lend the joined view to the step and take it straight back: nothing
    outside the step may see input 1 claiming a stream it does not have.

    The stream list alone was never enough. `packet_sizes`,
    `audio_volume_stats` and `audio_duplicate_report` are all keyed off the
    PRIMARY file, so lending only the streams left every borrowed track judged
    by unrelated primary data, and a real later-only track was auto-dropped as
    empty (B12). Lend the whole per-carrier model, and take all of it back.
    """

    def run(answers: dict[str, Any]) -> None:
        model = joined_audio_track_model(answers)
        view = model["view"]
        _streams, lent_tracks = join_audio_recovery(answers)
        if not lent_tracks and view == list(answers.get("audio_streams") or []):
            step(answers)
            return
        missing = object()
        lent = {
            "audio_streams": view,
            "packet_sizes": model["packet_sizes"],
            "audio_volume_stats": model["volume_stats"],
            "audio_duplicate_report": model["report"],
        }
        saved = {key: answers.get(key, missing) for key in (*lent, "audio_tracks")}
        answers.update(lent)
        if lent_tracks:
            answers["audio_tracks"] = lent_tracks
        try:
            step(answers)
        finally:
            for key in lent:
                if saved[key] is missing:
                    answers.pop(key, None)
                else:
                    answers[key] = saved[key]
            # The TRACK question writes audio_tracks itself. Taking the lend
            # back by position would throw the user's answer away, so only the
            # object that was actually lent is reclaimed.
            if lent_tracks and answers.get("audio_tracks") is lent_tracks:
                if saved["audio_tracks"] is missing:
                    answers.pop("audio_tracks", None)
                else:
                    answers["audio_tracks"] = saved["audio_tracks"]

    return run


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
    'additional_source_video_drop_reason',
    'resolve_source_extra_video_keep',
    'can_map_additional_source_video_streams',
    'source_extra_stream_outcome_notes',
    'confirm_source_extra_stream_outcomes',
    'item_audio_streams',
    'join_items_from_answers',
    'join_audio_segment_flags',
    'join_audio_track_count',
    'join_audio_streams_view',
    'any_join_audio',
    'join_audio_selection',
    'join_audio_recovery',
    'joined_audio_track_model',
    'with_join_audio_view',
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
from ffmwiz.support.ext00b import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_ext00b.__all__)


# ext00c holds an overflow slice of this module (split for file size).
from ffmwiz.support import ext00c as _ext00c  # noqa: E402
# Guard the direct-import case: importing this overflow module FIRST
# re-enters the parent while the child has no __all__ yet.
from ffmwiz.support.ext00c import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_ext00c.__all__)
