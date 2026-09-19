"""Regression: audio reverse must never buffer the whole track (D13).

`areverse` holds its ENTIRE input in RAM, exactly as `reverse` does for video,
so peak memory tracks duration directly. The video side gained a segmented
executor; audio kept executing the generated one-shot command. Measured on a
six-hour synthetic input through the public standalone builder:

    DURATION_S 21600.0
    AREVERSE_COUNT 1
    INPUT_COUNT 1
    BOUNDS_PRESENT False
    SEGMENT_OR_MEMORY_POLICY False

which is about 8.3 GiB of decoded samples at 48 kHz stereo.

`run_bounded_audio_reverse()` is the one plan every audio-reverse entry point
shares:

  1. ONE forward decode into lossless chunks, cuts applied (continuous);
  2. reverse each chunk, lossless in and lossless out (bounded);
  3. concatenate the chunks in REVERSE order, because reverse(A||B) is
     exactly reverse(B)||reverse(A);
  4. the original job again, re-pointed at the reversed scratch, applying only
     the filters whose semantics stay continuous -- atempo, LoudNorm,
     resampling. Splitting one of those per chunk would change the result at
     every boundary, which is why the REVERSAL is staged and the filter graph
     is not.

A job that already fits the budget runs the one-shot command unchanged: it is
bounded by construction and staging it would cost an extra decode for nothing.

This module covers the PLAN only -- the arithmetic that decides how many
segments, how long, at what width, for which tracks. It needs no ffmpeg, which
is the point of separating it. Its two siblings cover the rest, along the same
seam as the source:

    test_bounded_audio_reverse_run.py      the executor, on real fixtures
    test_bounded_audio_reverse_callers.py  the modes and the join that reuse it

Fixtures the three share live in `audio_reverse_fixtures.py`.
"""

from __future__ import annotations

import unittest
from unittest import mock

import FFmWiz

from ffmwiz.support import ext04b, ext04c


class TheMemoryBudget(unittest.TestCase):
    """The arithmetic, without ffmpeg."""

    def test_the_budget_shrinks_as_the_stream_gets_heavier(self):
        stereo = ext04b.reverse_audio_segment_seconds_for(48000, 2, "s32")
        mono = ext04b.reverse_audio_segment_seconds_for(48000, 1, "s32")
        wide = ext04b.reverse_audio_segment_seconds_for(192000, 8, "s32")
        self.assertAlmostEqual(mono, stereo * 2, delta=1.0)
        self.assertLess(wide, stereo / 10)

    def test_a_segment_never_exceeds_the_shared_peak_budget(self):
        for rate, channels, fmt in ((48000, 2, "s32"), (44100, 1, "s16"),
                                    (192000, 8, "s32"), (8000, 1, "u8")):
            seconds = ext04b.reverse_audio_segment_seconds_for(rate, channels, fmt)
            held = (seconds * rate * channels
                    * ext04b.decoded_bytes_per_sample(fmt)
                    * FFmWiz.REVERSE_FRAME_SAFETY)
            self.assertLessEqual(
                held + FFmWiz.REVERSE_FIXED_OVERHEAD_BYTES,
                FFmWiz.REVERSE_PEAK_BUDGET_BYTES,
                f"{rate}Hz {channels}ch {fmt} exceeds the peak budget")

    def test_unknown_geometry_budgets_for_a_demanding_case(self):
        # Same policy as the video splitter: assume something expensive rather
        # than something convenient.
        self.assertEqual(ext04b.reverse_audio_segment_seconds_for(192000, 8, "s32"),
                         ext04b.reverse_audio_segment_seconds_for(None, None))

    def test_a_float_source_is_carried_at_24_bits_and_an_integer_one_is_not_widened(self):
        self.assertEqual("s32", ext04b.intermediate_audio_sample_fmt("fltp"))
        self.assertEqual("s32", ext04b.intermediate_audio_sample_fmt("s32p"))
        self.assertEqual("s16", ext04b.intermediate_audio_sample_fmt("s16p"))

    def test_more_than_eight_channels_leaves_flac_for_pcm(self):
        # FLAC tops out at eight channels; downmixing silently would be worse.
        self.assertIn("flac", ext04b.lossless_scratch_audio_args(
            [{"channels": 6, "sample_fmt": "s16p"}]))
        self.assertIn("pcm_s32le", ext04b.lossless_scratch_audio_args(
            [{"channels": 12, "sample_fmt": "fltp"}]))

    def test_the_widest_selected_track_also_sets_the_scratch_width(self):
        # Taking the FIRST stream's format carried a float-decoded second track
        # at 16 bits because track 0 happened to be 16-bit PCM.
        mixed = [{"channels": 2, "sample_fmt": "s16p"},
                 {"channels": 2, "sample_fmt": "fltp"}]
        self.assertEqual(["-c:a", "flac", "-sample_fmt", "s32"],
                         ext04b.lossless_scratch_audio_args(mixed))
        self.assertEqual(["-c:a", "flac", "-sample_fmt", "s16"],
                         ext04b.lossless_scratch_audio_args(mixed[:1]))

    # --- the aggregate budget (D05) -------------------------------------
    #
    # This class used to assert that the WIDEST selected track set the segment
    # length. That was the defect, written down: `areverse` buffers every
    # selected stream in one process, so their decoded buffers coexist and the
    # cost is the SUM. Eight worst-case tracks were planned at 227.951302 s,
    # an estimated 12.5 GiB peak against a 2.00 GiB cap -- 6.25x over.

    def _aggregate_peak_gib(self, streams, seconds):
        per_second = sum(
            stream["sample_rate"] * stream["channels"]
            * ext04b.decoded_bytes_per_sample(
                ext04b.intermediate_audio_sample_fmt(stream["sample_fmt"]))
            * FFmWiz.REVERSE_FRAME_SAFETY
            for stream in streams)
        return ((FFmWiz.REVERSE_FIXED_OVERHEAD_BYTES + per_second * seconds)
                / 1024 ** 3)

    def test_every_selected_track_is_paid_for(self):
        wide = {"sample_rate": 192000, "channels": 8, "sample_fmt": "fltp"}
        one = ext04b.reverse_audio_segment_seconds_for_streams([wide])
        eight = ext04b.reverse_audio_segment_seconds_for_streams([dict(wide)] * 8)
        self.assertAlmostEqual(one / 8, eight, delta=one / 800,
                               msg="eight identical tracks must cost eight times")

    def test_the_aggregate_peak_stays_inside_the_cap(self):
        cap = FFmWiz.REVERSE_PEAK_BUDGET_BYTES / 1024 ** 3
        cases = {
            "one 48k stereo": [{"sample_rate": 48000, "channels": 2, "sample_fmt": "s16p"}],
            "heterogeneous": [
                {"sample_rate": 44100, "channels": 1, "sample_fmt": "s16p"},
                {"sample_rate": 192000, "channels": 8, "sample_fmt": "fltp"},
            ],
            "eight worst-case": [
                {"sample_rate": 192000, "channels": 8, "sample_fmt": "fltp"}] * 8,
        }
        for label, streams in cases.items():
            with self.subTest(case=label):
                seconds = ext04b.reverse_audio_segment_seconds_for_streams(streams)
                peak = self._aggregate_peak_gib(streams, seconds)
                self.assertLessEqual(peak, cap * 1.001,
                                     f"{label}: {peak:.2f} GiB against a {cap:.2f} GiB cap")

    def test_the_entry_the_executor_calls_uses_the_aggregate(self):
        # The tests above exercise the arithmetic directly. This is the
        # answers-level entry the executor actually reaches, and it is where
        # the `min(per-stream window)` lived.
        answers = {"audio_streams": [
            {"sample_rate": 44100, "channels": 1, "sample_fmt": "s16p"},
            {"sample_rate": 192000, "channels": 8, "sample_fmt": "fltp"},
        ]}
        self.assertEqual(
            ext04b.reverse_audio_segment_seconds_for_streams(answers["audio_streams"]),
            ext04b.audio_reverse_segment_seconds(answers, [0, 1]))
        self.assertLess(
            ext04b.audio_reverse_segment_seconds(answers, [0, 1]),
            ext04b.reverse_audio_segment_seconds_for(192000, 8, "s32"),
            "budgeting only the widest track is what overran the cap")

    def test_the_old_widest_track_policy_really_did_overrun(self):
        # Guard the guard: if the cap ever grew enough for the old number, the
        # assertions above would prove nothing about the defect.
        eight = [{"sample_rate": 192000, "channels": 8, "sample_fmt": "fltp"}] * 8
        widest = ext04b.reverse_audio_segment_seconds_for(192000, 8, "s32")
        self.assertGreater(self._aggregate_peak_gib(eight, widest),
                           4 * FFmWiz.REVERSE_PEAK_BUDGET_BYTES / 1024 ** 3)

    def test_a_heterogeneous_selection_lands_on_a_whole_sample(self):
        streams = [{"sample_rate": 44100, "channels": 2, "sample_fmt": "s16p"},
                   {"sample_rate": 48000, "channels": 2, "sample_fmt": "s16p"}]
        seconds = ext04b.reverse_audio_segment_seconds_for_streams(streams)
        self.assertAlmostEqual(round(seconds * 48000), seconds * 48000, places=6,
                               msg="the window must land on the highest rate's grid")

    def test_unknown_metadata_is_budgeted_as_the_demanding_case(self):
        unknown = ext04b.reverse_audio_segment_seconds_for_streams([{}])
        worst = ext04b.reverse_audio_segment_seconds_for_streams(
            [{"sample_rate": 192000, "channels": 8, "sample_fmt": "s32"}])
        self.assertEqual(worst, unknown)

    def test_a_selection_that_cannot_fit_one_sample_is_refused(self):
        # Never an arbitrary minimum duration: refuse, which is what the cap
        # means.
        streams = [{"sample_rate": 192000, "channels": 8, "sample_fmt": "fltp"}] * 8
        with mock.patch.object(ext04c, "REVERSE_PEAK_BUDGET_BYTES",
                               FFmWiz.REVERSE_FIXED_OVERHEAD_BYTES + 8):
            with self.assertRaises(FFmWiz.ReverseBudgetError):
                ext04b.reverse_audio_segment_seconds_for_streams(streams)

    # --- the selection itself (D04) --------------------------------------
    def test_the_main_executors_track_selection_is_honoured(self):
        # `audio_index` alone was the whole answer, so a two-track selection
        # expressed as `audio_tracks` silently reversed track 0 and dropped the
        # rest.
        self.assertEqual([0, 1], ext04b.audio_reverse_indices(
            {"audio_tracks": [0, 1], "audio_streams": [{}, {}]}))

    def test_the_single_track_tools_still_get_their_track(self):
        self.assertEqual([1], ext04b.audio_reverse_indices(
            {"audio_index": 1, "audio_streams": [{}, {}]}))

    def test_a_missing_key_is_not_read_as_keep_them_all(self):
        self.assertEqual([0], ext04b.audio_reverse_indices(
            {"audio_streams": [{}, {}]}))

    def test_duplicates_collapse_and_order_is_the_selection_order(self):
        self.assertEqual([1, 0], ext04b.audio_reverse_indices(
            {"audio_tracks": [1, 1, 0], "audio_streams": [{}, {}]}))

    def test_an_impossible_index_is_refused_not_silently_changed(self):
        with self.assertRaises(ValueError):
            ext04b.audio_reverse_indices(
                {"audio_tracks": [5], "audio_streams": [{}, {}]})
