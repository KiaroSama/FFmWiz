"""Prompt-layer decisions in ffmwiz.muxcleanup.prompts.

USER-16-6: prompts.py sat at 16% coverage, so the rules the console layer
enforces - quit, back, defaults, re-asking on a bad answer, and refusing an
output folder that would feed the next run its own output - were unpinned.

Every test drives the real prompt with scripted keystrokes; nothing under test
is mocked. The input script raises when it runs out, so a prompt that loops
forever fails the test instead of hanging it.
"""
from __future__ import annotations

import builtins
import contextlib
import io
import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The subsystem logs every prompt. Without a handler, logging's last-resort
# writer would scatter those records across the test runner's stderr.
logging.getLogger("MuxCls").addHandler(logging.NullHandler())

from ffmwiz.muxcleanup.prompts import (
    MenuBack,
    MenuExit,
    ask_csv_int_required,
    ask_language_codes_required,
    ask_numbered_menu,
    ask_output_base_path,
    ask_yes_no,
    input_path_from_args,
    normalize_path_text,
)


class ScriptExhausted(AssertionError):
    """A prompt asked for more input than the test scripted."""


@contextlib.contextmanager
def keystrokes(*answers: str):
    """Feed `answers` to input() in order and capture the console output."""
    pending = iter(answers)

    def fake_input(_prompt: str = "") -> str:
        try:
            return next(pending)
        except StopIteration:
            raise ScriptExhausted(f"prompt asked past the script {answers!r}") from None

    original = builtins.input
    builtins.input = fake_input
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            yield out
    finally:
        builtins.input = original


class NavigationTokenTests(unittest.TestCase):
    def test_quit_token_raises_menu_exit(self):
        for token in ("q", "quit", "exit", "QUIT"):
            with self.subTest(token=token), keystrokes(token):
                with self.assertRaises(MenuExit):
                    ask_yes_no("Keep chapters?", True)

    def test_zero_is_back_when_back_is_allowed(self):
        with keystrokes("0"):
            with self.assertRaises(MenuBack):
                ask_yes_no("Keep chapters?", True)

    def test_zero_is_refused_and_re_asked_when_back_is_not_allowed(self):
        with keystrokes("0", "n") as out:
            self.assertFalse(ask_yes_no("Keep chapters?", True, allow_back=False))
        self.assertIn("Back is not available here.", out.getvalue())


class YesNoTests(unittest.TestCase):
    def test_empty_answer_takes_the_default(self):
        with keystrokes(""):
            self.assertTrue(ask_yes_no("Keep metadata?", True))
        with keystrokes(""):
            self.assertFalse(ask_yes_no("Overwrite existing output files?", False))

    def test_spelled_out_answers_are_accepted(self):
        with keystrokes("YES"):
            self.assertTrue(ask_yes_no("Keep metadata?", False))
        with keystrokes("no"):
            self.assertFalse(ask_yes_no("Keep metadata?", True))

    def test_junk_answer_is_re_asked_rather_than_taken_as_the_default(self):
        with keystrokes("maybe", "y") as out:
            self.assertTrue(ask_yes_no("Keep metadata?", False))
        self.assertIn("Please enter y or n", out.getvalue())


class NumberedMenuTests(unittest.TestCase):
    OPTIONS = (("1", "keep all"), ("2", "keep none"))

    def test_empty_answer_returns_the_default_option(self):
        with keystrokes(""):
            self.assertEqual(
                ask_numbered_menu("Subtitle selection modes", self.OPTIONS, "2", "Choose"),
                "2",
            )

    def test_out_of_range_answer_is_re_asked(self):
        with keystrokes("7", "1") as out:
            self.assertEqual(
                ask_numbered_menu("Subtitle selection modes", self.OPTIONS, "2", "Choose"),
                "1",
            )
        self.assertIn("Invalid choice", out.getvalue())


class CsvAnswerTests(unittest.TestCase):
    def test_index_outside_the_scan_needs_confirmation(self):
        # Declining the confirmation must re-ask, not accept the index anyway.
        with keystrokes("9", "n", "2") as out:
            self.assertEqual(ask_csv_int_required("Audio indexes", [1, 2]), [2])
        self.assertIn("These indexes were not found in the scan", out.getvalue())

    def test_index_outside_the_scan_is_kept_when_confirmed(self):
        with keystrokes("9", "y"):
            self.assertEqual(ask_csv_int_required("Audio indexes", [1, 2]), [9])

    def test_known_indexes_are_taken_without_a_confirmation_prompt(self):
        with keystrokes("2,1"):
            self.assertEqual(ask_csv_int_required("Audio indexes", [1, 2]), [2, 1])

    def test_non_numeric_answer_is_re_asked(self):
        with keystrokes("abc", "3") as out:
            self.assertEqual(ask_csv_int_required("Audio indexes", None), [3])
        self.assertIn("Please enter at least one stream index", out.getvalue())

    def test_language_codes_are_normalised(self):
        with keystrokes(" JPN , unknown "):
            self.assertEqual(ask_language_codes_required("Languages"), ["jpn", "und"])


class OutputBasePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input_dir = self.root / "input"
        self.input_dir.mkdir()

    def test_empty_answer_uses_the_input_parent_folder(self):
        with keystrokes(""):
            self.assertEqual(ask_output_base_path(self.input_dir), self.root)

    def test_output_inside_the_input_folder_is_refused(self):
        # A folder run walks its input recursively, so this run's output would
        # become the next run's input.
        inside = self.input_dir / "out"
        with keystrokes(str(inside), str(self.root / "safe")) as out:
            self.assertEqual(ask_output_base_path(self.input_dir), self.root / "safe")
        self.assertIn("cannot be inside the input folder", out.getvalue())

    def test_the_input_folder_itself_is_refused(self):
        with keystrokes(str(self.input_dir), str(self.root / "safe")) as out:
            self.assertEqual(ask_output_base_path(self.input_dir), self.root / "safe")
        self.assertIn("cannot be the input folder itself", out.getvalue())

    def test_a_yes_no_answer_is_not_taken_as_a_folder_name(self):
        with keystrokes("y", "") as out:
            self.assertEqual(ask_output_base_path(self.input_dir), self.root)
        self.assertIn("Please enter a folder path", out.getvalue())

    def test_a_media_file_name_is_refused_as_an_output_folder(self):
        with keystrokes(str(self.root / "out.mkv"), "") as out:
            self.assertEqual(ask_output_base_path(self.input_dir), self.root)
        self.assertIn("must be a folder, not a media file name", out.getvalue())

    def test_an_existing_file_is_refused_as_an_output_folder(self):
        existing = self.root / "taken"
        existing.write_text("x", encoding="utf-8")
        with keystrokes(str(existing), "") as out:
            self.assertEqual(ask_output_base_path(self.input_dir), self.root)
        self.assertIn("exists but is not a folder", out.getvalue())


class PathNormalisationTests(unittest.TestCase):
    def test_a_relative_name_is_anchored_so_ffmpeg_cannot_read_it_as_an_option(self):
        # 'Path(".") / "-name.mkv"' collapses to '-name.mkv', which ffprobe
        # answers with "Unrecognized option".
        path = normalize_path_text("-name.mkv")
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.name, "-name.mkv")

    def test_surrounding_quotes_from_a_drag_and_drop_are_stripped(self):
        raw = '"C:\\videos\\clip.mkv"' if sys.platform == "win32" else '"/videos/clip.mkv"'
        path = normalize_path_text(raw)
        self.assertEqual(path.name, "clip.mkv")
        self.assertNotIn('"', str(path))

    def test_blank_launcher_argument_is_not_a_path(self):
        self.assertIsNone(input_path_from_args([]))
        self.assertIsNone(input_path_from_args(["   "]))
        self.assertEqual(input_path_from_args(["clip.mkv"]).name, "clip.mkv")


if __name__ == "__main__":
    unittest.main()
