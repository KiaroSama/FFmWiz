"""FFmWiz codec/extension lookup tables (extract + lossless-copy).

Pure literal data split out of ffmwiz/core/constants.py to keep that module
under 800 lines. Leaf module: no imports. Re-exported by constants.py, so the
public API (FFmWiz.EXTRACT_AUDIO_EXTENSIONS, ...) is unchanged.
"""
from __future__ import annotations


LOSSLESS_AUDIO_COPY_EXT_BY_CODEC = {
    "aac": "m4a", "alac": "m4a", "mp3": "mp3", "ac3": "ac3", "eac3": "eac3",
    "opus": "opus", "vorbis": "ogg", "flac": "flac",
    "pcm_s16le": "wav", "pcm_s24le": "wav", "pcm_s32le": "wav", "pcm_u8": "wav",
    "pcm_f32le": "wav", "pcm_s16be": "wav",
}

LOSSLESS_AUDIO_COPY_EXT_CHOICES = {
    "aac": ["m4a", "aac", "mka", "mp4", "mov", "ts"],
    "alac": ["m4a", "mka", "mov"],
    "mp3": ["mp3", "mka", "mp4"],
    "ac3": ["ac3", "mka", "mp4", "ts"],
    "eac3": ["eac3", "mka", "mp4", "ts"],
    "opus": ["opus", "ogg", "mka", "webm"],
    "vorbis": ["ogg", "mka", "webm"],
    "flac": ["flac", "mka", "ogg"],
    "pcm_s16le": ["wav", "mka", "mov"],
    "pcm_s24le": ["wav", "mka", "mov"],
    "pcm_s32le": ["wav", "mka", "mov"],
    "pcm_f32le": ["wav", "mka", "mov"],
    "pcm_u8": ["wav", "mka"],
    "pcm_s16be": ["wav", "mka", "mov"],
}

TRACK_MANAGER_MEDIA_EXTS = {
    ".mkv", ".mp4", ".mov", ".m4v", ".webm", ".avi", ".ts", ".mpg", ".mpeg", ".wmv", ".flv",
    ".m4a", ".mka", ".mp3", ".aac", ".flac", ".wav", ".opus", ".ogg", ".ac3", ".eac3", ".dts",
}

EXTRACT_AUDIO_EXTENSIONS = {
    "aac": ".aac",
    "ac3": ".ac3",
    "eac3": ".eac3",
    "mp3": ".mp3",
    "mp2": ".mp2",
    "opus": ".opus",
    "vorbis": ".ogg",
    "flac": ".flac",
    "alac": ".m4a",
    "pcm_s16le": ".wav",
    "pcm_s24le": ".wav",
    "pcm_s32le": ".wav",
    "pcm_f32le": ".wav",
    "dts": ".dts",
    "truehd": ".thd",
}

EXTRACT_SUBTITLE_EXTENSIONS = {
    "ass": ".ass",
    "ssa": ".ssa",
    "subrip": ".srt",
    "srt": ".srt",
    "text": ".srt",
    "mov_text": ".srt",
    "webvtt": ".vtt",
    "hdmv_pgs_subtitle": ".sup",
    "pgs": ".sup",
    "dvd_subtitle": ".sub",
    "dvdsub": ".sub",
    "vobsub": ".sub",
    "dvb_subtitle": ".sub",
    "dvbsub": ".sub",
    "xsub": ".avi",
}

# Containers each audio codec can be stream-COPIED into, most preferred first
# (entry 0 is the default the extract prompt offers). Verified by really
# muxing each pair on ffmpeg 8.1.1.
#
# `.m4a` is NOT `.mp4`: it selects the `ipod` muxer, which accepts a much
# narrower codec set. mp3 and eac3 were listed here -- mp3 with `.m4a` FIRST,
# so the default offer for every extracted MP3 track died with "Could not
# write header (incorrect codec parameters ?)" and wrote nothing. Both keep
# their `.mp4` entry, which really does carry them.
EXTRACT_COPY_CONTAINERS_AUDIO = {
    "aac": ["m4a", "mp4", "aac", "ts", "mov"],
    "alac": ["m4a", "mov", "caf"],
    "ac3": ["m4a", "ac3", "mp4", "ts"],
    "eac3": ["eac3", "mp4", "ts"],
    "mp3": ["mp3", "mp4"],
    "mp2": ["mp2", "mpg", "ts"],
    "opus": ["opus", "ogg", "webm"],
    "vorbis": ["ogg", "webm"],
    "flac": ["flac", "ogg"],
    "pcm_s16le": ["wav", "mov", "caf"],
    "pcm_s24le": ["wav", "mov", "caf"],
    "pcm_s32le": ["wav", "mov", "caf"],
    "pcm_f32le": ["wav", "mov", "caf"],
    "dts": ["dts", "ts"],
    "truehd": ["thd"],
}

EXTRACT_COPY_CONTAINERS_VIDEO = {
    "h264": ["mp4", "mov", "ts", "m4v"],
    "hevc": ["mp4", "mov", "ts"],
    "h265": ["mp4", "mov", "ts"],
    "av1": ["mp4", "webm"],
    "vp9": ["webm", "mp4"],
    "vp8": ["webm"],
    "mpeg4": ["mp4", "avi", "mov"],
    "mpeg2video": ["mpg", "ts"],
    "prores": ["mov"],
}

# Same contract for subtitles: every entry must survive `-c copy`.
# `.ass` and `.ssa` both select the `ass` muxer, whose only subtitle codec is
# `ass`, so a subrip stream copied into one fails at header-write time. It is
# offered only for the sources that already are ASS/SSA.
EXTRACT_COPY_CONTAINERS_SUBTITLE = {
    "subrip": ["srt"],
    "srt": ["srt"],
    "text": ["srt"],
    "mov_text": ["srt"],
    "ass": ["ass", "ssa"],
    "ssa": ["ssa", "ass"],
    "webvtt": ["vtt"],
    "hdmv_pgs_subtitle": ["sup"],
    "pgs": ["sup"],
    "dvd_subtitle": ["sub"],
    "vobsub": ["sub"],
    "dvbsub": ["sub"],
}
