"""Preview transport for the classic speed / reverse editor.

Split out of `gui_editor_speed.py`, which had grown past 1200 lines as one
builder function. Everything here answers a single question: where is the
player, and how does it get somewhere else. The position slider always reports
SOURCE time, so a seek is a mapping rather than a passthrough, and the guards
below exist because Qt emits a burst of stale audio and one stale frame every
time the player is repositioned.

Defined inside a factory rather than at module level so PySide6 stays
importable-on-demand -- most CI jobs install no Qt at all. The class is mixed
into `SpeedEditorWindow` ahead of QMainWindow, so `self` is the window and
every attribute read here is created in its `__init__`.

Unlike the modules listed in `ffmwiz_gui._MODULES`, this one is imported by its
parent instead of being merged into the assembled namespace, so it never
receives the injection and names its shared imports explicitly.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.gui_common import _import_qt, _import_qt_multimedia
from ffmwiz.gui.gui_geometry import seconds_to_timecode


def build_speed_transport_mixin():
    """Define and return the transport mixin; PySide6 is imported here."""
    QtCore, _QtGui, QtWidgets, _ = _import_qt()
    QtMultimedia = _import_qt_multimedia()
    QStyle = QtWidgets.QStyle

    class SpeedTransportMixin:
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

    return SpeedTransportMixin
