"""FFmWiz ext00 overflow (ext00c) — split for file size.

Back-imports ext00 and is re-exported by it, so every consumer of
`from ffmwiz.support.ext00 import *` still sees the full set. Monkeypatch-safe.
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
from ffmwiz.support.ext00 import *  # noqa: E402,F401,F403  (back-import)


def load_capability_cache() -> dict[str, Any]:
    """Load the capability cache, rebuilding safely on corruption or schema drift."""
    path = capability_cache_path()
    empty = {"schema_version": CAPABILITY_CACHE_SCHEMA_VERSION, "environments": {}}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("environments"), dict):
            raise ValueError("unexpected cache structure")
        if data.get("schema_version") != CAPABILITY_CACHE_SCHEMA_VERSION:
            log_info("FFmpeg capability cache schema changed; starting a fresh cache.")
            return empty
        return data
    except Exception as exc:
        log_warn("FFmpeg capability cache is unreadable (%s); rebuilding. File: %s" % (exc, path))
        try:
            shutil.move(str(path), str(path.with_suffix(".corrupt")))
        except Exception:
            pass
        return empty


def save_capability_cache(data: dict[str, Any]) -> bool:
    """Atomically write the capability cache (temp file + flush + replace)."""
    path = capability_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return True
    except Exception as exc:
        log_warn("Could not write FFmpeg capability cache: %s" % exc)
        return False


def log_crop_normalization_summary(answers: dict[str, Any]) -> None:
    """Log the crop normalization decision once before command generation."""
    try:
        lines = crop_normalization_summary_lines(answers)
    except ValueError:
        # Validation errors are surfaced later by the command builders.
        return
    for line in lines:
        log_info(f"Crop normalization | {line}")


def set_crop_margins_if_valid(
    answers: dict[str, Any],
    top: int,
    left: int,
    right: int,
    bottom: int,
) -> bool:
    message = crop_margins_validation_message(answers, top, left, right, bottom)
    if message:
        appio.error(message)
        log_warn(message)
        return False
    answers["crop_enabled"] = any((top, left, right, bottom))
    answers["crop_top"] = top
    answers["crop_left"] = left
    answers["crop_right"] = right
    answers["crop_bottom"] = bottom
    return True


def set_single_crop_margin_if_valid(answers: dict[str, Any], key: str, value: int) -> bool:
    old_marker = object()
    old_value = answers.get(key, old_marker)
    answers[key] = value
    top = int(answers.get("crop_top", 0) or 0)
    left = int(answers.get("crop_left", 0) or 0)
    right = int(answers.get("crop_right", 0) or 0)
    bottom = int(answers.get("crop_bottom", 0) or 0)
    message = crop_margins_validation_message(answers, top, left, right, bottom)
    if not message:
        return True
    if old_value is old_marker:
        answers.pop(key, None)
    else:
        answers[key] = old_value
    appio.error(message)
    log_warn(message)
    return False


def calculate_scale_dimensions(answers: dict[str, Any], resolution: Any) -> tuple[tuple[int, int] | None, str]:
    if resolution is None or resolution == "n":
        return None, ""

    crop_w, crop_h = cropped_source_size(answers)
    answers["crop_box_dimensions"] = (crop_w, crop_h)
    answers["cropped_aspect_ratio"] = crop_w / max(1, crop_h)

    # Use display dimensions (accounting for SAR) for AR-preserving modes.
    # Output pixels are square (setsar=1), so scaled dimensions must reflect
    # the display aspect ratio, not the coded pixel grid.
    sar = source_sar(answers)
    disp_w, disp_h = cropped_display_size(answers)

    warning_parts: list[str] = []
    axis = ""
    if isinstance(resolution, dict):
        mode = resolution.get("mode")
        if mode == "preset":
            # Preset mode: compute AR-preserving dimensions that fit within
            # the preset box. The scale+pad filter handles final canvas.
            width, height, axis = closest_edge_scale_dimensions(
                disp_w,
                disp_h,
                int(resolution.get("width", disp_w) or disp_w),
                int(resolution.get("height", disp_h) or disp_h),
            )
        elif mode == "box":
            # Box mode: target the exact requested canvas dimensions.
            # The scale filter uses force_original_aspect_ratio=decrease to
            # fit the content, then pad fills the canvas. This ensures the
            # output is exactly the requested size without distortion.
            width = even_dimension(int(resolution.get("width", disp_w) or disp_w))
            height = even_dimension(int(resolution.get("height", disp_h) or disp_h))
            axis = "box"
        elif mode == "height":
            height = even_dimension(resolution.get("height", disp_h))
            width = even_dimension(height * disp_w / max(1, disp_h))
            axis = "height"
        elif mode == "width":
            width = even_dimension(resolution.get("width", disp_w))
            height = even_dimension(width * disp_h / max(1, disp_w))
            axis = "width"
        elif mode == "exact_stretch":
            requested_w = int(resolution.get("width", crop_w) or crop_w)
            requested_h = int(resolution.get("height", crop_h) or crop_h)
            width = even_dimension(requested_w)
            height = even_dimension(requested_h)
            axis = "stretch"
            if (width, height) != (requested_w, requested_h):
                warning_parts.append(
                    f"exact stretch resolution adjusted to codec-safe even dimensions: {width}x{height}"
                )
            crop_ar = disp_w / max(1, disp_h)
            out_ar = width / max(1, height)
            if abs(crop_ar - out_ar) / max(crop_ar, 1e-9) > 0.01:
                warning_parts.append(
                    "exact stretch output differs from the cropped aspect ratio and will stretch the image"
                )
        else:
            raise ValueError(f"Unknown resolution mode: {mode!r}")
    elif isinstance(resolution, tuple) and len(resolution) == 2:
        # Backward compatibility for older in-memory callers.
        width = even_dimension(resolution[0])
        height = even_dimension(resolution[1])
        axis = "stretch"
    else:
        raise ValueError(f"Invalid resolution value: {resolution!r}")

    answers["resolution_scale_axis"] = axis
    answers["final_resolution"] = (width, height)
    log_info(
        "Resolution calculation: "
        f"source={first_video_size(answers)}; SAR={sar:.4f}; "
        f"crop_margins={format_crop_margins(answers)}; "
        f"cropped_coded={crop_w}x{crop_h}; cropped_display={disp_w}x{disp_h}; "
        f"mode={resolution}; axis={axis}; "
        f"final={width}x{height}; exact_stretch={'yes' if axis == 'stretch' else 'no'}"
    )
    return (width, height), "; ".join(warning_parts)


def yn_prompt(title: str, default: bool) -> str:
    """Build a colored yes/no sub-prompt that matches the standard wizard style
    (bold title, HINT_YELLOW '(y/n)', green default, back/quit hint), for
    standalone confirmations that do not go through question_prompt."""
    default_text = "y" if default else "n"
    return (
        f"\n{paint(title, Color.BOLD)} "
        f"({paint('y/n', Color.HINT_YELLOW)}) "
        f"{paint('[' + default_text + ']', Color.GREEN)} "
        f"{back_text('back=0, quit=exit')}: "
    )


def selection_menu_line(number: int, label: str) -> str:
    """A numbered menu option line consistent with the rest of the app: a
    sky-blue 'N.' key followed by a bold label."""
    return f"  {paint(str(number) + '.', Color.LIGHT_BLUE)} {paint(label, Color.BOLD)}"


def fail(message: str) -> None:
    appio.error(f"\nERROR: {message}")
    sys.exit(1)


def _run_dependency_install(cmd: list[str], label: str) -> bool:
    print()
    appio.note("Running: " + " ".join(cmd))
    log_info(f"Dependency install command for {label}: {cmd}")
    try:
        result = subprocess.run(cmd, check=False)
    except Exception:
        log_exception(f"Could not start dependency installer for {label}")
        appio.error(f"Could not start {label} installer. See log file: {_log_file_text()}")
        return False
    if result.returncode != 0:
        log_error(f"Dependency installer for {label} exited with code {result.returncode}")
        return False
    return True


def log_ffprobe_diagnostics(
    input_path: Path,
    ffprobe: str,
    args: list[str],
    return_code: int | str | None,
    stdout_text: str,
    stderr_text: str,
    decoded_using: str,
    exc: BaseException | None = None,
) -> None:
    replacement_char = chr(0xFFFD)
    log_error("ffprobe diagnostic block begin")
    log_error(f"  input path: {input_path}")
    log_error(f"  normalized path: {_safe_resolved_path(input_path)}")
    log_error(f"  path exists: {_path_exists_text(input_path)}")
    log_error(f"  ffprobe executable path: {ffprobe}")
    log_error(f"  ffprobe command arguments: {json.dumps(args, ensure_ascii=False)}")
    log_error(f"  return code: {return_code}")
    log_error(f"  decoded using: {decoded_using}")
    log_error(f"  stdout length: {len(stdout_text)}")
    log_error(f"  stderr length: {len(stderr_text)}")
    log_error(f"  stdout replacement characters: {'yes' if replacement_char in stdout_text else 'no'}")
    log_error(f"  stderr replacement characters: {'yes' if replacement_char in stderr_text else 'no'}")
    log_error("  stdout preview:\n" + _text_preview(stdout_text))
    log_error("  stderr preview:\n" + _text_preview(stderr_text))
    if exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log_error("  exception traceback:\n" + tb.rstrip())
    log_error("ffprobe diagnostic block end")


def ffprobe_text_overview(ffprobe: str, input_path: Path) -> str:
    args = [ffprobe, "-hide_banner", str(input_path)]
    log_debug(f"Media Info ffprobe text command: {json.dumps(args, ensure_ascii=False)}")
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout_text, _ = decode_subprocess_bytes(result.stdout, "utf-8")
        stderr_text, _ = decode_subprocess_bytes(result.stderr, "utf-8")
        overview = (stderr_text.strip() or stdout_text.strip() or "(no ffprobe text overview)")
        log_debug(
            f"Media Info ffprobe text overview returncode={result.returncode}; "
            f"length={len(overview)}"
        )
        return overview
    except Exception:
        log_exception(f"Media Info ffprobe text overview failed for {input_path}")
        return "(ffprobe text overview failed; see log file)"


def format_integrated_loudness_line(stats: dict[str, float]) -> str:
    """Build the 'Integrated loudness' summary line. The label is emphasized
    (bold + a distinct emerald green) while the measured value keeps its
    original MEAN_VOLUME color, so the actionable value stands out without
    recoloring the number itself."""
    label = paint("Integrated loudness:", Color.BOLD + Color.MUX_EMERALD)
    value = paint(f"{stats['input_i']:.1f} LUFS", Color.MEAN_VOLUME)
    return "  " + label + " " + value


def audio_hash_segment(
    ffmpeg: str,
    input_path: Path,
    stream_index: int,
    start: float,
    sample_seconds: float,
) -> str | None:
    args = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(input_path),
        "-map",
        f"0:{stream_index}",
        "-t",
        f"{sample_seconds:.3f}",
        "-vn",
        "-sn",
        "-dn",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        "-f",
        "hash",
        "-hash",
        "md5",
        "-",
    ]
    result = subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        log_warn(f"Could not hash audio stream #{stream_index}: {result.stderr.strip()}")
        return None
    match = re.search(r"MD5=([0-9a-fA-F]+)", result.stdout)
    return match.group(1).lower() if match else result.stdout.strip().lower() or None


def audio_hash_full(ffmpeg: str, input_path: Path, stream_index: int) -> str | None:
    args = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(input_path),
        "-map",
        f"0:{stream_index}",
        "-vn",
        "-sn",
        "-dn",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        "-f",
        "hash",
        "-hash",
        "md5",
        "-",
    ]
    result = subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        log_warn(f"Could not full-hash audio stream #{stream_index}: {result.stderr.strip()}")
        return None
    match = re.search(r"MD5=([0-9a-fA-F]+)", result.stdout)
    return match.group(1).lower() if match else result.stdout.strip().lower() or None


def duplicate_labels(index: int, report: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    empty_tracks: set[int] = report.get("empty_tracks", set())
    near_empty_tracks: set[int] = report.get("near_empty_tracks", set())
    if index in empty_tracks:
        labels.append(paint("EMPTY", Color.RED))
    elif index in near_empty_tracks:
        labels.append(paint("NEAR-EMPTY", Color.NEAR_EMPTY))

    confirmed = [pair for pair in report.get("confirmed_pairs", []) if index in pair]
    possible = [pair for pair in report.get("possible_pairs", []) if index in pair and pair not in report.get("confirmed_pairs", [])]
    if confirmed:
        peers = sorted({other for pair in confirmed for other in pair if other != index})
        labels.append(paint(f"CONFIRMED duplicate of {','.join(map(str, peers))}", Color.RED))
    if possible:
        peers = sorted({other for pair in possible for other in pair if other != index})
        labels.append(paint(f"POSSIBLE duplicate of {','.join(map(str, peers))}", Color.YELLOW))
    return labels


def run_media_info_text_command(args: list[str], label: str) -> tuple[int, str, str]:
    log_debug(f"{label} command: {json.dumps(args, ensure_ascii=False)}")
    result = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    stdout_text, stdout_encoding = decode_subprocess_bytes(result.stdout, "utf-8")
    stderr_text, stderr_encoding = decode_subprocess_bytes(result.stderr, "utf-8")
    log_debug(
        f"{label} returncode={result.returncode}; "
        f"stdout_encoding={stdout_encoding}; stderr_encoding={stderr_encoding}; "
        f"stdout_len={len(stdout_text)}; stderr_len={len(stderr_text)}"
    )
    if stderr_text.strip():
        log_debug(f"{label} stderr:\n{stderr_text.rstrip()}")
    return result.returncode, stdout_text, stderr_text


def render_info_report(lines: list[tuple[str, str]], color: bool = True) -> str:
    rendered: list[str] = []
    for text, color_code in lines:
        rendered.append(paint(text, color_code) if color and text else text)
    return "\n".join(rendered)


def ffmpeg_filter_available(ffmpeg: str, filter_name: str) -> bool:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        text, _ = decode_subprocess_bytes(result.stdout, "utf-8")
        return filter_name in text
    except Exception:
        log_exception(f"Could not inspect FFmpeg filters for {filter_name}")
        return False


def media_info_next_prompt(
    answers: dict[str, Any],
    title: str,
    details: str | None = None,
    default: str | None = None,
    back: str = "back=0, quit=exit",
) -> str:
    current = int(answers.get("_question_number", 0) or 0)
    if current < 1:
        current = 1
    answers["_question_number"] = current
    prompt = appio.question_prompt(answers, title, details, default, back)
    answers["_question_number"] = current + 1
    return prompt


__all__ = [
    'load_capability_cache',
    'save_capability_cache',
    'log_crop_normalization_summary',
    'set_crop_margins_if_valid',
    'set_single_crop_margin_if_valid',
    'calculate_scale_dimensions',
    'yn_prompt',
    'selection_menu_line',
    'fail',
    '_run_dependency_install',
    'log_ffprobe_diagnostics',
    'ffprobe_text_overview',
    'format_integrated_loudness_line',
    'audio_hash_segment',
    'audio_hash_full',
    'duplicate_labels',
    'run_media_info_text_command',
    'render_info_report',
    'ffmpeg_filter_available',
    'media_info_next_prompt',
]
