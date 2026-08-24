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

from ffmwiz import wizard  # facade for monkeypatch-stable cross-module calls  # noqa: F401
from ffmwiz.wizard import *  # sibling helpers  # noqa: F401,F403


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
    return build_cuda_video_filter(answers) if use_gpu_filtering else wizard.build_cpu_video_filter(answers)


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
    retimed_subtitles: list[dict[str, Any]] | None = None,
) -> bool:
    source_duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    final_duration = final_processed_duration_for_splits(answers, source_duration)
    split_points = normalize_separator_points(answers.get("separator_points"), final_duration)
    if not split_points:
        return False
    filters: list[str] = []
    audio_labels: list[str] = []
    if multi_cut:
        filters.extend(wizard.build_cut_filter_complex(answers, list(answers.get("cut_keep_ranges") or []), audio_for_cut).split(";"))
        video_label = "v"
        if audio_for_cut is not None:
            audio_labels.append("a")
    else:
        video_filter = wizard.build_cpu_video_filter(answers) or "null"
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
                    # Leased, not just keyed: every Split part is rebuilt from a
                    # SHALLOW COPY of answers, so a key written here dies with
                    # the copy and the directory outlived the run (R06).
                    chapter_temp_dir = str(artifact_lease(answers).register(
                        Path(tempfile.mkdtemp(prefix="ffmwiz_split_chapters_"))))
                    answers["_chapter_metadata_temp_dir"] = chapter_temp_dir
                metadata_path = write_encode_chapter_metadata(plan, Path(chapter_temp_dir), suffix=f"_part{part_idx + 1:02d}")
                split_chapter_metadata_paths.append(metadata_path)
            else:
                split_chapter_metadata_paths.append(None)

    # Add chapter metadata inputs (input index 1, 2, ... for each part that has chapters).
    # Not a literal 1: the caller adds the retimed-subtitle inputs before this
    # runs, so a hardcoded base pointed -map_chapters at an .srt and gave every
    # part the PREVIOUS part's chapters. Count what is already there instead.
    chapter_input_base = sum(1 for arg in cmd if arg == "-i")
    metadata_input_count = 0
    part_chapter_input_indices: list[int | None] = []
    for metadata_path in split_chapter_metadata_paths:
        if metadata_path is not None:
            cmd.extend(["-i", str(metadata_path)])
            part_chapter_input_indices.append(chapter_input_base + metadata_input_count)
            metadata_input_count += 1
        else:
            part_chapter_input_indices.append(None)

    # Slice the subtitles onto each part's own clock and map them. Without this
    # the Split path emitted -sn: a cut/speed Split built and announced a
    # retimed track that no output ever carried.
    split_subtitles = wizard.build_split_subtitle_inputs(
        answers, split_intervals, retimed_subtitles)
    subtitle_input_indices: list[list[int]] = []
    next_input_index = chapter_input_base + metadata_input_count
    for part_tracks in split_subtitles:
        indices: list[int] = []
        for track in part_tracks:
            cmd.extend(["-i", str(track["path"])])
            indices.append(next_input_index)
            next_input_index += 1
        subtitle_input_indices.append(indices)

    cmd.extend(["-filter_complex", ";".join(filters)])
    for part_idx, part_output in enumerate(output_paths):
        cmd.extend(["-map", f"[{video_outputs[part_idx]}]"])
        for audio_label in audio_outputs_by_part[part_idx]:
            cmd.extend(["-map", f"[{audio_label}]"])
        part_subtitles = (split_subtitles[part_idx]
                          if part_idx < len(split_subtitles) else [])
        for subtitle_input in (subtitle_input_indices[part_idx]
                               if part_idx < len(subtitle_input_indices) else []):
            cmd.extend(["-map", f"{subtitle_input}:s:0"])
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

        append_negative_stream_options(
            cmd, answers, True,
            list(range(len(part_subtitles))) if part_subtitles else [],
            data_mapped)
        wizard.append_video_encode_options(cmd, answers, video_encoder, tag, profile)
        append_audio_encode_options(cmd, answers, bool(audio_outputs_by_part[part_idx]))
        append_clear_reencoded_stream_stat_metadata(
            cmd,
            answers,
            video_output_count=1 if video_encoder != "copy" else 0,
            audio_output_count=len(audio_outputs_by_part[part_idx]) if audio_outputs_by_part[part_idx] else 0,
            subtitle_output_count=len(part_subtitles),
        )
        if part_subtitles:
            # Sliced tracks are freshly written SRT whatever the source was, so
            # ask the container about subrip -- the same check the non-split
            # path makes before mapping anything.
            subtitle_args, subtitle_problems = subtitle_codec_args_for_container(
                answers["output_ext"], ["subrip"] * len(part_subtitles))
            for problem in subtitle_problems:
                log_warn(f"Subtitle/container: {problem}")
            if subtitle_args:
                cmd.extend(subtitle_args)
            append_subtitle_track_metadata(cmd, part_subtitles)
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
    # Open the lease FIRST. `dict()` copies the key but shares the object, so a
    # lease opened here is still the outer job's lease when a Split part or a
    # reverse segment rebuilds from `dict(answers)`; opened later, the copy gets
    # one of its own and everything it registers leaks.
    artifact_lease(answers)
    # Before the command exists, so the user is told while the settings summary
    # and the command still agree: a config-retained cpu_two_pass=y reaches a
    # Join/Split/cut/speed/reverse job the wizard would never have offered it
    # for, and either fails at pass 2 or is silently run as one pass (F11).
    _two_pass_off = normalize_cpu_two_pass_selection(answers)
    if _two_pass_off:
        appio.note(f"CPU two-pass was turned off for this job: {_two_pass_off}.")
        log_info(f"CPU two-pass disabled before execution: {_two_pass_off}")
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
        # -t belongs to the SOURCE INPUT, not to the output. As an output option
        # it only truncates what the graph already produced, which breaks any
        # filter that must consume its whole input first: `reverse` read the
        # ENTIRE file and -t then kept the wrong end of the reversed result
        # (F01) -- keeping 0-2s of a red/blue clip returned blue. It also broke
        # slow motion, where a 4 s window at 0.5x wants an 8 s output and an
        # output -t 4 cut it back to 4 s. Placed here it binds to the source
        # only; the auxiliary chapter/subtitle inputs come later and are
        # unaffected, which is what the old "-t after every -i" note was about.
        cmd.extend(["-t", f"{max(0.0, end - start):.6f}"])

    cmd.extend(["-i", str(input_path)])

    # Chapter remapping: if timeline is modified and source has chapters,
    # generate a metadata file and add it as a second input so -map_chapters
    # can reference it. The metadata file path is stored for later cleanup.
    chapter_metadata_inputs = 0
    if (
        has_video
        and source_chapters_keep_enabled(answers)
        and timeline_is_modified(answers)
        and not answers.get("separator_points")  # Split path handles its own chapters.
    ):
        speed_factor = encode_video_speed_factor(answers) if video_speed_transform_enabled(answers) else 1.0
        chapter_plan = remap_chapters_for_encode(answers, speed_factor=speed_factor)
        if chapter_plan.get("mode") == "metadata" and chapter_plan.get("chapters"):
            chapter_temp_dir = str(artifact_lease(answers).register(
                Path(tempfile.mkdtemp(prefix="ffmwiz_encode_chapters_"))))
            answers["_chapter_metadata_temp_dir"] = chapter_temp_dir
            metadata_path = write_encode_chapter_metadata(chapter_plan, Path(chapter_temp_dir))
            cmd.extend(["-i", str(metadata_path)])
            answers["_chapter_metadata_input_index"] = 1
            chapter_metadata_inputs = 1
            log_info(f"Chapters: injected metadata input at index 1 ({metadata_path})")

    # Subtitle packets never enter the video/audio filter graph, so a speed,
    # reverse or multi-range cut left their timestamps on the SOURCE clock: a
    # 2.500-3.500 s cue stayed put in a 2 s 2x output, and the stale packet
    # stretched the container to 3.521 s (R03). The rebuilt tracks arrive as
    # their own inputs, added AFTER the chapter metadata so its index 1 holds.
    subtitle_retiming = encode_subtitle_retiming_required(answers)
    retimed_subtitles = wizard.build_retimed_subtitle_inputs(answers) if subtitle_retiming else []
    retimed_subtitle_base = 1 + chapter_metadata_inputs
    for retimed_track in retimed_subtitles:
        cmd.extend(["-i", str(retimed_track["path"])])

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

    # `-map 0 -c copy` would carry the SOURCE subtitle packets verbatim, which
    # is exactly what retiming exists to prevent.
    full_source_map = not subtitle_retiming and can_use_full_source_map_for_simple_encode(
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
        and wizard.append_single_input_split_outputs(
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
            retimed_subtitles,
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

    subtitle_indices = [] if (full_source_map or subtitle_retiming) else (
        selected_subtitle_streams(answers)
        if has_video and source_subtitles_keep_enabled(answers) and answers.get("subtitle_streams")
        else []
    )
    if multi_cut and subtitle_indices:
        appio.note("Cuts active: subtitle streams are not mapped through filter_complex and were skipped.")
        subtitle_indices = []
    if subtitle_indices:
        # A subtitle the target container cannot carry must not be MAPPED either:
        # dropping only `-c:s` still leaves `-map 0:s:N`, and the muxer then
        # refuses to write the header (AVI rejects every subtitle codec).
        allowed_subtitles: list[int] = []
        skipped_subtitles: list[int] = []
        for subtitle_index in subtitle_indices:
            codec = str(answers["subtitle_streams"][subtitle_index].get("codec_name", "")).lower()
            if subtitle_codec_for_container(answers["output_ext"], codec) is None:
                skipped_subtitles.append(subtitle_index)
            else:
                allowed_subtitles.append(subtitle_index)
        if skipped_subtitles:
            appio.note(
                f"Skipped subtitle tracks that .{str(answers['output_ext']).lstrip('.')} "
                f"cannot store: {skipped_subtitles}"
            )
        subtitle_indices = allowed_subtitles
    for subtitle_index in subtitle_indices:
        cmd.extend(["-map", f"0:s:{subtitle_index}"])
    for offset in range(len(retimed_subtitles)):
        cmd.extend(["-map", f"{retimed_subtitle_base + offset}:s:0"])
    attachments_mapped = embedded_attachment_keep_enabled(answers) if full_source_map else append_embedded_attachment_maps(cmd, answers)
    data_mapped = bool(source_data_streams(answers) and source_data_keep_enabled(answers)) if full_source_map else append_source_data_maps(cmd, answers)
    append_source_metadata_chapter_options(cmd, answers)

    if not full_source_map:
        append_negative_stream_options(
            cmd, answers, has_video,
            subtitle_indices or list(range(len(retimed_subtitles))), data_mapped)

    if has_video and video_encoder:
        video_bitrate = answers.get("video_bitrate_kbps")
        if video_encoder == "copy":
            cmd.extend(["-c:v", "copy"])
        else:
            if full_source_map:
                cmd.extend(["-c", "copy"])
            if multi_cut:
                fc = wizard.build_cut_filter_complex(answers, cut_keep_ranges, audio_for_cut)
                cmd.extend(["-filter_complex", fc])
            else:
                video_filter = wizard.build_video_filter(answers, use_gpu_filtering=use_cuda_fast_path)
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
                wizard.append_nvenc_multipass_args(cmd, answers, video_encoder)
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
                    cmd.extend(["-crf", f"{crf_value:g}"])
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
        # Every constrained container, not just WebM: aac into .flac/.ogg/.opus
        # was emitted happily and then rejected by the muxer.
        container_ext = str(answers.get("output_ext", "")).lower().lstrip(".")
        allowed_audio = AUDIO_CODECS_BY_FORMAT.get(container_ext)
        if allowed_audio and audio_codec not in allowed_audio:
            replacement = default_audio_codec_for_ext(container_ext)
            appio.note(
                f".{container_ext} cannot store {audio_codec} audio; "
                f"{replacement} was selected for container compatibility."
            )
            audio_codec = replacement
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
            channels = resolve_audio_channels(answers)
            if channels:
                cmd.extend(["-ac", str(channels)])
            _ar = resolve_audio_sample_rate(answers)
            if _ar:
                cmd.extend(["-ar", str(_ar)])
    else:
        cmd.append("-an")

    output_is_processed = bool(has_video and video_encoder and video_encoder != "copy")
    output_is_processed = output_is_processed or bool(audio_indices and audio_codec_for_stats and audio_codec_for_stats != "copy")
    output_is_processed = output_is_processed or bool(
        (subtitle_indices or retimed_subtitles) and answers["output_ext"].lower() in MP4_LIKE_EXTS)
    if output_is_processed:
        video_output_count_for_stats = 0
        if has_video:
            video_output_count_for_stats = len(answers.get("video_streams") or []) if full_source_map else 1 + extra_video_count
        audio_output_count_for_stats = 0
        if audio_indices:
            audio_output_count_for_stats = len(answers.get("audio_streams") or []) if full_source_map else len(audio_indices)
        subtitle_output_count_for_stats = (
            len(answers.get("subtitle_streams") or []) if full_source_map
            else len(subtitle_indices) + len(retimed_subtitles))
        append_clear_reencoded_stream_stat_metadata(
            cmd,
            answers,
            video_output_count=video_output_count_for_stats,
            audio_output_count=audio_output_count_for_stats,
            subtitle_output_count=subtitle_output_count_for_stats,
        )

    if subtitle_indices or retimed_subtitles:
        # Ask the container what it can carry instead of assuming "copy works
        # everywhere except MP4". mov_text into mkv, subrip into webm and any
        # subtitle into avi are all rejected by the muxer at header-write time.
        source_subtitles = answers.get("subtitle_streams") or []
        source_codecs = [
            str((source_subtitles[index] if index < len(source_subtitles) else {}).get("codec_name") or "")
            for index in subtitle_indices
        ]
        # A retimed track is a freshly written SRT, whatever the source codec was.
        source_codecs.extend(["subrip"] * len(retimed_subtitles))
        subtitle_args, subtitle_problems = subtitle_codec_args_for_container(
            answers["output_ext"], source_codecs
        )
        for problem in subtitle_problems:
            log_warn(f"Subtitle/container: {problem}")
        if subtitle_args:
            cmd.extend(subtitle_args)
        append_subtitle_track_metadata(cmd, retimed_subtitles)
    if attachments_mapped:
        append_embedded_attachment_codec_options(cmd, answers)
    if data_mapped:
        append_source_data_codec_options(cmd, answers)

    if answers["output_ext"].lower() in MP4_LIKE_EXTS and MOVFLAGS:
        cmd.extend(["-movflags", MOVFLAGS])

    cmd.append(str(output_path))
    return cmd


__all__ = [
    'append_single_input_split_outputs',
    'build_cpu_video_filter',
    'build_ffmpeg_command',
    'build_video_filter',
]


# wizard_build_b holds an overflow slice of this module (split for file size).
from ffmwiz import wizard_build_b as _wizard_build_b  # noqa: E402
from ffmwiz.wizard_build_b import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_wizard_build_b.__all__)
