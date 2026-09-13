"""Guard: every main-menu mode has a driver-level test, and the map says where.

Menu entries drift -- modes 14 and 15 once shipped undocumented -- and a mode
whose driver nothing exercises can return early, swallow a cancel or report
success after doing nothing, with the whole suite still green. The dispatch is
parsed out of `run_one_job` every run, so adding a mode fails this file until
its coverage is real and recorded.

The map names modules, not counts: it is a pointer for the next reader, and it
fails when a module is renamed or stops mentioning the driver it claims to
drive.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k mode_coverage_map
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ENTRY = TESTS_DIR.parent / "FFmWiz.py"

# mode -> (driver symbol, module, success test, stopped test). Named tests, not
# a keyword heuristic over test names: a heuristic passes on a suite that never
# drove the mode, and it silently stops meaning anything the moment somebody
# renames a test. Modes 1 and 2 run the shared wizard rather than a `run_*_mode`
# of their own, so they name `run_wizard`.
MODE_COVERAGE: dict[int, tuple[str, str, str, str]] = {
    1: ("run_wizard", "test_manual_run_mode1",
        "test_accepting_still_cleans_up",
        "test_a_declined_cut_command_still_runs_afterwards"),
    2: ("run_wizard", "test_mode_driver_gaps",
        "test_a_readable_config_reaches_the_wizard",
        "test_an_unreadable_config_is_reported_and_the_wizard_never_starts"),
    3: ("run_copy_cut_mode", "test_mode_driver_gaps",
        "test_a_confirmed_cut_runs_the_copy_executor",
        "test_declining_the_confirmation_cancels_without_cutting"),
    4: ("run_folder_encode_mode", "test_folder_job_isolation",
        "test_every_job_gets_its_own_lease_and_resolved_settings",
        "test_a_cancellation_still_releases_the_job"),
    5: ("run_add_files_to_video_mode", "test_mode_driver_gaps",
        "test_a_compatible_set_reaches_the_command_builder_and_runs",
        "test_incompatible_streams_stop_the_mode_without_an_output"),
    6: ("run_extract_stream_mode", "test_mode_driver_gaps",
        "test_a_successful_run_extracts_every_planned_stream",
        "test_a_cancel_returns_to_the_menu_without_running_ffmpeg"),
    7: ("run_media_info_mode", "test_mode_driver_gaps",
        "test_a_single_file_writes_one_report",
        "test_a_cancel_at_the_first_prompt_returns_to_the_menu"),
    8: ("run_mux_cleanup_mode", "test_mux_cleanup_port",
        "test_successful_run_reports_success",
        "test_failed_run_reports_a_failure_exit_code"),
    9: ("run_hardsub_encode_mode", "test_mode_driver_hardsub",
        "test_the_subtitle_answers_reach_the_builder",
        "test_back_from_the_first_step_leaves_the_mode"),
    10: ("run_video_speed_reverse_mode", "test_mode_driver_gaps",
         "test_a_successful_speed_change_runs_the_built_command",
         "test_an_invalid_setting_is_reported_and_stops_the_mode"),
    11: ("run_audio_transform_mode", "test_bounded_audio_reverse",
         "test_the_standalone_transform_mode_routes_through_the_bounded_plan",
         "test_a_selection_that_cannot_fit_one_sample_is_refused"),
    12: ("run_join_videos_mode", "test_mode_driver_join",
         "test_two_copy_compatible_inputs_use_the_copy_builder",
         "test_mixing_audio_only_and_video_inputs_is_refused_before_any_builder"),
    13: ("run_metadata_editor_mode", "test_mode_driver_gaps",
         "test_a_menu_choice_reaches_its_editor_and_then_returns",
         "test_a_failing_editor_is_reported_without_leaving_the_mode"),
    14: ("run_capability_cache_menu", "test_mode_driver_gaps",
         "test_viewing_the_cache_succeeds_and_returns_to_the_menu",
         "test_an_unknown_selection_is_reported_and_re_asked"),
    15: ("run_track_manager_mode", "test_mode_driver_track_manager",
         "test_the_removal_specs_and_externals_reach_the_builder",
         "test_a_subtitle_the_container_cannot_carry_is_refused_before_the_builder"),
}


def dispatched_modes() -> dict[int, str]:
    """`{mode number: driver name}` as `run_one_job` actually dispatches them."""
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"), filename=str(ENTRY))
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == "run_one_job")
    found: dict[int, str] = {}
    for node in ast.walk(function):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)):
            continue
        left = node.test.left
        if not (isinstance(left, ast.Name) and left.id == "start_mode"):
            continue
        number = node.test.comparators[0].value
        drivers = [call.func.attr if isinstance(call.func, ast.Attribute)
                   else getattr(call.func, "id", "")
                   for call in ast.walk(node) if isinstance(call, ast.Call)]
        drivers = [name for name in drivers if name.startswith("run_")]
        if drivers:
            found[number] = drivers[0]
    # Modes 1 and 2 fall through the chain into the shared wizard.
    found.setdefault(1, "run_wizard")
    found.setdefault(2, "run_wizard")
    return found


def test_names(module: str) -> list[str]:
    path = TESTS_DIR / f"{module}.py"
    if not path.is_file():
        return []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")]


class EveryModeHasADriverTest(unittest.TestCase):
    def test_the_map_matches_the_dispatch(self):
        dispatch = dispatched_modes()
        self.assertEqual(sorted(dispatch), sorted(MODE_COVERAGE),
                         "the main menu and this map disagree about which modes exist")
        for number, driver in sorted(dispatch.items()):
            with self.subTest(mode=number):
                self.assertEqual(MODE_COVERAGE[number][0], driver)

    def test_every_named_module_exists(self):
        for number, (_driver, module, _ok, _stopped) in sorted(MODE_COVERAGE.items()):
            with self.subTest(mode=number, module=module):
                self.assertTrue((TESTS_DIR / f"{module}.py").is_file(),
                                f"mode {number} names a test module that is gone")

    def test_every_named_module_actually_drives_its_mode(self):
        for number, (driver, module, _ok, _stopped) in sorted(MODE_COVERAGE.items()):
            with self.subTest(mode=number, module=module):
                text = (TESTS_DIR / f"{module}.py").read_text(encoding="utf-8")
                self.assertIn(driver, text, f"{module} no longer mentions {driver}")

    def test_every_mode_covers_success_and_being_stopped(self):
        for number, (_driver, module, success, stopped) in sorted(MODE_COVERAGE.items()):
            names = test_names(module)
            with self.subTest(mode=number):
                self.assertIn(success, names,
                              f"mode {number}: the success test named here is gone")
                self.assertIn(stopped, names,
                              f"mode {number}: the cancel/error test named here is gone")
                self.assertNotEqual(success, stopped)

    def test_the_guard_notices_a_new_mode(self):
        dispatch = dict(dispatched_modes())
        dispatch[99] = "run_a_brand_new_mode"
        self.assertNotEqual(sorted(dispatch), sorted(MODE_COVERAGE))


if __name__ == "__main__":
    unittest.main()
