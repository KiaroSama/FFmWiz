"""The edit model of the classic unified video editor.

Split out of `gui_editor_unified.py`: the snapshot the undo stack stores, and
every operation that mutates it -- crop margins, speed, in/out marks, cut
ranges, split points and the resets. A pure code move; the methods are
unchanged.

Unlike its sibling mixins this one needs no Qt at all: it reads and writes
widget state through `self`, so it is a plain module-level class rather than a
factory that has to import PySide6 first.

Not in `ffmwiz_gui._MODULES`, so the assembled namespace is never injected
here: the shared helpers this file uses are imported by name instead.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.gui_common import _gui_log_debug
from ffmwiz.gui.gui_geometry import (HistoryStack, _snap_crop_axis_even,
                                     invert_cut_ranges, normalize_ranges,
                                     seconds_to_timecode, snap_crop_margins_even)


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


class UnifiedEditorEditMixin:
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
            include_audio=bool(self.include_audio_box.isChecked()) if hasattr(self, "include_audio_box") else bool(self.any_audio),
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
            include_audio=bool(self.any_audio),
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
