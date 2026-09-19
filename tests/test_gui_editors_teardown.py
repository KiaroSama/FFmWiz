"""The unified editor must survive being filtered while its children are gone.

`gui_editor_crop_layout` installs the window as an event filter on the whole
APPLICATION and on its canvas, sliders and combo line edits. `eventFilter` then
builds its `crop_widgets` set from `self.crop_spinboxes` BEFORE it looks at
`obj`, so once a spinbox's C++ object is destroyed, any event to any filtered
object raises:

    RuntimeError: Internal C++ object (PySide6.QtWidgets.QSpinBox) already deleted.

That is the state Qt reaches when it tears the window down, and it surfaced as
an unraisable at interpreter shutdown rather than as a failure: the exception
escapes a Qt callback and Python routes it to `sys.unraisablehook`.

Removing the application-wide filter in `closeEvent` (which the cut editor does)
is NOT the fix and was tried: the window stays installed on its own widgets, so
the events keep coming. The guard belongs in the filter body, which is the one
place every installation site routes through.
"""
from __future__ import annotations

import os
import sys
import unittest

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 is required to build the real editor window")
class UnifiedEditorTeardown(unittest.TestCase):
    def test_closing_the_editor_stops_it_filtering_the_application(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from ffmwiz.gui.classic import ffmwiz_gui
        window = ffmwiz_gui.gui_editor_unified.build_unified_video_editor(
            {"input_path": "unused.mkv", "duration": 1, "source_w": 64,
             "source_h": 48, "has_audio": False})

        # Destroy one spinbox's C++ object while leaving the Python wrapper, which
        # is exactly the state Qt reaches when it tears the window down, then send
        # any event at all: an application-wide filter sees events for every
        # object, and `eventFilter` walks `crop_spinboxes` before it looks at
        # `obj`. With the filter uninstalled on close, it is never entered.
        from shiboken6 import delete as destroy_cpp
        window.close()
        destroy_cpp(next(iter(window.crop_spinboxes.values())))

        escaped: list = []
        previous = sys.unraisablehook
        sys.unraisablehook = escaped.append
        try:
            app.sendEvent(QtWidgets.QWidget(), QtCore.QEvent(QtCore.QEvent.Type.User))
        finally:
            sys.unraisablehook = previous

        self.assertEqual([], [f"{e.exc_type.__name__}: {e.exc_value}" for e in escaped],
                         "a closed editor still filtered the application and "
                         "dereferenced its own destroyed spinbox")


if __name__ == "__main__":
    unittest.main()
