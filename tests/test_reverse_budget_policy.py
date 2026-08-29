"""Regression: the reverse memory cap has to survive the whole plan (D09-D12).

Three separate ways the advertised 2 GiB peak stopped being a cap.

D09 -- the splitter reinstated a one-second minimum. The calculator returns a
subsecond window for anything large, and `max(1.0, segment_seconds)` threw it
away, so the executor always ran at least a second of decoded frames:

    case                calculator   executor   peak at the executor's window
    4K60 10-bit          933 ms       1.000 s    2.099 GiB
    8K60 8-bit           467 ms       1.000 s    3.698 GiB
    8K60 10-bit          233 ms       1.000 s    6.897 GiB
    16K120 12-bit 4:4:4    8 ms       1.000 s  102.838 GiB

The smallest legal chunk is one FRAME. A window is planned in whole frames and
printed in milliseconds, because `f"{0.2333:.0f}s"` announces a real 233 ms
window as `0s`.

D11 -- `reverse` buffers POST-filter frames and the budget read the SOURCE
stream. The CPU graph is crop -> fps -> scale/pad -> speed/reverse -> format,
so a 1080p30 clip upscaled to 8K was given the 1080p30 window of 15.0 s, whose
real peak at 8K is 24.5 GiB. `format=` sits downstream of `reverse`, and
`reverse` passes formats through, so the buffered frames carry the graph's
negotiated format rather than the source's.

D12 -- unknown geometry assumed 1080p60 10-bit and carried on, and `max(1, ...)`
still returned one frame when a single frame plus overhead already exceeded the
allowance. A warning does not turn an unbounded allocation into a bound: an
unknown that the plan needs is a planning error, and a frame that does not fit
is a refusal.
"""
import math
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz
from ffmwiz import encoding
from ffmwiz.support import L00_split
from ffmwiz import reverse_stages

CAP = FFmWiz.REVERSE_PEAK_BUDGET_BYTES
OVERHEAD = FFmWiz.REVERSE_FIXED_OVERHEAD_BYTES

# The geometries the audit measured, plus the 8K120 and synthetic 16K cases the
# mandatory regressions call for.
GEOMETRIES = [
    ("4K60 10-bit", 3840, 2160, 60.0, "yuv420p10le"),
    ("8K60 8-bit", 7680, 4320, 60.0, "yuv420p"),
    ("8K60 10-bit", 7680, 4320, 60.0, "yuv420p10le"),
    ("8K120 8-bit", 7680, 4320, 120.0, "yuv420p"),
    ("16K120 12-bit 4:4:4", 15360, 8640, 120.0, "yuv444p12le"),
    ("1080p30 8-bit", 1920, 1080, 30.0, "yuv420p"),
    ("4K24000/1001 10-bit", 3840, 2160, 24000 / 1001.0, "yuv420p10le"),
    ("8K60000/1001 8-bit", 7680, 4320, 60000 / 1001.0, "yuv420p"),
]


def peak_bytes(width, height, pix_fmt, seconds, fps):
    """What holding `seconds` of this stream really costs, overhead included."""
    per_frame = (width * height * FFmWiz.decoded_bytes_per_pixel(pix_fmt)
                 * FFmWiz.REVERSE_FRAME_SAFETY)
    return OVERHEAD + per_frame * math.ceil(seconds * fps - 1e-9)


def segment_answers(width, height, fps, pix_fmt, duration=120.0):
    """Enough state for the real reverse segment command builder."""
    return {
        "ffmpeg": "ffmpeg", "ffprobe": "ffprobe",
        "input_path": Path("clip.mkv"),
        "output_location": Path("out"),
        "output_ext": "mkv",
        "video_streams": [{"codec_type": "video", "codec_name": "h264",
                           "width": width, "height": height,
                           "avg_frame_rate": f"{fps}/1", "pix_fmt": pix_fmt,
                           "color_range": "tv"}],
        "audio_streams": [], "subtitle_streams": [], "data_streams": [],
        "audio_tracks": [], "subtitle_tracks": [],
        "video_codec": "H265", "use_gpu": False,
        "crf": 20, "preset": "medium", "resolution": "n",
        "format": {"duration": f"{duration}"},
        "color_range_choice": "tv",
        "video_speed_enabled": True, "video_speed_factor": 1.0,
        "reverse_video": True, "fps": fps,
    }


class TheSplitterHasNoOneSecondFloor(unittest.TestCase):
    """D09: the calculated window must reach the chunks unchanged."""

    def test_a_subsecond_window_survives_the_splitter(self):
        chunks = FFmWiz.split_ranges_for_reverse_segments([(0.0, 1.0)], 1.0, 0.2333)
        self.assertGreater(len(chunks), 4, "a 233 ms window over 1 s is 5 chunks")
        for start, end in chunks:
            self.assertLessEqual(end - start, 0.2333 + 1e-9)

    def test_every_geometry_stays_inside_the_cap_after_splitting(self):
        for label, width, height, fps, pix_fmt in GEOMETRIES:
            with self.subTest(geometry=label):
                plan = FFmWiz.reverse_segment_plan(width, height, fps, pix_fmt)
                chunks = FFmWiz.split_ranges_for_reverse_segments(
                    [(0.0, 60.0)], 60.0, plan.seconds, fps)
                self.assertTrue(chunks)
                worst = max(end - start for start, end in chunks)
                peak = peak_bytes(width, height, pix_fmt, worst, fps)
                self.assertLessEqual(
                    peak, CAP,
                    f"{label}: widest chunk {worst:.6f} s holds "
                    f"{peak / 1024 ** 3:.3f} GiB against a "
                    f"{CAP / 1024 ** 3:.2f} GiB cap")

    def test_the_old_floor_really_did_overrun_every_one_of_them(self):
        # Guard the guard: the assertions above prove nothing unless a
        # one-second chunk was genuinely over the cap for these geometries.
        overruns = {}
        for label, width, height, fps, pix_fmt in GEOMETRIES:
            plan = FFmWiz.reverse_segment_plan(width, height, fps, pix_fmt)
            if plan.seconds >= 1.0:
                continue  # 1080p30 and friends were never affected by the floor
            overruns[label] = peak_bytes(width, height, pix_fmt, 1.0, fps)
        self.assertGreaterEqual(len(overruns), 5)
        for label, peak in overruns.items():
            with self.subTest(geometry=label):
                self.assertGreater(peak, CAP, f"{label} at 1 s is only "
                                              f"{peak / 1024 ** 3:.3f} GiB")

    def test_chunks_are_whole_frames_when_the_rate_is_known(self):
        for fps in (60.0, 24000 / 1001.0, 60000 / 1001.0):
            with self.subTest(fps=fps):
                chunks = FFmWiz.split_ranges_for_reverse_segments(
                    [(0.0, 5.0)], 5.0, 0.5, fps)
                held = (chunks[0][1] - chunks[0][0]) * fps
                frames = round(held)
                self.assertGreaterEqual(frames, 1)
                self.assertLessEqual(held, frames,
                                     "a chunk must not reach past a frame boundary")
                self.assertGreater(held, frames - 1e-3,
                                   "a chunk must still hold whole frames")

    def test_a_chunk_is_not_rounded_past_a_frame_when_the_command_prints_it(self):
        # FFmpeg time arguments are emitted as `f"{value:.6f}"`, which ROUNDS.
        # 28 frames at 60 fps is 0.4666666 s and printed `-t 0.466667`, two
        # microseconds past frame 28, so the decoder read a 29th frame into the
        # buffer and the segment was one frame over its own cap.
        for fps, requested in ((60.0, 28 / 60.0), (24000 / 1001.0, 56 * 1001 / 24000.0),
                               (120.0, 1 / 120.0)):
            with self.subTest(fps=fps):
                chunk = FFmWiz.split_ranges_for_reverse_segments(
                    [(0.0, 30.0)], 30.0, requested, fps)[0]
                emitted = float(f"{chunk[1] - chunk[0]:.6f}")
                self.assertLessEqual(math.ceil(emitted * fps - 1e-9),
                                     round(requested * fps),
                                     "the printed window reads an extra frame")

    def test_a_single_frame_is_a_legal_chunk(self):
        # The minimum the old floor forbade: 1/120 s, not 1 s.
        fps = 120.0
        chunks = FFmWiz.split_ranges_for_reverse_segments(
            [(0.0, 0.05)], 0.05, 1.0 / fps, fps)
        self.assertGreaterEqual(len(chunks), 6, "0.05 s of 120 fps is 6 frames")
        for start, end in chunks:
            self.assertLessEqual(end - start, 1.0 / fps + 1e-9)
        self.assertEqual(0.0, chunks[0][0])
        self.assertAlmostEqual(0.05, chunks[-1][1], places=9)
        for (_, end), (start, _) in zip(chunks, chunks[1:]):
            self.assertEqual(end, start, "the chunks must tile without a gap")

    def test_a_missing_or_zero_size_still_falls_back_to_the_ceiling(self):
        for value in (None, 0.0, -3.0):
            with self.subTest(segment_seconds=value):
                chunks = FFmWiz.split_ranges_for_reverse_segments(
                    [(0.0, 200.0)], 200.0, value)
                self.assertEqual(FFmWiz.REVERSE_SEGMENT_SECONDS,
                                 chunks[0][1] - chunks[0][0])

    def test_existing_callers_keep_working_positionally(self):
        # encoding.py and ext04b.py call this with three positional arguments.
        self.assertEqual([(0.0, 2.0), (2.0, 4.0)],
                         FFmWiz.split_ranges_for_reverse_segments([(0.0, 4.0)], 4.0, 2.0))


class TheWindowIsPrintable(unittest.TestCase):
    """D09: a valid subsecond window formatted as `0s` is a false statement."""

    def test_a_subsecond_window_is_not_announced_as_zero(self):
        plan = FFmWiz.reverse_segment_plan(7680, 4320, 60.0, "yuv420p10le")
        self.assertLess(plan.seconds, 1.0)
        self.assertEqual("0s", f"{plan.seconds:.0f}s", "the old notice format")
        self.assertIn("ms", plan.window_text)
        self.assertIn("14 frames", plan.window_text)

    def test_a_whole_second_window_reads_as_seconds(self):
        plan = FFmWiz.reverse_segment_plan(1920, 1080, 30.0, "yuv420p")
        self.assertIn("s (", plan.window_text)
        self.assertIn("450 frames", plan.window_text)

    def test_one_frame_is_singular(self):
        self.assertEqual("8 ms (1 frame)",
                         FFmWiz.format_reverse_segment_window(1 / 120.0, 120.0))

    def test_the_calculation_is_logged_in_full(self):
        # D12 asks for dimensions, fps, format, bytes/frame, frame count,
        # overhead and peak, so the number in a notice can be checked afterwards.
        text = FFmWiz.reverse_segment_plan(3840, 2160, 60.0, "yuv420p10le").describe()
        for fragment in ("3840x2160", "yuv420p10le", "60 fps", "3 B/px",
                         "MiB/frame", "frame(s)", "peak", "overhead", "cap"):
            self.assertIn(fragment, text)


class TheBudgetFollowsTheReverseFilterInput(unittest.TestCase):
    """D11: `reverse` buffers what the filter chain hands it, not the source."""

    def test_an_upscale_before_reverse_is_sized_at_the_upscale(self):
        source = FFmWiz.reverse_segment_plan(1920, 1080, 30.0, "yuv420p")
        descriptor = FFmWiz.reverse_filter_input_descriptor(
            1920, 1080, 30.0, "yuv420p", scale_size=(7680, 4320))
        self.assertEqual((7680, 4320, 30.0, "yuv420p"), tuple(descriptor))
        scaled = FFmWiz.reverse_segment_plan(*descriptor)

        # The defect, measured: the source window applied to the scaled frames.
        wrong = peak_bytes(7680, 4320, "yuv420p", source.seconds, 30.0)
        self.assertGreater(wrong, CAP * 10,
                           f"{wrong / 1024 ** 3:.3f} GiB should dwarf the cap")
        self.assertLessEqual(scaled.peak_bytes, CAP)
        self.assertLess(scaled.seconds, source.seconds)

    def test_an_upscale_that_also_raises_the_rate_is_sized_at_both(self):
        descriptor = FFmWiz.reverse_filter_input_descriptor(
            1920, 1080, 30.0, "yuv420p", scale_size=(7680, 4320), output_fps=60.0)
        self.assertEqual(60.0, descriptor.fps)
        plan = FFmWiz.reverse_segment_plan(*descriptor)
        self.assertLessEqual(plan.peak_bytes, CAP)
        self.assertLessEqual(
            peak_bytes(7680, 4320, "yuv420p", plan.seconds, 60.0), CAP)

    def test_a_downscale_before_reverse_earns_a_longer_window(self):
        source = FFmWiz.reverse_segment_plan(7680, 4320, 60.0, "yuv420p")
        descriptor = FFmWiz.reverse_filter_input_descriptor(
            7680, 4320, 60.0, "yuv420p", scale_size=(1280, 720))
        shrunk = FFmWiz.reverse_segment_plan(*descriptor)
        self.assertGreater(shrunk.seconds, source.seconds,
                           "sizing a 720p buffer as if it were 8K wastes 97% "
                           "of the allowance on frames that do not exist")
        self.assertLessEqual(shrunk.peak_bytes, CAP)

    def test_a_scale_after_a_crop_wins_because_it_is_last(self):
        descriptor = FFmWiz.reverse_filter_input_descriptor(
            3840, 2160, 30.0, "yuv420p",
            crop_size=(1920, 1080), scale_size=(3840, 2160))
        self.assertEqual((3840, 2160), (descriptor.width, descriptor.height))

    def test_a_crop_with_no_resize_is_what_reverse_sees(self):
        descriptor = FFmWiz.reverse_filter_input_descriptor(
            3840, 2160, 30.0, "yuv420p", crop_size=(1920, 800))
        self.assertEqual((1920, 800), (descriptor.width, descriptor.height))
        self.assertGreater(FFmWiz.reverse_segment_plan(*descriptor).seconds,
                           FFmWiz.reverse_segment_plan(3840, 2160, 30.0, "yuv420p").seconds)

    def test_an_aspect_preserving_pad_is_sized_at_the_canvas(self):
        # `scale=W:H:force_original_aspect_ratio=decrease` then `pad=W:H` puts
        # the full canvas into the buffer, letterbox included, so a 4:3 source
        # on a 16:9 canvas still costs the whole canvas.
        descriptor = FFmWiz.reverse_filter_input_descriptor(
            1440, 1080, 30.0, "yuv420p", scale_size=(3840, 2160))
        self.assertEqual((3840, 2160), (descriptor.width, descriptor.height))

    def test_the_graph_format_beats_the_source_format(self):
        # `format=` is DOWNSTREAM of `reverse` and `reverse` passes formats
        # through, so FFmpeg negotiates the graph format back up the chain and
        # the buffered frames are in it, not in the source's.
        descriptor = FFmWiz.reverse_filter_input_descriptor(
            3840, 2160, 30.0, "yuv420p", graph_pix_fmt="yuv444p12le")
        self.assertEqual("yuv444p12le", descriptor.pix_fmt)
        self.assertLess(FFmWiz.reverse_segment_plan(*descriptor).seconds,
                        FFmWiz.reverse_segment_plan(3840, 2160, 30.0, "yuv420p").seconds)

    def test_the_real_graph_really_does_put_format_after_reverse(self):
        # The claim above, checked against the builder instead of asserted.
        answers = segment_answers(3840, 2160, 60.0, "yuv420p10le")
        chain = FFmWiz.build_cpu_video_filter(dict(answers)) or ""
        self.assertIn("reverse", chain)
        self.assertIn("format=", chain)
        self.assertLess(chain.index("reverse"), chain.index("format="))

    def test_an_absent_stage_falls_back_to_the_source(self):
        self.assertEqual(
            (1920, 1080, 30.0, "yuv420p"),
            tuple(FFmWiz.reverse_filter_input_descriptor(1920, 1080, 30.0, "yuv420p")))


class AnUnknownIsARefusalNotAnAssumption(unittest.TestCase):
    """D12: the plan needs these values; guessing them removes the bound."""

    def test_unknown_width_is_refused(self):
        with self.assertRaises(FFmWiz.ReverseBudgetError) as caught:
            FFmWiz.reverse_segment_plan(0, 2160, 60.0, "yuv420p")
        self.assertIn("geometry", str(caught.exception))

    def test_unknown_height_is_refused(self):
        with self.assertRaises(FFmWiz.ReverseBudgetError):
            FFmWiz.reverse_segment_plan(3840, None, 60.0, "yuv420p")

    def test_unknown_frame_rate_is_refused(self):
        for fps in (0, None, -5, float("nan"), float("inf"), "abc"):
            with self.subTest(fps=fps):
                with self.assertRaises(FFmWiz.ReverseBudgetError) as caught:
                    FFmWiz.reverse_segment_plan(3840, 2160, fps, "yuv420p")
                self.assertIn("frame rate", str(caught.exception))

    def test_unknown_pixel_format_is_refused(self):
        for pix_fmt in (None, "", "something_new"):
            with self.subTest(pix_fmt=pix_fmt):
                with self.assertRaises(FFmWiz.ReverseBudgetError) as caught:
                    FFmWiz.reverse_segment_plan(3840, 2160, 60.0, pix_fmt)
                self.assertIn("cannot size frames", str(caught.exception))

    def test_a_hardware_surface_says_so(self):
        with self.assertRaises(FFmWiz.ReverseBudgetError) as caught:
            FFmWiz.reverse_segment_plan(3840, 2160, 60.0, "cuda")
        self.assertIn("hardware surface", str(caught.exception))

    def test_everything_unknown_at_once_is_still_refused(self):
        with self.assertRaises(FFmWiz.ReverseBudgetError):
            FFmWiz.reverse_segment_plan(None, None, None, None)

    def test_the_float_wrapper_refuses_the_same_way(self):
        with self.assertRaises(FFmWiz.ReverseBudgetError):
            FFmWiz.reverse_segment_seconds_for(None, None, None, None)

    def test_the_override_is_explicit_and_drops_the_claim(self):
        plan = FFmWiz.reverse_segment_plan(None, None, None, None, best_effort=True)
        self.assertFalse(plan.hard_capped)
        self.assertEqual(3, len(plan.assumptions))
        self.assertIn("ASSUMED", plan.describe())
        self.assertLessEqual(plan.peak_bytes, CAP)

    def test_a_complete_descriptor_is_hard_capped(self):
        plan = FFmWiz.reverse_segment_plan(3840, 2160, 60.0, "yuv420p10le")
        self.assertTrue(plan.hard_capped)
        self.assertEqual((), plan.assumptions)

    def test_the_assumed_geometry_is_no_longer_1080p(self):
        # 1080p60 10-bit was the old guess, and it is smaller than most sources
        # anyone reverses in anger, so the guess itself broke the cap.
        plan = FFmWiz.reverse_segment_plan(None, None, None, "yuv420p10le",
                                           best_effort=True)
        self.assertGreaterEqual(plan.width * plan.height, 3840 * 2160)


class AFrameThatDoesNotFitIsRefused(unittest.TestCase):
    """D12: `max(1, ...)` returned a chunk that broke the cap by itself."""

    HUGE = (15360, 8640, 120.0, "yuv444p12le")  # 873 MiB per frame

    def test_one_frame_over_the_allowance_raises(self):
        with self.assertRaises(FFmWiz.ReverseBudgetError) as caught:
            FFmWiz.reverse_segment_plan(*self.HUGE, cap_bytes=OVERHEAD + 1024 ** 2)
        message = str(caught.exception)
        self.assertIn("One decoded", message)
        self.assertIn("MiB", message)

    def test_the_override_returns_one_frame_and_says_it_is_best_effort(self):
        plan = FFmWiz.reverse_segment_plan(
            *self.HUGE, cap_bytes=OVERHEAD + 1024 ** 2, best_effort=True)
        self.assertEqual(1, plan.frames)
        self.assertFalse(plan.hard_capped)
        self.assertGreater(plan.peak_bytes, plan.cap_bytes,
                           "the override is honest about exceeding the cap")

    def test_the_default_cap_still_fits_one_16k_frame(self):
        # Guard the guard: with the shipped cap this geometry is bounded, so the
        # refusal above is about the allowance and not about 16K in general.
        plan = FFmWiz.reverse_segment_plan(*self.HUGE)
        self.assertEqual(1, plan.frames)
        self.assertTrue(plan.hard_capped)
        self.assertLessEqual(plan.peak_bytes, CAP)


class AnInvalidCapIsAlwaysAnError(unittest.TestCase):
    """D12: a cap is configurable, which means it can be configured wrong."""

    def test_zero_and_negative_caps_raise(self):
        for cap in (0, -1, -(1024 ** 3)):
            with self.subTest(cap_bytes=cap):
                with self.assertRaises(FFmWiz.ReverseBudgetError) as caught:
                    FFmWiz.reverse_segment_plan(1920, 1080, 30.0, "yuv420p",
                                                cap_bytes=cap)
                self.assertIn("must be positive", str(caught.exception))

    def test_a_cap_below_the_fixed_overhead_raises(self):
        for cap in (OVERHEAD, OVERHEAD // 2, 1):
            with self.subTest(cap_bytes=cap):
                with self.assertRaises(FFmWiz.ReverseBudgetError) as caught:
                    FFmWiz.reverse_segment_plan(1920, 1080, 30.0, "yuv420p",
                                                cap_bytes=cap)
                self.assertIn("reserves", str(caught.exception))

    def test_best_effort_does_not_excuse_an_invalid_cap(self):
        # An unknown is something we could not read; an impossible cap is
        # something the operator typed. There is no honest segment for it.
        for cap in (0, -1, OVERHEAD):
            with self.subTest(cap_bytes=cap):
                with self.assertRaises(FFmWiz.ReverseBudgetError):
                    FFmWiz.reverse_segment_plan(1920, 1080, 30.0, "yuv420p",
                                                cap_bytes=cap, best_effort=True)

    def test_a_smaller_cap_really_shortens_the_window(self):
        wide = FFmWiz.reverse_segment_plan(1920, 1080, 30.0, "yuv420p")
        tight = FFmWiz.reverse_segment_plan(1920, 1080, 30.0, "yuv420p",
                                            cap_bytes=OVERHEAD + 256 * 1024 ** 2)
        self.assertLess(tight.seconds, wide.seconds)
        self.assertLessEqual(tight.peak_bytes, tight.cap_bytes)

    def test_the_shipped_cap_is_configurable_from_the_environment(self):
        source = (Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "core" / "constants.py").read_text(encoding="utf-8")
        self.assertIn("FFMWIZ_REVERSE_PEAK_BUDGET_MB", source)


class NoFFmpegStartsOnARejectedPlan(unittest.TestCase):
    """D12: refusing after the encoder is running is not refusing."""

    def _spy_run(self, width, height, fps, pix_fmt, duration=120.0):
        """The executor's shape: plan -> split -> build -> run."""
        commands = []
        with mock.patch.object(subprocess, "Popen") as popen, \
                mock.patch.object(subprocess, "run") as run:
            answers = segment_answers(width, height, fps, pix_fmt, duration)
            plan = FFmWiz.reverse_segment_plan(width, height, fps, pix_fmt)
            for start, end in FFmWiz.split_ranges_for_reverse_segments(
                    [(0.0, duration)], duration, plan.seconds, plan.fps):
                commands.append([str(part) for part in
                                 reverse_stages.build_main_encode_reverse_segment_command(
                                     answers, start, end, Path("out/seg.mkv"))])
            return plan, commands, popen, run

    def test_a_rejected_plan_builds_no_command_and_starts_no_process(self):
        with mock.patch.object(subprocess, "Popen") as popen, \
                mock.patch.object(subprocess, "run") as run:
            with self.assertRaises(FFmWiz.ReverseBudgetError):
                self._spy_run(0, 0, 0, None)
            popen.assert_not_called()
            run.assert_not_called()

    def test_an_accepted_plan_bounds_the_source_window_in_every_command(self):
        # The mandatory calculator -> splitter -> command-builder assertion.
        for label, width, height, fps, pix_fmt in GEOMETRIES:
            with self.subTest(geometry=label):
                plan, commands, popen, run = self._spy_run(
                    width, height, fps, pix_fmt, duration=8.0)
                popen.assert_not_called()
                run.assert_not_called()
                self.assertTrue(commands)
                for cmd in commands:
                    first_input = cmd.index("-i")
                    pre_input = cmd[:first_input]
                    self.assertIn("-t", pre_input, "the decode is not bounded")
                    window = float(pre_input[pre_input.index("-t") + 1])
                    self.assertLessEqual(
                        window, plan.seconds + 1e-5,
                        f"{label}: a segment reads {window:.6f} s against a "
                        f"{plan.seconds:.6f} s budget")
                    self.assertLessEqual(
                        peak_bytes(width, height, pix_fmt, window, fps), CAP)


class TheSharedEntryPointsStillLineUp(unittest.TestCase):
    """Every reverse caller has to reach the same budget (B06)."""

    def test_the_float_wrapper_agrees_with_the_plan(self):
        self.assertEqual(
            FFmWiz.reverse_segment_plan(3840, 2160, 30.0, "yuv420p").seconds,
            FFmWiz.reverse_segment_seconds_for(3840, 2160, 30.0, "yuv420p"))

    def test_the_pure_calculator_is_reachable_from_the_support_layer(self):
        for name in ("reverse_segment_plan", "reverse_segment_seconds_for",
                     "reverse_filter_input_descriptor",
                     "format_reverse_segment_window"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(L00_split, name))

    def test_the_executor_wrapper_agrees_with_the_pure_function(self):
        answers = segment_answers(3840, 2160, 30.0, "yuv420p")
        self.assertAlmostEqual(
            reverse_stages.reverse_segment_seconds(answers),
            FFmWiz.reverse_segment_seconds_for(3840, 2160, 30.0, "yuv420p"),
            places=9)


if __name__ == "__main__":
    unittest.main()
