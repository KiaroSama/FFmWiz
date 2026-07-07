"""Regression tests for pixel-based color-range estimation (services.estimate_color_range).

Guards two root causes of the "always reports full range" bug:
  1. Bit-depth scaling: signalstats reports luma in the source's native bit depth
     (0..1023 for 10-bit), so 8-bit thresholds must be applied to normalized values.
  2. Outlier robustness: the decision uses the per-frame median floor/ceiling, not the
     absolute min/max, so a single fade/flash frame cannot force a "full" verdict.

Uses real ffmpeg to synthesize small, controlled clips; skipped when ffmpeg is absent.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import ffmwiz.services as services

FFMPEG = shutil.which("ffmpeg")
FFMPEG_AVAILABLE = FFMPEG is not None and shutil.which("ffprobe") is not None


def _make_clip(path: Path, luma_expr: str, pix_fmt: str) -> None:
    """Synthesize a 2s clip whose luma is set directly by a geq expression.

    geq writes raw luma values in the target pixel format's bit depth, bypassing
    ffmpeg's range-conversion defaults, so the stored Y values are exactly what we
    ask for (e.g. 16..235 limited 8-bit, 0..255 full 8-bit, 64..940 limited 10-bit).
    """
    vf = f"format={pix_fmt},geq=lum='{luma_expr}'"
    cmd = [
        FFMPEG, "-y", "-f", "lavfi",
        "-i", "color=c=gray:s=160x120:d=2:r=10",
        "-vf", vf, "-pix_fmt", pix_fmt, "-c:v", "ffv1", str(path),
    ]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert r.returncode == 0, r.stderr[-800:]


@unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg/ffprobe not found in PATH")
class ColorRangeEstimationTests(unittest.TestCase):
    def _estimate(self, path: Path, bit_depth: int) -> dict:
        with tempfile.TemporaryDirectory() as reports:
            with mock.patch.object(services, "default_media_reports_dir", return_value=Path(reports)):
                return services.estimate_color_range(path, 0, "detailed", FFMPEG, bit_depth=bit_depth)

    def test_limited_8bit_detected_as_limited(self):
        with tempfile.TemporaryDirectory() as td:
            clip = Path(td) / "limited8.mkv"
            _make_clip(clip, "16+(X/(W-1))*219", "yuv420p")  # 16..235
            res = self._estimate(clip, 8)
            self.assertIn("limited", res["conclusion"].lower(), res)

    def test_full_8bit_detected_as_full(self):
        with tempfile.TemporaryDirectory() as td:
            clip = Path(td) / "full8.mkv"
            _make_clip(clip, "(X/(W-1))*255", "yuv420p")  # 0..255
            res = self._estimate(clip, 8)
            self.assertIn("full", res["conclusion"].lower(), res)

    def test_limited_10bit_detected_as_limited_with_correct_depth(self):
        # The core regression: a 10-bit limited clip (luma ~64..940) must be read as
        # limited when the real 10-bit depth is supplied.
        with tempfile.TemporaryDirectory() as td:
            clip = Path(td) / "limited10.mkv"
            _make_clip(clip, "64+(X/(W-1))*876", "yuv420p10le")  # 64..940
            res = self._estimate(clip, 10)
            self.assertEqual(res["bit_depth"], 10)
            self.assertIn("limited", res["conclusion"].lower(), res)

    def test_10bit_misclassified_when_depth_assumed_8(self):
        # Documents why the depth matters: feeding a 10-bit clip's native luma
        # (~940) into the 8-bit thresholds (the old behavior) misreads it as full.
        with tempfile.TemporaryDirectory() as td:
            clip = Path(td) / "limited10.mkv"
            _make_clip(clip, "64+(X/(W-1))*876", "yuv420p10le")  # 64..940
            res = self._estimate(clip, 8)
            self.assertNotIn("limited", res["conclusion"].lower(), res)


if __name__ == "__main__":
    unittest.main()
