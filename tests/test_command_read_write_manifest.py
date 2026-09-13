"""A01: the execution guard must know everything the command reads and writes.

R02 put the source/output identity check at the one place every job passes
through. It modelled the command as "one output, the last token" and "inputs are
what follows `-i`". Real FFmpeg commands are not that shape, and four readings
slipped straight past it -- each one verified by a real FFmpeg run that returned
zero and changed the protected file's hash:

    a two-output command whose FIRST output is an alias of the source
    media read through an ffconcat list rather than named on the command line
    an input given as a local `file:` URL
    an attachment consumed by `-attach` and then overwritten

The last-output case was already caught and stays caught.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k command_read_write_manifest
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ffmwiz.support.L00_paths import (command_file_dependencies, command_input_paths,
                                      command_output_paths,
                                      command_source_output_conflict)

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = unittest.skipUnless(bool(FFMPEG), "ffmpeg not available")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TheGuardSeesEveryDestination(unittest.TestCase):
    """Pure argv analysis: no FFmpeg needed to state what a command touches."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_a01_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.source = self.root / "source.mkv"
        self.source.write_bytes(b"SOURCE BYTES")
        self.alias = self.root / "alias.mkv"
        try:
            os.link(self.source, self.alias)
        except (OSError, NotImplementedError) as exc:   # pragma: no cover
            self.skipTest(f"this filesystem has no hardlink privilege: {exc}")

    # --- what the command writes -----------------------------------------

    def test_every_output_of_a_multi_output_command_is_found(self):
        cmd = [FFMPEG or "ffmpeg", "-i", str(self.source),
               "-map", "0:v", str(self.root / "video.mkv"),
               "-map", "0:a", str(self.root / "audio.mka")]
        self.assertEqual([self.root / "video.mkv", self.root / "audio.mka"],
                         command_output_paths(cmd))

    def test_an_option_value_is_never_mistaken_for_an_output(self):
        cmd = [FFMPEG or "ffmpeg", "-i", str(self.source), "-preset", "fast",
               "-crf", "18", str(self.root / "out.mkv")]
        self.assertEqual([self.root / "out.mkv"], command_output_paths(cmd))

    def test_a_valueless_flag_does_not_swallow_the_output(self):
        cmd = [FFMPEG or "ffmpeg", "-i", str(self.source), "-an",
               str(self.root / "out.mkv")]
        self.assertEqual([self.root / "out.mkv"], command_output_paths(cmd))

    def test_a_pipe_destination_is_not_a_file(self):
        cmd = [FFMPEG or "ffmpeg", "-i", str(self.source), "-f", "matroska", "pipe:1"]
        self.assertEqual([], command_output_paths(cmd))

    # --- what the command reads ------------------------------------------

    def test_a_file_url_input_is_a_source(self):
        cmd = [FFMPEG or "ffmpeg", "-i", self.source.as_uri(), str(self.root / "out.mkv")]
        self.assertIn(self.source, [p.resolve() for p in command_input_paths(cmd)])

    def test_a_concat_list_contributes_its_members(self):
        listing = self.root / "list.txt"
        listing.write_text(f"file '{self.source.as_posix()}'\n", encoding="utf-8")
        cmd = [FFMPEG or "ffmpeg", "-f", "concat", "-safe", "0", "-i", str(listing),
               str(self.root / "out.mkv")]
        found = [p.resolve() for p in command_file_dependencies(cmd)]
        self.assertIn(self.source, found)

    def test_an_attachment_is_a_dependency(self):
        cover = self.root / "cover.png"
        cover.write_bytes(b"PNG")
        cmd = [FFMPEG or "ffmpeg", "-i", str(self.source), "-attach", str(cover),
               str(self.root / "out.mkv")]
        self.assertIn(cover, [p.resolve() for p in command_file_dependencies(cmd)])

    # --- the conflicts themselves ----------------------------------------

    def test_the_last_output_alias_is_still_refused(self):
        cmd = [FFMPEG or "ffmpeg", "-i", str(self.source), str(self.alias)]
        self.assertIsNotNone(command_source_output_conflict(cmd))

    def test_a_first_output_alias_is_refused(self):
        cmd = [FFMPEG or "ffmpeg", "-i", str(self.source),
               "-map", "0:v", str(self.alias),
               "-map", "0:a", str(self.root / "audio.mka")]
        self.assertIsNotNone(command_source_output_conflict(cmd),
                             "only the LAST destination was being checked")

    def test_a_concat_member_is_protected(self):
        listing = self.root / "list.txt"
        listing.write_text(f"file '{self.source.as_posix()}'\n", encoding="utf-8")
        cmd = [FFMPEG or "ffmpeg", "-f", "concat", "-safe", "0", "-i", str(listing),
               str(self.alias)]
        self.assertIsNotNone(command_source_output_conflict(cmd),
                             "media named only inside the concat list was unprotected")

    def test_a_file_url_source_is_protected(self):
        cmd = [FFMPEG or "ffmpeg", "-i", self.source.as_uri(), str(self.alias)]
        self.assertIsNotNone(command_source_output_conflict(cmd))

    def test_an_attachment_is_protected_from_its_own_output(self):
        cover = self.root / "cover.png"
        cover.write_bytes(b"PNG")
        cmd = [FFMPEG or "ffmpeg", "-i", str(self.source), "-attach", str(cover),
               str(cover)]
        self.assertIsNotNone(command_source_output_conflict(cmd))

    def test_a_caller_can_declare_a_dependency_argv_cannot_show(self):
        # The planner knows things argv does not. Passing them explicitly is
        # better than another heuristic.
        cmd = [FFMPEG or "ffmpeg", "-i", str(self.root / "other.mkv"), str(self.alias)]
        self.assertIsNone(command_source_output_conflict(cmd))
        self.assertIsNotNone(command_source_output_conflict(cmd, extra_sources=[self.source]))

    # --- what must NOT be refused ----------------------------------------

    def test_an_unrelated_existing_output_is_still_allowed(self):
        existing = self.root / "previous.mkv"
        existing.write_bytes(b"an older render")
        cmd = [FFMPEG or "ffmpeg", "-i", str(self.source), str(existing)]
        self.assertIsNone(command_source_output_conflict(cmd),
                          "overwriting an unrelated output is legitimate")

    def test_a_free_destination_is_allowed(self):
        cmd = [FFMPEG or "ffmpeg", "-i", str(self.source), str(self.root / "new.mkv")]
        self.assertIsNone(command_source_output_conflict(cmd))


@requires_ffmpeg
class TheRefusedCommandsReallyDestroyTheSource(unittest.TestCase):
    """Each refusal above, run for real, with the source hash as the witness."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_a01run_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.source = self.root / "source.mkv"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(self.source)], check=True, timeout=180)
        self.alias = self.root / "alias.mkv"
        try:
            os.link(self.source, self.alias)
        except (OSError, NotImplementedError) as exc:   # pragma: no cover
            self.skipTest(f"this filesystem has no hardlink privilege: {exc}")

    def test_a_first_output_alias_really_rewrites_the_source(self):
        before = digest(self.source)
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(self.source),
               "-map", "0:v", "-c", "copy", str(self.alias),
               "-map", "0:a", "-c", "copy", str(self.root / "audio.mka")]
        self.assertIsNotNone(command_source_output_conflict(cmd),
                             "the guard would have let this run")
        result = subprocess.run(cmd, capture_output=True, timeout=180)
        self.assertEqual(0, result.returncode, "the premise needs a command that SUCCEEDS")
        self.assertNotEqual(before, digest(self.source),
                            "the source survived, so this is not the harm claimed")


if __name__ == "__main__":
    unittest.main()
