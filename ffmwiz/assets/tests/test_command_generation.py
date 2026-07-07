"""Split from the command-generation suite (see command_gen_base.py)."""
import contextlib
import io
import os
import tempfile
from pathlib import Path
from unittest import mock
import FFmWiz
import cache_test_utils
from command_gen_base import CommandGenBase, _home_module


class CommandGenerationCoreTests(CommandGenBase):
    def test_format_bytes_keeps_media_sizes_in_mb(self):
        self.assertEqual(FFmWiz.format_bytes(500), "500 B")
        self.assertEqual(FFmWiz.format_bytes(1536), "1.5 KB")
        self.assertEqual(FFmWiz.format_bytes(1_073_741_824), "1024.0 MB")
        self.assertEqual(FFmWiz.format_bytes(1_118_000_000), "1066.2 MB")

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

    def test_progress_single_output_size_refresh_still_sets_bitrate(self):
        # The bitrate suppression above is ONLY for multi-output splits; a
        # single output must still get a refreshed bitrate from the on-disk size.
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "encoded.mp4"
            output.write_bytes(b"x" * 1_500_000)
            state = {"progress": "continue"}
            FFmWiz._apply_output_file_size_progress(state, [output], 12.0)
            self.assertIn("kbits/s", state.get("_ffmwiz_bitrate_text", ""))

    def test_progress_eta_frozen_between_heartbeats(self):
        # The render loop re-renders every ~0.25s to keep the line live, but the
        # ETA must only change when the media position (current_s) advances, so
        # it refreshes in lock-step with the percent/time/size fields instead of
        # drifting on every heartbeat. Same position across renders => same ETA.
        import re as _re
        import time as _time
        ansi = _re.compile(r"\x1b\[[0-9;]*m")

        def eta_of(line):
            m = _re.search(r"ETA\s+([0-9:]+|calculating)", ansi.sub("", line))
            return m.group(1) if m else None

        total = 600.0
        started = _time.perf_counter() - 60.0
        state = {"_ffmwiz_current_s": "60.0"}
        first = eta_of(FFmWiz._render_progress_line(state, total, started))
        # Heartbeat re-renders at the SAME position but a later wall clock.
        _time.sleep(0.2)
        second = eta_of(FFmWiz._render_progress_line(state, total, started))
        _time.sleep(0.2)
        third = eta_of(FFmWiz._render_progress_line(state, total, started))
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        # When the position advances, the ETA is recomputed (anchor changes).
        state["_ffmwiz_current_s"] = "120.0"
        fourth = eta_of(FFmWiz._render_progress_line(state, total, started))
        self.assertNotEqual(state.get("_ffmwiz_eta_anchor"), "60.000")

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
            with mock.patch.object(FFmWiz.services, "probe_packet_sizes", return_value={0: 1500, 1: 500}) as probe:
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
        with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="۰"):
            with self.assertRaises(FFmWiz.Back):
                FFmWiz.appio.ask_yes_no("Continue? (y/n) [n]: ", False)

    def test_zero_based_selection_still_accepts_track_zero(self):
        with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="0"):
            self.assertEqual(FFmWiz.ask_selection("tracks: ", 3, [0]), [0])

    def test_step_video_codec_rejects_unknown_before_accepting_valid_alias(self):
        prompts = iter(["h2654", "H264"])
        errors: list[str] = []
        answers = self.base_answers(".")
        answers["video_encoders"] = ["libx264", "libx265"]
        with mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=lambda _prompt: next(prompts)), \
             mock.patch.object(FFmWiz.appio, "error", side_effect=lambda message: errors.append(message)):
            FFmWiz.wizard.step_video_codec(answers)
        self.assertEqual(answers["video_codec"], "H264")
        self.assertTrue(any("Unknown video encoder" in message for message in errors))

    def test_ensure_launcher_file_preserves_user_modified_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.ps1"
            path.write_text("# user custom launcher\n", encoding="utf-8")
            FFmWiz.ensure_launcher_file(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "# user custom launcher\n")

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

    def test_stream_cleanup_output_suffix_matches_input_and_copy_maps_video(self):
        rules = FFmWiz.mux.MuxCleanupRules(
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
            media = FFmWiz.mux.MuxMediaFile(
                path=input_file,
                format={},
                streams=[FFmWiz.mux.MuxStreamInfo(index=0, codec_type="video", codec_name="h264")],
            )
            cmd, _audio, _subtitle = FFmWiz.mux_build_ffmpeg_command("ffmpeg", input_file, output, media, rules)
        self.assertIn("-map", cmd)
        self.assertIn("0:v?", cmd)
        self.assertIn("-c", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")

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
        with mock.patch.object(FFmWiz.metadata, "metadata_menu_selection", side_effect=["2", "0"]), \
                mock.patch.object(FFmWiz.metadata, "metadata_refresh_probe", return_value=probe), \
                mock.patch.object(FFmWiz.appio, "ask_raw", return_value="1") as ask_raw, \
                mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
                mock.patch.object(FFmWiz.services, "estimate_color_range", return_value=estimate) as estimate_color, \
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

    def test_crop_margin_prompt_accepts_zero_value(self):
        answers = {
            "_question_number": 1,
            "video_streams": [{"width": 1920, "height": 1080}],
            "format": {},
        }
        with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="0"):
            FFmWiz.step_crop_top(answers)
        self.assertEqual(answers["crop_top"], 0)

    def test_mux_stream_index_prompt_accepts_zero_value(self):
        answers = {"_question_number": 1}
        with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="0"):
            self.assertEqual(FFmWiz.mux_ask_text(answers, "Audio stream indexes to keep", "use b to go back", zero_is_value=True), "0")

    def test_mux_csv_stream_indexes_accept_zero_value(self):
        answers = {"_question_number": 1}
        with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="0"):
            self.assertEqual(FFmWiz.mux_ask_csv_int_required(answers, "Audio stream indexes to edit", [0, 2]), [0])

    def test_stream_cleanup_detects_when_remux_is_not_needed(self):
        rules = FFmWiz.mux.MuxCleanupRules(
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
        media = FFmWiz.mux.MuxMediaFile(
            path=Path("input.mkv"),
            format={},
            streams=[
                FFmWiz.mux.MuxStreamInfo(index=0, codec_type="video", codec_name="h264"),
                FFmWiz.mux.MuxStreamInfo(index=1, codec_type="audio", codec_name="aac", disposition_default=1),
                FFmWiz.mux.MuxStreamInfo(index=2, codec_type="subtitle", codec_name="ass", disposition_default=1),
            ],
        )
        audio_keep = FFmWiz.mux_selected_audio_streams(media, rules)
        subtitle_keep = FFmWiz.mux_selected_subtitle_streams(media, rules)
        self.assertEqual(FFmWiz.mux_remux_needed_reasons(media, rules, audio_keep, subtitle_keep), [])
        rules.metadata_edits = [FFmWiz.mux.MuxStreamMetadataEdit(codec_type="audio", match_indexes=[1], title="Edited")]
        self.assertIn("stream metadata is edited", FFmWiz.mux_remux_needed_reasons(media, rules, audio_keep, subtitle_keep))

    def test_stream_cleanup_visible_question_numbers_do_not_jump_when_steps_are_skipped(self):
        media_files = [
            FFmWiz.mux.MuxMediaFile(
                path=Path("input.mkv"),
                format={},
                streams=[
                    FFmWiz.mux.MuxStreamInfo(index=0, codec_type="video", codec_name="h264"),
                    FFmWiz.mux.MuxStreamInfo(index=1, codec_type="audio", codec_name="aac", language="jpn"),
                ],
            )
        ]
        prompts: list[str] = []
        responses = iter(["", "n", "", "", "", "", ""])

        def fake_ask_raw(prompt: str) -> str:
            prompts.append(prompt.strip())
            return next(responses)

        answers = {"_question_number": 1, "_mux_next_question_number": 2}
        with mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=fake_ask_raw), \
             mock.patch.object(FFmWiz.appio, "note", lambda _message: None):
            FFmWiz.mux_configure_rules(answers, media_files)
            FFmWiz.mux_ask_output_base(answers, Path("input.mkv"))
            FFmWiz.mux_ask_yes_no(answers, "Start Stream Cleanup Remux now?", True)

        visible_numbers = [int(prompt.split(".", 1)[0]) for prompt in prompts]
        self.assertEqual(visible_numbers, [2, 3, 4, 5, 6, 7, 8])
        self.assertIn("Keep input metadata?", prompts[0])
        self.assertIn("Enter output folder path", prompts[-2])
        self.assertIn("Start Stream Cleanup Remux now?", prompts[-1])

    def test_run_wizard_uses_unified_editor_before_legacy_video_edit_prompts(self):
        calls: list[str] = []
        originals = {
            "step_input_path": FFmWiz.wizard.step_input_path,
            "step_output_location": FFmWiz.wizard.step_output_location,
            "step_output_format": FFmWiz.wizard.step_output_format,
            "step_video_codec": FFmWiz.wizard.step_video_codec,
            "step_use_gpu": FFmWiz.wizard.step_use_gpu,
            "step_unified_video_editor_for_encode": FFmWiz.wizard.step_unified_video_editor_for_encode,
            "step_video_bitrate": FFmWiz.wizard.step_video_bitrate,
            "step_resolution": FFmWiz.wizard.step_resolution,
            "step_fps": FFmWiz.wizard.step_fps,
            "step_start_now": FFmWiz.wizard.step_start_now,
            "ask_raw": FFmWiz.appio.ask_raw,
            "get_video_fps": FFmWiz.services.get_video_fps,
            "stream_duration_seconds": FFmWiz.services.stream_duration_seconds,
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
            FFmWiz.wizard.step_input_path = lambda answers: calls.append("input")
            FFmWiz.wizard.step_output_location = lambda answers: calls.append("output")
            FFmWiz.wizard.step_output_format = lambda answers: calls.append("format")
            FFmWiz.wizard.step_video_codec = lambda answers: calls.append("codec")
            FFmWiz.wizard.step_use_gpu = lambda answers: calls.append("gpu")
            FFmWiz.wizard.step_unified_video_editor_for_encode = fake_unified
            FFmWiz.wizard.step_video_bitrate = lambda answers: calls.append("bitrate")
            FFmWiz.wizard.step_resolution = lambda answers: calls.append("resolution")
            FFmWiz.wizard.step_fps = lambda answers: calls.append("fps")
            FFmWiz.wizard.step_start_now = lambda answers: calls.append("start")
            FFmWiz.appio.ask_raw = lambda _prompt: (_ for _ in ()).throw(AssertionError("legacy prompt was shown"))
            FFmWiz.services.get_video_fps = lambda _answers: 30.0
            FFmWiz.services.stream_duration_seconds = lambda _stream, _fmt=None: 100.0
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
                setattr(_home_module(name), name, original)

        self.assertIn("unified", calls)
        self.assertTrue(answers["crop_enabled"])
        self.assertEqual(answers["cut_keep_ranges"], [(10.0, 20.0)])
        self.assertTrue(answers["video_speed_enabled"])
        self.assertEqual(answers["video_speed_factor"], 1.5)
        self.assertTrue(answers["reverse_video"])

    def test_step_use_gpu_skips_prompt_when_gpu_unavailable(self):
        answers = {"ffmpeg": "ffmpeg", "video_encoders": ["libx265"], "gpu_available": False}
        with mock.patch.object(FFmWiz.appio, "ask_yes_no") as ask_yes_no:
            FFmWiz.wizard.step_use_gpu(answers)
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

    def test_av1_cpu_resolves_to_libsvtav1_with_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["video_codec"] = "av1"
            answers["use_gpu"] = False
            text = self.command_text(answers)
        self.assertIn("-c:v libsvtav1", text)
        self.assertIn("-preset 6", text)
        self.assertIn("-svtav1-params tune=0", text)
        self.assertIn("-b:v 400k", text)
        # SVT-AV1 VBR targets -b:v only; no HRD maxrate/bufsize.
        self.assertNotIn("-maxrate", text)
        self.assertNotIn("-bufsize", text)

    def test_parse_env_config_parses_settings(self):
        text = "\n".join([
            "# a comment line",
            "input_path=I:/My Videos/clip 01.mkv",
            'video_codec="AV1"',
            "use_gpu=n",
            "crop_top=12",
            "export gui_engine=qml",
            "",
            "line_without_equals",
        ])
        cfg = FFmWiz.parse_env_config(text)
        s = cfg["settings"]
        self.assertEqual(s["input_path"], "I:/My Videos/clip 01.mkv")  # spaces ok
        self.assertEqual(s["video_codec"], "AV1")                       # quotes stripped
        self.assertEqual(s["use_gpu"], "n")
        self.assertEqual(s["crop_top"], "12")
        self.assertEqual(s["gui_engine"], "qml")                        # export prefix
        self.assertNotIn("line_without_equals", s)
        self.assertEqual(FFmWiz.config_value(cfg, "video_codec"), "AV1")
        self.assertFalse(FFmWiz.parse_bool_config(FFmWiz.config_value(cfg, "use_gpu"), True))

    def test_config_is_env_and_template_matches_example(self):
        from pathlib import Path as _P
        self.assertEqual(FFmWiz.CONFIG_FILE_NAME, "config.env")
        example = (_P(FFmWiz.__file__).parent / "config.env.example").read_text(encoding="utf-8")
        # The auto-created config.env must match the committed sample exactly.
        self.assertEqual(FFmWiz.CONFIG_TEMPLATE, example)

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
    # SAR / DAR parsing, calculation, and setsar handling
    # ===================================================================

    def test_parse_rational_valid_and_invalid(self):
        self.assertAlmostEqual(FFmWiz.parse_rational("16:9"), 16 / 9)
        self.assertAlmostEqual(FFmWiz.parse_rational("4/3"), 4 / 3)
        self.assertAlmostEqual(FFmWiz.parse_rational("1.5"), 1.5)
        for bad in ("0:1", "0/0", "-1", "N/A", "unknown", "", None, "abc"):
            self.assertIsNone(FFmWiz.parse_rational(bad))

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

    def test_pixfmt_422_to_420_warns(self):
        """Test 15: yuv422p -> yuv420p warns about chroma reduction."""
        info = FFmWiz.compare_pixel_formats("yuv422p", "yuv420p")
        self.assertEqual(info["chroma_conversion"], "4:2:2 -> 4:2:0")
        self.assertTrue(any("Chroma subsampling will be reduced from 4:2:2 to 4:2:0" in w for w in info["warnings"]))

    def test_pixfmt_rgb_to_yuv_warns(self):
        """Test 17: rgb24 -> yuv420p warns about RGB->YUV conversion."""
        info = FFmWiz.compare_pixel_formats("rgb24", "yuv420p")
        self.assertTrue(any("RGB video will be converted to YUV 4:2:0" in w for w in info["warnings"]))

    def test_pixfmt_unknown_does_not_crash(self):
        """Test 18: unknown source pixel format -> no crash, operation unknown."""
        info = FFmWiz.compare_pixel_formats(None, "yuv420p")
        self.assertEqual(info["operation"], "unknown")
        self.assertEqual(info["warnings"], [])

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

    # ===================================================================
    # Pixel-format no-op (no destructive warning)
    # ===================================================================

    def test_pixfmt_420_to_nv12_no_destructive_warning(self):
        """yuv420p -> nv12 is a relabel: no bit-depth/chroma warning."""
        info = FFmWiz.compare_pixel_formats("yuv420p", "nv12")
        self.assertEqual(info["warnings"], [])
        self.assertEqual(info["bit_depth_conversion"], "no")
        self.assertEqual(info["chroma_conversion"], "no")

    def test_back_nav_preserves_do_not_force_default(self):
        """Re-entering the menu with a stored 'unspecified' choice defaults to 2."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            answers["color_range_choice"] = "unspecified"
            captured = {}

            def fake_ask(prompt):
                captured["prompt"] = prompt
                return ""  # Enter keeps the default.

            with mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=fake_ask), \
                    contextlib.redirect_stdout(io.StringIO()):
                FFmWiz.step_color_range(answers)
            self.assertIn("[2]", captured["prompt"])
            self.assertEqual(answers["color_range_choice"], "unspecified")

    def test_environment_fingerprint_stable(self):
        with mock.patch.object(FFmWiz.services, "capability_environment_identity",
                               return_value=self._fake_identity()):
            _, k1 = FFmWiz.services.capability_environment_key("ffmpeg", "ffprobe", "libx265")
            _, k2 = FFmWiz.services.capability_environment_key("ffmpeg", "ffprobe", "libx265")
        self.assertEqual(k1, k2)

    def test_environment_fingerprint_path_invalidates(self):
        def ident(ffmpeg, ffprobe, *, include_gpu):
            return self._fake_identity(ffmpeg_path=ffmpeg)
        with mock.patch.object(FFmWiz.services, "capability_environment_identity", side_effect=ident):
            _, k1 = FFmWiz.services.capability_environment_key("C:/a/ffmpeg.exe", "ffprobe", "libx265")
            _, k2 = FFmWiz.services.capability_environment_key("C:/b/ffmpeg.exe", "ffprobe", "libx265")
        self.assertNotEqual(k1, k2)

    def test_environment_fingerprint_build_invalidates(self):
        def ident(ffmpeg, ffprobe, *, include_gpu):
            return self._fake_identity(ffmpeg_build_hash="v1" if "a" in ffmpeg else "v2")
        with mock.patch.object(FFmWiz.services, "capability_environment_identity", side_effect=ident):
            _, k1 = FFmWiz.services.capability_environment_key("a", "ffprobe", "libx265")
            _, k2 = FFmWiz.services.capability_environment_key("b", "ffprobe", "libx265")
        self.assertNotEqual(k1, k2)

    def test_cpu_entry_does_not_include_gpu(self):
        captured = {}
        def ident(ffmpeg, ffprobe, *, include_gpu):
            captured["include_gpu"] = include_gpu
            return self._fake_identity()
        with mock.patch.object(FFmWiz.services, "capability_environment_identity", side_effect=ident):
            FFmWiz.services.capability_environment_key("ffmpeg", "ffprobe", "libx265")
        self.assertFalse(captured["include_gpu"])

    def test_lazy_probe_runs_once_and_caches(self):
        verified = {"status": "verified", "expected_final_range": "tv",
                    "probe_method": "real encode + ffprobe", "encoder": "libx265",
                    "container_family": "mkv", "sample_command_hash": "h",
                    "ffprobe_result": "tv", "verified_at_utc": "t", "error": None}
        with mock.patch.object(FFmWiz.services, "capability_environment_identity",
                               return_value=self._fake_identity()), \
                mock.patch.object(FFmWiz.services, "probe_color_range_capability",
                                  return_value=verified) as probe:
            a = self._cap_answers()
            r1 = FFmWiz.services.resolve_capability(a)
            r2 = FFmWiz.services.resolve_capability(a)  # session memo -> no second probe
            FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()
            r3 = FFmWiz.services.resolve_capability(a)  # file cache -> still no probe
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(r1["capability_source"], "fresh probe")
        self.assertEqual(r2["capability_source"], "fresh probe")  # memoized copy
        self.assertEqual(r3["capability_source"], "verified cache")
        self.assertEqual(r3["expected_final_range"], "tv")

    def test_entry_from_other_environment_not_reused(self):
        # Seed cache under a different env key.
        cache = {"schema_version": 1, "environments": {"OTHER": {"capabilities": {
            "color_range_do_not_force": {"libx265|mkv": {"status": "verified",
                                                          "expected_final_range": "pc"}}}}}}
        FFmWiz.save_capability_cache(cache)
        verified = {"status": "verified", "expected_final_range": "tv",
                    "probe_method": "m", "encoder": "libx265", "container_family": "mkv",
                    "sample_command_hash": "h", "ffprobe_result": "tv",
                    "verified_at_utc": "t", "error": None}
        with mock.patch.object(FFmWiz.services, "capability_environment_identity",
                               return_value=self._fake_identity()), \
                mock.patch.object(FFmWiz.services, "probe_color_range_capability",
                                  return_value=verified) as probe:
            r = FFmWiz.services.resolve_capability(self._cap_answers())
        self.assertEqual(probe.call_count, 1)  # other env not reused
        self.assertEqual(r["expected_final_range"], "tv")

    def test_probe_failure_does_not_abort(self):
        failed = {"status": "probe_failed", "expected_final_range": None, "probe_method": "m",
                  "encoder": "libx265", "container_family": "mkv", "sample_command_hash": "h",
                  "ffprobe_result": None, "verified_at_utc": "t", "error": "boom"}
        with mock.patch.object(FFmWiz.services, "capability_environment_identity",
                               return_value=self._fake_identity()), \
                mock.patch.object(FFmWiz.services, "probe_color_range_capability", return_value=failed):
            r = FFmWiz.services.resolve_capability(self._cap_answers())
        self.assertEqual(r["status"], "probe_failed")
        self.assertIsNone(r["expected_final_range"])

    def test_corrupted_cache_does_not_crash(self):
        Path(self._cache_dir, FFmWiz.CAPABILITY_CACHE_FILENAME).write_text("{not json", encoding="utf-8")
        data = FFmWiz.load_capability_cache()
        self.assertEqual(data["environments"], {})

    def test_atomic_cache_write_roundtrip(self):
        data = {"schema_version": 1, "environments": {"E": {"capabilities": {}}}}
        self.assertTrue(FFmWiz.save_capability_cache(data))
        self.assertTrue(Path(self._cache_dir, FFmWiz.CAPABILITY_CACHE_FILENAME).exists())
        self.assertEqual(FFmWiz.load_capability_cache()["environments"], {"E": {"capabilities": {}}})

    def test_view_cache_output_accurate(self):
        cache = {"schema_version": 1, "environments": {"ENVKEY123456": {
            "ffmpeg_identity": {"version": "ffmpeg 8.1.1", "build_hash": "x", "path": "p"},
            "hardware_identity": {"gpu": "n/a", "driver": "n/a"},
            "capabilities": {"color_range_do_not_force": {
                "libx265|mkv": {"status": "verified", "expected_final_range": "tv",
                                "verified_at_utc": "t"}}}}}}
        FFmWiz.save_capability_cache(cache)
        buf = io.StringIO()
        with mock.patch.object(FFmWiz.services, "capability_environment_key", return_value=({}, "ENVKEY123456")), \
                contextlib.redirect_stdout(buf):
            FFmWiz._capability_cache_view("ffmpeg", "ffprobe")
        out = buf.getvalue()
        self.assertIn("libx265|mkv", out)
        self.assertIn("status=verified", out)
        self.assertIn("expected_final_range=tv", out)

    def test_stale_mismatch_invalidates_entry(self):
        cache = {"schema_version": 1, "environments": {"K": {"capabilities": {
            "color_range_do_not_force": {"libx265|mkv": {"status": "verified",
                                                         "expected_final_range": "tv"}}}}}}
        FFmWiz.save_capability_cache(cache)
        with mock.patch.object(FFmWiz.services, "capability_environment_key", return_value=({}, "K")):
            FFmWiz.invalidate_capability_entry(self._cap_answers())
        data = FFmWiz.load_capability_cache()
        self.assertNotIn("libx265|mkv",
                         data["environments"]["K"]["capabilities"]["color_range_do_not_force"])

    # ===================================================================
    # SAR/DAR provenance separation (raw ffprobe vs resolved)
    # ===================================================================

    def test_raw_fields_unchanged_after_resolution(self):
        """Raw ffprobe SAR/DAR are preserved exactly; resolved values are separate."""
        stream = {"width": 720, "height": 576, "sample_aspect_ratio": "16:15"}
        answers = {"video_streams": [stream]}
        info = FFmWiz.sar_dar_info(answers)
        # Raw SAR present (16:15 -> ~1.0667), raw DAR absent.
        self.assertAlmostEqual(info["raw_ffprobe_sar"], 16 / 15, places=4)
        self.assertIsNone(info["raw_ffprobe_dar"])
        # Resolved values are stored separately and do not overwrite the stream.
        self.assertIsNotNone(info["resolved_dar"])
        self.assertEqual(stream.get("sample_aspect_ratio"), "16:15")
        self.assertNotIn("display_aspect_ratio", stream)

    def test_resolver_is_pure_and_idempotent(self):
        """resolve_video_geometry never mutates input and is idempotent."""
        g1 = FFmWiz.resolve_video_geometry(2160, 3840, None, 9 / 16)
        g2 = FFmWiz.resolve_video_geometry(2160, 3840, None, 9 / 16)
        self.assertEqual(g1, g2)
        # Feeding the resolved DAR back as raw must NOT change Case-B provenance
        # to a detected SAR-derived case; it is a different (legitimate) input,
        # but the resolver never consumes its own dict.
        self.assertEqual(g1["raw_ffprobe_dar"], 9 / 16)
        self.assertEqual(g1["raw_ffprobe_sar"], None)

    def test_fixture_both_unknown_provenance(self):
        """Fixture 2: no raw SAR/DAR -> fallback SAR 1:1, calculated DAR."""
        info = FFmWiz.sar_dar_info({"video_streams": [{"width": 2160, "height": 3840}]})
        self.assertEqual(info["sar_text"], "1:1")
        self.assertEqual(info["sar_source"], "fallback assumption")
        self.assertEqual(info["dar_text"], "9:16")
        self.assertEqual(info["dar_source"], "calculated from coded resolution and fallback SAR")
        self.assertEqual(info["pixel_shape"], "square (assumed)")
        self.assertTrue(info["fallback_used"])

    def test_both_fixtures_same_geometry_different_provenance(self):
        """The two 2160x3840 fixtures match numerically but differ in provenance."""
        detected = FFmWiz.sar_dar_info({"video_streams": [{"width": 2160, "height": 3840,
                                                          "display_aspect_ratio": "9:16"}]})
        fallback = FFmWiz.sar_dar_info({"video_streams": [{"width": 2160, "height": 3840}]})
        self.assertAlmostEqual(detected["effective_dar_decimal"],
                               fallback["effective_dar_decimal"], places=6)
        self.assertNotEqual(detected["sar_source"], fallback["sar_source"])
        self.assertNotEqual(detected["dar_source"], fallback["dar_source"])
        self.assertFalse(detected["fallback_used"])
        self.assertTrue(fallback["fallback_used"])

    def test_no_report_labels_fallback_as_detected(self):
        """A both-unknown result never labels SAR/DAR as detected by ffprobe."""
        info = FFmWiz.sar_dar_info({"video_streams": [{"width": 2160, "height": 3840}]})
        self.assertNotIn("detected by ffprobe", info["sar_source"])
        self.assertNotIn("detected by ffprobe", info["dar_source"])

    def test_provenance_independent_per_stream(self):
        """Resolving one stream does not contaminate another (Folder Encode)."""
        s1 = {"width": 2160, "height": 3840, "display_aspect_ratio": "9:16"}
        s2 = {"width": 2160, "height": 3840}
        i1 = FFmWiz.sar_dar_info({"video_streams": [s1]})
        i2 = FFmWiz.sar_dar_info({"video_streams": [s2]})
        self.assertEqual(i1["dar_source"], "detected by ffprobe")
        self.assertTrue(i2["fallback_used"])
        # Original raw streams unchanged.
        self.assertEqual(s1.get("display_aspect_ratio"), "9:16")
        self.assertNotIn("display_aspect_ratio", s2)

    def test_workflow_crop_does_not_overwrite_raw_geometry(self):
        """Crop keeps normalization and never writes post-crop DAR into raw fields."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["color_range_choice"] = "tv"
            stream = {"codec_type": "video", "codec_name": "h264",
                      "width": 720, "height": 576, "sample_aspect_ratio": "16:15"}
            answers["video_streams"] = [stream]
            answers["resolution"] = "n"
            answers["crop_enabled"] = True
            answers["crop_top"] = 2
            answers["crop_bottom"] = 2
            answers["crop_left"] = 4
            answers["crop_right"] = 4
            text = self.command_text(answers)
            self.assertIn("crop=", text)
            self.assertNotIn("pad=", text)  # no black compatibility padding
            # Raw source SAR is not overwritten by any post-crop geometry.
            self._assert_raw_immutable(stream, "16:15", None)
            info = FFmWiz.sar_dar_info(answers)
            self.assertEqual(info["raw_ffprobe_sar"], 16 / 15)
            self.assertEqual(info["sar_source"], "detected by ffprobe")

    # ===================================================================
    # Cleanup-safety: owned temp cache only; protected paths refused
    # ===================================================================

    def test_cleanup_isolated_temp_cache_created_with_marker(self):
        run_id = "run-" + os.urandom(4).hex()
        path = cache_test_utils.create_owned_temp_cache_dir(run_id)
        try:
            p = Path(path)
            self.assertTrue(p.is_dir())
            self.assertEqual(Path(tempfile.gettempdir()).resolve(), p.resolve().parent)
            marker = p / cache_test_utils.TEST_CACHE_OWNER_MARKER
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), run_id)
        finally:
            cache_test_utils.safe_remove_owned_temp_dir(path, run_id, tempfile.gettempdir())

    def test_cleanup_owned_delete_and_idempotent(self):
        run_id = "run-" + os.urandom(4).hex()
        path = cache_test_utils.create_owned_temp_cache_dir(run_id)
        self.assertTrue(cache_test_utils.safe_remove_owned_temp_dir(path, run_id, tempfile.gettempdir()))
        self.assertFalse(Path(path).exists())
        # Idempotent second call.
        self.assertFalse(cache_test_utils.safe_remove_owned_temp_dir(path, run_id, tempfile.gettempdir()))

    def test_cleanup_marker_mismatch_blocks(self):
        run_id = "run-" + os.urandom(4).hex()
        path = cache_test_utils.create_owned_temp_cache_dir(run_id)
        try:
            with self.assertRaises(RuntimeError):
                cache_test_utils.safe_remove_owned_temp_dir(path, "WRONG-ID", tempfile.gettempdir())
            self.assertTrue(Path(path).exists())
        finally:
            cache_test_utils.safe_remove_owned_temp_dir(path, run_id, tempfile.gettempdir())

    def test_cleanup_missing_marker_blocks(self):
        run_id = "run-" + os.urandom(4).hex()
        path = cache_test_utils.create_owned_temp_cache_dir(run_id)
        try:
            (Path(path) / cache_test_utils.TEST_CACHE_OWNER_MARKER).unlink()
            with self.assertRaises(RuntimeError):
                cache_test_utils.safe_remove_owned_temp_dir(path, run_id, tempfile.gettempdir())
            self.assertTrue(Path(path).exists())
        finally:
            import shutil as _sh
            _sh.rmtree(path, ignore_errors=True)

    def test_cleanup_refuses_protected_paths(self):
        root = tempfile.gettempdir()
        project_root = Path(FFmWiz.__file__).resolve().parent
        protected_candidates = [
            project_root,                                  # project root
            project_root / FFmWiz.CAPABILITY_CACHE_DIRNAME,  # project .cache
            Path.home(),                                   # home
            Path(project_root.anchor),                     # filesystem root
            Path(tempfile.gettempdir()),                   # temp root itself
        ]
        for candidate in protected_candidates:
            with self.assertRaises(RuntimeError):
                cache_test_utils.safe_remove_owned_temp_dir(candidate, "any", root)

    def test_cleanup_refuses_path_outside_temp_root(self):
        project_root = Path(FFmWiz.__file__).resolve().parent
        with self.assertRaises(RuntimeError):
            cache_test_utils.safe_remove_owned_temp_dir(project_root / "some_sub", "any", tempfile.gettempdir())

    def test_cleanup_refuses_symlink_to_protected(self):
        run_id = "run-" + os.urandom(4).hex()
        link_parent = cache_test_utils.create_owned_temp_cache_dir(run_id)
        link = Path(link_parent) / "link_to_cache"
        target = Path(FFmWiz.__file__).resolve().parent / FFmWiz.CAPABILITY_CACHE_DIRNAME
        try:
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation not permitted on this system")
            # Resolves to project .cache -> protected -> refused.
            with self.assertRaises(RuntimeError):
                cache_test_utils.safe_remove_owned_temp_dir(link, run_id, tempfile.gettempdir())
        finally:
            cache_test_utils.safe_remove_owned_temp_dir(link_parent, run_id, tempfile.gettempdir())

    def test_corrupted_cache_recovery_keeps_unrelated_files(self):
        Path(self._cache_dir, FFmWiz.CAPABILITY_CACHE_FILENAME).write_text("{bad", encoding="utf-8")
        unrelated = Path(self._cache_dir, "keep_me.txt")
        unrelated.write_text("data", encoding="utf-8")
        FFmWiz.load_capability_cache()  # moves corrupt aside, does not delete others
        self.assertTrue(unrelated.exists())

    def test_teardown_uses_isolated_cache_not_real(self):
        """The active cache path resolves under the owned temp dir, not project."""
        active = FFmWiz.capability_cache_path().resolve()
        self.assertEqual(active.parent, Path(self._cache_dir).resolve())
        project_cache = Path(FFmWiz.__file__).resolve().parent / FFmWiz.CAPABILITY_CACHE_DIRNAME
        self.assertNotEqual(active.parent, project_cache)

    def test_production_has_no_test_only_cache_helpers(self):
        """Test-only cleanup infrastructure must not live in production FFmWiz.py."""
        for symbol in ("create_owned_temp_cache_dir", "safe_remove_owned_temp_dir",
                       "TEST_CACHE_OWNER_MARKER", "protected_cleanup_paths"):
            self.assertFalse(hasattr(FFmWiz, symbol),
                             "FFmWiz unexpectedly exposes test-only symbol %s" % symbol)
        src = Path(FFmWiz.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".ffmwiz_test_cache_owner", src)
        self.assertNotIn("create_owned_temp_cache_dir", src)
        self.assertNotIn("safe_remove_owned_temp_dir", src)
        self.assertNotIn("ffmwiz_test_cache_", src)
        # The test utility module provides them instead.
        self.assertTrue(hasattr(cache_test_utils, "create_owned_temp_cache_dir"))
        self.assertTrue(hasattr(cache_test_utils, "safe_remove_owned_temp_dir"))

    def test_clear_removes_recovery_created_corrupt_backup(self):
        """The clear action removes the exact corrupt-backup the recovery makes."""
        cap = FFmWiz.capability_cache_path()
        cap.parent.mkdir(parents=True, exist_ok=True)
        cap.write_text("{not valid json", encoding="utf-8")
        # Real recovery path renames the bad file to ffmpeg_capabilities.corrupt.
        FFmWiz.load_capability_cache()
        corrupt = cap.with_suffix(".corrupt")
        self.assertEqual(corrupt.name, "ffmpeg_capabilities.corrupt")
        self.assertTrue(corrupt.exists())
        # Recreate a valid primary file as well.
        FFmWiz.save_capability_cache({"schema_version": 1, "environments": {}})
        self.assertTrue(cap.exists())
        with mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()):
            FFmWiz._capability_cache_clear()
        self.assertFalse(cap.exists())
        self.assertFalse(corrupt.exists())

    def test_clear_keeps_similar_but_unrelated_filenames(self):
        """Files that merely resemble cache artifacts must survive the clear."""
        cap = FFmWiz.capability_cache_path()
        cap.parent.mkdir(parents=True, exist_ok=True)
        FFmWiz.save_capability_cache({"schema_version": 1, "environments": {}})
        decoys = [
            Path(self._cache_dir, "ffmpeg_capabilities.json.bak"),
            Path(self._cache_dir, "my_ffmpeg_capabilities.json"),
            Path(self._cache_dir, "capabilities.corrupt"),
            Path(self._cache_dir, "ffmpeg_capabilities.corrupt.old"),
            Path(self._cache_dir, "notes.txt"),
        ]
        for d in decoys:
            d.write_text("keep", encoding="utf-8")
        with mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()):
            FFmWiz._capability_cache_clear()
        self.assertFalse(cap.exists())
        for d in decoys:
            self.assertTrue(d.exists(), "decoy unexpectedly removed: %s" % d.name)

    def test_clear_missing_files_is_idempotent_noop(self):
        """Clearing when no cache files exist is a safe no-op (no prompt, no error)."""
        cap = FFmWiz.capability_cache_path()
        self.assertFalse(cap.exists())
        with mock.patch.object(FFmWiz.appio, "ask_yes_no",
                               side_effect=AssertionError("should not prompt")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            FFmWiz._capability_cache_clear()
            FFmWiz._capability_cache_clear()  # idempotent
        self.assertIn("already empty", out.getvalue())
        self.assertTrue(Path(self._cache_dir).is_dir())

    def test_clear_partial_failure_does_not_claim_full_success(self):
        """If one owned artifact cannot be deleted, unrelated files survive and
        the message does not falsely claim a full clear."""
        cap = FFmWiz.capability_cache_path()
        cap.parent.mkdir(parents=True, exist_ok=True)
        FFmWiz.save_capability_cache({"schema_version": 1, "environments": {}})
        # Make the corrupt-backup name an undeletable directory (unlink fails).
        corrupt_dir = cap.with_suffix(".corrupt")
        corrupt_dir.mkdir()
        (corrupt_dir / "blocker.txt").write_text("x", encoding="utf-8")
        unrelated = Path(self._cache_dir, "survivor.json")
        unrelated.write_text("{}", encoding="utf-8")
        buf = io.StringIO()
        with mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
                contextlib.redirect_stdout(buf):
            FFmWiz._capability_cache_clear()
        text = buf.getvalue()
        self.assertFalse(cap.exists())               # primary removed
        self.assertTrue(corrupt_dir.is_dir())         # failed artifact remains
        self.assertTrue(unrelated.exists())           # unrelated survives
        self.assertIn("partially cleared", text)
        self.assertNotIn("Capability cache cleared (", text)

    # ===================================================================
    # Real-cache read-only snapshot safety
    # ===================================================================

    def test_snapshot_is_read_only_for_existing_file(self):
        """Snapshotting an existing cache file does not modify it."""
        with tempfile.TemporaryDirectory(prefix="ffmwiz_snap_") as tmp:
            f = Path(tmp, "ffmpeg_capabilities.json")
            f.write_text('{"schema_version":1,"environments":{}}', encoding="utf-8")
            before = (f.stat().st_size, f.stat().st_mtime_ns, f.read_bytes())
            snap = cache_test_utils.snapshot_runtime_cache_state(tmp)
            after = (f.stat().st_size, f.stat().st_mtime_ns, f.read_bytes())
            self.assertEqual(before, after)  # unchanged
            self.assertTrue(snap["ffmpeg_capabilities.json"]["exists"])
            self.assertIsNotNone(snap["ffmpeg_capabilities.json"]["sha256"])
            self.assertFalse(snap["ffmpeg_capabilities.corrupt"]["exists"])

    def test_snapshot_does_not_create_absent_files(self):
        """Snapshotting an empty cache dir creates nothing."""
        with tempfile.TemporaryDirectory(prefix="ffmwiz_snap_") as tmp:
            snap = cache_test_utils.snapshot_runtime_cache_state(tmp)
            for name in cache_test_utils.CAPABILITY_CACHE_OWNED_FILENAMES:
                self.assertFalse(snap[name]["exists"])
                self.assertIsNone(snap[name]["sha256"])
                self.assertFalse(Path(tmp, name).exists())  # not created

    def test_snapshot_does_not_create_runtime_cache_dir(self):
        """The default runtime snapshot never creates the real .cache directory."""
        runtime_dir = cache_test_utils.runtime_cache_dir()
        existed_before = runtime_dir.exists()
        cache_test_utils.snapshot_runtime_cache_state()  # default = real dir
        self.assertEqual(runtime_dir.exists(), existed_before)

    # ===================================================================
    # Isolated cache environment save/restore
    # ===================================================================

    def test_isolated_cache_env_restores_previous_value(self):
        os.environ["FFMWIZ_CACHE_DIR"] = "SENTINEL_PREV_VALUE"
        try:
            with cache_test_utils.isolated_cache_env() as (path, _run):
                self.assertEqual(os.environ["FFMWIZ_CACHE_DIR"], path)
            self.assertEqual(os.environ["FFMWIZ_CACHE_DIR"], "SENTINEL_PREV_VALUE")
        finally:
            os.environ.pop("FFMWIZ_CACHE_DIR", None)

    def test_isolated_cache_env_restores_on_exception(self):
        os.environ.pop("FFMWIZ_CACHE_DIR", None)
        with self.assertRaises(ValueError):
            with cache_test_utils.isolated_cache_env():
                raise ValueError("boom")
        # Absent before -> absent after, even though the body raised.
        self.assertNotIn("FFMWIZ_CACHE_DIR", os.environ)

    def test_writable_tests_use_isolated_cache_dir(self):
        active = FFmWiz.capability_cache_path().resolve()
        self.assertEqual(active.parent, Path(self._cache_dir).resolve())
        self.assertEqual(os.environ.get("FFMWIZ_CACHE_DIR"), self._cache_dir)

    # ===================================================================
    # Validation sentinel safety
    # ===================================================================

    def test_sentinel_exclusive_creation_and_unique_name(self):
        info = cache_test_utils.create_validation_sentinel(self._cache_dir, self._cache_run_id)
        try:
            self.assertIn(self._cache_run_id, info["name"])
            self.assertTrue(info["name"].startswith(".ffmwiz_validation_sentinel_"))
            self.assertNotIn(info["name"], cache_test_utils.CAPABILITY_CACHE_OWNED_FILENAMES)
            self.assertEqual(Path(info["path"]).read_text(encoding="utf-8"), info["token"])
        finally:
            cache_test_utils.remove_validation_sentinel(info, self._cache_dir)

    def test_sentinel_existing_path_blocks_overwrite(self):
        info = cache_test_utils.create_validation_sentinel(self._cache_dir, self._cache_run_id)
        try:
            # Re-creating with the same run id targets the same path -> refused.
            with self.assertRaises(RuntimeError):
                cache_test_utils.create_validation_sentinel(self._cache_dir, self._cache_run_id)
        finally:
            cache_test_utils.remove_validation_sentinel(info, self._cache_dir)

    def test_sentinel_token_mismatch_blocks_deletion(self):
        info = cache_test_utils.create_validation_sentinel(self._cache_dir, self._cache_run_id)
        try:
            tampered = dict(info)
            tampered["token"] = "WRONG-TOKEN"
            with self.assertRaises(RuntimeError):
                cache_test_utils.remove_validation_sentinel(tampered, self._cache_dir)
            self.assertTrue(Path(info["path"]).exists())
        finally:
            cache_test_utils.remove_validation_sentinel(info, self._cache_dir)

    def test_sentinel_never_uses_production_cache_filename(self):
        info = cache_test_utils.create_validation_sentinel(self._cache_dir, self._cache_run_id)
        try:
            self.assertNotIn("ffmpeg_capabilities", info["name"])
        finally:
            cache_test_utils.remove_validation_sentinel(info, self._cache_dir)

    def test_sentinel_only_exact_owned_removed(self):
        info = cache_test_utils.create_validation_sentinel(self._cache_dir, self._cache_run_id)
        decoy = Path(self._cache_dir, ".ffmwiz_validation_sentinel_OTHER")
        decoy.write_text("other", encoding="utf-8")
        self.assertTrue(cache_test_utils.remove_validation_sentinel(info, self._cache_dir))
        self.assertFalse(Path(info["path"]).exists())
        self.assertTrue(decoy.exists())  # unrelated sentinel-like file survives

    def test_mock_protected_cache_dir_not_recursively_deleted(self):
        """A mock runtime .cache dir (no ownership marker) cannot be rmtree'd."""
        with tempfile.TemporaryDirectory(prefix="ffmwiz_mockproj_") as tmp:
            mock_cache = Path(tmp, "mock_project", ".cache")
            mock_cache.mkdir(parents=True)
            keep = mock_cache / "ffmpeg_capabilities.json"
            keep.write_text("{}", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                cache_test_utils.safe_remove_owned_temp_dir(
                    mock_cache, "any-id", tempfile.gettempdir())
            self.assertTrue(keep.exists())  # nothing deleted

    # ===================================================================
    # Professional logging (UTC, structured, redaction, shutdown)
    # ===================================================================

    def test_redact_secrets_masks_credentials_only(self):
        r = FFmWiz.redact_secrets
        self.assertNotIn("SECRET123", r("api_key=SECRET123"))
        self.assertIn("[REDACTED]", r("api_key=SECRET123"))
        self.assertNotIn("hunter2", r("password: hunter2"))
        self.assertNotIn("tok_abc", r("access_token=tok_abc"))
        self.assertNotIn("jwtpart", r("Authorization: Bearer jwtpart.more"))
        self.assertEqual(r("://u:p4ss@host/x"), "://u:[REDACTED]@host/x")
        # Ordinary FFmpeg arguments must not be touched.
        self.assertEqual(r("crf=23 preset=medium scale=1280:720"),
                         "crf=23 preset=medium scale=1280:720")
        self.assertEqual(r(""), "")
        self.assertEqual(r(None), "")

    def test_logging_file_is_utc_structured_and_redacted(self):
        prev = (FFmWiz.appio._LOGGER, FFmWiz.appio._LOG_PATH, FFmWiz.appio._SHUTDOWN_LOGGED,
                FFmWiz.appio._EXECUTION_ID, FFmWiz.appio._SESSION_START_MONOTONIC)
        FFmWiz.appio._LOGGER = None
        FFmWiz.appio._LOG_PATH = None
        FFmWiz.appio._SHUTDOWN_LOGGED = False
        try:
            with tempfile.TemporaryDirectory(prefix="ffmwiz_logtest_") as tmp, \
                    mock.patch.object(FFmWiz.appio, "_logs_dir", return_value=Path(tmp)), \
                    mock.patch.object(FFmWiz.appio, "_logging_enabled_from_config", return_value=True), \
                    mock.patch.object(FFmWiz.appio, "_log_retention_days_from_config", return_value=0):
                path = FFmWiz.setup_logging()
                self.assertIsNotNone(path)
                self.assertTrue(path.name.startswith("ffmwiz_"))
                self.assertTrue(path.name.endswith("_UTC.log"))
                FFmWiz.log_info("hello world", component="UnitTest")
                FFmWiz.log_warn("careful now", component="UnitTest")
                FFmWiz.log_info("login api_key=TOPSECRETXYZ done", component="Net")
                FFmWiz.shutdown_logging(exit_code=0)
                text = path.read_text(encoding="utf-8")
            import re as _re
            self.assertRegex(
                text,
                r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\] \[INFO\] \[UnitTest\] hello world",
            )
            self.assertIn("[WARNING] [UnitTest] careful now", text)
            self.assertNotIn("TOPSECRETXYZ", text)
            self.assertIn("api_key=[REDACTED]", text)
            self.assertNotRegex(text, r"\d{2}:\d{2}:\d{2}[.,]\d")  # no milliseconds
            self.assertIn("[Shutdown]", text)
            self.assertIn("total duration=", text)
        finally:
            # Restore module logging state so other tests are unaffected.
            if FFmWiz.appio._LOGGER is not None:
                for h in list(FFmWiz.appio._LOGGER.handlers):
                    try:
                        h.close()
                    except Exception:
                        pass
                    FFmWiz.appio._LOGGER.removeHandler(h)
            (FFmWiz.appio._LOGGER, FFmWiz.appio._LOG_PATH, FFmWiz.appio._SHUTDOWN_LOGGED,
             FFmWiz.appio._EXECUTION_ID, FFmWiz.appio._SESSION_START_MONOTONIC) = prev


if __name__ == "__main__":
    unittest.main()
