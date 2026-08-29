"""The look filters: rotate/flip, colour, denoise, sharpen/blur, fade.

Added after a review of the ffmpeg-webCLI tool, which offers these as one-click
operations. They are wizard answers rather than a separate mode, so they
compose with crop, resize, speed and reverse into ONE command -- the same
"single stack" idea, expressed the way FFmWiz already builds jobs.

Almost all of the risk here is ORDER, not syntax:

    crop -> rotate/flip -> fps -> scale/pad -> colour/denoise/sharpen
         -> speed/reverse -> fade -> format

A rotation after the scale would size the canvas against the source
orientation. A fade before the speed change would last the wrong number of
seconds. Anything that changes geometry after the reverse would make the
memory budget -- which is computed at the reverse filter's input -- describe a
frame that no longer exists. These tests pin the order, and the real-media ones
check the picture rather than the argv.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")


def _run(args, timeout=300):
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace", timeout=timeout)


class TheChainIsBuiltInTheRightOrder(unittest.TestCase):

    def _answers(self, **extra):
        answers = {
            "video_streams": [{"width": 640, "height": 480, "pix_fmt": "yuv420p"}],
            "format": {"duration": "10.0"},
            "video_codec": "H264", "video_encoder": "libx264",
            "use_gpu": False, "output_ext": "mkv",
        }
        answers.update(extra)
        return answers

    def _chain(self, **extra):
        return (FFmWiz.build_cpu_video_filter(self._answers(**extra)) or "").split(",")

    def test_a_rotation_comes_before_the_resize(self):
        # A 90-degree turn swaps width and height, so the target canvas has to
        # be applied to the ROTATED picture.
        chain = self._chain(rotate_choice="90cw",
                            resolution={"mode": "exact_stretch",
                                        "width": 320, "height": 240})
        transpose = next(i for i, f in enumerate(chain) if f.startswith("transpose"))
        scale = next(i for i, f in enumerate(chain) if f.startswith("scale"))
        self.assertLess(transpose, scale)

    def test_a_crop_still_comes_first(self):
        chain = self._chain(crop_enabled=True, crop_left=10, crop_right=10,
                            crop_top=0, crop_bottom=0, rotate_choice="180")
        self.assertTrue(chain[0].startswith("crop="), chain)
        self.assertTrue(chain[1].startswith("transpose"), chain)

    def test_the_look_filters_sit_before_the_reverse(self):
        # `reverse` buffers whatever reaches it, and the memory budget is sized
        # from the frame at its input. A filter after it would be applied to a
        # buffered frame for nothing.
        chain = self._chain(denoise_level="medium", reverse_video=True,
                            video_speed_enabled=True, video_speed_factor=1.0)
        denoise = next(i for i, f in enumerate(chain) if f.startswith("hqdn3d"))
        reverse = next(i for i, f in enumerate(chain) if f == "reverse")
        self.assertLess(denoise, reverse)

    def test_the_fade_comes_after_the_speed_change(self):
        # "One second of fade" means one second of the OUTPUT. Before a 2x
        # speed change it would come out half as long.
        chain = self._chain(fade_in_seconds=1.0, video_speed_enabled=True,
                            video_speed_factor=2.0)
        setpts = next(i for i, f in enumerate(chain) if f.startswith("setpts"))
        fade = next(i for i, f in enumerate(chain) if f.startswith("fade=t=in"))
        self.assertLess(setpts, fade)

    def test_a_fade_out_is_placed_from_the_end_of_the_output(self):
        chain = self._chain(fade_out_seconds=2.0)
        fade = next(f for f in chain if f.startswith("fade=t=out"))
        self.assertIn("st=8.000", fade, "a 2 s fade out of a 10 s output starts at 8 s")

    def test_a_fade_out_longer_than_the_output_is_dropped(self):
        # Guessing here would emit a negative start time and fail the encode.
        self.assertEqual([], FFmWiz.build_fade_filters(
            self._answers(fade_out_seconds=30.0), 10.0))

    def test_denoise_runs_before_sharpen(self):
        chain = self._chain(denoise_level="light", sharpen_level="heavy")
        self.assertLess(chain.index(FFmWiz.DENOISE_FILTERS["light"]),
                        chain.index(FFmWiz.SHARPEN_FILTERS["heavy"]))

    def test_sharpen_and_blur_are_mutually_exclusive(self):
        chain = self._chain(sharpen_level="light", blur_level="heavy")
        self.assertIn(FFmWiz.SHARPEN_FILTERS["light"], chain)
        self.assertNotIn(FFmWiz.BLUR_FILTERS["heavy"], chain)

    def test_grayscale_overrides_a_saturation_answer(self):
        looks = FFmWiz.build_look_filters(
            self._answers(adjust_saturation=2.0, adjust_grayscale=True))
        self.assertEqual(["eq=saturation=0"], looks)

    def test_neutral_answers_add_nothing(self):
        # The chain of a job that asked for none of this must be unchanged.
        self.assertEqual([], FFmWiz.build_look_filters(self._answers(
            adjust_brightness=0.0, adjust_contrast=1.0, adjust_saturation=1.0,
            denoise_level="off", sharpen_level="off", blur_level="off")))
        self.assertEqual([], FFmWiz.build_orientation_filters(
            self._answers(rotate_choice="none")))

    def test_an_out_of_range_adjustment_is_clamped_not_rejected(self):
        looks = FFmWiz.build_look_filters(self._answers(adjust_contrast=99.0))
        self.assertIn("contrast=3", looks[0])

    def test_a_nonsense_adjustment_is_ignored(self):
        self.assertEqual([], FFmWiz.build_look_filters(
            self._answers(adjust_brightness="quite bright")))


@requires_ffmpeg
class TheFiltersDoWhatTheySay(NoLeakedArtifacts, unittest.TestCase):
    """Real encodes: the picture is the contract, not the filter string."""

    @classmethod
    def setUpClass(cls):
        if not (FFMPEG and FFPROBE):
            raise unittest.SkipTest("ffmpeg/ffprobe not on PATH")
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_look_"))
        # 320x180 so a rotation is unmistakable, with a red left half and a
        # blue right half so a flip is too.
        cls.source = cls._root / "src.mkv"
        result = _run([
            FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            "color=c=red:s=320x180:r=15:d=2,"
            "drawbox=x=160:y=0:w=160:h=180:color=blue:t=fill",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", cls.source])
        if result.returncode != 0:
            raise unittest.SkipTest(f"could not build the source: {result.stderr[-300:]}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="look_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))

    def _encode(self, label, **extra):
        out = self._tmp / label
        out.mkdir()
        info = json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                "-show_format", "-show_streams", self.source]).stdout)
        streams = info["streams"]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": self.source,
            "probe": info, "format": info["format"], "streams": streams,
            "output_location": out, "output_ext": "mkv",
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [], "data_streams": [], "audio_tracks": [0],
            "color_range_choice": "tv", "video_encoder": "libx264",
            "crf": 28, "preset": "ultrafast", "audio_codec": "aac",
        })
        answers.update(extra)
        cmd = FFmWiz.build_ffmpeg_command(answers)
        result = _run([str(part) for part in cmd], timeout=600)
        self.assertEqual(0, result.returncode, result.stderr[-800:])
        return Path(answers["output_path"])

    def _geometry(self, path):
        stream = json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                  "-select_streams", "v:0", "-show_streams",
                                  path]).stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])

    def _pixel(self, path, at_x=0.5, at_y=0.5):
        """The (r, g, b) of a small box at a RELATIVE position in frame one.

        A `scale=2:1` shortcut looked cheaper, but the scaler does not average
        by box: on this source it blended both halves into the same purple, so
        a flip test written against it passed without anything being flipped.
        Cropping samples the picture that is really there. Relative positions
        keep the sampler honest after a rotation changes the dimensions.
        """
        width, height = self._geometry(path)
        x = max(0, min(width - 8, int(width * at_x) - 4))
        y = max(0, min(height - 8, int(height * at_y) - 4))
        raw = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
             "-i", str(path), "-frames:v", "1",
             "-vf", f"crop=8:8:{x}:{y},scale=1:1",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300).stdout
        self.assertEqual(3, len(raw), "expected exactly one sampled pixel")
        return raw[0], raw[1], raw[2]

    def _side_colours(self, path):
        """(left, right) colour of the first frame."""
        sides = []
        for fraction in (0.125, 0.875):
            red, _green, blue = self._pixel(path, at_x=fraction)
            sides.append("red" if red > blue else "blue")
        return tuple(sides)

    def test_the_fixture_really_has_two_sides(self):
        # Guard the guard: a single-colour source would make the flip test pass
        # without flipping anything.
        self.assertEqual(("red", "blue"), self._side_colours(self.source))
        self.assertEqual((320, 180), self._geometry(self.source))

    def test_a_quarter_turn_swaps_the_dimensions(self):
        self.assertEqual((180, 320),
                         self._geometry(self._encode("rot", rotate_choice="90cw")))

    def test_a_horizontal_flip_swaps_the_sides(self):
        self.assertEqual(("blue", "red"),
                         self._side_colours(self._encode("flip", flip_horizontal=True)))

    def test_grayscale_really_removes_the_colour(self):
        produced = self._encode("gray", adjust_grayscale=True)
        # Both halves collapse to grey. Sampling each one separately is the
        # point: a whole-frame average of a red half and a blue half is also
        # grey-ish, and would pass without the filter doing anything.
        for fraction in (0.125, 0.875):
            red, green, blue = self._pixel(produced, at_x=fraction)
            self.assertLess(max(red, green, blue) - min(red, green, blue), 24,
                            f"({red},{green},{blue}) at x={fraction} is still coloured")

    def test_a_rotation_and_a_resize_agree_on_the_orientation(self):
        # The canvas is applied to the ROTATED picture: a portrait target of a
        # landscape source, turned first, fills it instead of letterboxing.
        produced = self._encode(
            "rotscale", rotate_choice="90cw",
            resolution={"mode": "exact_stretch", "width": 180, "height": 320})
        self.assertEqual((180, 320), self._geometry(produced))

    def test_a_denoised_encode_still_produces_a_picture(self):
        # The strengths come from a table; this proves the emitted filter is
        # one FFmpeg accepts, on every level.
        for level in ("light", "medium", "heavy"):
            with self.subTest(level=level):
                self.assertEqual((320, 180), self._geometry(
                    self._encode(f"dn{level}", denoise_level=level)))

    def test_every_sharpen_and_blur_level_is_accepted(self):
        for key, level in (("sharpen_level", "heavy"), ("blur_level", "heavy")):
            with self.subTest(filter=key):
                self.assertEqual((320, 180),
                                 self._geometry(self._encode(f"sb{key}", **{key: level})))

    def test_a_fade_darkens_the_first_frame(self):
        produced = self._encode("fade", fade_in_seconds=1.0)
        raw = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
             "-i", str(produced), "-frames:v", "1", "-vf", "scale=1:1,format=gray",
             "-f", "rawvideo", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300).stdout
        self.assertTrue(raw, "no frame was decoded")
        self.assertLess(raw[0], 40, "the first frame of a fade-in must be near black")


if __name__ == "__main__":
    unittest.main()


class TheWizardQuestionReachesTheBuilders(unittest.TestCase):
    """The step writes answer keys; the builders read them.

    That seam is the one that breaks silently: renaming `denoise_level` on one
    side leaves a wizard that accepts the answer and a command that ignores it,
    with nothing failing anywhere. So this drives the parser and then feeds its
    output straight to the builders.
    """

    def _prompted(self, reply):
        """Run the step with one canned answer."""
        from ffmwiz import wizard_look
        answers = {}
        replies = iter([reply])
        real_ask, real_error = FFmWiz.appio.ask_raw, FFmWiz.appio.error
        FFmWiz.appio.ask_raw = lambda *a, **k: next(replies)
        FFmWiz.appio.error = lambda *a, **k: None
        try:
            wizard_look.step_video_look(answers)
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.error = real_ask, real_error
        return answers

    def test_every_token_the_prompt_offers_produces_a_filter(self):
        # A token the parser accepts but no builder acts on is a lie in the
        # hint text. This walks the whole advertised vocabulary.
        from ffmwiz.wizard_look import parse_look_tokens
        tokens = (["90cw", "90ccw", "180", "hflip", "vflip", "gray"]
                  + [f"{n}={lvl}" for n, lvls in
                     (("denoise", FFmWiz.DENOISE_FILTERS),
                      ("sharpen", FFmWiz.SHARPEN_FILTERS),
                      ("blur", FFmWiz.BLUR_FILTERS)) for lvl in lvls]
                  + ["bright=0.2", "contrast=1.5", "sat=1.5",
                     "fadein=1", "fadeout=1"])
        base = {"video_streams": [{"width": 640, "height": 480,
                                   "pix_fmt": "yuv420p"}],
                "format": {"duration": "10.0"}}
        for token in tokens:
            with self.subTest(token=token):
                answers = dict(base, **parse_look_tokens(token))
                produced = (FFmWiz.build_orientation_filters(answers)
                            + FFmWiz.build_look_filters(answers)
                            + FFmWiz.build_fade_filters(answers, 10.0))
                self.assertTrue(produced, f"{token} reached no builder")

    def test_declining_writes_nothing(self):
        self.assertEqual({}, self._prompted("n"))
        self.assertEqual({}, self._prompted(""))

    def test_an_answer_survives_into_the_command(self):
        answers = self._prompted("180,gray,fadeout=2")
        self.assertEqual("180", answers["rotate_choice"])
        chain = FFmWiz.build_cpu_video_filter(dict(
            answers,
            video_streams=[{"width": 640, "height": 480, "pix_fmt": "yuv420p"}],
            format={"duration": "10.0"}, video_encoder="libx264",
            use_gpu=False, output_ext="mkv"))
        for expected in ("transpose=1,transpose=1", "eq=saturation=0", "fade=t=out"):
            self.assertIn(expected, chain)

    def test_a_rejected_answer_leaves_no_debris(self):
        # The prompt re-asks on a bad list. Keys from the failed attempt must
        # not survive, or `sharpen,blur` would leave the sharpen behind.
        from ffmwiz import wizard_look
        replies = iter(["sharpen,blur", "n"])
        answers = {}
        real_ask, real_error = FFmWiz.appio.ask_raw, FFmWiz.appio.error
        FFmWiz.appio.ask_raw = lambda *a, **k: next(replies)
        FFmWiz.appio.error = lambda *a, **k: None
        try:
            wizard_look.step_video_look(answers)
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.error = real_ask, real_error
        self.assertEqual({}, answers)

    def test_back_still_works(self):
        # The back token is `0`, the same one every other step accepts -- not
        # "b". A step that answered only to "b" would trap the user in the
        # wizard with no way to step back to the crop question.
        from ffmwiz import wizard_look
        for token in sorted(FFmWiz.BACK_INPUT_TOKENS):
            with self.subTest(token=token):
                real_ask = FFmWiz.appio.ask_raw
                FFmWiz.appio.ask_raw = lambda *a, **k: token
                try:
                    with self.assertRaises(FFmWiz.Back):
                        wizard_look.step_video_look({})
                finally:
                    FFmWiz.appio.ask_raw = real_ask

    def test_the_step_is_on_the_encode_path(self):
        # Registered but unreachable is the same as absent.
        import inspect
        from ffmwiz import wizard_flow
        self.assertIn('wizard_base.Step("video_look"',
                      inspect.getsource(wizard_flow.run_wizard))


class ThePictureAndTheSoundFadeTogether(unittest.TestCase):
    """One rule, two spellings. They used to be one rule and one spelling.

    A `fade` with no `afade` is not a smaller feature, it is a wrong one: the
    picture goes to black while the sound stays at full volume.
    """

    def _answers(self, **extra):
        answers = {
            "video_streams": [{"width": 640, "height": 480, "pix_fmt": "yuv420p"}],
            "format": {"duration": "10.0"}, "audio_tracks": [0],
            "video_encoder": "libx264", "use_gpu": False, "output_ext": "mkv",
        }
        answers.update(extra)
        return answers

    def test_both_chains_fade_over_the_same_seconds(self):
        answers = self._answers(fade_in_seconds=1.0, fade_out_seconds=2.0)
        video = FFmWiz.build_fade_filters(answers, 10.0)
        audio = FFmWiz.build_encode_audio_processing_filter(answers).split(",")
        self.assertEqual([f"a{part}" for part in video],
                         [part for part in audio if "fade" in part])

    def test_a_fade_only_job_still_builds_the_audio_transform(self):
        # The gate used to ask only about speed and loudnorm, so a fade-only
        # job never reached the chain that carries the afade.
        from ffmwiz.support import ext04b
        graph, labels = ext04b.build_audio_transform_filter_complex(
            self._answers(fade_in_seconds=1.0), [0])
        self.assertIn("afade=t=in", graph)
        self.assertTrue(labels)

    def test_an_unknown_duration_drops_only_the_fade_out(self):
        answers = self._answers(fade_in_seconds=1.0, fade_out_seconds=2.0)
        answers["format"] = {}
        audio = FFmWiz.build_encode_audio_processing_filter(answers)
        self.assertIn("afade=t=in", audio)
        self.assertNotIn("afade=t=out", audio)

    def test_a_missing_name_is_not_swallowed_as_an_unknown_duration(self):
        # The bug this guards: a bare `except Exception` around the timeline
        # lookup turned "this function is not importable here" into "this
        # output has no duration", and dropped every fade-out in silence.
        import ffmwiz.wizard_build_b as wbb
        real = wbb.encode_timeline_map

        def explode(_answers):
            raise NameError("encode_timeline_map")

        wbb.encode_timeline_map = explode
        try:
            with self.assertRaises(NameError):
                FFmWiz.build_encode_audio_processing_filter(
                    self._answers(fade_out_seconds=2.0))
        finally:
            wbb.encode_timeline_map = real


class TheConfigKeyReachesTheSameFilters(unittest.TestCase):
    """`video_look` in config.env, documented in Appendix B.

    Mode 2 never asks the question, so the config key is the ONLY way a
    non-interactive run reaches these filters. A key that is documented but not
    read is worse than one that does not exist.
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
        }
        real = FFmWiz.services.get_packet_sizes
        FFmWiz.services.get_packet_sizes = lambda _a: {}
        try:
            ext08.apply_config_video_options(answers, config,
                                             force_video_options=True)
        finally:
            FFmWiz.services.get_packet_sizes = real
        return answers

    def test_the_key_is_parsed_into_the_same_answers_the_prompt_writes(self):
        from ffmwiz.wizard_look import parse_look_tokens
        text = "180,gray,denoise=heavy,fadeout=2"
        applied = self._applied({"video_look": text})
        for key, value in parse_look_tokens(text).items():
            self.assertEqual(value, applied.get(key), key)

    def test_an_absent_key_leaves_the_chain_alone(self):
        applied = self._applied({})
        for key in FFmWiz.LOOK_ANSWER_KEYS:
            self.assertNotIn(key, applied)

    def test_n_means_none(self):
        self.assertNotIn("rotate_choice", self._applied({"video_look": "n"}))

    def test_a_stale_answer_is_cleared_by_a_config_that_omits_the_key(self):
        # The applier runs on a dict that may already carry a previous job's
        # answers. Leaving them would silently rotate an unrelated encode.
        from ffmwiz.support import ext08
        answers = self._applied({"video_look": "90cw"})
        real = FFmWiz.services.get_packet_sizes
        FFmWiz.services.get_packet_sizes = lambda _a: {}
        try:
            ext08.apply_config_video_options(answers, {"settings": {}},
                                             force_video_options=True)
        finally:
            FFmWiz.services.get_packet_sizes = real
        self.assertNotIn("rotate_choice", answers)

    def test_a_bad_value_is_rejected_not_ignored(self):
        # `fail()` prints before it exits; keep that off the suite's console.
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()),                 contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                self._applied({"video_look": "sideways"})
