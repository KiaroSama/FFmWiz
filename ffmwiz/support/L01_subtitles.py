"""FFmWiz subtitle timeline maths (dependency level 1).

Subtitle packets are not touched by the video/audio filter graph, so every
timeline edit the encode performs has to be replayed on the cues by hand. Both
users of that arithmetic live here:

* `TimelineMap` -- the single source->output transform for cuts, speed and
  reverse. A cut collapses the retained ranges, reverse mirrors the retained
  clock, speed divides it. Split reuses it unchanged, because a Split part is
  rebuilt as its own single-input job with that part's keep ranges.
* the joined-timeline assembly -- FFmpeg's `concat` filter handles video and
  audio but NOT subtitles, so a joined track is built by shifting each input's
  cues by the duration of everything before it and feeding the result back as
  an extra input.

Only TEXT subtitles can be retimed or assembled. A bitmap track (PGS, VobSub,
DVB) is a picture stream with no cue text to move, so it is reported explicitly
rather than mapped with source timestamps that no longer match the picture.
"""
from __future__ import annotations

import re
from typing import Any, Callable  # noqa: F401

from ffmwiz.core.constants import *  # noqa: F401,F403
from ffmwiz.core.exceptions import *  # noqa: F401,F403
from ffmwiz.core.timeline import *  # noqa: F401,F403
from ffmwiz.support.L00_misc import *  # noqa: F401,F403
from ffmwiz.support.L00_probe import *  # noqa: F401,F403
from ffmwiz.support.L00_streams import *  # noqa: F401,F403


# "00:01:02,500 --> 00:01:05,000" (SRT uses a comma for the decimal separator)
_SRT_TIME = r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
SRT_CUE_RE = re.compile(rf"^\s*{_SRT_TIME}\s*-->\s*{_SRT_TIME}", re.M)


def srt_timestamp(seconds: float) -> str:
    """Seconds -> `HH:MM:SS,mmm`."""
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total_ms, 3600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    """Parse SRT into (start, end, body) cues.

    Tolerant on purpose: a missing or duplicated index line, CRLF endings and a
    dot instead of a comma all appear in real files, and none of them should
    cost the user their subtitles.
    """
    cues: list[tuple[float, float, str]] = []
    blocks = re.split(r"\r?\n\s*\r?\n", (text or "").replace("﻿", ""))
    for block in blocks:
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        timing_index = None
        for index, line in enumerate(lines):
            if SRT_CUE_RE.match(line):
                timing_index = index
                break
        if timing_index is None:
            continue
        match = SRT_CUE_RE.match(lines[timing_index])
        start = (int(match.group(1)) * 3600 + int(match.group(2)) * 60
                 + int(match.group(3)) + int(match.group(4).ljust(3, "0")) / 1000.0)
        end = (int(match.group(5)) * 3600 + int(match.group(6)) * 60
               + int(match.group(7)) + int(match.group(8).ljust(3, "0")) / 1000.0)
        body = "\n".join(lines[timing_index + 1:]).strip()
        if body and end > start:
            cues.append((start, end, body))
    return cues


def shift_cues(cues: list[tuple[float, float, str]], offset: float,
               limit: float | None = None) -> list[tuple[float, float, str]]:
    """Move cues onto the joined timeline.

    `limit` is the input's own duration: a cue that runs past the end of its
    segment would otherwise overlap the next input's dialogue, so it is clipped.
    Clipping also drops a cue starting at or past the end -- it collapses to
    zero length, which the emptiness check below discards.
    """
    shifted: list[tuple[float, float, str]] = []
    for start, end, body in cues:
        if limit is not None:
            end = min(end, limit)
        if end <= start:
            continue
        shifted.append((start + offset, end + offset, body))
    return shifted


def render_srt(cues: list[tuple[float, float, str]]) -> str:
    """Cues -> SRT text, renumbered from 1 in time order."""
    parts: list[str] = []
    for index, (start, end, body) in enumerate(sorted(cues, key=lambda cue: (cue[0], cue[1])), start=1):
        parts.append(f"{index}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{body}\n")
    return "\n".join(parts)


class TimelineMap:
    """Source seconds -> processed-output seconds for one encode.

    Cut, speed and reverse all move the same clock, and the picture, the
    container duration and the cues have to agree on where it ended up. Keeping
    the arithmetic in one object is what stops each of them growing its own
    slightly different version.

    Applied in the order FFmpeg applies it: the retained ranges are collapsed
    first (`-ss`/`-t` or trim+concat), `reverse` then mirrors the retained
    clock, and `setpts=PTS/speed` divides it.

    Split needs nothing extra: a Split part is rebuilt as its own single-input
    job whose `cut_keep_ranges` are that part's retained ranges, so it is
    already just another set of keep ranges here.
    """

    def __init__(self, keep_ranges: Any = None, source_duration: float = 0.0,
                 speed: float = 1.0, reverse: bool = False) -> None:
        duration = max(0.0, float(source_duration or 0.0))
        ranges = normalize_cut_ranges(list(keep_ranges or []), duration)
        if not ranges:
            ranges = [(0.0, duration)] if duration > 0 else []
        try:
            factor = float(speed)
        except (TypeError, ValueError):
            factor = 1.0
        self.keep_ranges = ranges
        self.source_duration = duration
        self.speed = factor if factor > 0 else 1.0
        self.reverse = bool(reverse)
        self.kept_duration = total_keep_duration(ranges)
        self.output_duration = self.kept_duration / self.speed

    @property
    def is_identity(self) -> bool:
        return (not self.reverse
                and abs(self.speed - 1.0) <= 1e-9
                and len(self.keep_ranges) <= 1
                and (not self.keep_ranges
                     or (self.keep_ranges[0][0] <= 1e-6
                         and self.keep_ranges[0][1] >= self.source_duration - 1e-6)))

    def map_interval(self, start: float, end: float) -> list[tuple[float, float]]:
        """Where [start, end) of the source lands in the output.

        Zero, one or several pieces: a span crossing a removed range survives as
        one piece per retained range it overlaps, rather than being stretched
        across the hole.
        """
        pieces: list[tuple[float, float]] = []
        offset = 0.0
        for keep_start, keep_end in self.keep_ranges:
            overlap_start = max(float(start), keep_start)
            overlap_end = min(float(end), keep_end)
            if overlap_end > overlap_start + 1e-6:
                pieces.append((offset + overlap_start - keep_start,
                               offset + overlap_end - keep_start))
            offset += keep_end - keep_start
        if self.reverse:
            pieces = [(self.kept_duration - piece_end, self.kept_duration - piece_start)
                      for piece_start, piece_end in reversed(pieces)]
        if abs(self.speed - 1.0) > 1e-9:
            pieces = [(piece_start / self.speed, piece_end / self.speed)
                      for piece_start, piece_end in pieces]
        return pieces


def retime_cues(cues: list[tuple[float, float, str]],
                timeline: TimelineMap) -> list[tuple[float, float, str]]:
    """Move cues from the source timeline onto the processed one.

    Reversing needs no special case for cue ORDER: mirrored boundaries plus
    `render_srt`'s time sort renumber the track back to front on their own.
    """
    retimed: list[tuple[float, float, str]] = []
    for start, end, body in cues:
        for new_start, new_end in timeline.map_interval(start, end):
            if new_end > new_start + 1e-6:
                retimed.append((new_start, new_end, body))
    return retimed


def video_timeline_origin(probe: dict[str, Any]) -> float | None:
    """The source timestamp that the picture starts at, or None if unknowable.

    Subtitle and video packets share one raw source clock, but FFmpeg's demuxer
    rebases what it hands out by the CONTAINER start -- the minimum across every
    stream. The two are not the same file: an MKV whose AAC track carries
    negative priming reports a container start of -0.023 while the video starts
    at 0, so a plainly extracted SRT arrives 23 ms late against the picture and
    every later transform multiplies the error (0.5x turned a wanted 0.400 into
    0.446). `TimelineMap` and the joined-timeline offsets both measure from the
    first video frame, so that is the one origin the cues have to be on.

    `probe` is `ffprobe -select_streams v:0 -show_entries
    stream=start_time:format=start_time -of json` output. The container start is
    the fallback because it reproduces the demuxer's own normalization, which is
    right whenever the video is what starts the file. None means the probe said
    nothing usable and the caller must leave the timestamps alone rather than
    guess an origin.
    """
    values = [stream.get("start_time") for stream in ((probe or {}).get("streams") or [])]
    values.append(((probe or {}).get("format") or {}).get("start_time"))
    for value in values:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def picture_clock_offset(answers: dict[str, Any]) -> float:
    """How far the demuxer's clock runs ahead of the picture, in seconds.

    Without `-copyts` FFmpeg rebases every input timestamp by the CONTAINER
    start -- the minimum across all streams -- so an input seek and a `trim`
    range both count from there. Editor ranges, `TimelineMap`, cues and
    chapters all count from the first video frame. The two clocks differ by
    exactly this value, and it is 0 for an ordinary file.

    Measured on a fixture whose audio starts 0.5 s before its picture: a
    requested picture cut of 2.0-4.0 issued as `-ss 2.0` produced a white flash
    at 1.0-1.9 instead of 0.5-1.5, because the seek landed at picture 1.5.
    `-ss 2.522` -- the same range plus this offset -- put it at 0.5-1.5 (B08).

    Add it to a source-clock seek or trim; never to a value that is already on
    the demuxer's clock, and never to a range on the PROCESSED clock, which the
    filter graph has already rebased with `setpts=PTS-STARTPTS`.
    """
    picture = video_timeline_origin({
        "streams": list(answers.get("video_streams") or []),
        "format": answers.get("format") or {},
    })
    if picture is None:
        return 0.0
    try:
        container = float((answers.get("format") or {}).get("start_time"))
    except (TypeError, ValueError):
        return 0.0
    return picture - container


def subtitle_source_origin(answers: dict[str, Any]) -> float:
    """The source timestamp this encode turns into output zero.

    ONE clock: the selected video's first frame. It used to be two -- the
    picture for an unseeked encode and the CONTAINER for a seeked one, because
    `-ss` and `trim` counted from the container while the unseeked chain
    rebased to the video. That split put every cue of a seeked cut late by the
    difference, and the difference is real: on a container starting 0.5 s
    before its picture a 2.0-4.0 cut landed its 2.5-3.5 cue at 1.0-2.0 instead
    of 0.5-1.5 (B08).

    The seek and the trim ranges now carry `picture_clock_offset` themselves,
    so both paths call the same moment zero and this needs no second answer.
    """
    for value in ([stream.get("start_time") for stream
                   in (answers.get("video_streams") or [])]
                  + [(answers.get("format") or {}).get("start_time")]):
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def is_text_subtitle(stream: dict[str, Any]) -> bool:
    return str((stream or {}).get("codec_name") or "").lower() in TEXT_SUBTITLE_CODECS


def subtitle_track_metadata(stream: dict[str, Any]) -> dict[str, Any]:
    """Language/title/disposition of a source track, so a rebuilt one keeps it.

    A retimed or merged track is a NEW stream; without this it would arrive as
    an untitled, language-less, never-default subtitle.
    """
    tags = {str(key).lower(): value for key, value in ((stream or {}).get("tags") or {}).items()}
    disposition = (stream or {}).get("disposition") or {}
    return {
        "language": str(tags.get("language") or "").strip(),
        "title": str(tags.get("title") or "").strip(),
        "default": bool(disposition.get("default")),
        "forced": bool(disposition.get("forced")),
    }


def merge_joined_srt(segments: list[tuple[str, float]]) -> str:
    """Merge per-input SRT text into one track for the joined timeline.

    `segments` is [(srt_text, duration_seconds), ...] in join order. Each
    input's cues are shifted by the total duration of everything before it.
    An input with no subtitle contributes nothing but still advances the offset,
    which is what keeps later inputs' cues aligned.
    """
    merged: list[tuple[float, float, str]] = []
    offset = 0.0
    for text, duration in segments:
        length = max(0.0, float(duration or 0.0))
        if text:
            merged.extend(shift_cues(parse_srt(text), offset, limit=length or None))
        offset += length
    return render_srt(merged)


def item_subtitle_streams(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Every subtitle stream of one join input, in file order.

    The PRIMARY join input is assembled without a `subtitle_streams` key -- its
    subtitles only appear inside `streams` -- so reading the key alone made
    input 1 look subtitle-free and its tracks never reached the joined output.
    """
    streams = item.get("subtitle_streams")
    if streams is None:
        streams = [stream for stream in (item.get("streams") or [])
                   if str(stream.get("codec_type") or "").lower() == "subtitle"]
    return list(streams or [])


def joinable_subtitle_streams(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Text subtitle streams of one join input; bitmap tracks are excluded."""
    return [stream for stream in item_subtitle_streams(item) if is_text_subtitle(stream)]


def join_subtitle_track_count(items: list[dict[str, Any]]) -> int:
    """How many logical subtitle tracks the joined set offers.

    Taken across ALL inputs, not input 1: a first input without subtitles used
    to hide a later input's tracks completely.
    """
    return max((len(item_subtitle_streams(item)) for item in items), default=0)


def selected_join_subtitle_tracks(answers: dict[str, Any],
                                  items: list[dict[str, Any]]) -> list[int]:
    """Which logical (relative) subtitle tracks the join should merge.

    `subtitle_tracks` was previously read as a yes/no flag and every input then
    contributed its track 0, so asking for track 1 silently muxed track 0. The
    indices are relative positions within each input's subtitle streams -- the
    same numbering the wizard shows.
    """
    count = join_subtitle_track_count(items)
    if not count:
        return []
    if "subtitle_tracks" in answers:
        selected = answers.get("subtitle_tracks")
        if selected == "all":
            return list(range(count))
        chosen: list[int] = []
        for value in (selected or []):
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= index < count and index not in chosen:
                chosen.append(index)
        return sorted(chosen)
    # The track question is only asked when input 1 has subtitles, so an absent
    # key on a keep-subtitles job means it was never asked -- keep everything
    # the joined set actually has instead of dropping the later inputs' tracks.
    if source_subtitles_keep_enabled(answers):
        return list(range(count))
    return []


def join_subtitle_streams_view(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """One representative stream per LOGICAL joined subtitle track.

    The track question reads input 1's list alone, so a join whose first input
    carries no subtitles was never asked which of the later inputs' tracks to
    keep -- every one of them was kept instead. Track i is described by the
    first input that actually has an i-th subtitle stream.
    """
    items = answers.get("join_input_items") or []
    if not items:
        return list(answers.get("subtitle_streams") or [])
    all_items = [{"subtitle_streams": list(answers.get("subtitle_streams") or [])}, *items]
    view: list[dict[str, Any]] = []
    for index in range(join_subtitle_track_count(all_items)):
        for item in all_items:
            streams = item_subtitle_streams(item)
            if index < len(streams):
                view.append(streams[index])
                break
    return view


def any_join_subtitles(answers: dict[str, Any]) -> bool:
    """True when ANY input carries subtitles, not just input 1."""
    return bool(join_subtitle_streams_view(answers))


def with_join_subtitle_view(step: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    """Run a subtitle step against the JOIN's tracks instead of input 1's.

    Mirrors `with_join_audio_view`: lend the joined track list to the step so it
    counts and labels what the output really carries, then take it straight
    back -- nothing outside the step may see input 1 claiming other inputs'
    streams.
    """

    def run(answers: dict[str, Any]) -> None:
        view = join_subtitle_streams_view(answers)
        if view == list(answers.get("subtitle_streams") or []):
            step(answers)
            return
        missing = object()
        saved = answers.get("subtitle_streams", missing)
        answers["subtitle_streams"] = view
        try:
            step(answers)
        finally:
            if saved is missing:
                answers.pop("subtitle_streams", None)
            else:
                answers["subtitle_streams"] = saved

    return run


def join_subtitle_plan(answers: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Which joined subtitle tracks can be assembled, and why the rest cannot.

    Returns {"supported", "reason", "tracks", "segments", "dropped_tracks"}.
    `tracks` holds one merged output track per SELECTED logical track, each with
    per-input segments; an input lacking that track contributes an empty segment
    so the later inputs' cues stay aligned. `segments` remains the first track's
    segment list for callers that only need one.

    Assembly is refused when the timeline is edited: cuts, a speed change or a
    split all move the joined clock, and the shifted cues would no longer line
    up with the picture. Saying so is better than shipping subtitles that drift.
    """
    plan: dict[str, Any] = {"supported": False, "reason": "", "segments": [],
                            "tracks": [], "dropped_tracks": []}
    selected = selected_join_subtitle_tracks(answers, items)
    if not selected:
        # "Nothing was asked for" and "nothing is there to give" are different
        # answers, and the user who DID ask is owed the second one.
        if not (answers.get("subtitle_tracks") or (
                source_subtitles_keep_enabled(answers) and answers.get("subtitle_streams"))):
            plan["reason"] = "no subtitle track was selected"
        elif not join_subtitle_track_count(items):
            plan["reason"] = "none of the inputs has a subtitle track"
        else:
            plan["reason"] = "the selected subtitle track does not exist in any input"
        return plan
    # An edited timeline is no longer a refusal. The merged track is assembled
    # on the UNEDITED joined clock here, and the caller then runs it through the
    # same TimelineMap the video and audio use -- cuts, speed and reverse all
    # compose, and a Split slices the transformed track into its parts. Refusing
    # meant an edited join silently shipped none of the tracks the user picked,
    # even though every piece needed to carry them already existed (F09).

    # The PICTURE span, not `item["duration"]`, which is the container's. A
    # subtitle or audio packet reaching past the last frame lengthens the
    # container without adding a frame to the joined video, so offsetting by it
    # pushed every later input late and left this input's own tail cue hanging
    # past its last frame: a 2.000 s picture in a 3.000 s MKV merged input 2's
    # 0.500-1.500 cue at 3.500-4.500 instead of 2.500-3.500 (B07). The same
    # value is the clip limit below, which is what trims the tail cue back to
    # the picture it belongs to.
    durations = [join_item_picture_span(item) for item in items]
    if any(duration <= 0 for duration in durations):
        plan["reason"] = "an input has no known duration, so cue offsets cannot be computed"
        return plan

    bitmap_only = sum(1 for item in items
                      if item_subtitle_streams(item) and not joinable_subtitle_streams(item))
    tracks: list[dict[str, Any]] = []
    dropped: list[int] = []
    for relative in selected:
        segments: list[tuple[dict[str, Any], dict[str, Any] | None, float]] = []
        metadata: dict[str, Any] | None = None
        for item, duration in zip(items, durations):
            streams = item_subtitle_streams(item)
            stream = streams[relative] if relative < len(streams) else None
            if stream is not None and not is_text_subtitle(stream):
                stream = None  # bitmap: this input contributes an empty segment
            if stream is not None and metadata is None:
                metadata = subtitle_track_metadata(stream)
            segments.append((item, stream, duration))
        if any(stream is not None for _item, stream, _duration in segments):
            tracks.append({"index": relative, "segments": segments,
                           **(metadata or subtitle_track_metadata({}))})
        else:
            dropped.append(relative)

    if not tracks:
        plan["reason"] = (
            "the selected inputs carry only bitmap subtitles, which cannot be shifted onto a joined timeline"
            if bitmap_only else "none of the inputs has a text subtitle track")
        plan["dropped_tracks"] = dropped
        return plan
    plan["supported"] = True
    plan["tracks"] = tracks
    plan["segments"] = tracks[0]["segments"]
    plan["dropped_tracks"] = dropped
    plan["bitmap_only_inputs"] = bitmap_only
    return plan


__all__ = [
    'SRT_CUE_RE',
    'srt_timestamp',
    'parse_srt',
    'shift_cues',
    'render_srt',
    'TimelineMap',
    'retime_cues',
    'video_timeline_origin',
    'subtitle_source_origin',
    'picture_clock_offset',
    'is_text_subtitle',
    'subtitle_track_metadata',
    'merge_joined_srt',
    'item_subtitle_streams',
    'joinable_subtitle_streams',
    'join_subtitle_track_count',
    'selected_join_subtitle_tracks',
    'join_subtitle_plan',
    'join_subtitle_streams_view',
    'any_join_subtitles',
    'with_join_subtitle_view',
]
