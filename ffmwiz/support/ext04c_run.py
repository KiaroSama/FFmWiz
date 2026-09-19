"""Running the bounded audio reverse: the passes, the workspace, the reassembly.

`ext04c` decides WHAT the bounded plan is -- how many segments, how long each
one may be, which streams and which commands. This module is what executes it:
it spawns the FFmpeg passes, owns the scratch workspace, checks that what came
back matches what was written, and refuses rather than falling back to an
unbounded `areverse`.

Split from `ext04c` when that file reached the size ceiling, along the boundary
that was already there: everything above is arithmetic over the answers dict,
everything here spawns processes and touches the filesystem.

Every planning call goes through `ext04c.<name>` deliberately. Resolving them
from this module's own globals would make the plan unpatchable from outside,
and forcing a multi-segment plan by replacing `ext04c.audio_reverse_segment_seconds`
is how the reverse suites test the staged path without a multi-hour fixture.
"""
from __future__ import annotations

import math
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from ffmwiz import appio
from ffmwiz import services  # noqa: F401 - used by the staged reassembly below
from ffmwiz.appio import log_info, log_warn
from ffmwiz.core.constants import INTERMEDIATE_CONTAINER_EXT
from ffmwiz.runtime import run_ffmpeg_with_progress
from ffmwiz.support import ext04c
from ffmwiz.support.ext04c import AUDIO_REVERSE_MAX_SEGMENTS
from ffmwiz.support.L00_misc import write_concat_list
from ffmwiz.support.L00_misc_b import build_audio_concat_filter_command
from ffmwiz.support.L01_cover import audio_tool_picture_args
from ffmwiz.support.L01_misc import command_to_powershell


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
    indices = ext04c.audio_reverse_indices(answers, audio_indices)
    duration, content_seconds, keep_ranges = ext04c.audio_reverse_content_seconds(answers)
    segment_seconds = ext04c.audio_reverse_segment_seconds(answers, indices)
    streams = ext04c.selected_audio_reverse_streams(answers, indices)

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
        forward = ext04c.build_audio_reverse_forward_command(
            answers, indices, keep_ranges, segment_seconds,
            workspace / f"areverse_fwd_%05d.{INTERMEDIATE_CONTAINER_EXT}")
        log_info("Bounded audio reverse forward pass: " + command_to_powershell(forward))
        code, _elapsed = run_ffmpeg_with_progress(
            forward, total_duration=content_seconds,
            label=f"{label}: preparing lossless segments")
        if code != 0:
            return code, time.perf_counter() - started_at

        chunks = ext04c.audio_reverse_chunk_paths(
            answers.get("ffprobe") or "ffprobe", workspace, "areverse_fwd_")
        if not chunks:
            appio.error("The bounded reverse produced no audio segments.")
            return 1, time.perf_counter() - started_at
        # Nothing used to compare what came back with what was written.
        # Losing chunks fails no step -- the concat joins whatever it is
        # handed -- so short audio reached the user with exit code 0.
        #
        # The bound is the muxer's OUTPUT, not `segment_count`: that is
        # `ceil(content / segment)` and over-counts whenever the content
        # divides evenly (measured: 9 planned, 8 written). What the
        # docstring of the collector actually promises is that exactly one
        # trailing stub may be dropped, so that is what is checked.
        written = sorted(workspace.glob("areverse_fwd_*"))
        log_info("Bounded audio reverse forward pass wrote "
                 f"{len(written)} file(s) for a {segment_count}-segment plan: "
                 + ", ".join(f"{path.name}={path.stat().st_size}B" for path in written))
        # The muxer writing FEWER files than planned is its own failure, and it
        # leaves nothing for the drop check below to notice: no file is
        # discarded, so the concat simply joins the one piece that exists and
        # the result is a fraction of the track. Measured on CI: 21776 samples
        # of an expected 176400, i.e. a single ~0.5 s segment, with exit 0.
        # `segment_count` is ceil() so one fewer is normal; anything below that
        # is the split having failed.
        if len(written) < segment_count - 1:
            appio.error(
                f"The bounded reverse split the track into {len(written)} "
                f"segment(s) where {segment_count} were planned, so the result "
                "would be short. Refusing to write truncated audio.")
            log_warn(f"Bounded audio reverse aborted: the segment muxer wrote "
                     f"{len(written)} of ~{segment_count} expected files")
            return 1, time.perf_counter() - started_at
        dropped = [path for path in written if path not in chunks]
        if len(dropped) > 1 or (dropped and dropped[0] != written[-1]):
            appio.error(
                f"The bounded reverse discarded {len(dropped)} of the "
                f"{len(written)} audio segments it wrote, so the result would "
                "be short. Refusing to write truncated audio.")
            log_warn("Bounded audio reverse aborted: dropped "
                     f"{[path.name for path in dropped]} of {len(written)} chunks")
            return 1, time.perf_counter() - started_at

        reversed_chunks: list[Path] = []
        for position, chunk in enumerate(chunks, start=1):
            chunk_target = workspace / f"areverse_rev_{position:05d}.{INTERMEDIATE_CONTAINER_EXT}"
            reversed_chunks.append(chunk_target)
            chunk_cmd = ext04c.build_audio_reverse_chunk_command(answers, streams, chunk, chunk_target)
            log_info(f"Bounded audio reverse segment {position}/{len(chunks)}: "
                     + command_to_powershell(chunk_cmd))
            code, _elapsed = run_ffmpeg_with_progress(
                chunk_cmd, total_duration=segment_seconds,
                label=f"{label}: reversing segment {position}/{len(chunks)}")
            if code != 0:
                return code, time.perf_counter() - started_at

        # The concat DEMUXER cannot join these: they are FLAC, each with its
        # own STREAMINFO, and it applies the first part's header to all of
        # them -- the rest fail to decode and vanish without a non-zero exit.
        # The list is still written, because the exported plan shows it and
        # the ORDER is the part that matters.
        concat_list = workspace / "areverse_concat.txt"
        # reverse(A||B) is reverse(B)||reverse(A), so joining the reversed
        # chunks in reverse order IS the whole-track reversal -- exactly, not
        # approximately.
        write_concat_list(list(reversed(reversed_chunks)), concat_list)
        concat_cmd = build_audio_concat_filter_command(
            answers["ffmpeg"], list(reversed(reversed_chunks)), target,
            tracks=max(1, len(indices)))
        log_info("Bounded audio reverse concat: " + command_to_powershell(concat_cmd))
        code, _elapsed = run_ffmpeg_with_progress(
            concat_cmd, total_duration=content_seconds,
            label=f"{label}: joining the reversed segments")
        if code != 0:
            return code, time.perf_counter() - started_at

        # MEASURE THE RESULT. Every step above checks its own exit code and
        # every one of them can succeed while the track comes out short --
        # the concat demuxer in particular exits 0 for whatever it managed to
        # read. Nothing had ever compared the joined length with the length
        # that went in, so a truncated reverse reached the user as a success.
        try:
            joined = services.stream_duration_seconds(
                {}, (services.ffprobe_json(answers.get("ffprobe") or "ffprobe",
                                          target) or {}).get("format") or {}) or 0.0
        except Exception:  # noqa: BLE001 - an unreadable result is a failure too
            joined = 0.0
        # A tenth of a second of slack: the last chunk is a remainder and a
        # lossless round trip lands a frame either side, never a segment.
        if content_seconds > 0 and joined < content_seconds - 0.1:
            appio.error(
                f"The bounded reverse joined {joined:.3f}s of audio from a "
                f"{content_seconds:.3f}s track across {len(reversed_chunks)} "
                "segment(s). Refusing to report a truncated result as success.")
            log_warn(f"Bounded audio reverse aborted: joined {joined:.3f}s of "
                     f"{content_seconds:.3f}s from {len(reversed_chunks)} chunks; "
                     "chunk sizes: "
                     + ", ".join(f"{c.name}={c.stat().st_size}B"
                                 for c in reversed_chunks if c.exists()))
            return 1, time.perf_counter() - started_at
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
    indices = ext04c.audio_reverse_indices(answers, audio_indices)
    _duration, content_seconds, _ranges = ext04c.audio_reverse_content_seconds(answers)
    # The stages that read the SOURCE timeline are paced by content_seconds; the
    # one that produces the user's file is paced by its own length, which a
    # speed change makes different.
    output_seconds = content_seconds if total_duration is None else float(total_duration)

    if not ext04c.audio_reverse_needs_staging(answers, indices):
        log_info(f"Bounded audio reverse: {content_seconds:.1f}s fits the "
                 f"{ext04c.audio_reverse_segment_seconds(answers, indices):.1f}s peak "
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

        staged = ext04c.staged_audio_reverse_answers(answers, indices, reversed_whole,
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
    'bounded_audio_reverse_to_file',
    'run_bounded_audio_reverse',
]
