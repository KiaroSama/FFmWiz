"""Regression: the reverse segment must fit a PEAK budget (B05, F10, D09-D12).

Three rounds of the same defect.

First the executor chunked a reverse into fixed 60-second pieces and told the
user that avoided buffering the whole video. `reverse` holds every decoded
frame, so 60 seconds is 1.03 GiB at 480p30, 5.21 GiB at 1080p30 and 20.86 GiB
at 4K30 -- the segment size defeated the promise it advertised.

Sizing by seconds against a frame-buffer budget fixed that case and introduced
the next one: the calculation assumed 8-bit 4:2:0 at a flat 1.5 bytes per pixel
and clamped the result to a 2-second floor. Neither holds up.

    2-second floor        4K60 8-bit    1.39 GiB
                          4K60 10-bit   2.78 GiB
                          8K60 8-bit    5.56 GiB
    flat 1.5 bytes/pixel  understates yuv420p10le by 2x and yuv444p12le by 4x

So the budget became a PEAK: decoded bytes derived from the real `pix_fmt`, a
measured safety factor for filter queues, and a reserved fixed overhead for the
decoder/encoder working set.

The third round is that the peak was still not a cap. The floor moved from the
calculator into the SPLITTER (`max(1.0, segment_seconds)`), the pixel-format
estimate was inferred from the format name, the geometry came from the source
rather than from the `reverse` filter's input, and an unknown geometry was
assumed rather than refused. Those four live in `test_reverse_budget_policy.py`
and `test_pixel_format_bytes.py`; this module keeps the peak arithmetic honest.

The overhead figure is measured, not guessed: 720p30 buffering 300 frames
peaked at 878 MB against a 415 MB frame estimate, and 600 frames at 1306 MB --
1.43 MB per frame against a theoretical 1.38, on a ~450 MB baseline.
"""
import math
import unittest

import FFmWiz


def _answers(width, height, fps, pix_fmt="yuv420p"):
    return {"video_streams": [{"width": width, "height": height,
                               "pix_fmt": pix_fmt}],
            "fps": fps}


def _peak_bytes(width, height, fps, pix_fmt, seconds):
    per_frame = (width * height * FFmWiz.decoded_bytes_per_pixel(pix_fmt)
                 * FFmWiz.REVERSE_FRAME_SAFETY)
    frames = math.ceil(seconds * fps - 1e-9)
    return FFmWiz.REVERSE_FIXED_OVERHEAD_BYTES + per_frame * frames


class DecodedPixelSize(unittest.TestCase):
    """The flat 1.5 bytes/pixel assumption, replaced by a measured table.

    The full sweep over every format the installed FFmpeg reports lives in
    `test_pixel_format_bytes.py`; these are the layouts the budget cases below
    depend on.
    """

    def test_eight_bit_layouts(self):
        self.assertEqual(1.5, FFmWiz.decoded_bytes_per_pixel("yuv420p"))
        self.assertEqual(1.5, FFmWiz.decoded_bytes_per_pixel("nv12"))
        self.assertEqual(2.0, FFmWiz.decoded_bytes_per_pixel("yuv422p"))
        self.assertEqual(3.0, FFmWiz.decoded_bytes_per_pixel("yuv444p"))

    def test_depth_multiplies_the_whole_frame(self):
        self.assertEqual(3.0, FFmWiz.decoded_bytes_per_pixel("yuv420p10le"))
        self.assertEqual(6.0, FFmWiz.decoded_bytes_per_pixel("yuv444p12le"))

    def test_rgb_and_gray_and_alpha(self):
        self.assertEqual(3.0, FFmWiz.decoded_bytes_per_pixel("rgb24"))
        self.assertEqual(1.0, FFmWiz.decoded_bytes_per_pixel("gray"))
        self.assertEqual(2.5, FFmWiz.decoded_bytes_per_pixel("yuva420p"))

    def test_an_unknown_name_is_conservative_rather_than_smallest(self):
        # It used to answer 1.5 -- the SMALLEST common layout -- so the cap held
        # only for formats the name reader had heard of. A number is still
        # returned so callers that only want an estimate keep working; callers
        # that must refuse ask `pixel_format_bytes_per_pixel` for the None.
        for unknown in ("something_new", None):
            with self.subTest(pix_fmt=unknown):
                self.assertEqual(FFmWiz.UNKNOWN_PIXEL_FORMAT_BYTES,
                                 FFmWiz.decoded_bytes_per_pixel(unknown))
                self.assertIsNone(FFmWiz.pixel_format_bytes_per_pixel(unknown))
        self.assertGreater(FFmWiz.UNKNOWN_PIXEL_FORMAT_BYTES, 1.5)


class TheSegmentFitsThePeakBudget(unittest.TestCase):
    CASES = [
        ("480p24 8-bit", 854, 480, 24, "yuv420p"),
        ("720p30 8-bit", 1280, 720, 30, "yuv420p"),
        ("720p120 8-bit", 1280, 720, 120, "yuv420p"),
        ("1080p30 8-bit", 1920, 1080, 30, "yuv420p"),
        ("1080p60 8-bit", 1920, 1080, 60, "yuv420p"),
        ("1080p60 10-bit", 1920, 1080, 60, "yuv420p10le"),
        ("4K30 8-bit", 3840, 2160, 30, "yuv420p"),
        ("4K60 10-bit", 3840, 2160, 60, "yuv420p10le"),
        ("4K30 12-bit 4:4:4", 3840, 2160, 30, "yuv444p12le"),
        ("8K60 8-bit", 7680, 4320, 60, "yuv420p"),
    ]

    def test_every_geometry_stays_inside_the_cap(self):
        for label, width, height, fps, pix_fmt in self.CASES:
            with self.subTest(geometry=label):
                seconds = FFmWiz.reverse_segment_seconds(
                    _answers(width, height, fps, pix_fmt))
                peak = _peak_bytes(width, height, fps, pix_fmt, seconds)
                self.assertLessEqual(
                    peak, FFmWiz.REVERSE_PEAK_BUDGET_BYTES,
                    f"{label}: peak {peak / 1024 ** 3:.2f} GiB against a "
                    f"{FFmWiz.REVERSE_PEAK_BUDGET_BYTES / 1024 ** 3:.2f} GiB cap")

    def test_the_old_two_second_floor_really_did_overrun(self):
        # Guard the guard: if the cap grew enough for a 2 s 8K60 segment, the
        # assertions above would prove nothing about the defect.
        held = _peak_bytes(7680, 4320, 60, "yuv420p", 2.0)
        self.assertGreater(held, 4 * 1024 ** 3,
                           "2 s of 8K60 should be several GiB")

    def test_bit_depth_shortens_the_segment(self):
        eight = FFmWiz.reverse_segment_seconds(_answers(1920, 1080, 60, "yuv420p"))
        ten = FFmWiz.reverse_segment_seconds(_answers(1920, 1080, 60, "yuv420p10le"))
        self.assertAlmostEqual(eight / 2.0, ten, delta=0.2,
                               msg="10-bit frames are twice the size")

    def test_chroma_subsampling_shortens_it_too(self):
        subsampled = FFmWiz.reverse_segment_seconds(_answers(1920, 1080, 30, "yuv420p"))
        full = FFmWiz.reverse_segment_seconds(_answers(1920, 1080, 30, "yuv444p"))
        self.assertGreater(subsampled, full)

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

    def test_there_is_no_floor_that_breaks_the_cap(self):
        # The specific defect: a minimum segment length applied regardless of
        # frame size. An extreme geometry must get a SHORT segment, not a
        # floor that blows the budget.
        seconds = FFmWiz.reverse_segment_seconds(
            _answers(15360, 8640, 120, "yuv444p12le"))
        peak = _peak_bytes(15360, 8640, 120, "yuv444p12le", seconds)
        self.assertLess(seconds, 1.0)
        self.assertLessEqual(peak, FFmWiz.REVERSE_PEAK_BUDGET_BYTES,
                             "even one frame of this is huge; it must not be "
                             "multiplied by a floor")

    def test_the_overhead_is_actually_reserved(self):
        # A budget that spent the whole cap on frames would leave nothing for
        # the decoder and encoder, which measured ~450 MB before any buffering.
        self.assertGreater(FFmWiz.REVERSE_FIXED_OVERHEAD_BYTES, 256 * 1024 ** 2)
        self.assertLess(FFmWiz.REVERSE_FIXED_OVERHEAD_BYTES,
                        FFmWiz.REVERSE_PEAK_BUDGET_BYTES)


class UnknownGeometryIsRefusedNotAssumed(unittest.TestCase):
    """The assumption that replaced the 60 s fallback was still not a bound.

    Returning 60 s for an unknown source is 20.9 GiB of frames at 4K30, and the
    caller cannot tell it was a guess. Assuming 1080p60 10-bit instead answers
    a smaller number and is exactly as unbounded: nothing stops the real source
    being 8K. The plan now refuses, and the refusal names what is missing.
    """

    def test_an_unknown_geometry_is_a_planning_error(self):
        with self.assertRaises(FFmWiz.ReverseBudgetError) as caught:
            FFmWiz.reverse_segment_seconds({})
        self.assertIn("geometry", str(caught.exception))

    def test_zero_dimensions_are_treated_the_same(self):
        with self.assertRaises(FFmWiz.ReverseBudgetError):
            FFmWiz.reverse_segment_seconds(
                {"video_streams": [{"width": 0, "height": 0}]})

    def test_it_does_not_quietly_fall_back_to_the_ceiling(self):
        # The failure mode this replaces: a number that looks planned but is
        # really REVERSE_SEGMENT_SECONDS wearing a hat.
        with self.assertRaises(FFmWiz.ReverseBudgetError):
            FFmWiz.reverse_segment_seconds_for(None, None, None, None)

    def test_the_explicit_override_says_it_is_no_longer_hard_capped(self):
        plan = FFmWiz.reverse_segment_plan(None, None, None, None,
                                           best_effort=True)
        self.assertFalse(plan.hard_capped)
        self.assertTrue(plan.assumptions)
        self.assertLess(plan.seconds, FFmWiz.REVERSE_SEGMENT_SECONDS)

    def test_the_override_assumption_still_fits_the_cap_it_assumed(self):
        plan = FFmWiz.reverse_segment_plan(None, None, None, None,
                                           best_effort=True)
        peak = _peak_bytes(plan.width, plan.height, plan.fps, plan.pix_fmt,
                           plan.seconds)
        self.assertLessEqual(peak, FFmWiz.REVERSE_PEAK_BUDGET_BYTES)


class EveryReverseEntryPointSharesTheBudget(unittest.TestCase):
    """The standalone Video Speed / Reverse mode had its own unbudgeted call.

    It passed no segment size at all and announced a flat 60 s window, which is
    20.9 GiB of decoded frames at 4K30 -- the promise the message makes is the
    one it broke (B06).
    """

    def test_the_pure_calculator_is_reachable_from_the_support_layer(self):
        # It lives beside the chunk splitter precisely so the standalone mode
        # can use it; the executor's own wrapper sits a layer above and cannot
        # be imported from there.
        from ffmwiz.support import ext04b
        self.assertTrue(hasattr(ext04b, "reverse_segment_seconds_for"))

    def test_the_standalone_mode_passes_a_size_to_the_splitter(self):
        source = (FFmWiz.Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "support" / "ext04b.py").read_text(encoding="utf-8")
        self.assertIn("reverse_segment_seconds_for(", source)
        self.assertIn("split_ranges_for_reverse_segments([], duration, segment_seconds)",
                      source)
        self.assertNotIn("split_ranges_for_reverse_segments([], duration)", source)

    def test_the_standalone_notice_does_not_reuse_the_flat_ceiling(self):
        # The notice must report the size actually planned. Whether it prints
        # seconds or milliseconds is the caller's call -- but see
        # `test_reverse_budget_policy.TheWindowIsPrintable`: a `:.0f}s` format
        # announces every legitimate subsecond window as `0s`.
        source = (FFmWiz.Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "support" / "ext04b.py").read_text(encoding="utf-8")
        self.assertNotIn("{int(REVERSE_SEGMENT_SECONDS)}s", source)

    def test_both_entry_points_agree_on_the_same_geometry(self):
        # The executor's wrapper and the pure function must not drift apart.
        answers = _answers(3840, 2160, 30, "yuv420p")
        stream = answers["video_streams"][0]
        self.assertAlmostEqual(
            FFmWiz.reverse_segment_seconds(answers),
            FFmWiz.reverse_segment_seconds_for(
                stream["width"], stream["height"], answers["fps"], stream["pix_fmt"]),
            places=6)


class TheExecutorUsesIt(unittest.TestCase):
    def test_the_chunk_planner_receives_the_measured_size(self):
        source = (FFmWiz.Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "encoding.py").read_text(encoding="utf-8")
        block = source.split("def run_segmented_reverse_main_encode")[1][:1500]
        self.assertIn("reverse_segment_plan_for(answers)", block)
        # Renamed when the plan gained its calculation and its
        # refusals; the old wrapper still exists for callers that only
        # want the number.
        self.assertIn("split_ranges_for_reverse_segments(original_keep_ranges, duration,",
                      block)

    def test_the_notice_does_not_reuse_the_flat_ceiling(self):
        source = (FFmWiz.Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "encoding.py").read_text(encoding="utf-8")
        block = source.split("def run_segmented_reverse_main_encode")[1][:2000]
        self.assertNotIn("{int(REVERSE_SEGMENT_SECONDS)}s", block)


if __name__ == "__main__":
    unittest.main()
