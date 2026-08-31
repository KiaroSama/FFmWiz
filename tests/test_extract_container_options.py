"""Every container the extract prompt offers must really accept `-c copy`.

`extract_stream_container_options` promises "All options keep the stream with
-c copy (no re-encode)" and returns `options[0]` as the DEFAULT the prompt
offers. Three entries could not keep that promise, and each one died at
header-write time with "Could not write header (incorrect codec parameters ?)"
after writing nothing:

    mp3    -> .m4a   (and `.m4a` was FIRST, so it was the default offer)
    eac3   -> .m4a
    subrip -> .ass

`.m4a` is not `.mp4`: it selects the `ipod` muxer, which takes a much narrower
codec set. `.ass`/`.ssa` select the `ass` muxer, whose only subtitle codec is
`ass`. The sibling table `LOSSLESS_AUDIO_COPY_EXT_CHOICES` already avoided both
traps, which is what made these three visible.

The static tests pin the specific entries so the guard bites with no ffmpeg on
PATH; the real-media sweep is the one that proves the whole table, by muxing
every offered pair for real.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

# Encoder for each codec the audio table lists and this build can produce.
# truehd is omitted: ffmpeg refuses it without -strict experimental, so the
# table's `thd` entry stays unproven rather than being asserted from nothing.
AUDIO_ENCODERS = {
    "aac": "aac", "alac": "alac", "ac3": "ac3", "eac3": "eac3",
    "mp3": "libmp3lame", "mp2": "mp2", "opus": "libopus", "vorbis": "libvorbis",
    "flac": "flac", "pcm_s16le": "pcm_s16le", "pcm_s24le": "pcm_s24le",
    "pcm_s32le": "pcm_s32le", "pcm_f32le": "pcm_f32le",
}
SUBTITLE_CODECS = ("subrip", "ass", "webvtt")


def _run(args, timeout=120):
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=timeout)


class TheOfferedContainersAreCopyCompatible(unittest.TestCase):
    """Static guard: the exact entries that could not be copied stay out."""

    def test_m4a_is_not_offered_for_codecs_the_ipod_muxer_refuses(self):
        for codec in ("mp3", "eac3"):
            with self.subTest(codec=codec):
                options, default = FFmWiz.extract_stream_container_options(
                    {"codec_type": "audio", "codec_name": codec})
                self.assertNotIn("m4a", options,
                                 f"the ipod (.m4a) muxer cannot carry {codec}")
                self.assertNotEqual("m4a", default)

    def test_mp4_is_still_offered_for_those_codecs(self):
        # Removing .m4a must not remove the MP4 family entirely: .mp4 really
        # does carry both.
        for codec in ("mp3", "eac3"):
            with self.subTest(codec=codec):
                options, _default = FFmWiz.extract_stream_container_options(
                    {"codec_type": "audio", "codec_name": codec})
                self.assertIn("mp4", options)

    def test_ass_is_not_offered_for_a_text_srt_source(self):
        for codec in ("subrip", "srt"):
            with self.subTest(codec=codec):
                options, _default = FFmWiz.extract_stream_container_options(
                    {"codec_type": "subtitle", "codec_name": codec})
                self.assertNotIn("ass", options,
                                 "the ass muxer only takes the ass codec")
                self.assertNotIn("ssa", options)

    def test_ass_is_still_offered_for_an_ass_source(self):
        options, default = FFmWiz.extract_stream_container_options(
            {"codec_type": "subtitle", "codec_name": "ass"})
        self.assertEqual("ass", default)
        self.assertIn("ssa", options)


@requires_ffmpeg
class TheWholeTableReallyMuxes(unittest.TestCase):
    """The proof: mux every offered pair and check a file came out."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_extract_opts_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _stream_of(self, path: Path, kind: str) -> dict:
        probe = _run([FFPROBE, "-v", "error", "-show_streams", "-of", "json",
                      str(path)])
        self.assertEqual(0, probe.returncode, probe.stderr.strip()[-200:])
        streams = [s for s in json.loads(probe.stdout)["streams"]
                   if s.get("codec_type") == kind]
        self.assertTrue(streams, f"no {kind} stream in {path.name}")
        return streams[0]

    def _assert_every_option_copies(self, source: Path, kind: str):
        stream = self._stream_of(source, kind)
        options, default = FFmWiz.extract_stream_container_options(stream)
        self.assertIn(default, options)
        self.assertEqual(default, options[0], "the default must be the first offer")
        for ext in options:
            with self.subTest(codec=stream["codec_name"], ext=ext):
                out = self._tmp / f"out_{stream['codec_name']}.{ext}"
                result = _run(FFmWiz.build_extract_stream_command(
                    FFMPEG, source, stream, out))
                self.assertEqual(
                    0, result.returncode,
                    f"{stream['codec_name']} -> .{ext}: "
                    f"{result.stderr.strip()[-220:]}")
                self.assertTrue(out.exists() and out.stat().st_size > 0,
                                f"{stream['codec_name']} -> .{ext} wrote nothing")
                out.unlink(missing_ok=True)

    def test_every_offered_audio_container_accepts_a_stream_copy(self):
        for codec, encoder in AUDIO_ENCODERS.items():
            source = self._tmp / f"a_{codec}.mka"
            made = _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                         "-f", "lavfi",
                         "-i", "sine=frequency=440:duration=1:sample_rate=48000",
                         "-ac", "2", "-c:a", encoder, str(source)])
            if made.returncode != 0:
                self.skipTest(f"this build cannot encode {codec}")
            with self.subTest(codec=codec):
                self._assert_every_option_copies(source, "audio")

    def test_every_offered_subtitle_container_accepts_a_stream_copy(self):
        srt = self._tmp / "cue.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHELLO\n",
                       encoding="utf-8")
        for codec in SUBTITLE_CODECS:
            source = self._tmp / f"s_{codec}.mkv"
            made = _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                         "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1:r=5",
                         "-i", str(srt), "-map", "0:v", "-map", "1:s",
                         "-c:v", "libx264", "-c:s", codec, "-shortest",
                         str(source)])
            if made.returncode != 0:
                self.skipTest(f"this build cannot mux {codec}")
            with self.subTest(codec=codec):
                self._assert_every_option_copies(source, "subtitle")


if __name__ == "__main__":
    unittest.main()
