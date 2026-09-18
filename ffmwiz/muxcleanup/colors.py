# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

import os
import re
import sys

ENABLE_COLORS = True


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    # USER-12-4: every name below that also exists in ffmwiz.core.colors.Color
    # carries Color's value, so entering Stream Cleanup from the wizard menu
    # does not change the colour scheme mid-session. The basic-ANSI originals
    # (94/95/96/90) read visibly duller than the wizard's 256-colour palette.
    BLUE = "\033[38;5;117m"
    MAGENTA = "\033[38;5;219m"
    CYAN = "\033[38;5;123m"
    WHITE = "\033[97m"
    GRAY = "\033[38;5;252m"
    ORANGE = "\033[38;5;222m"
    GOLD = "\033[38;5;220m"
    AMBER = "\033[38;5;214m"
    LIME = "\033[38;5;118m"
    MINT = "\033[38;5;121m"
    EMERALD = "\033[38;5;48m"
    TEAL = "\033[38;5;37m"
    AQUA = "\033[38;5;159m"
    SKY = "\033[38;5;117m"
    AZURE = "\033[38;5;75m"
    INDIGO = "\033[38;5;99m"
    VIOLET = "\033[38;5;135m"
    PURPLE = "\033[38;5;141m"
    LAVENDER = "\033[38;5;183m"
    PINK = "\033[38;5;218m"
    ROSE = "\033[38;5;204m"
    SILVER = "\033[38;5;250m"
    LAUNCHER_PINK = "\033[38;2;255;50;115m"
    LOG_YELLOW = "\033[38;2;255;240;74m"
    BACK_ORANGE = "\033[38;5;166m"
    QUIT_GREEN = "\033[38;5;32m"
    FILE_RED = "\033[38;2;255;20;20m"
    SCAN_HEADER = "\033[38;2;68;221;255m"
    SUMMARY_HEADER = "\033[38;2;170;255;82m"
    VERIFY_HEADER = "\033[38;2;255;115;225m"
    NOTE_BLUE = "\033[38;2;80;190;255m"
    FOUND_LABEL = "\033[38;2;90;255;190m"
    FOUND_VALUE = "\033[38;2;255;235;120m"
    FOUND_DETAIL_VALUE = "\033[38;2;120;220;255m"
    EXAMPLE_COLOR = "\033[38;2;255;215;80m"
    ACTION_SEPARATOR = "\033[38;2;75;130;190m"
    CONFIRM_HEADER = "\033[38;2;255;155;60m"
    PROCESS_HEADER = "\033[38;2;80;255;205m"
    DONE_HEADER = "\033[38;2;145;255;95m"
    PROCESS_SEPARATOR = "\033[38;2;80;150;210m"
    PROCESS_DONE = "\033[38;2;90;255;135m"
    YES_NO_HINT = "\033[38;5;178m"
    UNKNOWN_LANGUAGE = "\033[38;5;244m"
    SETTING_LABEL = "\033[38;2;110;210;255m"
    SETTING_VALUE = "\033[38;2;245;245;245m"
    SETTING_INPUT_PATH = "\033[38;2;70;255;210m"
    SETTING_OUTPUT_BASE = "\033[38;2;255;105;180m"
    SETTING_OUTPUT_ROOT = "\033[38;2;190;255;70m"
    SETTING_MODE = "\033[38;2;180;145;255m"
    SETTING_AUDIO = "\033[38;2;120;255;170m"
    SETTING_SUBTITLE = "\033[38;2;255;150;220m"
    SETTING_TRUE = "\033[38;2;95;255;120m"
    SETTING_FALSE = "\033[38;2;255;95;95m"
    SUMMARY_FAILED = "\033[38;2;255;60;72m"
    SUMMARY_EXTRA_FAILED = "\033[38;2;255;95;120m"
    SUMMARY_SIZE_DIFF = "\033[38;2;0;170;125m"
    SUMMARY_ELAPSED = "\033[38;2;205;122;42m"
    # Progress-view palette, taken value-for-value from EVdlc's shared console
    # palette so the block reads the same in both tools. The unfilled track is
    # deliberately crimson rather than grey - that contrast is what makes the
    # filled portion readable at a glance.
    BAR_FILL = "\033[38;2;0;191;185m"
    BAR_TRACK = "\033[38;2;214;0;68m"
    BAR_FAIL = "\033[38;2;255;79;109m"
    # The five PROGRESS_* names below are shared with Color, so they carry
    # Color's values: percent/size/ETA/elapsed must not change colour between
    # the wizard's FFmpeg progress line and this one. BAR_*, PROGRESS_MUTED,
    # PROGRESS_DONE_WORD and PROGRESS_OVERALL have no counterpart in Color and
    # keep the upstream palette.
    PROGRESS_PERCENT = "\033[38;5;46m"
    PROGRESS_SIZE = "\033[38;5;119m"
    PROGRESS_DONE_WORD = "\033[38;2;57;255;106m"
    PROGRESS_ETA_LABEL = "\033[38;2;255;78;178m"
    PROGRESS_ETA_VALUE = "\033[38;2;255;132;206m"
    PROGRESS_ELAPSED = "\033[38;5;180m"
    PROGRESS_MUTED = "\033[38;2;138;143;163m"
    PROGRESS_OVERALL = "\033[38;2;66;232;255m"


LANGUAGE_COLORS = (
    C.GREEN,
    C.CYAN,
    C.MAGENTA,
    C.YELLOW,
    # C.BLUE is deliberately absent: it now carries the same value as C.SKY
    # below, and two identical entries make two languages indistinguishable.
    C.ORANGE,
    C.GOLD,
    C.LIME,
    C.MINT,
    C.EMERALD,
    C.TEAL,
    C.AQUA,
    C.SKY,
    C.AZURE,
    C.INDIGO,
    C.VIOLET,
    C.PURPLE,
    C.LAVENDER,
    C.PINK,
    C.ROSE,
)


HEADER_COLOR = C.BOLD + C.LAUNCHER_PINK


HEADER_SEPARATOR_COLOR = C.LAUNCHER_PINK


SCAN_SEPARATOR_COLOR = C.BOLD + C.AQUA


FILE_LINE_COLOR = C.BOLD + C.FILE_RED


PROMPT_DEFAULT_COLOR = C.BOLD + C.GREEN


FOUND_LABEL_COLOR = C.BOLD + C.FOUND_LABEL


FOUND_VALUE_COLOR = C.BOLD + C.FOUND_VALUE


FOUND_DETAIL_VALUE_COLOR = C.BOLD + C.FOUND_DETAIL_VALUE


EXAMPLE_TEXT_COLOR = C.EXAMPLE_COLOR


ACTION_SEPARATOR_COLOR = C.BOLD + C.ACTION_SEPARATOR


PROCESS_SEPARATOR_COLOR = C.BOLD + C.PROCESS_SEPARATOR


PROCESS_DONE_COLOR = C.BOLD + C.PROCESS_DONE


YES_NO_HINT_COLOR = C.YES_NO_HINT


UNKNOWN_LANGUAGE_COLOR = C.UNKNOWN_LANGUAGE


SETTING_LABEL_COLOR = C.BOLD + C.SETTING_LABEL


SETTING_VALUE_COLOR = C.SETTING_VALUE


SETTING_INPUT_PATH_COLOR = C.SETTING_INPUT_PATH


SETTING_OUTPUT_BASE_COLOR = C.SETTING_OUTPUT_BASE


SETTING_OUTPUT_ROOT_COLOR = C.SETTING_OUTPUT_ROOT


SETTING_MODE_COLOR = C.BOLD + C.SETTING_MODE


SETTING_AUDIO_COLOR = C.SETTING_AUDIO


SETTING_SUBTITLE_COLOR = C.SETTING_SUBTITLE


SETTING_TRUE_COLOR = C.BOLD + C.SETTING_TRUE


SETTING_FALSE_COLOR = C.BOLD + C.SETTING_FALSE


# Every escape sequence the console output can carry, not just colour: the log
# has to strip cursor moves and clears too, or a progress frame would arrive as
# unreadable control codes.
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def plain(text: object) -> str:
    """The same text with every escape sequence removed.

    Console output is formatted once and then written twice - to the terminal
    with colour, to the log without. Stripping here rather than formatting
    twice is what keeps the two from drifting apart.
    """
    return ANSI_PATTERN.sub("", str(text))


def color(text: object, code: str) -> str:
    if not ENABLE_COLORS:
        return str(text)
    return f"{code}{text}{C.RESET}"


def ok(text: object) -> str:
    return color(text, C.GREEN)


def warn(text: object) -> str:
    return color(text, C.YELLOW)


def err(text: object) -> str:
    return color(text, C.RED)


def info(text: object) -> str:
    return color(text, C.CYAN)


def dim(text: object) -> str:
    return color(text, C.GRAY)


def enable_windows_ansi() -> None:
    # sys.platform rather than os.name, deliberately: a type checker narrows on
    # sys.platform, so everything below is understood as Windows-only code and
    # `ctypes.windll` - which does not exist elsewhere - stops being an error
    # when the project is checked on Linux. Same runtime meaning either way.
    if sys.platform != "win32":
        return

    try:
        import ctypes

        ENABLE_PROCESSED_OUTPUT = 0x0001
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32 = ctypes.windll.kernel32
        enabled = False

        for handle_id in (-11, -12):
            handle = kernel32.GetStdHandle(handle_id)
            if not handle or handle == -1:
                continue

            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                new_mode = mode.value | ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                if kernel32.SetConsoleMode(handle, new_mode):
                    enabled = True

        if not enabled:
            os.system("")
    except Exception:
        # Color is cosmetic. If VT mode cannot be enabled, continue without failing startup.
        try:
            os.system("")
        except Exception:
            pass
        return
