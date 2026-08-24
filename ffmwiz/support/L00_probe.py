"""FFmWiz helpers (dependency level 0) — concerns: probe(2).

Extracted verbatim from FFmWiz.py; imports ffmwiz.core.* and lower levels.
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
import concurrent.futures
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from ffmwiz.core.constants import *  # noqa: F401,F403
from ffmwiz.core.colors import *  # noqa: F401,F403
from ffmwiz.core.exceptions import *  # noqa: F401,F403
from ffmwiz.core.timeline import *  # noqa: F401,F403


def decoded_bytes_per_pixel(pix_fmt: Any) -> float:
    """Bytes one decoded pixel occupies in the named planar format.

    Derived from the format NAME rather than assumed: 8-bit `yuv420p` really is
    1.5 bytes, but `yuv420p10le` is 3.0 and `yuv444p` is 3.0, so a flat 1.5
    understated a 10-bit 4:4:4 source by four times. FFmpeg stores >8-bit
    components in whole 16-bit words, which is why the depth rounds up to a
    byte count rather than being used as a bit count.

    Falls back to 8-bit 4:2:0 when the name is unknown, which is the smallest
    common layout -- so callers that treat the result as a budget should apply
    their own safety factor rather than trusting an unrecognised name.
    """
    name = str(pix_fmt or "").strip().lower()
    if not name:
        return 1.5
    depth = 8
    match = re.search(r"p(\d+)(?:le|be)?$", name)
    if match:
        depth = int(match.group(1))
    elif name.endswith(("le", "be")):
        inner = re.search(r"(\d+)(?:le|be)$", name)
        if inner:
            depth = int(inner.group(1))
    bytes_per_component = max(1, (depth + 7) // 8)

    if name.startswith("gray"):
        planes = 1.0
    elif name.startswith(("rgb", "bgr", "gbr", "argb", "abgr", "rgba", "bgra")):
        planes = 4.0 if "a" in name[:5] else 3.0
    else:
        # Planar YUV: luma plus two chroma planes at the subsampling ratio.
        if "444" in name:
            chroma = 1.0
        elif "422" in name:
            chroma = 0.5
        elif "440" in name:
            chroma = 0.5
        elif "411" in name or "410" in name:
            chroma = 0.25
        else:
            chroma = 0.25  # 4:2:0 and NV12/NV21
        planes = 1.0 + 2.0 * chroma
        if name.startswith("yuva") or name.startswith("ya"):
            planes += 1.0
    return planes * bytes_per_component


def video_stream_span_seconds(stream: dict[str, Any],
                             fmt: dict[str, Any] | None = None) -> float:
    """How long the PICTURE lasts, which is not the container duration.

    A container outlives its video whenever another stream is longer -- audio
    padding, a trailing subtitle, an AAC priming delay. Reverse mirrors the
    timeline around this value, so using `format.duration` shifted every
    retimed cue by the difference: on a 4.000 s video in a 4.523 s container, a
    cue that belongs at the very start of the reversed clip landed 523 ms late.

    Sources in order of precision: the stream's own duration (MP4/MOV report
    it), Matroska's per-stream `DURATION` tag, the frame count over the frame
    rate, and finally the container. Returns 0.0 when nothing is known.

    A stream's START has to be taken off the Matroska tag, because that tag is
    an END timestamp rather than a length: a 4.000 s picture remuxed with
    `-output_ts_offset 1.0` reports `start_time=1.000` and
    `DURATION=00:00:05.000000000`, and 40 packets at 10 fps. MP4/MOV report the
    track's own length, which already excludes the start -- the same remux
    gives `start_time=1.000` with `duration=4.000` -- so only the tag is
    adjusted. Reading the tag as a length made a joined input one second too
    long and mirrored reverse around the wrong axis.

    ponytail: the container fallback is left as the raw `format.duration`. It
    is end-relative in Matroska and start-relative in MP4, and it only fires
    when the stream has no duration, no tag and no frame count -- telling the
    two apart there needs the format name, which is more machinery than a
    last-resort estimate is worth.
    """
    try:
        start = float(stream.get("start_time") or 0.0)
    except (TypeError, ValueError):
        start = 0.0

    for key in ("duration",):
        try:
            value = float(stream.get(key) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value

    tag = ""
    for tags in (stream.get("tags"), stream.get("TAGS")):
        if isinstance(tags, dict):
            for name in ("DURATION", "duration", "DURATION-eng"):
                if tags.get(name):
                    tag = str(tags[name])
                    break
        if tag:
            break
    if tag:
        # Matroska writes HH:MM:SS.nnnnnnnnn.
        pieces = tag.split(":")
        try:
            if len(pieces) == 3:
                seconds = (int(pieces[0]) * 3600 + int(pieces[1]) * 60
                           + float(pieces[2]))
            else:
                seconds = float(tag)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds - start > 0:
            return seconds - start

    frames = stream.get("nb_frames")
    rate = parse_rational(stream.get("avg_frame_rate")) or parse_rational(
        stream.get("r_frame_rate"))
    try:
        count = float(frames or 0.0)
    except (TypeError, ValueError):
        count = 0.0
    if count > 0 and rate and rate > 0:
        return count / rate

    try:
        return float((fmt or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def join_item_picture_span(item: dict[str, Any]) -> float:
    """How long ONE join input's PICTURE lasts, in seconds.

    The joined program's video timeline is the sum of these, never of the
    containers. `concat` splices decoded frames, so a stream that outlives the
    picture -- a trailing subtitle cue, an audio pad, AAC priming -- adds no
    frames to the joined video but does inflate `format.duration`. Measured on
    a 2.000 s picture whose subtitle stretched its MKV to 3.000 s: every later
    input was offset by 3.000 instead of 2.000, so input 2's 0.500-1.500 cue
    was merged at 3.500-4.500 instead of 2.500-3.500, and input 1's own tail
    cue was never clipped back to its 2.000 s picture (B07).

    Delegates to `video_stream_span_seconds`, so the precision order is the
    same: the video stream's own duration, Matroska's per-stream `DURATION`
    tag, the frame count over the frame rate, then the container. That order
    also keeps a VFR tail honest -- the two duration sources are exact and are
    consulted before frames/fps, which assumes a constant rate.

    An audio-only join item has no picture at all, so the program duration IS
    its recorded container duration and is returned unchanged. 0.0 means
    nothing is knowable, which every caller already treats as "this input
    cannot be placed on the joined timeline".
    """
    video = item.get("video_streams")
    if video is None:
        video = [stream for stream in (item.get("streams") or [])
                 if str(stream.get("codec_type") or "").lower() == "video"]
    if video:
        span = video_stream_span_seconds(video[0], item.get("format"))
        if span > 0:
            return span
    try:
        return max(0.0, float(item.get("duration") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def parse_rational(text: Any) -> float | None:
    """Parse 'N:M', 'N/M', or a float to a positive finite float. Return None for
    unknown/empty/invalid/zero/negative/non-finite values (so callers can detect
    them). Examples accepted: 1:1, 9:16, 16:15, 64:45, 30000/1001, decimals.
    Rejected: 0:1, zero/negative denominators, malformed values, NaN, infinity."""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw or raw.lower() in {"n/a", "unknown", "none"}:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)", raw)
    if match:
        num = float(match.group(1))
        den = float(match.group(2))
        if num > 0 and den > 0 and math.isfinite(num) and math.isfinite(den):
            return num / den
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if (val > 0 and math.isfinite(val)) else None


def rational_to_float(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            num_i = float(num)
            den_i = float(den)
            return None if den_i == 0 else num_i / den_i
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


__all__ = [
    'decoded_bytes_per_pixel',
    'video_stream_span_seconds',
    'join_item_picture_span',
    'parse_rational',
    'rational_to_float',
]
