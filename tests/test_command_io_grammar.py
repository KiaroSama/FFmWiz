"""A01: the guard reads FFmpeg's own grammar, measured rather than assumed.

Eight ways a real FFmpeg run changed a protected file while the guard saw no
conflict. Each is a grammar question, and every answer here was MEASURED against
ffmpeg 9.0.1 rather than taken from a general-purpose parser:

  1  a concat member containing an apostrophe, written `'it'\\''s'`
  2  a concat directive separated by a TAB rather than a space
  3  an `ffconcat version 1.0` list auto-detected with no `-f concat`
  4  a valid list past the size this guard can read -- a refusal, not silence
  5  `file:` naming a file whose name contains a literal `%20`
  6  `file:` naming a file whose name contains a literal `#`
  7  `-vstats_file` -- one value like `-attach`, but a WRITE
  8  a subtitle read by `-vf subtitles=`, which is no `-i` input at all

Where a spelling had to be verified, the test verifies it: a counterexample
built from a command FFmpeg refuses proves nothing, and one of the previous
round's tests was exactly that.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k command_io_grammar
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ffmwiz.support.L00_command_io import (command_reads, command_unresolved_dependencies,
                                           command_writes, concat_list_members,
                                           concat_token, ffmpeg_url_path,
                                           filter_graph_files, looks_like_concat_list)
from ffmwiz.support.L00_paths import command_source_output_conflict

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = unittest.skipUnless(bool(FFMPEG), "ffmpeg not available")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quote_concat(path: Path) -> str:
    """The way all three of this project's concat writers escape a member."""
    return path.as_posix().replace("'", "'\\''")


class TheConcatGrammarIsFFmpegsOwn(unittest.TestCase):
    """`shlex` is not this grammar: it eats the backslashes in a Windows path."""

    def test_an_apostrophe_survives_the_round_trip(self):
        # The writer emits `'it'\''s here.wav'`; a reader that merely strips the
        # outer quotes produced `it'/''s here.wav` and protected nothing.
        original = Path("C:/media/it's here.wav")
        token, _ = concat_token("'" + quote_concat(original) + "'")
        self.assertEqual("C:/media/it's here.wav", token)

    def test_a_quoted_windows_path_keeps_its_backslashes(self):
        token, _ = concat_token(r"'C:\media\a b.wav'")
        self.assertEqual(r"C:\media\a b.wav", token)

    def test_a_backslash_escapes_outside_quotes(self):
        token, _ = concat_token(r"a\ b.wav")
        self.assertEqual("a b.wav", token)

    def test_a_token_stops_at_unquoted_whitespace(self):
        token, index = concat_token("first second")
        self.assertEqual("first", token)
        self.assertEqual(5, index)


class AConcatListIsReadCompletely(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_a01g_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.member = self.root / "part one.wav"
        self.member.write_bytes(b"media")

    def write_list(self, body: str, name: str = "list.txt") -> Path:
        listing = self.root / name
        listing.write_text(body, encoding="utf-8")
        return listing

    def test_a_space_separated_directive(self):
        listing = self.write_list(f"file '{quote_concat(self.member)}'\n")
        members, problems = concat_list_members(listing)
        self.assertEqual([], problems)
        self.assertEqual([self.member], [p.resolve() for p in members])

    def test_a_tab_separated_directive(self):
        listing = self.write_list(f"file\t'{quote_concat(self.member)}'\n")
        members, problems = concat_list_members(listing)
        self.assertEqual([], problems)
        self.assertEqual([self.member], [p.resolve() for p in members],
                         "a tab is whitespace; assuming a single space skipped the line")

    def test_a_member_with_an_apostrophe(self):
        odd = self.root / "it's here.wav"
        odd.write_bytes(b"media")
        listing = self.write_list(f"file '{quote_concat(odd)}'\n")
        members, _ = concat_list_members(listing)
        self.assertEqual([odd], [p.resolve() for p in members])

    def test_a_relative_member_resolves_against_the_list(self):
        listing = self.write_list("file 'part one.wav'\n")
        members, _ = concat_list_members(listing)
        self.assertEqual([self.member], [p.resolve() for p in members])

    def test_comments_and_blank_lines_are_ignored(self):
        listing = self.write_list(
            f"# a comment\n\nfile '{quote_concat(self.member)}'\nduration 1.0\n")
        members, _ = concat_list_members(listing)
        self.assertEqual([self.member], [p.resolve() for p in members])

    # --- the auto-detected spelling --------------------------------------

    def test_an_ffconcat_header_is_recognised_without_f_concat(self):
        listing = self.write_list(
            f"ffconcat version 1.0\nfile '{quote_concat(self.member)}'\n", "auto.ffconcat")
        self.assertTrue(looks_like_concat_list(listing))
        reads, _ = command_reads(["ffmpeg", "-i", str(listing), "out.wav"])
        self.assertIn(self.member, [p.resolve() for p in reads],
                      "an auto-detected list contributed no members, so its media "
                      "was unprotected")

    def test_an_ordinary_media_input_is_not_probed_as_a_list(self):
        reads, problems = command_reads(["ffmpeg", "-i", str(self.member), "out.wav"])
        self.assertEqual([], problems)
        self.assertEqual([self.member], [p.resolve() for p in reads])

    # --- what "unknown" must mean ----------------------------------------

    def test_an_oversized_list_is_a_refusal_not_an_empty_answer(self):
        listing = self.write_list("# " + ("x" * 200) + "\n")
        with listing.open("a", encoding="utf-8") as handle:
            handle.write(("# " + "x" * 200 + "\n") * 22000)
        members, problems = concat_list_members(listing)
        self.assertEqual([], members)
        self.assertTrue(problems, "an unreadable list silently meant 'no dependency'")
        self.assertIn("limit", problems[0])

    def test_a_missing_list_is_a_refusal(self):
        members, problems = concat_list_members(self.root / "absent.txt")
        self.assertEqual([], members)
        self.assertTrue(problems)

    def test_the_command_reports_the_unresolved_list(self):
        listing = self.write_list("")
        listing.unlink()
        cmd = ["ffmpeg", "-f", "concat", "-i", str(listing), "out.wav"]
        self.assertTrue(command_unresolved_dependencies(cmd),
                        "the boundary had nothing to refuse on")

    def test_a_readable_list_reports_no_problem(self):
        listing = self.write_list(f"file '{quote_concat(self.member)}'\n")
        self.assertEqual([], command_unresolved_dependencies(
            ["ffmpeg", "-f", "concat", "-i", str(listing), "out.wav"]))


class TheFileProtocolIsAPrefixNotAUrl(unittest.TestCase):
    def test_a_literal_percent_is_not_decoded(self):
        self.assertEqual(Path("a%20b.wav"), ffmpeg_url_path("file:a%20b.wav"))

    def test_a_literal_hash_is_not_a_fragment(self):
        self.assertEqual(Path("a#b.wav"), ffmpeg_url_path("file:a#b.wav"))

    def test_a_plain_path_is_unchanged(self):
        self.assertEqual(Path(r"C:\media\clip.mkv"), ffmpeg_url_path(r"C:\media\clip.mkv"))

    def test_a_pipe_or_null_sink_is_not_a_file(self):
        for value in ("pipe:1", "-", "NUL", os.devnull):
            with self.subTest(value=value):
                self.assertIsNone(ffmpeg_url_path(value))


class AFiltergraphNamesFilesToo(unittest.TestCase):
    def test_a_quoted_escaped_windows_path_is_recovered(self):
        # The one spelling ffmpeg accepts for a drive path (measured).
        value = "subtitles='C\\:/media/cues.srt'"
        self.assertEqual([Path("C:/media/cues.srt")], filter_graph_files(value))

    def test_a_relative_subtitle_is_recovered(self):
        self.assertEqual([Path("cues.srt")], filter_graph_files("subtitles=cues.srt"))

    def test_the_movie_sources_are_recovered(self):
        found = filter_graph_files("movie='C\\:/media/logo.png'[l];amovie=bed.wav[a]")
        self.assertIn(Path("C:/media/logo.png"), found)
        self.assertIn(Path("bed.wav"), found)

    def test_a_graph_with_no_files_yields_none(self):
        self.assertEqual([], filter_graph_files("scale=1280:-2,fps=30"))

    def test_the_subtitle_reaches_the_command_read_set(self):
        cmd = ["ffmpeg", "-i", "in.mkv", "-vf", "subtitles='C\\:/media/cues.srt'", "out.mkv"]
        reads, _ = command_reads(cmd)
        self.assertIn(Path("C:/media/cues.srt"), reads)


class DirectionIsNotArity(unittest.TestCase):
    def test_vstats_file_is_a_destination(self):
        cmd = ["ffmpeg", "-i", "in.mkv", "-vstats_file", "stats.txt", "out.mkv"]
        self.assertEqual([Path("stats.txt"), Path("out.mkv")], command_writes(cmd))

    def test_vstats_file_is_not_a_source(self):
        cmd = ["ffmpeg", "-i", "in.mkv", "-vstats_file", "stats.txt", "out.mkv"]
        reads, _ = command_reads(cmd)
        self.assertNotIn(Path("stats.txt"), reads)

    def test_passlogfile_also_claims_its_expanded_names(self):
        cmd = ["ffmpeg", "-i", "in.mkv", "-passlogfile", "pass", "out.mkv"]
        writes = command_writes(cmd)
        self.assertIn(Path("pass-0.log"), writes)

    def test_an_attachment_is_still_a_source(self):
        cmd = ["ffmpeg", "-i", "in.mkv", "-attach", "cover.png", "out.mkv"]
        reads, _ = command_reads(cmd)
        self.assertIn(Path("cover.png"), reads)
        self.assertNotIn(Path("cover.png"), command_writes(cmd))


@requires_ffmpeg
class EachCounterexampleReallyDestroysItsSource(unittest.TestCase):
    """The premise, run for real: the guard refuses, and the run would not have.

    A counterexample whose command FFmpeg refuses proves nothing, so every case
    here was measured on ffmpeg 9.0.1 first and the recipe adjusted until the
    protected file genuinely changed. Two adjustments were needed and both are
    worth stating:

    * a re-encode of the same sine to the same codec produces byte-identical
      output, so "the hash changed" only means something when the command
      cannot reproduce the source -- every media case therefore cuts with
      `-t 0.5`;
    * a nonzero exit is NOT source protection. The subtitle case exits nonzero
      on this build and still leaves the SRT at zero bytes, because the
      destination was opened and truncated before the failure.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_a01run_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.source = self.root / "source.wav"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-c:a", "pcm_s16le", str(self.source)], check=True, timeout=180)

    def video(self, name: str) -> Path:
        path = self.root / name
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                       check=True, timeout=180)
        return path

    def alias_of(self, target: Path, name: str) -> Path:
        alias = self.root / name
        try:
            os.link(target, alias)
        except (OSError, NotImplementedError) as exc:   # pragma: no cover
            self.skipTest(f"no hardlink privilege: {exc}")
        return alias

    def assert_refused_and_harmful(self, cmd: list[str], protected: Path,
                                   expect_exit_zero: bool = True) -> None:
        self.assertIsNotNone(command_source_output_conflict(cmd),
                             "the guard would have let this run")
        # Digests, not raw bytes: a failed comparison on media dumps the whole
        # file into the report and buries the reason under a megabyte of WAV.
        before = digest(protected)
        result = subprocess.run(cmd, capture_output=True, timeout=180)
        if expect_exit_zero:
            self.assertEqual(0, result.returncode,
                             "the premise needs a command FFmpeg actually runs:\n"
                             + result.stderr.decode("utf-8", "replace")[-600:])
        self.assertNotEqual(before, digest(protected),
                            f"{protected.name} survived, so this is not the harm claimed")

    def test_a_concat_member_with_an_apostrophe(self):
        odd = self.root / "it's here.wav"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=duration=3", "-c:a", "pcm_s16le",
                        str(odd)], check=True, timeout=180)
        listing = self.root / "apostrophe.txt"
        listing.write_text(f"file '{quote_concat(odd)}'\n", encoding="utf-8")
        alias = self.alias_of(odd, "apo_alias.wav")
        self.assert_refused_and_harmful(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "concat", "-safe", "0", "-i", str(listing), "-t", "0.5",
             str(alias)], odd)

    def test_a_tab_separated_concat_member(self):
        listing = self.root / "tabbed.txt"
        listing.write_text(f"file\t'{quote_concat(self.source)}'\n", encoding="utf-8")
        alias = self.alias_of(self.source, "tab_alias.wav")
        self.assert_refused_and_harmful(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "concat", "-safe", "0", "-i", str(listing), "-t", "0.5",
             str(alias)], self.source)

    def test_an_auto_detected_ffconcat_list(self):
        listing = self.root / "auto.ffconcat"
        listing.write_text(f"ffconcat version 1.0\nfile '{quote_concat(self.source)}'\n",
                           encoding="utf-8")
        alias = self.alias_of(self.source, "auto_alias.wav")
        self.assert_refused_and_harmful(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-safe", "0", "-i", str(listing), "-t", "0.5", str(alias)], self.source)

    def test_a_file_input_whose_name_contains_a_percent(self):
        odd = self.root / "a%20b.wav"
        shutil.copyfile(self.source, odd)
        alias = self.alias_of(odd, "pct_alias.wav")
        self.assert_refused_and_harmful(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-i", f"file:{odd}", "-t", "0.5", str(alias)], odd)

    def test_a_file_input_whose_name_contains_a_hash(self):
        odd = self.root / "a#b.wav"
        shutil.copyfile(self.source, odd)
        alias = self.alias_of(odd, "hash_alias.wav")
        self.assert_refused_and_harmful(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-i", f"file:{odd}", "-t", "0.5", str(alias)], odd)

    def test_vstats_file_pointed_at_the_source(self):
        # `-vstats_file` needs a video stream before it writes anything, and it
        # writes while the real output goes elsewhere -- exit 0, source gone.
        source = self.video("stats_source.mkv")
        alias = self.alias_of(source, "stats_alias.mkv")
        self.assert_refused_and_harmful(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
             "-vstats_file", str(alias), "-c:v", "libx264", "-pix_fmt", "yuv420p",
             str(self.root / "out.mkv")], source)

    def test_a_subtitle_burned_in_by_the_filtergraph(self):
        srt = self.root / "cues.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi there\n\n", encoding="utf-8")
        # A container extension on the destination, because that is the shape a
        # user reaches: the output name they chose happens to be an alias of
        # their own subtitle file.
        alias = self.alias_of(srt, "burned.mkv")
        escaped = srt.as_posix().replace(":", "\\:", 1)
        self.assert_refused_and_harmful(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=1",
             "-vf", f"subtitles='{escaped}'", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             str(alias)],
            srt,
            # Measured on this build: FFmpeg exits nonzero AND leaves the SRT at
            # zero bytes. A nonzero exit is not source protection.
            expect_exit_zero=False)

    def test_the_rfc_file_uri_is_not_a_spelling_this_backend_opens(self):
        # Stated as a measured fact, because the previous round asserted
        # protection for exactly this form -- a command FFmpeg cannot run, so
        # the assertion proved nothing either way.
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-i", self.source.as_uri(), str(self.root / "uri.wav")],
            capture_output=True, timeout=180)
        self.assertNotEqual(0, result.returncode,
                            "this ffmpeg now accepts file:/// URIs; the guard's "
                            "literal reading of `file:` needs rechecking")

if __name__ == "__main__":
    unittest.main()


class ThePlannerActuallyDeclaresWhatItReads(unittest.TestCase):
    """`source_dependencies` is integration only when a caller supplies it.

    The argument existed for a round with no production caller behind it, which
    is an unused parameter, not a contract. The join executor holds the input
    list the concat route hides inside a generated file, so that is where the
    declaration belongs -- and it keeps working when the list itself cannot be
    read (A01).
    """

    def test_the_join_executor_passes_its_input_list(self):
        import inspect

        from ffmwiz import modes_join

        body = inspect.getsource(modes_join.run_join_job) if hasattr(
            modes_join, "run_join_job") else inspect.getsource(modes_join)
        self.assertIn("source_dependencies=", body,
                      "no production caller declares its reads, so the argument "
                      "is decoration")

    def test_a_declared_source_is_protected_even_with_no_readable_list(self):
        root = Path(tempfile.mkdtemp(prefix="ffmwiz_a01dep_")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        member = root / "part.wav"
        member.write_bytes(b"media")
        alias = root / "alias.wav"
        try:
            os.link(member, alias)
        except (OSError, NotImplementedError) as exc:   # pragma: no cover
            self.skipTest(f"no hardlink privilege: {exc}")
        # A list the guard cannot read at all: without the declaration there is
        # nothing to compare the destination against.
        cmd = ["ffmpeg", "-f", "concat", "-i", str(root / "gone.txt"), str(alias)]
        self.assertIsNone(command_source_output_conflict(cmd))
        self.assertIsNotNone(
            command_source_output_conflict(cmd, extra_sources=[member]),
            "a declared source was ignored")
