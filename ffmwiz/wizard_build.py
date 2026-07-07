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
        wizard.append_video_encode_options(cmd, answers, video_encoder, tag, profile)
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
    cmd.extend(["-filter:v", wizard.build_hardsub_video_filter(answers, video_encoder)])
    cmd.extend(["-c:v", video_encoder])
    append_hardsub_quality_args(cmd, answers, video_encoder)
    wizard.append_nvenc_multipass_args(cmd, answers, video_encoder)
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
    user_video_filter = wizard.build_cpu_video_filter(answers)
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
        wizard.append_video_encode_options(cmd, join_answers, video_encoder, tag, profile)
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
    'append_single_input_split_outputs',
    'build_cpu_video_filter',
    'build_cut_filter_complex',
    'build_ffmpeg_command',
    'build_hardsub_command',
    'build_hardsub_video_filter',
    'build_join_encode_command',
    'build_video_filter',
]
