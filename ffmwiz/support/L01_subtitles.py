"""FFmWiz joined-subtitle assembly (dependency level 1).

A joined re-encode concatenates several inputs onto one timeline. FFmpeg's
`concat` filter handles video and audio but NOT subtitles, so the wizard used to
ask which subtitle tracks to keep and then emit `-sn`, discarding them silently.

The workable route is to build one subtitle track for the joined timeline: take
each input's selected text subtitle, shift its cues by that input's start offset,
clip anything past the input's own duration, and concatenate. The result is fed
back as an extra input and mapped into the output.

Only TEXT subtitles can be assembled this way. A bitmap track (PGS, VobSub,
DVB) is a picture stream with no cue text to shift, so it is reported as dropped
rather than silently discarded.
"""
from __future__ import annotations

import re
from typing import Any  # noqa: F401

from ffmwiz.core.constants import *  # noqa: F401,F403
from ffmwiz.core.exceptions import *  # noqa: F401,F403
from ffmwiz.support.L00_misc import *  # noqa: F401,F403
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


def joinable_subtitle_streams(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Text subtitle streams of one join input; bitmap tracks are excluded."""
    return [
        stream for stream in (item.get("subtitle_streams") or [])
        if str(stream.get("codec_name") or "").lower() in TEXT_SUBTITLE_CODECS
    ]


def join_subtitle_plan(answers: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether the joined re-encode can carry a subtitle track, and why not.

    Returns {"supported": bool, "reason": str, "segments": [(item, stream|None, duration)]}.
    Assembly is refused when the timeline is edited: cuts, a speed change or a
    split all move the joined clock, and the shifted cues would no longer line
    up with the picture. Saying so is better than shipping subtitles that drift.
    """
    plan: dict[str, Any] = {"supported": False, "reason": "", "segments": []}
    if not (answers.get("subtitle_tracks") or (
            source_subtitles_keep_enabled(answers) and answers.get("subtitle_streams"))):
        plan["reason"] = "no subtitle track was selected"
        return plan
    if answers.get("cut_keep_ranges"):
        plan["reason"] = "cuts move the joined timeline, so shifted cues would drift"
        return plan
    if answers.get("separator_points"):
        plan["reason"] = "a split writes several files, each needing its own subtitle track"
        return plan
    if video_speed_transform_enabled(answers) or answers.get("reverse_video"):
        plan["reason"] = "a speed or reverse change rescales the timeline"
        return plan

    segments: list[tuple[dict[str, Any], dict[str, Any] | None, float]] = []
    bitmap_only = 0
    for item in items:
        text_streams = joinable_subtitle_streams(item)
        if not text_streams and (item.get("subtitle_streams") or []):
            bitmap_only += 1
        duration = float(item.get("duration") or 0.0)
        segments.append((item, text_streams[0] if text_streams else None, duration))
    if not any(stream is not None for _item, stream, _d in segments):
        plan["reason"] = (
            "the selected inputs carry only bitmap subtitles, which cannot be shifted onto a joined timeline"
            if bitmap_only else "none of the inputs has a text subtitle track")
        return plan
    if any(duration <= 0 for _item, _stream, duration in segments):
        plan["reason"] = "an input has no known duration, so cue offsets cannot be computed"
        return plan
    plan["supported"] = True
    plan["segments"] = segments
    plan["bitmap_only_inputs"] = bitmap_only
    return plan


__all__ = [
    'SRT_CUE_RE',
    'srt_timestamp',
    'parse_srt',
    'shift_cues',
    'render_srt',
    'merge_joined_srt',
    'joinable_subtitle_streams',
    'join_subtitle_plan',
]
