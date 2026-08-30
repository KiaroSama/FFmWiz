"""The quick-output question: GIF, boomerang, loop and thumbnail.

The same trade the picture-filter question made. A web tool that inspired these
gives each one its own button; four more questions on the path to every encode
costs more than the features are worth, because almost every job wants none of
them. So this is ONE optional prompt, and `n` (the default) skips the lot.

Three of the four change what the output IS rather than how it looks, so unlike
the picture filters they do not compose with each other: a job is a GIF, or a
boomerang, or a still frame, or none of those. `loop` is the exception -- it is
an INPUT option (`-stream_loop`), it changes nothing about the output shape, so
it rides along with any of them.

The step only records answers. `wizard_build_b.build_quick_output_stages`
decides what commands each one becomes and `encoding.run_quick_output_stages`
runs them; the stage list is built once and consumed by both, so the record and
the execution cannot describe different jobs.
"""
from __future__ import annotations

from typing import Any

from ffmwiz import appio
from ffmwiz.appio import paint
from ffmwiz.core.colors import Color
from ffmwiz.core.constants import (GIF_DEFAULT_FPS, GIF_DEFAULT_WIDTH,
                                   QUICK_ANSWER_KEYS, THUMBNAIL_DEFAULT_EXT)
from ffmwiz.support.L00_misc import parse_colon_duration_seconds

# The container each mode forces. A GIF that came out .mp4 and a still frame
# that came out .mkv are both a broken job, so the mode owns the extension
# rather than trusting the format question that ran several steps earlier.
QUICK_OUTPUT_EXTS = {"gif": "gif", "thumbnail": THUMBNAIL_DEFAULT_EXT}

_MODES = ("gif", "boomerang", "thumb")


def _positive_number(text: str, label: str) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number, not {text!r}") from None
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero, not {text!r}")
    return value


def _time_seconds(text: str, label: str) -> float:
    """Seconds, either plain (`3.5`) or as `HH:MM:SS(.mmm)`."""
    colon = parse_colon_duration_seconds(text)
    if colon is not None:
        return colon
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise ValueError(
            f"{label} must be seconds or HH:MM:SS, not {text!r}") from None
    if value < 0:
        raise ValueError(f"{label} cannot be negative, not {text!r}")
    return value


def parse_quick_tokens(value: str) -> dict[str, Any]:
    """Parse the prompt's answer into answer keys. Raises ValueError on junk.

    Separate from the prompt so the whole vocabulary is testable without a
    terminal, and so the same text could later come from a config key.
    """
    answers: dict[str, Any] = {}
    mode_seen = ""
    for raw in str(value).split(","):
        token = raw.strip().lower()
        if not token:
            continue
        name, _, argument = token.partition("=")
        name = name.strip()
        argument = argument.strip()
        if name == "loop":
            if not argument:
                raise ValueError("loop needs a count, for example loop=2")
            try:
                count = int(argument)
            except ValueError:
                raise ValueError(
                    f"loop needs a whole number of extra plays, not {argument!r}"
                ) from None
            if count < 1:
                raise ValueError("loop must be at least 1 extra play")
            answers["loop_count"] = count
            continue
        if name not in _MODES:
            raise ValueError(
                f"unknown quick output {name!r}; expected one of "
                + ", ".join(_MODES) + ", loop=N, or n")
        if mode_seen == name:
            raise ValueError(f"{name} was given twice")
        if mode_seen:
            raise ValueError(
                f"{mode_seen} and {name} are different outputs; ask for one of them")
        mode_seen = name
        if name == "gif":
            answers["quick_output"] = "gif"
            if argument:
                fps_text, _, width_text = argument.partition(":")
                if fps_text.strip():
                    answers["gif_fps"] = _positive_number(
                        fps_text.strip(), "the GIF frame rate")
                if width_text.strip():
                    answers["gif_width"] = int(_positive_number(
                        width_text.strip(), "the GIF width"))
        elif name == "boomerang":
            if argument:
                raise ValueError("boomerang takes no value")
            answers["quick_output"] = "boomerang"
        else:
            answers["quick_output"] = "thumbnail"
            answers["thumbnail_seconds"] = (
                _time_seconds(argument, "the thumbnail time") if argument else 0.0)
            answers["thumbnail_ext"] = THUMBNAIL_DEFAULT_EXT
    if not answers:
        raise ValueError("no quick output was named")
    return answers


def describe_quick(answers: dict[str, Any]) -> str:
    """One line naming what was asked for, for the summary and the tests."""
    parts: list[str] = []
    mode = str(answers.get("quick_output") or "")
    if mode == "gif":
        parts.append("GIF at {:g} fps, {} px wide (two-pass palette)".format(
            float(answers.get("gif_fps") or GIF_DEFAULT_FPS),
            int(answers.get("gif_width") or GIF_DEFAULT_WIDTH)))
    elif mode == "boomerang":
        parts.append("boomerang (forward, then the same clip reversed)")
    elif mode == "thumbnail":
        parts.append("single frame at {:.3f}s".format(
            float(answers.get("thumbnail_seconds") or 0.0)))
    if answers.get("loop_count"):
        parts.append(f"input repeated {int(answers['loop_count'])} extra time(s)")
    return ", ".join(parts) if parts else "none"


def step_quick_output(answers: dict[str, Any]) -> None:
    hint = (
        "n, or one of " + paint("gif[=fps[:width]]", Color.LIME) + ", "
        "boomerang, thumb[=SECONDS|HH:MM:SS]; loop=N may be added to any of them"
    )

    def record(value, answers):
        parsed = parse_quick_tokens(value)
        answers.update(parsed)
        apply_quick_output_ext(answers)
        return parsed

    def describe(answers):
        return f"Quick output: {describe_quick(answers)}"

    appio.ask_optional(answers, "Quick output?", hint,
                       forget_quick_answers, record, describe)


def apply_quick_output_ext(answers: dict[str, Any]) -> None:
    """Force the container the chosen mode has to produce, remembering the old one.

    The format question runs several steps earlier, so by the time this one is
    answered `output_ext` already holds mp4 or mkv. Writing a GIF into a .mp4
    name is not a cosmetic problem: `build_output_path` names the file from the
    extension and FFmpeg picks the muxer from the suffix, so the job would hand
    a palette-quantised stream to a muxer that cannot carry it.
    """
    forced = QUICK_OUTPUT_EXTS.get(str(answers.get("quick_output") or ""))
    if not forced:
        return
    if "_output_ext_before_quick" not in answers:
        answers["_output_ext_before_quick"] = answers.get("output_ext")
    answers["output_ext"] = forced


def forget_quick_answers(answers: dict[str, Any]) -> None:
    """Clear the whole answer set, and put the previous container back."""
    for key in QUICK_ANSWER_KEYS:
        answers.pop(key, None)
    # Restore whatever the format question chose. Popping the key instead would
    # leave the job with no container at all.
    if "_output_ext_before_quick" in answers:
        previous = answers.pop("_output_ext_before_quick")
        if previous is not None:
            answers["output_ext"] = previous


__all__ = [
    "QUICK_OUTPUT_EXTS",
    "apply_quick_output_ext",
    "describe_quick",
    "forget_quick_answers",
    "parse_quick_tokens",
    "step_quick_output",
]
