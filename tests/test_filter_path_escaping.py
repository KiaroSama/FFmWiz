"""Regression: an apostrophe in a path must survive the filtergraph (NEW-CMD4).

`subtitles=filename='...'` puts the path through TWO unescaping passes -- the
filtergraph parser, then av_opt_set_from_string. The old code escaped the
apostrophe once, as \\', which the second pass ate: hard-subbing anything under
a folder like "bob's music" failed with

    [Parsed_subtitles_0] Unable to open .../bobs music/s.srt

note the missing apostrophe. Music and film libraries are full of such names,
so this was not a corner case.

Every 0..6-backslash variant was probed against real ffmpeg 8.1.1. Exactly one
survives both passes: close the quote, three literal backslashes, a quote,
reopen the quote. The real-ffmpeg tests below assert the subtitle is actually
BURNED IN (a pure black frame's YAVG rises from 0 to ~16), because exit code 0
alone does not prove libass drew anything.
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz

FFMPEG = shutil.which("ffmpeg")

# A black frame stays at YAVG 0 unless something was drawn on it.
BLANK_LUMA = 0.05


class EscapedForm(unittest.TestCase):
    def test_the_apostrophe_is_not_escaped_with_a_single_backslash(self):
        # The exact form that used to be emitted and silently lost the quote.
        quoted = FFmWiz.hardsub_filter_quote_path(Path("/tmp/bob's/s.srt"))
        self.assertNotIn("\\'s", quoted,
                         "a single-backslash escape is eaten by the second pass")

    def test_the_apostrophe_uses_the_close_reopen_sequence(self):
        quoted = FFmWiz.hardsub_filter_quote_path(Path("/tmp/bob's/s.srt"))
        self.assertIn("'" + ("\\" * 3) + "'" + "'", quoted)

    def test_a_path_without_an_apostrophe_is_untouched_by_the_rule(self):
        quoted = FFmWiz.hardsub_filter_quote_path(Path("/tmp/plain/s.srt"))
        self.assertNotIn("\\\\\\", quoted)

    def test_colons_commas_and_brackets_are_still_escaped(self):
        quoted = FFmWiz.hardsub_filter_quote_path(Path("/tmp/a,b[1]/s.srt"))
        for fragment in ("\\,", "\\[", "\\]"):
            with self.subTest(fragment):
                self.assertIn(fragment, quoted)

    def test_metadata_filter_path_uses_the_same_sequence(self):
        # It feeds the OUTPUT path of build_signalstats_command and had the
        # identical single-backslash bug.
        quoted = FFmWiz.metadata_filter_path(Path("/tmp/bob's/out.txt"))
        self.assertIn("'" + ("\\" * 3) + "'" + "'", quoted)
        self.assertNotIn("\\'s", quoted)

    def test_both_helpers_wrap_the_value_in_quotes(self):
        for helper in (FFmWiz.hardsub_filter_quote_path, FFmWiz.metadata_filter_path):
            with self.subTest(helper.__name__):
                value = helper(Path("/tmp/plain/s.srt"))
                self.assertTrue(value.startswith("'") and value.endswith("'"), value)


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class RealBurnIn(unittest.TestCase):
    """Exit 0 is not enough -- prove libass opened the file and drew the text."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwiz_escape_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _luma_after_burn_in(self, folder_name):
        folder = self._tmp / folder_name
        folder.mkdir(parents=True)
        srt = folder / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nHELLO\n", encoding="utf-8")
        out = folder / "o.mp4"
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "color=c=black:s=320x120:d=1:r=5",
             "-filter:v", f"subtitles=filename={FFmWiz.hardsub_filter_quote_path(srt)}",
             "-frames:v", "3", str(out)],
            capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0,
                         f"{folder_name}: {result.stderr.strip()[-300:]}")
        stats = subprocess.run(
            [FFMPEG, "-hide_banner", "-i", str(out),
             "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
             "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180)
        match = re.search(r"YAVG=([\d.]+)", stats.stderr or "")
        self.assertIsNotNone(match, "signalstats reported no YAVG")
        return float(match.group(1))

    def test_a_path_with_an_apostrophe_burns_in(self):
        self.assertGreater(self._luma_after_burn_in("bob's music"), BLANK_LUMA)

    def test_apostrophe_with_a_comma_and_brackets_burns_in(self):
        self.assertGreater(self._luma_after_burn_in("o'neil, vol [1]"), BLANK_LUMA)

    def test_a_plain_path_still_burns_in(self):
        self.assertGreater(self._luma_after_burn_in("plain"), BLANK_LUMA)

    def test_a_path_with_spaces_and_unicode_burns_in(self):
        self.assertGreater(self._luma_after_burn_in("d'été — ok"), BLANK_LUMA)


if __name__ == "__main__":
    unittest.main()
