"""Regression: a Split must carry its subtitles and its own chapters.

Two defects shared one root -- the Split builder assumed it owned every input
index from 1 upwards, and that the caller had added nothing before it.

1. Subtitles. The builder emitted `-sn`. With a cut or a speed change that was
   worse than a silent drop: the whole-timeline track WAS extracted, retimed,
   announced to the user as "1 text track(s) were retimed onto the processed
   timeline" and handed to FFmpeg as an input -- then never mapped. The message
   described an output that carried nothing.

2. Chapters. `chapter_input_base = 1` ignored those already-added subtitle
   inputs, so on a chaptered source `-map_chapters 1` addressed `retimed00.srt`
   and every part received the PREVIOUS part's chapters.

Measured on a 6 s source, cues at 0.5-2.5 ("FIRST HALF") and 4.0-5.5 ("SECOND
HALF"), chapters ONE 0-3 and TWO 3-6, split at 3 s:

    before: Part01 subtitles NONE, chapters read from an .srt
    after : Part01 0.5-2.5 FIRST HALF / ONE, Part02 1.0-2.5 SECOND HALF / TWO
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


def _answers(**extra):
    answers = {
        "ffmpeg": FFMPEG or "ffmpeg", "ffprobe": FFPROBE or "ffprobe",
        "output_ext": "mkv",
        "color_range_choice": "tv",
        "keep_source_subtitles": True, "subtitle_tracks": [0],
        "keep_source_chapters": True,
        "video_encoder": "libx264", "crf": 28, "preset": "ultrafast",
        "audio_codec": "copy", "audio_tracks": [0],
    }
    answers.update(extra)
    return answers


class PerPartSlicing(unittest.TestCase):
    """The cue arithmetic the file-level tests below assert on."""

    CUES = [(0.5, 2.5, "FIRST HALF"), (4.0, 5.5, "SECOND HALF")]

    def _sliced(self, part_start, part_end):
        out = []
        for start, end, text in self.CUES:
            clipped_start = max(start, part_start)
            clipped_end = min(end, part_end)
            if clipped_end > clipped_start + 1e-6:
                out.append((round(clipped_start - part_start, 3),
                            round(clipped_end - part_start, 3), text))
        return out

    def test_a_part_keeps_only_its_own_cues_rebased_to_zero(self):
        self.assertEqual([(0.5, 2.5, "FIRST HALF")], self._sliced(0.0, 3.0))
        self.assertEqual([(1.0, 2.5, "SECOND HALF")], self._sliced(3.0, 6.0))


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe required")
class SplitOutputsCarrySubtitlesAndChapters(NoLeakedArtifacts, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_split_subs_test_"))
        srt = cls._tmp / "s.srt"
        srt.write_text(
            "1\n00:00:00,500 --> 00:00:02,500\nFIRST HALF\n\n"
            "2\n00:00:04,000 --> 00:00:05,500\nSECOND HALF\n\n",
            encoding="utf-8", newline="\n")
        meta = cls._tmp / "ch.txt"
        meta.write_text(
            ";FFMETADATA1\n"
            "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=3000\ntitle=ONE\n"
            "[CHAPTER]\nTIMEBASE=1/1000\nSTART=3000\nEND=6000\ntitle=TWO\n",
            encoding="utf-8", newline="\n")
        cls.source = cls._tmp / "src.mkv"
        subprocess.run(
            [FFMPEG, "-v", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=6",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
             "-i", str(srt), "-i", str(meta),
             "-map", "0:v", "-map", "1:a", "-map", "2:s", "-map_metadata", "3",
             "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
             "-c:s", "srt", str(cls.source)],
            check=True, capture_output=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _run_split(self, name, **extra):
        out = self._tmp / name
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_chapters",
             "-show_format", "-of", "json", str(self.source)],
            capture_output=True, text=True, timeout=120).stdout)
        streams = probe["streams"]
        extra.setdefault("separator_points", [3.0])
        answers = self.own(_answers(
            input_path=self.source, probe=probe, format=probe["format"],
            output_location=out,
            video_streams=[s for s in streams if s["codec_type"] == "video"],
            audio_streams=[s for s in streams if s["codec_type"] == "audio"],
            subtitle_streams=[s for s in streams if s["codec_type"] == "subtitle"],
            **extra))
        cmd = [str(x) for x in FFmWiz.build_ffmpeg_command(answers)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        self.assertEqual(0, result.returncode, result.stderr[-2000:])
        return sorted(out.glob("*.mkv"))

    def _cues_of(self, part):
        dump = part.with_suffix(".out.srt")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(part),
                        "-map", "0:s:0", "-c:s", "srt", str(dump)],
                       capture_output=True, timeout=120)
        if not dump.exists() or not dump.stat().st_size:
            return []
        return [(round(start, 3), round(end, 3), text)
                for start, end, text in FFmWiz.parse_srt(
                    dump.read_text(encoding="utf-8"))]

    def _chapter_titles(self, part):
        out = subprocess.run([FFPROBE, "-v", "error", "-show_chapters",
                              "-of", "json", str(part)],
                             capture_output=True, text=True, timeout=120).stdout
        return [(chapter.get("tags") or {}).get("title")
                for chapter in json.loads(out).get("chapters", [])]

    def test_a_plain_split_gives_each_part_its_own_cues(self):
        parts = self._run_split("plain")
        self.assertEqual(2, len(parts))
        self.assertEqual([(0.5, 2.5, "FIRST HALF")], self._cues_of(parts[0]))
        self.assertEqual([(1.0, 2.5, "SECOND HALF")], self._cues_of(parts[1]))

    def test_each_part_gets_its_own_chapters_not_the_previous_one(self):
        parts = self._run_split("chapters")
        self.assertEqual(["ONE"], self._chapter_titles(parts[0]))
        self.assertEqual(["TWO"], self._chapter_titles(parts[1]))

    def test_a_cut_split_maps_the_retimed_track_it_announced(self):
        # The path that printed "were retimed onto the processed timeline" and
        # then shipped nothing.
        parts = self._run_split("cut", cut_keep_ranges=[(0.0, 2.0), (3.0, 6.0)],
                                separator_points=[1.5])
        carried = [text for part in parts for _s, _e, text in self._cues_of(part)]
        self.assertTrue(carried, "the announced retimed track must reach an output")
        self.assertIn("SECOND HALF", carried)

    def test_a_cut_that_removes_every_cue_carries_none(self):
        # Branch trap: the retimed set is empty here because the cut removed
        # every cue, NOT because the clock stands still. Falling back to the
        # source track would slice cues that belong to a timeline this output
        # does not use -- part 1 would show "FIRST HALF" at 0.5 s.
        parts = self._run_split("empty", cut_keep_ranges=[(2.6, 3.9)],
                                separator_points=[0.6])
        for index, part in enumerate(parts):
            self.assertEqual([], self._cues_of(part),
                             f"part {index + 1} carried a cue the cut removed")

    def test_no_cue_outlives_the_part_that_carries_it(self):
        parts = self._run_split("bounds")
        for index, part in enumerate(parts):
            duration = float(json.loads(subprocess.run(
                [FFPROBE, "-v", "error", "-show_format", "-of", "json", str(part)],
                capture_output=True, text=True,
                timeout=120).stdout)["format"]["duration"])
            for _start, end, text in self._cues_of(part):
                self.assertLessEqual(
                    end, duration + 0.5,
                    f"part {index + 1}: cue {text!r} ends past the part")


class BitmapTracksAreDeclaredNotSilentlyLost(NoLeakedArtifacts, unittest.TestCase):
    """A picture subtitle has no cue text to slice, so it cannot follow a
    Split. The documented contract is that it is stated and confirmed, the
    same as on the non-split cut/speed path -- never dropped in silence."""

    def _answers(self, codec):
        return self.own({
            "video_streams": [{"codec_type": "video"}],
            "subtitle_streams": [{"codec_type": "subtitle", "codec_name": codec}],
            "subtitle_tracks": [0], "keep_source_subtitles": True,
            "output_ext": "mkv", "input_path": "x.mkv",
            "color_range_choice": "tv",
        })

    def _build(self, codec, confirm):
        from ffmwiz import wizard_build_b as wb
        asked = []
        real = wb.confirm_bitmap_subtitle_drop
        wb.confirm_bitmap_subtitle_drop = lambda a, t: (asked.append(t) or confirm)
        try:
            tracks = wb.build_split_subtitle_inputs(
                self._answers(codec), [(0.0, 3.0), (3.0, 6.0)])
        finally:
            wb.confirm_bitmap_subtitle_drop = real
        return asked, tracks

    def test_a_pgs_track_is_confirmed_before_it_is_dropped(self):
        asked, tracks = self._build("hdmv_pgs_subtitle", True)
        self.assertEqual([[(0, "hdmv_pgs_subtitle")]], asked)
        self.assertEqual([], tracks)

    def test_declining_stops_the_build_instead_of_dropping_it(self):
        from ffmwiz import wizard_build_b as wb
        real = wb.confirm_bitmap_subtitle_drop
        wb.confirm_bitmap_subtitle_drop = lambda a, t: False
        try:
            with self.assertRaises(RuntimeError):
                wb.build_split_subtitle_inputs(
                    self._answers("dvd_subtitle"), [(0.0, 3.0), (3.0, 6.0)])
        finally:
            wb.confirm_bitmap_subtitle_drop = real

    def test_a_text_track_is_not_treated_as_bitmap(self):
        asked, _tracks = self._build("subrip", True)
        self.assertEqual([], asked, "a text track must not trigger the drop prompt")


class TheBuilderDoesNotAssumeItOwnsInputOne(unittest.TestCase):
    def test_the_chapter_base_is_counted_not_hardcoded(self):
        source = (Path(FFmWiz.__file__).resolve().parent
                  / "ffmwiz" / "wizard_build.py").read_text(encoding="utf-8")
        self.assertNotIn("chapter_input_base = 1", source,
                         "a literal base ignores the caller's inputs")
        self.assertIn('chapter_input_base = sum(1 for arg in cmd if arg == "-i")',
                      source)


if __name__ == "__main__":
    unittest.main()
