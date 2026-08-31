"""Split from the command-generation suite (see command_gen_base.py)."""
import contextlib
import io
import tempfile
from pathlib import Path
from unittest import mock
import FFmWiz
from command_gen_base import CommandGenBase, _home_module


class CommandCutJoinFolderTests(CommandGenBase):
    def test_progress_split_shows_real_size_not_target_extrapolation(self):
        # For a multi-output Split, the per-tick progress is byte-derived, so
        # extrapolating size at the target bitrate would fake a frozen
        # size/bitrate. With >1 output path the displayed size must be the REAL
        # summed on-disk bytes (no "estimated-file").
        with tempfile.TemporaryDirectory() as tmp:
            part1 = Path(tmp) / "out_Part01.mp4"
            part2 = Path(tmp) / "out_Part02.mp4"
            part1.write_bytes(b"x" * 3_000_000)
            part2.write_bytes(b"x" * 1_000_000)  # real aggregate = 4 MB
            state = {
                "progress": "continue",
                "_ffmwiz_target_bitrate_kbps": "1600",
                "_ffmwiz_last_file_size_bytes": "4000000",
                "_ffmwiz_last_file_size_seconds": "10.000000",
            }
            FFmWiz._apply_output_file_size_progress(state, [part1, part2], 12.0)
            self.assertEqual(state["_ffmwiz_size_source"], "file")  # not estimated
            self.assertIn("3.8 MB", state["_ffmwiz_size_text"])     # 4,000,000 bytes ~= 3.8 MB

    def test_progress_split_size_refresh_does_not_overwrite_bitrate(self):
        # The split branch owns the bitrate (it derives it from the REAL
        # reconstructed media time). The on-disk size refresh must only update
        # the size for a multi-output Split, never the bitrate, otherwise the
        # bitrate jumps between the real value and a stale-current_s spike.
        with tempfile.TemporaryDirectory() as tmp:
            part1 = Path(tmp) / "out_Part01.mp4"
            part2 = Path(tmp) / "out_Part02.mp4"
            part1.write_bytes(b"x" * 3_000_000)
            part2.write_bytes(b"x" * 1_000_000)
            state = {
                "progress": "continue",
                "_ffmwiz_bitrate_text": "1492.3kbits/s",  # set by the split branch
            }
            FFmWiz._apply_output_file_size_progress(state, [part1, part2], 12.0)
            # Size updated, bitrate left untouched.
            self.assertIn("3.8 MB", state["_ffmwiz_size_text"])
            self.assertEqual(state["_ffmwiz_bitrate_text"], "1492.3kbits/s")

    def test_split_progress_uses_aggregate_frame_time_without_double_counting(self):
        state = {
            "frame": "60",
            "fps": "58.72",
            "stream_0_0_q": "10.0",
            "bitrate": "419.5kbits/s",
            "total_size": "786432",
            "out_time_us": "15000000",
            "out_time_ms": "15000000",
            "out_time": "00:00:15.000000",
            "speed": "0.413x",
            "progress": "continue",
        }
        raw_seconds = FFmWiz._progress_raw_seconds_from_state(state)
        current_seconds = max(raw_seconds, float(state["frame"]) / 4.0)
        state["_ffmwiz_current_s"] = str(current_seconds)
        state["_ffmwiz_prefer_elapsed_speed"] = "1"
        state["_ffmwiz_speed_text"] = "15x"
        state["_ffmwiz_size_text"] = "768.0 KB"
        state["_ffmwiz_bitrate_text"] = f"{int(state['total_size']) * 8 / 1000 / current_seconds:.1f}kbits/s"
        line = FFmWiz._strip_ansi(FFmWiz._render_progress_line(state, 60.0, FFmWiz.time.perf_counter() - 2))
        self.assertIn("25.0%", line)
        self.assertIn("time 00:00:15 / 00:01:00", line)
        self.assertIn("speed 15x", line)
        self.assertIn("size 768.0 KB", line)
        self.assertIn("bitrate 419.4kbits/s", line)
        self.assertNotIn("bitrate 419.5kbits/s", line)

    def test_split_progress_advances_after_first_output_part(self):
        current_seconds, active_part, _ = FFmWiz._split_progress_seconds(
            raw_current_s=6.16,
            frame_seconds=10.0,
            part_durations=[10.0, 10.0],
            output_sizes=[786480, 120000],
            previous_output_sizes=[786480, 0],
            active_part=0,
            previous_raw_s=0.0,
        )
        self.assertEqual(active_part, 1)
        self.assertAlmostEqual(current_seconds, 16.16, places=2)

    def test_split_progress_detects_part_handoff_before_file_size_flush(self):
        current_seconds, active_part, _ = FFmWiz._split_progress_seconds(
            raw_current_s=1.10,
            frame_seconds=10.0,
            part_durations=[10.0, 10.0],
            output_sizes=[786480, 0],
            previous_output_sizes=[786480, 0],
            active_part=0,
            previous_raw_s=0.0,
        )
        self.assertEqual(active_part, 1)
        self.assertAlmostEqual(current_seconds, 11.10, places=2)

    def test_split_progress_keeps_first_part_when_it_is_still_growing(self):
        current_seconds, active_part, _ = FFmWiz._split_progress_seconds(
            raw_current_s=9.70,
            frame_seconds=10.0,
            part_durations=[10.0, 10.0],
            output_sizes=[900000, 0],
            previous_output_sizes=[786480, 0],
            active_part=0,
            previous_raw_s=9.4,
        )
        self.assertEqual(active_part, 0)
        self.assertAlmostEqual(current_seconds, 10.0, places=2)

    def test_split_progress_does_not_move_active_part_backwards(self):
        current_seconds, active_part, _ = FFmWiz._split_progress_seconds(
            raw_current_s=7.0,
            frame_seconds=10.0,
            part_durations=[10.0, 10.0],
            output_sizes=[900000, 120000],
            previous_output_sizes=[786480, 120000],
            active_part=1,
            previous_raw_s=6.0,
            active_part_start_raw=0.0,
        )
        self.assertEqual(active_part, 1)
        self.assertAlmostEqual(current_seconds, 17.0, places=2)

    def test_split_progress_ignores_tiny_next_part_header_write(self):
        # Part 2's file was just created (small container header flush) while
        # Part 1 is still being written. The active part must NOT jump forward,
        # otherwise the aggregate percent jumps (e.g. 30% -> 59%).
        current_seconds, active_part, _ = FFmWiz._split_progress_seconds(
            raw_current_s=3.0,
            frame_seconds=3.0,
            part_durations=[10.0, 10.0],
            output_sizes=[400000, 20000],       # part1 paused this tick; part2 tiny header
            previous_output_sizes=[400000, 0],
            active_part=0,
            previous_raw_s=2.5,
        )
        self.assertEqual(active_part, 0)
        self.assertAlmostEqual(current_seconds, 3.0, places=2)

    def test_split_progress_handles_global_continuous_out_time_without_double_count(self):
        # Some ffmpeg builds/filter graphs report -progress out_time as the
        # GLOBAL/continuous input position rather than resetting to zero for
        # each output. Part 1 = 100s, part 2 = 200s (total 300s). When part 2
        # becomes active, the global out_time is already ~100s (= part 1 end).
        # The aggregate must NOT become offset(100) + raw(100) = 200s (66%); it
        # must stay ~100s (33%). This is the root cause of the reported
        # "30% -> 59%" jump and the wrong (too-low) bitrate during part 2.
        durations = [100.0, 200.0]
        # Tick at the part1->part2 handoff: global out_time = 100, part2 file
        # just started growing past the 64KB threshold.
        current_seconds, active_part, baseline = FFmWiz._split_progress_seconds(
            raw_current_s=100.0,
            frame_seconds=100.0,
            part_durations=durations,
            output_sizes=[6_600_000, 200_000],
            previous_output_sizes=[6_600_000, 0],
            active_part=0,
            previous_raw_s=99.5,
            active_part_start_raw=0.0,
        )
        self.assertEqual(active_part, 1)
        # No double-count: ~100s (33%), NOT 200s (66%).
        self.assertAlmostEqual(current_seconds, 100.0, places=1)
        self.assertAlmostEqual(baseline, 100.0, places=1)
        # A later tick deep into part 2: global out_time = 250s -> aggregate 250s.
        current_seconds, active_part, baseline = FFmWiz._split_progress_seconds(
            raw_current_s=250.0,
            frame_seconds=100.0,
            part_durations=durations,
            output_sizes=[6_600_000, 10_000_000],
            previous_output_sizes=[6_600_000, 9_900_000],
            active_part=1,
            previous_raw_s=249.0,
            active_part_start_raw=baseline,
        )
        self.assertEqual(active_part, 1)
        self.assertAlmostEqual(current_seconds, 250.0, places=1)

    def test_split_points_generate_part_outputs_after_final_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["separator_points"] = [120.0, 300.0]
            text = self.command_text(answers)
            self.assertIn("Pato12_Part01.mp4", text)
            self.assertIn("Pato12_Part02.mp4", text)
            self.assertIn("Pato12_Part03.mp4", text)
            self.assertIn("[vfinal]split=3[svpart0_src][svpart1_src][svpart2_src]", text)
            self.assertIn("trim=start=0.000000:end=120.000000,setpts=PTS-STARTPTS[svout0]", text)
            self.assertIn("trim=start=120.000000:end=300.000000,setpts=PTS-STARTPTS[svout1]", text)
            self.assertIn("-map [svout0]", text)
            self.assertIn("-map [svout1]", text)
            self.assertIn("-map [svout2]", text)
            self.assertNotIn("Pato12_1.mp4", text)

    def test_join_videos_copy_command_uses_concat_demuxer_and_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mkv"
            second = base / "b.mkv"
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
            compatible, reasons = FFmWiz.join_copy_compatibility(items)
            self.assertTrue(compatible, reasons)
            cmd = FFmWiz.build_join_copy_command({"ffmpeg": "ffmpeg"}, items, base / "joined.mkv")
            self.assertIn("-f", cmd)
            self.assertIn("concat", cmd)
            self.assertIn("-c", cmd)
            self.assertIn("copy", cmd)

    def test_join_frame_rate_helpers_detect_and_classify_mixed_rates(self):
        def item(rate, **over):
            stream = {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": rate,
                "pix_fmt": "yuv420p",
            }
            stream.update(over)
            return {
                "path": Path(f"clip_{rate.replace('/', '_')}.mkv"),
                "streams": [stream],
                "video_streams": [stream],
                "audio_streams": [],
                "format": {},
                "duration": 1.0,
            }

        mixed = [item("24/1"), item("30/1"), item("60/1")]
        self.assertEqual(FFmWiz.join_video_frame_rates(mixed), [24.0, 30.0, 60.0])
        self.assertTrue(FFmWiz.join_frame_rates_differ(mixed))
        self.assertEqual(FFmWiz.join_highest_frame_rate(mixed), 60.0)
        # Differ only by fps -> not fully copy-compatible, but copy-compatible
        # except for the frame rate (concat demuxer can preserve VFR).
        self.assertFalse(FFmWiz.join_copy_compatibility(mixed)[0])
        self.assertTrue(FFmWiz.join_copy_compatible_except_fps(mixed))

        same = [item("30/1"), item("30/1")]
        self.assertFalse(FFmWiz.join_frame_rates_differ(same))

        # A resolution difference is a real incompatibility even for VFR.
        res_diff = [item("24/1"), item("30/1", width=1920)]
        self.assertFalse(FFmWiz.join_copy_compatible_except_fps(res_diff))

    def test_join_near_quality_vfr_omits_fps_filter_and_sets_fps_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mkv"
            second = base / "b.mkv"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "24/1",
                "pix_fmt": "yuv420p",
            }
            items = [
                {"path": first, "streams": [stream], "video_streams": [stream], "audio_streams": [], "format": {}, "duration": 1.0},
                {"path": second, "streams": [dict(stream, width=1920, avg_frame_rate="60/1")], "video_streams": [dict(stream, width=1920, avg_frame_rate="60/1")], "audio_streams": [], "format": {}, "duration": 1.0},
            ]
            vfr_cmd = FFmWiz.build_join_near_quality_command({"ffmpeg": "ffmpeg", "join_vfr": True}, items, base / "vfr.mkv")
            self.assertNotIn("fps=", " ".join(vfr_cmd))
            self.assertIn("-fps_mode", vfr_cmd)
            self.assertIn("vfr", vfr_cmd)

            cfr_cmd = FFmWiz.build_join_near_quality_command({"ffmpeg": "ffmpeg", "join_vfr": False, "fps": 30}, items, base / "cfr.mkv")
            self.assertIn("fps=", " ".join(cfr_cmd))
            self.assertNotIn("-fps_mode", cfr_cmd)

    def test_join_videos_near_quality_command_uses_concat_filter(self):
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
            cmd = FFmWiz.build_join_near_quality_command({"ffmpeg": "ffmpeg"}, items, base / "joined.mp4")
            self.assertIn("-filter_complex", cmd)
            self.assertIn("-c:v", cmd)
            self.assertIn("libx264", cmd)

    def test_join_encode_output_path_never_matches_any_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "a.mkv"
            second = base / "a.mp4"
            first.write_bytes(b"")
            second.write_bytes(b"")
            stream = {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "avg_frame_rate": "30/1", "color_range": "tv"}
            audio = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}
            answers = {
                "ffmpeg": "ffmpeg",
                "input_path": first,
                "output_path": second,
                "output_ext": "mp4",
                "color_range_choice": "tv",
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
            cmd = FFmWiz.build_join_encode_command(answers, items, second)
            self.assertNotEqual(Path(cmd[-1]).resolve(), second.resolve())
            self.assertIn("_Encode", Path(cmd[-1]).stem)

    def test_join_encode_normalizes_pts_before_concat(self):
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
                "output_path": base / "out.mp4",
                "output_ext": "mp4",
                "color_range_choice": "tv",
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
            self.assertIn("crop=", text) if answers.get("crop_enabled") else None
            self.assertIn("fps=4,scale=", text)
            self.assertIn("format=yuv420p,setpts=PTS-STARTPTS[jv0]", text)
            self.assertIn("aresample=48000:async=1:first_pts=0", text)

    def test_join_encode_split_points_generate_multiple_part_outputs(self):
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
                "color_range_choice": "tv",
                "video_codec": "H265",
                "use_gpu": True,
                "video_bitrate_kbps": 400,
                "resolution": FFmWiz.parse_resolution("480p"),
                "fps": 4,
                "audio_tracks": [0],
                "audio_codec": "aac",
                "audio_bitrate_kbps": 128,
                "separator_points": [0.75],
                "audio_speed_from_video": True,
                "video_speed_enabled": False,
                "video_speed_factor": 1.0,
                "format": {"duration": "2"},
                "video_streams": [stream],
                "audio_streams": [audio],
            }
            items = [
                {"path": first, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
                {"path": second, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
            ]
            text = " ".join(FFmWiz.build_join_encode_command(answers, items, base / "joined.mp4"))
            self.assertIn("joined_Part01.mp4", text)
            self.assertIn("joined_Part02.mp4", text)
            self.assertNotIn(":unsafe=1", text)
            self.assertNotIn("[jvcat]scale=", text)
            self.assertNotIn("[jvcat_norm]", text)
            self.assertNotIn("atempo=1", text)
            self.assertIn("[jvfinal]split=2[jvpart0_src][jvpart1_src]", text)
            self.assertIn("[jafinal0]asplit=2[japart0_0_src][japart1_0_src]", text)
            self.assertIn("-map [jvout0]", text)
            self.assertIn("-map [jaout0_0]", text)
            self.assertIn("-map [jvout1]", text)
            self.assertIn("-map [jaout1_0]", text)
            self.assertEqual(text.count("-hwaccel cuda -hwaccel_device 0 -i"), 2)
            self.assert_not_contains_any(text, ["-hwaccel_output_format cuda", "scale_cuda", "hwupload_cuda", "hwdownload"])

    def test_join_encode_cpu_path_does_not_add_cuda_decode(self):
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
                "use_gpu": False,
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
            self.assertNotIn("-hwaccel cuda", text)

    def test_gpu_crop_480p_fps_no_cuts_uses_cuda_fast_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            text = self.command_text(answers)
        self.assertIn("-hwaccel cuda", text)
        self.assertIn("-hwaccel_output_format cuda", text)
        self.assertIn("-c:v h264_cuvid", text)
        self.assertIn("-crop 172x189x428x1095", text)
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
        self.assertIn("-crop 172x189x428x1095", text)
        self.assertIn("-t 6074.221000", text)
        self.assertIn("scale_cuda=w=852:h=480", text)
        self.assertIn("hevc_nvenc", text)
        self.assert_not_contains_any(text, ["filter_complex", "trim", "atrim", "concat=n=1", "anull", "hwdownload", "hwupload_cuda"])

    def test_gpu_multi_cut_uses_cuda_decode_only_with_cpu_filter_complex(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["cut_keep_ranges"] = [(0.0, 2.0), (5.0, 8.0)]
            text = self.command_text(answers)
        self.assertIn("-hwaccel cuda -hwaccel_device 0 -i", text)
        self.assertIn("-filter_complex", text)
        self.assertIn("trim=start=", text)
        self.assertIn("hevc_nvenc", text)
        self.assert_not_contains_any(text, ["-hwaccel_output_format cuda", "scale_cuda", "pad_cuda", "hwupload_cuda", "hwdownload"])

    def test_copy_cut_no_chapter_overlap_keeps_source_chapters(self):
        answers = self.copy_cut_answers([self.chapter(0, 100, "Intro")], 300)
        plan = FFmWiz.analyze_copy_cut_chapter_plan(answers, [(0, 300)])
        self.assertEqual(plan["mode"], "copy")
        cmd = FFmWiz.build_copy_cut_range_command("ffmpeg", "-y", Path("input.mkv"), 0, 300, Path("out.mkv"), plan)
        self.assertIn("-map_chapters", cmd)
        self.assertEqual(cmd[cmd.index("-map_chapters") + 1], "0")

    def test_copy_cut_shifts_chapters_no_cut_passed_through(self):
        # "No chapter was cut through" is not "the clock did not move". A range
        # WAS removed here (100-200 s), so the chapter after it sits 100 s early
        # in the output -- and `-map_chapters 0` cannot say that. Across the
        # multi-range concat demuxer it does not even carry the chapters:
        # measured on a real 12 s source with chapters A 0-2 and B 8-10 cut to
        # [(0,4),(8,12)], the output came back with ZERO chapters, while the
        # metadata plan produced the right A 0-2 / B 4-6.
        answers = self.copy_cut_answers(
            [self.chapter(0, 50, "Intro"), self.chapter(250, 300, "Outro")], 300)
        plan = FFmWiz.analyze_copy_cut_chapter_plan(answers, [(0, 100), (200, 300)])
        self.assertEqual("metadata", plan["mode"])
        self.assertEqual(0, plan["overlap_count"], "neither chapter is cut through")
        self.assertEqual(
            [(0.0, 50.0, "Intro"), (150.0, 200.0, "Outro")],
            [(c["start"], c["end"], c["metadata"]["title"]) for c in plan["chapters"]])

    def test_copy_cut_with_nothing_removed_still_copies_source_chapters(self):
        # The other side of the same branch: no range removed means the clock
        # never moved, so `-map_chapters 0` is still the right answer.
        answers = self.copy_cut_answers([self.chapter(0, 100, "Intro")], 300)
        self.assertEqual(
            "copy", FFmWiz.analyze_copy_cut_chapter_plan(answers, [(0, 300)])["mode"])

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

    def test_copy_cut_back_from_method_returns_to_output_step(self):
        calls: list[str] = []
        output_calls = {"count": 0}
        method_calls = {"count": 0}
        # `modes._run_copy_cut_mode_impl` calls the step prompts through the
        # wizard facade, not through wizard_steps the way run_wizard does, so
        # the patch and the restore both have to go there.
        facade_originals = {
            "step_input_path": FFmWiz.wizard.step_input_path,
            "step_output_location": FFmWiz.wizard.step_output_location,
        }
        originals = {
            "ask_cut_method": FFmWiz.modes.ask_cut_method,
            "collect_cut_ranges_terminal": FFmWiz.services.collect_cut_ranges_terminal,
            "ask_continue_default_yes": FFmWiz.modes.ask_continue_default_yes,
            "print_cut_summary": FFmWiz.modes.print_cut_summary,
            "get_video_fps": FFmWiz.services.get_video_fps,
            "stream_duration_seconds": FFmWiz.services.stream_duration_seconds,
            "build_output_path": FFmWiz.services.build_output_path,
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
            FFmWiz.wizard.step_input_path = fake_input
            FFmWiz.wizard.step_output_location = fake_output
            FFmWiz.modes.ask_cut_method = fake_method
            FFmWiz.services.collect_cut_ranges_terminal = lambda _answers, fps, duration: calls.append("manual") or [(0.0, 10.0)]
            FFmWiz.modes.ask_continue_default_yes = lambda _answers: calls.append("confirm") or False
            FFmWiz.modes.print_cut_summary = lambda *args, **kwargs: calls.append("summary")
            FFmWiz.services.get_video_fps = lambda _answers: 25.0
            FFmWiz.services.stream_duration_seconds = lambda _stream, _fmt=None: 100.0
            FFmWiz.services.build_output_path = lambda _answers: Path("out.mkv")
            result = FFmWiz._run_copy_cut_mode_impl({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})
        finally:
            for name, original in originals.items():
                setattr(_home_module(name), name, original)
            for name, original in facade_originals.items():
                setattr(FFmWiz.wizard, name, original)

        self.assertIsNone(result)
        self.assertEqual(calls[:4], ["input", "output", "method", "output"])
        self.assertEqual(output_calls["count"], 2)

    def test_step_cuts_rejects_archived_gui_shortcut(self):
        prompts = iter(["g", "n"])
        errors: list[str] = []
        gui_calls = {"count": 0}
        originals = {
            "ask_raw": FFmWiz.appio.ask_raw,
            "open_cut_gui": FFmWiz.guibridge.open_cut_gui,
            "error": FFmWiz.appio.error,
            "get_video_fps": FFmWiz.services.get_video_fps,
            "stream_duration_seconds": FFmWiz.services.stream_duration_seconds,
        }

        def fake_open_cut_gui(_answers, fps, duration):
            gui_calls["count"] += 1
            return None

        try:
            FFmWiz.appio.ask_raw = lambda _prompt: next(prompts)
            FFmWiz.guibridge.open_cut_gui = fake_open_cut_gui
            FFmWiz.appio.error = lambda message: errors.append(message)
            FFmWiz.services.get_video_fps = lambda _answers: 25.0
            FFmWiz.services.stream_duration_seconds = lambda _stream, _fmt=None: 100.0
            answers = {"format": {"duration": "100"}}
            FFmWiz.wizard.step_cuts(answers)
        finally:
            for name, original in originals.items():
                setattr(_home_module(name), name, original)

        self.assertEqual(gui_calls["count"], 0)
        self.assertNotIn("cut_keep_ranges", answers)
        self.assertIn("archived", errors[-1])

    def test_unified_decline_disables_followup_video_speed_gui(self):
        prompts = iter(["g", "n"])
        errors: list[str] = []
        gui_calls = {"count": 0}
        originals = {
            "ask_raw": FFmWiz.appio.ask_raw,
            "open_video_speed_gui": FFmWiz.guibridge.open_video_speed_gui,
            "error": FFmWiz.appio.error,
        }
        try:
            FFmWiz.appio.ask_raw = lambda _prompt: next(prompts)
            FFmWiz.guibridge.open_video_speed_gui = lambda _answers: gui_calls.__setitem__("count", gui_calls["count"] + 1)
            FFmWiz.appio.error = lambda message: errors.append(message)
            answers = {
                "_disable_followup_video_gui_prompts": True,
                "video_streams": [{"codec_type": "video"}],
                "audio_streams": [],
            }
            FFmWiz.wizard.step_video_speed_reverse_for_encode(answers)
        finally:
            for name, original in originals.items():
                setattr(_home_module(name), name, original)
        self.assertEqual(gui_calls["count"], 0)
        self.assertFalse(answers["video_speed_enabled"])
        self.assertIn("not available", errors[-1])


    def test_join_near_quality_nvenc_requires_a_real_gpu_not_just_the_encoder_list(self):
        # `video_encoders` is the BUILD's capability list: every full FFmpeg
        # build advertises h264_nvenc whether or not the machine has an NVIDIA
        # card. Gating on it alone emitted `-c:v h264_nvenc` on a GPU-less box,
        # which dies at encoder init. Both the encoder choice and the NVENC
        # multipass question must consult the device probe.
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
                {"path": first, "streams": [stream], "video_streams": [stream],
                 "audio_streams": [], "format": {}, "duration": 1.0},
                {"path": second, "streams": [dict(stream, width=1920)],
                 "video_streams": [dict(stream, width=1920)],
                 "audio_streams": [], "format": {}, "duration": 1.0},
            ]
            encoders = ["h264_nvenc", "hevc_nvenc", "libx264"]

            no_gpu = FFmWiz.build_join_near_quality_command(
                {"ffmpeg": "ffmpeg", "video_encoders": encoders, "gpu_available": False},
                items, base / "no_gpu.mp4")
            self.assertNotIn("h264_nvenc", no_gpu)
            self.assertIn("libx264", no_gpu)

            with_gpu = FFmWiz.build_join_near_quality_command(
                {"ffmpeg": "ffmpeg", "video_encoders": encoders, "gpu_available": True},
                items, base / "with_gpu.mp4")
            self.assertIn("h264_nvenc", with_gpu)

            # The device probe is the only thing that may decide this, and it
            # must not run before the encoder is even a candidate: a build with
            # no NVENC at all never pays for the probe.
            probed = {"count": 0}

            def counting_probe(answers):
                probed["count"] += 1
                return True

            with mock.patch.object(FFmWiz.modes_join, "gpu_available_for_answers", counting_probe):
                cpu_only = FFmWiz.build_join_near_quality_command(
                    {"ffmpeg": "ffmpeg", "video_encoders": ["libx264"]},
                    items, base / "cpu_only.mp4")
            self.assertNotIn("h264_nvenc", cpu_only)
            self.assertEqual(probed["count"], 0)


if __name__ == "__main__":
    unittest.main()
