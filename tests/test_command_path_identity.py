"""A01: compare exactly the pathname the backend opens, not UI-normalized text."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave

from ffmwiz import runtime
from ffmwiz.support import L00_command_io as command_io
from ffmwiz.support.L00_paths import _progress_output_paths_from_command

FFMPEG = shutil.which("ffmpeg")


class LiteralOperands(unittest.TestCase):
    def test_file_operands_keep_significant_characters(self):
        names = [" leading.wav", "trailing.wav ", "-source.wav", "null", "null.wav",
                 "بررسی #1%20.wav", "  spaced  .wav"]
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(Path(name), command_io.ffmpeg_url_path(name))
                self.assertEqual(Path(name), command_io.ffmpeg_url_path("file:" + name))

    def test_protocol_and_device_endpoints_do_not_become_ordinary_files(self):
        for value in ("", "-", "pipe:0", "pipe:1", os.devnull):
            with self.subTest(value=value):
                self.assertIsNone(command_io.ffmpeg_url_path(value))
        self.assertEqual(Path("-"), command_io.ffmpeg_url_path("file:-"))
        self.assertEqual(Path("pipe:1"), command_io.ffmpeg_url_path("file:pipe:1"))
        self.assertEqual(None if os.name == "nt" else Path("NUL"),
                         command_io.ffmpeg_url_path("NUL"))

    def test_dash_prefixed_input_is_consumed_once(self):
        args = ["ffmpeg", "-i", "-i", "-metadata", "title=-i", "out.wav"]
        self.assertEqual([Path("-i")], command_io.command_input_paths(args))
        self.assertEqual([Path("-i")], command_io.command_reads(args)[0])
        self.assertEqual([Path("out.wav")], command_io.command_writes(args))

    def test_null_muxer_scope_ends_at_each_output(self):
        args = ["ffmpeg", "-i", "source.wav", "-f", "null", "ignored.wav", "null"]
        self.assertEqual([Path("null")], command_io.command_writes(args))
        self.assertEqual([Path("null")], command_io.command_writes(
            ["ffmpeg", "-i", "source.wav", "-f", "wav", "null"]))
        self.assertEqual([], command_io.command_writes(
            ["ffmpeg", "-i", "source.wav", "-f", "null", "source.wav"]))

    def test_progress_uses_the_literal_output(self):
        args = ["ffmpeg", "-i", "source.wav", " leading.wav "]
        self.assertEqual([Path(" leading.wav ")], _progress_output_paths_from_command(args))
        self.assertEqual([], _progress_output_paths_from_command(
            ["ffmpeg", "-i", "source.wav", "-f", "null", "source.wav"]))


@unittest.skipUnless(FFMPEG, "ffmpeg is required for literal-path runtime regressions")
class ActualPathProtection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffmwiz_literal_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)

    def media(self, name):
        path = Path(name)
        with wave.open(str(path.absolute()), "wb") as handle:
            handle.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            handle.writeframes(struct.pack("<8000h", *([1000, -1000] * 4000)))
        return path

    def alias(self, source, output):
        try:
            os.link(source, output)
        except OSError as exc:
            self.skipTest(f"hardlink privilege unavailable: {exc}")

    def execute(self, source, output, muxer="wav"):
        cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-i", source,
               "-t", "0.25", "-c:a", "pcm_s16le", "-f", muxer, output]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code, elapsed = runtime.run_ffmpeg_with_progress(cmd, total_duration=0.25)
        return code, elapsed

    def check_alias(self, source_name, output_name):
        source = self.media(source_name)
        self.alias(source, output_name)
        before = source.read_bytes()
        code, elapsed = self.execute(source_name, output_name)
        self.assertNotEqual(0, code, "the actual runtime executed a source-overwriting command")
        self.assertEqual(0.0, elapsed, "collision must be refused before FFmpeg starts")
        self.assertEqual(before, source.read_bytes())

    def test_leading_whitespace_source_is_protected(self):
        self.check_alias(" leading.wav", "out.wav")

    @unittest.skipIf(os.name == "nt", "Win32 normalizes trailing spaces in filenames")
    def test_trailing_whitespace_source_is_protected(self):
        self.check_alias("trailing.wav ", "out.wav")

    def test_dash_prefixed_input_is_protected(self):
        self.check_alias("-source.wav", "out.wav")

    def test_literal_null_source_is_protected(self):
        self.check_alias("null", "out.wav")

    def test_leading_whitespace_output_is_protected(self):
        self.check_alias("source.wav", " out.wav")

    def test_literal_null_output_is_protected(self):
        self.check_alias("source.wav", "null")

    def test_positive_controls_really_encode_the_exact_destinations(self):
        source = self.media("source.wav")
        original = source.read_bytes()
        for output in ("null", " leading.wav", "normal.wav"):
            with self.subTest(output=output):
                code, _ = self.execute("source.wav", output)
                self.assertEqual(0, code)
                with wave.open(output, "rb") as handle:
                    self.assertEqual(2000, handle.getnframes())
                self.assertEqual(original, source.read_bytes())

    def test_null_muxer_does_not_modify_the_named_source(self):
        source = self.media("source.wav")
        original = source.read_bytes()
        code, _ = self.execute("source.wav", "source.wav", "null")
        self.assertEqual(0, code)
        self.assertEqual(original, source.read_bytes())

    def test_concat_members_keep_leading_whitespace(self):
        source = self.media(" part.wav")
        self.alias(source, "out.wav")
        listing = Path("input.ffconcat")
        listing.write_text("ffconcat version 1.0\nfile ' part.wav'\n", encoding="utf-8")
        self.assertIsNotNone(command_io.command_source_output_conflict(
            [FFMPEG, "-safe", "0", "-i", str(listing), "out.wav"]))

    def test_quoted_file_loaded_operand_keeps_whitespace(self):
        source = self.media(" source.wav")
        self.alias(source, "out.wav")
        Path("input.txt").write_text(" source.wav", encoding="utf-8")
        self.assertIsNotNone(command_io.command_source_output_conflict(
            [FFMPEG, "-/i", "input.txt", "out.wav"]))


if __name__ == "__main__":
    unittest.main()
