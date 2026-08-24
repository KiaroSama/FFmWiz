"""Regression: reverse must never be handed a whole timeline (F10).

`reverse` holds every decoded frame of its input. Across a join that is the sum
of every input, so the one-pass plan was not a memory characteristic to warn
about -- an hour of joined 1080p30 is roughly 336 GiB of decoded frames, and the
job simply cannot finish. Peak RSS was measured to track that frame total almost
exactly (720p30: 300 frames -> 878 MB, 600 frames -> 1306 MB).

The segmented executor cannot be aimed at a join directly: it rebuilds each
segment with the SINGLE-input builder, which reverses input 1 alone (R01). So
the join runs FORWARD into a leased intermediate first, and the bounded reverse
then treats that as the ordinary single-input case.

Measured on red+440 Hz joined with blue+880 Hz, reversed:

    one pass (before)   4.063 s, blue then red
    bounded (after)     4.130 s, blue then red

Same answer, bounded plan. The intermediate is leased, so it is removed with
the rest of the job's artifacts.
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
from ffmwiz.support import L00_split

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


class TheSelectorRoutesAJoinedReverse(unittest.TestCase):
    """Which plan is chosen, without an encode."""

    def setUp(self):
        self.bounded = []
        self.segmented = []
        self.one_shot = []
        self.notes = []
        self._real_bounded = encoding.run_bounded_reverse_pipeline
        self._real_segmented = encoding.run_segmented_reverse_main_encode
        self._real_runner = encoding.run_ffmpeg_with_progress
        self._real_note = FFmWiz.appio.note
        encoding.run_bounded_reverse_pipeline = (
            lambda answers: (self.bounded.append(answers) or (0, 0.0)))
        encoding.run_segmented_reverse_main_encode = (
            lambda answers: (self.segmented.append(answers) or (0, 0.0)))
        encoding.run_ffmpeg_with_progress = (
            lambda cmd, **kwargs: (self.one_shot.append(cmd) or (0, 0.0)))
        FFmWiz.appio.note = self.notes.append

    def tearDown(self):
        encoding.run_bounded_reverse_pipeline = self._real_bounded
        encoding.run_segmented_reverse_main_encode = self._real_segmented
        encoding.run_ffmpeg_with_progress = self._real_runner
        FFmWiz.appio.note = self._real_note

    def _run(self, **extra):
        answers = {
            "video_streams": [{"codec_type": "video", "codec_name": "h264"}],
            "audio_streams": [], "output_ext": "mkv",
            # color_range_choice is REQUIRED by the strict builders: without it a
            # source whose range FFmpeg does not report raises
            # ColorRangeUnresolvedError. Synthetic lavfi sources report "tv" on
            # FFmpeg 8.1 and nothing on 6.1, so omitting it passes here and
            # errors on the project's declared floor (B16).
            "color_range_choice": "tv",
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True,
        }
        answers.update(extra)
        encoding.execute_encode_plan(answers, ["ffmpeg", "-i", "a.mkv", "out.mkv"],
                                     total_duration=1.0, label="test")
        return answers

    def test_a_joined_reverse_takes_the_bounded_plan(self):
        self._run(join_input_items=[{"path": "b.mkv"}])
        self.assertEqual(1, len(self.bounded))
        self.assertEqual(0, len(self.one_shot),
                         "a join must no longer run reverse in one pass")

    def test_an_ordinary_reverse_still_takes_the_segmented_plan(self):
        self._run()
        self.assertEqual(0, len(self.bounded))
        self.assertEqual(1, len(self.segmented))

    def test_a_split_join_takes_the_pipeline_as_well(self):
        self._run(join_input_items=[{"path": "b.mkv"}], separator_points=[1.0])
        self.assertEqual(1, len(self.bounded))
        self.assertEqual(0, len(self.one_shot))

    def test_a_plain_split_reverse_takes_it_too(self):
        # No join: a Split alone still hands `reverse` the whole timeline.
        self._run(separator_points=[1.0])
        self.assertEqual(1, len(self.bounded))
        self.assertEqual(0, len(self.one_shot))

    def test_a_non_reverse_join_is_untouched(self):
        self._run(join_input_items=[{"path": "b.mkv"}], reverse_video=False,
                  video_speed_enabled=False)
        self.assertEqual(0, len(self.bounded))
        self.assertEqual(1, len(self.one_shot))


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")
class TheBoundedPlanProducesTheRightFile(NoLeakedArtifacts, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_boundedjoin_"))
        cls.inputs = []
        for name, colour, freq in (("a", "red", 440), ("b", "blue", 880)):
            path = cls._tmp / f"{name}.mkv"
            subprocess.run(
                [FFMPEG, "-v", "error", "-y",
                 "-f", "lavfi", "-i", f"color=c={colour}:size=160x120:rate=30:duration=2",
                 "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=2",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-shortest", str(path)],
                check=True, capture_output=True, timeout=300)
            cls.inputs.append(path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _probe(self, path):
        return json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format",
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

    def _item(self, path):
        info = self._probe(path)
        return {"path": path, "probe": info, "format": info["format"],
                "streams": info["streams"],
                "video_streams": [s for s in info["streams"] if s["codec_type"] == "video"],
                "audio_streams": [s for s in info["streams"] if s["codec_type"] == "audio"],
                "subtitle_streams": [], "data_streams": [], "duration": 2.0}

    def _reverse(self, label, chunk_seconds=None):
        return self._reverse_with(label, chunk_seconds=chunk_seconds)

    def _reverse_with(self, label, chunk_seconds=None, **extra):
        out = self._tmp / label
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        items = [self._item(path) for path in self.inputs]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.inputs[0],
            "probe": items[0]["probe"], "format": items[0]["format"],
            "output_location": out,
            "video_streams": items[0]["video_streams"],
            "audio_streams": items[0]["audio_streams"],
            "subtitle_streams": [], "output_ext": "mkv", "audio_tracks": [0],
            "color_range_choice": "tv",
            "video_encoder": "libx264", "crf": 28, "preset": "ultrafast",
            "audio_codec": "aac", "join_input_items": items[1:],
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True,
        })
        answers.update(extra)
        answers["output_path"] = out / "bounded.mkv"
        real_split = L00_split.split_ranges_for_reverse_segments
        commands = []
        real_runner = encoding.run_ffmpeg_with_progress

        def small_split(ranges, duration, seconds=None):
            return real_split(ranges, duration, chunk_seconds or seconds)

        def spy(cmd, **kwargs):
            commands.append([str(part) for part in cmd])
            return real_runner(cmd, **kwargs)

        if chunk_seconds:
            encoding.split_ranges_for_reverse_segments = small_split
        encoding.run_ffmpeg_with_progress = spy
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                answers["cmd"] = [str(part) for part in
                                  FFmWiz.build_join_encode_command(
                                      dict(answers), items, answers["output_path"])]
                code, _elapsed = encoding.run_bounded_reverse_pipeline(answers)
        finally:
            encoding.split_ranges_for_reverse_segments = real_split
            encoding.run_ffmpeg_with_progress = real_runner
        self.assertEqual(0, code, noise.getvalue()[-1500:])
        return answers["output_path"], commands, noise.getvalue()

    def test_the_joined_reverse_plays_backwards(self):
        output, _commands, _log = self._reverse("plain")
        duration = float(self._probe(output)["format"]["duration"])
        self.assertEqual("blue", self._colour_at(output, duration * 0.25),
                         "the END of the joined timeline must come first")
        self.assertEqual("red", self._colour_at(output, duration * 0.75))

    def test_both_inputs_are_still_present(self):
        output, _commands, _log = self._reverse("length")
        duration = float(self._probe(output)["format"]["duration"])
        self.assertGreater(duration, 3.5,
                           "a 2 s + 2 s join must not come back as one input")

    def test_the_audio_survives_the_intermediate(self):
        output, _commands, _log = self._reverse("audio")
        kinds = [s["codec_type"] for s in self._probe(output)["streams"]]
        self.assertIn("audio", kinds)
        self.assertIn("video", kinds)

    def test_it_really_segments_when_the_chunk_is_small(self):
        _output, commands, _log = self._reverse("chunked", chunk_seconds=1.0)
        segments = [cmd for cmd in commands
                    if "-t" in cmd[:cmd.index("-i")] and "concat" not in cmd]
        self.assertGreaterEqual(len(segments), 2,
                                "a 1 s chunk over ~4 s should make several segments")
        for cmd in segments:
            self.assertIn("-t", cmd[:cmd.index("-i")],
                          "each segment must bound its decode before reverse")

    def test_an_edit_is_applied_exactly_once(self):
        # The intermediate must hold the PLAIN joined program. Keeping 1-4 s of
        # the 4 s join leaves 1 s of red and 2 s of blue; apply that cut to the
        # intermediate as well and the second pass keeps 1-3 s of a 3 s file --
        # 2 s of blue with the red gone. A deliberately non-idempotent range,
        # because keeping 0-3 twice would look identical either way.
        output, _commands, _log = self._reverse_with(
            "onceonly", cut_keep_ranges=[(1.0, 4.0)])
        duration = float(self._probe(output)["format"]["duration"])
        self.assertAlmostEqual(
            duration, 3.0, delta=0.4,
            msg=f"expected the 3 s kept stretch, got {duration:.3f}s")
        self.assertEqual("blue", self._colour_at(output, duration * 0.25))
        self.assertEqual("red", self._colour_at(output, duration * 0.9),
                         "the red second must survive; a doubled cut removes it")

    def test_a_split_reverse_puts_the_end_of_the_source_in_part_one(self):
        # Reversing each part on its own would return them in the ORIGINAL
        # order; reversing the whole join at once is the unbounded plan. The
        # pipeline reverses first, then cuts the parts out of the result.
        out, _commands, _log = self._reverse_with("splitrev", separator_points=[2.0])
        parts = sorted(Path(out).parent.glob("*Part*.mkv"))
        self.assertEqual(2, len(parts), f"expected two parts, got {parts}")
        self.assertEqual("blue", self._colour_at(parts[0], 0.5),
                         "Part 1 must hold the END of the joined timeline")
        self.assertEqual("red", self._colour_at(parts[1], 0.5))
        # The part files keep the SOURCE's name, not the intermediate's. The
        # summary already showed the user these paths before the run started.
        expected = Path(self.inputs[0]).stem
        for part in parts:
            self.assertTrue(part.name.startswith(expected),
                            f"{part.name} should be named after {expected}, "
                            "not after the pipeline's scratch file")

    def test_a_split_reverse_really_uses_the_bounded_executor(self):
        # The whole point: the segmented executor has to run. Before this the
        # split branch went straight to a one-shot `reverse` over everything.
        seen = []
        real = encoding.run_segmented_reverse_main_encode

        def spy(answers):
            seen.append(answers)
            return real(answers)

        encoding.run_segmented_reverse_main_encode = spy
        try:
            self._reverse_with("splitbounded", separator_points=[2.0])
        finally:
            encoding.run_segmented_reverse_main_encode = real
        self.assertEqual(1, len(seen))
        self.assertNotIn("separator_points", seen[0],
                         "the reverse stage must not also try to split")

    def test_cut_speed_reverse_and_split_are_each_applied_once(self):
        # The stage-3 discriminator. Keeping 1-4 s of the 4 s join leaves 3 s
        # (1 s red, 2 s blue); at 2x that is 1.5 s; reversed it is blue then
        # red; split at 0.75 s gives two ~0.75 s parts.
        #
        #   correct                      ~1.5 s total
        #   speed re-applied in stage 3  ~0.75 s
        #   cut re-applied in stage 3    ~0.5 s   (1-4 s of a 1.5 s file)
        #
        # Every output file also carries two frames of container padding, which
        # at 30 fps is 0.066 s each -- hence the tolerance rather than equality.
        # audio_speed_from_video matters: without it the audio keeps its
        # original length and the CONTAINER stays 3 s however short the video
        # is, which measures like a missing speed change and is not one.
        out, _commands, _log = self._reverse_with(
            "composed", cut_keep_ranges=[(1.0, 4.0)],
            video_speed_enabled=True, video_speed_factor=2.0,
            audio_speed_from_video=True, separator_points=[0.75])
        parts = sorted(Path(out).parent.glob("*Part*.mkv"))
        self.assertEqual(2, len(parts), f"expected two parts, got {parts}")
        total = sum(float(self._probe(part)["format"]["duration"]) for part in parts)
        self.assertAlmostEqual(
            total, 1.5, delta=0.4,
            msg=f"expected ~1.5 s of output, got {total:.3f}s -- an edit ran twice")
        self.assertEqual("blue", self._colour_at(parts[0], 0.3),
                         "the reversed timeline starts at the blue tail")

    def test_a_custom_output_name_survives_the_pipeline(self):
        # The final stage took its stem from `input_path`, which by then is the
        # pipeline's own scratch file: a job summarised as CustomMovie_Part01
        # actually wrote a_Part01 (B14). The names the user confirmed are the
        # contract, not the scratch file's.
        out = self._tmp / "customname"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        items = [self._item(path) for path in self.inputs]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.inputs[0],
            "probe": items[0]["probe"], "format": items[0]["format"],
            "output_location": out, "output_name_stem": "CustomMovie",
            "video_streams": items[0]["video_streams"],
            "audio_streams": items[0]["audio_streams"],
            "subtitle_streams": [], "output_ext": "mkv", "audio_tracks": [0],
            "color_range_choice": "tv",
            "video_encoder": "libx264", "crf": 28, "preset": "ultrafast",
            "audio_codec": "aac", "join_input_items": items[1:],
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True, "separator_points": [2.0],
        })
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            # The builder writes split_output_paths into the dict it is GIVEN,
            # so read them back from that one rather than the outer copy.
            built = dict(answers)
            answers["cmd"] = [str(p) for p in FFmWiz.build_join_encode_command(
                built, items, out / "CustomMovie.mkv")]
            promised = [Path(p).name for p in (built.get("split_output_paths") or [])]
            answers["split_output_paths"] = built.get("split_output_paths")
            answers["split_part_intervals"] = built.get("split_part_intervals")
            answers["output_path"] = (built.get("split_output_paths")
                                      or [out / "CustomMovie.mkv"])[0]
            code, _elapsed = encoding.run_bounded_reverse_pipeline(answers)
        self.assertEqual(0, code, noise.getvalue()[-1500:])
        self.assertTrue(promised, "the build promised no part paths to compare")
        written = sorted(part.name for part in out.glob("*Part*.mkv"))
        self.assertEqual(sorted(promised), written,
                         "the files on disk must be the ones the summary showed")

    def test_the_user_is_told_which_plan_ran(self):
        _output, _commands, log = self._reverse("note")
        self.assertIn("joining first", log.lower())

    def test_the_intermediate_does_not_outlive_the_job(self):
        # It is leased, so the guard mixin's release proves this; assert the
        # workspace is gone explicitly too, because a stray joined copy of the
        # user's material is the worst thing to leave behind.
        output, _commands, _log = self._reverse("cleanup")
        answers = self._owned_answers[-1]
        FFmWiz.release_artifacts(answers)
        leftovers = list(self._artifact_root.glob("ffmwiz_join_reverse_*"))
        self.assertEqual([], leftovers)
        self.assertTrue(output.exists(), "the real output must survive cleanup")


if __name__ == "__main__":
    unittest.main()
