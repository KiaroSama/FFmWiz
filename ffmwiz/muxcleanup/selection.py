# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

from .colors import C, color, info, warn
from .constants import (
    AUDIO_ALL,
    AUDIO_BY_INDEX,
    AUDIO_BY_LANGUAGE,
    AUDIO_BY_TITLE,
    AUDIO_NONE,
    SUBTITLE_ALL,
    SUBTITLE_BY_INDEX,
    SUBTITLE_BY_LANGUAGE,
    SUBTITLE_BY_TITLE,
    SUBTITLE_NONE,
)
from .logsetup import LOGGER
from .metadata_edits import ask_metadata_edits
from .models import MediaFile, SelectionRules
from .prompts import (
    MenuBack,
    ask_csv_int_required,
    ask_csv_text_required,
    ask_language_codes_required,
    ask_numbered_menu,
    ask_text,
    ask_yes_no,
    print_metadata_note,
)
from .reporting import (
    max_stream_count_for,
    print_selection_preview,
    print_stream_choices,
    stream_indexes_for,
    stream_languages_for,
    streams_for_type,
)
from .textutil import format_index_list, format_prompt_label, format_text_list, parse_csv_int


def ask_keep_indexes(
    label: str,
    available_indexes: list[int],
    exact_mode: str,
    all_mode: str,
    none_mode: str,
) -> tuple[str, list[int]]:
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
    skip_copy_non_video: bool = False,
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
        # Step 8 is not asked for a single-file input, so Back must not land there.
        return 7 if skip_copy_non_video else 8
    return 0


def previous_exact_step(step: int, subtitle_mode: str, skip_copy_non_video: bool = False) -> int:
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
        # Step 6 is not asked for a single-file input, so Back must not land there.
        return 5 if skip_copy_non_video else 6
    return 0


def should_skip_audio_selection(media_files: list[MediaFile]) -> bool:
    """Only a set with no audio at all can skip the audio menu. Even a single
    track is a real choice: the user may want to drop it (AUDIO_NONE), and that
    option only exists inside the menu."""
    return max_stream_count_for(media_files, "audio") == 0


def configure_rules_advanced(
    media_files: list[MediaFile],
    initial: SelectionRules | None = None,
    start_step: int = 0,
    single_file_input: bool = False,
) -> SelectionRules:
    audio_language_options = stream_languages_for(media_files, "audio")
    subtitle_language_options = stream_languages_for(media_files, "subtitle")
    max_audio_tracks = max_stream_count_for(media_files, "audio")
    skip_audio_selection = should_skip_audio_selection(media_files)
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
    audio_order = list(initial.audio_order) if initial else []
    subtitle_order = list(initial.subtitle_order) if initial else []
    keep_chapters = initial.keep_chapters if initial else True
    copy_non_video_files = initial.copy_non_video_files if initial else True
    overwrite = initial.overwrite if initial else False

    def make_rules() -> SelectionRules:
        # Reads the current step-machine locals at call time.
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
            audio_order=audio_order,
            subtitle_order=subtitle_order,
        )

    if initial is None and skip_audio_selection:
        # Nothing in the scan has audio, so there is nothing to choose from.
        audio_mode = AUDIO_NONE
    if initial is None and skip_subtitle_selection:
        subtitle_mode = SUBTITLE_NONE
        keep_attachments = False

    if initial is None and skip_audio_selection:
        print()
        print("Configure Output Rules:")
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
                edits = ask_metadata_edits(media_files, metadata_edits, make_rules())
                metadata_edits = edits.metadata_edits
                audio_order = edits.audio_order
                subtitle_order = edits.subtitle_order
                step = 7
                continue

            if step == 7:
                keep_chapters = ask_yes_no("Keep chapters?", True)
                step = 8
                continue

            if step == 8:
                if single_file_input:
                    # One file in, one file out: there is no sibling to copy.
                    copy_non_video_files = False
                    step = 9
                    continue
                copy_non_video_files = ask_yes_no("Copy non-video files to output folder?", True)
                step = 9
                continue

            if step == 9:
                overwrite = ask_yes_no("Overwrite existing output files?", False)
                return make_rules()
        except MenuBack:
            if step == 0:
                raise
            step = previous_advanced_step(
                step,
                audio_mode,
                subtitle_mode,
                skip_audio_selection=skip_audio_selection,
                skip_subtitle_selection=skip_subtitle_selection,
                skip_copy_non_video=single_file_input,
            )
            if step < 0:
                raise
            print(warn("Back. Returning to previous step."))


def configure_rules_exact(
    media_files: list[MediaFile],
    initial: SelectionRules | None = None,
    start_step: int = 0,
    single_file_input: bool = False,
) -> SelectionRules:
    step = start_step
    audio_mode = initial.audio_mode if initial else AUDIO_BY_INDEX
    audio_indexes = list(initial.audio_indexes) if initial else []
    subtitle_mode = initial.subtitle_mode if initial else SUBTITLE_BY_INDEX
    subtitle_indexes = list(initial.subtitle_indexes) if initial else []
    keep_attachments = initial.keep_attachments if initial else True
    keep_metadata = initial.keep_metadata if initial else True
    metadata_edits = list(initial.metadata_edits) if initial else []
    audio_order = list(initial.audio_order) if initial else []
    subtitle_order = list(initial.subtitle_order) if initial else []
    keep_chapters = initial.keep_chapters if initial else True
    copy_non_video_files = initial.copy_non_video_files if initial else True
    overwrite = initial.overwrite if initial else False

    def make_rules() -> SelectionRules:
        # Reads the current step-machine locals at call time.
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
            audio_order=audio_order,
            subtitle_order=subtitle_order,
        )

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
                edits = ask_metadata_edits(media_files, metadata_edits, make_rules())
                metadata_edits = edits.metadata_edits
                audio_order = edits.audio_order
                subtitle_order = edits.subtitle_order
                step = 5
                continue

            if step == 5:
                keep_chapters = ask_yes_no("Keep chapters?", True)
                step = 6
                continue

            if step == 6:
                if single_file_input:
                    # One file in, one file out: there is no sibling to copy.
                    copy_non_video_files = False
                    step = 7
                    continue
                copy_non_video_files = ask_yes_no("Copy non-video files to output folder?", True)
                step = 7
                continue

            if step == 7:
                overwrite = ask_yes_no("Overwrite existing output files?", False)
                return make_rules()
        except MenuBack:
            if step == 0:
                raise
            step = previous_exact_step(step, subtitle_mode, skip_copy_non_video=single_file_input)
            print(warn("Back. Returning to previous step."))


def configure_rules(media_files: list[MediaFile], single_file_input: bool = False) -> SelectionRules:
    if len(stream_languages_for(media_files, "audio")) <= 1:
        LOGGER.info("Selection style skipped: one or zero audio languages found")
        return configure_rules_advanced(media_files, single_file_input=single_file_input)

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
                return configure_rules_advanced(media_files, single_file_input=single_file_input)

            LOGGER.info("Selection style: exact stream indexes")
            return configure_rules_exact(media_files, single_file_input=single_file_input)
        except MenuBack:
            LOGGER.info("Back requested inside stream selection; returning to selection style")
            print(warn("Back. Returning to selection style."))


def revisit_last_rule_step(
    media_files: list[MediaFile],
    rules: SelectionRules,
    single_file_input: bool = False,
) -> SelectionRules:
    if rules.selection_style == "exact":
        return configure_rules_exact(media_files, initial=rules, start_step=7, single_file_input=single_file_input)
    return configure_rules_advanced(media_files, initial=rules, start_step=9, single_file_input=single_file_input)
