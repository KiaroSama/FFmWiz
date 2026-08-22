from __future__ import annotations
import gui_common  # noqa: F401
from gui_common import *  # noqa: F401,F403


def build_unified_video_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    # PERF: QtMultimedia pulls in the native multimedia backend (Qt6Multimedia +
    # the bundled FFmpeg backend DLLs), which is the single most expensive cold
    # load on first launch. Defer it so it does NOT block building/showing the
    # window; it is imported lazily inside _setup_player (which itself runs ~120ms
    # AFTER the window is shown). Every QtMultimedia use lives in player callbacks
    # that only run once _setup_player has created self.player, so this is safe.
    QtMultimedia = None
    Qt = QtCore.Qt
    Signal = QtCore.Signal
    QPointF = QtCore.QPointF
    QRectF = QtCore.QRectF
    QMainWindow = QtWidgets.QMainWindow
    QWidget = QtWidgets.QWidget
    QVBoxLayout = QtWidgets.QVBoxLayout
    QHBoxLayout = QtWidgets.QHBoxLayout
    QGridLayout = QtWidgets.QGridLayout
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QSlider = QtWidgets.QSlider
    QComboBox = QtWidgets.QComboBox
    QSpinBox = QtWidgets.QSpinBox
    QCheckBox = QtWidgets.QCheckBox
    QScrollBar = QtWidgets.QScrollBar
    QSplitter = QtWidgets.QSplitter
    QFrame = QtWidgets.QFrame
    QStyle = QtWidgets.QStyle
    QSizePolicy = QtWidgets.QSizePolicy
    QAction = QtGui.QAction

    @dataclass
    class UnifiedSnapshot:
        margins: tuple[int, int, int, int] = (0, 0, 0, 0)
        cut_ranges: tuple[tuple[float, float], ...] = ()
        separators: tuple[float, ...] = ()
        mark_in: float | None = None
        mark_out: float | None = None
        speed: float = 1.0
        reverse: bool = False
        include_audio: bool = True

    UnifiedPreviewCanvas, FrameExtractWorker = build_unified_preview_widgets()

    UnifiedTimelineWidget = build_unified_timeline_widget()


    class UnifiedVideoEditorWindow(QMainWindow):
        def __init__(self, req):
            super().__init__()
            self.request = req
            raw_segments = list(req.get("join_segments") or [])
            self.join_segments: list[dict[str, object]] = []
            offset = 0.0
            if raw_segments:
                for idx, segment in enumerate(raw_segments):
                    seg_duration = max(0.001, float(segment.get("duration") or 0.001))
                    path = Path(segment.get("path") or req.get("input_path") or "")
                    self.join_segments.append(
                        {
                            "index": idx,
                            "path": path,
                            "name": str(segment.get("name") or path.name or f"Video {idx + 1}"),
                            "start": offset,
                            "end": offset + seg_duration,
                            "duration": seg_duration,
                            "label": f"Video {idx + 1}",
                        }
                    )
                    offset += seg_duration
            self.duration = float(offset if self.join_segments else (req.get("duration") or 0.0))
            self.fps = float(req.get("fps") or 25.0)
            self.source_w = int(req.get("source_w") or 1920)
            self.source_h = int(req.get("source_h") or 1080)
            self.input_path = Path(req.get("input_path") or "")
            self._active_segment_index = 0
            self._pending_segment_position_ms = None
            self._pending_segment_play = False
            self._switching_segment = False
            self.chapters = normalize_chapters(req.get("chapters") or [], self.duration)
            self.result = {"status": "canceled"}
            self._icon = _icon_loader(self, self.style())
            self._wave_temp = tempfile.TemporaryDirectory(prefix="ffmwiz_unified_waveform_")
            self._wave_path = Path(self._wave_temp.name) / "waveform.pcm"
            self._wave_proc = None
            # FFmWiz hands back KEEP ranges (the segments to keep). Internally the
            # editor stores CUT (removed) ranges, so convert KEEP -> CUT (the
            # complement) here; otherwise reopening would store cuts inverted.
            _initial_keep = [
                (float(s), float(e)) for s, e in (req.get("initial_keep_ranges") or [])
                if float(e) > float(s)
            ]
            if _initial_keep:
                self._cut_ranges: list[tuple[float, float]] = [
                    (float(s), float(e))
                    for s, e in invert_cuts_to_keep(
                        normalize_ranges(_initial_keep, self.duration), self.duration
                    )
                ]
            else:
                self._cut_ranges = []
            self._separator_points: list[float] = sorted({
                round(float(v), 6) for v in (req.get("initial_separator_points") or [])
                if 0.0 < float(v) < self.duration
            })
            self._mark_in: float | None = None
            self._mark_out: float | None = None
            # Crop/speed/reverse/include-audio need their widgets, so they are
            # applied after the UI is built (see _apply_initial_session_state).
            # Reopening the editor restores the previous session's edits instead
            # of starting from zero.
            self._initial_margins = [int(v) for v in (req.get("initial_margins") or [0, 0, 0, 0])][:4]
            while len(self._initial_margins) < 4:
                self._initial_margins.append(0)
            self._initial_speed = float(req.get("initial_speed") or 1.0)
            self._initial_reverse = bool(req.get("initial_reverse"))
            self._initial_include_audio = bool(req.get("initial_include_audio", req.get("has_audio")))
            self._history = HistoryStack(self._snapshot(), max_size=120)
            self._syncing_zoom = False
            self._syncing_view = False
            self._view_scroll_scale = 10000
            self._view_handle_width_px = None
            self._syncing_preview_zoom_control = False
            self._syncing_crop_controls = False
            self._zoom_presets = (10, 15, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 800, 1200, 1600, 3200)
            self._shortcuts = []
            self._restoring_snapshot = False
            self.player = None
            self.audio = None
            self.video_sink = None
            self._frame_worker = None
            self._pending_frame_request = None
            self._frame_request_started = 0.0
            self.setWindowTitle("FFmWiz Unified Video Editor")
            _apply_window_icon(self, self._icon)
            # PREVIEW: minimum wide enough for two 250px side columns plus the
            # 520px preview canvas (+ splitter handles/margins) so nothing clips.
            self.setMinimumSize(1120, 640)
            # Kick off the waveform decode NOW (async ffmpeg) so it runs in PARALLEL
            # with building the UI. By the time the window is shown the PCM is usually
            # ready, instead of the waveform appearing seconds after the window.
            # PERF: each build phase is timed so the dominant cold-start cost is
            # visible in the log (window-build vs deferred multimedia backend load).
            _phase_t = time.perf_counter()
            self._start_waveform()
            _gui_log_debug(f"unified _start_waveform in {time.perf_counter() - _phase_t:.3f}s", force=True)
            _phase_t = time.perf_counter()
            self._build_ui()
            _gui_log_debug(f"unified _build_ui in {time.perf_counter() - _phase_t:.3f}s", force=True)
            _phase_t = time.perf_counter()
            self._apply_initial_session_state()
            _gui_log_debug(f"unified _apply_initial_session_state in {time.perf_counter() - _phase_t:.3f}s", force=True)
            _phase_t = time.perf_counter()
            self._refresh_all()
            _gui_log_debug(f"unified _refresh_all in {time.perf_counter() - _phase_t:.3f}s", force=True)
            QtCore.QTimer.singleShot(120, self._setup_player)
            # Stream the (already-built) side-column panels in after the first paint so
            # the window appears fast instead of blocking on the column's layout/polish.
            QtCore.QTimer.singleShot(16, self._attach_next_panel)

        def _snapshot(self):
            margins = tuple(int(v) for v in getattr(self, "preview", None).margins) if hasattr(self, "preview") else (0, 0, 0, 0)
            return UnifiedSnapshot(
                margins=margins,
                cut_ranges=tuple((float(s), float(e)) for s, e in normalize_ranges(getattr(self, "_cut_ranges", []), self.duration)),
                separators=tuple(float(v) for v in getattr(self, "_separator_points", [])),
                mark_in=getattr(self, "_mark_in", None),
                mark_out=getattr(self, "_mark_out", None),
                speed=float(self._speed()) if hasattr(self, "speed_combo") else 1.0,
                reverse=bool(self.reverse_box.isChecked()) if hasattr(self, "reverse_box") else False,
                include_audio=bool(self.include_audio_box.isChecked()) if hasattr(self, "include_audio_box") else bool(self.request.get("has_audio")),
            )

        def _restore_snapshot(self, snap):
            self._restoring_snapshot = True
            try:
                self.preview.set_margins(list(snap.margins))
                self._cut_ranges = list(snap.cut_ranges)
                self._separator_points = list(snap.separators)
                self._mark_in = snap.mark_in
                self._mark_out = snap.mark_out
                self.speed_combo.setCurrentText(f"{snap.speed * 100:g}%")
                self.factor_combo.setCurrentText(f"{snap.speed:g}x")
                if hasattr(self, "speed_slider"):
                    self.speed_slider.setValue(int(round(snap.speed * 100.0)))
                self.reverse_box.setChecked(bool(snap.reverse))
                self.include_audio_box.setChecked(bool(snap.include_audio))
                self._apply_speed_to_player()
                self._refresh_all()
            finally:
                self._restoring_snapshot = False

        def _commit_history(self):
            if getattr(self, "_restoring_snapshot", False):
                return
            self._history.push(self._snapshot())
            self._update_undo_redo()

        def _apply_initial_session_state(self):
            # Apply crop/cuts/split/speed/reverse/include-audio carried over from
            # a previous session (passed in the request). Re-baseline history so
            # the restored state is the clean starting point for undo/redo.
            initial = UnifiedSnapshot(
                margins=tuple(self._initial_margins),
                cut_ranges=tuple(self._cut_ranges),
                separators=tuple(self._separator_points),
                mark_in=None,
                mark_out=None,
                speed=float(self._initial_speed),
                reverse=bool(self._initial_reverse),
                include_audio=bool(self._initial_include_audio),
            )
            self._restore_snapshot(initial)
            self._history = HistoryStack(self._snapshot(), max_size=120)
            self._update_undo_redo()

        def _undo(self):
            snap = self._history.undo()
            if snap is not None:
                self._restore_snapshot(snap)
            self._update_undo_redo()

        def _redo(self):
            snap = self._history.redo()
            if snap is not None:
                self._restore_snapshot(snap)
            self._update_undo_redo()

        def _update_undo_redo(self):
            self.btn_undo.setEnabled(self._history.can_undo())
            self.btn_redo.setEnabled(self._history.can_redo())

        def _build_ui(self):
            central = QWidget()
            central.setObjectName("central")
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            # PREVIEW: tighter outer margins/spacing to reclaim vertical space.
            root.setContentsMargins(8, 5, 8, 5)
            root.setSpacing(4)

            # PERF PROBE: the cold-start cost lives inside _build_ui. Isolate the two
            # most likely first-touch costs so the next cold log pinpoints the cause:
            #  - font database init (first text widget enumerates system fonts), and
            #  - the image/SVG icon plugin load (first icon read).
            _t = time.perf_counter()
            _probe_lbl = QLabel("0")
            _probe_lbl.fontMetrics().height()  # force QFontDatabase population
            _gui_log_debug(f"unified probe font-db init in {time.perf_counter() - _t:.3f}s", force=True)
            _probe_lbl.deleteLater()
            _t = time.perf_counter()
            self._icon("play", QStyle.SP_MediaPlay)  # force icon/image plugin + first asset read
            _gui_log_debug(f"unified probe first-icon load in {time.perf_counter() - _t:.3f}s", force=True)

            header = QFrame()
            header.setObjectName("header")
            h = QHBoxLayout(header)
            h.setContentsMargins(12, 5, 12, 5)
            title = QLabel("FFmWiz Unified Video Editor")
            title.setObjectName("title")
            h.addWidget(title)
            h.addSpacing(16)
            h.addWidget(QLabel(f"Source  {self.input_path.name}"))
            h.addStretch(1)
            self.btn_undo = QPushButton(self._icon("undo", QStyle.SP_ArrowBack), " Undo (Ctrl+Z)")
            self.btn_undo.clicked.connect(self._undo)
            h.addWidget(self.btn_undo)
            self.btn_redo = QPushButton(self._icon("redo", QStyle.SP_ArrowForward), " Redo (Ctrl+Y)")
            self.btn_redo.clicked.connect(self._redo)
            h.addWidget(self.btn_redo)
            root.addWidget(header)

            playback_panel = QFrame()
            playback_panel.setObjectName("panel")
            playback_outer = QVBoxLayout(playback_panel)
            playback_outer.setContentsMargins(8, 4, 8, 4)
            playback_outer.setSpacing(4)
            playback_header = QLabel("Playback")
            playback_header.setObjectName("sectionLabel")
            playback_outer.addWidget(playback_header)
            # PREVIEW: Playback laid out vertically so it fits a left side column.
            self.btn_play = QPushButton(self._icon("play", QStyle.SP_MediaPlay), " Play (Space)")
            self.btn_play.setObjectName("primary")
            self.btn_play.clicked.connect(self.toggle_playback)
            playback_outer.addWidget(self.btn_play)
            self.cti_time_label = QLabel("00:00:00.000")
            self.cti_time_label.setObjectName("status")
            self.cti_time_label.setAlignment(Qt.AlignCenter)
            self.cti_time_label.setToolTip("Current timeline position (CTI).")
            playback_outer.addWidget(self.cti_time_label)
            _seek_grid = QGridLayout()
            _seek_grid.setSpacing(4)
            _btn_home = self._btn(" Home", lambda: self.seek(0.0), "skip_backward", QStyle.SP_MediaSkipBackward)
            _btn_end = self._btn(" End", lambda: self.seek(self.duration), "skip_forward", QStyle.SP_MediaSkipForward)
            _btn_m5 = self._btn(" -5s", lambda: self.seek(self.current_time() - 5.0), "seek_backward", QStyle.SP_MediaSeekBackward)
            _btn_m1 = self._btn(" -1s", lambda: self.seek(self.current_time() - 1.0), "seek_backward", QStyle.SP_MediaSeekBackward)
            _btn_p1 = self._btn(" +1s", lambda: self.seek(self.current_time() + 1.0), "seek_forward", QStyle.SP_MediaSeekForward)
            _btn_p5 = self._btn(" +5s", lambda: self.seek(self.current_time() + 5.0), "seek_forward", QStyle.SP_MediaSeekForward)
            for _b in (_btn_m5, _btn_m1, _btn_p1, _btn_p5):
                _b.setAutoRepeat(True)
                _b.setAutoRepeatDelay(260)
                _b.setAutoRepeatInterval(80)
            for _b in (_btn_home, _btn_end, _btn_m5, _btn_m1, _btn_p1, _btn_p5):
                _b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            _seek_grid.addWidget(_btn_home, 0, 0)
            _seek_grid.addWidget(_btn_end, 0, 1)
            _seek_grid.addWidget(_btn_m5, 1, 0)
            _seek_grid.addWidget(_btn_m1, 1, 1)
            _seek_grid.addWidget(_btn_p1, 2, 0)
            _seek_grid.addWidget(_btn_p5, 2, 1)
            playback_outer.addLayout(_seek_grid)
            _edge_row = QHBoxLayout()
            _edge_row.setSpacing(4)
            self.btn_prev_cut_edge = self._btn(" Prev Edge", lambda: self.seek_nearest_cut_edge(-1), "seek_backward", QStyle.SP_MediaSeekBackward)
            self.btn_prev_cut_edge.setToolTip("Go to the nearest previous cut edge (Ctrl+Alt+Left). Hold to repeat.")
            self.btn_prev_cut_edge.setAutoRepeat(True)
            self.btn_prev_cut_edge.setAutoRepeatDelay(260)
            self.btn_prev_cut_edge.setAutoRepeatInterval(90)
            self.btn_next_cut_edge = self._btn(" Next Edge", lambda: self.seek_nearest_cut_edge(1), "seek_forward", QStyle.SP_MediaSeekForward)
            self.btn_next_cut_edge.setToolTip("Go to the nearest next cut edge (Ctrl+Alt+Right). Hold to repeat.")
            self.btn_next_cut_edge.setAutoRepeat(True)
            self.btn_next_cut_edge.setAutoRepeatDelay(260)
            self.btn_next_cut_edge.setAutoRepeatInterval(90)
            for _b in (self.btn_prev_cut_edge, self.btn_next_cut_edge):
                _b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            _edge_row.addWidget(self.btn_prev_cut_edge)
            _edge_row.addWidget(self.btn_next_cut_edge)
            playback_outer.addLayout(_edge_row)
            self.btn_mute = QPushButton(self._icon("volume_meter_3", QStyle.SP_MediaVolume), " Mute (M)")
            self.btn_mute.clicked.connect(self.toggle_mute)
            playback_outer.addWidget(self.btn_mute)
            volume_frame = QFrame()
            volume_frame.setObjectName("inlineControlFrame")
            volume_layout = QHBoxLayout(volume_frame)
            volume_layout.setContentsMargins(8, 2, 8, 2)
            volume_layout.setSpacing(5)
            self.btn_vol_down = QPushButton("◀")
            self.btn_vol_down.setObjectName("timelineZoomArrow")
            self.btn_vol_down.setFixedSize(20, 22)
            self.btn_vol_down.setAutoRepeat(True)
            self.btn_vol_down.setAutoRepeatDelay(240)
            self.btn_vol_down.setAutoRepeatInterval(60)
            self.btn_vol_down.setToolTip("Decrease volume")
            self.btn_vol_down.clicked.connect(lambda: self.volume_slider.setValue(self.volume_slider.value() - 5))
            volume_layout.addWidget(self.btn_vol_down)
            self.volume_slider = QSlider(Qt.Horizontal)
            self.volume_slider.setObjectName("cutVolumeSlider")
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(60)
            self.volume_slider.setFixedHeight(24)
            self.volume_slider.valueChanged.connect(self._on_volume_changed)
            volume_layout.addWidget(self.volume_slider, 1)
            self.btn_vol_up = QPushButton("▶")
            self.btn_vol_up.setObjectName("timelineZoomArrow")
            self.btn_vol_up.setFixedSize(20, 22)
            self.btn_vol_up.setAutoRepeat(True)
            self.btn_vol_up.setAutoRepeatDelay(240)
            self.btn_vol_up.setAutoRepeatInterval(60)
            self.btn_vol_up.setToolTip("Increase volume")
            self.btn_vol_up.clicked.connect(lambda: self.volume_slider.setValue(self.volume_slider.value() + 5))
            volume_layout.addWidget(self.btn_vol_up)
            self.volume_label = QLabel("60%")
            self.volume_label.setObjectName("dim")
            self.volume_label.setMinimumWidth(36)
            self.volume_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            volume_layout.addWidget(self.volume_label)
            playback_outer.addWidget(volume_frame)

            cut_panel = QFrame()
            cut_panel.setObjectName("markerPanel")
            cut_box = QVBoxLayout(cut_panel)
            cut_box.setContentsMargins(8, 4, 8, 4)
            cut_box.setSpacing(4)
            cut_header = QLabel("Markers / Cuts")
            cut_header.setObjectName("sectionLabel")
            cut_box.addWidget(cut_header)
            # PREVIEW: 2-column grid so Markers/Cuts fits a right side column.
            cut_grid = QGridLayout()
            cut_grid.setHorizontalSpacing(6)
            cut_grid.setVerticalSpacing(4)
            self.btn_mark_in = self._btn(" Mark In (I)", self.mark_in, "mark_in", None)
            self.btn_mark_in.setObjectName("markIn")
            self.btn_mark_out = self._btn(" Mark Out (O)", self.mark_out, "mark_out", None)
            self.btn_mark_out.setObjectName("markOut")
            add_cut = self._btn("Add Cut(s) (A)", self.add_cut)
            add_cut.setObjectName("green")
            self.btn_add_separator = self._btn("Add Split (S)", self.add_separator)
            self.btn_add_separator.setObjectName("separator")
            self.btn_add_separator.setToolTip("Add a Split point. Split divides the final processed output into multiple parts.")
            self.btn_invert = self._btn("Invert Cuts (Ctrl+Shift+I)", self.invert_cuts)
            self.btn_invert.setObjectName("purple")
            self.btn_invert.setToolTip("Invert cut ranges (Ctrl+Shift+I).")
            self.btn_convert_marker = self._btn("Convert In/Out (Ctrl+I)", self.convert_selected_marker)
            self.btn_convert_marker.setObjectName("convertMarker")
            self.btn_convert_marker.setToolTip("Select Mark In or Mark Out, then convert it to the opposite marker type (Ctrl+I).")
            self.btn_delete_markers = self._btn("Del Marker(s) (Del)", self.delete_selected_markers)
            self.btn_delete_markers.setObjectName("danger")
            self.btn_delete_markers.setToolTip("Delete the selected Mark In or Mark Out marker (Del).")
            self.btn_delete_separator = self._btn("Del Split (Del)", self.delete_selected_separator)
            self.btn_delete_separator.setObjectName("dangerAlt")
            self.btn_delete_separator.setToolTip("Delete the selected Split point (Del).")
            self.btn_delete_cut = self._btn("Del Cut (Del)", self.delete_selected_cut)
            self.btn_delete_cut.setObjectName("dangerCut")
            self.btn_delete_cut.setToolTip("Delete the selected cut (Del).")
            self.btn_delete_all = self._btn("Del All Cuts", self.delete_all_cuts)
            self.btn_delete_all.setObjectName("danger")
            _cut_cells = [
                self.btn_mark_in, self.btn_mark_out,
                add_cut, self.btn_add_separator,
                self.btn_invert, self.btn_convert_marker,
                self.btn_delete_markers, self.btn_delete_separator,
                self.btn_delete_cut, self.btn_delete_all,
            ]
            for _i, _b in enumerate(_cut_cells):
                _b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                cut_grid.addWidget(_b, _i // 2, _i % 2)
            cut_box.addLayout(cut_grid)
            self.snap_box = QCheckBox("Magnetic snap (CTI / marks / splits)")
            self.snap_box.setChecked(True)
            self.snap_box.setToolTip(
                "When on, dragged cut edges, marks and split points snap to the playhead,\n"
                "other marks, splits and the clip ends. The pull is pixel-based, so it\n"
                "weakens automatically as you zoom in. Turn it off for fully free-hand,\n"
                "frame-exact placement."
            )
            self.snap_box.toggled.connect(
                lambda on: setattr(self.timeline, "snap_enabled", bool(on)) if hasattr(self, "timeline") else None
            )
            cut_box.addWidget(self.snap_box)

            view_panel = QFrame()
            view_panel.setObjectName("panel")
            # PREVIEW: 2-column grid with short labels (full text in tooltips) so
            # nothing clips inside the narrow side column.
            view = QGridLayout(view_panel)
            view.setContentsMargins(8, 6, 8, 6)
            view.setHorizontalSpacing(6)
            view.setVerticalSpacing(5)
            view.setColumnStretch(0, 1)
            view.setColumnStretch(1, 1)
            view_title = QLabel("Crop / View")
            view_title.setObjectName("sectionLabel")
            view.addWidget(view_title, 0, 0, 1, 2)
            self.btn_hand_tool = self._btn(" Hand (H)", lambda: self.set_preview_tool("hand"), "hand_open", None)
            self.btn_hand_tool.setObjectName("tool")
            self.btn_hand_tool.setToolTip("Hand / pan tool (H). Double-click to reset the view.")
            self.btn_hand_tool.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.btn_hand_tool.installEventFilter(self)
            view.addWidget(self.btn_hand_tool, 1, 0)
            self.btn_zoom_tool = self._btn(" Zoom (Z)", lambda: self.set_preview_tool("zoom"), "zoom_tool_orange", None)
            self.btn_zoom_tool.setObjectName("tool")
            self.btn_zoom_tool.setToolTip("Zoom tool (Z).")
            self.btn_zoom_tool.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            view.addWidget(self.btn_zoom_tool, 1, 1)
            zoom_caption = QLabel("Zoom")
            zoom_caption.setObjectName("timelineControlLabel")
            view.addWidget(zoom_caption, 2, 0)
            self.zoom_percent_box = QFrame()
            self.zoom_percent_box.setObjectName("zoomPercentBox")
            self.zoom_percent_box.setFixedHeight(34)
            self.zoom_percent_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            zoom_box_layout = QHBoxLayout(self.zoom_percent_box)
            zoom_box_layout.setContentsMargins(0, 0, 0, 0)
            zoom_box_layout.setSpacing(0)
            self.preview_zoom_combo = QComboBox()
            self.preview_zoom_combo.setObjectName("zoomPercentCombo")
            self.preview_zoom_combo.setEditable(True)
            self.preview_zoom_combo.setInsertPolicy(QComboBox.NoInsert)
            self.preview_zoom_combo.setFixedHeight(32)
            self.preview_zoom_combo.setToolTip("Preview zoom presets. Type a percent value and press Enter to zoom manually.")
            self.preview_zoom_combo.addItems([f"{value}%" for value in self._zoom_presets])
            self.preview_zoom_combo.setCurrentText("100%")
            if self.preview_zoom_combo.lineEdit() is not None:
                self.preview_zoom_combo.lineEdit().returnPressed.connect(self._apply_preview_zoom_text)
                self.preview_zoom_combo.lineEdit().editingFinished.connect(self._apply_preview_zoom_text)
            self.preview_zoom_combo.activated.connect(lambda _idx: self._apply_preview_zoom_text())
            zoom_box_layout.addWidget(self.preview_zoom_combo)
            view.addWidget(self.zoom_percent_box, 2, 1)
            self.btn_crop_overlay = self._btn("Hide Crop Overlay (Ctrl+U)", self.toggle_crop_overlay)
            self.btn_crop_overlay.setObjectName("overlayToggle")
            self.btn_crop_overlay.setToolTip("Show or hide crop handles and guide lines.")
            self.btn_crop_overlay.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            view.addWidget(self.btn_crop_overlay, 3, 0, 1, 2)
            btn_reset_view = self._btn("Reset View (Ctrl+0)", self.reset_view)
            btn_reset_view.setToolTip("Reset zoom and pan.")
            btn_reset_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            view.addWidget(btn_reset_view, 4, 0, 1, 2)
            btn_reset_crop = self._btn("Reset Crop (Ctrl+R)", self.reset_crop)
            btn_reset_crop.setToolTip("Reset crop to the full frame.")
            btn_reset_crop.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            view.addWidget(btn_reset_crop, 5, 0, 1, 2)

            crop_number_frame = QFrame()
            crop_number_frame.setObjectName("panel")
            # PREVIEW: compact, symmetric arrow-key cross. Top above, Left/Right
            # mirrored on the middle row, Bottom below. All four cells are built
            # identically (centered label over a fixed-width centered field) so
            # the cross is perfectly even. Same signals/keys as the original.
            crop_grid = QGridLayout(crop_number_frame)
            crop_grid.setContentsMargins(8, 4, 8, 4)
            crop_grid.setHorizontalSpacing(2)
            crop_grid.setVerticalSpacing(1)
            crop_title = QLabel("Crop margins")
            crop_title.setObjectName("sectionLabel")
            crop_grid.addWidget(crop_title, 0, 0, 1, 5)
            self.crop_spinboxes: dict[str, QSpinBox] = {}

            def _make_crop_field(_key, _maximum):
                spin = QSpinBox()
                spin.setObjectName("cropNumberField")
                spin.setButtonSymbols(QSpinBox.UpDownArrows)   # integrated steppers (arrow icons via QSS)
                spin.setKeyboardTracking(True)
                spin.setRange(0, max(0, int(_maximum)))
                spin.setAlignment(Qt.AlignCenter)
                spin.setFixedWidth(70)
                spin.setFixedHeight(28)
                spin.setToolTip(f"Crop {_key} margin in pixels — type, or use the arrows.")
                spin.valueChanged.connect(lambda _value, _k=_key: self._on_crop_field_changed(_k))
                spin.editingFinished.connect(self._on_crop_field_edit_finished)
                spin.installEventFilter(self)
                if spin.lineEdit() is not None:
                    spin.lineEdit().installEventFilter(self)
                self.crop_spinboxes[_key] = spin
                return spin

            def _axis_label(_text):
                lab = QLabel(_text)
                lab.setObjectName("timelineControlLabel")
                return lab

            # Tight, centred cross: the outer columns (0 and 4) absorb the slack
            # so the four boxes pack close together in the middle; the steppers
            # live inside each field.
            _top_lab = _axis_label("Top"); _top_lab.setAlignment(Qt.AlignCenter)
            crop_grid.addWidget(_top_lab, 1, 2, alignment=Qt.AlignHCenter | Qt.AlignBottom)
            crop_grid.addWidget(_make_crop_field("top", self.source_h - 16), 2, 2, alignment=Qt.AlignHCenter)
            _left_w = QWidget(); _lh = QHBoxLayout(_left_w)
            _lh.setContentsMargins(0, 0, 0, 0); _lh.setSpacing(3)
            _lh.addWidget(_axis_label("Left")); _lh.addWidget(_make_crop_field("left", self.source_w - 16))
            crop_grid.addWidget(_left_w, 3, 1, alignment=Qt.AlignRight | Qt.AlignVCenter)
            _right_w = QWidget(); _rh = QHBoxLayout(_right_w)
            _rh.setContentsMargins(0, 0, 0, 0); _rh.setSpacing(3)
            _rh.addWidget(_make_crop_field("right", self.source_w - 16)); _rh.addWidget(_axis_label("Right"))
            crop_grid.addWidget(_right_w, 3, 3, alignment=Qt.AlignLeft | Qt.AlignVCenter)
            crop_grid.addWidget(_make_crop_field("bottom", self.source_h - 16), 4, 2, alignment=Qt.AlignHCenter)
            _bot_lab = _axis_label("Bottom"); _bot_lab.setAlignment(Qt.AlignCenter)
            crop_grid.addWidget(_bot_lab, 5, 2, alignment=Qt.AlignHCenter | Qt.AlignTop)
            crop_note = QLabel(
                "Crop is auto-aligned to even dimensions to keep the chroma phase "
                "correct so the video colors are not damaged."
            )
            crop_note.setObjectName("tip")
            crop_note.setWordWrap(True)
            crop_grid.addWidget(crop_note, 6, 0, 1, 5)
            crop_grid.setColumnStretch(0, 1)
            crop_grid.setColumnStretch(1, 0)
            crop_grid.setColumnStretch(2, 0)
            crop_grid.setColumnStretch(3, 0)
            crop_grid.setColumnStretch(4, 1)

            speed_frame = QFrame()
            speed_frame.setObjectName("panel")
            # PREVIEW: Speed / Reverse stacked vertically so it fits a side column.
            speed_box = QVBoxLayout(speed_frame)
            speed_box.setContentsMargins(8, 4, 8, 4)
            speed_box.setSpacing(4)
            speed_title = QLabel("Speed / Reverse")
            speed_title.setObjectName("sectionLabel")
            speed_box.addWidget(speed_title)
            _pct_row = QHBoxLayout()
            _pct_row.setSpacing(6)
            _pct_label = QLabel("Percent")
            _pct_label.setMinimumWidth(46)
            _pct_row.addWidget(_pct_label)
            self.speed_combo = QComboBox()
            self.speed_combo.setObjectName("speedValueCombo")
            self.speed_combo.setEditable(True)
            self.speed_combo.addItems(["25%", "50%", "75%", "100%", "125%", "150%", "175%", "200%", "250%", "300%"])
            self.speed_combo.setCurrentText("100%")
            self.speed_combo.setMinimumWidth(70)
            self.speed_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.speed_combo.installEventFilter(self)
            if self.speed_combo.lineEdit() is not None:
                self.speed_combo.lineEdit().installEventFilter(self)
            self.speed_combo.activated.connect(lambda _idx: self._on_speed_text_changed(self.speed_combo.currentText()))
            if self.speed_combo.lineEdit() is not None:
                self.speed_combo.lineEdit().returnPressed.connect(
                    lambda: self._on_speed_text_changed(self.speed_combo.currentText())
                )
                self.speed_combo.lineEdit().editingFinished.connect(
                    lambda: self._on_speed_text_changed(self.speed_combo.currentText())
                )
            _pct_row.addWidget(self.speed_combo, 1)
            speed_box.addLayout(_pct_row)
            _fac_row = QHBoxLayout()
            _fac_row.setSpacing(6)
            _fac_label = QLabel("Factor")
            _fac_label.setMinimumWidth(46)
            _fac_row.addWidget(_fac_label)
            self.factor_combo = QComboBox()
            self.factor_combo.setObjectName("speedValueCombo")
            self.factor_combo.setEditable(True)
            self.factor_combo.addItems(["0.25x", "0.5x", "0.75x", "1x", "1.25x", "1.5x", "1.75x", "2x", "2.5x", "3x"])
            self.factor_combo.setCurrentText("1x")
            self.factor_combo.setMinimumWidth(70)
            self.factor_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.factor_combo.installEventFilter(self)
            if self.factor_combo.lineEdit() is not None:
                self.factor_combo.lineEdit().installEventFilter(self)
            self.factor_combo.activated.connect(lambda _idx: self._on_factor_text_changed(self.factor_combo.currentText()))
            if self.factor_combo.lineEdit() is not None:
                self.factor_combo.lineEdit().returnPressed.connect(
                    lambda: self._on_factor_text_changed(self.factor_combo.currentText())
                )
                self.factor_combo.lineEdit().editingFinished.connect(
                    lambda: self._on_factor_text_changed(self.factor_combo.currentText())
                )
            _fac_row.addWidget(self.factor_combo, 1)
            speed_box.addLayout(_fac_row)
            speed_slider_frame = QFrame()
            speed_slider_frame.setObjectName("inlineControlFrame")
            speed_slider_frame_layout = QHBoxLayout(speed_slider_frame)
            speed_slider_frame_layout.setContentsMargins(8, 2, 8, 2)
            speed_slider_frame_layout.setSpacing(5)
            self.btn_speed_down = QPushButton("◀")
            self.btn_speed_down.setObjectName("timelineZoomArrow")
            self.btn_speed_down.setFixedSize(20, 22)
            self.btn_speed_down.setAutoRepeat(True)
            self.btn_speed_down.setAutoRepeatDelay(240)
            self.btn_speed_down.setAutoRepeatInterval(60)
            self.btn_speed_down.setToolTip("Slower")
            self.btn_speed_down.clicked.connect(lambda: self.speed_slider.setValue(self.speed_slider.value() - 5))
            speed_slider_frame_layout.addWidget(self.btn_speed_down)
            self.speed_slider = QSlider(Qt.Horizontal)
            self.speed_slider.setObjectName("timelineZoomSlider")
            self.speed_slider.setRange(5, 1000)
            self.speed_slider.setValue(100)
            self.speed_slider.setMinimumWidth(120)
            self.speed_slider.setToolTip("Speed control: 100% = normal speed, higher = faster, lower = slower.")
            self.speed_slider.valueChanged.connect(self._on_speed_slider_changed)
            self.speed_slider.sliderReleased.connect(self._commit_history)
            speed_slider_frame_layout.addWidget(self.speed_slider, 1)
            self.btn_speed_up = QPushButton("▶")
            self.btn_speed_up.setObjectName("timelineZoomArrow")
            self.btn_speed_up.setFixedSize(20, 22)
            self.btn_speed_up.setAutoRepeat(True)
            self.btn_speed_up.setAutoRepeatDelay(240)
            self.btn_speed_up.setAutoRepeatInterval(60)
            self.btn_speed_up.setToolTip("Faster")
            self.btn_speed_up.clicked.connect(lambda: self.speed_slider.setValue(self.speed_slider.value() + 5))
            speed_slider_frame_layout.addWidget(self.btn_speed_up)
            speed_box.addWidget(speed_slider_frame)
            _opt_row = QHBoxLayout()
            _opt_row.setSpacing(6)
            self.reverse_box = QCheckBox("Reverse")
            self.reverse_box.stateChanged.connect(lambda _v: self._on_reverse_toggled())
            _opt_row.addWidget(self.reverse_box)
            self.include_audio_box = QCheckBox("Sync all audio tracks")
            self.include_audio_box.setChecked(bool(self.request.get("has_audio")))
            self.include_audio_box.setEnabled(bool(self.request.get("has_audio")))
            self.include_audio_box.stateChanged.connect(lambda _v: self._commit_history())
            _opt_row.addWidget(self.include_audio_box)
            _opt_row.addStretch(1)
            speed_box.addLayout(_opt_row)
            # PREVIEW: tidy multi-line summary inside the Speed panel (the footer
            # status bar keeps the single-line version).
            self.summary_label = QLabel("")
            self.summary_label.setObjectName("status")
            self.summary_label.setWordWrap(True)
            speed_box.addWidget(self.summary_label)
            self.time_label = QLabel("")
            self.time_label.setObjectName("dim")
            self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            speed_box.addWidget(self.time_label)

            self.preview = UnifiedPreviewCanvas(self.source_w, self.source_h, self.duration)
            self.preview.margins_changed.connect(self._on_crop_changed)
            self.preview.edit_finished.connect(self._on_crop_edit_finished)
            self.preview.toggle_playback_requested.connect(self.toggle_playback)
            self.preview.zoom_changed.connect(self._sync_preview_zoom_combo)
            self.preview.set_tool("hand")

            self.timeline = UnifiedTimelineWidget(self.duration, self.fps)
            self.timeline.set_chapters(self.chapters)
            self.timeline.set_join_segments(self.join_segments)
            self.timeline.seek_requested.connect(self.seek)
            self.timeline.cut_selected.connect(self._on_cut_selected)
            self.timeline.separator_selected.connect(self._on_separator_selected)
            self.timeline.marker_moved.connect(self._on_marker_moved)
            self.timeline.separator_moved.connect(self._on_separator_moved)
            self.timeline.edit_finished.connect(self._on_timeline_edit_finished)
            self.timeline.view_changed.connect(self._sync_timeline_controls)

            self.editor_splitter = QSplitter(Qt.Vertical)
            self.editor_splitter.setChildrenCollapsible(False)
            self.editor_splitter.setHandleWidth(10)
            self.editor_splitter.addWidget(self.preview)
            self.editor_splitter.addWidget(self.timeline)
            # PREVIEW: bias the splitter strongly toward the video preview so it
            # expands vertically and the waveform/timeline stays compact.
            self.editor_splitter.setStretchFactor(0, 11)
            self.editor_splitter.setStretchFactor(1, 2)
            self.editor_splitter.setSizes([900, 170])

            # PREVIEW: dock ALL control panels into a single vertical column on
            # one side, grouped at the top with normal spacing. This frees the
            # whole other side for the video, which gets much larger.
            for _panel in (playback_panel, speed_frame, cut_panel, view_panel, crop_number_frame):
                _panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

            controls_col = QWidget()
            controls_col.setObjectName("sideColumn")
            controls_v = QVBoxLayout(controls_col)
            controls_v.setContentsMargins(0, 0, 6, 0)
            controls_v.setSpacing(8)
            controls_v.addStretch(1)
            # The panels are built (above) but NOT attached yet. Laying out + polishing
            # the whole side column at once blocks ~3.5s on this machine, delaying the
            # window. Instead we stream the panels in one-per-tick AFTER the first paint
            # (see _attach_next_panel), so the video/timeline show almost immediately and
            # the editor stays responsive while the side controls fill in.
            self._controls_v = controls_v
            self._deferred_panels = [playback_panel, cut_panel, view_panel, crop_number_frame, speed_frame]
            # Wrap in a scroll area so the panels keep their natural size and the
            # column scrolls on short windows instead of squishing/clipping.
            controls_scroll = QtWidgets.QScrollArea()
            controls_scroll.setObjectName("sideColumn")
            controls_scroll.setWidgetResizable(True)
            controls_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            controls_scroll.setWidget(controls_col)
            controls_scroll.setFixedWidth(338)   # wide enough that panels (and their right border) aren't clipped

            center_widget = QWidget()
            center_v = QVBoxLayout(center_widget)
            center_v.setContentsMargins(0, 0, 0, 0)
            center_v.setSpacing(0)
            center_v.addWidget(self.editor_splitter, 1)
            self.timeline_resize_grip = QFrame()
            self.timeline_resize_grip.setObjectName("timelineResizeGrip")
            self.timeline_resize_grip.setFixedHeight(8)
            self.timeline_resize_grip.setCursor(Qt.SizeVerCursor)
            self.timeline_resize_grip.setToolTip("Drag to resize the waveform/timeline panel.")
            self.timeline_resize_grip.installEventFilter(self)
            center_v.addWidget(self.timeline_resize_grip)

            # PREVIEW: a plain row (not a splitter) so the centre always fills the
            # remaining width with no dead gap — controls on the left, video+timeline
            # take everything else.
            center_row = QHBoxLayout()
            center_row.setContentsMargins(0, 0, 0, 0)
            center_row.setSpacing(8)
            center_row.addWidget(controls_scroll)
            _col_divider = QFrame()
            _col_divider.setFixedWidth(1)
            _col_divider.setStyleSheet(f"background-color: {PALETTE['border_strong']};")
            center_row.addWidget(_col_divider)
            center_row.addWidget(center_widget, 1)
            root.addLayout(center_row, 1)

            nav_frame = QFrame()
            nav_frame.setObjectName("panel")
            nav_row = QHBoxLayout(nav_frame)
            nav_row.setContentsMargins(10, 8, 10, 8)
            nav_row.setSpacing(8)
            view_label = QLabel("Timeline view")
            view_label.setObjectName("timelineControlLabel")
            nav_row.addWidget(view_label)
            view_frame = QFrame()
            view_frame.setObjectName("timelineViewFrame")
            view_frame_layout = QHBoxLayout(view_frame)
            view_frame_layout.setContentsMargins(4, 2, 4, 2)
            view_frame_layout.setSpacing(3)
            self.btn_view_left = QPushButton("◀")
            self.btn_view_left.setObjectName("timelineViewArrow")
            self.btn_view_left.setFixedSize(16, 16)
            self.btn_view_left.setAutoRepeat(True)
            self.btn_view_left.setAutoRepeatDelay(220)
            self.btn_view_left.setAutoRepeatInterval(70)
            self.btn_view_left.clicked.connect(lambda: self._nudge_view_scroll(-1))
            view_frame_layout.addWidget(self.btn_view_left)
            self.view_scroll = QSlider(Qt.Horizontal)
            self.view_scroll.setObjectName("timelineViewSlider")
            self.view_scroll.setTracking(True)
            self.view_scroll.setRange(0, self._view_scroll_scale)
            self.view_scroll.setMinimumWidth(420)
            self.view_scroll.valueChanged.connect(self._on_view_scroll)
            self.view_scroll.installEventFilter(self)
            view_frame_layout.addWidget(self.view_scroll, 1)
            self.btn_view_right = QPushButton("▶")
            self.btn_view_right.setObjectName("timelineViewArrow")
            self.btn_view_right.setFixedSize(16, 16)
            self.btn_view_right.setAutoRepeat(True)
            self.btn_view_right.setAutoRepeatDelay(220)
            self.btn_view_right.setAutoRepeatInterval(70)
            self.btn_view_right.clicked.connect(lambda: self._nudge_view_scroll(1))
            view_frame_layout.addWidget(self.btn_view_right)
            nav_row.addWidget(view_frame, 1)
            zoom_label = QLabel("Timeline zoom")
            zoom_label.setObjectName("timelineControlLabel")
            nav_row.addWidget(zoom_label)
            zoom_frame = QFrame()
            zoom_frame.setObjectName("timelineViewFrame")
            zoom_frame_layout = QHBoxLayout(zoom_frame)
            zoom_frame_layout.setContentsMargins(4, 2, 4, 2)
            zoom_frame_layout.setSpacing(3)
            self.btn_zoom_left = QPushButton("◀")
            self.btn_zoom_left.setObjectName("timelineZoomArrow")
            self.btn_zoom_left.setFixedSize(16, 16)
            self.btn_zoom_left.setAutoRepeat(True)
            self.btn_zoom_left.setAutoRepeatDelay(220)
            self.btn_zoom_left.setAutoRepeatInterval(70)
            self.btn_zoom_left.clicked.connect(lambda: self._nudge_timeline_zoom(-1))
            zoom_frame_layout.addWidget(self.btn_zoom_left)
            self.zoom_slider = QSlider(Qt.Horizontal)
            self.zoom_slider.setObjectName("timelineZoomSlider")
            self.zoom_slider.setRange(0, 100)
            self.zoom_slider.setMinimumWidth(420)
            self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
            zoom_frame_layout.addWidget(self.zoom_slider, 1)
            self.btn_zoom_right = QPushButton("▶")
            self.btn_zoom_right.setObjectName("timelineZoomArrow")
            self.btn_zoom_right.setFixedSize(16, 16)
            self.btn_zoom_right.setAutoRepeat(True)
            self.btn_zoom_right.setAutoRepeatDelay(220)
            self.btn_zoom_right.setAutoRepeatInterval(70)
            self.btn_zoom_right.clicked.connect(lambda: self._nudge_timeline_zoom(1))
            zoom_frame_layout.addWidget(self.btn_zoom_right)
            nav_row.addWidget(zoom_frame, 1)
            self.zoom_value_label = QLabel("0%")
            self.zoom_value_label.setObjectName("timelineControlLabel")
            self.zoom_value_label.setMinimumWidth(58)
            self.zoom_value_label.setAlignment(Qt.AlignCenter)
            self.zoom_value_label.setToolTip("Current timeline zoom (0% = whole clip, 100% = max zoom).")
            nav_row.addWidget(self.zoom_value_label)
            self.btn_reset_panels = QPushButton("Reset Panels")
            self.btn_reset_panels.setToolTip("Restore preview and timeline panel sizes.")
            self.btn_reset_panels.clicked.connect(self.reset_panels)
            nav_row.addWidget(self.btn_reset_panels)
            root.addWidget(nav_frame)

            # PREVIEW: condense the multi-line tip into one compact line.
            self.tip_label = QLabel(
                "Space play/pause • right-click preview play/pause • H pan, Z zoom, Ctrl+/- preview zoom, +/- timeline zoom "
                "• drag CTI horizontally to seek, up/down to zoom timeline • I/O mark, A cut, S split, Del remove, "
                "Ctrl+Shift+I invert, Ctrl+U overlay, Ctrl+Z/Y undo/redo"
            )
            self.tip_label.setObjectName("tip")
            self.tip_label.setWordWrap(True)
            root.addWidget(self.tip_label)

            self.cut_list_label = QLabel("")
            self.cut_list_label.setObjectName("tip")
            self.cut_list_label.setWordWrap(False)
            root.addWidget(self.cut_list_label)

            footer = QHBoxLayout()
            self.status = QLabel("Unified timeline: video preview, crop overlay, cut ranges, audio waveform, speed, and reverse are edited together.")
            self.status.setObjectName("dim")
            footer.addWidget(self.status, 1)
            btn_reset_all = QPushButton(" Reset All")
            btn_reset_all.setToolTip("Reset every edit (crop, cuts, split points, markers, speed, reverse) to defaults.")
            btn_reset_all.clicked.connect(self.reset_all)
            btn_reset_all.setMinimumHeight(40)
            btn_reset_all.setMinimumWidth(120)
            btn_reset_all.setStyleSheet("font-size: 13px; font-weight: 700; padding: 8px 16px;")
            footer.addWidget(btn_reset_all)
            btn_cancel = QPushButton("Cancel (Esc)")
            btn_cancel.setObjectName("danger")
            btn_cancel.clicked.connect(self.cancel)
            btn_cancel.setMinimumHeight(40)
            btn_cancel.setMinimumWidth(150)
            btn_cancel.setStyleSheet("font-size: 14px; font-weight: 700; padding: 8px 20px;")
            footer.addWidget(btn_cancel)
            btn_apply = QPushButton(self._icon("check", QStyle.SP_DialogOkButton), " Confirm (Enter)")
            btn_apply.setObjectName("primary")
            btn_apply.clicked.connect(self.confirm)
            btn_apply.setMinimumHeight(40)
            btn_apply.setMinimumWidth(180)
            btn_apply.setStyleSheet("font-size: 14px; font-weight: 700; padding: 8px 22px;")
            footer.addWidget(btn_apply)
            root.addLayout(footer)
            self._install_shortcuts()
            self._update_tool_buttons()

        def eventFilter(self, obj, event):
            if obj is getattr(self, "btn_hand_tool", None) and event.type() == QtCore.QEvent.MouseButtonDblClick:
                self.reset_view()
                return True
            if obj is getattr(self, "timeline_resize_grip", None):
                if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                    self._timeline_resize_origin_y = float(event.globalPosition().y())
                    self._timeline_resize_sizes = list(self.editor_splitter.sizes())
                    return True
                if event.type() == QtCore.QEvent.MouseMove and getattr(self, "_timeline_resize_sizes", None):
                    dy = float(event.globalPosition().y()) - float(getattr(self, "_timeline_resize_origin_y", 0.0))
                    top, bottom = (list(getattr(self, "_timeline_resize_sizes", [520, 260])) + [260])[:2]
                    self.editor_splitter.setSizes([max(180, int(top - dy)), max(150, int(bottom + dy))])
                    return True
                if event.type() in (QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.Leave):
                    self._timeline_resize_sizes = None
                    return event.type() == QtCore.QEvent.MouseButtonRelease
            if obj is getattr(self, "view_scroll", None) and event.type() == QtCore.QEvent.Resize:
                self._view_handle_width_px = None
                QtCore.QTimer.singleShot(0, self._sync_timeline_view_handle)
            crop_widgets = set(getattr(self, "crop_spinboxes", {}).values())
            crop_widgets.update(
                box.lineEdit()
                for box in getattr(self, "crop_spinboxes", {}).values()
                if box.lineEdit() is not None
            )
            if obj in crop_widgets:
                if event.type() == QtCore.QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    self._apply_crop_fields(commit=True)
                    if hasattr(obj, "clearFocus"):
                        obj.clearFocus()
                    return True
                if event.type() == QtCore.QEvent.FocusOut:
                    QtCore.QTimer.singleShot(0, lambda: self._apply_crop_fields(commit=True))
                if event.type() in (QtCore.QEvent.FocusIn, QtCore.QEvent.MouseButtonPress):
                    line = obj if hasattr(obj, "selectAll") else getattr(obj, "lineEdit", lambda: None)()
                    if line is not None:
                        QtCore.QTimer.singleShot(0, line.selectAll)
            speed_widgets = {
                getattr(self, "speed_combo", None),
                getattr(self, "factor_combo", None),
                getattr(getattr(self, "speed_combo", None), "lineEdit", lambda: None)(),
                getattr(getattr(self, "factor_combo", None), "lineEdit", lambda: None)(),
            }
            if obj in speed_widgets:
                if event.type() == QtCore.QEvent.Wheel:
                    delta = event.angleDelta().y()
                    if delta:
                        step = 0.05 if not (event.modifiers() & Qt.ControlModifier) else 0.25
                        self._set_speed_controls(self._speed() + (step if delta > 0 else -step), commit=True)
                    return True
                if event.type() == QtCore.QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    if obj in {getattr(self, "factor_combo", None), getattr(getattr(self, "factor_combo", None), "lineEdit", lambda: None)()}:
                        self._on_factor_text_changed(self.factor_combo.currentText())
                    else:
                        self._on_speed_text_changed(self.speed_combo.currentText())
                    if hasattr(obj, "clearFocus"):
                        obj.clearFocus()
                    return True
                if event.type() == QtCore.QEvent.FocusOut:
                    if obj in {getattr(self, "factor_combo", None), getattr(getattr(self, "factor_combo", None), "lineEdit", lambda: None)()}:
                        QtCore.QTimer.singleShot(0, lambda: self._on_factor_text_changed(self.factor_combo.currentText()))
                    else:
                        QtCore.QTimer.singleShot(0, lambda: self._on_speed_text_changed(self.speed_combo.currentText()))
                if event.type() in (QtCore.QEvent.FocusIn, QtCore.QEvent.MouseButtonPress):
                    line = obj if hasattr(obj, "selectAll") else getattr(obj, "lineEdit", lambda: None)()
                    if line is not None:
                        QtCore.QTimer.singleShot(0, line.selectAll)
            return super().eventFilter(obj, event)

        def _add_shortcut(self, sequence, slot):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)
            return shortcut

        def _install_shortcuts(self):
            self._shortcuts.clear()
            for sequence, slot in (
                ("Esc", self.cancel),
                ("Return", self._confirm_or_apply_text_editor),
                ("Enter", self._confirm_or_apply_text_editor),
                ("Space", self.toggle_playback),
                ("I", self.mark_in),
                ("O", self.mark_out),
                ("Ctrl+I", self.convert_selected_marker),
                ("Ctrl+O", self.convert_selected_marker),
                ("A", self.add_cut),
                ("S", self.add_separator),
                ("Delete", self.delete_selection),
                ("Ctrl+Z", self._undo),
                ("Ctrl+Y", self._redo),
                ("Ctrl+Shift+Z", self._redo),
                ("M", self.toggle_mute),
                ("Ctrl+R", self.reset_crop),
                ("Ctrl+0", self.reset_view),
                ("Ctrl+Shift+I", self.invert_cuts),
                ("Ctrl+Alt+Left", lambda: self.seek_nearest_cut_edge(-1)),
                ("Ctrl+Alt+Right", lambda: self.seek_nearest_cut_edge(1)),
                ("Home", lambda: self.seek(0.0)),
                ("End", lambda: self.seek(self.duration)),
                ("Left", lambda: self.seek(self.current_time() - 1.0)),
                ("Right", lambda: self.seek(self.current_time() + 1.0)),
                ("Shift+Left", lambda: self.seek(self.current_time() - 5.0)),
                ("Shift+Right", lambda: self.seek(self.current_time() + 5.0)),
                ("H", lambda: self.set_preview_tool("hand")),
                ("Z", lambda: self.set_preview_tool("zoom")),
                ("Ctrl+U", self.toggle_crop_overlay),
                ("+", lambda: self._nudge_timeline_zoom(1)),
                ("=", lambda: self._nudge_timeline_zoom(1)),
                ("-", lambda: self._nudge_timeline_zoom(-1)),
                ("Ctrl++", self.preview_zoom_in),
                ("Ctrl+=", self.preview_zoom_in),
                ("Ctrl+-", self.preview_zoom_out),
            ):
                self._add_shortcut(sequence, slot)

        def _text_input_has_focus(self) -> bool:
            focus = QtWidgets.QApplication.focusWidget()
            if focus is None:
                return False
            text_types = (
                QtWidgets.QLineEdit,
                QtWidgets.QTextEdit,
                QtWidgets.QPlainTextEdit,
                QtWidgets.QAbstractSpinBox,
                QtWidgets.QComboBox,
            )
            return isinstance(focus, text_types)

        def _btn(self, text, slot, icon_name: str | None = None, fallback=None):
            b = QPushButton(self._icon(icon_name, fallback), text) if icon_name or fallback is not None else QPushButton(text)
            b.clicked.connect(slot)
            return b

        def _panel(self, title: str):
            frame = QFrame()
            frame.setObjectName("panel")
            layout = QHBoxLayout(frame)
            layout.setContentsMargins(10, 8, 10, 8)
            layout.setSpacing(8)
            label = QLabel(title)
            label.setObjectName("sectionLabel")
            layout.addWidget(label)
            return frame, layout

        def _update_tool_buttons(self):
            tool = getattr(getattr(self, "preview", None), "_tool", "hand")
            for button, name in (
                (getattr(self, "btn_hand_tool", None), "hand"),
                (getattr(self, "btn_zoom_tool", None), "zoom"),
            ):
                if button is not None:
                    button.setProperty("active", "true" if tool == name else "false")
                    button.style().unpolish(button)
                    button.style().polish(button)

        def set_preview_tool(self, tool: str):
            if hasattr(self, "preview"):
                self.preview.set_tool(tool)
            self._update_tool_buttons()

        def _set_zoom_out_mode(self, enabled: bool):
            if hasattr(self, "preview"):
                self.preview.set_zoom_out_mode(bool(enabled))
            if hasattr(self, "btn_zoom_tool"):
                self.btn_zoom_tool.setIcon(self._icon("zoom_tool_out_orange" if enabled else "zoom_tool_orange", None))

        def toggle_crop_overlay(self):
            if not hasattr(self, "preview"):
                return
            visible = not bool(self.preview._show_crop_overlay)
            self.preview.set_crop_overlay_visible(visible)
            if hasattr(self, "btn_crop_overlay"):
                self.btn_crop_overlay.setText("Hide Crop Overlay (Ctrl+U)" if visible else "Show Crop Overlay (Ctrl+U)")

        def _preview_zoom_focus_source(self):
            return (self.preview.source_w / 2.0, self.preview.source_h / 2.0)

        def _sync_preview_zoom_combo(self, zoom: float | None = None):
            if not hasattr(self, "preview_zoom_combo"):
                return
            value = int(round((self.preview.zoom if zoom is None else float(zoom)) * 100.0))
            self._syncing_preview_zoom_control = True
            try:
                self.preview_zoom_combo.blockSignals(True)
                self.preview_zoom_combo.setCurrentText(f"{value}%")
                self.preview_zoom_combo.blockSignals(False)
            finally:
                self._syncing_preview_zoom_control = False

        def _apply_preview_zoom_text(self):
            if getattr(self, "_syncing_preview_zoom_control", False) or not hasattr(self, "preview_zoom_combo"):
                return
            text = self.preview_zoom_combo.currentText().strip()
            match = re.search(r"\d+(?:[.,]\d+)?", text)
            if not match:
                self._sync_preview_zoom_combo()
                return
            value = max(10.0, min(3200.0, float(match.group(0).replace(",", "."))))
            self.preview._zoom_centered(value / 100.0, self._preview_zoom_focus_source())
            self._sync_preview_zoom_combo()

        def _preview_zoom_editor_has_focus(self):
            if not hasattr(self, "preview_zoom_combo") or self.preview_zoom_combo.lineEdit() is None:
                return False
            focus = QtWidgets.QApplication.focusWidget()
            return (
                focus is self.preview_zoom_combo
                or focus is self.preview_zoom_combo.lineEdit()
                or self.preview_zoom_combo.hasFocus()
                or self.preview_zoom_combo.lineEdit().hasFocus()
            )

        def _confirm_or_apply_text_editor(self):
            if self._preview_zoom_editor_has_focus():
                self._apply_preview_zoom_text()
                self.preview_zoom_combo.clearFocus()
                return
            if self._crop_editor_has_focus():
                self._apply_crop_fields(commit=True)
                focus = QtWidgets.QApplication.focusWidget()
                if focus is not None:
                    focus.clearFocus()
                return
            if self._speed_editor_has_focus():
                self._apply_active_speed_editor()
                focus = QtWidgets.QApplication.focusWidget()
                if focus is not None:
                    focus.clearFocus()
                return
            self.confirm()

        def _crop_editor_has_focus(self):
            focus = QtWidgets.QApplication.focusWidget()
            if focus is None:
                return False
            for box in getattr(self, "crop_spinboxes", {}).values():
                if focus is box or focus is box.lineEdit() or box.hasFocus() or box.lineEdit().hasFocus():
                    return True
            return False

        def _validated_crop_field_margins(self) -> list[int]:
            boxes = getattr(self, "crop_spinboxes", {})
            top = int(boxes.get("top").value()) if boxes.get("top") is not None else 0
            left = int(boxes.get("left").value()) if boxes.get("left") is not None else 0
            right = int(boxes.get("right").value()) if boxes.get("right") is not None else 0
            bottom = int(boxes.get("bottom").value()) if boxes.get("bottom") is not None else 0
            return self.preview._clamped_margins([top, left, right, bottom])

        def _set_crop_field_ranges(self, margins: list[int]) -> None:
            boxes = getattr(self, "crop_spinboxes", {})
            if not boxes:
                return
            top, left, right, bottom = [int(v) for v in margins]
            min_size = 16
            ranges = {
                "top": max(0, self.source_h - bottom - min_size),
                "bottom": max(0, self.source_h - top - min_size),
                "left": max(0, self.source_w - right - min_size),
                "right": max(0, self.source_w - left - min_size),
            }
            for key, maximum in ranges.items():
                boxes[key].setRange(0, int(maximum))

        def _sync_crop_fields(self) -> None:
            boxes = getattr(self, "crop_spinboxes", {})
            if not boxes or not hasattr(self, "preview"):
                return
            margins = [int(v) for v in self.preview.margins]
            self._syncing_crop_controls = True
            try:
                for box in boxes.values():
                    box.blockSignals(True)
                self._set_crop_field_ranges(margins)
                for key, value in zip(("top", "left", "right", "bottom"), margins):
                    boxes[key].setValue(int(value))
            finally:
                for box in boxes.values():
                    box.blockSignals(False)
                self._syncing_crop_controls = False

        def _apply_crop_fields(self, commit: bool = False) -> None:
            if getattr(self, "_syncing_crop_controls", False) or not hasattr(self, "preview"):
                return
            margins = self._validated_crop_field_margins()
            if commit:
                # Snap typed values to even origin/size so the editor never
                # commits odd crop dimensions.
                margins = self._normalize_crop_margins_even(margins)
            changed = margins != [int(v) for v in self.preview.margins]
            if changed:
                self.preview.set_margins(margins)
            else:
                self._sync_crop_fields()
            if commit and changed:
                self._commit_history()
            self._refresh_all()

        def _on_crop_field_changed(self, _key: str) -> None:
            if getattr(self, "_syncing_crop_controls", False):
                return
            self._apply_crop_fields(commit=False)

        def _on_crop_field_edit_finished(self) -> None:
            if getattr(self, "_syncing_crop_controls", False):
                return
            self._apply_crop_fields(commit=True)

        def _speed_editor_has_focus(self):
            focus = QtWidgets.QApplication.focusWidget()
            widgets = {
                getattr(self, "speed_combo", None),
                getattr(self, "factor_combo", None),
                getattr(getattr(self, "speed_combo", None), "lineEdit", lambda: None)(),
                getattr(getattr(self, "factor_combo", None), "lineEdit", lambda: None)(),
            }
            return focus in widgets

        def _apply_active_speed_editor(self):
            focus = QtWidgets.QApplication.focusWidget()
            if focus in {getattr(self, "factor_combo", None), getattr(getattr(self, "factor_combo", None), "lineEdit", lambda: None)()}:
                self._on_factor_text_changed(self.factor_combo.currentText())
            else:
                self._on_speed_text_changed(self.speed_combo.currentText())

        def preview_zoom_in(self):
            if hasattr(self, "preview"):
                self.preview._zoom_centered(self.preview.zoom * 1.25)

        def preview_zoom_out(self):
            if hasattr(self, "preview"):
                self.preview._zoom_centered(self.preview.zoom / 1.25)

        def _attach_next_panel(self):
            """Stream the side-column panels into the (visible) scroll area one per
            event-loop tick. Laying them out all at once blocks ~3.5s here; doing it
            after the first paint, incrementally, keeps the editor responsive."""
            if getattr(self, "_closing", False):
                return
            panels = getattr(self, "_deferred_panels", None)
            if not panels:
                return
            panel = panels.pop(0)
            # Insert above the trailing stretch so the visual order is preserved.
            self._controls_v.insertWidget(max(0, self._controls_v.count() - 1), panel)
            if panels:
                QtCore.QTimer.singleShot(0, self._attach_next_panel)

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

        # ---- live reverse preview (preview-only; export uses the real reverse filter) ----
        REV_WINDOW = 15.0     # seconds of source reversed per proxy chunk

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
                src = self.input_path
                ss = win_start
                chunk = win_end - win_start
            tw = self._rev_target_width()
            vf = f"scale={tw}:-2,reverse,setpts=(PTS-STARTPTS)/{_ffmpeg_float(speed)}"
            want_audio = bool(self.request.get("has_audio"))
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

        def _speed(self):
            text = self.speed_combo.currentText().strip().lower()
            try:
                if text.endswith("%"):
                    return max(0.05, min(10.0, float(text[:-1]) / 100.0))
                if text.endswith("x"):
                    return max(0.05, min(10.0, float(text[:-1])))
                return max(0.05, min(10.0, float(text) / 100.0))
            except Exception:
                return 1.0

        def _factor(self):
            text = self.factor_combo.currentText().strip().lower()
            try:
                if text.endswith("x"):
                    return max(0.05, min(10.0, float(text[:-1])))
                if text.endswith("%"):
                    return max(0.05, min(10.0, float(text[:-1]) / 100.0))
                return max(0.05, min(10.0, float(text)))
            except Exception:
                return 1.0

        def _set_speed_controls(self, speed: float, commit: bool = False):
            speed = max(0.05, min(10.0, float(speed)))
            self._syncing_speed_controls = True
            try:
                self.speed_combo.setCurrentText(f"{speed * 100:g}%")
                self.factor_combo.setCurrentText(f"{speed:g}x")
                self.speed_slider.setValue(max(5, min(1000, int(round(speed * 100.0)))))
            finally:
                self._syncing_speed_controls = False
            self._on_transform_changed(commit=commit)

        def _on_speed_text_changed(self, _text):
            if getattr(self, "_syncing_speed_controls", False):
                return
            speed = self._speed()
            self._syncing_speed_controls = True
            self.factor_combo.setCurrentText(f"{speed:g}x")
            self.speed_slider.setValue(max(5, min(1000, int(round(speed * 100.0)))))
            self._syncing_speed_controls = False
            self._on_transform_changed(commit=True)

        def _on_factor_text_changed(self, _text):
            if getattr(self, "_syncing_speed_controls", False):
                return
            speed = self._factor()
            self._syncing_speed_controls = True
            self.speed_combo.setCurrentText(f"{speed * 100:g}%")
            self.speed_slider.setValue(max(5, min(1000, int(round(speed * 100.0)))))
            self._syncing_speed_controls = False
            self._on_transform_changed(commit=True)

        def _on_speed_slider_changed(self, value):
            if getattr(self, "_syncing_speed_controls", False):
                return
            speed = max(0.05, min(10.0, float(value) / 100.0))
            self._syncing_speed_controls = True
            self.speed_combo.setCurrentText(f"{speed * 100:g}%")
            self.factor_combo.setCurrentText(f"{speed:g}x")
            self._syncing_speed_controls = False
            self._on_transform_changed(commit=False)

        def _on_transform_changed(self, commit: bool = True):
            self._apply_speed_to_player()
            if commit:
                self._commit_history()
            self._refresh_all()

        def _apply_speed_to_player(self):
            try:
                self.player.setPlaybackRate(self._speed())
            except Exception as exc:
                _gui_log_debug(f"Could not apply preview playback rate: {exc}", force=True)

        @staticmethod
        def _snap_axis_even(near: int, far: int, source_dim: int) -> tuple[int, int]:
            return _snap_crop_axis_even(near, far, source_dim)

        def _normalize_crop_margins_even(self, margins) -> list[int]:
            return snap_crop_margins_even(margins, int(self.source_w), int(self.source_h))

        def _snap_crop_to_even(self) -> bool:
            # Snap the current preview crop to even origin/size. Returns True when
            # the margins changed.
            if not hasattr(self, "preview"):
                return False
            current = [int(v) for v in self.preview.margins]
            if not any(current):
                return False
            snapped = self._normalize_crop_margins_even(current)
            if snapped != current:
                self.preview.set_margins(snapped)
                self._sync_crop_fields()
                return True
            return False

        def _on_crop_changed(self):
            self._sync_crop_fields()
            self._refresh_all()

        def _on_crop_edit_finished(self):
            self._snap_crop_to_even()
            self._commit_history()
            self._refresh_all()

        def _on_marker_moved(self, name, value):
            if name == "in":
                self._mark_in = max(0.0, min(self.duration, float(value)))
                self.status.setText(f"Mark In: {seconds_to_timecode(self._mark_in)}")
                self.cut_list_label.setText(f"Mark In: {seconds_to_timecode(self._mark_in)}")
            elif name == "out":
                self._mark_out = max(0.0, min(self.duration, float(value)))
                self.status.setText(f"Mark Out: {seconds_to_timecode(self._mark_out)}")
                self.cut_list_label.setText(f"Mark Out: {seconds_to_timecode(self._mark_out)}")

        def _on_separator_moved(self, idx, value):
            if 0 <= int(idx) < len(self._separator_points):
                self._separator_points[int(idx)] = max(1e-6, min(self.duration - 1e-6, float(value)))
                current = self._separator_points[int(idx)]
                self.status.setText(f"Split #{int(idx) + 1}: {seconds_to_timecode(current)}")
                self.cut_list_label.setText(f"Split #{int(idx) + 1}: {seconds_to_timecode(current)}")

        def _on_timeline_edit_finished(self):
            self._cut_ranges = normalize_ranges(getattr(self.timeline, "cut_ranges", self._cut_ranges), self.duration)
            self._separator_points = sorted(set(round(float(value), 6) for value in self._separator_points))
            self._commit_history()
            self._refresh_all()

        def mark_in(self):
            current = self.current_time()
            tolerance = max(0.001, 0.5 / max(1.0, self.fps))
            if self._mark_out is not None and abs(float(self._mark_out) - current) <= tolerance:
                self._mark_out = None
            self._mark_in = current
            self.timeline.selected_marker = "in"
            self._commit_history()
            self._refresh_all()

        def mark_out(self):
            current = self.current_time()
            tolerance = max(0.001, 0.5 / max(1.0, self.fps))
            if self._mark_in is not None and abs(float(self._mark_in) - current) <= tolerance:
                self._mark_in = None
            self._mark_out = current
            self.timeline.selected_marker = "out"
            self._commit_history()
            self._refresh_all()

        def convert_in_to_out(self):
            if self._mark_in is None:
                self.status.setText("No Mark In marker exists to convert.")
                return
            self._mark_out = float(self._mark_in)
            self._mark_in = None
            self.timeline.selected_marker = "out"
            self._commit_history()
            self._refresh_all()

        def convert_out_to_in(self):
            if self._mark_out is None:
                self.status.setText("No Mark Out marker exists to convert.")
                return
            self._mark_in = float(self._mark_out)
            self._mark_out = None
            self.timeline.selected_marker = "in"
            self._commit_history()
            self._refresh_all()

        def convert_selected_marker(self):
            if self.timeline.selected_marker == "in":
                self.convert_in_to_out()
            elif self.timeline.selected_marker == "out":
                self.convert_out_to_in()
            else:
                self.status.setText("Select Mark In or Mark Out before converting.")

        def add_cut(self):
            if self._mark_in is None:
                self._mark_in = self.current_time()
                self._commit_history()
                self._refresh_all()
                return
            if self._mark_out is None:
                self._mark_out = self.current_time()
            s, e = sorted((float(self._mark_in), float(self._mark_out)))
            if e <= s:
                self.status.setText("Set different Mark In and Mark Out times before adding a cut.")
                return
            self._cut_ranges = normalize_ranges(self._cut_ranges + [(s, e)], self.duration)
            self.timeline.selected_cut = min(
                range(len(self._cut_ranges)),
                key=lambda idx: abs(self._cut_ranges[idx][0] - s) + abs(self._cut_ranges[idx][1] - e),
                default=-1,
            )
            self.timeline.selected_separator = -1
            self._mark_in = None
            self._mark_out = None
            self._commit_history()
            self._refresh_all()

        def add_separator(self):
            point = self.current_time()
            if not (1e-6 < point < self.duration - 1e-6):
                self.status.setText("Move the CTI inside the clip before adding a Split point.")
                return
            points = sorted(set(round(v, 6) for v in self._separator_points + [point]))
            self._separator_points = points
            self.timeline.selected_cut = -1
            self.timeline.selected_separator = points.index(round(point, 6))
            self._commit_history()
            self._refresh_all()

        def delete_selected_separator(self):
            idx = self.timeline.selected_separator
            if 0 <= idx < len(self._separator_points):
                self._separator_points.pop(idx)
                self.timeline.selected_separator = -1
                self._commit_history()
                self._refresh_all()

        def delete_selection(self):
            if self.timeline.selected_marker in {"in", "out"}:
                self.delete_selected_markers()
                return
            if self.timeline.selected_separator >= 0:
                self.delete_selected_separator()
                return
            self.delete_selected_cut()

        def delete_selected_markers(self):
            marker = self.timeline.selected_marker
            if marker == "in":
                self._mark_in = None
            elif marker == "out":
                self._mark_out = None
            else:
                return
            self.timeline.selected_marker = None
            self._commit_history()
            self._refresh_all()

        def delete_selected_cut(self):
            idx = self.timeline.selected_cut
            if 0 <= idx < len(self._cut_ranges):
                self._cut_ranges.pop(idx)
                self.timeline.selected_cut = -1
                self._commit_history()
                self._refresh_all()

        def delete_all_cuts(self):
            if not self._cut_ranges:
                return
            self._cut_ranges = []
            self.timeline.selected_cut = -1
            self._mark_in = None
            self._mark_out = None
            self._commit_history()
            self._refresh_all()

        def invert_cuts(self):
            if not self._cut_ranges:
                self.status.setText("No cut ranges exist to invert.")
                return
            self._cut_ranges = invert_cut_ranges(self._cut_ranges, self.duration)
            self.timeline.selected_cut = -1
            self._commit_history()
            self._refresh_all()

        def reset_crop(self):
            self.preview.reset_crop()
            self._commit_history()
            self._refresh_all()

        def reset_all(self):
            # Return every edit made in this editor to its zero/default state:
            # crop, cuts, split points, in/out markers, speed, and reverse.
            default = UnifiedSnapshot(
                margins=(0, 0, 0, 0),
                cut_ranges=(),
                separators=(),
                mark_in=None,
                mark_out=None,
                speed=1.0,
                reverse=False,
                include_audio=bool(self.request.get("has_audio")),
            )
            self.timeline.selected_cut = -1
            self.timeline.selected_separator = -1
            self.timeline.selected_marker = None
            self._restore_snapshot(default)
            self._commit_history()
            self._refresh_all()
            self.status.setText(
                "All edits reset to defaults: crop, cuts, split points, markers, speed, and reverse cleared."
            )

        def reset_view(self):
            self.preview.reset_view()
            self.timeline.view_start = 0.0
            self.timeline.view_span = self.timeline.duration
            self._sync_timeline_controls()
            self.timeline.update()

        def reset_panels(self):
            if hasattr(self, "editor_splitter"):
                self.editor_splitter.setSizes([780, 210])

        def _on_cut_selected(self, idx):
            self.btn_delete_cut.setEnabled(0 <= idx < len(self._cut_ranges))
            self.btn_delete_separator.setEnabled(False)
            self.btn_delete_markers.setEnabled(self.timeline.selected_marker in {"in", "out"})
            self._sync_marker_convert_button()

        def _on_separator_selected(self, idx):
            self.btn_delete_separator.setEnabled(0 <= idx < len(self._separator_points))
            self.btn_delete_cut.setEnabled(False)
            self.btn_delete_markers.setEnabled(self.timeline.selected_marker in {"in", "out"})
            self._sync_marker_convert_button()

        def _sync_marker_convert_button(self):
            marker = self.timeline.selected_marker
            if marker == "in":
                self.btn_convert_marker.setEnabled(True)
                self.btn_convert_marker.setText("Convert to Out (Ctrl+I)")
                self.btn_convert_marker.setToolTip("Convert the selected Mark In marker to Mark Out (Ctrl+I).")
            elif marker == "out":
                self.btn_convert_marker.setEnabled(True)
                self.btn_convert_marker.setText("Convert to In (Ctrl+I)")
                self.btn_convert_marker.setToolTip("Convert the selected Mark Out marker to Mark In (Ctrl+I).")
            else:
                self.btn_convert_marker.setEnabled(False)
                self.btn_convert_marker.setText("Select Marker")
                self.btn_convert_marker.setToolTip("Select Mark In or Mark Out before converting (Ctrl+I).")

        def _on_zoom_slider(self, value):
            if self._syncing_zoom:
                return
            dur = max(0.001, float(self.timeline.duration))
            min_span = min(dur, 0.05)
            frac = max(0.0, min(1.0, float(value) / 100.0))
            span = dur * (min_span / dur) ** frac          # log: 0 -> full view, 100 -> deepest zoom
            self.timeline.set_zoom_ratio(dur / span, focus_time=self.timeline.playhead)
            self._sync_timeline_controls()

        def _on_view_scroll(self, value):
            if self._syncing_view:
                return
            max_start = max(0.0, float(self.timeline.duration) - float(self.timeline.view_span))
            scale = max(1.0, float(getattr(self, "_view_scroll_scale", 10000)))
            start = 0.0 if max_start <= 1e-9 else (float(value) / scale) * max_start
            self.timeline.set_view_start(start)

        def _nudge_view_scroll(self, direction: int):
            step = max(0.25, self.timeline.view_span * 0.08)
            self.timeline.scroll_view(float(direction) * step)
            self._sync_timeline_controls()

        def _nudge_timeline_zoom(self, direction: int):
            value = max(0, min(100, self.zoom_slider.value() + int(direction) * 4))
            self.zoom_slider.setValue(value)

        def _sync_timeline_controls(self):
            if not hasattr(self, "zoom_slider"):
                return
            dur = max(0.001, float(self.timeline.duration))
            min_span = min(dur, 0.05)
            span = max(min_span, min(dur, float(self.timeline.view_span)))
            denom = math.log(min_span / dur) if dur > min_span else -1.0
            frac = (math.log(span / dur) / denom) if denom else 0.0
            value = int(round(max(0.0, min(1.0, frac)) * 100.0))
            self._syncing_zoom = True
            self.zoom_slider.setValue(max(0, min(100, value)))
            self._syncing_zoom = False
            if hasattr(self, "zoom_value_label"):
                self.zoom_value_label.setText(f"{max(0, min(100, value))}%")
            max_start_seconds = max(0.0, float(self.timeline.duration) - float(self.timeline.view_span))
            scale = max(1, int(getattr(self, "_view_scroll_scale", 10000)))
            span_ratio = max(0.001, min(1.0, float(self.timeline.view_span) / max(0.001, float(self.timeline.duration))))
            self._syncing_view = True
            self.view_scroll.setRange(0, 0 if max_start_seconds <= 1e-9 else scale)
            page = max(1, int(round(scale * span_ratio)))
            self.view_scroll.setPageStep(page)
            self.view_scroll.setSingleStep(max(1, page // 10))
            value = 0 if max_start_seconds <= 1e-9 else int(round((float(self.timeline.view_start) / max_start_seconds) * scale))
            self.view_scroll.setValue(max(0, min(self.view_scroll.maximum(), value)))
            self._syncing_view = False
            self._sync_timeline_view_handle()

        def _sync_timeline_view_handle(self):
            if not hasattr(self, "view_scroll"):
                return
            ratio = max(0.02, min(1.0, float(self.timeline.view_span) / max(0.001, float(self.timeline.duration))))
            track_width = max(40, self.view_scroll.width() - 24)
            width = int(max(34, min(track_width, track_width * ratio)))
            if getattr(self, "_view_handle_width_px", None) == width:
                return
            self._view_handle_width_px = width
            # Re-applying a stylesheet forces a full style recompute; during a zoom
            # gesture the handle width changes every step, so debounce it — apply
            # once motion settles. (The thumb size just lags a frame; cheap & invisible.)
            timer = getattr(self, "_view_handle_timer", None)
            if timer is None:
                timer = QtCore.QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(self._apply_view_handle_style)
                self._view_handle_timer = timer
            timer.start(90)

        def _apply_view_handle_style(self):
            if not hasattr(self, "view_scroll"):
                return
            width = int(getattr(self, "_view_handle_width_px", 34) or 34)
            self.view_scroll.setStyleSheet(
                "QSlider#timelineViewSlider::groove:horizontal {"
                "background: #18212b; height: 5px; border-radius: 3px;"
                "}"
                "QSlider#timelineViewSlider::sub-page:horizontal {"
                "background: #18212b; border-radius: 3px;"
                "}"
                "QSlider#timelineViewSlider::handle:horizontal {"
                f"background: #8cff9d; width: {width}px; height: 12px; margin: -4px 0; "
                "border-radius: 6px; border: 1px solid #2ea043;"
                "}"
                "QSlider#timelineViewSlider::handle:horizontal:hover { background: #c7ffd0; }"
            )

        def _refresh_all(self):
            self._cut_ranges = normalize_ranges(self._cut_ranges, self.duration)
            self._separator_points = sorted(
                set(
                    round(float(value), 6)
                    for value in self._separator_points
                    if 1e-6 < float(value) < self.duration - 1e-6
                )
            )
            self.timeline.set_cut_ranges(self._cut_ranges)
            self.timeline.set_separator_points(self._separator_points)
            self.timeline.set_marks(self._mark_in, self._mark_out)
            self.timeline.selected_cut = min(self.timeline.selected_cut, len(self._cut_ranges) - 1)
            self.timeline.selected_separator = min(self.timeline.selected_separator, len(self._separator_points) - 1)
            has_cuts = bool(self._cut_ranges)
            self.btn_delete_cut.setEnabled(0 <= self.timeline.selected_cut < len(self._cut_ranges))
            self.btn_delete_separator.setEnabled(0 <= self.timeline.selected_separator < len(self._separator_points))
            self.btn_delete_markers.setEnabled(self.timeline.selected_marker in {"in", "out"})
            self.btn_delete_all.setEnabled(has_cuts)
            self.btn_invert.setEnabled(has_cuts)
            self._sync_marker_convert_button()
            self._sync_crop_fields()
            top, left, right, bottom = self.preview.margins
            crop_w = max(1, self.source_w - left - right)
            crop_h = max(1, self.source_h - top - bottom)
            in_text = seconds_to_timecode(self._mark_in) if self._mark_in is not None else "--"
            out_text = seconds_to_timecode(self._mark_out) if self._mark_out is not None else "--"
            summary = (
                f"Crop {crop_w}x{crop_h}  |  Mark In {in_text}  |  Mark Out {out_text}  |  "
                f"Cuts {len(self._cut_ranges)}  |  Splits {len(self._separator_points)}  |  Videos {max(1, len(self.join_segments))}  |  Chapters {len(self.chapters)}  |  Speed {self._speed():g}x  |  "
                f"Reverse {'yes' if self.reverse_box.isChecked() else 'no'}"
            )
            # Tidy, grouped multi-line version for the Speed panel (avoids the
            # tangled single-line wrap in the narrow column).
            panel_summary = (
                f"Crop&nbsp; <b>{crop_w}×{crop_h}</b><br>"
                f"In {in_text} &nbsp;·&nbsp; Out {out_text}<br>"
                f"Cuts {len(self._cut_ranges)} &nbsp;·&nbsp; Splits {len(self._separator_points)} "
                f"&nbsp;·&nbsp; Speed {self._speed():g}× &nbsp;·&nbsp; "
                f"{'Reversed' if self.reverse_box.isChecked() else 'Forward'}"
            )
            self.summary_label.setText(panel_summary)
            self.status.setText(summary)
            parts: list[str] = []
            if self._cut_ranges:
                shown = "   ".join(
                    f"#{idx + 1} {seconds_to_timecode(s)}->{seconds_to_timecode(e)}"
                    for idx, (s, e) in enumerate(self._cut_ranges[:5])
                )
                more = f"   +{len(self._cut_ranges) - 5} more" if len(self._cut_ranges) > 5 else ""
                parts.append("Cut ranges: " + shown + more)
            if self._separator_points:
                shown = "   ".join(
                    f"#{idx + 1} {seconds_to_timecode(value)}"
                    for idx, value in enumerate(self._separator_points[:8])
                )
                more = f"   +{len(self._separator_points) - 8} more" if len(self._separator_points) > 8 else ""
                parts.append("Splits: " + shown + more)
            if parts:
                self.cut_list_label.setText("   |   ".join(parts))
            else:
                self.cut_list_label.setText("Cut ranges and Splits: none. Mark In/Out and press Add Cut(s), or press S at the CTI to split the final output into parts.")
            self._update_undo_redo()
            self._sync_timeline_controls()
            self.timeline.update()

        def _start_waveform(self):
            # A join whose FIRST input is silent still has audio to draw: the
            # global has_audio flag is derived from input 0 alone (D07).
            any_segment_audio = any(seg.get("has_audio") for seg in (self.join_segments or []))
            if not bool(self.request.get("has_audio")) and not any_segment_audio:
                if hasattr(self, "status"):
                    self.status.setText("No audio stream is available for waveform preview.")
                return
            # Decode the audio to low-rate mono PCM; peaks are computed from it and
            # drawn as a crisp vector waveform (no stretched image).
            args = ["-hide_banner", "-loglevel", "error", "-y"]
            if self.join_segments:
                labels = []
                for idx, segment in enumerate(self.join_segments):
                    if segment.get("has_audio", True):
                        args.extend(["-i", str(segment["path"])])
                    else:
                        # [idx:a:0] matching nothing makes ffmpeg refuse the WHOLE
                        # filtergraph, so one silent segment killed the waveform
                        # for the entire join. Feed matching silence instead (D07).
                        seg_duration = max(0.001, float(segment.get("duration") or 0.0))
                        args.extend(["-f", "lavfi", "-t", f"{seg_duration:.3f}",
                                     "-i", "anullsrc=channel_layout=mono:sample_rate=4000"])
                    labels.append(f"[a{idx}]")
                filters = []
                for idx, _segment in enumerate(self.join_segments):
                    filters.append(f"[{idx}:a:0]aformat=channel_layouts=mono,aresample=4000,asetpts=PTS-STARTPTS[a{idx}]")
                filters.append(f"{''.join(labels)}concat=n={len(self.join_segments)}:v=0:a=1[mix]")
                args.extend(["-filter_complex", ";".join(filters), "-map", "[mix]"])
            else:
                args.extend([
                    "-i", str(self.request.get("input_path") or ""),
                    "-filter_complex", "[0:a:0]aformat=channel_layouts=mono,aresample=4000[mix]",
                    "-map", "[mix]",
                ])
            args.extend(["-f", "s16le", "-acodec", "pcm_s16le", str(self._wave_path)])
            self._wave_proc = QtCore.QProcess(self)
            self._wave_proc.finished.connect(self._waveform_finished)
            self._wave_proc.start(str(self.request.get("ffmpeg") or "ffmpeg"), args)

        def _waveform_finished(self, *_args):
            if not self._wave_path.exists() or self._wave_path.stat().st_size == 0:
                self.status.setText("Waveform preview could not be generated.")
                return
            try:
                data = self._wave_path.read_bytes()
                if not data:
                    return
                # Hand the raw mono PCM to the timeline; it renders a crisp,
                # detailed per-pixel waveform from it at any zoom.
                self.timeline.set_pcm(data, 4000)
            except Exception as exc:  # noqa: BLE001
                self.status.setText(f"Waveform preview could not be generated ({exc}).")

        def resizeEvent(self, event):
            super().resizeEvent(event)
            # Guard: a resize can fire from the early empty-shell show() before
            # _build_ui has created self.timeline.
            if hasattr(self, "timeline"):
                self.timeline.update()

        def keyPressEvent(self, event):
            if event.key() == Qt.Key_Alt:
                self._set_zoom_out_mode(True)
                event.accept()
                return
            modifiers = event.modifiers()
            ctrl = bool(modifiers & Qt.ControlModifier)
            shift = bool(modifiers & Qt.ShiftModifier)
            alt = bool(modifiers & Qt.AltModifier)
            vk = int(event.nativeVirtualKey() or 0)
            if self._text_input_has_focus() and not ctrl and not alt:
                super().keyPressEvent(event)
                return

            action = None
            if ctrl and shift and vk == WIN_VK["i"]:
                action = self.invert_cuts
            elif ctrl and shift and vk == WIN_VK["z"]:
                action = self._redo
            elif ctrl and alt and vk == WIN_VK["left"]:
                action = lambda: self.seek_nearest_cut_edge(-1)
            elif ctrl and alt and vk == WIN_VK["right"]:
                action = lambda: self.seek_nearest_cut_edge(1)
            elif ctrl and vk in (WIN_VK["i"], WIN_VK["o"]):
                action = self.convert_selected_marker
            elif ctrl and vk == WIN_VK["z"]:
                action = self._undo
            elif ctrl and vk == WIN_VK["y"]:
                action = self._redo
            elif ctrl and vk == WIN_VK["r"]:
                action = self.reset_crop
            elif ctrl and vk == WIN_VK["0"]:
                action = self.reset_view
            elif ctrl and vk == WIN_VK["u"]:
                action = self.toggle_crop_overlay
            elif ctrl and vk in (WIN_VK["plus"], WIN_VK["kp_add"]):
                action = self.preview_zoom_in
            elif ctrl and vk in (WIN_VK["minus"], WIN_VK["kp_subtract"]):
                action = self.preview_zoom_out
            elif not ctrl and not alt:
                action = {
                    WIN_VK["space"]: self.toggle_playback,
                    WIN_VK["i"]: self.mark_in,
                    WIN_VK["o"]: self.mark_out,
                    WIN_VK["a"]: self.add_cut,
                    WIN_VK["s"]: self.add_separator,
                    WIN_VK["delete"]: self.delete_selection,
                    WIN_VK["m"]: self.toggle_mute,
                    WIN_VK["home"]: lambda: self.seek(0.0),
                    WIN_VK["end"]: lambda: self.seek(self.duration),
                    WIN_VK["h"]: lambda: self.set_preview_tool("hand"),
                    WIN_VK["z"]: lambda: self.set_preview_tool("zoom"),
                    WIN_VK["plus"]: lambda: self._nudge_timeline_zoom(1),
                    WIN_VK["kp_add"]: lambda: self._nudge_timeline_zoom(1),
                    WIN_VK["equal"]: lambda: self._nudge_timeline_zoom(1),
                    WIN_VK["minus"]: lambda: self._nudge_timeline_zoom(-1),
                    WIN_VK["kp_subtract"]: lambda: self._nudge_timeline_zoom(-1),
                }.get(vk)
                if action is None and vk == WIN_VK["left"]:
                    action = (lambda: self.seek(self.current_time() - 5.0)) if shift else (lambda: self.seek(self.current_time() - 1.0))
                elif action is None and vk == WIN_VK["right"]:
                    action = (lambda: self.seek(self.current_time() + 5.0)) if shift else (lambda: self.seek(self.current_time() + 1.0))
            if action is not None:
                action()
                event.accept()
                return
            super().keyPressEvent(event)

        def keyReleaseEvent(self, event):
            if event.key() == Qt.Key_Alt:
                self._set_zoom_out_mode(False)
                event.accept()
                return
            super().keyReleaseEvent(event)

        def confirm(self):
            self._snap_crop_to_even()
            margins = self._normalize_crop_margins_even([int(v) for v in self.preview.margins])
            cuts = normalize_ranges(self._cut_ranges, self.duration)
            keep_ranges = [[float(s), float(e)] for s, e in (invert_cuts_to_keep(cuts, self.duration) if cuts else [])]
            speed = float(self._speed())
            reverse = bool(self.reverse_box.isChecked())
            include_audio = bool(self.include_audio_box.isChecked())
            self.result = {
                "status": "ok",
                "margins": margins,
                "keep_ranges": keep_ranges,
                # Tells the caller that an EMPTY keep list means "every frame is
                # cut", not "nothing was cut" (D13).
                "cuts_applied": bool(cuts),
                "separator_points": [float(v) for v in self._separator_points],
                "speed": speed,
                "reverse": reverse,
                "include_audio": include_audio,
            }
            self.close()

        def cancel(self):
            self.result = {"status": "canceled"}
            self.close()

        def closeEvent(self, event):
            self._closing = True
            try:
                self.player.stop()
            except Exception:
                pass
            # Stop the frame-extract QThread first — destroying a running QThread on
            # teardown crashes Qt (0xC0000409).
            try:
                self._stop_preview_frame_worker()
            except Exception:
                pass
            for _attr in ("_rev_proc", "_wave_proc"):
                try:
                    proc = getattr(self, _attr, None)
                    if proc is not None and proc.state() != QtCore.QProcess.NotRunning:
                        proc.kill()
                        proc.waitForFinished(1000)
                except Exception:
                    pass
            try:
                self._wave_temp.cleanup()
            except Exception:
                pass
            super().closeEvent(event)

    return UnifiedVideoEditorWindow(request)
