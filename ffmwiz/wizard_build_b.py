"""FFmWiz wizard_build overflow (wizard_build_b) — split for file size.

Re-exported by wizard_build, so every consumer of
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
    # A build is a PLAN BOUNDARY, and this builder is the only place its callers
    # share. Without it a map left over from an earlier plan still decided what
    # HardSub encoded: a fresh H264 request carrying a previous plan's effective
    # VP9 built `-c:v libvpx-vp9`, and the revision never moved (D12). Before
    # ANY effective-value lookup, so nothing resolves into a map it does not
    # own, and on the OUTER dict, before any shallow copy below it.
    require_plan_revision(answers)
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
        # The RESOLVED codec, never the requested one. Writing the fallback back
        # into `answers["video_codec"]` left Back, the summary and the retained
        # config unable to tell a one-plan container fallback from the user's
        # own answer: a .webm HardSub of an H264 request came back reading VP9,
        # and the effective map stayed EMPTY, so nothing recorded that a
        # substitution had happened at all (D11). `resolve_video_encoder` reads
        # the effective map first and falls back to the request, so recording it
        # here is the whole fix.
        effective_settings(answers)["video_codec"] = container_codec
    video_encoder, tag, _profile = resolve_video_encoder(answers)
    if video_encoder == "copy":
        # HardSub burns the subtitles into the picture, so a copy request can
        # never stand. The summary prints `effective_value(answers,
        # "video_codec")`, which would otherwise announce a copy over a
        # `-c:v libx265` command.
        video_encoder = "libx265"
        effective_settings(answers)["video_codec"] = "H265"
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
            audio_args, audio_note = container_audio_encode_args(output_ext, "aac", bitrate, resolve_audio_channels(answers))
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
            audio_args, audio_note = container_audio_encode_args(output_ext, "aac", bitrate, resolve_audio_channels(answers))
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
    cmd.extend(["-filter:v", build_hardsub_video_filter(answers, video_encoder)])
    cmd.extend(["-c:v", video_encoder])
    append_hardsub_quality_args(cmd, answers, video_encoder)
    wizard_base.append_nvenc_multipass_args(cmd, answers, video_encoder)
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
    plan = join_subtitle_plan(answers, items)
    if plan.get("supported"):
        for track in plan["tracks"]:
            carried = sum(1 for _i, stream, _d in track["segments"] if stream is not None)
            label = track.get("title") or track.get("language") or f"track {track['index']}"
            lines.append(
                f"Join extras -- Subtitles: merged text track {track['index']} ({label}) from "
                f"{carried} of {len(track['segments'])} input(s), shifted onto the joined timeline.")
        for index in plan.get("dropped_tracks") or []:
            lines.append(
                f"Join extras -- Subtitles: selected track {index} is dropped - no input carries it "
                "as text, and a bitmap track has no cue times to shift.")
    elif plan.get("reason") != "no subtitle track was selected":
        lines.append(f"Join extras -- Subtitles: dropped - {plan.get('reason') or 'unavailable'}.")
    return lines


def extract_subtitle_text(ffmpeg: str, source: Path, relative_index: int,
                          destination: Path, label: str,
                          origin: float | None = None,
                          ffprobe: str | None = None) -> str:
    """Pull one subtitle stream out as SRT text measured from `origin`.

    Returns "" when the stream cannot be read.

    Extracting without `-copyts` hands back cues the demuxer has already rebased
    by the CONTAINER start, which is the minimum across every stream and not
    where the encode puts output zero. On an MKV whose AAC track carries
    negative priming the container starts at -0.023 while the picture starts at
    0, so a source packet at 0.200 came back as 0.223; a 0.5x retime turned that
    into 0.446 instead of 0.400, and re-extracting the result added the same
    23 ms again. `-copyts` keeps the source's own timestamps and `origin` -- see
    `subtitle_source_origin` -- says which moment of them is zero.

    `origin=None` means "the picture's own start", read from the file. That is
    the joined-timeline rule: a join refuses any edited timeline, so its inputs
    are never seeked and each one's cues belong to its first video frame.
    """
    if origin is None:
        if not ffprobe:
            # The join builder extracts without an ffprobe in hand; ffprobe ships
            # beside ffmpeg, so a custom ffmpeg still finds its matching probe.
            sibling = Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix)
            ffprobe = str(sibling) if sibling.exists() else "ffprobe"
        try:
            probed = subprocess.run(
                [str(ffprobe), "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=start_time:format=start_time",
                 "-of", "json", str(source)],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                encoding="utf-8", errors="replace", timeout=60)
            if probed.returncode == 0:
                origin = video_timeline_origin(json.loads(probed.stdout or "{}"))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            log_warn(f"{label}: could not read the video origin of {source.name}: {exc}")
    # -nostdin: this runs inside a wizard/GUI/test process whose stdin is not a
    # terminal, and an FFmpeg left polling it can sit there until the timeout.
    command = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if origin is not None:
        # Keep the source's own timestamps; the origin below is subtracted from
        # them. Without an origin there is no way to tell the video clock from
        # the container clock, so leave the demuxer's normalization in place.
        command.append("-copyts")
    command += ["-i", str(source), "-map", f"0:s:{relative_index}",
                "-c:s", "srt", str(destination)]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL,
                                encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        log_warn(f"{label}: extraction failed for {source.name}: {exc}")
        return ""
    if result.returncode != 0 or not destination.exists():
        log_warn(f"{label}: could not extract stream {relative_index} from {source.name} "
                 f"(exit {result.returncode})")
        return ""
    text = destination.read_text(encoding="utf-8", errors="replace")
    if origin:
        # A cue that ends before the first video frame has no picture to sit on.
        text = render_srt([(start - origin, end - origin, body)
                           for start, end, body in parse_srt(text)
                           if end - origin > 0])
    return text


def build_joined_subtitle_files(answers: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One merged SRT per SELECTED logical subtitle track.

    Returns [{"path", "index", "language", "title", "default", "forced"}, ...],
    or [] when the join cannot carry subtitles -- in which case the reason is
    logged and shown, because the wizard asked the user about subtitles and owes
    them an answer either way.

    Every selected track gets its own merged file. Reading `subtitle_tracks` as
    a yes/no flag and then taking each input's track 0 meant a request for the
    Spanish track silently produced the English one (R04).
    """
    plan = join_subtitle_plan(answers, items)
    if not plan.get("supported"):
        reason = plan.get("reason") or "unavailable"
        if reason != "no subtitle track was selected":
            appio.note(f"Joined subtitles: not assembled - {reason}.")
            log_info(f"Joined subtitles skipped: {reason}")
        return []
    for index in plan.get("dropped_tracks") or []:
        appio.note(
            f"Joined subtitles: selected track {index} is dropped - no input carries it as "
            "text, and a bitmap track has no cue times to shift onto a joined timeline.")
        log_warn(f"Joined subtitles: selected track {index} dropped (no text stream in any input)")

    # Registered on the shared lease, NOT as a key: this function is called
    # with `join_answers = dict(answers)`, so a key written here never reaches
    # the executor that cleans up and the directory leaked on every run (R06).
    temp_dir = artifact_lease(answers).register(
        Path(tempfile.mkdtemp(prefix="ffmwiz_join_subs_")))
    ffmpeg = answers.get("ffmpeg") or "ffmpeg"
    built: list[dict[str, Any]] = []
    for track in plan["tracks"]:
        relative = track["index"]
        segments: list[tuple[str, float]] = []
        extracted = 0
        for position, (item, stream, duration) in enumerate(track["segments"]):
            text = ""
            if stream is not None:
                part = temp_dir / f"track{relative:02d}_part{position:02d}.srt"
                text = extract_subtitle_text(ffmpeg, Path(item["path"]), relative,
                                             part, "Joined subtitles")
                if text:
                    extracted += 1
            segments.append((text, duration))
        if not extracted:
            appio.note(f"Joined subtitles: track {relative} produced no cues and was dropped.")
            continue
        merged_text = merge_joined_srt(segments)
        if not merged_text.strip():
            appio.note(f"Joined subtitles: track {relative} contained no cues and was dropped.")
            continue
        # The merge above sits on the UNEDITED joined clock. Replay whatever the
        # video and audio graphs do to it, through the same TimelineMap, so an
        # edited join keeps the tracks the user selected instead of dropping
        # every one of them (F09).
        timeline = joined_timeline_map(answers, items)
        if not timeline.is_identity:
            retimed = retime_cues(parse_srt(merged_text), timeline)
            if not retimed:
                appio.note(f"Joined subtitles: track {relative} has no cue left on the "
                           "edited timeline and was dropped.")
                continue
            merged_text = render_srt(retimed)
            log_info(f"Joined subtitles: track {relative} retimed onto the edited joined "
                     f"timeline (keep_ranges={timeline.keep_ranges}; "
                     f"speed={timeline.speed:g}; reverse={timeline.reverse})")
        merged = temp_dir / f"joined{relative:02d}.srt"
        merged.write_text(merged_text, encoding="utf-8", newline="\n")
        log_info(f"Joined subtitles: merged track {relative} from {extracted}/"
                 f"{len(track['segments'])} input(s) into {merged}")
        appio.note(
            f"Joined subtitles: assembled track {relative} from {extracted} input(s) "
            "with each input's cues shifted onto the joined timeline.")
        built.append({"path": merged, "index": relative,
                      "language": track.get("language", ""), "title": track.get("title", ""),
                      "default": bool(track.get("default")), "forced": bool(track.get("forced"))})
    return built


def append_subtitle_track_metadata(cmd: list[str], tracks: list[dict[str, Any]]) -> None:
    """Carry each rebuilt track's language/title/disposition onto the output.

    A retimed or merged track is a brand new stream, so without this it lands as
    an untitled, language-less, never-default subtitle whatever the source said.
    """
    for position, track in enumerate(tracks):
        if track.get("language"):
            cmd.extend([f"-metadata:s:s:{position}", f"language={track['language']}"])
        if track.get("title"):
            cmd.extend([f"-metadata:s:s:{position}", f"title={track['title']}"])
        flags = [name for name in ("default", "forced") if track.get(name)]
        cmd.extend([f"-disposition:s:{position}", "+".join(flags) if flags else "0"])


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


def slice_subtitle_tracks_into_parts(
    answers: dict[str, Any],
    sources: list[tuple[list[tuple[float, float, str]], dict[str, Any]]],
    part_intervals: list[tuple[float, float]],
) -> list[list[dict[str, Any]]]:
    """Cut whole-timeline cues into one SRT per Split part, rebased to zero.

    `sources` is [(cues, metadata), ...] already on the PROCESSED clock. Shared
    by the single-input Split builder and the join builder: a joined Split has
    exactly the same problem, and mapping the one merged track into every part
    would put Part 2's cues at their full-timeline positions.

    Returns one list of track dicts per interval, parallel to `part_intervals`.
    """
    if not sources or not part_intervals:
        return []
    part_dir = artifact_lease(answers).register(
        Path(tempfile.mkdtemp(prefix="ffmwiz_split_subs_parts_")))
    per_part: list[list[dict[str, Any]]] = []
    for part_idx, (part_start, part_end) in enumerate(part_intervals):
        tracks: list[dict[str, Any]] = []
        for order, (cues, meta) in enumerate(sources):
            sliced = []
            for start, end, text in cues:
                clipped_start = max(float(start), float(part_start))
                clipped_end = min(float(end), float(part_end))
                if clipped_end > clipped_start + 1e-6:
                    sliced.append((clipped_start - part_start,
                                   clipped_end - part_start, text))
            if not sliced:
                continue
            path = part_dir / f"part{part_idx + 1:02d}_track{order:02d}.srt"
            path.write_text(render_srt(sliced), encoding="utf-8", newline="\n")
            tracks.append({**meta, "path": path})
        per_part.append(tracks)
    return per_part


def joined_timeline_map(answers: dict[str, Any],
                        items: list[dict[str, Any]]) -> TimelineMap:
    """The same transform, measured on the JOINED clock.

    A join's source timeline is the sum of its inputs, so `encode_timeline_map`
    -- which reads input 1's format duration -- describes the wrong axis for it.
    Cuts, speed and reverse are the same answers either way.

    Summed over each input's PICTURE, for the reason `encode_timeline_map` uses
    `video_stream_span_seconds`: `concat` splices frames, so a container that
    outlives its video contributes nothing to the joined picture. Summing
    `item["duration"]` made a 2.000 s picture inside a 3.000 s MKV count as
    3.000 s of joined timeline, which moved every cut, split point and cue on
    the edited join by the difference (B07).
    """
    return TimelineMap(
        keep_ranges=list(answers.get("cut_keep_ranges") or []),
        source_duration=sum(join_item_picture_span(item) for item in items),
        speed=encode_video_speed_factor(answers) if video_speed_transform_enabled(answers) else 1.0,
        reverse=bool(answers.get("reverse_video")),
    )


def encode_subtitle_retiming_required(answers: dict[str, Any]) -> bool:
    """Does this encode move the clock away from the source subtitle timestamps?

    Every timeline edit qualifies, a single-range trim included. `-ss` before the
    source input does NOT rebase its subtitle packets with the picture: measured
    on a 4 s source trimmed to 2.0-4.0 s, the cues moved by 0.2 s instead of 2 s
    and one of them ended 1.3 s past the end of the output.
    """
    if not output_has_video(answers):
        return False
    return not encode_timeline_map(answers).is_identity


def confirm_bitmap_subtitle_drop(answers: dict[str, Any],
                                 tracks: list[tuple[int, str]]) -> bool:
    """State that bitmap tracks cannot be retimed, then ask before dropping them.

    Mapping them through unchanged is the outcome that must never happen
    silently: the picture moves, the subtitle bitmaps do not, and the user is
    handed subtitles that are simply wrong with nothing said about it.
    """
    if "bitmap_subtitle_drop_confirmed" in answers:
        return bool(answers["bitmap_subtitle_drop_confirmed"])
    listed = ", ".join(f"0:s:{index} ({codec or 'unknown'})" for index, codec in tracks)
    appio.note(
        "Subtitles: this encode changes the timeline (cut/speed/reverse). Bitmap subtitle "
        f"track(s) {listed} are pictures with no cue times to move, so they cannot be "
        "retimed -- the only correct outcome is to drop them. Keeping them would mux "
        "subtitles that no longer match the video.")
    confirmed = appio.ask_yes_no(
        appio.question_prompt(
            answers,
            "Drop the bitmap subtitle track(s) and continue?",
            "y/n; text subtitle tracks are still retimed and kept",
            "n",
        ),
        False,
    )
    answers["bitmap_subtitle_drop_confirmed"] = confirmed
    log_info(f"User choice: bitmap_subtitle_drop_confirmed={confirmed}; tracks={listed}")
    return confirmed


def build_retimed_subtitle_inputs(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """Rebuild every selected TEXT subtitle track on the processed timeline.

    Returns [{"path", "source_index", "language", "title", "default", "forced"}]
    for injection as extra inputs. Empty when nothing needs retiming, when the
    selection holds no text track, or when every cue falls inside a removed
    range.

    Mapping the source streams straight through left their timestamps on the
    source clock: a 2.5-3.5 s cue stayed put in a 2 s 2x output, and the stale
    packet stretched the container to 3.5 s (R03).
    """
    if not (output_has_video(answers) and source_subtitles_keep_enabled(answers)):
        return []
    streams = list(answers.get("subtitle_streams") or [])
    if not streams or not encode_subtitle_retiming_required(answers):
        return []
    selected = [index for index in selected_subtitle_streams(answers)
                if 0 <= int(index) < len(streams)]
    if not selected:
        return []

    bitmap = [(index, str(streams[index].get("codec_name") or ""))
              for index in selected if not is_text_subtitle(streams[index])]
    if bitmap and not confirm_bitmap_subtitle_drop(answers, bitmap):
        raise RuntimeError(
            "Bitmap subtitle tracks cannot be retimed for a cut/speed/reverse encode, and "
            "dropping them was not confirmed. Deselect those tracks in the subtitle "
            "question, or remove the cut/speed/reverse change.")

    text_indices = [index for index in selected if is_text_subtitle(streams[index])]
    if not text_indices:
        return []
    # A rebuilt track is SRT whatever the source was, so ask the target container
    # about subrip. Mapping a stream the muxer cannot carry kills the whole
    # output at header-write time, which is worse than losing the subtitles.
    if subtitle_codec_for_container(answers.get("output_ext", ""), "subrip") is None:
        appio.note(
            f".{str(answers.get('output_ext') or '').lstrip('.')} cannot store text subtitles, "
            "so the retimed track(s) were dropped.")
        return []
    timeline = encode_timeline_map(answers)
    # Cut ranges reach FFmpeg as `-ss`/`trim`, which count from the container
    # start; without them the filter chain rebases to the video's first frame.
    origin = subtitle_source_origin(answers)
    # Leased, not stored as a key: this builder is also called with a shallow
    # copy of answers, and a key written on the copy never reaches cleanup (R06).
    temp_dir = artifact_lease(answers).register(
        Path(tempfile.mkdtemp(prefix="ffmwiz_retimed_subs_")))
    ffmpeg = answers.get("ffmpeg") or "ffmpeg"
    source = Path(answers["input_path"])
    built: list[dict[str, Any]] = []
    for index in text_indices:
        raw = temp_dir / f"source{index:02d}.srt"
        source_text = extract_subtitle_text(ffmpeg, source, index, raw, "Retimed subtitles",
                                            origin, answers.get("ffprobe"))
        cues = retime_cues(parse_srt(source_text), timeline) if source_text else []
        if not cues:
            appio.note(
                f"Subtitles: track 0:s:{index} has no cue left on the processed timeline "
                "and was dropped.")
            continue
        path = temp_dir / f"retimed{index:02d}.srt"
        path.write_text(render_srt(cues), encoding="utf-8", newline="\n")
        built.append({"path": path, "source_index": index,
                      **subtitle_track_metadata(streams[index])})
    if built:
        appio.note(
            f"Subtitles: {len(built)} text track(s) were retimed onto the processed "
            f"timeline ({timeline.output_duration:.3f}s).")
        log_info(
            "Subtitles retimed: tracks="
            + ",".join(str(track["source_index"]) for track in built)
            + f"; keep_ranges={timeline.keep_ranges}; speed={timeline.speed:g}; "
            f"reverse={timeline.reverse}; output_duration={timeline.output_duration:.6f}")
    return built


def build_split_subtitle_inputs(
    answers: dict[str, Any],
    part_intervals: list[tuple[float, float]],
    retimed: list[dict[str, Any]] | None = None,
) -> list[list[dict[str, Any]]]:
    """One text subtitle file per Split part, on that part's own clock.

    A Split part is its own output starting at zero, so the whole-timeline cues
    have to be sliced to that part's processed interval and shifted back to it.
    The Split builder emitted `-sn` instead. With a cut or a speed change that
    was worse than a silent drop: the retimed track WAS built, announced as
    "1 text track(s) were retimed onto the processed timeline", handed to
    FFmpeg as an input -- and then never mapped, so the message described an
    output that carried no subtitles at all.

    `retimed` is the whole-timeline set the caller already built (cut/speed/
    reverse). Without one the timeline is identity, so the source cues already
    sit on the output clock and only need slicing.

    Returns one list of track dicts per part, parallel to `part_intervals`.
    """
    if not part_intervals:
        return []
    if not (output_has_video(answers) and source_subtitles_keep_enabled(answers)):
        return []
    streams = list(answers.get("subtitle_streams") or [])
    if not streams:
        return []
    selected = [index for index in selected_subtitle_streams(answers)
                if 0 <= int(index) < len(streams)]
    if not selected:
        return []
    # A sliced track is SRT whatever the source was. Mapping a stream the muxer
    # cannot carry kills the whole output at header-write time.
    if subtitle_codec_for_container(answers.get("output_ext", ""), "subrip") is None:
        appio.note(
            f".{str(answers.get('output_ext') or '').lstrip('.')} cannot store text "
            "subtitles, so the Split parts carry none.")
        return []

    sources: list[tuple[list[tuple[float, float, str]], dict[str, Any]]] = []
    # Tracks handed in by the caller win outright, whatever the clock did. The
    # exported staged plan is the case: its Split stage reads an intermediate
    # that has not been written yet, so extraction below finds nothing and the
    # part is emitted with `-sn` -- the plan silently dropped subtitles the
    # automatic run keeps. The planner builds these from the SOURCES, which do
    # exist at plan time, and the stage owns only the split, so its own clock
    # is the identity and the cues arrive ready to slice.
    prebuilt = answers.get("_prebuilt_retimed_subtitles")
    if prebuilt:
        for track in prebuilt:
            try:
                cues = parse_srt(Path(track["path"]).read_text(encoding="utf-8"))
            except OSError:
                continue
            if cues:
                sources.append((cues, track))
    # Branch on whether the clock MOVED, not on whether `retimed` is empty: a
    # cut can legitimately leave no cue at all, and reading the source track in
    # that case would slice cues that sit on a timeline the output does not use.
    elif encode_subtitle_retiming_required(answers):
        for track in (retimed or []):
            try:
                text = Path(track["path"]).read_text(encoding="utf-8")
            except OSError:
                continue
            cues = parse_srt(text)
            if cues:
                sources.append((cues, track))
    else:
        bitmap = [(index, str(streams[index].get("codec_name") or ""))
                  for index in selected if not is_text_subtitle(streams[index])]
        if bitmap and not confirm_bitmap_subtitle_drop(answers, bitmap):
            raise RuntimeError(
                "Bitmap subtitle tracks cannot be sliced into Split parts, and dropping "
                "them was not confirmed. Deselect those tracks in the subtitle question, "
                "or remove the Split points.")
        text_indices = [index for index in selected if is_text_subtitle(streams[index])]
        if not text_indices:
            return []
        temp_dir = artifact_lease(answers).register(
            Path(tempfile.mkdtemp(prefix="ffmwiz_split_subs_")))
        ffmpeg = answers.get("ffmpeg") or "ffmpeg"
        source = Path(answers["input_path"])
        for index in text_indices:
            raw = temp_dir / f"source{index:02d}.srt"
            # Every Split part is trimmed, so its clock is the container's.
            text = extract_subtitle_text(ffmpeg, source, index, raw, "Split subtitles",
                                         subtitle_source_origin(answers),
                                         answers.get("ffprobe"))
            cues = parse_srt(text) if text else []
            if cues:
                sources.append((cues, {"source_index": index,
                                       **subtitle_track_metadata(streams[index])}))
    if not sources:
        return []

    per_part = slice_subtitle_tracks_into_parts(answers, sources, part_intervals)

    carried = sum(len(tracks) for tracks in per_part)
    if carried:
        appio.note(
            f"Subtitles: {len(sources)} text track(s) were sliced across "
            f"{len(part_intervals)} Split part(s) ({carried} output track(s)).")
        log_info(
            "Split subtitles: tracks="
            + ",".join(str(meta.get("source_index")) for _c, meta in sources)
            + "; parts=" + ",".join(f"{s:.3f}-{e:.3f}" for s, e in part_intervals)
            + f"; output_tracks={carried}")
    return per_part


def build_join_encode_command(answers: dict[str, Any], items: list[dict[str, Any]], output_path: Path) -> list[str]:
    output_path = resolve_output_collision_against_inputs(
        output_path,
        [Path(item["path"]) for item in items if item.get("path")],
        answers.get("output_collision_suffix", "_Encode"),
    )
    answers["output_path"] = output_path
    # A build is a PLAN BOUNDARY. The lease and the effective map were opened
    # here but the revision was not, so a map from an earlier plan still decided
    # what the join encoded: a fresh H264 request carrying a previous plan's
    # effective VP9 built `-c:v libvpx-vp9` with the revision at None throughout
    # (D13). It must come BEFORE `effective_settings()` below -- an untagged map
    # on a dict that already claims a revision is exactly what the boundary
    # refuses -- and before the shallow copy, so a reverse-pipeline stage gets
    # its own revision instead of resolving into the job's.
    require_plan_revision(answers)
    # Open the lease BEFORE the shallow copy. dict() copies the key but shares
    # the object, so anything the copy leases below is still owned out here --
    # but only if the lease already exists at copy time.
    artifact_lease(answers)
    effective_settings(answers)
    # BEFORE the command exists, so the summary the user confirms and the
    # command that runs cannot disagree. `build_ffmpeg_command` already did
    # this; the join path did not, so a config-retained cpu_two_pass survived
    # into the summary and was only disabled later by the executor -- and a
    # declined run never reached that point at all (B09).
    _two_pass_off = normalize_cpu_two_pass_selection(answers)
    if _two_pass_off:
        appio.note(f"CPU two-pass was turned off for this job: {_two_pass_off}.")
        log_info(f"CPU two-pass disabled before the join summary: {_two_pass_off}")
    join_answers = dict(answers)
    join_answers["_join_complex_graph"] = True
    video_encoder, tag, profile = resolve_video_encoder(join_answers)
    if video_encoder == "copy":
        join_answers["video_codec"] = DEFAULT_VIDEO_CODEC
        video_encoder, tag, profile = resolve_video_encoder(join_answers)
    video_encoder, tag, profile = enforce_bit_depth_compatible_video_encoder(join_answers, video_encoder, tag, profile)
    # A join always re-encodes through the concat filter, so a requested "copy"
    # silently became libx264/libx265 while `answers` still said copy and the
    # summary reported the request rather than the reality (R10). Record what
    # was actually resolved, on the OUTER dict, so summaries and logs can show
    # the truth without losing what the user originally asked for.
    effective_settings(answers)["video_codec"] = video_encoder
    if str(answers.get("video_codec", "")).lower() == "copy":
        appio.note(
            "Video copy cannot be used across a join; the joined timeline is "
            f"re-encoded with {video_encoder}.")
    first_video = items[0]["video_streams"][0]
    join_answers["video_streams"] = [first_video]
    target_dimensions = resolve_scale_dimensions(join_answers, join_answers.get("resolution", "n"))
    if target_dimensions:
        target_w, target_h = target_dimensions
    else:
        # The size AFTER the crop this same graph applies, not the raw source.
        # Normalising back to the source dimensions scaled a 140x120 crop of a
        # 160x120 input straight back up to 160x120, so the join undid its own
        # crop and the next stage cropped the result again (D01).
        target_w, target_h = cropped_source_size(join_answers)
        target_w = int(target_w or first_video.get("width") or 1280)
        target_h = int(target_h or first_video.get("height") or 720)
        join_answers["final_resolution"] = (target_w, target_h)
    target_fps = float(join_answers.get("fps") or rational_to_float(first_video.get("avg_frame_rate")) or rational_to_float(first_video.get("r_frame_rate")) or 30.0)

    cmd: list[str] = [join_answers["ffmpeg"], "-hide_banner", "-y" if OVERWRITE_OUTPUT else "-n"]
    use_cuda_decode_complex = should_use_cuda_decode_for_complex_graph(join_answers, video_encoder, False)
    for item in items:
        if use_cuda_decode_complex:
            append_cuda_decode_args_for_input(cmd, join_answers)
        cmd.extend(["-i", str(item["path"])])

    # The concat FILTER cannot carry subtitles, so joined subtitle tracks have
    # to be assembled separately: each input's cues shifted by that input's
    # start offset, merged, and fed back as extra inputs -- one per selected
    # logical track, not one for the whole job.
    # Built here, but the `-i` entries are appended further down: a Split needs
    # one sliced track PER PART, and the part intervals are not known until the
    # final split filters are laid out. Everything before that point references
    # inputs 0..len(items)-1, so appending later cannot disturb the graph.
    joined_subtitle_tracks = build_joined_subtitle_files(join_answers, items)

    # The selection is an explicit STATE, not a truthy list. An empty
    # `audio_tracks` used to be indistinguishable from a missing one, so an
    # explicit "no audio tracks" answer was overwritten by the silent-first
    # recovery below and the output arrived with an audio stream the user had
    # said no to (F04).
    audio_state, selected_audio = join_audio_selection(join_answers, items)
    if audio_state == "unasked" and any(item_audio_streams(item) for item in items):
        # The track question is gated on input 1 having audio, so an ABSENT key
        # means it was never asked and every LATER input's audio would go
        # unmapped and unmentioned. Only this state may recover a track.
        selected_audio = [0]
        appio.note(
            "Join audio: input 1 has no audio, so the track question was never asked; track 0 "
            "of the other inputs is joined and input 1's segment is silent."
        )
    silenced: list[str] = []
    unselected: list[str] = []
    for item_pos, item in enumerate(items, start=1):
        audio_count = len(item_audio_streams(item))
        name = Path(item.get("path")).name
        if any(idx >= audio_count for idx in selected_audio):
            silenced.append(f"input {item_pos} ({name})")
        skipped = [idx for idx in range(audio_count) if idx not in selected_audio]
        if skipped and selected_audio:
            unselected.append(f"input {item_pos} ({name}): track(s) {skipped}")
    if silenced:
        # The standalone join path has always synthesised silence here; the
        # wizard join used to refuse the very same set of files instead.
        appio.note(
            "Join audio: silence is synthesised for the selected track(s) missing from "
            + ", ".join(silenced) + "."
        )
    if unselected:
        # Every logical track IS reachable now, so this states what the answer
        # left out rather than what the question could not offer.
        appio.note(
            "Join audio: these source tracks were not selected, so they are NOT in the joined "
            "output: " + "; ".join(unselected) + "."
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
    # The concat filter refuses mismatched audio, so every input AND every
    # synthesised-silence segment has to share one layout. Take the widest
    # present instead of forcing stereo, which used to flatten a 5.1 join.
    join_layout = join_target_channel_layout(items)
    for input_idx, item in enumerate(items):
        chain = []
        if crop_filter:
            chain.append(crop_filter)
        chain.extend([
            "" if vfr_join else f"fps={target_fps:g}",
            square_pixel_scale_chain(
                join_answers.get("ffmpeg") or "ffmpeg",
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease"),
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2",
            output_pix_fmt and f"format={output_pix_fmt}",
            "setpts=PTS-STARTPTS",
        ])
        chain = [part for part in chain if part]
        filters.append(f"[{input_idx}:v:0]{','.join(chain)}[jv{input_idx}]")
        concat_inputs.append(f"[jv{input_idx}]")
        item_audio_count = len(item_audio_streams(item))
        for audio_pos, audio_index in enumerate(selected_audio):
            label = f"[ja{input_idx}_{audio_pos}]"
            if audio_index < item_audio_count:
                filters.append(f"[{input_idx}:a:{audio_index}]"
                               f"{join_audio_prep_filter(join_rate, join_layout)}{label}")
            else:
                # The PICTURE span, not the container. `concat` splices the
                # decoded picture, so silence sized from `format.duration`
                # outlives the frames it stands in for: a 2.000 s picture in a
                # 3.000 s MKV produced d=3.000000 and a 5.044 s join whose last
                # 1.044 s had no frame at all (B07).
                silence = max(0.001, join_item_picture_span(item) or 0.001)
                filters.append(
                    f"anullsrc=channel_layout={join_layout}:sample_rate={join_rate}:d={silence:.6f}{label}"
                )
            concat_inputs.append(label)

    concat_outputs = ["[jvcat]"] + [f"[jacat{pos}]" for pos, _idx in enumerate(selected_audio)]
    filters.append(
        f"{''.join(concat_inputs)}concat=n={len(items)}:v=1:a={len(selected_audio)}{''.join(concat_outputs)}"
    )

    source_join_duration = sum(float(item.get("duration") or 0.0) for item in items)
    keep_ranges = normalize_cut_ranges(list(join_answers.get("cut_keep_ranges") or []), source_join_duration)
    final_duration = final_processed_duration_for_splits(join_answers, source_join_duration)
    video_label = append_join_trim_concat_filter(filters, "jvcat", keep_ranges, "video", "jvcut")
    # Orientation and look, which the per-input chain above has no way to
    # carry. `build_cpu_video_filter` is deliberately NOT reused here: that
    # chain already emits crop, fps, scale and format, so calling it would
    # apply all four a SECOND time. Only the parts a join cannot otherwise
    # express are rebuilt, in the relative order that builder uses.
    #
    # After the concat rather than per input, and the rotation is why: a
    # 90-degree turn swaps width and height, so rotating each input first
    # would leave a portrait frame to be fitted back into the landscape canvas
    # every input is normalised to -- pillarboxing the picture instead of
    # standing the output on its side. Applying it once, after the common
    # scale, gives the 120x160 a 160x120 join is expected to produce.
    # The one divergence from the single-input path: with an EXPLICIT
    # resolution answer that path scales the ROTATED frame into the requested
    # canvas, while this one rotates the canvas, so 720p + 90cw is 720x1280
    # here and a pillarboxed 1280x720 there.
    #
    # A staged job reaches this with `look` already stripped by
    # `stage_answers(owns=GEOMETRY_TRANSFORMATIONS)`, so the reverse stage
    # keeps its ownership and nothing is applied twice. A plain join carries
    # every key and gets the whole chain -- which is the case that was
    # silently dropping all of it.
    picture_chain = build_orientation_filters(join_answers) + build_look_filters(join_answers)
    if picture_chain:
        filters.append(f"[{video_label}]{','.join(picture_chain)}[jvpic]")
        video_label = "jvpic"
    if video_speed_transform_enabled(join_answers):
        filters.append(
            f"[{video_label}]{build_video_speed_filter(encode_video_speed_factor(join_answers), bool(join_answers.get('reverse_video')))}[jvfinal]"
        )
    else:
        filters.append(f"[{video_label}]setpts=PTS-STARTPTS[jvfinal]")
    video_label = "jvfinal"
    # Fade last, and timed against the duration AFTER the trim and the speed
    # change: a fade-out measured from the source length lands in the middle
    # of a sped-up output, or past its end entirely.
    fade_chain = build_fade_filters(join_answers, final_duration)
    if fade_chain:
        filters.append(f"[{video_label}]{','.join(fade_chain)}[jvfade]")
        video_label = "jvfade"

    audio_labels: list[str] = []
    for audio_pos, _audio_index in enumerate(selected_audio):
        label = append_join_trim_concat_filter(filters, f"jacat{audio_pos}", keep_ranges, "audio", f"jacut{audio_pos}")
        final_audio_label = f"jafinal{audio_pos}"
        if audio_speed_transform_enabled(join_answers) or loudnorm_transform_enabled(join_answers):
            filters.append(f"[{label}]{build_encode_audio_speed_filter(join_answers)}[{final_audio_label}]")
        else:
            filters.append(f"[{label}]asetpts=PTS-STARTPTS[{final_audio_label}]")
        audio_labels.append(final_audio_label)

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

    # Subtitle inputs go in now that the part intervals exist. Without the
    # per-part slice every part mapped the same whole-timeline track, so Part 2
    # carried its cues at their joined-timeline positions (F09).
    subtitle_tracks_by_part: list[list[dict[str, Any]]] = []
    subtitle_inputs_by_part: list[list[int]] = []
    if joined_subtitle_tracks and split_active and split_intervals:
        sources = []
        for track in joined_subtitle_tracks:
            try:
                cues = parse_srt(Path(track["path"]).read_text(encoding="utf-8"))
            except OSError:
                continue
            if cues:
                sources.append((cues, track))
        subtitle_tracks_by_part = slice_subtitle_tracks_into_parts(
            join_answers, sources, split_intervals)
    elif joined_subtitle_tracks:
        subtitle_tracks_by_part = [list(joined_subtitle_tracks)]
    next_subtitle_input = len(items)
    for part_tracks in subtitle_tracks_by_part:
        indices: list[int] = []
        for track in part_tracks:
            cmd.extend(["-i", str(track["path"])])
            indices.append(next_subtitle_input)
            next_subtitle_input += 1
        subtitle_inputs_by_part.append(indices)

    cmd.extend(["-filter_complex", ";".join(filters)])

    if join_answers.get("use_gpu") and str(video_encoder).endswith("_nvenc"):
        log_info("Join Videos uses CPU concat filters; NVENC is still used for final video encoding.")
    for part_idx, part_output in enumerate(output_paths):
        cmd.extend(["-map", f"[{video_outputs[part_idx]}]"])
        for audio_label in audio_outputs_by_part[part_idx]:
            cmd.extend(["-map", f"[{audio_label}]"])
        subtitle_args: list[str] = []
        mapped_subtitle_tracks: list[dict[str, Any]] = []
        # This part's OWN tracks: one whole-timeline set when there is no
        # Split, one sliced set per part when there is.
        part_subtitle_tracks = (subtitle_tracks_by_part[part_idx]
                                if part_idx < len(subtitle_tracks_by_part) else [])
        part_subtitle_inputs = (subtitle_inputs_by_part[part_idx]
                                if part_idx < len(subtitle_inputs_by_part) else [])
        if part_subtitle_tracks:
            target, problems = subtitle_codec_args_for_container(
                str(output_path.suffix), ["subrip"] * len(part_subtitle_tracks))
            for problem in problems:
                log_warn(f"Joined subtitles: {problem}")
            if target:
                for input_index in part_subtitle_inputs:
                    cmd.extend(["-map", f"{input_index}:s:0"])
                subtitle_args = target
                mapped_subtitle_tracks = part_subtitle_tracks
        attachments_mapped = append_embedded_attachment_maps(cmd, join_answers)
        data_mapped = append_source_data_maps(cmd, join_answers)
        append_source_metadata_chapter_options(cmd, join_answers)
        append_negative_stream_options(
            cmd, join_answers, True,
            list(range(len(mapped_subtitle_tracks))), data_mapped)
        cmd.extend(subtitle_args)
        append_subtitle_track_metadata(cmd, mapped_subtitle_tracks)
        wizard_base.append_video_encode_options(cmd, join_answers, video_encoder, tag, profile)
        if video_speed_transform_enabled(join_answers) and video_encoder != "copy":
            # A speed change outranks the VFR choice below: `vfr` still drops
            # frames against the guessed source rate (measured 40 -> 22 at 2x),
            # and only one -fps_mode may be given.
            cmd.extend(VIDEO_SPEED_OUTPUT_TIMING_ARGS)
        elif vfr_join and video_encoder != "copy":
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
        return gif_scale_chain(answers)
    base = build_cpu_video_filter(answers) or ""
    parts = [part for part in base.split(",")
             if part and not part.startswith("format=")]
    parts.append(gif_scale_chain(answers))
    return ",".join(parts)


def build_gif_palette_command(answers: dict[str, Any], source: Path,
                              palette: Path, prepared: bool = False) -> list[str]:
    """Pass 1: the 256 colours this clip actually uses."""
    return [str(answers["ffmpeg"]), "-y" if OVERWRITE_OUTPUT else "-n",
            "-hide_banner", *gif_input_options(answers), "-i", str(source),
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
            "-hide_banner", *gif_input_options(answers), "-i", str(source),
            "-i", str(palette),
            "-lavfi", gif_filter_chain(answers, prepared) + "," + GIF_PALETTEUSE_FILTER,
            "-an", "-sn", str(output_path)]

__all__ = [
    'build_gif_palette_command',
    'build_gif_write_command',
    'gif_filter_chain',
    'build_fade_filters',
    'build_look_filters',
    'build_orientation_filters',
    'build_cpu_video_filter',
    'build_hardsub_video_filter',
    'build_hardsub_command',
    'build_cut_filter_complex',
    'join_extras_outcome_notes',
    'extract_subtitle_text',
    'build_joined_subtitle_files',
    'append_subtitle_track_metadata',
    'encode_timeline_map',
    'encode_subtitle_retiming_required',
    'confirm_bitmap_subtitle_drop',
    'build_retimed_subtitle_inputs',
    'build_split_subtitle_inputs',
    'build_join_encode_command',
]


# wizard_base holds the encode-option builders this module calls. It is a leaf:
# it imports neither wizard nor wizard_build, so nothing here can re-enter a
# facade that is still merging its __all__.
from ffmwiz import wizard_base  # noqa: E402,F401


# The compositing and quick-output builders live in their own module now;
# re-exported here so every existing `from ffmwiz.wizard_build_b import *`,
# and every `wizard_build_b.<name>` call site, is unchanged.
from ffmwiz import wizard_build_c as _wizard_build_c  # noqa: E402
from ffmwiz.wizard_build_c import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_wizard_build_c.__all__)
