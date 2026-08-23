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
# reset_sar exists only on FFmpeg 7.2+; the shared helper asserts the scale
# chain each capability branch must emit. See tests/test_sar_capability.py.
from test_sar_capability import assert_both_scale_branches


class CommandGenerationCoreTests2(CommandGenBase):
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
        def build():
            with tempfile.TemporaryDirectory() as tmp:
                answers = self.base_answers(tmp)
                # Anamorphic 4:3 source: coded 720x576 with SAR 16:15 → display 768x576 (4:3)
                answers["video_streams"] = [{"codec_type": "video", "codec_name": "h264", "width": 720, "height": 576, "sample_aspect_ratio": "16:15"}]
                answers["crop_enabled"] = False
                answers["use_gpu"] = False
                answers["resolution"] = FFmWiz.parse_resolution("480p")
                text = self.command_text(answers)
            # The display AR is 4:3, so the canvas is 4:3 (720x540), NOT the
            # 5:4 the coded 720x576 would give. Same on either FFmpeg.
            dims = answers.get("final_resolution")
            self.assertEqual((720, 540), dims)
            display_ar = 768 / 576  # 4:3
            self.assertAlmostEqual(dims[0] / dims[1], display_ar, delta=0.02)
            return text

        # The square pixels come from the scale chain, never a trailing setsar.
        assert_both_scale_branches(
            self, build,
            "scale=720:540:force_original_aspect_ratio=decrease:force_divisible_by=2")

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


if __name__ == "__main__":
    unittest.main()
