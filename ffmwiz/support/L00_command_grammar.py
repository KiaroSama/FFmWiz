"""Bounded FFmpeg token parsing for dependency inspection; never execute media."""
from __future__ import annotations

from pathlib import Path
import os
import stat

MAX_GRAPH_BYTES = 4 * 1024 * 1024
READ_OPTIONS = {
    "subtitles": ({"filename", "f"}, True),
    "ass": ({"filename", "f"}, True),
    "movie": ({"filename"}, True),
    "amovie": ({"filename"}, True),
    "drawtext": ({"fontfile", "textfile"}, False),
    "lut1d": ({"file"}, True),
    "lut3d": ({"file"}, True),
}
WRITE_OPTIONS = {
    "psnr": {"stats_file", "f"}, "ssim": {"stats_file", "f"},
    "libvmaf": {"log_path"}, "metadata": {"file"}, "ametadata": {"file"},
}


def token(text: str, index: int, separators: str) -> tuple[str, int]:
    """One av_get_token-style unescape pass, not shell or URL parsing."""
    out, protected_end = [], 0
    while index < len(text) and text[index].isspace():
        index += 1
    while index < len(text) and text[index] not in separators:
        char = text[index]
        index += 1
        if char == "\\" and index < len(text):
            out.append(text[index])
            index += 1
            protected_end = len(out)
        elif char == "'":
            end = text.find("'", index)
            if end < 0:
                raise ValueError("unclosed quote in filter description")
            out.extend(text[index:end])
            index = end + 1
            protected_end = len(out)
        else:
            out.append(char)
    while len(out) > protected_end and out[-1].isspace():
        out.pop()
    return "".join(out), index


def bounded_text(path: Path, limit: int = MAX_GRAPH_BYTES) -> str:
    """Read at most limit+1 bytes, including if the file grows after stat."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{path} is not a regular dependency file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise ValueError(f"{path} exceeds the {limit}-byte dependency limit")
    return data.decode("utf-8", "strict")


def filter_files(graph: str) -> tuple[list[str], list[str], list[str]]:
    """Read/write operands and unresolved syntax, with BOTH escaping layers.

    Parse graph boundaries first, then option boundaries. An instance suffix
    and reordered named options do not change which resource a filter reads.
    Literal text that merely contains 'subtitles=' is not a filter instance.
    """
    reads, writes, problems = [], [], []
    if len(graph.encode("utf-8")) > MAX_GRAPH_BYTES:
        return [], [], ["filter graph exceeds the dependency size limit"]
    index = 0
    try:
        while index < len(graph):
            while index < len(graph) and (graph[index].isspace() or graph[index] in ",;"):
                index += 1
            while index < len(graph) and graph[index] == "[":
                end = graph.find("]", index + 1)
                if end < 0:
                    raise ValueError("unclosed filter link label")
                index = end + 1
                while index < len(graph) and graph[index].isspace():
                    index += 1
            if index >= len(graph):
                break
            name, index = token(graph, index, "=,;[")
            name = name.partition("@")[0]
            args = ""
            if index < len(graph) and graph[index] == "=":
                args, index = token(graph, index + 1, "[],;")
            read_keys, positional = READ_OPTIONS.get(name, (set(), False))
            write_keys = WRITE_OPTIONS.get(name, set())
            cursor, position = 0, 0
            while cursor < len(args):
                item, cursor = token(args, cursor, ":")
                cursor += 1
                key, separator, value = item.partition("=")
                if separator:
                    if key.startswith("/"):
                        # This spelling loads an option from another file. Its
                        # contents may name another dependency; do not guess.
                        problems.append(f"file-loaded filter option {name}/{key} is unsupported")
                    elif key == "fontsdir" and name in {"subtitles", "ass"} and value:
                        # libass loads explicit font-directory members too.
                        # A directory operand alone does not protect its files.
                        reads.append(value)
                        try:
                            for number, member in enumerate(Path(value).iterdir()):
                                if number >= 10000:
                                    raise ValueError("font directory exceeds the member limit")
                                if member.is_file():
                                    reads.append(str(member))
                        except (OSError, ValueError) as exc:
                            problems.append(f"cannot inspect font directory {value}: {exc}")
                    elif key in read_keys and value:
                        reads.append(value)
                    elif key in write_keys and value not in {"", "-", "pipe:1", "pipe:2"}:
                        writes.append(value)
                elif positional and position == 0 and item:
                    reads.append(item)
                position += 1
    except ValueError as exc:
        problems.append(str(exc))
    return reads, writes, problems


def passlog_outputs(prefix: Path) -> list[Path]:
    """Known outputs plus existing indexed sidecars, including temporary files.

    Existing aliases are the destructive case. Enumerate every stream index,
    not just 0 and 1; x264 also writes .mbtree and .temp files before rename.
    """
    import re
    outputs = [Path(f"{prefix}-{number}.log{suffix}")
               for number in range(2)
               for suffix in ("", ".temp", ".mbtree", ".mbtree.temp")]
    pattern = re.compile(re.escape(prefix.name) + r"-\d+\.log(?:\.mbtree)?(?:\.temp)?$")
    try:
        outputs.extend(item for item in prefix.parent.iterdir() if pattern.fullmatch(item.name))
    except FileNotFoundError:
        pass
    return list(dict.fromkeys(outputs))
