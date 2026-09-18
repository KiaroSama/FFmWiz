# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

import shutil
import sys
import time
from collections.abc import Iterable, Sequence

from .colors import (
    EXAMPLE_TEXT_COLOR,
    FOUND_DETAIL_VALUE_COLOR,
    FOUND_LABEL_COLOR,
    FOUND_VALUE_COLOR,
    HEADER_SEPARATOR_COLOR,
    LANGUAGE_COLORS,
    PROMPT_DEFAULT_COLOR,
    UNKNOWN_LANGUAGE_COLOR,
    C,
    color,
    warn,
)
from .constants import UNKNOWN_LANGUAGE_DISPLAY, UNKNOWN_LANGUAGE_INPUTS
from .logsetup import LOGGER
from .models import StreamInfo


def terminal_width() -> int:
    return max(20, shutil.get_terminal_size((80, 20)).columns)


def separator_line(code: str = HEADER_SEPARATOR_COLOR) -> str:
    return color("=" * terminal_width(), code)


def center_for_terminal(text: str) -> str:
    return text.center(terminal_width())


def prompt_label(prompt: str) -> str:
    return prompt.strip().rstrip(":").strip()


def normalize_language_code(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized in UNKNOWN_LANGUAGE_INPUTS:
        return "und"
    return normalized


def is_unknown_language(value: str) -> bool:
    return normalize_language_code(value) == "und"


def display_language(value: str) -> str:
    if is_unknown_language(value):
        return UNKNOWN_LANGUAGE_DISPLAY
    return value or UNKNOWN_LANGUAGE_DISPLAY


def color_language_value(value: str, fallback_color: str = FOUND_VALUE_COLOR) -> str:
    if is_unknown_language(value):
        return color(display_language(value), UNKNOWN_LANGUAGE_COLOR)
    return color(display_language(value), fallback_color)


def format_language_list(values: Iterable[str], fallback_color: str = FOUND_VALUE_COLOR) -> str:
    rendered = [color_language_value(value, fallback_color) for value in values]
    return ", ".join(rendered) if rendered else "none"


def format_language_list_from_text(text: str, fallback_color: str = FOUND_VALUE_COLOR) -> str:
    values = [value.strip() for value in text.split(",") if value.strip()]
    return format_language_list(values, fallback_color)


def color_found_text(text: str) -> str:
    marker = "Found: "
    index = text.find(marker)
    if index == -1:
        return text
    start = index
    value_start = start + len(marker)
    return (
        text[:start]
        + color(marker.rstrip(), FOUND_LABEL_COLOR)
        + " "
        + format_language_list_from_text(text[value_start:], FOUND_VALUE_COLOR)
    )


def color_found_detail_text(text: str) -> str:
    marker = " found: "
    index = text.lower().find(marker)
    if index == -1:
        return text
    label_end = index + len(marker)
    return text[:label_end] + format_language_list_from_text(text[label_end:], FOUND_DETAIL_VALUE_COLOR)


def color_example_text(text: str) -> str:
    start = text.find("(example")
    if start == -1:
        return text
    end = text.find(")", start)
    if end == -1:
        return text
    end += 1
    return text[:start] + color(text[start:end], EXAMPLE_TEXT_COLOR) + text[end:]


def color_enter_hint_text(text: str) -> str:
    """Paint a `[Enter=...]` hint the same green as `[Y]` and `[n]`.

    It is a default value like any other - it just spells out what pressing
    Enter does instead of showing the value itself - so it reads as one of the
    navigation hints rather than part of the question.
    """
    start = text.find("[Enter=")
    if start == -1:
        return text
    end = text.find("]", start)
    if end == -1:
        return text
    end += 1
    return text[:start] + color(text[start:end], PROMPT_DEFAULT_COLOR) + text[end:]


def format_prompt_label(prompt: str) -> str:
    return color_enter_hint_text(
        color_example_text(color_found_detail_text(color_found_text(prompt_label(prompt))))
    )


def parse_csv_text(raw: str) -> list[str]:
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def parse_csv_int(raw: str) -> list[int]:
    result: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError:
            print(warn(f"Ignored invalid number: {part}"))
    return result


def format_elapsed_time(seconds: float) -> str:
    if seconds < 0:
        LOGGER.warning("Negative elapsed time received: %s", seconds)
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


# Exactly one thing can own the cursor. The live progress block takes it for the
# length of a run; the per-command timer below has to stay quiet while it does.
# A line printed underneath the block scrolls the terminal by one, and the
# block's next repaint - which moves up by the number of lines it drew last
# time - then lands one line short and strands its top row on screen.
_BLOCK_OWNS_SCREEN = False


def set_block_owns_screen(owned: bool) -> None:
    global _BLOCK_OWNS_SCREEN
    _BLOCK_OWNS_SCREEN = owned


def block_owns_screen() -> bool:
    return _BLOCK_OWNS_SCREEN


class ProgressPrinter:
    """One rewritable console line showing how long the current file has been
    running, next to how long the whole run has been going. Each file gets its
    own printer, so its timer starts at zero and stops when the file is done."""

    def __init__(self, total_started_at: float | None = None) -> None:
        self.started_at = time.perf_counter()
        self.total_started_at = total_started_at
        self._last_second = -1
        self._line_open = False
        # Redirected output has no cursor to rewrite, so \r would pile every
        # tick into the file instead of replacing the previous one.
        self._enabled = sys.stdout.isatty()

    def tick(self, force: bool = False) -> None:
        if not self._enabled or block_owns_screen():
            return
        elapsed = int(time.perf_counter() - self.started_at)
        if elapsed == self._last_second and not force:
            return
        self._last_second = elapsed

        text = f"          Elapsed {format_elapsed_time(elapsed)}"
        if self.total_started_at is not None:
            total = time.perf_counter() - self.total_started_at
            text += f" | Total {format_elapsed_time(total)}"

        if not self._line_open:
            sys.stdout.write("\n")
            self._line_open = True
        sys.stdout.write("\r" + color(text, C.BOLD + C.SUMMARY_ELAPSED))
        sys.stdout.flush()

    def close(self) -> None:
        """Erase the progress line. Safe to call more than once."""
        if not self._enabled or block_owns_screen():
            return
        blank = "\r" + (" " * terminal_width()) + "\r"
        sys.stdout.write(blank + "\n" if self._line_open else blank)
        sys.stdout.flush()
        self._line_open = False


def format_stream_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "-"
    if size_bytes < 0:
        return "-"

    kb = size_bytes / 1024
    mb = kb / 1024
    gb = mb / 1024

    if gb >= 1:
        return f"{gb:.1f} GB"
    if mb >= 1:
        return f"{mb:.1f} MB"
    return f"{kb:.0f} KB"


def format_size_difference(size_bytes: int) -> str:
    sign = "+" if size_bytes > 0 else "-" if size_bytes < 0 else ""
    absolute = abs(size_bytes)
    kb = absolute / 1024
    mb = kb / 1024
    gb = mb / 1024

    if mb < 5:
        return f"{sign}{kb:.2f} KB"
    if gb >= 1:
        return f"{sign}{gb:.2f} GB"
    return f"{sign}{mb:.2f} MB"


def format_stream(stream: StreamInfo) -> str:
    title = stream.title if stream.title else "-"
    lang = stream.language if stream.language else "und"
    display_lang = display_language(lang)
    codec = stream.codec_name if stream.codec_name else "-"
    channels = str(stream.channels) if stream.channels is not None else "-"
    default = "yes" if stream.disposition_default else "no"

    index_part = color(f"index={stream.index}", C.BOLD + C.GOLD)
    lang_part = color(f"lang={display_lang}", language_color(lang))
    title_part = color(f"title={title}", C.SKY)
    codec_part = color(f"codec={codec}", C.MINT)
    default_part = color(f"default={default}", C.BOLD + C.GREEN if default == "yes" else C.GRAY)
    size_part = color(f"size={format_stream_size(stream.size_bytes)}", C.SILVER)

    if stream.codec_type == "audio":
        type_part = color("type=audio", C.BOLD + C.AZURE)
        channels_part = color(f"channels={channels}", C.ORANGE)
        return " | ".join((index_part, type_part, lang_part, title_part, codec_part, channels_part, default_part, size_part))

    if stream.codec_type == "subtitle":
        type_part = color("type=subtitle", C.BOLD + C.VIOLET)
        return " | ".join((index_part, type_part, lang_part, title_part, codec_part, default_part, size_part))

    type_part = color(f"type={stream.codec_type}", C.GRAY)
    return " | ".join((index_part, type_part, lang_part, title_part, codec_part))


def language_color(language: str) -> str:
    normalized = normalize_language_code(language)
    if normalized == "und":
        return UNKNOWN_LANGUAGE_COLOR
    return LANGUAGE_COLORS[sum(ord(ch) for ch in normalized) % len(LANGUAGE_COLORS)]


def format_index_list(indexes: list[int]) -> str:
    if not indexes:
        return "none"
    return ", ".join(str(index) for index in indexes)


def format_text_list(values: list[str]) -> str:
    if not values:
        return "none"
    return ", ".join(display_language(value) for value in values)


def language_label(value: str) -> str:
    normalized = normalize_language_code(value)
    labels = {
        "und": "UKNOWN",
        "ja": "JA",
        "jpn": "JA",
        "japanese": "JA",
        "en": "EN",
        "eng": "EN",
        "english": "EN",
        "fa": "FA",
        "fas": "FA",
        "per": "FA",
        "persian": "FA",
    }
    return labels.get(normalized, normalized.upper())


def compact_labels(values: Sequence[str], max_items: int = 3) -> str:
    unique = []
    for value in values:
        label = language_label(value)
        if label not in unique:
            unique.append(label)
    if not unique:
        return ""
    if len(unique) > max_items:
        return "+".join(unique[:max_items]) + "+..."
    return "+".join(unique)
