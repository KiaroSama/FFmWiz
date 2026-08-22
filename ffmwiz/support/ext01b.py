"""FFmWiz second-layer helpers (appio-dependent), tier 1.

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
from ffmwiz.support.ext00 import *  # noqa: F401,F403

from ffmwiz.support.ext01 import *  # sibling helpers  # noqa: F401,F403


def metadata_stream_line(probe_json: dict[str, Any], stream: dict[str, Any]) -> str:
    codec_type = metadata_stream_type(stream)
    tags = metadata_tags(stream)
    parts = [
        field_text("stream index", metadata_stream_index(stream), Color.LIGHT_BLUE),
        field_text("type", codec_type, metadata_type_color(codec_type)),
        field_text("codec", stream.get("codec_name") or "unknown", Color.CYAN),
    ]
    if codec_type != "video":
        parts.extend([
            field_text("language", display_language(tags.get("language")), Color.GREEN),
            field_text("title", tags.get("title") or "unknown", Color.WHITE),
        ])
    parts.extend([
        field_text("disposition", metadata_disposition_summary(stream), Color.YELLOW),
        field_text("spec", metadata_stream_spec(probe_json, stream), Color.MAGENTA),
    ])
    if codec_type == "video":
        parts.extend([
            field_text("size", f"{stream.get('width', '?')}x{stream.get('height', '?')}", Color.LIME),
            field_text("pix_fmt", stream.get("pix_fmt") or "unknown", Color.ORANGE),
            field_text("color_range", stream.get("color_range") or "unknown", Color.COLOR_RANGE_VALUE),
            field_text("color_space", stream.get("color_space") or "unknown", Color.LIGHT_BLUE),
            field_text("color_transfer", stream.get("color_transfer") or "unknown", Color.PINK),
            field_text("color_primaries", stream.get("color_primaries") or "unknown", Color.AQUA),
        ])
    elif codec_type == "audio":
        parts.extend([
            field_text("sample_rate", stream.get("sample_rate") or "unknown", Color.MAGENTA),
            field_text("channels", stream.get("channels") or "unknown", Color.GREEN),
            field_text("channel_layout", stream.get("channel_layout") or "unknown", Color.AQUA),
        ])
    elif codec_type == "subtitle":
        parts.append(field_text("subtitle codec", stream.get("codec_name") or "unknown", Color.LIGHT_YELLOW))
    return " | ".join(str(part) for part in parts)


def metadata_output_path(input_path: Path, suffix: str, output_ext: str | None = None) -> Path:
    ext = output_ext or input_path.suffix or ".mkv"
    if ext and not str(ext).startswith("."):
        ext = "." + str(ext)
    candidate = input_path.with_name(f"{sanitize_output_stem(input_path.stem)}{suffix}{ext}")
    candidate = resolve_output_collision_against_inputs(candidate, [input_path], suffix or "_metadata")
    return unique_numbered_path(candidate)


def metadata_value_prompt(answers: dict[str, Any], title: str, allow_empty: bool = False) -> str:
    while True:
        value = appio.ask_raw(metadata_prompt(answers, title, back="back=b, quit=exit"))
        if value.lower().strip() in {"b", "back"}:
            raise Back()
        if value or allow_empty:
            return value
        appio.error("This value cannot be empty.")


def ask_lossless_split_ext(answers: dict[str, Any], codec_name: str) -> str:
    """Ask which output container extension to use for the lossless audio split,
    listing only extensions that can hold the source codec WITHOUT re-encoding.
    Caches the answer; raises Back on '0'."""
    choices = lossless_audio_copy_ext_choices(codec_name, Path(answers["input_path"]).suffix)
    default = str(answers.get("lossless_split_ext") or choices[0]).lower().lstrip(".")
    if default not in choices:
        choices = [default] + [c for c in choices if c != default]
    valid = {c.lower() for c in choices}
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Choose output extension for the split audio",
                f"lossless containers for {codec_name or 'this codec'} (no re-encode): {option_list(choices)}",
                default,
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = default
        ext = value.strip().lower().lstrip(".")
        if ext in valid:
            answers["lossless_split_ext"] = ext
            return ext
        appio.error(f"Enter one of: {', '.join(choices)} (these hold {codec_name or 'the source codec'} without re-encoding).")


def ask_manual_cut_layout(answers: dict[str, Any], allow_split: bool = False) -> int:
    """Ask which manual cut layout the user wants. Returns 1..4 (or 5 when
    allow_split and the user chooses lossless split)."""
    print()
    print(paint("Manual cut mode:", Color.BOLD + Color.LIGHT_BLUE))
    print(selection_menu_line(1, "Keep one range") + " " + paint("[1]", Color.GREEN))
    print(selection_menu_line(2, "Remove one range"))
    print(selection_menu_line(3, "Remove multiple ranges"))
    print(selection_menu_line(4, "Keep multiple ranges"))
    valid = {"1", "2", "3", "4"}
    if allow_split:
        print(selection_menu_line(5, "Split into separate files at given times (lossless)"))
        valid.add("5")
    print()
    while True:
        value = appio.ask_raw(
            f"{paint('Selection', Color.BOLD)} {paint('[1]', Color.GREEN)} "
            f"{back_text('0=back, quit=exit')}: "
        )
        if not value:
            return 1
        if is_back_value(value):
            raise Back()
        if value in valid:
            return int(value)
        appio.error("Enter " + ", ".join(sorted(valid)) + ".")


def step_folder_input_path(answers: dict[str, Any]) -> None:
    while True:
        folder_example = example_text(r"D:\Videos\Season 01")
        value = appio.ask_required(
            appio.question_prompt(
                answers,
                "Enter input folder path",
                f"drag and drop a folder here or paste a path; example: {folder_example}",
            )
        )
        folder_path = terminal_path(value)
        if not folder_path.exists() or not folder_path.is_dir():
            appio.error("Folder not found. Enter the full folder path again.")
            continue
        answers["folder_input_path"] = folder_path
        return


def step_folder_output_location(answers: dict[str, Any]) -> None:
    input_folder: Path = answers["folder_input_path"]
    default_output = folder_default_output_path(input_folder)
    folder_example = example_text(r"E:\output")
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter output folder",
                f"Enter=create sibling folder named {default_output.name}; example: {folder_example}",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            output_folder = default_output
        else:
            output_folder = terminal_path(value)
            if not output_folder.is_absolute():
                output_folder = input_folder.parent / output_folder
        if output_folder.exists() and not output_folder.is_dir():
            appio.error("Output path exists and is not a folder. Enter a different folder.")
            continue
        answers["folder_output_location"] = output_folder
        answers["output_location"] = output_folder
        answers.pop("output_name_stem", None)
        return


def ask_metadata_for_added_stream(
    answers: dict[str, Any],
    stream_kind: str,
    stream_index: int,
    stream: dict[str, Any],
) -> dict[str, str]:
    current_language = display_language(stream_tag_value(stream, "language"))
    current_title = stream_tag_value(stream, "title")
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                f"Set {stream_kind} stream {stream_index} metadata",
                (
                    f"{example_text('language,title')} {paint('like', Color.HINT_YELLOW)} {example_text('eng,English')} ; "
                    f"{keep_value_text('Enter=keep current metadata')} ; "
                    f"{field_text('current language', current_language, Color.CYAN)} ; "
                    f"{field_text('title', current_title, Color.MAGENTA)}"
                ),
                None,
                "back=0, quit=exit",
            )
        )
        if is_back_value(value):
            raise RetryAdditionalFile()
        metadata = parse_add_track_metadata(value)
        if value and not metadata:
            appio.error("Enter metadata like eng,English or press Enter to keep current metadata.")
            continue
        return metadata


def describe_additional_track_file_colored(item: dict[str, Any]) -> str:
    parts: list[str] = []
    audio_count = len(item.get("audio_streams") or [])
    subtitle_count = len(item.get("subtitle_streams") or [])
    ignored_video_count = len(item.get("video_streams") or [])
    if audio_count:
        parts.append(field_text("audio streams", audio_count, Color.GREEN))
    if subtitle_count:
        parts.append(field_text("subtitle streams", subtitle_count, Color.MAGENTA))
    if ignored_video_count:
        parts.append(field_text("ignored cover/video streams", ignored_video_count, Color.ORANGE))
    return f" {paint('|', Color.GRAY)} ".join(parts) if parts else paint("no addable streams", Color.RED)


def build_track_manager_command(
    ffmpeg: str,
    input_path: Path,
    remove_specs: list[str],
    extra_items: list[dict[str, Any]],
    output_path: Path,
    answers: dict[str, Any] | None = None,
) -> list[str]:
    """Stream-copy command that maps all source streams except the removed ones
    and appends audio/subtitle streams from external files. Replace = remove the
    old track and add the new one in the same run.

    When loudnorm is enabled (answers['loudnorm_enabled']) the audio streams are
    re-encoded with the loudnorm filter while video/subtitles stay stream-copied.
    """
    cmd: list[str] = [ffmpeg, "-hide_banner", "-y" if OVERWRITE_OUTPUT else "-n", "-i", str(input_path)]
    for item in extra_items:
        cmd.extend(["-i", str(item["path"])])
    cmd.extend(["-map", "0"])
    for spec in remove_specs:
        cmd.extend(["-map", f"-0:{spec}"])
    for input_number, item in enumerate(extra_items, start=1):
        # Required maps (no trailing '?'): if the external file's audio/subtitle
        # stream is missing or undetectable, FFmpeg must fail loudly instead of
        # silently producing output without the replacement track. Map only the
        # FIRST stream of each kind (:a:0 / :s:0) so a multi-track external file
        # adds exactly one audio/subtitle track, matching the metadata prompt
        # which only configures stream 0.
        if item.get("audio_streams"):
            cmd.extend(["-map", f"{input_number}:a:0"])
        if item.get("subtitle_streams"):
            cmd.extend(["-map", f"{input_number}:s:0"])
    keep_metadata = True if answers is None else bool(answers.get("track_manager_keep_metadata", True))
    if keep_metadata:
        cmd.extend(["-map_metadata", "0"])
    else:
        # Drop container/global metadata, chapters, and every output stream's
        # metadata (titles, language tags) for a clean output.
        cmd.extend(["-map_metadata", "-1", "-map_chapters", "-1", "-map_metadata:s", "-1"])
    if answers is not None and loudnorm_transform_enabled(answers):
        # Copy everything, then override audio so loudnorm can re-encode it.
        # The later -c:a wins over the earlier -c copy for audio streams only.
        audio_codec = normalize_audio_codec(
            answers.get("audio_codec"),
            default_audio_codec_for_ext(output_path.suffix.lstrip(".")),
        )
        if audio_codec == "copy":
            audio_codec = DEFAULT_AUDIO_CODEC
        cmd.extend(["-c", "copy", "-c:a", audio_codec])
        bitrate = answers.get("audio_bitrate_kbps")
        if bitrate:
            cmd.extend(["-b:a", f"{int(bitrate)}k"])
        _ar = resolve_audio_sample_rate(answers)
        if _ar:
            cmd.extend(["-ar", str(_ar)])
        # -filter:a applies the loudnorm chain to every mapped audio stream.
        cmd.extend(["-filter:a", build_loudnorm_filter(answers)])
    else:
        cmd.extend(["-c", "copy"])
    cmd.append(str(output_path))
    return cmd


def choose_extract_stream_output_path(input_path: Path, stream: dict[str, Any], value: str, ext: str | None = None) -> Path:
    default_path = default_extract_stream_output_path(input_path, stream, ext)
    default_suffix = default_path.suffix
    if not value:
        candidate = default_path
    else:
        output_value = terminal_path(value)
        if not output_value.drive and not output_value.root and output_value.parent == Path("."):
            if output_value.suffix:
                candidate = input_path.parent / sanitize_output_stem(output_value.stem)
                candidate = candidate.with_suffix(output_value.suffix)
            else:
                candidate = input_path.parent / f"{sanitize_output_stem(output_value.name)}{default_suffix}"
        elif output_value.suffix:
            candidate = output_value.with_name(f"{sanitize_output_stem(output_value.stem)}{output_value.suffix}")
        else:
            candidate = output_value / default_path.name
    candidate = resolve_output_collision_against_inputs(candidate, [input_path], "_Extract")
    return unique_numbered_path(candidate)


def build_extract_jobs(
    entries: list[dict[str, Any]],
    requested: list[int],
    ext_override: str | None,
) -> tuple[list[dict[str, Any]], list[tuple[str, list[int], bool]], list[int]]:
    """Resolve the per-file extraction plan.

    Returns (jobs, per_file_summary, missing_everywhere).
      jobs: list of {path, stream, output_path, multi, fmt}
      per_file_summary: [(file_name, [extracted indexes], multi_bool)]
      missing_everywhere: requested indexes present in NO file
    A file yielding >= 2 streams gets its own "<file.name>" subfolder; a file
    yielding exactly one stream writes next to itself. When a single stream is
    extracted from a single file, ext_override (if any) is honored; otherwise
    each stream keeps its own copy-compatible default container.
    """
    jobs: list[dict[str, Any]] = []
    per_file: list[tuple[str, list[int], bool]] = []
    present_any: set[int] = set()
    single_file_single_stream = len(entries) == 1 and len([i for i in requested if i in entries[0]["by_index"]]) == 1
    for entry in entries:
        got = [i for i in requested if i in entry["by_index"]]
        present_any.update(got)
        if not got:
            continue
        matching = [entry["by_index"][i] for i in got]
        multi = len(matching) >= 2
        # A file with several extracted streams gets its own folder named exactly
        # after the file (e.g. "movie.mkv"). It lives under an "_Extracted" root
        # so the folder name never clashes with the source file that sits in the
        # same directory (a file and a folder cannot share a name).
        out_dir = (entry["path"].parent / "_Extracted" / entry["path"].name) if multi else entry["path"].parent
        for stream in matching:
            if ext_override and single_file_single_stream:
                ext = ext_override
            else:
                ext = extract_stream_container_options(stream)[1]
            idx = stream_global_index(stream)
            ctype = str(stream.get("codec_type") or "stream").lower()
            stem = f"{sanitize_output_stem(entry['path'].stem)}{EXTRACT_STREAM_OUTPUT_SUFFIX}{idx}_{ctype}"
            candidate = out_dir / f"{stem}.{ext.lstrip('.')}"
            candidate = resolve_output_collision_against_inputs(candidate, [entry["path"]], "_Extract")
            candidate = unique_numbered_path(candidate)
            jobs.append({"path": entry["path"], "stream": stream, "output_path": candidate, "multi": multi, "fmt": entry["format"]})
        per_file.append((entry["path"].name, got, multi))
    missing_everywhere = [i for i in requested if i not in present_any]
    return jobs, per_file, missing_everywhere


def print_extract_stream_summary(answers: dict[str, Any], jobs: list[dict[str, Any]]) -> None:
    files = sorted({str(job["path"]) for job in jobs})
    print()
    print(paint("Extract Stream summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("Files", len(files), Color.LIGHT_BLUE))
    print("  " + field_text("Streams to extract", len(jobs), Color.CYAN))
    for job in jobs:
        stream = job["stream"]
        idx = stream_global_index(stream)
        ctype = str(stream.get("codec_type") or "stream")
        label = f"{Path(job['path']).name} #{idx} {ctype}"
        out = Path(job["output_path"])
        # For multi-stream files show the "<file.ext>" subfolder + name; else just name.
        shown = f"{out.parent.name}/{out.name}" if job.get("multi") else out.name
        print("    " + field_text(label, shown, Color.WHITE))
    log_info("Extract Stream plan: " + json.dumps(
        [{"input": str(job["path"]), "index": stream_global_index(job["stream"]),
          "type": job["stream"].get("codec_type"), "codec": job["stream"].get("codec_name"),
          "output": str(job["output_path"])} for job in jobs],
        ensure_ascii=False,
    ))


def step_hardsub_output_location(answers: dict[str, Any]) -> None:
    folder_example = example_text(r"E:\output")
    value = appio.ask_raw(
        appio.question_prompt(
            answers,
            "Enter output path, output folder, or bare output name",
            f"Enter=same folder as input; default suffix {HARDSUB_OUTPUT_SUFFIX}; example: {folder_example}",
        )
    )
    if is_back_value(value):
        raise Back()
    apply_output_location_value(answers, value)


__all__ = [
    'metadata_stream_line',
    'metadata_output_path',
    'metadata_value_prompt',
    'ask_lossless_split_ext',
    'ask_manual_cut_layout',
    'step_folder_input_path',
    'step_folder_output_location',
    'ask_metadata_for_added_stream',
    'describe_additional_track_file_colored',
    'build_track_manager_command',
    'choose_extract_stream_output_path',
    'build_extract_jobs',
    'print_extract_stream_summary',
    'step_hardsub_output_location',
]


# ext01d holds an overflow slice of this module (split for file size).
from ffmwiz.support import ext01d as _ext01d  # noqa: E402
# Guard the direct-import case: importing this overflow module FIRST
# re-enters the parent while the child has no __all__ yet.
_ext01d_names = list(getattr(_ext01d, "__all__", []))
if _ext01d_names:
    from ffmwiz.support.ext01d import *  # noqa: E402,F401,F403
    __all__ = list(__all__) + _ext01d_names
