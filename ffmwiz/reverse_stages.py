"""What ONE stage of the staged reverse is: what it owns, and what it emits.

Split out of `reverse_pipeline`, which had grown to hold both this and the
pipeline that plans, exports and runs the stages. Everything here answers a
question about a single stage -- which transformations it owns, what the file
it writes will look like, how much of the timeline it may buffer, which streams
its final mux keeps -- and none of it knows the order stages run in.

`reverse_pipeline` imports this module; the facade names the tests monkeypatch
are still called through `encoding.<name>`, so a patch on the facade reaches
the code that runs.
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
import queue
import threading
import concurrent.futures
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, NamedTuple
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
from ffmwiz.support.L01_subtitles import *  # noqa: F401,F403
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
from ffmwiz.guibridge import *  # noqa: F401,F403
from ffmwiz.metadata import *  # noqa: F401,F403
from ffmwiz import metadata  # noqa: F401
from ffmwiz.modes import *  # noqa: F401,F403
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.trackmanager import *  # noqa: F401,F403
from ffmwiz.wizard import *  # noqa: F401,F403
from ffmwiz import wizard_build  # noqa: E402,F401  (defines build_ffmpeg_command)
# The `encoding` back-import was deleted: the names used here are defined
# in this module or in the lower tiers above. `encoding` ends with
# `__all__ += reverse_pipeline.__all__`, which made this module
# unimportable on its own.


def build_main_encode_reverse_segment_command(
    answers: dict[str, Any],
    start: float,
    end: float,
    output_path: Path,
) -> list[str]:
    # Open the lease BEFORE the shallow copy. dict() shares the container only
    # if the key is already there, so without this each segment got a lease of
    # its own and every per-segment retimed-subtitle and chapter directory
    # leaked -- the R06 trap, on the one path that makes the most copies.
    artifact_lease(answers)
    segment_answers = dict(answers)
    segment_answers["cut_keep_ranges"] = [(start, end)]
    segment_answers["output_location"] = output_path.parent
    segment_answers["output_name_stem"] = output_path.stem
    segment_answers["output_ext"] = output_path.suffix.lstrip(".") or str(answers.get("output_ext") or "mp4")
    segment_answers["output_collision_suffix"] = ""
    segment_answers.pop("output_path", None)
    return wizard_build.build_ffmpeg_command(segment_answers)


def reverse_filter_input_for(answers: dict[str, Any]):
    """What the `reverse` filter will actually buffer for this job.

    `reverse` holds POST-filter frames and the CPU chain runs
    crop -> fps -> scale/pad -> speed/reverse -> format, so sizing the budget
    from the probe is sizing it from the wrong picture. A 1080p30 source
    upscaled to 8K received a 15 s window whose real peak is 24.485 GiB against
    a 2 GiB cap (D11). This resolves the geometry, rate and pixel format at the
    filter's input and hands them to the shared planner.
    """
    stream = (answers.get("video_streams") or [{}])[0]
    try:
        # `default=0.0`, NOT the helper's 25.0. A stream whose rate the probe
        # cannot read was budgeted at 25 fps and still reported hard_capped:
        # 7680x4320 yuv420p10le with `avg_frame_rate=0/0` planned a 560 ms
        # window of 14 frames, which at a real 120 fps decodes 68 frames =
        # 7.749 GiB against the 2 GiB cap. Zero is what makes the planner's
        # "rate unknown" branch fire, so an unknown rate is refused or marked
        # best-effort instead of silently promised.
        source_fps = float(services.get_video_fps(answers, default=0.0) or 0.0)
    except (KeyError, ValueError, TypeError):
        source_fps = 0.0
    crop_size = cropped_source_size(answers) if answers.get("crop_enabled") else None
    try:
        scale_size = resolve_scale_dimensions(answers, answers.get("resolution", "n"))
    except Exception:
        scale_size = None
    try:
        encoder, _tag, _profile = resolve_video_encoder(answers)
        graph_pix_fmt = cpu_graph_pixel_format_for_encoder(answers, encoder)
    except Exception:
        graph_pix_fmt = None
    # The LARGER of the two, not simply the graph's. `format=` sits downstream
    # of `reverse`, and FFmpeg negotiates that format back up the chain, so the
    # buffered frames usually carry the encoder's format -- but "usually" is
    # not a basis for a HARD cap. If the negotiation does not reach this far the
    # buffer holds source frames, and sizing a 12-bit 4:4:4 source as 8-bit
    # 4:2:0 under-counts it four to one. Taking the wider format costs a
    # shorter segment and never an overrun.
    source_pix_fmt = stream.get("pix_fmt")
    if graph_pix_fmt and source_pix_fmt:
        if decoded_bytes_per_pixel(source_pix_fmt) > decoded_bytes_per_pixel(graph_pix_fmt):
            graph_pix_fmt = source_pix_fmt
    return reverse_filter_input_descriptor(
        stream.get("width"), stream.get("height"), source_fps, source_pix_fmt,
        crop_size=crop_size, scale_size=scale_size,
        output_fps=answers.get("fps"), graph_pix_fmt=graph_pix_fmt)


def reverse_segment_plan_for(answers: dict[str, Any], best_effort: bool = False):
    """The bounded plan for one reverse segment, with its whole calculation.

    Raises `ReverseBudgetError` when the geometry it needs is unreadable or one
    frame already exceeds the allowance. That is the point: a warning cannot
    turn an unbounded allocation into a bound, and the old code assumed
    1080p60 10-bit and carried on (D12).
    """
    resolved = reverse_filter_input_for(answers)
    plan = reverse_segment_plan(resolved.width, resolved.height, resolved.fps,
                                resolved.pix_fmt, best_effort=best_effort)
    log_info("Reverse budget: " + plan.describe())
    if not plan.hard_capped:
        log_warn("Reverse budget: NOT hard-capped -- " + "; ".join(plan.assumptions))
    return plan


def reverse_segment_seconds(answers: dict[str, Any]) -> float:
    """How many seconds of video one reverse segment may safely hold.

    Thin wrapper for callers that only want the number; anything that has to
    PRINT the window should use `reverse_segment_plan_for` and its
    `window_text`, because `f"{seconds:.0f}s"` renders a legitimate 233 ms
    budget as `0s` (D09).
    """
    return reverse_segment_plan_for(answers).seconds


# Every user edit the staged reverse pipeline can apply, grouped by the
# transformation that owns it. A stage that does not own a transformation must
# not carry its keys: the pipeline used to clear only the VIDEO edits, so an
# independent 2x audio speed was applied by the forward-join stage, again by
# the segmented reverse and again by the final split. Measured on two joined
# 2 s clips: 4.100 s of video against 1.111 s of audio, where one application
# owes about 2 s (B01).
STAGE_TRANSFORMATIONS: dict[str, tuple[str, ...]] = {
    "cuts": ("cut_keep_ranges",),
    "audio_cuts": ("audio_cut_keep_ranges", "audio_cut_stream_copy"),
    "video_speed": ("video_speed_enabled", "video_speed_factor"),
    "audio_speed": ("audio_speed_enabled", "audio_speed_factor",
                    "audio_speed_from_video"),
    "video_reverse": ("reverse_video",),
    "audio_reverse": ("reverse_audio",),
    "loudnorm": ("loudnorm_enabled", "loudnorm_mode", "loudnorm_measured",
                 "loudnorm_target_i"),
    "split": ("separator_points", "split_output_paths", "split_part_intervals"),
    # GEOMETRY. `build_cpu_video_filter` orders crop -> fps -> scale/pad ->
    # speed/reverse, so every one of these is a semantic transformation that a
    # stage can apply a second time. They were missing, which is why a
    # `reverse_stages.stage_answers(..., owns=())` "neutral" stage still cropped: a real
    # 160x120 Join + Reverse + Split asking for 10 px off each side produced
    # 120x120 parts instead of 140x120, with `crop=` in joined_forward.mkv, in
    # every reverse segment AND in the final Split graph (D01). FPS and resize
    # leak the same way; a repeated scale is not free even when it is
    # dimensionally idempotent, because it re-processes an already lossy
    # intermediate and changes the frame layout the reverse budget is sized
    # from (D02).
    "crop": ("crop_enabled", "crop_top", "crop_left", "crop_right",
             "crop_bottom", "crop_box_dimensions", "cropped_aspect_ratio"),
    "fps": ("fps",),
    "resize": ("resolution", "final_resolution"),
    # Five more transformations shipped in d0e8f69/0fe7e98 with no schema
    # entry at all, so a Join + Reverse + Split applied every one of them
    # three times: a 90-degree rotation applied twice is 180 degrees, and a
    # 2x gain applied three times is 8x.
    #
    # QUICK_ANSWER_KEYS and COMPOSITE_ANSWER_KEYS are deliberately NOT added
    # below, on evidence rather than omission -- a dead schema entry would
    # make the next reader believe a path is covered that it is not. Quick
    # outputs never reach a staged reverse: `ffmwiz/encoding.py:512-516`
    # dispatches a `quick_output_plan` job to `run_quick_output_stages` before
    # the reverse branch below it is reached, and `_quick_sub_job`
    # (`ffmwiz/wizard_build.py:371-381`) strips every `QUICK_ANSWER_KEYS` entry
    # from the sub-job it hands onward. Compositing is DROPPED by a staged
    # reverse rather than repeated: the composite branch is reached only from
    # `ffmwiz/wizard_b.py:222`, and neither `build_ffmpeg_command` nor
    # `build_join_encode_command` has one -- a missing branch, which is a
    # different fix.
    #
    # Rotation and flips change the FRAME the rest of the chain sees: a
    # 90-degree rotation swaps width and height, so it travels with the
    # geometry group rather than with `look` (`build_orientation_filters`).
    "orientation": ("rotate_choice", "flip_horizontal", "flip_vertical"),
    # Colour, denoise and sharpen/blur (`build_look_filters`). Denoising twice
    # is visibly softer, not merely redundant -- each pass re-processes an
    # already lossy intermediate.
    "look": ("adjust_grayscale", "denoise_level", "sharpen_level", "blur_level",
             "adjust_brightness", "adjust_contrast", "adjust_saturation",
             "adjust_gamma"),
    # A fade per stage compounds, and a fade applied by a stage before the
    # reverse lands at the OTHER end of the clip once the picture is mirrored
    # (`fade_filter_parts`, `ffmwiz/support/L01_filters.py:140-167`).
    "fade": ("fade_in_seconds", "fade_out_seconds"),
    # A gain applied by three stages is the gain CUBED, not tripled: 2x
    # becomes 8x (`build_volume_filter`, `ffmwiz/wizard_raw.py:92-105`).
    "volume": ("audio_volume",),
    # The user's own output options, appended LAST so they override the
    # wizard's (`ffmwiz/wizard_build.py:886`). `intermediate_profile`
    # deliberately strips the rate control from a scratch stage but cannot
    # strip an opaque argv, so a `-b:v 500k` here re-imposes on
    # `joined_forward.mkv` exactly the low bitrate that function exists to
    # avoid.
    "raw_args": ("raw_ffmpeg_args",),
}

# Geometry travels together: cropping in one stage and resizing in another
# would make the second stage scale a frame the first already changed.
# Orientation belongs here too, not with `look`: a 90-degree rotation swaps
# width and height, so it must not be split from the resize that is sized
# against it.
GEOMETRY_TRANSFORMATIONS: tuple[str, ...] = ("crop", "fps", "resize", "orientation")


def _requests_video_speed(answers: dict[str, Any]) -> bool:
    return (bool(answers.get("video_speed_enabled"))
            and float(answers.get("video_speed_factor") or 1.0) != 1.0)


def _requests_orientation(answers: dict[str, Any]) -> bool:
    # Read the same way `build_orientation_filters` reads it, so a value the
    # builder ignores (an unrecognised `rotate_choice`) is not counted as a
    # request. "none" is the explicit no-op, not a key of ROTATE_FILTERS.
    rotation = str(answers.get("rotate_choice") or "none").strip().lower()
    return (rotation in ROTATE_FILTERS
            or bool(answers.get("flip_horizontal"))
            or bool(answers.get("flip_vertical")))


def _requests_look(answers: dict[str, Any]) -> bool:
    # Same reads as `build_look_filters`: a level not in its filter table is
    # "off" even if the key is set to something, and a value that will not
    # convert to float is "not requested" rather than a plan-time TypeError.
    if answers.get("adjust_grayscale"):
        return True
    for key, table in (("denoise_level", DENOISE_FILTERS),
                       ("sharpen_level", SHARPEN_FILTERS),
                       ("blur_level", BLUR_FILTERS)):
        if str(answers.get(key) or "off").strip().lower() in table:
            return True
    for key, (_low, _high, neutral) in ADJUST_RANGES.items():
        try:
            value = float(answers.get(key, neutral))
        except (TypeError, ValueError):
            continue
        if value != neutral:
            return True
    return False


def _requests_volume(answers: dict[str, Any]) -> bool:
    # Matches `build_volume_filter`'s own guard and its own tolerance
    # (`ffmwiz/wizard_raw.py:98-102`), so this predicate and the filter it
    # predicts agree on what counts as a gain.
    try:
        factor = float(answers.get("audio_volume") or 1.0)
    except (TypeError, ValueError):
        return False
    return abs(factor - 1.0) > 1e-9


# What makes each transformation REQUESTED. Ownership is only meaningful
# against this: a plan that owns nothing is valid for a job that asks for
# nothing, and invalid for one that asks for a crop.
_TRANSFORMATION_REQUESTED: dict[str, Any] = {
    "cuts": lambda a: bool(a.get("cut_keep_ranges")),
    "audio_cuts": lambda a: bool(a.get("audio_cut_keep_ranges")),
    "video_speed": _requests_video_speed,
    "audio_speed": lambda a: (
        (bool(a.get("audio_speed_enabled"))
         and float(a.get("audio_speed_factor") or 1.0) != 1.0)
        or (bool(a.get("audio_speed_from_video")) and _requests_video_speed(a))),
    "video_reverse": lambda a: bool(a.get("reverse_video")),
    "audio_reverse": lambda a: bool(a.get("reverse_audio")),
    "loudnorm": lambda a: (bool(a.get("loudnorm_enabled"))
                           and str(a.get("loudnorm_mode") or "off") != "off"),
    "split": lambda a: bool(a.get("separator_points")),
    "crop": lambda a: (bool(a.get("crop_enabled"))
                       and any(int(a.get(f"crop_{edge}", 0) or 0)
                               for edge in ("top", "left", "right", "bottom"))),
    "fps": lambda a: a.get("fps") is not None,
    "resize": lambda a: a.get("resolution") not in (None, "n"),
    "orientation": _requests_orientation,
    "look": _requests_look,
    "fade": lambda a: any(requested_fade_seconds(a)),
    "volume": _requests_volume,
    "raw_args": lambda a: bool(a.get("raw_ffmpeg_args")),
}


def requested_transformations(answers: dict[str, Any]) -> set[str]:
    """Every transformation this job actually asks for.

    Declared per transformation rather than inferred, so a new key added to
    `STAGE_TRANSFORMATIONS` without a matching predicate fails loudly here
    instead of being silently treated as never requested.
    """
    missing = set(STAGE_TRANSFORMATIONS) - set(_TRANSFORMATION_REQUESTED)
    if missing:
        raise ValueError(
            f"transformation(s) with no requested-predicate: {sorted(missing)}")
    return {name for name, asked in _TRANSFORMATION_REQUESTED.items()
            if asked(answers)}

# Falsey neutral values, so a stage that reads a key without checking for its
# absence still sees "no transformation" rather than a stale truth.
_NEUTRAL_VALUES: dict[str, Any] = {
    "video_speed_factor": 1.0,
    "audio_speed_factor": 1.0,
    "loudnorm_mode": "off",
    # "n" is what the builders read as "keep the source size"; removing the key
    # would make `answers.get("resolution", "n")` agree by accident, but a
    # stage that reads it without a default would see nothing at all.
    "resolution": "n",
}


def intermediate_profile(staged: dict[str, Any]) -> dict[str, Any]:
    """Retune a pipeline stage that writes a SCRATCH file, not the user's output.

    An intermediate is re-encoded again by a later stage, so it must not carry
    the final output's rate control. Setting `video_crf` alone did nothing: the
    encoder builder prefers a bitrate when one is present, so a job targeting
    250 kbps wrote `joined_forward.mkv` AND the reverse segments at `-b:v 250k`
    with no effective CRF -- several low-bitrate generations before the encode
    the user actually asked for, and the same again for `-b:a 32k` (B03).

    Video becomes near-lossless CRF, audio becomes lossless FLAC, and the
    container becomes Matroska so both are always legal. Pixel format, bit
    depth and colour metadata are left alone: they are the properties the
    later stage has to preserve.
    """
    scratch = dict(staged)
    for key in ("video_bitrate_kbps", "video_bitrate_mode", "cpu_two_pass",
                "nvenc_multipass", "nvenc_multipass_skip_reason",
                "audio_bitrate_kbps"):
        scratch.pop(key, None)
    scratch["video_crf"] = REVERSE_INTERMEDIATE_CRF
    scratch["crf"] = REVERSE_INTERMEDIATE_CRF
    scratch["audio_codec"] = INTERMEDIATE_AUDIO_CODEC
    scratch["output_ext"] = INTERMEDIATE_CONTAINER_EXT
    return scratch


# Dispositions worth carrying, named explicitly. Echoing back every truthy key
# ffprobe reports would eventually hand FFmpeg a flag name its `-disposition`
# parser does not accept, and a rejected command loses more than a lost flag.
_CARRIED_DISPOSITIONS = ("default", "forced", "hearing_impaired",
                         "visual_impaired", "comment", "descriptions",
                         "original", "dub")


def _disposition_value(stream: dict[str, Any]) -> str:
    flags = (stream or {}).get("disposition") or {}
    kept = [name for name in _CARRIED_DISPOSITIONS if flags.get(name)]
    return "+".join(kept) if kept else "0"


def _selected_indices(streams: list[Any], chosen: Any) -> list[int]:
    if chosen is None or chosen is True or chosen == "all":
        return list(range(len(streams)))
    try:
        return [int(index) for index in chosen if 0 <= int(index) < len(streams)]
    except TypeError:
        return list(range(len(streams)))


# What an encoder actually writes, for describing a file that does not exist
# yet. The planner hardcoded `h264` here, which is wrong the moment the job
# selects HEVC, VP9 or AV1 and silently changes later container/codec
# decisions taken against the descriptor (D07).
_ENCODER_CODEC_NAMES: dict[str, str] = {
    "libx264": "h264", "h264_nvenc": "h264", "h264_qsv": "h264",
    "libx265": "hevc", "hevc_nvenc": "hevc", "hevc_qsv": "hevc",
    "libvpx-vp9": "vp9", "vp9_qsv": "vp9",
    "libaom-av1": "av1", "av1_nvenc": "av1", "libsvtav1": "av1",
    "mpeg4": "mpeg4", "libxvid": "mpeg4",
}


def intermediate_video_codec_name(writer: dict[str, Any]) -> str:
    """The codec_name the stage described by `writer` will actually produce.

    Resolved through the SAME `resolve_video_encoder` the command builder uses,
    so the descriptor cannot disagree with the command. An encoder this table
    does not know returns its own name rather than a plausible substitute --
    wrong-but-recognisable beats confidently wrong.
    """
    try:
        encoder, _tag, _profile = resolve_video_encoder(writer)
    except Exception:  # a descriptor must never break the plan it describes
        encoder = ""
    encoder = str(encoder or "").lower()
    return _ENCODER_CODEC_NAMES.get(encoder, encoder or "h264")


def intermediate_video_descriptor(writer: dict[str, Any]) -> dict[str, Any]:
    """The video properties of the file the stage described by `writer` WRITES.

    Not the properties of the file it reads. A forward join owns crop, fps and
    resize, so the picture it hands the reverse stage can be nothing like the
    one it started from -- and the reverse memory budget is computed from
    exactly these numbers. Measured on two 160x120 sources joined and scaled to
    1920x1080 at 60 fps, against a 640 MiB cap over 512 MiB of overhead:

        EXPORTED_SEGMENTS 1, -t 5.000000  (300 frames of 1920x1080)
        SAFE_POST_TRANSFORM 37 frames = 0.616666 s
        ESTIMATED_PEAK 1.499 GiB   CAP 0.625 GiB   EXCESS 2.40x

    The automatic executor probes the intermediate it has just written and is
    safe; only the exported plan, which describes a file that does not exist
    yet, could be this wrong (D07).

    Every value comes from the SAME helper the command builder uses --
    `reverse_filter_input_for` for the geometry and rate at the end of the
    filter chain, `target_pixel_format_for_answers` for the format the resolved
    encoder writes -- so a descriptor cannot disagree with the command that
    produces it. What a stage cannot know until it has run is deliberately
    absent: the written duration is supplied by the caller that planned it, and
    the frame count is not guessed at all.

    `width`/`height` come back as 0 when the source geometry is unreadable,
    which is what makes the reverse budget REFUSE rather than plan against an
    invented picture.
    """
    resolved = reverse_filter_input_for(dict(writer))
    try:
        # The format the file will hold, not the wider one the reverse BUFFER
        # may hold: `reverse_filter_input_for` deliberately takes the larger of
        # the source and the graph format when sizing memory, and that
        # conservatism belongs to the budget, not to a description of a file.
        pix_fmt = target_pixel_format_for_answers(writer) or resolved.pix_fmt
    except Exception:  # a descriptor must never break the plan it describes
        pix_fmt = resolved.pix_fmt
    return {"codec_name": intermediate_video_codec_name(writer),
            "width": resolved.width, "height": resolved.height,
            "pix_fmt": pix_fmt, "fps": resolved.fps}


def reverse_mux_stream_policy(
        answers: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """Which streams the video-only reverse mux keeps, and from which input.

    Input 0 is the reversed picture, input 1 the forward audio. The whole
    policy used to be `-map 0:v -map 1:a`, and once ANY explicit map is given
    FFmpeg selects nothing else -- so every other stream type was dropped in
    the last command of the pipeline, after the earlier stages had carried it
    faithfully. A real source came out two streams short:

        SOURCE topology  ['video', 'audio', 'subtitle', 'attachment']
        OUTPUT topology  ['video', 'audio']

    The subtitle had even been retimed onto the processed timeline and
    announced to the user before being discarded (D03), and attachments and
    data streams went the same way (D04).

    The reversed picture is the authority for everything except audio: the
    per-segment encodes already applied the user's stream selection, and the
    concat copy preserves what they produced -- measured on segments carrying
    video/audio/subtitle/attachment, `-map 0 -c copy -an` yielded
    video/subtitle/attachment. So the policy is "everything input 0 still has,
    minus its audio, plus input 1's audio", stated per type rather than by
    negative mapping so it does not depend on the FFmpeg build's handling of
    `-map -0:a`.

    Dispositions come back too. The concat DEMUXER does not carry them, which
    is invisible until you look: traced through one run, the segment held
    `default=1 forced=1` and the concat copy that consumed it held
    `default=0 forced=0`, so a subtitle the source marked default and forced
    arrived marked neither. Only the ordinary encode paths were preserving
    them, which is why nothing caught it. They are restored here, against the
    SOURCE streams, at the one point where the final output order is known.

    Returns (maps, dispositions, warnings). Attachments are only claimed when
    the chosen container can actually hold one; when it cannot, the loss is
    REPORTED rather than silent, which is the difference the brief asks for.
    """
    maps = ["-map", "0:v", "-map", "1:a", "-map", "0:s?", "-map", "0:d?"]
    warnings: list[str] = []
    has_attachments = bool(answers.get("attachment_streams"))
    if output_supports_embedded_attachments(answers):
        maps += ["-map", "0:t?"]
    elif has_attachments and answers.get("keep_embedded_attachments"):
        warnings.append(
            f"{str(answers.get('output_ext') or '').upper()} cannot store embedded "
            "attachments, so the source's attachment(s) are not carried into the "
            "reversed output. Choose MKV to keep them.")

    dispositions: list[str] = []
    for kind, key, chosen in (("a", "audio_streams", answers.get("audio_tracks")),
                              ("s", "subtitle_streams", answers.get("subtitle_tracks"))):
        streams = list(answers.get(key) or [])
        for position, index in enumerate(_selected_indices(streams, chosen)):
            dispositions += [f"-disposition:{kind}:{position}",
                             _disposition_value(streams[index])]
    return maps, dispositions, warnings


def stage_answers(answers: dict[str, Any], owns: tuple[str, ...]) -> dict[str, Any]:
    """A copy of `answers` carrying ONLY the transformations this stage owns.

    Ownership is declared, never inferred from what happens to be in a copied
    dictionary. Anything the stage does not own is removed, and the effective
    map is dropped with it so a resolution made for another stage cannot leak
    in (the same map is what let a Join's libx265 fallback survive a rebuild).
    """
    unknown = set(owns) - set(STAGE_TRANSFORMATIONS)
    if unknown:
        raise ValueError(f"unknown transformation(s): {sorted(unknown)}")
    staged = dict(answers)
    staged.pop(EFFECTIVE_SETTINGS_KEY, None)
    for name, keys in STAGE_TRANSFORMATIONS.items():
        if name in owns:
            continue
        for key in keys:
            if key in _NEUTRAL_VALUES:
                staged[key] = _NEUTRAL_VALUES[key]
            else:
                staged.pop(key, None)
    return staged


def validate_stage_plan(stages: list[tuple[str, tuple[str, ...]]],
                       answers: dict[str, Any] | None = None) -> None:
    """Every transformation the job requests is owned by EXACTLY one stage.

    Raises on a duplicate: applying a speed change twice is silent in the argv
    and only visible in the finished media, which is how B01 survived.

    Pass `answers` and it also raises on a MISSING owner. Duplicate-only
    checking could not see D01 at all -- crop was owned by no stage, so there
    was nothing to be a duplicate OF, and it simply survived into all three.
    An unowned transformation is not neutral; it is applied wherever the
    filter builder happens to look.
    """
    seen: dict[str, str] = {}
    for label, owns in stages:
        for name in owns:
            if name not in STAGE_TRANSFORMATIONS:
                raise ValueError(f"unknown transformation: {name!r}")
            if name in seen:
                raise ValueError(
                    f"transformation {name!r} is owned by both {seen[name]!r} "
                    f"and {label!r}")
            seen[name] = label
    if answers is None:
        return
    unowned = sorted(requested_transformations(answers) - set(seen))
    if unowned:
        raise ValueError(
            f"transformation(s) {unowned} are requested but owned by no stage; "
            f"they would be applied in every stage that reads them")


def _single_input_answers(answers: dict[str, Any], source: Path) -> dict[str, Any]:
    """Re-point a job at one already-produced file, keeping its output settings."""
    probe = services.ffprobe_json(answers.get("ffprobe") or "ffprobe", source)
    streams = (probe or {}).get("streams") or []
    rebased = dict(answers)
    rebased.pop("join_input_items", None)
    rebased["input_path"] = source
    rebased["probe"] = probe or {}
    rebased["format"] = (probe or {}).get("format") or {}
    rebased["video_streams"] = [s for s in streams if s.get("codec_type") == "video"]
    rebased["audio_streams"] = [s for s in streams if s.get("codec_type") == "audio"]
    rebased["subtitle_streams"] = [s for s in streams if s.get("codec_type") == "subtitle"]
    return rebased


def reverse_concat_stages(answers: dict[str, Any], segment_paths: list[Path],
                          workdir: Path, output_path: Path, speed: float,
                          segment_ext: str,
                          progress_seconds: float | None = None,
                          ) -> tuple[list[tuple[str, list[str], float | None]], list[str]]:
    """Every command that turns finished reverse segments into the output.

    ONE implementation, consumed twice: the executor runs these commands and
    the exported plan writes them down. It used to be two -- the executor built
    the final mux by hand with chapter arguments and mp4 flags, while the
    planner reached for `build_concat_copy_command` -- so the exported plan
    quietly omitted `-map_chapters` and could not have produced the same file.
    A second implementation of a pipeline drifts; the only fix that stays fixed
    is not having one.

    Writes the concat lists and any chapter metadata into `workdir`, so the
    exported script is runnable as it stands. Returns (stages, warnings) where
    each stage is (label, argv, progress_seconds_or_None).
    """
    stages: list[tuple[str, list[str], float | None]] = []
    warnings: list[str] = []
    if not segment_paths:
        return stages, warnings
    ffmpeg = answers.get("ffmpeg") or "ffmpeg"
    audio_follows_reverse = encode_audio_reverse_enabled(answers)
    has_audio = bool(answers.get("audio_streams")) and bool(answers.get("audio_tracks", True))
    concat_list = workdir / "concat.txt"
    if has_audio and not audio_follows_reverse:
        video_ext = output_path.suffix.lstrip(".") or segment_ext
        reversed_video = workdir / f"video_reversed.{video_ext}"
        forward_audio = workdir / f"audio_forward.{video_ext}"
        write_concat_list(list(reversed(segment_paths)), concat_list)
        video_cmd = build_concat_copy_command(ffmpeg, concat_list, reversed_video)
        video_cmd[video_cmd.index(str(reversed_video)):] = ["-an", str(reversed_video)]
        audio_list = workdir / "concat_audio.txt"
        write_concat_list(list(segment_paths), audio_list)
        audio_cmd = build_concat_copy_command(ffmpeg, audio_list, forward_audio)
        audio_cmd[audio_cmd.index(str(forward_audio)):] = ["-vn", str(forward_audio)]
        stages.append(("Reverse encode concat video (reversed order)",
                       [str(part) for part in video_cmd], progress_seconds))
        stages.append(("Reverse encode concat audio (source order)",
                       [str(part) for part in audio_cmd], progress_seconds))
        mux_inputs = ["-i", str(reversed_video), "-i", str(forward_audio)]
        mux_maps, mux_dispositions, warnings = reverse_mux_stream_policy(answers)
        mux_maps = mux_maps + mux_dispositions
        metadata_input = 2
    else:
        write_concat_list(list(reversed(segment_paths)), concat_list)
        mux_inputs = ["-f", "concat", "-safe", "0", "-i", str(concat_list)]
        mux_maps = ["-map", "0"]
        metadata_input = 1

    # The per-segment encodes carry chapter metadata, but this final
    # concat-copy did not restore any of it, so a reversed chaptered source
    # came out with ZERO chapters (R05). Attach the remapped chapters here,
    # where the output timeline finally exists. remap_chapters_for_encode
    # applies the reverse flip itself.
    chapter_plan = remap_chapters_for_encode(answers, speed_factor=speed)
    concat_cmd = [str(ffmpeg), "-y" if OVERWRITE_OUTPUT else "-n",
                  "-hide_banner", *mux_inputs]
    if chapter_plan.get("mode") == "metadata" and chapter_plan.get("chapters"):
        chapter_metadata = write_encode_chapter_metadata(chapter_plan, workdir, "_reverse")
        # Appended LAST so it is always the highest input index: the
        # video-only branch already occupies 0 and 1, and inserting it
        # earlier would renumber the audio input the maps depend on.
        concat_cmd.extend(["-i", str(chapter_metadata)])
        chapter_args = copy_cut_chapter_map_args(
            chapter_plan, metadata_input_index=metadata_input)
    else:
        # Be explicit rather than leaving a silent gap: a source WITH
        # chapters whose plan is not usable loses them here.
        chapter_args = copy_cut_chapter_map_args(chapter_plan)
    concat_cmd.extend([*mux_maps, "-c", "copy",
                       "-avoid_negative_ts", "make_zero", *chapter_args])
    if output_path.suffix.lstrip(".").lower() in MP4_LIKE_EXTS and MOVFLAGS:
        concat_cmd.extend(["-movflags", MOVFLAGS])
    concat_cmd.append(str(output_path))
    stages.append(("Concatenating reversed encoded segments",
                   [str(part) for part in concat_cmd], progress_seconds))
    return stages, warnings


__all__ = [
    'GEOMETRY_TRANSFORMATIONS',
    'STAGE_TRANSFORMATIONS',
    '_disposition_value',
    '_requests_look',
    '_requests_orientation',
    '_requests_video_speed',
    '_requests_volume',
    '_selected_indices',
    '_single_input_answers',
    'build_main_encode_reverse_segment_command',
    'intermediate_profile',
    'intermediate_video_codec_name',
    'intermediate_video_descriptor',
    'requested_transformations',
    'reverse_concat_stages',
    'reverse_filter_input_for',
    'reverse_mux_stream_policy',
    'reverse_segment_plan_for',
    'reverse_segment_seconds',
    'stage_answers',
    'validate_stage_plan',
]
