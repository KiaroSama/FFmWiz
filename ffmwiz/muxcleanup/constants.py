# Part of the FFmWiz Stream Cleanup Remux subsystem.
from __future__ import annotations

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".webm", ".mov", ".avi"}


FFMPEG_BIN = "ffmpeg"


FFPROBE_BIN = "ffprobe"


ROBOCOPY_BIN = "robocopy"


APP_VERSION = "1.3.1"


AUDIO_BY_LANGUAGE = "1"


AUDIO_BY_TITLE = "2"


AUDIO_BY_INDEX = "3"


AUDIO_ALL = "4"


AUDIO_NONE = "5"


SUBTITLE_ALL = "1"


SUBTITLE_BY_LANGUAGE = "2"


SUBTITLE_BY_TITLE = "3"


SUBTITLE_BY_INDEX = "4"


SUBTITLE_NONE = "5"


EXIT_TOKENS = {"q", "quit", "exit"}


UNKNOWN_LANGUAGE_DISPLAY = "*uknown"


UNKNOWN_LANGUAGE_INPUTS = {"", "und", "unk", "unknown", "undefined", "uknown", "*uknown"}


INVALID_FILENAME_CHARS = '<>:"/\\|?*'
