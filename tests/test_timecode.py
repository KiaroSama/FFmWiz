"""Regression tests for HMSF timecode conversion at fractional frame rates.

`seconds_to_hmsf` counted total frames at the TRUE rate (23.976) and then split
them with the ROUNDED one (24), losing (fps_int - fps) / fps_int every second --
about 0.0999%, i.e. 3.58 s per hour at 23.976 / 29.97 / 59.94. One hour showed as
`00:59:56:10`. It also meant the function was not the inverse of
`parse_hmsf_time`, so a timecode read off the editor and typed back into a prompt
landed seconds away from where the user was.
"""
import unittest

import FFmWiz

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

    def test_degenerate_inputs_do_not_raise(self):
        self.assertEqual(FFmWiz.seconds_to_hmsf(-5.0, 25.0), "00:00:00:00")
        self.assertEqual(FFmWiz.seconds_to_hmsf(None, 25.0), "00:00:00:00")
        self.assertEqual(FFmWiz.seconds_to_hmsf(10.0, 0.0), "00:00:10:00")


if __name__ == "__main__":
    unittest.main()
