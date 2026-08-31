"""One colour token layer for every FFmWiz GUI surface.

gui_style.PALETTE is the canonical source. The Qt QSS and the classic editors
read it directly, the QML editor imports it, and the Tk editors read it through
guibridge._UIPalette. These tests are the ratchet that keeps a second palette
from growing back (USER-12-1/2/3/5).
"""
import ast
import re
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_GUI_DIR = _ROOT / "ffmwiz" / "gui"
for _p in (str(_ROOT), str(_GUI_DIR),
           str(_GUI_DIR / "classic"), str(_GUI_DIR / "modern")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gui_style  # noqa: E402

def _classic_or_shared(name: str) -> Path:
    """Where a GUI source file lives now.

    The two engines moved into gui/classic and gui/modern, but the palette,
    geometry and common helpers they SHARE stayed at gui/. A blanket "classic"
    prefix therefore looks in the wrong place for exactly those three.
    """
    inside = _GUI_DIR / "classic" / name
    return inside if inside.exists() else _GUI_DIR / name


# Every .qml of the modern engine, not just its entry file: the reusable
# controls were split into siblings, and a single-file read would have quietly
# stopped policing them.
_QML_DIR = _GUI_DIR / "modern" / "qml"
_QML = "\n".join(path.read_text(encoding="utf-8")
                 for path in sorted(_QML_DIR.glob("*.qml")))

# The classic unified editor spans several modules (the builder plus its
# extracted canvas/timeline widgets). Glob them so these token assertions follow
# the code instead of pinning it to one filename -- and so the "no hard-coded
# hex" half covers every file the editor draws from.
_CLASSIC_UNIFIED = "\n".join(
    path.read_text(encoding="utf-8")
    for path in sorted((_GUI_DIR / "classic").glob("gui_editor_unified*.py")))


class BrandAnchorTests(unittest.TestCase):
    """USER-12-5: the logo is neon cyan/blue/violet/magenta, and those four
    accents are pinned here.

    The FIELD they sit on is not pinned any more. `bg` was `#0a0f2e` (deep
    navy) to match the logo's background; the user asked for the near-black
    ground back, so `bg` and its panel/border/timeline family returned to the
    pre-8fc3bbc GitHub-dark values. Pinning it here would have made this test
    the thing that vetoes a user's own colour choice, which is not what a brand
    ratchet is for -- the accents are the brand, the field is a preference."""

    ANCHORS = {
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
        # Read off the LOADED class instead of a pinned file path. The class
        # moved to guibridge_tk_common (guibridge still re-exports it) and a
        # path pinned here would keep failing a test whose subject -- one
        # shared palette -- is untouched.
        import inspect
        from ffmwiz import guibridge
        body = inspect.getsource(guibridge._UIPalette)
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{6}", body), [])

    def test_qml_declares_no_fallback_palette_of_its_own(self):
        # Checked on the LOADED module rather than the source text. The text
        # form pinned `from gui_style import PALETTE`, which only ever resolved
        # because the GUI was launched as a script from its own folder; making
        # the editors addressable as `ffmwiz.gui.<name>` (D10) changed the
        # spelling and failed a test whose actual subject -- one shared palette
        # -- was untouched.
        from ffmwiz.gui.modern import ffmwiz_gui_qml
        from ffmwiz.gui import gui_style   # shared: it did not move
        self.assertIs(ffmwiz_gui_qml._PALETTE, gui_style.PALETTE)
        # The old hand-copied 26-key dict is gone.
        src = (_GUI_DIR / "modern" / "ffmwiz_gui_qml.py").read_text(encoding="utf-8")
        self.assertNotIn('"panel_alt": "#1a1f2a"', src)


class CrossEngineColourTests(unittest.TestCase):
    """USER-12-3: the same element must not be a different colour per engine."""

    def test_waveform_trace_is_one_token(self):
        classic = _CLASSIC_UNIFIED
        self.assertIn('QtGui.QColor(PALETTE["waveform"])', classic)
        self.assertNotIn("#3a8bff", classic)
        self.assertNotIn("0xFF3A8BFF", classic)
        self.assertIn('colA("waveform"', _QML)

    def test_selected_split_marker_is_one_token(self):
        classic = _CLASSIC_UNIFIED
        self.assertIn('PALETTE["split_marker_sel" if selected else "split_marker"]', classic)
        # The TOKEN must reach a col() lookup; the exact spelling is not the
        # contract. `col("split_marker_sel", ...)` and
        # `col(sel ? "split_marker_sel" : "split_marker", ...)` both satisfy it,
        # and pinning one of the two spellings makes this guard fail on a
        # refactor that kept the token -- the failure mode this file exists to
        # prevent is a HARDCODED colour, not a ternary.
        self.assertIn('"split_marker_sel"', _QML,
                      "the selected split marker must name the palette token")
        # QML used to draw the selected marker white while classic used #7dd3fc.
        self.assertNotIn('(k === win.selSplit) ? "#ffffff"', _QML)

    def test_qml_interaction_states_come_from_tokens(self):
        self.assertIn("property color hoverColor", _QML)
        self.assertIn('hoverColor: win.col("green_hover"', _QML)
        self.assertIn('pressedColor: win.col("danger_pressed"', _QML)


class TerminalPaletteTests(unittest.TestCase):
    """USER-12-4: the same NAME must not be a different COLOUR in the wizard's
    palette and the Stream Cleanup palette."""

    # A name shared by both palettes must carry the same colour in both, so
    # entering Stream Cleanup (menu 8) does not change the scheme mid-session.
    # This set is a ratchet: it is empty and must stay empty. C is a pinned
    # vendored copy of upstream, so any retint there also needs a LOCAL_EDITS
    # entry in tests/test_mux_cleanup_port.py.
    KNOWN_DIFFERENT: set[str] = set()

    def _classes(self):
        from ffmwiz.core.colors import Color
        from ffmwiz.muxcleanup.colors import C
        return Color, C

    def test_no_name_means_two_colours(self):
        Color, C = self._classes()
        shared = [n for n in dir(Color) if not n.startswith("_") and hasattr(C, n)]
        self.assertTrue(shared)
        differing = {n for n in shared if getattr(Color, n) != getattr(C, n)}
        self.assertEqual(differing, self.KNOWN_DIFFERENT)

    def test_language_colours_stay_distinguishable(self):
        from ffmwiz.muxcleanup.colors import LANGUAGE_COLORS
        self.assertEqual(len(set(LANGUAGE_COLORS)), len(LANGUAGE_COLORS))

    # Color.MUX_* existed only to re-declare a muxcleanup value on the wizard
    # side. Every one of them WITHOUT a caller is gone. These six STAY, and
    # the reason is measured, not assumed -- 'switch the call sites to import
    # C directly' was proposed and does not work:
    #
    # 1. Only three are the same string as the constant they supposedly
    #    mirror. The other three are `C.BOLD + <a DIFFERENTLY NAMED one>`:
    #      MUX_HEADER        == C.BOLD + C.LAUNCHER_PINK      (not C.HEADER;
    #                                                          there is none)
    #      MUX_SEPARATOR     == C.BOLD + C.ACTION_SEPARATOR
    #      MUX_SETTING_LABEL == C.BOLD + C.SETTING_LABEL
    #    so it is not a rename.
    #
    # 2. `muxcleanup/` sits ABOVE `support/` in the layer order, so
    #    `support/` cannot import it at module level -- ext00b.py:174 does
    #    its muxcleanup imports INSIDE the function for exactly that reason.
    #    Six of the nine references are default argument values, which are
    #    evaluated at import time, so a deferred import cannot supply them.
    #    Rewriting them as `= None` and resolving in the body would change
    #    public signatures for no behavioural gain, and L00_metadata.py:146
    #    sits in a dict of Color.* constants where one C.* entry reads worse.
    #
    # The bound may shrink if a call site genuinely disappears; it may never
    # grow.
    MUX_MIRRORS_LEFT = {
        "MUX_EMERALD", "MUX_LAVENDER", "MUX_HEADER", "MUX_SEPARATOR",
        "MUX_SETTING_LABEL", "MUX_SETTING_VALUE",
    }

    def test_the_mux_mirror_block_does_not_grow_back(self):
        Color, _ = self._classes()
        mirrors = {n for n in vars(Color) if n.startswith("MUX_")}
        self.assertLessEqual(mirrors, self.MUX_MIRRORS_LEFT,
                             "a Color.MUX_* mirror of muxcleanup.colors.C came back; "
                             "import C at the call site instead")

    def test_wizard_banner_wears_the_gui_title_colour(self):
        """USER-12-5: the terminal banner was hot pink, which is in neither the
        logo nor the GUI. ffmwiz/gui is a script directory, so core/colors.py
        cannot import PALETTE -- this assertion is the link that keeps the two
        from drifting apart again."""
        Color, _ = self._classes()
        red, green, blue = (int(gui_style.PALETTE["accent_text"][i:i + 2], 16)
                            for i in (1, 3, 5))
        self.assertEqual(
            Color.WIZARD_TITLE,
            chr(27) + "[38;2;{};{};{}m".format(red, green, blue),
            "the wizard banner drifted from the GUI title colour")

    def test_one_literal_per_colour_where_the_names_are_synonyms(self):
        # LIGHT_BLUE and BLUE are the same colour under two names; keeping two
        # literals meant a retint of one silently left the other behind.
        Color, _ = self._classes()
        src = (_ROOT / "ffmwiz" / "core" / "colors.py").read_text(encoding="utf-8")
        # Line-level so a failure reports the line, not the whole module.
        declaration = [ln.strip() for ln in src.splitlines()
                       if ln.strip().startswith("LIGHT_BLUE =")]
        self.assertEqual(1, len(declaration), declaration)
        self.assertTrue(declaration[0].startswith("LIGHT_BLUE = BLUE"),
                        f"LIGHT_BLUE re-declared its own literal: {declaration[0]}")
        self.assertEqual(Color.LIGHT_BLUE, Color.BLUE)


class NoSecondPaletteTests(unittest.TestCase):
    """USER-12: every ACTIVE surface must draw from gui_style.PALETTE.

    Measured on the real rendered window before this ratchet existed:
      * the classic timeline painted #0f1a26 / #111820 -- a slate-green family,
        while the toolbar and panels were navy #121a44, so that whole section
        visibly did not match the rest of the app;
      * the side control column is a QScrollArea whose viewport the QSS never
        styled, leaving 5124 pixels of #efefef light grey showing through the
        8px gaps between the docked panels (and across the entire column while
        those panels stream in after first paint).

    Both now resolve through tokens. This keeps a second palette from growing back.
    """

    # Modules a user actually looks at. The Tk bridges are an archived fallback
    # reached only when PySide6 is missing, so they are out of scope here.
    #
    # Globbed, not listed: the classic editor is ~20 modules now and a four-name
    # list policed four of them, so a hard-coded colour in any of the sixteen
    # others (the cut/crop/speed/audio editors, the extracted layout and input
    # halves) was invisible to this ratchet -- which is the whole failure mode
    # USER-12-3 exists to stop.
    ACTIVE = sorted(p.name for p in (_GUI_DIR / "classic").glob("*.py")) + ["gui_common.py"]

    # The zoom cursor is drawn over arbitrary video frames and must stay legible
    # against any content, so it is exempt BY DESIGN. See the THEME EXEMPTION
    # comment on the function itself.
    EXEMPT_FUNCTIONS = {"_make_zoom_cursor"}

    # A hex inside col()/colA()/PALETTE.get() is a fallback for a token that
    # already owns that colour, not a second palette.
    FALLBACK = re.compile(r'(?:col|colA|PALETTE\.get)\s*\([^()]*?"#[0-9a-fA-F]{6}"')
    HEX = re.compile(r'"#[0-9a-fA-F]{6}"')

    def _exempt_line_ranges(self, source):
        """Line spans of functions allowed to hard-code colours."""
        spans = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name in self.EXEMPT_FUNCTIONS:
                spans.append((node.lineno, node.end_lineno))
        return spans

    def _standalone_hexes(self, path):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        fallback = [m.span() for m in self.FALLBACK.finditer(text)]
        exempt = self._exempt_line_ranges(text)
        found = []
        for m in self.HEX.finditer(text):
            if any(a <= m.start() < b for a, b in fallback):
                continue
            line = text[:m.start()].count("\n") + 1
            if any(start <= line <= end for start, end in exempt):
                continue
            found.append((line, m.group(0), lines[line - 1].strip()[:60]))
        return found

    def test_active_classic_modules_declare_no_colours_of_their_own(self):
        # ACTIVE names files, and those files are under gui/classic/ now.
        for name in self.ACTIVE:
            with self.subTest(name):
                found = self._standalone_hexes(_classic_or_shared(name))
                self.assertEqual(
                    [], found,
                    f"{name} hard-codes colours instead of using PALETTE: "
                    + "; ".join(f"L{ln} {hx} -> {snip}" for ln, hx, snip in found))

    # Pure black/white are contrast-critical and theme-independent: the crop
    # scrim, the crop rectangle and its rule-of-thirds guides sit over arbitrary
    # video frames, so they are literals BY DESIGN.
    QML_LITERAL_ALLOWLIST = {"#000000", "#ffffff"}

    def test_every_qml_colour_is_a_token_fallback(self):
        """USER-12-3: a hex in the QML must be the fallback for the token that
        already owns that colour, so retinting gui_style reaches both engines.

        Covers the helper spellings a `col("k", "#hex")` pattern misses -- the
        timeline draws its IN/OUT/SPLIT/CENTER chips through
        `chip(x, key, fallback, ...)`, which forwards to win.col() but does not
        look like a col() call to a regex.
        """
        by_value: dict[str, list[str]] = {}
        for key, value in gui_style.PALETTE.items():
            by_value.setdefault(value.lower(), []).append(key)
        bad = []
        for path in sorted(_QML_DIR.glob("*.qml")):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r'"(#[0-9a-fA-F]{6})"', text):
                literal = match.group(1).lower()
                if literal in self.QML_LITERAL_ALLOWLIST:
                    continue
                line = text[:match.start()].count("\n") + 1
                owners = by_value.get(literal)
                if not owners:
                    bad.append(f"{path.name}:{line} {literal} is in no palette token")
                    continue
                # The token naming that colour must be right there, so the
                # literal is demonstrably a fallback and not a second palette.
                near = set(re.findall(r'"([a-z][a-z_0-9]+)"',
                                      text[max(0, match.start() - 160):match.start()]))
                if not near & set(owners):
                    bad.append(f"{path.name}:{line} {literal} names no token "
                               f"(expected one of {owners})")
        self.assertEqual([], bad, "; ".join(bad))

    def test_the_cursor_exemption_stays_documented(self):
        # If the exemption is ever silently widened, the reason must still be
        # written down at the place it applies.
        src = (_classic_or_shared("gui_editor_unified_canvas.py")).read_text(encoding="utf-8")
        self.assertIn("THEME EXEMPTION", src)
        for name in self.EXEMPT_FUNCTIONS:
            self.assertIn(f"def {name}", src)

    def test_the_timeline_is_fully_token_driven(self):
        # The regression that started this: the timeline was the one section
        # painting outside the palette entirely.
        self.assertEqual([], self._standalone_hexes(_classic_or_shared("gui_editor_unified_timeline.py")))

    def test_the_scroll_area_viewport_is_themed(self):
        # Without this rule the side column viewport falls back to Qt's default
        # light grey and shows as bars between the docked panels. Match the whole
        # rule, not just the word "QScrollArea" -- a renamed selector still
        # contains that substring while styling nothing.
        rule = re.search(
            r"(QScrollArea[^{}]*QAbstractScrollArea::viewport[^{}]*)\{([^{}]*)\}",
            gui_style.QSS)
        self.assertIsNotNone(
            rule, "QSS has no rule covering both QScrollArea and its viewport")
        self.assertIn(gui_style.PALETTE["bg"], rule.group(2),
                      "the scroll-area rule must paint a palette background")

    def test_the_new_tokens_exist_and_are_used(self):
        timeline = (_classic_or_shared("gui_editor_unified_timeline.py")).read_text(encoding="utf-8")
        for token in ("cut_bar", "cut_bar_dim", "center_guide", "center_guide_text"):
            with self.subTest(token):
                self.assertIn(token, gui_style.PALETTE)
                self.assertIn(f'PALETTE["{token}"]', timeline)


if __name__ == "__main__":
    unittest.main()
