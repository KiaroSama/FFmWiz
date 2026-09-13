"""F02: a dotted directory is a directory, not an output filename.

`Exports.v1` has a suffix, so every output builder read it as the FILE
`Exports.<ext>` and wrote the job to the folder's SIBLING. The default output
location is the input's own parent, so an ordinary job with no custom name was
affected whenever the media lived in a dotted folder.

Where the file actually lands is what these tests check, not just the string
the builder returned.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k output_location_intent
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz.services_b import build_output_path
from ffmwiz.support.L00_paths import output_location_names_a_file
from ffmwiz.support.L01_paths import build_separator_base_output_path
from ffmwiz.support.L02 import choose_hardsub_output_path, join_default_output_path
from ffmwiz.support.L03 import apply_output_location_value


class DottedDirectoriesAreDirectories(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_f02_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.exports = self.root / "Exports.v1"
        self.exports.mkdir()
        self.input = self.root / "clip.mkv"
        self.input.write_bytes(b"not really a video")

    def answers(self, location: Path, **extra) -> dict:
        answers = {"input_path": self.input, "output_location": location,
                   "output_ext": "mkv", "output_collision_suffix": "_Encode"}
        answers.update(extra)
        return answers

    def test_an_existing_dotted_directory_receives_the_output(self):
        output = build_output_path(self.answers(self.exports))
        self.assertEqual(output.parent, self.exports)
        self.assertEqual(output.name, "clip.mkv")

    def test_the_output_is_written_where_the_builder_said(self):
        output = build_output_path(self.answers(self.exports))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered")
        self.assertTrue((self.exports / "clip.mkv").is_file())
        self.assertFalse((self.root / "Exports.mkv").exists(),
                         "the job landed beside the folder instead of inside it")

    def test_a_default_location_inside_a_dotted_folder_stays_a_folder(self):
        media = self.root / "Season.01"
        media.mkdir()
        source = media / "episode.mkv"
        source.write_bytes(b"x")
        answers = {"input_path": source}
        apply_output_location_value(answers, "")
        answers.update({"output_ext": "mp4", "output_collision_suffix": "_Encode"})
        output = build_output_path(answers)
        self.assertEqual(output.parent, media)
        self.assertEqual(output.name, "episode.mp4")

    def test_a_directory_named_like_a_media_file_is_still_a_directory(self):
        odd = self.root / "music.mp3"
        odd.mkdir()
        output = build_output_path(self.answers(odd, output_ext="mp3"))
        self.assertEqual(output.parent, odd)

    def test_a_persian_dotted_directory_behaves_the_same(self):
        persian = self.root / "خروجی.نسخه۱"
        persian.mkdir()
        output = build_output_path(self.answers(persian))
        self.assertEqual(output.parent, persian)
        self.assertEqual(output.name, "clip.mkv")

    def test_a_relative_dotted_directory_behaves_the_same(self):
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        output = build_output_path(self.answers(Path("Exports.v1")))
        self.assertEqual(output.resolve().parent, self.exports.resolve())

    def test_an_explicit_filename_is_still_a_filename(self):
        target = self.root / "final.mkv"
        output = build_output_path(self.answers(target))
        self.assertEqual(output, target)

    def test_an_existing_file_is_a_filename_whatever_it_is_called(self):
        existing = self.root / "already.mkv"
        existing.write_bytes(b"x")
        self.assertTrue(output_location_names_a_file(existing))

    def test_a_missing_directory_asked_for_with_a_separator_is_a_directory(self):
        answers = {"input_path": self.input}
        apply_output_location_value(answers, str(self.root / "New.v2") + os.sep)
        answers.update({"output_ext": "mkv", "output_collision_suffix": "_Encode"})
        self.assertTrue(answers["output_location_is_dir"])
        output = build_output_path(answers)
        self.assertEqual(output.parent, self.root / "New.v2")
        self.assertEqual(output.name, "clip.mkv")

    def test_a_missing_dotted_path_with_no_separator_is_still_a_filename(self):
        answers = {"input_path": self.input}
        apply_output_location_value(answers, str(self.root / "final.mkv"))
        answers.update({"output_ext": "mkv", "output_collision_suffix": "_Encode"})
        self.assertFalse(answers["output_location_is_dir"])
        self.assertEqual(build_output_path(answers), self.root / "final.mkv")

    def test_the_documented_bare_stem_still_names_the_output(self):
        answers = {"input_path": self.input}
        apply_output_location_value(answers, "my_render")
        self.assertEqual(answers["output_name_stem"], "my_render")
        answers.update({"output_ext": "mkv", "output_collision_suffix": "_Encode"})
        output = build_output_path(answers)
        self.assertEqual(output.name, "my_render.mkv")
        self.assertEqual(output.parent, self.root)


class EveryOutputFlowAgreesOnTheSameIntent(unittest.TestCase):
    """Split, folder, join, hardsub and audio flows share one classifier."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_f02b_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.exports = self.root / "Exports.v1"
        self.exports.mkdir()
        self.input = self.root / "clip.mkv"
        self.input.write_bytes(b"x")

    def test_the_split_builder_writes_into_the_dotted_directory(self):
        answers = {"input_path": self.input, "output_location": self.exports,
                   "output_ext": "mkv"}
        output = build_separator_base_output_path(answers)
        self.assertEqual(output.parent, self.exports)

    def test_the_join_builder_writes_into_the_dotted_directory(self):
        answers = {"output_location": self.exports}
        output = join_default_output_path(answers, self.input)
        self.assertEqual(output.parent, self.exports)

    def test_the_hardsub_builder_writes_into_the_dotted_directory(self):
        output = choose_hardsub_output_path(self.input, "mp4", self.exports)
        self.assertEqual(output.parent, self.exports)

    def test_the_audio_tool_does_not_read_the_folder_name_as_an_extension(self):
        from ffmwiz.support.L01_audio import resolve_audio_tool_output_ext
        answers = {"input_path": self.input, "output_location": self.exports}
        self.assertNotEqual(resolve_audio_tool_output_ext(answers), "v1",
                            "the folder's .v1 suffix was taken as the audio format")
        # An explicit audio filename still selects its own format.
        explicit = {"input_path": self.input, "output_location": self.root / "song.flac"}
        self.assertEqual(resolve_audio_tool_output_ext(explicit), "flac")


if __name__ == "__main__":
    unittest.main()
