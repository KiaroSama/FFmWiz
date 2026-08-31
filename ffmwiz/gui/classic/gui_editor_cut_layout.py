"""Widget tree, layout and stylesheets for the classic cut editor window.

Split out of `gui_editor_cut.py`, where construction and behaviour shared one
1800-line builder. Only construction lives here: it takes the window and hangs
the finished widgets off it exactly as the original `_build_ui` method did,
which is why the parameter is named `window` rather than `self`.

The widget classes arrive as arguments rather than from their own factory --
calling that a second time would mint a second, unrelated set of classes.

Not in `ffmwiz_gui._MODULES`, so nothing is injected into this module; see
`gui_editor_cut_widgets.py` for why the shared helpers are imported by name.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.gui_common import _gui_log_debug, _import_qt
from ffmwiz.gui.gui_style import PALETTE


def build_cut_editor_layout(window, HeaderBand, StatusStrip, VideoPreview,
                            TimelineWidget):
    """Build the cut window's widgets onto `window` and wire their signals."""
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    Qt = QtCore.Qt

    QStyle = QtWidgets.QStyle
    QWidget = QtWidgets.QWidget
    QFrame = QtWidgets.QFrame
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QSlider = QtWidgets.QSlider
    QScrollBar = QtWidgets.QScrollBar
    QListWidget = QtWidgets.QListWidget
    QHBoxLayout = QtWidgets.QHBoxLayout
    QVBoxLayout = QtWidgets.QVBoxLayout
    QSizePolicy = QtWidgets.QSizePolicy

    layout_start = time.perf_counter()
    central = QWidget()
    central.setObjectName("central")
    window.setCentralWidget(central)
    root = QVBoxLayout(central)
    root.setContentsMargins(14, 10, 14, 10)
    root.setSpacing(8)

    window.btn_undo = QPushButton(window._icon("undo", QStyle.SP_ArrowBack), "Undo (Ctrl+Z)")
    window.btn_undo.setToolTip("Undo last GUI edit")
    window.btn_undo.clicked.connect(window._undo)
    window.btn_redo = QPushButton(window._icon("redo", QStyle.SP_ArrowForward), "Redo (Ctrl+Y)")
    window.btn_redo.setToolTip("Redo last undone GUI edit")
    window.btn_redo.clicked.connect(window._redo)
    window.btn_undo.setEnabled(False)
    window.btn_redo.setEnabled(False)

    header = HeaderBand(window.input_path.name, window.fps, window.duration,
                        window.btn_undo, window.btn_redo)
    root.addWidget(header)

    window.preview = VideoPreview()
    window.preview.clicked.connect(window.toggle_playback)
    root.addWidget(window.preview, 1)

    window.status_strip = StatusStrip(window.fps, window.duration)
    root.addWidget(window.status_strip)

    window.timeline = TimelineWidget(window.fps, window.duration)
    window.timeline.set_chapters(window.chapters)
    window.timeline.playhead_seek_requested.connect(window._on_timeline_seek)
    window.timeline.marker_selection_requested.connect(window._on_marker_selection_requested)
    window.timeline.marker_drag_started.connect(window._on_marker_drag_started)
    window.timeline.marker_drag_moved.connect(window._on_marker_drag_moved)
    window.timeline.marker_drag_finished.connect(window._on_marker_drag_finished)
    window.timeline.cut_selected.connect(window._on_cut_selected_from_timeline)
    window.timeline.cut_context_requested.connect(window._on_cut_context)
    window.timeline.view_changed.connect(window._sync_timeline_zoom_slider)
    window.timeline.view_changed.connect(window._sync_timeline_navigation_slider)
    root.addWidget(window.timeline)

    view_row = QHBoxLayout()
    view_row.setSpacing(8)
    nav_lbl = QLabel("Timeline view")
    nav_lbl.setObjectName("controlLabel")
    view_row.addWidget(nav_lbl)
    view_frame = QFrame()
    view_frame.setObjectName("timelineViewFrame")
    view_frame.setStyleSheet(f"""
        QFrame#timelineViewFrame {{
            background: #111820;
            border: 1px solid {PALETTE['border_strong']};
            border-radius: 9px;
        }}
    """)
    view_frame.setMinimumWidth(780)
    view_frame.setMaximumWidth(16777215)
    view_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    view_frame_layout = QHBoxLayout(view_frame)
    view_frame_layout.setContentsMargins(3, 1, 3, 1)
    view_frame_layout.setSpacing(3)
    window.btn_timeline_view_left = QPushButton("◀")
    window.btn_timeline_view_left.setObjectName("timelineViewArrow")
    window.btn_timeline_view_left.setFixedSize(14, 14)
    window.btn_timeline_view_left.setAutoRepeat(True)
    window.btn_timeline_view_left.setAutoRepeatDelay(220)
    window.btn_timeline_view_left.setAutoRepeatInterval(70)
    window.btn_timeline_view_left.setToolTip("Move timeline view left")
    window.btn_timeline_view_left.clicked.connect(lambda: window._nudge_timeline_view(-1))
    view_frame_layout.addWidget(window.btn_timeline_view_left)
    window.timeline_navigation_slider = QScrollBar(Qt.Horizontal)
    window.timeline_navigation_slider.setObjectName("timelineViewScroll")
    window.timeline_navigation_slider.setRange(0, 0)
    window.timeline_navigation_slider.setValue(0)
    window.timeline_navigation_slider.setTracking(True)
    window.timeline_navigation_slider.setMinimumWidth(520)
    window.timeline_navigation_slider.setStyleSheet("""
        QScrollBar:horizontal {
            background: transparent;
            border: none;
            border-radius: 8px;
            height: 16px;
            margin: 0;
        }
        QScrollBar::handle:horizontal {
            background: #7df58a;
            min-width: 120px;
            border-radius: 6px;
            margin: 2px 0;
        }
        QScrollBar::handle:horizontal:hover {
            background: #a6ffad;
        }
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {
            background: transparent;
            border: none;
            width: 0;
        }
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {
            background: #17202a;
            border-radius: 7px;
        }
    """)
    window.timeline_navigation_slider.setToolTip(
        "Timeline view: drag left/right to pan through the visible timeline window without moving the CTI.")
    window.timeline_navigation_slider.valueChanged.connect(window._on_timeline_navigation_slider)
    window.timeline_navigation_slider.installEventFilter(window)
    view_frame_layout.addWidget(window.timeline_navigation_slider, 1)
    window.btn_timeline_view_right = QPushButton("▶")
    window.btn_timeline_view_right.setObjectName("timelineViewArrow")
    window.btn_timeline_view_right.setFixedSize(14, 14)
    window.btn_timeline_view_right.setAutoRepeat(True)
    window.btn_timeline_view_right.setAutoRepeatDelay(220)
    window.btn_timeline_view_right.setAutoRepeatInterval(70)
    window.btn_timeline_view_right.setToolTip("Move timeline view right")
    window.btn_timeline_view_right.clicked.connect(lambda: window._nudge_timeline_view(1))
    view_frame_layout.addWidget(window.btn_timeline_view_right)
    view_row.addWidget(view_frame, 1)
    root.addLayout(view_row)

    zoom_row = QHBoxLayout()
    zoom_row.setSpacing(8)
    zoom_lbl = QLabel("Timeline zoom")
    zoom_lbl.setObjectName("controlLabel")
    zoom_row.addWidget(zoom_lbl)
    window.btn_timeline_zoom_left = QPushButton("◀")
    window.btn_timeline_zoom_left.setObjectName("timelineZoomArrow")
    window.btn_timeline_zoom_left.setFixedSize(14, 14)
    window.btn_timeline_zoom_left.setAutoRepeat(True)
    window.btn_timeline_zoom_left.setAutoRepeatDelay(220)
    window.btn_timeline_zoom_left.setAutoRepeatInterval(70)
    window.btn_timeline_zoom_left.setToolTip("Zoom timeline out")
    window.btn_timeline_zoom_left.clicked.connect(lambda: window._nudge_timeline_zoom(-1))
    zoom_row.addWidget(window.btn_timeline_zoom_left)
    window.timeline_zoom_slider = QSlider(Qt.Horizontal)
    window.timeline_zoom_slider.setObjectName("timelineZoomSlider")
    window.timeline_zoom_slider.setRange(0, 100)
    window.timeline_zoom_slider.setValue(0)
    window.timeline_zoom_slider.setTracking(True)
    window.timeline_zoom_slider.setMinimumWidth(780)
    window.timeline_zoom_slider.setMaximumWidth(16777215)
    window.timeline_zoom_slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    window.timeline_zoom_slider.setStyleSheet(f"""
        QSlider::groove:horizontal {{
            background: #334155;
            height: 6px;
            border-radius: 3px;
        }}
        QSlider::sub-page:horizontal {{
            background: {PALETTE['accent']};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            background: {PALETTE['accent_hover']};
            width: 44px;
            height: 12px;
            min-height: 12px;
            max-height: 12px;
            margin: -4px 0;
            border-radius: 6px;
            border: 1px solid {PALETTE['accent']};
        }}
        QSlider::handle:horizontal:hover {{
            background: {PALETTE['text']};
        }}
    """)
    window.timeline_zoom_slider.setToolTip(
        "Timeline zoom. Mouse wheel here zooms in/out; Shift=fast, Ctrl=fine.")
    window.timeline_zoom_slider.valueChanged.connect(window._on_timeline_zoom_slider)
    window.timeline_zoom_slider.installEventFilter(window)
    zoom_row.addWidget(window.timeline_zoom_slider, 1)
    window.btn_timeline_zoom_right = QPushButton("▶")
    window.btn_timeline_zoom_right.setObjectName("timelineZoomArrow")
    window.btn_timeline_zoom_right.setFixedSize(14, 14)
    window.btn_timeline_zoom_right.setAutoRepeat(True)
    window.btn_timeline_zoom_right.setAutoRepeatDelay(220)
    window.btn_timeline_zoom_right.setAutoRepeatInterval(70)
    window.btn_timeline_zoom_right.setToolTip("Zoom timeline in")
    window.btn_timeline_zoom_right.clicked.connect(lambda: window._nudge_timeline_zoom(1))
    zoom_row.addWidget(window.btn_timeline_zoom_right)
    root.addLayout(zoom_row)

    tip = QLabel(
        "Tip: Left-click a cut region to move the playhead. "
        "Right-click a cut region to select it. "
        "Ctrl+click marker = multi-select marker. "
        "Drag CTI upward to zoom in and downward to zoom out. "
        "Selecting an In/Out marker and pressing the opposite button converts its type."
    )
    tip.setObjectName("tip")
    tip.setWordWrap(True)
    root.addWidget(tip)

    # ----- Transport row -----
    row1 = QHBoxLayout()
    row1.setSpacing(6)
    window.btn_play = QPushButton(window._icon("play", QStyle.SP_MediaPlay), " Play (Space)")
    window.btn_play.setObjectName("primary")
    window.btn_play.setToolTip("Play / Pause (Space)")
    window.btn_play.clicked.connect(window.toggle_playback)
    row1.addWidget(window.btn_play)

    row1.addWidget(window._tbtn(window._icon("skip_backward", QStyle.SP_MediaSkipBackward),
                               "Home (Home)", "Jump to start (Home)", window.go_home))
    row1.addWidget(window._tbtn(window._icon("seek_backward", QStyle.SP_MediaSeekBackward),
                               "-5s (Shift+Left)", "Seek -5s (Shift+Left)",
                               lambda: window._seek_relative(-5.0)))
    row1.addWidget(window._tbtn(None, "-1s (Left)", "Seek -1s (Left)",
                               lambda: window._seek_relative(-1.0)))
    row1.addWidget(window._tbtn(None, "+1s (Right)", "Seek +1s (Right)",
                               lambda: window._seek_relative(1.0)))
    row1.addWidget(window._tbtn(window._icon("seek_forward", QStyle.SP_MediaSeekForward),
                               "+5s (Shift+Right)", "Seek +5s (Shift+Right)",
                               lambda: window._seek_relative(5.0)))
    row1.addWidget(window._tbtn(window._icon("skip_forward", QStyle.SP_MediaSkipForward),
                               "End (End)", "Jump to end (End)", window.go_end))
    row1.addStretch(1)
    row1.addWidget(window._tbtn(None, "◀◀ Prev (Ctrl+Left)",
                               "Snap CTI to previous marker (Ctrl+Left)",
                               window.snap_marker_prev))
    row1.addWidget(window._tbtn(None, "Next (Ctrl+Right) ▶▶",
                               "Snap CTI to next marker (Ctrl+Right)",
                               window.snap_marker_next))
    row1.addStretch(1)
    root.addLayout(row1)

    # ----- Marker / cut row -----
    row2 = QHBoxLayout()
    row2.setSpacing(6)
    window.btn_mark_in = QPushButton(window._icon("mark_in", None), " Mark In (I)")
    window.btn_mark_in.setObjectName("markIn")
    window.btn_mark_in.setToolTip(
        "Mark In at the current CTI/playhead (I). Uses the green In-marker color. "
        "If an Out marker is selected, converts it to In.")
    window.btn_mark_in.clicked.connect(window.mark_in)
    row2.addWidget(window.btn_mark_in)

    window.btn_mark_out = QPushButton(window._icon("mark_out", None), " Mark Out (O)")
    window.btn_mark_out.setObjectName("markOut")
    window.btn_mark_out.setToolTip(
        "Mark Out at the current CTI/playhead (O). Uses the amber Out-marker color. "
        "If an In marker is selected, converts it to Out.")
    window.btn_mark_out.clicked.connect(window.mark_out)
    row2.addWidget(window.btn_mark_out)

    row2.addStretch(1)

    window.btn_add_cut = QPushButton(window._icon("add_cut", None), " Add Cut(s) (A)")
    window.btn_add_cut.setObjectName("green")
    window.btn_add_cut.setToolTip(
        "Add a complete cut at the playhead (A). "
        "Places an In marker now and a paired Out 1 second later.")
    window.btn_add_cut.clicked.connect(window.add_cuts)
    row2.addWidget(window.btn_add_cut)

    window.btn_invert_cuts = QPushButton(window._icon("invert", None), " Invert Cuts (Ctrl+Shift+I)")
    window.btn_invert_cuts.setObjectName("purple")
    window.btn_invert_cuts.setToolTip(
        "Invert cut ranges: keep the currently selected cut ranges and remove everything else. "
        "Shortcut: Ctrl+Shift+I")
    window.btn_invert_cuts.clicked.connect(window.invert_cuts)
    row2.addWidget(window.btn_invert_cuts)
    row2.addSpacing(10)
    window.btn_delete_markers = QPushButton(
        window._icon("delete", QStyle.SP_TrashIcon), " Delete Selected Marker(s) (Del)")
    window.btn_delete_markers.setObjectName("danger")
    window.btn_delete_markers.setToolTip("Delete all selected markers (Del when markers are selected)")
    window.btn_delete_markers.clicked.connect(window.delete_selected_markers)
    row2.addWidget(window.btn_delete_markers)

    window.btn_delete_selected_cut = QPushButton(
        window._icon("delete", QStyle.SP_TrashIcon), " Delete Selected Cut (Del)")
    window.btn_delete_selected_cut.setObjectName("dangerCut")
    window.btn_delete_selected_cut.setToolTip("Delete the selected cut region (Del when a cut is selected)")
    window.btn_delete_selected_cut.clicked.connect(window.delete_selected_cut)
    row2.addWidget(window.btn_delete_selected_cut)

    window.btn_delete_all = QPushButton(
        window._icon("delete_all", QStyle.SP_TrashIcon), " Delete All Markers")
    window.btn_delete_all.setObjectName("dangerAlt")
    window.btn_delete_all.setToolTip("Remove every marker (asks for confirmation)")
    window.btn_delete_all.clicked.connect(window.delete_all_markers)
    row2.addWidget(window.btn_delete_all)
    row2.addStretch(1)
    root.addLayout(row2)

    # ----- Audio / zoom / confirmation row -----
    row3 = QHBoxLayout()
    row3.setSpacing(8)
    row3.addWidget(window._tbtn(window._icon("zoom_in", None), "Zoom In (+)",
                               "Zoom timeline in (+)",
                               window.timeline_zoom_in))
    row3.addWidget(window._tbtn(window._icon("zoom_out", None), "Zoom Out (-)",
                               "Zoom timeline out (-)",
                               window.timeline_zoom_out))
    row3.addWidget(window._tbtn(None, "Reset Zoom (Ctrl+R)",
                               "Reset zoom and view position (Ctrl+R)",
                               window.timeline_fit))
    row3.addSpacing(12)
    window.btn_mute = QPushButton(window._icon("volume_meter_3", QStyle.SP_MediaVolume), "  Mute (M)")
    window.btn_mute.setMinimumWidth(118)
    window.btn_mute.setIconSize(QtCore.QSize(20, 20))
    window.btn_mute.setToolTip("Mute / unmute (M)")
    window.btn_mute.clicked.connect(window.toggle_mute)
    row3.addWidget(window.btn_mute)
    window.volume_slider = QSlider(Qt.Horizontal)
    window.volume_slider.setObjectName("cutVolumeSlider")
    window.volume_slider.setRange(0, 100)
    window.volume_slider.setValue(60)
    window.volume_slider.setFixedWidth(130)
    window.volume_slider.setStyleSheet(f"""
        QSlider::groove:horizontal {{
            background: {PALETTE['timeline_track']};
            height: 6px;
            border-radius: 3px;
        }}
        QSlider::sub-page:horizontal {{
            background: {PALETTE['accent']};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            background: {PALETTE['accent_hover']};
            width: 28px;
            height: 12px;
            min-height: 12px;
            max-height: 12px;
            margin: -4px 0;
            border-radius: 6px;
            border: 1px solid {PALETTE['accent']};
        }}
        QSlider::handle:horizontal:hover {{
            background: {PALETTE['text']};
        }}
    """)
    window.volume_slider.setToolTip("Volume (mouse wheel works here)")
    window.volume_slider.valueChanged.connect(window._on_volume_changed)
    window.volume_slider.installEventFilter(window)
    row3.addWidget(window.volume_slider)
    window.volume_label = QLabel("60%")
    window.volume_label.setObjectName("dim")
    row3.addWidget(window.volume_label)
    row3.addStretch(1)
    window.btn_cancel = QPushButton("Cancel (Esc)")
    window.btn_cancel.setObjectName("danger")
    window.btn_cancel.clicked.connect(window.cancel)
    row3.addWidget(window.btn_cancel)
    window.btn_confirm = QPushButton(window._icon("check", QStyle.SP_DialogOkButton),
                                    " Confirm (Enter)")
    window.btn_confirm.setObjectName("primary")
    window.btn_confirm.clicked.connect(window.confirm)
    row3.addWidget(window.btn_confirm)
    root.addLayout(row3)

    # ----- Marker list -----
    header_lbl = QLabel("Cut ranges (derived from In→Out marker pairs)")
    header_lbl.setObjectName("dim")
    root.addWidget(header_lbl)
    window.cut_list = QListWidget()
    window.cut_list.setMinimumHeight(110)
    window.cut_list.itemSelectionChanged.connect(window._on_cut_list_select)
    root.addWidget(window.cut_list)
    _gui_log_debug(
        f"Cut GUI layout initialized in {time.perf_counter() - layout_start:.3f}s",
        force=True,
    )
