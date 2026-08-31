"""FFmWiz helpers (dependency level 1) — concerns: filters(8).

Extracted verbatim from FFmWiz.py; imports ffmwiz.core.* and lower levels.
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


# token -> (answer key, value). The three levels each filter offers are spelled
# out rather than parsed, so an unknown level is rejected instead of silently
# becoming "off".
_FLAGS: dict[str, tuple[str, Any]] = {
    "hflip": ("flip_horizontal", True),
    "vflip": ("flip_vertical", True),
    "gray": ("adjust_grayscale", True),
    "grey": ("adjust_grayscale", True),
}
for _name in ROTATE_FILTERS:
    _FLAGS[_name] = ("rotate_choice", _name)
for _key, _table in (("denoise_level", DENOISE_FILTERS),
                     ("sharpen_level", SHARPEN_FILTERS),
                     ("blur_level", BLUR_FILTERS)):
    _short = _key.split("_")[0]
    for _level in _table:
        _FLAGS[f"{_short}={_level}"] = (_key, _level)
    # Bare `denoise` means the middle setting, which is what someone typing it
    # without a level wants.
    _FLAGS[_short] = (_key, "medium" if "medium" in _table else next(iter(_table)))

# token -> answer key, for the ones that carry a number.
_NUMBERS: dict[str, str] = {
    "fadein": "fade_in_seconds",
    "fadeout": "fade_out_seconds",
    "bright": "adjust_brightness",
    "brightness": "adjust_brightness",
    "contrast": "adjust_contrast",
    "sat": "adjust_saturation",
    "saturation": "adjust_saturation",
}


def parse_look_tokens(text: str) -> dict[str, Any]:
    """Turn `90cw,gray,fadein=1.5` into answer keys. Raises ValueError.

    Separated from the prompt so it can be tested without a terminal, and so
    the GUI can reuse the same spelling.
    """
    chosen: dict[str, Any] = {}
    for raw in text.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token in _FLAGS:
            key, value = _FLAGS[token]
            chosen[key] = value
            continue
        name, _, amount = token.partition("=")
        if name in _NUMBERS and amount:
            try:
                number = float(amount)
            except ValueError:
                raise ValueError(f"{token!r}: {amount!r} is not a number")
            if number < 0:
                raise ValueError(f"{token!r}: negative amounts are not allowed")
            chosen[_NUMBERS[name]] = number
            continue
        raise ValueError(f"{token!r} is not one of: " + ", ".join(sorted(_FLAGS)))
    if chosen.get("sharpen_level") and chosen.get("blur_level"):
        raise ValueError("sharpen and blur cancel each other; pick one")
    return chosen


def describe_look(answers: dict[str, Any]) -> str:
    """A short line naming what was chosen, for the summary and the prompt."""
    parts = []
    if answers.get("rotate_choice") not in (None, "none"):
        parts.append(f"rotate {answers['rotate_choice']}")
    for key, label in (("flip_horizontal", "hflip"), ("flip_vertical", "vflip"),
                       ("adjust_grayscale", "grayscale")):
        if answers.get(key):
            parts.append(label)
    for key in ("denoise_level", "sharpen_level", "blur_level"):
        value = answers.get(key)
        if value and value != "off":
            parts.append(f"{key.split('_')[0]} {value}")
    for key, label in (("adjust_brightness", "brightness"),
                       ("adjust_contrast", "contrast"),
                       ("adjust_saturation", "saturation")):
        if key in answers:
            parts.append(f"{label} {answers[key]:g}")
    for key, label in (("fade_in_seconds", "fade in"),
                       ("fade_out_seconds", "fade out")):
        if answers.get(key):
            parts.append(f"{label} {answers[key]:g}s")
    return ", ".join(parts) or "none"


def fade_filter_parts(prefix: str, output_seconds: float,
                      fade_in: float, fade_out: float) -> list[str]:
    """Fade filters measured on the OUTPUT timeline.

    `prefix` is "" for the picture (`fade`) and "a" for the sound (`afade`).
    One function rather than two so the two chains cannot drift: a picture
    that fades to black over a second while the sound stays at full volume is
    the bug this shape prevents.

    Both fades belong LAST in their chain, after the speed change and the
    reversal, because "one second" means one second of the file the user gets.
    A fade-out also has to know where that file ends, so an unknown or too
    short duration drops it with a warning instead of emitting a negative
    start time, which FFmpeg rejects outright.
    """
    parts: list[str] = []
    if fade_in > 0:
        parts.append(f"{prefix}fade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0:
        if output_seconds > fade_out:
            parts.append(f"{prefix}fade=t=out:"
                         f"st={output_seconds - fade_out:.3f}:d={fade_out:.3f}")
        else:
            from ffmwiz.appio import log_warn  # higher tier: deferred to avoid a cycle
            log_warn(
                f"Fade out of {fade_out:.3f}s was dropped: the output is "
                f"{output_seconds:.3f}s, so there is nothing to fade from.")
    return parts


def requested_fade_seconds(answers: dict[str, Any]) -> tuple[float, float]:
    """(in, out) from the answers, or (0, 0) when they are not numbers."""
    try:
        return (max(0.0, float(answers.get("fade_in_seconds") or 0.0)),
                max(0.0, float(answers.get("fade_out_seconds") or 0.0)))
    except (TypeError, ValueError):
        return 0.0, 0.0


def requested_volume_gain(answers: dict[str, Any]) -> bool:
    """Did the user ask for a volume change?

    The single definition of that question. `build_volume_filter` clamps and
    formats the gain; `audio_transform_enabled` decides whether the audio
    filter chain is built at all; `reverse_stages` decides which stage owns
    it. All three have to agree on what counts, or the gate closes on a
    request the chain behind it would have honoured -- which is exactly how a
    volume-only job lost its gain.
    """
    try:
        factor = float(answers.get("audio_volume") or 1.0)
    except (TypeError, ValueError):
        return False
    return abs(factor - 1.0) > 1e-9


def atempo_filter_chain(speed: float) -> str:
    """Build an atempo chain with each stage kept in FFmpeg's safe 0.5..2.0
    range. This avoids the artifacts/skipped-sample behavior of very large
    single atempo values."""
    remaining = clamp_speed_factor(speed)
    stages: list[float] = []
    while remaining > 2.0 + 1e-9:
        stages.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        stages.append(0.5)
        remaining /= 0.5
    stages.append(remaining)
    return ",".join(f"atempo={ffmpeg_float(stage)}" for stage in stages)


def build_video_speed_filter(speed: float, reverse: bool) -> str:
    speed = clamp_speed_factor(speed)
    filters: list[str] = []
    if reverse:
        filters.append("reverse")
    filters.append(f"setpts=(PTS-STARTPTS)/{ffmpeg_float(speed)}")
    return ",".join(filters)


# The output options a retimed video needs, and the reason the filter alone is
# not enough. `setpts` moves frames without changing how many there are, but the
# output frame rate stays whatever FFmpeg guessed from the SOURCE, so speeding up
# hands the encoder more frames per second than that rate carries and it silently
# drops the excess. Measured on a 10 fps, 40-frame source:
#
#     2.0x   40 frames -> 22   white band (0.20, 0.60) instead of (0.10, 0.40)
#     1.5x   40 frames -> 28   white band (0.20, 0.70) instead of (0.13, 0.53)
#
# So nearly half the picture was thrown away and what survived no longer sat on
# its own subtitles. `-fps_mode vfr` does NOT help -- it still drops against the
# guessed rate (22 frames, identical bands) -- and `-r` cannot be combined with a
# non-CFR mode at all ("One of -r/-fpsmax was specified together a non-CFR
# -vsync/-fps_mode. This is contradictory."). Passthrough keeps the graph's own
# timing, which is the only source of truth once `setpts` has run.
#
# It needs no speed threshold: at 1.0x and at 0.5x the output is byte-identical
# to not passing it, because nothing is arriving faster than the guessed rate.
VIDEO_SPEED_OUTPUT_TIMING_ARGS: tuple[str, ...] = ("-fps_mode", "passthrough")


def loudnorm_analysis_filter(target_i: float) -> str:
    """Pass-1 measurement filter: same target, JSON output, no media encode."""
    return (
        "loudnorm="
        f"I={loudnorm_number(target_i)}:"
        f"TP={loudnorm_number(LOUDNORM_TARGET_TP)}:"
        f"LRA={loudnorm_number(LOUDNORM_TARGET_LRA)}:"
        "print_format=json"
    )


def source_sar(answers: dict[str, Any]) -> float:
    """Return the source video sample aspect ratio as a float (>0). Default 1.0."""
    stream = answers.get("video_streams", [{}])[0] if answers.get("video_streams") else {}
    sar_str = stream.get("sample_aspect_ratio")
    return parse_sar_value(sar_str)


def crop_margins_validation_message(
    answers: dict[str, Any],
    top: int,
    left: int,
    right: int,
    bottom: int,
) -> str | None:
    # Every joined input receives the SAME crop string, so the limit is the
    # smallest input, not the first one.
    source_w, source_h = smallest_video_size(answers)
    if min(top, left, right, bottom) < 0:
        return "Invalid crop margins: crop values must be zero or positive."
    if left + right >= source_w:
        return (
            f"Invalid crop margins: left + right ({left + right} px) must be "
            f"smaller than the smallest input width ({source_w} px)."
        )
    if top + bottom >= source_h:
        return (
            f"Invalid crop margins: top + bottom ({top + bottom} px) must be "
            f"smaller than the smallest input height ({source_h} px)."
        )
    return None


def closest_edge_scale_dimensions(
    crop_w: int,
    crop_h: int,
    target_w: int,
    target_h: int,
) -> tuple[int, int, str]:
    source_landscape = crop_w >= crop_h
    target_landscape = target_w >= target_h
    if source_landscape != target_landscape:
        target_w, target_h = target_h, target_w
    width_distance = abs(crop_w - target_w)
    height_distance = abs(crop_h - target_h)
    if width_distance <= height_distance:
        final_width = even_dimension(target_w)
        final_height = even_dimension(final_width * crop_h / max(1, crop_w))
        return final_width, final_height, "width"
    final_height = even_dimension(target_h)
    final_width = even_dimension(final_height * crop_w / max(1, crop_h))
    return final_width, final_height, "height"


def look_filters_requested(answers: dict[str, Any]) -> bool:
    """Did the user ask for a rotate/flip, a colour adjustment, or de/sharpen/blur?

    The single definition of that question, tested against the same tables
    `build_orientation_filters` and `build_look_filters` read, so the gate and
    the chain cannot disagree about what counts.
    """
    if str(answers.get("rotate_choice") or "none").strip().lower() in ROTATE_FILTERS:
        return True
    if answers.get("flip_horizontal") or answers.get("flip_vertical"):
        return True
    if answers.get("adjust_grayscale"):
        return True
    for key, table in (("denoise_level", DENOISE_FILTERS),
                       ("sharpen_level", SHARPEN_FILTERS),
                       ("blur_level", BLUR_FILTERS)):
        if str(answers.get(key) or "off").strip().lower() in table:
            return True
    for key, (_low, _high, neutral) in ADJUST_RANGES.items():
        try:
            if float(answers.get(key, neutral)) != neutral:
                return True
        except (TypeError, ValueError):
            continue
    return False


def video_filters_required(answers: dict[str, Any]) -> bool:
    """Whether the picture chain has to be built at all -- the video twin of
    `audio_transform_enabled`, and it must cover everything that chain emits.

    It used to ask only about crop, fps, resize, cuts, split points and speed,
    while `build_cpu_video_filter` behind it also emits the orientation, the
    look and the fades. So a `-c:v copy` job with a rotation, a flip, a
    grayscale, a denoise/sharpen/blur or a fade never tripped the "copy cannot
    be used with filters" fallback: the command came out `-c:v copy` with no
    `-vf` at all and the edit was silently dropped. A gate narrower than its
    own body drops the request silently.
    """
    return bool(
        answers.get("crop_enabled")
        or answers.get("fps") is not None
        or answers.get("resolution", "n") != "n"
        or answers.get("cut_keep_ranges")
        or answers.get("separator_points")
        or video_speed_transform_enabled(answers)
        or look_filters_requested(answers)
        or any(requested_fade_seconds(answers))
    )


def hardsub_subtitle_filter(answers: dict[str, Any]) -> str:
    source = answers.get("hardsub_subtitle_source")
    parts: list[str]
    if source == "internal":
        input_path: Path = answers["input_path"]
        subtitle_index = int(answers.get("hardsub_subtitle_index", 0))
        parts = [f"filename={hardsub_filter_quote_path(input_path)}", f"si={subtitle_index}"]
    else:
        subtitle_path: Path = answers["hardsub_subtitle_path"]
        parts = [f"filename={hardsub_filter_quote_path(subtitle_path)}"]
    fontsdir = answers.get("hardsub_fontsdir")
    if fontsdir:
        parts.append(f"fontsdir={hardsub_filter_quote_path(Path(fontsdir))}")
    return "subtitles=" + ":".join(parts)


__all__ = [
    'atempo_filter_chain',
    'fade_filter_parts',
    'parse_look_tokens',
    'describe_look',
    'LOOK_ANSWER_KEYS',
    'requested_fade_seconds',
    'requested_volume_gain',
    'build_video_speed_filter',
    'VIDEO_SPEED_OUTPUT_TIMING_ARGS',
    'loudnorm_analysis_filter',
    'source_sar',
    'crop_margins_validation_message',
    'closest_edge_scale_dimensions',
    'look_filters_requested',
    'video_filters_required',
    'hardsub_subtitle_filter',
]
