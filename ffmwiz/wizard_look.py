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
from ffmwiz.core.constants import ROTATE_FILTERS
from ffmwiz.support.L01_filters import (LOOK_ANSWER_KEYS, describe_look,
                                        parse_look_tokens)

def step_video_look(answers: dict[str, Any]) -> None:
    hint = (
        "n, or a list like " + paint("90cw,gray,fadein=1", Color.LIME) + "; "
        "rotate " + "/".join(k for k in ROTATE_FILTERS if k != "none") + ", "
        "hflip, vflip, gray, denoise[=light|medium|heavy], sharpen[=...], "
        "blur[=...], bright=N, contrast=N, sat=N, fadein=N, fadeout=N"
    )
    def forget(answers):
        for key in LOOK_ANSWER_KEYS:
            answers.pop(key, None)

    def record(value, answers):
        answers.update(parse_look_tokens(value))
        return True

    def describe(answers):
        return f"Picture filters: {describe_look(answers)}"

    appio.ask_optional(answers, "Extra picture filters?", hint,
                       forget, record, describe)


__all__ = ["parse_look_tokens", "describe_look", "step_video_look"]
