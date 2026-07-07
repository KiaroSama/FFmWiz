# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .constants import AUDIO_ALL, AUDIO_BY_INDEX, AUDIO_BY_LANGUAGE, AUDIO_BY_TITLE, AUDIO_NONE, SUBTITLE_ALL, SUBTITLE_BY_INDEX, SUBTITLE_BY_LANGUAGE, SUBTITLE_BY_TITLE, SUBTITLE_NONE
from .colors import C, color, dim, info, ok, warn
from .logsetup import LOGGER
from .models import MediaFile, SelectionRules, StreamInfo, StreamMetadataEdit
from .textutil import format_index_list, format_prompt_label, format_text_list, is_unknown_language, normalize_language_code, parse_csv_int
from .prompts import MenuBack, ask_csv_int_required, ask_csv_text_required, ask_language_code, ask_language_codes_required, ask_numbered_menu, ask_text, ask_yes_no, print_metadata_note
from .muxlogic import selected_audio_streams, selected_subtitle_streams
from .reporting import format_metadata_edit, format_metadata_edits, max_stream_count_for, print_selection_preview, print_stream_choices, stream_indexes_for, stream_languages_for, streams_for_type

def kept_streams_for_metadata(
    media_files: List[MediaFile],
    codec_type: str,
    current_rules: Optional[SelectionRules],
) -> List[StreamInfo]:
    if current_rules is None:
        return [
            stream
            for media in media_files
            for stream in media.streams
            if stream.codec_type == codec_type
        ]

    kept: List[StreamInfo] = []
    for media in media_files:
        if codec_type == "audio":
            kept.extend(selected_audio_streams(media, current_rules))
        elif codec_type == "subtitle":
            kept.extend(selected_subtitle_streams(media, current_rules))
    return kept


def kept_languages_for_metadata(
    media_files: List[MediaFile],
    codec_type: str,
    current_rules: Optional[SelectionRules],
) -> List[str]:
    return sorted({
        normalize_language_code(stream.language)
        for stream in kept_streams_for_metadata(media_files, codec_type, current_rules)
    })


def kept_indexes_for_metadata(
    media_files: List[MediaFile],
    codec_type: str,
    current_rules: Optional[SelectionRules],
) -> List[int]:
    return sorted({
        stream.index
        for stream in kept_streams_for_metadata(media_files, codec_type, current_rules)
    })


def ask_metadata_edits(
    media_files: List[MediaFile],
    initial_edits: Optional[Sequence[StreamMetadataEdit]] = None,
    current_rules: Optional[SelectionRules] = None,
) -> List[StreamMetadataEdit]:
    current_edits = list(initial_edits or [])
    audio_languages_available = kept_languages_for_metadata(media_files, "audio", current_rules)
    subtitle_languages_available = kept_languages_for_metadata(media_files, "subtitle", current_rules)
    unknown_audio_found = any(is_unknown_language(value) for value in audio_languages_available)
    unknown_subtitle_found = any(is_unknown_language(value) for value in subtitle_languages_available)
    default_action = "1" if unknown_audio_found else "2" if unknown_subtitle_found else "8"

    while True:
        print()
        print("Edit Output Metadata:")
        print(dim("  Edits apply only to output audio/subtitle streams that are kept. Input files are not changed."))
        if current_edits:
            print(info(f"  Current edits: {format_metadata_edits(current_edits)}"))

        edit_enabled = ask_yes_no("Edit output stream metadata?", bool(current_edits))
        if not edit_enabled:
            return []

        while True:
            if current_edits:
                print(info(f"Current metadata edits: {format_metadata_edits(current_edits)}"))

            try:
                action = ask_numbered_menu(
                    "Metadata edit actions",
                    (
                        ("1", "set audio language by current language"),
                        ("2", "set subtitle language by current language"),
                        ("3", "set audio language by exact stream indexes"),
                        ("4", "set subtitle language by exact stream indexes"),
                        ("5", "set audio title by exact stream indexes"),
                        ("6", "set subtitle title by exact stream indexes"),
                        ("7", "clear metadata edits"),
                        ("8", "done"),
                    ),
                    default_action if not current_edits else "8",
                    "Choose metadata edit",
                    leading_blank=True,
                )
            except MenuBack:
                print(warn("Back. Returning to metadata edit question."))
                break

            if action == "8":
                return current_edits

            if action == "7":
                current_edits = []
                print(warn("Metadata edits cleared."))
                continue

            try:
                if action in {"1", "2"}:
                    codec_type = "audio" if action == "1" else "subtitle"
                    available_languages = audio_languages_available if codec_type == "audio" else subtitle_languages_available
                    if not available_languages:
                        print(warn(f"No kept {codec_type} streams are available for metadata editing."))
                        continue
                    print(format_prompt_label(f"Current {codec_type} languages found: {format_text_list(available_languages)}"))
                    current_languages = ask_language_codes_required(
                        f"Current {codec_type} language code(s) to edit from the list above, (example: *uknown,jpn)"
                    )
                    new_language = ask_language_code(f"New {codec_type} language code, (example: jpn)")
                    current_edits.append(StreamMetadataEdit(
                        codec_type=codec_type,
                        match_languages=current_languages,
                        language=new_language,
                    ))
                    print(ok(f"Added metadata edit: {format_metadata_edit(current_edits[-1])}"))
                    continue

                codec_type = "audio" if action in {"3", "5"} else "subtitle"
                available_indexes = kept_indexes_for_metadata(media_files, codec_type, current_rules)
                if not available_indexes:
                    print(warn(f"No kept {codec_type} streams are available for metadata editing."))
                    continue
                print(format_prompt_label(f"Current {codec_type} indexes found: {format_index_list(available_indexes)}"))
                indexes = ask_csv_int_required(
                    f"{codec_type.capitalize()} stream indexes to edit from the list above, (example: 2,3)",
                    available_indexes,
                )

                if action in {"3", "4"}:
                    new_language = ask_language_code(f"New {codec_type} language code, (example: jpn)")
                    current_edits.append(StreamMetadataEdit(
                        codec_type=codec_type,
                        match_indexes=indexes,
                        language=new_language,
                    ))
                else:
                    new_title = ask_text(f"New {codec_type} title")
                    current_edits.append(StreamMetadataEdit(
                        codec_type=codec_type,
                        match_indexes=indexes,
                        title=new_title,
                    ))

                print(ok(f"Added metadata edit: {format_metadata_edit(current_edits[-1])}"))
            except MenuBack:
                print(warn("Back. Returning to metadata edit actions."))


def ask_keep_indexes(
    label: str,
    available_indexes: List[int],
    exact_mode: str,
    all_mode: str,
    none_mode: str,
) -> Tuple[str, List[int]]:
    print()
    print(color(f"{label} stream selection", C.BOLD + C.WHITE))
    print(info(f"Available {label.lower()} stream indexes: {format_index_list(available_indexes)}"))

    if not available_indexes:
        print(warn(f"No {label.lower()} streams were found; selecting none."))
        return none_mode, []

    print("Enter indexes from the scan report to Keep.")
    print("Examples: 1,2 | all | none")

    available_set = set(available_indexes)

    while True:
        raw = ask_text(f"{label} stream indexes to Keep").lower()
        if not raw:
            print(warn("Please enter stream indexes, all, or none."))
            continue

        if raw in {"all", "a", "*"}:
            return all_mode, []

        if raw in {"none", "n", "no", "remove", "-"}:
            return none_mode, []

        indexes = parse_csv_int(raw)
        if not indexes:
            print(warn("Please enter stream indexes, all, or none."))
            continue

        unknown_indexes = sorted(set(indexes) - available_set)
        if unknown_indexes:
            print(warn(f"These indexes were not found in the scan: {format_index_list(unknown_indexes)}"))
            try:
                keep_unknown = ask_yes_no("Keep these indexes anyway?", False)
            except MenuBack:
                print(warn("Back. Returning to stream index entry."))
                continue
            if not keep_unknown:
                continue

        return exact_mode, indexes


def audio_mode_needs_detail(mode: str) -> bool:
    return mode in {AUDIO_BY_LANGUAGE, AUDIO_BY_TITLE, AUDIO_BY_INDEX}


def subtitle_mode_needs_detail(mode: str) -> bool:
    return mode in {SUBTITLE_BY_LANGUAGE, SUBTITLE_BY_TITLE, SUBTITLE_BY_INDEX}


def previous_advanced_step(
    step: int,
    audio_mode: str,
    subtitle_mode: str,
    skip_audio_selection: bool = False,
    skip_subtitle_selection: bool = False,
) -> int:
    if step == 1:
        return 0
    if step == 2:
        if skip_audio_selection:
            return -1
        return 1 if audio_mode_needs_detail(audio_mode) else 0
    if step == 3:
        return 2
    if step == 4:
        if skip_subtitle_selection:
            if skip_audio_selection:
                return -1
            if audio_mode_needs_detail(audio_mode):
                return 1
            return 0
        return 3 if subtitle_mode_needs_detail(subtitle_mode) else 2
    if step == 5:
        if skip_subtitle_selection:
            if skip_audio_selection:
                return -1
            if audio_mode_needs_detail(audio_mode):
                return 1
            return 0
        if subtitle_mode == SUBTITLE_NONE:
            return 3 if subtitle_mode_needs_detail(subtitle_mode) else 2
        return 4
    if step == 6:
        return 5
    if step == 7:
        return 6
    if step == 8:
        return 7
    if step == 9:
        return 8
    return 0


def previous_exact_step(step: int, subtitle_mode: str) -> int:
    if step == 1:
        return 0
    if step == 2:
        return 1
    if step == 3:
        return 2 if subtitle_mode != SUBTITLE_NONE else 1
    if step == 4:
        return 3
    if step == 5:
        return 4
    if step == 6:
        return 5
    if step == 7:
        return 6
    return 0


def configure_rules_advanced(
    media_files: List[MediaFile],
    initial: Optional[SelectionRules] = None,
    start_step: int = 0,
) -> SelectionRules:
    audio_language_options = stream_languages_for(media_files, "audio")
    subtitle_language_options = stream_languages_for(media_files, "subtitle")
    # Skip audio selection only when no file has more than one audio track.
    # A single language across multiple tracks is not enough to skip: the user
    # may still want to choose which of those tracks to keep.
    max_audio_tracks = max_stream_count_for(media_files, "audio")
    skip_audio_selection = max_audio_tracks <= 1
    skip_subtitle_selection = not subtitle_language_options
    LOGGER.info(
        "Advanced rules setup: audio_languages=%s max_audio_tracks=%d skip_audio_selection=%s "
        "subtitle_languages=%s skip_subtitle_selection=%s",
        audio_language_options,
        max_audio_tracks,
        skip_audio_selection,
        subtitle_language_options,
        skip_subtitle_selection,
    )

    if initial is None and skip_audio_selection:
        start_step = 5 if skip_subtitle_selection else 2

    step = start_step
    audio_mode = initial.audio_mode if initial else AUDIO_BY_LANGUAGE
    audio_languages = list(initial.audio_languages) if initial else []
    audio_titles = list(initial.audio_titles) if initial else []
    audio_indexes = list(initial.audio_indexes) if initial else []
    subtitle_mode = initial.subtitle_mode if initial else SUBTITLE_ALL
    subtitle_languages = list(initial.subtitle_languages) if initial else []
    subtitle_titles = list(initial.subtitle_titles) if initial else []
    subtitle_indexes = list(initial.subtitle_indexes) if initial else []
    keep_attachments = initial.keep_attachments if initial else True
    keep_metadata = initial.keep_metadata if initial else True
    metadata_edits = list(initial.metadata_edits) if initial else []
    keep_chapters = initial.keep_chapters if initial else True
    copy_non_video_files = initial.copy_non_video_files if initial else True
    overwrite = initial.overwrite if initial else False

    if initial is None and skip_audio_selection:
        if audio_language_options:
            audio_mode = AUDIO_BY_LANGUAGE
            audio_languages = list(audio_language_options)
        else:
            audio_mode = AUDIO_NONE
    if initial is None and skip_subtitle_selection:
        subtitle_mode = SUBTITLE_NONE
        keep_attachments = False

    if initial is None and skip_audio_selection:
        print()
        print("Configure Output Rules:")
        if audio_language_options:
            print(format_prompt_label(f"Audio languages found: {format_text_list(audio_language_options)}"))
            print(info("Only one audio track found; keeping all audio."))
        else:
            print(warn("No audio streams found; selecting no audio."))
        if skip_subtitle_selection:
            print(warn("No subtitle streams found; skipping subtitle selection."))

    while True:
        try:
            if step == 0:
                print()
                print("Configure Output Rules:")
                audio_mode = ask_numbered_menu(
                    "Audio selection modes",
                    (
                        (AUDIO_BY_LANGUAGE, "keep audio by language codes."),
                        (AUDIO_BY_TITLE, "keep audio by title text, (example: Stereo,Main)"),
                        (AUDIO_BY_INDEX, "keep audio by exact stream indexes, (example: 2,3)"),
                        (AUDIO_ALL, "keep all audio"),
                        (AUDIO_NONE, "remove all audio"),
                    ),
                    AUDIO_BY_LANGUAGE,
                    "Choose audio mode",
                    leading_blank=True,
                    notes=(f"Found: {format_text_list(audio_language_options)}",),
                )
                audio_languages = []
                audio_titles = []
                audio_indexes = []
                step = 1 if audio_mode_needs_detail(audio_mode) else (5 if skip_subtitle_selection else 2)
                continue

            if step == 1:
                if audio_mode == AUDIO_BY_LANGUAGE:
                    print(format_prompt_label(f"Audio languages found: {format_text_list(audio_language_options)}"))
                    audio_languages = ask_language_codes_required("Audio language codes to Keep from the list above")
                    print_selection_preview(
                        "Audio",
                        media_files,
                        "audio",
                        audio_mode,
                        languages=audio_languages,
                    )
                elif audio_mode == AUDIO_BY_TITLE:
                    print_stream_choices("Audio", streams_for_type(media_files, "audio"), include_index=False)
                    audio_titles = ask_csv_text_required("Audio title text to Keep, (example: japanese,commentary)")
                    print_selection_preview(
                        "Audio",
                        media_files,
                        "audio",
                        audio_mode,
                        titles=audio_titles,
                    )
                elif audio_mode == AUDIO_BY_INDEX:
                    print_stream_choices("Audio", streams_for_type(media_files, "audio"), include_index=True)
                    audio_indexes = ask_csv_int_required(
                        "Audio stream indexes to Keep, (example: 2,3)",
                        stream_indexes_for(media_files, "audio"),
                    )
                    print_selection_preview(
                        "Audio",
                        media_files,
                        "audio",
                        audio_mode,
                        indexes=audio_indexes,
                    )
                step = 5 if skip_subtitle_selection else 2
                continue

            if step == 2:
                if skip_subtitle_selection:
                    subtitle_mode = SUBTITLE_NONE
                    subtitle_languages = []
                    subtitle_titles = []
                    subtitle_indexes = []
                    keep_attachments = False
                    print(warn("No subtitle streams found; skipping subtitle selection."))
                    step = 5
                    continue

                subtitle_mode = ask_numbered_menu(
                    "Subtitle selection modes",
                    (
                        (SUBTITLE_ALL, "keep all subtitles"),
                        (SUBTITLE_BY_LANGUAGE, "keep subtitles by language codes."),
                        (SUBTITLE_BY_TITLE, "keep subtitles by title text, (example: Signs,Full)"),
                        (SUBTITLE_BY_INDEX, "keep subtitles by exact stream indexes, (example: 3,4)"),
                        (SUBTITLE_NONE, "remove all subtitles"),
                    ),
                    SUBTITLE_ALL,
                    "Choose subtitle mode",
                    leading_blank=True,
                    notes=(f"Found: {format_text_list(subtitle_language_options)}",),
                )
                subtitle_languages = []
                subtitle_titles = []
                subtitle_indexes = []
                step = 3 if subtitle_mode_needs_detail(subtitle_mode) else 4
                continue

            if step == 3:
                if subtitle_mode == SUBTITLE_BY_LANGUAGE:
                    print(format_prompt_label(f"Subtitle languages found: {format_text_list(subtitle_language_options)}"))
                    subtitle_languages = ask_language_codes_required("Subtitle language codes to Keep from the list above")
                    print_selection_preview(
                        "Subtitle",
                        media_files,
                        "subtitle",
                        subtitle_mode,
                        languages=subtitle_languages,
                    )
                elif subtitle_mode == SUBTITLE_BY_TITLE:
                    print_stream_choices("Subtitle", streams_for_type(media_files, "subtitle"), include_index=False)
                    subtitle_titles = ask_csv_text_required("Subtitle title text to Keep, (example: signs,full)")
                    print_selection_preview(
                        "Subtitle",
                        media_files,
                        "subtitle",
                        subtitle_mode,
                        titles=subtitle_titles,
                    )
                elif subtitle_mode == SUBTITLE_BY_INDEX:
                    print_stream_choices("Subtitle", streams_for_type(media_files, "subtitle"), include_index=True)
                    subtitle_indexes = ask_csv_int_required(
                        "Subtitle stream indexes to Keep, (example: 3,4)",
                        stream_indexes_for(media_files, "subtitle"),
                    )
                    print_selection_preview(
                        "Subtitle",
                        media_files,
                        "subtitle",
                        subtitle_mode,
                        indexes=subtitle_indexes,
                    )
                step = 4
                continue

            if step == 4:
                print()
                if subtitle_mode == SUBTITLE_NONE:
                    keep_attachments = False
                    print(warn("Subtitle mode is remove-all, so font attachments will also be removed."))
                else:
                    keep_attachments = ask_yes_no("Keep MKV font attachments? Recommended if you keep ASS/SSA subtitles", True)
                step = 5
                continue

            if step == 5:
                print_metadata_note()
                keep_metadata = ask_yes_no("Keep input metadata?", True)
                step = 6
                continue

            if step == 6:
                metadata_context_rules = SelectionRules(
                    audio_mode=audio_mode,
                    audio_languages=audio_languages,
                    audio_titles=audio_titles,
                    audio_indexes=audio_indexes,
                    subtitle_mode=subtitle_mode,
                    subtitle_languages=subtitle_languages,
                    subtitle_titles=subtitle_titles,
                    subtitle_indexes=subtitle_indexes,
                    keep_attachments=keep_attachments,
                    keep_metadata=keep_metadata,
                    keep_chapters=keep_chapters,
                    overwrite=overwrite,
                    copy_non_video_files=copy_non_video_files,
                    selection_style="advanced",
                    metadata_edits=metadata_edits,
                )
                metadata_edits = ask_metadata_edits(media_files, metadata_edits, metadata_context_rules)
                step = 7
                continue

            if step == 7:
                keep_chapters = ask_yes_no("Keep chapters?", True)
                step = 8
                continue

            if step == 8:
                copy_non_video_files = ask_yes_no("Copy non-video files to output folder?", True)
                step = 9
                continue

            if step == 9:
                overwrite = ask_yes_no("Overwrite existing output files?", False)
                return SelectionRules(
                    audio_mode=audio_mode,
                    audio_languages=audio_languages,
                    audio_titles=audio_titles,
                    audio_indexes=audio_indexes,
                    subtitle_mode=subtitle_mode,
                    subtitle_languages=subtitle_languages,
                    subtitle_titles=subtitle_titles,
                    subtitle_indexes=subtitle_indexes,
                    keep_attachments=keep_attachments,
                    keep_metadata=keep_metadata,
                    keep_chapters=keep_chapters,
                    overwrite=overwrite,
                    copy_non_video_files=copy_non_video_files,
                    selection_style="advanced",
                    metadata_edits=metadata_edits,
                )
        except MenuBack:
            if step == 0:
                raise
            step = previous_advanced_step(
                step,
                audio_mode,
                subtitle_mode,
                skip_audio_selection=skip_audio_selection,
                skip_subtitle_selection=skip_subtitle_selection,
            )
            if step < 0:
                raise
            print(warn("Back. Returning to previous step."))


def configure_rules_exact(
    media_files: List[MediaFile],
    initial: Optional[SelectionRules] = None,
    start_step: int = 0,
) -> SelectionRules:
    step = start_step
    audio_mode = initial.audio_mode if initial else AUDIO_BY_INDEX
    audio_indexes = list(initial.audio_indexes) if initial else []
    subtitle_mode = initial.subtitle_mode if initial else SUBTITLE_BY_INDEX
    subtitle_indexes = list(initial.subtitle_indexes) if initial else []
    keep_attachments = initial.keep_attachments if initial else True
    keep_metadata = initial.keep_metadata if initial else True
    metadata_edits = list(initial.metadata_edits) if initial else []
    keep_chapters = initial.keep_chapters if initial else True
    copy_non_video_files = initial.copy_non_video_files if initial else True
    overwrite = initial.overwrite if initial else False

    while True:
        try:
            if step == 0:
                audio_mode, audio_indexes = ask_keep_indexes(
                    "Audio",
                    stream_indexes_for(media_files, "audio"),
                    exact_mode=AUDIO_BY_INDEX,
                    all_mode=AUDIO_ALL,
                    none_mode=AUDIO_NONE,
                )
                step = 1
                continue

            if step == 1:
                subtitle_mode, subtitle_indexes = ask_keep_indexes(
                    "Subtitle",
                    stream_indexes_for(media_files, "subtitle"),
                    exact_mode=SUBTITLE_BY_INDEX,
                    all_mode=SUBTITLE_ALL,
                    none_mode=SUBTITLE_NONE,
                )
                step = 2
                continue

            if step == 2:
                print()
                if subtitle_mode == SUBTITLE_NONE:
                    keep_attachments = False
                    print(warn("Subtitle selection is none, so font attachments will also be removed."))
                else:
                    keep_attachments = ask_yes_no("Keep MKV font attachments? Recommended if you keep ASS/SSA subtitles", True)
                step = 3
                continue

            if step == 3:
                print_metadata_note()
                keep_metadata = ask_yes_no("Keep input metadata?", True)
                step = 4
                continue

            if step == 4:
                metadata_context_rules = SelectionRules(
                    audio_mode=audio_mode,
                    audio_languages=[],
                    audio_titles=[],
                    audio_indexes=audio_indexes,
                    subtitle_mode=subtitle_mode,
                    subtitle_languages=[],
                    subtitle_titles=[],
                    subtitle_indexes=subtitle_indexes,
                    keep_attachments=keep_attachments,
                    keep_metadata=keep_metadata,
                    keep_chapters=keep_chapters,
                    overwrite=overwrite,
                    copy_non_video_files=copy_non_video_files,
                    selection_style="exact",
                    metadata_edits=metadata_edits,
                )
                metadata_edits = ask_metadata_edits(media_files, metadata_edits, metadata_context_rules)
                step = 5
                continue

            if step == 5:
                keep_chapters = ask_yes_no("Keep chapters?", True)
                step = 6
                continue

            if step == 6:
                copy_non_video_files = ask_yes_no("Copy non-video files to output folder?", True)
                step = 7
                continue

            if step == 7:
                overwrite = ask_yes_no("Overwrite existing output files?", False)
                return SelectionRules(
                    audio_mode=audio_mode,
                    audio_languages=[],
                    audio_titles=[],
                    audio_indexes=audio_indexes,
                    subtitle_mode=subtitle_mode,
                    subtitle_languages=[],
                    subtitle_titles=[],
                    subtitle_indexes=subtitle_indexes,
                    keep_attachments=keep_attachments,
                    keep_metadata=keep_metadata,
                    keep_chapters=keep_chapters,
                    overwrite=overwrite,
                    copy_non_video_files=copy_non_video_files,
                    selection_style="exact",
                    metadata_edits=metadata_edits,
                )
        except MenuBack:
            if step == 0:
                raise
            step = previous_exact_step(step, subtitle_mode)
            print(warn("Back. Returning to previous step."))


def configure_rules(media_files: List[MediaFile]) -> SelectionRules:
    if len(stream_languages_for(media_files, "audio")) <= 1:
        LOGGER.info("Selection style skipped: one or zero audio languages found")
        return configure_rules_advanced(media_files)

    while True:
        selection_style = ask_numbered_menu(
            "Choose Streams to Keep",
            (
                ("1", "advanced rules by language, title, or index"),
                ("2", "choose exact stream indexes from the scan report"),
            ),
            "1",
            "Choose selection style",
            leading_blank=True,
        )

        try:
            if selection_style == "1":
                LOGGER.info("Selection style: advanced")
                return configure_rules_advanced(media_files)

            LOGGER.info("Selection style: exact stream indexes")
            return configure_rules_exact(media_files)
        except MenuBack:
            LOGGER.info("Back requested inside stream selection; returning to selection style")
            print(warn("Back. Returning to selection style."))


def revisit_last_rule_step(media_files: List[MediaFile], rules: SelectionRules) -> SelectionRules:
    if rules.selection_style == "exact":
        return configure_rules_exact(media_files, initial=rules, start_step=7)
    return configure_rules_advanced(media_files, initial=rules, start_step=9)
