"""Regression tests for the join stream-copy compatibility check.

The concat demuxer labels the WHOLE output with input 0's stream parameters, so
two clips it considers "compatible" must really be interchangeable. The
signature compared only codec/width/height/fps/pix_fmt, which let two 320x240
H.264 clips with SAR 1:1 and SAR 2:1 through: the concatenated file carried
input 0's 4:3 aspect for its entire runtime and the second half played squashed,
with no error anywhere.

The guard must not over-tighten either: a file that simply does not declare a
SAR is still square-pixel and must stay copy-compatible.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _video(**overrides):
    stream = {
        "codec_type": "video", "codec_name": "h264", "width": 320, "height": 240,
        "avg_frame_rate": "30/1", "r_frame_rate": "30/1", "pix_fmt": "yuv420p",
        "sample_aspect_ratio": "1:1", "profile": "High", "level": 13,
        "field_order": "progressive",
    }
    stream.update(overrides)
    return stream


def _audio():
    return {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
            "channels": 2, "channel_layout": "stereo"}


def _item(name, video):
    return {"path": Path(tempfile.gettempdir()) / name,
            "streams": [video, _audio()],
            "video_streams": [video], "audio_streams": [_audio()],
            "format": {"duration": "2"}}


class SarNormalisation(unittest.TestCase):
    def test_all_square_pixel_spellings_compare_equal(self):
        square = {None, "", "1:1", "0:1", "1/1", "0/1", "N/A"}
        values = {FFmWiz.normalized_sar_text(v) for v in square}
        self.assertEqual(len(values), 1, f"square-pixel spellings disagreed: {values}")

    def test_a_real_non_square_sar_is_distinct(self):
        self.assertNotEqual(FFmWiz.normalized_sar_text("2:1"), FFmWiz.normalized_sar_text("1:1"))

    def test_slash_and_colon_forms_match(self):
        self.assertEqual(FFmWiz.normalized_sar_text("2/1"), FFmWiz.normalized_sar_text("2:1"))


class JoinCopyCompatibility(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def _compat(self, second_video):
        items = [_item("a.mp4", _video()), _item("b.mp4", second_video)]
        return FFmWiz.join_copy_compatibility(items)

    def test_identical_streams_are_copy_compatible(self):
        ok, reasons = self._compat(_video())
        self.assertTrue(ok, reasons)

    def test_aspect_ratio_mismatch_blocks_the_copy(self):
        ok, reasons = self._compat(_video(sample_aspect_ratio="2:1"))
        self.assertFalse(ok)
        self.assertTrue(any("aspect" in r for r in reasons), reasons)

    def test_profile_mismatch_blocks_the_copy(self):
        ok, _ = self._compat(_video(profile="Constrained Baseline"))
        self.assertFalse(ok)

    def test_level_mismatch_blocks_the_copy(self):
        ok, _ = self._compat(_video(level=41))
        self.assertFalse(ok)

    def test_field_order_mismatch_blocks_the_copy(self):
        ok, _ = self._compat(_video(field_order="tt"))
        self.assertFalse(ok)

    def test_an_undeclared_sar_is_still_compatible(self):
        # Over-tightening would force a needless re-encode on ordinary files.
        first = _item("a.mp4", _video(sample_aspect_ratio=None))
        second = _item("b.mp4", _video(sample_aspect_ratio="1:1"))
        ok, reasons = FFmWiz.join_copy_compatibility([first, second])
        self.assertTrue(ok, reasons)

    def test_fps_only_difference_still_allows_the_vfr_copy_path(self):
        # join_signature_without_fps drops ONLY the frame rate; the new members
        # must survive that slice or the VFR exception would stop working.
        first = _item("a.mp4", _video())
        second = _item("b.mp4", _video(avg_frame_rate="24/1", r_frame_rate="24/1"))
        self.assertEqual(
            FFmWiz.join_signature_without_fps(first),
            FFmWiz.join_signature_without_fps(second),
        )

    def test_fps_free_signature_still_notices_an_aspect_mismatch(self):
        first = _item("a.mp4", _video())
        second = _item("b.mp4", _video(avg_frame_rate="24/1", sample_aspect_ratio="2:1"))
        self.assertNotEqual(
            FFmWiz.join_signature_without_fps(first),
            FFmWiz.join_signature_without_fps(second),
        )


@unittest.skipIf(not FFMPEG or not FFPROBE, "ffmpeg/ffprobe not on PATH")
class JoinCopyCompatibilityRealFiles(unittest.TestCase):
    """Real ffprobe output, because the field names and spellings are the whole
    point of this check."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_joinsar_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _make(self, name, sar="1/1", profile="high"):
        path = self._tmp / name
        # NOT -preset ultrafast: it forces Constrained Baseline regardless of
        # -profile:v, which would make a profile fixture silently identical.
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=1",
             "-f", "lavfi", "-i", "sine=duration=1",
             "-c:v", "libx264", "-profile:v", profile, "-preset", "veryfast",
             "-vf", f"format=yuv420p,setsar={sar}", "-c:a", "aac", str(path)],
            check=True, timeout=180)
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60).stdout)
        return {"path": path, "streams": probe["streams"], "format": probe["format"],
                "video_streams": [s for s in probe["streams"] if s["codec_type"] == "video"],
                "audio_streams": [s for s in probe["streams"] if s["codec_type"] == "audio"]}

    def test_real_identical_files_are_compatible(self):
        ok, reasons = FFmWiz.join_copy_compatibility([self._make("a.mp4"), self._make("a2.mp4")])
        self.assertTrue(ok, reasons)

    def test_real_sar_mismatch_is_rejected(self):
        ok, _ = FFmWiz.join_copy_compatibility([self._make("s1.mp4", sar="1/1"),
                                                self._make("s2.mp4", sar="2/1")])
        self.assertFalse(ok, "SAR 1:1 and 2:1 must not be concat-copied together")

    def test_real_profile_mismatch_is_rejected(self):
        ok, _ = FFmWiz.join_copy_compatibility([self._make("p1.mp4", profile="high"),
                                                self._make("p2.mp4", profile="baseline")])
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
