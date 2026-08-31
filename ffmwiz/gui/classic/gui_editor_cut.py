from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403


def build_cut_editor(request: dict[str, Any]):
    QtCore, QtGui, QtWidgets, QtMultimedia = _import_qt()

    Qt = QtCore.Qt
    Signal = QtCore.Signal
    QUrl = QtCore.QUrl
    QPointF = QtCore.QPointF
    QRectF = QtCore.QRectF

    QColor = QtGui.QColor
    QFont = QtGui.QFont
    QPainter = QtGui.QPainter
    QPen = QtGui.QPen
    QBrush = QtGui.QBrush
    QPolygonF = QtGui.QPolygonF
    QPixmap = QtGui.QPixmap
    QKeySequence = QtGui.QKeySequence
    QShortcut = QtGui.QShortcut
    QAction = QtGui.QAction

    QApplication = QtWidgets.QApplication
    QStyle = QtWidgets.QStyle
    QMainWindow = QtWidgets.QMainWindow
    QWidget = QtWidgets.QWidget
    QFrame = QtWidgets.QFrame
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QSlider = QtWidgets.QSlider
    QScrollBar = QtWidgets.QScrollBar
    QListWidget = QtWidgets.QListWidget
    QListWidgetItem = QtWidgets.QListWidgetItem
    QHBoxLayout = QtWidgets.QHBoxLayout
    QVBoxLayout = QtWidgets.QVBoxLayout
    QSizePolicy = QtWidgets.QSizePolicy
    QMessageBox = QtWidgets.QMessageBox
    QMenu = QtWidgets.QMenu
    QGraphicsOpacityEffect = QtWidgets.QGraphicsOpacityEffect

    class HeaderBand(QFrame):
        def __init__(self, file_name, fps, duration, btn_undo, btn_redo):
            super().__init__()
            self.setObjectName("header")
            self.setFrameShape(QFrame.NoFrame)
            lay = QHBoxLayout(self)
            lay.setContentsMargins(14, 10, 14, 10)
            lay.setSpacing(10)
            title = QLabel("FFmWiz Cut Editor")
            title.setObjectName("title")
            lay.addWidget(title)
            lay.addStretch(1)
            lay.addWidget(btn_undo)
            lay.addWidget(btn_redo)
            lay.addStretch(1)
            info = QLabel(
                f"FPS  {fps:.3f}      •      Duration  {seconds_to_timecode(duration)}"
                f"      •      Source  {file_name}"
            )
            info.setObjectName("headerInfo")
            lay.addWidget(info)

    class StatusStrip(QFrame):
        def __init__(self, fps, duration):
            super().__init__()
            self.setObjectName("statusBand")
            self._fps = fps
            self._duration = duration
            self._label = QLabel("")
            self._label.setObjectName("status")
            lay = QHBoxLayout(self)
            lay.setContentsMargins(12, 6, 12, 6)
            lay.addWidget(self._label)
            self.update_status(0.0, None, None, [], 1.0)

        def update_status(self, now, in_m, out_m, cuts, zoom_ratio):
            kept = self._duration - sum(max(0.0, e - s) for s, e in cuts)
            zoom_pct = int(round(100.0 / max(0.001, zoom_ratio)))
            in_text = seconds_to_hmsf(in_m, self._fps) if in_m is not None else "—"
            out_text = seconds_to_hmsf(out_m, self._fps) if out_m is not None else "—"
            self._label.setText(
                f"●  Now {seconds_to_hmsf(now, self._fps)}    "
                f"{seconds_to_timecode(now)} / {seconds_to_timecode(self._duration)}"
                f"      ●  In {in_text}      ●  Out {out_text}"
                f"      ●  Kept {seconds_to_timecode(max(0.0, kept))}"
                f"      ●  Cuts {len(cuts)}"
                f"      ●  Zoom {zoom_pct}%"
            )

    class VideoPreview(QLabel):
        clicked = Signal()

        def __init__(self):
            super().__init__()
            self.setAlignment(Qt.AlignCenter)
            self.setMinimumSize(480, 270)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setStyleSheet(
                f"background-color: {PALETTE['timeline_bg']};"
                f"border: 1px solid {PALETTE['border']}; border-radius: 8px;"
            )
            self._press_pos = None
            self._dragged = False
            self._latest = None

        def on_frame(self, frame):
            if frame is None or not frame.isValid():
                return
            image = frame.toImage()
            if image.isNull():
                return
            self._latest = QPixmap.fromImage(image)
            self._render()

        def _render(self):
            if self._latest is None:
                return
            self.setPixmap(self._latest.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation,
            ))

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self._render()

        def mousePressEvent(self, event):
            self._press_pos = event.position()
            self._dragged = False
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event):
            if self._press_pos is not None:
                d = event.position() - self._press_pos
                if abs(d.x()) + abs(d.y()) > 6:
                    self._dragged = True
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event):
            if self._press_pos is not None and not self._dragged \
               and event.button() == Qt.LeftButton:
                self.clicked.emit()
            self._press_pos = None
            super().mouseReleaseEvent(event)

    @dataclass
    class TimelineState:
        duration: float = 0.0
        fps: float = 25.0
        playhead: float = 0.0
        markers: list = field(default_factory=list)
        cuts: list = field(default_factory=list)
        chapters: list = field(default_factory=list)
        selected_marker_ids: set = field(default_factory=set)
        selected_cut: int = -1
        view_start: float = 0.0
        view_span: float = 0.0

    class TimelineWidget(QWidget):
        playhead_seek_requested = Signal(float)
        marker_drag_started = Signal()
        marker_drag_finished = Signal()
        marker_drag_moved = Signal(int, float)
        marker_selection_requested = Signal(int, bool)
        cut_selected = Signal(int)
        cut_context_requested = Signal(int, QtCore.QPoint)
        view_changed = Signal()

        PAD = 16
        TRACK_TOP = 40
        BOTTOM_PAD = 16
        MARKER_HIT_PX = 14

        def __init__(self, fps, duration):
            super().__init__()
            self.state = TimelineState(
                duration=duration, fps=fps,
                view_start=0.0, view_span=max(0.001, duration),
            )
            self.setMinimumHeight(120)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.setMouseTracking(True)
            self.setStyleSheet(
                f"background-color: {PALETTE['timeline_bg']};"
                f"border: 1px solid {PALETTE['border']}; border-radius: 8px;"
            )
            self._drag_target = None
            self._drag_in_progress = False
            self._cti_zoom_active = False

        def set_markers(self, markers, selected_ids):
            self.state.markers = list(markers)
            if isinstance(selected_ids, int):
                selected = set() if selected_ids < 0 else {selected_ids}
            else:
                selected = {int(x) for x in (selected_ids or [])}
            self.state.selected_marker_ids = selected
            self.state.cuts = compute_cut_ranges(self.state.markers, self.state.duration)
            self.update()

        def set_selected_cut(self, idx):
            self.state.selected_cut = idx
            self.update()

        def set_chapters(self, chapters):
            self.state.chapters = list(chapters or [])
            self.update()

        def _clamp_view(self):
            duration = max(0.001, self.state.duration)
            self.state.view_span = max(0.05, min(duration, self.state.view_span or duration))
            self.state.view_start = max(0.0, min(max(0.0, duration - self.state.view_span), self.state.view_start))

        def _ensure_time_visible(self, t, force=False):
            self._clamp_view()
            span = self.state.view_span
            start = self.state.view_start
            end = start + span
            margin = span * (0.18 if not force else 0.30)
            if force or t < start + margin:
                self.state.view_start = max(0.0, t - span * 0.30)
            elif t > end - margin:
                self.state.view_start = min(max(0.0, self.state.duration - span), t - span * 0.70)
            self._clamp_view()

        def set_playhead(self, t, follow=True, force_visible=False):
            old_start = self.state.view_start
            old_span = self.state.view_span
            self.state.playhead = max(0.0, min(self.state.duration, float(t)))
            if follow:
                self._ensure_time_visible(self.state.playhead, force=force_visible)
            self.update()
            if abs(self.state.view_start - old_start) > 1e-6 or abs(self.state.view_span - old_span) > 1e-6:
                self.view_changed.emit()

        def fit_view(self):
            self.state.view_start = 0.0
            self.state.view_span = max(0.001, self.state.duration)
            self.update()
            self.view_changed.emit()

        def zoom_ratio(self):
            return max(1.0, self.state.duration / max(0.001, self.state.view_span))

        def set_zoom_ratio(self, ratio, focus_time=None):
            duration = max(0.001, self.state.duration)
            ratio = max(1.0, min(64.0, float(ratio)))
            focus = self.state.playhead if focus_time is None else max(0.0, min(duration, float(focus_time)))
            span = max(0.05, min(duration, duration / ratio))
            old_span = max(0.001, self.state.view_span)
            old_ratio = (focus - self.state.view_start) / old_span
            self.state.view_span = span
            self.state.view_start = focus - old_ratio * span
            self._ensure_time_visible(self.state.playhead)
            self.update()
            self.view_changed.emit()

        def zoom_around(self, focus_time, factor):
            span = max(0.001, self.state.view_span)
            duration = max(0.001, self.state.duration)
            ratio = (focus_time - self.state.view_start) / span
            new_span = max(0.05, min(duration, span * factor))
            self.state.view_span = new_span
            self.state.view_start = focus_time - ratio * new_span
            self._clamp_view()
            self.update()
            self.view_changed.emit()

        def zoom_in_at_playhead(self):
            self.zoom_around(self.state.playhead, 0.86)

        def zoom_out_at_playhead(self):
            self.zoom_around(self.state.playhead, 1.16)

        def scroll_view(self, seconds):
            self._clamp_view()
            duration = max(0.001, self.state.duration)
            self.state.view_start = max(
                0.0,
                min(max(0.0, duration - self.state.view_span),
                    self.state.view_start + float(seconds)),
            )
            self.update()
            self.view_changed.emit()

        def view_position_ratio(self):
            self._clamp_view()
            max_start = max(0.0, self.state.duration - self.state.view_span)
            if max_start <= 1e-6:
                return 0.0
            return max(0.0, min(1.0, self.state.view_start / max_start))

        def set_view_position_ratio(self, ratio):
            self._clamp_view()
            max_start = max(0.0, self.state.duration - self.state.view_span)
            self.state.view_start = max_start * max(0.0, min(1.0, float(ratio)))
            self._clamp_view()
            self.update()
            self.view_changed.emit()

        def set_view_start(self, start):
            old_start = self.state.view_start
            self.state.view_start = float(start)
            self._clamp_view()
            self.update()
            if abs(self.state.view_start - old_start) > 1e-6:
                self.view_changed.emit()

        def set_view_span_around(self, focus_time, target_span, focus_ratio=None):
            duration = max(0.001, self.state.duration)
            old_span = max(0.001, self.state.view_span)
            if focus_ratio is None:
                focus_ratio = (focus_time - self.state.view_start) / old_span
            focus_ratio = max(0.0, min(1.0, float(focus_ratio)))
            self.state.view_span = max(0.05, min(duration, float(target_span)))
            self.state.view_start = focus_time - focus_ratio * self.state.view_span
            self._clamp_view()
            self.update()
            self.view_changed.emit()

        def _usable_width(self):
            return max(1, self.width() - self.PAD * 2)

        def _time_to_x(self, t):
            return self.PAD + ((t - self.state.view_start)
                               / max(0.001, self.state.view_span)) * self._usable_width()

        def _x_to_time(self, x):
            ratio = max(0.0, min(1.0, (x - self.PAD) / self._usable_width()))
            return self.state.view_start + ratio * self.state.view_span

        def paintEvent(self, _event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.fillRect(self.rect(), QColor(PALETTE["timeline_bg"]))

            w = self.width()
            h = self.height()
            track_top = self.TRACK_TOP
            track_bottom = h - self.BOTTOM_PAD

            painter.setPen(QPen(QColor(PALETTE["border"]), 1))
            painter.setBrush(QBrush(QColor(PALETTE["timeline_track"])))
            painter.drawRoundedRect(
                QRectF(self.PAD - 2, track_top,
                       w - (self.PAD - 2) * 2, track_bottom - track_top), 4, 4,
            )

            span = max(0.001, self.state.view_span)
            approx_step = span / 8.0
            exponent = math.floor(math.log10(max(approx_step, 0.001)))
            base = 10 ** exponent
            step = base
            for candidate in (1, 2, 5, 10):
                step = candidate * base
                if span / step <= 10:
                    break
            start_t = self.state.view_start
            end_t = start_t + span
            painter.setFont(QFont("Segoe UI Semibold", 9))
            tick_pen = QPen(QColor(PALETTE["tick_hi"]), 1)
            t = math.ceil(start_t / step) * step
            while t <= end_t + 1e-6:
                x = self._time_to_x(t)
                if self.PAD <= x <= w - self.PAD:
                    painter.setPen(tick_pen)
                    painter.drawLine(QPointF(x, track_top - 6), QPointF(x, track_top))
                    label = seconds_to_timecode(t)
                    label_w = max(110, painter.fontMetrics().horizontalAdvance(label) + 10)
                    label_w = min(label_w, max(1, w - self.PAD * 2))
                    label_left = max(
                        self.PAD,
                        min(x - label_w / 2.0, w - self.PAD - label_w),
                    )
                    painter.drawText(QRectF(label_left, 6, label_w, 20),
                                     Qt.AlignCenter, label)
                t += step
            minor_step = step / 5.0 if step > 0 else 0.0
            if minor_step > 0:
                t = math.ceil(start_t / minor_step) * minor_step
                while t <= end_t + 1e-6:
                    x = self._time_to_x(t)
                    if self.PAD <= x <= w - self.PAD:
                        painter.setPen(QPen(QColor(PALETTE["tick_lo"]), 1))
                        painter.drawLine(QPointF(x, track_top - 3), QPointF(x, track_top))
                    t += minor_step

            for chapter in self.state.chapters:
                cs = float(chapter.get("start", 0.0))
                if cs < start_t or cs > end_t:
                    continue
                x = self._time_to_x(cs)
                painter.setPen(QPen(QColor(PALETTE["purple_hover"]), 1, Qt.DashLine))
                painter.drawLine(QPointF(x, track_top), QPointF(x, track_bottom))
                title = str(chapter.get("title") or "Chapter")
                label = short_gui_label(title, 24)
                painter.setFont(QFont("Segoe UI Semibold", 8))
                painter.setPen(QPen(QColor(PALETTE["accent_text"]), 1))
                painter.drawText(
                    QRectF(max(self.PAD, min(x + 4, w - self.PAD - 150)), track_top + 4, 150, 18),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    label,
                )

            for idx, (cs, ce) in enumerate(self.state.cuts):
                if ce < start_t or cs > end_t:
                    continue
                x1 = self._time_to_x(max(cs, start_t))
                x2 = self._time_to_x(min(ce, end_t))
                selected = idx == self.state.selected_cut
                color = QColor(PALETTE["cut_red"] if selected else PALETTE["cut_red_dim"])
                painter.setPen(QPen(QColor(PALETTE["border"]), 1 if not selected else 2))
                painter.setBrush(QBrush(color))
                painter.drawRoundedRect(
                    QRectF(x1, track_top + 3, x2 - x1, track_bottom - track_top - 6), 3, 3,
                )
                if x2 - x1 > 36:
                    painter.setPen(QPen(QColor(PALETTE["text"]), 1))
                    painter.setFont(QFont("Segoe UI Semibold", 10))
                    painter.drawText(
                        QRectF(x1, track_top, x2 - x1, track_bottom - track_top),
                        Qt.AlignCenter, f"#{idx + 1}",
                    )

            for marker in sorted(self.state.markers, key=lambda m: m.time):
                self._draw_marker(painter, marker, track_top, track_bottom)

            ph_x = self._time_to_x(self.state.playhead)
            if self.PAD - 4 <= ph_x <= w - self.PAD + 4:
                painter.setPen(QPen(QColor(PALETTE["playhead"]), 2))
                painter.drawLine(QPointF(ph_x, track_top - 10),
                                 QPointF(ph_x, track_bottom + 10))
                painter.setBrush(QBrush(QColor(PALETTE["playhead"])))
                painter.setPen(QPen(QColor(PALETTE["bg"]), 1))
                painter.drawPolygon(QPolygonF([
                    QPointF(ph_x - 7, track_bottom + 4),
                    QPointF(ph_x + 7, track_bottom + 4),
                    QPointF(ph_x, track_bottom + 14),
                ]))

        def _draw_marker(self, painter, marker, track_top, track_bottom):
            x = self._time_to_x(marker.time)
            w = self.width()
            if not (self.PAD - 10 <= x <= w - self.PAD + 10):
                return
            color_hex = PALETTE["marker_in"] if marker.kind == "in" else PALETTE["marker_out"]
            selected = marker.id in self.state.selected_marker_ids
            painter.setPen(QPen(QColor(color_hex), 3 if not selected else 5))
            painter.drawLine(QPointF(x, track_top - 4), QPointF(x, track_bottom + 4))
            painter.setBrush(QBrush(QColor(color_hex)))
            border = QColor(PALETTE["accent_text"] if selected else PALETTE["bg"])
            painter.setPen(QPen(border, 3 if selected else 1))
            painter.drawPolygon(QPolygonF([
                QPointF(x, track_top - 4),
                QPointF(x - 10, track_top - 22),
                QPointF(x + 10, track_top - 22),
            ]))
            painter.setPen(QPen(QColor(color_hex), 1))
            painter.setFont(QFont("Segoe UI Semibold", 9))
            label = "IN" if marker.kind == "in" else "OUT"
            painter.drawText(QPointF(x + 12 if marker.kind == "in" else x - 38,
                                     track_top - 8), label)

        def _hit_marker(self, x, y):
            if not (self.TRACK_TOP - 26 <= y <= self.TRACK_TOP + 6):
                return None
            best = None
            best_dx = 1e9
            for marker in self.state.markers:
                mx = self._time_to_x(marker.time)
                dx = abs(x - mx)
                if dx <= self.MARKER_HIT_PX and dx < best_dx:
                    best = marker
                    best_dx = dx
            return best

        def _hit_cut(self, x, y):
            track_top = self.TRACK_TOP + 2
            track_bottom = self.height() - self.BOTTOM_PAD - 2
            if not (track_top <= y <= track_bottom):
                return -1
            start_t = self.state.view_start
            end_t = start_t + self.state.view_span
            for idx, (cs, ce) in enumerate(self.state.cuts):
                if ce < start_t or cs > end_t:
                    continue
                x1 = self._time_to_x(max(cs, start_t))
                x2 = self._time_to_x(min(ce, end_t))
                if x1 - 2 <= x <= x2 + 2:
                    return idx
            return -1

        def _hit_playhead(self, x, y):
            ph_x = self._time_to_x(self.state.playhead)
            return (
                abs(x - ph_x) <= 12
                and self.TRACK_TOP - 16 <= y <= self.height() - self.BOTTOM_PAD + 18
            )

        def mousePressEvent(self, event):
            pos = event.position()
            x, y = pos.x(), pos.y()
            ctrl = bool(event.modifiers() & Qt.ControlModifier)

            if event.button() == Qt.RightButton:
                cut_idx = self._hit_cut(x, y)
                if cut_idx >= 0:
                    self.cut_selected.emit(cut_idx)
                    self.cut_context_requested.emit(cut_idx, event.globalPosition().toPoint())
                    return
                marker = self._hit_marker(x, y)
                if marker is not None:
                    self.marker_selection_requested.emit(marker.id, False)
                return

            if event.button() != Qt.LeftButton:
                super().mousePressEvent(event)
                return

            marker = self._hit_marker(x, y)
            if marker is not None:
                self.marker_selection_requested.emit(marker.id, ctrl)
                self._drag_target = ("marker", marker.id)
                self._drag_in_progress = False
                return

            if self._hit_playhead(x, y):
                self._drag_target = (
                    "cti",
                    QPointF(x, y),
                    self.state.playhead,
                    self.state.view_start,
                    max(0.001, self.state.view_span),
                )
                self._drag_in_progress = False
                self._cti_zoom_active = False
                self.marker_selection_requested.emit(-1, False)
                self.cut_selected.emit(-1)
                return

            t = self._x_to_time(x)
            self.set_playhead(t)
            self.playhead_seek_requested.emit(self.state.playhead)
            self._drag_target = (
                "playhead",
                QPointF(x, y),
                self.state.view_start,
                max(0.001, self.state.view_span),
            )
            self._drag_in_progress = False
            self._cti_zoom_active = False
            self.marker_selection_requested.emit(-1, False)
            self.cut_selected.emit(-1)

        def mouseMoveEvent(self, event):
            if self._drag_target is None:
                return
            t = self._x_to_time(event.position().x())
            kind = self._drag_target[0]
            if kind == "playhead":
                press_pos = self._drag_target[1] if len(self._drag_target) > 1 else event.position()
                start_span = self._drag_target[3] if len(self._drag_target) > 3 else max(0.001, self.state.view_span)
                dy = event.position().y() - press_pos.y()
                if self._cti_zoom_active or abs(dy) >= 4:
                    self._cti_zoom_active = True
                    current_focus = max(0.0, min(self.state.duration, t))
                    focus_ratio = max(0.0, min(1.0, (event.position().x() - self.PAD) / self._usable_width()))
                    target_span = start_span * (2.0 ** (dy / 150.0))
                    self.set_playhead(current_focus, follow=False)
                    self.playhead_seek_requested.emit(self.state.playhead)
                    self.set_view_span_around(current_focus, target_span, focus_ratio)
                    self._drag_in_progress = True
                else:
                    self.set_playhead(t)
                    self.playhead_seek_requested.emit(self.state.playhead)
            elif kind == "cti":
                press_pos = self._drag_target[1]
                start_span = self._drag_target[4]
                dy = event.position().y() - press_pos.y()
                if self._cti_zoom_active or abs(dy) >= 4:
                    self._cti_zoom_active = True
                    current_focus = max(0.0, min(self.state.duration, t))
                    focus_ratio = max(0.0, min(1.0, (event.position().x() - self.PAD) / self._usable_width()))
                    target_span = start_span * (2.0 ** (dy / 150.0))
                    self.set_playhead(current_focus, follow=False)
                    self.playhead_seek_requested.emit(self.state.playhead)
                    self.set_view_span_around(current_focus, target_span, focus_ratio)
                    self._drag_in_progress = True
                else:
                    self.set_playhead(t)
                    self.playhead_seek_requested.emit(self.state.playhead)
            elif kind == "marker":
                if not self._drag_in_progress:
                    self.marker_drag_started.emit()
                    self._drag_in_progress = True
                marker_id = self._drag_target[1]
                self.marker_drag_moved.emit(marker_id, max(0.0, min(self.state.duration, t)))

        def mouseReleaseEvent(self, event):
            if (self._drag_target is not None
                    and self._drag_target[0] == "marker"
                    and self._drag_in_progress):
                self.marker_drag_finished.emit()
            self._drag_target = None
            self._drag_in_progress = False
            self._cti_zoom_active = False

        def wheelEvent(self, event):
            delta = event.angleDelta().y()
            if delta == 0:
                return
            if event.modifiers() & Qt.ControlModifier:
                factor_base = 1.25
                factor = 1.0 / factor_base if delta > 0 else factor_base
                self.zoom_around(self._x_to_time(event.position().x()), factor)
                return
            step = 10.0 if (event.modifiers() & Qt.ShiftModifier) else 5.0
            direction = 1.0 if delta > 0 else -1.0
            self.playhead_seek_requested.emit(max(0.0, min(self.state.duration, self.state.playhead + direction * step)))
    class CutEditorWindow(QMainWindow):
        def __init__(self, request):
            init_start = time.perf_counter()
            super().__init__()
            self.request = request
            self.input_path = Path(request["input_path"])
            requested_fps = request.get("fps")
            self.fps = float(requested_fps or 25.0)
            if not requested_fps:
                print("Cut Editor FPS fallback: using 25.000 fps.", file=sys.stderr)
            self.duration = float(request.get("duration") or 0.0)
            self.chapters = normalize_chapters(request.get("chapters") or [], self.duration)
            self._log_path = Path(request["log_path"]) if request.get("log_path") else None
            self.result = {"status": "canceled", "keep_ranges": []}

            # Marker model state. Cut ranges are derived from the markers.
            self._next_marker_id = 1
            self._markers: list = []
            self._selected_marker_ids: set[int] = set()
            self._selected_cut = -1
            self._drag_snapshot = None
            self._syncing_timeline_zoom = False
            self._syncing_timeline_navigation = False
            self._media_ready = False

            self._history = HistoryStack(self._take_snapshot(), max_size=100)

            self.setWindowTitle("FFmWiz Cut Editor")
            self._icon = _icon_loader(self, self.style())
            _apply_window_icon(self, self._icon)
            self.setMinimumSize(1180, 860)
            self.resize(1360, 900)

            self._build_ui()
            self._install_shortcuts()
            self._refresh_all()
            _gui_log_debug(
                f"Cut GUI widgets initialized in {time.perf_counter() - init_start:.3f}s",
                force=True,
            )
            QtCore.QTimer.singleShot(150, self._deferred_media_startup)

        def _deferred_media_startup(self):
            start = time.perf_counter()
            self._setup_player()
            self._media_ready = True
            self._refresh_all()
            _gui_log_debug(
                f"Cut GUI media startup completed in {time.perf_counter() - start:.3f}s",
                force=True,
            )

        def _take_snapshot(self):
            return CutSnapshot(
                markers=[copy.copy(m) for m in self._markers],
                selected_marker_ids=tuple(sorted(self._selected_marker_ids)),
            )

        def _commit_history(self):
            self._history.push(self._take_snapshot())
            self._update_undo_redo_state()

        def _restore_snapshot(self, snap):
            self._markers = [copy.copy(m) for m in snap.markers]
            self._selected_marker_ids = set(getattr(snap, "selected_marker_ids", ()))
            self._selected_cut = -1
            self._refresh_all_no_history()

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

        def _apply_disabled_opacity(self, button):
            if button.isEnabled():
                button.setGraphicsEffect(None)
                return
            effect = button.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                effect = QGraphicsOpacityEffect(button)
                button.setGraphicsEffect(effect)
            effect.setOpacity(0.32)

        def _selected_marker(self):
            if len(self._selected_marker_ids) != 1:
                return None
            marker_id = next(iter(self._selected_marker_ids))
            for m in self._markers:
                if m.id == marker_id:
                    return m
            return None

        def _set_selected_marker(self, marker_id, additive=False):
            if marker_id < 0:
                self._selected_marker_ids.clear()
            elif additive:
                if marker_id in self._selected_marker_ids:
                    self._selected_marker_ids.remove(marker_id)
                else:
                    self._selected_marker_ids.add(marker_id)
            else:
                self._selected_marker_ids = {marker_id}
            self._selected_cut = -1
            self._refresh_all_no_history()

        def _clear_marker_selection(self):
            self._selected_marker_ids.clear()

        def _add_marker(self, time, kind):
            m = Marker(id=self._next_marker_id, time=time, kind=kind)
            self._next_marker_id += 1
            self._markers.append(m)
            return m

        def _replace_cut_ranges_with_markers(self, ranges):
            self._markers = []
            for start, end in normalize_ranges(ranges, self.duration):
                self._add_marker(start, "in")
                self._add_marker(end, "out")

        def _cuts(self):
            return compute_cut_ranges(self._markers, self.duration)

        def _log_debug(self, message):
            line = f"Cut Editor DEBUG: {message}\n"
            if self._log_path is not None:
                try:
                    with self._log_path.open("a", encoding="utf-8") as handle:
                        handle.write(line)
                    return
                except Exception:
                    pass
            print(line.rstrip(), file=sys.stderr)

        def _build_ui(self):
            layout_start = time.perf_counter()
            central = QWidget()
            central.setObjectName("central")
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(14, 10, 14, 10)
            root.setSpacing(8)

            self.btn_undo = QPushButton(self._icon("undo", QStyle.SP_ArrowBack), "Undo (Ctrl+Z)")
            self.btn_undo.setToolTip("Undo last GUI edit")
            self.btn_undo.clicked.connect(self._undo)
            self.btn_redo = QPushButton(self._icon("redo", QStyle.SP_ArrowForward), "Redo (Ctrl+Y)")
            self.btn_redo.setToolTip("Redo last undone GUI edit")
            self.btn_redo.clicked.connect(self._redo)
            self.btn_undo.setEnabled(False)
            self.btn_redo.setEnabled(False)

            header = HeaderBand(self.input_path.name, self.fps, self.duration,
                                self.btn_undo, self.btn_redo)
            root.addWidget(header)

            self.preview = VideoPreview()
            self.preview.clicked.connect(self.toggle_playback)
            root.addWidget(self.preview, 1)

            self.status_strip = StatusStrip(self.fps, self.duration)
            root.addWidget(self.status_strip)

            self.timeline = TimelineWidget(self.fps, self.duration)
            self.timeline.set_chapters(self.chapters)
            self.timeline.playhead_seek_requested.connect(self._on_timeline_seek)
            self.timeline.marker_selection_requested.connect(self._on_marker_selection_requested)
            self.timeline.marker_drag_started.connect(self._on_marker_drag_started)
            self.timeline.marker_drag_moved.connect(self._on_marker_drag_moved)
            self.timeline.marker_drag_finished.connect(self._on_marker_drag_finished)
            self.timeline.cut_selected.connect(self._on_cut_selected_from_timeline)
            self.timeline.cut_context_requested.connect(self._on_cut_context)
            self.timeline.view_changed.connect(self._sync_timeline_zoom_slider)
            self.timeline.view_changed.connect(self._sync_timeline_navigation_slider)
            root.addWidget(self.timeline)

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
            self.btn_timeline_view_left = QPushButton("◀")
            self.btn_timeline_view_left.setObjectName("timelineViewArrow")
            self.btn_timeline_view_left.setFixedSize(14, 14)
            self.btn_timeline_view_left.setAutoRepeat(True)
            self.btn_timeline_view_left.setAutoRepeatDelay(220)
            self.btn_timeline_view_left.setAutoRepeatInterval(70)
            self.btn_timeline_view_left.setToolTip("Move timeline view left")
            self.btn_timeline_view_left.clicked.connect(lambda: self._nudge_timeline_view(-1))
            view_frame_layout.addWidget(self.btn_timeline_view_left)
            self.timeline_navigation_slider = QScrollBar(Qt.Horizontal)
            self.timeline_navigation_slider.setObjectName("timelineViewScroll")
            self.timeline_navigation_slider.setRange(0, 0)
            self.timeline_navigation_slider.setValue(0)
            self.timeline_navigation_slider.setTracking(True)
            self.timeline_navigation_slider.setMinimumWidth(520)
            self.timeline_navigation_slider.setStyleSheet("""
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
            self.timeline_navigation_slider.setToolTip(
                "Timeline view: drag left/right to pan through the visible timeline window without moving the CTI.")
            self.timeline_navigation_slider.valueChanged.connect(self._on_timeline_navigation_slider)
            self.timeline_navigation_slider.installEventFilter(self)
            view_frame_layout.addWidget(self.timeline_navigation_slider, 1)
            self.btn_timeline_view_right = QPushButton("▶")
            self.btn_timeline_view_right.setObjectName("timelineViewArrow")
            self.btn_timeline_view_right.setFixedSize(14, 14)
            self.btn_timeline_view_right.setAutoRepeat(True)
            self.btn_timeline_view_right.setAutoRepeatDelay(220)
            self.btn_timeline_view_right.setAutoRepeatInterval(70)
            self.btn_timeline_view_right.setToolTip("Move timeline view right")
            self.btn_timeline_view_right.clicked.connect(lambda: self._nudge_timeline_view(1))
            view_frame_layout.addWidget(self.btn_timeline_view_right)
            view_row.addWidget(view_frame, 1)
            root.addLayout(view_row)

            zoom_row = QHBoxLayout()
            zoom_row.setSpacing(8)
            zoom_lbl = QLabel("Timeline zoom")
            zoom_lbl.setObjectName("controlLabel")
            zoom_row.addWidget(zoom_lbl)
            self.btn_timeline_zoom_left = QPushButton("◀")
            self.btn_timeline_zoom_left.setObjectName("timelineZoomArrow")
            self.btn_timeline_zoom_left.setFixedSize(14, 14)
            self.btn_timeline_zoom_left.setAutoRepeat(True)
            self.btn_timeline_zoom_left.setAutoRepeatDelay(220)
            self.btn_timeline_zoom_left.setAutoRepeatInterval(70)
            self.btn_timeline_zoom_left.setToolTip("Zoom timeline out")
            self.btn_timeline_zoom_left.clicked.connect(lambda: self._nudge_timeline_zoom(-1))
            zoom_row.addWidget(self.btn_timeline_zoom_left)
            self.timeline_zoom_slider = QSlider(Qt.Horizontal)
            self.timeline_zoom_slider.setObjectName("timelineZoomSlider")
            self.timeline_zoom_slider.setRange(0, 100)
            self.timeline_zoom_slider.setValue(0)
            self.timeline_zoom_slider.setTracking(True)
            self.timeline_zoom_slider.setMinimumWidth(780)
            self.timeline_zoom_slider.setMaximumWidth(16777215)
            self.timeline_zoom_slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.timeline_zoom_slider.setStyleSheet(f"""
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
            self.timeline_zoom_slider.setToolTip(
                "Timeline zoom. Mouse wheel here zooms in/out; Shift=fast, Ctrl=fine.")
            self.timeline_zoom_slider.valueChanged.connect(self._on_timeline_zoom_slider)
            self.timeline_zoom_slider.installEventFilter(self)
            zoom_row.addWidget(self.timeline_zoom_slider, 1)
            self.btn_timeline_zoom_right = QPushButton("▶")
            self.btn_timeline_zoom_right.setObjectName("timelineZoomArrow")
            self.btn_timeline_zoom_right.setFixedSize(14, 14)
            self.btn_timeline_zoom_right.setAutoRepeat(True)
            self.btn_timeline_zoom_right.setAutoRepeatDelay(220)
            self.btn_timeline_zoom_right.setAutoRepeatInterval(70)
            self.btn_timeline_zoom_right.setToolTip("Zoom timeline in")
            self.btn_timeline_zoom_right.clicked.connect(lambda: self._nudge_timeline_zoom(1))
            zoom_row.addWidget(self.btn_timeline_zoom_right)
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
            self.btn_play = QPushButton(self._icon("play", QStyle.SP_MediaPlay), " Play (Space)")
            self.btn_play.setObjectName("primary")
            self.btn_play.setToolTip("Play / Pause (Space)")
            self.btn_play.clicked.connect(self.toggle_playback)
            row1.addWidget(self.btn_play)

            row1.addWidget(self._tbtn(self._icon("skip_backward", QStyle.SP_MediaSkipBackward),
                                       "Home (Home)", "Jump to start (Home)", self.go_home))
            row1.addWidget(self._tbtn(self._icon("seek_backward", QStyle.SP_MediaSeekBackward),
                                       "-5s (Shift+Left)", "Seek -5s (Shift+Left)",
                                       lambda: self._seek_relative(-5.0)))
            row1.addWidget(self._tbtn(None, "-1s (Left)", "Seek -1s (Left)",
                                       lambda: self._seek_relative(-1.0)))
            row1.addWidget(self._tbtn(None, "+1s (Right)", "Seek +1s (Right)",
                                       lambda: self._seek_relative(1.0)))
            row1.addWidget(self._tbtn(self._icon("seek_forward", QStyle.SP_MediaSeekForward),
                                       "+5s (Shift+Right)", "Seek +5s (Shift+Right)",
                                       lambda: self._seek_relative(5.0)))
            row1.addWidget(self._tbtn(self._icon("skip_forward", QStyle.SP_MediaSkipForward),
                                       "End (End)", "Jump to end (End)", self.go_end))
            row1.addStretch(1)
            row1.addWidget(self._tbtn(None, "◀◀ Prev (Ctrl+Left)",
                                       "Snap CTI to previous marker (Ctrl+Left)",
                                       self.snap_marker_prev))
            row1.addWidget(self._tbtn(None, "Next (Ctrl+Right) ▶▶",
                                       "Snap CTI to next marker (Ctrl+Right)",
                                       self.snap_marker_next))
            row1.addStretch(1)
            root.addLayout(row1)

            # ----- Marker / cut row -----
            row2 = QHBoxLayout()
            row2.setSpacing(6)
            self.btn_mark_in = QPushButton(self._icon("mark_in", None), " Mark In (I)")
            self.btn_mark_in.setObjectName("markIn")
            self.btn_mark_in.setToolTip(
                "Mark In at the current CTI/playhead (I). Uses the green In-marker color. "
                "If an Out marker is selected, converts it to In.")
            self.btn_mark_in.clicked.connect(self.mark_in)
            row2.addWidget(self.btn_mark_in)

            self.btn_mark_out = QPushButton(self._icon("mark_out", None), " Mark Out (O)")
            self.btn_mark_out.setObjectName("markOut")
            self.btn_mark_out.setToolTip(
                "Mark Out at the current CTI/playhead (O). Uses the amber Out-marker color. "
                "If an In marker is selected, converts it to Out.")
            self.btn_mark_out.clicked.connect(self.mark_out)
            row2.addWidget(self.btn_mark_out)

            row2.addStretch(1)

            self.btn_add_cut = QPushButton(self._icon("add_cut", None), " Add Cut(s) (A)")
            self.btn_add_cut.setObjectName("green")
            self.btn_add_cut.setToolTip(
                "Add a complete cut at the playhead (A). "
                "Places an In marker now and a paired Out 1 second later.")
            self.btn_add_cut.clicked.connect(self.add_cuts)
            row2.addWidget(self.btn_add_cut)

            self.btn_invert_cuts = QPushButton(self._icon("invert", None), " Invert Cuts (Ctrl+Shift+I)")
            self.btn_invert_cuts.setObjectName("purple")
            self.btn_invert_cuts.setToolTip(
                "Invert cut ranges: keep the currently selected cut ranges and remove everything else. "
                "Shortcut: Ctrl+Shift+I")
            self.btn_invert_cuts.clicked.connect(self.invert_cuts)
            row2.addWidget(self.btn_invert_cuts)
            row2.addSpacing(10)
            self.btn_delete_markers = QPushButton(
                self._icon("delete", QStyle.SP_TrashIcon), " Delete Selected Marker(s) (Del)")
            self.btn_delete_markers.setObjectName("danger")
            self.btn_delete_markers.setToolTip("Delete all selected markers (Del when markers are selected)")
            self.btn_delete_markers.clicked.connect(self.delete_selected_markers)
            row2.addWidget(self.btn_delete_markers)

            self.btn_delete_selected_cut = QPushButton(
                self._icon("delete", QStyle.SP_TrashIcon), " Delete Selected Cut (Del)")
            self.btn_delete_selected_cut.setObjectName("dangerCut")
            self.btn_delete_selected_cut.setToolTip("Delete the selected cut region (Del when a cut is selected)")
            self.btn_delete_selected_cut.clicked.connect(self.delete_selected_cut)
            row2.addWidget(self.btn_delete_selected_cut)

            self.btn_delete_all = QPushButton(
                self._icon("delete_all", QStyle.SP_TrashIcon), " Delete All Markers")
            self.btn_delete_all.setObjectName("dangerAlt")
            self.btn_delete_all.setToolTip("Remove every marker (asks for confirmation)")
            self.btn_delete_all.clicked.connect(self.delete_all_markers)
            row2.addWidget(self.btn_delete_all)
            row2.addStretch(1)
            root.addLayout(row2)

            # ----- Audio / zoom / confirmation row -----
            row3 = QHBoxLayout()
            row3.setSpacing(8)
            row3.addWidget(self._tbtn(self._icon("zoom_in", None), "Zoom In (+)",
                                       "Zoom timeline in (+)",
                                       self.timeline_zoom_in))
            row3.addWidget(self._tbtn(self._icon("zoom_out", None), "Zoom Out (-)",
                                       "Zoom timeline out (-)",
                                       self.timeline_zoom_out))
            row3.addWidget(self._tbtn(None, "Reset Zoom (Ctrl+R)",
                                       "Reset zoom and view position (Ctrl+R)",
                                       self.timeline_fit))
            row3.addSpacing(12)
            self.btn_mute = QPushButton(self._icon("volume_meter_3", QStyle.SP_MediaVolume), "  Mute (M)")
            self.btn_mute.setMinimumWidth(118)
            self.btn_mute.setIconSize(QtCore.QSize(20, 20))
            self.btn_mute.setToolTip("Mute / unmute (M)")
            self.btn_mute.clicked.connect(self.toggle_mute)
            row3.addWidget(self.btn_mute)
            self.volume_slider = QSlider(Qt.Horizontal)
            self.volume_slider.setObjectName("cutVolumeSlider")
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(60)
            self.volume_slider.setFixedWidth(130)
            self.volume_slider.setStyleSheet(f"""
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
            self.volume_slider.setToolTip("Volume (mouse wheel works here)")
            self.volume_slider.valueChanged.connect(self._on_volume_changed)
            self.volume_slider.installEventFilter(self)
            row3.addWidget(self.volume_slider)
            self.volume_label = QLabel("60%")
            self.volume_label.setObjectName("dim")
            row3.addWidget(self.volume_label)
            row3.addStretch(1)
            self.btn_cancel = QPushButton("Cancel (Esc)")
            self.btn_cancel.setObjectName("danger")
            self.btn_cancel.clicked.connect(self.cancel)
            row3.addWidget(self.btn_cancel)
            self.btn_confirm = QPushButton(self._icon("check", QStyle.SP_DialogOkButton),
                                            " Confirm (Enter)")
            self.btn_confirm.setObjectName("primary")
            self.btn_confirm.clicked.connect(self.confirm)
            row3.addWidget(self.btn_confirm)
            root.addLayout(row3)

            # ----- Marker list -----
            header_lbl = QLabel("Cut ranges (derived from In→Out marker pairs)")
            header_lbl.setObjectName("dim")
            root.addWidget(header_lbl)
            self.cut_list = QListWidget()
            self.cut_list.setMinimumHeight(110)
            self.cut_list.itemSelectionChanged.connect(self._on_cut_list_select)
            root.addWidget(self.cut_list)
            _gui_log_debug(
                f"Cut GUI layout initialized in {time.perf_counter() - layout_start:.3f}s",
                force=True,
            )

        def _tbtn(self, icon, label, tip, slot):
            b = QPushButton(label) if icon is None else QPushButton(icon, " " + label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        def _setup_player(self):
            QtMultimedia = _import_qt_multimedia()
            self._media_player_cls = QtMultimedia.QMediaPlayer
            self.player = QtMultimedia.QMediaPlayer(self)
            self.audio = QtMultimedia.QAudioOutput(self)
            self.audio.setVolume(0.6)
            self.player.setAudioOutput(self.audio)
            self.video_sink = QtMultimedia.QVideoSink(self)
            self.player.setVideoSink(self.video_sink)
            self.video_sink.videoFrameChanged.connect(self.preview.on_frame)
            self.player.positionChanged.connect(self._on_player_position)
            self.player.errorOccurred.connect(self._on_player_error)
            self.player.setSource(QUrl.fromLocalFile(str(self.input_path)))
            self.player.pause()

        def _on_player_error(self, _err, msg):
            if msg:
                self.status_strip._label.setText(f"Player message: {msg}")

        def _install_shortcuts(self):
            QShortcut(QKeySequence("Space"), self).activated.connect(self.toggle_playback)
            QShortcut(QKeySequence("I"), self).activated.connect(self.mark_in)
            QShortcut(QKeySequence("O"), self).activated.connect(self.mark_out)
            QShortcut(QKeySequence("A"), self).activated.connect(self.add_cuts)
            QShortcut(QKeySequence("M"), self).activated.connect(self.toggle_mute)
            QShortcut(QKeySequence("F"), self).activated.connect(self.timeline_fit)
            QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.timeline_fit)
            QShortcut(QKeySequence(Qt.Key_Plus), self).activated.connect(self.timeline_zoom_in)
            QShortcut(QKeySequence(Qt.Key_Equal), self).activated.connect(self.timeline_zoom_in)
            QShortcut(QKeySequence(Qt.Key_Minus), self).activated.connect(self.timeline_zoom_out)
            QShortcut(QKeySequence(Qt.Key_Home), self).activated.connect(self.go_home)
            QShortcut(QKeySequence(Qt.Key_End), self).activated.connect(self.go_end)
            QShortcut(QKeySequence(Qt.Key_Left), self).activated.connect(lambda: self._seek_relative(-1.0))
            QShortcut(QKeySequence(Qt.Key_Right), self).activated.connect(lambda: self._seek_relative(1.0))
            QShortcut(QKeySequence("Shift+Left"), self).activated.connect(lambda: self._seek_relative(-5.0))
            QShortcut(QKeySequence("Shift+Right"), self).activated.connect(lambda: self._seek_relative(5.0))
            QShortcut(QKeySequence("Ctrl+Left"), self).activated.connect(self.snap_marker_prev)
            QShortcut(QKeySequence("Ctrl+Right"), self).activated.connect(self.snap_marker_next)
            QShortcut(QKeySequence("Delete"), self).activated.connect(self.delete_selected_item)
            QShortcut(QKeySequence("Return"), self).activated.connect(self.confirm)
            QShortcut(QKeySequence("Enter"), self).activated.connect(self.confirm)
            QShortcut(QKeySequence("Escape"), self).activated.connect(self.cancel)
            QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._undo)
            QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self._redo)
            QShortcut(QKeySequence("Ctrl+Shift+Z"), self).activated.connect(self._redo)
            QShortcut(QKeySequence("Ctrl+Shift+I"), self).activated.connect(self.invert_cuts)

        def keyPressEvent(self, event):
            vk = event.nativeVirtualKey()
            mods = event.modifiers()
            ctrl = bool(mods & Qt.ControlModifier)
            shift = bool(mods & Qt.ShiftModifier)
            if ctrl and not shift:
                if vk == WIN_VK["z"]: self._undo(); return
                if vk == WIN_VK["y"]: self._redo(); return
                if vk == WIN_VK["r"]: self.timeline_fit(); return
                if vk == WIN_VK["left"]: self.snap_marker_prev(); return
                if vk == WIN_VK["right"]: self.snap_marker_next(); return
            if ctrl and shift and vk == WIN_VK["z"]:
                self._redo(); return
            if ctrl and shift and vk == WIN_VK["i"]:
                self.invert_cuts(); return
            if not ctrl:
                if shift and vk == WIN_VK["left"]:
                    self._seek_relative(-5.0); return
                if shift and vk == WIN_VK["right"]:
                    self._seek_relative(5.0); return
                actions = {
                    WIN_VK["space"]: self.toggle_playback,
                    WIN_VK["i"]: self.mark_in,
                    WIN_VK["o"]: self.mark_out,
                    WIN_VK["a"]: self.add_cuts,
                    WIN_VK["m"]: self.toggle_mute,
                    WIN_VK["f"]: self.timeline_fit,
                    WIN_VK["plus"]: self.timeline_zoom_in,
                    WIN_VK["minus"]: self.timeline_zoom_out,
                    WIN_VK["kp_add"]: self.timeline_zoom_in,
                    WIN_VK["kp_subtract"]: self.timeline_zoom_out,
                    WIN_VK["home"]: self.go_home,
                    WIN_VK["end"]: self.go_end,
                    WIN_VK["left"]: lambda: self._seek_relative(-1.0),
                    WIN_VK["right"]: lambda: self._seek_relative(1.0),
                    WIN_VK["delete"]: self.delete_selected_item,
                    WIN_VK["return"]: self.confirm,
                    WIN_VK["escape"]: self.cancel,
                }
                fn = actions.get(vk)
                if fn is not None:
                    fn(); return
            super().keyPressEvent(event)

        def eventFilter(self, obj, event):
            volume_slider = getattr(self, "volume_slider", None)
            timeline_zoom_slider = getattr(self, "timeline_zoom_slider", None)
            timeline_navigation_slider = getattr(self, "timeline_navigation_slider", None)
            if obj is volume_slider and event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y()
                step = 3 if (event.modifiers() & Qt.ControlModifier) else 5
                new_val = volume_slider.value() + (step if delta > 0 else -step)
                volume_slider.setValue(max(0, min(100, new_val)))
                return True
            if obj is timeline_zoom_slider and event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y()
                if delta:
                    base_step = 6 if (event.modifiers() & Qt.ShiftModifier) else 1 if (event.modifiers() & Qt.ControlModifier) else 2
                    wheel_steps = max(1, abs(delta) // 120)
                    direction = 1 if delta > 0 else -1
                    timeline_zoom_slider.setValue(
                        max(0, min(100, timeline_zoom_slider.value() + direction * base_step * wheel_steps))
                    )
                    _gui_log_debug(
                        f"Cut timeline zoom bar wheel delta={delta} value={timeline_zoom_slider.value()}",
                        force=False,
                    )
                    return True
            if obj is timeline_navigation_slider and event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y()
                if delta:
                    span = max(0.001, self.timeline.state.view_span)
                    step_seconds = span * (0.30 if (event.modifiers() & Qt.ShiftModifier) else 0.03 if (event.modifiers() & Qt.ControlModifier) else 0.10)
                    base_step = max(1, int(round(step_seconds * 1000.0)))
                    wheel_steps = max(1, abs(delta) // 120)
                    direction = 1 if delta > 0 else -1
                    timeline_navigation_slider.setValue(
                        max(
                            timeline_navigation_slider.minimum(),
                            min(
                                timeline_navigation_slider.maximum(),
                                timeline_navigation_slider.value() + direction * base_step * wheel_steps,
                            ),
                        )
                    )
                    _gui_log_debug(
                        f"Cut timeline navigation bar wheel delta={delta} value={timeline_navigation_slider.value()}",
                        force=False,
                    )
                    return True
            if obj is timeline_zoom_slider and event.type() in {
                QtCore.QEvent.MouseButtonPress,
                QtCore.QEvent.MouseMove,
            }:
                if obj is None or not obj.isEnabled():
                    return True
                buttons = event.buttons() if hasattr(event, "buttons") else Qt.NoButton
                if event.type() == QtCore.QEvent.MouseButtonPress and getattr(event, "button", lambda: Qt.NoButton)() != Qt.LeftButton:
                    return False
                if event.type() == QtCore.QEvent.MouseMove and not (buttons & Qt.LeftButton):
                    return False
                x = int(max(0, min(obj.width(), event.position().x())))
                value = QStyle.sliderValueFromPosition(
                    obj.minimum(),
                    obj.maximum(),
                    x,
                    max(1, obj.width()),
                )
                obj.setValue(max(obj.minimum(), min(obj.maximum(), value)))
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
                self.btn_play.setText(" Play (Space)")
                self.btn_play.setIcon(self._icon("play", QStyle.SP_MediaPlay))
            else:
                pos_ms = self.player.position()
                dur_ms = max(0, self.player.duration() or int(self.duration * 1000))
                if dur_ms > 0 and pos_ms >= dur_ms - 500:
                    self.player.setPosition(0)
                self.player.play()
                self.btn_play.setText(" Pause (Space)")
                self.btn_play.setIcon(self._icon("pause", QStyle.SP_MediaPause))

        def go_home(self):
            if hasattr(self, "player"):
                self.player.setPosition(0)
            self.timeline.set_playhead(0.0, follow=True, force_visible=True)

        def go_end(self):
            dur_ms = int(self.duration * 1000)
            if hasattr(self, "player"):
                dur_ms = max(0, self.player.duration() or dur_ms)
                self.player.setPosition(dur_ms)
            self.timeline.set_playhead(dur_ms / 1000.0, follow=True, force_visible=True)

        def _seek_relative(self, dt):
            cur = self._current_time()
            target = max(0.0, min(self.duration, cur + dt))
            if hasattr(self, "player"):
                self.player.setPosition(int(round(target * 1000)))
            self.timeline.set_playhead(target, follow=True)

        def _on_player_position(self, ms):
            self.timeline.set_playhead(ms / 1000.0, follow=True)
            self._refresh_status()

        def _on_timeline_seek(self, t):
            if hasattr(self, "player"):
                self.player.setPosition(int(round(t * 1000)))
            self.timeline.set_playhead(t, follow=True)
            self._refresh_status()

        def _on_volume_changed(self, value):
            # Pure audio-mixer change; does NOT seek or interrupt playback.
            if not hasattr(self, "audio"):
                return
            self.audio.setVolume(max(0, min(100, value)) / 100.0)
            self.volume_label.setText(f"{value}%")
            level = 0 if value <= 0 else 1 if value <= 25 else 2 if value <= 50 else 3 if value <= 75 else 4
            if value <= 0:
                self.btn_mute.setIcon(self._icon("volume_meter_muted", QStyle.SP_MediaVolumeMuted))
            elif not self.audio.isMuted():
                self.btn_mute.setIcon(self._icon(f"volume_meter_{level}", QStyle.SP_MediaVolume))

        def toggle_mute(self):
            if not hasattr(self, "audio"):
                return
            muted = not self.audio.isMuted()
            self.audio.setMuted(muted)
            if muted:
                self.btn_mute.setIcon(self._icon("volume_meter_muted", QStyle.SP_MediaVolumeMuted))
            else:
                value = self.volume_slider.value()
                level = 0 if value <= 0 else 1 if value <= 25 else 2 if value <= 50 else 3 if value <= 75 else 4
                icon = "volume_meter_muted" if value <= 0 else f"volume_meter_{level}"
                self.btn_mute.setIcon(self._icon(icon, QStyle.SP_MediaVolume))

        def _current_time(self):
            if not hasattr(self, "player"):
                return 0.0
            return self.player.position() / 1000.0

        def mark_in(self):
            sel = self._selected_marker()
            if sel is not None and sel.kind == "out":
                sel.kind = "in"
                self._refresh_all_no_history()
                self._commit_history()
                return
            self._add_marker(self._current_time(), "in")
            self._clear_marker_selection()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def mark_out(self):
            sel = self._selected_marker()
            if sel is not None and sel.kind == "in":
                sel.kind = "out"
                self._refresh_all_no_history()
                self._commit_history()
                return
            self._add_marker(self._current_time(), "out")
            self._clear_marker_selection()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def _unpaired_marker_summary(self):
            ordered = sorted(self._markers, key=lambda m: (m.time, 0 if m.kind == "in" else 1))
            warnings = []
            i = 0
            while i < len(ordered):
                marker = ordered[i]
                nxt = ordered[i + 1] if i + 1 < len(ordered) else None
                if marker.kind == "in" and nxt is not None and nxt.kind == "out" and nxt.time > marker.time:
                    i += 2
                    continue
                warnings.append(f"{marker.kind.upper()} at {seconds_to_hmsf(marker.time, self.fps)}")
                i += 1
            return warnings

        def add_cuts(self):
            cuts = self._cuts()
            if cuts:
                warnings = self._unpaired_marker_summary()
                message = f"Add Cut(s): {len(cuts)} valid adjacent pair(s) are active."
                if warnings:
                    message += " Unpaired marker(s): " + "; ".join(warnings[:4])
                    if len(warnings) > 4:
                        message += f"; +{len(warnings) - 4} more"
                self.status_strip._label.setText(message)
                self._refresh_all_no_history()
                return
            t = self._current_time()
            out_t = min(self.duration, max(t + 1.0, t + 0.1))
            self._add_marker(t, "in")
            self._add_marker(out_t, "out")
            self._clear_marker_selection()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def invert_cuts(self):
            if not math.isfinite(self.duration) or self.duration <= 0:
                self._log_debug(
                    f"Invert cuts refused: invalid duration={self.duration!r}; "
                    f"input_path={self.input_path}")
                QMessageBox.critical(
                    self,
                    "Cannot invert cuts",
                    "Cannot invert cut ranges because the video duration is unknown.")
                return

            before = normalize_ranges(self._cuts(), self.duration)
            if not before:
                self._log_debug(
                    f"Invert cuts refused: no valid cut ranges; input_path={self.input_path}")
                QMessageBox.warning(
                    self,
                    "No cut ranges",
                    "There are no valid cut ranges to invert.")
                return

            after = invert_cut_ranges(before, self.duration)
            self._log_debug(
                "Invert cuts before="
                + _format_debug_ranges(before)
                + " after="
                + _format_debug_ranges(after)
                + f" duration={self.duration:.6f}")

            self._replace_cut_ranges_with_markers(after)
            self._selected_marker_ids.clear()
            self._selected_cut = 0 if after else -1
            self._refresh_all_no_history()
            self._commit_history()
            self.status_strip._label.setText(
                f"Inverted cuts. Kept the previous {len(before)} cut range(s); "
                f"now removing {len(after)} range(s).")

        def delete_selected_item(self):
            if self._selected_marker_ids:
                self.delete_selected_markers()
            elif self._selected_cut >= 0:
                self.delete_selected_cut()

        def delete_selected_markers(self):
            if not self._selected_marker_ids:
                return
            selected = set(self._selected_marker_ids)
            before = len(self._markers)
            self._markers = [m for m in self._markers if m.id not in selected]
            if len(self._markers) == before:
                return
            self._selected_marker_ids.clear()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def delete_selected_cut(self):
            if self._selected_cut < 0:
                return
            cuts = self._cuts()
            if 0 <= self._selected_cut < len(cuts):
                cs, ce = cuts[self._selected_cut]
                self._markers = [
                    m for m in self._markers
                    if not (abs(m.time - cs) < 1e-6 and m.kind == "in")
                    and not (abs(m.time - ce) < 1e-6 and m.kind == "out")
                ]
                self._selected_cut = -1
                self._selected_marker_ids.clear()
                self._refresh_all_no_history()
                self._commit_history()

        def delete_all_markers(self):
            if not self._markers:
                return
            box = QMessageBox(self)
            box.setWindowTitle("Delete all markers")
            box.setText("Remove every marker? This can be undone with Ctrl+Z.")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.No)
            if box.exec() != QMessageBox.Yes:
                return
            self._markers = []
            self._selected_marker_ids.clear()
            self._selected_cut = -1
            self._refresh_all_no_history()
            self._commit_history()

        def snap_marker_prev(self):
            self._snap(direction=-1)

        def snap_marker_next(self):
            self._snap(direction=1)

        def _snap(self, direction):
            now = self._current_time()
            candidates = sorted({m.time for m in self._markers})
            target = None
            if direction < 0:
                for c in reversed(candidates):
                    if c < now - 1e-4:
                        target = c
                        break
            else:
                for c in candidates:
                    if c > now + 1e-4:
                        target = c
                        break
            if target is not None:
                if hasattr(self, "player"):
                    self.player.setPosition(int(round(target * 1000)))
                self.timeline.set_playhead(target, follow=True)

        def _on_marker_selection_requested(self, marker_id, additive):
            self._set_selected_marker(marker_id, additive=additive)

        def _on_marker_drag_started(self):
            self._drag_snapshot = self._take_snapshot()

        def _on_marker_drag_moved(self, marker_id, new_time):
            for m in self._markers:
                if m.id == marker_id:
                    m.time = max(0.0, min(self.duration, new_time))
                    break
            self._refresh_all_no_history()

        def _on_marker_drag_finished(self):
            if self._drag_snapshot is None:
                return
            current = self._take_snapshot()
            pre = self._drag_snapshot
            self._drag_snapshot = None
            self._markers = [copy.copy(m) for m in pre.markers]
            self._selected_marker_ids = set(pre.selected_marker_ids)
            self._history.push(self._take_snapshot())
            self._markers = [copy.copy(m) for m in current.markers]
            self._selected_marker_ids = set(current.selected_marker_ids)
            self._history.push(self._take_snapshot())
            self._refresh_all_no_history()

        def _on_cut_selected_from_timeline(self, idx):
            self._selected_cut = idx
            if idx >= 0:
                self._selected_marker_ids.clear()
            if idx < 0:
                self.cut_list.blockSignals(True)
                self.cut_list.clearSelection()
                self.cut_list.blockSignals(False)
                self.timeline.set_selected_cut(-1)
                self._update_button_states()
                return
            self.cut_list.blockSignals(True)
            self.cut_list.setCurrentRow(idx)
            self.cut_list.blockSignals(False)
            self.timeline.set_selected_cut(idx)
            self._update_button_states()

        def _on_cut_context(self, idx, global_pos):
            menu = QMenu(self)
            act_seek = QAction("Seek playhead to this cut", self)
            act_seek.triggered.connect(lambda: self._seek_to_cut(idx))
            menu.addAction(act_seek)
            menu.addSeparator()
            act_del = QAction("Delete this cut", self)
            act_del.triggered.connect(self.delete_selected_cut)
            menu.addAction(act_del)
            menu.exec(global_pos)

        def _seek_to_cut(self, idx):
            cuts = self._cuts()
            if 0 <= idx < len(cuts):
                if hasattr(self, "player"):
                    self.player.setPosition(int(round(cuts[idx][0] * 1000)))
                self.timeline.set_playhead(cuts[idx][0], follow=True)

        def _on_cut_list_select(self):
            items = self.cut_list.selectedItems()
            self._selected_cut = self.cut_list.row(items[0]) if items else -1
            if self._selected_cut >= 0:
                self._selected_marker_ids.clear()
            self.timeline.set_selected_cut(self._selected_cut)
            self._update_button_states()

        def timeline_zoom_in(self):
            self.timeline.zoom_in_at_playhead()
            self._sync_timeline_zoom_slider()
            self._sync_timeline_navigation_slider()

        def timeline_zoom_out(self):
            self.timeline.zoom_out_at_playhead()
            self._sync_timeline_zoom_slider()
            self._sync_timeline_navigation_slider()

        def timeline_fit(self):
            self.timeline.fit_view()
            self._sync_timeline_zoom_slider()
            self._sync_timeline_navigation_slider()

        def _on_timeline_zoom_slider(self, value):
            if self._syncing_timeline_zoom:
                return
            ratio = 64.0 ** (float(value) / 100.0)
            self.timeline.set_zoom_ratio(ratio)
            self._sync_timeline_navigation_slider()
            self._update_timeline_zoom_arrow_states()

        def _sync_timeline_zoom_slider(self):
            if not hasattr(self, "timeline_zoom_slider"):
                return
            ratio = self.timeline.zoom_ratio()
            ratio = max(1.0, min(64.0, ratio))
            value = int(round(math.log(ratio, 64.0) * 100.0))
            self._syncing_timeline_zoom = True
            self.timeline_zoom_slider.setValue(max(0, min(100, value)))
            self._syncing_timeline_zoom = False
            self._update_timeline_zoom_arrow_states()

        def _nudge_timeline_zoom(self, direction):
            if not hasattr(self, "timeline_zoom_slider"):
                return
            step = 2
            value = max(
                self.timeline_zoom_slider.minimum(),
                min(
                    self.timeline_zoom_slider.maximum(),
                    self.timeline_zoom_slider.value() + int(direction) * step,
                ),
            )
            self.timeline_zoom_slider.setValue(value)

        def _update_timeline_zoom_arrow_states(self):
            slider = getattr(self, "timeline_zoom_slider", None)
            if slider is None:
                return
            left = getattr(self, "btn_timeline_zoom_left", None)
            right = getattr(self, "btn_timeline_zoom_right", None)
            if left is not None:
                left.setEnabled(slider.value() > slider.minimum())
            if right is not None:
                right.setEnabled(slider.value() < slider.maximum())

        def _on_timeline_navigation_slider(self, value):
            if self._syncing_timeline_navigation:
                return
            self.timeline.set_view_start(float(value) / 1000.0)

        def _nudge_timeline_view(self, direction):
            span = max(0.001, self.timeline.state.view_span)
            self.timeline.scroll_view(float(direction) * span * 0.10)
            self._sync_timeline_navigation_slider()

        def _sync_timeline_navigation_slider(self):
            if not hasattr(self, "timeline_navigation_slider"):
                return
            duration_ms = max(1, int(round(max(0.001, self.timeline.state.duration) * 1000.0)))
            span_ms = max(1, int(round(max(0.001, self.timeline.state.view_span) * 1000.0)))
            max_start = max(0, duration_ms - span_ms)
            value = max(0, min(max_start, int(round(max(0.0, self.timeline.state.view_start) * 1000.0))))
            single_step = max(1, int(round(span_ms * 0.05)))
            self._syncing_timeline_navigation = True
            self.timeline_navigation_slider.setRange(0, max_start)
            self.timeline_navigation_slider.setPageStep(max(1, span_ms))
            self.timeline_navigation_slider.setSingleStep(single_step)
            self.timeline_navigation_slider.setEnabled(max_start > 0)
            self.timeline_navigation_slider.setValue(value)
            self._syncing_timeline_navigation = False
            for button_name in ("btn_timeline_view_left", "btn_timeline_view_right"):
                button = getattr(self, button_name, None)
                if button is not None:
                    button.setEnabled(max_start > 0)

        def _refresh_all(self):
            self._refresh_all_no_history()

        def _refresh_all_no_history(self):
            self.timeline.set_markers(self._markers, self._selected_marker_ids)
            self.timeline.set_selected_cut(self._selected_cut)
            self._refresh_marker_list()
            self._refresh_status()
            self._update_button_states()
            self._update_undo_redo_state()
            self._sync_timeline_zoom_slider()
            self._sync_timeline_navigation_slider()

        def _refresh_marker_list(self):
            self.cut_list.blockSignals(True)
            self.cut_list.clear()
            cuts = self._cuts()
            for idx, (s, e) in enumerate(cuts):
                item = QListWidgetItem(
                    f"{idx + 1:>2}.  {seconds_to_hmsf(s, self.fps)}  ➜  "
                    f"{seconds_to_hmsf(e, self.fps)}      "
                    f"({seconds_to_timecode(s)}  ➜  {seconds_to_timecode(e)})"
                )
                self.cut_list.addItem(item)
            if 0 <= self._selected_cut < self.cut_list.count():
                self.cut_list.setCurrentRow(self._selected_cut)
            self.cut_list.blockSignals(False)

        def _refresh_status(self):
            sel = self._selected_marker()
            in_time = sel.time if sel and sel.kind == "in" else None
            out_time = sel.time if sel and sel.kind == "out" else None
            now = self._current_time()
            self.status_strip.update_status(
                now, in_time, out_time, self._cuts(),
                self.timeline.state.view_span / max(0.001, self.duration),
            )

        def _update_button_states(self):
            has_marker_sel = bool(self._selected_marker_ids)
            has_cut_sel = self._selected_cut >= 0
            self.btn_delete_markers.setEnabled(has_marker_sel)
            self.btn_delete_selected_cut.setEnabled(has_cut_sel)
            self.btn_delete_all.setEnabled(bool(self._markers))
            self.btn_invert_cuts.setEnabled(bool(self._cuts()) and self.duration > 0)
            for button in (self.btn_delete_markers, self.btn_delete_selected_cut, self.btn_delete_all):
                self._apply_disabled_opacity(button)
            sel = self._selected_marker()
            if sel and sel.kind == "out":
                self.btn_mark_in.setText(" Convert → In (I)")
            else:
                self.btn_mark_in.setText(" Mark In (I)")
            if sel and sel.kind == "in":
                self.btn_mark_out.setText(" Convert → Out (O)")
            else:
                self.btn_mark_out.setText(" Mark Out (O)")

        def confirm(self):
            cuts = self._cuts()
            keep = invert_cuts_to_keep(cuts, self.duration) if cuts else [(0.0, self.duration)]
            self.result = {"status": "ok",
                           "keep_ranges": [[s, e] for s, e in keep],
                           # Empty keep + cuts_applied means "everything is cut"
                           # rather than "no cuts" (D13).
                           "cuts_applied": bool(cuts)}
            if hasattr(self, "player"):
                self.player.stop()
            self.close()

        def cancel(self):
            self.result = {"status": "canceled", "keep_ranges": []}
            if hasattr(self, "player"):
                self.player.stop()
            self.close()

        def closeEvent(self, event):
            try:
                QApplication.instance().removeEventFilter(self)
            except Exception:
                pass
            try:
                self.player.stop()
            except Exception:
                pass
            super().closeEvent(event)

    return CutEditorWindow(request)
