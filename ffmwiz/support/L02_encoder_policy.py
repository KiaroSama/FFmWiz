"""How a job encodes: the CPU two-pass and CUDA fast-path decisions.

Split out of `L02` when that file reached the size ceiling, along a boundary it
already had. L02 answers what the wizard should ASK and where output should GO;
these six answer how the encode should RUN -- whether a two-pass is applicable
and, when it is not, the exact reason to show; whether the source and the
requested work allow the CUDA fast path at all; whether a graph too complex for
that path should still decode on the GPU; and the two commands a two-pass run
is actually made of.

Imports only tiers below L02, so `L02` re-exports it and nothing here reaches
back up into `L02`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ffmwiz.core.artifacts import effective_settings
from ffmwiz.support.L00_encode_opts import (cpu_two_pass_video_output_args,
                                            encoder_supports_two_pass,
                                            resolve_video_encoder)
from ffmwiz.support.L00_filters import has_crop
from ffmwiz.support.L00_misc import video_speed_transform_enabled
from ffmwiz.support.L00_misc_b import ffmpeg_input_section_end
from ffmwiz.support.L01_encode_opts import cpu_two_pass_log_prefix
from ffmwiz.support.L01_filters import (look_filters_requested,
                                        requested_fade_seconds,
                                        video_filters_required)
from ffmwiz.support.L01_misc import (_cuda_fast_path_needs_ar_padding,
                                     cuda_decoder_for_source, output_has_video)


def cpu_two_pass_applicable(answers: dict[str, Any]) -> bool:
    if not output_has_video(answers) or answers.get("use_gpu"):
        return False
    if str(answers.get("video_codec") or "").strip().lower() in {"copy", "n"}:
        return False
    if answers.get("join_input_items") or answers.get("separator_points") or answers.get("split_output_paths"):
        return False
    if answers.get("cut_keep_ranges") or answers.get("video_speed_enabled") or answers.get("reverse_video"):
        return False
    video_encoder, _tag, _profile = resolve_video_encoder(answers)
    if not encoder_supports_two_pass(video_encoder):
        return False
    return True

def cpu_two_pass_unsupported_reason(answers: dict[str, Any]) -> str:
    """Why this job cannot run CPU two-pass, in the user's terms."""
    if answers.get("join_input_items"):
        return "a join builds its video through filter_complex, which pass 1 cannot analyse"
    if answers.get("separator_points") or answers.get("split_output_paths"):
        return "a Split writes several outputs from one graph, so one stats file cannot describe them"
    if answers.get("reverse_video"):
        return "reverse is encoded in bounded segments, and a stats file cannot span them"
    if answers.get("cut_keep_ranges") or answers.get("video_speed_enabled"):
        return "cuts and speed rebuild the video timeline, so pass 1 would measure a different one"
    if answers.get("use_gpu"):
        return "the GPU encoder has its own rate-control passes"
    if str(answers.get("video_codec") or "").strip().lower() in {"copy", "n"}:
        return "the video is stream-copied, so there is nothing to encode twice"
    return "this encoder or output does not support it"

def normalize_cpu_two_pass_selection(answers: dict[str, Any]) -> str:
    """Reconcile a retained `cpu_two_pass` with what this job can really do.

    The wizard hides the question for a join, a Split, cuts, speed and reverse,
    but a config file carries `cpu_two_pass=y` straight past that. Nothing
    re-checked it once the job was known, so a two-input Join really did run
    `build_cpu_two_pass_commands`: pass 1 had no `-filter_complex` and a
    hardcoded `-map 0:v:0`, and pass 2 died with "Incomplete MB-tree stats
    file" (exit 187). On the segmented-reverse and per-part Split executors the
    runner is called directly, so the same retained flag was silently downgraded
    to a single pass while the summary still reported "CPU two-pass: yes" (F11).

    Returns "" when nothing changed, otherwise the reason it was turned off.
    """
    if not answers.get("cpu_two_pass"):
        return ""
    if cpu_two_pass_applicable(answers):
        return ""
    reason = cpu_two_pass_unsupported_reason(answers)
    answers["cpu_two_pass"] = False
    effective_settings(answers)["cpu_two_pass"] = False
    # Pure by design: this layer is below appio, and returning the reason keeps
    # the notice with the caller that owns the user's screen. A second call
    # returns "" because the flag is already off, so the message appears once.
    return reason

def can_use_cuda_fast_path(answers: dict[str, Any], video_encoder: str | None) -> bool:
    cut_ranges = list(answers.get("cut_keep_ranges") or [])
    if has_crop(answers) and not cuda_decoder_for_source(answers):
        return False
    # AR-preserving resize with padding cannot be done purely in CUDA; fall back
    # to the complex graph path so CPU scale+pad filters are used with NVENC encode.
    if _cuda_fast_path_needs_ar_padding(answers):
        return False
    return bool(
        answers.get("use_gpu")
        and output_has_video(answers)
        and video_encoder
        and video_encoder != "copy"
        and str(video_encoder).endswith("_nvenc")
        and not answers.get("_hardsub_mode")
        and len(cut_ranges) <= 1
        and not answers.get("_force_cpu_video_filter")
        and not answers.get("separator_points")
        and not video_speed_transform_enabled(answers)
        # Everything the CUDA path CANNOT express. `build_cuda_video_filter`
        # emits exactly one filter -- `scale_cuda` -- so a job that also asked
        # for a rotate/flip, a colour adjustment or denoise/sharpen/blur took
        # this path and lost it silently: measured on rotate, grayscale and
        # denoise, the whole video chain was `scale_cuda=...` and nothing else.
        # A fade was worse than lost -- `afade` was still emitted, so the sound
        # faded over a picture that did not.
        #
        # NOT `not video_filters_required(answers)`: that also covers crop and
        # resize, which this path DOES handle (decoder crop + scale_cuda), so
        # rejecting on it would disable the GPU path for the jobs it exists for.
        # `fps` is not here either: it is applied as the `-r:v` OUTPUT option,
        # which needs no filter and works on CUDA frames.
        and not look_filters_requested(answers)
        and not any(requested_fade_seconds(answers))
    )

def should_use_cuda_decode_for_complex_graph(
    answers: dict[str, Any],
    video_encoder: str | None,
    using_cuda_fast_path: bool,
) -> bool:
    if using_cuda_fast_path:
        return False
    if not (
        answers.get("use_gpu")
        and output_has_video(answers)
        and video_encoder
        and video_encoder != "copy"
        and str(video_encoder).endswith("_nvenc")
    ):
        return False
    cut_ranges = list(answers.get("cut_keep_ranges") or [])
    return bool(
        answers.get("_join_complex_graph")
        or answers.get("_force_cpu_video_filter")
        or len(cut_ranges) > 1
        or video_speed_transform_enabled(answers)
        or video_filters_required(answers)
    )

def build_cpu_two_pass_commands(cmd: list[str], answers: dict[str, Any]) -> tuple[list[str], list[str], Path]:
    if len(cmd) < 2:
        raise ValueError("FFmpeg command is too short for two-pass encoding.")
    passlog = cpu_two_pass_log_prefix(answers)
    input_end = ffmpeg_input_section_end(cmd)
    input_args = list(cmd[:input_end])
    output_args = list(cmd[input_end:-1])
    video_args = cpu_two_pass_video_output_args(output_args)
    first = (
        input_args
        + ["-map", "0:v:0"]
        + video_args
        + ["-pass", "1", "-passlogfile", str(passlog), "-an", "-sn", "-dn", "-f", "null", os.devnull]
    )
    second = list(cmd[:-1]) + ["-pass", "2", "-passlogfile", str(passlog), str(cmd[-1])]
    return first, second, passlog


__all__ = [
    'cpu_two_pass_applicable',
    'cpu_two_pass_unsupported_reason',
    'normalize_cpu_two_pass_selection',
    'can_use_cuda_fast_path',
    'should_use_cuda_decode_for_complex_graph',
    'build_cpu_two_pass_commands',
]
