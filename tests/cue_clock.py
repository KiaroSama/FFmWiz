"""Read muxed subtitle cues on ONE explicit clock (D16).

Two suites extracted cues with a plain `ffmpeg -i out.mkv -map 0:s:0 out.srt`
and compared the result against times written on the PICTURE clock. Without
`-copyts` the demuxer rebases everything it hands back by the CONTAINER start --
the minimum across all streams -- so an output whose AAC track carries negative
priming returns every cue late by that offset. Measured on FFmpeg 6.1.1 against
an output whose own packets were correct:

    VIDEO_FIRST_PTS  0.0
    SUB_PACKETS      [(0.5, 1.0), (2.5, 1.0)]
    plain extract    [(0.523, 1.523, 'FIRST'), (2.523, 3.523, 'SECOND')]
    -copyts extract  [(0.5, 1.5, 'FIRST'), (2.5, 3.5, 'SECOND')]

The 23 ms came from the reading, not from the product. Widening the tolerance
would have hidden it and blunted the suite against real regressions, so the
clock is made explicit instead: keep the source timestamps with `-copyts`, then
subtract the output's own first video packet. That is the same origin
`TimelineMap`, the joined-timeline offsets and `subtitle_source_origin()` all
use, so a cue read here is directly comparable to the frame it should sit on.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import FFmWiz


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace", timeout=timeout)


def picture_origin(ffprobe: str, path: Path) -> float:
    """The output's own first video packet, or 0.0 when it has no picture."""
    result = _run([ffprobe, "-v", "error", "-print_format", "json",
                   "-select_streams", "v", "-show_packets",
                   "-read_intervals", "%+#1", path])
    packets = (json.loads(result.stdout or "{}") or {}).get("packets") or []
    for packet in packets:
        try:
            return float(packet["pts_time"])
        except (KeyError, TypeError, ValueError):
            continue
    return 0.0


def read_cues(ffmpeg: str, ffprobe: str, path: Path, stream: int = 0,
              rebase: bool = True) -> list[tuple[float, float, str]]:
    """Cues of `path`'s subtitle stream, measured from its first video packet.

    `rebase=False` reproduces the OLD plain extraction, so a test can prove the
    demuxer really does move the timestamps on this FFmpeg build rather than
    assert it from a comment.
    """
    dump = Path(path).with_suffix(f".cueclock{stream}.srt")
    if dump.exists():
        dump.unlink()
    command: list[Any] = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if rebase:
        command.append("-copyts")
    command += ["-i", path, "-map", f"0:s:{stream}", "-c:s", "srt", dump]
    _run(command)
    if not dump.exists() or not dump.stat().st_size:
        return []
    text = dump.read_text(encoding="utf-8")
    dump.unlink()
    origin = picture_origin(ffprobe, path) if rebase else 0.0
    return [(round(start - origin, 3), round(end - origin, 3), body)
            for start, end, body in FFmWiz.parse_srt(text)]
