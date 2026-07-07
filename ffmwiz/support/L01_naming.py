"""FFmWiz helpers (dependency level 1) — concerns: naming(2).

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


def source_extra_video_keep_enabled(answers: dict[str, Any]) -> bool:
    return bool(answers.get("keep_source_extra_video_streams", source_metadata_keep_enabled(answers)))


def mux_extra_file_sources(input_root: Path, output_root: Path) -> list[Path]:
    if input_root.is_file():
        return []
    sources: list[Path] = []
    for source in sorted(input_root.rglob("*"), key=lambda path: str(path).lower()):
        if not source.is_file():
            continue
        if source.suffix.lower() in MUX_CLEANUP_VIDEO_EXTS:
            continue
        if mux_path_is_under(source, output_root):
            continue
        sources.append(source)
    return sources


__all__ = [
    'source_extra_video_keep_enabled',
    'mux_extra_file_sources',
]
