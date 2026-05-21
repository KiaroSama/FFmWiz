"""FFmWiz GUI - PySide6 professional Cut + Crop editors.

This file is the dedicated GUI front-end for FFmWiz. It is launched as a
subprocess by FFmWiz.py via a small JSON-IPC contract so the existing
Python CLI stays isolated from Qt, and the GUI surface uses a proper
Windows-friendly toolkit (Qt) with smooth playback, accurate timeline
interaction, undo/redo, layout-independent keyboard shortcuts, and a
polished "Claude Design"-style dark theme inspired by GitHub.

Run directly:
    python ffmwiz_gui.py --request <request.json> --reply <reply.json>

Request JSON contract:
    {
        "mode": "cut" | "crop" | "video_speed" | "audio_cut" | "audio_speed",
        "input_path": "<absolute path to the source media>",
        "fps": 30.0,
        "duration": 123.456,
        // mode-specific fields (see build_cut_editor / build_crop_editor)
    }

Reply JSON contract:
    cut         -> {"status": "ok"|"canceled", "keep_ranges": [[start_s, end_s], ...]}
    crop        -> {"status": "ok"|"canceled", "margins": [top, left, right, bottom]}
    video_speed -> {"status": "ok"|"canceled", "speed": 1.25, "reverse": false, "include_audio": true}
    audio_cut   -> {"status": "ok"|"canceled", "keep_ranges": [[start_s, end_s], ...]}
    audio_speed -> {"status": "ok"|"canceled", "speed": 1.25, "reverse": false}
"""

from __future__ import annotations

import argparse
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


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass


# =====================================================================
# Shared logging/icon helpers.
# =====================================================================

APP_ID = "FFmWiz.GUI"
ASSETS_ROOT = Path(__file__).resolve().parent / "assets"
ASSETS_DIR = ASSETS_ROOT / "icons"
_GUI_LOG_PATH: Path | None = None
_APP_QICON_CACHE: Any = None
_PARENT_PID: int | None = None


def _debug_enabled() -> bool:
    return bool(os.environ.get("FFMWIZ_DEBUG") or os.environ.get("FFMWIZ_DEBUG_GUI"))


def _gui_log_debug(message: str, *, force: bool = False) -> None:
    debug_enabled = _debug_enabled()
    if not force and not debug_enabled:
        return
    line = f"GUI DEBUG: {message}"
    if _GUI_LOG_PATH is not None:
        try:
            with _GUI_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            return
        except Exception:
            pass
    if debug_enabled:
        print(line, file=sys.stderr)


def _app_icon_path(prefer_ico: bool = False) -> Path | None:
    suffixes = (".ico", ".png", ".svg") if prefer_ico else (".png", ".ico", ".svg")
    for suffix in suffixes:
        path = ASSETS_DIR / f"ffmwiz_app{suffix}"
        if path.exists():
            return path
    return None


def _set_windows_app_id() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        _gui_log_debug(f"Set Windows AppUserModelID={APP_ID}", force=True)
    except Exception as exc:
        _gui_log_debug(f"Could not set Windows AppUserModelID: {exc}", force=True)


def _parent_process_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return True
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            SYNCHRONIZE = 0x00100000
            WAIT_TIMEOUT = 0x00000102
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                False,
                int(pid),
            )
            if not handle:
                return False
            try:
                return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _install_parent_watchdog(app) -> None:
    parent_pid = _PARENT_PID
    if not parent_pid:
        return
    try:
        from PySide6 import QtCore  # type: ignore

        timer = QtCore.QTimer(app)
        timer.setInterval(700)

        def check_parent() -> None:
            if not _parent_process_is_alive(parent_pid):
                _gui_log_debug(f"Parent process {parent_pid} is gone; closing GUI.", force=True)
                app.quit()

        timer.timeout.connect(check_parent)
        timer.start()
        app._ffmwiz_parent_watchdog = timer
        _gui_log_debug(f"Installed parent process watchdog for pid={parent_pid}", force=True)
    except Exception as exc:
        _gui_log_debug(f"Could not install parent process watchdog: {exc}", force=True)


def _apply_native_windows_icon(window) -> None:
    """Force the native HWND icon so Windows taskbar/Alt+Tab do not fall back
    to the python.exe icon when Qt is launched from the CLI subprocess."""
    if os.name != "nt":
        return
    icon_path = _app_icon_path(prefer_ico=True)
    if icon_path is None:
        _gui_log_debug("Native Windows icon skipped: ffmwiz_app icon asset not found.", force=True)
        return
    try:
        import ctypes

        hwnd = int(window.winId())
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_void_p,
        ]
        set_class_long_ptr = getattr(user32, "SetClassLongPtrW", None) or user32.SetClassLongW
        set_class_long_ptr.restype = ctypes.c_void_p
        set_class_long_ptr.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        ICON_SMALL2 = 2
        GCLP_HICON = -14
        GCLP_HICONSM = -34

        small_icon = user32.LoadImageW(
            None, str(icon_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE
        )
        big_icon = user32.LoadImageW(
            None, str(icon_path), IMAGE_ICON, 256, 256, LR_LOADFROMFILE
        )
        if not small_icon and not big_icon:
            _gui_log_debug(f"Native Windows icon load returned null handles: {icon_path}", force=True)
            return
        if small_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small_icon)
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL2, small_icon)
            set_class_long_ptr(hwnd, GCLP_HICONSM, small_icon)
        if big_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big_icon)
            set_class_long_ptr(hwnd, GCLP_HICON, big_icon)
        # Keep handles alive for the lifetime of the window. Destroying them
        # immediately can make Explorer fall back to the default process icon.
        window._ffmwiz_native_icon_handles = (small_icon, big_icon)
        _gui_log_debug(f"Applied native Windows HWND icons from {icon_path}", force=True)
    except Exception as exc:
        _gui_log_debug(f"Could not apply native Windows icon: {exc}", force=True)


# =====================================================================
# Claude-Design-inspired dark theme palette + QSS.
# Shared between both editor windows so the Cut Editor and Crop Editor
# have an identical visual identity.
# =====================================================================

PALETTE: dict[str, str] = {
    "bg":              "#0d1117",
    "panel":           "#161b22",
    "panel_alt":       "#1a1f2a",
    "surface":         "#21262d",
    "surface_hover":   "#2e353d",
    "surface_pressed": "#1c2128",
    "surface_disabled":"#161b22",
    "border":          "#30363d",
    "border_strong":   "#3a4150",
    "border_soft":     "#21262d",
    "timeline_bg":     "#0a0d12",
    "timeline_track":  "#1c2128",
    "tick_hi":         "#e6edf3",
    "tick_lo":         "#7d8590",
    "accent":          "#1f6feb",
    "accent_hover":    "#388bfd",
    "accent_pressed":  "#1158c7",
    "accent_dim":      "#1f3a66",
    "accent_text":     "#79b4ff",
    "green":           "#238636",
    "green_hover":     "#2ea043",
    "green_pressed":   "#196c2e",
    "green_text":      "#56d364",
    "purple":          "#4b2a7f",
    "purple_hover":    "#5f35a3",
    "purple_pressed":  "#3a2064",
    "danger":          "#a40e26",
    "danger_hover":    "#c9303f",
    "danger_pressed":  "#7d0a1c",
    "danger_text":     "#ff7b72",
    "danger_cut":      "#7f123f",
    "danger_cut_hover":"#a51b55",
    "danger_cut_pressed":"#5e0d2e",
    "danger_alt":      "#643618",
    "danger_alt_hover":"#8a4a1f",
    "danger_alt_pressed":"#4d2812",
    "danger_alt_text": "#f0a96b",
    "warn":            "#d29922",
    "marker_in":       "#2ddc7f",
    "marker_out":      "#d29922",
    "cut_red":         "#f85149",
    "cut_red_dim":     "#a92927",
    "playhead":        "#ffffff",
    "text":            "#e6edf3",
    "text_dim":        "#c9d1d9",
    "text_mute":       "#7d8590",
    "text_subtle":     "#484f58",
    "text_on_accent":  "#ffffff",
}

QSS = f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; }}

QMainWindow, QWidget#central {{
    background-color: {PALETTE['bg']};
    color: {PALETTE['text']};
}}

QLabel {{ color: {PALETTE['text']}; background: transparent; }}
QLabel#title {{
    color: {PALETTE['accent_text']};
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.2px;
}}
QLabel#headerInfo {{ color: {PALETTE['text_mute']}; font-size: 11px; }}
QLabel#dim       {{ color: {PALETTE['text_mute']}; font-size: 11px; }}
QLabel#controlLabel {{ color: {PALETTE['text']}; font-size: 12px; font-weight: 700; }}
QLabel#tip       {{ color: {PALETTE['text_dim']}; font-size: 11px; font-style: normal; padding: 5px 8px; background-color: {PALETTE['panel_alt']}; border: 1px solid {PALETTE['border_soft']}; border-radius: 6px; }}
QLabel#status    {{ color: {PALETTE['text']}; font-size: 12px; }}

QFrame#header, QFrame#panel, QFrame#statusBand {{
    background-color: {PALETTE['panel']};
    border: 1px solid {PALETTE['border']};
    border-radius: 8px;
}}

QPushButton {{
    background-color: {PALETTE['surface']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 11px;
    min-height: 22px;
}}
QPushButton:hover {{
    background-color: {PALETTE['surface_hover']};
    border-color: {PALETTE['border_strong']};
}}
QPushButton:pressed {{ background-color: {PALETTE['surface_pressed']}; }}
QPushButton:disabled {{
    background-color: {PALETTE['surface_disabled']};
    color: {PALETTE['text_subtle']};
    border-color: {PALETTE['border_soft']};
}}

QPushButton#primary {{
    background-color: {PALETTE['accent']};
    color: {PALETTE['text_on_accent']};
    border-color: {PALETTE['accent']};
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background-color: {PALETTE['accent_hover']};
    border-color: {PALETTE['accent_hover']};
}}
QPushButton#primary:pressed {{ background-color: {PALETTE['accent_pressed']}; }}
QPushButton#primary:disabled {{
    background-color: {PALETTE['surface_disabled']};
    color: {PALETTE['text_subtle']};
    border-color: {PALETTE['border_soft']};
}}

QPushButton#green {{
    background-color: {PALETTE['green']};
    color: {PALETTE['text_on_accent']};
    border-color: {PALETTE['green']};
    font-weight: 600;
}}
QPushButton#green:hover {{
    background-color: {PALETTE['green_hover']};
    border-color: {PALETTE['green_hover']};
}}
QPushButton#green:pressed {{ background-color: {PALETTE['green_pressed']}; }}
QPushButton#green:disabled {{
    background-color: #1b2f22;
    color: {PALETTE['text_mute']};
    border-color: #285b35;
    font-weight: 500;
}}

QPushButton#purple {{
    background-color: {PALETTE['purple']};
    color: {PALETTE['text_on_accent']};
    border-color: {PALETTE['purple_hover']};
    font-weight: 600;
}}
QPushButton#purple:hover {{
    background-color: {PALETTE['purple_hover']};
    border-color: {PALETTE['purple_hover']};
}}
QPushButton#purple:pressed {{ background-color: {PALETTE['purple_pressed']}; }}
QPushButton#purple:disabled {{
    background-color: #2d2440;
    color: {PALETTE['text_mute']};
    border-color: #46345f;
    font-weight: 500;
}}
QPushButton#purple:disabled:hover {{
    background-color: #2d2440;
    border-color: #46345f;
}}

QPushButton#danger {{
    background-color: {PALETTE['danger']};
    color: {PALETTE['text_on_accent']};
    border-color: {PALETTE['danger_hover']};
    font-weight: 600;
}}
QPushButton#danger:hover {{
    background-color: {PALETTE['danger_hover']};
    border-color: {PALETTE['danger_hover']};
}}
QPushButton#danger:pressed {{ background-color: {PALETTE['danger_pressed']}; }}
QPushButton#danger:disabled {{
    background-color: #701020;
    color: #f0a3aa;
    border-color: #8c2638;
    font-weight: 600;
}}
QPushButton#danger:disabled:hover {{
    background-color: #701020;
    color: #f0a3aa;
    border-color: #8c2638;
}}

QPushButton#dangerCut {{
    background-color: {PALETTE['danger_cut']};
    color: {PALETTE['text_on_accent']};
    border-color: {PALETTE['danger_cut_hover']};
    font-weight: 600;
}}
QPushButton#dangerCut:hover {{
    background-color: {PALETTE['danger_cut_hover']};
    border-color: {PALETTE['danger_cut_hover']};
}}
QPushButton#dangerCut:pressed {{ background-color: {PALETTE['danger_cut_pressed']}; }}
QPushButton#dangerCut:disabled {{
    background-color: #4d1730;
    color: #e6a4bf;
    border-color: #783154;
    font-weight: 600;
}}
QPushButton#dangerCut:disabled:hover {{
    background-color: #4d1730;
    color: #e6a4bf;
    border-color: #783154;
}}

QPushButton#dangerAlt {{
    background-color: {PALETTE['danger_alt']};
    color: {PALETTE['text_on_accent']};
    border-color: {PALETTE['danger_alt_hover']};
    font-weight: 600;
}}
QPushButton#dangerAlt:hover {{
    background-color: {PALETTE['danger_alt_hover']};
    border-color: {PALETTE['danger_alt_hover']};
}}
QPushButton#dangerAlt:pressed {{ background-color: {PALETTE['danger_alt_pressed']}; }}
QPushButton#dangerAlt:disabled {{
    background-color: #523016;
    color: #e5b27d;
    border-color: #78451f;
    font-weight: 600;
}}
QPushButton#dangerAlt:disabled:hover {{
    background-color: #523016;
    color: #e5b27d;
    border-color: #78451f;
}}

QPushButton#tool {{ padding: 6px 10px; }}
QPushButton#tool[active="true"] {{
    background-color: {PALETTE['accent_dim']};
    color: {PALETTE['accent_text']};
    border-color: {PALETTE['accent']};
    font-weight: 600;
}}

QPushButton#markIn {{
    background-color: #123a29;
    color: #d7ffe8;
    border-color: {PALETTE['marker_in']};
    font-weight: 600;
}}
QPushButton#markIn:hover {{
    background-color: #185236;
    border-color: #5ef0a5;
}}
QPushButton#markOut {{
    background-color: #3b2b12;
    color: #ffe7b8;
    border-color: {PALETTE['marker_out']};
    font-weight: 600;
}}
QPushButton#markOut:hover {{
    background-color: #563c16;
    border-color: #e7b94f;
}}

QPushButton#iconOnly {{
    padding: 5px 6px;
    min-width: 26px;
}}
QPushButton#timelineViewArrow {{
    background-color: transparent;
    color: #7df58a;
    border: none;
    border-radius: 8px;
    padding: 0;
    min-width: 14px;
    max-width: 14px;
    min-height: 14px;
    max-height: 14px;
    font-size: 9px;
    font-weight: 700;
}}
QPushButton#timelineViewArrow:hover {{
    background-color: #20302a;
    color: #a6ffad;
}}
QPushButton#timelineViewArrow:pressed {{
    background-color: #284435;
}}
QPushButton#timelineViewArrow:disabled {{
    background-color: #111820;
    color: #35563c;
    border-color: {PALETTE['border_soft']};
}}

QPushButton#timelineZoomArrow {{
    background-color: transparent;
    color: {PALETTE['accent_hover']};
    border: none;
    border-radius: 8px;
    padding: 0;
    min-width: 14px;
    max-width: 14px;
    min-height: 14px;
    max-height: 14px;
    font-size: 9px;
    font-weight: 700;
}}
QPushButton#timelineZoomArrow:hover {{
    background-color: #182943;
    color: {PALETTE['accent_text']};
}}
QPushButton#timelineZoomArrow:pressed {{
    background-color: #1f3a66;
}}
QPushButton#timelineZoomArrow:disabled {{
    background-color: #111820;
    color: #23466f;
    border-color: {PALETTE['border_soft']};
}}

QSlider::groove:horizontal {{
    background: {PALETTE['timeline_track']};
    height: 5px;
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{ background: {PALETTE['accent']}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: {PALETTE['accent_hover']};
    width: 40px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
    border: 1px solid {PALETTE['accent']};
}}
QSlider::handle:horizontal:hover {{ background: {PALETTE['text']}; }}

QSlider#timelineZoomSlider::groove:horizontal {{
    background: {PALETTE['timeline_track']};
    height: 5px;
    border-radius: 3px;
}}
QSlider#timelineZoomSlider::sub-page:horizontal {{
    background: {PALETTE['accent']};
    border-radius: 3px;
}}
QSlider#timelineZoomSlider::handle:horizontal {{
    background: {PALETTE['accent_hover']};
    width: 44px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
    border: 1px solid {PALETTE['accent']};
}}
QSlider#timelineZoomSlider::handle:horizontal:hover {{ background: {PALETTE['text']}; }}

QFrame#timelineViewFrame {{
    background: #111820;
    border: 1px solid {PALETTE['border_strong']};
    border-radius: 9px;
}}

QScrollBar#timelineViewScroll:horizontal {{
    background: transparent;
    border: none;
    border-radius: 8px;
    height: 16px;
    margin: 0;
}}
QScrollBar#timelineViewScroll::handle:horizontal {{
    background: #7df58a;
    min-width: 192px;
    border-radius: 7px;
    margin: 2px 0;
}}
QScrollBar#timelineViewScroll::handle:horizontal:hover {{
    background: #a6ffad;
}}
QScrollBar#timelineViewScroll::add-line:horizontal,
QScrollBar#timelineViewScroll::sub-line:horizontal {{
    background: transparent;
    border: none;
    width: 0;
}}
QScrollBar#timelineViewScroll::add-page:horizontal,
QScrollBar#timelineViewScroll::sub-page:horizontal {{
    background: #17202a;
    border-radius: 8px;
}}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {PALETTE['surface']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #0078d4;
    selection-color: #ffffff;
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    background-color: {PALETTE['surface_hover']};
    border-color: {PALETTE['border_strong']};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background-color: {PALETTE['surface']};
    border-left: 1px solid {PALETTE['border_soft']};
    width: 16px;
}}
QCheckBox {{
    color: {PALETTE['text']};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid {PALETTE['border_strong']};
    background-color: {PALETTE['surface']};
}}
QCheckBox::indicator:hover {{
    border-color: {PALETTE['accent_hover']};
}}
QCheckBox::indicator:checked {{
    background-color: {PALETTE['accent']};
    border-color: {PALETTE['accent_hover']};
    image: url("{(ASSETS_DIR / 'check.svg').as_posix()}");
}}
QComboBox::drop-down {{
    width: 22px;
    border-left: 1px solid {PALETTE['border_soft']};
}}
QComboBox::down-arrow {{
    image: url("{(ASSETS_DIR / 'combo_down.svg').as_posix()}");
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background-color: {PALETTE['panel']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    selection-background-color: {PALETTE['accent_dim']};
    selection-color: {PALETTE['accent_text']};
    outline: 0;
}}
QComboBox QAbstractItemView QScrollBar:vertical {{
    background-color: {PALETTE['timeline_bg']};
    width: 10px;
    margin: 0;
    border: none;
}}
QComboBox QAbstractItemView QScrollBar::handle:vertical {{
    background-color: {PALETTE['purple_hover']};
    min-height: 24px;
    border-radius: 5px;
}}
QComboBox QAbstractItemView QScrollBar::handle:vertical:hover {{
    background-color: #7b4fc5;
}}
QComboBox QAbstractItemView QScrollBar::add-line:vertical,
QComboBox QAbstractItemView QScrollBar::sub-line:vertical {{
    height: 0;
    background: transparent;
    border: none;
}}
QComboBox QAbstractItemView QScrollBar::add-page:vertical,
QComboBox QAbstractItemView QScrollBar::sub-page:vertical {{
    background-color: {PALETTE['timeline_bg']};
}}

QFrame#zoomPercentBox {{
    background-color: {PALETTE['surface']};
    border: 1px solid {PALETTE['border']};
    border-radius: 6px;
}}
QFrame#zoomPercentBox:hover {{
    background-color: {PALETTE['surface_hover']};
    border-color: {PALETTE['border_strong']};
}}
QComboBox#zoomPercentCombo {{
    background-color: transparent;
    border: none;
    padding: 5px 24px 5px 8px;
    min-height: 22px;
    selection-background-color: #0078d4;
    selection-color: #ffffff;
}}
QComboBox#zoomPercentCombo:hover {{
    background-color: transparent;
    border: none;
}}
QComboBox#zoomPercentCombo::drop-down {{
    width: 22px;
    border-left: 1px solid {PALETTE['border_soft']};
}}
QComboBox#speedValueCombo {{
    background-color: {PALETTE['surface']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border_strong']};
    border-radius: 6px;
    padding: 5px 24px 5px 8px;
    min-height: 22px;
    selection-background-color: #0078d4;
    selection-color: #ffffff;
}}
QComboBox#speedValueCombo:hover {{
    background-color: {PALETTE['surface_hover']};
    border-color: {PALETTE['accent_hover']};
}}
QComboBox#speedValueCombo::drop-down {{
    width: 22px;
    border-left: 1px solid {PALETTE['border_soft']};
}}

QListWidget {{
    background-color: {PALETTE['timeline_bg']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    border-radius: 6px;
    outline: none;
    padding: 4px;
    font-family: "Consolas", "Cascadia Code", monospace;
    font-size: 11px;
}}
QListWidget::item {{ padding: 6px 8px; border-radius: 4px; }}
QListWidget::item:selected {{
    background-color: {PALETTE['accent_dim']};
    color: {PALETTE['accent_text']};
}}
QListWidget::item:hover {{ background-color: {PALETTE['surface']}; }}

QToolTip {{
    background-color: {PALETTE['panel']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    padding: 6px 8px;
    border-radius: 4px;
    font-size: 11px;
}}

QMessageBox {{ background-color: {PALETTE['panel']}; color: {PALETTE['text']}; }}
QMessageBox QLabel {{ color: {PALETTE['text']}; }}
QMessageBox QPushButton {{ min-width: 80px; }}

QMenu {{
    background-color: {PALETTE['panel']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    padding: 4px;
}}
QMenu::item {{ padding: 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {PALETTE['accent_dim']}; color: {PALETTE['accent_text']}; }}
QMenu::separator {{ height: 1px; background: {PALETTE['border']}; margin: 4px 6px; }}

QScrollBar:vertical, QScrollBar:horizontal {{ background: {PALETTE['panel']}; border: none; }}
QScrollBar::handle {{
    background: {PALETTE['surface']};
    border: 1px solid {PALETTE['border']};
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::handle:hover {{ background: {PALETTE['surface_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ background: transparent; border: none; }}
"""


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


def seconds_to_hmsf(seconds: float, fps: float) -> str:
    if seconds is None or seconds < 0:
        seconds = 0.0
    if fps <= 0:
        fps = 25.0
    fps_int = max(1, round(fps))
    total_frames = int(round(float(seconds) * fps))
    frame = total_frames % fps_int
    whole_seconds = total_frames // fps_int
    hours, rem = divmod(whole_seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frame:02d}"


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


def _import_qt():
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
    return QtCore, QtGui, QtWidgets, None


def _import_qt_multimedia():
    from PySide6 import QtMultimedia  # type: ignore
    return QtMultimedia


def _icon_loader(window, qstyle):
    """Return helper(name, sp_fallback) -> QIcon that prefers local PNG/SVG/ICO assets
    and falls back to a built-in QStyle.StandardPixmap when not found."""
    from PySide6.QtGui import QIcon, QPixmap  # type: ignore

    cache: dict[tuple[str | None, Any], QIcon] = {}

    def load(name: str | None, sp_fallback=None):
        key = (name, sp_fallback)
        if key in cache:
            return cache[key]
        if name:
            suffixes = (".ico", ".png", ".svg") if name == "ffmwiz_app" else (".png", ".ico", ".svg")
            for suffix in suffixes:
                path = ASSETS_DIR / f"{name}{suffix}"
                if path.exists():
                    icon = QIcon(str(path))
                    if suffix == ".png":
                        pix = QPixmap(str(path))
                        if not pix.isNull():
                            icon.addPixmap(pix, QIcon.Normal)
                            icon.addPixmap(pix, QIcon.Disabled)
                    if not icon.isNull():
                        cache[key] = icon
                        return icon
                    _gui_log_debug(f"Icon asset exists but did not load: {path}", force=(name == "ffmwiz_app"))
            if sp_fallback is None:
                _gui_log_debug(f"Icon asset not found for name={name}", force=(name == "ffmwiz_app"))
        if sp_fallback is not None:
            try:
                icon = qstyle.standardIcon(sp_fallback)
                cache[key] = icon
                return icon
            except Exception as exc:
                _gui_log_debug(f"Standard icon fallback failed for {name}: {exc}", force=True)
        icon = QIcon()
        cache[key] = icon
        return icon

    return load


def _set_qt_application_icon(app) -> None:
    try:
        icon = _qt_app_icon()
        if icon is None or icon.isNull():
            _gui_log_debug("No ffmwiz_app icon asset was found.", force=True)
            return
        app.setApplicationName("FFmWiz")
        app.setWindowIcon(icon)
        _gui_log_debug("Set QApplication icon from bundled ffmwiz_app assets", force=True)
    except Exception as exc:
        _gui_log_debug(f"Could not set QApplication icon: {exc}", force=True)


def _apply_window_icon(window, icon_loader) -> None:
    try:
        icon = _qt_app_icon()
        if icon is None or icon.isNull():
            icon = icon_loader("ffmwiz_app", None)
        if icon is None or icon.isNull():
            _gui_log_debug(f"Window icon is null for {window.windowTitle()}", force=True)
            return
        window.setWindowIcon(icon)
        _apply_native_windows_icon(window)
        _gui_log_debug(f"Set window icon for {window.windowTitle()}", force=True)
    except Exception as exc:
        _gui_log_debug(f"Could not set window icon for {window.windowTitle()}: {exc}", force=True)


def _qt_app_icon():
    global _APP_QICON_CACHE
    if _APP_QICON_CACHE is not None:
        return _APP_QICON_CACHE
    try:
        from PySide6.QtGui import QIcon, QPixmap  # type: ignore
        icon = QIcon()
        loaded: list[str] = []
        for suffix in (".ico", ".png", ".svg"):
            path = ASSETS_DIR / f"ffmwiz_app{suffix}"
            if not path.exists():
                continue
            if suffix == ".png":
                pix = QPixmap(str(path))
                if not pix.isNull():
                    icon.addPixmap(pix, QIcon.Normal)
                    icon.addPixmap(pix, QIcon.Disabled)
                    loaded.append(str(path))
            else:
                part = QIcon(str(path))
                if not part.isNull():
                    icon.addFile(str(path))
                    loaded.append(str(path))
        if not icon.isNull():
            _APP_QICON_CACHE = icon
            _gui_log_debug("Loaded app icon assets: " + ", ".join(loaded), force=True)
            return icon
    except Exception as exc:
        _gui_log_debug(f"Could not build QApplication icon: {exc}", force=True)
    return None


# =====================================================================
# Cut Editor window
# =====================================================================


def build_cut_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, QtMultimedia = _import_qt()

    Qt = QtCore.Qt
    Signal = QtCore.Signal
    QUrl = QtCore.QUrl
    QPointF = QtCore.QPointF
    QRectF = QtCore.QRectF

    QColor = QtGui.QColor
    QFont = QtGui.QFont
    QPainter = QtGui.QPainter
    QPen = QtGui.QPen
    QBrush = QtGui.QBrush
    QPolygonF = QtGui.QPolygonF
    QPixmap = QtGui.QPixmap
    QKeySequence = QtGui.QKeySequence
    QShortcut = QtGui.QShortcut
    QAction = QtGui.QAction

    QApplication = QtWidgets.QApplication
    QStyle = QtWidgets.QStyle
    QMainWindow = QtWidgets.QMainWindow
    QWidget = QtWidgets.QWidget
    QFrame = QtWidgets.QFrame
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QSlider = QtWidgets.QSlider
    QScrollBar = QtWidgets.QScrollBar
    QListWidget = QtWidgets.QListWidget
    QListWidgetItem = QtWidgets.QListWidgetItem
    QHBoxLayout = QtWidgets.QHBoxLayout
    QVBoxLayout = QtWidgets.QVBoxLayout
    QSizePolicy = QtWidgets.QSizePolicy
    QMessageBox = QtWidgets.QMessageBox
    QMenu = QtWidgets.QMenu
    QGraphicsOpacityEffect = QtWidgets.QGraphicsOpacityEffect

    class HeaderBand(QFrame):
        def __init__(self, file_name, fps, duration, btn_undo, btn_redo):
            super().__init__()
            self.setObjectName("header")
            self.setFrameShape(QFrame.NoFrame)
            lay = QHBoxLayout(self)
            lay.setContentsMargins(14, 10, 14, 10)
            lay.setSpacing(10)
            title = QLabel("FFmWiz Cut Editor")
            title.setObjectName("title")
            lay.addWidget(title)
            lay.addStretch(1)
            lay.addWidget(btn_undo)
            lay.addWidget(btn_redo)
            lay.addStretch(1)
            info = QLabel(
                f"FPS  {fps:.3f}      •      Duration  {seconds_to_timecode(duration)}"
                f"      •      Source  {file_name}"
            )
            info.setObjectName("headerInfo")
            lay.addWidget(info)

    class StatusStrip(QFrame):
        def __init__(self, fps, duration):
            super().__init__()
            self.setObjectName("statusBand")
            self._fps = fps
            self._duration = duration
            self._label = QLabel("")
            self._label.setObjectName("status")
            lay = QHBoxLayout(self)
            lay.setContentsMargins(12, 6, 12, 6)
            lay.addWidget(self._label)
            self.update_status(0.0, None, None, [], 1.0)

        def update_status(self, now, in_m, out_m, cuts, zoom_ratio):
            kept = self._duration - sum(max(0.0, e - s) for s, e in cuts)
            zoom_pct = int(round(100.0 / max(0.001, zoom_ratio)))
            in_text = seconds_to_hmsf(in_m, self._fps) if in_m is not None else "—"
            out_text = seconds_to_hmsf(out_m, self._fps) if out_m is not None else "—"
            self._label.setText(
                f"●  Now {seconds_to_hmsf(now, self._fps)}    "
                f"{seconds_to_timecode(now)} / {seconds_to_timecode(self._duration)}"
                f"      ●  In {in_text}      ●  Out {out_text}"
                f"      ●  Kept {seconds_to_timecode(max(0.0, kept))}"
                f"      ●  Cuts {len(cuts)}"
                f"      ●  Zoom {zoom_pct}%"
            )

    class VideoPreview(QLabel):
        clicked = Signal()

        def __init__(self):
            super().__init__()
            self.setAlignment(Qt.AlignCenter)
            self.setMinimumSize(480, 270)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setStyleSheet(
                f"background-color: {PALETTE['timeline_bg']};"
                f"border: 1px solid {PALETTE['border']}; border-radius: 8px;"
            )
            self._press_pos = None
            self._dragged = False
            self._latest = None

        def on_frame(self, frame):
            if frame is None or not frame.isValid():
                return
            image = frame.toImage()
            if image.isNull():
                return
            self._latest = QPixmap.fromImage(image)
            self._render()

        def _render(self):
            if self._latest is None:
                return
            self.setPixmap(self._latest.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation,
            ))

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self._render()

        def mousePressEvent(self, event):
            self._press_pos = event.position()
            self._dragged = False
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event):
            if self._press_pos is not None:
                d = event.position() - self._press_pos
                if abs(d.x()) + abs(d.y()) > 6:
                    self._dragged = True
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event):
            if self._press_pos is not None and not self._dragged \
               and event.button() == Qt.LeftButton:
                self.clicked.emit()
            self._press_pos = None
            super().mouseReleaseEvent(event)

    @dataclass
    class TimelineState:
        duration: float = 0.0
        fps: float = 25.0
        playhead: float = 0.0
        markers: list = field(default_factory=list)
        cuts: list = field(default_factory=list)
        selected_marker_ids: set = field(default_factory=set)
        selected_cut: int = -1
        view_start: float = 0.0
        view_span: float = 0.0

    class TimelineWidget(QWidget):
        playhead_seek_requested = Signal(float)
        marker_drag_started = Signal()
        marker_drag_finished = Signal()
        marker_drag_moved = Signal(int, float)
        marker_selection_requested = Signal(int, bool)
        cut_selected = Signal(int)
        cut_context_requested = Signal(int, QtCore.QPoint)
        view_changed = Signal()

        PAD = 16
        TRACK_TOP = 40
        BOTTOM_PAD = 16
        MARKER_HIT_PX = 14

        def __init__(self, fps, duration):
            super().__init__()
            self.state = TimelineState(
                duration=duration, fps=fps,
                view_start=0.0, view_span=max(0.001, duration),
            )
            self.setMinimumHeight(120)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.setMouseTracking(True)
            self.setStyleSheet(
                f"background-color: {PALETTE['timeline_bg']};"
                f"border: 1px solid {PALETTE['border']}; border-radius: 8px;"
            )
            self._drag_target = None
            self._drag_in_progress = False
            self._cti_zoom_active = False

        def set_markers(self, markers, selected_ids):
            self.state.markers = list(markers)
            if isinstance(selected_ids, int):
                selected = set() if selected_ids < 0 else {selected_ids}
            else:
                selected = {int(x) for x in (selected_ids or [])}
            self.state.selected_marker_ids = selected
            self.state.cuts = compute_cut_ranges(self.state.markers, self.state.duration)
            self.update()

        def set_selected_cut(self, idx):
            self.state.selected_cut = idx
            self.update()

        def _clamp_view(self):
            duration = max(0.001, self.state.duration)
            self.state.view_span = max(0.05, min(duration, self.state.view_span or duration))
            self.state.view_start = max(0.0, min(max(0.0, duration - self.state.view_span), self.state.view_start))

        def _ensure_time_visible(self, t, force=False):
            self._clamp_view()
            span = self.state.view_span
            start = self.state.view_start
            end = start + span
            margin = span * (0.18 if not force else 0.30)
            if force or t < start + margin:
                self.state.view_start = max(0.0, t - span * 0.30)
            elif t > end - margin:
                self.state.view_start = min(max(0.0, self.state.duration - span), t - span * 0.70)
            self._clamp_view()

        def set_playhead(self, t, follow=True, force_visible=False):
            old_start = self.state.view_start
            old_span = self.state.view_span
            self.state.playhead = max(0.0, min(self.state.duration, float(t)))
            if follow:
                self._ensure_time_visible(self.state.playhead, force=force_visible)
            self.update()
            if abs(self.state.view_start - old_start) > 1e-6 or abs(self.state.view_span - old_span) > 1e-6:
                self.view_changed.emit()

        def fit_view(self):
            self.state.view_start = 0.0
            self.state.view_span = max(0.001, self.state.duration)
            self.update()
            self.view_changed.emit()

        def zoom_ratio(self):
            return max(1.0, self.state.duration / max(0.001, self.state.view_span))

        def set_zoom_ratio(self, ratio, focus_time=None):
            duration = max(0.001, self.state.duration)
            ratio = max(1.0, min(64.0, float(ratio)))
            focus = self.state.playhead if focus_time is None else max(0.0, min(duration, float(focus_time)))
            span = max(0.05, min(duration, duration / ratio))
            old_span = max(0.001, self.state.view_span)
            old_ratio = (focus - self.state.view_start) / old_span
            self.state.view_span = span
            self.state.view_start = focus - old_ratio * span
            self._ensure_time_visible(self.state.playhead)
            self.update()
            self.view_changed.emit()

        def zoom_around(self, focus_time, factor):
            span = max(0.001, self.state.view_span)
            duration = max(0.001, self.state.duration)
            ratio = (focus_time - self.state.view_start) / span
            new_span = max(0.05, min(duration, span * factor))
            self.state.view_span = new_span
            self.state.view_start = focus_time - ratio * new_span
            self._clamp_view()
            self.update()
            self.view_changed.emit()

        def zoom_in_at_playhead(self):
            self.zoom_around(self.state.playhead, 0.86)

        def zoom_out_at_playhead(self):
            self.zoom_around(self.state.playhead, 1.16)

        def scroll_view(self, seconds):
            self._clamp_view()
            duration = max(0.001, self.state.duration)
            self.state.view_start = max(
                0.0,
                min(max(0.0, duration - self.state.view_span),
                    self.state.view_start + float(seconds)),
            )
            self.update()
            self.view_changed.emit()

        def view_position_ratio(self):
            self._clamp_view()
            max_start = max(0.0, self.state.duration - self.state.view_span)
            if max_start <= 1e-6:
                return 0.0
            return max(0.0, min(1.0, self.state.view_start / max_start))

        def set_view_position_ratio(self, ratio):
            self._clamp_view()
            max_start = max(0.0, self.state.duration - self.state.view_span)
            self.state.view_start = max_start * max(0.0, min(1.0, float(ratio)))
            self._clamp_view()
            self.update()
            self.view_changed.emit()

        def set_view_start(self, start):
            old_start = self.state.view_start
            self.state.view_start = float(start)
            self._clamp_view()
            self.update()
            if abs(self.state.view_start - old_start) > 1e-6:
                self.view_changed.emit()

        def set_view_span_around(self, focus_time, target_span, focus_ratio=None):
            duration = max(0.001, self.state.duration)
            old_span = max(0.001, self.state.view_span)
            if focus_ratio is None:
                focus_ratio = (focus_time - self.state.view_start) / old_span
            focus_ratio = max(0.0, min(1.0, float(focus_ratio)))
            self.state.view_span = max(0.05, min(duration, float(target_span)))
            self.state.view_start = focus_time - focus_ratio * self.state.view_span
            self._clamp_view()
            self.update()
            self.view_changed.emit()

        def _usable_width(self):
            return max(1, self.width() - self.PAD * 2)

        def _time_to_x(self, t):
            return self.PAD + ((t - self.state.view_start)
                               / max(0.001, self.state.view_span)) * self._usable_width()

        def _x_to_time(self, x):
            ratio = max(0.0, min(1.0, (x - self.PAD) / self._usable_width()))
            return self.state.view_start + ratio * self.state.view_span

        def paintEvent(self, _event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.fillRect(self.rect(), QColor(PALETTE["timeline_bg"]))

            w = self.width()
            h = self.height()
            track_top = self.TRACK_TOP
            track_bottom = h - self.BOTTOM_PAD

            painter.setPen(QPen(QColor(PALETTE["border"]), 1))
            painter.setBrush(QBrush(QColor(PALETTE["timeline_track"])))
            painter.drawRoundedRect(
                QRectF(self.PAD - 2, track_top,
                       w - (self.PAD - 2) * 2, track_bottom - track_top), 4, 4,
            )

            span = max(0.001, self.state.view_span)
            approx_step = span / 8.0
            exponent = math.floor(math.log10(max(approx_step, 0.001)))
            base = 10 ** exponent
            step = base
            for candidate in (1, 2, 5, 10):
                step = candidate * base
                if span / step <= 10:
                    break
            start_t = self.state.view_start
            end_t = start_t + span
            painter.setFont(QFont("Segoe UI Semibold", 9))
            tick_pen = QPen(QColor(PALETTE["tick_hi"]), 1)
            t = math.ceil(start_t / step) * step
            while t <= end_t + 1e-6:
                x = self._time_to_x(t)
                if self.PAD <= x <= w - self.PAD:
                    painter.setPen(tick_pen)
                    painter.drawLine(QPointF(x, track_top - 6), QPointF(x, track_top))
                    label = seconds_to_timecode(t)
                    label_w = max(110, painter.fontMetrics().horizontalAdvance(label) + 10)
                    label_w = min(label_w, max(1, w - self.PAD * 2))
                    label_left = max(
                        self.PAD,
                        min(x - label_w / 2.0, w - self.PAD - label_w),
                    )
                    painter.drawText(QRectF(label_left, 6, label_w, 20),
                                     Qt.AlignCenter, label)
                t += step
            minor_step = step / 5.0 if step > 0 else 0.0
            if minor_step > 0:
                t = math.ceil(start_t / minor_step) * minor_step
                while t <= end_t + 1e-6:
                    x = self._time_to_x(t)
                    if self.PAD <= x <= w - self.PAD:
                        painter.setPen(QPen(QColor(PALETTE["tick_lo"]), 1))
                        painter.drawLine(QPointF(x, track_top - 3), QPointF(x, track_top))
                    t += minor_step

            for idx, (cs, ce) in enumerate(self.state.cuts):
                if ce < start_t or cs > end_t:
                    continue
                x1 = self._time_to_x(max(cs, start_t))
                x2 = self._time_to_x(min(ce, end_t))
                selected = idx == self.state.selected_cut
                color = QColor(PALETTE["cut_red"] if selected else PALETTE["cut_red_dim"])
                painter.setPen(QPen(QColor(PALETTE["border"]), 1 if not selected else 2))
                painter.setBrush(QBrush(color))
                painter.drawRoundedRect(
                    QRectF(x1, track_top + 3, x2 - x1, track_bottom - track_top - 6), 3, 3,
                )
                if x2 - x1 > 36:
                    painter.setPen(QPen(QColor(PALETTE["text"]), 1))
                    painter.setFont(QFont("Segoe UI Semibold", 10))
                    painter.drawText(
                        QRectF(x1, track_top, x2 - x1, track_bottom - track_top),
                        Qt.AlignCenter, f"#{idx + 1}",
                    )

            for marker in sorted(self.state.markers, key=lambda m: m.time):
                self._draw_marker(painter, marker, track_top, track_bottom)

            ph_x = self._time_to_x(self.state.playhead)
            if self.PAD - 4 <= ph_x <= w - self.PAD + 4:
                painter.setPen(QPen(QColor(PALETTE["playhead"]), 2))
                painter.drawLine(QPointF(ph_x, track_top - 10),
                                 QPointF(ph_x, track_bottom + 10))
                painter.setBrush(QBrush(QColor(PALETTE["playhead"])))
                painter.setPen(QPen(QColor(PALETTE["bg"]), 1))
                painter.drawPolygon(QPolygonF([
                    QPointF(ph_x - 7, track_bottom + 4),
                    QPointF(ph_x + 7, track_bottom + 4),
                    QPointF(ph_x, track_bottom + 14),
                ]))

        def _draw_marker(self, painter, marker, track_top, track_bottom):
            x = self._time_to_x(marker.time)
            w = self.width()
            if not (self.PAD - 10 <= x <= w - self.PAD + 10):
                return
            color_hex = PALETTE["marker_in"] if marker.kind == "in" else PALETTE["marker_out"]
            selected = marker.id in self.state.selected_marker_ids
            painter.setPen(QPen(QColor(color_hex), 3 if not selected else 5))
            painter.drawLine(QPointF(x, track_top - 4), QPointF(x, track_bottom + 4))
            painter.setBrush(QBrush(QColor(color_hex)))
            border = QColor(PALETTE["accent_text"] if selected else PALETTE["bg"])
            painter.setPen(QPen(border, 3 if selected else 1))
            painter.drawPolygon(QPolygonF([
                QPointF(x, track_top - 4),
                QPointF(x - 10, track_top - 22),
                QPointF(x + 10, track_top - 22),
            ]))
            painter.setPen(QPen(QColor(color_hex), 1))
            painter.setFont(QFont("Segoe UI Semibold", 9))
            label = "IN" if marker.kind == "in" else "OUT"
            painter.drawText(QPointF(x + 12 if marker.kind == "in" else x - 38,
                                     track_top - 8), label)

        def _hit_marker(self, x, y):
            if not (self.TRACK_TOP - 26 <= y <= self.TRACK_TOP + 6):
                return None
            best = None
            best_dx = 1e9
            for marker in self.state.markers:
                mx = self._time_to_x(marker.time)
                dx = abs(x - mx)
                if dx <= self.MARKER_HIT_PX and dx < best_dx:
                    best = marker
                    best_dx = dx
            return best

        def _hit_cut(self, x, y):
            track_top = self.TRACK_TOP + 2
            track_bottom = self.height() - self.BOTTOM_PAD - 2
            if not (track_top <= y <= track_bottom):
                return -1
            start_t = self.state.view_start
            end_t = start_t + self.state.view_span
            for idx, (cs, ce) in enumerate(self.state.cuts):
                if ce < start_t or cs > end_t:
                    continue
                x1 = self._time_to_x(max(cs, start_t))
                x2 = self._time_to_x(min(ce, end_t))
                if x1 - 2 <= x <= x2 + 2:
                    return idx
            return -1

        def _hit_playhead(self, x, y):
            ph_x = self._time_to_x(self.state.playhead)
            return (
                abs(x - ph_x) <= 12
                and self.TRACK_TOP - 16 <= y <= self.height() - self.BOTTOM_PAD + 18
            )

        def mousePressEvent(self, event):
            pos = event.position()
            x, y = pos.x(), pos.y()
            ctrl = bool(event.modifiers() & Qt.ControlModifier)

            if event.button() == Qt.RightButton:
                cut_idx = self._hit_cut(x, y)
                if cut_idx >= 0:
                    self.cut_selected.emit(cut_idx)
                    self.cut_context_requested.emit(cut_idx, event.globalPosition().toPoint())
                    return
                marker = self._hit_marker(x, y)
                if marker is not None:
                    self.marker_selection_requested.emit(marker.id, False)
                return

            if event.button() != Qt.LeftButton:
                super().mousePressEvent(event)
                return

            marker = self._hit_marker(x, y)
            if marker is not None:
                self.marker_selection_requested.emit(marker.id, ctrl)
                self._drag_target = ("marker", marker.id)
                self._drag_in_progress = False
                return

            if self._hit_playhead(x, y):
                self._drag_target = (
                    "cti",
                    QPointF(x, y),
                    self.state.playhead,
                    self.state.view_start,
                    max(0.001, self.state.view_span),
                )
                self._drag_in_progress = False
                self._cti_zoom_active = False
                self.marker_selection_requested.emit(-1, False)
                self.cut_selected.emit(-1)
                return

            t = self._x_to_time(x)
            self.set_playhead(t)
            self.playhead_seek_requested.emit(self.state.playhead)
            self._drag_target = (
                "playhead",
                QPointF(x, y),
                self.state.view_start,
                max(0.001, self.state.view_span),
            )
            self._drag_in_progress = False
            self._cti_zoom_active = False
            self.marker_selection_requested.emit(-1, False)
            self.cut_selected.emit(-1)

        def mouseMoveEvent(self, event):
            if self._drag_target is None:
                return
            t = self._x_to_time(event.position().x())
            kind = self._drag_target[0]
            if kind == "playhead":
                press_pos = self._drag_target[1] if len(self._drag_target) > 1 else event.position()
                start_span = self._drag_target[3] if len(self._drag_target) > 3 else max(0.001, self.state.view_span)
                dy = event.position().y() - press_pos.y()
                if self._cti_zoom_active or abs(dy) >= 4:
                    self._cti_zoom_active = True
                    current_focus = max(0.0, min(self.state.duration, t))
                    focus_ratio = max(0.0, min(1.0, (event.position().x() - self.PAD) / self._usable_width()))
                    target_span = start_span * (2.0 ** (dy / 150.0))
                    self.set_playhead(current_focus, follow=False)
                    self.playhead_seek_requested.emit(self.state.playhead)
                    self.set_view_span_around(current_focus, target_span, focus_ratio)
                    self._drag_in_progress = True
                else:
                    self.set_playhead(t)
                    self.playhead_seek_requested.emit(self.state.playhead)
            elif kind == "cti":
                press_pos = self._drag_target[1]
                start_span = self._drag_target[4]
                dy = event.position().y() - press_pos.y()
                if self._cti_zoom_active or abs(dy) >= 4:
                    self._cti_zoom_active = True
                    current_focus = max(0.0, min(self.state.duration, t))
                    focus_ratio = max(0.0, min(1.0, (event.position().x() - self.PAD) / self._usable_width()))
                    target_span = start_span * (2.0 ** (dy / 150.0))
                    self.set_playhead(current_focus, follow=False)
                    self.playhead_seek_requested.emit(self.state.playhead)
                    self.set_view_span_around(current_focus, target_span, focus_ratio)
                    self._drag_in_progress = True
                else:
                    self.set_playhead(t)
                    self.playhead_seek_requested.emit(self.state.playhead)
            elif kind == "marker":
                if not self._drag_in_progress:
                    self.marker_drag_started.emit()
                    self._drag_in_progress = True
                marker_id = self._drag_target[1]
                self.marker_drag_moved.emit(marker_id, max(0.0, min(self.state.duration, t)))

        def mouseReleaseEvent(self, event):
            if (self._drag_target is not None
                    and self._drag_target[0] == "marker"
                    and self._drag_in_progress):
                self.marker_drag_finished.emit()
            self._drag_target = None
            self._drag_in_progress = False
            self._cti_zoom_active = False

        def wheelEvent(self, event):
            delta = event.angleDelta().y()
            if delta == 0:
                return
            if event.modifiers() & Qt.ControlModifier:
                factor_base = 1.25
                factor = 1.0 / factor_base if delta > 0 else factor_base
                self.zoom_around(self._x_to_time(event.position().x()), factor)
                return
            step = 10.0 if (event.modifiers() & Qt.ShiftModifier) else 5.0
            direction = 1.0 if delta > 0 else -1.0
            self.playhead_seek_requested.emit(max(0.0, min(self.state.duration, self.state.playhead + direction * step)))
    class CutEditorWindow(QMainWindow):
        def __init__(self, request):
            init_start = time.perf_counter()
            super().__init__()
            self.request = request
            self.input_path = Path(request["input_path"])
            requested_fps = request.get("fps")
            self.fps = float(requested_fps or 25.0)
            if not requested_fps:
                print("Cut Editor FPS fallback: using 25.000 fps.", file=sys.stderr)
            self.duration = float(request.get("duration") or 0.0)
            self._log_path = Path(request["log_path"]) if request.get("log_path") else None
            self.result = {"status": "canceled", "keep_ranges": []}

            # Marker model state. Cut ranges are derived from the markers.
            self._next_marker_id = 1
            self._markers: list = []
            self._selected_marker_ids: set[int] = set()
            self._selected_cut = -1
            self._drag_snapshot = None
            self._syncing_timeline_zoom = False
            self._syncing_timeline_navigation = False
            self._media_ready = False

            self._history = HistoryStack(self._take_snapshot(), max_size=100)

            self.setWindowTitle("FFmWiz Cut Editor")
            self._icon = _icon_loader(self, self.style())
            _apply_window_icon(self, self._icon)
            self.setMinimumSize(1180, 860)
            self.resize(1360, 900)

            self._build_ui()
            self._install_shortcuts()
            self._refresh_all()
            _gui_log_debug(
                f"Cut GUI widgets initialized in {time.perf_counter() - init_start:.3f}s",
                force=True,
            )
            QtCore.QTimer.singleShot(150, self._deferred_media_startup)

        def _deferred_media_startup(self):
            start = time.perf_counter()
            self._setup_player()
            self._media_ready = True
            self._refresh_all()
            _gui_log_debug(
                f"Cut GUI media startup completed in {time.perf_counter() - start:.3f}s",
                force=True,
            )

        def _take_snapshot(self):
            return CutSnapshot(
                markers=[copy.copy(m) for m in self._markers],
                selected_marker_ids=tuple(sorted(self._selected_marker_ids)),
            )

        def _commit_history(self):
            self._history.push(self._take_snapshot())
            self._update_undo_redo_state()

        def _restore_snapshot(self, snap):
            self._markers = [copy.copy(m) for m in snap.markers]
            self._selected_marker_ids = set(getattr(snap, "selected_marker_ids", ()))
            self._selected_cut = -1
            self._refresh_all_no_history()

        def _undo(self):
            snap = self._history.undo()
            if snap is not None:
                self._restore_snapshot(snap)
                self._update_undo_redo_state()

        def _redo(self):
            snap = self._history.redo()
            if snap is not None:
                self._restore_snapshot(snap)
                self._update_undo_redo_state()

        def _update_undo_redo_state(self):
            self.btn_undo.setEnabled(self._history.can_undo())
            self.btn_redo.setEnabled(self._history.can_redo())

        def _apply_disabled_opacity(self, button):
            if button.isEnabled():
                button.setGraphicsEffect(None)
                return
            effect = button.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                effect = QGraphicsOpacityEffect(button)
                button.setGraphicsEffect(effect)
            effect.setOpacity(0.32)

        def _selected_marker(self):
            if len(self._selected_marker_ids) != 1:
                return None
            marker_id = next(iter(self._selected_marker_ids))
            for m in self._markers:
                if m.id == marker_id:
                    return m
            return None

        def _set_selected_marker(self, marker_id, additive=False):
            if marker_id < 0:
                self._selected_marker_ids.clear()
            elif additive:
                if marker_id in self._selected_marker_ids:
                    self._selected_marker_ids.remove(marker_id)
                else:
                    self._selected_marker_ids.add(marker_id)
            else:
                self._selected_marker_ids = {marker_id}
            self._selected_cut = -1
            self._refresh_all_no_history()

        def _clear_marker_selection(self):
            self._selected_marker_ids.clear()

        def _add_marker(self, time, kind):
            m = Marker(id=self._next_marker_id, time=time, kind=kind)
            self._next_marker_id += 1
            self._markers.append(m)
            return m

        def _replace_cut_ranges_with_markers(self, ranges):
            self._markers = []
            for start, end in normalize_ranges(ranges, self.duration):
                self._add_marker(start, "in")
                self._add_marker(end, "out")

        def _cuts(self):
            return compute_cut_ranges(self._markers, self.duration)

        def _log_debug(self, message):
            line = f"Cut Editor DEBUG: {message}\n"
            if self._log_path is not None:
                try:
                    with self._log_path.open("a", encoding="utf-8") as handle:
                        handle.write(line)
                    return
                except Exception:
                    pass
            print(line.rstrip(), file=sys.stderr)

        def _build_ui(self):
            layout_start = time.perf_counter()
            central = QWidget()
            central.setObjectName("central")
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(14, 10, 14, 10)
            root.setSpacing(8)

            self.btn_undo = QPushButton(self._icon("undo", QStyle.SP_ArrowBack), "Undo (Ctrl+Z)")
            self.btn_undo.setToolTip("Undo last GUI edit")
            self.btn_undo.clicked.connect(self._undo)
            self.btn_redo = QPushButton(self._icon("redo", QStyle.SP_ArrowForward), "Redo (Ctrl+Y)")
            self.btn_redo.setToolTip("Redo last undone GUI edit")
            self.btn_redo.clicked.connect(self._redo)
            self.btn_undo.setEnabled(False)
            self.btn_redo.setEnabled(False)

            header = HeaderBand(self.input_path.name, self.fps, self.duration,
                                self.btn_undo, self.btn_redo)
            root.addWidget(header)

            self.preview = VideoPreview()
            self.preview.clicked.connect(self.toggle_playback)
            root.addWidget(self.preview, 1)

            self.status_strip = StatusStrip(self.fps, self.duration)
            root.addWidget(self.status_strip)

            self.timeline = TimelineWidget(self.fps, self.duration)
            self.timeline.playhead_seek_requested.connect(self._on_timeline_seek)
            self.timeline.marker_selection_requested.connect(self._on_marker_selection_requested)
            self.timeline.marker_drag_started.connect(self._on_marker_drag_started)
            self.timeline.marker_drag_moved.connect(self._on_marker_drag_moved)
            self.timeline.marker_drag_finished.connect(self._on_marker_drag_finished)
            self.timeline.cut_selected.connect(self._on_cut_selected_from_timeline)
            self.timeline.cut_context_requested.connect(self._on_cut_context)
            self.timeline.view_changed.connect(self._sync_timeline_zoom_slider)
            self.timeline.view_changed.connect(self._sync_timeline_navigation_slider)
            root.addWidget(self.timeline)

            view_row = QHBoxLayout()
            view_row.setSpacing(8)
            nav_lbl = QLabel("Timeline view")
            nav_lbl.setObjectName("controlLabel")
            view_row.addWidget(nav_lbl)
            view_frame = QFrame()
            view_frame.setObjectName("timelineViewFrame")
            view_frame.setStyleSheet(f"""
                QFrame#timelineViewFrame {{
                    background: #111820;
                    border: 1px solid {PALETTE['border_strong']};
                    border-radius: 9px;
                }}
            """)
            view_frame.setMinimumWidth(780)
            view_frame.setMaximumWidth(16777215)
            view_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            view_frame_layout = QHBoxLayout(view_frame)
            view_frame_layout.setContentsMargins(3, 1, 3, 1)
            view_frame_layout.setSpacing(3)
            self.btn_timeline_view_left = QPushButton("◀")
            self.btn_timeline_view_left.setObjectName("timelineViewArrow")
            self.btn_timeline_view_left.setFixedSize(14, 14)
            self.btn_timeline_view_left.setAutoRepeat(True)
            self.btn_timeline_view_left.setAutoRepeatDelay(220)
            self.btn_timeline_view_left.setAutoRepeatInterval(70)
            self.btn_timeline_view_left.setToolTip("Move timeline view left")
            self.btn_timeline_view_left.clicked.connect(lambda: self._nudge_timeline_view(-1))
            view_frame_layout.addWidget(self.btn_timeline_view_left)
            self.timeline_navigation_slider = QScrollBar(Qt.Horizontal)
            self.timeline_navigation_slider.setObjectName("timelineViewScroll")
            self.timeline_navigation_slider.setRange(0, 0)
            self.timeline_navigation_slider.setValue(0)
            self.timeline_navigation_slider.setTracking(True)
            self.timeline_navigation_slider.setMinimumWidth(520)
            self.timeline_navigation_slider.setStyleSheet("""
                QScrollBar:horizontal {
                    background: transparent;
                    border: none;
                    border-radius: 8px;
                    height: 16px;
                    margin: 0;
                }
                QScrollBar::handle:horizontal {
                    background: #7df58a;
                    min-width: 120px;
                    border-radius: 6px;
                    margin: 2px 0;
                }
                QScrollBar::handle:horizontal:hover {
                    background: #a6ffad;
                }
                QScrollBar::add-line:horizontal,
                QScrollBar::sub-line:horizontal {
                    background: transparent;
                    border: none;
                    width: 0;
                }
                QScrollBar::add-page:horizontal,
                QScrollBar::sub-page:horizontal {
                    background: #17202a;
                    border-radius: 7px;
                }
            """)
            self.timeline_navigation_slider.setToolTip(
                "Timeline view: drag left/right to pan through the visible timeline window without moving the CTI.")
            self.timeline_navigation_slider.valueChanged.connect(self._on_timeline_navigation_slider)
            self.timeline_navigation_slider.installEventFilter(self)
            view_frame_layout.addWidget(self.timeline_navigation_slider, 1)
            self.btn_timeline_view_right = QPushButton("▶")
            self.btn_timeline_view_right.setObjectName("timelineViewArrow")
            self.btn_timeline_view_right.setFixedSize(14, 14)
            self.btn_timeline_view_right.setAutoRepeat(True)
            self.btn_timeline_view_right.setAutoRepeatDelay(220)
            self.btn_timeline_view_right.setAutoRepeatInterval(70)
            self.btn_timeline_view_right.setToolTip("Move timeline view right")
            self.btn_timeline_view_right.clicked.connect(lambda: self._nudge_timeline_view(1))
            view_frame_layout.addWidget(self.btn_timeline_view_right)
            view_row.addWidget(view_frame, 1)
            root.addLayout(view_row)

            zoom_row = QHBoxLayout()
            zoom_row.setSpacing(8)
            zoom_lbl = QLabel("Timeline zoom")
            zoom_lbl.setObjectName("controlLabel")
            zoom_row.addWidget(zoom_lbl)
            self.btn_timeline_zoom_left = QPushButton("◀")
            self.btn_timeline_zoom_left.setObjectName("timelineZoomArrow")
            self.btn_timeline_zoom_left.setFixedSize(14, 14)
            self.btn_timeline_zoom_left.setAutoRepeat(True)
            self.btn_timeline_zoom_left.setAutoRepeatDelay(220)
            self.btn_timeline_zoom_left.setAutoRepeatInterval(70)
            self.btn_timeline_zoom_left.setToolTip("Zoom timeline out")
            self.btn_timeline_zoom_left.clicked.connect(lambda: self._nudge_timeline_zoom(-1))
            zoom_row.addWidget(self.btn_timeline_zoom_left)
            self.timeline_zoom_slider = QSlider(Qt.Horizontal)
            self.timeline_zoom_slider.setObjectName("timelineZoomSlider")
            self.timeline_zoom_slider.setRange(0, 100)
            self.timeline_zoom_slider.setValue(0)
            self.timeline_zoom_slider.setTracking(True)
            self.timeline_zoom_slider.setMinimumWidth(780)
            self.timeline_zoom_slider.setMaximumWidth(16777215)
            self.timeline_zoom_slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.timeline_zoom_slider.setStyleSheet(f"""
                QSlider::groove:horizontal {{
                    background: #334155;
                    height: 6px;
                    border-radius: 3px;
                }}
                QSlider::sub-page:horizontal {{
                    background: {PALETTE['accent']};
                    border-radius: 3px;
                }}
                QSlider::handle:horizontal {{
                    background: {PALETTE['accent_hover']};
                    width: 44px;
                    height: 12px;
                    min-height: 12px;
                    max-height: 12px;
                    margin: -4px 0;
                    border-radius: 6px;
                    border: 1px solid {PALETTE['accent']};
                }}
                QSlider::handle:horizontal:hover {{
                    background: {PALETTE['text']};
                }}
            """)
            self.timeline_zoom_slider.setToolTip(
                "Timeline zoom. Mouse wheel here zooms in/out; Shift=fast, Ctrl=fine.")
            self.timeline_zoom_slider.valueChanged.connect(self._on_timeline_zoom_slider)
            self.timeline_zoom_slider.installEventFilter(self)
            zoom_row.addWidget(self.timeline_zoom_slider, 1)
            self.btn_timeline_zoom_right = QPushButton("▶")
            self.btn_timeline_zoom_right.setObjectName("timelineZoomArrow")
            self.btn_timeline_zoom_right.setFixedSize(14, 14)
            self.btn_timeline_zoom_right.setAutoRepeat(True)
            self.btn_timeline_zoom_right.setAutoRepeatDelay(220)
            self.btn_timeline_zoom_right.setAutoRepeatInterval(70)
            self.btn_timeline_zoom_right.setToolTip("Zoom timeline in")
            self.btn_timeline_zoom_right.clicked.connect(lambda: self._nudge_timeline_zoom(1))
            zoom_row.addWidget(self.btn_timeline_zoom_right)
            root.addLayout(zoom_row)

            tip = QLabel(
                "Tip: Left-click a cut region to move the playhead. "
                "Right-click a cut region to select it. "
                "Ctrl+click marker = multi-select marker. "
                "Drag CTI upward to zoom in and downward to zoom out. "
                "Selecting an In/Out marker and pressing the opposite button converts its type."
            )
            tip.setObjectName("tip")
            tip.setWordWrap(True)
            root.addWidget(tip)

            # ----- Transport row -----
            row1 = QHBoxLayout()
            row1.setSpacing(6)
            self.btn_play = QPushButton(self._icon("play", QStyle.SP_MediaPlay), " Play (Space)")
            self.btn_play.setObjectName("primary")
            self.btn_play.setToolTip("Play / Pause (Space)")
            self.btn_play.clicked.connect(self.toggle_playback)
            row1.addWidget(self.btn_play)

            row1.addWidget(self._tbtn(self._icon("skip_backward", QStyle.SP_MediaSkipBackward),
                                       "Home (Home)", "Jump to start (Home)", self.go_home))
            row1.addWidget(self._tbtn(self._icon("seek_backward", QStyle.SP_MediaSeekBackward),
                                       "-5s (Shift+Left)", "Seek -5s (Shift+Left)",
                                       lambda: self._seek_relative(-5.0)))
            row1.addWidget(self._tbtn(None, "-1s (Left)", "Seek -1s (Left)",
                                       lambda: self._seek_relative(-1.0)))
            row1.addWidget(self._tbtn(None, "+1s (Right)", "Seek +1s (Right)",
                                       lambda: self._seek_relative(1.0)))
            row1.addWidget(self._tbtn(self._icon("seek_forward", QStyle.SP_MediaSeekForward),
                                       "+5s (Shift+Right)", "Seek +5s (Shift+Right)",
                                       lambda: self._seek_relative(5.0)))
            row1.addWidget(self._tbtn(self._icon("skip_forward", QStyle.SP_MediaSkipForward),
                                       "End (End)", "Jump to end (End)", self.go_end))
            row1.addStretch(1)
            row1.addWidget(self._tbtn(None, "◀◀ Prev (Ctrl+Left)",
                                       "Snap CTI to previous marker (Ctrl+Left)",
                                       self.snap_marker_prev))
            row1.addWidget(self._tbtn(None, "Next (Ctrl+Right) ▶▶",
                                       "Snap CTI to next marker (Ctrl+Right)",
                                       self.snap_marker_next))
            row1.addStretch(1)
            root.addLayout(row1)

            # ----- Marker / cut row -----
            row2 = QHBoxLayout()
            row2.setSpacing(6)
            self.btn_mark_in = QPushButton(self._icon("mark_in", None), " Mark In (I)")
            self.btn_mark_in.setObjectName("markIn")
            self.btn_mark_in.setToolTip(
                "Mark In at the current CTI/playhead (I). Uses the green In-marker color. "
                "If an Out marker is selected, converts it to In.")
            self.btn_mark_in.clicked.connect(self.mark_in)
            row2.addWidget(self.btn_mark_in)

            self.btn_mark_out = QPushButton(self._icon("mark_out", None), " Mark Out (O)")
            self.btn_mark_out.setObjectName("markOut")
            self.btn_mark_out.setToolTip(
                "Mark Out at the current CTI/playhead (O). Uses the amber Out-marker color. "
                "If an In marker is selected, converts it to Out.")
            self.btn_mark_out.clicked.connect(self.mark_out)
            row2.addWidget(self.btn_mark_out)

            row2.addStretch(1)

            self.btn_add_cut = QPushButton(self._icon("add_cut", None), " Add Cut(s) (A)")
            self.btn_add_cut.setObjectName("green")
            self.btn_add_cut.setToolTip(
                "Add a complete cut at the playhead (A). "
                "Places an In marker now and a paired Out 1 second later.")
            self.btn_add_cut.clicked.connect(self.add_cuts)
            row2.addWidget(self.btn_add_cut)

            self.btn_invert_cuts = QPushButton(self._icon("invert", None), " Invert Cuts (Ctrl+Shift+I)")
            self.btn_invert_cuts.setObjectName("purple")
            self.btn_invert_cuts.setToolTip(
                "Invert cut ranges: keep the currently selected cut ranges and remove everything else. "
                "Shortcut: Ctrl+Shift+I")
            self.btn_invert_cuts.clicked.connect(self.invert_cuts)
            row2.addWidget(self.btn_invert_cuts)
            row2.addSpacing(10)
            self.btn_delete_markers = QPushButton(
                self._icon("delete", QStyle.SP_TrashIcon), " Delete Selected Marker(s) (Del)")
            self.btn_delete_markers.setObjectName("danger")
            self.btn_delete_markers.setToolTip("Delete all selected markers (Del when markers are selected)")
            self.btn_delete_markers.clicked.connect(self.delete_selected_markers)
            row2.addWidget(self.btn_delete_markers)

            self.btn_delete_selected_cut = QPushButton(
                self._icon("delete", QStyle.SP_TrashIcon), " Delete Selected Cut (Del)")
            self.btn_delete_selected_cut.setObjectName("dangerCut")
            self.btn_delete_selected_cut.setToolTip("Delete the selected cut region (Del when a cut is selected)")
            self.btn_delete_selected_cut.clicked.connect(self.delete_selected_cut)
            row2.addWidget(self.btn_delete_selected_cut)

            self.btn_delete_all = QPushButton(
                self._icon("delete_all", QStyle.SP_TrashIcon), " Delete All Markers")
            self.btn_delete_all.setObjectName("dangerAlt")
            self.btn_delete_all.setToolTip("Remove every marker (asks for confirmation)")
            self.btn_delete_all.clicked.connect(self.delete_all_markers)
            row2.addWidget(self.btn_delete_all)
            row2.addStretch(1)
            root.addLayout(row2)

            # ----- Audio / zoom / confirmation row -----
            row3 = QHBoxLayout()
            row3.setSpacing(8)
            row3.addWidget(self._tbtn(self._icon("zoom_in", None), "Zoom In (+)",
                                       "Zoom timeline in (+)",
                                       self.timeline_zoom_in))
            row3.addWidget(self._tbtn(self._icon("zoom_out", None), "Zoom Out (-)",
                                       "Zoom timeline out (-)",
                                       self.timeline_zoom_out))
            row3.addWidget(self._tbtn(None, "Reset Zoom (Ctrl+R)",
                                       "Reset zoom and view position (Ctrl+R)",
                                       self.timeline_fit))
            row3.addSpacing(12)
            self.btn_mute = QPushButton(self._icon("volume_meter_3", QStyle.SP_MediaVolume), "  Mute (M)")
            self.btn_mute.setMinimumWidth(118)
            self.btn_mute.setIconSize(QtCore.QSize(20, 20))
            self.btn_mute.setToolTip("Mute / unmute (M)")
            self.btn_mute.clicked.connect(self.toggle_mute)
            row3.addWidget(self.btn_mute)
            self.volume_slider = QSlider(Qt.Horizontal)
            self.volume_slider.setObjectName("cutVolumeSlider")
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(60)
            self.volume_slider.setFixedWidth(180)
            self.volume_slider.setStyleSheet(f"""
                QSlider::groove:horizontal {{
                    background: {PALETTE['timeline_track']};
                    height: 6px;
                    border-radius: 3px;
                }}
                QSlider::sub-page:horizontal {{
                    background: {PALETTE['accent']};
                    border-radius: 3px;
                }}
                QSlider::handle:horizontal {{
                    background: {PALETTE['accent_hover']};
                    width: 28px;
                    height: 12px;
                    min-height: 12px;
                    max-height: 12px;
                    margin: -4px 0;
                    border-radius: 6px;
                    border: 1px solid {PALETTE['accent']};
                }}
                QSlider::handle:horizontal:hover {{
                    background: {PALETTE['text']};
                }}
            """)
            self.volume_slider.setToolTip("Volume (mouse wheel works here)")
            self.volume_slider.valueChanged.connect(self._on_volume_changed)
            self.volume_slider.installEventFilter(self)
            row3.addWidget(self.volume_slider)
            self.volume_label = QLabel("60%")
            self.volume_label.setObjectName("dim")
            row3.addWidget(self.volume_label)
            row3.addStretch(1)
            self.btn_cancel = QPushButton("Cancel (Esc)")
            self.btn_cancel.setObjectName("danger")
            self.btn_cancel.clicked.connect(self.cancel)
            row3.addWidget(self.btn_cancel)
            self.btn_confirm = QPushButton(self._icon("check", QStyle.SP_DialogOkButton),
                                            " Confirm (Enter)")
            self.btn_confirm.setObjectName("primary")
            self.btn_confirm.clicked.connect(self.confirm)
            row3.addWidget(self.btn_confirm)
            root.addLayout(row3)

            # ----- Marker list -----
            header_lbl = QLabel("Cut ranges (derived from In→Out marker pairs)")
            header_lbl.setObjectName("dim")
            root.addWidget(header_lbl)
            self.cut_list = QListWidget()
            self.cut_list.setMinimumHeight(110)
            self.cut_list.itemSelectionChanged.connect(self._on_cut_list_select)
            root.addWidget(self.cut_list)
            _gui_log_debug(
                f"Cut GUI layout initialized in {time.perf_counter() - layout_start:.3f}s",
                force=True,
            )

        def _tbtn(self, icon, label, tip, slot):
            b = QPushButton(label) if icon is None else QPushButton(icon, " " + label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        def _setup_player(self):
            QtMultimedia = _import_qt_multimedia()
            self._media_player_cls = QtMultimedia.QMediaPlayer
            self.player = QtMultimedia.QMediaPlayer(self)
            self.audio = QtMultimedia.QAudioOutput(self)
            self.audio.setVolume(0.6)
            self.player.setAudioOutput(self.audio)
            self.video_sink = QtMultimedia.QVideoSink(self)
            self.player.setVideoSink(self.video_sink)
            self.video_sink.videoFrameChanged.connect(self.preview.on_frame)
            self.player.positionChanged.connect(self._on_player_position)
            self.player.errorOccurred.connect(self._on_player_error)
            self.player.setSource(QUrl.fromLocalFile(str(self.input_path)))
            self.player.pause()

        def _on_player_error(self, _err, msg):
            if msg:
                self.status_strip._label.setText(f"Player message: {msg}")

        def _install_shortcuts(self):
            QShortcut(QKeySequence("Space"), self).activated.connect(self.toggle_playback)
            QShortcut(QKeySequence("I"), self).activated.connect(self.mark_in)
            QShortcut(QKeySequence("O"), self).activated.connect(self.mark_out)
            QShortcut(QKeySequence("A"), self).activated.connect(self.add_cuts)
            QShortcut(QKeySequence("M"), self).activated.connect(self.toggle_mute)
            QShortcut(QKeySequence("F"), self).activated.connect(self.timeline_fit)
            QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.timeline_fit)
            QShortcut(QKeySequence(Qt.Key_Plus), self).activated.connect(self.timeline_zoom_in)
            QShortcut(QKeySequence(Qt.Key_Equal), self).activated.connect(self.timeline_zoom_in)
            QShortcut(QKeySequence(Qt.Key_Minus), self).activated.connect(self.timeline_zoom_out)
            QShortcut(QKeySequence(Qt.Key_Home), self).activated.connect(self.go_home)
            QShortcut(QKeySequence(Qt.Key_End), self).activated.connect(self.go_end)
            QShortcut(QKeySequence(Qt.Key_Left), self).activated.connect(lambda: self._seek_relative(-1.0))
            QShortcut(QKeySequence(Qt.Key_Right), self).activated.connect(lambda: self._seek_relative(1.0))
            QShortcut(QKeySequence("Shift+Left"), self).activated.connect(lambda: self._seek_relative(-5.0))
            QShortcut(QKeySequence("Shift+Right"), self).activated.connect(lambda: self._seek_relative(5.0))
            QShortcut(QKeySequence("Ctrl+Left"), self).activated.connect(self.snap_marker_prev)
            QShortcut(QKeySequence("Ctrl+Right"), self).activated.connect(self.snap_marker_next)
            QShortcut(QKeySequence("Delete"), self).activated.connect(self.delete_selected_item)
            QShortcut(QKeySequence("Return"), self).activated.connect(self.confirm)
            QShortcut(QKeySequence("Enter"), self).activated.connect(self.confirm)
            QShortcut(QKeySequence("Escape"), self).activated.connect(self.cancel)
            QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._undo)
            QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self._redo)
            QShortcut(QKeySequence("Ctrl+Shift+Z"), self).activated.connect(self._redo)
            QShortcut(QKeySequence("Ctrl+Shift+I"), self).activated.connect(self.invert_cuts)

        def keyPressEvent(self, event):
            vk = event.nativeVirtualKey()
            mods = event.modifiers()
            ctrl = bool(mods & Qt.ControlModifier)
            shift = bool(mods & Qt.ShiftModifier)
            if ctrl and not shift:
                if vk == WIN_VK["z"]: self._undo(); return
                if vk == WIN_VK["y"]: self._redo(); return
                if vk == WIN_VK["r"]: self.timeline_fit(); return
                if vk == WIN_VK["left"]: self.snap_marker_prev(); return
                if vk == WIN_VK["right"]: self.snap_marker_next(); return
            if ctrl and shift and vk == WIN_VK["z"]:
                self._redo(); return
            if ctrl and shift and vk == WIN_VK["i"]:
                self.invert_cuts(); return
            if not ctrl:
                if shift and vk == WIN_VK["left"]:
                    self._seek_relative(-5.0); return
                if shift and vk == WIN_VK["right"]:
                    self._seek_relative(5.0); return
                actions = {
                    WIN_VK["space"]: self.toggle_playback,
                    WIN_VK["i"]: self.mark_in,
                    WIN_VK["o"]: self.mark_out,
                    WIN_VK["a"]: self.add_cuts,
                    WIN_VK["m"]: self.toggle_mute,
                    WIN_VK["f"]: self.timeline_fit,
                    WIN_VK["plus"]: self.timeline_zoom_in,
                    WIN_VK["minus"]: self.timeline_zoom_out,
                    WIN_VK["kp_add"]: self.timeline_zoom_in,
                    WIN_VK["kp_subtract"]: self.timeline_zoom_out,
                    WIN_VK["home"]: self.go_home,
                    WIN_VK["end"]: self.go_end,
                    WIN_VK["left"]: lambda: self._seek_relative(-1.0),
                    WIN_VK["right"]: lambda: self._seek_relative(1.0),
                    WIN_VK["delete"]: self.delete_selected_item,
                    WIN_VK["return"]: self.confirm,
                    WIN_VK["escape"]: self.cancel,
                }
                fn = actions.get(vk)
                if fn is not None:
                    fn(); return
            super().keyPressEvent(event)

        def eventFilter(self, obj, event):
            volume_slider = getattr(self, "volume_slider", None)
            timeline_zoom_slider = getattr(self, "timeline_zoom_slider", None)
            timeline_navigation_slider = getattr(self, "timeline_navigation_slider", None)
            if obj is volume_slider and event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y()
                step = 3 if (event.modifiers() & Qt.ControlModifier) else 5
                new_val = volume_slider.value() + (step if delta > 0 else -step)
                volume_slider.setValue(max(0, min(100, new_val)))
                return True
            if obj is timeline_zoom_slider and event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y()
                if delta:
                    base_step = 6 if (event.modifiers() & Qt.ShiftModifier) else 1 if (event.modifiers() & Qt.ControlModifier) else 2
                    wheel_steps = max(1, abs(delta) // 120)
                    direction = 1 if delta > 0 else -1
                    timeline_zoom_slider.setValue(
                        max(0, min(100, timeline_zoom_slider.value() + direction * base_step * wheel_steps))
                    )
                    _gui_log_debug(
                        f"Cut timeline zoom bar wheel delta={delta} value={timeline_zoom_slider.value()}",
                        force=False,
                    )
                    return True
            if obj is timeline_navigation_slider and event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y()
                if delta:
                    span = max(0.001, self.timeline.state.view_span)
                    step_seconds = span * (0.30 if (event.modifiers() & Qt.ShiftModifier) else 0.03 if (event.modifiers() & Qt.ControlModifier) else 0.10)
                    base_step = max(1, int(round(step_seconds * 1000.0)))
                    wheel_steps = max(1, abs(delta) // 120)
                    direction = 1 if delta > 0 else -1
                    timeline_navigation_slider.setValue(
                        max(
                            timeline_navigation_slider.minimum(),
                            min(
                                timeline_navigation_slider.maximum(),
                                timeline_navigation_slider.value() + direction * base_step * wheel_steps,
                            ),
                        )
                    )
                    _gui_log_debug(
                        f"Cut timeline navigation bar wheel delta={delta} value={timeline_navigation_slider.value()}",
                        force=False,
                    )
                    return True
            if obj is timeline_zoom_slider and event.type() in {
                QtCore.QEvent.MouseButtonPress,
                QtCore.QEvent.MouseMove,
            }:
                if obj is None or not obj.isEnabled():
                    return True
                buttons = event.buttons() if hasattr(event, "buttons") else Qt.NoButton
                if event.type() == QtCore.QEvent.MouseButtonPress and getattr(event, "button", lambda: Qt.NoButton)() != Qt.LeftButton:
                    return False
                if event.type() == QtCore.QEvent.MouseMove and not (buttons & Qt.LeftButton):
                    return False
                x = int(max(0, min(obj.width(), event.position().x())))
                value = QStyle.sliderValueFromPosition(
                    obj.minimum(),
                    obj.maximum(),
                    x,
                    max(1, obj.width()),
                )
                obj.setValue(max(obj.minimum(), min(obj.maximum(), value)))
                event.accept()
                return True
            return super().eventFilter(obj, event)

        def _is_playing(self):
            if not hasattr(self, "player"):
                return False
            return self.player.playbackState() == self._media_player_cls.PlayingState

        def toggle_playback(self):
            if not hasattr(self, "player"):
                return
            if self._is_playing():
                self.player.pause()
                self.btn_play.setText(" Play (Space)")
                self.btn_play.setIcon(self._icon("play", QStyle.SP_MediaPlay))
            else:
                pos_ms = self.player.position()
                dur_ms = max(0, self.player.duration() or int(self.duration * 1000))
                if dur_ms > 0 and pos_ms >= dur_ms - 500:
                    self.player.setPosition(0)
                self.player.play()
                self.btn_play.setText(" Pause (Space)")
                self.btn_play.setIcon(self._icon("pause", QStyle.SP_MediaPause))

        def go_home(self):
            if hasattr(self, "player"):
                self.player.setPosition(0)
            self.timeline.set_playhead(0.0, follow=True, force_visible=True)

        def go_end(self):
            dur_ms = int(self.duration * 1000)
            if hasattr(self, "player"):
                dur_ms = max(0, self.player.duration() or dur_ms)
                self.player.setPosition(dur_ms)
            self.timeline.set_playhead(dur_ms / 1000.0, follow=True, force_visible=True)

        def _seek_relative(self, dt):
            cur = self._current_time()
            target = max(0.0, min(self.duration, cur + dt))
            if hasattr(self, "player"):
                self.player.setPosition(int(round(target * 1000)))
            self.timeline.set_playhead(target, follow=True)

        def _on_player_position(self, ms):
            self.timeline.set_playhead(ms / 1000.0, follow=True)
            self._refresh_status()

        def _on_timeline_seek(self, t):
            if hasattr(self, "player"):
                self.player.setPosition(int(round(t * 1000)))
            self.timeline.set_playhead(t, follow=True)
            self._refresh_status()

        def _on_volume_changed(self, value):
            # Pure audio-mixer change; does NOT seek or interrupt playback.
            if not hasattr(self, "audio"):
                return
            self.audio.setVolume(max(0, min(100, value)) / 100.0)
            self.volume_label.setText(f"{value}%")
            level = 0 if value <= 0 else 1 if value <= 25 else 2 if value <= 50 else 3 if value <= 75 else 4
            if value <= 0:
                self.btn_mute.setIcon(self._icon("volume_meter_muted", QStyle.SP_MediaVolumeMuted))
            elif not self.audio.isMuted():
                self.btn_mute.setIcon(self._icon(f"volume_meter_{level}", QStyle.SP_MediaVolume))

        def toggle_mute(self):
            if not hasattr(self, "audio"):
                return
            muted = not self.audio.isMuted()
            self.audio.setMuted(muted)
            if muted:
                self.btn_mute.setIcon(self._icon("volume_meter_muted", QStyle.SP_MediaVolumeMuted))
            else:
                value = self.volume_slider.value()
                level = 0 if value <= 0 else 1 if value <= 25 else 2 if value <= 50 else 3 if value <= 75 else 4
                icon = "volume_meter_muted" if value <= 0 else f"volume_meter_{level}"
                self.btn_mute.setIcon(self._icon(icon, QStyle.SP_MediaVolume))

        def _current_time(self):
            if not hasattr(self, "player"):
                return 0.0
            return self.player.position() / 1000.0

        def mark_in(self):
            sel = self._selected_marker()
            if sel is not None and sel.kind == "out":
                sel.kind = "in"
                self._refresh_all_no_history()
                self._commit_history()
                return
            self._add_marker(self._current_time(), "in")
            self._clear_marker_selection()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def mark_out(self):
            sel = self._selected_marker()
            if sel is not None and sel.kind == "in":
                sel.kind = "out"
                self._refresh_all_no_history()
                self._commit_history()
                return
            self._add_marker(self._current_time(), "out")
            self._clear_marker_selection()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def _unpaired_marker_summary(self):
            ordered = sorted(self._markers, key=lambda m: (m.time, 0 if m.kind == "in" else 1))
            warnings = []
            i = 0
            while i < len(ordered):
                marker = ordered[i]
                nxt = ordered[i + 1] if i + 1 < len(ordered) else None
                if marker.kind == "in" and nxt is not None and nxt.kind == "out" and nxt.time > marker.time:
                    i += 2
                    continue
                warnings.append(f"{marker.kind.upper()} at {seconds_to_hmsf(marker.time, self.fps)}")
                i += 1
            return warnings

        def add_cuts(self):
            cuts = self._cuts()
            if cuts:
                warnings = self._unpaired_marker_summary()
                message = f"Add Cut(s): {len(cuts)} valid adjacent pair(s) are active."
                if warnings:
                    message += " Unpaired marker(s): " + "; ".join(warnings[:4])
                    if len(warnings) > 4:
                        message += f"; +{len(warnings) - 4} more"
                self.status_strip._label.setText(message)
                self._refresh_all_no_history()
                return
            t = self._current_time()
            out_t = min(self.duration, max(t + 1.0, t + 0.1))
            self._add_marker(t, "in")
            self._add_marker(out_t, "out")
            self._clear_marker_selection()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def invert_cuts(self):
            if not math.isfinite(self.duration) or self.duration <= 0:
                self._log_debug(
                    f"Invert cuts refused: invalid duration={self.duration!r}; "
                    f"input_path={self.input_path}")
                QMessageBox.critical(
                    self,
                    "Cannot invert cuts",
                    "Cannot invert cut ranges because the video duration is unknown.")
                return

            before = normalize_ranges(self._cuts(), self.duration)
            if not before:
                self._log_debug(
                    f"Invert cuts refused: no valid cut ranges; input_path={self.input_path}")
                QMessageBox.warning(
                    self,
                    "No cut ranges",
                    "There are no valid cut ranges to invert.")
                return

            after = invert_cut_ranges(before, self.duration)
            self._log_debug(
                "Invert cuts before="
                + _format_debug_ranges(before)
                + " after="
                + _format_debug_ranges(after)
                + f" duration={self.duration:.6f}")

            self._replace_cut_ranges_with_markers(after)
            self._selected_marker_ids.clear()
            self._selected_cut = 0 if after else -1
            self._refresh_all_no_history()
            self._commit_history()
            self.status_strip._label.setText(
                f"Inverted cuts. Kept the previous {len(before)} cut range(s); "
                f"now removing {len(after)} range(s).")

        def delete_selected_item(self):
            if self._selected_marker_ids:
                self.delete_selected_markers()
            elif self._selected_cut >= 0:
                self.delete_selected_cut()

        def delete_selected_markers(self):
            if not self._selected_marker_ids:
                return
            selected = set(self._selected_marker_ids)
            before = len(self._markers)
            self._markers = [m for m in self._markers if m.id not in selected]
            if len(self._markers) == before:
                return
            self._selected_marker_ids.clear()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def delete_selected_cut(self):
            if self._selected_cut < 0:
                return
            cuts = self._cuts()
            if 0 <= self._selected_cut < len(cuts):
                cs, ce = cuts[self._selected_cut]
                self._markers = [
                    m for m in self._markers
                    if not (abs(m.time - cs) < 1e-6 and m.kind == "in")
                    and not (abs(m.time - ce) < 1e-6 and m.kind == "out")
                ]
                self._selected_cut = -1
                self._selected_marker_ids.clear()
                self._refresh_all_no_history()
                self._commit_history()

        def delete_all_markers(self):
            if not self._markers:
                return
            box = QMessageBox(self)
            box.setWindowTitle("Delete all markers")
            box.setText("Remove every marker? This can be undone with Ctrl+Z.")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.No)
            if box.exec() != QMessageBox.Yes:
                return
            self._markers = []
            self._selected_marker_ids.clear()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def snap_marker_prev(self):
            self._snap(direction=-1)

        def snap_marker_next(self):
            self._snap(direction=1)

        def _snap(self, direction):
            now = self._current_time()
            candidates = sorted({m.time for m in self._markers})
            target = None
            if direction < 0:
                for c in reversed(candidates):
                    if c < now - 1e-4:
                        target = c
                        break
            else:
                for c in candidates:
                    if c > now + 1e-4:
                        target = c
                        break
            if target is not None:
                if hasattr(self, "player"):
                    self.player.setPosition(int(round(target * 1000)))
                self.timeline.set_playhead(target, follow=True)

        def _on_marker_selection_requested(self, marker_id, additive):
            self._set_selected_marker(marker_id, additive=additive)

        def _on_marker_drag_started(self):
            self._drag_snapshot = self._take_snapshot()

        def _on_marker_drag_moved(self, marker_id, new_time):
            for m in self._markers:
                if m.id == marker_id:
                    m.time = max(0.0, min(self.duration, new_time))
                    break
            self._refresh_all_no_history()

        def _on_marker_drag_finished(self):
            if self._drag_snapshot is None:
                return
            current = self._take_snapshot()
            pre = self._drag_snapshot
            self._drag_snapshot = None
            self._markers = [copy.copy(m) for m in pre.markers]
            self._selected_marker_ids = set(pre.selected_marker_ids)
            self._history.push(self._take_snapshot())
            self._markers = [copy.copy(m) for m in current.markers]
            self._selected_marker_ids = set(current.selected_marker_ids)
            self._history.push(self._take_snapshot())
            self._refresh_all_no_history()

        def _on_cut_selected_from_timeline(self, idx):
            self._selected_cut = idx
            if idx >= 0:
                self._selected_marker_ids.clear()
            if idx < 0:
                self.cut_list.blockSignals(True)
                self.cut_list.clearSelection()
                self.cut_list.blockSignals(False)
                self.timeline.set_selected_cut(-1)
                self._update_button_states()
                return
            self.cut_list.blockSignals(True)
            self.cut_list.setCurrentRow(idx)
            self.cut_list.blockSignals(False)
            self.timeline.set_selected_cut(idx)
            self._update_button_states()

        def _on_cut_context(self, idx, global_pos):
            menu = QMenu(self)
            act_seek = QAction("Seek playhead to this cut", self)
            act_seek.triggered.connect(lambda: self._seek_to_cut(idx))
            menu.addAction(act_seek)
            menu.addSeparator()
            act_del = QAction("Delete this cut", self)
            act_del.triggered.connect(self.delete_selected_cut)
            menu.addAction(act_del)
            menu.exec(global_pos)

        def _seek_to_cut(self, idx):
            cuts = self._cuts()
            if 0 <= idx < len(cuts):
                if hasattr(self, "player"):
                    self.player.setPosition(int(round(cuts[idx][0] * 1000)))
                self.timeline.set_playhead(cuts[idx][0], follow=True)

        def _on_cut_list_select(self):
            items = self.cut_list.selectedItems()
            self._selected_cut = self.cut_list.row(items[0]) if items else -1
            if self._selected_cut >= 0:
                self._selected_marker_ids.clear()
            self.timeline.set_selected_cut(self._selected_cut)
            self._update_button_states()

        def timeline_zoom_in(self):
            self.timeline.zoom_in_at_playhead()
            self._sync_timeline_zoom_slider()
            self._sync_timeline_navigation_slider()

        def timeline_zoom_out(self):
            self.timeline.zoom_out_at_playhead()
            self._sync_timeline_zoom_slider()
            self._sync_timeline_navigation_slider()

        def timeline_fit(self):
            self.timeline.fit_view()
            self._sync_timeline_zoom_slider()
            self._sync_timeline_navigation_slider()

        def _on_timeline_zoom_slider(self, value):
            if self._syncing_timeline_zoom:
                return
            ratio = 64.0 ** (float(value) / 100.0)
            self.timeline.set_zoom_ratio(ratio)
            self._sync_timeline_navigation_slider()
            self._update_timeline_zoom_arrow_states()

        def _sync_timeline_zoom_slider(self):
            if not hasattr(self, "timeline_zoom_slider"):
                return
            ratio = self.timeline.zoom_ratio()
            ratio = max(1.0, min(64.0, ratio))
            value = int(round(math.log(ratio, 64.0) * 100.0))
            self._syncing_timeline_zoom = True
            self.timeline_zoom_slider.setValue(max(0, min(100, value)))
            self._syncing_timeline_zoom = False
            self._update_timeline_zoom_arrow_states()

        def _nudge_timeline_zoom(self, direction):
            if not hasattr(self, "timeline_zoom_slider"):
                return
            step = 2
            value = max(
                self.timeline_zoom_slider.minimum(),
                min(
                    self.timeline_zoom_slider.maximum(),
                    self.timeline_zoom_slider.value() + int(direction) * step,
                ),
            )
            self.timeline_zoom_slider.setValue(value)

        def _update_timeline_zoom_arrow_states(self):
            slider = getattr(self, "timeline_zoom_slider", None)
            if slider is None:
                return
            left = getattr(self, "btn_timeline_zoom_left", None)
            right = getattr(self, "btn_timeline_zoom_right", None)
            if left is not None:
                left.setEnabled(slider.value() > slider.minimum())
            if right is not None:
                right.setEnabled(slider.value() < slider.maximum())

        def _on_timeline_navigation_slider(self, value):
            if self._syncing_timeline_navigation:
                return
            self.timeline.set_view_start(float(value) / 1000.0)

        def _nudge_timeline_view(self, direction):
            span = max(0.001, self.timeline.state.view_span)
            self.timeline.scroll_view(float(direction) * span * 0.10)
            self._sync_timeline_navigation_slider()

        def _sync_timeline_navigation_slider(self):
            if not hasattr(self, "timeline_navigation_slider"):
                return
            duration_ms = max(1, int(round(max(0.001, self.timeline.state.duration) * 1000.0)))
            span_ms = max(1, int(round(max(0.001, self.timeline.state.view_span) * 1000.0)))
            max_start = max(0, duration_ms - span_ms)
            value = max(0, min(max_start, int(round(max(0.0, self.timeline.state.view_start) * 1000.0))))
            single_step = max(1, int(round(span_ms * 0.05)))
            self._syncing_timeline_navigation = True
            self.timeline_navigation_slider.setRange(0, max_start)
            self.timeline_navigation_slider.setPageStep(max(1, span_ms))
            self.timeline_navigation_slider.setSingleStep(single_step)
            self.timeline_navigation_slider.setEnabled(max_start > 0)
            self.timeline_navigation_slider.setValue(value)
            self._syncing_timeline_navigation = False
            for button_name in ("btn_timeline_view_left", "btn_timeline_view_right"):
                button = getattr(self, button_name, None)
                if button is not None:
                    button.setEnabled(max_start > 0)

        def _refresh_all(self):
            self._refresh_all_no_history()

        def _refresh_all_no_history(self):
            self.timeline.set_markers(self._markers, self._selected_marker_ids)
            self.timeline.set_selected_cut(self._selected_cut)
            self._refresh_marker_list()
            self._refresh_status()
            self._update_button_states()
            self._update_undo_redo_state()
            self._sync_timeline_zoom_slider()
            self._sync_timeline_navigation_slider()

        def _refresh_marker_list(self):
            self.cut_list.blockSignals(True)
            self.cut_list.clear()
            cuts = self._cuts()
            for idx, (s, e) in enumerate(cuts):
                item = QListWidgetItem(
                    f"{idx + 1:>2}.  {seconds_to_hmsf(s, self.fps)}  ➜  "
                    f"{seconds_to_hmsf(e, self.fps)}      "
                    f"({seconds_to_timecode(s)}  ➜  {seconds_to_timecode(e)})"
                )
                self.cut_list.addItem(item)
            if 0 <= self._selected_cut < self.cut_list.count():
                self.cut_list.setCurrentRow(self._selected_cut)
            self.cut_list.blockSignals(False)

        def _refresh_status(self):
            sel = self._selected_marker()
            in_time = sel.time if sel and sel.kind == "in" else None
            out_time = sel.time if sel and sel.kind == "out" else None
            now = self._current_time()
            self.status_strip.update_status(
                now, in_time, out_time, self._cuts(),
                self.timeline.state.view_span / max(0.001, self.duration),
            )

        def _update_button_states(self):
            has_marker_sel = bool(self._selected_marker_ids)
            has_cut_sel = self._selected_cut >= 0
            self.btn_delete_markers.setEnabled(has_marker_sel)
            self.btn_delete_selected_cut.setEnabled(has_cut_sel)
            self.btn_delete_all.setEnabled(bool(self._markers))
            self.btn_invert_cuts.setEnabled(bool(self._cuts()) and self.duration > 0)
            for button in (self.btn_delete_markers, self.btn_delete_selected_cut, self.btn_delete_all):
                self._apply_disabled_opacity(button)
            sel = self._selected_marker()
            if sel and sel.kind == "out":
                self.btn_mark_in.setText(" Convert → In (I)")
            else:
                self.btn_mark_in.setText(" Mark In (I)")
            if sel and sel.kind == "in":
                self.btn_mark_out.setText(" Convert → Out (O)")
            else:
                self.btn_mark_out.setText(" Mark Out (O)")

        def confirm(self):
            cuts = self._cuts()
            keep = invert_cuts_to_keep(cuts, self.duration) if cuts else [(0.0, self.duration)]
            self.result = {"status": "ok",
                           "keep_ranges": [[s, e] for s, e in keep]}
            if hasattr(self, "player"):
                self.player.stop()
            self.close()

        def cancel(self):
            self.result = {"status": "canceled", "keep_ranges": []}
            if hasattr(self, "player"):
                self.player.stop()
            self.close()

        def closeEvent(self, event):
            try:
                QApplication.instance().removeEventFilter(self)
            except Exception:
                pass
            try:
                self.player.stop()
            except Exception:
                pass
            super().closeEvent(event)

    return CutEditorWindow(request)


# =====================================================================
# Crop Editor window
# =====================================================================


def build_crop_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, QtMultimedia = _import_qt()
    Qt = QtCore.Qt
    Signal = QtCore.Signal
    QPoint = QtCore.QPoint
    QPointF = QtCore.QPointF
    QRectF = QtCore.QRectF
    QSize = QtCore.QSize
    QTimer = QtCore.QTimer
    QThread = QtCore.QThread
    QUrl = QtCore.QUrl

    QColor = QtGui.QColor
    QFont = QtGui.QFont
    QImage = QtGui.QImage
    QPainter = QtGui.QPainter
    QPen = QtGui.QPen
    QBrush = QtGui.QBrush
    QPixmap = QtGui.QPixmap
    QCursor = QtGui.QCursor
    QShortcut = QtGui.QShortcut
    QKeySequence = QtGui.QKeySequence

    QApplication = QtWidgets.QApplication
    QStyle = QtWidgets.QStyle
    QMainWindow = QtWidgets.QMainWindow
    QWidget = QtWidgets.QWidget
    QFrame = QtWidgets.QFrame
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QSlider = QtWidgets.QSlider
    QComboBox = QtWidgets.QComboBox
    QHBoxLayout = QtWidgets.QHBoxLayout
    QVBoxLayout = QtWidgets.QVBoxLayout
    QSizePolicy = QtWidgets.QSizePolicy

    class CropCanvas(QWidget):
        margins_drag_started = Signal()
        margins_drag_finished = Signal()
        margins_changed = Signal()
        zoom_changed = Signal()
        request_toggle_playback = Signal()

        def __init__(self, source_w, source_h):
            super().__init__()
            self.source_w = max(1, int(source_w)); self.source_h = max(1, int(source_h))
            self.image = None; self.zoom = 1.0; self.tool = "hand"
            self._scroll = QPointF(0, 0); self._scroll_origin = QPointF(0, 0)
            self._press_pos = None; self._pan_origin = None; self._drag_handle = None
            self._move_origin = None; self._move_margins = None
            self._zoom_press_y = None; self._zoom_press_pos = None; self._zoom_press_zoom = 1.0; self._zoom_focus = None
            self._zoom_drag_last_pos = None
            self._zoom_drag_started = False; self._last_zoom_drag_update = 0.0
            self._zoom_out_mode = False; self._dragged = False
            self.margins = [0, 0, 0, 0]
            self._open_hand_cursor = self._load_cursor("hand_open.png", Qt.OpenHandCursor)
            self._zoom_in_cursor = self._make_zoom_cursor(False)
            self._zoom_out_cursor = self._make_zoom_cursor(True)
            self.setMouseTracking(True)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setMinimumSize(640, 360)
            self.setStyleSheet(f"background-color: {PALETTE['timeline_bg']};border: 1px solid {PALETTE['border']}; border-radius: 8px;")
            self._refresh_cursor_at_current_pos()

        def set_image(self, image): self.image = image; self.update()
        def set_margins(self, margins):
            self.margins = [int(x) for x in margins]
            self.margins_changed.emit(); self.update()
        def set_tool(self, tool): self.tool = tool; self._refresh_cursor_at_current_pos(); self.update()
        def set_zoom_out_mode(self, enabled, force=False):
            if force or self._zoom_out_mode != bool(enabled):
                self._zoom_out_mode = bool(enabled)
                if self.tool == "zoom": self._refresh_cursor_at_current_pos()
        def pan_by(self, dx, dy): self._scroll = QPointF(self._scroll.x()+dx, self._scroll.y()+dy); self.update()

        def _load_cursor(self, file_name, fallback):
            path = ASSETS_DIR / file_name
            if path.exists():
                pix = QPixmap(str(path))
                if not pix.isNull(): return QCursor(pix, 9, 9)
            return QCursor(fallback)

        def _make_zoom_cursor(self, zoom_out):
            pix = QPixmap(40, 40); pix.fill(Qt.transparent)
            p = QPainter(pix); p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(QPen(QColor(PALETTE["text"]), 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(QBrush(QColor(13, 17, 23, 220)))
            p.drawEllipse(QPointF(16, 16), 10, 10); p.drawLine(QPointF(24, 24), QPointF(34, 34))
            p.setPen(QPen(QColor(PALETTE["warn"] if zoom_out else PALETTE["accent_text"]), 3.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(11, 16), QPointF(21, 16))
            if not zoom_out: p.drawLine(QPointF(16, 11), QPointF(16, 21))
            p.end(); return QCursor(pix, 16, 16)

        def _cursor_for_handle(self, h):
            if h in {"n", "s"}: return QCursor(Qt.SizeVerCursor)
            if h in {"e", "w"}: return QCursor(Qt.SizeHorCursor)
            if h in {"nw", "se"}: return QCursor(Qt.SizeFDiagCursor)
            if h in {"ne", "sw"}: return QCursor(Qt.SizeBDiagCursor)
            if h == "move": return QCursor(Qt.SizeAllCursor)
            return QCursor(Qt.ArrowCursor)
        def _default_tool_cursor(self): return self._zoom_out_cursor if self.tool == "zoom" and self._zoom_out_mode else self._zoom_in_cursor if self.tool == "zoom" else self._open_hand_cursor
        def _refresh_cursor_at_current_pos(self):
            p = self.mapFromGlobal(QCursor.pos())
            self._refresh_cursor_at(QPointF(p.x(), p.y())) if self.rect().contains(p) else self.setCursor(self._default_tool_cursor())
        def _refresh_cursor_at(self, pos):
            h = self._hit_handle(pos.x(), pos.y())
            if h:
                self.setCursor(self._cursor_for_handle(h))
            elif (QApplication.keyboardModifiers() & Qt.ControlModifier) and self._crop_rect_screen().contains(pos):
                self.setCursor(QCursor(Qt.SizeAllCursor))
            else:
                self.setCursor(self._default_tool_cursor())

        def _display_size(self):
            s = self.size(); scale = min(s.width()/self.source_w, s.height()/self.source_h, 1.0) * self.zoom if s.width() > 0 and s.height() > 0 else 1.0
            return max(1, int(self.source_w*scale)), max(1, int(self.source_h*scale))
        def _image_rect(self):
            w,h = self._display_size(); return QRectF(self.width()/2 + self._scroll.x() - w/2, self.height()/2 + self._scroll.y() - h/2, w, h)
        def _zoom_anchor(self, x, y):
            old = self._image_rect()
            p = QPointF(float(x), float(y))
            if old.contains(p):
                rx=max(0,min(1,(p.x()-old.left())/max(1,old.width())))
                ry=max(0,min(1,(p.y()-old.top())/max(1,old.height())))
                return p.x(), p.y(), rx, ry
            return self.width()/2, self.height()/2, 0.5, 0.5
        def _crop_rect_screen(self):
            img = self._image_rect(); w,h = self._display_size(); t,l,r,b = self.margins
            return QRectF(img.left()+l/self.source_w*w, img.top()+t/self.source_h*h, max(1, img.right()-r/self.source_w*w-(img.left()+l/self.source_w*w)), max(1, img.bottom()-b/self.source_h*h-(img.top()+t/self.source_h*h)))

        def paintEvent(self, _event):
            p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, True); p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.fillRect(self.rect(), QColor(PALETTE["timeline_bg"])); img = self._image_rect()
            if self.image is not None and not self.image.isNull(): p.drawImage(img, self.image)
            else:
                p.setPen(QPen(QColor(PALETTE["text_mute"]))); p.setFont(QFont("Segoe UI", 12)); p.drawText(self.rect(), Qt.AlignCenter, "Loading preview frame...")
            crop = self._crop_rect_screen(); dim = QColor(0,0,0,165)
            p.fillRect(QRectF(img.left(), img.top(), img.width(), crop.top()-img.top()), dim); p.fillRect(QRectF(img.left(), crop.bottom(), img.width(), img.bottom()-crop.bottom()), dim)
            p.fillRect(QRectF(img.left(), crop.top(), crop.left()-img.left(), crop.height()), dim); p.fillRect(QRectF(crop.right(), crop.top(), img.right()-crop.right(), crop.height()), dim)
            p.setPen(QPen(QColor(PALETTE["warn"]), 2)); p.setBrush(Qt.NoBrush); p.drawRect(crop)
            p.setPen(QPen(QColor(PALETTE["warn"]), 1, Qt.DashLine))
            for i in (1,2):
                xp=crop.left()+crop.width()*i/3; yp=crop.top()+crop.height()*i/3; p.drawLine(QPointF(xp,crop.top()), QPointF(xp,crop.bottom())); p.drawLine(QPointF(crop.left(),yp), QPointF(crop.right(),yp))
            p.setBrush(QBrush(QColor(PALETTE["warn"]))); p.setPen(QPen(QColor(PALETTE["bg"]),1)); hs=8
            for cx,cy in self._handle_centers().values(): p.drawRect(QRectF(cx-hs, cy-hs, hs*2, hs*2))

        def _handle_centers(self):
            r=self._crop_rect_screen(); mx=(r.left()+r.right())/2; my=(r.top()+r.bottom())/2
            return {"nw":(r.left(),r.top()),"n":(mx,r.top()),"ne":(r.right(),r.top()),"e":(r.right(),my),"se":(r.right(),r.bottom()),"s":(mx,r.bottom()),"sw":(r.left(),r.bottom()),"w":(r.left(),my)}
        def _hit_handle(self, x, y):
            crop=self._crop_rect_screen(); hit=24; edge=14
            for name,(cx,cy) in self._handle_centers().items():
                if abs(x-cx)<=hit and abs(y-cy)<=hit: return name
            if crop.adjusted(-edge,-edge,edge,edge).contains(QPointF(x,y)):
                nl,nr,nt,nb = abs(x-crop.left())<=edge, abs(x-crop.right())<=edge, abs(y-crop.top())<=edge, abs(y-crop.bottom())<=edge
                if nl and nt: return "nw"
                if nr and nt: return "ne"
                if nr and nb: return "se"
                if nl and nb: return "sw"
                if nt: return "n"
                if nr: return "e"
                if nb: return "s"
                if nl: return "w"
            return None

        def mousePressEvent(self, event):
            self.setFocus(Qt.MouseFocusReason)
            if event.button() == Qt.RightButton:
                if self._image_rect().contains(event.position()): self.request_toggle_playback.emit()
                return
            if event.button() != Qt.LeftButton: return
            pos=event.position(); self._press_pos=pos; self._dragged=False; self._drag_handle=self._hit_handle(pos.x(), pos.y())
            if self._drag_handle: self.margins_drag_started.emit(); return
            if (event.modifiers() & Qt.ControlModifier) and self._crop_rect_screen().contains(pos):
                self._drag_handle = "move"
                self._move_origin = pos
                self._move_margins = list(self.margins)
                self.margins_drag_started.emit()
                self.setCursor(Qt.SizeAllCursor)
                return
            if self.tool == "zoom":
                self.set_zoom_out_mode(bool(event.modifiers() & Qt.AltModifier), force=True)
                self._zoom_press_y=pos.y(); self._zoom_press_pos=QPointF(pos.x(), pos.y())
                self._zoom_press_zoom=self.zoom; self._zoom_focus=QPointF(pos.x(), pos.y())
                self._zoom_drag_started=False; self._last_zoom_drag_update=0.0; self._zoom_drag_last_pos=QPointF(pos.x(), pos.y())
            elif self._image_rect().contains(pos):
                self._pan_origin=pos; self._scroll_origin=QPointF(self._scroll); self.setCursor(Qt.ClosedHandCursor)

        def mouseMoveEvent(self, event):
            pos=event.position()
            if self._press_pos is not None:
                d=pos-self._press_pos; self._dragged = self._dragged or abs(d.x())+abs(d.y())>6
            if self._drag_handle:
                if self._drag_handle == "move": self._move_crop(pos)
                else: self._resize_crop(self._drag_handle, pos.x(), pos.y())
                return
            if self.tool == "zoom" and self._zoom_press_y is not None and self._zoom_focus is not None:
                if event.modifiers() & Qt.AltModifier:
                    self.set_zoom_out_mode(True, force=True)
                self._apply_zoom_drag(pos)
                return
            if self._pan_origin is not None: self._scroll=self._scroll_origin+(pos-self._pan_origin); self.update(); return
            self._refresh_cursor_at(pos)

        def mouseReleaseEvent(self, event):
            zp=self._zoom_focus; zd=self._zoom_drag_started
            if self._drag_handle is not None: self.margins_drag_finished.emit()
            self._drag_handle=None; self._move_origin=None; self._move_margins=None; self._zoom_press_y=None; self._zoom_press_pos=None; self._zoom_focus=None; self._zoom_drag_last_pos=None; self._zoom_drag_started=False; self._pan_origin=None
            if self.tool == "zoom" and zp is not None and not zd and not self._dragged: self._zoom_centered(zp.x(), zp.y(), 1.0/1.25 if event.modifiers() & Qt.AltModifier else 1.25)
            elif self.tool == "zoom" and zp is not None and zd:
                self.zoom_changed.emit()
            self._press_pos=None; self._refresh_cursor_at(event.position())

        def wheelEvent(self, event):
            d=event.angleDelta().y()
            if d: self._zoom_centered(event.position().x(), event.position().y(), (1.15 if event.modifiers() & Qt.ControlModifier else 1.25) if d>0 else 1.0/(1.15 if event.modifiers() & Qt.ControlModifier else 1.25))
        def _zoom_centered(self, x, y, factor): self._set_zoom_centered(x, y, self.zoom*factor)
        def _apply_zoom_drag(self, pos):
            if self._zoom_press_pos is None or self._zoom_focus is None:
                return False
            if self._zoom_drag_last_pos is None:
                self._zoom_drag_last_pos = QPointF(pos.x(), pos.y())
                return False
            total_dy = pos.y() - self._zoom_press_pos.y()
            total_dx = pos.x() - self._zoom_press_pos.x()
            dy = pos.y() - self._zoom_drag_last_pos.y()
            dx = pos.x() - self._zoom_drag_last_pos.x()
            if abs(total_dy) < 2.0 and abs(total_dx) < 2.0:
                return False
            self._zoom_drag_last_pos = QPointF(pos.x(), pos.y())
            self._zoom_drag_started = True
            effective_dy = -dy if self._zoom_out_mode else dy
            target_zoom = self.zoom * (2.0 ** (-effective_dy / 75.0))
            if abs(target_zoom - self.zoom) < 0.0005:
                return True
            self._set_zoom_centered(self._zoom_focus.x(), self._zoom_focus.y(), target_zoom)
            if os.environ.get("FFMWIZ_DEBUG_COORDS"):
                _gui_log_debug(
                    f"Crop zoom drag dy={dy:.1f} dx={dx:.1f} total_dy={total_dy:.1f} target_zoom={target_zoom:.3f}",
                    force=True,
                )
            return True
        def _set_zoom_centered(self, x, y, target_zoom):
            x,y,rx,ry=self._zoom_anchor(x,y); oldz=self.zoom; self.zoom=max(0.10,min(32.0,target_zoom))
            if abs(self.zoom-oldz)<1e-6: return
            nw,nh=self._display_size(); self._scroll=QPointF(x-rx*nw+nw/2-self.width()/2, y-ry*nh+nh/2-self.height()/2)
            if os.environ.get("FFMWIZ_DEBUG_COORDS"):
                _gui_log_debug(
                    f"Crop zoom transform focus=({x:.1f},{y:.1f}) rel=({rx:.4f},{ry:.4f}) "
                    f"zoom={oldz:.3f}->{self.zoom:.3f} scroll=({self._scroll.x()},{self._scroll.y()})",
                    force=True,
                )
            self.update(); self.zoom_changed.emit()
        def _resize_crop(self, handle, x, y):
            img=self._image_rect(); w,h=self._display_size()
            if w<=0 or h<=0: return
            sx=max(0,min(1,(x-img.left())/w))*self.source_w; sy=max(0,min(1,(y-img.top())/h))*self.source_h; t,l,r,b=self.margins; ms=16
            if "w" in handle: l=max(0,min(self.source_w-r-ms,int(round(sx))))
            if "e" in handle: r=max(0,min(self.source_w-l-ms,int(round(self.source_w-sx))))
            if "n" in handle: t=max(0,min(self.source_h-b-ms,int(round(sy))))
            if "s" in handle: b=max(0,min(self.source_h-t-ms,int(round(self.source_h-sy))))
            self.margins=[t,l,r,b]; self.margins_changed.emit(); self.update()
        def _move_crop(self, pos):
            if self._move_origin is None or self._move_margins is None:
                return
            img=self._image_rect(); w,h=self._display_size()
            if w<=0 or h<=0:
                return
            dx=int(round((pos.x()-self._move_origin.x())/w*self.source_w))
            dy=int(round((pos.y()-self._move_origin.y())/h*self.source_h))
            t,l,r,b=[int(v) for v in self._move_margins]
            crop_w=max(1,self.source_w-l-r); crop_h=max(1,self.source_h-t-b)
            new_l=max(0,min(self.source_w-crop_w,l+dx)); new_t=max(0,min(self.source_h-crop_h,t+dy))
            new_r=self.source_w-crop_w-new_l; new_b=self.source_h-crop_h-new_t
            self.margins=[new_t,new_l,new_r,new_b]; self.margins_changed.emit(); self.update()
    class FrameExtractWorker(QThread):
        finished_with_image = Signal(QImage)

        def __init__(self, ffmpeg, source, timestamp, width, height, parent=None):
            super().__init__(parent)
            self.ffmpeg = ffmpeg
            self.source = source
            self.timestamp = timestamp
            self.width = width
            self.height = height
            self._process = None

        def stop(self):
            self.requestInterruption()
            proc = self._process
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass

        def run(self):
            tmp_dir = Path(tempfile.gettempdir()) / "ffmwiz_crop_qt"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            out_path = tmp_dir / f"frame_{int(self.timestamp * 1000)}_{self.width}x{self.height}.png"
            args = [
                self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{max(0.0, self.timestamp):.3f}",
                "-i", str(self.source),
                "-frames:v", "1",
                "-vf", f"scale={self.width}:{self.height}:flags=fast_bilinear",
                str(out_path),
            ]
            try:
                self._process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                _stdout, stderr = self._process.communicate()
                return_code = self._process.returncode
                self._process = None
                if self.isInterruptionRequested():
                    return
                if return_code != 0:
                    message = (stderr or b"").decode("utf-8", "replace").strip()
                    raise RuntimeError(message or f"ffmpeg exited with code {return_code}")
                image = QImage(str(out_path))
                self.finished_with_image.emit(image)
            except Exception:
                if not self.isInterruptionRequested():
                    self.finished_with_image.emit(QImage())

    class CropEditorWindow(QMainWindow):
        def __init__(self, request):
            init_start = time.perf_counter()
            super().__init__()
            self.request = request
            self.input_path = Path(request["input_path"])
            requested_fps = request.get("fps")
            self.fps = float(requested_fps or 25.0)
            if not requested_fps:
                print("Crop Editor FPS fallback: using 25.000 fps.", file=sys.stderr)
            self.duration = float(request.get("duration") or 0.0)
            self.source_w = int(request.get("source_w") or 1920)
            self.source_h = int(request.get("source_h") or 1080)
            self.ffmpeg = request.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"
            self.result = {"status": "canceled", "margins": [0, 0, 0, 0]}
            self._worker = None
            self._pending_request = None
            self._frame_request_started = 0.0
            self._syncing_zoom_control = False
            self._zoom_editor_active = False
            self._zoom_select_all_pending = False
            self._zoom_presets = (10, 15, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 800, 1200, 1600, 3200)

            # CRITICAL: state initialized BEFORE _build_ui() so it can read
            # self._timestamp during widget construction (previous _timestamp
            # AttributeError fix).
            self._timestamp = min(30.0, max(0.0, self.duration * 0.25))
            self._drag_snapshot = None
            self._history = HistoryStack(CropSnapshot(margins=(0, 0, 0, 0)), max_size=100)

            self.setWindowTitle("FFmWiz Crop Editor")
            self._icon = _icon_loader(self, self.style())
            _apply_window_icon(self, self._icon)
            self.setMinimumSize(1080, 720)
            self.resize(1280, 820)

            self._build_ui()
            self._install_shortcuts()
            _gui_log_debug(
                f"Crop GUI widgets initialized in {time.perf_counter() - init_start:.3f}s",
                force=True,
            )
            QtCore.QTimer.singleShot(150, self._deferred_media_startup)

        def _deferred_media_startup(self):
            start = time.perf_counter()
            self._setup_player()
            self._seek(self._timestamp)
            _gui_log_debug(
                f"Crop GUI media startup completed in {time.perf_counter() - start:.3f}s",
                force=True,
            )

        def _take_snapshot(self):
            t, l, r, b = self.canvas.margins
            return CropSnapshot(margins=(int(t), int(l), int(r), int(b)))

        def _commit_history(self):
            self._history.push(self._take_snapshot())
            self._update_undo_redo_state()

        def _restore_snapshot(self, snap):
            self.canvas.set_margins(list(snap.margins))
            self._refresh_info()

        def _undo(self):
            snap = self._history.undo()
            if snap is not None:
                self._restore_snapshot(snap)
                self._update_undo_redo_state()

        def _redo(self):
            snap = self._history.redo()
            if snap is not None:
                self._restore_snapshot(snap)
                self._update_undo_redo_state()

        def _update_undo_redo_state(self):
            self.btn_undo.setEnabled(self._history.can_undo())
            self.btn_redo.setEnabled(self._history.can_redo())

        def _build_ui(self):
            layout_start = time.perf_counter()
            central = QWidget()
            central.setObjectName("central")
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(14, 10, 14, 10)
            root.setSpacing(8)

            self.btn_undo = QPushButton(self._icon("undo", QStyle.SP_ArrowBack), "Undo (Ctrl+Z)")
            self.btn_undo.setToolTip("Undo last crop edit")
            self.btn_undo.clicked.connect(self._undo)
            self.btn_redo = QPushButton(self._icon("redo", QStyle.SP_ArrowForward), "Redo (Ctrl+Y)")
            self.btn_redo.setToolTip("Redo last undone crop edit")
            self.btn_redo.clicked.connect(self._redo)
            self.btn_undo.setEnabled(False)
            self.btn_redo.setEnabled(False)

            header = QFrame()
            header.setObjectName("header")
            hlay = QHBoxLayout(header)
            hlay.setContentsMargins(14, 10, 14, 10)
            hlay.setSpacing(10)
            title = QLabel("FFmWiz Crop Editor")
            title.setObjectName("title")
            hlay.addWidget(title)
            hlay.addSpacing(12)
            hlay.addWidget(self.btn_undo)
            hlay.addWidget(self.btn_redo)
            hlay.addStretch(1)
            info = QLabel(f"FPS  {self.fps:.3f}      •      Duration  "
                          f"{seconds_to_hmsf(self.duration, self.fps)}      •      Source  "
                          f"{self.input_path.name}")
            info.setObjectName("headerInfo")
            hlay.addWidget(info)
            root.addWidget(header)

            tool_row = QHBoxLayout()
            self.btn_hand = QPushButton(self._icon("hand_open", None), "  Hand Tool  (H)")
            self.btn_hand.setObjectName("tool")
            self.btn_hand.setProperty("active", "true")
            self.btn_hand.clicked.connect(self.activate_hand)
            tool_row.addWidget(self.btn_hand)
            self.btn_zoom = QPushButton(self._icon("zoom_in", None), "  Zoom Tool  (Z)")
            self.btn_zoom.setObjectName("tool")
            self.btn_zoom.clicked.connect(self.activate_zoom)
            tool_row.addWidget(self.btn_zoom)
            self.zoom_percent_box = QFrame()
            self.zoom_percent_box.setObjectName("zoomPercentBox")
            zoom_box_layout = QHBoxLayout(self.zoom_percent_box)
            zoom_box_layout.setContentsMargins(0, 0, 0, 0)
            zoom_box_layout.setSpacing(0)
            self.zoom_percent_combo = QComboBox()
            self.zoom_percent_combo.setObjectName("zoomPercentCombo")
            self.zoom_percent_combo.setEditable(True)
            self.zoom_percent_combo.setInsertPolicy(QComboBox.NoInsert)
            self.zoom_percent_combo.setFixedWidth(112)
            self.zoom_percent_combo.setToolTip(
                "Preview zoom presets. Type a percent value and press Enter to zoom manually."
            )
            self.zoom_percent_combo.addItems([f"{value}%" for value in self._zoom_presets])
            self.zoom_percent_combo.setCurrentText("100%")
            if self.zoom_percent_combo.lineEdit() is not None:
                self.zoom_percent_combo.lineEdit().installEventFilter(self)
                self.zoom_percent_combo.lineEdit().returnPressed.connect(self._apply_zoom_percent_text)
                self.zoom_percent_combo.lineEdit().editingFinished.connect(self._apply_zoom_percent_text)
            self.zoom_percent_combo.activated.connect(lambda _idx: self._apply_zoom_percent_text())
            zoom_box_layout.addWidget(self.zoom_percent_combo)
            tool_row.addWidget(self.zoom_percent_box)
            tool_row.addStretch(1)
            tool_row.addWidget(self._btn("Reset Zoom  (Ctrl+0)", self.reset_zoom))
            tool_row.addWidget(self._btn("Reset Crop  (Ctrl+R)", self.reset_crop))
            root.addLayout(tool_row)

            self.canvas = CropCanvas(self.source_w, self.source_h)
            self.canvas.margins_drag_started.connect(self._on_drag_started)
            self.canvas.margins_drag_finished.connect(self._on_drag_finished)
            self.canvas.margins_changed.connect(self._refresh_info)
            self.canvas.zoom_changed.connect(self._on_canvas_zoom_changed)
            self.canvas.request_toggle_playback.connect(self.toggle_playback)
            self.canvas.setFocusPolicy(Qt.StrongFocus)
            self.canvas.installEventFilter(self)
            QApplication.instance().installEventFilter(self)
            root.addWidget(self.canvas, 1)

            self.info_label = QLabel("")
            self.info_label.setObjectName("dim")
            root.addWidget(self.info_label)

            seek_row = QHBoxLayout()
            seek_row.setSpacing(6)
            self.btn_play = QPushButton(self._icon("play", QStyle.SP_MediaPlay), " Play  (Space)")
            self.btn_play.setObjectName("primary")
            self.btn_play.clicked.connect(self.toggle_playback)
            seek_row.addWidget(self.btn_play)
            seek_row.addWidget(self._btn("⏮  Home", self.go_home))
            seek_row.addWidget(self._btn("−10s (Shift+←)", lambda: self._seek_relative(-10.0)))
            seek_row.addWidget(self._btn("+10s (Shift+→)", lambda: self._seek_relative(10.0)))
            seek_row.addWidget(self._btn("End  ⏭", self.go_end))
            seek_row.addStretch(1)
            self.btn_mute = QPushButton(self._icon("volume_meter_3", QStyle.SP_MediaVolume), "  Mute (M)")
            self.btn_mute.setMinimumWidth(118)
            self.btn_mute.setIconSize(QtCore.QSize(20, 20))
            self.btn_mute.setToolTip("Mute / unmute (M)")
            self.btn_mute.clicked.connect(self.toggle_mute)
            seek_row.addWidget(self.btn_mute)
            self.volume_slider = QSlider(Qt.Horizontal)
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(60)
            self.volume_slider.setFixedWidth(160)
            self.volume_slider.valueChanged.connect(self._on_volume_changed)
            self.volume_slider.installEventFilter(self)
            seek_row.addWidget(self.volume_slider)
            self.volume_label = QLabel("60%")
            self.volume_label.setObjectName("dim")
            seek_row.addWidget(self.volume_label)
            root.addLayout(seek_row)

            time_row = QHBoxLayout()
            time_row.setSpacing(8)
            time_frame = QFrame()
            time_frame.setObjectName("timelineViewFrame")
            time_frame.setStyleSheet(f"""
                QFrame#timelineViewFrame {{
                    background: #111820;
                    border: 1px solid {PALETTE['border_strong']};
                    border-radius: 9px;
                }}
            """)
            time_frame.setMinimumWidth(760)
            time_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            time_frame_layout = QHBoxLayout(time_frame)
            time_frame_layout.setContentsMargins(3, 1, 3, 1)
            time_frame_layout.setSpacing(3)
            self.btn_time_seek_left = QPushButton("◀")
            self.btn_time_seek_left.setObjectName("timelineZoomArrow")
            self.btn_time_seek_left.setFixedSize(14, 14)
            self.btn_time_seek_left.setAutoRepeat(True)
            self.btn_time_seek_left.setAutoRepeatDelay(220)
            self.btn_time_seek_left.setAutoRepeatInterval(70)
            self.btn_time_seek_left.setToolTip("Seek preview backward")
            self.btn_time_seek_left.clicked.connect(lambda: self._nudge_time_slider(-1))
            time_frame_layout.addWidget(self.btn_time_seek_left)
            self.time_slider = QSlider(Qt.Horizontal)
            self.time_slider.setRange(0, max(1, int(self.duration * 1000)))
            self.time_slider.setTracking(True)
            self.time_slider.setSingleStep(max(1, int(1000 / max(1.0, self.fps))))
            self.time_slider.setPageStep(5000)
            self.time_slider.valueChanged.connect(lambda v: self._seek(v / 1000.0))
            self.time_slider.sliderMoved.connect(lambda v: self._seek(v / 1000.0))
            self.time_slider.installEventFilter(self)
            time_frame_layout.addWidget(self.time_slider, 1)
            self.btn_time_seek_right = QPushButton("▶")
            self.btn_time_seek_right.setObjectName("timelineZoomArrow")
            self.btn_time_seek_right.setFixedSize(14, 14)
            self.btn_time_seek_right.setAutoRepeat(True)
            self.btn_time_seek_right.setAutoRepeatDelay(220)
            self.btn_time_seek_right.setAutoRepeatInterval(70)
            self.btn_time_seek_right.setToolTip("Seek preview forward")
            self.btn_time_seek_right.clicked.connect(lambda: self._nudge_time_slider(1))
            time_frame_layout.addWidget(self.btn_time_seek_right)
            time_row.addWidget(time_frame, 1)
            self.time_label = QLabel("")
            self.time_label.setObjectName("dim")
            self.time_label.setMinimumWidth(168)
            self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            time_row.addWidget(self.time_label)
            root.addLayout(time_row)

            confirm_row = QHBoxLayout()
            tip = QLabel("Tip: Ctrl+drag moves crop. Mouse wheel over timeline seeks. Ctrl+(+) / Ctrl+(-) zooms preview.\n"
                         "Double-click Hand Tool resets zoom.")
            tip.setObjectName("tip")
            tip.setWordWrap(False)
            tip.setMinimumWidth(760)
            tip.setMinimumHeight(42)
            confirm_row.addWidget(tip, 1)
            confirm_row.addStretch(1)
            cancel_btn = QPushButton("Cancel  (Esc)")
            cancel_btn.setObjectName("danger")
            cancel_btn.clicked.connect(self.cancel)
            confirm_row.addWidget(cancel_btn)
            apply_btn = QPushButton(self._icon("check", QStyle.SP_DialogOkButton),
                                     "  Apply  (Enter)")
            apply_btn.setObjectName("primary")
            apply_btn.clicked.connect(self.confirm)
            confirm_row.addWidget(apply_btn)
            root.addLayout(confirm_row)

            self._refresh_info()
            self._update_undo_redo_state()
            self._update_zoom_tool_icon()
            _gui_log_debug(
                f"Crop GUI layout initialized in {time.perf_counter() - layout_start:.3f}s",
                force=True,
            )

        def _btn(self, text, slot):
            b = QPushButton(text)
            b.clicked.connect(slot)
            return b

        def _setup_player(self):
            QtMultimedia = _import_qt_multimedia()
            self._media_player_cls = QtMultimedia.QMediaPlayer
            self.player = QtMultimedia.QMediaPlayer(self)
            self.audio = QtMultimedia.QAudioOutput(self)
            self.audio.setVolume(self.volume_slider.value() / 100.0)
            self.player.setAudioOutput(self.audio)
            self.video_sink = QtMultimedia.QVideoSink(self)
            self.player.setVideoSink(self.video_sink)
            self.video_sink.videoFrameChanged.connect(self._on_video_frame)
            self.player.positionChanged.connect(self._on_player_position)
            self.player.playbackStateChanged.connect(self._on_playback_state_changed)
            self.player.errorOccurred.connect(self._on_player_error)
            self.player.setSource(QUrl.fromLocalFile(str(self.input_path)))
            self.player.pause()
            self._update_volume_icon()

        def eventFilter(self, obj, event):
            canvas = getattr(self, "canvas", None)
            volume_slider = getattr(self, "volume_slider", None)
            time_slider = getattr(self, "time_slider", None)
            if obj is getattr(self, "btn_hand", None) and event.type() == QtCore.QEvent.MouseButtonDblClick:
                self.reset_zoom()
                return True
            zoom_line = self.zoom_percent_combo.lineEdit() if hasattr(self, "zoom_percent_combo") and self.zoom_percent_combo.lineEdit() is not None else None
            if zoom_line is not None and event.type() == QtCore.QEvent.MouseButtonPress:
                try:
                    clicked_zoom_control = obj is zoom_line or obj is self.zoom_percent_combo or self.zoom_percent_combo.isAncestorOf(obj)
                except Exception:
                    clicked_zoom_control = False
                if self._zoom_editor_active and not clicked_zoom_control:
                    self._apply_zoom_percent_text()
                    zoom_line.deselect()
                    zoom_line.clearFocus()
                    self.zoom_percent_combo.clearFocus()
                    self._zoom_editor_active = False
                    if obj is canvas:
                        canvas.setFocus(Qt.MouseFocusReason)
                    else:
                        self.setFocus(Qt.MouseFocusReason)
            if obj is zoom_line:
                if event.type() == QtCore.QEvent.MouseButtonPress:
                    self._zoom_editor_active = True
                    if not zoom_line.hasFocus() or not zoom_line.hasSelectedText():
                        self._zoom_select_all_pending = True
                        QTimer.singleShot(0, self._select_zoom_percent_text_once)
                elif event.type() == QtCore.QEvent.FocusIn:
                    self._zoom_editor_active = True
                    self._zoom_select_all_pending = True
                    QTimer.singleShot(0, self._select_zoom_percent_text_once)
                elif event.type() == QtCore.QEvent.FocusOut:
                    self._apply_zoom_percent_text()
                    zoom_line.deselect()
                    self._zoom_editor_active = False
                    self._zoom_select_all_pending = False
            if obj is canvas and event.type() == QtCore.QEvent.KeyPress:
                if event.nativeVirtualKey() == WIN_VK.get("alt") or (event.modifiers() & Qt.AltModifier):
                    self._update_zoom_tool_icon(True, force=True)
                    return True
            if obj is canvas and event.type() == QtCore.QEvent.KeyRelease:
                if event.nativeVirtualKey() == WIN_VK.get("alt"):
                    self._update_zoom_tool_icon(False, force=True)
                    return True
            if obj is volume_slider and event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y()
                step = 3 if (event.modifiers() & Qt.ControlModifier) else 5
                volume_slider.setValue(max(0, min(100, volume_slider.value() + (step if delta > 0 else -step))))
                return True
            if obj is time_slider and event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y()
                step = 10.0 if (event.modifiers() & Qt.ShiftModifier) else 1.0 if (event.modifiers() & Qt.ControlModifier) else 5.0
                self._seek(self._timestamp + (step if delta > 0 else -step))
                event.accept()
                return True
            if obj is time_slider and event.type() in {
                QtCore.QEvent.MouseButtonPress,
                QtCore.QEvent.MouseMove,
                QtCore.QEvent.MouseButtonRelease,
            }:
                buttons = event.buttons() if hasattr(event, "buttons") else Qt.NoButton
                if event.type() == QtCore.QEvent.MouseButtonPress and getattr(event, "button", lambda: Qt.NoButton)() != Qt.LeftButton:
                    return False
                if event.type() == QtCore.QEvent.MouseMove and not (buttons & Qt.LeftButton):
                    return False
                if event.type() != QtCore.QEvent.MouseButtonRelease or getattr(event, "button", lambda: Qt.LeftButton)() == Qt.LeftButton:
                    x = int(max(0, min(time_slider.width(), event.position().x())))
                    value = QStyle.sliderValueFromPosition(
                        time_slider.minimum(),
                        time_slider.maximum(),
                        x,
                        max(1, time_slider.width()),
                    )
                    self._seek(value / 1000.0)
                    event.accept()
                    return True
            return super().eventFilter(obj, event)

        def _is_playing(self):
            if not hasattr(self, "player"):
                return False
            return self.player.playbackState() == self._media_player_cls.PlayingState

        def toggle_playback(self):
            if not hasattr(self, "player"):
                return
            if self._is_playing():
                self.player.pause()
                self._request_frame()
            else:
                pos_ms = self.player.position()
                dur_ms = max(0, self.player.duration() or int(self.duration * 1000))
                if dur_ms > 0 and pos_ms >= dur_ms - 500:
                    self.player.setPosition(0)
                self.player.play()
            self._on_playback_state_changed(self.player.playbackState())

        def _on_playback_state_changed(self, _state):
            if self._is_playing():
                self.btn_play.setText(" Pause  (Space)")
                self.btn_play.setIcon(self._icon("pause", QStyle.SP_MediaPause))
            else:
                self.btn_play.setText(" Play  (Space)")
                self.btn_play.setIcon(self._icon("play", QStyle.SP_MediaPlay))

        def _on_video_frame(self, frame):
            try:
                image = frame.toImage()
            except Exception:
                image = None
            if image is not None and not image.isNull():
                self.canvas.set_image(image)

        def _on_player_position(self, ms):
            self._timestamp = max(0.0, min(self.duration, ms / 1000.0))
            self.time_slider.blockSignals(True)
            self.time_slider.setValue(int(round(self._timestamp * 1000)))
            self.time_slider.blockSignals(False)
            self.time_label.setText(f"{seconds_to_hmsf(self._timestamp, self.fps)} / {seconds_to_hmsf(self.duration, self.fps)}")
            self._refresh_info()

        def _on_player_error(self, _err, msg):
            if msg:
                self.info_label.setText(f"Player message: {msg}")

        def _on_volume_changed(self, value):
            if not hasattr(self, "audio"):
                return
            self.audio.setVolume(max(0, min(100, value)) / 100.0)
            self.volume_label.setText(f"{value}%")
            self._update_volume_icon()

        def toggle_mute(self):
            if not hasattr(self, "audio"):
                return
            self.audio.setMuted(not self.audio.isMuted())
            self._update_volume_icon()

        def _update_volume_icon(self):
            value = self.volume_slider.value()
            if hasattr(self, "audio") and self.audio.isMuted():
                icon = "volume_meter_muted"
            elif value <= 0:
                icon = "volume_meter_muted"
            elif value <= 25:
                icon = "volume_meter_1"
            elif value <= 50:
                icon = "volume_meter_2"
            elif value <= 75:
                icon = "volume_meter_3"
            else:
                icon = "volume_meter_4"
            self.btn_mute.setIcon(self._icon(icon, QStyle.SP_MediaVolume))

        def _update_zoom_tool_icon(self, zoom_out=None, force=False):
            if zoom_out is None:
                zoom_out = bool(QApplication.keyboardModifiers() & Qt.AltModifier)
            self.canvas.set_zoom_out_mode(bool(zoom_out), force=force)
            self.btn_zoom.setIcon(self._icon("zoom_out" if zoom_out else "zoom_in", None))
            _gui_log_debug(f"Crop zoom Alt mode set to {'out' if zoom_out else 'in'}", force=force)

        def _on_drag_started(self):
            self._drag_snapshot = self._take_snapshot()

        def _on_drag_finished(self):
            if self._drag_snapshot is None:
                return
            current = self._take_snapshot()
            pre = self._drag_snapshot
            self._drag_snapshot = None
            self.canvas.margins = list(pre.margins)
            self._history.push(self._take_snapshot())
            self.canvas.margins = list(current.margins)
            self._history.push(self._take_snapshot())
            self.canvas.update()
            self._refresh_info()
            self._update_undo_redo_state()

        def activate_hand(self):
            self.canvas.set_tool("hand")
            self.btn_hand.setProperty("active", "true")
            self.btn_zoom.setProperty("active", "false")
            self._restyle_tools()

        def activate_zoom(self):
            self.canvas.set_tool("zoom")
            self.btn_hand.setProperty("active", "false")
            self.btn_zoom.setProperty("active", "true")
            self._update_zoom_tool_icon(force=True)
            self._restyle_tools()

        def _restyle_tools(self):
            for w in (self.btn_hand, self.btn_zoom):
                w.style().unpolish(w); w.style().polish(w); w.update()

        def reset_zoom(self):
            self.canvas.zoom = 1.0
            self.canvas._scroll = QPointF(0, 0)
            self.canvas.update()
            self._sync_zoom_percent_control()
            self._refresh_info()

        def _zoom_percent_value(self):
            return int(round(self.canvas.zoom * 100))

        def _sync_zoom_percent_control(self):
            if not hasattr(self, "zoom_percent_combo"):
                return
            value = self._zoom_percent_value()
            text = f"{value}%"
            self._syncing_zoom_control = True
            try:
                self.zoom_percent_combo.blockSignals(True)
                self.zoom_percent_combo.setCurrentText(text)
                self.zoom_percent_combo.blockSignals(False)
            finally:
                self._syncing_zoom_control = False

        def _focus_zoom_percent_editor(self):
            self._zoom_editor_active = True
            self.zoom_percent_combo.setFocus(Qt.MouseFocusReason)
            if self.zoom_percent_combo.lineEdit() is not None:
                self._zoom_select_all_pending = True
                QTimer.singleShot(0, self._select_zoom_percent_text_once)
            self.zoom_percent_combo.showPopup()

        def _select_zoom_percent_text_once(self):
            if not self._zoom_select_all_pending:
                return
            if self.zoom_percent_combo.lineEdit() is not None:
                self.zoom_percent_combo.lineEdit().selectAll()
            self._zoom_select_all_pending = False

        def _apply_zoom_percent_text(self):
            if self._syncing_zoom_control or not hasattr(self, "zoom_percent_combo"):
                return
            text = self.zoom_percent_combo.currentText().strip()
            match = re.search(r"\d+(?:[.,]\d+)?", text)
            if not match:
                self._sync_zoom_percent_control()
                return
            value = float(match.group(0).replace(",", "."))
            value = max(10.0, min(3200.0, value))
            self._set_preview_zoom_percent(value)

        def _set_preview_zoom_percent(self, value):
            p = self._preview_zoom_focus()
            self.canvas._set_zoom_centered(p.x(), p.y(), float(value) / 100.0)
            self._sync_zoom_percent_control()
            self._refresh_info()

        def _on_canvas_zoom_changed(self):
            self._sync_zoom_percent_control()
            self._refresh_info()

        def _preview_zoom_focus(self):
            p = self.canvas.mapFromGlobal(QCursor.pos())
            if self.canvas.rect().contains(p):
                return QPointF(p.x(), p.y())
            return QPointF(self.canvas.width() / 2.0, self.canvas.height() / 2.0)

        def zoom_preview_in(self):
            p = self._preview_zoom_focus()
            self.canvas._zoom_centered(p.x(), p.y(), 1.15)
            self._refresh_info()

        def zoom_preview_out(self):
            p = self._preview_zoom_focus()
            self.canvas._zoom_centered(p.x(), p.y(), 1.0 / 1.15)
            self._refresh_info()

        def reset_crop(self):
            if self.canvas.margins == [0, 0, 0, 0]:
                return
            self.canvas.set_margins([0, 0, 0, 0])
            self._refresh_info()
            self._commit_history()

        def go_home(self):
            self._seek(0.0)

        def go_end(self):
            self._seek(self.duration)

        def _seek_relative(self, dt):
            self._seek(self._timestamp + dt)

        def _nudge_time_slider(self, direction):
            frame_step = 1.0 / max(1.0, self.fps)
            self._seek(self._timestamp + float(direction) * max(frame_step, 0.25))

        def _seek(self, t):
            self._timestamp = max(0.0, min(self.duration, float(t)))
            target_ms = int(round(self._timestamp * 1000))
            if hasattr(self, "player") and abs(self.player.position() - target_ms) > 40:
                self.player.setPosition(target_ms)
            self.time_slider.blockSignals(True)
            self.time_slider.setValue(target_ms)
            self.time_slider.blockSignals(False)
            self.time_label.setText(f"{seconds_to_hmsf(self._timestamp, self.fps)} / {seconds_to_hmsf(self.duration, self.fps)}")
            if not hasattr(self, "player") or not self._is_playing():
                self._request_frame()
            self._refresh_info()

        def _request_frame(self):
            target_w = max(320, self.canvas.width())
            target_h = max(180, self.canvas.height())
            req = (round(self._timestamp * 1000), target_w, target_h)
            self._pending_request = req
            if self._worker is not None and self._worker.isRunning():
                return
            self._launch_worker()

        def _launch_worker(self):
            if not self._pending_request:
                return
            ts_ms, w, h = self._pending_request
            self._pending_request = None
            self._frame_request_started = time.perf_counter()
            self._worker = FrameExtractWorker(self.ffmpeg, self.input_path,
                                              ts_ms / 1000.0, w, h, parent=self)
            self._worker.finished_with_image.connect(self._on_frame_extracted)
            self._worker.finished.connect(self._worker.deleteLater)
            self._worker.start()

        def _on_frame_extracted(self, image):
            if not image.isNull():
                self.canvas.set_image(image)
            if self._frame_request_started:
                _gui_log_debug(
                    f"Crop preview frame loaded in {time.perf_counter() - self._frame_request_started:.3f}s",
                    force=True,
                )
                self._frame_request_started = 0.0
            self._worker = None
            if self._pending_request:
                self._launch_worker()

        def _stop_worker(self):
            self._pending_request = None
            worker = self._worker
            self._worker = None
            if worker is not None and worker.isRunning():
                worker.stop()
                if not worker.wait(3000):
                    worker.terminate()
                    worker.wait(1000)

        def _refresh_info(self):
            t, l, r, b = self.canvas.margins
            self.info_label.setText(
                f"Crop margins:  top={t} px  left={l} px  right={r} px  bottom={b} px      "
                f"Output crop box:  {max(1, self.source_w - l - r)}x{max(1, self.source_h - t - b)}      "
                f"Time:  {seconds_to_hmsf(self._timestamp, self.fps)} / {seconds_to_hmsf(self.duration, self.fps)}      "
                f"Tool:  {'Zoom' if self.canvas.tool == 'zoom' else 'Hand'}      "
                f"Zoom:  {int(round(self.canvas.zoom * 100))}%"
            )

        def _install_shortcuts(self):
            QShortcut(QKeySequence("Space"), self).activated.connect(self.toggle_playback)
            QShortcut(QKeySequence("M"), self).activated.connect(self.toggle_mute)
            QShortcut(QKeySequence("H"), self).activated.connect(self.activate_hand)
            QShortcut(QKeySequence("Z"), self).activated.connect(self.activate_zoom)
            QShortcut(QKeySequence("R"), self).activated.connect(self.reset_crop)
            QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.reset_crop)
            QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.reset_zoom)
            QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.zoom_preview_in)
            QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.zoom_preview_in)
            QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.zoom_preview_out)
            QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._undo)
            QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self._redo)
            QShortcut(QKeySequence("Ctrl+Shift+Z"), self).activated.connect(self._redo)
            QShortcut(QKeySequence(Qt.Key_Home), self).activated.connect(self.go_home)
            QShortcut(QKeySequence(Qt.Key_End), self).activated.connect(self.go_end)
            QShortcut(QKeySequence("Shift+Left"), self).activated.connect(lambda: self._seek_relative(-10.0))
            QShortcut(QKeySequence("Shift+Right"), self).activated.connect(lambda: self._seek_relative(10.0))
            QShortcut(QKeySequence("Return"), self).activated.connect(self._confirm_or_apply_zoom_editor)
            QShortcut(QKeySequence("Enter"), self).activated.connect(self._confirm_or_apply_zoom_editor)
            QShortcut(QKeySequence("Escape"), self).activated.connect(self.cancel)

        def _zoom_editor_has_focus(self):
            if not hasattr(self, "zoom_percent_combo") or self.zoom_percent_combo.lineEdit() is None:
                return False
            focus = QApplication.focusWidget()
            return (
                self._zoom_editor_active
                or focus is self.zoom_percent_combo
                or focus is self.zoom_percent_combo.lineEdit()
                or self.zoom_percent_combo.hasFocus()
                or self.zoom_percent_combo.lineEdit().hasFocus()
            )

        def _confirm_or_apply_zoom_editor(self):
            if self._zoom_editor_has_focus():
                self._apply_zoom_percent_text()
                return
            self.confirm()

        def keyPressEvent(self, event):
            vk = event.nativeVirtualKey()
            mods = event.modifiers()
            ctrl = bool(mods & Qt.ControlModifier)
            shift = bool(mods & Qt.ShiftModifier)
            if (mods & Qt.AltModifier) or vk == WIN_VK.get("alt"):
                self._update_zoom_tool_icon(True, force=True)
                if vk == WIN_VK.get("alt"):
                    event.accept()
                    return
            if ctrl and not shift:
                if vk == WIN_VK["z"]: self._undo(); return
                if vk == WIN_VK["y"]: self._redo(); return
                if vk == WIN_VK["r"]: self.reset_crop(); return
                if vk == WIN_VK["0"]: self.reset_zoom(); return
                if vk in (WIN_VK["plus"], WIN_VK["equal"], WIN_VK["kp_add"]): self.zoom_preview_in(); return
                if vk in (WIN_VK["minus"], WIN_VK["kp_subtract"]): self.zoom_preview_out(); return
            if ctrl and shift and vk == WIN_VK["z"]:
                self._redo(); return
            if shift and not ctrl and vk == WIN_VK["left"]:
                self._seek_relative(-10.0); return
            if shift and not ctrl and vk == WIN_VK["right"]:
                self._seek_relative(10.0); return
            if not ctrl and not shift:
                pan_step = max(8, int(min(self.canvas.width(), self.canvas.height()) * 0.035))
                if vk == WIN_VK["left"]: self.canvas.pan_by(pan_step, 0); return
                if vk == WIN_VK["right"]: self.canvas.pan_by(-pan_step, 0); return
                if vk == WIN_VK["up"]: self.canvas.pan_by(0, pan_step); return
                if vk == WIN_VK["down"]: self.canvas.pan_by(0, -pan_step); return
                actions = {
                    WIN_VK["space"]: self.toggle_playback,
                    WIN_VK["m"]: self.toggle_mute,
                    WIN_VK["h"]: self.activate_hand,
                    WIN_VK["z"]: self.activate_zoom,
                    WIN_VK["r"]: self.reset_crop,
                    WIN_VK["home"]: self.go_home,
                    WIN_VK["end"]: self.go_end,
                    WIN_VK["return"]: self._confirm_or_apply_zoom_editor,
                    WIN_VK["escape"]: self.cancel,
                }
                fn = actions.get(vk)
                if fn is not None:
                    fn(); return
            super().keyPressEvent(event)

        def keyReleaseEvent(self, event):
            vk = event.nativeVirtualKey()
            if vk == WIN_VK.get("alt"):
                self._update_zoom_tool_icon(False, force=True)
                event.accept()
                return
            self._update_zoom_tool_icon(force=True)
            super().keyReleaseEvent(event)

        def confirm(self):
            self.result = {"status": "ok", "margins": list(self.canvas.margins)}
            try:
                self.player.stop()
            except Exception:
                pass
            self._stop_worker()
            self.close()

        def cancel(self):
            self.result = {"status": "canceled", "margins": [0, 0, 0, 0]}
            try:
                self.player.stop()
            except Exception:
                pass
            self._stop_worker()
            self.close()

        def closeEvent(self, event):
            try:
                self.player.stop()
            except Exception:
                pass
            self._stop_worker()
            super().closeEvent(event)
    return CropEditorWindow(request)


# =====================================================================
# Speed / reverse and audio waveform editors
# =====================================================================


def _speed_to_percent(speed: float) -> int:
    try:
        return max(10, min(800, int(round(float(speed) * 100))))
    except Exception:
        return 100


def _ffmpeg_float(value: float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


def _gui_atempo_chain(speed: float) -> str:
    remaining = max(0.10, min(8.0, float(speed or 1.0)))
    stages: list[float] = []
    while remaining > 2.0 + 1e-9:
        stages.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        stages.append(0.5)
        remaining /= 0.5
    stages.append(remaining)
    return ",".join(f"atempo={_ffmpeg_float(stage)}" for stage in stages)


def build_speed_editor(request: dict[str, Any], media_kind: str):
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    QtMultimedia = _import_qt_multimedia()
    Qt = QtCore.Qt
    QUrl = QtCore.QUrl

    QMainWindow = QtWidgets.QMainWindow
    QWidget = QtWidgets.QWidget
    QFrame = QtWidgets.QFrame
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QSlider = QtWidgets.QSlider
    QCheckBox = QtWidgets.QCheckBox
    QHBoxLayout = QtWidgets.QHBoxLayout
    QVBoxLayout = QtWidgets.QVBoxLayout
    QSizePolicy = QtWidgets.QSizePolicy
    QDoubleSpinBox = QtWidgets.QDoubleSpinBox
    QComboBox = QtWidgets.QComboBox
    QStyle = QtWidgets.QStyle

    class PreviewLabel(QLabel):
        clicked = QtCore.Signal()

        def __init__(self, audio_only=False):
            super().__init__()
            self.audio_only = audio_only
            self._pix = None
            self.setAlignment(Qt.AlignCenter)
            self.setMinimumSize(720, 360 if not audio_only else 220)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setStyleSheet(
                f"background-color: {PALETTE['timeline_bg']};"
                f"border: 1px solid {PALETTE['border']}; border-radius: 8px;"
            )
            self.setText("Loading preview...")

        def on_frame(self, frame):
            if self.audio_only or frame is None or not frame.isValid():
                return
            image = frame.toImage()
            if image.isNull():
                return
            self._pix = QtGui.QPixmap.fromImage(image)
            self._render()

        def set_waveform(self, path: Path):
            pix = QtGui.QPixmap(str(path))
            if not pix.isNull():
                self._pix = pix
                self._render()

        def _render(self):
            if self._pix is None:
                return
            self.setPixmap(self._pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self._render()

        def mouseReleaseEvent(self, event):
            if event.button() == Qt.LeftButton:
                self.clicked.emit()
            super().mouseReleaseEvent(event)

    class SpeedEditorWindow(QMainWindow):
        def __init__(self, req, kind):
            super().__init__()
            self.request = req
            self.kind = kind
            self.input_path = Path(req["input_path"])
            self.ffmpeg = str(req.get("ffmpeg") or "ffmpeg")
            self.audio_index = int(req.get("audio_index", 0) or 0)
            self.duration = float(req.get("duration") or 0.0)
            self.has_audio = bool(req.get("has_audio", kind == "audio"))
            self.audio_count = max(0, int(req.get("audio_count", 1 if self.has_audio else 0) or 0))
            self.result = {"status": "canceled"}
            self._icon = _icon_loader(self, self.style())
            self._wave_temp = tempfile.TemporaryDirectory(prefix="ffmwiz_waveform_")
            self._wave_path = Path(self._wave_temp.name) / "waveform.png"
            self._preview_generation = 0
            self._using_rendered_preview = False
            self._preview_source_start = 0.0
            self._preview_source_duration = 0.0
            self._rendered_preview_speed = 1.0
            self._scheduled_preview_generation = 0
            self._pending_preview_resume_playing = False
            self._current_preview_path = None
            self._pending_preview_path = None
            self._preview_proc = None
            self._closing = False
            self._preview_direction = "source"
            self._reverse_seek_pending = False
            self._pending_reverse_source_ms = None
            self._pending_reverse_resume_playing = False
            self._seek_resume_after_release = False
            self._resume_after_frame_pending = False
            self._resume_after_frame_armed = False
            self._resume_after_frame_should_play = False
            self._reverse_scrub_last_tick = None
            self._reverse_audio_mute_active = False
            self._reverse_audio_restore_muted = False
            self._audio_guard_active = False
            self._audio_guard_waiting_after_play = False
            self._audio_guard_restore_muted = False
            self._audio_guard_restore_volume = 0.7
            self._seeking = False
            self._restoring_settings = False
            self._history = None
            self._speed_value = 1.0
            self.setWindowTitle("FFmWiz Video Speed / Reverse" if kind == "video" else "FFmWiz Audio Speed / Reverse")
            _apply_window_icon(self, self._icon)
            self.setMinimumSize(940, 620 if kind == "video" else 500)
            self.resize(1160, 760 if kind == "video" else 560)
            self._build_ui()
            self._history = HistoryStack(self._snapshot())
            self._update_undo_redo_state()
            self._preview_timer = QtCore.QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.timeout.connect(self._render_reverse_preview)
            self._reverse_scrub_timer = QtCore.QTimer(self)
            self._reverse_scrub_timer.setInterval(90)
            self._reverse_scrub_timer.timeout.connect(self._reverse_scrub_tick)
            self._resume_after_frame_timer = QtCore.QTimer(self)
            self._resume_after_frame_timer.setSingleShot(True)
            self._resume_after_frame_timer.timeout.connect(self._finish_delayed_resume)
            self._audio_guard_timer = QtCore.QTimer(self)
            self._audio_guard_timer.setSingleShot(True)
            self._audio_guard_timer.timeout.connect(self._end_seek_audio_guard)
            self._setup_player()
            QtCore.QTimer.singleShot(60, self._load_media)
            if kind == "audio":
                QtCore.QTimer.singleShot(80, self._start_waveform)

        def _build_ui(self):
            central = QWidget()
            central.setObjectName("central")
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(14, 10, 14, 10)
            root.setSpacing(8)

            header = QFrame()
            header.setObjectName("header")
            h = QHBoxLayout(header)
            h.setContentsMargins(14, 10, 14, 10)
            title = QLabel("FFmWiz Video Speed / Reverse" if self.kind == "video" else "FFmWiz Audio Speed / Reverse")
            title.setObjectName("title")
            h.addWidget(title)
            h.addSpacing(12)
            self.btn_undo = QPushButton(self._icon("undo", QStyle.SP_ArrowBack), " Undo (Ctrl+Z)")
            self.btn_undo.setToolTip("Undo speed/reverse setting change")
            self.btn_undo.clicked.connect(self._undo)
            h.addWidget(self.btn_undo)
            self.btn_redo = QPushButton(self._icon("redo", QStyle.SP_ArrowForward), " Redo (Ctrl+Y)")
            self.btn_redo.setToolTip("Redo speed/reverse setting change")
            self.btn_redo.clicked.connect(self._redo)
            h.addWidget(self.btn_redo)
            h.addStretch(1)
            info = QLabel(f"Duration {seconds_to_timecode(self.duration)}      •      Source  {self.input_path.name}")
            info.setObjectName("headerInfo")
            h.addWidget(info)
            root.addWidget(header)

            self.preview = PreviewLabel(audio_only=(self.kind == "audio"))
            self.preview.clicked.connect(self.toggle_playback)
            root.addWidget(self.preview, 1)

            controls = QFrame()
            controls.setObjectName("panel")
            lay = QVBoxLayout(controls)
            lay.setContentsMargins(12, 10, 12, 10)
            lay.setSpacing(8)

            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(QLabel("Speed"))
            self.speed_slider = QSlider(Qt.Horizontal)
            self.speed_slider.setRange(10, 800)
            self.speed_slider.setValue(100)
            self.speed_slider.setTracking(True)
            self.speed_slider.setMinimumWidth(360)
            row.addWidget(self.speed_slider, 1)
            self.speed_spin = QComboBox()
            self.speed_spin.setObjectName("speedValueCombo")
            self.speed_spin.setEditable(True)
            self.speed_spin.setInsertPolicy(QComboBox.NoInsert)
            self.speed_spin.setMinimumWidth(136)
            self.speed_spin.setToolTip("Speed percent presets. Type a percent value and press Enter.")
            for label in ("25%", "50%", "75%", "100%", "125%", "150%", "200%", "250%", "300%", "400%"):
                self.speed_spin.addItem(label)
            self.speed_spin.setCurrentText("100%")
            self.speed_spin.installEventFilter(self)
            row.addWidget(self.speed_spin)
            self.factor_spin = QComboBox()
            self.factor_spin.setObjectName("speedValueCombo")
            self.factor_spin.setEditable(True)
            self.factor_spin.setInsertPolicy(QComboBox.NoInsert)
            self.factor_spin.setMinimumWidth(128)
            self.factor_spin.setToolTip("Speed multiplier presets. 1.5x means 1.5 times faster.")
            for label in ("0.25x", "0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x", "3x", "4x", "8x"):
                self.factor_spin.addItem(label)
            self.factor_spin.setCurrentText("1x")
            self.factor_spin.installEventFilter(self)
            row.addWidget(self.factor_spin)
            self.reverse_box = QCheckBox("Reverse")
            self.reverse_box.setToolTip("Reverse playback/export direction")
            row.addWidget(self.reverse_box)
            if self.kind == "video" and self.has_audio:
                self.include_audio_box = QCheckBox("Sync all audio tracks")
                self.include_audio_box.setChecked(True)
                self.include_audio_box.setToolTip(
                    "Apply the same speed and reverse changes to every audio track so the preview/export stays in sync. "
                    "Disable it only if this tool should output video without synced audio."
                )
                row.addWidget(self.include_audio_box)
            lay.addLayout(row)

            timeline_row = QHBoxLayout()
            timeline_row.setSpacing(8)
            self.time_label = QLabel("00:00:00.000 / " + seconds_to_timecode(self.duration))
            self.time_label.setObjectName("dim")
            timeline_row.addWidget(self.time_label)
            self.position_slider = QSlider(Qt.Horizontal)
            self.position_slider.setRange(0, max(1, int(round(self.duration * 1000))))
            self.position_slider.setTracking(True)
            self.position_slider.setToolTip("Playback timeline")
            self.position_slider.installEventFilter(self)
            timeline_row.addWidget(self.position_slider, 1)
            lay.addLayout(timeline_row)

            audio_row = QHBoxLayout()
            audio_row.setSpacing(8)
            audio_row.addStretch(1)
            self.btn_mute = QPushButton(self._icon("volume_meter_3", QStyle.SP_MediaVolume), "  Mute (M)")
            self.btn_mute.setMinimumWidth(118)
            self.btn_mute.setIconSize(QtCore.QSize(20, 20))
            self.btn_mute.setToolTip("Mute / unmute preview audio (M)")
            self.btn_mute.clicked.connect(self.toggle_mute)
            audio_row.addWidget(self.btn_mute)
            self.volume_slider = QSlider(Qt.Horizontal)
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(70)
            self.volume_slider.setFixedWidth(170)
            self.volume_slider.setToolTip("Preview volume")
            audio_row.addWidget(self.volume_slider)
            self.volume_label = QLabel("70%")
            self.volume_label.setObjectName("dim")
            audio_row.addWidget(self.volume_label)
            lay.addLayout(audio_row)

            self.status = QLabel(
                "Speed preview updates live. Reverse preview renders a short synced segment when enabled."
            )
            self.status.setObjectName("dim")
            lay.addWidget(self.status)
            root.addWidget(controls)

            bottom = QHBoxLayout()
            self.btn_play = QPushButton(self._icon("play", QStyle.SP_MediaPlay), " Play (Space)")
            self.btn_play.setObjectName("primary")
            self.btn_play.clicked.connect(self.toggle_playback)
            bottom.addWidget(self.btn_play)
            bottom.addStretch(1)
            self.btn_cancel = QPushButton("Cancel (Esc)")
            self.btn_cancel.setObjectName("danger")
            self.btn_cancel.clicked.connect(self.cancel)
            bottom.addWidget(self.btn_cancel)
            self.btn_apply = QPushButton(self._icon("check", QStyle.SP_DialogOkButton), " Apply (Enter)")
            self.btn_apply.setObjectName("primary")
            self.btn_apply.clicked.connect(self.confirm)
            bottom.addWidget(self.btn_apply)
            root.addLayout(bottom)

            self.speed_slider.valueChanged.connect(self._on_slider_changed)
            self.speed_slider.sliderReleased.connect(self._commit_history)
            self.speed_spin.activated.connect(lambda _idx: self._apply_percent_text(commit=True))
            self.factor_spin.activated.connect(lambda _idx: self._apply_factor_text(commit=True))
            if self.speed_spin.lineEdit() is not None:
                self.speed_spin.lineEdit().returnPressed.connect(lambda: self._apply_percent_text(commit=True))
                self.speed_spin.lineEdit().editingFinished.connect(lambda: self._apply_percent_text(commit=True))
                self.speed_spin.lineEdit().installEventFilter(self)
            if self.factor_spin.lineEdit() is not None:
                self.factor_spin.lineEdit().returnPressed.connect(lambda: self._apply_factor_text(commit=True))
                self.factor_spin.lineEdit().editingFinished.connect(lambda: self._apply_factor_text(commit=True))
                self.factor_spin.lineEdit().installEventFilter(self)
            self.reverse_box.stateChanged.connect(self._on_settings_changed_commit)
            if self.kind == "video" and hasattr(self, "include_audio_box"):
                self.include_audio_box.stateChanged.connect(self._on_settings_changed_commit)
            self.position_slider.sliderPressed.connect(self._on_seek_pressed)
            self.position_slider.sliderReleased.connect(self._on_seek_released)
            self.position_slider.sliderMoved.connect(self._seek_from_slider)
            self.volume_slider.valueChanged.connect(self._on_volume_changed)
            self.volume_slider.sliderReleased.connect(self._commit_history)
            QtGui.QShortcut(QtGui.QKeySequence("Space"), self, activated=self.toggle_playback)
            QtGui.QShortcut(QtGui.QKeySequence("M"), self, activated=self.toggle_mute)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Z"), self, activated=self._undo)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Y"), self, activated=self._redo)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+Z"), self, activated=self._redo)
            QtGui.QShortcut(QtGui.QKeySequence("Esc"), self, activated=self.cancel)
            QtGui.QShortcut(QtGui.QKeySequence("Return"), self, activated=self._confirm_if_not_editing_speed)
            QtGui.QShortcut(QtGui.QKeySequence("Enter"), self, activated=self._confirm_if_not_editing_speed)

        def _setup_player(self):
            self.player = QtMultimedia.QMediaPlayer(self)
            self.audio = QtMultimedia.QAudioOutput(self)
            self.audio.setVolume(0.7)
            self.player.setAudioOutput(self.audio)
            if self.kind == "video":
                self.video_sink = QtMultimedia.QVideoSink(self)
                self.player.setVideoSink(self.video_sink)
                self.video_sink.videoFrameChanged.connect(self.preview.on_frame)
                self.video_sink.videoFrameChanged.connect(self._on_video_frame_for_resume)
            self.player.playbackStateChanged.connect(self._on_playback_state)
            self.player.mediaStatusChanged.connect(self._on_media_status_changed)
            self.player.errorOccurred.connect(self._on_player_error)
            self.player.positionChanged.connect(self._on_position_changed)
            self.player.durationChanged.connect(self._on_duration_changed)
            self.player.setPlaybackRate(1.0)

        def _load_media(self):
            self.player.setSource(QUrl.fromLocalFile(str(self.input_path)))
            self.player.pause()
            QtCore.QTimer.singleShot(150, self.player.pause)

        def _start_waveform(self):
            args = [
                "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(self.input_path),
                "-filter_complex", f"[0:a:{self.audio_index}]aformat=channel_layouts=mono,showwavespic=s=1600x260:colors=388bfd[wave]",
                "-map", "[wave]",
                "-frames:v", "1",
                "-c:v", "png",
                str(self._wave_path),
            ]
            self.wave_proc = QtCore.QProcess(self)
            self.wave_proc.finished.connect(self._waveform_finished)
            self.wave_proc.start(self.ffmpeg, args)

        def _waveform_finished(self, *_args):
            if self._wave_path.exists():
                self.preview.set_waveform(self._wave_path)
            else:
                self.preview.setText("Waveform preview could not be generated.")

        def _speed(self):
            return max(0.10, min(8.0, float(getattr(self, "_speed_value", 1.0))))

        def _parse_percent_text(self):
            text = str(self.speed_spin.currentText() or "").strip().lower().replace(" ", "")
            if text.endswith("%"):
                text = text[:-1]
            return max(0.10, min(8.0, float(text) / 100.0))

        def _parse_factor_text(self):
            text = str(self.factor_spin.currentText() or "").strip().lower().replace(" ", "")
            if text.endswith("x"):
                text = text[:-1]
            return max(0.10, min(8.0, float(text)))

        def _snapshot(self):
            return {
                "speed": self._speed(),
                "reverse": bool(self.reverse_box.isChecked()),
                "include_audio": bool(self.include_audio_box.isChecked()) if hasattr(self, "include_audio_box") else False,
                "volume": int(self.volume_slider.value()) if hasattr(self, "volume_slider") else 70,
                "muted": bool(self.audio.isMuted()) if hasattr(self, "audio") else False,
            }

        def _commit_history(self):
            if self._restoring_settings or self._history is None:
                return
            self._history.push(self._snapshot())
            self._update_undo_redo_state()

        def _restore_snapshot(self, snap):
            self._restoring_settings = True
            try:
                self._set_speed(float(snap.get("speed", 1.0)))
                self.reverse_box.setChecked(bool(snap.get("reverse", False)))
                if self.kind == "video" and hasattr(self, "include_audio_box"):
                    self.include_audio_box.setChecked(bool(snap.get("include_audio", True)))
                self.volume_slider.setValue(int(snap.get("volume", 70)))
                if hasattr(self, "audio"):
                    self.audio.setMuted(bool(snap.get("muted", False)))
            finally:
                self._restoring_settings = False
            self._on_settings_changed()
            self._update_volume_icon()
            self._update_undo_redo_state()

        def _undo(self):
            if self._history is None:
                return
            snap = self._history.undo()
            if snap is not None:
                self._restore_snapshot(snap)
                self._update_undo_redo_state()

        def _redo(self):
            if self._history is None:
                return
            snap = self._history.redo()
            if snap is not None:
                self._restore_snapshot(snap)
                self._update_undo_redo_state()

        def _update_undo_redo_state(self):
            if hasattr(self, "btn_undo"):
                self.btn_undo.setEnabled(bool(self._history and self._history.can_undo()))
            if hasattr(self, "btn_redo"):
                self.btn_redo.setEnabled(bool(self._history and self._history.can_redo()))

        def _set_speed(self, speed):
            speed = max(0.10, min(8.0, float(speed or 1.0)))
            self._speed_value = speed
            percent = speed * 100.0
            self.speed_slider.blockSignals(True)
            self.speed_spin.blockSignals(True)
            self.factor_spin.blockSignals(True)
            self.speed_slider.setValue(int(round(percent)))
            self.speed_spin.setCurrentText(f"{percent:.1f}%")
            self.factor_spin.setCurrentText(f"{speed:.2f}x")
            self.speed_slider.blockSignals(False)
            self.speed_spin.blockSignals(False)
            self.factor_spin.blockSignals(False)
            self._sync_presets(speed)
            self._on_settings_changed()

        def _sync_presets(self, speed):
            percent = int(round(speed * 100.0))
            factor_text = f"{speed:g}x"
            for combo, text in ((self.speed_spin, f"{percent}%"), (self.factor_spin, factor_text)):
                for i in range(combo.count()):
                    if combo.itemText(i).lower() == text.lower():
                        combo.blockSignals(True)
                        combo.setCurrentIndex(i)
                        combo.blockSignals(False)
                        break

        def _on_slider_changed(self, value):
            self._set_speed(float(value) / 100.0)

        def _apply_percent_text(self, commit=False):
            try:
                self._set_speed(self._parse_percent_text())
            except Exception:
                self._set_speed(self._speed())
            if commit:
                self._commit_history()

        def _apply_factor_text(self, commit=False):
            try:
                self._set_speed(self._parse_factor_text())
            except Exception:
                self._set_speed(self._speed())
            if commit:
                self._commit_history()

        def _wheel_step_speed(self, source_combo, event):
            delta = event.angleDelta().y()
            if delta == 0:
                return True
            step_count = max(1, abs(delta) // 120)
            direction = 1 if delta > 0 else -1
            current = self._speed()
            if source_combo is self.speed_spin:
                next_percent = round(current * 100.0 / 5.0) * 5.0 + direction * 5.0 * step_count
                next_speed = next_percent / 100.0
            else:
                next_speed = round(current / 0.05) * 0.05 + direction * 0.05 * step_count
            self._set_speed(max(0.10, min(8.0, next_speed)))
            self._commit_history()
            event.accept()
            return True

        def _timeline_value_from_event(self, event):
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            rect = self.position_slider.rect()
            width = max(1, rect.width())
            ratio = max(0.0, min(1.0, float(pos.x() - rect.left()) / float(width)))
            return int(round(self.position_slider.minimum() + ratio * (self.position_slider.maximum() - self.position_slider.minimum())))

        def _confirm_if_not_editing_speed(self):
            focus = QtWidgets.QApplication.focusWidget()
            speed_line = self.speed_spin.lineEdit() if self.speed_spin.lineEdit() is not None else None
            factor_line = self.factor_spin.lineEdit() if self.factor_spin.lineEdit() is not None else None
            if focus in (self.speed_spin, speed_line):
                self._apply_percent_text(commit=True)
                return
            if focus in (self.factor_spin, factor_line):
                self._apply_factor_text(commit=True)
                return
            self.confirm()

        def _select_combo_text(self, combo):
            if combo.lineEdit() is not None:
                combo.lineEdit().selectAll()

        def eventFilter(self, obj, event):
            speed_line = self.speed_spin.lineEdit() if hasattr(self, "speed_spin") and self.speed_spin.lineEdit() is not None else None
            factor_line = self.factor_spin.lineEdit() if hasattr(self, "factor_spin") and self.factor_spin.lineEdit() is not None else None
            if obj is getattr(self, "position_slider", None):
                if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                    self._on_seek_pressed()
                    value = self._timeline_value_from_event(event)
                    self.position_slider.setValue(value)
                    self._update_time_label(value)
                    event.accept()
                    return True
                if event.type() == QtCore.QEvent.MouseMove and self._seeking:
                    value = self._timeline_value_from_event(event)
                    self.position_slider.setValue(value)
                    self._update_time_label(value)
                    event.accept()
                    return True
                if event.type() == QtCore.QEvent.MouseButtonRelease and self._seeking and event.button() == Qt.LeftButton:
                    value = self._timeline_value_from_event(event)
                    self.position_slider.setValue(value)
                    self._update_time_label(value)
                    self._seeking = False
                    self._seek_to_logical_ms(value, self._seek_resume_after_release)
                    self._seek_resume_after_release = False
                    event.accept()
                    return True
            if event.type() == QtCore.QEvent.Wheel:
                if obj in (self.speed_spin, speed_line):
                    return self._wheel_step_speed(self.speed_spin, event)
                if obj in (self.factor_spin, factor_line):
                    return self._wheel_step_speed(self.factor_spin, event)
            if obj in (speed_line, factor_line):
                if event.type() == QtCore.QEvent.FocusIn:
                    QtCore.QTimer.singleShot(0, obj.selectAll)
                elif event.type() == QtCore.QEvent.MouseButtonPress and not obj.hasSelectedText():
                    QtCore.QTimer.singleShot(0, obj.selectAll)
            return super().eventFilter(obj, event)

        def _on_settings_changed_commit(self):
            self._on_settings_changed()
            self._commit_history()

        def _on_settings_changed(self):
            speed = self._speed()
            if self.reverse_box.isChecked():
                self._stop_reverse_scrub(update_button=False)
                self._end_reverse_audio_mute()
                self._schedule_reverse_preview()
            else:
                self._stop_reverse_scrub()
                self._end_reverse_audio_mute()
                self._restore_original_preview()
                try:
                    was_playing = self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
                    self.player.setPlaybackRate(speed)
                    if was_playing:
                        self.player.play()
                except Exception:
                    pass
            self.status.setText(
                f"Speed {speed * 100:.0f}% ({speed:.2f}x)"
                + (
                    " • rendering reverse preview"
                    if self.reverse_box.isChecked()
                    else ""
                )
            )

        def _on_seek_pressed(self):
            self._seeking = True
            self._seek_resume_after_release = (
                self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
            )
            if self._seek_resume_after_release:
                self.player.pause()

        def _on_seek_released(self):
            self._seeking = False
            self._seek_to_logical_ms(self.position_slider.value(), self._seek_resume_after_release)
            self._seek_resume_after_release = False

        def _seek_from_slider(self, value):
            if self._seeking:
                value = max(0, min(self.position_slider.maximum(), int(value)))
                self.position_slider.blockSignals(True)
                self.position_slider.setValue(value)
                self.position_slider.blockSignals(False)
                self._update_time_label(value)
                return
            self._seek_to_logical_ms(value, self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState)

        def _seek_to_logical_ms(self, value, resume_playing=False):
            value = max(0, min(self.position_slider.maximum(), int(value)))
            if self.reverse_box.isChecked():
                self._reverse_seek_pending = True
                self._pending_reverse_source_ms = value
                self._pending_reverse_resume_playing = bool(resume_playing)
                try:
                    self.player.pause()
                except Exception:
                    pass
                self.position_slider.blockSignals(True)
                self.position_slider.setValue(value)
                self.position_slider.blockSignals(False)
                self._update_time_label(value)
                self._schedule_reverse_preview(40)
                return
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(value)
            self.position_slider.blockSignals(False)
            self._update_time_label(value)
            self._set_player_position_and_state(value, bool(resume_playing), self._speed())

        def _on_position_changed(self, ms):
            if self._using_rendered_preview and self.reverse_box.isChecked():
                if self._reverse_seek_pending:
                    source_ms = int(self._pending_reverse_source_ms if self._pending_reverse_source_ms is not None else self.position_slider.value())
                    self.position_slider.blockSignals(True)
                    self.position_slider.setValue(max(0, min(self.position_slider.maximum(), source_ms)))
                    self.position_slider.blockSignals(False)
                    self._update_time_label(source_ms)
                    return
                speed = max(0.10, float(getattr(self, "_rendered_preview_speed", self._speed()) or self._speed()))
                source_seconds = self._preview_source_start + max(
                    0.0,
                    self._preview_source_duration - (float(ms) / 1000.0) * speed,
                )
                source_ms = int(round(max(0.0, min(self.duration, source_seconds)) * 1000.0))
                if not self._seeking:
                    self.position_slider.blockSignals(True)
                    self.position_slider.setValue(max(0, min(self.position_slider.maximum(), source_ms)))
                    self.position_slider.blockSignals(False)
                self._update_time_label(source_ms)
                return
            if not self._seeking:
                self.position_slider.blockSignals(True)
                self.position_slider.setValue(max(0, min(self.position_slider.maximum(), int(ms))))
                self.position_slider.blockSignals(False)
            self._update_time_label(int(ms))

        def _on_duration_changed(self, ms):
            if self._using_rendered_preview and self.reverse_box.isChecked():
                self.position_slider.setRange(0, max(1, int(round(self.duration * 1000))))
                self._update_time_label(self.position_slider.value())
                return
            if ms and ms > 0:
                self.position_slider.setRange(0, int(ms))
                if self.duration <= 0:
                    self.duration = float(ms) / 1000.0
                self._update_time_label(self.player.position())

        def _update_time_label(self, ms):
            current = seconds_to_timecode(max(0.0, float(ms) / 1000.0))
            duration = seconds_to_timecode(max(self.duration, self.position_slider.maximum() / 1000.0))
            self.time_label.setText(f"{current} / {duration}")

        def _begin_reverse_audio_mute(self):
            if self.kind != "video" or not hasattr(self, "audio"):
                return
            if not self._reverse_audio_mute_active:
                try:
                    self._reverse_audio_restore_muted = bool(self.audio.isMuted())
                except Exception:
                    self._reverse_audio_restore_muted = False
            self._reverse_audio_mute_active = True
            try:
                self.audio.setMuted(True)
            except Exception:
                pass
            self._update_volume_icon()

        def _end_reverse_audio_mute(self):
            if not self._reverse_audio_mute_active or not hasattr(self, "audio"):
                return
            self._reverse_audio_mute_active = False
            try:
                self.audio.setMuted(bool(self._reverse_audio_restore_muted) or int(self.volume_slider.value()) <= 0)
            except Exception:
                pass
            self._update_volume_icon()

        def _activate_video_reverse_scrub(self):
            if self.kind != "video":
                return
            current_ms = max(0, min(self.position_slider.maximum(), int(self.position_slider.value())))
            was_playing = (
                self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
                or self._reverse_scrub_timer.isActive()
            )
            self._clear_pending_reverse_preview_state()
            if self._using_rendered_preview:
                self._using_rendered_preview = False
                self._current_preview_path = None
                self.player.setSource(QUrl.fromLocalFile(str(self.input_path)))
            try:
                self.player.setPlaybackRate(1.0)
                self.player.pause()
                self.player.setPosition(current_ms)
            except Exception:
                pass
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(current_ms)
            self.position_slider.blockSignals(False)
            self._update_time_label(current_ms)
            self._begin_reverse_audio_mute()
            if was_playing:
                self._start_reverse_scrub()
            else:
                self._update_play_button(False)
            self.status.setText(
                f"Speed {self._speed() * 100:.0f}% ({self._speed():.2f}x) • reverse video preview uses frame scrubbing; export reverses synced audio."
            )

        def _start_reverse_scrub(self):
            if self.kind != "video":
                return
            self._begin_reverse_audio_mute()
            try:
                self.player.pause()
            except Exception:
                pass
            self._reverse_scrub_last_tick = time.perf_counter()
            self._reverse_scrub_timer.start()
            self._update_play_button(True)

        def _stop_reverse_scrub(self, update_button=True):
            try:
                self._reverse_scrub_timer.stop()
            except Exception:
                pass
            self._reverse_scrub_last_tick = None
            if update_button:
                self._update_play_button(False)

        def _reverse_scrub_tick(self):
            if getattr(self, "_closing", False) or not self.reverse_box.isChecked() or self.kind != "video":
                self._stop_reverse_scrub()
                return
            now = time.perf_counter()
            last = self._reverse_scrub_last_tick or now
            self._reverse_scrub_last_tick = now
            elapsed_ms = max(1.0, (now - last) * 1000.0)
            step_ms = max(20, int(round(elapsed_ms * self._speed())))
            current_ms = max(0, int(self.position_slider.value()))
            next_ms = max(0, current_ms - step_ms)
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(next_ms)
            self.position_slider.blockSignals(False)
            self._update_time_label(next_ms)
            try:
                self.player.setPosition(next_ms)
            except Exception:
                pass
            if next_ms <= 0:
                self._stop_reverse_scrub()

        def _update_play_button(self, playing):
            self.btn_play.setText(" Pause (Space)" if playing else " Play (Space)")
            self.btn_play.setIcon(self._icon("pause" if playing else "play", QStyle.SP_MediaPause if playing else QStyle.SP_MediaPlay))

        def _begin_delayed_resume(self):
            if getattr(self, "_closing", False):
                return
            if not self._resume_after_frame_should_play:
                return
            if self.kind != "video":
                try:
                    self.player.play()
                except Exception:
                    pass
                return
            self._begin_seek_audio_guard()
            self._resume_after_frame_pending = True
            self._resume_after_frame_armed = False

            def arm_resume():
                if self._resume_after_frame_pending:
                    self._resume_after_frame_armed = True

            QtCore.QTimer.singleShot(80, arm_resume)
            self._resume_after_frame_timer.start(700)

        def _finish_delayed_resume(self):
            if not self._resume_after_frame_pending or getattr(self, "_closing", False):
                return
            self._resume_after_frame_pending = False
            self._resume_after_frame_armed = False
            try:
                self._resume_after_frame_timer.stop()
            except Exception:
                pass
            if self._resume_after_frame_should_play:
                try:
                    self.player.play()
                except Exception:
                    pass
            if self._audio_guard_active:
                self._audio_guard_waiting_after_play = True
                self._audio_guard_timer.start(700)

        def _on_video_frame_for_resume(self, _frame):
            if self._resume_after_frame_pending and self._resume_after_frame_armed:
                QtCore.QTimer.singleShot(20, self._finish_delayed_resume)
                return
            if self._audio_guard_active and self._audio_guard_waiting_after_play:
                QtCore.QTimer.singleShot(180, self._end_seek_audio_guard)

        def _begin_seek_audio_guard(self):
            if self.kind != "video" or not hasattr(self, "audio"):
                return
            if not self._audio_guard_active:
                try:
                    self._audio_guard_restore_muted = bool(self.audio.isMuted())
                except Exception:
                    self._audio_guard_restore_muted = False
                try:
                    self._audio_guard_restore_volume = float(self.audio.volume())
                except Exception:
                    self._audio_guard_restore_volume = max(0.0, min(1.0, int(self.volume_slider.value()) / 100.0))
            self._audio_guard_active = True
            self._audio_guard_waiting_after_play = False
            try:
                self._audio_guard_timer.stop()
            except Exception:
                pass
            try:
                self.audio.setVolume(0.0)
            except Exception:
                pass
            try:
                self.audio.setMuted(True)
            except Exception:
                pass

        def _end_seek_audio_guard(self):
            if not self._audio_guard_active:
                return
            self._audio_guard_active = False
            self._audio_guard_waiting_after_play = False
            try:
                self._audio_guard_timer.stop()
            except Exception:
                pass
            try:
                target_volume = max(0.0, min(1.0, int(self.volume_slider.value()) / 100.0))
                self.audio.setVolume(target_volume)
            except Exception:
                try:
                    self.audio.setVolume(float(self._audio_guard_restore_volume))
                except Exception:
                    pass
            try:
                self.audio.setMuted(bool(self._audio_guard_restore_muted) or int(self.volume_slider.value()) <= 0)
            except Exception:
                pass
            self._update_volume_icon()

        def _on_volume_changed(self, value):
            try:
                self.audio.setVolume(max(0, min(100, int(value))) / 100.0)
            except Exception:
                pass
            if value > 0 and hasattr(self, "audio") and self.audio.isMuted() and not self._audio_guard_active:
                self.audio.setMuted(False)
            self.volume_label.setText(f"{int(value)}%")
            self._update_volume_icon()

        def toggle_mute(self):
            self.audio.setMuted(not self.audio.isMuted())
            self._update_volume_icon()
            self._commit_history()

        def _update_volume_icon(self):
            value = int(self.volume_slider.value()) if hasattr(self, "volume_slider") else 0
            muted = bool(self.audio.isMuted()) if hasattr(self, "audio") else False
            if muted or value <= 0:
                icon = "volume_meter_muted"
            elif value < 30:
                icon = "volume_meter_1"
            elif value < 60:
                icon = "volume_meter_2"
            elif value < 85:
                icon = "volume_meter_3"
            else:
                icon = "volume_meter_4"
            self.btn_mute.setIcon(self._icon(icon, QStyle.SP_MediaVolumeMuted if muted or value <= 0 else QStyle.SP_MediaVolume))

        def _set_player_position_and_state(self, position_ms, resume_playing, playback_rate=None):
            position_ms = max(0, int(position_ms or 0))
            self._resume_after_frame_should_play = bool(resume_playing)

            def apply():
                try:
                    if playback_rate is not None:
                        self.player.setPlaybackRate(float(playback_rate))
                except Exception:
                    pass
                try:
                    self.player.setPosition(position_ms)
                except Exception:
                    pass
                try:
                    self.player.pause()
                except Exception:
                    pass

            apply()
            QtCore.QTimer.singleShot(80, apply)
            if resume_playing:
                QtCore.QTimer.singleShot(120, self._begin_delayed_resume)

        def _source_seconds_to_reverse_preview_ms(self, source_seconds, speed=None):
            speed = max(0.10, float(speed or getattr(self, "_rendered_preview_speed", self._speed()) or self._speed()))
            start = float(self._preview_source_start or 0.0)
            duration = float(self._preview_source_duration or 0.0)
            end = start + duration
            source_seconds = max(start, min(end, float(source_seconds or 0.0)))
            preview_ms = int(round(max(0.0, (end - source_seconds) / speed) * 1000.0))
            preview_duration_ms = int(round(max(0.0, duration / speed) * 1000.0))
            if preview_duration_ms > 300:
                preview_ms = min(preview_ms, preview_duration_ms - 150)
            return max(0, preview_ms)

        def _clear_pending_reverse_preview_state(self):
            self._reverse_seek_pending = False
            self._pending_reverse_source_ms = None
            self._pending_reverse_resume_playing = False
            self._pending_preview_resume_playing = False
            self._preview_direction = "source"
            self._resume_after_frame_pending = False
            self._resume_after_frame_armed = False
            self._resume_after_frame_should_play = False
            try:
                self._preview_timer.stop()
            except Exception:
                pass
            try:
                self._resume_after_frame_timer.stop()
            except Exception:
                pass
            if self._audio_guard_active:
                self._end_seek_audio_guard()

        def _cleanup_reverse_preview_files(self, keep_path=None):
            try:
                keep = Path(keep_path).resolve() if keep_path else None
                for path in Path(self._wave_temp.name).glob("reverse_preview_*"):
                    try:
                        if keep is not None and path.resolve() == keep:
                            continue
                        path.unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception:
                pass

        def _restore_original_preview(self):
            self._clear_pending_reverse_preview_state()
            if self._preview_proc is not None:
                try:
                    self._preview_proc.finished.disconnect()
                except Exception:
                    pass
                try:
                    self._preview_proc.kill()
                except Exception:
                    pass
                self._preview_proc = None
            self._pending_preview_path = None
            if not self._using_rendered_preview:
                return
            resume_playing = self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
            current_ms = self.position_slider.value()
            self._using_rendered_preview = False
            self._current_preview_path = None
            self.player.setSource(QUrl.fromLocalFile(str(self.input_path)))
            self.position_slider.setRange(0, max(1, int(round(self.duration * 1000))))
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(max(0, min(self.position_slider.maximum(), int(current_ms))))
            self.position_slider.blockSignals(False)
            self._update_time_label(current_ms)
            self._set_player_position_and_state(current_ms, resume_playing, self._speed())
            self._cleanup_reverse_preview_files()

        def _schedule_reverse_preview(self, delay_ms=180):
            self._preview_generation += 1
            self._scheduled_preview_generation = self._preview_generation
            self._preview_timer.start(max(0, int(delay_ms)))

        def _start_preview_process(self, out_path: Path, args: list[str], generation: int, speed: float):
            if self._preview_proc is not None:
                try:
                    self._preview_proc.finished.disconnect()
                except Exception:
                    pass
                try:
                    self._preview_proc.kill()
                except Exception:
                    pass
            if self._pending_preview_path is not None:
                try:
                    Path(self._pending_preview_path).unlink(missing_ok=True)
                except Exception:
                    pass
            self._pending_preview_path = out_path
            self._preview_proc = QtCore.QProcess(self)
            self._preview_proc.finished.connect(
                lambda *_args, p=out_path, g=generation, s=speed: self._reverse_preview_finished(p, g, s)
            )
            self._preview_proc.start(self.ffmpeg, args)

        def _render_reverse_preview(self):
            generation = self._scheduled_preview_generation or self._preview_generation
            speed = self._speed()
            self._pending_preview_resume_playing = (
                self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
            )
            if self._pending_reverse_source_ms is not None:
                target = max(0.0, min(self.duration, float(self._pending_reverse_source_ms) / 1000.0))
            else:
                target = max(0.0, min(self.duration, self.position_slider.value() / 1000.0))
            window_span = max(0.001, min(8.0, max(0.001, self.duration)))
            if target <= 0.25:
                window_start = 0.0
                window_end = min(max(0.001, self.duration), window_span)
            else:
                window_end = max(0.001, min(self.duration, target))
                window_start = max(0.0, window_end - window_span)
            preview_source_duration = max(0.001, window_end - window_start)
            self._preview_source_start = window_start
            self._preview_source_duration = preview_source_duration
            if self.kind == "video":
                out_path = Path(self._wave_temp.name) / f"reverse_preview_{generation}.mp4"
                vf = f"reverse,setpts=(PTS-STARTPTS)/{_ffmpeg_float(speed)}"
                args = [
                    "-hide_banner", "-loglevel", "error", "-y",
                ]
                if window_start > 0:
                    args.extend(["-ss", _ffmpeg_float(window_start), "-noaccurate_seek"])
                args.extend([
                    "-t", _ffmpeg_float(preview_source_duration),
                    "-i", str(self.input_path),
                    "-map", "0:v:0",
                    "-sn",
                    "-dn",
                    "-filter:v", vf,
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "35",
                ])
                if bool(getattr(self, "include_audio_box", None) and self.include_audio_box.isChecked()):
                    af = f"areverse,asetpts=PTS-STARTPTS,{_gui_atempo_chain(speed)},aresample=async=1:first_pts=0"
                    args.extend([
                        "-map", f"0:a:{self.audio_index}?",
                        "-filter:a", af,
                        "-c:a", "aac",
                        "-b:a", "96k",
                    ])
                    if self.audio_count > 1:
                        self.status.setText(
                            f"Preview uses audio track {self.audio_index + 1}; export applies the same change to all audio tracks."
                        )
                else:
                    args.append("-an")
                args.extend(["-avoid_negative_ts", "make_zero", "-movflags", "+faststart"])
                args.append(str(out_path))
            else:
                out_path = Path(self._wave_temp.name) / f"reverse_preview_{generation}.m4a"
                af = f"areverse,asetpts=PTS-STARTPTS,{_gui_atempo_chain(speed)},aresample=async=1:first_pts=0"
                args = [
                    "-hide_banner", "-loglevel", "error", "-y",
                ]
                if window_start > 0:
                    args.extend(["-ss", _ffmpeg_float(window_start), "-noaccurate_seek"])
                args.extend([
                    "-t", _ffmpeg_float(preview_source_duration),
                    "-i", str(self.input_path),
                    "-map", f"0:a:{self.audio_index}",
                    "-vn",
                    "-sn",
                    "-dn",
                    "-filter:a", af,
                    "-c:a", "aac",
                    "-b:a", "96k",
                    "-avoid_negative_ts", "make_zero",
                    str(out_path),
                ])
            self._start_preview_process(out_path, args, generation, speed)

        def _reverse_preview_finished(self, path: Path, generation: int, rendered_speed: float):
            if getattr(self, "_closing", False):
                return
            if generation != self._preview_generation or not self.reverse_box.isChecked():
                return
            if not path.exists():
                self.status.setText("Reverse preview failed. Export command can still be created.")
                return
            old_preview_path = self._current_preview_path
            self._current_preview_path = path
            self._pending_preview_path = None
            self._using_rendered_preview = True
            self._preview_direction = "reverse"
            self._rendered_preview_speed = max(0.10, float(rendered_speed or self._speed()))
            if self._pending_reverse_source_ms is not None:
                source_ms = int(self._pending_reverse_source_ms)
            else:
                source_ms = int(self.position_slider.value())
            source_ms = max(0, min(int(round(self.duration * 1000)), source_ms))
            preview_ms = self._source_seconds_to_reverse_preview_ms(source_ms / 1000.0, self._rendered_preview_speed)
            resume_playing = (
                bool(self._pending_reverse_resume_playing)
                if self._reverse_seek_pending
                else bool(getattr(self, "_pending_preview_resume_playing", False))
            )
            self._reverse_seek_pending = False
            self._pending_reverse_source_ms = None
            self._pending_reverse_resume_playing = False
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.player.setPlaybackRate(1.0)
            self.position_slider.setRange(0, max(1, int(round(self.duration * 1000))))
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(source_ms)
            self.position_slider.blockSignals(False)
            self._update_time_label(source_ms)
            self._set_player_position_and_state(preview_ms, resume_playing, 1.0)
            self.status.setText(
                f"Speed {self._rendered_preview_speed * 100:.0f}% ({self._rendered_preview_speed:.2f}x) • "
                f"reverse preview segment {seconds_to_timecode(self._preview_source_start)} -> "
                f"{seconds_to_timecode(self._preview_source_start + self._preview_source_duration)}"
            )
            if old_preview_path is not None and Path(old_preview_path) != path:
                try:
                    Path(old_preview_path).unlink(missing_ok=True)
                except Exception:
                    pass
            self._cleanup_reverse_preview_files(keep_path=path)

        def _on_media_status_changed(self, status):
            if (
                status == QtMultimedia.QMediaPlayer.MediaStatus.EndOfMedia
                and self.reverse_box.isChecked()
                and self._using_rendered_preview
                and not self._reverse_seek_pending
                and not getattr(self, "_closing", False)
            ):
                source_ms = int(round(max(0.0, self._preview_source_start) * 1000.0))
                if source_ms <= 0:
                    return
                self._reverse_seek_pending = True
                self._pending_reverse_source_ms = source_ms
                self._pending_reverse_resume_playing = True
                self.position_slider.blockSignals(True)
                self.position_slider.setValue(max(0, min(self.position_slider.maximum(), source_ms)))
                self.position_slider.blockSignals(False)
                self._update_time_label(source_ms)
                self._schedule_reverse_preview(40)

        def _on_player_error(self, *_args):
            self.status.setText("Preview playback error. Export command can still be created.")

        def _on_playback_state(self, state):
            if self.kind == "video" and self.reverse_box.isChecked() and self._reverse_scrub_timer.isActive():
                self._update_play_button(True)
                return
            playing = state == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
            self._update_play_button(playing)

        def toggle_playback(self):
            if self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                if self.reverse_box.isChecked() and not self._using_rendered_preview:
                    self._pending_reverse_source_ms = int(self.position_slider.value())
                    self._pending_reverse_resume_playing = True
                    self._schedule_reverse_preview(20)
                    return
                self.player.play()

        def confirm(self):
            payload = {
                "status": "ok",
                "speed": self._speed(),
                "reverse": bool(self.reverse_box.isChecked()),
            }
            if self.kind == "video" and hasattr(self, "include_audio_box"):
                payload["include_audio"] = bool(self.include_audio_box.isChecked())
            elif self.kind == "video":
                payload["include_audio"] = False
            self.result = payload
            self.close()

        def cancel(self):
            self.result = {"status": "canceled"}
            self.close()

        def closeEvent(self, event):
            self._closing = True
            self._stop_reverse_scrub()
            self._end_reverse_audio_mute()
            self._clear_pending_reverse_preview_state()
            try:
                self.player.stop()
            except Exception:
                pass
            try:
                if self._preview_proc is not None:
                    try:
                        self._preview_proc.finished.disconnect()
                    except Exception:
                        pass
                    self._preview_proc.kill()
            except Exception:
                pass
            self._cleanup_reverse_preview_files()
            try:
                self._wave_temp.cleanup()
            except Exception:
                pass
            super().closeEvent(event)

    return SpeedEditorWindow(request, media_kind)


def build_audio_cut_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    QtMultimedia = _import_qt_multimedia()
    Qt = QtCore.Qt
    QUrl = QtCore.QUrl

    def _timeline_tick_label(seconds: float) -> str:
        seconds = max(0.0, float(seconds or 0.0))
        total = int(round(seconds))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _nice_tick_step(span: float) -> float:
        for step in (0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600):
            if span / step <= 10:
                return step
        return 7200

    class WaveformCutWidget(QtWidgets.QWidget):
        seek_requested = QtCore.Signal(float)
        cut_selected = QtCore.Signal(int)
        view_changed = QtCore.Signal()

        def __init__(self, duration):
            super().__init__()
            self.duration = max(0.001, float(duration or 0.0))
            self.playhead = 0.0
            self.in_marker = 0.0
            self.out_marker = min(5.0, self.duration)
            self.cut_ranges: list[tuple[float, float]] = []
            self.selected_cut = -1
            self.view_start = 0.0
            self.view_span = self.duration
            self._dragging_playhead = False
            self._drag_origin_y = 0.0
            self._drag_origin_span = self.view_span
            self.wave = None
            self.setMinimumHeight(260)
            self.setMouseTracking(True)
            self.setStyleSheet(
                f"background-color: {PALETTE['timeline_bg']};"
                f"border: 1px solid {PALETTE['border']}; border-radius: 8px;"
            )

        def set_waveform(self, path: Path):
            pix = QtGui.QPixmap(str(path))
            if not pix.isNull():
                self.wave = pix
                self.update()

        def set_playhead(self, value):
            self.playhead = max(0.0, min(self.duration, float(value)))
            self.update()

        def _inner_rect(self):
            return self.rect().adjusted(14, 28, -14, -36)

        def _clamp_view(self, start, span):
            min_span = min(self.duration, max(0.05, self.duration / 500.0))
            span = max(min_span, min(self.duration, float(span or self.duration)))
            start = max(0.0, min(max(0.0, self.duration - span), float(start or 0.0)))
            return start, span

        def set_view(self, start, span, emit=True):
            self.view_start, self.view_span = self._clamp_view(start, span)
            self.update()
            if emit:
                self.view_changed.emit()

        def fit_view(self):
            self.set_view(0.0, self.duration)

        def zoom_around(self, center_time, factor):
            old_span = max(0.001, self.view_span)
            new_span = old_span * max(0.05, float(factor or 1.0))
            center_time = max(0.0, min(self.duration, float(center_time or 0.0)))
            ratio = (center_time - self.view_start) / old_span
            ratio = max(0.0, min(1.0, ratio))
            self.set_view(center_time - new_span * ratio, new_span)

        def zoom_ratio(self):
            return max(1.0, self.duration / max(0.001, self.view_span))

        def _time_to_x(self, value):
            inner = self._inner_rect()
            visible = max(0.001, self.view_span)
            return inner.left() + inner.width() * ((max(0.0, min(self.duration, value)) - self.view_start) / visible)

        def _x_to_time(self, x):
            inner = self._inner_rect()
            return max(0.0, min(self.duration, self.view_start + (float(x) - inner.left()) / max(1, inner.width()) * self.view_span))

        def _cut_index_at(self, seconds):
            for idx, (start, end) in enumerate(normalize_ranges(self.cut_ranges, self.duration)):
                if start <= seconds <= end:
                    return idx
            return -1

        def mousePressEvent(self, event):
            t = self._x_to_time(event.position().x())
            if event.button() == Qt.RightButton:
                self.selected_cut = self._cut_index_at(t)
                self.cut_selected.emit(self.selected_cut)
                self.update()
                return
            if event.button() == Qt.LeftButton:
                self._dragging_playhead = True
                self._drag_origin_y = event.position().y()
                self._drag_origin_span = self.view_span
                self.set_playhead(t)
                self.seek_requested.emit(t)

        def mouseMoveEvent(self, event):
            if event.buttons() & Qt.LeftButton:
                t = self._x_to_time(event.position().x())
                self.set_playhead(t)
                self.seek_requested.emit(t)
                if self._dragging_playhead:
                    dy = event.position().y() - self._drag_origin_y
                    if abs(dy) >= 2:
                        factor = 2.0 ** (dy / 180.0)
                        self.set_view(t - (self._drag_origin_span * factor) * 0.5, self._drag_origin_span * factor)

        def mouseReleaseEvent(self, event):
            if event.button() == Qt.LeftButton:
                self._dragging_playhead = False
            super().mouseReleaseEvent(event)

        def wheelEvent(self, event):
            delta = event.angleDelta().y()
            if not delta:
                return
            steps = max(1, abs(delta) // 120)
            factor = (0.88 if delta > 0 else 1.14) ** steps
            self.zoom_around(self._x_to_time(event.position().x()), factor)
            event.accept()

        def paintEvent(self, _event):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.fillRect(self.rect(), QtGui.QColor(PALETTE["timeline_bg"]))
            inner = self._inner_rect()
            if self.wave is not None:
                sx = self.wave.width() * (self.view_start / self.duration)
                sw = self.wave.width() * (self.view_span / self.duration)
                src = QtCore.QRectF(sx, 0, max(1.0, sw), self.wave.height())
                p.drawPixmap(QtCore.QRectF(inner), self.wave, src)
            else:
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["text_mute"])))
                p.drawText(inner, Qt.AlignCenter, "Generating waveform...")

            for idx, (start, end) in enumerate(normalize_ranges(self.cut_ranges, self.duration)):
                if end < self.view_start or start > self.view_start + self.view_span:
                    continue
                x1 = self._time_to_x(start)
                x2 = self._time_to_x(end)
                color = QtGui.QColor(248, 81, 73, 145 if idx == self.selected_cut else 90)
                p.fillRect(QtCore.QRectF(x1, inner.top(), max(1, x2 - x1), inner.height()), color)
                if idx == self.selected_cut:
                    p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["danger_text"]), 2))
                    p.drawRect(QtCore.QRectF(x1, inner.top(), max(1, x2 - x1), inner.height()))

            step = _nice_tick_step(self.view_span)
            first_tick = math.ceil(self.view_start / step) * step
            t = first_tick
            p.setFont(QtGui.QFont("Segoe UI", 8))
            while t <= self.view_start + self.view_span + 1e-6:
                x = self._time_to_x(t)
                major = abs((t / step) % 2) < 1e-6
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["tick_hi"] if major else PALETTE["tick_lo"]), 1))
                p.drawLine(QtCore.QPointF(x, inner.bottom() + 2), QtCore.QPointF(x, inner.bottom() + (12 if major else 7)))
                if major:
                    p.drawText(QtCore.QRectF(x - 36, inner.bottom() + 13, 72, 16), Qt.AlignCenter, _timeline_tick_label(t))
                t += step

            for t, color in ((self.in_marker, PALETTE["marker_in"]), (self.out_marker, PALETTE["marker_out"])):
                if t < self.view_start or t > self.view_start + self.view_span:
                    continue
                x = self._time_to_x(t)
                p.setPen(QtGui.QPen(QtGui.QColor(color), 2))
                p.drawLine(QtCore.QPointF(x, inner.top() - 8), QtCore.QPointF(x, inner.bottom() + 8))

            x = self._time_to_x(self.playhead)
            if inner.left() - 20 <= x <= inner.right() + 20:
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["playhead"]), 2))
                p.drawLine(QtCore.QPointF(x, inner.top() - 12), QtCore.QPointF(x, inner.bottom() + 12))
            p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["text_dim"])))
            p.drawText(14, self.height() - 8, seconds_to_timecode(self.playhead))
            p.drawText(self.width() - 110, self.height() - 8, seconds_to_timecode(self.duration))

    class AudioCutWindow(QtWidgets.QMainWindow):
        def __init__(self, req):
            super().__init__()
            self.request = req
            self.input_path = Path(req["input_path"])
            self.ffmpeg = str(req.get("ffmpeg") or "ffmpeg")
            self.audio_index = int(req.get("audio_index", 0) or 0)
            self.duration = float(req.get("duration") or 0.0)
            self.result = {"status": "canceled", "keep_ranges": []}
            self._history = None
            self._restoring = False
            self._syncing_view_controls = False
            self._icon = _icon_loader(self, self.style())
            self._wave_temp = tempfile.TemporaryDirectory(prefix="ffmwiz_waveform_")
            self._wave_path = Path(self._wave_temp.name) / "waveform.png"
            self.setWindowTitle("FFmWiz Audio Cut Editor")
            _apply_window_icon(self, self._icon)
            self.setMinimumSize(1080, 620)
            self.resize(1240, 720)
            self._build_ui()
            self._history = HistoryStack(self._snapshot())
            self._update_undo_redo_state()
            self._setup_player()
            QtCore.QTimer.singleShot(80, self._load_media)
            QtCore.QTimer.singleShot(80, self._start_waveform)

        def _build_ui(self):
            central = QtWidgets.QWidget()
            central.setObjectName("central")
            self.setCentralWidget(central)
            root = QtWidgets.QVBoxLayout(central)
            root.setContentsMargins(14, 10, 14, 10)
            root.setSpacing(8)

            header = QtWidgets.QFrame()
            header.setObjectName("header")
            h = QtWidgets.QHBoxLayout(header)
            h.setContentsMargins(14, 10, 14, 10)
            title = QtWidgets.QLabel("FFmWiz Audio Cut Editor")
            title.setObjectName("title")
            h.addWidget(title)
            h.addStretch(1)
            info = QtWidgets.QLabel(f"Duration {seconds_to_timecode(self.duration)}      •      Source  {self.input_path.name}")
            info.setObjectName("headerInfo")
            h.addWidget(info)
            root.addWidget(header)

            self.waveform = WaveformCutWidget(self.duration)
            self.waveform.seek_requested.connect(self.seek)
            self.waveform.cut_selected.connect(self._on_waveform_cut_selected)
            self.waveform.view_changed.connect(self._sync_view_controls)
            root.addWidget(self.waveform, 1)

            view_row = QtWidgets.QHBoxLayout()
            view_row.setSpacing(8)
            nav_lbl = QtWidgets.QLabel("Waveform view")
            nav_lbl.setObjectName("controlLabel")
            view_row.addWidget(nav_lbl)
            view_frame = QtWidgets.QFrame()
            view_frame.setObjectName("timelineViewFrame")
            view_frame.setMinimumWidth(620)
            view_frame.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            vf_layout = QtWidgets.QHBoxLayout(view_frame)
            vf_layout.setContentsMargins(3, 1, 3, 1)
            vf_layout.setSpacing(3)
            self.btn_view_left = QtWidgets.QPushButton("◀")
            self.btn_view_left.setObjectName("timelineViewArrow")
            self.btn_view_left.setFixedSize(14, 14)
            self.btn_view_left.setAutoRepeat(True)
            self.btn_view_left.setAutoRepeatDelay(220)
            self.btn_view_left.setAutoRepeatInterval(70)
            self.btn_view_left.clicked.connect(lambda: self._nudge_view(-1))
            vf_layout.addWidget(self.btn_view_left)
            self.view_scroll = QtWidgets.QScrollBar(Qt.Horizontal)
            self.view_scroll.setObjectName("timelineViewScroll")
            self.view_scroll.setTracking(True)
            self.view_scroll.valueChanged.connect(self._on_view_scroll)
            vf_layout.addWidget(self.view_scroll, 1)
            self.btn_view_right = QtWidgets.QPushButton("▶")
            self.btn_view_right.setObjectName("timelineViewArrow")
            self.btn_view_right.setFixedSize(14, 14)
            self.btn_view_right.setAutoRepeat(True)
            self.btn_view_right.setAutoRepeatDelay(220)
            self.btn_view_right.setAutoRepeatInterval(70)
            self.btn_view_right.clicked.connect(lambda: self._nudge_view(1))
            vf_layout.addWidget(self.btn_view_right)
            view_row.addWidget(view_frame, 1)
            root.addLayout(view_row)

            zoom_row = QtWidgets.QHBoxLayout()
            zoom_row.setSpacing(8)
            zoom_lbl = QtWidgets.QLabel("Waveform zoom")
            zoom_lbl.setObjectName("controlLabel")
            zoom_row.addWidget(zoom_lbl)
            self.btn_zoom_left = QtWidgets.QPushButton("◀")
            self.btn_zoom_left.setObjectName("timelineZoomArrow")
            self.btn_zoom_left.setFixedSize(14, 14)
            self.btn_zoom_left.setAutoRepeat(True)
            self.btn_zoom_left.setAutoRepeatDelay(220)
            self.btn_zoom_left.setAutoRepeatInterval(70)
            self.btn_zoom_left.clicked.connect(lambda: self._nudge_zoom(-1))
            zoom_row.addWidget(self.btn_zoom_left)
            self.zoom_slider = QtWidgets.QSlider(Qt.Horizontal)
            self.zoom_slider.setObjectName("timelineZoomSlider")
            self.zoom_slider.setRange(0, 100)
            self.zoom_slider.setValue(0)
            self.zoom_slider.setTracking(True)
            self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
            zoom_row.addWidget(self.zoom_slider, 1)
            self.btn_zoom_right = QtWidgets.QPushButton("▶")
            self.btn_zoom_right.setObjectName("timelineZoomArrow")
            self.btn_zoom_right.setFixedSize(14, 14)
            self.btn_zoom_right.setAutoRepeat(True)
            self.btn_zoom_right.setAutoRepeatDelay(220)
            self.btn_zoom_right.setAutoRepeatInterval(70)
            self.btn_zoom_right.clicked.connect(lambda: self._nudge_zoom(1))
            zoom_row.addWidget(self.btn_zoom_right)
            root.addLayout(zoom_row)

            tip = QtWidgets.QLabel(
                "Tip: Mark ranges you want removed. Right-click a cut region to select it. Drag the CTI upward to zoom."
            )
            tip.setObjectName("tip")
            tip.setWordWrap(True)
            root.addWidget(tip)

            row = QtWidgets.QHBoxLayout()
            row.setSpacing(8)
            self.btn_play = QtWidgets.QPushButton(self._icon("play", QtWidgets.QStyle.SP_MediaPlay), " Play (Space)")
            self.btn_play.setObjectName("primary")
            self.btn_play.clicked.connect(self.toggle_playback)
            row.addWidget(self.btn_play)
            self.btn_undo = self._button("Undo (Ctrl+Z)", self._undo)
            row.addWidget(self.btn_undo)
            self.btn_redo = self._button("Redo (Ctrl+Y)", self._redo)
            row.addWidget(self.btn_redo)
            row.addWidget(self._button("Mark In (I)", self.mark_in, "markIn"))
            row.addWidget(self._button("Mark Out (O)", self.mark_out, "markOut"))
            row.addWidget(self._button("Add Cut (A)", self.add_cut, "green"))
            self.btn_invert = self._button("Invert Cuts (Ctrl+Shift+I)", self.invert_cuts, "purple")
            self.btn_invert.setToolTip("Invert cut ranges: keep the currently selected cut ranges and remove everything else.")
            row.addWidget(self.btn_invert)
            self.btn_delete_selected = self._button("Delete Selected Cut (Del)", self.delete_selected_cut, "dangerCut")
            row.addWidget(self.btn_delete_selected)
            row.addWidget(self._button("Delete All Cuts", self.delete_all_cuts, "danger"))
            row.addStretch(1)
            self.status = QtWidgets.QLabel("")
            self.status.setObjectName("status")
            row.addWidget(self.status)
            root.addLayout(row)

            self.cut_list = QtWidgets.QListWidget()
            self.cut_list.setMinimumHeight(120)
            self.cut_list.itemSelectionChanged.connect(self._on_cut_list_select)
            root.addWidget(self.cut_list)

            bottom = QtWidgets.QHBoxLayout()
            bottom.addStretch(1)
            cancel = QtWidgets.QPushButton("Cancel (Esc)")
            cancel.setObjectName("danger")
            cancel.clicked.connect(self.cancel)
            bottom.addWidget(cancel)
            apply = QtWidgets.QPushButton(self._icon("check", QtWidgets.QStyle.SP_DialogOkButton), " Confirm (Enter)")
            apply.setObjectName("primary")
            apply.clicked.connect(self.confirm)
            bottom.addWidget(apply)
            root.addLayout(bottom)

            QtGui.QShortcut(QtGui.QKeySequence("Space"), self, activated=self.toggle_playback)
            QtGui.QShortcut(QtGui.QKeySequence("I"), self, activated=self.mark_in)
            QtGui.QShortcut(QtGui.QKeySequence("O"), self, activated=self.mark_out)
            QtGui.QShortcut(QtGui.QKeySequence("A"), self, activated=self.add_cut)
            QtGui.QShortcut(QtGui.QKeySequence("Delete"), self, activated=self.delete_selected_cut)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Z"), self, activated=self._undo)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Y"), self, activated=self._redo)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+Z"), self, activated=self._redo)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+I"), self, activated=self.invert_cuts)
            QtGui.QShortcut(QtGui.QKeySequence("Esc"), self, activated=self.cancel)
            QtGui.QShortcut(QtGui.QKeySequence("Return"), self, activated=self.confirm)
            QtGui.QShortcut(QtGui.QKeySequence("Enter"), self, activated=self.confirm)
            self._sync_view_controls()
            self._refresh()

        def _button(self, label, slot, object_name=None):
            btn = QtWidgets.QPushButton(label)
            if object_name:
                btn.setObjectName(object_name)
            btn.clicked.connect(slot)
            return btn

        def _setup_player(self):
            self.player = QtMultimedia.QMediaPlayer(self)
            self.audio = QtMultimedia.QAudioOutput(self)
            self.audio.setVolume(0.7)
            self.player.setAudioOutput(self.audio)
            self.player.positionChanged.connect(lambda ms: self.waveform.set_playhead(ms / 1000.0))
            self.player.playbackStateChanged.connect(self._on_playback_state)

        def _load_media(self):
            self.player.setSource(QUrl.fromLocalFile(str(self.input_path)))
            self.player.pause()

        def _start_waveform(self):
            args = [
                "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(self.input_path),
                "-filter_complex", f"[0:a:{self.audio_index}]aformat=channel_layouts=mono,showwavespic=s=1800x320:colors=79b4ff[wave]",
                "-map", "[wave]",
                "-frames:v", "1",
                "-c:v", "png",
                str(self._wave_path),
            ]
            self.wave_proc = QtCore.QProcess(self)
            self.wave_proc.finished.connect(self._waveform_finished)
            self.wave_proc.start(self.ffmpeg, args)

        def _waveform_finished(self, *_args):
            if self._wave_path.exists():
                self.waveform.set_waveform(self._wave_path)
            else:
                self.status.setText("Waveform generation failed.")

        def _on_playback_state(self, state):
            playing = state == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
            self.btn_play.setText(" Pause (Space)" if playing else " Play (Space)")
            self.btn_play.setIcon(self._icon("pause" if playing else "play", QtWidgets.QStyle.SP_MediaPause if playing else QtWidgets.QStyle.SP_MediaPlay))

        def seek(self, seconds):
            self.player.setPosition(int(max(0.0, min(self.duration, seconds)) * 1000))

        def toggle_playback(self):
            if self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()

        def mark_in(self):
            self.waveform.in_marker = self.waveform.playhead
            if self.waveform.out_marker <= self.waveform.in_marker:
                self.waveform.out_marker = min(self.duration, self.waveform.in_marker + 1.0)
            self._commit_history()
            self._refresh()

        def mark_out(self):
            self.waveform.out_marker = self.waveform.playhead
            if self.waveform.out_marker <= self.waveform.in_marker:
                self.waveform.in_marker = max(0.0, self.waveform.out_marker - 1.0)
            self._commit_history()
            self._refresh()

        def add_cut(self):
            start = min(self.waveform.in_marker, self.waveform.out_marker)
            end = max(self.waveform.in_marker, self.waveform.out_marker)
            if end <= start:
                return
            self.waveform.cut_ranges = normalize_ranges(self.waveform.cut_ranges + [(start, end)], self.duration)
            self.waveform.selected_cut = len(self.waveform.cut_ranges) - 1
            self._commit_history()
            self._refresh()

        def delete_selected_cut(self):
            idx = self.waveform.selected_cut
            ranges = normalize_ranges(self.waveform.cut_ranges, self.duration)
            if idx < 0 or idx >= len(ranges):
                return
            del ranges[idx]
            self.waveform.cut_ranges = ranges
            self.waveform.selected_cut = -1
            self._commit_history()
            self._refresh()

        def delete_all_cuts(self):
            if not self.waveform.cut_ranges:
                return
            self.waveform.cut_ranges = []
            self.waveform.selected_cut = -1
            self._commit_history()
            self._refresh()

        def invert_cuts(self):
            if self.duration <= 0:
                QtWidgets.QMessageBox.critical(self, "Unknown duration", "Audio duration is unknown, so cuts cannot be inverted.")
                return
            if not self.waveform.cut_ranges:
                QtWidgets.QMessageBox.warning(self, "No cuts", "Add at least one cut range before inverting.")
                return
            self.waveform.cut_ranges = invert_cut_ranges(self.waveform.cut_ranges, self.duration)
            self.waveform.selected_cut = -1
            self._commit_history()
            self._refresh()

        def _snapshot(self):
            return {
                "ranges": list(normalize_ranges(self.waveform.cut_ranges, self.duration)),
                "in_marker": float(self.waveform.in_marker),
                "out_marker": float(self.waveform.out_marker),
                "selected_cut": int(self.waveform.selected_cut),
            }

        def _commit_history(self):
            if self._restoring or self._history is None:
                return
            self._history.push(self._snapshot())
            self._update_undo_redo_state()

        def _restore_snapshot(self, snap):
            self._restoring = True
            try:
                self.waveform.cut_ranges = list(snap.get("ranges") or [])
                self.waveform.in_marker = float(snap.get("in_marker", 0.0))
                self.waveform.out_marker = float(snap.get("out_marker", min(5.0, self.duration)))
                self.waveform.selected_cut = int(snap.get("selected_cut", -1))
            finally:
                self._restoring = False
            self._refresh()

        def _undo(self):
            if self._history is None:
                return
            snap = self._history.undo()
            if snap is not None:
                self._restore_snapshot(snap)

        def _redo(self):
            if self._history is None:
                return
            snap = self._history.redo()
            if snap is not None:
                self._restore_snapshot(snap)

        def _update_undo_redo_state(self):
            if hasattr(self, "btn_undo"):
                self.btn_undo.setEnabled(bool(self._history and self._history.can_undo()))
            if hasattr(self, "btn_redo"):
                self.btn_redo.setEnabled(bool(self._history and self._history.can_redo()))
            if hasattr(self, "btn_delete_selected"):
                self.btn_delete_selected.setEnabled(0 <= self.waveform.selected_cut < len(normalize_ranges(self.waveform.cut_ranges, self.duration)))

        def _on_waveform_cut_selected(self, idx):
            self.waveform.selected_cut = idx
            self._refresh()

        def _on_cut_list_select(self):
            items = self.cut_list.selectedIndexes()
            self.waveform.selected_cut = items[0].row() if items else -1
            self.waveform.update()
            self._update_undo_redo_state()

        def _zoom_value_from_span(self):
            ratio = self.waveform.zoom_ratio()
            max_ratio = 64.0
            return int(round(max(0.0, min(1.0, math.log(ratio, max_ratio))) * 100.0))

        def _span_from_zoom_value(self, value):
            max_ratio = 64.0
            ratio = max_ratio ** (max(0, min(100, int(value))) / 100.0)
            return self.waveform.duration / ratio

        def _sync_view_controls(self):
            if self._syncing_view_controls:
                return
            self._syncing_view_controls = True
            try:
                max_value = max(0, int(round((self.waveform.duration - self.waveform.view_span) * 1000.0)))
                page = max(1, int(round(self.waveform.view_span * 1000.0)))
                self.view_scroll.setRange(0, max_value)
                self.view_scroll.setPageStep(page)
                self.view_scroll.setSingleStep(max(1, page // 20))
                self.view_scroll.setValue(int(round(self.waveform.view_start * 1000.0)))
                self.zoom_slider.setValue(self._zoom_value_from_span())
            finally:
                self._syncing_view_controls = False

        def _on_view_scroll(self, value):
            if self._syncing_view_controls:
                return
            self.waveform.set_view(float(value) / 1000.0, self.waveform.view_span, emit=False)
            self.waveform.update()

        def _on_zoom_slider(self, value):
            if self._syncing_view_controls:
                return
            center = self.waveform.view_start + self.waveform.view_span * 0.5
            span = self._span_from_zoom_value(value)
            self.waveform.set_view(center - span * 0.5, span)

        def _nudge_view(self, direction):
            self.waveform.set_view(
                self.waveform.view_start + direction * max(0.05, self.waveform.view_span * 0.10),
                self.waveform.view_span,
            )

        def _nudge_zoom(self, direction):
            self.zoom_slider.setValue(max(0, min(100, self.zoom_slider.value() + direction * 4)))

        def _refresh(self):
            self.waveform.update()
            self.status.setText(
                f"In {seconds_to_timecode(self.waveform.in_marker)}  •  "
                f"Out {seconds_to_timecode(self.waveform.out_marker)}  •  "
                f"Cuts {len(self.waveform.cut_ranges)}"
            )
            self.cut_list.blockSignals(True)
            self.cut_list.clear()
            for idx, (start, end) in enumerate(self.waveform.cut_ranges, start=1):
                item = QtWidgets.QListWidgetItem(f"{idx}. remove {seconds_to_timecode(start)} -> {seconds_to_timecode(end)}")
                self.cut_list.addItem(item)
                if idx - 1 == self.waveform.selected_cut:
                    item.setSelected(True)
            self.cut_list.blockSignals(False)
            self._sync_view_controls()
            self._update_undo_redo_state()

        def confirm(self):
            if not self.waveform.cut_ranges:
                QtWidgets.QMessageBox.warning(self, "No cuts", "Add at least one audio cut range or cancel.")
                return
            keep = invert_cuts_to_keep(self.waveform.cut_ranges, self.duration)
            self.result = {"status": "ok", "keep_ranges": keep}
            self.close()

        def cancel(self):
            self.result = {"status": "canceled", "keep_ranges": []}
            self.close()

        def closeEvent(self, event):
            try:
                self.player.stop()
            except Exception:
                pass
            try:
                self._wave_temp.cleanup()
            except Exception:
                pass
            super().closeEvent(event)

    return AudioCutWindow(request)


# =====================================================================
# Entry point: JSON IPC dispatcher
# =====================================================================


def _write_reply(reply_path: Path, payload: dict[str, Any]) -> None:
    reply_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    global _GUI_LOG_PATH, _PARENT_PID
    parser = argparse.ArgumentParser(description="FFmWiz GUI (PySide6)")
    parser.add_argument("--request", required=True, help="Path to request JSON.")
    parser.add_argument("--reply", required=True, help="Path to write reply JSON.")
    args = parser.parse_args()

    request_path = Path(args.request)
    reply_path = Path(args.reply)
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _write_reply(reply_path, {"status": "error", "message": f"Bad request JSON: {exc}"})
        return 2
    if request.get("log_path"):
        try:
            _GUI_LOG_PATH = Path(request["log_path"])
        except Exception:
            _GUI_LOG_PATH = None
    try:
        _PARENT_PID = int(request.get("parent_pid") or 0) or None
    except Exception:
        _PARENT_PID = None

    _set_windows_app_id()
    try:
        from PySide6.QtWidgets import QApplication  # type: ignore
        from PySide6 import QtCore  # type: ignore
    except Exception as exc:
        _write_reply(reply_path, {"status": "error",
                                  "message": f"PySide6 not installed: {exc}"})
        return 3

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("FFmWiz")
    app.setApplicationDisplayName("FFmWiz")
    try:
        app.setDesktopFileName(APP_ID)
    except Exception:
        pass
    _set_qt_application_icon(app)
    _install_parent_watchdog(app)

    mode = request.get("mode")
    gui_start = time.perf_counter()
    try:
        if mode == "cut":
            window = build_cut_editor(request)
        elif mode == "crop":
            window = build_crop_editor(request)
        elif mode == "video_speed":
            window = build_speed_editor(request, "video")
        elif mode == "audio_speed":
            window = build_speed_editor(request, "audio")
        elif mode == "audio_cut":
            window = build_audio_cut_editor(request)
        else:
            _write_reply(reply_path, {"status": "error",
                                      "message": f"Unknown mode: {mode}"})
            return 2
    except Exception as exc:
        import traceback
        _write_reply(reply_path, {
            "status": "error",
            "message": f"GUI init failed: {exc}",
            "traceback": traceback.format_exc(),
        })
        return 4

    _apply_native_windows_icon(window)
    _gui_log_debug(
        f"{mode} GUI window built in {time.perf_counter() - gui_start:.3f}s",
        force=True,
    )
    window.show()
    _apply_native_windows_icon(window)

    def apply_deferred_stylesheet():
        style_start = time.perf_counter()
        app.setStyleSheet(QSS)
        _gui_log_debug(
            f"Qt stylesheet applied in {time.perf_counter() - style_start:.3f}s",
            force=True,
        )
        _apply_native_windows_icon(window)
        _gui_log_debug(
            f"{mode} GUI init completed in {time.perf_counter() - gui_start:.3f}s",
            force=True,
        )

    QtCore.QTimer.singleShot(0, apply_deferred_stylesheet)
    app.exec()

    payload = getattr(window, "result", {"status": "canceled"})
    _write_reply(reply_path, payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())


























