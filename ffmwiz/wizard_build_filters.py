"""Video filter chains: look, orientation, fade, the CPU graph, cut and GIF.

Split out of ffmwiz/wizard_build_b.py for file size. This is the LEAF of that
module's internal call graph -- every function here calls only its neighbours in
this file, so nothing reaches back up and the pair cannot become a cycle.

`build_cpu_video_filter` in particular is the one the join builder must NOT call
(it would apply crop/fps/scale/format a second time after the concat); the join
path composes orientation/look/fade itself. Kept together so that stays visible.
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
from ffmwiz.core.artifacts import *  # noqa: F401,F403
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
from ffmwiz.support.L01_subtitles import *  # noqa: F401,F403
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
from ffmwiz.metadata import *  # noqa: F401,F403
from ffmwiz import metadata  # noqa: F401
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.trackmanager import *  # noqa: F401,F403
# The GIF input/scale helpers live in wizard_build_c. wizard_build_b reached
# them through its TAIL import, which this module does not inherit -- name it.
from ffmwiz import wizard_build_c  # noqa: E402,F401


def build_orientation_filters(answers: dict[str, Any]) -> list[str]:
    """Rotation and flips, which change the FRAME the rest of the chain sees.

    They belong straight after the crop and before the frame rate and the
    scale: a 90-degree rotation swaps width and height, so a resize target
    asked for afterwards applies to the rotated picture, which is what the user
    means by it. Putting them later would size the canvas against the source
    orientation and letterbox the result.
    """
    out: list[str] = []
    rotation = str(answers.get("rotate_choice") or "none").strip().lower()
    if rotation in ROTATE_FILTERS:
        out.append(ROTATE_FILTERS[rotation])
    if answers.get("flip_horizontal"):
        out.append("hflip")
    if answers.get("flip_vertical"):
        out.append("vflip")
    return out

def build_look_filters(answers: dict[str, Any]) -> list[str]:
    """Colour, denoise and sharpen/blur, in the order they have to run.

    All three leave the geometry alone, so they sit after the scale and before
    the speed/reverse -- which matters, because `reverse` buffers whatever
    reaches it and the memory budget is computed from the frame at ITS input.
    A filter placed after `reverse` would also be applied to a buffered frame
    for no benefit.

    Denoise before sharpen is deliberate: sharpening first amplifies exactly
    the grain the denoiser is about to remove.
    """
    out: list[str] = []
    settings = []
    for key, (low, high, neutral) in ADJUST_RANGES.items():
        try:
            value = float(answers.get(key, neutral))
        except (TypeError, ValueError):
            continue
        if value != neutral:
            settings.append(f"{key[len('adjust_'):]}={max(low, min(high, value)):g}")
    if answers.get("adjust_grayscale"):
        # Saturation wins over any explicit value: the user asked for no colour.
        settings = [s for s in settings if not s.startswith("saturation=")]
        settings.append("saturation=0")
    if settings:
        out.append("eq=" + ":".join(settings))

    denoise = str(answers.get("denoise_level") or "off").strip().lower()
    if denoise in DENOISE_FILTERS:
        out.append(DENOISE_FILTERS[denoise])

    sharpen = str(answers.get("sharpen_level") or "off").strip().lower()
    blur = str(answers.get("blur_level") or "off").strip().lower()
    if sharpen in SHARPEN_FILTERS:
        out.append(SHARPEN_FILTERS[sharpen])
    elif blur in BLUR_FILTERS:
        # Only one of the two: sharpening a blur back is not a thing a user
        # means, and emitting both would silently make the pair meaningless.
        out.append(BLUR_FILTERS[blur])
    return out

def build_fade_filters(answers: dict[str, Any], output_seconds: float) -> list[str]:
    """The picture's half of the shared fade rule (see `fade_filter_parts`)."""
    return fade_filter_parts("", output_seconds, *requested_fade_seconds(answers))

def encode_timeline_map(answers: dict[str, Any]) -> TimelineMap:
    """The one source->output transform this encode applies.

    Cut, speed and reverse are read from the same answers the filter graph reads,
    so the cues cannot disagree with the picture. A Split part arrives here as
    its own single-input job carrying that part's `cut_keep_ranges`, which is
    already just another set of keep ranges.
    """
    # The PICTURE's span, not the container's. Reverse mirrors around this, so
    # a container that outlives its video -- audio padding, a trailing subtitle,
    # AAC priming -- pushed every retimed cue out by the difference: 523 ms on a
    # 4.000 s video inside a 4.523 s file.
    source_duration = video_stream_span_seconds(
        (answers.get("video_streams") or [{}])[0], answers.get("format"))
    if source_duration <= 0:
        source_duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    return TimelineMap(
        keep_ranges=list(answers.get("cut_keep_ranges") or []),
        source_duration=source_duration,
        speed=encode_video_speed_factor(answers) if video_speed_transform_enabled(answers) else 1.0,
        reverse=bool(answers.get("reverse_video")),
    )

def build_cpu_video_filter(answers: dict[str, Any]) -> str | None:
    filters: list[str] = []
    if answers.get("crop_enabled"):
        left, right, top, bottom = normalized_crop_margins(answers)
        filters.append(f"crop=iw-{left}-{right}:ih-{top}-{bottom}:{left}:{top}:exact=1")

    filters.extend(build_orientation_filters(answers))

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
            # square, making a trailing setsar=1 unnecessary -- but it does not
            # exist before FFmpeg 7.2, so ask before emitting it. A bare
            # trailing setsar=1 is NOT a substitute: on a 720x576 DAR-16:9
            # source it yields a squeezed 900x720 DAR-5:4 picture (D01).
            sar = source_sar(answers)
            crop_w, crop_h = cropped_source_size(answers)
            display_w, display_h = cropped_display_size(answers)
            filters.append(square_pixel_scale_chain(
                answers.get("ffmpeg") or "ffmpeg",
                f"scale={width}:{height}:"
                f"force_original_aspect_ratio=decrease:force_divisible_by=2",
            ))
            filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
            scale_resets_sar = True
            log_info(
                f"Resize mode: Preserve; "
                f"source_coded={crop_w}x{crop_h}; SAR={sar:.4f}; "
                f"display={display_w}x{display_h}; target={width}x{height}; "
                f"upscaling={'yes' if max(width, height) > max(display_w, display_h) else 'no'}"
            )

    filters.extend(build_look_filters(answers))

    if video_speed_transform_enabled(answers):
        filters.append(build_video_speed_filter(encode_video_speed_factor(answers), bool(answers.get("reverse_video"))))

    try:
        output_seconds = encode_timeline_map(answers).output_duration
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        # Only a genuinely unknown duration. A bare `except Exception` here
        # would also swallow a NameError or an ImportError and silently drop
        # every fade-out, which is how the audio side broke.
        output_seconds = 0.0
    filters.extend(build_fade_filters(answers, output_seconds))

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
    # `trim` reads what the demuxer hands the graph, which is already rebased by
    # the container start, while these ranges come off the editor's picture
    # clock. The same offset the input seek needs (B08).
    offset = picture_clock_offset(answers)
    for idx, (start, end) in enumerate(keep_ranges):
        trim_start, trim_end = start + offset, end + offset
        fc_parts.append(
            f"[{video_sources[idx]}]trim=start={trim_start:.6f}:end={trim_end:.6f},"
            f"setpts=PTS-STARTPTS[v{idx}]"
        )
        if audio_for_cut is not None:
            fc_parts.append(
                f"[{audio_sources[idx]}]atrim=start={trim_start:.6f}:end={trim_end:.6f},"
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

def gif_filter_chain(answers: dict[str, Any], prepared: bool = False) -> str:
    """The whole picture chain a GIF pass applies: the job's own, then the GIF's.

    A GIF is still an encode of THIS job. Building it from the bare source would
    silently discard the crop, the rotation, the colour work and the fade the
    user answered several questions to ask for -- a job that exits 0 and ignores
    half of what it was told. `build_cpu_video_filter` is the single place that
    knows the order those have to run in, so it is asked rather than copied.

    Its trailing `format=` is dropped: it selects a pixel format for a video
    ENCODER, and the next filter here is `palettegen`, which quantises to its
    own palette regardless.

    `prepared=True` for a source an earlier stage already wrote -- the
    boomerang's joined halves. Those edits are spent, and applying them again
    would crop the crop.
    """
    if prepared:
        return wizard_build_c.gif_scale_chain(answers)
    base = build_cpu_video_filter(answers) or ""
    parts = [part for part in base.split(",")
             if part and not part.startswith("format=")]
    parts.append(wizard_build_c.gif_scale_chain(answers))
    return ",".join(parts)

def build_gif_palette_command(answers: dict[str, Any], source: Path,
                              palette: Path, prepared: bool = False) -> list[str]:
    """Pass 1: the 256 colours this clip actually uses."""
    return [str(answers["ffmpeg"]), "-y" if OVERWRITE_OUTPUT else "-n",
            "-hide_banner", *wizard_build_c.gif_input_options(answers), "-i", str(source),
            "-vf", gif_filter_chain(answers, prepared) + "," + GIF_PALETTEGEN_FILTER,
            "-frames:v", "1", "-update", "1", str(palette)]

def build_gif_write_command(answers: dict[str, Any], source: Path, palette: Path,
                            output_path: Path, prepared: bool = False) -> list[str]:
    """Pass 2: the GIF, quantised against the palette pass 1 produced.

    `-an` is not tidiness. Without it FFmpeg auto-selects the source's audio for
    an output whose muxer has no way to carry it, and the job fails at the
    header rather than at the argument -- a long way from the cause.

    The unlabelled chain is deliberate: `paletteuse` takes two inputs, the chain
    supplies the first, and FFmpeg connects the remaining pad to the one input
    stream nothing else has claimed -- the palette.
    """
    return [str(answers["ffmpeg"]), "-y" if OVERWRITE_OUTPUT else "-n",
            "-hide_banner", *wizard_build_c.gif_input_options(answers), "-i", str(source),
            "-i", str(palette),
            "-lavfi", gif_filter_chain(answers, prepared) + "," + GIF_PALETTEUSE_FILTER,
            "-an", "-sn", str(output_path)]


__all__ = [
    'build_orientation_filters',
    'build_look_filters',
    'build_fade_filters',
    'encode_timeline_map',
    'build_cpu_video_filter',
    'build_cut_filter_complex',
    'gif_filter_chain',
    'build_gif_palette_command',
    'build_gif_write_command',
]
