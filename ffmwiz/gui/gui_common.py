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
# This file lives in <project>/ffmwiz/gui/, so the package dir is parents[1]
# and the bundled assets (icons) live under <project>/ffmwiz/assets/.
ASSETS_ROOT = Path(__file__).resolve().parents[1] / "assets"
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


# =====================================================================
# Crop Editor window
# =====================================================================


# =====================================================================
# Speed / reverse and audio waveform editors
# =====================================================================


# =====================================================================
# Unified experimental editor shells.
# These live on the feature/unified-editors branch and embed the existing
# editor panels instead of deleting the archived standalone editors.
# =====================================================================


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
