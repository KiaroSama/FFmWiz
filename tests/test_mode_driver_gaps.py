"""Driver-level cover for the main-menu modes that had none.

Modes 9, 12 and 15 have dedicated driver suites; 1, 3, 4, 8, 11 and 13 are
reached by name from existing modules. Modes 2, 5, 6, 7, 10 and 14 were driven
by nothing at all: every test around them stopped at a builder or a helper, so
a driver that returned early, swallowed a cancel or reported success after
doing nothing would not have failed anything.

Each mode here gets the three outcomes that actually differ for a user -- it
worked, it was cancelled, it failed -- driven through the REAL driver function
with the prompts and the FFmpeg launch replaced, never the driver itself.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k mode_driver_gaps
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz
from ffmwiz import modes, modes_b, modes_mediainfo, modes_transform
from ffmwiz.core.exceptions import Back

from artifact_guard import NoLeakedArtifacts


class ModeDriverCase(NoLeakedArtifacts, unittest.TestCase):
    """Shared scaffolding: a temp workspace and a silenced console."""

    def setUp(self):
        super().setUp()
        FFmWiz.appio.USE_COLOR = False
        self._tmpdir = tempfile.TemporaryDirectory(prefix="ffmwiz_mode_gap_")
        self.tmp = Path(self._tmpdir.name)
        self.source = self.tmp / "input.mkv"
        self.source.write_bytes(b"")

    def tearDown(self):
        self._tmpdir.cleanup()
        super().tearDown()

    def base(self, **extra) -> dict:
        answers = {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "_question_number": 1}
        answers.update(extra)
        return answers

    @contextlib.contextmanager
    def quiet(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            yield buffer


class Mode14CapabilityCache(ModeDriverCase):
    """Mode 14 -- FFmpeg capability cache (diagnostics)."""

    def drive(self, answers_typed: list[str]):
        with mock.patch.object(FFmWiz.appio, "ask_raw", side_effect=answers_typed):
            with self.quiet() as out:
                modes.run_capability_cache_menu(self.base())
        return out.getvalue()

    def test_back_leaves_the_menu_without_touching_the_cache(self):
        with mock.patch.object(modes, "_capability_cache_view") as view, \
             mock.patch.object(modes, "_capability_cache_clear") as clear:
            self.drive(["0"])
        view.assert_not_called()
        clear.assert_not_called()

    def test_an_empty_answer_is_back_too(self):
        with mock.patch.object(modes, "_capability_cache_view") as view:
            self.drive([""])
        view.assert_not_called()

    def test_viewing_the_cache_succeeds_and_returns_to_the_menu(self):
        with mock.patch.object(modes, "_capability_cache_view") as view:
            self.drive(["1", "0"])
        view.assert_called_once()

    def test_clearing_the_cache_succeeds(self):
        with mock.patch.object(modes, "_capability_cache_clear") as clear:
            self.drive(["3", "0"])
        clear.assert_called_once()

    def test_an_unknown_selection_is_reported_and_re_asked(self):
        with mock.patch.object(modes, "_capability_cache_view") as view:
            printed = self.drive(["9", "0"])
        view.assert_not_called()
        self.assertIn("Enter 0, 1, 2, or 3", printed)


class Mode7MediaInfo(ModeDriverCase):
    """Mode 7 -- Media info report."""

    def test_a_single_file_writes_one_report(self):
        report = mock.Mock(sidecar_paths=[], screenshot_dir=None, skipped_sections=[],
                           info_path=self.tmp / "report.txt")
        with mock.patch.object(modes_mediainfo, "ask_media_info_input_path",
                               return_value=self.source), \
             mock.patch.object(modes_mediainfo, "ask_media_info_options",
                               return_value=mock.Mock()), \
             mock.patch.object(modes_mediainfo, "create_media_info_report",
                               return_value=report) as create, \
             mock.patch.object(modes_mediainfo, "print_media_info_result"):
            with self.quiet():
                modes_mediainfo.run_media_info_mode(self.base())
        create.assert_called_once()

    def test_an_empty_folder_is_reported_and_writes_nothing(self):
        folder = self.tmp / "empty"
        folder.mkdir()
        with mock.patch.object(modes_mediainfo, "ask_media_info_input_path",
                               return_value=folder), \
             mock.patch.object(modes_mediainfo, "ask_media_info_options",
                               return_value=mock.Mock()), \
             mock.patch.object(modes_mediainfo, "media_info_folder_candidates",
                               return_value=[]), \
             mock.patch.object(modes_mediainfo, "create_media_info_report") as create, \
             mock.patch.object(FFmWiz.appio, "error") as error:
            with self.quiet():
                modes_mediainfo.run_media_info_mode(self.base())
        create.assert_not_called()
        self.assertIn("No files were found", " ".join(str(c) for c in error.call_args_list))

    def test_a_cancel_at_the_first_prompt_returns_to_the_menu(self):
        with mock.patch.object(modes_mediainfo, "ask_media_info_input_path",
                               side_effect=Back()), \
             mock.patch.object(modes_mediainfo, "create_media_info_report") as create:
            with self.quiet():
                result = modes_mediainfo.run_media_info_mode(self.base())
        self.assertIsNone(result)
        create.assert_not_called()

    def test_one_unreadable_file_does_not_abandon_the_folder(self):
        folder = self.tmp / "media"
        folder.mkdir()
        good, bad = folder / "a.mkv", folder / "b.mkv"
        good.write_bytes(b"")
        bad.write_bytes(b"")
        report = mock.Mock(sidecar_paths=[], screenshot_dir=None, skipped_sections=[],
                           info_path=self.tmp / "report.txt")
        with mock.patch.object(modes_mediainfo, "ask_media_info_input_path",
                               return_value=folder), \
             mock.patch.object(modes_mediainfo, "ask_media_info_options",
                               return_value=mock.Mock()), \
             mock.patch.object(modes_mediainfo, "media_info_folder_candidates",
                               return_value=[bad, good]), \
             mock.patch.object(modes_mediainfo, "create_media_info_report",
                               side_effect=[RuntimeError("unreadable"), report]) as create:
            with self.quiet() as out:
                modes_mediainfo.run_media_info_mode(self.base())
        self.assertEqual(create.call_count, 2)
        self.assertIn("wrote", out.getvalue())


class Mode6ExtractStream(ModeDriverCase):
    """Mode 6 -- Extract Stream."""

    def fill(self, **answers):
        def run_steps(target, _steps):
            target.update(answers)
        return run_steps

    def test_a_cancel_returns_to_the_menu_without_running_ffmpeg(self):
        with mock.patch.object(modes_b, "run_mode_steps", side_effect=Back()), \
             mock.patch.object(modes_b, "run_ffmpeg_with_progress") as run:
            with self.quiet():
                self.assertIsNone(modes_b.run_extract_stream_mode(self.base()))
        run.assert_not_called()

    def test_declining_to_start_leaves_the_plan_unexecuted(self):
        with mock.patch.object(modes_b, "run_mode_steps",
                               side_effect=self.fill(start_now=False, _extract_jobs=[])), \
             mock.patch.object(modes_b, "run_ffmpeg_with_progress") as run:
            with self.quiet() as out:
                self.assertIsNone(modes_b.run_extract_stream_mode(self.base()))
        run.assert_not_called()
        self.assertIn("FFmpeg was not started", out.getvalue())

    def test_a_successful_run_extracts_every_planned_stream(self):
        job = {"path": str(self.source), "output_path": str(self.tmp / "out.aac"),
               "stream": {"index": 1, "codec_type": "audio"}}
        with mock.patch.object(modes_b, "run_mode_steps",
                               side_effect=self.fill(start_now=True, _extract_jobs=[job])), \
             mock.patch.object(modes_b, "build_extract_streams_command",
                               return_value=["ffmpeg", "-i", str(self.source)]), \
             mock.patch.object(modes_b, "run_ffmpeg_with_progress",
                               return_value=(0, 1.0)) as run:
            with self.quiet():
                result = modes_b.run_extract_stream_mode(self.base())
        run.assert_called_once()
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 0)

    def test_a_failing_extraction_reports_a_nonzero_code(self):
        job = {"path": str(self.source), "output_path": str(self.tmp / "out.aac"),
               "stream": {"index": 1, "codec_type": "audio"}}
        with mock.patch.object(modes_b, "run_mode_steps",
                               side_effect=self.fill(start_now=True, _extract_jobs=[job])), \
             mock.patch.object(modes_b, "build_extract_streams_command",
                               return_value=["ffmpeg", "-i", str(self.source)]), \
             mock.patch.object(modes_b, "run_ffmpeg_with_progress",
                               return_value=(1, 1.0)):
            with self.quiet():
                result = modes_b.run_extract_stream_mode(self.base())
        self.assertIsNotNone(result)
        self.assertNotEqual(result[0], 0)


class Mode10VideoSpeedReverse(ModeDriverCase):
    """Mode 10 -- Video Speed / Reverse."""

    def fill(self, **answers):
        def run_steps(target, _steps):
            target.update(answers)
        return run_steps

    def test_a_cancel_returns_to_the_menu(self):
        with mock.patch.object(modes_transform, "run_mode_steps", side_effect=Back()), \
             mock.patch.object(modes_transform, "run_ffmpeg_with_progress") as run:
            with self.quiet():
                self.assertIsNone(modes_transform.run_video_speed_reverse_mode(self.base()))
        run.assert_not_called()

    def test_choosing_no_change_returns_without_encoding(self):
        with mock.patch.object(modes_transform, "run_mode_steps",
                               side_effect=self.fill(_speed_reverse_noop=True,
                                                     input_path=self.source)), \
             mock.patch.object(modes_transform, "ensure_video_input"), \
             mock.patch.object(modes_transform, "run_ffmpeg_with_progress") as run:
            with self.quiet() as out:
                self.assertIsNone(modes_transform.run_video_speed_reverse_mode(self.base()))
        run.assert_not_called()
        self.assertIn("was not enabled", out.getvalue())

    def test_an_invalid_setting_is_reported_and_stops_the_mode(self):
        with mock.patch.object(modes_transform, "run_mode_steps",
                               side_effect=ValueError("speed must be positive")), \
             mock.patch.object(modes_transform, "run_ffmpeg_with_progress") as run, \
             mock.patch.object(FFmWiz.appio, "error") as error:
            with self.quiet():
                self.assertIsNone(modes_transform.run_video_speed_reverse_mode(self.base()))
        run.assert_not_called()
        error.assert_called_once()

    def test_a_successful_speed_change_runs_the_built_command(self):
        with mock.patch.object(modes_transform, "run_mode_steps",
                               side_effect=self.fill(input_path=self.source, start_now=True,
                                                     cmd=["ffmpeg", "-i", str(self.source)],
                                                     output_path=self.tmp / "out.mp4")), \
             mock.patch.object(modes_transform, "ensure_video_input"), \
             mock.patch.object(modes_transform, "run_ffmpeg_with_progress",
                               return_value=(0, 2.0)) as run:
            with self.quiet():
                result = modes_transform.run_video_speed_reverse_mode(self.base())
        run.assert_called_once()
        self.assertEqual(result[0], 0)

    def test_a_reverse_is_routed_to_the_segmented_pipeline(self):
        with mock.patch.object(modes_transform, "run_mode_steps",
                               side_effect=self.fill(input_path=self.source, start_now=True,
                                                     reverse_video=True,
                                                     cmd=["ffmpeg", "-i", str(self.source)],
                                                     output_path=self.tmp / "out.mp4")), \
             mock.patch.object(modes_transform, "ensure_video_input"), \
             mock.patch.object(modes_transform, "run_segmented_reverse_video_speed",
                               return_value=(0, 3.0)) as segmented, \
             mock.patch.object(modes_transform, "run_ffmpeg_with_progress") as plain:
            with self.quiet():
                result = modes_transform.run_video_speed_reverse_mode(self.base())
        segmented.assert_called_once()
        plain.assert_not_called()
        self.assertEqual(result[0], 0)


class Mode5AddFilesToVideo(ModeDriverCase):
    """Mode 5 -- Add files to video."""

    def test_a_cancel_at_the_source_prompt_returns_to_the_menu(self):
        with mock.patch.object(modes, "ask_add_files_source_video", side_effect=Back()), \
             mock.patch.object(modes, "build_add_files_to_video_command") as build:
            with self.quiet():
                self.assertIsNone(modes.run_add_files_to_video_mode(self.base()))
        build.assert_not_called()

    def test_incompatible_streams_stop_the_mode_without_an_output(self):
        def fill(answers):
            answers["input_path"] = self.source

        with mock.patch.object(modes, "ask_add_files_source_video", side_effect=fill), \
             mock.patch.object(modes, "ask_additional_track_files",
                               return_value=[{"path": self.tmp / "extra.aac"}]), \
             mock.patch.object(modes, "add_files_stream_copy_compatibility_errors",
                               return_value=["aac cannot be copied into avi"]), \
             mock.patch.object(modes, "build_add_files_to_video_command") as build, \
             mock.patch.object(FFmWiz.appio, "error") as error:
            with self.quiet():
                self.assertIsNone(modes.run_add_files_to_video_mode(self.base()))
        build.assert_not_called()
        self.assertTrue(error.called)

    def compatible_run(self, start_now: bool, launch_result=(0, 1.0)):
        """Drive the whole mode with one compatible extra track."""
        def fill(answers):
            answers["input_path"] = self.source

        with mock.patch.object(modes, "ask_add_files_source_video", side_effect=fill), \
             mock.patch.object(modes, "ask_additional_track_files",
                               return_value=[{"path": self.tmp / "extra.aac"}]), \
             mock.patch.object(modes, "add_files_stream_copy_compatibility_errors",
                               return_value=[]), \
             mock.patch.object(modes, "choose_add_files_output_path",
                               return_value=self.tmp / "out.mkv"), \
             mock.patch.object(modes, "build_add_files_to_video_command",
                               return_value=["ffmpeg", "-i", str(self.source)]) as build, \
             mock.patch.object(modes, "print_add_files_summary"), \
             mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=start_now), \
             mock.patch.object(modes, "run_ffmpeg_with_progress",
                               return_value=launch_result) as run:
            with self.quiet():
                result = modes.run_add_files_to_video_mode(self.base())
        return result, build, run

    def test_a_compatible_set_reaches_the_command_builder_and_runs(self):
        result, build, run = self.compatible_run(start_now=True)
        build.assert_called_once()
        run.assert_called_once()
        self.assertEqual(result[0], 0)
        self.assertEqual(run.call_args.args[0], ["ffmpeg", "-i", str(self.source)])

    def test_declining_to_start_leaves_the_command_unexecuted(self):
        result, build, run = self.compatible_run(start_now=False)
        build.assert_called_once()
        run.assert_not_called()
        self.assertIsNone(result)

    def test_a_failing_mux_reports_its_exit_code(self):
        result, _build, run = self.compatible_run(start_now=True, launch_result=(1, 1.0))
        run.assert_called_once()
        self.assertEqual(result[0], 1)


class Mode3CopyCut(ModeDriverCase):
    """Mode 3 -- Cut video only with copy."""

    def fill_input(self, answers):
        answers["input_path"] = self.source
        answers["video_streams"] = [{"index": 0, "codec_type": "video"}]
        answers["format"] = {"duration": "10.0"}

    def drive(self, method=1, keep_ranges=((0.0, 5.0),), proceed=True,
              video=True, copy_result=0):
        def fill(answers):
            self.fill_input(answers)
            if not video:
                answers["video_streams"] = []

        with mock.patch.object(modes.wizard, "step_input_path", side_effect=fill), \
             mock.patch.object(modes.wizard, "step_output_location",
                               side_effect=lambda a: a.update(output_location=self.tmp)), \
             mock.patch.object(modes.services, "get_video_fps", return_value=25.0), \
             mock.patch.object(modes.services, "stream_duration_seconds", return_value=10.0), \
             mock.patch.object(modes, "ask_cut_method", return_value=method), \
             mock.patch.object(modes.services, "collect_cut_ranges_terminal",
                               return_value=list(keep_ranges)), \
             mock.patch.object(modes, "print_cut_summary"), \
             mock.patch.object(modes, "ask_continue_default_yes", return_value=proceed), \
             mock.patch.object(modes, "run_copy_cut", return_value=copy_result) as cut:
            with self.quiet() as out:
                result = modes.run_copy_cut_mode(self.base())
        return result, cut, out.getvalue()

    def test_a_confirmed_cut_runs_the_copy_executor(self):
        result, cut, _ = self.drive()
        cut.assert_called_once()
        self.assertEqual(result[0], 0)
        self.assertEqual(cut.call_args.args[1], [(0.0, 5.0)])

    def test_declining_the_confirmation_cancels_without_cutting(self):
        result, cut, printed = self.drive(proceed=False)
        cut.assert_not_called()
        self.assertIsNone(result)
        self.assertIn("canceled", printed.lower())

    def test_a_failing_copy_cut_reports_its_exit_code(self):
        result, cut, _ = self.drive(copy_result=1)
        cut.assert_called_once()
        self.assertEqual(result[0], 1)

    def test_a_cancel_at_the_first_question_returns_to_the_menu(self):
        with mock.patch.object(modes.wizard, "step_input_path", side_effect=Back()), \
             mock.patch.object(modes, "run_copy_cut") as cut:
            with self.quiet():
                self.assertIsNone(modes.run_copy_cut_mode(self.base()))
        cut.assert_not_called()


class Mode13MetadataEditor(ModeDriverCase):
    """Mode 13 -- Metadata Editor."""

    def test_back_at_the_first_prompt_returns_to_the_menu(self):
        from ffmwiz import metadata
        with mock.patch.object(metadata, "metadata_prompt_input", side_effect=Back()), \
             mock.patch.object(metadata, "run_stream_metadata_editor") as editor:
            with self.quiet():
                self.assertIsNone(metadata.run_metadata_editor_mode(self.base()))
        editor.assert_not_called()

    def test_a_menu_choice_reaches_its_editor_and_then_returns(self):
        from ffmwiz import metadata
        with mock.patch.object(metadata, "metadata_prompt_input", return_value=self.base()), \
             mock.patch.object(metadata, "metadata_menu_selection", side_effect=["1", "0"]), \
             mock.patch.object(metadata, "run_stream_metadata_editor") as editor:
            with self.quiet():
                self.assertIsNone(metadata.run_metadata_editor_mode(self.base()))
        editor.assert_called_once()

    def test_a_failing_editor_is_reported_without_leaving_the_mode(self):
        from ffmwiz import metadata
        with mock.patch.object(metadata, "metadata_prompt_input", return_value=self.base()), \
             mock.patch.object(metadata, "metadata_menu_selection", side_effect=["1", "0"]), \
             mock.patch.object(metadata, "run_stream_metadata_editor",
                               side_effect=RuntimeError("ffprobe said no")), \
             mock.patch.object(FFmWiz.appio, "error") as error:
            with self.quiet():
                self.assertIsNone(metadata.run_metadata_editor_mode(self.base()))
        self.assertIn("ffprobe said no",
                      " ".join(str(call) for call in error.call_args_list))


class Mode2WizardFromConfig(ModeDriverCase):
    """Mode 2 -- Wizard from config (ask only what is blank)."""

    def test_an_unreadable_config_is_reported_and_the_wizard_never_starts(self):
        config = self.tmp / "config.env"
        with mock.patch.object(FFmWiz, "ask_main_menu", return_value=2), \
             mock.patch.object(FFmWiz, "ensure_config_file"), \
             mock.patch.object(FFmWiz, "parse_env_config",
                               side_effect=ValueError("bad line 3")), \
             mock.patch.object(FFmWiz, "run_wizard") as wizard, \
             mock.patch.object(FFmWiz, "fail") as failed:
            with self.quiet():
                self.assertIsNone(FFmWiz.run_one_job(self.base(), config))
        wizard.assert_not_called()
        self.assertIn("Could not read config file",
                      " ".join(str(call) for call in failed.call_args_list))

    def test_a_readable_config_reaches_the_wizard(self):
        config = self.tmp / "config.env"
        config.write_text("video_codec=libx264\n", encoding="utf-8")
        with mock.patch.object(FFmWiz, "ask_main_menu", return_value=2), \
             mock.patch.object(FFmWiz, "ensure_config_file"), \
             mock.patch.object(FFmWiz, "parse_env_config",
                               return_value={"settings": {"video_codec": "libx264"}}), \
             mock.patch.object(FFmWiz, "run_wizard", side_effect=Back()) as wizard:
            with self.quiet():
                self.assertIsNone(FFmWiz.run_one_job(self.base(), config))
        wizard.assert_called_once()
        self.assertIsNotNone(wizard.call_args.kwargs.get("config"))

    def test_a_cancel_inside_the_wizard_returns_to_the_menu(self):
        config = self.tmp / "config.env"
        with mock.patch.object(FFmWiz, "ask_main_menu", return_value=1), \
             mock.patch.object(FFmWiz, "run_wizard", side_effect=Back()) as wizard:
            with self.quiet():
                self.assertIsNone(FFmWiz.run_one_job(self.base(), config))
        wizard.assert_called_once()


if __name__ == "__main__":
    unittest.main()
