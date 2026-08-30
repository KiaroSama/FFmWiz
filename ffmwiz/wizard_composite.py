"""Combining several inputs into ONE picture: overlay, PiP, stacks, audio mix.

Join already takes several inputs and plays them one after another. These put
them on screen -- or in the mix -- AT THE SAME TIME, which is a different graph
entirely: join concatenates, this composites.

Two slots, asked in one prompt:

  * a PICTURE partner (`overlay`, `pip`, `sbs`/`hstack`, `vstack`), and
  * an AUDIO partner (`amix`).

They are separate on purpose. A video with a logo AND a music bed is three
inputs, and the two graphs then reference DIFFERENT ones -- `[0:v][1:v]overlay`
against `[0:a][2:a]amix`. Treating "the extra input" as one thing is exactly
how this codebase has been bitten before (a `-map_chapters 1` that addressed a
subtitle file because an earlier input had shifted the base), so the index of
each partner is carried separately all the way into
`build_composite_filter_graph`.

Only the answers are collected here. `build_composite_command` in
`wizard_build_b` turns them into a command.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ffmwiz import appio
from ffmwiz import services
from ffmwiz.appio import paint
from ffmwiz.core.colors import Color
from ffmwiz.core.constants import (COMPOSITE_ANSWER_KEYS,
                                   COMPOSITE_DEFAULT_CORNER,
                                   COMPOSITE_DEFAULT_MARGIN,
                                   COMPOSITE_DEFAULT_MIX_WEIGHT,
                                   COMPOSITE_DEFAULT_PIP_SCALE,
                                   COMPOSITE_PICTURE_MODES, OVERLAY_CORNERS)
from ffmwiz.core.exceptions import Back
from ffmwiz.support.L00_misc_b import (is_back_value,
                                       looks_like_generated_output_file)
from ffmwiz.support.L00_paths import paths_same
from ffmwiz.support.L02 import terminal_path

# Spelling -> picture mode. `sbs` because that is what the request is usually
# called, `hstack` because that is what FFmpeg calls the filter.
_PICTURE_MODES: dict[str, str] = {
    "overlay": "overlay", "logo": "overlay", "watermark": "overlay",
    "pip": "pip",
    "sbs": "hstack", "hstack": "hstack", "sidebyside": "hstack",
    "vstack": "vstack",
}

_MIX_MODES = {"amix", "mix", "music"}

# token -> (answer key, minimum, maximum). All four carry a number.
_NUMBERS: dict[str, tuple[str, float, float]] = {
    "margin": ("composite_margin", 0.0, 4096.0),
    "opacity": ("composite_opacity", 0.0, 1.0),
    "size": ("composite_scale", 0.05, 1.0),
    "weight": ("composite_audio_weight", 0.0, 4.0),
}

# Which options belong to which mode, so an option that cannot do anything is
# rejected rather than accepted and ignored.
_CORNER_MODES = {"overlay", "pip"}


def parse_composite_tokens(text: str) -> dict[str, Any]:
    """Turn `overlay,tr,margin=20,opacity=0.5` into answer keys.

    Raises ValueError. Separated from the prompt so it can be tested without a
    terminal, exactly like `parse_look_tokens`.
    """
    chosen: dict[str, Any] = {}
    for raw in text.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token in _PICTURE_MODES:
            mode = _PICTURE_MODES[token]
            if chosen.get("composite_mode") not in (None, mode):
                raise ValueError(
                    f"{chosen['composite_mode']} and {mode} both decide where "
                    f"the second picture goes; pick one")
            chosen["composite_mode"] = mode
            continue
        if token in _MIX_MODES:
            chosen["composite_audio_mix"] = True
            continue
        if token in OVERLAY_CORNERS:
            chosen["composite_corner"] = token
            continue
        name, _, amount = token.partition("=")
        if name in _NUMBERS and amount:
            key, low, high = _NUMBERS[name]
            try:
                number = float(amount)
            except ValueError:
                raise ValueError(f"{token!r}: {amount!r} is not a number")
            if not low <= number <= high:
                raise ValueError(f"{token!r}: {name} must be between "
                                 f"{low:g} and {high:g}")
            chosen[key] = int(number) if key == "composite_margin" else number
            continue
        raise ValueError(
            f"{token!r} is not one of: "
            + ", ".join(sorted(set(_PICTURE_MODES) | _MIX_MODES
                               | set(OVERLAY_CORNERS)
                               | {f"{n}=N" for n in _NUMBERS})))

    mode = chosen.get("composite_mode")
    if not mode and not chosen.get("composite_audio_mix"):
        raise ValueError("name what to combine: "
                         + ", ".join(sorted(set(_PICTURE_MODES)))
                         + ", or amix")
    # An option with nothing to apply to is a typo, not a preference. Accepting
    # it silently would leave the hint text promising something the command
    # never does.
    if mode not in _CORNER_MODES:
        for key, label in (("composite_corner", "a corner"),
                           ("composite_margin", "margin"),
                           ("composite_opacity", "opacity")):
            if key in chosen:
                raise ValueError(f"{label} only applies to overlay and pip")
    if "composite_scale" in chosen and mode != "pip":
        raise ValueError("size only applies to pip")
    if "composite_audio_weight" in chosen and not chosen.get("composite_audio_mix"):
        raise ValueError("weight only applies to amix")
    return chosen


def describe_composite(answers: dict[str, Any]) -> str:
    """A short line naming what was chosen, for the summary and the prompt."""
    parts: list[str] = []
    mode = answers.get("composite_mode")
    if mode in _CORNER_MODES:
        corner = answers.get("composite_corner", COMPOSITE_DEFAULT_CORNER)
        margin = int(answers.get("composite_margin", COMPOSITE_DEFAULT_MARGIN))
        piece = f"{mode} at {corner} ({margin} px in)"
        if mode == "pip":
            piece += f", {float(answers.get('composite_scale', COMPOSITE_DEFAULT_PIP_SCALE)):g} of the width"
        opacity = answers.get("composite_opacity")
        if opacity is not None and float(opacity) < 1.0:
            piece += f", {float(opacity):g} opacity"
        parts.append(piece)
    elif mode:
        parts.append("side by side" if mode == "hstack" else "one above the other")
    if mode and answers.get("composite_path"):
        parts.append(f"with {Path(answers['composite_path']).name}")
    if answers.get("composite_audio_mix"):
        weight = float(answers.get("composite_audio_weight", COMPOSITE_DEFAULT_MIX_WEIGHT))
        piece = f"audio mixed at {weight:g}"
        if answers.get("composite_audio_path"):
            piece += f" with {Path(answers['composite_audio_path']).name}"
        parts.append(piece)
    return ", ".join(parts) or "none"


def _ask_partner(answers: dict[str, Any], title: str, hint: str,
                 allow_audio_only: bool) -> dict[str, Any] | None:
    """Ask for one extra input and probe it. None when the user backs out.

    The probe is `services.join_load_media_item`, the same one the Join step
    uses: it already rejects a file with no usable streams and returns the
    stream lists the builder needs, so there is nothing here to duplicate.
    """
    while True:
        value = appio.ask_raw(appio.question_prompt(answers, title, hint, None))
        if is_back_value(value):
            raise Back()
        if not value:
            appio.error("This value cannot be empty. Enter a file path, or '0' to go back.")
            continue
        path = terminal_path(value)
        if not path.exists() or not path.is_file():
            appio.error("File not found. Enter the full file path again.")
            continue
        if answers.get("input_path") and paths_same(path, answers["input_path"]):
            appio.error("That is the main input. Choose a different file.")
            continue
        if looks_like_generated_output_file(path):
            appio.error("This looks like a previously generated FFmWiz output file.")
            continue
        try:
            item = services.join_load_media_item(answers, path,
                                                 allow_audio_only=allow_audio_only)
        except Exception as exc:
            appio.error(str(exc))
            continue
        if allow_audio_only and not item.get("audio_streams"):
            appio.error("That file has no audio to mix. Choose a different file.")
            continue
        return item


def step_video_composite(answers: dict[str, Any]) -> None:
    hint = (
        "n, or " + paint("overlay,tr,margin=20", Color.LIME) + "; "
        + "/".join(sorted(set(_PICTURE_MODES.values()))) + " (sbs=hstack), "
        "amix; " + "|".join(OVERLAY_CORNERS) + ", margin=N, opacity=N, "
        "size=N, weight=N"
    )

    def forget(answers):
        for key in COMPOSITE_ANSWER_KEYS:
            answers.pop(key, None)

    def record(value, answers):
        # `_ask_partner` can send the user 0 (back) out of the file question;
        # that exception must reach ask_optional's caller unchanged, which is
        # exactly what letting it propagate out of here, uncaught, does.
        chosen = parse_composite_tokens(value)
        answers.update(chosen)
        if chosen.get("composite_mode"):
            item = _ask_partner(
                answers, "Second picture file",
                "the video or image to composite with this input", False)
            answers["composite_item"] = item
            answers["composite_path"] = item["path"]
        if chosen.get("composite_audio_mix"):
            item = _ask_partner(
                answers, "Audio file to mix in",
                "music or narration to lay under the main audio", True)
            answers["composite_audio_item"] = item
            answers["composite_audio_path"] = item["path"]
        return chosen

    def describe(answers):
        return f"Compositing: {describe_composite(answers)}"

    appio.ask_optional(answers, "Combine with another input?", hint,
                       forget, record, describe)


__all__ = ["COMPOSITE_ANSWER_KEYS", "parse_composite_tokens",
           "describe_composite", "step_video_composite"]
