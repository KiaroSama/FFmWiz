"""Focused tests for the improved loudnorm modes, Join-mode audio workflow,
progress de-duplication, Join input summary, and audio-report suppression.

These complement test_command_generation.py and exercise the new behavior
described in the loudnorm/Join task: explicit Off/Single/Two-pass modes,
measurement over the COMPLETE joined audio (all inputs), the duplicate final
100% progress line fix, the Join audio-bitrate default, the Join metadata
default, and the Join input summary.
"""
import contextlib
import io
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import FFmWiz
import cache_test_utils


def video_stream(width=1920, height=1080, fps="30/1", pix_fmt="yuv420p"):
    return {
        "codec_type": "video",
        "codec_name": "h264",
        "width": width,
        "height": height,
        "avg_frame_rate": fps,
        "r_frame_rate": fps,
        "pix_fmt": pix_fmt,
        "color_range": "tv",
    }


def audio_stream(bit_rate="128000", channels=2):
    stream = {"codec_type": "audio", "codec_name": "aac", "channels": channels, "sample_rate": "48000"}
    if bit_rate is not None:
        stream["bit_rate"] = bit_rate
    return stream


def make_item(path, duration=10.0, audio_bitrate="128000", with_audio=True, fps="30/1"):
    vstreams = [video_stream(fps=fps)]
    astreams = [audio_stream(bit_rate=audio_bitrate)] if with_audio else []
    return {
        "path": Path(path),
        "probe": {},
        "format": {"duration": str(duration)},
        "streams": vstreams + astreams,
        "video_streams": vstreams,
        "audio_streams": astreams,
        "subtitle_streams": [],
        "attachment_streams": [],
        "data_streams": [],
        "duration": duration,
    }


class LoudnormJoinProgressTests(unittest.TestCase):
    def setUp(self):
        FFmWiz.USE_COLOR = False
        self._prev_cache_env = os.environ.get("FFMWIZ_CACHE_DIR")
        self._cache_run_id = uuid.uuid4().hex
        self._cache_dir = cache_test_utils.create_owned_temp_cache_dir(self._cache_run_id)
        os.environ["FFMWIZ_CACHE_DIR"] = self._cache_dir
        FFmWiz._CAPABILITY_SESSION_MEMO.clear()

    def tearDown(self):
        if self._prev_cache_env is None:
            os.environ.pop("FFMWIZ_CACHE_DIR", None)
        else:
            os.environ["FFMWIZ_CACHE_DIR"] = self._prev_cache_env
        FFmWiz._CAPABILITY_SESSION_MEMO.clear()
        cache_test_utils.safe_remove_owned_temp_dir(self._cache_dir, self._cache_run_id, tempfile.gettempdir())

    # ---- helpers ----
    def join_answers(self, tmp, *, audio=True, bitrates=("128000", "96000", "160000")):
        items = [
            make_item(Path(tmp) / "B.mov", duration=12.0, audio_bitrate=bitrates[1], with_audio=audio),
            make_item(Path(tmp) / "C.mov", duration=8.0, audio_bitrate=bitrates[2], with_audio=audio),
        ]
        answers = {
            "ffmpeg": "ffmpeg",
            "ffprobe": "ffprobe",
            "video_encoders": ["libx264", "libx265"],
            "input_path": Path(tmp) / "A.mov",
            "output_location": Path(tmp),
            "output_ext": "mp4",
            "video_streams": [video_stream()],
            "audio_streams": [audio_stream(bit_rate=bitrates[0])] if audio else [],
            "subtitle_streams": [],
            "data_streams": [],
            "attachment_streams": [],
            "audio_tracks": [0] if audio else [],
            "subtitle_tracks": [],
            "video_codec": "H264",
            "use_gpu": False,
            "crop_enabled": False,
            "audio_codec": "aac",
            "audio_bitrate_kbps": 128,
            "resolution": "n",
            "fps": 30,
            "format": {"duration": "10.0"},
            "keep_source_metadata": False,
            "keep_source_chapters": False,
            "keep_source_subtitles": False,
            "keep_source_data_streams": False,
            "keep_source_extra_video_streams": False,
            "join_input_items": items,
        }
        return answers, [
            make_item(answers["input_path"], duration=10.0, audio_bitrate=bitrates[0], with_audio=audio),
            *items,
        ]

    # ================= JSON parser tests =================
    def test_parser_valid_string_numbers(self):
        text = '{"input_i":"-23.40","input_tp":"-5.10","input_lra":"4.20","input_thresh":"-33.90","target_offset":"-0.30"}'
        self.assertEqual(
            FFmWiz.parse_loudnorm_measurement_output(text),
            {"input_i": -23.4, "input_tp": -5.1, "input_lra": 4.2, "input_thresh": -33.9, "target_offset": -0.3},
        )

    def test_parser_json_surrounded_by_logs(self):
        text = (
            "frame= 100 fps=50 ...\n[Parsed_loudnorm_0 @ 0x1] \n"
            '{\n  "input_i" : "-18.00",\n  "input_tp" : "-2.00",\n  "input_lra" : "5.00",\n'
            '  "input_thresh" : "-28.00",\n  "target_offset" : "-0.10"\n}\n'
            "[out#0] video:0kB audio:1kB ...\n"
        )
        result = FFmWiz.parse_loudnorm_measurement_output(text)
        self.assertEqual(result["input_i"], -18.0)
        self.assertEqual(result["target_offset"], -0.1)

    def test_parser_rejects_missing_fields(self):
        text = '{"input_i":"-18.0","input_tp":"-2.0"}'
        self.assertIsNone(FFmWiz.parse_loudnorm_measurement_output(text))

    def test_parser_rejects_invalid_json(self):
        self.assertIsNone(FFmWiz.parse_loudnorm_measurement_output("not json at all { oops"))

    def test_parser_rejects_nonnumeric_values(self):
        text = '{"input_i":"abc","input_tp":"-2.0","input_lra":"5.0","input_thresh":"-28.0","target_offset":"-0.1"}'
        self.assertIsNone(FFmWiz.parse_loudnorm_measurement_output(text))

    def test_parser_uses_last_valid_object(self):
        first = '{"input_i":"-30.0","input_tp":"-9.0","input_lra":"1.0","input_thresh":"-40.0","target_offset":"-9.0"}'
        last = '{"input_i":"-16.0","input_tp":"-2.0","input_lra":"6.0","input_thresh":"-26.0","target_offset":"-0.2"}'
        result = FFmWiz.parse_loudnorm_measurement_output(first + "\n...logs...\n" + last)
        self.assertEqual(result["input_i"], -16.0)

    def test_parser_preserves_precision(self):
        text = '{"input_i":"-23.45","input_tp":"-1.55","input_lra":"7.77","input_thresh":"-33.33","target_offset":"-0.12"}'
        result = FFmWiz.parse_loudnorm_measurement_output(text)
        self.assertAlmostEqual(result["input_tp"], -1.55, places=2)
        self.assertAlmostEqual(result["input_thresh"], -33.33, places=2)

    # ================= Single-pass command tests =================
    def test_single_pass_filter_in_audio_chain(self):
        answers = {"loudnorm_enabled": True, "loudnorm_target_i": -16.0, "loudnorm_mode": "single"}
        chain = FFmWiz.build_encode_audio_processing_filter(answers)
        self.assertIn("loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary", chain)
        self.assertNotIn("measured_I", chain)

    def test_single_pass_join_after_concat_not_first_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self.join_answers(tmp)
            answers["loudnorm_enabled"] = True
            answers["loudnorm_target_i"] = -16.0
            answers["loudnorm_mode"] = "single"
            cmd = FFmWiz.build_join_encode_command(answers, items, Path(tmp) / "out.mp4")
            fc = next(cmd[i + 1] for i, a in enumerate(cmd) if a == "-filter_complex")
            self.assertIn("concat=n=3:v=1:a=1", fc)
            self.assertLess(fc.index("concat=n=3"), fc.index("loudnorm"), "loudnorm must come after the audio concat")
            # Per-input audio prep [0:a:0]... must NOT contain loudnorm.
            prep = fc.split("[ja0_0]")[0]
            self.assertNotIn("loudnorm", prep)

    def test_loudnorm_disables_stream_copy_in_step(self):
        # When loudnorm is requested and audio is 'copy', the step switches the
        # audio codec away from copy (AAC) so loudnorm can be applied.
        answers = {
            "audio_streams": [audio_stream()],
            "audio_tracks": [0],
            "audio_codec": "copy",
            "output_ext": "mp4",
            "input_path": Path("x.mkv"),
            "format": {"duration": "5"},
        }
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=["2", "-16"]), \
             mock.patch.object(FFmWiz, "ask_yes_no", return_value=True):
            FFmWiz.step_loudnorm(answers)
        self.assertNotEqual(answers["audio_codec"], "copy")
        self.assertTrue(answers["loudnorm_enabled"])

    # ================= Two-pass analysis command tests =================
    def test_pass1_maps_assembled_audio_to_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self.join_answers(tmp)
            args = FFmWiz.build_join_loudnorm_analysis_args(answers, items, 0, -16.0)
            self.assertIn("[mjanalysis]", args)
            self.assertIn("-map", args)
            self.assertEqual(args[args.index("-map") + 1], "[mjanalysis]")
            self.assertEqual(args[-3:], ["-f", "null", os.devnull])

    def test_pass1_includes_all_join_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self.join_answers(tmp)
            args = FFmWiz.build_join_loudnorm_analysis_args(answers, items, 0, -16.0)
            self.assertEqual(args.count("-i"), 3)
            fc = args[args.index("-filter_complex") + 1]
            for i in range(3):
                self.assertIn(f"[{i}:a:0]", fc)
            self.assertIn("concat=n=3:v=0:a=1", fc)

    def test_pass1_no_video_encode(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self.join_answers(tmp)
            args = FFmWiz.build_join_loudnorm_analysis_args(answers, items, 0, -16.0)
            self.assertIn("-vn", args)
            self.assertNotIn("-c:v", args)

    def test_pass1_no_media_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self.join_answers(tmp)
            args = FFmWiz.build_join_loudnorm_analysis_args(answers, items, 0, -16.0)
            self.assertEqual(args[-3:], ["-f", "null", os.devnull])

    def test_pass1_uses_print_format_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self.join_answers(tmp)
            args = FFmWiz.build_join_loudnorm_analysis_args(answers, items, 0, -16.0)
            fc = args[args.index("-filter_complex") + 1]
            self.assertIn("print_format=json", fc)

    def test_pass1_not_only_first_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self.join_answers(tmp)
            fc = FFmWiz.build_join_loudnorm_analysis_args(answers, items, 0, -16.0)[
                FFmWiz.build_join_loudnorm_analysis_args(answers, items, 0, -16.0).index("-filter_complex") + 1
            ]
            self.assertEqual(fc.count(":a:0]"), 3)

    def test_pass1_respects_trims(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self.join_answers(tmp)
            answers["cut_keep_ranges"] = [(1.0, 5.0), (8.0, 12.0)]
            fc = FFmWiz.build_join_loudnorm_analysis_args(answers, items, 0, -16.0)[
                FFmWiz.build_join_loudnorm_analysis_args(answers, items, 0, -16.0).index("-filter_complex") + 1
            ]
            self.assertIn("atrim=", fc)

    def test_pass1_respects_channel_layout_prep(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self.join_answers(tmp)
            args = FFmWiz.build_join_loudnorm_analysis_args(answers, items, 0, -16.0)
            fc = args[args.index("-filter_complex") + 1]
            self.assertIn(FFmWiz.JOIN_AUDIO_PREP_FILTER, fc)

    def test_pass1_topology_matches_input_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self.join_answers(tmp)
            args = FFmWiz.build_join_loudnorm_analysis_args(answers, items, 0, -16.0)
            fc = args[args.index("-filter_complex") + 1]
            self.assertIn(f"concat=n={len(items)}:v=0:a=1", fc)

    # ================= Two-pass final command tests =================
    def _two_pass_join_cmd(self, tmp):
        answers, items = self.join_answers(tmp)
        answers["loudnorm_enabled"] = True
        answers["loudnorm_target_i"] = -16.0
        answers["loudnorm_mode"] = "two_pass"
        answers["loudnorm_measured"] = {
            "input_i": -19.0, "input_tp": -3.0, "input_lra": 6.0,
            "input_thresh": -29.0, "target_offset": -0.2,
        }
        cmd = FFmWiz.build_join_encode_command(answers, items, Path(tmp) / "out.mp4")
        fc = next(cmd[i + 1] for i, a in enumerate(cmd) if a == "-filter_complex")
        return cmd, fc

    def test_pass2_injects_measured_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, fc = self._two_pass_join_cmd(tmp)
            for token in ("measured_I=-19", "measured_TP=-3", "measured_LRA=6", "measured_thresh=-29", "offset=-0.2"):
                self.assertIn(token, fc)

    def test_pass2_uses_linear_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, fc = self._two_pass_join_cmd(tmp)
            self.assertIn("linear=true", fc)

    def test_pass2_loudnorm_after_concat(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, fc = self._two_pass_join_cmd(tmp)
            self.assertLess(fc.index("concat=n=3"), fc.index("loudnorm"))

    def test_pass2_encodes_final_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd, _ = self._two_pass_join_cmd(tmp)
            self.assertTrue(any(str(part).endswith("out.mp4") for part in cmd))
            self.assertIn("-c:v", cmd)

    def test_pass2_not_first_input_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, fc = self._two_pass_join_cmd(tmp)
            prep = fc.split("[ja0_0]")[0]
            self.assertNotIn("loudnorm", prep)

    def test_join_encode_clean_mapping_when_metadata_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd, _ = self._two_pass_join_cmd(tmp)
            # Clean mapping: metadata/chapters stripped and NO accidental data
            # stream mapping (only the filtered video + audio labels are mapped).
            self.assertIn("-map_metadata", cmd)
            self.assertEqual(cmd[cmd.index("-map_metadata") + 1], "-1")
            self.assertIn("-map_chapters", cmd)
            self.assertEqual(cmd[cmd.index("-map_chapters") + 1], "-1")
            mapped = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
            self.assertTrue(all(m.startswith("[") for m in mapped), f"only label maps expected, got {mapped}")
            self.assertFalse(any(":d" in m or m.endswith(":d") for m in mapped))

    # ================= Join regression: 31 inputs =================
    def test_join_31_inputs_measurement(self):
        import re
        with tempfile.TemporaryDirectory() as tmp:
            items = [make_item(Path(tmp) / f"v{i:02d}.mov", duration=5.0) for i in range(31)]
            answers = {"ffmpeg": "ffmpeg", "cut_keep_ranges": []}
            args = FFmWiz.build_join_loudnorm_analysis_args(answers, items, 0, -16.0)
            fc = args[args.index("-filter_complex") + 1]
            self.assertEqual(args.count("-i"), 31)
            self.assertEqual(fc.count(":a:0]"), 31)
            self.assertIn("concat=n=31:v=0:a=1", fc)
            labels = sorted(set(re.findall(r"\[mja(\d+)\]", fc)), key=int)
            self.assertEqual(len(labels), 31, "31 unique per-input audio labels expected")
            self.assertLess(fc.index("concat=n=31"), fc.index("loudnorm"))

    # ================= Progress de-duplication tests =================
    def _capture_progress(self, fn):
        buf = io.StringIO()
        with mock.patch.object(FFmWiz, "_stdout_supports_in_place_progress", return_value=True), \
             mock.patch.object(FFmWiz, "_progress_terminal_width", return_value=200), \
             mock.patch.object(FFmWiz, "_enable_windows_vt_mode"), \
             contextlib.redirect_stdout(buf):
            fn()
        return FFmWiz._strip_ansi(buf.getvalue())

    def test_progress_final_line_emitted_once(self):
        def run():
            FFmWiz._begin_progress_render()
            FFmWiz._write_progress_line("LIVE 50pct")
            FFmWiz._finish_progress_line("FINAL 100pct")   # committed once
            FFmWiz._write_progress_line("LATE 100pct")      # suppressed
            FFmWiz._finish_progress_line("FINAL 100pct")    # suppressed
        out = self._capture_progress(run)
        self.assertEqual(out.count("FINAL 100pct"), 1)
        self.assertNotIn("LATE", out)

    def test_progress_live_then_final_for_new_run(self):
        def run():
            FFmWiz._begin_progress_render()
            FFmWiz._write_progress_line("A live")
            FFmWiz._finish_progress_line("A final")
            # New run resets the guard so its own final line commits.
            FFmWiz._begin_progress_render()
            FFmWiz._write_progress_line("B live")
            FFmWiz._finish_progress_line("B final")
        out = self._capture_progress(run)
        self.assertEqual(out.count("A final"), 1)
        self.assertEqual(out.count("B final"), 1)

    # ================= Join default bitrate tests =================
    def test_join_bitrate_default_uses_highest(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers(tmp, bitrates=("128000", "96000", "160000"))
            kbps, name = FFmWiz.join_max_source_audio_bitrate(answers, 0)
            self.assertEqual(kbps, 160)
            self.assertEqual(name, "C.mov")

    def test_join_bitrate_ignores_unknown_when_some_known(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers(tmp)
            # Make the second join item's audio bitrate unknown.
            answers["join_input_items"][0]["audio_streams"][0].pop("bit_rate", None)
            answers["join_input_items"][0]["audio_streams"][0].pop("duration", None)
            kbps, name = FFmWiz.join_max_source_audio_bitrate(answers, 0)
            self.assertIsNotNone(kbps)

    def test_join_bitrate_all_unknown_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers(tmp)
            for stream in [answers["audio_streams"][0]] + [it["audio_streams"][0] for it in answers["join_input_items"]]:
                stream.pop("bit_rate", None)
                stream.pop("duration", None)
            with mock.patch.object(FFmWiz, "get_packet_sizes", return_value={}):
                kbps, name = FFmWiz.join_max_source_audio_bitrate(answers, 0)
            self.assertIsNone(kbps)
            self.assertIsNone(name)

    # ================= Join metadata default tests =================
    def test_join_metadata_default_is_n(self):
        captured = {}

        def fake_yes_no(prompt, default):
            captured["default"] = default
            return default

        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers(tmp)
            with mock.patch.object(FFmWiz, "source_extra_preservation_features", return_value=["metadata"]), \
                 mock.patch.object(FFmWiz, "ask_yes_no", side_effect=fake_yes_no):
                FFmWiz.step_source_extra_policy(answers)
            self.assertFalse(captured["default"])
            self.assertFalse(answers["keep_source_metadata"])

    def test_non_join_metadata_default_is_y(self):
        captured = {}

        def fake_yes_no(prompt, default):
            captured["default"] = default
            return default

        answers = {
            "input_path": Path("x.mkv"), "output_ext": "mkv", "video_streams": [video_stream()],
            "audio_streams": [audio_stream()], "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
        }
        with mock.patch.object(FFmWiz, "source_extra_preservation_features", return_value=["metadata"]), \
             mock.patch.object(FFmWiz, "ask_yes_no", side_effect=fake_yes_no):
            FFmWiz.step_source_extra_policy(answers)
        self.assertTrue(captured["default"])
        self.assertTrue(answers["keep_source_metadata"])

    # ================= Join summary report tests =================
    def test_join_summary_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers(tmp, bitrates=("128000", "96000", "160000"))
            with mock.patch.object(FFmWiz, "get_packet_sizes", return_value={}):
                lines = FFmWiz.format_join_input_summary_lines(answers)
            joined = "\n".join(lines)
            self.assertIn("Join input summary", joined)
            self.assertIn("Files selected: 3", joined)
            self.assertIn("Audio bitrate:", joined)
            self.assertIn("highest 160 kbps (C.mov)", joined)
            self.assertIn("lowest 96 kbps (B.mov)", joined)
            self.assertTrue(any("FPS:" in line for line in lines))

    def test_join_summary_handles_all_unknown_fps(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers(tmp)
            for streams in [answers["video_streams"]] + [it["video_streams"] for it in answers["join_input_items"]]:
                streams[0].pop("avg_frame_rate", None)
                streams[0].pop("r_frame_rate", None)
            with mock.patch.object(FFmWiz, "get_packet_sizes", return_value={}):
                lines = FFmWiz.format_join_input_summary_lines(answers)
            self.assertIn("FPS: unavailable", "\n".join(lines))

    # ================= Audio report suppression tests =================
    def test_audio_report_signature_changes_with_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers(tmp)
            sig1 = FFmWiz._audio_report_signature(answers)
            answers["join_input_items"].append(make_item(Path(tmp) / "D.mov"))
            sig2 = FFmWiz._audio_report_signature(answers)
            self.assertNotEqual(sig1, sig2)

    def test_audio_report_suppressed_on_reentry(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers(tmp)
            answers["detect_duplicate_audio"] = False
            with mock.patch.object(FFmWiz, "get_audio_volume_stats", return_value={}), \
                 mock.patch.object(FFmWiz, "get_packet_sizes", return_value={}), \
                 mock.patch.object(FFmWiz, "ask_raw", return_value="0"):
                buf1 = io.StringIO()
                with contextlib.redirect_stdout(buf1):
                    FFmWiz.step_audio_tracks(answers)
                buf2 = io.StringIO()
                with contextlib.redirect_stdout(buf2):
                    FFmWiz.step_audio_tracks(answers)
            self.assertIn("Detected audio tracks:", buf1.getvalue())
            self.assertNotIn("Detected audio tracks:", buf2.getvalue())
            self.assertIn("already displayed", buf2.getvalue())

    # ================= loudnorm mode + UI tests =================
    def test_loudnorm_mode_resolution(self):
        self.assertEqual(FFmWiz.loudnorm_mode({"loudnorm_mode": "off"}), "off")
        self.assertEqual(FFmWiz.loudnorm_mode({"loudnorm_enabled": True, "loudnorm_mode": "single"}), "single")
        self.assertEqual(FFmWiz.loudnorm_mode({"loudnorm_enabled": False}), "off")
        self.assertEqual(
            FFmWiz.loudnorm_mode({"loudnorm_enabled": True, "loudnorm_measured": {"input_i": -1}}),
            "two_pass",
        )

    def test_loudnorm_menu_default_off(self):
        answers = {"audio_streams": [audio_stream()], "audio_tracks": [0], "audio_codec": "aac",
                   "output_ext": "mp4", "input_path": Path("x.mkv"), "format": {"duration": "5"}}
        with mock.patch.object(FFmWiz, "ask_raw", return_value=""):  # Enter -> default
            FFmWiz.step_loudnorm(answers)
        self.assertFalse(answers["loudnorm_enabled"])
        self.assertEqual(answers["loudnorm_mode"], "off")

    def test_loudnorm_menu_single_sets_mode(self):
        answers = {"audio_streams": [audio_stream()], "audio_tracks": [0], "audio_codec": "aac",
                   "output_ext": "mp4", "input_path": Path("x.mkv"), "format": {"duration": "5"}}
        # Single-pass now offers to measure first; decline -> straight to target.
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=["2", "-16"]), \
             mock.patch.object(FFmWiz, "ask_yes_no", return_value=False):
            FFmWiz.step_loudnorm(answers)
        self.assertTrue(answers["loudnorm_enabled"])
        self.assertEqual(answers["loudnorm_mode"], "single")
        self.assertNotIn("loudnorm_measured", answers)

    def test_loudnorm_single_measures_first_then_target(self):
        answers = {"audio_streams": [audio_stream()], "audio_tracks": [0], "audio_codec": "aac",
                   "output_ext": "mp4", "input_path": Path("x.mkv"), "format": {"duration": "5"}}
        measured = {"input_i": -22.0, "input_tp": -3.0, "input_lra": 5.0, "input_thresh": -32.0, "target_offset": -0.2}
        # Choose single (2), accept "measure first" -> measurement shown -> then target.
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=["2", "-16"]), \
             mock.patch.object(FFmWiz, "ask_yes_no", return_value=True), \
             mock.patch.object(FFmWiz, "probe_loudnorm_measurement", return_value=measured), \
             mock.patch.object(FFmWiz, "print_loudnorm_stats") as stats:
            FFmWiz.step_loudnorm(answers)
        self.assertTrue(stats.called)  # current loudness shown BEFORE the target
        self.assertEqual(answers["loudnorm_mode"], "single")
        # Single-pass shows the measurement but does not inject measured values.
        self.assertNotIn("loudnorm_measured", answers)

    def test_loudnorm_menu_two_pass_measures_and_no_vague_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers(tmp)
            measured = {"input_i": -18.0, "input_tp": -2.0, "input_lra": 5.0, "input_thresh": -28.0, "target_offset": -0.1}
            with mock.patch.object(FFmWiz, "ask_raw", side_effect=["3", "-16"]), \
                 mock.patch.object(FFmWiz, "ask_yes_no", return_value=True), \
                 mock.patch.object(FFmWiz, "probe_join_loudnorm_measurement", return_value=measured), \
                 mock.patch.object(FFmWiz, "print_loudnorm_stats"):
                FFmWiz.step_loudnorm(answers)
            self.assertEqual(answers["loudnorm_mode"], "two_pass")
            self.assertEqual(answers["loudnorm_measured"], measured)

    def test_loudnorm_two_pass_decline_measure_falls_back_to_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers(tmp)
            # Choose two-pass (3) but decline the measure question -> single-pass.
            with mock.patch.object(FFmWiz, "ask_raw", side_effect=["3", "-16"]), \
                 mock.patch.object(FFmWiz, "ask_yes_no", return_value=False):
                FFmWiz.step_loudnorm(answers)
            self.assertEqual(answers["loudnorm_mode"], "single")
            self.assertNotIn("loudnorm_measured", answers)

    def test_loudnorm_two_pass_parse_failure_can_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers(tmp)
            with mock.patch.object(FFmWiz, "ask_raw", side_effect=["3", "-16", "m"]), \
                 mock.patch.object(FFmWiz, "ask_yes_no", return_value=True), \
                 mock.patch.object(FFmWiz, "probe_join_loudnorm_measurement", return_value=None):
                FFmWiz.step_loudnorm(answers)
            self.assertFalse(answers["loudnorm_enabled"])
            self.assertEqual(answers["loudnorm_mode"], "off")

    # ================= Missing-audio handling =================
    def test_join_missing_audio_raises_listing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self.join_answers(tmp)
            # Remove audio from the middle joined input.
            items[1]["audio_streams"] = []
            with self.assertRaises(RuntimeError) as ctx:
                FFmWiz.build_join_encode_command(answers, items, Path(tmp) / "out.mp4")
            self.assertIn("B.mov", str(ctx.exception))

    # ================= Audio+Video Join (auto-detect) =================
    def test_join_load_media_item_rejects_audio_only_by_default(self):
        with mock.patch.object(FFmWiz, "ffprobe_json", return_value={
            "format": {"duration": "5"},
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
        }):
            with self.assertRaises(ValueError):
                FFmWiz.join_load_media_item({"ffprobe": "ffprobe"}, Path("a.m4a"))

    def test_join_load_media_item_accepts_audio_only_when_allowed(self):
        with mock.patch.object(FFmWiz, "ffprobe_json", return_value={
            "format": {"duration": "5"},
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
        }):
            item = FFmWiz.join_load_media_item({"ffprobe": "ffprobe"}, Path("a.m4a"), allow_audio_only=True)
        self.assertEqual(item["video_streams"], [])
        self.assertEqual(len(item["audio_streams"]), 1)

    def test_join_load_media_item_rejects_empty_when_allowed(self):
        with mock.patch.object(FFmWiz, "ffprobe_json", return_value={"format": {}, "streams": []}):
            with self.assertRaises(ValueError):
                FFmWiz.join_load_media_item({"ffprobe": "ffprobe"}, Path("x.bin"), allow_audio_only=True)

    def test_build_join_audio_encode_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [
                make_item(Path(tmp) / "a.m4a", duration=4.0, audio_bitrate="128000"),
                make_item(Path(tmp) / "b.m4a", duration=6.0, audio_bitrate="96000"),
            ]
            for it in items:
                it["video_streams"] = []  # audio-only
            answers = {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "audio_bitrate_kbps": 160,
                       "output_collision_suffix": "_Encode"}
            cmd = FFmWiz.build_join_audio_encode_command(answers, items, Path(tmp) / "out.m4a")
            fc = cmd[cmd.index("-filter_complex") + 1]
            self.assertIn("concat=n=2:v=0:a=1", fc)
            self.assertIn("[0:a:0]", fc)
            self.assertIn("[1:a:0]", fc)
            self.assertEqual(cmd[cmd.index("-map") + 1], "[a]")
            self.assertIn("-vn", cmd)
            self.assertNotIn("-c:v", cmd)
            self.assertIn("aac", cmd)
            self.assertIn("160k", cmd)

    def test_print_join_summary_handles_audio_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [make_item(Path(tmp) / "a.m4a"), make_item(Path(tmp) / "b.m4a")]
            for it in items:
                it["video_streams"] = []
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                FFmWiz.print_join_summary(items, False, ["audio re-encode"])
            out = buf.getvalue()
            self.assertIn("audio-only", out)
            self.assertNotIn("Traceback", out)

    # ================= Audio transform: GUI vs manual =================
    def test_audio_transform_menu_manual_sets_speed(self):
        answers = {"audio_index": 0, "format": {"duration": "10"}}
        # _apply_manual_audio_transform: cuts? -> False, speed -> 150%, reverse -> False.
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=["150%"]), \
             mock.patch.object(FFmWiz, "ask_yes_no", side_effect=[False, False]):
            FFmWiz._apply_manual_audio_transform(answers)
        self.assertFalse(answers["_audio_transform_noop"])
        self.assertAlmostEqual(answers["audio_speed_factor"], 1.5)
        self.assertFalse(answers["reverse_audio"])
        self.assertTrue(answers["audio_speed_enabled"])

    def test_audio_transform_manual_noop_when_unchanged(self):
        answers = {"audio_index": 0, "format": {"duration": "10"}}
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=["100%"]), \
             mock.patch.object(FFmWiz, "ask_yes_no", side_effect=[False, False]):
            FFmWiz._apply_manual_audio_transform(answers)
        self.assertTrue(answers["_audio_transform_noop"])

    def test_audio_transform_manual_reverse_only(self):
        answers = {"audio_index": 0, "format": {"duration": "10"}}
        # cuts? -> False, speed -> 100%, reverse -> True.
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=["100%"]), \
             mock.patch.object(FFmWiz, "ask_yes_no", side_effect=[False, True]):
            FFmWiz._apply_manual_audio_transform(answers)
        self.assertFalse(answers["_audio_transform_noop"])
        self.assertTrue(answers["reverse_audio"])

    def test_audio_transform_manual_with_cuts(self):
        answers = {"audio_index": 0, "format": {"duration": "10"}}
        # cuts? -> True (collect mocked), speed -> 100%, reverse -> False.
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=["100%"]), \
             mock.patch.object(FFmWiz, "ask_yes_no", side_effect=[True, False]), \
             mock.patch.object(FFmWiz, "collect_cut_ranges_terminal", return_value=[(0.0, 4.0)]):
            FFmWiz._apply_manual_audio_transform(answers)
        self.assertFalse(answers["_audio_transform_noop"])
        self.assertEqual(answers["audio_cut_keep_ranges"], [(0.0, 4.0)])

    def test_audio_transform_manual_back_at_speed_returns_to_cuts(self):
        # Back ('0') at the speed prompt returns to the cuts question (not out).
        answers = {"audio_index": 0, "format": {"duration": "10"}}
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=["0", "150%"]), \
             mock.patch.object(FFmWiz, "ask_yes_no", side_effect=[False, False, False]):
            FFmWiz._apply_manual_audio_transform(answers)
        self.assertAlmostEqual(answers["audio_speed_factor"], 1.5)

    def test_audio_transform_menu_manual_routes(self):
        answers = {"audio_index": 0, "format": {"duration": "10"}}
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=["2", "150%"]), \
             mock.patch.object(FFmWiz, "ask_yes_no", side_effect=[False, False]), \
             mock.patch.object(FFmWiz, "_confirm_audio_transform_start") as confirm:
            FFmWiz.step_audio_transform_editor(answers)
        self.assertAlmostEqual(answers["audio_speed_factor"], 1.5)
        self.assertTrue(confirm.called)
        self.assertTrue(answers["_audio_transform_finalized"])

    def test_audio_transform_gui_success(self):
        answers = {"audio_index": 0, "format": {"duration": "10"}}
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=["1"]), \
             mock.patch.object(FFmWiz, "_confirm_audio_transform_start"), \
             mock.patch.object(FFmWiz, "open_audio_transform_gui",
                               return_value={"keep_ranges": [], "speed": 1.5, "reverse": True}):
            FFmWiz.step_audio_transform_editor(answers)
        self.assertFalse(answers["_audio_transform_noop"])
        self.assertAlmostEqual(answers["audio_speed_factor"], 1.5)
        self.assertTrue(answers["reverse_audio"])

    def test_audio_transform_back_at_confirm_returns_to_configure(self):
        # Confirm raises Back once -> editor re-runs the value prompts (configure),
        # not the menu; the second confirm succeeds.
        answers = {"audio_index": 0, "format": {"duration": "10"}}
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=["2", "150%", "120%"]), \
             mock.patch.object(FFmWiz, "ask_yes_no", side_effect=[False, False, False, False]), \
             mock.patch.object(FFmWiz, "_confirm_audio_transform_start",
                               side_effect=[FFmWiz.Back(), None]):
            FFmWiz.step_audio_transform_editor(answers)
        # The first menu choice persists; only the value prompts were repeated.
        self.assertAlmostEqual(answers["audio_speed_factor"], 1.2)
        self.assertTrue(answers["_audio_transform_finalized"])

    # ================= Lossless split =================
    def test_parse_split_timestamp_formats(self):
        self.assertAlmostEqual(FFmWiz.parse_split_timestamp("10:00:000"), 600.0)
        self.assertAlmostEqual(FFmWiz.parse_split_timestamp("10:00"), 600.0)
        self.assertAlmostEqual(FFmWiz.parse_split_timestamp("20:00:000"), 1200.0)
        self.assertAlmostEqual(FFmWiz.parse_split_timestamp("90"), 90.0)
        self.assertAlmostEqual(FFmWiz.parse_split_timestamp("1:00:00:000"), 3600.0)
        self.assertAlmostEqual(FFmWiz.parse_split_timestamp("0:30:500"), 30.5)

    def test_parse_split_timestamp_bare_unit(self):
        self.assertAlmostEqual(FFmWiz.parse_split_timestamp("16", "m"), 960.0)
        self.assertAlmostEqual(FFmWiz.parse_split_timestamp("16", "h"), 57600.0)
        self.assertAlmostEqual(FFmWiz.parse_split_timestamp("16", "s"), 16.0)

    def test_largest_time_unit(self):
        self.assertEqual(FFmWiz.largest_time_unit(45.0), "s")
        self.assertEqual(FFmWiz.largest_time_unit(120.0), "m")
        self.assertEqual(FFmWiz.largest_time_unit(7200.0), "h")

    def test_parse_split_times_bare_number_uses_largest_unit(self):
        # 31-minute file: a bare "16" means 16 minutes.
        self.assertEqual(FFmWiz.parse_split_times_line("16", 1884.0), [960.0])
        # Short file: a bare "16" means 16 seconds.
        self.assertEqual(FFmWiz.parse_split_times_line("16", 30.0), [16.0])

    def test_parse_split_times_line_sorts_and_filters(self):
        pts = FFmWiz.parse_split_times_line("20:00:000,10:00,25:00:000", 1800.0)
        self.assertEqual(pts, [600.0, 1200.0, 1500.0])
        # Out-of-range points are dropped.
        self.assertEqual(FFmWiz.parse_split_times_line("10:00, 40:00", 1800.0), [600.0])
        # Missing milliseconds treated as 0.
        self.assertEqual(FFmWiz.parse_split_times_line("5:00", 600.0), [300.0])

    def test_build_lossless_split_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = {"ffmpeg": "ffmpeg", "input_path": Path(tmp) / "clip.mp4",
                       "output_location": Path(tmp), "audio_index": 0,
                       "audio_streams": [{"codec_type": "audio", "codec_name": "aac"}]}
            cmd, pattern = FFmWiz.build_lossless_split_command(answers, [600.0, 1200.0, 1500.0])
            self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
            self.assertEqual(cmd[cmd.index("-f") + 1], "segment")
            self.assertEqual(cmd[cmd.index("-segment_times") + 1], "600.000000,1200.000000,1500.000000")
            # Audio Cut tool: only the selected audio track is mapped (not -map 0).
            self.assertEqual(cmd[cmd.index("-map") + 1], "0:a:0")
            self.assertIn("-vn", cmd)
            self.assertNotIn("0", [cmd[i + 1] for i, t in enumerate(cmd) if t == "-map"])
            # aac source -> .m4a audio container.
            self.assertIn("_part%03d.m4a", str(pattern))

    def test_lossless_audio_copy_ext_mapping(self):
        self.assertEqual(FFmWiz.lossless_audio_copy_ext("aac", ".mp4"), "m4a")
        self.assertEqual(FFmWiz.lossless_audio_copy_ext("mp3", ".mp4"), "mp3")
        self.assertEqual(FFmWiz.lossless_audio_copy_ext("flac", ".mkv"), "flac")
        self.assertEqual(FFmWiz.lossless_audio_copy_ext("opus", ".webm"), "opus")
        # Unknown codec falls back to a universal audio container.
        self.assertEqual(FFmWiz.lossless_audio_copy_ext("weird", ".xyz"), "mka")

    def test_manual_split_flow_sets_split_points(self):
        answers = {"audio_index": 0, "format": {"duration": "1800"}}
        # menu -> manual(2); "add cuts/split?" yes; layout 5; split line.
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=["2", "5", "10:00,20:00,25:00"]), \
             mock.patch.object(FFmWiz, "ask_yes_no", return_value=True), \
             mock.patch.object(FFmWiz, "_confirm_audio_transform_start"):
            FFmWiz.step_audio_transform_editor(answers)
        self.assertFalse(answers["_audio_transform_noop"])
        self.assertEqual(answers["_audio_transform_split_points"], [600.0, 1200.0, 1500.0])
        # Split is mutually exclusive with speed/reverse transforms.
        self.assertFalse(answers["audio_speed_enabled"])
        self.assertEqual(answers["audio_cut_keep_ranges"], [])

    # ================= Audio join in the interactive wizard =================
    def test_wizard_join_applicable_for_audio_input(self):
        audio_answers = {"input_path": Path("a.wav"), "audio_streams": [audio_stream()], "video_streams": []}
        video_answers = {"input_path": Path("v.mkv"), "audio_streams": [audio_stream()],
                         "video_streams": [video_stream()], "output_ext": "mkv"}
        no_input = {"audio_streams": [audio_stream()], "video_streams": []}
        self.assertTrue(FFmWiz.wizard_audio_join_applicable(audio_answers))
        self.assertFalse(FFmWiz.wizard_audio_join_applicable(video_answers))
        self.assertFalse(FFmWiz.wizard_audio_join_applicable(no_input))
        self.assertTrue(FFmWiz.wizard_join_inputs_applicable(audio_answers))
        self.assertTrue(FFmWiz.wizard_join_inputs_applicable(video_answers))

    # ================= Track Manager (menu 15) =================
    def test_parse_track_remove_specs(self):
        self.assertEqual(FFmWiz.parse_track_remove_specs("2, a:1 , s:0"), ["2", "a:1", "s:0"])
        self.assertEqual(FFmWiz.parse_track_remove_specs("a:0,a:0"), ["a:0"])  # dedup
        self.assertEqual(FFmWiz.parse_track_remove_specs(""), [])
        with self.assertRaises(ValueError):
            FFmWiz.parse_track_remove_specs("x:9")
        with self.assertRaises(ValueError):
            FFmWiz.parse_track_remove_specs("5", stream_count=3)

    def test_build_track_manager_command_remove_and_add(self):
        ext = {"path": Path("ext.aac"), "audio_streams": [{"codec_type": "audio"}], "subtitle_streams": []}
        cmd = FFmWiz.build_track_manager_command("ffmpeg", Path("in.mkv"), ["a:1"], [ext], Path("out.mkv"))
        # map all, drop a:1, add the external audio, copy.
        self.assertEqual(cmd[cmd.index("-map") + 1], "0")
        self.assertIn("-0:a:1", cmd)
        # External audio map must be REQUIRED (no trailing '?') and target only
        # the first audio stream of the external file (:a:0).
        self.assertIn("1:a:0", cmd)
        self.assertNotIn("1:a", cmd)
        self.assertNotIn("1:a?", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
        self.assertTrue(str(cmd[-1]).endswith("out.mkv"))

    def test_build_track_manager_command_remove_only(self):
        cmd = FFmWiz.build_track_manager_command("ffmpeg", Path("in.mkv"), ["2"], [], Path("out.mkv"))
        self.assertIn("-0:2", cmd)
        self.assertNotIn("1:a:0", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")

    def test_build_track_manager_command_loudnorm_reencodes_audio(self):
        # With loudnorm enabled, audio is re-encoded (-c:a) with a loudnorm
        # -filter:a, while the rest stays stream-copied.
        answers = {"loudnorm_enabled": True, "loudnorm_mode": "single",
                   "loudnorm_target_i": -16.0, "audio_codec": "aac", "audio_bitrate_kbps": 160}
        cmd = FFmWiz.build_track_manager_command(
            "ffmpeg", Path("in.mkv"), [], [], Path("out.mkv"), answers)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
        self.assertIn("-c:a", cmd)
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "aac")
        self.assertIn("-filter:a", cmd)
        self.assertIn("loudnorm", cmd[cmd.index("-filter:a") + 1])
        self.assertEqual(cmd[cmd.index("-b:a") + 1], "160k")

    def test_build_track_manager_command_drops_metadata_when_requested(self):
        answers = {"track_manager_keep_metadata": False}
        cmd = FFmWiz.build_track_manager_command("ffmpeg", Path("in.mkv"), [], [], Path("out.mkv"), answers)
        self.assertEqual(cmd[cmd.index("-map_metadata") + 1], "-1")
        self.assertIn("-map_chapters", cmd)
        self.assertEqual(cmd[cmd.index("-map_chapters") + 1], "-1")
        self.assertIn("-map_metadata:s", cmd)

    def test_build_track_manager_command_keeps_metadata_by_default(self):
        cmd = FFmWiz.build_track_manager_command("ffmpeg", Path("in.mkv"), [], [], Path("out.mkv"))
        self.assertEqual(cmd[cmd.index("-map_metadata") + 1], "0")
        self.assertNotIn("-map_metadata:s", cmd)

    def test_join_audio_encode_applies_loudnorm(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = [make_item(Path(tmp) / "a.m4a"), make_item(Path(tmp) / "b.m4a")]
            for it in items:
                it["video_streams"] = []
            answers = {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "audio_bitrate_kbps": 160,
                       "loudnorm_enabled": True, "loudnorm_target_i": -16.0, "loudnorm_mode": "single"}
            cmd = FFmWiz.build_join_audio_encode_command(answers, items, Path(tmp) / "out.m4a")
            fc = cmd[cmd.index("-filter_complex") + 1]
            self.assertIn("concat=n=2:v=0:a=1", fc)
            self.assertIn("loudnorm", fc)
            self.assertLess(fc.index("concat=n=2"), fc.index("loudnorm"))


class TrackManagerAndOutputFormatTests(unittest.TestCase):
    """Covers the Track Manager removal-spec normalization and the stricter
    output-format handling (mkv default for mkv input, reject typos)."""

    def setUp(self):
        FFmWiz.USE_COLOR = False

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
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=list(typed)):
            FFmWiz.step_output_format(answers)
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


def _vstream(pix_fmt="yuv420p", depth=8):
    return {
        "codec_type": "video", "codec_name": "hevc",
        "width": 1920, "height": 1080,
        "avg_frame_rate": "30/1", "r_frame_rate": "30/1",
        "pix_fmt": pix_fmt, "bits_per_raw_sample": str(depth),
        "color_range": "tv",
    }


class PixelFormatResolverTests(unittest.TestCase):
    """Architecture-aware 10-bit/12-bit+ pixel-format selection (CPU vs NVENC)."""

    def setUp(self):
        FFmWiz.USE_COLOR = False

    def _ans(self, pix_fmt, depth, use_gpu, codec="H265"):
        return {"video_streams": [_vstream(pix_fmt, depth)], "video_codec": codec, "use_gpu": use_gpu}

    # ---- resolver: encoder x bit depth ----
    def test_libx265_main_8bit_yuv420p(self):
        a = self._ans("yuv420p", 8, False)
        self.assertEqual(FFmWiz.cpu_graph_pixel_format_for_encoder(a), "yuv420p")
        self.assertEqual(FFmWiz.hevc_profile_for_output(a, "main"), "main")

    def test_libx265_main10_10bit_yuv420p10le(self):
        a = self._ans("yuv420p10le", 10, False)
        self.assertEqual(FFmWiz.cpu_graph_pixel_format_for_encoder(a), "yuv420p10le")
        self.assertEqual(FFmWiz.hevc_profile_for_output(a, "main"), "main10")

    def test_nvenc_main_8bit_format(self):
        a = self._ans("yuv420p", 8, True)
        # CPU filter graph feeding NVENC keeps yuv420p (8-bit, unchanged).
        self.assertEqual(FFmWiz.cpu_graph_pixel_format_for_encoder(a), "yuv420p")
        # CUDA hardware path uses nv12.
        self.assertEqual(FFmWiz.cuda_pixel_format_for_output(a), "nv12")
        self.assertEqual(FFmWiz.target_pixel_format_for_answers(a), "nv12")

    def test_nvenc_main10_10bit_p010le(self):
        a = self._ans("yuv420p10le", 10, True)
        self.assertEqual(FFmWiz.cpu_graph_pixel_format_for_encoder(a), "p010le")
        self.assertEqual(FFmWiz.cuda_pixel_format_for_output(a), "p010le")
        self.assertEqual(FFmWiz.hevc_profile_for_output(a), "main10")

    # ---- source/output preservation + notes ----
    def test_source_10bit_output_10bit_preserved_no_note(self):
        a = self._ans("yuv420p10le", 10, False)
        self.assertEqual(FFmWiz.output_video_bit_depth(a), 10)
        self.assertIsNone(FFmWiz.bit_depth_precision_note(a))

    def test_source_8bit_output_10bit_upconvert_note(self):
        a = self._ans("yuv420p", 8, False)
        a["force_output_bit_depth"] = 10
        note = FFmWiz.bit_depth_precision_note(a)
        self.assertIsNotNone(note)
        self.assertIn("source precision remains 8-bit", note)
        # Up-conversion is not a reduction.
        self.assertNotIn("reduced", note)

    def test_unsupported_high_depth_caps_at_10(self):
        # 12-bit+ never silently kept; output capped at 10-bit Main10.
        for depth, pix in ((12, "yuv420p12le"), (14, "yuv420p14le"), (16, "yuv420p16le")):
            a = self._ans(pix, depth, False)
            self.assertEqual(FFmWiz.output_video_bit_depth(a), 10)
            self.assertEqual(FFmWiz.cpu_pixel_format_for_output(a), "yuv420p10le")
            self.assertEqual(FFmWiz.hevc_profile_for_output(a, "main"), "main10")

    # ---- 12-bit+ reduction notes ----
    def test_12bit_reduction_note(self):
        a = self._ans("yuv420p12le", 12, False)
        self.assertIn("reduced from 12-bit to 10-bit", FFmWiz.bit_depth_precision_note(a))

    def test_14bit_reduction_note(self):
        a = self._ans("yuv420p14le", 14, False)
        self.assertIn("reduced from 14-bit to 10-bit", FFmWiz.bit_depth_precision_note(a))

    def test_12bit_forced_8bit_reduction_note(self):
        a = self._ans("yuv420p12le", 12, False)
        a["force_output_bit_depth"] = 8
        self.assertIn("reduced from 12-bit to 8-bit", FFmWiz.bit_depth_precision_note(a))

    # ---- terminal format of the software filter graph ----
    def test_cpu_filter_nvenc_10bit_ends_p010le(self):
        f = FFmWiz.build_cpu_video_filter(self._ans("yuv420p10le", 10, True))
        self.assertTrue(f.endswith("format=p010le"), f)

    def test_cpu_filter_libx265_10bit_ends_yuv420p10le(self):
        f = FFmWiz.build_cpu_video_filter(self._ans("yuv420p10le", 10, False))
        self.assertTrue(f.endswith("format=yuv420p10le"), f)

    def test_cpu_filter_nvenc_8bit_ends_yuv420p(self):
        f = FFmWiz.build_cpu_video_filter(self._ans("yuv420p", 8, True))
        self.assertTrue(f.endswith("format=yuv420p"), f)

    def test_cuda_filter_10bit_uses_scale_cuda_p010le(self):
        f = FFmWiz.build_cuda_video_filter(self._ans("yuv420p10le", 10, True))
        self.assertIn("format=p010le", f)
        self.assertNotIn("yuv420p10le", f)

    def test_cuda_filter_8bit_uses_nv12(self):
        f = FFmWiz.build_cuda_video_filter(self._ans("yuv420p", 8, True))
        self.assertIn("format=nv12", f)


class JoinMain10CommandTests(unittest.TestCase):
    """Join command generation: GPU Main10 must use p010le, CPU Main10
    yuv420p10le, and 8-bit NVENC must stay unchanged."""

    def setUp(self):
        FFmWiz.USE_COLOR = False

    def _join(self, tmp, use_gpu, depth=10, codec="H265", loudnorm=False):
        pix = "yuv420p10le" if depth >= 10 else "yuv420p"
        v = _vstream(pix, depth)
        a = audio_stream()
        def item(name):
            return {"path": Path(tmp) / name, "streams": [v, a], "video_streams": [v],
                    "audio_streams": [a], "format": {"duration": "5"}, "duration": 5.0}
        answers = {
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "input_path": Path(tmp) / "A.mov",
            "output_ext": "mp4", "video_codec": codec, "use_gpu": use_gpu,
            "video_streams": [v], "audio_streams": [a], "subtitle_streams": [],
            "data_streams": [], "attachment_streams": [], "audio_tracks": [0],
            "subtitle_tracks": [], "audio_codec": "aac", "audio_bitrate_kbps": 128,
            "resolution": "n", "fps": 30, "video_bitrate_kbps": 4000,
            "format": {"duration": "5"}, "color_range_choice": "tv",
            "join_input_items": [item("B.mov")],
        }
        if loudnorm:
            answers.update({"loudnorm_enabled": True, "loudnorm_mode": "single", "loudnorm_target_i": -16.0})
        items = [item("A.mov"), item("B.mov")]
        return answers, items

    def test_join_gpu_main10_uses_p010le_not_yuv420p10le(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self._join(tmp, use_gpu=True, depth=10)
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, Path(tmp) / "out.mp4"))
        self.assertIn("format=p010le", text)
        self.assertNotIn("format=yuv420p10le", text)
        self.assertIn("hevc_nvenc", text)
        self.assertIn("-profile:v main10", text)
        self.assertNotIn("-profile:v main ", text)

    def test_join_cpu_main10_uses_yuv420p10le(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self._join(tmp, use_gpu=False, depth=10)
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, Path(tmp) / "out.mp4"))
        self.assertIn("format=yuv420p10le", text)
        self.assertIn("-c:v libx265", text)
        self.assertIn("-profile:v main10", text)
        self.assertNotIn("format=p010le", text)

    def test_join_8bit_nvenc_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self._join(tmp, use_gpu=True, depth=8)
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, Path(tmp) / "out.mp4"))
        self.assertIn("format=yuv420p", text)
        self.assertNotIn("format=p010le", text)
        self.assertNotIn("main10", text)

    def test_join_gpu_main10_with_loudnorm_keeps_p010le(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self._join(tmp, use_gpu=True, depth=10, loudnorm=True)
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, Path(tmp) / "out.mp4"))
        self.assertIn("format=p010le", text)
        self.assertIn("loudnorm", text)
        self.assertIn("-profile:v main10", text)


class JoinSummaryEnhancementTests(unittest.TestCase):
    """Join input summary: distinct colors, total raw duration + frame count,
    and audio volume extremes."""

    def setUp(self):
        FFmWiz.USE_COLOR = False

    def _rows_answers(self, with_volume=True, with_duration=True):
        def item(name, vk, ak, fps, dur, mean, mx):
            v = {**_vstream(), "bit_rate": str(vk * 1000)}
            a = {**audio_stream(bit_rate=str(ak * 1000))}
            it = {"path": Path(name), "streams": [v, a], "video_streams": [v],
                  "audio_streams": [a], "format": {"duration": str(dur)} if with_duration else {},
                  "duration": float(dur) if with_duration else None}
            v["avg_frame_rate"] = f"{fps}/1"
            if with_volume:
                it["audio_volume_stats"] = {0: {"mean_volume": f"{mean} dB", "max_volume": f"{mx} dB"}}
            return it
        primary = item("A.mov", 8900, 175, 30, 600.0, "-19.8", "-0.6")
        extra1 = item("B.mov", 8499, 164, 30, 700.0, "-24.1", "-2.0")
        extra2 = item("C.mov", 8948, 170, 29, 0.0, "-20.0", "-0.3")
        answers = {
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "input_path": primary["path"],
            "format": primary["format"], "video_streams": primary["video_streams"],
            "audio_streams": primary["audio_streams"], "data_streams": [],
            "subtitle_streams": [], "attachment_streams": [], "duration": primary["duration"],
            "fps": 30, "audio_volume_stats": primary.get("audio_volume_stats"),
            "join_input_items": [extra1, extra2],
        }
        return answers

    def _capture_summary(self, answers):
        buf = io.StringIO()
        with mock.patch.object(FFmWiz, "get_packet_sizes", return_value={}):
            with contextlib.redirect_stdout(buf):
                FFmWiz.print_join_input_summary(answers)
        return buf.getvalue()

    def test_plain_lines_include_duration_and_volume(self):
        answers = self._rows_answers()
        with mock.patch.object(FFmWiz, "get_packet_sizes", return_value={}):
            lines = FFmWiz.format_join_input_summary_lines(answers)
        joined = "\n".join(lines)
        self.assertIn("Files selected: 3", joined)
        self.assertIn("Total raw duration:", joined)
        self.assertIn("Mean volume: lowest -24.1 dB (B.mov)", joined)
        self.assertIn("Max volume: highest -0.3 dB (C.mov)", joined)

    def test_total_raw_duration_and_frame_count(self):
        answers = self._rows_answers()
        with mock.patch.object(FFmWiz, "get_packet_sizes", return_value={}):
            rows = FFmWiz.join_input_media_stats(answers)
            info = FFmWiz.join_summary_total_duration(answers, rows)
        # One file (C.mov) has unknown duration -> 600 + 700 known.
        self.assertEqual(info["unknown_count"], 1)
        self.assertAlmostEqual(info["total_seconds"], 1300.0)
        # Output fps is set -> frames at selected output fps.
        self.assertIsNotNone(info["frames"])
        self.assertIn("selected output", info["frame_basis"])

    def test_duration_handles_all_unknown(self):
        answers = self._rows_answers(with_duration=False)
        with mock.patch.object(FFmWiz, "get_packet_sizes", return_value={}):
            rows = FFmWiz.join_input_media_stats(answers)
            info = FFmWiz.join_summary_total_duration(answers, rows)
        self.assertEqual(info["known_count"], 0)
        self.assertEqual(FFmWiz.join_summary_duration_text(info), "unavailable")

    def test_volume_extremes_unavailable_clean(self):
        answers = self._rows_answers(with_volume=False)
        with mock.patch.object(FFmWiz, "get_packet_sizes", return_value={}):
            rows = FFmWiz.join_input_media_stats(answers)
            self.assertIsNone(FFmWiz.join_summary_volume_extremes(rows))

    def test_summary_colors_highest_differs_from_lowest(self):
        answers = self._rows_answers()
        FFmWiz.USE_COLOR = True
        try:
            out = self._capture_summary(answers)
        finally:
            FFmWiz.USE_COLOR = False
        # highest and lowest must use different color categories.
        self.assertIn(FFmWiz.Color.JOIN_HIGH, out)
        self.assertIn(FFmWiz.Color.JOIN_LOW, out)
        self.assertNotEqual(FFmWiz.Color.JOIN_HIGH, FFmWiz.Color.JOIN_LOW)
        # file names use a distinct color.
        self.assertIn(FFmWiz.Color.JOIN_FILE, out)
        # volume extremes use distinct colors.
        self.assertIn(FFmWiz.Color.JOIN_VOL_LOW, out)
        self.assertIn(FFmWiz.Color.JOIN_VOL_HIGH, out)

    def test_summary_does_not_print_full_paths(self):
        answers = self._rows_answers()
        out = self._capture_summary(answers)
        # Only base names, never directory separators from the item paths.
        self.assertIn("A.mov", out)
        self.assertNotIn("/A.mov", out)
        self.assertNotIn("\\A.mov", out)


class LoudnormHighlightTests(unittest.TestCase):
    """Integrated loudness must be emphasized (bold + green)."""

    def setUp(self):
        FFmWiz.USE_COLOR = False

    STATS = {"input_i": -21.4, "input_tp": -0.7, "input_lra": 8.0,
             "input_thresh": -32.0, "target_offset": -0.4}

    def test_integrated_loudness_label_emerald_bold_value_original(self):
        FFmWiz.USE_COLOR = True
        try:
            line = FFmWiz.format_integrated_loudness_line(self.STATS)
        finally:
            FFmWiz.USE_COLOR = False
        # Label: bold + a distinct (emerald) green.
        self.assertIn(FFmWiz.Color.MUX_EMERALD, line)
        self.assertIn(FFmWiz.Color.BOLD, line)
        # Value keeps its original MEAN_VOLUME color (not recolored green).
        self.assertIn(FFmWiz.Color.MEAN_VOLUME, line)
        self.assertIn("-21.4 LUFS", line)

    def test_other_lines_not_emerald_bold(self):
        FFmWiz.USE_COLOR = True
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                FFmWiz.print_loudnorm_stats(self.STATS)
            out = buf.getvalue()
        finally:
            FFmWiz.USE_COLOR = False
        # Only the integrated-loudness label is emerald; true-peak is not.
        true_peak_line = [ln for ln in out.splitlines() if "True peak" in ln][0]
        self.assertNotIn(FFmWiz.Color.MUX_EMERALD, true_peak_line)


class JoinVolumeScanTests(unittest.TestCase):
    """ensure_join_volume_stats populates volume extremes via a (mocked) scan."""

    def setUp(self):
        FFmWiz.USE_COLOR = False

    def _answers(self, tmp):
        v = _vstream()
        a = audio_stream()
        def item(name):
            return {"path": Path(tmp) / name, "streams": [v, a], "video_streams": [v],
                    "audio_streams": [a], "format": {"duration": "5"}, "duration": 5.0}
        return {
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "input_path": Path(tmp) / "A.mov",
            "format": {"duration": "5"}, "video_streams": [v], "audio_streams": [a],
            "data_streams": [], "subtitle_streams": [], "attachment_streams": [], "duration": 5.0,
            "fps": 30, "join_input_items": [item("B.mov"), item("C.mov")],
        }

    def test_scan_populates_volume_then_summary_shows_extremes(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._answers(tmp)
            fake = {
                Path(tmp) / "A.mov": {"mean_volume": "-19.8 dB", "max_volume": "-0.6 dB"},
                Path(tmp) / "B.mov": {"mean_volume": "-24.1 dB", "max_volume": "-2.0 dB"},
                Path(tmp) / "C.mov": {"mean_volume": "-20.0 dB", "max_volume": "-0.3 dB"},
            }
            def fake_probe(ffmpeg, path, idx):
                return fake[Path(path)]
            with mock.patch.object(FFmWiz, "probe_audio_volume_stats", side_effect=fake_probe), \
                 mock.patch.object(FFmWiz, "get_packet_sizes", return_value={}):
                FFmWiz.ensure_join_volume_stats(answers)
                # Primary cached on answers; extras cached on their item dicts.
                self.assertEqual(answers["audio_volume_stats"][0]["mean_volume"], "-19.8 dB")
                self.assertIn("audio_volume_stats", answers["join_input_items"][0])
                lines = FFmWiz.format_join_input_summary_lines(answers)
        joined = "\n".join(lines)
        self.assertIn("Mean volume: lowest -24.1 dB (B.mov)", joined)
        self.assertIn("Max volume: highest -0.3 dB (C.mov)", joined)

    def test_scan_skips_when_already_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._answers(tmp)
            answers["audio_volume_stats"] = {0: {"mean_volume": "-10 dB", "max_volume": "-1 dB"}}
            for it in answers["join_input_items"]:
                it["audio_volume_stats"] = {0: {"mean_volume": "-10 dB", "max_volume": "-1 dB"}}
            with mock.patch.object(FFmWiz, "probe_audio_volume_stats",
                                   side_effect=AssertionError("should not probe")) as probe:
                FFmWiz.ensure_join_volume_stats(answers)
                probe.assert_not_called()


class SmoothedEtaTests(unittest.TestCase):
    """ETA rate smoothing: stable against FFmpeg's jumpy per-tick speed."""

    def test_first_sample_seeds_overall_average(self):
        state = {}
        rate = FFmWiz._smoothed_eta_rate(state, current_s=100.0, elapsed=10.0)
        self.assertAlmostEqual(rate, 10.0, places=3)  # 100/10

    def test_jumpy_samples_are_smoothed(self):
        # Feed a steady ~10x rate, then a single huge spike; smoothed rate must
        # not jump to the spike (EMA + overall-average blend dampens it).
        state = {}
        FFmWiz._smoothed_eta_rate(state, 100.0, 10.0)   # seed: 10/s
        FFmWiz._smoothed_eta_rate(state, 200.0, 20.0)   # +100 in 10s -> 10/s
        # Spike: +500 media-seconds in 10s wall (50/s) for one sample.
        smoothed = FFmWiz._smoothed_eta_rate(state, 700.0, 30.0)
        instantaneous = 500.0 / 10.0  # 50/s
        self.assertLess(smoothed, instantaneous)
        # And it stays in a sensible band (well below the raw spike).
        self.assertLess(smoothed, 30.0)

    def test_idle_rerender_does_not_corrupt_rate(self):
        state = {}
        FFmWiz._smoothed_eta_rate(state, 100.0, 10.0)
        FFmWiz._smoothed_eta_rate(state, 200.0, 20.0)
        before = float(state["_ffmwiz_eta_rate_ema"])
        # Idle re-render: no media progress, tiny elapsed delta -> no EMA update.
        FFmWiz._smoothed_eta_rate(state, 200.0, 20.2)
        after = float(state["_ffmwiz_eta_rate_ema"])
        self.assertEqual(before, after)


class AudioTrackSelectionTests(unittest.TestCase):
    """Single audio track is auto-selected (with a note); multiple tracks are
    listed and chosen by the user."""

    def setUp(self):
        FFmWiz.USE_COLOR = False

    def test_single_track_auto_selects_with_note(self):
        answers = {"audio_streams": [audio_stream()], "format": {"duration": "10"}}
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=AssertionError("must not prompt")) as ask:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                FFmWiz.step_audio_track_for_tool(answers)
            ask.assert_not_called()
        self.assertEqual(answers["audio_index"], 0)
        self.assertIn("Only one audio track", buf.getvalue())

    def test_multi_track_selects_chosen_one_based(self):
        answers = {"audio_streams": [audio_stream(), audio_stream()], "format": {"duration": "10"}}
        with mock.patch.object(FFmWiz, "ask_raw", return_value="2"), \
             mock.patch.object(FFmWiz, "get_packet_sizes", return_value={}), \
             mock.patch.object(FFmWiz, "get_audio_volume_stats", return_value={}):
            with contextlib.redirect_stdout(io.StringIO()):
                FFmWiz.step_audio_track_for_tool(answers)
        self.assertEqual(answers["audio_index"], 1)  # "2" -> index 1


class LosslessSplitExtTests(unittest.TestCase):
    """Lossless audio split lets the user choose a copy-compatible extension."""

    def setUp(self):
        FFmWiz.USE_COLOR = False

    def test_copy_ext_choices_per_codec(self):
        self.assertEqual(FFmWiz.lossless_audio_copy_ext_choices("aac")[0], "m4a")
        self.assertIn("aac", FFmWiz.lossless_audio_copy_ext_choices("aac"))
        self.assertIn("mka", FFmWiz.lossless_audio_copy_ext_choices("aac"))
        self.assertEqual(FFmWiz.lossless_audio_copy_ext_choices("flac")[0], "flac")
        self.assertEqual(FFmWiz.lossless_audio_copy_ext_choices("opus")[0], "opus")
        # Unknown codec -> universal containers.
        self.assertEqual(FFmWiz.lossless_audio_copy_ext_choices("weird"), ["mka", "mov"])

    def test_ask_uses_chosen_ext_and_lists_copy_options(self):
        answers = {"input_path": Path("clip.mp4"),
                   "audio_streams": [{"codec_type": "audio", "codec_name": "aac"}], "audio_index": 0}
        buf = io.StringIO()
        with mock.patch.object(FFmWiz, "ask_raw", return_value="aac"):
            with contextlib.redirect_stdout(buf):
                ext = FFmWiz.ask_lossless_split_ext(answers, "aac")
        self.assertEqual(ext, "aac")
        self.assertEqual(answers["lossless_split_ext"], "aac")

    def test_ask_default_is_preferred_container(self):
        answers = {"input_path": Path("clip.mp4"),
                   "audio_streams": [{"codec_type": "audio", "codec_name": "aac"}], "audio_index": 0}
        with mock.patch.object(FFmWiz, "ask_raw", return_value=""):
            with contextlib.redirect_stdout(io.StringIO()):
                ext = FFmWiz.ask_lossless_split_ext(answers, "aac")
        self.assertEqual(ext, "m4a")  # Enter -> preferred default

    def test_chosen_ext_used_in_split_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = {"ffmpeg": "ffmpeg", "input_path": Path(tmp) / "clip.mp4",
                       "output_location": Path(tmp), "audio_index": 0,
                       "audio_streams": [{"codec_type": "audio", "codec_name": "aac"}],
                       "lossless_split_ext": "aac"}
            cmd, pattern = FFmWiz.build_lossless_split_command(answers, [600.0])
        self.assertIn("_part%03d.aac", str(pattern))
        self.assertEqual(cmd[cmd.index("-map") + 1], "0:a:0")


class TrackManagerBackTests(unittest.TestCase):
    """Back ('0') in the single-file Track Manager goes ONE step back instead
    of cancelling the whole mode."""

    def setUp(self):
        FFmWiz.USE_COLOR = False

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
        with mock.patch.object(FFmWiz, "ask_track_manager_source", lambda a: None), \
             mock.patch.object(FFmWiz, "print_source_info", lambda a: None), \
             mock.patch.object(FFmWiz, "print_track_list", lambda a: None), \
             mock.patch.object(FFmWiz, "ask_track_remove_specs", lambda a, c: []), \
             mock.patch.object(FFmWiz, "_track_manager_collect_externals", side_effect=fake_ext), \
             mock.patch.object(FFmWiz, "ask_yes_no", return_value=True), \
             mock.patch.object(FFmWiz, "_track_manager_ask_loudnorm", side_effect=fake_loud):
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
        with mock.patch.object(FFmWiz, "ask_track_manager_source", lambda a: None), \
             mock.patch.object(FFmWiz, "print_source_info", lambda a: None), \
             mock.patch.object(FFmWiz, "print_track_list", lambda a: None), \
             mock.patch.object(FFmWiz, "ask_track_remove_specs", side_effect=fake_remove), \
             mock.patch.object(FFmWiz, "_track_manager_collect_externals", side_effect=fake_ext), \
             mock.patch.object(FFmWiz, "ask_yes_no", return_value=True), \
             mock.patch.object(FFmWiz, "_track_manager_ask_loudnorm", lambda a: None):
            with contextlib.redirect_stdout(io.StringIO()):
                result = FFmWiz._run_track_manager_single(answers)
        self.assertIsNone(result)
        self.assertEqual(len(remove_calls), 2)  # went back to remove
        self.assertEqual(len(ext_calls), 2)


if __name__ == "__main__":
    unittest.main()
