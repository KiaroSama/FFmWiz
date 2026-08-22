# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .constants import AUDIO_ALL, PARTIAL_MARKER, AUDIO_BY_INDEX, AUDIO_BY_LANGUAGE, AUDIO_BY_TITLE, AUDIO_NONE, INVALID_FILENAME_CHARS, SUBTITLE_ALL, SUBTITLE_BY_INDEX, SUBTITLE_BY_LANGUAGE, SUBTITLE_BY_TITLE, SUBTITLE_NONE, VIDEO_EXTENSIONS
from .logsetup import LOGGER
from .models import SelectionRules
from .textutil import compact_labels

def sanitize_filename_part(value: str, fallback: str = "Muxed") -> str:
    cleaned = "".join("-" if ch in INVALID_FILENAME_CHARS else ch for ch in value)
    cleaned = " ".join(cleaned.split()).strip(" .")
    if not any(ch.isalnum() for ch in cleaned):
        return fallback
    return cleaned or fallback


def stream_rule_part(kind: str, mode: str, languages: List[str], titles: List[str], indexes: List[int]) -> str:
    if kind == "audio":
        if mode == AUDIO_BY_LANGUAGE and languages:
            return f"{compact_labels(languages)} Audio"
        if mode == AUDIO_BY_TITLE and titles:
            return "Selected Audio"
        if mode == AUDIO_BY_INDEX and indexes:
            return "Audio " + "+".join(str(index) for index in indexes[:4])
        if mode == AUDIO_ALL:
            return "All Audio"
        if mode == AUDIO_NONE:
            return "No Audio"
        return "No Audio Match"

    if mode == SUBTITLE_NONE:
        return "No Subs"
    if mode == SUBTITLE_BY_LANGUAGE and languages:
        return f"{compact_labels(languages)} Subs"
    if mode == SUBTITLE_BY_TITLE and titles:
        return "Selected Subs"
    if mode == SUBTITLE_BY_INDEX and indexes:
        return "Subs " + "+".join(str(index) for index in indexes[:4])
    if mode == SUBTITLE_ALL:
        return "All Subs"
    return "No Subtitle Match"


def selection_suffix(rules: SelectionRules) -> str:
    parts = [
        stream_rule_part("audio", rules.audio_mode, rules.audio_languages, rules.audio_titles, rules.audio_indexes),
        stream_rule_part("subtitle", rules.subtitle_mode, rules.subtitle_languages, rules.subtitle_titles, rules.subtitle_indexes),
    ]

    compact: List[str] = []
    for part in parts:
        if part and part not in compact:
            compact.append(part)

    suffix = " + ".join(compact) if compact else "Muxed"
    return f"[{sanitize_filename_part(suffix)}]"


def partial_path(final: Path) -> Path:
    """Sibling name used while a file is still being written. The real extension
    stays last so FFmpeg can still infer the output container from it."""
    return final.with_name(f"{final.stem}{PARTIAL_MARKER}{final.suffix}")


def unique_path(path: Path, is_directory: bool = False) -> Path:
    """`name (2).mkv`, `name (3).mkv`, ... until one is free.

    A folder numbers its whole name; a file numbers the stem so the extension
    stays last.
    """
    if not path.exists():
        return path

    stem, suffix = (path.name, "") if is_directory else (path.stem, path.suffix)
    for counter in range(2, 10000):
        candidate = path.with_name(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate

    kind = "folder" if is_directory else "path"
    raise RuntimeError(f"Could not find available output {kind} for: {path}")


def unique_directory_path(path: Path) -> Path:
    return unique_path(path, is_directory=True)


def output_base_conflict(input_root: Path, output_base: Path) -> Optional[str]:
    """Return why this output base is unusable, or None when it is safe.

    A folder run walks its input recursively, so writing anywhere inside that
    input turns this run's output into the next run's input. Single-file runs
    are unaffected: they only touch the one file they were given, so writing
    beside it stays allowed.
    """
    if not input_root.is_dir():
        return None

    try:
        source = input_root.resolve()
        base = output_base.resolve()
    except OSError:
        return None

    # Path comparison is case-insensitive on Windows and case-sensitive on
    # POSIX, which is what each filesystem actually means by "the same folder".
    if base == source:
        return "The output folder cannot be the input folder itself."
    if path_is_under(base, source):
        return "The output folder cannot be inside the input folder."
    return None


def resolve_output_root(input_root: Path, output_base: Path, rules: SelectionRules) -> Path:
    conflict = output_base_conflict(input_root, output_base)
    if conflict:
        raise RuntimeError(conflict)

    if input_root.is_dir():
        folder_name = sanitize_filename_part(f"{input_root.name} {selection_suffix(rules)}")
        target = output_base / folder_name
        # Overwrite means "use the folder I asked for"; otherwise never touch an
        # existing output folder and pick the next free name.
        return target if rules.overwrite else unique_directory_path(target)

    return output_base


def make_output_path(input_root: Path, output_root: Path, input_file: Path, rules: SelectionRules) -> Path:
    if input_root.is_file():
        suffix = selection_suffix(rules)
        filename = sanitize_filename_part(f"{input_file.stem} {suffix}") + input_file.suffix
        output_file = output_root / filename
    else:
        rel = input_file.relative_to(input_root)
        output_file = output_root / rel

    try:
        if output_file.resolve() == input_file.resolve():
            output_file = unique_path(output_file)
    except OSError:
        # An unresolvable path cannot be compared, and refusing to guess is
        # right here: make_output_path's caller already handles a name clash.
        pass

    if output_file.exists() and not rules.overwrite:
        output_file = unique_path(output_file)

    return output_file


def display_path(input_root: Path, input_file: Path) -> Path:
    if input_root.is_file():
        return Path(input_file.name)

    try:
        return input_file.relative_to(input_root)
    except ValueError:
        return input_file


def path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def resolved_roots(paths: Optional[Sequence[Path]]) -> List[Path]:
    """Resolve each root once, for walks that then compare against them.

    Resolving inside the per-file loop instead is what made the end-of-run size
    accounting cost several seconds on a large library: `path_is_under` resolves
    *both* sides on every call, so the excluded root was re-resolved once per
    file in the tree.
    """
    roots: List[Path] = []
    for path in paths or []:
        try:
            roots.append(path.resolve())
        except OSError:
            roots.append(path.absolute())
    return roots


def walk_files(root: Path, skip_roots: Sequence[Path]) -> Iterable[Path]:
    """Every file under `root`, never descending into one of `skip_roots`.

    Pruning at the directory boundary is the point: an excluded subtree is
    stepped over once rather than tested once per file inside it.
    """
    skip = set(skip_roots)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        if skip:
            kept = []
            for name in dirnames:
                try:
                    resolved = (here / name).resolve()
                except OSError:
                    resolved = (here / name).absolute()
                if resolved not in skip:
                    kept.append(name)
            dirnames[:] = kept
        for name in filenames:
            yield here / name


def path_total_size(path: Path, exclude_paths: Optional[Sequence[Path]] = None) -> int:
    total = 0

    if not path.exists():
        return 0

    if path.is_file():
        try:
            return path.stat().st_size
        except OSError as exc:
            LOGGER.warning("Could not read file size for %s: %s", path, exc)
            return 0

    for child in walk_files(path, resolved_roots(exclude_paths)):
        try:
            total += child.stat().st_size
        except OSError as exc:
            LOGGER.warning("Could not read file size for %s: %s", child, exc)

    return total


def extra_file_sources(input_root: Path, output_root: Path) -> List[Path]:
    # Same pruning as path_total_size: skip the output tree at its root rather
    # than asking "is this file under it?" once per file.
    sources = [
        source
        for source in walk_files(input_root, resolved_roots([output_root]))
        if source.suffix.lower() not in VIDEO_EXTENSIONS
    ]
    return sorted(sources, key=lambda path: str(path).lower())


def destination_snapshot(paths: Iterable[Path]) -> Dict[Path, Tuple[int, int]]:
    snapshot: Dict[Path, Tuple[int, int]] = {}
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[path] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def robocopy_success(returncode: int) -> bool:
    return 0 <= returncode <= 7
