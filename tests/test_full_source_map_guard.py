"""Regression: the `-map 0 -c copy` shortcut must respect subtitle containers (NEW-CMD3).

`can_use_full_source_map_for_simple_encode` enables a fast path that maps every
source stream and copies it, then overrides only the video (and, when needed, the
audio) encoder. That is a real optimisation and must keep working -- but it
copies subtitles VERBATIM, so it is only legal when the target container can
actually hold them.

The guard used to check the MP4 family alone. Everything else took the shortcut
unchecked, and FFmpeg died at header-write time. Reproduced end to end with a
real subrip MKV before the fix:

    .avi   -> Could not write header ... Not yet implemented in FFmpeg
    .webm  -> Only VP8 or VP9 or AV1 video and Vorbis or Opus audio and
              WebVTT subtitles are supported for WebM
    .mkv, .ts -> fine (copy really is legal there)

The guard now asks the shared `subtitle_codec_for_container` resolver, so a
source it cannot copy falls back to the per-stream path that transcodes it
(webvtt for WebM, mov_text for MP4) or drops it with a note (AVI).
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _subtitle(codec):
    return {"codec_type": "subtitle", "codec_name": codec, "index": 2}


def _answers(output_ext, subtitle_codecs=("subrip",)):
    """Answers that satisfy every OTHER precondition of the shortcut."""
    subs = [_subtitle(codec) for codec in subtitle_codecs]
    return {
        "output_ext": output_ext,
        "video_streams": [{"codec_type": "video", "codec_name": "h264",
                           "width": 1920, "height": 1080,
                           "avg_frame_rate": "25/1", "r_frame_rate": "25/1",
                           "pix_fmt": "yuv420p"}],
        "audio_streams": [{"codec_type": "audio", "codec_name": "aac", "channels": 2}],
        "subtitle_streams": subs,
        "data_streams": [], "attachment_streams": [],
        # "all" is what the guard requires -- an explicit index list disables the
        # shortcut for a different reason and would mask what we are testing.
        "audio_tracks": "all", "subtitle_tracks": "all",
        "keep_source_metadata": True, "keep_source_chapters": True,
        "keep_source_extra_video": True, "keep_source_subtitles": True,
        "keep_source_data": True, "keep_embedded_attachments": True,
    }


def _allowed(output_ext, subtitle_codecs=("subrip",)):
    return FFmWiz.can_use_full_source_map_for_simple_encode(
        _answers(output_ext, subtitle_codecs), [0], False, False)


class ShortcutRefusedWhenTheContainerCannotCopy(unittest.TestCase):
    def test_avi_cannot_carry_subtitles_at_all(self):
        self.assertFalse(_allowed("avi"))

    def test_webm_cannot_copy_subrip(self):
        self.assertFalse(_allowed("webm"))

    def test_mp4_cannot_copy_subrip(self):
        # The one case the old guard did cover; it must stay covered.
        self.assertFalse(_allowed("mp4"))

    def test_mkv_cannot_copy_mov_text(self):
        # The reverse direction: an MP4 source into Matroska.
        # "[matroska] Subtitle codec mov_text is not supported."
        self.assertFalse(_allowed("mkv", ("mov_text",)))

    def test_one_bad_track_among_several_refuses_the_whole_shortcut(self):
        # -c copy is all-or-nothing, so a single uncopyable track disqualifies it.
        self.assertFalse(_allowed("mkv", ("subrip", "mov_text")))


class ShortcutPreservedWhereCopyIsLegal(unittest.TestCase):
    """The fast path is an optimisation -- do not lose it to over-blocking."""

    def test_mkv_copies_subrip(self):
        self.assertTrue(_allowed("mkv"))

    def test_mkv_copies_ass_and_pgs(self):
        for codec in ("ass", "hdmv_pgs_subtitle"):
            with self.subTest(codec):
                self.assertTrue(_allowed("mkv", (codec,)))

    def test_ts_copies_subrip(self):
        self.assertTrue(_allowed("ts"))

    def test_a_source_with_no_subtitles_is_unaffected(self):
        for ext in ("avi", "webm", "mp4", "mkv"):
            with self.subTest(ext):
                self.assertTrue(_allowed(ext, ()))


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")
class RealMuxAcrossContainers(unittest.TestCase):
    """Build the real command for each container and run it."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_fullmap_"))
        srt = cls._tmp / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nHELLO\n", encoding="utf-8")
        cls._src = cls._tmp / "in.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "color=c=red:s=160x120:d=3:r=25",
             "-f", "lavfi", "-i", "sine=duration=3", "-i", str(srt),
             "-map", "0:v", "-map", "1:a", "-map", "2:s",
             "-c:v", "libx264", "-preset", "ultrafast", "-vf", "format=yuv420p",
             "-c:a", "aac", "-c:s", "srt", str(cls._src)],
            check=True, timeout=300)
        cls._probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json",
             str(cls._src)], capture_output=True, text=True, timeout=60).stdout)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _build_and_run(self, ext):
        streams = self._probe["streams"]
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
            "input_path": self._src, "output_location": self._tmp,
            "streams": streams, "format": self._probe["format"],
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [s for s in streams if s["codec_type"] == "subtitle"],
            "data_streams": [], "attachment_streams": [],
            "output_ext": ext, "video_codec": "H264", "use_gpu": False,
            "audio_codec": "aac", "audio_bitrate_kbps": 128,
            "audio_tracks": "all", "subtitle_tracks": "all",
            "resolution": "n", "fps": 25, "video_bitrate_kbps": 400,
            "color_range_choice": "tv",
            "keep_source_metadata": True, "keep_source_chapters": True,
            "keep_source_extra_video": True, "keep_source_subtitles": True,
            "keep_source_data": True, "keep_embedded_attachments": True,
        }
        cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return result, Path(answers["output_path"])

    def test_every_container_muxes(self):
        # Before the fix, .avi and .webm both failed to write a header here.
        for ext in ("avi", "webm", "mp4", "mkv", "ts"):
            with self.subTest(ext):
                result, output = self._build_and_run(ext)
                self.assertEqual(result.returncode, 0,
                                 f".{ext}: {result.stderr.strip()[-400:]}")
                self.assertTrue(output.exists() and output.stat().st_size > 0,
                                f".{ext} produced no output")


if __name__ == "__main__":
    unittest.main()
