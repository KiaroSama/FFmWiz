"""Regression: Folder Encode must own and release each job's artifacts (B15).

`_run_folder_encode_mode_impl()` prepared and executed every file with no
`finally: release_artifacts(...)`, and `prepare_folder_job_answers()` shallow-
copies the settings dict -- so the representative build and every job shared ONE
lease. Nothing could be released after job 1 without deleting inputs job 2 still
referenced, so nothing was released at all. Measured on a real batch of two
files with a retimed embedded subtitle track:

    return code                      0
    representative/job lease shared  true
    leased paths after execution     3
    all three temp directories       still on disk
    outputs                          written

The three `ffmwiz_retimed_subs_*` directories disappeared only when the audit
called `release_artifacts()` by hand.

`tests/artifact_guard.NoLeakedArtifacts` points `tempfile.tempdir` at a private
root for each test, so a sibling worker's directory cannot be mistaken for this
test's leak -- and anything the batch forgets is reported by name.
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import FFmWiz

from artifact_guard import NoLeakedArtifacts
import cache_test_utils

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not on PATH")

ARTIFACT_PREFIX = "ffmwiz_"


def _run(args, timeout=300):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, encoding="utf-8",
                          errors="replace", timeout=timeout)


class Cancelled(Exception):
    """Stands in for the user interrupting a batch."""


@requires_ffmpeg
class FolderJobsOwnTheirArtifacts(NoLeakedArtifacts, unittest.TestCase):
    """The REAL dispatcher: only the interactive questions are stubbed."""

    def setUp(self):
        # The capability cache and this test's own media are created BEFORE the
        # guard redirects tempfile.tempdir, so the private artifact root holds
        # nothing but what the batch leased -- otherwise the cache directory
        # itself reads as a leak.
        FFmWiz.appio.USE_COLOR = False
        self._shared_tempdir = tempfile.gettempdir()
        self._cache_run_id = uuid.uuid4().hex
        self._cache_dir = cache_test_utils.create_owned_temp_cache_dir(self._cache_run_id)
        self._prev_cache = os.environ.get("FFMWIZ_CACHE_DIR")
        os.environ["FFMWIZ_CACHE_DIR"] = self._cache_dir
        FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()
        self._root = Path(tempfile.mkdtemp(prefix="ffmwiz_folderjob_"))
        super().setUp()
        self._src = self._root / "in"
        self._out = self._root / "out"
        self._src.mkdir()
        self._out.mkdir()

    def tearDown(self):
        shutil.rmtree(self._root, ignore_errors=True)
        if self._prev_cache is None:
            os.environ.pop("FFMWIZ_CACHE_DIR", None)
        else:
            os.environ["FFMWIZ_CACHE_DIR"] = self._prev_cache
        FFmWiz.services._CAPABILITY_SESSION_MEMO.clear()
        cache_test_utils.safe_remove_owned_temp_dir(
            self._cache_dir, self._cache_run_id, self._shared_tempdir)
        super().tearDown()

    # ---------------------------------------------------------------- fixtures
    def _source(self, name, *, seconds=2.0, subtitles=True, chapters=False,
                audio_tracks=1):
        args = [FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y",
                "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=10:duration={seconds}"]
        for freq in (440, 880)[:audio_tracks]:
            args += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}"]
        inputs = 1 + audio_tracks
        if subtitles:
            srt = self._src / f"{name}.srt"
            srt.write_text(
                f"1\n00:00:00,600 --> 00:00:01,200\nCUE-{name}\n\n", encoding="utf-8")
            args += ["-i", str(srt)]
        if chapters:
            meta = self._src / f"{name}.ffmeta"
            meta.write_text(
                ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\n"
                f"title=Part one of {name}\n", encoding="utf-8")
            args += ["-i", str(meta)]
        args += ["-map", "0:v"]
        for index in range(audio_tracks):
            args += ["-map", f"{index + 1}:a"]
        if subtitles:
            args += ["-map", f"{inputs}:s"]
        if chapters:
            args += ["-map_chapters", str(inputs + (1 if subtitles else 0))]
        args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                 "-c:a", "aac"]
        if subtitles:
            args += ["-c:s", "srt"]
        # No `-shortest`: the SRT input is shorter than the picture and would
        # truncate the whole file to the last cue.
        path = self._src / f"{name}.mkv"
        args += [str(path)]
        result = _run(args)
        if result.returncode != 0:
            self.skipTest("could not build synthetic source: " + (result.stderr or "")[-300:])
        for scratch in (self._src / f"{name}.srt", self._src / f"{name}.ffmeta"):
            if scratch.exists():
                scratch.unlink()
        return path

    def _settings(self, **extra):
        settings = {
            "output_ext": "mkv", "video_codec": "H264", "use_gpu": False,
            "audio_codec": "aac", "audio_bitrate_kbps": 96,
            "video_bitrate_kbps": 300, "video_bitrate_mode": "quality_vbr",
            "resolution": "n", "fps": None, "crop_enabled": False,
            "audio_tracks": [0], "subtitle_tracks": [0],
            # A timeline edit that really moves the clock, which is what makes
            # the builder rebuild the subtitle track and the chapter metadata
            # into leased temporary directories. A keep range starting at 0
            # leaves every cue where it is, so nothing would be rebuilt.
            "cut_keep_ranges": [(0.5, 1.5)],
            # Every real-media fixture states the range: an FFmpeg build that
            # cannot report one raises ColorRangeUnresolvedError (B16). A folder
            # job re-resolves its own range from the BATCH policy, so the batch
            # answer is the one that has to be present here.
            "color_range_choice": "tv",
            "_batch_color_range_policy": "tv",
        }
        settings.update(extra)
        return settings

    # ------------------------------------------------------------ the driver
    def _drive(self, settings, *, start_now=True, execute=None):
        """Run the real Folder Encode dispatcher; stub only the questions."""
        seen = {"settings": None, "jobs": [], "leases": [], "maps": []}

        def settings_wizard(answers):
            answers.update(settings)
            seen["settings"] = answers
            FFmWiz.step_start_folder_now(answers)   # the REAL representative build

        real_prepare = FFmWiz.modes.prepare_folder_job_answers

        def spy_prepare(scoped, item):
            job = real_prepare(scoped, item)
            seen["jobs"].append(job)
            seen["leases"].append(job.get("_artifact_lease"))
            seen["maps"].append(job.get("_effective_settings"))
            return job

        from ffmwiz import encoding as encoding_module
        real_execute = encoding_module.execute_encode_plan
        patches = [
            mock.patch.object(FFmWiz.modes, "run_folder_input_output_steps",
                              lambda a: a.update({"folder_input_path": self._src,
                                                  "folder_output_location": self._out,
                                                  "output_location": self._out})),
            mock.patch.object(FFmWiz.modes, "run_folder_settings_wizard", settings_wizard),
            mock.patch.object(FFmWiz.modes, "prepare_folder_job_answers", spy_prepare),
            mock.patch.object(FFmWiz.appio, "ask_yes_no", return_value=start_now),
        ]
        if execute is not None:
            patches.append(mock.patch.object(
                encoding_module, "execute_encode_plan",
                lambda *a, **k: execute(real_execute, *a, **k)))
        buffer = io.StringIO()
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(contextlib.redirect_stdout(buffer))
            seen["result"] = FFmWiz.run_folder_encode_mode({"ffmpeg": FFMPEG, "ffprobe": FFPROBE})
        seen["output"] = buffer.getvalue()
        return seen

    # ------------------------------------------------------------- assertions
    def _surviving_artifacts(self):
        return sorted(str(p) for p in self._artifact_root.glob(ARTIFACT_PREFIX + "*"))

    def _streams(self, path):
        probe = FFmWiz.ffprobe_full_json(FFPROBE, path)
        return [(s.get("codec_type"), s.get("codec_name")) for s in probe.get("streams", [])]

    def _assert_nothing_owned(self, seen):
        for index, lease in enumerate(seen["leases"], start=1):
            self.assertEqual([], list(lease or []),
                             f"job {index} still owns temporary paths")
        settings = seen["settings"] or {}
        self.assertEqual([], list(settings.get("_artifact_lease") or []),
                         "the representative still owns temporary paths")
        self.assertEqual([], self._surviving_artifacts())

    # ------------------------------------------------------------------ tests
    def test_an_automatic_batch_leaves_no_owned_artifact(self):
        self._source("one", subtitles=True, chapters=True)
        self._source("two", subtitles=True)
        seen = self._drive(self._settings())
        self.assertEqual(0, seen["result"][0], seen["output"][-800:])
        outputs = sorted(p.name for p in self._out.iterdir())
        self.assertEqual(["one.mkv", "two.mkv"], outputs)
        for name in outputs:
            kinds = [kind for kind, _ in self._streams(self._out / name)]
            self.assertIn("subtitle", kinds,
                          "the retimed subtitle track must reach the output")
        self._assert_nothing_owned(seen)

    def test_every_job_gets_its_own_lease_and_resolved_settings(self):
        self._source("one")
        self._source("two")
        seen = self._drive(self._settings())
        representative = seen["settings"]
        self.assertEqual(2, len(seen["leases"]))
        self.assertEqual(2, len({id(lease) for lease in seen["leases"]}),
                         "two files must not share one lease")
        self.assertEqual(2, len({id(m) for m in seen["maps"]}),
                         "two files must not share one resolved-settings map")
        for lease, resolved in zip(seen["leases"], seen["maps"]):
            self.assertIsNot(lease, representative.get("_artifact_lease"))
            self.assertIsNot(resolved, representative.get("_effective_settings"))

    def test_a_heterogeneous_batch_resolves_each_file_on_its_own(self):
        # A stereo file and a file whose only audio track is elsewhere in the
        # list: the second file must not inherit the first file's resolution.
        self._source("one", audio_tracks=2)
        self._source("two", audio_tracks=1)
        seen = self._drive(self._settings(audio_tracks=[0], subtitle_tracks=[0]))
        self.assertEqual(0, seen["result"][0], seen["output"][-800:])
        for job, resolved in zip(seen["jobs"], seen["maps"]):
            self.assertIs(resolved, job.get("_effective_settings"))
        self._assert_nothing_owned(seen)

    def test_a_reversed_batch_releases_its_staged_parts(self):
        self._source("one", seconds=1.0)
        self._source("two", seconds=1.0)
        seen = self._drive(self._settings(reverse_video=True, cut_keep_ranges=None,
                                          subtitle_tracks=[0]))
        self.assertEqual(0, seen["result"][0], seen["output"][-1500:])
        self._assert_nothing_owned(seen)

    def test_a_preparation_failure_still_releases_the_batch(self):
        # File "two" has one audio track, so selecting track 1 is a real
        # preparation error for that file and that file only.
        self._source("one", audio_tracks=2)
        self._source("two", audio_tracks=1)
        seen = self._drive(self._settings(audio_tracks=[1]))
        self.assertEqual(1, seen["result"][0], "the failing file must be reported")
        self.assertIn("Skipped two.mkv", seen["output"])
        self.assertTrue((self._out / "one.mkv").exists())
        self._assert_nothing_owned(seen)

    def test_an_ffmpeg_failure_still_releases_the_job(self):
        self._source("one")
        source_two = self._source("two")

        def truncate_then_run(real, job_answers, cmd, **kwargs):
            # A genuine FFmpeg failure: the file was probed and its command
            # built, then the bytes went away underneath it.
            if Path(job_answers["input_path"]).name == "two.mkv":
                source_two.write_bytes(b"not media")
            return real(job_answers, cmd, **kwargs)

        seen = self._drive(self._settings(), execute=truncate_then_run)
        self.assertEqual(1, seen["result"][0])
        self.assertIn("Failed two.mkv", seen["output"])
        self._assert_nothing_owned(seen)

    def test_a_cancellation_still_releases_the_job(self):
        self._source("one")
        self._source("two")

        def cancel_second(real, job_answers, cmd, **kwargs):
            if Path(job_answers["input_path"]).name == "two.mkv":
                raise Cancelled("user interrupted the batch")
            return real(job_answers, cmd, **kwargs)

        with self.assertRaises(Cancelled):
            self._drive(self._settings(), execute=cancel_second)
        # The dispatcher never returned, so the leases are unreachable from
        # here; the private artifact root is the whole evidence.
        self.assertEqual([], self._surviving_artifacts())

    def test_a_declined_run_keeps_the_example_commands_generated_inputs(self):
        self._source("one")
        self._source("two")
        seen = self._drive(self._settings(), start_now=False)
        self.assertIsNone(seen["result"], "declining must not start the batch")
        self.assertEqual([], list(seen["jobs"]), "no job may be prepared")
        kept = self._surviving_artifacts()
        self.assertTrue(kept, "the printed command's generated inputs must survive")
        self.assertEqual([], list(seen["settings"].get("_artifact_lease") or []),
                         "ownership is handed to the user, not merely skipped")
        # Ownership is the user's now, so this test removes them itself.
        for path in kept:
            shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
