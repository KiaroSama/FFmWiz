"""Characterizes `ffmwiz.modes_b.run_hardsub_encode_mode` (main-menu mode 9,
Hard Sub Encode), whose driver had no test anywhere under `tests/` before this
file -- `build_hardsub_command` itself appears in 7 test modules, but it has
exactly one call site in the whole package
(`ffmwiz/wizard_flow_b.py:394`, inside `step_hardsub_start_now`), and nothing
drove the 14-step table that fills the answers it receives there.

HardSub burns subtitles into the picture: a wrong argv here is a full
re-encode the user has to notice and redo, which is why this mode -- not one
of the six deferred in `test_mode_driver_join.py`'s docstring -- gets a
driver suite.

`_run_hardsub_encode_mode_impl` (`ffmwiz/modes_b.py:203-253`) is a
hand-written duplicate of the shared `wizard_base.run_mode_steps` loop
(`ffmwiz/wizard_base.py:518-540`), and it is missing one piece that the
shared helper has: it calls `steps[idx].run(answers)` unconditionally for
every step (`ffmwiz/modes_b.py:230`), and never calls `steps[idx].applicable`
at all -- confirmed by direct read and by an instrumented run where a dummy
replacement for the nvenc_multipass step's `run` fired even with its
`applicable` callable (`nvenc_multipass_prompt_applicable`) stubbed to
return False. `wizard_base.run_mode_steps` checks `.applicable` before
calling `.run`, and again during the back-skip walk; this loop checks it in
neither place. Nothing in this file exercises that gap directly (see
`test_the_nvenc_step_is_skipped_when_it_does_not_apply` below for why), but it
is recorded here because a future edit to this loop that starts consulting
`.applicable` is a behavior change worth noticing, not a silent bugfix.

In production this is invisible: the two Step entries with a real
`applicable` callable (`nvenc_multipass`, and `color_range` via
`color_range_prompt_applicable`) both point at REAL step functions
(`ask_nvenc_multipass_if_applicable`, `step_color_range`) that separately
self-gate on their OWN internal checks
(`nvenc_multipass_applicable_for_encoder`, `source_color_range_known`) before
prompting -- so a user never sees an out-of-place question. The only
observable cost is that `_question_number` is not adjusted for a silently
skipped step the way `run_mode_steps`'s `visible_question_number` does it, a
cosmetic-only difference. Because of this, the tests below leave
`nvenc_multipass` and `color_range` as their REAL production step functions
(never replaced by a filler) and arrange answers so both self-gate silently
-- a CPU video codec, and a source with a known color range -- which
characterizes what a real user actually experiences, rather than pinning the
unused `.applicable` field.
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz
from ffmwiz import modes_b, wizard_build_b

from artifact_guard import NoLeakedArtifacts


class ModeDriverHardsubTests(NoLeakedArtifacts, unittest.TestCase):
    """Drives the real `run_hardsub_encode_mode`. The 11 always-applicable
    steps are replaced by fillers that record their name and write the
    answers that step is responsible for; `nvenc_multipass`, `color_range`
    and `step_hardsub_start_now` (the table's other 3 entries) are left real,
    so `build_hardsub_command` is exercised through its only call site and
    spied on there. None of this needs ffmpeg/ffprobe on PATH."""

    def setUp(self):
        super().setUp()
        FFmWiz.appio.USE_COLOR = False
        self._tmpdir = tempfile.TemporaryDirectory(prefix="ffmwiz_mode_driver_hardsub_")
        self.tmp = Path(self._tmpdir.name)
        self.src = self.tmp / "input.mkv"
        self.src.write_bytes(b"")
        self.sub = self.tmp / "subtitle.srt"
        self.sub.write_text("1\n00:00:00,000 --> 00:00:01,000\nText\n", encoding="utf-8")

    def tearDown(self):
        self._tmpdir.cleanup()
        super().tearDown()

    # ------------------------------------------------------------- fixtures
    def _standard_fillers(self, visited: list, *, subtitle_source="external",
                           subtitle_index=0, video_codec="H265", use_gpu=False,
                           back_step=None, back_armed=None):
        """One filler per always-applicable Step entry (11 of the table's 14 --
        everything except nvenc_multipass, color_range and start_now; see the
        module docstring for why those three stay real). `back_step`, when
        given the name of one of these 11, raises FFmWiz.Back() from that
        step's filler the FIRST time only (armed via the one-item list
        `back_armed`); every later call (including the retry after Back)
        behaves normally."""
        subtitle_updates = (
            {"hardsub_subtitle_source": "internal", "hardsub_subtitle_index": subtitle_index}
            if subtitle_source == "internal"
            else {"hardsub_subtitle_source": "external", "hardsub_subtitle_path": self.sub}
        )
        # name -> ("visited" label, answers it writes). Order matches the Step
        # table (ffmwiz/modes_b.py:205-224) minus nvenc_multipass, color_range
        # and start_now (see the module/class docstrings for why).
        per_step_updates = {
            "ask_hardsub_source_video": ("input_path", {
                "input_path": self.src,
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                    "width": 1920, "height": 1080, "color_range": "tv"}],
                "audio_streams": [{"codec_type": "audio", "codec_name": "aac"}],
                "format": {"duration": "10"},
            }),
            "step_hardsub_output_location": ("output_location", {"output_location": self.tmp}),
            "step_hardsub_output_format": ("output_format", {"output_ext": "mp4"}),
            "step_hardsub_subtitle_source": ("hardsub_subtitle", subtitle_updates),
            "step_hardsub_fontsdir": ("hardsub_fontsdir", {}),
            "step_hardsub_video_codec": ("video_codec", {"video_codec": video_codec}),
            "step_hardsub_use_gpu": ("use_gpu", {"use_gpu": use_gpu}),
            "step_hardsub_quality": ("hardsub_quality", {"hardsub_quality_mode": "near-lossless"}),
            "step_hardsub_hdr_handling": ("hardsub_hdr", {"hardsub_hdr_handling": "standard"}),
            "step_hardsub_audio_mode": ("hardsub_audio", {"hardsub_audio_mode": "copy-all"}),
            "step_hardsub_audio_container_policy": ("hardsub_audio_container", {"hardsub_audio_container_policy": "copy-anyway"}),
        }

        def make(step_name, label, updates):
            def f(a):
                if back_step == step_name and back_armed is not None and back_armed[0]:
                    back_armed[0] = False
                    visited.append(label + " (back)")
                    raise FFmWiz.Back()
                visited.append(label)
                a.update(updates)
            return f

        return {name: make(name, label, updates) for name, (label, updates) in per_step_updates.items()}

    def _drive(self, fillers: dict, *, start_now=True, ask_yes_no_seq=None):
        captured = {}
        real_build = wizard_build_b.build_hardsub_command

        def spy_build(answers):
            self.own(answers)
            cmd = real_build(answers)
            captured["answers"] = dict(answers)
            captured["cmd"] = cmd
            return cmd

        ffmpeg_calls = []

        def fake_run_ffmpeg(cmd, **kwargs):
            ffmpeg_calls.append((cmd, kwargs))
            return (0, 0.0)

        if ask_yes_no_seq is not None:
            yn_iter = iter(ask_yes_no_seq)
            ask_yes_no_fn = lambda *a, **k: next(yn_iter)
        else:
            ask_yes_no_fn = lambda *a, **k: start_now

        buffer = io.StringIO()
        with contextlib.ExitStack() as stack:
            for name, fn in fillers.items():
                stack.enter_context(mock.patch.object(modes_b, name, fn))
            stack.enter_context(mock.patch.object(wizard_build_b, "build_hardsub_command", spy_build))
            stack.enter_context(mock.patch.object(modes_b, "run_ffmpeg_with_progress", fake_run_ffmpeg))
            stack.enter_context(mock.patch.object(FFmWiz.appio, "ask_yes_no", ask_yes_no_fn))
            stack.enter_context(contextlib.redirect_stdout(buffer))
            result = modes_b.run_hardsub_encode_mode({"ffmpeg": "ffmpeg"})
        captured["result"] = result
        captured["ffmpeg_calls"] = ffmpeg_calls
        captured["output"] = buffer.getvalue()
        # The cmd that actually reaches the executor -- NOT necessarily the
        # same object as captured["cmd"] (build_hardsub_command's own return
        # value). step_hardsub_start_now assigns answers["cmd"] = cmd itself
        # AFTER the builder returns; a test that only inspected the spy's
        # captured return value would stay green even if that assignment were
        # broken (mutation-tested: it did -- see the plan's Step 5).
        captured["final_cmd"] = ffmpeg_calls[0][0] if ffmpeg_calls else None
        return captured

    # ---------------------------------------------------------------- tests
    def test_the_driver_visits_every_applicable_step_in_order(self):
        # This is what would have caught a question asked but never wired: if
        # a filler is skipped or run out of order, this list diverges.
        visited = []
        fillers = self._standard_fillers(visited)
        captured = self._drive(fillers)
        self.assertEqual([
            "input_path", "output_location", "output_format", "hardsub_subtitle",
            "hardsub_fontsdir", "video_codec", "use_gpu", "hardsub_quality",
            "hardsub_hdr", "hardsub_audio", "hardsub_audio_container",
        ], visited)
        self.assertEqual((0, 0.0), captured["result"])

    def test_the_subtitle_answers_reach_the_builder(self):
        # Internal-subtitle case: build_hardsub_video_filter emits `si=<index>`
        # (ffmwiz/support/L01_filters.py:303-306) for the answers the fillers
        # wrote.
        visited = []
        fillers = self._standard_fillers(visited, subtitle_source="internal", subtitle_index=0)
        captured = self._drive(fillers)
        # Asserts on final_cmd (what reaches run_ffmpeg_with_progress), not on
        # the builder's raw return value -- see the note in _drive.
        cmd = captured["final_cmd"]
        self.assertIn("-filter:v", cmd)
        video_filter = cmd[cmd.index("-filter:v") + 1]
        self.assertIn("subtitles=filename=", video_filter)
        self.assertIn(":si=0", video_filter)

    def test_an_external_subtitle_file_reaches_the_builder(self):
        # The other branch of step_hardsub_subtitle_source: an external file
        # path, not an internal stream index.
        visited = []
        fillers = self._standard_fillers(visited, subtitle_source="external")
        captured = self._drive(fillers)
        cmd = captured["final_cmd"]
        video_filter = cmd[cmd.index("-filter:v") + 1]
        self.assertIn("subtitles=filename=", video_filter)
        self.assertNotIn(":si=", video_filter)
        self.assertIn(self.sub.name, video_filter)

    def test_a_back_from_a_middle_step_re_asks_the_previous_one(self):
        # The loop under test (ffmwiz/modes_b.py:226-237) is a hand-written
        # copy of wizard_base.run_mode_steps -- two copies of a Back loop,
        # only one of which had ever been executed by a test.
        visited = []
        back_armed = [True]
        fillers = self._standard_fillers(visited, back_step="step_hardsub_hdr_handling", back_armed=back_armed)
        captured = self._drive(fillers)
        self.assertEqual([
            "input_path", "output_location", "output_format", "hardsub_subtitle",
            "hardsub_fontsdir", "video_codec", "use_gpu",
            "hardsub_quality",       # first visit
            "hardsub_hdr (back)",    # backs out on its first call
            "hardsub_quality",       # the PREVIOUS step's filler runs a second time
            "hardsub_hdr",           # answered for real this time
            "hardsub_audio", "hardsub_audio_container",
        ], visited)
        self.assertEqual((0, 0.0), captured["result"])

    def test_back_from_the_first_step_leaves_the_mode(self):
        # ffmwiz/modes_b.py:234 re-raises when idx==0; the wrapper at
        # ffmwiz/modes_b.py:195-200 catches it and returns None.
        visited = []
        back_armed = [True]
        fillers = self._standard_fillers(visited, back_step="ask_hardsub_source_video", back_armed=back_armed)
        captured = self._drive(fillers)
        self.assertIsNone(captured["result"])
        self.assertEqual(["input_path (back)"], visited)
        self.assertEqual([], captured["ffmpeg_calls"])

    def test_declining_start_now_runs_nothing(self):
        # ffmwiz/modes_b.py:239-241: answers["start_now"] = False (set by the
        # REAL step_hardsub_start_now, which asks "Start FFmpeg now?" after it
        # has already built and printed the command) returns None with no
        # run_ffmpeg_with_progress call.
        visited = []
        fillers = self._standard_fillers(visited)
        captured = self._drive(fillers, start_now=False)
        self.assertIsNone(captured["result"])
        self.assertEqual([], captured["ffmpeg_calls"])
        self.assertIn("cmd", captured, "the command must still be built and printed before the question")

    def test_the_nvenc_step_is_skipped_when_it_does_not_apply(self):
        # Characterizes the OBSERVABLE outcome (see the module docstring for
        # why this does not stub nvenc_multipass_prompt_applicable directly):
        # for a CPU video codec, the REAL ask_nvenc_multipass_if_applicable
        # self-gates via nvenc_multipass_applicable_for_encoder and returns
        # "disabled" without ever prompting or recording a chosen mode.
        visited = []
        fillers = self._standard_fillers(visited, video_codec="H265", use_gpu=False)
        captured = self._drive(fillers)
        self.assertNotIn("nvenc_multipass", captured["answers"],
                          "no prompt was shown, so no mode should have been recorded")
        self.assertEqual("CPU encoder selected", captured["answers"].get("nvenc_multipass_skip_reason"))


if __name__ == "__main__":
    unittest.main()
