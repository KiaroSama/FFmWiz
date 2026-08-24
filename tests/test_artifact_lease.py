"""Regression: temporary artifacts must survive shallow-copied answers (R06).

Builders work on a shallow copy (`join_answers = dict(answers)`) and used to
record cleanup paths as a KEY on that copy. The outer executor pops from the
ORIGINAL dict, so it found nothing and the directory stayed in %TEMP%.

Reproduced before the fix: building a joined command with subtitles created
`ffmwiz_join_subs_*`, the key was absent from the outer answers, and the real
`cleanup_join_concat_list()` left the directory behind.

A lease fixes the class rather than the instance: `dict()` copies the key but
SHARES the object, so anything leased through any copy is still owned outside.
The one rule -- open the lease before the copy -- is what the first attempt got
wrong, and it still leaked until the lease was created ahead of `dict(answers)`.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import FFmWiz
from ffmwiz.core import artifacts

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")



class LeaseSemantics(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwizlease_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _file(self, name):
        path = self._tmp / name
        path.write_text("x", encoding="utf-8")
        return path

    def test_a_shallow_copy_shares_the_lease(self):
        answers = {}
        lease = FFmWiz.artifact_lease(answers)
        copy = dict(answers)
        FFmWiz.artifact_lease(copy).register(self._file("a.txt"))
        self.assertEqual(1, len(lease),
                         "the copy leased into its own object instead of sharing")

    def test_copying_BEFORE_the_lease_exists_does_not_share(self):
        # Pinning the trap, not endorsing it: this is why builders must open the
        # lease before `dict(answers)`, and why the first fix still leaked.
        answers = {}
        copy = dict(answers)
        FFmWiz.artifact_lease(copy).register(self._file("b.txt"))
        self.assertEqual(0, len(FFmWiz.artifact_lease(answers)))

    def test_release_removes_files_and_directories(self):
        answers = {}
        lease = FFmWiz.artifact_lease(answers)
        a_file = lease.register(self._file("c.txt"))
        a_dir = lease.register(Path(tempfile.mkdtemp(prefix="ffmwizlease_dir_")))
        FFmWiz.release_artifacts(answers)
        self.assertFalse(a_file.exists())
        self.assertFalse(a_dir.exists())

    def test_release_is_idempotent(self):
        answers = {}
        FFmWiz.artifact_lease(answers).register(self._file("d.txt"))
        FFmWiz.release_artifacts(answers)
        self.assertEqual([], FFmWiz.release_artifacts(answers))

    def test_release_on_a_dict_with_no_lease_is_harmless(self):
        self.assertEqual([], FFmWiz.release_artifacts({}))

    def test_registering_the_same_path_twice_owns_it_once(self):
        lease = FFmWiz.ArtifactLease()
        path = self._file("e.txt")
        lease.register(path)
        lease.register(path)
        self.assertEqual(1, len(lease))

    def test_forget_hands_an_artifact_back_without_deleting(self):
        # A command printed for manual execution still needs its generated
        # inputs; the lease must be able to give one up deliberately.
        answers = {}
        lease = FFmWiz.artifact_lease(answers)
        kept = lease.register(self._file("f.txt"))
        lease.forget(kept)
        FFmWiz.release_artifacts(answers)
        self.assertTrue(kept.exists())

    def test_release_only_touches_what_it_owns(self):
        bystander = self._file("g.txt")
        answers = {}
        FFmWiz.artifact_lease(answers).register(self._file("h.txt"))
        FFmWiz.release_artifacts(answers)
        self.assertTrue(bystander.exists())


class ReleaseNeverReportsWhatItDidNotRemove(unittest.TestCase):
    """Regression: a locked directory was reported removed and forgotten (F12).

    `release()` called `shutil.rmtree(path, ignore_errors=True)` and then dropped
    the path unconditionally. `ignore_errors` swallows a Windows sharing
    violation and returns normally, so a directory holding one open file
    survived while `release()` answered "removed" -- and because the path had
    left the lease, the retry its own comment promises could never happen.

    Measured before the fix, with a real open handle inside the directory:

        PATH_STILL_EXISTS                 true
        LEASE_LENGTH_AFTER_FALSE_SUCCESS  0
        REPORTED_REMOVED                  true
    """

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwizlocked_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _locked_directory(self):
        """A directory with one file held open, which is what really happens
        when a job is cancelled while ffmpeg still has an input mapped."""
        directory = self._tmp / "artifact"
        directory.mkdir()
        inside = directory / "retimed00.srt"
        inside.write_text("one cue", encoding="utf-8")
        handle = inside.open("r", encoding="utf-8")
        self.addCleanup(handle.close)
        return directory, handle

    def test_a_locked_directory_is_not_reported_as_removed(self):
        directory, _handle = self._locked_directory()
        lease = FFmWiz.ArtifactLease()
        lease.register(directory)
        removed = lease.release()
        if not directory.exists():
            self.skipTest("this platform deletes a directory holding an open file")
        self.assertNotIn(directory, removed,
                         "release() claimed a directory that is still there")

    def test_a_locked_directory_stays_owned_for_the_retry(self):
        directory, _handle = self._locked_directory()
        lease = FFmWiz.ArtifactLease()
        lease.register(directory)
        lease.release()
        if not directory.exists():
            self.skipTest("this platform deletes a directory holding an open file")
        self.assertEqual(1, len(lease),
                         "a failed deletion dropped out of the lease, so nothing "
                         "is left to retry")

    def test_the_retry_finishes_the_job_once_the_handle_closes(self):
        directory, handle = self._locked_directory()
        lease = FFmWiz.ArtifactLease()
        lease.register(directory)
        lease.release()
        if not directory.exists():
            self.skipTest("this platform deletes a directory holding an open file")
        handle.close()
        self.assertIn(directory, lease.release())
        self.assertFalse(directory.exists())
        self.assertEqual(0, len(lease))

    def test_one_stuck_path_does_not_strand_the_others(self):
        directory, _handle = self._locked_directory()
        loose = self._tmp / "loose.srt"
        loose.write_text("x", encoding="utf-8")
        lease = FFmWiz.ArtifactLease()
        lease.register(directory)
        lease.register(loose)
        removed = lease.release()
        self.assertIn(loose, removed)
        self.assertFalse(loose.exists())

    def test_a_deletion_that_silently_does_nothing_is_caught(self):
        # The general shape, independent of what this OS locks: any rmtree that
        # returns without deleting must not be believed.
        directory = self._tmp / "noop"
        directory.mkdir()
        lease = FFmWiz.ArtifactLease()
        lease.register(directory)
        real_rmtree = artifacts.shutil.rmtree
        artifacts.shutil.rmtree = lambda *args, **kwargs: None
        try:
            removed = lease.release()
        finally:
            artifacts.shutil.rmtree = real_rmtree
        self.assertEqual([], removed)
        self.assertEqual(1, len(lease))
        self.assertTrue(directory.exists())

    def test_an_unlinkable_file_is_kept_too(self):
        path = self._tmp / "stuck.txt"
        path.write_text("x", encoding="utf-8")
        lease = FFmWiz.ArtifactLease()
        lease.register(path)
        real_unlink = Path.unlink
        Path.unlink = lambda self, *a, **k: None
        try:
            removed = lease.release()
        finally:
            Path.unlink = real_unlink
        self.assertEqual([], removed)
        self.assertEqual(1, len(lease))


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")
class JoinedSubtitleDirectoryDoesNotLeak(unittest.TestCase):
    """The real workflow the leak was found in."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ffmwizleak_"))
        # Redirect tempfile to a private root for the duration of the test.
        # Scanning the SHARED %TEMP% for ffmwiz_* is not parallel-safe: another
        # worker's directory shows up as this test's leak.
        self._saved_tempdir = tempfile.tempdir
        self._temp_root = self._tmp / "temproot"
        self._temp_root.mkdir()
        tempfile.tempdir = str(self._temp_root)

    def tearDown(self):
        tempfile.tempdir = self._saved_tempdir
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _ffmwiz_temp_paths(self):
        return {path for path in self._temp_root.glob("ffmwiz_*")}

    def _new_temp_paths(self):
        return self._ffmwiz_temp_paths() - getattr(self, "_before", set())

    def _clip(self, name, text, duration=4):
        srt = self._tmp / f"{name}.srt"
        srt.write_text(f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n\n", encoding="utf-8")
        path = self._tmp / f"{name}.mkv"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"color=c=red:s=160x120:d={duration}:r=25",
             "-f", "lavfi", "-i", f"sine=duration={duration}", "-i", str(srt),
             "-map", "0:v", "-map", "1:a", "-map", "2:s",
             "-c:v", "libx264", "-preset", "ultrafast", "-vf", "format=yuv420p",
             "-c:a", "aac", "-c:s", "srt", str(path)],
            check=True, timeout=300)
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json",
             str(path)], capture_output=True, text=True, timeout=60).stdout)
        streams = probe["streams"]
        return {"path": path, "streams": streams, "format": probe["format"],
                "duration": float(duration),
                "video_streams": [s for s in streams if s["codec_type"] == "video"],
                "audio_streams": [s for s in streams if s["codec_type"] == "audio"],
                "subtitle_streams": [s for s in streams if s["codec_type"] == "subtitle"],
                "attachment_streams": [], "data_streams": []}

    def _build(self):
        items = [self._clip("a", "ALPHA"), self._clip("b", "BETA")]
        first = items[0]
        answers = {
            "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "input_path": first["path"],
            "output_location": self._tmp, "streams": first["streams"],
            "format": first["format"], "video_streams": first["video_streams"],
            "audio_streams": first["audio_streams"],
            "subtitle_streams": first["subtitle_streams"],
            "data_streams": [], "attachment_streams": [],
            "output_ext": "mkv", "video_codec": "H264", "use_gpu": False,
            "audio_codec": "aac", "audio_bitrate_kbps": 128,
            "audio_tracks": [0], "subtitle_tracks": [0],
            "resolution": "n", "fps": 25, "video_bitrate_kbps": 400,
            "color_range_choice": "tv", "join_input_items": items[1:],
            "keep_source_subtitles": True,
        }
        FFmWiz.build_join_encode_command(answers, items, self._tmp / "joined.mkv")
        return answers

    def test_the_build_really_creates_a_temp_directory(self):
        # Guard the guard: if the build stopped creating one, the leak test
        # below would pass for the wrong reason.
        self._before = self._ffmwiz_temp_paths()
        answers = self._build()
        created = self._new_temp_paths()
        self.assertTrue(any("join_subs" in path.name for path in created),
                        f"no joined-subtitle temp dir was created: {created}")
        FFmWiz.cleanup_join_concat_list(answers)

    def test_cleanup_leaves_nothing_behind(self):
        self._before = self._ffmwiz_temp_paths()
        answers = self._build()
        FFmWiz.cleanup_join_concat_list(answers)
        self.assertEqual(set(), self._new_temp_paths(),
                         "a temporary artifact outlived the workflow")

    def test_the_outer_answers_owns_the_artifact(self):
        # The precise thing that was broken: ownership must be visible from the
        # dict the executor holds, not only from the builder's private copy.
        self._before = self._ffmwiz_temp_paths()
        answers = self._build()
        try:
            self.assertGreaterEqual(len(FFmWiz.artifact_lease(answers)), 1)
        finally:
            FFmWiz.cleanup_join_concat_list(answers)


if __name__ == "__main__":
    unittest.main()
