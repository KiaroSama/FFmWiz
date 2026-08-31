"""Pointer interaction for the classic unified editor's timeline strip.

Split out of `gui_editor_unified_timeline.py`: hit-testing, magnetic snapping
and the drag/wheel handlers -- everything that turns a mouse position into an
edit or a view change, as opposed to the widget's own state and painting. A
pure code move; the methods are unchanged.

The mixin is defined inside a factory rather than at module level because its
body binds Qt symbols, and PySide6 must stay importable-on-demand: most CI jobs
install no Qt at all.

Not in `ffmwiz_gui._MODULES`, so the assembled namespace is never injected
here: the shared helpers this file uses are imported by name instead.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.gui_common import _import_qt
from ffmwiz.gui.gui_geometry import normalize_ranges


def build_unified_timeline_input_mixin():
    """Return the mixin that handles the timeline's mouse and wheel input."""
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    Qt = QtCore.Qt
    QPointF = QtCore.QPointF

    class UnifiedTimelineInputMixin:
        def _snap_targets(self, exclude_separator=None, exclude_marker=None):
            """Times the magnet can snap to: CTI, mark in/out, split points,
            joined-video boundaries, clip ends."""
            targets = [self.playhead, 0.0, self.duration]
            if self.mark_in is not None and exclude_marker != "in":
                targets.append(float(self.mark_in))
            if self.mark_out is not None and exclude_marker != "out":
                targets.append(float(self.mark_out))
            for i, sp in enumerate(self.separator_points):
                if i != exclude_separator:
                    targets.append(float(sp))
            # Joined-video boundaries: markers/CTI snap to where each new video
            # starts (a little stickiness so cuts/marks land exactly on a join).
            for segment in getattr(self, "join_segments", None) or []:
                start_t = float(segment.get("start", 0.0))
                if start_t > 1e-6:
                    targets.append(start_t)
            return targets

        def _snap_time(self, seconds, exclude_separator=None, exclude_marker=None):
            """Snap a time to the nearest magnet target (CTI / marks / splits / clip
            ends) when magnetic snapping is enabled. The tolerance is PURELY pixel
            based, so zooming in tightens the pull (precise adjustment); switch
            self.snap_enabled off for completely free-hand placement."""
            seconds = max(0.0, min(self.duration, float(seconds)))
            if not self.snap_enabled:
                return seconds
            seconds_per_px = self.view_span / max(1.0, self._wave_rect().width())
            tolerance = seconds_per_px * 8.0          # ~8 px pull; shrinks as you zoom in
            best, best_dist = seconds, tolerance
            for target in self._snap_targets(exclude_separator, exclude_marker):
                d = abs(seconds - target)
                if d <= best_dist:
                    best, best_dist = target, d
            return best

        def _hit_cut(self, x, y):
            r = self._wave_rect()
            if not r.contains(QPointF(x, y)):
                return -1
            start = self.view_start
            end = start + self.view_span
            for idx, (s, e) in enumerate(self.cut_ranges):
                if e < start or s > end:
                    continue
                if self._time_to_x(max(s, start)) - 3 <= x <= self._time_to_x(min(e, end)) + 3:
                    return idx
            return -1

        def _hit_cut_edge(self, x, y):
            r = self._wave_rect().adjusted(0, -28, 0, 0)
            if not r.contains(QPointF(x, y)):
                return None
            start = self.view_start
            end = start + self.view_span
            for idx, (s, e) in enumerate(self.cut_ranges):
                if e < start or s > end:
                    continue
                x1 = self._time_to_x(max(s, start))
                x2 = self._time_to_x(min(e, end))
                if abs(x - x1) <= 10:
                    return idx, "start"
                if abs(x - x2) <= 10:
                    return idx, "end"
            return None

        def _hit_cut_bar(self, x, y):
            point = QPointF(x, y)
            start = self.view_start
            end = start + self.view_span
            for idx, (s, e) in enumerate(self.cut_ranges):
                if e < start or s > end:
                    continue
                if self._cut_bar_rect(max(s, start), min(e, end)).contains(point):
                    return idx
            return -1

        def _hit_separator(self, x, y):
            r = self._timeline_rect().adjusted(0, -4, 0, 4)
            if not r.contains(QPointF(x, y)):
                return -1
            start = self.view_start
            end = start + self.view_span
            for idx, value in enumerate(self.separator_points):
                if value < start or value > end:
                    continue
                if abs(self._time_to_x(value) - x) <= 18:
                    return idx
            return -1

        def _hit_marker(self, x, y):
            r = self._timeline_rect().adjusted(0, -4, 0, 4)
            if not r.contains(QPointF(x, y)):
                return None
            start = self.view_start
            end = start + self.view_span
            for name, value in (("in", self.mark_in), ("out", self.mark_out)):
                if value is None or value < start or value > end:
                    continue
                if abs(self._time_to_x(value) - x) <= 18:
                    return name
            return None

        def mousePressEvent(self, event):
            pos = event.position()
            if event.button() == Qt.RightButton:
                idx = self._hit_cut(pos.x(), pos.y())
                if idx >= 0:
                    self.selected_cut = idx
                    self.selected_separator = -1
                    self.cut_selected.emit(idx)
                    self.update()
                return
            if event.button() != Qt.LeftButton:
                return
            cut_edge = self._hit_cut_edge(pos.x(), pos.y())
            if cut_edge is not None:
                self.selected_cut = int(cut_edge[0])
                self.selected_separator = -1
                self.selected_marker = None
                self._dragging = True
                self._drag_kind = "cut_edge"
                self._drag_target = cut_edge
                self._drag_origin = QPointF(pos.x(), pos.y())
                self._start_span = self.view_span
                self.cut_selected.emit(self.selected_cut)
                self.update()
                return
            cut_bar = self._hit_cut_bar(pos.x(), pos.y())
            if cut_bar >= 0:
                self.selected_cut = int(cut_bar)
                self.selected_separator = -1
                self.selected_marker = None
                self._dragging = True
                self._drag_kind = "cut_move"
                self._drag_target = int(cut_bar)
                self._drag_origin = QPointF(pos.x(), pos.y())
                self._drag_playhead_start = self._x_to_time(pos.x())
                self._move_origin = self.cut_ranges[int(cut_bar)]
                self.cut_selected.emit(self.selected_cut)
                self.update()
                return
            marker_name = self._hit_marker(pos.x(), pos.y())
            if marker_name is not None:
                self.selected_marker = marker_name
                self.selected_separator = -1
                self.selected_cut = -1
                self._dragging = True
                self._drag_kind = "marker"
                self._drag_target = marker_name
                self._drag_origin = QPointF(pos.x(), pos.y())
                self._start_span = self.view_span
                self.cut_selected.emit(-1)
                self.separator_selected.emit(-1)
                self.update()
                return
            sep_idx = self._hit_separator(pos.x(), pos.y())
            if sep_idx >= 0:
                self.selected_separator = sep_idx
                self.selected_cut = -1
                self.selected_marker = None
                self._dragging = True
                self._drag_kind = "separator"
                self._drag_target = sep_idx
                self._drag_origin = QPointF(pos.x(), pos.y())
                self._start_span = self.view_span
                self.separator_selected.emit(sep_idx)
                self.update()
                return
            self.selected_marker = None
            self.selected_separator = -1
            self.selected_cut = -1
            self.cut_selected.emit(-1)
            self.separator_selected.emit(-1)
            self._dragging = True
            self._drag_kind = "playhead"
            self._drag_target = None
            self._drag_origin = QPointF(pos.x(), pos.y())
            self._last_drag_pos = QPointF(pos.x(), pos.y())
            self._vertical_zoom_lock = False
            t = self._x_to_time(pos.x())
            self._drag_playhead_start = t
            self._cti_zoom_focus_time = t
            self._cti_zoom_start_y = pos.y()
            self._start_span = self.view_span
            self.set_playhead(t)
            self.seek_requested.emit(t)

        def mouseMoveEvent(self, event):
            if not self._dragging:
                pos = event.position()
                if self._hit_cut_edge(pos.x(), pos.y()) is not None:
                    self.setCursor(Qt.SizeHorCursor)
                elif self._hit_cut_bar(pos.x(), pos.y()) >= 0:
                    self.setCursor(Qt.SizeAllCursor)
                elif self._hit_marker(pos.x(), pos.y()) is not None or self._hit_separator(pos.x(), pos.y()) >= 0:
                    self.setCursor(Qt.SizeAllCursor)
                else:
                    self.unsetCursor()
                return
            pos = event.position()
            t = self._x_to_time(pos.x())
            if self._drag_kind == "marker" and self._drag_target in {"in", "out"}:
                t = self._snap_time(t, exclude_marker=str(self._drag_target))
                if self._drag_target == "in":
                    self.mark_in = t
                else:
                    self.mark_out = t
                self.marker_moved.emit(str(self._drag_target), t)
                self.update()
                return
            if self._drag_kind == "separator" and isinstance(self._drag_target, int):
                idx = int(self._drag_target)
                if 0 <= idx < len(self.separator_points):
                    t = self._snap_time(t, exclude_separator=idx)
                    t = max(1e-6, min(self.duration - 1e-6, t))
                    self.separator_points[idx] = t
                    self.separator_moved.emit(idx, t)
                    self.update()
                return
            if self._drag_kind == "cut_edge" and isinstance(self._drag_target, tuple):
                idx, side = self._drag_target
                idx = int(idx)
                if 0 <= idx < len(self.cut_ranges):
                    s, e = self.cut_ranges[idx]
                    t = self._snap_time(t)
                    if side == "start":
                        s = min(t, e - 0.001)
                    else:
                        e = max(t, s + 0.001)
                    self.cut_ranges[idx] = (s, e)
                    self.selected_cut = idx
                    self.cut_selected.emit(idx)
                    self.update()
                return
            if self._drag_kind == "cut_move" and isinstance(self._drag_target, int):
                idx = int(self._drag_target)
                origin = getattr(self, "_move_origin", None)
                if origin is not None and 0 <= idx < len(self.cut_ranges):
                    s0, e0 = origin
                    width = max(0.001, e0 - s0)
                    delta = t - float(getattr(self, "_drag_playhead_start", t))
                    s = max(0.0, min(self.duration - width, s0 + delta))
                    self.cut_ranges[idx] = (s, s + width)
                    self.selected_cut = idx
                    self.cut_selected.emit(idx)
                    self.update()
                return
            if self._drag_origin is not None:
                last_pos = self._last_drag_pos or self._drag_origin
                inc_dx = pos.x() - last_pos.x()
                inc_dy = pos.y() - last_pos.y()
                total_dy = pos.y() - self._drag_origin.y()
                if (
                    not self._vertical_zoom_lock
                    and abs(total_dy) >= 4
                    and abs(inc_dy) > max(3.0, abs(inc_dx) * 0.85)
                ):
                    self._cti_zoom_focus_time = max(0.0, min(self.duration, t))
                    self._cti_zoom_start_y = pos.y()
                    self._start_span = self.view_span
                    if not self._vertical_zoom_lock:
                        self.set_playhead(self._cti_zoom_focus_time)
                        self.seek_requested.emit(self._cti_zoom_focus_time)
                    self._vertical_zoom_lock = True
                if self._vertical_zoom_lock:
                    focus = max(0.0, min(self.duration, self._cti_zoom_focus_time))
                    dy_zoom = pos.y() - float(getattr(self, "_cti_zoom_start_y", pos.y()))
                    self.view_span = max(0.05, min(self.duration, self._start_span * (2.0 ** (dy_zoom / 150.0))))
                    self.view_start = focus - 0.5 * self.view_span
                    self._clamp_view()
                    self.view_changed.emit()
                    self.set_playhead(focus)
                    self.seek_requested.emit(focus)
                    self.update()
                    self._last_drag_pos = QPointF(pos.x(), pos.y())
                    return
            self.set_playhead(t)
            self.seek_requested.emit(t)
            self._cti_zoom_focus_time = self.playhead
            self._last_drag_pos = QPointF(pos.x(), pos.y())

        def mouseReleaseEvent(self, _event):
            edited = self._drag_kind in {"marker", "separator", "cut_edge", "cut_move"}
            was_playhead = self._drag_kind == "playhead"
            if self._drag_kind == "separator":
                self.separator_points = sorted(set(round(float(value), 6) for value in self.separator_points))
            if self._drag_kind == "cut_edge":
                self.cut_ranges = normalize_ranges(self.cut_ranges, self.duration)
            self._dragging = False
            self._drag_kind = None
            self._drag_target = None
            self._drag_origin = None
            self._last_drag_pos = None
            self._move_origin = None
            self._vertical_zoom_lock = False
            self._cti_zoom_focus_time = self.playhead
            if edited:
                self.edit_finished.emit()
            # PREVIEW: after a playhead drag ends, request one final seek so the
            # preview frame for the landing position is decoded (it was skipped
            # during the drag to keep things responsive).
            if was_playhead:
                self.seek_requested.emit(self.playhead)

        def wheelEvent(self, event):
            delta = event.angleDelta().y()
            if not delta:
                return
            factor = 1 / 1.16 if delta > 0 else 1.16
            self.view_span = max(0.05, min(self.duration, self.view_span * factor))
            focus = max(0.0, min(self.duration, self.playhead))
            self.view_start = focus - 0.5 * self.view_span
            self._clamp_view()
            self.update()
            self.view_changed.emit()

    return UnifiedTimelineInputMixin
