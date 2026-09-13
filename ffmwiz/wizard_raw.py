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

import re
import shlex
from typing import Any

from ffmwiz import appio
from ffmwiz.appio import paint
from ffmwiz.core.colors import Color
from ffmwiz.support.L01_filters import requested_volume_gain
from ffmwiz.core.constants_ffmpeg_options import (FFMPEG_VALUED_OPTIONS,
                                                  FFMPEG_VALUELESS_OPTIONS)
from ffmwiz.support.L01_misc import _config_setting_for_logging

# The tables moved down a layer so the execution-boundary guard reads the same
# contract (A01). The wizard's own names stay, because they are public.
VALUELESS_RAW_OPTIONS = FFMPEG_VALUELESS_OPTIONS
VALUED_RAW_OPTIONS = FFMPEG_VALUED_OPTIONS

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


# Options that take exactly ONE value. Arity is DECLARED here, never guessed
# from whether the next token happens to look like a file: guessing is what let
# `-report extra.wav` and `-nobitexact extra.wav` through, and FFmpeg then wrote
# BOTH `extra.wav` and the planned output with exit 0 (R03). Stream qualifiers
# are stripped before lookup, so `-b:v` and `-metadata:s:a:0` resolve here too.

# The user's own extension of the table above, for an expert option this build
# supports and FFmWiz has not listed. Declaring arity is the ONLY safe way to
# widen the grammar: guessing that an unknown option eats the next token is
# what let a bare filename become a second output (R03), so it stays refused.
RAW_VALUED_OPTIONS_SETTING = "raw_ffmpeg_valued_args"


def user_declared_valued_options() -> set[str]:
    """Extra one-value options declared in config.env, normalized to `-name`."""
    raw = _config_setting_for_logging(RAW_VALUED_OPTIONS_SETTING, "")
    text = str(raw or "").strip()
    if not text or text.lower() in {"n", "no", "none"}:
        return set()
    declared = set()
    for piece in re.split(r"[,\s]+", text):
        name = piece.strip().lower()
        if not name:
            continue
        declared.add(name if name.startswith("-") else f"-{name}")
    return declared

# The file-loaded spelling of any option: `-/filter:a filters.txt` reads the
# VALUE of `-filter:a` out of a file. It reached the wizard's own audio filter
# and replaced it -- measured PCM peak 2048 -> 8190 -- because the leading `/`
# hid the option family from the ownership check (R03).
FILE_LOADED_PREFIX = "-/"

# `-filter_script:a` is the same thing under another name: it hands FFmpeg a
# file whose contents become that stream's filtergraph.
FILTER_SCRIPT_ALIASES = {
    "-filter_script": "-filter", "-filter_script:v": "-filter:v",
    "-filter_script:a": "-filter:a", "-filter_script:s": "-filter:s",
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

    Canonicalization happens BEFORE any ownership decision, because every
    spelling that escaped the check was a spelling this function did not
    normalize: the legacy `-vcodec` alias, the file-loaded `-/filter:a` form and
    the `-filter_script:a` twin all reach options the wizard owns. A stream
    qualifier is kept (`-c:v:0` stays qualified) so the caller can still compare
    the BASE.
    """
    lowered = token.lower()
    if lowered.startswith(FILE_LOADED_PREFIX) and len(lowered) > len(FILE_LOADED_PREFIX):
        lowered = "-" + lowered[len(FILE_LOADED_PREFIX):]
    lowered = FILTER_SCRIPT_ALIASES.get(lowered, lowered)
    head, separator, qualifier = lowered.partition(":")
    head = FILTER_SCRIPT_ALIASES.get(head, head)
    canonical = RAW_OPTION_ALIASES.get(head, head)
    return canonical + (separator + qualifier if separator else "")


def raw_option_arity(token: str) -> int | None:
    """How many values an option takes: 0, 1, or None when it is not known.

    None is the honest answer for an option outside the schema, and the caller
    must treat it as ambiguous rather than assuming it swallows the next token.
    That assumption is the whole of R03: it turned a bare filename into "the
    value of -report" and let FFmpeg write an output nobody planned.
    """
    canonical = canonical_raw_option(token)
    base = canonical.partition(":")[0]
    if canonical in VALUELESS_RAW_OPTIONS or base in VALUELESS_RAW_OPTIONS:
        return 0
    # `-noX` is FFmpeg's boolean-off spelling and never takes a value.
    if base.startswith("-no") and len(base) > 3:
        return 0
    if canonical in VALUED_RAW_OPTIONS or base in VALUED_RAW_OPTIONS:
        return 1
    declared = user_declared_valued_options()
    if canonical in declared or base in declared:
        return 1
    return None


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
        arity = raw_option_arity(part)
        index += 1
        following = parts[index] if index < len(parts) else None
        if arity == 1:
            if following is None or _is_option_token(following):
                raise ValueError(
                    f"{part} needs a value and none was given")
            index += 1
        elif arity is None and following is not None and not _is_option_token(following):
            # FFmWiz does not know this option's arity, so it cannot tell a
            # value from an operand -- and an operand here becomes a file
            # FFmpeg writes. Refused with the two readings named, rather than
            # guessed in the direction that creates a hidden output.
            raise ValueError(
                f"FFmWiz does not know whether {part} takes a value, so it "
                f"cannot tell if {following} is that value or a file name "
                f"FFmpeg would write. If {part} takes a value, declare it in "
                f"config.env as {RAW_VALUED_OPTIONS_SETTING}={part.lstrip('-')} "
                f"and run again; otherwise remove {following}")
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
