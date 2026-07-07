"""FFmWiz mux cluster (extracted from FFmWiz.py, method الف)."""
from __future__ import annotations
from dataclasses import dataclass, field

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
from ffmwiz import guibridge  # noqa: F401
from ffmwiz.metadata import *  # noqa: F401,F403
from ffmwiz import metadata  # noqa: F401
from ffmwiz.modes import *  # noqa: F401,F403
from ffmwiz import modes  # noqa: F401
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz import runner  # noqa: F401
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz import runtime  # noqa: F401
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.trackmanager import *  # noqa: F401,F403
from ffmwiz import trackmanager  # noqa: F401
from ffmwiz.wizard import *  # noqa: F401,F403
from ffmwiz import wizard  # noqa: F401
from ffmwiz.mux import *  # sibling mux_* helpers  # noqa: F401,F403


MUX_LANGUAGE_COLORS = (
    Color.GREEN,
    Color.CYAN,
    Color.MAGENTA,
    Color.YELLOW,
    Color.BLUE,
    Color.ORANGE,
    Color.MUX_GOLD,
    Color.LIME,
    Color.MUX_MINT,
    Color.MUX_EMERALD,
    Color.MUX_TEAL,
    Color.MUX_AQUA,
    Color.MUX_SKY,
    Color.MUX_AZURE,
    Color.MUX_INDIGO,
    Color.MUX_VIOLET,
    Color.MUX_PURPLE,
    Color.MUX_LAVENDER,
    Color.PINK,
    Color.MUX_ROSE,
)


def mux_ask_metadata_edits(
    answers: dict[str, Any],
    media_files: list[MuxMediaFile],
    current_rules: MuxCleanupRules,
) -> list[MuxStreamMetadataEdit]:
    edits: list[MuxStreamMetadataEdit] = []
    audio_languages = mux_kept_languages_for_metadata(media_files, "audio", current_rules)
    subtitle_languages = mux_kept_languages_for_metadata(media_files, "subtitle", current_rules)
    default = "1" if "unknown" in audio_languages else "2" if "unknown" in subtitle_languages else "8"

    if not mux_ask_yes_no(answers, "Edit output stream metadata?", False):
        return edits

    while True:
        answers["_question_number"] += 1
        if edits:
            appio.note("Current metadata edits: " + mux_format_metadata_edits(edits))
        action = mux_ask_choice(
            answers,
            "Metadata edit action",
            (
                "1=set audio language by current language; "
                "2=set subtitle language by current language; "
                "3=set audio language by exact stream indexes; "
                "4=set subtitle language by exact stream indexes; "
                "5=set audio title by exact stream indexes; "
                "6=set subtitle title by exact stream indexes; "
                "7=clear edits; 8=done"
            ),
            {"1", "2", "3", "4", "5", "6", "7", "8"},
            default if not edits else "8",
        )
        if action == "8":
            return edits
        if action == "7":
            edits = []
            appio.note("Metadata edits cleared.")
            continue
        if action in {"1", "2"}:
            codec_type = "audio" if action == "1" else "subtitle"
            available = audio_languages if codec_type == "audio" else subtitle_languages
            if not available:
                appio.note(f"No kept {codec_type} streams are available for metadata editing.")
                continue
            answers["_question_number"] += 1
            current_languages = mux_ask_language_codes_required(
                answers,
                f"Current {codec_type} language code(s) to edit",
                available,
            )
            answers["_question_number"] += 1
            new_language = mux_normalize_language(mux_ask_text(answers, f"New {codec_type} language code", "example: jpn"))
            edits.append(MuxStreamMetadataEdit(codec_type=codec_type, match_languages=current_languages, language=new_language))
            appio.note("Added metadata edit: " + mux_format_metadata_edit(edits[-1]))
            continue

        codec_type = "audio" if action in {"3", "5"} else "subtitle"
        available_indexes = mux_kept_indexes_for_metadata(media_files, codec_type, current_rules)
        if not available_indexes:
            appio.note(f"No kept {codec_type} streams are available for metadata editing.")
            continue
        answers["_question_number"] += 1
        indexes = mux_ask_csv_int_required(answers, f"{codec_type.capitalize()} stream indexes to edit", available_indexes)
        if action in {"3", "4"}:
            answers["_question_number"] += 1
            new_language = mux_normalize_language(mux_ask_text(answers, f"New {codec_type} language code", "example: jpn"))
            edits.append(MuxStreamMetadataEdit(codec_type=codec_type, match_indexes=indexes, language=new_language))
        else:
            answers["_question_number"] += 1
            new_title = mux_ask_text(answers, f"New {codec_type} title", "text title for the kept output stream")
            edits.append(MuxStreamMetadataEdit(codec_type=codec_type, match_indexes=indexes, title=new_title))
        appio.note("Added metadata edit: " + mux_format_metadata_edit(edits[-1]))


def mux_configure_rules(answers: dict[str, Any], media_files: list[MuxMediaFile]) -> MuxCleanupRules:
    audio_languages = mux_unique_stream_values(media_files, "audio", "language")
    subtitle_languages = mux_unique_stream_values(media_files, "subtitle", "language")
    answers["_question_number"] = 2
    if len(audio_languages) <= 1:
        selection_style = "2"
        log_info("Stream Cleanup selection style skipped: one or zero audio languages found.")
    else:
        selection_style = mux_ask_choice(
            answers,
            "Choose stream selection style",
            "1=exact stream indexes; 2=advanced rules by language/title/index",
            {"1", "2"},
            "2",
        )

    audio_mode = "4"
    audio_language_values: list[str] = []
    audio_titles: list[str] = []
    audio_indexes: list[int] = []
    subtitle_mode = "1"
    subtitle_language_values: list[str] = []
    subtitle_titles: list[str] = []
    subtitle_indexes: list[int] = []

    if selection_style == "1":
        answers["_question_number"] = 3
        audio_value = mux_ask_text(
            answers,
            "Audio stream indexes to keep",
            f"examples: {example_text('0,1,2')}; all; none; available: {example_text(','.join(map(str, mux_stream_indexes(media_files, 'audio'))) or 'none')}; use b to go back",
            zero_is_value=True,
        ).lower()
        if audio_value in {"all", "a", "*"}:
            audio_mode = "4"
        elif audio_value in {"none", "n", "no", "remove", "-"}:
            audio_mode = "5"
        else:
            audio_mode = "3"
            audio_indexes = mux_parse_csv_int(audio_value)

        answers["_question_number"] = 4
        subtitle_indexes_available = mux_stream_indexes(media_files, "subtitle")
        if not subtitle_indexes_available:
            subtitle_mode = "1"
            appio.note("No subtitle streams found; selecting none.")
        else:
            subtitle_value = mux_ask_text(
                answers,
                "Subtitle stream indexes to keep",
                f"examples: {example_text('0,3,4')}; all; none; available: {example_text(','.join(map(str, subtitle_indexes_available)) or 'none')}; use b to go back",
                zero_is_value=True,
            ).lower()
            if subtitle_value in {"all", "a", "*"}:
                subtitle_mode = "5"
            elif subtitle_value in {"none", "n", "no", "remove", "-"}:
                subtitle_mode = "1"
            else:
                subtitle_mode = "4"
                subtitle_indexes = mux_parse_csv_int(subtitle_value)
    else:
        if len(audio_languages) <= 1:
            answers["_question_number"] = 3
            if audio_languages:
                audio_mode = "1"
                audio_language_values = list(audio_languages)
                appio.note(f"Only one audio language found; keeping audio language: {', '.join(audio_language_values)}")
            else:
                audio_mode = "5"
                appio.note("No audio streams found; selecting no audio.")
        else:
            answers["_question_number"] = 3
            audio_mode = mux_ask_choice(
                answers,
                "Choose audio mode",
                f"1=by language; 2=by title; 3=by exact stream indexes; 4=keep all; 5=remove all; found languages: {example_text(','.join(audio_languages) or 'none')}",
                {"1", "2", "3", "4", "5"},
                "1",
            )
            if audio_mode == "1":
                answers["_question_number"] = 4
                audio_language_values = mux_parse_csv_text(mux_ask_text(answers, "Audio language codes to keep", "example: jpn,eng,fas"))
            elif audio_mode == "2":
                answers["_question_number"] = 4
                audio_titles = mux_parse_csv_text(mux_ask_text(answers, "Audio title text to keep", "example: japanese,commentary"))
            elif audio_mode == "3":
                answers["_question_number"] = 4
                audio_indexes = mux_parse_csv_int(mux_ask_text(answers, "Audio stream indexes to keep", "example: 0,2,3; use b to go back", zero_is_value=True))

        answers["_question_number"] = 5
        if not subtitle_languages:
            subtitle_mode = "1"
            appio.note("No subtitle streams found; selecting none.")
        else:
            subtitle_mode = mux_ask_choice(
                answers,
                "Choose subtitle mode",
                f"1=remove all; 2=by language; 3=by title; 4=by exact stream indexes; 5=keep all; found languages: {example_text(','.join(subtitle_languages) or 'none')}",
                {"1", "2", "3", "4", "5"},
                "5",
            )
            if subtitle_mode == "2":
                answers["_question_number"] = 6
                subtitle_language_values = mux_parse_csv_text(mux_ask_text(answers, "Subtitle language codes to keep", "example: eng,fas"))
            elif subtitle_mode == "3":
                answers["_question_number"] = 6
                subtitle_titles = mux_parse_csv_text(mux_ask_text(answers, "Subtitle title text to keep", "example: signs,full"))
            elif subtitle_mode == "4":
                answers["_question_number"] = 6
                subtitle_indexes = mux_parse_csv_int(mux_ask_text(answers, "Subtitle stream indexes to keep", "example: 0,3,4; use b to go back", zero_is_value=True))

    answers["_question_number"] = 7
    if subtitle_mode == "1":
        keep_attachments = False
        appio.note("Subtitle mode removes all subtitles, so font attachments will also be removed.")
    else:
        keep_attachments = mux_ask_yes_no(answers, "Keep MKV font attachments?", True)

    answers["_question_number"] = 8
    keep_metadata = mux_ask_yes_no(answers, "Keep input metadata?", True)
    metadata_context = MuxCleanupRules(
        audio_mode=audio_mode,
        audio_languages=audio_language_values,
        audio_titles=audio_titles,
        audio_indexes=audio_indexes,
        subtitle_mode=subtitle_mode,
        subtitle_languages=subtitle_language_values,
        subtitle_titles=subtitle_titles,
        subtitle_indexes=subtitle_indexes,
        keep_attachments=keep_attachments,
        keep_metadata=keep_metadata,
        keep_chapters=True,
        overwrite=False,
        copy_non_video_files=True,
        selection_style="exact" if selection_style == "1" else "advanced",
    )
    answers["_question_number"] = 9
    metadata_edits = mux_ask_metadata_edits(answers, media_files, metadata_context)
    answers["_question_number"] = int(answers.get("_question_number", 9)) + 1
    keep_chapters = mux_ask_yes_no(answers, "Keep chapters?", True)
    answers["_question_number"] = int(answers.get("_question_number", 10)) + 1
    copy_non_video_files = mux_ask_yes_no(answers, "Copy non-video files to output folder?", True)
    answers["_question_number"] = int(answers.get("_question_number", 11)) + 1
    overwrite = mux_ask_yes_no(answers, "Overwrite existing output files?", False)

    return MuxCleanupRules(
        audio_mode=audio_mode,
        audio_languages=audio_language_values,
        audio_titles=audio_titles,
        audio_indexes=audio_indexes,
        subtitle_mode=subtitle_mode,
        subtitle_languages=subtitle_language_values,
        subtitle_titles=subtitle_titles,
        subtitle_indexes=subtitle_indexes,
        keep_attachments=keep_attachments,
        keep_metadata=keep_metadata,
        keep_chapters=keep_chapters,
        overwrite=overwrite,
        copy_non_video_files=copy_non_video_files,
        selection_style="exact" if selection_style == "1" else "advanced",
        metadata_edits=metadata_edits,
    )


def mux_metadata_edit_applies(edit: MuxStreamMetadataEdit, stream: MuxStreamInfo) -> bool:
    if edit.codec_type != stream.codec_type:
        return False
    if edit.match_indexes:
        return stream.index in edit.match_indexes
    if edit.match_languages:
        wanted = {mux_normalize_language(value) for value in edit.match_languages}
        return mux_normalize_language(stream.language) in wanted
    return True


def mux_metadata_values_for_stream(stream: MuxStreamInfo, rules: MuxCleanupRules) -> tuple[str, str]:
    language = ""
    title = ""
    for edit in rules.metadata_edits:
        if not mux_metadata_edit_applies(edit, stream):
            continue
        if edit.language:
            language = mux_normalize_language(edit.language)
        if edit.title:
            title = edit.title
    return language, title


def mux_add_stream_metadata_options(
    cmd: list[str],
    stream_spec: str,
    stream: MuxStreamInfo,
    rules: MuxCleanupRules,
) -> None:
    language, title = mux_metadata_values_for_stream(stream, rules)
    if language:
        cmd.extend([f"-metadata:{stream_spec}", f"language={language}"])
    if title:
        cmd.extend([f"-metadata:{stream_spec}", f"title={title}"])


def mux_same_stream_indexes(original: list[MuxStreamInfo], selected: list[MuxStreamInfo]) -> bool:
    return [stream.index for stream in original] == [stream.index for stream in selected]


def mux_default_disposition_needs_update(streams: list[MuxStreamInfo]) -> bool:
    if not streams:
        return False
    if streams[0].disposition_default != 1:
        return True
    return any(stream.disposition_default != 0 for stream in streams[1:])


def mux_metadata_edits_need_remux(streams: list[MuxStreamInfo], rules: MuxCleanupRules) -> bool:
    for stream in streams:
        target_language, target_title = mux_metadata_values_for_stream(stream, rules)
        if target_language and mux_normalize_language(stream.language) != mux_normalize_language(target_language):
            return True
        if target_title and (stream.title or "") != target_title:
            return True
    return False


def mux_remux_needed_reasons(
    media: MuxMediaFile,
    rules: MuxCleanupRules,
    audio_keep: list[MuxStreamInfo],
    subtitle_keep: list[MuxStreamInfo],
) -> list[str]:
    reasons: list[str] = []
    if not mux_same_stream_indexes(media.audio_streams, audio_keep):
        reasons.append("audio stream selection changes")
    if not mux_same_stream_indexes(media.subtitle_streams, subtitle_keep):
        reasons.append("subtitle stream selection changes")
    if not rules.keep_attachments and media.attachment_streams:
        reasons.append("attachments are removed")
    if not rules.keep_metadata:
        reasons.append("input metadata is removed")
    if not rules.keep_chapters:
        reasons.append("chapters are removed")
    if mux_default_disposition_needs_update(audio_keep):
        reasons.append("audio default disposition is normalized")
    if mux_default_disposition_needs_update(subtitle_keep):
        reasons.append("subtitle default disposition is normalized")
    if mux_metadata_edits_need_remux([*audio_keep, *subtitle_keep], rules):
        reasons.append("stream metadata is edited")
    return reasons


def mux_stream_rule_part(kind: str, rules: MuxCleanupRules) -> str:
    if kind == "audio":
        if rules.audio_mode == "1" and rules.audio_languages:
            return f"{mux_compact_labels(rules.audio_languages)} Audio"
        if rules.audio_mode == "2" and rules.audio_titles:
            return "Selected Audio"
        if rules.audio_mode == "3" and rules.audio_indexes:
            return "Audio " + "+".join(str(index) for index in rules.audio_indexes[:4])
        if rules.audio_mode == "4":
            return "All Audio"
        if rules.audio_mode == "5":
            return "No Audio"
        return "Audio"
    if rules.subtitle_mode == "1":
        return "No Subs"
    if rules.subtitle_mode == "2" and rules.subtitle_languages:
        return f"{mux_compact_labels(rules.subtitle_languages)} Subs"
    if rules.subtitle_mode == "3" and rules.subtitle_titles:
        return "Selected Subs"
    if rules.subtitle_mode == "4" and rules.subtitle_indexes:
        return "Subs " + "+".join(str(index) for index in rules.subtitle_indexes[:4])
    if rules.subtitle_mode == "5":
        return "All Subs"
    return "Subs"


def mux_selection_suffix(rules: MuxCleanupRules) -> str:
    parts = [mux_stream_rule_part("audio", rules), mux_stream_rule_part("subtitle", rules)]
    compact: list[str] = []
    for part in parts:
        if part and part not in compact:
            compact.append(part)
    return "[" + sanitize_output_stem(" + ".join(compact) if compact else "Muxed") + "]"


def mux_resolve_output_root(input_root: Path, output_base: Path, rules: MuxCleanupRules) -> Path:
    if input_root.is_dir():
        folder_name = sanitize_output_stem(f"{input_root.name} {mux_selection_suffix(rules)}")
        return mux_unique_directory_path(output_base / folder_name)
    return output_base


def mux_make_output_path(input_root: Path, output_root: Path, input_file: Path, rules: MuxCleanupRules) -> Path:
    if input_root.is_file():
        filename = sanitize_output_stem(f"{input_file.stem} {mux_selection_suffix(rules)}") + input_file.suffix
        output_file = output_root / filename
        if paths_same(output_file, input_file):
            output_file = unique_numbered_path(output_file)
        if output_file.exists() and not rules.overwrite:
            output_file = unique_numbered_path(output_file)
        return output_file
    return output_root / input_file.relative_to(input_root)


def mux_copy_extra_files(input_root: Path, output_root: Path, rules: MuxCleanupRules) -> tuple[int, int, int]:
    sources = mux_extra_file_sources(input_root, output_root)
    if not sources:
        return 0, 0, 0
    if shutil.which(ROBOCOPY_BIN) is None:
        log_warn("robocopy was not found in PATH; non-video files were not copied.")
        appio.note("robocopy was not found in PATH; non-video files were not copied.")
        return 0, 0, len(sources)
    destinations: list[Path] = []
    for source in sources:
        try:
            destinations.append(output_root / source.relative_to(input_root))
        except ValueError:
            continue
    before = mux_destination_snapshot(destinations)
    args = [
        ROBOCOPY_BIN,
        str(input_root),
        str(output_root),
        "/E",
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
        "/XF",
    ]
    args.extend(f"*{suffix}" for suffix in sorted(MUX_CLEANUP_VIDEO_EXTS))
    if mux_path_is_under(output_root, input_root):
        args.extend(["/XD", str(output_root)])
    if not rules.overwrite:
        args.extend(["/XC", "/XN", "/XO"])
    result = mux_run_robocopy(args)
    if result.stdout.strip():
        log_debug("robocopy stdout: " + result.stdout.strip())
    if result.stderr.strip():
        log_debug("robocopy stderr: " + result.stderr.strip())
    after = mux_destination_snapshot(destinations)
    copied = skipped = failed = 0
    for destination in destinations:
        before_stat = before.get(destination)
        after_stat = after.get(destination)
        if after_stat is None:
            failed += 1
        elif before_stat is None or after_stat != before_stat:
            copied += 1
        else:
            skipped += 1
    if not mux_robocopy_success(result.returncode):
        failed = max(failed, 1)
        log_warn(f"robocopy failed with exit code {result.returncode}")
    log_info(
        f"Stream Cleanup non-video copy: copied={copied}; skipped={skipped}; "
        f"failed={failed}; returncode={result.returncode}"
    )
    return copied, skipped, failed


def mux_build_ffmpeg_command(
    ffmpeg: str,
    input_file: Path,
    output_file: Path,
    media: MuxMediaFile,
    rules: MuxCleanupRules,
) -> tuple[list[str], list[MuxStreamInfo], list[MuxStreamInfo]]:
    audio_keep = mux_selected_audio_streams(media, rules)
    subtitle_keep = mux_selected_subtitle_streams(media, rules)
    cmd = [ffmpeg, "-hide_banner", "-y" if rules.overwrite else "-n", "-i", str(input_file), "-map", "0:v?"]
    for stream in audio_keep:
        cmd.extend(["-map", f"0:{stream.index}"])
    for stream in subtitle_keep:
        cmd.extend(["-map", f"0:{stream.index}"])
    if rules.keep_attachments:
        cmd.extend(["-map", "0:t?"])
    cmd.extend(["-map_metadata", "0" if rules.keep_metadata else "-1"])
    cmd.extend(["-map_chapters", "0" if rules.keep_chapters else "-1"])
    cmd.extend(["-c", "copy"])
    for index, _stream in enumerate(audio_keep):
        cmd.extend([f"-disposition:a:{index}", "+default" if index == 0 else "-default"])
    for index, _stream in enumerate(subtitle_keep):
        cmd.extend([f"-disposition:s:{index}", "+default" if index == 0 else "-default"])
    for index, stream in enumerate(audio_keep):
        mux_add_stream_metadata_options(cmd, f"s:a:{index}", stream, rules)
    for index, stream in enumerate(subtitle_keep):
        mux_add_stream_metadata_options(cmd, f"s:s:{index}", stream, rules)
    cmd.append(str(output_file))
    return cmd, audio_keep, subtitle_keep


def mux_print_confirm(input_root: Path, output_base: Path, output_root: Path, rules: MuxCleanupRules) -> None:
    mux_print_header("Confirm Stream Cleanup Remux", Color.MUX_CONFIRM_HEADER, "-")
    mux_print_setting("Input", input_root, Color.MUX_INPUT_PATH)
    mux_print_setting("Output base", output_base, Color.MUX_OUTPUT_BASE)
    mux_print_setting("Output root", output_root, Color.MUX_OUTPUT_ROOT)
    mux_print_setting("Audio mode", rules.audio_mode, Color.MUX_MODE)
    mux_print_setting("Audio languages", mux_format_value_list(rules.audio_languages, Color.MUX_AUDIO))
    mux_print_setting("Audio titles", mux_format_value_list(rules.audio_titles, Color.MUX_AUDIO))
    mux_print_setting("Audio indexes", mux_format_value_list(rules.audio_indexes, Color.MUX_AUDIO))
    mux_print_setting("Subtitle mode", rules.subtitle_mode, Color.MUX_MODE)
    mux_print_setting("Subtitle languages", mux_format_value_list(rules.subtitle_languages, Color.MUX_SUBTITLE))
    mux_print_setting("Subtitle titles", mux_format_value_list(rules.subtitle_titles, Color.MUX_SUBTITLE))
    mux_print_setting("Subtitle indexes", mux_format_value_list(rules.subtitle_indexes, Color.MUX_SUBTITLE))
    mux_print_setting("Metadata edits", mux_format_metadata_edits(rules.metadata_edits), Color.CYAN)
    mux_print_setting("Keep attachments", rules.keep_attachments, Color.MUX_TRUE if rules.keep_attachments else Color.MUX_FALSE)
    mux_print_setting("Keep metadata", rules.keep_metadata, Color.MUX_TRUE if rules.keep_metadata else Color.MUX_FALSE)
    mux_print_setting("Keep chapters", rules.keep_chapters, Color.MUX_TRUE if rules.keep_chapters else Color.MUX_FALSE)
    mux_print_setting("Copy non-video files", rules.copy_non_video_files, Color.MUX_TRUE if rules.copy_non_video_files else Color.MUX_FALSE)
    mux_print_setting("Overwrite", rules.overwrite, Color.MUX_FALSE if rules.overwrite else Color.MUX_TRUE)
    log_info(
        "Stream Cleanup confirmed rules: "
        f"input={input_root}; output_base={output_base}; output_root={output_root}; "
        f"audio_mode={rules.audio_mode}; audio_languages={rules.audio_languages}; "
        f"audio_titles={rules.audio_titles}; audio_indexes={rules.audio_indexes}; "
        f"subtitle_mode={rules.subtitle_mode}; subtitle_languages={rules.subtitle_languages}; "
        f"subtitle_titles={rules.subtitle_titles}; subtitle_indexes={rules.subtitle_indexes}; "
        f"keep_attachments={rules.keep_attachments}; keep_metadata={rules.keep_metadata}; "
        f"keep_chapters={rules.keep_chapters}; copy_non_video_files={rules.copy_non_video_files}; "
        f"overwrite={rules.overwrite}; metadata_edits={mux_format_metadata_edits(rules.metadata_edits)}"
    )


def mux_process_files(
    ffmpeg: str,
    media_files: list[MuxMediaFile],
    input_root: Path,
    output_root: Path,
    rules: MuxCleanupRules,
) -> tuple[int, float]:
    mux_print_header("Processing Stream Cleanup Remux", Color.MUX_PROCESS_HEADER)
    started_at = time.perf_counter()
    total = len(media_files)
    succeeded = 0
    skipped = 0
    no_audio = 0
    failed = 0
    remuxed = 0
    copied_unchanged = 0
    output_files_for_size: list[Path] = []
    output_root.mkdir(parents=True, exist_ok=True)
    log_info(f"Stream Cleanup Remux processing started: input={input_root}; output={output_root}; files={total}; rules={rules}")
    for index, media in enumerate(media_files, start=1):
        output_file = mux_make_output_path(input_root, output_root, media.path, rules)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        rel = mux_display_path(input_root, media.path)
        audio_keep = mux_selected_audio_streams(media, rules)
        subtitle_keep = mux_selected_subtitle_streams(media, rules)
        if rules.audio_mode != "5" and not audio_keep and media.audio_streams:
            no_audio += 1
            appio.note(f"[{index}/{total}] Skip no matching audio selected: {rel}")
            log_warn(f"Stream Cleanup no matching audio selected: {media.path}")
            continue
        if output_file.exists() and not rules.overwrite:
            skipped += 1
            appio.note(f"[{index}/{total}] Skip existing output: {rel}")
            log_warn(f"Stream Cleanup Remux skip existing output: {output_file}")
            continue
        remux_reasons = mux_remux_needed_reasons(media, rules, audio_keep, subtitle_keep)
        if not remux_reasons:
            print()
            print(
                f"{paint('[' + str(index) + '/' + str(total) + ']', Color.MUX_GOLD)} "
                f"{paint('Copying unchanged:', Color.MUX_PROCESS_HEADER)} {paint(str(rel), Color.WHITE)}"
            )
            print(paint("  no remux needed", Color.GRAY))
            log_info(f"Stream Cleanup copy unchanged: input={media.path}; output={output_file}")
            try:
                mux_copy_video_without_remux(media.path, output_file)
            except OSError as exc:
                failed += 1
                log_exception(f"Could not copy unchanged video: {media.path} -> {output_file}")
                appio.error(f"FAILED to copy unchanged file: {exc}")
                continue
            succeeded += 1
            copied_unchanged += 1
            output_files_for_size.append(output_file)
            appio.note(f"OK: {output_file}")
            continue
        cmd, audio_keep, subtitle_keep = mux_build_ffmpeg_command(ffmpeg, media.path, output_file, media, rules)
        log_info(
            f"Stream Cleanup Remux file {index}/{total}: input={media.path}; output={output_file}; "
            f"audio_keep={[s.index for s in audio_keep]}; subtitle_keep={[s.index for s in subtitle_keep]}; "
            f"attachments={rules.keep_attachments}; remux_reasons={remux_reasons}"
        )
        if not media.video_streams:
            appio.note(f"[{index}/{total}] Warning: no video stream found: {rel}")
        print()
        print(
            f"{paint('[' + str(index) + '/' + str(total) + ']', Color.MUX_GOLD)} "
            f"{paint('Remuxing:', Color.MUX_PROCESS_HEADER)} {paint(str(rel), Color.WHITE)}"
        )
        print(
            "  "
            + field_text("audio kept", len(audio_keep), Color.MUX_AUDIO)
            + " | "
            + field_text("subtitles kept", len(subtitle_keep), Color.MUX_SUBTITLE)
            + " | "
            + field_text("attachments", "yes" if rules.keep_attachments else "no", Color.PINK)
        )
        duration = services.stream_duration_seconds({}, media.format) or None
        rc, _elapsed = run_ffmpeg_with_progress(cmd, total_duration=duration, label="Stream Cleanup Remux")
        if rc == 0:
            succeeded += 1
            remuxed += 1
            output_files_for_size.append(output_file)
            appio.note(f"OK: {output_file}")
        else:
            failed += 1
            appio.error(f"FAILED: {media.path}. See log file: {_log_file_text()}")
    if rules.copy_non_video_files:
        extra_copied, extra_skipped, extra_failed = mux_copy_extra_files(input_root, output_root, rules)
    else:
        extra_copied = extra_skipped = extra_failed = 0
        log_info("Stream Cleanup non-video file copy skipped by user setting.")
    elapsed = time.perf_counter() - started_at
    if input_root.is_dir():
        original_total_size = mux_path_total_size(input_root, exclude_paths=[output_root])
        output_total_size = mux_path_total_size(output_root)
    else:
        original_total_size = mux_path_total_size(input_root)
        output_total_size = sum(mux_path_total_size(path) for path in output_files_for_size)
    size_delta = output_total_size - original_total_size
    size_delta_text = mux_format_size_difference(size_delta)
    mux_print_header("Stream Cleanup Remux Done", Color.MUX_DONE_HEADER)
    mux_print_setting("Total", total, Color.WHITE)
    mux_print_setting("OK", succeeded, Color.MUX_TRUE)
    mux_print_setting("Remuxed", remuxed, Color.MUX_AZURE)
    mux_print_setting("Copied unchanged", copied_unchanged, Color.LIME)
    mux_print_setting("Skipped", skipped, Color.YELLOW)
    mux_print_setting("No audio match", no_audio, Color.ORANGE)
    mux_print_setting("Failed", failed, Color.RED if failed else Color.MUX_TRUE)
    mux_print_setting("Extra files copied", extra_copied, Color.MUX_AQUA)
    mux_print_setting("Extra files skipped", extra_skipped, Color.YELLOW)
    mux_print_setting("Extra files failed", extra_failed, Color.RED if extra_failed else Color.MUX_TRUE)
    mux_print_setting("Output", output_root, Color.MUX_OUTPUT_ROOT)
    mux_print_setting("Size difference", size_delta_text, Color.MUX_SIZE_DIFF)
    mux_print_setting("Total time elapsed", format_elapsed(elapsed), Color.MUX_ELAPSED)
    log_info(
        f"Stream Cleanup Remux done: total={total}; ok={succeeded}; remuxed={remuxed}; "
        f"copied_unchanged={copied_unchanged}; skipped={skipped}; no_audio_match={no_audio}; "
        f"failed={failed}; extra_copied={extra_copied}; extra_skipped={extra_skipped}; "
        f"extra_failed={extra_failed}; original_size_bytes={original_total_size}; "
        f"output_size_bytes={output_total_size}; size_difference={size_delta_text}; "
        f"size_delta_bytes={size_delta}; elapsed={format_elapsed(elapsed)}; output={output_root}"
    )
    return (1 if failed else 0), elapsed


def mux_verify_output(ffprobe: str, root: Path) -> None:
    files = mux_find_video_files(root)
    mux_print_header("Verify Stream Cleanup Output", Color.MUX_VERIFY_HEADER, "-")
    if not files:
        appio.note("No supported video files found.")
        return
    log_info(f"Stream Cleanup verify output: root={root}; files={len(files)}")
    for path in files:
        media = mux_probe_file(ffprobe, path)
        if media is None:
            continue
        audio_langs = ",".join(display_language(stream.language) for stream in media.audio_streams) or "-"
        subtitle_langs = ",".join(display_language(stream.language) for stream in media.subtitle_streams) or "-"
        print(
            f"{paint(str(mux_display_path(root, path)), Color.MUX_FILE_LINE)} | "
            f"{field_text('video', len(media.video_streams), Color.MAGENTA)} | "
            f"{field_text('audio', len(media.audio_streams), Color.MUX_AUDIO)} "
            f"{paint('[' + audio_langs + ']', Color.MUX_AUDIO)} | "
            f"{field_text('subs', len(media.subtitle_streams), Color.MUX_SUBTITLE)} "
            f"{paint('[' + subtitle_langs + ']', Color.MUX_SUBTITLE)} | "
            f"{field_text('attachments', len(media.attachment_streams), Color.PINK)}"
        )


def _run_mux_cleanup_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    input_root = ask_mux_cleanup_input_path(answers)
    log_info(f"Stream Cleanup Remux input: {input_root}")
    files = mux_find_video_files(input_root)
    if not files:
        appio.error("No supported video files were found.")
        return None
    appio.note(f"Found {len(files)} supported video file(s).")
    media_files = mux_scan_files(answers["ffprobe"], files, answers.get("ffmpeg"))
    if not media_files:
        appio.error("No files could be scanned successfully.")
        return None
    mux_print_scan_report(media_files, input_root)
    mux_print_unique_summary(media_files)

    while True:
        try:
            answers["_mux_next_question_number"] = 2
            rules = mux_configure_rules(answers, media_files)
            output_base = mux_ask_output_base(answers, input_root)
            output_root = mux_resolve_output_root(input_root, output_base, rules)
            mux_print_confirm(input_root, output_base, output_root, rules)
            if not mux_ask_yes_no(answers, "Start Stream Cleanup Remux now?", True):
                appio.note("Stream Cleanup Remux was not started.")
                return None
            break
        except Back:
            appio.note("Back. Returning to stream selection.")
            continue
    result = mux_process_files(answers["ffmpeg"], media_files, input_root, output_root, rules)
    try:
        if mux_ask_yes_no(answers, "Verify output folder now?", True):
            mux_verify_output(answers["ffprobe"], output_root)
    except Back:
        appio.note("Verification skipped.")
    return result


__all__ = [
    'mux_add_stream_metadata_options',
    'mux_ask_metadata_edits',
    'mux_build_ffmpeg_command',
    'mux_configure_rules',
    'mux_copy_extra_files',
    'mux_default_disposition_needs_update',
    'mux_make_output_path',
    'mux_metadata_edit_applies',
    'mux_metadata_edits_need_remux',
    'mux_metadata_values_for_stream',
    'mux_print_confirm',
    'mux_process_files',
    'mux_remux_needed_reasons',
    'mux_resolve_output_root',
    'mux_same_stream_indexes',
    'mux_selection_suffix',
    'mux_stream_rule_part',
    'mux_verify_output',
    '_run_mux_cleanup_mode_impl',
]
