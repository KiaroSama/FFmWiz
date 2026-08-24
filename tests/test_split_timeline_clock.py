"""Regression: Split points are PROCESSED-clock seconds (F02).

`build_ffmpeg_command` plans the parts on the final timeline, but the public
dispatcher threw that command away for a single-input Split and called
`run_separator_main_encode()`, whose `build_separator_job_specs()` reads the
same numbers as SOURCE seconds. The two clocks only coincide when nothing has
moved them.

Measured on a 4 s source, red 0-2 s then blue 2-4 s:

    2x, split at the 1.0 s processed point
        before: parts of 0.700 s and 1.700 s
        after : parts of 1.200 s and 1.200 s

    reverse, split at 2.0 s  (the reversed timeline starts blue)
        before: Part01 red,  Part02 blue     <- original order, backwards
        after : Part01 blue, Part02 red

These drive `FFmWiz.run_one_job()`, not the builder: the defect lives in the
dispatcher's choice of executor, so a test that runs the prebuilt command
cannot see it.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from ffmwiz import encoding

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


class WhichExecutorOwnsTheSplit(unittest.TestCase):
    """The predicate alone, so the rule is pinned without an encode."""

    def test_an_untouched_timeline_may_use_the_per_part_executor(self):
        self.assertTrue(FFmWiz.split_parts_share_the_source_clock(
            {"separator_points": [1.0]}))

    def test_a_speed_change_moves_the_clock(self):
        self.assertFalse(FFmWiz.split_parts_share_the_source_clock(
            {"separator_points": [1.0], "video_speed_enabled": True,
             "video_speed_factor": 2.0}))

    def test_reverse_moves_the_clock(self):
        self.assertFalse(FFmWiz.split_parts_share_the_source_clock(
            {"separator_points": [1.0], "reverse_video": True}))

    def test_a_cut_moves_the_clock(self):
        self.assertFalse(FFmWiz.split_parts_share_the_source_clock(
            {"separator_points": [1.0], "cut_keep_ranges": [(0.0, 2.0)]}))

    def test_the_spec_builder_refuses_a_moved_clock_too(self):
        # Defence in depth: even called directly it must not rebuild source
        # ranges from processed-clock points.
        specs = FFmWiz.build_separator_job_specs({
            "separator_points": [1.0], "reverse_video": True,
            "format": {"duration": "4.0"},
        })
        self.assertEqual([], specs)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")
class TheRealDispatcherProducesTheRightParts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_splitclock_"))
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

    def setUp(self):
        self._real_menu = FFmWiz.ask_main_menu
        self._real_wizard = FFmWiz.run_wizard
        self._real_plan = FFmWiz.print_ffmpeg_processing_plan
        FFmWiz.ask_main_menu = lambda answers, config_path: 1
        FFmWiz.print_ffmpeg_processing_plan = lambda *a, **k: None

    def tearDown(self):
        FFmWiz.ask_main_menu = self._real_menu
        FFmWiz.run_wizard = self._real_wizard
        FFmWiz.print_ffmpeg_processing_plan = self._real_plan

    def _probe(self, path):
        return json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_format", "-of", "json", str(path)],
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

    def _dispatch(self, label, **extra):
        out = self._tmp / label
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        info = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(self.source)],
            capture_output=True, text=True, timeout=120).stdout)
        prepared = {
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
            "start_now": True,
        }
        prepared.update(extra)

        def fake_wizard(answers, config=None):
            answers.update(prepared)
            answers["cmd"] = FFmWiz.build_ffmpeg_command(answers)

        FFmWiz.run_wizard = fake_wizard
        result = FFmWiz.run_one_job({}, out / "cfg.json")
        self.assertIsNotNone(result, "the dispatcher returned nothing")
        self.assertEqual(0, result[0], "the dispatcher's encode failed")
        return sorted(out.glob("*Part*.mkv"))

    def _durations(self, parts):
        return [round(float(self._probe(p)["format"]["duration"]), 1) for p in parts]

    def test_a_speed_split_produces_equal_parts_on_the_processed_clock(self):
        parts = self._dispatch("speed", separator_points=[1.0],
                               video_speed_enabled=True, video_speed_factor=2.0)
        self.assertEqual(2, len(parts))
        first, second = self._durations(parts)
        self.assertAlmostEqual(first, second, delta=0.35,
                               msg=f"parts should be near-equal, got {first} and {second}")

    def test_a_slow_motion_split_is_not_truncated(self):
        parts = self._dispatch("slow", separator_points=[4.0],
                               video_speed_enabled=True, video_speed_factor=0.5)
        total = sum(float(self._probe(p)["format"]["duration"]) for p in parts)
        self.assertGreater(total, 7.0,
                           "a 4 s source at 0.5x owes about 8 s of output")

    def test_a_reversed_split_puts_the_end_of_the_source_first(self):
        parts = self._dispatch("reverse", separator_points=[2.0],
                               video_speed_enabled=True, video_speed_factor=1.0,
                               reverse_video=True)
        self.assertEqual(2, len(parts))
        first = self._colour_at(parts[0], 0.5)
        second = self._colour_at(parts[1], 0.5)
        self.assertEqual("blue", first, "Part 1 must hold the END of the source")
        self.assertEqual("red", second, "Part 2 must hold the BEGINNING")

    def test_a_split_after_a_removed_hole_lands_on_the_kept_timeline(self):
        parts = self._dispatch("hole", separator_points=[1.5],
                               cut_keep_ranges=[(0.0, 1.0), (2.0, 4.0)])
        self.assertEqual(2, len(parts))
        for part, seconds in zip(parts, self._durations(parts)):
            self.assertAlmostEqual(
                seconds, 1.5, delta=0.35,
                msg=f"{part.name} should be ~1.5 s of the 3 s kept timeline")

    def test_a_transformed_split_reaches_the_progress_aware_path(self):
        # Both guards refuse the per-part rebuild, so the OUTPUT is correct
        # either way -- but only the lower dispatcher branch hands the runner
        # the per-part durations and output paths. Routed through
        # `run_separator_main_encode`'s empty-spec fallback instead, every
        # transformed Split runs with `total_duration=None` and no per-part
        # progress at all.
        seen = {}
        real = encoding.run_ffmpeg_with_progress

        def spy(cmd, **kwargs):
            seen.update(kwargs)
            return real(cmd, **kwargs)

        encoding.run_ffmpeg_with_progress = spy
        try:
            self._dispatch("progress", separator_points=[1.0],
                           video_speed_enabled=True, video_speed_factor=2.0)
        finally:
            encoding.run_ffmpeg_with_progress = real
        self.assertTrue(seen.get("split_progress_part_durations"),
                        "the runner was given no per-part durations")
        self.assertTrue(seen.get("progress_output_paths"),
                        "the runner was given no per-part output paths")
        self.assertIsNotNone(seen.get("total_duration"),
                             "the progress bar has nothing to divide by")

    def test_cut_speed_reverse_and_split_together_match_a_reference_encode(self):
        # The composite case. Keep 0-1 (red) and 2-4 (blue) = 3 s, run it at
        # 2x = 1.5 s, reverse it (blue first), then split at the 1.0 s
        # processed point. Compared against the SAME edit without a split.
        #
        # Durations are compared with a frame-count tolerance, not exactly:
        # every output file carries two extra frames of container accounting,
        # measured as +0.200 s at 10 fps and +0.066 s at 30 fps -- the same two
        # frames, so it scales with the source and is not drift. Two parts
        # therefore owe four frames where one reference file owes two.
        edit = dict(cut_keep_ranges=[(0.0, 1.0), (2.0, 4.0)],
                    video_speed_enabled=True, video_speed_factor=2.0,
                    reverse_video=True)
        reference = self._dispatch("compref", **edit)
        if not reference:
            reference = sorted((self._tmp / "compref").glob("*.mkv"))
        self.assertTrue(reference, "the reference encode produced nothing")
        reference_seconds = sum(
            float(self._probe(part)["format"]["duration"]) for part in reference)

        parts = self._dispatch("comp", separator_points=[1.0], **edit)
        self.assertEqual(2, len(parts))
        frame = 1.0 / 10.0  # the fixture's rate
        total = sum(float(self._probe(part)["format"]["duration"]) for part in parts)
        self.assertAlmostEqual(
            total, reference_seconds, delta=4 * frame,
            msg=f"split total {total:.3f}s vs reference {reference_seconds:.3f}s")

        # Order and content: reverse puts the blue tail of the source first.
        self.assertEqual("blue", self._colour_at(parts[0], 0.3))
        self.assertEqual("red", self._colour_at(parts[1], 0.3))

        # And the proportions survive: 2 s of blue against 1 s of red, halved.
        blue, red = (float(self._probe(part)["format"]["duration"])
                     for part in parts)
        self.assertGreater(blue, red,
                           "the blue part covers twice as much source as the red one")

    def test_an_untransformed_split_still_works(self):
        # The per-part executor keeps this case; it must not have been broken.
        parts = self._dispatch("plain", separator_points=[2.0])
        self.assertEqual(2, len(parts))
        self.assertEqual("red", self._colour_at(parts[0], 0.5))
        self.assertEqual("blue", self._colour_at(parts[1], 0.5))


if __name__ == "__main__":
    unittest.main()
