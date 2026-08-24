"""Regression: reverse mirrors around the PICTURE, not the container.

`encode_timeline_map()` took its source duration from `format.duration`. A
container outlives its video whenever another stream is longer -- audio
padding, a trailing subtitle, an AAC priming delay -- so every retimed cue came
out shifted by the difference.

Measured on a 4.000 s video muxed with 4.5 s of audio (container 4.523 s), with
one cue at 3.500-4.000 s. Reversed, that cue belongs at the very START of the
output:

    before   0.523 -> 1.023     mirrored on the 4.523 s container
    after    0.000 -> 0.500     mirrored on the 4.000 s video

An external audit saw the same defect as a 23 ms drift; the size of the error
is just the size of the container's lead, so a fixture with a bigger lead makes
it unmistakable.

Matroska does not fill in a per-stream `duration` field, which is why the fix
cannot be a one-line swap: the span is recovered from the stream duration, then
Matroska's `DURATION` tag, then the frame count over the frame rate, and only
then the container.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts
from ffmwiz import encoding

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


class TheSpanHelper(unittest.TestCase):
    """Each source in turn, without touching a file."""

    def test_a_stream_duration_wins(self):
        self.assertEqual(3.5, FFmWiz.video_stream_span_seconds(
            {"duration": "3.5"}, {"duration": "4.0"}))

    def test_a_matroska_duration_tag_is_used_next(self):
        self.assertEqual(4.0, FFmWiz.video_stream_span_seconds(
            {"tags": {"DURATION": "00:00:04.000000000"}}, {"duration": "4.523"}))

    def test_a_tag_with_minutes_and_hours_parses(self):
        self.assertAlmostEqual(3723.5, FFmWiz.video_stream_span_seconds(
            {"tags": {"DURATION": "01:02:03.500000000"}}, {}), places=3)

    def test_the_frame_count_is_used_after_that(self):
        self.assertEqual(4.0, FFmWiz.video_stream_span_seconds(
            {"nb_frames": "100", "avg_frame_rate": "25/1"}, {"duration": "9.9"}))

    def test_the_container_is_the_last_resort(self):
        self.assertEqual(4.523, FFmWiz.video_stream_span_seconds(
            {}, {"duration": "4.523"}))

    def test_an_unknown_source_is_zero_not_an_error(self):
        self.assertEqual(0.0, FFmWiz.video_stream_span_seconds({}, {}))
        self.assertEqual(0.0, FFmWiz.video_stream_span_seconds(
            {"duration": "not a number"}, {}))

    def test_a_broken_tag_falls_through_instead_of_raising(self):
        self.assertEqual(4.0, FFmWiz.video_stream_span_seconds(
            {"tags": {"DURATION": "garbage"}}, {"duration": "4.0"}))


class TheTimelineMapUsesIt(unittest.TestCase):
    def _answers(self, stream, fmt):
        return {"video_streams": [stream], "format": fmt,
                "reverse_video": True, "video_speed_enabled": True,
                "video_speed_factor": 1.0}

    def test_the_mirror_axis_is_the_video_span(self):
        timeline = FFmWiz.encode_timeline_map(self._answers(
            {"tags": {"DURATION": "00:00:04.000000000"}}, {"duration": "4.523"}))
        self.assertEqual(4.0, timeline.source_duration)
        # A cue at 3.5-4.0 belongs at the very start once mirrored.
        self.assertEqual([(0.0, 0.5)], timeline.map_interval(3.5, 4.0))

    def test_the_container_still_answers_when_nothing_else_can(self):
        timeline = FFmWiz.encode_timeline_map(
            self._answers({}, {"duration": "4.523"}))
        self.assertAlmostEqual(4.523, timeline.source_duration, places=3)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")
class TheRealOutputAgrees(NoLeakedArtifacts, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_mirror_"))
        srt = cls._tmp / "tail.srt"
        srt.write_text("1\n00:00:03,500 --> 00:00:04,000\nTAIL\n\n",
                       encoding="utf-8", newline="\n")
        cls.source = cls._tmp / "src.mkv"
        # 4 s of video inside a 4.5 s audio track: the container outlives the
        # picture, which is the whole point of the fixture.
        subprocess.run(
            [FFMPEG, "-v", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=4",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=4.5",
             "-i", str(srt), "-map", "0:v", "-map", "1:a", "-map", "2:s",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-c:s", "srt", str(cls.source)],
            check=True, capture_output=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _probe(self, path):
        return json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120).stdout)

    def test_the_fixture_really_has_a_container_lead(self):
        # Guard the guard: without the lead the two axes coincide and the
        # assertion below would pass either way.
        info = self._probe(self.source)
        container = float(info["format"]["duration"])
        video = FFmWiz.video_stream_span_seconds(
            [s for s in info["streams"] if s["codec_type"] == "video"][0],
            info["format"])
        self.assertGreater(container - video, 0.4,
                           f"container {container:.3f}s vs video {video:.3f}s")

    def test_the_reversed_cue_lands_at_the_start(self):
        out = self._tmp / "out"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        info = self._probe(self.source)
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.source,
            "probe": info, "format": info["format"], "output_location": out,
            "video_streams": [s for s in info["streams"] if s["codec_type"] == "video"],
            "audio_streams": [s for s in info["streams"] if s["codec_type"] == "audio"],
            "subtitle_streams": [s for s in info["streams"] if s["codec_type"] == "subtitle"],
            "output_ext": "mkv", "audio_tracks": [0],
            "keep_source_subtitles": True, "subtitle_tracks": [0],
            "video_encoder": "libx264", "crf": 28, "preset": "ultrafast",
            "audio_codec": "aac", "audio_speed_from_video": True,
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True,
        })
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            answers["cmd"] = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
            code, _elapsed = encoding.execute_encode_plan(
                answers, answers["cmd"], total_duration=4.0, label="test")
        self.assertEqual(0, code, noise.getvalue()[-1500:])
        result = Path(answers["output_path"])
        dump = out / "cues.srt"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(result),
                        "-map", "0:s:0", "-c:s", "srt", str(dump)],
                       capture_output=True, timeout=120)
        cues = (FFmWiz.parse_srt(dump.read_text(encoding="utf-8"))
                if dump.exists() and dump.stat().st_size else [])
        self.assertEqual(1, len(cues), f"expected the one cue back, got {cues}")
        start, end, text = cues[0]
        self.assertEqual("TAIL", text)
        self.assertAlmostEqual(0.0, start, delta=0.1,
                               msg=f"the tail cue should open the reversed clip, got {start:.3f}s")
        self.assertAlmostEqual(0.5, end, delta=0.1)


if __name__ == "__main__":
    unittest.main()
