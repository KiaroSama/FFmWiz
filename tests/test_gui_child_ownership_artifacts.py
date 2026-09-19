"""A03: both worker lanes retain artifacts until the actual writer is gone."""
from __future__ import annotations

import gc
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from ffmwiz.gui.gui_child_owner import ChildProcessOwner
from ffmwiz.gui.modern.gui_qml_bridge import build_bridge

try:
    from PySide6.QtCore import QObject, Property, Signal, Slot, QCoreApplication
except ImportError:
    QT = None
else:
    QT = (QObject, Slot, Signal, Property)


class DummySignal:
    def __init__(self, *args):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


def plain_slot(*args, **kwargs):
    return lambda function: function


def plain_property(*args, **kwargs):
    return property(args[1])


class ArtifactOwnership(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix="ffmwiz_artifact_guard_")
        self.addCleanup(self.root.cleanup)
        self.old_temp = tempfile.tempdir
        tempfile.tempdir = self.root.name
        self.addCleanup(setattr, tempfile, "tempdir", self.old_temp)
        self.children = []
        self.addCleanup(self.reap)

    def reap(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            if child.stderr:
                child.stderr.close()

    def make_bridge(self, native=False):
        symbols = QT if native else (object, plain_slot, DummySignal, plain_property)
        if native:
            self.app = QCoreApplication.instance() or QCoreApplication([])
        else:
            self.app = None
        cls = build_bridge(*symbols, lambda result: None, lambda level, text: None,
                           lambda req, spec: False, lambda width: "reverse")
        with mock.patch("ffmwiz.gui.modern.gui_qml_bridge.ChildProcessOwner",
                        side_effect=lambda: ChildProcessOwner(kill_timeout=0.1)):
            bridge = cls(self.app, {"input_path": "unused.mkv", "duration": 1,
                                   "has_audio": True, "ffmpeg": "unused"})
        self.addCleanup(bridge.cleanup_reverse)
        return bridge

    def deny_writer(self, owner):
        real_start = owner.start

        def start(args, **kwargs):
            path = Path(args[-1])
            path.write_bytes(b"writer-owned output")
            child = real_start([sys.executable, "-c", "import time; time.sleep(30)"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.children.append(child)
            child.communicate = mock.Mock(side_effect=OSError("communication failed"))
            child.terminate = mock.Mock(side_effect=PermissionError("terminate denied"))
            child.kill = mock.Mock(side_effect=PermissionError("kill denied"))
            return child

        owner.start = start

    def restore_writer(self, child):
        # Undo only this fixture's fault injection; never kill an unrelated PID.
        for name in ("communicate", "terminate", "kill"):
            child.__dict__.pop(name, None)

    def test_reverse_failed_stop_retains_proxy_until_retry(self):
        for native in ([False, True] if QT else [False]):
            with self.subTest(native_qt=native):
                bridge = self.make_bridge(native)
                self.deny_writer(bridge._reverse_children)
                try:
                    bridge._render_reverse({"gen": 1})
                    path = Path(bridge._rev_temp.name) / "rev_1.mp4"
                    child = self.children[-1]
                    self.assertIsNone(child.poll())
                    self.assertTrue(path.exists(), "live reverse writer lost its proxy")
                    self.assertIn(path, bridge._reverse_children.retained_artifacts())
                    self.assertFalse(bridge.cleanup_reverse())
                    self.assertTrue(path.exists())
                finally:
                    self.restore_writer(self.children[-1])
                self.assertTrue(bridge.cleanup_reverse())
                self.assertIsNotNone(child.poll())
                self.assertFalse(path.exists())

    def test_wave_failed_stop_keeps_the_existing_retention_contract(self):
        bridge = self.make_bridge()
        self.deny_writer(bridge._wave_children)
        try:
            with self.assertRaises(OSError):
                bridge._load_pcm("test")
            child = self.children[-1]
            self.assertIsNone(child.poll())
            kept = bridge._wave_children.retained_artifacts()
            self.assertEqual(1, len(kept))
            self.assertTrue(kept[0].exists())
        finally:
            self.restore_writer(self.children[-1])
        self.assertTrue(bridge.cleanup_reverse())
        self.assertFalse(kept[0].exists())

    def test_discard_failure_is_retained_for_retry(self):
        bridge = self.make_bridge()
        path = Path(bridge._rev_temp.name) / "closed_proxy.mp4"
        path.write_bytes(b"completed proxy")
        with mock.patch("os.remove", side_effect=PermissionError("busy reader")):
            bridge._discard_proxy(str(path))
        self.assertIn(path, bridge._reverse_children.retained_artifacts())
        self.assertTrue(bridge.cleanup_reverse())
        self.assertFalse(path.exists())

    def test_failed_thread_start_releases_both_worker_lanes(self):
        for lane in ("reverse", "waveform"):
            with self.subTest(lane=lane):
                bridge = self.make_bridge()
                with mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("thread unavailable")):
                    if lane == "reverse":
                        bridge.renderReverse('{"gen":1}')
                    else:
                        bridge.startWaveform()
                self.assertEqual(0, bridge._reverse_children.active_workers())
                self.assertEqual(0, bridge._wave_children.active_workers())
                self.assertTrue(bridge.cleanup_reverse())

    def test_directory_finalization_does_not_delete_a_live_writers_file(self):
        # No Qt is needed to prove the lifetime of the directory itself.
        bridge = self.make_bridge()
        directory = bridge._rev_temp
        path = Path(directory.name) / "live.mp4"
        path.write_bytes(b"owned")
        owner = bridge._reverse_children
        child = owner.start([sys.executable, "-c", "import time; time.sleep(30)"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(child)
        bridge._rev_temp = tempfile.TemporaryDirectory(prefix="ffmwiz_replacement_")
        del directory
        gc.collect()
        self.assertTrue(path.exists(), "directory finalizer deleted a live child's output")
        child.kill()
        child.wait(timeout=5)
        owner.finish(child)
        shutil.rmtree(path.parent)


if __name__ == "__main__":
    unittest.main()
