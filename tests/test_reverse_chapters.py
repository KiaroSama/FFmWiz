"""Regression: reverse must keep chapters and flip their times (R05).

`remap_chapters_for_encode()` handled cuts and speed but had no reverse case at
all, and the segmented reverse executor wrote chapters into its intermediate
segments while the final concat-copy restored none of them.

Reproduced on a 6 s source with FIRST 0-2 s and SECOND 2-6 s:

    remap plan   : FIRST 0-2, SECOND 2-6      (unreversed -- wrong)
    output file  : []                          (no chapters at all)
    expected     : SECOND 0-4, FIRST 4-6

Both halves are fixed: the remapper flips `new_start = T - old_end` and re-sorts,
and the concat attaches the remapped metadata where the output timeline finally
exists.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

from artifact_guard import NoLeakedArtifacts

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _answers(chapters, duration, **extra):
    answers = {
        "probe": {"chapters": chapters},
        "format": {"duration": str(duration)},
        "video_streams": [{"codec_type": "video", "codec_name": "h264"}],
        "audio_streams": [],
        "output_ext": "mkv", "keep_source_chapters": True,
        "video_speed_enabled": True, "video_speed_factor": 1.0,
    }
    answers.update(extra)
    return answers


def _chapter(start, end, title):
    return {"start_time": str(start), "end_time": str(end), "tags": {"title": title}}


def _plan_times(plan):
    return [(round(c["start"], 3), round(c["end"], 3),
             (c.get("metadata") or {}).get("title")) for c in plan["chapters"]]


class ReverseFlipsChapterTimes(unittest.TestCase):
    CHAPTERS = [_chapter(0, 2, "FIRST"), _chapter(2, 6, "SECOND")]

    def test_reverse_puts_the_last_chapter_first(self):
        plan = FFmWiz.remap_chapters_for_encode(
            _answers(self.CHAPTERS, 6.0, reverse_video=True), speed_factor=1.0)
        self.assertEqual("metadata", plan["mode"])
        self.assertEqual([(0.0, 4.0, "SECOND"), (4.0, 6.0, "FIRST")],
                         _plan_times(plan))

    def test_without_reverse_the_times_are_unchanged(self):
        # A cut is what makes the timeline "modified"; without one the plan is
        # a plain copy, so ask for a cut that keeps everything.
        plan = FFmWiz.remap_chapters_for_encode(
            _answers(self.CHAPTERS, 6.0, cut_keep_ranges=[(0.0, 6.0)]),
            speed_factor=1.0)
        self.assertEqual([(0.0, 2.0, "FIRST"), (2.0, 6.0, "SECOND")],
                         _plan_times(plan))

    def test_reverse_combines_with_speed(self):
        # 2x halves the timeline to 3 s, then the flip mirrors it.
        plan = FFmWiz.remap_chapters_for_encode(
            _answers(self.CHAPTERS, 6.0, reverse_video=True), speed_factor=2.0)
        self.assertEqual([(0.0, 2.0, "SECOND"), (2.0, 3.0, "FIRST")],
                         _plan_times(plan))

    def test_reverse_combines_with_a_cut(self):
        # Keep 0-4 s: FIRST 0-2 survives whole, SECOND is clipped to 2-4.
        # Reversed over the 4 s kept timeline: SECOND 0-2, FIRST 2-4.
        plan = FFmWiz.remap_chapters_for_encode(
            _answers(self.CHAPTERS, 6.0, reverse_video=True,
                     cut_keep_ranges=[(0.0, 4.0)]), speed_factor=1.0)
        self.assertEqual([(0.0, 2.0, "SECOND"), (2.0, 4.0, "FIRST")],
                         _plan_times(plan))

    def test_chapter_titles_survive_the_flip(self):
        plan = FFmWiz.remap_chapters_for_encode(
            _answers(self.CHAPTERS, 6.0, reverse_video=True), speed_factor=1.0)
        self.assertEqual(["SECOND", "FIRST"],
                         [(c.get("metadata") or {}).get("title") for c in plan["chapters"]])

    def test_a_source_without_chapters_is_still_a_plain_copy(self):
        plan = FFmWiz.remap_chapters_for_encode(
            _answers([], 6.0, reverse_video=True), speed_factor=1.0)
        self.assertEqual("copy", plan["mode"])


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")
class RealReverseKeepsChapters(NoLeakedArtifacts, unittest.TestCase):
    """Through the public execution path, probing the finished file."""

    def setUp(self):
        super().setUp()
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_revchap_"))
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def _chaptered_source(self, duration=6):
        meta = self._tmp / "chapters.txt"
        meta.write_text(
            ";FFMETADATA1\n"
            "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\ntitle=FIRST\n"
            "[CHAPTER]\nTIMEBASE=1/1000\nSTART=2000\nEND=6000\ntitle=SECOND\n",
            encoding="utf-8")
        path = self._tmp / "chaptered.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={duration}",
             "-f", "lavfi", "-i", f"sine=duration={duration}", "-i", str(meta),
             "-map", "0:v", "-map", "1:a", "-map_metadata", "2",
             "-c:v", "libx264", "-preset", "ultrafast", "-vf", "format=yuv420p",
             "-c:a", "aac", str(path)],
            check=True, timeout=300)
        return path

    def _chapters_of(self, path):
        data = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_chapters", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60).stdout or "{}")
        return [(float(c["start_time"]), float(c["end_time"]),
                 (c.get("tags") or {}).get("title"))
                for c in data.get("chapters", [])]

    def _run_reverse(self):
        src = self._chaptered_source()
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format",
             "-show_chapters", "-of", "json", str(src)],
            capture_output=True, text=True, timeout=60).stdout)
        streams = probe["streams"]
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": src,
            "output_location": self._tmp, "probe": probe,
            "streams": streams, "format": probe["format"],
            "video_streams": [s for s in streams if s["codec_type"] == "video"],
            "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
            "subtitle_streams": [], "data_streams": [], "attachment_streams": [],
            "output_ext": "mkv", "video_codec": "H264", "use_gpu": False,
            "audio_codec": "aac", "audio_bitrate_kbps": 128,
            "audio_tracks": [0], "subtitle_tracks": [],
            "resolution": "n", "fps": 25, "video_bitrate_kbps": 500,
            "color_range_choice": "tv",
            "keep_source_chapters": True, "keep_source_metadata": True,
            "video_speed_enabled": True, "video_speed_factor": 1.0,
            "reverse_video": True, "audio_speed_from_video": True,
        }
        self.own(answers)
        answers["cmd"] = [str(part) for part in FFmWiz.build_ffmpeg_command(answers)]
        code, _elapsed = FFmWiz.run_segmented_reverse_main_encode(answers)
        self.assertEqual(0, code, "the reverse encode failed")
        return Path(answers["output_path"])

    def test_the_source_fixture_really_has_two_chapters(self):
        # Guard the guard: an empty fixture would make the assertion below
        # pass for the wrong reason.
        self.assertEqual(2, len(self._chapters_of(self._chaptered_source())))

    def test_the_reversed_output_keeps_both_chapters(self):
        chapters = self._chapters_of(self._run_reverse())
        self.assertEqual(2, len(chapters), f"chapters were lost: {chapters}")

    def test_the_reversed_output_has_them_in_reverse_order(self):
        chapters = self._chapters_of(self._run_reverse())
        self.assertEqual(["SECOND", "FIRST"], [title for _s, _e, title in chapters])

    def test_the_reversed_boundaries_are_numerically_right(self):
        # Source is ~6.02 s, so SECOND (4 s long) leads and FIRST follows.
        chapters = self._chapters_of(self._run_reverse())
        (first_start, first_end, _t1), (second_start, second_end, _t2) = chapters
        self.assertAlmostEqual(0.0, first_start, delta=0.1)
        self.assertAlmostEqual(4.0, first_end, delta=0.15)
        self.assertAlmostEqual(4.0, second_start, delta=0.15)
        self.assertAlmostEqual(6.0, second_end, delta=0.15)


if __name__ == "__main__":
    unittest.main()


class AChapterSpanningACutHole(unittest.TestCase):
    """A cut inside a chapter must shorten it, not truncate it at the hole.

    Chapters and subtitle cues now share one `TimelineMap`, but they want
    different things from it: a cue that crosses a removed range survives as
    two separate cues, while a chapter is a single contiguous label and has to
    keep the whole span it still covers.
    """

    def _plan(self, keep_ranges, **extra):
        answers = _answers([_chapter(10, 50, "ACT ONE")], 100,
                           cut_keep_ranges=keep_ranges, **extra)
        return _plan_times(FFmWiz.remap_chapters_for_encode(answers))

    def test_the_chapter_keeps_the_span_after_the_hole(self):
        # keep 0-20 and 30-100: the chapter's 20-30 middle is removed, so it
        # runs 10 -> 40 in the output, not 10 -> 20.
        self.assertEqual([(10.0, 40.0, "ACT ONE")], self._plan([(0, 20), (30, 100)]))

    def test_a_cut_before_the_chapter_shifts_it_earlier(self):
        self.assertEqual([(5.0, 45.0, "ACT ONE")], self._plan([(0, 5), (10, 100)]))

    def test_a_chapter_entirely_inside_a_removed_range_is_dropped(self):
        self.assertEqual([], self._plan([(0, 5), (60, 100)]))

    def test_the_hole_span_survives_reverse_too(self):
        # kept duration 90; the chapter occupies 10-40 forward, so reversed it
        # runs 90-40 -> 90-10.
        self.assertEqual([(50.0, 80.0, "ACT ONE")],
                         self._plan([(0, 20), (30, 100)], reverse_video=True))
