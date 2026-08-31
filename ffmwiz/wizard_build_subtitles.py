"""Subtitle handling for the encode builders: extract, retime, slice, hardsub.

Split out of ffmwiz/wizard_build_b.py for file size. Depends on
`wizard_build_filters` (one direction only, for `encode_timeline_map`)
and on nothing above it.

`slice_subtitle_tracks_into_parts` and `joined_timeline_map` stay OUT of
__all__ because they were never in wizard_build_b's either -- callers outside
this file reach them through the module object, not through `import *`.
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
from ffmwiz import wizard_base  # noqa: E402,F401  (leaf: encode-option builders)
from ffmwiz import wizard_build_filters  # noqa: E402,F401  (defines encode_timeline_map)


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
    return not wizard_build_filters.encode_timeline_map(answers).is_identity

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
    timeline = wizard_build_filters.encode_timeline_map(answers)
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


__all__ = [
    'build_hardsub_video_filter',
    'build_hardsub_command',
    'extract_subtitle_text',
    'append_subtitle_track_metadata',
    'encode_subtitle_retiming_required',
    'confirm_bitmap_subtitle_drop',
    'build_joined_subtitle_files',
    'build_retimed_subtitle_inputs',
    'build_split_subtitle_inputs',
]
