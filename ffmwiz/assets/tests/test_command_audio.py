"""Split from the command-generation suite (see command_gen_base.py)."""
import tempfile
from pathlib import Path
import FFmWiz
from command_gen_base import CommandGenBase


class CommandAudioTests(CommandGenBase):
    def test_parse_volumedetect_output(self):
        text = """
        [Parsed_volumedetect_0 @ 000001] mean_volume: -23.4 dB
        [Parsed_volumedetect_0 @ 000001] max_volume: -1.2 dB
        """
        self.assertEqual(
            FFmWiz.parse_volumedetect_output(text),
            {"mean_volume": "-23.4 dB", "max_volume": "-1.2 dB"},
        )

    def test_reencoded_outputs_clear_statistics_for_all_audio_and_subtitle_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["output_ext"] = "mkv"
            answers["keep_source_metadata"] = True
            answers["audio_codec"] = "libopus"
            answers["audio_bitrate_kbps"] = 160
            answers["audio_tracks"] = "all"
            answers["audio_streams"] = [
                {"codec_type": "audio", "codec_name": "aac", "duration": "60", "index": 1},
                {"codec_type": "audio", "codec_name": "aac", "duration": "60", "index": 2},
                {"codec_type": "audio", "codec_name": "aac", "duration": "60", "index": 3},
            ]
            answers["subtitle_tracks"] = "all"
            answers["subtitle_streams"] = [
                {"codec_type": "subtitle", "codec_name": "ass", "index": 4},
                {"codec_type": "subtitle", "codec_name": "subrip", "index": 5},
            ]
            text = self.command_text(answers)
        for spec in ("a", "s"):
            self.assertIn(f"-metadata:s:{spec} BPS=", text)
            self.assertIn(f"-metadata:s:{spec} NUMBER_OF_BYTES=", text)
        self.assertNotIn("-metadata:s:a:0 BPS=", text)
        self.assertNotIn("-metadata:s:s:0 BPS=", text)

    def test_parse_loudnorm_measurement_output(self):
        text = """
        [Parsed_loudnorm_0 @ 000001]
        {
          "input_i" : "-23.40",
          "input_tp" : "-5.10",
          "input_lra" : "4.20",
          "input_thresh" : "-33.90",
          "target_offset" : "-0.30"
        }
        """
        self.assertEqual(
            FFmWiz.parse_loudnorm_measurement_output(text),
            {
                "input_i": -23.4,
                "input_tp": -5.1,
                "input_lra": 4.2,
                "input_thresh": -33.9,
                "target_offset": -0.3,
            },
        )

    def test_progress_target_mux_bitrate_uses_first_video_and_audio_bitrates(self):
        cmd = [
            "ffmpeg",
            "-i",
            "in.mkv",
            "-b:v:0",
            "1650k",
            "-b:a",
            "160k",
            "out.mkv",
            "-b:v:0",
            "1650k",
            "-b:a",
            "160k",
            "out2.mkv",
        ]
        self.assertAlmostEqual(FFmWiz._progress_target_mux_bitrate_kbps_from_command(cmd), 1810.0)

    def test_loudnorm_filter_falls_back_to_single_pass(self):
        filt = FFmWiz.build_loudnorm_filter({"loudnorm_enabled": True, "loudnorm_target_i": -18})
        self.assertEqual(filt, "loudnorm=I=-18:TP=-1.5:LRA=11:print_format=summary")

    def test_media_info_report_includes_audio_volume_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.mkv"
            input_path.write_bytes(b"dummy")
            payload = {
                "format": {"duration": "10", "size": str(len(b"dummy"))},
                "streams": [
                    {"codec_type": "audio", "codec_name": "aac", "index": 1},
                ],
            }
            lines = FFmWiz.build_media_info_report_lines(
                input_path,
                payload,
                "",
                Path(tmp) / "input_info.txt",
                {0: {"mean_volume": "-20.0 dB", "max_volume": "-1.0 dB"}},
            )
            plain = FFmWiz.render_info_report(lines, color=False)
            self.assertIn("mean / max volume: -20.0 / -1.0 dB", plain)

    def test_join_videos_near_quality_pads_missing_audio_with_silence(self):
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
            audio = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}
            items = [
                {"path": first, "streams": [stream, audio], "video_streams": [stream], "audio_streams": [audio], "format": {}, "duration": 1.0},
                {"path": second, "streams": [dict(stream)], "video_streams": [dict(stream)], "audio_streams": [], "format": {}, "duration": 1.0},
            ]
            cmd = FFmWiz.build_join_near_quality_command({"ffmpeg": "ffmpeg"}, items, base / "joined.mp4")
            text = " ".join(cmd)
            self.assertIn("anullsrc=channel_layout=stereo", text)
            self.assertIn("concat=n=2:v=1:a=1", text)
            self.assertIn("-map [a]", text)
            self.assertNotIn("-an", cmd)

    def test_run_mode_steps_back_skips_auto_single_audio_track_step(self):
        calls: list[str] = []
        output_calls = {"count": 0}

        def fake_input(answers):
            calls.append("input")
            answers["audio_streams"] = [{"codec_type": "audio"}]

        def fake_audio_track(answers):
            calls.append("audio_auto")
            answers["audio_index"] = 0

        def fake_output(_answers):
            calls.append("output")
            output_calls["count"] += 1
            if output_calls["count"] == 1:
                raise FFmWiz.Back()

        FFmWiz.run_mode_steps(
            {"_question_offset": 0, "audio_streams": [{"codec_type": "audio"}]},
            [
                FFmWiz.wizard.Step("input_path", lambda a: True, fake_input),
                FFmWiz.wizard.Step("audio_track", lambda a: True, fake_audio_track),
                FFmWiz.wizard.Step("output_location", lambda a: True, fake_output),
            ],
        )
        self.assertEqual(calls, ["input", "audio_auto", "output", "input", "audio_auto", "output"])

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

    def test_main_encode_video_speed_from_gui_respects_selected_audio_tracks(self):
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
        self.assertIn("[0:a:0]areverse,atempo=1.25,asetpts=PTS-STARTPTS[aout0]", text)
        self.assertNotIn("[0:a:1]areverse,atempo=1.25,asetpts=PTS-STARTPTS[aout1]", text)
        self.assertIn("-map [aout0]", text)
        self.assertNotIn("-map [aout1]", text)

    def test_split_encode_respects_selected_audio_tracks_when_syncing_video_speed(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["audio_streams"].extend([
                {"codec_type": "audio", "codec_name": "aac", "bit_rate": "122000", "duration": "6074.221"},
                {"codec_type": "audio", "codec_name": "aac", "bit_rate": "2000", "duration": "6074.221"},
            ])
            answers.update({
                "audio_tracks": [0],
                "audio_speed_from_video": True,
                "separator_points": [3000.0],
                "video_speed_enabled": True,
                "video_speed_factor": 1.0,
            })
            text = self.command_text(answers)
        self.assertIn("-map [saout0_0]", text)
        self.assertIn("-map [saout1_0]", text)
        self.assertNotIn("[afinal1]asplit", text)
        self.assertNotIn("[afinal2]asplit", text)
        self.assertNotIn("-map [saout0_1]", text)
        self.assertNotIn("-map [saout0_2]", text)

    def test_split_encode_inserts_loudnorm_for_selected_audio_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["audio_streams"].append({
                "codec_type": "audio",
                "codec_name": "aac",
                "bit_rate": "122000",
                "duration": "6074.221",
            })
            answers.update({
                "audio_tracks": [0],
                "separator_points": [3000.0],
                "loudnorm_enabled": True,
                "loudnorm_target_i": -16.0,
                "loudnorm_measured": {
                    "input_i": -8.54,
                    "input_tp": 1.74,
                    "input_lra": 7.6,
                    "input_thresh": -19.29,
                    "target_offset": -1.22,
                },
            })
            text = self.command_text(answers)
        self.assertIn("loudnorm=I=-16:TP=-1.5:LRA=11", text)
        self.assertIn("[afinal0]asplit", text)
        self.assertNotIn("[afinal1]asplit", text)

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
        self.assertIn("[0:a:0]asplit=2[acut0_src0][acut0_src1]", text)
        self.assertIn("[acut0_src0]atrim=start=0.000000:end=2.000000", text)
        self.assertNotIn("[0:a:0]atrim=start=0.000000:end=2.000000", text)
        self.assertIn("concat=n=2:v=0:a=1[acut0]", text)
        self.assertIn("[acut0]areverse,atempo=0.5,asetpts=PTS-STARTPTS[aout0]", text)
        self.assertIn("-map [aout0]", text)

    def test_main_encode_loudnorm_forces_audio_filter_and_aac_when_copy_was_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "audio_codec": "copy",
                "loudnorm_enabled": True,
                "loudnorm_target_i": -16.0,
                "loudnorm_measured": {
                    "input_i": -23.4,
                    "input_tp": -5.1,
                    "input_lra": 4.2,
                    "input_thresh": -33.9,
                    "target_offset": -0.3,
                },
            })
            text = self.command_text(answers)
        self.assertIn("loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=-23.4", text)
        self.assertIn("-c:a aac", text)
        self.assertNotIn("-c:a copy", text)

    def test_main_encode_normalizes_opus_audio_alias_to_libopus(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["audio_codec"] = "opus"
            text = self.command_text(answers)
        self.assertIn("-c:a libopus", text)
        self.assertNotIn("-c:a opus", text)
        self.assertEqual(answers["audio_codec"], "libopus")

    def test_main_encode_libopus_keeps_requested_audio_bitrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers["audio_codec"] = "opus"
            answers["audio_bitrate_kbps"] = 160
            text = self.command_text(answers)
        self.assertIn("-c:a libopus", text)
        self.assertIn("-b:a 160k", text)

    def test_audio_track_selection_n_means_all_tracks(self):
        self.assertEqual(FFmWiz.parse_selection_config("n", 3, [0]), "all")
        self.assertEqual(
            FFmWiz.selected_audio_streams({
                "audio_tracks": "all",
                "audio_streams": [{}, {}, {}],
            }),
            [0, 1, 2],
        )

    def test_split_encode_normalizes_opus_audio_alias_to_libopus(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "audio_codec": "opus",
                "separator_points": [120.0],
            })
            text = self.command_text(answers)
        self.assertIn("-c:a libopus", text)
        self.assertNotIn("-c:a opus", text)
        self.assertEqual(answers["audio_codec"], "libopus")

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

    def test_audio_transform_command_combines_cut_and_speed(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            answers.update({
                "output_location": Path(tmp),
                "audio_index": 0,
                "audio_cut_keep_ranges": [(0.0, 2.0), (4.0, 6.0)],
                "audio_speed_enabled": True,
                "audio_speed_factor": 0.5,
                "reverse_audio": True,
            })
            cmd = FFmWiz.build_audio_transform_command(answers)
            text = " ".join(cmd)
        self.assertIn("atrim=start=0.000000:end=2.000000", text)
        self.assertIn("concat=n=2:v=0:a=1", text)
        self.assertIn("areverse,atempo=0.5,asetpts=PTS-STARTPTS", text)
        self.assertIn("-map [aout0]", text)
        self.assertIn("-c:a aac", text)

    def test_audio_only_transform_prompts_do_not_show_for_video_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = self.base_answers(tmp)
            self.assertFalse(FFmWiz.audio_only_transform_prompt_applicable(answers))
            answers["output_ext"] = "mp3"
            self.assertTrue(FFmWiz.audio_only_transform_prompt_applicable(answers))
            answers["audio_tracks"] = []
            self.assertFalse(FFmWiz.audio_only_transform_prompt_applicable(answers))

    # ===================================================================
    # LoudNorm linear/dynamic and crop even-dimension tests
    # ===================================================================

    def test_loudnorm_linear_feasible(self):
        """Linear mode feasible: gain does not exceed target TP."""
        answers = {
            "loudnorm_enabled": True,
            "loudnorm_target_i": -16.0,
            "loudnorm_measured": {
                "input_i": -17.0,
                "input_tp": -3.0,
                "input_lra": 6.0,
                "input_thresh": -27.5,
                "target_offset": -0.2,
            },
        }
        # gain = +1 dB, predicted_TP = -3.0 + 1 = -2.0 <= -1.5 → linear
        filt = FFmWiz.build_loudnorm_filter(answers)
        self.assertIn("linear=true", filt)

    def test_loudnorm_aresample_after_filter(self):
        """LoudNorm processing chain includes aresample=48000 after loudnorm."""
        answers = {
            "loudnorm_enabled": True,
            "loudnorm_target_i": -16.0,
            "audio_speed_from_video": False,
            "video_speed_enabled": False,
        }
        chain = FFmWiz.build_encode_audio_processing_filter(answers)
        self.assertIn("loudnorm=", chain)
        self.assertIn("aresample=48000", chain)
        # aresample must come after loudnorm but before asetpts
        loudnorm_pos = chain.index("loudnorm=")
        aresample_pos = chain.index("aresample=48000")
        asetpts_pos = chain.index("asetpts=")
        self.assertLess(loudnorm_pos, aresample_pos)
        self.assertLess(aresample_pos, asetpts_pos)


if __name__ == "__main__":
    unittest.main()
