"""FFmWiz helpers (dependency level 5) — concerns: filters(1), probe(1).

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


def normalized_crop_margins(answers: dict[str, Any]) -> tuple[int, int, int, int]:
    """Return chroma/encoder-aligned (left, right, top, bottom) crop margins.

    The crop origin is aligned to the source chroma grid and the cropped frame
    is aligned to the output encoder grid, keeping the values as close as
    possible to what the user entered. No black compatibility padding is ever
    added. Raises ValueError when no valid crop rectangle exists."""
    source_w, source_h = first_video_size(answers)
    left = int(answers.get("crop_left", 0) or 0)
    right = int(answers.get("crop_right", 0) or 0)
    top = int(answers.get("crop_top", 0) or 0)
    bottom = int(answers.get("crop_bottom", 0) or 0)

    message = crop_margins_validation_message(answers, top, left, right, bottom)
    if message:
        raise ValueError(message)

    stream = source_video_stream(answers) or {}
    h_origin_align, v_origin_align = chroma_subsampling_alignment(stream.get("pix_fmt"))
    out_w_align, out_h_align = output_size_alignment(answers)

    horizontal = _normalize_crop_axis(left, right, source_w, h_origin_align, out_w_align)
    vertical = _normalize_crop_axis(top, bottom, source_h, v_origin_align, out_h_align)
    if horizontal is None or vertical is None:
        raise ValueError(
            "Could not find a valid crop rectangle that satisfies the source "
            "chroma grid and the output encoder dimensions. Reduce the crop amount."
        )
    adj_left, adj_right = horizontal
    adj_top, adj_bottom = vertical
    return adj_left, adj_right, adj_top, adj_bottom


def packet_size_probe_needed(answers: dict[str, Any]) -> bool:
    fmt = answers.get("format", {})
    streams = list(answers.get("video_streams", [])) + list(answers.get("audio_streams", []))
    if not streams:
        return False
    total_size = format_size_bytes_from_metadata(fmt)
    tag_sizes = [stream_tag_size_bytes(stream) for stream in streams]
    if total_size and all(size and size > 0 for size in tag_sizes):
        if sum(int(size or 0) for size in tag_sizes) > int(total_size * 1.02):
            return True
    return any(not stream_has_fast_size_metadata(stream, fmt, streams) for stream in streams)


__all__ = [
    'normalized_crop_margins',
    'packet_size_probe_needed',
]
