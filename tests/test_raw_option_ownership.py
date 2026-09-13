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
from ffmwiz.wizard_raw import (RAW_VALUED_OPTIONS_SETTING, VOLUME_MAX, VOLUME_MIN,
                               canonical_raw_option,
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

    # Two spellings load a filter from a file, and both land in the SAME owned
    # option family: canonical_raw_option maps each to `-filter:a`. Which one a
    # build accepts differs -- `-/opt file` is the general form and the 6.1.1 leg
    # rejects it, while `-filter_script:a` is the older filter-only form. The
    # harm is identical either way, so the test asks the build which spelling it
    # speaks rather than skipping on the one it does not.
    FILE_LOADED_SPELLINGS = ("-/filter:a", "-filter_script:a")

    def file_loaded_filter_option(self) -> str:
        """The `<option> <file>` filter spelling THIS ffmpeg accepts.

        Deliberately not a skip. ffmpeg is a capability this job installs, so a
        skip naming it shrinks the suite for a reason the job could fix -- which
        is exactly the false-success shape R08 exists to reject. A build that
        speaks neither spelling is real news about the premise, so it fails.
        """
        probe = self.root / "probe.txt"
        probe.write_text("volume=1", encoding="utf-8")
        for option in self.FILE_LOADED_SPELLINGS:
            out = self.root / "probe.wav"
            result = subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(self.source), "-vn", option, str(probe), str(out)],
                capture_output=True, timeout=180)
            out.unlink(missing_ok=True)
            if result.returncode == 0:
                return option
        self.fail("no file-loaded filter spelling works in this ffmpeg build; "
                  "recheck the premise of the refusal, do not weaken it")

    def test_a_file_loaded_filter_costs_the_user_the_output_they_planned(self):
        """The harm is real on every supported build -- in two different shapes.

        Where the spelling is the SAME option (`-/filter:a`, ffmpeg 7+), the last
        one wins and the user silently gets a different encode. Where it is a
        SEPARATE option (`-filter_script:a`, through 6.x), ffmpeg refuses the
        combination outright with EINVAL and the planned encode never happens.

        So the oracle is not "it overrode" -- it is "the planned output is not
        what the user gets". A raw option that were genuinely harmless would
        succeed AND produce the identical bytes, and that is what fails here.
        """
        option = self.file_loaded_filter_option()
        script = self.root / "filters.txt"
        script.write_text("volume=4", encoding="utf-8")
        loud = self.root / "loud.wav"
        quiet = self.root / "quiet.wav"
        base = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(self.source), "-filter:a", "volume=0.5", "-vn"]
        subprocess.run(base + [str(quiet)], check=True, timeout=180)
        # Not check=True: a refusal IS one of the two harms. The spelling itself
        # is known good on this build -- file_loaded_filter_option() proved it
        # runs alone -- so a nonzero here is about the COMBINATION, nothing else.
        result = subprocess.run(base + [option, str(script), str(loud)],
                                capture_output=True, timeout=180)
        produced = loud.read_bytes() if loud.exists() else b""
        self.assertNotEqual(
            quiet.read_bytes(), produced,
            f"{option} changed nothing: it neither overrode the planned filter "
            f"nor broke the run (exit {result.returncode}), so there is no harm "
            "behind the refusal any more -- recheck the premise")
        with self.assertRaises(ValueError):
            parse_raw_arguments(f"{option} {script}")


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


@requires_ffmpeg
class TheParserAcceptsWhatFFmpegAccepts(unittest.TestCase):
    """A05: the arity contract went too far the other way.

    `-brand iso6` and `-strict -2` are ordinary expert options that real FFmpeg
    runs. The parser refused both as unknown arity and told the user to write
    `-brand=iso6` instead -- which the parser then accepted and FFmpeg exited 8
    on, because that spelling does not exist. A validator must never recommend
    a syntax the tool rejects.

    Every refusal R03 established stays refused; see the classes above.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_a05_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.source = self.root / "in.mp4"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.source)],
                       check=True, timeout=180)

    def ffmpeg_accepts(self, extra: list[str]) -> bool:
        out = self.root / f"out{len(extra)}{abs(hash(tuple(extra))) % 1000}.mp4"
        result = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                                 "-i", str(self.source), "-c", "copy", *extra, str(out)],
                                capture_output=True, timeout=180)
        return result.returncode == 0

    def test_the_expert_options_really_work_in_this_ffmpeg(self):
        # The premise. If a future build drops one, this says so plainly
        # instead of the acceptance test below failing for a hidden reason.
        for extra in (["-brand", "iso6"], ["-strict", "-2"]):
            with self.subTest(extra=extra):
                self.assertTrue(self.ffmpeg_accepts(extra),
                                f"this ffmpeg build no longer accepts {' '.join(extra)}")

    def test_the_parser_accepts_them_in_ffmpeg_s_own_form(self):
        for text in ("-brand iso6", "-strict -2"):
            with self.subTest(text=text):
                self.assertEqual(text.split(), parse_raw_arguments(text))

    def test_a_negative_value_is_a_value_not_an_option(self):
        self.assertEqual(["-strict", "-2"], parse_raw_arguments("-strict -2"))

    def test_no_refusal_ever_recommends_the_ffmpeg_equals_spelling(self):
        # `-opt=value` is the form FFmpeg exits 8 on. Recommending it turned a
        # refusal into a broken command the user then had to debug. The config
        # key the message DOES name contains an `=` of its own, which is why
        # this asserts on the option spelling rather than on the character.
        for option, value in (("-madeupopt", "somevalue"), ("-anotherfakeopt", "12")):
            with self.subTest(option=option):
                with self.assertRaises(ValueError) as caught:
                    parse_raw_arguments(f"{option} {value}")
                message = str(caught.exception)
                self.assertNotIn(f"{option}={value}", message,
                                 f"the refusal still recommends a spelling FFmpeg "
                                 f"rejects: {message}")
                self.assertIn(RAW_VALUED_OPTIONS_SETTING, message,
                              "the refusal does not say how to declare a real option")

    def test_a_genuinely_unknown_option_is_still_refused(self):
        # The R03 guarantee: no guessing that an unknown option eats the next
        # token, because that is how a bare filename became a second output.
        with self.assertRaises(ValueError):
            parse_raw_arguments("-madeupopt extra.wav")

    def test_the_extension_point_is_the_declared_table(self):
        self.assertEqual(1, raw_option_arity("-brand"))
        self.assertEqual(1, raw_option_arity("-strict"))
        self.assertIsNone(raw_option_arity("-madeupopt"))
