"""FFmWiz extracted helper tier ext8 (post-services layer).

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
from ffmwiz.support.ext05 import *  # noqa: F401,F403
from ffmwiz.support.ext06 import *  # noqa: F401,F403
from ffmwiz.support.ext07 import *  # noqa: F401,F403


def join_input_media_stats(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-file (name, video_kbps, audio_kbps, fps, duration, volume) for the
    ordered Join set, using the already-probed source data. Unknown values are
    stored as None. Volume extremes use only pre-computed analysis (cached in
    each item's 'audio_volume_stats'); the summary never triggers a new
    expensive volumedetect scan."""
    rows: list[dict[str, Any]] = []
    for item in join_ordered_items_for_answers(answers):
        item_answers = join_item_answers(answers, item)
        fmt = item.get("format") or {}
        packet_sizes = services.get_packet_sizes(item_answers)
        video_streams = item.get("video_streams") or []
        audio_streams = item.get("audio_streams") or []
        video_kbps = stream_bitrate_kbps(video_streams[0], fmt, packet_sizes) if video_streams else None
        audio_kbps = stream_bitrate_kbps(audio_streams[0], fmt, packet_sizes) if audio_streams else None
        fps = None
        if video_streams:
            fps = rational_to_float(video_streams[0].get("avg_frame_rate")) or rational_to_float(video_streams[0].get("r_frame_rate"))
        duration = item.get("duration")
        if not duration:
            duration = services.stream_duration_seconds({}, fmt)
        # Volume extremes: only from already-computed analysis, never re-probed.
        mean_db = max_db = None
        cached_volume = item.get("audio_volume_stats") or item_answers.get("audio_volume_stats")
        if isinstance(cached_volume, dict) and cached_volume:
            first = cached_volume.get(0) or next(iter(cached_volume.values()), None)
            if isinstance(first, dict):
                mean_db = _parse_db_value(first.get("mean_volume"))
                max_db = _parse_db_value(first.get("max_volume"))
        rows.append({
            "name": Path(item.get("path")).name,
            "video_kbps": int(video_kbps) if video_kbps else None,
            "audio_kbps": int(audio_kbps) if audio_kbps else None,
            "fps": float(fps) if fps else None,
            "duration": float(duration) if duration else None,
            "mean_volume_db": mean_db,
            "max_volume_db": max_db,
        })
    return rows


def possible_audio_duplicate(
    left: dict[str, Any],
    right: dict[str, Any],
    fmt: dict[str, Any],
    packet_sizes: dict[int, int],
) -> bool:
    if left.get("codec_name") != right.get("codec_name"):
        return False
    if stream_metadata_value(left, "sample_rate") != stream_metadata_value(right, "sample_rate"):
        return False
    if left.get("channels") != right.get("channels"):
        return False
    if not compatible_channel_layout(
        stream_metadata_value(left, "channel_layout"),
        stream_metadata_value(right, "channel_layout"),
    ):
        return False

    left_duration = services.stream_duration_seconds(left, fmt)
    right_duration = services.stream_duration_seconds(right, fmt)
    if left_duration is not None and right_duration is not None and abs(left_duration - right_duration) >= 0.5:
        return False

    left_size, _ = stream_size_bytes(left, fmt, packet_sizes)
    right_size, _ = stream_size_bytes(right, fmt, packet_sizes)
    if left_size is not None and right_size is not None:
        larger = max(left_size, right_size)
        if larger > 0 and abs(left_size - right_size) / larger >= 0.01:
            return False

    return close_enough_bitrate(
        stream_bitrate_kbps(left, fmt, packet_sizes),
        stream_bitrate_kbps(right, fmt, packet_sizes),
    )


def classify_sparse_audio_tracks(
    audio_streams: list[dict[str, Any]],
    fmt: dict[str, Any],
    packet_sizes: dict[int, int],
) -> tuple[set[int], set[int]]:
    sizes: list[int] = []
    stream_sizes: dict[int, int] = {}
    for idx, stream in enumerate(audio_streams):
        size, _ = stream_size_bytes(stream, fmt, packet_sizes)
        if size is None:
            continue
        stream_sizes[idx] = size
        sizes.append(size)

    typical_size = max(sizes) if sizes else None
    empty_tracks: set[int] = set()
    near_empty_tracks: set[int] = set()

    for idx, stream in enumerate(audio_streams):
        size = stream_sizes.get(idx)
        duration = services.stream_duration_seconds(stream, fmt)
        rate = stream_bitrate_kbps(stream, fmt, packet_sizes)
        long_enough = duration is None or duration >= 10

        if size is not None and size <= EMPTY_AUDIO_MAX_BYTES:
            empty_tracks.add(idx)
            continue

        much_smaller_than_peers = (
            size is not None
            and typical_size is not None
            and typical_size > NEAR_EMPTY_AUDIO_MAX_BYTES
            and size <= max(EMPTY_AUDIO_MAX_BYTES, round(typical_size * NEAR_EMPTY_AUDIO_RATIO))
        )
        very_low_bitrate = rate is not None and rate <= NEAR_EMPTY_AUDIO_MAX_KBPS and long_enough
        tiny_long_track = size is not None and size <= 64 * 1024 and long_enough

        if much_smaller_than_peers or very_low_bitrate or tiny_long_track:
            near_empty_tracks.add(idx)

    return empty_tracks, near_empty_tracks


def print_audio_duplicate_report(answers: dict[str, Any], report: dict[str, Any]) -> None:
    if not answers.get("audio_streams"):
        return
    print()
    print(paint("Audio duplicate report", Color.BOLD + Color.ORANGE))
    packet_sizes = services.get_packet_sizes(answers)
    fmt = answers.get("format", {})
    sibling_streams = streams_for_statistics_from_answers(answers)
    for idx, stream in enumerate(answers["audio_streams"]):
        size, _ = stream_size_bytes(stream, fmt, packet_sizes, sibling_streams)
        volume_stats = services.get_audio_volume_stats(answers)
        labels = duplicate_labels(idx, report)
        suffix = f" | {' | '.join(labels)}" if labels else ""
        print(
            f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
            f"{field_text('stream', '#' + str(stream.get('index')), Color.WHITE)} | "
            f"{field_text('codec', stream_metadata_value(stream, 'codec_name'), Color.CYAN)} | "
            f"{field_text('sample_rate', stream_metadata_value(stream, 'sample_rate'), Color.GREEN)} | "
            f"{field_text('channels', stream.get('channels', 'unknown'), Color.GREEN)} | "
            f"{field_text('layout', stream_metadata_value(stream, 'channel_layout'), Color.WHITE)} | "
            f"{field_text('bitrate', describe_bitrate(stream_bitrate_kbps(stream, fmt, packet_sizes, sibling_streams)), Color.YELLOW)} | "
            f"{field_text('mean / max volume', audio_mean_max_volume_field(volume_stats, idx), Color.MEAN_VOLUME)} | "
            f"{field_text('duration', format_duration(services.stream_duration_seconds(stream, fmt)), Color.MAGENTA)} | "
            f"{field_text('lang', display_language(stream_tag_value(stream, 'language')), Color.WHITE)} | "
            f"{field_text('title', stream_tag_value(stream, 'title'), Color.WHITE)} | "
            f"{field_text('size', format_bytes(size), Color.LIME)}{suffix}"
        )

    possible_pairs = report.get("possible_pairs", [])
    confirmed_pairs = report.get("confirmed_pairs", [])
    empty_tracks = sorted(report.get("empty_tracks", set()))
    near_empty_tracks = sorted(report.get("near_empty_tracks", set()))
    print("  " + field_text("empty tracks", empty_tracks or "none", Color.RED if empty_tracks else Color.GREEN))
    print("  " + field_text("near-empty tracks", near_empty_tracks or "none", Color.ORANGE if near_empty_tracks else Color.GREEN))
    print("  " + field_text("possible duplicate pairs", possible_pairs or "none", Color.YELLOW if possible_pairs else Color.GREEN))
    print("  " + field_text("confirmed duplicate pairs", confirmed_pairs or "none", Color.RED if confirmed_pairs else Color.GREEN))


def media_info_stream_size_rows(
    input_path: Path,
    payload: dict[str, Any],
    packet_sizes: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    total_size = input_path.stat().st_size if input_path.exists() else format_size_bytes_from_metadata(fmt)
    rows: list[dict[str, Any]] = []
    for stream in streams:
        tags = media_info_stream_tags(stream)
        size_bytes, estimated = stream_size_bytes(stream, fmt, packet_sizes, streams)
        bitrate = stream_bitrate_kbps(stream, fmt, packet_sizes, streams)
        percent = (size_bytes / total_size * 100.0) if size_bytes is not None and total_size else None
        rows.append({
            "stream_index": stream.get("index", "unknown"),
            "type": stream.get("codec_type", "unknown"),
            "codec": stream.get("codec_name", "unknown"),
            "language": display_language(tags.get("language")),
            "title": tags.get("title") or "unknown",
            "duration": format_duration(services.stream_duration_seconds(stream, fmt)),
            "bitrate": describe_bitrate(bitrate),
            "size_bytes": size_bytes if size_bytes is not None else "unknown",
            "size_mb": f"{size_bytes / (1024 * 1024):.2f}" if size_bytes is not None else "unknown",
            "percent_of_file": f"{percent:.2f}" if percent is not None else "unknown",
            "estimated": "yes" if estimated else "no",
        })
    return rows


def calculate_bpppf_rows(payload: dict[str, Any], packet_sizes: dict[int, int] | None = None) -> list[dict[str, Any]]:
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    rows: list[dict[str, Any]] = []
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        width = int_metadata_value(stream, "width")
        height = int_metadata_value(stream, "height")
        fps = media_info_video_fps(stream)
        bitrate = stream_bitrate_kbps(stream, fmt, packet_sizes, streams)
        bpppf = None
        if width and height and fps and bitrate:
            bpppf = (bitrate * 1000.0) / (width * height * fps)
        rows.append({
            "stream_index": stream.get("index", "unknown"),
            "width": width or "unknown",
            "height": height or "unknown",
            "fps": f"{fps:.5g}" if fps else "unknown",
            "video_bitrate": describe_bitrate(bitrate),
            "bpppf": f"{bpppf:.6f}" if bpppf is not None else "unknown",
        })
    return rows


def build_media_info_technical_diagnosis(
    input_path: Path,
    payload: dict[str, Any],
    stream_rows: list[dict[str, Any]],
) -> list[str]:
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    chapters = payload.get("chapters") or []
    observations: list[str] = []
    total_size = input_path.stat().st_size if input_path.exists() else format_size_bytes_from_metadata(fmt)
    numeric_rows = [row for row in stream_rows if isinstance(row.get("size_bytes"), int)]
    if numeric_rows:
        dominant = max(numeric_rows, key=lambda row: int(row["size_bytes"]))
        observations.append(
            f"The largest measured stream is stream #{dominant.get('stream_index')} "
            f"({dominant.get('type')} {dominant.get('codec')}) at {format_bytes(int(dominant['size_bytes']))}."
        )
    elif total_size:
        observations.append(f"The file size is {format_bytes(total_size)}; per-stream sizes were not fully available.")

    main_video = media_info_main_video_stream(payload)
    if main_video:
        codec = str(main_video.get("codec_name") or "unknown")
        width = main_video.get("width", "unknown")
        height = main_video.get("height", "unknown")
        main_index = main_video.get("index")
        main_row = next((row for row in stream_rows if row.get("stream_index") == main_index), None)
        bitrate = str(main_row.get("bitrate")) if main_row else describe_bitrate(stream_bitrate_kbps(main_video, fmt, None, streams))
        observations.append(
            f"The main video stream uses {media_info_video_codec_family(codec)} at {width}x{height} with bitrate {bitrate}."
        )
        if main_video.get("color_range") in {None, "", "unknown"}:
            observations.append("Color range is not declared in metadata.")

    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if audio_streams:
        audio_types = sorted({media_info_audio_coding_type(str(stream.get("codec_name") or "")) for stream in audio_streams})
        observations.append(f"Audio streams appear to be: {', '.join(audio_types)}.")

    cover_like = [
        stream for stream in streams
        if stream.get("codec_type") == "video"
        and (stream.get("disposition", {}) or {}).get("attached_pic")
    ]
    if cover_like:
        observations.append(f"{len(cover_like)} extra video stream(s) look like attached cover art.")
    observations.append("Chapters are present." if chapters else "No chapters were reported by ffprobe.")
    return observations


def detected_video_bitrate_limit(answers: dict[str, Any]) -> tuple[int | None, str]:
    values: list[int] = []
    items = _folder_validation_items(answers)
    if items:
        for item in items:
            item_answers = _answers_for_folder_item(answers, item)
            if not item_answers.get("video_streams"):
                continue
            packet_sizes = services.get_packet_sizes(item_answers)
            value = stream_bitrate_kbps(item_answers["video_streams"][0], item_answers.get("format"), packet_sizes)
            if value:
                values.append(value)
        return (min(values) if values else None), "lowest detected source video bitrate in folder"

    if not answers.get("video_streams"):
        return None, "detected source video bitrate"
    packet_sizes = services.get_packet_sizes(answers)
    value = stream_bitrate_kbps(answers["video_streams"][0], answers.get("format"), packet_sizes)
    return value, "detected source video bitrate"


def join_audio_bitrate_candidates(answers: dict[str, Any], audio_index: int) -> list[tuple[int, str]]:
    """(kbps, file_name) for the given audio track across the ordered Join input
    set (primary + join items). Unknown/missing bitrates are skipped."""
    candidates: list[tuple[int, str]] = []
    primary_streams = answers.get("audio_streams") or []
    if 0 <= audio_index < len(primary_streams):
        value = stream_bitrate_kbps(primary_streams[audio_index], answers.get("format"), services.get_packet_sizes(answers))
        if value:
            candidates.append((int(value), Path(answers["input_path"]).name))
    for item in answers.get("join_input_items") or []:
        item_streams = item.get("audio_streams") or []
        if 0 <= audio_index < len(item_streams):
            item_answers = join_item_answers(answers, item)
            value = stream_bitrate_kbps(item_streams[audio_index], item.get("format"), services.get_packet_sizes(item_answers))
            if value:
                candidates.append((int(value), Path(item.get("path")).name))
    return candidates


def detected_audio_bitrate_limit(answers: dict[str, Any]) -> tuple[int | None, str]:
    values: list[int] = []
    items = _folder_validation_items(answers)
    if items:
        for item in items:
            item_answers = _answers_for_folder_item(answers, item)
            streams = item_answers.get("audio_streams") or []
            if not streams:
                continue
            if item_answers.get("audio_tracks_mode") in {"d", "e", "de", "ed"}:
                indexes = list(range(len(streams)))
            else:
                indexes = selected_audio_streams(item_answers) if item_answers.get("audio_tracks") is not None else [0]
            packet_sizes = services.get_packet_sizes(item_answers)
            for index in indexes:
                if 0 <= index < len(streams):
                    value = stream_bitrate_kbps(streams[index], item_answers.get("format"), packet_sizes)
                    if value:
                        values.append(value)
        return (min(values) if values else None), "lowest detected selected audio bitrate in folder"

    streams = answers.get("audio_streams") or []
    selected = selected_audio_streams(answers) if streams else []
    if not selected:
        return None, "detected source audio bitrate"
    packet_sizes = services.get_packet_sizes(answers)
    first_selected = selected[0]
    if first_selected < 0 or first_selected >= len(streams):
        return None, "detected source audio bitrate"
    value = stream_bitrate_kbps(streams[first_selected], answers.get("format"), packet_sizes)
    return value, "detected selected audio bitrate"


def apply_config_video_options(
    answers: dict[str, Any],
    config: dict[str, Any],
    skip_crop: bool = False,
    force_video_options: bool = False,
) -> None:
    video_codec = config_value(config, "video_codec") or DEFAULT_VIDEO_CODEC
    if video_codec.lower() == "n":
        video_codec = "copy"
    answers["video_codec"] = video_codec
    answers["use_gpu"] = parse_bool_config(config_value(config, "use_gpu"), True)
    if skip_crop:
        answers["crop_enabled"] = False
        answers["crop_values_inline"] = False
        for key in ("crop_top", "crop_left", "crop_right", "crop_bottom"):
            answers.pop(key, None)
    else:
        parse_crop_config_value(answers, config_value(config, "crop"), config)

    if not force_video_options and not video_reencode_options_applicable(answers):
        return

    packet_sizes = services.get_packet_sizes(answers)
    source_video_bitrate = stream_bitrate_kbps(answers["video_streams"][0], answers.get("format"), packet_sizes)
    video_bitrate_value = parse_int_config(
        config_value(config, "video_bitrate_kbps"),
        DEFAULT_OUTPUT_VIDEO_BITRATE_KBPS,
        allow_n=True,
    )
    if video_bitrate_value == "n":
        answers["video_bitrate_kbps"] = source_video_bitrate
        answers["video_bitrate_keep"] = True
    else:
        answers["video_bitrate_kbps"] = video_bitrate_value
        answers["video_bitrate_keep"] = False
    mode = str(config_value(config, "video_bitrate_mode") or "quality_vbr").strip().lower()
    answers["video_bitrate_mode"] = mode if mode in {"quality_vbr", "strict_size"} else "quality_vbr"

    resolution_value = config_value(config, "resolution") or "n"
    answers["resolution"] = parse_resolution(resolution_value)

    fps_value = parse_int_config(config_value(config, "fps"), None, allow_n=True)
    answers["fps"] = None if fps_value in {None, "n"} else fps_value


def step_audio_track_for_tool(answers: dict[str, Any]) -> None:
    streams = answers.get("audio_streams") or []
    if not streams:
        raise ValueError("This mode needs an audio stream.")
    if len(streams) == 1:
        # Only one audio track: select it automatically (no prompt) but say so.
        answers["audio_index"] = 0
        codec = str(streams[0].get("codec_name", "unknown"))
        appio.note(f"Only one audio track (codec: {codec}); selecting it automatically.")
        log_info("Audio tool auto-selected the only audio track: index=0")
        return
    print()
    print(paint("Audio streams", Color.BOLD + Color.BLUE))
    fmt = answers.get("format", {})
    packet_sizes = services.get_packet_sizes(answers)
    volume_stats = services.get_audio_volume_stats(answers)
    for idx, stream in enumerate(streams):
        print(
            f"  {paint(str(idx + 1), Color.LIGHT_BLUE)}: "
            f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
            f"{field_text('channels', stream.get('channels', 'unknown'), Color.GREEN)} | "
            f"{field_text('sample_rate', stream.get('sample_rate', 'unknown'), Color.MAGENTA)} | "
            f"{field_text('bitrate', describe_bitrate(stream_bitrate_kbps(stream, fmt, packet_sizes)), Color.YELLOW)} | "
            f"{field_text('mean / max volume', audio_mean_max_volume_field(volume_stats, idx), Color.MEAN_VOLUME)}"
        )
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Choose audio track to process",
                "track number 1 is the first audio track",
                "1",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "1"
        if re.fullmatch(r"\d+", value):
            index = int(value) - 1
            if 0 <= index < len(streams):
                answers["audio_index"] = index
                log_info(f"Audio tool selected audio track: index={index} (1-based {index + 1})")
                return
        appio.error(f"Enter an audio track number from 1 to {len(streams)}.")


def print_additional_track_file_info(item: dict[str, Any]) -> None:
    path: Path = item["path"]
    fmt = item.get("format", {})
    print()
    print(paint("Additional file info", Color.BOLD + Color.LIGHT_BLUE))
    print(paint("-" * 48, Color.GRAY))
    print(field_text("Path", path, Color.WHITE))
    print(field_text("Container", fmt.get("format_name", "unknown"), Color.CYAN))
    print(field_text("Duration", format_duration(services.stream_duration_seconds({}, fmt)), Color.MAGENTA))
    print(field_text("Total bitrate", describe_total_bitrate(fmt), Color.YELLOW))
    if path.exists():
        print(field_text("File size", format_bytes(path.stat().st_size), Color.LIME))

    audio_streams = item.get("audio_streams") or []
    subtitle_streams = item.get("subtitle_streams") or []
    ignored_video_streams = item.get("video_streams") or []

    if audio_streams:
        print(paint("\nAudio streams to add", Color.BOLD + Color.BLUE))
        volume_stats = item.get("audio_volume_stats") or {}
        for idx, stream in enumerate(audio_streams):
            rate = stream_bitrate_kbps(stream, fmt)
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('channels', stream.get('channels', 'unknown'), Color.GREEN)} | "
                f"{field_text('sample_rate', stream.get('sample_rate', 'unknown'), Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('mean / max volume', audio_mean_max_volume_field(volume_stats, idx), Color.MEAN_VOLUME)} | "
            f"{field_text('language', display_language(stream_tag_value(stream, 'language')), Color.WHITE)} | "
                f"{field_text('title', stream_tag_value(stream, 'title'), Color.WHITE)}"
            )

    if subtitle_streams:
        print(paint("\nSubtitle streams to add", Color.BOLD + Color.MAGENTA))
        for idx, stream in enumerate(subtitle_streams):
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('language', display_language(stream_tag_value(stream, 'language')), Color.WHITE)} | "
                f"{field_text('title', stream_tag_value(stream, 'title'), Color.WHITE)}"
            )

    if ignored_video_streams:
        print(paint("\nIgnored cover art/video streams (not added)", Color.BOLD + Color.ORANGE))
        for idx, stream in enumerate(ignored_video_streams):
            width = stream.get("width", "?")
            height = stream.get("height", "?")
            rate = stream_bitrate_kbps(stream, fmt)
            duration = format_duration(services.stream_duration_seconds(stream, fmt))
            disposition = stream.get("disposition") or {}
            is_cover_art = bool(disposition.get("attached_pic")) or str(stream.get("codec_name", "")).lower() in {"mjpeg", "png"}
            stream_type = "cover art" if is_cover_art else "video"
            chapters_value, chapters_color = chapter_presence(item)
            print(
                f"  {paint(str(idx), Color.LIGHT_BLUE)}: "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('type', stream_type, Color.ORANGE)} | "
                f"{field_text('size', str(width) + 'x' + str(height), Color.LIME)} | "
                f"{field_text('bit depth', describe_video_bit_depth(stream), Color.PINK)} | "
                f"{field_text('Color range', display_color_range(stream.get('color_range')), Color.COLOR_RANGE_VALUE)} | "
                f"{field_text('duration', duration, Color.MAGENTA)} | "
                f"{field_text('bitrate', describe_bitrate(rate), Color.YELLOW)} | "
                f"{field_text('total bitrate', describe_total_bitrate(fmt), Color.AQUA)} | "
                f"{field_text('chapters', chapters_value, chapters_color)}"
            )
    print()


def extract_stream_description(stream: dict[str, Any], answers: dict[str, Any]) -> str:
    codec_type = str(stream.get("codec_type") or "unknown")
    codec = str(stream.get("codec_name") or "unknown")
    stream_index = stream_global_index(stream)
    duration = format_duration(services.stream_duration_seconds(stream, answers.get("format")))
    packet_sizes = services.get_packet_sizes(answers)
    color = {"video": Color.MAGENTA, "audio": Color.BLUE, "subtitle": Color.LIGHT_YELLOW}.get(codec_type, Color.WHITE)
    parts = [
        field_text("stream index", stream_index if stream_index is not None else "unknown", Color.LIGHT_BLUE),
        field_text("type", codec_type, color),
        field_text("codec", codec, Color.CYAN),
        field_text("duration", duration, Color.MAGENTA),
    ]
    if codec_type == "video":
        parts.append(field_text("size", f"{stream.get('width', '?')}x{stream.get('height', '?')}", Color.LIME))
        fps = rational_to_float(stream.get("avg_frame_rate"))
        parts.append(field_text("fps", format(fps, ".3g") if fps else "unknown", Color.MAGENTA))
    elif codec_type == "audio":
        parts.append(field_text("channels", stream.get("channels", "unknown"), Color.GREEN))
        parts.append(field_text("sample_rate", stream.get("sample_rate", "unknown"), Color.MAGENTA))
        parts.append(field_text("bitrate", describe_bitrate(stream_bitrate_kbps(stream, answers.get("format"), packet_sizes)), Color.YELLOW))
    elif codec_type == "subtitle":
        lang = display_language(stream.get("tags", {}).get("language"))
        title = stream.get("tags", {}).get("title") or "unknown"
        parts.append(field_text("language", lang or "unknown", Color.AQUA))
        parts.append(field_text("title", title, Color.WHITE))
    return " | ".join(parts)


__all__ = [
    'join_input_media_stats',
    'possible_audio_duplicate',
    'classify_sparse_audio_tracks',
    'print_audio_duplicate_report',
    'media_info_stream_size_rows',
    'calculate_bpppf_rows',
    'build_media_info_technical_diagnosis',
    'detected_video_bitrate_limit',
    'join_audio_bitrate_candidates',
    'detected_audio_bitrate_limit',
    'apply_config_video_options',
    'step_audio_track_for_tool',
    'print_additional_track_file_info',
    'extract_stream_description',
]
