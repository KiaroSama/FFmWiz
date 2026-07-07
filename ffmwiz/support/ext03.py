"""FFmWiz second-layer helpers (appio-dependent), tier 3.

Extracted from FFmWiz.py after the appio qualification; imports core,
existing support modules, and appio. Acyclic (imports only lower tiers).
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
from ffmwiz.support.L06 import *  # noqa: F401,F403
from ffmwiz.support.L07 import *  # noqa: F401,F403
from ffmwiz.appio import *  # noqa: F401,F403
from ffmwiz import appio  # qualified primitives
from ffmwiz.support.ext00 import *  # noqa: F401,F403
from ffmwiz.support.ext01 import *  # noqa: F401,F403
from ffmwiz.support.ext02 import *  # noqa: F401,F403


def select_stream(probe_json: dict[str, Any], answers: dict[str, Any],
                  allowed_types: set[str] | None = None) -> dict[str, Any]:
    allowed_types = {item.lower() for item in allowed_types} if allowed_types else None
    streams = [
        stream for stream in (probe_json.get("streams") or [])
        if allowed_types is None or metadata_stream_type(stream) in allowed_types
    ]
    if not streams:
        appio.error("No matching streams were found for this operation.")
        raise Back()
    list_streams_for_selection(probe_json, streams)
    by_index = {metadata_stream_index(stream): stream for stream in streams if metadata_stream_index(stream) is not None}
    while True:
        value = appio.ask_raw(metadata_prompt(
            answers,
            "Enter stream index",
            "use the real ffprobe stream index shown above; use b to go back",
            back="back=b, quit=exit",
        ))
        if str(value).strip().lower() in {"b", "back"}:
            raise Back()
        if not re.fullmatch(r"\d+", value or ""):
            appio.error("Enter a numeric stream index from the list above.")
            continue
        stream = by_index.get(int(value))
        if stream is None:
            appio.error("That stream index is not available for this operation.")
            continue
        log_info(
            f"Metadata Editor selected stream: index={value}; "
            f"type={metadata_stream_type(stream)}; spec={metadata_stream_spec(probe_json, stream)}"
        )
        return stream


__all__ = [
    'select_stream',
]
