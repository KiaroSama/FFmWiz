"""FFmWiz guibridge overflow (guibridge_b) — split for file size.

Back-imports guibridge and is re-exported by it, so every consumer of
`from ffmwiz.guibridge import *` still sees the full set. Monkeypatch-safe.
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
import logging
import atexit
import queue
import threading
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
from ffmwiz.support.ext00 import *  # noqa: F401,F403
from ffmwiz.support.ext01 import *  # noqa: F401,F403
from ffmwiz.support.ext02 import *  # noqa: F401,F403
from ffmwiz.support.ext03 import *  # noqa: F401,F403
from ffmwiz.support.ext04 import *  # noqa: F401,F403
from ffmwiz.support.ext05 import *  # noqa: F401,F403
from ffmwiz.support.ext06 import *  # noqa: F401,F403
from ffmwiz.support.ext07 import *  # noqa: F401,F403
from ffmwiz.support.ext08 import *  # noqa: F401,F403
from ffmwiz.support.ext09 import *  # noqa: F401,F403
from ffmwiz.support.ext10 import *  # noqa: F401,F403
from ffmwiz.support.ext11 import *  # noqa: F401,F403
from ffmwiz.support.ext12 import *  # noqa: F401,F403
from ffmwiz.appio import *  # noqa: F401,F403
from ffmwiz import appio  # noqa: F401
from ffmwiz.runner import *  # noqa: F401,F403
from ffmwiz import runner  # noqa: F401
from ffmwiz.runtime import *  # noqa: F401,F403
from ffmwiz import runtime  # noqa: F401
from ffmwiz.services import *  # noqa: F401,F403
from ffmwiz import services  # noqa: F401
from ffmwiz.guibridge import *  # noqa: E402,F401,F403  (back-import)
from ffmwiz import guibridge  # noqa: E402,F401  (qualified self-ref for patched names)


def _launch_qt_gui(request: dict[str, Any]) -> dict[str, Any] | None:
    """Launch ffmwiz/gui/ffmwiz_gui.py as a subprocess, hand it the request via a
    temp JSON file, and return the parsed reply dict.

    Returns None only when the dedicated GUI is unavailable before launch
    (missing PySide6, missing ffmwiz/gui/ffmwiz_gui.py, etc.). Once the Qt GUI starts,
    internal GUI errors are returned as {"status": "error", ...} so callers
    do not hide real bugs behind archived fallback helpers.
    """
    gui_path = _ffmwiz_gui_path()
    if not gui_path.exists():
        return None
    if not _pyside6_available():
        return None
    # Modern QML engine (opt-in) handles the UNIFIED video editor only; every
    # other mode keeps using the classic engine. Falls back to classic if the
    # QML files are missing.
    if request.get("mode") == "video_unified" and _gui_engine_selected() == "qml":
        qml_script = _qml_gui_path()
        qml_file = script_dir() / "ffmwiz" / FFMWIZ_GUI_DIR_NAME / "qml" / "UnifiedEditor.qml"
        if qml_script.exists() and qml_file.exists():
            gui_path = qml_script
            log_info("Using modern QML GUI engine for the unified video editor.")
        else:
            log_warn("QML GUI engine selected but its files are missing; using the classic editor.")

    request_payload = dict(request)
    # Serialize Path objects to plain strings for JSON.
    for key, value in list(request_payload.items()):
        if isinstance(value, Path):
            request_payload[key] = str(value)
    request_payload["parent_pid"] = os.getpid()

    with tempfile.TemporaryDirectory(prefix="ffmwiz_ipc_") as tmp:
        tmp_path = Path(tmp)
        req_path = tmp_path / "request.json"
        rep_path = tmp_path / "reply.json"
        req_path.write_text(json.dumps(request_payload, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, str(gui_path),
               "--request", str(req_path),
               "--reply", str(rep_path)]
        try:
            result = subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            tb = traceback.format_exc()
            log_exception(f"Qt GUI subprocess failed: {exc}")
            return {"status": "error", "message": f"Qt GUI subprocess failed: {exc}", "traceback": tb}
        # One log record PER LINE. Writing the whole capture as a single record
        # left every line after the first without a timestamp or level, which is
        # most of a real GUI session's log.
        for line in (result.stdout or "").splitlines():
            if line.strip():
                log_debug("Qt GUI stdout: " + line.rstrip())
        emit = log_debug if result.returncode == 0 else log_error
        for line in (result.stderr or "").splitlines():
            if line.strip():
                emit("Qt GUI stderr: " + line.rstrip())
        if result.returncode != 0:
            try:
                payload = json.loads(rep_path.read_text(encoding="utf-8"))
            except Exception:
                payload = None
            if isinstance(payload, dict) and payload.get("status") == "error":
                message = payload.get("message") or "Qt GUI failed."
                appio.error(f"Qt GUI reported: {message}")
                if payload.get("traceback"):
                    log_error(payload["traceback"])
                if os.environ.get("FFMWIZ_DEBUG") and payload.get("traceback"):
                    print(payload["traceback"])
                return payload
            message = f"Qt GUI exited with code {result.returncode}."
            log_error(message)
            return {"status": "error", "message": message}
        if not rep_path.exists():
            message = "Qt GUI exited without writing a reply file."
            log_error(message)
            return {"status": "error", "message": message}
        try:
            payload = json.loads(rep_path.read_text(encoding="utf-8"))
            log_info(f"Qt GUI returned status={payload.get('status')}")
            return payload
        except Exception as exc:
            tb = traceback.format_exc()
            log_exception(f"Could not parse Qt GUI reply: {exc}")
            if os.environ.get("FFMWIZ_DEBUG"):
                print(tb)
            return {"status": "error", "message": f"Could not parse Qt GUI reply: {exc}", "traceback": tb}


def choose_crop_graphically(answers: dict[str, Any]) -> tuple[int, int, int, int] | None:
    """Archived standalone Crop Editor GUI.

    Normal CLI prompts no longer call this helper. Use the Unified Video
    Editor for active graphical crop workflows.
    """
    if answers.get("video_streams"):
        try:
            source_w, source_h = first_video_size(answers)
        except Exception:
            source_w, source_h = 1920, 1080
    else:
        source_w, source_h = 1920, 1080
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    request = {
        "mode": "crop",
        "input_path": str(answers["input_path"]),
        "fps": float(services.get_video_fps(answers)) if "get_video_fps" in globals() else 25.0,
        "duration": float(duration),
        "source_w": int(source_w),
        "source_h": int(source_h),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
    }
    reply = guibridge._launch_qt_gui(request)
    if reply is not None:
        if reply.get("status") == "ok":
            margins = reply.get("margins") or [0, 0, 0, 0]
            try:
                t, l, r, b = (int(x) for x in margins)
                return t, l, r, b
            except Exception:
                return None
        if reply.get("status") == "error":
            answers["_last_gui_error"] = "crop"
            appio.error("Crop GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
            return None
        return None
    appio.note(
        "Falling back to the archived legacy Tk crop preview. To enable the archived Qt helper later, "
        "run:  py -3 -m pip install -r requirements.txt  (or restart FFmWiz with "
        "FFMWIZ_AUTO_INSTALL=1)."
    )
    return _choose_crop_graphically_tk(answers)


def open_cut_gui(
    answers: dict[str, Any],
    fps: float,
    duration: float,
) -> list[tuple[float, float]] | None:
    """Archived standalone Cut Editor GUI.

    Normal CLI prompts no longer call this helper. Use the Unified Video
    Editor for active graphical cut workflows; Mode 3 is manual-only.
    """
    request = {
        "mode": "cut",
        "input_path": str(answers["input_path"]),
        "fps": float(fps),
        "duration": float(duration),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "ffprobe": answers.get("ffprobe") or shutil.which("ffprobe") or "ffprobe",
        "chapters": (answers.get("probe") or {}).get("chapters") or [],
        "log_path": str(log_path()) if log_path() is not None else "",
    }
    reply = guibridge._launch_qt_gui(request)
    if reply is not None:
        if reply.get("status") == "ok":
            ranges = reply.get("keep_ranges") or []
            normalized: list[tuple[float, float]] = []
            for entry in ranges:
                try:
                    s, e = float(entry[0]), float(entry[1])
                except Exception:
                    continue
                if e > s:
                    normalized.append((s, e))
            if reply.get("cuts_applied") and not normalized:
                appio.error("Every frame is cut - nothing would remain. Adjust the cuts.")
                return None
            return normalized
        if reply.get("status") == "error":
            answers["_last_gui_error"] = "cut"
            appio.error("Cut GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
            return None
        return None  # canceled
    appio.note(
        "Falling back to the archived legacy Tk cut editor. To enable the archived Qt helper later, "
        "run:  py -3 -m pip install -r requirements.txt  (or restart FFmWiz with "
        "FFMWIZ_AUTO_INSTALL=1)."
    )
    return _open_legacy_cut_gui_tk(answers, fps, duration)


def open_video_speed_gui(answers: dict[str, Any]) -> dict[str, Any] | None:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    request = {
        "mode": "video_speed",
        "input_path": str(answers["input_path"]),
        "duration": float(duration),
        "fps": float(services.get_video_fps(answers)),
        "has_audio": bool(answers.get("audio_streams")),
        "audio_count": len(answers.get("audio_streams") or []),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
    }
    reply = guibridge._launch_qt_gui(request)
    if reply is None:
        appio.error("Graphical video speed editor is not available. Install PySide6 and try again.")
        return None
    if reply.get("status") == "ok":
        try:
            return {
                "speed": clamp_speed_factor(reply.get("speed", DEFAULT_SPEED_FACTOR)),
                "reverse": bool(reply.get("reverse")),
                "include_audio": bool(reply.get("include_audio", True)),
            }
        except ValueError as exc:
            appio.error(str(exc))
            return None
    if reply.get("status") == "error":
        answers["_last_gui_error"] = "video_speed"
        appio.error("Video speed GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
    return None


def open_unified_video_gui(answers: dict[str, Any]) -> dict[str, Any] | None:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    join_segments: list[dict[str, Any]] = []
    if answers.get("join_input_items"):
        first_segment = {
            "path": str(answers["input_path"]),
            "name": Path(answers["input_path"]).name,
            "duration": float(duration),
            "chapters": (answers.get("probe") or {}).get("chapters") or [],
            # Per-segment, because the request-level has_audio is input 0 only:
            # the editors need to know which inputs actually carry audio before
            # they build a concat filtergraph over all of them (D07).
            "has_audio": bool(answers.get("audio_streams")),
        }
        join_segments.append(first_segment)
        for item in answers.get("join_input_items") or []:
            join_segments.append(
                {
                    "path": str(item.get("path")),
                    "name": Path(item.get("path")).name,
                    "duration": float(item.get("duration") or 0.0),
                    "chapters": (item.get("probe") or {}).get("chapters") or [],
                    "has_audio": bool(item.get("audio_streams")),
                }
            )
        if join_segments:
            duration = sum(max(0.0, float(segment.get("duration") or 0.0)) for segment in join_segments)
    try:
        source_w, source_h = first_video_size(answers)
    except Exception:
        source_w, source_h = 1920, 1080
    chapters = []
    if join_segments:
        offset = 0.0
        for segment_idx, segment in enumerate(join_segments, start=1):
            for chapter in segment.get("chapters") or []:
                copied = dict(chapter)
                try:
                    start_time = float(copied.get("start_time", copied.get("start", 0)))
                    end_time = float(copied.get("end_time", copied.get("end", start_time)))
                    copied["start_time"] = f"{start_time + offset:.6f}"
                    copied["end_time"] = f"{end_time + offset:.6f}"
                except Exception:
                    pass
                tags = dict(copied.get("tags") or {})
                if tags.get("title"):
                    tags["title"] = f"{tags['title']} (Video {segment_idx})"
                copied["tags"] = tags
                chapters.append(copied)
            offset += max(0.0, float(segment.get("duration") or 0.0))
    else:
        chapters = (answers.get("probe") or {}).get("chapters") or []
    request = {
        "mode": "video_unified",
        "input_path": str(answers["input_path"]),
        "duration": float(duration),
        "fps": float(services.get_video_fps(answers)),
        "source_w": int(source_w),
        "source_h": int(source_h),
        # The whole join topology: an editor must offer its audio controls when
        # ANY input is audible, not only when input 1 is (R02). Which segments
        # actually carry audio is in join_segments below.
        "has_audio": any_join_audio(answers),
        "audio_count": len(answers.get("audio_streams") or []),
        "chapters": chapters,
        "join_segments": join_segments,
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
        "start_maximized": True,
        # Carry the previous session's edits back into the editor so reopening it
        # (e.g. after pressing back from a later step) restores the prior crop,
        # cuts, split points, speed, and reverse instead of starting from zero.
        "initial_margins": [
            int(answers.get("crop_top", 0) or 0),
            int(answers.get("crop_left", 0) or 0),
            int(answers.get("crop_right", 0) or 0),
            int(answers.get("crop_bottom", 0) or 0),
        ],
        "initial_keep_ranges": [
            [float(s), float(e)] for s, e in (answers.get("_unified_cut_keep_ranges") or [])
        ],
        "initial_separator_points": [
            float(v) for v in (answers.get("_unified_separator_points") or [])
        ],
        "initial_speed": float(answers.get("_unified_video_speed") or 1.0),
        "initial_reverse": bool(answers.get("_unified_reverse_video")),
        "initial_include_audio": bool(
            answers.get("_unified_include_audio", any_join_audio(answers))
        ),
    }
    if join_segments:
        log_info(
            "Opening Unified Video Editor with joined inputs: "
            + ", ".join(f"{idx + 1}:{Path(segment.get('path') or '').name}" for idx, segment in enumerate(join_segments))
        )
    reply = guibridge._launch_qt_gui(request)
    if reply is None:
        appio.error("Unified graphical video editor is not available. Install PySide6 and try again.")
        return None
    if reply.get("status") == "ok":
        try:
            margins = reply.get("margins") or [0, 0, 0, 0]
            top, left, right, bottom = (int(x) for x in margins)
            keep_ranges: list[tuple[float, float]] = []
            for entry in reply.get("keep_ranges") or []:
                s, e = float(entry[0]), float(entry[1])
                if e > s:
                    keep_ranges.append((s, e))
            # An empty keep list is ambiguous on its own: it means "no cuts" for
            # a normal edit and "every frame is cut" when the editor reports
            # cuts_applied. Refuse the second instead of silently exporting the
            # untouched source (D13).
            if reply.get("cuts_applied") and not keep_ranges:
                appio.error("Every frame is cut - nothing would remain. Adjust the cuts.")
                return None
            separators = normalize_separator_points(reply.get("separator_points") or [], duration)
            return {
                "margins": (top, left, right, bottom),
                "keep_ranges": normalize_cut_ranges(keep_ranges, duration),
                "separator_points": separators,
                "speed": clamp_speed_factor(reply.get("speed", DEFAULT_SPEED_FACTOR)),
                "reverse": bool(reply.get("reverse")),
                "include_audio": bool(reply.get("include_audio", True)),
            }
        except Exception as exc:
            appio.error(f"Unified video editor returned invalid data: {exc}")
            return None
    if reply.get("status") == "error":
        answers["_last_gui_error"] = "video_unified"
        appio.error("Unified video GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
    return None


def open_audio_transform_gui(answers: dict[str, Any], audio_index: int) -> dict[str, Any] | None:
    duration = services.stream_duration_seconds({}, answers.get("format")) or 0.0
    request = {
        "mode": "audio_transform",
        "input_path": str(answers["input_path"]),
        "audio_index": int(audio_index),
        "duration": float(duration),
        "ffmpeg": answers.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
        "log_path": str(log_path()) if log_path() is not None else "",
        "start_maximized": True,
    }
    reply = guibridge._launch_qt_gui(request)
    if reply is None:
        appio.error("Graphical audio transform editor is not available. Install PySide6 and try again.")
        return None
    if reply.get("status") == "ok":
        try:
            ranges: list[tuple[float, float]] = []
            for entry in reply.get("keep_ranges") or []:
                s, e = float(entry[0]), float(entry[1])
                if e > s:
                    ranges.append((s, e))
            return {
                "keep_ranges": normalize_cut_ranges(ranges, duration),
                "speed": clamp_speed_factor(reply.get("speed", DEFAULT_SPEED_FACTOR)),
                "reverse": bool(reply.get("reverse")),
            }
        except Exception as exc:
            appio.error(f"Audio transform editor returned invalid data: {exc}")
            return None
    if reply.get("status") == "error":
        answers["_last_gui_error"] = "audio_transform"
        appio.error("Audio transform GUI failed. Set FFMWIZ_DEBUG=1 before running FFmWiz to print the full traceback.")
    return None


__all__ = [
    '_launch_qt_gui',
    'choose_crop_graphically',
    'open_cut_gui',
    'open_video_speed_gui',
    'open_unified_video_gui',
    'open_audio_transform_gui',
]
