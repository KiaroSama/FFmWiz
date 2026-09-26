"""F11-1: wrapper protocols must not hide the file FFmpeg really opens.

`cache:clip.wav` reads `clip.wav`; `tee:a.wav|clip.wav` writes it. The guard used to
compare a file literally named `cache:clip.wav`, found nothing, and FFmpeg truncated
the source. Grammar per FFmpeg master 8864fd0a (libavformat/avio.c url_find_protocol,
concat.c, teeproto.c, libavutil/avstring.c av_get_token).

Run just this file (CI only for this project):
    python tests/run_suite.py -k ffmpeg_url_protocols
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import wave

from ffmwiz import runtime
from ffmwiz.support import L00_command_io as command_io
from ffmwiz.support.L00_ffmpeg_url import ffmpeg_get_token, url_scheme

FFMPEG = shutil.which("ffmpeg")


class SchemeDetectionFollowsFfmpeg(unittest.TestCase):
    def test_plain_and_drive_paths_have_no_scheme(self):
        for value in ("clip.wav", r"C:\media\clip.wav", "c:clip.wav", "a b:c.wav",
                      " leading.wav", "-dash.wav", "subfile,no-colon.wav"):
            with self.subTest(value=value):
                self.assertIsNone(url_scheme(value))

    def test_the_scheme_is_the_leading_run_of_scheme_characters(self):
        cases = {"cache:clip.wav": "cache", "crypto+file:x.wav": "crypto+file",
                 "my.clip:1.wav": "my.clip", "Cache:x.wav": "Cache", "file:x.wav": "file",
                 "subfile,,start,0,end,0,,:x.wav": "subfile", ":x.wav": ""}
        for value, scheme in cases.items():
            with self.subTest(value=value):
                self.assertEqual(scheme, url_scheme(value))


class TokenizerFollowsAvGetToken(unittest.TestCase):
    def test_escapes_quotes_and_trailing_whitespace(self):
        cases = [(("  a\\|b|c", "|"), ("a|b", "|c")),
                 (("'x|y' z|w", "|"), ("x|y z", "|w")),
                 (("a  |b", "|"), ("a", "|b")),
                 (("a\\ |b", "|"), ("a ", "|b")),
                 (("line one\r\nline two", "\r\n"), ("line one", "\r\nline two"))]
        for (text, terms), expected in cases:
            with self.subTest(text=text):
                self.assertEqual(expected, ffmpeg_get_token(text, terms))


class WrappedReads(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffmwiz_urlproto_")
        self.addCleanup(self.temp.cleanup)
        previous = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, previous)

    def reads(self, operand):
        return command_io.command_reads(["ffmpeg", "-i", operand, "out.wav"])

    def test_each_read_wrapper_reaches_the_inner_file(self):
        for operand in ("cache:clip.wav", "async:clip.wav", "crypto:clip.wav",
                        "crypto+file:clip.wav", "shared:clip.wav",
                        "subfile,,start,0,end,0,,:clip.wav", "async:cache:file:clip.wav"):
            with self.subTest(operand=operand):
                reads, problems = self.reads(operand)
                self.assertIn(Path("clip.wav"), reads)
                self.assertEqual([], problems)

    def test_concat_splits_and_skips_empty_segments(self):
        reads, problems = self.reads("concat:a.wav||cache:b.wav")
        self.assertEqual([], problems)
        self.assertIn(Path("a.wav"), reads)
        self.assertIn(Path("b.wav"), reads)

    def test_concatf_reads_the_list_and_every_member(self):
        Path("list.txt").write_text("a.wav\n\n  'b c.wav'\r\ncache:d.wav\n", encoding="utf-8")
        reads, problems = self.reads("concatf:list.txt")
        self.assertEqual([], problems)
        for expected in ("list.txt", "a.wav", "b c.wav", "d.wav"):
            self.assertIn(Path(expected), reads)

    def test_concatf_list_as_output_conflicts(self):
        Path("list.txt").write_text("a.wav\n", encoding="utf-8")
        Path("a.wav").write_bytes(b"x")
        conflict = command_io.command_source_output_conflict(
            ["ffmpeg", "-i", "concatf:list.txt", "-f", "wav", "list.txt"])
        self.assertIsNotNone(conflict)

    def test_unreadable_or_oversized_concatf_list_is_a_refusal(self):
        self.assertTrue(command_io.command_unresolved_dependencies(
            ["ffmpeg", "-i", "concatf:absent.txt", "out.wav"]))
        Path("big.txt").write_bytes(b"a.wav\n" * (command_io.CONCAT_LIST_MAX_BYTES // 6 + 2))
        self.assertTrue(command_io.command_unresolved_dependencies(
            ["ffmpeg", "-i", "concatf:big.txt", "out.wav"]))

    def test_unknown_scheme_and_deep_nesting_are_refusals(self):
        for operand in ("Cache:clip.wav", "my.clip:1.wav", "hls+http://h/x", "cache:" * 17 + "x.wav"):
            with self.subTest(operand=operand[:40]):
                self.assertTrue(command_io.command_unresolved_dependencies(
                    ["ffmpeg", "-i", operand, "out.wav"]))
        self.assertEqual([], command_io.command_unresolved_dependencies(
            ["ffmpeg", "-i", "cache:" * 16 + "x.wav", "out.wav"]))

    def test_a_wrapper_inside_a_concat_list_is_resolved(self):
        Path("list.ffconcat").write_text("ffconcat version 1.0\nfile 'cache:clip.wav'\n",
                                         encoding="utf-8")
        reads, problems = self.reads("list.ffconcat")
        self.assertEqual([], problems)
        self.assertIn(Path("clip.wav"), reads)


class WrappedWrites(unittest.TestCase):
    def writes(self, operand):
        return command_io.command_writes(["ffmpeg", "-i", "in.wav", "-f", "wav", operand])

    def test_write_wrappers_reach_the_inner_file(self):
        self.assertIn(Path("hash.md5"), self.writes("md5:hash.md5"))
        self.assertEqual([], self.writes("md5:"))
        self.assertIn(Path("x.wav"), self.writes("crypto:x.wav"))
        self.assertIn(Path("x.wav"), self.writes("crypto+file:x.wav"))
        tee = self.writes("tee:[f=wav]a.wav|'b|c.wav'")
        self.assertIn(Path("a.wav"), tee)
        self.assertIn(Path("b|c.wav"), tee)

    def test_unknown_output_scheme_is_a_refusal(self):
        self.assertTrue(command_io.command_unresolved_dependencies(
            ["ffmpeg", "-i", "in.wav", "-f", "wav", "foo:out.wav"]))


class EndpointsAndFilterArgumentsKeepWorking(unittest.TestCase):
    def test_streams_and_network_are_not_files_and_not_refusals(self):
        for operand in ("http://host/a.mp4", "pipe:0", "fd:3", "data:,abc", "-"):
            with self.subTest(operand=operand):
                reads, problems = command_io.command_reads(["ffmpeg", "-i", operand, "out.wav"])
                self.assertEqual(([], []), (reads, problems))
        self.assertEqual([], command_io.command_writes(["ffmpeg", "-i", "a.wav", "-f", "wav", "pipe:1"]))

    def test_a_colon_in_a_filter_file_name_stays_literal(self):
        reads, problems = command_io.command_reads(
            ["ffmpeg", "-i", "a.mkv", "-vf", r"subtitles='cues\:one.srt'", "o.mkv"])
        self.assertEqual([], problems)
        self.assertIn(Path("cues:one.srt"), reads)

    def test_a_wrapper_inside_a_filter_argument_is_also_seen(self):
        reads, problems = command_io.command_reads(
            ["ffmpeg", "-f", "lavfi", "-i", r"amovie='cache\:x.wav'", "o.wav"])
        self.assertEqual([], problems)
        self.assertIn(Path("x.wav"), reads)


@unittest.skipUnless(FFMPEG, "ffmpeg is required for wrapper-protocol runtime regressions")
class RuntimeProtection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffmwiz_urlproto_rt_")
        self.addCleanup(self.temp.cleanup)
        previous = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, previous)

    def media(self, name):
        with wave.open(name, "wb") as handle:
            handle.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            handle.writeframes(struct.pack("<8000h", *([1000, -1000] * 4000)))
        return Path(name)

    def run_job(self, source, output, muxer="wav"):
        cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-i", source,
               "-t", "0.25", "-c:a", "pcm_s16le", "-f", muxer, output]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return runtime.run_ffmpeg_with_progress(cmd, total_duration=0.25)

    def assert_refused(self, source_operand, output_operand, protected, muxer="wav"):
        before = protected.read_bytes()
        code, elapsed = self.run_job(source_operand, output_operand, muxer)
        self.assertNotEqual(0, code, "a source-overwriting command was executed")
        self.assertEqual(0.0, elapsed, "the refusal must happen before FFmpeg starts")
        self.assertEqual(before, protected.read_bytes())

    def test_wrapped_inputs_cannot_be_overwritten(self):
        for operand in ("cache:clip.wav", "async:clip.wav", "async:cache:file:clip.wav",
                        "subfile,,start,0,end,0,,:clip.wav", "concat:other.wav|clip.wav"):
            with self.subTest(operand=operand):
                clip = self.media("clip.wav")
                self.media("other.wav")
                self.assert_refused(operand, "clip.wav", clip)

    def test_a_concatf_member_cannot_be_overwritten(self):
        clip = self.media("clip.wav")
        Path("list.txt").write_text("clip.wav\n", encoding="utf-8")
        self.assert_refused("concatf:list.txt", "clip.wav", clip)

    def test_wrapped_outputs_cannot_overwrite_the_source(self):
        clip = self.media("clip.wav")
        self.assert_refused("clip.wav", "tee:copy.raw|clip.wav", clip, muxer="s16le")
        self.assert_refused("clip.wav", "md5:clip.wav", clip)

    def test_positive_controls_encode_through_wrappers(self):
        for operand in ("cache:clip.wav", "concat:clip.wav|other.wav"):
            with self.subTest(operand=operand):
                clip, other = self.media("clip.wav"), self.media("other.wav")
                before = (clip.read_bytes(), other.read_bytes())
                code, _elapsed = self.run_job(operand, "out.wav")
                self.assertEqual(0, code)
                with wave.open("out.wav", "rb") as handle:
                    self.assertEqual(2000, handle.getnframes())
                self.assertEqual(before, (clip.read_bytes(), other.read_bytes()))
                Path("out.wav").unlink()


if __name__ == "__main__":
    unittest.main()
