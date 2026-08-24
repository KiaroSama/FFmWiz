"""R11: "keep source extras" must not promise video streams the command drops.

`can_map_additional_source_video_streams()` refuses Split, speed and
frame-accurate cuts because an extra video stream is copied on the SOURCE
timeline and cannot follow a rebuilt one. It used to refuse them with nothing
but a log line: the keep flag stayed true and the summary went on saying the
streams were kept. The outcome is now stated and confirmed BEFORE the command
is generated, and the resolved state follows the command.

Time-based data streams get the same treatment: they are still copied, but the
summary says outright that their timestamps describe the source timeline.
"""
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz
from command_gen_base import CommandGenBase

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")

# Ordinary video streams, NOT attached pictures: those have their own handling.
SECOND_VIDEO = {
    "index": 2,
    "codec_type": "video",
    "codec_name": "h264",
    # Same geometry as stream 0: the fixture's crop margins are sized for it,
    # and an extra stream is copied, never scaled.
    "width": 3440,
    "height": 1440,
    "avg_frame_rate": "25/1",
    "color_range": "tv",
}

# Every timeline edit that cannot carry a source-timeline video stream, with the
# words the user must see for it.
TIMELINE_EDITS = {
    "speed": ({"video_speed_enabled": True, "video_speed_factor": 2.0}, "speed/reverse"),
    "cuts": ({"cut_keep_ranges": [(1.0, 4.0)]}, "cuts"),
    "split": ({"separator_points": [2.0]}, "split"),
}


class _Prompt:
    """Stand in for the confirmation prompt and record what it was shown."""

    def __init__(self, answer=True):
        self.answer = answer
        self.asked = 0

    def __enter__(self):
        self._real_yes_no = FFmWiz.appio.ask_yes_no
        self._real_note = FFmWiz.appio.note
        self.notes = []
        FFmWiz.appio.ask_yes_no = self._ask
        FFmWiz.appio.note = self.notes.append
        return self

    def __exit__(self, *_exc):
        FFmWiz.appio.ask_yes_no = self._real_yes_no
        FFmWiz.appio.note = self._real_note
        return False

    def _ask(self, _prompt, _default=True):
        self.asked += 1
        return self.answer


class ExtraVideoStreams(CommandGenBase):
    def extras_answers(self, tmp, **extra):
        answers = self.base_answers(tmp)
        answers["output_ext"] = "mkv"
        answers["use_gpu"] = False
        answers["video_streams"] = answers["video_streams"] + [dict(SECOND_VIDEO)]
        answers["keep_source_metadata"] = True
        answers["keep_source_extra_video_streams"] = True
        answers.update(extra)
        return answers

    def _confirm(self, answers, answer=True):
        with _Prompt(answer) as prompt:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                FFmWiz.confirm_source_extra_stream_outcomes(answers)
        return buf.getvalue(), prompt

    # -- an untouched timeline still keeps them ----------------------------
    def test_an_untouched_timeline_keeps_both_streams_and_says_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.extras_answers(tmp)
            text = self.command_text(answers)
            summary = self._summary_text(answers)
            self.assertEqual(FFmWiz.source_extra_stream_outcome_notes(answers), [])
        self.assertIn("-map 0:v:0", text)
        self.assertIn("-map 0:v:1", text)
        self.assertIn("extra source video streams", summary)
        self.assertIn("keep 1", summary)

    def test_an_untouched_timeline_asks_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.extras_answers(tmp)
            printed, prompt = self._confirm(answers)
        self.assertEqual(prompt.asked, 0)
        self.assertEqual(printed, "")

    # -- speed / cuts / Split each drop them, and say so -------------------
    def test_every_timeline_edit_states_the_exact_drop(self):
        for name, (edit, wording) in TIMELINE_EDITS.items():
            with self.subTest(edit=name), tempfile.TemporaryDirectory() as tmp:
                answers = self.extras_answers(tmp, **edit)
                self.assertTrue(FFmWiz.additional_source_video_drop_reason(answers))
                notes = FFmWiz.source_extra_stream_outcome_notes(answers)
                self.assertEqual(len(notes), 1, notes)
                self.assertIn("Extra source video streams", notes[0])
                self.assertIn("REMOVED", notes[0])
                self.assertIn(wording, notes[0])

    def test_every_timeline_edit_shows_the_notice_and_takes_an_answer(self):
        for name, (edit, _wording) in TIMELINE_EDITS.items():
            with self.subTest(edit=name), tempfile.TemporaryDirectory() as tmp:
                answers = self.extras_answers(tmp, **edit)
                printed, prompt = self._confirm(answers)
                self.assertEqual(prompt.asked, 1)
                self.assertIn("REMOVED", printed)
                self.assertFalse(
                    FFmWiz.effective_value(answers, "keep_source_extra_video_streams"),
                    "the resolved state must follow the command, not the request")
                # The request the user made is preserved for Back/reopen.
                self.assertTrue(answers["keep_source_extra_video_streams"])

    def test_declining_the_outcome_goes_back_instead_of_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.extras_answers(tmp, video_speed_enabled=True, video_speed_factor=2.0)
            with self.assertRaises(FFmWiz.Back):
                self._confirm(answers, answer=False)

    def test_every_timeline_edit_drops_them_from_the_command(self):
        for name, (edit, _wording) in TIMELINE_EDITS.items():
            with self.subTest(edit=name), tempfile.TemporaryDirectory() as tmp:
                answers = self.extras_answers(tmp, **edit)
                text = self.command_text(answers)
                self.assertNotIn("-map 0:v:1", text)

    def test_the_summary_reports_the_drop_it_actually_performs(self):
        for name, (edit, _wording) in TIMELINE_EDITS.items():
            with self.subTest(edit=name), tempfile.TemporaryDirectory() as tmp:
                answers = self.extras_answers(tmp, **edit)
                summary = self._summary_text(answers)
                self.assertIn("extra source video streams", summary)
                self.assertIn("dropped", summary)
                self.assertNotIn("keep 1", summary)

    def test_the_wizard_asks_before_the_command_is_generated(self):
        # Captured from run_wizard itself: a confirmation that is not in the
        # step list confirms nothing, and it must sit before start_now.
        recorded = []
        real_step = FFmWiz.wizard.Step
        real_input = FFmWiz.wizard.step_input_path

        class _Stop(Exception):
            pass

        def recorder(name, applicable, run):
            step = real_step(name, applicable, run)
            recorded.append(step)
            return step

        def stop(_answers):
            raise _Stop()

        FFmWiz.wizard.Step = recorder
        FFmWiz.wizard.step_input_path = stop
        try:
            with self.assertRaises(_Stop):
                FFmWiz.run_wizard({})
        finally:
            FFmWiz.wizard.Step = real_step
            FFmWiz.wizard.step_input_path = real_input

        names = [step.name for step in recorded]
        self.assertIn("source_extra_outcomes", names)
        self.assertLess(names.index("source_extra_outcomes"), names.index("start_now"))
        step = recorded[names.index("source_extra_outcomes")]
        with tempfile.TemporaryDirectory() as tmp:
            edited = self.extras_answers(tmp, video_speed_enabled=True, video_speed_factor=2.0)
            self.assertTrue(step.applicable(edited))
            self.assertFalse(step.applicable(self.extras_answers(tmp)))

    def test_a_join_reports_the_drop_its_graph_performs(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.extras_answers(tmp, join_input_items=[{"path": Path("b.mkv")}])
            reason = FFmWiz.additional_source_video_drop_reason(answers)
        self.assertIn("join", reason)

    # -- time-based data streams -------------------------------------------
    def test_data_streams_disclose_that_their_timestamps_do_not_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.extras_answers(
                tmp, video_speed_enabled=True, video_speed_factor=2.0,
                data_streams=[{"codec_type": "data", "codec_name": "bin_data"}],
                keep_source_data_streams=True)
            answers["video_streams"] = answers["video_streams"][:1]
            notes = FFmWiz.source_extra_stream_outcome_notes(answers)
            summary = self._summary_text(answers)
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("Source data streams", notes[0])
        self.assertIn("timestamps", notes[0])
        self.assertIn("source data streams", summary)
        self.assertIn("timestamps still follow the source timeline", summary)

    def test_data_streams_say_nothing_when_the_timeline_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.extras_answers(
                tmp, data_streams=[{"codec_type": "data", "codec_name": "bin_data"}],
                keep_source_data_streams=True)
            answers["video_streams"] = answers["video_streams"][:1]
            self.assertEqual(FFmWiz.source_extra_stream_outcome_notes(answers), [])

    # -- the extras policy itself still wins -------------------------------
    def test_removing_extras_outright_needs_no_drop_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.extras_answers(
                tmp, keep_source_extra_video_streams=False,
                video_speed_enabled=True, video_speed_factor=2.0)
            self.assertEqual(FFmWiz.source_extra_stream_outcome_notes(answers), [])


@requires_ffmpeg
class RealTwoVideoStreamFile(unittest.TestCase):
    """A real file with two ordinary video streams, through the real builder."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_extravideo_"))
        self.source = self._make_source()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_source(self):
        path = self._tmp / "two_video_streams.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "color=c=red:s=160x120:d=4:r=25",
             "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=4:r=25",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
             "-map", "0:v", "-map", "1:v", "-map", "2:a",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", str(path)],
            check=True, timeout=300)
        return path

    def _probe(self, path):
        return json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120).stdout)

    def _answers(self, **extra):
        probe = self._probe(self.source)
        streams = probe["streams"]
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
            "input_path": self.source, "output_location": self._tmp,
            "output_ext": "mkv", "video_codec": "H264", "use_gpu": False,
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
            "streams": streams, "format": probe["format"], "probe": probe,
            "audio_tracks": [0], "subtitle_tracks": [],
            "audio_codec": "aac", "audio_bitrate_kbps": 128,
            "resolution": "n", "fps": None, "crop_enabled": False,
            "video_bitrate_kbps": 400, "color_range_choice": "tv",
            "keep_source_metadata": True, "keep_source_extra_video_streams": True,
        }
        answers.update(extra)
        return answers

    def test_the_source_really_has_two_ordinary_video_streams(self):
        answers = self._answers()
        self.assertEqual(len(answers["video_streams"]), 2)
        for stream in answers["video_streams"]:
            self.assertFalse((stream.get("disposition") or {}).get("attached_pic"))

    def test_an_untouched_encode_carries_both_streams_into_the_output(self):
        answers = self._answers()
        cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        self.assertEqual(result.returncode, 0, result.stderr[-600:])
        streams = self._probe(answers["output_path"])["streams"]
        self.assertEqual(len([s for s in streams if s["codec_type"] == "video"]), 2)

    def test_a_sped_up_encode_matches_the_notice_it_showed(self):
        answers = self._answers(video_speed_enabled=True, video_speed_factor=2.0,
                                audio_speed_from_video=True)
        notes = FFmWiz.source_extra_stream_outcome_notes(answers)
        self.assertEqual(len(notes), 1, notes)
        cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        self.assertEqual(result.returncode, 0, result.stderr[-600:])
        streams = self._probe(answers["output_path"])["streams"]
        video = [s for s in streams if s["codec_type"] == "video"]
        self.assertEqual(len(video), 1,
                         "the notice said one stream is removed; the output must agree")
        self.assertFalse(
            FFmWiz.effective_value(answers, "keep_source_extra_video_streams"),
            "the build must record the drop it performed")

    def test_a_cut_encode_matches_the_notice_it_showed(self):
        answers = self._answers(cut_keep_ranges=[(0.5, 2.5)])
        self.assertEqual(len(FFmWiz.source_extra_stream_outcome_notes(answers)), 1)
        cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        self.assertEqual(result.returncode, 0, result.stderr[-600:])
        streams = self._probe(answers["output_path"])["streams"]
        self.assertEqual(len([s for s in streams if s["codec_type"] == "video"]), 1)

    def test_a_split_encode_matches_the_notice_it_showed(self):
        answers = self._answers(separator_points=[2.0])
        self.assertEqual(len(FFmWiz.source_extra_stream_outcome_notes(answers)), 1)
        cmd = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        self.assertEqual(result.returncode, 0, result.stderr[-600:])
        parts = list(answers.get("split_output_paths") or [answers["output_path"]])
        self.assertGreaterEqual(len(parts), 2)
        for part in parts:
            streams = self._probe(part)["streams"]
            self.assertEqual(len([s for s in streams if s["codec_type"] == "video"]), 1)
        # The Split builder returns before the per-stream map, so the resolved
        # state is recorded by the summary the user is shown next -- which is
        # the surface that has to agree with the command.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            FFmWiz.print_summary(answers, cmd)
        self.assertIn("dropped", buf.getvalue())
        self.assertFalse(
            FFmWiz.effective_value(answers, "keep_source_extra_video_streams"),
            "the summary must record the drop the command performed")


if __name__ == "__main__":
    unittest.main()
