# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .constants import AUDIO_ALL, AUDIO_BY_INDEX, AUDIO_BY_LANGUAGE, AUDIO_BY_TITLE, SUBTITLE_ALL, SUBTITLE_BY_INDEX, SUBTITLE_BY_LANGUAGE, SUBTITLE_BY_TITLE
from .colors import C, FILE_LINE_COLOR, HEADER_COLOR, SETTING_AUDIO_COLOR, SETTING_FALSE_COLOR, SETTING_INPUT_PATH_COLOR, SETTING_LABEL_COLOR, SETTING_MODE_COLOR, SETTING_OUTPUT_BASE_COLOR, SETTING_OUTPUT_ROOT_COLOR, SETTING_SUBTITLE_COLOR, SETTING_TRUE_COLOR, SETTING_VALUE_COLOR, color, dim, info, warn
from .models import MediaFile, StreamInfo, StreamMetadataEdit
from .textutil import center_for_terminal, display_language, format_language_list, format_stream, is_unknown_language, language_color, normalize_language_code, separator_line, terminal_separator
from .muxlogic import text_matches_any
from .output import display_path

def header_color(text: str) -> str:
    normalized = text.strip().upper()
    if normalized == "SCAN REPORT: AUDIO AND SUBTITLE STREAMS":
        return C.BOLD + C.SCAN_HEADER
    if normalized == "UNIQUE STREAM SUMMARY":
        return C.BOLD + C.SUMMARY_HEADER
    if normalized == "VERIFY OUTPUT FOLDER":
        return C.BOLD + C.VERIFY_HEADER
    if normalized == "CONFIRM SETTINGS":
        return C.BOLD + C.CONFIRM_HEADER
    if normalized == "PROCESSING FILES":
        return C.BOLD + C.PROCESS_HEADER
    if normalized == "DONE":
        return C.BOLD + C.DONE_HEADER
    return HEADER_COLOR


def print_header(text: str, leading_blank: bool = True) -> None:
    if leading_blank:
        print()
    code = header_color(text)
    print(color(center_for_terminal(text), code))
    print(separator_line(code))


def print_setting(label: str, value: object) -> None:
    normalized = label.lower()

    if normalized == "metadata edits" and isinstance(value, list):
        print(f"{color(label + ':', SETTING_LABEL_COLOR)} {color(format_metadata_edits(value), SETTING_VALUE_COLOR)}")
        return

    if normalized in {"audio languages", "subtitle languages"} and isinstance(value, list):
        print(f"{color(label + ':', SETTING_LABEL_COLOR)} {format_language_list(value, SETTING_AUDIO_COLOR if normalized.startswith('audio') else SETTING_SUBTITLE_COLOR)}")
        return

    if isinstance(value, bool):
        value_color = SETTING_TRUE_COLOR if value else SETTING_FALSE_COLOR
    elif normalized == "input":
        value_color = SETTING_INPUT_PATH_COLOR
    elif normalized == "output base":
        value_color = SETTING_OUTPUT_BASE_COLOR
    elif normalized == "output root":
        value_color = SETTING_OUTPUT_ROOT_COLOR
    elif normalized.endswith("mode"):
        value_color = SETTING_MODE_COLOR
    elif normalized.startswith("audio"):
        value_color = SETTING_AUDIO_COLOR
    elif normalized.startswith("subtitle"):
        value_color = SETTING_SUBTITLE_COLOR
    elif normalized in {"overwrite", "copy non-video files"}:
        value_color = SETTING_FALSE_COLOR if not value else SETTING_TRUE_COLOR
    else:
        value_color = SETTING_VALUE_COLOR

    print(f"{color(label + ':', SETTING_LABEL_COLOR)} {color(value, value_color)}")


def print_scan_report(media_files: List[MediaFile], root: Path) -> None:
    print_header("Scan Report: Audio And Subtitle Streams")

    for i, media in enumerate(media_files, start=1):
        rel = display_path(root, media.path)

        print()
        if i > 1:
            print(terminal_separator())
        print(color(f"File: {rel}", FILE_LINE_COLOR))

        audio = media.audio_streams
        subtitles = media.subtitle_streams

        if audio:
            print(color("  Audio:", C.BOLD + C.AZURE))
            for s in audio:
                print(f"    {format_stream(s)}")
        else:
            print(dim("  Audio: none"))

        if subtitles:
            print(color("  Subtitles:", C.BOLD + C.VIOLET))
            for s in subtitles:
                print(f"    {format_stream(s)}")
        else:
            print(dim("  Subtitles: none"))


def add_stream_summary(
    summary: Dict[Tuple[str, str, str], Tuple[int, str, str, str]],
    stream: StreamInfo,
) -> None:
    lang = normalize_language_code(stream.language)
    title = stream.title or ""
    codec = stream.codec_name or ""
    key = (lang.lower(), title.lower(), codec.lower())
    if key in summary:
        count, display_lang, display_title, display_codec = summary[key]
        summary[key] = (count + 1, display_lang, display_title, display_codec)
    else:
        summary[key] = (1, lang, title, codec)


def format_stream_summary_row(count: int, lang: str, title: str, codec: str) -> str:
    display_lang = display_language(lang)
    display_title = title or "-"
    display_codec = codec or "-"
    return " | ".join((
        color(f"count={count}", C.BOLD + C.GOLD),
        color(f"lang={display_lang}", language_color(lang)),
        color(f"title={display_title}", C.SKY),
        color(f"codec={display_codec}", C.MINT),
    ))


def add_stream_index_summary(
    summary: Dict[Tuple[int, str, str, str], Tuple[int, int, str, str, str]],
    stream: StreamInfo,
) -> None:
    index = stream.index
    lang = normalize_language_code(stream.language)
    title = stream.title or ""
    codec = stream.codec_name or ""
    key = (index, lang.lower(), title.lower(), codec.lower())
    if key in summary:
        count, display_index, display_lang, display_title, display_codec = summary[key]
        summary[key] = (count + 1, display_index, display_lang, display_title, display_codec)
    else:
        summary[key] = (1, index, lang, title, codec)


def format_stream_index_summary_row(count: int, index: int, lang: str, title: str, codec: str) -> str:
    display_lang = display_language(lang)
    display_title = title or "-"
    display_codec = codec or "-"
    return " | ".join((
        color(f"count={count}", C.BOLD + C.GOLD),
        color(f"index={index}", C.BOLD + C.ORANGE),
        color(f"lang={display_lang}", language_color(lang)),
        color(f"title={display_title}", C.SKY),
        color(f"codec={display_codec}", C.MINT),
    ))


def streams_for_type(media_files: List[MediaFile], codec_type: str) -> List[StreamInfo]:
    return [
        stream
        for media in media_files
        for stream in media.streams
        if stream.codec_type == codec_type
    ]


def print_stream_choices(label: str, streams: List[StreamInfo], include_index: bool) -> None:
    heading = f"{label} {'indexes' if include_index else 'titles'} found:"
    print(color(heading, C.BOLD + (C.AZURE if label.lower() == "audio" else C.VIOLET)))

    if not streams:
        print(dim("  none"))
        return

    if include_index:
        index_summary: Dict[Tuple[int, str, str, str], Tuple[int, int, str, str, str]] = {}
        for stream in streams:
            add_stream_index_summary(index_summary, stream)
        for key in sorted(index_summary):
            count, index, lang, title, codec = index_summary[key]
            print(f"  {format_stream_index_summary_row(count, index, lang, title, codec)}")
        return

    title_summary: Dict[Tuple[str, str, str], Tuple[int, str, str, str]] = {}
    for stream in streams:
        add_stream_summary(title_summary, stream)
    for key in sorted(title_summary):
        count, lang, title, codec = title_summary[key]
        print(f"  {format_stream_summary_row(count, lang, title, codec)}")


def matching_streams_for_selection(
    media_files: List[MediaFile],
    codec_type: str,
    mode: str,
    languages: Optional[List[str]] = None,
    titles: Optional[List[str]] = None,
    indexes: Optional[List[int]] = None,
) -> List[StreamInfo]:
    streams = streams_for_type(media_files, codec_type)
    languages = languages or []
    titles = titles or []
    indexes = indexes or []

    if codec_type == "audio":
        if mode == AUDIO_BY_LANGUAGE:
            wanted = {normalize_language_code(value) for value in languages}
            return [stream for stream in streams if normalize_language_code(stream.language) in wanted]
        if mode == AUDIO_BY_TITLE:
            return [stream for stream in streams if text_matches_any(stream.title, titles)]
        if mode == AUDIO_BY_INDEX:
            return [stream for stream in streams if stream.index in indexes]
        if mode == AUDIO_ALL:
            return streams
        return []

    if mode == SUBTITLE_BY_LANGUAGE:
        wanted = {normalize_language_code(value) for value in languages}
        return [stream for stream in streams if normalize_language_code(stream.language) in wanted]
    if mode == SUBTITLE_BY_TITLE:
        return [stream for stream in streams if text_matches_any(stream.title, titles)]
    if mode == SUBTITLE_BY_INDEX:
        return [stream for stream in streams if stream.index in indexes]
    if mode == SUBTITLE_ALL:
        return streams

    return []


def matching_streams_for_media(
    media: MediaFile,
    codec_type: str,
    mode: str,
    languages: Optional[List[str]] = None,
    titles: Optional[List[str]] = None,
    indexes: Optional[List[int]] = None,
) -> List[StreamInfo]:
    return matching_streams_for_selection([media], codec_type, mode, languages, titles, indexes)


def print_selection_preview(
    label: str,
    media_files: List[MediaFile],
    codec_type: str,
    mode: str,
    languages: Optional[List[str]] = None,
    titles: Optional[List[str]] = None,
    indexes: Optional[List[int]] = None,
) -> None:
    selected_by_file = [
        (media, matching_streams_for_media(media, codec_type, mode, languages, titles, indexes))
        for media in media_files
    ]
    streams = [stream for _, matches in selected_by_file for stream in matches]
    matched_files = sum(1 for _, matches in selected_by_file if matches)
    unmatched_files = [media for media, matches in selected_by_file if not matches]

    print(color(f"Selected {label.lower()} streams:", C.BOLD + (C.AZURE if label.lower() == "audio" else C.VIOLET)))
    print(info(f"  files matched={matched_files}/{len(media_files)} | streams selected={len(streams)} | no match={len(unmatched_files)}"))
    if not streams:
        print(warn("  none matched"))
        return

    index_summary: Dict[Tuple[int, str, str, str], Tuple[int, int, str, str, str]] = {}
    for stream in streams:
        add_stream_index_summary(index_summary, stream)
    for key in sorted(index_summary):
        count, index, lang, title, codec = index_summary[key]
        print(f"  {format_stream_index_summary_row(count, index, lang, title, codec)}")

    if unmatched_files:
        print(warn(f"  files with no selected {label.lower()}: {len(unmatched_files)}"))
        for media in unmatched_files[:8]:
            print(warn(f"    {media.path.name}"))
        if len(unmatched_files) > 8:
            print(warn(f"    ... {len(unmatched_files) - 8} more"))


def print_unique_summary(media_files: List[MediaFile]) -> None:
    audio_summary: Dict[Tuple[str, str, str], Tuple[int, str, str, str]] = {}
    subtitle_summary: Dict[Tuple[str, str, str], Tuple[int, str, str, str]] = {}

    for media in media_files:
        for s in media.audio_streams:
            add_stream_summary(audio_summary, s)

        for s in media.subtitle_streams:
            add_stream_summary(subtitle_summary, s)

    print_header("Unique Stream Summary")

    print(color("Audio streams found:", C.BOLD + C.AZURE))
    if audio_summary:
        for key in sorted(audio_summary):
            count, lang, title, codec = audio_summary[key]
            print(f"  {format_stream_summary_row(count, lang, title, codec)}")
    else:
        print(dim("  none"))

    print()
    print(color("Subtitle streams found:", C.BOLD + C.VIOLET))
    if subtitle_summary:
        for key in sorted(subtitle_summary):
            count, lang, title, codec = subtitle_summary[key]
            print(f"  {format_stream_summary_row(count, lang, title, codec)}")
    else:
        print(dim("  none"))


def stream_indexes_for(media_files: List[MediaFile], codec_type: str) -> List[int]:
    return sorted({
        stream.index
        for media in media_files
        for stream in media.streams
        if stream.codec_type == codec_type
    })


def stream_languages_for(media_files: List[MediaFile], codec_type: str) -> List[str]:
    return sorted({
        normalize_language_code(stream.language)
        for media in media_files
        for stream in media.streams
        if stream.codec_type == codec_type
    })


def max_stream_count_for(media_files: List[MediaFile], codec_type: str) -> int:
    # Highest number of streams of this type found in any single file.
    # Used to decide whether stream selection can be skipped: selection is only
    # skippable when no file has more than one track, regardless of language.
    counts = [
        sum(1 for stream in media.streams if stream.codec_type == codec_type)
        for media in media_files
    ]
    return max(counts) if counts else 0


def has_unknown_language(media_files: List[MediaFile], codec_type: Optional[str] = None) -> bool:
    return any(
        (codec_type is None or stream.codec_type == codec_type)
        and is_unknown_language(stream.language)
        for media in media_files
        for stream in media.streams
    )


def metadata_edit_match_text(edit: StreamMetadataEdit) -> str:
    if edit.match_indexes:
        return "indexes=" + ",".join(str(index) for index in edit.match_indexes)
    if edit.match_languages:
        return "languages=" + ", ".join(display_language(value) for value in edit.match_languages)
    return "all"


def metadata_edit_change_text(edit: StreamMetadataEdit) -> str:
    changes = []
    if edit.language:
        changes.append(f"language={display_language(edit.language)}")
    if edit.title:
        changes.append(f"title={edit.title}")
    return ", ".join(changes) if changes else "no changes"


def format_metadata_edit(edit: StreamMetadataEdit) -> str:
    return f"{edit.codec_type} {metadata_edit_match_text(edit)} -> {metadata_edit_change_text(edit)}"


def format_metadata_edits(edits: Sequence[StreamMetadataEdit]) -> str:
    if not edits:
        return "none"
    return "; ".join(format_metadata_edit(edit) for edit in edits)
