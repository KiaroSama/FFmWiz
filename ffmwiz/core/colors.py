"""ANSI color palette for FFmWiz terminal output.

Leaf module extracted verbatim from FFmWiz.py; depends on nothing.
The mutable USE_COLOR flag stays in FFmWiz.py so tests that set
`FFmWiz.USE_COLOR` keep controlling colored output.
"""
from __future__ import annotations


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[38;5;117m"
    MAGENTA = "\033[38;5;219m"
    CYAN = "\033[38;5;123m"
    WHITE = "\033[97m"
    DIM = "\033[38;5;250m"
    GRAY = "\033[38;5;252m"
    ORANGE = "\033[38;5;222m"
    LIGHT_BLUE = "\033[38;5;117m"
    LIGHT_YELLOW = "\033[38;5;229m"
    NOTE_YELLOW = "\033[38;5;227m"
    HINT_YELLOW = "\033[38;5;221m"
    AQUA = "\033[38;5;159m"
    PINK = "\033[38;5;218m"
    LIME = "\033[38;5;118m"
    KEEP_VALUE = "\033[38;5;207m"
    RES_NUMBERS = "\033[38;5;165m"
    RES_TARGET = "\033[38;5;204m"
    RES_EXACT = "\033[38;5;141m"
    # Distinct prompt option-key colors, chosen for high contrast against the
    # surrounding prompt text. Two of the three are shared with another token
    # (OPT_KEY_CYAN with AUDIO_TRACK_NOTE, OPT_KEY_CORAL with MUX_CORAL).
    OPT_KEY_CYAN = "\033[38;5;87m"     # Bright cyan-turquoise for bitrate/CRF keys.
    OPT_KEY_CHARTREUSE = "\033[38;5;154m"  # Bright yellow-green for y/n keys.
    OPT_KEY_CORAL = "\033[38;5;209m"   # Bright coral-orange for 0/1/2 keys.
    AUDIO_ALL = "\033[38;5;120m"
    AUDIO_DROP_DUP = "\033[38;5;208m"
    AUDIO_DROP_EMPTY = "\033[38;5;198m"
    AUDIO_DROP_BOTH = "\033[38;5;99m"
    AUDIO_TRACK_NOTE = "\033[38;5;87m"
    FINAL_COMMAND_LABEL = "\033[38;5;75m"
    FINAL_COMMAND_TEXT = "\033[38;5;153m"
    SUGGESTION = "\033[38;5;190m"
    BACK_PROMPT = "\033[38;5;166m"
    EXIT_PROMPT = "\033[38;5;32m"
    FOLDER_PROMPT = "\033[38;2;180;140;255m"  # light violet for the join-folder option
    NEAR_EMPTY = "\033[38;5;172m"
    ZERO_INLINE = "\033[38;5;177m"
    PROGRESS_PERCENT = "\033[38;5;46m"
    PROGRESS_TIME = "\033[38;5;51m"
    PROGRESS_FPS = "\033[38;5;226m"
    PROGRESS_Q = "\033[38;5;202m"
    PROGRESS_SPEED = "\033[38;5;171m"
    PROGRESS_SIZE = "\033[38;5;119m"
    PROGRESS_BITRATE = "\033[38;5;39m"
    PROGRESS_ELAPSED = "\033[38;5;180m"
    PROGRESS_ETA_LABEL = "\033[38;2;255;78;178m"
    PROGRESS_ETA_VALUE = "\033[38;2;255;132;206m"
    COLOR_RANGE_VALUE = "\033[38;2;90;210;255m"
    MAX_VOLUME = "\033[38;2;255;142;86m"
    MEAN_VOLUME = "\033[38;2;132;220;255m"
    CHAPTERS_YES = "\033[38;2;119;255;163m"
    CHAPTERS_NO = "\033[38;2;255;198;92m"
    UNIFIED_CAP_CROP = "\033[38;2;118;213;255m"
    UNIFIED_CAP_CUTS = "\033[38;2;255;122;122m"
    UNIFIED_CAP_SPEED = "\033[38;2;210;156;255m"
    UNIFIED_CAP_WAVEFORM = "\033[38;2;118;255;191m"
    WIZARD_TITLE = "\033[38;2;255;50;115m"
    MUX_GOLD = "\033[38;5;220m"
    MUX_AMBER = "\033[38;5;214m"
    MUX_MINT = "\033[38;5;121m"
    MUX_EMERALD = "\033[38;5;48m"
    MUX_TEAL = "\033[38;5;37m"
    MUX_AQUA = "\033[38;5;51m"
    MUX_SKY = "\033[38;5;117m"
    MUX_AZURE = "\033[38;5;75m"
    MUX_INDIGO = "\033[38;5;99m"
    MUX_VIOLET = "\033[38;5;135m"
    MUX_PURPLE = "\033[38;5;141m"
    MUX_LAVENDER = "\033[38;5;183m"
    MUX_ROSE = "\033[38;5;204m"
    # Compact Join input summary (min/max bitrate, fps, file count).
    JOIN_SUMMARY = "\033[38;5;111m"
    # Join summary value styling: highest vs lowest must use distinct colors.
    JOIN_LABEL = "\033[38;5;81m"        # cyan-ish labels (Video bitrate, FPS, ...)
    JOIN_HIGH = "\033[38;5;82m"         # bright green for "highest" values
    JOIN_LOW = "\033[38;5;214m"         # amber-orange for "lowest" values
    JOIN_FILE = "\033[38;5;147m"        # soft violet for file names
    JOIN_COUNT = "\033[38;5;123m"       # cyan for the file count
    JOIN_DURATION = "\033[38;5;120m"    # mint for total raw duration
    JOIN_FRAMES = "\033[38;5;180m"      # tan for approximate frame count
    JOIN_VOL_LOW = "\033[38;5;39m"      # blue for lowest mean volume
    JOIN_VOL_HIGH = "\033[38;5;203m"    # coral-red for highest max volume
    MUX_CORAL = "\033[38;5;209m"
    MUX_SALMON = "\033[38;5;210m"
    MUX_STEEL = "\033[38;5;110m"
    MUX_SILVER = "\033[38;5;250m"
    # Audio sample-rate (Hz) value styling, distinct from bitrate/volume colors.
    AUDIO_SAMPLE_RATE = "\033[38;5;43m"
    MUX_HEADER = "\033[1m\033[38;2;255;50;115m"
    MUX_SCAN_HEADER = "\033[1m\033[38;2;68;221;255m"
    MUX_SUMMARY_HEADER = "\033[1m\033[38;2;170;255;82m"
    MUX_VERIFY_HEADER = "\033[1m\033[38;2;255;115;225m"
    MUX_CONFIRM_HEADER = "\033[1m\033[38;2;255;155;60m"
    MUX_PROCESS_HEADER = "\033[1m\033[38;2;80;255;205m"
    MUX_DONE_HEADER = "\033[1m\033[38;2;145;255;95m"
    MUX_SEPARATOR = "\033[1m\033[38;2;75;130;190m"
    MUX_FILE_LINE = "\033[1m\033[38;2;255;20;20m"
    MUX_SETTING_LABEL = "\033[1m\033[38;2;110;210;255m"
    MUX_SETTING_VALUE = "\033[38;2;245;245;245m"
    MUX_INPUT_PATH = "\033[38;2;70;255;210m"
    MUX_OUTPUT_BASE = "\033[38;2;255;105;180m"
    MUX_OUTPUT_ROOT = "\033[38;2;190;255;70m"
    MUX_MODE = "\033[1m\033[38;2;180;145;255m"
    MUX_AUDIO = "\033[38;2;120;255;170m"
    MUX_SUBTITLE = "\033[38;2;255;150;220m"
    MUX_TRUE = "\033[1m\033[38;2;95;255;120m"
    MUX_FALSE = "\033[1m\033[38;2;255;95;95m"
    MUX_UNKNOWN_LANGUAGE = "\033[38;5;244m"
    MUX_SIZE_DIFF = "\033[38;2;0;170;125m"
    MUX_ELAPSED = "\033[38;2;205;122;42m"



__all__ = ["Color"]
