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

from typing import Any

from ffmwiz import appio
from ffmwiz.appio import paint
from ffmwiz.core.colors import Color
from ffmwiz.core.constants import ROTATE_FILTERS
from ffmwiz.support.L01_filters import (LOOK_ANSWER_KEYS, describe_look,
                                        parse_look_tokens)
from ffmwiz.core.exceptions import Back
from ffmwiz.support.L00_misc_b import is_back_value

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
        for key in LOOK_ANSWER_KEYS:
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
