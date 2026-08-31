"""Tests for the modern (QML) classic-quality waveform model.

The heavy waveform work lives in module-level functions in ffmwiz_gui_qml.py so
it can be unit-tested without Qt. These cover: cache-key invalidation, min/max
pair output (not single peaks), deep-zoom detail vs the overview, full-scale
amplitude (vs int16 32768, not the clip's own peak), and join-timeline decoding.
"""
import os
import sys
import unittest
from pathlib import Path

# The QML driver lives in ffmwiz/gui; add it so we can import the pure
# waveform helpers. Importing it does NOT require PySide6 (its Qt imports are
# inside main(); the palette import is guarded).
_ROOT = Path(__file__).resolve().parents[1]
# The QML engine lives in gui/modern; the palette it reads is SHARED and
# stays at gui/, so both directories go on the path.
_GUI_DIR = _ROOT / "ffmwiz" / "gui" / "modern"
for _p in (str(_ROOT / "ffmwiz" / "gui"), str(_GUI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ffmwiz_gui_qml as Q  # noqa: E402

try:
    import numpy as np
    _HAS_NUMPY = True
except Exception:
    _HAS_NUMPY = False

requires_numpy = unittest.skipUnless(_HAS_NUMPY, "numpy required for waveform math")


class WaveCacheKeyTests(unittest.TestCase):
    def test_key_stable_for_same_request(self):
        req = {"input_path": "a.mkv", "duration": 10.0}
        self.assertEqual(Q.compute_wave_key(req), Q.compute_wave_key(dict(req)))

    def test_key_changes_with_input_duration_and_join(self):
        base = {"input_path": "a.mkv", "duration": 10.0}
        k0 = Q.compute_wave_key(base)
        self.assertNotEqual(k0, Q.compute_wave_key({"input_path": "b.mkv", "duration": 10.0}))
        self.assertNotEqual(k0, Q.compute_wave_key({"input_path": "a.mkv", "duration": 20.0}))
        joined = {"duration": 10.0, "join_segments": [
            {"path": "a.mkv", "duration": 5.0}, {"path": "b.mkv", "duration": 5.0}]}
        self.assertNotEqual(k0, Q.compute_wave_key(joined))
        # Different join order / list => different key.
        joined2 = {"duration": 10.0, "join_segments": [
            {"path": "b.mkv", "duration": 5.0}, {"path": "a.mkv", "duration": 5.0}]}
        self.assertNotEqual(Q.compute_wave_key(joined), Q.compute_wave_key(joined2))


class WaveDecodeArgsTests(unittest.TestCase):
    def test_single_input_uses_4000hz_mono(self):
        args = Q.build_wave_decode_args({"input_path": "in.mkv"}, "out.pcm")
        joined = " ".join(args)
        self.assertIn("aresample=4000", joined)
        self.assertIn("channel_layouts=mono", joined)
        self.assertIn("pcm_s16le", joined)
        self.assertIn("in.mkv", args)

    def test_join_uses_concat_over_all_inputs(self):
        req = {"join_segments": [
            {"path": "v0.mkv", "duration": 5.0},
            {"path": "v1.mkv", "duration": 5.0},
            {"path": "v2.mkv", "duration": 5.0}]}
        args = Q.build_wave_decode_args(req, "out.pcm")
        joined = " ".join(args)
        self.assertIn("concat=n=3:v=0:a=1", joined)
        for p in ("v0.mkv", "v1.mkv", "v2.mkv"):
            self.assertIn(p, args)
        self.assertEqual(args.count("-i"), 3)  # one -i per joined input


@requires_numpy
class WaveformWindowTests(unittest.TestCase):
    RATE = 4000

    def _pcm(self, samples):
        return np.asarray(samples, dtype=np.int16)

    def test_returns_minmax_pairs(self):
        # A signal that swings negative and positive must yield min<max pairs.
        n = self.RATE * 4
        t = np.arange(n)
        pcm = (20000 * np.sin(t / 50.0)).astype(np.int16)
        emn, emx = Q.build_wave_envelope(pcm)
        pairs = Q.waveform_window(pcm, emn, emx, self.RATE, 0.0, 4.0, 200)
        self.assertGreater(len(pairs), 1)
        for mn, mx in pairs:
            self.assertLessEqual(mn, mx)               # min/max, not a single peak
            self.assertGreaterEqual(mn, -1.0 - 1e-6)
            self.assertLessEqual(mx, 1.0 + 1e-6)
        # At least one column must actually carry a negative min (true min/max,
        # not absolute-peak-only).
        self.assertTrue(any(mn < -0.1 for mn, _ in pairs))

    def test_full_scale_not_normalized_to_clip_peak(self):
        # A constant half-scale signal must read ~0.5, NOT 1.0 (i.e. it is scaled
        # against int16 full scale, not the clip's own maximum).
        pcm = self._pcm([16384] * (self.RATE * 2))
        emn, emx = Q.build_wave_envelope(pcm)
        pairs = Q.waveform_window(pcm, emn, emx, self.RATE, 0.0, 2.0, 100)
        self.assertTrue(pairs)
        peak = max(mx for _, mx in pairs)
        self.assertAlmostEqual(peak, 0.5, delta=0.02)

    def test_deep_zoom_reveals_detail_overview_compresses(self):
        # First 1% of the clip is loud, the rest silent. The full-timeline
        # overview compresses the loud region into ~1 column; a deep zoom on that
        # region fills (almost) every column with loud data.
        n = self.RATE * 100  # 100s
        pcm = np.zeros(n, dtype=np.int16)
        loud_len = n // 100  # first 1%
        pcm[:loud_len] = 30000
        emn, emx = Q.build_wave_envelope(pcm)
        dur = n / self.RATE
        overview = Q.waveform_window(pcm, emn, emx, self.RATE, 0.0, dur, 100)
        zoomed = Q.waveform_window(pcm, emn, emx, self.RATE, 0.0, dur * 0.01, 100)
        loud_over = sum(1 for _, mx in overview if mx > 0.5)
        loud_zoom = sum(1 for _, mx in zoomed if mx > 0.5)
        self.assertLessEqual(loud_over, 3)        # overview crushes it to ~1 column
        self.assertGreater(loud_zoom, 50)         # deep zoom resolves the loud region

    def test_window_clamps_and_handles_empty(self):
        pcm = self._pcm([100] * (self.RATE * 2))
        emn, emx = Q.build_wave_envelope(pcm)
        # Out-of-range / inverted window must not throw and returns a list.
        self.assertIsInstance(Q.waveform_window(pcm, emn, emx, self.RATE, -5.0, 1.0, 50), list)
        self.assertEqual(Q.waveform_window(None, None, None, self.RATE, 0.0, 1.0, 50), [])


class QmlPaletteAndLoggingTests(unittest.TestCase):
    """D23: the QML engine must reuse the classic palette/log writer, not a
    hand-copied fallback dict."""

    def test_palette_comes_from_gui_style_not_fallback(self):
        import gui_style
        self.assertEqual(set(Q._PALETTE), set(gui_style.PALETTE))
        self.assertEqual(Q._PALETTE["cut_red"], gui_style.PALETTE["cut_red"])

    def test_classic_log_writer_is_wired(self):
        self.assertIsNotNone(Q._classic_write_log)

    def test_qml_palette_covers_every_col_key(self):
        """USER-12-2: every col("key") used by the QML must exist in the palette."""
        import re
        qml = "\n".join(p.read_text(encoding="utf-8")
                      for p in sorted((_GUI_DIR / "qml").glob("*.qml")))
        keys = set(re.findall(r'col\("([a-z_0-9]+)"', qml))
        self.assertTrue(keys)
        missing = sorted(k for k in keys if k not in Q._PALETTE)
        self.assertEqual(missing, [])

    def test_qml_col_literals_match_palette(self):
        """The inline fallback literal must not drift from the palette value."""
        import re
        qml = "\n".join(p.read_text(encoding="utf-8")
                      for p in sorted((_GUI_DIR / "qml").glob("*.qml")))
        drift = []
        for key, literal in re.findall(r'col\("([a-z_0-9]+)",\s*"(#[0-9a-fA-F]{6})"\)', qml):
            want = Q._PALETTE.get(key)
            if want and want.lower() != literal.lower():
                drift.append((key, literal, want))
        self.assertEqual(drift, [])


if __name__ == "__main__":
    unittest.main()
