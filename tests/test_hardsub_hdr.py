"""Coverage for the hard-sub HDR / Dolby Vision prompt (USER-16-6).

`step_hardsub_hdr_handling` is the only thing standing between an HDR source and
a hard-sub re-encode that quietly clips it to SDR, and it had no test at all.
Two decisions live in it and neither is visible from the outside:

  * whether to ask at all -- an SDR source must go straight to `standard` with
    no prompt, because a question nobody can answer usefully is worse than none;
  * whether to warn -- README promises a Dolby Vision warning, and it is tied to
    the `dolby` flag, not to `hdr`. HDR10 alone survives a re-encode
    best-effort; DV dynamic metadata does not.

`test_hdr_dolby_detection.py` covers the detector that produces the flags. This
covers what the wizard does with them, so the two meet in the middle: the HDR
fixtures here are built by running the real `video_hdr_dolby_info` over probe
dicts, not by hand-writing the flags it would have set.

Assertions are on the answer the step records and on whether a warning/error was
emitted at all -- not on the wording, which is free to change.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

import FFmWiz

# Probe-dict shapes for the four source kinds, fed through the real detector.
SDR = {"color_transfer": "bt709", "color_primaries": "bt709"}
HDR10 = {"color_transfer": "smpte2084", "color_primaries": "bt2020"}
HLG = {"color_transfer": "arib-std-b67", "color_primaries": "bt2020"}
DOLBY = {"color_transfer": "smpte2084", "color_primaries": "bt2020",
         "side_data_list": [{"side_data_type": "DOVI configuration record"}]}
# Profile 5 carries no HDR10 colour tags, so `hdr` is false and only `dolby`
# fires -- the branch that proves the gate is an OR, not an AND.
DOLBY_ONLY = {"side_data_list": [{"side_data_type": "DOVI configuration record"}]}


class HardsubHdrPrompt(unittest.TestCase):
    def setUp(self):
        self._ask = FFmWiz.appio.ask_raw
        self._note = FFmWiz.appio.note
        self._error = FFmWiz.appio.error
        self._colour = FFmWiz.appio.USE_COLOR
        FFmWiz.appio.USE_COLOR = False
        # One ordered log for every I/O the step performs, so "the warning
        # comes before the question" is checkable and not just assumed.
        self.events: list[tuple[str, str]] = []
        FFmWiz.appio.note = lambda text: self.events.append(("note", text))
        FFmWiz.appio.error = lambda text: self.events.append(("error", text))

    def _of(self, kind):
        return [text for event, text in self.events if event == kind]

    @property
    def prompts(self):
        return self._of("ask")

    @property
    def notes(self):
        return self._of("note")

    @property
    def errors(self):
        return self._of("error")

    def tearDown(self):
        FFmWiz.appio.ask_raw = self._ask
        FFmWiz.appio.note = self._note
        FFmWiz.appio.error = self._error
        FFmWiz.appio.USE_COLOR = self._colour

    def _run(self, stream, *typed):
        """Drive the real step with the prompt scripted and stdout captured."""
        replies = iter(typed)

        def fake_ask(prompt, *args, **kwargs):
            self.events.append(("ask", prompt))
            try:
                return next(replies)
            except StopIteration:  # a loop that never exits would hang the suite
                raise AssertionError("step asked more times than the test scripted")

        FFmWiz.appio.ask_raw = fake_ask
        answers = self.answers = {"hardsub_hdr_info": FFmWiz.video_hdr_dolby_info(stream)}
        self.shown = io.StringIO()
        with redirect_stdout(self.shown):
            FFmWiz.step_hardsub_hdr_handling(answers)
        return answers

    # --- SDR: decided without asking ------------------------------------

    def test_an_sdr_source_is_standard_without_a_prompt(self):
        answers = self._run(SDR)
        self.assertEqual("standard", answers["hardsub_hdr_handling"])
        self.assertEqual([], self.prompts)
        self.assertEqual([], self.notes)
        self.assertEqual("", self.shown.getvalue())

    def test_a_missing_hdr_probe_is_treated_as_sdr(self):
        # Modes that never ran the detector must not crash or stall here.
        answers = {}
        with redirect_stdout(io.StringIO()):
            FFmWiz.step_hardsub_hdr_handling(answers)
        self.assertEqual("standard", answers["hardsub_hdr_handling"])
        self.assertEqual([], self.prompts)

    # --- HDR: asked, and defaulted to preserving --------------------------

    def test_an_hdr10_source_is_asked_and_defaults_to_preserve(self):
        answers = self._run(HDR10, "")
        self.assertEqual("preserve", answers["hardsub_hdr_handling"])
        self.assertEqual(1, len(self.prompts))

    def test_an_hlg_source_is_asked_and_defaults_to_preserve(self):
        answers = self._run(HLG, "")
        self.assertEqual("preserve", answers["hardsub_hdr_handling"])
        self.assertEqual(1, len(self.prompts))

    def test_hdr_without_dolby_is_not_warned_about(self):
        # The warning belongs to DV only; firing it for every HDR10 source
        # would train users to ignore the one case that really loses data.
        for stream in (HDR10, HLG):
            with self.subTest(stream=stream["color_transfer"]):
                self.events.clear()
                self._run(stream, "")
                self.assertEqual([], self.notes)

    def test_each_choice_maps_to_its_handling(self):
        for typed, expected in (("1", "preserve"), ("2", "tone-map"), ("3", "standard")):
            with self.subTest(typed=typed):
                self.assertEqual(expected,
                                 self._run(HDR10, typed)["hardsub_hdr_handling"])

    def test_an_unrecognised_answer_is_rejected_and_re_asked(self):
        answers = self._run(HDR10, "9", "2")
        self.assertEqual("tone-map", answers["hardsub_hdr_handling"])
        self.assertEqual(2, len(self.prompts))
        self.assertEqual(1, len(self.errors))

    def test_back_leaves_the_answer_unset(self):
        with self.assertRaises(FFmWiz.Back):
            self._run(HDR10, "0")
        # Recording a handling on the way out would outlive the step the user
        # just backed out of, and the re-asked question would come pre-answered.
        self.assertNotIn("hardsub_hdr_handling", self.answers)
        self.assertEqual([], self.errors)

    # --- Dolby Vision: warned, whether or not HDR10 tags are present -------

    def test_a_dolby_source_is_warned_about(self):
        answers = self._run(DOLBY, "")
        self.assertEqual("preserve", answers["hardsub_hdr_handling"])
        self.assertEqual(1, len(self.notes))
        self.assertIn("dolby vision", self.notes[0].lower())

    def test_dolby_without_hdr_tags_still_prompts_and_warns(self):
        info = FFmWiz.video_hdr_dolby_info(DOLBY_ONLY)
        self.assertFalse(info["hdr"])  # guards the premise of this case
        self.assertTrue(info["dolby"])

        answers = self._run(DOLBY_ONLY, "2")
        self.assertEqual("tone-map", answers["hardsub_hdr_handling"])
        self.assertEqual(1, len(self.prompts))
        self.assertEqual(1, len(self.notes))

    def test_the_dolby_warning_is_shown_before_the_choice(self):
        # Emitted after the question it qualifies, it would arrive too late to
        # inform the answer.
        self._run(DOLBY, "")
        self.assertEqual(["note", "ask"], [event for event, _ in self.events])


if __name__ == "__main__":
    unittest.main()
