"""Split from test_loudnorm_join_progress.py (see join_test_helpers.py)."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import FFmWiz
from join_test_helpers import video_stream, audio_stream


class TrackManagerAndOutputFormatTests(unittest.TestCase):
    """Covers the Track Manager removal-spec normalization and the stricter
    output-format handling (mkv default for mkv input, reject typos)."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    # ---- normalize_track_remove_specs ----
    def test_normalize_absolute_index_to_typed(self):
        # streams: #0 video, #1 audio -> absolute "1" should become "a:0".
        streams = [
            {"index": 0, "codec_type": "video"},
            {"index": 1, "codec_type": "audio"},
        ]
        self.assertEqual(FFmWiz.normalize_track_remove_specs(["1"], streams), ["a:0"])

    def test_normalize_multiple_streams(self):
        # #0 video, #1 audio, #2 audio, #3 subtitle.
        streams = [
            {"index": 0, "codec_type": "video"},
            {"index": 1, "codec_type": "audio"},
            {"index": 2, "codec_type": "audio"},
            {"index": 3, "codec_type": "subtitle"},
        ]
        self.assertEqual(
            FFmWiz.normalize_track_remove_specs(["2", "3"], streams),
            ["a:1", "s:0"],
        )

    def test_normalize_keeps_typed_specs_unchanged(self):
        streams = [
            {"index": 0, "codec_type": "video"},
            {"index": 1, "codec_type": "audio"},
        ]
        self.assertEqual(
            FFmWiz.normalize_track_remove_specs(["a:0", "s:1"], streams),
            ["a:0", "s:1"],
        )

    def test_normalize_no_streams_returns_input(self):
        self.assertEqual(FFmWiz.normalize_track_remove_specs(["1", "a:0"], []), ["1", "a:0"])

    # ---- step_output_format default ----
    def _run_output_format(self, input_name, has_video, typed):
        answers = {
            "input_path": Path(input_name),
            "video_streams": [video_stream()] if has_video else [],
            "_question_number": 1,
        }
        with mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=list(typed)):
            FFmWiz.wizard.step_output_format(answers)
        return answers

    def test_output_default_mp4_for_non_mkv_input(self):
        # Pressing Enter (empty) on an mp4 input keeps mp4.
        answers = self._run_output_format("video.mp4", True, [""])
        self.assertEqual(answers["output_ext"], "mp4")

    def test_output_default_mkv_for_mkv_input(self):
        # Pressing Enter (empty) on an mkv input defaults to mkv.
        answers = self._run_output_format("video.mkv", True, [""])
        self.assertEqual(answers["output_ext"], "mkv")

    def test_output_default_mp3_for_audio_input(self):
        answers = self._run_output_format("audio.wav", False, [""])
        self.assertEqual(answers["output_ext"], "mp3")

    def test_output_rejects_unknown_format_then_accepts(self):
        # "acc" is a typo for ac3 -> rejected and re-asked; then "mp4" accepted.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            answers = self._run_output_format("video.mp4", True, ["acc", "mp4"])
        self.assertEqual(answers["output_ext"], "mp4")
        out = buf.getvalue().lower()
        self.assertIn("not a supported output format", out)
        self.assertIn("ac3", out)  # close-match suggestion

    def test_output_keep_input_n_allows_unknown(self):
        # "n" = keep input format, even an unusual container extension.
        answers = self._run_output_format("clip.xyz", True, ["n"])
        self.assertTrue(answers["output_format_keep_input"])
        self.assertEqual(answers["output_ext"], "xyz")


class TrackManagerBackTests(unittest.TestCase):
    """Back ('0') in the single-file Track Manager goes ONE step back instead
    of cancelling the whole mode."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def _answers(self):
        return {
            "ffmpeg": "ffmpeg", "input_path": Path("x.mkv"),
            "probe": {"streams": [{"index": 0, "codec_type": "video"},
                                  {"index": 1, "codec_type": "audio"}]},
            "format": {"duration": "10"},
        }

    def test_back_at_loudnorm_returns_to_externals_not_cancel(self):
        loud_calls, ext_calls = [], []

        def fake_loud(a):
            loud_calls.append(1)
            if len(loud_calls) == 1:
                raise FFmWiz.Back()  # user pressed 0 at the loudnorm menu

        def fake_ext(a):
            ext_calls.append(1)
            return []

        answers = self._answers()
        with mock.patch.object(FFmWiz.trackmanager, "ask_track_manager_source", lambda a: None), \
             mock.patch.object(FFmWiz.trackmanager, "print_source_info", lambda a: None), \
             mock.patch.object(FFmWiz.trackmanager, "print_track_list", lambda a: None), \
             mock.patch.object(FFmWiz.trackmanager, "ask_track_remove_specs", lambda a, c: []), \
             mock.patch.object(FFmWiz.trackmanager, "_track_manager_collect_externals", side_effect=fake_ext), \
             mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
             mock.patch.object(FFmWiz.trackmanager, "_track_manager_ask_loudnorm", side_effect=fake_loud):
            with contextlib.redirect_stdout(io.StringIO()):
                result = FFmWiz._run_track_manager_single(answers)
        # Did NOT cancel the mode (no Back propagated out); instead re-ran the
        # previous (externals) step and then loudnorm again.
        self.assertIsNone(result)
        self.assertEqual(len(loud_calls), 2)
        self.assertEqual(len(ext_calls), 2)

    def test_back_at_externals_returns_to_remove(self):
        remove_calls, ext_calls = [], []

        def fake_remove(a, c):
            remove_calls.append(1)
            return []

        def fake_ext(a):
            ext_calls.append(1)
            if len(ext_calls) == 1:
                raise FFmWiz.Back()  # 0 at "Add a track?"
            return []

        answers = self._answers()
        with mock.patch.object(FFmWiz.trackmanager, "ask_track_manager_source", lambda a: None), \
             mock.patch.object(FFmWiz.trackmanager, "print_source_info", lambda a: None), \
             mock.patch.object(FFmWiz.trackmanager, "print_track_list", lambda a: None), \
             mock.patch.object(FFmWiz.trackmanager, "ask_track_remove_specs", side_effect=fake_remove), \
             mock.patch.object(FFmWiz.trackmanager, "_track_manager_collect_externals", side_effect=fake_ext), \
             mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
             mock.patch.object(FFmWiz.trackmanager, "_track_manager_ask_loudnorm", lambda a: None):
            with contextlib.redirect_stdout(io.StringIO()):
                result = FFmWiz._run_track_manager_single(answers)
        self.assertIsNone(result)
        self.assertEqual(len(remove_calls), 2)  # went back to remove
        self.assertEqual(len(ext_calls), 2)


class TrackManagerFolderResultTests(unittest.TestCase):
    """Folder scope must report the aggregate outcome, not the last file's."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def _run_folder(self, results):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.mkv").write_bytes(b"")
            (folder / "b.mkv").write_bytes(b"")
            answers = {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}
            buf = io.StringIO()
            with mock.patch.object(FFmWiz.appio, "ask_required", return_value=str(folder)),                  mock.patch.object(FFmWiz.appio, "ask_raw", return_value="a:1"),                  mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True),                  mock.patch.object(FFmWiz.trackmanager, "_track_manager_collect_externals",
                                   lambda a: []),                  mock.patch.object(FFmWiz.trackmanager, "_track_manager_ask_loudnorm",
                                   lambda a, sample_path=None: None),                  mock.patch.object(FFmWiz.trackmanager, "run_ffmpeg_with_progress",
                                   side_effect=list(results)):
                with contextlib.redirect_stdout(buf):
                    result = FFmWiz._run_track_manager_folder(answers)
            return result, buf.getvalue()

    def test_early_failure_is_not_masked_by_a_successful_last_file(self):
        (rc, elapsed), out = self._run_folder([(1, 1.0), (0, 2.0)])
        self.assertEqual(rc, 1)
        self.assertIn("1 failure", out)
        # Elapsed must time the whole run, not repeat the last file's value.
        self.assertLess(elapsed, 2.0)

    def test_all_success_returns_zero(self):
        (rc, _elapsed), _out = self._run_folder([(0, 1.0), (0, 2.0)])
        self.assertEqual(rc, 0)


class AudioTrackSelectionTests(unittest.TestCase):
    """Single audio track is auto-selected (with a note); multiple tracks are
    listed and chosen by the user."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_single_track_auto_selects_with_note(self):
        answers = {"audio_streams": [audio_stream()], "format": {"duration": "10"}}
        with mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=AssertionError("must not prompt")) as ask:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                FFmWiz.step_audio_track_for_tool(answers)
            ask.assert_not_called()
        self.assertEqual(answers["audio_index"], 0)
        self.assertIn("Only one audio track", buf.getvalue())

    def test_multi_track_selects_chosen_one_based(self):
        answers = {"audio_streams": [audio_stream(), audio_stream()], "format": {"duration": "10"}}
        with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="2"), \
             mock.patch.object(FFmWiz.services, "get_packet_sizes", return_value={}), \
             mock.patch.object(FFmWiz.services, "get_audio_volume_stats", return_value={}):
            with contextlib.redirect_stdout(io.StringIO()):
                FFmWiz.step_audio_track_for_tool(answers)
        self.assertEqual(answers["audio_index"], 1)  # "2" -> index 1


class AudioSampleRateTests(unittest.TestCase):
    """Output audio sample-rate selection and uniform join resampling."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_resolve_explicit_and_keep(self):
        self.assertEqual(FFmWiz.resolve_audio_sample_rate({"audio_sample_rate": 44100}), 44100)
        self.assertIsNone(FFmWiz.resolve_audio_sample_rate({"audio_sample_rate": None}))
        self.assertIsNone(FFmWiz.resolve_audio_sample_rate({}))

    def test_source_sample_rate_detection(self):
        answers = {"audio_streams": [audio_stream()], "audio_tracks": [0]}
        self.assertEqual(FFmWiz.source_audio_sample_rate(answers), 48000)

    def test_append_audio_encode_options_adds_ar(self):
        cmd = []
        FFmWiz.append_audio_encode_options(cmd, {"audio_codec": "aac", "audio_sample_rate": 44100, "output_ext": "mp4"}, True)
        self.assertIn("-ar", cmd)
        self.assertEqual(cmd[cmd.index("-ar") + 1], "44100")

    def test_append_audio_encode_options_omits_ar_when_keep(self):
        cmd = []
        FFmWiz.append_audio_encode_options(cmd, {"audio_codec": "aac", "output_ext": "mp4"}, True)
        self.assertNotIn("-ar", cmd)

    def test_sample_rate_warns_when_above_source(self):
        # A rate above the source rate must trigger the over-source confirmation;
        # declining re-prompts, a rate <= source is accepted without a warning.
        import builtins

        def drive(inputs, default_rate):
            queue = list(inputs)

            def fake_input(prompt=""):
                print(prompt, end="")
                return queue.pop(0)

            answers = {}
            buf = io.StringIO()
            with mock.patch.object(builtins, "input", fake_input), contextlib.redirect_stdout(buf):
                FFmWiz.ask_audio_sample_rate(answers, default_rate)
            return answers.get("audio_sample_rate"), buf.getvalue()

        # Above source, confirmed -> accepted with a warning shown.
        rate, out = drive(["96000", "y"], 48000)
        self.assertEqual(rate, 96000)
        self.assertIn("higher than source", out)
        # Above source, declined, then a lower rate -> stored lower, warned once.
        rate2, out2 = drive(["96000", "n", "44100"], 48000)
        self.assertEqual(rate2, 44100)
        self.assertEqual(out2.count("higher than source"), 1)
        # Equal to source -> no warning.
        rate3, out3 = drive(["48000"], 48000)
        self.assertEqual(rate3, 48000)
        self.assertNotIn("higher than source", out3)

    def test_join_target_rate_is_uniform_highest(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers_for_rate(tmp)
            with mock.patch.object(FFmWiz.services, "get_packet_sizes", return_value={}):
                target = FFmWiz.join_target_sample_rate(answers)
        self.assertEqual(target, 96000)  # highest among inputs (44100/48000/96000)

    def test_join_explicit_rate_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers_for_rate(tmp)
            answers["audio_sample_rate"] = 48000
            self.assertEqual(FFmWiz.join_target_sample_rate(answers), 48000)

    def test_join_prep_filter_uses_target_rate(self):
        self.assertIn("aresample=44100:", FFmWiz.join_audio_prep_filter(44100))

    def join_answers_for_rate(self, tmp):
        def vid_audio(sr):
            a = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": str(sr), "bit_rate": "128000"}
            return a
        v = video_stream()
        def item(name, sr):
            return {"path": Path(tmp) / name, "streams": [v, vid_audio(sr)], "video_streams": [v],
                    "audio_streams": [vid_audio(sr)], "format": {"duration": "5"}, "duration": 5.0}
        answers = {
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "input_path": Path(tmp) / "A.mov",
            "format": {"duration": "5"}, "video_streams": [v], "audio_streams": [vid_audio(48000)],
            "data_streams": [], "subtitle_streams": [], "attachment_streams": [], "duration": 5.0,
            "join_input_items": [item("B.mov", 44100), item("C.mov", 96000)],
        }
        return answers, None


if __name__ == "__main__":
    unittest.main()
