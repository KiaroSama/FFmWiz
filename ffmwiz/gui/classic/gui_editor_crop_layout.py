"""Widget tree and layout for the classic crop editor window.

Split out of `gui_editor_crop.py`, where construction and behaviour shared one
1100-line builder. Only construction lives here: it takes the window and hangs
the finished widgets off it exactly as the original `_build_ui` method did,
which is why the parameter is named `window` rather than `self`.

Not in `ffmwiz_gui._MODULES`, so nothing is injected into this module -- see
`gui_editor_crop_canvas.py` for why the shared helpers are imported by name.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.gui_common import _gui_log_debug, _import_qt
from ffmwiz.gui.gui_geometry import seconds_to_hmsf
from ffmwiz.gui.gui_style import PALETTE


def build_crop_editor_layout(window, CropCanvas):
    """Build the crop window's widgets onto `window`.

    `CropCanvas` is passed in rather than re-derived from its factory: calling
    that a second time would mint a second, unrelated class object.
    """
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    Qt = QtCore.Qt

    QApplication = QtWidgets.QApplication
    QStyle = QtWidgets.QStyle
    QWidget = QtWidgets.QWidget
    QFrame = QtWidgets.QFrame
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QSlider = QtWidgets.QSlider
    QComboBox = QtWidgets.QComboBox
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
    window.btn_undo.setToolTip("Undo last crop edit")
    window.btn_undo.clicked.connect(window._undo)
    window.btn_redo = QPushButton(window._icon("redo", QStyle.SP_ArrowForward), "Redo (Ctrl+Y)")
    window.btn_redo.setToolTip("Redo last undone crop edit")
    window.btn_redo.clicked.connect(window._redo)
    window.btn_undo.setEnabled(False)
    window.btn_redo.setEnabled(False)

    header = QFrame()
    header.setObjectName("header")
    hlay = QHBoxLayout(header)
    hlay.setContentsMargins(14, 10, 14, 10)
    hlay.setSpacing(10)
    title = QLabel("FFmWiz Crop Editor")
    title.setObjectName("title")
    hlay.addWidget(title)
    hlay.addSpacing(12)
    hlay.addWidget(window.btn_undo)
    hlay.addWidget(window.btn_redo)
    hlay.addStretch(1)
    info = QLabel(f"FPS  {window.fps:.3f}      •      Duration  "
                  f"{seconds_to_hmsf(window.duration, window.fps)}      •      Source  "
                  f"{window.input_path.name}")
    info.setObjectName("headerInfo")
    hlay.addWidget(info)
    root.addWidget(header)

    tool_row = QHBoxLayout()
    window.btn_hand = QPushButton(window._icon("hand_open", None), "  Hand Tool  (H)")
    window.btn_hand.setObjectName("tool")
    window.btn_hand.setProperty("active", "true")
    window.btn_hand.clicked.connect(window.activate_hand)
    tool_row.addWidget(window.btn_hand)
    window.btn_zoom = QPushButton(window._icon("zoom_in", None), "  Zoom Tool  (Z)")
    window.btn_zoom.setObjectName("tool")
    window.btn_zoom.clicked.connect(window.activate_zoom)
    tool_row.addWidget(window.btn_zoom)
    window.zoom_percent_box = QFrame()
    window.zoom_percent_box.setObjectName("zoomPercentBox")
    zoom_box_layout = QHBoxLayout(window.zoom_percent_box)
    zoom_box_layout.setContentsMargins(0, 0, 0, 0)
    zoom_box_layout.setSpacing(0)
    window.zoom_percent_combo = QComboBox()
    window.zoom_percent_combo.setObjectName("zoomPercentCombo")
    window.zoom_percent_combo.setEditable(True)
    window.zoom_percent_combo.setInsertPolicy(QComboBox.NoInsert)
    window.zoom_percent_combo.setFixedWidth(112)
    window.zoom_percent_combo.setToolTip(
        "Preview zoom presets. Type a percent value and press Enter to zoom manually."
    )
    window.zoom_percent_combo.addItems([f"{value}%" for value in window._zoom_presets])
    window.zoom_percent_combo.setCurrentText("100%")
    if window.zoom_percent_combo.lineEdit() is not None:
        window.zoom_percent_combo.lineEdit().installEventFilter(window)
        window.zoom_percent_combo.lineEdit().returnPressed.connect(window._apply_zoom_percent_text)
        window.zoom_percent_combo.lineEdit().editingFinished.connect(window._apply_zoom_percent_text)
    window.zoom_percent_combo.activated.connect(lambda _idx: window._apply_zoom_percent_text())
    zoom_box_layout.addWidget(window.zoom_percent_combo)
    tool_row.addWidget(window.zoom_percent_box)
    tool_row.addStretch(1)
    tool_row.addWidget(window._btn("Reset Zoom  (Ctrl+0)", window.reset_zoom))
    tool_row.addWidget(window._btn("Reset Crop  (Ctrl+R)", window.reset_crop))
    root.addLayout(tool_row)

    window.canvas = CropCanvas(window.source_w, window.source_h)
    window.canvas.margins_drag_started.connect(window._on_drag_started)
    window.canvas.margins_drag_finished.connect(window._on_drag_finished)
    window.canvas.margins_changed.connect(window._refresh_info)
    window.canvas.zoom_changed.connect(window._on_canvas_zoom_changed)
    window.canvas.request_toggle_playback.connect(window.toggle_playback)
    window.canvas.setFocusPolicy(Qt.StrongFocus)
    window.canvas.installEventFilter(window)
    QApplication.instance().installEventFilter(window)
    root.addWidget(window.canvas, 1)

    window.info_label = QLabel("")
    window.info_label.setObjectName("dim")
    root.addWidget(window.info_label)

    seek_row = QHBoxLayout()
    seek_row.setSpacing(6)
    window.btn_play = QPushButton(window._icon("play", QStyle.SP_MediaPlay), " Play  (Space)")
    window.btn_play.setObjectName("primary")
    window.btn_play.clicked.connect(window.toggle_playback)
    seek_row.addWidget(window.btn_play)
    seek_row.addWidget(window._btn("⏮  Home", window.go_home))
    seek_row.addWidget(window._btn("−10s (Shift+←)", lambda: window._seek_relative(-10.0)))
    seek_row.addWidget(window._btn("+10s (Shift+→)", lambda: window._seek_relative(10.0)))
    seek_row.addWidget(window._btn("End  ⏭", window.go_end))
    seek_row.addStretch(1)
    window.btn_mute = QPushButton(window._icon("volume_meter_3", QStyle.SP_MediaVolume), "  Mute (M)")
    window.btn_mute.setMinimumWidth(118)
    window.btn_mute.setIconSize(QtCore.QSize(20, 20))
    window.btn_mute.setToolTip("Mute / unmute (M)")
    window.btn_mute.clicked.connect(window.toggle_mute)
    seek_row.addWidget(window.btn_mute)
    window.volume_slider = QSlider(Qt.Horizontal)
    window.volume_slider.setRange(0, 100)
    window.volume_slider.setValue(60)
    window.volume_slider.setFixedWidth(160)
    window.volume_slider.valueChanged.connect(window._on_volume_changed)
    window.volume_slider.installEventFilter(window)
    seek_row.addWidget(window.volume_slider)
    window.volume_label = QLabel("60%")
    window.volume_label.setObjectName("dim")
    seek_row.addWidget(window.volume_label)
    root.addLayout(seek_row)

    time_row = QHBoxLayout()
    time_row.setSpacing(8)
    time_frame = QFrame()
    time_frame.setObjectName("timelineViewFrame")
    time_frame.setStyleSheet(f"""
        QFrame#timelineViewFrame {{
            background: #111820;
            border: 1px solid {PALETTE['border_strong']};
            border-radius: 9px;
        }}
    """)
    time_frame.setMinimumWidth(760)
    time_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    time_frame_layout = QHBoxLayout(time_frame)
    time_frame_layout.setContentsMargins(3, 1, 3, 1)
    time_frame_layout.setSpacing(3)
    window.btn_time_seek_left = QPushButton("◀")
    window.btn_time_seek_left.setObjectName("timelineZoomArrow")
    window.btn_time_seek_left.setFixedSize(14, 14)
    window.btn_time_seek_left.setAutoRepeat(True)
    window.btn_time_seek_left.setAutoRepeatDelay(220)
    window.btn_time_seek_left.setAutoRepeatInterval(70)
    window.btn_time_seek_left.setToolTip("Seek preview backward")
    window.btn_time_seek_left.clicked.connect(lambda: window._nudge_time_slider(-1))
    time_frame_layout.addWidget(window.btn_time_seek_left)
    window.time_slider = QSlider(Qt.Horizontal)
    window.time_slider.setRange(0, max(1, int(window.duration * 1000)))
    window.time_slider.setTracking(True)
    window.time_slider.setSingleStep(max(1, int(1000 / max(1.0, window.fps))))
    window.time_slider.setPageStep(5000)
    window.time_slider.valueChanged.connect(lambda v: window._seek(v / 1000.0))
    window.time_slider.sliderMoved.connect(lambda v: window._seek(v / 1000.0))
    window.time_slider.installEventFilter(window)
    time_frame_layout.addWidget(window.time_slider, 1)
    window.btn_time_seek_right = QPushButton("▶")
    window.btn_time_seek_right.setObjectName("timelineZoomArrow")
    window.btn_time_seek_right.setFixedSize(14, 14)
    window.btn_time_seek_right.setAutoRepeat(True)
    window.btn_time_seek_right.setAutoRepeatDelay(220)
    window.btn_time_seek_right.setAutoRepeatInterval(70)
    window.btn_time_seek_right.setToolTip("Seek preview forward")
    window.btn_time_seek_right.clicked.connect(lambda: window._nudge_time_slider(1))
    time_frame_layout.addWidget(window.btn_time_seek_right)
    time_row.addWidget(time_frame, 1)
    window.time_label = QLabel("")
    window.time_label.setObjectName("dim")
    window.time_label.setMinimumWidth(168)
    window.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    time_row.addWidget(window.time_label)
    root.addLayout(time_row)

    confirm_row = QHBoxLayout()
    tip = QLabel("Tip: Ctrl+drag moves crop. Mouse wheel over timeline seeks. Ctrl+(+) / Ctrl+(-) zooms preview.\n"
                 "Double-click Hand Tool resets zoom.")
    tip.setObjectName("tip")
    tip.setWordWrap(False)
    tip.setMinimumWidth(760)
    tip.setMinimumHeight(42)
    confirm_row.addWidget(tip, 1)
    confirm_row.addStretch(1)
    cancel_btn = QPushButton("Cancel  (Esc)")
    cancel_btn.setObjectName("danger")
    cancel_btn.clicked.connect(window.cancel)
    confirm_row.addWidget(cancel_btn)
    apply_btn = QPushButton(window._icon("check", QStyle.SP_DialogOkButton),
                             "  Apply  (Enter)")
    apply_btn.setObjectName("primary")
    apply_btn.clicked.connect(window.confirm)
    confirm_row.addWidget(apply_btn)
    root.addLayout(confirm_row)

    window._refresh_info()
    window._update_undo_redo_state()
    window._update_zoom_tool_icon()
    _gui_log_debug(
        f"Crop GUI layout initialized in {time.perf_counter() - layout_start:.3f}s",
        force=True,
    )
