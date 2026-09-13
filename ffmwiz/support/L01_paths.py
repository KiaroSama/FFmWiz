"""FFmWiz helpers (dependency level 1) — concerns: paths(6).

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


def normalize_terminal_path_text(value: str) -> str:
    value = strip_quotes(value)
    if value.startswith("&"):
        value = strip_quotes(value[1:].strip())
    if value.startswith("<") and value.endswith(">"):
        value = strip_quotes(value[1:-1].strip())

    parsed = urlparse(value)
    if parsed.scheme.lower() == "file":
        path_text = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:", path_text):
            path_text = path_text[1:]
        elif parsed.netloc:
            path_text = f"//{parsed.netloc}{path_text}"
        return path_text.replace("/", os.sep)

    return value


def output_location_is_explicit_directory(value: str) -> bool:
    """True when the user's own text ends in a separator: "this is a folder".

    `Path()` discards a trailing separator, so the intent has to be read from
    the text -- and from the NORMALIZED text. A quoted `"D:\\Exports\\"` ends in
    the quote, not the separator, so testing the raw string answered False and
    a not-yet-created folder became a filename (A07). Both pickers ask here
    rather than each spelling out the test.
    """
    return normalize_terminal_path_text(value).rstrip().endswith(("/", "\\", os.sep))


def folder_default_output_path(input_folder: Path) -> Path:
    return input_folder.parent / f"{sanitize_output_stem(input_folder.name)}_Encode"


def asset_path(*parts: str) -> Path:
    # Assets live inside the package (<project>/ffmwiz/assets/). This module is at
    # <project>/ffmwiz/support/, so the package directory is parents[1]; resolving
    # relative to it keeps assets locatable regardless of the caller's CWD.
    return Path(__file__).resolve().parents[1].joinpath(ASSET_DIR_NAME, *parts)


def resolve_output_collision(output_path: Path, input_path: Path, collision_suffix: str) -> Path:
    """Avoid writing over the source file when output name and extension match."""
    if not paths_same(output_path, input_path):
        return output_path
    safe_stem = sanitize_output_stem(output_path.stem)
    candidate = output_path.with_name(f"{safe_stem}{collision_suffix}{output_path.suffix}")
    return unique_numbered_path(candidate)


def resolve_output_collision_for_sources(output_path: Path, sources: list[Path],
                                         collision_suffix: str) -> Path:
    """Avoid writing the output over ANY source the job reads.

    `resolve_output_collision` guards one input. A job usually has several --
    joined media, external tracks, subtitles, covers -- and guarding only the
    primary is how Track Manager came to overwrite an added WAV (R02). This is
    the shared form; `ext00b.resolve_output_collision_against_inputs` keeps its
    name and logging and delegates here, so there is one rule, not two.
    """
    resolved = output_path
    for source in sources:
        if source and paths_same(resolved, source):
            safe_stem = sanitize_output_stem(resolved.stem)
            resolved = unique_numbered_path(
                resolved.with_name(f"{safe_stem}{collision_suffix}{resolved.suffix}"))
    return resolved


def build_separator_base_output_path(answers: dict[str, Any]) -> Path:
    input_path: Path = answers["input_path"]
    output_location: Path = answers["output_location"]
    output_ext = answers["output_ext"]
    if answers.get("output_name_stem"):
        return output_location / f"{sanitize_output_stem(answers['output_name_stem'])}.{output_ext}"
    if output_location_names_a_file(output_location, bool(answers.get("output_location_is_dir"))):
        output_path = output_location.with_suffix("." + output_ext)
        return output_path.with_name(f"{sanitize_output_stem(output_path.stem)}{output_path.suffix}")
    return output_location / f"{sanitize_output_stem(input_path.stem)}.{output_ext}"


def default_extract_stream_output_path(input_path: Path, stream: dict[str, Any], ext: str | None = None) -> Path:
    stream_index = stream_global_index(stream)
    codec_type = str(stream.get("codec_type") or "stream").lower()
    suffix = ("." + ext.lstrip(".")) if ext else extract_stream_default_extension(stream)
    stem = f"{sanitize_output_stem(input_path.stem)}{EXTRACT_STREAM_OUTPUT_SUFFIX}{stream_index}_{codec_type}"
    return input_path.parent / f"{stem}{suffix}"


__all__ = [
    'normalize_terminal_path_text',
    'output_location_is_explicit_directory',
    'folder_default_output_path',
    'asset_path',
    'resolve_output_collision',
    'resolve_output_collision_for_sources',
    'build_separator_base_output_path',
    'default_extract_stream_output_path',
]
