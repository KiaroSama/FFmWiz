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


# Bytes one decoded pixel of each software pixel format really occupies,
# measured against the installed FFmpeg rather than inferred from the name.
#
# The name-sniffing predecessor guessed the layout from substrings and fell back
# to 1.5 B/px, which understated 26 of the 205 measurable formats on FFmpeg
# 8.1.1 -- `vuya`/`ayuv`/`uyva`/`0rgb`/`0bgr`/`vuyx`/`v30xle` by 2.67x,
# `nv24`/`nv42`/`xyz12*` and the eight `*msb*` formats by 2.00x, `nv16`/`rgb0`/
# `bgr0`/`x2rgb10le`/`x2bgr10le` by 1.33x -- and it also read the "565" in
# `rgb565le` as a 565-bit component depth, returning 213 B/px for a 2 B/px
# format. Both directions break a hard budget: one overruns the cap, the other
# collapses the segment to a single frame.
#
# Ground truth is `ffmpeg -f lavfi -i nullsrc=s=640x480 -vf format=<fmt>
# -pix_fmt <fmt> -f rawvideo -` divided by the pixel count, i.e.
# av_image_get_buffer_size(). Formats this build cannot convert to fall back to
# their BE/LE twin's measurement, and the handful with no twin (bayer, a few
# float layouts) to descriptor arithmetic over `ffprobe -show_pixel_formats`,
# which over-states rather than under-states every one of them. `pal8` sits in
# the nv12 class: the index plane is 1 B/px and its 1 KiB palette is fixed side
# data, invisible beside the 512 MiB reserved overhead.
#
# The CLI cannot report component STEP, so `ffprobe`'s BITS_PER_PIXEL alone is
# not enough: it says 24 for `0rgb` and `vuyx`, which really allocate 32, and 15
# for `yuv420p10le`, which really allocates 24 (>8-bit components sit in whole
# 16-bit words). Measuring sidesteps both gaps.
_BYTES_PER_PIXEL_GROUPS: dict[float, str] = {
    0.125: "monob monow",
    0.5: "bgr4 rgb4",
    1: "bgr4_byte bgr8 gray rgb4_byte rgb8",
    1.125: "yuv410p",
    1.5: "nv12 nv21 pal8 uyyvyy411 yuv411p yuv420p yuvj411p yuvj420p",
    2: "bgr444be bgr444le bgr555be bgr555le bgr565be bgr565le gray10be"
       " gray10le gray12be gray12le gray14be gray14le gray16be gray16le"
       " gray9be gray9le grayf16be grayf16le nv16 rgb444be rgb444le rgb555be"
       " rgb555le rgb565be rgb565le uyvy422 ya8 yuv422p yuv440p yuvj422p"
       " yuvj440p yuyv422 yvyu422",
    2.5: "yuva420p",
    3: "bayer_bggr16be bayer_bggr16le bayer_bggr8 bayer_gbrg16be"
       " bayer_gbrg16le bayer_gbrg8 bayer_grbg16be bayer_grbg16le bayer_grbg8"
       " bayer_rggb16be bayer_rggb16le bayer_rggb8 bgr24 gbrp nv24 nv42 p010be"
       " p010le p012be p012le p016be p016le rgb24 vyu444 yuv420p10be"
       " yuv420p10le yuv420p12be yuv420p12le yuv420p14be yuv420p14le"
       " yuv420p16be yuv420p16le yuv420p9be yuv420p9le yuv444p yuva422p"
       " yuvj444p",
    4: "0bgr 0rgb abgr argb ayuv bgr0 bgra gbrap gray32be gray32le grayf32be"
       " grayf32le nv20be nv20le p210be p210le p212be p212le p216be p216le"
       " rgb0 rgba uyva v30xbe v30xle vuya vuyx x2bgr10be x2bgr10le x2rgb10be"
       " x2rgb10le xv30be xv30le y210be y210le y212be y212le y216be y216le"
       " ya16be ya16le yaf16be yaf16le yuv422p10be yuv422p10le yuv422p12be"
       " yuv422p12le yuv422p14be yuv422p14le yuv422p16be yuv422p16le"
       " yuv422p9be yuv422p9le yuv440p10be yuv440p10le yuv440p12be yuv440p12le"
       " yuva444p",
    5: "yuva420p10be yuva420p10le yuva420p16be yuva420p16le yuva420p9be"
       " yuva420p9le",
    6: "bgr48be bgr48le gbrp10be gbrp10le gbrp10msbbe gbrp10msble gbrp12be"
       " gbrp12le gbrp12msbbe gbrp12msble gbrp14be gbrp14le gbrp16be gbrp16le"
       " gbrp9be gbrp9le gbrpf16be gbrpf16le p410be p410le p412be p412le"
       " p416be p416le rgb48be rgb48le rgbf16be rgbf16le xyz12be xyz12le"
       " yuv444p10be yuv444p10le yuv444p10msbbe yuv444p10msble yuv444p12be"
       " yuv444p12le yuv444p12msbbe yuv444p12msble yuv444p14be yuv444p14le"
       " yuv444p16be yuv444p16le yuv444p9be yuv444p9le yuva422p10be"
       " yuva422p10le yuva422p12be yuva422p12le yuva422p16be yuva422p16le"
       " yuva422p9be yuva422p9le",
    8: "ayuv64be ayuv64le bgra64be bgra64le gbrap10be gbrap10le gbrap12be"
       " gbrap12le gbrap14be gbrap14le gbrap16be gbrap16le gbrapf16be"
       " gbrapf16le rgba64be rgba64le rgbaf16be rgbaf16le xv36be xv36le xv48be"
       " xv48le yaf32be yaf32le yuva444p10be yuva444p10le yuva444p12be"
       " yuva444p12le yuva444p16be yuva444p16le yuva444p9be yuva444p9le",
    12: "gbrpf32be gbrpf32le rgb96be rgb96le rgbf32be rgbf32le",
    16: "gbrap32be gbrap32le gbrapf32be gbrapf32le rgba128be rgba128le"
        " rgbaf32be rgbaf32le",
}

BYTES_PER_PIXEL_BY_FORMAT: dict[str, float] = {
    name: size
    for size, names in _BYTES_PER_PIXEL_GROUPS.items()
    for name in names.split()
}

# Opaque driver surfaces. Their frames are not a plain byte block we can size,
# so a budget must refuse rather than pretend, and `reverse` downloads them to
# a software format we cannot name in advance.
HARDWARE_PIXEL_FORMATS: frozenset[str] = frozenset(
    "amf cuda d3d11 d3d11va_vld d3d12 drm_prime dxva2_vld mediacodec mmal"
    " ohcodec opencl qsv vaapi vdpau videotoolbox_vld vulkan".split())

# What an unrecognised name costs. The widest software layout FFmpeg 8.1.1 ships
# is 16 B/px (rgba128/rgbaf32/gbrap32), so this is a real worst case rather than
# an average. The old fallback was 1.5 -- the SMALLEST common layout -- which
# turned "hard cap" into "hard cap unless we have not heard of the format".
UNKNOWN_PIXEL_FORMAT_BYTES = 16.0


def pixel_format_bytes_per_pixel(pix_fmt: Any) -> float | None:
    """Measured bytes per decoded pixel, or None when the name is not known.

    None is the signal a hard budget needs: the caller decides whether to refuse
    (D12) or to fall back to the conservative worst case. Hardware surfaces are
    deliberately absent from the table.
    """
    name = str(pix_fmt or "").strip().lower()
    return BYTES_PER_PIXEL_BY_FORMAT.get(name)


def decoded_bytes_per_pixel(pix_fmt: Any) -> float:
    """Bytes one decoded pixel occupies, never below the real allocation.

    Total by design -- an unknown or hardware format yields
    UNKNOWN_PIXEL_FORMAT_BYTES rather than raising, so callers that only want a
    number keep working. Callers that must REFUSE an unknown format ask
    `pixel_format_bytes_per_pixel` for the None instead.
    """
    known = pixel_format_bytes_per_pixel(pix_fmt)
    return UNKNOWN_PIXEL_FORMAT_BYTES if known is None else known


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
    'BYTES_PER_PIXEL_BY_FORMAT',
    'HARDWARE_PIXEL_FORMATS',
    'UNKNOWN_PIXEL_FORMAT_BYTES',
    'pixel_format_bytes_per_pixel',
    'decoded_bytes_per_pixel',
    'video_stream_span_seconds',
    'join_item_picture_span',
    'parse_rational',
    'rational_to_float',
]
