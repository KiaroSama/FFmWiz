"""Regression tests: the Audio Cut / Speed window must not build the hidden tab.

A user reported a long wait before the audio_transform window appeared, and one
real log recorded `audio_transform GUI window built in 24.256s`. The audit
guessed the cause was "two full child editors built synchronously"; measuring it
showed that was only half right.

Measured on this machine with a real MP3 and a real (non-offscreen) window:

    build_cut_editor    0.08 s
    build_speed_editor  2.47 s   <- pure waste, its tab is not visible
    addTab (Audio Cut)  5.70 s   <- Qt realising the VISIBLE editor
    embed helpers       0.00 s

So the Speed / Reverse editor cost ~2.5 s to build a tab nobody was looking at.
It is now created the first time its tab is opened. The remaining cost is Qt
realising the visible editor, which is not something this test covers.

These tests avoid Qt entirely: they read the source, because the property that
matters ("the second editor is not constructed during window construction") is
structural and would otherwise need a live Qt session to observe.
"""
import ast
import unittest
from pathlib import Path

import FFmWiz

MODULE = Path(FFmWiz.__file__).resolve().parent / "ffmwiz" / "gui" / "gui_editor_audio.py"


def _source():
    return MODULE.read_text(encoding="utf-8")


# The module defines several editor classes, each with its own `_build_ui` and
# `confirm`. Scope every lookup to the transform WINDOW class -- the one that
# owns the lazy builder -- or the assertions silently inspect a different editor.
MARKER = "_ensure_speed_editor"


def _transform_class():
    for node in ast.walk(ast.parse(_source())):
        if isinstance(node, ast.ClassDef) and any(
            isinstance(child, ast.FunctionDef) and child.name == MARKER
            for child in node.body
        ):
            return node
    raise AssertionError(f"no class in {MODULE.name} defines {MARKER}")


def _function_source(name):
    """Source of `name` as defined by the transform window class."""
    lines = _source().split("\n")
    for child in _transform_class().body:
        if isinstance(child, ast.FunctionDef) and child.name == name:
            return "\n".join(lines[child.lineno - 1:child.end_lineno])
    raise AssertionError(f"{name} not found on the transform window class")


class AudioTransformStartup(unittest.TestCase):
    def test_the_module_exists_where_expected(self):
        self.assertTrue(MODULE.is_file(), MODULE)

    def test_window_construction_builds_only_the_visible_editor(self):
        build_ui = _function_source("_build_ui")
        self.assertIn("build_audio_cut_editor", build_ui,
                      "the visible tab's editor is still built eagerly, as it should be")
        self.assertNotIn(
            "build_speed_editor", build_ui,
            "the Speed / Reverse editor must NOT be constructed during window build; "
            "its tab is not visible and it cost ~2.5 s")

    def test_the_speed_editor_has_a_lazy_builder(self):
        lazy = _function_source("_ensure_speed_editor")
        self.assertIn("build_speed_editor", lazy,
                      "the lazy path is what actually constructs it")

    def test_the_lazy_builder_is_wired_to_the_tab_change(self):
        build_ui = _function_source("_build_ui")
        self.assertIn("currentChanged", build_ui)
        self.assertIn("_ensure_speed_editor", build_ui)

    def test_the_lazy_builder_is_idempotent(self):
        lazy = _function_source("_ensure_speed_editor")
        self.assertIn("if self.speed_editor is not None", lazy,
                      "reopening the tab must not rebuild the editor")

    def test_confirm_tolerates_a_never_opened_tab(self):
        confirm = _function_source("confirm")
        self.assertIn("if self.speed_editor is not None", confirm,
                      "confirm must not crash, or force a build, when the tab was never opened")

    def test_confirm_defaults_are_a_no_op_transform(self):
        # Never opening the tab means no speed/reverse edit, so the defaults
        # have to mean "unchanged".
        confirm = _function_source("confirm")
        self.assertIn("speed = 1.0", confirm)
        self.assertIn("reverse = False", confirm)

    def test_embedded_children_skip_the_native_window_handle(self):
        # Stamping a native HWND calls winId(), which realises a window the
        # embedded editor never shows.
        source = _source()
        self.assertIn("_embedded", source)
        self.assertIn("native=", source)


if __name__ == "__main__":
    unittest.main()
