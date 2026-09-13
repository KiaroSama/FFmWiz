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
from ffmwiz.support.L01_filters import requested_volume_gain

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

# ffmpeg still accepts the pre-`-c:v` spellings, and a denylist written in the
# canonical form waves every one of them through: `-acodec copy` was accepted,
# replaced the audio encoder the wizard had planned, and made a volume-filter
# job fail outright while the printed summary still promised the encode.
RAW_OPTION_ALIASES = {
    "-vcodec": "-c:v", "-acodec": "-c:a", "-scodec": "-c:s", "-dcodec": "-c:d",
    "-vfilter": "-vf", "-afilter": "-af",
}

# ffmpeg options that take NO value of their own. Without this set the token
# after one of them reads as its argument, which is how a bare output pathname
# (`-shortest out.mp4`) slipped in and made ffmpeg write a second, untracked
# file alongside the planned one -- exit code 0, two outputs, one a surprise.
VALUELESS_RAW_OPTIONS = {
    "-an", "-vn", "-sn", "-dn", "-nostats", "-stats", "-copyts", "-re",
    "-start_at_zero", "-shortest", "-ignore_unknown", "-copy_unknown",
    "-benchmark", "-benchmark_all", "-dump", "-hex", "-xerror", "-bitexact",
    "-fix_sub_duration", "-copyinkf", "-autorotate", "-noautorotate",
    "-autoscale", "-noautoscale", "-accurate_seek", "-noaccurate_seek",
    "-debug_ts", "-psnr", "-vstats", "-stdin", "-auto_conversion_filters",
    "-noauto_conversion_filters", "-nostdin",
}


def _is_option_token(token: str) -> bool:
    """Whether a token is an ffmpeg OPTION rather than a value.

    A leading dash is not enough: `-1`, `-0.5` and `-1e3` are ordinary negative
    values (`-aq -1`), and rejecting every dashed token would break them.
    """
    if not token.startswith("-") or len(token) == 1:
        return False
    try:
        float(token)
    except ValueError:
        return True
    return False


def canonical_raw_option(token: str) -> str:
    """An option spelled in the family FFmWiz reasons about.

    A stream qualifier is kept (`-c:v:0` stays qualified) so the caller can
    still compare the BASE, while a legacy alias is rewritten to the modern
    name it is an alias FOR.
    """
    lowered = token.lower()
    head, separator, qualifier = lowered.partition(":")
    canonical = RAW_OPTION_ALIASES.get(head, head)
    return canonical + (separator + qualifier if separator else "")


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
    except OverflowError:
        # `+10000dB` is 10**500. The prompt's retry contract and every config
        # consumer are built on ValueError, so an OverflowError escaped
        # validation entirely instead of asking the user again.
        raise ValueError(
            f"{text!r} is outside {VOLUME_MIN}-{VOLUME_MAX}; "
            f"{VOLUME_MIN} is about -40 dB and {VOLUME_MAX} about +20 dB")
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
    # shlex treats `#` as a comment by default, so `-metadata title=Episode#1`
    # arrived as `-metadata title=Episode` and a path with a `#` lost its tail.
    # An ffmpeg option line has no comments; this is not a shell script.
    lex.commenters = ""
    try:
        parts = list(lex)
    except ValueError as error:      # an unbalanced quote
        raise ValueError(f"could not read that: {error}")

    index = 0
    while index < len(parts):
        part = parts[index]
        if not _is_option_token(part):
            # Nothing reaches here except a token in OPERAND position, and
            # ffmpeg reads an operand as a file: the last one becomes an extra
            # OUTPUT, earlier ones extra inputs. FFmWiz owns both ends of the
            # command -- the summary, the output path, the source-collision
            # check and the progress reader all read them back from `answers`
            # -- so an untracked destination is refused, not silently written.
            raise ValueError(
                f"{part} is a file name, not an option; FFmWiz owns the input "
                f"and output paths, so ffmpeg would write a file the wizard "
                f"does not know about. Remove it, or attach it to the option "
                f"it belongs to")
        canonical = canonical_raw_option(part)
        # Compare the BASE, not just the exact string. ffmpeg lets almost any
        # option take a per-stream qualifier (`-c:v:0`, `-map_metadata:s:0`),
        # and an exact-match denylist waves every one of those spellings
        # through: `-c:v:0` used to slip past `-c`/`-c:v` and get appended
        # after the wizard's own codec choice, so ffmpeg would honour it
        # silently.
        base = canonical.partition(":")[0]
        if canonical in RESERVED_RAW_OPTIONS or base in RESERVED_RAW_OPTIONS:
            raise ValueError(
                f"{part} is set by the wizard itself; changing it here would "
                f"make the printed command and the job disagree")
        index += 1
        if (canonical not in VALUELESS_RAW_OPTIONS
                and base not in VALUELESS_RAW_OPTIONS
                and index < len(parts)
                and not _is_option_token(parts[index])):
            index += 1      # this token is that option's value, not an operand
    return parts


def build_volume_filter(answers: dict[str, Any]) -> list[str]:
    """The `volume` filter, or nothing.

    Before the fade and after LoudNorm: LoudNorm normalises to a target, so a
    manual gain applied first is exactly what it would undo.
    """
    if not requested_volume_gain(answers):
        return []
    factor = float(answers["audio_volume"])
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

    def forget(answers):
        answers.pop("audio_volume", None)

    def record(value, answers):
        answers["audio_volume"] = parse_volume(value)
        return True

    def describe(answers):
        return f"Audio volume: {answers['audio_volume']:g}x"

    appio.ask_optional(answers, "Change the audio volume?", hint,
                       forget, record, describe)


def step_raw_ffmpeg_args(answers: dict[str, Any]) -> None:
    hint = ("n, or your own ffmpeg OPTIONS (no file names), e.g. "
            + paint('-metadata title="My film" -tune film', Color.LIME))

    def forget(answers):
        answers.pop("raw_ffmpeg_args", None)

    def record(value, answers):
        parsed = parse_raw_arguments(value)
        if not parsed:
            return parsed
        answers["raw_ffmpeg_args"] = parsed
        # Printed here rather than left to the helper's single confirmation
        # line: the warning must come BEFORE "Extra options: ..." so the user
        # sees why the command deserves a second look before what was added.
        print(paint("These go in unchanged, just before the output path. "
                    "Options the wizard owns (codecs, filters, maps, -i, -ss) "
                    "and bare file names are refused. Check the command below "
                    "before starting.", Color.YELLOW))
        return parsed

    def describe(answers):
        return "Extra options: " + " ".join(answers["raw_ffmpeg_args"])

    appio.ask_optional(answers, "Extra ffmpeg options?", hint,
                       forget, record, describe)


__all__ = ["VOLUME_MIN", "VOLUME_MAX", "RESERVED_RAW_OPTIONS",
           "RAW_OPTION_ALIASES", "VALUELESS_RAW_OPTIONS", "canonical_raw_option",
           "parse_volume", "parse_raw_arguments", "build_volume_filter",
           "describe_raw", "step_audio_volume", "step_raw_ffmpeg_args"]
