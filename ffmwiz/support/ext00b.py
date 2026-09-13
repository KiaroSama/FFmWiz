"""FFmWiz second-layer helpers (appio-dependent), tier 0.

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

# The facade back-import was deleted: it carried no name this module does
# not already get from the lower tiers above, and it made the facade's
# `__all__` depend on which side was imported first.


def mux_separator_line(color_code: str = Color.MUX_SEPARATOR, char: str = "=") -> str:
    return paint(char * mux_terminal_width(), color_code)


def mux_pair_text(name: str, value: Any, value_color: str = Color.MUX_SETTING_VALUE) -> str:
    return f"{paint(name + ':', Color.GRAY)} {paint(str(value), value_color)}"


def mux_setting_text(name: str, value: Any, value_color: str = Color.MUX_SETTING_VALUE) -> str:
    return f"{paint(name + ':', Color.MUX_SETTING_LABEL)} {paint(str(value), value_color)}"


def mux_format_value_list(values: Any, value_color: str = Color.MUX_SETTING_VALUE) -> str:
    if isinstance(values, (list, tuple, set)):
        if not values:
            return paint("-", Color.GRAY)
        return paint(",".join(str(value) for value in values), value_color)
    if values in (None, "", []):
        return paint("-", Color.GRAY)
    return paint(str(values), value_color)


def mux_parse_csv_int(raw: str) -> list[int]:
    indexes: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            indexes.append(int(item))
        except ValueError:
            appio.note(f"Ignored invalid stream index: {item}")
    return indexes


def mux_ask_choice(answers: dict[str, Any], title: str, details: str, valid: set[str], default: str) -> str:
    prompt_number = mux_assign_prompt_number(answers)
    while True:
        answers["_question_number"] = prompt_number
        value = appio.ask_raw(appio.question_prompt(answers, title, details, default))
        if is_back_value(value):
            raise Back()
        if not value:
            value = default
        lowered = value.lower()
        if lowered in valid:
            return lowered
        appio.error("Enter one of: " + ", ".join(sorted(valid)))


def mux_ask_text(answers: dict[str, Any], title: str, details: str, *, zero_is_value: bool = False) -> str:
    prompt_number = mux_assign_prompt_number(answers)
    back = "back=b, quit=exit" if zero_is_value else "back=0, quit=exit"
    while True:
        answers["_question_number"] = prompt_number
        value = appio.ask_raw(appio.question_prompt(answers, title, details, back=back))
        if zero_is_value:
            if value.lower().strip() in {"b", "back"}:
                raise Back()
        elif is_back_value(value):
            raise Back()
        if value:
            return value
        appio.error("This value cannot be empty.")


def mux_path_total_size(path: Path, exclude_paths: list[Path] | None = None) -> int:
    excludes = list(exclude_paths or [])
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError as exc:
            log_warn(f"Could not read file size for {path}: {exc}")
            return 0
    total = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        if any(mux_path_is_under(child, excluded) for excluded in excludes):
            continue
        try:
            total += child.stat().st_size
        except OSError as exc:
            log_warn(f"Could not read file size for {child}: {exc}")
    return total


def mux_run_robocopy(args: list[str]) -> subprocess.CompletedProcess[str]:
    log_info("robocopy command: " + command_to_text(args))
    return subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_mux_cleanup_mode(base_answers: dict[str, Any]) -> tuple[int, float] | None:
    # Stream Cleanup Remux runs in-process from the ffmwiz.muxcleanup subsystem.
    from ffmwiz.muxcleanup.app import main_menu as mux_main_menu
    from ffmwiz.muxcleanup.prompts import MenuBack as MuxMenuBack, MenuExit as MuxMenuExit
    from ffmwiz.muxcleanup.textutil import set_block_owns_screen as mux_release_screen

    started_at = time.perf_counter()
    old_argv = list(sys.argv)
    try:
        # The Stream Cleanup menu reads sys.argv[1:] for an optional drag/drop
        # input path. Clear FFmWiz's own argv so it always prompts interactively.
        sys.argv = ["MuxCls"]
        log_info("Starting Stream Cleanup Remux.")
        # allow_back turns the tool's first prompt into a way back to the FFmWiz
        # main menu; standalone MuxCls leaves it off because it has nowhere to go.
        summary = mux_main_menu(allow_back=True)
        if summary is None:
            # Cancelled or backed out before anything ran: nothing to report.
            return None
        failed = summary.failed + summary.extra_failed
        log_info(
            f"Stream Cleanup Remux finished: {summary.succeeded} succeeded, {failed} failed."
        )
        return (1 if failed else 0), time.perf_counter() - started_at
    except MuxMenuExit:
        # Inside the wizard "exit this tool" means the tool, not the wizard.
        appio.note("Leaving Stream Cleanup Remux.")
        return None
    except (MuxMenuBack, Back):
        appio.note("Returning to main menu.")
        return None
    except SystemExit as exc:
        # The subsystem exits the process on its own error paths (no video files
        # found, no file could be scanned). No FFmpeg ran, so reporting an FFmpeg
        # return code here would be a lie: stop the tool, keep the wizard.
        code = exc.code if isinstance(exc.code, int) else 1
        if code:
            log_warn(f"Stream Cleanup Remux stopped with exit code {code}.")
            appio.note("Stream Cleanup Remux stopped. Returning to main menu.")
        return None
    except KeyboardInterrupt:
        appio.note("Stream Cleanup Remux cancelled. Returning to main menu.")
        return None
    except Exception as exc:
        # main_menu() is called directly rather than through the subsystem's
        # main(), which is where it keeps this safety net; without it any
        # unhandled error would take the whole wizard down.
        log_exception(f"Stream Cleanup Remux failed: {exc}")
        appio.error(f"Stream Cleanup Remux failed: {exc}")
        return 1, time.perf_counter() - started_at
    finally:
        sys.argv = old_argv
        # The live progress block hides the cursor and claims the screen for the
        # length of a run. Standalone that is released by the process exiting;
        # here the wizard carries on, so it has to be released explicitly.
        mux_release_screen(False)
        if sys.stdout.isatty():
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()


def detect_nvidia_gpu_available(ffmpeg: str, video_encoders: list[str] | None = None) -> bool:
    if video_encoders is not None and not nvenc_encoder_available(video_encoders):
        log_info("GPU auto-detect: FFmpeg does not report NVENC encoders.")
        return False
    encoder = "h264_nvenc"
    encoders = {str(name).lower() for name in (video_encoders or [])}
    if encoders and encoder not in encoders:
        encoder = "hevc_nvenc" if "hevc_nvenc" in encoders else next((name for name in encoders if name.endswith("_nvenc")), encoder)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        # 256x256: NVENC rejects tiny frames ("Frame Dimension less than the
        # minimum supported value"), so a 16x16 probe falsely reported "no GPU"
        # even on cards that fully support NVENC. 256x256 clears the minimum for
        # h264/hevc/av1 NVENC while staying a trivially fast probe.
        "-i",
        "nullsrc=s=256x256:d=0.1",
        "-frames:v",
        "1",
        "-c:v",
        encoder,
        "-f",
        "null",
        os.devnull,
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=6)
    except Exception as exc:
        log_info(f"GPU auto-detect: NVENC probe failed to run: {exc}")
        return False
    available = result.returncode == 0
    log_info(f"GPU auto-detect: NVENC probe encoder={encoder}; available={available}; returncode={result.returncode}")
    return available


def detect_gpu_model_name(ffmpeg: str) -> str | None:
    """Best-effort human-readable NVIDIA GPU model for the startup banner.

    Tries nvidia-smi first (exact marketing name, e.g. "NVIDIA GeForce RTX 4070
    Ti"); if that is unavailable, parses the GPU name FFmpeg prints while
    initialising NVENC. Returns None when no name can be determined.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=5,
        )
        if result.returncode == 0:
            for raw in result.stdout.decode("utf-8", "replace").splitlines():
                name = raw.strip()
                if name:
                    return name
    except Exception as exc:
        log_info(f"GPU model: nvidia-smi query failed: {exc}")
    # Fallback: read the device name from FFmpeg's verbose NVENC init log.
    try:
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "verbose", "-f", "lavfi",
            "-i", "nullsrc=s=256x256:d=0.1", "-frames:v", "1",
            "-c:v", "h264_nvenc", "-f", "null", os.devnull,
        ]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False, timeout=8)
        match = re.search(r"GPU #\d+\s*-\s*<\s*([^>]+?)\s*>", result.stderr.decode("utf-8", "replace"))
        if match:
            return match.group(1).strip()
    except Exception as exc:
        log_info(f"GPU model: FFmpeg NVENC name probe failed: {exc}")
    return None


def ensure_config_file(path: Path) -> None:
    if path.exists():
        return
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    appio.note(f"Created config file: {path}")


def ensure_launcher_file(path: Path) -> None:
    content = launcher_content()
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8-sig")
            if existing == content:
                return
        except UnicodeDecodeError:
            existing = path.read_text(encoding="utf-8", errors="replace")
            if existing == content:
                return
        log_warn(f"Existing launcher file differs from the FFmWiz template; leaving it unchanged: {path}")
        return
    path.write_text(content, encoding="utf-8")
    appio.note(f"Created launcher file: {path}")


def ask_positive_int_or_n(prompt: str, allow_n: bool = True, allow_zero_word: bool = False) -> int | str:
    while True:
        value = appio.ask_required(prompt, allow_n=allow_n)
        lowered = value.lower()
        if lowered == "n" and allow_n:
            return "n"
        if allow_zero_word and value == "00":
            return 0
        if not re.fullmatch(r"\d+", value):
            appio.error("Enter an integer only.")
            continue
        number = int(value)
        if number == 0:
            raise Back()
        return number


def ask_selection(
    prompt: str,
    max_count: int,
    default: list[int],
    allow_none: bool = False,
) -> list[int] | str:
    while True:
        value = appio.ask_raw(prompt)
        lowered = value.lower()
        if not value:
            return default
        if lowered in {"b", "back"}:
            raise Back()
        if lowered == "n" or lowered == "all":
            return "all"
        if allow_none and lowered in {"none", "no", "clear", "delete"}:
            return []

        pieces = [piece.strip() for piece in value.split(",") if piece.strip()]
        if not pieces:
            appio.error("Invalid selection.")
            continue
        if any(not re.fullmatch(r"\d+", piece) for piece in pieces):
            appio.error("Enter numbers separated by commas, for example: 0,1,2")
            continue

        numbers = [int(piece) for piece in pieces]
        bad = [number for number in numbers if number < 0 or number >= max_count]
        if bad:
            appio.error(f"Invalid number(s): {bad}. Allowed range: 0 to {max_count - 1}")
            continue
        return sorted(set(numbers))


def extract_crop_preview_frame(
    answers: dict[str, Any],
    temp_dir: Path,
    width: int,
    height: int,
    timestamp: float | None = None,
) -> Path:
    input_path: Path = answers["input_path"]
    safe_timestamp = max(0.0, timestamp or 0.0)
    output_path = temp_dir / f"crop_preview_{round(safe_timestamp * 1000)}_{width}x{height}.png"

    def run_extract(use_gpu: bool) -> None:
        args = [
            answers["ffmpeg"],
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        if use_gpu:
            args.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])
        if safe_timestamp > 0:
            args.extend(["-ss", f"{safe_timestamp:.3f}", "-noaccurate_seek"])
        video_filter = f"scale={width}:{height}:flags=fast_bilinear"
        if use_gpu:
            video_filter = f"scale_cuda={width}:{height}:format=nv12,hwdownload,format=nv12,format=rgb24"
        args.extend(
            [
            "-i",
            str(input_path),
            "-an",
            "-sn",
            "-dn",
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            video_filter,
            str(output_path),
            ]
        )
        subprocess.run(
            args,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    if answers.get("use_gpu") and not answers.get("_crop_preview_gpu_failed"):
        try:
            run_extract(True)
            return output_path
        except subprocess.CalledProcessError:
            if not answers.get("_crop_preview_gpu_failed"):
                appio.note("GPU crop preview refresh failed once; CPU preview refresh will be used instead.")
                answers["_crop_preview_gpu_failed"] = True

    run_extract(False)
    return output_path


def detected_resolution_limit(answers: dict[str, Any]) -> tuple[tuple[int, int] | None, str]:
    values: list[tuple[int, int]] = []
    items = _folder_validation_items(answers)
    if items:
        for item in items:
            item_answers = _answers_for_folder_item(answers, item)
            if not item_answers.get("video_streams"):
                continue
            try:
                values.append(cropped_source_size(item_answers))
            except Exception:
                log_exception(f"Could not detect source resolution for folder validation: {item.get('path')}")
        if not values:
            return None, "smallest detected source resolution in folder"
        min_width = min(width for width, _height in values)
        min_height = min(height for _width, height in values)
        return (min_width, min_height), "smallest detected source/cropped resolution in folder"

    if not answers.get("video_streams"):
        return None, "detected source resolution"
    try:
        return cropped_source_size(answers), "detected source/cropped resolution"
    except Exception:
        log_exception("Could not detect source resolution for validation")
        return None, "detected source resolution"


def confirm_target_above_source(
    answers: dict[str, Any],
    setting_label: str,
    target_text: str,
    source_text: str,
    source_label: str,
    consequence: str,
) -> bool:
    prompt = appio.question_prompt(
        answers,
        f"Warning: {setting_label} is higher than source. Continue?",
        f"y/n; target {target_text} > {source_label} {source_text}; {consequence}",
        "n",
    )
    confirmed = appio.ask_yes_no(prompt, False)
    if confirmed:
        log_info(f"User confirmed above-source {setting_label}: target={target_text}; source={source_text}; source_label={source_label}")
    else:
        appio.note(f"{setting_label} was not accepted. Returning to the same question.")
    return confirmed


def resolve_output_collision_against_inputs(output_path: Path, input_paths: list[Path], collision_suffix: str) -> Path:
    """Avoid writing the output over any source input, including joined inputs."""
    resolved = resolve_output_collision_for_sources(output_path, list(input_paths), collision_suffix)
    if resolved != output_path:
        log_info(f"Output path matched an input path; using safe output path instead: {resolved}")
    return resolved


def ask_join_folder_path(answers: dict[str, Any]) -> Path | None:
    """Prompt for a folder whose videos will be joined in name order. Returns the
    folder, or None if the user backs out (0/b/Enter)."""
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter folder path (all videos inside are joined in name order)",
                "drag and drop a folder here or paste a path",
            )
        )
        if is_back_value(value, allow_text=True) or not value:
            return None
        folder = terminal_path(value)
        if not folder.exists() or not folder.is_dir():
            appio.error("Folder not found. Enter a valid folder path.")
            continue
        return folder


def print_join_order_list(paths: list[Any]) -> None:
    """Print the videos in the exact order they will be concatenated."""
    ordered = [Path(p) for p in paths if p]
    if len(ordered) < 2:
        return
    print(paint(f"Join order ({len(ordered)} videos, joined top to bottom):", Color.BOLD + Color.CYAN))
    for idx, p in enumerate(ordered, start=1):
        print("  " + paint(f"{idx}. {p.name}", Color.WHITE))


__all__ = [
    'mux_separator_line',
    'mux_pair_text',
    'mux_setting_text',
    'mux_format_value_list',
    'mux_parse_csv_int',
    'mux_ask_choice',
    'mux_ask_text',
    'mux_path_total_size',
    'mux_run_robocopy',
    'run_mux_cleanup_mode',
    'detect_nvidia_gpu_available',
    'detect_gpu_model_name',
    'ensure_config_file',
    'ensure_launcher_file',
    'ask_positive_int_or_n',
    'ask_selection',
    'extract_crop_preview_frame',
    'detected_resolution_limit',
    'confirm_target_above_source',
    'resolve_output_collision_against_inputs',
    'ask_join_folder_path',
    'print_join_order_list',
]


# ext00d holds an overflow slice of this module (split for file size).
from ffmwiz.support import ext00d as _ext00d  # noqa: E402
# Guard the direct-import case: importing this overflow module FIRST
# re-enters the parent while the child has no __all__ yet.
from ffmwiz.support.ext00d import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_ext00d.__all__)
