import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz


class CommandGenerationTests(unittest.TestCase):
    def setUp(self):
        FFmWiz.USE_COLOR = False

    def base_answers(self, output_dir: str) -> dict:
        return {
            "ffmpeg": "ffmpeg",
            "ffprobe": "ffprobe",
            "input_path": Path("Pato12.mkv"),
            "output_location": Path(output_dir),
            "output_ext": "mp4",
            "video_streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 3440,
                    "height": 1440,
                    "avg_frame_rate": "30/1",
                    "color_range": "tv",
                }
            ],
            "audio_streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "bit_rate": "122000",
                    "duration": "6074.221",
                }
            ],
            "subtitle_streams": [],
            "data_streams": [],
            "audio_tracks": [0],
            "subtitle_tracks": [],
            "video_codec": "H265",
            "use_gpu": True,
            "crop_enabled": True,
            "crop_top": 172,
            "crop_left": 429,
            "crop_right": 1095,
            "crop_bottom": 189,
            "video_bitrate_kbps": 400,
            "video_bitrate_mode": "quality_vbr",
            "audio_codec": "aac",
            "audio_bitrate_kbps": 122,
            "resolution": FFmWiz.parse_resolution("480p"),
            "fps": 4,
            "format": {"duration": "6074.221"},
        }

    def _ensure_test_color_range(self, answers: dict) -> None:
        """Synthetic test sources frequently omit color_range. Real completed
        wizard/job state always resolves the range before reaching a builder
        (detected source range, or an explicit wizard/batch choice), so default
        an otherwise-unresolved fixture to a TV/Limited assumption. Tests that
        exercise color-range behavior set their own stream range or
        color_range_choice and are therefore left untouched."""
        stream = (answers.get("video_streams") or [{}])[0]
        has_range = FFmWiz.normalize_color_range(stream.get("color_range")) in {"tv", "pc"}
        if not has_range and not str(answers.get("color_range_choice") or "").strip():
            answers["color_range_choice"] = "tv"

    def command_for(self, answers: dict) -> list[str]:
        self._ensure_test_color_range(answers)
        return FFmWiz.build_ffmpeg_command(answers)

    def command_text(self, answers: dict) -> str:
        return " ".join(self.command_for(answers))

    def assert_not_contains_any(self, text: str, needles: list[str]) -> None:
        for needle in needles:
            self.assertNotIn(needle, text)

    def test_main_menu_routes_metadata_editor_mode(self):
        with mock.patch.object(FFmWiz, "ask_main_menu", return_value=13), \
                mock.patch.object(FFmWiz, "run_metadata_editor_mode", return_value=None) as run_metadata:
            result = FFmWiz.run_one_job({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}, Path("config.json"))
        self.assertIsNone(result)
        run_metadata.assert_called_once()

    def test_format_bytes_keeps_media_sizes_in_mb(self):
        self.assertEqual(FFmWiz.format_bytes(500), "500 B")
        self.assertEqual(FFmWiz.format_bytes(1536), "1.5 KB")
        self.assertEqual(FFmWiz.format_bytes(1_073_741_824), "1024.0 MB")
        self.assertEqual(FFmWiz.format_bytes(1_118_000_000), "1066.2 MB")

    def test_parse_volumedetect_output(self):
        text = """
        [Parsed_volumedetect_0 @ 000001] mean_volume: -23.4 dB
        [Parsed_volumedetect_0 @ 000001] max_volume: -1.2 dB
        """
        self.assertEqual(
            FFmWiz.parse_volumedetect_output(text),
            {"mean_volume": "-23.4 dB", "max_volume": "-1.2 dB"},
        )

    def test_extract_stream_audio_uses_global_stream_index_and_copy(self):
        stream = {"index": 2, "codec_type": "audio", "codec_name": "aac"}
        output = Path("episode_stream2_audio.aac")
        cmd = FFmWiz.build_extract_stream_command("ffmpeg", Path("episode.mkv"), stream, output)
        text = " ".join(str(part) for part in cmd)
        self.assertIn("-map 0:2", text)
        self.assertIn("-vn -sn -dn", text)
        self.assertIn("-c copy", text)
        self.assertEqual(cmd[-1], str(output))

    def test_extract_stream_mov_text_exports_srt(self):
        stream = {"index": 3, "codec_type": "subtitle", "codec_name": "mov_text"}
        self.assertEqual(FFmWiz.extract_stream_default_extension(stream), ".srt")
        cmd = FFmWiz.build_extract_stream_command("ffmpeg", Path("episode.mp4"), stream, Path("episode_stream3_subtitle.srt"))
        text = " ".join(str(part) for part in cmd)
        self.assertIn("-map 0:3", text)
        self.assertIn("-vn -an -dn", text)
        self.assertIn("-c:s srt", text)

    def test_extract_stream_candidates_use_ffprobe_indexes(self):
        answers = {
            "probe": {
                "streams": [
                    {"index": 4, "codec_type": "subtitle", "codec_name": "ass"},
                    {"index": 0, "codec_type": "video", "codec_name": "h264"},
                    {"index": 2, "codec_type": "audio", "codec_name": "aac"},
                    {"index": 5, "codec_type": "attachment", "codec_name": "ttf"},
                ]
            }
        }
        indexes = [stream["index"] for stream in FFmWiz.extract_stream_candidates(answers)]
        self.assertEqual(indexes, [0, 2, 4])

    def test_extract_stream_description_uses_packet_bitrate_when_metadata_is_missing(self):
        stream = {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "duration": "10.0",
            "channels": 2,
            "sample_rate": "48000",
        }
        answers = {
            "format": {"duration": "10.0"},
            "packet_sizes": {1: 1_000_000},
        }
        description = FFmWiz.extract_stream_description(stream, answers)
        self.assertIn("bitrate: 800 kbps", description)
        self.assertNotIn("bitrate: unknown", description)

    def test_stream_metrics_reject_stale_tags_and_prefer_packet_size(self):
        fmt = {"duration": "309.392", "size": str(62_500_000), "bit_rate": "1693000"}
        stream = {
            "index": 0,
            "codec_type": "video",
            "codec_name": "hevc",
            "duration": "309.392",
            "tags": {
                "BPS": "25906000",
                "NUMBER_OF_BYTES": str(955_700_000),
            },
        }
        self.assertTrue(FFmWiz.packet_size_probe_needed({"format": fmt, "video_streams": [stream], "audio_streams": []}))
        size, estimated = FFmWiz.stream_size_bytes(stream, fmt, {0: 17_400_000})
        self.assertFalse(estimated)
        self.assertEqual(size, 17_400_000)
        self.assertEqual(FFmWiz.stream_bitrate_kbps(stream, fmt, {0: 17_400_000}), 450)
        self.assertIsNone(FFmWiz.bitrate_kbps(stream, fmt))

    def test_stream_metrics_reject_sibling_stale_matroska_statistics(self):
        fmt = {"duration": "309.392", "size": str(62_500_000), "bit_rate": "1693000"}
        video = {
            "index": 0,
            "codec_type": "video",
            "codec_name": "hevc",
            "duration": "309.392",
            "tags": {
                "BPS": "25905716",
                "NUMBER_OF_BYTES": "1002149696",
            },
        }
        audio = {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "opus",
            "duration": "309.392",
            "tags": {
                "BPS": "1221468",
                "NUMBER_OF_BYTES": "47251754",
            },
        }
        streams = [video, audio]
        answers = {"format": fmt, "video_streams": [video], "audio_streams": [audio]}
        self.assertTrue(FFmWiz.packet_size_probe_needed(answers))
        self.assertEqual(FFmWiz.stream_size_bytes(audio, fmt, None, streams), (None, True))
        self.assertIsNone(FFmWiz.stream_bitrate_kbps(audio, fmt, None, streams))
        size, estimated = FFmWiz.stream_size_bytes(audio, fmt, {1: 6_200_000}, streams)
        self.assertFalse(estimated)
        self.assertEqual(size, 6_200_000)
        self.assertEqual(FFmWiz.stream_bitrate_kbps(audio, fmt, {1: 6_200_000}, streams), 160)

    def test_reencoded_outputs_clear_copied_stream_statistics_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["output_ext"] = "mkv"
            answers["keep_source_metadata"] = True
            answers["audio_codec"] = "libopus"
            answers["audio_bitrate_kbps"] = 160
            text = self.command_text(answers)
        self.assertIn("-map_metadata 0", text)
        self.assertIn("-metadata:s:v BPS=", text)
        self.assertIn("-metadata:s:v NUMBER_OF_BYTES=", text)
        self.assertIn("-metadata:s:a BPS=", text)
        self.assertIn("-metadata:s:a NUMBER_OF_BYTES=", text)
        self.assertNotIn("-metadata:s:a:0 BPS=", text)

    def test_reencoded_outputs_clear_statistics_for_all_audio_and_subtitle_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["output_ext"] = "mkv"
            answers["keep_source_metadata"] = True
            answers["audio_codec"] = "libopus"
            answers["audio_bitrate_kbps"] = 160
            answers["audio_tracks"] = "all"
            answers["audio_streams"] = [
                {"codec_type": "audio", "codec_name": "aac", "duration": "60", "index": 1},
                {"codec_type": "audio", "codec_name": "aac", "duration": "60", "index": 2},
                {"codec_type": "audio", "codec_name": "aac", "duration": "60", "index": 3},
            ]
            answers["subtitle_tracks"] = "all"
            answers["subtitle_streams"] = [
                {"codec_type": "subtitle", "codec_name": "ass", "index": 4},
                {"codec_type": "subtitle", "codec_name": "subrip", "index": 5},
            ]
            text = self.command_text(answers)
        for spec in ("a", "s"):
            self.assertIn(f"-metadata:s:{spec} BPS=", text)
            self.assertIn(f"-metadata:s:{spec} NUMBER_OF_BYTES=", text)
        self.assertNotIn("-metadata:s:a:0 BPS=", text)
        self.assertNotIn("-metadata:s:s:0 BPS=", text)

    def test_main_encode_keeps_mkv_embedded_attachments_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["output_ext"] = "mkv"
            answers["attachment_streams"] = [
                {"index": 4, "codec_type": "attachment", "codec_name": "ttf", "tags": {"filename": "Font.ttf"}},
            ]
            answers["keep_embedded_attachments"] = True
            text = self.command_text(answers)
            self.assertIn("-map 0:t?", text)
            self.assertIn("-c:t copy", text)

    def test_main_encode_drops_embedded_attachments_when_not_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["output_ext"] = "mkv"
            answers["attachment_streams"] = [
                {"index": 4, "codec_type": "attachment", "codec_name": "ttf", "tags": {"filename": "Font.ttf"}},
            ]
            answers["keep_embedded_attachments"] = False
            text = self.command_text(answers)
            self.assertNotIn("0:t?", text)
            self.assertNotIn("-c:t copy", text)

    def test_main_encode_does_not_map_attachments_for_mp4_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["output_ext"] = "mp4"
            answers["attachment_streams"] = [
                {"index": 4, "codec_type": "attachment", "codec_name": "ttf", "tags": {"filename": "Font.ttf"}},
            ]
            answers["keep_embedded_attachments"] = True
            text = self.command_text(answers)
            self.assertNotIn("0:t?", text)
            self.assertNotIn("-c:t copy", text)

    def test_parse_loudnorm_measurement_output(self):
        text = """
        [Parsed_loudnorm_0 @ 000001]
        {
          "input_i" : "-23.40",
          "input_tp" : "-5.10",
          "input_lra" : "4.20",
          "input_thresh" : "-33.90",
          "target_offset" : "-0.30"
        }
        """
        self.assertEqual(
            FFmWiz.parse_loudnorm_measurement_output(text),
            {
                "input_i": -23.4,
                "input_tp": -5.1,
                "input_lra": 4.2,
                "input_thresh": -33.9,
                "target_offset": -0.3,
            },
        )

    def test_progress_line_omits_unavailable_fps_and_q(self):
        state = {
            "out_time_us": "5551000000",
            "out_time_ms": "5551000000",
            "out_time": "01:32:31.000000",
            "speed": "32.7x",
            "progress": "end",
            "total_size": "N/A",
            "bitrate": "N/A",
            "_ffmwiz_size_text": "549.1 MB",
            "_ffmwiz_bitrate_text": "829.9kbits/s",
        }
        line = FFmWiz._strip_ansi(FFmWiz._render_progress_line(state, 5551, FFmWiz.time.perf_counter() - 10))
        self.assertNotIn("fps N/A", line)
        self.assertNotIn("q N/A", line)
        self.assertIn("size 549.1 MB", line)

    def test_progress_line_omits_unavailable_size_and_bitrate_without_override(self):
        state = {
            "out_time": "00:06:54.000000",
            "speed": "28.7x",
            "total_size": "N/A",
            "bitrate": "N/A",
            "progress": "continue",
        }
        line = FFmWiz._strip_ansi(FFmWiz._render_progress_line(state, 5512, FFmWiz.time.perf_counter() - 15))
        self.assertNotIn("size N/A", line)
        self.assertNotIn("bitrate N/A", line)
        self.assertIn("speed 28.7x", line)

    def test_progress_line_omits_nvenc_q_sentinel(self):
        state = {
            "out_time_us": "1000000",
            "speed": "12x",
            "stream_0_0_q": "-1.0",
            "progress": "continue",
        }
        line = FFmWiz._strip_ansi(FFmWiz._render_progress_line(state, 60.0, FFmWiz.time.perf_counter() - 1))
        self.assertNotIn("q -1.0", line)

    def test_progress_line_prefers_polled_output_file_size(self):
        state = {
            "out_time_us": "30000000",
            "speed": "1.56x",
            "total_size": "1024",
            "bitrate": "50.0kbits/s",
            "_ffmwiz_size_text": "5.2 MB",
            "_ffmwiz_size_source": "file",
            "_ffmwiz_bitrate_text": "1478.9kbits/s",
            "progress": "continue",
        }
        line = FFmWiz._strip_ansi(FFmWiz._render_progress_line(state, 309.0, FFmWiz.time.perf_counter() - 19))
        self.assertIn("size 5.2 MB", line)
        self.assertIn("bitrate 1478.9kbits/s", line)
        self.assertNotIn("size 1.0 KB", line)
        self.assertNotIn("bitrate 50.0kbits/s", line)

    def test_progress_size_estimates_between_output_file_flushes(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "encoded.mkv"
            output.write_bytes(b"x" * 1_000_000)
            state = {
                "progress": "continue",
                "_ffmwiz_target_bitrate_kbps": "1600",
                "_ffmwiz_last_file_size_bytes": "1000000",
                "_ffmwiz_last_file_size_seconds": "10.000000",
            }
            changed = FFmWiz._apply_output_file_size_progress(state, [output], 12.0)
            self.assertTrue(changed)
            self.assertEqual(state["_ffmwiz_size_source"], "estimated-file")
            self.assertIn("1.3 MB", state["_ffmwiz_size_text"])
            line = FFmWiz._strip_ansi(FFmWiz._render_progress_line(state, 60.0, FFmWiz.time.perf_counter() - 4))
            self.assertIn("size 1.3 MB", line)

    def test_progress_target_mux_bitrate_uses_first_video_and_audio_bitrates(self):
        cmd = [
            "ffmpeg",
            "-i",
            "in.mkv",
            "-b:v:0",
            "1650k",
            "-b:a",
            "160k",
            "out.mkv",
            "-b:v:0",
            "1650k",
            "-b:a",
            "160k",
            "out2.mkv",
        ]
        self.assertAlmostEqual(FFmWiz._progress_target_mux_bitrate_kbps_from_command(cmd), 1810.0)

    def test_compact_ffmpeg_progress_state_logs_one_line(self):
        state = {
            "frame": "7420",
            "fps": "38.31",
            "stream_0_0_q": "20.0",
            "bitrate": "1693.8kbits/s",
            "total_size": "65507886",
            "out_time": "00:05:09.392417",
            "speed": "1.6x",
            "progress": "end",
            "_ffmwiz_size_text": "62.5 MB",
        }
        line = FFmWiz._compact_ffmpeg_progress_state(state)
        self.assertEqual(line.count("\n"), 0)
        self.assertIn("frame=7420, fps=38.31", line)
        self.assertIn("total_size=65507886", line)
        self.assertIn("progress=end", line)
        self.assertNotIn("_ffmwiz_size_text", line)

    def test_progress_output_paths_inferred_from_simple_command(self):
        output = Path("encoded.mp4")
        cmd = ["ffmpeg", "-y", "-i", "input.mkv", "-c:v", "libx264", str(output)]
        self.assertEqual(FFmWiz._progress_output_paths_from_command(cmd), [output])

    def test_split_progress_uses_aggregate_frame_time_without_double_counting(self):
        state = {
            "frame": "60",
            "fps": "58.72",
            "stream_0_0_q": "10.0",
            "bitrate": "419.5kbits/s",
            "total_size": "786432",
            "out_time_us": "15000000",
            "out_time_ms": "15000000",
            "out_time": "00:00:15.000000",
            "speed": "0.413x",
            "progress": "continue",
        }
        raw_seconds = FFmWiz._progress_raw_seconds_from_state(state)
        current_seconds = max(raw_seconds, float(state["frame"]) / 4.0)
        state["_ffmwiz_current_s"] = str(current_seconds)
        state["_ffmwiz_prefer_elapsed_speed"] = "1"
        state["_ffmwiz_speed_text"] = "15x"
        state["_ffmwiz_size_text"] = "768.0 KB"
        state["_ffmwiz_bitrate_text"] = f"{int(state['total_size']) * 8 / 1000 / current_seconds:.1f}kbits/s"
        line = FFmWiz._strip_ansi(FFmWiz._render_progress_line(state, 60.0, FFmWiz.time.perf_counter() - 2))
        self.assertIn("25.0%", line)
        self.assertIn("time 00:00:15 / 00:01:00", line)
        self.assertIn("speed 15x", line)
        self.assertIn("size 768.0 KB", line)
        self.assertIn("bitrate 419.4kbits/s", line)
        self.assertNotIn("bitrate 419.5kbits/s", line)

    def test_split_progress_advances_after_first_output_part(self):
        current_seconds, active_part = FFmWiz._split_progress_seconds(
            raw_current_s=6.16,
            frame_seconds=10.0,
            part_durations=[10.0, 10.0],
            output_sizes=[786480, 120000],
            previous_output_sizes=[786480, 0],
            active_part=0,
            previous_raw_s=0.0,
        )
        self.assertEqual(active_part, 1)
        self.assertAlmostEqual(current_seconds, 16.16, places=2)

    def test_split_progress_detects_part_handoff_before_file_size_flush(self):
        current_seconds, active_part = FFmWiz._split_progress_seconds(
            raw_current_s=1.10,
            frame_seconds=10.0,
            part_durations=[10.0, 10.0],
            output_sizes=[786480, 0],
            previous_output_sizes=[786480, 0],
            active_part=0,
            previous_raw_s=0.0,
        )
        self.assertEqual(active_part, 1)
        self.assertAlmostEqual(current_seconds, 11.10, places=2)

    def test_split_progress_keeps_first_part_when_it_is_still_growing(self):
        current_seconds, active_part = FFmWiz._split_progress_seconds(
            raw_current_s=9.70,
            frame_seconds=10.0,
            part_durations=[10.0, 10.0],
            output_sizes=[900000, 0],
            previous_output_sizes=[786480, 0],
            active_part=0,
            previous_raw_s=9.4,
        )
        self.assertEqual(active_part, 0)
        self.assertAlmostEqual(current_seconds, 10.0, places=2)

    def test_split_progress_does_not_move_active_part_backwards(self):
        current_seconds, active_part = FFmWiz._split_progress_seconds(
            raw_current_s=7.0,
            frame_seconds=10.0,
            part_durations=[10.0, 10.0],
            output_sizes=[900000, 120000],
            previous_output_sizes=[786480, 120000],
            active_part=1,
            previous_raw_s=6.0,
        )
        self.assertEqual(active_part, 1)
        self.assertAlmostEqual(current_seconds, 17.0, places=2)

    def test_loudnorm_filter_uses_two_pass_values_when_available(self):
        # Use values where linear mode is feasible:
        # gain = -16 - (-18) = +2 dB; predicted_TP = -4.0 + 2 = -2.0 <= -1.5 target TP.
        answers = {
            "loudnorm_enabled": True,
            "loudnorm_target_i": -16.0,
            "loudnorm_measured": {
                "input_i": -18.0,
                "input_tp": -4.0,
                "input_lra": 4.2,
                "input_thresh": -28.5,
                "target_offset": -0.3,
            },
        }
        filt = FFmWiz.build_loudnorm_filter(answers)
        self.assertIn("loudnorm=I=-16:TP=-1.5:LRA=11", filt)
        self.assertIn("measured_I=-18", filt)
        self.assertIn("linear=true", filt)

    def test_loudnorm_filter_falls_back_to_single_pass(self):
        filt = FFmWiz.build_loudnorm_filter({"loudnorm_enabled": True, "loudnorm_target_i": -18})
        self.assertEqual(filt, "loudnorm=I=-18:TP=-1.5:LRA=11:print_format=summary")

    def test_media_info_report_includes_audio_volume_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.mkv"
            input_path.write_bytes(b"dummy")
            payload = {
                "format": {"duration": "10", "size": str(len(b"dummy"))},
                "streams": [
                    {"codec_type": "audio", "codec_name": "aac", "index": 1},
                ],
            }
            lines = FFmWiz.build_media_info_report_lines(
                input_path,
                payload,
                "",
                Path(tmp) / "input_info.txt",
                {0: {"mean_volume": "-20.0 dB", "max_volume": "-1.0 dB"}},
            )
            plain = FFmWiz.render_info_report(lines, color=False)
            self.assertIn("mean / max volume: -20.0 / -1.0 dB", plain)

    def test_media_info_report_includes_enhanced_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.mkv"
            input_path.write_bytes(b"dummy")
            payload = {
                "format": {"duration": "10", "size": str(len(b"dummy")), "bit_rate": "1000000"},
                "streams": [
                    {
                        "index": 0,
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 1920,
                        "height": 1080,
                        "avg_frame_rate": "24/1",
                        "bit_rate": "900000",
                        "color_range": "tv",
                    },
                    {"index": 1, "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
                ],
                "chapters": [],
            }
            analysis = {
                "packet_sizes": {0: 1_000_000, 1: 100_000},
                "stream_size_rows": FFmWiz.media_info_stream_size_rows(input_path, payload, {0: 1_000_000, 1: 100_000}),
                "bpppf_rows": FFmWiz.calculate_bpppf_rows(payload, {0: 1_000_000, 1: 100_000}),
                "packet_bitrate_skip": "test skip",
                "frame_analysis_skip": "test skip",
            }
            lines = FFmWiz.build_media_info_report_lines(
                input_path,
                payload,
                "",
                Path(tmp) / "input_info.txt",
                {},
                analysis,
            )
            plain = FFmWiz.render_info_report(lines, color=False)
            self.assertIn("Technical Diagnosis", plain)
            self.assertIn("Encoder Metadata / Encoding Settings", plain)
            self.assertIn("Stream Size Analysis", plain)
            self.assertIn("Bits Per Pixel Per Frame", plain)
            self.assertIn("Color Metadata Extended", plain)
            self.assertIn("Audio Technical Detail", plain)

    def test_media_info_stream_summary_csv_writes_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.csv"
            rows = [{
                "stream_index": 1,
                "type": "audio",
                "codec": "aac",
                "language": "unknown",
                "title": "Track1",
                "duration": "00:10",
                "bitrate": "128 kbps",
                "size_bytes": 160000,
                "size_mb": "0.15",
                "percent_of_file": "10.00",
                "estimated": "no",
            }]
            FFmWiz.write_media_info_stream_summary_csv(path, rows)
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("stream_index,type,codec", text)
            self.assertIn("1,audio,aac", text)

    def test_media_info_calculates_exact_stream_sizes_without_deep_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.mkv"
            input_path.write_bytes(b"x" * 2000)
            payload = {
                "format": {"duration": "10", "size": "2000"},
                "streams": [
                    {"index": 0, "codec_type": "video", "codec_name": "hevc", "duration": "10"},
                    {"index": 1, "codec_type": "audio", "codec_name": "opus", "duration": "10"},
                ],
            }
            sidecars: list[Path] = []
            skipped: list[str] = []
            with mock.patch.object(FFmWiz, "probe_packet_sizes", return_value={0: 1500, 1: 500}) as probe:
                analysis = FFmWiz.build_media_info_analysis(
                    "ffprobe",
                    input_path,
                    payload,
                    Path(tmp) / "input_info.txt",
                    False,
                    sidecars,
                    skipped,
                )
            probe.assert_called_once()
            self.assertEqual(sidecars, [])
            rows = analysis["stream_size_rows"]
            self.assertEqual(rows[0]["size_bytes"], 1500)
            self.assertEqual(rows[0]["bitrate"], "1 kbps")
            self.assertEqual(rows[1]["size_bytes"], 500)
            self.assertEqual(rows[1]["bitrate"], "1 kbps")
            diagnosis = FFmWiz.build_media_info_technical_diagnosis(input_path, payload, rows)
            self.assertTrue(any("with bitrate 1 kbps" in line for line in diagnosis))
            self.assertIn("Deep packet/frame analysis skipped by user.", skipped)

    def test_source_video_stream_summary_includes_chapter_presence(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.mkv"
            input_path.write_bytes(b"dummy")
            answers = {
                "input_path": input_path,
                "format": {"duration": "10", "bit_rate": "1000000"},
                "video_streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "hevc",
                        "width": 1920,
                        "height": 1080,
                        "avg_frame_rate": "24/1",
                        "color_range": "tv",
                        "bit_rate": "900000",
                        "duration": "10",
                    }
                ],
                "audio_streams": [],
                "subtitle_streams": [],
                "packet_sizes": {},
                "probe": {"chapters": [self.chapter(0, 5, "Intro")]},
            }
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                FFmWiz.print_source_info(answers)
            self.assertIn("chapters: yes", out.getvalue())

            answers["probe"] = {"chapters": []}
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                FFmWiz.print_source_info(answers)
            self.assertIn("chapters: no", out.getvalue())

    def test_separator_ranges_normalize_points(self):
        ranges = FFmWiz.separator_ranges([10, 5, 5, -1, 20, 100], 20.0)
        self.assertEqual(ranges, [(0.0, 5.0), (5.0, 10.0), (10.0, 20.0)])

    def test_split_points_generate_part_outputs_after_final_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["separator_points"] = [120.0, 300.0]
            text = self.command_text(answers)
            self.assertIn("Pato12_Part01.mp4", text)
            self.assertIn("Pato12_Part02.mp4", text)
            self.assertIn("Pato12_Part03.mp4", text)
            self.assertIn("[vfinal]split=3[svpart0_src][svpart1_src][svpart2_src]", text)
            self.assertIn("trim=start=0.000000:end=120.000000,setpts=PTS-STARTPTS[svout0]", text)
            self.assertIn("trim=start=120.000000:end=300.000000,setpts=PTS-STARTPTS[svout1]", text)
            self.assertIn("-map [svout0]", text)
            self.assertIn("-map [svout1]", text)
            self.assertIn("-map [svout2]", text)
            self.assertNotIn("Pato12_1.mp4", text)

    def test_join_videos_copy_command_uses_concat_demuxer_and_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mkv"
            second = base / "b.mkv"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
            }
            items = [
                {"path": first, "streams": [stream], "video_streams": [stream], "audio_streams": [], "format": {}, "duration": 1.0},
                {"path": second, "streams": [dict(stream)], "video_streams": [dict(stream)], "audio_streams": [], "format": {}, "duration": 1.0},
            ]
            compatible, reasons = FFmWiz.join_copy_compatibility(items)
            self.assertTrue(compatible, reasons)
            cmd = FFmWiz.build_join_copy_command({"ffmpeg": "ffmpeg"}, items, base / "joined.mkv")
            self.assertIn("-f", cmd)
            self.assertIn("concat", cmd)
            self.assertIn("-c", cmd)
            self.assertIn("copy", cmd)

    def test_join_videos_near_quality_command_uses_concat_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mp4"
            second = base / "b.mp4"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
            }
            items = [
                {"path": first, "streams": [stream], "video_streams": [stream], "audio_streams": [], "format": {}, "duration": 1.0},
                {"path": second, "streams": [dict(stream, width=1920)], "video_streams": [dict(stream, width=1920)], "audio_streams": [], "format": {}, "duration": 1.0},
            ]
            cmd = FFmWiz.build_join_near_quality_command({"ffmpeg": "ffmpeg"}, items, base / "joined.mp4")
            self.assertIn("-filter_complex", cmd)
            self.assertIn("-c:v", cmd)
            self.assertIn("libx264", cmd)

    def test_join_videos_near_quality_pads_missing_audio_with_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mp4"
            second = base / "b.mp4"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
            }
            audio = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}
            items = [
                {"path": first, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
                {"path": second, "streams": [dict(stream)], "video_streams": [dict(stream)], "audio_streams": [], "format": {}, "duration": 1.0},
            ]
            cmd = FFmWiz.build_join_near_quality_command({"ffmpeg": "ffmpeg"}, items, base / "joined.mp4")
            text = " ".join(cmd)
            self.assertIn("anullsrc=channel_layout=stereo", text)
            self.assertIn("concat=n=2:v=1:a=1", text)
            self.assertIn("-map [a]", text)
            self.assertNotIn("-an", cmd)

    def test_join_videos_near_quality_uses_nvenc_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mp4"
            second = base / "b.mp4"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
            }
            items = [
                {"path": first, "streams": [stream], "video_streams": [stream], "audio_streams": [], "format": {}, "duration": 1.0},
                {"path": second, "streams": [dict(stream, width=1920)], "video_streams": [dict(stream, width=1920)], "audio_streams": [], "format": {}, "duration": 1.0},
            ]
            cmd = FFmWiz.build_join_near_quality_command(
                {"ffmpeg": "ffmpeg", "video_encoders": ["h264_nvenc"]},
                items,
                base / "joined.mp4",
            )
            self.assertIn("h264_nvenc", cmd)
            self.assertIn("-qp", cmd)

    def test_join_videos_near_quality_applies_nvenc_multipass(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mp4"
            second = base / "b.mp4"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
            }
            items = [
                {"path": first, "streams": [stream], "video_streams": [stream], "audio_streams": [], "format": {}, "duration": 1.0},
                {"path": second, "streams": [dict(stream)], "video_streams": [dict(stream)], "audio_streams": [], "format": {}, "duration": 1.0},
            ]
            cmd = FFmWiz.build_join_near_quality_command(
                {"ffmpeg": "ffmpeg", "video_encoders": ["h264_nvenc"], "nvenc_multipass": "fullres"},
                items,
                base / "joined.mp4",
            )
            text = " ".join(cmd)
        self.assertIn("-multipass fullres", text)

    def test_join_encode_output_path_never_matches_any_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mkv"
            second = base / "a.mp4"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "avg_frame_rate": "30/1", "color_range": "tv"}
            audio = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}
            answers = {
                "ffmpeg": "ffmpeg",
                "input_path": first,
                "output_path": second,
                "output_ext": "mp4",
                "video_codec": "H265",
                "use_gpu": False,
                "video_bitrate_kbps": 400,
                "resolution": FFmWiz.parse_resolution("480p"),
                "fps": 4,
                "audio_tracks": [0],
                "audio_codec": "aac",
                "audio_bitrate_kbps": 128,
                "format": {"duration": "2"},
                "video_streams": [stream],
                "audio_streams": [audio],
            }
            items = [
                {"path": first, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
                {"path": second, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
            ]
            cmd = FFmWiz.build_join_encode_command(answers, items, second)
            self.assertNotEqual(Path(cmd[-1]).resolve(), second.resolve())
            self.assertIn("_Encode", Path(cmd[-1]).stem)

    def test_join_encode_normalizes_pts_before_concat(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mkv"
            second = base / "b.mkv"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "avg_frame_rate": "30/1", "color_range": "tv"}
            audio = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}
            answers = {
                "ffmpeg": "ffmpeg",
                "input_path": first,
                "output_path": base / "out.mp4",
                "output_ext": "mp4",
                "video_codec": "H265",
                "use_gpu": False,
                "video_bitrate_kbps": 400,
                "resolution": FFmWiz.parse_resolution("480p"),
                "fps": 4,
                "audio_tracks": [0],
                "audio_codec": "aac",
                "audio_bitrate_kbps": 128,
                "format": {"duration": "2"},
                "video_streams": [stream],
                "audio_streams": [audio],
            }
            items = [
                {"path": first, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
                {"path": second, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
            ]
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, base / "out.mp4"))
            self.assertIn("crop=", text) if answers.get("crop_enabled") else None
            self.assertIn("fps=4,scale=", text)
            self.assertIn("format=yuv420p,setpts=PTS-STARTPTS[jv0]", text)
            self.assertIn("aresample=48000:async=1:first_pts=0", text)

    def test_join_encode_preserves_10bit_source_pixel_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mkv"
            second = base / "b.mkv"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {
                "codec_type": "video",
                "codec_name": "h265",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
                "pix_fmt": "yuv420p10le",
                "bits_per_raw_sample": "10",
                "color_range": "tv",
            }
            audio = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}
            answers = {
                "ffmpeg": "ffmpeg",
                "input_path": first,
                "output_path": base / "out.mp4",
                "output_ext": "mp4",
                "video_codec": "H265",
                "use_gpu": False,
                "video_bitrate_kbps": 400,
                "resolution": FFmWiz.parse_resolution("480p"),
                "fps": 4,
                "audio_tracks": [0],
                "audio_codec": "aac",
                "audio_bitrate_kbps": 128,
                "format": {"duration": "2"},
                "video_streams": [stream],
                "audio_streams": [audio],
            }
            items = [
                {"path": first, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
                {"path": second, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
            ]
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, base / "out.mp4"))
        self.assertIn("format=yuv420p10le,setpts=PTS-STARTPTS[jv0]", text)
        self.assertIn("-profile:v main10", text)

    def test_join_encode_split_points_generate_multiple_part_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mkv"
            second = base / "b.mkv"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "avg_frame_rate": "30/1", "color_range": "tv"}
            audio = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}
            answers = {
                "ffmpeg": "ffmpeg",
                "input_path": first,
                "output_path": base / "joined.mp4",
                "output_ext": "mp4",
                "video_codec": "H265",
                "use_gpu": True,
                "video_bitrate_kbps": 400,
                "resolution": FFmWiz.parse_resolution("480p"),
                "fps": 4,
                "audio_tracks": [0],
                "audio_codec": "aac",
                "audio_bitrate_kbps": 128,
                "separator_points": [0.75],
                "audio_speed_from_video": True,
                "video_speed_enabled": False,
                "video_speed_factor": 1.0,
                "format": {"duration": "2"},
                "video_streams": [stream],
                "audio_streams": [audio],
            }
            items = [
                {"path": first, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
                {"path": second, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
            ]
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, base / "joined.mp4"))
            self.assertIn("joined_Part01.mp4", text)
            self.assertIn("joined_Part02.mp4", text)
            self.assertNotIn(":unsafe=1", text)
            self.assertNotIn("[jvcat]scale=", text)
            self.assertNotIn("[jvcat_norm]", text)
            self.assertNotIn("atempo=1", text)
            self.assertIn("[jvfinal]split=2[jvpart0_src][jvpart1_src]", text)
            self.assertIn("[jafinal0]asplit=2[japart0_0_src][japart1_0_src]", text)
            self.assertIn("-map [jvout0]", text)
            self.assertIn("-map [jaout0_0]", text)
            self.assertIn("-map [jvout1]", text)
            self.assertIn("-map [jaout1_0]", text)
            self.assertEqual(text.count("-hwaccel cuda -hwaccel_device 0 -i"), 2)
            self.assert_not_contains_any(text, ["-hwaccel_output_format cuda", "scale_cuda", "hwupload_cuda", "hwdownload"])

    def test_join_encode_multi_range_trim_splits_joined_streams_before_branching(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mkv"
            second = base / "b.mkv"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "avg_frame_rate": "30/1", "color_range": "tv"}
            audio = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}
            answers = {
                "ffmpeg": "ffmpeg",
                "input_path": first,
                "output_path": base / "joined.mp4",
                "output_ext": "mp4",
                "video_codec": "H265",
                "use_gpu": True,
                "video_bitrate_kbps": 400,
                "resolution": FFmWiz.parse_resolution("480p"),
                "fps": 4,
                "audio_tracks": [0],
                "audio_codec": "aac",
                "audio_bitrate_kbps": 128,
                "cut_keep_ranges": [(0.0, 0.5), (1.0, 1.5), (1.6, 2.0)],
                "format": {"duration": "2"},
                "video_streams": [stream],
                "audio_streams": [audio],
            }
            items = [
                {"path": first, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
                {"path": second, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
            ]
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, base / "joined.mp4"))
        self.assertIn("[jvcat]split=3[jvcut_src0][jvcut_src1][jvcut_src2]", text)
        self.assertIn("[jvcut_src0]trim=start=0.000000:end=0.500000,setpts=PTS-STARTPTS[jvcut_0]", text)
        self.assertIn("[jvcut_src1]trim=start=1.000000:end=1.500000,setpts=PTS-STARTPTS[jvcut_1]", text)
        self.assertIn("[jacat0]asplit=3[jacut0_src0][jacut0_src1][jacut0_src2]", text)
        self.assertIn("[jacut0_src1]atrim=start=1.000000:end=1.500000,asetpts=PTS-STARTPTS[jacut0_1]", text)
        self.assertNotIn("[jvcat]trim=start=1.000000", text)
        self.assertNotIn("[jacat0]atrim=start=1.000000", text)
        self.assertNotIn(":unsafe=1", text)

    def test_join_encode_single_range_trim_does_not_generate_split_or_concat_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mkv"
            second = base / "b.mkv"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "avg_frame_rate": "30/1", "color_range": "tv"}
            audio = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}
            answers = {
                "ffmpeg": "ffmpeg",
                "input_path": first,
                "output_path": base / "joined.mp4",
                "output_ext": "mp4",
                "video_codec": "H265",
                "use_gpu": True,
                "video_bitrate_kbps": 400,
                "resolution": FFmWiz.parse_resolution("480p"),
                "fps": 4,
                "audio_tracks": [0],
                "audio_codec": "aac",
                "audio_bitrate_kbps": 128,
                "cut_keep_ranges": [(0.25, 1.25)],
                "format": {"duration": "2"},
                "video_streams": [stream],
                "audio_streams": [audio],
            }
            items = [
                {"path": first, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
                {"path": second, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
            ]
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, base / "joined.mp4"))
        self.assertIn("[jvcat]trim=start=0.250000:end=1.250000,setpts=PTS-STARTPTS[jvcut_0]", text)
        self.assertIn("[jacat0]atrim=start=0.250000:end=1.250000,asetpts=PTS-STARTPTS[jacut0_0]", text)
        self.assertNotIn("[jvcat]split=", text)
        self.assertNotIn("[jacat0]asplit=", text)
        self.assertNotIn("concat=n=1", text)

    def test_join_encode_complex_nvenc_uses_cuda_decode_only_per_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mkv"
            second = base / "b.mkv"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "avg_frame_rate": "30/1", "color_range": "tv"}
            answers = {
                "ffmpeg": "ffmpeg",
                "input_path": first,
                "output_path": base / "out.mp4",
                "output_ext": "mp4",
                "video_codec": "H265",
                "use_gpu": True,
                "video_bitrate_kbps": 400,
                "resolution": FFmWiz.parse_resolution("480p"),
                "fps": 4,
                "audio_tracks": [],
                "audio_codec": "aac",
                "audio_bitrate_kbps": 128,
                "format": {"duration": "2"},
                "video_streams": [stream],
                "audio_streams": [],
            }
            items = [
                {"path": first, "streams": [stream], "video_streams": [stream], "audio_streams": [], "format": {}, "duration": 1.0},
                {"path": second, "streams": [stream], "video_streams": [stream], "audio_streams": [], "format": {}, "duration": 1.0},
            ]
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, base / "out.mp4"))
            self.assertEqual(text.count("-hwaccel cuda -hwaccel_device 0 -i"), 2)
            self.assertIn("hevc_nvenc", text)
            self.assertIn("-filter_complex", text)
            self.assert_not_contains_any(text, ["-hwaccel_output_format cuda", "scale_cuda", "pad_cuda", "hwupload_cuda", "hwdownload"])

    def test_join_encode_cpu_path_does_not_add_cuda_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mkv"
            second = base / "b.mkv"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "avg_frame_rate": "30/1", "color_range": "tv"}
            answers = {
                "ffmpeg": "ffmpeg",
                "input_path": first,
                "output_path": base / "out.mp4",
                "output_ext": "mp4",
                "video_codec": "H265",
                "use_gpu": False,
                "video_bitrate_kbps": 400,
                "resolution": FFmWiz.parse_resolution("480p"),
                "fps": 4,
                "audio_tracks": [],
                "audio_codec": "aac",
                "audio_bitrate_kbps": 128,
                "format": {"duration": "2"},
                "video_streams": [stream],
                "audio_streams": [],
            }
            items = [
                {"path": first, "streams": [stream], "video_streams": [stream], "audio_streams": [], "format": {}, "duration": 1.0},
                {"path": second, "streams": [stream], "video_streams": [stream], "audio_streams": [], "format": {}, "duration": 1.0},
            ]
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, base / "out.mp4"))
            self.assertNotIn("-hwaccel cuda", text)

    def test_mux_video_stream_summary_includes_chapter_presence(self):
        stream = FFmWiz.MuxStreamInfo(
            index=0,
            codec_type="video",
            codec_name="h264",
            width=1920,
            height=1080,
            fps=24.0,
            duration=10.0,
        )
        self.assertIn("chapters: yes", FFmWiz.mux_format_stream(stream, {}, chapter_count=2))
        self.assertIn("chapters: no", FFmWiz.mux_format_stream(stream, {}, chapter_count=0))

    def chapter(self, start: float, end: float, title: str) -> dict:
        return {
            "time_base": "1/1000",
            "start": int(start * 1000),
            "end": int(end * 1000),
            "start_time": f"{start:.6f}",
            "end_time": f"{end:.6f}",
            "tags": {"title": title},
        }

    def test_gpu_crop_480p_fps_no_cuts_uses_cuda_fast_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            text = self.command_text(answers)
        self.assertIn("-hwaccel cuda", text)
        self.assertIn("-hwaccel_output_format cuda", text)
        self.assertIn("-c:v h264_cuvid", text)
        self.assertIn("-crop 172x189x428x1095", text)
        self.assertIn("scale_cuda=w=852:h=480", text)
        self.assertIn("-r:v 4", text)
        self.assertIn("-fps_mode:v cfr", text)
        self.assert_not_contains_any(text, ["hwdownload", "hwupload_cuda", "crop=", "fps=4"])

    def test_gpu_single_cut_does_not_use_filter_complex(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["cut_keep_ranges"] = [(0.0, 6074.221)]
            text = self.command_text(answers)
        self.assertIn("-hwaccel cuda", text)
        self.assertIn("-c:v h264_cuvid", text)
        self.assertIn("-crop 172x189x428x1095", text)
        self.assertIn("-t 6074.221000", text)
        self.assertIn("scale_cuda=w=852:h=480", text)
        self.assertIn("hevc_nvenc", text)
        self.assert_not_contains_any(text, ["filter_complex", "trim", "atrim", "concat=n=1", "anull", "hwdownload", "hwupload_cuda"])

    def test_gpu_multi_cut_uses_cuda_decode_only_with_cpu_filter_complex(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["cut_keep_ranges"] = [(0.0, 2.0), (5.0, 8.0)]
            text = self.command_text(answers)
        self.assertIn("-hwaccel cuda -hwaccel_device 0 -i", text)
        self.assertIn("-filter_complex", text)
        self.assertIn("trim=start=", text)
        self.assertIn("hevc_nvenc", text)
        self.assert_not_contains_any(text, ["-hwaccel_output_format cuda", "scale_cuda", "pad_cuda", "hwupload_cuda", "hwdownload"])

    def test_gpu_crop_unknown_decoder_falls_back_to_cpu_crop_before_nvenc(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["video_streams"][0]["codec_name"] = "prores"
            text = self.command_text(answers)
        self.assertIn("-hwaccel cuda -hwaccel_device 0 -i", text)
        self.assertIn("crop=iw-428-1095:ih-172-189:428:172", text)
        self.assertIn("scale=852:480", text)
        self.assertIn("fps=4", text)
        self.assertIn("hevc_nvenc", text)
        self.assert_not_contains_any(text, ["-hwaccel_output_format cuda", "-crop 172x189x428x1095", "scale_cuda", "hwdownload", "hwupload_cuda"])

    def test_strict_size_bitrate_mode_uses_old_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["video_bitrate_mode"] = "strict_size"
            text = self.command_text(answers)
        self.assertIn("-b:v 400k", text)
        self.assertIn("-maxrate:v 400k", text)
        self.assertIn("-bufsize:v 800k", text)

    def test_cropped_source_size_rejects_overlarge_crop_margins(self):
        answers = self.base_answers(".")
        answers.update({
            "crop_enabled": True,
            "crop_left": 2000,
            "crop_right": 2000,
            "crop_top": 0,
            "crop_bottom": 0,
        })
        with self.assertRaisesRegex(ValueError, "left \\+ right"):
            FFmWiz.cropped_source_size(answers)

    def test_parse_crop_config_n_clears_stale_margins(self):
        answers = self.base_answers(".")
        answers.update({
            "crop_enabled": True,
            "crop_left": 100,
            "crop_right": 100,
            "crop_top": 10,
            "crop_bottom": 10,
        })
        FFmWiz.parse_crop_config_value(answers, "n", {})
        self.assertFalse(answers["crop_enabled"])
        for key in ("crop_left", "crop_right", "crop_top", "crop_bottom"):
            self.assertNotIn(key, answers)

    def test_parse_crop_config_rejects_overlarge_inline_crop(self):
        answers = self.base_answers(".")
        with self.assertRaisesRegex(ValueError, "top \\+ bottom"):
            FFmWiz.parse_crop_config_value(answers, "800,0,0,800", {})

    def test_parse_hmsf_time_rejects_unbounded_frame_overflow(self):
        with self.assertRaisesRegex(ValueError, "Frame value is too large"):
            FFmWiz.parse_hmsf_time("0:0:0:99999", 30.0)

    def test_back_accepts_persian_and_arabic_zero(self):
        self.assertTrue(FFmWiz.is_back_value("0"))
        self.assertTrue(FFmWiz.is_back_value("۰"))
        self.assertTrue(FFmWiz.is_back_value("٠"))
        with mock.patch.object(FFmWiz, "ask_raw", return_value="۰"):
            with self.assertRaises(FFmWiz.Back):
                FFmWiz.ask_yes_no("Continue? (y/n) [n]: ", False)

    def test_zero_based_selection_still_accepts_track_zero(self):
        with mock.patch.object(FFmWiz, "ask_raw", return_value="0"):
            self.assertEqual(FFmWiz.ask_selection("tracks: ", 3, [0]), [0])

    def test_run_mode_steps_back_skips_auto_single_audio_track_step(self):
        calls: list[str] = []
        output_calls = {"count": 0}

        def fake_input(answers):
            calls.append("input")
            answers["audio_streams"] = [{"codec_type": "audio"}]

        def fake_audio_track(answers):
            calls.append("audio_auto")
            answers["audio_index"] = 0

        def fake_output(_answers):
            calls.append("output")
            output_calls["count"] += 1
            if output_calls["count"] == 1:
                raise FFmWiz.Back()

        FFmWiz.run_mode_steps(
            {"_question_offset": 0, "audio_streams": [{"codec_type": "audio"}]},
            [
                FFmWiz.Step("input_path", lambda a: True, fake_input),
                FFmWiz.Step("audio_track", lambda a: True, fake_audio_track),
                FFmWiz.Step("output_location", lambda a: True, fake_output),
            ],
        )
        self.assertEqual(calls, ["input", "audio_auto", "output", "input", "audio_auto", "output"])

    def test_step_video_codec_rejects_unknown_before_accepting_valid_alias(self):
        prompts = iter(["h2654", "H264"])
        errors: list[str] = []
        answers = self.base_answers(".")
        answers["video_encoders"] = ["libx264", "libx265"]
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=lambda _prompt: next(prompts)), \
             mock.patch.object(FFmWiz, "error", side_effect=lambda message: errors.append(message)):
            FFmWiz.step_video_codec(answers)
        self.assertEqual(answers["video_codec"], "H264")
        self.assertTrue(any("Unknown video encoder" in message for message in errors))

    def test_ensure_launcher_file_preserves_user_modified_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.ps1"
            path.write_text("# user custom launcher\n", encoding="utf-8")
            FFmWiz.ensure_launcher_file(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "# user custom launcher\n")

    def test_gpu_multiple_cut_ranges_keep_nvenc_without_concat_one_or_anull(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["crop_enabled"] = False
            answers["resolution"] = "n"
            answers["fps"] = None
            answers["audio_codec"] = "copy"
            answers["cut_keep_ranges"] = [(0.0, 10.0), (20.0, 30.0)]
            text = self.command_text(answers)
        self.assertIn("-filter_complex", text)
        self.assertIn("concat=n=2", text)
        self.assertIn("hevc_nvenc", text)
        self.assert_not_contains_any(text, ["concat=n=1", "anull"])

    def test_resolution_preset_closest_edge_portrait(self):
        answers = {"video_streams": [{"width": 950, "height": 1840}], "crop_enabled": False}
        dims, warning = FFmWiz.calculate_scale_dimensions(answers, FFmWiz.parse_resolution("1080p"))
        self.assertEqual(dims, (992, 1920))
        self.assertEqual(warning, "")
        self.assertEqual(answers["resolution_scale_axis"], "height")

    def test_resolution_preset_closest_edge_landscape(self):
        answers = {"video_streams": [{"width": 1840, "height": 950}], "crop_enabled": False}
        dims, warning = FFmWiz.calculate_scale_dimensions(answers, FFmWiz.parse_resolution("1080p"))
        self.assertEqual(dims, (1920, 992))
        self.assertEqual(warning, "")
        self.assertEqual(answers["resolution_scale_axis"], "width")

    def test_pato12_crop_480p_resolves_to_852x480(self):
        answers = {
            "video_streams": [{"width": 3440, "height": 1440}],
            "crop_enabled": True,
            "crop_left": 429,
            "crop_right": 1095,
            "crop_top": 172,
            "crop_bottom": 189,
        }
        dims, _warning = FFmWiz.calculate_scale_dimensions(answers, FFmWiz.parse_resolution("480p"))
        # answers has no "resolution" key, so crop normalization treats this as a
        # no-resize case and aligns the cropped frame to even output dimensions.
        self.assertEqual(answers["crop_box_dimensions"], (1916, 1080))
        self.assertEqual(dims, (852, 480))

    def test_explicit_width_preserves_ratio(self):
        answers = {"video_streams": [{"width": 950, "height": 1840}], "crop_enabled": False}
        dims, _warning = FFmWiz.calculate_scale_dimensions(answers, FFmWiz.parse_resolution("w1080"))
        self.assertEqual(dims[0], 1080)
        self.assertEqual(dims[1] % 2, 0)
        self.assertAlmostEqual(dims[0] / dims[1], 950 / 1840, delta=0.002)

    def test_explicit_height_preserves_ratio(self):
        answers = {"video_streams": [{"width": 950, "height": 1840}], "crop_enabled": False}
        dims, _warning = FFmWiz.calculate_scale_dimensions(answers, FFmWiz.parse_resolution("h1080"))
        self.assertEqual(dims[1], 1080)
        self.assertEqual(dims[0] % 2, 0)
        self.assertAlmostEqual(dims[0] / dims[1], 950 / 1840, delta=0.002)

    def test_box_mode_returns_exact_canvas_dimensions(self):
        answers = {"video_streams": [{"width": 950, "height": 1840}], "crop_enabled": False}
        dims, warning = FFmWiz.calculate_scale_dimensions(answers, FFmWiz.parse_resolution("1080x1920"))
        # Box mode returns the exact requested canvas; AR preservation is handled
        # by force_original_aspect_ratio=decrease + pad in the filter chain.
        self.assertEqual(dims, (1080, 1920))
        self.assertEqual(warning, "")

    def test_exact_stretch_mode_outputs_exact_dimensions_and_warns(self):
        answers = {"video_streams": [{"width": 950, "height": 1840}], "crop_enabled": False}
        dims, warning = FFmWiz.calculate_scale_dimensions(answers, FFmWiz.parse_resolution("stretch:1080x1920"))
        self.assertEqual(dims, (1080, 1920))
        self.assertIn("stretch", warning)

    def test_hardsub_internal_bitmap_subtitle_is_rejected(self):
        self.assertFalse(FFmWiz.hardsub_internal_subtitle_codec_is_supported("hdmv_pgs_subtitle"))
        self.assertIn("Bitmap subtitle streams", FFmWiz.hardsub_internal_subtitle_error("hdmv_pgs_subtitle"))

    def test_hardsub_internal_text_subtitles_are_accepted(self):
        for codec in ("ass", "srt", "subrip", "text", "mov_text", "webvtt"):
            self.assertTrue(FFmWiz.hardsub_internal_subtitle_codec_is_supported(codec))
            self.assertIsNone(FFmWiz.hardsub_internal_subtitle_error(codec))

    def test_hardsub_external_unsupported_extension_is_rejected(self):
        self.assertFalse(FFmWiz.hardsub_external_subtitle_extension_supported(Path("episode.sup")))
        self.assertTrue(FFmWiz.hardsub_external_subtitle_extension_supported(Path("episode.ass")))
        self.assertTrue(FFmWiz.hardsub_external_subtitle_extension_supported(Path("episode.webvtt")))

    def test_hardsub_ass_embedded_fonts_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            subtitle = Path(tmp) / "episode.ass"
            subtitle.write_text("[Script Info]\nTitle: x\n\n[Fonts]\nfontname: Custom.ttf\n", encoding="utf-8")
            self.assertTrue(FFmWiz.ass_ssa_has_embedded_fonts(subtitle))

    def test_hardsub_fontsdir_is_included_in_filter_when_provided(self):
        with tempfile.TemporaryDirectory() as tmp:
            subtitle = Path(tmp) / "episode.ass"
            fontsdir = Path(tmp) / "fonts"
            subtitle.write_text("[Script Info]\nTitle: x\n", encoding="utf-8")
            fontsdir.mkdir()
            answers = {
                "hardsub_subtitle_source": "external",
                "hardsub_subtitle_path": subtitle,
                "hardsub_fontsdir": fontsdir,
            }
            subtitle_filter = FFmWiz.hardsub_subtitle_filter(answers)
        self.assertIn("subtitles=filename=", subtitle_filter)
        self.assertIn(":fontsdir=", subtitle_filter)

    def test_hardsub_internal_fontsdir_keeps_subtitle_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "input.mkv"
            fontsdir = Path(tmp) / "fonts"
            video.write_bytes(b"")
            fontsdir.mkdir()
            answers = {
                "input_path": video,
                "hardsub_subtitle_source": "internal",
                "hardsub_subtitle_index": 2,
                "hardsub_fontsdir": fontsdir,
            }
            subtitle_filter = FFmWiz.hardsub_subtitle_filter(answers)
        self.assertIn(":si=2", subtitle_filter)
        self.assertIn(":fontsdir=", subtitle_filter)

    def hardsub_answers(self, tmp: str, input_name: str = "input.mkv", output_ext: str = "mp4") -> dict:
        root = Path(tmp)
        input_path = root / input_name
        subtitle_path = root / "subtitle.srt"
        input_path.write_bytes(b"")
        subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nText\n", encoding="utf-8")
        return {
            "ffmpeg": "ffmpeg",
            "input_path": input_path,
            "output_location": root,
            "output_ext": output_ext,
            "video_codec": "H265",
            "use_gpu": False,
            "video_streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "color_range": "tv"}],
            "audio_streams": [{"codec_type": "audio", "codec_name": "aac"}],
            "hardsub_subtitle_source": "external",
            "hardsub_subtitle_path": subtitle_path,
            "hardsub_audio_mode": "copy-all",
            "hardsub_quality_mode": "near-lossless",
            "hardsub_hdr_handling": "standard",
            "format": {"duration": "1"},
        }

    def test_hardsub_matching_container_copies_audio_without_policy_prompt_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.hardsub_answers(tmp, "input.mkv", "mkv")
            cmd = FFmWiz.build_hardsub_command(answers)
        self.assertIn("-c:a", cmd)
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "copy")

    def test_hardsub_different_container_requires_audio_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.hardsub_answers(tmp, "input.mkv", "mp4")
            with self.assertRaises(ValueError):
                FFmWiz.build_hardsub_command(answers)

    def test_hardsub_different_container_aac_policy_transcodes_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.hardsub_answers(tmp, "input.mkv", "mp4")
            answers["hardsub_audio_container_policy"] = "aac"
            cmd = FFmWiz.build_hardsub_command(answers)
        self.assertIn("-c:a", cmd)
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "aac")
        self.assertIn("-b:a", cmd)
        self.assertEqual(cmd[cmd.index("-b:a") + 1], f"{FFmWiz.DEFAULT_AUDIO_BITRATE_KBPS}k")
        self.assertIn("-ac", cmd)
        self.assertEqual(cmd[cmd.index("-ac") + 1], "2")

    def test_hardsub_different_container_copy_anyway_policy_warns_by_choice_and_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.hardsub_answers(tmp, "input.mkv", "mp4")
            answers["hardsub_audio_container_policy"] = "copy-anyway"
            cmd = FFmWiz.build_hardsub_command(answers)
        self.assertIn("-c:a", cmd)
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "copy")

    def test_hardsub_nvenc_multipass_is_added_when_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.hardsub_answers(tmp, "input.mkv", "mp4")
            answers["use_gpu"] = True
            answers["hardsub_audio_container_policy"] = "aac"
            answers["nvenc_multipass"] = "qres"
            cmd = FFmWiz.build_hardsub_command(answers)
            text = " ".join(cmd)
        self.assertIn("hevc_nvenc", text)
        self.assertIn("-multipass qres", text)

    def copy_cut_answers(self, chapters: list[dict], duration: float = 900.0) -> dict:
        return {
            "ffprobe": "ffprobe",
            "input_path": Path("input.mkv"),
            "probe": {"chapters": chapters},
            "format": {"duration": str(duration)},
        }

    def test_copy_cut_no_chapter_overlap_keeps_source_chapters(self):
        answers = self.copy_cut_answers([self.chapter(0, 100, "Intro")], 300)
        plan = FFmWiz.analyze_copy_cut_chapter_plan(answers, [(0, 300)])
        self.assertEqual(plan["mode"], "copy")
        cmd = FFmWiz.build_copy_cut_range_command("ffmpeg", "-y", Path("input.mkv"), 0, 300, Path("out.mkv"), plan)
        self.assertIn("-map_chapters", cmd)
        self.assertEqual(cmd[cmd.index("-map_chapters") + 1], "0")

    def test_copy_cut_overlapping_chapter_writes_metadata_and_remaps(self):
        answers = self.copy_cut_answers([
            self.chapter(0, 100, "Intro"),
            self.chapter(300, 600, "Removed"),
            self.chapter(600, 900, "Part 2"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            plan = FFmWiz.prepare_copy_cut_chapter_plan(answers, [(0, 300), (600, 900)], Path(tmp))
            self.assertEqual(plan["mode"], "metadata")
            metadata_text = Path(plan["metadata_path"]).read_text(encoding="utf-8")
            cmd = FFmWiz.build_copy_cut_concat_command("ffmpeg", "-y", Path("concat.txt"), Path("out.mkv"), plan)
        self.assertIn("title=Intro", metadata_text)
        self.assertIn("START=300000", metadata_text)
        self.assertIn("title=Part 2", metadata_text)
        self.assertNotIn("Removed", metadata_text)
        self.assertEqual(cmd[cmd.index("-map_chapters") + 1], "1")

    def test_copy_cut_all_chapters_dropped_uses_no_chapters(self):
        answers = self.copy_cut_answers([self.chapter(0, 300, "Removed")])
        plan = FFmWiz.analyze_copy_cut_chapter_plan(answers, [(600, 900)])
        self.assertEqual(plan["mode"], "drop")
        cmd = FFmWiz.build_copy_cut_range_command("ffmpeg", "-y", Path("input.mkv"), 600, 900, Path("out.mkv"), plan)
        self.assertEqual(cmd[cmd.index("-map_chapters") + 1], "-1")

    def test_copy_cut_keeps_all_streams_and_copy_codec(self):
        cmd = FFmWiz.build_copy_cut_range_command("ffmpeg", "-y", Path("input.mkv"), 0, 300, Path("out.mkv"))
        self.assertIn("-map", cmd)
        self.assertEqual(cmd[cmd.index("-map") + 1], "0")
        self.assertIn("-c", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")

    def test_copy_cut_uses_duration_not_to(self):
        cmd = FFmWiz.build_copy_cut_range_command("ffmpeg", "-y", Path("input.mkv"), 600, 900, Path("out.mkv"))
        self.assertNotIn("-to", cmd)
        self.assertIn("-t", cmd)
        self.assertLess(cmd.index("-ss"), cmd.index("-i"))
        self.assertLess(cmd.index("-i"), cmd.index("-t"))
        self.assertEqual(cmd[cmd.index("-t") + 1], "00:05:00.000")

    def test_add_files_output_extension_matches_input_even_with_subtitles(self):
        output = FFmWiz.choose_add_files_output_path(
            Path("video.mp4"),
            [{"path": Path("sub.srt"), "subtitle_streams": [{"codec_name": "subrip"}]}],
        )
        self.assertEqual(output.suffix, ".mp4")

    def test_add_files_rejects_incompatible_stream_for_original_container(self):
        errors = FFmWiz.add_files_stream_copy_compatibility_errors(
            Path("video.mp4"),
            [{"path": Path("sub.srt"), "subtitle_streams": [{"codec_name": "subrip"}]}],
        )
        self.assertTrue(errors)
        self.assertIn("cannot be safely stream-copied into .mp4", errors[0])

    def test_add_files_allows_matroska_without_container_switch(self):
        errors = FFmWiz.add_files_stream_copy_compatibility_errors(
            Path("video.mkv"),
            [{"path": Path("sub.srt"), "subtitle_streams": [{"codec_name": "subrip"}]}],
        )
        self.assertEqual(errors, [])
        output = FFmWiz.choose_add_files_output_path(Path("video.mkv"), [])
        self.assertEqual(output.suffix, ".mkv")

    def test_stream_cleanup_output_suffix_matches_input_and_copy_maps_video(self):
        rules = FFmWiz.MuxCleanupRules(
            audio_mode="4",
            audio_languages=[],
            audio_titles=[],
            audio_indexes=[],
            subtitle_mode="1",
            subtitle_languages=[],
            subtitle_titles=[],
            subtitle_indexes=[],
            keep_attachments=False,
            keep_metadata=True,
            keep_chapters=True,
            overwrite=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_file = root / "input.mp4"
            output_root = root / "out"
            input_file.write_bytes(b"")
            output_root.mkdir()
            output = FFmWiz.mux_make_output_path(input_file, output_root, input_file, rules)
            self.assertEqual(output.suffix, ".mp4")
            media = FFmWiz.MuxMediaFile(
                path=input_file,
                format={},
                streams=[FFmWiz.MuxStreamInfo(index=0, codec_type="video", codec_name="h264")],
            )
            cmd, _audio, _subtitle = FFmWiz.mux_build_ffmpeg_command("ffmpeg", input_file, output, media, rules)
        self.assertIn("-map", cmd)
        self.assertIn("0:v?", cmd)
        self.assertIn("-c", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")

    def test_stream_cleanup_metadata_edits_and_default_dispositions_are_emitted(self):
        rules = FFmWiz.MuxCleanupRules(
            audio_mode="4",
            audio_languages=[],
            audio_titles=[],
            audio_indexes=[],
            subtitle_mode="5",
            subtitle_languages=[],
            subtitle_titles=[],
            subtitle_indexes=[],
            keep_attachments=True,
            keep_metadata=True,
            keep_chapters=True,
            overwrite=True,
            metadata_edits=[
                FFmWiz.MuxStreamMetadataEdit(codec_type="audio", match_indexes=[1], language="jpn", title="Main"),
                FFmWiz.MuxStreamMetadataEdit(codec_type="subtitle", match_languages=["unknown"], language="eng"),
            ],
        )
        input_file = Path("input.mkv")
        output = Path("output.mkv")
        media = FFmWiz.MuxMediaFile(
            path=input_file,
            format={},
            streams=[
                FFmWiz.MuxStreamInfo(index=0, codec_type="video", codec_name="h264"),
                FFmWiz.MuxStreamInfo(index=1, codec_type="audio", codec_name="aac", language="unknown", disposition_default=0),
                FFmWiz.MuxStreamInfo(index=2, codec_type="audio", codec_name="aac", language="eng", disposition_default=1),
                FFmWiz.MuxStreamInfo(index=3, codec_type="subtitle", codec_name="ass", language="unknown", disposition_default=1),
            ],
        )
        cmd, _audio, _subtitle = FFmWiz.mux_build_ffmpeg_command("ffmpeg", input_file, output, media, rules)
        text = " ".join(cmd)
        self.assertIn("-disposition:a:0 +default", text)
        self.assertIn("-disposition:a:1 -default", text)
        self.assertIn("-disposition:s:0 +default", text)
        self.assertIn("-metadata:s:a:0 language=jpn", text)
        self.assertIn("-metadata:s:a:0 title=Main", text)
        self.assertIn("-metadata:s:s:0 language=eng", text)

    def test_metadata_video_stream_line_omits_language_and_title(self):
        probe = {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 1920,
                    "height": 1080,
                    "pix_fmt": "yuv420p",
                    "tags": {"language": "eng", "title": "Main video"},
                }
            ]
        }
        line = FFmWiz._strip_ansi(FFmWiz.metadata_stream_line(probe, probe["streams"][0]))
        self.assertIn("type: video", line)
        self.assertIn("codec: hevc", line)
        self.assertIn("size: 1920x1080", line)
        self.assertNotIn("language:", line)
        self.assertNotIn("title:", line)

    def test_metadata_menu_item_marks_default_selection(self):
        self.assertEqual(
            FFmWiz.metadata_menu_item(1, "Stream Metadata Editor", default=True),
            "  1. Stream Metadata Editor [1]",
        )

    def test_metadata_editor_menu_does_not_duplicate_media_report_mode(self):
        answers = {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "metadata_input_path": Path("input.mkv"), "gpu_available": True}
        with mock.patch.object(FFmWiz, "metadata_prompt_input", return_value=answers), \
                mock.patch.object(FFmWiz, "ask_raw", side_effect=["6", "0"]), \
                mock.patch.object(FFmWiz, "error") as error_call, \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            FFmWiz.run_metadata_editor_mode({})
        self.assertIn("Stream Metadata Editor [1]", stdout.getvalue())
        self.assertNotIn("Metadata Report / Inspect", stdout.getvalue())
        error_call.assert_any_call("Enter a menu number from 1 to 5.")

    def test_video_bitstream_single_video_stream_auto_selected(self):
        answers = {
            "ffmpeg": "ffmpeg",
            "ffprobe": "ffprobe",
            "metadata_input_path": Path("input.mkv"),
            "gpu_available": True,
        }
        probe = {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "pix_fmt": "yuv420p",
                }
            ]
        }
        estimate = {
            "ymin": 16,
            "ymax": 235,
            "ylow": 18.0,
            "yhigh": 232.0,
            "frames": 10,
            "conclusion": "likely limited/TV range",
        }
        with mock.patch.object(FFmWiz, "metadata_menu_selection", side_effect=["2", "0"]), \
                mock.patch.object(FFmWiz, "metadata_refresh_probe", return_value=probe), \
                mock.patch.object(FFmWiz, "ask_raw", return_value="1") as ask_raw, \
                mock.patch.object(FFmWiz, "ask_yes_no", return_value=True), \
                mock.patch.object(FFmWiz, "estimate_color_range", return_value=estimate) as estimate_color, \
                contextlib.redirect_stdout(io.StringIO()):
            FFmWiz.run_video_bitstream_metadata_tools(answers)
        prompts = "\n".join(str(call.args[0]) for call in ask_raw.call_args_list)
        self.assertIn("Choose sampling density", prompts)
        self.assertNotIn("Enter stream index", prompts)
        estimate_color.assert_called_once()
        self.assertEqual(estimate_color.call_args.args[1], 0)
        self.assertTrue(estimate_color.call_args.kwargs["use_cuda_decode"])

    def test_signalstats_cuda_decode_command_omits_cuda_frame_output(self):
        cmd = FFmWiz.build_signalstats_command(
            Path("input.mkv"),
            0,
            "balanced",
            "ffmpeg",
            Path("report.txt"),
            use_cuda_decode=True,
        )
        text = " ".join(str(part) for part in cmd)
        self.assertIn("-hwaccel cuda", text)
        self.assertIn("-hwaccel_device 0", text)
        self.assertNotIn("-hwaccel_output_format cuda", text)
        self.assertIn("signalstats", text)

    def test_metadata_report_output_path_creates_reports_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp) / "MediaReports"
            with mock.patch.object(FFmWiz, "default_media_reports_dir", return_value=reports_dir):
                output = FFmWiz.metadata_report_output_path(Path("input.mkv"), "_metadata_report", ".txt")
            self.assertEqual(output.parent.name, "MediaReports")
            self.assertTrue(reports_dir.exists())
            self.assertEqual(output.name, "input.mkv_metadata_report.txt")

    def test_metadata_stream_selection_accepts_zero_index(self):
        probe = {
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "hevc"},
                {"index": 1, "codec_type": "video", "codec_name": "mjpeg"},
            ]
        }
        answers = {}
        with mock.patch.object(FFmWiz, "ask_raw", return_value="0") as ask_raw, \
                contextlib.redirect_stdout(io.StringIO()):
            stream = FFmWiz.select_stream(probe, answers, {"video"})
        self.assertEqual(stream["index"], 0)
        prompt = str(ask_raw.call_args.args[0])
        self.assertIn("back=b", FFmWiz._strip_ansi(prompt))

    def test_metadata_value_prompt_accepts_zero_value(self):
        with mock.patch.object(FFmWiz, "ask_raw", return_value="0"):
            self.assertEqual(FFmWiz.metadata_value_prompt({}, "Enter metadata value"), "0")

    def test_video_full_range_flag_accepts_zero_limited_value(self):
        answers = {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "metadata_input_path": Path("input.mkv")}
        probe = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}]}
        with mock.patch.object(FFmWiz, "metadata_menu_selection", side_effect=["3", "0"]), \
                mock.patch.object(FFmWiz, "metadata_refresh_probe", return_value=probe), \
                mock.patch.object(FFmWiz, "ask_yes_no", return_value=True), \
                mock.patch.object(FFmWiz, "ask_raw", return_value="0"), \
                mock.patch.object(FFmWiz, "confirm_and_run_ffmpeg", return_value=False) as confirm_run, \
                contextlib.redirect_stdout(io.StringIO()):
            FFmWiz.run_video_bitstream_metadata_tools(answers)
        command_text = " ".join(str(part) for part in confirm_run.call_args.args[1])
        self.assertIn("h264_metadata=video_full_range_flag=0", command_text)

    def test_crop_margin_prompt_accepts_zero_value(self):
        answers = {
            "_question_number": 1,
            "video_streams": [{"width": 1920, "height": 1080}],
            "format": {},
        }
        with mock.patch.object(FFmWiz, "ask_raw", return_value="0"):
            FFmWiz.step_crop_top(answers)
        self.assertEqual(answers["crop_top"], 0)

    def test_mux_stream_index_prompt_accepts_zero_value(self):
        answers = {"_question_number": 1}
        with mock.patch.object(FFmWiz, "ask_raw", return_value="0"):
            self.assertEqual(FFmWiz.mux_ask_text(answers, "Audio stream indexes to keep", "use b to go back", zero_is_value=True), "0")

    def test_mux_csv_stream_indexes_accept_zero_value(self):
        answers = {"_question_number": 1}
        with mock.patch.object(FFmWiz, "ask_raw", return_value="0"):
            self.assertEqual(FFmWiz.mux_ask_csv_int_required(answers, "Audio stream indexes to edit", [0, 2]), [0])

    def test_stream_cleanup_detects_when_remux_is_not_needed(self):
        rules = FFmWiz.MuxCleanupRules(
            audio_mode="4",
            audio_languages=[],
            audio_titles=[],
            audio_indexes=[],
            subtitle_mode="5",
            subtitle_languages=[],
            subtitle_titles=[],
            subtitle_indexes=[],
            keep_attachments=True,
            keep_metadata=True,
            keep_chapters=True,
            overwrite=False,
        )
        media = FFmWiz.MuxMediaFile(
            path=Path("input.mkv"),
            format={},
            streams=[
                FFmWiz.MuxStreamInfo(index=0, codec_type="video", codec_name="h264"),
                FFmWiz.MuxStreamInfo(index=1, codec_type="audio", codec_name="aac", disposition_default=1),
                FFmWiz.MuxStreamInfo(index=2, codec_type="subtitle", codec_name="ass", disposition_default=1),
            ],
        )
        audio_keep = FFmWiz.mux_selected_audio_streams(media, rules)
        subtitle_keep = FFmWiz.mux_selected_subtitle_streams(media, rules)
        self.assertEqual(FFmWiz.mux_remux_needed_reasons(media, rules, audio_keep, subtitle_keep), [])
        rules.metadata_edits = [FFmWiz.MuxStreamMetadataEdit(codec_type="audio", match_indexes=[1], title="Edited")]
        self.assertIn("stream metadata is edited", FFmWiz.mux_remux_needed_reasons(media, rules, audio_keep, subtitle_keep))

    def test_stream_cleanup_visible_question_numbers_do_not_jump_when_steps_are_skipped(self):
        media_files = [
            FFmWiz.MuxMediaFile(
                path=Path("input.mkv"),
                format={},
                streams=[
                    FFmWiz.MuxStreamInfo(index=0, codec_type="video", codec_name="h264"),
                    FFmWiz.MuxStreamInfo(index=1, codec_type="audio", codec_name="aac", language="jpn"),
                ],
            )
        ]
        prompts: list[str] = []
        responses = iter(["", "n", "", "", "", "", ""])

        def fake_ask_raw(prompt: str) -> str:
            prompts.append(prompt.strip())
            return next(responses)

        answers = {"_question_number": 1, "_mux_next_question_number": 2}
        with mock.patch.object(FFmWiz, "ask_raw", side_effect=fake_ask_raw), \
             mock.patch.object(FFmWiz, "note", lambda _message: None):
            FFmWiz.mux_configure_rules(answers, media_files)
            FFmWiz.mux_ask_output_base(answers, Path("input.mkv"))
            FFmWiz.mux_ask_yes_no(answers, "Start Stream Cleanup Remux now?", True)

        visible_numbers = [int(prompt.split(".", 1)[0]) for prompt in prompts]
        self.assertEqual(visible_numbers, [2, 3, 4, 5, 6, 7, 8])
        self.assertIn("Keep input metadata?", prompts[0])
        self.assertIn("Enter output folder path", prompts[-2])
        self.assertIn("Start Stream Cleanup Remux now?", prompts[-1])

    def test_copy_cut_back_from_method_returns_to_output_step(self):
        calls: list[str] = []
        output_calls = {"count": 0}
        method_calls = {"count": 0}
        originals = {
            "step_input_path": FFmWiz.step_input_path,
            "step_output_location": FFmWiz.step_output_location,
            "ask_cut_method": FFmWiz.ask_cut_method,
            "collect_cut_ranges_terminal": FFmWiz.collect_cut_ranges_terminal,
            "ask_continue_default_yes": FFmWiz.ask_continue_default_yes,
            "print_cut_summary": FFmWiz.print_cut_summary,
            "get_video_fps": FFmWiz.get_video_fps,
            "stream_duration_seconds": FFmWiz.stream_duration_seconds,
            "build_output_path": FFmWiz.build_output_path,
        }

        def fake_input(answers):
            calls.append("input")
            answers["input_path"] = Path("input.mkv")
            answers["video_streams"] = [{"codec_type": "video"}]
            answers["audio_streams"] = []
            answers["format"] = {"duration": "100"}

        def fake_output(answers):
            output_calls["count"] += 1
            calls.append("output")
            answers["output_location"] = Path(".")

        def fake_method(_answers):
            method_calls["count"] += 1
            calls.append("method")
            if method_calls["count"] == 1:
                raise FFmWiz.Back()
            return 1

        try:
            FFmWiz.step_input_path = fake_input
            FFmWiz.step_output_location = fake_output
            FFmWiz.ask_cut_method = fake_method
            FFmWiz.collect_cut_ranges_terminal = lambda _answers, fps, duration: calls.append("manual") or [(0.0, 10.0)]
            FFmWiz.ask_continue_default_yes = lambda _answers: calls.append("confirm") or False
            FFmWiz.print_cut_summary = lambda *args, **kwargs: calls.append("summary")
            FFmWiz.get_video_fps = lambda _answers: 25.0
            FFmWiz.stream_duration_seconds = lambda _stream, _fmt=None: 100.0
            FFmWiz.build_output_path = lambda _answers: Path("out.mkv")
            result = FFmWiz._run_copy_cut_mode_impl({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})
        finally:
            for name, original in originals.items():
                setattr(FFmWiz, name, original)

        self.assertIsNone(result)
        self.assertEqual(calls[:4], ["input", "output", "method", "output"])
        self.assertEqual(output_calls["count"], 2)

    def test_step_cuts_rejects_archived_gui_shortcut(self):
        prompts = iter(["g", "n"])
        errors: list[str] = []
        gui_calls = {"count": 0}
        originals = {
            "ask_raw": FFmWiz.ask_raw,
            "open_cut_gui": FFmWiz.open_cut_gui,
            "error": FFmWiz.error,
            "get_video_fps": FFmWiz.get_video_fps,
            "stream_duration_seconds": FFmWiz.stream_duration_seconds,
        }

        def fake_open_cut_gui(_answers, fps, duration):
            gui_calls["count"] += 1
            return None

        try:
            FFmWiz.ask_raw = lambda _prompt: next(prompts)
            FFmWiz.open_cut_gui = fake_open_cut_gui
            FFmWiz.error = lambda message: errors.append(message)
            FFmWiz.get_video_fps = lambda _answers: 25.0
            FFmWiz.stream_duration_seconds = lambda _stream, _fmt=None: 100.0
            answers = {"format": {"duration": "100"}}
            FFmWiz.step_cuts(answers)
        finally:
            for name, original in originals.items():
                setattr(FFmWiz, name, original)

        self.assertEqual(gui_calls["count"], 0)
        self.assertNotIn("cut_keep_ranges", answers)
        self.assertIn("archived", errors[-1])

    def test_run_wizard_uses_unified_editor_before_legacy_video_edit_prompts(self):
        calls: list[str] = []
        originals = {
            "step_input_path": FFmWiz.step_input_path,
            "step_output_location": FFmWiz.step_output_location,
            "step_output_format": FFmWiz.step_output_format,
            "step_video_codec": FFmWiz.step_video_codec,
            "step_use_gpu": FFmWiz.step_use_gpu,
            "step_unified_video_editor_for_encode": FFmWiz.step_unified_video_editor_for_encode,
            "step_video_bitrate": FFmWiz.step_video_bitrate,
            "step_resolution": FFmWiz.step_resolution,
            "step_fps": FFmWiz.step_fps,
            "step_start_now": FFmWiz.step_start_now,
            "ask_raw": FFmWiz.ask_raw,
            "get_video_fps": FFmWiz.get_video_fps,
            "stream_duration_seconds": FFmWiz.stream_duration_seconds,
        }

        def fake_unified(answers):
            calls.append("unified")
            answers.update({
                "_unified_video_editor_used": True,
                "_unified_cut_keep_ranges": [(10.0, 20.0)],
                "_unified_video_speed": 1.5,
                "_unified_reverse_video": True,
                "_unified_include_audio": True,
                "crop_top": 1,
                "crop_left": 2,
                "crop_right": 3,
                "crop_bottom": 4,
            })

        try:
            FFmWiz.step_input_path = lambda answers: calls.append("input")
            FFmWiz.step_output_location = lambda answers: calls.append("output")
            FFmWiz.step_output_format = lambda answers: calls.append("format")
            FFmWiz.step_video_codec = lambda answers: calls.append("codec")
            FFmWiz.step_use_gpu = lambda answers: calls.append("gpu")
            FFmWiz.step_unified_video_editor_for_encode = fake_unified
            FFmWiz.step_video_bitrate = lambda answers: calls.append("bitrate")
            FFmWiz.step_resolution = lambda answers: calls.append("resolution")
            FFmWiz.step_fps = lambda answers: calls.append("fps")
            FFmWiz.step_start_now = lambda answers: calls.append("start")
            FFmWiz.ask_raw = lambda _prompt: (_ for _ in ()).throw(AssertionError("legacy prompt was shown"))
            FFmWiz.get_video_fps = lambda _answers: 30.0
            FFmWiz.stream_duration_seconds = lambda _stream, _fmt=None: 100.0
            answers = {
                "output_ext": "mp4",
                "video_codec": "h264",
                "video_streams": [{"codec_type": "video", "width": 100, "height": 100, "color_range": "tv"}],
                "audio_streams": [],
                "subtitle_streams": [],
                "format": {"duration": "100"},
            }
            FFmWiz.run_wizard(answers)
        finally:
            for name, original in originals.items():
                setattr(FFmWiz, name, original)

        self.assertIn("unified", calls)
        self.assertTrue(answers["crop_enabled"])
        self.assertEqual(answers["cut_keep_ranges"], [(10.0, 20.0)])
        self.assertTrue(answers["video_speed_enabled"])
        self.assertEqual(answers["video_speed_factor"], 1.5)
        self.assertTrue(answers["reverse_video"])

    def test_unified_decline_disables_followup_video_speed_gui(self):
        prompts = iter(["g", "n"])
        errors: list[str] = []
        gui_calls = {"count": 0}
        originals = {
            "ask_raw": FFmWiz.ask_raw,
            "open_video_speed_gui": FFmWiz.open_video_speed_gui,
            "error": FFmWiz.error,
        }
        try:
            FFmWiz.ask_raw = lambda _prompt: next(prompts)
            FFmWiz.open_video_speed_gui = lambda _answers: gui_calls.__setitem__("count", gui_calls["count"] + 1)
            FFmWiz.error = lambda message: errors.append(message)
            answers = {
                "_disable_followup_video_gui_prompts": True,
                "video_streams": [{"codec_type": "video"}],
                "audio_streams": [],
            }
            FFmWiz.step_video_speed_reverse_for_encode(answers)
        finally:
            for name, original in originals.items():
                setattr(FFmWiz, name, original)
        self.assertEqual(gui_calls["count"], 0)
        self.assertFalse(answers["video_speed_enabled"])
        self.assertIn("not available", errors[-1])

    def test_run_wizard_unified_decline_skips_legacy_crop_speed_and_cut_questions(self):
        class StopRun(Exception):
            pass

        calls: list[str] = []
        originals = {
            "step_input_path": FFmWiz.step_input_path,
            "step_join_additional_inputs_for_encode": FFmWiz.step_join_additional_inputs_for_encode,
            "step_output_location": FFmWiz.step_output_location,
            "step_output_format": FFmWiz.step_output_format,
            "step_video_codec": FFmWiz.step_video_codec,
            "step_use_gpu": FFmWiz.step_use_gpu,
            "step_unified_video_editor_for_encode": FFmWiz.step_unified_video_editor_for_encode,
            "step_crop_enabled": FFmWiz.step_crop_enabled,
            "step_video_bitrate": FFmWiz.step_video_bitrate,
            "step_cpu_two_pass": FFmWiz.step_cpu_two_pass,
            "step_resolution": FFmWiz.step_resolution,
            "step_fps": FFmWiz.step_fps,
            "step_video_speed_reverse_for_encode": FFmWiz.step_video_speed_reverse_for_encode,
            "step_cuts": FFmWiz.step_cuts,
            "step_start_now": FFmWiz.step_start_now,
        }
        try:
            FFmWiz.step_input_path = lambda answers: calls.append("input")
            FFmWiz.step_join_additional_inputs_for_encode = lambda answers: calls.append("join")
            FFmWiz.step_output_location = lambda answers: calls.append("output")
            FFmWiz.step_output_format = lambda answers: calls.append("format")
            FFmWiz.step_video_codec = lambda answers: calls.append("codec")
            FFmWiz.step_use_gpu = lambda answers: (calls.append("gpu"), answers.__setitem__("use_gpu", False))
            FFmWiz.step_unified_video_editor_for_encode = lambda answers: (
                calls.append("unified"),
                answers.__setitem__("_unified_video_editor_used", False),
                answers.__setitem__("_unified_video_editor_declined", True),
                answers.__setitem__("crop_enabled", False),
                answers.__setitem__("video_speed_enabled", False),
                answers.__setitem__("cut_keep_ranges", []),
            )
            FFmWiz.step_crop_enabled = lambda answers: calls.append("crop")
            FFmWiz.step_video_bitrate = lambda answers: calls.append("bitrate")
            FFmWiz.step_cpu_two_pass = lambda answers: calls.append("two_pass")
            FFmWiz.step_resolution = lambda answers: calls.append("resolution")
            FFmWiz.step_fps = lambda answers: calls.append("fps")
            FFmWiz.step_video_speed_reverse_for_encode = lambda answers: calls.append("speed")
            FFmWiz.step_cuts = lambda answers: calls.append("cuts")
            FFmWiz.step_start_now = lambda answers: (_ for _ in ()).throw(StopRun())
            with self.assertRaises(StopRun):
                FFmWiz.run_wizard({
                    "output_ext": "mp4",
                    "video_codec": "H265",
                    "video_streams": [{"codec_type": "video", "width": 100, "height": 100, "color_range": "tv"}],
                    "audio_streams": [],
                    "subtitle_streams": [],
                    "format": {"duration": "100"},
                })
        finally:
            for name, original in originals.items():
                setattr(FFmWiz, name, original)
        self.assertIn("unified", calls)
        self.assertNotIn("crop", calls)
        self.assertNotIn("speed", calls)
        self.assertNotIn("cuts", calls)

    def test_cpu_two_pass_commands_use_passlog_and_null_first_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["cpu_two_pass"] = True
            cmd = self.command_for(answers)
            first, second, _passlog = FFmWiz.build_cpu_two_pass_commands(cmd, answers)
        first_text = " ".join(first)
        second_text = " ".join(second)
        self.assertIn("-pass 1", first_text)
        self.assertIn("-f null", first_text)
        self.assertIn("-an", first)
        self.assertIn("-map 0:v:0", first_text)
        self.assertIn("-filter:v", first)
        self.assertNotIn("-map 0:a:0", first_text)
        self.assertNotIn("-c:a", first)
        self.assertNotIn("-map_metadata", first)
        self.assertIn("-pass 2", second_text)
        self.assertEqual(second[-1], cmd[-1])

    def test_cpu_two_pass_first_pass_does_not_copy_full_source_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["cpu_two_pass"] = True
            answers["crop_enabled"] = False
            answers["crop_top"] = answers["crop_left"] = answers["crop_right"] = answers["crop_bottom"] = 0
            answers["resolution"] = None
            answers["fps"] = None
            answers["audio_tracks"] = "all"
            cmd = self.command_for(answers)
            self.assertIn("-map 0", " ".join(cmd))
            first, _second, _passlog = FFmWiz.build_cpu_two_pass_commands(cmd, answers)
        first_text = " ".join(first)
        self.assertIn("-map 0:v:0", first_text)
        self.assertNotIn("-map 0 -map_metadata", first_text)
        self.assertNotIn("-c copy", first_text)
        self.assertNotIn("-c:a", first)
        self.assertIn("-pass 1", first_text)

    def test_print_summary_shows_two_pass_commands_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["cpu_two_pass"] = True
            cmd = self.command_for(answers)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                FFmWiz.print_summary(answers, cmd)
        text = output.getvalue()
        self.assertIn("CPU two-pass pass 1/2", text)
        self.assertIn("CPU two-pass pass 2/2", text)
        self.assertIn("-pass 1", text)
        self.assertIn("-pass 2", text)

    def test_step_use_gpu_skips_prompt_when_gpu_unavailable(self):
        answers = {"ffmpeg": "ffmpeg", "video_encoders": ["libx265"], "gpu_available": False}
        with mock.patch.object(FFmWiz, "ask_yes_no") as ask_yes_no:
            FFmWiz.step_use_gpu(answers)
        self.assertFalse(answers["use_gpu"])
        ask_yes_no.assert_not_called()

    def test_build_command_forces_cpu_when_gpu_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = True
            answers["gpu_available"] = False
            text = self.command_text(answers)
        self.assertNotIn("_nvenc", text)
        self.assertNotIn("-hwaccel cuda", text)
        self.assertIn("libx265", text)

    def test_nvenc_multipass_fullres_added_for_gpu_encode(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["nvenc_multipass"] = "fullres"
            text = self.command_text(answers)
        self.assertIn("-multipass fullres", text)
        self.assertNotIn("-pass 1", text)
        self.assertNotIn("-passlogfile", text)

    def test_nvenc_multipass_disabled_omits_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["nvenc_multipass"] = "disabled"
            text = self.command_text(answers)
        self.assertIn("hevc_nvenc", text)
        self.assertNotIn("-multipass", text)

    def test_nvenc_multipass_not_added_for_cpu_encode(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["nvenc_multipass"] = "fullres"
            text = self.command_text(answers)
        self.assertIn("libx265", text)
        self.assertNotIn("-multipass", text)

    def test_nvenc_multipass_split_applies_to_every_output_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["nvenc_multipass"] = "qres"
            answers["separator_points"] = [1000.0]
            text = self.command_text(answers)
        self.assertEqual(text.count("-multipass qres"), 2)
        self.assertIn("_Part01.mp4", text)
        self.assertIn("_Part02.mp4", text)

    def test_step_nvenc_multipass_defaults_to_qres_for_bitrate_nvenc(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            with mock.patch.object(FFmWiz, "ask_raw", return_value=""):
                FFmWiz.step_nvenc_multipass(answers)
        self.assertEqual(answers["nvenc_multipass"], "qres")

    def test_nvenc_multipass_prompt_skips_cpu_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            self.assertFalse(FFmWiz.nvenc_multipass_prompt_applicable(answers))
        self.assertEqual(answers.get("nvenc_multipass_skip_reason"), "CPU encoder selected")

    def test_run_wizard_question_numbers_continue_after_join_subquestions(self):
        class StopRun(Exception):
            pass

        seen_numbers: list[int] = []
        originals = {
            "step_input_path": FFmWiz.step_input_path,
            "step_join_additional_inputs_for_encode": FFmWiz.step_join_additional_inputs_for_encode,
            "step_output_location": FFmWiz.step_output_location,
        }

        def fake_input(answers):
            answers.update({
                "input_path": Path("a.mkv"),
                "output_ext": "mp4",
                "video_streams": [{"codec_type": "video", "width": 100, "height": 100}],
                "audio_streams": [],
                "subtitle_streams": [],
                "format": {"duration": "10"},
            })

        def fake_join(answers):
            self.assertEqual(answers["_question_number"], 3)
            answers["_join_question_extra"] = 4

        def fake_output(answers):
            seen_numbers.append(int(answers["_question_number"]))
            raise StopRun()

        try:
            FFmWiz.step_input_path = fake_input
            FFmWiz.step_join_additional_inputs_for_encode = fake_join
            FFmWiz.step_output_location = fake_output
            with self.assertRaises(StopRun):
                FFmWiz.run_wizard({"_question_offset": 1})
        finally:
            for name, original in originals.items():
                setattr(FFmWiz, name, original)

        self.assertEqual(seen_numbers, [8])

    def test_step_join_back_resume_preserves_existing_join_items_and_number_extra(self):
        originals = {
            "ask_yes_no": FFmWiz.ask_yes_no,
            "ask_required": FFmWiz.ask_required,
        }
        item = {
            "path": Path("b.mkv"),
            "streams": [],
            "video_streams": [{"codec_type": "video"}],
            "audio_streams": [],
            "format": {},
            "duration": 10.0,
        }
        prompts: list[str] = []

        def fake_yes_no(prompt, default):
            prompts.append(prompt)
            return False

        try:
            FFmWiz.ask_yes_no = fake_yes_no
            FFmWiz.ask_required = lambda _prompt: (_ for _ in ()).throw(AssertionError("path prompt should not be shown"))
            answers = {
                "_question_number": 5,
                "_join_question_extra": 2,
                "input_path": Path("a.mkv"),
                "video_streams": [{"codec_type": "video"}],
                "join_input_items": [item],
            }
            FFmWiz.step_join_additional_inputs_for_encode(answers)
        finally:
            for name, original in originals.items():
                setattr(FFmWiz, name, original)

        self.assertEqual(answers["join_input_items"], [item])
        self.assertEqual(answers["_join_question_extra"], 2)
        self.assertIn("5. Add another video file?", prompts[0])

    def test_graphical_video_requests_include_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            chapters = [self.chapter(10.0, 20.0, "Opening")]
            answers["probe"] = {"chapters": chapters}
            captured: list[dict] = []

            def fake_launch(request):
                captured.append(request)
                if request["mode"] == "video_unified":
                    return {
                        "status": "ok",
                        "margins": [0, 0, 0, 0],
                        "keep_ranges": [],
                        "speed": 1.0,
                        "reverse": False,
                        "include_audio": True,
                    }
                return {"status": "ok", "keep_ranges": [[0.0, 10.0]]}

            with mock.patch.object(FFmWiz, "_launch_qt_gui", side_effect=fake_launch):
                self.assertIsNotNone(FFmWiz.open_unified_video_gui(answers))
                self.assertEqual(captured[-1]["chapters"], chapters)
                self.assertEqual(FFmWiz.open_cut_gui(answers, fps=30.0, duration=100.0), [(0.0, 10.0)])
                self.assertEqual(captured[-1]["chapters"], chapters)

    def test_atempo_filter_chain_splits_extreme_speed(self):
        self.assertEqual(FFmWiz.atempo_filter_chain(4.0), "atempo=2,atempo=2")
        self.assertEqual(FFmWiz.atempo_filter_chain(0.25), "atempo=0.5,atempo=0.5")

    def test_video_speed_reverse_command_uses_timestamp_and_audio_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "speed_factor": 1.25,
                "reverse_video": True,
                "include_audio": True,
                "crop_enabled": False,
                "resolution": "n",
                "fps": None,
            })
            cmd = FFmWiz.build_video_speed_reverse_command(answers)
            text = " ".join(cmd)
        self.assertIn("reverse,setpts=(PTS-STARTPTS)/1.25", text)
        self.assertIn("[0:a:0]areverse,asetpts=PTS-STARTPTS,atempo=1.25[aspd0]", text)
        self.assertIn("-map [aspd0]", text)
        self.assertIn("-c:v libx264", text)
        self.assertIn("-c:a aac", text)
        self.assertNotIn("-c copy", text)

    def test_video_speed_reverse_command_syncs_all_audio_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["audio_streams"].append({
                "codec_type": "audio",
                "codec_name": "aac",
                "bit_rate": "96000",
                "duration": "6074.221",
            })
            answers.update({
                "speed_factor": 1.25,
                "reverse_video": True,
                "include_audio": True,
                "crop_enabled": False,
                "resolution": "n",
                "fps": None,
            })
            cmd = FFmWiz.build_video_speed_reverse_command(answers)
            text = " ".join(cmd)
        self.assertIn("[0:a:0]areverse,asetpts=PTS-STARTPTS,atempo=1.25[aspd0]", text)
        self.assertIn("[0:a:1]areverse,asetpts=PTS-STARTPTS,atempo=1.25[aspd1]", text)
        self.assertIn("-map [aspd0]", text)
        self.assertIn("-map [aspd1]", text)

    def test_reverse_segments_split_and_concat_in_reverse_order(self):
        chunks = FFmWiz.split_ranges_for_reverse_segments([(0.0, 130.0)], 130.0, 60.0)
        self.assertEqual(chunks, [(0.0, 60.0), (60.0, 120.0), (120.0, 130.0)])
        chunks = FFmWiz.split_ranges_for_reverse_segments([(10.0, 70.0), (100.0, 130.0)], 140.0, 30.0)
        self.assertEqual(chunks, [(10.0, 40.0), (40.0, 70.0), (100.0, 130.0)])

    def test_video_speed_reverse_segment_command_trims_before_reverse(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "speed_factor": 1.5,
                "reverse_video": True,
                "include_audio": True,
            })
            cmd = FFmWiz.build_video_speed_reverse_segment_command(
                answers,
                60.0,
                75.0,
                Path(tmp) / "seg_0001.mp4",
            )
            text = " ".join(cmd)
        self.assertIn("-ss 60", text)
        self.assertIn("-t 15", text)
        self.assertIn("reverse,setpts=(PTS-STARTPTS)/1.5", text)
        self.assertIn("[0:a:0]areverse,asetpts=PTS-STARTPTS,atempo=1.5[aspd0]", text)

    def test_main_reverse_segment_command_uses_single_range_not_full_clip_reverse(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_speed_enabled": True,
                "video_speed_factor": 1.5,
                "reverse_video": True,
                "use_gpu": False,
            })
            cmd = FFmWiz.build_main_encode_reverse_segment_command(
                answers,
                30.0,
                45.0,
                Path(tmp) / "seg_0001.mp4",
            )
            text = " ".join(cmd)
        self.assertIn("-ss 30.000000", text)
        self.assertIn("-t 15.000000", text)
        self.assertIn("reverse,setpts=(PTS-STARTPTS)/1.5", text)
        self.assertNotIn("concat=n=1", text)

    def test_main_encode_video_speed_reverse_is_part_of_normal_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_speed_enabled": True,
                "video_speed_factor": 1.5,
                "reverse_video": True,
                "use_gpu": True,
            })
            text = self.command_text(answers)
        self.assertIn("reverse,setpts=(PTS-STARTPTS)/1.5", text)
        self.assertIn("hevc_nvenc", text)
        self.assertIn("-hwaccel cuda -hwaccel_device 0 -i", text)
        self.assertNotIn("scale_cuda", text)
        self.assertNotIn("-hwaccel_output_format cuda", text)

    def test_main_encode_video_speed_from_gui_respects_selected_audio_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["audio_streams"].append({
                "codec_type": "audio",
                "codec_name": "aac",
                "bit_rate": "96000",
                "duration": "6074.221",
            })
            answers.update({
                "video_speed_enabled": True,
                "video_speed_factor": 1.25,
                "reverse_video": True,
                "audio_speed_from_video": True,
                "audio_tracks": [0],
                "use_gpu": False,
            })
            text = self.command_text(answers)
        self.assertIn("[0:a:0]areverse,atempo=1.25,asetpts=PTS-STARTPTS[aout0]", text)
        self.assertNotIn("[0:a:1]areverse,atempo=1.25,asetpts=PTS-STARTPTS[aout1]", text)
        self.assertIn("-map [aout0]", text)
        self.assertNotIn("-map [aout1]", text)

    def test_split_encode_respects_selected_audio_tracks_when_syncing_video_speed(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["audio_streams"].extend([
                {"codec_type": "audio", "codec_name": "aac", "bit_rate": "122000", "duration": "6074.221"},
                {"codec_type": "audio", "codec_name": "aac", "bit_rate": "2000", "duration": "6074.221"},
            ])
            answers.update({
                "audio_tracks": [0],
                "audio_speed_from_video": True,
                "separator_points": [3000.0],
                "video_speed_enabled": True,
                "video_speed_factor": 1.0,
            })
            text = self.command_text(answers)
        self.assertIn("-map [saout0_0]", text)
        self.assertIn("-map [saout1_0]", text)
        self.assertNotIn("[afinal1]asplit", text)
        self.assertNotIn("[afinal2]asplit", text)
        self.assertNotIn("-map [saout0_1]", text)
        self.assertNotIn("-map [saout0_2]", text)

    def test_split_encode_inserts_loudnorm_for_selected_audio_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["audio_streams"].append({
                "codec_type": "audio",
                "codec_name": "aac",
                "bit_rate": "122000",
                "duration": "6074.221",
            })
            answers.update({
                "audio_tracks": [0],
                "separator_points": [3000.0],
                "loudnorm_enabled": True,
                "loudnorm_target_i": -16.0,
                "loudnorm_measured": {
                    "input_i": -8.54,
                    "input_tp": 1.74,
                    "input_lra": 7.6,
                    "input_thresh": -19.29,
                    "target_offset": -1.22,
                },
            })
            text = self.command_text(answers)
        self.assertIn("loudnorm=I=-16:TP=-1.5:LRA=11", text)
        self.assertIn("[afinal0]asplit", text)
        self.assertNotIn("[afinal1]asplit", text)

    def test_main_encode_audio_cut_and_speed_use_audio_filter_complex(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "audio_cut_keep_ranges": [(0.0, 2.0), (4.0, 5.0)],
                "audio_speed_enabled": True,
                "audio_speed_factor": 0.5,
                "reverse_audio": True,
            })
            text = self.command_text(answers)
        self.assertIn("[0:a:0]asplit=2[acut0_src0][acut0_src1]", text)
        self.assertIn("[acut0_src0]atrim=start=0.000000:end=2.000000", text)
        self.assertNotIn("[0:a:0]atrim=start=0.000000:end=2.000000", text)
        self.assertIn("concat=n=2:v=0:a=1[acut0]", text)
        self.assertIn("[acut0]areverse,atempo=0.5,asetpts=PTS-STARTPTS[aout0]", text)
        self.assertIn("-map [aout0]", text)

    def test_multi_range_video_cut_splits_raw_video_and_audio_before_trim(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "cut_keep_ranges": [(0.0, 2.0), (4.0, 5.0), (7.0, 9.0)],
                "use_gpu": False,
            })
            text = self.command_text(answers)
        self.assertIn("[0:v:0]split=3[vsrc0][vsrc1][vsrc2]", text)
        self.assertIn("[vsrc1]trim=start=4.000000:end=5.000000,setpts=PTS-STARTPTS[v1]", text)
        self.assertIn("[0:a:0]asplit=3[asrc0][asrc1][asrc2]", text)
        self.assertIn("[asrc2]atrim=start=7.000000:end=9.000000,asetpts=PTS-STARTPTS[a2]", text)
        self.assertNotIn("[0:v:0]trim=start=4.000000", text)
        self.assertNotIn("[0:a:0]atrim=start=4.000000", text)

    def test_main_encode_loudnorm_forces_audio_filter_and_aac_when_copy_was_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "audio_codec": "copy",
                "loudnorm_enabled": True,
                "loudnorm_target_i": -16.0,
                "loudnorm_measured": {
                    "input_i": -23.4,
                    "input_tp": -5.1,
                    "input_lra": 4.2,
                    "input_thresh": -33.9,
                    "target_offset": -0.3,
                },
            })
            text = self.command_text(answers)
        self.assertIn("loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=-23.4", text)
        self.assertIn("-c:a aac", text)
        self.assertNotIn("-c:a copy", text)

    def test_main_encode_normalizes_opus_audio_alias_to_libopus(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["audio_codec"] = "opus"
            text = self.command_text(answers)
        self.assertIn("-c:a libopus", text)
        self.assertNotIn("-c:a opus", text)
        self.assertEqual(answers["audio_codec"], "libopus")

    def test_main_encode_libopus_keeps_requested_audio_bitrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["audio_codec"] = "opus"
            answers["audio_bitrate_kbps"] = 160
            text = self.command_text(answers)
        self.assertIn("-c:a libopus", text)
        self.assertIn("-b:a 160k", text)

    def test_main_encode_preserves_all_selected_audio_metadata_and_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["audio_streams"].append({
                "codec_type": "audio",
                "codec_name": "aac",
                "bit_rate": "96000",
                "duration": "6074.221",
            })
            answers["audio_tracks"] = "all"
            text = self.command_text(answers)
        self.assertIn("-map 0", text)
        self.assertNotIn("-map 0:a:0", text)
        self.assertNotIn("-map 0:a:1", text)
        self.assertIn("-map_metadata 0", text)
        self.assertIn("-map_chapters 0", text)

    def test_main_encode_source_extra_policy_can_remove_metadata_chapters_subtitles_and_fonts(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["output_ext"] = "mkv"
            answers["probe"] = {"chapters": [self.chapter(0, 5, "Intro")]}
            answers["format"] = {"duration": "6074.221", "tags": {"title": "Source title"}}
            answers["subtitle_streams"] = [{"index": 2, "codec_type": "subtitle", "codec_name": "ass"}]
            answers["subtitle_tracks"] = "all"
            answers["attachment_streams"] = [
                {"index": 3, "codec_type": "attachment", "codec_name": "ttf", "tags": {"filename": "Font.ttf"}},
            ]
            answers["data_streams"] = [{"index": 4, "codec_type": "data", "codec_name": "bin_data"}]
            answers["keep_source_metadata"] = False
            answers["keep_source_chapters"] = False
            answers["keep_source_subtitles"] = False
            answers["keep_source_data_streams"] = False
            answers["keep_embedded_attachments"] = False
            text = self.command_text(answers)
        self.assertIn("-map_metadata -1", text)
        self.assertIn("-map_chapters -1", text)
        self.assertNotIn("-map 0:s:0", text)
        self.assertNotIn("-map 0:t?", text)
        self.assertIn("-sn", text)
        self.assertIn("-dn", text)

    def test_main_encode_keeps_source_subtitle_and_data_streams_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["output_ext"] = "mkv"
            answers["subtitle_streams"] = [{"index": 2, "codec_type": "subtitle", "codec_name": "ass"}]
            answers["subtitle_tracks"] = "all"
            answers["data_streams"] = [{"index": 3, "codec_type": "data", "codec_name": "bin_data"}]
            answers["keep_source_metadata"] = True
            answers["keep_source_chapters"] = True
            answers["keep_source_subtitles"] = True
            answers["keep_source_data_streams"] = True
            text = self.command_text(answers)
        self.assertIn("-map 0:s:0", text)
        self.assertIn("-map 0:d:0", text)
        self.assertIn("-c:s copy", text)
        self.assertIn("-c:d copy", text)
        self.assertNotIn("-sn", text)
        self.assertNotIn("-dn", text)

    def test_main_encode_keeps_additional_source_video_streams_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["output_ext"] = "mkv"
            answers["use_gpu"] = False
            answers["video_streams"].append({
                "index": 3,
                "codec_type": "video",
                "codec_name": "mjpeg",
                "disposition": {"attached_pic": 1},
            })
            answers["keep_source_metadata"] = True
            answers["keep_source_extra_video_streams"] = True
            cmd = self.command_for(answers)
            text = " ".join(cmd)
        self.assertIn("-map 0:v:0", text)
        self.assertIn("-map 0:v:1", text)
        self.assertIn("-filter:v:0", text)
        self.assertIn("-c:v:1 copy", text)

    def test_main_encode_keep_all_tracks_uses_full_source_map_for_simple_encode(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["output_ext"] = "mkv"
            answers["use_gpu"] = False
            answers["crop_enabled"] = False
            answers["resolution"] = None
            answers["fps"] = None
            answers["audio_tracks"] = "all"
            answers["audio_codec"] = "opus"
            answers["keep_source_metadata"] = True
            answers["keep_source_chapters"] = True
            answers["keep_source_subtitles"] = True
            answers["keep_source_data_streams"] = True
            answers["keep_source_extra_video_streams"] = True
            cmd = self.command_for(answers)
            text = " ".join(cmd)
        self.assertIn("-map 0", text)
        self.assertNotIn("-map 0:v:0", text)
        self.assertNotIn("-map 0:a:0", text)
        self.assertIn("-c copy", text)
        self.assertIn("-filter:v:0", text)
        self.assertIn("-c:v:0 libx265", text)
        self.assertIn("-c:a libopus", text)
        self.assertIn("-map_metadata 0", text)
        self.assertIn("-map_chapters 0", text)

    def test_main_encode_drops_additional_source_video_streams_when_policy_removes_extras(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["output_ext"] = "mkv"
            answers["use_gpu"] = False
            answers["video_streams"].append({
                "index": 3,
                "codec_type": "video",
                "codec_name": "mjpeg",
                "disposition": {"attached_pic": 1},
            })
            answers["keep_source_metadata"] = False
            answers["keep_source_extra_video_streams"] = False
            text = self.command_text(answers)
        self.assertIn("-map 0:v:0", text)
        self.assertNotIn("-map 0:v:1", text)

    def test_main_encode_does_not_print_sn_dn_when_no_subtitle_or_data_streams_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["output_ext"] = "mkv"
            answers["keep_source_metadata"] = True
            answers["keep_source_chapters"] = True
            answers["keep_source_subtitles"] = True
            answers["keep_source_data_streams"] = True
            text = self.command_text(answers)
        self.assertNotIn("-sn", text)
        self.assertNotIn("-dn", text)

    def test_config_source_extra_policy_can_remove_subtitles_and_metadata(self):
        answers = self.base_answers(".")
        answers["subtitle_streams"] = [{"index": 2, "codec_type": "subtitle", "codec_name": "ass"}]
        answers["data_streams"] = [{"index": 3, "codec_type": "data", "codec_name": "bin_data"}]
        config = {"settings": {"keep_source_metadata": "n", "subtitle_tracks": "all", "keep_embedded_attachments": "y"}}
        FFmWiz.apply_config_source_extra_options(answers, config)
        FFmWiz.apply_config_subtitle_options(answers, config)
        self.assertFalse(answers["keep_source_metadata"])
        self.assertFalse(answers["keep_source_chapters"])
        self.assertFalse(answers["keep_source_subtitles"])
        self.assertFalse(answers["keep_source_data_streams"])
        self.assertFalse(answers["keep_embedded_attachments"])
        self.assertEqual(answers["subtitle_tracks"], [])

    def test_cpu_main_encode_8bit_hevc_uses_main_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["video_streams"][0]["pix_fmt"] = "yuv420p"
            answers["video_streams"][0]["bits_per_raw_sample"] = "8"
            text = self.command_text(answers)
        self.assertIn("-c:v libx265", text)
        self.assertIn("-profile:v main", text)
        self.assertIn("format=yuv420p", text)

    def test_cpu_main_encode_uses_single_output_color_range_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            text = self.command_text(answers)
        self.assertNotIn("setparams=range=tv", text)
        self.assertIn("-color_range:v:0 tv", text)

    def test_cpu_main_encode_preserves_10bit_source_pixel_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["video_streams"][0]["pix_fmt"] = "yuv420p10le"
            answers["video_streams"][0]["bits_per_raw_sample"] = "10"
            text = self.command_text(answers)
        self.assertIn("format=yuv420p10le", text)
        self.assertIn("-profile:v main10", text)
        self.assertNotIn("format=yuv420p ", text)

    def test_cuda_main_encode_preserves_10bit_source_pixel_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["video_streams"][0]["pix_fmt"] = "yuv420p10le"
            answers["video_streams"][0]["bits_per_raw_sample"] = "10"
            text = self.command_text(answers)
        self.assertIn("scale_cuda=w=852:h=480:format=p010le", text)
        self.assertIn("-profile:v main10", text)
        self.assertNotIn("format=nv12", text)

    def test_cuda_request_for_12bit_source_uses_cpu_high_bit_depth_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["video_streams"][0]["pix_fmt"] = "yuv420p12le"
            answers["video_streams"][0]["bits_per_raw_sample"] = "12"
            text = self.command_text(answers)
        self.assertIn("format=yuv420p12le", text)
        self.assertIn("-c:v libx265", text)
        self.assertIn("-profile:v main12", text)
        self.assertNotIn("hevc_nvenc", text)
        self.assertNotIn("format=p010le", text)

    def test_cpu_main_encode_preserves_14bit_source_pixel_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["video_streams"][0]["pix_fmt"] = "yuv420p14le"
            answers["video_streams"][0]["bits_per_raw_sample"] = "14"
            text = self.command_text(answers)
        self.assertIn("format=yuv420p14le", text)
        self.assertIn("-profile:v rext", text)

    def test_cpu_main_encode_caps_above_16bit_source_to_16bit_pixel_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["video_streams"][0]["pix_fmt"] = "yuv420p16le"
            answers["video_streams"][0]["bits_per_raw_sample"] = "24"
            text = self.command_text(answers)
        self.assertIn("format=yuv420p16le", text)
        self.assertIn("-profile:v rext", text)
        self.assertEqual(FFmWiz.output_video_bit_depth(answers), 16)

    def test_audio_track_selection_n_means_all_tracks(self):
        self.assertEqual(FFmWiz.parse_selection_config("n", 3, [0]), "all")
        self.assertEqual(
            FFmWiz.selected_audio_streams({
                "audio_tracks": "all",
                "audio_streams": [{}, {}, {}],
            }),
            [0, 1, 2],
        )

    def test_split_encode_normalizes_opus_audio_alias_to_libopus(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "audio_codec": "opus",
                "separator_points": [120.0],
            })
            text = self.command_text(answers)
        self.assertIn("-c:a libopus", text)
        self.assertNotIn("-c:a opus", text)
        self.assertEqual(answers["audio_codec"], "libopus")

    def test_audio_speed_reverse_command_outputs_audio_only_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "output_location": Path(tmp),
                "audio_index": 0,
                "speed_factor": 0.5,
                "reverse_audio": True,
            })
            cmd = FFmWiz.build_audio_speed_reverse_command(answers)
            text = " ".join(cmd)
        self.assertIn("-map 0:a:0", text)
        self.assertIn("-vn -sn -dn", text)
        self.assertIn("areverse,asetpts=PTS-STARTPTS,atempo=0.5", text)
        self.assertIn("-c:a aac", text)

    def test_audio_speed_reverse_mp3_output_uses_mp3_codec(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "input_path": Path("sample.mp3"),
                "output_location": Path(tmp),
                "audio_index": 0,
                "speed_factor": 0.5,
                "reverse_audio": True,
            })
            cmd = FFmWiz.build_audio_speed_reverse_command(answers)
            text = " ".join(cmd)
        self.assertIn("-c:a libmp3lame", text)
        self.assertNotIn("-c:a aac", text)
        self.assertTrue(str(answers["output_path"]).lower().endswith(".mp3"))

    def test_audio_cut_wav_output_uses_pcm_codec(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "input_path": Path("sample.wav"),
                "output_location": Path(tmp),
                "audio_index": 0,
                "audio_keep_ranges": [(0.0, 1.0)],
            })
            cmd = FFmWiz.build_audio_cut_command(answers)
            text = " ".join(cmd)
        self.assertIn("-c:a pcm_s16le", text)
        self.assertNotIn("-b:a", text)
        self.assertTrue(str(answers["output_path"]).lower().endswith(".wav"))

    def test_audio_transform_command_combines_cut_and_speed(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "output_location": Path(tmp),
                "audio_index": 0,
                "audio_cut_keep_ranges": [(0.0, 2.0), (4.0, 6.0)],
                "audio_speed_enabled": True,
                "audio_speed_factor": 0.5,
                "reverse_audio": True,
            })
            cmd = FFmWiz.build_audio_transform_command(answers)
            text = " ".join(cmd)
        self.assertIn("atrim=start=0.000000:end=2.000000", text)
        self.assertIn("concat=n=2:v=0:a=1", text)
        self.assertIn("areverse,atempo=0.5,asetpts=PTS-STARTPTS", text)
        self.assertIn("-map [aout0]", text)
        self.assertIn("-c:a aac", text)

    def test_audio_cut_multiple_ranges_uses_atrim_concat(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "output_location": Path(tmp),
                "audio_index": 0,
                "audio_keep_ranges": [(0.0, 2.5), (5.0, 7.0)],
            })
            cmd = FFmWiz.build_audio_cut_command(answers)
            text = " ".join(cmd)
        self.assertIn("-filter_complex", text)
        self.assertIn("[0:a:0]asplit=2[acut_src0][acut_src1]", text)
        self.assertIn("[acut_src0]atrim=start=0.000000:end=2.500000", text)
        self.assertIn("[acut_src1]atrim=start=5.000000:end=7.000000", text)
        self.assertIn("concat=n=2:v=0:a=1[a]", text)
        self.assertIn("-map [a]", text)
        self.assertNotIn("[0:a:0]atrim=start=5.000000", text)

    def test_audio_cut_single_range_uses_ss_i_t(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "output_location": Path(tmp),
                "audio_index": 0,
                "audio_keep_ranges": [(10.0, 15.0)],
            })
            cmd = FFmWiz.build_audio_cut_command(answers)
            text = " ".join(cmd)
        self.assertIn("-ss 10.000000 -i Pato12.mkv -t 5.000000", text)
        self.assertNotIn("-to", text)
        self.assertNotIn("filter_complex", text)

    def test_audio_only_transform_prompts_do_not_show_for_video_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            self.assertFalse(FFmWiz.audio_only_transform_prompt_applicable(answers))
            answers["output_ext"] = "mp3"
            self.assertTrue(FFmWiz.audio_only_transform_prompt_applicable(answers))
            answers["audio_tracks"] = []
            self.assertFalse(FFmWiz.audio_only_transform_prompt_applicable(answers))

    # ===================================================================
    # Tests for Issue 1: Chapter handling after timeline modifications
    # ===================================================================

    def test_multi_range_cut_with_chapters_removes_deleted_and_remaps_retained(self):
        """Multi-range cut: deleted chapters are removed, retained chapters get corrected timestamps."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["probe"] = {"chapters": [
                self.chapter(0, 100, "Intro"),
                self.chapter(100, 300, "Deleted"),
                self.chapter(300, 600, "Middle"),
                self.chapter(600, 900, "End"),
            ]}
            answers["format"] = {"duration": "900.0"}
            # Keep ranges: [0-100] and [300-900] → delete 100-300
            answers["cut_keep_ranges"] = [(0, 100), (300, 900)]
            plan = FFmWiz.remap_chapters_for_encode(answers, speed_factor=1.0)
            self.assertEqual(plan["mode"], "metadata")
            chapters = plan["chapters"]
            # "Intro" should remain at [0, 100]
            self.assertAlmostEqual(chapters[0]["start"], 0.0, places=2)
            self.assertAlmostEqual(chapters[0]["end"], 100.0, places=2)
            self.assertEqual(chapters[0]["metadata"]["title"], "Intro")
            # "Deleted" should be removed (falls within deleted range 100-300)
            titles = [ch["metadata"].get("title") for ch in chapters]
            self.assertNotIn("Deleted", titles)
            # "Middle" should start at 100 (offset by kept range 0-100)
            self.assertAlmostEqual(chapters[1]["start"], 100.0, places=2)
            self.assertAlmostEqual(chapters[1]["end"], 400.0, places=2)
            self.assertEqual(chapters[1]["metadata"]["title"], "Middle")

    def test_split_into_two_parts_remaps_chapters_per_part(self):
        """Split into 2 parts: each part gets only its own remapped chapters at relative time zero."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["probe"] = {"chapters": [
                self.chapter(0, 200, "Chapter 1"),
                self.chapter(200, 400, "Chapter 2"),
                self.chapter(400, 600, "Chapter 3"),
            ]}
            answers["format"] = {"duration": "600.0"}
            answers["separator_points"] = [300.0]
            answers["cut_keep_ranges"] = []
            answers.pop("video_speed_enabled", None)
            answers.pop("reverse_video", None)
            # Part 1: [0, 300]
            plan1 = FFmWiz.remap_chapters_for_encode(answers, speed_factor=1.0, part_interval=(0.0, 300.0))
            self.assertEqual(plan1["mode"], "metadata")
            ch1_titles = [ch["metadata"].get("title") for ch in plan1["chapters"]]
            self.assertIn("Chapter 1", ch1_titles)
            self.assertIn("Chapter 2", ch1_titles)
            self.assertNotIn("Chapter 3", ch1_titles)
            # Part 1 chapters start at zero-relative time
            self.assertAlmostEqual(plan1["chapters"][0]["start"], 0.0, places=2)
            self.assertAlmostEqual(plan1["chapters"][0]["end"], 200.0, places=2)
            # Part 2: [300, 600]
            plan2 = FFmWiz.remap_chapters_for_encode(answers, speed_factor=1.0, part_interval=(300.0, 600.0))
            self.assertEqual(plan2["mode"], "metadata")
            ch2_titles = [ch["metadata"].get("title") for ch in plan2["chapters"]]
            self.assertNotIn("Chapter 1", ch2_titles)
            self.assertIn("Chapter 3", ch2_titles)
            # Part 2 chapter timestamps start at zero (relative to Part 2 start)
            for ch in plan2["chapters"]:
                self.assertGreaterEqual(ch["start"], 0.0)

    def test_timeline_modified_with_unavailable_chapters_uses_map_chapters_minus_one(self):
        """When timeline is modified and no chapters exist in source, command must contain -map_chapters -1."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["probe"] = {"chapters": []}
            answers["format"] = {"duration": "600.0"}
            answers["cut_keep_ranges"] = [(0, 300)]
            text = self.command_text(answers)
            self.assertIn("-map_chapters -1", text)
            self.assertNotIn("-map_chapters 0", text)

    def test_unmodified_timeline_preserves_chapters(self):
        """When no timeline modification, command uses -map_chapters 0."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.pop("cut_keep_ranges", None)
            answers.pop("separator_points", None)
            answers.pop("video_speed_enabled", None)
            answers.pop("reverse_video", None)
            answers.pop("join_input_items", None)
            answers["probe"] = {"chapters": [self.chapter(0, 100, "Test")]}
            answers["format"] = {"duration": "600.0"}
            text = self.command_text(answers)
            self.assertIn("-map_chapters 0", text)

    def test_multi_range_cut_timeline_modified_with_chapters_injects_metadata(self):
        """Multi-range cut with chapters: command uses metadata input for chapter remapping."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["probe"] = {"chapters": [
                self.chapter(0, 100, "Intro"),
                self.chapter(100, 300, "Deleted"),
                self.chapter(300, 600, "End"),
            ]}
            answers["format"] = {"duration": "600.0"}
            answers["cut_keep_ranges"] = [(0, 100), (300, 600)]
            text = self.command_text(answers)
            # Should have -map_chapters 1 (referencing the metadata input)
            self.assertIn("-map_chapters 1", text)
            self.assertNotIn("-map_chapters 0", text)
            # Cleanup
            FFmWiz.cleanup_encode_chapter_metadata(answers)

    # ===================================================================
    # Tests for Issue 2: Preserve source display aspect ratio during resize
    # ===================================================================

    def test_16x9_source_into_1146x480_preserves_aspect_ratio_with_padding(self):
        """Anamorphic 4:3 source (SAR 16:15) scaled to a 16:9 target uses display AR for dimensions."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            # Anamorphic 4:3 source: coded 720x576 with SAR 16:15 → display 768x576 (4:3)
            answers["video_streams"] = [{"codec_type": "video", "codec_name": "h264", "width": 720, "height": 576, "sample_aspect_ratio": "16:15"}]
            answers["crop_enabled"] = False
            answers["use_gpu"] = False
            answers["resolution"] = FFmWiz.parse_resolution("480p")
            text = self.command_text(answers)
            # The display AR is 4:3, so 480p should produce ~640x480 (4:3), NOT 600x480 (which would be from coded 720:576 = 5:4)
            dims = answers.get("final_resolution")
            self.assertIsNotNone(dims)
            out_ar = dims[0] / dims[1]
            display_ar = 768 / 576  # 4:3
            self.assertAlmostEqual(out_ar, display_ar, delta=0.02)
            # reset_sar=1 is inside scale filter; no trailing setsar=1
            self.assertIn("reset_sar=1", text)
            self.assertIn("scale=", text)

    def test_matching_aspect_ratio_no_effective_padding(self):
        """Source whose aspect ratio matches the target: pad is present but is a no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            # 16:9 source → 480p preset → should compute ~854x480 (matching AR)
            answers["video_streams"] = [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]
            answers["crop_enabled"] = False
            answers["use_gpu"] = False
            answers["resolution"] = FFmWiz.parse_resolution("480p")
            text = self.command_text(answers)
            # Preset mode uses force_original_aspect_ratio + pad, but when AR
            # matches the pad adds zero pixels (scale output == pad dimensions).
            self.assertIn("force_original_aspect_ratio=decrease", text)
            self.assertIn("scale=", text)
            # The final dimensions must be valid and even
            dims = answers.get("final_resolution")
            self.assertIsNotNone(dims)
            self.assertEqual(dims[0] % 2, 0)
            self.assertEqual(dims[1] % 2, 0)

    def test_explicit_stretch_mode_uses_exact_dimensions(self):
        """Explicit Stretch mode: exact requested dimensions are used without padding."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["video_streams"] = [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]
            answers["crop_enabled"] = False
            answers["use_gpu"] = False
            answers["resolution"] = FFmWiz.parse_resolution("stretch:1146x480")
            text = self.command_text(answers)
            # Stretch mode must use exact dimensions without force_original_aspect_ratio or pad
            self.assertIn("scale=1146:480", text)
            self.assertNotIn("force_original_aspect_ratio", text)
            self.assertNotIn("pad=", text)

    def test_sar_aware_resolution_uses_display_dimensions(self):
        """Source with non-square pixels (SAR != 1:1) should use display AR for scaling."""
        # Anamorphic source: 720x576 with SAR 16:15 → display 768x576 (4:3)
        answers = {
            "video_streams": [{"codec_type": "video", "codec_name": "h264", "width": 720, "height": 576, "sample_aspect_ratio": "16:15"}],
            "crop_enabled": False,
        }
        # Check that display size accounts for SAR
        disp_w, disp_h = FFmWiz.cropped_display_size(answers)
        # 720 * (16/15) ≈ 768
        self.assertAlmostEqual(disp_w / disp_h, 768 / 576, delta=0.02)

    def test_parse_sar_value_handles_various_formats(self):
        """parse_sar_value handles ratio strings, floats, and edge cases."""
        self.assertAlmostEqual(FFmWiz.parse_sar_value("16:15"), 16 / 15, places=4)
        self.assertAlmostEqual(FFmWiz.parse_sar_value("4/3"), 4 / 3, places=4)
        self.assertAlmostEqual(FFmWiz.parse_sar_value("1.333"), 1.333, places=3)
        self.assertEqual(FFmWiz.parse_sar_value(None), 1.0)
        self.assertEqual(FFmWiz.parse_sar_value(""), 1.0)
        self.assertEqual(FFmWiz.parse_sar_value("N/A"), 1.0)
        self.assertEqual(FFmWiz.parse_sar_value("0:0"), 1.0)
        self.assertEqual(FFmWiz.parse_sar_value("1:1"), 1.0)

    # ===================================================================
    # Comprehensive resize / aspect-ratio tests for Issue 2 final fix
    # ===================================================================

    def test_exact_failing_workflow_multi_cut_crop_split_uses_ar_safe_scale(self):
        """Test 1: Exact reported failing workflow — multi-range trim + concat +
        crop + 480p box target + Split into 2 Parts + NVENC. Must produce
        force_original_aspect_ratio + pad, NOT plain scale=WxH,setsar=1."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 2876, "height": 1442, "avg_frame_rate": "30/1", "color_range": "tv"}],
                "crop_enabled": True,
                "crop_left": 420, "crop_right": 736,
                "crop_top": 179, "crop_bottom": 184,
                "fps": 4,
                "resolution": FFmWiz.parse_resolution("1018x480"),
                "use_gpu": True,
                "video_codec": "H265",
                "separator_points": [3000.0],
                "cut_keep_ranges": [(10, 4000), (5000, 7000)],
                "format": {"duration": "8000.0"},
                "audio_speed_from_video": False,
            })
            text = self.command_text(answers)
            # Must contain AR-safe resize
            self.assertIn("force_original_aspect_ratio=decrease", text)
            self.assertIn("pad=1018:480", text)
            # Must NOT contain plain stretch scale
            self.assertNotIn("scale=1018:480,setsar=1", text)
            # Must still use expected settings (top normalized 179->178 for 4:2:0 origin)
            self.assertIn("crop=iw-420-736:ih-178-184:420:178", text)
            self.assertIn("fps=4", text)
            self.assertIn("hevc_nvenc", text)
            self.assertIn("-map_chapters -1", text)
            # Must produce two split output parts
            self.assertIn("_Part01", text)
            self.assertIn("_Part02", text)
            # Cleanup
            FFmWiz.cleanup_encode_chapter_metadata(answers)

    def test_16x9_source_into_wider_canvas_uses_padding(self):
        """Test 2: 16:9 source into 1018x480 box canvas (wider than 16:9).
        Content AR must remain ~16:9 with horizontal padding."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 1920, "height": 1080}],
                "crop_enabled": False,
                "use_gpu": False,
                "resolution": FFmWiz.parse_resolution("1018x480"),
            })
            text = self.command_text(answers)
            # Must contain AR-safe resize with padding
            self.assertIn("force_original_aspect_ratio=decrease", text)
            self.assertIn("pad=1018:480:(ow-iw)/2:(oh-ih)/2", text)
            # Must NOT have plain stretch
            self.assertNotIn("scale=1018:480,setsar", text)
            # The target canvas must be exactly 1018x480
            self.assertEqual(answers["final_resolution"], (1018, 480))

    def test_stretch_mode_no_ar_preservation(self):
        """Test 4: Explicit Stretch mode generates exact scale without AR logic."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 1920, "height": 1080}],
                "crop_enabled": False,
                "use_gpu": False,
                "resolution": FFmWiz.parse_resolution("stretch:1018x480"),
            })
            text = self.command_text(answers)
            self.assertIn("scale=1018:480", text)
            self.assertNotIn("force_original_aspect_ratio", text)
            self.assertNotIn("pad=", text)

    def test_crop_affects_aspect_ratio_calculation(self):
        """Test 5: AR is calculated from post-crop frame, not original source."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            # 1920x1080 source (16:9), crop to 1080x1080 (1:1)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 1920, "height": 1080}],
                "crop_enabled": True,
                "crop_left": 420, "crop_right": 420,
                "crop_top": 0, "crop_bottom": 0,
                "use_gpu": False,
                "resolution": FFmWiz.parse_resolution("640x480"),
            })
            text = self.command_text(answers)
            # Post-crop is 1080x1080 (1:1). Target canvas is 640x480 (4:3).
            # AR preservation means the 1:1 content fits inside 640x480.
            # Scaled content should be ~480x480 with horizontal padding.
            self.assertIn("force_original_aspect_ratio=decrease", text)
            self.assertIn("pad=640:480", text)
            # The crop filter must be present
            self.assertIn("crop=", text)

    def test_no_upscale_smaller_source_pads_to_canvas(self):
        """Test 6: Source smaller than target with upscaling disabled.
        Content retains its size; canvas is padded."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            # Small source: 320x240. Target: 640x480.
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 320, "height": 240}],
                "crop_enabled": False,
                "use_gpu": False,
                "resolution": FFmWiz.parse_resolution("640x480"),
            })
            text = self.command_text(answers)
            # force_original_aspect_ratio=decrease won't upscale because the
            # source (320x240) fits inside 640x480 without scaling up — it
            # simply keeps 320x240 and pads to 640x480.
            # The command must contain AR-safe scale + pad.
            self.assertIn("force_original_aspect_ratio=decrease", text)
            self.assertIn("pad=640:480", text)

    def test_non_square_sar_display_ar_preserved(self):
        """Test 7: Non-square SAR source has display AR preserved after resize."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            # Anamorphic: 720x576, SAR 64:45 → display 1024x576 (16:9)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 720, "height": 576,
                                   "sample_aspect_ratio": "64:45"}],
                "crop_enabled": False,
                "use_gpu": False,
                "resolution": FFmWiz.parse_resolution("480p"),
            })
            text = self.command_text(answers)
            # Display AR is 16:9. 480p should produce ~854x480.
            dims = answers.get("final_resolution")
            self.assertIsNotNone(dims)
            display_ar = (720 * 64 / 45) / 576  # ≈ 1.778 (16:9)
            out_ar = dims[0] / dims[1]
            self.assertAlmostEqual(out_ar, display_ar, delta=0.02)
            # SAR is reset inside the scale filter, output uses square pixels.
            self.assertIn("reset_sar=1", text)

    def test_complex_graph_does_not_add_cuda_filters(self):
        """Test 8: Complex filter graph (multi-cut) remains CPU-based, no CUDA filters."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 1920, "height": 1080}],
                "crop_enabled": False,
                "use_gpu": True,
                "video_codec": "H265",
                "cut_keep_ranges": [(10, 100), (200, 300)],
                "format": {"duration": "600.0"},
                "resolution": FFmWiz.parse_resolution("1018x480"),
            })
            text = self.command_text(answers)
            # Complex graph (multi-cut) must use CPU filters
            self.assertIn("force_original_aspect_ratio=decrease", text)
            self.assertIn("pad=1018:480", text)
            # Must NOT add CUDA upload/download for the filter graph
            self.assertNotIn("-hwaccel_output_format cuda", text)
            self.assertNotIn("hwupload", text)
            self.assertNotIn("hwdownload", text)
            self.assertNotIn("scale_cuda", text)
            # Cleanup
            FFmWiz.cleanup_encode_chapter_metadata(answers)

    # ===================================================================
    # Tests for crop exact=1 and scale reset_sar=1
    # ===================================================================

    def test_crop_with_odd_coordinates_includes_exact_flag(self):
        """Crop with odd values must include exact=1 to prevent FFmpeg rounding."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 2876, "height": 1442}],
                "crop_enabled": True,
                "crop_left": 421, "crop_right": 730,
                "crop_top": 176, "crop_bottom": 182,
                "use_gpu": False,
                "resolution": FFmWiz.parse_resolution("1016x480"),
            })
            text = self.command_text(answers)
            self.assertIn("crop=iw-420-730:ih-176-182:420:176:exact=1", text)

    def test_preserve_mode_scale_includes_reset_sar(self):
        """Preserve-mode scale must include reset_sar=1 and no trailing setsar=1 after pad."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 1920, "height": 1080}],
                "crop_enabled": False,
                "use_gpu": False,
                "resolution": FFmWiz.parse_resolution("1016x480"),
            })
            text = self.command_text(answers)
            self.assertIn("reset_sar=1", text)
            # There must be no trailing setsar=1 after pad
            self.assertNotIn("pad=1016:480:(ow-iw)/2:(oh-ih)/2,setsar=1", text)

    def test_non_square_sar_preserved_after_reset_sar(self):
        """Non-square SAR input: display AR is preserved, output uses square pixels via reset_sar."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            # 720x576 SAR 64:45 → display 1024x576 (16:9)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 720, "height": 576,
                                   "sample_aspect_ratio": "64:45"}],
                "crop_enabled": False,
                "use_gpu": False,
                "resolution": FFmWiz.parse_resolution("854x480"),
            })
            text = self.command_text(answers)
            self.assertIn("reset_sar=1", text)
            # Output canvas is 854x480
            self.assertIn("pad=854:480", text)
            # No trailing setsar=1 after pad
            self.assertNotIn("pad=854:480:(ow-iw)/2:(oh-ih)/2,setsar=1", text)

    def test_stretch_mode_not_affected_by_reset_sar(self):
        """Stretch mode does not add force_original_aspect_ratio or reset_sar."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 1920, "height": 1080}],
                "crop_enabled": False,
                "use_gpu": False,
                "resolution": FFmWiz.parse_resolution("stretch:1016x480"),
            })
            text = self.command_text(answers)
            self.assertIn("scale=1016:480", text)
            self.assertNotIn("force_original_aspect_ratio", text)
            self.assertNotIn("reset_sar=1", text)
            self.assertNotIn("pad=", text)
            # Stretch mode still uses setsar from FORCE_SAR
            self.assertIn("setsar=1", text)

    def test_exact_reported_workflow_crop_exact_and_reset_sar(self):
        """Exact reported workflow: multi-range trim + crop + 4fps + 1016x480 + NVENC + split.
        Must contain crop=...:exact=1 and scale=...:reset_sar=1, no trailing setsar=1 after pad."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 2876, "height": 1442, "avg_frame_rate": "30/1", "color_range": "tv"}],
                "crop_enabled": True,
                "crop_left": 421, "crop_right": 730,
                "crop_top": 176, "crop_bottom": 182,
                "fps": 4,
                "resolution": FFmWiz.parse_resolution("1016x480"),
                "use_gpu": True,
                "video_codec": "H265",
                "separator_points": [3000.0],
                "cut_keep_ranges": [(10, 4000), (5000, 7000)],
                "format": {"duration": "8000.0"},
                "audio_speed_from_video": False,
            })
            text = self.command_text(answers)
            # Must contain exact=1 in crop (left normalized 421->420 for 4:2:0 origin)
            self.assertIn("crop=iw-420-730:ih-176-182:420:176:exact=1", text)
            # Must contain reset_sar=1 in scale
            self.assertIn("scale=1016:480:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1", text)
            # Must contain pad
            self.assertIn("pad=1016:480:(ow-iw)/2:(oh-ih)/2", text)
            # Must NOT have trailing setsar=1 after pad
            self.assertNotIn("pad=1016:480:(ow-iw)/2:(oh-ih)/2,setsar=1", text)
            # Must NOT have plain crop without exact=1
            self.assertNotIn("crop=iw-420-730:ih-176-182:420:176,", text)
            # Other settings preserved
            self.assertIn("fps=4", text)
            self.assertIn("hevc_nvenc", text)
            self.assertIn("-map_chapters -1", text)
            self.assertIn("_Part01", text)
            self.assertIn("_Part02", text)
            # Cleanup
            FFmWiz.cleanup_encode_chapter_metadata(answers)

    # ===================================================================
    # LoudNorm linear/dynamic and crop even-dimension tests
    # ===================================================================

    def test_loudnorm_linear_feasible(self):
        """Linear mode feasible: gain does not exceed target TP."""
        answers = {
            "loudnorm_enabled": True,
            "loudnorm_target_i": -16.0,
            "loudnorm_measured": {
                "input_i": -17.0,
                "input_tp": -3.0,
                "input_lra": 6.0,
                "input_thresh": -27.5,
                "target_offset": -0.2,
            },
        }
        # gain = +1 dB, predicted_TP = -3.0 + 1 = -2.0 <= -1.5 → linear
        filt = FFmWiz.build_loudnorm_filter(answers)
        self.assertIn("linear=true", filt)

    def test_loudnorm_linear_not_feasible(self):
        """Linear mode not feasible: predicted TP exceeds target TP."""
        answers = {
            "loudnorm_enabled": True,
            "loudnorm_target_i": -14.0,
            "loudnorm_measured": {
                "input_i": -19.64,
                "input_tp": -2.96,
                "input_lra": 8.4,
                "input_thresh": -30.32,
                "target_offset": -0.51,
            },
        }
        # gain = +5.64 dB, predicted_TP = -2.96 + 5.64 = +2.68 > -1.5 → dynamic
        filt = FFmWiz.build_loudnorm_filter(answers)
        self.assertIn("linear=false", filt)
        self.assertNotIn("linear=true", filt)

    def test_loudnorm_aresample_after_filter(self):
        """LoudNorm processing chain includes aresample=48000 after loudnorm."""
        answers = {
            "loudnorm_enabled": True,
            "loudnorm_target_i": -16.0,
            "audio_speed_from_video": False,
            "video_speed_enabled": False,
        }
        chain = FFmWiz.build_encode_audio_processing_filter(answers)
        self.assertIn("loudnorm=", chain)
        self.assertIn("aresample=48000", chain)
        # aresample must come after loudnorm but before asetpts
        loudnorm_pos = chain.index("loudnorm=")
        aresample_pos = chain.index("aresample=48000")
        asetpts_pos = chain.index("asetpts=")
        self.assertLess(loudnorm_pos, aresample_pos)
        self.assertLess(aresample_pos, asetpts_pos)

    def test_crop_odd_dimensions_normalized_without_compatibility_pad(self):
        """Odd crop values are normalized to chroma/encoder-aligned values and
        NEVER repaired with a black compatibility pad."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            # 1920x1080, 4:2:0, no resize. Requested left=44 right=39 top=27 bottom=22.
            # Origin must be even (left ok, top 27->26); cropped size must be even
            # (source even -> right even 39->38; bottom 22 keeps height even).
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 1920, "height": 1080, "pix_fmt": "yuv420p"}],
                "crop_enabled": True,
                "crop_left": 44, "crop_right": 39,
                "crop_top": 27, "crop_bottom": 22,
                "use_gpu": False,
                "resolution": "n",  # No resize
            })
            answers.pop("fps", None)
            text = self.command_text(answers)
            self.assertIn("crop=iw-44-38:ih-26-22:44:26:exact=1", text)
            self.assertNotIn("pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0", text)
            # Final cropped dimensions must be even.
            self.assertEqual(FFmWiz.cropped_source_size(answers), (1838, 1032))

    def test_crop_even_dimensions_no_compatibility_pad(self):
        """Already-aligned crop values are unchanged and add no compatibility pad."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            # 1920x1080, crop: left=100 right=100 top=40 bottom=40 → 1720x1000 (both even)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 1920, "height": 1080, "pix_fmt": "yuv420p"}],
                "crop_enabled": True,
                "crop_left": 100, "crop_right": 100,
                "crop_top": 40, "crop_bottom": 40,
                "use_gpu": False,
                "resolution": "n",
            })
            answers.pop("fps", None)
            text = self.command_text(answers)
            self.assertIn("crop=iw-100-100:ih-40-40:100:40:exact=1", text)
            self.assertNotIn("pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0", text)

    def test_crop_followed_by_preserve_resize_no_double_pad(self):
        """Crop + Preserve resize: no redundant compatibility pad before scale."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 1920, "height": 1080}],
                "crop_enabled": True,
                "crop_left": 44, "crop_right": 39,
                "crop_top": 27, "crop_bottom": 22,
                "use_gpu": False,
                "resolution": FFmWiz.parse_resolution("854x480"),
            })
            text = self.command_text(answers)
            self.assertIn("exact=1", text)
            self.assertIn("force_divisible_by=2", text)
            # Should NOT have the ceil pad when resize with force_divisible_by=2 follows
            self.assertNotIn("pad=ceil(iw/2)*2", text)

    # ===================================================================
    # Crop normalization (chroma/encoder alignment without black padding)
    # ===================================================================

    def _crop_answers(self, width, height, pix_fmt, left, right, top, bottom, resolution="n"):
        return {
            "video_streams": [{
                "codec_type": "video", "codec_name": "h264",
                "width": width, "height": height, "pix_fmt": pix_fmt,
            }],
            "crop_enabled": True,
            "crop_left": left, "crop_right": right,
            "crop_top": top, "crop_bottom": bottom,
            "resolution": resolution,
        }

    def test_crop_norm_1_reported_1280x720_case(self):
        """Test 1: 1280x720 yuv420p, requested 45/39/25/22 -> 44/40/24/22."""
        answers = self._crop_answers(1280, 720, "yuv420p", 45, 39, 25, 22)
        left, right, top, bottom = FFmWiz.normalized_crop_margins(answers)
        self.assertEqual((left, right, top, bottom), (44, 40, 24, 22))
        self.assertEqual(FFmWiz.cropped_source_size(answers), (1196, 674))
        # CPU filter uses normalized values with exact=1, no compatibility pad.
        cpu_answers = self.base_answers_with_crop(1280, 720, "yuv420p", 45, 39, 25, 22)
        cpu_text = self.command_text(cpu_answers)
        self.assertIn("crop=iw-44-40:ih-24-22:44:24:exact=1", cpu_text)
        self.assertNotIn("pad=ceil(iw/2)*2", cpu_text)
        # CUVID decoder crop uses the same geometry (top x bottom x left x right).
        gpu_answers = dict(cpu_answers)
        gpu_answers["use_gpu"] = True
        self.assertEqual(FFmWiz.crop_margins_to_cuvid_crop(gpu_answers), "24x22x44x40")

    def base_answers_with_crop(self, width, height, pix_fmt, left, right, top, bottom):
        # A full no-resize encode answers set for command-text assertions.
        answers = self.base_answers(".")
        answers.update({
            "video_streams": [{
                "codec_type": "video", "codec_name": "h264",
                "width": width, "height": height, "pix_fmt": pix_fmt,
                "avg_frame_rate": "30/1", "color_range": "tv",
            }],
            "crop_enabled": True,
            "crop_left": left, "crop_right": right,
            "crop_top": top, "crop_bottom": bottom,
            "use_gpu": False,
            "resolution": "n",
        })
        answers.pop("fps", None)
        return answers

    def test_crop_norm_2_already_valid_values_unchanged(self):
        """Test 2: already-aligned values are unchanged, no padding."""
        answers = self._crop_answers(1280, 720, "yuv420p", 44, 40, 24, 22)
        self.assertEqual(FFmWiz.normalized_crop_margins(answers), (44, 40, 24, 22))
        text = self.command_text(self.base_answers_with_crop(1280, 720, "yuv420p", 44, 40, 24, 22))
        self.assertIn("crop=iw-44-40:ih-24-22:44:24:exact=1", text)
        self.assertNotIn("pad=ceil(iw/2)*2", text)

    def test_crop_norm_3_odd_source_width(self):
        """Test 3: source width 1279 (odd), 4:2:0 -> left even, right odd, width even."""
        answers = self._crop_answers(1279, 720, "yuv420p", 44, 40, 24, 22)
        left, right, top, bottom = FFmWiz.normalized_crop_margins(answers)
        self.assertEqual(left % 2, 0)
        self.assertEqual(right % 2, 1)
        width = 1279 - left - right
        self.assertEqual(width % 2, 0)
        self.assertEqual((left, right), (44, 39))

    def test_crop_norm_4_odd_source_height(self):
        """Test 4: source height 719 (odd), 4:2:0 -> top even, bottom odd, height even."""
        answers = self._crop_answers(1280, 719, "yuv420p", 44, 40, 24, 22)
        left, right, top, bottom = FFmWiz.normalized_crop_margins(answers)
        self.assertEqual(top % 2, 0)
        self.assertEqual(bottom % 2, 1)
        height = 719 - top - bottom
        self.assertEqual(height % 2, 0)
        self.assertEqual((top, bottom), (24, 21))

    def test_crop_norm_5_both_dimensions_odd(self):
        """Test 5: both source dimensions odd; each axis validated independently."""
        answers = self._crop_answers(1279, 719, "yuv420p", 45, 39, 25, 22)
        left, right, top, bottom = FFmWiz.normalized_crop_margins(answers)
        self.assertEqual(left % 2, 0)
        self.assertEqual(top % 2, 0)
        self.assertEqual((1279 - left - right) % 2, 0)
        self.assertEqual((719 - top - bottom) % 2, 0)
        crop_w, crop_h = FFmWiz.cropped_source_size(answers)
        self.assertGreater(crop_w, 0)
        self.assertGreater(crop_h, 0)

    def test_crop_norm_6_zero_and_near_zero(self):
        """Test 6: zero crop stays zero; tiny crop never goes negative."""
        zero = self._crop_answers(1280, 720, "yuv420p", 0, 0, 0, 0)
        self.assertEqual(FFmWiz.normalized_crop_margins(zero), (0, 0, 0, 0))
        tiny = self._crop_answers(1280, 720, "yuv420p", 1, 0, 1, 0)
        left, right, top, bottom = FFmWiz.normalized_crop_margins(tiny)
        self.assertTrue(min(left, right, top, bottom) >= 0)
        self.assertEqual(left % 2, 0)
        self.assertEqual(top % 2, 0)

    def test_crop_norm_7_crop_near_boundary_rejected(self):
        """Test 7: an impossible crop rectangle is rejected before FFmpeg runs."""
        answers = self._crop_answers(1280, 720, "yuv420p", 2000, 2000, 0, 0)
        with self.assertRaises(ValueError):
            FFmWiz.normalized_crop_margins(answers)

    def test_crop_norm_8_yuv422_horizontal_only_origin(self):
        """Test 8: 4:2:2 source does not force an even vertical crop origin.
        Requested top stays odd because the output height is already even."""
        h, v = FFmWiz.chroma_subsampling_alignment("yuv422p")
        self.assertEqual((h, v), (2, 1))
        answers = self._crop_answers(1280, 720, "yuv422p", 44, 40, 25, 23)
        left, right, top, bottom = FFmWiz.normalized_crop_margins(answers)
        # 720-25-23 = 672 (even) so the odd vertical origin is preserved.
        self.assertEqual((left, right, top, bottom), (44, 40, 25, 23))
        self.assertEqual((720 - top - bottom) % 2, 0)
        # A 4:2:0 source with the same request WOULD force an even top.
        yuv420 = self._crop_answers(1280, 720, "yuv420p", 44, 40, 25, 23)
        self.assertEqual(FFmWiz.normalized_crop_margins(yuv420)[2] % 2, 0)

    def test_crop_norm_9_yuv444_origin_not_forced(self):
        """Test 9: 4:4:4 source allows odd crop origin; output stays encodable."""
        h, v = FFmWiz.chroma_subsampling_alignment("yuv444p")
        self.assertEqual((h, v), (1, 1))
        # 45+39=84 (even width crop) and 25+23=48 (even height crop) keep the
        # output dimensions even, so both odd origins are preserved.
        answers = self._crop_answers(1280, 720, "yuv444p", 45, 39, 25, 23)
        left, right, top, bottom = FFmWiz.normalized_crop_margins(answers)
        self.assertEqual((left, right, top, bottom), (45, 39, 25, 23))
        self.assertEqual((1280 - left - right) % 2, 0)
        self.assertEqual((720 - top - bottom) % 2, 0)

    def test_crop_norm_10_preserve_resize_after_crop(self):
        """Test 10: crop origin normalized; preserve resize handles output size;
        no separate one-pixel compatibility pad is inserted."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 2876, "height": 1442, "pix_fmt": "yuv420p",
                                   "avg_frame_rate": "30/1", "color_range": "tv"}],
                "crop_enabled": True,
                "crop_left": 421, "crop_right": 730,
                "crop_top": 179, "crop_bottom": 184,
                "use_gpu": False,
                "resolution": FFmWiz.parse_resolution("1018x480"),
            })
            text = self.command_text(answers)
            self.assertIn("crop=iw-420-730:ih-178-184:420:178:exact=1", text)
            self.assertIn("force_divisible_by=2", text)
            self.assertNotIn("pad=ceil(iw/2)*2", text)

    def test_crop_norm_11_cpu_and_cuvid_equivalent(self):
        """Test 11: CPU filter and CUVID decoder resolve to the same rectangle."""
        answers = self.base_answers_with_crop(1280, 720, "yuv420p", 45, 39, 25, 22)
        cpu_filter = FFmWiz.build_cpu_video_filter(answers)
        self.assertIn("crop=iw-44-40:ih-24-22:44:24:exact=1", cpu_filter)
        gpu_answers = dict(answers)
        gpu_answers["use_gpu"] = True
        self.assertEqual(FFmWiz.crop_margins_to_cuvid_crop(gpu_answers), "24x22x44x40")

    def test_crop_norm_12_no_odd_origin_color_shift_regression(self):
        """Test 12: a 4:2:0 source with an odd requested origin must never emit
        an odd crop x/y (which causes chroma-phase color shift)."""
        answers = self.base_answers_with_crop(1280, 720, "yuv420p", 45, 39, 25, 22)
        left, right, top, bottom = FFmWiz.normalized_crop_margins(answers)
        self.assertEqual(left % 2, 0)
        self.assertEqual(top % 2, 0)
        text = self.command_text(answers)
        self.assertNotIn(":45:", text)
        self.assertNotIn("ih-25-", text)
        self.assertIn("crop=iw-44-40:ih-24-22:44:24:exact=1", text)

    # ===================================================================
    # Color-range resolution and menu
    # ===================================================================

    def _encode_answers(self, tmp, color_range=None, resolution="480p"):
        answers = self.base_answers(tmp)
        answers["use_gpu"] = False
        stream = dict(answers["video_streams"][0])
        if color_range is None:
            stream.pop("color_range", None)
        else:
            stream["color_range"] = color_range
        answers["video_streams"] = [stream]
        answers["resolution"] = FFmWiz.parse_resolution(resolution) if resolution != "n" else "n"
        return answers

    def test_color_range_normalize(self):
        self.assertEqual(FFmWiz.normalize_color_range("limited"), "tv")
        self.assertEqual(FFmWiz.normalize_color_range("mpeg"), "tv")
        self.assertEqual(FFmWiz.normalize_color_range("tv"), "tv")
        self.assertEqual(FFmWiz.normalize_color_range("full"), "pc")
        self.assertEqual(FFmWiz.normalize_color_range("jpeg"), "pc")
        self.assertEqual(FFmWiz.normalize_color_range("pc"), "pc")
        self.assertEqual(FFmWiz.normalize_color_range("unknown"), "")
        self.assertEqual(FFmWiz.normalize_color_range(""), "")

    def test_color_range_unknown_default_enter_is_tv(self):
        """Test 1: unknown range, menu appears, Enter selects TV/Limited, command has tv."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            self.assertTrue(FFmWiz.color_range_prompt_applicable(answers))
            with mock.patch.object(FFmWiz, "ask_raw", return_value="") as ask, \
                    contextlib.redirect_stdout(io.StringIO()):
                FFmWiz.step_color_range(answers)
            self.assertTrue(ask.called)
            self.assertEqual(answers["color_range_choice"], "tv")
            self.assertIn("-color_range:v:0 tv", self.command_text(answers))

    def test_color_range_unknown_keep_unspecified(self):
        """Test 2: unknown range + Keep unspecified -> no forced tv/pc."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            with mock.patch.object(FFmWiz, "ask_raw", return_value="2"), \
                    contextlib.redirect_stdout(io.StringIO()):
                FFmWiz.step_color_range(answers)
            self.assertEqual(answers["color_range_choice"], "unspecified")
            text = self.command_text(answers)
            self.assertNotIn("-color_range:v:0 tv", text)
            self.assertNotIn("-color_range:v:0 pc", text)

    def test_color_range_unknown_assume_pc(self):
        """Test 3: unknown range + Assume PC/Full -> command has pc."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            with mock.patch.object(FFmWiz, "ask_raw", return_value="3"), \
                    contextlib.redirect_stdout(io.StringIO()):
                FFmWiz.step_color_range(answers)
            self.assertEqual(answers["color_range_choice"], "pc")
            self.assertIn("-color_range:v:0 pc", self.command_text(answers))

    def test_color_range_known_tv_no_menu(self):
        """Test 4: known TV/Limited -> menu not applicable, value preserved."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range="tv")
            self.assertFalse(FFmWiz.color_range_prompt_applicable(answers))
            self.assertEqual(FFmWiz.resolve_color_range(answers), ("tv", "detected"))
            self.assertIn("-color_range:v:0 tv", self.command_text(answers))

    def test_color_range_known_pc_no_menu(self):
        """Test 5: known PC/Full -> menu not applicable, value preserved."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range="pc")
            self.assertFalse(FFmWiz.color_range_prompt_applicable(answers))
            self.assertEqual(FFmWiz.resolve_color_range(answers), ("pc", "detected"))
            self.assertIn("-color_range:v:0 pc", self.command_text(answers))

    def test_color_range_split_applies_to_all_parts(self):
        """Test 6: one workflow, multiple Split parts -> same resolved choice everywhere."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            answers["color_range_choice"] = "pc"
            answers["separator_points"] = [120.0, 300.0]
            answers["format"] = {"duration": "600.0"}
            text = self.command_text(answers)
            self.assertEqual(text.count("-color_range:v:0 pc"), text.count("-c:v libx265"))
            self.assertNotIn("-color_range:v:0 tv", text)

    def test_color_range_back_navigation_keeps_previous_default(self):
        """Test 7: re-entering the menu offers the previous choice as default."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            answers["color_range_choice"] = "pc"
            captured = {}

            def fake_ask(prompt):
                captured["prompt"] = prompt
                return ""  # Enter keeps the default.

            with mock.patch.object(FFmWiz, "ask_raw", side_effect=fake_ask), \
                    contextlib.redirect_stdout(io.StringIO()):
                FFmWiz.step_color_range(answers)
            self.assertIn("[3]", captured["prompt"])  # previous pc -> default option 3
            self.assertEqual(answers["color_range_choice"], "pc")

    def test_color_range_stream_copy_has_no_forced_metadata(self):
        """Test 8: pure stream copy/remux -> no menu, no invented color-range metadata."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None, resolution="n")
            answers["video_codec"] = "copy"
            answers["audio_codec"] = "copy"
            answers["crop_enabled"] = False
            answers.pop("fps", None)
            answers["fps"] = None
            self.assertFalse(FFmWiz.color_range_prompt_applicable(answers))
            text = self.command_text(answers)
            self.assertNotIn("-color_range", text)

    def test_color_range_cpu_two_pass_consistent(self):
        """Test 9: CPU two-pass uses consistent resolved color-range behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            answers["color_range_choice"] = "pc"
            answers["cpu_two_pass"] = True
            cmd = self.command_for(answers)
            first, second, _passlog = FFmWiz.build_cpu_two_pass_commands(cmd, answers)
            # Pass 2 carries the resolved color-range; pass 1 is the analysis pass.
            self.assertIn("-color_range:v:0 pc", " ".join(second))

    # ===================================================================
    # SAR / DAR parsing, calculation, and setsar handling
    # ===================================================================

    def test_parse_rational_valid_and_invalid(self):
        self.assertAlmostEqual(FFmWiz.parse_rational("16:9"), 16 / 9)
        self.assertAlmostEqual(FFmWiz.parse_rational("4/3"), 4 / 3)
        self.assertAlmostEqual(FFmWiz.parse_rational("1.5"), 1.5)
        for bad in ("0:1", "0/0", "-1", "N/A", "unknown", "", None, "abc"):
            self.assertIsNone(FFmWiz.parse_rational(bad))

    def test_sar_dar_square_pixels(self):
        """Test 10: 1920x1080 SAR 1:1 -> DAR 16:9, square pixels."""
        answers = {"video_streams": [{"width": 1920, "height": 1080, "sample_aspect_ratio": "1:1"}]}
        info = FFmWiz.sar_dar_info(answers)
        self.assertEqual(info["sar_text"], "1:1")
        self.assertEqual(info["dar_text"], "16:9")
        self.assertEqual(info["pixel_shape"], "square")

    def test_sar_dar_calculated_from_sar_when_dar_missing(self):
        """Test 12: known SAR, missing DAR -> DAR calculated as 4:3, labeled calculated."""
        answers = {"video_streams": [{"width": 720, "height": 576, "sample_aspect_ratio": "16:15"}]}
        info = FFmWiz.sar_dar_info(answers)
        self.assertAlmostEqual(info["dar"], 4 / 3, places=4)
        self.assertEqual(info["dar_text"], "4:3")
        self.assertEqual(info["dar_source"], "calculated from coded resolution and SAR")
        self.assertEqual(info["pixel_shape"], "non-square")

    def test_sar_dar_discrepancy_uses_calculated(self):
        """Test 18: ffprobe DAR disagrees with calculated -> discrepancy flagged, calculated used."""
        answers = {"video_streams": [{"width": 720, "height": 576,
                                       "sample_aspect_ratio": "16:15",
                                       "display_aspect_ratio": "16:9"}]}
        info = FFmWiz.sar_dar_info(answers)
        self.assertEqual(info["dar_source"], "calculated from coded resolution and SAR")
        self.assertIsNotNone(info["discrepancy"])
        self.assertAlmostEqual(info["dar"], 4 / 3, places=4)

    def test_sar_dar_unknown_fallback(self):
        """Test 16: unknown SAR -> width/height fallback reported, no crash."""
        answers = {"video_streams": [{"width": 1920, "height": 1080}]}
        info = FFmWiz.sar_dar_info(answers)
        self.assertEqual(info["sar_text"], "unknown")
        self.assertEqual(info["pixel_shape"], "unknown")
        self.assertEqual(info["dar_source"], "width/height fallback (assumed SAR 1:1)")

    def test_no_resize_nonsquare_sar_omits_setsar(self):
        """Test 11: 720x576 SAR 16:15 no-resize -> source SAR preserved, no setsar=1."""
        answers = {
            "video_streams": [{"codec_type": "video", "codec_name": "h264",
                               "width": 720, "height": 576, "sample_aspect_ratio": "16:15"}],
            "resolution": "n", "crop_enabled": False,
        }
        vf = FFmWiz.build_cpu_video_filter(answers) or ""
        self.assertNotIn("setsar", vf)

    def test_no_resize_square_sar_omits_setsar(self):
        """No-resize square source: redundant setsar is omitted."""
        answers = {
            "video_streams": [{"codec_type": "video", "codec_name": "h264",
                               "width": 1920, "height": 1080, "sample_aspect_ratio": "1:1"}],
            "resolution": "n", "crop_enabled": False,
        }
        vf = FFmWiz.build_cpu_video_filter(answers) or ""
        self.assertNotIn("setsar", vf)

    def test_stretch_resize_keeps_setsar(self):
        """Test 15: explicit stretch keeps the intentional square-pixel setsar."""
        answers = {
            "video_streams": [{"codec_type": "video", "codec_name": "h264",
                               "width": 1920, "height": 1080}],
            "resolution": FFmWiz.parse_resolution("stretch:1280x720"),
            "crop_enabled": False,
        }
        vf = FFmWiz.build_cpu_video_filter(answers) or ""
        self.assertIn("scale=1280:720", vf)
        self.assertIn("setsar=1", vf)

    # ===================================================================
    # Folder/batch color-range policy
    # ===================================================================

    def _folder_settings(self, policy=None, per_file=None, codec="H265"):
        s = {"video_codec": codec, "folder_output_location": Path("."), "use_gpu": False}
        if policy:
            s["_batch_color_range_policy"] = policy
        if per_file:
            s["_batch_color_range_per_file"] = per_file
        return s

    def _folder_job(self, color_range=None):
        stream = {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
        if color_range is not None:
            stream["color_range"] = color_range
        return {"video_streams": [stream], "video_codec": "H265"}

    def test_batch_policy_tv_unknown_resolves_tv_known_preserved(self):
        """Tests 3/4: option 1 -> unknown becomes tv; known is never overwritten."""
        settings = self._folder_settings(policy="tv")
        unknown = self._folder_job(color_range=None)
        FFmWiz.apply_folder_batch_color_range(unknown, settings, {"path": Path("a.mkv")})
        self.assertEqual(unknown["color_range_choice"], "tv")
        self.assertEqual(FFmWiz.resolve_color_range(unknown), ("tv", "batch user assumption"))
        known_pc = self._folder_job(color_range="pc")
        FFmWiz.apply_folder_batch_color_range(known_pc, settings, {"path": Path("b.mkv")})
        self.assertNotIn("color_range_choice", known_pc)
        self.assertEqual(FFmWiz.resolve_color_range(known_pc), ("pc", "detected"))

    def test_batch_policy_unspecified_omits_color_range(self):
        """Test 5: option 2 -> unknown files omit -color_range."""
        settings = self._folder_settings(policy="unspecified")
        job = self._folder_job(color_range=None)
        FFmWiz.apply_folder_batch_color_range(job, settings, {"path": Path("a.mkv")})
        self.assertEqual(job["color_range_choice"], "unspecified")
        self.assertEqual(FFmWiz.color_range_output_args(job), [])

    def test_batch_policy_pc_unknown_resolves_pc(self):
        """Test 6: option 3 -> unknown files use pc."""
        settings = self._folder_settings(policy="pc")
        job = self._folder_job(color_range=None)
        FFmWiz.apply_folder_batch_color_range(job, settings, {"path": Path("a.mkv")})
        self.assertEqual(FFmWiz.color_range_output_args(job), ["-color_range:v:0", "pc"])

    def test_batch_policy_each_uses_per_file_choice(self):
        """Test 7: option 4 -> each unknown file uses its own choice."""
        settings = self._folder_settings(policy="each", per_file={str(Path("a.mkv")): "pc"})
        job = self._folder_job(color_range=None)
        FFmWiz.apply_folder_batch_color_range(job, settings, {"path": Path("a.mkv")})
        self.assertEqual(job["color_range_choice"], "pc")
        self.assertEqual(FFmWiz.resolve_color_range(job)[0], "pc")

    def test_batch_applicable_only_with_unknown_files(self):
        """Tests 1/2: all-known folders do not trigger the batch menu."""
        all_known = {
            "video_codec": "H265",
            "video_streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
            "_folder_items": [
                {"answers": {"video_streams": [{"color_range": "tv"}]}},
                {"answers": {"video_streams": [{"color_range": "pc"}]}},
            ],
        }
        self.assertFalse(FFmWiz.folder_batch_color_range_applicable(all_known))
        mixed = {
            "video_codec": "H265",
            "video_streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
            "_folder_items": [
                {"answers": {"video_streams": [{"color_range": "tv"}]}},
                {"answers": {"video_streams": [{}]}},
            ],
        }
        self.assertTrue(FFmWiz.folder_batch_color_range_applicable(mixed))

    def test_nonint_builder_unknown_is_compatibility_fallback(self):
        """Test 11: the legacy/direct API compatibility fallback must be opted
        into explicitly; when enabled it reports a logged compatibility
        fallback, never a silent 'detected' tv."""
        job = self._folder_job(color_range=None)
        resolved, source = FFmWiz.resolve_color_range(job, allow_compatibility_fallback=True)
        self.assertEqual(resolved, "tv")
        self.assertEqual(source, "compatibility fallback")

    # ===================================================================
    # Pixel-format analysis and warnings
    # ===================================================================

    def test_pixfmt_descriptor(self):
        self.assertEqual(FFmWiz.pix_fmt_descriptor("yuv420p")["chroma"], "4:2:0")
        self.assertEqual(FFmWiz.pix_fmt_descriptor("yuv420p10le")["bit_depth"], 10)
        self.assertEqual(FFmWiz.pix_fmt_descriptor("yuv422p")["chroma"], "4:2:2")
        self.assertEqual(FFmWiz.pix_fmt_descriptor("yuv444p10le")["chroma"], "4:4:4")
        self.assertEqual(FFmWiz.pix_fmt_descriptor("nv12")["bit_depth"], 8)
        self.assertEqual(FFmWiz.pix_fmt_descriptor("p010le")["bit_depth"], 10)
        self.assertEqual(FFmWiz.pix_fmt_descriptor("rgb24")["kind"], "rgb")
        self.assertEqual(FFmWiz.pix_fmt_descriptor("gray")["kind"], "gray")
        self.assertEqual(FFmWiz.pix_fmt_descriptor("totally-unknown")["kind"], "unknown")

    def test_pixfmt_noop_8bit_420(self):
        """Test 13: yuv420p -> yuv420p is a no-op (none), no warning."""
        info = FFmWiz.compare_pixel_formats("yuv420p", "yuv420p")
        self.assertEqual(info["operation"], "none")
        self.assertEqual(info["warnings"], [])
        self.assertEqual(info["bit_depth_conversion"], "no")
        self.assertEqual(info["chroma_conversion"], "no")

    def test_pixfmt_relabel_is_no_op_constraint(self):
        """nv12 <-> yuv420p (same geometry) is a no-op compatibility constraint."""
        info = FFmWiz.compare_pixel_formats("nv12", "yuv420p")
        self.assertEqual(info["operation"], "no-op compatibility constraint")
        self.assertEqual(info["warnings"], [])

    def test_pixfmt_10bit_to_8bit_warns(self):
        """Test 14: yuv420p10le -> yuv420p warns about bit-depth reduction."""
        info = FFmWiz.compare_pixel_formats("yuv420p10le", "yuv420p")
        self.assertEqual(info["bit_depth_conversion"], "10-bit -> 8-bit")
        self.assertTrue(any("bit depth will be reduced from 10-bit to 8-bit" in w for w in info["warnings"]))

    def test_pixfmt_422_to_420_warns(self):
        """Test 15: yuv422p -> yuv420p warns about chroma reduction."""
        info = FFmWiz.compare_pixel_formats("yuv422p", "yuv420p")
        self.assertEqual(info["chroma_conversion"], "4:2:2 -> 4:2:0")
        self.assertTrue(any("Chroma subsampling will be reduced from 4:2:2 to 4:2:0" in w for w in info["warnings"]))

    def test_pixfmt_444_10bit_to_420_warns_both(self):
        """Test 16: yuv444p10le -> yuv420p warns about bit depth and chroma."""
        info = FFmWiz.compare_pixel_formats("yuv444p10le", "yuv420p")
        self.assertTrue(any("bit depth" in w for w in info["warnings"]))
        self.assertTrue(any("Chroma subsampling will be reduced from 4:4:4 to 4:2:0" in w for w in info["warnings"]))

    def test_pixfmt_rgb_to_yuv_warns(self):
        """Test 17: rgb24 -> yuv420p warns about RGB->YUV conversion."""
        info = FFmWiz.compare_pixel_formats("rgb24", "yuv420p")
        self.assertTrue(any("RGB video will be converted to YUV 4:2:0" in w for w in info["warnings"]))

    def test_pixfmt_unknown_does_not_crash(self):
        """Test 18: unknown source pixel format -> no crash, operation unknown."""
        info = FFmWiz.compare_pixel_formats(None, "yuv420p")
        self.assertEqual(info["operation"], "unknown")
        self.assertEqual(info["warnings"], [])

    def test_pixfmt_10bit_cpu_uses_yuv420p10le_and_main10(self):
        """Test 19: 10-bit CPU HEVC output uses yuv420p10le + Main10."""
        answers = {"video_streams": [{"codec_type": "video", "codec_name": "hevc",
                                       "width": 1920, "height": 1080, "pix_fmt": "yuv420p10le"}],
                   "video_codec": "H265", "use_gpu": False}
        self.assertEqual(FFmWiz.cpu_pixel_format_for_output(answers), "yuv420p10le")
        self.assertEqual(FFmWiz.hevc_profile_for_output(answers, "main"), "main10")
        self.assertEqual(FFmWiz.target_pixel_format_for_answers(answers), "yuv420p10le")

    def test_pixfmt_10bit_nvenc_uses_p010(self):
        """Test 20: 10-bit NVENC output uses p010le."""
        answers = {"video_streams": [{"codec_type": "video", "codec_name": "hevc",
                                       "width": 1920, "height": 1080, "pix_fmt": "yuv420p10le"}],
                   "video_codec": "H265", "use_gpu": True}
        self.assertEqual(FFmWiz.target_pixel_format_for_answers(answers), "p010le")

    def test_pixfmt_8bit_nvenc_uses_nv12(self):
        """Test 21: 8-bit NVENC output uses nv12."""
        answers = {"video_streams": [{"codec_type": "video", "codec_name": "h264",
                                       "width": 1920, "height": 1080, "pix_fmt": "yuv420p"}],
                   "video_codec": "H265", "use_gpu": True}
        self.assertEqual(FFmWiz.target_pixel_format_for_answers(answers), "nv12")

    def test_pixfmt_cpu_two_pass_identical_format_filters(self):
        """Test 22: CPU two-pass uses identical pixel-format filters in both passes."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            cmd = self.command_for(answers)
            first, second, _ = FFmWiz.build_cpu_two_pass_commands(cmd, answers)
            def vfilt(c):
                return c[c.index("-filter:v") + 1] if "-filter:v" in c else ""
            self.assertEqual(vfilt(first), vfilt(second))
            self.assertIn("format=yuv420p", vfilt(first))

    # ===================================================================
    # Color-range resolution contract (strict vs compatibility fallback)
    # ===================================================================

    def _unknown_range_encode(self, output_dir: str, use_gpu: bool = False) -> dict:
        """base_answers variant whose source color range is unknown."""
        answers = self.base_answers(output_dir)
        answers["use_gpu"] = use_gpu
        for stream in answers["video_streams"]:
            stream.pop("color_range", None)
        return answers

    def test_strict_resolve_raises_when_unresolved(self):
        """Strict mode raises instead of silently assuming a range."""
        job = self._folder_job(color_range=None)
        with self.assertRaises(FFmWiz.ColorRangeUnresolvedError):
            FFmWiz.resolve_color_range(job, allow_compatibility_fallback=False)

    def test_strict_resolve_ok_after_user_choice(self):
        """After a wizard choice, strict mode resolves without raising."""
        job = self._folder_job(color_range=None)
        job["color_range_choice"] = "tv"
        resolved, source = FFmWiz.resolve_color_range(job, allow_compatibility_fallback=False)
        self.assertEqual(resolved, "tv")
        self.assertEqual(source, "user assumption")
        job["color_range_choice"] = "unspecified"
        self.assertEqual(
            FFmWiz.resolve_color_range(job, allow_compatibility_fallback=False),
            ("", "user choice"),
        )

    def test_strict_resolve_ok_after_batch_choice(self):
        """After a batch policy, strict mode reports a batch user assumption."""
        settings = self._folder_settings(policy="pc")
        job = self._folder_job(color_range=None)
        FFmWiz.apply_folder_batch_color_range(job, settings, {"path": Path("a.mkv")})
        resolved, source = FFmWiz.resolve_color_range(job, allow_compatibility_fallback=False)
        self.assertEqual(resolved, "pc")
        self.assertEqual(source, "batch user assumption")

    def test_strict_resolve_ok_when_detected(self):
        """A detected source range satisfies the strict guard."""
        job = self._folder_job(color_range="tv")
        self.assertEqual(
            FFmWiz.resolve_color_range(job, allow_compatibility_fallback=False),
            ("tv", "detected"),
        )

    def test_entry_guard_raises_on_unresolved_reencode(self):
        """The production entry guard rejects an unresolved re-encode workflow."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp)
            with self.assertRaises(FFmWiz.ColorRangeUnresolvedError):
                FFmWiz.ensure_color_range_resolved(answers)

    def test_entry_guard_passes_after_choice(self):
        """The entry guard is a no-op once a choice is recorded."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp)
            answers["color_range_choice"] = "unspecified"
            FFmWiz.ensure_color_range_resolved(answers)  # must not raise

    def test_entry_guard_noop_for_stream_copy(self):
        """Stream-copy output never triggers the color-range guard."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp)
            answers["video_codec"] = "copy"
            answers["crop_enabled"] = False
            FFmWiz.ensure_color_range_resolved(answers)  # must not raise

    def test_build_command_unknown_range_raises_by_default(self):
        """Production builder: an unresolved unknown range must raise instead of
        silently falling back. (command_for/command_text inject a resolved test
        choice, so this calls the production builder directly.)"""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp, use_gpu=False)
            with self.assertRaises(FFmWiz.ColorRangeUnresolvedError):
                FFmWiz.build_ffmpeg_command(answers)

    def test_legacy_opt_in_color_range_output_args(self):
        """Legacy/direct API opt-in still returns the labeled fallback."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp, use_gpu=False)
            self.assertEqual(
                FFmWiz.color_range_output_args(answers, ":v:0", allow_compatibility_fallback=True),
                ["-color_range:v:0", "tv"],
            )

    def test_color_range_helpers_default_disabled_fallback(self):
        """resolve_color_range and color_range_output_args default to strict."""
        job = self._folder_job(color_range=None)
        with self.assertRaises(FFmWiz.ColorRangeUnresolvedError):
            FFmWiz.resolve_color_range(job)
        with self.assertRaises(FFmWiz.ColorRangeUnresolvedError):
            FFmWiz.color_range_output_args(job)

    def test_color_range_error_message_identifies_context(self):
        """The unresolved error names source path, stream, range, and workflow."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp, use_gpu=False)
            try:
                FFmWiz.resolve_color_range(answers, workflow="UnitTest")
                self.fail("expected ColorRangeUnresolvedError")
            except FFmWiz.ColorRangeUnresolvedError as exc:
                msg = str(exc)
                self.assertIn("UnitTest", msg)
                self.assertIn("source path=", msg)
                self.assertIn("detected range=unknown", msg)
                self.assertIn("color_range_choice", msg)

    # ===================================================================
    # 'Keep unspecified' omits -color_range in rendered commands
    # ===================================================================

    def test_unspecified_omits_color_range_cpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp, use_gpu=False)
            answers["color_range_choice"] = "unspecified"
            self.assertNotIn("-color_range", self.command_text(answers))

    def test_unspecified_omits_color_range_gpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp, use_gpu=True)
            answers["color_range_choice"] = "unspecified"
            self.assertNotIn("-color_range", self.command_text(answers))

    def test_unspecified_omits_color_range_two_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp, use_gpu=False)
            answers["color_range_choice"] = "unspecified"
            cmd = self.command_for(answers)
            first, second, _ = FFmWiz.build_cpu_two_pass_commands(cmd, answers)
            self.assertNotIn("-color_range", " ".join(first))
            self.assertNotIn("-color_range", " ".join(second))

    def test_unspecified_omits_color_range_folder(self):
        settings = self._folder_settings(policy="unspecified")
        job = self._folder_job(color_range=None)
        FFmWiz.apply_folder_batch_color_range(job, settings, {"path": Path("a.mkv")})
        self.assertEqual(FFmWiz.color_range_output_args(job), [])

    def test_tv_choice_emits_color_range_cpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._unknown_range_encode(tmp, use_gpu=False)
            answers["color_range_choice"] = "tv"
            self.assertIn("-color_range:v:0 tv", self.command_text(answers))

    # ===================================================================
    # 10-bit NVENC rendered command: p010le + main10, no 8-bit override
    # ===================================================================

    def _tenbit_answers(self, output_dir: str, use_gpu: bool) -> dict:
        answers = self.base_answers(output_dir)
        answers["use_gpu"] = use_gpu
        for stream in answers["video_streams"]:
            stream["codec_name"] = "hevc"
            stream["pix_fmt"] = "yuv420p10le"
        return answers

    def test_10bit_nvenc_rendered_command_uses_p010_main10(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._tenbit_answers(tmp, use_gpu=True)
            text = self.command_text(answers)
            self.assertIn("format=p010le", text)
            self.assertIn("main10", text)
            self.assert_not_contains_any(
                text, ["format=nv12", "format=yuv420p ", "-pix_fmt yuv420p", "-profile:v main "]
            )
            self.assertNotIn("-profile:v main\n", text)

    def test_10bit_cpu_rendered_command_uses_yuv420p10le_main10(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._tenbit_answers(tmp, use_gpu=False)
            text = self.command_text(answers)
            self.assertIn("format=yuv420p10le", text)
            self.assertIn("main10", text)
            self.assert_not_contains_any(text, ["format=nv12", "-pix_fmt yuv420p "])

    def test_8bit_nvenc_rendered_command_uses_nv12_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = True
            for stream in answers["video_streams"]:
                stream["pix_fmt"] = "yuv420p"
            text = self.command_text(answers)
            self.assertIn("format=nv12", text)
            self.assertNotIn("format=p010le", text)
            self.assertNotIn("main10", text)

    # ===================================================================
    # Two-pass geometry + pixel-format parity (8-bit and 10-bit)
    # ===================================================================

    def _two_pass_field(self, cmd: list[str], flag: str) -> str:
        return cmd[cmd.index(flag) + 1] if flag in cmd else ""

    def _assert_two_pass_parity(self, answers: dict) -> None:
        cmd = self.command_for(answers)
        first, second, _ = FFmWiz.build_cpu_two_pass_commands(cmd, answers)
        for flag in ("-filter:v", "-profile:v", "-b:v", "-maxrate:v", "-bufsize:v"):
            self.assertEqual(
                self._two_pass_field(first, flag),
                self._two_pass_field(second, flag),
                msg=f"two-pass mismatch for {flag}",
            )

    def test_two_pass_parity_8bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            self._assert_two_pass_parity(answers)

    def test_two_pass_parity_10bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._tenbit_answers(tmp, use_gpu=False)
            self._assert_two_pass_parity(answers)
            first, second, _ = FFmWiz.build_cpu_two_pass_commands(
                self.command_for(answers), answers
            )
            self.assertIn("format=yuv420p10le", self._two_pass_field(first, "-filter:v"))
            self.assertEqual(self._two_pass_field(first, "-profile:v"), "main10")

    # ===================================================================
    # Pixel-format no-op (no destructive warning)
    # ===================================================================

    def test_pixfmt_420_to_nv12_no_destructive_warning(self):
        """yuv420p -> nv12 is a relabel: no bit-depth/chroma warning."""
        info = FFmWiz.compare_pixel_formats("yuv420p", "nv12")
        self.assertEqual(info["warnings"], [])
        self.assertEqual(info["bit_depth_conversion"], "no")
        self.assertEqual(info["chroma_conversion"], "no")

    # ===================================================================
    # HardSub pixel-format warnings
    # ===================================================================

    def test_hardsub_8bit_no_warning(self):
        info = FFmWiz.compare_pixel_formats("yuv420p", "yuv420p")
        self.assertEqual(info["warnings"], [])

    def test_hardsub_10bit_to_8bit_warns(self):
        info = FFmWiz.compare_pixel_formats("yuv420p10le", "yuv420p")
        self.assertTrue(any("bit depth will be reduced" in w for w in info["warnings"]))

    def test_hardsub_444_to_420_warns(self):
        info = FFmWiz.compare_pixel_formats("yuv444p", "yuv420p")
        self.assertTrue(
            any("Chroma subsampling will be reduced from 4:4:4 to 4:2:0" in w for w in info["warnings"])
        )


if __name__ == "__main__":
    unittest.main()
