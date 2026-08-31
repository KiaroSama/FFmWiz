"""Keyboard and wheel input plumbing for the classic cut editor window.

Split out of `gui_editor_cut.py`: shortcut registration, the layout-independent
virtual-key handler, and the event filter that turns wheel and click gestures on
the volume/zoom/navigation sliders into value changes.

Delivered as a mixin rather than free functions because `keyPressEvent` and
`eventFilter` are Qt virtual overrides -- they only fire if Qt finds them on the
window's own class. Mixing first puts them ahead of QMainWindow in the MRO, so
their `super()` calls still reach Qt's defaults.

Not in `ffmwiz_gui._MODULES`, so nothing is injected into this module; see
`gui_editor_cut_widgets.py` for why the shared helpers are imported by name.
"""
from __future__ import annotations
from ffmwiz.gui import gui_common  # noqa: F401
from ffmwiz.gui.gui_common import *  # noqa: F401,F403
from ffmwiz.gui.gui_common import _gui_log_debug, _import_qt
from ffmwiz.gui.gui_geometry import WIN_VK


def build_cut_input_mixin():
    """Define and return the mixin; PySide6 is imported here."""
    QtCore, QtGui, QtWidgets, _ = _import_qt()
    Qt = QtCore.Qt
    QKeySequence = QtGui.QKeySequence
    QShortcut = QtGui.QShortcut
    QStyle = QtWidgets.QStyle

    class CutEditorInputMixin:
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

    return CutEditorInputMixin
