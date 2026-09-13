"""FFmWiz unified video editor — QtQuick/QML implementation (modern engine).

This is the NEW, modern GUI engine. It is a SEPARATE module from the classic
PySide6-widgets editor (ffmwiz/gui/ffmwiz_gui.py), which is kept intact and
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

Implemented scope (all phases): modern dark theme using the EXACT current
colors, fast load with no white flash, aspect-correct video preview across
joined segments (PreserveAspectFit, no stretching for mixed orientations),
double-buffered two-player seamless join playback, vector audio waveform,
multi-range cuts, split points, frame-accurate scrubbing, timeline zoom/pan,
interactive draggable crop handles on the preview, speed, reverse,
include-audio, mark in/out, and confirm/cancel returning the correct reply.

Note: a headless self-test (FFMWIZ_QML_SELFTEST=1 + QT_QPA_PLATFORM=offscreen)
verifies the QML compiles and the root window is created; it does not exercise
interactive playback or painting, which must be verified on a real display.
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
# The PACKAGE ROOT, not this directory: the sibling modules are addressed as
# `ffmwiz.gui.<name>` now, so a bare `sys.path` entry for this folder would
# import them a second time under different names. Bare imports were also why
# the installed package could not import a single GUI module (D10).
# Three levels now: this file moved into ffmwiz/gui/modern/.
_PACKAGE_ROOT = _THIS_DIR.parent.parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

# Import the palette/log writer from the modules that DEFINE them. ffmwiz_gui
# is only a wrapper: it injects the assembled namespace INTO its siblings and
# exports nothing itself, so importing from it always raised ImportError and
# silently dropped the QML engine onto a stale 26-key fallback palette with no
# shared-log output (D23). gui_style/gui_common have no top-level Qt import, so
# this stays cheap and keeps the module importable without PySide6.
from ffmwiz.gui import gui_geometry  # type: ignore
from ffmwiz.gui.modern.gui_qml_bridge import build_bridge  # type: ignore
from ffmwiz.gui.gui_style import PALETTE as _PALETTE  # type: ignore
from ffmwiz.gui.gui_common import _gui_write_log as _classic_write_log  # type: ignore


def _log(level: str, message: str) -> None:
    """Write to the shared FFmWiz log (same format) when available, else stderr."""
    try:
        if _classic_write_log is not None:
            _classic_write_log(level, f"[QML] {message}")
            return
    except Exception:
        pass
    print(f"[QML] {level}: {message}", file=sys.stderr)


# The waveform model lives in its own module (this file hit the size ceiling).
# Re-exported here because it is part of this driver's public surface: the
# request builders and the waveform tests both address it as
# `ffmwiz.gui.modern.ffmwiz_gui_qml.<name>`.
from ffmwiz.gui.modern.gui_qml_waveform import (  # noqa: E402
    WAVE_RATE, WAVE_ENV_STEP, WAVE_MAX_BUCKETS, compute_wave_key,
    segment_audio_stream_spec, segment_audio_filter, build_wave_decode_args,
    decode_pcm_samples, build_wave_envelope, waveform_window,
)


def normalize_request_chapters(request: dict) -> list[dict]:
    """Chapters as [{"start": s, "end": s, "title": str}] in SECONDS.

    The wire carries raw ffprobe dicts, where `start` is in time_base ticks and
    the title lives under tags.title. QML read `.start` as seconds, so a chapter
    at 3 s arrived as 3000 and was filtered out by the `<= totalDuration` check,
    while the survivor was labelled "Chapter 1" (D14). The classic engine
    already runs the same normaliser, which is idempotent."""
    return gui_geometry.normalize_chapters(request.get("chapters") or [],
                                           float(request.get("duration") or 0.0))


def reverse_proxy_wants_audio(req: dict, spec: dict) -> bool:
    """Whether the reversed preview chunk should carry audio.

    The chunk comes from ONE segment, so its own flag decides. The request-level
    flag describes the whole job -- using it muted the reverse preview of an
    audible later clip whenever input 1 was silent, and fed `areverse` to a
    silent segment in the opposite topology (R02).
    """
    if "has_audio" in spec:
        return bool(spec["has_audio"])
    return bool(req.get("has_audio"))


def build_reverse_proxy_vf(width: int) -> str:
    """Video filter chain for one reversed preview chunk.

    scale MUST come before reverse: filters run left to right and `reverse`
    buffers the entire window in memory, so scaling first cuts the peak RSS to
    roughly a third (measured 832 MB -> 299 MB on 8 s of 1080p30) — NEW-GUI1.
    scale=-2 keeps even dimensions for yuv420p."""
    return f"scale={int(width)}:-2:flags=fast_bilinear,reverse"


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
    # The global it reads lives in gui_common and must be a Path, not a str.
    try:
        from ffmwiz.gui import gui_common  # type: ignore
        log_path = request.get("log_path")
        if log_path:
            gui_common._GUI_LOG_PATH = Path(log_path)  # noqa: SLF001
    except Exception:
        pass

    request["chapters"] = normalize_request_chapters(request)

    try:
        from PySide6.QtGui import QGuiApplication, QColor
        from PySide6.QtCore import QObject, Slot, Signal, Property, QUrl, Qt
        from PySide6.QtQml import QQmlApplicationEngine
        # Importing QtQuickControls2 / QtMultimedia ensures their QML plugins load.
        from PySide6 import QtQuick  # noqa: F401
    except Exception as exc:
        write_reply({"status": "error", "message": f"PySide6 QtQuick is not available: {exc}",
                     "traceback": traceback.format_exc()})
        return 3

    selftest = os.environ.get("FFMWIZ_QML_SELFTEST") == "1"

    Bridge = build_bridge(QObject, Slot, Signal, Property, write_reply, _log,
                          reverse_proxy_wants_audio, build_reverse_proxy_vf)

    app = QGuiApplication(sys.argv)
    if os.environ.get('FFMWIZ_QML_SHOT'):
        # Same reason as the classic path: offscreen has no font database.
        from ffmwiz.gui import gui_common  # type: ignore
        _log('DEBUG', f'screenshot fonts: {gui_common.load_screenshot_fonts()}')
    app.setApplicationName("FFmWiz")
    app.setApplicationDisplayName("FFmWiz Unified Video Editor")
    # Reuse the classic engine's identity wiring instead of a second, weaker
    # copy: AppUserModelID + the multi-resolution QIcon, so Windows stops
    # grouping this window under the host python.exe (USER-2-2), and the same
    # parent watchdog so the editor dies with the FFmWiz CLI that launched it
    # instead of outliving it with its ffmpeg proxies (D15).
    try:
        from ffmwiz.gui import gui_common  # type: ignore
        gui_common._set_windows_app_id()  # noqa: SLF001
        gui_common._set_qt_application_icon(app)  # noqa: SLF001
        app.setDesktopFileName(gui_common.APP_ID)
        gui_common._PARENT_PID = int(request.get("parent_pid") or 0) or None  # noqa: SLF001
        gui_common._install_parent_watchdog(app)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        _log("DEBUG", f"Could not apply shared app identity/watchdog: {exc}")

    bridge = Bridge(app, request)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    # The palette as a CONTEXT property, not just a window property. The reusable
    # controls live in their own .qml files now, and a separate file cannot see
    # the root window's `theme` -- but every file sees the root context. One
    # source, still `gui_style.PALETTE`, reachable from all of them.
    # `Palette.col(key, fallback)` mirrors the window's own `col()` so the
    # extracted controls read exactly what they read inline. A plain dict cannot
    # carry a method, so a tiny QObject does -- and it keeps the SAME fallback
    # contract the palette guard in tests/test_qml_waveform.py enforces.
    class _Palette(QObject):
        @Slot(str, str, result=str)
        def col(self, key: str, fallback: str) -> str:
            return _PALETTE.get(key, fallback)

        @Slot(str, str, float, result=str)
        def colA(self, key: str, fallback: str, a: float) -> str:
            base = _PALETTE.get(key, fallback)
            if not (isinstance(base, str) and base.startswith("#") and len(base) == 7):
                return base
            return "#%02x%s" % (max(0, min(255, int(round(a * 255)))), base[1:])

    # Metrics, so the split files keep the one 4px grid rather than each
    # inventing its own numbers again.
    # A plain dict, not a QObject: QML reads a dict's keys as properties, and
    # `setProperty` on a QObject creates DYNAMIC properties that QML cannot see
    # at all -- every `Tok.sp2` came back undefined.
    # Type scale raised one step across the board (10/11/12/14 -> 11/12/13/15).
    # The old scale was set before the editor had real text in review shots; on
    # a 1600px window the timecodes in particular were unreadable at a glance,
    # which is the one number this UI exists to show. Rows grew with it so the
    # larger glyphs are not cramped against the button edge.
    _TOKENS = {"sp0": 2, "sp1": 4, "sp2": 8, "sp3": 12, "sp4": 16, "sp5": 24,
               "radSm": 6, "radMd": 8, "fsMicro": 11, "fsBody": 12, "fsLead": 13,
               "fsTitle": 15, "rowSm": 24, "rowMd": 28, "rowLg": 34}

    _palette_obj = _Palette()
    engine.rootContext().setContextProperty("Skin", _palette_obj)   # not "Palette": QtQuick owns that name
    engine.rootContext().setContextProperty("Tok", _TOKENS)

    qml_path = _THIS_DIR / "qml" / "UnifiedEditor.qml"
    if not qml_path.exists():
        write_reply({"status": "error", "message": f"QML file missing: {qml_path}"})
        return 4

    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        write_reply({"status": "error", "message": "QML failed to load (see stderr)."})
        return 4

    # QQuickWindow exposes winId() just like QWidget, which is all the native
    # WM_SETICON path needs (USER-2-2).
    try:
        from ffmwiz.gui import gui_common  # type: ignore
        gui_common._apply_native_windows_icon(engine.rootObjects()[0])  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        _log("DEBUG", f"Could not apply the native window icon: {exc}")

    _log("INFO", f"QML editor loaded: mode={request.get('mode')}; "
                 f"segments={len(request.get('join_segments') or [])}")

    if selftest:
        # Headless verification: QML compiled and a root window exists. Report ok
        # without entering the event loop so CI/offscreen runs do not hang.
        write_reply({"status": "ok", "selftest": True,
                     "margins": [0, 0, 0, 0], "keep_ranges": [],
                     "separator_points": [], "speed": 1.0,
                     "reverse": False, "include_audio": bool(request.get("has_audio"))})
        # Optional: render the scene graph to a PNG. This exists so the
        # editor can be reviewed VISUALLY without a window ever being
        # created -- under QT_QPA_PLATFORM=offscreen Qt has no native
        # surface at all, so unlike moving a real window off-screen there
        # is no interval in which it can appear in front of the user.
        shot = os.environ.get("FFMWIZ_QML_SHOT")
        if shot:
            try:
                win = engine.rootObjects()[0]
                size = os.environ.get("FFMWIZ_QML_SHOT_SIZE", "")
                if "x" in size:
                    w_px, _, h_px = size.partition("x")
                    win.setGeometry(0, 0, int(w_px), int(h_px))
                    # The offscreen screen is a fixed 800x800, so the window
                    # is born clamped; the resize only reaches the layout
                    # after the event loop has run once.
                    # Enough passes for a ScrollView + nested Layouts to settle.
                    # Six was not: three different width fixes in a row produced
                    # byte-identical PNGs because the grab captured the FIRST
                    # layout pass, before any binding that depends on the
                    # resized window had been re-evaluated.
                    for _ in range(40):
                        app.processEvents()
                win.grabWindow().save(shot)
                _log("INFO", f"QML scene grabbed to {shot}")
            except Exception as exc:  # noqa: BLE001
                _log("WARNING", f"Could not grab the QML scene: {exc}")
        _log("INFO", "QML self-test passed (root window created).")
        # The self-test returns before the event loop, so it used to skip the
        # cleanup below entirely and leave its owned proxy directory in %TEMP%.
        # The return value is the verdict, not decoration: an incomplete
        # cleanup means a child is still running and its files are still
        # there, and reporting 0 for that is how the caller came to believe a
        # window had shut down cleanly when it had not (A03).
        if not bridge.cleanup_reverse():
            _log("WARNING", "QML self-test finished but cleanup did not complete")
            return 3
        return 0

    try:
        rc = app.exec()
    finally:
        # `finally`: an exception out of the event loop used to skip cleanup
        # entirely and leave the proxies, the PCM file and their ffmpeg
        # children behind.
        bridge.finalize_if_unsubmitted()
        cleaned = bridge.cleanup_reverse()
    if not cleaned:
        _log("WARNING", "the editor exited before cleanup completed; "
                        "owned children or temporary files remain")
        return 3
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
