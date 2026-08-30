"""Characterizes `ffmwiz.trackmanager.run_track_manager_mode` (main-menu mode
15, Track Manager), whose driver had no test anywhere under `tests/` before
this file.

This is the assertion that matters most in the whole plan this file is part
of: Track Manager is the only one of the fifteen main-menu modes that writes
its output NEXT TO THE SOURCE, inside the user's own media folder
(`track_manager_output_path`, `ffmwiz/support/L02.py:708-711` --
`<stem>_TrackEdit<ext>`), rather than into a chosen output folder. A wrong
path there overwrites or clutters a library; every other mode's blast radius
is a folder the user picked for the run.

`_run_track_manager_single` (`ffmwiz/trackmanager.py:575-670`) is a six-stage
hand-rolled state machine -- its own comment names five stages
(source/remove/externals/loudnorm/confirm); `metadata` is a sixth, between
`loudnorm` and `confirm` (line 616) -- with no test having exercised any of
its Back handling before this file.
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz
from ffmwiz import trackmanager


class ModeDriverTrackManagerTests(unittest.TestCase):
    """Drives the real `run_track_manager_mode` (single-file scope). Stubs
    only the prompts (`ask_track_manager_source`, `ask_track_remove_specs`,
    `_track_manager_collect_externals`, `_track_manager_ask_loudnorm`,
    `appio.ask_raw`/`ask_yes_no`), the builder (`build_track_manager_command`)
    and the executor (`run_ffmpeg_with_progress`). None of this needs
    ffmpeg/ffprobe on PATH."""

    VIDEO_STREAM = {"index": 0, "codec_type": "video", "codec_name": "h264",
                     "width": 1920, "height": 1080, "avg_frame_rate": "30/1",
                     "pix_fmt": "yuv420p", "color_range": "tv"}
    AUDIO_STREAM = {"index": 1, "codec_type": "audio", "codec_name": "aac",
                     "sample_rate": "48000", "channels": 2, "channel_layout": "stereo"}

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._tmpdir = tempfile.TemporaryDirectory(prefix="ffmwiz_mode_driver_trackmanager_")
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    # ------------------------------------------------------------- fixtures
    def _make_source(self, name: str = "input.mkv") -> Path:
        path = self.tmp / name
        path.write_bytes(b"")
        return path

    # --------------------------------------------------------------- driver
    def _drive(self, *, source_path: Path, remove_specs=("1",), externals=(),
               loudnorm_enabled=False, keep_metadata=True, start_now=True,
               scope_replies=("1",), back_step=None):
        """Run the real single-file Track Manager dispatcher.

        `back_step` (one of "source", "remove", "externals", "loudnorm"),
        when given, raises FFmWiz.Back() from that stage's stub the FIRST
        time it is called only; every later call behaves normally.
        """
        captured = {"stage_calls": {"source": 0, "remove": 0, "externals": 0, "loudnorm": 0}}
        streams = [dict(self.VIDEO_STREAM), dict(self.AUDIO_STREAM)]
        back_armed = [back_step is not None]

        def maybe_back(name):
            if back_step == name and back_armed[0]:
                back_armed[0] = False
                raise FFmWiz.Back()

        def fake_source(answers):
            captured["stage_calls"]["source"] += 1
            maybe_back("source")
            answers["input_path"] = source_path
            answers["probe"] = {"streams": streams, "format": {"duration": "10.0", "format_name": "matroska"}}
            answers["format"] = answers["probe"]["format"]
            answers["video_streams"] = [self.VIDEO_STREAM]
            answers["audio_streams"] = [self.AUDIO_STREAM]
            answers["subtitle_streams"] = []

        def fake_remove_specs(answers, stream_count):
            captured["stage_calls"]["remove"] += 1
            maybe_back("remove")
            return list(remove_specs)

        def fake_externals(answers):
            captured["stage_calls"]["externals"] += 1
            maybe_back("externals")
            return list(externals)

        def fake_loudnorm(answers, *, sample_path=None):
            captured["stage_calls"]["loudnorm"] += 1
            maybe_back("loudnorm")
            answers["loudnorm_enabled"] = loudnorm_enabled
            answers["loudnorm_mode"] = "single_pass" if loudnorm_enabled else "off"

        def fake_build(ffmpeg, input_path, norm_specs, extra_items, output_path, answers):
            captured["build_args"] = (ffmpeg, input_path, norm_specs, extra_items, output_path)
            captured["build_answers"] = answers
            return ["ffmpeg", "-i", str(input_path), str(output_path)]

        ffmpeg_calls = []

        def fake_run_ffmpeg(cmd, **kwargs):
            ffmpeg_calls.append((cmd, kwargs))
            return (0, 0.0)

        scope_iter = iter(scope_replies)
        # The stage machine only ever asks two y/n questions on the path these
        # tests exercise: "keep metadata?" (the `metadata` stage, ALWAYS asked
        # before the "nothing to do"/subtitle-container checks, both of which
        # live inside the later `confirm` stage) and "Start FFmpeg now?"
        # (also in `confirm`, reached only when the earlier checks pass).
        yn_sequence = iter([keep_metadata, start_now])

        def fake_ask_yes_no(*a, **k):
            try:
                return next(yn_sequence)
            except StopIteration:
                return True

        buffer = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(trackmanager, "ask_track_manager_source", fake_source))
            stack.enter_context(mock.patch.object(trackmanager, "ask_track_remove_specs", fake_remove_specs))
            stack.enter_context(mock.patch.object(trackmanager, "_track_manager_collect_externals", fake_externals))
            stack.enter_context(mock.patch.object(trackmanager, "_track_manager_ask_loudnorm", fake_loudnorm))
            stack.enter_context(mock.patch.object(trackmanager, "build_track_manager_command", fake_build))
            stack.enter_context(mock.patch.object(trackmanager, "run_ffmpeg_with_progress", fake_run_ffmpeg))
            # Avoids a real ffprobe subprocess from print_source_info's own
            # services.get_packet_sizes(answers) call (see the equivalent note
            # in test_mode_driver_join.py -- same eager-evaluation trap).
            stack.enter_context(mock.patch.object(trackmanager.services, "get_packet_sizes", lambda a: {}))
            stack.enter_context(mock.patch.object(FFmWiz.appio, "ask_raw", lambda *a, **k: next(scope_iter)))
            stack.enter_context(mock.patch.object(FFmWiz.appio, "ask_yes_no", fake_ask_yes_no))
            stack.enter_context(contextlib.redirect_stdout(buffer))
            result = trackmanager.run_track_manager_mode({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})
        captured["result"] = result
        captured["ffmpeg_calls"] = ffmpeg_calls
        captured["output"] = buffer.getvalue()
        return captured

    # ---------------------------------------------------------------- tests
    def test_the_removal_specs_and_externals_reach_the_builder(self):
        source = self._make_source()
        external = {"path": self.tmp / "commentary.eac3", "subtitle_streams": []}
        captured = self._drive(source_path=source, remove_specs=("1",), externals=(external,))
        ffmpeg, input_path, norm_specs, extra_items, output_path = captured["build_args"]
        self.assertEqual("ffmpeg", ffmpeg)
        self.assertEqual(source, input_path)
        # normalize_track_remove_specs converts the absolute index "1" to the
        # first audio stream's typed spec "a:0" (ffmwiz/support/L00_misc_b.py).
        self.assertEqual(["a:0"], norm_specs)
        self.assertEqual([external], extra_items)

    def test_the_output_is_a_TrackEdit_sibling_of_the_source(self):
        # This is the assertion that matters most in the whole plan: this
        # mode is the only one that writes into the user's own media folder,
        # and a wrong path here overwrites or clutters a library.
        source = self._make_source("movie.mp4")
        captured = self._drive(source_path=source, remove_specs=("1",))
        _, _, _, _, output_path = captured["build_args"]
        self.assertEqual(source.parent, output_path.parent)
        self.assertEqual("movie_TrackEdit.mp4", output_path.name)

    def test_a_job_that_changes_nothing_builds_no_command(self):
        # ffmwiz/trackmanager.py:632-634: no removals, no externals, loudnorm
        # off -> notes "nothing to do" and returns None with no builder call.
        source = self._make_source()
        captured = self._drive(source_path=source, remove_specs=(), externals=(), loudnorm_enabled=False)
        self.assertIsNone(captured["result"])
        self.assertNotIn("build_args", captured)
        self.assertEqual([], captured["ffmpeg_calls"])

    def test_a_subtitle_the_container_cannot_carry_is_refused_before_the_builder(self):
        # ffmwiz/trackmanager.py:640-645: a PGS (bitmap) external subtitle
        # cannot be carried by an MP4 output -- refused after printing the
        # problems. The comment there says the point is to refuse "instead of
        # failing at header-write time and leaving a 0-byte file next to the
        # source."
        source = self._make_source("input.mp4")
        pgs_external = {"path": self.tmp / "commentary.sup",
                         "subtitle_streams": [{"codec_name": "hdmv_pgs_subtitle"}]}
        captured = self._drive(source_path=source, remove_specs=(), externals=(pgs_external,))
        self.assertIsNone(captured["result"])
        self.assertNotIn("build_args", captured)
        self.assertEqual([], captured["ffmpeg_calls"])

    def test_back_from_the_externals_stage_returns_to_the_remove_stage(self):
        # The stage machine (ffmwiz/trackmanager.py:575-630) is six stages of
        # hand-rolled Back handling with no test before this file.
        source = self._make_source()
        captured = self._drive(source_path=source, remove_specs=("1",), back_step="externals")
        self.assertEqual(2, captured["stage_calls"]["remove"], "ask_track_remove_specs must run twice")
        self.assertEqual(2, captured["stage_calls"]["externals"])
        self.assertEqual((0, 0.0), captured["result"])

    def test_the_metadata_answer_reaches_the_builder(self):
        source = self._make_source()
        captured = self._drive(source_path=source, remove_specs=("1",), keep_metadata=False)
        self.assertFalse(captured["build_answers"]["track_manager_keep_metadata"])

    def test_declining_start_now_runs_nothing(self):
        # ffmwiz/trackmanager.py:664-666.
        source = self._make_source()
        captured = self._drive(source_path=source, remove_specs=("1",), start_now=False)
        self.assertIsNone(captured["result"])
        self.assertEqual([], captured["ffmpeg_calls"])
        self.assertIn("build_args", captured, "the command must still be built and printed before the question")


if __name__ == "__main__":
    unittest.main()
