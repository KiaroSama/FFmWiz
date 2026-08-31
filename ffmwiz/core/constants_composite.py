"""FFmWiz multi-input compositing constants (overlay, PiP, stacks, audio mix).

Pure literal data split out of ffmwiz/core/constants.py to keep that module
under 800 lines. Leaf module: no imports."""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Multi-input compositing (overlay / picture-in-picture / stacks / audio mix).
# ---------------------------------------------------------------------------

# Corner -> (x, y) expressions for `overlay`, with `{m}` for the margin.
# W/H are the MAIN input's dimensions and w/h the overlay's, so the same four
# strings place any size of logo against any size of picture -- which is why
# they are expressions rather than numbers computed from the probe.
OVERLAY_CORNERS: dict[str, tuple[str, str]] = {
    "tl": ("{m}", "{m}"),
    "tr": ("W-w-{m}", "{m}"),
    "bl": ("{m}", "H-h-{m}"),
    "br": ("W-w-{m}", "H-h-{m}"),
    "center": ("(W-w)/2", "(H-h)/2"),
}

# Picture modes, and how each one combines the second input with the first.
COMPOSITE_PICTURE_MODES: dict[str, str] = {
    "overlay": "a second input placed at a corner, at its own size",
    "pip": "a second video scaled down and placed at a corner",
    "hstack": "the two inputs side by side",
    "vstack": "the two inputs one above the other",
}

# Defaults for the options the corner modes take. A margin in pixels rather
# than a fraction: a logo 10 px from the edge looks the same on 720p and 4K,
# a logo 2% in does not.
COMPOSITE_DEFAULT_CORNER = "br"
COMPOSITE_DEFAULT_MARGIN = 10
COMPOSITE_DEFAULT_PIP_SCALE = 0.25
# The SECOND source's level in the mix. The first keeps its own (normalize=0),
# so this reads as "the music plays at 30% of its own volume under the voice".
COMPOSITE_DEFAULT_MIX_WEIGHT = 0.3

# Every key the composite question may write. Listed once so a re-ask can
# clear the previous attempt instead of leaving half of it behind.
COMPOSITE_ANSWER_KEYS = (
    "composite_mode", "composite_corner", "composite_margin",
    "composite_opacity", "composite_scale", "composite_path",
    "composite_item", "composite_audio_mix", "composite_audio_weight",
    "composite_audio_path", "composite_audio_item",
)
