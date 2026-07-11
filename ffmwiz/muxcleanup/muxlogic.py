# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, Tuple

from .constants import AUDIO_ALL, AUDIO_BY_INDEX, AUDIO_BY_LANGUAGE, AUDIO_BY_TITLE, FFMPEG_BIN, SUBTITLE_ALL, SUBTITLE_BY_INDEX, SUBTITLE_BY_LANGUAGE, SUBTITLE_BY_TITLE, SUBTITLE_NONE
from .models import MediaFile, SelectionRules, StreamInfo, StreamMetadataEdit
from .textutil import normalize_language_code

def text_matches_any(value: str, needles: List[str]) -> bool:
    haystack = (value or "").lower()
    return any(needle in haystack for needle in needles)


def selected_audio_streams(media: MediaFile, rules: SelectionRules) -> List[StreamInfo]:
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


def selected_subtitle_streams(media: MediaFile, rules: SelectionRules) -> List[StreamInfo]:
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


def metadata_values_for_stream(stream: StreamInfo, rules: SelectionRules) -> Tuple[str, str]:
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
    cmd: List[str],
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


def default_disposition_needs_update(streams: Sequence[StreamInfo]) -> bool:
    if not streams:
        return False
    if streams[0].disposition_default != 1:
        return True
    return any(stream.disposition_default != 0 for stream in streams[1:])


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
) -> List[str]:
    reasons: List[str] = []

    if not same_stream_indexes(media.audio_streams, audio_keep):
        reasons.append("audio stream selection changes")
    if not same_stream_indexes(media.subtitle_streams, subtitles_keep):
        reasons.append("subtitle stream selection changes")
    if not rules.keep_attachments and media.attachment_streams:
        reasons.append("attachments are removed")
    if not rules.keep_metadata:
        reasons.append("input metadata is removed")
    if not rules.keep_chapters:
        reasons.append("chapters are removed")
    if default_disposition_needs_update(audio_keep):
        reasons.append("audio default disposition is normalized")
    if default_disposition_needs_update(subtitles_keep):
        reasons.append("subtitle default disposition is normalized")
    if metadata_edits_need_remux([*audio_keep, *subtitles_keep], rules):
        reasons.append("stream metadata is edited")

    return reasons


def build_ffmpeg_command(
    input_file: Path,
    output_file: Path,
    media: MediaFile,
    rules: SelectionRules,
) -> Tuple[List[str], List[StreamInfo], List[StreamInfo]]:
    audio_keep = selected_audio_streams(media, rules)
    subtitles_keep = selected_subtitle_streams(media, rules)

    cmd = [FFMPEG_BIN, "-hide_banner"]

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

    for index in range(len(audio_keep)):
        cmd += [f"-disposition:a:{index}", "+default" if index == 0 else "-default"]

    for index in range(len(subtitles_keep)):
        cmd += [f"-disposition:s:{index}", "+default" if index == 0 else "-default"]

    for index, stream in enumerate(audio_keep):
        add_stream_metadata_options(cmd, f"s:a:{index}", stream, rules)

    for index, stream in enumerate(subtitles_keep):
        add_stream_metadata_options(cmd, f"s:s:{index}", stream, rules)

    cmd += [str(output_file)]

    return cmd, audio_keep, subtitles_keep
