"""Regression tests for HMSF timecode conversion at fractional frame rates.

`seconds_to_hmsf` counted total frames at the TRUE rate (23.976) and then split
them with the ROUNDED one (24), losing (fps_int - fps) / fps_int every second --
about 0.0999%, i.e. 3.58 s per hour at 23.976 / 29.97 / 59.94. One hour showed as
`00:59:56:10`. It also meant the function was not the inverse of
`parse_hmsf_time`, so a timecode read off the editor and typed back into a prompt
landed seconds away from where the user was.
"""
import subprocess
import sys
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz.core import timeline

# gui_geometry is a top-level module inside the standalone GUI subprocess, so
# it is reached the same way ffmwiz_gui.py reaches it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ffmwiz" / "gui"))
import gui_geometry  # noqa: E402

BROADCAST_RATES = [
    23.976023976023978,
    24.0,
    25.0,
    29.97002997002997,
    30.0,
    50.0,
    59.94005994005994,
    60.0,
]

MARKS = [0.0, 1.0, 59.5, 60.0, 600.0, 3600.0, 7200.0, 10800.0]


class TimecodeConversion(unittest.TestCase):
    def test_round_trip_is_within_half_a_frame(self):
        for fps in BROADCAST_RATES:
            for seconds in MARKS:
                with self.subTest(fps=round(fps, 4), seconds=seconds):
                    text = FFmWiz.seconds_to_hmsf(seconds, fps)
                    back = FFmWiz.parse_hmsf_time(text, fps)
                    # Half a frame is the exact quantisation limit: a position
                    # sitting midway between two frames (59.5 s at 25 fps) lands
                    # on one of them and is off by exactly 0.5/fps.
                    self.assertLessEqual(
                        abs(back - seconds), 0.5 / fps + 1e-9,
                        f"{seconds}s -> {text} -> {back}s at {fps} fps",
                    )

    def test_wall_clock_fields_are_exact_at_whole_seconds(self):
        # The h:m:s part is wall clock, not a frame count, so an exact second
        # must render exactly -- one hour is 01:00:00, never 00:59:56.
        for fps in BROADCAST_RATES:
            with self.subTest(fps=round(fps, 4)):
                self.assertEqual(FFmWiz.seconds_to_hmsf(3600.0, fps), "01:00:00:00")
                self.assertEqual(FFmWiz.seconds_to_hmsf(60.0, fps), "00:01:00:00")
                self.assertEqual(FFmWiz.seconds_to_hmsf(0.0, fps), "00:00:00:00")

    def test_no_drift_accumulates_over_three_hours(self):
        # The original bug grew linearly: ~3.58 s at one hour, ~10.7 s at three.
        for fps in (23.976023976023978, 29.97002997002997, 59.94005994005994):
            with self.subTest(fps=round(fps, 4)):
                text = FFmWiz.seconds_to_hmsf(10800.0, fps)
                self.assertEqual(text, "03:00:00:00")
                self.assertAlmostEqual(FFmWiz.parse_hmsf_time(text, fps), 10800.0, places=3)

    def test_frame_field_stays_below_the_nominal_rate(self):
        fps = 23.976023976023978
        for millis in range(0, 1000, 7):
            text = FFmWiz.seconds_to_hmsf(10.0 + millis / 1000.0, fps)
            frame = int(text.rsplit(":", 1)[1])
            self.assertLess(frame, round(fps), f"{text} has a frame index >= the nominal rate")

    def test_sub_second_positions_map_to_the_right_frame(self):
        fps = 25.0
        self.assertEqual(FFmWiz.seconds_to_hmsf(10.0, fps), "00:00:10:00")
        self.assertEqual(FFmWiz.seconds_to_hmsf(10.04, fps), "00:00:10:01")
        self.assertEqual(FFmWiz.seconds_to_hmsf(10.96, fps), "00:00:10:24")

    def test_round_trip_stays_within_one_frame_across_the_d19_grid(self):
        # The defect grid from D19: every broadcast rate that is not an integer
        # plus the integer ones that were already correct, at positions that
        # straddle a minute and an hour boundary.
        for fps in (24000 / 1001, 30000 / 1001, 60000 / 1001, 24.0, 25.0, 30.0):
            for seconds in (0, 0.5, 59.9, 60, 600, 3599.9, 3600, 7200):
                with self.subTest(fps=round(fps, 4), seconds=seconds):
                    text = FFmWiz.seconds_to_hmsf(seconds, fps)
                    back = FFmWiz.parse_hmsf_time(text, fps)
                    self.assertLessEqual(
                        abs(back - seconds), 1.0 / fps,
                        f"{seconds}s -> {text} -> {back}s at {fps} fps",
                    )

    def test_the_old_mixed_clock_arithmetic_is_pinned_as_a_regression(self):
        # Documents exactly what was wrong, so a future "optimisation" back to
        # frame counting is recognisable. The old code counted total frames at
        # the TRUE rate and then split them with the ROUNDED one.
        fps = 24000 / 1001  # 23.976023976023978

        def old_seconds_to_hmsf(seconds, fps):
            fps_int = max(1, round(fps))
            total_frames = int(round(float(seconds) * fps))
            frame = total_frames % fps_int
            whole_seconds = total_frames // fps_int
            hours, rem = divmod(whole_seconds, 3600)
            minutes, secs = divmod(rem, 60)
            return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frame:02d}"

        old_text = old_seconds_to_hmsf(3600.0, fps)
        self.assertEqual(old_text, "00:59:56:10")
        old_error = FFmWiz.parse_hmsf_time(old_text, fps) - 3600.0
        self.assertAlmostEqual(old_error, -3.58291666, places=6)

        new_text = FFmWiz.seconds_to_hmsf(3600.0, fps)
        self.assertEqual(new_text, "01:00:00:00")
        self.assertEqual(FFmWiz.parse_hmsf_time(new_text, fps), 3600.0)

    def test_every_caller_shares_one_definition(self):
        # The GUI kept a byte-identical copy that stayed buggy after the core
        # was fixed. Same object, not merely equal output: a fresh copy passes
        # a value comparison on the day it is made and drifts the day after.
        self.assertIs(gui_geometry.seconds_to_hmsf, timeline.seconds_to_hmsf)
        self.assertIs(FFmWiz.seconds_to_hmsf, timeline.seconds_to_hmsf)

    def test_the_gui_copy_resolves_in_a_bare_subprocess(self):
        # The GUI is launched as `python ffmwiz/gui/ffmwiz_gui.py`, so it starts
        # with ONLY ffmwiz/gui on sys.path -- the project root that makes
        # `from ffmwiz.core...` work is not there by luck. This suite always has
        # the root on sys.path, so only a fresh interpreter can prove it.
        gui_dir = Path(FFmWiz.__file__).resolve().parent / "ffmwiz" / "gui"
        probe = (
            "import sys; sys.path[:] = [r'%s'] + sys.path[1:]; "
            "import gui_geometry, ffmwiz.core.timeline as t; "
            "assert gui_geometry.seconds_to_hmsf is t.seconds_to_hmsf"
        ) % gui_dir
        # cwd is the drive root, not the project: inheriting the project cwd
        # would put the root on sys.path for free and hide the very failure
        # this test exists to catch.
        result = subprocess.run([sys.executable, "-c", probe], cwd=gui_dir.anchor,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(
            result.returncode, 0,
            "the GUI subprocess cannot import gui_geometry: "
            + result.stderr.strip()[-500:])

    def test_degenerate_inputs_do_not_raise(self):
        self.assertEqual(FFmWiz.seconds_to_hmsf(-5.0, 25.0), "00:00:00:00")
        self.assertEqual(FFmWiz.seconds_to_hmsf(None, 25.0), "00:00:00:00")
        self.assertEqual(FFmWiz.seconds_to_hmsf(10.0, 0.0), "00:00:10:00")


if __name__ == "__main__":
    unittest.main()
