"""F03/F04/F05: what a user's own ffmpeg options are allowed to do.

F03 -- `shlex` treated `#` as a comment, so `-metadata title=Episode#1` became
`-metadata title=Episode` and a path with a `#` lost its tail.
F04 -- the denylist was written in the canonical `-c:v` spelling, so the legacy
aliases went through and replaced a codec the wizard owns; a bare pathname went
through as well and made ffmpeg write a second, untracked file.
F05 -- `+10000dB` raised OverflowError, which the prompt's retry contract and
every config consumer (all built on ValueError) never caught.

The accept/reject decisions are checked against real FFmpeg runs, not only
against the parser, because "rejected" only matters if what survives produces
exactly the planned output set.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k raw_option_ownership
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz.wizard_raw import (VOLUME_MAX, VOLUME_MIN, canonical_raw_option,
                               parse_raw_arguments, parse_volume, raw_option_arity)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(bool(FFMPEG and FFPROBE), "ffmpeg/ffprobe not available")


class AHashIsNotAComment(unittest.TestCase):
    """F03."""

    def test_an_unquoted_hash_survives_in_a_metadata_value(self):
        self.assertEqual(parse_raw_arguments("-metadata title=Episode#1 -tune film"),
                         ["-metadata", "title=Episode#1", "-tune", "film"])

    def test_a_quoted_hash_survives(self):
        self.assertEqual(parse_raw_arguments('-metadata title="Episode #1"'),
                         ["-metadata", "title=Episode #1"])

    def test_a_windows_path_with_a_hash_keeps_its_tail(self):
        parsed = parse_raw_arguments(r"-attach C:\Media\clip#2.png")
        self.assertEqual(parsed, ["-attach", r"C:\Media\clip#2.png"])

    def test_options_after_a_hash_are_not_swallowed(self):
        parsed = parse_raw_arguments("-metadata comment=a#b -preset fast")
        self.assertIn("-preset", parsed)
        self.assertIn("fast", parsed)

    def test_a_windows_backslash_path_is_not_an_escape_sequence(self):
        self.assertEqual(parse_raw_arguments(r"-attach C:\Users\me\x.png"),
                         ["-attach", r"C:\Users\me\x.png"])

    def test_a_unicode_value_survives(self):
        self.assertEqual(parse_raw_arguments('-metadata title="فیلم من"'),
                         ["-metadata", "title=فیلم من"])

    def test_an_unmatched_quote_is_a_validation_error(self):
        with self.assertRaises(ValueError):
            parse_raw_arguments('-metadata title="my film')


class OwnedOptionsAndOperandsAreRefused(unittest.TestCase):
    """F04 -- table driven, every alias and qualifier spelling."""

    REJECTED = [
        "-vcodec copy", "-acodec copy", "-scodec copy", "-dcodec copy",
        "-VCODEC copy", "-c:v:0 copy", "-codec:a copy", "-c copy",
        "-i other.mkv", "-map 0:v", "-filter_complex [0:v]null[v]",
        "-vf scale=2:2", "-af volume=2", "-ss 5", "-to 9", "-t 3",
        "out.mp4", "-shortest out.mp4", "-metadata title=x out.mp4",
        "extra.mkv -tune film",
    ]
    ACCEPTED = [
        "-tune film", "-preset veryfast", "-crf 18", "-aq -1",
        "-movflags +faststart", "-x264-params keyint=30",
        "-metadata title=Episode#1", '-metadata comment="a, b"',
        "-an", "-an -tune film", "-g 48", "-bf 2",
    ]

    def test_every_owned_spelling_is_refused(self):
        for text in self.REJECTED:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_raw_arguments(text)

    def test_every_legitimate_expert_option_still_works(self):
        for text in self.ACCEPTED:
            with self.subTest(text=text):
                self.assertTrue(parse_raw_arguments(text))

    def test_aliases_normalize_to_the_family_the_wizard_owns(self):
        self.assertEqual(canonical_raw_option("-vcodec"), "-c:v")
        self.assertEqual(canonical_raw_option("-ACODEC"), "-c:a")
        self.assertEqual(canonical_raw_option("-c:v:0"), "-c:v:0")
        self.assertEqual(canonical_raw_option("-preset"), "-preset")

    def test_a_negative_number_is_a_value_not_an_operand(self):
        self.assertEqual(parse_raw_arguments("-aq -1.5"), ["-aq", "-1.5"])

    def test_the_error_names_the_offending_token(self):
        with self.assertRaises(ValueError) as caught:
            parse_raw_arguments("-tune film surprise.mp4")
        self.assertIn("surprise.mp4", str(caught.exception))


@requires_ffmpeg
class RefusedOptionsWouldHaveBrokenTheJob(unittest.TestCase):
    """The reason each rejection exists, proved by running FFmpeg."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_f04_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.source = self.root / "in.mkv"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(self.source)], check=True, timeout=180)

    def planned_command(self, extra: list[str], out: Path) -> list[str]:
        """The shape FFmWiz builds: raw options just before the output path."""
        return [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(self.source), "-filter:a", "volume=0.5",
                "-c:v", "copy", "-c:a", "aac", *extra, str(out)]

    def test_accepted_audio_copy_would_have_failed_the_planned_job(self):
        out = self.root / "broken.mkv"
        result = subprocess.run(self.planned_command(["-acodec", "copy"], out),
                                capture_output=True, timeout=180)
        self.assertNotEqual(result.returncode, 0,
                            "-acodec copy is refused because it breaks the planned job")

    def test_an_accepted_pathname_would_have_written_a_second_file(self):
        surprise = self.root / "surprise.mkv"
        out = self.root / "planned.mkv"
        result = subprocess.run(self.planned_command([str(surprise)], out),
                                capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(surprise.exists(),
                        "a positional operand really does create an untracked output")
        self.assertTrue(out.exists())

    def test_the_surviving_options_produce_exactly_the_planned_output(self):
        out = self.root / "planned.mkv"
        extra = parse_raw_arguments("-metadata title=Episode#1 -tune film")
        result = subprocess.run(self.planned_command(extra, out), capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ["in.mkv", "planned.mkv"])
        probe = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                                "format_tags=title", "-of", "default=nw=1:nk=1", str(out)],
                               capture_output=True, text=True, timeout=180)
        self.assertEqual(probe.stdout.strip(), "Episode#1")
        codecs = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                                 "stream=codec_name", "-of", "csv=p=0", str(out)],
                                capture_output=True, text=True, timeout=180)
        self.assertIn("aac", codecs.stdout, "the planned audio encoder must survive")


class ArityIsDeclaredNotGuessed(unittest.TestCase):
    """R03 -- the parser must not invent an option's arity."""

    HIDDEN_OUTPUT = ["-report extra.wav", "-nobitexact extra.wav",
                     "-benchmark extra.wav", "-xerror out.mkv"]
    OWNED_BY_ANOTHER_SPELLING = ["-/filter:a filters.txt", "-/af filters.txt",
                                 "-filter_script:a f.txt", "-filter_script:v f.txt",
                                 "-/vf f.txt", "-/c:v copy"]
    MISSING_VALUE = ["-metadata", "-preset", "-crf", "-b:v", "-tune"]

    def test_a_bare_file_after_a_valueless_flag_is_refused(self):
        for text in self.HIDDEN_OUTPUT:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_raw_arguments(text)

    def test_file_loaded_and_script_spellings_reach_the_ownership_check(self):
        for text in self.OWNED_BY_ANOTHER_SPELLING:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_raw_arguments(text)

    def test_a_missing_required_value_is_refused(self):
        for text in self.MISSING_VALUE:
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as caught:
                    parse_raw_arguments(text)
                self.assertIn("needs a value", str(caught.exception))

    def test_an_unknown_option_followed_by_a_bare_token_is_ambiguous(self):
        with self.assertRaises(ValueError) as caught:
            parse_raw_arguments("-totally_unknown_option something.wav")
        message = str(caught.exception)
        self.assertIn("does not know whether", message)
        self.assertIn("something.wav", message)

    def test_an_unknown_option_alone_is_still_allowed(self):
        self.assertEqual(parse_raw_arguments("-totally_unknown_option"),
                         ["-totally_unknown_option"])
        self.assertEqual(parse_raw_arguments("-unknown_a -unknown_b"),
                         ["-unknown_a", "-unknown_b"])

    def test_arity_is_reported_honestly(self):
        self.assertEqual(raw_option_arity("-preset"), 1)
        self.assertEqual(raw_option_arity("-b:v"), 1)
        self.assertEqual(raw_option_arity("-metadata:s:a:0"), 1)
        self.assertEqual(raw_option_arity("-an"), 0)
        self.assertEqual(raw_option_arity("-nobitexact"), 0)
        self.assertIsNone(raw_option_arity("-something_nobody_declared"))

    def test_canonicalization_unwraps_every_spelling(self):
        self.assertEqual(canonical_raw_option("-/filter:a"), "-filter:a")
        self.assertEqual(canonical_raw_option("-filter_script:a"), "-filter:a")
        self.assertEqual(canonical_raw_option("-/vcodec"), "-c:v")

    def test_legitimate_expert_options_still_pass(self):
        for text in ("-attach cover.png", "-metadata:s:a:0 language=eng",
                     "-b:v 2M", "-x264-params keyint=30", "-aq -1",
                     "-pix_fmt yuv420p10le", "-movflags +faststart"):
            with self.subTest(text=text):
                self.assertTrue(parse_raw_arguments(text))


@requires_ffmpeg
class TheRefusedGrammarWouldHaveCostRealOutput(unittest.TestCase):
    """R03 proved with FFmpeg: each refusal prevents a real, observable harm."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_r03_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.source = self.root / "in.mkv"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le",
                        "-shortest", str(self.source)], check=True, timeout=180)

    def planned(self, extra: list[str], out: Path) -> list[str]:
        return [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(self.source), "-filter:a", "volume=0.5",
                "-c:v", "copy", "-c:a", "pcm_s16le", *extra, str(out)]

    def test_a_valueless_flag_plus_a_filename_really_writes_two_files(self):
        surprise = self.root / "extra.wav"
        out = self.root / "planned.mkv"
        result = subprocess.run(self.planned(["-nobitexact", str(surprise)], out),
                                capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(surprise.exists(),
                        "the hidden-output case is not reproducible any more")
        # And the parser refuses exactly that shape.
        with self.assertRaises(ValueError):
            parse_raw_arguments(f"-nobitexact {surprise}")

    def test_a_file_loaded_filter_really_overrides_the_planned_one(self):
        script = self.root / "filters.txt"
        script.write_text("volume=4", encoding="utf-8")
        loud = self.root / "loud.wav"
        quiet = self.root / "quiet.wav"
        base = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(self.source), "-filter:a", "volume=0.5", "-vn"]
        subprocess.run(base + [str(quiet)], check=True, timeout=180)
        subprocess.run(base + ["-/filter:a", str(script), str(loud)],
                       check=True, timeout=180)
        self.assertNotEqual(quiet.read_bytes(), loud.read_bytes(),
                            "the file-loaded filter no longer overrides; recheck the premise")
        with self.assertRaises(ValueError):
            parse_raw_arguments(f"-/filter:a {script}")


class ExtremeVolumeIsAValidationError(unittest.TestCase):
    """F05."""

    def test_a_huge_decibel_value_raises_value_error(self):
        for text in ("+10000dB", "1e400dB", "+400dB"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_volume(text)

    def test_a_huge_negative_decibel_value_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_volume("-10000dB")

    def test_nonfinite_values_are_still_refused(self):
        for text in ("nan", "inf", "-inf", "nandb", "infdb"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_volume(text)

    def test_empty_and_malformed_input_is_refused(self):
        for text in ("", "   ", "loud", "1.5x", "%", "dB"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_volume(text)

    def test_the_supported_range_still_works(self):
        self.assertAlmostEqual(parse_volume("-40dB"), VOLUME_MIN, places=4)
        self.assertAlmostEqual(parse_volume("+20dB"), VOLUME_MAX, places=4)
        self.assertAlmostEqual(parse_volume("150%"), 1.5)
        self.assertAlmostEqual(parse_volume("+6dB"), 1.995262, places=5)

    def test_no_overflow_error_ever_escapes(self):
        for text in ("+10000dB", "1e308dB", "9" * 400 + "dB"):
            with self.subTest(text=text):
                try:
                    parse_volume(text)
                except ValueError:
                    pass
                except OverflowError:               # pragma: no cover
                    self.fail(f"{text} escaped validation as OverflowError")


if __name__ == "__main__":
    unittest.main()
