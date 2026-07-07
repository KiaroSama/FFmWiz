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
    """Launch assets/runtime/ffmwiz_gui.py as a subprocess, hand it the request via a
    temp JSON file, and return the parsed reply dict.

    Returns None only when the dedicated GUI is unavailable before launch
    (missing PySide6, missing assets/runtime/ffmwiz_gui.py, etc.). Once the Qt GUI starts,
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
        qml_file = script_dir() / "assets" / FFMWIZ_RUNTIME_DIR_NAME / "qml" / "UnifiedEditor.qml"
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


def _choose_crop_graphically_tk(answers: dict[str, Any]) -> tuple[int, int, int, int] | None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as exc:
        appio.error(f"Tkinter is not available, so graphical crop editor cannot be opened: {exc}")
        return None

    try:
        source_width, source_height = first_video_size(answers)
        frame_width, frame_height = preview_size(source_width, source_height)
        duration = services.stream_duration_seconds(answers.get("video_streams", [{}])[0], answers.get("format"))
        if duration is None:
            duration = services.stream_duration_seconds({}, answers.get("format"))
        timeline_duration = max(1.0, duration or 60.0)
        initial_timestamp = min(30.0, max(0.0, timeline_duration * 0.25))
        ffplay = shutil.which("ffplay")
        with tempfile.TemporaryDirectory(prefix="ffmwizard_crop_") as temp_name:
            temp_dir = Path(temp_name)

            result: dict[str, tuple[int, int, int, int] | None] = {"margins": None}
            palette = _UIPalette
            root = tk.Tk()
            root.title("FFmWiz Crop Editor")
            root.configure(bg=palette.BG)
            _apply_tk_window_icon(root)
            _apply_app_ttk_theme(root)
            try:
                style = ttk.Style(root)
                # Combobox styling specific to this GUI (zoom dropdown).
                style.configure(
                    "TCombobox",
                    fieldbackground=palette.SURFACE,
                    background=palette.SURFACE,
                    foreground=palette.TEXT,
                    arrowcolor=palette.ACCENT_YELLOW,
                )
                style.map(
                    "TCombobox",
                    fieldbackground=[("readonly", palette.SURFACE)],
                    foreground=[("readonly", palette.TEXT)],
                )
            except Exception:
                pass
            _apply_dark_title_bar(root)
            load_icon_image = _make_icon_loader(root)

            pad = 32
            image_x = pad
            image_y = pad
            min_size = 24
            handle_radius = 8
            handle_hit_radius = 26
            edge_hit_radius = 14
            min_zoom = 25
            max_zoom = 1000

            state: dict[str, Any] = {
                "left": 0,
                "top": 0,
                "right": 0,
                "bottom": 0,
                "drag": "",
                "pan": False,
                "timestamp": initial_timestamp,
                "zoom_percent": 100,
                "photo": None,
                "photo_key": None,
                "playing": False,
                "audio_proc": None,
                "volume": 50,
                "mute": False,
                # Active tool: "hand" pans the image, "zoom" zooms on click/drag.
                # Alt held while dragging in zoom mode inverts the zoom direction.
                "tool": "hand",
                "zoom_drag_y": None,
            }

            viewport_width = min(frame_width + pad * 2, 1280)
            viewport_height = min(frame_height + pad * 2, 760)
            root.minsize(min(viewport_width + 70, 1350), min(viewport_height + 150, 930))

            shell = ttk.Frame(root, padding=(14, 14, 14, 12))
            shell.pack(fill="both", expand=True)
            shell.columnconfigure(0, weight=1)
            shell.rowconfigure(0, weight=1)
            canvas = tk.Canvas(
                shell,
                width=viewport_width,
                height=viewport_height,
                bg="#11151d",
                highlightthickness=0,
            )
            canvas.grid(row=0, column=0, sticky="nsew")
            y_scroll = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview, style="Dark.Vertical.TScrollbar")
            x_scroll = ttk.Scrollbar(shell, orient="horizontal", command=canvas.xview, style="Dark.Horizontal.TScrollbar")
            y_scroll.grid(row=0, column=1, sticky="ns")
            x_scroll.grid(row=1, column=0, sticky="ew")
            canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)

            info = ttk.Label(shell, text="", anchor="w")
            info.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 8))
            controls = ttk.Frame(shell)
            controls.grid(row=3, column=0, columnspan=2, sticky="ew")
            media_controls = ttk.Frame(shell)
            media_controls.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
            zoom_var = tk.StringVar(value="100")
            time_var = tk.DoubleVar(value=initial_timestamp)
            time_label = ttk.Label(media_controls, text="")
            play_button: Any | None = None
            volume_var = tk.IntVar(value=50)
            mute_var = tk.BooleanVar(value=False)
            frame_cache: dict[tuple[int, int, int], Path] = {}
            icon_cache: dict[str, Any] = {}
            zoom_presets = ["25", "33", "50", "67", "75", "90", "100", "110", "125", "150", "175", "200", "250", "300", "400", "500", "600", "800", "1000"]
            speaker_button: Any | None = None
            volume_slider: Any | None = None

            def load_icon(name: str) -> Any | None:
                if name in icon_cache:
                    return icon_cache[name]
                path = asset_path(ICON_DIR_NAME, f"{name}.png")
                if not path.exists():
                    icon_cache[name] = None
                    return None
                try:
                    icon_cache[name] = tk.PhotoImage(file=str(path))
                except tk.TclError:
                    icon_cache[name] = None
                return icon_cache[name]

            def draw_round_rect(target: Any, x1: int, y1: int, x2: int, y2: int, radius: int, fill: str, outline: str) -> None:
                radius = min(radius, max(1, (x2 - x1) // 2), max(1, (y2 - y1) // 2))
                centers = [
                    (x2 - radius, y1 + radius, -90, 0),
                    (x2 - radius, y2 - radius, 0, 90),
                    (x1 + radius, y2 - radius, 90, 180),
                    (x1 + radius, y1 + radius, 180, 270),
                ]
                points: list[float] = []
                for cx, cy, start, end in centers:
                    for angle in range(start, end + 1, 15):
                        radians = math.radians(angle)
                        points.extend([cx + math.cos(radians) * radius, cy + math.sin(radians) * radius])
                target.create_polygon(points, fill=fill, outline=outline, width=1, smooth=True)

            def make_round_button(parent: Any, text: str, command: Callable[[], None], width: int = 92, height: int = 32) -> Any:
                button = tk.Canvas(parent, width=width, height=height, bg="#0b0f17", highlightthickness=0, bd=0, relief="flat")
                label = {"text": text}

                def draw(active: bool = False) -> None:
                    button.delete("all")
                    draw_round_rect(button, 2, 2, width - 3, height - 3, 12, "#263550" if active else "#1b2433", "#5b6f91")
                    button.create_text(width // 2, height // 2, text=label["text"], fill="#f5f7fb", font=("Segoe UI", 9))

                def set_text(new_text: str) -> None:
                    label["text"] = new_text
                    draw(False)

                def on_press(_event: Any) -> None:
                    draw(True)

                def on_release(_event: Any) -> None:
                    draw(False)
                    command()

                button.bind("<ButtonPress-1>", on_press)
                button.bind("<ButtonRelease-1>", on_release)
                button.bind("<Enter>", lambda _event: button.configure(cursor="hand2"))
                button.bind("<Leave>", lambda _event: draw(False))
                button.set_text = set_text  # type: ignore[attr-defined]
                draw(False)
                return button

            def make_icon_button(parent: Any, kind: str, command: Callable[[], None], width: int = 46, height: int = 36) -> Any:
                button = tk.Canvas(parent, width=width, height=height, bg="#0b0f17", highlightthickness=0, bd=0, relief="flat")
                button.pack_propagate(False)

                def draw_icon(active: bool = False) -> None:
                    button.delete("all")
                    bg = "#263550" if active else "#1b2433"
                    fg = "#f8fbff"
                    accent = "#f5d66a"
                    muted_line = "#46556d"
                    draw_round_rect(button, 2, 2, width - 3, height - 3, 12, bg, "#5b6f91")
                    icon_name = kind
                    if kind == "speaker":
                        volume = int(volume_var.get())
                        level = 0 if mute_var.get() or volume <= 0 else 1 if volume < 34 else 2 if volume < 67 else 3
                        icon_name = f"volume_{level}"
                    icon = load_icon(icon_name)
                    if icon is not None:
                        button.create_image(width // 2, height // 2, image=icon)
                    elif kind in {"zoom_in", "zoom_out"}:
                        button.create_oval(9, 6, 23, 20, outline=accent, width=2)
                        button.create_line(21, 19, 30, 26, fill=accent, width=2)
                        button.create_line(13, 13, 19, 13, fill=fg, width=2)
                        if kind == "zoom_in":
                            button.create_line(16, 10, 16, 16, fill=fg, width=2)
                    elif kind == "speaker":
                        volume = int(volume_var.get())
                        level = 0 if mute_var.get() or volume <= 0 else 1 if volume <= 33 else 2 if volume <= 66 else 3
                        button.create_polygon(7, 13, 13, 13, 20, 7, 20, 23, 13, 17, 7, 17, fill=accent, outline="")
                        if mute_var.get():
                            button.create_line(25, 10, 33, 20, fill="#ff6f6f", width=2)
                            button.create_line(33, 10, 25, 20, fill="#ff6f6f", width=2)
                        else:
                            button.create_arc(21, 11, 28, 19, start=-35, extent=70, style="arc", outline=fg if level >= 1 else muted_line, width=2)
                            button.create_arc(19, 8, 33, 22, start=-35, extent=70, style="arc", outline=fg if level >= 2 else muted_line, width=2)
                            button.create_arc(17, 5, 38, 25, start=-35, extent=70, style="arc", outline=fg if level >= 3 else muted_line, width=2)

                def on_press(_event: Any) -> None:
                    draw_icon(True)

                def on_release(_event: Any) -> None:
                    draw_icon(False)
                    command()

                button.bind("<ButtonPress-1>", on_press)
                button.bind("<ButtonRelease-1>", on_release)
                button.bind("<Enter>", lambda _event: button.configure(cursor="hand2"))
                button.bind("<Leave>", lambda _event: draw_icon(False))
                draw_icon(False)
                button.redraw_icon = draw_icon  # type: ignore[attr-defined]
                return button

            def clamp(value: float, low: float, high: float) -> float:
                return max(low, min(high, value))

            def display_width() -> int:
                return max(1, round(frame_width * int(state["zoom_percent"]) / 100))

            def display_height() -> int:
                return max(1, round(frame_height * int(state["zoom_percent"]) / 100))

            def image_bounds() -> tuple[int, int, int, int]:
                return image_x, image_y, image_x + display_width(), image_y + display_height()

            def format_time(seconds: float) -> str:
                total = max(0, int(round(seconds)))
                minutes, secs = divmod(total, 60)
                hours, minutes = divmod(minutes, 60)
                if hours:
                    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
                return f"{minutes:02d}:{secs:02d}"

            # --- Async preview scheduler -------------------------------------
            # Background-extract frames on a worker thread with debouncing and
            # caching. The cache is keyed by (timestamp_ms, width, height) so
            # zoom changes invalidate naturally.

            def _crop_extract(timestamp: float, w: int, h: int) -> Path:
                return extract_crop_preview_frame(answers, temp_dir, w, h, timestamp)

            def _crop_on_ready(path: Path) -> None:
                try:
                    state["photo"] = tk.PhotoImage(file=str(path))
                except Exception:
                    return
                state["photo_key"] = (
                    int(round(float(state["timestamp"]) * 1000)),
                    display_width(),
                    display_height(),
                )
                redraw(force_image_request=False)

            crop_scheduler = _PreviewScheduler(
                root,
                extract_fn=_crop_extract,
                on_ready=_crop_on_ready,
                debounce_ms=70,
            )

            def render_photo(force: bool = True) -> None:
                """Request the current frame from the scheduler.

                If a cached path is available, set state['photo'] immediately
                so the next canvas draw shows the correct frame. Otherwise the
                scheduler will deliver it asynchronously and trigger a redraw.
                """
                key = (
                    int(round(float(state["timestamp"]) * 1000)),
                    display_width(),
                    display_height(),
                )
                if state.get("photo_key") == key and state.get("photo") is not None:
                    return
                cached = crop_scheduler.request(float(state["timestamp"]), key[1], key[2])
                if cached is not None:
                    try:
                        state["photo"] = tk.PhotoImage(file=str(cached))
                        state["photo_key"] = key
                    except Exception:
                        state["photo_key"] = None
                # Else: keep showing the previous frame; the worker will
                # deliver the new one and trigger a redraw via _crop_on_ready.

            def min_source_width() -> int:
                return max(1, round(source_width * min_size / display_width()))

            def min_source_height() -> int:
                return max(1, round(source_height * min_size / display_height()))

            def clamp_margins() -> None:
                state["left"] = int(clamp(state["left"], 0, max(0, source_width - state["right"] - min_source_width())))
                state["right"] = int(clamp(state["right"], 0, max(0, source_width - state["left"] - min_source_width())))
                state["top"] = int(clamp(state["top"], 0, max(0, source_height - state["bottom"] - min_source_height())))
                state["bottom"] = int(clamp(state["bottom"], 0, max(0, source_height - state["top"] - min_source_height())))

            def current_margins() -> tuple[int, int, int, int]:
                clamp_margins()
                return int(state["top"]), int(state["left"]), int(state["right"]), int(state["bottom"])

            def crop_rect() -> tuple[int, int, int, int]:
                _, _, image_right, image_bottom = image_bounds()
                left = image_x + round(state["left"] * display_width() / source_width)
                top = image_y + round(state["top"] * display_height() / source_height)
                right = image_right - round(state["right"] * display_width() / source_width)
                bottom = image_bottom - round(state["bottom"] * display_height() / source_height)
                return left, top, right, bottom

            def handle_points() -> dict[str, tuple[int, int]]:
                left, top, right, bottom = crop_rect()
                mid_x = round((left + right) / 2)
                mid_y = round((top + bottom) / 2)
                return {
                    "nw": (left, top),
                    "n": (mid_x, top),
                    "ne": (right, top),
                    "e": (right, mid_y),
                    "se": (right, bottom),
                    "s": (mid_x, bottom),
                    "sw": (left, bottom),
                    "w": (left, mid_y),
                }

            def redraw(force_image_request: bool = True) -> None:
                clamp_margins()
                if force_image_request:
                    try:
                        render_photo()
                    except Exception as exc:
                        appio.error(f"Could not refresh crop preview frame: {exc}")
                canvas.delete("all")

                image_left, image_top, image_right, image_bottom = image_bounds()
                if state.get("photo") is not None:
                    canvas.create_image(image_left, image_top, image=state["photo"], anchor="nw")
                else:
                    # Show a placeholder until the worker delivers the first frame.
                    canvas.create_rectangle(
                        image_left, image_top, image_right, image_bottom,
                        fill="#11151d", outline="#2e3a4f", width=1,
                    )
                    canvas.create_text(
                        (image_left + image_right) // 2,
                        (image_top + image_bottom) // 2,
                        text="(loading preview frame...)",
                        fill="#7c8aa6",
                    )
                left, top, right, bottom = crop_rect()
                canvas.create_rectangle(image_left, image_top, image_right, top, fill="#000000", stipple="gray50", outline="")
                canvas.create_rectangle(image_left, bottom, image_right, image_bottom, fill="#000000", stipple="gray50", outline="")
                canvas.create_rectangle(image_left, top, left, bottom, fill="#000000", stipple="gray50", outline="")
                canvas.create_rectangle(right, top, image_right, bottom, fill="#000000", stipple="gray50", outline="")
                canvas.create_rectangle(left, top, right, bottom, outline="#ffcc33", width=2)

                third_x = (right - left) / 3
                third_y = (bottom - top) / 3
                for pos in (left + third_x, left + third_x * 2):
                    canvas.create_line(pos, top, pos, bottom, fill="#ffcc33", dash=(4, 5), width=1)
                for pos in (top + third_y, top + third_y * 2):
                    canvas.create_line(left, pos, right, pos, fill="#ffcc33", dash=(4, 5), width=1)

                for name, (x_pos, y_pos) in handle_points().items():
                    fill = "#f8fbff" if len(name) == 1 else "#ffcc33"
                    canvas.create_rectangle(
                        x_pos - handle_radius,
                        y_pos - handle_radius,
                        x_pos + handle_radius,
                        y_pos + handle_radius,
                        fill=fill,
                        outline="#11151d",
                        width=1,
                    )
                top_m, left_m, right_m, bottom_m = current_margins()
                crop_width = source_width - left_m - right_m
                crop_height = source_height - top_m - bottom_m
                info.configure(
                    text=(
                        f"Crop margins: top={top_m}, left={left_m}, right={right_m}, bottom={bottom_m} "
                        f"| output crop box: {crop_width}x{crop_height} | time {format_time(float(state['timestamp']))}"
                    )
                )
                canvas.configure(scrollregion=(0, 0, image_right + pad, image_bottom + pad))
                zoom_var.set(str(int(state["zoom_percent"])))
                time_var.set(float(state["timestamp"]))
                time_label.configure(text=f"{format_time(float(state['timestamp']))} / {format_time(timeline_duration)}")

            def hit_handle(x_pos: float, y_pos: float) -> str:
                left, top, right, bottom = crop_rect()
                in_x = left - edge_hit_radius <= x_pos <= right + edge_hit_radius
                in_y = top - edge_hit_radius <= y_pos <= bottom + edge_hit_radius

                corner_zones = {
                    "nw": (left, top),
                    "ne": (right, top),
                    "se": (right, bottom),
                    "sw": (left, bottom),
                }
                for name, (corner_x, corner_y) in corner_zones.items():
                    if abs(x_pos - corner_x) <= handle_hit_radius and abs(y_pos - corner_y) <= handle_hit_radius:
                        return name

                for name, (handle_x, handle_y) in handle_points().items():
                    if abs(x_pos - handle_x) <= handle_hit_radius and abs(y_pos - handle_y) <= handle_hit_radius:
                        return name

                if in_x and abs(y_pos - top) <= edge_hit_radius:
                    return "n"
                if in_x and abs(y_pos - bottom) <= edge_hit_radius:
                    return "s"
                if in_y and abs(x_pos - left) <= edge_hit_radius:
                    return "w"
                if in_y and abs(x_pos - right) <= edge_hit_radius:
                    return "e"
                return ""

            def cursor_for_handle(handle: str) -> str:
                if handle in {"e", "w"}:
                    return "sb_h_double_arrow"
                if handle in {"n", "s"}:
                    return "sb_v_double_arrow"
                if handle in {"nw", "se"}:
                    return "size_nw_se"
                if handle in {"ne", "sw"}:
                    return "size_ne_sw"
                return ""

            def point_in_image(x_pos: float, y_pos: float) -> bool:
                image_left, image_top, image_right, image_bottom = image_bounds()
                return image_left <= x_pos <= image_right and image_top <= y_pos <= image_bottom

            def set_canvas_cursor(cursor: str) -> None:
                try:
                    if cursor == "open_hand":
                        cursor_file = asset_path(CURSOR_DIR_NAME, "open_hand.xbm")
                        if cursor_file.exists():
                            canvas.configure(cursor=f"@{cursor_file}")
                            return
                        canvas.configure(cursor="hand1")
                        return
                    canvas.configure(cursor=cursor)
                except tk.TclError:
                    fallback = "fleur" if cursor == "open_hand" else "crosshair" if cursor else ""
                    canvas.configure(cursor=fallback)

            def _alt_held(event: Any) -> bool:
                """Return True if any Alt modifier is held in the event.state mask."""
                if event is None:
                    return False
                mask = getattr(event, "state", 0) or 0
                # Windows: Alt = 0x20000. Linux/X11: Mod1 = 0x0008.
                return bool(mask & 0x20000) or bool(mask & 0x0008)

            def update_cursor(event: Any) -> None:
                if state["drag"]:
                    return
                x_pos = canvas.canvasx(event.x)
                y_pos = canvas.canvasy(event.y)
                handle = hit_handle(x_pos, y_pos)
                if handle:
                    set_canvas_cursor(cursor_for_handle(handle))
                elif point_in_image(x_pos, y_pos):
                    if state["tool"] == "zoom":
                        # Use the universally-supported "crosshair" cursor
                        # for Zoom Tool. The Tk "icon" cursor used previously
                        # appeared as a black square on some Windows builds.
                        set_canvas_cursor("crosshair")
                    else:
                        set_canvas_cursor("open_hand")
                else:
                    set_canvas_cursor("")

            def apply_zoom_centered_on(x_pos: float, y_pos: float, factor: float) -> None:
                """Zoom the preview by 'factor' (>1 zoom in, <1 zoom out)
                keeping the canvas point (x_pos, y_pos) at the same screen
                location after the zoom."""
                if factor <= 0 or abs(factor - 1.0) < 1e-6:
                    return
                old_width = display_width()
                old_height = display_height()
                if old_width <= 0 or old_height <= 0:
                    return
                # Image-space coordinates of the focused canvas point.
                rel_x = (canvas.canvasx(x_pos) - image_x) / max(1, old_width)
                rel_y = (canvas.canvasy(y_pos) - image_y) / max(1, old_height)
                rel_x = max(0.0, min(1.0, rel_x))
                rel_y = max(0.0, min(1.0, rel_y))

                new_zoom = int(round(int(state["zoom_percent"]) * factor))
                new_zoom = int(clamp(new_zoom, min_zoom, max_zoom))
                if new_zoom == int(state["zoom_percent"]):
                    return
                state["zoom_percent"] = new_zoom
                state["photo_key"] = None  # force re-render at the new size
                redraw()
                # After redraw, re-center scroll so the focused image-relative
                # point lands under the original mouse position.
                root.update_idletasks()
                new_width = display_width()
                new_height = display_height()
                target_canvas_x = image_x + rel_x * new_width
                target_canvas_y = image_y + rel_y * new_height
                desired_x = target_canvas_x - x_pos
                desired_y = target_canvas_y - y_pos
                scroll_w = max(1, new_width + pad * 2)
                scroll_h = max(1, new_height + pad * 2)
                canvas.xview_moveto(max(0.0, min(1.0, desired_x / scroll_w)))
                canvas.yview_moveto(max(0.0, min(1.0, desired_y / scroll_h)))

            def begin_drag(event: Any) -> None:
                x_pos = canvas.canvasx(event.x)
                y_pos = canvas.canvasy(event.y)
                state["drag"] = hit_handle(x_pos, y_pos)
                if state["drag"]:
                    set_canvas_cursor(cursor_for_handle(state["drag"]))
                    canvas.focus_set()
                    return

                if point_in_image(x_pos, y_pos):
                    if state["tool"] == "zoom":
                        # Photoshop-style: clicking zooms in (or out with Alt).
                        # Hold + drag tracks vertical motion for finer control.
                        state["zoom_drag_y"] = event.y
                        state["drag"] = "_zoom"
                        # Single-click zoom step:
                        factor = 1.0 / 1.25 if _alt_held(event) else 1.25
                        apply_zoom_centered_on(event.x, event.y, factor)
                        update_cursor(event)
                    else:
                        # Hand tool: pan.
                        state["pan"] = True
                        canvas.scan_mark(event.x, event.y)
                        set_canvas_cursor("open_hand")
                canvas.focus_set()

            def drag(event: Any) -> None:
                if state.get("drag") == "_zoom" and state.get("zoom_drag_y") is not None:
                    dy = event.y - int(state["zoom_drag_y"])
                    if abs(dy) >= 6:
                        # Up = zoom in, down = zoom out. Alt inverts.
                        zoom_in_dir = dy < 0
                        if _alt_held(event):
                            zoom_in_dir = not zoom_in_dir
                        factor = 1.07 if zoom_in_dir else (1.0 / 1.07)
                        apply_zoom_centered_on(event.x, event.y, factor)
                        state["zoom_drag_y"] = event.y
                    return
                if state["pan"]:
                    canvas.scan_dragto(event.x, event.y, gain=1)
                    return
                mode = state["drag"]
                if not mode:
                    return
                image_left, image_top, image_right, image_bottom = image_bounds()
                left, top, right, bottom = crop_rect()
                x_pos = clamp(canvas.canvasx(event.x), image_left, image_right)
                y_pos = clamp(canvas.canvasy(event.y), image_top, image_bottom)
                if "w" in mode:
                    new_left = clamp(x_pos, image_left, right - min_size)
                    state["left"] = round((new_left - image_left) * source_width / display_width())
                if "e" in mode:
                    new_right = clamp(x_pos, left + min_size, image_right)
                    state["right"] = round((image_right - new_right) * source_width / display_width())
                if "n" in mode:
                    new_top = clamp(y_pos, image_top, bottom - min_size)
                    state["top"] = round((new_top - image_top) * source_height / display_height())
                if "s" in mode:
                    new_bottom = clamp(y_pos, top + min_size, image_bottom)
                    state["bottom"] = round((image_bottom - new_bottom) * source_height / display_height())
                redraw(force_image_request=False)

            def end_drag(_event: Any) -> None:
                state["drag"] = ""
                state["pan"] = False
                state["zoom_drag_y"] = None

            def set_tool_hand(_event: Any = None) -> None:
                state["tool"] = "hand"
                refresh_tool_label()

            def set_tool_zoom(_event: Any = None) -> None:
                state["tool"] = "zoom"
                refresh_tool_label()

            def refresh_tool_label() -> None:
                # The two tool buttons exist as attributes set later; update
                # their visible labels so the active tool is obvious.
                try:
                    hand_button.configure(
                        text=("Hand Tool* (H)" if state["tool"] == "hand" else "Hand Tool (H)")
                    )
                    zoom_button.configure(
                        text=("Zoom Tool* (Z)" if state["tool"] == "zoom" else "Zoom Tool (Z)")
                    )
                except Exception:
                    pass

            def reset_crop() -> None:
                state["left"] = 0
                state["top"] = 0
                state["right"] = 0
                state["bottom"] = 0
                redraw()

            def set_zoom_percent(value: int | str) -> None:
                try:
                    number = int(str(value).strip().rstrip("%"))
                except ValueError:
                    appio.error("Zoom must be an integer percent, for example 150.")
                    return
                state["zoom_percent"] = int(clamp(number, min_zoom, max_zoom))
                state["photo_key"] = None
                redraw()

            def zoom_in() -> None:
                set_zoom_percent(int(state["zoom_percent"]) + 25)

            def zoom_out() -> None:
                set_zoom_percent(int(state["zoom_percent"]) - 25)

            def reset_zoom() -> None:
                set_zoom_percent(100)

            def set_time(value: float, restart_audio: bool = True) -> None:
                state["timestamp"] = clamp(float(value), 0.0, timeline_duration)
                state["photo_key"] = None
                redraw()
                if restart_audio and state["playing"]:
                    start_audio()

            def step_time(delta: float) -> None:
                set_time(float(state["timestamp"]) + delta)

            def stop_audio() -> None:
                process = state.get("audio_proc")
                state["audio_proc"] = None
                if process and process.poll() is None:
                    try:
                        process.terminate()
                        process.wait(timeout=0.8)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass

            def start_audio() -> None:
                stop_audio()
                if not ffplay or state["mute"] or int(state["volume"]) <= 0:
                    return
                args = [
                    ffplay,
                    "-nodisp",
                    "-autoexit",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{float(state['timestamp']):.3f}",
                    "-volume",
                    str(int(state["volume"])),
                    str(answers["input_path"]),
                ]
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                try:
                    state["audio_proc"] = subprocess.Popen(
                        args,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creation_flags,
                    )
                except Exception as exc:
                    appio.error(f"Could not start ffplay audio preview: {exc}")

            def playback_tick() -> None:
                if not state["playing"]:
                    return
                next_time = float(state["timestamp"]) + 1.0
                if next_time >= timeline_duration:
                    state["timestamp"] = timeline_duration
                    state["playing"] = False
                    if play_button is not None:
                        play_button.set_text("▶ Play (Space)")  # type: ignore[attr-defined]
                    stop_audio()
                    redraw()
                    return
                state["timestamp"] = next_time
                state["photo_key"] = None
                redraw()
                root.after(1000, playback_tick)

            def toggle_playback() -> None:
                # Bug fix: if playback reached the end and the user presses
                # Play again, rewind to the start so playback continues
                # normally instead of ending after ~1 second.
                if not state["playing"] and float(state["timestamp"]) >= timeline_duration - 0.5:
                    state["timestamp"] = 0.0
                    state["photo_key"] = None
                    redraw()
                state["playing"] = not state["playing"]
                if play_button is not None:
                    play_button.set_text("⏸ Pause (Space)" if state["playing"] else "▶ Play (Space)")  # type: ignore[attr-defined]
                if state["playing"]:
                    start_audio()
                    root.after(1000, playback_tick)
                else:
                    stop_audio()

            def update_audio_settings(_event: Any | None = None, restart: bool = False) -> None:
                state["volume"] = int(volume_var.get())
                state["mute"] = bool(mute_var.get())
                if speaker_button is not None:
                    speaker_button.redraw_icon(False)  # type: ignore[attr-defined]
                draw_volume_slider()
                if state["playing"] and restart:
                    start_audio()

            def toggle_mute() -> None:
                mute_var.set(not mute_var.get())
                update_audio_settings(restart=True)
                if speaker_button is not None:
                    speaker_button.redraw_icon(False)  # type: ignore[attr-defined]

            def slider_value_from_click(widget: Any, event: Any, low: float, high: float) -> float:
                width = max(1, widget.winfo_width())
                ratio = clamp(event.x / width, 0.0, 1.0)
                return low + (high - low) * ratio

            def seek_time_from_click(event: Any) -> None:
                set_time(slider_value_from_click(time_slider, event, 0.0, timeline_duration))

            def set_volume_from_click(event: Any) -> None:
                set_volume_from_x(event.x, restart=True)

            def draw_volume_slider(active: bool = False) -> None:
                if volume_slider is None:
                    return
                width = int(volume_slider["width"])
                height = int(volume_slider["height"])
                left = 9
                right = width - 9
                center = height // 2
                volume_slider.delete("all")
                draw_round_rect(volume_slider, left, center - 4, right, center + 4, 4, "#101827", "#3f5576")
                fill_right = left + round((right - left) * int(volume_var.get()) / 100)
                if fill_right > left:
                    draw_round_rect(volume_slider, left, center - 4, fill_right, center + 4, 4, "#f5d66a", "#f5d66a")
                thumb_x = max(left, min(right, fill_right))
                thumb_fill = "#ffffff" if active else "#dfeaff"
                volume_slider.create_oval(thumb_x - 7, center - 7, thumb_x + 7, center + 7, fill=thumb_fill, outline="#6f8dc1", width=2)

            def set_volume_value(value: float, restart: bool = False) -> None:
                volume_var.set(int(clamp(round(value), 0, 100)))
                update_audio_settings(restart=restart)

            def set_volume_from_x(x_pos: float, restart: bool = False) -> None:
                if volume_slider is None:
                    return
                width = int(volume_slider["width"])
                left = 9
                right = width - 9
                ratio = clamp((x_pos - left) / max(1, right - left), 0.0, 1.0)
                set_volume_value(ratio * 100, restart=restart)

            def drag_volume(event: Any) -> str:
                set_volume_from_x(event.x, restart=False)
                return "break"

            def release_volume(event: Any) -> str:
                set_volume_from_x(event.x, restart=True)
                return "break"

            def volume_wheel(event: Any) -> str:
                delta = 3 if event.delta > 0 else -3
                set_volume_value(int(volume_var.get()) + delta, restart=True)
                return "break"

            def mouse_wheel(event: Any) -> str:
                step = -1 if event.delta > 0 else 1
                if event.state & 0x0001:
                    canvas.xview_scroll(step, "units")
                else:
                    canvas.yview_scroll(step, "units")
                return "break"

            def x_scroll_wheel(event: Any) -> str:
                canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"

            def y_scroll_wheel(event: Any) -> str:
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"

            def time_slider_wheel(event: Any) -> str:
                step_time(-5 if event.delta > 0 else 5)
                return "break"

            def ctrl_mouse_wheel(event: Any) -> str:
                if event.delta > 0:
                    zoom_in()
                else:
                    zoom_out()
                return "break"

            def pan_with_key(event: Any) -> str:
                moves = {
                    "Left": (-1, 0),
                    "Right": (1, 0),
                    "Up": (0, -1),
                    "Down": (0, 1),
                }
                dx, dy = moves.get(event.keysym, (0, 0))
                if dx:
                    canvas.xview_scroll(dx, "units")
                if dy:
                    canvas.yview_scroll(dy, "units")
                return "break"

            def apply_crop() -> None:
                result["margins"] = current_margins()
                state["playing"] = False
                stop_audio()
                try:
                    crop_scheduler.cancel()
                except Exception:
                    pass
                root.destroy()

            def cancel_crop() -> None:
                result["margins"] = None
                state["playing"] = False
                stop_audio()
                try:
                    crop_scheduler.cancel()
                except Exception:
                    pass
                root.destroy()

            def is_editing_text(event: Any) -> bool:
                """True if the current keyboard focus is on a text-entry widget."""
                widget = getattr(event, "widget", None) if event is not None else None
                try:
                    cls = str(widget.winfo_class()) if widget is not None else ""
                except Exception:
                    cls = ""
                return cls in ("Entry", "TEntry", "Text", "Spinbox", "TCombobox", "TSpinbox")

            def space_toggle_playback(event: Any) -> str | None:
                if is_editing_text(event):
                    return None
                toggle_playback()
                return "break"

            canvas.bind("<ButtonPress-1>", begin_drag)
            canvas.bind("<B1-Motion>", drag)
            canvas.bind("<ButtonRelease-1>", end_drag)
            canvas.bind("<Motion>", update_cursor)
            canvas.bind("<Leave>", lambda _event: set_canvas_cursor(""))
            canvas.bind("<MouseWheel>", mouse_wheel)
            canvas.bind("<Control-MouseWheel>", ctrl_mouse_wheel)
            x_scroll.bind("<MouseWheel>", x_scroll_wheel)
            y_scroll.bind("<MouseWheel>", y_scroll_wheel)
            root.bind("<Control-plus>", lambda _event: zoom_in())
            root.bind("<Control-equal>", lambda _event: zoom_in())
            root.bind("<Control-minus>", lambda _event: zoom_out())
            for key_name in ("<Left>", "<Right>", "<Up>", "<Down>"):
                root.bind(key_name, pan_with_key)

            # Layout-independent shortcuts so the Crop GUI still responds
            # when the active keyboard language is Persian or another
            # non-Latin layout (these bindings match by Windows VK code).
            _bind_layout_independent_keys(root, [
                {"key": "space", "callback": lambda _e: space_toggle_playback(_e)},
                {"key": "h", "ctrl": False, "alt": False, "callback": set_tool_hand},
                {"key": "z", "ctrl": False, "alt": False, "callback": set_tool_zoom},
                {"key": "r", "ctrl": False, "callback": lambda _e: reset_crop()},
                {"key": "r", "ctrl": True, "callback": lambda _e: reset_crop()},
                {"key": "m", "ctrl": False, "callback": lambda _e: toggle_mute()},
                {"key": "0", "ctrl": True, "callback": lambda _e: reset_zoom()},
                {"key": "plus", "ctrl": True, "callback": lambda _e: zoom_in()},
                {"key": "minus", "ctrl": True, "callback": lambda _e: zoom_out()},
                {"key": "plus", "ctrl": False, "callback": lambda _e: zoom_in()},
                {"key": "minus", "ctrl": False, "callback": lambda _e: zoom_out()},
                {"key": "left", "shift": True, "callback": lambda _e: step_time(-10)},
                {"key": "right", "shift": True, "callback": lambda _e: step_time(10)},
                {"key": "return", "callback": lambda _e: apply_crop()},
                {"key": "escape", "callback": lambda _e: cancel_crop()},
            ], is_text_focus_fn=is_editing_text)

            root.protocol("WM_DELETE_WINDOW", cancel_crop)
            # Tool selector buttons. Hand is the default to preserve the
            # previous panning behavior; Zoom Tool is opt-in via Z.
            hand_button = make_round_button(controls, "Hand Tool* (H)", set_tool_hand, width=120)
            hand_button.pack(side="left")
            zoom_button = make_round_button(controls, "Zoom Tool (Z)", set_tool_zoom, width=120)
            zoom_button.pack(side="left", padx=(6, 12))
            make_icon_button(controls, "zoom_out", zoom_out).pack(side="left")
            ttk.Label(controls, text="Zoom %").pack(side="left", padx=(8, 4))
            zoom_box = tk.Frame(
                controls,
                bg="#101827",
                highlightthickness=1,
                highlightbackground="#3a4b65",
                highlightcolor="#f5d66a",
            )
            zoom_box.pack(side="left")
            zoom_entry = tk.Entry(
                zoom_box,
                textvariable=zoom_var,
                width=5,
                bg="#101827",
                fg="#f5f7fb",
                insertbackground="#f5d66a",
                relief="flat",
                highlightthickness=0,
                justify="center",
            )
            zoom_entry.pack(side="left", ipady=5)
            zoom_entry.bind("<Return>", lambda _event: set_zoom_percent(zoom_var.get()))
            zoom_arrow = tk.Canvas(zoom_box, width=22, height=28, bg="#101827", highlightthickness=0, bd=0, relief="flat")
            zoom_arrow.pack(side="left")
            zoom_arrow.create_polygon(7, 10, 15, 10, 11, 16, fill="#f5d66a", outline="")
            zoom_menu = tk.Menu(
                root,
                tearoff=False,
                bg="#101827",
                fg="#f5f7fb",
                activebackground="#263550",
                activeforeground="#ffffff",
                bd=1,
                relief="solid",
            )
            for preset in zoom_presets:
                zoom_menu.add_command(label=f"{preset}%", command=lambda value=preset: set_zoom_percent(value))

            def show_zoom_menu() -> None:
                zoom_menu.tk_popup(zoom_box.winfo_rootx(), zoom_box.winfo_rooty() + zoom_box.winfo_height())

            zoom_arrow.bind("<Button-1>", lambda _event: show_zoom_menu())
            zoom_arrow.bind("<Enter>", lambda _event: zoom_arrow.configure(cursor="hand2"))
            make_icon_button(controls, "zoom_in", zoom_in).pack(side="left", padx=(12, 0))
            make_round_button(controls, "Reset Zoom (Ctrl+0)", reset_zoom, width=146).pack(side="left", padx=(8, 0))
            make_round_button(controls, "Reset Crop (Ctrl+R)", reset_crop, width=154).pack(side="left", padx=(8, 0))
            make_round_button(controls, "Cancel (Esc)", cancel_crop, width=104).pack(side="right", padx=(8, 0))
            make_round_button(controls, "Apply (Enter)", apply_crop, width=110).pack(side="right")

            play_button = make_round_button(media_controls, "▶ Play (Space)", toggle_playback, width=120)
            play_button.pack(side="left")
            make_round_button(media_controls, "-10s (Shift+←)", lambda: step_time(-10), width=110).pack(side="left", padx=(8, 0))
            time_slider = ttk.Scale(
                media_controls,
                from_=0.0,
                to=timeline_duration,
                orient="horizontal",
                variable=time_var,
            )
            time_slider.pack(side="left", fill="x", expand=True, padx=8)
            time_slider.bind("<Button-1>", seek_time_from_click)
            time_slider.bind("<ButtonRelease-1>", lambda _event: set_time(time_var.get()))
            time_slider.bind("<MouseWheel>", time_slider_wheel)
            make_round_button(media_controls, "+10s (Shift+→)", lambda: step_time(10), width=110).pack(side="left")
            time_label.pack(side="left", padx=(8, 16))
            speaker_button = make_icon_button(media_controls, "speaker", toggle_mute, width=48, height=36)
            speaker_button.pack(side="left")
            # Home / End jump to start / end of the audio-preview timeline.
            ttk.Button(media_controls, text="⏮", width=3,
                       command=lambda: set_time(0.0)).pack(side="left", padx=(8, 0))
            ttk.Button(media_controls, text="⏭", width=3,
                       command=lambda: set_time(timeline_duration)).pack(side="left", padx=(4, 0))
            _bind_layout_independent_keys(root, [
                {"key": "home", "callback": lambda _e: set_time(0.0)},
                {"key": "end", "callback": lambda _e: set_time(timeline_duration)},
            ], is_text_focus_fn=is_editing_text)
            volume_slider = tk.Canvas(
                media_controls,
                width=142,
                height=30,
                bg="#0b0f17",
                highlightthickness=0,
                bd=0,
                relief="flat",
            )
            volume_slider.pack(side="left", padx=(8, 0))
            volume_slider.bind("<Button-1>", set_volume_from_click)
            volume_slider.bind("<B1-Motion>", drag_volume)
            volume_slider.bind("<ButtonRelease-1>", release_volume)
            volume_slider.bind("<MouseWheel>", volume_wheel)
            volume_slider.bind("<Enter>", lambda _event: volume_slider.configure(cursor="hand2") if volume_slider is not None else None)
            if not ffplay:
                speaker_button.unbind("<ButtonPress-1>")
                speaker_button.unbind("<ButtonRelease-1>")
                if volume_slider is not None:
                    volume_slider.unbind("<Button-1>")
                    volume_slider.unbind("<B1-Motion>")
                    volume_slider.unbind("<ButtonRelease-1>")
                    volume_slider.unbind("<MouseWheel>")
                ttk.Label(media_controls, text="Audio preview needs ffplay").pack(side="left", padx=(8, 0))
            draw_volume_slider()
            redraw()
            canvas.focus_set()
            root.mainloop()
            return result["margins"]
    except subprocess.CalledProcessError as exc:
        appio.error(f"FFmpeg could not create a crop preview frame: {exc}")
    except Exception as exc:
        appio.error(f"Graphical crop preview failed: {exc}")
    return None


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


def _open_legacy_cut_gui_tk(
    answers: dict[str, Any],
    fps: float,
    duration: float,
) -> list[tuple[float, float]] | None:
    """Premiere-inspired Cut Editor (legacy Tk fallback).

    Layout (top -> bottom):
        - Toolbar / header band with title and clip stats.
        - Large preview viewport.
        - "Now / In / Out / Kept / Cuts / Zoom" status strip.
        - Wide timeline with high-contrast ticks, markers, cut ranges.
        - Transport row (play/pause/seek/Mark In/Mark Out/cuts).
        - Tool row (timeline zoom buttons + audio mute + volume slider).
        - Cut-ranges-to-remove list.

    Returns the final list of keep ranges (the inverse of the cut ranges) or
    None on cancel. Cancel from this GUI does NOT abort the cut workflow;
    the caller loops back to the cut-method menu.
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as exc:
        appio.error(f"Tkinter is not available, so the cut GUI cannot be opened: {exc}")
        return None

    if duration <= 0:
        appio.error("Cannot open the cut GUI: source duration is unknown.")
        return None

    palette = _UIPalette
    ffplay = shutil.which("ffplay")
    input_path: Path = answers["input_path"]

    try:
        with tempfile.TemporaryDirectory(prefix="ffmwizard_cut_") as temp_name:
            temp_dir = Path(temp_name)
            source_width, source_height = (1280, 720)
            try:
                source_width, source_height = first_video_size(answers)
            except Exception:
                pass
            preview_w, preview_h = preview_size(source_width, source_height)
            preview_w = max(320, min(960, preview_w))
            preview_h = max(180, round(preview_w * source_height / source_width))

            timeline_default_width = max(preview_w + 240, 920)
            timeline_height = 112

            state: dict[str, Any] = {
                "timestamp": 0.0,
                "in_marker": 0.0,
                "out_marker": min(5.0, duration),
                "cut_ranges": [],            # list of (start_s, end_s) REMOVE
                "selected_cut": -1,
                "playing": False,
                "audio_proc": None,
                "photo": None,
                "photo_key": None,
                "drag_target": None,         # ("playhead",) | ("in",) | ("out",) | ("cut", idx)
                # Timeline zoom: visible window expressed in seconds.
                "view_start_s": 0.0,
                "view_span_s": float(duration),
                # Audio.
                "volume": 50,
                "mute": False,
            }

            result: dict[str, list[tuple[float, float]] | None] = {"keep_ranges": None}

            root = tk.Tk()
            root.title("FFmWiz Cut Editor")
            root.configure(bg=palette.BG)
            _apply_tk_window_icon(root)
            _apply_app_ttk_theme(root)
            _apply_dark_title_bar(root)
            load_icon = _make_icon_loader(root)

            shell = ttk.Frame(root, padding=(16, 0, 16, 12))
            shell.pack(fill="both", expand=True)

            # ---- Header band ----------------------------------------------
            header = ttk.Frame(shell, style="Panel.TFrame", padding=(14, 12, 14, 12))
            header.pack(side="top", fill="x", pady=(8, 10))
            ttk.Label(
                header,
                text="FFmWiz Cut Editor",
                style="Title.TLabel",
            ).pack(side="left")
            ttk.Label(
                header,
                text=(
                    f"FPS {fps:.3f}    •    "
                    f"Duration {seconds_to_ffmpeg_time(duration)}    •    "
                    f"Source {Path(input_path).name}"
                ),
                style="Header.TLabel",
            ).pack(side="right")

            # ---- Preview viewport -----------------------------------------
            preview = tk.Canvas(
                shell,
                width=preview_w,
                height=preview_h,
                bg=palette.TIMELINE_BG,
                highlightthickness=1,
                highlightbackground=palette.BORDER,
            )
            preview.pack(side="top", fill="x", expand=False, pady=(0, 8))

            info_label = ttk.Label(shell, text="", anchor="w", style="Muted.TLabel")
            info_label.pack(side="top", fill="x", pady=(0, 4))

            # ---- Status strip (above the timeline) ------------------------
            status_strip = ttk.Frame(shell, style="Panel.TFrame", padding=(14, 8, 14, 8))
            status_strip.pack(side="top", fill="x", pady=(2, 6))
            time_label = ttk.Label(status_strip, text="", style="Header.TLabel")
            time_label.pack(side="left")

            # ---- Timeline -------------------------------------------------
            timeline_wrap = ttk.Frame(shell, style="Surface.TFrame", padding=(2, 2, 2, 2))
            timeline_wrap.pack(side="top", fill="x", expand=False, pady=(0, 8))
            timeline = tk.Canvas(
                timeline_wrap,
                width=timeline_default_width,
                height=timeline_height,
                bg=palette.TIMELINE_BG,
                highlightthickness=0,
            )
            timeline.pack(side="top", fill="x", expand=True)

            controls = ttk.Frame(shell)
            controls.pack(side="top", fill="x", pady=(2, 0))
            audio_row = ttk.Frame(shell)
            audio_row.pack(side="top", fill="x", pady=(8, 0))

            cut_list_frame = ttk.Frame(shell)
            cut_list_frame.pack(side="top", fill="both", expand=False, pady=(10, 4))
            ttk.Label(cut_list_frame, text="Cut ranges to remove:", style="Dim.TLabel").pack(side="top", anchor="w")
            cut_listbox = tk.Listbox(
                cut_list_frame,
                bg=palette.TIMELINE_BG,
                fg=palette.TEXT,
                selectbackground=palette.ACCENT_DARK,
                selectforeground=palette.TEXT,
                height=5,
                highlightthickness=1,
                highlightbackground=palette.BORDER,
                bd=0,
                activestyle="none",
                font=("Consolas", 10),
            )
            cut_listbox.pack(side="top", fill="x", expand=True)

            # --- Preview frame extraction (async, debounced) -----------------
            def _extract(timestamp: float, w: int, h: int) -> Path:
                return extract_crop_preview_frame(answers, temp_dir, w, h, timestamp)

            def _on_frame_ready(path: Path) -> None:
                try:
                    state["photo"] = tk.PhotoImage(file=str(path))
                except Exception:
                    return
                redraw_preview_canvas()

            scheduler = _PreviewScheduler(
                root,
                extract_fn=_extract,
                on_ready=_on_frame_ready,
                debounce_ms=70,
            )

            # --- Timeline math (zoomable) ------------------------------------
            def _view_start() -> float:
                return float(state["view_start_s"])

            def _view_span() -> float:
                return max(0.001, float(state["view_span_s"]))

            def _view_end() -> float:
                return _view_start() + _view_span()

            def timeline_width() -> int:
                return max(1, int(timeline.winfo_width()) or timeline_default_width)

            def time_to_x(seconds: float) -> int:
                width = timeline_width()
                pad = 12
                usable = max(1, width - pad * 2)
                start = _view_start()
                span = _view_span()
                return int(pad + ((seconds - start) / span) * usable)

            def x_to_time(x_pos: float) -> float:
                width = timeline_width()
                pad = 12
                usable = max(1, width - pad * 2)
                start = _view_start()
                span = _view_span()
                ratio = max(0.0, min(1.0, (x_pos - pad) / usable))
                return start + ratio * span

            def clamp_view() -> None:
                span = min(duration, max(0.05, _view_span()))
                start = max(0.0, min(duration - span, _view_start()))
                state["view_span_s"] = span
                state["view_start_s"] = start

            def zoom_around(focus_time: float, factor: float) -> None:
                """Zoom timeline by 'factor' (<1 zoom in, >1 zoom out), keeping
                focus_time at the same screen position."""
                width = timeline_width()
                pad = 12
                usable = max(1, width - pad * 2)
                # Current screen-x of focus_time before zooming.
                start = _view_start()
                span = _view_span()
                ratio = (focus_time - start) / max(0.001, span)
                new_span = max(0.05, min(duration, span * factor))
                new_start = focus_time - ratio * new_span
                state["view_span_s"] = new_span
                state["view_start_s"] = new_start
                clamp_view()
                redraw_all()

            def zoom_in(_event: Any = None) -> None:
                zoom_around(state["timestamp"], 0.5)

            def zoom_out(_event: Any = None) -> None:
                zoom_around(state["timestamp"], 2.0)

            def zoom_fit(_event: Any = None) -> None:
                state["view_start_s"] = 0.0
                state["view_span_s"] = duration
                redraw_all()

            def timeline_wheel(event: Any) -> str:
                # Wheel = zoom around cursor. Ctrl+wheel = finer zoom.
                step = 1.25 if (event.state & 0x0004) else 1.6
                factor = 1.0 / step if event.delta > 0 else step
                focus_time = x_to_time(event.x)
                zoom_around(focus_time, factor)
                return "break"

            # --- Rendering ---------------------------------------------------
            def refresh_cut_listbox() -> None:
                cut_listbox.delete(0, tk.END)
                for idx, (start, end) in enumerate(state["cut_ranges"]):
                    line = (
                        f"{idx + 1:2d}. {seconds_to_hmsf(start, fps)} -> "
                        f"{seconds_to_hmsf(end, fps)}   "
                        f"({seconds_to_ffmpeg_time(start)} -> {seconds_to_ffmpeg_time(end)})"
                    )
                    cut_listbox.insert(tk.END, line)
                if 0 <= state["selected_cut"] < len(state["cut_ranges"]):
                    cut_listbox.selection_clear(0, tk.END)
                    cut_listbox.selection_set(state["selected_cut"])

            def refresh_time_label() -> None:
                kept = duration - sum(max(0.0, e - s) for s, e in state["cut_ranges"])
                zoom_pct = int(round(100.0 * duration / max(0.001, _view_span())))
                # Compact, high-contrast status strip. We use Unicode "Big dot"
                # separators so individual fields stay readable.
                time_label.configure(text=(
                    f"●  Now {seconds_to_hmsf(state['timestamp'], fps)}    "
                    f"{seconds_to_ffmpeg_time(state['timestamp'])} / "
                    f"{seconds_to_ffmpeg_time(duration)}        "
                    f"●  In {seconds_to_hmsf(state['in_marker'], fps)}    "
                    f"●  Out {seconds_to_hmsf(state['out_marker'], fps)}    "
                    f"●  Kept {format_duration(kept)}    "
                    f"●  Cuts {len(state['cut_ranges'])}    "
                    f"●  Zoom {zoom_pct}%"
                ))

            def redraw_preview_canvas() -> None:
                preview.delete("all")
                cached = scheduler.request(state["timestamp"], preview_w, preview_h)
                if cached is not None:
                    try:
                        state["photo"] = tk.PhotoImage(file=str(cached))
                    except Exception:
                        pass
                if state.get("photo") is not None:
                    preview.create_image(preview_w // 2, preview_h // 2, image=state["photo"])
                else:
                    preview.create_text(
                        preview_w // 2,
                        preview_h // 2,
                        text="(loading preview frame...)",
                        fill=palette.TEXT_MUTE,
                        font=("Segoe UI", 11),
                    )

            def redraw_timeline() -> None:
                timeline.delete("all")
                width = timeline_width()
                height = timeline_height
                # Larger, more breathable layout than the original.
                label_strip_top = 6
                label_strip_bottom = 24
                track_top = 30
                track_bottom = height - 18
                track_mid = (track_top + track_bottom) // 2
                pad = 14
                # Frame around the timeline so it visually reads as a panel.
                timeline.create_rectangle(
                    0, 0, width, height,
                    fill=palette.TIMELINE_BG, outline="",
                )
                timeline.create_rectangle(
                    pad - 2, track_top, width - pad + 2, track_bottom,
                    fill=palette.TIMELINE_TRACK, outline=palette.BORDER, width=1,
                )
                # Choose a tick step that yields ~7-10 labels regardless of zoom.
                span = _view_span()
                approx_step = span / 8.0
                exponent = math.floor(math.log10(max(approx_step, 0.001)))
                base = 10 ** exponent
                step = base
                for candidate in (1, 2, 5, 10):
                    step = candidate * base
                    if span / step <= 10:
                        break
                start = _view_start()
                end = _view_end()
                first_tick = math.ceil(start / step) * step
                t = first_tick
                tick_font = ("Segoe UI Semibold", 10)
                sub_font = ("Segoe UI", 8)
                while t <= end + 1e-6:
                    x_pos = time_to_x(t)
                    if pad <= x_pos <= width - pad:
                        timeline.create_line(
                            x_pos, track_top - 6, x_pos, track_top,
                            fill=palette.TIMELINE_TICK_HI, width=1,
                        )
                        timeline.create_text(
                            x_pos, label_strip_top + (label_strip_bottom - label_strip_top) // 2,
                            text=seconds_to_ffmpeg_time(t),
                            fill=palette.TIMELINE_TICK_HI,
                            font=tick_font,
                        )
                    t += step
                # Secondary minor ticks (no label) at step / 5.
                minor_step = step / 5
                if minor_step > 0:
                    t = math.ceil(start / minor_step) * minor_step
                    while t <= end + 1e-6:
                        x_pos = time_to_x(t)
                        if pad <= x_pos <= width - pad:
                            timeline.create_line(
                                x_pos, track_top - 3, x_pos, track_top,
                                fill=palette.TIMELINE_TICK, width=1,
                            )
                        t += minor_step
                # Removed cut ranges (red boxes).
                for idx, (cstart, cend) in enumerate(state["cut_ranges"]):
                    if cend < start or cstart > end:
                        continue
                    x1 = time_to_x(max(cstart, start))
                    x2 = time_to_x(min(cend, end))
                    fill = palette.ACCENT_RED if idx == state["selected_cut"] else palette.ACCENT_RED_DK
                    timeline.create_rectangle(
                        x1, track_top + 3, x2, track_bottom - 3,
                        fill=fill, outline=palette.BORDER, width=1, tags=("cut", str(idx)),
                    )
                    if x2 - x1 > 36:
                        timeline.create_text(
                            (x1 + x2) // 2,
                            track_mid,
                            text=f"#{idx + 1}",
                            fill=palette.TEXT,
                            font=("Segoe UI Semibold", 10),
                            tags=("cut", str(idx)),
                        )
                # In / Out markers.
                in_x = time_to_x(state["in_marker"])
                out_x = time_to_x(state["out_marker"])
                marker_font = ("Segoe UI Semibold", 9)
                if pad - 8 <= in_x <= width - pad + 8:
                    timeline.create_line(in_x, track_top - 4, in_x, track_bottom + 4,
                                         fill=palette.ACCENT_GREEN, width=3, tags=("marker", "in"))
                    timeline.create_polygon(
                        in_x, track_top - 4, in_x - 8, track_top - 14, in_x + 8, track_top - 14,
                        fill=palette.ACCENT_GREEN, outline=palette.BG, tags=("marker", "in"),
                    )
                    timeline.create_text(in_x + 12, track_top - 9, anchor="w",
                                         text="IN", fill=palette.ACCENT_GREEN, font=marker_font)
                if pad - 8 <= out_x <= width - pad + 8:
                    timeline.create_line(out_x, track_top - 4, out_x, track_bottom + 4,
                                         fill=palette.ACCENT_YELLOW, width=3, tags=("marker", "out"))
                    timeline.create_polygon(
                        out_x, track_top - 4, out_x - 8, track_top - 14, out_x + 8, track_top - 14,
                        fill=palette.ACCENT_YELLOW, outline=palette.BG, tags=("marker", "out"),
                    )
                    timeline.create_text(out_x - 12, track_top - 9, anchor="e",
                                         text="OUT", fill=palette.ACCENT_YELLOW, font=marker_font)
                # Playhead.
                ph_x = time_to_x(state["timestamp"])
                if pad - 6 <= ph_x <= width - pad + 6:
                    timeline.create_line(ph_x, track_top - 10, ph_x, track_bottom + 10,
                                         fill=palette.PLAYHEAD, width=2, tags=("playhead",))
                    timeline.create_polygon(
                        ph_x - 7, track_bottom + 4,
                        ph_x + 7, track_bottom + 4,
                        ph_x, track_bottom + 14,
                        fill=palette.PLAYHEAD, outline=palette.BG, tags=("playhead",),
                    )
                    timeline.create_text(
                        ph_x, label_strip_bottom - 4,
                        text=seconds_to_ffmpeg_time(state["timestamp"]),
                        fill=palette.PLAYHEAD,
                        font=sub_font,
                        tags=("playhead",),
                    )

            def redraw_all() -> None:
                redraw_preview_canvas()
                redraw_timeline()
                refresh_cut_listbox()
                refresh_time_label()

            # --- Audio playback (ffplay) -------------------------------------
            def stop_audio() -> None:
                proc = state.get("audio_proc")
                state["audio_proc"] = None
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=0.8)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass

            def start_audio() -> None:
                stop_audio()
                if not ffplay or state["mute"] or int(state["volume"]) <= 0:
                    return
                args = [
                    ffplay, "-nodisp", "-autoexit", "-loglevel", "error",
                    "-ss", f"{state['timestamp']:.3f}",
                    "-volume", str(int(state["volume"])),
                    str(input_path),
                ]
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                try:
                    state["audio_proc"] = subprocess.Popen(
                        args,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creation_flags,
                    )
                except Exception as exc:
                    appio.error(f"Could not start ffplay audio preview: {exc}")

            def playback_tick() -> None:
                if not state["playing"]:
                    return
                state["timestamp"] = min(duration, state["timestamp"] + 1.0)
                redraw_all()
                if state["timestamp"] >= duration - 1e-6:
                    # Reached the end. Stop playback cleanly so the next Play
                    # press can rewind to the beginning (see toggle_playback).
                    state["playing"] = False
                    play_button.configure(text="▶ Play (Space)")
                    stop_audio()
                    return
                root.after(1000, playback_tick)

            def toggle_playback(_event: Any = None) -> None:
                # Bug fix: if playback is at or near the end and the user
                # presses Play again, rewind to the start so playback runs
                # for the whole clip instead of ending after ~1 second.
                if not state["playing"] and state["timestamp"] >= duration - 0.5:
                    state["timestamp"] = 0.0
                    redraw_all()
                state["playing"] = not state["playing"]
                play_button.configure(text=("⏸ Pause (Space)" if state["playing"] else "▶ Play (Space)"))
                if state["playing"]:
                    start_audio()
                    root.after(1000, playback_tick)
                else:
                    stop_audio()

            def set_time(value: float) -> None:
                state["timestamp"] = max(0.0, min(duration, float(value)))
                redraw_all()
                if state["playing"]:
                    start_audio()

            def go_home(_event: Any = None) -> None:
                set_time(0.0)

            def go_end(_event: Any = None) -> None:
                set_time(duration)

            def set_in_marker(_event: Any = None) -> None:
                state["in_marker"] = state["timestamp"]
                if state["out_marker"] < state["in_marker"]:
                    state["out_marker"] = state["in_marker"]
                redraw_all()

            def set_out_marker(_event: Any = None) -> None:
                state["out_marker"] = state["timestamp"]
                if state["in_marker"] > state["out_marker"]:
                    state["in_marker"] = state["out_marker"]
                redraw_all()

            def add_cut_range(_event: Any = None) -> None:
                start = min(state["in_marker"], state["out_marker"])
                end = max(state["in_marker"], state["out_marker"])
                if end - start < 1e-3:
                    appio.error("Cut range is empty; place In and Out at different positions first.")
                    return
                state["cut_ranges"].append((start, end))
                state["cut_ranges"] = list(normalize_cut_ranges(state["cut_ranges"], duration))
                state["selected_cut"] = len(state["cut_ranges"]) - 1
                redraw_all()

            def remove_selected_cut(_event: Any = None) -> None:
                idx = state["selected_cut"]
                if 0 <= idx < len(state["cut_ranges"]):
                    del state["cut_ranges"][idx]
                    state["selected_cut"] = min(idx, len(state["cut_ranges"]) - 1)
                    redraw_all()

            def clear_all_cuts(_event: Any = None) -> None:
                state["cut_ranges"] = []
                state["selected_cut"] = -1
                redraw_all()

            def confirm(_event: Any = None) -> None:
                keep = invert_cut_ranges_to_keep_ranges(state["cut_ranges"], duration)
                if not state["cut_ranges"]:
                    start = min(state["in_marker"], state["out_marker"])
                    end = max(state["in_marker"], state["out_marker"])
                    if end - start > 1e-3:
                        keep = [(start, end)]
                    else:
                        keep = [(0.0, duration)]
                result["keep_ranges"] = keep
                state["playing"] = False
                stop_audio()
                scheduler.cancel()
                root.destroy()

            def cancel(_event: Any = None) -> None:
                result["keep_ranges"] = None
                state["playing"] = False
                stop_audio()
                scheduler.cancel()
                root.destroy()

            def on_listbox_select(_event: Any) -> None:
                sel = cut_listbox.curselection()
                state["selected_cut"] = sel[0] if sel else -1
                redraw_timeline()

            # --- Timeline drag handlers --------------------------------------
            def begin_timeline_drag(event: Any) -> None:
                items = timeline.find_overlapping(event.x - 4, event.y - 4, event.x + 4, event.y + 4)
                target = None
                for item_id in reversed(items):
                    tags = timeline.gettags(item_id)
                    if "marker" in tags and "in" in tags:
                        target = ("in",)
                        break
                    if "marker" in tags and "out" in tags:
                        target = ("out",)
                        break
                    if "playhead" in tags:
                        target = ("playhead",)
                        break
                    if "cut" in tags:
                        idx = int(tags[tags.index("cut") + 1])
                        state["selected_cut"] = idx
                        refresh_cut_listbox()
                        target = ("cut", idx)
                        break
                if target is None:
                    set_time(x_to_time(event.x))
                    target = ("playhead",)
                state["drag_target"] = target

            def drag_timeline(event: Any) -> None:
                target = state.get("drag_target")
                if not target:
                    return
                t = x_to_time(event.x)
                if target[0] == "playhead":
                    set_time(t)
                elif target[0] == "in":
                    state["in_marker"] = max(0.0, min(duration, t))
                    if state["out_marker"] < state["in_marker"]:
                        state["out_marker"] = state["in_marker"]
                    redraw_all()
                elif target[0] == "out":
                    state["out_marker"] = max(0.0, min(duration, t))
                    if state["in_marker"] > state["out_marker"]:
                        state["in_marker"] = state["out_marker"]
                    redraw_all()
                elif target[0] == "cut":
                    idx = target[1]
                    if 0 <= idx < len(state["cut_ranges"]):
                        start, end = state["cut_ranges"][idx]
                        span = end - start
                        new_start = max(0.0, min(duration - span, t - span / 2))
                        state["cut_ranges"][idx] = (new_start, new_start + span)
                        redraw_all()

            def end_timeline_drag(_event: Any) -> None:
                if state.get("drag_target") and state["drag_target"][0] == "cut":
                    state["cut_ranges"] = list(normalize_cut_ranges(state["cut_ranges"], duration))
                state["drag_target"] = None

            # --- Audio controls ----------------------------------------------
            volume_var = tk.IntVar(value=int(state["volume"]))
            mute_var = tk.BooleanVar(value=bool(state["mute"]))

            def volume_level_index() -> int:
                v = int(volume_var.get())
                if mute_var.get() or v <= 0:
                    return 0
                if v < 34:
                    return 1
                if v < 67:
                    return 2
                return 3

            def refresh_audio_label() -> None:
                lvl = volume_level_index()
                icon = volume_icons.get(lvl)
                mute_text = "Muted (M)" if state["mute"] else "Mute (M)"
                if icon is not None:
                    mute_btn.configure(text=mute_text, image=icon, compound="left")
                else:
                    mute_btn.configure(text=("🔇 " + mute_text if state["mute"] else "🔊 " + mute_text))
                volume_label.configure(text=f"Vol {int(volume_var.get()):3d}%")

            def on_volume_change(_value: Any = None) -> None:
                state["volume"] = max(0, min(100, int(volume_var.get())))
                if state["playing"]:
                    start_audio()
                refresh_audio_label()

            def toggle_mute(_event: Any = None) -> None:
                state["mute"] = not state["mute"]
                mute_var.set(state["mute"])
                if state["playing"]:
                    start_audio()
                refresh_audio_label()

            def volume_wheel(event: Any) -> str:
                step = 3 if (event.state & 0x0004) else 5
                if event.delta > 0:
                    new_val = min(100, int(volume_var.get()) + step)
                else:
                    new_val = max(0, int(volume_var.get()) - step)
                volume_var.set(new_val)
                on_volume_change()
                return "break"

            # --- Icons -------------------------------------------------------
            zoom_in_icon = load_icon("zoom_in")
            zoom_out_icon = load_icon("zoom_out")
            volume_icons = {
                0: load_icon("volume_0"),
                1: load_icon("volume_1"),
                2: load_icon("volume_2"),
                3: load_icon("volume_3"),
            }

            # --- Transport row (buttons) ------------------------------------
            play_button = ttk.Button(
                controls, text="▶ Play (Space)", command=toggle_playback, style="Accent.TButton",
            )
            play_button.pack(side="left")
            ttk.Button(controls, text="⏮ Home (Home)", command=go_home).pack(side="left", padx=(8, 0))
            ttk.Button(controls, text="-5s (Shift+←)", command=lambda: set_time(state["timestamp"] - 5.0)).pack(side="left", padx=(8, 0))
            ttk.Button(controls, text="-1s (←)", command=lambda: set_time(state["timestamp"] - 1.0)).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="+1s (→)", command=lambda: set_time(state["timestamp"] + 1.0)).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="+5s (Shift+→)", command=lambda: set_time(state["timestamp"] + 5.0)).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="End (End) ⏭", command=go_end).pack(side="left", padx=(8, 0))
            ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=10)
            ttk.Button(controls, text="Mark In (I)", command=set_in_marker).pack(side="left")
            ttk.Button(controls, text="Mark Out (O)", command=set_out_marker).pack(side="left", padx=(4, 0))
            ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=10)
            ttk.Button(controls, text="Add cut (A)", command=add_cut_range).pack(side="left")
            ttk.Button(controls, text="Delete cut (Del)", command=remove_selected_cut).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="Clear all", command=clear_all_cuts).pack(side="left", padx=(4, 0))
            ttk.Button(controls, text="Confirm (Enter)", command=confirm, style="Accent.TButton").pack(side="right")
            ttk.Button(controls, text="Cancel (Esc)", command=cancel, style="Danger.TButton").pack(side="right", padx=(0, 8))

            # --- Audio + zoom row -------------------------------------------
            zoom_in_btn = ttk.Button(audio_row, text="Zoom In (+)", command=zoom_in)
            if zoom_in_icon is not None:
                zoom_in_btn.configure(image=zoom_in_icon, compound="left")
            zoom_in_btn.pack(side="left")
            zoom_out_btn = ttk.Button(audio_row, text="Zoom Out (-)", command=zoom_out)
            if zoom_out_icon is not None:
                zoom_out_btn.configure(image=zoom_out_icon, compound="left")
            zoom_out_btn.pack(side="left", padx=(6, 0))
            ttk.Button(audio_row, text="Reset Zoom (Ctrl+R)", command=zoom_fit).pack(side="left", padx=(6, 0))
            ttk.Separator(audio_row, orient="vertical").pack(side="left", fill="y", padx=12)
            mute_btn = ttk.Button(audio_row, text="Mute (M)", command=toggle_mute)
            mute_btn.pack(side="left")
            volume_label = ttk.Label(audio_row, text="Vol  50%", style="Dim.TLabel")
            volume_label.pack(side="left", padx=(10, 6))
            volume_slider = ttk.Scale(
                audio_row,
                from_=0,
                to=100,
                orient="horizontal",
                variable=volume_var,
                command=on_volume_change,
                length=200,
            )
            volume_slider.pack(side="left")
            # Mouse-wheel volume support when hovering the slider OR the
            # label/mute button (so users have a clear hover target).
            for widget in (volume_slider, volume_label, mute_btn):
                widget.bind("<MouseWheel>", volume_wheel)
            if not ffplay:
                volume_slider.state(["disabled"])
                mute_btn.state(["disabled"])
                ttk.Label(audio_row, text="(ffplay not in PATH; audio preview disabled)",
                          style="Muted.TLabel").pack(side="left", padx=(10, 0))

            info_label.configure(text=(
                "Click or drag the playhead to seek. Mark In (I) and Mark Out (O), "
                "then Add cut (A). Mouse wheel over the timeline zooms around the cursor; "
                "Ctrl+wheel = finer zoom. Mouse wheel over the volume slider changes volume."
            ))

            # --- Timeline mouse bindings ------------------------------------
            timeline.bind("<ButtonPress-1>", begin_timeline_drag)
            timeline.bind("<B1-Motion>", drag_timeline)
            timeline.bind("<ButtonRelease-1>", end_timeline_drag)
            timeline.bind("<MouseWheel>", timeline_wheel)
            timeline.bind("<Configure>", lambda _e: redraw_timeline())
            cut_listbox.bind("<<ListboxSelect>>", on_listbox_select)

            # --- Layout-independent keyboard shortcuts ----------------------
            # Bindings target the physical key (Windows VK code) so they
            # still fire when the active keyboard layout is Persian or any
            # other non-Latin layout.

            def is_editing_text(event: Any) -> bool:
                widget = getattr(event, "widget", None)
                try:
                    cls = str(widget.winfo_class()) if widget is not None else ""
                except Exception:
                    cls = ""
                return cls in ("Entry", "TEntry", "Text", "Spinbox", "TCombobox", "TSpinbox")

            _bind_layout_independent_keys(root, [
                {"key": "space", "callback": toggle_playback},
                {"key": "i", "callback": set_in_marker},
                {"key": "o", "callback": set_out_marker},
                {"key": "a", "callback": add_cut_range},
                {"key": "m", "callback": toggle_mute},
                {"key": "f", "callback": zoom_fit},
                {"key": "r", "ctrl": True, "callback": zoom_fit},
                {"key": "plus", "callback": zoom_in},
                {"key": "minus", "callback": zoom_out},
                {"key": "home", "callback": go_home},
                {"key": "end", "callback": go_end},
                {"key": "left", "shift": False, "callback": lambda _e: set_time(state["timestamp"] - 1.0)},
                {"key": "right", "shift": False, "callback": lambda _e: set_time(state["timestamp"] + 1.0)},
                {"key": "left", "shift": True, "callback": lambda _e: set_time(state["timestamp"] - 5.0)},
                {"key": "right", "shift": True, "callback": lambda _e: set_time(state["timestamp"] + 5.0)},
                {"key": "delete", "callback": remove_selected_cut},
                {"key": "return", "callback": confirm},
                {"key": "escape", "callback": cancel},
            ], is_text_focus_fn=is_editing_text)
            root.protocol("WM_DELETE_WINDOW", cancel)

            root.update_idletasks()
            refresh_audio_label()
            redraw_all()
            root.mainloop()
    except Exception as exc:
        appio.error(f"Cut GUI failed: {exc}")
        return None

    return result.get("keep_ranges")


__all__ = [
    'choose_crop_graphically',
    'open_audio_transform_gui',
    'open_cut_gui',
    'open_unified_video_gui',
    'open_video_speed_gui',
    '_apply_app_ttk_theme',
    '_bind_layout_independent_keys',
    '_choose_crop_graphically_tk',
    '_launch_qt_gui',
    '_open_legacy_cut_gui_tk',
    'WIN_VK_BY_NAME',
    '_PreviewScheduler',
    '_UIPalette',
]
