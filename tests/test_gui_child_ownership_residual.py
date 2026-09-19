"""A03: actual bridge methods keep failed workers and their resources accountable."""
from __future__ import annotations

import gc
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from ffmwiz.gui.modern import gui_qml_bridge as module
from ffmwiz.gui.modern.ffmwiz_gui_qml import build_reverse_proxy_vf, reverse_proxy_wants_audio

try:
    from PySide6.QtCore import QObject, Property, Signal, Slot
    HAVE_QT = True
except ImportError:
    HAVE_QT = False
    # Local tests execute the production factory with only Qt's binding surface
    # replaced. The equipped GUI CI selects this module and uses real Qt symbols.
    class QObject:
        pass

    class _BoundSignal:
        def __init__(self):
            self.listeners = []

        def connect(self, callback):
            self.listeners.append(callback)

        def emit(self, *args):
            for listener in self.listeners:
                listener(*args)

    class Signal:
        def __init__(self, *types):
            pass

        def __set_name__(self, owner, name):
            self.name = name

        def __get__(self, instance, owner):
            if instance is None:
                return self
            return instance.__dict__.setdefault(self.name, _BoundSignal())

    def Slot(*args, **kwargs):
        return lambda function: function

    def Property(_type, getter, **kwargs):
        return property(getter)


class FaultChild:
    """A real PID whose communication and stop operations fail deterministically."""
    def __init__(self, path):
        Path(path).write_bytes(b"partial output")
        self.real = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)

    def poll(self):
        return self.real.poll()

    def wait(self, timeout=None):
        return self.real.wait(timeout=timeout)

    def communicate(self):
        raise OSError("injected pipe failure")

    def terminate(self):
        raise PermissionError("injected terminate denial")

    def kill(self):
        raise PermissionError("injected kill denial")


class BridgeFailureOwnership(unittest.TestCase):
    def setUp(self):
        self.replies, self.logs, self.signals, self.children = [], [], [], []
        self.app = SimpleNamespace(quit=mock.Mock())
        self.Bridge = module.build_bridge(QObject, Slot, Signal, Property,
                                         self.replies.append, lambda *args: self.logs.append(args),
                                         reverse_proxy_wants_audio, build_reverse_proxy_vf)
        self.bridge = self.Bridge(self.app, {"input_path": "unused.mkv", "duration": 1,
                                             "has_audio": True})
        self.bridge._reverse_children._kill_timeout = .1
        self.bridge.reverseReady.connect(lambda *args: self.signals.append(args))
        self.addCleanup(self.reap)

    def reap(self):
        for proc in self.children:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
        if self.bridge._rev_temp is not None:
            self.bridge.cleanup_reverse()

    def faulty_reverse(self):
        owner = self.bridge._reverse_children
        original = owner.start
        def start(args, **kwargs):
            child = FaultChild(args[-1])
            self.children.append(child.real)
            return original(args, generation=1, popen=lambda *a, **k: child)
        with mock.patch.object(owner, "start", side_effect=start):
            self.bridge._render_reverse({"gen": 1, "dur": .1})
        return Path(self.bridge._rev_temp.name) / "rev_1.mp4"

    def test_reverse_pipe_failure_retains_the_live_writers_file(self):
        path = self.faulty_reverse()
        self.assertIsNone(self.children[0].poll())
        self.assertTrue(path.exists(), "a live writer's proxy was deleted")
        self.assertIn(path, self.bridge._reverse_children.retained_artifacts())
        self.children[0].kill()
        self.children[0].wait(timeout=10)
        self.assertTrue(self.bridge.cleanup_reverse())
        self.assertFalse(path.exists())

    def test_failed_shutdown_is_not_undone_by_temporary_directory_finalization(self):
        self.faulty_reverse()
        directory = Path(self.bridge._rev_temp.name)
        try:
            self.assertFalse(self.bridge.cleanup_reverse())
            self.bridge._rev_temp = None
            gc.collect()
            self.assertTrue(directory.exists(), "GC removed explicitly retained resources")
        finally:
            for proc in self.children:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=10)
            shutil.rmtree(directory, ignore_errors=True)

    def test_waveform_thread_launch_failure_rolls_back_its_worker(self):
        with mock.patch.object(module.threading.Thread, "start", side_effect=RuntimeError("no thread")):
            self.bridge.startWaveform()
        self.assertEqual(0, self.bridge._wave_children.active_workers())
        self.assertTrue(self.bridge.cleanup_reverse())

    def test_reverse_thread_launch_failure_rolls_back_its_worker(self):
        with mock.patch.object(module.threading.Thread, "start", side_effect=RuntimeError("no thread")):
            self.bridge.renderReverse(json.dumps({"gen": 1}))
        self.assertEqual(0, self.bridge._reverse_children.active_workers())
        self.assertTrue(self.bridge.cleanup_reverse())

    def test_valid_json_of_the_wrong_type_returns_an_error_reply(self):
        for value in ([], None, 3, "not an object"):
            with self.subTest(value=value):
                self.bridge.submit(json.dumps(value))
                self.assertEqual("error", self.replies[-1]["status"])
        self.assertEqual(4, self.app.quit.call_count)

    def test_temporary_pcm_delete_failure_is_owned_until_retry(self):
        owner = self.bridge._wave_children
        original = owner.start
        def start(args, **kwargs):
            process = original([sys.executable, "-c",
                                "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'\\0\\0'*4000)",
                                args[-1]], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.children.append(process)
            return process
        with mock.patch.object(owner, "start", side_effect=start), mock.patch.object(
                module.os, "remove", side_effect=PermissionError("transient PCM lock")):
            self.bridge._load_pcm("key")
        kept = owner.retained_artifacts()
        self.assertEqual(1, len(kept), "failed deletion lost ownership of the PCM")
        self.assertTrue(kept[0].exists())
        self.assertTrue(self.bridge.cleanup_reverse())
        self.assertFalse(kept[0].exists())

    def test_invalid_reverse_payload_never_registers_work(self):
        for payload in ("null", "[]", "3", '{"gen": "invalid"}'):
            with self.subTest(payload=payload):
                self.bridge.renderReverse(payload)
                self.assertEqual(0, self.bridge._reverse_children.active_workers())
                self.assertEqual(0, self.bridge._reverse_children.active_children())

    def test_waveform_thread_constructor_failure_rolls_back_its_worker(self):
        with mock.patch.object(module.threading, "Thread", side_effect=RuntimeError("no thread")):
            self.bridge.startWaveform()
        self.assertEqual(0, self.bridge._wave_children.active_workers())
        self.assertTrue(self.bridge.cleanup_reverse())


if __name__ == "__main__":
    unittest.main()
