"""Regression tests for FFmpeg option POSITION, not just option presence.

The FFmpeg CLI is positional: an option belongs to the file that follows it.
`-t` emitted before an `-i` is an INPUT option limiting how much of that input is
read; emitted after the last `-i` it is an OUTPUT option bounding the result.

`build_ffmpeg_command` emitted the single-cut `-t` immediately after the source
`-i`, which was fine until a second input appeared. When the source has chapters
and the timeline is edited, the builder injects a generated ffmetadata file as
input #1 -- and the `-t` then bound that metadata file instead of the output, so
a 10-second cut silently produced 20 seconds of video.
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


def _chaptered_source(tmp: Path, seconds: int = 30) -> Path:
    src, meta = tmp / "src.mp4", tmp / "chapters.ffmetadata"
    half = seconds * 1000 // 2
    meta.write_text(
        ";FFMETADATA1\n"
        f"[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND={half}\ntitle=one\n"
        f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={half}\nEND={seconds * 1000}\ntitle=two\n",
        encoding="utf-8")
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=15:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=duration={seconds}",
         "-i", str(meta), "-map", "0:v", "-map", "1:a", "-map_chapters", "2",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)],
        check=True, timeout=180)
    return src


def _answers_for(src: Path, tmp: Path, probe: dict, keep_range=(10.0, 20.0)):
    return {
        "ffmpeg": FFMPEG or "ffmpeg", "ffprobe": FFPROBE or "ffprobe",
        "input_path": src, "output_location": tmp,
        "probe": probe, "streams": probe["streams"],
        "video_streams": [s for s in probe["streams"] if s["codec_type"] == "video"],
        "audio_streams": [s for s in probe["streams"] if s["codec_type"] == "audio"],
        "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
        "chapters": probe.get("chapters", []), "format": probe["format"],
        "output_ext": "mp4", "video_codec": "H264", "use_gpu": False,
        "audio_codec": "aac", "audio_bitrate_kbps": 128,
        "audio_tracks": [0], "subtitle_tracks": [],
        "resolution": "n", "fps": 15, "video_bitrate_kbps": 1000,
        "color_range_choice": "tv",
        "cut_keep_ranges": [keep_range],
        "keep_source_chapters": True, "source_extra_policy": True,
    }


@unittest.skipIf(not FFMPEG or not FFPROBE, "ffmpeg/ffprobe not on PATH")
class CutDurationOptionOrder(unittest.TestCase):
    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_optorder_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _probe(self, path, *extra):
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", *extra, "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60).stdout
        return json.loads(out)

    def _build(self, keep_range=(10.0, 20.0)):
        src = _chaptered_source(self._tmp)
        probe = self._probe(src, "-show_chapters")
        answers = _answers_for(src, self._tmp, probe, keep_range)
        return answers, [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]

    def test_chapter_metadata_input_is_actually_injected(self):
        # Guards the test itself: without the second input the ordering bug
        # cannot appear, so a silent change here must not make the real
        # assertions below vacuously pass.
        _, cmd = self._build()
        self.assertEqual(cmd.count("-i"), 2, "expected source + chapter-metadata inputs")
        self.assertEqual(cmd[cmd.index("-map_chapters") + 1], "1")

    def test_duration_is_an_output_option_not_an_input_option(self):
        _, cmd = self._build()
        last_input = max(index for index, part in enumerate(cmd) if part == "-i")
        self.assertGreater(
            cmd.index("-t"), last_input,
            "-t must follow EVERY -i or it binds to the next input instead of the output",
        )

    def test_seek_stays_an_input_option(self):
        # -ss before the source -i is the fast demuxer seek; moving it would be
        # a real performance regression.
        _, cmd = self._build()
        self.assertLess(cmd.index("-ss"), cmd.index("-i"))

    def test_real_encode_honours_the_requested_cut_length(self):
        answers, cmd = self._build(keep_range=(10.0, 20.0))
        subprocess.run(cmd, check=True, timeout=300,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out = Path(answers["output_path"])
        duration = float(self._probe(out)["format"]["duration"])
        self.assertAlmostEqual(
            duration, 10.0, delta=0.5,
            msg=f"a 10 s cut produced {duration:.2f} s of output",
        )

    def test_real_encode_still_keeps_the_remapped_chapters(self):
        answers, cmd = self._build()
        subprocess.run(cmd, check=True, timeout=300,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chapters = self._probe(Path(answers["output_path"]), "-show_chapters").get("chapters", [])
        self.assertTrue(chapters, "the chapter metadata input must still be mapped")


if __name__ == "__main__":
    unittest.main()
