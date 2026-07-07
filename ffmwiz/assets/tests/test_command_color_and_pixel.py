"""Split from the command-generation suite (see command_gen_base.py)."""
import contextlib
import io
import tempfile
from pathlib import Path
from unittest import mock
import FFmWiz
from command_gen_base import CommandGenBase


class CommandColorAndPixelTests(CommandGenBase):
    def test_separator_ranges_normalize_points(self):
        ranges = FFmWiz.separator_ranges([10, 5, 5, -1, 20, 100], 20.0)
        self.assertEqual(ranges, [(0.0, 5.0), (5.0, 10.0), (10.0, 20.0)])

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

    def test_video_full_range_flag_accepts_zero_limited_value(self):
        answers = {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "metadata_input_path": Path("input.mkv")}
        probe = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}]}
        with mock.patch.object(FFmWiz.metadata, "metadata_menu_selection", side_effect=["3", "0"]), \
                mock.patch.object(FFmWiz.metadata, "metadata_refresh_probe", return_value=probe), \
                mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=True), \
                mock.patch.object(FFmWiz.appio, "ask_raw", return_value="0"), \
                mock.patch.object(FFmWiz.runner, "confirm_and_run_ffmpeg", return_value=False) as confirm_run, \
                contextlib.redirect_stdout(io.StringIO()):
            FFmWiz.run_video_bitstream_metadata_tools(answers)
        command_text = " ".join(str(part) for part in confirm_run.call_args.args[1])
        self.assertIn("h264_metadata=video_full_range_flag=0", command_text)

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

    def test_gpu_request_for_12bit_source_reduces_to_10bit_nvenc_main10(self):
        # New policy: 12-bit+ sources are delivered as 10-bit Main10. For GPU,
        # NVENC stays (no forced CPU fallback) and uses p010le + main10.
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["video_streams"][0]["pix_fmt"] = "yuv420p12le"
            answers["video_streams"][0]["bits_per_raw_sample"] = "12"
            text = self.command_text(answers)
        self.assertIn("format=p010le", text)
        self.assertIn("hevc_nvenc", text)
        self.assertIn("-profile:v main10", text)
        self.assertNotIn("format=yuv420p12le", text)
        self.assertNotIn("main12", text)
        self.assertEqual(FFmWiz.output_video_bit_depth(answers), 10)

    def test_cpu_12bit_source_reduces_to_10bit_main10(self):
        # 12-bit source + CPU/libx265 falls back to 10-bit Main10 (yuv420p10le).
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["video_streams"][0]["pix_fmt"] = "yuv420p12le"
            answers["video_streams"][0]["bits_per_raw_sample"] = "12"
            text = self.command_text(answers)
        self.assertIn("format=yuv420p10le", text)
        self.assertIn("-c:v libx265", text)
        self.assertIn("-profile:v main10", text)
        self.assertNotIn("yuv420p12le", text)
        self.assertNotIn("main12", text)

    def test_cpu_main_encode_reduces_14bit_source_to_10bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["video_streams"][0]["pix_fmt"] = "yuv420p14le"
            answers["video_streams"][0]["bits_per_raw_sample"] = "14"
            text = self.command_text(answers)
        self.assertIn("format=yuv420p10le", text)
        self.assertIn("-profile:v main10", text)
        self.assertNotIn("yuv420p14le", text)
        self.assertNotIn("rext", text)

    def test_cpu_main_encode_reduces_above_16bit_source_to_10bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["video_streams"][0]["pix_fmt"] = "yuv420p16le"
            answers["video_streams"][0]["bits_per_raw_sample"] = "24"
            text = self.command_text(answers)
        self.assertIn("format=yuv420p10le", text)
        self.assertIn("-profile:v main10", text)
        self.assertNotIn("yuv420p16le", text)
        self.assertEqual(FFmWiz.output_video_bit_depth(answers), 10)

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

    def test_crop_norm_7_crop_near_boundary_rejected(self):
        """Test 7: an impossible crop rectangle is rejected before FFmpeg runs."""
        answers = self._crop_answers(1280, 720, "yuv420p", 2000, 2000, 0, 0)
        with self.assertRaises(ValueError):
            FFmWiz.normalized_crop_margins(answers)

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
            with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="") as ask, \
                    contextlib.redirect_stdout(io.StringIO()):
                FFmWiz.step_color_range(answers)
            self.assertTrue(ask.called)
            self.assertEqual(answers["color_range_choice"], "tv")
            self.assertIn("-color_range:v:0 tv", self.command_text(answers))

    def test_color_range_unknown_keep_unspecified(self):
        """Test 2: unknown range + Do not force -> no forced tv/pc."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="2"), \
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
            with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="3"), \
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

            with mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=fake_ask), \
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
        """Test 16/Case 4: both SAR and DAR unknown -> SAR 1:1 fallback, labeled."""
        answers = {"video_streams": [{"width": 1920, "height": 1080}]}
        info = FFmWiz.sar_dar_info(answers)
        self.assertEqual(info["sar_text"], "1:1")
        self.assertEqual(info["sar_source"], "fallback assumption")
        self.assertTrue(info["fallback_used"])
        self.assertEqual(info["pixel_shape"], "square (assumed)")
        self.assertEqual(info["dar_source"], "calculated from coded resolution and fallback SAR")
        self.assertEqual(info["dar_text"], "16:9")

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

    def test_batch_policy_unspecified_omits_color_range(self):
        """Test 5: option 2 -> unknown files omit -color_range."""
        settings = self._folder_settings(policy="unspecified")
        job = self._folder_job(color_range=None)
        FFmWiz.apply_folder_batch_color_range(job, settings, {"path": Path("a.mkv")})
        self.assertEqual(job["color_range_choice"], "unspecified")
        self.assertEqual(FFmWiz.color_range_output_args(job), [])

    def test_pixfmt_10bit_to_8bit_warns(self):
        """Test 14: yuv420p10le -> yuv420p warns about bit-depth reduction."""
        info = FFmWiz.compare_pixel_formats("yuv420p10le", "yuv420p")
        self.assertEqual(info["bit_depth_conversion"], "10-bit -> 8-bit")
        self.assertTrue(any("bit depth will be reduced from 10-bit to 8-bit" in w for w in info["warnings"]))

    def test_pixfmt_444_10bit_to_420_warns_both(self):
        """Test 16: yuv444p10le -> yuv420p warns about bit depth and chroma."""
        info = FFmWiz.compare_pixel_formats("yuv444p10le", "yuv420p")
        self.assertTrue(any("bit depth" in w for w in info["warnings"]))
        self.assertTrue(any("Chroma subsampling will be reduced from 4:4:4 to 4:2:0" in w for w in info["warnings"]))

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
    # 'Do not force a range' omits -color_range in rendered commands
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

    def test_two_pass_parity_10bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._tenbit_answers(tmp, use_gpu=False)
            self._assert_two_pass_parity(answers)
            first, second, _ = FFmWiz.build_cpu_two_pass_commands(
                self.command_for(answers), answers
            )
            self.assertIn("format=yuv420p10le", self._two_pass_field(first, "-filter:v"))
            self.assertEqual(self._two_pass_field(first, "-profile:v"), "main10")

    def test_hardsub_10bit_to_8bit_warns(self):
        info = FFmWiz.compare_pixel_formats("yuv420p10le", "yuv420p")
        self.assertTrue(any("bit depth will be reduced" in w for w in info["warnings"]))

    def test_do_not_force_label_in_color_range_menu(self):
        """The option-2 label reads 'Do not force a range in FFmWiz'; the prompt
        uses 0=back (not b) and prints no separate option list."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            captured = {}

            def fake_ask(prompt):
                captured["prompt"] = prompt
                return "2"

            buf = io.StringIO()
            with mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=fake_ask), \
                    contextlib.redirect_stdout(buf):
                FFmWiz.step_color_range(answers)
            out = buf.getvalue()
            prompt = captured["prompt"]
            self.assertIn("2=Do not force a range", prompt)
            self.assertIn("back=0", prompt)
            self.assertNotIn("back=b", prompt)
            # No separate numbered option list is printed to stdout anymore.
            self.assertNotIn(". Assume TV/Limited", out)
            self.assertNotIn(". Assume PC/Full", out)
            self.assertIn("Do not force a range in FFmWiz", out)  # confirmation note
            self.assertNotIn("Keep unspecified", out)
            self.assertEqual(answers["color_range_choice"], "unspecified")

    def test_color_range_menu_zero_goes_back(self):
        """Entering 0 at the color-range menu navigates back."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="0"), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(FFmWiz.Back):
                    FFmWiz.step_color_range(answers)

    def test_summary_stream_copy_reports_preserved_range(self):
        """Stream copy reports the source range as preserved, not menu semantics."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range="tv", resolution="n")
            answers["video_codec"] = "copy"
            answers["audio_codec"] = "copy"
            answers["crop_enabled"] = False
            answers["fps"] = None
            out = self._summary_text(answers)
            self.assertIn("stream copy (preserved from source)", out)
            self.assertNotIn("do not force", out)

    def test_two_pass_option2_no_color_range_both_passes(self):
        """CPU two-pass 'do not force': neither pass emits -color_range."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._encode_answers(tmp, color_range=None)
            answers["color_range_choice"] = "unspecified"
            cmd = self.command_for(answers)
            first, second, _ = FFmWiz.build_cpu_two_pass_commands(cmd, answers)
            self.assertNotIn("-color_range", " ".join(first))
            self.assertNotIn("-color_range", " ".join(second))

    # ===================================================================
    # Corrected SAR/DAR inference (provenance-aware)
    # ===================================================================

    def test_sar_derived_from_dar_vertical_video(self):
        """2160x3840, SAR unknown, DAR 9:16 -> SAR 1:1 derived, square pixels."""
        answers = {"video_streams": [{"width": 2160, "height": 3840,
                                       "display_aspect_ratio": "9:16"}]}
        info = FFmWiz.sar_dar_info(answers)
        self.assertEqual(info["sar_text"], "1:1")
        self.assertEqual(info["sar_source"], "calculated from coded resolution and detected DAR")
        self.assertEqual(info["dar_text"], "9:16")
        self.assertEqual(info["dar_source"], "detected by ffprobe")
        self.assertEqual(info["pixel_shape"], "square")
        self.assertAlmostEqual(info["effective_dar_decimal"], 0.5625, places=4)
        self.assertFalse(info["fallback_used"])

    def test_dar_derived_from_sar_when_dar_missing(self):
        """720x576, SAR 16:15, DAR unknown -> DAR 4:3 derived, no fallback."""
        answers = {"video_streams": [{"width": 720, "height": 576,
                                       "sample_aspect_ratio": "16:15"}]}
        info = FFmWiz.sar_dar_info(answers)
        self.assertEqual(info["sar_text"], "16:15")
        self.assertEqual(info["sar_source"], "detected by ffprobe")
        self.assertEqual(info["dar_text"], "4:3")
        self.assertEqual(info["dar_source"], "calculated from coded resolution and SAR")
        self.assertEqual(info["pixel_shape"], "non-square")
        self.assertFalse(info["fallback_used"])

    def test_sar_dar_matching_no_discrepancy(self):
        """Valid SAR and matching ffprobe DAR -> no discrepancy flagged."""
        answers = {"video_streams": [{"width": 1920, "height": 1080,
                                       "sample_aspect_ratio": "1:1",
                                       "display_aspect_ratio": "16:9"}]}
        info = FFmWiz.sar_dar_info(answers)
        self.assertFalse(info["discrepancy_detected"])
        self.assertIn("ffprobe DAR agrees", info["dar_source"])

    def test_sar_dar_conflict_dimensions_and_sar_win(self):
        """Valid SAR conflicting with ffprobe DAR -> discrepancy logged, SAR wins."""
        answers = {"video_streams": [{"width": 720, "height": 576,
                                       "sample_aspect_ratio": "16:15",
                                       "display_aspect_ratio": "16:9"}]}
        info = FFmWiz.sar_dar_info(answers)
        self.assertTrue(info["discrepancy_detected"])
        self.assertEqual(info["dar_source"], "calculated from coded resolution and SAR")
        self.assertAlmostEqual(info["resolved_dar"], 4 / 3, places=4)
        self.assertIsNotNone(info["warning"])

    def test_sar_dar_invalid_rationals_no_crash(self):
        """Malformed/zero/non-finite rationals are rejected without crashing."""
        for bad in ("0:1", "1:0", "-2:1", "abc", "inf", "nan", "0", ""):
            self.assertIsNone(FFmWiz.parse_rational(bad))
        # Invalid coded dims -> unresolved geometry, no crash.
        info = FFmWiz.sar_dar_info({"video_streams": [{"width": 0, "height": 0}]})
        self.assertEqual(info["sar_source"], "unknown")
        self.assertIsNotNone(info["warning"])

    def test_derived_non_square_sar_from_dar(self):
        """720x576 + DAR 16:9 -> derived SAR 64:45 (non-square)."""
        answers = {"video_streams": [{"width": 720, "height": 576,
                                       "display_aspect_ratio": "16:9"}]}
        info = FFmWiz.sar_dar_info(answers)
        self.assertEqual(info["sar_text"], "64:45")
        self.assertEqual(info["sar_source"], "calculated from coded resolution and detected DAR")
        self.assertEqual(info["pixel_shape"], "non-square")

    def test_resize_uses_resolved_effective_dar(self):
        """effective_source_dar reflects the resolved DAR from a derived SAR."""
        answers = {"video_streams": [{"width": 2160, "height": 3840,
                                       "display_aspect_ratio": "9:16"}]}
        self.assertAlmostEqual(FFmWiz.effective_source_dar(answers), 0.5625, places=4)

    def test_no_resize_preserves_derived_non_square_sar(self):
        """No-resize with a DAR-derived non-square SAR omits setsar (preserved)."""
        answers = {
            "video_streams": [{"codec_type": "video", "codec_name": "h264",
                               "width": 720, "height": 576, "display_aspect_ratio": "16:9"}],
            "resolution": "n", "crop_enabled": False,
        }
        vf = FFmWiz.build_cpu_video_filter(answers) or ""
        self.assertNotIn("setsar", vf)

    def test_sar_dar_no_contradictory_states(self):
        """When resolved, neither SAR nor pixel shape remains 'unknown'."""
        info = FFmWiz.sar_dar_info({"video_streams": [{"width": 2160, "height": 3840,
                                                       "display_aspect_ratio": "9:16"}]})
        self.assertNotEqual(info["sar_text"], "unknown")
        self.assertNotEqual(info["pixel_shape"], "unknown")
        self.assertNotEqual(info["sar_source"], "unknown")

    def test_fallback_dar_cannot_be_reused_as_detected(self):
        """A both-unknown fallback result must not enter the detected-DAR branch
        when its numeric DAR is treated as raw input by mistake. The fallback
        result's raw_ffprobe_dar stays None."""
        fallback = FFmWiz.resolve_video_geometry(2160, 3840, None, None)
        self.assertTrue(fallback["fallback_used"])
        self.assertIsNone(fallback["raw_ffprobe_dar"])
        self.assertEqual(fallback["dar_source"], "calculated from coded resolution and fallback SAR")
        self.assertEqual(fallback["sar_source"], "fallback assumption")

        # The pure resolver only consumes raw scalars, so the fallback DAR value
        # can never be passed as raw_ffprobe_dar from the resolver's own output.

    def test_fixture_detected_dar_provenance(self):
        """Fixture 1: raw DAR 9:16 detected -> SAR derived from detected DAR."""
        info = FFmWiz.sar_dar_info({"video_streams": [{"width": 2160, "height": 3840,
                                                       "display_aspect_ratio": "9:16"}]})
        self.assertEqual(info["sar_text"], "1:1")
        self.assertEqual(info["sar_source"], "calculated from coded resolution and detected DAR")
        self.assertEqual(info["dar_text"], "9:16")
        self.assertEqual(info["dar_source"], "detected by ffprobe")
        self.assertEqual(info["pixel_shape"], "square")
        self.assertFalse(info["fallback_used"])

    def test_workflow_preserve_fit_resize_uses_resolved_dar(self):
        """Preserve/Fit resize uses resolved DAR from a derived non-square SAR."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["crop_enabled"] = False
            answers["color_range_choice"] = "tv"
            stream = {"codec_type": "video", "codec_name": "h264",
                      "width": 720, "height": 576, "display_aspect_ratio": "16:9"}
            answers["video_streams"] = [stream]
            answers["resolution"] = FFmWiz.parse_resolution("480p")
            info = FFmWiz.sar_dar_info(answers)
            self.assertEqual(info["sar_text"], "64:45")
            self.assertEqual(info["pixel_shape"], "non-square")
            self.assertAlmostEqual(info["resolved_dar"], 16 / 9, places=4)
            text = self.command_text(answers)
            self.assertIn("reset_sar=1", text)
            self.assertNotIn("setsar=1,scale", text)  # not a forced stretch
            self._assert_raw_immutable(stream, None, "16:9")

    def test_workflow_no_resize_preserves_derived_sar(self):
        """No-resize keeps the derived non-square SAR; source not claimed detected."""
        answers = {
            "video_streams": [{"codec_type": "video", "codec_name": "h264",
                               "width": 720, "height": 576, "display_aspect_ratio": "16:9"}],
            "resolution": "n", "crop_enabled": False,
        }
        info = FFmWiz.sar_dar_info(answers)
        self.assertEqual(info["sar_source"], "calculated from coded resolution and detected DAR")
        vf = FFmWiz.build_cpu_video_filter(answers) or ""
        self.assertNotIn("setsar", vf)
        self._assert_raw_immutable(answers["video_streams"][0], None, "16:9")

    def test_crop_output_dar_cannot_masquerade_as_raw(self):
        """The resolver only reads raw stream fields; post-crop geometry can never
        be fed back as raw ffprobe metadata."""
        stream = {"codec_type": "video", "codec_name": "h264", "width": 720, "height": 576,
                  "sample_aspect_ratio": "16:15"}
        before_sar = stream.get("sample_aspect_ratio")
        before_dar = stream.get("display_aspect_ratio")
        # Simulate crop changing coded dims downstream (separate storage only).
        cropped = dict(stream)
        cropped["width"] = 712
        cropped["height"] = 572
        info = FFmWiz.sar_dar_info({"video_streams": [stream]})
        # Original raw fields are untouched and still drive provenance.
        self.assertEqual(stream.get("sample_aspect_ratio"), before_sar)
        self.assertEqual(stream.get("display_aspect_ratio"), before_dar)
        self.assertEqual(info["raw_ffprobe_dar"], None)
        self.assertEqual(info["dar_source"], "calculated from coded resolution and SAR")

    def test_hardsub_square_sar_single_setsar(self):
        """Square-SAR HardSub gains exactly one setsar=1 (no redundant/contradictory)."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.hardsub_answers(tmp, output_ext="mkv")
            answers["color_range_choice"] = "tv"
            answers["video_streams"] = [{"codec_type": "video", "codec_name": "h264",
                                         "width": 1920, "height": 1080,
                                         "sample_aspect_ratio": "1:1"}]
            vf = FFmWiz.build_hardsub_video_filter(answers, "libx265")
            self.assertEqual(vf.count("setsar="), 1)
            self.assertIn("setsar=1", vf)
            self.assertNotIn("setsar=1/1", vf)


if __name__ == "__main__":
    unittest.main()
