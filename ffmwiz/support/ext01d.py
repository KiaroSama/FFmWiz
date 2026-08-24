"""FFmWiz ext01b overflow (ext01d) — split for file size.

Back-imports ext01b and is re-exported by it, so every consumer of
`from ffmwiz.support.ext01b import *` still sees the full set. Monkeypatch-safe.
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
from ffmwiz.support.ext00 import *  # noqa: F401,F403
from ffmwiz.support.L01_subtitles import (  # noqa: F401
    item_subtitle_streams,
    join_subtitle_track_count,
    selected_join_subtitle_tracks,
)

from ffmwiz.support.ext01 import *  # sibling helpers  # noqa: F401,F403
from ffmwiz.support.ext01b import *  # noqa: E402,F401,F403  (back-import)


def step_hardsub_output_format(answers: dict[str, Any]) -> None:
    input_ext = answers["input_path"].suffix.lstrip(".") or "mkv"
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter final output format",
                f"common: {option_list(COMMON_VIDEO_FORMATS)}; {keep_value_text(f'n=Use input format ({input_ext})')}",
                "n",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        try:
            answers["output_ext"] = normalize_format(value, input_ext)
            return
        except ValueError as exc:
            appio.error(str(exc))


def step_hardsub_subtitle_source(answers: dict[str, Any]) -> None:
    subtitle_streams = answers.get("subtitle_streams") or []
    if subtitle_streams:
        print()
        print(paint("Internal subtitle streams", Color.BOLD + Color.MAGENTA))
        for index, stream in enumerate(subtitle_streams, start=1):
            print(
                f"  {paint(str(index) + '.', Color.LIGHT_BLUE)} "
                f"{field_text('codec', stream.get('codec_name', 'unknown'), Color.CYAN)} | "
                f"{field_text('language', display_language(stream_tag_value(stream, 'language')), Color.WHITE)} | "
                f"{field_text('title', stream_tag_value(stream, 'title'), Color.WHITE)}"
            )
        while True:
            value = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    "Choose subtitle source",
                    "1=internal subtitle track; 2=external .srt/.ass/.ssa/.vtt/.webvtt file",
                    "1",
                )
            )
            if is_back_value(value):
                raise Back()
            if not value:
                value = "1"
            if value in {"1", "2"}:
                source = "internal" if value == "1" else "external"
                break
            appio.error("Enter 1 or 2.")
    else:
        appio.note("No internal subtitle streams were found; external subtitle file will be used.")
        source = "external"

    if source == "internal":
        while True:
            value = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    "Choose internal subtitle track",
                    f"1-{len(subtitle_streams)}; track number 1 is the first subtitle stream",
                    "1",
                )
            )
            if is_back_value(value):
                raise Back()
            if not value:
                value = "1"
            if re.fullmatch(r"\d+", value) and 1 <= int(value) <= len(subtitle_streams):
                selected = int(value) - 1
                codec = str(subtitle_streams[selected].get("codec_name", "")).lower()
                codec_error = hardsub_internal_subtitle_error(codec)
                if codec_error:
                    appio.error(codec_error)
                    continue
                answers["hardsub_subtitle_source"] = "internal"
                answers["hardsub_subtitle_index"] = selected
                answers["hardsub_subtitle_codec"] = codec
                return
            appio.error(f"Enter a number from 1 to {len(subtitle_streams)}.")

    while True:
        value = appio.ask_required(
            appio.question_prompt(
                answers,
                "Enter external subtitle file path",
                f"supported common files: {option_list(sorted(ext.lstrip('.') for ext in HARDSUB_SUBTITLE_EXTS))}",
            )
        )
        path = terminal_path(value)
        if not path.exists() or not path.is_file():
            appio.error("Subtitle file not found. Enter the full path again.")
            continue
        if not hardsub_external_subtitle_extension_supported(path):
            appio.error("Unsupported external subtitle extension. Use .srt, .ass, .ssa, .vtt, or .webvtt.")
            continue
        if path.suffix.lower() in {".ass", ".ssa"}:
            if ass_ssa_has_embedded_fonts(path):
                appio.note("This ASS/SSA file contains embedded fonts. fontsdir is optional.")
            else:
                appio.note("No embedded fonts were found. If this subtitle uses custom fonts that are not installed system-wide, provide a fonts directory.")
        answers["hardsub_subtitle_source"] = "external"
        answers["hardsub_subtitle_path"] = path
        return


def step_hardsub_fontsdir(answers: dict[str, Any]) -> None:
    fonts_example = example_text(r"D:\Subs\fonts")
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter subtitle fonts directory",
                f"Enter=none; optional for ASS/SSA embedded fonts and MKV font attachments; example: {fonts_example}",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            answers["hardsub_fontsdir"] = None
            return
        path = terminal_path(value)
        if not path.exists() or not path.is_dir():
            appio.error("Fonts directory not found. Enter an existing folder path or press Enter for none.")
            continue
        answers["hardsub_fontsdir"] = path
        return


def step_hardsub_video_codec(answers: dict[str, Any]) -> None:
    hdr_info = answers.get("hardsub_hdr_info") or {}
    default_codec = "H265" if hdr_info.get("hdr") or hdr_info.get("dolby") else source_video_codec_family(answers)
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter hard-sub video codec",
                f"common: {option_list(['H265', 'H264', 'AV1', 'VP9'])}; {keep_value_text(f'n=match source family ({default_codec})')}; copy is not possible for hard subtitles",
                "n",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value or value.lower() == "n":
            value = default_codec
        if value.lower() == "copy":
            appio.error("Hard subtitles require video re-encoding; copy is not valid here.")
            continue
        answers["video_codec"] = value
        return


def step_hardsub_quality(answers: dict[str, Any]) -> None:
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Choose hard-sub quality",
                colored_hardsub_quality_options(),
                "1",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "1"
        mapping = {"1": "near-lossless", "2": "high quality", "3": "balanced"}
        if value in mapping:
            answers["hardsub_quality_mode"] = mapping[value]
            return
        if value == "4":
            while True:
                custom = appio.ask_raw(
                    appio.question_prompt(
                        answers,
                        "Enter custom quality value",
                        "CRF/CQ integer 1-51; lower is higher quality",
                        "18",
                    )
                )
                if is_back_value(custom):
                    raise Back()
                if not custom:
                    custom = "18"
                if re.fullmatch(r"\d+", custom) and 1 <= int(custom) <= 51:
                    answers["hardsub_quality_mode"] = "custom"
                    answers["hardsub_quality_value"] = int(custom)
                    return
                appio.error("Enter an integer from 1 to 51.")
        else:
            appio.error("Enter 1, 2, 3, or 4.")


def step_hardsub_hdr_handling(answers: dict[str, Any]) -> None:
    hdr_info = answers.get("hardsub_hdr_info") or {}
    if not hdr_info.get("hdr") and not hdr_info.get("dolby"):
        answers["hardsub_hdr_handling"] = "standard"
        return
    print()
    print(paint("HDR / Dolby Vision detected", Color.BOLD + Color.ORANGE))
    print("  " + field_text("HDR", "yes" if hdr_info.get("hdr") else "no", Color.ORANGE))
    print("  " + field_text("Dolby Vision", "yes" if hdr_info.get("dolby") else "no", Color.ORANGE))
    print("  " + field_text("transfer", hdr_info.get("color_transfer"), Color.CYAN))
    print("  " + field_text("primaries", hdr_info.get("color_primaries"), Color.CYAN))
    print("  " + field_text("bit depth", hdr_info.get("bit_depth"), Color.PINK))
    if hdr_info.get("dolby"):
        appio.note("Dolby Vision dynamic metadata cannot be reliably preserved after hard-sub re-encoding; HDR10/static metadata can only be copied best-effort.")
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Choose HDR/Dolby handling",
                "1=preserve HDR metadata best effort; 2=tone-map to SDR; 3=standard encode without HDR-specific handling",
                "1",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "1"
        mapping = {"1": "preserve", "2": "tone-map", "3": "standard"}
        if value in mapping:
            answers["hardsub_hdr_handling"] = mapping[value]
            return
        appio.error("Enter 1, 2, or 3.")


def step_hardsub_audio_mode(answers: dict[str, Any]) -> None:
    if not answers.get("audio_streams"):
        answers["hardsub_audio_mode"] = "none"
        return
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Choose audio handling",
                colored_hardsub_audio_options(),
                "1",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "1"
        if value == "1":
            answers["hardsub_audio_mode"] = "copy-all"
            return
        if value == "3":
            answers["hardsub_audio_mode"] = "none"
            return
        if value == "2":
            answers["hardsub_audio_mode"] = "selected"
            answers["hardsub_audio_tracks"] = ask_selection(
                appio.question_prompt(
                    answers,
                    "Which audio tracks should be copied?",
                    f"example: {example_text('0,1')}; 0 is the first audio track here; back=b, quit=exit",
                    back="back=b, quit=exit",
                ),
                max_count=len(answers["audio_streams"]),
                default=[0],
                allow_none=True,
            )
            if answers["hardsub_audio_tracks"] == "all":
                answers["hardsub_audio_mode"] = "copy-all"
                answers.pop("hardsub_audio_tracks", None)
            return
        appio.error("Enter 1, 2, or 3.")


def print_startup_banner(config_path: Path, launcher_path: Path, answers: dict[str, Any] | None = None) -> None:
    _ = (config_path, launcher_path)
    startup_line("FFmpeg", "found.", Color.LIME)
    answers = answers or {}
    if answers.get("gpu_available"):
        model = answers.get("gpu_model") or "NVIDIA NVENC GPU"
        startup_line("GPU", f"detected - {model} (NVENC hardware encoding).", Color.GREEN, Color.GREEN)
    else:
        startup_line("GPU", "not detected - video will be encoded on the CPU.", Color.RED, Color.RED)


def join_copy_plan(answers: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether a concat-demuxer stream copy delivers EXACTLY the selected plan.

    "The inputs are copy-compatible" and "the selected plan can be stream-
    copied" are different questions, and conflating them cost the user both
    ways: the summary announced "stream copy, no re-encode" for a plan the
    builder then re-encoded, because the gate compared REPRESENTATIONS
    (`audio_tracks in (None, "all")`) instead of sets -- an ordinary one-track
    input answered `[0]`, which IS everything, and was pushed onto the encode
    path anyway (F06).

    `"supported"` answers the routing question. `"maps_everything"` answers the
    narrower one `build_join_copy_command` needs: may a blanket `-map 0` be
    used, or would it copy streams the user asked to drop? Codec and timeline
    choices block the first but not the second -- they change how streams are
    written, not which ones exist.
    """
    stream_reasons: list[str] = []
    audio_state, audio_indices = join_audio_selection(answers, items)
    audio_count = join_audio_track_count(items)
    if audio_state != "unasked" and audio_indices != list(range(audio_count)):
        stream_reasons.append(
            "no audio track was selected" if not audio_indices
            else f"only audio track(s) {audio_indices} of {audio_count} were selected")
    subtitle_count = join_subtitle_track_count(items)
    if subtitle_count:
        if not source_subtitles_keep_enabled(answers):
            stream_reasons.append("source subtitles are dropped")
        elif ("subtitle_tracks" in answers
                and selected_join_subtitle_tracks(answers, items) != list(range(subtitle_count))):
            stream_reasons.append("only some subtitle tracks were selected")
    if any(item.get("data_streams") for item in items) and not source_data_keep_enabled(answers):
        stream_reasons.append("source data streams are dropped")
    if (any(item.get("attachment_streams") for item in items)
            and "keep_embedded_attachments" in answers
            and not answers.get("keep_embedded_attachments")):
        # Only an EXPLICIT answer counts: the standalone join never asks, and a
        # missing key must not be read as "drop them".
        stream_reasons.append("embedded attachments are dropped")
    if (any(len(item.get("video_streams") or []) > 1 for item in items)
            and not source_extra_video_keep_enabled(answers)):
        stream_reasons.append("extra source video streams are dropped")

    edit_reasons: list[str] = []
    if (any(item.get("video_streams") for item in items)
            and str(answers.get("video_codec", "")).lower() != "copy"):
        edit_reasons.append("the selected video codec re-encodes")
    if (any(item_audio_streams(item) for item in items)
            and str(answers.get("audio_codec", "")).lower() != "copy"):
        edit_reasons.append("the selected audio codec re-encodes")
    if not source_metadata_keep_enabled(answers):
        edit_reasons.append("source metadata is dropped")
    if not source_chapters_keep_enabled(answers):
        edit_reasons.append("source chapters are dropped")
    if video_filters_required(answers) or answers.get("reverse_video"):
        edit_reasons.append("the selected video edit rebuilds the timeline")
    if (loudnorm_transform_enabled(answers)
            or audio_speed_transform_enabled(answers)
            or answers.get("audio_cut_keep_ranges")):
        edit_reasons.append("the selected audio edit requires re-encoding")
    return {
        "supported": not (stream_reasons or edit_reasons),
        "maps_everything": not stream_reasons,
        "reasons": stream_reasons + edit_reasons,
    }


def build_join_copy_command(answers: dict[str, Any], items: list[dict[str, Any]], output_path: Path) -> list[str]:
    output_path = resolve_output_collision_against_inputs(
        output_path,
        [Path(item["path"]) for item in items if item.get("path")],
        answers.get("output_collision_suffix", "_Encode"),
    )
    answers["output_path"] = output_path
    list_path = write_join_concat_list(items, output_path)
    answers["_join_concat_list"] = list_path
    cmd = [
        answers["ffmpeg"],
        "-hide_banner",
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
    ]
    if join_copy_plan(answers, items)["maps_everything"]:
        cmd.extend(["-map", "0"])
    else:
        # `-map 0` copies whatever the concat input happens to carry, so a
        # selective request was silently OVER-fulfilled: tracks the user asked
        # to drop came through anyway (F06). Map exactly what was asked for.
        if any(item.get("video_streams") for item in items):
            cmd.extend(["-map", "0:v" if source_extra_video_keep_enabled(answers) else "0:v:0"])
        for index in join_audio_selection(answers, items)[1]:
            cmd.extend(["-map", f"0:a:{index}?"])
        if source_subtitles_keep_enabled(answers):
            for index in selected_join_subtitle_tracks(answers, items):
                cmd.extend(["-map", f"0:s:{index}?"])
        if source_data_keep_enabled(answers) and any(item.get("data_streams") for item in items):
            cmd.extend(["-map", "0:d?"])
        if answers.get("keep_embedded_attachments") and any(item.get("attachment_streams") for item in items):
            cmd.extend(["-map", "0:t?"])
    cmd.extend(["-c", "copy", str(output_path)])
    return cmd


def print_join_summary(items: list[dict[str, Any]], copy_compatible: bool, reasons: list[str],
                       plan: dict[str, Any] | None = None) -> None:
    """Join summary. `plan` is `join_copy_plan`'s verdict when the caller has one.

    Without it the summary reported input COMPATIBILITY as if it were the join
    mode, so a compatible pair whose selected plan needed re-encoding was told
    "stream copy, no re-encode" while the command ran libx265 (F06).
    """
    print()
    print(paint("Join summary:", Color.BOLD + Color.LIGHT_BLUE))
    for idx, item in enumerate(items, start=1):
        video_streams = item.get("video_streams") or []
        if video_streams:
            video = video_streams[0]
            fps = rational_to_float(video.get("avg_frame_rate")) or rational_to_float(video.get("r_frame_rate")) or 0.0
            detail = (
                f"{item['path'].name} | duration: {format_duration(item.get('duration'))} | "
                f"video: {video.get('codec_name', 'unknown')} {video.get('width', '?')}x{video.get('height', '?')} {fps:g} fps | "
                f"audio tracks: {len(item.get('audio_streams') or [])}"
            )
        else:
            audio_streams = item.get("audio_streams") or []
            codec = audio_streams[0].get("codec_name", "unknown") if audio_streams else "none"
            detail = (
                f"{item['path'].name} | duration: {format_duration(item.get('duration'))} | "
                f"audio-only: {codec} | audio tracks: {len(audio_streams)}"
            )
        print("  " + field_text(f"input {idx}", detail, Color.WHITE))
    if copy_compatible:
        print("  " + field_text("inputs", "stream-copy compatible", Color.GREEN))
    else:
        print("  " + field_text("inputs", "not stream-copy compatible", Color.ORANGE))
        for reason in reasons:
            print("    " + paint(reason, Color.YELLOW))
    will_copy = copy_compatible and (plan is None or bool(plan.get("supported")))
    if will_copy:
        print("  " + field_text("join mode", "stream copy, no re-encode", Color.GREEN))
    else:
        print("  " + field_text("join mode", "re-encode required", Color.ORANGE))
        if copy_compatible and plan is not None:
            for reason in plan.get("reasons") or []:
                print("    " + paint(reason, Color.YELLOW))


def ask_join_frame_rate_policy(answers: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    """Ask how to handle joined video inputs that have different frame rates.

    Only relevant for a video join of >=2 inputs whose frame rates differ.
    Default (yes) unifies every input to one frame rate: it then asks the target
    fps (defaulting to the highest source rate) and stores it in answers['fps'].
    Declining (no) marks the join as VFR (variable frame rate): each file keeps
    its own frame rate and the output has a variable frame rate. The unify
    question is asked first and the fps question comes after it, per design.

    Returns True when this join branch decided the fps (the caller must NOT ask
    the fps question again), False when the policy does not apply."""
    if len(items) < 2 or not join_frame_rates_differ(items):
        answers.setdefault("join_vfr", False)
        return False
    rates_text = ", ".join(f"{rate:g}" for rate in join_video_frame_rates(items))
    appio.note(f"Joined inputs have different frame rates ({rates_text} fps).")
    unify = appio.ask_yes_no(
        appio.question_prompt(answers, "Make all frame rates the same?", "y/n", "y"),
        True,
    )
    if not unify:
        answers["join_vfr"] = True
        answers["join_unify_fps"] = False
        answers["fps"] = None
        appio.note("VFR join: each file keeps its own frame rate; the output will have a variable frame rate.")
        return True
    answers["join_vfr"] = False
    answers["join_unify_fps"] = True
    highest = join_highest_frame_rate(items) or 30.0
    default_fps = max(1, int(round(highest)))
    prompt = appio.question_prompt(
        answers,
        "Enter frames per second for all joined videos",
        f"examples: {example_text('24,30,60')}; highest source is {format(highest, '.3g')}",
        str(default_fps),
    )
    while True:
        value = appio.ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        if not value:
            value = str(default_fps)
        if not re.fullmatch(r"\d+", value):
            appio.error("Enter an integer only.")
            continue
        number = int(value)
        if number == 0:
            raise Back()
        answers["fps"] = number
        return True


__all__ = [
    'step_hardsub_output_format',
    'step_hardsub_subtitle_source',
    'step_hardsub_fontsdir',
    'step_hardsub_video_codec',
    'step_hardsub_quality',
    'step_hardsub_hdr_handling',
    'step_hardsub_audio_mode',
    'print_startup_banner',
    'join_copy_plan',
    'build_join_copy_command',
    'print_join_summary',
    'ask_join_frame_rate_policy',
]
