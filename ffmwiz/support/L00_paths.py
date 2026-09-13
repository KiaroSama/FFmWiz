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


def _is_ffmpeg_option(token: str) -> bool:
    """A dashed token that is an OPTION, not a negative number (`-aq -1`)."""
    text = str(token or "")
    if not text.startswith("-") or len(text) == 1:
        return False
    try:
        float(text)
    except ValueError:
        return True
    return False


def _takes_a_value(token: str) -> bool:
    """Whether this option consumes the next token, per the shared contract."""
    base = str(token or "").lower()
    if base in FFMPEG_VALUELESS_OPTIONS:
        return False
    stem = base.partition(":")[0]
    if stem in FFMPEG_VALUELESS_OPTIONS:
        return False
    if stem.startswith("-no") and len(stem) > 3:
        return False            # FFmpeg's boolean-off spelling
    if base in FFMPEG_ALL_VALUED_OPTIONS or stem in FFMPEG_ALL_VALUED_OPTIONS:
        return True
    # Unknown. For a SAFETY guard the protective reading is that the next token
    # is POSITIONAL, because an over-detected output costs nothing -- it only
    # matters if it is identical to a source -- while a missed one is a
    # destroyed file.
    return False


def _names_a_file(value: str) -> bool:
    text = str(value or "").strip()
    if not text or text.startswith("-"):
        return False
    lowered = text.lower()
    if lowered.startswith("pipe:") or lowered in {"-", "nul", "null", os.devnull.lower()}:
        return False
    return True


def _as_path(value: str):
    """A local path for `value`, including the `file:` URL spelling.

    `file:///C:/media/clip.mkv` and the plain Windows path are the same file and
    FFmpeg accepts both, so comparing the literal text alone meant the URL form
    was never recognised as a source (A01).
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.lower().startswith("file:"):
        parsed = urlparse(text)
        path_text = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:", path_text):
            path_text = path_text[1:]
        elif parsed.netloc:
            path_text = f"//{parsed.netloc}{path_text}"
        text = path_text.replace("/", os.sep)
    try:
        return Path(text)
    except (TypeError, ValueError):
        return None


def command_input_paths(cmd: list[str]) -> list[Path]:
    """Every path an FFmpeg argv reads as an INPUT (`-i <path>`)."""
    inputs: list[Path] = []
    for index, token in enumerate(cmd[:-1] if cmd else []):
        if str(token) != "-i":
            continue
        value = str(cmd[index + 1] or "").strip()
        if not _names_a_file(value):
            continue
        path = _as_path(value)
        if path is not None:
            inputs.append(path)
    return inputs


def concat_list_members(listing: Path) -> list[Path]:
    """The media a `-f concat` list names, resolved relative to the list itself.

    Media reached this way is never on the command line, so a guard reading only
    argv protected none of it (A01). Bounded and forgiving: an unreadable or
    oversized list yields nothing rather than raising where a job starts.
    """
    try:
        if listing.stat().st_size > CONCAT_LIST_MAX_BYTES:
            return []
        text = listing.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    members: list[Path] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("file "):
            continue
        value = stripped[5:].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in QUOTE_CHARS:
            value = value[1:-1]
        path = _as_path(value)
        if path is None:
            continue
        members.append(path if path.is_absolute() else (listing.parent / path))
    return members


def command_file_dependencies(cmd: list[str]) -> list[Path]:
    """Every file the command READS: inputs, concat members, file-valued options.

    The complete read side of the manifest. `-i` alone was not it: media can
    arrive through a concat list, an input can be spelled as a `file:` URL, and
    an attachment is consumed by `-attach` without ever being an input (A01).
    """
    reads: list[Path] = list(command_input_paths(cmd))
    concat_selected = False
    for index, token in enumerate(cmd or []):
        lowered = str(token or "").lower()
        following = str(cmd[index + 1] or "") if index + 1 < len(cmd) else ""
        if lowered == "-f" and following.lower() == "concat":
            concat_selected = True
            continue
        if lowered == "-i" and concat_selected:
            listing = _as_path(following)
            if listing is not None:
                reads.extend(concat_list_members(listing))
            concat_selected = False
            continue
        if lowered.partition(":")[0] in FFMPEG_FILE_VALUED_OPTIONS and _names_a_file(following):
            path = _as_path(following)
            if path is not None:
                reads.append(path)
    return reads


def command_output_paths(cmd: list[str]) -> list[Path]:
    """Every destination an FFmpeg argv writes.

    A command has as many outputs as it has positional tokens the options did
    not claim: `-map 0:v out.mkv -map 0:a out.mka` writes two. Reading only
    `cmd[-1]` handed an alias of the source at the FIRST destination straight to
    FFmpeg (A01).
    """
    outputs: list[Path] = []
    index = 1                    # cmd[0] is the executable
    while index < len(cmd or []):
        token = str(cmd[index] or "")
        if _is_ffmpeg_option(token):
            index += 2 if _takes_a_value(token) else 1
            continue
        if _names_a_file(token):
            path = _as_path(token)
            if path is not None:
                outputs.append(path)
        index += 1
    return outputs


def command_output_path(cmd: list[str]):
    """The LAST destination, for callers that only ever had one."""
    outputs = command_output_paths(cmd)
    return outputs[-1] if outputs else None


def command_source_output_conflict(cmd: list[str], extra_sources=None):
    """(source, output) when running `cmd` would overwrite something it reads.

    Planning resolves a safe destination, but that is not the last word. The
    destination can BECOME an alias of a source afterwards -- a hardlink created
    between confirmation and execution -- and a caller can simply have failed to
    consider one of its sources at all. This is the final point at which the
    command is still data rather than a running process, so the check is
    repeated here for every caller at once.

    Every destination is compared against every source, including the ones argv
    only implies: concat members, `file:` URLs and file-valued options.
    `extra_sources` carries what the PLANNER knows and argv cannot show; an
    explicit declaration beats another heuristic.

    Not a proof of safety. A preflight stat cannot close the window between the
    check and the open, which is why a caller that can should publish through a
    staged path rather than write its destination in place.
    """
    outputs = command_output_paths(cmd)
    if not outputs:
        return None
    sources = list(command_file_dependencies(cmd)) + list(extra_sources or [])
    for output in outputs:
        for source in sources:
            if source and paths_same(source, output):
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
    'path_is_existing_directory',
    'command_input_paths',
    'concat_list_members',
    'command_file_dependencies',
    'command_output_paths',
    'command_output_path',
    'command_source_output_conflict',
    'unique_numbered_path',
]
