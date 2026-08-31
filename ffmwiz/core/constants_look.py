"""FFmWiz look-filter tables (rotate/flip, colour, denoise, sharpen, blur).

Pure literal data split out of ffmwiz/core/constants.py to keep that module
under 800 lines. Leaf module: no imports. LOOK_ANSWER_KEYS stays here with
ADJUST_RANGES because it splices that dict's keys in."""
from __future__ import annotations


# --- Look filters (rotate/flip, colour, denoise, sharpen/blur, fade) --------
# Added after a review of the ffmpeg-webCLI tool, which offers these as one-click
# operations. They all fit the existing CPU filter chain, so they are wizard
# questions rather than a separate mode: one composed command, the way the rest
# of FFmWiz already works.

# `transpose` values, plus the flips. 180 is two 90s rather than hflip,vflip so
# a source with non-square pixels keeps its geometry.
ROTATE_FILTERS: dict[str, str] = {
    "90cw": "transpose=1",
    "90ccw": "transpose=2",
    "180": "transpose=1,transpose=1",
}

# hqdn3d luma/chroma spatial and temporal strengths. The middle row is FFmpeg's
# own default; the others are half and double it.
DENOISE_FILTERS: dict[str, str] = {
    "light": "hqdn3d=2:1.5:3:2.25",
    "medium": "hqdn3d=4:3:6:4.5",
    "heavy": "hqdn3d=8:6:12:9",
}

# `unsharp=lx:ly:la:cx:cy:ca` -- luma amount only, chroma left alone so colour
# fringing is not amplified along with detail.
SHARPEN_FILTERS: dict[str, str] = {
    "light": "unsharp=5:5:0.5:5:5:0.0",
    "medium": "unsharp=5:5:1.0:5:5:0.0",
    "heavy": "unsharp=5:5:1.5:5:5:0.0",
}

BLUR_FILTERS: dict[str, str] = {
    "light": "boxblur=2:1",
    "medium": "boxblur=5:1",
    "heavy": "boxblur=10:1",
}

# `eq` ranges FFmpeg accepts, used to validate the wizard's answers.
ADJUST_RANGES: dict[str, tuple[float, float, float]] = {
    # key: (minimum, maximum, neutral)
    "adjust_brightness": (-1.0, 1.0, 0.0),
    "adjust_contrast": (0.0, 3.0, 1.0),
    "adjust_saturation": (0.0, 3.0, 1.0),
    "adjust_gamma": (0.1, 10.0, 1.0),
}

# Every key the look-filters question may write. Listed once so a re-ask can
# clear the previous attempt instead of leaving half of it behind. Defined
# here, AFTER `ADJUST_RANGES`, because it splices that dict's keys in -- moved
# any earlier and this raises NameError at import.
LOOK_ANSWER_KEYS = ("rotate_choice", "flip_horizontal", "flip_vertical",
                    "adjust_grayscale", "denoise_level", "sharpen_level",
                    "blur_level", "fade_in_seconds", "fade_out_seconds",
                    *ADJUST_RANGES)
