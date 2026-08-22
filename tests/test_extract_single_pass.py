"""Regression tests: extracting several streams from one file must read it once.

Audio and subtitle packets are interleaved throughout a container, so pulling a
40 KB subtitle out of a 722 MB MKV still costs a full read of the whole file --
that part is unavoidable and was measured at 98% of raw disk throughput
(`dd` 4.31 s vs ffmpeg 4.39 s on the same disk).

What WAS avoidable: the extract mode ran one FFmpeg per selected stream, so
picking N streams from one file re-read it N times. The prompt explicitly offers
`list 1,2,3`, `range 1-5` and `mix 1-5,6,8-10`, so this was the normal case, not
an edge case. Measured on a real 722 MB MKV: two streams as separate runs
5.80 s, as one run with two outputs 4.57 s.
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


def _stream(index, codec_type, codec_name):
    return {"index": index, "codec_type": codec_type, "codec_name": codec_name}


class ExtractCommandShape(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.gettempdir())

    def test_several_streams_share_one_input(self):
        cmd = [str(p) for p in FFmWiz.build_extract_streams_command(
            "ffmpeg", Path("in.mkv"),
            [(_stream(1, "audio", "aac"), self._tmp / "a.mka"),
             (_stream(2, "subtitle", "ass"), self._tmp / "s.ass"),
             (_stream(0, "video", "hevc"), self._tmp / "v.mkv")])]
        self.assertEqual(cmd.count("-i"), 1, "one read means exactly one input")
        self.assertEqual(sum(1 for part in cmd if part == "-map"), 3)

    def test_each_output_keeps_its_own_map_and_codec(self):
        cmd = [str(p) for p in FFmWiz.build_extract_streams_command(
            "ffmpeg", Path("in.mkv"),
            [(_stream(1, "audio", "aac"), self._tmp / "a.mka"),
             (_stream(2, "subtitle", "mov_text"), self._tmp / "s.srt")])]
        # mov_text has to be transcoded to srt; the neighbouring audio output
        # must still be a plain copy, so the option cannot be global.
        self.assertIn("-c:s", cmd)
        self.assertIn("srt", cmd)
        self.assertIn("copy", cmd)
        # every -map is immediately followed by its stream specifier
        for index, part in enumerate(cmd):
            if part == "-map":
                self.assertTrue(cmd[index + 1].startswith("0:"))

    def test_negative_selectors_stay_with_their_own_output(self):
        cmd = [str(p) for p in FFmWiz.build_extract_streams_command(
            "ffmpeg", Path("in.mkv"),
            [(_stream(1, "audio", "aac"), self._tmp / "a.mka"),
             (_stream(2, "subtitle", "ass"), self._tmp / "s.ass")])]
        # The audio output drops video/subs/data; the subtitle output drops
        # video/audio/data. Both -vn appear, but -an only once and -sn only once.
        self.assertEqual(cmd.count("-vn"), 2)
        self.assertEqual(cmd.count("-an"), 1)
        self.assertEqual(cmd.count("-sn"), 1)

    def test_a_single_stream_still_works(self):
        cmd = [str(p) for p in FFmWiz.build_extract_streams_command(
            "ffmpeg", Path("in.mkv"), [(_stream(2, "subtitle", "ass"), self._tmp / "s.ass")])]
        self.assertEqual(cmd.count("-i"), 1)
        self.assertEqual(sum(1 for part in cmd if part == "-map"), 1)

    def test_no_streams_is_rejected(self):
        with self.assertRaises(ValueError):
            FFmWiz.build_extract_streams_command("ffmpeg", Path("in.mkv"), [])

    def test_a_stream_without_an_index_is_rejected(self):
        with self.assertRaises(ValueError):
            FFmWiz.build_extract_streams_command(
                "ffmpeg", Path("in.mkv"),
                [({"codec_type": "audio", "codec_name": "aac"}, self._tmp / "a.mka")])


@unittest.skipIf(not FFMPEG or not FFPROBE, "ffmpeg/ffprobe not on PATH")
class ExtractSinglePassRealMedia(unittest.TestCase):
    """One command must really produce every requested stream."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_extract_"))
        srt = self._tmp / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nhello\n\n", encoding="utf-8")
        self._src = self._tmp / "src.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=3",
             "-f", "lavfi", "-i", "sine=duration=3",
             "-f", "lavfi", "-i", "sine=frequency=880:duration=3", "-i", str(srt),
             "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
             "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-c:s", "srt",
             str(self._src)], check=True, timeout=180)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _streams(self):
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(self._src)],
            capture_output=True, text=True, timeout=60).stdout
        return json.loads(out)["streams"]

    def test_one_pass_produces_every_requested_stream(self):
        streams = self._streams()
        audio = [s for s in streams if s["codec_type"] == "audio"]
        subtitle = [s for s in streams if s["codec_type"] == "subtitle"]
        targets = [(audio[0], self._tmp / "a0.mka"),
                   (audio[1], self._tmp / "a1.mka"),
                   (subtitle[0], self._tmp / "s0.srt")]
        cmd = FFmWiz.build_extract_streams_command(FFMPEG, self._src, targets)
        result = subprocess.run([str(p) for p in cmd], capture_output=True,
                                text=True, timeout=300)
        self.assertEqual(result.returncode, 0,
                         result.stderr.strip().splitlines()[-1] if result.stderr else "")
        for _stream_info, path in targets:
            self.assertTrue(path.exists(), f"{path.name} was not written")
            self.assertGreater(path.stat().st_size, 0, f"{path.name} is empty")

    def test_the_second_audio_track_is_the_one_asked_for(self):
        # A shared `-map` would have silently written the first track twice.
        streams = self._streams()
        audio = [s for s in streams if s["codec_type"] == "audio"]
        first, second = self._tmp / "first.mka", self._tmp / "second.mka"
        cmd = FFmWiz.build_extract_streams_command(
            FFMPEG, self._src, [(audio[0], first), (audio[1], second)])
        subprocess.run([str(p) for p in cmd], check=True, timeout=300,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertTrue(first.exists() and second.exists())
        # Different source tones, so the extracted payloads must differ.
        self.assertNotEqual(first.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
