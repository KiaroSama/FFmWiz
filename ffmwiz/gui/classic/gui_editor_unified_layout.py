"""Widget tree for the classic unified video editor.

Split out of `gui_editor_unified.py`: everything that CONSTRUCTS the window --
the header, the four docked side panels, the preview/timeline splitter and the
footer -- lives here, leaving the builder module the window's own state and
lifecycle. A pure code move; the methods are unchanged.

The mixin is defined inside a factory rather than at module level because its
body binds Qt symbols, and PySide6 must stay importable-on-demand: most CI jobs
install no Qt at all.

Not in `ffmwiz_gui._MODULES`, so the assembled namespace is never injected
here: the shared helpers this file uses are imported by name instead.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.gui_common import _gui_log_debug, _import_qt
from ffmwiz.gui.gui_style import PALETTE


def build_unified_editor_layout_mixin(UnifiedPreviewCanvas, UnifiedTimelineWidget):
    """Return the mixin that builds the editor window's widgets.

    The two canvas/timeline classes are passed in rather than rebuilt here so
    the window and its panels share ONE class each -- their factories mint a
    fresh class per call.
    """
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    Qt = QtCore.Qt
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
    QSplitter = QtWidgets.QSplitter
    QFrame = QtWidgets.QFrame
    QStyle = QtWidgets.QStyle
    QSizePolicy = QtWidgets.QSizePolicy

    class UnifiedEditorLayoutMixin:
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
            self.include_audio_box.setChecked(bool(self.any_audio))
            self.include_audio_box.setEnabled(bool(self.any_audio))
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

    return UnifiedEditorLayoutMixin
