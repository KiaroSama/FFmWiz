"""FFmWiz extracted helper tier ext11 (post-services layer).

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
from ffmwiz import wizard_raw  # noqa: F401  (module, so a patch is seen)
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
from ffmwiz.support.ext08 import *  # noqa: F401,F403
from ffmwiz.support.ext09 import *  # noqa: F401,F403
from ffmwiz.support.ext10 import *  # noqa: F401,F403


def prepare_folder_job_answers(
    settings_answers: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any]:
    job = dict(settings_answers)
    copy_media_metadata(job, item["answers"])
    job["output_location"] = settings_answers["folder_output_location"]
    job["output_collision_suffix"] = "_Encode"
    job.pop("output_name_stem", None)
    job.pop("output_path", None)
    job.pop("cmd", None)
    # Resolve this file's color range from the batch policy (known ranges win).
    job.pop("color_range_choice", None)
    job.pop("_color_range_from_batch", None)
    apply_folder_batch_color_range(job, settings_answers, item)

    if settings_answers.get("output_format_keep_input"):
        input_ext = job["input_path"].suffix.lstrip(".") or ("mp4" if job.get("video_streams") else "mp3")
        job["output_ext"] = input_ext.lower()

    if output_has_video(job):
        if job.get("video_bitrate_keep"):
            packet_sizes = services.get_packet_sizes(job)
            source = stream_bitrate_kbps(job["video_streams"][0], job.get("format"), packet_sizes)
            job["video_bitrate_kbps"] = source
        if job.get("cut_keep_ranges"):
            duration = services.stream_duration_seconds({}, job.get("format")) or 0.0
            if duration > 0:
                keep_ranges = normalize_cut_ranges(list(job.get("cut_keep_ranges") or []), duration)
                if keep_ranges:
                    job["cut_keep_ranges"] = keep_ranges
                else:
                    job.pop("cut_keep_ranges", None)
                    appio.note(f"Cuts skipped for {job['input_path'].name}: no valid ranges remain after clamping to this file.")
    else:
        job.pop("cut_keep_ranges", None)

    if not job.get("audio_streams"):
        job["audio_tracks"] = []
    elif job.get("audio_tracks_mode") in {"d", "e", "de", "ed"}:
        job["audio_tracks"] = auto_select_audio_tracks(job, str(job["audio_tracks_mode"]))
    else:
        selected_audio = job.get("audio_tracks", [0])
        validate_stream_selection_for_folder_job(selected_audio, len(job["audio_streams"]), "audio")

    if job.get("subtitle_tracks") is not None:
        if not job.get("subtitle_streams"):
            job["subtitle_tracks"] = []
        else:
            validate_stream_selection_for_folder_job(
                job.get("subtitle_tracks", []),
                len(job["subtitle_streams"]),
                "subtitle",
            )

    audio_indices = selected_audio_streams(job) if job.get("audio_streams") else []
    if audio_indices and job.get("audio_bitrate_keep") and job.get("audio_codec") != "copy":
        first_selected = audio_indices[0]
        packet_sizes = services.get_packet_sizes(job)
        source = stream_bitrate_kbps(job["audio_streams"][first_selected], job.get("format"), packet_sizes)
        job["audio_bitrate_kbps"] = source
    # The same rule as the bitrate above, for the same reason: "n = keep the
    # current rate" is a per-FILE answer that was resolved once, against the
    # representative file. Baked in, it stopped meaning "keep" and started
    # meaning that one number -- a 44.1 kHz representative silently resampled
    # every 48 kHz and 96 kHz file in the folder down to 44.1 kHz.
    if audio_indices and job.get("audio_sample_rate_keep") and job.get("audio_codec") != "copy":
        job["audio_sample_rate"] = source_audio_sample_rate(job)

    if output_is_audio_only(job) and not audio_indices:
        raise ValueError("Audio-only output was selected, but this file has no selected audio stream.")
    if not output_has_video(job) and not audio_indices:
        raise ValueError("No output streams are selected for this file.")

    return job


def step_audio_tracks(answers: dict[str, Any]) -> None:
    streams = answers["audio_streams"]
    signature = _audio_report_signature(answers)

    def print_audio_report() -> None:
        packet_sizes = services.get_packet_sizes(answers)
        report = detect_duplicate_audio(answers) if answers.get("detect_duplicate_audio", True) else None
        fmt = answers.get("format", {})
        volume_stats = services.get_audio_volume_stats(answers)
        print()
        print(paint("Detected audio tracks:", Color.BOLD + Color.BLUE))
        for idx, stream in enumerate(streams):
            size, _ = stream_size_bytes(stream, fmt, packet_sizes)
            labels = duplicate_labels(idx, report) if report else []
            label_text = f" | {' | '.join(labels)}" if labels else ""
            print(
                f"  {paint(stream_title(stream, idx), Color.WHITE)} | "
                f"{field_text('mean / max volume', audio_mean_max_volume_field(volume_stats, idx), Color.MEAN_VOLUME)} | "
                f"{field_text('size', format_bytes(size), Color.LIME)}{label_text}"
            )
        join_items = list(answers.get("join_input_items") or [])
        if join_items:
            print()
            print(paint("Joined input audio tracks", Color.BOLD + Color.BLUE))
            print("  " + paint("The selected track numbers below will be applied to every joined input.", Color.YELLOW))
            for input_pos, item in enumerate(join_items, start=2):
                joined = join_item_answers(answers, item)
                joined_streams = joined.get("audio_streams") or []
                joined_fmt = joined.get("format", {})
                joined_packet_sizes = services.get_packet_sizes(joined)
                joined_report = detect_duplicate_audio(joined) if joined.get("detect_duplicate_audio", True) and joined_streams else None
                joined_volume = services.get_audio_volume_stats(joined) if joined_streams else {}
                print("  " + field_text(f"input {input_pos}", Path(item.get("path")).name, Color.WHITE))
                for idx, stream in enumerate(joined_streams):
                    size, _ = stream_size_bytes(stream, joined_fmt, joined_packet_sizes)
                    labels = duplicate_labels(idx, joined_report) if joined_report else []
                    label_text = f" | {' | '.join(labels)}" if labels else ""
                    print(
                        f"    {paint(stream_title(stream, idx), Color.WHITE)} | "
                        f"{field_text('mean / max volume', audio_mean_max_volume_field(joined_volume, idx), Color.MEAN_VOLUME)} | "
                        f"{field_text('size', format_bytes(size), Color.LIME)}{label_text}"
                    )
                if len(joined_streams) != len(streams):
                    warning = f"input {input_pos} has {len(joined_streams)} audio track(s), primary input has {len(streams)}."
                    print("    " + paint(warning, Color.YELLOW))
        answers["_audio_report_shown_signature"] = signature

    # Suppress re-printing the (potentially long) audio report on Back
    # navigation when nothing relevant changed; the user can type 'r' to
    # reprint it. A changed file/track set invalidates the cached signature.
    if answers.get("_audio_report_shown_signature") == signature:
        print()
        appio.note("Audio report already displayed in this session; skipping repeat to avoid clutter. Enter 'r' at the prompt to reprint it.")
    else:
        print_audio_report()

    prompt = appio.question_prompt(
        answers,
        "Which audio tracks should be kept?",
        f"example: {example_text('0,1,2')}; {colored_audio_track_hint()}; r=reprint report",
        "de",
        back="back=b, quit=exit",
    )
    while True:
        value = appio.ask_raw(prompt)
        lowered = value.lower()
        if lowered == "r":
            print_audio_report()
            continue
        if not value:
            answers["audio_tracks_mode"] = "de"
            answers["audio_tracks"] = auto_select_audio_tracks(answers, "de")
            print(paint(f"Auto-selected audio tracks: {answers['audio_tracks']}", Color.LIME))
            return
        # This prompt uses zero-based audio track numbers, so 0 must remain a
        # valid stream selection. Back is intentionally b/back here.
        if lowered in {"b", "back"}:
            raise Back()
        if lowered in {"d", "e", "de", "ed"}:
            answers["audio_tracks_mode"] = lowered
            answers["audio_tracks"] = auto_select_audio_tracks(answers, lowered)
            print(paint(f"Auto-selected audio tracks: {answers['audio_tracks']}", Color.LIME))
            return
        try:
            answers["audio_tracks"] = parse_selection_config(value, len(streams), [0])
            answers["audio_tracks_mode"] = "manual"
            return
        except ValueError as exc:
            appio.error(str(exc))


def apply_config_audio_options(answers: dict[str, Any], config: dict[str, Any]) -> None:
    if not answers.get("audio_streams"):
        return

    # Volume rides the same gate the interactive step uses -- any audio
    # present, independent of which tracks end up selected or which codec is
    # chosen -- so it is read here, before either of those narrow further.
    answers.pop("audio_volume", None)
    volume = (config_value(config, "audio_volume") or "").strip()
    if volume and volume.lower() not in {"n", "no"}:
        try:
            answers["audio_volume"] = wizard_raw.parse_volume(volume)
        except ValueError as error:
            fail(f"audio_volume in config.env is not valid: {error}")

    audio_tracks_value = config_value(config, "audio_tracks") or "de"
    if audio_tracks_value.lower() in {"d", "e", "de", "ed"}:
        answers["audio_tracks"] = auto_select_audio_tracks(answers, audio_tracks_value)
    else:
        answers["audio_tracks"] = parse_selection_config(
            audio_tracks_value,
            len(answers["audio_streams"]),
            [0],
        )
    if not selected_audio_streams(answers):
        return

    audio_codec = normalize_audio_codec(
        config_value(config, "audio_codec"),
        default_audio_codec_for_ext(answers.get("output_ext", "")),
    )
    answers["audio_codec"] = audio_codec
    if audio_codec == "copy":
        return

    first_selected = selected_audio_streams(answers)[0]
    packet_sizes = services.get_packet_sizes(answers)
    source_audio_bitrate = stream_bitrate_kbps(answers["audio_streams"][first_selected], answers.get("format"), packet_sizes)
    audio_bitrate_value = parse_int_config(
        config_value(config, "audio_bitrate_kbps"),
        DEFAULT_AUDIO_BITRATE_KBPS,
        allow_n=True,
    )
    if audio_bitrate_value == "n":
        answers["audio_bitrate_kbps"] = source_audio_bitrate
        answers["audio_bitrate_keep"] = True
    else:
        answers["audio_bitrate_kbps"] = audio_bitrate_value
        answers["audio_bitrate_keep"] = False


__all__ = [
    'prepare_folder_job_answers',
    'step_audio_tracks',
    'apply_config_audio_options',
]
