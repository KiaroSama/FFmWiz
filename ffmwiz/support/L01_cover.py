"""FFmWiz cover-art / thumbnail attachment (dependency level 1).

Every container stores cover art differently, and getting it wrong fails in
three distinct ways: a hard mux error (opus/ogg), a silent drop (mov), or a
corrupted neighbouring attachment (mkv). The recipes here were each verified by
really muxing them with ffmpeg 8.1.1 and reading the result back with ffprobe:

  mp4 / m4a / m4v : an extra video stream flagged `attached_pic`
  mkv / mka       : a real Matroska ATTACHMENT (`-attach`), needs a mimetype tag
  mp3             : ID3v2 APIC frame, needs `-id3v2_version 3`
  flac            : METADATA_BLOCK_PICTURE, written as a mapped stream
  opus / ogg      : a base64 METADATA_BLOCK_PICTURE VorbisComment -- NOT a stream
                    on the WRITE side. It reads back as one, which is not a
                    contradiction; see the note below before 'fixing' it.
  mov             : refused; the QuickTime brand silently discards the picture
  wav/webm/avi    : refused; no cover-art mechanism at all
"""

# Opus/Ogg: written as a tag, read back as a stream.
#
# Measured on ffmpeg 8.1.1 by really muxing one and probing the result three
# ways, because the three answers disagree and only together do they make sense:
#
#   raw bytes      METADATA_BLOCK_PICTURE is present
#   format_tags    EMPTY -- ffprobe reports no tag at all
#   streams        an extra mjpeg/png stream with disposition attached_pic=1
#
# So the demuxer DECODES the VorbisComment on read and surfaces it as a virtual
# attached picture. The tag is what is stored; the stream is a convenience the
# reader synthesises. Two consequences worth writing down:
#
#   1. Do NOT verify an opus/ogg cover through `format_tags` -- it is empty
#      there and the check fails against a file that is perfectly correct.
#      Check the raw bytes, or the attached_pic stream (which is what
#      tests/test_cover_art.py::CoverArtRealMux does).
#   2. Seeing that stream in ffprobe output is NOT evidence that this module
#      mapped an image stream. It does not: `cover_art_input_args` returns []
#      for these containers and the output args carry no -map. Mapping one
#      really is rejected by the muxer, which is why the tag path exists.
from __future__ import annotations

import base64
import struct
from pathlib import Path
from typing import Any  # noqa: F401

from ffmwiz.core.constants import *  # noqa: F401,F403
from ffmwiz.core.colors import *  # noqa: F401,F403
from ffmwiz.core.exceptions import *  # noqa: F401,F403
from ffmwiz.support.L00_metadata import *  # noqa: F401,F403
from ffmwiz.support.L00_streams import *  # noqa: F401,F403


COVER_ART_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

# How each container stores a cover.
#   "attached_pic" -> extra video stream with the attached_pic disposition
#   "attachment"   -> Matroska -attach with a mimetype tag
#   "id3"          -> MP3 APIC frame
#   "flac_stream"  -> FLAC METADATA_BLOCK_PICTURE written as a mapped stream
#   "vorbis_tag"   -> base64 METADATA_BLOCK_PICTURE VorbisComment (no stream)
COVER_ART_METHOD_BY_FORMAT = {
    "mp4": "attached_pic",
    "m4a": "attached_pic",
    "m4v": "attached_pic",
    "mkv": "attachment",
    "mka": "attachment",
    "mp3": "id3",
    "flac": "flac_stream",
    "opus": "vorbis_tag",
    "ogg": "vorbis_tag",
    "oga": "vorbis_tag",
}

# Containers a user might reasonably ask for that genuinely cannot hold a cover.
# `mov` is the dangerous one: ffmpeg accepts the command, logs the mapping, exits
# 0 -- and writes no `covr` atom at all, so "success" would be a lie.
COVER_ART_UNSUPPORTED_REASONS = {
    "mov": "QuickTime (.mov) silently discards attached cover art; use .mp4 instead.",
    "wav": "WAV cannot store a cover image.",
    "webm": "WebM cannot store a cover image.",
    "avi": "AVI cannot store a cover image.",
    "ts": "MPEG-TS cannot store a cover image.",
}

COVER_ART_COMMENT = "Cover (front)"

COVER_ART_METHOD_DESCRIPTIONS = {
    "attached_pic": "an attached-picture video stream",
    "attachment": "a Matroska attachment",
    "id3": "an ID3v2 APIC frame",
    "flac_stream": "a FLAC METADATA_BLOCK_PICTURE",
    "vorbis_tag": "a base64 METADATA_BLOCK_PICTURE tag",
    None: "an attached picture",
}


def cover_art_mime_type(cover_path: Path) -> str | None:
    """MIME type for a cover image, or None when the extension is not supported."""
    return COVER_ART_MIME_TYPES.get(Path(cover_path).suffix.lower())


def cover_art_method(output_ext: str) -> str | None:
    """Which mechanism this container uses, or None when it has none."""
    return COVER_ART_METHOD_BY_FORMAT.get(str(output_ext or "").lower().lstrip("."))


def cover_art_rejection_reason(output_ext: str, cover_path: Path) -> str | None:
    """Why this cover cannot be attached to this container, or None if it can."""
    ext = str(output_ext or "").lower().lstrip(".")
    if cover_art_mime_type(cover_path) is None:
        return (
            f"Cover art must be a JPEG or PNG image; got '{Path(cover_path).suffix or 'no extension'}'."
        )
    if not Path(cover_path).is_file():
        return f"Cover image not found: {cover_path}"
    if ext in COVER_ART_UNSUPPORTED_REASONS:
        return COVER_ART_UNSUPPORTED_REASONS[ext]
    if cover_art_method(ext) is None:
        return f".{ext} has no supported cover-art mechanism."
    return None


def _image_dimensions(cover_path: Path) -> tuple[int, int]:
    """(width, height) from a JPEG or PNG header, (0, 0) when unreadable.

    The FLAC picture block carries the dimensions; zeros are tolerated by
    players but real values are cheap to read and better metadata.
    """
    data = Path(cover_path).read_bytes()
    try:
        # Check the IHDR chunk, not just the signature: reading blindly at a
        # fixed offset happily returns the ASCII of a chunk name as a width.
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if data[:2] == b"\xff\xd8":  # JPEG
            index = 2
            while index < len(data) - 9:
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                # SOF0..SOF15, skipping the four non-SOF markers in that range.
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC, 0xD8):
                    height, width = struct.unpack(">HH", data[index + 5:index + 9])
                    return int(width), int(height)
                segment = struct.unpack(">H", data[index + 2:index + 4])[0]
                index += 2 + segment
    except (struct.error, IndexError, ValueError):
        pass
    return 0, 0


def metadata_block_picture_value(cover_path: Path) -> str:
    """Base64 FLAC METADATA_BLOCK_PICTURE for an Opus/Ogg VorbisComment.

    Opus and Ogg reject a mapped image stream outright ("Unsupported codec id in
    stream 1"), so the picture has to travel as a tag instead.
    Layout (all big-endian): picture type, MIME length + MIME, description
    length + description, width, height, colour depth, colours used, data
    length + data.
    """
    path = Path(cover_path)
    mime = (cover_art_mime_type(path) or "image/jpeg").encode("ascii")
    data = path.read_bytes()
    width, height = _image_dimensions(path)
    block = b"".join([
        struct.pack(">I", 3),              # picture type 3 = front cover
        struct.pack(">I", len(mime)), mime,
        struct.pack(">I", 0),              # empty description
        struct.pack(">I", width),
        struct.pack(">I", height),
        struct.pack(">I", 24),             # colour depth
        struct.pack(">I", 0),              # indexed colours used
        struct.pack(">I", len(data)), data,
    ])
    return base64.b64encode(block).decode("ascii")


def cover_art_input_args(output_ext: str, cover_path: Path) -> list[str]:
    """Extra `-i` arguments the cover needs, or [] when it travels as a tag."""
    method = cover_art_method(output_ext)
    if method in {"attached_pic", "id3", "flac_stream"}:
        return ["-i", str(cover_path)]
    return []


def cover_art_output_args(
    output_ext: str,
    cover_path: Path,
    *,
    cover_input_index: int = 1,
    mapped_video_streams: int = 0,
    existing_attachments: int = 0,
) -> list[str]:
    """Output arguments that attach the cover.

    `mapped_video_streams` is how many REAL video streams the output already
    has, because the picture becomes the next video stream and `-c:v:N` /
    `-disposition:v:N` are indexed by OUTPUT position: 0 for an audio-only file,
    1 for a normal video file.

    `existing_attachments` matters only for Matroska: the unindexed
    `-metadata:s:t mimetype=...` form retags EVERY attachment, which relabels an
    existing subtitle font as a JPEG. The index has to point at the new one.
    """
    method = cover_art_method(output_ext)
    mime = cover_art_mime_type(cover_path) or "image/jpeg"
    if method == "attached_pic":
        index = max(0, int(mapped_video_streams))
        return [
            "-map", f"{int(cover_input_index)}:v:0",
            "-c:v:" + str(index), "copy",
            "-disposition:v:" + str(index), "attached_pic",
        ]
    if method == "attachment":
        slot = max(0, int(existing_attachments))
        return [
            "-attach", str(cover_path),
            f"-metadata:s:t:{slot}", f"mimetype={mime}",
            f"-metadata:s:t:{slot}", f"filename={Path(cover_path).name}",
        ]
    if method == "id3":
        return [
            "-map", f"{int(cover_input_index)}:v:0",
            "-c:v", "copy",
            "-id3v2_version", "3",
            "-metadata:s:v", "title=Album cover",
            "-metadata:s:v", f"comment={COVER_ART_COMMENT}",
            "-disposition:v", "attached_pic",
        ]
    if method == "flac_stream":
        return [
            "-map", f"{int(cover_input_index)}:v:0",
            "-c:v", "copy",
            "-metadata:s:v", f"comment={COVER_ART_COMMENT}",
            "-disposition:v", "attached_pic",
        ]
    if method == "vorbis_tag":
        return ["-metadata", f"METADATA_BLOCK_PICTURE={metadata_block_picture_value(cover_path)}"]
    return []


def build_cover_art_command(
    ffmpeg: str,
    input_path: Path,
    cover_path: Path,
    output_path: Path,
    *,
    mapped_video_streams: int = 0,
    existing_attachments: int = 0,
) -> list[str]:
    """A complete stream-copy command that adds a cover to one media file.

    Raises ValueError when the container cannot carry one, rather than emitting
    a command that fails at header-write time or silently drops the picture.
    """
    output_ext = Path(output_path).suffix.lstrip(".").lower()
    reason = cover_art_rejection_reason(output_ext, cover_path)
    if reason:
        raise ValueError(reason)
    cmd: list[str] = [ffmpeg, "-hide_banner", "-y" if OVERWRITE_OUTPUT else "-n",
                      "-i", str(input_path)]
    cmd.extend(cover_art_input_args(output_ext, cover_path))
    cmd.extend(["-map", "0", "-map_metadata", "0", "-c", "copy"])
    cmd.extend(cover_art_output_args(
        output_ext, cover_path,
        cover_input_index=1,
        mapped_video_streams=mapped_video_streams,
        existing_attachments=existing_attachments,
    ))
    cmd.append(str(output_path))
    return cmd



def audio_tool_picture_args(answers: dict[str, Any], output_ext: str) -> list[str]:
    """Args that carry the source cover art through an audio tool, or ["-vn"].

    Mapping the picture only works for containers that store a cover AS a
    stream (mp4/m4a, mp3, flac). Opus/Ogg keep it in a base64 VorbisComment and
    reject a mapped image stream outright, and wav has no mechanism at all, so
    those still drop video.
    """
    method = cover_art_method(output_ext)
    if method not in {"attached_pic", "id3", "flac_stream"}:
        return ["-vn"]
    # Normally input 0. The bounded audio-reverse pipeline re-points input 0 at
    # a scratch file that holds only the reversed audio, and adds the original
    # as a second input purely so the cover art still has a home; carrying the
    # picture through the segment/concat stages instead would replicate it into
    # every chunk for no gain.
    picture_input = int(answers.get("_picture_input_index") or 0)
    for position, stream in enumerate(answers.get("video_streams") or []):
        if (stream.get("disposition") or {}).get("attached_pic"):
            args = ["-map", f"{picture_input}:v:{position}", "-c:v", "copy"]
            if method == "id3":
                args.extend(["-id3v2_version", "3"])
            args.extend(["-disposition:v", "attached_pic"])
            return args
    return ["-vn"]


__all__ = [
    'audio_tool_picture_args',
    'COVER_ART_MIME_TYPES',
    'COVER_ART_METHOD_BY_FORMAT',
    'COVER_ART_UNSUPPORTED_REASONS',
    'COVER_ART_COMMENT',
    'COVER_ART_METHOD_DESCRIPTIONS',
    'cover_art_method',
    'cover_art_rejection_reason',
    'metadata_block_picture_value',
    'cover_art_input_args',
    'cover_art_output_args',
    'build_cover_art_command',
]
