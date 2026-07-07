# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .constants import AUDIO_NONE, ROBOCOPY_BIN, VIDEO_EXTENSIONS
from .colors import ACTION_SEPARATOR_COLOR, C, PROCESS_DONE_COLOR, PROCESS_SEPARATOR_COLOR, color, dim, err, info, warn
from .logsetup import LOGGER, command_to_text
from .models import MediaFile, SelectionRules
from .textutil import center_for_terminal, format_elapsed_time, format_language_list, format_size_difference, separator_line
from .media import find_video_files, run_command, run_copy_command, run_ffmpeg_command, scan_files
from .muxlogic import build_ffmpeg_command, remux_needed_reasons, selected_audio_streams, selected_subtitle_streams
from .output import destination_snapshot, display_path, extra_file_sources, make_output_path, path_is_under, path_total_size, robocopy_success
from .reporting import print_header

def append_file_result(
    results: List[Dict[str, str]],
    index: int,
    total: int,
    action: str,
    status: str,
    input_file: Path,
    output_file: Optional[Path],
    detail: str,
    started_at: float,
    returncode: Optional[int] = None,
) -> None:
    elapsed = format_elapsed_time(time.perf_counter() - started_at)
    result = {
        "index": f"{index}/{total}",
        "action": action,
        "status": status,
        "input": str(input_file),
        "output": str(output_file) if output_file else "-",
        "detail": detail or "-",
        "returncode": str(returncode) if returncode is not None else "-",
        "elapsed": elapsed,
    }
    results.append(result)

    message = (
        "RESULT %(index)s | status=%(status)s | action=%(action)s | "
        "input=%(input)s | output=%(output)s | detail=%(detail)s | "
        "returncode=%(returncode)s | elapsed=%(elapsed)s"
    ) % result

    if status == "FAILED":
        LOGGER.error(message)
    elif status in {"SKIPPED", "NO_AUDIO_MATCH", "WARNING"}:
        LOGGER.warning(message)
    else:
        LOGGER.info(message)


def log_file_result_summary(results: Sequence[Dict[str, str]]) -> None:
    LOGGER.info("Per-file result summary begin: count=%s", len(results))
    for result in results:
        message = (
            "SUMMARY_RESULT %(index)s | status=%(status)s | action=%(action)s | "
            "input=%(input)s | output=%(output)s | detail=%(detail)s | "
            "returncode=%(returncode)s | elapsed=%(elapsed)s"
        ) % result
        LOGGER.info(message)
    LOGGER.info("Per-file result summary end")


def copy_extra_files(input_root: Path, output_root: Path, rules: SelectionRules) -> Tuple[int, int, int]:
    if input_root.is_file():
        return 0, 0, 0

    sources = extra_file_sources(input_root, output_root)
    if not sources:
        return 0, 0, 0

    if shutil.which(ROBOCOPY_BIN) is None:
        LOGGER.error("robocopy was not found in PATH")
        print(err("robocopy was not found in PATH; non-video files were not copied."))
        return 0, 0, len(sources)

    destinations: List[Path] = []
    for source in sources:
        try:
            destinations.append(output_root / source.relative_to(input_root))
        except ValueError:
            pass

    before = destination_snapshot(destinations)

    args = [
        ROBOCOPY_BIN,
        str(input_root),
        str(output_root),
        "/E",
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
        "/XF",
    ]
    args.extend(f"*{suffix}" for suffix in sorted(VIDEO_EXTENSIONS))

    if path_is_under(output_root, input_root):
        args.extend(["/XD", str(output_root)])

    if not rules.overwrite:
        args.extend(["/XC", "/XN", "/XO"])

    LOGGER.info("Command: %s", command_to_text(args))
    proc = run_command(args)
    if proc.stdout.strip():
        LOGGER.debug("robocopy stdout: %s", proc.stdout.strip())
    if proc.stderr.strip():
        LOGGER.debug("robocopy stderr: %s", proc.stderr.strip())

    after = destination_snapshot(destinations)
    copied = 0
    skipped = 0
    failed = 0

    for destination in destinations:
        before_stat = before.get(destination)
        after_stat = after.get(destination)
        if after_stat is None:
            failed += 1
        elif before_stat is None or after_stat != before_stat:
            copied += 1
        else:
            skipped += 1

    if not robocopy_success(proc.returncode):
        failed = max(failed, 1)
        LOGGER.error("robocopy failed with exit code %s", proc.returncode)
        print(err(f"robocopy failed while copying non-video files. Exit code: {proc.returncode}"))
    else:
        LOGGER.info(
            "robocopy copied extra files: copied=%s skipped=%s failed=%s returncode=%s",
            copied,
            skipped,
            failed,
            proc.returncode,
        )

    return copied, skipped, failed


def copy_video_without_remux(input_file: Path, output_file: Path, progress_started_at: Optional[float] = None) -> int:
    if shutil.which(ROBOCOPY_BIN) is None:
        raise OSError("robocopy was not found in PATH")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    before = destination_snapshot([output_file])

    args = [
        ROBOCOPY_BIN,
        str(input_file.parent),
        str(output_file.parent),
        input_file.name,
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
        "/J",
    ]

    LOGGER.info("Command: %s", command_to_text(args))
    proc = run_copy_command(args, progress_started_at)
    if proc.stdout.strip():
        LOGGER.debug("robocopy stdout: %s", proc.stdout.strip())
    if proc.stderr.strip():
        LOGGER.debug("robocopy stderr: %s", proc.stderr.strip())

    after = destination_snapshot([output_file])
    if not robocopy_success(proc.returncode):
        raise OSError(f"robocopy failed with exit code {proc.returncode}")
    if output_file not in after:
        raise OSError("robocopy did not create the output file")
    if before.get(output_file) == after.get(output_file):
        LOGGER.info("robocopy copied unchanged video but destination metadata did not change: %s", output_file)
    return proc.returncode


def process_files(media_files: List[MediaFile], input_root: Path, output_root: Path, rules: SelectionRules) -> None:
    print_header("Processing Files")
    print()
    started_at = time.perf_counter()
    LOGGER.info("Processing started: input=%s output=%s files=%s", input_root, output_root, len(media_files))
    LOGGER.info("Rules: %s", rules)

    total = len(media_files)
    succeeded = 0
    skipped = 0
    no_audio = 0
    failed = 0
    copied_unchanged = 0
    remuxed = 0
    output_files_for_size: List[Path] = []
    file_results: List[Dict[str, str]] = []

    for i, media in enumerate(media_files, start=1):
        input_file = media.path
        rel = display_path(input_root, input_file)
        if i > 1:
            print(separator_line(PROCESS_SEPARATOR_COLOR))

        try:
            output_file = make_output_path(input_root, output_root, input_file, rules)
        except RuntimeError as exc:
            print(err(f"[{i}/{total}] FAILED: {exc}"))
            LOGGER.exception("Could not resolve output path for %s", input_file)
            failed += 1
            append_file_result(
                file_results,
                i,
                total,
                "resolve-output",
                "FAILED",
                input_file,
                None,
                str(exc),
                started_at,
            )
            continue

        audio_keep = selected_audio_streams(media, rules)
        if rules.audio_mode != AUDIO_NONE and not audio_keep and media.audio_streams:
            print(warn(f"[{i}/{total}] SKIP no matching audio selected: {rel}"))
            LOGGER.warning("No matching audio selected: %s", input_file)
            no_audio += 1
            append_file_result(
                file_results,
                i,
                total,
                "select-audio",
                "NO_AUDIO_MATCH",
                input_file,
                output_file,
                "selected audio rule matched no audio streams",
                started_at,
            )
            continue

        if output_file.exists() and not rules.overwrite:
            print(warn(f"[{i}/{total}] SKIP exists: {rel}"))
            LOGGER.warning("Skip existing output: %s", output_file)
            skipped += 1
            append_file_result(
                file_results,
                i,
                total,
                "skip-existing",
                "SKIPPED",
                input_file,
                output_file,
                "output already exists and overwrite is disabled",
                started_at,
            )
            continue

        subtitles_keep = selected_subtitle_streams(media, rules)
        reasons = remux_needed_reasons(media, rules, audio_keep, subtitles_keep)
        if not reasons:
            print(info(f"[{i}/{total}] Copying unchanged: {rel}"))
            print(dim("          no remux needed"))
            LOGGER.info("Action: copy unchanged | file=%s | reason=no remux needed", input_file)
            try:
                copy_returncode = copy_video_without_remux(input_file, output_file, started_at)
            except OSError as exc:
                failed += 1
                LOGGER.exception("Could not copy unchanged video %s -> %s", input_file, output_file)
                print(err(f"          FAILED to copy unchanged file: {exc}"))
                append_file_result(
                    file_results,
                    i,
                    total,
                    "copy-unchanged",
                    "FAILED",
                    input_file,
                    output_file,
                    str(exc),
                    started_at,
                )
                continue

            succeeded += 1
            copied_unchanged += 1
            output_files_for_size.append(output_file)
            LOGGER.info("Copy unchanged OK: %s -> %s", input_file, output_file)
            append_file_result(
                file_results,
                i,
                total,
                "copy-unchanged",
                "OK",
                input_file,
                output_file,
                "no remux needed",
                started_at,
                copy_returncode,
            )
            print(color(center_for_terminal("Done"), PROCESS_DONE_COLOR))
            continue

        cmd, audio_keep, subtitles_keep = build_ffmpeg_command(input_file, output_file, media, rules)
        LOGGER.info("Action: remux | file=%s | reasons=%s", input_file, "; ".join(reasons))
        LOGGER.info(
            "File %s/%s: %s -> %s | audio_keep=%s subtitle_keep=%s attachments=%s remux_reasons=%s",
            i,
            total,
            input_file,
            output_file,
            [s.index for s in audio_keep],
            [s.index for s in subtitles_keep],
            rules.keep_attachments,
            reasons,
        )

        if not media.video_streams:
            print(warn(f"[{i}/{total}] WARNING: no video stream found: {rel}"))
            LOGGER.warning("No video stream found: %s", input_file)

        print(info(f"[{i}/{total}] Remuxing: {rel}"))
        print(dim(f"          audio kept: {len(audio_keep)} | subtitles kept: {len(subtitles_keep)} | attachments: {'yes' if rules.keep_attachments else 'no'}"))
        LOGGER.info("Command: %s", command_to_text(cmd))

        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            failed += 1
            LOGGER.exception("Could not create output folder for %s", output_file)
            print(err(f"          FAILED to create output folder: {exc}"))
            append_file_result(
                file_results,
                i,
                total,
                "prepare-output",
                "FAILED",
                input_file,
                output_file,
                str(exc),
                started_at,
            )
            continue

        proc = run_ffmpeg_command(cmd, started_at)

        if proc.returncode == 0:
            succeeded += 1
            remuxed += 1
            output_files_for_size.append(output_file)
            LOGGER.info("Remux OK: %s", output_file)
            append_file_result(
                file_results,
                i,
                total,
                "remux",
                "OK",
                input_file,
                output_file,
                "; ".join(reasons),
                started_at,
                proc.returncode,
            )
            print(color(center_for_terminal("Done"), PROCESS_DONE_COLOR))
        else:
            failed += 1
            LOGGER.error("Remux failed: %s | returncode=%s", input_file, proc.returncode)
            print(err("          FAILED"))
            if proc.stderr.strip():
                print(proc.stderr.strip())
            append_file_result(
                file_results,
                i,
                total,
                "remux",
                "FAILED",
                input_file,
                output_file,
                f"ffmpeg return code {proc.returncode}",
                started_at,
                proc.returncode,
            )

    if rules.copy_non_video_files:
        extra_copied, extra_skipped, extra_failed = copy_extra_files(input_root, output_root, rules)
    else:
        extra_copied, extra_skipped, extra_failed = 0, 0, 0
        LOGGER.info("Non-video file copy skipped by user setting")
    elapsed = time.perf_counter() - started_at
    print()
    print(separator_line(C.BOLD + C.DONE_HEADER))
    print(color(center_for_terminal("All Done"), C.BOLD + C.DONE_HEADER))
    print(separator_line(C.BOLD + C.DONE_HEADER))

    if input_root.is_dir():
        original_total_size = path_total_size(input_root, exclude_paths=[output_root])
        output_total_size = path_total_size(output_root)
    else:
        original_total_size = path_total_size(input_root)
        output_total_size = sum(path_total_size(path) for path in output_files_for_size)

    size_delta = output_total_size - original_total_size
    formatted_size_delta = format_size_difference(size_delta)

    print(color(f"Total:   {total}", C.BOLD + C.LAVENDER))
    print(color(f"OK:      {succeeded}", C.BOLD + C.GREEN))
    print(color(f"Remuxed: {remuxed}", C.BOLD + C.AZURE))
    print(color(f"Copied unchanged: {copied_unchanged}", C.BOLD + C.MINT))
    print(color(f"Skipped: {skipped}", C.BOLD + C.AMBER))
    print(color(f"No audio match: {no_audio}", C.BOLD + C.YELLOW))
    print(color(f"Failed:  {failed}", C.BOLD + C.SUMMARY_FAILED))
    print(color(f"Extra files copied:  {extra_copied}", C.BOLD + C.AQUA))
    print(color(f"Extra files skipped: {extra_skipped}", C.BOLD + C.GOLD))
    print(color(f"Extra files failed:  {extra_failed}", C.BOLD + C.SUMMARY_EXTRA_FAILED))
    print(color(f"Output:  {output_root}", C.BOLD + C.SKY))
    print(color(f"Size difference: {formatted_size_delta}", C.BOLD + C.SUMMARY_SIZE_DIFF))
    print(color(f"Elapsed {format_elapsed_time(elapsed)}", C.BOLD + C.SUMMARY_ELAPSED))
    log_file_result_summary(file_results)
    LOGGER.info("Original size: %s bytes", original_total_size)
    LOGGER.info("Output size: %s bytes", output_total_size)
    LOGGER.info("Size difference: %s (%s bytes)", formatted_size_delta, size_delta)
    LOGGER.info(
        "Processing done: total=%s ok=%s remuxed=%s copied_unchanged=%s skipped=%s no_audio_match=%s failed=%s extra_copied=%s extra_skipped=%s extra_failed=%s original_size_bytes=%s output_size_bytes=%s size_difference=%s size_delta_bytes=%s elapsed=%s output=%s",
        total,
        succeeded,
        remuxed,
        copied_unchanged,
        skipped,
        no_audio,
        failed,
        extra_copied,
        extra_skipped,
        extra_failed,
        original_total_size,
        output_total_size,
        formatted_size_delta,
        size_delta,
        format_elapsed_time(elapsed),
        output_root,
    )


def print_ready_for_next_task(message: str = "Task complete. Ready for next task.") -> None:
    print()
    print(separator_line(ACTION_SEPARATOR_COLOR))
    print(color(center_for_terminal(message), PROCESS_DONE_COLOR))
    print(separator_line(ACTION_SEPARATOR_COLOR))
    print()


def verify_output(root: Path, rules: Optional[SelectionRules] = None) -> None:
    print_header("Verify Output Folder")

    files = find_video_files(root)
    if not files:
        print(warn("No video files found."))
        return

    media_files = scan_files(files)

    for media in media_files:
        rel = display_path(root, media.path)

        video_count = len(media.video_streams)
        audio_count = len(media.audio_streams)
        subtitle_count = len(media.subtitle_streams)
        attachment_count = len(media.attachment_streams)

        audio_expected = rules is None or rules.audio_mode != AUDIO_NONE
        status_color = C.GREEN if video_count >= 1 and (audio_count >= 1 or not audio_expected) else C.YELLOW
        audio_langs = format_language_list((s.language for s in media.audio_streams), status_color) if audio_count else "-"
        subtitle_langs = format_language_list((s.language for s in media.subtitle_streams), status_color) if subtitle_count else "-"
        print(
            color(f"{rel} | video={video_count} | audio={audio_count} [", status_color)
            + audio_langs
            + color(f"] | subs={subtitle_count} [", status_color)
            + subtitle_langs
            + color(f"] | attachments={attachment_count}", status_color)
        )
