"""FFmWiz unified video editor — QtQuick/QML implementation (Phase 1).

This is the NEW, modern GUI engine. It is a SEPARATE module from the classic
PySide6-widgets editor (assets/runtime/ffmwiz_gui.py), which is kept intact and
remains the default. FFmWiz launches this module only when the QML engine is
selected (config "gui_engine": "qml" or env FFMWIZ_GUI_ENGINE=qml), and only for
the unified video editor mode. All other modes still use the classic engine.

IPC contract is identical to the classic engine:
  argv:  --request <request.json>  --reply <reply.json>
  reply: {"status": "ok", "margins": [t,l,r,b], "keep_ranges": [[s,e],...],
          "separator_points": [...], "speed": <float>, "reverse": <bool>,
          "include_audio": <bool>}   or   {"status": "canceled"}   or
         {"status": "error", "message": "...", "traceback": "..."}

Why QtQuick: the scene graph renders on the GPU and avoids the QWidget
QSS-polish cost that made the classic editor slow to appear; the window is
painted from its first frame (no white flash); and VideoOutput preserves the
source aspect ratio natively (no stretching for mixed-orientation joins).

Phase 1 scope (fully working): modern dark theme using the EXACT current colors,
fast load, no white flash, aspect-correct video preview across joined segments,
play/pause/seek, crop margins, speed, reverse, include-audio, trim via mark
in/out, and confirm/cancel returning the correct reply. Pending phases: vector
waveform, multi-range cuts, split points, and frame-accurate scrubbing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

# Reuse the classic engine's palette and logging so colors/log format match.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

try:
    from ffmwiz_gui import PALETTE as _PALETTE  # type: ignore
    from ffmwiz_gui import _gui_write_log as _classic_write_log  # type: ignore
except Exception:  # pragma: no cover - palette fallback if import fails
    _PALETTE = {
        "bg": "#0d1117", "panel": "#161b22", "panel_alt": "#1a1f2a",
        "surface": "#21262d", "border": "#30363d", "border_strong": "#3a4150",
        "timeline_bg": "#0a0d12", "timeline_track": "#1c2128", "tick_hi": "#e6edf3",
        "tick_lo": "#7d8590", "accent": "#1f6feb", "accent_hover": "#388bfd",
        "accent_text": "#79b4ff", "green": "#238636", "green_text": "#56d364",
        "danger": "#a40e26", "danger_text": "#ff7b72", "warn": "#d29922",
        "marker_in": "#2ddc7f", "marker_out": "#d29922", "playhead": "#ff4d55",
        "playhead_halo": "#2f81f7", "text": "#e6edf3", "text_dim": "#c9d1d9",
        "text_mute": "#7d8590", "chapter_text": "#d9bdff",
    }
    _classic_write_log = None


def _log(level: str, message: str) -> None:
    """Write to the shared FFmWiz log (same format) when available, else stderr."""
    try:
        if _classic_write_log is not None:
            _classic_write_log(level, f"[QML] {message}")
            return
    except Exception:
        pass
    print(f"[QML] {level}: {message}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="FFmWiz QML unified video editor")
    parser.add_argument("--request", required=True)
    parser.add_argument("--reply", required=True)
    args = parser.parse_args()

    reply_path = Path(args.reply)

    def write_reply(payload: dict) -> None:
        try:
            reply_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # pragma: no cover
            _log("ERROR", f"Could not write reply: {exc}")

    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
    except Exception as exc:
        write_reply({"status": "error", "message": f"Could not read request: {exc}"})
        return 3

    # Set the log path so _gui_write_log targets the shared FFmWiz log file.
    try:
        import ffmwiz_gui  # type: ignore
        log_path = request.get("log_path")
        if log_path:
            ffmwiz_gui._GUI_LOG_PATH = log_path  # noqa: SLF001
    except Exception:
        pass

    try:
        from PySide6.QtGui import QGuiApplication, QColor
        from PySide6.QtCore import QObject, Slot, Property, QUrl, Qt
        from PySide6.QtQml import QQmlApplicationEngine
        # Importing QtQuickControls2 / QtMultimedia ensures their QML plugins load.
        from PySide6 import QtQuick  # noqa: F401
    except Exception as exc:
        write_reply({"status": "error", "message": f"PySide6 QtQuick is not available: {exc}",
                     "traceback": traceback.format_exc()})
        return 3

    selftest = os.environ.get("FFMWIZ_QML_SELFTEST") == "1"

    class Bridge(QObject):
        """Exposes the request to QML and collects the editor result."""

        def __init__(self, app: QGuiApplication, req: dict) -> None:
            super().__init__()
            self._app = app
            self._req = req
            self._submitted = False
            self._request_json = json.dumps(req, ensure_ascii=False)
            self._palette_json = json.dumps(_PALETTE, ensure_ascii=False)

        # --- Read-only data for QML ---
        def _get_request(self) -> str:
            return self._request_json

        def _get_palette(self) -> str:
            return self._palette_json

        requestJson = Property(str, _get_request, constant=True)
        paletteJson = Property(str, _get_palette, constant=True)

        # --- Result callbacks from QML ---
        @Slot(str)
        def submit(self, result_json: str) -> None:
            self._submitted = True
            try:
                result = json.loads(result_json)
            except Exception as exc:
                _log("ERROR", f"Bad result JSON from QML: {exc}")
                result = {"status": "error", "message": f"Bad result JSON: {exc}"}
            if "status" not in result:
                result["status"] = "ok"
            write_reply(result)
            _log("INFO", f"Editor submitted: status={result.get('status')}")
            self._app.quit()

        @Slot()
        def cancel(self) -> None:
            self._submitted = True
            write_reply({"status": "canceled"})
            _log("INFO", "Editor canceled")
            self._app.quit()

        @Slot(str)
        def logMessage(self, message: str) -> None:  # noqa: N802 (QML camelCase)
            _log("DEBUG", message)

        def finalize_if_unsubmitted(self) -> None:
            if not self._submitted:
                write_reply({"status": "canceled"})

    app = QGuiApplication(sys.argv)
    app.setApplicationName("FFmWiz")
    app.setApplicationDisplayName("FFmWiz Unified Video Editor")
    # App icon (reuse the bundled asset) so the taskbar/window match the classic UI.
    try:
        from PySide6.QtGui import QIcon
        ico = _THIS_DIR.parent / "icons" / "ffmwiz_app.ico"
        if ico.exists():
            app.setWindowIcon(QIcon(str(ico)))
    except Exception:
        pass

    bridge = Bridge(app, request)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)

    qml_path = _THIS_DIR / "qml" / "UnifiedEditor.qml"
    if not qml_path.exists():
        write_reply({"status": "error", "message": f"QML file missing: {qml_path}"})
        return 4

    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        write_reply({"status": "error", "message": "QML failed to load (see stderr)."})
        return 4

    _log("INFO", f"QML editor loaded: mode={request.get('mode')}; "
                 f"segments={len(request.get('join_segments') or [])}")

    if selftest:
        # Headless verification: QML compiled and a root window exists. Report ok
        # without entering the event loop so CI/offscreen runs do not hang.
        write_reply({"status": "ok", "selftest": True,
                     "margins": [0, 0, 0, 0], "keep_ranges": [],
                     "separator_points": [], "speed": 1.0,
                     "reverse": False, "include_audio": bool(request.get("has_audio"))})
        _log("INFO", "QML self-test passed (root window created).")
        return 0

    rc = app.exec()
    bridge.finalize_if_unsubmitted()
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
