"""FFmWiz services overflow (services_b) — split for file size.

Re-exported by services, so every consumer of `from ffmwiz.services import *`
still sees the full set. Imports only tiers BELOW services -- never services
itself, which is what made this module unimportable on its own.
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
import logging
import atexit
import concurrent.futures
import queue
import threading
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
# The facade back-import was deleted: every name this module uses comes
# from the LOWER tiers above, which the facade only re-exported. Importing
# it here bought nothing and made this module unimportable on its own,
# because the facade ends with `__all__ += <this module>.__all__` and
# reached that line while this module was still on its first statements.


def stream_duration_seconds(stream: dict[str, Any], fmt: dict[str, Any] | None = None) -> float | None:
    for source in (stream, fmt):
        if not source:
            continue
        duration = source.get("duration")
        if duration:
            try:
                return float(duration)
            except ValueError:
                pass
    return None


BITRATE_SIZE_ESTIMATE_NOTE = (
    "Estimated size is approximate. The real output bitrate can differ from the "
    "value you chose; 2-pass encoding brings the actual bitrate closer to the target."
)


def estimated_encode_duration_seconds(answers: dict[str, Any]) -> float | None:
    """Best-effort output duration (seconds) for file-size estimation.

    Starts from the source duration, shortens it by any chosen cut keep-ranges,
    and scales it by a chosen speed factor. Approximate by design: transforms
    selected after this point (e.g. manual cuts asked later) are not yet known.
    Returns None when no reliable source duration is available.
    """
    fmt = answers.get("format")
    base: float | None = None
    video_streams = answers.get("video_streams") or []
    audio_streams = answers.get("audio_streams") or []
    for stream in (video_streams[0] if video_streams else None,
                   audio_streams[0] if audio_streams else None):
        if stream:
            base = stream_duration_seconds(stream, fmt)
            if base:
                break
    if not base:
        base = stream_duration_seconds({}, fmt)
    if not base or base <= 0:
        return None
    duration = float(base)
    keep_ranges = answers.get("cut_keep_ranges") or answers.get("audio_cut_keep_ranges") or []
    try:
        kept = sum(max(0.0, float(end) - float(start)) for start, end in keep_ranges)
    except (TypeError, ValueError):
        kept = 0.0
    if keep_ranges and kept > 0:
        duration = kept
    try:
        speed = float(answers.get("speed_factor") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    if speed > 0:
        duration /= speed
    return duration if duration > 0 else None


def print_encode_size_estimate(answers: dict[str, Any], kbps: float | None, kind: str) -> None:
    """After a video/audio bitrate question, show the approximate output size at
    the entered bitrate plus a note that the real bitrate can differ.

    `kind` is 'video' or 'audio'. The size line is printed only when a duration
    is available; the accuracy note is always shown so the guidance appears after
    every bitrate answer (including folder mode, where a single size is
    meaningless because one setting applies to many files).
    """
    label = "audio" if kind == "audio" else "video"
    duration = estimated_encode_duration_seconds(answers)
    size = estimate_size_bytes_from_bitrate(kbps, duration)
    if size is not None:
        line = (
            f"Estimated {label} size at {int(kbps)} kbps: "
            f"{format_estimated_size(size)}  (over {format_duration(duration)})"
        )
        print("  " + paint(line, Color.LIME))
        log_info(line)
    appio.note(BITRATE_SIZE_ESTIMATE_NOTE)


def default_media_reports_dir() -> Path:
    return script_dir() / MEDIA_REPORTS_DIR_NAME


def build_output_path(answers: dict[str, Any]) -> Path:
    input_path: Path = answers["input_path"]
    output_location: Path = answers["output_location"]
    output_ext = answers["output_ext"]

    if answers.get("output_name_stem"):
        output_path = output_location / f"{sanitize_output_stem(answers['output_name_stem'])}.{output_ext}"
    elif output_location.suffix:
        output_path = output_location.with_suffix("." + output_ext)
        output_path = output_path.with_name(f"{sanitize_output_stem(output_path.stem)}{output_path.suffix}")
    else:
        output_path = output_location / f"{sanitize_output_stem(input_path.stem)}.{output_ext}"

    collision_suffix = answers.get("output_collision_suffix", "_Encode")
    output_path = resolve_output_collision(output_path, input_path, collision_suffix)
    all_input_paths = [input_path] + [Path(item["path"]) for item in answers.get("join_input_items") or [] if item.get("path")]
    output_path = resolve_output_collision_against_inputs(output_path, all_input_paths, collision_suffix)
    log_info(f"Resolved output path: {output_path}")
    return output_path


def metadata_report_output_path(input_path: Path, suffix: str, ext: str) -> Path:
    report_dir = default_media_reports_dir()
    report_dir.mkdir(parents=True, exist_ok=True)
    candidate = report_dir / f"{sanitize_output_stem(input_path.name)}{suffix}{ext}"
    return unique_numbered_path(candidate)


def estimate_color_range(
    input_path: Path,
    video_stream_index: int,
    sampling_mode: str,
    ffmpeg: str,
    use_cuda_decode: bool = False,
    bit_depth: int | None = 8,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    # FFmpeg's signalstats reports YMIN/YMAX in the source pixel format's native
    # bit-depth range (0..2**bits-1), NOT a fixed 0..255. A 10-bit limited-range
    # clip therefore reports YMIN~=64 / YMAX~=940, and a 12-bit one ~256 / ~3760.
    # The limited-vs-full thresholds below are expressed in 8-bit terms, so we
    # normalize the observed values to an 8-bit scale before classifying;
    # otherwise every >8-bit clip is misread as full/PC range.
    output_path = metadata_report_output_path(input_path, f"_color_range_signalstats_stream{video_stream_index}", ".txt")
    args = build_signalstats_command(input_path, video_stream_index, sampling_mode, ffmpeg, output_path, use_cuda_decode)
    log_info("Metadata Editor signalstats command: " + command_to_powershell(args))
    # The signalstats metadata is written to a file, so ffmpeg's stdout is free
    # for -progress: run through the shared progress renderer so this analysis
    # shows the same live percent / time / ETA line as every other FFmpeg run
    # (out_time advances across the source timeline even though frames are
    # sub-sampled). total_duration enables the percentage/ETA; without it the
    # line still shows elapsed time and speed.
    print()
    print(paint("Analyzing decoded pixel statistics for color-range estimation...", Color.LIGHT_BLUE))
    returncode, _elapsed = run_ffmpeg_with_progress(args, total_duration=duration_seconds, label="Color range analysis")
    if returncode != 0 and use_cuda_decode:
        log_error("Color range signalstats CUDA decode failed; retrying with CPU decode.")
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        appio.note("GPU decode failed for this analysis; retrying with CPU decode...")
        args = build_signalstats_command(input_path, video_stream_index, sampling_mode, ffmpeg, output_path, False)
        log_info("Metadata Editor signalstats CPU fallback command: " + command_to_powershell(args))
        returncode, _elapsed = run_ffmpeg_with_progress(args, total_duration=duration_seconds, label="Color range analysis (CPU)")
    if returncode != 0:
        log_error("Color range signalstats failed (see the captured FFmpeg stderr in the log).")
        raise RuntimeError("Color range estimation failed. See log file.")
    values: dict[str, list[float]] = {"YMIN": [], "YLOW": [], "YAVG": [], "YHIGH": [], "YMAX": []}
    if output_path.exists():
        for line in output_path.read_text(encoding="utf-8", errors="replace").splitlines():
            for key in values:
                needle = f"lavfi.signalstats.{key}="
                if needle in line:
                    try:
                        values[key].append(float(line.split("=", 1)[1].strip()))
                    except ValueError:
                        pass
    frames = max((len(item) for item in values.values()), default=0)
    # Absolute extremes across all sampled frames. These are outlier-sensitive:
    # a single fade-to-black or bright flash frame drags min toward 0 / max
    # toward the maximum, which alone must NOT decide the range.
    ymin = min(values["YMIN"]) if values["YMIN"] else None
    ymax = max(values["YMAX"]) if values["YMAX"] else None
    ylow = sum(values["YLOW"]) / len(values["YLOW"]) if values["YLOW"] else None
    yhigh = sum(values["YHIGH"]) / len(values["YHIGH"]) if values["YHIGH"] else None

    def _median(items: list[float]) -> float | None:
        ordered = sorted(items)
        count = len(ordered)
        if count == 0:
            return None
        mid = count // 2
        if count % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2.0

    # Robust per-frame typical floor/ceiling: the median rejects the rare
    # fade/flash frames that make the absolute min/max look full-range.
    median_ymin = _median(values["YMIN"])
    median_ymax = _median(values["YMAX"])

    # signalstats reports luma in the source's native bit depth; scale the
    # decision values to 8-bit equivalents before comparing to 8-bit thresholds.
    depth = bit_depth if isinstance(bit_depth, int) and bit_depth >= 8 else 8
    scale = float(1 << (depth - 8))
    ymin8 = median_ymin / scale if median_ymin is not None else None
    ymax8 = median_ymax / scale if median_ymax is not None else None
    abs_ymin8 = ymin / scale if ymin is not None else None
    abs_ymax8 = ymax / scale if ymax is not None else None

    # Reference points (8-bit): limited/TV luma sits in [16, 235]; full/PC uses
    # [0, 255]. Classify from the robust typical floor/ceiling.
    conclusion = "Estimated range: uncertain"
    if ymin8 is not None and ymax8 is not None:
        if ymin8 >= 8 and ymax8 <= 247:
            conclusion = "Estimated range: probably limited/TV range"
        elif ymin8 <= 5 or ymax8 >= 250:
            conclusion = "Estimated range: possibly full/PC range"
    return {
        "frames": frames, "ymin": ymin, "ymax": ymax, "ylow": ylow, "yhigh": yhigh,
        "bit_depth": depth,
        "median_ymin": median_ymin, "median_ymax": median_ymax,
        "ymin8": ymin8, "ymax8": ymax8,
        "abs_ymin8": abs_ymin8, "abs_ymax8": abs_ymax8,
        "report_path": str(output_path), "conclusion": conclusion,
    }


def collect_cut_ranges_terminal(
    answers: dict[str, Any],
    fps: float,
    duration: float,
    allow_split: bool = False,
) -> list[tuple[float, float]]:
    """Run the terminal manual-cut flow. Returns the final keep_ranges list.
    When allow_split and the user picks the split option, the split points are
    stored in answers['_manual_split_points'] and an empty keep list is
    returned (the caller routes to the lossless split path)."""
    answers.pop("_manual_split_points", None)
    layout = ask_manual_cut_layout(answers, allow_split=allow_split)

    if layout == 5:
        answers["_manual_split_points"] = ask_split_points_terminal(answers, duration)
        return []

    if layout == 1:
        start = ask_hmsf_time(answers, "Keep start time", fps, duration)
        end = ask_hmsf_time(answers, "Keep end time", fps, duration)
        if end <= start:
            appio.error("End must be greater than start.")
            return []
        return normalize_cut_ranges([(start, end)], duration or end)

    if layout == 2:
        start = ask_hmsf_time(answers, "Cut start time", fps, duration)
        end = ask_hmsf_time(answers, "Cut end time", fps, duration)
        if end <= start:
            appio.error("End must be greater than start.")
            return []
        return invert_cut_ranges_to_keep_ranges([(start, end)], duration)

    if layout == 3:
        removes: list[tuple[float, float]] = []
        idx = 1
        while True:
            print()
            print(paint(f"Cut range #{idx}", Color.BOLD + Color.ORANGE))
            start = ask_hmsf_time(answers, "  Cut start", fps, duration)
            end = ask_hmsf_time(answers, "  Cut end", fps, duration)
            if end <= start:
                appio.error("End must be greater than start; this range was ignored.")
            else:
                removes.append((start, end))
            again = appio.ask_yes_no(yn_prompt("Add another remove range?", False), False)
            if not again:
                break
            idx += 1
        return invert_cut_ranges_to_keep_ranges(removes, duration)

    # layout == 4
    keeps: list[tuple[float, float]] = []
    idx = 1
    while True:
        print()
        print(paint(f"Keep range #{idx}", Color.BOLD + Color.LIME))
        start = ask_hmsf_time(answers, "  Keep start", fps, duration)
        end = ask_hmsf_time(answers, "  Keep end", fps, duration)
        if end <= start:
            appio.error("End must be greater than start; this range was ignored.")
        else:
            keeps.append((start, end))
        again = appio.ask_yes_no(yn_prompt("Add another keep range?", False), False)
        if not again:
            break
        idx += 1
    return normalize_cut_ranges(keeps, duration or (keeps[-1][1] if keeps else 0.0))


# Read budget for the header probe: it only reads container metadata, so it
# gets far less room than the packet probe in services.py. An unbounded read
# is what let one wedged probe hang the whole wizard with no way out.
_FFPROBE_JSON_TIMEOUT = 60.0


def ffprobe_json(ffprobe: str, input_path: Path) -> dict[str, Any]:
    args = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        str(input_path),
    ]
    stdout_text = ""
    stderr_text = ""
    decoded_using = "not decoded"
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=_FFPROBE_JSON_TIMEOUT,
        )
        stdout_text, stdout_encoding = decode_subprocess_bytes(result.stdout, "utf-8-sig")
        stderr_text, stderr_encoding = decode_subprocess_bytes(result.stderr, "utf-8")
        decoded_using = f"stdout={stdout_encoding}; stderr={stderr_encoding}"
        if result.returncode != 0:
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe could not read the file. See log file: {_log_file_text()}")
        if not stdout_text.strip():
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe returned no JSON output. See log file: {_log_file_text()}")
        try:
            payload = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using, exc,
            )
            raise FFprobeError(f"ffprobe returned invalid JSON. See log file: {_log_file_text()}") from exc
        if not isinstance(payload, dict):
            log_ffprobe_diagnostics(
                input_path, ffprobe, args, result.returncode,
                stdout_text, stderr_text, decoded_using,
            )
            raise FFprobeError(f"ffprobe returned unexpected JSON. See log file: {_log_file_text()}")
        log_debug(
            f"ffprobe JSON decoded successfully for {input_path}; "
            f"stdout length={len(stdout_text)} stderr length={len(stderr_text)}")
        if os.environ.get("FFMWIZ_DEBUG"):
            try:
                safe_name = sanitize_output_stem(input_path.name)
                stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
                json_path = appio._logs_dir() / f"ffprobe_{stamp}_{safe_name}.json"
                json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                log_info(f"Full ffprobe JSON saved to: {json_path}")
            except Exception as exc:
                log_warn(f"Could not save ffprobe JSON debug file: {exc}")
        if stderr_text.strip():
            log_debug("ffprobe stderr:\n" + stderr_text.rstrip())
        return payload
    except FFprobeError:
        raise
    except Exception as exc:
        log_ffprobe_diagnostics(
            input_path, ffprobe, args, "not available",
            stdout_text, stderr_text, decoded_using, exc,
        )
        raise FFprobeError(f"ffprobe could not read the file. See log file: {_log_file_text()}") from exc


def join_load_media_item(answers: dict[str, Any], path: Path, allow_audio_only: bool = False) -> dict[str, Any]:
    probe = ffprobe_json(answers["ffprobe"], path)
    streams = probe.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
    attachment_streams = [stream for stream in streams if stream.get("codec_type") == "attachment"]
    data_streams = [stream for stream in streams if stream.get("codec_type") == "data"]
    if not video_streams:
        # Audio-only inputs are accepted only when the caller explicitly allows
        # an audio join (the standalone "Join Audios and Videos" mode); the
        # video-encode join paths still require video inputs.
        if not (allow_audio_only and audio_streams):
            if allow_audio_only:
                raise ValueError("This file has no audio or video streams to join.")
            raise ValueError("Join Videos requires video inputs.")
    primary_stream = (video_streams or audio_streams or [{}])[0]
    return {
        "path": path,
        "probe": probe,
        "format": probe.get("format", {}),
        "streams": streams,
        "video_streams": video_streams,
        "audio_streams": audio_streams,
        "subtitle_streams": subtitle_streams,
        "attachment_streams": attachment_streams,
        "data_streams": data_streams,
        "duration": stream_duration_seconds({}, probe.get("format")) or stream_duration_seconds(primary_stream, probe.get("format")) or 0.0,
    }


__all__ = [
    'ffprobe_json',
    'stream_duration_seconds',
    'BITRATE_SIZE_ESTIMATE_NOTE',
    'estimated_encode_duration_seconds',
    'print_encode_size_estimate',
    'default_media_reports_dir',
    'build_output_path',
    'metadata_report_output_path',
    'estimate_color_range',
    'collect_cut_ranges_terminal',
    'join_load_media_item',
]
