"""F09: a multiline chapter title must survive an FFmetadata round trip.

`ffmetadata_escape` emitted the C escapes `\\n` and `\\r`, but FFmetadata escapes
a special character by prefixing it with a backslash -- including a PHYSICAL
newline. A real FFmpeg/ffprobe round trip therefore read `First<LF>Second` back
as the literal `FirstnSecond`, and CRLF as `FirstrnSecond`: the break vanished
and its letter stayed behind.

Both production writers are exercised through real FFmpeg, and the chapter text
and timing are read back with ffprobe.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k chapter_metadata_roundtrip
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz.support.L00_metadata import ffmetadata_escape
from ffmwiz.support.L01_metadata import (write_copy_cut_chapter_metadata,
                                         write_encode_chapter_metadata)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(bool(FFMPEG and FFPROBE), "ffmpeg/ffprobe not available")

WRITERS = {"encode": write_encode_chapter_metadata, "copy_cut": write_copy_cut_chapter_metadata}


class TheEscapeFollowsFFmetadataSyntax(unittest.TestCase):
    def test_a_newline_becomes_backslash_then_a_real_newline(self):
        self.assertEqual(ffmetadata_escape("First\nSecond"), "First\\\nSecond")

    def test_crlf_is_normalized_to_one_escaped_newline(self):
        self.assertEqual(ffmetadata_escape("First\r\nSecond"), "First\\\nSecond")

    def test_a_lone_carriage_return_is_normalized_too(self):
        self.assertEqual(ffmetadata_escape("First\rSecond"), "First\\\nSecond")

    def test_consecutive_and_trailing_newlines_are_each_escaped(self):
        self.assertEqual(ffmetadata_escape("a\n\nb\n"), "a\\\n\\\nb\\\n")

    def test_a_literal_backslash_n_stays_two_characters(self):
        self.assertEqual(ffmetadata_escape(r"literal\n"), "literal\\\\n")

    def test_the_other_special_characters_are_still_escaped(self):
        self.assertEqual(ffmetadata_escape("a=b;c#d"), "a\\=b\\;c\\#d")

    def test_unicode_is_untouched(self):
        self.assertEqual(ffmetadata_escape("فصل اول"), "فصل اول")

    def test_no_c_style_escape_is_emitted(self):
        escaped = ffmetadata_escape("x\ny")
        self.assertNotIn("\\n", escaped.replace("\\\n", ""))


@requires_ffmpeg
class ChaptersSurviveARealRoundTrip(unittest.TestCase):
    TITLES = {
        "multiline": "First\nSecond",
        "crlf": "First\r\nSecond",
        "consecutive": "One\n\nTwo",
        "specials": "a=b;c#d",
        "unicode": "فصل اول — Chapter 1",
        "section_like": "[CHAPTER] is not a section here",
        "literal_backslash_n": "literal\\n stays",
    }

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_f09_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.source = self.root / "in.mp4"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=4",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.source)],
                       check=True)

    def round_trip(self, writer, title: str) -> list[dict]:
        plan = {"chapters": [
            {"start": 0.0, "end": 2.0, "metadata": {"title": title}},
            {"start": 2.0, "end": 4.0, "metadata": {"title": "Second chapter"}},
        ]}
        metadata_path = writer(plan, self.root)
        out = self.root / f"out_{abs(hash(title))}.mp4"
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(self.source), "-i", str(metadata_path),
             "-map_metadata", "1", "-map_chapters", "1", "-c", "copy", str(out)],
            capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        probe = subprocess.run([FFPROBE, "-v", "error", "-show_chapters",
                                "-of", "json", str(out)], capture_output=True, text=True,
                               encoding="utf-8")
        return json.loads(probe.stdout)["chapters"]

    def test_every_title_shape_comes_back_intact(self):
        expected = dict(self.TITLES)
        expected["crlf"] = "First\nSecond"      # documented CRLF normalization
        for name, writer in WRITERS.items():
            for key, title in self.TITLES.items():
                with self.subTest(writer=name, title=key):
                    chapters = self.round_trip(writer, title)
                    self.assertEqual(len(chapters), 2)
                    self.assertEqual(chapters[0]["tags"]["title"], expected[key])
                    self.assertEqual(chapters[1]["tags"]["title"], "Second chapter")

    def test_the_newline_is_a_newline_not_the_letter_n(self):
        for name, writer in WRITERS.items():
            with self.subTest(writer=name):
                title = self.round_trip(writer, "First\nSecond")[0]["tags"]["title"]
                self.assertNotEqual(title, "FirstnSecond",
                                    "the escaped newline was read back as a letter")
                self.assertIn("\n", title)

    def test_the_chapter_timing_is_preserved(self):
        for name, writer in WRITERS.items():
            with self.subTest(writer=name):
                chapters = self.round_trip(writer, "First\nSecond")
                self.assertAlmostEqual(float(chapters[0]["start_time"]), 0.0, places=3)
                self.assertAlmostEqual(float(chapters[0]["end_time"]), 2.0, places=3)
                self.assertAlmostEqual(float(chapters[1]["end_time"]), 4.0, places=3)

    def test_nothing_is_stripped_out_of_the_title(self):
        chapters = self.round_trip(write_encode_chapter_metadata, "First\nSecond")
        self.assertEqual(chapters[0]["tags"]["title"].replace("\n", ""), "FirstSecond")

    def test_the_metadata_file_is_written_with_lf_endings(self):
        plan = {"chapters": [{"start": 0.0, "end": 1.0,
                              "metadata": {"title": "First\nSecond"}}]}
        for name, writer in WRITERS.items():
            with self.subTest(writer=name):
                raw = writer(plan, self.root).read_bytes()
                self.assertNotIn(b"\r\n", raw,
                                 "Windows text mode turned the escaped LF into CRLF")
                self.assertIn(b"First\\\nSecond", raw)


if __name__ == "__main__":
    unittest.main()
