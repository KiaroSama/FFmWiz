"""Regression: the reverse segment size must follow the frame size (F10).

The executor split a reverse into fixed 60-second chunks and told the user it
was doing so "to avoid buffering the full video in RAM". `reverse` holds every
decoded frame of its input, so what matters is the frame SIZE, not the clock --
and 60 seconds is only an SD-sized promise:

    480p30    1.03 GiB of decoded frames
    720p30    2.32 GiB
    1080p30   5.21 GiB
    1080p60  10.43 GiB
    4K30     20.86 GiB

Measured on this machine with real encodes, peak RSS tracks that frame total
almost exactly: 720p30 buffering 300 frames peaked at 878 MB against a 415 MB
frame estimate, and 600 frames at 1306 MB -- 1.43 MB per frame against a
theoretical 1.38, plus a ~450 MB fixed encoder/decoder baseline.

So the segment length is now derived from the geometry against a 1 GiB budget.
The numbers below are the budget, not a guess: each case is asserted to land
within it.
"""
import unittest

import FFmWiz


def _answers(width, height, fps):
    return {"video_streams": [{"width": width, "height": height}], "fps": fps}


def _frames_bytes(width, height, fps, seconds):
    # yuv420p: one luma byte plus half a byte of chroma per pixel.
    return width * height * 1.5 * fps * seconds


class TheSegmentFitsTheBudget(unittest.TestCase):
    CASES = [
        ("480p30", 854, 480, 30),
        ("720p30", 1280, 720, 30),
        ("1080p30", 1920, 1080, 30),
        ("1080p60", 1920, 1080, 60),
        ("4K30", 3840, 2160, 30),
    ]

    def test_every_common_geometry_stays_inside_one_gib(self):
        for label, width, height, fps in self.CASES:
            with self.subTest(geometry=label):
                seconds = FFmWiz.reverse_segment_seconds(_answers(width, height, fps))
                held = _frames_bytes(width, height, fps, seconds)
                self.assertLessEqual(
                    held, FFmWiz.REVERSE_SEGMENT_BUDGET_BYTES * 1.01,
                    f"{label}: a segment would hold {held / 1024 ** 3:.2f} GiB")

    def test_the_old_flat_value_really_did_overrun(self):
        # Guard the guard: if the budget or the arithmetic changed so that 60 s
        # were fine everywhere, the assertion above would prove nothing.
        held = _frames_bytes(1920, 1080, 30, FFmWiz.REVERSE_SEGMENT_SECONDS)
        self.assertGreater(held, 4 * 1024 ** 3,
                           "1080p30 at the flat 60 s should be several GiB")

    def test_a_bigger_frame_gets_a_shorter_segment(self):
        small = FFmWiz.reverse_segment_seconds(_answers(854, 480, 30))
        large = FFmWiz.reverse_segment_seconds(_answers(3840, 2160, 30))
        self.assertGreater(small, large)

    def test_a_higher_frame_rate_gets_a_shorter_segment(self):
        thirty = FFmWiz.reverse_segment_seconds(_answers(1920, 1080, 30))
        sixty = FFmWiz.reverse_segment_seconds(_answers(1920, 1080, 60))
        self.assertAlmostEqual(thirty / 2.0, sixty, delta=0.2)

    def test_it_never_exceeds_the_documented_ceiling(self):
        tiny = FFmWiz.reverse_segment_seconds(_answers(16, 16, 1))
        self.assertLessEqual(tiny, FFmWiz.REVERSE_SEGMENT_SECONDS)

    def test_it_never_collapses_to_nothing(self):
        huge = FFmWiz.reverse_segment_seconds(_answers(15360, 8640, 120))
        self.assertGreaterEqual(huge, FFmWiz.REVERSE_SEGMENT_MIN_SECONDS)

    def test_unknown_geometry_keeps_the_previous_behaviour(self):
        # An unprobeable input must not fail here; it behaves as it always did.
        self.assertEqual(FFmWiz.REVERSE_SEGMENT_SECONDS,
                         FFmWiz.reverse_segment_seconds({}))
        self.assertEqual(FFmWiz.REVERSE_SEGMENT_SECONDS,
                         FFmWiz.reverse_segment_seconds(
                             {"video_streams": [{"width": 0, "height": 0}]}))

    def test_a_missing_frame_rate_assumes_a_common_one(self):
        seconds = FFmWiz.reverse_segment_seconds(
            {"video_streams": [{"width": 1920, "height": 1080}]})
        self.assertLess(seconds, FFmWiz.REVERSE_SEGMENT_SECONDS,
                        "1080p must not fall back to the flat 60 s")


class TheExecutorUsesIt(unittest.TestCase):
    def test_the_chunk_planner_receives_the_measured_size(self):
        source = (FFmWiz.Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "encoding.py").read_text(encoding="utf-8")
        block = source.split("def run_segmented_reverse_main_encode")[1][:1500]
        self.assertIn("reverse_segment_seconds(answers)", block)
        self.assertIn("split_ranges_for_reverse_segments(original_keep_ranges, duration,",
                      block)

    def test_the_notice_reports_the_size_actually_used(self):
        # It used to print the constant while chunking by something else would
        # have made the message wrong; keep the printed number derived.
        source = (FFmWiz.Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "encoding.py").read_text(encoding="utf-8")
        block = source.split("def run_segmented_reverse_main_encode")[1][:2000]
        self.assertIn("{segment_seconds:.0f}s", block)
        self.assertNotIn("{int(REVERSE_SEGMENT_SECONDS)}s", block)


if __name__ == "__main__":
    unittest.main()
