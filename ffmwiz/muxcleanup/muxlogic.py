# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .constants import (
    AUDIO_ALL,
    AUDIO_BY_INDEX,
    AUDIO_BY_LANGUAGE,
    AUDIO_BY_TITLE,
    FFMPEG_BIN,
    SUBTITLE_ALL,
    SUBTITLE_BY_INDEX,
    SUBTITLE_BY_LANGUAGE,
    SUBTITLE_BY_TITLE,
    SUBTITLE_NONE,
)
from .models import MediaFile, SelectionRules, StreamInfo, StreamMetadataEdit
from .textutil import normalize_language_code


def text_matches_any(value: str, needles: list[str]) -> bool:
    haystack = (value or "").lower()
    return any(needle in haystack for needle in needles)


def apply_stream_order(streams: list[StreamInfo], order: Sequence[int]) -> list[StreamInfo]:
    """Put the streams the user named first, in the order they named them.

    `-map` order is what decides the output stream order, so reordering here is
    the whole feature. Anything not named keeps its original relative position
    after the named ones: a user who only wants one track moved should not have
    to retype the rest. An index that names no kept stream is ignored, and a
    repeated one is honoured once.
    """
    if not order:
        return streams

    by_index = {stream.index: stream for stream in streams}
    ordered: list[StreamInfo] = []
    placed = set()
    for index in order:
        stream = by_index.get(index)
        if stream is not None and index not in placed:
            ordered.append(stream)
            placed.add(index)

    return ordered + [stream for stream in streams if stream.index not in placed]


def matched_audio_streams(media: MediaFile, rules: SelectionRules) -> list[StreamInfo]:
    audio = media.audio_streams

    if rules.audio_mode == AUDIO_BY_LANGUAGE:
        wanted = {normalize_language_code(value) for value in rules.audio_languages}
        return [s for s in audio if normalize_language_code(s.language) in wanted]

    if rules.audio_mode == AUDIO_BY_TITLE:
        return [s for s in audio if text_matches_any(s.title, rules.audio_titles)]

    if rules.audio_mode == AUDIO_BY_INDEX:
        return [s for s in audio if s.index in rules.audio_indexes]

    if rules.audio_mode == AUDIO_ALL:
        return audio

    return []


def selected_audio_streams(media: MediaFile, rules: SelectionRules) -> list[StreamInfo]:
    """The audio the output keeps, in the order the output will carry it."""
    return apply_stream_order(matched_audio_streams(media, rules), rules.audio_order)


def matched_subtitle_streams(media: MediaFile, rules: SelectionRules) -> list[StreamInfo]:
    subtitles = media.subtitle_streams

    if rules.subtitle_mode == SUBTITLE_NONE:
        return []

    if rules.subtitle_mode == SUBTITLE_BY_LANGUAGE:
        wanted = {normalize_language_code(value) for value in rules.subtitle_languages}
        return [s for s in subtitles if normalize_language_code(s.language) in wanted]

    if rules.subtitle_mode == SUBTITLE_BY_TITLE:
        return [s for s in subtitles if text_matches_any(s.title, rules.subtitle_titles)]

    if rules.subtitle_mode == SUBTITLE_BY_INDEX:
        return [s for s in subtitles if s.index in rules.subtitle_indexes]

    if rules.subtitle_mode == SUBTITLE_ALL:
        return subtitles

    return []


def selected_subtitle_streams(media: MediaFile, rules: SelectionRules) -> list[StreamInfo]:
    """The subtitles the output keeps, in the order the output will carry them."""
    return apply_stream_order(matched_subtitle_streams(media, rules), rules.subtitle_order)


def metadata_edit_applies(edit: StreamMetadataEdit, stream: StreamInfo) -> bool:
    if edit.codec_type != stream.codec_type:
        return False
    if edit.match_indexes and stream.index in edit.match_indexes:
        return True
    if edit.match_languages and normalize_language_code(stream.language) in {
        normalize_language_code(value) for value in edit.match_languages
    }:
        return True
    return not edit.match_indexes and not edit.match_languages


def metadata_values_for_stream(stream: StreamInfo, rules: SelectionRules) -> tuple[str, str]:
    language = ""
    title = ""

    for edit in rules.metadata_edits:
        if not metadata_edit_applies(edit, stream):
            continue
        if edit.language:
            language = normalize_language_code(edit.language)
        if edit.title:
            title = edit.title

    return language, title


def add_stream_metadata_options(
    cmd: list[str],
    stream_spec: str,
    stream: StreamInfo,
    rules: SelectionRules,
) -> None:
    language, title = metadata_values_for_stream(stream, rules)
    if language:
        cmd += [f"-metadata:{stream_spec}", f"language={language}"]
    if title:
        cmd += [f"-metadata:{stream_spec}", f"title={title}"]


def same_stream_indexes(original: Sequence[StreamInfo], selected: Sequence[StreamInfo]) -> bool:
    return [stream.index for stream in original] == [stream.index for stream in selected]


def stream_change_reason(
    label: str,
    original: Sequence[StreamInfo],
    selected: Sequence[StreamInfo],
) -> str | None:
    """Why this stream type forces a remux, or None when nothing changed.

    Keeping every stream but reordering it is a real change, and calling that a
    selection change would send the reader looking for a dropped track.
    """
    if same_stream_indexes(original, selected):
        return None
    if {stream.index for stream in original} == {stream.index for stream in selected}:
        return f"{label} stream order changes"
    return f"{label} stream selection changes"


def metadata_edits_need_remux(streams: Sequence[StreamInfo], rules: SelectionRules) -> bool:
    for stream in streams:
        target_language, target_title = metadata_values_for_stream(stream, rules)
        if target_language and normalize_language_code(stream.language) != normalize_language_code(target_language):
            return True
        if target_title and (stream.title or "") != target_title:
            return True
    return False


def remux_needed_reasons(
    media: MediaFile,
    rules: SelectionRules,
    audio_keep: Sequence[StreamInfo],
    subtitles_keep: Sequence[StreamInfo],
) -> list[str]:
    reasons: list[str] = []

    audio_reason = stream_change_reason("audio", media.audio_streams, audio_keep)
    if audio_reason:
        reasons.append(audio_reason)
    subtitle_reason = stream_change_reason("subtitle", media.subtitle_streams, subtitles_keep)
    if subtitle_reason:
        reasons.append(subtitle_reason)
    if not rules.keep_attachments and media.attachment_streams:
        reasons.append("attachments are removed")
    if not rules.keep_metadata:
        reasons.append("input metadata is removed")
    if not rules.keep_chapters:
        reasons.append("chapters are removed")
    if metadata_edits_need_remux([*audio_keep, *subtitles_keep], rules):
        reasons.append("stream metadata is edited")

    return reasons


def build_ffmpeg_command(
    input_file: Path,
    output_file: Path,
    media: MediaFile,
    rules: SelectionRules,
) -> tuple[list[str], list[StreamInfo], list[StreamInfo]]:
    audio_keep = selected_audio_streams(media, rules)
    subtitles_keep = selected_subtitle_streams(media, rules)

    # -nostdin keeps FFmpeg from grabbing the console, so Ctrl+C reaches MuxCls.
    # -progress writes machine-readable position lines to stdout (out_time_us),
    # which is the only way to know how far along a remux is; -nostats drops the
    # human status line that would otherwise interleave with them.
    cmd = [FFMPEG_BIN, "-hide_banner", "-nostdin", "-nostats", "-progress", "pipe:1"]

    if rules.overwrite:
        cmd.append("-y")
    else:
        cmd.append("-n")

    cmd += ["-i", str(input_file)]

    # Always keep all video streams. This is safer for special files with multiple video streams,
    # such as cover art or alternate angles. Usually this is just one video stream.
    cmd += ["-map", "0:v?"]

    for s in audio_keep:
        cmd += ["-map", f"0:{s.index}"]

    for s in subtitles_keep:
        cmd += ["-map", f"0:{s.index}"]

    if rules.keep_attachments:
        cmd += ["-map", "0:t?"]

    if rules.keep_metadata:
        cmd += ["-map_metadata", "0"]
    else:
        cmd += ["-map_metadata", "-1"]

    if rules.keep_chapters:
        cmd += ["-map_chapters", "0"]
    else:
        cmd += ["-map_chapters", "-1"]

    cmd += ["-c", "copy"]

    # Clear every output default, then restore only the defaults the selected
    # source streams already had. The first kept stream is never promoted just
    # because it is first.
    for index, stream in enumerate(audio_keep):
        cmd += [f"-disposition:a:{index}", "+default" if stream.disposition_default else "-default"]

    for index, stream in enumerate(subtitles_keep):
        cmd += [f"-disposition:s:{index}", "+default" if stream.disposition_default else "-default"]

    for index, stream in enumerate(audio_keep):
        add_stream_metadata_options(cmd, f"s:a:{index}", stream, rules)

    for index, stream in enumerate(subtitles_keep):
        add_stream_metadata_options(cmd, f"s:s:{index}", stream, rules)

    cmd += [str(output_file)]

    return cmd, audio_keep, subtitles_keep
