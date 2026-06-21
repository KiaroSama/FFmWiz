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
        "mode": "cut" | "crop" | "video_speed" | "video_unified" | "audio_cut" | "audio_speed",
        "input_path": "<absolute path to the source media>",
        "fps": 30.0,
        "duration": 123.456,
        // mode-specific fields (see build_cut_editor / build_crop_editor)
    }

Reply JSON contract:
    cut         -> {"status": "ok"|"canceled", "keep_ranges": [[start_s, end_s], ...]}
    crop        -> {"status": "ok"|"canceled", "margins": [top, left, right, bottom]}
    video_speed -> {"status": "ok"|"canceled", "speed": 1.25, "reverse": false, "include_audio": true}
    video_unified -> {"status": "ok"|"canceled", "margins": [...], "keep_ranges": [...], "separator_points": [...], "speed": 1.25, "reverse": false, "include_audio": true}
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
# This file lives in <project>/assets/runtime/, so the asset root is its parent's
# parent (<project>/assets/) and icons live under <project>/assets/icons/.
ASSETS_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ASSETS_ROOT / "icons"
_GUI_LOG_PATH: Path | None = None
_APP_QICON_CACHE: Any = None
_PARENT_PID: int | None = None


def _debug_enabled() -> bool:
    return bool(os.environ.get("FFMWIZ_DEBUG") or os.environ.get("FFMWIZ_DEBUG_GUI"))


# Secret/credential redaction so GUI log lines (appended to the shared FFmWiz
# log file) never leak tokens. Conservative: only known sensitive keys, Bearer
# tokens, and URL credentials are masked; ordinary text is left intact.
_GUI_SECRET_KEY_RE = re.compile(
    r"(?i)\b(pass(?:word|wd)?|tokens?|api[_-]?keys?|secrets?|access[_-]?tokens?|"
    r"refresh[_-]?tokens?|client[_-]?secrets?|private[_-]?keys?|authorization|"
    r"signing[_-]?secret|webhook[_-]?secret)\b(\s*[:=]\s*|\s+)([^\s,;\"']+)"
)
_GUI_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_GUI_URL_CRED_RE = re.compile(r"://([^:@/\s]+):([^@/\s]+)@")


def _gui_redact_secrets(text: object) -> str:
    if text is None:
        return ""
    out = str(text)
    try:
        out = _GUI_BEARER_RE.sub("Bearer [REDACTED]", out)
        out = _GUI_SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", out)
        out = _GUI_URL_CRED_RE.sub(r"://\1:[REDACTED]@", out)
    except Exception:
        return out
    return out


def _gui_write_log(level: str, message: str) -> bool:
    """Append one structured, UTC, redacted line to the shared FFmWiz log file.
    Format matches FFmWiz: '[YYYY-MM-DD HH:mm:ss UTC] [LEVEL] [GUI] message'.
    Returns True when written to the file."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    line = f"[{stamp} UTC] [{level}] [GUI] {_gui_redact_secrets(message)}"
    if _GUI_LOG_PATH is not None:
        try:
            with _GUI_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            return True
        except Exception:
            pass
    return False


def _gui_log_debug(message: str, *, force: bool = False) -> None:
    debug_enabled = _debug_enabled()
    if not force and not debug_enabled:
        return
    if _gui_write_log("DEBUG", message):
        return
    if debug_enabled or force:
        print(f"GUI DEBUG: {_gui_redact_secrets(message)}", file=sys.stderr)


def _gui_log_info(message: str) -> None:
    # Important GUI lifecycle events are always recorded in the shared log.
    if not _gui_write_log("INFO", message) and _debug_enabled():
        print(f"GUI INFO: {_gui_redact_secrets(message)}", file=sys.stderr)


def _gui_log_error(message: str) -> None:
    if not _gui_write_log("ERROR", message):
        print(f"GUI ERROR: {_gui_redact_secrets(message)}", file=sys.stderr)


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
    "chapter":         "#b77dff",
    "chapter_text":    "#d9bdff",
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
    "playhead":        "#ff4d55",
    "playhead_halo":   "#2f81f7",
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
QLabel#timelineControlLabel {{ color: {PALETTE['text']}; font-size: 12px; font-weight: 700; }}
QLabel#sectionLabel {{ color: {PALETTE['accent_text']}; font-size: 12px; font-weight: 700; }}
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

QPushButton#overlayToggle {{
    background-color: #765407;
    color: {PALETTE['text_on_accent']};
    border-color: #9b7417;
    border-radius: 7px;
    font-weight: 500;
}}

QPushButton#separator {{
    background-color: #075985;
    color: {PALETTE['text_on_accent']};
    border-color: #0ea5e9;
    font-weight: 700;
}}
QPushButton#separator:hover {{
    background-color: #0369a1;
    border-color: #38bdf8;
}}
QPushButton#separator:pressed {{
    background-color: #064e78;
}}
QPushButton#convertMarker {{
    background-color: #243449;
    color: #e8f2ff;
    border-color: #5aa9ff;
    font-weight: 700;
}}
QPushButton#convertMarker:hover {{
    background-color: #2f4561;
    border-color: #8cc7ff;
}}
QPushButton#convertMarker:disabled {{
    background-color: #182331;
    color: #7790aa;
    border-color: #30445d;
}}
QPushButton#overlayToggle:hover {{
    background-color: #86610b;
    border-color: #b18420;
}}
QPushButton#overlayToggle:pressed {{
    background-color: #5c4104;
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
QPushButton#timelineViewArrow:hover {{
    background-color: #182943;
    color: {PALETTE['accent_text']};
}}
QPushButton#timelineViewArrow:pressed {{
    background-color: #1f3a66;
}}
QPushButton#timelineViewArrow:disabled {{
    background-color: #111820;
    color: #23466f;
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
    background: #101820;
    border: 1px solid #667585;
    border-radius: 10px;
}}

QFrame#markerPanel {{
    background-color: {PALETTE['panel']};
    border: 1px solid #506071;
    border-radius: 8px;
}}

QFrame#inlineControlFrame {{
    background: #101820;
    border: 1px solid #667585;
    border-radius: 10px;
}}

QSlider#timelineViewSlider::groove:horizontal {{
    background: {PALETTE['timeline_track']};
    height: 5px;
    border-radius: 3px;
}}
QSlider#timelineViewSlider::sub-page:horizontal {{
    background: {PALETTE['timeline_track']};
    border-radius: 3px;
}}
QSlider#timelineViewSlider::handle:horizontal {{
    background: #8cff9d;
    width: 54px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
    border: 1px solid #2ea043;
}}
QSlider#timelineViewSlider::handle:horizontal:hover {{ background: #c7ffd0; }}

QSlider#cutVolumeSlider::groove:horizontal {{
    background: {PALETTE['timeline_track']};
    height: 6px;
    border-radius: 3px;
}}
QSlider#cutVolumeSlider::sub-page:horizontal {{
    background: {PALETTE['accent']};
    border-radius: 3px;
}}
QSlider#cutVolumeSlider::handle:horizontal {{
    background: {PALETTE['accent_hover']};
    width: 28px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
    border: 1px solid {PALETTE['accent']};
}}
QSlider#cutVolumeSlider::handle:horizontal:hover {{ background: {PALETTE['text']}; }}

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
QSpinBox#cropNumberField::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 15px;
    background-color: {PALETTE['surface']};
    border-left: 1px solid {PALETTE['border_soft']};
    border-top-right-radius: 6px;
}}
QSpinBox#cropNumberField::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 15px;
    background-color: {PALETTE['surface']};
    border-left: 1px solid {PALETTE['border_soft']};
    border-bottom-right-radius: 6px;
}}
QSpinBox#cropNumberField::up-button:hover, QSpinBox#cropNumberField::down-button:hover {{
    background-color: {PALETTE['surface_hover']};
}}
QSpinBox#cropNumberField::up-arrow {{
    image: url("{(ASSETS_DIR / 'spin_up.svg').as_posix()}");
    width: 9px; height: 9px;
}}
QSpinBox#cropNumberField::down-arrow {{
    image: url("{(ASSETS_DIR / 'spin_down.svg').as_posix()}");
    width: 9px; height: 9px;
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
    background-color: {PALETTE['accent']};
    min-height: 24px;
    border-radius: 5px;
}}
QComboBox QAbstractItemView QScrollBar::handle:vertical:hover {{
    background-color: {PALETTE['accent_hover']};
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

QSplitter::handle {{
    background-color: {PALETTE['border_soft']};
    border-radius: 3px;
}}
QSplitter::handle:vertical {{
    height: 7px;
    margin: 1px 28px;
}}
QSplitter::handle:hover {{
    background-color: {PALETTE['accent_dim']};
}}

QFrame#timelineResizeGrip {{
    background-color: #16212c;
    border: 1px solid #506071;
    border-radius: 4px;
}}
QFrame#timelineResizeGrip:hover {{
    background-color: #1e3144;
    border-color: {PALETTE['accent_hover']};
}}

QFrame#zoomPercentBox {{
    background-color: {PALETTE['surface']};
    border: 1px solid {PALETTE['border']};
    border-radius: 6px;
    max-height: 34px;
}}
QFrame#zoomPercentBox:hover {{
    background-color: {PALETTE['surface_hover']};
    border-color: {PALETTE['border_strong']};
}}
QComboBox#zoomPercentCombo {{
    background-color: transparent;
    border: none;
    padding: 4px 22px 4px 8px;
    min-height: 20px;
    max-height: 30px;
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
# PREVIEW BUILD ONLY: a compact stylesheet override appended on top of QSS.
# It shrinks button/combo/spin heights, paddings and fonts in the top control
# panels so the toolbars take far less vertical room and the video preview
# (inside the splitter) keeps the freed space. Production QSS is untouched.
# =====================================================================
PREVIEW_COMPACT_QSS = f"""
QPushButton {{ padding: 4px 9px; min-height: 18px; }}
QLabel#tip {{ padding: 4px 7px; }}
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
        chapters: list = field(default_factory=list)
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

        def set_chapters(self, chapters):
            self.state.chapters = list(chapters or [])
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

            for chapter in self.state.chapters:
                cs = float(chapter.get("start", 0.0))
                if cs < start_t or cs > end_t:
                    continue
                x = self._time_to_x(cs)
                painter.setPen(QPen(QColor(PALETTE["purple_hover"]), 1, Qt.DashLine))
                painter.drawLine(QPointF(x, track_top), QPointF(x, track_bottom))
                title = str(chapter.get("title") or "Chapter")
                label = short_gui_label(title, 24)
                painter.setFont(QFont("Segoe UI Semibold", 8))
                painter.setPen(QPen(QColor(PALETTE["accent_text"]), 1))
                painter.drawText(
                    QRectF(max(self.PAD, min(x + 4, w - self.PAD - 150)), track_top + 4, 150, 18),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    label,
                )

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
            self.chapters = normalize_chapters(request.get("chapters") or [], self.duration)
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
            self.timeline.set_chapters(self.chapters)
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
            self.volume_slider.setFixedWidth(130)
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
            self.result = {"status": "ok", "margins": snap_crop_margins_even(self.canvas.margins, int(self.source_w), int(self.source_h))}
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
            # Guard with getattr: the player attribute does not exist until this
            # runs (other methods intentionally use hasattr(self, "player")), so a
            # direct `self.player` read here raised AttributeError and the editor
            # failed to initialize (notably for audio inputs).
            if getattr(self, "player", None) is not None:
                return
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
            self.chapters: list[dict[str, Any]] = []
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
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
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
            # AudioCutWindow has no inline preview-zoom/crop/speed text editors,
            # so Return/Enter simply confirm. (Binding to the video editor's
            # _confirm_or_apply_text_editor here previously crashed init.)
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
                self._stop_preview_frame_worker()
            except Exception:
                pass
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
# Unified experimental editor shells.
# These live on the feature/unified-editors branch and embed the existing
# editor panels instead of deleting the archived standalone editors.
# =====================================================================


def _hide_embedded_editor_actions(root_widget: Any) -> None:
    try:
        from PySide6 import QtWidgets  # type: ignore
        for button in root_widget.findChildren(QtWidgets.QPushButton):
            text = str(button.text() or "").lower()
            if "apply" in text or "confirm" in text or "cancel" in text:
                button.hide()
    except Exception:
        pass


def _stop_embedded_editor(editor: Any) -> None:
    for attr in ("player", "wave_proc", "_preview_proc"):
        obj = getattr(editor, attr, None)
        if obj is None:
            continue
        try:
            obj.stop()
        except Exception:
            try:
                obj.kill()
            except Exception:
                pass
        try:
            obj.waitForFinished(1000)
        except Exception:
            pass
    try:
        editor._stop_worker()
    except Exception:
        pass


def build_unified_video_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    # PERF: QtMultimedia pulls in the native multimedia backend (Qt6Multimedia +
    # the bundled FFmpeg backend DLLs), which is the single most expensive cold
    # load on first launch. Defer it so it does NOT block building/showing the
    # window; it is imported lazily inside _setup_player (which itself runs ~120ms
    # AFTER the window is shown). Every QtMultimedia use lives in player callbacks
    # that only run once _setup_player has created self.player, so this is safe.
    QtMultimedia = None
    Qt = QtCore.Qt
    Signal = QtCore.Signal
    QPointF = QtCore.QPointF
    QRectF = QtCore.QRectF
    QMainWindow = QtWidgets.QMainWindow
    QWidget = QtWidgets.QWidget
    QVBoxLayout = QtWidgets.QVBoxLayout
    QHBoxLayout = QtWidgets.QHBoxLayout
    QGridLayout = QtWidgets.QGridLayout
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QSlider = QtWidgets.QSlider
    QComboBox = QtWidgets.QComboBox
    QSpinBox = QtWidgets.QSpinBox
    QCheckBox = QtWidgets.QCheckBox
    QScrollBar = QtWidgets.QScrollBar
    QSplitter = QtWidgets.QSplitter
    QFrame = QtWidgets.QFrame
    QStyle = QtWidgets.QStyle
    QSizePolicy = QtWidgets.QSizePolicy
    QAction = QtGui.QAction

    @dataclass
    class UnifiedSnapshot:
        margins: tuple[int, int, int, int] = (0, 0, 0, 0)
        cut_ranges: tuple[tuple[float, float], ...] = ()
        separators: tuple[float, ...] = ()
        mark_in: float | None = None
        mark_out: float | None = None
        speed: float = 1.0
        reverse: bool = False
        include_audio: bool = True

    class UnifiedPreviewCanvas(QWidget):
        margins_changed = Signal()
        edit_finished = Signal()
        seek_requested = Signal(float)
        toggle_playback_requested = Signal()
        zoom_changed = Signal(float)

        def __init__(self, source_w: int, source_h: int, duration: float):
            super().__init__()
            self.source_w = max(1, int(source_w or 1920))
            self.source_h = max(1, int(source_h or 1080))
            self.duration = max(0.0, float(duration or 0.0))
            self.image = None
            self.margins = [0, 0, 0, 0]
            self.zoom = 1.0
            self._scroll = QPointF(0, 0)
            self._press_pos = None
            self._pan_origin = None
            self._scroll_origin = QPointF(0, 0)
            self._drag_handle = None
            self._move_origin = None
            self._move_margins = None
            self._tool = "hand"
            self._zoom_origin = None
            self._zoom_start = 1.0
            self._zoom_focus_source = (self.source_w / 2.0, self.source_h / 2.0)
            self._zoom_focus_screen = None
            self._toggle_click_candidate = False
            self._zoom_out_mode = False
            self._show_crop_overlay = True
            self._zoom_in_cursor = self._make_zoom_cursor(False)
            self._zoom_out_cursor = self._make_zoom_cursor(True)
            self.setMouseTracking(True)
            self.setMinimumSize(520, 260)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setStyleSheet(
                f"background-color: {PALETTE['timeline_bg']};"
                f"border: 1px solid {PALETTE['border_strong']}; border-radius: 8px;"
            )

        def set_image(self, image) -> None:
            self.image = image
            self.update()

        def set_margins(self, margins) -> None:
            self.margins = self._clamped_margins(margins)
            self.margins_changed.emit()
            self.update()

        def _clamped_margins(self, margins) -> list[int]:
            values = [int(round(float(v or 0))) for v in list(margins)[:4]]
            while len(values) < 4:
                values.append(0)
            top, left, right, bottom = [max(0, value) for value in values]
            min_size = 16
            top = min(top, max(0, self.source_h - min_size))
            bottom = min(bottom, max(0, self.source_h - top - min_size))
            left = min(left, max(0, self.source_w - min_size))
            right = min(right, max(0, self.source_w - left - min_size))
            return [top, left, right, bottom]

        def reset_crop(self) -> None:
            self.set_margins([0, 0, 0, 0])

        def reset_view(self) -> None:
            self.zoom = 1.0
            self._scroll = QPointF(0, 0)
            self._constrain_scroll()
            self._refresh_cursor_at_current_pos()
            self.zoom_changed.emit(self.zoom)
            self.update()

        def set_tool(self, tool: str) -> None:
            self._tool = "zoom" if tool == "zoom" else "hand"
            self._refresh_cursor_at_current_pos()

        def set_zoom_out_mode(self, enabled: bool) -> None:
            if self._zoom_out_mode == bool(enabled):
                return
            self._zoom_out_mode = bool(enabled)
            self._refresh_cursor_at_current_pos()

        def set_crop_overlay_visible(self, visible: bool) -> None:
            self._show_crop_overlay = bool(visible)
            self._refresh_cursor_at_current_pos()
            self.update()

        def _display_size(self):
            available_w = max(80, self.width() - 32)
            available_h = max(80, self.height() - 32)
            scale = min(
                available_w / self.source_w,
                available_h / self.source_h,
            ) * self.zoom if self.width() > 0 and self.height() > 0 else 1.0
            return max(1, int(self.source_w * scale)), max(1, int(self.source_h * scale))

        def _image_rect(self):
            w, h = self._display_size()
            return QRectF(
                self.width() / 2 + self._scroll.x() - w / 2,
                self.height() / 2 + self._scroll.y() - h / 2,
                w,
                h,
            )

        def _constrain_scroll(self) -> None:
            w, h = self._display_size()
            if w <= self.width():
                max_x = max(0.0, (self.width() - w) * 0.05)
            else:
                max_x = max(0.0, (w - self.width()) / 2 + self.width() * 0.5)
            if h <= self.height():
                max_y = max(0.0, (self.height() - h) * 0.05)
            else:
                max_y = max(0.0, (h - self.height()) / 2 + self.height() * 0.5)
            self._scroll = QPointF(
                max(-max_x, min(max_x, self._scroll.x())),
                max(-max_y, min(max_y, self._scroll.y())),
            )

        def _crop_rect_screen(self):
            img = self._image_rect()
            w, h = self._display_size()
            top, left, right, bottom = self.margins
            x1 = img.left() + left / self.source_w * w
            y1 = img.top() + top / self.source_h * h
            x2 = img.right() - right / self.source_w * w
            y2 = img.bottom() - bottom / self.source_h * h
            return QRectF(x1, y1, max(1, x2 - x1), max(1, y2 - y1))

        def _handle_centers(self):
            r = self._crop_rect_screen()
            mx = (r.left() + r.right()) / 2
            my = (r.top() + r.bottom()) / 2
            return {
                "nw": (r.left(), r.top()), "n": (mx, r.top()), "ne": (r.right(), r.top()),
                "e": (r.right(), my), "se": (r.right(), r.bottom()), "s": (mx, r.bottom()),
                "sw": (r.left(), r.bottom()), "w": (r.left(), my),
            }

        def _hit_handle(self, x, y):
            if not self._show_crop_overlay:
                return None
            hit = 18
            for name, (cx, cy) in self._handle_centers().items():
                if abs(x - cx) <= hit and abs(y - cy) <= hit:
                    return name
            crop = self._crop_rect_screen()
            edge_hit = 9
            point = QPointF(x, y)
            if crop.adjusted(-edge_hit, -edge_hit, edge_hit, edge_hit).contains(point):
                near_left = abs(x - crop.left()) <= edge_hit
                near_right = abs(x - crop.right()) <= edge_hit
                near_top = abs(y - crop.top()) <= edge_hit
                near_bottom = abs(y - crop.bottom()) <= edge_hit
                inside_x = crop.left() - edge_hit <= x <= crop.right() + edge_hit
                inside_y = crop.top() - edge_hit <= y <= crop.bottom() + edge_hit
                if near_left and inside_y:
                    return "w"
                if near_right and inside_y:
                    return "e"
                if near_top and inside_x:
                    return "n"
                if near_bottom and inside_x:
                    return "s"
            return None

        def _cursor_for_handle(self, handle):
            if handle in {"n", "s"}:
                return Qt.SizeVerCursor
            if handle in {"e", "w"}:
                return Qt.SizeHorCursor
            if handle in {"nw", "se"}:
                return Qt.SizeFDiagCursor
            if handle in {"ne", "sw"}:
                return Qt.SizeBDiagCursor
            return None

        def _refresh_cursor_at(self, pos) -> None:
            if self._drag_handle:
                return
            handle = self._hit_handle(pos.x(), pos.y())
            cursor = self._cursor_for_handle(handle)
            if cursor is not None:
                self.setCursor(cursor)
                return
            if self._tool == "zoom":
                self.setCursor(self._zoom_out_cursor if self._zoom_out_mode else self._zoom_in_cursor)
            else:
                self.setCursor(Qt.OpenHandCursor)

        def _make_zoom_cursor(self, zoom_out: bool):
            pix = QtGui.QPixmap(34, 34)
            pix.fill(Qt.transparent)
            p = QtGui.QPainter(pix)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.setPen(QtGui.QPen(QtGui.QColor("#050505"), 5.0, Qt.SolidLine, Qt.RoundCap))
            p.drawEllipse(QPointF(14, 14), 9, 9)
            p.drawLine(QPointF(21, 21), QPointF(30, 30))
            p.setPen(QtGui.QPen(QtGui.QColor("#ff9f1a"), 3.0, Qt.SolidLine, Qt.RoundCap))
            p.drawEllipse(QPointF(14, 14), 9, 9)
            p.drawLine(QPointF(21, 21), QPointF(30, 30))
            p.setPen(QtGui.QPen(QtGui.QColor("#050505"), 5.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(9, 14), QPointF(19, 14))
            if not zoom_out:
                p.drawLine(QPointF(14, 9), QPointF(14, 19))
            p.setPen(QtGui.QPen(QtGui.QColor("#2ea8ff"), 3.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(9, 14), QPointF(19, 14))
            if not zoom_out:
                p.drawLine(QPointF(14, 9), QPointF(14, 19))
            p.end()
            return QtGui.QCursor(pix, 14, 14)

        def _refresh_cursor_at_current_pos(self) -> None:
            self._refresh_cursor_at(self.mapFromGlobal(QtGui.QCursor.pos()))

        def _source_point_for_pos(self, pos):
            img = self._image_rect()
            w, h = self._display_size()
            sx = max(0.0, min(1.0, (pos.x() - img.left()) / max(1, w))) * self.source_w
            sy = max(0.0, min(1.0, (pos.y() - img.top()) / max(1, h))) * self.source_h
            return sx, sy

        def _zoom_centered(self, new_zoom: float, focus_source=None, focus_screen=None) -> None:
            focus_source = focus_source or self._zoom_focus_source
            old_rect = self._image_rect()
            focus_x, focus_y = focus_source
            if focus_screen is None:
                focus_screen = QPointF(
                    old_rect.left() + focus_x / self.source_w * old_rect.width(),
                    old_rect.top() + focus_y / self.source_h * old_rect.height(),
                )
            else:
                focus_screen = QPointF(float(focus_screen.x()), float(focus_screen.y()))
            self.zoom = max(0.08, min(64.0, float(new_zoom)))
            new_w, new_h = self._display_size()
            self._scroll = QPointF(
                focus_screen.x() - self.width() / 2 + new_w / 2 - focus_x / self.source_w * new_w,
                focus_screen.y() - self.height() / 2 + new_h / 2 - focus_y / self.source_h * new_h,
            )
            self._constrain_scroll()
            self.zoom_changed.emit(self.zoom)
            self.update()

        def paintEvent(self, _event):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            p.fillRect(self.rect(), QtGui.QColor(PALETTE["timeline_bg"]))
            img = self._image_rect()
            if self.image is not None and not self.image.isNull():
                # Preserve the frame's own aspect ratio inside the canvas rect.
                # Joined clips can have different aspect ratios (e.g. a vertical
                # clip after a horizontal one); fit-with-black instead of
                # stretching, matching the encode's scale+pad output.
                iw = self.image.width()
                ih = self.image.height()
                if iw > 0 and ih > 0:
                    fit = min(img.width() / iw, img.height() / ih)
                    dw = iw * fit
                    dh = ih * fit
                    dst = QRectF(
                        img.center().x() - dw / 2.0,
                        img.center().y() - dh / 2.0,
                        dw,
                        dh,
                    )
                    p.drawImage(dst, self.image)
                else:
                    p.drawImage(img, self.image)
            else:
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["text_mute"])))
                p.setFont(QtGui.QFont("Segoe UI", 12))
                p.drawText(self.rect(), Qt.AlignCenter, "Loading video preview...")
            center_line = QtGui.QColor("#b9c7d8")
            center_line.setAlpha(82)
            p.setPen(QtGui.QPen(center_line, 1))
            center_x = img.left() + img.width() * 0.5
            p.drawLine(QPointF(center_x, img.top()), QPointF(center_x, img.bottom()))
            if not self._show_crop_overlay:
                return
            crop = self._crop_rect_screen()
            dim = QtGui.QColor(0, 0, 0, 150)
            p.fillRect(QRectF(img.left(), img.top(), img.width(), max(0, crop.top() - img.top())), dim)
            p.fillRect(QRectF(img.left(), crop.bottom(), img.width(), max(0, img.bottom() - crop.bottom())), dim)
            p.fillRect(QRectF(img.left(), crop.top(), max(0, crop.left() - img.left()), crop.height()), dim)
            p.fillRect(QRectF(crop.right(), crop.top(), max(0, img.right() - crop.right()), crop.height()), dim)
            p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["warn"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(crop)
            p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["warn"]), 1, Qt.DashLine))
            for i in (1, 2):
                x = crop.left() + crop.width() * i / 3
                y = crop.top() + crop.height() * i / 3
                p.drawLine(QPointF(x, crop.top()), QPointF(x, crop.bottom()))
                p.drawLine(QPointF(crop.left(), y), QPointF(crop.right(), y))
            p.setBrush(QtGui.QBrush(QtGui.QColor(PALETTE["warn"])))
            p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["bg"]), 1))
            for cx, cy in self._handle_centers().values():
                p.drawRoundedRect(QRectF(cx - 7, cy - 7, 14, 14), 3, 3)

        def mousePressEvent(self, event):
            if event.button() == Qt.RightButton:
                self.toggle_playback_requested.emit()
                return
            if event.button() != Qt.LeftButton:
                return
            pos = event.position()
            self._press_pos = pos
            self._drag_handle = self._hit_handle(pos.x(), pos.y())
            if self._drag_handle:
                self.setCursor(self._cursor_for_handle(self._drag_handle) or Qt.ClosedHandCursor)
                return
            if event.modifiers() & Qt.ControlModifier:
                self._drag_handle = "move"
                self._move_origin = pos
                self._move_margins = list(self.margins)
                self.setCursor(Qt.SizeAllCursor)
                return
            if self._tool == "zoom":
                self._zoom_origin = pos
                self._zoom_start = self.zoom
                self._zoom_focus_source = self._source_point_for_pos(pos)
                self._zoom_focus_screen = QPointF(pos)
                self.set_zoom_out_mode(bool(event.modifiers() & Qt.AltModifier))
                return
            self._pan_origin = pos
            self._scroll_origin = QPointF(self._scroll)
            self._toggle_click_candidate = True
            self.setCursor(Qt.ClosedHandCursor)

        def mouseMoveEvent(self, event):
            pos = event.position()
            if self._drag_handle:
                if self._drag_handle == "move":
                    self._move_crop(pos)
                else:
                    self._resize_crop(self._drag_handle, pos.x(), pos.y())
                return
            if self._zoom_origin is not None:
                dy = self._zoom_origin.y() - pos.y()
                self._zoom_centered(
                    self._zoom_start * (2.0 ** (dy / 110.0)),
                    self._zoom_focus_source,
                    self._zoom_focus_screen,
                )
                return
            if self._pan_origin is not None:
                if abs(pos.x() - self._pan_origin.x()) + abs(pos.y() - self._pan_origin.y()) > 6:
                    self._toggle_click_candidate = False
                self._scroll = self._scroll_origin + (pos - self._pan_origin)
                self._constrain_scroll()
                self.update()
                return
            self._refresh_cursor_at(pos)

        def mouseReleaseEvent(self, _event):
            crop_edit = bool(self._drag_handle)
            if self._zoom_origin is not None and self._press_pos is not None:
                pos = _event.position()
                if abs(pos.x() - self._press_pos.x()) + abs(pos.y() - self._press_pos.y()) < 4:
                    factor = 1 / 1.35 if self._zoom_out_mode else 1.35
                    self._zoom_centered(self.zoom * factor, self._source_point_for_pos(pos), pos)
            self._drag_handle = None
            self._move_origin = None
            self._move_margins = None
            self._pan_origin = None
            self._zoom_origin = None
            self._zoom_focus_screen = None
            self._toggle_click_candidate = False
            self._refresh_cursor_at_current_pos()
            if crop_edit:
                self.edit_finished.emit()

        def wheelEvent(self, event):
            delta = event.angleDelta().y()
            if not delta:
                return
            factor = 1.18 if delta > 0 else 1 / 1.18
            self._zoom_centered(self.zoom * factor, self._source_point_for_pos(event.position()), event.position())

        def _resize_crop(self, handle, x, y):
            img = self._image_rect()
            w, h = self._display_size()
            sx = max(0, min(1, (x - img.left()) / max(1, w))) * self.source_w
            sy = max(0, min(1, (y - img.top()) / max(1, h))) * self.source_h
            top, left, right, bottom = self.margins
            min_size = 16
            if "w" in handle:
                left = max(0, min(self.source_w - right - min_size, int(round(sx))))
            if "e" in handle:
                right = max(0, min(self.source_w - left - min_size, int(round(self.source_w - sx))))
            if "n" in handle:
                top = max(0, min(self.source_h - bottom - min_size, int(round(sy))))
            if "s" in handle:
                bottom = max(0, min(self.source_h - top - min_size, int(round(self.source_h - sy))))
            self.margins = [top, left, right, bottom]
            self.margins_changed.emit()
            self.update()

        def _move_crop(self, pos):
            if self._move_origin is None or self._move_margins is None:
                return
            img = self._image_rect()
            w, h = self._display_size()
            dx = int(round((pos.x() - self._move_origin.x()) / max(1, w) * self.source_w))
            dy = int(round((pos.y() - self._move_origin.y()) / max(1, h) * self.source_h))
            top, left, right, bottom = [int(v) for v in self._move_margins]
            crop_w = max(1, self.source_w - left - right)
            crop_h = max(1, self.source_h - top - bottom)
            new_left = max(0, min(self.source_w - crop_w, left + dx))
            new_top = max(0, min(self.source_h - crop_h, top + dy))
            self.margins = [new_top, new_left, self.source_w - crop_w - new_left, self.source_h - crop_h - new_top]
            self.margins_changed.emit()
            self.update()

    class UnifiedTimelineWidget(QWidget):
        seek_requested = Signal(float)
        cut_selected = Signal(int)
        separator_selected = Signal(int)
        marker_moved = Signal(str, float)
        separator_moved = Signal(int, float)
        edit_finished = Signal()
        view_changed = Signal()

        PAD = 18

        def __init__(self, duration: float, fps: float):
            super().__init__()
            self.duration = max(0.001, float(duration or 0.001))
            self.fps = max(1.0, float(fps or 25.0))
            self.playhead = 0.0
            self.view_start = 0.0
            self.view_span = self.duration
            self.cut_ranges: list[tuple[float, float]] = []
            self.selected_cut = -1
            self.separator_points: list[float] = []
            self.selected_separator = -1
            self.selected_marker: str | None = None
            self.snap_enabled = True        # magnetic snapping to CTI / marks / splits
            self.wave_pix = None
            self._pcm = None                # decoded mono PCM (s16le) for the crisp waveform
            self._pcm_np = None             # numpy int16 view of _pcm (fast per-pixel min/max)
            self._pcm_rate = 4000
            self._pcm_gmax = 1
            self._wave_cache = None         # ((start, span, w, h), QPixmap) — crisp render
            self._reverse_view = False      # mirror the waveform when previewing reverse
            self._reverse_anchor = 0.0      # mirror is taken around THIS time (no jump)
            self._wave_timer = QtCore.QTimer(self)   # debounce: rebuild after motion settles
            self._wave_timer.setSingleShot(True)
            self._wave_timer.timeout.connect(self._rebuild_wave)
            self.chapters: list[dict[str, object]] = []
            self.join_segments: list[dict[str, object]] = []
            self.mark_in: float | None = None
            self.mark_out: float | None = None
            self._dragging = False
            self._drag_kind = None
            self._drag_target = None
            self._drag_origin = None
            self._last_drag_pos = None
            self._drag_playhead_start = 0.0
            self._vertical_zoom_lock = False
            self._cti_zoom_focus_time = 0.0
            self._cti_zoom_start_y = 0.0
            self._start_span = self.view_span
            self.setMinimumHeight(150)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setMouseTracking(True)
            self.setStyleSheet(
                f"background-color: {PALETTE['timeline_bg']};"
                f"border: 1px solid {PALETTE['border_strong']}; border-radius: 8px;"
            )

        def set_waveform(self, path: Path) -> None:
            pix = QtGui.QPixmap(str(path))
            if not pix.isNull():
                self.wave_pix = pix
                self.update()

        def set_pcm(self, data, rate) -> None:
            self._pcm = data or None
            self._pcm_rate = max(1, int(rate))
            self._wave_cache = None
            self._pcm_np = None
            self._env_min = None
            self._env_max = None
            self._env_step = 256
            if self._pcm:
                try:
                    import numpy as _np
                    self._pcm_np = _np.frombuffer(self._pcm, dtype=_np.int16)
                    # int() BEFORE negating: -np.int16(-32768) overflows (stays negative).
                    self._pcm_gmax = max(1, int(self._pcm_np.max()), -int(self._pcm_np.min()))
                    # Decimated min/max envelope: lets zoomed-out views render from a
                    # tiny array instead of scanning all ~22M samples (keeps it ~1ms).
                    D = self._env_step
                    m = self._pcm_np.size // D
                    if m >= 2:
                        block = self._pcm_np[:m * D].reshape(m, D)
                        self._env_max = block.max(axis=1)
                        self._env_min = block.min(axis=1)
                except Exception:
                    self._pcm_np = None
                    try:
                        import audioop
                        self._pcm_gmax = max(1, audioop.max(self._pcm, 2))
                    except Exception:
                        self._pcm_gmax = 32768
            self.update()

        def _render_wave_pixmap(self, start_t, span, width_px, wh):
            """Render the waveform for one view into a QPixmap.

            ROOT FIX for "heavy": the common min/max case is filled with a SINGLE
            vectorised numpy op (no thousands-of-points QPainterPath loop), so a
            full render is ~1ms at any zoom. Only the extreme-zoom smooth curve
            (few samples) uses a short cubic path. Cheap enough to run inline on
            the paint path — no worker thread, so no GIL contention."""
            width_px = max(1, int(width_px)); wh = max(1, int(wh))
            cyl = wh / 2.0
            # Leave a clear top/bottom margin inside the waveform lane so even a
            # ceiling-clamped (loud) peak never touches the lane edges / borders.
            half = wh * 0.42
            # Reverse preview: mirror the WHOLE clip around its midpoint — one single
            # consistent flip for the entire timeline (timeline t <-> source[dur - t]),
            # so moving the CTI never re-mirrors. Render the mirrored window + flip it.
            flip = bool(getattr(self, "_reverse_view", False))
            if flip:
                start_t = max(0.0, min(self.duration, self.duration - (start_t + span)))

            def _out(img):
                return QtGui.QPixmap.fromImage(img.mirrored(True, False) if flip else img)

            def _blank():
                im = QtGui.QImage(width_px, wh, QtGui.QImage.Format_ARGB32)
                im.fill(0)
                return QtGui.QPixmap.fromImage(im)

            if not self._pcm:
                return _blank()
            rate = self._pcm_rate
            total = len(self._pcm) // 2
            # Absolute amplitude: scale against full-scale int16, NOT the clip's
            # own peak. This way quiet audio renders a short waveform and loud
            # audio a tall one, instead of every clip being normalized to fill the
            # same height regardless of its real loudness. The value is clamped so
            # amplitude above the ceiling reference caps at full height.
            gmax = WAVEFORM_CEILING_PEAK
            col = QtGui.QColor("#3a8bff")

            if self._pcm_np is not None:
                try:
                    import numpy as _np
                    s0 = max(0, min(total, int(start_t * rate)))
                    s1 = max(s0 + 1, min(total, int((start_t + span) * rate)))
                    seg = self._pcm_np[s0:s1]
                    n = int(seg.size)
                    if 1 < n < width_px:
                        # Fewer samples than pixels: interpolate to one value per pixel
                        # with a Catmull-Rom spline (ROUNDED peaks, not angular like
                        # linear), then fill a 2px connected trace. All vectorised numpy
                        # -> ~1ms at any zoom (no per-point cubic/polyline, which was 16-60ms).
                        pos = _np.arange(width_px, dtype=_np.float64) * (n - 1) / max(1.0, width_px - 1)
                        i = _np.floor(pos).astype(_np.int64)
                        frac = pos - i
                        s = seg.astype(_np.float64)
                        p0 = s[_np.clip(i - 1, 0, n - 1)]
                        p1 = s[_np.clip(i, 0, n - 1)]
                        p2 = s[_np.clip(i + 1, 0, n - 1)]
                        p3 = s[_np.clip(i + 2, 0, n - 1)]
                        f2 = frac * frac
                        f3 = f2 * frac
                        v = (0.5 * (2.0 * p1 + (-p0 + p2) * frac
                                    + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * f2
                                    + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * f3)) / gmax
                        v = _np.clip(v, -1.0, 1.0)   # cap at the ceiling reference
                        vpx = cyl - v * half
                        v2 = _np.empty_like(vpx)
                        v2[:-1] = vpx[1:]; v2[-1] = vpx[-1]   # connect each column to the next
                        top = _np.minimum(vpx, v2) - 1.0      # ~2px line (1px each side)
                        bot = _np.maximum(vpx, v2) + 1.0
                        # Anti-alias in numpy: per-pixel fractional coverage -> alpha, so
                        # the curve's edges are smooth instead of stair-stepped/pixelated.
                        rows = _np.arange(wh, dtype=_np.float64)[:, None]
                        cov = _np.clip(_np.minimum(rows + 1.0, bot[None, :]) - _np.maximum(rows, top[None, :]), 0.0, 1.0)
                        alpha = (cov * 255.0).astype(_np.uint32)
                        buf = (alpha << 24) | _np.uint32(0x003A8BFF)   # ARGB w/ soft edges
                        img = QtGui.QImage(buf.tobytes(), width_px, wh, width_px * 4,
                                           QtGui.QImage.Format_ARGB32).copy()
                        return _out(img)
                    # Per-pixel min/max bars, filled in ONE numpy op. For zoomed-out
                    # views read the decimated envelope so we never scan all samples.
                    use_env = (self._env_max is not None
                               and n > width_px * self._env_step)
                    if n <= 0:
                        top = _np.zeros(width_px, dtype=_np.float64)
                        bot = top
                    elif use_env:
                        D = self._env_step
                        e0 = max(0, min(self._env_max.size, s0 // D))
                        e1 = max(e0 + 1, min(self._env_max.size, s1 // D))
                        em = self._env_max[e0:e1]; en = self._env_min[e0:e1]
                        m_env = em.size
                        idx = (_np.arange(width_px + 1, dtype=_np.int64) * m_env) // (width_px + 1)
                        _np.clip(idx, 0, m_env - 1, out=idx)
                        top = _np.maximum.reduceat(em, idx)[:width_px].astype(_np.float64) / gmax
                        bot = _np.minimum.reduceat(en, idx)[:width_px].astype(_np.float64) / gmax
                    else:
                        idx = (_np.arange(width_px + 1, dtype=_np.int64) * n) // (width_px + 1)
                        _np.clip(idx, 0, n - 1, out=idx)
                        top = _np.maximum.reduceat(seg, idx)[:width_px].astype(_np.float64) / gmax
                        bot = _np.minimum.reduceat(seg, idx)[:width_px].astype(_np.float64) / gmax
                    # Cap at the ceiling reference so loud peaks clamp instead of
                    # overflowing the waveform area.
                    top = _np.clip(top, -1.0, 1.0)
                    bot = _np.clip(bot, -1.0, 1.0)
                    top_px = cyl - top * half
                    bot_px = cyl - bot * half
                    # Always span the centre line so adjacent columns stay connected — a
                    # continuous filled waveform instead of disjoint specks when there
                    # are only a couple of samples per pixel (sub-second zoom).
                    lo = _np.minimum(_np.minimum(top_px, bot_px), cyl)
                    hi = _np.maximum(_np.maximum(top_px, bot_px), cyl)
                    y_lo = _np.clip(_np.floor(lo), 0, wh - 1).astype(_np.int32)
                    y_hi = _np.clip(_np.ceil(hi), 0, wh - 1).astype(_np.int32)
                    rows = _np.arange(wh, dtype=_np.int32)[:, None]
                    mask = (rows >= y_lo[None, :]) & (rows <= y_hi[None, :])
                    buf = _np.zeros((wh, width_px), dtype=_np.uint32)
                    buf[mask] = 0xFF3A8BFF        # ARGB (little-endian / Windows) = #3a8bff
                    img = QtGui.QImage(buf.tobytes(), width_px, wh, width_px * 4,
                                       QtGui.QImage.Format_ARGB32).copy()
                    return _out(img)
                except Exception:
                    pass  # fall through to the audioop path on any numpy mishap

            # No numpy -> audioop fallback (rare): cheap vertical bars via drawLines.
            img = QtGui.QImage(width_px, wh, QtGui.QImage.Format_ARGB32)
            img.fill(0)
            try:
                import audioop
                lines = []
                for px in range(width_px):
                    t0 = start_t + (px / width_px) * span
                    t1 = start_t + ((px + 1) / width_px) * span
                    s0 = max(0, min(total - 1, int(t0 * rate))) if total else 0
                    s1 = max(s0 + 1, min(total, int(t1 * rate)))
                    frag = self._pcm[s0 * 2:s1 * 2]
                    if frag:
                        mn, mx = audioop.minmax(frag, 2)
                    else:
                        mn = mx = 0
                    mxv = max(-1.0, min(1.0, mx / gmax))
                    mnv = max(-1.0, min(1.0, mn / gmax))
                    lines.append(QtCore.QLineF(px + 0.5, cyl - mxv * half,
                                               px + 0.5, cyl - mnv * half))
                pp = QtGui.QPainter(img)
                pp.setPen(QtGui.QPen(col, 1.0))
                pp.drawLines(lines)
                pp.end()
            except Exception:
                pass
            return _out(img)

        def set_reverse_view(self, on: bool, anchor: float = 0.0) -> None:
            # Whole-clip mirror around the midpoint; `anchor` kept for call
            # compatibility but unused (single consistent flip).
            on = bool(on)
            if self._reverse_view == on:
                return
            self._reverse_view = on
            self._wave_cache = None     # force a re-render with the new orientation
            self.update()

        def _schedule_wave_rebuild(self):
            # Fire ~60ms after motion (debounce); if motion is sustained, don't keep
            # resetting it — let it fire periodically so the render chases the view.
            if not self._wave_timer.isActive():
                self._wave_timer.start(60)

        def _rebuild_wave(self):
            """Render the crisp waveform for the current view and cache it.

            Runs ~1ms (vectorised), so doing it inline after a 60ms debounce is
            cheap. Skips work when the cache already matches the view."""
            if not self._pcm:
                return
            wave = self._wave_rect()
            width_px = max(1, int(wave.width()))
            wh = max(1, int(wave.height()))
            start_t = self.view_start
            span = max(1e-9, self.view_span)
            key = (round(start_t, 4), round(span, 5), width_px, wh)
            if self._wave_cache is not None and self._wave_cache[0] == key:
                return
            self._wave_cache = (key, self._render_wave_pixmap(start_t, span, width_px, wh))
            self.update()

        def set_playhead(self, seconds: float, follow: bool = False) -> None:
            t = max(0.0, min(self.duration, float(seconds)))
            # Snap to video midpoint when within a small pixel threshold.
            center_time = self.duration / 2.0
            if self.view_span > 0:
                wave_w = self._wave_rect().width()
                pixels_per_second = wave_w / max(0.001, self.view_span)
                snap_threshold_px = 6
                if abs(t - center_time) * pixels_per_second < snap_threshold_px:
                    t = center_time
            self.playhead = t
            if follow:
                self._ensure_visible(self.playhead)
            self.update()

        def set_cut_ranges(self, ranges) -> None:
            self.cut_ranges = normalize_ranges(ranges, self.duration)
            self.selected_cut = min(self.selected_cut, len(self.cut_ranges) - 1)
            self.update()

        def set_separator_points(self, points) -> None:
            cleaned: list[float] = []
            for value in points or []:
                try:
                    point = float(value)
                except (TypeError, ValueError):
                    continue
                if 1e-6 < point < self.duration - 1e-6:
                    cleaned.append(point)
            cleaned = sorted(set(round(point, 6) for point in cleaned))
            self.separator_points = cleaned
            self.selected_separator = min(self.selected_separator, len(self.separator_points) - 1)
            self.update()

        def set_chapters(self, chapters) -> None:
            self.chapters = list(chapters or [])
            self.update()

        def set_join_segments(self, segments) -> None:
            self.join_segments = list(segments or [])
            self.update()

        def set_marks(self, mark_in, mark_out) -> None:
            self.mark_in = None if mark_in is None else max(0.0, min(self.duration, float(mark_in)))
            self.mark_out = None if mark_out is None else max(0.0, min(self.duration, float(mark_out)))
            if self.selected_marker == "in" and self.mark_in is None:
                self.selected_marker = None
            if self.selected_marker == "out" and self.mark_out is None:
                self.selected_marker = None
            self.update()

        def _timeline_rect(self):
            return QRectF(
                self.PAD,
                8,
                max(1, self.width() - self.PAD * 2),
                max(150, self.height() - 16),
            )

        def _ruler_rect(self):
            r = self._timeline_rect()
            return QRectF(r.left() + 8, r.top() + 8, max(1, r.width() - 16), 38)

        def _wave_rect(self):
            r = self._timeline_rect()
            return QRectF(r.left() + 8, r.top() + 72, max(1, r.width() - 16), max(76, r.height() - 84))

        def _cut_bar_rect(self, start_s: float, end_s: float):
            wave = self._wave_rect()
            x1 = self._time_to_x(start_s)
            x2 = self._time_to_x(end_s)
            return QRectF(x1, wave.top() - 22, max(4, x2 - x1), 18)

        def _clamp_view(self):
            self.view_span = max(0.05, min(self.duration, self.view_span))
            self.view_start = max(0.0, min(max(0.0, self.duration - self.view_span), self.view_start))

        def _ensure_visible(self, t):
            # PAGE scroll: when the playhead leaves the visible window, jump the view
            # one page so the playhead reappears at the LEADING edge and keeps sweeping
            # (instead of gluing the playhead to the edge and scrolling continuously).
            self._clamp_view()
            if t > self.view_start + self.view_span:
                self.view_start = t                      # forward: playhead -> left edge
            elif t < self.view_start:
                self.view_start = t - self.view_span     # backward: playhead -> right edge
            self._clamp_view()
            self.view_changed.emit()

        def _time_to_x(self, t):
            r = self._wave_rect()
            return r.left() + (float(t) - self.view_start) / max(0.001, self.view_span) * r.width()

        def _x_to_time(self, x):
            r = self._wave_rect()
            ratio = max(0.0, min(1.0, (float(x) - r.left()) / max(1, r.width())))
            return self.view_start + ratio * self.view_span

        def _snap_targets(self, exclude_separator=None, exclude_marker=None):
            """Times the magnet can snap to: CTI, mark in/out, split points,
            joined-video boundaries, clip ends."""
            targets = [self.playhead, 0.0, self.duration]
            if self.mark_in is not None and exclude_marker != "in":
                targets.append(float(self.mark_in))
            if self.mark_out is not None and exclude_marker != "out":
                targets.append(float(self.mark_out))
            for i, sp in enumerate(self.separator_points):
                if i != exclude_separator:
                    targets.append(float(sp))
            # Joined-video boundaries: markers/CTI snap to where each new video
            # starts (a little stickiness so cuts/marks land exactly on a join).
            for segment in getattr(self, "join_segments", None) or []:
                start_t = float(segment.get("start", 0.0))
                if start_t > 1e-6:
                    targets.append(start_t)
            return targets

        def _snap_time(self, seconds, exclude_separator=None, exclude_marker=None):
            """Snap a time to the nearest magnet target (CTI / marks / splits / clip
            ends) when magnetic snapping is enabled. The tolerance is PURELY pixel
            based, so zooming in tightens the pull (precise adjustment); switch
            self.snap_enabled off for completely free-hand placement."""
            seconds = max(0.0, min(self.duration, float(seconds)))
            if not self.snap_enabled:
                return seconds
            seconds_per_px = self.view_span / max(1.0, self._wave_rect().width())
            tolerance = seconds_per_px * 8.0          # ~8 px pull; shrinks as you zoom in
            best, best_dist = seconds, tolerance
            for target in self._snap_targets(exclude_separator, exclude_marker):
                d = abs(seconds - target)
                if d <= best_dist:
                    best, best_dist = target, d
            return best

        def _snap_time_to_cti(self, seconds: float) -> float:
            return self._snap_time(seconds)

        def set_zoom_ratio(self, ratio, focus_time=None):
            old_span = self.view_span
            ratio = max(1.0, min(self.duration / 0.05, float(ratio)))   # allow the same depth as drag/wheel
            focus = self.playhead if focus_time is None else max(0.0, min(self.duration, float(focus_time)))
            focus_ratio = (focus - self.view_start) / max(0.001, old_span)
            self.view_span = self.duration / ratio
            self.view_start = focus - focus_ratio * self.view_span
            self._clamp_view()
            self.update()
            self.view_changed.emit()

        def zoom_ratio(self):
            return max(1.0, self.duration / max(0.001, self.view_span))

        def set_view_start(self, seconds: float) -> None:
            self.view_start = float(seconds)
            self._clamp_view()
            self.update()
            self.view_changed.emit()

        def scroll_view(self, seconds: float) -> None:
            self.set_view_start(self.view_start + float(seconds))

        def paintEvent(self, _event):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.fillRect(self.rect(), QtGui.QColor(PALETTE["timeline_bg"]))
            r = self._timeline_rect()
            ruler = self._ruler_rect()
            wave = self._wave_rect()
            p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["border"]), 1))
            p.setBrush(QtGui.QBrush(QtGui.QColor(PALETTE["timeline_track"])))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["border_soft"]), 1))
            p.setBrush(QtGui.QBrush(QtGui.QColor("#111820")))
            p.drawRoundedRect(ruler, 5, 5)
            p.setBrush(QtGui.QBrush(QtGui.QColor("#101722")))
            p.drawRoundedRect(wave, 4, 4)
            grid_pen = QtGui.QPen(QtGui.QColor("#263447"), 1)
            p.setPen(grid_pen)
            for i in range(1, 12):
                x = wave.left() + wave.width() * i / 12.0
                p.drawLine(QPointF(x, wave.top()), QPointF(x, wave.bottom()))
            for i in range(1, 4):
                y = wave.top() + wave.height() * i / 4.0
                p.drawLine(QPointF(wave.left(), y), QPointF(wave.right(), y))
            span = max(0.001, self.view_span)
            target_ticks = max(8, min(15, int(ruler.width() // 70)))
            approx = span / max(1, target_ticks)
            exp = math.floor(math.log10(max(approx, 0.001)))
            base = 10 ** exp
            step = base
            for candidate in (1, 2, 2.5, 5, 10):
                step = candidate * base
                if span / step <= target_ticks:
                    break
            start = self.view_start
            end = start + span
            for idx, segment in enumerate(self.join_segments):
                seg_start = float(segment.get("start", 0.0))
                seg_end = float(segment.get("end", 0.0))
                if seg_end < start or seg_start > end:
                    continue
                x1 = self._time_to_x(max(seg_start, start))
                x2 = self._time_to_x(min(seg_end, end))
                if x2 <= x1:
                    continue
                fill = QtGui.QColor("#0d1b2a" if idx % 2 == 0 else "#132238")
                fill.setAlpha(150)
                p.setBrush(QtGui.QBrush(fill))
                p.setPen(QtGui.QPen(QtGui.QColor("#254567"), 1))
                p.drawRect(QRectF(x1, ruler.top(), x2 - x1, wave.bottom() - ruler.top()))
            p.setFont(QtGui.QFont("Segoe UI Semibold", 9))
            tick_label_width = 100
            tick_positions: list[float] = []
            last_label_right: float = -999.0  # Track rightmost label edge to prevent overlap.
            t = math.ceil(start / step) * step
            while t <= end + 1e-6:
                x = self._time_to_x(t)
                if abs(t - start) <= max(0.001, step * 0.04) or abs(t - end) <= max(0.001, step * 0.04):
                    t += step
                    continue
                label_left = max(ruler.left(), min(x - tick_label_width / 2, ruler.right() - tick_label_width))
                # Skip label if it overlaps the previous one.
                if label_left < last_label_right + 8:
                    t += step
                    continue
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["tick_hi"]), 1))
                p.drawLine(QPointF(x, ruler.bottom() - 11), QPointF(x, ruler.bottom() - 3))
                tick_positions.append(float(x))
                p.drawText(
                    QRectF(
                        label_left,
                        ruler.top() + 5,
                        tick_label_width,
                        18,
                    ),
                    Qt.AlignCenter,
                    seconds_to_timecode(t),
                )
                last_label_right = label_left + tick_label_width
                t += step
            for edge_t in (start, end):
                x = self._time_to_x(edge_t)
                label_left = max(ruler.left(), min(x - tick_label_width / 2, ruler.right() - tick_label_width))
                label_right = label_left + tick_label_width
                # Skip if overlaps any existing tick label or the previous edge label.
                if label_left < last_label_right + 8:
                    continue
                too_close = any(abs(x - tx) < tick_label_width * 0.85 for tx in tick_positions)
                if too_close:
                    continue
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["tick_hi"]), 1))
                p.drawLine(QPointF(x, ruler.bottom() - 12), QPointF(x, ruler.bottom() - 2))
                label_rect = QRectF(label_left, ruler.top() + 5, tick_label_width, 18)
                p.drawText(label_rect, Qt.AlignCenter, seconds_to_timecode(edge_t))
                last_label_right = label_right
                tick_positions.append(float(x))
            for chapter_idx, chapter in enumerate(self.chapters, start=1):
                cs = float(chapter.get("start", 0.0))
                if cs < start or cs > end:
                    continue
                x = self._time_to_x(cs)
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["chapter"]), 2, Qt.DashLine))
                p.drawLine(QPointF(x, ruler.top() + 4), QPointF(x, ruler.bottom() - 4))
                title = str(chapter.get("title") or "Chapter")
                number = chapter_idx
                label = f"Chapter {number}" if title == f"Chapter {number}" else f"Chapter {number}: {title}"
                label = short_gui_label(label, 32)
                p.setFont(QtGui.QFont("Segoe UI Semibold", 8))
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["chapter_text"]), 1))
                p.drawText(
                    QRectF(max(ruler.left(), min(x + 4, ruler.right() - 210)), ruler.top() + 20, 210, 16),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    label,
                )
            if self._pcm:
                # Render the crisp waveform for the EXACT current view (~1ms, cached).
                # No stretched transient blit — that's what briefly distorted the
                # waveform mid-zoom — and the render is cheap enough to do per view.
                width_px = max(1, int(wave.width()))
                wh = max(1, int(wave.height()))
                start_t = self.view_start
                span = max(1e-9, self.view_span)
                cur = (round(start_t, 4), round(span, 5), width_px, wh)
                c = self._wave_cache
                if c is None or c[0] != cur:
                    c = (cur, self._render_wave_pixmap(start_t, span, width_px, wh))
                    self._wave_cache = c
                p.drawPixmap(int(wave.left()), int(wave.top()), c[1])
            else:
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["text_mute"])))
                p.drawText(wave, Qt.AlignCenter, "Audio waveform loading...")
            grid_overlay = QtGui.QColor("#2b3b52")
            grid_overlay.setAlpha(120)
            p.setPen(QtGui.QPen(grid_overlay, 1))
            for i in range(1, 12):
                x = wave.left() + wave.width() * i / 12.0
                p.drawLine(QPointF(x, wave.top()), QPointF(x, wave.bottom()))
            for i in range(1, 4):
                y = wave.top() + wave.height() * i / 4.0
                p.drawLine(QPointF(wave.left(), y), QPointF(wave.right(), y))
            tick_guide = QtGui.QColor("#75869a")
            tick_guide.setAlpha(78)
            p.setPen(QtGui.QPen(tick_guide, 1))
            for x in tick_positions:
                p.drawLine(QPointF(x, ruler.bottom()), QPointF(x, wave.bottom()))
            for idx, (s, e) in enumerate(self.cut_ranges):
                if e < start or s > end:
                    continue
                x1 = self._time_to_x(max(s, start))
                x2 = self._time_to_x(min(e, end))
                color = QtGui.QColor(PALETTE["cut_red"] if idx == self.selected_cut else PALETTE["cut_red_dim"])
                color.setAlpha(150 if idx == self.selected_cut else 105)
                border = QtGui.QColor("#ffb3ad" if idx == self.selected_cut else "#ff8c86")
                border.setAlpha(230 if idx == self.selected_cut else 190)
                p.setPen(QtGui.QPen(border, 2))
                p.setBrush(QtGui.QBrush(color))
                cut_rect = QRectF(x1, wave.top(), max(3, x2 - x1), wave.height()).adjusted(0.5, 0.5, -0.5, -0.5)
                p.drawRoundedRect(cut_rect, 5, 5)
                if cut_rect.width() >= 34:
                    p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1))
                    p.setFont(QtGui.QFont("Segoe UI Semibold", 8))
                    p.drawText(cut_rect.adjusted(5, 3, -5, -3), Qt.AlignCenter, f"Cut #{idx + 1}")
                bar_rect = self._cut_bar_rect(max(s, start), min(e, end))
                bar_fill = QtGui.QColor("#4b1118" if idx == self.selected_cut else "#301117")
                bar_fill.setAlpha(245 if idx == self.selected_cut else 210)
                bar_border = QtGui.QColor("#ffd1cc" if idx == self.selected_cut else "#e88a84")
                bar_border.setAlpha(245 if idx == self.selected_cut else 190)
                p.setBrush(QtGui.QBrush(bar_fill))
                p.setPen(QtGui.QPen(bar_border, 2 if idx == self.selected_cut else 1))
                p.drawRoundedRect(bar_rect, 4, 4)
                p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1))
                p.setFont(QtGui.QFont("Segoe UI Semibold", 8))
                p.drawText(bar_rect.adjusted(7, 1, -7, -1), Qt.AlignCenter, f"Cut #{idx + 1}")
                handle_fill = QtGui.QColor("#ffffff")
                handle_fill.setAlpha(245 if idx == self.selected_cut else 205)
                handle_border = QtGui.QColor("#ffb3ad")
                handle_border.setAlpha(245 if idx == self.selected_cut else 190)
                p.setBrush(QtGui.QBrush(handle_fill))
                p.setPen(QtGui.QPen(handle_border, 1))
                y_top = bar_rect.top() + 1
                y_bottom = bar_rect.bottom() - 1
                left_x = bar_rect.left()
                right_x = bar_rect.right()
                tab_w = min(13.0, max(8.0, bar_rect.width() * 0.22))
                p.drawPolygon(QtGui.QPolygonF([
                    QPointF(left_x, y_top),
                    QPointF(left_x + tab_w, y_top),
                    QPointF(left_x, y_bottom),
                ]))
                p.drawPolygon(QtGui.QPolygonF([
                    QPointF(right_x, y_top),
                    QPointF(right_x - tab_w, y_top),
                    QPointF(right_x, y_bottom),
                ]))
            for segment in self.join_segments:
                boundary = float(segment.get("start", 0.0))
                if boundary <= 1e-6 or boundary < start or boundary > end:
                    continue
                x = self._time_to_x(boundary)
                # Subtle dotted boundary between joined videos: thin, faint and
                # small (like the centre guide) instead of a thick coloured bar,
                # so many joined clips stay readable and uncluttered.
                backing = QtGui.QColor(6, 10, 16, 200)
                line_color = QtGui.QColor(232, 178, 120, 200)  # amber, a little stronger
                top_y = ruler.bottom() + 2
                bot_y = wave.bottom() + 4
                p.setPen(QtGui.QPen(backing, 2))
                p.drawLine(QPointF(x, top_y), QPointF(x, bot_y))
                p.setPen(QtGui.QPen(line_color, 1, Qt.DotLine))
                p.drawLine(QPointF(x, top_y), QPointF(x, bot_y))
                # Centre the label on the dotted line (like the Center guide),
                # not left-aligned beside it.
                p.setPen(QtGui.QPen(QtGui.QColor(240, 200, 150, 235), 1))
                p.setFont(QtGui.QFont("Segoe UI", 7))
                label = short_gui_label(str(segment.get("label") or "Video"), 14)
                _lbl_w = 84.0
                p.drawText(QRectF(x - _lbl_w / 2.0, top_y, _lbl_w, 12), Qt.AlignCenter, label)
            for idx, value in enumerate(self.separator_points):
                if value < start or value > end:
                    continue
                x = self._time_to_x(value)
                selected = idx == self.selected_separator
                sep_color = QtGui.QColor("#7dd3fc" if selected else "#38bdf8")
                stroke = QtGui.QColor(PALETTE["playhead_halo"])
                stroke.setAlpha(150 if selected else 95)
                line_top = ruler.bottom() + (14 if selected else 12)
                line_bottom = wave.bottom() + (8 if selected else 5)
                p.setPen(QtGui.QPen(stroke, 8 if selected else 6))
                p.drawLine(QPointF(x, line_top), QPointF(x, line_bottom))
                p.setPen(QtGui.QPen(sep_color, 4 if selected else 3))
                p.drawLine(QPointF(x, line_top), QPointF(x, line_bottom))
                p.setBrush(QtGui.QBrush(sep_color))
                p.setPen(QtGui.QPen(stroke, 2))
                half = 12 if selected else 9
                tip = ruler.bottom() + (14 if selected else 12)
                p.drawPolygon(QtGui.QPolygonF([
                    QPointF(x - half, ruler.bottom() - 1),
                    QPointF(x + half, ruler.bottom() - 1),
                    QPointF(x, tip),
                ]))
                p.setPen(QtGui.QPen(sep_color, 1))
                p.setFont(QtGui.QFont("Segoe UI Semibold", 9 if selected else 8))
                p.drawText(QRectF(x + 5, ruler.bottom() + 2, 72, 16), Qt.AlignLeft | Qt.AlignVCenter, "SPLIT")
            for label, value, color_name in (
                ("IN", self.mark_in, "marker_in"),
                ("OUT", self.mark_out, "marker_out"),
            ):
                if value is None or value < start or value > end:
                    continue
                x = self._time_to_x(value)
                marker_color = QtGui.QColor(PALETTE[color_name])
                selected = self.selected_marker == label.lower()
                stroke = QtGui.QColor(PALETTE["playhead_halo"])
                stroke.setAlpha(155 if selected else 100)
                # Anchor at the ruler bottom (exactly like SPLIT) so IN/OUT are the SAME
                # height as the split markers instead of starting lower at the wave top.
                line_top = ruler.bottom() + (14 if selected else 12)
                line_bottom = wave.bottom() + (8 if selected else 5)
                p.setPen(QtGui.QPen(stroke, 8 if selected else 6))
                p.drawLine(QPointF(x, line_top), QPointF(x, line_bottom))
                p.setPen(QtGui.QPen(marker_color, 4 if selected else 3))
                p.drawLine(QPointF(x, line_top), QPointF(x, line_bottom))
                p.setBrush(QtGui.QBrush(marker_color))
                p.setPen(QtGui.QPen(stroke, 2))
                half = 12 if selected else 9
                tip = ruler.bottom() + (14 if selected else 12)
                p.drawPolygon(QtGui.QPolygonF([
                    QPointF(x - half, ruler.bottom() - 1),
                    QPointF(x + half, ruler.bottom() - 1),
                    QPointF(x, tip),
                ]))
                p.setPen(QtGui.QPen(marker_color, 1))
                p.setFont(QtGui.QFont("Segoe UI Semibold", 9 if selected else 8))
                p.drawText(QRectF(x + 5, ruler.bottom() + 2, 42, 16), Qt.AlignLeft | Qt.AlignVCenter, label)
            # Video midpoint guide: fixed at the exact centre of the entire video
            # duration. Drawn like the CTI/playhead (arrow at the top of the ruler
            # plus a full-height line) in a distinct violet so it is always clearly
            # visible and not confused with the red playhead.
            center_time = self.duration / 2.0
            cx = self._time_to_x(center_time)
            if ruler.left() - 2 <= cx <= ruler.right() + 2:
                guide_color = QtGui.QColor("#c084fc")
                guide_backing = QtGui.QColor(10, 6, 18, 220)
                arrow_top = ruler.top() + 1
                arrow_tip = ruler.top() + 19
                # Full-height guide line (same span as the playhead). A dark backing
                # line gives contrast where it crosses the bright blue waveform.
                p.setPen(QtGui.QPen(guide_backing, 3))
                p.drawLine(QPointF(cx, arrow_tip), QPointF(cx, wave.bottom() + 8))
                p.setPen(QtGui.QPen(guide_color, 2, Qt.DashLine))
                p.drawLine(QPointF(cx, arrow_tip), QPointF(cx, wave.bottom() + 8))
                # Down-pointing arrow at the top of the ruler, the same height as
                # the CTI arrow.
                p.setBrush(QtGui.QBrush(guide_color))
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["bg"]), 1))
                p.drawPolygon(QtGui.QPolygonF([
                    QPointF(cx - 9, arrow_top),
                    QPointF(cx + 9, arrow_top),
                    QPointF(cx, arrow_tip),
                ]))
                # Timecode pill centered horizontally on the centre marker, placed
                # just below the ruler tick numbers (in the ruler->waveform gap)
                # so it reads as the marker's label and never collides with the
                # time labels above it.
                _ctc = seconds_to_timecode(center_time)
                p.setFont(QtGui.QFont("Segoe UI Semibold", 8))
                pill_w = 124
                pill_h = 16
                pill_y = ruler.bottom() + 3
                pill_x = max(ruler.left() + 2, min(cx - pill_w / 2.0, ruler.right() - pill_w - 2))
                _pill = QRectF(pill_x, pill_y, pill_w, pill_h)
                p.setBrush(QtGui.QBrush(QtGui.QColor(20, 12, 32, 235)))
                p.setPen(QtGui.QPen(guide_color, 1))
                p.drawRoundedRect(_pill, 4, 4)
                p.setPen(QtGui.QPen(QtGui.QColor("#e9d5ff"), 1))
                p.drawText(_pill, Qt.AlignCenter, f"Center  {_ctc}")
            ph_x = self._time_to_x(self.playhead)
            if wave.left() - 4 <= ph_x <= wave.right() + 4:
                halo = QtGui.QColor(PALETTE["playhead_halo"])
                halo.setAlpha(210)
                arrow_top = ruler.top() + 1
                arrow_tip = ruler.top() + 19
                p.setPen(QtGui.QPen(halo, 9))
                p.drawLine(QPointF(ph_x, arrow_tip), QPointF(ph_x, wave.bottom() + 8))
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["playhead"]), 5))
                p.drawLine(QPointF(ph_x, arrow_tip), QPointF(ph_x, wave.bottom() + 8))
                p.setBrush(QtGui.QBrush(QtGui.QColor(PALETTE["playhead"])))
                p.setPen(QtGui.QPen(halo, 2))
                p.drawPolygon(QtGui.QPolygonF([
                    QPointF(ph_x - 12, arrow_top),
                    QPointF(ph_x + 12, arrow_top),
                    QPointF(ph_x, arrow_tip),
                ]))

        def _hit_cut(self, x, y):
            r = self._wave_rect()
            if not r.contains(QPointF(x, y)):
                return -1
            start = self.view_start
            end = start + self.view_span
            for idx, (s, e) in enumerate(self.cut_ranges):
                if e < start or s > end:
                    continue
                if self._time_to_x(max(s, start)) - 3 <= x <= self._time_to_x(min(e, end)) + 3:
                    return idx
            return -1

        def _hit_cut_edge(self, x, y):
            r = self._wave_rect().adjusted(0, -28, 0, 0)
            if not r.contains(QPointF(x, y)):
                return None
            start = self.view_start
            end = start + self.view_span
            for idx, (s, e) in enumerate(self.cut_ranges):
                if e < start or s > end:
                    continue
                x1 = self._time_to_x(max(s, start))
                x2 = self._time_to_x(min(e, end))
                if abs(x - x1) <= 10:
                    return idx, "start"
                if abs(x - x2) <= 10:
                    return idx, "end"
            return None

        def _hit_cut_bar(self, x, y):
            point = QPointF(x, y)
            start = self.view_start
            end = start + self.view_span
            for idx, (s, e) in enumerate(self.cut_ranges):
                if e < start or s > end:
                    continue
                if self._cut_bar_rect(max(s, start), min(e, end)).contains(point):
                    return idx
            return -1

        def _hit_separator(self, x, y):
            r = self._timeline_rect().adjusted(0, -4, 0, 4)
            if not r.contains(QPointF(x, y)):
                return -1
            start = self.view_start
            end = start + self.view_span
            for idx, value in enumerate(self.separator_points):
                if value < start or value > end:
                    continue
                if abs(self._time_to_x(value) - x) <= 18:
                    return idx
            return -1

        def _hit_marker(self, x, y):
            r = self._timeline_rect().adjusted(0, -4, 0, 4)
            if not r.contains(QPointF(x, y)):
                return None
            start = self.view_start
            end = start + self.view_span
            for name, value in (("in", self.mark_in), ("out", self.mark_out)):
                if value is None or value < start or value > end:
                    continue
                if abs(self._time_to_x(value) - x) <= 18:
                    return name
            return None

        def mousePressEvent(self, event):
            pos = event.position()
            if event.button() == Qt.RightButton:
                idx = self._hit_cut(pos.x(), pos.y())
                if idx >= 0:
                    self.selected_cut = idx
                    self.selected_separator = -1
                    self.cut_selected.emit(idx)
                    self.update()
                return
            if event.button() != Qt.LeftButton:
                return
            cut_edge = self._hit_cut_edge(pos.x(), pos.y())
            if cut_edge is not None:
                self.selected_cut = int(cut_edge[0])
                self.selected_separator = -1
                self.selected_marker = None
                self._dragging = True
                self._drag_kind = "cut_edge"
                self._drag_target = cut_edge
                self._drag_origin = QPointF(pos.x(), pos.y())
                self._start_span = self.view_span
                self.cut_selected.emit(self.selected_cut)
                self.update()
                return
            cut_bar = self._hit_cut_bar(pos.x(), pos.y())
            if cut_bar >= 0:
                self.selected_cut = int(cut_bar)
                self.selected_separator = -1
                self.selected_marker = None
                self._dragging = True
                self._drag_kind = "cut_move"
                self._drag_target = int(cut_bar)
                self._drag_origin = QPointF(pos.x(), pos.y())
                self._drag_playhead_start = self._x_to_time(pos.x())
                self._move_origin = self.cut_ranges[int(cut_bar)]
                self.cut_selected.emit(self.selected_cut)
                self.update()
                return
            marker_name = self._hit_marker(pos.x(), pos.y())
            if marker_name is not None:
                self.selected_marker = marker_name
                self.selected_separator = -1
                self.selected_cut = -1
                self._dragging = True
                self._drag_kind = "marker"
                self._drag_target = marker_name
                self._drag_origin = QPointF(pos.x(), pos.y())
                self._start_span = self.view_span
                self.cut_selected.emit(-1)
                self.separator_selected.emit(-1)
                self.update()
                return
            sep_idx = self._hit_separator(pos.x(), pos.y())
            if sep_idx >= 0:
                self.selected_separator = sep_idx
                self.selected_cut = -1
                self.selected_marker = None
                self._dragging = True
                self._drag_kind = "separator"
                self._drag_target = sep_idx
                self._drag_origin = QPointF(pos.x(), pos.y())
                self._start_span = self.view_span
                self.separator_selected.emit(sep_idx)
                self.update()
                return
            self.selected_marker = None
            self.selected_separator = -1
            self.selected_cut = -1
            self.cut_selected.emit(-1)
            self.separator_selected.emit(-1)
            self._dragging = True
            self._drag_kind = "playhead"
            self._drag_target = None
            self._drag_origin = QPointF(pos.x(), pos.y())
            self._last_drag_pos = QPointF(pos.x(), pos.y())
            self._vertical_zoom_lock = False
            t = self._x_to_time(pos.x())
            self._drag_playhead_start = t
            self._cti_zoom_focus_time = t
            self._cti_zoom_start_y = pos.y()
            self._start_span = self.view_span
            self.set_playhead(t)
            self.seek_requested.emit(t)

        def mouseMoveEvent(self, event):
            if not self._dragging:
                pos = event.position()
                if self._hit_cut_edge(pos.x(), pos.y()) is not None:
                    self.setCursor(Qt.SizeHorCursor)
                elif self._hit_cut_bar(pos.x(), pos.y()) >= 0:
                    self.setCursor(Qt.SizeAllCursor)
                elif self._hit_marker(pos.x(), pos.y()) is not None or self._hit_separator(pos.x(), pos.y()) >= 0:
                    self.setCursor(Qt.SizeAllCursor)
                else:
                    self.unsetCursor()
                return
            pos = event.position()
            t = self._x_to_time(pos.x())
            if self._drag_kind == "marker" and self._drag_target in {"in", "out"}:
                t = self._snap_time(t, exclude_marker=str(self._drag_target))
                if self._drag_target == "in":
                    self.mark_in = t
                else:
                    self.mark_out = t
                self.marker_moved.emit(str(self._drag_target), t)
                self.update()
                return
            if self._drag_kind == "separator" and isinstance(self._drag_target, int):
                idx = int(self._drag_target)
                if 0 <= idx < len(self.separator_points):
                    t = self._snap_time(t, exclude_separator=idx)
                    t = max(1e-6, min(self.duration - 1e-6, t))
                    self.separator_points[idx] = t
                    self.separator_moved.emit(idx, t)
                    self.update()
                return
            if self._drag_kind == "cut_edge" and isinstance(self._drag_target, tuple):
                idx, side = self._drag_target
                idx = int(idx)
                if 0 <= idx < len(self.cut_ranges):
                    s, e = self.cut_ranges[idx]
                    t = self._snap_time(t)
                    if side == "start":
                        s = min(t, e - 0.001)
                    else:
                        e = max(t, s + 0.001)
                    self.cut_ranges[idx] = (s, e)
                    self.selected_cut = idx
                    self.cut_selected.emit(idx)
                    self.update()
                return
            if self._drag_kind == "cut_move" and isinstance(self._drag_target, int):
                idx = int(self._drag_target)
                origin = getattr(self, "_move_origin", None)
                if origin is not None and 0 <= idx < len(self.cut_ranges):
                    s0, e0 = origin
                    width = max(0.001, e0 - s0)
                    delta = t - float(getattr(self, "_drag_playhead_start", t))
                    s = max(0.0, min(self.duration - width, s0 + delta))
                    self.cut_ranges[idx] = (s, s + width)
                    self.selected_cut = idx
                    self.cut_selected.emit(idx)
                    self.update()
                return
            if self._drag_origin is not None:
                last_pos = self._last_drag_pos or self._drag_origin
                inc_dx = pos.x() - last_pos.x()
                inc_dy = pos.y() - last_pos.y()
                total_dy = pos.y() - self._drag_origin.y()
                if (
                    not self._vertical_zoom_lock
                    and abs(total_dy) >= 4
                    and abs(inc_dy) > max(3.0, abs(inc_dx) * 0.85)
                ):
                    self._cti_zoom_focus_time = max(0.0, min(self.duration, t))
                    self._cti_zoom_start_y = pos.y()
                    self._start_span = self.view_span
                    if not self._vertical_zoom_lock:
                        self.set_playhead(self._cti_zoom_focus_time)
                        self.seek_requested.emit(self._cti_zoom_focus_time)
                    self._vertical_zoom_lock = True
                if self._vertical_zoom_lock:
                    focus = max(0.0, min(self.duration, self._cti_zoom_focus_time))
                    dy_zoom = pos.y() - float(getattr(self, "_cti_zoom_start_y", pos.y()))
                    self.view_span = max(0.05, min(self.duration, self._start_span * (2.0 ** (dy_zoom / 150.0))))
                    self.view_start = focus - 0.5 * self.view_span
                    self._clamp_view()
                    self.view_changed.emit()
                    self.set_playhead(focus)
                    self.seek_requested.emit(focus)
                    self.update()
                    self._last_drag_pos = QPointF(pos.x(), pos.y())
                    return
            self.set_playhead(t)
            self.seek_requested.emit(t)
            self._cti_zoom_focus_time = self.playhead
            self._last_drag_pos = QPointF(pos.x(), pos.y())

        def mouseReleaseEvent(self, _event):
            edited = self._drag_kind in {"marker", "separator", "cut_edge", "cut_move"}
            was_playhead = self._drag_kind == "playhead"
            if self._drag_kind == "separator":
                self.separator_points = sorted(set(round(float(value), 6) for value in self.separator_points))
            if self._drag_kind == "cut_edge":
                self.cut_ranges = normalize_ranges(self.cut_ranges, self.duration)
            self._dragging = False
            self._drag_kind = None
            self._drag_target = None
            self._drag_origin = None
            self._last_drag_pos = None
            self._move_origin = None
            self._vertical_zoom_lock = False
            self._cti_zoom_focus_time = self.playhead
            if edited:
                self.edit_finished.emit()
            # PREVIEW: after a playhead drag ends, request one final seek so the
            # preview frame for the landing position is decoded (it was skipped
            # during the drag to keep things responsive).
            if was_playhead:
                self.seek_requested.emit(self.playhead)

        def wheelEvent(self, event):
            delta = event.angleDelta().y()
            if not delta:
                return
            factor = 1 / 1.16 if delta > 0 else 1.16
            self.view_span = max(0.05, min(self.duration, self.view_span * factor))
            focus = max(0.0, min(self.duration, self.playhead))
            self.view_start = focus - 0.5 * self.view_span
            self._clamp_view()
            self.update()
            self.view_changed.emit()

    class FrameExtractWorker(QtCore.QThread):
        # PREVIEW FIX: the original referenced FrameExtractWorker here but the class
        # is only defined inside build_crop_editor's scope, so paused-seek frame
        # extraction raised NameError in the unified editor. Give it its own copy.
        finished_with_image = Signal(QtGui.QImage)

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
            tmp_dir = Path(tempfile.gettempdir()) / "ffmwiz_unified_qt"
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
                image = QtGui.QImage(str(out_path))
                self.finished_with_image.emit(image)
            except Exception:
                if not self.isInterruptionRequested():
                    self.finished_with_image.emit(QtGui.QImage())

    class UnifiedVideoEditorWindow(QMainWindow):
        def __init__(self, req):
            super().__init__()
            self.request = req
            raw_segments = list(req.get("join_segments") or [])
            self.join_segments: list[dict[str, object]] = []
            offset = 0.0
            if raw_segments:
                for idx, segment in enumerate(raw_segments):
                    seg_duration = max(0.001, float(segment.get("duration") or 0.001))
                    path = Path(segment.get("path") or req.get("input_path") or "")
                    self.join_segments.append(
                        {
                            "index": idx,
                            "path": path,
                            "name": str(segment.get("name") or path.name or f"Video {idx + 1}"),
                            "start": offset,
                            "end": offset + seg_duration,
                            "duration": seg_duration,
                            "label": f"Video {idx + 1}",
                        }
                    )
                    offset += seg_duration
            self.duration = float(offset if self.join_segments else (req.get("duration") or 0.0))
            self.fps = float(req.get("fps") or 25.0)
            self.source_w = int(req.get("source_w") or 1920)
            self.source_h = int(req.get("source_h") or 1080)
            self.input_path = Path(req.get("input_path") or "")
            self._active_segment_index = 0
            self._pending_segment_position_ms = None
            self._pending_segment_play = False
            self._switching_segment = False
            self.chapters = normalize_chapters(req.get("chapters") or [], self.duration)
            self.result = {"status": "canceled"}
            self._icon = _icon_loader(self, self.style())
            self._wave_temp = tempfile.TemporaryDirectory(prefix="ffmwiz_unified_waveform_")
            self._wave_path = Path(self._wave_temp.name) / "waveform.pcm"
            self._wave_proc = None
            # FFmWiz hands back KEEP ranges (the segments to keep). Internally the
            # editor stores CUT (removed) ranges, so convert KEEP -> CUT (the
            # complement) here; otherwise reopening would store cuts inverted.
            _initial_keep = [
                (float(s), float(e)) for s, e in (req.get("initial_keep_ranges") or [])
                if float(e) > float(s)
            ]
            if _initial_keep:
                self._cut_ranges: list[tuple[float, float]] = [
                    (float(s), float(e))
                    for s, e in invert_cuts_to_keep(
                        normalize_ranges(_initial_keep, self.duration), self.duration
                    )
                ]
            else:
                self._cut_ranges = []
            self._separator_points: list[float] = sorted({
                round(float(v), 6) for v in (req.get("initial_separator_points") or [])
                if 0.0 < float(v) < self.duration
            })
            self._mark_in: float | None = None
            self._mark_out: float | None = None
            # Crop/speed/reverse/include-audio need their widgets, so they are
            # applied after the UI is built (see _apply_initial_session_state).
            # Reopening the editor restores the previous session's edits instead
            # of starting from zero.
            self._initial_margins = [int(v) for v in (req.get("initial_margins") or [0, 0, 0, 0])][:4]
            while len(self._initial_margins) < 4:
                self._initial_margins.append(0)
            self._initial_speed = float(req.get("initial_speed") or 1.0)
            self._initial_reverse = bool(req.get("initial_reverse"))
            self._initial_include_audio = bool(req.get("initial_include_audio", req.get("has_audio")))
            self._history = HistoryStack(self._snapshot(), max_size=120)
            self._syncing_zoom = False
            self._syncing_view = False
            self._view_scroll_scale = 10000
            self._view_handle_width_px = None
            self._syncing_preview_zoom_control = False
            self._syncing_crop_controls = False
            self._zoom_presets = (10, 15, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 800, 1200, 1600, 3200)
            self._shortcuts = []
            self._restoring_snapshot = False
            self.player = None
            self.audio = None
            self.video_sink = None
            self._frame_worker = None
            self._pending_frame_request = None
            self._frame_request_started = 0.0
            self.setWindowTitle("FFmWiz Unified Video Editor")
            _apply_window_icon(self, self._icon)
            # PREVIEW: minimum wide enough for two 250px side columns plus the
            # 520px preview canvas (+ splitter handles/margins) so nothing clips.
            self.setMinimumSize(1120, 640)
            # Kick off the waveform decode NOW (async ffmpeg) so it runs in PARALLEL
            # with building the UI. By the time the window is shown the PCM is usually
            # ready, instead of the waveform appearing seconds after the window.
            # PERF: each build phase is timed so the dominant cold-start cost is
            # visible in the log (window-build vs deferred multimedia backend load).
            _phase_t = time.perf_counter()
            self._start_waveform()
            _gui_log_debug(f"unified _start_waveform in {time.perf_counter() - _phase_t:.3f}s", force=True)
            _phase_t = time.perf_counter()
            self._build_ui()
            _gui_log_debug(f"unified _build_ui in {time.perf_counter() - _phase_t:.3f}s", force=True)
            _phase_t = time.perf_counter()
            self._apply_initial_session_state()
            _gui_log_debug(f"unified _apply_initial_session_state in {time.perf_counter() - _phase_t:.3f}s", force=True)
            _phase_t = time.perf_counter()
            self._refresh_all()
            _gui_log_debug(f"unified _refresh_all in {time.perf_counter() - _phase_t:.3f}s", force=True)
            QtCore.QTimer.singleShot(120, self._setup_player)
            # Stream the (already-built) side-column panels in after the first paint so
            # the window appears fast instead of blocking on the column's layout/polish.
            QtCore.QTimer.singleShot(16, self._attach_next_panel)

        def _snapshot(self):
            margins = tuple(int(v) for v in getattr(self, "preview", None).margins) if hasattr(self, "preview") else (0, 0, 0, 0)
            return UnifiedSnapshot(
                margins=margins,
                cut_ranges=tuple((float(s), float(e)) for s, e in normalize_ranges(getattr(self, "_cut_ranges", []), self.duration)),
                separators=tuple(float(v) for v in getattr(self, "_separator_points", [])),
                mark_in=getattr(self, "_mark_in", None),
                mark_out=getattr(self, "_mark_out", None),
                speed=float(self._speed()) if hasattr(self, "speed_combo") else 1.0,
                reverse=bool(self.reverse_box.isChecked()) if hasattr(self, "reverse_box") else False,
                include_audio=bool(self.include_audio_box.isChecked()) if hasattr(self, "include_audio_box") else bool(self.request.get("has_audio")),
            )

        def _restore_snapshot(self, snap):
            self._restoring_snapshot = True
            try:
                self.preview.set_margins(list(snap.margins))
                self._cut_ranges = list(snap.cut_ranges)
                self._separator_points = list(snap.separators)
                self._mark_in = snap.mark_in
                self._mark_out = snap.mark_out
                self.speed_combo.setCurrentText(f"{snap.speed * 100:g}%")
                self.factor_combo.setCurrentText(f"{snap.speed:g}x")
                if hasattr(self, "speed_slider"):
                    self.speed_slider.setValue(int(round(snap.speed * 100.0)))
                self.reverse_box.setChecked(bool(snap.reverse))
                self.include_audio_box.setChecked(bool(snap.include_audio))
                self._apply_speed_to_player()
                self._refresh_all()
            finally:
                self._restoring_snapshot = False

        def _commit_history(self):
            if getattr(self, "_restoring_snapshot", False):
                return
            self._history.push(self._snapshot())
            self._update_undo_redo()

        def _apply_initial_session_state(self):
            # Apply crop/cuts/split/speed/reverse/include-audio carried over from
            # a previous session (passed in the request). Re-baseline history so
            # the restored state is the clean starting point for undo/redo.
            initial = UnifiedSnapshot(
                margins=tuple(self._initial_margins),
                cut_ranges=tuple(self._cut_ranges),
                separators=tuple(self._separator_points),
                mark_in=None,
                mark_out=None,
                speed=float(self._initial_speed),
                reverse=bool(self._initial_reverse),
                include_audio=bool(self._initial_include_audio),
            )
            self._restore_snapshot(initial)
            self._history = HistoryStack(self._snapshot(), max_size=120)
            self._update_undo_redo()

        def _undo(self):
            snap = self._history.undo()
            if snap is not None:
                self._restore_snapshot(snap)
            self._update_undo_redo()

        def _redo(self):
            snap = self._history.redo()
            if snap is not None:
                self._restore_snapshot(snap)
            self._update_undo_redo()

        def _update_undo_redo(self):
            self.btn_undo.setEnabled(self._history.can_undo())
            self.btn_redo.setEnabled(self._history.can_redo())

        def _build_ui(self):
            central = QWidget()
            central.setObjectName("central")
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            # PREVIEW: tighter outer margins/spacing to reclaim vertical space.
            root.setContentsMargins(8, 5, 8, 5)
            root.setSpacing(4)

            # PERF PROBE: the cold-start cost lives inside _build_ui. Isolate the two
            # most likely first-touch costs so the next cold log pinpoints the cause:
            #  - font database init (first text widget enumerates system fonts), and
            #  - the image/SVG icon plugin load (first icon read).
            _t = time.perf_counter()
            _probe_lbl = QLabel("0")
            _probe_lbl.fontMetrics().height()  # force QFontDatabase population
            _gui_log_debug(f"unified probe font-db init in {time.perf_counter() - _t:.3f}s", force=True)
            _probe_lbl.deleteLater()
            _t = time.perf_counter()
            self._icon("play", QStyle.SP_MediaPlay)  # force icon/image plugin + first asset read
            _gui_log_debug(f"unified probe first-icon load in {time.perf_counter() - _t:.3f}s", force=True)

            header = QFrame()
            header.setObjectName("header")
            h = QHBoxLayout(header)
            h.setContentsMargins(12, 5, 12, 5)
            title = QLabel("FFmWiz Unified Video Editor")
            title.setObjectName("title")
            h.addWidget(title)
            h.addSpacing(16)
            h.addWidget(QLabel(f"Source  {self.input_path.name}"))
            h.addStretch(1)
            self.btn_undo = QPushButton(self._icon("undo", QStyle.SP_ArrowBack), " Undo (Ctrl+Z)")
            self.btn_undo.clicked.connect(self._undo)
            h.addWidget(self.btn_undo)
            self.btn_redo = QPushButton(self._icon("redo", QStyle.SP_ArrowForward), " Redo (Ctrl+Y)")
            self.btn_redo.clicked.connect(self._redo)
            h.addWidget(self.btn_redo)
            root.addWidget(header)

            playback_panel = QFrame()
            playback_panel.setObjectName("panel")
            playback_outer = QVBoxLayout(playback_panel)
            playback_outer.setContentsMargins(8, 4, 8, 4)
            playback_outer.setSpacing(4)
            playback_header = QLabel("Playback")
            playback_header.setObjectName("sectionLabel")
            playback_outer.addWidget(playback_header)
            # PREVIEW: Playback laid out vertically so it fits a left side column.
            self.btn_play = QPushButton(self._icon("play", QStyle.SP_MediaPlay), " Play (Space)")
            self.btn_play.setObjectName("primary")
            self.btn_play.clicked.connect(self.toggle_playback)
            playback_outer.addWidget(self.btn_play)
            self.cti_time_label = QLabel("00:00:00.000")
            self.cti_time_label.setObjectName("status")
            self.cti_time_label.setAlignment(Qt.AlignCenter)
            self.cti_time_label.setToolTip("Current timeline position (CTI).")
            playback_outer.addWidget(self.cti_time_label)
            _seek_grid = QGridLayout()
            _seek_grid.setSpacing(4)
            _btn_home = self._btn(" Home", lambda: self.seek(0.0), "skip_backward", QStyle.SP_MediaSkipBackward)
            _btn_end = self._btn(" End", lambda: self.seek(self.duration), "skip_forward", QStyle.SP_MediaSkipForward)
            _btn_m5 = self._btn(" -5s", lambda: self.seek(self.current_time() - 5.0), "seek_backward", QStyle.SP_MediaSeekBackward)
            _btn_m1 = self._btn(" -1s", lambda: self.seek(self.current_time() - 1.0), "seek_backward", QStyle.SP_MediaSeekBackward)
            _btn_p1 = self._btn(" +1s", lambda: self.seek(self.current_time() + 1.0), "seek_forward", QStyle.SP_MediaSeekForward)
            _btn_p5 = self._btn(" +5s", lambda: self.seek(self.current_time() + 5.0), "seek_forward", QStyle.SP_MediaSeekForward)
            for _b in (_btn_m5, _btn_m1, _btn_p1, _btn_p5):
                _b.setAutoRepeat(True)
                _b.setAutoRepeatDelay(260)
                _b.setAutoRepeatInterval(80)
            for _b in (_btn_home, _btn_end, _btn_m5, _btn_m1, _btn_p1, _btn_p5):
                _b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            _seek_grid.addWidget(_btn_home, 0, 0)
            _seek_grid.addWidget(_btn_end, 0, 1)
            _seek_grid.addWidget(_btn_m5, 1, 0)
            _seek_grid.addWidget(_btn_m1, 1, 1)
            _seek_grid.addWidget(_btn_p1, 2, 0)
            _seek_grid.addWidget(_btn_p5, 2, 1)
            playback_outer.addLayout(_seek_grid)
            _edge_row = QHBoxLayout()
            _edge_row.setSpacing(4)
            self.btn_prev_cut_edge = self._btn(" Prev Edge", lambda: self.seek_nearest_cut_edge(-1), "seek_backward", QStyle.SP_MediaSeekBackward)
            self.btn_prev_cut_edge.setToolTip("Go to the nearest previous cut edge (Ctrl+Alt+Left). Hold to repeat.")
            self.btn_prev_cut_edge.setAutoRepeat(True)
            self.btn_prev_cut_edge.setAutoRepeatDelay(260)
            self.btn_prev_cut_edge.setAutoRepeatInterval(90)
            self.btn_next_cut_edge = self._btn(" Next Edge", lambda: self.seek_nearest_cut_edge(1), "seek_forward", QStyle.SP_MediaSeekForward)
            self.btn_next_cut_edge.setToolTip("Go to the nearest next cut edge (Ctrl+Alt+Right). Hold to repeat.")
            self.btn_next_cut_edge.setAutoRepeat(True)
            self.btn_next_cut_edge.setAutoRepeatDelay(260)
            self.btn_next_cut_edge.setAutoRepeatInterval(90)
            for _b in (self.btn_prev_cut_edge, self.btn_next_cut_edge):
                _b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            _edge_row.addWidget(self.btn_prev_cut_edge)
            _edge_row.addWidget(self.btn_next_cut_edge)
            playback_outer.addLayout(_edge_row)
            self.btn_mute = QPushButton(self._icon("volume_meter_3", QStyle.SP_MediaVolume), " Mute (M)")
            self.btn_mute.clicked.connect(self.toggle_mute)
            playback_outer.addWidget(self.btn_mute)
            volume_frame = QFrame()
            volume_frame.setObjectName("inlineControlFrame")
            volume_layout = QHBoxLayout(volume_frame)
            volume_layout.setContentsMargins(8, 2, 8, 2)
            volume_layout.setSpacing(5)
            self.btn_vol_down = QPushButton("◀")
            self.btn_vol_down.setObjectName("timelineZoomArrow")
            self.btn_vol_down.setFixedSize(20, 22)
            self.btn_vol_down.setAutoRepeat(True)
            self.btn_vol_down.setAutoRepeatDelay(240)
            self.btn_vol_down.setAutoRepeatInterval(60)
            self.btn_vol_down.setToolTip("Decrease volume")
            self.btn_vol_down.clicked.connect(lambda: self.volume_slider.setValue(self.volume_slider.value() - 5))
            volume_layout.addWidget(self.btn_vol_down)
            self.volume_slider = QSlider(Qt.Horizontal)
            self.volume_slider.setObjectName("cutVolumeSlider")
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(60)
            self.volume_slider.setFixedHeight(24)
            self.volume_slider.valueChanged.connect(self._on_volume_changed)
            volume_layout.addWidget(self.volume_slider, 1)
            self.btn_vol_up = QPushButton("▶")
            self.btn_vol_up.setObjectName("timelineZoomArrow")
            self.btn_vol_up.setFixedSize(20, 22)
            self.btn_vol_up.setAutoRepeat(True)
            self.btn_vol_up.setAutoRepeatDelay(240)
            self.btn_vol_up.setAutoRepeatInterval(60)
            self.btn_vol_up.setToolTip("Increase volume")
            self.btn_vol_up.clicked.connect(lambda: self.volume_slider.setValue(self.volume_slider.value() + 5))
            volume_layout.addWidget(self.btn_vol_up)
            self.volume_label = QLabel("60%")
            self.volume_label.setObjectName("dim")
            self.volume_label.setMinimumWidth(36)
            self.volume_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            volume_layout.addWidget(self.volume_label)
            playback_outer.addWidget(volume_frame)

            cut_panel = QFrame()
            cut_panel.setObjectName("markerPanel")
            cut_box = QVBoxLayout(cut_panel)
            cut_box.setContentsMargins(8, 4, 8, 4)
            cut_box.setSpacing(4)
            cut_header = QLabel("Markers / Cuts")
            cut_header.setObjectName("sectionLabel")
            cut_box.addWidget(cut_header)
            # PREVIEW: 2-column grid so Markers/Cuts fits a right side column.
            cut_grid = QGridLayout()
            cut_grid.setHorizontalSpacing(6)
            cut_grid.setVerticalSpacing(4)
            self.btn_mark_in = self._btn(" Mark In (I)", self.mark_in, "mark_in", None)
            self.btn_mark_in.setObjectName("markIn")
            self.btn_mark_out = self._btn(" Mark Out (O)", self.mark_out, "mark_out", None)
            self.btn_mark_out.setObjectName("markOut")
            add_cut = self._btn("Add Cut(s) (A)", self.add_cut)
            add_cut.setObjectName("green")
            self.btn_add_separator = self._btn("Add Split (S)", self.add_separator)
            self.btn_add_separator.setObjectName("separator")
            self.btn_add_separator.setToolTip("Add a Split point. Split divides the final processed output into multiple parts.")
            self.btn_invert = self._btn("Invert Cuts (Ctrl+Shift+I)", self.invert_cuts)
            self.btn_invert.setObjectName("purple")
            self.btn_invert.setToolTip("Invert cut ranges (Ctrl+Shift+I).")
            self.btn_convert_marker = self._btn("Convert In/Out (Ctrl+I)", self.convert_selected_marker)
            self.btn_convert_marker.setObjectName("convertMarker")
            self.btn_convert_marker.setToolTip("Select Mark In or Mark Out, then convert it to the opposite marker type (Ctrl+I).")
            self.btn_delete_markers = self._btn("Del Marker(s) (Del)", self.delete_selected_markers)
            self.btn_delete_markers.setObjectName("danger")
            self.btn_delete_markers.setToolTip("Delete the selected Mark In or Mark Out marker (Del).")
            self.btn_delete_separator = self._btn("Del Split (Del)", self.delete_selected_separator)
            self.btn_delete_separator.setObjectName("dangerAlt")
            self.btn_delete_separator.setToolTip("Delete the selected Split point (Del).")
            self.btn_delete_cut = self._btn("Del Cut (Del)", self.delete_selected_cut)
            self.btn_delete_cut.setObjectName("dangerCut")
            self.btn_delete_cut.setToolTip("Delete the selected cut (Del).")
            self.btn_delete_all = self._btn("Del All Cuts", self.delete_all_cuts)
            self.btn_delete_all.setObjectName("danger")
            _cut_cells = [
                self.btn_mark_in, self.btn_mark_out,
                add_cut, self.btn_add_separator,
                self.btn_invert, self.btn_convert_marker,
                self.btn_delete_markers, self.btn_delete_separator,
                self.btn_delete_cut, self.btn_delete_all,
            ]
            for _i, _b in enumerate(_cut_cells):
                _b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                cut_grid.addWidget(_b, _i // 2, _i % 2)
            cut_box.addLayout(cut_grid)
            self.snap_box = QCheckBox("Magnetic snap (CTI / marks / splits)")
            self.snap_box.setChecked(True)
            self.snap_box.setToolTip(
                "When on, dragged cut edges, marks and split points snap to the playhead,\n"
                "other marks, splits and the clip ends. The pull is pixel-based, so it\n"
                "weakens automatically as you zoom in. Turn it off for fully free-hand,\n"
                "frame-exact placement."
            )
            self.snap_box.toggled.connect(
                lambda on: setattr(self.timeline, "snap_enabled", bool(on)) if hasattr(self, "timeline") else None
            )
            cut_box.addWidget(self.snap_box)

            view_panel = QFrame()
            view_panel.setObjectName("panel")
            # PREVIEW: 2-column grid with short labels (full text in tooltips) so
            # nothing clips inside the narrow side column.
            view = QGridLayout(view_panel)
            view.setContentsMargins(8, 6, 8, 6)
            view.setHorizontalSpacing(6)
            view.setVerticalSpacing(5)
            view.setColumnStretch(0, 1)
            view.setColumnStretch(1, 1)
            view_title = QLabel("Crop / View")
            view_title.setObjectName("sectionLabel")
            view.addWidget(view_title, 0, 0, 1, 2)
            self.btn_hand_tool = self._btn(" Hand (H)", lambda: self.set_preview_tool("hand"), "hand_open", None)
            self.btn_hand_tool.setObjectName("tool")
            self.btn_hand_tool.setToolTip("Hand / pan tool (H). Double-click to reset the view.")
            self.btn_hand_tool.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.btn_hand_tool.installEventFilter(self)
            view.addWidget(self.btn_hand_tool, 1, 0)
            self.btn_zoom_tool = self._btn(" Zoom (Z)", lambda: self.set_preview_tool("zoom"), "zoom_tool_orange", None)
            self.btn_zoom_tool.setObjectName("tool")
            self.btn_zoom_tool.setToolTip("Zoom tool (Z).")
            self.btn_zoom_tool.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            view.addWidget(self.btn_zoom_tool, 1, 1)
            zoom_caption = QLabel("Zoom")
            zoom_caption.setObjectName("timelineControlLabel")
            view.addWidget(zoom_caption, 2, 0)
            self.zoom_percent_box = QFrame()
            self.zoom_percent_box.setObjectName("zoomPercentBox")
            self.zoom_percent_box.setFixedHeight(34)
            self.zoom_percent_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            zoom_box_layout = QHBoxLayout(self.zoom_percent_box)
            zoom_box_layout.setContentsMargins(0, 0, 0, 0)
            zoom_box_layout.setSpacing(0)
            self.preview_zoom_combo = QComboBox()
            self.preview_zoom_combo.setObjectName("zoomPercentCombo")
            self.preview_zoom_combo.setEditable(True)
            self.preview_zoom_combo.setInsertPolicy(QComboBox.NoInsert)
            self.preview_zoom_combo.setFixedHeight(32)
            self.preview_zoom_combo.setToolTip("Preview zoom presets. Type a percent value and press Enter to zoom manually.")
            self.preview_zoom_combo.addItems([f"{value}%" for value in self._zoom_presets])
            self.preview_zoom_combo.setCurrentText("100%")
            if self.preview_zoom_combo.lineEdit() is not None:
                self.preview_zoom_combo.lineEdit().returnPressed.connect(self._apply_preview_zoom_text)
                self.preview_zoom_combo.lineEdit().editingFinished.connect(self._apply_preview_zoom_text)
            self.preview_zoom_combo.activated.connect(lambda _idx: self._apply_preview_zoom_text())
            zoom_box_layout.addWidget(self.preview_zoom_combo)
            view.addWidget(self.zoom_percent_box, 2, 1)
            self.btn_crop_overlay = self._btn("Hide Crop Overlay (Ctrl+U)", self.toggle_crop_overlay)
            self.btn_crop_overlay.setObjectName("overlayToggle")
            self.btn_crop_overlay.setToolTip("Show or hide crop handles and guide lines.")
            self.btn_crop_overlay.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            view.addWidget(self.btn_crop_overlay, 3, 0, 1, 2)
            btn_reset_view = self._btn("Reset View (Ctrl+0)", self.reset_view)
            btn_reset_view.setToolTip("Reset zoom and pan.")
            btn_reset_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            view.addWidget(btn_reset_view, 4, 0, 1, 2)
            btn_reset_crop = self._btn("Reset Crop (Ctrl+R)", self.reset_crop)
            btn_reset_crop.setToolTip("Reset crop to the full frame.")
            btn_reset_crop.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            view.addWidget(btn_reset_crop, 5, 0, 1, 2)

            crop_number_frame = QFrame()
            crop_number_frame.setObjectName("panel")
            # PREVIEW: compact, symmetric arrow-key cross. Top above, Left/Right
            # mirrored on the middle row, Bottom below. All four cells are built
            # identically (centered label over a fixed-width centered field) so
            # the cross is perfectly even. Same signals/keys as the original.
            crop_grid = QGridLayout(crop_number_frame)
            crop_grid.setContentsMargins(8, 4, 8, 4)
            crop_grid.setHorizontalSpacing(2)
            crop_grid.setVerticalSpacing(1)
            crop_title = QLabel("Crop margins")
            crop_title.setObjectName("sectionLabel")
            crop_grid.addWidget(crop_title, 0, 0, 1, 5)
            self.crop_spinboxes: dict[str, QSpinBox] = {}

            def _make_crop_field(_key, _maximum):
                spin = QSpinBox()
                spin.setObjectName("cropNumberField")
                spin.setButtonSymbols(QSpinBox.UpDownArrows)   # integrated steppers (arrow icons via QSS)
                spin.setKeyboardTracking(True)
                spin.setRange(0, max(0, int(_maximum)))
                spin.setAlignment(Qt.AlignCenter)
                spin.setFixedWidth(70)
                spin.setFixedHeight(28)
                spin.setToolTip(f"Crop {_key} margin in pixels — type, or use the arrows.")
                spin.valueChanged.connect(lambda _value, _k=_key: self._on_crop_field_changed(_k))
                spin.editingFinished.connect(self._on_crop_field_edit_finished)
                spin.installEventFilter(self)
                if spin.lineEdit() is not None:
                    spin.lineEdit().installEventFilter(self)
                self.crop_spinboxes[_key] = spin
                return spin

            def _axis_label(_text):
                lab = QLabel(_text)
                lab.setObjectName("timelineControlLabel")
                return lab

            # Tight, centred cross: the outer columns (0 and 4) absorb the slack
            # so the four boxes pack close together in the middle; the steppers
            # live inside each field.
            _top_lab = _axis_label("Top"); _top_lab.setAlignment(Qt.AlignCenter)
            crop_grid.addWidget(_top_lab, 1, 2, alignment=Qt.AlignHCenter | Qt.AlignBottom)
            crop_grid.addWidget(_make_crop_field("top", self.source_h - 16), 2, 2, alignment=Qt.AlignHCenter)
            _left_w = QWidget(); _lh = QHBoxLayout(_left_w)
            _lh.setContentsMargins(0, 0, 0, 0); _lh.setSpacing(3)
            _lh.addWidget(_axis_label("Left")); _lh.addWidget(_make_crop_field("left", self.source_w - 16))
            crop_grid.addWidget(_left_w, 3, 1, alignment=Qt.AlignRight | Qt.AlignVCenter)
            _right_w = QWidget(); _rh = QHBoxLayout(_right_w)
            _rh.setContentsMargins(0, 0, 0, 0); _rh.setSpacing(3)
            _rh.addWidget(_make_crop_field("right", self.source_w - 16)); _rh.addWidget(_axis_label("Right"))
            crop_grid.addWidget(_right_w, 3, 3, alignment=Qt.AlignLeft | Qt.AlignVCenter)
            crop_grid.addWidget(_make_crop_field("bottom", self.source_h - 16), 4, 2, alignment=Qt.AlignHCenter)
            _bot_lab = _axis_label("Bottom"); _bot_lab.setAlignment(Qt.AlignCenter)
            crop_grid.addWidget(_bot_lab, 5, 2, alignment=Qt.AlignHCenter | Qt.AlignTop)
            crop_note = QLabel(
                "Crop is auto-aligned to even dimensions to keep the chroma phase "
                "correct so the video colors are not damaged."
            )
            crop_note.setObjectName("tip")
            crop_note.setWordWrap(True)
            crop_grid.addWidget(crop_note, 6, 0, 1, 5)
            crop_grid.setColumnStretch(0, 1)
            crop_grid.setColumnStretch(1, 0)
            crop_grid.setColumnStretch(2, 0)
            crop_grid.setColumnStretch(3, 0)
            crop_grid.setColumnStretch(4, 1)

            speed_frame = QFrame()
            speed_frame.setObjectName("panel")
            # PREVIEW: Speed / Reverse stacked vertically so it fits a side column.
            speed_box = QVBoxLayout(speed_frame)
            speed_box.setContentsMargins(8, 4, 8, 4)
            speed_box.setSpacing(4)
            speed_title = QLabel("Speed / Reverse")
            speed_title.setObjectName("sectionLabel")
            speed_box.addWidget(speed_title)
            _pct_row = QHBoxLayout()
            _pct_row.setSpacing(6)
            _pct_label = QLabel("Percent")
            _pct_label.setMinimumWidth(46)
            _pct_row.addWidget(_pct_label)
            self.speed_combo = QComboBox()
            self.speed_combo.setObjectName("speedValueCombo")
            self.speed_combo.setEditable(True)
            self.speed_combo.addItems(["25%", "50%", "75%", "100%", "125%", "150%", "175%", "200%", "250%", "300%"])
            self.speed_combo.setCurrentText("100%")
            self.speed_combo.setMinimumWidth(70)
            self.speed_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.speed_combo.installEventFilter(self)
            if self.speed_combo.lineEdit() is not None:
                self.speed_combo.lineEdit().installEventFilter(self)
            self.speed_combo.activated.connect(lambda _idx: self._on_speed_text_changed(self.speed_combo.currentText()))
            if self.speed_combo.lineEdit() is not None:
                self.speed_combo.lineEdit().returnPressed.connect(
                    lambda: self._on_speed_text_changed(self.speed_combo.currentText())
                )
                self.speed_combo.lineEdit().editingFinished.connect(
                    lambda: self._on_speed_text_changed(self.speed_combo.currentText())
                )
            _pct_row.addWidget(self.speed_combo, 1)
            speed_box.addLayout(_pct_row)
            _fac_row = QHBoxLayout()
            _fac_row.setSpacing(6)
            _fac_label = QLabel("Factor")
            _fac_label.setMinimumWidth(46)
            _fac_row.addWidget(_fac_label)
            self.factor_combo = QComboBox()
            self.factor_combo.setObjectName("speedValueCombo")
            self.factor_combo.setEditable(True)
            self.factor_combo.addItems(["0.25x", "0.5x", "0.75x", "1x", "1.25x", "1.5x", "1.75x", "2x", "2.5x", "3x"])
            self.factor_combo.setCurrentText("1x")
            self.factor_combo.setMinimumWidth(70)
            self.factor_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.factor_combo.installEventFilter(self)
            if self.factor_combo.lineEdit() is not None:
                self.factor_combo.lineEdit().installEventFilter(self)
            self.factor_combo.activated.connect(lambda _idx: self._on_factor_text_changed(self.factor_combo.currentText()))
            if self.factor_combo.lineEdit() is not None:
                self.factor_combo.lineEdit().returnPressed.connect(
                    lambda: self._on_factor_text_changed(self.factor_combo.currentText())
                )
                self.factor_combo.lineEdit().editingFinished.connect(
                    lambda: self._on_factor_text_changed(self.factor_combo.currentText())
                )
            _fac_row.addWidget(self.factor_combo, 1)
            speed_box.addLayout(_fac_row)
            speed_slider_frame = QFrame()
            speed_slider_frame.setObjectName("inlineControlFrame")
            speed_slider_frame_layout = QHBoxLayout(speed_slider_frame)
            speed_slider_frame_layout.setContentsMargins(8, 2, 8, 2)
            speed_slider_frame_layout.setSpacing(5)
            self.btn_speed_down = QPushButton("◀")
            self.btn_speed_down.setObjectName("timelineZoomArrow")
            self.btn_speed_down.setFixedSize(20, 22)
            self.btn_speed_down.setAutoRepeat(True)
            self.btn_speed_down.setAutoRepeatDelay(240)
            self.btn_speed_down.setAutoRepeatInterval(60)
            self.btn_speed_down.setToolTip("Slower")
            self.btn_speed_down.clicked.connect(lambda: self.speed_slider.setValue(self.speed_slider.value() - 5))
            speed_slider_frame_layout.addWidget(self.btn_speed_down)
            self.speed_slider = QSlider(Qt.Horizontal)
            self.speed_slider.setObjectName("timelineZoomSlider")
            self.speed_slider.setRange(5, 1000)
            self.speed_slider.setValue(100)
            self.speed_slider.setMinimumWidth(120)
            self.speed_slider.setToolTip("Speed control: 100% = normal speed, higher = faster, lower = slower.")
            self.speed_slider.valueChanged.connect(self._on_speed_slider_changed)
            self.speed_slider.sliderReleased.connect(self._commit_history)
            speed_slider_frame_layout.addWidget(self.speed_slider, 1)
            self.btn_speed_up = QPushButton("▶")
            self.btn_speed_up.setObjectName("timelineZoomArrow")
            self.btn_speed_up.setFixedSize(20, 22)
            self.btn_speed_up.setAutoRepeat(True)
            self.btn_speed_up.setAutoRepeatDelay(240)
            self.btn_speed_up.setAutoRepeatInterval(60)
            self.btn_speed_up.setToolTip("Faster")
            self.btn_speed_up.clicked.connect(lambda: self.speed_slider.setValue(self.speed_slider.value() + 5))
            speed_slider_frame_layout.addWidget(self.btn_speed_up)
            speed_box.addWidget(speed_slider_frame)
            _opt_row = QHBoxLayout()
            _opt_row.setSpacing(6)
            self.reverse_box = QCheckBox("Reverse")
            self.reverse_box.stateChanged.connect(lambda _v: self._on_reverse_toggled())
            _opt_row.addWidget(self.reverse_box)
            self.include_audio_box = QCheckBox("Sync all audio tracks")
            self.include_audio_box.setChecked(bool(self.request.get("has_audio")))
            self.include_audio_box.setEnabled(bool(self.request.get("has_audio")))
            self.include_audio_box.stateChanged.connect(lambda _v: self._commit_history())
            _opt_row.addWidget(self.include_audio_box)
            _opt_row.addStretch(1)
            speed_box.addLayout(_opt_row)
            # PREVIEW: tidy multi-line summary inside the Speed panel (the footer
            # status bar keeps the single-line version).
            self.summary_label = QLabel("")
            self.summary_label.setObjectName("status")
            self.summary_label.setWordWrap(True)
            speed_box.addWidget(self.summary_label)
            self.time_label = QLabel("")
            self.time_label.setObjectName("dim")
            self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            speed_box.addWidget(self.time_label)

            self.preview = UnifiedPreviewCanvas(self.source_w, self.source_h, self.duration)
            self.preview.margins_changed.connect(self._on_crop_changed)
            self.preview.edit_finished.connect(self._on_crop_edit_finished)
            self.preview.toggle_playback_requested.connect(self.toggle_playback)
            self.preview.zoom_changed.connect(self._sync_preview_zoom_combo)
            self.preview.set_tool("hand")

            self.timeline = UnifiedTimelineWidget(self.duration, self.fps)
            self.timeline.set_chapters(self.chapters)
            self.timeline.set_join_segments(self.join_segments)
            self.timeline.seek_requested.connect(self.seek)
            self.timeline.cut_selected.connect(self._on_cut_selected)
            self.timeline.separator_selected.connect(self._on_separator_selected)
            self.timeline.marker_moved.connect(self._on_marker_moved)
            self.timeline.separator_moved.connect(self._on_separator_moved)
            self.timeline.edit_finished.connect(self._on_timeline_edit_finished)
            self.timeline.view_changed.connect(self._sync_timeline_controls)

            self.editor_splitter = QSplitter(Qt.Vertical)
            self.editor_splitter.setChildrenCollapsible(False)
            self.editor_splitter.setHandleWidth(10)
            self.editor_splitter.addWidget(self.preview)
            self.editor_splitter.addWidget(self.timeline)
            # PREVIEW: bias the splitter strongly toward the video preview so it
            # expands vertically and the waveform/timeline stays compact.
            self.editor_splitter.setStretchFactor(0, 11)
            self.editor_splitter.setStretchFactor(1, 2)
            self.editor_splitter.setSizes([900, 170])

            # PREVIEW: dock ALL control panels into a single vertical column on
            # one side, grouped at the top with normal spacing. This frees the
            # whole other side for the video, which gets much larger.
            for _panel in (playback_panel, speed_frame, cut_panel, view_panel, crop_number_frame):
                _panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

            controls_col = QWidget()
            controls_col.setObjectName("sideColumn")
            controls_v = QVBoxLayout(controls_col)
            controls_v.setContentsMargins(0, 0, 6, 0)
            controls_v.setSpacing(8)
            controls_v.addStretch(1)
            # The panels are built (above) but NOT attached yet. Laying out + polishing
            # the whole side column at once blocks ~3.5s on this machine, delaying the
            # window. Instead we stream the panels in one-per-tick AFTER the first paint
            # (see _attach_next_panel), so the video/timeline show almost immediately and
            # the editor stays responsive while the side controls fill in.
            self._controls_v = controls_v
            self._deferred_panels = [playback_panel, cut_panel, view_panel, crop_number_frame, speed_frame]
            # Wrap in a scroll area so the panels keep their natural size and the
            # column scrolls on short windows instead of squishing/clipping.
            controls_scroll = QtWidgets.QScrollArea()
            controls_scroll.setObjectName("sideColumn")
            controls_scroll.setWidgetResizable(True)
            controls_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            controls_scroll.setWidget(controls_col)
            controls_scroll.setFixedWidth(338)   # wide enough that panels (and their right border) aren't clipped

            center_widget = QWidget()
            center_v = QVBoxLayout(center_widget)
            center_v.setContentsMargins(0, 0, 0, 0)
            center_v.setSpacing(0)
            center_v.addWidget(self.editor_splitter, 1)
            self.timeline_resize_grip = QFrame()
            self.timeline_resize_grip.setObjectName("timelineResizeGrip")
            self.timeline_resize_grip.setFixedHeight(8)
            self.timeline_resize_grip.setCursor(Qt.SizeVerCursor)
            self.timeline_resize_grip.setToolTip("Drag to resize the waveform/timeline panel.")
            self.timeline_resize_grip.installEventFilter(self)
            center_v.addWidget(self.timeline_resize_grip)

            # PREVIEW: a plain row (not a splitter) so the centre always fills the
            # remaining width with no dead gap — controls on the left, video+timeline
            # take everything else.
            center_row = QHBoxLayout()
            center_row.setContentsMargins(0, 0, 0, 0)
            center_row.setSpacing(8)
            center_row.addWidget(controls_scroll)
            _col_divider = QFrame()
            _col_divider.setFixedWidth(1)
            _col_divider.setStyleSheet(f"background-color: {PALETTE['border_strong']};")
            center_row.addWidget(_col_divider)
            center_row.addWidget(center_widget, 1)
            root.addLayout(center_row, 1)

            nav_frame = QFrame()
            nav_frame.setObjectName("panel")
            nav_row = QHBoxLayout(nav_frame)
            nav_row.setContentsMargins(10, 8, 10, 8)
            nav_row.setSpacing(8)
            view_label = QLabel("Timeline view")
            view_label.setObjectName("timelineControlLabel")
            nav_row.addWidget(view_label)
            view_frame = QFrame()
            view_frame.setObjectName("timelineViewFrame")
            view_frame_layout = QHBoxLayout(view_frame)
            view_frame_layout.setContentsMargins(4, 2, 4, 2)
            view_frame_layout.setSpacing(3)
            self.btn_view_left = QPushButton("◀")
            self.btn_view_left.setObjectName("timelineViewArrow")
            self.btn_view_left.setFixedSize(16, 16)
            self.btn_view_left.setAutoRepeat(True)
            self.btn_view_left.setAutoRepeatDelay(220)
            self.btn_view_left.setAutoRepeatInterval(70)
            self.btn_view_left.clicked.connect(lambda: self._nudge_view_scroll(-1))
            view_frame_layout.addWidget(self.btn_view_left)
            self.view_scroll = QSlider(Qt.Horizontal)
            self.view_scroll.setObjectName("timelineViewSlider")
            self.view_scroll.setTracking(True)
            self.view_scroll.setRange(0, self._view_scroll_scale)
            self.view_scroll.setMinimumWidth(420)
            self.view_scroll.valueChanged.connect(self._on_view_scroll)
            self.view_scroll.installEventFilter(self)
            view_frame_layout.addWidget(self.view_scroll, 1)
            self.btn_view_right = QPushButton("▶")
            self.btn_view_right.setObjectName("timelineViewArrow")
            self.btn_view_right.setFixedSize(16, 16)
            self.btn_view_right.setAutoRepeat(True)
            self.btn_view_right.setAutoRepeatDelay(220)
            self.btn_view_right.setAutoRepeatInterval(70)
            self.btn_view_right.clicked.connect(lambda: self._nudge_view_scroll(1))
            view_frame_layout.addWidget(self.btn_view_right)
            nav_row.addWidget(view_frame, 1)
            zoom_label = QLabel("Timeline zoom")
            zoom_label.setObjectName("timelineControlLabel")
            nav_row.addWidget(zoom_label)
            zoom_frame = QFrame()
            zoom_frame.setObjectName("timelineViewFrame")
            zoom_frame_layout = QHBoxLayout(zoom_frame)
            zoom_frame_layout.setContentsMargins(4, 2, 4, 2)
            zoom_frame_layout.setSpacing(3)
            self.btn_zoom_left = QPushButton("◀")
            self.btn_zoom_left.setObjectName("timelineZoomArrow")
            self.btn_zoom_left.setFixedSize(16, 16)
            self.btn_zoom_left.setAutoRepeat(True)
            self.btn_zoom_left.setAutoRepeatDelay(220)
            self.btn_zoom_left.setAutoRepeatInterval(70)
            self.btn_zoom_left.clicked.connect(lambda: self._nudge_timeline_zoom(-1))
            zoom_frame_layout.addWidget(self.btn_zoom_left)
            self.zoom_slider = QSlider(Qt.Horizontal)
            self.zoom_slider.setObjectName("timelineZoomSlider")
            self.zoom_slider.setRange(0, 100)
            self.zoom_slider.setMinimumWidth(420)
            self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
            zoom_frame_layout.addWidget(self.zoom_slider, 1)
            self.btn_zoom_right = QPushButton("▶")
            self.btn_zoom_right.setObjectName("timelineZoomArrow")
            self.btn_zoom_right.setFixedSize(16, 16)
            self.btn_zoom_right.setAutoRepeat(True)
            self.btn_zoom_right.setAutoRepeatDelay(220)
            self.btn_zoom_right.setAutoRepeatInterval(70)
            self.btn_zoom_right.clicked.connect(lambda: self._nudge_timeline_zoom(1))
            zoom_frame_layout.addWidget(self.btn_zoom_right)
            nav_row.addWidget(zoom_frame, 1)
            self.zoom_value_label = QLabel("0%")
            self.zoom_value_label.setObjectName("timelineControlLabel")
            self.zoom_value_label.setMinimumWidth(58)
            self.zoom_value_label.setAlignment(Qt.AlignCenter)
            self.zoom_value_label.setToolTip("Current timeline zoom (0% = whole clip, 100% = max zoom).")
            nav_row.addWidget(self.zoom_value_label)
            self.btn_reset_panels = QPushButton("Reset Panels")
            self.btn_reset_panels.setToolTip("Restore preview and timeline panel sizes.")
            self.btn_reset_panels.clicked.connect(self.reset_panels)
            nav_row.addWidget(self.btn_reset_panels)
            root.addWidget(nav_frame)

            # PREVIEW: condense the multi-line tip into one compact line.
            self.tip_label = QLabel(
                "Space play/pause • right-click preview play/pause • H pan, Z zoom, Ctrl+/- preview zoom, +/- timeline zoom "
                "• drag CTI horizontally to seek, up/down to zoom timeline • I/O mark, A cut, S split, Del remove, "
                "Ctrl+Shift+I invert, Ctrl+U overlay, Ctrl+Z/Y undo/redo"
            )
            self.tip_label.setObjectName("tip")
            self.tip_label.setWordWrap(True)
            root.addWidget(self.tip_label)

            self.cut_list_label = QLabel("")
            self.cut_list_label.setObjectName("tip")
            self.cut_list_label.setWordWrap(False)
            root.addWidget(self.cut_list_label)

            footer = QHBoxLayout()
            self.status = QLabel("Unified timeline: video preview, crop overlay, cut ranges, audio waveform, speed, and reverse are edited together.")
            self.status.setObjectName("dim")
            footer.addWidget(self.status, 1)
            btn_reset_all = QPushButton(" Reset All")
            btn_reset_all.setToolTip("Reset every edit (crop, cuts, split points, markers, speed, reverse) to defaults.")
            btn_reset_all.clicked.connect(self.reset_all)
            btn_reset_all.setMinimumHeight(40)
            btn_reset_all.setMinimumWidth(120)
            btn_reset_all.setStyleSheet("font-size: 13px; font-weight: 700; padding: 8px 16px;")
            footer.addWidget(btn_reset_all)
            btn_cancel = QPushButton("Cancel (Esc)")
            btn_cancel.setObjectName("danger")
            btn_cancel.clicked.connect(self.cancel)
            btn_cancel.setMinimumHeight(40)
            btn_cancel.setMinimumWidth(150)
            btn_cancel.setStyleSheet("font-size: 14px; font-weight: 700; padding: 8px 20px;")
            footer.addWidget(btn_cancel)
            btn_apply = QPushButton(self._icon("check", QStyle.SP_DialogOkButton), " Confirm (Enter)")
            btn_apply.setObjectName("primary")
            btn_apply.clicked.connect(self.confirm)
            btn_apply.setMinimumHeight(40)
            btn_apply.setMinimumWidth(180)
            btn_apply.setStyleSheet("font-size: 14px; font-weight: 700; padding: 8px 22px;")
            footer.addWidget(btn_apply)
            root.addLayout(footer)
            self._install_shortcuts()
            self._update_tool_buttons()

        def eventFilter(self, obj, event):
            if obj is getattr(self, "btn_hand_tool", None) and event.type() == QtCore.QEvent.MouseButtonDblClick:
                self.reset_view()
                return True
            if obj is getattr(self, "timeline_resize_grip", None):
                if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                    self._timeline_resize_origin_y = float(event.globalPosition().y())
                    self._timeline_resize_sizes = list(self.editor_splitter.sizes())
                    return True
                if event.type() == QtCore.QEvent.MouseMove and getattr(self, "_timeline_resize_sizes", None):
                    dy = float(event.globalPosition().y()) - float(getattr(self, "_timeline_resize_origin_y", 0.0))
                    top, bottom = (list(getattr(self, "_timeline_resize_sizes", [520, 260])) + [260])[:2]
                    self.editor_splitter.setSizes([max(180, int(top - dy)), max(150, int(bottom + dy))])
                    return True
                if event.type() in (QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.Leave):
                    self._timeline_resize_sizes = None
                    return event.type() == QtCore.QEvent.MouseButtonRelease
            if obj is getattr(self, "view_scroll", None) and event.type() == QtCore.QEvent.Resize:
                self._view_handle_width_px = None
                QtCore.QTimer.singleShot(0, self._sync_timeline_view_handle)
            crop_widgets = set(getattr(self, "crop_spinboxes", {}).values())
            crop_widgets.update(
                box.lineEdit()
                for box in getattr(self, "crop_spinboxes", {}).values()
                if box.lineEdit() is not None
            )
            if obj in crop_widgets:
                if event.type() == QtCore.QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    self._apply_crop_fields(commit=True)
                    if hasattr(obj, "clearFocus"):
                        obj.clearFocus()
                    return True
                if event.type() == QtCore.QEvent.FocusOut:
                    QtCore.QTimer.singleShot(0, lambda: self._apply_crop_fields(commit=True))
                if event.type() in (QtCore.QEvent.FocusIn, QtCore.QEvent.MouseButtonPress):
                    line = obj if hasattr(obj, "selectAll") else getattr(obj, "lineEdit", lambda: None)()
                    if line is not None:
                        QtCore.QTimer.singleShot(0, line.selectAll)
            speed_widgets = {
                getattr(self, "speed_combo", None),
                getattr(self, "factor_combo", None),
                getattr(getattr(self, "speed_combo", None), "lineEdit", lambda: None)(),
                getattr(getattr(self, "factor_combo", None), "lineEdit", lambda: None)(),
            }
            if obj in speed_widgets:
                if event.type() == QtCore.QEvent.Wheel:
                    delta = event.angleDelta().y()
                    if delta:
                        step = 0.05 if not (event.modifiers() & Qt.ControlModifier) else 0.25
                        self._set_speed_controls(self._speed() + (step if delta > 0 else -step), commit=True)
                    return True
                if event.type() == QtCore.QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    if obj in {getattr(self, "factor_combo", None), getattr(getattr(self, "factor_combo", None), "lineEdit", lambda: None)()}:
                        self._on_factor_text_changed(self.factor_combo.currentText())
                    else:
                        self._on_speed_text_changed(self.speed_combo.currentText())
                    if hasattr(obj, "clearFocus"):
                        obj.clearFocus()
                    return True
                if event.type() == QtCore.QEvent.FocusOut:
                    if obj in {getattr(self, "factor_combo", None), getattr(getattr(self, "factor_combo", None), "lineEdit", lambda: None)()}:
                        QtCore.QTimer.singleShot(0, lambda: self._on_factor_text_changed(self.factor_combo.currentText()))
                    else:
                        QtCore.QTimer.singleShot(0, lambda: self._on_speed_text_changed(self.speed_combo.currentText()))
                if event.type() in (QtCore.QEvent.FocusIn, QtCore.QEvent.MouseButtonPress):
                    line = obj if hasattr(obj, "selectAll") else getattr(obj, "lineEdit", lambda: None)()
                    if line is not None:
                        QtCore.QTimer.singleShot(0, line.selectAll)
            return super().eventFilter(obj, event)

        def _add_shortcut(self, sequence, slot):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)
            return shortcut

        def _install_shortcuts(self):
            self._shortcuts.clear()
            for sequence, slot in (
                ("Esc", self.cancel),
                ("Return", self._confirm_or_apply_text_editor),
                ("Enter", self._confirm_or_apply_text_editor),
                ("Space", self.toggle_playback),
                ("I", self.mark_in),
                ("O", self.mark_out),
                ("Ctrl+I", self.convert_selected_marker),
                ("Ctrl+O", self.convert_selected_marker),
                ("A", self.add_cut),
                ("S", self.add_separator),
                ("Delete", self.delete_selection),
                ("Ctrl+Z", self._undo),
                ("Ctrl+Y", self._redo),
                ("Ctrl+Shift+Z", self._redo),
                ("M", self.toggle_mute),
                ("Ctrl+R", self.reset_crop),
                ("Ctrl+0", self.reset_view),
                ("Ctrl+Shift+I", self.invert_cuts),
                ("Ctrl+Alt+Left", lambda: self.seek_nearest_cut_edge(-1)),
                ("Ctrl+Alt+Right", lambda: self.seek_nearest_cut_edge(1)),
                ("Home", lambda: self.seek(0.0)),
                ("End", lambda: self.seek(self.duration)),
                ("Left", lambda: self.seek(self.current_time() - 1.0)),
                ("Right", lambda: self.seek(self.current_time() + 1.0)),
                ("Shift+Left", lambda: self.seek(self.current_time() - 5.0)),
                ("Shift+Right", lambda: self.seek(self.current_time() + 5.0)),
                ("H", lambda: self.set_preview_tool("hand")),
                ("Z", lambda: self.set_preview_tool("zoom")),
                ("Ctrl+U", self.toggle_crop_overlay),
                ("+", lambda: self._nudge_timeline_zoom(1)),
                ("=", lambda: self._nudge_timeline_zoom(1)),
                ("-", lambda: self._nudge_timeline_zoom(-1)),
                ("Ctrl++", self.preview_zoom_in),
                ("Ctrl+=", self.preview_zoom_in),
                ("Ctrl+-", self.preview_zoom_out),
            ):
                self._add_shortcut(sequence, slot)

        def _text_input_has_focus(self) -> bool:
            focus = QtWidgets.QApplication.focusWidget()
            if focus is None:
                return False
            text_types = (
                QtWidgets.QLineEdit,
                QtWidgets.QTextEdit,
                QtWidgets.QPlainTextEdit,
                QtWidgets.QAbstractSpinBox,
                QtWidgets.QComboBox,
            )
            return isinstance(focus, text_types)

        def _btn(self, text, slot, icon_name: str | None = None, fallback=None):
            b = QPushButton(self._icon(icon_name, fallback), text) if icon_name or fallback is not None else QPushButton(text)
            b.clicked.connect(slot)
            return b

        def _panel(self, title: str):
            frame = QFrame()
            frame.setObjectName("panel")
            layout = QHBoxLayout(frame)
            layout.setContentsMargins(10, 8, 10, 8)
            layout.setSpacing(8)
            label = QLabel(title)
            label.setObjectName("sectionLabel")
            layout.addWidget(label)
            return frame, layout

        def _update_tool_buttons(self):
            tool = getattr(getattr(self, "preview", None), "_tool", "hand")
            for button, name in (
                (getattr(self, "btn_hand_tool", None), "hand"),
                (getattr(self, "btn_zoom_tool", None), "zoom"),
            ):
                if button is not None:
                    button.setProperty("active", "true" if tool == name else "false")
                    button.style().unpolish(button)
                    button.style().polish(button)

        def set_preview_tool(self, tool: str):
            if hasattr(self, "preview"):
                self.preview.set_tool(tool)
            self._update_tool_buttons()

        def _set_zoom_out_mode(self, enabled: bool):
            if hasattr(self, "preview"):
                self.preview.set_zoom_out_mode(bool(enabled))
            if hasattr(self, "btn_zoom_tool"):
                self.btn_zoom_tool.setIcon(self._icon("zoom_tool_out_orange" if enabled else "zoom_tool_orange", None))

        def toggle_crop_overlay(self):
            if not hasattr(self, "preview"):
                return
            visible = not bool(self.preview._show_crop_overlay)
            self.preview.set_crop_overlay_visible(visible)
            if hasattr(self, "btn_crop_overlay"):
                self.btn_crop_overlay.setText("Hide Crop Overlay (Ctrl+U)" if visible else "Show Crop Overlay (Ctrl+U)")

        def _preview_zoom_focus_source(self):
            return (self.preview.source_w / 2.0, self.preview.source_h / 2.0)

        def _sync_preview_zoom_combo(self, zoom: float | None = None):
            if not hasattr(self, "preview_zoom_combo"):
                return
            value = int(round((self.preview.zoom if zoom is None else float(zoom)) * 100.0))
            self._syncing_preview_zoom_control = True
            try:
                self.preview_zoom_combo.blockSignals(True)
                self.preview_zoom_combo.setCurrentText(f"{value}%")
                self.preview_zoom_combo.blockSignals(False)
            finally:
                self._syncing_preview_zoom_control = False

        def _apply_preview_zoom_text(self):
            if getattr(self, "_syncing_preview_zoom_control", False) or not hasattr(self, "preview_zoom_combo"):
                return
            text = self.preview_zoom_combo.currentText().strip()
            match = re.search(r"\d+(?:[.,]\d+)?", text)
            if not match:
                self._sync_preview_zoom_combo()
                return
            value = max(10.0, min(3200.0, float(match.group(0).replace(",", "."))))
            self.preview._zoom_centered(value / 100.0, self._preview_zoom_focus_source())
            self._sync_preview_zoom_combo()

        def _preview_zoom_editor_has_focus(self):
            if not hasattr(self, "preview_zoom_combo") or self.preview_zoom_combo.lineEdit() is None:
                return False
            focus = QtWidgets.QApplication.focusWidget()
            return (
                focus is self.preview_zoom_combo
                or focus is self.preview_zoom_combo.lineEdit()
                or self.preview_zoom_combo.hasFocus()
                or self.preview_zoom_combo.lineEdit().hasFocus()
            )

        def _confirm_or_apply_text_editor(self):
            if self._preview_zoom_editor_has_focus():
                self._apply_preview_zoom_text()
                self.preview_zoom_combo.clearFocus()
                return
            if self._crop_editor_has_focus():
                self._apply_crop_fields(commit=True)
                focus = QtWidgets.QApplication.focusWidget()
                if focus is not None:
                    focus.clearFocus()
                return
            if self._speed_editor_has_focus():
                self._apply_active_speed_editor()
                focus = QtWidgets.QApplication.focusWidget()
                if focus is not None:
                    focus.clearFocus()
                return
            self.confirm()

        def _crop_editor_has_focus(self):
            focus = QtWidgets.QApplication.focusWidget()
            if focus is None:
                return False
            for box in getattr(self, "crop_spinboxes", {}).values():
                if focus is box or focus is box.lineEdit() or box.hasFocus() or box.lineEdit().hasFocus():
                    return True
            return False

        def _validated_crop_field_margins(self) -> list[int]:
            boxes = getattr(self, "crop_spinboxes", {})
            top = int(boxes.get("top").value()) if boxes.get("top") is not None else 0
            left = int(boxes.get("left").value()) if boxes.get("left") is not None else 0
            right = int(boxes.get("right").value()) if boxes.get("right") is not None else 0
            bottom = int(boxes.get("bottom").value()) if boxes.get("bottom") is not None else 0
            return self.preview._clamped_margins([top, left, right, bottom])

        def _set_crop_field_ranges(self, margins: list[int]) -> None:
            boxes = getattr(self, "crop_spinboxes", {})
            if not boxes:
                return
            top, left, right, bottom = [int(v) for v in margins]
            min_size = 16
            ranges = {
                "top": max(0, self.source_h - bottom - min_size),
                "bottom": max(0, self.source_h - top - min_size),
                "left": max(0, self.source_w - right - min_size),
                "right": max(0, self.source_w - left - min_size),
            }
            for key, maximum in ranges.items():
                boxes[key].setRange(0, int(maximum))

        def _sync_crop_fields(self) -> None:
            boxes = getattr(self, "crop_spinboxes", {})
            if not boxes or not hasattr(self, "preview"):
                return
            margins = [int(v) for v in self.preview.margins]
            self._syncing_crop_controls = True
            try:
                for box in boxes.values():
                    box.blockSignals(True)
                self._set_crop_field_ranges(margins)
                for key, value in zip(("top", "left", "right", "bottom"), margins):
                    boxes[key].setValue(int(value))
            finally:
                for box in boxes.values():
                    box.blockSignals(False)
                self._syncing_crop_controls = False

        def _apply_crop_fields(self, commit: bool = False) -> None:
            if getattr(self, "_syncing_crop_controls", False) or not hasattr(self, "preview"):
                return
            margins = self._validated_crop_field_margins()
            if commit:
                # Snap typed values to even origin/size so the editor never
                # commits odd crop dimensions.
                margins = self._normalize_crop_margins_even(margins)
            changed = margins != [int(v) for v in self.preview.margins]
            if changed:
                self.preview.set_margins(margins)
            else:
                self._sync_crop_fields()
            if commit and changed:
                self._commit_history()
            self._refresh_all()

        def _on_crop_field_changed(self, _key: str) -> None:
            if getattr(self, "_syncing_crop_controls", False):
                return
            self._apply_crop_fields(commit=False)

        def _on_crop_field_edit_finished(self) -> None:
            if getattr(self, "_syncing_crop_controls", False):
                return
            self._apply_crop_fields(commit=True)

        def _speed_editor_has_focus(self):
            focus = QtWidgets.QApplication.focusWidget()
            widgets = {
                getattr(self, "speed_combo", None),
                getattr(self, "factor_combo", None),
                getattr(getattr(self, "speed_combo", None), "lineEdit", lambda: None)(),
                getattr(getattr(self, "factor_combo", None), "lineEdit", lambda: None)(),
            }
            return focus in widgets

        def _apply_active_speed_editor(self):
            focus = QtWidgets.QApplication.focusWidget()
            if focus in {getattr(self, "factor_combo", None), getattr(getattr(self, "factor_combo", None), "lineEdit", lambda: None)()}:
                self._on_factor_text_changed(self.factor_combo.currentText())
            else:
                self._on_speed_text_changed(self.speed_combo.currentText())

        def preview_zoom_in(self):
            if hasattr(self, "preview"):
                self.preview._zoom_centered(self.preview.zoom * 1.25)

        def preview_zoom_out(self):
            if hasattr(self, "preview"):
                self.preview._zoom_centered(self.preview.zoom / 1.25)

        def _attach_next_panel(self):
            """Stream the side-column panels into the (visible) scroll area one per
            event-loop tick. Laying them out all at once blocks ~3.5s here; doing it
            after the first paint, incrementally, keeps the editor responsive."""
            if getattr(self, "_closing", False):
                return
            panels = getattr(self, "_deferred_panels", None)
            if not panels:
                return
            panel = panels.pop(0)
            # Insert above the trailing stretch so the visual order is preserved.
            self._controls_v.insertWidget(max(0, self._controls_v.count() - 1), panel)
            if panels:
                QtCore.QTimer.singleShot(0, self._attach_next_panel)

        def _setup_player(self):
            # Lazily load the multimedia backend the first time the player is
            # created (deferred out of the synchronous window-build path). The
            # cold native-DLL load is timed so its real cost is visible in the log.
            nonlocal QtMultimedia
            if QtMultimedia is None:
                _mm_start = time.perf_counter()
                QtMultimedia = _import_qt_multimedia()
                _gui_log_debug(
                    f"QtMultimedia backend loaded in {time.perf_counter() - _mm_start:.3f}s",
                    force=True,
                )
            self.player = QtMultimedia.QMediaPlayer(self)
            self.audio = QtMultimedia.QAudioOutput(self)
            self.audio.setVolume(self.volume_slider.value() / 100.0)
            self.player.setAudioOutput(self.audio)
            self.video_sink = QtMultimedia.QVideoSink(self)
            self.player.setVideoSink(self.video_sink)
            self.video_sink.videoFrameChanged.connect(self._on_video_frame)
            self.player.positionChanged.connect(self._on_position_changed)
            self.player.mediaStatusChanged.connect(self._on_media_status_changed)
            self.player.playbackStateChanged.connect(self._on_playback_state_changed)
            self.player.setSource(QtCore.QUrl.fromLocalFile(str(self._segment_path(0))))
            self.player.pause()
            self._update_volume_icon()
            # --- live reverse preview state (renders a small reversed proxy window) ---
            self._rev_active = False        # currently playing a reversed proxy
            self._rev_proc = None           # QProcess rendering the proxy
            self._rev_gen = 0               # generation guard for async renders
            self._rev_win_start = 0.0       # source-time window the proxy covers
            self._rev_win_end = 0.0
            self._rev_speed = 1.0           # speed baked into the current proxy
            self._rev_proxy_path = None
            self._rev_play_base = 0.0       # CTI position where reverse playback began
            self._rev_elapsed_base = 0.0    # source secs reversed in completed windows
            self._rev_src_shown = 0.0       # source time of the frame currently displayed
            self._closing = False

        def _segment_path(self, index: int) -> Path:
            if self.join_segments:
                index = max(0, min(len(self.join_segments) - 1, int(index)))
                return Path(self.join_segments[index]["path"])
            return self.input_path

        def _segment_for_time(self, seconds: float) -> tuple[int, float]:
            seconds = max(0.0, min(self.duration, float(seconds)))
            if not self.join_segments:
                return 0, seconds
            if seconds >= self.duration:
                last = self.join_segments[-1]
                return len(self.join_segments) - 1, float(last["duration"])
            for idx, segment in enumerate(self.join_segments):
                start = float(segment["start"])
                end = float(segment["end"])
                if start <= seconds < end:
                    return idx, max(0.0, min(float(segment["duration"]), seconds - start))
            return len(self.join_segments) - 1, float(self.join_segments[-1]["duration"])

        def _segment_start(self, index: int) -> float:
            if not self.join_segments:
                return 0.0
            return float(self.join_segments[max(0, min(len(self.join_segments) - 1, int(index)))]["start"])

        def _apply_pending_segment_seek(self):
            if self.player is None or self._pending_segment_position_ms is None:
                return
            ms = int(self._pending_segment_position_ms)
            # Only apply once the INTENDED source is really loaded: it must be seekable
            # AND long enough for the target. This guards against seeking a still-loaded
            # short reverse proxy (which would clamp to its tiny duration), and against
            # setPosition being dropped before the new media is ready.
            dur_ms = self.player.duration()
            if (not self.player.isSeekable()) or dur_ms < ms + 200:
                tries = getattr(self, "_pending_seek_tries", 0)
                if tries < 60:
                    self._pending_seek_tries = tries + 1
                    QtCore.QTimer.singleShot(60, self._apply_pending_segment_seek)
                    return
            self._pending_seek_tries = 0
            play_after = bool(self._pending_segment_play)
            self._pending_segment_position_ms = None
            self.player.setPosition(ms)
            if play_after:
                self.player.play()
            else:
                self.player.pause()
            self._switching_segment = False

        def _switch_to_segment(self, index: int, local_seconds: float, play_after: bool):
            if self.player is None:
                return
            index = max(0, min(len(self.join_segments) - 1 if self.join_segments else 0, int(index)))
            self._active_segment_index = index
            self._pending_segment_position_ms = int(round(max(0.0, local_seconds) * 1000.0))
            self._pending_segment_play = bool(play_after)
            self._switching_segment = True
            self.player.setSource(QtCore.QUrl.fromLocalFile(str(self._segment_path(index))))
            QtCore.QTimer.singleShot(0, self._apply_pending_segment_seek)
            QtCore.QTimer.singleShot(90, self._apply_pending_segment_seek)

        def _on_video_frame(self, frame):
            try:
                image = frame.toImage()
            except Exception:
                image = None
            if image is not None and not image.isNull():
                self.preview.set_image(image)

        def _src_time(self, t):
            """Map a timeline (CTI) time to the SOURCE time shown. Reverse flips the
            WHOLE clip around its midpoint, so timeline t shows source[duration - t] —
            one consistent mirror for the entire timeline (works from any CTI)."""
            t = max(0.0, min(self.duration, float(t)))
            if hasattr(self, "reverse_box") and self.reverse_box.isChecked():
                return max(0.0, min(self.duration, self.duration - t))
            return t

        def _request_preview_frame(self, seconds: float) -> None:
            if self.player is not None and self.player.playbackState() == QtMultimedia.QMediaPlayer.PlayingState:
                return
            segment_index, local_seconds = self._segment_for_time(self._src_time(seconds))
            source = self._segment_path(segment_index)
            target_w = max(320, self.preview.width())
            target_h = max(180, self.preview.height())
            self._pending_frame_request = (source, max(0.0, local_seconds), target_w, target_h)
            worker = getattr(self, "_frame_worker", None)
            if worker is not None and worker.isRunning():
                return
            self._launch_preview_frame_worker()

        def _launch_preview_frame_worker(self) -> None:
            if not self._pending_frame_request:
                return
            source, timestamp, width, height = self._pending_frame_request
            self._pending_frame_request = None
            self._frame_request_started = time.perf_counter()
            self._frame_worker = FrameExtractWorker(
                self.request.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg",
                source,
                timestamp,
                width,
                height,
                parent=self,
            )
            self._frame_worker.finished_with_image.connect(self._on_preview_frame_extracted)
            self._frame_worker.finished.connect(self._frame_worker.deleteLater)
            self._frame_worker.start()

        def _on_preview_frame_extracted(self, image):
            if image is not None and not image.isNull():
                self.preview.set_image(image)
            self._frame_worker = None
            if self._pending_frame_request:
                self._launch_preview_frame_worker()

        def _stop_preview_frame_worker(self) -> None:
            self._pending_frame_request = None
            worker = getattr(self, "_frame_worker", None)
            self._frame_worker = None
            if worker is not None and worker.isRunning():
                worker.stop()
                if not worker.wait(1500):
                    worker.terminate()
                    worker.wait(800)

        def current_time(self):
            if hasattr(self, "timeline"):
                return max(0.0, min(self.duration, float(getattr(self.timeline, "playhead", 0.0))))
            try:
                if self.player is not None:
                    local_seconds = self.player.position() / 1000.0
                    return max(0.0, min(self.duration, self._segment_start(self._active_segment_index) + local_seconds))
            except Exception:
                pass
            return max(0.0, min(self.duration, float(getattr(self.timeline, "playhead", 0.0))))

        def seek(self, seconds):
            seconds = max(0.0, min(self.duration, float(seconds)))
            # PREVIEW: update the timecode + playhead FIRST so the readout tracks
            # the CTI live, before the heavier player seek / frame extraction.
            self.timeline.set_playhead(seconds, follow=True)
            self._set_time_display(seconds)
            dragging = getattr(getattr(self, "timeline", None), "_dragging", False)
            if self.reverse_box.isChecked() and getattr(self, "player", None) is not None:
                # In reverse mode the player holds a proxy — never setPosition on it.
                # Re-anchor the reverse window to the seeked time instead.
                was_reverse_playing = (
                    self._rev_active
                    and self.player.playbackState() == QtMultimedia.QMediaPlayer.PlayingState
                )
                self._rev_active = False
                if dragging:
                    return
                if was_reverse_playing:
                    self._start_reverse_playback(from_seconds=seconds)
                else:
                    # The mirror is global/fixed — just show the frame at this point
                    # (source[duration - seconds]); don't re-mirror the timeline.
                    self._request_preview_frame(seconds)
                return
            if getattr(self, "player", None) is not None:
                segment_index, local_seconds = self._segment_for_time(seconds)
                playing = self.player.playbackState() == QtMultimedia.QMediaPlayer.PlayingState
                if self.join_segments and segment_index != self._active_segment_index:
                    self._switch_to_segment(segment_index, local_seconds, playing)
                else:
                    self.player.setPosition(int(round(local_seconds * 1000)))
                # Skip per-tick frame extraction while actively dragging the CTI so
                # dragging stays snappy; the final frame loads on mouse release.
                if not playing and not dragging:
                    self._request_preview_frame(seconds)

        def _set_time_display(self, seconds: float) -> None:
            seconds = max(0.0, min(self.duration, float(seconds)))
            if hasattr(self, "cti_time_label"):
                self.cti_time_label.setText(seconds_to_timecode(seconds))
            if hasattr(self, "time_label"):
                self.time_label.setText(f"{seconds_to_hmsf(seconds, self.fps)} / {seconds_to_hmsf(self.duration, self.fps)}")

        def seek_nearest_cut_edge(self, direction: int):
            edges = sorted(
                {round(float(value), 6) for cut in self._cut_ranges for value in cut}
            )
            if not edges:
                self.status.setText("No cut edges are available.")
                return
            current = self.current_time()
            if direction < 0:
                candidates = [value for value in edges if value < current - 1e-4]
                target = candidates[-1] if candidates else edges[-1]
            else:
                candidates = [value for value in edges if value > current + 1e-4]
                target = candidates[0] if candidates else edges[0]
            self.seek(target)

        def toggle_playback(self):
            if self.player is None:
                return
            playing = self.player.playbackState() == QtMultimedia.QMediaPlayer.PlayingState
            if self.reverse_box.isChecked():
                if playing:
                    self.player.pause()
                elif self._rev_active:
                    self.player.play()                 # resume the current reversed proxy
                else:
                    self._start_reverse_playback()      # render + play a reversed window
                return
            if playing:
                self.player.pause()
            elif getattr(self, "_pending_segment_position_ms", None) is not None:
                # A restore/segment seek is still loading — play once it lands so we
                # don't start from 0 and jump.
                self._pending_segment_play = True
            else:
                self.player.play()

        def _on_playback_state_changed(self, _state):
            if self.player.playbackState() == QtMultimedia.QMediaPlayer.PlayingState:
                self.btn_play.setText(" Pause (Space)")
                self.btn_play.setIcon(self._icon("pause", QStyle.SP_MediaPause))
            else:
                self.btn_play.setText(" Play (Space)")
                self.btn_play.setIcon(self._icon("play", QStyle.SP_MediaPlay))

        # ---- live reverse preview (preview-only; export uses the real reverse filter) ----
        REV_WINDOW = 15.0     # seconds of source reversed per proxy chunk

        def _rev_target_width(self):
            try:
                w = int(self.preview.width()) if self.preview is not None else 0
            except Exception:
                w = 0
            # Down-scale before the reverse filter so its frame buffer stays small/fast
            # (the filter buffers the whole window — keep it modest for 15s chunks).
            return max(320, min(960, (w or 854)))

        def _start_reverse_playback(self, from_seconds=None):
            """Play the reversed clip forward from the playhead. The CTI (clip time)
            moves FORWARD; the SOURCE frame shown = duration - CTI. So we render a
            reversed proxy of the source window [dur-(p+W), dur-p] and play it."""
            if self.player is None:
                return
            cti = self.current_time() if from_seconds is None else max(0.0, min(self.duration, float(from_seconds)))
            # CTI moves FORWARD from here; the source content runs backward, mirrored
            # around the fixed anchor (set when reverse was toggled on).
            self._rev_play_base = cti
            self._rev_elapsed_base = 0.0
            win_end = self._src_time(cti)                    # source content at the CTI (= dur - cti)
            win_start = max(0.0, win_end - self.REV_WINDOW)
            self._rev_src_shown = win_end
            if hasattr(self, "timeline"):
                self.timeline.set_reverse_view(True)
            if win_end <= 0.05:
                self.status.setText("At the start of the source already — nothing to reverse from here.")
                return
            self._render_reverse_proxy(win_start, win_end)

        def _render_reverse_proxy(self, win_start, win_end):
            win_start = max(0.0, float(win_start))
            win_end = max(win_start + 0.05, float(win_end))
            chunk = win_end - win_start
            speed = self._speed()
            self._rev_gen += 1
            gen = self._rev_gen
            out = Path(self._wave_temp.name) / f"rev_proxy_{gen}.mp4"
            if self.join_segments:
                seg_index, _ = self._segment_for_time(max(0.0, win_end - 1e-3))
                src = self._segment_path(seg_index)
                ss = max(0.0, win_start - self._segment_start(seg_index))
            else:
                src = self.input_path
                ss = win_start
            tw = self._rev_target_width()
            vf = f"scale={tw}:-2,reverse,setpts=(PTS-STARTPTS)/{_ffmpeg_float(speed)}"
            want_audio = bool(self.request.get("has_audio"))
            args = ["-hide_banner", "-loglevel", "error", "-y"]
            if ss > 0:
                # ACCURATE seek (no -noaccurate_seek): video & audio both start exactly
                # at ss. With keyframe seeking the video would start at an earlier
                # keyframe while audio started at ss -> a per-chunk A/V offset that made
                # the sound drift out of sync after the first chunk.
                args += ["-ss", _ffmpeg_float(ss)]
            args += [
                "-t", _ffmpeg_float(chunk),
                "-i", str(src),
                "-map", "0:v:0", "-filter:v", vf,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32", "-pix_fmt", "yuv420p",
            ]
            if want_audio:
                # Reverse the SAME window's audio and rebuild clean monotonic PTS from
                # the sample index (asetpts=N/SR/TB) so it stays locked to the video —
                # no resampler 'async' drift, which was nudging later chunks out of sync.
                af = f"areverse,{_gui_atempo_chain(speed)},asetpts=N/SR/TB"
                args += ["-map", "0:a:0?", "-filter:a", af, "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
            else:
                args += ["-an"]
            args += [
                "-sn", "-dn",
                "-avoid_negative_ts", "make_zero",
                "-shortest",                       # trim to the shorter stream -> exact A/V length
                "-movflags", "+faststart",
                str(out),
            ]
            if self._rev_proc is not None:
                try:
                    self._rev_proc.finished.disconnect()
                except Exception:
                    pass
                try:
                    self._rev_proc.kill()
                except Exception:
                    pass
            self.status.setText(
                f"Rendering reverse preview {seconds_to_timecode(win_start)} → {seconds_to_timecode(win_end)} …"
            )
            self._rev_proc = QtCore.QProcess(self)
            self._rev_proc.finished.connect(
                lambda *_a, p=out, g=gen, ws=win_start, we=win_end, sp=speed:
                self._rev_proxy_ready(p, g, ws, we, sp)
            )
            self._rev_proc.start(
                str(self.request.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"), args
            )

        def _rev_proxy_ready(self, path, gen, win_start, win_end, speed):
            if getattr(self, "_closing", False):
                return
            if gen != self._rev_gen or not self.reverse_box.isChecked():
                return
            try:
                ok = Path(path).exists() and Path(path).stat().st_size > 0
            except Exception:
                ok = False
            if not ok:
                self.status.setText("Reverse preview couldn't render here (the exported file is still reversed).")
                return
            self._rev_active = True
            self._rev_proxy_path = Path(path)
            self._rev_win_start = float(win_start)
            self._rev_win_end = float(win_end)
            self._rev_speed = max(0.05, float(speed))
            # Pin the CTI to where reverse is up to (no flash to 0 before playback ticks).
            # Must use the SAME base as _on_position_changed (_rev_play_base) so the view
            # doesn't jump to the start of the timeline at each chunk hand-off.
            cti0 = max(0.0, min(self.duration, self._rev_play_base + self._rev_elapsed_base))
            self.timeline.set_playhead(cti0, follow=True)
            self._set_time_display(cti0)
            self.player.setSource(QtCore.QUrl.fromLocalFile(str(path)))
            self.player.setPlaybackRate(1.0)        # the speed is baked into the proxy
            self.player.play()
            self.status.setText(
                f"◀ Reverse preview  {seconds_to_timecode(win_start)} – {seconds_to_timecode(win_end)}"
            )

        def _exit_reverse_mode(self, resume=False):
            """Leave reversed playback (reverse just turned OFF). The CTI stays where it
            is; reverse is off now, so the frame there is the normal source[cti]."""
            self._rev_active = False
            if self._rev_proc is not None:
                try:
                    self._rev_proc.finished.disconnect()
                except Exception:
                    pass
                try:
                    if self._rev_proc.state() != QtCore.QProcess.NotRunning:
                        self._rev_proc.kill()
                except Exception:
                    pass
                self._rev_proc = None
            if self.player is None:
                return
            # Keep the frame on screen: go to the source position that was showing.
            t = max(0.0, min(self.duration, float(getattr(self, "_rev_src_shown", self.current_time()))))
            self.timeline.set_playhead(t, follow=True)
            self._set_time_display(t)
            seg_index, local = self._segment_for_time(t)
            self._active_segment_index = seg_index
            self._pending_segment_position_ms = int(round(max(0.0, local) * 1000.0))
            self._pending_segment_play = bool(resume)
            self._switching_segment = True
            self.player.setSource(QtCore.QUrl.fromLocalFile(str(self._segment_path(seg_index))))
            QtCore.QTimer.singleShot(0, self._apply_pending_segment_seek)
            QtCore.QTimer.singleShot(90, self._apply_pending_segment_seek)
            if not resume:
                self._request_preview_frame(t)

        def _on_reverse_toggled(self, _state=None):
            self._on_transform_changed()
            on = self.reverse_box.isChecked()
            if hasattr(self, "timeline"):
                self.timeline.set_reverse_view(on)   # whole-clip mirror (global, consistent)
            if self.player is None:
                return
            if on and self._rev_active:
                return
            if not on and self._rev_active:
                self._exit_reverse_mode(resume=False)
                return
            # The CTI moves to the MIRROR of where it was, so the SAME frame stays on
            # screen (timeline t <-> source[dur - t]); only the cursor relocates.
            mirror_cti = max(0.0, min(self.duration, self.duration - self.current_time()))
            if on and self.player.playbackState() == QtMultimedia.QMediaPlayer.PlayingState:
                self._start_reverse_playback(from_seconds=mirror_cti)
                return
            self.timeline.set_playhead(mirror_cti, follow=True)
            self._set_time_display(mirror_cti)
            self._request_preview_frame(mirror_cti)
            if on:
                self.status.setText("Reverse on — whole clip flipped (same frame kept). Press Play to preview backward.")

        def toggle_mute(self):
            if self.audio is None:
                return
            self.audio.setMuted(not self.audio.isMuted())
            self._update_volume_icon()

        def _on_volume_changed(self, value):
            if self.audio is not None:
                self.audio.setVolume(max(0, min(100, value)) / 100.0)
                if value > 0 and self.audio.isMuted():
                    self.audio.setMuted(False)
            self.volume_label.setText(f"{int(value)}%")
            self._update_volume_icon()

        def _update_volume_icon(self):
            if not hasattr(self, "btn_mute"):
                return
            muted = bool(self.audio is not None and self.audio.isMuted())
            value = int(self.volume_slider.value()) if hasattr(self, "volume_slider") else 60
            if muted or value <= 0:
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
            self.btn_mute.setText(" Mute (M)" if not muted and value > 0 else " Unmute (M)")

        def _on_position_changed(self, ms):
            if getattr(self, "_switching_segment", False):
                return
            if getattr(self, "_rev_active", False):
                # CTI moves FORWARD from the play start; the source content runs BACKWARD
                # from win_end (mirror is around the fixed anchor).
                reversed_in_win = (ms / 1000.0) * self._rev_speed
                self._rev_src_shown = max(0.0, self._rev_win_end - reversed_in_win)
                cti = self._rev_play_base + self._rev_elapsed_base + reversed_in_win
                cti = max(0.0, min(self.duration, cti))
                if not getattr(getattr(self, "timeline", None), "_dragging", False):
                    self.timeline.set_playhead(cti, follow=True)
                    self._set_time_display(cti)
                return
            if getattr(getattr(self, "timeline", None), "_dragging", False):
                return
            seconds = max(0.0, min(self.duration, self._segment_start(self._active_segment_index) + ms / 1000.0))
            self.timeline.set_playhead(seconds, follow=True)
            self._set_time_display(seconds)

        def _on_media_status_changed(self, status):
            # Apply a queued seek once the new source is actually loaded — setPosition
            # before load is dropped by some backends (large files load slowly).
            if status in (QtMultimedia.QMediaPlayer.MediaStatus.LoadedMedia,
                          QtMultimedia.QMediaPlayer.MediaStatus.BufferedMedia) \
                    and getattr(self, "_pending_segment_position_ms", None) is not None:
                self._apply_pending_segment_seek()
            if status == QtMultimedia.QMediaPlayer.MediaStatus.EndOfMedia and getattr(self, "_rev_active", False):
                # Finished reversing this window — keep the CTI moving forward and
                # chain the previous window (further back in the source), or stop.
                self._rev_elapsed_base += max(0.0, self._rev_win_end - self._rev_win_start)
                next_end = self._rev_win_start
                cti_now = self._rev_play_base + self._rev_elapsed_base
                if (self.reverse_box.isChecked() and next_end > 0.05
                        and cti_now < self.duration - 0.05):
                    self._render_reverse_proxy(max(0.0, next_end - self.REV_WINDOW), next_end)
                else:
                    # Reached the start of the source (or the timeline end): stop, but
                    # stay in reverse mode (mirror on). Show the current still frame.
                    self._rev_active = False
                    try:
                        self.player.pause()
                    except Exception:
                        pass
                    self._request_preview_frame(self.current_time())
                    self.status.setText("Reverse preview reached the start of the clip.")
                return
            if (
                status == QtMultimedia.QMediaPlayer.MediaStatus.EndOfMedia
                and self.join_segments
                and self._active_segment_index + 1 < len(self.join_segments)
            ):
                next_index = self._active_segment_index + 1
                self._switch_to_segment(next_index, 0.0, True)
                self.timeline.set_playhead(self._segment_start(next_index), follow=True)
                self._set_time_display(self._segment_start(next_index))

        def _speed(self):
            text = self.speed_combo.currentText().strip().lower()
            try:
                if text.endswith("%"):
                    return max(0.05, min(10.0, float(text[:-1]) / 100.0))
                if text.endswith("x"):
                    return max(0.05, min(10.0, float(text[:-1])))
                return max(0.05, min(10.0, float(text) / 100.0))
            except Exception:
                return 1.0

        def _factor(self):
            text = self.factor_combo.currentText().strip().lower()
            try:
                if text.endswith("x"):
                    return max(0.05, min(10.0, float(text[:-1])))
                if text.endswith("%"):
                    return max(0.05, min(10.0, float(text[:-1]) / 100.0))
                return max(0.05, min(10.0, float(text)))
            except Exception:
                return 1.0

        def _set_speed_controls(self, speed: float, commit: bool = False):
            speed = max(0.05, min(10.0, float(speed)))
            self._syncing_speed_controls = True
            try:
                self.speed_combo.setCurrentText(f"{speed * 100:g}%")
                self.factor_combo.setCurrentText(f"{speed:g}x")
                self.speed_slider.setValue(max(5, min(1000, int(round(speed * 100.0)))))
            finally:
                self._syncing_speed_controls = False
            self._on_transform_changed(commit=commit)

        def _on_speed_text_changed(self, _text):
            if getattr(self, "_syncing_speed_controls", False):
                return
            speed = self._speed()
            self._syncing_speed_controls = True
            self.factor_combo.setCurrentText(f"{speed:g}x")
            self.speed_slider.setValue(max(5, min(1000, int(round(speed * 100.0)))))
            self._syncing_speed_controls = False
            self._on_transform_changed(commit=True)

        def _on_factor_text_changed(self, _text):
            if getattr(self, "_syncing_speed_controls", False):
                return
            speed = self._factor()
            self._syncing_speed_controls = True
            self.speed_combo.setCurrentText(f"{speed * 100:g}%")
            self.speed_slider.setValue(max(5, min(1000, int(round(speed * 100.0)))))
            self._syncing_speed_controls = False
            self._on_transform_changed(commit=True)

        def _on_speed_slider_changed(self, value):
            if getattr(self, "_syncing_speed_controls", False):
                return
            speed = max(0.05, min(10.0, float(value) / 100.0))
            self._syncing_speed_controls = True
            self.speed_combo.setCurrentText(f"{speed * 100:g}%")
            self.factor_combo.setCurrentText(f"{speed:g}x")
            self._syncing_speed_controls = False
            self._on_transform_changed(commit=False)

        def _on_transform_changed(self, commit: bool = True):
            self._apply_speed_to_player()
            if commit:
                self._commit_history()
            self._refresh_all()

        def _apply_speed_to_player(self):
            try:
                self.player.setPlaybackRate(self._speed())
            except Exception as exc:
                _gui_log_debug(f"Could not apply preview playback rate: {exc}", force=True)

        @staticmethod
        def _snap_axis_even(near: int, far: int, source_dim: int) -> tuple[int, int]:
            return _snap_crop_axis_even(near, far, source_dim)

        def _normalize_crop_margins_even(self, margins) -> list[int]:
            return snap_crop_margins_even(margins, int(self.source_w), int(self.source_h))

        def _snap_crop_to_even(self) -> bool:
            # Snap the current preview crop to even origin/size. Returns True when
            # the margins changed.
            if not hasattr(self, "preview"):
                return False
            current = [int(v) for v in self.preview.margins]
            if not any(current):
                return False
            snapped = self._normalize_crop_margins_even(current)
            if snapped != current:
                self.preview.set_margins(snapped)
                self._sync_crop_fields()
                return True
            return False

        def _on_crop_changed(self):
            self._sync_crop_fields()
            self._refresh_all()

        def _on_crop_edit_finished(self):
            self._snap_crop_to_even()
            self._commit_history()
            self._refresh_all()

        def _on_marker_moved(self, name, value):
            if name == "in":
                self._mark_in = max(0.0, min(self.duration, float(value)))
                self.status.setText(f"Mark In: {seconds_to_timecode(self._mark_in)}")
                self.cut_list_label.setText(f"Mark In: {seconds_to_timecode(self._mark_in)}")
            elif name == "out":
                self._mark_out = max(0.0, min(self.duration, float(value)))
                self.status.setText(f"Mark Out: {seconds_to_timecode(self._mark_out)}")
                self.cut_list_label.setText(f"Mark Out: {seconds_to_timecode(self._mark_out)}")

        def _on_separator_moved(self, idx, value):
            if 0 <= int(idx) < len(self._separator_points):
                self._separator_points[int(idx)] = max(1e-6, min(self.duration - 1e-6, float(value)))
                current = self._separator_points[int(idx)]
                self.status.setText(f"Split #{int(idx) + 1}: {seconds_to_timecode(current)}")
                self.cut_list_label.setText(f"Split #{int(idx) + 1}: {seconds_to_timecode(current)}")

        def _on_timeline_edit_finished(self):
            self._cut_ranges = normalize_ranges(getattr(self.timeline, "cut_ranges", self._cut_ranges), self.duration)
            self._separator_points = sorted(set(round(float(value), 6) for value in self._separator_points))
            self._commit_history()
            self._refresh_all()

        def mark_in(self):
            current = self.current_time()
            tolerance = max(0.001, 0.5 / max(1.0, self.fps))
            if self._mark_out is not None and abs(float(self._mark_out) - current) <= tolerance:
                self._mark_out = None
            self._mark_in = current
            self.timeline.selected_marker = "in"
            self._commit_history()
            self._refresh_all()

        def mark_out(self):
            current = self.current_time()
            tolerance = max(0.001, 0.5 / max(1.0, self.fps))
            if self._mark_in is not None and abs(float(self._mark_in) - current) <= tolerance:
                self._mark_in = None
            self._mark_out = current
            self.timeline.selected_marker = "out"
            self._commit_history()
            self._refresh_all()

        def convert_in_to_out(self):
            if self._mark_in is None:
                self.status.setText("No Mark In marker exists to convert.")
                return
            self._mark_out = float(self._mark_in)
            self._mark_in = None
            self.timeline.selected_marker = "out"
            self._commit_history()
            self._refresh_all()

        def convert_out_to_in(self):
            if self._mark_out is None:
                self.status.setText("No Mark Out marker exists to convert.")
                return
            self._mark_in = float(self._mark_out)
            self._mark_out = None
            self.timeline.selected_marker = "in"
            self._commit_history()
            self._refresh_all()

        def convert_selected_marker(self):
            if self.timeline.selected_marker == "in":
                self.convert_in_to_out()
            elif self.timeline.selected_marker == "out":
                self.convert_out_to_in()
            else:
                self.status.setText("Select Mark In or Mark Out before converting.")

        def add_cut(self):
            if self._mark_in is None:
                self._mark_in = self.current_time()
                self._commit_history()
                self._refresh_all()
                return
            if self._mark_out is None:
                self._mark_out = self.current_time()
            s, e = sorted((float(self._mark_in), float(self._mark_out)))
            if e <= s:
                self.status.setText("Set different Mark In and Mark Out times before adding a cut.")
                return
            self._cut_ranges = normalize_ranges(self._cut_ranges + [(s, e)], self.duration)
            self.timeline.selected_cut = min(
                range(len(self._cut_ranges)),
                key=lambda idx: abs(self._cut_ranges[idx][0] - s) + abs(self._cut_ranges[idx][1] - e),
                default=-1,
            )
            self.timeline.selected_separator = -1
            self._mark_in = None
            self._mark_out = None
            self._commit_history()
            self._refresh_all()

        def add_separator(self):
            point = self.current_time()
            if not (1e-6 < point < self.duration - 1e-6):
                self.status.setText("Move the CTI inside the clip before adding a Split point.")
                return
            points = sorted(set(round(v, 6) for v in self._separator_points + [point]))
            self._separator_points = points
            self.timeline.selected_cut = -1
            self.timeline.selected_separator = points.index(round(point, 6))
            self._commit_history()
            self._refresh_all()

        def delete_selected_separator(self):
            idx = self.timeline.selected_separator
            if 0 <= idx < len(self._separator_points):
                self._separator_points.pop(idx)
                self.timeline.selected_separator = -1
                self._commit_history()
                self._refresh_all()

        def delete_selection(self):
            if self.timeline.selected_marker in {"in", "out"}:
                self.delete_selected_markers()
                return
            if self.timeline.selected_separator >= 0:
                self.delete_selected_separator()
                return
            self.delete_selected_cut()

        def delete_selected_markers(self):
            marker = self.timeline.selected_marker
            if marker == "in":
                self._mark_in = None
            elif marker == "out":
                self._mark_out = None
            else:
                return
            self.timeline.selected_marker = None
            self._commit_history()
            self._refresh_all()

        def delete_selected_cut(self):
            idx = self.timeline.selected_cut
            if 0 <= idx < len(self._cut_ranges):
                self._cut_ranges.pop(idx)
                self.timeline.selected_cut = -1
                self._commit_history()
                self._refresh_all()

        def delete_all_cuts(self):
            if not self._cut_ranges:
                return
            self._cut_ranges = []
            self.timeline.selected_cut = -1
            self._mark_in = None
            self._mark_out = None
            self._commit_history()
            self._refresh_all()

        def invert_cuts(self):
            if not self._cut_ranges:
                self.status.setText("No cut ranges exist to invert.")
                return
            self._cut_ranges = invert_cut_ranges(self._cut_ranges, self.duration)
            self.timeline.selected_cut = -1
            self._commit_history()
            self._refresh_all()

        def reset_crop(self):
            self.preview.reset_crop()
            self._commit_history()
            self._refresh_all()

        def reset_all(self):
            # Return every edit made in this editor to its zero/default state:
            # crop, cuts, split points, in/out markers, speed, and reverse.
            default = UnifiedSnapshot(
                margins=(0, 0, 0, 0),
                cut_ranges=(),
                separators=(),
                mark_in=None,
                mark_out=None,
                speed=1.0,
                reverse=False,
                include_audio=bool(self.request.get("has_audio")),
            )
            self.timeline.selected_cut = -1
            self.timeline.selected_separator = -1
            self.timeline.selected_marker = None
            self._restore_snapshot(default)
            self._commit_history()
            self._refresh_all()
            self.status.setText(
                "All edits reset to defaults: crop, cuts, split points, markers, speed, and reverse cleared."
            )

        def reset_view(self):
            self.preview.reset_view()
            self.timeline.view_start = 0.0
            self.timeline.view_span = self.timeline.duration
            self._sync_timeline_controls()
            self.timeline.update()

        def reset_panels(self):
            if hasattr(self, "editor_splitter"):
                self.editor_splitter.setSizes([780, 210])

        def _on_cut_selected(self, idx):
            self.btn_delete_cut.setEnabled(0 <= idx < len(self._cut_ranges))
            self.btn_delete_separator.setEnabled(False)
            self.btn_delete_markers.setEnabled(self.timeline.selected_marker in {"in", "out"})
            self._sync_marker_convert_button()

        def _on_separator_selected(self, idx):
            self.btn_delete_separator.setEnabled(0 <= idx < len(self._separator_points))
            self.btn_delete_cut.setEnabled(False)
            self.btn_delete_markers.setEnabled(self.timeline.selected_marker in {"in", "out"})
            self._sync_marker_convert_button()

        def _sync_marker_convert_button(self):
            marker = self.timeline.selected_marker
            if marker == "in":
                self.btn_convert_marker.setEnabled(True)
                self.btn_convert_marker.setText("Convert to Out (Ctrl+I)")
                self.btn_convert_marker.setToolTip("Convert the selected Mark In marker to Mark Out (Ctrl+I).")
            elif marker == "out":
                self.btn_convert_marker.setEnabled(True)
                self.btn_convert_marker.setText("Convert to In (Ctrl+I)")
                self.btn_convert_marker.setToolTip("Convert the selected Mark Out marker to Mark In (Ctrl+I).")
            else:
                self.btn_convert_marker.setEnabled(False)
                self.btn_convert_marker.setText("Select Marker")
                self.btn_convert_marker.setToolTip("Select Mark In or Mark Out before converting (Ctrl+I).")

        def _on_zoom_slider(self, value):
            if self._syncing_zoom:
                return
            dur = max(0.001, float(self.timeline.duration))
            min_span = min(dur, 0.05)
            frac = max(0.0, min(1.0, float(value) / 100.0))
            span = dur * (min_span / dur) ** frac          # log: 0 -> full view, 100 -> deepest zoom
            self.timeline.set_zoom_ratio(dur / span, focus_time=self.timeline.playhead)
            self._sync_timeline_controls()

        def _on_view_scroll(self, value):
            if self._syncing_view:
                return
            max_start = max(0.0, float(self.timeline.duration) - float(self.timeline.view_span))
            scale = max(1.0, float(getattr(self, "_view_scroll_scale", 10000)))
            start = 0.0 if max_start <= 1e-9 else (float(value) / scale) * max_start
            self.timeline.set_view_start(start)

        def _nudge_view_scroll(self, direction: int):
            step = max(0.25, self.timeline.view_span * 0.08)
            self.timeline.scroll_view(float(direction) * step)
            self._sync_timeline_controls()

        def _nudge_timeline_zoom(self, direction: int):
            value = max(0, min(100, self.zoom_slider.value() + int(direction) * 4))
            self.zoom_slider.setValue(value)

        def _sync_timeline_controls(self):
            if not hasattr(self, "zoom_slider"):
                return
            dur = max(0.001, float(self.timeline.duration))
            min_span = min(dur, 0.05)
            span = max(min_span, min(dur, float(self.timeline.view_span)))
            denom = math.log(min_span / dur) if dur > min_span else -1.0
            frac = (math.log(span / dur) / denom) if denom else 0.0
            value = int(round(max(0.0, min(1.0, frac)) * 100.0))
            self._syncing_zoom = True
            self.zoom_slider.setValue(max(0, min(100, value)))
            self._syncing_zoom = False
            if hasattr(self, "zoom_value_label"):
                self.zoom_value_label.setText(f"{max(0, min(100, value))}%")
            max_start_seconds = max(0.0, float(self.timeline.duration) - float(self.timeline.view_span))
            scale = max(1, int(getattr(self, "_view_scroll_scale", 10000)))
            span_ratio = max(0.001, min(1.0, float(self.timeline.view_span) / max(0.001, float(self.timeline.duration))))
            self._syncing_view = True
            self.view_scroll.setRange(0, 0 if max_start_seconds <= 1e-9 else scale)
            page = max(1, int(round(scale * span_ratio)))
            self.view_scroll.setPageStep(page)
            self.view_scroll.setSingleStep(max(1, page // 10))
            value = 0 if max_start_seconds <= 1e-9 else int(round((float(self.timeline.view_start) / max_start_seconds) * scale))
            self.view_scroll.setValue(max(0, min(self.view_scroll.maximum(), value)))
            self._syncing_view = False
            self._sync_timeline_view_handle()

        def _sync_timeline_view_handle(self):
            if not hasattr(self, "view_scroll"):
                return
            ratio = max(0.02, min(1.0, float(self.timeline.view_span) / max(0.001, float(self.timeline.duration))))
            track_width = max(40, self.view_scroll.width() - 24)
            width = int(max(34, min(track_width, track_width * ratio)))
            if getattr(self, "_view_handle_width_px", None) == width:
                return
            self._view_handle_width_px = width
            # Re-applying a stylesheet forces a full style recompute; during a zoom
            # gesture the handle width changes every step, so debounce it — apply
            # once motion settles. (The thumb size just lags a frame; cheap & invisible.)
            timer = getattr(self, "_view_handle_timer", None)
            if timer is None:
                timer = QtCore.QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(self._apply_view_handle_style)
                self._view_handle_timer = timer
            timer.start(90)

        def _apply_view_handle_style(self):
            if not hasattr(self, "view_scroll"):
                return
            width = int(getattr(self, "_view_handle_width_px", 34) or 34)
            self.view_scroll.setStyleSheet(
                "QSlider#timelineViewSlider::groove:horizontal {"
                "background: #18212b; height: 5px; border-radius: 3px;"
                "}"
                "QSlider#timelineViewSlider::sub-page:horizontal {"
                "background: #18212b; border-radius: 3px;"
                "}"
                "QSlider#timelineViewSlider::handle:horizontal {"
                f"background: #8cff9d; width: {width}px; height: 12px; margin: -4px 0; "
                "border-radius: 6px; border: 1px solid #2ea043;"
                "}"
                "QSlider#timelineViewSlider::handle:horizontal:hover { background: #c7ffd0; }"
            )

        def _refresh_all(self):
            self._cut_ranges = normalize_ranges(self._cut_ranges, self.duration)
            self._separator_points = sorted(
                set(
                    round(float(value), 6)
                    for value in self._separator_points
                    if 1e-6 < float(value) < self.duration - 1e-6
                )
            )
            self.timeline.set_cut_ranges(self._cut_ranges)
            self.timeline.set_separator_points(self._separator_points)
            self.timeline.set_marks(self._mark_in, self._mark_out)
            self.timeline.selected_cut = min(self.timeline.selected_cut, len(self._cut_ranges) - 1)
            self.timeline.selected_separator = min(self.timeline.selected_separator, len(self._separator_points) - 1)
            has_cuts = bool(self._cut_ranges)
            self.btn_delete_cut.setEnabled(0 <= self.timeline.selected_cut < len(self._cut_ranges))
            self.btn_delete_separator.setEnabled(0 <= self.timeline.selected_separator < len(self._separator_points))
            self.btn_delete_markers.setEnabled(self.timeline.selected_marker in {"in", "out"})
            self.btn_delete_all.setEnabled(has_cuts)
            self.btn_invert.setEnabled(has_cuts)
            self._sync_marker_convert_button()
            self._sync_crop_fields()
            top, left, right, bottom = self.preview.margins
            crop_w = max(1, self.source_w - left - right)
            crop_h = max(1, self.source_h - top - bottom)
            in_text = seconds_to_timecode(self._mark_in) if self._mark_in is not None else "--"
            out_text = seconds_to_timecode(self._mark_out) if self._mark_out is not None else "--"
            summary = (
                f"Crop {crop_w}x{crop_h}  |  Mark In {in_text}  |  Mark Out {out_text}  |  "
                f"Cuts {len(self._cut_ranges)}  |  Splits {len(self._separator_points)}  |  Videos {max(1, len(self.join_segments))}  |  Chapters {len(self.chapters)}  |  Speed {self._speed():g}x  |  "
                f"Reverse {'yes' if self.reverse_box.isChecked() else 'no'}"
            )
            # Tidy, grouped multi-line version for the Speed panel (avoids the
            # tangled single-line wrap in the narrow column).
            panel_summary = (
                f"Crop&nbsp; <b>{crop_w}×{crop_h}</b><br>"
                f"In {in_text} &nbsp;·&nbsp; Out {out_text}<br>"
                f"Cuts {len(self._cut_ranges)} &nbsp;·&nbsp; Splits {len(self._separator_points)} "
                f"&nbsp;·&nbsp; Speed {self._speed():g}× &nbsp;·&nbsp; "
                f"{'Reversed' if self.reverse_box.isChecked() else 'Forward'}"
            )
            self.summary_label.setText(panel_summary)
            self.status.setText(summary)
            parts: list[str] = []
            if self._cut_ranges:
                shown = "   ".join(
                    f"#{idx + 1} {seconds_to_timecode(s)}->{seconds_to_timecode(e)}"
                    for idx, (s, e) in enumerate(self._cut_ranges[:5])
                )
                more = f"   +{len(self._cut_ranges) - 5} more" if len(self._cut_ranges) > 5 else ""
                parts.append("Cut ranges: " + shown + more)
            if self._separator_points:
                shown = "   ".join(
                    f"#{idx + 1} {seconds_to_timecode(value)}"
                    for idx, value in enumerate(self._separator_points[:8])
                )
                more = f"   +{len(self._separator_points) - 8} more" if len(self._separator_points) > 8 else ""
                parts.append("Splits: " + shown + more)
            if parts:
                self.cut_list_label.setText("   |   ".join(parts))
            else:
                self.cut_list_label.setText("Cut ranges and Splits: none. Mark In/Out and press Add Cut(s), or press S at the CTI to split the final output into parts.")
            self._update_undo_redo()
            self._sync_timeline_controls()
            self.timeline.update()

        def _start_waveform(self):
            if not bool(self.request.get("has_audio")):
                if hasattr(self, "status"):
                    self.status.setText("No audio stream is available for waveform preview.")
                return
            # Decode the audio to low-rate mono PCM; peaks are computed from it and
            # drawn as a crisp vector waveform (no stretched image).
            args = ["-hide_banner", "-loglevel", "error", "-y"]
            if self.join_segments:
                labels = []
                for idx, segment in enumerate(self.join_segments):
                    args.extend(["-i", str(segment["path"])])
                    labels.append(f"[a{idx}]")
                filters = []
                for idx, _segment in enumerate(self.join_segments):
                    filters.append(f"[{idx}:a:0]aformat=channel_layouts=mono,aresample=4000,asetpts=PTS-STARTPTS[a{idx}]")
                filters.append(f"{''.join(labels)}concat=n={len(self.join_segments)}:v=0:a=1[mix]")
                args.extend(["-filter_complex", ";".join(filters), "-map", "[mix]"])
            else:
                args.extend([
                    "-i", str(self.request.get("input_path") or ""),
                    "-filter_complex", "[0:a:0]aformat=channel_layouts=mono,aresample=4000[mix]",
                    "-map", "[mix]",
                ])
            args.extend(["-f", "s16le", "-acodec", "pcm_s16le", str(self._wave_path)])
            self._wave_proc = QtCore.QProcess(self)
            self._wave_proc.finished.connect(self._waveform_finished)
            self._wave_proc.start(str(self.request.get("ffmpeg") or "ffmpeg"), args)

        def _waveform_finished(self, *_args):
            if not self._wave_path.exists() or self._wave_path.stat().st_size == 0:
                self.status.setText("Waveform preview could not be generated.")
                return
            try:
                data = self._wave_path.read_bytes()
                if not data:
                    return
                # Hand the raw mono PCM to the timeline; it renders a crisp,
                # detailed per-pixel waveform from it at any zoom.
                self.timeline.set_pcm(data, 4000)
            except Exception as exc:  # noqa: BLE001
                self.status.setText(f"Waveform preview could not be generated ({exc}).")

        def resizeEvent(self, event):
            super().resizeEvent(event)
            # Guard: a resize can fire from the early empty-shell show() before
            # _build_ui has created self.timeline.
            if hasattr(self, "timeline"):
                self.timeline.update()

        def keyPressEvent(self, event):
            if event.key() == Qt.Key_Alt:
                self._set_zoom_out_mode(True)
                event.accept()
                return
            modifiers = event.modifiers()
            ctrl = bool(modifiers & Qt.ControlModifier)
            shift = bool(modifiers & Qt.ShiftModifier)
            alt = bool(modifiers & Qt.AltModifier)
            vk = int(event.nativeVirtualKey() or 0)
            if self._text_input_has_focus() and not ctrl and not alt:
                super().keyPressEvent(event)
                return

            action = None
            if ctrl and shift and vk == WIN_VK["i"]:
                action = self.invert_cuts
            elif ctrl and shift and vk == WIN_VK["z"]:
                action = self._redo
            elif ctrl and alt and vk == WIN_VK["left"]:
                action = lambda: self.seek_nearest_cut_edge(-1)
            elif ctrl and alt and vk == WIN_VK["right"]:
                action = lambda: self.seek_nearest_cut_edge(1)
            elif ctrl and vk in (WIN_VK["i"], WIN_VK["o"]):
                action = self.convert_selected_marker
            elif ctrl and vk == WIN_VK["z"]:
                action = self._undo
            elif ctrl and vk == WIN_VK["y"]:
                action = self._redo
            elif ctrl and vk == WIN_VK["r"]:
                action = self.reset_crop
            elif ctrl and vk == WIN_VK["0"]:
                action = self.reset_view
            elif ctrl and vk == WIN_VK["u"]:
                action = self.toggle_crop_overlay
            elif ctrl and vk in (WIN_VK["plus"], WIN_VK["kp_add"]):
                action = self.preview_zoom_in
            elif ctrl and vk in (WIN_VK["minus"], WIN_VK["kp_subtract"]):
                action = self.preview_zoom_out
            elif not ctrl and not alt:
                action = {
                    WIN_VK["space"]: self.toggle_playback,
                    WIN_VK["i"]: self.mark_in,
                    WIN_VK["o"]: self.mark_out,
                    WIN_VK["a"]: self.add_cut,
                    WIN_VK["s"]: self.add_separator,
                    WIN_VK["delete"]: self.delete_selection,
                    WIN_VK["m"]: self.toggle_mute,
                    WIN_VK["home"]: lambda: self.seek(0.0),
                    WIN_VK["end"]: lambda: self.seek(self.duration),
                    WIN_VK["h"]: lambda: self.set_preview_tool("hand"),
                    WIN_VK["z"]: lambda: self.set_preview_tool("zoom"),
                    WIN_VK["plus"]: lambda: self._nudge_timeline_zoom(1),
                    WIN_VK["kp_add"]: lambda: self._nudge_timeline_zoom(1),
                    WIN_VK["equal"]: lambda: self._nudge_timeline_zoom(1),
                    WIN_VK["minus"]: lambda: self._nudge_timeline_zoom(-1),
                    WIN_VK["kp_subtract"]: lambda: self._nudge_timeline_zoom(-1),
                }.get(vk)
                if action is None and vk == WIN_VK["left"]:
                    action = (lambda: self.seek(self.current_time() - 5.0)) if shift else (lambda: self.seek(self.current_time() - 1.0))
                elif action is None and vk == WIN_VK["right"]:
                    action = (lambda: self.seek(self.current_time() + 5.0)) if shift else (lambda: self.seek(self.current_time() + 1.0))
            if action is not None:
                action()
                event.accept()
                return
            super().keyPressEvent(event)

        def keyReleaseEvent(self, event):
            if event.key() == Qt.Key_Alt:
                self._set_zoom_out_mode(False)
                event.accept()
                return
            super().keyReleaseEvent(event)

        def confirm(self):
            self._snap_crop_to_even()
            margins = self._normalize_crop_margins_even([int(v) for v in self.preview.margins])
            cuts = normalize_ranges(self._cut_ranges, self.duration)
            keep_ranges = [[float(s), float(e)] for s, e in (invert_cuts_to_keep(cuts, self.duration) if cuts else [])]
            speed = float(self._speed())
            reverse = bool(self.reverse_box.isChecked())
            include_audio = bool(self.include_audio_box.isChecked())
            self.result = {
                "status": "ok",
                "margins": margins,
                "keep_ranges": keep_ranges,
                "separator_points": [float(v) for v in self._separator_points],
                "speed": speed,
                "reverse": reverse,
                "include_audio": include_audio,
            }
            self.close()

        def cancel(self):
            self.result = {"status": "canceled"}
            self.close()

        def closeEvent(self, event):
            self._closing = True
            try:
                self.player.stop()
            except Exception:
                pass
            # Stop the frame-extract QThread first — destroying a running QThread on
            # teardown crashes Qt (0xC0000409).
            try:
                self._stop_preview_frame_worker()
            except Exception:
                pass
            for _attr in ("_rev_proc", "_wave_proc"):
                try:
                    proc = getattr(self, _attr, None)
                    if proc is not None and proc.state() != QtCore.QProcess.NotRunning:
                        proc.kill()
                        proc.waitForFinished(1000)
                except Exception:
                    pass
            try:
                self._wave_temp.cleanup()
            except Exception:
                pass
            super().closeEvent(event)

    return UnifiedVideoEditorWindow(request)


def build_audio_transform_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    QMainWindow = QtWidgets.QMainWindow
    QWidget = QtWidgets.QWidget
    QVBoxLayout = QtWidgets.QVBoxLayout
    QHBoxLayout = QtWidgets.QHBoxLayout
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QTabWidget = QtWidgets.QTabWidget
    QFrame = QtWidgets.QFrame
    QStyle = QtWidgets.QStyle

    class AudioTransformEditorWindow(QMainWindow):
        def __init__(self, req):
            super().__init__()
            self.request = req
            self.duration = float(req.get("duration") or 0.0)
            self.result = {"status": "canceled"}
            self._icon = _icon_loader(self, self.style())
            self._embedded_editors: list[Any] = []
            self.setWindowTitle("FFmWiz Audio Cut / Speed / Reverse")
            _apply_window_icon(self, self._icon)
            self.setMinimumSize(1080, 680)
            self._build_ui()

        def _child_request(self, mode: str) -> dict[str, Any]:
            child = dict(self.request)
            child["mode"] = mode
            return child

        def _embed_editor(self, title: str, editor: Any) -> QWidget:
            self._embedded_editors.append(editor)
            widget = editor.takeCentralWidget()
            if widget is None:
                widget = QWidget()
                layout = QVBoxLayout(widget)
                layout.addWidget(QLabel(f"{title} could not be embedded."))
            widget.setParent(self)
            _hide_embedded_editor_actions(widget)
            return widget

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
            title = QLabel("FFmWiz Audio Cut / Speed / Reverse")
            title.setObjectName("title")
            h.addWidget(title)
            h.addStretch(1)
            root.addWidget(header)

            self.tabs = QTabWidget()
            self.cut_editor = build_audio_cut_editor(self._child_request("audio_cut"))
            self.speed_editor = build_speed_editor(self._child_request("audio_speed"), "audio")
            self.tabs.addTab(self._embed_editor("Audio Cut", self.cut_editor), "Audio Cut")
            self.tabs.addTab(self._embed_editor("Speed / Reverse", self.speed_editor), "Speed / Reverse")
            root.addWidget(self.tabs, 1)

            footer = QHBoxLayout()
            self.status = QLabel("Edit audio cuts and speed/reverse in one place.")
            self.status.setObjectName("dim")
            footer.addWidget(self.status, 1)
            btn_cancel = QPushButton("Cancel (Esc)")
            btn_cancel.setObjectName("danger")
            btn_cancel.clicked.connect(self.cancel)
            footer.addWidget(btn_cancel)
            btn_apply = QPushButton(self._icon("check", QStyle.SP_DialogOkButton), " Confirm (Enter)")
            btn_apply.setObjectName("primary")
            btn_apply.clicked.connect(self.confirm)
            footer.addWidget(btn_apply)
            root.addLayout(footer)
            QtGui.QShortcut(QtGui.QKeySequence("Esc"), self, activated=self.cancel)
            QtGui.QShortcut(QtGui.QKeySequence("Return"), self, activated=self.confirm)
            QtGui.QShortcut(QtGui.QKeySequence("Enter"), self, activated=self.confirm)

        def confirm(self):
            keep_ranges: list[list[float]] = []
            try:
                cuts = normalize_ranges(self.cut_editor.waveform.cut_ranges, self.duration)
                keep_ranges = [[float(s), float(e)] for s, e in (invert_cuts_to_keep(cuts, self.duration) if cuts else [])]
            except Exception:
                keep_ranges = []
            speed = 1.0
            reverse = False
            try:
                speed = float(self.speed_editor._speed())
                reverse = bool(self.speed_editor.reverse_box.isChecked())
            except Exception:
                pass
            self.result = {
                "status": "ok",
                "keep_ranges": keep_ranges,
                "speed": speed,
                "reverse": reverse,
            }
            self.close()

        def cancel(self):
            self.result = {"status": "canceled"}
            self.close()

        def closeEvent(self, event):
            for editor in self._embedded_editors:
                _stop_embedded_editor(editor)
            super().closeEvent(event)

    return AudioTransformEditorWindow(request)


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

    _gui_log_info(
        f"GUI process started: mode={request.get('mode')}; pid={os.getpid()}; "
        f"parent_pid={_PARENT_PID}"
    )
    _set_windows_app_id()
    try:
        from PySide6.QtWidgets import QApplication  # type: ignore
        from PySide6 import QtCore  # type: ignore
    except Exception as exc:
        _write_reply(reply_path, {"status": "error",
                                  "message": f"PySide6 not installed: {exc}"})
        return 3

    app = QApplication.instance() or QApplication(sys.argv)
    # PREVIEW: the native Windows style is pathologically slow on some machines
    # (~30ms per widget create/polish -> multi-second startup). Fusion is pure-Qt,
    # avoids the native theme calls, and our heavy QSS makes it look the same — but
    # it builds the window markedly faster.
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    # Dark base palette applied immediately so the window's very first frame is
    # dark instead of flashing white during the brief gap before the full QSS
    # stylesheet is applied (which is deferred until after show for fast startup).
    try:
        from PySide6.QtGui import QPalette, QColor  # type: ignore
        _bg = QColor(PALETTE.get("bg", "#0d1117"))
        _fg = QColor(PALETTE.get("text", "#e6edf3"))
        _pal = app.palette()
        for _role in (QPalette.Window, QPalette.Base, QPalette.Button, QPalette.AlternateBase, QPalette.ToolTipBase):
            _pal.setColor(_role, _bg)
        for _role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.ToolTipText):
            _pal.setColor(_role, _fg)
        app.setPalette(_pal)
    except Exception as exc:  # noqa: BLE001
        _gui_log_debug(f"Could not set dark base palette: {exc}", force=True)
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
        elif mode == "video_unified":
            window = build_unified_video_editor(request)
        elif mode == "video_speed":
            window = build_speed_editor(request, "video")
        elif mode == "audio_transform":
            window = build_audio_transform_editor(request)
        elif mode == "audio_speed":
            window = build_speed_editor(request, "audio")
        elif mode == "audio_cut":
            window = build_audio_cut_editor(request)
        else:
            _gui_log_error(f"Unknown GUI mode requested: {mode}")
            _write_reply(reply_path, {"status": "error",
                                      "message": f"Unknown mode: {mode}"})
            return 2
    except Exception as exc:
        import traceback
        _gui_log_error(f"GUI init failed for mode={mode}: {exc}")
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
    # Paint the window dark on its very first frame (a tiny, instant stylesheet)
    # so it never flashes white before the full QSS is applied a tick after show.
    try:
        window.setStyleSheet(
            f"QMainWindow {{ background-color: {PALETTE['bg']}; }}"
            f" QWidget#central {{ background-color: {PALETTE['bg']}; color: {PALETTE['text']}; }}"
        )
    except Exception:
        pass
    if request.get("start_maximized"):
        window.showMaximized()
    else:
        window.show()
    _apply_native_windows_icon(window)

    def apply_deferred_stylesheet():
        style_start = time.perf_counter()
        app.setStyleSheet(QSS + PREVIEW_COMPACT_QSS)
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

    def _force_foreground_windows():
        # QShortcut(Qt.WindowShortcut) only fires when the editor is the ACTIVE
        # top-level window. A window shown from a subprocess often does not win
        # the Windows foreground lock via activateWindow() alone, so shortcuts
        # stay dead until the user clicks. Attach to the current foreground
        # thread's input queue to bypass the lock and force activation.
        if os.name != "nt":
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = int(window.winId())
            if not hwnd:
                return
            fg = user32.GetForegroundWindow()
            cur_thread = kernel32.GetCurrentThreadId()
            fg_thread = user32.GetWindowThreadProcessId(fg, 0) if fg else 0
            attached = bool(fg_thread) and fg_thread != cur_thread
            if attached:
                user32.AttachThreadInput(fg_thread, cur_thread, True)
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                user32.SetActiveWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(fg_thread, cur_thread, False)
        except Exception as exc:
            _gui_log_debug(f"Could not force window foreground: {exc}", force=True)

    def grab_initial_keyboard_focus():
        # Without this, the window opens without keyboard focus / active state and
        # the layout-independent shortcuts only start working after the user
        # clicks inside the window. Activate the window and move keyboard focus to
        # the preview canvas (which routes keys, falling back to the window-level
        # keyPressEvent for unhandled keys) or to the window itself.
        try:
            try:
                window.setWindowState(
                    (window.windowState() & ~QtCore.Qt.WindowMinimized) | QtCore.Qt.WindowActive
                )
            except Exception:
                pass
            window.activateWindow()
            window.raise_()
            _force_foreground_windows()
            target = getattr(window, "canvas", None) or getattr(window, "preview", None)
            if target is None or not hasattr(target, "setFocus"):
                target = window
            try:
                target.setFocusPolicy(QtCore.Qt.StrongFocus)
            except Exception:
                pass
            target.setFocus(QtCore.Qt.OtherFocusReason)
            _gui_log_debug(f"{mode} GUI grabbed initial keyboard focus", force=True)
        except Exception as exc:
            _gui_log_debug(f"Could not grab initial keyboard focus: {exc}", force=True)

    # Run immediately after show and again shortly after, because the first
    # attempt can land before the window is fully mapped/activated by the OS.
    QtCore.QTimer.singleShot(0, grab_initial_keyboard_focus)
    QtCore.QTimer.singleShot(180, grab_initial_keyboard_focus)
    app.exec()

    payload = getattr(window, "result", {"status": "canceled"})
    _gui_log_info(
        f"GUI process finished: mode={mode}; status={payload.get('status', 'unknown')}; "
        f"elapsed={time.perf_counter() - gui_start:.2f}s"
    )
    _write_reply(reply_path, payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())


























