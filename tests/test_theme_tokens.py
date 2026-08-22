"""One colour token layer for every FFmWiz GUI surface.

gui_style.PALETTE is the canonical source. The Qt QSS and the classic editors
read it directly, the QML editor imports it, and the Tk editors read it through
guibridge._UIPalette. These tests are the ratchet that keeps a second palette
from growing back (USER-12-1/2/3/5).
"""
import re
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_GUI_DIR = _ROOT / "ffmwiz" / "gui"
for _p in (str(_ROOT), str(_GUI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gui_style  # noqa: E402

_QML = (_GUI_DIR / "qml" / "UnifiedEditor.qml").read_text(encoding="utf-8")


class BrandAnchorTests(unittest.TestCase):
    """USER-12-5: the app used GitHub's dark palette; the logo is neon
    cyan/blue/violet/magenta on deep navy."""

    ANCHORS = {
        "bg": "#0a0f2e",        # deep navy field
        "accent": "#3b82f6",    # logo blue
        "waveform": "#22d3ee",  # logo cyan
        "purple": "#7c3aed",    # logo violet
        "chapter": "#c026d3",   # logo magenta
    }

    def test_brand_anchors_are_the_logo_colours(self):
        for key, value in self.ANCHORS.items():
            with self.subTest(key):
                self.assertEqual(gui_style.PALETTE[key], value)

    def test_state_colours_stay_off_the_brand_ramp(self):
        # A warning must never read as decoration.
        brand = set(self.ANCHORS.values())
        for key in ("danger", "warn", "cut_red", "green", "playhead"):
            with self.subTest(key):
                self.assertNotIn(gui_style.PALETTE[key], brand)

    def test_every_token_is_a_six_digit_hex(self):
        for key, value in gui_style.PALETTE.items():
            with self.subTest(key):
                self.assertRegex(value, r"^#[0-9a-f]{6}$")


class SinglePaletteTests(unittest.TestCase):
    def test_tk_palette_is_a_view_over_the_canonical_tokens(self):
        from ffmwiz import guibridge
        palette = gui_style.PALETTE
        pairs = {
            "BG": "bg", "PANEL": "panel", "PANEL_HI": "panel_alt",
            "SURFACE": "surface", "SURFACE_HOVER": "surface_hover",
            "SURFACE_PRESSED": "surface_pressed", "SURFACE_DIS": "surface_disabled",
            "BORDER": "border", "BORDER_SOFT": "border_soft",
            "TIMELINE_BG": "timeline_bg", "TIMELINE_TRACK": "timeline_track",
            "TIMELINE_TICK": "tick_lo", "TIMELINE_TICK_HI": "tick_hi",
            "ACCENT": "accent", "ACCENT_STRONG": "accent_hover",
            "ACCENT_DARK": "accent_dim", "TEXT": "text", "TEXT_DIM": "text_dim",
            "TEXT_MUTE": "text_mute", "PLAYHEAD": "playhead",
        }
        for attr, key in pairs.items():
            with self.subTest(attr):
                self.assertEqual(getattr(guibridge._UIPalette, attr), palette[key])

    def test_tk_palette_declares_no_colours_of_its_own(self):
        src = (_ROOT / "ffmwiz" / "guibridge.py").read_text(encoding="utf-8")
        body = src[src.index("class _UIPalette:"):src.index("def _apply_app_ttk_theme")]
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{6}", body), [])

    def test_qml_declares_no_fallback_palette_of_its_own(self):
        src = (_GUI_DIR / "ffmwiz_gui_qml.py").read_text(encoding="utf-8")
        self.assertIn("from gui_style import PALETTE", src)
        # The old hand-copied 26-key dict is gone.
        self.assertNotIn('"panel_alt": "#1a1f2a"', src)


class CrossEngineColourTests(unittest.TestCase):
    """USER-12-3: the same element must not be a different colour per engine."""

    def test_waveform_trace_is_one_token(self):
        classic = (_GUI_DIR / "gui_editor_unified.py").read_text(encoding="utf-8")
        self.assertIn('QtGui.QColor(PALETTE["waveform"])', classic)
        self.assertNotIn("#3a8bff", classic)
        self.assertNotIn("0xFF3A8BFF", classic)
        self.assertIn('colA("waveform"', _QML)

    def test_selected_split_marker_is_one_token(self):
        classic = (_GUI_DIR / "gui_editor_unified.py").read_text(encoding="utf-8")
        self.assertIn('PALETTE["split_marker_sel" if selected else "split_marker"]', classic)
        self.assertIn('col("split_marker_sel"', _QML)
        # QML used to draw the selected marker white while classic used #7dd3fc.
        self.assertNotIn('(k === win.selSplit) ? "#ffffff"', _QML)

    def test_qml_interaction_states_come_from_tokens(self):
        self.assertIn("property color hoverColor", _QML)
        self.assertIn('hoverColor: win.col("green_hover"', _QML)
        self.assertIn('pressedColor: win.col("danger_pressed"', _QML)


class TerminalPaletteTests(unittest.TestCase):
    """USER-12-4: the same NAME must not be a different COLOUR in the wizard's
    palette and the Stream Cleanup palette."""

    # Every name below is a real collision: the SAME name is a DIFFERENT colour
    # in the two palettes. This set is a ratchet, not an approval - it must only
    # ever shrink. It cannot shrink from this lane because:
    #   * ffmwiz/core/colors.py holds the other half of every pair, and
    #   * ffmwiz/muxcleanup/ is a pinned verbatim vendored copy of upstream
    #     (tests/test_mux_cleanup_port.py), so retinting C needs a LOCAL_EDITS
    #     entry there.
    # Extra constraint for whoever does: Color.BLUE is 38;5;117, which C already
    # uses for SKY, so aligning BLUE alone makes two entries of LANGUAGE_COLORS
    # identical and two languages indistinguishable.
    KNOWN_DIFFERENT = {"AQUA", "BLUE", "CYAN", "GRAY", "LIME", "MAGENTA",
                       "ORANGE", "PINK", "PROGRESS_ELAPSED", "PROGRESS_ETA_LABEL",
                       "PROGRESS_ETA_VALUE", "PROGRESS_PERCENT", "PROGRESS_SIZE"}

    def _classes(self):
        from ffmwiz.core.colors import Color
        from ffmwiz.muxcleanup.colors import C
        return Color, C

    def test_no_new_name_collisions_appear(self):
        Color, C = self._classes()
        shared = [n for n in dir(Color) if not n.startswith("_") and hasattr(C, n)]
        self.assertTrue(shared)
        differing = {n for n in shared if getattr(Color, n) != getattr(C, n)}
        self.assertEqual(differing, self.KNOWN_DIFFERENT)

    def test_language_colours_stay_distinguishable(self):
        from ffmwiz.muxcleanup.colors import LANGUAGE_COLORS
        self.assertEqual(len(set(LANGUAGE_COLORS)), len(LANGUAGE_COLORS))


if __name__ == "__main__":
    unittest.main()
