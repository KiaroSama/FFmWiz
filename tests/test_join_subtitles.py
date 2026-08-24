"""Regression tests: a joined re-encode must carry its subtitles (D31).

The wizard asked which subtitle tracks to keep, then emitted `-sn` and dropped
them, because FFmpeg's `concat` filter cannot process subtitle streams. The
answer is to build ONE subtitle track for the joined timeline: shift each
input's cues by the duration of everything before it, then feed the merged file
back in as an extra input.

Verified end to end against real MKVs -- clipA (10 s, cues at 1-3 s and 5-7 s)
joined with clipB (8 s, cues at 1-3 s and 5-7 s) produced one subrip track with
BETA's cues at 11-13 s and 15-17 s. These tests pin the arithmetic and the
command shape so that stays true.
"""
import unittest

import FFmWiz


class SrtParsing(unittest.TestCase):
    def test_parses_a_plain_cue(self):
        cues = FFmWiz.parse_srt("1\n00:00:01,500 --> 00:00:03,250\nHello\n")
        self.assertEqual(cues, [(1.5, 3.25, "Hello")])

    def test_keeps_multi_line_bodies(self):
        cues = FFmWiz.parse_srt("1\n00:00:01,000 --> 00:00:02,000\nline one\nline two\n")
        self.assertEqual(cues[0][2], "line one\nline two")

    def test_tolerates_crlf_bom_and_a_dot_separator(self):
        # All three turn up in subtitles pulled off real discs and websites.
        text = "﻿1\r\n00:00:01.000 --> 00:00:02.000\r\nHi\r\n"
        self.assertEqual(FFmWiz.parse_srt(text), [(1.0, 2.0, "Hi")])

    def test_tolerates_a_missing_index_line(self):
        self.assertEqual(
            FFmWiz.parse_srt("00:00:01,000 --> 00:00:02,000\nHi\n"),
            [(1.0, 2.0, "Hi")])

    def test_drops_empty_and_zero_length_cues(self):
        text = ("1\n00:00:01,000 --> 00:00:02,000\n\n\n"
                "2\n00:00:03,000 --> 00:00:03,000\nzero\n")
        self.assertEqual(FFmWiz.parse_srt(text), [])

    def test_no_input_is_no_cues(self):
        self.assertEqual(FFmWiz.parse_srt(""), [])
        self.assertEqual(FFmWiz.parse_srt(None), [])

    def test_hours_survive_the_round_trip(self):
        cues = FFmWiz.parse_srt("1\n01:02:03,004 --> 01:02:04,005\nlate\n")
        self.assertEqual(cues[0][0], 3723.004)
        self.assertEqual(FFmWiz.srt_timestamp(3723.004), "01:02:03,004")


class Timestamps(unittest.TestCase):
    def test_formats_zero(self):
        self.assertEqual(FFmWiz.srt_timestamp(0), "00:00:00,000")

    def test_rounds_to_the_millisecond(self):
        self.assertEqual(FFmWiz.srt_timestamp(1.23456), "00:00:01,235")

    def test_clamps_a_negative_offset(self):
        self.assertEqual(FFmWiz.srt_timestamp(-5), "00:00:00,000")


class CueShifting(unittest.TestCase):
    def test_shifts_by_the_offset(self):
        self.assertEqual(
            FFmWiz.shift_cues([(1.0, 2.0, "x")], 10.0),
            [(11.0, 12.0, "x")])

    def test_clips_a_cue_running_past_its_segment(self):
        # Otherwise it would sit on top of the next input's dialogue.
        self.assertEqual(
            FFmWiz.shift_cues([(8.0, 12.0, "x")], 0.0, limit=10.0),
            [(8.0, 10.0, "x")])

    def test_drops_a_cue_starting_past_its_segment(self):
        self.assertEqual(FFmWiz.shift_cues([(11.0, 12.0, "x")], 0.0, limit=10.0), [])

    def test_no_limit_means_no_clipping(self):
        self.assertEqual(
            FFmWiz.shift_cues([(8.0, 12.0, "x")], 0.0),
            [(8.0, 12.0, "x")])


class Merging(unittest.TestCase):
    A = "1\n00:00:01,000 --> 00:00:03,000\nA one\n"
    B = "1\n00:00:00,500 --> 00:00:02,000\nB one\n"

    def test_the_second_input_is_shifted_by_the_first_duration(self):
        merged = FFmWiz.merge_joined_srt([(self.A, 10.0), (self.B, 8.0)])
        self.assertIn("00:00:01,000 --> 00:00:03,000", merged)
        self.assertIn("00:00:10,500 --> 00:00:12,000", merged)

    def test_cues_are_renumbered_from_one_in_time_order(self):
        merged = FFmWiz.merge_joined_srt([(self.A, 10.0), (self.B, 8.0)])
        indexes = [line for line in merged.splitlines() if line.strip().isdigit()]
        self.assertEqual(indexes, ["1", "2"])

    def test_an_input_without_subtitles_still_advances_the_offset(self):
        # The gap has to be preserved or every later cue lands early.
        merged = FFmWiz.merge_joined_srt([("", 10.0), (self.B, 8.0)])
        self.assertIn("00:00:10,500 --> 00:00:12,000", merged)

    def test_nothing_in_nothing_out(self):
        self.assertEqual(FFmWiz.merge_joined_srt([("", 10.0), ("", 8.0)]), "")


class JoinablePicking(unittest.TestCase):
    def test_text_tracks_are_joinable(self):
        item = {"subtitle_streams": [{"index": 2, "codec_name": "subrip"}]}
        self.assertEqual(len(FFmWiz.joinable_subtitle_streams(item)), 1)

    def test_bitmap_tracks_are_not(self):
        # A PGS stream is a picture; there are no cue times to shift.
        item = {"subtitle_streams": [{"index": 2, "codec_name": "hdmv_pgs_subtitle"}]}
        self.assertEqual(FFmWiz.joinable_subtitle_streams(item), [])


def _item(codec="subrip", duration=10.0):
    return {"subtitle_streams": ([{"index": 2, "codec_name": codec}] if codec else []),
            "duration": duration}


def _answers(**extra):
    base = {"subtitle_tracks": [0], "subtitle_streams": [{"index": 2, "codec_name": "subrip"}]}
    base.update(extra)
    return base


class Plan(unittest.TestCase):
    def test_two_text_inputs_are_supported(self):
        plan = FFmWiz.join_subtitle_plan(_answers(), [_item(), _item(duration=8.0)])
        self.assertTrue(plan["supported"], plan["reason"])
        self.assertEqual(len(plan["segments"]), 2)

    def test_one_input_without_subtitles_is_still_supported(self):
        # It contributes no cues but keeps the timeline offsets right.
        plan = FFmWiz.join_subtitle_plan(_answers(), [_item(), _item(codec=None)])
        self.assertTrue(plan["supported"], plan["reason"])

    def test_bitmap_only_inputs_are_refused_with_a_reason(self):
        plan = FFmWiz.join_subtitle_plan(
            _answers(), [_item("hdmv_pgs_subtitle"), _item("hdmv_pgs_subtitle")])
        self.assertFalse(plan["supported"])
        self.assertIn("bitmap", plan["reason"])

    def test_no_selection_means_no_plan(self):
        plan = FFmWiz.join_subtitle_plan({"subtitle_tracks": []}, [_item(), _item()])
        self.assertFalse(plan["supported"])

    def test_a_missing_duration_is_refused(self):
        # Without a duration the next input's offset is unknown; guessing it
        # would silently desync every later cue.
        plan = FFmWiz.join_subtitle_plan(_answers(), [_item(duration=0.0), _item()])
        self.assertFalse(plan["supported"])
        self.assertIn("duration", plan["reason"])

    # An edited timeline used to refuse assembly outright, which dropped every
    # track the user had selected. The merged track is built on the unedited
    # joined clock and then run through the same TimelineMap the picture uses,
    # so all four of these now assemble (F09). Real cue times for each are in
    # tests/test_join_subtitles_edited.py.
    def test_cuts_no_longer_refuse_assembly(self):
        plan = FFmWiz.join_subtitle_plan(
            _answers(cut_keep_ranges=[(0.0, 5.0)]), [_item(), _item()])
        self.assertTrue(plan["supported"], plan.get("reason"))

    def test_a_split_no_longer_refuses_assembly(self):
        plan = FFmWiz.join_subtitle_plan(
            _answers(separator_points=[5.0]), [_item(), _item()])
        self.assertTrue(plan["supported"], plan.get("reason"))

    def test_a_speed_change_no_longer_refuses_assembly(self):
        plan = FFmWiz.join_subtitle_plan(
            _answers(video_speed_enabled=True, video_speed=2.0), [_item(), _item()])
        self.assertTrue(plan["supported"], plan.get("reason"))

    def test_reverse_no_longer_refuses_assembly(self):
        plan = FFmWiz.join_subtitle_plan(
            _answers(reverse_video=True), [_item(), _item()])
        self.assertTrue(plan["supported"], plan.get("reason"))

    def test_a_missing_duration_still_refuses(self):
        # The one refusal that must stay: without durations the per-input cue
        # offsets cannot be computed at all.
        plan = FFmWiz.join_subtitle_plan(
            _answers(), [_item(duration=0.0), _item()])
        self.assertFalse(plan["supported"])
        self.assertIn("duration", plan["reason"])


class SelectedTrackSurvivesIntoThePlan(unittest.TestCase):
    """The selected relative index must reach the segments, not just enable them.

    The plan used to treat `subtitle_tracks` as a yes/no flag and then take each
    input's first TEXT stream, so a request for track 1 quietly built track 0.
    """

    @staticmethod
    def _two_track_item(duration=10.0):
        return {"duration": duration, "subtitle_streams": [
            {"index": 2, "codec_name": "subrip", "tags": {"language": "eng"}},
            {"index": 3, "codec_name": "subrip", "tags": {"language": "spa"}},
        ]}

    def test_track_one_selects_the_second_stream_of_every_input(self):
        items = [self._two_track_item(), self._two_track_item(8.0)]
        plan = FFmWiz.join_subtitle_plan({"subtitle_tracks": [1]}, items)
        self.assertTrue(plan["supported"], plan["reason"])
        for _item, stream, _duration in plan["tracks"][0]["segments"]:
            self.assertEqual("spa", stream["tags"]["language"])

    def test_segments_still_names_the_first_selected_track(self):
        # Kept as an alias so single-track callers do not have to change.
        items = [self._two_track_item(), self._two_track_item(8.0)]
        plan = FFmWiz.join_subtitle_plan({"subtitle_tracks": [1]}, items)
        self.assertEqual(plan["tracks"][0]["segments"], plan["segments"])


class OutcomeNotes(unittest.TestCase):
    """The note the user reads must match what the command actually does."""

    def test_a_supported_join_is_reported_as_kept(self):
        answers = _answers(keep_source_subtitles=True)
        notes = " ".join(FFmWiz.join_extras_outcome_notes(
            answers, [_item(), _item(duration=8.0)]))
        self.assertIn("merged", notes.lower())
        self.assertNotIn("dropped; the concat filter", notes)

    def test_a_bitmap_join_is_reported_as_dropped(self):
        answers = _answers(keep_source_subtitles=True)
        notes = " ".join(FFmWiz.join_extras_outcome_notes(
            answers,
            [_item("hdmv_pgs_subtitle"), _item("hdmv_pgs_subtitle", duration=8.0)]))
        self.assertIn("bitmap", notes.lower())

    def test_every_merged_track_gets_its_own_line(self):
        # One line saying "one merged track" would understate a two-track join.
        item = {"duration": 10.0, "subtitle_streams": [
            {"index": 2, "codec_name": "subrip", "tags": {"language": "eng"}},
            {"index": 3, "codec_name": "subrip", "tags": {"language": "spa"}}]}
        lines = [line for line in FFmWiz.join_extras_outcome_notes(
            {"subtitle_tracks": "all", "keep_source_subtitles": True}, [item, item])
            if "Subtitles" in line]
        self.assertEqual(2, len(lines), lines)

    def test_no_subtitle_selection_says_nothing_about_subtitles(self):
        lines = FFmWiz.join_extras_outcome_notes(
            {"subtitle_tracks": [], "keep_source_subtitles": True},
            [_item(), _item(duration=8.0)])
        self.assertEqual([], [line for line in lines if "Subtitles" in line])


if __name__ == "__main__":
    unittest.main()
