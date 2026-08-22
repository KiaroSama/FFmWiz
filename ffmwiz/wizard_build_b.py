"""FFmWiz wizard_build overflow (wizard_build_b) — split for file size.

Back-imports wizard_build and is re-exported by it, so every consumer of
`from ffmwiz.wizard_build import *` still sees the full set. Monkeypatch-safe.
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
from ffmwiz.wizard_build import *  # noqa: E402,F401,F403  (back-import)
from ffmwiz import wizard_build  # noqa: E402,F401  (qualified self-ref for patched names)


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

    # The container, the video codec and the audio policy are three independent
    # questions, so nothing stopped H.264 + copied AAC landing in a .webm and
    # dying at header-write time. Reconcile the codec with the container the
    # same way the main wizard does, before resolving the encoder.
    requested_codec = answers.get("video_codec", DEFAULT_VIDEO_CODEC)
    container_codec, codec_note = container_video_codec(output_ext, requested_codec)
    if codec_note:
        appio.note(codec_note)
        answers["video_codec"] = container_codec
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
            audio_args, audio_note = container_audio_encode_args(output_ext, "aac", bitrate, AUDIO_CHANNELS)
        else:
            first_audio = (answers.get("audio_streams") or [{}])[0]
            audio_args, audio_note = container_audio_encode_args(
                output_ext, "copy", None,
                source_codec=str(first_audio.get("codec_name") or "") or None)
        if audio_note:
            appio.note(audio_note)
        cmd.extend(audio_args)
    else:
        cmd.extend(["-map", "0:a?"])
        mapped_audio_output_count = len(answers.get("audio_streams") or [])
        if audio_policy == "aac":
            bitrate = int(answers.get("hardsub_audio_bitrate_kbps") or DEFAULT_AUDIO_BITRATE_KBPS)
            audio_args, audio_note = container_audio_encode_args(output_ext, "aac", bitrate, AUDIO_CHANNELS)
        else:
            first_audio = (answers.get("audio_streams") or [{}])[0]
            audio_args, audio_note = container_audio_encode_args(
                output_ext, "copy", None,
                source_codec=str(first_audio.get("codec_name") or "") or None)
        if audio_note:
            appio.note(audio_note)
        cmd.extend(audio_args)

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


def join_extras_outcome_notes(answers: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    """What the joined re-encode really does with each "keep source extras"
    category, one line per category.

    One yes/no covers metadata, chapters, extra video, subtitles, data streams
    and attachments, but the join command maps metadata/data/attachments from
    input 1 alone and drops chapters, subtitles and extra video outright.
    Stating each outcome before the confirmation is what stops the question
    from promising what the command discards.
    """
    first = Path(items[0]["path"]).name if items and items[0].get("path") else "input 1"
    lines: list[str] = []
    if source_metadata_keep_enabled(answers):
        lines.append(f"Join extras -- Metadata: copied from {first} only; the other inputs contribute none.")
    if source_chapters_keep_enabled(answers):
        lines.append("Join extras -- Chapters: dropped; a joined timeline cannot reuse the source chapter times.")
    if answers.get("keep_embedded_attachments") and embedded_attachment_streams(answers):
        lines.append(f"Join extras -- Attachments/fonts: taken from {first} only.")
    if source_data_keep_enabled(answers) and source_data_streams(answers):
        lines.append(f"Join extras -- Data streams: taken from {first} only.")
    if source_extra_video_keep_enabled(answers) and additional_source_video_streams(answers):
        lines.append("Join extras -- Extra video streams: dropped; the join graph produces one video stream.")
    if answers.get("subtitle_tracks") or (source_subtitles_keep_enabled(answers) and answers.get("subtitle_streams")):
        plan = join_subtitle_plan(answers, items)
        if plan.get("supported"):
            carried = sum(1 for _i, stream, _d in plan["segments"] if stream is not None)
            lines.append(
                f"Join extras -- Subtitles: one merged text track from {carried} of "
                f"{len(plan['segments'])} input(s), shifted onto the joined timeline.")
        else:
            lines.append(f"Join extras -- Subtitles: dropped - {plan.get('reason') or 'unavailable'}.")
    return lines


def build_joined_subtitle_file(answers: dict[str, Any], items: list[dict[str, Any]]) -> Path | None:
    """Extract, shift and merge each input's text subtitle into one SRT.

    Returns the merged file, or None when the join cannot carry subtitles -- in
    which case the reason is logged and shown, because the wizard asked the user
    about subtitles and owes them an answer either way.

    The temp directory is recorded on `answers` so the existing cleanup path
    removes it with the rest of the join scratch files.
    """
    plan = join_subtitle_plan(answers, items)
    if not plan.get("supported"):
        reason = plan.get("reason") or "unavailable"
        if reason != "no subtitle track was selected":
            appio.note(f"Joined subtitles: not assembled - {reason}.")
            log_info(f"Joined subtitles skipped: {reason}")
        return None

    temp_dir = Path(tempfile.mkdtemp(prefix="ffmwiz_join_subs_"))
    answers["_join_subtitle_temp_dir"] = str(temp_dir)
    ffmpeg = answers.get("ffmpeg") or "ffmpeg"
    segments: list[tuple[str, float]] = []
    extracted = 0
    for index, (item, stream, duration) in enumerate(plan["segments"]):
        text = ""
        if stream is not None:
            relative = 0
            for candidate in item.get("subtitle_streams") or []:
                if candidate is stream:
                    break
                relative += 1
            part = temp_dir / f"part{index:02d}.srt"
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                       "-i", str(item["path"]), "-map", f"0:s:{relative}",
                       "-c:s", "srt", str(part)]
            try:
                result = subprocess.run(command, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=300)
                if result.returncode == 0 and part.exists():
                    text = part.read_text(encoding="utf-8", errors="replace")
                    extracted += 1
                else:
                    log_warn(
                        f"Joined subtitles: could not extract from {Path(item['path']).name} "
                        f"(exit {result.returncode})")
            except (OSError, subprocess.SubprocessError) as exc:
                log_warn(f"Joined subtitles: extraction failed for {Path(item['path']).name}: {exc}")
        segments.append((text, duration))

    if not extracted:
        appio.note("Joined subtitles: no cues could be extracted; the output has no subtitle track.")
        return None
    merged_text = merge_joined_srt(segments)
    if not merged_text.strip():
        appio.note("Joined subtitles: the selected tracks contained no cues.")
        return None
    merged = temp_dir / "joined.srt"
    merged.write_text(merged_text, encoding="utf-8", newline="\n")
    log_info(
        f"Joined subtitles: merged {extracted}/{len(plan['segments'])} input track(s) "
        f"into {merged}")
    appio.note(
        f"Joined subtitles: assembled one track from {extracted} input(s) "
        "with each input's cues shifted onto the joined timeline.")
    return merged


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

    # The concat FILTER cannot carry subtitles, so a joined subtitle track has
    # to be assembled separately: each input's cues shifted by that input's
    # start offset, merged, and fed back as one extra input.
    joined_subtitle_path = build_joined_subtitle_file(join_answers, items)
    joined_subtitle_input = None
    if joined_subtitle_path is not None:
        joined_subtitle_input = len(items)
        cmd.extend(["-i", str(joined_subtitle_path)])

    selected_audio = selected_audio_streams(join_answers) if join_answers.get("audio_streams") else []
    if not selected_audio and any(item.get("audio_streams") for item in items):
        # The track question is asked for input 1 only, so a silent input 1 left
        # every LATER input's audio unmapped and unmentioned.
        selected_audio = [0]
        appio.note(
            "Join audio: input 1 has no audio, so track 0 of the other inputs is joined and "
            "input 1's segment is silent."
        )
    # How many tracks the question could reach: input 1's count, or the
    # recovered track above when input 1 was silent.
    offered_tracks = max(
        len(join_answers.get("audio_streams") or []),
        (max(selected_audio) + 1) if selected_audio else 0,
    )
    silenced: list[str] = []
    unreachable: list[str] = []
    for item_pos, item in enumerate(items, start=1):
        audio_count = len(item.get("audio_streams") or [])
        name = Path(item.get("path")).name
        if any(idx >= audio_count for idx in selected_audio):
            silenced.append(f"input {item_pos} ({name})")
        if audio_count > offered_tracks:
            unreachable.append(
                f"input {item_pos} ({name}): track(s) {list(range(offered_tracks, audio_count))}"
            )
    if silenced:
        # The standalone join path has always synthesised silence here; the
        # wizard join used to refuse the very same set of files instead.
        appio.note(
            "Join audio: silence is synthesised for the selected track(s) missing from "
            + ", ".join(silenced) + "."
        )
    if unreachable:
        appio.note(
            f"Join audio: the track question covers input 1's {offered_tracks} track(s), so these "
            "are NOT in the joined output: " + "; ".join(unreachable) + "."
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
    join_rate = join_target_sample_rate(join_answers)
    for input_idx, item in enumerate(items):
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
        item_audio_count = len(item.get("audio_streams") or [])
        for audio_pos, audio_index in enumerate(selected_audio):
            label = f"[ja{input_idx}_{audio_pos}]"
            if audio_index < item_audio_count:
                filters.append(f"[{input_idx}:a:{audio_index}]{join_audio_prep_filter(join_rate)}{label}")
            else:
                silence = max(0.001, float(item.get("duration") or 0.001))
                filters.append(
                    f"anullsrc=channel_layout=stereo:sample_rate={join_rate}:d={silence:.6f}{label}"
                )
            concat_inputs.append(label)

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
        subtitle_args: list[str] = []
        if joined_subtitle_input is not None:
            target, problems = subtitle_codec_args_for_container(
                str(output_path.suffix), ["subrip"])
            for problem in problems:
                log_warn(f"Joined subtitles: {problem}")
            if target:
                cmd.extend(["-map", f"{joined_subtitle_input}:s:0"])
                subtitle_args = target
        attachments_mapped = append_embedded_attachment_maps(cmd, join_answers)
        data_mapped = append_source_data_maps(cmd, join_answers)
        append_source_metadata_chapter_options(cmd, join_answers)
        append_negative_stream_options(
            cmd, join_answers, True, [0] if subtitle_args else [], data_mapped)
        cmd.extend(subtitle_args)
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
    for line in join_extras_outcome_notes(join_answers, items):
        appio.note(line)
    answers["final_resolution"] = join_answers.get("final_resolution")
    answers.pop("_join_complex_graph", None)
    return cmd


__all__ = [
    'build_hardsub_video_filter',
    'build_hardsub_command',
    'build_cut_filter_complex',
    'join_extras_outcome_notes',
    'build_join_encode_command',
]
