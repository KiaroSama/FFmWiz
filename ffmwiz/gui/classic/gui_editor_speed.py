from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403


def _speed_to_percent(speed: float) -> int:
    try:
        return max(10, min(800, int(round(float(speed) * 100))))
    except Exception:
        return 100


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


def build_speed_editor(request: dict[str, Any], media_kind: str):
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    QtMultimedia = _import_qt_multimedia()
    Qt = QtCore.Qt
    QUrl = QtCore.QUrl

    QMainWindow = QtWidgets.QMainWindow
    QWidget = QtWidgets.QWidget
    QFrame = QtWidgets.QFrame
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QSlider = QtWidgets.QSlider
    QCheckBox = QtWidgets.QCheckBox
    QHBoxLayout = QtWidgets.QHBoxLayout
    QVBoxLayout = QtWidgets.QVBoxLayout
    QSizePolicy = QtWidgets.QSizePolicy
    QDoubleSpinBox = QtWidgets.QDoubleSpinBox
    QComboBox = QtWidgets.QComboBox
    QStyle = QtWidgets.QStyle

    class PreviewLabel(QLabel):
        clicked = QtCore.Signal()

        def __init__(self, audio_only=False):
            super().__init__()
            self.audio_only = audio_only
            self._pix = None
            self.setAlignment(Qt.AlignCenter)
            self.setMinimumSize(720, 360 if not audio_only else 220)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setStyleSheet(
                f"background-color: {PALETTE['timeline_bg']};"
                f"border: 1px solid {PALETTE['border']}; border-radius: 8px;"
            )
            self.setText("Loading preview...")

        def on_frame(self, frame):
            if self.audio_only or frame is None or not frame.isValid():
                return
            image = frame.toImage()
            if image.isNull():
                return
            self._pix = QtGui.QPixmap.fromImage(image)
            self._render()

        def set_waveform(self, path: Path):
            pix = QtGui.QPixmap(str(path))
            if not pix.isNull():
                self._pix = pix
                self._render()

        def _render(self):
            if self._pix is None:
                return
            self.setPixmap(self._pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self._render()

        def mouseReleaseEvent(self, event):
            if event.button() == Qt.LeftButton:
                self.clicked.emit()
            super().mouseReleaseEvent(event)

    class SpeedEditorWindow(QMainWindow):
        def __init__(self, req, kind):
            super().__init__()
            self.request = req
            self.kind = kind
            self.input_path = Path(req["input_path"])
            self.ffmpeg = str(req.get("ffmpeg") or "ffmpeg")
            self.audio_index = int(req.get("audio_index", 0) or 0)
            self.duration = float(req.get("duration") or 0.0)
            self.has_audio = bool(req.get("has_audio", kind == "audio"))
            self.audio_count = max(0, int(req.get("audio_count", 1 if self.has_audio else 0) or 0))
            self.result = {"status": "canceled"}
            self._icon = _icon_loader(self, self.style())
            self._wave_temp = tempfile.TemporaryDirectory(prefix="ffmwiz_waveform_")
            self._wave_path = Path(self._wave_temp.name) / "waveform.png"
            self._preview_generation = 0
            self._using_rendered_preview = False
            self._preview_source_start = 0.0
            self._preview_source_duration = 0.0
            self._rendered_preview_speed = 1.0
            self._scheduled_preview_generation = 0
            self._pending_preview_resume_playing = False
            self._current_preview_path = None
            self._pending_preview_path = None
            self._preview_proc = None
            self._closing = False
            self._preview_direction = "source"
            self._reverse_seek_pending = False
            self._pending_reverse_source_ms = None
            self._pending_reverse_resume_playing = False
            self._seek_resume_after_release = False
            self._resume_after_frame_pending = False
            self._resume_after_frame_armed = False
            self._resume_after_frame_should_play = False
            self._reverse_scrub_last_tick = None
            self._reverse_audio_mute_active = False
            self._reverse_audio_restore_muted = False
            self._audio_guard_active = False
            self._audio_guard_waiting_after_play = False
            self._audio_guard_restore_muted = False
            self._audio_guard_restore_volume = 0.7
            self._seeking = False
            self._restoring_settings = False
            self._history = None
            self._speed_value = 1.0
            self.setWindowTitle("FFmWiz Video Speed / Reverse" if kind == "video" else "FFmWiz Audio Speed / Reverse")
            # An embedded editor is reparented into a tab, so it must not be
            # given a native window handle it will never use.
            _apply_window_icon(self, self._icon,
                               native=not bool(self.request.get("_embedded")))
            self.setMinimumSize(940, 620 if kind == "video" else 500)
            self.resize(1160, 760 if kind == "video" else 560)
            self._build_ui()
            self._history = HistoryStack(self._snapshot())
            self._update_undo_redo_state()
            self._preview_timer = QtCore.QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.timeout.connect(self._render_reverse_preview)
            self._reverse_scrub_timer = QtCore.QTimer(self)
            self._reverse_scrub_timer.setInterval(90)
            self._reverse_scrub_timer.timeout.connect(self._reverse_scrub_tick)
            self._resume_after_frame_timer = QtCore.QTimer(self)
            self._resume_after_frame_timer.setSingleShot(True)
            self._resume_after_frame_timer.timeout.connect(self._finish_delayed_resume)
            self._audio_guard_timer = QtCore.QTimer(self)
            self._audio_guard_timer.setSingleShot(True)
            self._audio_guard_timer.timeout.connect(self._end_seek_audio_guard)
            self._setup_player()
            QtCore.QTimer.singleShot(60, self._load_media)
            if kind == "audio":
                QtCore.QTimer.singleShot(80, self._start_waveform)

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
            title = QLabel("FFmWiz Video Speed / Reverse" if self.kind == "video" else "FFmWiz Audio Speed / Reverse")
            title.setObjectName("title")
            h.addWidget(title)
            h.addSpacing(12)
            self.btn_undo = QPushButton(self._icon("undo", QStyle.SP_ArrowBack), " Undo (Ctrl+Z)")
            self.btn_undo.setToolTip("Undo speed/reverse setting change")
            self.btn_undo.clicked.connect(self._undo)
            h.addWidget(self.btn_undo)
            self.btn_redo = QPushButton(self._icon("redo", QStyle.SP_ArrowForward), " Redo (Ctrl+Y)")
            self.btn_redo.setToolTip("Redo speed/reverse setting change")
            self.btn_redo.clicked.connect(self._redo)
            h.addWidget(self.btn_redo)
            h.addStretch(1)
            info = QLabel(f"Duration {seconds_to_timecode(self.duration)}      •      Source  {self.input_path.name}")
            info.setObjectName("headerInfo")
            h.addWidget(info)
            root.addWidget(header)

            self.preview = PreviewLabel(audio_only=(self.kind == "audio"))
            self.preview.clicked.connect(self.toggle_playback)
            root.addWidget(self.preview, 1)

            controls = QFrame()
            controls.setObjectName("panel")
            lay = QVBoxLayout(controls)
            lay.setContentsMargins(12, 10, 12, 10)
            lay.setSpacing(8)

            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(QLabel("Speed"))
            self.speed_slider = QSlider(Qt.Horizontal)
            self.speed_slider.setRange(10, 800)
            self.speed_slider.setValue(100)
            self.speed_slider.setTracking(True)
            self.speed_slider.setMinimumWidth(360)
            row.addWidget(self.speed_slider, 1)
            self.speed_spin = QComboBox()
            self.speed_spin.setObjectName("speedValueCombo")
            self.speed_spin.setEditable(True)
            self.speed_spin.setInsertPolicy(QComboBox.NoInsert)
            self.speed_spin.setMinimumWidth(136)
            self.speed_spin.setToolTip("Speed percent presets. Type a percent value and press Enter.")
            for label in ("25%", "50%", "75%", "100%", "125%", "150%", "200%", "250%", "300%", "400%"):
                self.speed_spin.addItem(label)
            self.speed_spin.setCurrentText("100%")
            self.speed_spin.installEventFilter(self)
            row.addWidget(self.speed_spin)
            self.factor_spin = QComboBox()
            self.factor_spin.setObjectName("speedValueCombo")
            self.factor_spin.setEditable(True)
            self.factor_spin.setInsertPolicy(QComboBox.NoInsert)
            self.factor_spin.setMinimumWidth(128)
            self.factor_spin.setToolTip("Speed multiplier presets. 1.5x means 1.5 times faster.")
            for label in ("0.25x", "0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x", "3x", "4x", "8x"):
                self.factor_spin.addItem(label)
            self.factor_spin.setCurrentText("1x")
            self.factor_spin.installEventFilter(self)
            row.addWidget(self.factor_spin)
            self.reverse_box = QCheckBox("Reverse")
            self.reverse_box.setToolTip("Reverse playback/export direction")
            row.addWidget(self.reverse_box)
            if self.kind == "video" and self.has_audio:
                self.include_audio_box = QCheckBox("Sync all audio tracks")
                self.include_audio_box.setChecked(True)
                self.include_audio_box.setToolTip(
                    "Apply the same speed and reverse changes to every audio track so the preview/export stays in sync. "
                    "Disable it only if this tool should output video without synced audio."
                )
                row.addWidget(self.include_audio_box)
            lay.addLayout(row)

            timeline_row = QHBoxLayout()
            timeline_row.setSpacing(8)
            self.time_label = QLabel("00:00:00.000 / " + seconds_to_timecode(self.duration))
            self.time_label.setObjectName("dim")
            timeline_row.addWidget(self.time_label)
            self.position_slider = QSlider(Qt.Horizontal)
            self.position_slider.setRange(0, max(1, int(round(self.duration * 1000))))
            self.position_slider.setTracking(True)
            self.position_slider.setToolTip("Playback timeline")
            self.position_slider.installEventFilter(self)
            timeline_row.addWidget(self.position_slider, 1)
            lay.addLayout(timeline_row)

            audio_row = QHBoxLayout()
            audio_row.setSpacing(8)
            audio_row.addStretch(1)
            self.btn_mute = QPushButton(self._icon("volume_meter_3", QStyle.SP_MediaVolume), "  Mute (M)")
            self.btn_mute.setMinimumWidth(118)
            self.btn_mute.setIconSize(QtCore.QSize(20, 20))
            self.btn_mute.setToolTip("Mute / unmute preview audio (M)")
            self.btn_mute.clicked.connect(self.toggle_mute)
            audio_row.addWidget(self.btn_mute)
            self.volume_slider = QSlider(Qt.Horizontal)
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(70)
            self.volume_slider.setFixedWidth(170)
            self.volume_slider.setToolTip("Preview volume")
            audio_row.addWidget(self.volume_slider)
            self.volume_label = QLabel("70%")
            self.volume_label.setObjectName("dim")
            audio_row.addWidget(self.volume_label)
            lay.addLayout(audio_row)

            self.status = QLabel(
                "Speed preview updates live. Reverse preview renders a short synced segment when enabled."
            )
            self.status.setObjectName("dim")
            lay.addWidget(self.status)
            root.addWidget(controls)

            bottom = QHBoxLayout()
            self.btn_play = QPushButton(self._icon("play", QStyle.SP_MediaPlay), " Play (Space)")
            self.btn_play.setObjectName("primary")
            self.btn_play.clicked.connect(self.toggle_playback)
            bottom.addWidget(self.btn_play)
            bottom.addStretch(1)
            self.btn_cancel = QPushButton("Cancel (Esc)")
            self.btn_cancel.setObjectName("danger")
            self.btn_cancel.clicked.connect(self.cancel)
            bottom.addWidget(self.btn_cancel)
            self.btn_apply = QPushButton(self._icon("check", QStyle.SP_DialogOkButton), " Apply (Enter)")
            self.btn_apply.setObjectName("primary")
            self.btn_apply.clicked.connect(self.confirm)
            bottom.addWidget(self.btn_apply)
            root.addLayout(bottom)

            self.speed_slider.valueChanged.connect(self._on_slider_changed)
            self.speed_slider.sliderReleased.connect(self._commit_history)
            self.speed_spin.activated.connect(lambda _idx: self._apply_percent_text(commit=True))
            self.factor_spin.activated.connect(lambda _idx: self._apply_factor_text(commit=True))
            if self.speed_spin.lineEdit() is not None:
                self.speed_spin.lineEdit().returnPressed.connect(lambda: self._apply_percent_text(commit=True))
                self.speed_spin.lineEdit().editingFinished.connect(lambda: self._apply_percent_text(commit=True))
                self.speed_spin.lineEdit().installEventFilter(self)
            if self.factor_spin.lineEdit() is not None:
                self.factor_spin.lineEdit().returnPressed.connect(lambda: self._apply_factor_text(commit=True))
                self.factor_spin.lineEdit().editingFinished.connect(lambda: self._apply_factor_text(commit=True))
                self.factor_spin.lineEdit().installEventFilter(self)
            self.reverse_box.stateChanged.connect(self._on_settings_changed_commit)
            if self.kind == "video" and hasattr(self, "include_audio_box"):
                self.include_audio_box.stateChanged.connect(self._on_settings_changed_commit)
            self.position_slider.sliderPressed.connect(self._on_seek_pressed)
            self.position_slider.sliderReleased.connect(self._on_seek_released)
            self.position_slider.sliderMoved.connect(self._seek_from_slider)
            self.volume_slider.valueChanged.connect(self._on_volume_changed)
            self.volume_slider.sliderReleased.connect(self._commit_history)
            QtGui.QShortcut(QtGui.QKeySequence("Space"), self, activated=self.toggle_playback)
            QtGui.QShortcut(QtGui.QKeySequence("M"), self, activated=self.toggle_mute)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Z"), self, activated=self._undo)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Y"), self, activated=self._redo)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+Z"), self, activated=self._redo)
            QtGui.QShortcut(QtGui.QKeySequence("Esc"), self, activated=self.cancel)
            QtGui.QShortcut(QtGui.QKeySequence("Return"), self, activated=self._confirm_if_not_editing_speed)
            QtGui.QShortcut(QtGui.QKeySequence("Enter"), self, activated=self._confirm_if_not_editing_speed)

        def _setup_player(self):
            # Guard with getattr: the player attribute does not exist until this
            # runs (other methods intentionally use hasattr(self, "player")), so a
            # direct `self.player` read here raised AttributeError and the editor
            # failed to initialize (notably for audio inputs).
            if getattr(self, "player", None) is not None:
                return
            self.player = QtMultimedia.QMediaPlayer(self)
            self.audio = QtMultimedia.QAudioOutput(self)
            self.audio.setVolume(0.7)
            self.player.setAudioOutput(self.audio)
            if self.kind == "video":
                self.video_sink = QtMultimedia.QVideoSink(self)
                self.player.setVideoSink(self.video_sink)
                self.video_sink.videoFrameChanged.connect(self.preview.on_frame)
                self.video_sink.videoFrameChanged.connect(self._on_video_frame_for_resume)
            self.player.playbackStateChanged.connect(self._on_playback_state)
            self.player.mediaStatusChanged.connect(self._on_media_status_changed)
            self.player.errorOccurred.connect(self._on_player_error)
            self.player.positionChanged.connect(self._on_position_changed)
            self.player.durationChanged.connect(self._on_duration_changed)
            self.player.setPlaybackRate(1.0)

        def _load_media(self):
            self.player.setSource(QUrl.fromLocalFile(str(self.input_path)))
            self.player.pause()
            QtCore.QTimer.singleShot(150, self.player.pause)

        def _start_waveform(self):
            args = [
                "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(self.input_path),
                "-filter_complex", f"[0:a:{self.audio_index}]aformat=channel_layouts=mono,showwavespic=s=1600x260:colors=388bfd[wave]",
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
                self.preview.set_waveform(self._wave_path)
            else:
                self.preview.setText("Waveform preview could not be generated.")

        def _speed(self):
            return max(0.10, min(8.0, float(getattr(self, "_speed_value", 1.0))))

        def _parse_percent_text(self):
            text = str(self.speed_spin.currentText() or "").strip().lower().replace(" ", "")
            if text.endswith("%"):
                text = text[:-1]
            return max(0.10, min(8.0, float(text) / 100.0))

        def _parse_factor_text(self):
            text = str(self.factor_spin.currentText() or "").strip().lower().replace(" ", "")
            if text.endswith("x"):
                text = text[:-1]
            return max(0.10, min(8.0, float(text)))

        def _snapshot(self):
            return {
                "speed": self._speed(),
                "reverse": bool(self.reverse_box.isChecked()),
                "include_audio": bool(self.include_audio_box.isChecked()) if hasattr(self, "include_audio_box") else False,
                "volume": int(self.volume_slider.value()) if hasattr(self, "volume_slider") else 70,
                "muted": bool(self.audio.isMuted()) if hasattr(self, "audio") else False,
            }

        def _commit_history(self):
            if self._restoring_settings or self._history is None:
                return
            self._history.push(self._snapshot())
            self._update_undo_redo_state()

        def _restore_snapshot(self, snap):
            self._restoring_settings = True
            try:
                self._set_speed(float(snap.get("speed", 1.0)))
                self.reverse_box.setChecked(bool(snap.get("reverse", False)))
                if self.kind == "video" and hasattr(self, "include_audio_box"):
                    self.include_audio_box.setChecked(bool(snap.get("include_audio", True)))
                self.volume_slider.setValue(int(snap.get("volume", 70)))
                if hasattr(self, "audio"):
                    self.audio.setMuted(bool(snap.get("muted", False)))
            finally:
                self._restoring_settings = False
            self._on_settings_changed()
            self._update_volume_icon()
            self._update_undo_redo_state()

        def _undo(self):
            if self._history is None:
                return
            snap = self._history.undo()
            if snap is not None:
                self._restore_snapshot(snap)
                self._update_undo_redo_state()

        def _redo(self):
            if self._history is None:
                return
            snap = self._history.redo()
            if snap is not None:
                self._restore_snapshot(snap)
                self._update_undo_redo_state()

        def _update_undo_redo_state(self):
            if hasattr(self, "btn_undo"):
                self.btn_undo.setEnabled(bool(self._history and self._history.can_undo()))
            if hasattr(self, "btn_redo"):
                self.btn_redo.setEnabled(bool(self._history and self._history.can_redo()))

        def _set_speed(self, speed):
            speed = max(0.10, min(8.0, float(speed or 1.0)))
            self._speed_value = speed
            percent = speed * 100.0
            self.speed_slider.blockSignals(True)
            self.speed_spin.blockSignals(True)
            self.factor_spin.blockSignals(True)
            self.speed_slider.setValue(int(round(percent)))
            self.speed_spin.setCurrentText(f"{percent:.1f}%")
            self.factor_spin.setCurrentText(f"{speed:.2f}x")
            self.speed_slider.blockSignals(False)
            self.speed_spin.blockSignals(False)
            self.factor_spin.blockSignals(False)
            self._sync_presets(speed)
            self._on_settings_changed()

        def _sync_presets(self, speed):
            percent = int(round(speed * 100.0))
            factor_text = f"{speed:g}x"
            for combo, text in ((self.speed_spin, f"{percent}%"), (self.factor_spin, factor_text)):
                for i in range(combo.count()):
                    if combo.itemText(i).lower() == text.lower():
                        combo.blockSignals(True)
                        combo.setCurrentIndex(i)
                        combo.blockSignals(False)
                        break

        def _on_slider_changed(self, value):
            self._set_speed(float(value) / 100.0)

        def _apply_percent_text(self, commit=False):
            try:
                self._set_speed(self._parse_percent_text())
            except Exception:
                self._set_speed(self._speed())
            if commit:
                self._commit_history()

        def _apply_factor_text(self, commit=False):
            try:
                self._set_speed(self._parse_factor_text())
            except Exception:
                self._set_speed(self._speed())
            if commit:
                self._commit_history()

        def _wheel_step_speed(self, source_combo, event):
            delta = event.angleDelta().y()
            if delta == 0:
                return True
            step_count = max(1, abs(delta) // 120)
            direction = 1 if delta > 0 else -1
            current = self._speed()
            if source_combo is self.speed_spin:
                next_percent = round(current * 100.0 / 5.0) * 5.0 + direction * 5.0 * step_count
                next_speed = next_percent / 100.0
            else:
                next_speed = round(current / 0.05) * 0.05 + direction * 0.05 * step_count
            self._set_speed(max(0.10, min(8.0, next_speed)))
            self._commit_history()
            event.accept()
            return True

        def _timeline_value_from_event(self, event):
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            rect = self.position_slider.rect()
            width = max(1, rect.width())
            ratio = max(0.0, min(1.0, float(pos.x() - rect.left()) / float(width)))
            return int(round(self.position_slider.minimum() + ratio * (self.position_slider.maximum() - self.position_slider.minimum())))

        def _confirm_if_not_editing_speed(self):
            focus = QtWidgets.QApplication.focusWidget()
            speed_line = self.speed_spin.lineEdit() if self.speed_spin.lineEdit() is not None else None
            factor_line = self.factor_spin.lineEdit() if self.factor_spin.lineEdit() is not None else None
            if focus in (self.speed_spin, speed_line):
                self._apply_percent_text(commit=True)
                return
            if focus in (self.factor_spin, factor_line):
                self._apply_factor_text(commit=True)
                return
            self.confirm()

        def _select_combo_text(self, combo):
            if combo.lineEdit() is not None:
                combo.lineEdit().selectAll()

        def eventFilter(self, obj, event):
            speed_line = self.speed_spin.lineEdit() if hasattr(self, "speed_spin") and self.speed_spin.lineEdit() is not None else None
            factor_line = self.factor_spin.lineEdit() if hasattr(self, "factor_spin") and self.factor_spin.lineEdit() is not None else None
            if obj is getattr(self, "position_slider", None):
                if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                    self._on_seek_pressed()
                    value = self._timeline_value_from_event(event)
                    self.position_slider.setValue(value)
                    self._update_time_label(value)
                    event.accept()
                    return True
                if event.type() == QtCore.QEvent.MouseMove and self._seeking:
                    value = self._timeline_value_from_event(event)
                    self.position_slider.setValue(value)
                    self._update_time_label(value)
                    event.accept()
                    return True
                if event.type() == QtCore.QEvent.MouseButtonRelease and self._seeking and event.button() == Qt.LeftButton:
                    value = self._timeline_value_from_event(event)
                    self.position_slider.setValue(value)
                    self._update_time_label(value)
                    self._seeking = False
                    self._seek_to_logical_ms(value, self._seek_resume_after_release)
                    self._seek_resume_after_release = False
                    event.accept()
                    return True
            if event.type() == QtCore.QEvent.Wheel:
                if obj in (self.speed_spin, speed_line):
                    return self._wheel_step_speed(self.speed_spin, event)
                if obj in (self.factor_spin, factor_line):
                    return self._wheel_step_speed(self.factor_spin, event)
            if obj in (speed_line, factor_line):
                if event.type() == QtCore.QEvent.FocusIn:
                    QtCore.QTimer.singleShot(0, obj.selectAll)
                elif event.type() == QtCore.QEvent.MouseButtonPress and not obj.hasSelectedText():
                    QtCore.QTimer.singleShot(0, obj.selectAll)
            return super().eventFilter(obj, event)

        def _on_settings_changed_commit(self):
            self._on_settings_changed()
            self._commit_history()

        def _on_settings_changed(self):
            speed = self._speed()
            if self.reverse_box.isChecked():
                self._stop_reverse_scrub(update_button=False)
                self._end_reverse_audio_mute()
                self._schedule_reverse_preview()
            else:
                self._stop_reverse_scrub()
                self._end_reverse_audio_mute()
                self._restore_original_preview()
                try:
                    was_playing = self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
                    self.player.setPlaybackRate(speed)
                    if was_playing:
                        self.player.play()
                except Exception:
                    pass
            self.status.setText(
                f"Speed {speed * 100:.0f}% ({speed:.2f}x)"
                + (
                    " • rendering reverse preview"
                    if self.reverse_box.isChecked()
                    else ""
                )
            )

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

        def _on_player_error(self, *_args):
            self.status.setText("Preview playback error. Export command can still be created.")

        def _on_playback_state(self, state):
            if self.kind == "video" and self.reverse_box.isChecked() and self._reverse_scrub_timer.isActive():
                self._update_play_button(True)
                return
            playing = state == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
            self._update_play_button(playing)

        def toggle_playback(self):
            if self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                if self.reverse_box.isChecked() and not self._using_rendered_preview:
                    self._pending_reverse_source_ms = int(self.position_slider.value())
                    self._pending_reverse_resume_playing = True
                    self._schedule_reverse_preview(20)
                    return
                self.player.play()

        def confirm(self):
            payload = {
                "status": "ok",
                "speed": self._speed(),
                "reverse": bool(self.reverse_box.isChecked()),
            }
            if self.kind == "video" and hasattr(self, "include_audio_box"):
                payload["include_audio"] = bool(self.include_audio_box.isChecked())
            elif self.kind == "video":
                payload["include_audio"] = False
            self.result = payload
            self.close()

        def cancel(self):
            self.result = {"status": "canceled"}
            self.close()

        def closeEvent(self, event):
            self._closing = True
            self._stop_reverse_scrub()
            self._end_reverse_audio_mute()
            self._clear_pending_reverse_preview_state()
            try:
                self.player.stop()
            except Exception:
                pass
            try:
                if self._preview_proc is not None:
                    try:
                        self._preview_proc.finished.disconnect()
                    except Exception:
                        pass
                    self._preview_proc.kill()
            except Exception:
                pass
            self._cleanup_reverse_preview_files()
            try:
                self._wave_temp.cleanup()
            except Exception:
                pass
            super().closeEvent(event)

    return SpeedEditorWindow(request, media_kind)
