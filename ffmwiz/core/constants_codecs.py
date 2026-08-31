"""FFmWiz codec, container and subtitle-policy reference tables.

Pure literal data split out of ffmwiz/core/constants.py to keep that module
under 800 lines. Leaf module: no imports. Re-exported by constants.py, so the
public API (FFmWiz.SUBTITLE_CONTAINER_POLICY, ...) is unchanged."""
from __future__ import annotations



# Container-aware safe defaults. Full support depends on your FFmpeg build and
# muxer, so the prompt still accepts any valid runtime encoder/format name.
AUDIO_CODEC_DEFAULTS_BY_FORMAT = {
    "aac": "aac",
    "m4a": "aac",
    "mp4": "aac",
    "mkv": "aac",
    "mov": "aac",
    "mp3": "libmp3lame",
    "ogg": "libopus",
    "opus": "libopus",
    "webm": "libopus",
    "weba": "libopus",
    "flac": "flac",
    "wav": "pcm_s16le",
}

# Which audio encoders each container can actually mux. Only WebM used to be
# checked, so `-c:a aac` into .flac / .ogg / .opus reached FFmpeg and failed with
# "Exactly one FLAC audio stream is required" / "Unsupported codec id in stream 0".
# An extension missing from this table is unconstrained -- mp4/mkv/mov/ts/avi
# accept a wide mix and were verified to (aac, flac and pcm_s16le all mux into
# mp4 or mkv on ffmpeg 8.1.1), so listing them would only create false rejects.
# Which VIDEO codec aliases each constrained container can mux, plus the one to
# fall back to. WebM is the only common container that genuinely refuses the
# defaults, but HardSub, standalone Join and the main wizard each had their own
# ad-hoc WebM special case (or none at all), so they are unified here.
# An extension missing from this table is unconstrained.
VIDEO_CODECS_BY_FORMAT = {
    "webm": {"allowed": {"VP9", "VP8", "AV1", "copy"}, "fallback": "VP9"},
    "weba": {"allowed": {"VP9", "VP8", "AV1", "copy"}, "fallback": "VP9"},
}

AUDIO_CODECS_BY_FORMAT = {
    "flac": {"flac", "copy"},
    # .opus is the Ogg muxer under another extension, so it mixes exactly like
    # .ogg/.oga: measured on ffmpeg 8.1.1, a copied opus, vorbis OR flac stream
    # all mux into .opus, while mp3 and aac give "Unsupported codec id in
    # stream 0". Listing only libopus made the container guard reject a copy
    # the muxer accepts. `default_audio_codec_for_ext` still picks libopus, so
    # the ENCODE default is unchanged.
    "opus": {"libopus", "libvorbis", "flac", "copy"},
    "ogg": {"libopus", "libvorbis", "flac", "copy"},
    "oga": {"libopus", "libvorbis", "flac", "copy"},
    "mp3": {"libmp3lame", "copy"},
    "webm": {"libopus", "libvorbis", "copy"},
    "weba": {"libopus", "libvorbis", "copy"},
}

STREAM_STAT_METADATA_TAGS = (
    "BPS",
    "BPS-eng",
    "BPS-ENG",
    "DURATION",
    "DURATION-eng",
    "DURATION-ENG",
    "NUMBER_OF_FRAMES",
    "NUMBER_OF_FRAMES-eng",
    "NUMBER_OF_FRAMES-ENG",
    "NUMBER_OF_BYTES",
    "NUMBER_OF_BYTES-eng",
    "NUMBER_OF_BYTES-ENG",
    "_STATISTICS_WRITING_APP",
    "_STATISTICS_WRITING_DATE_UTC",
    "_STATISTICS_TAGS",
)

AUDIO_CODEC_ALIASES = {
    "opus": "libopus",
    "mp3": "libmp3lame",
    "mp3lame": "libmp3lame",
    "vorbis": "libvorbis",
}

BITRATE_AUDIO_CODECS = {
    "aac",
    "ac3",
    "eac3",
    "libfdk_aac",
    "libmp3lame",
    "libopus",
    "libvorbis",
    "mp2",
    "mp3",
    "opus",
    "vorbis",
}

TEXT_SUBTITLE_CODECS = {"ass", "mov_text", "ssa", "srt", "subrip", "text", "webvtt"}
BITMAP_SUBTITLE_CODECS = {
    "dvb_subtitle",
    "dvbsub",
    "dvd_subtitle",
    "dvdsub",
    "hdmv_pgs_subtitle",
    "pgs",
    "vobsub",
    "xsub",
}
# Which subtitle codec a container can actually carry. `-c:s copy` was emitted
# for every non-MP4 container without checking, so mov_text -> mkv, subrip ->
# webm and subrip -> avi all reached FFmpeg and died at header-write time with
# "Subtitle codec ... is not supported" / "Could not write header".
# Verified against ffmpeg 8.1.1 by really muxing each pair.
#   "copy" -> stream-copy is legal
#   a codec name -> must be transcoded to that codec
#   None -> the container cannot carry this subtitle at all; drop it
SUBTITLE_CONTAINER_POLICY: dict[str, dict[str, str | None]] = {
    # MP4 family: timed text only, and bitmap subtitles cannot be carried.
    "mp4": {"text": "mov_text", "bitmap": None},
    "m4v": {"text": "mov_text", "bitmap": None},
    "m4a": {"text": "mov_text", "bitmap": None},
    "mov": {"text": "mov_text", "bitmap": None},
    "ismv": {"text": "mov_text", "bitmap": None},
    # Matroska carries essentially everything, but not MP4's mov_text.
    "mkv": {"text": "copy", "bitmap": "copy", "mov_text": "srt"},
    "mka": {"text": "copy", "bitmap": "copy", "mov_text": "srt"},
    # WebM: WebVTT only.
    "webm": {"text": "webvtt", "bitmap": None},
    # MPEG-TS accepts both text and bitmap subtitles as-is.
    "ts": {"text": "copy", "bitmap": "copy"},
    "mpg": {"text": "copy", "bitmap": "copy"},
    "mpeg": {"text": "copy", "bitmap": "copy"},
    # AVI has no usable subtitle muxing in FFmpeg ("Not yet implemented").
    "avi": {"text": None, "bitmap": None},
}

HARDSUB_BITMAP_SUBTITLE_ERROR = (
    "Bitmap subtitle streams such as PGS/VobSub/DVDSub are not supported by this HardSub mode. "
    "Choose a text subtitle stream or use an external .srt/.ass/.ssa/.vtt/.webvtt file."
)
