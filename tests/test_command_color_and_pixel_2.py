"""Split from the command-generation suite (see command_gen_base.py)."""
import contextlib
import io
import tempfile
from pathlib import Path
from unittest import mock
import FFmWiz
from command_gen_base import CommandGenBase
# reset_sar exists only on FFmpeg 7.2+; the shared helper asserts the scale
# chain each capability branch must emit. See tests/test_sar_capability.py.
from test_sar_capability import assert_both_scale_branches


class CommandColorAndPixelTests2(CommandGenBase):
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
        def build():
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
            # Same resolved geometry on either FFmpeg; only the chain differs.
            self.assertEqual((720, 576), answers.get("final_resolution"))
            # Not a forced stretch: the target scale keeps the source AR, so a
            # bare `scale=720:576` (the stretch shape) must never appear.
            self.assertIn("force_original_aspect_ratio=decrease", text)
            self.assertNotIn("scale=720:576,", text)
            self._assert_raw_immutable(stream, None, "16:9")
            return text

        assert_both_scale_branches(
            self, build,
            "scale=720:576:force_original_aspect_ratio=decrease:force_divisible_by=2")

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
