"""Every answer the wizard collects has to reach the builder that owns it.

Four defects of that shape, found by tracing a key from the question that
writes it to the command that would have to carry it:

  * The JOIN builder opened its audio chain on
    `audio_speed_transform_enabled or loudnorm_transform_enabled`, while the
    chain behind that gate also emits a gain and a fade. A volume-only or
    fade-only join therefore never reached it. The fade case is the loud one:
    the PICTURE fade is built separately and was applied, so the output faded
    to black over full-volume audio -- precisely what the audio chain's own
    comment says the audio fade exists to prevent. `audio_transform_enabled`
    is the predicate that covers everything the chain can emit, and its
    docstring already says a narrower gate drops the request silently.

  * `build_cut_filter_complex` carried the same narrow gate, twice, so the
    same volume/fade answer was dropped on any multi-range cut. A SINGLE
    keep range takes a different path and always worked, which is why this
    survived: the feature looked fine until you cut twice.

  * `raw_ffmpeg_args` -- the documented escape hatch, asked as the last
    question before the summary -- reached only `build_ffmpeg_command` and the
    Split per-part writer. `build_join_encode_command` and
    `build_composite_command` each own their whole command, and neither
    appended it, so on those two routes the answer did nothing.

  * `step_video_bitrate` dropped a stale `video_crf` on its numeric branch and
    not on `n=keep current value`. After backing out of the CRF question and
    answering `bitrate` + `n`, both keys survived: the summary reads
    `video_crf` first and announced "constant-quality CRF/CQ mode" while the
    command carried `-b:v`, and on a source with no detectable bitrate
    (`video_bitrate_kbps` is then None, which is falsy) the encode really did
    fall back to the stale CRF.

Three more of the "wired into one route but not the feature" shape:

  * `video_look`, `video_quick` and `video_composite` could never be asked at
    all. Each required BOTH `_unified_video_editor_used` and
    `_unified_video_editor_declined` to be falsy -- a copy of the crop
    questions' gate -- while the editor step always sets exactly one of them.
    `video_composite` is the one with no way back: its own comment records it
    as prompt-only, with no `config.env` key, so for a video output the whole
    feature had no entry point. On a join the pair stays shut on purpose:
    `step_start_now` dispatches a join before it looks at `composite_mode`,
    and a joined GIF is not a degraded output but an invalid command
    ("gif muxer supports only codec gif for type video", measured on FFmpeg 8).
    The `.gif` container answer is refused at the format question for the same
    reason.

  * `_output_ext_before_quick`, the container a declined quick output puts
    back, was never invalidated when the format question was answered again.

  * Folder Encode offered `nvenc_multipass` but never `cpu_two_pass`, although
    appendix A.15 documents the question for any single-output CPU encode and
    `execute_encode_plan` -- the executor every folder job runs through --
    already handles it.

And one that is stated rather than carried: `build_composite_command` applies
neither the picture chain nor the audio chain, so crop, resolution, frame
rate, picture filters, fades, speed, volume, LoudNorm, cuts and Split are all
dropped while the summary prints them. Carrying only part of that set would
desynchronise sound from picture, so the builder names what it cannot carry
instead (`composite_unsupported_answer_notes`), on the same rule as
`gif_unsupported_answer_notes`.

These assert on the built argv, not on a helper, because every one of these
defects lived in the gate or the omission in FRONT of a correct helper.
"""
import tempfile
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz import modes, wizard_quick

VIDEO = {"codec_type": "video", "codec_name": "h264", "width": 1280,
         "height": 720, "avg_frame_rate": "30/1", "color_range": "tv",
         "pix_fmt": "yuv420p", "bit_rate": "1500000"}
AUDIO = {"codec_type": "audio", "codec_name": "aac", "channels": 2,
         "sample_rate": "48000"}


class _Quiet(unittest.TestCase):
    """The builders narrate; the assertions are on the argv."""

    def setUp(self):
        self._note = FFmWiz.appio.note
        self._colour = FFmWiz.appio.USE_COLOR
        FFmWiz.appio.USE_COLOR = False
        FFmWiz.appio.note = lambda *a, **k: None
        self.tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_reach_"))

    def tearDown(self):
        FFmWiz.appio.note = self._note
        FFmWiz.appio.USE_COLOR = self._colour

    def _answers(self, **extra):
        source = self.tmp / "in.mkv"
        source.write_bytes(b"")
        answers = {
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "input_path": source,
            "output_location": self.tmp, "output_ext": "mkv",
            "color_range_choice": "tv", "video_codec": "H264",
            "use_gpu": False, "video_bitrate_kbps": 400,
            "audio_tracks": [0], "audio_codec": "aac",
            "audio_bitrate_kbps": 128, "format": {"duration": "10.0"},
            "video_streams": [VIDEO], "audio_streams": [AUDIO],
            "subtitle_streams": [], "data_streams": [],
            "streams": [VIDEO, AUDIO],
            "probe": {"streams": [VIDEO, AUDIO], "format": {}},
        }
        answers.update(extra)
        return answers


class TheJoinCarriesEveryAudioAnswer(_Quiet):
    def _join(self, **extra):
        first, second = self.tmp / "a.mkv", self.tmp / "b.mkv"
        first.write_bytes(b"")
        second.write_bytes(b"")
        answers = self._answers(input_path=first, **extra)
        items = [
            {"path": first, "streams": [VIDEO, AUDIO], "video_streams": [VIDEO],
             "audio_streams": [AUDIO], "format": {}, "duration": 5.0},
            {"path": second, "streams": [VIDEO, AUDIO], "video_streams": [VIDEO],
             "audio_streams": [AUDIO], "format": {}, "duration": 5.0},
        ]
        cmd = FFmWiz.build_join_encode_command(answers, items, self.tmp / "out.mkv")
        return answers, [str(part) for part in cmd]

    def test_a_volume_only_join_still_carries_the_gain(self):
        _answers, cmd = self._join(audio_volume=1.5)
        self.assertIn("volume=1.5", " ".join(cmd))

    def test_a_fade_only_join_fades_the_sound_with_the_picture(self):
        # Both halves or neither. The picture fade was never gated, so the
        # broken state was a video fading to black at full volume.
        _answers, cmd = self._join(fade_out_seconds=1.0)
        text = " ".join(cmd)
        self.assertIn("fade=t=out", text, "the picture fade should still be built")
        self.assertIn("afade", text, "the sound has to fade with the picture")

    def test_a_speed_join_is_unchanged(self):
        # The other direction: the gate that already worked must keep working,
        # and widening it must not double the gain.
        _answers, cmd = self._join(audio_volume=1.5, audio_speed_enabled=True,
                                   audio_speed_factor=2.0)
        self.assertEqual(1, " ".join(cmd).count("volume=1.5"))

    def test_a_join_that_asked_for_nothing_gets_no_audio_chain(self):
        # A gate that opens for everything is not a gate.
        _answers, cmd = self._join()
        text = " ".join(cmd)
        self.assertNotIn("volume=", text)
        self.assertNotIn("afade", text)

    def test_a_join_carries_the_raw_options_before_its_output(self):
        # "Last, immediately before the output" is the whole point of the
        # escape hatch: ffmpeg reads output options in order.
        _answers, cmd = self._join(raw_ffmpeg_args=["-tune", "film"])
        self.assertEqual(["-tune", "film"], cmd[-3:-1],
                         f"expected them just before the output: {cmd[-5:]}")

    def test_every_part_of_a_split_join_carries_the_raw_options(self):
        answers, cmd = self._join(separator_points=[4.0],
                                  raw_ffmpeg_args=["-tune", "film"])
        parts = answers.get("split_output_paths") or []
        self.assertEqual(2, len(parts), f"expected a two-part Split: {parts}")
        positions = [i for i, part in enumerate(cmd) if part == "-tune"]
        self.assertEqual(2, len(positions),
                         f"one per part, got {len(positions)}: {cmd}")
        for index in positions:
            self.assertEqual("film", cmd[index + 1])
            self.assertTrue(cmd[index + 2].endswith(".mkv"),
                            f"expected an output path after them: {cmd[index:index + 3]}")


class ACutCarriesEveryAudioAnswer(_Quiet):
    RANGES = [(0.0, 2.0), (4.0, 6.0)]

    def _cut(self, **extra):
        answers = self._answers(cut_keep_ranges=self.RANGES, **extra)
        return [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]

    def test_a_volume_only_cut_still_carries_the_gain(self):
        self.assertIn("volume=1.5", " ".join(self._cut(audio_volume=1.5)))

    def test_a_fade_only_cut_fades_the_sound_with_the_picture(self):
        text = " ".join(self._cut(fade_out_seconds=1.0))
        self.assertIn("fade=t=out", text, "the picture fade should still be built")
        self.assertIn("afade", text, "the sound has to fade with the picture")

    def test_a_single_keep_range_was_never_broken_and_still_works(self):
        # The path that hid the defect: one range takes the -ss/-t route and
        # always carried the gain, so the feature looked fine until you cut
        # twice. Pinned so a fix on one path cannot regress the other.
        answers = self._answers(cut_keep_ranges=[(0.0, 2.0)], audio_volume=1.5)
        cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
        self.assertIn("volume=1.5", " ".join(cmd))

    def test_a_cut_that_asked_for_nothing_gets_no_gain(self):
        text = " ".join(self._cut())
        self.assertNotIn("volume=", text)
        self.assertNotIn("afade", text)

    def test_the_one_range_branch_of_the_builder_carries_it_too(self):
        # Called directly, because both production call sites gate on
        # `multi_cut = len(cut_keep_ranges) > 1`, so nothing in the package
        # reaches this branch today. It is still part of the function's own
        # contract -- the guard above rejects only an EMPTY range list -- and
        # it carried the identical narrow gate, so the next caller that passes
        # one range would inherit the bug this file is about.
        answers = self._answers(audio_volume=1.5)
        graph = FFmWiz.build_cut_filter_complex(answers, [(0.0, 2.0)], 0)
        self.assertIn("volume=1.5", graph)


class TheCompositeCarriesTheRawOptions(_Quiet):
    def test_a_composite_puts_them_just_before_its_output(self):
        logo = self.tmp / "logo.png"
        logo.write_bytes(b"")
        answers = self._answers(composite_mode="overlay", composite_path=logo,
                                composite_corner="tr", composite_margin=10,
                                raw_ffmpeg_args=["-tune", "film"])
        cmd = [str(part) for part in
               FFmWiz.build_composite_command(answers, self.tmp / "out.mkv")]
        self.assertEqual(["-tune", "film"], cmd[-3:-1],
                         f"expected them just before the output: {cmd[-5:]}")

    def test_a_composite_without_them_adds_nothing(self):
        logo = self.tmp / "logo.png"
        logo.write_bytes(b"")
        answers = self._answers(composite_mode="overlay", composite_path=logo)
        cmd = [str(part) for part in
               FFmWiz.build_composite_command(answers, self.tmp / "out.mkv")]
        self.assertNotIn("-tune", cmd)


class TheCompositeSaysWhatItCannotCarry(_Quiet):
    """The composite builder owns its whole command and applies neither the
    picture chain nor the audio chain, so crop, resolution, frame rate,
    picture filters, fades, speed, volume, LoudNorm, cuts and Split are all
    dropped -- while the summary prints them exactly as for an ordinary
    encode. Not carried (applying only part of the set desynchronises sound
    from picture), but no longer dropped in silence.
    """

    def _notes(self, **extra):
        logo = self.tmp / "logo.png"
        logo.write_bytes(b"")
        answers = self._answers(composite_mode="overlay", composite_path=logo,
                                **extra)
        return FFmWiz.composite_unsupported_answer_notes(answers)

    def test_a_plain_composite_says_nothing(self):
        self.assertEqual([], self._notes())

    def test_the_geometry_answers_are_named_one_by_one(self):
        text = " ".join(self._notes(
            crop_enabled=True, crop_top=20, crop_left=10, crop_right=10,
            crop_bottom=20, resolution=(640, 360), fps=15,
            denoise_level="medium", fade_out_seconds=1.0))
        for wanted in ("crop", "resolution", "frame rate", "picture filters",
                       "fade"):
            self.assertIn(wanted, text, f"{wanted} is dropped without a word")

    def test_the_audio_answers_are_named(self):
        self.assertTrue(any("volume" in note for note in
                            self._notes(audio_volume=1.5)))

    def test_the_timeline_answers_are_named(self):
        self.assertTrue(any("cut ranges" in note for note in
                            self._notes(cut_keep_ranges=[(0.0, 2.0), (4.0, 6.0)])))
        self.assertTrue(any("Split" in note for note in
                            self._notes(separator_points=[4.0])))

    def test_the_notes_reach_the_user_before_the_command(self):
        # Emitted by the builder itself, so every route that builds a
        # composite -- not just the one the wizard takes -- carries them.
        logo = self.tmp / "logo.png"
        logo.write_bytes(b"")
        said = []
        FFmWiz.appio.note = lambda message, *a, **k: said.append(str(message))
        answers = self._answers(composite_mode="overlay", composite_path=logo,
                                crop_enabled=True, crop_top=20, crop_left=10,
                                crop_right=10, crop_bottom=20)
        FFmWiz.build_composite_command(answers, self.tmp / "out.mkv")
        self.assertTrue(any("NOT applied" in line for line in said),
                        f"the builder said nothing about the dropped crop: {said}")


class ChoosingABitrateRetiresTheCrf(unittest.TestCase):
    """The two answers are mutually exclusive; both keys must never coexist."""

    def setUp(self):
        self._ask = FFmWiz.appio.ask_raw
        self._packets = FFmWiz.services.get_packet_sizes
        self._estimate = FFmWiz.services.print_encode_size_estimate
        self._colour = FFmWiz.appio.USE_COLOR
        FFmWiz.appio.USE_COLOR = False
        FFmWiz.services.get_packet_sizes = lambda answers: {}
        FFmWiz.services.print_encode_size_estimate = lambda *a, **k: None

    def tearDown(self):
        FFmWiz.appio.ask_raw = self._ask
        FFmWiz.services.get_packet_sizes = self._packets
        FFmWiz.services.print_encode_size_estimate = self._estimate
        FFmWiz.appio.USE_COLOR = self._colour

    def _run(self, typed, **extra):
        script = list(typed)

        def fake_ask(prompt, *args, **kwargs):
            return script.pop(0) if script else ""

        FFmWiz.appio.ask_raw = fake_ask
        answers = {
            "video_streams": [VIDEO], "audio_streams": [], "format": {"duration": "10.0"},
            "output_ext": "mkv", "video_codec": "H264", "use_gpu": False,
            "ffprobe": "ffprobe", "input_path": Path("clip.mkv"),
            # What an earlier pass through this same step left behind.
            "video_crf": 23,
        }
        answers.update(extra)
        FFmWiz.step_video_bitrate(answers)
        return answers

    def test_keeping_the_current_bitrate_drops_a_stale_crf(self):
        answers = self._run(["bitrate", "n"])
        self.assertTrue(answers.get("video_bitrate_keep"))
        self.assertNotIn("video_crf", answers,
                         "a bitrate answer and a CRF answer cannot both stand: "
                         "the summary reports the CRF and the command carries "
                         f"-b:v. {answers.get('video_bitrate_kbps')=}")

    def test_typing_a_bitrate_drops_a_stale_crf(self):
        answers = self._run(["bitrate", "800"])
        self.assertEqual(800, answers["video_bitrate_kbps"])
        self.assertNotIn("video_crf", answers)

    def test_choosing_crf_still_drops_the_bitrate(self):
        # The other direction, already correct; pinned so the pair stays
        # symmetric.
        answers = self._run(["crf", "20"])
        self.assertEqual(20, answers["video_crf"])
        self.assertNotIn("video_bitrate_kbps", answers)
        self.assertNotIn("video_bitrate_keep", answers)


class DecliningTheEditorStillAsksTheTerminalQuestions(unittest.TestCase):
    """Three questions had a gate that could never open.

    `step_unified_video_editor_for_encode` always sets exactly one of
    `_unified_video_editor_used` / `_unified_video_editor_declined`, and
    `video_look`, `video_quick` and `video_composite` each required BOTH to be
    falsy -- a copy of the crop questions' gate, where declining means
    something (the decline zeroes the crop margins). Declining a GRAPHICAL
    editor says nothing about denoise, a GIF or a watermark, none of which
    that editor can set, so all three prompts were dead on both branches.

    `video_composite` is the one with no way back: its own comment records it
    as prompt-only, with no `config.env` key, so for a video output the whole
    feature had no entry point. `video_look` and `video_quick` at least kept
    their config route, which is why the builders behind them still work.

    Driven through the real `run_wizard`, so it reads the actual step list.
    """

    LATER_STEP = "step_video_bitrate"

    class _Stop(Exception):
        pass

    def setUp(self):
        from ffmwiz import (wizard_composite, wizard_flow_b, wizard_look,
                            wizard_raw, wizard_steps)
        self.calls = []
        self._colour = FFmWiz.appio.USE_COLOR
        FFmWiz.appio.USE_COLOR = False
        targets = [
            (wizard_steps, "step_input_path"),
            (wizard_steps, "step_join_additional_inputs_for_encode"),
            (wizard_steps, "step_output_location"),
            (wizard_steps, "step_output_format"),
            (wizard_steps, "step_video_codec"),
            (wizard_steps, "step_use_gpu"),
            (wizard_steps, "step_crop_enabled"),
            (wizard_look, "step_video_look"),
            (wizard_quick, "step_quick_output"),
            (wizard_composite, "step_video_composite"),
            (wizard_raw, "step_audio_volume"),
            (wizard_raw, "step_raw_ffmpeg_args"),
            (wizard_steps, "step_video_bitrate"),
            (wizard_flow_b, "step_video_speed_reverse_for_encode"),
        ]
        self._saved = [(m, n, getattr(m, n)) for m, n in targets]
        self._saved.append((wizard_steps, "step_unified_video_editor_for_encode",
                            wizard_steps.step_unified_video_editor_for_encode))
        for module, name in targets:
            setattr(module, name, self._stub(name))
        self._wizard_steps = wizard_steps

    def tearDown(self):
        for module, name, original in self._saved:
            setattr(module, name, original)
        FFmWiz.appio.USE_COLOR = self._colour

    def _stub(self, name):
        def run(answers):
            self.calls.append(name)
            if name == self.LATER_STEP:
                raise self._Stop()
        return run

    def _run(self, declined, **extra):
        def unified(answers):
            self.calls.append("step_unified_video_editor_for_encode")
            answers["_unified_video_editor_used"] = not declined
            answers["_unified_video_editor_declined"] = declined
            if declined:
                answers["crop_enabled"] = False
                answers["video_speed_enabled"] = False
                answers["cut_keep_ranges"] = []

        self._wizard_steps.step_unified_video_editor_for_encode = unified
        answers = {
            "output_ext": "mp4", "video_codec": "H265", "use_gpu": False,
            "video_streams": [{"codec_type": "video", "width": 100,
                               "height": 100, "color_range": "tv"}],
            "audio_streams": [], "subtitle_streams": [],
            "format": {"duration": "100"},
        }
        answers.update(extra)
        try:
            FFmWiz.run_wizard(answers)
        except self._Stop:
            pass
        return list(self.calls)

    def test_declining_the_editor_reaches_all_three(self):
        calls = self._run(declined=True)
        for name in ("step_video_look", "step_quick_output",
                     "step_video_composite"):
            self.assertIn(name, calls, f"{name} is unreachable: {calls}")

    def test_accepting_the_editor_still_skips_them(self):
        # The stated contract for the OTHER branch: "the unified editor path is
        # contractually prompt-free". Widening the gate must not break it.
        calls = self._run(declined=False)
        for name in ("step_video_look", "step_quick_output",
                     "step_video_composite"):
            self.assertNotIn(name, calls, f"{name} should stay silent: {calls}")

    def test_declining_still_skips_the_legacy_crop_question(self):
        # Unchanged, and deliberately so: declining the editor ZEROES the crop
        # margins, which is what makes skipping that question meaningful.
        self.assertNotIn("step_crop_enabled", self._run(declined=True))

    def test_a_join_is_asked_only_the_one_its_builder_can_honour(self):
        # `step_start_now` sends a join to `build_join_encode_command`, which
        # applies the picture filters but has no quick-output plan and never
        # looks at `composite_mode` -- the join branch runs BEFORE that test.
        # Asking either question there collects an answer no builder reads,
        # and the GIF case is worse than dropped: it forces `output_ext=gif`
        # and then builds `-c:v libx264 -c:a aac` into a .gif the muxer
        # refuses. Same exclusion `cpu_two_pass_applicable` already makes.
        calls = self._run(declined=True, join_input_items=[
            {"path": Path("b.mkv"), "video_streams": [VIDEO],
             "audio_streams": [], "streams": [VIDEO], "format": {},
             "duration": 5.0}])
        self.assertIn("step_video_look", calls,
                      f"a join CAN carry the picture filters: {calls}")
        self.assertNotIn("step_quick_output", calls)
        self.assertNotIn("step_video_composite", calls)


class ReAnsweringTheFormatRetiresTheQuickSnapshot(unittest.TestCase):
    """A quick output remembers the container to put back when it is declined.

    That snapshot is taken at the quick-output question and was never
    invalidated, so a Back to the FORMAT question in between made it stale:
    ask for a GIF, go back and choose a different container, come forward and
    decline the quick output, and the declined answer's snapshot overwrote the
    container the user had just chosen.
    """

    def setUp(self):
        self._ask = FFmWiz.appio.ask_raw
        self._colour = FFmWiz.appio.USE_COLOR
        FFmWiz.appio.USE_COLOR = False
        self.script = []
        FFmWiz.appio.ask_raw = lambda *a, **k: (
            self.script.pop(0) if self.script else "")
        self.answers = {"input_path": Path("clip.mkv"),
                        "video_streams": [VIDEO], "audio_streams": [],
                        "format": {"duration": "10"}}

    def tearDown(self):
        FFmWiz.appio.ask_raw = self._ask
        FFmWiz.appio.USE_COLOR = self._colour

    def _type(self, value, step):
        self.script[:] = [value]
        step(self.answers)

    def test_the_container_chosen_after_a_gif_survives_declining_it(self):
        self._type("mkv", FFmWiz.step_output_format)
        self._type("gif", wizard_quick.step_quick_output)
        self.assertEqual("gif", self.answers["output_ext"])
        self._type("mp4", FFmWiz.step_output_format)     # Back, then a new answer
        self._type("n", wizard_quick.step_quick_output)        # forward, and decline
        self.assertEqual("mp4", self.answers["output_ext"],
                         "the declined quick output put back a container the "
                         "user had already replaced")

    def test_declining_without_a_second_format_answer_still_restores(self):
        # The behaviour the snapshot exists for, unchanged.
        self._type("mkv", FFmWiz.step_output_format)
        self._type("gif", wizard_quick.step_quick_output)
        self._type("n", wizard_quick.step_quick_output)
        self.assertEqual("mkv", self.answers["output_ext"])

    def test_a_join_cannot_choose_the_gif_container(self):
        # The other route into the same broken command the `video_quick` gate
        # now blocks: `quick_output_mode` reads a `.gif` container as a request
        # for the palette pipeline, which the join builder does not have.
        # Measured on FFmpeg 8: "gif muxer supports only codec gif for type
        # video", header write fails.
        self.answers["join_input_items"] = [{"path": Path("b.mkv")}]
        said = []
        real_error = FFmWiz.appio.error
        FFmWiz.appio.error = lambda message, *a, **k: said.append(str(message))
        self.addCleanup(lambda: setattr(FFmWiz.appio, "error", real_error))
        self.script[:] = ["gif", "mkv"]              # refused, then accepted
        FFmWiz.step_output_format(self.answers)
        self.assertEqual("mkv", self.answers["output_ext"])
        self.assertTrue(any("cannot be a GIF" in line for line in said), said)

    def test_a_single_input_may_still_choose_gif(self):
        self._type("gif", FFmWiz.step_output_format)
        self.assertEqual("gif", self.answers["output_ext"])


class FolderEncodeAsksTheCpuTwoPassQuestion(unittest.TestCase):
    """Mode 4 offered NVENC multipass but not its CPU counterpart.

    Driven through the real `run_folder_settings_wizard` with the prompts
    stubbed, so it reads the actual step list rather than a copy of it.
    """

    STEPS = ("step_output_format", "step_video_codec", "step_use_gpu",
             "step_unified_video_editor_for_encode", "step_crop_enabled",
             "step_video_bitrate", "step_nvenc_multipass", "step_cpu_two_pass",
             "step_resolution", "step_video_speed_reverse_for_encode")
    # Whichever comes first: a stream-copy batch skips every re-encode
    # question, so stopping only at `resolution` would run off the end of the
    # stubs and block on a real prompt.
    STOP_AT = ("step_resolution", "step_video_speed_reverse_for_encode")

    class _Stop(Exception):
        pass

    def setUp(self):
        self.calls = []
        self._saved = [(modes, name, getattr(modes, name)) for name in self.STEPS
                       if hasattr(modes, name)]
        self._saved += [(modes.wizard, name, getattr(modes.wizard, name))
                        for name in self.STEPS if hasattr(modes.wizard, name)]
        for module, name, _original in self._saved:
            setattr(module, name, self._stub(name))

    def tearDown(self):
        for module, name, original in self._saved:
            setattr(module, name, original)

    def _stub(self, name):
        def run(answers):
            self.calls.append(name)
            if name in self.STOP_AT:
                raise self._Stop()
        return run

    def _run(self, **extra):
        answers = {
            "ffmpeg": "ffmpeg", "output_ext": "mkv", "video_codec": "H264",
            "use_gpu": False, "video_bitrate_kbps": 800,
            "video_streams": [VIDEO], "audio_streams": [], "subtitle_streams": [],
            "format": {"duration": "10"}, "_folder_encode_mode": True,
            "_disable_graphical_editors": True,
        }
        answers.update(extra)
        try:
            modes.run_folder_settings_wizard(answers)
        except self._Stop:
            pass
        return answers

    def test_a_cpu_bitrate_batch_is_offered_two_pass(self):
        self._run()
        self.assertIn("step_cpu_two_pass", self.calls,
                      f"Folder Encode never asks it: {self.calls}")

    def test_a_gpu_batch_is_not(self):
        # cpu_two_pass_applicable still hides it where it cannot work, so the
        # new step cannot add a question to a job that would ignore it.
        self._run(use_gpu=True)
        self.assertNotIn("step_cpu_two_pass", self.calls)

    def test_a_stream_copy_batch_is_not(self):
        self._run(video_codec="copy")
        self.assertNotIn("step_cpu_two_pass", self.calls)


class TheAudioOnlyJoinCarriesEveryAudioAnswer(_Quiet):
    """Menu 12 builds its own command and had the same narrow gate.

    `build_join_audio_encode_command` named speed and LoudNorm while the chain
    it calls also emits the gain and the fade, and it never appended the raw
    escape hatch that every other builder honours.
    """

    def _join(self, **extra):
        from ffmwiz.support.ext04b import build_join_audio_encode_command
        items = []
        for name in ("a.m4a", "b.m4a"):
            path = self.tmp / name
            path.write_bytes(b"")
            items.append({"path": path, "name": name, "duration": 5.0})
        answers = self._answers(**extra)
        answers["join_input_items"] = items
        return build_join_audio_encode_command(
            answers, items, self.tmp / "joined.m4a")

    def test_a_volume_only_join_still_carries_the_gain(self):
        self.assertIn("volume=1.5", " ".join(self._join(audio_volume=1.5)))

    def test_a_fade_only_join_still_fades(self):
        self.assertIn("afade", " ".join(self._join(fade_out_seconds=1.0)))

    def test_a_join_that_asked_for_nothing_gets_the_bare_reset(self):
        # Removing the gate must not start emitting a chain for a plain join.
        text = " ".join(self._join())
        self.assertIn("asetpts=PTS-STARTPTS", text)
        for filt in ("volume=", "afade", "atempo", "loudnorm"):
            self.assertNotIn(filt, text)

    def test_it_carries_the_raw_options_before_its_output(self):
        cmd = self._join(raw_ffmpeg_args=["-tune", "film"])
        self.assertIn("-tune", cmd)
        self.assertLess(cmd.index("-tune"), len(cmd) - 1,
                        "the raw options belong before the output path")
        self.assertTrue(cmd[-1].endswith("joined.m4a"))


if __name__ == "__main__":
    unittest.main()
