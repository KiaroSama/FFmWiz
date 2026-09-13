"""R02: every source a job reads is protected, not just the primary input.

`paths_same` was fixed, but Track Manager never handed it the external tracks.
Reproducer from the audit: a video-only `clip.mkv`, an added `external.wav`, and
a hardlink from that WAV to the default destination `clip_TrackEdit.mkv`. The
planner kept the destination, the builder emitted both inputs, real FFmpeg
returned 0 -- and the external WAV's SHA-256 changed. Fixing `paths_same` again
would not have fixed the caller.

Two layers are tested here, because the audit asks for both: the PLANNER must
consider every source, and the EXECUTION boundary must re-check, since a
destination that was free at confirmation can be an alias by the time it runs.

Run just this file locally, from the repo root:
    python tests/run_suite.py -k track_manager_source_safety
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import FFmWiz
from ffmwiz import runtime
from ffmwiz.support.ext01b import build_track_manager_command
from ffmwiz.support.L00_paths import (command_input_paths, command_output_path,
                                      command_source_output_conflict)
from ffmwiz.support.L02 import track_manager_output_path

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = unittest.skipUnless(bool(FFMPEG), "ffmpeg is not available")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TheExecutionGuardReadsTheCommandItself(unittest.TestCase):
    """The shared last-line check, independent of any one builder."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_r02u_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.source = self.root / "in.mkv"
        self.source.write_bytes(b"source")

    def test_inputs_and_output_are_parsed_out_of_argv(self):
        cmd = ["ffmpeg", "-i", "a.mkv", "-i", "b.wav", "-c", "copy", "out.mkv"]
        self.assertEqual([p.name for p in command_input_paths(cmd)], ["a.mkv", "b.wav"])
        self.assertEqual(command_output_path(cmd).name, "out.mkv")

    def test_a_non_file_sink_is_not_an_output(self):
        for sink in ("-", "NUL", "pipe:1", os.devnull):
            with self.subTest(sink=sink):
                self.assertIsNone(command_output_path(["ffmpeg", "-i", "a.mkv", "-f", "null", sink]))

    def test_a_command_that_only_reads_has_no_output(self):
        self.assertIsNone(command_output_path(["ffprobe", "-show_streams", "-i", "a.mkv"]))

    def test_unrelated_input_and_output_do_not_conflict(self):
        other = self.root / "out.mkv"
        self.assertIsNone(command_source_output_conflict(
            ["ffmpeg", "-i", str(self.source), str(other)]))

    def test_an_exact_repeat_is_a_conflict(self):
        conflict = command_source_output_conflict(
            ["ffmpeg", "-i", str(self.source), str(self.source)])
        self.assertIsNotNone(conflict)

    def test_a_hardlinked_output_is_a_conflict(self):
        link = self.root / "alias.mkv"
        try:
            os.link(self.source, link)
        except (OSError, AttributeError) as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        conflict = command_source_output_conflict(
            ["ffmpeg", "-i", str(self.source), str(link)])
        self.assertIsNotNone(conflict, "a hardlinked destination slipped past the guard")

    def test_the_second_input_is_checked_too(self):
        extra = self.root / "extra.wav"
        extra.write_bytes(b"audio")
        link = self.root / "alias.wav"
        try:
            os.link(extra, link)
        except (OSError, AttributeError) as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        conflict = command_source_output_conflict(
            ["ffmpeg", "-i", str(self.source), "-i", str(extra), str(link)])
        self.assertIsNotNone(conflict, "only the FIRST input was checked")
        self.assertEqual(conflict[0].name, "extra.wav")

    def test_the_runner_refuses_instead_of_running(self):
        link = self.root / "alias.mkv"
        try:
            os.link(self.source, link)
        except (OSError, AttributeError) as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        before = sha256(self.source)
        with mock.patch.object(runtime.subprocess, "Popen") as popen:
            code, elapsed = runtime.run_ffmpeg_with_progress(
                ["ffmpeg", "-i", str(self.source), str(link)], label="guard test")
        popen.assert_not_called()
        self.assertNotEqual(code, 0)
        self.assertEqual(sha256(self.source), before)


class ThePlannerConsidersEverySource(unittest.TestCase):
    """Track Manager's destination, against the primary AND the added tracks."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_r02p_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.clip = self.root / "clip.mkv"
        self.clip.write_bytes(b"video")

    def test_a_free_destination_is_unchanged(self):
        chosen = track_manager_output_path(self.clip, [])
        self.assertEqual(chosen.name, "clip_TrackEdit.mkv")

    def test_a_hardlinked_external_track_moves_the_destination(self):
        external = self.root / "external.wav"
        external.write_bytes(b"audio")
        default = self.root / "clip_TrackEdit.mkv"
        try:
            os.link(external, default)
        except (OSError, AttributeError) as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        chosen = track_manager_output_path(self.clip, [{"path": external}])
        self.assertNotEqual(chosen, default,
                            "the destination is still an alias of the added track")

    def test_the_primary_input_is_still_protected(self):
        primary = self.root / "clip_TrackEdit.mkv"
        primary.write_bytes(b"video")
        chosen = track_manager_output_path(primary, [])
        self.assertNotEqual(chosen, primary)

    def test_a_folder_batch_passes_its_external_tracks_too(self):
        source = (Path(FFmWiz.__file__).resolve().parent / "ffmwiz" / "trackmanager.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("track_manager_output_path(input_path, extra_items)"), 1)
        self.assertEqual(source.count("track_manager_output_path(media, extra_items)"), 1)
        self.assertNotIn("track_manager_output_path(media)", source)


@requires_ffmpeg
class TheAuditReproducerNoLongerDestroysTheTrack(unittest.TestCase):
    """The exact case, end to end, with every source hash asserted."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ffmwiz_r02e_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.clip = self.root / "clip.mkv"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.clip)],
                       check=True, timeout=180)
        self.external = self.root / "external.wav"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        str(self.external)], check=True, timeout=180)

    def test_the_added_track_survives_the_run(self):
        default = self.root / "clip_TrackEdit.mkv"
        try:
            os.link(self.external, default)
        except (OSError, AttributeError) as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        before = sha256(self.external)

        items = [{"path": self.external, "audio_streams": [{"codec_name": "pcm_s16le"}],
                  "subtitle_streams": []}]
        output = track_manager_output_path(self.clip, items)
        self.assertNotEqual(output, default)

        cmd = build_track_manager_command(FFMPEG, self.clip, [], items, output, {})
        # The guard must also agree, since this is what the runner will see.
        self.assertIsNone(command_source_output_conflict(cmd))
        result = subprocess.run(cmd, capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))

        self.assertEqual(sha256(self.external), before,
                         "the external track was overwritten by its own job")
        self.assertTrue(output.is_file())
        self.assertEqual(sha256(default), before,
                         "the hardlink content changed, so the source did too")

    def test_a_destination_that_becomes_an_alias_after_planning_is_refused(self):
        """The planning/execution gap: free at confirmation, aliased at run."""
        output = track_manager_output_path(self.clip, [{"path": self.external}])
        self.assertFalse(output.exists())
        try:
            os.link(self.external, output)        # the change planning could not see
        except (OSError, AttributeError) as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        before = sha256(self.external)

        items = [{"path": self.external, "audio_streams": [{"codec_name": "pcm_s16le"}],
                  "subtitle_streams": []}]
        cmd = build_track_manager_command(FFMPEG, self.clip, [], items, output, {})
        with mock.patch.object(runtime.subprocess, "Popen") as popen:
            code, _elapsed = runtime.run_ffmpeg_with_progress(cmd, label="Track Manager")
        popen.assert_not_called()
        self.assertNotEqual(code, 0)
        self.assertEqual(sha256(self.external), before)


if __name__ == "__main__":
    unittest.main()
