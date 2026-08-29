"""Volume, and the escape hatch: your own ffmpeg options.

Both come from the ffmpeg-webCLI review. They sit together because they answer
the same complaint -- "the wizard does not offer the one thing I need" -- from
opposite ends. `volume` is the single most-asked-for audio knob the wizard did
not have. Raw arguments are the general answer for everything else.

On safety: FFmWiz builds an argv LIST and never goes through a shell, so a raw
argument cannot inject a second command no matter what it contains. `shlex`
splits it the way a POSIX shell would quote it, which is what a user copying a
line out of an ffmpeg tutorial expects. What raw arguments CAN do is produce an
invalid command -- and FFmWiz prints the whole command for review before it
runs anything, which is exactly the confirmation this needs.
"""
from __future__ import annotations

import shlex
from typing import Any

from ffmwiz import appio
from ffmwiz.appio import paint
from ffmwiz.core.colors import Color
from ffmwiz.core.exceptions import Back
from ffmwiz.support.L00_misc_b import is_back_value

# ffmpeg's own limits on the `volume` filter are far wider, but a factor
# outside this range is much more likely to be a typo than an intention: 0.01
# is -40 dB and 10.0 is +20 dB, past which clipping is the only outcome.
VOLUME_MIN = 0.01
VOLUME_MAX = 10.0

# Options FFmWiz owns. Letting a raw argument set these does not extend the
# wizard, it makes the wizard lie: the summary, the output path, the plan
# export and the progress reader all read them back from `answers`.
RESERVED_RAW_OPTIONS = {
    "-i", "-y", "-n", "-f", "-progress", "-nostdin", "-hide_banner",
    "-filter_complex", "-lavfi", "-vf", "-filter:v", "-af", "-filter:a",
    "-filter", "-map", "-map_metadata", "-map_chapters", "-map_channel",
    "-c", "-codec", "-c:v", "-codec:v", "-c:a", "-codec:a", "-c:s", "-c:d",
    "-c:t", "-ss", "-t", "-to", "-stream_loop", "-fpre", "-vpre", "-apre",
}


def parse_volume(text: str) -> float:
    """`1.5`, `150%` or `+6dB` -> a linear factor. Raises ValueError.

    Three spellings because all three appear in the wild and the wizard already
    accepts `150%` for speed, so accepting it here too costs nothing and
    surprises no one.
    """
    value = text.strip().lower().replace(" ", "")
    if not value:
        raise ValueError("no volume given")
    try:
        if value.endswith("db"):
            # Converted here rather than returned here: an early return skipped
            # the range check below, and `+40dB` -- a hundredfold gain -- was
            # accepted in silence.
            factor = round(10 ** (float(value[:-2]) / 20.0), 6)
        elif value.endswith("%"):
            factor = float(value[:-1]) / 100.0
        else:
            factor = float(value)
    except ValueError:
        raise ValueError(f"{text!r} is not a volume; try 1.5, 150% or +6dB")
    if not VOLUME_MIN <= factor <= VOLUME_MAX:
        raise ValueError(
            f"{factor:g} is outside {VOLUME_MIN}-{VOLUME_MAX}; "
            f"{VOLUME_MIN} is about -40 dB and {VOLUME_MAX} about +20 dB")
    return factor


def parse_raw_arguments(text: str) -> list[str]:
    """Split a user's own ffmpeg options. Raises ValueError.

    `shlex.split` with posix=True so `-metadata title="my film"` arrives as two
    arguments and keeps its spaces, which is how the line reads in every
    tutorial it will be copied from. `escape` is cleared to "" because its
    default is `\\`, and a Windows path uses `\\` as a separator, not an
    escape character -- left at the default, `C:\\Users\\me\\x.png` would
    lose every backslash. `shlex.split` has no parameter for this, so the
    `shlex.shlex` object it wraps is built directly instead.
    """
    lex = shlex.shlex(text.strip(), posix=True)
    lex.whitespace_split = True
    lex.escape = ""      # a Windows path is not an escape sequence
    try:
        parts = list(lex)
    except ValueError as error:      # an unbalanced quote
        raise ValueError(f"could not read that: {error}")
    for part in parts:
        lowered = part.lower()
        # Compare the BASE, not just the exact string. ffmpeg lets almost any
        # option take a per-stream qualifier (`-c:v:0`, `-map_metadata:s:0`),
        # and an exact-match denylist waves every one of those spellings
        # through: `-c:v:0` used to slip past `-c`/`-c:v` and get appended
        # after the wizard's own codec choice, so ffmpeg would honour it
        # silently.
        base = lowered.partition(":")[0] if lowered.startswith("-") else lowered
        if lowered in RESERVED_RAW_OPTIONS or base in RESERVED_RAW_OPTIONS:
            raise ValueError(
                f"{part} is set by the wizard itself; changing it here would "
                f"make the printed command and the job disagree")
    return parts


def build_volume_filter(answers: dict[str, Any]) -> list[str]:
    """The `volume` filter, or nothing.

    Before the fade and after LoudNorm: LoudNorm normalises to a target, so a
    manual gain applied first is exactly what it would undo.
    """
    try:
        factor = float(answers.get("audio_volume") or 1.0)
    except (TypeError, ValueError):
        return []
    if abs(factor - 1.0) <= 1e-9:
        return []
    clamped = max(VOLUME_MIN, min(VOLUME_MAX, factor))
    return [f"volume={clamped:g}"]


def describe_raw(answers: dict[str, Any]) -> str:
    parts = []
    volume = answers.get("audio_volume")
    if volume and abs(float(volume) - 1.0) > 1e-9:
        parts.append(f"volume {float(volume):g}x")
    if answers.get("raw_ffmpeg_args"):
        parts.append("extra args: " + " ".join(answers["raw_ffmpeg_args"]))
    return ", ".join(parts) or "none"


def step_audio_volume(answers: dict[str, Any]) -> None:
    hint = ("n, or a factor / percentage / decibels like "
            + paint("1.5", Color.LIME) + ", " + paint("150%", Color.LIME)
            + ", " + paint("+6dB", Color.LIME))
    while True:
        value = appio.ask_raw(
            appio.question_prompt(answers, "Change the audio volume?", hint, "n"))
        if is_back_value(value):
            raise Back()
        answers.pop("audio_volume", None)
        if not value or value.strip().lower() in {"n", "no"}:
            return
        try:
            answers["audio_volume"] = parse_volume(value)
        except ValueError as error:
            appio.error(str(error))
            continue
        print(paint(f"Audio volume: {answers['audio_volume']:g}x", Color.LIME))
        return


def step_raw_ffmpeg_args(answers: dict[str, Any]) -> None:
    hint = ("n, or your own ffmpeg options, e.g. "
            + paint('-metadata title="My film" -tune film', Color.LIME))
    while True:
        value = appio.ask_raw(
            appio.question_prompt(answers, "Extra ffmpeg options?", hint, "n"))
        if is_back_value(value):
            raise Back()
        answers.pop("raw_ffmpeg_args", None)
        if not value or value.strip().lower() in {"n", "no"}:
            return
        try:
            parsed = parse_raw_arguments(value)
        except ValueError as error:
            appio.error(str(error))
            continue
        if not parsed:
            return
        answers["raw_ffmpeg_args"] = parsed
        print(paint("These go in unchanged, just before the output path. "
                    "Check the command below before starting.", Color.YELLOW))
        print(paint("Extra options: " + " ".join(parsed), Color.LIME))
        return


__all__ = ["VOLUME_MIN", "VOLUME_MAX", "RESERVED_RAW_OPTIONS",
           "parse_volume", "parse_raw_arguments", "build_volume_filter",
           "describe_raw", "step_audio_volume", "step_raw_ffmpeg_args"]
