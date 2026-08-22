"""The Stream Cleanup menu flow in ffmwiz.muxcleanup.app.

USER-16-6: app.py was at 9% coverage - the outer loop that ties input, rules,
output root and processing together had no test, so what the menu returns for a
success, a cancel, a Back or a missing tool was undefined by the suite.

The prompts, the rule step machine and the output-root resolution all run for
real here; only the four things that would touch the outside world are replaced
(tool discovery, the file walk, the ffprobe scan and the remux itself). The
input script raises when it runs out, so a flow that asks an unexpected question
fails rather than hangs.
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

# The subsystem logs every prompt; without a handler logging's last-resort
# writer scatters those records across the test runner's stderr.
logging.getLogger("MuxCls").addHandler(logging.NullHandler())

from ffmwiz.muxcleanup import app as mux_app
from ffmwiz.muxcleanup.media import ScanResult
from ffmwiz.muxcleanup.models import MediaFile, StreamInfo
from ffmwiz.muxcleanup.processing import ProcessSummary


class ScriptExhausted(AssertionError):
    """The menu asked for more input than the test scripted."""


def _summary(succeeded: int = 1, failed: int = 0) -> ProcessSummary:
    return ProcessSummary(
        total=succeeded + failed,
        succeeded=succeeded,
        remuxed=succeeded,
        copied_unchanged=0,
        skipped=0,
        no_audio=0,
        failed=failed,
        extra_copied=0,
        extra_skipped=0,
        extra_failed=0,
        size_delta=0,
        elapsed=0.0,
        results=[],
    )


# One video, one Japanese audio track, one English subtitle track: a single
# audio language, so the selection-style menu is skipped and the advanced rule
# steps start immediately.
def _media(path: Path) -> MediaFile:
    return MediaFile(
        path=path,
        streams=[
            StreamInfo(index=0, codec_type="video", codec_name="h264"),
            StreamInfo(index=1, codec_type="audio", codec_name="aac", language="jpn"),
            StreamInfo(index=2, codec_type="subtitle", codec_name="ass", language="eng"),
        ],
        duration_seconds=10.0,
    )


# The advanced rule steps, answered with defaults: keep all audio, all
# subtitles, attachments, metadata, no metadata edits, chapters, copy siblings,
# do not overwrite.
DEFAULT_RULE_ANSWERS = ("4", "1", "", "", "", "", "", "")


class MenuFlowTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.input_dir = self.root / "input"
        self.input_dir.mkdir()
        self.video = self.input_dir / "show.mkv"
        self.video.write_bytes(b"not really a video")
        self.media = _media(self.video)

        self.process_calls: list[dict] = []
        self.verify_calls: list[Path] = []

        self._patch("setup_logging", lambda: None)
        self._patch("require_tool", lambda binary: True)
        self._patch("find_video_files", lambda root: [self.video])
        self._patch("scan_files", lambda files: ScanResult([self.media], []))
        self._patch("process_files", self._fake_process_files)
        self._patch("verify_output", lambda root, rules=None: self.verify_calls.append(root))

    def _patch(self, name: str, replacement):
        original = getattr(mux_app, name)
        setattr(mux_app, name, replacement)
        self.addCleanup(setattr, mux_app, name, original)

    def _fake_process_files(self, media_files, input_root, output_root, rules, probe_failures=()):
        self.process_calls.append({
            "input_root": input_root,
            "output_root": output_root,
            "rules": rules,
            "probe_failures": list(probe_failures),
        })
        return _summary()

    @contextlib.contextmanager
    def keystrokes(self, *answers: str):
        pending = iter(answers)

        def fake_input(_prompt: str = "") -> str:
            try:
                return next(pending)
            except StopIteration:
                raise ScriptExhausted(f"the menu asked past the script {answers!r}") from None

        original = builtins.input
        builtins.input = fake_input
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                yield out
        finally:
            builtins.input = original

    def run_menu(self, *answers: str, allow_back: bool = True):
        original_argv = sys.argv
        sys.argv = ["muxcls"]
        self.addCleanup(setattr, sys, "argv", original_argv)
        with self.keystrokes(*answers) as out:
            self.out = out
            return mux_app.main_menu(allow_back=allow_back)


class SuccessfulRunTests(MenuFlowTestCase):
    def test_a_full_run_returns_the_processing_summary(self):
        summary = self.run_menu(
            str(self.input_dir),
            *DEFAULT_RULE_ANSWERS,
            "",   # output folder -> input parent
            "",   # start processing? -> default yes
            "n",  # verify the output folder? -> no
            "0",  # back at the next input prompt -> leave the menu
        )

        self.assertIsNotNone(summary)
        self.assertEqual(summary.succeeded, 1)
        self.assertEqual(len(self.process_calls), 1)
        self.assertEqual(self.verify_calls, [])

    def test_the_output_root_is_a_named_subfolder_of_the_chosen_base(self):
        self.run_menu(
            str(self.input_dir),
            *DEFAULT_RULE_ANSWERS,
            "", "", "n", "0",
        )

        call = self.process_calls[0]
        self.assertEqual(call["input_root"], self.input_dir)
        self.assertEqual(call["output_root"].parent, self.root)
        self.assertTrue(call["output_root"].name.startswith("input ["))
        self.assertIn("All Audio", call["output_root"].name)

    def test_verify_runs_only_when_it_is_confirmed(self):
        self.run_menu(
            str(self.input_dir),
            *DEFAULT_RULE_ANSWERS,
            "", "",
            "y",  # verify the output folder? -> yes
            "0",
        )

        self.assertEqual(len(self.verify_calls), 1)


class CancelAndBackTests(MenuFlowTestCase):
    def test_declining_to_start_ends_the_menu_without_processing(self):
        summary = self.run_menu(
            str(self.input_dir),
            *DEFAULT_RULE_ANSWERS,
            "",   # output folder
            "n",  # start processing? -> no
        )

        self.assertIsNone(summary)
        self.assertEqual(self.process_calls, [])

    def test_back_at_the_input_prompt_leaves_the_menu(self):
        self.assertIsNone(self.run_menu("0"))
        self.assertEqual(self.process_calls, [])

    def test_back_is_refused_at_the_input_prompt_when_the_menu_owns_the_screen(self):
        # Standalone MuxCls has nowhere to go back to.
        with self.assertRaises(ScriptExhausted):
            self.run_menu("0", allow_back=False)
        self.assertIn("Back is not available here.", self.out.getvalue())

    def test_back_at_the_output_folder_revisits_the_last_rule_step(self):
        summary = self.run_menu(
            str(self.input_dir),
            *DEFAULT_RULE_ANSWERS,
            "0",  # back from the output folder prompt
            "y",  # the revisited last rule step: overwrite -> yes
            "",   # output folder, asked again
            "",   # start processing?
            "n",  # verify?
            "0",
        )

        self.assertIsNotNone(summary)
        self.assertTrue(self.process_calls[0]["rules"].overwrite)


class BlockedRunTests(MenuFlowTestCase):
    def test_a_missing_ffmpeg_stops_the_menu(self):
        self._patch("require_tool", lambda binary: False)
        with self.assertRaises(SystemExit) as caught:
            self.run_menu()
        self.assertEqual(caught.exception.code, 1)

    def test_no_supported_video_files_stops_the_menu(self):
        self._patch("find_video_files", lambda root: [])
        self._patch("find_non_video_extensions", lambda root: [".txt"])
        with self.assertRaises(SystemExit) as caught:
            self.run_menu(str(self.input_dir))
        self.assertEqual(caught.exception.code, 1)
        self.assertIn(".txt", self.out.getvalue())

    def test_a_probe_failure_is_reported_and_may_stop_the_run(self):
        bad = self.input_dir / "broken.mkv"
        self._patch("scan_files", lambda files: ScanResult([self.media], [bad]))
        with self.assertRaises(SystemExit) as caught:
            self.run_menu(str(self.input_dir), "n")
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("broken.mkv", self.out.getvalue())
        self.assertEqual(self.process_calls, [])

    def test_a_probe_failure_may_be_accepted_and_travels_into_processing(self):
        bad = self.input_dir / "broken.mkv"
        self._patch("scan_files", lambda files: ScanResult([self.media], [bad]))
        self.run_menu(
            str(self.input_dir),
            "y",  # continue with the file that scanned
            *DEFAULT_RULE_ANSWERS,
            "", "", "n", "0",
        )

        self.assertEqual(self.process_calls[0]["probe_failures"], [bad])

    def test_a_nonexistent_path_is_re_asked(self):
        missing = self.root / "nope"
        self.run_menu(
            str(missing),
            str(self.input_dir),
            *DEFAULT_RULE_ANSWERS,
            "", "", "n", "0",
        )

        self.assertIn("Path does not exist", self.out.getvalue())
        self.assertEqual(len(self.process_calls), 1)


if __name__ == "__main__":
    unittest.main()
