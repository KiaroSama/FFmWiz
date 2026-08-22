"""Rule configuration in ffmwiz.muxcleanup.selection.

USER-16-6: selection.py was the largest zero-coverage module in the subsystem,
so nothing checked that a sequence of answers produces the SelectionRules the
user asked for, or that Back lands on the step it came from.

Each test drives the real step machine with scripted keystrokes and asserts the
returned rules. The script raises when it runs out, so a step machine that asks
one question too many fails the test - that is what pins the skipped questions
(no audio in the scan, subtitles removed, a single input file) rather than a
comment claiming they are skipped.
"""
from __future__ import annotations

import builtins
import contextlib
import io
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The subsystem logs every prompt; without a handler logging's last-resort
# writer scatters those records across the test runner's stderr.
logging.getLogger("MuxCls").addHandler(logging.NullHandler())

from ffmwiz.muxcleanup.constants import (
    AUDIO_ALL,
    AUDIO_BY_INDEX,
    AUDIO_BY_LANGUAGE,
    AUDIO_NONE,
    SUBTITLE_ALL,
    SUBTITLE_BY_LANGUAGE,
    SUBTITLE_BY_INDEX,
    SUBTITLE_NONE,
)
from ffmwiz.muxcleanup.models import MediaFile, StreamInfo
from ffmwiz.muxcleanup.selection import (
    configure_rules,
    configure_rules_advanced,
    configure_rules_exact,
    previous_advanced_step,
    previous_exact_step,
)


class ScriptExhausted(AssertionError):
    """A step asked for more input than the test scripted."""


@contextlib.contextmanager
def keystrokes(*answers: str):
    pending = iter(answers)

    def fake_input(_prompt: str = "") -> str:
        try:
            return next(pending)
        except StopIteration:
            raise ScriptExhausted(f"a step asked past the script {answers!r}") from None

    original = builtins.input
    builtins.input = fake_input
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            yield out
    finally:
        builtins.input = original


def _stream(index, codec_type, language="und", title="", codec_name="") -> StreamInfo:
    return StreamInfo(
        index=index,
        codec_type=codec_type,
        codec_name=codec_name or ("h264" if codec_type == "video" else "aac"),
        language=language,
        title=title,
    )


def _media(*streams: StreamInfo) -> MediaFile:
    return MediaFile(path=Path("show.mkv"), streams=list(streams))


def _dual_language_file() -> MediaFile:
    """One video, Japanese + English audio, English + Spanish subtitles."""
    return _media(
        _stream(0, "video"),
        _stream(1, "audio", "jpn", "Original"),
        _stream(2, "audio", "eng", "Dub"),
        _stream(3, "subtitle", "eng", "Full"),
        _stream(4, "subtitle", "spa", "Signs"),
    )


class AdvancedRuleTests(unittest.TestCase):
    def test_language_answers_become_language_rules(self):
        answers = (
            "1",    # audio mode: by language
            "jpn",  # audio languages
            "2",    # subtitle mode: by language
            "eng",  # subtitle languages
            "",     # keep attachments -> default yes
            "",     # keep metadata -> default yes
            "",     # edit output stream metadata/order -> default no
            "",     # keep chapters -> default yes
            "",     # copy non-video files -> default yes
            "",     # overwrite -> default no
        )
        with keystrokes(*answers):
            rules = configure_rules_advanced([_dual_language_file()])

        self.assertEqual(rules.selection_style, "advanced")
        self.assertEqual(rules.audio_mode, AUDIO_BY_LANGUAGE)
        self.assertEqual(rules.audio_languages, ["jpn"])
        self.assertEqual(rules.subtitle_mode, SUBTITLE_BY_LANGUAGE)
        self.assertEqual(rules.subtitle_languages, ["eng"])
        self.assertTrue(rules.keep_attachments)
        self.assertTrue(rules.keep_metadata)
        self.assertTrue(rules.keep_chapters)
        self.assertTrue(rules.copy_non_video_files)
        self.assertFalse(rules.overwrite)

    def test_removing_all_subtitles_also_removes_font_attachments_without_asking(self):
        answers = (
            "4",  # audio mode: all
            "5",  # subtitle mode: none  (the attachment question is not asked)
            "",   # keep metadata
            "",   # edit output stream metadata/order
            "",   # keep chapters
            "",   # copy non-video files
            "",   # overwrite
        )
        with keystrokes(*answers) as out:
            rules = configure_rules_advanced([_dual_language_file()])

        self.assertEqual(rules.audio_mode, AUDIO_ALL)
        self.assertEqual(rules.subtitle_mode, SUBTITLE_NONE)
        self.assertFalse(rules.keep_attachments)
        self.assertIn("font attachments will also be removed", out.getvalue())

    def test_a_single_input_file_is_never_asked_about_copying_siblings(self):
        answers = (
            "4",  # audio mode: all
            "1",  # subtitle mode: all
            "",   # keep attachments
            "",   # keep metadata
            "",   # edit output stream metadata/order
            "",   # keep chapters
            "",   # overwrite  (no copy question for one file in, one file out)
        )
        with keystrokes(*answers):
            rules = configure_rules_advanced([_dual_language_file()], single_file_input=True)

        self.assertEqual(rules.subtitle_mode, SUBTITLE_ALL)
        self.assertFalse(rules.copy_non_video_files)

    def test_a_scan_with_no_audio_skips_the_audio_menu_and_keeps_no_audio(self):
        video_and_subs = _media(_stream(0, "video"), _stream(1, "subtitle", "eng"))
        answers = (
            "1",  # subtitle mode: all  (first question asked is the subtitle one)
            "",   # keep attachments
            "",   # keep metadata
            "",   # edit output stream metadata/order
            "",   # keep chapters
            "",   # copy non-video files
            "",   # overwrite
        )
        with keystrokes(*answers) as out:
            rules = configure_rules_advanced([video_and_subs])

        self.assertEqual(rules.audio_mode, AUDIO_NONE)
        self.assertEqual(rules.audio_languages, [])
        self.assertIn("No audio streams found", out.getvalue())

    def test_typing_an_index_list_selects_by_index(self):
        answers = (
            "3",    # audio mode: by exact index
            "1",    # audio indexes
            "4",    # subtitle mode: by exact index
            "3,4",  # subtitle indexes
            "",     # keep attachments
            "",     # keep metadata
            "",     # edit output stream metadata/order
            "",     # keep chapters
            "",     # copy non-video files
            "y",    # overwrite
        )
        with keystrokes(*answers):
            rules = configure_rules_advanced([_dual_language_file()])

        self.assertEqual(rules.audio_indexes, [1])
        self.assertEqual(rules.subtitle_mode, SUBTITLE_BY_INDEX)
        self.assertEqual(rules.subtitle_indexes, [3, 4])
        self.assertTrue(rules.overwrite)


class BackNavigationTests(unittest.TestCase):
    def test_back_from_the_subtitle_menu_re_asks_the_audio_detail(self):
        answers = (
            "1",    # audio mode: by language
            "jpn",  # audio languages
            "0",    # back from the subtitle mode menu
            "kor",  # audio languages again - this is the answer that must win
            "1",    # subtitle mode: all
            "",     # keep attachments
            "",     # keep metadata
            "",     # edit output stream metadata/order
            "",     # keep chapters
            "",     # copy non-video files
            "",     # overwrite
        )
        with keystrokes(*answers) as out:
            rules = configure_rules_advanced([_dual_language_file()])

        self.assertEqual(rules.audio_languages, ["kor"])
        self.assertIn("Returning to previous step", out.getvalue())

    def test_back_at_the_first_step_leaves_the_rule_editor(self):
        from ffmwiz.muxcleanup.prompts import MenuBack

        with keystrokes("0"):
            with self.assertRaises(MenuBack):
                configure_rules_advanced([_dual_language_file()])

    def test_back_skips_the_copy_question_that_a_single_file_run_never_asked(self):
        # Landing on step 8 would show a question the user never answered.
        self.assertEqual(
            previous_advanced_step(9, AUDIO_ALL, SUBTITLE_ALL, skip_copy_non_video=True),
            7,
        )
        self.assertEqual(
            previous_advanced_step(9, AUDIO_ALL, SUBTITLE_ALL, skip_copy_non_video=False),
            8,
        )
        self.assertEqual(previous_exact_step(7, SUBTITLE_ALL, skip_copy_non_video=True), 5)
        self.assertEqual(previous_exact_step(7, SUBTITLE_ALL, skip_copy_non_video=False), 6)

    def test_back_skips_the_audio_detail_step_when_the_mode_has_none(self):
        self.assertEqual(previous_advanced_step(2, AUDIO_BY_LANGUAGE, SUBTITLE_ALL), 1)
        self.assertEqual(previous_advanced_step(2, AUDIO_ALL, SUBTITLE_ALL), 0)

    def test_back_past_a_skipped_audio_menu_leaves_the_rule_editor(self):
        # Nothing had audio, so there is no audio step to go back to.
        self.assertEqual(
            previous_advanced_step(2, AUDIO_NONE, SUBTITLE_ALL, skip_audio_selection=True),
            -1,
        )


class ExactRuleTests(unittest.TestCase):
    def test_index_answers_become_index_rules(self):
        answers = (
            "1,2",  # audio indexes to keep
            "3",    # subtitle indexes to keep
            "",     # keep attachments
            "",     # keep metadata
            "",     # edit output stream metadata/order
            "",     # keep chapters
            "",     # copy non-video files
            "",     # overwrite
        )
        with keystrokes(*answers):
            rules = configure_rules_exact([_dual_language_file()])

        self.assertEqual(rules.selection_style, "exact")
        self.assertEqual(rules.audio_mode, AUDIO_BY_INDEX)
        self.assertEqual(rules.audio_indexes, [1, 2])
        self.assertEqual(rules.subtitle_mode, SUBTITLE_BY_INDEX)
        self.assertEqual(rules.subtitle_indexes, [3])

    def test_all_and_none_words_become_modes_not_indexes(self):
        answers = (
            "all",   # audio: keep everything
            "none",  # subtitles: keep nothing (attachments go too, unasked)
            "",      # keep metadata
            "",      # edit output stream metadata/order
            "",      # keep chapters
            "",      # copy non-video files
            "",      # overwrite
        )
        with keystrokes(*answers):
            rules = configure_rules_exact([_dual_language_file()])

        self.assertEqual(rules.audio_mode, AUDIO_ALL)
        self.assertEqual(rules.audio_indexes, [])
        self.assertEqual(rules.subtitle_mode, SUBTITLE_NONE)
        self.assertFalse(rules.keep_attachments)


class SelectionStyleTests(unittest.TestCase):
    def test_two_audio_languages_offer_the_style_menu(self):
        answers = (
            "2",    # selection style: exact stream indexes
            "1",    # audio indexes
            "none",  # subtitles
            "",     # keep metadata
            "",     # edit output stream metadata/order
            "",     # keep chapters
            "",     # copy non-video files
            "",     # overwrite
        )
        with keystrokes(*answers):
            rules = configure_rules([_dual_language_file()])

        self.assertEqual(rules.selection_style, "exact")

    def test_one_audio_language_goes_straight_to_the_advanced_rules(self):
        single_language = _media(
            _stream(0, "video"),
            _stream(1, "audio", "jpn"),
            _stream(2, "subtitle", "eng"),
        )
        answers = (
            "4",  # audio mode: all - so the first question is already the audio mode
            "1",  # subtitle mode: all
            "",   # keep attachments
            "",   # keep metadata
            "",   # edit output stream metadata/order
            "",   # keep chapters
            "",   # copy non-video files
            "",   # overwrite
        )
        with keystrokes(*answers):
            rules = configure_rules([single_language])

        self.assertEqual(rules.selection_style, "advanced")
        self.assertEqual(rules.audio_mode, AUDIO_ALL)


if __name__ == "__main__":
    unittest.main()
