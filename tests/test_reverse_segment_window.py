"""Regression: a reverse segment must bound what it DECODES (F01).

`reverse` cannot emit a frame until it has consumed its whole input, so an
output-side `-t` truncates the already-reversed result instead of limiting the
read. Every segment therefore decoded the entire source and returned the wrong
window, and the "runs in short segments to avoid buffering the full video in
RAM" promise was false as well.

Measured on a 4 s source, red 0-2 s then blue 2-4 s, with the chunk size forced
to 2 s (frames sampled at 10/40/60/90% of the output):

    full reverse   before: blue blue blue blue   after: blue blue red red
    keep 0-2 (red) before: blue                  after: red
    keep 1-2 (red) before: blue                  after: red

The colour check is the point: a duration-only assertion passed throughout,
because the output was always the right LENGTH and the wrong CONTENT.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from ffmwiz import encoding
from ffmwiz.support import L00_split

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")
class SegmentedReverseReturnsTheSelectedWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_revwindow_"))
        cls.source = cls._tmp / "src.mkv"
        subprocess.run(
            [FFMPEG, "-v", "error", "-y",
             "-f", "lavfi", "-i", "color=c=red:size=160x120:rate=10:duration=2",
             "-f", "lavfi", "-i", "color=c=blue:size=160x120:rate=10:duration=2",
             "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(cls.source)],
            check=True, capture_output=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _probe(self, path, *extra):
        return json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", *extra,
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120).stdout)

    def _colour_at(self, path, at):
        raw = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
             "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=120).stdout
        if len(raw) < 3:
            return "missing"
        red, _green, blue = raw[0], raw[1], raw[2]
        if red > blue + 40:
            return "red"
        if blue > red + 40:
            return "blue"
        return f"other({red},{blue})"

    def _run(self, label, chunk_seconds, **extra):
        out = self._tmp / label
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        info = self._probe(self.source)
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
            "input_path": self.source, "probe": info, "format": info["format"],
            "output_location": out,
            "video_streams": [s for s in info["streams"] if s["codec_type"] == "video"],
            "audio_streams": [], "subtitle_streams": [],
            "output_ext": "mkv",
            # color_range_choice is REQUIRED by the strict builders: without it a
            # source whose range FFmpeg does not report raises
            # ColorRangeUnresolvedError. Synthetic lavfi sources report "tv" on
            # FFmpeg 8.1 and nothing on 6.1, so omitting it passes here and
            # errors on the project's declared floor (B16).
            "color_range_choice": "tv",
            "video_encoder": "libx264", "crf": 28, "preset": "ultrafast",
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True,
        }
        answers.update(extra)
        answers["cmd"] = FFmWiz.build_ffmpeg_command(dict(answers))
        answers["output_path"] = out / "src_Encode.mkv"

        # A real multi-segment run has to stay fast, so the chunk size is
        # injected rather than using a 60+ second fixture.
        real_split = L00_split.split_ranges_for_reverse_segments
        commands = []
        real_runner = encoding.run_ffmpeg_with_progress

        def small_split(ranges, duration, seconds=None):
            return real_split(ranges, duration, chunk_seconds)

        def spy(cmd, **kwargs):
            commands.append([str(part) for part in cmd])
            return real_runner(cmd, **kwargs)

        encoding.split_ranges_for_reverse_segments = small_split
        encoding.run_ffmpeg_with_progress = spy
        try:
            code, _elapsed = encoding.run_segmented_reverse_main_encode(answers)
        finally:
            encoding.split_ranges_for_reverse_segments = real_split
            encoding.run_ffmpeg_with_progress = real_runner
        self.assertEqual(0, code)
        return Path(answers["output_path"]), commands

    def _halves(self, path):
        duration = float(self._probe(path)["format"]["duration"])
        return [self._colour_at(path, duration * fraction)
                for fraction in (0.1, 0.4, 0.6, 0.9)]

    def test_a_two_chunk_reverse_is_not_the_same_half_twice(self):
        output, _commands = self._run("full", 2.0)
        self.assertEqual(["blue", "blue", "red", "red"], self._halves(output))

    def test_a_kept_range_returns_that_range_not_the_source_tail(self):
        output, _commands = self._run("keep02", 2.0, cut_keep_ranges=[(0.0, 2.0)])
        colours = set(self._halves(output)) - {"missing"}
        self.assertEqual({"red"}, colours,
                         "keeping 0-2 s of a red half must return red")

    def test_a_mid_range_excludes_frames_after_its_end(self):
        output, _commands = self._run("keep12", 2.0, cut_keep_ranges=[(1.0, 2.0)])
        colours = set(self._halves(output)) - {"missing"}
        self.assertEqual({"red"}, colours,
                         "keeping 1-2 s must not reach the 2-4 s blue half")

    def test_every_segment_bounds_its_decode_before_reverse(self):
        _output, commands = self._run("argv", 2.0)
        segments = [cmd for cmd in commands if "concat" not in cmd]
        self.assertEqual(2, len(segments), "expected two real segment encodes")
        for index, cmd in enumerate(segments, start=1):
            first_input = cmd.index("-i")
            self.assertIn("-t", cmd[:first_input],
                          f"segment {index} does not bound its source decode")
            after = cmd[first_input:]
            self.assertNotIn("-t", after,
                             f"segment {index} still truncates the reversed result")

    def test_the_second_segment_seeks_as_well_as_bounds(self):
        _output, commands = self._run("argv2", 2.0)
        segments = [cmd for cmd in commands if "concat" not in cmd]
        pre_input = segments[1][:segments[1].index("-i")]
        self.assertIn("-ss", pre_input)
        self.assertIn("-t", pre_input)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")
class TheChunkPlanCoversEveryKeptSecond(unittest.TestCase):
    """Cheap guard on the planner the executor relies on."""

    def test_chunks_tile_the_kept_range_without_gaps(self):
        chunks = FFmWiz.split_ranges_for_reverse_segments([(0.0, 4.0)], 4.0, 2.0)
        self.assertEqual([(0.0, 2.0), (2.0, 4.0)], chunks)

    def test_a_hole_is_not_bridged(self):
        chunks = FFmWiz.split_ranges_for_reverse_segments(
            [(0.0, 1.0), (3.0, 4.0)], 4.0, 2.0)
        self.assertEqual([(0.0, 1.0), (3.0, 4.0)], chunks)


if __name__ == "__main__":
    unittest.main()
