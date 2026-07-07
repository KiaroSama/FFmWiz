"""Split from test_loudnorm_join_progress.py (see join_test_helpers.py)."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import FFmWiz
from join_test_helpers import audio_stream, _vstream


class PixelFormatResolverTests(unittest.TestCase):
    """Architecture-aware 10-bit/12-bit+ pixel-format selection (CPU vs NVENC)."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def _ans(self, pix_fmt, depth, use_gpu, codec="H265"):
        return {"video_streams": [_vstream(pix_fmt, depth)], "video_codec": codec, "use_gpu": use_gpu}

    # ---- resolver: encoder x bit depth ----
    def test_libx265_main_8bit_yuv420p(self):
        a = self._ans("yuv420p", 8, False)
        self.assertEqual(FFmWiz.cpu_graph_pixel_format_for_encoder(a), "yuv420p")
        self.assertEqual(FFmWiz.hevc_profile_for_output(a, "main"), "main")

    def test_libx265_main10_10bit_yuv420p10le(self):
        a = self._ans("yuv420p10le", 10, False)
        self.assertEqual(FFmWiz.cpu_graph_pixel_format_for_encoder(a), "yuv420p10le")
        self.assertEqual(FFmWiz.hevc_profile_for_output(a, "main"), "main10")

    def test_nvenc_main_8bit_format(self):
        a = self._ans("yuv420p", 8, True)
        # CPU filter graph feeding NVENC keeps yuv420p (8-bit, unchanged).
        self.assertEqual(FFmWiz.cpu_graph_pixel_format_for_encoder(a), "yuv420p")
        # CUDA hardware path uses nv12.
        self.assertEqual(FFmWiz.cuda_pixel_format_for_output(a), "nv12")
        self.assertEqual(FFmWiz.target_pixel_format_for_answers(a), "nv12")

    def test_nvenc_main10_10bit_p010le(self):
        a = self._ans("yuv420p10le", 10, True)
        self.assertEqual(FFmWiz.cpu_graph_pixel_format_for_encoder(a), "p010le")
        self.assertEqual(FFmWiz.cuda_pixel_format_for_output(a), "p010le")
        self.assertEqual(FFmWiz.hevc_profile_for_output(a), "main10")

    # ---- source/output preservation + notes ----
    def test_source_10bit_output_10bit_preserved_no_note(self):
        a = self._ans("yuv420p10le", 10, False)
        self.assertEqual(FFmWiz.output_video_bit_depth(a), 10)
        self.assertIsNone(FFmWiz.bit_depth_precision_note(a))

    def test_source_8bit_output_10bit_upconvert_note(self):
        a = self._ans("yuv420p", 8, False)
        a["force_output_bit_depth"] = 10
        note = FFmWiz.bit_depth_precision_note(a)
        self.assertIsNotNone(note)
        self.assertIn("source precision remains 8-bit", note)
        # Up-conversion is not a reduction.
        self.assertNotIn("reduced", note)

    def test_unsupported_high_depth_caps_at_10(self):
        # 12-bit+ never silently kept; output capped at 10-bit Main10.
        for depth, pix in ((12, "yuv420p12le"), (14, "yuv420p14le"), (16, "yuv420p16le")):
            a = self._ans(pix, depth, False)
            self.assertEqual(FFmWiz.output_video_bit_depth(a), 10)
            self.assertEqual(FFmWiz.cpu_pixel_format_for_output(a), "yuv420p10le")
            self.assertEqual(FFmWiz.hevc_profile_for_output(a, "main"), "main10")

    # ---- 12-bit+ reduction notes ----
    def test_12bit_reduction_note(self):
        a = self._ans("yuv420p12le", 12, False)
        self.assertIn("reduced from 12-bit to 10-bit", FFmWiz.bit_depth_precision_note(a))

    def test_14bit_reduction_note(self):
        a = self._ans("yuv420p14le", 14, False)
        self.assertIn("reduced from 14-bit to 10-bit", FFmWiz.bit_depth_precision_note(a))

    def test_12bit_forced_8bit_reduction_note(self):
        a = self._ans("yuv420p12le", 12, False)
        a["force_output_bit_depth"] = 8
        self.assertIn("reduced from 12-bit to 8-bit", FFmWiz.bit_depth_precision_note(a))

    # ---- terminal format of the software filter graph ----
    def test_cpu_filter_nvenc_10bit_ends_p010le(self):
        f = FFmWiz.build_cpu_video_filter(self._ans("yuv420p10le", 10, True))
        self.assertTrue(f.endswith("format=p010le"), f)

    def test_cpu_filter_libx265_10bit_ends_yuv420p10le(self):
        f = FFmWiz.build_cpu_video_filter(self._ans("yuv420p10le", 10, False))
        self.assertTrue(f.endswith("format=yuv420p10le"), f)

    def test_cpu_filter_nvenc_8bit_ends_yuv420p(self):
        f = FFmWiz.build_cpu_video_filter(self._ans("yuv420p", 8, True))
        self.assertTrue(f.endswith("format=yuv420p"), f)

    def test_cuda_filter_10bit_uses_scale_cuda_p010le(self):
        f = FFmWiz.build_cuda_video_filter(self._ans("yuv420p10le", 10, True))
        self.assertIn("format=p010le", f)
        self.assertNotIn("yuv420p10le", f)

    def test_cuda_filter_8bit_uses_nv12(self):
        f = FFmWiz.build_cuda_video_filter(self._ans("yuv420p", 8, True))
        self.assertIn("format=nv12", f)


class JoinMain10CommandTests(unittest.TestCase):
    """Join command generation: GPU Main10 must use p010le, CPU Main10
    yuv420p10le, and 8-bit NVENC must stay unchanged."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def _join(self, tmp, use_gpu, depth=10, codec="H265", loudnorm=False):
        pix = "yuv420p10le" if depth >= 10 else "yuv420p"
        v = _vstream(pix, depth)
        a = audio_stream()
        def item(name):
            return {"path": Path(tmp) / name, "streams": [v, a], "video_streams": [v],
                    "audio_streams": [a], "format": {"duration": "5"}, "duration": 5.0}
        answers = {
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "input_path": Path(tmp) / "A.mov",
            "output_ext": "mp4", "video_codec": codec, "use_gpu": use_gpu,
            "video_streams": [v], "audio_streams": [a], "subtitle_streams": [],
            "data_streams": [], "attachment_streams": [], "audio_tracks": [0],
            "subtitle_tracks": [], "audio_codec": "aac", "audio_bitrate_kbps": 128,
            "resolution": "n", "fps": 30, "video_bitrate_kbps": 4000,
            "format": {"duration": "5"}, "color_range_choice": "tv",
            "join_input_items": [item("B.mov")],
        }
        if loudnorm:
            answers.update({"loudnorm_enabled": True, "loudnorm_mode": "single", "loudnorm_target_i": -16.0})
        items = [item("A.mov"), item("B.mov")]
        return answers, items

    def test_join_gpu_main10_uses_p010le_not_yuv420p10le(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self._join(tmp, use_gpu=True, depth=10)
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, Path(tmp) / "out.mp4"))
        self.assertIn("format=p010le", text)
        self.assertNotIn("format=yuv420p10le", text)
        self.assertIn("hevc_nvenc", text)
        self.assertIn("-profile:v main10", text)
        self.assertNotIn("-profile:v main ", text)

    def test_join_cpu_main10_uses_yuv420p10le(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self._join(tmp, use_gpu=False, depth=10)
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, Path(tmp) / "out.mp4"))
        self.assertIn("format=yuv420p10le", text)
        self.assertIn("-c:v libx265", text)
        self.assertIn("-profile:v main10", text)
        self.assertNotIn("format=p010le", text)

    def test_join_8bit_nvenc_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self._join(tmp, use_gpu=True, depth=8)
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, Path(tmp) / "out.mp4"))
        self.assertIn("format=yuv420p", text)
        self.assertNotIn("format=p010le", text)
        self.assertNotIn("main10", text)

    def test_join_gpu_main10_with_loudnorm_keeps_p010le(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers, items = self._join(tmp, use_gpu=True, depth=10, loudnorm=True)
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, Path(tmp) / "out.mp4"))
        self.assertIn("format=p010le", text)
        self.assertIn("loudnorm", text)
        self.assertIn("-profile:v main10", text)


class JoinSummaryEnhancementTests(unittest.TestCase):
    """Join input summary: distinct colors, total raw duration + frame count,
    and audio volume extremes."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def _rows_answers(self, with_volume=True, with_duration=True):
        def item(name, vk, ak, fps, dur, mean, mx):
            v = {**_vstream(), "bit_rate": str(vk * 1000)}
            a = {**audio_stream(bit_rate=str(ak * 1000))}
            it = {"path": Path(name), "streams": [v, a], "video_streams": [v],
                  "audio_streams": [a], "format": {"duration": str(dur)} if with_duration else {},
                  "duration": float(dur) if with_duration else None}
            v["avg_frame_rate"] = f"{fps}/1"
            if with_volume:
                it["audio_volume_stats"] = {0: {"mean_volume": f"{mean} dB", "max_volume": f"{mx} dB"}}
            return it
        primary = item("A.mov", 8900, 175, 30, 600.0, "-19.8", "-0.6")
        extra1 = item("B.mov", 8499, 164, 30, 700.0, "-24.1", "-2.0")
        extra2 = item("C.mov", 8948, 170, 29, 0.0, "-20.0", "-0.3")
        answers = {
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "input_path": primary["path"],
            "format": primary["format"], "video_streams": primary["video_streams"],
            "audio_streams": primary["audio_streams"], "data_streams": [],
            "subtitle_streams": [], "attachment_streams": [], "duration": primary["duration"],
            "fps": 30, "audio_volume_stats": primary.get("audio_volume_stats"),
            "join_input_items": [extra1, extra2],
        }
        return answers

    def _capture_summary(self, answers):
        buf = io.StringIO()
        with mock.patch.object(FFmWiz.services, "get_packet_sizes", return_value={}):
            with contextlib.redirect_stdout(buf):
                FFmWiz.print_join_input_summary(answers)
        return buf.getvalue()

    def test_plain_lines_include_duration_and_volume(self):
        answers = self._rows_answers()
        with mock.patch.object(FFmWiz.services, "get_packet_sizes", return_value={}):
            lines = FFmWiz.format_join_input_summary_lines(answers)
        joined = "\n".join(lines)
        self.assertIn("Files selected: 3", joined)
        self.assertIn("Total raw duration:", joined)
        self.assertIn("Mean volume: lowest -24.1 dB (B.mov)", joined)
        self.assertIn("Max volume: highest -0.3 dB (C.mov)", joined)

    def test_total_raw_duration_and_frame_count(self):
        answers = self._rows_answers()
        with mock.patch.object(FFmWiz.services, "get_packet_sizes", return_value={}):
            rows = FFmWiz.join_input_media_stats(answers)
            info = FFmWiz.join_summary_total_duration(answers, rows)
        # One file (C.mov) has unknown duration -> 600 + 700 known.
        self.assertEqual(info["unknown_count"], 1)
        self.assertAlmostEqual(info["total_seconds"], 1300.0)
        # Output fps is set -> frames at selected output fps.
        self.assertIsNotNone(info["frames"])
        self.assertIn("selected output", info["frame_basis"])

    def test_duration_handles_all_unknown(self):
        answers = self._rows_answers(with_duration=False)
        with mock.patch.object(FFmWiz.services, "get_packet_sizes", return_value={}):
            rows = FFmWiz.join_input_media_stats(answers)
            info = FFmWiz.join_summary_total_duration(answers, rows)
        self.assertEqual(info["known_count"], 0)
        self.assertEqual(FFmWiz.join_summary_duration_text(info), "unavailable")

    def test_volume_extremes_unavailable_clean(self):
        answers = self._rows_answers(with_volume=False)
        with mock.patch.object(FFmWiz.services, "get_packet_sizes", return_value={}):
            rows = FFmWiz.join_input_media_stats(answers)
            self.assertIsNone(FFmWiz.join_summary_volume_extremes(rows))

    def test_summary_colors_highest_differs_from_lowest(self):
        answers = self._rows_answers()
        FFmWiz.appio.USE_COLOR = True
        try:
            out = self._capture_summary(answers)
        finally:
            FFmWiz.appio.USE_COLOR = False
        # highest and lowest must use different color categories.
        self.assertIn(FFmWiz.Color.JOIN_HIGH, out)
        self.assertIn(FFmWiz.Color.JOIN_LOW, out)
        self.assertNotEqual(FFmWiz.Color.JOIN_HIGH, FFmWiz.Color.JOIN_LOW)
        # file names use a distinct color.
        self.assertIn(FFmWiz.Color.JOIN_FILE, out)
        # volume extremes use distinct colors.
        self.assertIn(FFmWiz.Color.JOIN_VOL_LOW, out)
        self.assertIn(FFmWiz.Color.JOIN_VOL_HIGH, out)

    def test_summary_does_not_print_full_paths(self):
        answers = self._rows_answers()
        out = self._capture_summary(answers)
        # Only base names, never directory separators from the item paths.
        self.assertIn("A.mov", out)
        self.assertNotIn("/A.mov", out)
        self.assertNotIn("\\A.mov", out)


class LoudnormHighlightTests(unittest.TestCase):
    """Integrated loudness must be emphasized (bold + green)."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    STATS = {"input_i": -21.4, "input_tp": -0.7, "input_lra": 8.0,
             "input_thresh": -32.0, "target_offset": -0.4}

    def test_integrated_loudness_label_emerald_bold_value_original(self):
        FFmWiz.appio.USE_COLOR = True
        try:
            line = FFmWiz.format_integrated_loudness_line(self.STATS)
        finally:
            FFmWiz.appio.USE_COLOR = False
        # Label: bold + a distinct (emerald) green.
        self.assertIn(FFmWiz.Color.MUX_EMERALD, line)
        self.assertIn(FFmWiz.Color.BOLD, line)
        # Value keeps its original MEAN_VOLUME color (not recolored green).
        self.assertIn(FFmWiz.Color.MEAN_VOLUME, line)
        self.assertIn("-21.4 LUFS", line)

    def test_other_lines_not_emerald_bold(self):
        FFmWiz.appio.USE_COLOR = True
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                FFmWiz.trackmanager.print_loudnorm_stats(self.STATS)
            out = buf.getvalue()
        finally:
            FFmWiz.appio.USE_COLOR = False
        # Only the integrated-loudness label is emerald; true-peak is not.
        true_peak_line = [ln for ln in out.splitlines() if "True peak" in ln][0]
        self.assertNotIn(FFmWiz.Color.MUX_EMERALD, true_peak_line)


class JoinVolumeScanTests(unittest.TestCase):
    """ensure_join_volume_stats populates volume extremes via a (mocked) scan."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def _answers(self, tmp):
        v = _vstream()
        a = audio_stream()
        def item(name):
            return {"path": Path(tmp) / name, "streams": [v, a], "video_streams": [v],
                    "audio_streams": [a], "format": {"duration": "5"}, "duration": 5.0}
        return {
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "input_path": Path(tmp) / "A.mov",
            "format": {"duration": "5"}, "video_streams": [v], "audio_streams": [a],
            "data_streams": [], "subtitle_streams": [], "attachment_streams": [], "duration": 5.0,
            "fps": 30, "join_input_items": [item("B.mov"), item("C.mov")],
        }

    def test_scan_populates_volume_then_summary_shows_extremes(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._answers(tmp)
            fake = {
                Path(tmp) / "A.mov": {"mean_volume": "-19.8 dB", "max_volume": "-0.6 dB"},
                Path(tmp) / "B.mov": {"mean_volume": "-24.1 dB", "max_volume": "-2.0 dB"},
                Path(tmp) / "C.mov": {"mean_volume": "-20.0 dB", "max_volume": "-0.3 dB"},
            }
            def fake_probe(ffmpeg, path, idx):
                return fake[Path(path)]
            with mock.patch.object(FFmWiz.services, "probe_audio_volume_stats", side_effect=fake_probe), \
                 mock.patch.object(FFmWiz.services, "get_packet_sizes", return_value={}):
                FFmWiz.ensure_join_volume_stats(answers)
                # Primary cached on answers; extras cached on their item dicts.
                self.assertEqual(answers["audio_volume_stats"][0]["mean_volume"], "-19.8 dB")
                self.assertIn("audio_volume_stats", answers["join_input_items"][0])
                lines = FFmWiz.format_join_input_summary_lines(answers)
        joined = "\n".join(lines)
        self.assertIn("Mean volume: lowest -24.1 dB (B.mov)", joined)
        self.assertIn("Max volume: highest -0.3 dB (C.mov)", joined)

    def test_scan_skips_when_already_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self._answers(tmp)
            answers["audio_volume_stats"] = {0: {"mean_volume": "-10 dB", "max_volume": "-1 dB"}}
            for it in answers["join_input_items"]:
                it["audio_volume_stats"] = {0: {"mean_volume": "-10 dB", "max_volume": "-1 dB"}}
            with mock.patch.object(FFmWiz.services, "probe_audio_volume_stats",
                                   side_effect=AssertionError("should not probe")) as probe:
                FFmWiz.ensure_join_volume_stats(answers)
                probe.assert_not_called()


class SmoothedEtaTests(unittest.TestCase):
    """ETA rate uses the overall average: stable and accurate (no early
    overshoot, no jumps)."""

    def test_first_sample_is_overall_average(self):
        rate = FFmWiz._smoothed_eta_rate({}, current_s=100.0, elapsed=10.0)
        self.assertAlmostEqual(rate, 10.0, places=3)  # 100/10

    def test_overall_average_does_not_overshoot_a_fast_burst(self):
        # A recent fast burst must not make the rate spike: the overall average
        # (700 media-s / 30 wall-s) stays near the true average, far below the
        # 50x of the last 10s window, so the ETA never becomes wildly optimistic.
        state = {}
        FFmWiz._smoothed_eta_rate(state, 100.0, 10.0)
        FFmWiz._smoothed_eta_rate(state, 200.0, 20.0)
        rate = FFmWiz._smoothed_eta_rate(state, 700.0, 30.0)
        self.assertAlmostEqual(rate, 700.0 / 30.0, places=3)
        self.assertLess(rate, 50.0)

    def test_pure_function_of_progress_and_elapsed(self):
        self.assertAlmostEqual(FFmWiz._smoothed_eta_rate({}, 200.0, 20.0), 10.0, places=3)
        self.assertAlmostEqual(FFmWiz._smoothed_eta_rate({}, 200.0, 20.2), 200.0 / 20.2, places=3)
        self.assertIsNone(FFmWiz._smoothed_eta_rate({}, 0.0, 10.0))


class LosslessSplitExtTests(unittest.TestCase):
    """Lossless audio split lets the user choose a copy-compatible extension."""

    def setUp(self):
        FFmWiz.appio.USE_COLOR = False

    def test_copy_ext_choices_per_codec(self):
        self.assertEqual(FFmWiz.lossless_audio_copy_ext_choices("aac")[0], "m4a")
        self.assertIn("aac", FFmWiz.lossless_audio_copy_ext_choices("aac"))
        self.assertIn("mka", FFmWiz.lossless_audio_copy_ext_choices("aac"))
        self.assertEqual(FFmWiz.lossless_audio_copy_ext_choices("flac")[0], "flac")
        self.assertEqual(FFmWiz.lossless_audio_copy_ext_choices("opus")[0], "opus")
        # Unknown codec -> universal containers.
        self.assertEqual(FFmWiz.lossless_audio_copy_ext_choices("weird"), ["mka", "mov"])

    def test_ask_uses_chosen_ext_and_lists_copy_options(self):
        answers = {"input_path": Path("clip.mp4"),
                   "audio_streams": [{"codec_type": "audio", "codec_name": "aac"}], "audio_index": 0}
        buf = io.StringIO()
        with mock.patch.object(FFmWiz.appio, "ask_raw", return_value="aac"):
            with contextlib.redirect_stdout(buf):
                ext = FFmWiz.ask_lossless_split_ext(answers, "aac")
        self.assertEqual(ext, "aac")
        self.assertEqual(answers["lossless_split_ext"], "aac")

    def test_ask_default_is_preferred_container(self):
        answers = {"input_path": Path("clip.mp4"),
                   "audio_streams": [{"codec_type": "audio", "codec_name": "aac"}], "audio_index": 0}
        with mock.patch.object(FFmWiz.appio, "ask_raw", return_value=""):
            with contextlib.redirect_stdout(io.StringIO()):
                ext = FFmWiz.ask_lossless_split_ext(answers, "aac")
        self.assertEqual(ext, "m4a")  # Enter -> preferred default

    def test_chosen_ext_used_in_split_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = {"ffmpeg": "ffmpeg", "input_path": Path(tmp) / "clip.mp4",
                       "output_location": Path(tmp), "audio_index": 0,
                       "audio_streams": [{"codec_type": "audio", "codec_name": "aac"}],
                       "lossless_split_ext": "aac"}
            cmd, pattern = FFmWiz.build_lossless_split_command(answers, [600.0])
        self.assertIn("_part%03d.aac", str(pattern))
        self.assertEqual(cmd[cmd.index("-map") + 1], "0:a:0")


if __name__ == "__main__":
    unittest.main()
