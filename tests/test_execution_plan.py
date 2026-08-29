"""Regression: one place decides how a reverse is executed (R09).

The project promises reverse is segmented so a long clip is not buffered whole,
but every caller decided for itself. Folder Encode never asked at all -- it
called `run_ffmpeg_with_progress` directly, so a reversed folder job ran the
full-buffer `reverse` filter while the UI still claimed the safe behaviour.

`execute_encode_plan()` is now the single decision point. A join deliberately
does NOT segment (the segment builder understands one input, and reversing input
1 alone is far worse than buffering -- R01), so that case says so out loud
instead of letting the UI imply a bounded plan it will not use.
"""
import unittest
from pathlib import Path

import FFmWiz

from ffmwiz import encoding
from ffmwiz import reverse_pipeline
from ffmwiz import runtime


def _answers(**extra):
    answers = {
        "video_streams": [{"codec_type": "video", "codec_name": "h264"}],
        "audio_streams": [],
        "output_ext": "mkv",
        "video_speed_enabled": True, "video_speed_factor": 1.0,
        "reverse_video": True,
    }
    answers.update(extra)
    return answers


class PlanSelection(unittest.TestCase):
    def setUp(self):
        self.segmented = []
        self.one_shot = []
        self.notes = []
        self.bounded_join = []
        self._real_segmented = reverse_pipeline.run_segmented_reverse_main_encode
        self._real_bounded = encoding.run_bounded_reverse_pipeline
        self._real_runner = runtime.run_ffmpeg_with_progress
        self._real_note = FFmWiz.appio.note
        reverse_pipeline.run_segmented_reverse_main_encode = (
            lambda answers: (self.segmented.append(answers) or (0, 0.0)))
        encoding.run_bounded_reverse_pipeline = (
            lambda answers: (self.bounded_join.append(answers) or (0, 0.0)))
        runtime.run_ffmpeg_with_progress = (
            lambda cmd, **kwargs: (self.one_shot.append(cmd) or (0, 0.0)))
        FFmWiz.appio.note = self.notes.append

    def tearDown(self):
        reverse_pipeline.run_segmented_reverse_main_encode = self._real_segmented
        encoding.run_bounded_reverse_pipeline = self._real_bounded
        runtime.run_ffmpeg_with_progress = self._real_runner
        FFmWiz.appio.note = self._real_note

    def _run(self, answers):
        return encoding.execute_encode_plan(
            answers, ["ffmpeg", "-i", "in.mkv", "out.mkv"],
            total_duration=10.0, label="test")

    def test_an_ordinary_reverse_uses_the_bounded_plan(self):
        self._run(_answers())
        self.assertEqual(1, len(self.segmented))
        self.assertEqual(0, len(self.one_shot))

    def test_a_non_reverse_job_runs_once(self):
        self._run(_answers(reverse_video=False, video_speed_enabled=False))
        self.assertEqual(0, len(self.segmented))
        self.assertEqual(1, len(self.one_shot))

    def test_a_split_reverse_is_left_to_the_split_graph(self):
        self._run(_answers(separator_points=[5.0]))
        self.assertEqual(0, len(self.segmented))

    def test_a_joined_reverse_never_reaches_the_single_input_segmenter(self):
        # Pointing the segmenter at a join reverses input 1 alone (R01). It is
        # used on the forward-joined INTERMEDIATE instead, from inside
        # run_bounded_reverse_pipeline.
        self._run(_answers(join_input_items=[{"path": "b.mkv"}]))
        self.assertEqual(0, len(self.segmented))

    def test_a_joined_reverse_takes_the_bounded_plan(self):
        # Was one pass with a memory warning. One pass across a join is 336 GiB
        # of decoded frames for an hour of 1080p30 -- not a caveat, a failure
        # (F10), so it joins forward first and then reverses in segments.
        self._run(_answers(join_input_items=[{"path": "b.mkv"}]))
        self.assertEqual(1, len(self.bounded_join))
        self.assertEqual(0, len(self.one_shot))

    def test_a_split_join_takes_the_pipeline_as_well(self):
        # Reversing each part separately returns them in the original order,
        # and reversing the whole join at once is the unbounded plan. The
        # pipeline joins forward, reverses in segments, then splits.
        self._run(_answers(join_input_items=[{"path": "b.mkv"}],
                           separator_points=[5.0]))
        self.assertEqual(1, len(self.bounded_join))
        self.assertEqual(0, len(self.one_shot))

    def test_a_plain_split_reverse_takes_it_too(self):
        # No join at all: a Split still hands `reverse` the whole timeline,
        # about 56 GiB for ten minutes of 1080p30.
        self._run(_answers(separator_points=[5.0]))
        self.assertEqual(1, len(self.bounded_join))
        self.assertEqual(0, len(self.one_shot))

    def test_an_ordinary_reverse_does_not_emit_that_warning(self):
        self._run(_answers())
        self.assertNotIn("one pass", " ".join(self.notes).lower())

    def test_the_command_reaches_the_segmented_executor(self):
        answers = _answers()
        self._run(answers)
        self.assertEqual(["ffmpeg", "-i", "in.mkv", "out.mkv"], answers["cmd"])


class EveryExecutorGoesThroughIt(unittest.TestCase):
    """A caller that runs ffmpeg for a main encode must not decide alone."""

    def test_folder_encode_uses_the_shared_selector(self):
        source = (Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "modes.py").read_text(encoding="utf-8")
        block = source.split("Folder Encode starting")[1].split("if return_code == 0")[0]
        self.assertIn("execute_encode_plan", block,
                      "Folder Encode must ask the shared selector, not the runner")

    def test_the_main_dispatcher_calls_the_shared_selector(self):
        # Was a source-substring search, which passed while `run_one_job`
        # carried its own COPY of the selection and reached the runner
        # directly -- so the selector's memory notice never fired on the
        # primary path (F10/F13). Drive the dispatcher instead.
        seen = []
        real_plan = FFmWiz.execute_encode_plan
        real_menu = FFmWiz.ask_main_menu
        real_wizard = FFmWiz.run_wizard
        real_print = FFmWiz.print_ffmpeg_processing_plan

        def spy(answers, cmd, **kwargs):
            seen.append(cmd)
            return 0, 0.0

        FFmWiz.execute_encode_plan = spy
        FFmWiz.ask_main_menu = lambda answers, config_path: 1
        FFmWiz.print_ffmpeg_processing_plan = lambda *a, **k: None
        FFmWiz.run_wizard = lambda answers, config=None: answers.update({
            "cmd": ["ffmpeg", "-i", "in.mkv", "out.mkv"],
            "start_now": True, "output_path": Path("out.mkv"),
        })
        try:
            FFmWiz.run_one_job({}, Path("cfg.json"))
        finally:
            FFmWiz.execute_encode_plan = real_plan
            FFmWiz.ask_main_menu = real_menu
            FFmWiz.run_wizard = real_wizard
            FFmWiz.print_ffmpeg_processing_plan = real_print
        self.assertEqual(1, len(seen),
                         "run_one_job must route through execute_encode_plan")

    def test_the_dispatcher_hands_over_the_per_part_progress_data(self):
        # The selector is only useful if it still receives what the runner
        # needs; a bare call would silently kill Split progress.
        captured = {}
        real_plan = FFmWiz.execute_encode_plan
        real_menu = FFmWiz.ask_main_menu
        real_wizard = FFmWiz.run_wizard
        real_print = FFmWiz.print_ffmpeg_processing_plan

        def spy(answers, cmd, **kwargs):
            captured.update(kwargs)
            return 0, 0.0

        FFmWiz.execute_encode_plan = spy
        FFmWiz.ask_main_menu = lambda answers, config_path: 1
        FFmWiz.print_ffmpeg_processing_plan = lambda *a, **k: None
        FFmWiz.run_wizard = lambda answers, config=None: answers.update({
            "cmd": ["ffmpeg", "-i", "in.mkv", "out.mkv"],
            "start_now": True, "output_path": Path("out.mkv"),
        })
        try:
            FFmWiz.run_one_job({}, Path("cfg.json"))
        finally:
            FFmWiz.execute_encode_plan = real_plan
            FFmWiz.ask_main_menu = real_menu
            FFmWiz.run_wizard = real_wizard
            FFmWiz.print_ffmpeg_processing_plan = real_print
        for key in ("label", "total_duration", "progress_output_paths"):
            self.assertIn(key, captured, f"the selector lost {key}")


if __name__ == "__main__":
    unittest.main()
