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


def _gui_palette() -> dict[str, str]:
    """The canonical colour tokens from ffmwiz/gui/gui_style.py.

    That directory is a script dir (the GUI runs as a subprocess), not a
    package, so it is imported the same way the GUI subprocess imports it."""
    gui_dir = str(Path(__file__).resolve().parent / FFMWIZ_GUI_DIR_NAME)
    if gui_dir not in sys.path:
        sys.path.insert(0, gui_dir)
    from gui_style import PALETTE  # type: ignore
    return PALETTE


class _UIPalette:
    """Tk-editor view over the canonical GUI tokens.

    These used to be 26 independently chosen hex values that drifted from the Qt
    palette on every shared role - background, panel, surface, border, timeline,
    accent and all three text weights - which is why the Tk crop/cut editors
    looked like a different application (USER-12-1). The attribute names are
    kept because the editors reference them."""

    _P = _gui_palette()
    BG = _P["bg"]                      # main app background
    PANEL = _P["panel"]                # toolbar / header band
    PANEL_HI = _P["panel_alt"]
    SURFACE = _P["surface"]            # button / widget surface
    SURFACE_HOVER = _P["surface_hover"]
    SURFACE_PRESSED = _P["surface_pressed"]
    SURFACE_DIS = _P["surface_disabled"]
    BORDER = _P["border"]
    BORDER_SOFT = _P["border_soft"]
    TIMELINE_BG = _P["timeline_bg"]
    TIMELINE_TRACK = _P["timeline_track"]
    TIMELINE_TICK = _P["tick_lo"]
    TIMELINE_TICK_HI = _P["tick_hi"]
    ACCENT = _P["accent"]              # primary action accent
    ACCENT_STRONG = _P["accent_hover"]
    ACCENT_DARK = _P["accent_dim"]
    ACCENT_RED = _P["cut_red"]
    ACCENT_RED_DK = _P["cut_red_dim"]
    ACCENT_GREEN = _P["green_text"]
    ACCENT_YELLOW = _P["warn"]
    ACCENT_ORANGE = _P["danger_alt_text"]
    PLAYHEAD = _P["playhead"]
    TEXT = _P["text"]
    TEXT_DIM = _P["text_dim"]
    TEXT_MUTE = _P["text_mute"]
    TEXT_ON_ACCENT = _P["text_on_accent"]


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


__all__ = [
    '_apply_app_ttk_theme',
    '_bind_layout_independent_keys',
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


# guibridge_b holds an overflow slice of this module (split for file size).
from ffmwiz import guibridge_b as _guibridge_b  # noqa: E402
from ffmwiz.guibridge_b import *  # noqa: E402,F401,F403
__all__ = list(__all__) + list(_guibridge_b.__all__)
