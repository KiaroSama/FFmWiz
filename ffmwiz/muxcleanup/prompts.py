# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .constants import EXIT_TOKENS, VIDEO_EXTENSIONS
from .colors import C, PROMPT_DEFAULT_COLOR, YES_NO_HINT_COLOR, color, dim, err, info, warn
from .textutil import color_example_text, color_found_text, format_index_list, format_prompt_label, normalize_language_code, parse_csv_int, parse_csv_text

class MenuExit(Exception):
    pass


class MenuBack(Exception):
    pass


def option_suffix(default: Optional[str], allow_back: bool, show_default: bool = True) -> str:
    parts: List[str] = []
    if default and show_default:
        parts.append(f"[{default}]")

    nav_parts: List[str] = []
    nav_parts.append("quit=exit")
    if allow_back:
        nav_parts.append("back=0")
    parts.append(f"{{{', '.join(nav_parts)}}}")

    return " ".join(parts)


def colored_option_suffix(default: Optional[str], allow_back: bool, show_default: bool = True) -> str:
    parts: List[str] = []
    if default and show_default:
        parts.append(color(f"[{default}]", PROMPT_DEFAULT_COLOR))

    nav_parts = [color("quit=exit", C.QUIT_GREEN)]
    if allow_back:
        nav_parts.append(color("back=0", C.BACK_ORANGE))
    parts.append("{" + ", ".join(nav_parts) + "}")

    return " ".join(parts)


def prompt_text(prompt: str, default: Optional[str], allow_back: bool, show_default: bool = True) -> str:
    label = format_prompt_label(prompt)
    suffix = colored_option_suffix(default, allow_back, show_default=show_default)
    return f"{label} {suffix}{color(':', C.GRAY)} "


def read_rendered_input(rendered_prompt: str, default: Optional[str], allow_back: bool) -> str:
    while True:
        raw = input(rendered_prompt).strip()
        lowered = raw.lower()

        if lowered in EXIT_TOKENS:
            raise MenuExit

        if raw == "0":
            if allow_back:
                raise MenuBack
            print(warn("Back is not available here."))
            continue

        if not raw and default is not None:
            return default

        return raw


def read_menu_input(
    prompt: str,
    default: Optional[str] = None,
    allow_back: bool = True,
    show_default: bool = True,
) -> str:
    return read_rendered_input(prompt_text(prompt, default, allow_back, show_default), default, allow_back)


def ask_text(prompt: str, allow_back: bool = True) -> str:
    while True:
        raw = read_menu_input(prompt, allow_back=allow_back)
        if raw:
            return raw
        print(err("Value cannot be empty."))


def ask_path(prompt: str, must_exist: bool = False, allow_back: bool = True) -> Path:
    while True:
        raw = read_menu_input(prompt, allow_back=allow_back)
        if not raw:
            print(err("Path cannot be empty."))
            continue

        path = normalize_path_text(raw)

        if must_exist and not path.exists():
            print(err(f"Path does not exist: {path}"))
            continue

        return path


def normalize_path_text(raw: str) -> Path:
    return Path(raw.strip().strip('"').strip("'")).expanduser()


def absolute_path_for_display(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        if path.is_absolute():
            return path
        return (Path.cwd() / path).absolute()


def input_path_from_args(args: Sequence[str]) -> Optional[Path]:
    if not args:
        return None

    # The launchers pass the dropped file or folder as the first argument.
    raw = args[0].strip()
    if not raw:
        return None

    return normalize_path_text(raw)


def ask_output_base_path(input_root: Path) -> Path:
    default_base = input_root.parent
    while True:
        raw = read_menu_input("Output folder path [Enter=input parent folder]", allow_back=True)
        if not raw:
            print(info(f"Using input parent folder: {absolute_path_for_display(default_base)}"))
            return default_base

        if raw.lower() in {"y", "yes", "n", "no", "y/n", "yes/no", "n/y", "no/yes"}:
            print(warn("Please enter a folder path, or press Enter to use the input parent folder."))
            continue

        path = normalize_path_text(raw)
        if path.exists() and not path.is_dir():
            print(err(f"Output path exists but is not a folder: {path}"))
            continue

        if path.suffix.lower() in VIDEO_EXTENSIONS:
            print(err("Output path must be a folder, not a media file name."))
            continue

        if path.suffix and not path.exists():
            print(warn(f"This output folder name has an extension: {path.name}"))
            try:
                use_extension_path = ask_yes_no("Use this as a folder path?", False)
            except MenuBack:
                print(warn("Back. Returning to output folder path."))
                continue
            if not use_extension_path:
                continue

        if not path.is_absolute():
            resolved = absolute_path_for_display(path)
            print(warn(f"Relative output folder will resolve to: {resolved}"))
            try:
                use_relative_path = ask_yes_no("Use this relative output folder?", False)
            except MenuBack:
                print(warn("Back. Returning to output folder path."))
                continue
            if not use_relative_path:
                continue
            return resolved

        return path


def yes_no_choice_suffix(default: bool) -> str:
    if default:
        return f"{color('(y/n)', YES_NO_HINT_COLOR)} {color('[Y]', PROMPT_DEFAULT_COLOR)}"
    return f"{color('(y/n)', YES_NO_HINT_COLOR)} {color('[n]', PROMPT_DEFAULT_COLOR)}"


def ask_yes_no(prompt: str, default: bool = True, allow_back: bool = True) -> bool:
    while True:
        rendered_prompt = (
            f"{prompt} {yes_no_choice_suffix(default)} "
            f"{colored_option_suffix(None, allow_back, show_default=False)}{color(':', C.GRAY)} "
        )
        raw = read_rendered_input(rendered_prompt, default=None, allow_back=allow_back).lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print(warn("Please enter y or n, or press Enter for the default."))


def print_metadata_note() -> None:
    print("Metadata note:")
    print(dim("  Keeping metadata preserves supported titles, language tags, chapters, and stream labels."))


def ask_choice(prompt: str, valid: Iterable[str], default: str, allow_back: bool = True) -> str:
    valid_set = {v.lower() for v in valid}
    while True:
        raw = read_menu_input(prompt, default=default, allow_back=allow_back).lower()
        if raw in valid_set:
            return raw
        print(warn(f"Invalid choice. Valid options: {', '.join(sorted(valid_set))}"))


def numbered_option(value: str, text: str, is_default: bool) -> str:
    suffix = f" {color('(default)', PROMPT_DEFAULT_COLOR)}" if is_default else ""
    return f"{color(value + '.', C.BOLD + C.GREEN)} {color_example_text(color_found_text(text))}{suffix}"


def numbered_choice_prompt(prompt: str, allow_back: bool, colon_after_prompt: bool) -> str:
    suffix = colored_option_suffix(None, allow_back, show_default=False)
    if colon_after_prompt:
        return f"{prompt}: {suffix}{color(':', C.GRAY)} "
    return f"{prompt} {suffix}{color(':', C.GRAY)} "


def ask_numbered_menu(
    title: str,
    options: Sequence[Tuple[str, str]],
    default: str,
    prompt: str,
    allow_back: bool = True,
    leading_blank: bool = True,
    colon_after_prompt: bool = False,
    notes: Optional[Sequence[str]] = None,
) -> str:
    valid_set = {value.lower() for value, _ in options}

    if leading_blank:
        print()
    print(f"{title}:")
    for note in notes or ():
        print(format_prompt_label(note))
    for value, text in options:
        print(numbered_option(value, text, value == default))

    rendered_prompt = numbered_choice_prompt(prompt, allow_back, colon_after_prompt)
    while True:
        raw = read_rendered_input(rendered_prompt, default=default, allow_back=allow_back).lower()
        if raw in valid_set:
            return raw
        print(warn(f"Invalid choice. Valid options: {', '.join(sorted(valid_set))}"))


def ask_csv_text_required(prompt: str) -> List[str]:
    while True:
        values = parse_csv_text(ask_text(prompt))
        if values:
            return values
        print(warn("Please enter at least one value."))


def ask_language_codes_required(prompt: str) -> List[str]:
    return [normalize_language_code(value) for value in ask_csv_text_required(prompt)]


def ask_csv_int_required(prompt: str, available_indexes: Optional[List[int]] = None) -> List[int]:
    while True:
        indexes = parse_csv_int(ask_text(prompt))
        if not indexes:
            print(warn("Please enter at least one stream index."))
            continue

        if available_indexes is not None:
            unknown_indexes = sorted(set(indexes) - set(available_indexes))
            if unknown_indexes:
                print(warn(f"These indexes were not found in the scan: {format_index_list(unknown_indexes)}"))
                try:
                    keep_unknown = ask_yes_no("Keep these indexes anyway?", False)
                except MenuBack:
                    print(warn("Back. Returning to stream index entry."))
                    continue
                if not keep_unknown:
                    continue

        return indexes


def ask_language_code(prompt: str) -> str:
    return normalize_language_code(ask_text(prompt))
