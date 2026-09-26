"""FFmpeg URL grammar: which local files an operand really opens (F11-1).

FFmpeg picks a protocol from the leading run of scheme characters before a colon
(`url_find_protocol`, libavformat/avio.c). Several protocols only WRAP another URL:
`cache:clip.wav` reads `clip.wav`, `tee:a.wav|clip.wav` writes it. Comparing the
wrapper text as if it were a filename finds nothing and lets an output overwrite the
wrapped source, so every operand is resolved here, recursively, to the files FFmpeg
opens. Rules copied from FFmpeg master 8864fd0a; see the per-function notes.
"""
from __future__ import annotations

import os
import string
from pathlib import Path

from ffmwiz.support.L00_command_grammar import bounded_text

# A concat list is a text file. Beyond this it is not one, and a guard must not
# read an arbitrary amount of disk at the moment a job starts -- but it must say
# so rather than report "no members".
CONCAT_LIST_MAX_BYTES = 4 * 1024 * 1024
SCHEME_CHARS = frozenset(string.ascii_letters + string.digits + "+-.")
MAX_NESTING = 16

# Protocols that open one inner URL after "<name>:" (async.c, cache.c, crypto.c,
# shared.c, md5proto.c). `md5:` with nothing after it writes to stdout.
SINGLE_WRAPPERS = frozenset({"async", "cache", "crypto", "shared", "md5"})
# Endpoints that are not local files: streams, inline data, a disc directory, network.
NON_FILE_SCHEMES = frozenset({
    "pipe", "fd", "data", "bluray", "http", "https", "ftp", "gopher", "gophers", "icecast",
    "ipfs", "ipns", "mmsh", "mmst", "rtmp", "rtmpe", "rtmps", "rtmpt", "rtmpte", "rtmpts",
    "rtp", "rtsp", "rtsps", "srt", "srtp", "udp", "udplite", "tcp", "tls", "dtls", "sctp",
    "rist", "zmq", "sap", "amqp", "prompeg", "unix", "whip", "sftp", "smb", "ssh",
    "librtmp", "librtmpe", "librtmps", "librtmpt", "librtmpte", "libsrt", "libssh",
    "libsmbclient", "android_content"})


def url_scheme(value: str) -> str | None:
    """The protocol name FFmpeg would select, or None for a local path.

    A single letter before the colon is a drive. FFmpeg only treats it so on
    Windows builds, but calling it a file everywhere can only add a comparison.
    """
    text = str(value or "")
    length = 0
    while length < len(text) and text[length] in SCHEME_CHARS:
        length += 1
    if text.startswith("subfile,") and ":" in text[length + 1:]:
        return "subfile"
    if length >= len(text) or text[length] != ":" or length == 1:
        return None
    return text[:length]


def ffmpeg_get_token(text: str, terminators: str) -> tuple[str, str]:
    """Port of av_get_token (libavutil/avstring.c): (token, unconsumed rest).

    Leading whitespace is skipped, a backslash escapes one character, single
    quotes are literal, and unquoted trailing whitespace is trimmed.
    """
    whitespace = " \n\t\r"
    index = 0
    while index < len(text) and text[index] in whitespace:
        index += 1
    out: list[str] = []
    keep = 0
    while index < len(text) and text[index] not in terminators:
        char = text[index]
        index += 1
        if char == "\\" and index < len(text):
            out.append(text[index])
            index += 1
            keep = len(out)
        elif char == "'":
            while index < len(text) and text[index] != "'":
                out.append(text[index])
                index += 1
            if index < len(text):
                index += 1
                keep = len(out)
        else:
            out.append(char)
    end = len(out)
    while end > keep and out[end - 1] in whitespace:
        end -= 1
    return "".join(out[:end]), text[index:]


def _literal(text: str) -> list[Path]:
    """The local file of a scheme-less or `file:` operand, [] for a device."""
    if text.startswith("file:"):
        text = text[len("file:"):]
        if not text:
            return []
    elif text == "-":
        return []
    if not text or text == os.devnull or (os.name == "nt" and text.casefold() == "nul"):
        return []
    return [Path(text)]


def _split_tokens(text: str) -> list[str]:
    tokens = []
    while text:
        token, text = ffmpeg_get_token(text, "|")
        text = text[1:]
        tokens.append(token)
    return tokens


def _resolve(text: str, depth: int, files: list[Path], problems: list[str]) -> None:
    if depth > MAX_NESTING:
        problems.append(f"FFmpeg URL nested more than {MAX_NESTING} levels: {text[:80]!r}")
        return
    scheme = url_scheme(text)
    if scheme is None or scheme == "file":
        files.extend(_literal(text))
        return
    if scheme in NON_FILE_SCHEMES:
        return
    if scheme in SINGLE_WRAPPERS:
        inner = text[len(scheme) + 1:]
        if inner or scheme != "md5":
            _resolve(inner, depth + 1, files, problems)
        return
    if scheme.startswith("crypto+"):
        _resolve(text[len("crypto+"):], depth + 1, files, problems)
        return
    if scheme == "subfile":
        # subfile,,start,N,end,M,,:<url> -- the options end at the first ",,:".
        marker = text.find(",,:")
        if text.startswith("subfile:"):
            _resolve(text[len("subfile:"):], depth + 1, files, problems)
        elif marker < 0:
            problems.append(f"cannot parse subfile URL: {text[:80]!r}")
        else:
            _resolve(text[marker + 3:], depth + 1, files, problems)
        return
    if scheme == "concat":
        for part in text[len("concat:"):].split("|"):
            if part:
                _resolve(part, depth + 1, files, problems)
        return
    if scheme == "tee":
        for child in _split_tokens(text[len("tee:"):]):
            if child.startswith("["):
                close = child.find("]")
                if close < 0:
                    problems.append(f"cannot parse tee child options: {child[:80]!r}")
                    continue
                child = child[close + 1:]
            if child:
                _resolve(child, depth + 1, files, problems)
        return
    if scheme == "concatf":
        _resolve_concatf(text[len("concatf:"):], depth, files, problems)
        return
    problems.append(f"unsupported FFmpeg protocol {scheme + ':'!r} in {text[:80]!r}; "
                    "the files it opens cannot be checked")


def _resolve_concatf(inner: str, depth: int, files: list[Path], problems: list[str]) -> None:
    lists: list[Path] = []
    _resolve(inner, depth + 1, lists, problems)
    files.extend(lists)
    if len(lists) != 1:
        if not problems:
            problems.append(f"concatf list is not one local file: {inner[:80]!r}")
        return
    try:
        if lists[0].stat().st_size > CONCAT_LIST_MAX_BYTES:
            raise ValueError(f"larger than {CONCAT_LIST_MAX_BYTES} bytes")
        text = bounded_text(lists[0], CONCAT_LIST_MAX_BYTES)
    except (OSError, ValueError) as exc:
        problems.append(f"concatf list {lists[0]} cannot be read: {exc}")
        return
    while text.strip(" \n\t\r"):
        member, text = ffmpeg_get_token(text, "\r\n")
        text = text[1:]
        if member:
            _resolve(member, depth + 1, files, problems)


def resolve_url(value: str, strict: bool = True) -> tuple[list[Path], list[str]]:
    """(local files, refusal reasons) for one FFmpeg operand.

    strict: the operand is opened through FFmpeg's protocol layer (inputs,
    positional outputs, concat members), so an unknown scheme is a refusal.
    Otherwise (filter arguments, file-valued options, some opened with plain
    fopen) the literal name is kept too and an unknown scheme is not an error.
    """
    text = str(value) if value is not None else ""
    files: list[Path] = []
    problems: list[str] = []
    if not text:
        return files, problems
    if strict:
        _resolve(text, 0, files, problems)
        return files, problems
    if not text.startswith("pipe:"):
        files.extend(_literal(text))
    scheme = url_scheme(text)
    if scheme and scheme != "file" and scheme not in NON_FILE_SCHEMES:
        wrapped: list[Path] = []
        trouble: list[str] = []
        _resolve(text, 0, wrapped, trouble)
        files.extend(wrapped)
        problems.extend(p for p in trouble if not p.startswith("unsupported FFmpeg protocol"))
    return files, problems


def ffmpeg_url_path(value: str) -> Path | None:
    """The literal local file of a scheme-less or `file:` operand, else None.

    Whitespace, a leading dash and the name ``null`` are meaningful: they are part
    of the name FFmpeg opens. ``file:`` is removed once and forces a filename.
    """
    text = str(value) if value is not None else ""
    if url_scheme(text) not in (None, "file"):
        return None
    found = _literal(text)
    return found[0] if found else None


__all__ = ["CONCAT_LIST_MAX_BYTES", "MAX_NESTING", "url_scheme", "ffmpeg_get_token", "resolve_url", "ffmpeg_url_path"]
