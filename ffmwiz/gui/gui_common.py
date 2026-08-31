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
import functools
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


@functools.cache
def _bound_win32():
    """The Win32 entry points this module calls, with their types declared.

    `ctypes` defaults every unbound argument and return value to `c_int`, which
    is 32 bits. A Windows HANDLE is 64 bits on a 64-bit build, so an unbound
    `OpenProcess` hands back a TRUNCATED handle -- and it can look like it works
    when the upper bits happen to be all ones and sign extension rebuilds the
    value by luck. `CloseHandle` on a truncated handle closes something else and
    leaks the real one, which matters here because the watchdog runs for the
    life of the editor. The icon path below already declared its signatures;
    these did not.

    Cached because the watchdog asks on every tick and the declarations only
    need to happen once per process.
    """
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    shell32 = ctypes.windll.shell32
    shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
    shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
    return kernel32, shell32


def _set_windows_app_id() -> None:
    if os.name != "nt":
        return
    try:
        _kernel32, shell32 = _bound_win32()
        # It returns an HRESULT. Ignoring it meant the log said the id was set
        # whatever happened.
        result = shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        if result == 0:
            _gui_log_debug(f"Set Windows AppUserModelID={APP_ID}", force=True)
        else:
            _gui_log_debug(
                f"Could not set Windows AppUserModelID={APP_ID}: HRESULT 0x{result & 0xFFFFFFFF:08X}",
                force=True)
    except Exception as exc:
        _gui_log_debug(f"Could not set Windows AppUserModelID: {exc}", force=True)


def _parent_process_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return True
    try:
        if os.name == "nt":
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            SYNCHRONIZE = 0x00100000
            WAIT_TIMEOUT = 0x00000102
            kernel32, _shell32 = _bound_win32()
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                False,
                int(pid),
            )
            if not handle:
                return False
            try:
                return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
            finally:
                kernel32.CloseHandle(handle)
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


def _apply_window_icon(window, icon_loader, native: bool = True) -> None:
    """Set the window icon. `native=False` skips the HWND stamp.

    The native stamp calls window.winId(), which FORCES Qt to create a real
    Windows window handle. For an editor that is about to be reparented into a
    tab that handle is pure waste, and reparenting an already-realized native
    widget tree is expensive: measured at 5.07 s for the Audio Cut editor alone.
    An embedded editor never appears in the taskbar, so it has no use for it.
    """
    try:
        icon = _qt_app_icon()
        if icon is None or icon.isNull():
            icon = icon_loader("ffmwiz_app", None)
        if icon is None or icon.isNull():
            _gui_log_debug(f"Window icon is null for {window.windowTitle()}", force=True)
            return
        window.setWindowIcon(icon)
        if native:
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

    # Render this window to a PNG and quit, instead of running the editor.
    # The counterpart of FFMWIZ_QML_SHOT, so the two engines can be compared
    # side by side. Under QT_QPA_PLATFORM=offscreen no native window exists,
    # so this can never put one in front of the user.
    shot = os.environ.get('FFMWIZ_GUI_SHOT')
    if shot:
        size = os.environ.get('FFMWIZ_GUI_SHOT_SIZE', '')
        if 'x' in size:
            w_px, _, h_px = size.partition('x')
            window.resize(int(w_px), int(h_px))

        def take_shot():
            try:
                window.grab().save(shot)
                _gui_log_debug(f'GUI grabbed to {shot}', force=True)
            except Exception as exc:  # noqa: BLE001
                _gui_log_debug(f'Could not grab the GUI: {exc}', force=True)
            app.quit()

        # After the deferred stylesheet lands, or the capture shows the
        # unstyled first frame rather than the editor the user sees.
        QtCore.QTimer.singleShot(2500, take_shot)

    app.exec()

    payload = getattr(window, "result", {"status": "canceled"})
    _gui_log_info(
        f"GUI process finished: mode={mode}; status={payload.get('status', 'unknown')}; "
        f"elapsed={time.perf_counter() - gui_start:.2f}s"
    )
    _write_reply(reply_path, payload)
    return 0
