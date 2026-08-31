from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.classic.gui_editor_audio_waveform import (
    build_audio_waveform_widget)


def _hide_embedded_editor_actions(root_widget: Any) -> None:
    try:
        from PySide6 import QtWidgets  # type: ignore
        for button in root_widget.findChildren(QtWidgets.QPushButton):
            text = str(button.text() or "").lower()
            if "apply" in text or "confirm" in text or "cancel" in text:
                button.hide()
    except Exception:
        pass


def _stop_embedded_editor(editor: Any) -> None:
    for attr in ("player", "wave_proc", "_preview_proc"):
        obj = getattr(editor, attr, None)
        if obj is None:
            continue
        try:
            obj.stop()
        except Exception:
            try:
                obj.kill()
            except Exception:
                pass
        try:
            obj.waitForFinished(1000)
        except Exception:
            pass
    try:
        editor._stop_worker()
    except Exception:
        pass


def build_audio_cut_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    QtMultimedia = _import_qt_multimedia()
    Qt = QtCore.Qt
    QUrl = QtCore.QUrl

    WaveformCutWidget = build_audio_waveform_widget()

    class AudioCutWindow(QtWidgets.QMainWindow):
        def __init__(self, req):
            super().__init__()
            self.request = req
            self.input_path = Path(req["input_path"])
            self.ffmpeg = str(req.get("ffmpeg") or "ffmpeg")
            self.audio_index = int(req.get("audio_index", 0) or 0)
            self.duration = float(req.get("duration") or 0.0)
            self.result = {"status": "canceled", "keep_ranges": []}
            self._history = None
            self._restoring = False
            self._syncing_view_controls = False
            self._icon = _icon_loader(self, self.style())
            self._wave_temp = tempfile.TemporaryDirectory(prefix="ffmwiz_waveform_")
            self._wave_path = Path(self._wave_temp.name) / "waveform.png"
            self.setWindowTitle("FFmWiz Audio Cut Editor")
            # An embedded editor is reparented into a tab, so it must not be
            # given a native window handle it will never use.
            _apply_window_icon(self, self._icon,
                               native=not bool(self.request.get("_embedded")))
            self.setMinimumSize(1080, 620)
            self.resize(1240, 720)
            self._build_ui()
            self._history = HistoryStack(self._snapshot())
            self._update_undo_redo_state()
            self._setup_player()
            QtCore.QTimer.singleShot(80, self._load_media)
            QtCore.QTimer.singleShot(80, self._start_waveform)

        def _build_ui(self):
            central = QtWidgets.QWidget()
            central.setObjectName("central")
            self.setCentralWidget(central)
            root = QtWidgets.QVBoxLayout(central)
            root.setContentsMargins(14, 10, 14, 10)
            root.setSpacing(8)

            header = QtWidgets.QFrame()
            header.setObjectName("header")
            h = QtWidgets.QHBoxLayout(header)
            h.setContentsMargins(14, 10, 14, 10)
            title = QtWidgets.QLabel("FFmWiz Audio Cut Editor")
            title.setObjectName("title")
            h.addWidget(title)
            h.addStretch(1)
            info = QtWidgets.QLabel(f"Duration {seconds_to_timecode(self.duration)}      •      Source  {self.input_path.name}")
            info.setObjectName("headerInfo")
            h.addWidget(info)
            root.addWidget(header)

            self.waveform = WaveformCutWidget(self.duration)
            self.waveform.seek_requested.connect(self.seek)
            self.waveform.cut_selected.connect(self._on_waveform_cut_selected)
            self.waveform.view_changed.connect(self._sync_view_controls)
            root.addWidget(self.waveform, 1)

            view_row = QtWidgets.QHBoxLayout()
            view_row.setSpacing(8)
            nav_lbl = QtWidgets.QLabel("Waveform view")
            nav_lbl.setObjectName("controlLabel")
            view_row.addWidget(nav_lbl)
            view_frame = QtWidgets.QFrame()
            view_frame.setObjectName("timelineViewFrame")
            view_frame.setMinimumWidth(620)
            view_frame.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            vf_layout = QtWidgets.QHBoxLayout(view_frame)
            vf_layout.setContentsMargins(3, 1, 3, 1)
            vf_layout.setSpacing(3)
            self.btn_view_left = QtWidgets.QPushButton("◀")
            self.btn_view_left.setObjectName("timelineViewArrow")
            self.btn_view_left.setFixedSize(14, 14)
            self.btn_view_left.setAutoRepeat(True)
            self.btn_view_left.setAutoRepeatDelay(220)
            self.btn_view_left.setAutoRepeatInterval(70)
            self.btn_view_left.clicked.connect(lambda: self._nudge_view(-1))
            vf_layout.addWidget(self.btn_view_left)
            self.view_scroll = QtWidgets.QScrollBar(Qt.Horizontal)
            self.view_scroll.setObjectName("timelineViewScroll")
            self.view_scroll.setTracking(True)
            self.view_scroll.valueChanged.connect(self._on_view_scroll)
            vf_layout.addWidget(self.view_scroll, 1)
            self.btn_view_right = QtWidgets.QPushButton("▶")
            self.btn_view_right.setObjectName("timelineViewArrow")
            self.btn_view_right.setFixedSize(14, 14)
            self.btn_view_right.setAutoRepeat(True)
            self.btn_view_right.setAutoRepeatDelay(220)
            self.btn_view_right.setAutoRepeatInterval(70)
            self.btn_view_right.clicked.connect(lambda: self._nudge_view(1))
            vf_layout.addWidget(self.btn_view_right)
            view_row.addWidget(view_frame, 1)
            root.addLayout(view_row)

            zoom_row = QtWidgets.QHBoxLayout()
            zoom_row.setSpacing(8)
            zoom_lbl = QtWidgets.QLabel("Waveform zoom")
            zoom_lbl.setObjectName("controlLabel")
            zoom_row.addWidget(zoom_lbl)
            self.btn_zoom_left = QtWidgets.QPushButton("◀")
            self.btn_zoom_left.setObjectName("timelineZoomArrow")
            self.btn_zoom_left.setFixedSize(14, 14)
            self.btn_zoom_left.setAutoRepeat(True)
            self.btn_zoom_left.setAutoRepeatDelay(220)
            self.btn_zoom_left.setAutoRepeatInterval(70)
            self.btn_zoom_left.clicked.connect(lambda: self._nudge_zoom(-1))
            zoom_row.addWidget(self.btn_zoom_left)
            self.zoom_slider = QtWidgets.QSlider(Qt.Horizontal)
            self.zoom_slider.setObjectName("timelineZoomSlider")
            self.zoom_slider.setRange(0, 100)
            self.zoom_slider.setValue(0)
            self.zoom_slider.setTracking(True)
            self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
            zoom_row.addWidget(self.zoom_slider, 1)
            self.btn_zoom_right = QtWidgets.QPushButton("▶")
            self.btn_zoom_right.setObjectName("timelineZoomArrow")
            self.btn_zoom_right.setFixedSize(14, 14)
            self.btn_zoom_right.setAutoRepeat(True)
            self.btn_zoom_right.setAutoRepeatDelay(220)
            self.btn_zoom_right.setAutoRepeatInterval(70)
            self.btn_zoom_right.clicked.connect(lambda: self._nudge_zoom(1))
            zoom_row.addWidget(self.btn_zoom_right)
            root.addLayout(zoom_row)

            tip = QtWidgets.QLabel(
                "Tip: Mark ranges you want removed. Right-click a cut region to select it. Drag the CTI upward to zoom."
            )
            tip.setObjectName("tip")
            tip.setWordWrap(True)
            root.addWidget(tip)

            row = QtWidgets.QHBoxLayout()
            row.setSpacing(8)
            self.btn_play = QtWidgets.QPushButton(self._icon("play", QtWidgets.QStyle.SP_MediaPlay), " Play (Space)")
            self.btn_play.setObjectName("primary")
            self.btn_play.clicked.connect(self.toggle_playback)
            row.addWidget(self.btn_play)
            self.btn_undo = self._button("Undo (Ctrl+Z)", self._undo)
            row.addWidget(self.btn_undo)
            self.btn_redo = self._button("Redo (Ctrl+Y)", self._redo)
            row.addWidget(self.btn_redo)
            row.addWidget(self._button("Mark In (I)", self.mark_in, "markIn"))
            row.addWidget(self._button("Mark Out (O)", self.mark_out, "markOut"))
            row.addWidget(self._button("Add Cut (A)", self.add_cut, "green"))
            self.btn_invert = self._button("Invert Cuts (Ctrl+Shift+I)", self.invert_cuts, "purple")
            self.btn_invert.setToolTip("Invert cut ranges: keep the currently selected cut ranges and remove everything else.")
            row.addWidget(self.btn_invert)
            self.btn_delete_selected = self._button("Delete Selected Cut (Del)", self.delete_selected_cut, "dangerCut")
            row.addWidget(self.btn_delete_selected)
            row.addWidget(self._button("Delete All Cuts", self.delete_all_cuts, "danger"))
            row.addStretch(1)
            self.status = QtWidgets.QLabel("")
            self.status.setObjectName("status")
            row.addWidget(self.status)
            root.addLayout(row)

            self.cut_list = QtWidgets.QListWidget()
            self.cut_list.setMinimumHeight(120)
            self.cut_list.itemSelectionChanged.connect(self._on_cut_list_select)
            root.addWidget(self.cut_list)

            bottom = QtWidgets.QHBoxLayout()
            bottom.addStretch(1)
            cancel = QtWidgets.QPushButton("Cancel (Esc)")
            cancel.setObjectName("danger")
            cancel.clicked.connect(self.cancel)
            bottom.addWidget(cancel)
            apply = QtWidgets.QPushButton(self._icon("check", QtWidgets.QStyle.SP_DialogOkButton), " Confirm (Enter)")
            apply.setObjectName("primary")
            apply.clicked.connect(self.confirm)
            bottom.addWidget(apply)
            root.addLayout(bottom)

            QtGui.QShortcut(QtGui.QKeySequence("Space"), self, activated=self.toggle_playback)
            QtGui.QShortcut(QtGui.QKeySequence("I"), self, activated=self.mark_in)
            QtGui.QShortcut(QtGui.QKeySequence("O"), self, activated=self.mark_out)
            QtGui.QShortcut(QtGui.QKeySequence("A"), self, activated=self.add_cut)
            QtGui.QShortcut(QtGui.QKeySequence("Delete"), self, activated=self.delete_selected_cut)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Z"), self, activated=self._undo)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Y"), self, activated=self._redo)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+Z"), self, activated=self._redo)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+I"), self, activated=self.invert_cuts)
            QtGui.QShortcut(QtGui.QKeySequence("Esc"), self, activated=self.cancel)
            # AudioCutWindow has no inline preview-zoom/crop/speed text editors,
            # so Return/Enter simply confirm. (Binding to the video editor's
            # _confirm_or_apply_text_editor here previously crashed init.)
            QtGui.QShortcut(QtGui.QKeySequence("Return"), self, activated=self.confirm)
            QtGui.QShortcut(QtGui.QKeySequence("Enter"), self, activated=self.confirm)
            self._sync_view_controls()
            self._refresh()

        def _button(self, label, slot, object_name=None):
            btn = QtWidgets.QPushButton(label)
            if object_name:
                btn.setObjectName(object_name)
            btn.clicked.connect(slot)
            return btn

        def _setup_player(self):
            self.player = QtMultimedia.QMediaPlayer(self)
            self.audio = QtMultimedia.QAudioOutput(self)
            self.audio.setVolume(0.7)
            self.player.setAudioOutput(self.audio)
            self.player.positionChanged.connect(lambda ms: self.waveform.set_playhead(ms / 1000.0))
            self.player.playbackStateChanged.connect(self._on_playback_state)

        def _load_media(self):
            self.player.setSource(QUrl.fromLocalFile(str(self.input_path)))
            self.player.pause()

        def _start_waveform(self):
            args = [
                "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(self.input_path),
                "-filter_complex", f"[0:a:{self.audio_index}]aformat=channel_layouts=mono,showwavespic=s=1800x320:colors=79b4ff[wave]",
                "-map", "[wave]",
                "-frames:v", "1",
                "-c:v", "png",
                str(self._wave_path),
            ]
            self.wave_proc = QtCore.QProcess(self)
            self.wave_proc.finished.connect(self._waveform_finished)
            self.wave_proc.start(self.ffmpeg, args)

        def _waveform_finished(self, *_args):
            if self._wave_path.exists():
                self.waveform.set_waveform(self._wave_path)
            else:
                self.status.setText("Waveform generation failed.")

        def _on_playback_state(self, state):
            playing = state == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
            self.btn_play.setText(" Pause (Space)" if playing else " Play (Space)")
            self.btn_play.setIcon(self._icon("pause" if playing else "play", QtWidgets.QStyle.SP_MediaPause if playing else QtWidgets.QStyle.SP_MediaPlay))

        def seek(self, seconds):
            self.player.setPosition(int(max(0.0, min(self.duration, seconds)) * 1000))

        def toggle_playback(self):
            if self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()

        def mark_in(self):
            self.waveform.in_marker = self.waveform.playhead
            if self.waveform.out_marker <= self.waveform.in_marker:
                self.waveform.out_marker = min(self.duration, self.waveform.in_marker + 1.0)
            self._commit_history()
            self._refresh()

        def mark_out(self):
            self.waveform.out_marker = self.waveform.playhead
            if self.waveform.out_marker <= self.waveform.in_marker:
                self.waveform.in_marker = max(0.0, self.waveform.out_marker - 1.0)
            self._commit_history()
            self._refresh()

        def add_cut(self):
            start = min(self.waveform.in_marker, self.waveform.out_marker)
            end = max(self.waveform.in_marker, self.waveform.out_marker)
            if end <= start:
                return
            self.waveform.cut_ranges = normalize_ranges(self.waveform.cut_ranges + [(start, end)], self.duration)
            self.waveform.selected_cut = len(self.waveform.cut_ranges) - 1
            self._commit_history()
            self._refresh()

        def delete_selected_cut(self):
            idx = self.waveform.selected_cut
            ranges = normalize_ranges(self.waveform.cut_ranges, self.duration)
            if idx < 0 or idx >= len(ranges):
                return
            del ranges[idx]
            self.waveform.cut_ranges = ranges
            self.waveform.selected_cut = -1
            self._commit_history()
            self._refresh()

        def delete_all_cuts(self):
            if not self.waveform.cut_ranges:
                return
            self.waveform.cut_ranges = []
            self.waveform.selected_cut = -1
            self._commit_history()
            self._refresh()

        def invert_cuts(self):
            if self.duration <= 0:
                QtWidgets.QMessageBox.critical(self, "Unknown duration", "Audio duration is unknown, so cuts cannot be inverted.")
                return
            if not self.waveform.cut_ranges:
                QtWidgets.QMessageBox.warning(self, "No cuts", "Add at least one cut range before inverting.")
                return
            self.waveform.cut_ranges = invert_cut_ranges(self.waveform.cut_ranges, self.duration)
            self.waveform.selected_cut = -1
            self._commit_history()
            self._refresh()

        def _snapshot(self):
            return {
                "ranges": list(normalize_ranges(self.waveform.cut_ranges, self.duration)),
                "in_marker": float(self.waveform.in_marker),
                "out_marker": float(self.waveform.out_marker),
                "selected_cut": int(self.waveform.selected_cut),
            }

        def _commit_history(self):
            if self._restoring or self._history is None:
                return
            self._history.push(self._snapshot())
            self._update_undo_redo_state()

        def _restore_snapshot(self, snap):
            self._restoring = True
            try:
                self.waveform.cut_ranges = list(snap.get("ranges") or [])
                self.waveform.in_marker = float(snap.get("in_marker", 0.0))
                self.waveform.out_marker = float(snap.get("out_marker", min(5.0, self.duration)))
                self.waveform.selected_cut = int(snap.get("selected_cut", -1))
            finally:
                self._restoring = False
            self._refresh()

        def _undo(self):
            if self._history is None:
                return
            snap = self._history.undo()
            if snap is not None:
                self._restore_snapshot(snap)

        def _redo(self):
            if self._history is None:
                return
            snap = self._history.redo()
            if snap is not None:
                self._restore_snapshot(snap)

        def _update_undo_redo_state(self):
            if hasattr(self, "btn_undo"):
                self.btn_undo.setEnabled(bool(self._history and self._history.can_undo()))
            if hasattr(self, "btn_redo"):
                self.btn_redo.setEnabled(bool(self._history and self._history.can_redo()))
            if hasattr(self, "btn_delete_selected"):
                self.btn_delete_selected.setEnabled(0 <= self.waveform.selected_cut < len(normalize_ranges(self.waveform.cut_ranges, self.duration)))

        def _on_waveform_cut_selected(self, idx):
            self.waveform.selected_cut = idx
            self._refresh()

        def _on_cut_list_select(self):
            items = self.cut_list.selectedIndexes()
            self.waveform.selected_cut = items[0].row() if items else -1
            self.waveform.update()
            self._update_undo_redo_state()

        def _zoom_value_from_span(self):
            ratio = self.waveform.zoom_ratio()
            max_ratio = 64.0
            return int(round(max(0.0, min(1.0, math.log(ratio, max_ratio))) * 100.0))

        def _span_from_zoom_value(self, value):
            max_ratio = 64.0
            ratio = max_ratio ** (max(0, min(100, int(value))) / 100.0)
            return self.waveform.duration / ratio

        def _sync_view_controls(self):
            if self._syncing_view_controls:
                return
            self._syncing_view_controls = True
            try:
                max_value = max(0, int(round((self.waveform.duration - self.waveform.view_span) * 1000.0)))
                page = max(1, int(round(self.waveform.view_span * 1000.0)))
                self.view_scroll.setRange(0, max_value)
                self.view_scroll.setPageStep(page)
                self.view_scroll.setSingleStep(max(1, page // 20))
                self.view_scroll.setValue(int(round(self.waveform.view_start * 1000.0)))
                self.zoom_slider.setValue(self._zoom_value_from_span())
            finally:
                self._syncing_view_controls = False

        def _on_view_scroll(self, value):
            if self._syncing_view_controls:
                return
            self.waveform.set_view(float(value) / 1000.0, self.waveform.view_span, emit=False)
            self.waveform.update()

        def _on_zoom_slider(self, value):
            if self._syncing_view_controls:
                return
            center = self.waveform.view_start + self.waveform.view_span * 0.5
            span = self._span_from_zoom_value(value)
            self.waveform.set_view(center - span * 0.5, span)

        def _nudge_view(self, direction):
            self.waveform.set_view(
                self.waveform.view_start + direction * max(0.05, self.waveform.view_span * 0.10),
                self.waveform.view_span,
            )

        def _nudge_zoom(self, direction):
            self.zoom_slider.setValue(max(0, min(100, self.zoom_slider.value() + direction * 4)))

        def _refresh(self):
            self.waveform.update()
            self.status.setText(
                f"In {seconds_to_timecode(self.waveform.in_marker)}  •  "
                f"Out {seconds_to_timecode(self.waveform.out_marker)}  •  "
                f"Cuts {len(self.waveform.cut_ranges)}"
            )
            self.cut_list.blockSignals(True)
            self.cut_list.clear()
            for idx, (start, end) in enumerate(self.waveform.cut_ranges, start=1):
                item = QtWidgets.QListWidgetItem(f"{idx}. remove {seconds_to_timecode(start)} -> {seconds_to_timecode(end)}")
                self.cut_list.addItem(item)
                if idx - 1 == self.waveform.selected_cut:
                    item.setSelected(True)
            self.cut_list.blockSignals(False)
            self._sync_view_controls()
            self._update_undo_redo_state()

        def confirm(self):
            if not self.waveform.cut_ranges:
                QtWidgets.QMessageBox.warning(self, "No cuts", "Add at least one audio cut range or cancel.")
                return
            keep = invert_cuts_to_keep(self.waveform.cut_ranges, self.duration)
            self.result = {"status": "ok", "keep_ranges": keep}
            self.close()

        def cancel(self):
            self.result = {"status": "canceled", "keep_ranges": []}
            self.close()

        def closeEvent(self, event):
            try:
                self._stop_preview_frame_worker()
            except Exception:
                pass
            try:
                self.player.stop()
            except Exception:
                pass
            try:
                self._wave_temp.cleanup()
            except Exception:
                pass
            super().closeEvent(event)

    return AudioCutWindow(request)


def build_audio_transform_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    QMainWindow = QtWidgets.QMainWindow
    QWidget = QtWidgets.QWidget
    QVBoxLayout = QtWidgets.QVBoxLayout
    QHBoxLayout = QtWidgets.QHBoxLayout
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QTabWidget = QtWidgets.QTabWidget
    QFrame = QtWidgets.QFrame
    QStyle = QtWidgets.QStyle

    class AudioTransformEditorWindow(QMainWindow):
        def __init__(self, req):
            super().__init__()
            self.request = req
            self.duration = float(req.get("duration") or 0.0)
            self.result = {"status": "canceled"}
            self._icon = _icon_loader(self, self.style())
            self._embedded_editors: list[Any] = []
            self.setWindowTitle("FFmWiz Audio Cut / Speed / Reverse")
            _apply_window_icon(self, self._icon)
            self.setMinimumSize(1080, 680)
            self._build_ui()

        def _child_request(self, mode: str) -> dict[str, Any]:
            child = dict(self.request)
            child["mode"] = mode
            child["_embedded"] = True
            return child

        def _embed_editor(self, title: str, editor: Any) -> QWidget:
            self._embedded_editors.append(editor)
            widget = editor.takeCentralWidget()
            if widget is None:
                widget = QWidget()
                layout = QVBoxLayout(widget)
                layout.addWidget(QLabel(f"{title} could not be embedded."))
            widget.setParent(self)
            _hide_embedded_editor_actions(widget)
            return widget

        def _ensure_speed_editor(self, index: int = -1) -> Any:
            """Build the Speed / Reverse editor on first use.

            Called from the tab-change signal, so a user who opens the tab gets the
            real editor; confirm() keeps the defaults when it was never opened.
            """
            if self.speed_editor is not None:
                return self.speed_editor
            if index != -1 and index != self._speed_tab_index:
                return None
            self.speed_editor = build_speed_editor(self._child_request("audio_speed"), "audio")
            placeholder = self.tabs.widget(self._speed_tab_index)
            self.tabs.removeTab(self._speed_tab_index)
            self.tabs.insertTab(
                self._speed_tab_index,
                self._embed_editor("Speed / Reverse", self.speed_editor),
                "Speed / Reverse")
            self.tabs.setCurrentIndex(self._speed_tab_index)
            if placeholder is not None:
                placeholder.deleteLater()
            return self.speed_editor

        def _build_ui(self):
            central = QWidget()
            central.setObjectName("central")
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(14, 10, 14, 10)
            root.setSpacing(8)

            header = QFrame()
            header.setObjectName("header")
            h = QHBoxLayout(header)
            h.setContentsMargins(14, 10, 14, 10)
            title = QLabel("FFmWiz Audio Cut / Speed / Reverse")
            title.setObjectName("title")
            h.addWidget(title)
            h.addStretch(1)
            root.addWidget(header)

            self.tabs = QTabWidget()
            self.cut_editor = build_audio_cut_editor(self._child_request("audio_cut"))
            self.tabs.addTab(self._embed_editor("Audio Cut", self.cut_editor), "Audio Cut")
            # The Speed / Reverse editor costs ~2.5 s to construct (its own
            # QMediaPlayer loads the media), and its tab is not visible at
            # startup, so building it here is pure wait for the user. Add a
            # placeholder and swap in the real editor the first time the tab is
            # opened -- or on confirm, if it was never opened.
            self.speed_editor = None
            self._speed_tab_index = self.tabs.addTab(QWidget(), "Speed / Reverse")
            self.tabs.currentChanged.connect(self._ensure_speed_editor)
            root.addWidget(self.tabs, 1)

            footer = QHBoxLayout()
            self.status = QLabel("Edit audio cuts and speed/reverse in one place.")
            self.status.setObjectName("dim")
            footer.addWidget(self.status, 1)
            btn_cancel = QPushButton("Cancel (Esc)")
            btn_cancel.setObjectName("danger")
            btn_cancel.clicked.connect(self.cancel)
            footer.addWidget(btn_cancel)
            btn_apply = QPushButton(self._icon("check", QStyle.SP_DialogOkButton), " Confirm (Enter)")
            btn_apply.setObjectName("primary")
            btn_apply.clicked.connect(self.confirm)
            footer.addWidget(btn_apply)
            root.addLayout(footer)
            QtGui.QShortcut(QtGui.QKeySequence("Esc"), self, activated=self.cancel)
            QtGui.QShortcut(QtGui.QKeySequence("Return"), self, activated=self.confirm)
            QtGui.QShortcut(QtGui.QKeySequence("Enter"), self, activated=self.confirm)

        def confirm(self):
            keep_ranges: list[list[float]] = []
            try:
                cuts = normalize_ranges(self.cut_editor.waveform.cut_ranges, self.duration)
                keep_ranges = [[float(s), float(e)] for s, e in (invert_cuts_to_keep(cuts, self.duration) if cuts else [])]
            except Exception:
                keep_ranges = []
            speed = 1.0
            reverse = False
            try:
                # Never opened the tab -> never edited speed/reverse, so the
                # defaults above are already correct; do not pay to build it.
                if self.speed_editor is not None:
                    speed = float(self.speed_editor._speed())
                    reverse = bool(self.speed_editor.reverse_box.isChecked())
            except Exception:
                pass
            self.result = {
                "status": "ok",
                "keep_ranges": keep_ranges,
                "speed": speed,
                "reverse": reverse,
            }
            self.close()

        def cancel(self):
            self.result = {"status": "canceled"}
            self.close()

        def closeEvent(self, event):
            for editor in self._embedded_editors:
                _stop_embedded_editor(editor)
            super().closeEvent(event)

    return AudioTransformEditorWindow(request)
