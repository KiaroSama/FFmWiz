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


if __name__ == "__main__":
    unittest.main()
