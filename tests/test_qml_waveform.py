"""Tests for the modern (QML) classic-quality waveform model.

The heavy waveform work lives in module-level functions in ffmwiz_gui_qml.py so
it can be unit-tested without Qt. These cover: cache-key invalidation, min/max
pair output (not single peaks), deep-zoom detail vs the overview, full-scale
amplitude (vs int16 32768, not the clip's own peak), and join-timeline decoding.
"""
import json
import os
import re
import sys
import array
import math
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


class WaveformWindowTests(unittest.TestCase):
    """The waveform maths, in BOTH representations it really runs in.

    numpy is an accelerator here, not a requirement: without it the model works
    on a stdlib `array('h')`, and that is what a user who never installed numpy
    sees. These tests used numpy only to BUILD their fixtures and were gated on
    it, so the stdlib branch was never exercised -- and a CI leg with no numpy
    installed simply skipped them, proving nothing (A01/CI).

    Each case now runs against every representation available: the stdlib array
    always, the numpy array when numpy is installed. `build_wave_envelope` and
    `waveform_window` branch on the type, so this is what covers both branches.
    """

    RATE = 4000

    def representations(self, samples):
        """(label, pcm) for every array type this model accepts."""
        clipped = [max(-32768, min(32767, int(value))) for value in samples]
        yield "array", array.array("h", clipped)
        if _HAS_NUMPY:
            yield "numpy", np.asarray(clipped, dtype=np.int16)

    def test_returns_minmax_pairs(self):
        # A signal that swings negative and positive must yield min<max pairs.
        n = self.RATE * 4
        samples = [int(20000 * math.sin(i / 50.0)) for i in range(n)]
        for label, pcm in self.representations(samples):
            with self.subTest(pcm=label):
                emn, emx = Q.build_wave_envelope(pcm)
                pairs = Q.waveform_window(pcm, emn, emx, self.RATE, 0.0, 4.0, 200)
                self.assertGreater(len(pairs), 1)
                for mn, mx in pairs:
                    self.assertLessEqual(mn, mx)       # min/max, not a single peak
                    self.assertGreaterEqual(mn, -1.0 - 1e-6)
                    self.assertLessEqual(mx, 1.0 + 1e-6)
                # At least one column must actually carry a negative min (true
                # min/max, not absolute-peak-only).
                self.assertTrue(any(mn < -0.1 for mn, _ in pairs))

    def test_full_scale_not_normalized_to_clip_peak(self):
        # A constant half-scale signal must read ~0.5, NOT 1.0 (i.e. it is scaled
        # against int16 full scale, not the clip's own maximum).
        for label, pcm in self.representations([16384] * (self.RATE * 2)):
            with self.subTest(pcm=label):
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
        loud_len = n // 100  # first 1%
        samples = [30000] * loud_len + [0] * (n - loud_len)
        dur = n / self.RATE
        for label, pcm in self.representations(samples):
            with self.subTest(pcm=label):
                emn, emx = Q.build_wave_envelope(pcm)
                overview = Q.waveform_window(pcm, emn, emx, self.RATE, 0.0, dur, 100)
                zoomed = Q.waveform_window(pcm, emn, emx, self.RATE, 0.0, dur * 0.01, 100)
                loud_over = sum(1 for _, mx in overview if mx > 0.5)
                loud_zoom = sum(1 for _, mx in zoomed if mx > 0.5)
                self.assertLessEqual(loud_over, 3)   # overview crushes it to ~1 column
                self.assertGreater(loud_zoom, 50)    # deep zoom resolves it

    def test_window_clamps_and_handles_empty(self):
        for label, pcm in self.representations([100] * (self.RATE * 2)):
            with self.subTest(pcm=label):
                emn, emx = Q.build_wave_envelope(pcm)
                # Out-of-range / inverted window must not throw, returns a list.
                self.assertIsInstance(
                    Q.waveform_window(pcm, emn, emx, self.RATE, -5.0, 1.0, 50), list)
                self.assertEqual(
                    Q.waveform_window(None, None, None, self.RATE, 0.0, 1.0, 50), [])

    def test_both_representations_agree_column_for_column(self):
        """The accelerator must not change the answer, only the speed."""
        if not _HAS_NUMPY:
            self.skipTest("numpy is absent, so there is no second representation "
                          "to compare against; the stdlib path is covered above")
        samples = [int(18000 * math.sin(i / 37.0)) for i in range(self.RATE * 3)]
        results = []
        for _label, pcm in self.representations(samples):
            emn, emx = Q.build_wave_envelope(pcm)
            results.append(Q.waveform_window(pcm, emn, emx, self.RATE, 0.2, 2.6, 120))
        self.assertEqual(len(results), 2)
        for index, (left, right) in enumerate(zip(*results)):
            with self.subTest(column=index):
                self.assertAlmostEqual(left[0], right[0], places=6)
                self.assertAlmostEqual(left[1], right[1], places=6)


class QmlPaletteAndLoggingTests(unittest.TestCase):
    """D23: the QML engine must reuse the classic palette/log writer, not a
    hand-copied fallback dict."""

    def test_palette_comes_from_gui_style_not_fallback(self):
        import gui_style
        self.assertEqual(set(Q._PALETTE), set(gui_style.PALETTE))
        self.assertEqual(Q._PALETTE["cut_red"], gui_style.PALETTE["cut_red"])

    def test_every_qml_icon_resolves_and_is_not_currentcolor(self):
        """An iconSource must exist AND be a colour Qt can actually paint.

        Two separate defects have hidden here. The first was a path that did
        not resolve (wrong directory and wrong filename), which Qt reports by
        drawing nothing at all. The second was subtler: the file resolved, but
        its stroke was `currentColor` -- an SVG 1.1/CSS feature Qt's SVG Tiny
        renderer does not implement, so it fell back to BLACK and the icon was
        invisible on a dark button. Measured with QSvgRenderer: lucide_hand.svg
        painted rgb(0,0,0), tool_hand.svg painted rgb(230,237,243).

        Both failures look identical from the outside -- no icon -- and neither
        raises. Hence a guard rather than a memory.
        """
        import re
        qml_dir = _GUI_DIR / "qml"
        sources = []
        for path in sorted(qml_dir.glob("*.qml")):
            for rel in re.findall(r'iconSource:\s*"([^"]+)"', path.read_text(encoding="utf-8")):
                sources.append((path.name, rel, (qml_dir / rel).resolve()))
        self.assertTrue(sources, "no iconSource found -- is this guard still pointed at the QML?")
        missing = [f"{q}: {rel}" for q, rel, full in sources if not full.exists()]
        self.assertEqual([], missing, f"iconSource paths that do not resolve: {missing}")
        black = [f"{q}: {rel}" for q, rel, full in sources
                 if "currentcolor" in full.read_text(encoding="utf-8").lower()]
        self.assertEqual([], black,
                         "Qt renders `currentColor` as black, which is invisible on the "
                         f"dark surfaces these sit on: {black}")

    def test_both_engines_honour_start_maximized(self):
        """One request key, two engines, one meaning.

        `guibridge_b` sends `start_maximized` and `gui_common` chooses show()
        vs showMaximized() on it. The QML engine used to call showMaximized()
        unconditionally, so it obeyed a flag it never read. That is invisible
        in the app -- which always sends true -- and surfaced only because the
        two preview launchers, which send neither, opened at different sizes.

        A request key that only one engine reads is a contract that has already
        come apart, so this checks the key is present on both sides.
        """
        classic = (_GUI_DIR.parent / "gui_common.py").read_text(encoding="utf-8")
        self.assertIn("start_maximized", classic,
                      "the classic engine no longer reads the key this guard is about")
        qml = (_GUI_DIR / "qml" / "UnifiedEditor.qml").read_text(encoding="utf-8")
        self.assertIn("start_maximized", qml,
                      "the QML engine ignores start_maximized, so it will maximise "
                      "even when the caller asked for a normal window")

    def test_classic_log_writer_is_wired(self):
        self.assertIsNotNone(Q._classic_write_log)

    def test_qml_palette_covers_every_col_key(self):
        """USER-12-2: every col("key") used by the QML must exist in the palette."""
        import re
        qml = "\n".join(p.read_text(encoding="utf-8")
                      for p in sorted((_GUI_DIR / "qml").glob("*.qml")))
        # colA() is the same lookup with an alpha, so it must be policed too --
        # a `col(`-only pattern let colA("typo", "#hex", a) through silently.
        keys = set(re.findall(r'\bcolA?\(\s*"([a-z_0-9]+)"', qml))
        self.assertTrue(keys)
        missing = sorted(k for k in keys if k not in Q._PALETTE)
        self.assertEqual(missing, [])

    def test_qml_col_literals_match_palette(self):
        """The inline fallback literal must not drift from the palette value."""
        import re
        qml = "\n".join(p.read_text(encoding="utf-8")
                      for p in sorted((_GUI_DIR / "qml").glob("*.qml")))
        drift = []
        for key, literal in re.findall(
                r'\bcolA?\(\s*"([a-z_0-9]+)",\s*"(#[0-9a-fA-F]{6})"', qml):
            want = Q._PALETTE.get(key)
            if want and want.lower() != literal.lower():
                drift.append((key, literal, want))
        self.assertEqual(drift, [])


class TimelineBandsDoNotOverlap(unittest.TestCase):
    """Four things used to be drawn in the same 28px at the top of the timeline.

    The ruler labels sat at y=9, the marker chips at y=1..20, the CTI clock at
    y=12..28 and the hover tooltip at y=2. A chip covered whatever timecode was
    behind it, and the CENTER chip was sliced in half by the clock -- the word
    read as "CFNTFR". The fix is three named bands the whole panel measures
    from; these tests pin that they stay ordered and stay used.
    """

    def setUp(self):
        self.src = (_GUI_DIR / "qml" / "TimelinePanel.qml").read_text(encoding="utf-8")

    def _band(self, name):
        m = re.search(r"readonly property int %s:\s*(\d+)" % name, self.src)
        self.assertIsNotNone(m, f"{name} is gone; the timeline bands are unnamed again")
        return int(m.group(1))

    def test_the_three_bands_are_declared_and_ordered(self):
        ruler, top, bot = self._band("rulerH"), self._band("chipTop"), self._band("chipBot")
        self.assertLessEqual(ruler, top, "the chips start before the ruler band ends")
        self.assertLess(top, bot, "the chip band has no height")

    def test_the_chips_are_drawn_below_the_ruler(self):
        # The chip geometry must be measured from CHIP_TOP. Hardcoded y=1 is
        # what put them in the ruler's band.
        self.assertIn("var top = CHIP_TOP", self.src,
                      "the chip no longer positions itself from the chip band")
        self.assertNotIn("ctx.moveTo(lx + r, 1)", self.src,
                         "the chip is back to a hardcoded y=1, i.e. in the ruler band")

    def test_the_content_starts_below_the_chips(self):
        # Cut ranges and the mark-in/out wash used to start at y=6, under the
        # chips. Every one of them now measures from CHIP_BOT.
        for fragment in ("ctx.fillRect(xi, CHIP_BOT",
                         "ctx.fillRect(cx0, CHIP_BOT",
                         "ctx.strokeRect(cx0, CHIP_BOT"):
            self.assertIn(fragment, self.src, f"{fragment!r} no longer clears the chip band")
        self.assertIn("Math.min(midY - CHIP_BOT", self.src,
                      "the waveform can climb back into the chip band")

    def test_the_ruler_label_carries_a_tick_and_dodges_its_neighbour(self):
        # A number with nothing under it does not say WHERE it is; the classic
        # draws a short tick below each label and drops a label that would
        # touch the previous one.
        # The COMPARISON, not just the variable: `if (false) continue` keeps
        # the name alive while dropping the suppression entirely.
        self.assertIn("if (lcx - lw / 2 < lastRight + 10) continue", self.src,
                      "ruler labels no longer suppress overlap")
        self.assertIn("ctx.lineTo(trx + 0.5, RULER_H - 1)", self.src,
                      "the ruler labels lost their tick marks")
        self.assertIn('ctx.textAlign = "center"', self.src)

    def test_the_centre_guide_is_one_element_drawn_behind_the_markers(self):
        # It was a chip PLUS a clock pill in an overlapping band. One chip now,
        # and it goes down before IN/OUT/SPLIT so a marker you can DRAG stays
        # readable when the two land close together.
        # `cgLbl` was the separate pill's Label id -- structural, unlike a
        # phrase that also appears in the comment explaining the history.
        self.assertNotIn("cgLbl", self.src, "the separate centre pill is back")
        centre = self.src.index('chip(cgx, "center_guide"')
        # IN/OUT are brackets now, not chips: they are the two edges of ONE
        # region, so they grip their own side of it instead of flying a label.
        first_marker = self.src.index('bracket(xi, "marker_in"')
        self.assertLess(centre, first_marker,
                        "the centre guide draws over the editable markers again")


class TheZoomToolSurvivesAClick(unittest.TestCase):
    """`dragged = true` on ANY mouse movement disarmed the zoom tool.

    A real click travels a pixel or two between press and release, so
    `win.tool === "zoom" && !dragged` was almost never true and clicking with
    the tool did nothing. The classic canvas measures distance instead
    (`abs(dx) + abs(dy) < 4` in its mouseReleaseEvent).
    """

    def setUp(self):
        self.src = (_GUI_DIR / "qml" / "PreviewPanel.qml").read_text(encoding="utf-8")

    def test_a_movement_threshold_guards_the_click(self):
        self.assertIn("clickSlop", self.src, "the zoom click has no movement threshold")
        m = re.search(r"readonly property int clickSlop:\s*(\d+)", self.src)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 2,
                                "a threshold below 2px cannot absorb ordinary hand jitter")

    def test_dragged_is_set_from_distance_not_from_any_movement(self):
        self.assertIn("Math.abs(m.x - mPressX) + Math.abs(m.y - mPressY) > clickSlop", self.src)
        self.assertNotIn("                dragged = true" + chr(10) +
                         "                if (activeHandle ===", self.src,
                         "dragged is unconditionally true on the first move again")

    def test_dragging_with_the_zoom_tool_zooms(self):
        # Before, a drag with this tool did nothing at all: only hand-panning
        # and handle-resizing had a drag path.
        self.assertIn("zooming = true", self.src, "the zoom tool has no drag path")
        self.assertIn("Math.pow(2, (mPressY - m.y) / 110.0)", self.src,
                      "the drag-zoom no longer matches the classic's response curve")
        self.assertIn("zooming = false", self.src, "the zoom drag is never released")


class ClickingAMarkerFlagGrabsIt(unittest.TestCase):
    """The IN/OUT flags have to be draggable by clicking the FLAG.

    `chip()` clamps its box back inside the panel, so the default IN at t=0 and
    OUT at the end draw tens of pixels away from their own stems. `pick()` used
    to measure only the stem, within 7px -- so aiming at the visible flag
    selected nothing, and IN/OUT could not be moved at all. These run the
    editor's own `pick()` in a real JS engine rather than asserting on source
    text, because the failure was arithmetic, not a missing line.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtCore import QCoreApplication
            from PySide6.QtQml import QJSEngine  # noqa: F401
        except Exception as exc:  # pragma: no cover - the tests job has no PySide6
            raise unittest.SkipTest(f"PySide6 QtQml required: {exc}")
        cls._app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
        src = (_GUI_DIR / "qml" / "TimelinePanel.qml").read_text(encoding="utf-8")
        start = src.index("            function pick(x, y) {")
        end = src.index("            onPressed:", start)
        cls._pick_js = src[start:end]

    def _engine(self, boxes, mark_in=0.0, mark_out=100.0):
        """`pick` against a stub panel: 1s == 1px, so times read as positions."""
        from PySide6.QtQml import QJSEngine
        eng = QJSEngine()
        prelude = (
            "var tl = { chipBoxes: %s, t2x: function (t) { return t; } };"
            "var markIn = %r; var markOut = %r;"
            "var separatorPoints = []; var cuts = [];"
            % (json.dumps(boxes), float(mark_in), float(mark_out))
        )
        res = eng.evaluate(prelude + chr(10) + self._pick_js)
        self.assertFalse(res.isError(), f"pick() failed to evaluate: {res.toString()}")
        return eng

    def _pick(self, eng, x, y):
        res = eng.evaluate("JSON.stringify(pick(%r, %r))" % (float(x), float(y)))
        self.assertFalse(res.isError(), res.toString())
        return json.loads(res.toString())

    def test_the_clamped_in_flag_is_hit_where_it_is_drawn(self):
        # IN sits at t=0 but its flag was pushed to x=1..35 by the clamp.
        box = {"kind": "in", "idx": -1, "lx": 1, "top": 23, "w": 34, "h": 17}
        got = self._pick(self._engine([box]), 20, 30)
        self.assertEqual("in", got["kind"],
                         "clicking the IN flag selects nothing, so it cannot be dragged")

    def test_the_stem_still_works_for_a_bare_line(self):
        # A split with no recorded box must still be grabbable by its stem.
        eng = self._engine([])
        eng.evaluate("separatorPoints = [50];")
        self.assertEqual("split", self._pick(eng, 52, 80)["kind"])

    def test_empty_track_area_is_a_seek_not_a_grab(self):
        box = {"kind": "in", "idx": -1, "lx": 1, "top": 23, "w": 34, "h": 17}
        got = self._pick(self._engine([box]), 400, 90)
        self.assertEqual("", got["kind"], "a click on empty track grabbed a marker")

    def test_a_click_below_the_flag_band_does_not_grab_it(self):
        # The box test must respect y, or the whole column under a flag becomes
        # a drag handle and dragging the waveform moves the marker instead.
        box = {"kind": "in", "idx": -1, "lx": 1, "top": 23, "w": 34, "h": 17}
        got = self._pick(self._engine([box], mark_in=500.0), 20, 120)
        self.assertEqual("", got["kind"])


if __name__ == "__main__":
    unittest.main()
