#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FFmWiz classic PySide6 GUI - subprocess entry point.

The GUI implementation is split across sibling modules for file size:
  gui_common          - imports, palette/QSS, logging, icons, time/history
                        helpers, shared state and the main() dispatcher
  gui_editor_cut      - build_cut_editor
  gui_editor_crop     - build_crop_editor
  gui_editor_speed    - build_speed_editor (video/audio speed + reverse)
  gui_editor_audio    - build_audio_cut_editor, build_audio_transform_editor
  gui_editor_unified  - build_unified_video_editor

This file only wires the modules together and launches main(). Because the
modules call each other freely at runtime, the fully assembled namespace is
injected into every module below so cross-module references always resolve.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gui_common
import gui_editor_cut
import gui_editor_crop
import gui_editor_speed
import gui_editor_audio
import gui_editor_unified

_MODULES = [
    gui_common,
    gui_editor_cut,
    gui_editor_crop,
    gui_editor_speed,
    gui_editor_audio,
    gui_editor_unified,
]

# Assemble the full namespace, then inject it into every module so that a
# function defined in one file can freely call names defined in another.
_ASSEMBLED = {}
for _m in _MODULES:
    _ASSEMBLED.update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
for _m in _MODULES:
    for _k, _v in _ASSEMBLED.items():
        setattr(_m, _k, _v)

from gui_common import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
