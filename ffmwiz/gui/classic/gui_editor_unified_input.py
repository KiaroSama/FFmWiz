"""Key and event routing for the classic unified video editor.

Split out of `gui_editor_unified.py`: the shortcut table, the window's
key handlers, and the event filter that turns Return/wheel/focus on the crop
and speed fields into the matching edit. A pure code move; the methods are
unchanged.

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
from ffmwiz.gui.gui_geometry import WIN_VK


def build_unified_editor_input_mixin():
    """Return the mixin that routes keyboard and filtered events."""
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    Qt = QtCore.Qt

    class UnifiedEditorInputMixin:
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
            try:
                crop_widgets.update(
                    box.lineEdit()
                    for box in getattr(self, "crop_spinboxes", {}).values()
                    if box.lineEdit() is not None
                )
            except RuntimeError:
                # A destroyed C++ spinbox. This window is installed as a filter on
                # the application AND on its canvas, sliders and combo line edits,
                # so events keep arriving while its own children are being torn
                # down -- and this block dereferences them before it has even
                # looked at `obj`. There is nothing left to filter for.
                return False
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

    return UnifiedEditorInputMixin
