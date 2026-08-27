"""The picture-filter question: rotate, flip, colour, denoise, sharpen, fade.

A web tool that inspired these offers each one as its own button. The wizard's
equivalent of a button is a question, and seven more questions on the path to
every encode is a worse trade than the filters are worth -- most jobs want none
of them. So this is ONE optional prompt that takes a list of shortcuts, and
`n` (the default) skips the lot.

The step only records answers. `build_orientation_filters`, `build_look_filters`
and `build_fade_filters` in `wizard_build` decide where each one lands in the
chain, which is where the order actually matters.
"""
from __future__ import annotations

import re
from typing import Any

from ffmwiz import appio
from ffmwiz.appio import paint
from ffmwiz.core.colors import Color
from ffmwiz.core.constants import (ADJUST_RANGES, BLUR_FILTERS, DENOISE_FILTERS,
                                   ROTATE_FILTERS, SHARPEN_FILTERS)
from ffmwiz.core.exceptions import Back
from ffmwiz.support.L00_misc_b import is_back_value

# token -> (answer key, value). The three levels each filter offers are spelled
# out rather than parsed, so an unknown level is rejected instead of silently
# becoming "off".
_FLAGS: dict[str, tuple[str, Any]] = {
    "hflip": ("flip_horizontal", True),
    "vflip": ("flip_vertical", True),
    "gray": ("adjust_grayscale", True),
    "grey": ("adjust_grayscale", True),
}
for _name in ROTATE_FILTERS:
    _FLAGS[_name] = ("rotate_choice", _name)
for _key, _table in (("denoise_level", DENOISE_FILTERS),
                     ("sharpen_level", SHARPEN_FILTERS),
                     ("blur_level", BLUR_FILTERS)):
    _short = _key.split("_")[0]
    for _level in _table:
        _FLAGS[f"{_short}={_level}"] = (_key, _level)
    # Bare `denoise` means the middle setting, which is what someone typing it
    # without a level wants.
    _FLAGS[_short] = (_key, "medium" if "medium" in _table else next(iter(_table)))

# token -> answer key, for the ones that carry a number.
_NUMBERS: dict[str, str] = {
    "fadein": "fade_in_seconds",
    "fadeout": "fade_out_seconds",
    "bright": "adjust_brightness",
    "brightness": "adjust_brightness",
    "contrast": "adjust_contrast",
    "sat": "adjust_saturation",
    "saturation": "adjust_saturation",
}

_LOOK_KEYS = ("rotate_choice", "flip_horizontal", "flip_vertical",
              "adjust_grayscale", "denoise_level", "sharpen_level",
              "blur_level", "fade_in_seconds", "fade_out_seconds",
              *ADJUST_RANGES)


def parse_look_tokens(text: str) -> dict[str, Any]:
    """Turn `90cw,gray,fadein=1.5` into answer keys. Raises ValueError.

    Separated from the prompt so it can be tested without a terminal, and so
    the GUI can reuse the same spelling.
    """
    chosen: dict[str, Any] = {}
    for raw in text.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token in _FLAGS:
            key, value = _FLAGS[token]
            chosen[key] = value
            continue
        name, _, amount = token.partition("=")
        if name in _NUMBERS and amount:
            try:
                number = float(amount)
            except ValueError:
                raise ValueError(f"{token!r}: {amount!r} is not a number")
            if number < 0:
                raise ValueError(f"{token!r}: negative amounts are not allowed")
            chosen[_NUMBERS[name]] = number
            continue
        raise ValueError(f"{token!r} is not one of: " + ", ".join(sorted(_FLAGS)))
    if chosen.get("sharpen_level") and chosen.get("blur_level"):
        raise ValueError("sharpen and blur cancel each other; pick one")
    return chosen


def describe_look(answers: dict[str, Any]) -> str:
    """A short line naming what was chosen, for the summary and the prompt."""
    parts = []
    if answers.get("rotate_choice") not in (None, "none"):
        parts.append(f"rotate {answers['rotate_choice']}")
    for key, label in (("flip_horizontal", "hflip"), ("flip_vertical", "vflip"),
                       ("adjust_grayscale", "grayscale")):
        if answers.get(key):
            parts.append(label)
    for key in ("denoise_level", "sharpen_level", "blur_level"):
        value = answers.get(key)
        if value and value != "off":
            parts.append(f"{key.split('_')[0]} {value}")
    for key, label in (("adjust_brightness", "brightness"),
                       ("adjust_contrast", "contrast"),
                       ("adjust_saturation", "saturation")):
        if key in answers:
            parts.append(f"{label} {answers[key]:g}")
    for key, label in (("fade_in_seconds", "fade in"),
                       ("fade_out_seconds", "fade out")):
        if answers.get(key):
            parts.append(f"{label} {answers[key]:g}s")
    return ", ".join(parts) or "none"


def step_video_look(answers: dict[str, Any]) -> None:
    hint = (
        "n, or a list like " + paint("90cw,gray,fadein=1", Color.LIME) + "; "
        "rotate " + "/".join(k for k in ROTATE_FILTERS if k != "none") + ", "
        "hflip, vflip, gray, denoise[=light|medium|heavy], sharpen[=...], "
        "blur[=...], bright=N, contrast=N, sat=N, fadein=N, fadeout=N"
    )
    while True:
        value = appio.ask_raw(
            appio.question_prompt(answers, "Extra picture filters?", hint, "n"))
        if is_back_value(value):
            raise Back()
        # Re-asking must not leave the previous attempt's keys behind, or a
        # rejected `sharpen,blur` would keep the sharpen on the second pass.
        for key in _LOOK_KEYS:
            answers.pop(key, None)
        if not value or value.strip().lower() in {"n", "no"}:
            return
        try:
            answers.update(parse_look_tokens(value))
        except ValueError as error:
            appio.error(str(error))
            continue
        print(paint(f"Picture filters: {describe_look(answers)}", Color.LIME))
        return


__all__ = ["parse_look_tokens", "describe_look", "step_video_look"]
