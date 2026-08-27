"""Bounded audio reverse: the staged plan every audio-reverse path shares.

Split out of `ext04b` as its own responsibility. `areverse` buffers its ENTIRE
input, so peak memory tracks duration directly and a long track cannot be
reversed in one command; everything here exists to reverse it in bounded
lossless chunks instead, and to REFUSE rather than run unbounded when a
combination cannot be staged exactly.

`ext04b` re-exports this module at its end, so every existing
`from ffmwiz.support.ext04b import *` keeps working unchanged.
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
from ffmwiz.appio import *  # noqa: F401,F403
from ffmwiz import appio  # noqa: F401
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401

from ffmwiz.core.artifacts import (  # noqa: F401
    EFFECTIVE_SETTINGS_KEY,
    PLAN_REVISION_KEY,
)
from ffmwiz.support.L01_cover import cover_art_method  # noqa: F401
from ffmwiz.support.ext04 import *  # sibling helpers  # noqa: F401,F403
from ffmwiz.support.ext04b import *  # sibling helpers  # noqa: F401,F403


# ---------------------------------------------------------------------------
# Bounded audio reverse (D13)
# ---------------------------------------------------------------------------
# `areverse` buffers its ENTIRE input, exactly like `reverse` does for video, so
# peak memory tracks duration directly. The video side gained a segmented
# executor; audio kept running the generated one-shot command, measured on a
# six-hour synthetic input as:
#
#     AREVERSE_COUNT 1
#     INPUT_COUNT 1
#     BOUNDS_PRESENT False
#     SEGMENT_OR_MEMORY_POLICY False
#
# which is 8.3 GiB of decoded samples at 48 kHz stereo.

# Bytes one decoded sample occupies per channel, by FFmpeg sample format.
# `areverse` holds decoded frames, so this -- not the compressed bitrate -- is
# what the budget has to be sized from.
AUDIO_SAMPLE_FMT_BYTES: dict[str, int] = {
    "u8": 1, "u8p": 1,
    "s16": 2, "s16p": 2,
    "s32": 4, "s32p": 4,
    "flt": 4, "fltp": 4,
    "dbl": 8, "dblp": 8,
    "s64": 8, "s64p": 8,
}

# Above this the staged plan is refused rather than run: the per-segment
# process cost stops being noise, and a job that needs more than this is far
# likelier to be a bad duration than a real request. At CD-quality stereo one
# segment already holds about an hour, so the cap is roughly three weeks of
# audio; at 192 kHz 8-channel it is about thirty hours.
AUDIO_REVERSE_MAX_SEGMENTS = 512


def decoded_bytes_per_sample(sample_fmt: Any) -> int:
    """Bytes one decoded sample of `sample_fmt` occupies, per channel.

    Unknown formats fall back to 4 rather than the smallest value: `fltp` is
    what most decoders emit, and underestimating here is what turns a budget
    into a promise it cannot keep.
    """
    return AUDIO_SAMPLE_FMT_BYTES.get(str(sample_fmt or "").strip().lower(), 4)


def intermediate_audio_sample_fmt(sample_fmt: Any) -> str:
    """The lossless scratch format that can hold `sample_fmt` without loss.

    FLAC is integer-only, so a float-decoded source (every lossy codec) is
    carried at 24 bits. Measured against the one-shot reverse of an AAC source:
    peak difference -138.5 dBFS, RMS -143.7 dBFS -- the 24-bit quantisation
    floor, and far below the noise floor of any codec that produced fltp in the
    first place. An integer source round-trips bit-exactly.
    """
    name = str(sample_fmt or "").strip().lower()
    return "s16" if name in {"u8", "u8p", "s16", "s16p"} else "s32"


def reverse_audio_segment_seconds_for(sample_rate: Any, channels: Any,
                                      sample_fmt: Any = None) -> float:
    """Seconds of audio one reverse segment may hold within the peak budget.

    Shares REVERSE_PEAK_BUDGET_BYTES with the video splitter: the promise is a
    peak for the process, not a per-stream allowance. There is deliberately no
    equivalent of the video path's 60 s ceiling -- a second of 48 kHz stereo is
    about 0.4 MB against 250 MB for a second of 4K30, so clamping audio to a
    minute would only multiply the number of FFmpeg invocations without
    changing the bound.

    The result is a whole number of SAMPLES, so no rounding can push a segment
    over the cap.
    """
    try:
        rate = int(sample_rate or 0)
        channel_count = int(channels or 0)
    except (TypeError, ValueError):
        rate = channel_count = 0
    if rate <= 0 or channel_count <= 0:
        # Budget for a demanding case rather than assuming the cheap one, the
        # same policy the video splitter uses for unknown geometry.
        rate, channel_count, sample_fmt = 192000, 8, "s32"
    per_second = (rate * channel_count * decoded_bytes_per_sample(sample_fmt)
                  * REVERSE_FRAME_SAFETY)
    allowance = REVERSE_PEAK_BUDGET_BYTES - REVERSE_FIXED_OVERHEAD_BYTES
    if per_second <= 0 or allowance <= 0:
        return 1.0
    samples = max(1, int(allowance // (per_second / rate)))
    return max(1.0 / rate, samples / rate)


def selected_audio_reverse_streams(answers: dict[str, Any],
                                   audio_indices: list[int]) -> list[dict[str, Any]]:
    streams = answers.get("audio_streams") or []
    return [streams[index] for index in audio_indices if index < len(streams)]


def reverse_audio_segment_seconds_for_streams(streams: list[dict[str, Any]]) -> float:
    """Seconds all selected tracks may hold TOGETHER within the peak budget.

    `areverse` buffers every selected stream in ONE process, so their decoded
    buffers coexist and the budget is the SUM of their per-second cost -- not
    the widest of them. Taking the widest is what the previous
    `min(per-stream window)` did, and it is wrong by the track count: eight
    192 kHz 8-channel float tracks reversed together were planned at

        PLANNED_SEGMENT_SECONDS 227.951302
        ESTIMATED_AGGREGATE_PEAK_GIB 12.5   against a 2.00 GiB cap  (6.25x)

    The window is rounded DOWN onto the highest selected sample rate, so every
    track lands on or before a whole sample and no rounding can push the
    aggregate over the cap. A track whose rate or channel count is unreadable
    is budgeted as the demanding case, the same policy the video splitter uses
    for unknown geometry.

    Raises `ReverseBudgetError` when a single sample of the aggregate does not
    fit: that is a planning error, and restoring an arbitrary minimum duration
    would be exactly the floor this budget exists to remove.
    """
    rates: list[int] = []
    per_second = 0.0
    for stream in streams:
        try:
            rate = int(stream.get("sample_rate") or 0)
            channels = int(stream.get("channels") or 0)
        except (TypeError, ValueError):
            rate = channels = 0
        sample_fmt = intermediate_audio_sample_fmt(stream.get("sample_fmt"))
        if rate <= 0 or channels <= 0:
            rate, channels, sample_fmt = 192000, 8, "s32"
        rates.append(rate)
        per_second += (rate * channels * decoded_bytes_per_sample(sample_fmt)
                       * REVERSE_FRAME_SAFETY)
    if not rates:
        return reverse_audio_segment_seconds_for(0, 0)
    allowance = REVERSE_PEAK_BUDGET_BYTES - REVERSE_FIXED_OVERHEAD_BYTES
    grid = max(rates)
    if per_second <= 0 or allowance <= 0:
        raise ReverseBudgetError(
            f"the reverse peak budget leaves {allowance} byte(s) for "
            f"{len(streams)} audio track(s); nothing can be planned inside it")
    samples = int(allowance // (per_second / grid))
    if samples < 1:
        raise ReverseBudgetError(
            f"one sample of the {len(streams)} selected audio track(s) needs "
            f"{per_second / grid:.0f} bytes, more than the "
            f"{allowance / 1024 ** 2:.0f} MiB the peak budget allows; reduce "
            "the selection or raise FFMWIZ_REVERSE_PEAK_BUDGET_MB")
    return samples / grid


def audio_reverse_segment_seconds(answers: dict[str, Any],
                                  audio_indices: list[int]) -> float:
    """The segment length for this job, summed over every selected track."""
    selected = selected_audio_reverse_streams(answers, audio_indices)
    if not selected:
        return reverse_audio_segment_seconds_for(0, 0)
    return reverse_audio_segment_seconds_for_streams(selected)


def lossless_scratch_audio_args(streams: list[dict[str, Any]]) -> list[str]:
    """Encoder options for the scratch chunks: lossless, and legal for the source.

    FLAC is the project's intermediate audio codec, but it tops out at eight
    channels, so anything wider falls back to PCM rather than being silently
    downmixed or refused.
    """
    channels = max((int(stream.get("channels") or 0) for stream in streams),
                   default=0)
    # The WIDEST selected stream decides. Taking the first one's format would
    # carry a float-decoded second track at 16 bits because track 0 happened to
    # be 16-bit PCM.
    sample_fmt = "s16" if streams and all(
        intermediate_audio_sample_fmt(stream.get("sample_fmt")) == "s16"
        for stream in streams) else "s32"
    if channels > 8:
        return ["-c:a", "pcm_s32le" if sample_fmt == "s32" else "pcm_s16le"]
    return ["-c:a", INTERMEDIATE_AUDIO_CODEC, "-sample_fmt", sample_fmt]


def audio_cut_only_filter_complex(
    audio_indices: list[int],
    keep_ranges: list[tuple[float, float]],
) -> tuple[str, list[str]]:
    """The cuts, and nothing else -- the one edit the FORWARD stage owns.

    Speed, LoudNorm and resampling stay continuous and are applied once, after
    the reversal, exactly where the one-shot filter chain puts them. Splitting
    an `atempo` or a LoudNorm across chunks would change the result at every
    boundary, which is the whole reason the REVERSAL is staged and the filter
    graph is not.

    Returns ("", []) when there is nothing to cut, so the forward pass maps the
    source tracks directly instead of routing them through a no-op graph.
    """
    if not keep_ranges:
        return "", []
    parts: list[str] = []
    labels: list[str] = []
    for position, audio_index in enumerate(audio_indices):
        out_label = f"fwd{position}"
        labels.append(out_label)
        if len(keep_ranges) == 1:
            start, end = keep_ranges[0]
            parts.append(f"[0:a:{audio_index}]atrim=start={start:.6f}:end={end:.6f},"
                         f"asetpts=PTS-STARTPTS[{out_label}]")
            continue
        sources = [f"acs{position}_{index}" for index in range(len(keep_ranges))]
        parts.append(f"[0:a:{audio_index}]asplit={len(keep_ranges)}"
                     + "".join(f"[{label}]" for label in sources))
        range_labels: list[str] = []
        for index, (start, end) in enumerate(keep_ranges):
            label = f"ac{position}_{index}"
            range_labels.append(f"[{label}]")
            parts.append(f"[{sources[index]}]atrim=start={start:.6f}:end={end:.6f},"
                         f"asetpts=PTS-STARTPTS[{label}]")
        parts.append(f"{''.join(range_labels)}concat=n={len(keep_ranges)}:v=0:a=1"
                     f"[{out_label}]")
    return ";".join(parts), labels


def build_audio_reverse_forward_command(
    answers: dict[str, Any],
    audio_indices: list[int],
    keep_ranges: list[tuple[float, float]],
    segment_seconds: float,
    pattern: Path,
) -> list[str]:
    """Stage 1: ONE forward decode of the source into lossless scratch chunks.

    The chunking is done by the segment muxer rather than by seeking, and that
    is the point: every packet lands in exactly one chunk, so the joined sample
    count is the source's to the sample. Re-seeking per chunk would give each
    chunk its own decoder start-up instead, which for a lossy source is not the
    signal the one-shot reverse would have produced.

    Measured on a 4 s AAC source cut into 1 s chunks: 46080 + 46080 + 41472 +
    43520 = 177152 samples, exactly the 177152 a single decode yields.
    """
    streams = selected_audio_reverse_streams(answers, audio_indices)
    cmd: list[str] = [answers["ffmpeg"], "-y" if OVERWRITE_OUTPUT else "-n",
                      "-hide_banner", "-i", str(answers["input_path"])]
    graph, labels = audio_cut_only_filter_complex(audio_indices, keep_ranges)
    if graph:
        cmd.extend(["-filter_complex", graph])
        for label in labels:
            cmd.extend(["-map", f"[{label}]"])
    else:
        for audio_index in audio_indices:
            cmd.extend(["-map", f"0:a:{audio_index}"])
    cmd.extend(["-vn", "-sn", "-dn"])
    cmd.extend(lossless_scratch_audio_args(streams))
    cmd.extend(["-f", "segment",
                "-segment_time", f"{max(0.001, float(segment_seconds)):.6f}",
                "-segment_format", "matroska",
                "-reset_timestamps", "1",
                str(pattern)])
    return cmd


def build_audio_reverse_chunk_command(answers: dict[str, Any],
                                      streams: list[dict[str, Any]],
                                      source: Path, target: Path) -> list[str]:
    """Stage 2: reverse ONE bounded chunk, lossless in and lossless out."""
    cmd = [answers["ffmpeg"], "-y" if OVERWRITE_OUTPUT else "-n", "-hide_banner",
           "-i", str(source), "-map", "0:a",
           # -filter:a is applied to EACH selected stream separately, so a
           # multi-track chunk is reversed track by track rather than mixed.
           "-filter:a", "areverse,asetpts=PTS-STARTPTS"]
    cmd.extend(lossless_scratch_audio_args(streams))
    cmd.append(str(target))
    return cmd


def audio_reverse_chunk_paths(ffprobe: str, workspace: Path, prefix: str) -> list[Path]:
    """The forward chunks that really hold audio, in order.

    The segment muxer writes a trailing stub for the boundary past the end of
    the stream -- 670 bytes of header on a 4 s fixture -- which ffprobe cannot
    even read. Anything without a positive duration is dropped; the sample-count
    regression is what proves nothing real was dropped with it.
    """
    kept: list[Path] = []
    for path in sorted(workspace.glob(prefix + "*")):
        try:
            probe = services.ffprobe_json(ffprobe, path) or {}
        except Exception:
            # The stub is not merely empty, it is unreadable: ffprobe reports
            # "EBML header parsing failed" and raises. That is the signature of
            # the artefact, so it is treated as a zero-length chunk rather than
            # allowed to fail the run.
            probe = {}
        duration = services.stream_duration_seconds({}, probe.get("format") or {}) or 0.0
        if duration > 0:
            kept.append(path)
        else:
            log_info(f"Bounded audio reverse: dropped empty scratch chunk {path.name}")
    return kept


def staged_audio_reverse_answers(answers: dict[str, Any],
                                 audio_indices: list[int],
                                 reversed_source: Path,
                                 carries_picture: bool) -> dict[str, Any]:
    """The answers the FINAL stage builds from: same job, reversal already done.

    Every source-derived resolution the builder makes -- output container,
    bitrate, channel count -- is deliberately still read from the ORIGINAL
    stream list, because the scratch is a 24-bit FLAC and would otherwise
    resolve a lossless-sized bitrate for a lossy output. Only what the earlier
    stages already consumed is cleared.
    """
    staged = dict(answers)
    # A stage is its own plan: it must not resolve into, or inherit from, the
    # map the outer job's build filled in.
    staged.pop(EFFECTIVE_SETTINGS_KEY, None)
    staged.pop(PLAN_REVISION_KEY, None)
    staged["input_path"] = reversed_source
    probe = services.ffprobe_json(answers.get("ffprobe"), reversed_source) or {}
    staged["probe"] = probe
    staged["format"] = probe.get("format") or {}
    staged["audio_streams"] = selected_audio_reverse_streams(answers, audio_indices)
    staged["audio_index"] = 0
    staged["audio_tracks"] = list(range(len(audio_indices)))
    staged["subtitle_streams"] = []
    staged["data_streams"] = []
    staged["attachment_streams"] = []
    # Consumed by stages 1-3. Leaving any of them here would apply the cut or
    # the reversal a second time, which is the class of bug the staged video
    # pipeline records as B01.
    staged["reverse_audio"] = False
    staged["reverse_video"] = False
    staged["audio_cut_keep_ranges"] = []
    staged["audio_keep_ranges"] = []
    staged["cut_keep_ranges"] = []
    if carries_picture:
        # Input 1 is the original file, added purely for its cover art.
        staged["_picture_input_index"] = 1
    else:
        staged["video_streams"] = []
        staged.pop("_picture_input_index", None)
    # A Path WITH a suffix: resolve_audio_tool_output_ext() reads it before
    # falling back to the input, which by now is a scratch container that would
    # resolve the wrong output format.
    staged["output_location"] = Path(answers["output_path"])
    staged.pop("output_name_stem", None)
    return staged


def audio_reverse_indices(answers: dict[str, Any],
                          audio_indices: list[int] | None = None) -> list[int]:
    """The source audio streams this reverse covers, in output order.

    `audio_index` alone was the whole answer, so the MAIN executor -- which
    passes no explicit indices and expresses its selection as `audio_tracks` --
    silently reversed track 0 and dropped the rest: a two-track selection
    returned success with one audio stream in the output (D04).

    `audio_tracks` is the selection every encode path uses; `audio_index` is
    the single-track answer the standalone audio tools ask for, and stays as
    the fallback rather than as an override. Duplicates are dropped and
    out-of-range indices are refused deterministically instead of silently
    changing what the user selected.
    """
    missing = object()
    if audio_indices is not None:
        chosen = list(audio_indices)
    else:
        tracks = answers.get("audio_tracks", missing)
        if tracks is missing:
            # The key ABSENT is not the same as "keep them all": the standalone
            # audio tools never set it and mean the one track they asked about.
            chosen = [answers.get("audio_index", 0)]
        elif tracks in (None, True, "all"):
            chosen = list(range(len(answers.get("audio_streams") or []))) or [0]
        elif isinstance(tracks, (list, tuple, set)):
            chosen = list(tracks)
        else:
            chosen = [tracks]
        if not chosen:
            chosen = [answers.get("audio_index", 0)]
    available = len(answers.get("audio_streams") or [])
    resolved: list[int] = []
    for value in chosen:
        try:
            index = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"audio track selection is not an index: {value!r}")
        if available and not 0 <= index < available:
            raise ValueError(
                f"audio track {index} was selected but the source has "
                f"{available} audio stream(s)")
        if index not in resolved:
            resolved.append(index)
    return resolved or [0]


def audio_reverse_content_seconds(answers: dict[str, Any]) -> tuple[float, float,
                                                                   list[tuple[float, float]]]:
    """(source duration, seconds that survive the cuts, the cut ranges)."""
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    keep_ranges = normalize_cut_ranges(
        list(answers.get("audio_cut_keep_ranges")
             or answers.get("audio_keep_ranges") or []), duration)
    content = total_keep_duration(keep_ranges) if keep_ranges else duration
    return duration, content, keep_ranges


def audio_reverse_needs_staging(answers: dict[str, Any],
                                audio_indices: list[int] | None = None) -> bool:
    """Whether this reverse is too long to hand `areverse` in one pass.

    False means the ordinary one-shot command is already inside the peak
    budget, so staging it would cost an extra decode for nothing. An unknown
    duration answers True, because a plan that cannot be sized cannot be
    claimed to be bounded -- `bounded_audio_reverse_to_file()` then refuses it
    rather than running the one-shot and hoping.
    """
    indices = audio_reverse_indices(answers, audio_indices)
    duration, content, _ranges = audio_reverse_content_seconds(answers)
    if duration <= 0:
        return True
    return content > audio_reverse_segment_seconds(answers, indices)


def audio_reverse_one_shot_warning(answers: dict[str, Any],
                                   audio_indices: list[int] | None = None) -> str:
    """Why the printed one-shot command is not what FFmWiz would have run ("" = it is).

    Declining "start now" hands the user the ONE-SHOT command, which for a
    track past the budget is precisely the unbounded `areverse` the staged plan
    exists to avoid. Saying nothing would repeat the mistake the video side
    already made once: a printed command that describes a different job from
    the one that would run (B04).
    """
    if not answers.get("reverse_audio"):
        return ""
    indices = audio_reverse_indices(answers, audio_indices)
    duration, content, _ranges = audio_reverse_content_seconds(answers)
    if duration <= 0:
        return ("the input duration could not be read, so its peak memory "
                "cannot be predicted")
    if content <= audio_reverse_segment_seconds(answers, indices):
        return ""
    streams = selected_audio_reverse_streams(answers, indices)
    per_second = sum(
        int(stream.get("sample_rate") or 0) * int(stream.get("channels") or 0)
        * decoded_bytes_per_sample(intermediate_audio_sample_fmt(stream.get("sample_fmt")))
        for stream in streams)
    return (f"`areverse` buffers the whole track, so running this command by "
            f"hand needs about {content * per_second / 1024 ** 3:.1f} GiB of RAM. "
            "Starting the job from FFmWiz reverses it in bounded lossless "
            "segments instead.")


def bounded_audio_reverse_to_file(
    answers: dict[str, Any],
    target: Path,
    *,
    audio_indices: list[int] | None = None,
    label: str = "Audio reverse",
) -> tuple[int, float]:
    """Write the selected tracks, cut and reversed, losslessly into `target`.

    Stages 1-3 of the bounded plan, factored out so a caller that has to mux
    the result back onto a picture -- the main encode, a Join, a Folder job --
    can reuse the exact reversal the standalone tools run instead of writing a
    second one that drifts from it.

    Refuses rather than falling back to an unbounded pass when the duration
    cannot be read or the plan would need more segments than the cap allows.
    """
    started_at = time.perf_counter()
    indices = audio_reverse_indices(answers, audio_indices)
    duration, content_seconds, keep_ranges = audio_reverse_content_seconds(answers)
    segment_seconds = audio_reverse_segment_seconds(answers, indices)
    streams = selected_audio_reverse_streams(answers, indices)

    if duration <= 0:
        # Nothing to size the plan from. Refusing beats running the unbounded
        # one-shot and hoping: areverse would buffer the whole track.
        appio.error(
            "The input duration could not be determined, so a bounded reverse "
            "cannot be planned. Remux or re-probe the file and try again.")
        log_warn("Bounded audio reverse refused: unknown source duration")
        return 1, time.perf_counter() - started_at

    segment_count = max(1, math.ceil(content_seconds / segment_seconds))
    if segment_count > AUDIO_REVERSE_MAX_SEGMENTS:
        appio.error(
            f"Reversing {content_seconds / 3600.0:.1f} h of this audio needs "
            f"{segment_count} bounded segments of {segment_seconds:.1f}s, above "
            f"the {AUDIO_REVERSE_MAX_SEGMENTS}-segment limit. Split the input "
            "into shorter pieces and reverse them separately.")
        log_warn(f"Bounded audio reverse refused: {segment_count} segments "
                 f"exceeds AUDIO_REVERSE_MAX_SEGMENTS={AUDIO_REVERSE_MAX_SEGMENTS}")
        return 1, time.perf_counter() - started_at

    appio.note(
        f"Reverse uses {segment_count} lossless segment(s) of up to "
        f"{segment_seconds:.1f}s so the whole track is never held in RAM.")
    with tempfile.TemporaryDirectory(prefix="ffmwiz_areverse_") as workspace_str:
        workspace = Path(workspace_str)
        forward = build_audio_reverse_forward_command(
            answers, indices, keep_ranges, segment_seconds,
            workspace / f"areverse_fwd_%05d.{INTERMEDIATE_CONTAINER_EXT}")
        log_info("Bounded audio reverse forward pass: " + command_to_powershell(forward))
        code, _elapsed = run_ffmpeg_with_progress(
            forward, total_duration=content_seconds,
            label=f"{label}: preparing lossless segments")
        if code != 0:
            return code, time.perf_counter() - started_at

        chunks = audio_reverse_chunk_paths(
            answers.get("ffprobe") or "ffprobe", workspace, "areverse_fwd_")
        if not chunks:
            appio.error("The bounded reverse produced no audio segments.")
            return 1, time.perf_counter() - started_at

        reversed_chunks: list[Path] = []
        for position, chunk in enumerate(chunks, start=1):
            chunk_target = workspace / f"areverse_rev_{position:05d}.{INTERMEDIATE_CONTAINER_EXT}"
            reversed_chunks.append(chunk_target)
            chunk_cmd = build_audio_reverse_chunk_command(answers, streams, chunk, chunk_target)
            log_info(f"Bounded audio reverse segment {position}/{len(chunks)}: "
                     + command_to_powershell(chunk_cmd))
            code, _elapsed = run_ffmpeg_with_progress(
                chunk_cmd, total_duration=segment_seconds,
                label=f"{label}: reversing segment {position}/{len(chunks)}")
            if code != 0:
                return code, time.perf_counter() - started_at

        concat_list = workspace / "areverse_concat.txt"
        # reverse(A||B) is reverse(B)||reverse(A), so joining the reversed
        # chunks in reverse order IS the whole-track reversal -- exactly, not
        # approximately.
        write_concat_list(list(reversed(reversed_chunks)), concat_list)
        concat_cmd = build_concat_copy_command(answers["ffmpeg"], concat_list, target)
        log_info("Bounded audio reverse concat: " + command_to_powershell(concat_cmd))
        code, _elapsed = run_ffmpeg_with_progress(
            concat_cmd, total_duration=content_seconds,
            label=f"{label}: joining the reversed segments")
        return code, time.perf_counter() - started_at


def run_bounded_audio_reverse(
    answers: dict[str, Any],
    rebuild: Callable[[dict[str, Any]], list[str]],
    *,
    label: str,
    audio_indices: list[int] | None = None,
    total_duration: float | None = None,
) -> tuple[int, float]:
    """Execute an audio reverse without ever buffering the whole track.

    The single bounded plan every audio-reverse entry point shares. `rebuild`
    is the SAME public builder that produced the one-shot command, called once
    more with the reversal and the cuts already applied, so the final encode
    settings cannot drift from the ones the summary showed.

    Order, and why:

    1. one forward decode into lossless chunks, cuts applied (continuous);
    2. reverse each chunk, lossless in and lossless out (bounded);
    3. concatenate the chunks in REVERSE order;
    4. the original job, re-pointed at the reversed scratch, applying only the
       filters whose semantics stay continuous: atempo, LoudNorm, resampling.
       Splitting one of those across chunks would change the result at every
       boundary, which is why the REVERSAL is staged and the filter graph is
       not.

    A job that already fits the budget skips all of it and runs the one-shot
    command the user was shown: it is bounded by construction, and staging it
    would cost an extra decode for nothing.
    """
    started_at = time.perf_counter()
    indices = audio_reverse_indices(answers, audio_indices)
    _duration, content_seconds, _ranges = audio_reverse_content_seconds(answers)
    # The stages that read the SOURCE timeline are paced by content_seconds; the
    # one that produces the user's file is paced by its own length, which a
    # speed change makes different.
    output_seconds = content_seconds if total_duration is None else float(total_duration)

    if not audio_reverse_needs_staging(answers, indices):
        log_info(f"Bounded audio reverse: {content_seconds:.1f}s fits the "
                 f"{audio_reverse_segment_seconds(answers, indices):.1f}s peak "
                 "budget; running in one pass")
        return run_ffmpeg_with_progress(
            answers["cmd"],
            total_duration=(output_seconds if output_seconds > 0 else None),
            label=label)

    output_path = Path(answers["output_path"])
    carries_picture = "-vn" not in audio_tool_picture_args(
        answers, str(answers.get("output_ext") or output_path.suffix.lstrip(".")))
    with tempfile.TemporaryDirectory(prefix="ffmwiz_arevjob_") as job_str:
        reversed_whole = Path(job_str) / f"areverse_whole.{INTERMEDIATE_CONTAINER_EXT}"
        code, _elapsed = bounded_audio_reverse_to_file(
            answers, reversed_whole, audio_indices=indices, label=label)
        if code != 0 or not reversed_whole.exists():
            return (code or 1), time.perf_counter() - started_at

        staged = staged_audio_reverse_answers(answers, indices, reversed_whole,
                                              carries_picture)
        final_cmd = [str(part) for part in rebuild(staged)]
        if carries_picture:
            # The cover art lives in the original file, not the scratch, so it
            # comes in as input 1 rather than being replicated into every chunk.
            insert_at = final_cmd.index("-i") + 2
            final_cmd[insert_at:insert_at] = ["-i", str(answers["input_path"])]
        # The user was shown ONE output path. The rebuild resolves its own from
        # a job that now has no cuts and no reverse, which names the file
        # differently.
        final_cmd[-1] = str(output_path)
        log_info("Bounded audio reverse final pass: " + command_to_powershell(final_cmd))
        code, _elapsed = run_ffmpeg_with_progress(
            final_cmd, total_duration=(output_seconds if output_seconds > 0 else None),
            label=label)
        answers["output_path"] = output_path
        return code, time.perf_counter() - started_at

__all__ = [
    'AUDIO_SAMPLE_FMT_BYTES',
    'AUDIO_REVERSE_MAX_SEGMENTS',
    'decoded_bytes_per_sample',
    'intermediate_audio_sample_fmt',
    'reverse_audio_segment_seconds_for',
    'reverse_audio_segment_seconds_for_streams',
    'selected_audio_reverse_streams',
    'audio_reverse_segment_seconds',
    'audio_reverse_indices',
    'audio_reverse_content_seconds',
    'audio_reverse_needs_staging',
    'audio_reverse_one_shot_warning',
    'bounded_audio_reverse_to_file',
    'lossless_scratch_audio_args',
    'audio_cut_only_filter_complex',
    'build_audio_reverse_forward_command',
    'build_audio_reverse_chunk_command',
    'audio_reverse_chunk_paths',
    'staged_audio_reverse_answers',
    'run_bounded_audio_reverse',
]
