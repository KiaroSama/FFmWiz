"""FFmWiz helpers (dependency level 0) — concerns: paths(6).

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
from ffmwiz.core.artifacts import *  # noqa: F401,F403
from ffmwiz.core.colors import *  # noqa: F401,F403
from ffmwiz.core.exceptions import *  # noqa: F401,F403
from ffmwiz.core.timeline import *  # noqa: F401,F403
from ffmwiz.core.constants_ffmpeg_options import (FFMPEG_ALL_VALUED_OPTIONS,
                                                  FFMPEG_FILE_VALUED_OPTIONS,
                                                  FFMPEG_VALUELESS_OPTIONS)

# A concat list is a text file; anything bigger is not one, and a guard must
# not read an arbitrary amount of disk at the moment a job starts.
CONCAT_LIST_MAX_BYTES = 4 * 1024 * 1024

# The quoting `-f concat` lists use around each member path.
QUOTE_CHARS = '\'"'


def _progress_output_paths_from_command(cmd: list[str]) -> list[Path]:
    """Infer the final output path for simple single-output FFmpeg commands."""
    if not cmd:
        return []
    # Keep progress on the same literal filename as execution. All shell and
    # terminal unquoting already happened before the argv was constructed.
    candidate = str(cmd[-1])
    if candidate.startswith("-"):
        return []
    path = ffmpeg_url_path(candidate)
    return [path] if path is not None and path.suffix and path in command_writes(cmd) else []


def mux_display_path(input_root: Path, input_file: Path) -> Path:
    if input_root.is_file():
        return Path(input_file.name)
    try:
        return input_file.relative_to(input_root)
    except ValueError:
        return input_file


def script_dir() -> Path:
    # The project root is where FFmWiz.py lives. This module is at
    # <root>/ffmwiz/support/L00_paths.py, so the root is two directories up
    # from the ffmwiz package (parents[2]). Keeping this correct is critical:
    # config.env, requirements.txt, the bundled GUI, MediaReports and Logs are
    # all resolved relative to it.
    return Path(__file__).resolve().parents[2]


def sanitize_output_stem(stem: str) -> str:
    """Sanitize only the filename stem, preserving folders and extensions."""
    cleaned = INVALID_FILENAME_CHARS_RE.sub("_", str(stem or "")).strip(" .")
    return cleaned or "output"


def paths_same(a: Path, b: Path) -> bool:
    """True when both paths denote the same FILE, not merely the same text.

    Comparing normalized strings misses every alias a filesystem offers: a
    hardlink, a symlink, a Windows junction and a symlinked parent directory
    all spell the source file differently while pointing at it. An output that
    survives this check as "different" is handed straight to FFmpeg, which
    happily rewrites the user's input in place and still exits 0.

    Two existing paths are therefore compared by file identity
    (`os.path.samefile`, i.e. st_dev/st_ino), and a not-yet-existing output is
    compared canonically so an alias in its PARENT chain is still caught. An
    identity check that fails for any reason other than a missing path is
    inconclusive -- never proof that the two paths differ -- so it errs toward
    "same", whose only cost is a renamed output.
    """
    text_a, text_b = str(a or ""), str(b or "")
    if not text_a or not text_b:
        return False
    if os.path.normcase(os.path.abspath(text_a)) == os.path.normcase(os.path.abspath(text_b)):
        return True
    if os.path.lexists(text_a) and os.path.lexists(text_b):
        try:
            return os.path.samefile(text_a, text_b)
        except (OSError, ValueError):
            return True
    try:
        return os.path.normcase(os.path.realpath(text_a)) == os.path.normcase(os.path.realpath(text_b))
    except (OSError, ValueError):
        return False


def output_location_names_a_file(output_location: Path, explicit_directory: bool = False) -> bool:
    """True when an output location is a target FILENAME rather than a folder.

    A dotted directory name is ordinary (`Exports.v1`, `Season.01`), and the
    default output location is the input's own parent, so classifying by
    `.suffix` alone sent a whole job to a sibling file named after the folder
    (`Exports.mkv`) instead of into the folder. Existence decides first: a path
    that IS a directory is a folder whatever it is called, and a path that IS a
    file is a filename. `explicit_directory` carries the intent of a trailing
    separator, which terminal-path normalization strips before this is reached,
    so a not-yet-created folder can still be requested explicitly.
    """
    if explicit_directory:
        return False
    if path_is_existing_directory(output_location):
        return False
    try:
        if output_location.is_file():
            return True
    except OSError:
        pass
    return bool(output_location.suffix)


def path_is_existing_directory(path: Path) -> bool:
    """True only when the path exists AND is a directory. Never raises.

    Separate from the classifier above because callers need this answer on its
    own: "is this already a folder" decides a branch BEFORE the file/stem
    question is even asked, and `not names_a_file(...)` is not a substitute --
    that is also true of a bare stem that does not exist yet (A07).
    """
    try:
        return path.is_dir()
    except OSError:
        return False


# The command read/write manifest moved to its own module: it answers what an
# FFmpeg ARGV touches, which is a different responsibility from resolving a
# path, and it grew a concat reader, a filtergraph reader and a write/read
# direction split (A01). Re-exported here because `runtime` and the wizard tiers
# import these names from this module.
from ffmwiz.support.L00_command_io import (  # noqa: E402,F401
    command_input_paths, command_reads, command_source_output_conflict,
    command_unresolved_dependencies, command_writes, concat_list_members,
    ffmpeg_url_path)


def command_output_paths(cmd: list[str]) -> list[Path]:
    """Every destination the command writes. Kept as the historical name."""
    return command_writes(cmd)


def command_file_dependencies(cmd: list[str]) -> list[Path]:
    """Every file the command reads. Kept as the historical name.

    The `problems` half of the answer is deliberately NOT collapsed into an
    empty list here: `command_unresolved_dependencies` reports it, and the
    execution boundary refuses on it.
    """
    reads, _problems = command_reads(cmd)
    return reads


def command_output_path(cmd: list[str]):
    """The LAST destination, for callers that only ever had one."""
    outputs = command_writes(cmd)
    return outputs[-1] if outputs else None


def unique_numbered_path(path: Path) -> Path:
    if not path.exists():
        return path
    for counter in range(2, 10000):
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a free output filename near: {path}")


__all__ = [
    '_progress_output_paths_from_command',
    'mux_display_path',
    'script_dir',
    'sanitize_output_stem',
    'paths_same',
    'output_location_names_a_file',
    'path_is_existing_directory',
    'command_input_paths',
    'concat_list_members',
    'command_file_dependencies',
    'command_output_paths',
    'command_reads',
    'command_writes',
    'command_unresolved_dependencies',
    'ffmpeg_url_path',
    'command_output_path',
    'command_source_output_conflict',
    'unique_numbered_path',
]
