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


__all__ = [
    'append_nvenc_multipass_args',
    'append_video_encode_options',
    'ask_nvenc_multipass_if_applicable',
    'compare_pixel_formats',
    'encoder_preserves_unspecified_range',
    'encoder_supports_multipass',
    'expected_unforced_range',
    'is_nvenc_multipass_encoder',
    'log_and_warn_pixel_format',
    'nvenc_multipass_applicable_for_encoder',
    'nvenc_multipass_prompt_applicable',
    'open_audio_cut_gui',
    'open_audio_speed_gui',
    'pixel_format_analysis',
    'step_audio_cut_editor',
    'step_audio_cut_for_encode',
    'step_audio_transform_editor',
    'step_audio_transform_start_now',
    'step_color_range',
    'step_is_auto_back_skip',
    'step_nvenc_multipass',
    'step_start_now',
    '_confirm_audio_transform_start',
    '_run_gui_audio_transform',
    'Step',
    '_CHROMA_RANK',
    '_MULTIPASS_ENCODER_CACHE',
    '_MULTIPASS_FALLBACK_ENCODERS',
    '_MULTIPASS_OPTION_RE',
    '_UNSPECIFIED_RANGE_PRESERVING_ENCODERS',
]


# wizard was split for file size into cohesive sibling modules:
#   wizard_build - FFmpeg command builders
#   wizard_flow  - run_wizard, print_summary and the speed/reverse step flow
#   wizard_steps - the per-field wizard step_* prompts
# Re-export them here (in dependency order) so every `from ffmwiz.wizard import *`
# consumer and wizard's own runtime calls keep seeing the full set. Each sibling
# imports wizard at its top; these imports run after wizard's own defs/__all__,
# so the cycle resolves cleanly.
from ffmwiz import wizard_build as _wizard_build  # noqa: E402
from ffmwiz.wizard_build import *  # noqa: E402,F401,F403
from ffmwiz import wizard_steps as _wizard_steps  # noqa: E402
from ffmwiz.wizard_steps import *  # noqa: E402,F401,F403
from ffmwiz import wizard_flow as _wizard_flow  # noqa: E402
from ffmwiz.wizard_flow import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_wizard_build.__all__) + list(_wizard_steps.__all__) + list(_wizard_flow.__all__)

