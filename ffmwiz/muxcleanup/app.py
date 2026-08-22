# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from . import logsetup
from .constants import FFMPEG_BIN, FFPROBE_BIN, VIDEO_EXTENSIONS
from .colors import C, color, enable_windows_ansi, err, info, ok, warn
from .logsetup import LOGGER, setup_logging
from .models import SelectionRules
from .prompts import MenuBack, MenuExit, ask_output_base_path, ask_path, ask_yes_no, input_path_from_args
from .media import find_non_video_extensions, find_video_files, require_tool, scan_files
from .output import resolve_output_root
from .reporting import print_header, print_scan_report, print_setting, print_unique_summary
from .selection import configure_rules, revisit_last_rule_step
from .processing import ProcessSummary, print_ready_for_next_task, process_files, verify_output

def main_menu(allow_back: bool = False) -> Optional[ProcessSummary]:
    # allow_back is the embedding switch: standalone MuxCls has nowhere to go
    # back to, but inside FFmWiz the input prompt is a way out to the wizard
    # menu. The last run's summary travels back with it so the caller can
    # report the outcome instead of guessing at it.
    enable_windows_ansi()
    log_path = setup_logging()
    print_header("MuxCls", leading_blank=False)
    if log_path:
        print(color(f"Log file: {log_path}", C.LOG_YELLOW))
    print()
    print(color("This script uses ffmpeg -c copy. It does not re-encode video, audio, or subtitles.", C.NOTE_BLUE))
    print()

    if not require_tool(FFMPEG_BIN):
        LOGGER.error("Required tool missing: %s", FFMPEG_BIN)
        print(err("ERROR: ffmpeg was not found in PATH."))
        print("Install FFmpeg or add ffmpeg.exe to PATH, then run this script again.")
        if logsetup.LOG_FILE:
            print(color(f"Log file: {logsetup.LOG_FILE}", C.LOG_YELLOW))
        sys.exit(1)

    if not require_tool(FFPROBE_BIN):
        LOGGER.error("Required tool missing: %s", FFPROBE_BIN)
        print(err("ERROR: ffprobe was not found in PATH."))
        print("Install FFmpeg or add ffprobe.exe to PATH, then run this script again.")
        if logsetup.LOG_FILE:
            print(color(f"Log file: {logsetup.LOG_FILE}", C.LOG_YELLOW))
        sys.exit(1)

    input_from_args = input_path_from_args(sys.argv[1:])
    last_summary: Optional[ProcessSummary] = None

    while True:
        input_root = input_from_args
        input_from_args = None

        if input_root is not None:
            print(info(f"Input from launcher/drag-drop: {input_root}"))
            LOGGER.debug("Input from args: %s", input_root)
            if not input_root.exists():
                LOGGER.warning("Input path from args does not exist: %s", input_root)
                print(err(f"Path does not exist: {input_root}"))
                input_root = None

        if input_root is None:
            try:
                input_root = ask_path(
                    "Input file or folder path (drag/drop here, then press Enter)",
                    must_exist=True,
                    allow_back=allow_back,
                )
            except MenuBack:
                LOGGER.info("Back requested at the input path; leaving the menu")
                return last_summary
        LOGGER.info("Input root: %s", input_root)
        print()

        files = find_video_files(input_root)
        if not files:
            LOGGER.warning("No supported video files found under %s", input_root)
            print(warn("No supported video files found."))
            print(f"Supported extensions: {', '.join(sorted(VIDEO_EXTENSIONS))}")
            skipped_extensions = find_non_video_extensions(input_root)
            if skipped_extensions:
                shown = ", ".join(skipped_extensions[:12])
                suffix = ", ..." if len(skipped_extensions) > 12 else ""
                print(warn(f"Other file extensions found: {shown}{suffix}"))
            sys.exit(1)

        print(ok(f"Found {len(files)} video file(s)."))
        LOGGER.info("Found %s video file(s)", len(files))

        scan = scan_files(files)
        media_files = scan.files
        if not media_files:
            print(err("No files could be scanned successfully."))
            sys.exit(1)

        if scan.failures:
            # Never continue silently: a file that could not be probed would
            # otherwise vanish from every count and from the final summary.
            print(err(f"{len(scan.failures)} file(s) could not be read by ffprobe:"))
            for path in scan.failures:
                print(err(f"  {path}"))
            if not ask_yes_no(
                f"Continue with the {len(media_files)} file(s) that scanned successfully?",
                False,
                allow_back=False,
            ):
                LOGGER.warning("User stopped after %s probe failure(s)", len(scan.failures))
                print(warn("Stopped. Fix or remove those files and run MuxCls again."))
                sys.exit(1)

        print_scan_report(media_files, input_root)
        print_unique_summary(media_files)

        # A single dropped file has no siblings to copy, so the non-video copy
        # question has no answer worth asking for.
        single_file_input = input_root.is_file()

        restart_input = False
        rules: Optional[SelectionRules] = None
        while True:
            if rules is None:
                try:
                    rules = configure_rules(media_files, single_file_input=single_file_input)
                except MenuBack:
                    LOGGER.info("Back requested; returning to input path")
                    print(warn("Back. Returning to input path."))
                    restart_input = True
                    break

            output_base: Optional[Path] = None
            while output_base is None:
                try:
                    output_base = ask_output_base_path(input_root)
                except MenuBack:
                    LOGGER.info("Back requested at output folder; returning to previous rule step")
                    print(warn("Back. Returning to previous step."))
                    # The enclosing loop configures the rules before the output
                    # prompt is ever reached, so this is never None here. The
                    # check is what makes that invariant visible instead of
                    # assumed - and it is a guard, not an assertion, so it
                    # survives `python -O`.
                    if rules is None:
                        break
                    try:
                        rules = revisit_last_rule_step(media_files, rules, single_file_input=single_file_input)
                    except MenuBack:
                        LOGGER.info("Back requested from first revisited rule step; returning to stream selection")
                        print(warn("Back. Returning to stream selection."))
                        rules = None
                        break

            # Both are set unless Back unwound the loop above, in which case the
            # outer loop starts over at the step the user went back to.
            if output_base is None or rules is None:
                continue

            try:
                output_root = resolve_output_root(input_root, output_base, rules)
            except RuntimeError as exc:
                LOGGER.exception("Could not resolve output root")
                print(err(f"Could not create a safe output root: {exc}"))
                continue

            LOGGER.info("Configured output base: %s", output_base)
            LOGGER.info("Resolved output root: %s", output_root)

            print_header("Confirm Settings")
            print_setting("Input", input_root)
            print_setting("Output base", output_base)
            print_setting("Output root", output_root)
            print_setting("Audio mode", rules.audio_mode)
            print_setting("Audio languages", rules.audio_languages)
            print_setting("Audio titles", rules.audio_titles)
            print_setting("Audio indexes", rules.audio_indexes)
            print_setting("Subtitle mode", rules.subtitle_mode)
            print_setting("Subtitle languages", rules.subtitle_languages)
            print_setting("Subtitle titles", rules.subtitle_titles)
            print_setting("Subtitle indexes", rules.subtitle_indexes)
            print_setting("Metadata edits", rules.metadata_edits)
            print_setting("Audio output order", rules.audio_order)
            print_setting("Subtitle output order", rules.subtitle_order)
            print_setting("Keep attachments", rules.keep_attachments)
            print_setting("Keep metadata", rules.keep_metadata)
            print_setting("Keep chapters", rules.keep_chapters)
            if not single_file_input:
                print_setting("Copy non-video files", rules.copy_non_video_files)
            print_setting("Overwrite", rules.overwrite)
            LOGGER.info("Confirmed rules: %s", rules)

            try:
                start_processing = ask_yes_no("Start processing?", True)
            except MenuBack:
                LOGGER.info("Back requested at confirmation; returning to output folder")
                print(warn("Back. Returning to output folder."))
                continue

            if not start_processing:
                LOGGER.info("User cancelled before processing")
                print(warn("Cancelled."))
                return last_summary

            summary = process_files(media_files, input_root, output_root, rules, scan.failures)
            last_summary = summary

            # Reading the finished files back is the only check that the output
            # carries the streams that were asked for. It costs one ffprobe per
            # file, so it is offered rather than always run, and defaults to no.
            if summary.succeeded and ask_yes_no("Verify the output folder now?", False, allow_back=False):
                verify_output(output_root, rules)

            print_ready_for_next_task()
            restart_input = True
            break

        if not restart_input:
            break

    return last_summary


def main() -> None:
    try:
        main_menu()
    except MenuExit:
        LOGGER.info("User selected exit")
        print(warn("Exited."))
        sys.exit(0)
    except MenuBack:
        LOGGER.info("Back requested at the first menu")
        print(warn("Back is not available here."))
        sys.exit(0)
    except KeyboardInterrupt:
        print()
        LOGGER.info("Cancelled by keyboard interrupt")
        print(warn("Cancelled by user."))
        sys.exit(130)
    except Exception as exc:
        LOGGER.exception("Unhandled error")
        print(err(f"ERROR: {exc}"))
        if logsetup.LOG_FILE:
            print(color(f"Log file: {logsetup.LOG_FILE}", C.LOG_YELLOW))
        sys.exit(1)
