"""Actual Classic callback rejects failed QProcess output, even when PCM exists."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest import mock

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtGui, QtWidgets
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 is required for the native Classic QProcess tests")
class ClassicWaveformCompletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        # The real entry module assembles the editor namespace before its factory
        # runs. No extracted method or substituted success predicate is tested.
        from ffmwiz.gui.classic import ffmwiz_gui
        self.window = ffmwiz_gui.build_unified_video_editor(
            {"input_path": "unused.mkv", "duration": 1, "source_w": 64,
             "source_h": 48, "has_audio": False})
        self.addCleanup(self.dispose)
        self.window.timeline.set_pcm = mock.Mock()
        self.process = QtCore.QProcess(self.window)
        self.window._wave_proc = self.process

    def dispose(self):
        if self.process.state() != QtCore.QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(5000)
        self.window.close()
        self.window.deleteLater()

    def finish_decode(self, exit_code, sample_bytes=8000):
        script = ("from pathlib import Path; import sys; "
                  "Path(sys.argv[1]).write_bytes(b'\\0'*int(sys.argv[2])); "
                  "sys.exit(int(sys.argv[3]))")
        self.process.start(sys.executable,
                           ["-c", script, str(self.window._wave_path), str(sample_bytes), str(exit_code)])
        self.assertTrue(self.process.waitForFinished(15000))
        self.window._waveform_finished(self.process.exitCode(), self.process.exitStatus())

    def test_failed_decode_does_not_cache_partial_pcm(self):
        self.finish_decode(17)
        self.window.timeline.set_pcm.assert_not_called()
        self.assertIn("could not be generated", self.window.status.text())

    def test_successful_decode_is_cached(self):
        self.finish_decode(0)
        self.window.timeline.set_pcm.assert_called_once_with(b"\0" * 8000, 4000)

    def test_odd_pcm_sample_is_not_cached(self):
        self.finish_decode(0, sample_bytes=3)
        self.window.timeline.set_pcm.assert_not_called()

    def test_empty_pcm_is_not_cached(self):
        self.finish_decode(0, sample_bytes=0)
        self.window.timeline.set_pcm.assert_not_called()

    def test_failed_stop_keeps_files_and_rejects_close_until_retry(self):
        marker = self.window._wave_path
        marker.write_bytes(b"owned waveform")
        self.process.start(sys.executable, ["-c", "import time; time.sleep(120)"])
        self.assertTrue(self.process.waitForStarted(10000))
        real = self.process

        class DeniedStop:
            def state(self):
                return real.state()
            def kill(self):
                pass
            def waitForFinished(self, _timeout):
                return False

        self.window._wave_proc = DeniedStop()
        event = QtGui.QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertEqual(b"owned waveform", marker.read_bytes())
        self.assertNotEqual(QtCore.QProcess.NotRunning, real.state())
        self.window._wave_proc = real
        retry = QtGui.QCloseEvent()
        self.window.closeEvent(retry)
        self.assertTrue(retry.isAccepted())
        self.assertEqual(QtCore.QProcess.NotRunning, real.state())
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
