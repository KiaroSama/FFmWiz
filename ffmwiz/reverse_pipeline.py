"""The staged reverse pipeline: plan it, export it, run it.

Split out of `encoding` as its own responsibility. Everything here exists
because `reverse` buffers every decoded frame it is given, so a Join, a Split
or a long single input cannot be reversed in one command -- the job is planned
as stages, the same plan is exported for manual use, and the executor runs it.

Facade names the tests monkeypatch are called through `encoding.<name>` rather
than resolved from this module's own globals, so a patch on the facade still
reaches the code that runs. `encoding` re-exports this module at its end, so
every existing `from ffmwiz.encoding import *` keeps working unchanged.
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
import logging
import atexit
import queue
import threading
import concurrent.futures
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, NamedTuple
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
from ffmwiz.support.L01_subtitles import *  # noqa: F401,F403
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
from ffmwiz.metadata import *  # noqa: F401,F403
from ffmwiz import metadata  # noqa: F401
from ffmwiz.modes import *  # noqa: F401,F403
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.trackmanager import *  # noqa: F401,F403
from ffmwiz.wizard import *  # noqa: F401,F403
from ffmwiz import wizard  # noqa: F401
# The facade back-import was deleted: it carried no name this module does
# not already get from the lower tiers above, and the facade ends with
# `__all__ += <this module>.__all__`, which it reached while this module
# was still on its first statements.
from ffmwiz.reverse_stages import *  # noqa: E402,F401,F403
from ffmwiz import reverse_stages  # noqa: E402,F401
from ffmwiz.support import L00_probe  # noqa: E402,F401  (defines join_item_picture_span)
from ffmwiz.support import L00_split  # noqa: E402,F401  (defines the chunk splitter)
from ffmwiz import runtime  # noqa: E402,F401  (defines run_ffmpeg_with_progress)
from ffmwiz import wizard_build  # noqa: E402,F401  (defines build_ffmpeg_command)
from ffmwiz import reverse_pipeline  # noqa: E402,F401  (own handle, so a patch of these names is seen)


def bounded_reverse_plan(answers: dict[str, Any],
                        workspace: Path) -> list[tuple[str, list[str]]]:
    """The staged commands this job WILL run, without running any of them.

    The summary printed the ordinary one-shot command, which for a Join or
    Split reverse carries a full-timeline `reverse` filter that execution never
    uses. Running it by hand buffers the whole timeline -- exactly what the
    staged plan exists to avoid -- so the printed command described a different
    job from the one that would run (B04).

    The intermediates are DESCRIBED rather than probed, because they do not
    exist yet: their geometry comes from the source and their codec/container
    from `intermediate_profile`, which is what actually produces them. The
    regression that keeps the two honest runs the exported plan in a fresh
    process and compares its media to the automatic path.
    """
    stages: list[tuple[str, list[str]]] = []
    extension = INTERMEDIATE_CONTAINER_EXT
    split_points = list(answers.get("separator_points") or [])
    planned_output_paths = list(answers.get("split_output_paths") or [])
    stage_source = answers

    def described(source_answers: dict[str, Any], produced: Path,
                  duration: float, writer: dict[str, Any]) -> dict[str, Any]:
        """The intermediate as the PRECEDING stage will actually write it.

        This used to invent one. It hardcoded every video stream to `h264`,
        emptied `subtitle_streams`, and -- the expensive part -- reused the
        SOURCE `format` dict, so the descriptor carried input 1's duration no
        matter what the stage before it had produced.

        Two user-visible failures came out of that one line. After a forward
        join of two 2 s inputs the plan reversed 2.023 s instead of 4.046, so
        the exported plan processed about one input:

            AUTO_DURATION   4.332   AUTO_COLORS   ['blue', 'red']
            MANUAL_DURATION 2.3     MANUAL_COLORS ['red', 'missing']

        And after a 0.5x speed change the reversed intermediate is twice as
        long, but the Split still cut against the source timeline, losing the
        second half:

            AUTO_PARTS   [('slow_Part01.mkv', 2.023), ('slow_Part02.mkv', 5.9)]
            MANUAL_PARTS [('slow_Part01.mkv', 2.023), ('slow_Part02.mkv', 2.04)]

        `duration` is now supplied by the caller, which knows what its stage
        does, and `writer` is the answers dict that WILL write the file, so
        every property comes from the same resolvers the command does rather
        than from a guess. An unknown duration is a planning error, not a value
        to invent: a plan that cannot describe its own intermediate must not be
        exported as if it could.

        Two properties used to be copied straight off the source and were
        wrong for the file being described.

        The CLOCK. An intermediate is written without `-copyts`, so the muxer
        rebases it to zero however skewed its source was -- but the source's
        `format.start_time` was carried onto it, and `-ss` is built from
        exactly that skew. On an MKV whose audio leads its picture by 522 ms:

            plan  -ss 0.522000 -t 5.000000
            run   no -ss,      -t 5.033000

        and the media the two wrote, 134 frames against 150, durations 4.664
        against 5.129, frame hashes and audio MD5 both unequal (D06). FFmpeg 8
        writes a zero start for ordinary fixtures, which hid it entirely on a
        modern build.

        The GEOMETRY, which `intermediate_video_descriptor` now owns (D07).
        """
        if not duration or duration <= 0:
            raise ValueError(
                f"cannot describe {produced.name}: the duration of the stage "
                "that writes it is unknown, so every later stage would be "
                "planned against the wrong timeline")
        rebased = dict(source_answers)
        rebased.pop("join_input_items", None)
        rebased["input_path"] = produced
        writes = reverse_stages.intermediate_video_descriptor(writer)
        rate = (str(Fraction(writes["fps"]).limit_denominator(1000000))
                if writes["fps"] else "")
        streams = [dict(stream) for stream in (source_answers.get("video_streams") or [])]
        for stream in streams:
            stream.update({"codec_name": writes["codec_name"],
                           "width": writes["width"], "height": writes["height"],
                           "pix_fmt": writes["pix_fmt"],
                           "duration": f"{duration:.6f}",
                           "start_time": "0.000000"})
            if rate:
                stream["avg_frame_rate"] = stream["r_frame_rate"] = rate
            # Both are duration-derived and now stale -- an fps change alone
            # invalidates the frame count. The stream duration above is the one
            # answer; a leftover count or Matroska DURATION tag would shadow it
            # the moment that answer went missing.
            stream.pop("nb_frames", None)
            if isinstance(stream.get("tags"), dict):
                stream["tags"] = {key: value for key, value in stream["tags"].items()
                                  if key.upper() != "DURATION"}
        rebased["video_streams"] = streams
        audio = [{**dict(stream), "codec_name": INTERMEDIATE_AUDIO_CODEC,
                  "duration": f"{duration:.6f}", "start_time": "0.000000"}
                 for stream in (source_answers.get("audio_streams") or [])]
        rebased["audio_streams"] = audio
        # Carried, not emptied: the forward join merges and retimes the source
        # subtitles into the intermediate, and the reverse stage carries them
        # through. Declaring none made the plan's later stages blind to a
        # track the file actually has.
        subtitles = [dict(stream) for stream in
                     (source_answers.get("subtitle_streams") or [])]
        rebased["subtitle_streams"] = subtitles
        fmt = dict(source_answers.get("format") or {})
        fmt["duration"] = f"{duration:.6f}"
        fmt["start_time"] = "0.000000"
        fmt["format_name"] = "matroska,webm"
        rebased["format"] = fmt
        rebased["probe"] = {"streams": streams + audio + subtitles, "format": fmt}
        return rebased

    # The SAME ownership the executor uses. Two copies of this decision is how
    # the exported plan drifted from the job it claimed to describe; keeping
    # the split identical is the point of validating both against one schema.
    has_join = bool(answers.get("join_input_items"))
    forward_owns = GEOMETRY_TRANSFORMATIONS if has_join else ()
    # `look` and `fade` belong to the stage that produces the reversed
    # timeline, not the forward join: `build_look_filters` runs after the
    # scale and `build_fade_filters` runs after the reverse, so a fade-in
    # applied by a forward stage would end up at the tail once the picture is
    # mirrored.
    reverse_owns = ("cuts", "audio_cuts", "video_speed", "audio_speed",
                    "video_reverse", "audio_reverse", "loudnorm",
                    "look", "fade", "volume")
    if not has_join:
        reverse_owns = reverse_owns + GEOMETRY_TRANSFORMATIONS
    # The raw options describe the FINAL file, so they belong to the LAST
    # stage that writes one -- Split when there is one, otherwise the reverse
    # -- and must never reach a scratch intermediate: `intermediate_profile`
    # strips the rate control from one, and an opaque argv is exactly what it
    # cannot strip.
    split_owns = ("split", "raw_args") if split_points else ()
    if not split_points:
        reverse_owns = reverse_owns + ("raw_args",)
    # Every `owns=` below takes one of these tuples. The split stage used to be
    # given a hand-written `("split",)` instead, so `validate_stage_plan`
    # approved a plan that owned `raw_args` while the code that built the stage
    # stripped the key -- the validator and the builder describing different
    # jobs is worse than either being wrong alone.
    validate_stage_plan([("forward join", forward_owns),
                         ("reverse", reverse_owns),
                         ("split", split_owns)],
                        answers)

    plan_items: list[dict[str, Any]] = []
    if has_join:
        items = join_items_from_answers(answers)
        plan_items = items
        subtitle_source_answers = forward_stage_answers = None
        if not items:
            return stages
        joined = workspace / f"joined_forward.{extension}"
        forward = intermediate_profile(reverse_stages.stage_answers(answers, owns=forward_owns))
        forward["output_path"] = joined
        subtitle_source_answers = forward
        stages.append(("Join the inputs forward",
                       [str(part) for part in
                        wizard.build_join_encode_command(forward, items, joined)]))
        # The JOINED length. The sum of the inputs' picture spans, PLUS the one
        # frame period the concat leaves at its first seam. Carrying input 1's
        # `format.duration` forward is what made the exported plan reverse one
        # input's worth of a multi-input timeline (D05); the seam is what made
        # it lose the last frame of whatever it did reverse (D06).
        #
        # The seam is measurable and it is exactly one frame, however many
        # inputs there are. Traced on 2 s + 3 s and on 2 s + 3 s + 1 s at
        # 30 fps, the joined picture runs to the summed span INCLUSIVE:
        #
        #     2+3  150 frames, pts 0.000..5.000, span tag 5.033
        #     2+3+1 180 frames, pts 0.000..6.000, span tag 6.033
        #     seam  ..., 1.933, 1.967, 2.033, ...   <- the 2.000 slot is empty
        #
        # `-t` is a half-open window, so a plan that stops at the summed span
        # stops ON the last frame's timestamp and drops it. The executor never
        # saw this because it probes the file and reads the span the container
        # reports, which already counts that frame: measured 150 frames against
        # the exported plan's 149.
        joined_seconds = sum(L00_probe.join_item_picture_span(item) for item in items)
        joined_fps = reverse_stages.intermediate_video_descriptor(forward)["fps"]
        # Only ever a correction to a length that is known. Adding it to an
        # unreadable timeline would turn "I cannot describe this" into a
        # confident one-frame plan, and `described()` refuses on zero for a
        # reason.
        if joined_seconds > 0 and joined_fps:
            joined_seconds += 1.0 / joined_fps
        stage_source = described(answers, joined, joined_seconds, forward)

    reverse_answers = reverse_stages.stage_answers(stage_source, owns=reverse_owns)
    reverse_answers.pop("join_input_items", None)
    # Hand the stage its subtitles instead of letting it try to extract them
    # from a file the plan has not written. Without this the planner emits
    # `-sn` and the exported plan silently drops tracks the run keeps.
    # The FORWARD stage's answers, not the job's. `build_joined_subtitle_files`
    # retimes by the timeline it is given, and the job's still says
    # `reverse_video`, so the merged cues came back already mirrored and the
    # reverse stage below mirrored them a second time -- a double reverse is
    # the identity, and the plan sliced a cue into the wrong Split part.
    # Traced: merged in [(2.2, 2.8, 'B'), (4.2, 4.8, 'A')], out
    # [(0.2, 0.8, 'A'), (2.2, 2.8, 'B')] -- 'B' looked untouched only because
    # it sits symmetrically on this fixture's 5 s timeline.
    plan_sources = plan_subtitle_sources(
        subtitle_source_answers if has_join and subtitle_source_answers else answers,
        plan_items, workspace)
    reverse_tracks = planned_retimed_subtitles(reverse_answers, plan_sources, workspace)
    reverse_answers["_prebuilt_retimed_subtitles"] = reverse_tracks
    if split_points:
        reversed_whole = workspace / f"reversed_whole.{extension}"
        reverse_answers = intermediate_profile(reverse_answers)
        reverse_answers["output_path"] = reversed_whole
        reverse_answers["output_location"] = workspace
        reverse_answers["output_name_stem"] = reversed_whole.stem
        reverse_answers["output_collision_suffix"] = ""
    else:
        reverse_answers["output_path"] = answers["output_path"]

    duration = reverse_source_seconds(reverse_answers)
    # What the reverse stage WRITES, not what it reads. Cuts shorten it and a
    # speed change stretches or compresses it; `reverse` leaves it alone. The
    # Split that follows must be planned against this, or a 0.5x job cuts the
    # 8 s result against the 4 s source and loses the second half (D06). Same
    # helper the real Split uses, so the two cannot drift.
    reversed_seconds = final_processed_duration_for_splits(reverse_answers, duration)
    keep_ranges = normalize_cut_ranges(
        list(reverse_answers.get("cut_keep_ranges") or []), duration)
    # The window the budget measured, tiled as measured. It used to be widened
    # on the way in: the splitter floored a rate-less step at 1 ms, which is
    # more than one frame above 1000 fps, so a plan whose own unit was one
    # 0.000833 s frame emitted 0.001 s chunks -- two frames, 2.206 GiB against
    # the 2 GiB cap (D08). The floor now stops at the command grid.
    chunks = L00_split.split_ranges_for_reverse_segments(
        keep_ranges, duration, reverse_stages.reverse_segment_seconds(reverse_answers))
    segment_ext = Path(reverse_answers["output_path"]).suffix.lstrip(".") or extension
    segments: list[Path] = []
    for index, (start, end) in enumerate(chunks, start=1):
        segment = workspace / f"reverse_encode_seg_{index:04d}.{segment_ext}"
        segments.append(segment)
        stages.append((
            f"Reverse segment {index}/{len(chunks)}",
            [str(part) for part in reverse_stages.build_main_encode_reverse_segment_command(
                reverse_answers, start, end, segment)]))

    # The SAME builder the executor uses, so the exported plan cannot describe
    # a different final mux. It writes its concat lists and chapter metadata
    # into the workspace, which is what makes the exported script runnable as
    # it stands.
    concat_stages, _plan_warnings = reverse_stages.reverse_concat_stages(
        reverse_answers, segments, workspace, Path(reverse_answers["output_path"]),
        encode_video_speed_factor(reverse_answers) or 1.0, segment_ext)
    stages.extend((label, cmd) for label, cmd, _progress in concat_stages)

    if split_points:
        split_answers = reverse_stages.stage_answers(
            described(answers, Path(reverse_answers["output_path"]),
                      reversed_seconds, reverse_answers), owns=split_owns)
        # The split reads the REVERSED intermediate, whose subtitle track is
        # what the stage above just wrote. Feed those cues forward rather than
        # extracting from a file that does not exist yet.
        split_answers["_prebuilt_retimed_subtitles"] = planned_retimed_subtitles(
            split_answers,
            [{"text": Path(track["path"]).read_text(encoding="utf-8"),
              "index": track["source_index"], "language": track.get("language", ""),
              "title": track.get("title", ""), "default": track.get("default"),
              "forced": track.get("forced")}
             for track in reverse_tracks if Path(track["path"]).exists()],
            workspace / "split")
        split_answers.pop("split_output_paths", None)
        split_answers.pop("split_part_intervals", None)
        split_answers["separator_points"] = split_points
        resolved_stem = str(answers.get("output_name_stem") or "").strip()
        if not resolved_stem and planned_output_paths:
            resolved_stem = re.sub(r"_Part\d+$", "", Path(planned_output_paths[0]).stem)
        if not resolved_stem:
            resolved_stem = Path(answers["input_path"]).stem
        split_answers["output_name_stem"] = resolved_stem
        stages.append(("Split the reversed result",
                       [str(part) for part in wizard_build.build_ffmpeg_command(split_answers)]))
    return stages


class PlanExport(NamedTuple):
    """The outcome of exporting a staged plan, with failure distinguishable.

    It used to be `Path | None`, and `None` meant BOTH "this job is not staged"
    and "the export failed". The caller could only tell them apart by guessing,
    so a failed export fell through to the generic

        FFmpeg was not started. The command above is ready to run manually.

    which is false exactly when it matters: for a staged Join or Split reverse
    the printed one-shot command is a readable reference that would buffer the
    whole timeline, and the runnable thing was the plan file that had just
    failed to be written (D08).
    """
    script: Path | None
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.script is not None


def export_bounded_reverse_plan(answers: dict[str, Any],
                                destination: Path) -> PlanExport:
    """Write the staged plan as a runnable PowerShell script.

    A multi-stage job has no single "final command", so exporting one and
    labelling it that way is what made the manual path wrong. The script stops
    on the first failure and names the scratch directory the user has to remove
    afterwards, because the generated inputs are deliberately preserved.

    Returns a `PlanExport`. A caller that only wants the path reads `.script`;
    a caller that has to tell the user something truthful reads `.error` too.
    """
    stem = re.sub(r"_Part\d+$", "", Path(destination).stem)
    workspace = Path(destination).parent / f"{stem}_plan"
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        stages = reverse_pipeline.bounded_reverse_plan(answers, workspace)
    except Exception as exc:  # a plan we cannot describe must not break the run
        log_warn(f"Could not export the staged reverse plan: {exc}")
        return PlanExport(None, str(exc) or exc.__class__.__name__)
    if not stages:
        return PlanExport(None, "the staged plan produced no commands")
    script = Path(destination).parent / f"{stem}.plan.ps1"
    lines = [
        "# FFmWiz staged reverse plan.",
        "# This job runs as several FFmpeg commands, in this order. The single",
        "# command shown in the summary is a readable reference only: running it",
        "# would buffer the whole timeline, which is what the staged plan avoids.",
        "$ErrorActionPreference = 'Stop'",
        "",
    ]
    for label, cmd in stages:
        lines.append(f"# {label}")
        if cmd and cmd[0].startswith("<"):
            lines.append(f"#   {cmd[0]} -- FFmWiz writes this list at run time")
        else:
            # `&` is required: command_to_powershell quotes the executable for
            # DISPLAY, and PowerShell parses a bare quoted string followed by
            # arguments as an expression, not a command.
            lines.append("& " + command_to_powershell(cmd))
            lines.append("if ($LASTEXITCODE -ne 0) { throw '"
                         + label.replace("'", "''") + " failed' }")
        lines.append("")
    lines.append(f"# Scratch files live in: {workspace}")
    lines.append("# Remove that directory once the outputs are correct.")
    try:
        script.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    except OSError as exc:
        # A read-only directory or a locked file is the ordinary case here, and
        # it must NOT be reported to the user as "not staged".
        log_warn(f"Could not write the staged reverse plan to {script}: {exc}")
        return PlanExport(None, f"could not write {script.name}: {exc}")
    log_info(f"Exported staged reverse plan with {len(stages)} stage(s) to {script}")
    return PlanExport(script)


def run_bounded_reverse_pipeline(answers: dict[str, Any]) -> tuple[int, float]:
    """Reverse without ever handing the filter a whole timeline (F10).

    `reverse` cannot emit a frame until it has buffered every decoded frame of
    its input, so the only safe shape is to give it one bounded segment at a
    time -- never a joined program, never a to-be-split one. Three stages, each
    skipped when it does not apply:

      1. JOIN the inputs forward into a leased intermediate. The segmented
         executor cannot be aimed at a join directly: it rebuilds each segment
         with the SINGLE-input builder, which reverses input 1 alone (R01).
      2. REVERSE that single input in segments sized against the frame budget,
         applying the cuts and speed along with it.
      3. SPLIT the reversed result. Split points are chosen on the final
         processed timeline, which is exactly what stage 2 produced, so the
         parts fall where the summary said they would.

    Measured: an hour of joined 1080p30 is roughly 336 GiB of decoded frames in
    one pass, and ten minutes of single-input 1080p30 with a split is about
    56 GiB -- neither can finish. Each intermediate costs one extra encode and
    is written at a visually lossless quality, then removed with the rest of the
    job's temporary files.
    """
    started_at = time.perf_counter()
    extension = str(answers.get("output_ext") or "mkv").lstrip(".")
    workspace = artifact_lease(answers).register(
        Path(tempfile.mkdtemp(prefix="ffmwiz_reverse_pipeline_")))
    split_points = list(answers.get("separator_points") or [])
    # Captured BEFORE any stage rewrites them: these are the paths the summary
    # showed and the user confirmed.
    planned_output_paths = list(answers.get("split_output_paths") or [])
    stage_source = answers

    # Geometry is owned by the FIRST stage that writes a picture: the forward
    # join when there is one, otherwise the reverse encode. Later stages read
    # an already-cropped, already-resized intermediate, so re-applying would
    # crop the crop (D01/D02).
    has_join = bool(answers.get("join_input_items"))
    forward_owns = GEOMETRY_TRANSFORMATIONS if has_join else ()
    # `look` and `fade` belong to the stage that produces the reversed
    # timeline, not the forward join: `build_look_filters` runs after the
    # scale and `build_fade_filters` runs after the reverse, so a fade-in
    # applied by a forward stage would end up at the tail once the picture is
    # mirrored.
    reverse_owns = ("cuts", "audio_cuts", "video_speed", "audio_speed",
                    "video_reverse", "audio_reverse", "loudnorm",
                    "look", "fade", "volume")
    if not has_join:
        reverse_owns = reverse_owns + GEOMETRY_TRANSFORMATIONS
    # The raw options describe the FINAL file, so they belong to the LAST
    # stage that writes one -- Split when there is one, otherwise the reverse
    # -- and must never reach a scratch intermediate: `intermediate_profile`
    # strips the rate control from one, and an opaque argv is exactly what it
    # cannot strip.
    split_owns = ("split", "raw_args") if split_points else ()
    if not split_points:
        reverse_owns = reverse_owns + ("raw_args",)
    # Before the join, not after: a plan that cannot be executed correctly must
    # not spend a full forward encode first.
    validate_stage_plan([("forward join", forward_owns),
                         ("reverse", reverse_owns),
                         ("split", split_owns)],
                        answers)

    if has_join:
        items = join_items_from_answers(answers)
        if not items:
            return 1, time.perf_counter() - started_at
        joined = workspace / f"joined_forward.{INTERMEDIATE_CONTAINER_EXT}"
        # Owns the GEOMETRY and nothing else. Clearing only the video edits
        # left an independent audio speed to be applied here AND by the reverse
        # stage AND by the split (B01); leaving geometry unowned did the same
        # to crop, fps and resize (D01/D02).
        forward = intermediate_profile(reverse_stages.stage_answers(answers, owns=forward_owns))
        forward["output_path"] = joined
        forward_cmd = wizard.build_join_encode_command(forward, items, joined)
        appio.note("Reverse across a join: joining first, then reversing in bounded "
                   "segments so the whole joined timeline is never held in RAM.")
        log_info(f"Bounded reverse pipeline: forward join -> {joined}")
        code, _elapsed = runtime.run_ffmpeg_with_progress(
            forward_cmd,
            total_duration=sum(float(item.get("duration") or 0.0) for item in items) or None,
            label="Joining before reverse")
        if code != 0 or not joined.exists():
            return (code or 1), time.perf_counter() - started_at
        stage_source = _single_input_answers(answers, joined)

    # Owns everything except the split -- and the geometry too when no forward
    # join already applied it.
    reverse_answers = reverse_stages.stage_answers(stage_source, owns=reverse_owns)
    reverse_answers.pop("join_input_items", None)
    if split_points:
        # Split AFTER the reverse: reversing each part separately would return
        # the parts in their original order, and reversing the whole thing at
        # once is the unbounded plan this exists to avoid.
        pass
        reversed_whole = workspace / f"reversed_whole.{INTERMEDIATE_CONTAINER_EXT}"
        reverse_answers = intermediate_profile(reverse_answers)
        reverse_answers["output_path"] = reversed_whole
        reverse_answers["output_location"] = workspace
        reverse_answers["output_name_stem"] = reversed_whole.stem
        reverse_answers["output_collision_suffix"] = ""
        appio.note("Reverse with Split: reversing the whole timeline in bounded "
                   "segments first, then cutting the parts out of the result.")
    else:
        reverse_answers["output_path"] = answers["output_path"]
    reverse_answers["cmd"] = wizard_build.build_ffmpeg_command(dict(reverse_answers))
    code, _elapsed = reverse_pipeline.run_segmented_reverse_main_encode(reverse_answers)
    if code != 0 or not split_points:
        return code, time.perf_counter() - started_at

    reversed_whole = Path(reverse_answers["output_path"])
    if not reversed_whole.exists():
        return 1, time.perf_counter() - started_at
    # Owns the split alone. Stage 2 already applied every other edit; leaving
    # any of them here would apply it a second (or third) time.
    split_answers = reverse_stages.stage_answers(
        _single_input_answers(answers, reversed_whole), owns=split_owns)
    split_answers.pop("split_output_paths", None)
    split_answers.pop("split_part_intervals", None)
    split_answers["separator_points"] = split_points
    # Keep the part filenames the summary already showed the user. Taking the
    # stem from input_path instead promised CustomMovie_Part01.mkv and wrote
    # a_Part01.mkv, because by this point input_path is the pipeline's own
    # scratch file (B14). Prefer the user's stem, then the stem the first build
    # already resolved, and only then the source name.
    resolved_stem = str(answers.get("output_name_stem") or "").strip()
    if not resolved_stem:
        planned = [Path(part) for part in (planned_output_paths or [])]
        if planned:
            resolved_stem = re.sub(r"_Part\d+$", "", planned[0].stem)
    if not resolved_stem:
        resolved_stem = Path(answers["input_path"]).stem
    split_answers["output_name_stem"] = resolved_stem
    split_cmd = wizard_build.build_ffmpeg_command(split_answers)
    log_info(f"Bounded reverse pipeline: splitting {reversed_whole} into "
             f"{len(split_answers.get('split_output_paths') or [])} part(s)")
    code, _elapsed = runtime.run_ffmpeg_with_progress(
        split_cmd,
        total_duration=services.stream_duration_seconds({}, split_answers.get("format")),
        label="Splitting the reversed result")
    answers["split_output_paths"] = split_answers.get("split_output_paths")
    return code, time.perf_counter() - started_at


def plan_subtitle_sources(answers: dict[str, Any], items: list[dict[str, Any]],
                          workspace: Path) -> list[dict[str, Any]]:
    """The cue TEXT of every selected track, read from files that exist NOW.

    For a join that is the merged track the forward stage will write, built
    here from the sources with the same helper the join builder uses; for a
    single input it is the source's own track. Either way the plan never has to
    read a file it has not produced.
    """
    if not (source_subtitles_keep_enabled(answers) and answers.get("subtitle_streams")):
        return []
    sources: list[dict[str, Any]] = []
    try:
        # The planner may be called with a workspace nobody has created yet.
        workspace.mkdir(parents=True, exist_ok=True)
        if items:
            for merged in wizard.build_joined_subtitle_files(answers, items):
                path = Path(merged["path"])
                if path.exists():
                    sources.append({"text": path.read_text(encoding="utf-8"),
                                    "index": int(merged.get("index") or 0),
                                    "language": merged.get("language", ""),
                                    "title": merged.get("title", ""),
                                    "default": bool(merged.get("default")),
                                    "forced": bool(merged.get("forced"))})
            return sources
        streams = list(answers.get("subtitle_streams") or [])
        origin = subtitle_source_origin(answers)
        for index in selected_subtitle_streams(answers):
            index = int(index)
            if not (0 <= index < len(streams)) or not is_text_subtitle(streams[index]):
                continue
            raw = workspace / f"plan_source{index:02d}.srt"
            text = extract_subtitle_text(
                answers.get("ffmpeg") or "ffmpeg", Path(answers["input_path"]),
                index, raw, "Planned subtitles", origin, answers.get("ffprobe"))
            if text:
                sources.append({"text": text, "index": index,
                                **subtitle_track_metadata(streams[index])})
    except Exception as exc:  # a plan must not fail over a subtitle it cannot read
        log_warn(f"Planned subtitles: could not read the source tracks: {exc}")
    return sources


def planned_retimed_subtitles(stage: dict[str, Any], sources: list[dict[str, Any]],
                              workspace: Path) -> list[dict[str, Any]]:
    """Retimed subtitle tracks for a stage whose INPUT does not exist yet.

    `build_retimed_subtitle_inputs()` extracts the cues from the file the stage
    will read. That works for the executor, which has just written it, and not
    at all for the exported plan: the planner's reverse stage reads
    `joined_forward.mkv` and its split stage reads `reversed_whole.mkv`, so
    extraction found nothing and the stage was emitted with `-sn`. The plan
    therefore DROPPED subtitles a staged reverse keeps -- planned against
    executed, that is `['-sn']` where the run has
    `['-c:s', '-disposition:s:0', '-metadata:s:s', '1:s:0', 'copy',
    'retimed00.srt']`.

    The cues themselves are never in doubt: the SOURCES exist at plan time.
    `sources` is [{"text", "index", metadata...}] already merged for a join,
    and this applies the stage's own `TimelineMap` to them and writes the
    result into the workspace, which is also what makes the exported script
    runnable as it stands.
    """
    if not sources:
        return []
    workspace.mkdir(parents=True, exist_ok=True)
    timeline = encode_timeline_map(stage)
    built: list[dict[str, Any]] = []
    for source in sources:
        cues = retime_cues(parse_srt(source.get("text") or ""), timeline)
        if not cues:
            continue
        index = int(source.get("index") or 0)
        path = workspace / f"retimed{index:02d}.srt"
        path.write_text(render_srt(cues), encoding="utf-8", newline="\n")
        built.append({"path": path, "source_index": index,
                      "language": source.get("language", ""),
                      "title": source.get("title", ""),
                      "default": bool(source.get("default")),
                      "forced": bool(source.get("forced"))})
    return built


def reverse_source_seconds(answers: dict[str, Any]) -> float:
    """The PICTURE span of the file a reverse stage will decode.

    The executor probes the intermediate it just wrote and read
    `format.duration`; the planner describes the same file from the inputs'
    picture spans. On a joined pair that is 5.039 against 5.000 -- the AAC tail
    the container carries past the last frame -- so the two bounded their
    reverse segments differently for the same job. Everything else in this
    pipeline was moved onto the picture clock already (B07/B08); this is the
    read that was left behind.
    """
    streams = list(answers.get("video_streams") or [])
    fmt = answers.get("format") or {}
    if streams:
        span = video_stream_span_seconds(streams[0], fmt)
        if span:
            return span
    return services.stream_duration_seconds({}, fmt) or 0.0


def run_segmented_reverse_main_encode(answers: dict[str, Any]) -> tuple[int, float]:
    duration = reverse_source_seconds(answers)
    if duration <= 0:
        return runtime.run_ffmpeg_with_progress(
            answers["cmd"],
            total_duration=None,
            label="FFmpeg encode",
        )
    original_keep_ranges = normalize_cut_ranges(list(answers.get("cut_keep_ranges") or []), duration)
    budget = reverse_stages.reverse_segment_plan_for(answers)
    segment_seconds = budget.seconds
    # Tiled at the size the budget measured. The splitter used to floor a
    # rate-less step at 1 ms, which is more than one frame above 1000 fps: at
    # 15360x8640, 1200 fps, yuv444p12le this tiled a 1 s timeline into 1000
    # chunks of two frames -- 2.206 GiB against the 2 GiB cap -- where the
    # budget's own unit is one frame of 0.000833 s and 1.353 GiB (D08).
    chunks = L00_split.split_ranges_for_reverse_segments(original_keep_ranges, duration,
                                               segment_seconds)
    if not chunks:
        return 1, 0.0
    speed = encode_video_speed_factor(answers)
    output_path = Path(answers["output_path"])
    # `{segment_seconds:.0f}s` printed every legitimate subsecond budget as
    # `0s` -- a 233 ms window at 8K60 10-bit announced as if it were nothing
    # (D09). `window_text` gives milliseconds and the frame count.
    appio.note(
        f"Reverse encode uses {len(chunks)} segment(s) of up to "
        f"{budget.window_text} to avoid buffering the full video in RAM."
    )
    if not budget.hard_capped:
        appio.note("This reverse is BEST-EFFORT, not hard-capped: "
                   + "; ".join(budget.assumptions))
    started_at = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ffmwiz_reverse_encode_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        segment_ext = output_path.suffix.lstrip(".") or str(answers.get("output_ext") or "mp4")
        segment_paths: list[Path] = []
        for idx, (start, end) in enumerate(chunks, start=1):
            segment_path = tmpdir / f"reverse_encode_seg_{idx:04d}.{segment_ext}"
            segment_paths.append(segment_path)
            cmd = reverse_stages.build_main_encode_reverse_segment_command(answers, start, end, segment_path)
            log_info(f"Reverse encode segment {idx}/{len(chunks)} command: {command_to_powershell(cmd)}")
            appio.note(f"Reverse encode segment {idx}/{len(chunks)}: {seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)}")
            rc, _ = runtime.run_ffmpeg_with_progress(
                cmd,
                total_duration=max(0.001, (end - start) / speed),
                label=f"Reverse encode segment {idx}/{len(chunks)}",
            )
            if rc != 0:
                return rc, time.perf_counter() - started_at
        # Concatenating reversed(segments) reverses the order of their AUDIO
        # blocks too. That is right when the audio follows the video reverse and
        # wrong when the user declined it: measured on 440 Hz for 0-2 s and
        # 880 Hz for 2-4 s with reverse_audio=False, the output played 880 Hz at
        # 0.4 s and 440 Hz at 3.2 s -- chunk-reordered without a single
        # `areverse` in any command (B02).
        #
        # So the two timelines are concatenated separately: video from the
        # reversed order, audio from the forward one, then muxed. Both passes
        # are stream copies, so this costs no extra encode.
        stages, mux_warnings = reverse_stages.reverse_concat_stages(
            answers, segment_paths, tmpdir, output_path, speed, segment_ext,
            progress_seconds=(total_keep_duration(chunks) / speed if chunks else None))
        if not stages:
            return 1, time.perf_counter() - started_at
        if len(stages) > 1:
            appio.note("Video reverse only: the audio keeps its own order and is "
                       "muxed back onto the reversed picture.")
        for warning in mux_warnings:
            appio.note(warning)
            log_warn(warning)
        rc = 0
        for label, cmd, progress in stages:
            log_info(f"{label}: " + command_to_powershell(cmd))
            appio.note(f"{label}...")
            rc, _ = runtime.run_ffmpeg_with_progress(cmd, total_duration=progress, label=label)
            if rc != 0:
                return rc, time.perf_counter() - started_at
        return rc, time.perf_counter() - started_at


__all__ = [
    'PlanExport',
    'bounded_reverse_plan',
    'export_bounded_reverse_plan',
    'plan_subtitle_sources',
    'planned_retimed_subtitles',
    'reverse_source_seconds',
    'run_bounded_reverse_pipeline',
    'run_segmented_reverse_main_encode',
]
__all__ += reverse_stages.__all__
