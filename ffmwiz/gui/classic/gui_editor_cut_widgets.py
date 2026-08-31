"""Presentation widgets for the classic cut editor: header, status, preview,
timeline.

Split out of `gui_editor_cut.py`, which had grown to 1800 lines as a single
builder function. This is a pure code move -- none of these classes read the
builder's state, only Qt symbols, which the factory below rebinds identically.

They are defined inside a factory rather than at module level because they
subclass QWidget and PySide6 must stay importable-on-demand; see
`gui_editor_unified_timeline.py` for the same reasoning.

Not in `ffmwiz_gui._MODULES`, so the assembled GUI namespace is never injected
here. `from gui_common import *` carries the stdlib re-exports but none of the
palette, geometry or underscore-prefixed helpers, so those are imported by name.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.gui_common import _import_qt
from ffmwiz.gui.gui_geometry import (
    compute_cut_ranges,
    seconds_to_hmsf,
    seconds_to_timecode,
    short_gui_label,
)
from ffmwiz.gui.gui_style import PALETTE


def build_cut_editor_widgets():
    """Define and return the widget classes; PySide6 is imported here."""
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    Qt = QtCore.Qt
    Signal = QtCore.Signal
    QPointF = QtCore.QPointF
    QRectF = QtCore.QRectF

    QColor = QtGui.QColor
    QFont = QtGui.QFont
    QPainter = QtGui.QPainter
    QPen = QtGui.QPen
    QBrush = QtGui.QBrush
    QPolygonF = QtGui.QPolygonF
    QPixmap = QtGui.QPixmap

    QWidget = QtWidgets.QWidget
    QFrame = QtWidgets.QFrame
    QLabel = QtWidgets.QLabel
    QHBoxLayout = QtWidgets.QHBoxLayout
    QSizePolicy = QtWidgets.QSizePolicy

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

    return HeaderBand, StatusStrip, VideoPreview, TimelineWidget
