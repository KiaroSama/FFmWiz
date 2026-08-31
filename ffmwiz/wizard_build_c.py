"""Compositing and quick outputs: the composite filter graph, and the argv
pieces GIF, boomerang and thumbnail jobs assemble from.

Split out of `wizard_build_b` for file size. `gif_filter_chain`,
`build_gif_palette_command` and `build_gif_write_command` deliberately stayed
behind there: they route through `build_cpu_video_filter`, which a guard test
in `tests/test_speed_frame_retention.py` pins to `wizard_build_b.py`.

`wizard_build_b` re-exports this module at its end, so every existing
`from ffmwiz.wizard_build_b import *` keeps working unchanged.
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


def composite_active(answers: dict[str, Any]) -> bool:
    """True when this job composites a second input over/beside the first."""
    return bool(answers.get("composite_path") or answers.get("composite_audio_path"))


def _even(value: float) -> int:
    """The nearest even number, at least 2 -- yuv420p refuses odd dimensions."""
    return max(2, int(round(value / 2.0)) * 2)


def _graph_ref(label: str) -> str:
    """A `-map` label as a filter_complex INPUT reference.

    The graph returns either a `[name]` pad or a bare `0:a:1` stream
    specifier, because `-map` takes both. Only the first is already a pad.
    """
    return label if label.startswith("[") else f"[{label}]"


def composite_base_picture_chain(answers: dict[str, Any]) -> str:
    """The picture chain that runs BEFORE the composite, as one filter string.

    Everything that describes what the BASE picture should look like -- crop,
    orientation, frame rate, resize, colour/denoise/sharpen -- belongs in front
    of the overlay, so the second input lands on the picture the user asked
    for. Speed and the fade do not: see `composite_tail_picture_chain`.

    Built by calling the ordinary `build_cpu_video_filter` on a copy with those
    two suppressed, rather than by assembling the parts again here. A second
    assembly is a second definition, and this codebase's recurring defect is
    exactly that -- a gate or a chain that names fewer things than its twin.
    """
    # Deferred: `wizard_build_filters` imports THIS module, so a module-level
    # import here is a cycle. Same reason `build_encode_audio_processing_filter`
    # defers `encode_timeline_map`.
    from ffmwiz.wizard_build_filters import build_cpu_video_filter
    pre = dict(answers)
    pre.pop("fade_in_seconds", None)
    pre.pop("fade_out_seconds", None)
    pre.pop("video_speed_enabled", None)
    chain = build_cpu_video_filter(pre) or ""
    # That builder always ends with a pixel-format conversion, and the
    # overlay and stack chains already end with one of their own. A chain
    # that is ONLY the conversion means the job asked for no picture edit,
    # so it is dropped rather than emitted as a no-op link.
    parts = [part for part in chain.split(",") if part]
    if all(part.startswith("format=") for part in parts):
        return ""
    return chain


def composite_tail_picture_chain(answers: dict[str, Any]) -> list[str]:
    """Speed and the fade, which run AFTER the composite.

    A fade in front of the overlay would fade the base while the overlay stayed
    at full brightness; the user means the finished picture. Speed in front of
    it would retime only the first input, so an hstack of two clips would
    drift apart -- applied to the composited stream it retimes both equally,
    and the audio chain applies the matching `atempo`, so sound and picture
    stay together.
    """
    parts: list[str] = []
    if video_speed_transform_enabled(answers):
        parts.append(build_video_speed_filter(
            encode_video_speed_factor(answers), bool(answers.get("reverse_video"))))
    from ffmwiz.wizard_build_filters import build_fade_filters
    # NOT `encode_timeline_map`: `wizard_build_b` imports this module, so
    # reaching back for it is the cycle `test_module_reference_hygiene`
    # forbids. It is also not needed -- a composite is as long as its FIRST
    # input by construction (`build_composite_command` emits `-t` to say so)
    # and it carries no cuts, so the output duration is the source duration
    # divided by the speed factor.
    try:
        output_seconds = float((answers.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        # A genuinely unknown duration; `build_fade_filters` drops the
        # fade-out and says so.
        output_seconds = 0.0
    if output_seconds and video_speed_transform_enabled(answers):
        factor = encode_video_speed_factor(answers)
        if factor > 0:
            output_seconds /= factor
    parts.extend(build_fade_filters(answers, output_seconds))
    return parts


def composite_base_output_size(answers: dict[str, Any]) -> tuple[int, int]:
    """The base picture's size AFTER `composite_base_picture_chain`.

    `hstack`/`vstack` need the partner scaled to the base's exact height or
    width, so the number has to come from the transformed picture, not from the
    source stream. Read in chain order: crop, then the 90-degree rotations that
    swap the axes, then the resize target if one was asked for.
    """
    width, height = cropped_source_size(answers)
    if str(answers.get("rotate_choice") or "").strip().lower() in {"90cw", "90ccw"}:
        width, height = height, width
    scaled = resolve_scale_dimensions(answers, answers.get("resolution", "n"))
    if scaled:
        width, height = scaled
    return _even(width or 640), _even(height or 360)


def build_composite_filter_graph(
    answers: dict[str, Any],
    picture_input: int | None,
    audio_input: int | None,
    pix_fmt: str = "yuv420p",
) -> tuple[list[str], str | None, str | None, list[str]]:
    """The filter chains for a composite, plus the labels to map and the notes.

    `picture_input` and `audio_input` are the INPUT INDICES of the two partners,
    passed separately and never derived from each other. A job with both is
    three inputs and the two graphs address different ones -- `[0:v][1:v]` for
    the picture, `[0:a][2:a]` for the mix. Assuming "the extra input" is a
    single thing is the arithmetic that has broken this codebase before.

    Returns (chains, video_label, audio_label, notes). A label is either a
    `[name]` from the graph or a plain `0:v:0` stream specifier, so the caller
    passes it straight to `-map` either way.
    """
    chains: list[str] = []
    notes: list[str] = []
    video_streams = list(answers.get("video_streams") or [])
    # The base picture is transformed FIRST, so the overlay lands on what
    # the user asked for and a stack is sized against the real result.
    base_chain = composite_base_picture_chain(answers)
    base_source = "[0:v]"
    if base_chain and video_streams:
        chains.append(f"[0:v]{base_chain}[cmpbase]")
        base_source = "[cmpbase]"
    base_w, base_h = composite_base_output_size(answers)
    mode = str(answers.get("composite_mode") or "") if picture_input is not None else ""

    video_label = "0:v:0" if video_streams else None
    if base_source != "[0:v]" and video_streams:
        video_label = base_source

    if mode in {"overlay", "pip"}:
        corner = str(answers.get("composite_corner") or COMPOSITE_DEFAULT_CORNER)
        x_expr, y_expr = OVERLAY_CORNERS.get(corner, OVERLAY_CORNERS[COMPOSITE_DEFAULT_CORNER])
        margin = max(0, int(answers.get("composite_margin", COMPOSITE_DEFAULT_MARGIN)))
        prepared: list[str] = []
        if mode == "pip":
            fraction = float(answers.get("composite_scale") or COMPOSITE_DEFAULT_PIP_SCALE)
            inset_w = _even(base_w * fraction)
            # -2 rather than a computed height: the inset keeps its own aspect
            # ratio instead of being squeezed into the main picture's.
            prepared.append(f"scale={inset_w}:-2")
            notes.append(f"Picture-in-picture: the second video was scaled to "
                         f"{inset_w} px wide ({fraction:g} of the main picture).")
        opacity = answers.get("composite_opacity")
        if opacity is not None and float(opacity) < 1.0:
            # format=rgba first: a source with no alpha channel has nothing for
            # colorchannelmixer to scale, and the overlay comes out opaque.
            prepared.append(f"format=rgba,colorchannelmixer=aa={float(opacity):g}")
        if prepared:
            chains.append(f"[{picture_input}:v]" + ",".join(prepared) + "[cmpsrc]")
            source = "[cmpsrc]"
        else:
            source = f"[{picture_input}:v]"
        # eof_action=repeat is what lets a still image be a watermark: the
        # single decoded frame is held for the whole main input instead of
        # ending the output with it.
        chains.append(
            f"{base_source}{source}overlay={x_expr.format(m=margin)}:{y_expr.format(m=margin)}"
            f":eof_action=repeat,format={pix_fmt}[vout]")
        video_label = "[vout]"
    elif mode in {"hstack", "vstack"}:
        if mode == "hstack":
            partner_scale = f"scale=-2:{base_h}"
            notes.append(f"Side by side: the second input was scaled to {base_h} px tall "
                         f"to match the first. Its width follows its own aspect ratio, "
                         f"so nothing is letterboxed or squeezed.")
        else:
            partner_scale = f"scale={base_w}:-2"
            notes.append(f"Stacked: the second input was scaled to {base_w} px wide "
                         f"to match the first. Its height follows its own aspect ratio, "
                         f"so nothing is letterboxed or squeezed.")
        chains.append(f"{base_source}scale={base_w}:{base_h},setsar=1,format={pix_fmt}[cmpa]")
        chains.append(f"[{picture_input}:v]{partner_scale},setsar=1,format={pix_fmt}[cmpb]")
        chains.append(f"[cmpa][cmpb]{mode}=inputs=2[vout]")
        video_label = "[vout]"

    audio_streams = list(answers.get("audio_streams") or [])
    selected = (selected_audio_streams(answers) if "audio_tracks" in answers
                else list(range(len(audio_streams))))
    selected = [idx for idx in selected if idx < len(audio_streams)]
    audio_label = f"0:a:{selected[0]}" if selected else None
    if len(selected) > 1:
        notes.append("A composite output carries one audio stream; "
                     f"track {selected[0]} was used and the rest were dropped.")
    if audio_input is not None:
        weight = float(answers.get("composite_audio_weight", COMPOSITE_DEFAULT_MIX_WEIGHT))
        if audio_label:
            # duration=first, NOT -shortest. `-shortest` measures every input,
            # so a 3 s music bed truncated the whole output to 3 s -- the same
            # trap an .srt input sprang on the reverse work. duration=first
            # ends the mix with the MAIN audio and lets a short bed simply stop.
            # normalize=0 so the weights mean what they say: the first source
            # keeps its own level and the second plays at `weight` of its own.
            chains.append(
                f"[{audio_label}][{audio_input}:a:0]"
                f"amix=inputs=2:duration=first:weights=1 {weight:g}:normalize=0[aout]")
            audio_label = "[aout]"
        else:
            audio_label = f"{audio_input}:a:0"
            notes.append("The main input has no audio, so the second file's "
                         "audio is used on its own.")
    # Speed and the fade run on the finished picture -- see
    # `composite_tail_picture_chain` for why they are not in the base chain.
    tail_parts = composite_tail_picture_chain(answers)
    if tail_parts and video_label:
        chains.append(f"{_graph_ref(video_label)}{','.join(tail_parts)}[vfinal]")
        video_label = "[vfinal]"

    # `audio_transform_enabled` is the gate the audio chain is defined
    # against, so it covers everything the chain can emit -- volume,
    # LoudNorm, speed/reverse and the fade. A narrower one here would drop
    # whichever of them it forgot to name.
    if audio_label and audio_transform_enabled(answers):
        chains.append(f"{_graph_ref(audio_label)}"
                      f"{build_encode_audio_processing_filter(answers)}[afinal]")
        audio_label = "[afinal]"

    return chains, video_label, audio_label, notes


def composite_unsupported_answer_notes(answers: dict[str, Any]) -> list[str]:
    """What a composite job asks for that this command still cannot carry.

    Said out loud rather than dropped, on the same rule as
    `gif_unsupported_answer_notes`. The picture and audio chains ARE carried
    now -- `composite_base_picture_chain` runs in front of the overlay and
    `composite_tail_picture_chain` plus the audio chain run behind it -- so the
    only thing left is the one that would change how many outputs exist.
    """
    notes: list[str] = []
    if answers.get("cut_keep_ranges") or answers.get("separator_points"):
        notes.append(
            "Compositing writes ONE output from the whole timeline, so cut "
            "ranges and Split points are NOT applied to it.")
    return notes


def build_composite_command(answers: dict[str, Any], output_path: Path) -> list[str]:
    """One FFmpeg command that composites the extra input(s) onto the first.

    Shaped like `build_join_encode_command`: it owns the whole command because
    the graph it needs -- several `-i` entries feeding one `-filter_complex` --
    is not something the single-input encode path can express.
    """
    picture_path = answers.get("composite_path")
    audio_path = answers.get("composite_audio_path")
    if not (picture_path or audio_path):
        raise ValueError("build_composite_command needs a second input")
    extra_paths = [Path(p) for p in (picture_path, audio_path) if p]
    output_path = resolve_output_collision_against_inputs(
        output_path, extra_paths + [Path(answers["input_path"])],
        answers.get("output_collision_suffix", "_Encode"))
    answers["output_path"] = output_path
    # A build is a PLAN BOUNDARY, and the lease has to exist before the shallow
    # copy below, or everything the copy registers leaks (see the join builder).
    require_plan_revision(answers)
    artifact_lease(answers)
    effective_settings(answers)

    job = dict(answers)
    video_encoder, tag, profile = resolve_video_encoder(job)
    if video_encoder == "copy":
        # Compositing is a filter graph; there is nothing to copy through it.
        job["video_codec"] = DEFAULT_VIDEO_CODEC
        video_encoder, tag, profile = resolve_video_encoder(job)
        effective_settings(answers)["video_codec"] = video_encoder
        appio.note("Video copy cannot be used while compositing; "
                   f"the result is re-encoded with {video_encoder}.")

    cmd: list[str] = [job["ffmpeg"], "-hide_banner", "-y" if OVERWRITE_OUTPUT else "-n",
                      "-i", str(answers["input_path"])]
    next_input = 1
    picture_input = audio_input = None
    if picture_path:
        cmd.extend(["-i", str(picture_path)])
        picture_input = next_input
        next_input += 1
    if audio_path:
        cmd.extend(["-i", str(audio_path)])
        audio_input = next_input
        next_input += 1

    chains, video_label, audio_label, notes = build_composite_filter_graph(
        job, picture_input, audio_input,
        cpu_graph_pixel_format_for_encoder(job, video_encoder))
    for line in notes + composite_unsupported_answer_notes(job):
        appio.note(line)
    if chains:
        cmd.extend(["-filter_complex", ";".join(chains)])
    if video_label:
        cmd.extend(["-map", video_label])
    if audio_label:
        cmd.extend(["-map", audio_label])
    if video_label:
        wizard_base.append_video_encode_options(cmd, job, video_encoder, tag, profile)
    else:
        cmd.append("-vn")
    append_audio_encode_options(cmd, job, bool(audio_label))
    # The output is exactly as long as the FIRST input. `-shortest` would
    # instead take the shortest of all of them, which is how a 3 s logo or
    # music bed silently truncates a 2 minute film.
    try:
        base_seconds = float((answers.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        base_seconds = 0.0
    if base_seconds > 0 and not video_speed_transform_enabled(job):
        # Skipped when the speed changed: `base_seconds` measures the SOURCE,
        # and a 2x composite is half that long, so the limit would be a lie
        # rather than a guard. The overlay's `eof_action=repeat` and the
        # mix's `duration=first` already end the output with the main input.
        cmd.extend(["-t", f"{base_seconds:.3f}"])
    if video_speed_transform_enabled(job):
        # The retiming filter alone is not enough: without this the muxer
        # resamples back to the source cadence and drops the frames the
        # `setpts` just spread out (measured 40 -> 22 at 2x on the encode
        # path). Every builder that emits the filter carries this option;
        # `test_speed_frame_retention` is the guard that says so.
        cmd.extend(VIDEO_SPEED_OUTPUT_TIMING_ARGS)
    append_container_options(cmd, job["output_ext"])
    # Last, immediately before the output path -- the same rule
    # `build_ffmpeg_command` follows, so the escape hatch still overrides what
    # the wizard chose. This builder owns the whole command, so without this
    # line a composite dropped the raw options the wizard had just asked for.
    cmd.extend(job.get("raw_ffmpeg_args") or [])
    cmd.append(str(output_path))
    log_info(f"Composite command built: mode={answers.get('composite_mode') or 'none'}; "
             f"picture_input={picture_input}; audio_input={audio_input}")
    return cmd


# ---------------------------------------------------------------------------
# Quick outputs: the argv pieces. `wizard_build.build_quick_output_stages`
# orchestrates them, because assembling a multi-stage job needs
# `build_ffmpeg_command` and this module must not import the facade that
# re-exports it.
# ---------------------------------------------------------------------------

QUICK_OUTPUT_MODES = ("gif", "boomerang", "thumbnail")


def quick_output_mode(answers: dict[str, Any]) -> str:
    """Which quick output this job is, or "" for an ordinary encode.

    A `.gif` asked for at the FORMAT question counts too. The palette pass is
    not an optional extra on top of a GIF: `-c:v gif` without one quantises
    every frame against the same fixed 216-colour palette and bands visibly, so
    the container alone is enough to select the pipeline. Deciding it anywhere
    else would leave a second, worse way to ask for the same output.
    """
    mode = str(answers.get("quick_output") or "").strip().lower()
    if mode in QUICK_OUTPUT_MODES:
        return mode
    if str(answers.get("output_ext") or "").strip().lower() == "gif":
        return "gif"
    return ""


def gif_frame_rate(answers: dict[str, Any]) -> float:
    """The GIF frame rate: the explicit answer, then the job's fps, then the default."""
    for value in (answers.get("gif_fps"), answers.get("fps")):
        try:
            rate = float(value)
        except (TypeError, ValueError):
            continue
        if rate > 0:
            return rate
    return float(GIF_DEFAULT_FPS)


def gif_target_width(answers: dict[str, Any]) -> int:
    """The GIF width: the explicit answer, then the resize answer, then the default."""
    candidates = [answers.get("gif_width")]
    resolution = answers.get("resolution")
    if isinstance(resolution, dict):
        candidates.append(resolution.get("width"))
    for value in candidates:
        try:
            width = int(value)
        except (TypeError, ValueError):
            continue
        if width > 0:
            return width
    return int(GIF_DEFAULT_WIDTH)


def gif_scale_chain(answers: dict[str, Any]) -> str:
    """The filter chain BOTH passes must share.

    Both passes really do have to be identical. The palette is built from the
    frames the second pass will quantise; generate it from a different size or
    a different frame rate and `paletteuse` is matching colours that its input
    never contains.
    """
    return (f"fps={gif_frame_rate(answers):g},"
            f"scale={gif_target_width(answers)}:-1:flags=lanczos")


def gif_input_options(answers: dict[str, Any]) -> list[str]:
    """Input options BOTH GIF passes must carry, in front of their `-i`.

    The two passes have to read the same frames. A `-ss` on the write pass that
    the palette pass did not have would quantise one part of the clip against
    another part's colours, which shows up as banding rather than as an error.
    """
    options: list[str] = []
    append_stream_loop(options, answers)
    ranges = list(answers.get("cut_keep_ranges") or [])
    if len(ranges) == 1:
        start, end = ranges[0]
        # The range is on the PICTURE clock and `-ss` counts from the container;
        # the main builder applies the same correction for the same reason.
        seek = float(start) + picture_clock_offset(answers)
        if seek > 0:
            options.extend(["-ss", f"{seek:.6f}"])
        options.extend(["-t", f"{max(0.0, float(end) - float(start)):.6f}"])
    return options


def gif_unsupported_answer_notes(answers: dict[str, Any]) -> list[str]:
    """What a GIF job asks for that the two palette passes cannot carry.

    Said out loud rather than dropped. A multi-range cut is built as a
    `filter_complex` with trim/concat, and this path has a `-vf`/`-lavfi` chain
    with one input; a `reverse` in that chain is accepted but buffers every
    decoded frame, which is the cost the staged pipeline exists to avoid.
    """
    notes: list[str] = []
    if len(list(answers.get("cut_keep_ranges") or [])) > 1:
        notes.append(
            "GIF output keeps the whole clip: multiple cut ranges are built as a "
            "trim/concat graph the palette passes cannot carry. Ask for one range, "
            "or encode to a video container first.")
    if answers.get("reverse_video"):
        notes.append(
            "A reversed GIF buffers every decoded frame, because `reverse` cannot "
            "emit one until it has read them all. Keep the clip short.")
    return notes


def append_stream_loop(cmd: list[str], answers: dict[str, Any]) -> None:
    """`-stream_loop N`, which is an INPUT option: it must precede its `-i`.

    Placed after the `-i` it is accepted and ignored, so the job runs, exits 0
    and writes an output of exactly the original length -- the same class of
    silent wrongness `-ss` and `-t` have on the wrong side of the input, which
    this codebase has already paid for twice.
    """
    try:
        count = int(answers.get("loop_count") or 0)
    except (TypeError, ValueError):
        return
    if count >= 1:
        cmd.extend(["-stream_loop", str(count)])


def build_thumbnail_command(answers: dict[str, Any], source: Path,
                            output_path: Path) -> list[str]:
    """One frame at one time, as an image.

    `-ss` before `-i` on purpose: as an OUTPUT option it would decode every
    frame up to that point and then throw them away, which on a long file is
    the difference between instant and minutes.
    """
    try:
        at_seconds = max(0.0, float(answers.get("thumbnail_seconds") or 0.0))
    except (TypeError, ValueError):
        at_seconds = 0.0
    return [str(answers["ffmpeg"]), "-y" if OVERWRITE_OUTPUT else "-n",
            "-hide_banner", "-ss", f"{at_seconds:.6f}", "-i", str(source),
            "-frames:v", "1", "-update", "1", str(output_path)]

__all__ = [
    'composite_active',
    'build_composite_filter_graph',
    'composite_unsupported_answer_notes',
    'build_composite_command',
    'QUICK_OUTPUT_MODES',
    'quick_output_mode',
    'gif_frame_rate',
    'gif_target_width',
    'gif_scale_chain',
    'gif_input_options',
    'gif_unsupported_answer_notes',
    'append_stream_loop',
    'build_thumbnail_command',
]


# wizard_base holds the encode-option builders this module calls. It is a leaf:
# it imports neither wizard nor wizard_build, so nothing here can re-enter a
# facade that is still merging its __all__.
from ffmwiz import wizard_base  # noqa: E402,F401
