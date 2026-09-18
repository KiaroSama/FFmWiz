"""FFmWiz -- an interactive FFmpeg command builder for Windows.

Copyright (C) 2026 KiaroSama

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program. If not, see <https://www.gnu.org/licenses/>.
"""
from __future__ import annotations

import sys as _sys

# pyproject declares requires-python >=3.10 and the package uses 3.10 syntax, but
# nothing stopped `python FFmWiz.py` under an older interpreter -- it died on an
# import-time SyntaxError deep inside the package, which reads like a corrupt
# install rather than the real cause. Check before importing anything of ours.
# Kept at the very top and deliberately 3.6-parseable so the message survives.
MINIMUM_PYTHON = (3, 10)
if _sys.version_info < MINIMUM_PYTHON:
    _sys.stderr.write(
        "FFmWiz needs Python {}.{} or newer; this is Python {}.{}.{}.\n"
        "Run it with a newer interpreter, e.g.  py -3 FFmWiz.py\n".format(
            MINIMUM_PYTHON[0], MINIMUM_PYTHON[1], *_sys.version_info[:3])
    )
    raise SystemExit(2)

import datetime
import concurrent.futures
import csv
import atexit
import hashlib
import html
import json
import logging
import math
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse


for _stream in (sys.stdout, sys.stderr):
    try:
        # encoding, not just errors: reconfiguring errors alone left the stream
        # on the console code page, so the progress separator and any non-ASCII
        # path printed as '?' or mojibake under cp437/cp850/cp1252.
        # Windows 10+ consoles render UTF-8 regardless of the active code page.
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass


# Search tag for the large in-code FFmpeg reference:
# FULL_FFMPEG_FORMAT_CODEC_LISTS
#
# Use this tag in your editor if you want to quickly find the long comment
# block that lists FFmpeg formats, muxers, demuxers, encoders, and decoders.


from ffmwiz.core.constants import *  # noqa: F401,F403  (constants extracted to ffmwiz/core/constants.py)
from ffmwiz.core.artifacts import *  # noqa: F401,F403  (temp-artifact ownership)
from ffmwiz.core.colors import *  # noqa: F401,F403  (extracted to ffmwiz/core/colors.py)


from ffmwiz.core.exceptions import *  # noqa: F401,F403  (extracted to ffmwiz/core/exceptions.py)
from ffmwiz.appio import *  # noqa: F401,F403  (interactive I/O + logging foundation)
from ffmwiz import appio  # qualified access for patched primitives


from ffmwiz.support.ext00 import *  # noqa: F401,F403  (appio-dependent helper tier)
from ffmwiz.support.ext01 import *  # noqa: F401,F403  (appio-dependent helper tier)
from ffmwiz.support.ext02 import *  # noqa: F401,F403  (appio-dependent helper tier)
from ffmwiz.support.ext03 import *  # noqa: F401,F403  (appio-dependent helper tier)


from ffmwiz.support.L00_audio import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L00_color_range import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L00_encode_opts import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L00_filters import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L00_metadata import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L00_misc import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L00_naming import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L00_paths import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L00_probe import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L00_split import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L00_streams import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L00_text import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L01_audio import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L01_cover import *  # noqa: F401,F403
from ffmwiz.support.L01_color_range import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L01_encode_opts import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L01_filters import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L01_metadata import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L01_misc import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L01_naming import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L01_paths import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L01_split import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L01_subtitles import *  # noqa: F401,F403
from ffmwiz.support.L01_streams import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L01_text import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L02 import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L03 import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L04 import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L05 import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L06 import *  # noqa: F401,F403  (extracted helper layer)
from ffmwiz.support.L07 import *  # noqa: F401,F403  (extracted helper layer)


from ffmwiz.core.timeline import *  # noqa: F401,F403  (extracted to ffmwiz/core/timeline.py)
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz import runtime  # qualified patched members
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # qualified patched members
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz import runner  # qualified patched members
from ffmwiz.guibridge import *  # noqa: F401,F403
from ffmwiz import guibridge  # qualified patched members
from ffmwiz.trackmanager import *  # noqa: F401,F403
from ffmwiz import trackmanager  # qualified patched members
from ffmwiz.metadata import *  # noqa: F401,F403
from ffmwiz import metadata  # qualified patched members
from ffmwiz.wizard import *  # noqa: F401,F403
from ffmwiz import wizard  # qualified patched members
from ffmwiz.modes import *  # noqa: F401,F403
from ffmwiz import modes  # qualified patched members
from ffmwiz.modes_mediainfo import *  # noqa: F401,F403
from ffmwiz import modes_mediainfo  # qualified patched members
from ffmwiz.modes_join import *  # noqa: F401,F403
from ffmwiz import modes_join  # qualified patched members
from ffmwiz.encoding import *  # noqa: F401,F403
from ffmwiz import encoding  # qualified patched members


# ------------------------------------------------------------------
# Time / cut-range helpers used by the new "Cut video only with copy"
# mode and by the re-encode cut path inside the interactive wizard.
# ------------------------------------------------------------------


# Used only when ffmpeg cannot be probed (e.g. CI without ffmpeg): the encoders
# known to expose -multipass on current FFmpeg/NVENC builds.


from ffmwiz.support.ext04 import *  # noqa: F401,F403
from ffmwiz.support.ext05 import *  # noqa: F401,F403
from ffmwiz.support.ext06 import *  # noqa: F401,F403
from ffmwiz.support.ext07 import *  # noqa: F401,F403
from ffmwiz.support.ext08 import *  # noqa: F401,F403
from ffmwiz.support.ext09 import *  # noqa: F401,F403
from ffmwiz.support.ext10 import *  # noqa: F401,F403
from ffmwiz.support.ext11 import *  # noqa: F401,F403
from ffmwiz.support.ext12 import *  # noqa: F401,F403


# ------------------------------------------------------------------
# Speed / reverse helpers.
# ------------------------------------------------------------------


# Shared per-input audio preparation for Join graphs. Used identically by the
# final Join encode AND the two-pass loudnorm measurement so the analyzed audio
# topology matches the encoded audio exactly.


# ------------------------------------------------------------------
# Shared GUI helpers used by archived Crop/Cut helpers and active editors:
#  - _apply_dark_title_bar: enable Windows DWM immersive dark mode
#    on the window's native frame so the system title bar stops
#    looking bright/system-default.
#  - _PreviewScheduler: debounced, asynchronous, cached frame
#    extraction. Keeps the UI thread responsive while scrubbing.
# ------------------------------------------------------------------


# ============================================================
# Shared professional dark UI palette + helpers used by both
# the archived Cut/Crop helpers and active GUI windows. Colors inspired by
# common Premiere Pro style guides: very dark workspace, soft
# panel surfaces, a single accent (blue) for primary actions,
# and high-contrast text on top.
# ============================================================


# ============================================================
# Layout-independent keyboard shortcuts.
#
# Tkinter's bind("<KeyPress-h>") matches by keysym, which depends on
# the active keyboard layout. When the user is on a Persian or other
# non-Latin layout, the same physical key produces a different keysym
# and the binding silently fails to fire.
#
# To make shortcuts work regardless of layout we bind a single
# <KeyPress> handler that matches BOTH:
#   - event.keycode (the Windows virtual-key code on Windows; the
#     X11 hardware keycode on Linux; layout-independent on both),
#   - event.keysym (lower-cased; layout-dependent but still useful
#     as a portable fallback).
#
# WIN_VK_BY_NAME maps friendly names -> Windows VK codes.
# ============================================================

for _letter in "abcdefghijklmnopqrstuvwxyz":
    WIN_VK_BY_NAME[_letter] = ord(_letter.upper())
for _digit in "0123456789":
    WIN_VK_BY_NAME[_digit] = ord(_digit)


# ============================================================
# PySide6 GUI bridge.
#
# The dedicated GUI lives in ffmwiz/gui/ffmwiz_gui.py and is launched as a
# subprocess so the Qt and Tk worlds never share an event loop. Input
# and output use small JSON files via two --request / --reply CLI args.
#
# If PySide6 is not installed, _launch_qt_gui returns None. Active CLI
# workflows stay in terminal/manual mode; archived helpers may still use
# their legacy Tk implementations when called directly.
# ============================================================


# Module-level cache for the PySide6 availability probe so we only shell
# out once per process. Set to True/False the first time the answer is
# known. Reset to None after a successful install so we re-probe.


# =====================================================================
# Logging system.
#
# Logging is enabled by default and writes to a dated UTF-8 file in the
# Logs/ folder next to this script. The console stays clean and readable;
# everything technical (full FFmpeg stderr, generated commands, tracebacks,
# parameter dumps) goes into the log file.
# =====================================================================


# Secret/credential redaction. Applied to every log record so sensitive values
# never reach the log file even via DEBUG or third-party command echoes. The
# patterns are conservative (a value is masked only when introduced by a known
# sensitive key, a Bearer token, or embedded URL credentials) to avoid mangling
# ordinary FFmpeg arguments such as crf=23.


# =====================================================================
# FFmpeg progress display.
#
# Injects -nostats -progress pipe:1 into an FFmpeg command so the
# binary writes machine-readable key=value lines to stdout while the
# console gets a single, in-place updating status line that shows
# FFmpeg-style frame/fps/q/size/time/bitrate/speed/elapsed/ETA fields.
# Raw FFmpeg stderr and compact FFmpeg progress events are captured into the
# main log file.
# =====================================================================


# Once a run commits its final progress line (via _finish_progress_line), late
# repaints from the run loop (e.g. queue-drain ticks after FFmpeg's
# progress=end) must be suppressed so the completed 100% line is printed
# exactly once. _begin_progress_render() resets this at the start of each run.


# ------------------------------------------------------------------
# Shared color-range helpers. The source metadata range is normalized to a
# small internal vocabulary ("tv", "pc", or "" = unspecified). When the source
# range is unknown the interactive wizard asks the user; command builders fall
# back to the historical default (tv) so non-interactive paths are unchanged.
# ------------------------------------------------------------------


# Encoder + container color-range signaling capability. Verified empirically
# against the installed FFmpeg 8.1.1 build by re-encoding the real source and
# inspecting the output with ffprobe:
#
#   container  libx264/h264_nvenc   libx265/hevc_nvenc
#   --------   ------------------   ------------------
#   mp4-like   color_range=unknown  color_range=tv
#   mkv        color_range=tv       color_range=tv
#
# So when FFmWiz omits -color_range, a genuinely unspecified final range is
# only achievable with an H.264 encoder writing to an MP4-like container. HEVC
# encoders always write a default 'tv' VUI range, and the Matroska muxer writes
# a default Colour Range element for limited-range YUV regardless of codec.
# Neither can be suppressed without altering signaling or pixel values
# (-color_range unknown and x265-params do not change this). The "do not force"
# policy is therefore reported honestly per encoder/container combination
# instead of universally claiming an unspecified final range.


# ====================================================================
# FFmpeg capability cache.
#
# Encoder/container color-range signaling behavior is an observation about a
# specific FFmpeg build and runtime environment, not a timeless global fact.
# Results are therefore cached per environment fingerprint (ffmpeg/ffprobe
# build, OS/arch, and GPU/driver for NVENC) and treated as valid only while the
# fingerprint is unchanged. Any FFmpeg/ffprobe/driver/GPU change invalidates the
# relevant entry. The cache is a local, deletable, git-ignored convenience; the
# main workflow never fails merely because a probe failed.
# ====================================================================


# Per-session memo so a single run never re-probes the same combination (used by
# Folder Encode / Split / two-pass which must probe once, not once per item).


# ------------------------------------------------------------------
# Shared pixel-format analysis. Classifies the source and target pixel formats
# and decides whether a format filter is a no-op compatibility constraint or a
# real conversion (bit-depth / chroma-subsampling / colour-model change).
# ------------------------------------------------------------------

# Chroma-subsampling ordering for "reduction" detection (higher = more chroma
# detail). 4:4:4 > 4:2:2 > 4:2:0 / 4:1:1.


# ------------------------------------------------------------------
# Shared SAR/DAR helpers. SAR (sample aspect ratio) describes pixel shape;
# DAR (display aspect ratio) = coded_w/coded_h * SAR. These parse safely and
# never invent values silently (a fallback is always reported by the caller).
# ------------------------------------------------------------------


# SAR/DAR rational approximation tuning. A bounded denominator keeps derived
# ratios stable for anamorphic sources while snapping to clean standard ratios.
# Common pixel/display aspect ratios used for snapping (value -> "W:H").


# ------------------------------------------------------------------
# FFmpeg capability reference generator. Captures the output of the
# main `ffmpeg -<list>` commands into a plain-text reference file
# beside this script. Available codecs/encoders/etc. are build-
# specific, so the only authoritative list is the one produced by the
# user's own ffmpeg.exe.
# ------------------------------------------------------------------


def ask_main_menu(answers: dict[str, Any], config_path: Path) -> int:
    print()
    print(paint("FFmWiz Main menu:", Color.BOLD + Color.LIGHT_BLUE))
    print(f"  {paint('1.', Color.LIGHT_BLUE)} Interactive wizard {paint('[1]', Color.GREEN)}")
    print(f"  {paint('2.', Color.LIGHT_BLUE)} Wizard from config (ask only what is blank)")
    print(f"  {paint('3.', Color.LIGHT_BLUE)} Cut video only with copy")
    print(f"  {paint('4.', Color.LIGHT_BLUE)} Folder Encode")
    print(f"  {paint('5.', Color.LIGHT_BLUE)} Add files to video")
    print(f"  {paint('6.', Color.LIGHT_BLUE)} Extract Stream")
    print(f"  {paint('7.', Color.LIGHT_BLUE)} Media info report")
    print(f"  {paint('8.', Color.LIGHT_BLUE)} Stream Cleanup Remux")
    print(f"  {paint('9.', Color.LIGHT_BLUE)} Hard Sub Encode")
    print(f"  {paint('10.', Color.LIGHT_BLUE)} Video Speed / Reverse")
    print(f"  {paint('11.', Color.LIGHT_BLUE)} Audio Cut / Speed / Reverse")
    print(f"  {paint('12.', Color.LIGHT_BLUE)} Join Audios and Videos")
    print(f"  {paint('13.', Color.LIGHT_BLUE)} Metadata Editor")
    print(f"  {paint('14.', Color.LIGHT_BLUE)} FFmpeg capability cache (diagnostics)")
    print(f"  {paint('15.', Color.LIGHT_BLUE)} Track Manager (remove / add / replace tracks, normalize loudness)")
    print()
    # The main menu has no previous step, so '0=back' is intentionally not
    # advertised. Submenus continue to support 0=back where it makes sense.
    while True:
        value = appio.ask_raw(
            f"{paint('Selection', Color.BOLD)} {paint('[1]', Color.GREEN)} "
            f"{back_text('quit=exit')}: "
        )
        if not value:
            return 1
        if value in {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"}:
            return int(value)
        appio.error("Enter a menu number from 1 to 15.")


# Kept as a thin wrapper for backwards compatibility with any external caller.
def ask_start_mode(answers: dict[str, Any], config_path: Path) -> int:
    return ask_main_menu(answers, config_path)


# ===================================================================
# "Cut video only with copy" mode and cut helpers shared with the
# interactive wizard re-encode path. Stream-copy cuts are very fast
# and lossless but cut boundaries snap to nearby keyframes. The
# re-encode path inside the wizard uses filter_complex/trim/atrim/
# concat for frame-accurate cuts.
# ===================================================================


# Container extensions that can hold each audio codec via stream copy (no
# re-encode). The first entry is the preferred default. Used for the Audio Cut
# lossless-split output-extension prompt.


# Backwards-compatible alias. The original name promised "default No" but the
# project's UX direction is now "default Yes". The alias is kept so any future
# external callers still resolve, but it returns the same default-Yes prompt.
ask_continue_yes_no_default_no = modes.ask_continue_default_yes


# -------------------------------------------------------------------
# Re-encode cut path used by the interactive wizard. Cuts are applied
# via -filter_complex with trim/atrim/concat for frame accuracy.
# -------------------------------------------------------------------


# Containers that accept each codec via -c copy (NO re-encode). The FIRST entry
# is the recommended default shown to the user. A universal MKV/MKA option is
# always appended so a clean copy target always exists.


def run_one_job(base_answers: dict[str, Any], config_path: Path) -> tuple[int, float] | None:
    answers = dict(base_answers)
    answers["_question_number"] = 1
    start_mode = ask_main_menu(answers, config_path)
    if start_mode == 15:
        return run_track_manager_mode(base_answers)
    if start_mode == 14:
        run_capability_cache_menu(base_answers)
        return None
    if start_mode == 13:
        return metadata.run_metadata_editor_mode(base_answers)
    if start_mode == 12:
        return run_join_videos_mode(base_answers)
    if start_mode == 11:
        return run_audio_transform_mode(base_answers)
    if start_mode == 10:
        return run_video_speed_reverse_mode(base_answers)
    if start_mode == 9:
        return run_hardsub_encode_mode(base_answers)
    if start_mode == 8:
        return run_mux_cleanup_mode(base_answers)
    if start_mode == 7:
        run_media_info_mode(base_answers)
        return None
    if start_mode == 6:
        return run_extract_stream_mode(base_answers)
    if start_mode == 5:
        return run_add_files_to_video_mode(base_answers)
    if start_mode == 4:
        return run_folder_encode_mode(base_answers)
    if start_mode == 3:
        return run_copy_cut_mode(base_answers)
    if start_mode == 2:
        ensure_config_file(config_path)
        try:
            wizard_config = parse_env_config(config_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            fail(f"Could not read config file: {config_path}. {exc}")
            return None
    else:
        wizard_config = None
    answers["_question_offset"] = 1
    try:
        run_wizard(answers, config=wizard_config)
    except Back:
        appio.note("Returning to main menu.")
        return None

    cmd = answers["cmd"]
    if not answers.get("start_now", True):
        # A Join or Split reverse does NOT run as the command printed above: it
        # runs as several, and that one carries a full-timeline `reverse` the
        # executor never uses. Calling it "the command" sent the user off to
        # buffer the whole timeline by hand (B04). Export the real stages.
        staged = (answers.get("reverse_video")
                  and (answers.get("join_input_items")
                       or answers.get("separator_points"))
                  and answers.get("output_path"))
        # Three outcomes, not two. `None` used to mean both "not staged" and
        # "the export failed", so a failed export was told the printed one-shot
        # command was ready to run -- the one case where that sentence is
        # actively dangerous, because for a staged job that command buffers the
        # whole timeline the staging exists to avoid (D08).
        export = (export_bounded_reverse_plan(answers, Path(answers["output_path"]))
                  if staged else None)
        if export is None:
            appio.note("FFmpeg was not started. The command above is ready to run manually.")
        elif export.succeeded:
            appio.note(
                "FFmpeg was not started. This job runs as SEVERAL commands, so the "
                "single command above is a readable reference, not the plan: running "
                "it would buffer the whole timeline. The runnable plan was written to:")
            appio.note(f"    {export.script}")
        else:
            appio.error(
                "FFmpeg was not started, and the staged plan could NOT be written: "
                f"{export.error}")
            appio.note(
                "This job runs as SEVERAL commands. The single command above is a "
                "readable reference only -- do NOT run it as a substitute: it would "
                "buffer the whole timeline, which is exactly what the staged plan "
                "avoids. The generated inputs it names have been kept.")
        # Keep, do not clean. This is the PRIMARY dispatcher; only the
        # standalone Mode 12 branch had been fixed, so declining here still
        # deleted the generated concat list, retimed/split subtitles and
        # chapter metadata the printed command names -- the command was dead
        # before the user could read it (F03).
        preserve_artifacts_for_manual_run(answers)
        return None

    print()
    print(paint("Starting FFmpeg...", Color.GREEN))
    # Split points (Split mode): encode each part as its OWN FFmpeg run so every
    # part shows an accurate, clean 0->100% progress line. A single multi-output
    # command reports ambiguous -progress counters across parts, which made the
    # Part-1 percent a wrong byte/bitrate-based guess.
    # ...but only when there is a single input. run_separator_main_encode
    # rebuilds every part with the single-input builder, which ignores
    # join_input_items entirely and clips the range to input 0's duration --
    # a Join + Split job encoded only the first file. The already-built join
    # command contains a correct multi-output split graph.
    # ...and only while the source clock still matches the processed one. Split
    # points are chosen on the FINAL timeline, but the per-part rebuild reads
    # them as SOURCE seconds, so a 2x Split at 1.0 s produced 0.700 s + 1.700 s
    # parts and a reversed Split returned its parts in the original order
    # (F02). The built multi-output graph is planned on the processed clock, so
    # a transformed Split falls through to it.
    if (answers.get("separator_points")
            and not answers.get("join_input_items")
            and split_parts_share_the_source_clock(answers)):
        try:
            return run_separator_main_encode(answers)
        finally:
            cleanup_join_concat_list(answers)
            cleanup_encode_chapter_metadata(answers)
    # Estimate total duration so the progress bar can compute percent / ETA.
    source_duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    if answers.get("join_input_items"):
        source_duration += sum(float(item.get("duration") or 0.0) for item in answers.get("join_input_items") or [])
    processed_duration = final_processed_duration_for_splits(answers, source_duration) if source_duration > 0 else 0.0
    progress_duration = ffmpeg_progress_duration_for_answers(answers, source_duration) if source_duration > 0 else 0.0
    print_ffmpeg_processing_plan(
        answers,
        cmd,
        progress_duration if progress_duration > 0 else None,
        processed_duration if processed_duration > 0 else None,
    )
    log_info(f"Starting FFmpeg encode. Estimated source duration: "
             f"{format_elapsed(source_duration) if source_duration else 'unknown'}; "
             f"estimated processed duration: {format_elapsed(processed_duration) if processed_duration else 'unknown'}; "
             f"progress duration: {format_elapsed(progress_duration) if progress_duration else 'unknown'}")
    try:
        split_progress_fps = None
        if answers.get("separator_points") and progress_duration > 0:
            try:
                split_progress_fps = float(answers.get("fps") or services.get_video_fps(answers))
            except Exception:
                split_progress_fps = None
        split_part_intervals = list(answers.get("split_part_intervals") or [])
        split_progress_part_durations = [
            max(0.0, float(end) - float(start))
            for start, end in split_part_intervals
        ]
        progress_output_paths = [Path(path) for path in (answers.get("split_output_paths") or [])]
        if not progress_output_paths and answers.get("output_path"):
            progress_output_paths = [Path(answers["output_path"])]
        # execute_encode_plan, not a second copy of the selection. This branch
        # duplicated the segmented-reverse test and then called the runner
        # directly, so the shared selector's memory notice never fired on the
        # PRIMARY path -- a joined reverse ran one full-buffer pass while the
        # summary said it ran in short segments (F10).
        return_code, elapsed = execute_encode_plan(
            answers, cmd,
            total_duration=(progress_duration if progress_duration > 0 else None),
            label="FFmpeg encode",
            split_progress_fps=split_progress_fps,
            split_progress_part_durations=split_progress_part_durations,
            initial_detail=ffmpeg_initial_progress_detail(answers, cmd),
            progress_output_paths=progress_output_paths,
        )
    finally:
        cleanup_join_concat_list(answers)
        cleanup_encode_chapter_metadata(answers)
    return return_code, elapsed


def main() -> int:
    cli_args = sys.argv[1:]
    refresh_reference = False
    preview_colors = False
    leftover_args: list[str] = []
    for arg in cli_args:
        if arg in ("--refresh-ffmpeg-reference", "--regen-ffmpeg-reference"):
            refresh_reference = True
        elif arg in ("--preview-colors", "--preview-progress-colors"):
            preview_colors = True
        else:
            leftover_args.append(arg)
    if leftover_args:
        appio.note(f"Ignoring unknown CLI arguments: {leftover_args}")
    if preview_colors:
        preview_console_colors()
        return 0

    title = "FFmpeg Wizard (FFmWiz)"
    terminal_width = shutil.get_terminal_size((80, 20)).columns
    title_padding = max(0, (terminal_width - len(title)) // 2)
    print((" " * title_padding) + paint(title, Color.BOLD + Color.WIZARD_TITLE))
    print(paint("=" * terminal_width, Color.WIZARD_TITLE))

    # Set up logging FIRST so every later action (auto-install, config
    # creation, FFmpeg runs, errors) is captured to a dated UTF-8 file.
    log_file = setup_logging()
    if log_file is not None:
        appio.note(f"Logging to: {log_file}")
    log_environment({"CLI args": cli_args or "(none)"})

    ffmpeg, ffprobe = check_tools()
    log_info(f"FFmpeg: {ffmpeg}", component="Startup")
    log_info(f"FFprobe: {ffprobe}", component="Startup")
    for label, binary in (("FFmpeg", ffmpeg), ("FFprobe", ffprobe)):
        try:
            _ver = subprocess.run([binary, "-hide_banner", "-version"],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=15)
            _first = (_ver.stdout or "").splitlines()
            log_info(f"{label} version: {_first[0].strip() if _first else 'unknown'}", component="Startup")
        except Exception as exc:
            log_warn(f"Could not read {label} version: {exc}", component="Startup")
    config_path = default_config_path()
    launcher_path = default_launcher_path()
    reference_path = default_ffmpeg_reference_path()
    log_info(f"Config path: {config_path}")
    log_info(f"Launcher path: {launcher_path}")
    log_info(f"FFmpeg reference path: {reference_path}")
    try:
        ensure_config_file(config_path)
    except OSError as exc:
        appio.note(f"Could not create config file next to the script: {exc}")
        log_warn(f"ensure_config_file failed: {exc}")
    try:
        ensure_launcher_file(launcher_path)
    except OSError as exc:
        appio.note(f"Could not create launcher file next to the script: {exc}")
        log_warn(f"ensure_launcher_file failed: {exc}")
    try:
        ensure_ffmpeg_reference_file(reference_path, ffmpeg, force=refresh_reference)
    except Exception as exc:
        appio.note(f"Could not refresh FFmpeg reference: {exc}")
        log_warn(f"ensure_ffmpeg_reference_file failed: {exc}")

    # Auto-install PySide6 (the runtime for the active unified/speed/audio
    # GUI windows) on first run so graphical editors are available. This
    # block runs only when PySide6 is missing; subsequent runs detect it
    # via the cached probe and skip the prompt entirely.
    try:
        ensure_pyside6_installed(interactive=True)
    except Exception as exc:
        appio.note(f"PySide6 auto-install check failed: {exc}")

    print_prerequisite_summary(ffmpeg, ffprobe)

    video_encoders = list_encoders(ffmpeg, "video")
    base_answers: dict[str, Any] = {
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "muxers": list_muxers(ffmpeg),
        "video_encoders": video_encoders,
        "audio_encoders": list_encoders(ffmpeg, "audio"),
        "gpu_available": detect_nvidia_gpu_available(ffmpeg, video_encoders),
        "detect_duplicate_audio": True,
    }
    # Resolve the GPU model name once (for the startup banner) so the per-job
    # banner reprint stays instant instead of re-querying nvidia-smi each loop.
    if base_answers["gpu_available"]:
        base_answers["gpu_model"] = detect_gpu_model_name(ffmpeg)
        log_info(f"GPU detected for encoding: {base_answers.get('gpu_model') or 'NVIDIA NVENC GPU'}")
    else:
        base_answers["gpu_model"] = None
        log_info("No usable NVENC GPU detected; CPU encoding will be used.")

    first_run = True
    while True:
        if not first_run:
            print()
            appio.note("Ready for a new job.")
            print()
        print_startup_banner(config_path, launcher_path, base_answers)
        result = run_one_job(base_answers, config_path)
        if result is None:
            appio.note("Returning to the first question.")
            first_run = False
            continue
        return_code, elapsed = result
        print()
        # appio.note() already writes its own INFO record; logging it again
        # put every "Total time elapsed" line in the log twice.
        appio.note(f"Total time elapsed: {format_elapsed(elapsed)}")
        if return_code == 0:
            appio.note("FFmpeg finished successfully. Returning to the first question.")
            log_info("FFmpeg finished successfully.")
        else:
            appio.error(f"FFmpeg finished with exit code {return_code}. Returning to the first question.")
            log_error(f"FFmpeg finished with exit code {return_code}.")
        first_run = False


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExitWizard:
        log_info("Wizard exited via 'exit' command.")
        raise SystemExit(0)
    except KeyboardInterrupt:
        log_info("Wizard interrupted with Ctrl+C.")
        sys.stdout.write("\n")
        raise SystemExit(130)
    except SystemExit:
        raise
    except Exception:
        # Always preserve crash tracebacks in the log file even if the
        # error path above wasn't reached.
        log_exception("Unhandled exception in FFmWiz.main")
        appio.error(f"Unhandled exception. See log file: {_log_file_text()}")
        if os.environ.get("FFMWIZ_DEBUG"):
            traceback.print_exc()
        raise SystemExit(1)


