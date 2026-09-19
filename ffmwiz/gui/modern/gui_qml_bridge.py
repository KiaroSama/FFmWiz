"""The QML editor's Bridge object, built against injected Qt symbols.

A factory rather than a module-level class for the same reason the classic
editor uses factories: `QObject`, `Slot`, `Signal` and `Property` only exist
once PySide6 has imported successfully, and this module has to stay importable
without it. Splitting it out of the driver also makes the child-process
lifecycle testable -- while the class lived inside `main()` nothing could
construct it, so the ownership races of F08 could only be argued about.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from ffmwiz.gui.gui_child_owner import ChildProcessOwner  # type: ignore
from ffmwiz.gui.gui_worker_artifacts import WorkerTemporaryDirectory
from ffmwiz.gui.gui_style import PALETTE as _PALETTE  # type: ignore
from ffmwiz.gui.modern.gui_qml_waveform import (build_wave_decode_args,
                                                build_wave_envelope,
                                                compute_wave_key,
                                                decode_pcm_samples,
                                                waveform_window, WAVE_RATE)


def build_bridge(QObject, Slot, Signal, Property, write_reply, log,
                 reverse_proxy_wants_audio, build_reverse_proxy_vf):
    """Return the Bridge class bound to this run's Qt symbols and callbacks."""
    _log = log

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
            # ONE owner for every child either worker starts -- the reverse
            # proxy AND the waveform decode. Both spawn on a background thread,
            # so registering after Popen returned left a window in which cancel
            # or shutdown saw nothing, deleted the temp dir and let an unowned
            # ffmpeg run on. The owner spawns and registers under one lock, so
            # that window does not exist (NEW-GUI2/NEW-GUI3).
            # TWO lanes, deliberately. One owner meant one generation counter
            # and one cancel: starting a reverse render advanced the counter the
            # WAVEFORM decode was registered under and killed its child, and a
            # public cancelReverse() could not stop reverse work without also
            # stopping the decode. They are separate jobs with separate
            # lifetimes, so they get separate owners (R04).
            self._reverse_children = ChildProcessOwner()
            self._wave_children = ChildProcessOwner()
            self._rev_lock = threading.Lock()
            self._rev_temp = WorkerTemporaryDirectory(
                (self._reverse_children, self._wave_children), _log)
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
            if self._wave_children.closed:
                return
            if self._wave_thread is not None and self._wave_thread.is_alive():
                # Never return silently: the caller has no timeout and QML would
                # sit on "decoding waveform…" forever (NEW-GUI6).
                self._wave_pending = key
                return
            self._wave_children.worker_started()
            try:
                self._wave_thread = threading.Thread(target=self._decode_waveform, args=(key,), daemon=True)
                self._wave_thread.start()
            except Exception as exc:
                self._wave_children.worker_finished()
                self._wave_thread = None
                _log("ERROR", f"Could not start waveform worker: {exc}")
                if not self._wave_children.closed:
                    self.waveformReady.emit(key, "[]")

        def _decode_waveform(self, key: str) -> None:
            try:
                try:
                    self._load_pcm(key)
                    payload = json.dumps(self._overview())
                except Exception as exc:  # noqa: BLE001
                    _log("DEBUG", f"Waveform decode failed: {exc}")
                    payload = "[]"
                if self._wave_children.closed:
                    # The window is going away. Publishing here would touch a
                    # half-torn-down QML scene for a result nobody will draw.
                    return
                self.waveformReady.emit(key, payload)
                # Serve whoever asked while this decode was running (NEW-GUI6).
                # The cache key comes from the immutable request, so it matches.
                if self._wave_pending is not None:
                    self._wave_pending = None
                    self.waveformReady.emit(key, payload)
            finally:
                # In `finally`, so a failed decode or a cancelled one still
                # releases the worker that cleanup_reverse waits on.
                self._wave_children.worker_finished()

        def _load_pcm(self, key: str) -> None:
            ffmpeg = str(self._req.get("ffmpeg") or "ffmpeg")
            fd, pcm_path = tempfile.mkstemp(suffix=".pcm", prefix="ffmwiz_qmlwave_")
            os.close(fd)
            # Whether this function may delete the PCM at the end. A refused
            # stop transfers that decision to the owner (A03).
            stopped_cleanly = True
            proc = None
            try:
                args = build_wave_decode_args(self._req, pcm_path)
                # Popen through the owner, not subprocess.run: run() holds the
                # child in a local nobody else can reach, so closing the window
                # mid-decode left an ffmpeg running and a PCM file behind.
                proc = self._wave_children.start([ffmpeg, *args],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if proc is None:
                    return          # closed before this decode could start
                try:
                    _, stderr = proc.communicate()
                except BaseException:
                    # Communication failed, so nothing is known about the child.
                    # Stop it BEFORE releasing anything -- and READ the answer:
                    # the stop can be refused, and then the decoder is still
                    # writing this PCM. Deleting it in the `finally` below was
                    # the same mistake one level down (A03).
                    if not self._wave_children.stop(proc):
                        stopped_cleanly = False
                        self._wave_children.retain_artifact(
                            pcm_path, "the waveform decoder could not be stopped")
                        _log("WARNING",
                             f"Waveform decode could not be stopped; keeping {pcm_path} "
                             "for a later retry rather than deleting a file it may "
                             "still be writing")
                    raise
                else:
                    self._wave_children.finish(proc)
                if proc.returncode != 0:
                    # A nonzero decoder exit means the PCM on disk is whatever
                    # FFmpeg managed before it died. Reading it anyway promoted
                    # a truncated 4000-sample fragment into the successful cache
                    # and the editor drew it as the whole clip (R04). Fail
                    # loudly instead, leaving the cache untouched so a retry is
                    # still possible.
                    detail = (stderr or b"").decode("utf-8", "replace").strip()[-400:]
                    _log("WARNING", f"Waveform decode failed (rc={proc.returncode}): {detail}")
                    raise RuntimeError(f"waveform decode failed (rc={proc.returncode})")
                if self._wave_children.closed:
                    raise RuntimeError("waveform decode cancelled by shutdown")
                data = Path(pcm_path).read_bytes()
            finally:
                # `finally`, and after the child is reaped: on Windows the file
                # stays locked while ffmpeg holds it, so removing it earlier
                # silently failed and left the PCM behind. Skipped entirely when
                # the stop was refused -- the owner holds that file now, and it
                # sweeps it when nothing is writing any more.
                if proc is not None and not self._wave_children.finish(proc):
                    stopped_cleanly = False
                    self._wave_children.retain_artifact(pcm_path, "waveform writer has not exited")
                if stopped_cleanly:
                    try:
                        os.remove(pcm_path)
                    except OSError as exc:
                        self._wave_children.retain_artifact(pcm_path, str(exc))
                        _log("WARNING", f"Keeping waveform PCM for cleanup retry {pcm_path}: {exc}")
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
            if self._reverse_children.closed:
                return
            # The previous chunk's result is already superseded and each render
            # holds an ffmpeg process plus a full window of frames, so stop it
            # before starting another (NEW-GUI2). Claiming the generation here,
            # on the GUI thread, is what makes a render started earlier but not
            # yet spawned refuse to spawn at all.
            #
            # SUPERSEDE, which is not the same as CANCEL: this stops the older
            # generations and spares the one being started. `cancelReverse()` is
            # the public "stop reverse work" slot and must stop the current one
            # too -- it used to call cancel(keep_generation=current), so a
            # direct QML cancel spared the very process it was asked to stop
            # (R04).
            generation = int(spec.get("gen", 0))
            self._reverse_children.bump_generation(generation)
            self._reverse_children.cancel(keep_generation=generation)
            self._reverse_children.worker_started()
            try:
                threading.Thread(target=self._do_reverse, args=(spec,), daemon=True).start()
            except Exception as exc:
                self._reverse_children.worker_finished()
                _log("ERROR", f"Could not start reverse worker: {exc}")
                if not self._reverse_children.closed:
                    self.reverseReady.emit(generation, "")

        @Slot()
        def cancelReverse(self) -> None:  # noqa: N802 (QML camelCase)
            """Stop EVERY reverse proxy, including the current generation.

            Called from QML when the user leaves reverse mode or seeks away, so
            "stop" has to mean stop. Waveform decoding is a different lane and
            is deliberately untouched.
            """
            self._reverse_children.bump_generation()
            self._reverse_children.cancel()

        def _do_reverse(self, spec: dict) -> None:
            try:
                self._render_reverse(spec)
            finally:
                self._reverse_children.worker_finished()

        def _render_reverse(self, spec: dict) -> None:
            gen = int(spec.get("gen", 0))
            out = ""
            proc = None
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
                # Spawn and register together. Doing it in two steps left a gap
                # in which cancelReverse/cleanup saw no child, returned "clean",
                # and the process it never saw outlived the window.
                proc = self._reverse_children.start(args, generation=gen,
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.PIPE,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if proc is None:
                    # Superseded or shut down before it could start.
                    self._discard_proxy(out, proc)
                    if not self._reverse_children.closed:
                        self.reverseReady.emit(gen, "")
                    return
                try:
                    _, err = proc.communicate()
                except BaseException:
                    self._reverse_children.stop(proc)      # same contract (A03)
                    raise
                else:
                    self._reverse_children.finish(proc)
                if self._reverse_children.closed or not self._reverse_children.is_current(gen):
                    # A stale or post-shutdown result must never be published.
                    self._discard_proxy(out, proc)
                    return
                # mkstemp/Popen create the file up front, so "the path exists" is
                # not proof of success: a failed ffmpeg used to hand QML a
                # nonempty path to a 0-byte mp4 (D17).
                ok = proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0
                if not ok:
                    detail = (err or b"").decode("utf-8", "replace").strip()[-400:]
                    _log("DEBUG", f"reverse proxy failed rc={proc.returncode}: {detail}")
                    self._discard_proxy(out, proc)
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
                self._discard_proxy(out, proc)
                if not self._reverse_children.closed:
                    self.reverseReady.emit(gen, "")

        def _discard_proxy(self, path: str, proc=None) -> None:
            if not path:
                return
            # Communication failure is not an exit. Use the actual writer's
            # state on every error/stale path, including a stop that was denied.
            if proc is not None and not self._reverse_children.finish(proc):
                self._reverse_children.retain_artifact(path, "reverse writer has not exited")
                _log("WARNING", f"Keeping reverse proxy with active writer: {path}")
                return
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                self._reverse_children.retain_artifact(path, str(exc))
                _log("WARNING", f"Keeping reverse proxy for cleanup retry {path}: {exc}")

        def cleanup_reverse(self) -> bool:
            """Shut every owned child and worker down, then delete what they
            wrote. Returns False when shutdown or removal did not complete.

            Idempotent and bounded, and it reports what it could not remove
            instead of swallowing the failure."""
            reverse_done = self._reverse_children.close()
            wave_done = self._wave_children.close()
            with self._rev_lock:
                self._rev_files = []
            if not (reverse_done and wave_done):
                # A worker is still running and still owns what it is writing.
                # Deleting its directory now is the race this guard exists for;
                # the temp directory is reported instead of silently removed.
                # The diagnostics travel with the verdict: "incomplete" without
                # the reason leaves the next reader guessing which child or
                # which file is still held (A03).
                detail = []
                for owner, name in ((self._reverse_children, "reverse"),
                                    (self._wave_children, "waveform")):
                    detail.extend(f"{name}: {why}" for why in owner.unreaped())
                    detail.extend(f"{name} kept: {why}" for why in owner.retained_reasons())
                _log("WARNING",
                     f"Shutdown incomplete (reverse={reverse_done}, waveform={wave_done}); "
                     f"leaving {self._rev_temp.name} in place rather than deleting files "
                     f"a worker still owns."
                     + (" " + "; ".join(detail) if detail else ""))
                return False
            # The children are reaped by now, so the handles Windows kept on
            # the proxies are gone; a short bounded retry still covers an
            # antivirus scan holding one for a moment.
            for attempt in range(5):
                try:
                    self._rev_temp.cleanup()
                    return True
                except FileNotFoundError:
                    return True
                except OSError as exc:     # Windows can still hold a lock
                    if attempt == 4:
                        _log("WARNING", f"Could not remove {self._rev_temp.name}: {exc}")
                        return False
                    time.sleep(0.2)
            return False
    return Bridge


__all__ = ["build_bridge"]
