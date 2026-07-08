"""FFmWiz mux_rules overflow (mux_rules_b) — split for file size.

Back-imports mux_rules and is re-exported by it, so every consumer of
`from ffmwiz.mux_rules import *` still sees the full set. Monkeypatch-safe.
"""
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
from ffmwiz.mux_rules import *  # noqa: E402,F401,F403  (back-import)


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
    'mux_resolve_output_root',
    'mux_make_output_path',
    'mux_copy_extra_files',
    'mux_build_ffmpeg_command',
    'mux_print_confirm',
    'mux_process_files',
    'mux_verify_output',
    '_run_mux_cleanup_mode_impl',
]
