"""FFmWiz guibridge cluster (extracted from FFmWiz.py, method الف)."""
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


class _UIPalette:
    BG = "#0e1217"             # main app background
    PANEL = "#161a22"          # toolbar / header band
    PANEL_HI = "#1b2030"
    SURFACE = "#1d232d"        # button / widget surface
    SURFACE_HOVER = "#252c39"
    SURFACE_PRESSED = "#2e3a52"
    SURFACE_DIS = "#161a22"
    BORDER = "#2e3a4f"
    BORDER_SOFT = "#1f2937"
    TIMELINE_BG = "#0f1422"
    TIMELINE_TRACK = "#1c2336"
    TIMELINE_TICK = "#5b6f91"
    TIMELINE_TICK_HI = "#c2cbe1"
    ACCENT = "#5b9eff"         # primary action accent
    ACCENT_STRONG = "#7ab0ff"
    ACCENT_DARK = "#1f3a66"
    ACCENT_RED = "#ff6f6f"
    ACCENT_RED_DK = "#c44a4a"
    ACCENT_GREEN = "#7be07b"
    ACCENT_YELLOW = "#f5d66a"
    ACCENT_ORANGE = "#f5b341"
    PLAYHEAD = "#ffffff"
    TEXT = "#f5f7fb"
    TEXT_DIM = "#c2cbe1"
    TEXT_MUTE = "#7c8aa6"
    TEXT_ON_ACCENT = "#0e1217"


def _apply_app_ttk_theme(root: Any) -> None:
    """Apply the unified FFmWiz dark ttk theme to the given Tk root."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return
    palette = _UIPalette
    try:
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=palette.BG)
        style.configure("Panel.TFrame", background=palette.PANEL)
        style.configure("Surface.TFrame", background=palette.SURFACE)
        style.configure("TLabel", background=palette.BG, foreground=palette.TEXT)
        style.configure("Panel.TLabel", background=palette.PANEL, foreground=palette.TEXT)
        style.configure("Dim.TLabel", background=palette.BG, foreground=palette.TEXT_DIM)
        style.configure("Muted.TLabel", background=palette.BG, foreground=palette.TEXT_MUTE)
        style.configure(
            "Header.TLabel",
            background=palette.PANEL,
            foreground=palette.TEXT,
            font=("Segoe UI Semibold", 12),
        )
        style.configure(
            "Title.TLabel",
            background=palette.PANEL,
            foreground=palette.ACCENT_STRONG,
            font=("Segoe UI Semibold", 13),
        )
        style.configure(
            "TButton",
            background=palette.SURFACE,
            foreground=palette.TEXT,
            bordercolor=palette.BORDER,
            focusthickness=0,
            padding=(12, 7),
            font=("Segoe UI", 9),
        )
        style.map(
            "TButton",
            background=[
                ("active", palette.SURFACE_HOVER),
                ("pressed", palette.SURFACE_PRESSED),
                ("disabled", palette.SURFACE_DIS),
            ],
            foreground=[("disabled", palette.TEXT_MUTE)],
        )
        # Primary action button (used for Confirm / Apply / Play).
        style.configure(
            "Accent.TButton",
            background=palette.ACCENT,
            foreground=palette.TEXT_ON_ACCENT,
            bordercolor=palette.ACCENT_STRONG,
            padding=(12, 7),
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", palette.ACCENT_STRONG),
                ("pressed", palette.ACCENT_STRONG),
                ("disabled", palette.SURFACE_DIS),
            ],
            foreground=[("disabled", palette.TEXT_MUTE)],
        )
        style.configure(
            "Danger.TButton",
            background=palette.SURFACE,
            foreground=palette.ACCENT_RED,
            padding=(12, 7),
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#2a1f24"), ("pressed", "#3a1f24")],
        )
        style.configure(
            "Tool.TButton",
            background=palette.SURFACE,
            foreground=palette.TEXT,
            padding=(10, 6),
        )
        style.configure(
            "ToolActive.TButton",
            background=palette.ACCENT_DARK,
            foreground=palette.ACCENT_STRONG,
            padding=(10, 6),
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "ToolActive.TButton",
            background=[("active", palette.ACCENT_DARK), ("pressed", palette.ACCENT_DARK)],
        )
        style.configure(
            "TScale",
            background=palette.BG,
            troughcolor=palette.TIMELINE_TRACK,
            bordercolor=palette.BORDER,
        )
        style.configure("TSeparator", background=palette.BORDER)
        style.configure(
            "TLabelframe",
            background=palette.BG,
            foreground=palette.TEXT_DIM,
            bordercolor=palette.BORDER,
        )
        style.configure(
            "TLabelframe.Label",
            background=palette.BG,
            foreground=palette.TEXT_DIM,
        )
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=palette.SURFACE,
            troughcolor=palette.PANEL,
            bordercolor=palette.BG,
            arrowcolor=palette.ACCENT_YELLOW,
            darkcolor=palette.SURFACE,
            lightcolor=palette.SURFACE_HOVER,
        )
        style.configure(
            "Dark.Horizontal.TScrollbar",
            background=palette.SURFACE,
            troughcolor=palette.PANEL,
            bordercolor=palette.BG,
            arrowcolor=palette.ACCENT_YELLOW,
            darkcolor=palette.SURFACE,
            lightcolor=palette.SURFACE_HOVER,
        )
        style.map(
            "Dark.Vertical.TScrollbar",
            background=[("active", palette.SURFACE_HOVER), ("pressed", palette.SURFACE_PRESSED)],
        )
        style.map(
            "Dark.Horizontal.TScrollbar",
            background=[("active", palette.SURFACE_HOVER), ("pressed", palette.SURFACE_PRESSED)],
        )
    except Exception:
        pass


WIN_VK_BY_NAME: dict[str, int] = {
    "space": 0x20, "return": 0x0D, "enter": 0x0D, "escape": 0x1B,
    "delete": 0x2E, "back": 0x08, "tab": 0x09,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "plus": 0xBB, "equal": 0xBB, "minus": 0xBD,
    "kp_add": 0x6B, "kp_subtract": 0x6D, "kp_enter": 0x0D,
}


def _bind_layout_independent_keys(
    root: Any,
    bindings: list[dict[str, Any]],
    is_text_focus_fn: Callable[[Any], bool] | None = None,
) -> None:
    """Bind keyboard shortcuts that work regardless of keyboard layout.

    Each binding is a dict with these keys:
        "key": "h" | "z" | "space" | "return" | ... (looked up in
                WIN_VK_BY_NAME for the VK code).
        "alt_keysyms": optional list of keysyms (lower-case) that should
                also trigger this binding when matched.
        "shift": True | False | None (None = "don't care")
        "ctrl":  True | False | None
        "alt":   True | False | None
        "callback": callable(event) -> None
        "allow_in_text": True to fire even when an Entry/Text widget
                has focus. Default False.

    Earlier entries win when multiple match.
    """
    parsed: list[dict[str, Any]] = []
    for spec in bindings:
        key_name = str(spec.get("key", "")).lower()
        vk = WIN_VK_BY_NAME.get(key_name)
        keysyms: set[str] = set()
        if key_name:
            keysyms.add(key_name)
        # Add convenient aliases.
        if key_name in ("plus", "equal"):
            keysyms.update({"plus", "equal", "kp_add"})
        elif key_name == "minus":
            keysyms.update({"minus", "kp_subtract"})
        elif key_name in ("return", "enter"):
            keysyms.update({"return", "kp_enter"})
        for alt in spec.get("alt_keysyms", []) or []:
            keysyms.add(str(alt).lower())
        parsed.append({
            "vk": vk,
            "keysyms": keysyms,
            "shift": spec.get("shift"),
            "ctrl": spec.get("ctrl"),
            "alt": spec.get("alt"),
            "callback": spec.get("callback"),
            "allow_in_text": spec.get("allow_in_text", False),
        })

    def _matches(event: Any, entry: dict[str, Any]) -> bool:
        state = getattr(event, "state", 0) or 0
        shift_held = bool(state & 0x0001)
        ctrl_held = bool(state & 0x0004)
        # Windows: Alt = 0x20000. X11 Mod1: 0x0008.
        alt_held = bool(state & 0x20000) or bool(state & 0x0008)
        for name, val in (("shift", shift_held), ("ctrl", ctrl_held), ("alt", alt_held)):
            required = entry.get(name)
            if required is None:
                continue
            if bool(required) != val:
                return False
        vk = entry.get("vk")
        if vk is not None and event.keycode == vk:
            return True
        return (event.keysym or "").lower() in entry["keysyms"]

    def _dispatch(event: Any) -> Any:
        for entry in parsed:
            if not entry["allow_in_text"] and is_text_focus_fn and is_text_focus_fn(event):
                continue
            if _matches(event, entry):
                cb = entry["callback"]
                if cb is not None:
                    cb(event)
                return "break"
        return None

    # Use add="+" so we don't clobber any existing bindings on root.
    root.bind("<KeyPress>", _dispatch, add="+")


class _PreviewScheduler:
    """Debounced, asynchronous, cached frame-extraction pump for GUIs.

    The extract callable runs on a worker thread so the Tk main loop stays
    responsive while the user scrubs. Worker results are marshalled back to
    the main thread through a thread-safe queue that is drained from a
    periodic Tk after() poll - calling root.after() directly from a worker
    thread is not safe across all Tk builds.

    Usage:
        scheduler = _PreviewScheduler(
            root,
            extract_fn=lambda t, w, h: extract_crop_preview_frame(answers, temp_dir, w, h, t),
            on_ready=lambda path: my_redraw(path),
            debounce_ms=70,
        )
        cached = scheduler.request(timestamp_s, width, height)
        if cached is not None:
            draw_image(cached)
        # else: extraction was scheduled; on_ready will fire from the main thread.

    Call scheduler.cancel() before destroying the root window.
    """

    def __init__(
        self,
        root: Any,
        extract_fn: Callable[[float, int, int], Path],
        on_ready: Callable[[Path], None],
        debounce_ms: int = 70,
        poll_ms: int = 25,
    ) -> None:
        import queue

        self._root = root
        self._extract = extract_fn
        self._on_ready = on_ready
        self._debounce_ms = max(0, int(debounce_ms))
        self._poll_ms = max(5, int(poll_ms))
        self._cache: dict[tuple[int, int, int], Path] = {}
        self._after_id: str | None = None
        self._poll_id: str | None = None
        self._pending: tuple[tuple[int, int, int], float, int, int] | None = None
        self._queued_next: tuple[tuple[int, int, int], float, int, int] | None = None
        self._worker_busy = False
        self._destroyed = False
        self._results: queue.Queue = queue.Queue()
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if self._destroyed:
            return
        try:
            self._poll_id = self._root.after(self._poll_ms, self._drain_results)
        except Exception:
            self._poll_id = None

    def _drain_results(self) -> None:
        import queue

        self._poll_id = None
        if self._destroyed:
            return
        try:
            while True:
                key, path = self._results.get_nowait()
                self._worker_busy = False
                if path is not None:
                    self._cache[key] = path
                    try:
                        self._on_ready(path)
                    except Exception:
                        pass
                nxt = self._queued_next
                self._queued_next = None
                if nxt is not None:
                    self._launch(nxt)
        except queue.Empty:
            pass
        self._schedule_poll()

    def cancel(self) -> None:
        self._destroyed = True
        for attr in ("_after_id", "_poll_id"):
            after_id = getattr(self, attr, None)
            if after_id is not None:
                try:
                    self._root.after_cancel(after_id)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._pending = None
        self._queued_next = None

    def clear_cache(self) -> None:
        self._cache.clear()

    def request(self, timestamp: float, width: int, height: int) -> Path | None:
        """Ask for a frame. Returns the cached Path immediately when known,
        otherwise schedules an asynchronous extraction and returns None."""
        if self._destroyed:
            return None
        key = (int(round(float(timestamp) * 1000)), int(width), int(height))
        cached = self._cache.get(key)
        if cached is not None and cached.exists():
            return cached
        self._pending = (key, float(timestamp), int(width), int(height))
        if self._after_id is not None:
            try:
                self._root.after_cancel(self._after_id)
            except Exception:
                pass
        self._after_id = self._root.after(self._debounce_ms, self._fire)
        return None

    def _fire(self) -> None:
        self._after_id = None
        if self._destroyed or self._pending is None:
            return
        if self._worker_busy:
            # Worker is mid-flight; remember this as the next-up request.
            self._queued_next = self._pending
            self._pending = None
            return
        request = self._pending
        self._pending = None
        self._launch(request)

    def _launch(self, request: tuple[tuple[int, int, int], float, int, int]) -> None:
        self._worker_busy = True
        key, timestamp, width, height = request

        def work() -> None:
            try:
                path = self._extract(timestamp, width, height)
            except Exception:
                path = None
            try:
                self._results.put((key, path))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()


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
        if result.stdout:
            log_debug("Qt GUI stdout: " + result.stdout.rstrip())
        if result.stderr:
            if result.returncode == 0:
                log_debug("Qt GUI stderr: " + result.stderr.rstrip())
            else:
                log_error("Qt GUI stderr: " + result.stderr.rstrip())
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
    reply = _launch_qt_gui(request)
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
    reply = _launch_qt_gui(request)
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
    reply = _launch_qt_gui(request)
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
        }
        join_segments.append(first_segment)
        for item in answers.get("join_input_items") or []:
            join_segments.append(
                {
                    "path": str(item.get("path")),
                    "name": Path(item.get("path")).name,
                    "duration": float(item.get("duration") or 0.0),
                    "chapters": (item.get("probe") or {}).get("chapters") or [],
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
        "has_audio": bool(answers.get("audio_streams")),
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
            answers.get("_unified_include_audio", bool(answers.get("audio_streams")))
        ),
    }
    if join_segments:
        log_info(
            "Opening Unified Video Editor with joined inputs: "
            + ", ".join(f"{idx + 1}:{Path(segment.get('path') or '').name}" for idx, segment in enumerate(join_segments))
        )
    reply = _launch_qt_gui(request)
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
    reply = _launch_qt_gui(request)
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
    'choose_crop_graphically',
    'open_audio_transform_gui',
    'open_cut_gui',
    'open_unified_video_gui',
    'open_video_speed_gui',
    '_apply_app_ttk_theme',
    '_bind_layout_independent_keys',
    '_launch_qt_gui',
    'WIN_VK_BY_NAME',
    '_PreviewScheduler',
    '_UIPalette',
]


# The large legacy-Tk fallback editors were split into sibling modules for file
# size (guibridge_crop_tk, guibridge_cut_tk). Re-export them so consumers of
# `from ffmwiz.guibridge import *` and guibridge's own runtime calls keep seeing
# them. Each sibling imports guibridge at its top; these imports run after
# guibridge's own defs/__all__, so the cycle resolves cleanly.
from ffmwiz import guibridge_crop_tk as _guibridge_crop_tk  # noqa: E402
from ffmwiz.guibridge_crop_tk import *  # noqa: E402,F401,F403
from ffmwiz import guibridge_cut_tk as _guibridge_cut_tk  # noqa: E402
from ffmwiz.guibridge_cut_tk import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_guibridge_crop_tk.__all__) + list(_guibridge_cut_tk.__all__)
