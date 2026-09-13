"""A07: a folder is a folder however the user spelled it.

`choose_extract_stream_output_path` ran its bare-name branch BEFORE the shared
directory/file classifier, and asked about a trailing separator using the raw,
still-quoted text. So four spellings of "put it in this folder" wrote a sibling
FILE instead:

    music.wav     an existing relative directory
    ./music.wav   the same folder, written the other way
    music.wav/    explicit directory intent, which Path() then discards
    "D:\\out\\"     quoted: the string ends in `"`, not in a separator

R07 fixed only the absolute branch, which is why the absolute case passes and
these do not. The assertions here are on the DESTINATION ON DISK, not on a
helper's boolean.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k extract_directory_intent
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ffmwiz.support.ext01b import choose_extract_stream_output_path

STREAM = {"index": 1, "codec_type": "audio", "codec_name": "aac"}


class TheRequestedFolderIsWhereItLands(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_a07_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.source = self.root / "in.mkv"
        self.source.write_bytes(b"not really a video")
        self.previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.previous_cwd)

    def choose(self, value: str) -> Path:
        return choose_extract_stream_output_path(self.source, STREAM, value, ".aac")

    def assertInside(self, folder: Path, chosen: Path) -> None:
        self.assertEqual(folder.resolve(), chosen.parent.resolve(),
                         f"{chosen} was not written inside {folder}")

    # --- an existing directory, spelled four ways -------------------------

    def test_a_relative_existing_dotted_directory(self):
        folder = self.root / "music.wav"
        folder.mkdir()
        self.assertInside(folder, self.choose("music.wav"))

    def test_the_same_directory_written_with_a_leading_dot_slash(self):
        folder = self.root / "music.wav"
        folder.mkdir()
        self.assertInside(folder, self.choose("./music.wav"))

    def test_an_absolute_existing_dotted_directory_still_works(self):
        # R07's case: it must keep passing.
        folder = self.root / "abs.wav"
        folder.mkdir()
        self.assertInside(folder, self.choose(str(folder)))

    # --- explicit intent for a directory that does not exist yet ----------

    def test_a_trailing_separator_on_a_relative_name(self):
        self.assertInside(self.root / "music.wav", self.choose("music.wav/"))

    def test_a_quoted_absolute_missing_directory_with_a_trailing_separator(self):
        folder = self.root / "Exports.v1"
        self.assertInside(folder, self.choose(f'"{folder}{os.sep}"'))

    def test_an_unquoted_absolute_missing_directory_still_works(self):
        folder = self.root / "Exports.v2"
        self.assertInside(folder, self.choose(f"{folder}{os.sep}"))

    def test_a_persian_folder_name_with_spaces(self):
        folder = self.root / "خروجی نهایی"
        folder.mkdir()
        self.assertInside(folder, self.choose(str(folder)))

    # --- what must NOT change --------------------------------------------

    def test_a_bare_stem_still_names_the_file_beside_the_input(self):
        chosen = self.choose("track")
        self.assertEqual(self.source.parent.resolve(), chosen.parent.resolve())
        self.assertEqual("track.aac", chosen.name)

    def test_a_bare_name_with_an_extension_still_names_the_file(self):
        chosen = self.choose("track.aac")
        self.assertEqual(self.source.parent.resolve(), chosen.parent.resolve())
        self.assertEqual("track.aac", chosen.name)

    def test_an_explicit_absolute_filename_is_still_a_filename(self):
        target = self.root / "sub"
        target.mkdir()
        chosen = self.choose(str(target / "chosen.aac"))
        self.assertEqual(target.resolve(), chosen.parent.resolve())
        self.assertEqual("chosen.aac", chosen.name)

    def test_an_existing_file_of_that_name_is_a_filename_not_a_folder(self):
        existing = self.root / "taken.aac"
        existing.write_bytes(b"occupied")
        chosen = self.choose(str(existing))
        self.assertEqual(self.root.resolve(), chosen.parent.resolve())
        self.assertNotEqual(existing.resolve(), chosen.resolve(),
                            "an existing output file must not be overwritten silently")

    def test_no_value_still_falls_back_to_the_default_beside_the_input(self):
        chosen = self.choose("")
        self.assertEqual(self.source.parent.resolve(), chosen.parent.resolve())


if __name__ == "__main__":
    unittest.main()
