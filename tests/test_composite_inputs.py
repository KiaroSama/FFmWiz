"""Multi-input compositing: overlay, picture-in-picture, stacks, audio mix.

The risk here is not filter syntax, it is INPUT INDICES. A job with a logo and
a music bed has three inputs, and the two halves of the graph address different
ones: `[0:v][1:v]overlay` for the picture, `[0:a][2:a]amix` for the sound. An
`amix` that assumed "the extra input" was 1 would reach for the logo's audio,
which does not exist -- the same class of mistake as the `-map_chapters 1` that
addressed an .srt because a subtitle input had shifted the base.

So the argv tests below always use THREE inputs. The real-media proofs -- which
show the result in pixels and tones rather than in the command string -- moved to
`test_composite_media.py` for file size; they prove
the result in pixels and in tones rather than in the command string: a logo
must appear at the corner that was asked for and at none of the others, the
left half of a side-by-side must be input 1, and a mix must carry both tones
with the second one measurably under the first.
"""
import shutil
import tempfile
import contextlib
import io
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz import encoding

from artifact_guard import NoLeakedArtifacts
from composite_test_helpers import FFMPEG, FFPROBE, requires_ffmpeg


class TheGraphAddressesTheRightInputs(unittest.TestCase):
    """Pure graph construction -- no encoding, three inputs throughout."""

    def _answers(self, **extra):
        answers = {
            "video_streams": [{"width": 320, "height": 180, "pix_fmt": "yuv420p"}],
            "audio_streams": [{"codec_type": "audio"}],
            "format": {"duration": "10.0"},
            "video_encoder": "libx264", "use_gpu": False, "output_ext": "mkv",
        }
        answers.update(extra)
        return answers

    def _graph(self, picture_input, audio_input, **extra):
        return FFmWiz.build_composite_filter_graph(
            self._answers(**extra), picture_input, audio_input)

    def test_a_logo_and_a_music_bed_address_different_inputs(self):
        # THE test this feature exists to keep honest. Input 1 is the logo and
        # input 2 is the music; swapping them silently produces a command that
        # either fails or mixes the wrong thing.
        chains, video, audio, _notes = self._graph(
            1, 2, composite_mode="overlay", composite_audio_weight=0.3)
        graph = ";".join(chains)
        self.assertIn("[0:v][1:v]overlay=", graph)
        self.assertIn("[2:a:0]", graph, "the mix must read the THIRD input")
        self.assertNotIn("[1:a", graph, "input 1 is the logo; it has no audio")
        self.assertEqual("[vout]", video)
        self.assertEqual("[aout]", audio)

    def test_the_picture_partner_is_never_read_from_the_audio_index(self):
        chains, _v, _a, _n = self._graph(1, 2, composite_mode="hstack")
        self.assertIn("[1:v]scale=", ";".join(chains))
        self.assertNotIn("[2:v]", ";".join(chains))

    def test_an_audio_only_partner_sits_at_input_one(self):
        # No picture partner, so the music is input 1 -- not 2. A builder that
        # hard-coded "the mix input is 2" would emit a stream that is not there.
        chains, video, audio, _notes = self._graph(None, 1)
        self.assertEqual("0:v:0", video, "an amix-only job keeps the source picture")
        self.assertIn("[0:a:0][1:a:0]amix=", ";".join(chains))
        self.assertEqual("[aout]", audio)

    def test_each_corner_produces_its_own_expression(self):
        seen = {}
        for corner in ("tl", "tr", "bl", "br"):
            chains, _v, _a, _n = self._graph(
                1, None, composite_mode="overlay", composite_corner=corner,
                composite_margin=10)
            seen[corner] = next(c for c in chains if "overlay=" in c).split("overlay=")[1]
        self.assertEqual(4, len(set(seen.values())), seen)
        self.assertTrue(seen["tl"].startswith("10:10"), seen["tl"])
        self.assertTrue(seen["tr"].startswith("W-w-10:10"), seen["tr"])
        self.assertTrue(seen["bl"].startswith("10:H-h-10"), seen["bl"])
        self.assertTrue(seen["br"].startswith("W-w-10:H-h-10"), seen["br"])

    def test_a_stack_scales_the_partner_to_the_shared_edge_and_says_so(self):
        # "Say what you did rather than silently letterboxing" -- the note is
        # part of the contract, not decoration.
        chains, _v, _a, notes = self._graph(1, None, composite_mode="hstack")
        self.assertIn("[1:v]scale=-2:180", ";".join(chains))
        self.assertTrue(any("180 px tall" in note for note in notes), notes)
        chains, _v, _a, notes = self._graph(1, None, composite_mode="vstack")
        self.assertIn("[1:v]scale=320:-2", ";".join(chains))
        self.assertTrue(any("320 px wide" in note for note in notes), notes)

    def test_the_inset_is_scaled_from_the_main_picture_width(self):
        chains, _v, _a, _n = self._graph(1, None, composite_mode="pip",
                                         composite_scale=0.25)
        self.assertIn("[1:v]scale=80:-2", ";".join(chains))

    def test_a_full_opacity_overlay_adds_no_alpha_filter(self):
        chains, _v, _a, _n = self._graph(1, None, composite_mode="overlay")
        self.assertNotIn("colorchannelmixer", ";".join(chains))
        chains, _v, _a, _n = self._graph(1, None, composite_mode="overlay",
                                         composite_opacity=0.5)
        # format=rgba first: without it a source with no alpha channel has
        # nothing for the mixer to scale and the overlay stays opaque.
        self.assertIn("format=rgba,colorchannelmixer=aa=0.5", ";".join(chains))

    def test_the_mix_ends_with_the_first_input_not_the_shortest(self):
        # `-shortest` counts every input, and a 3 s music bed under a 2 minute
        # film truncated the output to 3 s. duration=first is the fix.
        chains, _v, _a, _n = self._graph(None, 1)
        mix = next(c for c in chains if "amix=" in c)
        self.assertIn("duration=first", mix)
        self.assertNotIn("shortest", mix)

    def test_the_weight_is_carried_and_not_renormalised_away(self):
        chains, _v, _a, _n = self._graph(None, 1, composite_audio_weight=0.3)
        mix = next(c for c in chains if "amix=" in c)
        self.assertIn("weights=1 0.3", mix)
        # normalize=1 (the default) divides by the sum of the weights, so the
        # main track would be quietened by the very option meant to quieten the
        # bed.
        self.assertIn("normalize=0", mix)

    def test_a_silent_main_input_uses_the_bed_on_its_own(self):
        chains, _v, audio, notes = self._graph(None, 1, audio_streams=[])
        self.assertEqual("1:a:0", audio)
        self.assertFalse([c for c in chains if "amix=" in c],
                         "a mix of one source is not a mix")
        self.assertTrue(any("no audio" in note for note in notes), notes)

    def test_nothing_is_built_for_a_job_that_asked_for_none_of_this(self):
        chains, video, audio, notes = self._graph(None, None)
        self.assertEqual([], chains)
        self.assertEqual(("0:v:0", "0:a:0", []), (video, audio, notes))


class TheCommandCarriesTheInputsInOrder(NoLeakedArtifacts, unittest.TestCase):

    def _answers(self, **extra):
        answers = self.own({
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe",
            "input_path": Path("main.mkv"),
            "video_streams": [{"width": 320, "height": 180, "pix_fmt": "yuv420p"}],
            "audio_streams": [{"codec_type": "audio"}],
            "format": {"duration": "12.5"},
            "output_ext": "mkv", "video_codec": "H264", "use_gpu": False,
            "color_range_choice": "tv", "video_crf": 28, "audio_codec": "aac",
        })
        answers.update(extra)
        return answers

    def test_three_inputs_arrive_in_the_order_the_graph_assumes(self):
        cmd = FFmWiz.build_composite_command(
            self._answers(composite_mode="overlay",
                          composite_path=Path("logo.png"),
                          composite_audio_path=Path("bed.flac")),
            Path("out.mkv"))
        inputs = [cmd[i + 1] for i, part in enumerate(cmd) if part == "-i"]
        self.assertEqual(["main.mkv", "logo.png", "bed.flac"], inputs)
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[0:v][1:v]overlay=", graph)
        self.assertIn("[2:a:0]", graph)

    def test_the_output_is_capped_at_the_first_inputs_length(self):
        cmd = FFmWiz.build_composite_command(
            self._answers(composite_audio_path=Path("bed.flac")), Path("out.mkv"))
        self.assertNotIn("-shortest", cmd)
        self.assertEqual("12.500", cmd[cmd.index("-t") + 1])

    def test_a_copy_request_is_resolved_to_a_real_encoder(self):
        answers = self._answers(video_codec="copy",
                                composite_mode="overlay",
                                composite_path=Path("logo.png"))
        real = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        try:
            cmd = FFmWiz.build_composite_command(answers, Path("out.mkv"))
        finally:
            FFmWiz.appio.note = real
        self.assertNotIn("copy", cmd[cmd.index("-c:v") + 1])

    def test_a_job_with_nothing_to_composite_is_refused(self):
        with self.assertRaises(ValueError):
            FFmWiz.build_composite_command(self._answers(), Path("out.mkv"))

    def test_composite_active_reads_the_answers_the_step_writes(self):
        self.assertFalse(FFmWiz.composite_active({}))
        self.assertTrue(FFmWiz.composite_active({"composite_path": Path("a.png")}))
        self.assertTrue(FFmWiz.composite_active({"composite_audio_path": Path("a.wav")}))


@requires_ffmpeg


class TheWizardQuestionReachesTheBuilder(unittest.TestCase):
    """The step writes answer keys; the builder reads them.

    That seam breaks silently: renaming `composite_corner` on one side leaves a
    wizard that accepts the answer and a command that ignores it.
    """

    def _prompted(self, *replies):
        """Run the step with a FINITE script of answers.

        Finite because the step re-asks on a rejected value: a constant stub
        never terminates, and one such stub has already burned a wall timeout
        in this suite.
        """
        from ffmwiz import wizard_composite
        answers = {}
        script = iter(replies)
        real_ask, real_error = FFmWiz.appio.ask_raw, FFmWiz.appio.error
        FFmWiz.appio.ask_raw = lambda *a, **k: next(script)
        FFmWiz.appio.error = lambda *a, **k: None
        try:
            wizard_composite.step_video_composite(answers)
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.error = real_ask, real_error
        return answers

    def test_declining_writes_nothing(self):
        self.assertEqual({}, self._prompted("n"))
        self.assertEqual({}, self._prompted(""))

    def test_every_token_the_prompt_offers_reaches_the_graph(self):
        # A token the parser accepts but no builder acts on is a lie in the
        # hint text.
        from ffmwiz.wizard_composite import parse_composite_tokens
        base = {"video_streams": [{"width": 320, "height": 180}],
                "audio_streams": [{"codec_type": "audio"}]}
        cases = ["overlay", "logo", "watermark", "pip", "sbs", "hstack",
                 "vstack", "amix", "mix", "music",
                 "overlay,tl", "overlay,tr", "overlay,bl", "overlay,br",
                 "overlay,center", "overlay,margin=20", "overlay,opacity=0.4",
                 "pip,size=0.4", "amix,weight=0.5"]
        for text in cases:
            with self.subTest(token=text):
                answers = dict(base, **parse_composite_tokens(text))
                picture = 1 if answers.get("composite_mode") else None
                audio = 2 if answers.get("composite_audio_mix") else None
                chains, _v, _a, _n = FFmWiz.build_composite_filter_graph(
                    answers, picture, audio)
                self.assertTrue(chains, f"{text} reached no builder")

    def test_two_picture_modes_are_refused(self):
        from ffmwiz.wizard_composite import parse_composite_tokens
        with self.assertRaises(ValueError):
            parse_composite_tokens("overlay,vstack")

    def test_an_option_with_nothing_to_apply_to_is_refused(self):
        # Accepting `sbs,opacity=0.5` and ignoring the opacity is how a hint
        # ends up promising something the command never does.
        from ffmwiz.wizard_composite import parse_composite_tokens
        for text in ("sbs,opacity=0.5", "sbs,tr", "overlay,size=0.5",
                     "overlay,weight=0.5", "tr", "margin=10"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_composite_tokens(text)

    def test_out_of_range_numbers_are_refused_not_clamped(self):
        from ffmwiz.wizard_composite import parse_composite_tokens
        for text in ("overlay,opacity=2", "overlay,opacity=-1", "pip,size=3"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_composite_tokens(text)

    def test_a_rejected_answer_leaves_no_debris(self):
        self.assertEqual({}, self._prompted("overlay,vstack", "n"))

    def test_back_still_works(self):
        # The back token is `0`, the same one every other step accepts -- not
        # "b". A step that answered only to "b" would trap the user here.
        from ffmwiz import wizard_composite
        for token in sorted(FFmWiz.BACK_INPUT_TOKENS):
            with self.subTest(token=token):
                real_ask = FFmWiz.appio.ask_raw
                FFmWiz.appio.ask_raw = lambda *a, **k: token
                try:
                    with self.assertRaises(FFmWiz.Back):
                        wizard_composite.step_video_composite({})
                finally:
                    FFmWiz.appio.ask_raw = real_ask

    def test_back_out_of_the_file_question_leaves_no_half_answer(self):
        from ffmwiz import wizard_composite
        answers = {}
        script = iter(["overlay", "0"])
        real_ask, real_error = FFmWiz.appio.ask_raw, FFmWiz.appio.error
        FFmWiz.appio.ask_raw = lambda *a, **k: next(script)
        FFmWiz.appio.error = lambda *a, **k: None
        try:
            with self.assertRaises(FFmWiz.Back):
                wizard_composite.step_video_composite(answers)
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.error = real_ask, real_error
        self.assertNotIn("composite_path", answers)

    def test_an_answer_survives_into_a_command(self):
        tmp = Path(tempfile.mkdtemp(prefix="composite_step_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        logo = tmp / "logo.png"
        logo.write_bytes(b"not really a png")
        answers = {"input_path": tmp / "main.mkv", "_question_number": 3}
        script = iter(["overlay,tl,margin=25", str(logo)])
        real_ask, real_error = FFmWiz.appio.ask_raw, FFmWiz.appio.error
        real_load = FFmWiz.services.join_load_media_item
        FFmWiz.appio.ask_raw = lambda *a, **k: next(script)
        FFmWiz.appio.error = lambda *a, **k: None
        FFmWiz.services.join_load_media_item = lambda a, path, **k: {
            "path": path, "video_streams": [{"width": 40, "height": 40}],
            "audio_streams": []}
        try:
            from ffmwiz import wizard_composite
            wizard_composite.step_video_composite(answers)
        finally:
            FFmWiz.appio.ask_raw, FFmWiz.appio.error = real_ask, real_error
            FFmWiz.services.join_load_media_item = real_load
        self.assertEqual("overlay", answers["composite_mode"])
        self.assertEqual(logo, answers["composite_path"])
        chains, _v, _a, _n = FFmWiz.build_composite_filter_graph(
            dict(answers, video_streams=[{"width": 320, "height": 180}],
                 audio_streams=[]), 1, None)
        self.assertIn("overlay=25:25", ";".join(chains))
        self.assertIn("logo.png", wizard_composite.describe_composite(answers))

    def test_the_step_is_on_the_encode_path(self):
        # Registered but unreachable is the same as absent.
        import inspect
        from ffmwiz import wizard_flow
        self.assertIn('wizard_base.Step("video_composite"',
                      inspect.getsource(wizard_flow.run_wizard))


class CompositingAndReverseAreRefusedTogether(unittest.TestCase):
    """The combination has no builder, so it must be refused, not attempted.

    Every reverse route rebuilds its stages through `build_ffmpeg_command` or
    `build_join_encode_command`, and neither maps a second input. The one-shot
    composite branch owns that graph and a staged reverse never reaches it, so
    the job used to succeed with the overlay silently missing -- a finished
    file that is simply wrong, which is worse than a refusal.
    """

    def _run(self, **extra):
        answers = {"reverse_video": True, "ffmpeg": "ffmpeg"}
        answers.update(extra)
        noise = io.StringIO()
        with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
            code, elapsed = FFmWiz.execute_encode_plan(
                answers, ["ffmpeg", "-i", "in.mkv", "out.mkv"],
                total_duration=None, label="test")
        return code, elapsed, noise.getvalue()

    def test_an_overlay_with_a_video_reverse_is_refused(self):
        code, _elapsed, out = self._run(composite_mode="overlay")
        self.assertEqual(1, code)
        self.assertIn("cannot be combined", out)

    def test_an_audio_mix_with_a_video_reverse_is_refused(self):
        # `mix` sets composite_audio_mix and never composite_mode -- the same
        # asymmetry that made the audio-only composite fall through once
        # before.
        code, _elapsed, out = self._run(composite_audio_mix="mix")
        self.assertEqual(1, code)
        self.assertIn("cannot be combined", out)

    def test_an_overlay_with_an_audio_reverse_is_refused(self):
        code, _elapsed, out = self._run(
            reverse_video=False, reverse_audio=True, composite_mode="overlay")
        self.assertEqual(1, code)
        self.assertIn("cannot be combined", out)

    def test_the_refusal_names_the_way_out(self):
        # A refusal that does not say what to do instead is a dead end. The
        # user can get the same result in two passes.
        _code, _elapsed, out = self._run(composite_mode="overlay")
        self.assertIn("then reverse its output", out)

    def test_a_reverse_without_a_composite_is_untouched(self):
        # The direction that matters most, and the one a mutation check caught
        # missing: a guard that lost its composite condition would refuse EVERY
        # reversed job, and every other test here would still pass.
        #
        # BOTH reverse entry points are stubbed. Letting a real reverse run
        # here cost a 30-minute wall timeout once: the job reached ffmpeg with
        # a fixture that has no frames and then sat waiting, and the full suite
        # was killed at its ceiling instead of failing. A test for a GUARD has
        # no business starting an encode.
        noise = io.StringIO()
        reached = []
        rp = encoding.reverse_pipeline
        real_segmented = rp.run_segmented_reverse_main_encode
        real_runner = encoding.runtime.run_ffmpeg_with_progress
        rp.run_segmented_reverse_main_encode = lambda a: (reached.append("segmented"), (0, 1.0))[1]
        encoding.runtime.run_ffmpeg_with_progress = (
            lambda cmd, **kw: (reached.append("one-shot"), (0, 1.0))[1])
        try:
            with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
                code, _elapsed = encoding.execute_encode_plan(
                    {"reverse_video": True, "ffmpeg": "ffmpeg",
                     "format": {"duration": "600"}},
                    ["ffmpeg", "-i", "in.mkv", "out.mkv"],
                    total_duration=None, label="test")
        finally:
            rp.run_segmented_reverse_main_encode = real_segmented
            encoding.runtime.run_ffmpeg_with_progress = real_runner
        self.assertNotIn("cannot be combined", noise.getvalue())
        self.assertEqual(0, code)
        self.assertTrue(reached, "the job should have reached a runner, not a refusal")

    def test_a_composite_without_a_reverse_is_untouched(self):
        # The guard must not fire on the combination that DOES work, or it
        # would take the whole feature away.
        noise = io.StringIO()
        calls = []
        real = FFmWiz.runtime.run_ffmpeg_with_progress
        FFmWiz.runtime.run_ffmpeg_with_progress = (
            lambda cmd, **kw: (calls.append(cmd), (0, 1.0))[1])
        try:
            with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
                code, _elapsed = FFmWiz.execute_encode_plan(
                    {"composite_mode": "overlay", "ffmpeg": "ffmpeg"},
                    ["ffmpeg", "-i", "in.mkv", "out.mkv"],
                    total_duration=None, label="test")
        finally:
            FFmWiz.runtime.run_ffmpeg_with_progress = real
        self.assertEqual(0, code)
        self.assertEqual(1, len(calls), "the composite command should have run")


if __name__ == "__main__":
    unittest.main()


class TheAnswerActuallyReachesTheCommand(unittest.TestCase):
    """A question whose answer is ignored is worse than a missing feature.

    `build_composite_command` existed, was tested, and was never CALLED: the
    wizard asked for an overlay, recorded the answer, and then ran an ordinary
    encode with no overlay and no warning. Every argv test in this file passed
    the whole time, because they all called the builder directly.

    So this drives `step_start_now` -- the real dispatcher -- and checks which
    builder it chose.
    """

    def _dispatch(self, **extra):
        """Run step_start_now far enough to see which builder it picked."""
        from ffmwiz import wizard_b, wizard_build, wizard_build_b
        picked = []
        tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_dispatch_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        source = tmp / "in.mkv"
        source.write_bytes(b"")

        answers = {
            "ffmpeg": FFMPEG or "ffmpeg", "ffprobe": FFPROBE or "ffprobe",
            "input_path": source, "output_location": tmp, "output_ext": "mkv",
            "video_streams": [{"codec_type": "video", "width": 640, "height": 480,
                               "pix_fmt": "yuv420p"}],
            "audio_streams": [], "subtitle_streams": [], "data_streams": [],
            "streams": [], "probe": {"streams": [], "format": {}},
            "format": {"duration": "10.0"}, "audio_tracks": [],
            "video_codec": "H264", "video_encoder": "libx264", "use_gpu": False,
            "color_range_choice": "tv", "crf": 28, "preset": "ultrafast",
        }
        answers.update(extra)

        real = {
            "ordinary": wizard_build.build_ffmpeg_command,
            "composite": wizard_build_b.build_composite_command,
            "ask": FFmWiz.appio.ask_yes_no,
            "note": FFmWiz.appio.note,
            "summary": None,
        }
        wizard_build.build_ffmpeg_command = (
            lambda a: picked.append("ordinary") or ["ffmpeg", "out.mkv"])
        wizard_build_b.build_composite_command = (
            lambda a, out: picked.append("composite") or ["ffmpeg", str(out)])
        FFmWiz.appio.ask_yes_no = lambda *a, **k: False
        FFmWiz.appio.note = lambda *a, **k: None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                wizard_b.step_start_now(answers)
        except Exception:
            # The summary/colour-range machinery beyond the dispatch is not what
            # this test measures; the builder choice is already recorded.
            pass
        finally:
            wizard_build.build_ffmpeg_command = real["ordinary"]
            wizard_build_b.build_composite_command = real["composite"]
            FFmWiz.appio.ask_yes_no = real["ask"]
            FFmWiz.appio.note = real["note"]
        return picked

    def test_a_composite_answer_selects_the_composite_builder(self):
        picked = self._dispatch(composite_mode="overlay",
                                composite_corner="bottom-right",
                                composite_margin=10,
                                composite_input_path="logo.png")
        self.assertIn("composite", picked,
                      f"the overlay answer was ignored; builder chosen: {picked}")
        self.assertNotIn("ordinary", picked)

    def test_a_job_without_one_still_takes_the_ordinary_path(self):
        picked = self._dispatch()
        self.assertEqual(["ordinary"], picked)

    def test_an_audio_only_mix_reaches_the_composite_builder(self):
        picked = self._dispatch(composite_audio_mix=True,
                                composite_audio_path="bed.mp3")
        self.assertIn("composite", picked,
                      f"the mix answer was ignored; builder chosen: {picked}")
        self.assertNotIn("ordinary", picked)

    def test_a_picture_mode_still_reaches_it(self):
        picked = self._dispatch(composite_mode="overlay",
                                composite_input_path="logo.png")
        self.assertIn("composite", picked,
                      f"the overlay answer was ignored; builder chosen: {picked}")
        self.assertNotIn("ordinary", picked)

    def test_a_job_with_neither_key_still_takes_the_ordinary_encode(self):
        picked = self._dispatch()
        self.assertEqual(["ordinary"], picked)
