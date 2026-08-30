"""The quick outputs: GIF, boomerang, loop and thumbnail.

Added after a review of the ffmpeg-webCLI tool, which offers these as one-click
operations. Unlike the picture filters they change what the job PRODUCES, so
most of them are not one more argument on the single command -- a GIF is two
commands and a boomerang is three, and the risk moves with them:

  * a GIF written without a palette pass is quantised against a fixed 216-colour
    table and bands visibly, so both passes must exist AND must share their
    filter chain: a palette built from different frames is the wrong palette;
  * a boomerang is the forward half followed by the REVERSED half, in that
    order, and the reversal has to go through the bounded segmented executor
    rather than one full-timeline `reverse`;
  * `-stream_loop` is an INPUT option -- after the `-i` it is accepted, ignored,
    and the job exits 0 having repeated nothing;
  * a GIF has no audio, and a command that lets FFmpeg auto-select one dies at
    the muxer instead of at the argument.

The argv tests pin the shapes above. The real-media tests below check the
produced FILES, because every one of these failures exits 0.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts

from ffmwiz import (encoding, reverse_pipeline, wizard_build, wizard_build_b,
                    wizard_quick)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")


def _run(args, timeout=600):
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace", timeout=timeout)


class TheAnswerVocabularyIsWhatThePromptOffers(unittest.TestCase):
    """The parser is the seam between the prompt text and the builders."""

    def test_every_mode_the_hint_names_is_accepted(self):
        for text, expected in (("gif", "gif"),
                               ("boomerang", "boomerang"),
                               ("thumb", "thumbnail"),
                               ("thumb=2.5", "thumbnail")):
            with self.subTest(text=text):
                self.assertEqual(expected,
                                 wizard_quick.parse_quick_tokens(text)["quick_output"])

    def test_a_gif_carries_its_rate_and_size(self):
        parsed = wizard_quick.parse_quick_tokens("gif=12:320")
        self.assertEqual(12.0, parsed["gif_fps"])
        self.assertEqual(320, parsed["gif_width"])

    def test_a_thumbnail_time_may_be_a_clock(self):
        self.assertEqual(
            83.5,
            wizard_quick.parse_quick_tokens("thumb=00:01:23.5")["thumbnail_seconds"])

    def test_loop_rides_along_with_a_mode(self):
        parsed = wizard_quick.parse_quick_tokens("gif,loop=3")
        self.assertEqual("gif", parsed["quick_output"])
        self.assertEqual(3, parsed["loop_count"])

    def test_two_modes_are_refused_rather_than_silently_merged(self):
        # Accepting both would build one of them and drop the other with no
        # message: the answer would be recorded and the output would be a lie.
        for text in ("gif,boomerang", "thumb=1,gif", "gif,gif"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    wizard_quick.parse_quick_tokens(text)

    def test_nonsense_is_refused(self):
        for text in ("sideways", "loop", "loop=0", "loop=x", "gif=0",
                     "gif=-4", "boomerang=2", "thumb=later"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    wizard_quick.parse_quick_tokens(text)

    def test_a_thumbnail_refuses_a_loop(self):
        # A thumbnail is a single frame; `-stream_loop` on it is meaningless.
        # Checked after the whole answer is parsed, so token order must not
        # change whether the combination is accepted.
        for text in ("thumb,loop=2", "loop=2,thumb"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    wizard_quick.parse_quick_tokens(text)

    def test_describe_names_what_was_asked_for(self):
        for text, fragment in (("gif=10:200", "10 fps, 200 px"),
                               ("boomerang", "reversed"),
                               ("thumb=3", "3.000s"),
                               ("loop=2", "2 extra")):
            with self.subTest(text=text):
                answers = wizard_quick.parse_quick_tokens(text)
                self.assertIn(fragment, wizard_quick.describe_quick(answers))


class TheStepWritesTheKeysTheBuildersRead(unittest.TestCase):
    """Renaming a key on one side of this seam breaks nothing loudly."""

    def _prompted(self, *replies):
        answers = {"output_ext": "mkv"}
        canned = iter(replies)
        real_ask, real_error = FFmWiz.appio.ask_raw, FFmWiz.appio.error
        FFmWiz.appio.ask_raw = lambda *a, **k: next(canned)
        FFmWiz.appio.error = lambda *a, **k: None
        try:
            wizard_quick.step_quick_output(answers)
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.error = real_ask, real_error
        return answers

    def test_declining_writes_nothing_and_keeps_the_container(self):
        for reply in ("n", "", "no"):
            with self.subTest(reply=reply):
                self.assertEqual({"output_ext": "mkv"}, self._prompted(reply))

    def test_a_gif_answer_forces_the_gif_container(self):
        answers = self._prompted("gif")
        self.assertEqual("gif", answers["output_ext"])
        self.assertEqual("gif", wizard_build_b.quick_output_mode(answers))

    def test_a_thumbnail_answer_forces_an_image_container(self):
        self.assertEqual("png", self._prompted("thumb=1")["output_ext"])

    def test_a_boomerang_keeps_the_container_the_format_question_chose(self):
        self.assertEqual("mkv", self._prompted("boomerang")["output_ext"])

    def test_a_rejected_answer_leaves_no_debris(self):
        # The prompt re-asks on a bad list. A `gif` accepted on the first pass
        # and then rejected must not leave `.gif` on a job that ended up
        # declining -- the file would be named for an output nobody asked for.
        answers = self._prompted("gif,boomerang", "n")
        self.assertEqual({"output_ext": "mkv"}, answers)

    def test_back_still_works(self):
        # The back token is `0`, the same one every other step accepts.
        for token in sorted(FFmWiz.BACK_INPUT_TOKENS):
            with self.subTest(token=token):
                real_ask = FFmWiz.appio.ask_raw
                FFmWiz.appio.ask_raw = lambda *a, **k: token
                try:
                    with self.assertRaises(FFmWiz.Back):
                        wizard_quick.step_quick_output({})
                finally:
                    FFmWiz.appio.ask_raw = real_ask

    def test_the_step_is_on_the_encode_path(self):
        # Registered but unreachable is the same as absent.
        import inspect
        from ffmwiz import wizard_flow
        source = inspect.getsource(wizard_flow.run_wizard)
        self.assertIn('wizard_base.Step("video_quick"', source)
        self.assertIn("wizard_quick.step_quick_output", source)


class TheConfigKeyReachesTheSameAnswers(unittest.TestCase):
    """`video_quick`, documented in Appendix B.

    Mode 2 never asks the question once its config key is filled, so the
    config key is the ONLY way a non-interactive run reaches it. A key that
    is documented but not read is worse than one that does not exist.
    """

    def _applied(self, settings):
        # `config_value` reads `config["settings"]`, not the top level.
        config = {"settings": settings}
        from ffmwiz.support import ext08
        answers = {
            "video_streams": [{"codec_type": "video", "width": 640,
                               "height": 480, "pix_fmt": "yuv420p"}],
            "audio_streams": [], "format": {"duration": "10.0"},
            "input_path": FFmWiz.Path("x.mkv"), "packet_sizes": {},
            "output_ext": "mp4",
        }
        real = FFmWiz.services.get_packet_sizes
        FFmWiz.services.get_packet_sizes = lambda _a: {}
        try:
            ext08.apply_config_video_options(answers, config,
                                             force_video_options=True)
        finally:
            FFmWiz.services.get_packet_sizes = real
        return answers

    def test_video_quick_from_config_reaches_the_answers(self):
        applied = self._applied({"video_quick": "gif"})
        self.assertEqual("gif", applied["quick_output"])

    def test_video_quick_also_forces_the_output_container(self):
        # Setting `quick_output` alone is not the whole feature: the prompt
        # also forces the container, or the job encodes the wrong file type.
        applied = self._applied({"video_quick": "gif"})
        self.assertEqual("gif", applied["output_ext"])

    def test_an_absent_video_quick_leaves_the_job_alone(self):
        applied = self._applied({})
        self.assertNotIn("quick_output", applied)
        self.assertEqual("mp4", applied["output_ext"])

    def test_an_invalid_video_quick_in_config_fails_loudly(self):
        # `fail()` prints before it exits; keep that off the suite's console.
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                self._applied({"video_quick": "sideways"})


class TheCommandsHaveTheShapeTheyHaveToHave(NoLeakedArtifacts, unittest.TestCase):

    def _answers(self, tmp, **extra):
        answers = self.own({
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe",
            "input_path": Path("in.mkv"),
            "output_location": Path(tmp), "output_ext": "mkv",
            "format": {"duration": "10.0"},
            "video_streams": [{"codec_type": "video", "width": 640,
                               "height": 480, "pix_fmt": "yuv420p"}],
            "audio_streams": [], "subtitle_streams": [], "data_streams": [],
            "audio_tracks": [], "video_codec": "H264",
            "video_encoder": "libx264", "use_gpu": False, "crf": 28,
            "color_range_choice": "tv",
        })
        answers.update(extra)
        return answers

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="quickshape_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))

    def _plan(self, **extra):
        answers = self._answers(self._tmp, **extra)
        wizard_build.build_ffmpeg_command(answers)
        return answers["quick_output_plan"]

    def test_a_gif_is_planned_as_two_passes_in_that_order(self):
        stages = self._plan(quick_output="gif", output_ext="gif")["stages"]
        self.assertEqual(2, len(stages))
        self.assertIn("palettegen", " ".join(stages[0][1]))
        self.assertIn("paletteuse", " ".join(stages[1][1]))

    def test_both_gif_passes_share_one_filter_chain(self):
        # A palette built from other frames is the wrong palette, and the
        # symptom is banding rather than an error.
        stages = self._plan(quick_output="gif", output_ext="gif",
                            crop_enabled=True, crop_left=10, crop_right=10,
                            crop_top=0, crop_bottom=0)["stages"]
        palette = stages[0][1][stages[0][1].index("-vf") + 1]
        write = stages[1][1][stages[1][1].index("-lavfi") + 1]
        self.assertEqual(palette[:-len(FFmWiz.GIF_PALETTEGEN_FILTER) - 1],
                         write[:-len(FFmWiz.GIF_PALETTEUSE_FILTER) - 1])
        self.assertIn("crop=", palette)

    def test_a_gif_maps_no_audio(self):
        stages = self._plan(quick_output="gif", output_ext="gif",
                            audio_streams=[{"codec_type": "audio"}],
                            audio_tracks=[0])["stages"]
        self.assertIn("-an", stages[1][1])

    def test_the_gif_chain_carries_the_jobs_own_picture_answers(self):
        # A GIF that ignored the rotation the user asked for two questions
        # earlier would exit 0 and be wrong.
        chain = wizard_build_b.gif_filter_chain(
            self._answers(self._tmp, rotate_choice="90cw", adjust_grayscale=True))
        self.assertIn("transpose=1", chain)
        self.assertIn("eq=saturation=0", chain)
        # ...and its own rate/size come LAST, so they size the finished picture.
        self.assertTrue(chain.endswith(
            wizard_build_b.gif_scale_chain({})), chain)

    def test_a_prepared_source_is_not_edited_a_second_time(self):
        answers = self._answers(self._tmp, rotate_choice="90cw")
        self.assertNotIn("transpose",
                         wizard_build_b.gif_filter_chain(answers, prepared=True))

    def test_the_gif_defaults_are_overridden_by_the_jobs_own_answers(self):
        self.assertEqual("fps=15,scale=480:-1:flags=lanczos",
                         wizard_build_b.gif_scale_chain({}))
        self.assertEqual(
            "fps=10,scale=320:-1:flags=lanczos",
            wizard_build_b.gif_scale_chain(
                {"fps": 10, "resolution": {"mode": "exact_stretch",
                                           "width": 320, "height": 240}}))
        # An explicit answer beats both.
        self.assertEqual(
            "fps=24,scale=200:-1:flags=lanczos",
            wizard_build_b.gif_scale_chain(
                {"fps": 10, "gif_fps": 24, "gif_width": 200}))

    def test_a_gif_container_alone_selects_the_palette_pipeline(self):
        # `.gif` typed at the FORMAT question must not fall through to
        # `-c:v gif`, which is the banded output the palette exists to avoid.
        self.assertEqual("gif", wizard_build_b.quick_output_mode(
            {"output_ext": "gif"}))
        self.assertEqual("", wizard_build_b.quick_output_mode(
            {"output_ext": "mkv"}))

    def test_a_boomerang_is_forward_then_reversed_then_concatenated(self):
        plan = self._plan(quick_output="boomerang")
        labels = [label for label, _argv in plan["stages"]]
        self.assertEqual(3, len(labels), labels)
        self.assertIn("forward", labels[0].lower())
        self.assertIn("revers", labels[1].lower())
        self.assertIn("concat", labels[2].lower())
        listed = Path(plan["concat_list"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(2, len(listed))
        self.assertIn(Path(plan["forward_path"]).name, listed[0])
        self.assertIn(Path(plan["backward_path"]).name, listed[1])

    def test_a_boomerang_loops_the_finished_clip(self):
        # `-stream_loop` cannot do this: it is an INPUT option on one `-i`,
        # and the finished boomerang is a CONCAT of two files. `loop_count=2`
        # must repeat the forward/backward PAIR three times (the original play
        # plus two extra), not loop either half's own input.
        plan = self._plan(quick_output="boomerang", loop_count=2)
        listed = Path(plan["concat_list"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(6, len(listed), listed)
        forward_name = Path(plan["forward_path"]).name
        backward_name = Path(plan["backward_path"]).name
        # Assert the ORDER, not just the count: six entries in the wrong order
        # play as something else entirely -- the mistake the comment beside
        # `write_concat_list` already exists to prevent.
        for index, line in enumerate(listed):
            expected_name = forward_name if index % 2 == 0 else backward_name
            self.assertIn(expected_name, line, listed)

    def test_the_boomerang_reverse_is_not_written_down_as_a_full_pass(self):
        # It is planned from the forward half's real geometry, which does not
        # exist yet. Emitting a one-shot `-vf reverse` here would print a
        # command that buffers the whole timeline and is never the one run.
        plan = self._plan(quick_output="boomerang")
        self.assertTrue(plan["stages"][1][1][0].startswith("<"),
                        plan["stages"][1][1])

    def test_a_thumbnail_is_one_command_that_seeks_before_the_input(self):
        stages = self._plan(quick_output="thumbnail", thumbnail_seconds=4.0,
                            output_ext="png")["stages"]
        self.assertEqual(1, len(stages))
        argv = stages[0][1]
        self.assertLess(argv.index("-ss"), argv.index("-i"))
        self.assertEqual("4.000000", argv[argv.index("-ss") + 1])
        self.assertEqual(["1"], argv[argv.index("-frames:v") + 1:][:1])

    def test_stream_loop_precedes_the_input_it_repeats(self):
        # After the `-i` FFmpeg accepts it, ignores it, and exits 0 having
        # repeated nothing.
        answers = self._answers(self._tmp, loop_count=2)
        cmd = wizard_build.build_ffmpeg_command(answers)
        self.assertIn("-stream_loop", cmd)
        self.assertLess(cmd.index("-stream_loop"), cmd.index("-i"))
        self.assertEqual("2", cmd[cmd.index("-stream_loop") + 1])

    def test_no_loop_answer_adds_no_option(self):
        self.assertNotIn("-stream_loop",
                         wizard_build.build_ffmpeg_command(self._answers(self._tmp)))
        for bad in (0, -1, None, "", "later"):
            with self.subTest(loop_count=bad):
                argv: list[str] = []
                wizard_build_b.append_stream_loop(argv, {"loop_count": bad})
                self.assertEqual([], argv)

    def test_a_gif_still_loops(self):
        # Regression guard: `gif_input_options` already threads `loop_count`
        # through both GIF passes, in front of each one's own `-i`.
        stages = self._plan(quick_output="gif", output_ext="gif",
                            loop_count=2)["stages"]
        for _label, argv in stages:
            self.assertIn("-stream_loop", argv, argv)
            self.assertLess(argv.index("-stream_loop"), argv.index("-i"))
            self.assertEqual("2", argv[argv.index("-stream_loop") + 1])

    def test_a_single_cut_reaches_both_gif_passes_identically(self):
        options = wizard_build_b.gif_input_options(
            self._answers(self._tmp, cut_keep_ranges=[(2.0, 5.0)]))
        self.assertEqual(["-ss", "2.000000", "-t", "3.000000"], options)

    def test_an_unsupported_gif_answer_is_stated_not_dropped(self):
        notes = wizard_build_b.gif_unsupported_answer_notes(
            {"cut_keep_ranges": [(0.0, 1.0), (2.0, 3.0)]})
        self.assertTrue(notes)
        self.assertEqual([], wizard_build_b.gif_unsupported_answer_notes({}))

    def test_the_recorded_plan_ends_with_the_command_that_is_printed(self):
        # `build_ffmpeg_command` returns one argv and the summary prints it. It
        # has to be the stage that writes the file the user named, or the
        # printed command describes an intermediate.
        answers = self._answers(self._tmp, quick_output="gif", output_ext="gif")
        cmd = wizard_build.build_ffmpeg_command(answers)
        plan = answers["quick_output_plan"]
        self.assertEqual([str(part) for part in plan["stages"][-1][1]], cmd)
        self.assertEqual(str(answers["output_path"]), cmd[-1])

    def test_the_sub_job_cannot_plan_another_quick_output(self):
        # A forward half that kept `quick_output` would plan a boomerang of a
        # boomerang, without end.
        plan = self._plan(quick_output="boomerang")
        for key in FFmWiz.QUICK_ANSWER_KEYS:
            self.assertNotIn(key, plan["forward_answers"], key)


@requires_ffmpeg
class TheOutputsAreWhatTheyClaimToBe(NoLeakedArtifacts, unittest.TestCase):
    """Real encodes. Every failure these guard against exits 0."""

    @classmethod
    def setUpClass(cls):
        if not (FFMPEG and FFPROBE):
            raise unittest.SkipTest("ffmpeg/ffprobe not on PATH")
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_quick_"))
        # 320x180, RED for the first second and BLUE for the second. A colour
        # that changes over time is the cheapest oracle there is for a reversal:
        # a boomerang of it must read red, blue, blue, red.
        cls.source = cls._root / "src.mkv"
        result = _run([
            FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            "color=c=red:s=320x180:r=15:d=2,"
            "drawbox=x=0:y=0:w=320:h=180:color=blue:t=fill:enable='gte(t,1)'",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", cls.source])
        if result.returncode != 0:
            raise unittest.SkipTest(f"could not build the source: {result.stderr[-300:]}")
        cls.probe = json.loads(_run([
            FFPROBE, "-v", "error", "-print_format", "json", "-show_format",
            "-show_streams", cls.source]).stdout)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="quick_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))

    def _encode(self, label, **extra):
        out = self._tmp / label
        out.mkdir(parents=True, exist_ok=True)
        streams = self.probe["streams"]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.source,
            "probe": self.probe, "format": self.probe["format"],
            "streams": streams, "output_location": out, "output_ext": "mkv",
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [], "data_streams": [], "audio_tracks": [0],
            "color_range_choice": "tv", "video_codec": "H264",
            "video_encoder": "libx264", "use_gpu": False,
            "crf": 28, "preset": "ultrafast", "audio_codec": "aac",
        })
        answers.update(extra)
        cmd = wizard_build.build_ffmpeg_command(answers)
        code, _elapsed = encoding.execute_encode_plan(
            answers, cmd, total_duration=None, label=label)
        self.assertEqual(0, code, f"{label} did not run")
        produced = Path(answers["output_path"])
        self.assertTrue(produced.exists(), f"{label} wrote nothing")
        return produced

    def _probe(self, path, *extra):
        return json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                "-show_format", "-show_streams", *extra,
                                path]).stdout)

    def _duration(self, path):
        return float(self._probe(path)["format"]["duration"])

    def _colour_at(self, path, seconds):
        """"red" or "blue" at a time, sampled from the real picture.

        Seeking then scaling to 1x1 averages the whole frame, which is safe
        here only because every frame of this fixture is ONE flat colour.
        """
        raw = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
             "-ss", f"{seconds:.3f}", "-i", str(path), "-frames:v", "1",
             "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300).stdout
        self.assertEqual(3, len(raw), f"no frame decoded at {seconds}s of {path}")
        return "red" if raw[0] > raw[2] else "blue"

    def test_the_fixture_really_changes_colour(self):
        # Guard the guard: a single-colour source would make every reversal
        # test below pass without anything being reversed.
        self.assertEqual("red", self._colour_at(self.source, 0.5))
        self.assertEqual("blue", self._colour_at(self.source, 1.5))

    # ---- GIF ------------------------------------------------------------

    def test_a_gif_job_writes_a_real_gif(self):
        produced = self._encode("gif", quick_output="gif", output_ext="gif")
        self.assertEqual(".gif", produced.suffix)
        info = self._probe(produced, "-count_frames")
        self.assertIn("gif", info["format"]["format_name"])
        video = [s for s in info["streams"] if s["codec_type"] == "video"]
        self.assertEqual(1, len(video))
        self.assertEqual("gif", video[0]["codec_name"])
        # 480 px wide by default, height from the 16:9 source.
        self.assertEqual((480, 270), (video[0]["width"], video[0]["height"]))
        # 2 seconds at the default 15 fps.
        self.assertEqual(30, int(video[0]["nb_read_frames"]))

    def test_a_gif_carries_no_audio(self):
        # The source has a tone. The GIF muxer cannot hold it, and a command
        # that lets FFmpeg auto-select it dies at the header.
        info = self._probe(self._encode("gifau", quick_output="gif",
                                        output_ext="gif"))
        self.assertEqual([], [s for s in info["streams"]
                              if s["codec_type"] == "audio"])

    def test_a_gif_honours_the_rate_and_size_it_was_asked_for(self):
        produced = self._encode("gifsize", quick_output="gif", output_ext="gif",
                                gif_fps=5, gif_width=160)
        info = self._probe(produced, "-count_frames")
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        self.assertEqual((160, 90), (video["width"], video["height"]))
        self.assertEqual(10, int(video["nb_read_frames"]))

    def test_a_gif_keeps_the_picture_answers_the_wizard_collected(self):
        # A quarter turn swaps the dimensions; a GIF built from the bare source
        # would come out 480x270 and exit 0.
        produced = self._encode("gifrot", quick_output="gif", output_ext="gif",
                                rotate_choice="90cw", gif_width=180)
        video = next(s for s in self._probe(produced)["streams"]
                     if s["codec_type"] == "video")
        self.assertEqual(180, video["width"])
        self.assertGreater(video["height"], video["width"])

    def test_a_gif_of_one_cut_range_holds_only_that_range(self):
        produced = self._encode("gifcut", quick_output="gif", output_ext="gif",
                                cut_keep_ranges=[(1.0, 2.0)])
        info = self._probe(produced, "-count_frames")
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        self.assertEqual(15, int(video["nb_read_frames"]))
        # 1.0-2.0 s of this fixture is the BLUE half.
        self.assertEqual("blue", self._colour_at(produced, 0.2))

    # ---- Boomerang ------------------------------------------------------

    def test_a_boomerang_plays_forward_then_backward(self):
        produced = self._encode("boom", quick_output="boomerang")
        self.assertAlmostEqual(2 * self._duration(self.source),
                               self._duration(produced), delta=0.5)
        # The whole contract in four samples. Concatenated in the wrong order
        # this reads blue, red, red, blue; with the reversal dropped it reads
        # red, blue, red, blue.
        self.assertEqual(["red", "blue", "blue", "red"],
                         [self._colour_at(produced, t)
                          for t in (0.4, 1.5, 2.4, 3.5)])

    def test_a_boomerang_keeps_its_sound(self):
        info = self._probe(self._encode("boomau", quick_output="boomerang"))
        self.assertEqual(1, len([s for s in info["streams"]
                                 if s["codec_type"] == "audio"]))

    def test_a_boomerang_reversal_runs_through_the_bounded_executor(self):
        # Not a second `reverse` implementation: the middle stage is handed to
        # `execute_encode_plan`, which routes it to the segmented reverse. If
        # that ever stopped happening a long clip would buffer every frame.
        seen: list[bool] = []
        real = reverse_pipeline.run_segmented_reverse_main_encode

        def watched(answers):
            seen.append(True)
            return real(answers)

        reverse_pipeline.run_segmented_reverse_main_encode = watched
        try:
            self._encode("boomseg", quick_output="boomerang")
        finally:
            reverse_pipeline.run_segmented_reverse_main_encode = real
        self.assertTrue(seen, "the boomerang reversed without the bounded executor")

    def test_a_boomerang_can_be_written_as_a_gif(self):
        # Both features at once, in the only order that works: concatenate the
        # halves first, then quantise the whole thing against one palette.
        produced = self._encode("boomgif", quick_output="boomerang",
                                output_ext="gif", gif_fps=10)
        self.assertEqual(".gif", produced.suffix)
        info = self._probe(produced, "-count_frames")
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        self.assertEqual("gif", video["codec_name"])
        self.assertGreaterEqual(int(video["nb_read_frames"]), 35)
        self.assertEqual(["red", "blue", "blue", "red"],
                         [self._colour_at(produced, t)
                          for t in (0.4, 1.5, 2.4, 3.5)])

    # ---- Loop -----------------------------------------------------------

    def test_a_loop_repeats_the_input(self):
        for count, factor in ((1, 2), (2, 3)):
            with self.subTest(loop_count=count):
                produced = self._encode(f"loop{count}", loop_count=count)
                self.assertAlmostEqual(factor * self._duration(self.source),
                                       self._duration(produced), delta=0.5)

    def test_a_looped_clip_really_repeats_its_content(self):
        # Duration alone would also grow if the last frame were held, so read
        # the picture: the colour has to come back round.
        produced = self._encode("loopcolour", loop_count=1)
        self.assertEqual(["red", "blue", "red", "blue"],
                         [self._colour_at(produced, t)
                          for t in (0.4, 1.5, 2.4, 3.5)])

    # ---- Thumbnail ------------------------------------------------------

    def test_a_thumbnail_is_one_image_from_the_time_asked_for(self):
        for seconds, expected in ((0.5, "red"), (1.5, "blue")):
            with self.subTest(seconds=seconds):
                produced = self._encode(f"thumb{seconds}",
                                        quick_output="thumbnail",
                                        thumbnail_seconds=seconds,
                                        thumbnail_ext="png", output_ext="png")
                info = self._probe(produced)
                video = next(s for s in info["streams"]
                             if s["codec_type"] == "video")
                self.assertEqual("png", video["codec_name"])
                self.assertEqual((320, 180), (video["width"], video["height"]))
                self.assertEqual(expected, self._colour_at(produced, 0.0))

    def test_a_thumbnail_beyond_the_end_fails_loudly(self):
        # A seek past the last frame decodes nothing. Reporting success would
        # leave the user with no file and no message.
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.source,
            "probe": self.probe, "format": self.probe["format"],
            "output_location": self._tmp / "late", "output_ext": "png",
            "video_streams": [s for s in self.probe["streams"]
                              if s["codec_type"] == "video"],
            "audio_streams": [], "subtitle_streams": [], "data_streams": [],
            "audio_tracks": [], "video_codec": "H264",
            "video_encoder": "libx264", "use_gpu": False,
            "quick_output": "thumbnail", "thumbnail_seconds": 99.0,
        })
        (self._tmp / "late").mkdir(parents=True, exist_ok=True)
        cmd = wizard_build.build_ffmpeg_command(answers)
        code, _elapsed = encoding.execute_encode_plan(
            answers, cmd, total_duration=None, label="late thumbnail")
        self.assertNotEqual(0, code)


if __name__ == "__main__":
    unittest.main()
