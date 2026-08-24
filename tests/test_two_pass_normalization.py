"""Regression: a retained CPU two-pass selection must not survive silently (F11).

The wizard hides the two-pass question for a join, a Split, cuts, speed and
reverse -- but a config file carries `cpu_two_pass=y` straight past that, and
nothing re-checked it once the job was known.

Two different wrong outcomes came from the same gap:

* A real two-input Join reached `build_cpu_two_pass_commands()`. Pass 1 had no
  `-filter_complex` and a hardcoded `-map 0:v:0`, pass 2 used the full joined
  graph, and the run died:

      PASS1_RC 0
      PASS2_RC 187
      Incomplete MB-tree stats file.
      Error submitting video frame to the encoder

* The segmented-reverse and per-part Split executors called the runner
  directly, so the same flag was quietly downgraded to a single pass while the
  settings summary still said "CPU two-pass: yes".

The contract chosen here is the explicit one the brief allows: reject the
combination once all settings are known, clear the effective state so no
summary can still claim it, and say why before the command is confirmed.
"""
import unittest

import FFmWiz


def _answers(**extra):
    answers = {
        "video_streams": [{"codec_type": "video", "codec_name": "h264"}],
        "audio_streams": [],
        "output_ext": "mkv",
        "video_codec": "H264", "use_gpu": False,
        "cpu_two_pass": True,
    }
    answers.update(extra)
    return answers


class UnsupportedCombinationsAreTurnedOff(unittest.TestCase):
    def setUp(self):
        self.notes = []
        self._real_note = FFmWiz.appio.note
        FFmWiz.appio.note = self.notes.append

    def tearDown(self):
        FFmWiz.appio.note = self._real_note

    def _normalize(self, **extra):
        answers = _answers(**extra)
        reason = FFmWiz.normalize_cpu_two_pass_selection(answers)
        return answers, reason

    def test_a_join_turns_it_off(self):
        answers, reason = self._normalize(join_input_items=[{"path": "b.mkv"}])
        self.assertFalse(answers["cpu_two_pass"])
        self.assertIn("filter_complex", reason)

    def test_a_split_turns_it_off(self):
        answers, _reason = self._normalize(separator_points=[5.0])
        self.assertFalse(answers["cpu_two_pass"])

    def test_reverse_turns_it_off(self):
        answers, reason = self._normalize(reverse_video=True)
        self.assertFalse(answers["cpu_two_pass"])
        self.assertIn("segment", reason)

    def test_a_cut_turns_it_off(self):
        answers, _reason = self._normalize(cut_keep_ranges=[(0.0, 2.0)])
        self.assertFalse(answers["cpu_two_pass"])

    def test_a_speed_change_turns_it_off(self):
        answers, _reason = self._normalize(video_speed_enabled=True,
                                           video_speed_factor=2.0)
        self.assertFalse(answers["cpu_two_pass"])

    def test_the_helper_itself_stays_silent(self):
        # It sits below appio, so it returns the reason and the caller that
        # owns the screen prints it. Announcing from here would be a layering
        # violation and would print twice.
        self._normalize(join_input_items=[{"path": "b.mkv"}])
        self.assertEqual([], self.notes)

    def test_the_effective_state_stops_claiming_it(self):
        # Assert the RESOLVED dictionary, not effective_value(): that falls
        # back to the requested key, which is also False here, so it would pass
        # even if nothing was recorded. Any summary reading the resolved map
        # directly would still print "CPU two-pass: yes".
        answers, _reason = self._normalize(reverse_video=True)
        resolved = FFmWiz.effective_settings(answers)
        self.assertIn("cpu_two_pass", resolved,
                      "the resolved map never learned it was turned off")
        self.assertIs(False, resolved["cpu_two_pass"])

    def test_an_ordinary_re_encode_keeps_it(self):
        answers, reason = self._normalize()
        self.assertTrue(answers["cpu_two_pass"])
        self.assertEqual("", reason)
        self.assertEqual([], self.notes, "a supported job must stay silent")

    def test_it_is_idempotent(self):
        answers = _answers(reverse_video=True)
        FFmWiz.normalize_cpu_two_pass_selection(answers)
        before = len(self.notes)
        FFmWiz.normalize_cpu_two_pass_selection(answers)
        self.assertEqual(before, len(self.notes),
                         "a second pass must not repeat the notice")

    def test_a_job_that_never_asked_is_untouched(self):
        answers = _answers(join_input_items=[{"path": "b.mkv"}])
        answers.pop("cpu_two_pass")
        self.assertEqual("", FFmWiz.normalize_cpu_two_pass_selection(answers))
        self.assertEqual([], self.notes)


class NoExecutorCanSkipTheCheck(unittest.TestCase):
    """The builder is not the only entry point -- joins skip it entirely."""

    def setUp(self):
        self._real_note = FFmWiz.appio.note
        self._real_runner = FFmWiz.encoding.run_ffmpeg_with_progress
        self._real_two_pass = FFmWiz.encoding.run_cpu_two_pass_ffmpeg
        self.two_pass_calls = []
        FFmWiz.appio.note = lambda *a, **k: None
        FFmWiz.encoding.run_ffmpeg_with_progress = lambda cmd, **kwargs: (0, 0.0)
        FFmWiz.encoding.run_cpu_two_pass_ffmpeg = (
            lambda cmd, answers, **kwargs: (self.two_pass_calls.append(cmd) or (0, 0.0)))

    def tearDown(self):
        FFmWiz.appio.note = self._real_note
        FFmWiz.encoding.run_ffmpeg_with_progress = self._real_runner
        FFmWiz.encoding.run_cpu_two_pass_ffmpeg = self._real_two_pass

    def _execute(self, **extra):
        answers = _answers(**extra)
        cmd = ["ffmpeg", "-i", "a.mkv", "-c:v", "libx264", "out.mkv"]
        FFmWiz.encoding.execute_encode_plan(
            answers, cmd, total_duration=1.0, label="test")
        return answers

    def test_a_joined_two_pass_never_reaches_the_two_pass_runner(self):
        answers = self._execute(join_input_items=[{"path": "b.mkv"}])
        self.assertEqual([], self.two_pass_calls,
                         "pass 1 cannot analyse a joined filter_complex")
        self.assertFalse(answers["cpu_two_pass"])

    def test_the_user_is_told_why_it_was_turned_off(self):
        notes = []
        real_note = FFmWiz.appio.note
        FFmWiz.appio.note = notes.append
        try:
            self._execute(join_input_items=[{"path": "b.mkv"}])
        finally:
            FFmWiz.appio.note = real_note
        joined = " ".join(notes).lower()
        self.assertIn("two-pass", joined)
        self.assertIn("turned off", joined)

    def test_a_supported_job_still_reaches_it(self):
        # Guard the guard: if nothing ever reached the two-pass runner the
        # assertion above would pass for the wrong reason.
        self._execute()
        self.assertEqual(1, len(self.two_pass_calls))


if __name__ == "__main__":
    unittest.main()
