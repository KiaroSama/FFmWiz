#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FFmWiz classic PySide6 GUI - subprocess entry point.

The GUI implementation is split across sibling modules for file size. Each
editor owns a builder and delegates its bulk to `_`-suffixed siblings:
  gui_editor_cut      - build_cut_editor        (+ _layout, _widgets, _input)
  gui_editor_crop     - build_crop_editor       (+ _canvas, _layout)
  gui_editor_speed    - build_speed_editor      (+ _transport, _reverse)
  gui_editor_audio    - build_audio_cut_editor, build_audio_transform_editor
                                                (+ _waveform)
  gui_editor_unified  - build_unified_video_editor, the window shell
                        (+ _layout, _player, _edits, _input, _canvas,
                           _timeline, _timeline_input)

`gui_common`, `gui_style` and `gui_geometry` sit one level up, in ffmwiz/gui/,
because the modern QtQuick engine reads them too.

Only the modules in _MODULES below receive the assembled namespace. A sibling
that is NOT listed there gets nothing injected -- and `from gui_common import *`
skips underscore names -- so those modules import their shared helpers by name
instead, and the parent passes in anything reachable only through the assembly.

This file only wires the modules together and launches main(). Because the
modules call each other freely at runtime, the fully assembled namespace is
injected into every module below so cross-module references always resolve.
"""
from __future__ import annotations

import os
import sys

# Run as a SCRIPT (the GUI is launched as a subprocess) the package root
# has to be importable, because the sibling modules are now addressed as
# `ffmwiz.gui.<name>` rather than as bare top-level modules. Bare imports
# only ever worked from this directory, so the installed package could not
# import a single GUI module (D10).
# Four levels now, not three: this file moved into ffmwiz/gui/classic/, so the
# walk to the directory ABOVE the `ffmwiz` package gained a step.
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from ffmwiz.gui import gui_common
from ffmwiz.gui import gui_style
from ffmwiz.gui import gui_geometry
from ffmwiz.gui.classic import gui_editor_cut
from ffmwiz.gui.classic import gui_editor_crop
from ffmwiz.gui.classic import gui_editor_speed
from ffmwiz.gui.classic import gui_editor_audio
from ffmwiz.gui.classic import gui_editor_unified
from ffmwiz.gui.classic import gui_editor_unified_canvas
from ffmwiz.gui.classic import gui_editor_unified_timeline

_MODULES = [
    gui_common,
    gui_style,
    gui_geometry,
    gui_editor_cut,
    gui_editor_crop,
    gui_editor_speed,
    gui_editor_audio,
    gui_editor_unified,
    gui_editor_unified_canvas,
    gui_editor_unified_timeline,
]

def _exported_names(module) -> dict:
    """One child's contribution to the shared namespace.

    Honouring `__all__` is what keeps a child's private imports out of the
    shared dict and stops it silently shadowing a sibling's name of the same
    spelling. No declaration means nothing to honour, so every public name
    still goes in and today's behaviour is unchanged.
    """
    declared = getattr(module, "__all__", None)
    if declared is None:
        return {k: v for k, v in vars(module).items() if not k.startswith("__")}
    # Lenient on drift: a stale name in `__all__` must not kill GUI startup.
    return {name: getattr(module, name) for name in declared if hasattr(module, name)}


# Assemble the full namespace, then inject it into every module so that a
# function defined in one file can freely call names defined in another.
_ASSEMBLED = {}
for _m in _MODULES:
    _ASSEMBLED.update(_exported_names(_m))
for _m in _MODULES:
    for _k, _v in _ASSEMBLED.items():
        setattr(_m, _k, _v)

from ffmwiz.gui.gui_common import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
