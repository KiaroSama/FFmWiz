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
        originals = {
            "step_input_path": FFmWiz.wizard.step_input_path,
            "step_output_location": FFmWiz.wizard.step_output_location,
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

    def test_run_wizard_unified_decline_skips_legacy_crop_speed_and_cut_questions(self):
        class StopRun(Exception):
            pass

        calls: list[str] = []
        originals = {
            "step_input_path": FFmWiz.wizard.step_input_path,
            "step_join_additional_inputs_for_encode": FFmWiz.wizard.step_join_additional_inputs_for_encode,
            "step_output_location": FFmWiz.wizard.step_output_location,
            "step_output_format": FFmWiz.wizard.step_output_format,
            "step_video_codec": FFmWiz.wizard.step_video_codec,
            "step_use_gpu": FFmWiz.wizard.step_use_gpu,
            "step_unified_video_editor_for_encode": FFmWiz.wizard.step_unified_video_editor_for_encode,
            "step_crop_enabled": FFmWiz.wizard.step_crop_enabled,
            "step_video_bitrate": FFmWiz.wizard.step_video_bitrate,
            "step_cpu_two_pass": FFmWiz.wizard.step_cpu_two_pass,
            "step_resolution": FFmWiz.wizard.step_resolution,
            "step_fps": FFmWiz.wizard.step_fps,
            "step_video_speed_reverse_for_encode": FFmWiz.wizard.step_video_speed_reverse_for_encode,
            "step_cuts": FFmWiz.wizard.step_cuts,
            "step_start_now": FFmWiz.wizard.step_start_now,
        }
        try:
            FFmWiz.wizard.step_input_path = lambda answers: calls.append("input")
            FFmWiz.wizard.step_join_additional_inputs_for_encode = lambda answers: calls.append("join")
            FFmWiz.wizard.step_output_location = lambda answers: calls.append("output")
            FFmWiz.wizard.step_output_format = lambda answers: calls.append("format")
            FFmWiz.wizard.step_video_codec = lambda answers: calls.append("codec")
            FFmWiz.wizard.step_use_gpu = lambda answers: (calls.append("gpu"), answers.__setitem__("use_gpu", False))
            FFmWiz.wizard.step_unified_video_editor_for_encode = lambda answers: (
                calls.append("unified"),
                answers.__setitem__("_unified_video_editor_used", False),
                answers.__setitem__("_unified_video_editor_declined", True),
                answers.__setitem__("crop_enabled", False),
                answers.__setitem__("video_speed_enabled", False),
                answers.__setitem__("cut_keep_ranges", []),
            )
            FFmWiz.wizard.step_crop_enabled = lambda answers: calls.append("crop")
            FFmWiz.wizard.step_video_bitrate = lambda answers: calls.append("bitrate")
            FFmWiz.wizard.step_cpu_two_pass = lambda answers: calls.append("two_pass")
            FFmWiz.wizard.step_resolution = lambda answers: calls.append("resolution")
            FFmWiz.wizard.step_fps = lambda answers: calls.append("fps")
            FFmWiz.wizard.step_video_speed_reverse_for_encode = lambda answers: calls.append("speed")
            FFmWiz.wizard.step_cuts = lambda answers: calls.append("cuts")
            FFmWiz.wizard.step_start_now = lambda answers: (_ for _ in ()).throw(StopRun())
            with self.assertRaises(StopRun):
                FFmWiz.run_wizard({
                    "output_ext": "mp4",
                    "video_codec": "H265",
                    "video_streams": [{"codec_type": "video", "width": 100, "height": 100, "color_range": "tv"}],
                    "audio_streams": [],
                    "subtitle_streams": [],
                    "format": {"duration": "100"},
                })
        finally:
            for name, original in originals.items():
                setattr(_home_module(name), name, original)
        self.assertIn("unified", calls)
        self.assertNotIn("crop", calls)
        self.assertNotIn("speed", calls)
        self.assertNotIn("cuts", calls)

    def test_run_wizard_question_numbers_continue_after_join_subquestions(self):
        class StopRun(Exception):
            pass

        seen_numbers: list[int] = []
        originals = {
            "step_input_path": FFmWiz.wizard.step_input_path,
            "step_join_additional_inputs_for_encode": FFmWiz.wizard.step_join_additional_inputs_for_encode,
            "step_output_location": FFmWiz.wizard.step_output_location,
        }

        def fake_input(answers):
            answers.update({
                "input_path": Path("a.mkv"),
                "output_ext": "mp4",
                "video_streams": [{"codec_type": "video", "width": 100, "height": 100}],
                "audio_streams": [],
                "subtitle_streams": [],
                "format": {"duration": "10"},
            })

        def fake_join(answers):
            self.assertEqual(answers["_question_number"], 3)
            answers["_join_question_extra"] = 4

        def fake_output(answers):
            seen_numbers.append(int(answers["_question_number"]))
            raise StopRun()

        try:
            FFmWiz.wizard.step_input_path = fake_input
            FFmWiz.wizard.step_join_additional_inputs_for_encode = fake_join
            FFmWiz.wizard.step_output_location = fake_output
            with self.assertRaises(StopRun):
                FFmWiz.run_wizard({"_question_offset": 1})
        finally:
            for name, original in originals.items():
                setattr(_home_module(name), name, original)

        self.assertEqual(seen_numbers, [8])

    def test_step_join_back_resume_preserves_existing_join_items_and_number_extra(self):
        originals = {
            "ask_join_add_another": FFmWiz.wizard.ask_join_add_another,
            "ask_required": FFmWiz.appio.ask_required,
        }
        item = {
            "path": Path("b.mkv"),
            "streams": [],
            "video_streams": [{"codec_type": "video"}],
            "audio_streams": [],
            "format": {},
            "duration": 10.0,
        }
        prompts: list[str] = []

        def fake_add_another(prompt, allow_folder=True):
            prompts.append(prompt)
            return False

        try:
            FFmWiz.wizard.ask_join_add_another = fake_add_another
            FFmWiz.appio.ask_required = lambda _prompt: (_ for _ in ()).throw(AssertionError("path prompt should not be shown"))
            answers = {
                "_question_number": 5,
                "_join_question_extra": 2,
                "input_path": Path("a.mkv"),
                "video_streams": [{"codec_type": "video"}],
                "join_input_items": [item],
            }
            FFmWiz.wizard.step_join_additional_inputs_for_encode(answers)
        finally:
            for name, original in originals.items():
                setattr(_home_module(name), name, original)

        self.assertEqual(answers["join_input_items"], [item])
        self.assertEqual(answers["_join_question_extra"], 2)
        self.assertIn("5. Add another video file?", prompts[0])
        self.assertIn("f=join all videos in folder", prompts[0])

    def test_step_join_folder_option_and_b_undo(self):
        # Verify the join collection: 'b' at the file prompt undoes the previous
        # file and re-asks it, and 'folder' adds every video in a folder sorted
        # by name. Uses real temp files; join_load_media_item is stubbed so no
        # real ffprobe is needed.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "in.mkv").write_bytes(b"x")
            for name in ("a.mov", "b.mov", "c.mov"):
                (root / name).write_bytes(b"x")
            vids = root / "vids"
            vids.mkdir()
            # Intentionally out of order to prove name sorting.
            for name in ("03_three.mp4", "01_one.mp4", "02_two.mp4"):
                (vids / name).write_bytes(b"x")

            original = FFmWiz.services.join_load_media_item
            FFmWiz.services.join_load_media_item = lambda answers, path, allow_audio_only=False: {
                "path": Path(path), "probe": {}, "format": {}, "streams": [],
                "video_streams": [{"codec_type": "video"}], "audio_streams": [],
                "subtitle_streams": [], "attachment_streams": [], "data_streams": [],
                "duration": 10.0,
            }
            try:
                # add a.mov; add b.mov; say yes; at the FILE prompt type 'b' to
                # undo b.mov; re-enter c.mov; then 'folder' adds the 3 sorted
                # videos; then no.
                script = iter([
                    "y", str(root / "a.mov"),
                    "y", str(root / "b.mov"),
                    "y", "b", str(root / "c.mov"),
                    "f", str(vids),
                    "n",
                ])
                answers = {
                    "ffprobe": "ffprobe",
                    "input_path": root / "in.mkv",
                    "video_streams": [{"codec_type": "video"}],
                    "_question_number": 3,
                }
                with mock.patch("builtins.input", lambda _prompt="": next(script)):
                    FFmWiz.wizard.step_join_additional_inputs_for_encode(answers)
            finally:
                FFmWiz.services.join_load_media_item = original

            names = [Path(it["path"]).name for it in answers.get("join_input_items", [])]
            self.assertEqual(
                names,
                ["a.mov", "c.mov", "01_one.mp4", "02_two.mp4", "03_three.mp4"],
            )

    def test_graphical_video_requests_include_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            chapters = [self.chapter(10.0, 20.0, "Opening")]
            answers["probe"] = {"chapters": chapters}
            captured: list[dict] = []

            def fake_launch(request):
                captured.append(request)
                if request["mode"] == "video_unified":
                    return {
                        "status": "ok",
                        "margins": [0, 0, 0, 0],
                        "keep_ranges": [],
                        "speed": 1.0,
                        "reverse": False,
                        "include_audio": True,
                    }
                return {"status": "ok", "keep_ranges": [[0.0, 10.0]]}

            with mock.patch.object(FFmWiz.guibridge, "_launch_qt_gui", side_effect=fake_launch):
                self.assertIsNotNone(FFmWiz.guibridge.open_unified_video_gui(answers))
                self.assertEqual(captured[-1]["chapters"], chapters)
                self.assertEqual(FFmWiz.guibridge.open_cut_gui(answers, fps=30.0, duration=100.0), [(0.0, 10.0)])
                self.assertEqual(captured[-1]["chapters"], chapters)

    def test_atempo_filter_chain_splits_extreme_speed(self):
        self.assertEqual(FFmWiz.atempo_filter_chain(4.0), "atempo=2,atempo=2")
        self.assertEqual(FFmWiz.atempo_filter_chain(0.25), "atempo=0.5,atempo=0.5")

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
        self.assertIn("-hwaccel cuda -hwaccel_device 0 -i", text)
        self.assertNotIn("scale_cuda", text)
        self.assertNotIn("-hwaccel_output_format cuda", text)

    def test_split_into_two_parts_remaps_chapters_per_part(self):
        """Split into 2 parts: each part gets only its own remapped chapters at relative time zero."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["probe"] = {"chapters": [
                self.chapter(0, 200, "Chapter 1"),
                self.chapter(200, 400, "Chapter 2"),
                self.chapter(400, 600, "Chapter 3"),
            ]}
            answers["format"] = {"duration": "600.0"}
            answers["separator_points"] = [300.0]
            answers["cut_keep_ranges"] = []
            answers.pop("video_speed_enabled", None)
            answers.pop("reverse_video", None)
            # Part 1: [0, 300]
            plan1 = FFmWiz.remap_chapters_for_encode(answers, speed_factor=1.0, part_interval=(0.0, 300.0))
            self.assertEqual(plan1["mode"], "metadata")
            ch1_titles = [ch["metadata"].get("title") for ch in plan1["chapters"]]
            self.assertIn("Chapter 1", ch1_titles)
            self.assertIn("Chapter 2", ch1_titles)
            self.assertNotIn("Chapter 3", ch1_titles)
            # Part 1 chapters start at zero-relative time
            self.assertAlmostEqual(plan1["chapters"][0]["start"], 0.0, places=2)
            self.assertAlmostEqual(plan1["chapters"][0]["end"], 200.0, places=2)
            # Part 2: [300, 600]
            plan2 = FFmWiz.remap_chapters_for_encode(answers, speed_factor=1.0, part_interval=(300.0, 600.0))
            self.assertEqual(plan2["mode"], "metadata")
            ch2_titles = [ch["metadata"].get("title") for ch in plan2["chapters"]]
            self.assertNotIn("Chapter 1", ch2_titles)
            self.assertIn("Chapter 3", ch2_titles)
            # Part 2 chapter timestamps start at zero (relative to Part 2 start)
            for ch in plan2["chapters"]:
                self.assertGreaterEqual(ch["start"], 0.0)

    def test_timeline_modified_with_unavailable_chapters_uses_map_chapters_minus_one(self):
        """When timeline is modified and no chapters exist in source, command must contain -map_chapters -1."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["probe"] = {"chapters": []}
            answers["format"] = {"duration": "600.0"}
            answers["cut_keep_ranges"] = [(0, 300)]
            text = self.command_text(answers)
            self.assertIn("-map_chapters -1", text)
            self.assertNotIn("-map_chapters 0", text)

    def test_unmodified_timeline_preserves_chapters(self):
        """When no timeline modification, command uses -map_chapters 0."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.pop("cut_keep_ranges", None)
            answers.pop("separator_points", None)
            answers.pop("video_speed_enabled", None)
            answers.pop("reverse_video", None)
            answers.pop("join_input_items", None)
            answers["probe"] = {"chapters": [self.chapter(0, 100, "Test")]}
            answers["format"] = {"duration": "600.0"}
            text = self.command_text(answers)
            self.assertIn("-map_chapters 0", text)

    # ===================================================================
    # Comprehensive resize / aspect-ratio tests for Issue 2 final fix
    # ===================================================================

    def test_exact_failing_workflow_multi_cut_crop_split_uses_ar_safe_scale(self):
        """Test 1: Exact reported failing workflow — multi-range trim + concat +
        crop + 480p box target + Split into 2 Parts + NVENC. Must produce
        force_original_aspect_ratio + pad, NOT plain scale=WxH,setsar=1."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "video_streams": [{"codec_type": "video", "codec_name": "h264",
                                   "width": 2876, "height": 1442, "avg_frame_rate": "30/1", "color_range": "tv"}],
                "crop_enabled": True,
                "crop_left": 420, "crop_right": 736,
                "crop_top": 179, "crop_bottom": 184,
                "fps": 4,
                "resolution": FFmWiz.parse_resolution("1018x480"),
                "use_gpu": True,
                "video_codec": "H265",
                "separator_points": [3000.0],
                "cut_keep_ranges": [(10, 4000), (5000, 7000)],
                "format": {"duration": "8000.0"},
                "audio_speed_from_video": False,
            })
            text = self.command_text(answers)
            # Must contain AR-safe resize
            self.assertIn("force_original_aspect_ratio=decrease", text)
            self.assertIn("pad=1018:480", text)
            # Must NOT contain plain stretch scale
            self.assertNotIn("scale=1018:480,setsar=1", text)
            # Must still use expected settings (top normalized 179->178 for 4:2:0 origin)
            self.assertIn("crop=iw-420-736:ih-178-184:420:178", text)
            self.assertIn("fps=4", text)
            self.assertIn("hevc_nvenc", text)
            self.assertIn("-map_chapters -1", text)
            # Must produce two split output parts
            self.assertIn("_Part01", text)
            self.assertIn("_Part02", text)
            # Cleanup
            FFmWiz.cleanup_encode_chapter_metadata(answers)

    def test_folder_batch_menu_corrected_wording(self):
        """The Folder Encode batch menu uses the corrected option-2 wording and
        a 0=back token (no separate printed option list)."""
        answers = {
            "video_codec": "H265", "use_gpu": False,
            "video_streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
            "_folder_items": [
                {"path": Path("a.mkv"), "answers": {"video_streams": [{"codec_type": "video"}]}},
            ],
        }
        captured = {}

        def fake_ask(prompt):
            captured["prompt"] = prompt
            return "2"

        buf = io.StringIO()
        with mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=fake_ask), \
                contextlib.redirect_stdout(buf):
            FFmWiz.step_folder_batch_color_range(answers)
        out = buf.getvalue()
        prompt = captured["prompt"]
        # Prompt presents the choices and uses 0=back (b is not used here).
        self.assertIn("2=Do not force a range", prompt)
        self.assertIn("back=0", prompt)
        self.assertNotIn("back=b", prompt)
        # The confirmation note uses the corrected wording; old wording is gone.
        self.assertIn("Do not force a range in FFmWiz", out)
        self.assertNotIn("Keep unknown files unspecified", out)
        self.assertNotIn("Keep unspecified", out)
        self.assertEqual(answers["_batch_color_range_policy"], "unspecified")

    def test_workflow_folder_encode_provenance_independent(self):
        """Folder Encode resolves each file independently; provenance never leaks."""
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.base_answers(tmp)
            settings["use_gpu"] = False
            settings["crop_enabled"] = False
            settings["folder_output_location"] = Path(tmp)
            s1 = {"codec_type": "video", "codec_name": "h264", "width": 2160,
                  "height": 3840, "display_aspect_ratio": "9:16"}
            s2 = {"codec_type": "video", "codec_name": "h264", "width": 2160, "height": 3840}
            audio = {"codec_type": "audio", "codec_name": "aac"}
            item1 = {"path": Path(tmp) / "a.mkv",
                     "answers": {"input_path": Path(tmp) / "a.mkv", "video_streams": [s1],
                                 "audio_streams": [audio], "format": {"duration": "10"}}}
            item2 = {"path": Path(tmp) / "b.mkv",
                     "answers": {"input_path": Path(tmp) / "b.mkv", "video_streams": [s2],
                                 "audio_streams": [audio], "format": {"duration": "10"}}}
            job1 = FFmWiz.prepare_folder_job_answers(settings, item1)
            job2 = FFmWiz.prepare_folder_job_answers(settings, item2)
            i1 = FFmWiz.sar_dar_info(job1)
            i2 = FFmWiz.sar_dar_info(job2)
            self.assertEqual(i1["sar_source"], "calculated from coded resolution and detected DAR")
            self.assertEqual(i1["dar_source"], "detected by ffprobe")
            self.assertFalse(i1["fallback_used"])
            self.assertEqual(i2["sar_source"], "fallback assumption")
            self.assertTrue(i2["fallback_used"])
            # Same numerical DAR, different provenance.
            self.assertAlmostEqual(i1["effective_dar_decimal"], i2["effective_dar_decimal"], places=6)
            # Original raw streams immutable; no leakage.
            self._assert_raw_immutable(s1, None, "9:16")
            self._assert_raw_immutable(s2, None, None)
            self._assert_detected_label_only_when_raw_valid(i1)
            self._assert_detected_label_only_when_raw_valid(i2)

    def test_workflow_split_reuses_resolved_geometry(self):
        """Split Parts reuse the same resolved geometry/provenance per workflow."""
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["use_gpu"] = False
            answers["crop_enabled"] = False
            answers["color_range_choice"] = "tv"
            answers["video_streams"] = [{"codec_type": "video", "codec_name": "h264",
                                         "width": 2160, "height": 3840, "display_aspect_ratio": "9:16"}]
            answers["resolution"] = "n"
            answers["separator_points"] = [3.0, 6.0]
            answers["format"] = {"duration": "10.0"}
            text = self.command_text(answers)
            # The resolver is pure: every Part sees the same provenance.
            i = FFmWiz.sar_dar_info(answers)
            self.assertEqual(i["sar_source"], "calculated from coded resolution and detected DAR")
            self.assertEqual(i["dar_source"], "detected by ffprobe")
            self.assertFalse(i["fallback_used"])
            self.assertGreater(text.count("-c:v libx265"), 1)  # multiple Parts
            self._assert_raw_immutable(answers["video_streams"][0], None, "9:16")
            # Fallback variant stays fallback for every Part.
            answers["video_streams"] = [{"codec_type": "video", "codec_name": "h264",
                                         "width": 2160, "height": 3840}]
            i2 = FFmWiz.sar_dar_info(answers)
            self.assertTrue(i2["fallback_used"])
            self.assertEqual(i2["sar_source"], "fallback assumption")


if __name__ == "__main__":
    unittest.main()
