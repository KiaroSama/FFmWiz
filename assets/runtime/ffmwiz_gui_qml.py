"""FFmWiz unified video editor — QtQuick/QML implementation (modern engine).

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
import array
import json
import os
import subprocess
import sys
import tempfile
import threading
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
        from PySide6.QtCore import QObject, Slot, Signal, Property, QUrl, Qt
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

        # Emitted (queued) from the decode thread with a JSON array of peak
        # amplitudes (0..1) spanning the whole timeline, for the waveform.
        waveformReady = Signal(str)

        def __init__(self, app: QGuiApplication, req: dict) -> None:
            super().__init__()
            self._app = app
            self._req = req
            self._submitted = False
            self._request_json = json.dumps(req, ensure_ascii=False)
            self._palette_json = json.dumps(_PALETTE, ensure_ascii=False)
            self._wave_thread: threading.Thread | None = None

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

        @Slot()
        def startWaveform(self) -> None:  # noqa: N802 (QML camelCase)
            """Decode the (joined) audio to mono PCM in a background thread and
            emit waveformReady with a downsampled peak array for the timeline."""
            if os.environ.get("FFMWIZ_QML_SELFTEST") == "1":
                self.waveformReady.emit("[]")
                return
            if not self._req.get("has_audio"):
                self.waveformReady.emit("[]")
                return
            if self._wave_thread is not None:
                return
            self._wave_thread = threading.Thread(target=self._decode_waveform, daemon=True)
            self._wave_thread.start()

        def _decode_waveform(self) -> None:
            try:
                peaks = self._compute_peaks()
                self.waveformReady.emit(json.dumps(peaks))
            except Exception as exc:  # noqa: BLE001
                _log("DEBUG", f"Waveform decode failed: {exc}")
                self.waveformReady.emit("[]")

        def _compute_peaks(self) -> list[float]:
            ffmpeg = str(self._req.get("ffmpeg") or "ffmpeg")
            rate = 2000  # mono samples/sec — enough for an amplitude envelope
            segs = self._req.get("join_segments") or []
            args = ["-hide_banner", "-loglevel", "error", "-y"]
            if segs:
                for seg in segs:
                    args += ["-i", str(seg.get("path"))]
                filt = [f"[{i}:a:0]aformat=channel_layouts=mono,aresample={rate},asetpts=PTS-STARTPTS[a{i}]"
                        for i in range(len(segs))]
                filt.append("".join(f"[a{i}]" for i in range(len(segs)))
                            + f"concat=n={len(segs)}:v=0:a=1[mix]")
                args += ["-filter_complex", ";".join(filt), "-map", "[mix]"]
            else:
                args += ["-i", str(self._req.get("input_path") or ""),
                         "-filter_complex", f"[0:a:0]aformat=channel_layouts=mono,aresample={rate}[mix]",
                         "-map", "[mix]"]
            fd, pcm_path = tempfile.mkstemp(suffix=".pcm", prefix="ffmwiz_qmlwave_")
            os.close(fd)
            try:
                args += ["-f", "s16le", "-acodec", "pcm_s16le", pcm_path]
                subprocess.run([ffmpeg, *args], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                data = Path(pcm_path).read_bytes()
            finally:
                try:
                    os.remove(pcm_path)
                except OSError:
                    pass
            if len(data) < 2:
                return []
            samples = array.array("h")
            samples.frombytes(data[: len(data) - (len(data) % 2)])
            n = len(samples)
            if n == 0:
                return []
            buckets = min(2400, n)
            step = n / buckets
            peaks: list[float] = []
            for b in range(buckets):
                s0 = int(b * step)
                s1 = int((b + 1) * step)
                if s1 <= s0:
                    s1 = s0 + 1
                chunk = samples[s0:s1]
                if chunk:
                    mx = max(max(chunk), -min(chunk))  # C-fast min/max on array
                    peaks.append(min(1.0, mx / 32768.0))
                else:
                    peaks.append(0.0)
            return peaks

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
