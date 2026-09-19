"""What an FFmpeg command READS and what it WRITES.

The source-overwrite guard needs one honest answer to that question, and the
answer is not "the last token is the output and inputs follow `-i`". A command
reads media named inside a concat list, subtitles named inside a filtergraph,
attachments and filter scripts; it writes several positional outputs and also
sidecars an option points at. Every one of those was missed at least once, and
each miss let a real FFmpeg run overwrite a file the job was reading (A01).

Three rules this module exists to keep:

* **Read the backend's own grammar, not a generic one.** FFmpeg's `file:`
  protocol takes everything after the prefix LITERALLY -- it does not
  percent-decode and has no fragment -- and its concat lists use the
  `'it'\\''s'` quoting convention with backslash escapes outside quotes.
  Measured on ffmpeg 9.0.1: `file:a%20b.wav` opens a file whose name contains
  `%20`, and `file:///C:/x.wav` does not open at all.
* **Arity and direction are separate properties.** `-vstats_file` takes one
  value like `-attach` does, and then WRITES it. Classifying it as a read let a
  job replace its own input with encoder statistics and exit 0.
* **Unknown is never "no dependency".** A list that cannot be read, or is too
  large to read safely, is an explicit refusal. Silently returning an empty
  member set is how an unreadable list became a command with no sources.
"""
from __future__ import annotations

import os
from ffmwiz.support.L00_command_grammar import bounded_text, filter_files, passlog_outputs
from pathlib import Path

from ffmwiz.core.constants_ffmpeg_options import (FFMPEG_ALL_VALUED_OPTIONS,
                                                  FFMPEG_FILE_VALUED_OPTIONS,
                                                  FFMPEG_VALUELESS_OPTIONS,
                                                  FFMPEG_WRITE_VALUED_OPTIONS)

# A concat list is a text file. Beyond this it is not one, and a guard must not
# read an arbitrary amount of disk at the moment a job starts -- but it must say
# so rather than report "no members".
CONCAT_LIST_MAX_BYTES = 4 * 1024 * 1024

# The first line of a list FFmpeg auto-detects as concat WITHOUT `-f concat`.
FFCONCAT_SIGNATURE = "ffconcat version"

# Filter options whose value is a file the graph READS. `subtitles`/`ass` burn
# a subtitle file into the picture; `movie`/`amovie` splice another media file
# in. None of them is an `-i` input, so none was in the dependency set.
FILTER_FILE_OPTIONS = ("subtitles", "ass", "movie", "amovie")


def concat_token(text: str, start: int = 0) -> tuple[str, int]:
    """One FFmpeg-quoted token from `text`, and the index just past it.

    Implements FFmpeg's own convention, which `shlex` does not: inside single
    quotes every character is literal (so a Windows path keeps its
    backslashes), outside them a backslash escapes the next character, and a
    literal quote is written by closing, escaping and reopening -- `'it'\\''s'`
    reads back as `it's`. This project's three concat writers all emit exactly
    that, so the round trip has to be exact.
    """
    out: list[str] = []
    index = start
    quoted = False
    while index < len(text):
        char = text[index]
        if quoted:
            if char == "'":
                quoted = False
            else:
                out.append(char)
            index += 1
            continue
        if char == "'":
            quoted = True
            index += 1
        elif char == "\\" and index + 1 < len(text):
            out.append(text[index + 1])
            index += 2
        elif char.isspace():
            break
        else:
            out.append(char)
            index += 1
    return "".join(out), index


def looks_like_concat_list(path: Path) -> bool:
    """True when FFmpeg would auto-detect this file as an ffconcat list.

    `-i playlist.ffconcat` needs no `-f concat`: the demuxer probes the first
    line. A guard that only reacted to an explicit `-f concat` therefore saw no
    members at all for the auto-detected spelling.
    """
    try:
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            header = handle.read(64)
        return header.startswith(FFCONCAT_SIGNATURE.encode("ascii"))
    except OSError:
        return False


def concat_list_members(listing: Path) -> tuple[list[Path], list[str]]:
    """(members, problems) for a concat list, resolved relative to the list.

    `problems` is never silently dropped by callers: an unreadable or oversized
    list means the command's real sources are UNKNOWN, and an unknown source is
    exactly the case where writing through an alias destroys data. The caller
    refuses rather than proceeding with an empty set.
    """
    try:
        size = listing.stat().st_size
    except OSError as exc:
        return [], [f"concat list {listing} could not be inspected: {exc}"]
    if size > CONCAT_LIST_MAX_BYTES:
        return [], [f"concat list {listing} is {size} bytes, beyond the "
                    f"{CONCAT_LIST_MAX_BYTES}-byte limit this guard can read, so the "
                    "files it names cannot be protected"]
    try:
        text = bounded_text(listing, CONCAT_LIST_MAX_BYTES)
    except (OSError, ValueError) as exc:
        return [], [f"concat list {listing} could not be read: {exc}"]

    members: list[Path] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keyword, index = concat_token(stripped)
        if keyword.lower() != "file":
            continue
        # Any whitespace separates the directive from its path -- a tab is as
        # valid as a space, and assuming `file ` skipped the tabbed form.
        while index < len(stripped) and stripped[index].isspace():
            index += 1
        value, _ = concat_token(stripped, index)
        if not value:
            continue
        candidate = ffmpeg_url_path(value)
        if candidate is None:
            return members, [f"unsupported concat member: {value}"]
        # Explicit file: operands use the file protocol's own CWD semantics.
        if value.startswith("file:") or candidate.is_absolute():
            members.append(candidate)
        else:
            members.append(listing.parent / candidate)
    return members, []


def ffmpeg_url_path(value: str) -> Path | None:
    """The local path FFmpeg opens for `value`, or None when it is not a file.

    `file:` is a PREFIX, not a URL: FFmpeg takes the remainder verbatim. Running
    it through a generic URL parser percent-decoded `%20` and threw away
    everything after `#`, so a source named `a%20b.wav` or `a#b.wav` was
    compared against a path FFmpeg never touches and its alias went unprotected.
    """
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("pipe:") or text in {"-"}:
        return None
    if lowered in {"nul", "null", os.devnull.lower()}:
        return None
    if lowered.startswith("file:"):
        text = text[len("file:"):]
        if not text:
            return None
    try:
        return Path(text)
    except (TypeError, ValueError):
        return None


def _is_option(token: str) -> bool:
    """A dashed token that is an OPTION, not a negative number (`-aq -1`)."""
    text = str(token or "")
    if not text.startswith("-") or len(text) == 1:
        return False
    try:
        float(text)
    except ValueError:
        return True
    return False


def _takes_a_value(token: str) -> bool:
    base = str(token or "").lower()
    stem = base.partition(":")[0]
    if base in FFMPEG_VALUELESS_OPTIONS or stem in FFMPEG_VALUELESS_OPTIONS:
        return False
    if stem.startswith("-no") and len(stem) > 3:
        return False
    return base in FFMPEG_ALL_VALUED_OPTIONS or stem in FFMPEG_ALL_VALUED_OPTIONS


def _names_a_file(value: str) -> bool:
    text = str(value or "").strip()
    if not text or text.startswith("-"):
        return False
    lowered = text.lower()
    if lowered.startswith("pipe:") or lowered in {"-", "nul", "null", os.devnull.lower()}:
        return False
    return True


def filter_graph_files(value: str) -> list[Path]:
    """Files read by graph instances, not text resembling a filter name."""
    reads, _writes, _problems = filter_files(str(value or ""))
    return [path for value in reads if (path := ffmpeg_url_path(value)) is not None]


def _concat_closure(path: Path, force: bool, reads: list[Path], problems: list[str],
                    stack: set[Path], budget: list[int]) -> None:
    if not (force or looks_like_concat_list(path)):
        return
    try:
        identity = path.resolve()
    except (OSError, RuntimeError) as exc:
        problems.append(f"concat dependency cannot be resolved: {exc}")
        return
    if identity in stack or len(stack) >= 32:
        problems.append(f"cyclic or over-depth concat dependency: {path}")
        return
    try:
        budget[0] -= path.stat().st_size
        if budget[0] < 0:
            problems.append("concat dependency tree exceeds the total byte limit")
            return
    except OSError as exc:
        problems.append(f"concat dependency cannot be inspected: {exc}")
        return
    members, trouble = concat_list_members(path)
    problems.extend(trouble)
    stack.add(identity)
    try:
        for member in members:
            budget[1] -= 1
            if budget[1] < 0:
                problems.append("concat dependency tree exceeds the member limit")
                break
            reads.append(member)
            _concat_closure(member, False, reads, problems, stack, budget)
    finally:
        stack.remove(identity)


def _file_arguments(cmd: list[str]) -> tuple[list[str], list[Path], list[str]]:
    """Expand -/option file once, retaining the parameter file as a read."""
    args, reads, problems = list(cmd), [], []
    index = 1
    while index < len(args):
        option = args[index]
        if option.startswith("-/"):
            if index + 1 >= len(args):
                problems.append(f"missing parameter file for {option}")
                break
            path = ffmpeg_url_path(args[index + 1])
            try:
                if path is None:
                    raise ValueError("parameter file is not local")
                reads.append(path)
                args[index], args[index + 1] = "-" + option[2:], bounded_text(path)
            except (OSError, ValueError) as exc:
                problems.append(f"cannot inspect {option}: {exc}")
            index += 2
        else:
            index += 2 if _is_option(option) and _takes_a_value(option) else 1
    return args, reads, problems



def command_input_paths(cmd: list[str]) -> list[Path]:
    """Every path an FFmpeg argv reads as an INPUT (`-i <path>`)."""
    inputs: list[Path] = []
    for index, token in enumerate(cmd[:-1] if cmd else []):
        if str(token) != "-i":
            continue
        value = str(cmd[index + 1] or "").strip()
        if not _names_a_file(value):
            continue
        path = ffmpeg_url_path(value)
        if path is not None:
            inputs.append(path)
    return inputs


def command_reads(cmd: list[str]) -> tuple[list[Path], list[str]]:
    """Bounded transitive reads, with uncertainty carried to the runtime guard."""
    args, reads, problems = _file_arguments(cmd)
    input_format, index = None, 1
    budget = [8 * 1024 * 1024, 10000]
    while index < len(args):
        option = str(args[index])
        stem = option.lower().partition(":")[0]
        following = str(args[index + 1]) if index + 1 < len(args) else ""
        if stem == "-f":
            input_format = following
        elif stem == "-i" and _names_a_file(following):
            if input_format == "lavfi":
                found, _writes, trouble = filter_files(following)
                reads.extend(path for value in found if (path := ffmpeg_url_path(value)) is not None)
                problems.extend(trouble)
            else:
                path = ffmpeg_url_path(following)
                if path is not None:
                    reads.append(path)
                    _concat_closure(path, input_format == "concat", reads, problems, set(), budget)
            input_format = None
        elif stem in FFMPEG_FILE_VALUED_OPTIONS and _names_a_file(following):
            path = ffmpeg_url_path(following)
            if path is not None:
                reads.append(path)
                if stem in {"-filter_script", "-filter_complex_script"}:
                    try:
                        found, _writes, trouble = filter_files(bounded_text(path))
                        reads.extend(path for value in found if (path := ffmpeg_url_path(value)) is not None)
                        problems.extend(trouble)
                    except (OSError, ValueError) as exc:
                        problems.append(f"cannot inspect filter script {path}: {exc}")
        elif stem in {"-vf", "-af", "-filter", "-filter_complex", "-lavfi"} and following:
            found, _writes, trouble = filter_files(following)
            reads.extend(path for value in found if (path := ffmpeg_url_path(value)) is not None)
            problems.extend(trouble)
        index += 2 if _is_option(option) and _takes_a_value(option) else 1
    return list(dict.fromkeys(reads)), problems



def command_writes(cmd: list[str]) -> list[Path]:
    """Every destination an FFmpeg argv writes: positional outputs AND sidecars.

    A command has as many positional outputs as it has tokens the options did
    not claim. On top of those, some options take a value the run WRITES --
    `-vstats_file` replaces whatever it points at with encoder statistics while
    the media output goes elsewhere, and FFmpeg exits 0 either way (A01).
    """
    cmd, _parameter_reads, _problems = _file_arguments(cmd)
    outputs: list[Path] = []
    index = 1                    # cmd[0] is the executable
    while index < len(cmd or []):
        token = str(cmd[index] or "")
        if _is_option(token):
            stem = token.lower().partition(":")[0]
            following = str(cmd[index + 1] or "") if index + 1 < len(cmd) else ""
            if stem in FFMPEG_WRITE_VALUED_OPTIONS and _names_a_file(following):
                path = ffmpeg_url_path(following)
                if path is not None:
                    outputs.append(path)
                    # `-passlogfile x` writes `x-0.log`, not `x`. The prefix
                    # expansion is a destination too.
                    if stem == "-passlogfile":
                        outputs.extend(passlog_outputs(path))
            if stem in {"-vf", "-af", "-filter", "-filter_complex", "-lavfi",
                        "-filter_script", "-filter_complex_script"}:
                graph = following
                if stem in {"-filter_script", "-filter_complex_script"}:
                    try:
                        graph = bounded_text(Path(following))
                    except (OSError, ValueError):
                        graph = ""  # command_reads carries the refusal reason
                _reads, writes, _trouble = filter_files(graph)
                outputs.extend(path for value in writes if (path := ffmpeg_url_path(value)) is not None)
            index += 2 if _takes_a_value(token) else 1
            continue
        if _names_a_file(token):
            path = ffmpeg_url_path(token)
            if path is not None:
                outputs.append(path)
        index += 1
    return outputs


def command_source_output_conflict(
    cmd: list[str], extra_sources: list[Path] | None = None,
) -> tuple[Path, Path] | None:
    """(source, output) when running `cmd` would overwrite something it reads.

    Every destination compared against every source, including the ones argv
    only implies. `extra_sources` carries what the PLANNER knows and argv cannot
    show; an explicit declaration beats another heuristic.

    Not a proof of safety: a preflight stat cannot close the window between the
    check and the open, which is why a caller that can should publish through a
    staged path rather than write its destination in place.
    """
    from ffmwiz.support.L00_paths import paths_same

    outputs = command_writes(cmd)
    if not outputs:
        return None
    reads, _problems = command_reads(cmd)
    sources = list(reads) + list(extra_sources or [])
    for output in outputs:
        for source in sources:
            if source and paths_same(source, output):
                return source, output
    return None


def command_unresolved_dependencies(cmd: list[str]) -> list[str]:
    """Why this command's read set is INCOMPLETE, or empty when it is complete.

    Separate from the conflict answer because they mean different things: a
    conflict is "this will destroy a file", while this is "I cannot tell what
    this reads". Both refuse, and conflating them would report the second as
    safety.
    """
    _reads, problems = command_reads(cmd)
    return problems


__all__ = [
    "CONCAT_LIST_MAX_BYTES",
    "FFCONCAT_SIGNATURE",
    "FILTER_FILE_OPTIONS",
    "concat_token",
    "looks_like_concat_list",
    "concat_list_members",
    "ffmpeg_url_path",
    "filter_graph_files",
    "command_input_paths",
    "command_reads",
    "command_writes",
    "command_source_output_conflict",
    "command_unresolved_dependencies",
]
