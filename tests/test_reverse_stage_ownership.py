"""Regression: a staged reverse applies each edit exactly once (B01).

`run_bounded_reverse_pipeline()` made its forward-join and final-split stages
neutral by clearing the VIDEO edits and the cuts. It cleared no audio
transformation at all -- not `audio_speed_*`, not `reverse_audio`, not the
loudnorm settings. Stage 1 therefore applied an independent audio speed, the
segmented reverse applied it again, and the final split could apply it a third
time.

Measured on two joined 2 s clips with an independent 2x audio speed, where one
application owes about 2 s of audio against 4 s of video:

    before   video 4.100 s, audio 1.111 s     (2x applied twice)
    after    video 4.100 s, audio 2.133 s

The repair is a declared stage contract rather than a longer list of keys to
pop: each stage names the transformations it OWNS, `stage_answers()` strips
everything else, and `validate_stage_plan()` refuses a plan in which two stages
claim the same transformation. Ownership is never inferred from what happens to
survive in a copied answers dictionary.
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

EVERY_EDIT = {
    "cut_keep_ranges": [(0.0, 1.0)],
    "audio_cut_keep_ranges": [(0.0, 1.0)],
    "audio_cut_stream_copy": True,
    "video_speed_enabled": True, "video_speed_factor": 2.0,
    "audio_speed_enabled": True, "audio_speed_factor": 3.0,
    "audio_speed_from_video": True,
    "reverse_video": True, "reverse_audio": True,
    "loudnorm_enabled": True, "loudnorm_mode": "two_pass",
    "loudnorm_measured": {"input_i": "-20"}, "loudnorm_target_i": -16.0,
    "separator_points": [1.0], "split_output_paths": ["a.mkv"],
    "split_part_intervals": [(0.0, 1.0)],
}


class AStageCarriesOnlyWhatItOwns(unittest.TestCase):
    # Keys that must be NEUTRALISED rather than removed, so a caller that
    # reads them without checking the enabled flag sees "no transformation".
    NEUTRAL = {"video_speed_factor": 1.0, "audio_speed_factor": 1.0,
               "loudnorm_mode": "off"}

    def test_owning_nothing_clears_every_transformation(self):
        staged = FFmWiz.stage_answers(dict(EVERY_EDIT), owns=())
        for name, keys in FFmWiz.STAGE_TRANSFORMATIONS.items():
            for key in keys:
                with self.subTest(transformation=name, key=key):
                    if key in self.NEUTRAL:
                        self.assertEqual(self.NEUTRAL[key], staged.get(key),
                                         f"{key} was not neutralised")
                    else:
                        self.assertFalse(
                            staged.get(key),
                            f"{key} survived into a stage that owns nothing")

    def test_the_neutral_set_covers_what_the_source_declares(self):
        # Guard the guard: if a new neutral key appeared in the product and not
        # here, the loop above would silently stop checking it properly.
        self.assertEqual(set(self.NEUTRAL), set(encoding._NEUTRAL_VALUES))

    def test_an_owned_transformation_is_kept_intact(self):
        staged = FFmWiz.stage_answers(dict(EVERY_EDIT), owns=("audio_speed",))
        self.assertTrue(staged["audio_speed_enabled"])
        self.assertEqual(3.0, staged["audio_speed_factor"])
        self.assertTrue(staged["audio_speed_from_video"])

    def test_an_unowned_neighbour_is_still_cleared(self):
        staged = FFmWiz.stage_answers(dict(EVERY_EDIT), owns=("audio_speed",))
        self.assertFalse(staged.get("reverse_audio"))
        self.assertFalse(staged.get("loudnorm_enabled"))
        self.assertFalse(staged.get("cut_keep_ranges"))

    def test_a_speed_factor_is_neutralised_rather_than_removed(self):
        # A caller that reads the factor without checking the enabled flag must
        # see 1.0, not a stale 3.0 or a KeyError.
        staged = FFmWiz.stage_answers(dict(EVERY_EDIT), owns=())
        self.assertEqual(1.0, staged["video_speed_factor"])
        self.assertEqual(1.0, staged["audio_speed_factor"])
        self.assertEqual("off", staged["loudnorm_mode"])

    def test_unrelated_answers_are_untouched(self):
        staged = FFmWiz.stage_answers(
            {**EVERY_EDIT, "output_ext": "mkv", "crf": 28}, owns=())
        self.assertEqual("mkv", staged["output_ext"])
        self.assertEqual(28, staged["crf"])

    def test_the_effective_map_does_not_travel_between_stages(self):
        answers = dict(EVERY_EDIT)
        FFmWiz.effective_settings(answers)["video_codec"] = "libx265"
        staged = FFmWiz.stage_answers(answers, owns=())
        self.assertNotIn(FFmWiz.EFFECTIVE_SETTINGS_KEY, staged)

    def test_an_unknown_transformation_name_is_rejected(self):
        with self.assertRaises(ValueError):
            FFmWiz.stage_answers({}, owns=("not_a_transformation",))


class APlanCannotApplyOneEditTwice(unittest.TestCase):
    def test_a_duplicate_owner_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            FFmWiz.validate_stage_plan([("join", ("audio_speed",)),
                                        ("reverse", ("audio_speed",))])
        self.assertIn("audio_speed", str(caught.exception))

    def test_a_clean_plan_is_accepted(self):
        FFmWiz.validate_stage_plan([("join", ()), ("reverse", ("cuts", "audio_speed")),
                                    ("split", ("split",))])

    def test_every_transformation_is_nameable(self):
        # Guard the guard: a plan that names nothing would validate trivially.
        self.assertIn("audio_speed", FFmWiz.STAGE_TRANSFORMATIONS)
        self.assertIn("loudnorm", FFmWiz.STAGE_TRANSFORMATIONS)
        self.assertIn("audio_reverse", FFmWiz.STAGE_TRANSFORMATIONS)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")
class TheFinishedMediaShowsOneApplication(NoLeakedArtifacts, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_stageown_"))
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

    def _item(self, path):
        info = self._probe(path)
        return {"path": path, "probe": info, "format": info["format"],
                "streams": info["streams"],
                "video_streams": [s for s in info["streams"] if s["codec_type"] == "video"],
                "audio_streams": [s for s in info["streams"] if s["codec_type"] == "audio"],
                "subtitle_streams": [], "data_streams": [], "duration": 2.0}

    def _span(self, path, kind):
        info = self._probe(path)
        for stream in info["streams"]:
            if stream["codec_type"] == kind:
                return FFmWiz.video_stream_span_seconds(stream, info["format"])
        return 0.0

    def _run(self, label, **extra):
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
            "reverse_video": True,
            "video_speed_enabled": True, "video_speed_factor": 1.0,
        })
        answers.update(extra)
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            answers["cmd"] = [str(p) for p in FFmWiz.build_join_encode_command(
                dict(answers), items, out / "o.mkv")]
            answers["output_path"] = (answers.get("split_output_paths")
                                      or [out / "o.mkv"])[0]
            code, _elapsed = encoding.execute_encode_plan(
                answers, answers["cmd"], total_duration=4.0, label="test")
        self.assertEqual(0, code, noise.getvalue()[-1500:])
        return sorted(out.glob("*.mkv"))

    def test_an_independent_audio_speed_is_applied_once(self):
        outputs = self._run("audio2x", audio_speed_enabled=True,
                            audio_speed_factor=2.0)
        self.assertEqual(1, len(outputs))
        video = self._span(outputs[0], "video")
        audio = self._span(outputs[0], "audio")
        self.assertAlmostEqual(
            audio, video / 2.0, delta=0.4,
            msg=f"video {video:.3f}s against audio {audio:.3f}s -- one 2x "
                "application owes about half; a second would owe a quarter")

    def test_it_is_applied_once_with_a_split_too(self):
        outputs = self._run("audio2xsplit", audio_speed_enabled=True,
                            audio_speed_factor=2.0, separator_points=[1.0])
        self.assertEqual(2, len(outputs))
        video = sum(self._span(part, "video") for part in outputs)
        audio = sum(self._span(part, "audio") for part in outputs)
        self.assertAlmostEqual(
            audio, video / 2.0, delta=0.6,
            msg=f"video {video:.3f}s against audio {audio:.3f}s across the parts")

    def test_no_speed_change_leaves_the_two_streams_together(self):
        # Guard the guard: without the edit the ratio is 1, so the assertions
        # above have to be measuring the edit and not the fixture.
        outputs = self._run("plain")
        video = self._span(outputs[0], "video")
        audio = self._span(outputs[0], "audio")
        self.assertAlmostEqual(audio, video, delta=0.4)


if __name__ == "__main__":
    unittest.main()
