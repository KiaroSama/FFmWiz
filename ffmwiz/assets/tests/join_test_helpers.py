"""Shared fixtures for the Join / loudnorm / track-manager test modules.

Extracted from test_loudnorm_join_progress.py so its split topical test files
can reuse the same stream/item builders without duplication.
"""
from pathlib import Path


def video_stream(width=1920, height=1080, fps="30/1", pix_fmt="yuv420p"):
    return {
        "codec_type": "video",
        "codec_name": "h264",
        "width": width,
        "height": height,
        "avg_frame_rate": fps,
        "r_frame_rate": fps,
        "pix_fmt": pix_fmt,
        "color_range": "tv",
    }


def audio_stream(bit_rate="128000", channels=2):
    stream = {"codec_type": "audio", "codec_name": "aac", "channels": channels, "sample_rate": "48000"}
    if bit_rate is not None:
        stream["bit_rate"] = bit_rate
    return stream


def make_item(path, duration=10.0, audio_bitrate="128000", with_audio=True, fps="30/1"):
    vstreams = [video_stream(fps=fps)]
    astreams = [audio_stream(bit_rate=audio_bitrate)] if with_audio else []
    return {
        "path": Path(path),
        "probe": {},
        "format": {"duration": str(duration)},
        "streams": vstreams + astreams,
        "video_streams": vstreams,
        "audio_streams": astreams,
        "subtitle_streams": [],
        "attachment_streams": [],
        "data_streams": [],
        "duration": duration,
    }


def _vstream(pix_fmt="yuv420p", depth=8):
    return {
        "codec_type": "video", "codec_name": "hevc",
        "width": 1920, "height": 1080,
        "avg_frame_rate": "30/1", "r_frame_rate": "30/1",
        "pix_fmt": pix_fmt, "bits_per_raw_sample": str(depth),
        "color_range": "tv",
    }
