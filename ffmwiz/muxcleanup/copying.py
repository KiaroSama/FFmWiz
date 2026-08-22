# Part of the FFmWiz Stream Cleanup Remux subsystem.
"""File-copy backends used when a video needs no remux and for non-video files.

Windows keeps using robocopy (unbuffered `/J` copies are noticeably faster for
large media files); every other platform uses the stdlib. Both backends report
the same copied/skipped/failed counts, so the caller does not care which ran.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .constants import COPY_CHUNK_BYTES, IS_WINDOWS, PARTIAL_MARKER, ROBOCOPY_BIN, VIDEO_EXTENSIONS
from .colors import err
from .logsetup import LOGGER
from .models import SelectionRules
from .textutil import ProgressPrinter
from .media import operation_timeout_seconds, read_robocopy_percent, run_with_progress
from .output import destination_snapshot, extra_file_sources, partial_path, path_is_under, robocopy_success


def robocopy_available() -> bool:
    return IS_WINDOWS and shutil.which(ROBOCOPY_BIN) is not None


def robocopy_skip_existing_flags() -> List[str]:
    """Excludes that make robocopy leave every existing destination alone. Only
    the no-overwrite tree copy uses robocopy now, so this is all it needs."""
    return ["/XC", "/XN", "/XO"]


def copy_file_with_progress(
    source: Path,
    destination: Path,
    total_started_at: Optional[float] = None,
    timeout: Optional[float] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> None:
    """Chunked stdlib copy with a live elapsed line.

    The bytes go to a temporary file beside the destination and are renamed into
    place only once the copy is complete. A failure, a timeout or a Ctrl+C
    therefore leaves neither a partial file nor a damaged previous output:
    whatever was already at the destination is still there, untouched.
    """
    progress = ProgressPrinter(total_started_at)
    partial = partial_path(destination)
    deadline = None if timeout is None else time.perf_counter() + timeout
    written = 0
    try:
        with source.open("rb") as reader, partial.open("wb") as writer:
            while True:
                chunk = reader.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                writer.write(chunk)
                written += len(chunk)
                if on_progress is not None:
                    on_progress(written)
                if deadline is not None and time.perf_counter() > deadline:
                    raise TimeoutError(f"copy of {source.name} exceeded its {timeout}s timeout")
                progress.tick()
        copy_timestamps(source, partial)
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        progress.close()


def copy_timestamps(source: Path, destination: Path) -> None:
    """Carry over the modification time but not the permission bits. Copying the
    mode would turn a read-only source into a read-only output, which the next
    overwrite run could not replace."""
    try:
        info = source.stat()
        os.utime(destination, ns=(info.st_atime_ns, info.st_mtime_ns))
    except OSError as exc:
        LOGGER.warning("Could not copy timestamps to %s: %s", destination, exc)


def copy_video_without_remux(
    input_file: Path,
    output_file: Path,
    overwrite: bool = False,
    total_started_at: Optional[float] = None,
    timeout: Optional[float] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    on_percent: Optional[Callable[[float], None]] = None,
) -> int:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Robocopy copies a file into a folder under its own name and cannot rename
    # on the way, so a renamed output (single-file mode adds a rule suffix) has
    # to go through the stdlib copy even on Windows.
    if robocopy_available() and input_file.name == output_file.name:
        return copy_video_with_robocopy(input_file, output_file, overwrite,
                                        total_started_at, timeout, on_percent)

    copy_file_with_progress(input_file, output_file, total_started_at, timeout, on_progress)
    if not output_file.exists():
        raise OSError("the copied file is missing after the copy finished")
    return 0


def copy_video_with_robocopy(
    input_file: Path,
    output_file: Path,
    overwrite: bool,
    total_started_at: Optional[float] = None,
    timeout: Optional[float] = None,
    on_percent: Optional[Callable[[float], None]] = None,
) -> int:
    """Copy through a private staging folder, then rename into place.

    Robocopy cannot rename while copying, and it decides for itself whether a
    destination needs copying at all. A destination with the same name, size and
    write time but a different NTFS change time lands in robocopy's "modified"
    class, which its own documentation says is not copied without /IM - /IS and
    /IT do not cover it (measured; see .ai/LESSON.md).

    Staging sidesteps both problems without depending on that classification at
    all: robocopy always writes into an empty folder, so there is nothing to
    skip, and the destination is replaced only once a complete copy exists. A
    failure or a Ctrl+C leaves the old file untouched.
    """
    staging = output_file.parent / f"{PARTIAL_MARKER}-{output_file.stem}"
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)

        args = [
            ROBOCOPY_BIN,
            str(input_file.parent),
            str(staging),
            input_file.name,
            "/R:1",
            "/W:1",
            "/NDL",
            "/NJH",
            "/NJS",
            "/J",
        ]

        # Neither /NP nor /NFL: robocopy's own percentage is the only progress
        # signal this path has, and it is printed as part of the file record -
        # so /NFL silences it just as surely as /NP does. Measured on a 1.6 GB
        # copy: with /NFL the capture stayed empty for the whole 29 s; without
        # it the same copy produced 1550 readings. The output goes to a capture
        # file rather than the console, so it adds no clutter.
        def report(chunk: str) -> None:
            if on_percent is None:
                return
            percent = read_robocopy_percent(chunk)
            if percent is not None:
                on_percent(percent)

        proc = run_with_progress(args, total_started_at, timeout, on_output=report)

        staged = staging / input_file.name
        if not robocopy_success(proc.returncode):
            raise OSError(f"robocopy failed with exit code {proc.returncode}")
        if not staged.exists():
            raise OSError("robocopy did not create the output file")
        if staged.stat().st_size != input_file.stat().st_size:
            raise OSError("robocopy produced an incomplete copy")
        if output_file.exists() and not overwrite:
            raise OSError("output already exists and overwrite is disabled")

        staged.replace(output_file)
        return proc.returncode
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def copy_extra_files(input_root: Path, output_root: Path, rules: SelectionRules) -> Tuple[int, int, int]:
    if input_root.is_file():
        return 0, 0, 0

    sources = extra_file_sources(input_root, output_root)
    if not sources:
        return 0, 0, 0

    # Robocopy only copies what its own classification says is different, and a
    # destination matching on name/size/write-time is not copied without /IM.
    # Overwrite has to be unconditional rather than classification-dependent, so
    # it goes through the stdlib copy, which replaces every destination through a
    # temporary file.
    if robocopy_available() and not rules.overwrite:
        return copy_extra_files_with_robocopy(sources, input_root, output_root, rules)
    return copy_extra_files_with_stdlib(sources, input_root, output_root, rules)


def copy_extra_files_with_stdlib(
    sources: Sequence[Path],
    input_root: Path,
    output_root: Path,
    rules: SelectionRules,
) -> Tuple[int, int, int]:
    copied = 0
    skipped = 0
    failed = 0

    for source in sources:
        try:
            destination = output_root / source.relative_to(input_root)
        except ValueError:
            continue

        if destination.exists() and not rules.overwrite:
            skipped += 1
            continue

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            copy_file_with_progress(source, destination, timeout=operation_timeout_seconds())
            copied += 1
        except OSError as exc:
            LOGGER.error("Could not copy %s -> %s: %s", source, destination, exc)
            failed += 1

    LOGGER.info("Extra files copied with stdlib: copied=%s skipped=%s failed=%s", copied, skipped, failed)
    if failed:
        print(err(f"{failed} non-video file(s) could not be copied."))
    return copied, skipped, failed


def remove_incomplete_copies(
    pairs: Sequence[Tuple[Path, Path]],
    before: Dict[Path, Tuple[int, int]],
) -> None:
    """Delete destinations this run created but did not finish writing.

    A destination that already existed before the run is never touched: it is
    not ours to remove, and it is still the last known-good copy.
    """
    for source, destination in pairs:
        if destination in before or not destination.exists():
            continue
        try:
            if destination.stat().st_size != source.stat().st_size:
                destination.unlink()
                LOGGER.warning("Removed incomplete copy: %s", destination)
        except OSError as exc:
            LOGGER.warning("Could not remove incomplete copy %s: %s", destination, exc)


def copy_extra_files_with_robocopy(
    sources: Sequence[Path],
    input_root: Path,
    output_root: Path,
    rules: SelectionRules,
) -> Tuple[int, int, int]:
    pairs: List[Tuple[Path, Path]] = []
    for source in sources:
        try:
            pairs.append((source, output_root / source.relative_to(input_root)))
        except ValueError:
            # relative_to() raises when a source sits outside input_root, which
            # means it is not ours to copy. Skipping it is the intended answer.
            pass
    destinations = [destination for _, destination in pairs]

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

    args.extend(robocopy_skip_existing_flags())

    # The cancellable runner, not run_command: a tree copy is long enough to need
    # a bound and to be interrupted, and its child must be reaped either way.
    try:
        proc = run_with_progress(args, None, operation_timeout_seconds())
    except BaseException:
        remove_incomplete_copies(pairs, before)
        raise

    # A half-written destination is worse than a missing one; the snapshot below
    # then reports it as failed rather than copied.
    remove_incomplete_copies(pairs, before)
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
            "Extra files copied with robocopy: copied=%s skipped=%s failed=%s rc=%s",
            copied,
            skipped,
            failed,
            proc.returncode,
        )

    return copied, skipped, failed
