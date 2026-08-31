from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.classic.gui_editor_crop_canvas import build_crop_canvas_widgets
from ffmwiz.gui.classic.gui_editor_crop_layout import build_crop_editor_layout


def build_crop_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, QtMultimedia = _import_qt()
    Qt = QtCore.Qt
    QPointF = QtCore.QPointF
    QTimer = QtCore.QTimer
    QUrl = QtCore.QUrl

    QCursor = QtGui.QCursor
    QShortcut = QtGui.QShortcut
    QKeySequence = QtGui.QKeySequence

    QApplication = QtWidgets.QApplication
    QStyle = QtWidgets.QStyle
    QMainWindow = QtWidgets.QMainWindow
    QPushButton = QtWidgets.QPushButton

    CropCanvas, FrameExtractWorker = build_crop_canvas_widgets()

    class CropEditorWindow(QMainWindow):
        def __init__(self, request):
            init_start = time.perf_counter()
            super().__init__()
            self.request = request
            self.input_path = Path(request["input_path"])
            requested_fps = request.get("fps")
            self.fps = float(requested_fps or 25.0)
            if not requested_fps:
                print("Crop Editor FPS fallback: using 25.000 fps.", file=sys.stderr)
            self.duration = float(request.get("duration") or 0.0)
            self.source_w = int(request.get("source_w") or 1920)
            self.source_h = int(request.get("source_h") or 1080)
            self.ffmpeg = request.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"
            self.result = {"status": "canceled", "margins": [0, 0, 0, 0]}
            self._worker = None
            self._pending_request = None
            self._frame_request_started = 0.0
            self._syncing_zoom_control = False
            self._zoom_editor_active = False
            self._zoom_select_all_pending = False
            self._zoom_presets = (10, 15, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 800, 1200, 1600, 3200)

            # CRITICAL: state initialized BEFORE _build_ui() so it can read
            # self._timestamp during widget construction (previous _timestamp
            # AttributeError fix).
            self._timestamp = min(30.0, max(0.0, self.duration * 0.25))
            self._drag_snapshot = None
            self._history = HistoryStack(CropSnapshot(margins=(0, 0, 0, 0)), max_size=100)

            self.setWindowTitle("FFmWiz Crop Editor")
            self._icon = _icon_loader(self, self.style())
            _apply_window_icon(self, self._icon)
            self.setMinimumSize(1080, 720)
            self.resize(1280, 820)

            build_crop_editor_layout(self, CropCanvas)
            self._install_shortcuts()
            _gui_log_debug(
                f"Crop GUI widgets initialized in {time.perf_counter() - init_start:.3f}s",
                force=True,
            )
            QtCore.QTimer.singleShot(150, self._deferred_media_startup)

        def _deferred_media_startup(self):
            start = time.perf_counter()
            self._setup_player()
            self._seek(self._timestamp)
            _gui_log_debug(
                f"Crop GUI media startup completed in {time.perf_counter() - start:.3f}s",
                force=True,
            )

        def _take_snapshot(self):
            t, l, r, b = self.canvas.margins
            return CropSnapshot(margins=(int(t), int(l), int(r), int(b)))

        def _commit_history(self):
            self._history.push(self._take_snapshot())
            self._update_undo_redo_state()

        def _restore_snapshot(self, snap):
            self.canvas.set_margins(list(snap.margins))
            self._refresh_info()

        def _undo(self):
            snap = self._history.undo()
            if snap is not None:
                self._restore_snapshot(snap)
                self._update_undo_redo_state()

        def _redo(self):
            snap = self._history.redo()
            if snap is not None:
                self._restore_snapshot(snap)
                self._update_undo_redo_state()

        def _update_undo_redo_state(self):
            self.btn_undo.setEnabled(self._history.can_undo())
            self.btn_redo.setEnabled(self._history.can_redo())

        def _btn(self, text, slot):
            b = QPushButton(text)
            b.clicked.connect(slot)
            return b

        def _setup_player(self):
            QtMultimedia = _import_qt_multimedia()
            self._media_player_cls = QtMultimedia.QMediaPlayer
            self.player = QtMultimedia.QMediaPlayer(self)
            self.audio = QtMultimedia.QAudioOutput(self)
            self.audio.setVolume(self.volume_slider.value() / 100.0)
            self.player.setAudioOutput(self.audio)
            self.video_sink = QtMultimedia.QVideoSink(self)
            self.player.setVideoSink(self.video_sink)
            self.video_sink.videoFrameChanged.connect(self._on_video_frame)
            self.player.positionChanged.connect(self._on_player_position)
            self.player.playbackStateChanged.connect(self._on_playback_state_changed)
            self.player.errorOccurred.connect(self._on_player_error)
            self.player.setSource(QUrl.fromLocalFile(str(self.input_path)))
            self.player.pause()
            self._update_volume_icon()

        def eventFilter(self, obj, event):
            canvas = getattr(self, "canvas", None)
            volume_slider = getattr(self, "volume_slider", None)
            time_slider = getattr(self, "time_slider", None)
            if obj is getattr(self, "btn_hand", None) and event.type() == QtCore.QEvent.MouseButtonDblClick:
                self.reset_zoom()
                return True
            zoom_line = self.zoom_percent_combo.lineEdit() if hasattr(self, "zoom_percent_combo") and self.zoom_percent_combo.lineEdit() is not None else None
            if zoom_line is not None and event.type() == QtCore.QEvent.MouseButtonPress:
                try:
                    clicked_zoom_control = obj is zoom_line or obj is self.zoom_percent_combo or self.zoom_percent_combo.isAncestorOf(obj)
                except Exception:
                    clicked_zoom_control = False
                if self._zoom_editor_active and not clicked_zoom_control:
                    self._apply_zoom_percent_text()
                    zoom_line.deselect()
                    zoom_line.clearFocus()
                    self.zoom_percent_combo.clearFocus()
                    self._zoom_editor_active = False
                    if obj is canvas:
                        canvas.setFocus(Qt.MouseFocusReason)
                    else:
                        self.setFocus(Qt.MouseFocusReason)
            if obj is zoom_line:
                if event.type() == QtCore.QEvent.MouseButtonPress:
                    self._zoom_editor_active = True
                    if not zoom_line.hasFocus() or not zoom_line.hasSelectedText():
                        self._zoom_select_all_pending = True
                        QTimer.singleShot(0, self._select_zoom_percent_text_once)
                elif event.type() == QtCore.QEvent.FocusIn:
                    self._zoom_editor_active = True
                    self._zoom_select_all_pending = True
                    QTimer.singleShot(0, self._select_zoom_percent_text_once)
                elif event.type() == QtCore.QEvent.FocusOut:
                    self._apply_zoom_percent_text()
                    zoom_line.deselect()
                    self._zoom_editor_active = False
                    self._zoom_select_all_pending = False
            if obj is canvas and event.type() == QtCore.QEvent.KeyPress:
                if event.nativeVirtualKey() == WIN_VK.get("alt") or (event.modifiers() & Qt.AltModifier):
                    self._update_zoom_tool_icon(True, force=True)
                    return True
            if obj is canvas and event.type() == QtCore.QEvent.KeyRelease:
                if event.nativeVirtualKey() == WIN_VK.get("alt"):
                    self._update_zoom_tool_icon(False, force=True)
                    return True
            if obj is volume_slider and event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y()
                step = 3 if (event.modifiers() & Qt.ControlModifier) else 5
                volume_slider.setValue(max(0, min(100, volume_slider.value() + (step if delta > 0 else -step))))
                return True
            if obj is time_slider and event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y()
                step = 10.0 if (event.modifiers() & Qt.ShiftModifier) else 1.0 if (event.modifiers() & Qt.ControlModifier) else 5.0
                self._seek(self._timestamp + (step if delta > 0 else -step))
                event.accept()
                return True
            if obj is time_slider and event.type() in {
                QtCore.QEvent.MouseButtonPress,
                QtCore.QEvent.MouseMove,
                QtCore.QEvent.MouseButtonRelease,
            }:
                buttons = event.buttons() if hasattr(event, "buttons") else Qt.NoButton
                if event.type() == QtCore.QEvent.MouseButtonPress and getattr(event, "button", lambda: Qt.NoButton)() != Qt.LeftButton:
                    return False
                if event.type() == QtCore.QEvent.MouseMove and not (buttons & Qt.LeftButton):
                    return False
                if event.type() != QtCore.QEvent.MouseButtonRelease or getattr(event, "button", lambda: Qt.LeftButton)() == Qt.LeftButton:
                    x = int(max(0, min(time_slider.width(), event.position().x())))
                    value = QStyle.sliderValueFromPosition(
                        time_slider.minimum(),
                        time_slider.maximum(),
                        x,
                        max(1, time_slider.width()),
                    )
                    self._seek(value / 1000.0)
                    event.accept()
                    return True
            return super().eventFilter(obj, event)

        def _is_playing(self):
            if not hasattr(self, "player"):
                return False
            return self.player.playbackState() == self._media_player_cls.PlayingState

        def toggle_playback(self):
            if not hasattr(self, "player"):
                return
            if self._is_playing():
                self.player.pause()
                self._request_frame()
            else:
                pos_ms = self.player.position()
                dur_ms = max(0, self.player.duration() or int(self.duration * 1000))
                if dur_ms > 0 and pos_ms >= dur_ms - 500:
                    self.player.setPosition(0)
                self.player.play()
            self._on_playback_state_changed(self.player.playbackState())

        def _on_playback_state_changed(self, _state):
            if self._is_playing():
                self.btn_play.setText(" Pause  (Space)")
                self.btn_play.setIcon(self._icon("pause", QStyle.SP_MediaPause))
            else:
                self.btn_play.setText(" Play  (Space)")
                self.btn_play.setIcon(self._icon("play", QStyle.SP_MediaPlay))

        def _on_video_frame(self, frame):
            try:
                image = frame.toImage()
            except Exception:
                image = None
            if image is not None and not image.isNull():
                self.canvas.set_image(image)

        def _on_player_position(self, ms):
            self._timestamp = max(0.0, min(self.duration, ms / 1000.0))
            self.time_slider.blockSignals(True)
            self.time_slider.setValue(int(round(self._timestamp * 1000)))
            self.time_slider.blockSignals(False)
            self.time_label.setText(f"{seconds_to_hmsf(self._timestamp, self.fps)} / {seconds_to_hmsf(self.duration, self.fps)}")
            self._refresh_info()

        def _on_player_error(self, _err, msg):
            if msg:
                self.info_label.setText(f"Player message: {msg}")

        def _on_volume_changed(self, value):
            if not hasattr(self, "audio"):
                return
            self.audio.setVolume(max(0, min(100, value)) / 100.0)
            self.volume_label.setText(f"{value}%")
            self._update_volume_icon()

        def toggle_mute(self):
            if not hasattr(self, "audio"):
                return
            self.audio.setMuted(not self.audio.isMuted())
            self._update_volume_icon()

        def _update_volume_icon(self):
            value = self.volume_slider.value()
            if hasattr(self, "audio") and self.audio.isMuted():
                icon = "volume_meter_muted"
            elif value <= 0:
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

        def _update_zoom_tool_icon(self, zoom_out=None, force=False):
            if zoom_out is None:
                zoom_out = bool(QApplication.keyboardModifiers() & Qt.AltModifier)
            self.canvas.set_zoom_out_mode(bool(zoom_out), force=force)
            self.btn_zoom.setIcon(self._icon("zoom_out" if zoom_out else "zoom_in", None))
            _gui_log_debug(f"Crop zoom Alt mode set to {'out' if zoom_out else 'in'}", force=force)

        def _on_drag_started(self):
            self._drag_snapshot = self._take_snapshot()

        def _on_drag_finished(self):
            if self._drag_snapshot is None:
                return
            current = self._take_snapshot()
            pre = self._drag_snapshot
            self._drag_snapshot = None
            self.canvas.margins = list(pre.margins)
            self._history.push(self._take_snapshot())
            self.canvas.margins = list(current.margins)
            self._history.push(self._take_snapshot())
            self.canvas.update()
            self._refresh_info()
            self._update_undo_redo_state()

        def activate_hand(self):
            self.canvas.set_tool("hand")
            self.btn_hand.setProperty("active", "true")
            self.btn_zoom.setProperty("active", "false")
            self._restyle_tools()

        def activate_zoom(self):
            self.canvas.set_tool("zoom")
            self.btn_hand.setProperty("active", "false")
            self.btn_zoom.setProperty("active", "true")
            self._update_zoom_tool_icon(force=True)
            self._restyle_tools()

        def _restyle_tools(self):
            for w in (self.btn_hand, self.btn_zoom):
                w.style().unpolish(w); w.style().polish(w); w.update()

        def reset_zoom(self):
            self.canvas.zoom = 1.0
            self.canvas._scroll = QPointF(0, 0)
            self.canvas.update()
            self._sync_zoom_percent_control()
            self._refresh_info()

        def _zoom_percent_value(self):
            return int(round(self.canvas.zoom * 100))

        def _sync_zoom_percent_control(self):
            if not hasattr(self, "zoom_percent_combo"):
                return
            value = self._zoom_percent_value()
            text = f"{value}%"
            self._syncing_zoom_control = True
            try:
                self.zoom_percent_combo.blockSignals(True)
                self.zoom_percent_combo.setCurrentText(text)
                self.zoom_percent_combo.blockSignals(False)
            finally:
                self._syncing_zoom_control = False

        def _focus_zoom_percent_editor(self):
            self._zoom_editor_active = True
            self.zoom_percent_combo.setFocus(Qt.MouseFocusReason)
            if self.zoom_percent_combo.lineEdit() is not None:
                self._zoom_select_all_pending = True
                QTimer.singleShot(0, self._select_zoom_percent_text_once)
            self.zoom_percent_combo.showPopup()

        def _select_zoom_percent_text_once(self):
            if not self._zoom_select_all_pending:
                return
            if self.zoom_percent_combo.lineEdit() is not None:
                self.zoom_percent_combo.lineEdit().selectAll()
            self._zoom_select_all_pending = False

        def _apply_zoom_percent_text(self):
            if self._syncing_zoom_control or not hasattr(self, "zoom_percent_combo"):
                return
            text = self.zoom_percent_combo.currentText().strip()
            match = re.search(r"\d+(?:[.,]\d+)?", text)
            if not match:
                self._sync_zoom_percent_control()
                return
            value = float(match.group(0).replace(",", "."))
            value = max(10.0, min(3200.0, value))
            self._set_preview_zoom_percent(value)

        def _set_preview_zoom_percent(self, value):
            p = self._preview_zoom_focus()
            self.canvas._set_zoom_centered(p.x(), p.y(), float(value) / 100.0)
            self._sync_zoom_percent_control()
            self._refresh_info()

        def _on_canvas_zoom_changed(self):
            self._sync_zoom_percent_control()
            self._refresh_info()

        def _preview_zoom_focus(self):
            p = self.canvas.mapFromGlobal(QCursor.pos())
            if self.canvas.rect().contains(p):
                return QPointF(p.x(), p.y())
            return QPointF(self.canvas.width() / 2.0, self.canvas.height() / 2.0)

        def zoom_preview_in(self):
            p = self._preview_zoom_focus()
            self.canvas._zoom_centered(p.x(), p.y(), 1.15)
            self._refresh_info()

        def zoom_preview_out(self):
            p = self._preview_zoom_focus()
            self.canvas._zoom_centered(p.x(), p.y(), 1.0 / 1.15)
            self._refresh_info()

        def reset_crop(self):
            if self.canvas.margins == [0, 0, 0, 0]:
                return
            self.canvas.set_margins([0, 0, 0, 0])
            self._refresh_info()
            self._commit_history()

        def go_home(self):
            self._seek(0.0)

        def go_end(self):
            self._seek(self.duration)

        def _seek_relative(self, dt):
            self._seek(self._timestamp + dt)

        def _nudge_time_slider(self, direction):
            frame_step = 1.0 / max(1.0, self.fps)
            self._seek(self._timestamp + float(direction) * max(frame_step, 0.25))

        def _seek(self, t):
            self._timestamp = max(0.0, min(self.duration, float(t)))
            target_ms = int(round(self._timestamp * 1000))
            if hasattr(self, "player") and abs(self.player.position() - target_ms) > 40:
                self.player.setPosition(target_ms)
            self.time_slider.blockSignals(True)
            self.time_slider.setValue(target_ms)
            self.time_slider.blockSignals(False)
            self.time_label.setText(f"{seconds_to_hmsf(self._timestamp, self.fps)} / {seconds_to_hmsf(self.duration, self.fps)}")
            if not hasattr(self, "player") or not self._is_playing():
                self._request_frame()
            self._refresh_info()

        def _request_frame(self):
            target_w = max(320, self.canvas.width())
            target_h = max(180, self.canvas.height())
            req = (round(self._timestamp * 1000), target_w, target_h)
            self._pending_request = req
            if self._worker is not None and self._worker.isRunning():
                return
            self._launch_worker()

        def _launch_worker(self):
            if not self._pending_request:
                return
            ts_ms, w, h = self._pending_request
            self._pending_request = None
            self._frame_request_started = time.perf_counter()
            self._worker = FrameExtractWorker(self.ffmpeg, self.input_path,
                                              ts_ms / 1000.0, w, h, parent=self)
            self._worker.finished_with_image.connect(self._on_frame_extracted)
            self._worker.finished.connect(self._worker.deleteLater)
            self._worker.start()

        def _on_frame_extracted(self, image):
            if not image.isNull():
                self.canvas.set_image(image)
            if self._frame_request_started:
                _gui_log_debug(
                    f"Crop preview frame loaded in {time.perf_counter() - self._frame_request_started:.3f}s",
                    force=True,
                )
                self._frame_request_started = 0.0
            self._worker = None
            if self._pending_request:
                self._launch_worker()

        def _stop_worker(self):
            self._pending_request = None
            worker = self._worker
            self._worker = None
            if worker is not None and worker.isRunning():
                worker.stop()
                if not worker.wait(3000):
                    worker.terminate()
                    worker.wait(1000)

        def _refresh_info(self):
            t, l, r, b = self.canvas.margins
            self.info_label.setText(
                f"Crop margins:  top={t} px  left={l} px  right={r} px  bottom={b} px      "
                f"Output crop box:  {max(1, self.source_w - l - r)}x{max(1, self.source_h - t - b)}      "
                f"Time:  {seconds_to_hmsf(self._timestamp, self.fps)} / {seconds_to_hmsf(self.duration, self.fps)}      "
                f"Tool:  {'Zoom' if self.canvas.tool == 'zoom' else 'Hand'}      "
                f"Zoom:  {int(round(self.canvas.zoom * 100))}%"
            )

        def _install_shortcuts(self):
            QShortcut(QKeySequence("Space"), self).activated.connect(self.toggle_playback)
            QShortcut(QKeySequence("M"), self).activated.connect(self.toggle_mute)
            QShortcut(QKeySequence("H"), self).activated.connect(self.activate_hand)
            QShortcut(QKeySequence("Z"), self).activated.connect(self.activate_zoom)
            QShortcut(QKeySequence("R"), self).activated.connect(self.reset_crop)
            QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.reset_crop)
            QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.reset_zoom)
            QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.zoom_preview_in)
            QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.zoom_preview_in)
            QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.zoom_preview_out)
            QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._undo)
            QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self._redo)
            QShortcut(QKeySequence("Ctrl+Shift+Z"), self).activated.connect(self._redo)
            QShortcut(QKeySequence(Qt.Key_Home), self).activated.connect(self.go_home)
            QShortcut(QKeySequence(Qt.Key_End), self).activated.connect(self.go_end)
            QShortcut(QKeySequence("Shift+Left"), self).activated.connect(lambda: self._seek_relative(-10.0))
            QShortcut(QKeySequence("Shift+Right"), self).activated.connect(lambda: self._seek_relative(10.0))
            QShortcut(QKeySequence("Return"), self).activated.connect(self._confirm_or_apply_zoom_editor)
            QShortcut(QKeySequence("Enter"), self).activated.connect(self._confirm_or_apply_zoom_editor)
            QShortcut(QKeySequence("Escape"), self).activated.connect(self.cancel)

        def _zoom_editor_has_focus(self):
            if not hasattr(self, "zoom_percent_combo") or self.zoom_percent_combo.lineEdit() is None:
                return False
            focus = QApplication.focusWidget()
            return (
                self._zoom_editor_active
                or focus is self.zoom_percent_combo
                or focus is self.zoom_percent_combo.lineEdit()
                or self.zoom_percent_combo.hasFocus()
                or self.zoom_percent_combo.lineEdit().hasFocus()
            )

        def _confirm_or_apply_zoom_editor(self):
            if self._zoom_editor_has_focus():
                self._apply_zoom_percent_text()
                return
            self.confirm()

        def keyPressEvent(self, event):
            vk = event.nativeVirtualKey()
            mods = event.modifiers()
            ctrl = bool(mods & Qt.ControlModifier)
            shift = bool(mods & Qt.ShiftModifier)
            if (mods & Qt.AltModifier) or vk == WIN_VK.get("alt"):
                self._update_zoom_tool_icon(True, force=True)
                if vk == WIN_VK.get("alt"):
                    event.accept()
                    return
            if ctrl and not shift:
                if vk == WIN_VK["z"]: self._undo(); return
                if vk == WIN_VK["y"]: self._redo(); return
                if vk == WIN_VK["r"]: self.reset_crop(); return
                if vk == WIN_VK["0"]: self.reset_zoom(); return
                if vk in (WIN_VK["plus"], WIN_VK["equal"], WIN_VK["kp_add"]): self.zoom_preview_in(); return
                if vk in (WIN_VK["minus"], WIN_VK["kp_subtract"]): self.zoom_preview_out(); return
            if ctrl and shift and vk == WIN_VK["z"]:
                self._redo(); return
            if shift and not ctrl and vk == WIN_VK["left"]:
                self._seek_relative(-10.0); return
            if shift and not ctrl and vk == WIN_VK["right"]:
                self._seek_relative(10.0); return
            if not ctrl and not shift:
                pan_step = max(8, int(min(self.canvas.width(), self.canvas.height()) * 0.035))
                if vk == WIN_VK["left"]: self.canvas.pan_by(pan_step, 0); return
                if vk == WIN_VK["right"]: self.canvas.pan_by(-pan_step, 0); return
                if vk == WIN_VK["up"]: self.canvas.pan_by(0, pan_step); return
                if vk == WIN_VK["down"]: self.canvas.pan_by(0, -pan_step); return
                actions = {
                    WIN_VK["space"]: self.toggle_playback,
                    WIN_VK["m"]: self.toggle_mute,
                    WIN_VK["h"]: self.activate_hand,
                    WIN_VK["z"]: self.activate_zoom,
                    WIN_VK["r"]: self.reset_crop,
                    WIN_VK["home"]: self.go_home,
                    WIN_VK["end"]: self.go_end,
                    WIN_VK["return"]: self._confirm_or_apply_zoom_editor,
                    WIN_VK["escape"]: self.cancel,
                }
                fn = actions.get(vk)
                if fn is not None:
                    fn(); return
            super().keyPressEvent(event)

        def keyReleaseEvent(self, event):
            vk = event.nativeVirtualKey()
            if vk == WIN_VK.get("alt"):
                self._update_zoom_tool_icon(False, force=True)
                event.accept()
                return
            self._update_zoom_tool_icon(force=True)
            super().keyReleaseEvent(event)

        def confirm(self):
            self.result = {"status": "ok", "margins": snap_crop_margins_even(self.canvas.margins, int(self.source_w), int(self.source_h))}
            try:
                self.player.stop()
            except Exception:
                pass
            self._stop_worker()
            self.close()

        def cancel(self):
            self.result = {"status": "canceled", "margins": [0, 0, 0, 0]}
            try:
                self.player.stop()
            except Exception:
                pass
            self._stop_worker()
            self.close()

        def closeEvent(self, event):
            try:
                self.player.stop()
            except Exception:
                pass
            self._stop_worker()
            super().closeEvent(event)
    return CropEditorWindow(request)
