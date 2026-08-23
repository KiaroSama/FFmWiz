"""FFmWiz GUI geometry / cut-range / history helpers, split from gui_common.

Pure logic (timecodes, range normalization, crop snapping, chapters, markers,
undo/redo history, cut-range computation). Injected into every GUI module by
ffmwiz_gui.py so cross-module references resolve at runtime.
"""
from __future__ import annotations

import argparse
import array
import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable


# =====================================================================
# Time / range helpers (duplicated from FFmWiz.py so this module can run
# as a fully standalone subprocess).
# =====================================================================


def seconds_to_timecode(seconds: float) -> str:
    if seconds is None or seconds < 0:
        seconds = 0.0
    total_ms = int(round(float(seconds) * 1000))
    hours, rem = divmod(total_ms, 3600 * 1000)
    minutes, rem = divmod(rem, 60 * 1000)
    secs = rem / 1000.0
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


# seconds_to_hmsf is IMPORTED, never copied. The copy that used to live here
# kept the mixed-clock bug after core/timeline.py was fixed: it counted total
# frames at the TRUE rate (23.976) and then split them with the ROUNDED one
# (24), losing (fps_int - fps) / fps_int per second -- -3.58 s per hour, and it
# made the editor's timecodes stop being the inverse of parse_hmsf_time. A
# single definition cannot drift again.
#
# This module also runs inside the standalone GUI subprocess, which is started
# as `python ffmwiz/gui/ffmwiz_gui.py` and therefore begins with only
# ffmwiz/gui on sys.path -- hence the explicit project-root entry. Appended,
# not inserted, so it can never shadow a sibling GUI module.
sys.path.append(str(Path(__file__).resolve().parents[2]))

from ffmwiz.core.timeline import seconds_to_hmsf  # noqa: E402,F401


def normalize_ranges(ranges, duration: float):
    duration = max(0.0, float(duration or 0.0))
    cleaned = []
    for entry in ranges or []:
        try:
            s = max(0.0, float(entry[0]))
            e = float(entry[1])
        except (TypeError, ValueError, IndexError):
            continue
        if duration > 0:
            s = min(s, duration)
            e = min(e, duration)
        if e <= s:
            continue
        cleaned.append((s, e))
    cleaned.sort()
    merged: list[tuple[float, float]] = []
    for s, e in cleaned:
        if merged and s <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def invert_cuts_to_keep(remove_ranges, duration: float):
    remove = normalize_ranges(remove_ranges, duration)
    keep = []
    cursor = 0.0
    for s, e in remove:
        if s > cursor:
            keep.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        keep.append((cursor, duration))
    return keep


def invert_cut_ranges(cut_ranges, duration: float):
    """Return the remove-ranges that keep the current cut-ranges."""
    return invert_cuts_to_keep(cut_ranges, duration)


def _snap_crop_axis_even(near: int, far: int, source_dim: int) -> tuple[int, int]:
    """Snap one crop axis so the origin (near = left/top) is even and the
    resulting output size is even, staying as close as possible to the requested
    pair. Mirrors the CLI crop normalization for the common 4:2:0 case so the
    editor never reports or returns odd crop dimensions.

    Deterministic priority (minimized in order): total adjustment, preserve the
    requested total crop (output size), smallest center shift, less content
    removed, smaller origin-side crop."""
    near = max(0, int(near))
    far = max(0, int(far))
    source_dim = int(source_dim)
    best_key = None
    best_pair = None
    window = 8
    while best_pair is None and window <= source_dim + 2:
        for n in range(max(0, near - window), near + window + 1):
            if n % 2:
                continue
            for f in range(max(0, far - window), far + window + 1):
                size = source_dim - n - f
                if size < 2 or size % 2:
                    continue
                key = (
                    abs(n - near) + abs(f - far),
                    abs((n + f) - (near + far)),
                    abs((n - f) - (near - far)),
                    n + f,
                    n,
                )
                if best_key is None or key < best_key:
                    best_key = key
                    best_pair = (n, f)
        window *= 2
    return best_pair if best_pair is not None else (near - (near % 2), far)


def snap_crop_margins_even(margins, source_w: int, source_h: int) -> list[int]:
    """Snap [top, left, right, bottom] crop margins to an even origin and even
    output size for the given source dimensions."""
    top, left, right, bottom = (int(v) for v in margins)
    h_near, h_far = _snap_crop_axis_even(left, right, source_w)
    v_near, v_far = _snap_crop_axis_even(top, bottom, source_h)
    return [v_near, h_near, h_far, v_far]


# Amplitude (16-bit sample value) at which the waveform reaches full height.
# Scaling is continuous and relative: quieter audio draws shorter bars and
# louder audio taller bars, capped (clamped) once it reaches this level. This is
# set below full-scale (32768) so normal audio uses the available height well
# instead of looking tiny, while very loud peaks clamp at the ceiling. ~ -3.4 dBFS.
WAVEFORM_CEILING_PEAK = 22000


def _chapter_time_seconds(chapter: dict[str, Any], key: str) -> float | None:
    text_key = f"{key}_time"
    if chapter.get(text_key) is not None:
        try:
            return float(chapter.get(text_key))
        except (TypeError, ValueError):
            return None
    raw = chapter.get(key)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    time_base = str(chapter.get("time_base") or "")
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", time_base)
    if match:
        numerator = float(match.group(1))
        denominator = max(1.0, float(match.group(2)))
        return value * numerator / denominator
    return value / 1000.0 if value > 10000 else value


def normalize_chapters(chapters, duration: float) -> list[dict[str, Any]]:
    duration = max(0.0, float(duration or 0.0))
    normalized: list[dict[str, Any]] = []
    for idx, chapter in enumerate(chapters or []):
        if not isinstance(chapter, dict):
            continue
        start = _chapter_time_seconds(chapter, "start")
        end = _chapter_time_seconds(chapter, "end")
        if start is None:
            continue
        if end is None or end <= start:
            end = duration if duration > start else start
        start = max(0.0, min(duration, start)) if duration else max(0.0, start)
        end = max(start, min(duration, end)) if duration else max(start, end)
        tags = chapter.get("tags") if isinstance(chapter.get("tags"), dict) else {}
        title = str(tags.get("title") or chapter.get("title") or f"Chapter {idx + 1}")
        normalized.append({"start": start, "end": end, "title": title})
    normalized.sort(key=lambda item: item["start"])
    return normalized


# =====================================================================
# PCM waveform helpers (stdlib only).
#
# NumPy is an optional accelerator for the waveform, never a requirement: it is
# not a declared dependency of FFmWiz, and the old `audioop` fallback these
# replace was removed in Python 3.13 (PEP 594), so on a numpy-less 3.13 both
# editors used to render an empty waveform with no error. These helpers are the
# shared floor both engines fall back to. (D08/D09)
# =====================================================================

def pcm_samples(data) -> "array.array":
    """Little-endian signed 16-bit samples of raw PCM `data` as a stdlib array.

    Trailing odd byte is dropped; the result is byte-swapped on big-endian hosts
    because `array('h')` uses native order while the PCM stream is LE."""
    samples = array.array("h")
    if not data:
        return samples
    view = memoryview(data).cast("B")
    samples.frombytes(bytes(view[: len(view) - (len(view) % 2)]))
    if sys.byteorder == "big":  # pragma: no cover - FFmWiz targets LE hosts
        samples.byteswap()
    return samples


def pcm_peak(data) -> int:
    """Largest ABSOLUTE int16 amplitude in `data` (0 when empty).

    Replaces audioop.max(data, 2)."""
    samples = pcm_samples(data)
    if not samples:
        return 0
    # int() before negating: -(-32768) is fine for Python ints but not int16.
    return max(int(max(samples)), -int(min(samples)))


def pcm_minmax(data) -> tuple[int, int]:
    """(min, max) int16 amplitude of `data`, (0, 0) when empty.

    Replaces audioop.minmax(data, 2)."""
    samples = pcm_samples(data)
    if not samples:
        return 0, 0
    return int(min(samples)), int(max(samples))


def reverse_chunk_spec(segments, win_start, win_end):
    """Map a reverse-preview window onto ONE source segment of a joined timeline.

    `segments` is [(start, duration), ...] on the joined timeline; a single
    input is just one entry. Returns (index, ss, dur, eff_start).

    A window that straddles a join boundary is SHORTENED to the segment that
    contains its end (eff_start > win_start) instead of keeping the full length
    with a clamped offset, which silently sourced the wrong content: the tail of
    the earlier segment was skipped and material past the window was shown
    (D16). The caller resumes the next chunk at `eff_start`.
    """
    win_start = max(0.0, float(win_start))
    win_end = max(win_start, float(win_end))
    if not segments:
        return 0, win_start, max(0.0, win_end - win_start), win_start
    # Probe just inside the window end so a window ending exactly on a boundary
    # still belongs to the segment before it.
    probe = max(0.0, win_end - 1e-3)
    index = len(segments) - 1
    for i, (start, duration) in enumerate(segments):
        if probe < float(start) + float(duration) - 1e-6:
            index = i
            break
    seg_start = float(segments[index][0])
    eff = max(win_start, seg_start)
    return index, eff - seg_start, max(0.0, win_end - eff), eff


def short_gui_label(value: Any, max_chars: int = 24) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 3)] + "..."


def _format_debug_ranges(ranges) -> str:
    normalized = []
    for entry in ranges or []:
        try:
            normalized.append((float(entry[0]), float(entry[1])))
        except (TypeError, ValueError, IndexError):
            continue
    if not normalized:
        return "[]"
    return "[" + ", ".join(f"({s:.6f}, {e:.6f})" for s, e in normalized) + "]"


# Windows VK codes for layout-independent shortcut handling. On Windows
# QKeyEvent.nativeVirtualKey() returns these codes regardless of the
# active keyboard layout (Persian, Arabic, etc.).
WIN_VK: dict[str, int] = {
    "space": 0x20, "return": 0x0D, "escape": 0x1B, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "tab": 0x09, "back": 0x08,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "plus": 0xBB, "equal": 0xBB, "minus": 0xBD,
    "kp_add": 0x6B, "kp_subtract": 0x6D, "kp_enter": 0x0D,
    "alt": 0x12,
}
for _c in "abcdefghijklmnopqrstuvwxyz":
    WIN_VK[_c] = ord(_c.upper())
for _d in "0123456789":
    WIN_VK[_d] = ord(_d)


# =====================================================================
# Marker model + history. Cut Editor uses a list of typed markers; cut
# ranges are derived by pairing consecutive In/Out markers in time order.
# =====================================================================


@dataclass
class Marker:
    id: int
    time: float
    kind: str  # "in" or "out"


@dataclass
class CutSnapshot:
    markers: list[Marker] = field(default_factory=list)
    selected_marker_ids: tuple[int, ...] = ()


@dataclass
class CropSnapshot:
    margins: tuple[int, int, int, int] = (0, 0, 0, 0)


class HistoryStack:
    """Snapshot-based undo/redo stack.

    Callers push() a fresh snapshot after every edit-affecting action.
    undo()/redo() return the snapshot to restore, or None when empty.
    """

    def __init__(self, initial: Any, max_size: int = 100) -> None:
        self._undo: list[Any] = []
        self._redo: list[Any] = []
        self._current = copy.deepcopy(initial)
        self._max = max_size

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def push(self, snapshot: Any) -> None:
        if self._current is not None and self._equals(self._current, snapshot):
            return  # skip duplicate
        if self._current is not None:
            self._undo.append(copy.deepcopy(self._current))
            if len(self._undo) > self._max:
                self._undo.pop(0)
        self._current = copy.deepcopy(snapshot)
        self._redo.clear()

    def undo(self) -> Any | None:
        if not self._undo:
            return None
        if self._current is not None:
            self._redo.append(copy.deepcopy(self._current))
            if len(self._redo) > self._max:
                self._redo.pop(0)
        self._current = self._undo.pop()
        return copy.deepcopy(self._current)

    def redo(self) -> Any | None:
        if not self._redo:
            return None
        if self._current is not None:
            self._undo.append(copy.deepcopy(self._current))
            if len(self._undo) > self._max:
                self._undo.pop(0)
        self._current = self._redo.pop()
        return copy.deepcopy(self._current)

    @staticmethod
    def _equals(a: Any, b: Any) -> bool:
        try:
            return a == b
        except Exception:
            return False


def compute_cut_ranges(markers: list[Marker], duration: float) -> list[tuple[float, float]]:
    """Pair sorted In/Out markers in time order into cut ranges.

    Unmatched In markers (no following Out) and stray Out markers (no
    preceding In) are silently dropped."""
    sorted_m = sorted(markers, key=lambda m: m.time)
    cuts: list[tuple[float, float]] = []
    pending_in: float | None = None
    for m in sorted_m:
        t = max(0.0, min(duration, m.time))
        if m.kind == "in":
            pending_in = t
        elif m.kind == "out":
            if pending_in is not None and t > pending_in:
                cuts.append((pending_in, t))
            pending_in = None
    return normalize_ranges(cuts, duration)


# =====================================================================
# Qt imports are deferred so the file is importable for tooling even
# when PySide6 is missing. Everything below only runs in the GUI subprocess.
# =====================================================================
