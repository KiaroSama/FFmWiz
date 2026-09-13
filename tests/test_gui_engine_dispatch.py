"""A02: choosing the QML engine must actually launch the QML driver.

The modern engine lives in `ffmwiz/gui/modern/` -- both the driver and its QML
files. The dispatcher's preflight looked for the QML file one directory too
high (`ffmwiz/gui/qml/UnifiedEditor.qml`), so the check never found it, the
"files are missing" branch won every time, and selecting QML silently launched
CLASSIC. Nothing failed; the user simply got the other editor.

Importing both drivers proves nothing about this: the defect is in the gate, so
the test has to go through the real dispatcher and assert WHICH script it was
about to run.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k gui_engine_dispatch
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz import guibridge_b
from ffmwiz.support.L01_misc import _ffmwiz_gui_path, _qml_gui_path

ROOT = Path(FFmWiz.__file__).resolve().parent


class LaunchRecorder:
    """Stands in for subprocess.run: records argv, writes a valid reply."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.commands.append(list(cmd))
        # The dispatcher reads the reply file the GUI would have written.
        for index, token in enumerate(cmd):
            if token == "--reply":
                Path(cmd[index + 1]).write_text(
                    json.dumps({"status": "ok", "engine": "recorded"}),
                    encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    @property
    def launched(self) -> Path:
        self_commands = self.commands
        if not self_commands:
            raise AssertionError("the dispatcher never launched anything")
        return Path(self_commands[-1][1])


class TheSelectedEngineIsTheOneThatRuns(unittest.TestCase):
    """The real `_launch_qt_gui`, only the process spawn replaced."""

    def setUp(self) -> None:
        self.recorder = LaunchRecorder()
        for module, name, value in (
            (guibridge_b, "subprocess", _ShimModule(self.recorder)),
            (guibridge_b, "_pyside6_available", lambda: True),
        ):
            original = getattr(module, name)
            setattr(module, name, value)
            self.addCleanup(setattr, module, name, original)
        previous = os.environ.get("FFMWIZ_GUI_ENGINE")
        self.addCleanup(self._restore_engine, previous)

    @staticmethod
    def _restore_engine(previous: str | None) -> None:
        if previous is None:
            os.environ.pop("FFMWIZ_GUI_ENGINE", None)
        else:
            os.environ["FFMWIZ_GUI_ENGINE"] = previous

    def launch(self, engine: str, mode: str = "video_unified"):
        os.environ["FFMWIZ_GUI_ENGINE"] = engine
        return guibridge_b._launch_qt_gui({"mode": mode, "path": str(ROOT / "FFmWiz.py")})

    def test_the_qml_resource_really_sits_beside_its_driver(self):
        # The premise of the whole item: state where the file actually is, so a
        # future move breaks this line rather than the silent gate above it.
        self.assertTrue((_qml_gui_path().parent / "qml" / "UnifiedEditor.qml").exists(),
                        "the modern engine's QML file is not beside its driver any more")

    def test_choosing_qml_launches_the_qml_driver(self):
        reply = self.launch("qml")
        self.assertIsNotNone(reply)
        self.assertEqual(self.recorder.launched, _qml_gui_path(),
                         "QML was selected but the classic driver was launched")

    def test_the_default_is_still_classic(self):
        self.launch("classic")
        self.assertEqual(self.recorder.launched, _ffmwiz_gui_path())

    def test_an_unknown_engine_falls_back_to_classic(self):
        self.launch("something-else")
        self.assertEqual(self.recorder.launched, _ffmwiz_gui_path())

    def test_qml_only_applies_to_the_unified_editor(self):
        self.launch("qml", mode="video_cut")
        self.assertEqual(self.recorder.launched, _ffmwiz_gui_path(),
                         "the QML engine took over a mode it does not implement")

    def test_a_genuinely_missing_resource_still_falls_back(self):
        # The fallback is a real feature, not a bug: an incomplete install must
        # open the classic editor rather than fail. Proved by pointing the
        # resolver at a driver that has no qml/ beside it.
        missing = ROOT / "tests" / "no_such_driver.py"
        original = guibridge_b._qml_gui_path
        guibridge_b._qml_gui_path = lambda: missing
        self.addCleanup(setattr, guibridge_b, "_qml_gui_path", original)
        self.launch("qml")
        self.assertEqual(self.recorder.launched, _ffmwiz_gui_path())


class _ShimModule:
    """`subprocess` with only `run` replaced, so the module's other uses work."""

    def __init__(self, run) -> None:
        self.run = run

    def __getattr__(self, name):
        return getattr(subprocess, name)


if __name__ == "__main__":
    unittest.main()
