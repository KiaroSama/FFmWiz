"""FFmWiz helpers (dependency level 6) — concerns: filters(3), probe(1).

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
from ffmwiz.support.L05 import *  # noqa: F401,F403


def cropped_source_size(answers: dict[str, Any]) -> tuple[int, int]:
    source_w, source_h = first_video_size(answers)
    if not answers.get("crop_enabled"):
        return source_w, source_h
    # Use the chroma/encoder-normalized margins so downstream resize math and
    # the actual crop filter agree on the post-crop dimensions.
    left, right, top, bottom = normalized_crop_margins(answers)
    return source_w - left - right, source_h - top - bottom


def crop_normalization_summary_lines(answers: dict[str, Any]) -> list[str]:
    """Build human-readable summary lines describing the crop normalization.
    Returns an empty list when crop is not active."""
    if not answers.get("crop_enabled"):
        return []
    req_left = int(answers.get("crop_left", 0) or 0)
    req_right = int(answers.get("crop_right", 0) or 0)
    req_top = int(answers.get("crop_top", 0) or 0)
    req_bottom = int(answers.get("crop_bottom", 0) or 0)
    if not any((req_left, req_right, req_top, req_bottom)):
        return []
    source_w, source_h = first_video_size(answers)
    stream = source_video_stream(answers) or {}
    pix_fmt = str(stream.get("pix_fmt") or "unknown")
    h_align, v_align = chroma_subsampling_alignment(stream.get("pix_fmt"))
    adj_left, adj_right, adj_top, adj_bottom = normalized_crop_margins(answers)
    final_w = source_w - adj_left - adj_right
    final_h = source_h - adj_top - adj_bottom
    backend = "CUVID decoder" if (
        answers.get("use_gpu")
        and cuda_decoder_for_source(answers)
        and can_use_cuda_fast_path(answers, resolve_video_encoder(answers)[0])
    ) else "CPU filter"
    changed = (adj_left, adj_right, adj_top, adj_bottom) != (req_left, req_right, req_top, req_bottom)
    lines = [
        f"Source resolution: {source_w}x{source_h}",
        f"Source pixel format: {pix_fmt}",
        f"Chroma origin alignment: horizontal={h_align}, vertical={v_align}",
        f"Requested crop: left={req_left}, right={req_right}, top={req_top}, bottom={req_bottom}",
        f"Adjusted crop: left={adj_left}, right={adj_right}, top={adj_top}, bottom={adj_bottom}",
        f"Adjustment per side: left={adj_left - req_left:+d}, right={adj_right - req_right:+d}, "
        f"top={adj_top - req_top:+d}, bottom={adj_bottom - req_bottom:+d}",
        f"Final cropped resolution: {final_w}x{final_h}",
        f"Crop backend: {backend}",
        "Automatic black compatibility padding: disabled",
    ]
    if changed:
        lines.append("Reason: Align crop origin to the source chroma grid and keep encodable output dimensions without padding.")
    else:
        lines.append("Crop values already satisfy chroma and encoder alignment requirements.")
    return lines


def packet_size_probe_allowed(answers: dict[str, Any]) -> bool:
    _ = PACKET_SIZE_PROBE_MAX_BYTES
    return packet_size_probe_needed(answers)


def crop_margins_to_cuvid_crop(answers: dict[str, Any]) -> str | None:
    if not has_crop(answers):
        return None
    # Use the same normalized margins as the CPU crop filter so the CUVID
    # decoder crop produces an identical visible rectangle.
    left, right, top, bottom = normalized_crop_margins(answers)
    return f"{top}x{bottom}x{left}x{right}"


__all__ = [
    'cropped_source_size',
    'crop_normalization_summary_lines',
    'packet_size_probe_allowed',
    'crop_margins_to_cuvid_crop',
]
