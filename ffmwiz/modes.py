"""FFmWiz modes cluster (extracted from FFmWiz.py, method الف)."""
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

from ffmwiz.core.artifacts import *  # noqa: F401,F403
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


MEDIA_INFO_VALUE_COLORS = [
    Color.CYAN,
    Color.LIME,
    Color.MAGENTA,
    Color.YELLOW,
    Color.AQUA,
    Color.PINK,
    Color.LIGHT_BLUE,
    Color.ORANGE,
]


def run_capability_cache_menu(base_answers: dict[str, Any]) -> None:
    """Diagnostics sub-menu for the FFmpeg capability cache. View / re-probe /
    clear, using the existing 0=Back convention. Never affects user settings,
    logs, or secrets."""
    ffmpeg = base_answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"
    ffprobe = base_answers.get("ffprobe") or shutil.which("ffprobe") or "ffprobe"
    while True:
        print()
        print(paint("FFmpeg capability cache:", Color.BOLD + Color.LIGHT_BLUE))
        print(f"  {paint('1.', Color.LIGHT_BLUE)} View cached capability results")
        print(f"  {paint('2.', Color.LIGHT_BLUE)} Re-probe current encoder/container capabilities")
        print(f"  {paint('3.', Color.LIGHT_BLUE)} Clear capability cache")
        print(f"  {paint('0.', Color.LIGHT_BLUE)} Back")
        value = appio.ask_raw(f"{paint('Selection', Color.BOLD)} {paint('[0]', Color.GREEN)} "
                        f"{back_text('quit=exit')}: ").strip().lower()
        if value in {"", "0", "b", "back"}:
            return
        if value == "1":
            _capability_cache_view(ffmpeg, ffprobe)
        elif value == "2":
            _capability_cache_reprobe(ffmpeg, ffprobe)
        elif value == "3":
            _capability_cache_clear()
        else:
            appio.error("Enter 0, 1, 2, or 3.")


def _capability_cache_reprobe(ffmpeg: str, ffprobe: str) -> None:
    print()
    encoders = ["libx264", "libx265", "h264_nvenc", "hevc_nvenc"]
    containers = ["mp4", "mkv"]
    cache = load_capability_cache()
    for encoder in encoders:
        identity, env_key = services.capability_environment_key(ffmpeg, ffprobe, encoder)
        for ext in containers:
            appio.note("Re-probing %s + %s ..." % (encoder, ext))
            probe = services.probe_color_range_capability(ffmpeg, ffprobe, encoder, ext)
            cap_key = "%s|%s" % (encoder, container_family(ext))
            _store_capability_entry(cache, env_key, identity, cap_key, probe)
            _CAPABILITY_SESSION_MEMO.pop("%s::%s" % (env_key, cap_key), None)
            fr = probe.get("expected_final_range")
            shown = "unspecified" if fr in {"unknown", "", None} else fr
            print("    " + field_text(cap_key, "%s -> %s" % (probe["status"], shown), Color.AQUA))
    save_capability_cache(cache)
    appio.note("Re-probe complete; results cached for the current FFmpeg environment.")


def _capability_cache_clear() -> None:
    # Delete only the exact FFmWiz-owned capability-cache artifacts that the
    # application itself writes: the primary ffmpeg_capabilities.json and the
    # single corrupt-backup the recovery code creates via path.with_suffix(
    # ".corrupt") -> ffmpeg_capabilities.corrupt. Never remove the enclosing
    # .cache directory, ownership markers, or unrelated files.
    path = capability_cache_path()
    owned_files = [path, path.with_suffix(".corrupt")]
    if not any(p.exists() for p in owned_files):
        appio.note("Capability cache is already empty.")
        return
    if not appio.ask_yes_no(yn_prompt("Clear the FFmpeg capability cache?", False), False):
        appio.note("Capability cache not cleared.")
        return
    removed: list[str] = []
    failed: list[str] = []
    for p in owned_files:
        if not p.exists():
            continue  # missing file is a no-op
        try:
            p.unlink()
            removed.append(p.name)
        except OSError as exc:
            failed.append(p.name)
            appio.error("Could not delete capability cache file %s: %s" % (p.name, exc))
    _CAPABILITY_SESSION_MEMO.clear()
    if failed:
        appio.note("Capability cache only partially cleared. Removed: %s. Failed: %s. "
             "The .cache directory, ownership markers, and unrelated files are untouched."
             % (", ".join(removed) or "none", ", ".join(failed)))
    else:
        appio.note("Capability cache cleared (%s). The .cache directory, user settings, logs, "
             "and unrelated files are untouched." % (", ".join(removed) or "nothing"))


def ask_cut_method(answers: dict[str, Any]) -> int:
    """Ask the user how to define cut ranges.

    Returns:
        1 -> enter cut times manually with h:m:s:frame
    """
    print()
    print(paint("Cut video only with copy:", Color.BOLD + Color.LIGHT_BLUE))
    print(f"  {paint('1.', Color.LIGHT_BLUE)} Manual cut using h:m:s:frame {paint('[default]', Color.GREEN)}")
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
        if value == "1":
            return int(value)
        if value.lower() in {"g", "gui", "graphical"}:
            appio.error("The standalone Cut GUI is archived. Use manual cut in this mode.")
            continue
        appio.error("Enter 1.")


def print_cut_summary(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
    remove_ranges: list[tuple[float, float]],
    fps: float,
    duration: float,
    mode_label: str,
    output_path: Path,
) -> None:
    print()
    print(paint("Cut summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("Mode", mode_label, Color.CYAN))
    print("  " + field_text("Input", answers["input_path"], Color.WHITE))
    print("  " + field_text("Output", output_path, Color.LIME))
    print("  " + field_text("Detected FPS", f"{fps:.3f}", Color.MAGENTA))
    print("  " + field_text("Source duration", format_duration(duration), Color.MAGENTA))
    print("  " + field_text("Kept duration", format_duration(total_keep_duration(keep_ranges)), Color.GREEN))
    if remove_ranges:
        print(paint(format_cut_ranges_for_summary(remove_ranges, fps, "Removed ranges"), Color.ORANGE))
    print(paint(format_cut_ranges_for_summary(keep_ranges, fps, "Kept ranges"), Color.LIGHT_BLUE))


def ask_continue_default_yes(answers: dict[str, Any]) -> bool:
    """Ask the user 'Continue? [Y/n]'. Default Yes; pressing Enter continues.

    Returns True to continue, False to cancel. Raises Back when the user enters
    '0' so callers can decide how to handle the previous-step navigation.
    Callers MUST wrap this in try/except Back when they want to return to a
    higher-level menu instead of crashing.
    """
    while True:
        value = appio.ask_raw(
            f"{paint('Continue?', Color.BOLD)} {paint('[Y/n]', Color.GREEN)} "
            f"{back_text('0=back, quit=exit')}: "
        )
        if is_back_value(value):
            raise Back()
        if not value:
            return True
        lowered = value.lower()
        if lowered in {"y", "yes"}:
            return True
        if lowered in {"n", "no"}:
            return False
        appio.error("Enter y or n. Default on Enter: Y (continue).")


def run_copy_cut_mode(
    base_answers: dict[str, Any],
) -> tuple[int, float] | None:
    """Top-level driver for main-menu option 3.

    Only Back from the first Copy Cut question bubbles up to the main menu.
    Later questions handle Back locally so 0 moves one step backward.
    """
    try:
        return _run_copy_cut_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_copy_cut_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    answers["_question_offset"] = 1
    answers.pop("cut_keep_ranges", None)

    keep_ranges: list[tuple[float, float]] = []
    output_path: Path | None = None
    fps = 25.0
    duration = 0.0
    stage = 0
    while True:
        if stage == 0:
            try:
                answers["_question_number"] = 1
                wizard.step_input_path(answers)
            except Back:
                raise
            if not answers.get("video_streams"):
                appio.error("Copy cut mode requires a video stream.")
                continue
            fps = services.get_video_fps(answers)
            duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
            stage = 1
            continue

        if stage == 1:
            try:
                answers["_question_number"] = 2
                wizard.step_output_location(answers)
            except Back:
                stage = 0
                continue
            input_path: Path = answers["input_path"]
            answers["output_ext"] = input_path.suffix.lstrip(".") or "mkv"
            stage = 2
            continue

        if stage == 2:
            appio.note(COPY_CUT_WARNING)
            answers["_question_number"] = 3
            try:
                method = ask_cut_method(answers)
            except Back:
                stage = 1
                continue
            if method == 1:
                try:
                    keep_ranges = services.collect_cut_ranges_terminal(answers, fps, duration)
                except Back:
                    appio.note("Returning to the cut-method menu.")
                    continue
            else:
                appio.error("The standalone Cut GUI is archived. Use manual cut in this mode.")
                continue
            if not keep_ranges:
                appio.note("No keep ranges were produced. Returning to the cut-method menu.")
                continue
            stage = 3
            continue

        answers["output_collision_suffix"] = "_cut"
        output_path = services.build_output_path(answers)
        answers["output_path"] = output_path

        remove_ranges = invert_cut_ranges_to_keep_ranges(keep_ranges, duration) if duration > 0 else []
        print_cut_summary(answers, keep_ranges, remove_ranges, fps, duration, "Stream copy", output_path)

        try:
            proceed = ask_continue_default_yes(answers)
        except Back:
            stage = 2
            continue
        if not proceed:
            appio.note("Operation canceled by user.")
            return None
        break

    started_at = time.perf_counter()
    if output_path is None:
        raise RuntimeError("Copy Cut output path was not resolved.")
    return_code = run_copy_cut(answers, keep_ranges, output_path)
    elapsed = time.perf_counter() - started_at
    return return_code, elapsed


def run_folder_settings_wizard(answers: dict[str, Any]) -> None:
    steps = [
        Step("output_format", lambda a: True, wizard.step_output_format),
        Step("video_codec", output_has_video, wizard.step_video_codec),
        Step("use_gpu", output_has_video, wizard.step_use_gpu),
        Step("unified_video_editor", output_has_video, wizard.step_unified_video_editor_for_encode),
        Step("crop_enabled", output_has_video, wizard.step_crop_enabled),
        Step("crop_top", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_top),
        Step("crop_left", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_left),
        Step("crop_right", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_right),
        Step("crop_bottom", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_bottom),
        Step("video_bitrate", video_reencode_options_applicable, wizard.step_video_bitrate),
        Step("nvenc_multipass", nvenc_multipass_prompt_applicable, step_nvenc_multipass),
        # Same position as the single-file wizard. Folder Encode writes ONE
        # output per file, which is exactly the condition A.15 documents for
        # this question, and `execute_encode_plan` -- the executor every folder
        # job goes through -- already runs `run_cpu_two_pass_ffmpeg`. Only the
        # question was missing, so the batch route could never turn it on while
        # its GPU counterpart above could. `cpu_two_pass_applicable` still hides
        # it for a copy, a GPU encode, cuts, speed and reverse.
        Step("cpu_two_pass", cpu_two_pass_applicable, step_cpu_two_pass),
        Step("resolution", video_reencode_options_applicable, wizard.step_resolution),
        Step("fps", video_reencode_options_applicable, wizard.step_fps),
        Step("video_speed_reverse", output_has_video, wizard.step_video_speed_reverse_for_encode),
        Step("audio_tracks", lambda a: bool(a.get("audio_streams")), step_audio_tracks),
        Step("loudnorm", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_loudnorm),
        Step("audio_cut", audio_only_transform_prompt_applicable, step_audio_cut_for_encode),
        Step("audio_speed_reverse", audio_only_transform_prompt_applicable, step_audio_speed_reverse_for_encode),
        Step("audio_codec", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_audio_codec),
        Step("audio_bitrate", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy" and audio_codec_uses_bitrate(str(a.get("audio_codec") or default_audio_codec_for_ext(a.get("output_ext", "")))), step_audio_bitrate),
        Step("audio_sample_rate", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy", step_audio_sample_rate),
        Step("source_extras", source_extra_policy_applicable, step_source_extra_policy),
        Step("subtitle_tracks", lambda a: output_has_video(a) and source_subtitles_keep_enabled(a) and bool(a.get("subtitle_streams")), step_subtitle_tracks),
        Step("color_range", folder_batch_color_range_applicable, step_folder_batch_color_range),
        Step("start_now", lambda a: True, step_start_folder_now),
    ]

    def next_index(start: int) -> int:
        idx = start
        while idx < len(steps) and not steps[idx].applicable(answers):
            idx += 1
        return idx

    def prev_index(start: int) -> int:
        idx = start
        while idx > 0 and (
            not steps[idx].applicable(answers)
            or is_auto_unified_crop_step(idx)
            or step_is_auto_back_skip(steps[idx], answers)
        ):
            idx -= 1
        return max(0, idx)

    def is_auto_unified_crop_step(pos: int) -> bool:
        return bool(
            answers.get("_unified_video_editor_used")
            and steps[pos].name in {"crop_enabled", "crop_top", "crop_left", "crop_right", "crop_bottom"}
        )

    def question_number(current: int) -> int:
        count = 0
        for pos in range(current + 1):
            if steps[pos].applicable(answers) and not is_auto_unified_crop_step(pos):
                count += 1
        return answers.get("_question_offset", 0) + count

    idx = next_index(0)
    while idx < len(steps):
        try:
            answers["_question_number"] = question_number(idx)
            steps[idx].run(answers)
            apply_unified_video_editor_answers(answers)
            idx = next_index(idx + 1)
        except Back:
            if idx == 0:
                raise
            idx = prev_index(idx - 1)


def run_folder_encode_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_folder_encode_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_folder_encode_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_disable_graphical_editors"] = True
    answers["_folder_encode_mode"] = True
    answers["_quiet_packet_size_probe"] = True
    answers["_question_number"] = 1
    answers["_question_offset"] = 0

    run_folder_input_output_steps(answers)

    folder_path: Path = answers["folder_input_path"]
    output_folder: Path = answers["folder_output_location"]
    items = scan_folder_media_files(base_answers, folder_path, exclude_folder=output_folder)
    if not items:
        appio.error("No supported audio or video files were found in this folder.")
        return None
    print_folder_media_summary(items, base_answers)
    answers["_folder_items"] = items
    appio.note(f"Folder Encode found {len(items)} media file(s). Files will be encoded one at a time.")

    representative = choose_folder_representative(items)
    copy_media_metadata(answers, representative["answers"])
    answers["_folder_representative_path"] = representative["path"]
    answers["_question_offset"] = 2
    while True:
        try:
            run_folder_settings_wizard(answers)
            break
        except Back:
            run_folder_output_step_with_back(answers)
            folder_path = answers["folder_input_path"]
            output_folder = answers["folder_output_location"]
            items = scan_folder_media_files(base_answers, folder_path, exclude_folder=output_folder)
            if not items:
                appio.error("No supported audio or video files were found in this folder.")
                return None
            print_folder_media_summary(items, base_answers)
            answers["_folder_items"] = items
            appio.note(f"Folder Encode found {len(items)} media file(s). Files will be encoded one at a time.")
            representative = choose_folder_representative(items)
            copy_media_metadata(answers, representative["answers"])
            answers["_folder_representative_path"] = representative["path"]

    if not answers.get("start_now", True):
        appio.note("FFmpeg was not started. The example command above is ready to adapt manually.")
        # The printed example command references the inputs the representative
        # build generated (retimed subtitles, extracted chapters). Hand them to
        # the user instead of deleting them, exactly as the single-file wizard
        # does when a run is declined.
        preserve_artifacts_for_manual_run(answers)
        return None
    # Starting automatically: the example command was only ever shown, so the
    # inputs it generated are dead now. They used to survive the whole batch --
    # every folder job shallow-copied this dict and inherited the SAME lease, so
    # nothing was ever released and each ffmwiz_retimed_subs_* directory
    # outlived the run (B15).
    release_artifacts(answers)

    output_folder: Path = answers["folder_output_location"]
    try:
        output_folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        appio.error(f"Could not create output folder: {output_folder}. {exc}")
        return 1, 0.0

    started_at = time.perf_counter()
    failures = 0
    completed = 0
    total = len(items)
    for index, item in enumerate(items, start=1):
        input_path: Path = item["path"]
        print()
        print(paint(f"Folder Encode [{index}/{total}]: {input_path.name}", Color.BOLD + Color.LIGHT_BLUE))
        # One file, one job: its own lease and its own resolved settings.
        # prepare_folder_job_answers() shallow-copies the dict it is given, so
        # handing it the shared settings dict gave every file the SAME lease --
        # releasing after file 1 would have deleted inputs file 2 still needed,
        # so nothing was released at all and every generated directory survived
        # the batch (B15). A shared resolved map contaminated the same way: a
        # fallback the representative or an earlier file needed stayed in force
        # for files that did not need it (B13). Both are opened HERE, on the
        # outer dict, before prepare copies it.
        item_settings = dict(answers)
        item_settings.pop(ARTIFACT_LEASE_KEY, None)
        artifact_lease(item_settings)
        begin_plan(item_settings)
        try:
            try:
                job_answers = prepare_folder_job_answers(item_settings, item)
                ensure_color_range_resolved(job_answers, workflow="Folder Encode")
                log_and_warn_pixel_format(job_answers)
                cmd = build_ffmpeg_command(job_answers)
            except Exception as exc:
                failures += 1
                log_exception(f"Folder Encode could not prepare file: {input_path}")
                appio.error(f"Skipped {input_path.name}: {exc}")
                continue

            total_duration = services.stream_duration_seconds({}, job_answers.get("format")) or 0.0
            log_info(
                f"Folder Encode starting {index}/{total}: input={input_path}; "
                f"output={job_answers.get('output_path')}; duration={total_duration or 'unknown'}"
            )
            print(paint("Starting FFmpeg...", Color.GREEN))
            # Folder Encode used to call the runner directly, so a reversed job ran
            # the full-buffer filter while the UI promised the segmented plan (R09).
            # Imported here, not at module scope: encoding sits ABOVE modes in the
            # layering and imports it, so a top-level import would be circular. By
            # the time a job runs, the package is fully loaded.
            from ffmwiz import encoding as _encoding
            return_code, _elapsed = _encoding.execute_encode_plan(
                job_answers,
                cmd,
                total_duration=(total_duration if total_duration > 0 else None),
                label=f"Folder Encode {index}/{total}",
            )
            if return_code == 0:
                completed += 1
                appio.note(f"Finished {input_path.name}")
            else:
                failures += 1
                appio.error(f"Failed {input_path.name} with exit code {return_code}.")
        finally:
            # Preparation failure, FFmpeg failure, exception and cancellation all
            # land here. job_answers shares this lease, so one release covers both.
            for removed in release_artifacts(item_settings):
                log_debug(f"Folder Encode removed leased temporary artifact: {removed}")

    elapsed = time.perf_counter() - started_at
    print()
    if failures:
        appio.error(f"Folder Encode completed with {completed} success(es) and {failures} failure(s).")
        return 1, elapsed
    appio.note(f"Folder Encode completed successfully: {completed} file(s).")
    return 0, elapsed


def ask_add_files_source_video(answers: dict[str, Any]) -> None:
    while True:
        video_example = example_text('"E:\\Input\\video.mkv"')
        value = appio.ask_required(
            appio.question_prompt(
                answers,
                "Enter source video file path",
                f"drag and drop a video file here or paste a path; example: {video_example}",
            )
        )
        input_path = terminal_path(value)
        if not input_path.exists() or not input_path.is_file():
            appio.error("File not found. Enter the full file path again.")
            continue
        try:
            load_input_metadata(answers, input_path)
        except FFprobeError as exc:
            appio.error(str(exc))
            continue
        except Exception:
            log_exception(f"ffprobe metadata load failed for Add files source video: {input_path}")
            appio.error(f"ffprobe could not read the file. See log file: {_log_file_text()}")
            continue
        if not answers.get("video_streams"):
            appio.error("The first input must contain a video stream.")
            continue
        trackmanager.print_source_info(answers)
        return


def run_add_files_to_video_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    try:
        return _run_add_files_to_video_mode_impl(base_answers)
    except Back:
        appio.note("Returning to main menu.")
        return None


def _run_add_files_to_video_mode_impl(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    ask_add_files_source_video(answers)
    extra_items: list[dict[str, Any]] = []
    while True:
        try:
            extra_items = ask_additional_track_files(answers, extra_items)
        except Back:
            answers["_question_number"] = 1
            ask_add_files_source_video(answers)
            extra_items = []
            continue

        compatibility_errors = add_files_stream_copy_compatibility_errors(answers["input_path"], extra_items)
        if compatibility_errors:
            appio.error("Cannot add these streams without changing container or re-encoding:")
            for message in compatibility_errors:
                appio.error(f"  {message}")
            appio.error("Add files mode keeps the original container and uses stream copy only. No output was created.")
            return None

        output_path = choose_add_files_output_path(answers["input_path"], extra_items)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = build_add_files_to_video_command(
            answers["ffmpeg"],
            answers["input_path"],
            extra_items,
            output_path,
            source_audio_count=len(answers.get("audio_streams") or []),
            source_subtitle_count=len(answers.get("subtitle_streams") or []),
        )
        print_add_files_summary(answers, extra_items, output_path, cmd)
        answers["_question_number"] = len(extra_items) + 3
        try:
            start_now = appio.ask_yes_no(
                appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
                True,
            )
        except Back:
            continue
        if not start_now:
            appio.note("FFmpeg was not started. The command above is ready to run manually.")
            return None
        break

    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    log_info(
        f"Add files to video starting: input={answers['input_path']}; "
        f"output={output_path}; extra_files={len(extra_items)}"
    )
    return run_ffmpeg_with_progress(
        cmd,
        total_duration=(duration if duration > 0 else None),
        label="Add files to video",
    )


__all__ = [
    'ask_add_files_source_video',
    'ask_continue_default_yes',
    'ask_cut_method',
    'print_cut_summary',
    'run_add_files_to_video_mode',
    'run_capability_cache_menu',
    'run_copy_cut_mode',
    'run_folder_encode_mode',
    'run_folder_settings_wizard',
    '_capability_cache_clear',
    '_capability_cache_reprobe',
    '_run_add_files_to_video_mode_impl',
    '_run_copy_cut_mode_impl',
    '_run_folder_encode_mode_impl',
    'MEDIA_INFO_VALUE_COLORS',
]



# Speed/reverse/cut transform mode runners live in a sibling module; re-export
# them so the flat public API (FFmWiz.run_audio_transform_mode, ...) is unchanged.
from ffmwiz import modes_transform  # noqa: E402
from ffmwiz.modes_transform import *  # noqa: E402,F401,F403
__all__ += modes_transform.__all__


# modes_b holds an overflow slice of this module (split for file size).
from ffmwiz import modes_b as _modes_b  # noqa: E402
from ffmwiz.modes_b import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_modes_b.__all__)
