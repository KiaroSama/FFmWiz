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
        self._real_segmented = encoding.run_segmented_reverse_main_encode
        self._real_runner = encoding.run_ffmpeg_with_progress
        self._real_note = FFmWiz.appio.note
        encoding.run_segmented_reverse_main_encode = (
            lambda answers: (self.segmented.append(answers) or (0, 0.0)))
        encoding.run_ffmpeg_with_progress = (
            lambda cmd, **kwargs: (self.one_shot.append(cmd) or (0, 0.0)))
        FFmWiz.appio.note = self.notes.append

    def tearDown(self):
        encoding.run_segmented_reverse_main_encode = self._real_segmented
        encoding.run_ffmpeg_with_progress = self._real_runner
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

    def test_a_joined_reverse_is_not_segmented(self):
        # Segmenting a join would reverse input 1 alone (R01) -- far worse.
        self._run(_answers(join_input_items=[{"path": "b.mkv"}]))
        self.assertEqual(0, len(self.segmented))
        self.assertEqual(1, len(self.one_shot))

    def test_a_joined_reverse_says_it_is_one_pass(self):
        self._run(_answers(join_input_items=[{"path": "b.mkv"}]))
        joined = " ".join(self.notes).lower()
        self.assertIn("one pass", joined,
                      "the memory characteristic must not be implied wrongly")

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

    def test_the_main_dispatcher_still_selects(self):
        source = (Path(FFmWiz.__file__).resolve().parent
                  / "FFmWiz.py").read_text(encoding="utf-8")
        self.assertIn("reverse_video_needs_segmented_main_encode", source)


if __name__ == "__main__":
    unittest.main()
