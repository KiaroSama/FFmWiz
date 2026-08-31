"""FFmWiz file-extension sets (audio-only, folder media, container families).

Pure literal data split out of ffmwiz/core/constants.py to keep that module
under 800 lines. Leaf module: no imports. FOLDER_MEDIA_EXTS is derived here
stays in constants.py instead: it unions these sets with COMMON_VIDEO_FORMATS
and COMMON_AUDIO_FORMATS, which this leaf must not reach up for."""
from __future__ import annotations


AUDIO_ONLY_EXTS = {
    "aac",
    "ac3",
    "aiff",
    "alac",
    "amr",
    "ape",
    "au",
    "dts",
    "eac3",
    "flac",
    "m4a",
    "mka",
    "mp2",
    "mp3",
    "oga",
    "ogg",
    "opus",
    "wav",
    "weba",
    "wma",
}

FOLDER_VIDEO_EXTS = {
    "3g2",
    "3gp",
    "asf",
    "avi",
    "divx",
    "dv",
    "f4v",
    "flv",
    "hevc",
    "m2ts",
    "m2v",
    "m4v",
    "mjpeg",
    "mkv",
    "mov",
    "mp4",
    "mpeg",
    "mpg",
    "mts",
    "mxf",
    "ogm",
    "ogv",
    "rm",
    "rmvb",
    "ts",
    "vob",
    "webm",
    "wmv",
    "y4m",
}

MP4_LIKE_EXTS = {"mp4", "m4a", "m4v", "mov", "ismv"}
ATTACHMENT_COMPATIBLE_EXTS = {"mkv"}
