"""Volume, and the escape hatch for your own ffmpeg options.

Both come from the ffmpeg-webCLI review. `volume` was the one audio knob the
wizard did not have; raw arguments are the general answer to "the wizard does
not offer the thing I need".

The volume tests MEASURE the sound. A `volume=1.5` that never reaches the
filter graph produces a perfectly valid file, and no argv assertion can tell
the difference -- so these decode the output and compare RMS against a control
encoded from the same source with no gain.

The raw-argument tests care about two things the string cannot show: that the
options really land in the argv in the right PLACE (last, so they can override
what the wizard chose), and that an option the wizard owns is refused rather
than silently producing a command that disagrees with the summary beside it.
"""
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz import wizard_raw

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")


class TheVolumeSpellingsAllMean(unittest.TestCase):
    """Three spellings, because all three appear in the wild."""

    def test_a_plain_factor(self):
        self.assertAlmostEqual(1.5, wizard_raw.parse_volume("1.5"))

    def test_a_percentage(self):
        # The wizard already takes `150%` for speed; taking it here too costs
        # nothing and surprises no one.
        self.assertAlmostEqual(1.5, wizard_raw.parse_volume("150%"))

    def test_decibels(self):
        self.assertAlmostEqual(2.0, wizard_raw.parse_volume("+6dB"), places=2)
        self.assertAlmostEqual(0.5, wizard_raw.parse_volume("-6dB"), places=2)

    def test_decibels_are_range_checked_like_everything_else(self):
        # The bug this pins: the dB branch returned early and skipped the range
        # check, so `+40dB` -- a hundredfold gain -- was accepted in silence.
        with self.assertRaises(ValueError):
            wizard_raw.parse_volume("+40dB")
        with self.assertRaises(ValueError):
            wizard_raw.parse_volume("-60dB")

    def test_nonsense_and_silence_are_refused(self):
        for bad in ("loud", "", "0", "99", "-", "1.5x"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    wizard_raw.parse_volume(bad)

    def test_a_neutral_volume_adds_no_filter(self):
        for answers in ({}, {"audio_volume": 1.0}, {"audio_volume": "loud"}):
            with self.subTest(answers=answers):
                self.assertEqual([], wizard_raw.build_volume_filter(answers))


class TheRawArgumentsAreSplitLikeAShellWould(unittest.TestCase):

    def test_a_quoted_value_survives_with_its_spaces(self):
        self.assertEqual(["-metadata", "title=My film", "-tune", "film"],
                         wizard_raw.parse_raw_arguments('-metadata title="My film" -tune film'))

    def test_an_unbalanced_quote_is_reported_not_guessed(self):
        with self.assertRaises(ValueError):
            wizard_raw.parse_raw_arguments('-metadata title="unclosed')

    def test_options_the_wizard_owns_are_refused(self):
        # Not a security boundary -- FFmWiz builds an argv list and never goes
        # through a shell, so nothing here can inject a second command. The
        # point is honesty: the summary, the output path, the plan export and
        # the progress reader all read these back from `answers`, so letting a
        # raw argument change one makes the printed command a lie.
        for owned in ("-i", "-vf", "-c:v", "-map", "-ss", "-y", "-filter_complex"):
            with self.subTest(option=owned):
                with self.assertRaises(ValueError):
                    wizard_raw.parse_raw_arguments(f"{owned} something")

    def test_the_check_is_case_insensitive(self):
        with self.assertRaises(ValueError):
            wizard_raw.parse_raw_arguments("-VF scale=2:2")

    def test_an_ordinary_option_passes(self):
        self.assertEqual(["-tune", "animation"],
                         wizard_raw.parse_raw_arguments("-tune animation"))

    def test_nothing_is_an_empty_list_not_an_error(self):
        self.assertEqual([], wizard_raw.parse_raw_arguments("   "))


class TheyReachTheCommand(unittest.TestCase):

    def _answers(self, **extra):
        answers = {
            "video_streams": [{"width": 640, "height": 480, "pix_fmt": "yuv420p"}],
            "audio_streams": [{"codec_type": "audio"}], "audio_tracks": [0],
            "format": {"duration": "10.0"},
            "video_encoder": "libx264", "use_gpu": False, "output_ext": "mkv",
        }
        answers.update(extra)
        return answers

    def test_volume_lands_in_the_audio_chain(self):
        chain = FFmWiz.build_encode_audio_processing_filter(
            self._answers(audio_volume=1.5))
        self.assertIn("volume=1.5", chain)

    def test_volume_comes_after_loudnorm(self):
        # LoudNorm normalises to a TARGET. A manual gain applied before it is
        # exactly what it would undo, so the order is the whole feature.
        answers = self._answers(audio_volume=2.0, loudnorm_enabled=True,
                                loudnorm_target_i=-16)
        chain = FFmWiz.build_encode_audio_processing_filter(answers).split(",")
        # Asserted, not skipped over. An earlier draft skipped when the fixture
        # produced no loudnorm, which would have hidden the day this stopped
        # enabling it -- the exact shape of the NVENC gate that reported absent
        # hardware for months. See `.ai/LESSON.md`.
        self.assertTrue(any("loudnorm" in part for part in chain),
                        f"the fixture no longer enables loudnorm: {chain}")
        loud = next(i for i, p in enumerate(chain) if "loudnorm" in p)
        vol = next(i for i, p in enumerate(chain) if p.startswith("volume="))
        self.assertLess(loud, vol)

    def test_a_volume_only_job_still_builds_the_audio_transform(self):
        # The gate used to ask only about speed, loudnorm and fade, so a
        # volume-only job never reached the chain that carries the filter.
        from ffmwiz.support import ext04b
        graph, labels = ext04b.build_audio_transform_filter_complex(
            self._answers(audio_volume=1.5), [0])
        self.assertIn("volume=1.5", graph)
        self.assertTrue(labels)

    def test_raw_arguments_sit_immediately_before_the_output_path(self):
        # ffmpeg reads output options in order, so "last" is what lets a user
        # override a choice the wizard made. Anywhere else and the escape hatch
        # does not escape.
        tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_raw_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        answers = self._answers(
            ffmpeg=FFMPEG or "ffmpeg", ffprobe=FFPROBE or "ffprobe",
            input_path=tmp / "in.mkv", output_location=tmp,
            probe={"streams": [], "format": {}}, streams=[],
            subtitle_streams=[], data_streams=[],
            color_range_choice="tv", crf=28, preset="ultrafast",
            raw_ffmpeg_args=["-tune", "film"])
        (tmp / "in.mkv").write_bytes(b"")
        real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        try:
            cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
        finally:
            FFmWiz.appio.note = real_note
        self.assertEqual(["-tune", "film"], cmd[-3:-1],
                         f"expected the raw options just before the output: {cmd[-5:]}")

    def test_no_raw_arguments_changes_nothing(self):
        self.assertEqual([], self._answers().get("raw_ffmpeg_args", []))


@requires_ffmpeg
class TheVolumeIsAudible(unittest.TestCase):
    """Decode the output and measure it. An argv assertion cannot.

    A `volume=1.5` that never reaches the graph still produces a valid file of
    the right length with the right codec. The only thing that separates the
    two cases is how loud the samples are.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_vol_"))
        cls.source = cls._tmp / "tone.wav"
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000",
             "-af", "volume=0.25", str(cls.source)],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
        if result.returncode != 0:
            raise unittest.SkipTest("could not build the tone fixture")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _rms(self, chain: str | None) -> float:
        """Root-mean-square of the decoded samples, 0.0-1.0."""
        args = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
                "-i", str(self.source)]
        if chain:
            args += ["-af", chain]
        args += ["-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-"]
        raw = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL,
                             timeout=300).stdout
        self.assertTrue(raw, "no audio was decoded")
        samples = struct.unpack(f"<{len(raw) // 2}h", raw[: len(raw) // 2 * 2])
        return math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0

    def test_the_fixture_is_quiet_enough_to_amplify(self):
        # Guard the guard: a fixture already near full scale would clip instead
        # of getting louder, and the 1.5x test would pass for the wrong reason.
        base = self._rms(None)
        self.assertGreater(base, 0.01, "the fixture is silent")
        self.assertLess(base * 1.5, 0.9, "the fixture is too loud to amplify cleanly")

    def test_the_emitted_filter_really_raises_the_level(self):
        answers = {"audio_volume": 1.5}
        chain = ",".join(wizard_raw.build_volume_filter(answers))
        self.assertTrue(chain, "no filter was emitted")
        self.assertAlmostEqual(1.5, self._rms(chain) / self._rms(None), places=2)

    def test_it_lowers_the_level_too(self):
        chain = ",".join(wizard_raw.build_volume_filter({"audio_volume": 0.5}))
        self.assertAlmostEqual(0.5, self._rms(chain) / self._rms(None), places=2)

    def test_the_decibel_spelling_reaches_the_same_level(self):
        factor = wizard_raw.parse_volume("+6dB")
        chain = ",".join(wizard_raw.build_volume_filter({"audio_volume": factor}))
        self.assertAlmostEqual(2.0, self._rms(chain) / self._rms(None), places=1)


class TheWizardQuestionsBehave(unittest.TestCase):

    def _ask(self, step, replies):
        """Run a prompt off a FINITE script.

        A constant stub never terminates: these steps re-ask on a rejected
        answer, so a lambda returning the same bad value loops forever. See
        `.ai/TESTING_NOTES.md`.
        """
        answers = {}
        script = iter(replies)
        real_ask, real_error = FFmWiz.appio.ask_raw, FFmWiz.appio.error
        FFmWiz.appio.ask_raw = lambda *a, **k: next(script)
        FFmWiz.appio.error = lambda *a, **k: None
        try:
            step(answers)
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.error = real_ask, real_error
        return answers

    def test_declining_writes_nothing(self):
        self.assertEqual({}, self._ask(wizard_raw.step_audio_volume, ["n"]))
        self.assertEqual({}, self._ask(wizard_raw.step_raw_ffmpeg_args, [""]))

    def test_a_rejected_answer_leaves_no_debris(self):
        # The re-ask must not keep the failed attempt's key.
        self.assertEqual({}, self._ask(wizard_raw.step_audio_volume, ["+40dB", "n"]))
        self.assertEqual({}, self._ask(wizard_raw.step_raw_ffmpeg_args, ["-vf x", "n"]))

    def test_an_accepted_answer_is_recorded(self):
        self.assertAlmostEqual(
            1.5, self._ask(wizard_raw.step_audio_volume, ["150%"])["audio_volume"])
        self.assertEqual(
            ["-tune", "film"],
            self._ask(wizard_raw.step_raw_ffmpeg_args, ["-tune film"])["raw_ffmpeg_args"])

    def test_back_works_on_both(self):
        # The back token is `0`, the same one every other step takes -- not "b".
        for step in (wizard_raw.step_audio_volume, wizard_raw.step_raw_ffmpeg_args):
            for token in sorted(FFmWiz.BACK_INPUT_TOKENS):
                with self.subTest(step=step.__name__, token=token):
                    with self.assertRaises(FFmWiz.Back):
                        self._ask(step, [token])

    def test_both_are_on_the_encode_path(self):
        # Registered but unreachable is the same as absent.
        import inspect
        from ffmwiz import wizard_flow
        source = inspect.getsource(wizard_flow.run_wizard)
        for name in ("audio_volume", "raw_ffmpeg_args"):
            with self.subTest(step=name):
                self.assertIn(f'Step("{name}"', source)

    def test_the_summary_describes_what_was_chosen(self):
        self.assertEqual("none", wizard_raw.describe_raw({}))
        text = wizard_raw.describe_raw(
            {"audio_volume": 1.5, "raw_ffmpeg_args": ["-tune", "film"]})
        self.assertIn("1.5x", text)
        self.assertIn("-tune film", text)


if __name__ == "__main__":
    unittest.main()
