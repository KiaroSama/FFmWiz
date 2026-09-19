"""A04: selection survives request -> Classic segment model -> real PCM decode."""
from __future__ import annotations

import array
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from ffmwiz.gui.classic.gui_editor_unified import build_classic_waveform_args, build_join_segment_model
from ffmwiz.gui.modern.gui_qml_waveform import build_wave_decode_args

FFMPEG = shutil.which("ffmpeg")


@unittest.skipUnless(FFMPEG, "ffmpeg is required for waveform stream-selection tests")
class StreamSelection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffmwiz_wave_selection_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "tracks.mkv"
        result = subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y",
                                 "-f", "lavfi", "-i", "anullsrc=r=4000:cl=mono:d=1",
                                 "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=4000:duration=1",
                                 "-map", "0:a", "-map", "1:a", "-c:a", "pcm_s16le", str(self.source)],
                                capture_output=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)

    def compare(self, request, *, expected_peak):
        outputs = []
        for name in ("classic", "qml"):
            target = self.root / (name + ".pcm")
            args = (build_classic_waveform_args(request, build_join_segment_model(request), target)
                    if name == "classic" else build_wave_decode_args(request, str(target)))
            result = subprocess.run([FFMPEG, *args], capture_output=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            outputs.append(target.read_bytes())
        self.assertEqual(outputs[0], outputs[1], "the two editors decoded different tracks")
        pcm = array.array("h", outputs[0])
        peak = max(abs(value) for value in pcm)
        self.assertEqual(round(request["duration"] * 4000), len(pcm))
        if expected_peak:
            self.assertGreater(peak, 1000)
        else:
            self.assertEqual(0, peak)

    def request(self):
        return {"input_path": str(self.source), "duration": 1, "has_audio": True,
                "audio_stream": "a:1"}

    def test_single_input_uses_the_selected_stream(self):
        self.compare(self.request(), expected_peak=True)

    def test_join_inherits_the_request_selection(self):
        request = self.request()
        request["join_segments"] = [{"path": str(self.source), "duration": 1, "has_audio": True}]
        self.compare(request, expected_peak=True)

    def test_segment_override_wins_over_request_selection(self):
        request = self.request()
        request["join_segments"] = [{"path": str(self.source), "duration": 1,
                                     "has_audio": True, "audio_stream": "a:0"}]
        self.compare(request, expected_peak=False)

    def test_generated_silence_does_not_request_a_nonexistent_stream(self):
        request = self.request()
        request["join_segments"] = [{"path": "not-opened.mkv", "duration": 1,
                                     "has_audio": False}]
        self.compare(request, expected_peak=False)


if __name__ == "__main__":
    unittest.main()
