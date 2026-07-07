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

    def test_mux_video_stream_summary_includes_chapter_presence(self):
        stream = FFmWiz.mux.MuxStreamInfo(
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

    def test_stream_cleanup_metadata_edits_and_default_dispositions_are_emitted(self):
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
            overwrite=True,
            metadata_edits=[
                FFmWiz.mux.MuxStreamMetadataEdit(codec_type="audio", match_indexes=[1], language="jpn", title="Main"),
                FFmWiz.mux.MuxStreamMetadataEdit(codec_type="subtitle", match_languages=["unknown"], language="eng"),
            ],
        )
        input_file = Path("input.mkv")
        output = Path("output.mkv")
        media = FFmWiz.mux.MuxMediaFile(
            path=input_file,
            format={},
            streams=[
                FFmWiz.mux.MuxStreamInfo(index=0, codec_type="video", codec_name="h264"),
                FFmWiz.mux.MuxStreamInfo(index=1, codec_type="audio", codec_name="aac", language="unknown", disposition_default=0),
                FFmWiz.mux.MuxStreamInfo(index=2, codec_type="audio", codec_name="aac", language="eng", disposition_default=1),
                FFmWiz.mux.MuxStreamInfo(index=3, codec_type="subtitle", codec_name="ass", language="unknown", disposition_default=1),
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

    def test_encoder_supports_multipass_detected_from_probe(self):
        # Any encoder whose `ffmpeg -h encoder=...` output advertises -multipass
        # is supported — detected dynamically, not from a hardcoded list.
        import subprocess as _sp
        FFmWiz._MULTIPASS_ENCODER_CACHE.clear()

        def fake_run(args, **kw):
            class R:
                pass
            r = R()
            has = "fake_mp_enc" in " ".join(str(a) for a in args)
            r.stdout = (b"Encoder fake_mp_enc\n  -multipass  <int>  E..V..  set multipass\n"
                        if has else b"Encoder other\n  -preset  <int>  E..V..\n")
            return r

        with mock.patch.object(_sp, "run", side_effect=fake_run):
            self.assertTrue(FFmWiz.encoder_supports_multipass("fake_mp_enc"))
            self.assertFalse(FFmWiz.encoder_supports_multipass("fake_no_mp"))
        FFmWiz._MULTIPASS_ENCODER_CACHE.clear()

    def test_encoder_supports_multipass_fallback_without_ffmpeg(self):
        # When ffmpeg cannot be probed, fall back to the known NVENC set.
        import subprocess as _sp
        FFmWiz._MULTIPASS_ENCODER_CACHE.clear()
        with mock.patch.object(_sp, "run", side_effect=FileNotFoundError("no ffmpeg")):
            self.assertTrue(FFmWiz.encoder_supports_multipass("h264_nvenc"))
            self.assertTrue(FFmWiz.encoder_supports_multipass("av1_nvenc"))
            self.assertFalse(FFmWiz.encoder_supports_multipass("libx264"))
        FFmWiz._MULTIPASS_ENCODER_CACHE.clear()

    def test_encoder_supports_two_pass_set(self):
        for enc in ("libx264", "libx265", "libvpx-vp9", "libaom-av1", "libsvtav1", "mpeg4"):
            self.assertTrue(FFmWiz.encoder_supports_two_pass(enc), enc)
        for enc in ("h264_nvenc", "hevc_nvenc", "av1_nvenc", "av1_qsv", "copy", "", None):
            self.assertFalse(FFmWiz.encoder_supports_two_pass(enc), enc)

    def test_cpu_two_pass_applicable_for_av1_and_vp9(self):
        with tempfile.TemporaryDirectory() as tmp:
            for codec in ("av1", "vp9"):
                answers = self.base_answers(tmp)
                answers["video_codec"] = codec
                answers["use_gpu"] = False
                self.assertTrue(FFmWiz.cpu_two_pass_applicable(answers), codec)

    def test_cpu_two_pass_command_detects_and_preserves_libsvtav1(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["video_codec"] = "av1"
            answers["use_gpu"] = False
            answers["cpu_two_pass"] = True
            cmd = self.command_for(answers)
            self.assertTrue(FFmWiz.cpu_two_pass_enabled_for_command(answers, cmd))
            first, second, _passlog = FFmWiz.build_cpu_two_pass_commands(cmd, answers)
        first_text = " ".join(first)
        # The SVT-AV1 params must survive into the analysis (pass 1) too.
        self.assertIn("-svtav1-params tune=0", first_text)
        self.assertIn("-pass 1", first_text)
        self.assertIn("-pass 2", " ".join(second))

    def test_apply_config_extra_recipe_options_sets_all_keys(self):
        answers = self._extra_recipe_answers()
        cfg = FFmWiz.parse_env_config("\n".join([
            "nvenc_multipass=fullres",
            "cpu_two_pass=y",
            "color_range=tv",
            "audio_sample_rate=48000",
            "loudnorm=on",
            "loudnorm_target_i=-14",
            "video_speed=2",
            "reverse_video=y",
            "audio_speed=match_video",
        ]))
        FFmWiz.apply_config_extra_recipe_options(answers, cfg)
        self.assertEqual(answers["nvenc_multipass"], "fullres")
        self.assertTrue(answers["cpu_two_pass"])
        self.assertEqual(answers["color_range_choice"], "tv")
        self.assertEqual(answers["audio_sample_rate"], 48000)
        self.assertFalse(answers.get("audio_sample_rate_keep"))
        self.assertTrue(answers["loudnorm_enabled"])
        self.assertEqual(answers["loudnorm_mode"], "single")
        self.assertEqual(answers["loudnorm_target_i"], -14.0)
        self.assertTrue(answers["video_speed_enabled"])
        self.assertEqual(answers["video_speed_factor"], 2.0)
        self.assertTrue(answers["reverse_video"])
        self.assertTrue(answers["audio_speed_from_video"])

    def test_apply_config_extra_recipe_options_defaults_change_nothing(self):
        # Empty / default config must not enable any optional transform.
        answers = self._extra_recipe_answers()
        cfg = FFmWiz.parse_env_config(FFmWiz.CONFIG_TEMPLATE)
        FFmWiz.apply_config_extra_recipe_options(answers, cfg)
        self.assertNotIn("loudnorm_enabled", answers)
        self.assertNotIn("video_speed_enabled", answers)
        self.assertNotIn("audio_speed_enabled", answers)
        self.assertNotIn("audio_speed_from_video", answers)
        self.assertNotIn("color_range_choice", answers)
        # nvenc_multipass=disabled and cpu_two_pass=n are explicit no-ops.
        self.assertEqual(answers.get("nvenc_multipass"), "disabled")
        self.assertFalse(answers.get("cpu_two_pass"))

    def test_apply_config_extra_recipe_options_rejects_bad_values(self):
        answers = self._extra_recipe_answers()
        for bad in ("nvenc_multipass=triple", "color_range=rec709", "loudnorm=maybe"):
            cfg = FFmWiz.parse_env_config(bad)
            with self.assertRaises(ValueError):
                FFmWiz.apply_config_extra_recipe_options(self._extra_recipe_answers(), cfg)

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

    def test_loudnorm_two_pass_always_linear_true(self):
        """Two-pass (measured) always emits linear=true per the loudnorm spec;
        FFmpeg internally falls back to dynamic if the linear gain would exceed
        the true-peak ceiling, so FFmWiz no longer hard-codes linear=false."""
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
        # gain = +5.64 dB, predicted_TP = +2.68 > -1.5: still linear=true here.
        filt = FFmWiz.build_loudnorm_filter(answers)
        self.assertIn("linear=true", filt)
        self.assertNotIn("linear=false", filt)
        self.assertIn("measured_I=-19.64", filt)
        self.assertIn("offset=-0.51", filt)

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

    def test_two_pass_parity_8bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            self._assert_two_pass_parity(answers)

    # ===================================================================
    # HardSub pixel-format warnings
    # ===================================================================

    def test_hardsub_8bit_no_warning(self):
        info = FFmWiz.compare_pixel_formats("yuv420p", "yuv420p")
        self.assertEqual(info["warnings"], [])

    def test_hardsub_444_to_420_warns(self):
        info = FFmWiz.compare_pixel_formats("yuv444p", "yuv420p")
        self.assertTrue(
            any("Chroma subsampling will be reduced from 4:4:4 to 4:2:0" in w for w in info["warnings"])
        )

    # ===================================================================
    # Encoder color-range signaling capability ("do not force" honesty)
    # ===================================================================

    def test_encoder_capability_model(self):
        """True unspecified is only achievable with an H.264 encoder writing to
        an MP4-like container; HEVC or MKV always yields a default 'tv'."""
        def a(codec, gpu, ext):
            return {"video_codec": codec, "use_gpu": gpu, "output_ext": ext,
                    "video_streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}
        # H.264 + MP4-like -> genuinely unspecified.
        self.assertTrue(FFmWiz.encoder_preserves_unspecified_range(a("H264", False, "mp4")))
        self.assertTrue(FFmWiz.encoder_preserves_unspecified_range(a("H264", True, "mov")))
        self.assertEqual(FFmWiz.expected_unforced_range(a("H264", False, "mp4")), "")
        # H.264 + MKV -> Matroska writes a default range.
        self.assertFalse(FFmWiz.encoder_preserves_unspecified_range(a("H264", False, "mkv")))
        self.assertEqual(FFmWiz.expected_unforced_range(a("H264", False, "mkv")), "tv")
        # HEVC -> encoder default regardless of container.
        self.assertFalse(FFmWiz.encoder_preserves_unspecified_range(a("H265", False, "mp4")))
        self.assertFalse(FFmWiz.encoder_preserves_unspecified_range(a("H265", True, "mkv")))
        self.assertEqual(FFmWiz.expected_unforced_range(a("H265", False, "mp4")), "tv")
        self.assertEqual(FFmWiz.expected_unforced_range(a("H265", True, "mkv")), "tv")

    def test_summary_option2_conservative_without_verified_capability(self):
        """Without a verified capability, the summary reports conservatively."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)  # H265, CPU
            answers["color_range_choice"] = "unspecified"
            answers["_no_capability_probe"] = True
            out = self._summary_text(answers)
            self.assertIn("requested color-range policy: do not force", out)
            self.assertIn("FFmWiz explicit color-range option: omitted", out)
            self.assertIn("capability source: unavailable", out)
            self.assertIn("expected encoder-reported final range: unknown until verified", out)

    def test_summary_option2_uses_verified_cache(self):
        """A verified capability result is surfaced in the summary."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            answers["color_range_choice"] = "unspecified"
            fake = {"capability_source": "verified cache", "env_short": "abc123def456",
                    "status": "verified", "expected_final_range": "tv",
                    "verified_at_utc": "2026-01-01 00:00:00 UTC"}
            with mock.patch.object(FFmWiz.services, "resolve_capability", return_value=fake):
                out = self._summary_text(answers)
            self.assertIn("capability source: verified cache", out)
            self.assertIn("capability environment fingerprint: abc123def456", out)
            self.assertIn("expected encoder-reported final range: tv", out)
            self.assertIn("verified probe timestamp: 2026-01-01 00:00:00 UTC", out)

    def test_environment_driver_invalidates_nvenc(self):
        calls = {}
        def ident(ffmpeg, ffprobe, *, include_gpu):
            calls["gpu"] = include_gpu
            d = self._fake_identity()
            if include_gpu:
                d["gpu"] = "RTX"
                d["nvidia_driver"] = "550" if "a" in ffmpeg else "560"
            return d
        with mock.patch.object(FFmWiz.services, "capability_environment_identity", side_effect=ident):
            _, k1 = FFmWiz.services.capability_environment_key("a", "ffprobe", "hevc_nvenc")
            _, k2 = FFmWiz.services.capability_environment_key("b", "ffprobe", "hevc_nvenc")
        self.assertTrue(calls["gpu"])
        self.assertNotEqual(k1, k2)

    def test_probe_once_for_split_two_pass_folder(self):
        """Multiple resolve calls for the same combo (parts/passes/files) probe once."""
        verified = {"status": "verified", "expected_final_range": "tv", "probe_method": "m",
                    "encoder": "libx265", "container_family": "mkv", "sample_command_hash": "h",
                    "ffprobe_result": "tv", "verified_at_utc": "t", "error": None}
        with mock.patch.object(FFmWiz.services, "capability_environment_identity",
                               return_value=self._fake_identity()), \
                mock.patch.object(FFmWiz.services, "probe_color_range_capability",
                                  return_value=verified) as probe:
            a = self._cap_answers()
            for _ in range(5):  # 5 split parts / passes / files
                FFmWiz.services.resolve_capability(a)
        self.assertEqual(probe.call_count, 1)

    def test_nvenc_unavailable_stores_unsupported(self):
        unsupported = {"status": "unsupported", "expected_final_range": None, "probe_method": "m",
                       "encoder": "hevc_nvenc", "container_family": "mp4", "sample_command_hash": "h",
                       "ffprobe_result": None, "verified_at_utc": "t", "error": "no nvenc"}
        with mock.patch.object(FFmWiz.services, "capability_environment_identity",
                               return_value=self._fake_identity(gpu="x", nvidia_driver="1")), \
                mock.patch.object(FFmWiz.services, "probe_color_range_capability", return_value=unsupported):
            r = FFmWiz.services.resolve_capability(self._cap_answers(codec="H265", gpu=True, ext="mp4"))
        self.assertEqual(r["status"], "unsupported")
        cache = FFmWiz.load_capability_cache()
        entry = next(iter(cache["environments"].values()))["capabilities"]["color_range_do_not_force"]["hevc_nvenc|mp4"]
        self.assertEqual(entry["status"], "unsupported")

    def test_clear_removes_only_capability_cache(self):
        FFmWiz.save_capability_cache({"schema_version": 1, "environments": {"E": {}}})
        sibling = Path(self._cache_dir, "user_setting.json")
        sibling.write_text("{}", encoding="utf-8")
        with mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()):
            FFmWiz._capability_cache_clear()
        self.assertFalse(Path(self._cache_dir, FFmWiz.CAPABILITY_CACHE_FILENAME).exists())
        self.assertTrue(sibling.exists())

    def test_workflow_cpu_two_pass_identical_provenance(self):
        """CPU two-pass: identical resolved geometry; raw metadata immutable."""
        for raw_dar, expect_fallback in (("9:16", False), (None, True)):
            with tempfile.TemporaryDirectory() as tmp:
                answers = self.base_answers(tmp)
                answers["use_gpu"] = False
                answers["crop_enabled"] = False
                answers["color_range_choice"] = "tv"
                stream = {"codec_type": "video", "codec_name": "h264",
                          "width": 2160, "height": 3840}
                if raw_dar:
                    stream["display_aspect_ratio"] = raw_dar
                answers["video_streams"] = [stream]
                cmd = self.command_for(answers)
                first, second, _ = FFmWiz.build_cpu_two_pass_commands(cmd, answers)

                def vf(c):
                    return c[c.index("-filter:v") + 1] if "-filter:v" in c else ""

                self.assertEqual(vf(first), vf(second))
                info = FFmWiz.sar_dar_info(answers)
                self.assertEqual(info["fallback_used"], expect_fallback)
                self._assert_detected_label_only_when_raw_valid(info)
                self._assert_raw_immutable(stream, None, raw_dar)

    def test_workflow_hardsub_provenance(self):
        """HardSub uses the shared resolver; non-square SAR preserved (no setsar=1)."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.hardsub_answers(tmp, output_ext="mkv")
            answers["color_range_choice"] = "tv"
            answers["video_streams"] = [{"codec_type": "video", "codec_name": "h264",
                                         "width": 720, "height": 576, "sample_aspect_ratio": "16:15"}]
            info = FFmWiz.sar_dar_info(answers)
            self.assertEqual(info["sar_source"], "detected by ffprobe")
            self.assertEqual(info["dar_text"], "4:3")
            self.assertEqual(info["dar_source"], "calculated from coded resolution and SAR")
            self.assertFalse(info["fallback_used"])
            cmd = FFmWiz.build_hardsub_command(answers)
            cmd_text = " ".join(cmd)
            # Non-square SAR is preserved explicitly, not reset to square 1:1.
            self.assertIn("setsar=16/15", cmd_text)
            self.assertNotIn("setsar=1,", cmd_text)
            self.assertNotIn("setsar=1 ", cmd_text)
            self._assert_raw_immutable(answers["video_streams"][0], "16:15", None)
        # Vertical detected-DAR fixture through HardSub.
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.hardsub_answers(tmp, output_ext="mkv")
            answers["color_range_choice"] = "tv"
            answers["video_streams"] = [{"codec_type": "video", "codec_name": "h264",
                                         "width": 2160, "height": 3840, "display_aspect_ratio": "9:16"}]
            info = FFmWiz.sar_dar_info(answers)
            self.assertEqual(info["sar_source"], "calculated from coded resolution and detected DAR")
            self.assertEqual(info["dar_source"], "detected by ffprobe")

    def test_capability_clear_removes_only_owned_file(self):
        FFmWiz.save_capability_cache({"schema_version": 1, "environments": {"E": {}}})
        unrelated = Path(self._cache_dir, "unrelated_user_file.json")
        unrelated.write_text("{}", encoding="utf-8")
        marker = Path(self._cache_dir, cache_test_utils.TEST_CACHE_OWNER_MARKER)
        with mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()):
            FFmWiz._capability_cache_clear()
        self.assertFalse(Path(self._cache_dir, FFmWiz.CAPABILITY_CACHE_FILENAME).exists())
        self.assertTrue(unrelated.exists())          # unrelated file survives
        self.assertTrue(marker.exists())             # ownership marker survives
        self.assertTrue(Path(self._cache_dir).is_dir())  # .cache dir survives


if __name__ == "__main__":
    unittest.main()
