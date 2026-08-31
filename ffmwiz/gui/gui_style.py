"""FFmWiz GUI palette + QSS stylesheets, split from gui_common for file size.

PALETTE below is the CANONICAL colour token layer for every FFmWiz surface:
the Qt QSS and the classic editors read it directly, the QML editor imports it
(ffmwiz/gui/ffmwiz_gui_qml.py) and the Tk editors read it through
guibridge._UIPalette. Nothing else may declare its own hex values — change a
colour here and every GUI section moves together.

Values are the app-logo brand ramp measured from
ffmwiz/assets/icons/ffmwiz_app.png: neon cyan / blue / violet / magenta on a
deep navy field. State colours (ok / warn / danger / cut / playhead) stay
OUTSIDE that ramp on purpose so a warning never reads as decoration.

NOTE: the terminal palettes (ffmwiz/core/colors.Color and
ffmwiz/muxcleanup/colors.C) still carry their own ANSI values; they cannot
import this module because ffmwiz/gui is a script directory, not a package.
Moving these tokens to a Qt-free ffmwiz/core/tokens.py is what would finally
let the terminal derive from them too.
"""
from __future__ import annotations

from pathlib import Path

# Icon asset dir (the QSS f-string interpolates ASSETS_DIR for image: url(...)).
# This module lives in <project>/ffmwiz/gui/, so parents[1] is the package dir.
ASSETS_ROOT = Path(__file__).resolve().parents[1] / "assets"
ASSETS_DIR = ASSETS_ROOT / "icons"


PALETTE: dict[str, str] = {
    # --- surfaces: the deep-navy field from the logo, lightest last ---
    "bg":              "#0d1117",
    "panel":           "#161b22",
    "panel_alt":       "#1a1f2a",
    "surface":         "#21262d",
    "surface_hover":   "#2e353d",
    "surface_pressed": "#1c2128",
    "surface_disabled":"#161b22",
    "border":          "#30363d",
    "border_strong":   "#3a4150",
    "border_soft":     "#21262d",
    "timeline_bg":     "#0a0d12",
    "timeline_track":  "#1c2128",
    "tick_hi":         "#e6edf3",
    "tick_lo":         "#7d8590",
    # --- brand accents: logo blue / violet / magenta / cyan ---
    "accent":          "#3b82f6",
    "accent_hover":    "#5b9bff",
    "accent_pressed":  "#2563eb",
    "accent_dim":      "#1b3468",
    "accent_text":     "#7db3ff",
    "purple":          "#7c3aed",
    "purple_hover":    "#8b5cf6",
    "purple_pressed":  "#6d28d9",
    "chapter":         "#c026d3",
    "chapter_text":    "#e9a8f2",
    "waveform":        "#22d3ee",
    "split_marker":    "#38bdf8",
    "split_marker_sel":"#7dd3fc",
    "segment_boundary":"#e8b278",
    # --- state: deliberately off the brand ramp ---
    "green":           "#238636",
    "green_hover":     "#2ea043",
    "green_pressed":   "#196c2e",
    "green_text":      "#56d364",
    "danger":          "#a40e26",
    "danger_hover":    "#c9303f",
    "danger_pressed":  "#7d0a1c",
    "danger_text":     "#ff7b72",
    "danger_cut":      "#7f123f",
    "danger_cut_hover":"#a51b55",
    "danger_cut_pressed":"#5e0d2e",
    "danger_alt":      "#643618",
    "danger_alt_hover":"#8a4a1f",
    "danger_alt_pressed":"#4d2812",
    "danger_alt_text": "#f0a96b",
    "warn":            "#d29922",
    "marker_in":       "#2ddc7f",
    "marker_out":      "#d29922",
    # Interaction states for the two mark buttons. The QML editor colours
    # them like the classic one, and every col() key it uses has to exist
    # here (test_qml_palette_covers_every_col_key).
    "marker_in_hover":    "#4ee89a",
    "marker_in_pressed":  "#1fa860",
    "marker_out_hover":   "#e8b13c",
    "marker_out_pressed": "#a8760f",
    "cut_red":         "#f85149",
    "cut_red_dim":     "#a92927",
    # Cut chip on the timeline bar: a very dark maroon so the white "Cut #n"
    # label stays readable. Nothing else in the palette is this dark, so these
    # keep their original values instead of being remapped onto a brighter red.
    "cut_bar":         "#4b1118",
    "cut_bar_dim":     "#301117",
    # Centre guide: deliberately violet so it is never mistaken for the red
    # playhead. Tokenised at its original value so both engines can share it.
    "center_guide":    "#c084fc",
    "center_guide_text": "#e9d5ff",
    "playhead":        "#ff4d55",
    "playhead_halo":   "#3b82f6",
    # --- text ---
    "text":            "#e8edfb",
    "text_dim":        "#c3cbea",
    "text_mute":       "#8891b4",
    "text_subtle":     "#4e5680",
    "text_on_accent":  "#ffffff",
}

QSS = f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; }}

QMainWindow, QWidget#central {{
    background-color: {PALETTE['bg']};
    color: {PALETTE['text']};
}}

/* The side control column is a QScrollArea. Its viewport is NOT covered by the
   QMainWindow rule above, so without this it paints with Qt's default light
   palette -- visible as light-grey bars in the 8px gaps between the docked
   panels, and across the whole column while the panels are still streaming in
   after first paint (USER-12). */
QScrollArea, QScrollArea > QWidget > QWidget, QAbstractScrollArea::viewport {{
    background-color: {PALETTE['bg']};
    border: none;
}}

QLabel {{ color: {PALETTE['text']}; background: transparent; }}
QLabel#title {{
    color: {PALETTE['accent_text']};
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.2px;
}}
QLabel#headerInfo {{ color: {PALETTE['text_mute']}; font-size: 11px; }}
QLabel#dim       {{ color: {PALETTE['text_mute']}; font-size: 11px; }}
QLabel#controlLabel {{ color: {PALETTE['text']}; font-size: 12px; font-weight: 700; }}
QLabel#timelineControlLabel {{ color: {PALETTE['text']}; font-size: 12px; font-weight: 700; }}
QLabel#sectionLabel {{ color: {PALETTE['accent_text']}; font-size: 12px; font-weight: 700; }}
QLabel#tip       {{ color: {PALETTE['text_dim']}; font-size: 11px; font-style: normal; padding: 5px 8px; background-color: {PALETTE['panel_alt']}; border: 1px solid {PALETTE['border_soft']}; border-radius: 6px; }}
QLabel#status    {{ color: {PALETTE['text']}; font-size: 12px; }}

QFrame#header, QFrame#panel, QFrame#statusBand {{
    background-color: {PALETTE['panel']};
    border: 1px solid {PALETTE['border']};
    border-radius: 8px;
}}

QPushButton {{
    background-color: {PALETTE['surface']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 11px;
    min-height: 22px;
}}
QPushButton:hover {{
    background-color: {PALETTE['surface_hover']};
    border-color: {PALETTE['border_strong']};
}}
QPushButton:pressed {{ background-color: {PALETTE['surface_pressed']}; }}
QPushButton:disabled {{
    background-color: {PALETTE['surface_disabled']};
    color: {PALETTE['text_subtle']};
    border-color: {PALETTE['border_soft']};
}}

QPushButton#primary {{
    background-color: {PALETTE['accent']};
    color: {PALETTE['text_on_accent']};
    border-color: {PALETTE['accent']};
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background-color: {PALETTE['accent_hover']};
    border-color: {PALETTE['accent_hover']};
}}
QPushButton#primary:pressed {{ background-color: {PALETTE['accent_pressed']}; }}
QPushButton#primary:disabled {{
    background-color: {PALETTE['surface_disabled']};
    color: {PALETTE['text_subtle']};
    border-color: {PALETTE['border_soft']};
}}

QPushButton#green {{
    background-color: {PALETTE['green']};
    color: {PALETTE['text_on_accent']};
    border-color: {PALETTE['green']};
    font-weight: 600;
}}
QPushButton#green:hover {{
    background-color: {PALETTE['green_hover']};
    border-color: {PALETTE['green_hover']};
}}
QPushButton#green:pressed {{ background-color: {PALETTE['green_pressed']}; }}
QPushButton#green:disabled {{
    background-color: #1b2f22;
    color: {PALETTE['text_mute']};
    border-color: #285b35;
    font-weight: 500;
}}

QPushButton#purple {{
    background-color: {PALETTE['purple']};
    color: {PALETTE['text_on_accent']};
    border-color: {PALETTE['purple_hover']};
    font-weight: 600;
}}
QPushButton#purple:hover {{
    background-color: {PALETTE['purple_hover']};
    border-color: {PALETTE['purple_hover']};
}}
QPushButton#purple:pressed {{ background-color: {PALETTE['purple_pressed']}; }}
QPushButton#purple:disabled {{
    background-color: #2d2440;
    color: {PALETTE['text_mute']};
    border-color: #46345f;
    font-weight: 500;
}}
QPushButton#purple:disabled:hover {{
    background-color: #2d2440;
    border-color: #46345f;
}}

QPushButton#danger {{
    background-color: {PALETTE['danger']};
    color: {PALETTE['text_on_accent']};
    border-color: {PALETTE['danger_hover']};
    font-weight: 600;
}}
QPushButton#danger:hover {{
    background-color: {PALETTE['danger_hover']};
    border-color: {PALETTE['danger_hover']};
}}
QPushButton#danger:pressed {{ background-color: {PALETTE['danger_pressed']}; }}
QPushButton#danger:disabled {{
    background-color: #701020;
    color: #f0a3aa;
    border-color: #8c2638;
    font-weight: 600;
}}
QPushButton#danger:disabled:hover {{
    background-color: #701020;
    color: #f0a3aa;
    border-color: #8c2638;
}}

QPushButton#dangerCut {{
    background-color: {PALETTE['danger_cut']};
    color: {PALETTE['text_on_accent']};
    border-color: {PALETTE['danger_cut_hover']};
    font-weight: 600;
}}
QPushButton#dangerCut:hover {{
    background-color: {PALETTE['danger_cut_hover']};
    border-color: {PALETTE['danger_cut_hover']};
}}
QPushButton#dangerCut:pressed {{ background-color: {PALETTE['danger_cut_pressed']}; }}
QPushButton#dangerCut:disabled {{
    background-color: #4d1730;
    color: #e6a4bf;
    border-color: #783154;
    font-weight: 600;
}}
QPushButton#dangerCut:disabled:hover {{
    background-color: #4d1730;
    color: #e6a4bf;
    border-color: #783154;
}}

QPushButton#dangerAlt {{
    background-color: {PALETTE['danger_alt']};
    color: {PALETTE['text_on_accent']};
    border-color: {PALETTE['danger_alt_hover']};
    font-weight: 600;
}}
QPushButton#dangerAlt:hover {{
    background-color: {PALETTE['danger_alt_hover']};
    border-color: {PALETTE['danger_alt_hover']};
}}
QPushButton#dangerAlt:pressed {{ background-color: {PALETTE['danger_alt_pressed']}; }}
QPushButton#dangerAlt:disabled {{
    background-color: #523016;
    color: #e5b27d;
    border-color: #78451f;
    font-weight: 600;
}}
QPushButton#dangerAlt:disabled:hover {{
    background-color: #523016;
    color: #e5b27d;
    border-color: #78451f;
}}

QPushButton#tool {{ padding: 6px 10px; }}
QPushButton#tool[active="true"] {{
    background-color: {PALETTE['accent_dim']};
    color: {PALETTE['accent_text']};
    border-color: {PALETTE['accent']};
    font-weight: 600;
}}

QPushButton#overlayToggle {{
    background-color: #765407;
    color: {PALETTE['text_on_accent']};
    border-color: #9b7417;
    border-radius: 7px;
    font-weight: 500;
}}

QPushButton#separator {{
    background-color: #075985;
    color: {PALETTE['text_on_accent']};
    border-color: #0ea5e9;
    font-weight: 700;
}}
QPushButton#separator:hover {{
    background-color: #0369a1;
    border-color: #38bdf8;
}}
QPushButton#separator:pressed {{
    background-color: #064e78;
}}
QPushButton#convertMarker {{
    background-color: #243449;
    color: #e8f2ff;
    border-color: #5aa9ff;
    font-weight: 700;
}}
QPushButton#convertMarker:hover {{
    background-color: #2f4561;
    border-color: #8cc7ff;
}}
QPushButton#convertMarker:disabled {{
    background-color: #182331;
    color: #7790aa;
    border-color: #30445d;
}}
QPushButton#overlayToggle:hover {{
    background-color: #86610b;
    border-color: #b18420;
}}
QPushButton#overlayToggle:pressed {{
    background-color: #5c4104;
}}

QPushButton#markIn {{
    background-color: #123a29;
    color: #d7ffe8;
    border-color: {PALETTE['marker_in']};
    font-weight: 600;
}}
QPushButton#markIn:hover {{
    background-color: #185236;
    border-color: #5ef0a5;
}}
QPushButton#markOut {{
    background-color: #3b2b12;
    color: #ffe7b8;
    border-color: {PALETTE['marker_out']};
    font-weight: 600;
}}
QPushButton#markOut:hover {{
    background-color: #563c16;
    border-color: #e7b94f;
}}

QPushButton#iconOnly {{
    padding: 5px 6px;
    min-width: 26px;
}}
QPushButton#timelineViewArrow {{
    background-color: transparent;
    color: {PALETTE['accent_hover']};
    border: none;
    border-radius: 8px;
    padding: 0;
    min-width: 14px;
    max-width: 14px;
    min-height: 14px;
    max-height: 14px;
    font-size: 9px;
    font-weight: 700;
}}
QPushButton#timelineViewArrow:hover {{
    background-color: #182943;
    color: {PALETTE['accent_text']};
}}
QPushButton#timelineViewArrow:pressed {{
    background-color: #1f3a66;
}}
QPushButton#timelineViewArrow:disabled {{
    background-color: #111820;
    color: #23466f;
    border-color: {PALETTE['border_soft']};
}}

QPushButton#timelineZoomArrow {{
    background-color: transparent;
    color: {PALETTE['accent_hover']};
    border: none;
    border-radius: 8px;
    padding: 0;
    min-width: 14px;
    max-width: 14px;
    min-height: 14px;
    max-height: 14px;
    font-size: 9px;
    font-weight: 700;
}}
QPushButton#timelineZoomArrow:hover {{
    background-color: #182943;
    color: {PALETTE['accent_text']};
}}
QPushButton#timelineZoomArrow:pressed {{
    background-color: #1f3a66;
}}
QPushButton#timelineZoomArrow:disabled {{
    background-color: #111820;
    color: #23466f;
    border-color: {PALETTE['border_soft']};
}}

QSlider::groove:horizontal {{
    background: {PALETTE['timeline_track']};
    height: 5px;
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{ background: {PALETTE['accent']}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: {PALETTE['accent_hover']};
    width: 40px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
    border: 1px solid {PALETTE['accent']};
}}
QSlider::handle:horizontal:hover {{ background: {PALETTE['text']}; }}

QSlider#timelineZoomSlider::groove:horizontal {{
    background: {PALETTE['timeline_track']};
    height: 5px;
    border-radius: 3px;
}}
QSlider#timelineZoomSlider::sub-page:horizontal {{
    background: {PALETTE['accent']};
    border-radius: 3px;
}}
QSlider#timelineZoomSlider::handle:horizontal {{
    background: {PALETTE['accent_hover']};
    width: 44px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
    border: 1px solid {PALETTE['accent']};
}}
QSlider#timelineZoomSlider::handle:horizontal:hover {{ background: {PALETTE['text']}; }}

QFrame#timelineViewFrame {{
    background: #101820;
    border: 1px solid #667585;
    border-radius: 10px;
}}

QFrame#markerPanel {{
    background-color: {PALETTE['panel']};
    border: 1px solid #506071;
    border-radius: 8px;
}}

QFrame#inlineControlFrame {{
    background: #101820;
    border: 1px solid #667585;
    border-radius: 10px;
}}

QSlider#timelineViewSlider::groove:horizontal {{
    background: {PALETTE['timeline_track']};
    height: 5px;
    border-radius: 3px;
}}
QSlider#timelineViewSlider::sub-page:horizontal {{
    background: {PALETTE['timeline_track']};
    border-radius: 3px;
}}
QSlider#timelineViewSlider::handle:horizontal {{
    background: #8cff9d;
    width: 54px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
    border: 1px solid #2ea043;
}}
QSlider#timelineViewSlider::handle:horizontal:hover {{ background: #c7ffd0; }}

QSlider#cutVolumeSlider::groove:horizontal {{
    background: {PALETTE['timeline_track']};
    height: 6px;
    border-radius: 3px;
}}
QSlider#cutVolumeSlider::sub-page:horizontal {{
    background: {PALETTE['accent']};
    border-radius: 3px;
}}
QSlider#cutVolumeSlider::handle:horizontal {{
    background: {PALETTE['accent_hover']};
    width: 28px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
    border: 1px solid {PALETTE['accent']};
}}
QSlider#cutVolumeSlider::handle:horizontal:hover {{ background: {PALETTE['text']}; }}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {PALETTE['surface']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #0078d4;
    selection-color: #ffffff;
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    background-color: {PALETTE['surface_hover']};
    border-color: {PALETTE['border_strong']};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background-color: {PALETTE['surface']};
    border-left: 1px solid {PALETTE['border_soft']};
    width: 16px;
}}
QSpinBox#cropNumberField::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 15px;
    background-color: {PALETTE['surface']};
    border-left: 1px solid {PALETTE['border_soft']};
    border-top-right-radius: 6px;
}}
QSpinBox#cropNumberField::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 15px;
    background-color: {PALETTE['surface']};
    border-left: 1px solid {PALETTE['border_soft']};
    border-bottom-right-radius: 6px;
}}
QSpinBox#cropNumberField::up-button:hover, QSpinBox#cropNumberField::down-button:hover {{
    background-color: {PALETTE['surface_hover']};
}}
QSpinBox#cropNumberField::up-arrow {{
    image: url("{(ASSETS_DIR / 'spin_up.svg').as_posix()}");
    width: 9px; height: 9px;
}}
QSpinBox#cropNumberField::down-arrow {{
    image: url("{(ASSETS_DIR / 'spin_down.svg').as_posix()}");
    width: 9px; height: 9px;
}}
QCheckBox {{
    color: {PALETTE['text']};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid {PALETTE['border_strong']};
    background-color: {PALETTE['surface']};
}}
QCheckBox::indicator:hover {{
    border-color: {PALETTE['accent_hover']};
}}
QCheckBox::indicator:checked {{
    background-color: {PALETTE['accent']};
    border-color: {PALETTE['accent_hover']};
    image: url("{(ASSETS_DIR / 'check.svg').as_posix()}");
}}
QComboBox::drop-down {{
    width: 22px;
    border-left: 1px solid {PALETTE['border_soft']};
}}
QComboBox::down-arrow {{
    image: url("{(ASSETS_DIR / 'combo_down.svg').as_posix()}");
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background-color: {PALETTE['panel']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    selection-background-color: {PALETTE['accent_dim']};
    selection-color: {PALETTE['accent_text']};
    outline: 0;
}}
QComboBox QAbstractItemView QScrollBar:vertical {{
    background-color: {PALETTE['timeline_bg']};
    width: 10px;
    margin: 0;
    border: none;
}}
QComboBox QAbstractItemView QScrollBar::handle:vertical {{
    background-color: {PALETTE['accent']};
    min-height: 24px;
    border-radius: 5px;
}}
QComboBox QAbstractItemView QScrollBar::handle:vertical:hover {{
    background-color: {PALETTE['accent_hover']};
}}
QComboBox QAbstractItemView QScrollBar::add-line:vertical,
QComboBox QAbstractItemView QScrollBar::sub-line:vertical {{
    height: 0;
    background: transparent;
    border: none;
}}
QComboBox QAbstractItemView QScrollBar::add-page:vertical,
QComboBox QAbstractItemView QScrollBar::sub-page:vertical {{
    background-color: {PALETTE['timeline_bg']};
}}

QSplitter::handle {{
    background-color: {PALETTE['border_soft']};
    border-radius: 3px;
}}
QSplitter::handle:vertical {{
    height: 7px;
    margin: 1px 28px;
}}
QSplitter::handle:hover {{
    background-color: {PALETTE['accent_dim']};
}}

QFrame#timelineResizeGrip {{
    background-color: #16212c;
    border: 1px solid #506071;
    border-radius: 4px;
}}
QFrame#timelineResizeGrip:hover {{
    background-color: #1e3144;
    border-color: {PALETTE['accent_hover']};
}}

QFrame#zoomPercentBox {{
    background-color: {PALETTE['surface']};
    border: 1px solid {PALETTE['border']};
    border-radius: 6px;
    max-height: 34px;
}}
QFrame#zoomPercentBox:hover {{
    background-color: {PALETTE['surface_hover']};
    border-color: {PALETTE['border_strong']};
}}
QComboBox#zoomPercentCombo {{
    background-color: transparent;
    border: none;
    padding: 4px 22px 4px 8px;
    min-height: 20px;
    max-height: 30px;
    selection-background-color: #0078d4;
    selection-color: #ffffff;
}}
QComboBox#zoomPercentCombo:hover {{
    background-color: transparent;
    border: none;
}}
QComboBox#zoomPercentCombo::drop-down {{
    width: 22px;
    border-left: 1px solid {PALETTE['border_soft']};
}}
QComboBox#speedValueCombo {{
    background-color: {PALETTE['surface']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border_strong']};
    border-radius: 6px;
    padding: 5px 24px 5px 8px;
    min-height: 22px;
    selection-background-color: #0078d4;
    selection-color: #ffffff;
}}
QComboBox#speedValueCombo:hover {{
    background-color: {PALETTE['surface_hover']};
    border-color: {PALETTE['accent_hover']};
}}
QComboBox#speedValueCombo::drop-down {{
    width: 22px;
    border-left: 1px solid {PALETTE['border_soft']};
}}

QListWidget {{
    background-color: {PALETTE['timeline_bg']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    border-radius: 6px;
    outline: none;
    padding: 4px;
    font-family: "Consolas", "Cascadia Code", monospace;
    font-size: 11px;
}}
QListWidget::item {{ padding: 6px 8px; border-radius: 4px; }}
QListWidget::item:selected {{
    background-color: {PALETTE['accent_dim']};
    color: {PALETTE['accent_text']};
}}
QListWidget::item:hover {{ background-color: {PALETTE['surface']}; }}

QToolTip {{
    background-color: {PALETTE['panel']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    padding: 6px 8px;
    border-radius: 4px;
    font-size: 11px;
}}

QMessageBox {{ background-color: {PALETTE['panel']}; color: {PALETTE['text']}; }}
QMessageBox QLabel {{ color: {PALETTE['text']}; }}
QMessageBox QPushButton {{ min-width: 80px; }}

QMenu {{
    background-color: {PALETTE['panel']};
    color: {PALETTE['text']};
    border: 1px solid {PALETTE['border']};
    padding: 4px;
}}
QMenu::item {{ padding: 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {PALETTE['accent_dim']}; color: {PALETTE['accent_text']}; }}
QMenu::separator {{ height: 1px; background: {PALETTE['border']}; margin: 4px 6px; }}

QScrollBar:vertical, QScrollBar:horizontal {{ background: {PALETTE['panel']}; border: none; }}
QScrollBar::handle {{
    background: {PALETTE['surface']};
    border: 1px solid {PALETTE['border']};
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::handle:hover {{ background: {PALETTE['surface_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ background: transparent; border: none; }}
"""


# =====================================================================
# PREVIEW BUILD ONLY: a compact stylesheet override appended on top of QSS.
# It shrinks button/combo/spin heights, paddings and fonts in the top control
# panels so the toolbars take far less vertical room and the video preview
# (inside the splitter) keeps the freed space. Production QSS is untouched.
# =====================================================================
PREVIEW_COMPACT_QSS = """
QPushButton { padding: 4px 9px; min-height: 18px; }
QLabel#tip { padding: 4px 7px; }
"""
