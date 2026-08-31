"""Split from the command-generation suite (see command_gen_base.py)."""
import contextlib
import io
import tempfile
from pathlib import Path
from unittest import mock
import FFmWiz
# `run_wizard` reaches these prompts through their DEFINING module now that the
# wizard facade is no longer imported by its own leaves, so a patch on the
# facade would rebind an attribute nothing reads.
from ffmwiz import (wizard_b, wizard_flow_b, wizard_quick, wizard_raw,  # noqa: E402
                    wizard_steps)
from command_gen_base import CommandGenBase, _home_module


class CommandCutJoinFolderTests2(CommandGenBase):
    def test_run_wizard_unified_decline_skips_legacy_crop_speed_and_cut_questions(self):
        class StopRun(Exception):
            pass

        calls: list[str] = []
        # Declining the editor no longer hides the picture-filter, quick-output
        # and compositing questions -- their gate carried the crop questions'
        # `_unified_video_editor_declined` clause, which (together with the
        # `used` clause beside it) could never be false on either branch, so
        # all three prompts were dead. They are `ask_optional` prompts, so
        # without a stub they read stdin and EOF here. Saved by module: the
        # `_home_module` map does not list wizard_quick/wizard_composite.
        from ffmwiz import wizard_composite, wizard_look  # noqa: PLC0415
        reachable_now = [
            (wizard_look, "step_video_look", "look"),
            (wizard_quick, "step_quick_output", "quick"),
            (wizard_composite, "step_video_composite", "composite"),
        ]
        saved_now = [(module, name, getattr(module, name))
                     for module, name, _label in reachable_now]
        for module, name, label in reachable_now:
            setattr(module, name, lambda answers, _l=label: calls.append(_l))
        self.addCleanup(lambda: [setattr(m, n, o) for m, n, o in saved_now])
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
            "step_raw_ffmpeg_args": wizard_raw.step_raw_ffmpeg_args,
        }
        try:
            wizard_steps.step_input_path = lambda answers: calls.append("input")
            wizard_steps.step_join_additional_inputs_for_encode = lambda answers: calls.append("join")
            wizard_steps.step_output_location = lambda answers: calls.append("output")
            wizard_steps.step_output_format = lambda answers: calls.append("format")
            wizard_steps.step_video_codec = lambda answers: calls.append("codec")
            wizard_steps.step_use_gpu = lambda answers: (calls.append("gpu"), answers.__setitem__("use_gpu", False))
            wizard_steps.step_unified_video_editor_for_encode = lambda answers: (
                calls.append("unified"),
                answers.__setitem__("_unified_video_editor_used", False),
                answers.__setitem__("_unified_video_editor_declined", True),
                answers.__setitem__("crop_enabled", False),
                answers.__setitem__("video_speed_enabled", False),
                answers.__setitem__("cut_keep_ranges", []),
            )
            wizard_steps.step_crop_enabled = lambda answers: calls.append("crop")
            wizard_steps.step_video_bitrate = lambda answers: calls.append("bitrate")
            wizard_raw.step_raw_ffmpeg_args = lambda answers: calls.append("raw_args")
            wizard_steps.step_cpu_two_pass = lambda answers: calls.append("two_pass")
            wizard_steps.step_resolution = lambda answers: calls.append("resolution")
            wizard_steps.step_fps = lambda answers: calls.append("fps")
            wizard_flow_b.step_video_speed_reverse_for_encode = lambda answers: calls.append("speed")
            wizard_flow_b.step_cuts = lambda answers: calls.append("cuts")
            wizard_b.step_start_now = lambda answers: (_ for _ in ()).throw(StopRun())
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
            wizard_steps.step_input_path = fake_input
            wizard_steps.step_join_additional_inputs_for_encode = fake_join
            wizard_steps.step_output_location = fake_output
            with self.assertRaises(StopRun):
                FFmWiz.run_wizard({"_question_offset": 1})
        finally:
            for name, original in originals.items():
                setattr(_home_module(name), name, original)

        self.assertEqual(seen_numbers, [8])

    def test_step_join_back_resume_preserves_existing_join_items_and_number_extra(self):
        originals = {
            "ask_join_add_another": wizard_steps.ask_join_add_another,
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
            # The DEFINING module. `wizard_steps` calls this name directly now, so a
            # patch on the facade would rebind an attribute nothing reads.
            wizard_steps.ask_join_add_another = fake_add_another
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

            # The DEFINING module. guibridge_b calls this name directly now, so a
            # patch on the facade would rebind an attribute nothing reads.
            from ffmwiz import guibridge_b
            with mock.patch.object(guibridge_b, "_launch_qt_gui", side_effect=fake_launch):
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

    def test_folder_keep_current_sample_rate_is_resolved_per_file(self):
        # "n = keep the current rate" is answered once, against the
        # REPRESENTATIVE file, and stored as that file's number plus a
        # `_keep` flag. The batch re-resolved the video and audio BITRATE per
        # file but not this, so a 44.1 kHz representative silently resampled
        # every 48 kHz and 96 kHz file in the folder down to its own rate.
        def _item(name, rate):
            audio = {"codec_type": "audio", "codec_name": "aac",
                     "sample_rate": str(rate), "bit_rate": "128000",
                     "duration": "10", "channels": 2}
            video = {"codec_type": "video", "codec_name": "h264",
                     "width": 1920, "height": 1080}
            return {"path": Path(name),
                    "answers": {"input_path": Path(name), "video_streams": [video],
                                "audio_streams": [audio], "format": {"duration": "10"}}}

        with tempfile.TemporaryDirectory() as tmp:
            settings = self.base_answers(tmp)
            settings["use_gpu"] = False
            settings["crop_enabled"] = False
            settings["folder_output_location"] = Path(tmp)
            settings["audio_codec"] = "aac"
            settings["audio_sample_rate"] = 44100
            settings["audio_sample_rate_keep"] = True
            for rate in (44100, 48000, 96000):
                with self.subTest(rate=rate):
                    with contextlib.redirect_stdout(io.StringIO()):
                        job = FFmWiz.prepare_folder_job_answers(
                            dict(settings), _item(f"f{rate}.mkv", rate))
                    self.assertEqual(rate, job["audio_sample_rate"])
                    self.assertEqual(rate, FFmWiz.resolve_audio_sample_rate(job))

            # An EXPLICIT rate still applies to every file unchanged.
            settings["audio_sample_rate"] = 48000
            settings["audio_sample_rate_keep"] = False
            for rate in (44100, 96000):
                with contextlib.redirect_stdout(io.StringIO()):
                    job = FFmWiz.prepare_folder_job_answers(
                        dict(settings), _item(f"g{rate}.mkv", rate))
                self.assertEqual(48000, job["audio_sample_rate"])

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
