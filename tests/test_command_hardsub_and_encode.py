"""Split from the command-generation suite (see command_gen_base.py)."""
import contextlib
import io
import tempfile
from pathlib import Path
from unittest import mock
import FFmWiz
import cache_test_utils
from command_gen_base import CommandGenBase


class CommandHardsubAndEncodeTests(CommandGenBase):
    def test_main_menu_routes_metadata_editor_mode(self):
        with mock.patch.object(FFmWiz, "ask_main_menu", return_value=13), \
                mock.patch.object(FFmWiz.metadata, "run_metadata_editor_mode", return_value=None) as run_metadata:
            result = FFmWiz.run_one_job({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}, Path("config.json"))
        self.assertIsNone(result)
        run_metadata.assert_called_once()

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

    def test_progress_line_omits_nvenc_q_sentinel(self):
        state = {
            "out_time_us": "1000000",
            "speed": "12x",
            "stream_0_0_q": "-1.0",
            "progress": "continue",
        }
        line = FFmWiz._strip_ansi(FFmWiz._render_progress_line(state, 60.0, FFmWiz.time.perf_counter() - 1))
        self.assertNotIn("q -1.0", line)

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
                FFmWiz.trackmanager.print_source_info(answers)
            self.assertIn("chapters: yes", out.getvalue())

            answers["probe"] = {"chapters": []}
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                FFmWiz.trackmanager.print_source_info(answers)
            self.assertIn("chapters: no", out.getvalue())

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
                "color_range_choice": "tv",
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
        # This used to assert a hard `-ac 2`, which pinned the USER-5-2 defect:
        # every hard-sub downmixed to stereo regardless of the source. The
        # fixture's stream declares no channel count, so the correct behaviour
        # is to emit NO -ac and let the source layout through.
        self.assertNotIn("-ac", cmd,
                         "an unknown source layout must not be forced to stereo")

    def test_hardsub_keeps_a_51_source_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.hardsub_answers(tmp, "input.mkv", "mp4")
            answers["hardsub_audio_container_policy"] = "aac"
            answers["audio_streams"] = [
                {"codec_type": "audio", "codec_name": "aac", "channels": 6}]
            cmd = FFmWiz.build_hardsub_command(answers)
        self.assertIn("-ac", cmd)
        self.assertEqual(cmd[cmd.index("-ac") + 1], "6")

    def test_hardsub_honours_an_explicit_downmix(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.hardsub_answers(tmp, "input.mkv", "mp4")
            answers["hardsub_audio_container_policy"] = "aac"
            answers["audio_streams"] = [
                {"codec_type": "audio", "codec_name": "aac", "channels": 6}]
            answers["audio_channels"] = 2
            cmd = FFmWiz.build_hardsub_command(answers)
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
        with mock.patch.object(FFmWiz.metadata, "metadata_prompt_input", return_value=answers), \
                mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=["6", "0"]), \
                mock.patch.object(FFmWiz.appio, "error") as error_call, \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            FFmWiz.metadata.run_metadata_editor_mode({})
        self.assertIn("Stream Metadata Editor [1]", stdout.getvalue())
        self.assertNotIn("Metadata Report / Inspect", stdout.getvalue())
        error_call.assert_any_call("Enter a menu number from 1 to 5.")

    def test_metadata_report_output_path_creates_reports_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp) / "MediaReports"
            with mock.patch.object(FFmWiz.services, "default_media_reports_dir", return_value=reports_dir):
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
        with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="0") as ask_raw, \
                contextlib.redirect_stdout(io.StringIO()):
            stream = FFmWiz.select_stream(probe, answers, {"video"})
        self.assertEqual(stream["index"], 0)
        prompt = str(ask_raw.call_args.args[0])
        self.assertIn("back=b", FFmWiz._strip_ansi(prompt))

    def test_metadata_value_prompt_accepts_zero_value(self):
        with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="0"):
            self.assertEqual(FFmWiz.metadata_value_prompt({}, "Enter metadata value"), "0")

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
            with mock.patch.object(FFmWiz.appio, "ask_raw", return_value=""):
                FFmWiz.step_nvenc_multipass(answers)
        self.assertEqual(answers["nvenc_multipass"], "qres")

    def test_nvenc_multipass_menu_digits_and_back(self):
        """NVENC multipass uses 1=Disabled, 2=qres, 3=fullres, and 0=back."""
        for digit, expected in (("1", "disabled"), ("2", "qres"), ("3", "fullres")):
            with tempfile.TemporaryDirectory() as tmp:
                answers = self.base_answers(tmp)
                with mock.patch.object(FFmWiz.appio, "ask_raw", return_value=digit):
                    FFmWiz.step_nvenc_multipass(answers)
                self.assertEqual(answers["nvenc_multipass"], expected)
        # Prompt wording and back token.
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            captured = {}

            def fake_ask(prompt):
                captured["prompt"] = prompt
                return "1"

            with mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=fake_ask):
                FFmWiz.step_nvenc_multipass(answers)
            prompt = captured["prompt"]
            self.assertIn("1=Disabled", prompt)
            self.assertIn("2=qres", prompt)
            self.assertIn("3=fullres", prompt)
            self.assertIn("back=0", prompt)
            self.assertNotIn("back=b", prompt)
            self.assertNotIn("0=Disabled", prompt)
        # Entering 0 navigates back.
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="0"):
                with self.assertRaises(FFmWiz.Back):
                    FFmWiz.step_nvenc_multipass(answers)

    def test_nvenc_multipass_prompt_skips_cpu_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            self.assertFalse(FFmWiz.nvenc_multipass_prompt_applicable(answers))
        self.assertEqual(answers.get("nvenc_multipass_skip_reason"), "CPU encoder selected")

    def test_is_nvenc_multipass_encoder_includes_av1(self):
        # av1_nvenc exposes -multipass too, so the prompt must apply to it just
        # like h264_nvenc / hevc_nvenc (it was previously omitted).
        for enc in ("h264_nvenc", "hevc_nvenc", "av1_nvenc", "AV1_NVENC"):
            self.assertTrue(FFmWiz.is_nvenc_multipass_encoder(enc), enc)
        for enc in ("libx264", "libaom-av1", "av1_qsv", "", "copy", None):
            self.assertFalse(FFmWiz.is_nvenc_multipass_encoder(enc), enc)


if __name__ == "__main__":
    unittest.main()
