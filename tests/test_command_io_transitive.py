"""A01: transitive resources and backend sidecars reach the real execution guard."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from ffmwiz import runtime
from ffmwiz.support import L00_command_io as io
from ffmwiz.support.L00_command_grammar import bounded_text, filter_files, passlog_outputs

FFMPEG = shutil.which("ffmpeg")


class Grammar(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffmwiz_transitive_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_filter_instance_and_reordered_options(self):
        for graph in ("subtitles@named=cues.srt", "subtitles=charenc=UTF-8:filename=cues.srt",
                      "[0:v]subtitles@named=filename=cues.srt[v]"):
            with self.subTest(graph=graph):
                reads, writes, errors = filter_files(graph)
                self.assertEqual(["cues.srt"], reads)
                self.assertEqual([], writes)
                self.assertEqual([], errors)

    def test_two_escaping_layers_keep_delimiters_inside_the_filename(self):
        for graph, expected in ((r"subtitles='C\:/media/cues.srt'", "C:/media/cues.srt"),
                                ("subtitles='cues,part;1.srt'", "cues,part;1.srt"),
                                (r"subtitles='cues\:one.srt'", "cues:one.srt")):
            with self.subTest(graph=graph):
                self.assertEqual([expected], filter_files(graph)[0])

    def test_text_resembling_a_filter_is_not_a_dependency(self):
        self.assertEqual([], filter_files("drawtext=text='subtitles=cues.srt'")[0])

    def test_movie_format_alias_is_not_a_filename(self):
        self.assertEqual(["movie.mkv"], filter_files("movie=filename=movie.mkv:f=matroska")[0])

    def test_explicit_filter_files_and_stats_have_opposite_directions(self):
        reads, writes, errors = filter_files(
            "drawtext=fontfile=font.ttf:textfile=title.txt,lut3d=file=color.cube;"
            "[a][b]psnr@quality=stats_file=quality.log")
        self.assertEqual(["font.ttf", "title.txt", "color.cube"], reads)
        self.assertEqual(["quality.log"], writes)
        self.assertEqual([], errors)

    def test_unparseable_or_file_loaded_filter_options_are_not_empty_success(self):
        for graph in ("subtitles='unterminated", "drawtext=/text=title.txt"):
            with self.subTest(graph=graph):
                self.assertTrue(filter_files(graph)[2])

    def test_concat_cycles_and_depth_are_explicit_refusals(self):
        first, second = self.root / "a.ffconcat", self.root / "b.ffconcat"
        first.write_text("ffconcat version 1.0\nfile 'b.ffconcat'\n")
        second.write_text("ffconcat version 1.0\nfile 'a.ffconcat'\n")
        reads, errors = io.command_reads(["ffmpeg", "-i", str(first), "out.wav"])
        self.assertIn(second, reads)
        self.assertTrue(any("cyclic" in error for error in errors))

    def test_generic_parameter_file_keeps_both_levels_of_dependencies(self):
        graph = self.root / "graph.txt"
        graph.write_text("subtitles@named=cues.srt")
        reads, errors = io.command_reads(["ffmpeg", "-i", "video.mkv", "-/vf", str(graph), "out.mkv"])
        self.assertIn(graph, reads)
        self.assertIn(Path("cues.srt"), reads)
        self.assertEqual([], errors)

    def test_every_existing_indexed_passlog_sidecar_is_protected(self):
        prefix = self.root / "stats"
        unusual = self.root / "stats-7.log.mbtree.temp"
        unusual.touch()
        self.assertIn(unusual, passlog_outputs(prefix))
        self.assertIn(self.root / "stats-0.log.mbtree.temp", passlog_outputs(prefix))

    def test_bounded_reader_refuses_oversize_and_nonregular_files(self):
        data = self.root / "data"
        data.write_bytes(b"12345")
        with self.assertRaises(ValueError):
            bounded_text(data, 4)
        with self.assertRaises((ValueError, OSError)):
            bounded_text(self.root)


@unittest.skipUnless(FFMPEG, "ffmpeg is required for generated-media guard tests")
class ExecutionGuard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ffmwiz_transitive_media_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.old_cwd)
        self.media = subprocess.run
        self.ff(["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "source.wav"])
        self.ff(["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=10:duration=1",
                 "-c:v", "ffv1", "video.mkv"])
        Path("cues.srt").write_text("1\n00:00:00,000 --> 00:00:00,900\nHello\n")

    def ff(self, arguments):
        result = self.media([FFMPEG, "-hide_banner", "-v", "error", "-y", *arguments],
                            capture_output=True, timeout=90)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))

    def guarded(self, arguments, source, alias):
        try:
            os.link(source, alias)
        except OSError as exc:
            self.skipTest(f"hardlink privilege is unavailable: {exc}")
        before = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        with mock.patch.object(runtime.subprocess, "Popen",
                               side_effect=AssertionError("unsafe command reached process launch")) as spawn:
            code, elapsed = runtime.run_ffmpeg_with_progress(
                [FFMPEG, "-hide_banner", "-v", "error", "-y", *arguments], label="source guard")
        self.assertNotEqual(0, code)
        self.assertEqual(0.0, elapsed)
        spawn.assert_not_called()
        self.assertEqual(before, hashlib.sha256(Path(source).read_bytes()).hexdigest())

    def test_oversized_autodetected_concat_cannot_hide_a_member(self):
        Path("list.ffconcat").write_text("ffconcat version 1.0\nfile 'source.wav'\n"
                                          + "# padding\n" * (io.CONCAT_LIST_MAX_BYTES // 10 + 10))
        self.guarded(["-safe", "0", "-i", "list.ffconcat", "out.wav"], "source.wav", "out.wav")

    def test_file_protocol_inside_concat_uses_the_backend_identity(self):
        Path("list.ffconcat").write_text("ffconcat version 1.0\nfile 'file:source.wav'\n")
        self.guarded(["-safe", "0", "-i", "list.ffconcat", "out.wav"], "source.wav", "out.wav")

    def test_nested_concat_protects_the_leaf_media(self):
        Path("inner.ffconcat").write_text("ffconcat version 1.0\nfile 'source.wav'\n")
        Path("outer.ffconcat").write_text("ffconcat version 1.0\nfile 'inner.ffconcat'\n")
        self.guarded(["-safe", "0", "-i", "outer.ffconcat", "out.wav"], "source.wav", "out.wav")

    def test_named_and_reordered_filters_reach_the_runtime(self):
        for number, graph in enumerate(("subtitles@named=cues.srt",
                                        "subtitles=charenc=UTF-8:filename=cues.srt")):
            with self.subTest(graph=graph):
                alias = f"out{number}.mkv"
                self.guarded(["-i", "video.mkv", "-vf", graph, "-c:v", "ffv1", alias],
                             "cues.srt", alias)

    def test_script_contents_are_dependencies_not_just_the_script_path(self):
        Path("graph.txt").write_text("[0:v]subtitles=cues.srt[v]")
        self.guarded(["-i", "video.mkv", "-filter_complex_script", "graph.txt", "-map", "[v]",
                      "-c:v", "ffv1", "out.mkv"], "cues.srt", "out.mkv")

    def test_explicit_font_directory_members_are_sources(self):
        Path("fonts").mkdir()
        font = Path("fonts/custom.ttf")
        font.write_bytes(b"a user-owned font dependency")
        self.guarded(["-i", "video.mkv", "-vf", "subtitles=cues.srt:fontsdir=fonts",
                      "-c:v", "ffv1", "out.mkv"], font, "out.mkv")

    def test_mbtree_temporary_sidecar_is_a_write(self):
        self.guarded(["-i", "video.mkv", "-c:v", "libx264", "-pass", "1",
                      "-passlogfile", "stats", "-an", "-f", "null", os.devnull],
                     "video.mkv", "stats-0.log.mbtree.temp")

    def test_free_and_unrelated_outputs_remain_usable(self):
        before = Path("video.mkv").read_bytes()
        for name in ("free.mkv", "existing.mkv"):
            with self.subTest(name=name):
                if name.startswith("existing"):
                    Path(name).write_bytes(b"older unrelated output")
                code, _ = runtime.run_ffmpeg_with_progress(
                    [FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", "video.mkv",
                     "-vf", "subtitles@review=charenc=UTF-8:filename=cues.srt",
                     "-c:v", "ffv1", name], total_duration=1.0, label="positive control")
                self.assertEqual(0, code)
                self.assertTrue(Path(name).stat().st_size)
                self.assertEqual(before, Path("video.mkv").read_bytes())


if __name__ == "__main__":
    unittest.main()
