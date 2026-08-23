"""The progress line's one-row clamp must measure COLUMNS, not codepoints.

`_write_progress_line` promises the FFmpeg status line never wraps, and it keeps
that promise by handing the rendered line to `_truncate_ansi_visible`. The
truncator used to advance its budget by one per character (`visible += 1`) while
its own early-out compared against `_visible_len`, which counts East Asian
Wide/Fullwidth characters as two columns and combining marks as zero.

So the two disagreed exactly when it mattered: a CJK or emoji filename in the
label was under-measured, the truncator kept twice as many columns as the budget
allowed, and the line wrapped -- the failure the clamp exists to prevent. Real
input paths in non-Latin scripts show up in this project's own logs, so this is
not a theoretical case (NEW-RT2).
"""
import unittest

from ffmwiz.support.L02 import _truncate_ansi_visible, _visible_len

# Three Han characters: six terminal columns, three codepoints.
CJK = "日本語"
RED = "\x1b[91m"
RESET = "\x1b[0m"


class WidthOracleTests(unittest.TestCase):
    def test_the_oracle_counts_columns_not_codepoints(self):
        self.assertEqual(_visible_len(CJK), 6)
        self.assertEqual(len(CJK), 3)

    def test_combining_marks_take_no_column(self):
        # "e" + COMBINING ACUTE renders as one glyph in one column. Spelled
        # with an escape so an editor cannot normalise the mark away.
        self.assertEqual(len("e" + chr(0x301)), 2)
        self.assertEqual(_visible_len("e" + chr(0x301)), 1)

    def test_escape_sequences_take_no_column(self):
        self.assertEqual(_visible_len(f"{RED}abc{RESET}"), 3)


class TruncationWidthTests(unittest.TestCase):
    def test_a_cjk_label_is_clamped_to_the_column_budget(self):
        label = CJK * 8  # 48 columns, 24 codepoints
        result = _truncate_ansi_visible(label, 20)
        self.assertLessEqual(_visible_len(result), 20)
        # Codepoint counting kept max_visible-3 == 17 characters, i.e. 34
        # columns plus the ellipsis. Column counting keeps 8.
        self.assertEqual(len(result.rstrip(".")), 8)

    def test_a_coloured_cjk_line_fits_and_keeps_its_colour(self):
        line = f"{RED}{CJK * 6}{RESET}"
        result = _truncate_ansi_visible(line, 16)
        self.assertLessEqual(_visible_len(result), 16)
        self.assertIn(RED, result)
        self.assertTrue(result.endswith(RESET), repr(result))

    def test_every_budget_fits_for_a_mixed_line(self):
        # Mixed ASCII / wide / combining / escapes, the shape a real progress
        # line takes once a non-Latin filename is in the label.
        line = f"{RED}Extract{RESET} {CJK}-01 époque {CJK}.mkv"
        for budget in range(4, _visible_len(line) + 4):
            with self.subTest(budget=budget):
                result = _truncate_ansi_visible(line, budget)
                self.assertLessEqual(
                    _visible_len(result), budget,
                    f"{budget}-column budget produced "
                    f"{_visible_len(result)} columns")

    def test_a_line_that_already_fits_is_returned_untouched(self):
        line = f"{RED}{CJK}{RESET}"
        self.assertIs(_truncate_ansi_visible(line, 6), line)

    def test_ascii_truncation_is_unchanged(self):
        # The common path must not have moved: one column per character still.
        self.assertEqual(_truncate_ansi_visible("abcdefghij", 8), "abcde...")
        self.assertEqual(_truncate_ansi_visible("abcdefghij", 10), "abcdefghij")


if __name__ == "__main__":
    unittest.main()
