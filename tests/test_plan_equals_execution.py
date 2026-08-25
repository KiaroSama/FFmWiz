"""Regression: the exported plan IS the job, stage for stage (D05-D08).

`bounded_reverse_plan()` is a second implementation of the pipeline, and it
drifted from the executor in the way a second implementation always does. Its
`described()` helper invented the intermediates instead of describing them: it
hardcoded every video codec to `h264`, emptied `subtitle_streams`, and reused
the SOURCE `format` dict -- so the descriptor carried input 1's duration no
matter what the preceding stage had written.

Two user-visible failures came from that one line. A forward join of two 2 s
inputs was described with 2.023 s, so the plan reversed about one input:

    AUTO_DURATION   4.332   AUTO_COLORS   ['blue', 'red']
    MANUAL_DURATION 2.3     MANUAL_COLORS ['red', 'missing']

And a 0.5x speed change doubles the reversed intermediate, but the Split still
cut against the source timeline:

    AUTO_PARTS   [('slow_Part01.mkv', 2.023), ('slow_Part02.mkv', 5.9)]
    MANUAL_PARTS [('slow_Part01.mkv', 2.023), ('slow_Part02.mkv', 2.04)]

Comparing finished media catches those two cases. It does not catch the NEXT
drift, so the load-bearing test here compares the plan's stage list against the
commands the executor actually issues, argument for argument, with scratch
paths normalised to their basenames. Anything the two decide differently shows
up as a diff instead of as a wrong file six months later.

Subtitles: the REVERSE stage is compared with subtitle-carrying inputs. The
planner builds its own retimed tracks from the sources -- which exist at plan
time -- and hands them to the builder, instead of trying to extract them from
an intermediate it has not written yet.

KNOWN GAP, measured and deliberately not asserted as acceptable: a SPLIT stage
slices its subtitles into per-part files (`part02_track00.srt`) by a different
mechanism, which still reads the intermediate. So the split comparisons use the
subtitle-free twins and the exported plan for a Split still omits the per-part
subtitle inputs. Closing it needs the same treatment the reverse stage just
received, applied to the per-part slicer.
"""
import json
import re
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
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

# Deliberately UNEQUAL, so a plan that measures only input 1 cannot come out
# right by coincidence.
CLIPS = (("red", 440, 2.0), ("blue", 880, 3.0), ("green", 1320, 1.0))


def _run(args, timeout=600):
    return subprocess.run([str(part) for part in args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace", timeout=timeout)


class PlanMatchesExecution(NoLeakedArtifacts, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not (FFMPEG and FFPROBE):
            raise unittest.SkipTest("ffmpeg/ffprobe not on PATH")
        cls._root = Path(tempfile.mkdtemp(prefix="ffmwiz_planexec_"))
        cls.inputs = []
        cls.plain_inputs = []
        for name, (colour, tone, seconds) in zip("abc", CLIPS):
            path = cls._root / f"{name}.mkv"
            # Each input carries a subtitle. Without one, a descriptor that
            # declares `subtitle_streams = []` looks identical to a correct
            # one, and the emptying that D07 is about goes unobserved.
            srt = cls._root / f"{name}.srt"
            cue = (f"1\n{FFmWiz.srt_timestamp(0.2)} --> "
                   f"{FFmWiz.srt_timestamp(min(0.8, seconds - 0.1))}\n"
                   f"{name.upper()}\n\n")
            srt.write_text(cue, encoding="utf-8", newline="\n")
            result = _run([
                FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"color=c={colour}:s=160x120:r=30:d={seconds}",
                "-f", "lavfi", "-i", f"sine=frequency={tone}:duration={seconds}",
                "-i", srt,
                "-map", "0:v", "-map", "1:a", "-map", "2:s",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                # `-t`, not `-shortest`: the SRT counts as an input for
                # `-shortest`, so a cue ending at 0.8 s truncated a 2 s clip to
                # 0.8 s and every duration measured below was silently wrong.
                "-c:a", "aac", "-c:s", "srt", "-t", str(seconds), path])
            if result.returncode != 0:
                raise unittest.SkipTest(f"could not build {name}: {result.stderr[-400:]}")
            cls.inputs.append(path)
            # A subtitle-free twin. The reverse stage retimes subtitles by
            # EXTRACTING them from its input, and for a join that input is the
            # intermediate the plan has not written yet, so the planned and the
            # executed segment commands cannot agree on the subtitle block
            # until the planner is given a pre-built track. The join
            # comparisons use these; the single-input comparisons, whose
            # reverse input is the real source, keep the subtitles.
            plain = cls._root / f"{name}_plain.mkv"
            _run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                  "-i", path, "-map", "0:v", "-map", "0:a", "-c", "copy", plain])
            cls.plain_inputs.append(plain)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="planexec_case_"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = lambda *a, **k: None
        self.addCleanup(lambda: setattr(FFmWiz.appio, "note", self._real_note))

    # ---- fixtures --------------------------------------------------------
    def _probe(self, path, *args):
        return json.loads(_run([FFPROBE, "-v", "error", "-print_format", "json",
                                *args, path]).stdout or "{}")

    def _item(self, path):
        info = self._probe(path, "-show_format", "-show_streams")
        return {"path": path, "probe": info, "format": info["format"],
                "streams": info["streams"],
                "video_streams": [s for s in info["streams"] if s["codec_type"] == "video"],
                "audio_streams": [s for s in info["streams"] if s["codec_type"] == "audio"],
                "subtitle_streams": [s for s in info["streams"] if s["codec_type"] == "subtitle"],
                "data_streams": [],
                "duration": float(info["format"]["duration"])}

    def _answers(self, out, inputs, **extra):
        items = [self._item(path) for path in inputs]
        answers = self.own({
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": items[0]["path"],
            "probe": items[0]["probe"], "format": items[0]["format"],
            "output_location": out,
            "video_streams": items[0]["video_streams"],
            "audio_streams": items[0]["audio_streams"],
            "subtitle_streams": items[0]["subtitle_streams"],
            "subtitle_tracks": [0], "keep_source_subtitles": True,
            "output_ext": "mkv", "audio_tracks": [0],
            "color_range_choice": "tv", "video_encoder": "libx264", "crf": 28,
            "preset": "ultrafast", "audio_codec": "aac",
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True,
        })
        if len(items) > 1:
            answers["join_input_items"] = items[1:]
        answers.update(extra)
        answers["output_path"] = out / "result.mkv"
        FFmWiz.artifact_lease(answers)
        return answers

    # ---- normalising a stage list ----------------------------------------
    @staticmethod
    def _normalise(command):
        """Argument list with every path reduced to its basename.

        The executor works in a `TemporaryDirectory` and the planner in
        `<stem>_plan`, so the scratch locations legitimately differ. Everything
        else must not.
        """
        out = []
        for part in command:
            text = str(part)
            if "\\" in text or "/" in text:
                text = re.sub(r"[^\s\"']*[\\/]", "", text)
            out.append(text)
        return out

    def _executed(self, answers):
        commands = []
        real_runner = encoding.run_ffmpeg_with_progress

        def spy(cmd, **kwargs):
            commands.append([str(part) for part in cmd])
            return real_runner(cmd, **kwargs)

        encoding.run_ffmpeg_with_progress = spy
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                code, _elapsed = encoding.run_bounded_reverse_pipeline(answers)
        finally:
            encoding.run_ffmpeg_with_progress = real_runner
        self.assertEqual(0, code, noise.getvalue()[-1500:])
        return commands

    def _planned(self, answers):
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            stages = encoding.bounded_reverse_plan(answers, self._tmp / "planned_ws")
        return [cmd for _label, cmd in stages]

    def _compare(self, label, sources=None, **extra):
        """Plan one job and execute an identical one; the argv must agree."""
        sources = sources or self.inputs[:2]
        planning = self._tmp / f"{label}_plan"
        planning.mkdir(parents=True, exist_ok=True)
        planned = self._planned(self._answers(planning, sources, **extra))

        running = self._tmp / f"{label}_run"
        running.mkdir(parents=True, exist_ok=True)
        executed = self._executed(self._answers(running, sources, **extra))

        self.assertEqual(len(planned), len(executed),
                         f"{label}: {len(planned)} planned stages against "
                         f"{len(executed)} executed commands")
        for index, (want, got) in enumerate(zip(planned, executed)):
            self._assert_same_command(self._normalise(want), self._normalise(got),
                                      f"{label}: stage {index + 1}")

    NUMBER = re.compile(r"\d+\.\d+")

    def _assert_same_command(self, want, got, where):
        """Identical argv, except where the plan can only ESTIMATE a duration.

        Every flag, filter name, label, map and path must match exactly -- the
        SHAPE of each argument is compared with its decimal numbers blanked
        out, so a different filter, a missing `-map_chapters` or a renamed
        scratch file still fails loudly.

        The numbers themselves get a tolerance, because one of them the plan
        genuinely cannot reproduce. The executor probes the intermediate it has
        just written and reads the span its container reports; on a
        concat-produced file that is one frame past the frames it actually
        holds -- 5.033 for 150 frames at 30 fps, against the 5.000 the plan
        computes from the inputs' picture spans. A speed change scales the gap
        with it: at 0.5x the split trims read 10.113 against 10.000. Both cover
        the whole picture.

        The tolerance is the larger of a couple of frames and 3%, which still
        fails the defect this replaced by a wide margin: 2.023 against 5.039 is
        149% apart, not 1%.
        """
        self.assertEqual(len(want), len(got), f"{where}: argument count differs")
        for position, (left, right) in enumerate(zip(want, got)):
            if left == right:
                continue
            self.assertEqual(
                self.NUMBER.sub("#", left), self.NUMBER.sub("#", right),
                f"{where}: argument {position} differs in more than its numbers\n"
                f"  plan: {left}\n  run:  {right}")
            for a, b in zip(self.NUMBER.findall(left), self.NUMBER.findall(right)):
                a, b = float(a), float(b)
                allowed = max(0.2, 0.03 * max(abs(a), abs(b)))
                self.assertLessEqual(
                    abs(a - b), allowed,
                    f"{where}: argument {position} has {a} in the plan and {b} "
                    f"in the run -- {abs(a - b):.3f}s apart, over the {allowed:.3f}s "
                    "an estimated duration may drift")

    # ---- D05 -------------------------------------------------------------
    def test_a_two_input_join_reverse_plans_what_it_runs(self):
        self._compare("join2")

    def test_a_three_input_join_reverse_plans_what_it_runs(self):
        planning = self._tmp / "join3_plan"
        planning.mkdir(parents=True, exist_ok=True)
        planned = self._planned(self._answers(planning, self.inputs))
        running = self._tmp / "join3_run"
        running.mkdir(parents=True, exist_ok=True)
        executed = self._executed(self._answers(running, self.inputs))
        self.assertEqual(len(planned), len(executed))
        for index, (want, got) in enumerate(zip(planned, executed)):
            self._assert_same_command(self._normalise(want), self._normalise(got),
                                      f"three-input stage {index + 1}")

    def test_the_plan_covers_the_whole_joined_timeline(self):
        # The defect in its own terms: the reverse stage used to be bounded to
        # input 1's duration, so most of the joined timeline was never touched.
        planning = self._tmp / "coverage"
        planning.mkdir(parents=True, exist_ok=True)
        answers = self._answers(planning, self.plain_inputs[:2])
        noise = StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            stages = encoding.bounded_reverse_plan(answers, planning / "ws")
        windows = [float(cmd[cmd.index("-t") + 1])
                   for label, cmd in stages
                   if label.startswith("Reverse segment") and "-t" in cmd]
        self.assertTrue(windows, "no bounded reverse segment was planned")
        joined = CLIPS[0][2] + CLIPS[1][2]
        self.assertAlmostEqual(joined, sum(windows), delta=0.2,
                               msg=f"segments cover {sum(windows)}s of a {joined}s "
                                   "joined timeline")

    # ---- D06 -------------------------------------------------------------
    def test_every_speed_change_plans_what_it_runs(self):
        for factor in (0.5, 0.75, 1.5, 2.0):
            with self.subTest(speed=factor):
                self._compare(f"speed{factor}".replace(".", "_"),
                              sources=self.plain_inputs[:2],
                              separator_points=[2.0], video_speed_factor=factor,
                              audio_speed_from_video=True)

    def test_a_single_input_reverse_split_plans_what_it_runs(self):
        self._compare("solo", sources=self.plain_inputs[:1], separator_points=[1.0])

    def test_the_descriptor_carries_the_subtitle_streams_it_is_given(self):
        # `described()` used to set `subtitle_streams = []`, so every later
        # stage of the plan was blind to a track the intermediate really has.
        # Asserted on the descriptor because the plan's subtitle COMMANDS
        # cannot yet be compared -- see the module docstring.
        planning = self._tmp / "descsubs"
        planning.mkdir(parents=True, exist_ok=True)
        answers = self._answers(planning, self.inputs[:2])
        seen = []
        real_stage = encoding.stage_answers
        encoding.stage_answers = (
            lambda a, owns: (seen.append(a) or real_stage(a, owns)))
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                encoding.bounded_reverse_plan(answers, planning / "ws")
        finally:
            encoding.stage_answers = real_stage
        described = [a for a in seen if str(a.get("input_path", "")).endswith(
            "joined_forward.mkv")]
        self.assertTrue(described, "the joined intermediate was never described")
        self.assertEqual(len(answers["subtitle_streams"]),
                         len(described[0].get("subtitle_streams") or []),
                         "the descriptor emptied the subtitle streams")

    def test_cuts_with_slow_motion_and_a_split_plan_what_they_run(self):
        self._compare("cutslow", sources=self.plain_inputs[:2],
                      separator_points=[1.5],
                      cut_keep_ranges=[(0.0, 1.0), (2.0, 4.0)],
                      video_speed_factor=0.5, audio_speed_from_video=True)

    def test_slow_motion_splits_against_the_stretched_timeline(self):
        # The split points must land on the PROCESSED clock: a 5 s joined
        # timeline at 0.5x is 10 s, so a cut at 2 s must leave ~8 s after it.
        planning = self._tmp / "stretched"
        planning.mkdir(parents=True, exist_ok=True)
        answers = self._answers(planning, self.plain_inputs[:2], separator_points=[2.0],
                                video_speed_factor=0.5, audio_speed_from_video=True)
        seen = []
        real_build = encoding.build_ffmpeg_command
        encoding.build_ffmpeg_command = (
            lambda built, *a, **k: (seen.append(built) or real_build(built, *a, **k)))
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                encoding.bounded_reverse_plan(answers, planning / "ws")
        finally:
            encoding.build_ffmpeg_command = real_build
        # The split stage builds into its OWN copy, so the intervals it
        # resolves are written there rather than back onto the job's answers.
        intervals = next((built.get("split_part_intervals") for built in reversed(seen)
                          if built.get("split_part_intervals")), [])
        self.assertEqual(2, len(intervals), intervals)
        self.assertAlmostEqual((CLIPS[0][2] + CLIPS[1][2]) * 2, intervals[-1][1],
                               delta=0.4,
                               msg=f"the split stops at {intervals[-1][1]}s")

    # ---- D07 -------------------------------------------------------------
    def test_the_descriptor_reports_the_codec_the_stage_writes(self):
        self.assertEqual("hevc", FFmWiz.intermediate_video_codec_name(
            {"video_codec": "H265", "use_gpu": False}))
        self.assertEqual("h264", FFmWiz.intermediate_video_codec_name(
            {"video_codec": "H264", "use_gpu": False}))

    def test_an_unrecognised_encoder_is_not_renamed_to_h264(self):
        # Wrong-but-recognisable beats confidently wrong: a substituted `h264`
        # silently changes later container and codec decisions.
        self.assertEqual("libtheora", FFmWiz.intermediate_video_codec_name(
            {"video_encoder": "libtheora", "video_codec": "libtheora",
             "use_gpu": False}))

    def test_an_hevc_job_plans_what_it_runs(self):
        self._compare("hevc", sources=self.plain_inputs[:2], separator_points=[2.0],
                      video_codec="H265", video_encoder="libx265")

    def test_the_descriptor_reports_hevc_for_an_hevc_job(self):
        # Asserted on the descriptor, not on the commands: replacing the codec
        # with a hardcoded `h264` changes no emitted argument on any path
        # reachable here, so the command comparison cannot see it. (An earlier
        # measurement that said otherwise was an artefact -- two plans written
        # into one output directory got collision-suffixed part names.) It is
        # still worth holding: the descriptor is what later container and
        # encoder decisions read, and a confidently wrong codec is how those
        # go wrong quietly.
        planning = self._tmp / "hevcdesc"
        planning.mkdir(parents=True, exist_ok=True)
        answers = self._answers(planning, self.plain_inputs[:2],
                                video_codec="H265", video_encoder="libx265")
        seen = []
        real_stage = encoding.stage_answers
        encoding.stage_answers = (
            lambda a, owns: (seen.append(a) or real_stage(a, owns)))
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                encoding.bounded_reverse_plan(answers, planning / "ws")
        finally:
            encoding.stage_answers = real_stage
        described = [a for a in seen if str(a.get("input_path", "")).endswith(
            "joined_forward.mkv")]
        self.assertTrue(described, "the joined intermediate was never described")
        self.assertEqual(["hevc"], [str(stream.get("codec_name")) for stream
                                    in described[0]["video_streams"]])

    def test_a_plan_with_an_unknowable_duration_refuses(self):
        # Everything else stays valid so the DURATION guard is what fires; a
        # fixture broken in several ways would raise for another reason and
        # prove nothing about this one. The message is asserted for the same
        # reason.
        planning = self._tmp / "blind"
        planning.mkdir(parents=True, exist_ok=True)
        answers = self._answers(planning, self.inputs[:2])
        blind = [dict(item) for item in answers["join_input_items"]]
        for item in blind:
            item["format"] = {k: v for k, v in item["format"].items() if k != "duration"}
            item["video_streams"] = [{k: v for k, v in stream.items()
                                      if k not in {"duration", "nb_frames", "tags"}}
                                     for stream in item["video_streams"]]
            item["duration"] = 0.0
        answers["join_input_items"] = blind
        real_span = encoding.join_item_picture_span
        encoding.join_item_picture_span = lambda _item: 0.0
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                with self.assertRaises(ValueError) as caught:
                    encoding.bounded_reverse_plan(answers, planning / "ws")
        finally:
            encoding.join_item_picture_span = real_span
        self.assertIn("duration", str(caught.exception).lower())

    # ---- D08 -------------------------------------------------------------
    def test_a_failed_export_is_not_reported_as_not_staged(self):
        planning = self._tmp / "failexport"
        planning.mkdir(parents=True, exist_ok=True)
        answers = self._answers(planning, self.inputs[:2], separator_points=[2.0])
        real_plan = encoding.bounded_reverse_plan

        def explode(_answers, _workspace):
            raise RuntimeError("scratch directory is read-only")

        encoding.bounded_reverse_plan = explode
        try:
            export = encoding.export_bounded_reverse_plan(
                answers, Path(answers["output_path"]))
        finally:
            encoding.bounded_reverse_plan = real_plan
        self.assertFalse(export.succeeded)
        self.assertIn("read-only", export.error)

    def test_an_empty_stage_list_is_a_failure_not_a_silence(self):
        planning = self._tmp / "emptyexport"
        planning.mkdir(parents=True, exist_ok=True)
        answers = self._answers(planning, self.inputs[:2], separator_points=[2.0])
        real_plan = encoding.bounded_reverse_plan
        encoding.bounded_reverse_plan = lambda _a, _w: []
        try:
            export = encoding.export_bounded_reverse_plan(
                answers, Path(answers["output_path"]))
        finally:
            encoding.bounded_reverse_plan = real_plan
        self.assertFalse(export.succeeded)
        self.assertTrue(export.error, "an empty plan reported no reason")

    def test_the_user_is_not_told_the_reference_command_is_runnable(self):
        notes, errors = [], []
        real_note, real_error = FFmWiz.appio.note, FFmWiz.appio.error
        real_menu, real_wizard = FFmWiz.ask_main_menu, FFmWiz.run_wizard
        real_summary = FFmWiz.print_ffmpeg_processing_plan
        real_export = FFmWiz.export_bounded_reverse_plan
        out = self._tmp / "declined"
        out.mkdir(parents=True, exist_ok=True)
        prepared = dict(self._answers(out, self.inputs[:2], separator_points=[2.0]))
        prepared["start_now"] = False

        def fake_wizard(answers, config=None):
            answers.update(prepared)
            answers["cmd"] = ["ffmpeg", "-i", "a.mkv", "out.mkv"]
            answers["output_path"] = out / "result.mkv"

        FFmWiz.appio.note = notes.append
        FFmWiz.appio.error = errors.append
        FFmWiz.ask_main_menu = lambda answers, config_path: 1
        FFmWiz.run_wizard = fake_wizard
        FFmWiz.print_ffmpeg_processing_plan = lambda *a, **k: None
        FFmWiz.export_bounded_reverse_plan = (
            lambda _a, _d: encoding.PlanExport(None, "disk full"))
        noise = StringIO()
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                FFmWiz.run_one_job({}, out / "cfg.json")
        finally:
            FFmWiz.appio.note, FFmWiz.appio.error = real_note, real_error
            FFmWiz.ask_main_menu, FFmWiz.run_wizard = real_menu, real_wizard
            FFmWiz.print_ffmpeg_processing_plan = real_summary
            FFmWiz.export_bounded_reverse_plan = real_export
        said = " ".join(notes + errors).lower()
        self.assertIn("disk full", said, "the failure reason was not reported")
        self.assertNotIn("the command above is ready to run manually", said)
        self.assertIn("do not run it", said)


if __name__ == "__main__":
    unittest.main()
