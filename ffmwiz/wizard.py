"""FFmWiz wizard cluster (extracted from FFmWiz.py, method الف)."""
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
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz import runner  # noqa: F401
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz import runtime  # noqa: F401
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.trackmanager import *  # noqa: F401,F403
from ffmwiz import trackmanager  # noqa: F401


@dataclass
class Step:
    name: str
    applicable: Callable[[dict[str, Any]], bool]
    run: Callable[[dict[str, Any]], None]


def step_is_auto_back_skip(step: Step, answers: dict[str, Any]) -> bool:
    """Return True for steps that may complete without showing a prompt."""
    if step.name == "audio_track" and len(answers.get("audio_streams") or []) <= 1:
        return True
    if step.name == "hardsub_audio_container":
        input_path = answers.get("input_path")
        input_ext = Path(input_path).suffix.lstrip(".").lower() if input_path else ""
        output_ext = str(answers.get("output_ext") or "").lstrip(".").lower()
        return answers.get("hardsub_audio_mode") == "none" or bool(input_ext and output_ext and input_ext == output_ext)
    return False


def append_video_encode_options(
    cmd: list[str],
    answers: dict[str, Any],
    video_encoder: str,
    tag: str | None,
    profile: str | None,
) -> None:
    cmd.extend(["-c:v", video_encoder])
    if str(video_encoder).endswith("_nvenc"):
        cmd.extend(["-preset", NVENC_PRESET, "-tune", NVENC_TUNE, "-rc", NVENC_RC])
        append_nvenc_multipass_args(cmd, answers, video_encoder)
        if "hevc" in str(video_encoder):
            cmd.extend(["-profile:v", hevc_profile_for_output(answers, profile)])
    elif video_encoder in {"libx264", "libx265"}:
        cmd.extend(["-preset", CPU_PRESET])
        if video_encoder == "libx265":
            cmd.extend(["-profile:v", hevc_profile_for_output(answers, "main")])
    elif video_encoder == "libsvtav1":
        # SVT-AV1: integer preset (0-13, lower = slower/better) + tune=0 for
        # subjective visual quality. (libaom-style 2-pass is handled separately.)
        cmd.extend(["-preset", SVTAV1_PRESET, "-svtav1-params", SVTAV1_PARAMS])
    video_bitrate = answers.get("video_bitrate_kbps")
    if video_bitrate:
        append_video_bitrate_args(cmd, answers, int(video_bitrate))
    elif answers.get("video_crf") is not None:
        crf_value = answers["video_crf"]
        if str(video_encoder).endswith("_nvenc"):
            cmd[cmd.index("-rc") + 1] = "constqp"
            cmd.extend(["-cq:v", str(int(round(crf_value))), "-b:v", "0"])
        else:
            cmd.extend(["-crf", f"{crf_value:g}"])
    cmd.extend(color_range_output_args(answers, ":v:0", workflow="append_video_encode_options"))
    if tag and str(answers.get("output_ext", "")).lower() in MP4_LIKE_EXTS:
        cmd.extend(["-tag:v", tag])


_MULTIPASS_ENCODER_CACHE: dict[str, bool] = {}


_MULTIPASS_FALLBACK_ENCODERS = {"h264_nvenc", "hevc_nvenc", "av1_nvenc"}


_MULTIPASS_OPTION_RE = re.compile(r"(?m)^\s*-multipass\b")


def encoder_supports_multipass(video_encoder: Any, ffmpeg: str | None = None) -> bool:
    """True if the FFmpeg encoder exposes the -multipass option.

    Detected DYNAMICALLY by probing `ffmpeg -h encoder=<name>` (cached per
    encoder), so ANY current or future encoder that supports multipass gets the
    prompt — not a hardcoded list. Falls back to the known NVENC set only when
    ffmpeg cannot be probed."""
    encoder = str(video_encoder or "").strip().lower()
    if not encoder or encoder in {"copy", "n"}:
        return False
    if encoder in _MULTIPASS_ENCODER_CACHE:
        return _MULTIPASS_ENCODER_CACHE[encoder]
    exe = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
    try:
        result = subprocess.run(
            [exe, "-hide_banner", "-h", f"encoder={encoder}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        text, _ = decode_subprocess_bytes(result.stdout, "utf-8")
        supported = bool(_MULTIPASS_OPTION_RE.search(text))
    except Exception:
        log_exception(f"Could not probe -multipass support for encoder {encoder}")
        supported = encoder in _MULTIPASS_FALLBACK_ENCODERS
    _MULTIPASS_ENCODER_CACHE[encoder] = supported
    log_info(f"Encoder -multipass capability: {encoder}={supported}")
    return supported


def is_nvenc_multipass_encoder(video_encoder: Any) -> bool:
    # Kept for call-site/back-compat; now driven by a dynamic capability probe so
    # every encoder that actually supports -multipass gets the prompt (not just a
    # fixed h264/hevc/av1 NVENC list).
    return encoder_supports_multipass(video_encoder)


def append_nvenc_multipass_args(cmd: list[str], answers: dict[str, Any], video_encoder: Any) -> None:
    if not is_nvenc_multipass_encoder(video_encoder):
        return
    mode = normalize_nvenc_multipass_mode(answers.get("nvenc_multipass"))
    args = nvenc_multipass_args(mode)
    if args:
        log_info(f"NVENC multipass option inserted: encoder={video_encoder}; mode={mode}; args={args}")
    else:
        log_info(f"NVENC multipass disabled for encoder={video_encoder}; no -multipass option inserted.")
    cmd.extend(args)


def nvenc_multipass_applicable_for_encoder(
    answers: dict[str, Any],
    video_encoder: Any,
    *,
    video_reencode: bool = True,
    pure_copy: bool = False,
    workflow_name: str = "video encode",
) -> bool:
    if not video_reencode:
        set_nvenc_multipass_skip_reason(answers, "no video re-encode")
        return False
    if pure_copy:
        set_nvenc_multipass_skip_reason(answers, "pure copy/remux workflow")
        return False
    encoder = str(video_encoder or "").strip().lower()
    if encoder in {"", "copy"}:
        set_nvenc_multipass_skip_reason(answers, "video codec is copy")
        return False
    if not is_nvenc_multipass_encoder(encoder):
        reason = "CPU encoder selected" if not encoder.endswith("_nvenc") else f"unsupported NVENC encoder {encoder}"
        set_nvenc_multipass_skip_reason(answers, reason)
        return False
    answers.pop("nvenc_multipass_skip_reason", None)
    log_info(f"NVENC multipass applicable for {workflow_name}: encoder={encoder}")
    return True


def nvenc_multipass_prompt_applicable(answers: dict[str, Any]) -> bool:
    # Applicability must depend only on the workflow shape (NVENC video
    # re-encode), not on whether the value was already answered. Otherwise the
    # step would vanish during back navigation and shift later question
    # numbers once the user answered it.
    if not output_has_video(answers):
        set_nvenc_multipass_skip_reason(answers, "audio-only workflow")
        return False
    if str(answers.get("video_codec") or "").strip().lower() in {"copy", "n"}:
        set_nvenc_multipass_skip_reason(answers, "video codec is copy")
        return False
    video_encoder = resolved_video_encoder_for_nvenc_multipass(answers)
    return nvenc_multipass_applicable_for_encoder(answers, video_encoder, workflow_name="main encode")


def ask_nvenc_multipass_if_applicable(
    answers: dict[str, Any],
    *,
    video_encoder: Any | None = None,
    workflow_name: str = "video encode",
    video_reencode: bool = True,
    quality_oriented: bool = True,
    pure_copy: bool = False,
) -> str:
    encoder = video_encoder if video_encoder is not None else resolved_video_encoder_for_nvenc_multipass(answers)
    if not nvenc_multipass_applicable_for_encoder(
        answers,
        encoder,
        video_reencode=video_reencode,
        pure_copy=pure_copy,
        workflow_name=workflow_name,
    ):
        return "disabled"
    # Re-prompt on every entry (including back navigation). When a value was
    # already chosen, offer it as the default so pressing Enter keeps it.
    previous_mode = answers.get("nvenc_multipass")
    if previous_mode in NVENC_MULTIPASS_MODES:
        default_mode = normalize_nvenc_multipass_mode(previous_mode)
    else:
        default_mode = nvenc_multipass_default_mode(answers, quality_oriented)
    default_choice = {"disabled": "1", "qres": "2", "fullres": "3"}[default_mode]
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Use NVENC multipass?",
                f"{paint('1', Color.OPT_KEY_CORAL)}{paint('=Disabled / fastest', Color.HINT_YELLOW)}; "
                f"{paint('2', Color.OPT_KEY_CORAL)}{paint('=qres / quarter-resolution first pass', Color.HINT_YELLOW)}; "
                f"{paint('3', Color.OPT_KEY_CORAL)}{paint('=fullres / best quality, slower', Color.HINT_YELLOW)}",
                default_choice,
            )
        )
        lowered = value.strip().lower()
        if is_back_value(value):
            raise Back()
        if not lowered:
            lowered = default_choice
            default_used = True
        else:
            default_used = False
        mapping = {
            "1": "disabled", "۱": "disabled", "١": "disabled",
            "2": "qres", "۲": "qres", "٢": "qres",
            "3": "fullres", "۳": "fullres", "٣": "fullres",
        }
        mode = mapping.get(lowered)
        if mode:
            answers["nvenc_multipass"] = mode
            answers.pop("nvenc_multipass_skip_reason", None)
            log_info(
                f"User choice: nvenc_multipass={mode}; workflow={workflow_name}; "
                f"default_used={'yes' if default_used else 'no'}"
            )
            return mode
        appio.error("Enter 1, 2, or 3. Use 0 to go back.")


def step_nvenc_multipass(answers: dict[str, Any]) -> None:
    ask_nvenc_multipass_if_applicable(answers, workflow_name="main encode", quality_oriented=True)


def step_color_range(answers: dict[str, Any]) -> None:
    """Resolve an unknown source color range via a 3-option menu. The choice is
    stored in answers['color_range_choice'] and reused for every output Part."""
    if source_color_range_known(answers):
        return
    previous = str(answers.get("color_range_choice") or "").strip().lower()
    default_choice = {"tv": "1", "unspecified": "2", "pc": "3"}.get(previous, "1")
    print()
    appio.note("Source color range is unknown:")
    mapping = {"1": "tv", "2": "unspecified", "3": "pc", "۱": "tv", "۲": "unspecified", "۳": "pc"}
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Select source color range",
                "1=Assume TV/Limited; 2=Do not force a range; 3=Assume PC/Full",
                default_choice,
            )
        )
        lowered = value.strip().lower()
        if is_back_value(value):
            raise Back()
        if not lowered:
            lowered = default_choice
        choice = mapping.get(lowered)
        if not choice:
            appio.error("Enter 1, 2, or 3. Use 0 to go back.")
            continue
        answers["color_range_choice"] = choice
        resolved, source = resolve_color_range(answers)
        log_info(
            "Color range resolved: detected=unknown; resolved=%s; resolution_source=%s; "
            "output_metadata=%s; pixel_value_range_conversion=no"
            % (resolved or "unspecified", source, resolved or "omitted")
        )
        if choice == "tv":
            appio.note("Color range: Assume TV/Limited (user-assumed, no pixel-value conversion).")
        elif choice == "pc":
            appio.note("Color range: Assume PC/Full (user-assumed, no pixel-value conversion).")
        else:
            # "Do not force a range in FFmWiz": FFmWiz omits -color_range, but
            # the selected encoder may still write its own default signaling.
            if encoder_preserves_unspecified_range(answers):
                appio.note(
                    "Color range: Do not force a range in FFmWiz. FFmWiz will not pass "
                    "an explicit color-range option; the selected encoder is expected to "
                    "leave the final range unspecified. No pixel-value conversion."
                )
            else:
                appio.note(
                    "Color range: Do not force a range in FFmWiz. FFmWiz will not pass "
                    "an explicit color-range option; the selected encoder may still write "
                    "its own default range signaling (expected: %s). No pixel-value conversion."
                    % (expected_unforced_range(answers) or "unspecified")
                )
        return


_UNSPECIFIED_RANGE_PRESERVING_ENCODERS = {"libx264", "h264_nvenc"}


def encoder_preserves_unspecified_range(answers: dict[str, Any]) -> bool:
    """True when the resolved encoder and output container together leave the
    final color range genuinely unspecified if no -color_range option is passed
    (H.264 encoder + MP4-like container only)."""
    encoder = str(resolve_video_encoder(answers)[0]).lower()
    if encoder not in _UNSPECIFIED_RANGE_PRESERVING_ENCODERS:
        return False
    return str(answers.get("output_ext", "")).strip().lower() in MP4_LIKE_EXTS


def expected_unforced_range(answers: dict[str, Any]) -> str:
    """The color range expected in the final file when FFmWiz forces none.
    Empty string when the encoder/container preserves an unspecified range;
    otherwise the verified default ('tv'). This is a static heuristic used for
    pre-probe guidance only; the FFmpeg capability cache is authoritative."""
    if encoder_preserves_unspecified_range(answers):
        return ""
    return "tv"


_CHROMA_RANK = {"4:4:4": 3, "4:2:2": 2, "4:2:0": 1, "4:1:1": 1}


def compare_pixel_formats(source_fmt: Any, target_fmt: Any) -> dict[str, Any]:
    """Compare a source and target pixel format and classify the operation.

    Returns a dict with: source/target descriptors, operation
    ('none' | 'no-op compatibility constraint' | 'conversion' | 'unknown'),
    bit_depth_conversion, chroma_conversion, and a list of human warnings for
    precision/chroma/colour-model reductions."""
    src = pix_fmt_descriptor(source_fmt)
    tgt = pix_fmt_descriptor(target_fmt)
    result: dict[str, Any] = {
        "source": src,
        "target": tgt,
        "operation": "conversion",
        "bit_depth_conversion": "no",
        "chroma_conversion": "no",
        "warnings": [],
    }

    if src["kind"] == "unknown" or not src["pix_fmt"] or src["pix_fmt"] == "unknown":
        result["operation"] = "unknown"
        return result
    if tgt["kind"] == "unknown" or tgt["pix_fmt"] == "unknown":
        result["operation"] = "unknown"
        return result

    same_fmt = src["pix_fmt"] == tgt["pix_fmt"]
    same_geom = (src["bit_depth"] == tgt["bit_depth"]
                 and src["chroma"] == tgt["chroma"]
                 and src["kind"] == tgt["kind"])

    if same_fmt:
        result["operation"] = "none"
    elif same_geom:
        # Same bit depth + chroma + colour model, only a format relabel
        # (e.g. yuv420p <-> nv12) -> harmless compatibility constraint.
        result["operation"] = "no-op compatibility constraint"
    else:
        result["operation"] = "conversion"

    # Bit-depth reduction.
    if (src["bit_depth"] and tgt["bit_depth"] and src["bit_depth"] > tgt["bit_depth"]):
        result["bit_depth_conversion"] = f"{src['bit_depth']}-bit -> {tgt['bit_depth']}-bit"
        result["warnings"].append(
            f"Video bit depth will be reduced from {src['bit_depth']}-bit to {tgt['bit_depth']}-bit."
        )

    # Chroma reduction (only meaningful for YUV->YUV).
    if src["kind"] == "yuv" and tgt["kind"] == "yuv":
        s_rank = _CHROMA_RANK.get(src["chroma"], 0)
        t_rank = _CHROMA_RANK.get(tgt["chroma"], 0)
        if s_rank and t_rank and s_rank > t_rank:
            result["chroma_conversion"] = f"{src['chroma']} -> {tgt['chroma']}"
            result["warnings"].append(
                f"Chroma subsampling will be reduced from {src['chroma']} to {tgt['chroma']}."
            )

    # Colour-model change (RGB/gray -> YUV).
    if src["kind"] in {"rgb", "gray"} and tgt["kind"] == "yuv":
        label = "RGB" if src["kind"] == "rgb" else "grayscale"
        result["warnings"].append(
            f"{label} video will be converted to YUV {tgt['chroma']}."
        )

    return result


def pixel_format_analysis(answers: dict[str, Any]) -> dict[str, Any]:
    """Analyse the source vs target pixel format for the current workflow."""
    stream = source_video_stream(answers) or {}
    return compare_pixel_formats(stream.get("pix_fmt"), target_pixel_format_for_answers(answers))


def log_and_warn_pixel_format(answers: dict[str, Any], announce: bool = True) -> dict[str, Any]:
    """Log the pixel-format decision and surface lossy-conversion warnings before
    command generation. No-op compatibility constraints are logged, not warned."""
    if not output_has_video(answers):
        return {}
    if str(resolve_video_encoder(answers)[0]).lower() == "copy":
        return {}
    info = pixel_format_analysis(answers)
    src = info["source"]
    tgt = info["target"]
    log_info(
        "Pixel format: source=%s (%s, %s); target=%s (%s, %s); operation=%s; "
        "bit_depth_conversion=%s; chroma_subsampling_conversion=%s"
        % (
            src["pix_fmt"], f"{src['bit_depth']}-bit" if src["bit_depth"] else "unknown-bit", src["chroma"],
            tgt["pix_fmt"], f"{tgt['bit_depth']}-bit" if tgt["bit_depth"] else "unknown-bit", tgt["chroma"],
            info["operation"], info["bit_depth_conversion"], info["chroma_conversion"],
        )
    )
    for warning in info["warnings"]:
        log_warn("Pixel format: " + warning)
        if announce:
            appio.note("Warning: " + warning)
    # Surface a precision-reduction note for high-bit-depth (12-bit+) sources
    # whose delivery is capped at a lower bit depth, so the reduction is never
    # silent. Up-conversion (8-bit source to 10-bit output) is informational.
    precision_note = bit_depth_precision_note(answers)
    if precision_note:
        src_depth = source_video_bit_depth(answers) or 0
        out_depth = output_video_bit_depth(answers)
        if src_depth > out_depth:
            log_warn("Bit depth: " + precision_note)
            if announce:
                appio.note("Warning: " + precision_note)
        else:
            log_info("Bit depth: " + precision_note)
            if announce:
                appio.note(precision_note)
    return info


def open_audio_speed_gui(answers: dict[str, Any], audio_index: int) -> dict[str, Any] | None:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    request = {
        "mode": "audio_speed",
        "input_path": str(answers["input_path"]),
        "audio_index": int(audio_index),
        "duration": float(duration),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
    }
    reply = guibridge._launch_qt_gui(request)
    if reply is None:
        appio.error("Graphical audio speed editor is not available. Install PySide6 and try again.")
        return None
    if reply.get("status") == "ok":
        try:
            return {
                "speed": clamp_speed_factor(reply.get("speed", DEFAULT_SPEED_FACTOR)),
                "reverse": bool(reply.get("reverse")),
            }
        except ValueError as exc:
            appio.error(str(exc))
            return None
    if reply.get("status") == "error":
        answers["_last_gui_error"] = "audio_speed"
        appio.error("Audio speed GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
    return None


def open_audio_cut_gui(answers: dict[str, Any], audio_index: int) -> list[tuple[float, float]] | None:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    request = {
        "mode": "audio_cut",
        "input_path": str(answers["input_path"]),
        "audio_index": int(audio_index),
        "duration": float(duration),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
    }
    reply = guibridge._launch_qt_gui(request)
    if reply is None:
        appio.error("Graphical audio cut editor is not available. Install PySide6 and try again.")
        return None
    if reply.get("status") == "ok":
        ranges = reply.get("keep_ranges") or []
        normalized: list[tuple[float, float]] = []
        for entry in ranges:
            try:
                s, e = float(entry[0]), float(entry[1])
            except Exception:
                continue
            if e > s:
                normalized.append((s, e))
        return normalize_cut_ranges(normalized, duration)
    if reply.get("status") == "error":
        answers["_last_gui_error"] = "audio_cut"
        appio.error("Audio cut GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
    return None


def step_input_path(answers: dict[str, Any]) -> None:
    while True:
        input_example = example_text('"E:\\Input\\video.mkv"')
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter input file path",
                f"drag and drop a file here or paste a path; example: {input_example}",
                back="back=0, quit=exit, f=join all videos in folder",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            appio.error("This value cannot be empty. Enter a file path, or 'f' to join a folder.")
            continue
        # Folder join: the 'f' keyword prompts for a folder; a directory path is
        # also accepted directly. Every video in it is joined in name order.
        folder: Path | None = None
        if value.lower() in {"f", "folder"}:
            folder = ask_join_folder_path(answers)
            if folder is None:
                continue
        else:
            candidate = terminal_path(value)
            if candidate.is_dir():
                folder = candidate
        if folder is not None:
            if _load_input_folder_join(answers, folder):
                return
            continue
        input_path = terminal_path(value)
        if not input_path.exists() or not input_path.is_file():
            appio.error("File not found. Enter the full file path again.")
            continue

        try:
            load_input_metadata(answers, input_path)
        except FFprobeError as exc:
            appio.error(str(exc))
            continue
        except Exception as exc:
            log_exception(f"ffprobe metadata load failed for input path: {input_path}")
            appio.error(f"ffprobe could not read the file. See log file: {_log_file_text()}")
            continue
        return


def step_output_location(answers: dict[str, Any]) -> None:
    folder_example = example_text(r"E:\output")
    name_example = example_text('"File name"')
    value = appio.ask_raw(
        appio.question_prompt(
            answers,
            "Enter output path, output folder, or bare output name",
            f"Enter=same folder as input; examples: {folder_example} or {name_example}",
        )
    )
    if is_back_value(value):
        raise Back()
    apply_output_location_value(answers, value)
    join_items = list(answers.get("join_input_items") or [])
    if join_items:
        original_title = answers.get("_source_info_title")
        answers["_source_info_title"] = f"Source file info (1/{len(join_items) + 1})"
        trackmanager.print_source_info(answers)
        if original_title is None:
            answers.pop("_source_info_title", None)
        else:
            answers["_source_info_title"] = original_title
        for idx, item in enumerate(join_items, start=2):
            joined_answers = join_item_answers(answers, item)
            joined_answers["_source_info_title"] = f"Source file info ({idx}/{len(join_items) + 1})"
            trackmanager.print_source_info(joined_answers)
    else:
        trackmanager.print_source_info(answers)


def ask_join_add_another(prompt: str, allow_folder: bool = True) -> bool | str:
    """Join-flow variant of the add-another question. Returns True (yes),
    False (no / Enter), or the string 'folder'. Raises Back on 0/back tokens."""
    while True:
        value = appio.ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        lowered = value.lower()
        if not value or lowered in {"n", "no"}:
            log_info(f"User choice: join_add_another=no; raw={value!r}")
            return False
        if lowered in {"y", "yes"}:
            log_info("User choice: join_add_another=yes")
            return True
        if allow_folder and lowered in {"f", "folder"}:
            log_info("User choice: join_add_another=folder")
            return "folder"
        appio.error("Enter y, n, or 'f' (join all videos in a folder)." if allow_folder else "Enter y or n.")


def step_join_additional_inputs_for_encode(answers: dict[str, Any]) -> None:
    audio_join = wizard_audio_join_applicable(answers) and not output_has_video(answers)
    if (not output_has_video(answers) and not audio_join) or not answers.get("input_path"):
        answers.pop("join_input_items", None)
        answers["_join_question_extra"] = 0
        answers.pop("_join_base_question", None)
        answers.pop("_join_last_question", None)
        return
    existing_items = list(answers.get("join_input_items") or [])
    # Media-aware wording + options (audio joins do not offer folder scanning).
    media_word = "audio" if audio_join else "video"
    join_back = JOIN_ADD_ANOTHER_BACK_AUDIO if audio_join else JOIN_ADD_ANOTHER_BACK
    allow_folder = not audio_join
    existing_extra = int(answers.get("_join_question_extra", 0) or 0)
    current_question = int(answers.get("_question_number", 0) or 0)
    resuming_existing_join = bool(existing_items and existing_extra and current_question > existing_extra)
    base_question = int(answers.get("_join_base_question") or (current_question - existing_extra if resuming_existing_join else current_question))
    items: list[dict[str, Any]] = existing_items if resuming_existing_join else []
    if resuming_existing_join:
        resume_question = max(current_question, int(answers.get("_join_last_question") or current_question))
        sub_question_base = resume_question
        first_title = f"Add another {media_word} file?"
    else:
        # Folder given at the input prompt pre-loads the join set; keep those
        # items instead of discarding them.
        preloaded_from_folder = bool(answers.pop("_join_preloaded_from_folder", False))
        if preloaded_from_folder:
            items = existing_items
        else:
            answers.pop("join_input_items", None)
            items = []
        answers["_join_question_extra"] = 0
        answers["_join_base_question"] = base_question
        sub_question_base = base_question
        first_title = f"Add another {media_word} file?" if items else f"Add another {media_word} file to join with this input?"

    # Initial add-another question (now also accepts 'folder').
    answers["_question_number"] = sub_question_base
    decision = ask_join_add_another(
        appio.question_prompt(answers, first_title, "y/n", "n", back=join_back), allow_folder=allow_folder
    )
    if decision is False:
        if resuming_existing_join:
            answers["join_input_items"] = items
            answers["_join_question_extra"] = existing_extra
        else:
            # Preserve folder-preloaded inputs (and bookkeeping for back-nav).
            answers["join_input_items"] = items
            if items:
                answers["_join_question_extra"] = max(1, existing_extra)
                answers["_join_last_question"] = sub_question_base
            else:
                answers.pop("_join_base_question", None)
                answers.pop("_join_last_question", None)
        return
    sub_question = sub_question_base + 1
    need_file = decision is True
    if decision == "folder":
        answers["_question_number"] = sub_question
        folder = ask_join_folder_path(answers)
        if folder is not None:
            join_add_folder_items(answers, folder, items)
        sub_question += 1
        need_file = False

    while True:
        if need_file:
            answers["_question_number"] = sub_question
            value = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    f"Enter additional {media_word} file path",
                    f"drag and drop a {media_word} file here or paste a path; b=re-enter previous file",
                    back="back=0, quit=exit",
                )
            )
            if is_back_value(value):
                # '0' goes back to the previous wizard step.
                raise Back()
            if value.lower() in {"b", "back"}:
                # 'b' steps back to the PREVIOUS join file (re-enter it) rather
                # than leaving the join question entirely. With no previous
                # additional file, fall back to the previous wizard step.
                if items:
                    removed = items.pop()
                    appio.note(f"Removed previous join file: {Path(removed['path']).name}. Re-enter it.")
                    continue
                raise Back()
            if not value:
                appio.error("This value cannot be empty. Enter a file path, or 'b' to go back.")
                continue
            path = terminal_path(value)
            if not path.exists() or not path.is_file():
                appio.error("File not found. Enter the full file path again.")
                continue
            if paths_same(path, answers["input_path"]) or any(paths_same(path, item["path"]) for item in items):
                appio.error("This file is already selected for joining. Enter a different file.")
                continue
            if looks_like_generated_output_file(path):
                appio.error("This looks like a previously generated FFmWiz output file. It was not added as a join input.")
                continue
            try:
                items.append(services.join_load_media_item(answers, path, allow_audio_only=audio_join))
            except Exception as exc:
                log_exception(f"Join input probe failed: {path}")
                appio.error(str(exc))
                continue
            sub_question += 1

        answers["_question_number"] = sub_question
        decision = ask_join_add_another(
            appio.question_prompt(answers, f"Add another {media_word} file?", "y/n", "n", back=join_back), allow_folder=allow_folder
        )
        if decision is False:
            break
        sub_question += 1
        if decision == "folder":
            answers["_question_number"] = sub_question
            folder = ask_join_folder_path(answers)
            if folder is not None:
                join_add_folder_items(answers, folder, items)
            sub_question += 1
            need_file = False
            continue
        need_file = True
    answers["_join_last_question"] = sub_question
    answers["_join_question_extra"] = max(0, sub_question - base_question)
    answers["join_input_items"] = items
    appio.note(f"Added {len(items)} additional {media_word} input(s) for joining.")
    if items:
        print_join_order_list([answers["input_path"], *[it["path"] for it in items]])


def step_output_format(answers: dict[str, Any]) -> None:
    # Join mode: show the compact source summary (min/max video bitrate, audio
    # bitrate, fps, file count) right before the format prompt.
    if answers.get("join_input_items"):
        print_join_input_summary(answers)
    input_ext = answers["input_path"].suffix.lstrip(".") or "mp4"
    if answers.get("video_streams"):
        # Default to mp4 for video, except keep mkv when the input is mkv.
        default_ext = "mkv" if input_ext.lower() == "mkv" else "mp4"
    else:
        default_ext = "mp3"
    common_formats = COMMON_VIDEO_FORMATS + COMMON_AUDIO_FORMATS
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter final output format",
                f"common: {option_list(common_formats)}; {keep_value_text(f'n=Use input format ({input_ext})')}",
                default_ext,
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = default_ext
        try:
            answers["output_format_keep_input"] = value.lower().strip() == "n"
            ext = normalize_format(value, input_ext)
        except ValueError as exc:
            appio.error(str(exc))
            continue
        # Reject unknown output formats (likely typos) with a suggestion, and
        # ask for a different format instead of building a command FFmpeg fails.
        if not answers["output_format_keep_input"] and ext not in KNOWN_OUTPUT_FORMATS:
            import difflib
            close = difflib.get_close_matches(ext, sorted(KNOWN_OUTPUT_FORMATS), n=1)
            hint = f" Did you mean '{close[0]}'?" if close else ""
            appio.error(f"'{ext}' is not a supported output format.{hint} Enter a different format.")
            continue
        answers["output_ext"] = ext
        return


def step_video_codec(answers: dict[str, Any]) -> None:
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Enter video codec",
                f"common: {option_list(COMMON_VIDEO_CODECS)}; {keep_value_text('n=copy current video stream without re-encoding')}",
                DEFAULT_VIDEO_CODEC,
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = DEFAULT_VIDEO_CODEC
        if value.lower() == "n":
            value = "copy"
        lowered = value.lower()
        if lowered == "copy" or lowered in VIDEO_CODEC_ALIASES:
            answers["video_codec"] = value
            return
        available = {str(item).lower() for item in answers.get("video_encoders") or []}
        if not available or lowered in available:
            answers["video_codec"] = value
            return
        appio.error(
            f"Unknown video encoder '{value}'. Enter one of the common aliases "
            "or an encoder reported by your FFmpeg build."
        )


def step_use_gpu(answers: dict[str, Any]) -> None:
    if not gpu_available_for_answers(answers):
        answers["use_gpu"] = False
        appio.note("No usable NVIDIA/NVENC GPU was detected. GPU question skipped; CPU mode selected.")
        log_info("User choice: use_gpu=False; reason=GPU unavailable")
        return
    answers["use_gpu"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Use GPU/NVIDIA for decode/filter/encode?", "y/n", "y"),
        True,
    )


def step_cpu_two_pass(answers: dict[str, Any]) -> None:
    answers["cpu_two_pass"] = appio.ask_yes_no(
        appio.question_prompt(
            answers,
            "Use two-pass CPU video encoding for closer target bitrate?",
            "y/n; slower, but usually closer to the requested bitrate",
            "y",
        ),
        True,
    )


def step_unified_video_editor_for_encode(answers: dict[str, Any]) -> None:
    if answers.get("_disable_graphical_editors"):
        answers["_unified_video_editor_used"] = False
        return
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Open unified graphical video editor?",
                f"y/n; {colored_unified_editor_hint()}",
                "y",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "y"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["_unified_video_editor_used"] = False
            answers["_unified_video_editor_declined"] = True
            answers["_disable_followup_video_gui_prompts"] = True
            # In the config wizard (Mode 2), declining the editor must NOT discard
            # crop/speed/reverse that came from config.env; those were already
            # applied and should still take effect.
            if not answers.get("_config_mode"):
                answers["crop_enabled"] = False
                answers["crop_values_inline"] = False
                for key in ("crop_top", "crop_left", "crop_right", "crop_bottom"):
                    answers.pop(key, None)
                answers["video_speed_enabled"] = False
                answers["reverse_video"] = False
                answers["audio_speed_from_video"] = False
                answers["cut_keep_ranges"] = []
                answers.pop("separator_points", None)
                answers.pop("_unified_separator_points", None)
            return
        if lowered in {"y", "yes"}:
            appio.note("Loading Unified Graphical Video Editor...")
            sys.stdout.flush()
            result = guibridge.open_unified_video_gui(answers)
            if result is None:
                appio.note("Unified graphical video editor was canceled. Returning to the unified editor question.")
                continue
            top, left, right, bottom = result["margins"]
            if not set_crop_margins_if_valid(answers, top, left, right, bottom):
                continue
            answers["_unified_video_editor_used"] = True
            answers["_unified_video_editor_declined"] = False
            answers["_disable_followup_video_gui_prompts"] = False
            answers["_unified_cut_keep_ranges"] = result.get("keep_ranges") or []
            answers["_unified_separator_points"] = result.get("separator_points") or []
            if answers["_unified_separator_points"]:
                answers["separator_points"] = list(answers["_unified_separator_points"])
            else:
                answers.pop("separator_points", None)
            answers["_unified_video_speed"] = result["speed"]
            answers["_unified_reverse_video"] = result["reverse"]
            answers["_unified_include_audio"] = result["include_audio"]
            speed = clamp_speed_factor(result.get("speed", DEFAULT_SPEED_FACTOR))
            reverse = bool(result.get("reverse"))
            answers["video_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
            answers["video_speed_factor"] = speed
            answers["reverse_video"] = reverse
            answers["audio_speed_from_video"] = bool(result.get("include_audio"))
            unified_duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
            unified_duration += sum(float(item.get("duration") or 0.0) for item in answers.get("join_input_items") or [])
            keep_ranges = normalize_cut_ranges(result.get("keep_ranges") or [], unified_duration)
            answers["cut_keep_ranges"] = keep_ranges
            if keep_ranges:
                print(paint(format_cut_ranges_for_summary(keep_ranges, services.get_video_fps(answers), "Cuts (keep ranges)"), Color.LIME))
            _unified_seps = answers.get("_unified_separator_points") or []
            if _unified_seps:
                print(paint(format_split_points_for_summary(_unified_seps, services.get_video_fps(answers), "Split points"), Color.LIME))
            print(paint("Graphical edits captured.", Color.LIME))
            if answers["video_speed_enabled"]:
                print(
                    paint(
                        f"Applied unified video speed: {speed * 100:.0f}% ({speed:g}x); "
                        f"reverse video: {'yes' if reverse else 'no'}; "
                        f"sync audio: {'yes' if answers['audio_speed_from_video'] else 'no'}",
                        Color.LIME,
                    )
                )
            return
        appio.error("Enter y or n.")


def step_crop_enabled(answers: dict[str, Any]) -> None:
    if answers.get("_unified_video_editor_used"):
        margins = (
            int(answers.get("crop_top", 0) or 0),
            int(answers.get("crop_left", 0) or 0),
            int(answers.get("crop_right", 0) or 0),
            int(answers.get("crop_bottom", 0) or 0),
        )
        answers["crop_enabled"] = any(margins)
        answers["crop_values_inline"] = bool(any(margins))
        if any(margins):
            print(paint(f"Applied unified crop: {format_crop_margins(answers)}", Color.LIME))
        return
    while True:
        crop_hint = (
            "y/n, or inline top,left,right,bottom like "
            f"{example_text('100,300,200,550')}; {paint('zero is allowed inside inline crop', Color.ZERO_INLINE)}"
        )
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Apply crop?",
                crop_hint,
                "n",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["crop_enabled"] = False
            answers["crop_values_inline"] = False
            for key in ("crop_top", "crop_left", "crop_right", "crop_bottom"):
                answers.pop(key, None)
            return
        if lowered in {"y", "yes"}:
            answers["crop_enabled"] = True
            answers["crop_values_inline"] = False
            return
        if lowered in {"g", "gui", "preview"}:
            appio.error("The standalone Crop GUI is archived. Use the Unified Video Editor or enter crop margins inline.")
            continue

        pieces = [piece.strip() for piece in value.split(",")]
        if len(pieces) != 4 or any(not re.fullmatch(r"\d+", piece) for piece in pieces):
            appio.error("Enter y, n, or four integer crop margins: top,left,right,bottom")
            continue
        top, left, right, bottom = [int(piece) for piece in pieces]
        if not set_crop_margins_if_valid(answers, top, left, right, bottom):
            continue
        answers["crop_values_inline"] = True
        return


def step_video_bitrate(answers: dict[str, Any]) -> None:
    packet_sizes = services.get_packet_sizes(answers)
    source = stream_bitrate_kbps(answers["video_streams"][0], answers.get("format"), packet_sizes)
    source_limit, source_limit_label = detected_video_bitrate_limit(answers)

    # First ask: bitrate mode or constant quality (RF/CQ).
    mode_prompt = appio.question_prompt(
        answers,
        "Video quality mode",
        f"{paint('bitrate', Color.OPT_KEY_CYAN)}{paint('=target average kbps', Color.HINT_YELLOW)}; "
        f"{paint('CRF', Color.OPT_KEY_CYAN)}{paint('=constant quality (RF/CQ)', Color.HINT_YELLOW)}",
        "bitrate",
    )
    while True:
        mode_value = appio.ask_raw(mode_prompt).strip().lower()
        if is_back_value(mode_value):
            raise Back()
        if not mode_value or mode_value in {"bitrate", "b", "1"}:
            mode_value = "bitrate"
            break
        if mode_value in {"rf", "crf", "cq", "2", "quality"}:
            mode_value = "rf"
            break
        appio.error("Enter 'bitrate' or 'CRF'.")

    if mode_value == "rf":
        _step_video_constant_quality(answers)
        return

    # Bitrate mode: same as before.
    suggested = source or DEFAULT_OUTPUT_VIDEO_BITRATE_KBPS
    prompt = appio.question_prompt(
        answers,
        "Enter average video bitrate in kbps",
        f"examples: {example_text('400,800,1500')}; "
        f"{keep_value_text('n=keep current value' + (' around ' + str(source) + 'k' if source else ''))}; "
        f"{suggestion_text(f'suggestion: {suggested}')}",
        str(suggested),
    )
    while True:
        value = appio.ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        if not value:
            value = str(suggested)
        if value.lower() == "n":
            answers["video_bitrate_kbps"] = source
            answers["video_bitrate_keep"] = True
            return
        if not re.fullmatch(r"\d+", value):
            appio.error("Enter an integer only.")
            continue
        number = int(value)
        if number == 0:
            raise Back()
        if not confirm_numeric_target_not_above_source(
            answers,
            "video bitrate",
            number,
            source_limit,
            source_limit_label,
            "kbps",
            "higher video bitrate can increase file size without adding real source detail",
        ):
            continue
        answers["video_bitrate_kbps"] = number
        answers["video_bitrate_keep"] = False
        answers.pop("video_crf", None)
        return


def step_resolution(answers: dict[str, Any]) -> None:
    while True:
        prompt = appio.question_prompt(
            answers,
            "Enter output resolution",
            f"presets/plain numbers preserve aspect ratio using closest-edge scaling: {option_list(list(RESOLUTION_PRESETS))}, "
            f"{paint('numbers without p like 480', Color.RES_NUMBERS)}, "
            f"{paint('force width like w720', Color.RES_TARGET)}, "
            f"{paint('force height like h480', Color.RES_TARGET)}, "
            f"{paint('target box like', Color.RES_EXACT)} {example_text('1280x720')}, "
            f"{paint('exact stretch like', Color.RES_EXACT)} {example_text('stretch:1280x720')}; "
            +
            keep_value_text("n=current resolution"),
            "n",
        )
        value = appio.ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        try:
            parsed = parse_resolution(value)
        except ValueError as exc:
            appio.error(str(exc))
            continue
        if not confirm_resolution_not_above_source(answers, parsed):
            continue
        answers["resolution"] = parsed
        return


def step_fps(answers: dict[str, Any]) -> None:
    # Join with different source frame rates: ask the unify/VFR policy first, and
    # (when unifying) ask the target fps here so the fps question comes after the
    # unify question. When declined the join stays VFR and no fps is asked.
    if answers.get("join_input_items") and output_has_video(answers):
        join_items = join_ordered_items_for_answers(answers)
        if len(join_items) >= 2 and join_frame_rates_differ(join_items):
            ask_join_frame_rate_policy(answers, join_items)
            return
        answers.setdefault("join_vfr", False)
    fps = rational_to_float(answers["video_streams"][0].get("avg_frame_rate"))
    source_limit, source_limit_label = detected_fps_limit(answers)
    keep_fps_text = "n=current FPS" + (f" around {format(fps, '.3g')}" if fps else "")
    prompt = appio.question_prompt(
        answers,
        "Enter frames per second",
        f"examples: {example_text('4,5,24,30,60')}; {keep_value_text(keep_fps_text)}",
        "n",
    )
    while True:
        value = appio.ask_raw(prompt)
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        if value.lower() == "n":
            answers["fps"] = None
            return
        if not re.fullmatch(r"\d+", value):
            appio.error("Enter an integer only.")
            continue
        number = int(value)
        if number == 0:
            raise Back()
        if not confirm_numeric_target_not_above_source(
            answers,
            "frames per second",
            number,
            source_limit,
            source_limit_label,
            "fps",
            "higher FPS duplicates or interpolates timing work without adding real captured frames",
        ):
            continue
        answers["fps"] = number
        return


def build_cpu_video_filter(answers: dict[str, Any]) -> str | None:
    filters: list[str] = []
    if answers.get("crop_enabled"):
        left, right, top, bottom = normalized_crop_margins(answers)
        filters.append(f"crop=iw-{left}-{right}:ih-{top}-{bottom}:{left}:{top}:exact=1")

    if answers.get("fps") is not None:
        filters.append(f"fps={answers['fps']}")

    resolution = answers.get("resolution", "n")
    scale_dimensions = resolve_scale_dimensions(answers, resolution)
    scale_resets_sar = False
    if scale_dimensions:
        width, height = scale_dimensions
        is_stretch = resize_mode_is_stretch(answers)
        if is_stretch:
            # Exact stretch: force the requested dimensions regardless of AR.
            filters.append(f"scale={width}:{height}")
            log_info(f"Resize mode: Stretch; scale={width}:{height}")
        else:
            # AR-preserving: always use force_original_aspect_ratio=decrease so
            # the content fits inside the target canvas without distortion, then
            # pad to the exact canvas dimensions. When the source AR matches
            # the target, FFmpeg produces the exact dimensions and the pad is a
            # no-op. This approach handles all cases uniformly.
            # reset_sar=1 inside the scale filter ensures output pixels are
            # square, making a trailing setsar=1 unnecessary.
            sar = source_sar(answers)
            crop_w, crop_h = cropped_source_size(answers)
            display_w, display_h = cropped_display_size(answers)
            filters.append(
                f"scale={width}:{height}:"
                f"force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1"
            )
            filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
            scale_resets_sar = True
            log_info(
                f"Resize mode: Preserve; "
                f"source_coded={crop_w}x{crop_h}; SAR={sar:.4f}; "
                f"display={display_w}x{display_h}; target={width}x{height}; "
                f"upscaling={'yes' if max(width, height) > max(display_w, display_h) else 'no'}"
            )

    if video_speed_transform_enabled(answers):
        filters.append(build_video_speed_filter(encode_video_speed_factor(answers), bool(answers.get("reverse_video"))))

    # Crop dimensions are normalized to the output encoder grid by
    # normalized_crop_margins, so no black compatibility padding is added here.

    # SAR handling:
    #  - Preserve/Fit resize already resets SAR inside the scale filter
    #    (scale_resets_sar) -> no trailing setsar needed.
    #  - Stretch resize (scale_dimensions set, but not reset) intentionally
    #    produces square pixels -> keep the explicit setsar.
    #  - No resize: do NOT blindly force setsar=1. Forcing 1:1 on a non-square
    #    source changes its display geometry. Preserve the source SAR by
    #    omitting the filter (the decoder passes the source SAR through), and do
    #    not invent 1:1 for an unknown SAR.
    if not scale_resets_sar:
        if scale_dimensions:
            # Stretch resize: square-pixel output is intentional here.
            if FORCE_SAR:
                filters.append(f"setsar={FORCE_SAR}")
        else:
            info = sar_dar_info(answers)
            sar = info.get("resolved_sar")
            if info.get("fallback_used"):
                log_info(
                    "SAR: source SAR/DAR unavailable; no-resize command generation assumes a "
                    "square-pixel source (SAR 1:1); no setsar forced."
                )
            elif sar is not None and abs(sar - 1.0) >= SAR_DAR_TOLERANCE:
                log_info(
                    f"SAR: no-resize path preserves resolved non-square SAR "
                    f"{info.get('sar_text')} ({info.get('sar_source')}); no setsar forced."
                )
            elif sar is None:
                log_info("SAR: source SAR unresolved; no setsar forced in no-resize path.")
            else:
                log_info(
                    f"SAR: resolved source pixels are square ({info.get('sar_source')}); "
                    f"setsar omitted as redundant."
                )

    filters.append(f"format={cpu_graph_pixel_format_for_encoder(answers)}")
    return ",".join(filters) if filters else None


def build_video_filter(answers: dict[str, Any], use_gpu_filtering: bool) -> str | None:
    return build_cuda_video_filter(answers) if use_gpu_filtering else build_cpu_video_filter(answers)


def append_single_input_split_outputs(
    cmd: list[str],
    answers: dict[str, Any],
    output_path: Path,
    video_encoder: str,
    tag: str | None,
    profile: str | None,
    audio_indices: list[int],
    audio_transform_active: bool,
    multi_cut: bool,
    audio_for_cut: int | None,
) -> bool:
    source_duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    final_duration = final_processed_duration_for_splits(answers, source_duration)
    split_points = normalize_separator_points(answers.get("separator_points"), final_duration)
    if not split_points:
        return False
    filters: list[str] = []
    audio_labels: list[str] = []
    if multi_cut:
        filters.extend(build_cut_filter_complex(answers, list(answers.get("cut_keep_ranges") or []), audio_for_cut).split(";"))
        video_label = "v"
        if audio_for_cut is not None:
            audio_labels.append("a")
    else:
        video_filter = build_cpu_video_filter(answers) or "null"
        filters.append(f"[0:v:0]{video_filter}[vbase]")
        video_label = "vbase"
        if audio_indices:
            if audio_transform_active:
                audio_fc, labels = build_audio_transform_filter_complex(answers, audio_indices)
                filters.extend(audio_fc.split(";"))
                audio_labels.extend(labels)
            else:
                for pos, audio_index in enumerate(audio_indices):
                    label = f"abase{pos}"
                    filters.append(f"[0:a:{audio_index}]asetpts=PTS-STARTPTS[{label}]")
                    audio_labels.append(label)
    filters.append(f"[{video_label}]setpts=PTS-STARTPTS[vfinal]")
    final_audio_labels: list[str] = []
    for pos, label in enumerate(audio_labels):
        final_label = f"afinal{pos}"
        filters.append(f"[{label}]asetpts=PTS-STARTPTS[{final_label}]")
        final_audio_labels.append(final_label)
    video_outputs, audio_outputs_by_part, split_intervals = append_final_split_filters(
        filters,
        "vfinal",
        final_audio_labels,
        split_points,
        final_duration,
        "s",
        float(answers.get("fps") or services.get_video_fps(answers) or 0.0),
    )
    output_paths = split_part_output_paths(output_path, len(video_outputs), [Path(answers["input_path"])])
    answers["split_output_paths"] = output_paths
    answers["split_part_intervals"] = split_intervals
    answers["output_path"] = output_paths[0]

    # Prepare per-part chapter remapping if timeline is modified and chapters exist.
    split_chapter_plans: list[dict[str, Any]] = []
    split_chapter_metadata_paths: list[Path] = []
    if source_chapters_keep_enabled(answers):
        speed_factor = encode_video_speed_factor(answers) if video_speed_transform_enabled(answers) else 1.0
        for part_idx, interval in enumerate(split_intervals):
            plan = remap_chapters_for_encode(answers, speed_factor=speed_factor, part_interval=interval)
            split_chapter_plans.append(plan)
            if plan.get("mode") == "metadata" and plan.get("chapters"):
                chapter_temp_dir = answers.get("_chapter_metadata_temp_dir")
                if not chapter_temp_dir:
                    chapter_temp_dir = tempfile.mkdtemp(prefix="ffmwiz_split_chapters_")
                    answers["_chapter_metadata_temp_dir"] = chapter_temp_dir
                metadata_path = write_encode_chapter_metadata(plan, Path(chapter_temp_dir), suffix=f"_part{part_idx + 1:02d}")
                split_chapter_metadata_paths.append(metadata_path)
            else:
                split_chapter_metadata_paths.append(None)

    # Add chapter metadata inputs (input index 1, 2, ... for each part that has chapters).
    chapter_input_base = 1  # Input 0 is the main source file.
    metadata_input_count = 0
    part_chapter_input_indices: list[int | None] = []
    for metadata_path in split_chapter_metadata_paths:
        if metadata_path is not None:
            cmd.extend(["-i", str(metadata_path)])
            part_chapter_input_indices.append(chapter_input_base + metadata_input_count)
            metadata_input_count += 1
        else:
            part_chapter_input_indices.append(None)

    cmd.extend(["-filter_complex", ";".join(filters)])
    for part_idx, part_output in enumerate(output_paths):
        cmd.extend(["-map", f"[{video_outputs[part_idx]}]"])
        for audio_label in audio_outputs_by_part[part_idx]:
            cmd.extend(["-map", f"[{audio_label}]"])
        attachments_mapped = append_embedded_attachment_maps(cmd, answers)
        data_mapped = append_source_data_maps(cmd, answers)

        # Per-part chapter handling for splits.
        if not output_has_video(answers):
            pass
        else:
            cmd.extend(["-map_metadata", "0" if source_metadata_keep_enabled(answers) else "-1"])
            if not source_chapters_keep_enabled(answers):
                cmd.extend(["-map_chapters", "-1"])
            elif part_chapter_input_indices and part_chapter_input_indices[part_idx] is not None:
                cmd.extend(["-map_chapters", str(part_chapter_input_indices[part_idx])])
                log_info(f"Chapters: Part {part_idx + 1} uses remapped metadata input {part_chapter_input_indices[part_idx]}")
            else:
                cmd.extend(["-map_chapters", "-1"])

        append_negative_stream_options(cmd, answers, True, [], data_mapped)
        append_video_encode_options(cmd, answers, video_encoder, tag, profile)
        append_audio_encode_options(cmd, answers, bool(audio_outputs_by_part[part_idx]))
        append_clear_reencoded_stream_stat_metadata(
            cmd,
            answers,
            video_output_count=1 if video_encoder != "copy" else 0,
            audio_output_count=len(audio_outputs_by_part[part_idx]) if audio_outputs_by_part[part_idx] else 0,
            subtitle_output_count=0,
        )
        if attachments_mapped:
            append_embedded_attachment_codec_options(cmd, answers)
        if data_mapped:
            append_source_data_codec_options(cmd, answers)
        append_container_options(cmd, answers["output_ext"])
        cmd.append(str(part_output))
    log_info(
        "Split final output into parts: "
        + ", ".join(
            f"Part {idx + 1:02d} {seconds_to_ffmpeg_time(start)}->{seconds_to_ffmpeg_time(end)}"
            for idx, (start, end) in enumerate(split_intervals)
        )
    )
    return True


def build_ffmpeg_command(answers: dict[str, Any]) -> list[str]:
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    output_path = services.build_output_path(answers)
    answers["output_path"] = output_path
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"Could not create output folder: {output_path.parent}. {exc}") from exc

    # FFmpeg command rules used here:
    # - Explicit -map options disable automatic stream selection for this output.
    # - Streamcopy (-c copy) skips decoding/filtering/encoding and cannot be used
    #   for a stream that needs crop/fps/scale/color-parameter filters.
    # - Audio-only outputs disable video/subtitle streams with -vn and -sn.
    # - MP4-like outputs get mov_text only for text subtitles, plus faststart/tag
    #   options that are valid for that family of muxers.
    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n"]
    if answers.get("use_gpu") and answers.get("gpu_available") is False:
        answers["use_gpu"] = False
        log_info("GPU disabled automatically because no usable NVIDIA/NVENC GPU was detected.")

    has_video = output_has_video(answers)
    video_encoder = None
    tag = None
    profile = None
    use_cuda_fast_path = False
    use_cuda_decode_complex = False

    cut_keep_ranges = list(answers.get("cut_keep_ranges") or [])
    cut_active = bool(cut_keep_ranges) and has_video
    single_cut = len(cut_keep_ranges) == 1 and has_video
    multi_cut = len(cut_keep_ranges) > 1 and has_video

    if has_video:
        if answers.get("output_ext", "").lower() == "webm" and str(answers.get("video_codec", "")).lower() in {
            "h264",
            "h265",
            "hevc",
            "mpeg4",
        }:
            appio.note("WebM does not support that video codec safely here. VP9 was selected for this output.")
            answers["video_codec"] = "VP9"
        video_encoder, tag, profile = resolve_video_encoder(answers)
        if video_encoder == "copy" and video_filters_required(answers):
            appio.note(
                "\nWarning: video copy cannot be used with crop/fps/scale/setparams or cuts. "
                "H265 was selected so filters/cuts can be applied."
            )
            answers["video_codec"] = DEFAULT_VIDEO_CODEC
            video_encoder, tag, profile = resolve_video_encoder(answers)
        video_encoder, tag, profile = enforce_bit_depth_compatible_video_encoder(answers, video_encoder, tag, profile)

        use_cuda_fast_path = can_use_cuda_fast_path(answers, video_encoder)
        use_cuda_decode_complex = should_use_cuda_decode_for_complex_graph(answers, video_encoder, use_cuda_fast_path)
        if answers.get("use_gpu") and video_encoder != "copy" and not str(video_encoder).endswith("_nvenc"):
            appio.note("The selected video encoder is not NVENC, so CPU decode/filter/encode will be used for video.")
        elif multi_cut and answers.get("use_gpu") and str(video_encoder).endswith("_nvenc"):
            log_info("Multiple cut ranges use CPU trim/concat filter_complex; NVENC encode remains enabled.")
        elif use_cuda_decode_complex:
            log_info("Complex CPU filter graph uses CUDA/NVDEC input decode and NVENC final encode.")
        elif (
            has_crop(answers)
            and answers.get("use_gpu")
            and video_encoder != "copy"
            and str(video_encoder).endswith("_nvenc")
            and not use_cuda_fast_path
        ):
            codec_name = source_video_codec_name(answers) or "unknown"
            log_info(
                "CUDA decoder crop is unavailable for source codec "
                f"{codec_name}; using CPU crop filter before NVENC encode to avoid stretch."
            )

        if use_cuda_fast_path:
            cmd.extend(["-hwaccel", "cuda", "-hwaccel_device", str(GPU_DEVICE_INDEX), "-hwaccel_output_format", "cuda"])
            cuvid_crop = crop_margins_to_cuvid_crop(answers)
            if cuvid_crop:
                cuda_decoder = cuda_decoder_for_source(answers)
                if cuda_decoder:
                    cmd.extend(["-c:v", cuda_decoder])
                cmd.extend(["-crop", cuvid_crop])
        elif use_cuda_decode_complex:
            append_cuda_decode_args_for_input(cmd, answers)

    if single_cut:
        start, end = cut_keep_ranges[0]
        if start > 0:
            cmd.extend(["-ss", f"{start:.6f}"])

    cmd.extend(["-i", str(input_path)])
    if single_cut:
        start, end = cut_keep_ranges[0]
        cmd.extend(["-t", f"{max(0.0, end - start):.6f}"])

    # Chapter remapping: if timeline is modified and source has chapters,
    # generate a metadata file and add it as a second input so -map_chapters
    # can reference it. The metadata file path is stored for later cleanup.
    if (
        has_video
        and source_chapters_keep_enabled(answers)
        and timeline_is_modified(answers)
        and not answers.get("separator_points")  # Split path handles its own chapters.
    ):
        speed_factor = encode_video_speed_factor(answers) if video_speed_transform_enabled(answers) else 1.0
        chapter_plan = remap_chapters_for_encode(answers, speed_factor=speed_factor)
        if chapter_plan.get("mode") == "metadata" and chapter_plan.get("chapters"):
            chapter_temp_dir = tempfile.mkdtemp(prefix="ffmwiz_encode_chapters_")
            answers["_chapter_metadata_temp_dir"] = chapter_temp_dir
            metadata_path = write_encode_chapter_metadata(chapter_plan, Path(chapter_temp_dir))
            cmd.extend(["-i", str(metadata_path)])
            answers["_chapter_metadata_input_index"] = 1
            log_info(f"Chapters: injected metadata input at index 1 ({metadata_path})")

    # Determine audio mapping. When multi-range cuts are active, only one audio
    # output stream is produced by the filter_complex concat. Pick the first
    # selected audio in that path.
    if answers.get("audio_speed_from_video") and answers.get("audio_streams"):
        audio_indices = (
            selected_audio_streams(answers)
            if "audio_tracks" in answers
            else list(range(len(answers.get("audio_streams") or [])))
        )
    else:
        audio_indices = selected_audio_streams(answers) if answers.get("audio_streams") else []
    audio_transform_active = bool(audio_indices) and audio_transform_enabled(answers)
    if multi_cut and audio_cut_transform_enabled(answers):
        appio.note("Audio waveform cuts are skipped when video multi-range cuts are active.")
        audio_transform_active = audio_speed_transform_enabled(answers) or loudnorm_transform_enabled(answers)
    audio_for_cut: int | None = None
    if multi_cut and audio_indices:
        audio_for_cut = audio_indices[0]
        if len(audio_indices) > 1:
            appio.note(
                "Cuts active: only the first selected audio track survives the "
                f"filter_complex concat. Using stream index 0:a:{audio_for_cut}."
            )
        audio_indices = [audio_for_cut]

    full_source_map = can_use_full_source_map_for_simple_encode(
        answers,
        audio_indices,
        audio_transform_active,
        multi_cut,
    )

    if (
        has_video
        and video_encoder
        and video_encoder != "copy"
        and answers.get("separator_points")
        and append_single_input_split_outputs(
            cmd,
            answers,
            output_path,
            video_encoder,
            tag,
            profile,
            audio_indices,
            audio_transform_active,
            multi_cut,
            audio_for_cut,
        )
    ):
        return cmd

    extra_video_count = 0
    if full_source_map:
        cmd.extend(["-map", "0"])
        log_info("Full source stream map enabled for simple encode: -map 0")
    elif has_video:
        if multi_cut:
            cmd.extend(["-map", "[v]"])
        else:
            cmd.extend(["-map", "0:v:0"])
            extra_video_count = append_additional_source_video_maps(cmd, answers)

    if multi_cut and audio_for_cut is not None:
        cmd.extend(["-map", "[a]"])
    elif full_source_map:
        pass
    elif audio_transform_active and not multi_cut:
        pass
    elif not multi_cut:
        for audio_index in audio_indices:
            cmd.extend(["-map", f"0:a:{audio_index}"])

    subtitle_indices = [] if full_source_map else (
        selected_subtitle_streams(answers)
        if has_video and source_subtitles_keep_enabled(answers) and answers.get("subtitle_streams")
        else []
    )
    if multi_cut and subtitle_indices:
        appio.note("Cuts active: subtitle streams are not mapped through filter_complex and were skipped.")
        subtitle_indices = []
    if subtitle_indices and answers["output_ext"].lower() in MP4_LIKE_EXTS:
        allowed_subtitles: list[int] = []
        skipped_subtitles: list[int] = []
        for subtitle_index in subtitle_indices:
            codec = str(answers["subtitle_streams"][subtitle_index].get("codec_name", "")).lower()
            if codec in TEXT_SUBTITLE_CODECS:
                allowed_subtitles.append(subtitle_index)
            else:
                skipped_subtitles.append(subtitle_index)
        if skipped_subtitles:
            appio.note(f"Skipped non-text subtitle tracks for MP4/MOV output: {skipped_subtitles}")
        subtitle_indices = allowed_subtitles
    for subtitle_index in subtitle_indices:
        cmd.extend(["-map", f"0:s:{subtitle_index}"])
    attachments_mapped = embedded_attachment_keep_enabled(answers) if full_source_map else append_embedded_attachment_maps(cmd, answers)
    data_mapped = bool(source_data_streams(answers) and source_data_keep_enabled(answers)) if full_source_map else append_source_data_maps(cmd, answers)
    append_source_metadata_chapter_options(cmd, answers)

    if not full_source_map:
        append_negative_stream_options(cmd, answers, has_video, subtitle_indices, data_mapped)

    if has_video and video_encoder:
        video_bitrate = answers.get("video_bitrate_kbps")
        if video_encoder == "copy":
            cmd.extend(["-c:v", "copy"])
        else:
            if full_source_map:
                cmd.extend(["-c", "copy"])
            if multi_cut:
                fc = build_cut_filter_complex(answers, cut_keep_ranges, audio_for_cut)
                cmd.extend(["-filter_complex", fc])
            else:
                video_filter = build_video_filter(answers, use_gpu_filtering=use_cuda_fast_path)
                if video_filter:
                    cmd.extend(["-filter:v:0" if (extra_video_count or full_source_map) else "-filter:v", video_filter])
            if use_cuda_fast_path and answers.get("fps") is not None:
                if extra_video_count or full_source_map:
                    cmd.extend(["-r:v:0", str(answers["fps"]), "-fps_mode:v:0", "cfr"])
                else:
                    cmd.extend(["-r:v", str(answers["fps"]), "-fps_mode:v", "cfr"])
            cmd.extend(["-c:v:0" if full_source_map else "-c:v", video_encoder])

            if video_encoder.endswith("_nvenc"):
                cmd.extend(["-preset", NVENC_PRESET, "-tune", NVENC_TUNE, "-rc", NVENC_RC])
                append_nvenc_multipass_args(cmd, answers, video_encoder)
                if "hevc" in video_encoder:
                    cmd.extend(["-profile:v:0" if full_source_map else "-profile:v", hevc_profile_for_output(answers, profile)])
            elif video_encoder in {"libx264", "libx265"}:
                cmd.extend(["-preset", CPU_PRESET])
                if video_encoder == "libx265":
                    cmd.extend(["-profile:v:0" if full_source_map else "-profile:v", hevc_profile_for_output(answers, "main")])
            elif video_encoder == "libsvtav1":
                # SVT-AV1: integer preset + tune=0 (subjective visual quality).
                cmd.extend(["-preset", SVTAV1_PRESET, "-svtav1-params", SVTAV1_PARAMS])

            if video_bitrate:
                append_video_bitrate_args(cmd, answers, int(video_bitrate), ":v:0" if full_source_map else ":v")
            elif answers.get("video_crf") is not None:
                crf_value = answers["video_crf"]
                stream_spec = ":v:0" if full_source_map else ":v"
                if video_encoder.endswith("_nvenc"):
                    # NVENC constant quality: use constqp rc with -cq:v
                    cmd[cmd.index("-rc") + 1] = "constqp"
                    cmd.extend([f"-cq{stream_spec}", str(int(round(crf_value))), f"-b{stream_spec}", "0"])
                    log_info(f"NVENC constant quality: -rc constqp -cq{stream_spec} {int(round(crf_value))}")
                else:
                    # CPU encoder: -crf
                    cmd.extend([f"-crf", f"{crf_value:g}"])
                    log_info(f"CPU encoder constant quality: -crf {crf_value:g}")

            cmd.extend(color_range_output_args(answers, ":v:0", workflow="build_ffmpeg_command"))

            if tag and answers["output_ext"].lower() in MP4_LIKE_EXTS:
                cmd.extend(["-tag:v:0" if full_source_map else "-tag:v", tag])

            if extra_video_count:
                append_additional_source_video_codec_options(cmd, extra_video_count)

    audio_codec_for_stats: str | None = None
    if audio_indices:
        if audio_transform_active and not multi_cut:
            audio_fc, audio_labels = build_audio_transform_filter_complex(answers, audio_indices)
            cmd.extend(["-filter_complex", audio_fc])
            for label in audio_labels:
                cmd.extend(["-map", f"[{label}]"])
        audio_codec = normalize_audio_codec(
            answers.get("audio_codec"),
            default_audio_codec_for_ext(answers.get("output_ext", "")),
        )
        answers["audio_codec"] = audio_codec
        audio_codec_for_stats = audio_codec
        if audio_transform_active and audio_codec == "copy":
            appio.note("Audio copy cannot be used with audio filters such as speed/reverse, waveform cuts, or LoudNorm. AAC was selected for audio.")
            audio_codec = DEFAULT_AUDIO_CODEC
            answers["audio_codec"] = audio_codec
            audio_codec_for_stats = audio_codec
        if answers.get("output_ext", "").lower() == "webm" and audio_codec not in {"copy", "libopus", "libvorbis"}:
            appio.note("WebM audio was changed to libopus for container compatibility.")
            audio_codec = "libopus"
            answers["audio_codec"] = audio_codec
            audio_codec_for_stats = audio_codec
        if audio_codec == "copy":
            cmd.extend(["-c:a", "copy"])
        else:
            if audio_codec == "aac":
                log_info("AAC audio encoding is CPU-side; video CUDA/NVENC path is unaffected.")
            cmd.extend(["-c:a", audio_codec])
            audio_bitrate = answers.get("audio_bitrate_kbps")
            if audio_bitrate and audio_codec_uses_bitrate(audio_codec):
                cmd.extend(["-b:a", f"{audio_bitrate}k"])
            if AUDIO_CHANNELS:
                cmd.extend(["-ac", str(AUDIO_CHANNELS)])
            _ar = resolve_audio_sample_rate(answers)
            if _ar:
                cmd.extend(["-ar", str(_ar)])
    else:
        cmd.append("-an")

    output_is_processed = bool(has_video and video_encoder and video_encoder != "copy")
    output_is_processed = output_is_processed or bool(audio_indices and audio_codec_for_stats and audio_codec_for_stats != "copy")
    output_is_processed = output_is_processed or bool(subtitle_indices and answers["output_ext"].lower() in MP4_LIKE_EXTS)
    if output_is_processed:
        video_output_count_for_stats = 0
        if has_video:
            video_output_count_for_stats = len(answers.get("video_streams") or []) if full_source_map else 1 + extra_video_count
        audio_output_count_for_stats = 0
        if audio_indices:
            audio_output_count_for_stats = len(answers.get("audio_streams") or []) if full_source_map else len(audio_indices)
        subtitle_output_count_for_stats = len(answers.get("subtitle_streams") or []) if full_source_map else len(subtitle_indices)
        append_clear_reencoded_stream_stat_metadata(
            cmd,
            answers,
            video_output_count=video_output_count_for_stats,
            audio_output_count=audio_output_count_for_stats,
            subtitle_output_count=subtitle_output_count_for_stats,
        )

    if subtitle_indices:
        if answers["output_ext"].lower() in MP4_LIKE_EXTS:
            cmd.extend(["-c:s", "mov_text"])
        else:
            cmd.extend(["-c:s", "copy"])
    if attachments_mapped:
        append_embedded_attachment_codec_options(cmd, answers)
    if data_mapped:
        append_source_data_codec_options(cmd, answers)

    if answers["output_ext"].lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])

    cmd.append(str(output_path))
    return cmd


def step_start_now(answers: dict[str, Any]) -> None:
    if output_is_audio_only(answers) and not answers.get("audio_streams"):
        fail("Audio-only output was selected, but the input file has no audio stream.")

    join_items = []
    if answers.get("join_input_items"):
        join_items = [
            {
                "path": answers["input_path"],
                "probe": answers.get("probe") or {},
                "format": answers.get("format") or {},
                "streams": (
                    list(answers.get("video_streams") or [])
                    + list(answers.get("audio_streams") or [])
                    + list(answers.get("subtitle_streams") or [])
                    + list(answers.get("attachment_streams") or [])
                    + list(answers.get("data_streams") or [])
                ),
                "video_streams": answers.get("video_streams") or [],
                "audio_streams": answers.get("audio_streams") or [],
                "data_streams": answers.get("data_streams") or [],
                "duration": services.stream_duration_seconds({}, answers.get("format")) or 0.0,
            },
            *list(answers.get("join_input_items") or []),
        ]
    separator_specs = []
    if separator_specs:
        cmd = separator_specs[0]["cmd"]
        answers["separator_jobs"] = [
            {
                "index": spec["index"],
                "segment": spec["segment"],
                "output_path": spec["output_path"],
            }
            for spec in separator_specs
        ]
        answers["output_path"] = separator_specs[0]["output_path"]
    else:
        answers.pop("separator_jobs", None)
        if join_items:
            output_path = services.build_output_path(answers)
            answers["output_path"] = output_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            copy_compatible, reasons = join_copy_compatibility(join_items)
            # VFR join whose inputs match on everything except frame rate can use
            # the concat demuxer (stream copy) to keep each segment's own rate.
            if (
                answers.get("join_vfr")
                and not copy_compatible
                and any(item.get("video_streams") for item in join_items)
                and join_copy_compatible_except_fps(join_items)
            ):
                copy_compatible = True
                reasons = []
                appio.note("VFR join: using stream copy (concat) to preserve each file's frame rate.")
            can_copy = (
                copy_compatible
                and str(answers.get("video_codec", "")).lower() == "copy"
                and str(answers.get("audio_codec", "")).lower() == "copy"
                and answers.get("audio_tracks") in (None, "all")
                and source_metadata_keep_enabled(answers)
                and source_chapters_keep_enabled(answers)
                and source_subtitles_keep_enabled(answers)
                and not video_filters_required(answers)
                and not answers.get("cut_keep_ranges")
                and not loudnorm_transform_enabled(answers)
            )
            print_join_summary(join_items, copy_compatible, reasons)
            audio_only_join = all(not item.get("video_streams") for item in join_items)
            if audio_only_join:
                # Interactive-wizard audio join: stream-copy when compatible,
                # otherwise concatenate and re-encode the joined audio.
                if copy_compatible and str(answers.get("audio_codec", "")).lower() == "copy":
                    cmd = build_join_copy_command(answers, join_items, output_path)
                else:
                    appio.note("Joining audio inputs (concatenate and re-encode).")
                    cmd = build_join_audio_encode_command(answers, join_items, output_path)
            elif can_copy:
                cmd = build_join_copy_command(answers, join_items, output_path)
            else:
                if copy_compatible:
                    appio.note("Join inputs are stream-copy compatible, but selected encode settings require re-encoding.")
                else:
                    appio.note("Join inputs are not stream-copy compatible. Re-encoding is required.")
                cmd = build_join_encode_command(answers, join_items, output_path)
        else:
            cmd = build_ffmpeg_command(answers)
    answers["cmd"] = cmd
    ensure_color_range_resolved(answers, workflow="Main Wizard")
    log_crop_normalization_summary(answers)
    log_and_warn_pixel_format(answers)
    print_summary(answers, cmd)
    answers["start_now"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def run_wizard(answers: dict[str, Any], config: dict[str, Any] | None = None) -> None:
    # config is None for Mode 1 (full wizard). In Mode 2 it is the parsed
    # config.env: every question whose config value is non-empty is auto-applied
    # and SKIPPED; empty ones are asked (just like Mode 1). The unified graphical
    # editor and the final "start now?" question are ALWAYS asked.
    config_mode = config is not None

    def cfg_has(key: str) -> bool:
        return config_mode and config_value(config, key).strip() != ""

    # step name -> config key(s). A step is skipped only when ALL its keys are
    # provided. Steps not listed (join_inputs, unified_video_editor, cuts,
    # audio_cut, start_now) are NEVER auto-skipped, so they are always asked.
    skip_map: dict[str, tuple[str, ...]] = {
        "input_path": ("input_path",),
        "output_location": ("output_path",),
        "output_format": ("output_format",),
        "video_codec": ("video_codec",),
        "use_gpu": ("use_gpu",),
        "crop_enabled": ("crop",),
        "crop_top": ("crop",), "crop_left": ("crop",), "crop_right": ("crop",), "crop_bottom": ("crop",),
        "video_bitrate": ("video_bitrate_kbps",),
        "nvenc_multipass": ("nvenc_multipass",),
        "cpu_two_pass": ("cpu_two_pass",),
        "resolution": ("resolution",),
        "fps": ("fps",),
        "video_speed_reverse": ("video_speed", "reverse_video"),
        "audio_tracks": ("audio_tracks",),
        "loudnorm": ("loudnorm",),
        "audio_speed_reverse": ("audio_speed", "reverse_audio"),
        "audio_codec": ("audio_codec",),
        "audio_bitrate": ("audio_bitrate_kbps",),
        "audio_sample_rate": ("audio_sample_rate",),
        "source_extras": ("keep_source_metadata",),
        "subtitle_tracks": ("subtitle_tracks",),
        "color_range": ("color_range",),
    }

    applied = {"done": False}

    def ensure_config_applied() -> None:
        if not config_mode or applied["done"]:
            return
        if not answers.get("input_path"):
            return
        if not (answers.get("video_streams") or answers.get("audio_streams")):
            return
        apply_config_settings_after_input(
            answers, config, skip_crop=not cfg_has("crop"), force_video_options=True
        )
        seed_unified_editor_from_config(answers, config)
        answers["_config_mode"] = True
        applied["done"] = True

    def is_config_skipped(step: "Step") -> bool:
        if not config_mode or not applied["done"]:
            return False
        keys = skip_map.get(step.name)
        if not keys:
            return False
        return all(cfg_has(k) for k in keys)

    steps = [
        Step("input_path", lambda a: True, step_input_path),
        Step("join_inputs", wizard_join_inputs_applicable, step_join_additional_inputs_for_encode),
        Step("output_location", lambda a: True, step_output_location),
        Step("output_format", lambda a: True, step_output_format),
        Step("video_codec", output_has_video, step_video_codec),
        Step("use_gpu", output_has_video, step_use_gpu),
        Step("unified_video_editor", output_has_video, step_unified_video_editor_for_encode),
        Step("crop_enabled", lambda a: output_has_video(a) and not a.get("_unified_video_editor_declined"), step_crop_enabled),
        Step("crop_top", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_top),
        Step("crop_left", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_left),
        Step("crop_right", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_right),
        Step("crop_bottom", lambda a: output_has_video(a) and a.get("crop_enabled") and not a.get("crop_values_inline"), step_crop_bottom),
        Step("video_bitrate", video_reencode_options_applicable, step_video_bitrate),
        Step("nvenc_multipass", nvenc_multipass_prompt_applicable, step_nvenc_multipass),
        Step("cpu_two_pass", cpu_two_pass_applicable, step_cpu_two_pass),
        Step("resolution", video_reencode_options_applicable, step_resolution),
        Step("fps", video_reencode_options_applicable, step_fps),
        Step("video_speed_reverse", lambda a: output_has_video(a) and not a.get("_unified_video_editor_used") and not a.get("_unified_video_editor_declined"), step_video_speed_reverse_for_encode),
        Step("cuts", lambda a: video_reencode_options_applicable(a) and not a.get("_unified_video_editor_used") and not a.get("_unified_video_editor_declined"), step_cuts),
        Step("audio_tracks", lambda a: bool(a.get("audio_streams")), step_audio_tracks),
        Step("loudnorm", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_loudnorm),
        Step("audio_cut", audio_only_transform_prompt_applicable, step_audio_cut_for_encode),
        Step("audio_speed_reverse", audio_only_transform_prompt_applicable, step_audio_speed_reverse_for_encode),
        Step("audio_codec", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True), step_audio_codec),
        Step("audio_bitrate", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy" and audio_codec_uses_bitrate(str(a.get("audio_codec") or default_audio_codec_for_ext(a.get("output_ext", "")))), step_audio_bitrate),
        Step("audio_sample_rate", lambda a: bool(a.get("audio_streams")) and bool(selected_audio_streams(a) if "audio_tracks" in a else True) and a.get("audio_codec") != "copy", step_audio_sample_rate),
        Step("source_extras", source_extra_policy_applicable, step_source_extra_policy),
        Step("subtitle_tracks", lambda a: output_has_video(a) and source_subtitles_keep_enabled(a) and bool(a.get("subtitle_streams")), step_subtitle_tracks),
        Step("color_range", color_range_prompt_applicable, step_color_range),
        Step("start_now", lambda a: True, step_start_now),
    ]

    def runnable(pos: int) -> bool:
        return steps[pos].applicable(answers) and not is_config_skipped(steps[pos])

    def next_index(start: int) -> int:
        idx = start
        while idx < len(steps) and not runnable(idx):
            idx += 1
        return idx

    def prev_index(start: int) -> int:
        idx = start
        while idx > 0 and (
            not runnable(idx)
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
        join_pos = next((pos for pos, step in enumerate(steps) if step.name == "join_inputs"), -1)
        for pos in range(current + 1):
            if runnable(pos) and not is_auto_unified_crop_step(pos):
                count += 1
        extra = int(answers.get("_join_question_extra", 0) or 0) if join_pos >= 0 and current >= join_pos else 0
        return answers.get("_question_offset", 0) + count + extra

    # In config mode, pre-load the input file from config (so the input question
    # is skipped) and apply every provided setting before the first question.
    if config_mode and cfg_has("input_path"):
        cfg_input = terminal_path(config_value(config, "input_path"))
        if not cfg_input.exists() or not cfg_input.is_file():
            fail(f"input_path in config.env does not exist: {cfg_input}")
        load_input_metadata(answers, cfg_input)
        trackmanager.print_source_info(answers)
    ensure_config_applied()

    idx = next_index(0)
    while idx < len(steps):
        try:
            answers["_question_number"] = question_number(idx)
            steps[idx].run(answers)
            apply_unified_video_editor_answers(answers)
            ensure_config_applied()
            idx = next_index(idx + 1)
        except Back:
            if idx == 0:
                raise
            idx = prev_index(idx - 1)


def print_summary(answers: dict[str, Any], cmd: list[str]) -> None:
    two_pass_display = cpu_two_pass_enabled_for_command(answers, cmd)
    if two_pass_display:
        pass1_cmd, pass2_cmd, _passlog = build_cpu_two_pass_commands(cmd, answers)
        log_info("Final PowerShell command (CPU two-pass pass 1/2): " + command_to_powershell(pass1_cmd))
        log_info("Final PowerShell command (CPU two-pass pass 2/2): " + command_to_powershell(pass2_cmd))
        log_command("Actual final subprocess pass 1/2", pass1_cmd)
        log_command("Actual final subprocess pass 2/2", pass2_cmd)
    else:
        log_info("Final PowerShell command: " + command_to_powershell(cmd))
        log_command("Actual final subprocess", cmd)
    log_final_normalized_answers(answers, cmd)
    log_info(
        "Selected settings: input={}; output={}; format={}; video_codec={}; audio_codec={}; crop={}; fps={}; resolution={}".format(
            answers.get("input_path"), answers.get("output_path"), answers.get("output_ext"),
            answers.get("video_codec"), answers.get("audio_codec"),
            format_crop_margins(answers) if answers.get("crop_enabled") else "no",
            answers.get("fps") or "source", format_resolution_summary(answers.get("resolution")),
        )
    )
    print()
    if two_pass_display:
        pass1_cmd, pass2_cmd, _passlog = build_cpu_two_pass_commands(cmd, answers)
        print(paint("Final PowerShell command (CPU two-pass pass 1/2):", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        print(paint(command_to_powershell(pass1_cmd), Color.FINAL_COMMAND_TEXT))
        print()
        print(paint("Final PowerShell command (CPU two-pass pass 2/2):", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        print(paint(command_to_powershell(pass2_cmd), Color.FINAL_COMMAND_TEXT))
    else:
        print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    print()
    print(paint("Selected settings summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("input", answers["input_path"], Color.WHITE))
    if answers.get("join_input_items"):
        join_items = list(answers.get("join_input_items") or [])
        print("  " + field_text("joined inputs", len(join_items) + 1, Color.LIGHT_BLUE))
        print("    " + paint(f"1. {Path(answers['input_path']).name}", Color.WHITE))
        for idx, item in enumerate(join_items, start=2):
            print("    " + paint(f"{idx}. {Path(item.get('path')).name}", Color.WHITE))
        print("  " + field_text("join settings", "video/audio settings apply by track number to every joined input", Color.YELLOW))
    print("  " + field_text("output", answers["output_path"], Color.LIME))
    if output_has_video(answers):
        print("  " + field_text("video codec", answers.get("video_codec", DEFAULT_VIDEO_CODEC), Color.CYAN))
        _resolved_encoder = resolve_video_encoder(answers)[0]
        print("  " + field_text("source bit depth", describe_video_bit_depth(source_video_stream(answers) or {}), Color.PINK))
        print("  " + field_text("output bit depth", f"{output_video_bit_depth(answers)}-bit", Color.PINK))
        if str(_resolved_encoder).lower() != "copy":
            print("  " + field_text("encoder", _resolved_encoder, Color.CYAN))
            if "hevc" in str(_resolved_encoder) or str(_resolved_encoder) in {"libx265"}:
                _default_profile = "main" if str(_resolved_encoder) in {"libx265"} else None
                print("  " + field_text("profile", hevc_profile_for_output(answers, _default_profile), Color.CYAN))
            print("  " + field_text(
                "pixel format",
                cuda_pixel_format_for_output(answers) if (str(_resolved_encoder).endswith("_nvenc") and can_use_cuda_fast_path(answers, _resolved_encoder))
                else cpu_graph_pixel_format_for_encoder(answers, _resolved_encoder),
                Color.ORANGE,
            ))
            print("  " + field_text("path", filter_graph_path_label(answers, _resolved_encoder), Color.AQUA))
            _precision_note = bit_depth_precision_note(answers)
            if _precision_note:
                print("  " + field_text("precision note", _precision_note, Color.NOTE_YELLOW))
        print("  " + field_text("GPU", "yes" if answers.get("use_gpu") else "no", Color.GREEN if answers.get("use_gpu") else Color.YELLOW))
        if "_nvenc" in command_to_text(cmd):
            print("  " + field_text("NVENC multipass", normalize_nvenc_multipass_mode(answers.get("nvenc_multipass")), Color.YELLOW))
        if answers.get("crop_enabled"):
            print("  " + field_text("crop", "yes, " + format_crop_margins(answers), Color.ORANGE))
            try:
                adj_left, adj_right, adj_top, adj_bottom = normalized_crop_margins(answers)
                req = (
                    int(answers.get("crop_left", 0) or 0),
                    int(answers.get("crop_right", 0) or 0),
                    int(answers.get("crop_top", 0) or 0),
                    int(answers.get("crop_bottom", 0) or 0),
                )
                if (adj_left, adj_right, adj_top, adj_bottom) != req:
                    print("  " + field_text(
                        "crop (aligned)",
                        f"top={adj_top} px, left={adj_left} px, right={adj_right} px, bottom={adj_bottom} px",
                        Color.ORANGE,
                    ))
                crop_w, crop_h = cropped_source_size(answers)
                print("  " + field_text("cropped resolution", f"{crop_w}x{crop_h}", Color.ORANGE))
            except ValueError:
                pass
            crop_box = answers.get("crop_box_dimensions")
            crop_ar = answers.get("cropped_aspect_ratio")
            if crop_box and crop_ar:
                print("  " + field_text("crop box", f"{crop_box[0]}x{crop_box[1]} (AR {crop_ar:.4f})", Color.ORANGE))
        else:
            print("  " + field_text("crop", "no", Color.GREEN))
        print("  " + field_text("video bitrate", str(answers.get("video_bitrate_kbps") or "source/default") + " kbps", Color.YELLOW))
        if answers.get("cpu_two_pass"):
            print("  " + field_text("CPU two-pass", "yes", Color.YELLOW))
        print("  " + field_text("resolution", format_resolution_summary(answers.get("resolution")), Color.MAGENTA))
        if answers.get("final_resolution"):
            final_w, final_h = answers["final_resolution"]
            print("  " + field_text("final output resolution", f"{final_w}x{final_h}", Color.LIME))
        # Color-range and SAR/DAR summary. The summary is a display surface, so
        # an unresolved unknown range (e.g. a pure stream-copy that writes no
        # color-range metadata) is reported honestly instead of raising.
        try:
            resolved_range, range_source = resolve_color_range(answers, workflow="print_summary")
        except ColorRangeUnresolvedError:
            resolved_range, range_source = "", "unresolved (no metadata written)"
        detected_range = display_color_range((source_video_stream(answers) or {}).get("color_range"))
        is_copy = str(resolve_video_encoder(answers)[0]).lower() == "copy"
        print("  " + field_text("detected source color range", detected_range, Color.COLOR_RANGE_VALUE))
        if is_copy:
            # Stream copy: bitstream range signaling is preserved from the source;
            # the re-encode menu semantics do not apply.
            print("  " + field_text("color-range policy", "stream copy (preserved from source)", Color.COLOR_RANGE_VALUE))
            print("  " + field_text(
                "output color-range metadata", f"{detected_range} (preserved from copied stream)", Color.COLOR_RANGE_VALUE))
        elif range_source == "user choice":
            # "Do not force a range in FFmWiz" (option 2). Capability is resolved
            # from the per-environment FFmpeg cache (lazy probe). Without a
            # verified result we report conservatively rather than guessing.
            cap = services.resolve_capability(answers, allow_probe=not answers.get("_no_capability_probe"))
            print("  " + field_text("requested color-range policy", "do not force", Color.COLOR_RANGE_VALUE))
            print("  " + field_text("FFmWiz explicit color-range option", "omitted", Color.COLOR_RANGE_VALUE))
            print("  " + field_text("capability source", cap["capability_source"], Color.COLOR_RANGE_VALUE))
            print("  " + field_text("capability environment fingerprint", cap["env_short"], Color.DIM))
            if cap.get("status") == "verified" and cap.get("expected_final_range") is not None:
                fr = cap["expected_final_range"]
                shown = "unspecified" if fr in {"unknown", "", None} else fr
                print("  " + field_text("expected encoder-reported final range", shown, Color.COLOR_RANGE_VALUE))
                if cap.get("verified_at_utc"):
                    print("  " + field_text("verified probe timestamp", cap["verified_at_utc"], Color.DIM))
            else:
                print("  " + field_text("expected encoder-reported final range",
                                        "unknown until verified", Color.COLOR_RANGE_VALUE))
        else:
            print("  " + field_text(
                "resolved color range",
                (resolved_range or "unspecified") + f" ({range_source})",
                Color.COLOR_RANGE_VALUE,
            ))
            print("  " + field_text(
                "output color-range metadata",
                resolved_range if resolved_range else "omitted",
                Color.COLOR_RANGE_VALUE,
            ))
        print("  " + field_text("pixel-value range conversion", "no", Color.DIM))
        _sd = sar_dar_info(answers)
        print("  " + field_text(
            "source SAR",
            f"{_sd['sar_text']} ({_sd['sar_source']})",
            Color.AQUA,
        ))
        print("  " + field_text(
            "source DAR",
            f"{_sd['dar_text']} ({_sd['dar_source']})",
            Color.AQUA,
        ))
        if _sd.get("effective_dar_decimal"):
            print("  " + field_text("effective DAR", f"{_sd['effective_dar_decimal']:.6f}", Color.AQUA))
        print("  " + field_text("pixel shape", _sd["pixel_shape"], Color.PINK))
        if _sd.get("warning"):
            print("  " + field_text("geometry warning", _sd["warning"], Color.YELLOW))
        # Pixel-format operation summary.
        try:
            if str(resolve_video_encoder(answers)[0]).lower() != "copy":
                _pf = pixel_format_analysis(answers)
                _src, _tgt = _pf["source"], _pf["target"]
                print("  " + field_text(
                    "source pixel format",
                    f"{_src['pix_fmt']} ({(str(_src['bit_depth']) + '-bit') if _src['bit_depth'] else 'unknown-bit'}, {_src['chroma']})",
                    Color.ORANGE,
                ))
                print("  " + field_text(
                    "target pixel format",
                    f"{_tgt['pix_fmt']} ({(str(_tgt['bit_depth']) + '-bit') if _tgt['bit_depth'] else 'unknown-bit'}, {_tgt['chroma']})",
                    Color.ORANGE,
                ))
                print("  " + field_text("pixel-format operation", _pf["operation"], Color.PINK))
                print("  " + field_text("bit-depth conversion", _pf["bit_depth_conversion"], Color.PINK))
                print("  " + field_text("chroma-subsampling conversion", _pf["chroma_conversion"], Color.PINK))
        except Exception:
            pass
        print("  " + field_text("fps", answers.get("fps") or "source", Color.MAGENTA))
        if video_speed_transform_enabled(answers):
            print("  " + field_text("video speed", f"{encode_video_speed_factor(answers) * 100:.0f}%", Color.MAGENTA))
            print("  " + field_text("reverse video", "yes" if answers.get("reverse_video") else "no", Color.ORANGE))
            if answers.get("reverse_video"):
                appio.note(
                    "FFmWiz runs reverse video in short segments to avoid buffering the whole clip in RAM. "
                    "The single command above is an equivalent simple reference command."
                )
        if answers.get("separator_points"):
            print(paint(format_split_points_for_summary(answers.get("separator_points") or [], services.get_video_fps(answers), "Split points"), Color.LIGHT_BLUE))
        if answers.get("split_output_paths"):
            print("  " + field_text("Split output parts", len(answers.get("split_output_paths") or []), Color.LIGHT_BLUE))
            split_intervals = list(answers.get("split_part_intervals") or [])
            for idx, part_path in enumerate(answers.get("split_output_paths") or [], start=1):
                interval_text = ""
                if idx - 1 < len(split_intervals):
                    start, end = split_intervals[idx - 1]
                    duration = max(0.0, end - start)
                    interval_text = f"  [{seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}, duration {format_elapsed(duration)}]"
                print("    " + field_text(f"Part {idx:02d}", str(part_path) + interval_text, Color.LIME))
    if answers.get("audio_streams"):
        print("  " + field_text("audio tracks", answers.get("audio_tracks"), Color.LIGHT_BLUE))
        print("  " + field_text("audio codec", answers.get("audio_codec"), Color.CYAN))
        print("  " + field_text("audio bitrate", str(answers.get("audio_bitrate_kbps") or "source/default") + " kbps", Color.YELLOW))
        _sr = resolve_audio_sample_rate(answers)
        if answers.get("join_input_items"):
            print("  " + field_text("audio sample rate", f"{join_target_sample_rate(answers)} Hz (uniform across joined inputs)", Color.AUDIO_SAMPLE_RATE))
        else:
            print("  " + field_text("audio sample rate", f"{_sr} Hz" if _sr else "keep source", Color.AUDIO_SAMPLE_RATE))
        if audio_cut_transform_enabled(answers):
            print(paint(format_audio_ranges_for_summary(answers["audio_cut_keep_ranges"], "audio cuts (keep ranges)"), Color.LIME))
        if audio_speed_transform_enabled(answers):
            print("  " + field_text("audio speed", f"{encode_audio_speed_factor(answers) * 100:.0f}%", Color.MAGENTA))
            print("  " + field_text("reverse audio", "yes" if encode_audio_reverse_enabled(answers) else "no", Color.ORANGE))
        if loudnorm_transform_enabled(answers):
            mode = loudnorm_mode(answers)
            mode_text = {"single": "Single-pass", "two_pass": "Two-pass"}.get(mode, mode)
            target_i = answers.get("loudnorm_target_i", LOUDNORM_DEFAULT_TARGET_I)
            print("  " + field_text(
                "LoudNorm",
                f"{mode_text} on final output audio  (I={target_i:g}, TP={LOUDNORM_TARGET_TP:g}, LRA={LOUDNORM_TARGET_LRA:g})",
                Color.MEAN_VOLUME,
            ))
            if mode == "two_pass":
                source_text = (
                    "final joined audio from all selected input clips"
                    if answers.get("join_input_items") else "final output audio"
                )
                print("    " + field_text("measurement source", source_text, Color.MEAN_VOLUME))
    if output_has_video(answers) and answers.get("subtitle_streams"):
        if source_subtitles_keep_enabled(answers):
            print("  " + field_text("subtitle tracks", answers.get("subtitle_tracks"), Color.WHITE))
        else:
            print("  " + field_text("subtitle tracks", "removed by metadata policy", Color.ORANGE))
    if output_has_video(answers) and services.source_extra_preservation_features(answers):
        print("  " + field_text("source metadata", "keep" if source_metadata_keep_enabled(answers) else "remove", Color.LIGHT_BLUE))
        print("  " + field_text("chapters", "keep" if source_chapters_keep_enabled(answers) else "remove", Color.LIGHT_BLUE))
    if output_has_video(answers) and embedded_attachment_streams(answers):
        attachment_state = "yes" if embedded_attachment_keep_enabled(answers) else "no"
        print("  " + field_text("embedded attachments", attachment_state, Color.PINK))
    cut_keep_ranges = answers.get("cut_keep_ranges") or []
    if cut_keep_ranges:
        fps = services.get_video_fps(answers)
        print(paint(
            format_cut_ranges_for_summary(cut_keep_ranges, fps, "cuts (keep ranges)"),
            Color.LIME,
        ))


def graphical_hint(text: str) -> str:
    if USE_COLOR:
        return f"{Color.AQUA}{text}{Color.RESET}{Color.HINT_YELLOW}"
    return text


def step_video_speed_reverse_options(answers: dict[str, Any]) -> None:
    ensure_video_input(answers)
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Change video speed or reverse video?",
                f"y/n, {graphical_hint('g=Show Graphical Video Speed Editor')}; "
                "y=manual speed/reverse settings",
                "n",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["_speed_reverse_noop"] = True
            return
        if lowered in {"g", "gui", "graphical"}:
            result = guibridge.open_video_speed_gui(answers)
            if result is None:
                appio.note("Graphical video speed editor was canceled.")
                continue
            answers["speed_factor"] = result["speed"]
            answers["reverse_video"] = result["reverse"]
            answers["include_audio"] = result["include_audio"]
            answers["_speed_reverse_noop"] = False
            return
        if lowered in {"y", "yes"}:
            while True:
                speed_text = appio.ask_raw(
                    "Enter speed factor or percent (examples: 0.5x, 1.25x, 125%; Enter=100%; 0=back): "
                )
                if is_back_value(speed_text):
                    break
                if not speed_text:
                    speed_text = "100%"
                try:
                    answers["speed_factor"] = parse_speed_factor(speed_text)
                    break
                except ValueError as exc:
                    appio.error(str(exc))
            if "speed_factor" not in answers:
                continue
            answers["reverse_video"] = appio.ask_yes_no(yn_prompt("Reverse video too?", False), False)
            include_default = bool(answers.get("audio_streams"))
            answers["include_audio"] = appio.ask_yes_no(yn_prompt("Sync all audio tracks with the video speed/reverse change?", include_default), include_default)
            answers["_speed_reverse_noop"] = False
            return
        appio.error("Enter y, n, or g.")


def step_video_speed_reverse_for_encode(answers: dict[str, Any]) -> None:
    for key in ("video_speed_enabled", "video_speed_factor", "reverse_video", "audio_speed_from_video"):
        answers.pop(key, None)
    if answers.get("_unified_video_editor_used"):
        speed = clamp_speed_factor(answers.get("_unified_video_speed", DEFAULT_SPEED_FACTOR))
        reverse = bool(answers.get("_unified_reverse_video"))
        answers["video_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
        answers["video_speed_factor"] = speed
        answers["reverse_video"] = reverse
        answers["audio_speed_from_video"] = bool(answers.get("_unified_include_audio"))
        if answers["video_speed_enabled"]:
            print(paint(f"Applied unified speed/reverse: {speed:.2f}x, reverse={'yes' if reverse else 'no'}", Color.LIME))
        return
    allow_gui = not answers.get("_disable_graphical_editors") and not answers.get("_disable_followup_video_gui_prompts")
    while True:
        hint = (
            f"y/n, {graphical_hint('g=Show Graphical Video Speed Editor')}; "
            "speed/reverse requires video re-encoding"
            if allow_gui
            else "y/n; speed/reverse requires video re-encoding"
        )
        value = appio.ask_raw(appio.question_prompt(answers, "Change video speed or reverse video?", hint, "n"))
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["video_speed_enabled"] = False
            return
        if lowered in {"g", "gui", "graphical"}:
            if not allow_gui:
                appio.error("Graphical video speed editor is not available here. Use the Unified Video Editor or manual settings.")
                continue
            appio.note("Loading Graphical Video Speed Editor...")
            sys.stdout.flush()
            result = guibridge.open_video_speed_gui(answers)
            if result is None:
                appio.note("Graphical video speed editor was canceled. Returning to the speed question.")
                continue
            answers["video_speed_enabled"] = True
            answers["video_speed_factor"] = result["speed"]
            answers["reverse_video"] = result["reverse"]
            answers["audio_speed_from_video"] = bool(result.get("include_audio"))
            return
        if lowered in {"y", "yes"}:
            while True:
                speed_text = appio.ask_raw("Enter video speed factor or percent (examples: 0.5x, 1.25x, 125%; Enter=100%; 0=back): ")
                if is_back_value(speed_text):
                    break
                if not speed_text:
                    speed_text = "100%"
                try:
                    answers["video_speed_factor"] = parse_speed_factor(speed_text)
                    break
                except ValueError as exc:
                    appio.error(str(exc))
            if "video_speed_factor" not in answers:
                continue
            answers["reverse_video"] = appio.ask_yes_no(yn_prompt("Reverse video too?", False), False)
            if answers.get("audio_streams"):
                answers["audio_speed_from_video"] = appio.ask_yes_no(yn_prompt("Apply the same speed/reverse to selected audio too?", True), True)
            answers["video_speed_enabled"] = True
            return
        appio.error("Enter y, n, or g." if allow_gui else "Enter y or n.")


def step_audio_speed_reverse_options(answers: dict[str, Any]) -> None:
    audio_index = int(answers.get("audio_index", 0))
    while True:
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Change audio speed or reverse audio?",
                f"y/n, {graphical_hint('g=Show Graphical Audio Speed Editor')}; "
                "y=manual speed/reverse settings",
                "n",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["_speed_reverse_noop"] = True
            return
        if lowered in {"g", "gui", "graphical"}:
            result = open_audio_speed_gui(answers, audio_index)
            if result is None:
                appio.note("Graphical audio speed editor was canceled.")
                continue
            answers["speed_factor"] = result["speed"]
            answers["reverse_audio"] = result["reverse"]
            answers["_speed_reverse_noop"] = False
            return
        if lowered in {"y", "yes"}:
            while True:
                speed_text = appio.ask_raw(
                    "Enter speed factor or percent (examples: 0.5x, 1.25x, 125%; Enter=100%; 0=back): "
                )
                if is_back_value(speed_text):
                    break
                if not speed_text:
                    speed_text = "100%"
                try:
                    answers["speed_factor"] = parse_speed_factor(speed_text)
                    break
                except ValueError as exc:
                    appio.error(str(exc))
            if "speed_factor" not in answers:
                continue
            answers["reverse_audio"] = appio.ask_yes_no(yn_prompt("Reverse audio too?", False), False)
            answers["_speed_reverse_noop"] = False
            return
        appio.error("Enter y, n, or g.")


def step_audio_speed_reverse_for_encode(answers: dict[str, Any]) -> None:
    for key in ("audio_speed_enabled", "audio_speed_factor", "reverse_audio"):
        answers.pop(key, None)
    allow_gui = not answers.get("_disable_graphical_editors")
    selected = selected_audio_streams(answers) if answers.get("audio_tracks") is not None else [0]
    audio_index = selected[0] if selected else 0
    while True:
        suffix = " Enter=n keeps any video-linked audio speed." if answers.get("audio_speed_from_video") else ""
        hint = (
            f"y/n, {graphical_hint('g=Show Graphical Audio Speed Editor')}; applies to selected audio tracks.{suffix}"
            if allow_gui
            else f"y/n; applies to selected audio tracks.{suffix}"
        )
        value = appio.ask_raw(appio.question_prompt(answers, "Change audio speed or reverse audio?", hint, "n"))
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            answers["audio_speed_enabled"] = False
            return
        if lowered in {"g", "gui", "graphical"}:
            if not allow_gui:
                appio.error("Graphical audio speed editor is not available in Folder Encode.")
                continue
            appio.note("Loading Graphical Audio Speed Editor...")
            sys.stdout.flush()
            result = open_audio_speed_gui(answers, audio_index)
            if result is None:
                appio.note("Graphical audio speed editor was canceled. Returning to the speed question.")
                continue
            answers["audio_speed_enabled"] = True
            answers["audio_speed_factor"] = result["speed"]
            answers["reverse_audio"] = result["reverse"]
            answers["audio_speed_from_video"] = False
            return
        if lowered in {"y", "yes"}:
            while True:
                speed_text = appio.ask_raw("Enter audio speed factor or percent (examples: 0.5x, 1.25x, 125%; Enter=100%; 0=back): ")
                if is_back_value(speed_text):
                    break
                if not speed_text:
                    speed_text = "100%"
                try:
                    answers["audio_speed_factor"] = parse_speed_factor(speed_text)
                    break
                except ValueError as exc:
                    appio.error(str(exc))
            if "audio_speed_factor" not in answers:
                continue
            answers["reverse_audio"] = appio.ask_yes_no(yn_prompt("Reverse audio too?", False), False)
            answers["audio_speed_enabled"] = True
            answers["audio_speed_from_video"] = False
            return
        appio.error("Enter y, n, or g." if allow_gui else "Enter y or n.")


def step_audio_cut_editor(answers: dict[str, Any]) -> None:
    audio_index = int(answers.get("audio_index", 0))
    while True:
        ranges = open_audio_cut_gui(answers, audio_index)
        if ranges is None:
            appio.note("Graphical audio cut editor was canceled.")
            try_again = appio.ask_yes_no(yn_prompt("Open it again?", True), True)
            if not try_again:
                answers["_audio_cut_noop"] = True
                return
            continue
        if not ranges:
            appio.error("No valid audio ranges were selected.")
            continue
        answers["audio_keep_ranges"] = ranges
        answers["_audio_cut_noop"] = False
        return


def _run_gui_audio_transform(answers: dict[str, Any], audio_index: int) -> None:
    """Graphical audio transform path. Sets the transform answers or marks
    _audio_transform_noop. Raises Back to return to the editor menu."""
    while True:
        result = guibridge.open_audio_transform_gui(answers, audio_index)
        if result is None:
            appio.note("Graphical audio transform editor was canceled.")
            action = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    "Reopen the editor, enter values manually, or skip?",
                    "g=graphical editor, m=manual entry, n=skip",
                    "g",
                )
            ).strip().lower()
            if is_back_value(action):
                raise Back()
            if action in {"m", "manual"}:
                _apply_manual_audio_transform(answers)
                return
            if action in {"n", "no", "skip"}:
                answers["_audio_transform_noop"] = True
                return
            continue  # 'g' or empty -> reopen
        keep_ranges = list(result.get("keep_ranges") or [])
        speed = clamp_speed_factor(result.get("speed", DEFAULT_SPEED_FACTOR))
        reverse = bool(result.get("reverse"))
        if not (keep_ranges or reverse or abs(speed - 1.0) > 1e-6):
            appio.note("No audio transform was selected in the editor.")
            action = appio.ask_raw(
                appio.question_prompt(
                    answers,
                    "Nothing changed. Enter values manually, reopen editor, or skip?",
                    "m=manual entry, g=graphical editor, n=skip",
                    "m",
                )
            ).strip().lower()
            if is_back_value(action):
                raise Back()
            if action in {"g", "graphical"}:
                continue
            if action in {"n", "no", "skip"}:
                answers["_audio_transform_noop"] = True
                return
            _apply_manual_audio_transform(answers)
            return
        answers["audio_cut_keep_ranges"] = keep_ranges
        answers["audio_speed_enabled"] = bool(reverse or abs(speed - 1.0) > 1e-6)
        answers["audio_speed_factor"] = speed
        answers["reverse_audio"] = reverse
        answers["_audio_transform_noop"] = False
        return


def _confirm_audio_transform_start(answers: dict[str, Any]) -> None:
    """Build the command, print the summary, and ask "Start FFmpeg now?".
    Raises Back (on '0') so the editor can return to the value prompts."""
    split_points = answers.get("_audio_transform_split_points")
    if split_points:
        # Ask which lossless container extension to use (copy-compatible list).
        _ai = int(answers.get("audio_index", 0))
        _astreams = answers.get("audio_streams") or []
        _acodec = str(_astreams[_ai].get("codec_name", "")) if 0 <= _ai < len(_astreams) else ""
        ask_lossless_split_ext(answers, _acodec)
        cmd, pattern = build_lossless_split_command(answers, split_points)
        answers["cmd"] = cmd
        answers["output_path"] = pattern
        print()
        print(paint("Lossless audio split (stream copy, selected audio track only):", Color.BOLD + Color.LIME))
        print("  " + field_text("input", answers["input_path"], Color.WHITE))
        _acodec_disp = _acodec or "unknown"
        print("  " + field_text("audio track", f"{_ai + 1} (codec: {_acodec_disp})", Color.AQUA))
        print("  " + field_text(
            "output container",
            f"{pattern.suffix.lstrip('.')} (holds {_acodec_disp} losslessly; tracks have a codec, not an extension)",
            Color.LIME,
        ))
        print("  " + field_text("output parts", f"{len(split_points) + 1} files -> {pattern.name}", Color.LIME))
        print(paint(format_split_points_for_summary(split_points, float(answers.get("fps") or 25.0)), Color.LIGHT_BLUE))
        appio.note("Stream-copy split: rejoining the audio parts reproduces the original audio stream.")
        print()
        print(paint("Final PowerShell command:", Color.BOLD + Color.FINAL_COMMAND_LABEL))
        log_info("Final PowerShell command: " + command_to_powershell(cmd))
        print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    else:
        cmd = build_audio_transform_command(answers)
        answers["cmd"] = cmd
        print_transform_summary(answers, cmd, "Audio Cut / Speed / Reverse")
    answers["start_now"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def step_audio_transform_editor(answers: dict[str, Any]) -> None:
    audio_index = int(answers.get("audio_index", 0))
    answers.pop("_audio_transform_split_points", None)
    answers.pop("lossless_split_ext", None)
    answers["_audio_transform_finalized"] = False
    # Stage machine so Back goes one prompt back instead of jumping to the start:
    #   menu  -> (Back) previous wizard step
    #   configure (values) -> (Back) menu
    #   confirm ("Start now?") -> (Back) configure / value prompts
    stage = "menu"
    choice = "1"
    while True:
        if stage == "menu":
            print()
            print(paint("Audio transform:", Color.BOLD + Color.LIGHT_BLUE))
            print(selection_menu_line(1, "Graphical editor (waveform cuts + speed / reverse)"))
            print(selection_menu_line(2, "Enter values manually (cuts + speed + reverse)"))
            choice_value = appio.ask_raw(appio.question_prompt(answers, "Select an option", None, "1")).strip()
            if is_back_value(choice_value):
                raise Back()
            if not choice_value:
                choice_value = "1"
            if choice_value not in {"1", "2"}:
                appio.error("Enter 1 or 2.")
                continue
            choice = choice_value
            stage = "configure"
        elif stage == "configure":
            answers["_audio_transform_noop"] = False
            try:
                if choice == "2":
                    _apply_manual_audio_transform(answers)
                else:
                    _run_gui_audio_transform(answers, audio_index)
            except Back:
                stage = "menu"
                continue
            if answers.get("_audio_transform_noop"):
                return  # nothing selected; no confirmation needed
            stage = "confirm"
        else:  # confirm
            try:
                _confirm_audio_transform_start(answers)
            except Back:
                stage = "configure"
                continue
            answers["_audio_transform_finalized"] = True
            return


def step_audio_cut_for_encode(answers: dict[str, Any]) -> None:
    answers.pop("audio_cut_keep_ranges", None)
    allow_gui = not answers.get("_disable_graphical_editors")
    selected = selected_audio_streams(answers) if answers.get("audio_tracks") is not None else [0]
    audio_index = selected[0] if selected else 0
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    while True:
        hint = (
            f"y/n, {graphical_hint('g=Show Graphical Audio Cut Editor')}; applies to selected audio tracks"
            if allow_gui
            else "y/n; terminal range entry applies to selected audio tracks"
        )
        value = appio.ask_raw(appio.question_prompt(answers, "Apply audio waveform cuts?", hint, "n"))
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            return
        if lowered in {"g", "gui", "graphical"}:
            if not allow_gui:
                appio.error("Graphical audio cut editor is not available in Folder Encode.")
                continue
            appio.note("Loading Graphical Audio Cut Editor...")
            sys.stdout.flush()
            ranges = open_audio_cut_gui(answers, audio_index)
            if ranges is None:
                appio.note("Graphical audio cut editor was canceled. Returning to the audio cut question.")
                continue
            keep_ranges = normalize_cut_ranges(ranges, duration)
        elif lowered in {"y", "yes"}:
            try:
                keep_ranges = services.collect_cut_ranges_terminal(answers, 25.0, duration)
            except Back:
                continue
        else:
            appio.error("Enter y, n, or g." if allow_gui else "Enter y or n.")
            continue
        if not keep_ranges:
            appio.note("No audio keep ranges were produced; audio cuts disabled.")
            return
        answers["audio_cut_keep_ranges"] = keep_ranges
        print(paint(format_audio_ranges_for_summary(keep_ranges, "Audio cuts (keep ranges)"), Color.LIME))
        return


def step_audio_transform_start_now(answers: dict[str, Any]) -> None:
    if answers.get("_audio_transform_noop"):
        return
    # The editor step now builds the command and asks "Start FFmpeg now?" itself
    # (so Back from the confirmation returns to the value prompts, not the menu).
    if answers.get("_audio_transform_finalized"):
        return
    # Fallback for any path that did not finalize in the editor.
    _confirm_audio_transform_start(answers)


def step_cuts(answers: dict[str, Any]) -> None:
    """Optional wizard step: ask the user whether to define cuts before
    re-encoding. Stores answers['cut_keep_ranges'] when active."""
    answers.pop("cut_keep_ranges", None)
    fps = services.get_video_fps(answers)
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    if answers.get("_unified_video_editor_used"):
        keep_ranges = normalize_cut_ranges(list(answers.get("_unified_cut_keep_ranges") or []), duration)
        if keep_ranges and not (len(keep_ranges) == 1 and keep_ranges[0][0] <= 1e-6 and keep_ranges[0][1] >= duration - 1e-6):
            answers["cut_keep_ranges"] = keep_ranges
            print(paint(
                format_cut_ranges_for_summary(keep_ranges, fps, "Cuts (keep ranges)"),
                Color.LIME,
            ))
        return
    while True:
        hint_text = "y/n; cuts are applied frame-accurate via filter_complex"
        value = appio.ask_raw(
            appio.question_prompt(
                answers,
                "Apply cuts before encoding?",
                hint_text,
                "n",
            )
        )
        if is_back_value(value):
            raise Back()
        if not value:
            value = "n"
        lowered = value.lower()
        if lowered in {"n", "no"}:
            return
        if lowered in {"y", "yes"}:
            try:
                keep_ranges = services.collect_cut_ranges_terminal(answers, fps, duration)
            except Back:
                continue
        elif lowered in {"g", "gui", "preview"}:
            appio.error("The standalone Cut GUI is archived. Use the Unified Video Editor or enter cuts manually.")
            continue
        else:
            appio.error("Enter y or n.")
            continue
        if not keep_ranges:
            appio.note("No keep ranges were produced; cuts disabled.")
            return
        answers["cut_keep_ranges"] = keep_ranges
        print(paint(
            format_cut_ranges_for_summary(keep_ranges, fps, "Cuts (keep ranges)"),
            Color.LIME,
        ))
        return


def step_start_folder_now(answers: dict[str, Any]) -> None:
    cmd = build_ffmpeg_command(answers)
    answers["cmd"] = cmd
    print_summary(answers, cmd)
    answers["start_now"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Start folder encode now?", "y/n", "y"),
        True,
    )


def build_hardsub_video_filter(answers: dict[str, Any], video_encoder: str) -> str:
    subtitle_filter = hardsub_subtitle_filter(answers)
    handling = answers.get("hardsub_hdr_handling", "standard")
    filters: list[str] = []
    if handling == "tone-map":
        filters.extend([
            "zscale=t=linear:npl=100",
            "format=gbrpf32le",
            "zscale=p=bt709:t=bt709:m=bt709:r=tv",
            "tonemap=hable:desat=0",
            "format=yuv420p",
            subtitle_filter,
            "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        ])
    else:
        filters.append(subtitle_filter)
        filters.append(
            "format=" + (cuda_pixel_format_for_output(answers) if video_encoder.endswith("_nvenc") else cpu_pixel_format_for_output(answers))
        )
        source_range = str((answers.get("video_streams") or [{}])[0].get("color_range") or "").lower()
        if source_range in {"tv", "pc"}:
            filters.append(f"setparams=range={source_range}")
    # SAR handling: HardSub burns subtitles without resizing, so a blanket
    # setsar=1 would destroy a valid non-square source SAR. Preserve a resolved
    # non-square SAR explicitly; only assert square pixels for square or
    # fallback-assumed sources.
    geo = sar_dar_info(answers)
    resolved_sar = geo.get("resolved_sar")
    if (resolved_sar is not None and not geo.get("fallback_used")
            and abs(resolved_sar - 1.0) >= SAR_DAR_TOLERANCE):
        pair = ratio_to_pair(resolved_sar)
        if pair:
            filters.append(f"setsar={pair[0]}/{pair[1]}")
            log_info(
                "HardSub: preserving resolved non-square SAR %d/%d (%s); no square reset forced."
                % (pair[0], pair[1], geo.get("sar_source"))
            )
        elif FORCE_SAR:
            filters.append(f"setsar={FORCE_SAR}")
    elif FORCE_SAR:
        filters.append(f"setsar={FORCE_SAR}")
    return ",".join(filters)


def build_hardsub_command(answers: dict[str, Any]) -> list[str]:
    answers["_hardsub_mode"] = True
    ffmpeg = answers["ffmpeg"]
    input_path: Path = answers["input_path"]
    input_ext, output_ext = hardsub_input_output_exts(answers)
    output_path = choose_hardsub_output_path(
        input_path,
        output_ext,
        answers["output_location"],
        answers.get("output_name_stem"),
    )
    answers["output_path"] = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    video_encoder, tag, _profile = resolve_video_encoder(answers)
    if video_encoder == "copy":
        video_encoder = "libx265"
    video_encoder, tag, _profile = enforce_bit_depth_compatible_video_encoder(answers, video_encoder, tag, _profile)
    cmd: list[str] = [ffmpeg, "-y" if OVERWRITE_OUTPUT else "-n", "-i", str(input_path)]

    cmd.extend(["-map", "0:v:0"])
    audio_mode = answers.get("hardsub_audio_mode", "copy-all")
    audio_policy = answers.get("hardsub_audio_container_policy")
    mapped_audio_output_count = 0
    if audio_mode != "none" and input_ext != output_ext and not audio_policy:
        raise ValueError("HardSub audio container policy is required when output container differs from the source container.")
    if audio_policy == "match-source-container":
        audio_policy = "copy-anyway"
    if input_ext == output_ext and not audio_policy:
        audio_policy = "copy-anyway"
    if audio_mode == "none" or audio_policy == "none":
        cmd.append("-an")
    elif audio_mode == "selected":
        selected_hardsub_audio = list(answers.get("hardsub_audio_tracks", []))
        for index in selected_hardsub_audio:
            cmd.extend(["-map", f"0:a:{index}"])
        mapped_audio_output_count = len(selected_hardsub_audio)
        if audio_policy == "aac":
            bitrate = int(answers.get("hardsub_audio_bitrate_kbps") or DEFAULT_AUDIO_BITRATE_KBPS)
            cmd.extend(["-c:a", "aac", "-b:a", f"{bitrate}k", "-ac", "2"])
        else:
            cmd.extend(["-c:a", "copy"])
    else:
        cmd.extend(["-map", "0:a?"])
        mapped_audio_output_count = len(answers.get("audio_streams") or [])
        if audio_policy == "aac":
            bitrate = int(answers.get("hardsub_audio_bitrate_kbps") or DEFAULT_AUDIO_BITRATE_KBPS)
            cmd.extend(["-c:a", "aac", "-b:a", f"{bitrate}k", "-ac", "2"])
        else:
            cmd.extend(["-c:a", "copy"])

    cmd.extend(["-sn", "-dn", "-map_metadata", "0", "-map_chapters", "0"])
    log_info("Hard Sub Encode uses the CPU subtitles/libass filter chain; NVENC may still be used for video encode.")
    cmd.extend(["-filter:v", build_hardsub_video_filter(answers, video_encoder)])
    cmd.extend(["-c:v", video_encoder])
    append_hardsub_quality_args(cmd, answers, video_encoder)
    append_nvenc_multipass_args(cmd, answers, video_encoder)
    append_hardsub_color_args(cmd, answers)
    if video_encoder == "hevc_nvenc":
        cmd.extend(["-profile:v", hevc_profile_for_output(answers, "main")])
    elif video_encoder == "libx265":
        cmd.extend(["-profile:v", hevc_profile_for_output(answers, "main")])
    append_clear_reencoded_stream_stat_metadata(
        cmd,
        answers,
        video_output_count=1,
        audio_output_count=mapped_audio_output_count,
        subtitle_output_count=0,
    )
    if tag and output_ext in MP4_LIKE_EXTS:
        cmd.extend(["-tag:v", tag])
    if output_ext in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])
    cmd.append(str(output_path))
    return cmd


def step_hardsub_start_now(answers: dict[str, Any]) -> None:
    ensure_color_range_resolved(answers, workflow="HardSub")
    log_and_warn_pixel_format(answers)
    cmd = build_hardsub_command(answers)
    answers["cmd"] = cmd
    print()
    print(paint("Hard Sub Encode summary:", Color.BOLD + Color.LIME))
    print("  " + field_text("input", answers["input_path"], Color.WHITE))
    if answers.get("hardsub_subtitle_source") == "internal":
        print("  " + field_text("subtitle", f"internal subtitle #{answers.get('hardsub_subtitle_index')}", Color.MAGENTA))
    else:
        print("  " + field_text("subtitle", answers.get("hardsub_subtitle_path"), Color.MAGENTA))
    print("  " + field_text("output", answers["output_path"], Color.LIME))
    print("  " + field_text("video codec", answers.get("video_codec"), Color.CYAN))
    if "_nvenc" in command_to_text(cmd):
        print("  " + field_text("NVENC multipass", normalize_nvenc_multipass_mode(answers.get("nvenc_multipass")), Color.YELLOW))
    print("  " + field_text("quality", answers.get("hardsub_quality_mode"), Color.YELLOW))
    print("  " + field_text("HDR/Dolby handling", answers.get("hardsub_hdr_handling"), Color.ORANGE))
    print("  " + field_text("audio", answers.get("hardsub_audio_mode"), Color.BLUE))
    print("  " + field_text("audio container policy", answers.get("hardsub_audio_container_policy", "copy-anyway"), Color.CYAN))
    print()
    print(paint("Final PowerShell command:", Color.FINAL_COMMAND_LABEL))
    log_info("Final PowerShell command: " + command_to_powershell(cmd))
    print(paint(command_to_powershell(cmd), Color.FINAL_COMMAND_TEXT))
    answers["start_now"] = appio.ask_yes_no(
        appio.question_prompt(answers, "Start FFmpeg now?", "y/n", "y"),
        True,
    )


def build_cut_filter_complex(
    answers: dict[str, Any],
    keep_ranges: list[tuple[float, float]],
    audio_for_cut: int | None,
) -> str:
    """Build the -filter_complex argument for the wizard re-encode cut path."""
    if not keep_ranges:
        raise ValueError("build_cut_filter_complex requires at least one keep range.")
    fc_parts: list[str] = []
    video_sources: list[str]
    audio_sources: list[str] = []
    if len(keep_ranges) > 1:
        video_sources = [f"vsrc{idx}" for idx in range(len(keep_ranges))]
        fc_parts.append(f"[0:v:0]split={len(keep_ranges)}{''.join(f'[{label}]' for label in video_sources)}")
        log_info(f"Inserted split={len(keep_ranges)} for multi-range video trim from [0:v:0].")
        if audio_for_cut is not None:
            audio_sources = [f"asrc{idx}" for idx in range(len(keep_ranges))]
            fc_parts.append(
                f"[0:a:{audio_for_cut}]asplit={len(keep_ranges)}"
                f"{''.join(f'[{label}]' for label in audio_sources)}"
            )
            log_info(f"Inserted asplit={len(keep_ranges)} for multi-range audio trim from [0:a:{audio_for_cut}].")
    else:
        video_sources = ["0:v:0"]
        if audio_for_cut is not None:
            audio_sources = [f"0:a:{audio_for_cut}"]
    for idx, (start, end) in enumerate(keep_ranges):
        fc_parts.append(
            f"[{video_sources[idx]}]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[v{idx}]"
        )
        if audio_for_cut is not None:
            fc_parts.append(
                f"[{audio_sources[idx]}]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS[a{idx}]"
            )

    if len(keep_ranges) > 1:
        concat_inputs = ""
        for idx in range(len(keep_ranges)):
            concat_inputs += f"[v{idx}]"
            if audio_for_cut is not None:
                concat_inputs += f"[a{idx}]"
        if audio_for_cut is not None:
            if audio_speed_transform_enabled(answers) or loudnorm_transform_enabled(answers):
                fc_parts.append(f"{concat_inputs}concat=n={len(keep_ranges)}:v=1:a=1[vc][ac]")
                fc_parts.append(f"[ac]{build_encode_audio_speed_filter(answers)}[a]")
            else:
                fc_parts.append(f"{concat_inputs}concat=n={len(keep_ranges)}:v=1:a=1[vc][a]")
        else:
            fc_parts.append(f"{concat_inputs}concat=n={len(keep_ranges)}:v=1:a=0[vc]")
        video_label = "vc"
    else:
        video_label = "v0"
        if audio_for_cut is not None:
            if audio_speed_transform_enabled(answers) or loudnorm_transform_enabled(answers):
                fc_parts.append(f"[a0]{build_encode_audio_speed_filter(answers)}[a]")
            else:
                fc_parts.append("[a0]asetpts=PTS-STARTPTS[a]")

    # Apply the user's video filters (crop/fps/scale/setsar/setparams) after concat.
    user_video_filter = build_cpu_video_filter(answers)
    if user_video_filter:
        fc_parts.append(f"[{video_label}]{user_video_filter}[v]")
    else:
        fc_parts.append(f"[{video_label}]null[v]")
    return ";".join(fc_parts)


def build_join_encode_command(answers: dict[str, Any], items: list[dict[str, Any]], output_path: Path) -> list[str]:
    output_path = resolve_output_collision_against_inputs(
        output_path,
        [Path(item["path"]) for item in items if item.get("path")],
        answers.get("output_collision_suffix", "_Encode"),
    )
    answers["output_path"] = output_path
    join_answers = dict(answers)
    join_answers["_join_complex_graph"] = True
    video_encoder, tag, profile = resolve_video_encoder(join_answers)
    if video_encoder == "copy":
        join_answers["video_codec"] = DEFAULT_VIDEO_CODEC
        video_encoder, tag, profile = resolve_video_encoder(join_answers)
    video_encoder, tag, profile = enforce_bit_depth_compatible_video_encoder(join_answers, video_encoder, tag, profile)
    first_video = items[0]["video_streams"][0]
    join_answers["video_streams"] = [first_video]
    target_dimensions = resolve_scale_dimensions(join_answers, join_answers.get("resolution", "n"))
    if target_dimensions:
        target_w, target_h = target_dimensions
    else:
        target_w = int(first_video.get("width") or 1280)
        target_h = int(first_video.get("height") or 720)
        join_answers["final_resolution"] = (target_w, target_h)
    target_fps = float(join_answers.get("fps") or rational_to_float(first_video.get("avg_frame_rate")) or rational_to_float(first_video.get("r_frame_rate")) or 30.0)

    cmd: list[str] = [join_answers["ffmpeg"], "-hide_banner", "-y" if OVERWRITE_OUTPUT else "-n"]
    use_cuda_decode_complex = should_use_cuda_decode_for_complex_graph(join_answers, video_encoder, False)
    for item in items:
        if use_cuda_decode_complex:
            append_cuda_decode_args_for_input(cmd, join_answers)
        cmd.extend(["-i", str(item["path"])])

    selected_audio = selected_audio_streams(join_answers) if join_answers.get("audio_streams") else []
    if selected_audio:
        # Reject the join with a clear message listing EVERY input that lacks a
        # selected audio track, instead of letting an unmapped [n:a:idx] label
        # fail deep inside FFmpeg. (Missing-audio behavior is explicit + tested.)
        missing_files: list[str] = []
        for item_pos, item in enumerate(items, start=1):
            audio_count = len(item.get("audio_streams") or [])
            missing = [idx for idx in selected_audio if idx >= audio_count]
            if missing:
                missing_files.append(
                    f"input {item_pos} ({Path(item.get('path')).name}): has {audio_count} audio track(s), "
                    f"missing selected track(s) {missing}"
                )
        if missing_files:
            raise RuntimeError(
                "Join cannot map the selected audio track(s) because some inputs lack them:\n  "
                + "\n  ".join(missing_files)
                + "\nRe-run and select only audio tracks present in every joined input, "
                "or remove the inputs without that audio."
            )

    filters: list[str] = []
    concat_inputs: list[str] = []
    top = int(join_answers.get("crop_top", 0) or 0)
    left = int(join_answers.get("crop_left", 0) or 0)
    right = int(join_answers.get("crop_right", 0) or 0)
    bottom = int(join_answers.get("crop_bottom", 0) or 0)
    if join_answers.get("crop_enabled") and any((top, left, right, bottom)):
        # Normalize to the source chroma grid / output encoder grid so the join
        # crop matches the single-input crop paths and adds no black padding.
        n_left, n_right, n_top, n_bottom = normalized_crop_margins(join_answers)
        crop_filter = f"crop=iw-{n_left}-{n_right}:ih-{n_top}-{n_bottom}:{n_left}:{n_top}:exact=1"
    else:
        crop_filter = ""
    # Final 'format=' for the CPU concat filter graph, chosen for the resolved
    # encoder: p010le (10-bit) / yuv420p (8-bit) for NVENC, yuv420p10le /
    # yuv420p for libx26x. This avoids feeding yuv420p10le to hevc_nvenc.
    output_pix_fmt = cpu_graph_pixel_format_for_encoder(join_answers, video_encoder)

    # VFR join re-encode: omit the per-input fps= filter (which forces CFR) and
    # keep variable timing on the output via -fps_mode vfr (added per output).
    vfr_join = bool(join_answers.get("join_vfr"))
    for input_idx, _item in enumerate(items):
        chain = []
        if crop_filter:
            chain.append(crop_filter)
        chain.extend([
            "" if vfr_join else f"fps={target_fps:g}",
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:reset_sar=1",
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2",
            output_pix_fmt and f"format={output_pix_fmt}",
            "setpts=PTS-STARTPTS",
        ])
        chain = [part for part in chain if part]
        filters.append(f"[{input_idx}:v:0]{','.join(chain)}[jv{input_idx}]")
        concat_inputs.append(f"[jv{input_idx}]")
        for audio_pos, audio_index in enumerate(selected_audio):
            filters.append(
                f"[{input_idx}:a:{audio_index}]"
                f"{join_audio_prep_filter(join_target_sample_rate(join_answers))}[ja{input_idx}_{audio_pos}]"
            )
            concat_inputs.append(f"[ja{input_idx}_{audio_pos}]")

    concat_outputs = ["[jvcat]"] + [f"[jacat{pos}]" for pos, _idx in enumerate(selected_audio)]
    filters.append(
        f"{''.join(concat_inputs)}concat=n={len(items)}:v=1:a={len(selected_audio)}{''.join(concat_outputs)}"
    )

    source_join_duration = sum(float(item.get("duration") or 0.0) for item in items)
    keep_ranges = normalize_cut_ranges(list(join_answers.get("cut_keep_ranges") or []), source_join_duration)
    video_label = append_join_trim_concat_filter(filters, "jvcat", keep_ranges, "video", "jvcut")
    if video_speed_transform_enabled(join_answers):
        filters.append(
            f"[{video_label}]{build_video_speed_filter(encode_video_speed_factor(join_answers), bool(join_answers.get('reverse_video')))}[jvfinal]"
        )
    else:
        filters.append(f"[{video_label}]setpts=PTS-STARTPTS[jvfinal]")
    video_label = "jvfinal"

    audio_labels: list[str] = []
    for audio_pos, _audio_index in enumerate(selected_audio):
        label = append_join_trim_concat_filter(filters, f"jacat{audio_pos}", keep_ranges, "audio", f"jacut{audio_pos}")
        final_audio_label = f"jafinal{audio_pos}"
        if audio_speed_transform_enabled(join_answers) or loudnorm_transform_enabled(join_answers):
            filters.append(f"[{label}]{build_encode_audio_speed_filter(join_answers)}[{final_audio_label}]")
        else:
            filters.append(f"[{label}]asetpts=PTS-STARTPTS[{final_audio_label}]")
        audio_labels.append(final_audio_label)

    final_duration = final_processed_duration_for_splits(join_answers, source_join_duration)
    split_points = normalize_separator_points(join_answers.get("separator_points"), final_duration)
    split_active = bool(split_points)
    if split_active:
        video_outputs, audio_outputs_by_part, split_intervals = append_final_split_filters(
            filters,
            video_label,
            audio_labels,
            split_points,
            final_duration,
            "j",
            float(join_answers.get("fps") or 0.0),
        )
        output_paths = split_part_output_paths(output_path, len(video_outputs), [Path(item["path"]) for item in items])
        join_answers["split_output_paths"] = output_paths
        join_answers["split_part_intervals"] = split_intervals
        answers["split_output_paths"] = output_paths
        answers["split_part_intervals"] = split_intervals
        join_answers["output_path"] = output_paths[0]
        answers["output_path"] = output_paths[0]
    else:
        video_outputs = [video_label]
        audio_outputs_by_part = [[label for label in audio_labels]]
        split_intervals = []
        output_paths = [output_path]

    cmd.extend(["-filter_complex", ";".join(filters)])

    if join_answers.get("use_gpu") and str(video_encoder).endswith("_nvenc"):
        log_info("Join Videos uses CPU concat filters; NVENC is still used for final video encoding.")
    for part_idx, part_output in enumerate(output_paths):
        cmd.extend(["-map", f"[{video_outputs[part_idx]}]"])
        for audio_label in audio_outputs_by_part[part_idx]:
            cmd.extend(["-map", f"[{audio_label}]"])
        attachments_mapped = append_embedded_attachment_maps(cmd, join_answers)
        data_mapped = append_source_data_maps(cmd, join_answers)
        append_source_metadata_chapter_options(cmd, join_answers)
        append_negative_stream_options(cmd, join_answers, True, [], data_mapped)
        append_video_encode_options(cmd, join_answers, video_encoder, tag, profile)
        if vfr_join and video_encoder != "copy":
            # Preserve variable timing across the joined segments instead of
            # resampling every frame to a single constant rate.
            cmd.extend(["-fps_mode", "vfr"])
        append_audio_encode_options(cmd, join_answers, bool(audio_outputs_by_part[part_idx]))
        append_clear_reencoded_stream_stat_metadata(
            cmd,
            join_answers,
            video_output_count=1 if video_encoder != "copy" else 0,
            audio_output_count=len(audio_outputs_by_part[part_idx]) if audio_outputs_by_part[part_idx] else 0,
            subtitle_output_count=0,
        )
        if attachments_mapped:
            append_embedded_attachment_codec_options(cmd, join_answers)
        if data_mapped:
            append_source_data_codec_options(cmd, join_answers)
        append_container_options(cmd, join_answers["output_ext"])
        cmd.append(str(part_output))
    if split_active:
        log_info(
            "Split final joined output into parts: "
            + ", ".join(
                f"Part {idx + 1:02d} {seconds_to_ffmpeg_time(start)}->{seconds_to_ffmpeg_time(end)}"
                for idx, (start, end) in enumerate(split_intervals)
            )
        )
    answers["final_resolution"] = join_answers.get("final_resolution")
    answers.pop("_join_complex_graph", None)
    return cmd


__all__ = [
    'append_nvenc_multipass_args',
    'append_single_input_split_outputs',
    'append_video_encode_options',
    'ask_join_add_another',
    'ask_nvenc_multipass_if_applicable',
    'build_cpu_video_filter',
    'build_cut_filter_complex',
    'build_ffmpeg_command',
    'build_hardsub_command',
    'build_hardsub_video_filter',
    'build_join_encode_command',
    'build_video_filter',
    'compare_pixel_formats',
    'encoder_preserves_unspecified_range',
    'encoder_supports_multipass',
    'expected_unforced_range',
    'graphical_hint',
    'is_nvenc_multipass_encoder',
    'log_and_warn_pixel_format',
    'nvenc_multipass_applicable_for_encoder',
    'nvenc_multipass_prompt_applicable',
    'open_audio_cut_gui',
    'open_audio_speed_gui',
    'pixel_format_analysis',
    'print_summary',
    'run_wizard',
    'step_audio_cut_editor',
    'step_audio_cut_for_encode',
    'step_audio_speed_reverse_for_encode',
    'step_audio_speed_reverse_options',
    'step_audio_transform_editor',
    'step_audio_transform_start_now',
    'step_color_range',
    'step_cpu_two_pass',
    'step_crop_enabled',
    'step_cuts',
    'step_fps',
    'step_hardsub_start_now',
    'step_input_path',
    'step_is_auto_back_skip',
    'step_join_additional_inputs_for_encode',
    'step_nvenc_multipass',
    'step_output_format',
    'step_output_location',
    'step_resolution',
    'step_start_folder_now',
    'step_start_now',
    'step_unified_video_editor_for_encode',
    'step_use_gpu',
    'step_video_bitrate',
    'step_video_codec',
    'step_video_speed_reverse_for_encode',
    'step_video_speed_reverse_options',
    '_confirm_audio_transform_start',
    '_run_gui_audio_transform',
    'Step',
    '_CHROMA_RANK',
    '_MULTIPASS_ENCODER_CACHE',
    '_MULTIPASS_FALLBACK_ENCODERS',
    '_MULTIPASS_OPTION_RE',
    '_UNSPECIFIED_RANGE_PRESERVING_ENCODERS',
]
