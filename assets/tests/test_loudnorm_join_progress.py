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
            captured_prompts = []

            def fake_ask_raw(prompt):
                captured_prompts.append(prompt)
                return ["3", "-16"][len(captured_prompts) - 1] if len(captured_prompts) <= 2 else ""

            with mock.patch.object(FFmWiz, "ask_raw", side_effect=fake_ask_raw), \
                 mock.patch.object(FFmWiz, "probe_join_loudnorm_measurement", return_value=measured), \
                 mock.patch.object(FFmWiz, "print_loudnorm_stats"):
                FFmWiz.step_loudnorm(answers)
            self.assertEqual(answers["loudnorm_mode"], "two_pass")
            self.assertEqual(answers["loudnorm_measured"], measured)
            joined_prompts = " ".join(captured_prompts)
            self.assertNotIn("Measure current audio loudness", joined_prompts)
            self.assertNotIn("Increase / normalize audio loudness", joined_prompts)

    def test_loudnorm_two_pass_parse_failure_can_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, _ = self.join_answers(tmp)
            with mock.patch.object(FFmWiz, "ask_raw", side_effect=["3", "-16", "m"]), \
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
            answers = {"ffmpeg": "ffmpeg", "input_path": Path(tmp) / "clip.aac",
                       "output_location": Path(tmp)}
            cmd, pattern = FFmWiz.build_lossless_split_command(answers, [600.0, 1200.0, 1500.0])
            self.assertIn("-c", cmd)
            self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
            self.assertIn("-f", cmd)
            self.assertEqual(cmd[cmd.index("-f") + 1], "segment")
            self.assertEqual(cmd[cmd.index("-segment_times") + 1], "600.000000,1200.000000,1500.000000")
            self.assertEqual(cmd[cmd.index("-map") + 1], "0")
            self.assertIn("_part%03d.aac", str(pattern))

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


if __name__ == "__main__":
    unittest.main()
