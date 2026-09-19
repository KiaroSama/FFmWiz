"""A04: actual PCM must honor the selected stream in both editor engines."""
from __future__ import annotations

import array
import copy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from ffmwiz.gui.classic.gui_editor_unified import build_classic_waveform_args, build_join_segment_model
from ffmwiz.gui.modern.gui_qml_waveform import build_wave_decode_args, compute_wave_key

FFMPEG = shutil.which("ffmpeg")


@unittest.skipUnless(FFMPEG, "ffmpeg required for actual PCM comparison")
class SelectedStream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="ffmwiz_stream_contract_")
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / "two_tracks.mkv"
        command = [FFMPEG, "-hide_banner", "-v", "error", "-y",
                   "-f", "lavfi", "-i", "color=size=64x48:rate=10:duration=2",
                   "-f", "lavfi", "-i", "aevalsrc=if(eq(n\\,1000)\\,0.8\\,0):s=4000:d=2",
                   "-f", "lavfi", "-i", "aevalsrc=if(eq(n\\,5000)\\,0.8\\,0):s=4000:d=2",
                   "-map", "0:v", "-map", "1:a", "-map", "2:a",
                   "-c:v", "ffv1", "-c:a", "pcm_s16le", str(cls.source)]
        subprocess.run(command, check=True, capture_output=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def decode(self, req, classic):
        out = self.root / ("classic.pcm" if classic else "qml.pcm")
        args = (build_classic_waveform_args(req, build_join_segment_model(req), out)
                if classic else build_wave_decode_args(req, str(out)))
        result = subprocess.run([FFMPEG, *args], capture_output=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        samples = array.array("h")
        samples.frombytes(out.read_bytes())
        return samples

    def assert_pcm(self, req, expected_peaks, count):
        outputs = [self.decode(req, classic) for classic in (False, True)]
        self.assertEqual(outputs[0], outputs[1], "Classic and QML decoded different tracks")
        for samples in outputs:
            self.assertEqual(count, len(samples))
            peaks = [i for i, value in enumerate(samples) if abs(value) > 20000]
            self.assertEqual(expected_peaks, peaks)

    def test_single_selected_track_is_not_replaced_by_track_zero(self):
        self.assert_pcm({"input_path": str(self.source), "duration": 2,
                         "audio_stream": "a:1"}, [5000], 8000)

    def test_join_overrides_inherit_once_and_silent_segments_stay_silent(self):
        req = {"duration": 6, "audio_stream": "a:1", "join_segments": [
            {"path": str(self.source), "duration": 2, "has_audio": True},
            {"path": str(self.source), "duration": 2, "has_audio": True, "audio_stream": "a:0"},
            {"path": str(self.source), "duration": 2, "has_audio": False}]}
        self.assert_pcm(req, [5000, 9000], 24000)

    def test_picture_origin_and_stream_selection_are_applied_together(self):
        req = {"duration": 1, "join_segments": [
            {"path": str(self.source), "duration": 1, "has_audio": True,
             "picture_clock_offset": 1, "audio_stream": "a:1"}]}
        self.assert_pcm(req, [1000], 4000)

    def test_default_track_and_unknown_duration_are_preserved(self):
        self.assert_pcm({"input_path": str(self.source)}, [1000], 8000)


class CacheIdentity(unittest.TestCase):
    def test_each_decode_relevant_segment_field_changes_the_key(self):
        req = {"duration": 2, "join_segments": [
            {"path": "a.mkv", "duration": 2, "has_audio": True,
             "audio_stream": "a:0", "picture_clock_offset": 0}]}
        for key, value in (("audio_stream", "a:1"), ("picture_clock_offset", 1), ("has_audio", False)):
            with self.subTest(field=key):
                changed = copy.deepcopy(req)
                changed["join_segments"][0][key] = value
                self.assertNotEqual(compute_wave_key(req), compute_wave_key(changed))

    def test_single_origin_is_part_of_decode_identity(self):
        req = {"input_path": "a.mkv", "duration": 2}
        self.assertNotEqual(compute_wave_key(req), compute_wave_key(dict(req, picture_clock_offset=1)))


if __name__ == "__main__":
    unittest.main()
