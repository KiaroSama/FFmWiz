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

    def test_a_windows_path_keeps_its_separators(self):
        # The bug this pins: `shlex.split(..., posix=True)` treats `\` as an
        # escape character by default, so a Windows path pasted straight out
        # of Explorer silently lost every separator -- `C:\Users\me\logo.png`
        # became `C:Usersmelogo.png`.
        self.assertEqual(
            ["-metadata", r"comment=C:\Users\example\video"],
            wizard_raw.parse_raw_arguments(r"-metadata comment=C:\Users\example\video"))

    def test_a_quoted_value_keeps_its_spaces_and_loses_its_quotes(self):
        self.assertEqual(
            ["-metadata", "title=my film"],
            wizard_raw.parse_raw_arguments('-metadata title="my film"'))

    def test_every_per_stream_spelling_of_a_reserved_option_is_refused(self):
        # The bug this pins: the denylist matched exact strings only, so
        # `-c:v:0 libx265` walked straight past `-c`/`-c:v` and got appended
        # after the wizard's own codec choice -- ffmpeg honours the later,
        # more specific option, so the file got encoded with a codec the
        # summary never mentioned.
        for owned in ("-c:v:0", "-codec:v:0", "-vf:0", "-af:1",
                      "-map_metadata:s:0", "-c:a:1"):
            with self.subTest(option=owned):
                with self.assertRaises(ValueError):
                    wizard_raw.parse_raw_arguments(f"{owned} something")

    def test_an_unrelated_per_stream_option_is_still_allowed(self):
        # The guard must not over-block: these are ordinary per-stream
        # options nothing in the wizard owns, and a prefix match wide enough
        # to catch them would make the escape hatch useless while looking
        # like a fix.
        self.assertEqual(["-b:v:0", "2M"],
                         wizard_raw.parse_raw_arguments("-b:v:0 2M"))
        self.assertEqual(["-metadata:s:v:0", "rotate=90"],
                         wizard_raw.parse_raw_arguments("-metadata:s:v:0 rotate=90"))

    def test_the_option_file_family_is_refused(self):
        # `-fpre`/`-vpre`/`-apre` read an option file that can set anything
        # the wizard owns -- same class of bypass as `-map_channel`.
        for owned in ("-fpre", "-vpre", "-apre"):
            with self.subTest(option=owned):
                with self.assertRaises(ValueError):
                    wizard_raw.parse_raw_arguments(f"{owned} something")


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

    def test_the_gate_opens_for_a_volume_only_job(self):
        # The chain test above calls `ext04b` directly, so it cannot see the
        # gate standing in front of it -- and that gate was the actual defect:
        # a correct filter that nothing ever asked for.
        self.assertTrue(FFmWiz.audio_transform_enabled(
            self._answers(audio_volume=1.5)))

    def test_the_gate_opens_for_a_fade_only_job(self):
        # Same gate, same shape: the chain behind it emits `afade`, so a
        # fade-only job has to open it too.
        self.assertTrue(FFmWiz.audio_transform_enabled(
            self._answers(fade_out_seconds=1.5)))

    def test_the_gate_stays_shut_when_nothing_was_asked_for(self):
        # The other direction. A gate that opens for everything is not a gate,
        # and it would put an audio filter chain on every plain encode.
        self.assertFalse(FFmWiz.audio_transform_enabled(self._answers()))

    def test_the_final_reverse_mux_carries_them_when_it_owns_them(self):
        # A reverse with no Split ends in `reverse_concat_stages`, and THAT
        # command writes the file the user receives -- the per-segment encodes
        # before it write throwaway intermediates. Without this the options
        # landed only on a scratch file and never on the output.
        from ffmwiz import reverse_stages
        tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_revraw_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        segments = [tmp / "seg_00.mkv", tmp / "seg_01.mkv"]
        for segment in segments:
            segment.write_bytes(b"")
        answers = self._answers(ffmpeg="ffmpeg", audio_streams=[],
                                raw_ffmpeg_args=["-metadata", "comment=revraw"])
        stages, _warnings = reverse_stages.reverse_concat_stages(
            answers, segments, tmp, tmp / "out.mkv", 1.0, "mkv")
        final = stages[-1][1]
        self.assertEqual(["-metadata", "comment=revraw"], final[-3:-1],
                         f"expected them just before the output: {final[-5:]}")

    def test_the_final_reverse_mux_adds_nothing_when_it_owns_nothing(self):
        # With a Split the options belong to the Split stage and
        # `stage_answers` has already removed the key, so this command must
        # stay clean -- otherwise they would be applied twice.
        from ffmwiz import reverse_stages
        tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_revclean_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        segment = tmp / "seg_00.mkv"
        segment.write_bytes(b"")
        stages, _warnings = reverse_stages.reverse_concat_stages(
            self._answers(ffmpeg="ffmpeg", audio_streams=[]),
            [segment], tmp, tmp / "out.mkv", 1.0, "mkv")
        self.assertNotIn("-metadata", stages[-1][1])

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


class TheConfigKeysReachTheSameAnswers(unittest.TestCase):
    """`audio_volume` and `raw_ffmpeg_args`, documented in Appendix B.

    Mode 2 never asks either question once its config key is filled, so the
    config key is the ONLY way a non-interactive run reaches them. A key that
    is documented but not read is worse than one that does not exist -- these
    two, and `video_quick`, were in exactly that state.
    """

    def _audio_applied(self, settings):
        # `config_value` reads `config["settings"]`, not the top level.
        config = {"settings": settings}
        from ffmwiz.support import ext11
        answers = {
            "audio_streams": [{"codec_type": "audio", "codec_name": "aac",
                               "channels": 2, "sample_rate": "48000"}],
            "format": {"duration": "10.0"},
            "input_path": FFmWiz.Path("x.mkv"), "output_ext": "mkv",
            "packet_sizes": {},
        }
        real = FFmWiz.services.get_packet_sizes
        FFmWiz.services.get_packet_sizes = lambda _a: {}
        try:
            ext11.apply_config_audio_options(answers, config)
        finally:
            FFmWiz.services.get_packet_sizes = real
        return answers

    def _raw_applied(self, settings):
        from ffmwiz.support import ext12
        answers: dict = {}
        ext12.apply_config_raw_options(answers, {"settings": settings})
        return answers

    def test_audio_volume_from_config_reaches_the_answers(self):
        applied = self._audio_applied({"audio_volume": "150%"})
        self.assertAlmostEqual(wizard_raw.parse_volume("150%"),
                               applied["audio_volume"])

    def test_raw_args_from_config_reach_the_answers(self):
        text = '-metadata title="My film" -tune film'
        applied = self._raw_applied({"raw_ffmpeg_args": text})
        self.assertEqual(wizard_raw.parse_raw_arguments(text),
                         applied["raw_ffmpeg_args"])

    def test_an_invalid_audio_volume_in_config_fails_loudly(self):
        # `fail()` prints before it exits; keep that off the suite's console.
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                self._audio_applied({"audio_volume": "loud"})

    def test_an_invalid_raw_args_in_config_fails_loudly(self):
        # The bug this whole plan started from: an invalid value silently
        # ignored rather than rejected.
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                self._raw_applied({"raw_ffmpeg_args": "-vf scale=2:2"})

    def test_an_absent_audio_volume_leaves_the_job_alone(self):
        self.assertNotIn("audio_volume", self._audio_applied({}))

    def test_an_absent_raw_args_leaves_the_job_alone(self):
        self.assertNotIn("raw_ffmpeg_args", self._raw_applied({}))


class TheOptionalPromptHelperItself(unittest.TestCase):
    """Direct tests of `appio.ask_optional`, the loop all five call sites share.

    These drive the helper itself, with throwaway `forget`/`record`/`describe`
    callables, rather than one wizard step -- the behaviour under test (clear-
    before-decline, a `Back` raised from inside `record`) is the helper's own
    contract, not any one step's.
    """

    def _run(self, answers, replies, forget=None, record=None, describe=None,
             calls=1, errors=None):
        """Run `ask_optional` `calls` times off one FINITE reply queue.

        A constant stub is not safe here either: a rejected value re-asks
        within the SAME call, same as every wizard step above. A second CALL
        is a different thing -- it is the question being asked again, the way
        Back navigation revisits an earlier step -- so `calls=2` is how
        `test_declining_on_the_second_pass_still_clears_the_first` gets its
        second pass, sharing this one `answers` dict and reply queue.
        """
        collected = errors if errors is not None else []
        script = iter(replies)
        real_ask, real_error = FFmWiz.appio.ask_raw, FFmWiz.appio.error
        FFmWiz.appio.ask_raw = lambda *a, **k: next(script)
        FFmWiz.appio.error = lambda message: collected.append(message)
        try:
            for _ in range(calls):
                FFmWiz.appio.ask_optional(
                    answers, "Optional thing?", "hint",
                    forget or (lambda a: None),
                    record or (lambda v, a: True),
                    describe or (lambda a: "recorded"))
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.error = real_ask, real_error

    def test_declining_on_the_second_pass_still_clears_the_first(self):
        # The rule the helper exists to hold: `forget` runs before the
        # decline check on EVERY call, so declining on a later pass clears an
        # earlier pass's answer exactly like declining right away would.
        def forget(answers):
            answers.pop("mark", None)

        def record(value, answers):
            answers["mark"] = value
            return True

        answers = {}
        self._run(answers, ["1.5", "n"], forget=forget, record=record, calls=2)
        self.assertEqual({}, answers)

    def test_a_value_error_re_asks_instead_of_raising(self):
        attempts = []

        def record(value, answers):
            attempts.append(value)
            if len(attempts) == 1:
                raise ValueError("bad")
            answers["value"] = value
            return True

        answers = {}
        errors = []
        self._run(answers, ["bogus", "good"], record=record, errors=errors)
        self.assertEqual(1, len(errors))
        self.assertEqual("good", answers["value"])

    def test_back_propagates_and_records_nothing(self):
        # The back token is `0`, the same one every wizard step accepts.
        answers = {}
        with self.assertRaises(FFmWiz.Back):
            self._run(answers, ["0"])
        self.assertEqual({}, answers)

    def test_a_falsy_record_prints_no_confirmation(self):
        described = []

        def describe(answers):
            described.append(answers)
            return "should never be printed"

        answers = {}
        self._run(answers, ["anything"], record=lambda v, a: {}, describe=describe)
        self.assertEqual([], described)

    def test_a_back_raised_inside_record_is_not_swallowed(self):
        # The composite step's partner prompts can raise Back from inside
        # `record`; only ValueError may be caught here, or that path would be
        # trapped in a loop instead of taking the user back a step.
        def record(value, answers):
            raise FFmWiz.Back()

        answers = {}
        with self.assertRaises(FFmWiz.Back):
            self._run(answers, ["anything"], record=record)


if __name__ == "__main__":
    unittest.main()
