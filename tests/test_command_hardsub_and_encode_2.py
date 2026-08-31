"""Split from the command-generation suite (see command_gen_base.py)."""
import contextlib
import io
import tempfile
from pathlib import Path
from unittest import mock
import FFmWiz
import cache_test_utils
from command_gen_base import CommandGenBase


class CommandHardsubAndEncodeTests2(CommandGenBase):
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

    def test_config_loudnorm_over_copy_still_applies_the_audio_sample_rate(self):
        # LoudNorm turns `audio_codec=copy` into an AAC re-encode. The sample
        # rate gate asks "is the audio re-encoded"; read before that flip it saw
        # the stale `copy` and dropped the user's rate, so the command came out
        # `-c:a aac -b:a 128k` with no `-ar` at all.
        answers = self._extra_recipe_answers()
        answers["audio_codec"] = "copy"
        cfg = FFmWiz.parse_env_config(
            "audio_codec=copy\naudio_sample_rate=48000\nloudnorm=on")
        with contextlib.redirect_stdout(io.StringIO()):
            FFmWiz.apply_config_extra_recipe_options(answers, cfg)
        self.assertEqual(answers["audio_codec"], FFmWiz.DEFAULT_AUDIO_CODEC)
        self.assertEqual(answers["audio_sample_rate"], 48000)
        self.assertFalse(answers.get("audio_sample_rate_keep"))
        cmd = []
        with contextlib.redirect_stdout(io.StringIO()):
            FFmWiz.append_audio_encode_options(cmd, answers, True)
        self.assertIn("-ar", cmd)
        self.assertEqual(cmd[cmd.index("-ar") + 1], "48000")

    def test_config_audio_copy_without_loudnorm_keeps_dropping_the_sample_rate(self):
        # The other side of the same gate: a real stream copy re-encodes
        # nothing, so an output sample rate is not applicable and must not be
        # recorded as if it were.
        answers = self._extra_recipe_answers()
        answers["audio_codec"] = "copy"
        cfg = FFmWiz.parse_env_config(
            "audio_codec=copy\naudio_sample_rate=48000\nloudnorm=off")
        with contextlib.redirect_stdout(io.StringIO()):
            FFmWiz.apply_config_extra_recipe_options(answers, cfg)
        self.assertIsNone(answers.get("audio_sample_rate"))

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
