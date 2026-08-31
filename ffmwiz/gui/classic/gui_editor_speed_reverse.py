"""Faked reverse playback for the classic speed / reverse editor.

QMediaPlayer cannot play backwards, so every line here exists to pretend that
it can. Video walks the playhead backwards on a timer and shows the frames it
lands on; audio -- and any seek taken while reverse is on -- gets a short
ffmpeg-reversed proxy clip rendered on demand. The proxy has its own timeline,
which is why the source <-> preview conversion below has to exist at all.

Factory-built mixin, and explicit imports, for the reasons given in
`gui_editor_speed_transport.py`.

`_ffmpeg_float` and `_gui_atempo_chain` build the filter arguments for that
proxy. They live here rather than in the parent because the dependency runs
that way round; `gui_editor_speed` re-exports both so `gui_editor_unified`
still reaches them through the assembled namespace.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.gui_common import _import_qt, _import_qt_multimedia
from ffmwiz.gui.gui_geometry import seconds_to_timecode


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


def build_speed_reverse_mixin():
    """Define and return the reverse-playback mixin; PySide6 is imported here."""
    QtCore, _QtGui, _QtWidgets, _ = _import_qt()
    QtMultimedia = _import_qt_multimedia()
    QUrl = QtCore.QUrl

    class SpeedReverseMixin:
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

    return SpeedReverseMixin
