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

# Import the palette/log writer from the modules that DEFINE them. ffmwiz_gui
# is only a wrapper: it injects the assembled namespace INTO its siblings and
# exports nothing itself, so importing from it always raised ImportError and
# silently dropped the QML engine onto a stale 26-key fallback palette with no
# shared-log output (D23). gui_style/gui_common have no top-level Qt import, so
# this stays cheap and keeps the module importable without PySide6.
import gui_geometry  # type: ignore
from gui_style import PALETTE as _PALETTE  # type: ignore
from gui_common import _gui_write_log as _classic_write_log  # type: ignore


def _log(level: str, message: str) -> None:
    """Write to the shared FFmWiz log (same format) when available, else stderr."""
    try:
        if _classic_write_log is not None:
            _classic_write_log(level, f"[QML] {message}")
            return
    except Exception:
        pass
    print(f"[QML] {level}: {message}", file=sys.stderr)


# ----------------------------------------------------------------------------
# Waveform model (classic-quality, backend-heavy).
#
# The heavy work lives here in Python so QML only draws. Audio is decoded once
# to 4000 Hz mono int16 PCM (same rate as the classic editor) and kept in
# memory, plus a decimated min/max envelope for fast zoomed-out rendering. The
# editor then asks for a per-viewport array of [min, max] pairs (one per canvas
# pixel column) via waveform_window(); zoomed-in views read the raw PCM for full
# detail, zoomed-out views read the envelope. Amplitudes are scaled against the
# int16 full scale (32768), NOT the clip's own peak, so quiet stays quiet and
# loud stays loud. This is a hybrid of options (1) full PCM and (2) a min/max
# pyramid from the task brief.
# ----------------------------------------------------------------------------

WAVE_RATE = 4000          # mono PCM sample rate for the waveform (matches classic)
WAVE_ENV_STEP = 256       # samples per decimated envelope bucket
WAVE_MAX_BUCKETS = 4000   # cap on columns returned for one viewport


def compute_wave_key(req: dict) -> str:
    """Stable cache key for the decoded waveform. Changes only when the input
    file, the join input list, the selected audio stream, the duration, or the
    sample rate change — so the waveform is decoded once and reused otherwise."""
    import hashlib

    segs = req.get("join_segments") or []
    if segs:
        parts = [str(s.get("path") or "") + ":" + str(s.get("duration") or "") for s in segs]
    else:
        parts = [str(req.get("input_path") or "")]
    payload = "|".join(parts)
    payload += f"|dur={req.get('duration')}|rate={WAVE_RATE}|astream={req.get('audio_stream', 'a:0')}"
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:16]


def build_wave_decode_args(req: dict, out_path: str) -> list[str]:
    """FFmpeg args that decode the (joined) audio to WAVE_RATE mono s16le PCM.
    For a join the audio of every input is concatenated on the joined timeline."""
    segs = req.get("join_segments") or []
    args = ["-hide_banner", "-loglevel", "error", "-y"]
    if segs:
        for seg in segs:
            if seg.get("has_audio", True):
                args += ["-i", str(seg.get("path"))]
            else:
                # A segment with no audio stream makes [i:a:0] match nothing, and
                # ffmpeg then refuses to build the WHOLE filtergraph — one silent
                # segment used to kill the waveform for the entire join (D07).
                # Feed silence of the same length so the concat arity still holds.
                seg_dur = max(0.001, float(seg.get("duration") or 0.0))
                args += ["-f", "lavfi", "-t", f"{seg_dur:.3f}",
                         "-i", f"anullsrc=channel_layout=mono:sample_rate={WAVE_RATE}"]
        filt = [
            f"[{i}:a:0]aformat=channel_layouts=mono,aresample={WAVE_RATE},asetpts=PTS-STARTPTS[a{i}]"
            for i in range(len(segs))
        ]
        filt.append("".join(f"[a{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=0:a=1[mix]")
        args += ["-filter_complex", ";".join(filt), "-map", "[mix]"]
    else:
        args += [
            "-i", str(req.get("input_path") or ""),
            "-filter_complex", f"[0:a:0]aformat=channel_layouts=mono,aresample={WAVE_RATE}[mix]",
            "-map", "[mix]",
        ]
    args += ["-f", "s16le", "-acodec", "pcm_s16le", out_path]
    return args


def decode_pcm_samples(data: bytes):
    """int16 samples of raw little-endian PCM `data`.

    Returns a numpy array when numpy is installed (fast path) and the stdlib
    `array('h')` otherwise. numpy is an ACCELERATOR here, not a requirement:
    it is not a declared FFmWiz dependency, and without this fallback the whole
    QML waveform degraded to a permanent "decoding waveform…" (D08)."""
    if not data:
        return None
    try:
        import numpy as np
        pcm = np.frombuffer(data[: len(data) - (len(data) % 2)], dtype=np.int16)
    except Exception:
        pcm = gui_geometry.pcm_samples(data)
    return pcm if len(pcm) else None


def build_wave_envelope(pcm, step: int = WAVE_ENV_STEP):
    """Decimated (min, max) envelope of the int16 PCM for fast zoomed-out views.
    Returns (env_min, env_max) as numpy arrays or stdlib arrays, or (None, None)
    when the clip is too short for an envelope to help."""
    if pcm is None or len(pcm) == 0:
        return None, None
    m = len(pcm) // step
    if m < 2:
        return None, None
    try:
        import numpy as np
        if isinstance(pcm, np.ndarray):
            block = pcm[: m * step].reshape(m, step)
            return block.min(axis=1), block.max(axis=1)
    except Exception:
        pass
    env_min = array.array("h")
    env_max = array.array("h")
    for i in range(m):
        chunk = pcm[i * step:(i + 1) * step]
        env_min.append(min(chunk))
        env_max.append(max(chunk))
    return env_min, env_max


def waveform_window(pcm, env_min, env_max, rate, start, end, width,
                    env_step: int = WAVE_ENV_STEP):
    """Return up to `width` [min, max] amplitude pairs (each in -1..1) covering
    the time window [start, end]. Zoomed-in windows read raw PCM for full detail;
    zoomed-out windows read the decimated envelope. Pure function (no Qt) so it
    is unit-testable with a synthetic PCM array. Works with or without numpy."""
    if pcm is None or rate <= 0 or width <= 0:
        return []
    total = int(len(pcm))
    if total <= 0:
        return []
    s0 = max(0, min(total, int(float(start) * rate)))
    s1 = max(s0 + 1, min(total, int(float(end) * rate)))
    nwin = s1 - s0
    if nwin <= 0:
        return []
    width = int(min(width, WAVE_MAX_BUCKETS))
    fs = 32768.0
    use_env = (env_min is not None and env_max is not None and (nwin / max(1, width)) > env_step)
    if use_env:
        e0 = max(0, s0 // env_step)
        e1 = max(e0 + 1, min(len(env_min), s1 // env_step))
        src_min = env_min[e0:e1]
        src_max = env_max[e0:e1]
    else:
        seg = pcm[s0:s1]
        src_min = seg
        src_max = seg
    m = len(src_min)
    if m <= 0:
        return []
    buckets = int(min(width, m))
    try:
        import numpy as np
        if isinstance(src_min, np.ndarray):
            starts = ((np.arange(buckets, dtype=np.int64) * m) // buckets)
            mins = np.minimum.reduceat(src_min, starts)
            maxs = np.maximum.reduceat(src_max, starts)
            return [[float(mins[i]) / fs, float(maxs[i]) / fs] for i in range(int(mins.size))]
    except Exception:
        pass
    out = []
    for i in range(buckets):
        a = (i * m) // buckets
        b = max(a + 1, ((i + 1) * m) // buckets)
        out.append([min(src_min[a:b]) / fs, max(src_max[a:b]) / fs])
    return out


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
        import gui_common  # type: ignore
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

    class Bridge(QObject):
        """Exposes the request to QML and collects the editor result."""

        # Emitted (queued) from the decode thread when the waveform is ready:
        # (cacheKey, overviewJson) where overviewJson is a JSON array of
        # [min, max] pairs (each in -1..1) spanning the whole timeline. Per-
        # viewport detail is fetched on demand via waveformWindow().
        waveformReady = Signal(str, str)
        # Emitted when a reversed preview proxy has finished rendering:
        # (generation, output_path). output_path is "" on failure.
        reverseReady = Signal(int, str)

        def __init__(self, app: QGuiApplication, req: dict) -> None:
            super().__init__()
            self._app = app
            self._req = req
            self._submitted = False
            self._request_json = json.dumps(req, ensure_ascii=False)
            self._palette_json = json.dumps(_PALETTE, ensure_ascii=False)
            self._wave_thread: threading.Thread | None = None
            self._wave_pending: str | None = None
            # One owned reverse-proxy process (superseded renders are killed) and
            # one temp dir holding every proxy, so nothing leaks into %TEMP% when
            # the editor quits mid-render (NEW-GUI2/NEW-GUI3).
            self._rev_lock = threading.Lock()
            self._rev_proc: subprocess.Popen | None = None
            self._rev_temp = tempfile.TemporaryDirectory(prefix="ffmwiz_qmlrev_")
            self._rev_files: list[str] = []
            # Cached waveform source (decoded once per cache key; reused after).
            self._wave_key: str | None = None
            self._pcm = None          # numpy int16 mono PCM at WAVE_RATE
            self._env_min = None      # decimated min envelope
            self._env_max = None      # decimated max envelope
            self._wave_rate = 0

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
            """Decode the (joined) audio to WAVE_RATE mono PCM once (background
            thread), build a min/max envelope, and emit waveformReady with a
            full-timeline overview. Re-decoding is skipped when the cache key is
            unchanged, so this does NOT run again on playback ticks/seeks."""
            if os.environ.get("FFMWIZ_QML_SELFTEST") == "1":
                self.waveformReady.emit("", "[]")
                return
            segs = self._req.get("join_segments") or []
            # A join whose FIRST input is silent still has a waveform to draw:
            # the global has_audio flag is derived from input 0 alone (D07).
            if not self._req.get("has_audio") and not any(s.get("has_audio") for s in segs):
                self.waveformReady.emit("", "[]")
                return
            key = compute_wave_key(self._req)
            if self._wave_key == key and self._pcm is not None:
                self.waveformReady.emit(key, json.dumps(self._overview()))
                return
            if self._wave_thread is not None and self._wave_thread.is_alive():
                # Never return silently: the caller has no timeout and QML would
                # sit on "decoding waveform…" forever (NEW-GUI6).
                self._wave_pending = key
                return
            self._wave_thread = threading.Thread(target=self._decode_waveform, args=(key,), daemon=True)
            self._wave_thread.start()

        def _decode_waveform(self, key: str) -> None:
            try:
                self._load_pcm(key)
                payload = json.dumps(self._overview())
            except Exception as exc:  # noqa: BLE001
                _log("DEBUG", f"Waveform decode failed: {exc}")
                payload = "[]"
            self.waveformReady.emit(key, payload)
            # Serve whoever asked while this decode was running (NEW-GUI6). The
            # cache key is derived from the immutable request, so it matches.
            if self._wave_pending is not None:
                self._wave_pending = None
                self.waveformReady.emit(key, payload)

        def _load_pcm(self, key: str) -> None:
            ffmpeg = str(self._req.get("ffmpeg") or "ffmpeg")
            fd, pcm_path = tempfile.mkstemp(suffix=".pcm", prefix="ffmwiz_qmlwave_")
            os.close(fd)
            try:
                args = build_wave_decode_args(self._req, pcm_path)
                proc = subprocess.run([ffmpeg, *args], check=False,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if proc.returncode != 0:
                    # Silent failure here left the canvas stuck forever (D07).
                    detail = (proc.stderr or b"").decode("utf-8", "replace").strip()[-400:]
                    _log("WARNING", f"Waveform decode failed (rc={proc.returncode}): {detail}")
                data = Path(pcm_path).read_bytes()
            finally:
                try:
                    os.remove(pcm_path)
                except OSError:
                    pass
            pcm = decode_pcm_samples(data)
            self._pcm = pcm
            self._wave_rate = WAVE_RATE
            self._env_min, self._env_max = build_wave_envelope(pcm)
            self._wave_key = key

        def _overview(self) -> list:
            dur = float(self._req.get("duration") or 0.0)
            if self._pcm is None or self._wave_rate <= 0:
                return []
            if dur <= 0:
                dur = len(self._pcm) / float(self._wave_rate)
            return waveform_window(self._pcm, self._env_min, self._env_max,
                                   self._wave_rate, 0.0, dur, 1600)

        @Slot(result=str)
        def waveformKey(self) -> str:  # noqa: N802 (QML camelCase)
            return self._wave_key or ""

        @Slot(float, float, int, result=str)
        def waveformWindow(self, start: float, end: float, width: int) -> str:  # noqa: N802
            """Return JSON [[min,max],...] for the viewport [start,end] at the
            given pixel width. Fast (reads cached PCM/envelope); called only when
            the viewport (zoom/pan) or canvas width changes, never per tick."""
            try:
                pairs = waveform_window(self._pcm, self._env_min, self._env_max,
                                        self._wave_rate, start, end, int(width))
                return json.dumps(pairs)
            except Exception as exc:  # noqa: BLE001
                _log("DEBUG", f"waveformWindow failed: {exc}")
                return "[]"

        def finalize_if_unsubmitted(self) -> None:
            if not self._submitted:
                write_reply({"status": "canceled"})

        # --- Live reverse preview: render a reversed proxy chunk on demand ---
        @Slot(str)
        def renderReverse(self, spec_json: str) -> None:  # noqa: N802 (QML camelCase)
            try:
                spec = json.loads(spec_json)
            except Exception as exc:
                _log("DEBUG", f"renderReverse bad spec: {exc}")
                return
            # The previous chunk's result is already superseded and each render
            # holds an ffmpeg process plus a full window of frames, so stop it
            # before starting another (NEW-GUI2).
            self.cancelReverse()
            threading.Thread(target=self._do_reverse, args=(spec,), daemon=True).start()

        @Slot()
        def cancelReverse(self) -> None:  # noqa: N802 (QML camelCase)
            """Kill the reverse proxy still rendering, if any."""
            with self._rev_lock:
                proc, self._rev_proc = self._rev_proc, None
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass

        def _do_reverse(self, spec: dict) -> None:
            gen = int(spec.get("gen", 0))
            out = ""
            try:
                ffmpeg = str(self._req.get("ffmpeg") or "ffmpeg")
                src = str(spec.get("src") or self._req.get("input_path") or "")
                ss = max(0.0, float(spec.get("ss", 0.0)))
                dur = max(0.05, float(spec.get("dur", 1.0)))
                width = int(spec.get("width", 854))
                # Proxies live in one owned temp dir so nothing survives the
                # editor, even a render still in flight at quit (NEW-GUI3).
                out = str(Path(self._rev_temp.name) / f"rev_{gen}.mp4")
                args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}", "-i", src,
                        "-vf", build_reverse_proxy_vf(width)]
                if reverse_proxy_wants_audio(self._req, spec):
                    args += ["-af", "areverse"]
                else:
                    args += ["-an"]
                args += ["-preset", "ultrafast", "-pix_fmt", "yuv420p", out]
                proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                with self._rev_lock:
                    self._rev_proc = proc
                _, err = proc.communicate()
                with self._rev_lock:
                    if self._rev_proc is proc:
                        self._rev_proc = None
                # mkstemp/Popen create the file up front, so "the path exists" is
                # not proof of success: a failed ffmpeg used to hand QML a
                # nonempty path to a 0-byte mp4 (D17).
                ok = proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0
                if not ok:
                    detail = (err or b"").decode("utf-8", "replace").strip()[-400:]
                    _log("DEBUG", f"reverse proxy failed rc={proc.returncode}: {detail}")
                    self._discard_proxy(out)
                    self.reverseReady.emit(gen, "")
                    return
                stale = []
                with self._rev_lock:
                    self._rev_files.append(out)
                    # Keep only the few most recent proxies on disk.
                    while len(self._rev_files) > 4:
                        stale.append(self._rev_files.pop(0))
                for path in stale:
                    self._discard_proxy(path)
                self.reverseReady.emit(gen, out)
            except Exception as exc:  # noqa: BLE001
                _log("DEBUG", f"reverse proxy failed: {exc}")
                self._discard_proxy(out)
                self.reverseReady.emit(gen, "")

        @staticmethod
        def _discard_proxy(path: str) -> None:
            if not path:
                return
            try:
                os.remove(path)
            except OSError:
                pass

        def cleanup_reverse(self) -> None:
            self.cancelReverse()
            with self._rev_lock:
                self._rev_files = []
            try:
                self._rev_temp.cleanup()
            except Exception:  # noqa: BLE001 - Windows can still hold a lock
                pass

    app = QGuiApplication(sys.argv)
    app.setApplicationName("FFmWiz")
    app.setApplicationDisplayName("FFmWiz Unified Video Editor")
    # Reuse the classic engine's identity wiring instead of a second, weaker
    # copy: AppUserModelID + the multi-resolution QIcon, so Windows stops
    # grouping this window under the host python.exe (USER-2-2), and the same
    # parent watchdog so the editor dies with the FFmWiz CLI that launched it
    # instead of outliving it with its ffmpeg proxies (D15).
    try:
        import gui_common  # type: ignore
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
        import gui_common  # type: ignore
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
        _log("INFO", "QML self-test passed (root window created).")
        return 0

    rc = app.exec()
    bridge.finalize_if_unsubmitted()
    bridge.cleanup_reverse()
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
