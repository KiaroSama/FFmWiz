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


def build_join_copy_command(answers: dict[str, Any], items: list[dict[str, Any]], output_path: Path) -> list[str]:
    output_path = resolve_output_collision_against_inputs(
        output_path,
        [Path(item["path"]) for item in items if item.get("path")],
        answers.get("output_collision_suffix", "_Encode"),
    )
    answers["output_path"] = output_path
    list_path = write_join_concat_list(items, output_path)
    answers["_join_concat_list"] = list_path
    return [
        answers["ffmpeg"],
        "-hide_banner",
        "-y" if OVERWRITE_OUTPUT else "-n",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-map",
        "0",
        "-c",
        "copy",
        str(output_path),
    ]


def print_join_summary(items: list[dict[str, Any]], copy_compatible: bool, reasons: list[str]) -> None:
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
        print("  " + field_text("join mode", "stream copy, no re-encode", Color.GREEN))
    else:
        print("  " + field_text("join mode", "re-encode required", Color.ORANGE))
        for reason in reasons:
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
    'step_hardsub_output_format',
    'step_hardsub_subtitle_source',
    'step_hardsub_fontsdir',
    'step_hardsub_video_codec',
    'step_hardsub_quality',
    'step_hardsub_hdr_handling',
    'step_hardsub_audio_mode',
    'print_startup_banner',
    'build_join_copy_command',
    'print_join_summary',
    'ask_join_frame_rate_policy',
]
