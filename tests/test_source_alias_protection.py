"""F01: an alias of an input must never be accepted as the output path.

`paths_same` used to compare normalized strings, so a hardlink, a symlink, a
junction or a symlinked parent directory all read as "a different file" and
were kept as the output. A real FFmpeg run then rewrote the user's source in
place and still exited 0.

These tests execute real FFmpeg and compare the input's SHA-256 before and
after, because the helper's Boolean is not the thing that matters -- where the
bytes actually land is.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k source_alias_protection
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz.services_b import build_output_path
from ffmwiz.support.L00_paths import paths_same

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = unittest.skipUnless(bool(FFMPEG), "ffmpeg is not available")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_wav(path: Path, seconds: float = 0.3, frequency: int = 440) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={seconds}",
                    str(path)], check=True, timeout=180)


def try_symlink(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link)
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False        # Windows without the developer/symlink privilege


def try_junction(link: Path, target: Path) -> bool:
    """A directory junction needs no privilege on Windows; nothing elsewhere."""
    if os.name != "nt":
        return False
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True, text=True, timeout=180)
    return result.returncode == 0 and link.exists()


@requires_ffmpeg
class AnAliasOfTheInputIsNeverTheOutput(unittest.TestCase):
    """Every spelling of "the same file" is redirected, and the source survives."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_f01_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.media = self.root / "media"
        self.media.mkdir()
        self.source = self.media / "clip.wav"
        make_wav(self.source)
        self.original_hash = sha256(self.source)

    def answers_for(self, output_location: Path, **extra) -> dict:
        answers = {
            "input_path": self.source,
            "output_location": output_location,
            "output_ext": "wav",
            "output_collision_suffix": "_Encode",
        }
        answers.update(extra)
        return answers

    def encode_into(self, output_path: Path) -> None:
        """A real volume conversion, the operation that destroyed the source."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(self.source), "-filter:a", "volume=0.5",
                        str(output_path)], check=True, timeout=180)

    def assert_source_survived(self, output_path: Path) -> None:
        self.assertFalse(paths_same(output_path, self.source),
                         f"{output_path} still resolves to the source file")
        self.encode_into(output_path)
        self.assertEqual(sha256(self.source), self.original_hash,
                         f"writing to {output_path} rewrote the input")
        self.assertTrue(output_path.exists())
        self.assertNotEqual(sha256(output_path), self.original_hash,
                            "the output is a byte copy of the input, not an encode")

    def test_the_exact_same_path_is_redirected(self):
        self.assert_source_survived(build_output_path(self.answers_for(self.media)))

    def test_a_relative_spelling_of_the_input_is_redirected(self):
        previous = Path.cwd()
        os.chdir(self.media)
        self.addCleanup(os.chdir, previous)
        self.assert_source_survived(build_output_path(self.answers_for(Path("."))))

    def test_a_hardlink_to_the_input_is_redirected(self):
        link = self.media / "alias.wav"
        try:
            os.link(self.source, link)
        except (OSError, AttributeError) as exc:      # non-NTFS, or no support
            self.skipTest(f"hardlinks unavailable: {exc}")
        answers = self.answers_for(self.media, output_name_stem="alias")
        self.assert_source_survived(build_output_path(answers))

    def test_a_symlink_to_the_input_is_redirected(self):
        link = self.media / "linked.wav"
        if not try_symlink(link, self.source):
            self.skipTest("creating a symlink needs a privilege this account lacks")
        answers = self.answers_for(self.media, output_name_stem="linked")
        self.assert_source_survived(build_output_path(answers))

    def test_a_symlinked_directory_holding_the_input_is_redirected(self):
        alias_dir = self.root / "media_alias"
        if not try_symlink(alias_dir, self.media):
            self.skipTest("creating a directory symlink needs a privilege this account lacks")
        self.assert_source_survived(build_output_path(self.answers_for(alias_dir)))

    def test_a_windows_junction_holding_the_input_is_redirected(self):
        alias_dir = self.root / "media_junction"
        if not try_junction(alias_dir, self.media):
            self.skipTest("directory junctions are a Windows feature")
        self.assert_source_survived(build_output_path(self.answers_for(alias_dir)))

    def test_a_case_variant_of_the_input_is_redirected(self):
        if os.path.normcase("A") == "A":
            self.skipTest("this filesystem is case-sensitive, so the name is a different file")
        answers = self.answers_for(self.media, output_name_stem="CLIP")
        self.assert_source_survived(build_output_path(answers))

    def test_a_later_joined_input_is_protected_too(self):
        second = self.media / "second.wav"
        make_wav(second, frequency=880)
        second_hash = sha256(second)
        link = self.media / "second_alias.wav"
        try:
            os.link(second, link)
        except (OSError, AttributeError) as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        answers = self.answers_for(self.media, output_name_stem="second_alias",
                                   join_input_items=[{"path": second}])
        output_path = build_output_path(answers)
        self.encode_into(output_path)
        self.assertEqual(sha256(second), second_hash,
                         "the second joined input was overwritten")
        self.assertEqual(sha256(self.source), self.original_hash)

    def test_a_free_output_name_is_left_alone(self):
        answers = self.answers_for(self.media, output_name_stem="rendered")
        output_path = build_output_path(answers)
        self.assertEqual(output_path.name, "rendered.wav")
        self.encode_into(output_path)
        self.assertEqual(sha256(self.source), self.original_hash)

    def test_an_unrelated_existing_output_is_still_overwritten(self):
        stale = self.media / "rendered.wav"
        make_wav(stale, frequency=220)
        answers = self.answers_for(self.media, output_name_stem="rendered")
        output_path = build_output_path(answers)
        self.assertEqual(output_path, stale,
                         "overwrite policy for an unrelated output must not change")


class PathIdentityIsNotStringEquality(unittest.TestCase):
    """The helper itself, including the cases real files cannot reach."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_f01u_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.file = self.root / "a.bin"
        self.file.write_bytes(b"payload")

    def test_two_genuinely_different_files_are_different(self):
        other = self.root / "b.bin"
        other.write_bytes(b"payload")
        self.assertFalse(paths_same(self.file, other))

    def test_two_missing_paths_compare_by_canonical_text(self):
        self.assertTrue(paths_same(self.root / "x.bin", self.root / "./x.bin"))
        self.assertFalse(paths_same(self.root / "x.bin", self.root / "y.bin"))

    def test_a_hardlink_is_the_same_file(self):
        link = self.root / "link.bin"
        try:
            os.link(self.file, link)
        except (OSError, AttributeError) as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        self.assertTrue(paths_same(self.file, link))

    def test_an_inconclusive_identity_check_is_not_proof_of_difference(self):
        """Access denied means "unknown", and unknown must not mean "safe"."""
        other = self.root / "b.bin"
        other.write_bytes(b"payload")
        real_samefile = os.path.samefile

        def deny(*_args, **_kwargs):
            raise PermissionError(13, "Access is denied")

        os.path.samefile = deny
        self.addCleanup(setattr, os.path, "samefile", real_samefile)
        self.assertTrue(paths_same(self.file, other),
                        "an unreadable identity check was read as 'different files'")

    def test_empty_paths_are_not_the_same_file(self):
        self.assertFalse(paths_same(Path(""), self.file))


if __name__ == "__main__":
    unittest.main()
