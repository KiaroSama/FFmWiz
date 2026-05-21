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

    def command_for(self, answers: dict) -> list[str]:
        return FFmWiz.build_ffmpeg_command(answers)

    def command_text(self, answers: dict) -> str:
        return " ".join(self.command_for(answers))

    def assert_not_contains_any(self, text: str, needles: list[str]) -> None:
        for needle in needles:
            self.assertNotIn(needle, text)

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
        self.assertIn("-crop 172x189x429x1095", text)
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
        self.assertIn("-crop 172x189x429x1095", text)
        self.assertIn("-t 6074.221000", text)
        self.assertIn("scale_cuda=w=852:h=480", text)
        self.assertIn("hevc_nvenc", text)
        self.assert_not_contains_any(text, ["filter_complex", "trim", "atrim", "concat=n=1", "anull", "hwdownload", "hwupload_cuda"])

    def test_gpu_crop_unknown_decoder_falls_back_to_cpu_crop_before_nvenc(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["video_streams"][0]["codec_name"] = "prores"
            text = self.command_text(answers)
        self.assertIn("crop=iw-429-1095:ih-172-189:429:172", text)
        self.assertIn("scale=852:480", text)
        self.assertIn("fps=4", text)
        self.assertIn("hevc_nvenc", text)
        self.assert_not_contains_any(text, ["-hwaccel cuda", "-crop 172x189x429x1095", "scale_cuda", "hwdownload", "hwupload_cuda"])

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
        self.assertEqual(answers["crop_box_dimensions"], (1916, 1079))
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

    def test_box_mode_preserves_ratio(self):
        answers = {"video_streams": [{"width": 950, "height": 1840}], "crop_enabled": False}
        dims, warning = FFmWiz.calculate_scale_dimensions(answers, FFmWiz.parse_resolution("1080x1920"))
        self.assertEqual(dims, (992, 1920))
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
            "video_streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}],
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

    def test_copy_cut_back_from_method_returns_to_output_step(self):
        calls: list[str] = []
        output_calls = {"count": 0}
        method_calls = {"count": 0}
        originals = {
            "step_input_path": FFmWiz.step_input_path,
            "step_output_location": FFmWiz.step_output_location,
            "ask_cut_method": FFmWiz.ask_cut_method,
            "open_cut_gui": FFmWiz.open_cut_gui,
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
            FFmWiz.open_cut_gui = lambda _answers, fps, duration: calls.append("gui") or [(0.0, 10.0)]
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

    def test_step_cuts_gui_cancel_reasks_cut_question(self):
        prompts = iter(["g", "n"])
        notes: list[str] = []
        gui_calls = {"count": 0}
        originals = {
            "ask_raw": FFmWiz.ask_raw,
            "open_cut_gui": FFmWiz.open_cut_gui,
            "note": FFmWiz.note,
            "get_video_fps": FFmWiz.get_video_fps,
            "stream_duration_seconds": FFmWiz.stream_duration_seconds,
        }

        def fake_open_cut_gui(_answers, fps, duration):
            gui_calls["count"] += 1
            return None

        try:
            FFmWiz.ask_raw = lambda _prompt: next(prompts)
            FFmWiz.open_cut_gui = fake_open_cut_gui
            FFmWiz.note = lambda message: notes.append(message)
            FFmWiz.get_video_fps = lambda _answers: 25.0
            FFmWiz.stream_duration_seconds = lambda _stream, _fmt=None: 100.0
            answers = {"format": {"duration": "100"}}
            FFmWiz.step_cuts(answers)
        finally:
            for name, original in originals.items():
                setattr(FFmWiz, name, original)

        self.assertEqual(gui_calls["count"], 1)
        self.assertNotIn("cut_keep_ranges", answers)
        self.assertIn("Returning to the cut question", notes[-1])

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
        self.assertNotIn("scale_cuda", text)
        self.assertNotIn("-hwaccel cuda", text)

    def test_main_encode_video_speed_from_gui_syncs_all_audio_tracks(self):
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
        self.assertIn("[0:a:0]areverse,asetpts=PTS-STARTPTS,atempo=1.25[aout0]", text)
        self.assertIn("[0:a:1]areverse,asetpts=PTS-STARTPTS,atempo=1.25[aout1]", text)
        self.assertIn("-map [aout0]", text)
        self.assertIn("-map [aout1]", text)

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
        self.assertIn("atrim=start=0.000000:end=2.000000", text)
        self.assertIn("concat=n=2:v=0:a=1[acut0]", text)
        self.assertIn("[acut0]areverse,asetpts=PTS-STARTPTS,atempo=0.5[aout0]", text)
        self.assertIn("-map [aout0]", text)

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
        self.assertIn("atrim=start=0.000000:end=2.500000", text)
        self.assertIn("concat=n=2:v=0:a=1[a]", text)
        self.assertIn("-map [a]", text)

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


if __name__ == "__main__":
    unittest.main()
