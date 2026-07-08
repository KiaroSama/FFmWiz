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


if __name__ == "__main__":
    unittest.main()
