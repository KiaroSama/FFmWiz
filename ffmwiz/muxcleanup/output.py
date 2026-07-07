# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .constants import AUDIO_ALL, AUDIO_BY_INDEX, AUDIO_BY_LANGUAGE, AUDIO_BY_TITLE, AUDIO_NONE, INVALID_FILENAME_CHARS, SUBTITLE_ALL, SUBTITLE_BY_INDEX, SUBTITLE_BY_LANGUAGE, SUBTITLE_BY_TITLE, SUBTITLE_NONE, VIDEO_EXTENSIONS
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


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    for counter in range(2, 10000):
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not find available output path for: {path}")


def unique_directory_path(path: Path) -> Path:
    if not path.exists():
        return path

    for counter in range(2, 10000):
        candidate = path.with_name(f"{path.name} ({counter})")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not find available output folder for: {path}")


def resolve_output_root(input_root: Path, output_base: Path, rules: SelectionRules) -> Path:
    if input_root.is_dir():
        folder_name = sanitize_filename_part(f"{input_root.name} {selection_suffix(rules)}")
        return unique_directory_path(output_base / folder_name)

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


def path_total_size(path: Path, exclude_paths: Optional[Sequence[Path]] = None) -> int:
    excludes = list(exclude_paths or [])
    total = 0

    if not path.exists():
        return 0

    if path.is_file():
        try:
            return path.stat().st_size
        except OSError as exc:
            LOGGER.warning("Could not read file size for %s: %s", path, exc)
            return 0

    for child in path.rglob("*"):
        if not child.is_file():
            continue
        if any(path_is_under(child, excluded) for excluded in excludes):
            continue
        try:
            total += child.stat().st_size
        except OSError as exc:
            LOGGER.warning("Could not read file size for %s: %s", child, exc)

    return total


def extra_file_sources(input_root: Path, output_root: Path) -> List[Path]:
    sources: List[Path] = []
    for source in sorted(input_root.rglob("*"), key=lambda path: str(path).lower()):
        if not source.is_file():
            continue
        if source.suffix.lower() in VIDEO_EXTENSIONS:
            continue
        if path_is_under(source, output_root):
            continue
        sources.append(source)
    return sources


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
