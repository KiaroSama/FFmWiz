"""Regression tests for the Stream Cleanup Remux port (ffmwiz.muxcleanup).

These lock in the behaviour the vendored subsystem must have after being
re-synced with upstream MuxCls, plus the FFmWiz-local embedding contract in
``ffmwiz.support.ext00b`` that upstream does not provide.
"""
from __future__ import annotations

import contextlib
import inspect
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ffmwiz.muxcleanup import app as mux_app
from ffmwiz.muxcleanup import logsetup as mux_logsetup
from ffmwiz.muxcleanup import media as mux_media
from ffmwiz.muxcleanup import muxlogic as mux_logic
from ffmwiz.muxcleanup import output as mux_output
from ffmwiz.muxcleanup import processing as mux_processing
from ffmwiz.muxcleanup import prompts as mux_prompts
from ffmwiz.muxcleanup import selection as mux_selection
from ffmwiz.muxcleanup import textutil as mux_textutil
from ffmwiz.muxcleanup.constants import (
    AUDIO_ALL,
    AUDIO_BY_LANGUAGE,
    SUBTITLE_ALL,
)
from ffmwiz.muxcleanup.models import MediaFile, SelectionRules, StreamInfo

# ext00b back-imports its parent, so the parent must be importable first for a
# plain ``import`` here. That ordering dependency is itself under test below.
import ffmwiz.support.ext00  # noqa: F401
from ffmwiz.support import ext00b

FFPROBE_AVAILABLE = shutil.which("ffprobe") is not None
UPSTREAM_REPO = Path(r"G:\Program Files\Portable\Scripts\MuxCls")
UPSTREAM_COMMIT = "8a9b948"
VENDOR_HEADER = "# Part of the FFmWiz Stream Cleanup Remux subsystem.\n"


def _rules(**overrides) -> SelectionRules:
    base = dict(
        audio_mode=AUDIO_ALL,
        audio_languages=[],
        audio_titles=[],
        audio_indexes=[],
        subtitle_mode=SUBTITLE_ALL,
        subtitle_languages=[],
        subtitle_titles=[],
        subtitle_indexes=[],
        keep_attachments=True,
        keep_metadata=True,
        keep_chapters=True,
        overwrite=False,
        copy_non_video_files=False,
    )
    base.update(overrides)
    return SelectionRules(**base)


class OutputRootTests(unittest.TestCase):
    def test_overwrite_reuses_the_folder_that_was_asked_for(self):
        """D03: overwrite=True must not create a numbered sibling tree."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_root = root / "Series"
            input_root.mkdir()
            output_base = root / "Out"
            output_base.mkdir()
            rules = _rules(overwrite=True)
            first = mux_output.resolve_output_root(input_root, output_base, rules)
            first.mkdir(parents=True)
            second = mux_output.resolve_output_root(input_root, output_base, rules)
            self.assertEqual(first, second)

    def test_output_base_inside_input_is_rejected(self):
        """D06: run 2 must not be able to re-ingest run 1's output."""
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td) / "Series"
            input_root.mkdir()
            rules = _rules()
            with self.assertRaises(RuntimeError):
                mux_output.resolve_output_root(input_root, input_root, rules)
            with self.assertRaises(RuntimeError):
                mux_output.resolve_output_root(input_root, input_root / "Muxed", rules)

    def test_single_file_input_may_write_beside_itself(self):
        """The containment rule is folder-only; a dropped file has no tree."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clip = root / "clip.mkv"
            clip.write_bytes(b"0")
            self.assertEqual(mux_output.resolve_output_root(clip, root, _rules()), root)


class DispositionTests(unittest.TestCase):
    @staticmethod
    def _media() -> MediaFile:
        return MediaFile(path=Path("in.mkv"), streams=[
            StreamInfo(index=0, codec_type="video"),
            StreamInfo(index=1, codec_type="audio", language="eng"),
            StreamInfo(index=2, codec_type="audio", language="jpn", disposition_default=1),
            StreamInfo(index=3, codec_type="subtitle", language="eng", disposition_default=1),
        ])

    def test_source_default_flags_are_carried(self):
        """NEW-MUX1: the source's default track must stay the default track."""
        media = self._media()
        cmd, _, _ = mux_logic.build_ffmpeg_command(
            Path("in.mkv"), Path("out.mkv"), media, _rules()
        )
        text = " ".join(cmd)
        self.assertIn("-disposition:a:0 -default", text)
        self.assertIn("-disposition:a:1 +default", text)

    def test_no_remux_purely_to_normalize_dispositions(self):
        """NEW-MUX1: keeping every stream must need no remux at all."""
        media = self._media()
        rules = _rules()
        reasons = mux_logic.remux_needed_reasons(
            media, rules,
            mux_logic.selected_audio_streams(media, rules),
            mux_logic.selected_subtitle_streams(media, rules),
        )
        self.assertEqual(reasons, [])

    def test_ffmpeg_is_launched_with_nostdin(self):
        """NEW-MUX7: ffmpeg must not eat the wizard's keystrokes."""
        cmd, _, _ = mux_logic.build_ffmpeg_command(
            Path("in.mkv"), Path("out.mkv"), self._media(), _rules()
        )
        self.assertIn("-nostdin", cmd)


class SelectionSkipTests(unittest.TestCase):
    def test_single_audio_track_still_reaches_the_audio_menu(self):
        """NEW-MUX5: 'remove all audio' only exists inside that menu."""
        media = MediaFile(path=Path("a.mkv"), streams=[
            StreamInfo(index=0, codec_type="video"),
            StreamInfo(index=1, codec_type="audio", language="jpn"),
        ])
        self.assertFalse(mux_selection.should_skip_audio_selection([media]))

    def test_no_audio_at_all_skips_the_audio_menu(self):
        media = MediaFile(path=Path("a.mkv"), streams=[StreamInfo(index=0, codec_type="video")])
        self.assertTrue(mux_selection.should_skip_audio_selection([media]))


class ScanResultTests(unittest.TestCase):
    def test_scan_files_reports_failures_separately(self):
        """NEW-MUX4: unreadable files must not vanish from the run."""
        result = mux_media.scan_files([])
        self.assertEqual(list(result.files), [])
        self.assertEqual(list(result.failures), [])

    @unittest.skipUnless(FFPROBE_AVAILABLE, "ffprobe not found in PATH")
    def test_unreadable_file_is_returned_as_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            bogus = Path(td) / "broken.mkv"
            bogus.write_text("not media", encoding="utf-8")
            result = mux_media.scan_files([bogus])
            self.assertEqual(list(result.files), [])
            self.assertEqual([Path(p) for p in result.failures], [bogus])


class _FakeProc:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


class ProcessFilesTests(unittest.TestCase):
    """process_files must return a summary and never lie about what it wrote."""

    def _run(self, media_files, input_root, output_root, rules):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            return mux_processing.process_files(media_files, input_root, output_root, rules)

    def test_file_without_video_stream_fails_and_writes_nothing(self):
        """NEW-MUX2 + D04."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            indir = root / "In"
            indir.mkdir()
            audio_only = indir / "song.mkv"
            audio_only.write_bytes(b"0" * 64)
            media = MediaFile(path=audio_only, streams=[
                StreamInfo(index=0, codec_type="audio", language="eng"),
            ])
            out_root = root / "Out"
            summary = self._run([media], indir, out_root, _rules())
            self.assertEqual(summary.failed, 1)
            self.assertEqual(summary.succeeded, 0)
            self.assertEqual(list(out_root.rglob("*.mkv")), [])

    def test_file_without_audio_counts_as_no_audio_match(self):
        """NEW-MUX3."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            indir = root / "In"
            indir.mkdir()
            silent = indir / "silent.mkv"
            silent.write_bytes(b"0" * 64)
            media = MediaFile(path=silent, streams=[StreamInfo(index=0, codec_type="video")])
            out_root = root / "Out"
            rules = _rules(audio_mode=AUDIO_BY_LANGUAGE, audio_languages=["jpn"])
            summary = self._run([media], indir, out_root, rules)
            self.assertEqual(summary.no_audio, 1)
            self.assertEqual(summary.succeeded, 0)
            self.assertEqual(list(out_root.rglob("*.mkv")), [])

    def test_failed_remux_leaves_no_partial_output(self):
        """NEW-MUX8: a truncated file must not survive, nor be counted."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            indir = root / "In"
            indir.mkdir()
            src = indir / "E01.mkv"
            src.write_bytes(b"0" * 4096)
            media = MediaFile(path=src, streams=[
                StreamInfo(index=0, codec_type="video"),
                StreamInfo(index=1, codec_type="audio", language="eng"),
                StreamInfo(index=2, codec_type="audio", language="jpn"),
            ])
            out_root = root / "Out"
            rules = _rules(audio_mode=AUDIO_BY_LANGUAGE, audio_languages=["jpn"], overwrite=True)

            def fake_run(cmd, *args, **kwargs):
                # ffmpeg writes a bit and then dies, exactly like a real failure.
                Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[-1]).write_bytes(b"partial")
                return _FakeProc(1, "boom")

            original = mux_processing.run_with_progress
            mux_processing.run_with_progress = fake_run
            try:
                summary = self._run([media], indir, out_root, rules)
            finally:
                mux_processing.run_with_progress = original

            self.assertEqual(summary.failed, 1)
            self.assertEqual(summary.succeeded, 0)
            self.assertEqual(summary.size_delta, 0)
            leftovers = [p for p in out_root.rglob("*") if p.is_file()]
            self.assertEqual(leftovers, [], f"partial output left behind: {leftovers}")


class VerifyWiringTests(unittest.TestCase):
    def test_verify_output_is_reachable_from_the_menu(self):
        """NEW-MUX6: it was implemented and tested but wired to nothing."""
        self.assertIs(getattr(mux_app, "verify_output", None), mux_processing.verify_output)


class LogSetupTests(unittest.TestCase):
    def test_log_file_name_is_utc_stamped(self):
        """NEW-MUX11: the project convention is UTC, not local time."""
        source = inspect.getsource(mux_logsetup.setup_logging)
        self.assertIn("timezone.utc", source)
        self.assertIn("_UTC.log", source)

    def test_log_root_stays_at_the_ffmwiz_project_root(self):
        """The one vendored path edit; upstream has parent.parent."""
        source = inspect.getsource(mux_logsetup.setup_logging)
        self.assertIn("parents[2]", source)


class EmbeddingContractTests(unittest.TestCase):
    """ffmwiz/support/ext00b.py — the FFmWiz-local half the port cannot deliver."""

    def setUp(self):
        self.calls: list[dict] = []

    def _patch_main_menu(self, func):
        original = mux_app.main_menu

        def recorder(**kwargs):
            self.calls.append(kwargs)
            return func(**kwargs)

        mux_app.main_menu = recorder
        self.addCleanup(setattr, mux_app, "main_menu", original)

    def test_menu_back_is_available_when_embedded(self):
        """D05: main_menu must be able to hand Back back to FFmWiz, and the
        embedding layer must actually ask for it."""
        self.assertIn("allow_back", inspect.signature(mux_app.main_menu).parameters)
        self._patch_main_menu(lambda **kwargs: None)
        ext00b.run_mux_cleanup_mode({})
        self.assertEqual(self.calls, [{"allow_back": True}])

    def test_exit_token_leaves_the_tool_not_the_wizard(self):
        """D05: inside the wizard, 'exit this tool' means the tool."""
        def raise_exit(**kwargs):
            raise mux_prompts.MenuExit()

        self._patch_main_menu(raise_exit)
        self.assertIsNone(ext00b.run_mux_cleanup_mode({}))

    def test_back_returns_to_the_ffmwiz_main_menu(self):
        def raise_back(**kwargs):
            raise mux_prompts.MenuBack()

        self._patch_main_menu(raise_back)
        self.assertIsNone(ext00b.run_mux_cleanup_mode({}))

    def test_system_exit_is_not_reported_as_an_ffmpeg_failure(self):
        """NEW-MUX9: no ffmpeg ran, so there is no ffmpeg exit code."""
        def raise_exit(**kwargs):
            raise SystemExit(1)

        self._patch_main_menu(raise_exit)
        self.assertIsNone(ext00b.run_mux_cleanup_mode({}))

    def test_keyboard_interrupt_returns_to_the_menu(self):
        def interrupt(**kwargs):
            raise KeyboardInterrupt()

        self._patch_main_menu(interrupt)
        self.assertIsNone(ext00b.run_mux_cleanup_mode({}))

    def test_failed_run_reports_a_failure_exit_code(self):
        """D04: a 0-of-N run must not read as a clean return."""
        summary = mux_processing.ProcessSummary(
            total=1, succeeded=0, remuxed=0, copied_unchanged=0, skipped=0,
            no_audio=0, failed=1, extra_copied=0, extra_skipped=0, extra_failed=0,
            size_delta=0, elapsed=0.0, results=[],
        )
        self._patch_main_menu(lambda **kwargs: summary)
        result = ext00b.run_mux_cleanup_mode({})
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 1)

    def test_successful_run_reports_success(self):
        summary = mux_processing.ProcessSummary(
            total=1, succeeded=1, remuxed=1, copied_unchanged=0, skipped=0,
            no_audio=0, failed=0, extra_copied=0, extra_skipped=0, extra_failed=0,
            size_delta=0, elapsed=0.0, results=[],
        )
        self._patch_main_menu(lambda **kwargs: summary)
        result = ext00b.run_mux_cleanup_mode({})
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 0)

    def test_screen_ownership_is_released_when_the_menu_raises(self):
        """USER-8-4: the live progress block owns the cursor and the
        screen-owner flag. Standalone the process exits and takes them with it;
        embedded, the wizard keeps running with a hidden cursor and a flag that
        silences its own progress printers."""
        def leave_screen_owned(**kwargs):
            mux_textutil.set_block_owns_screen(True)
            raise RuntimeError("interrupted mid-run")

        self.addCleanup(mux_textutil.set_block_owns_screen, False)
        self._patch_main_menu(leave_screen_owned)
        ext00b.run_mux_cleanup_mode({})
        self.assertFalse(mux_textutil.block_owns_screen())

    def test_argv_is_restored_even_when_the_menu_raises(self):
        """USER-8-2: FFmWiz's own CLI args must survive the tool."""
        def boom(**kwargs):
            self.assertEqual(sys.argv, ["MuxCls"])
            raise RuntimeError("boom")

        self._patch_main_menu(boom)
        before = list(sys.argv)
        result = ext00b.run_mux_cleanup_mode({})
        self.assertEqual(sys.argv, before)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 1)


def _upstream_available() -> bool:
    return (UPSTREAM_REPO / ".git").exists() and shutil.which("git") is not None


@unittest.skipUnless(_upstream_available(), "upstream MuxCls checkout not present")
class VendoringContractTests(unittest.TestCase):
    """USER-8-1: the vendored tree is upstream plus exactly three local edits.

    Recording the contract here means the next drift is visible immediately
    instead of having to be reconstructed by diffing against every commit.
    """

    LOCAL_EDITS = {
        # main_menu is the embedding seam: standalone MuxCls cannot go "back"
        # and has no caller to report an outcome to, so neither exists upstream.
        "app.py": [
            ("from .processing import print_ready_for_next_task, process_files, verify_output\n"
             "\n"
             "def main_menu() -> None:\n",
             "from .processing import ProcessSummary, print_ready_for_next_task, process_files, verify_output\n"
             "\n"
             "def main_menu(allow_back: bool = False) -> Optional[ProcessSummary]:\n"
             "    # allow_back is the embedding switch: standalone MuxCls has nowhere to go\n"
             "    # back to, but inside FFmWiz the input prompt is a way out to the wizard\n"
             "    # menu. The last run's summary travels back with it so the caller can\n"
             "    # report the outcome instead of guessing at it.\n"),
            ("    input_from_args = input_path_from_args(sys.argv[1:])\n",
             "    input_from_args = input_path_from_args(sys.argv[1:])\n"
             "    last_summary: Optional[ProcessSummary] = None\n"),
            ('        if input_root is None:\n'
             '            input_root = ask_path(\n'
             '                "Input file or folder path (drag/drop here, then press Enter)",\n'
             '                must_exist=True,\n'
             '                allow_back=False,\n'
             '            )\n',
             '        if input_root is None:\n'
             '            try:\n'
             '                input_root = ask_path(\n'
             '                    "Input file or folder path (drag/drop here, then press Enter)",\n'
             '                    must_exist=True,\n'
             '                    allow_back=allow_back,\n'
             '                )\n'
             '            except MenuBack:\n'
             '                LOGGER.info("Back requested at the input path; leaving the menu")\n'
             '                return last_summary\n'),
            ('                LOGGER.info("User cancelled before processing")\n'
             '                print(warn("Cancelled."))\n'
             '                return\n',
             '                LOGGER.info("User cancelled before processing")\n'
             '                print(warn("Cancelled."))\n'
             '                return last_summary\n'),
            ("            summary = process_files(media_files, input_root, output_root, rules, scan.failures)\n",
             "            summary = process_files(media_files, input_root, output_root, rules, scan.failures)\n"
             "            last_summary = summary\n"),
            ("        if not restart_input:\n"
             "            break\n"
             "\n"
             "\n"
             "def main() -> None:\n",
             "        if not restart_input:\n"
             "            break\n"
             "\n"
             "    return last_summary\n"
             "\n"
             "\n"
             "def main() -> None:\n"),
        ],
        "__init__.py": [
            ('"""MuxCls package (split from the original single-file MuxCls.py)."""',
             '"""FFmWiz Stream Cleanup Remux subsystem."""'),
        ],
        "logsetup.py": [
            ("        # This module lives in the muxcls package, so the project root (where the\n"
             "        # Logs folder belongs) is the parent of the package directory.\n"
             "        log_root = Path(__file__).resolve().parent.parent / \"Logs\"",
             "        # This module lives in ffmwiz/muxcleanup, so the project root (where the\n"
             "        # Logs folder belongs) is two directories up from the package.\n"
             "        log_root = Path(__file__).resolve().parents[2] / \"Logs\""),
        ],
    }

    def test_vendored_tree_matches_pinned_upstream(self):
        vendored_dir = Path(__file__).resolve().parents[1] / "ffmwiz" / "muxcleanup"
        listing = subprocess.run(
            ["git", "ls-tree", "--name-only", f"{UPSTREAM_COMMIT}:muxcls"],
            cwd=str(UPSTREAM_REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=60,
        )
        self.assertEqual(listing.returncode, 0, listing.stderr)
        upstream_names = sorted(n for n in listing.stdout.split() if n.endswith(".py"))
        vendored_names = sorted(p.name for p in vendored_dir.glob("*.py"))
        self.assertEqual(vendored_names, upstream_names)

        for name in upstream_names:
            shown = subprocess.run(
                ["git", "show", f"{UPSTREAM_COMMIT}:muxcls/{name}"],
                cwd=str(UPSTREAM_REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=60,
            )
            self.assertEqual(shown.returncode, 0, shown.stderr.decode("utf-8", "replace"))
            expected = shown.stdout.decode("utf-8").replace("\r\n", "\n")
            for old, new in self.LOCAL_EDITS.get(name, []):
                self.assertIn(old, expected, f"{name}: upstream text moved, edit no longer applies")
                expected = expected.replace(old, new)
            expected = VENDOR_HEADER + expected
            actual = (vendored_dir / name).read_text(encoding="utf-8")
            self.assertEqual(actual, expected, f"{name} drifted from upstream {UPSTREAM_COMMIT}")


if __name__ == "__main__":
    unittest.main()
