"""FFmpeg URL grammar: which local files an operand really opens.

Skeleton only; the implementation follows once CI has shown the new tests failing.
"""
from __future__ import annotations

from pathlib import Path


def url_scheme(value: str) -> str | None:
    raise NotImplementedError


def ffmpeg_get_token(text: str, terminators: str) -> tuple[str, str]:
    raise NotImplementedError


def resolve_url(value: str, strict: bool = True) -> tuple[list[Path], list[str]]:
    raise NotImplementedError


__all__ = ["url_scheme", "ffmpeg_get_token", "resolve_url"]
