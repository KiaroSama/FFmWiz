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
    joined_subtitle_tracks = wizard_build_subtitles.build_joined_subtitle_files(join_answers, items)

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
    picture_chain = wizard_build_filters.build_orientation_filters(join_answers) + wizard_build_filters.build_look_filters(join_answers)
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
    fade_chain = wizard_build_filters.build_fade_filters(join_answers, final_duration)
    if fade_chain:
        filters.append(f"[{video_label}]{','.join(fade_chain)}[jvfade]")
        video_label = "jvfade"

    audio_labels: list[str] = []
    for audio_pos, _audio_index in enumerate(selected_audio):
        label = append_join_trim_concat_filter(filters, f"jacat{audio_pos}", keep_ranges, "audio", f"jacut{audio_pos}")
        final_audio_label = f"jafinal{audio_pos}"
        # `audio_transform_enabled`, not the speed/LoudNorm pair this used to
        # ask: the chain behind the gate also emits a gain and a fade, so a
        # volume-only or fade-only join never reached it and the answer was
        # dropped in silence -- with the PICTURE fade still applied, which is
        # the "black screen over full-volume audio" the chain warns about.
        if audio_transform_enabled(join_answers):
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
        subtitle_tracks_by_part = wizard_build_subtitles.slice_subtitle_tracks_into_parts(
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
        wizard_build_subtitles.append_subtitle_track_metadata(cmd, mapped_subtitle_tracks)
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
        # Same position and same reason as `build_ffmpeg_command` and the Split
        # per-part writer: last, immediately before this output, so the user's
        # own options can override what the wizard chose. This builder owns the
        # whole command, so without this line a join silently dropped the
        # escape hatch the wizard had just asked for.
        cmd.extend(join_answers.get("raw_ffmpeg_args") or [])
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
    'join_extras_outcome_notes',
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

# The filter chains and the subtitle handling moved into their own modules;
# re-exported here so every `from ffmwiz.wizard_build_b import *` and every
# `wizard_build_b.<name>` call site is unchanged.
from ffmwiz import wizard_build_filters as _wizard_build_filters  # noqa: E402
from ffmwiz import wizard_build_filters  # noqa: E402,F401
from ffmwiz.wizard_build_filters import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_wizard_build_filters.__all__)

from ffmwiz import wizard_build_subtitles as _wizard_build_subtitles  # noqa: E402
from ffmwiz import wizard_build_subtitles  # noqa: E402,F401
from ffmwiz.wizard_build_subtitles import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_wizard_build_subtitles.__all__)

# Deliberately NOT in __all__, exactly as before the split: these two are
# reached as wizard_build_b.<name> (tests/test_join_picture_duration.py does
# precisely that and says so), never through `import *`. `import *` above
# cannot carry them because the sibling's __all__ omits them, so name them.
from ffmwiz.wizard_build_subtitles import (  # noqa: E402,F401
    joined_timeline_map, slice_subtitle_tracks_into_parts)
