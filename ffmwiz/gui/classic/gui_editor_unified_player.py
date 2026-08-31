"""Playback for the classic unified video editor.

Split out of `gui_editor_unified.py`: the QtMultimedia player, the join
segment routing behind it, frame-accurate preview extraction, seeking, volume
and the reverse-proxy render. A pure code move; the methods are unchanged.

The mixin is defined inside a factory rather than at module level because its
body binds Qt symbols, and PySide6 must stay importable-on-demand: most CI jobs
install no Qt at all.

Not in `ffmwiz_gui._MODULES`, so the assembled namespace is never injected
here: the shared helpers this file uses are imported by name instead.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.gui_common import (_gui_log_debug, _import_qt,
                                   _import_qt_multimedia)
from ffmwiz.gui.gui_geometry import (reverse_chunk_spec, seconds_to_hmsf,
                                     seconds_to_timecode)


def build_unified_editor_player_mixin(FrameExtractWorker, active_segment_has_audio,
                                      _gui_atempo_chain, _ffmpeg_float):
    """Return the mixin that drives the editor window's media player.

    The three helpers arrive as arguments rather than as imports: they are
    reached through the assembled namespace, which only `gui_editor_unified`
    itself receives (`active_segment_has_audio` is defined there, and the two
    ffmpeg filter helpers belong to the speed editor, which re-exports them
    for exactly this caller).
    """
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    QStyle = QtWidgets.QStyle
    # PERF: QtMultimedia pulls in the native multimedia backend (Qt6Multimedia +
    # the bundled FFmpeg backend DLLs), which is the single most expensive cold
    # load on first launch. Defer it so it does NOT block building/showing the
    # window; it is imported lazily inside _setup_player (which itself runs ~120ms
    # AFTER the window is shown). Every QtMultimedia use lives in player callbacks
    # that only run once _setup_player has created self.player, so this is safe.
    QtMultimedia = None

    class UnifiedEditorPlayerMixin:
        # ---- live reverse preview (preview-only; export uses the real reverse filter) ----
        REV_WINDOW = 15.0     # seconds of source reversed per proxy chunk

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
            speed = self._speed()
            self._rev_gen += 1
            gen = self._rev_gen
            out = Path(self._wave_temp.name) / f"rev_proxy_{gen}.mp4"
            if self.join_segments:
                # Clip the window to ONE segment instead of clamping the offset
                # to zero: a window straddling a join boundary was sourced
                # entirely from the END segment, skipping the tail of the
                # previous one and showing material past the window (D16).
                # Shortening it makes the next chunk resume at the boundary.
                segments = [(float(seg["start"]), float(seg["duration"]))
                            for seg in self.join_segments]
                seg_index, ss, chunk, win_start = reverse_chunk_spec(segments, win_start, win_end)
                chunk = max(0.05, chunk)
                src = self._segment_path(seg_index)
            else:
                seg_index = 0
                src = self.input_path
                ss = win_start
                chunk = win_end - win_start
            tw = self._rev_target_width()
            vf = f"scale={tw}:-2,reverse,setpts=(PTS-STARTPTS)/{_ffmpeg_float(speed)}"
            # The active segment's own flag: a silent input 1 muted the reverse
            # preview of every later audible clip, and vice versa (R02).
            want_audio = active_segment_has_audio(self.request, self.join_segments, seg_index)
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

    return UnifiedEditorPlayerMixin
