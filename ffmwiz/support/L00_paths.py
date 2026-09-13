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


def _progress_output_paths_from_command(cmd: list[str]) -> list[Path]:
    """Infer the final output path for simple single-output FFmpeg commands."""
    if not cmd:
        return []
    candidate = str(cmd[-1] or "").strip()
    if not candidate or candidate.startswith("-"):
        return []
    lowered = candidate.lower()
    blocked = {"-", "nul", "null", os.devnull.lower()}
    if lowered in blocked or lowered.startswith("pipe:"):
        return []
    try:
        path = Path(candidate)
    except (TypeError, ValueError):
        return []
    return [path] if path.suffix else []


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
    try:
        if output_location.is_dir():
            return False
        if output_location.is_file():
            return True
    except OSError:
        pass
    return bool(output_location.suffix)


def command_input_paths(cmd: list[str]) -> list[Path]:
    """Every path an FFmpeg argv reads as an INPUT (`-i <path>`)."""
    inputs: list[Path] = []
    for index, token in enumerate(cmd[:-1] if cmd else []):
        if str(token) != "-i":
            continue
        value = str(cmd[index + 1] or "").strip()
        if not value or value.startswith("-"):
            continue
        lowered = value.lower()
        if lowered.startswith("pipe:") or lowered in {"-", "nul", "null", os.devnull.lower()}:
            continue
        try:
            inputs.append(Path(value))
        except (TypeError, ValueError):
            continue
    return inputs


def command_output_path(cmd: list[str]) -> Path | None:
    """The destination an FFmpeg argv writes, or None for a non-file sink."""
    if not cmd:
        return None
    candidate = str(cmd[-1] or "").strip()
    if not candidate or candidate.startswith("-"):
        return None
    lowered = candidate.lower()
    if lowered.startswith("pipe:") or lowered in {"-", "nul", "null", os.devnull.lower()}:
        return None
    if len(cmd) >= 2 and str(cmd[-2]) == "-i":
        return None        # the last token is an INPUT, so this command has no file output
    try:
        return Path(candidate)
    except (TypeError, ValueError):
        return None


def command_source_output_conflict(cmd: list[str]) -> tuple[Path, Path] | None:
    """(input, output) when running `cmd` would overwrite one of its own inputs.

    Planning resolves a safe destination, but that is not the last word. The
    destination can BECOME an alias of a source afterwards -- a hardlink created
    between confirmation and execution -- and a caller can simply have failed to
    consider one of its sources at all: Track Manager checked the primary file
    and never the external tracks it also reads. This is the final point at
    which the command is still data rather than a running process, so the
    identity check is repeated here for every caller at once.
    """
    output = command_output_path(cmd)
    if output is None:
        return None
    for source in command_input_paths(cmd):
        if paths_same(output, source):
            return source, output
    return None


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
    'command_input_paths',
    'command_output_path',
    'command_source_output_conflict',
    'unique_numbered_path',
]
