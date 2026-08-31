"""Waveform strip for the classic audio cut editor.

Split out of `gui_editor_audio.py` as a pure code move -- the same seam already
taken for `gui_editor_unified_canvas.py`, and for the same reason: the widget
captured no state from the builder function, only Qt symbols and the two ruler
helpers that came with it. Both helpers exist solely to label this ruler.

Defined inside a factory rather than at module level so PySide6 stays
importable-on-demand: most CI jobs install no Qt at all.

Unlike the modules listed in `ffmwiz_gui._MODULES`, this one is imported by its
parent instead of being merged into the assembled namespace, so it never
receives the injection and names its shared imports explicitly.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.gui_common import _import_qt
from ffmwiz.gui.gui_geometry import normalize_ranges, seconds_to_timecode
from ffmwiz.gui.gui_style import PALETTE


def build_audio_waveform_widget():
    """Define and return the waveform widget; PySide6 is imported here."""
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    Qt = QtCore.Qt

    def _timeline_tick_label(seconds: float) -> str:
        seconds = max(0.0, float(seconds or 0.0))
        total = int(round(seconds))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _nice_tick_step(span: float) -> float:
        for step in (0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600):
            if span / step <= 10:
                return step
        return 7200

    class WaveformCutWidget(QtWidgets.QWidget):
        seek_requested = QtCore.Signal(float)
        cut_selected = QtCore.Signal(int)
        view_changed = QtCore.Signal()

        def __init__(self, duration):
            super().__init__()
            self.duration = max(0.001, float(duration or 0.0))
            self.playhead = 0.0
            self.in_marker = 0.0
            self.out_marker = min(5.0, self.duration)
            self.cut_ranges: list[tuple[float, float]] = []
            self.chapters: list[dict[str, Any]] = []
            self.selected_cut = -1
            self.view_start = 0.0
            self.view_span = self.duration
            self._dragging_playhead = False
            self._drag_origin_y = 0.0
            self._drag_origin_span = self.view_span
            self.wave = None
            self.setMinimumHeight(260)
            self.setMouseTracking(True)
            self.setStyleSheet(
                f"background-color: {PALETTE['timeline_bg']};"
                f"border: 1px solid {PALETTE['border']}; border-radius: 8px;"
            )

        def set_waveform(self, path: Path):
            pix = QtGui.QPixmap(str(path))
            if not pix.isNull():
                self.wave = pix
                self.update()

        def set_playhead(self, value):
            self.playhead = max(0.0, min(self.duration, float(value)))
            self.update()

        def _inner_rect(self):
            return self.rect().adjusted(14, 28, -14, -36)

        def _clamp_view(self, start, span):
            min_span = min(self.duration, max(0.05, self.duration / 500.0))
            span = max(min_span, min(self.duration, float(span or self.duration)))
            start = max(0.0, min(max(0.0, self.duration - span), float(start or 0.0)))
            return start, span

        def set_view(self, start, span, emit=True):
            self.view_start, self.view_span = self._clamp_view(start, span)
            self.update()
            if emit:
                self.view_changed.emit()

        def fit_view(self):
            self.set_view(0.0, self.duration)

        def zoom_around(self, center_time, factor):
            old_span = max(0.001, self.view_span)
            new_span = old_span * max(0.05, float(factor or 1.0))
            center_time = max(0.0, min(self.duration, float(center_time or 0.0)))
            ratio = (center_time - self.view_start) / old_span
            ratio = max(0.0, min(1.0, ratio))
            self.set_view(center_time - new_span * ratio, new_span)

        def zoom_ratio(self):
            return max(1.0, self.duration / max(0.001, self.view_span))

        def _time_to_x(self, value):
            inner = self._inner_rect()
            visible = max(0.001, self.view_span)
            return inner.left() + inner.width() * ((max(0.0, min(self.duration, value)) - self.view_start) / visible)

        def _x_to_time(self, x):
            inner = self._inner_rect()
            return max(0.0, min(self.duration, self.view_start + (float(x) - inner.left()) / max(1, inner.width()) * self.view_span))

        def _cut_index_at(self, seconds):
            for idx, (start, end) in enumerate(normalize_ranges(self.cut_ranges, self.duration)):
                if start <= seconds <= end:
                    return idx
            return -1

        def mousePressEvent(self, event):
            t = self._x_to_time(event.position().x())
            if event.button() == Qt.RightButton:
                self.selected_cut = self._cut_index_at(t)
                self.cut_selected.emit(self.selected_cut)
                self.update()
                return
            if event.button() == Qt.LeftButton:
                self._dragging_playhead = True
                self._drag_origin_y = event.position().y()
                self._drag_origin_span = self.view_span
                self.set_playhead(t)
                self.seek_requested.emit(t)

        def mouseMoveEvent(self, event):
            if event.buttons() & Qt.LeftButton:
                t = self._x_to_time(event.position().x())
                self.set_playhead(t)
                self.seek_requested.emit(t)
                if self._dragging_playhead:
                    dy = event.position().y() - self._drag_origin_y
                    if abs(dy) >= 2:
                        factor = 2.0 ** (dy / 180.0)
                        self.set_view(t - (self._drag_origin_span * factor) * 0.5, self._drag_origin_span * factor)

        def mouseReleaseEvent(self, event):
            if event.button() == Qt.LeftButton:
                self._dragging_playhead = False
            super().mouseReleaseEvent(event)

        def wheelEvent(self, event):
            delta = event.angleDelta().y()
            if not delta:
                return
            steps = max(1, abs(delta) // 120)
            factor = (0.88 if delta > 0 else 1.14) ** steps
            self.zoom_around(self._x_to_time(event.position().x()), factor)
            event.accept()

        def paintEvent(self, _event):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            p.fillRect(self.rect(), QtGui.QColor(PALETTE["timeline_bg"]))
            inner = self._inner_rect()
            if self.wave is not None:
                sx = self.wave.width() * (self.view_start / self.duration)
                sw = self.wave.width() * (self.view_span / self.duration)
                src = QtCore.QRectF(sx, 0, max(1.0, sw), self.wave.height())
                p.drawPixmap(QtCore.QRectF(inner), self.wave, src)
            else:
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["text_mute"])))
                p.drawText(inner, Qt.AlignCenter, "Generating waveform...")

            for idx, (start, end) in enumerate(normalize_ranges(self.cut_ranges, self.duration)):
                if end < self.view_start or start > self.view_start + self.view_span:
                    continue
                x1 = self._time_to_x(start)
                x2 = self._time_to_x(end)
                color = QtGui.QColor(248, 81, 73, 145 if idx == self.selected_cut else 90)
                p.fillRect(QtCore.QRectF(x1, inner.top(), max(1, x2 - x1), inner.height()), color)
                if idx == self.selected_cut:
                    p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["danger_text"]), 2))
                    p.drawRect(QtCore.QRectF(x1, inner.top(), max(1, x2 - x1), inner.height()))

            step = _nice_tick_step(self.view_span)
            first_tick = math.ceil(self.view_start / step) * step
            t = first_tick
            p.setFont(QtGui.QFont("Segoe UI", 8))
            while t <= self.view_start + self.view_span + 1e-6:
                x = self._time_to_x(t)
                major = abs((t / step) % 2) < 1e-6
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["tick_hi"] if major else PALETTE["tick_lo"]), 1))
                p.drawLine(QtCore.QPointF(x, inner.bottom() + 2), QtCore.QPointF(x, inner.bottom() + (12 if major else 7)))
                if major:
                    p.drawText(QtCore.QRectF(x - 36, inner.bottom() + 13, 72, 16), Qt.AlignCenter, _timeline_tick_label(t))
                t += step

            for t, color in ((self.in_marker, PALETTE["marker_in"]), (self.out_marker, PALETTE["marker_out"])):
                if t < self.view_start or t > self.view_start + self.view_span:
                    continue
                x = self._time_to_x(t)
                p.setPen(QtGui.QPen(QtGui.QColor(color), 2))
                p.drawLine(QtCore.QPointF(x, inner.top() - 8), QtCore.QPointF(x, inner.bottom() + 8))

            x = self._time_to_x(self.playhead)
            if inner.left() - 20 <= x <= inner.right() + 20:
                p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["playhead"]), 2))
                p.drawLine(QtCore.QPointF(x, inner.top() - 12), QtCore.QPointF(x, inner.bottom() + 12))
            p.setPen(QtGui.QPen(QtGui.QColor(PALETTE["text_dim"])))
            p.drawText(14, self.height() - 8, seconds_to_timecode(self.playhead))
            p.drawText(self.width() - 110, self.height() - 8, seconds_to_timecode(self.duration))

    return WaveformCutWidget
