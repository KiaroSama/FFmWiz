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


__all__ = [
    'mux_add_stream_metadata_options',
    'mux_ask_metadata_edits',
    'mux_configure_rules',
    'mux_default_disposition_needs_update',
    'mux_metadata_edit_applies',
    'mux_metadata_edits_need_remux',
    'mux_metadata_values_for_stream',
    'mux_remux_needed_reasons',
    'mux_same_stream_indexes',
    'mux_selection_suffix',
    'mux_stream_rule_part',
]


# mux_rules_b holds an overflow slice of this module (split for file size).
from ffmwiz import mux_rules_b as _mux_rules_b  # noqa: E402
from ffmwiz.mux_rules_b import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_mux_rules_b.__all__)
