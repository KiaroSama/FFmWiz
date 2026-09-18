# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StreamInfo:
    index: int
    codec_type: str
    codec_name: str = ""
    language: str = ""
    title: str = ""
    channels: int | None = None
    disposition_default: int = 0
    size_bytes: int | None = None

    @classmethod
    def from_ffprobe(cls, raw: dict[str, Any]) -> StreamInfo:
        """Build a stream from one ffprobe record.

        Every value here comes from a file somebody else produced, so nothing
        is parsed with a bare `int()`: ffprobe writes `N/A` wherever a
        container carries no value, and one such field would otherwise raise
        part-way through a scan and take the whole run down with it.
        `parse_int_value` returns None instead, which the display and command
        layers already handle.
        """
        tags = raw.get("tags") or {}
        disposition = raw.get("disposition") or {}
        index = parse_int_value(raw.get("index"))
        return cls(
            index=index if index is not None else -1,
            codec_type=str(raw.get("codec_type", "")),
            codec_name=str(raw.get("codec_name", "")),
            language=str(tags.get("language", "") or "und"),
            title=str(tags.get("title", "") or ""),
            channels=parse_int_value(raw.get("channels")),
            disposition_default=parse_int_value(disposition.get("default")) or 0,
            size_bytes=stream_size_bytes_from_ffprobe(raw, tags),
        )


@dataclass
class MediaFile:
    path: Path
    streams: list[StreamInfo]
    # Container duration in seconds, when ffprobe reports one. FFmpeg's progress
    # output gives a position on the timeline; without this there is nothing to
    # turn that position into a percentage.
    duration_seconds: float | None = None

    @property
    def video_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "video"]

    @property
    def audio_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "audio"]

    @property
    def subtitle_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "subtitle"]

    @property
    def attachment_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "attachment"]


@dataclass
class StreamMetadataEdit:
    codec_type: str
    match_indexes: list[int] = field(default_factory=list)
    match_languages: list[str] = field(default_factory=list)
    language: str = ""
    title: str = ""


@dataclass
class OutputStreamEdits:
    """Everything the output-stream screen decides: what the kept streams are
    called, and what order the output carries them in."""
    metadata_edits: list[StreamMetadataEdit] = field(default_factory=list)
    audio_order: list[int] = field(default_factory=list)
    subtitle_order: list[int] = field(default_factory=list)


@dataclass
class SelectionRules:
    audio_mode: str
    audio_languages: list[str]
    audio_titles: list[str]
    audio_indexes: list[int]

    subtitle_mode: str
    subtitle_languages: list[str]
    subtitle_titles: list[str]
    subtitle_indexes: list[int]

    keep_attachments: bool
    keep_metadata: bool
    keep_chapters: bool
    overwrite: bool
    copy_non_video_files: bool = True
    selection_style: str = "advanced"
    metadata_edits: list[StreamMetadataEdit] = field(default_factory=list)
    # Source stream indexes in the order the output should carry them. Streams
    # left out keep their original relative position after the listed ones, so a
    # partial answer is still a complete order.
    audio_order: list[int] = field(default_factory=list)
    subtitle_order: list[int] = field(default_factory=list)


def parse_int_value(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_float_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_duration_seconds(value: Any) -> float | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if ":" not in text:
        return parse_float_value(text)

    parts = text.split(":")
    try:
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + float(part)
        return seconds
    except ValueError:
        return None


def tag_value_by_prefix(tags: dict[str, Any], prefix: str) -> Any | None:
    wanted = prefix.upper()
    for key, value in tags.items():
        if str(key).upper().startswith(wanted):
            return value
    return None


def stream_size_bytes_from_ffprobe(raw: dict[str, Any], tags: dict[str, Any]) -> int | None:
    exact_bytes = parse_int_value(tag_value_by_prefix(tags, "NUMBER_OF_BYTES"))
    if exact_bytes is not None and exact_bytes >= 0:
        return exact_bytes

    bit_rate = parse_int_value(raw.get("bit_rate"))
    if bit_rate is None:
        bit_rate = parse_int_value(tag_value_by_prefix(tags, "BPS"))

    duration = parse_duration_seconds(raw.get("duration"))
    if duration is None:
        duration = parse_duration_seconds(tag_value_by_prefix(tags, "DURATION"))

    if bit_rate is None or bit_rate <= 0 or duration is None or duration <= 0:
        return None

    return round((bit_rate * duration) / 8)
